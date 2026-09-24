"""Shell command execution tool."""

import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Optional, Tuple

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

from .base import Tool, _declares_shell_spec
from ..capabilities import BackgroundHandle, LocalShellExecutor, ShellRequest, ShellResult
from ..capabilities import powershell as ps
from ..capabilities.process import build_child_env
from ..capabilities.shell_spec import (
    AbsPath,
    DecidedCall,
    Deny,
    Exhausted,
    LaunchRequest,
    LaunchRefused,
    LegacyLaunch,
    ShellDialect,
    ShellSpec,
    WindowsLaunch,
    default_spec,
    display_name,
)
from ..capabilities.shell import (
    _is_binary,
    resolve_shell_executable,
    shell_display_name,
)
from ..sandbox import SandboxProfile
from ..security import PathPolicy, PathPolicyError

# Maximum length of the string the tool returns (~10K tokens): output, headers, status
# line and notices together. Matches Gemini CLI's default threshold of 40,000 characters,
# and must not exceed the result layer's TOOL_OUTPUT_SAVE_THRESHOLD, past which the
# already-cut result is saved to disk as the command's "Full output".
_MAX_OUTPUT_CHARS = 40_000


def _omitted(result: ShellResult, stream: str) -> int:
    """Bytes the executor did not keep of *stream*, or 0.

    Read defensively: a host executor may return an object that predates the
    field, and anything that is not a plain non-negative int is read as 0.
    """
    value = getattr(result, f"{stream}_omitted_bytes", 0)
    if type(value) is not int or value < 0:
        return 0
    return value


def _omitted_at(result: ShellResult, stream: str) -> int:
    """Where in *stream*'s bytes the gap is; 0 (the front) for anything unusable."""
    value = getattr(result, f"{stream}_omitted_at", 0)
    raw = getattr(result, stream, b"")
    if type(value) is not int or not 0 <= value <= len(raw):
        return 0
    return value


# What the model sees of a stream that is too long: this share from its start, the rest
# from its end — the same split as the result layer's own excerpt. The end carries the
# outcome (the test summary, the last error); the start carries what the command was
# doing, and a compiler's first error, which is often the one that matters.
_HEAD_SHARE = 0.2


@dataclass(frozen=True)
class _Stream:
    """One stream, ready to show: text before the gap, bytes lost in it, text after.

    ``gap`` is 0 for a stream the executor kept whole, and then ``tail`` is empty.
    """

    head: str
    gap: int = 0
    tail: str = ""


def _clean(text: str) -> str:
    """Collapse progress-bar overwrites and strip ANSI codes before the model sees it.

    Progress bars use \\r to overwrite lines in a terminal; without this, the LLM
    receives all intermediate states as separate lines of noise.
    """
    return _strip_ansi(_collapse_carriage_returns(text))


def _stream_of(raw: bytes, omitted: int, at: int, powershell: bool) -> _Stream:
    if _is_binary(raw):
        return _Stream(f"[binary output — {len(raw) + omitted:,} bytes not shown]")
    if not omitted:
        text = raw.decode("utf-8", errors="replace")
        if powershell:
            # Windows PowerShell 5.1 serialises a *redirected* error stream as CLIXML,
            # and agentao always redirects — so without this the model reads an XML
            # envelope instead of the error.
            text = ps.extract(text, _MAX_OUTPUT_CHARS)
        return _Stream(_clean(text))
    # A gap: the two sides are decoded and cleaned apart and never joined, and a
    # CLIXML envelope is not unwrapped across one — the scan would read the elements
    # on either side of the hole as one message. It is shown raw, gap marked.
    head = raw[:at].decode("utf-8", errors="replace")
    tail = raw[at:].decode("utf-8", errors="replace")
    # The cut can land inside an escape sequence. ``_strip_ansi`` only removes whole ones,
    # so a severed ``\x1b[3`` would reach the model — and a terminal, where the ``[`` of
    # the gap notice after it completes the sequence. The tail's side of such a cut is
    # plain characters (``1mRED``) and needs nothing.
    head = _SEVERED_ESCAPE_RE.sub("", head)
    return _Stream(_clean(head), omitted, _clean(tail))


def _gap_note(gap: int, cut: int) -> str:
    if gap and cut:
        return f"[... {gap:,} bytes of output not kept, and {cut:,} more chars omitted ...]"
    if gap:
        return f"[... {gap:,} bytes of output not kept ...]"
    return f"[... {cut:,} chars omitted ...]"


def _around(head: str, note: str, tail: str) -> str:
    return head + ("\n" if head else "") + note + "\n" + tail


def _fit_stream(stream: _Stream, budget: int) -> str:
    """*stream*'s head and tail, with the notice between, in at most *budget* characters.

    The notice is counted inside the budget, not added on top: the string the tool
    returns must stay within ``_MAX_OUTPUT_CHARS``, or the result layer saves this
    excerpt to disk as the command's "Full output". It names both losses when there
    are two — bytes the executor never kept, and characters this cut removes.
    """
    if budget <= 0:
        return ""
    head, gap, tail = stream.head, stream.gap, stream.tail
    if not gap:
        if len(head) <= budget:
            return head
        text, head, tail = head, "", ""
        # One text: split it here, the head share from its start and the rest from its end.
        keep = budget - len(_around("x", _gap_note(0, len(text)), ""))
        if keep <= 0:
            return _gap_note(0, len(text))[:budget]
        first = int(keep * _HEAD_SHARE)
        last = keep - first
        return _around(text[:first], _gap_note(0, len(text) - keep), text[len(text) - last:])
    whole = _around(head, _gap_note(gap, 0), tail)
    if len(whole) <= budget:
        return whole
    # The note's numbers can only shrink once the cut is known, so sizing it with the
    # largest values bounds it from above.
    keep = budget - len(_around("x", _gap_note(gap, len(head) + len(tail)), ""))
    if keep <= 0:
        return _gap_note(gap, 0)[:budget]
    first = min(len(head), int(keep * _HEAD_SHARE))
    last = min(len(tail), keep - first)
    first = min(len(head), keep - last)  # a short tail gives its share back to the head
    cut = (len(head) - first) + (len(tail) - last)
    return _around(head[:first], _gap_note(gap, cut), tail[len(tail) - last:])


def _stream_need(stream: _Stream) -> int:
    """Characters *stream* takes shown whole, notice included; 0 for nothing to show."""
    if not (stream.head or stream.gap or stream.tail):
        return 0
    return len(_fit_stream(stream, 1 << 62))


# The timeout message echoes the command. The model wrote it and has it already, and a
# heredoc can run to tens of kilobytes, so the echo is the first thing cut.
_MAX_COMMAND_ECHO_CHARS = 2_000


def _clip_command(command: str, what: str = "the command") -> str:
    if len(command) <= _MAX_COMMAND_ECHO_CHARS:
        return command
    omitted = len(command) - _MAX_COMMAND_ECHO_CHARS
    return command[:_MAX_COMMAND_ECHO_CHARS] + f" [... {omitted:,} more chars of {what} not shown]"


def _unusable_working_directory(working_directory: str, error: Exception) -> str:
    """The refusal for a working directory the OS would not even stat.

    The OS's reason, never ``str(error)``: an ``OSError`` renders the whole path a
    second time, and the path is what made it fail.
    """
    if isinstance(error, OSError):
        reason = error.strerror or type(error).__name__
    else:
        reason = str(error) or type(error).__name__
    return _cap_result(
        f"Error: working_directory '{_clip_command(working_directory, 'the path')}' "
        f"cannot be used: {reason}."
    )


_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
# An escape sequence cut short at the end of a text: ESC, optionally ``[`` and the
# parameter and intermediate bytes, and no final byte.
_SEVERED_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*)?\Z")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences (colors, cursor codes) from text."""
    return _ANSI_ESCAPE_RE.sub("", text)


def _collapse_carriage_returns(text: str) -> str:
    """Simulate terminal \\r behavior: collapse progress-bar overwrite sequences.

    Progress bars use \\r to stay on one line. This collapses each line to
    only what would be visible after all carriage-returns are applied.
    """
    if not text:
        return text
    # Normalise \\r\\n -> \\n first so we don't treat it as an in-line \\r
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    collapsed = []
    for line in lines:
        if "\r" in line:
            segment = line.split("\r")[-1]
            if segment:
                collapsed.append(segment)
            # else: line was entirely overwritten by \\r, drop it
        else:
            collapsed.append(line)
    return "\n".join(collapsed)


def _wrap_with_sandbox(command: str, profile: SandboxProfile) -> str:
    """Prefix `command` with a sandbox-exec invocation.

    The resulting string is a single shell expression that, when run under
    the outer shell, will exec `sandbox-exec` with the required -D /-f
    flags and pass the original command to a fresh inner shell inside the
    sandbox. stdout / stderr / exit code propagate normally.

    The inner shell is :func:`resolve_shell_executable`'s answer — the same
    one the outer `Popen(shell=True, executable=...)` uses. Hardcoding
    `/bin/sh` here would mean a sandboxed command silently got a different
    dialect from an unsandboxed one, so the same command would parse on a
    plain run and fail under `--sandbox`.
    """
    if not IS_MACOS:
        return command
    inner = resolve_shell_executable() or "/bin/sh"
    prefix = " ".join(shlex.quote(a) for a in profile.as_args())
    return f"{prefix} {shlex.quote(inner)} -c {shlex.quote(command)}"


_SANDBOX_DENIAL_MARKERS = (
    "Operation not permitted",
    "deny file-write",
    "deny network",
)


def _sandbox_hint(profile: SandboxProfile) -> str:
    """The note appended to a result that looks like a sandbox denial."""
    return (
        f"\n\n[Sandbox hint] The command ran under macOS sandbox profile "
        f"'{profile.name}'. If the failure looks like a capability denial "
        f"(file-write outside workspace, network access, etc.) rather than "
        f"a real command error, ask the user to run `/sandbox off` or switch "
        f"profile via `/sandbox profile <name>`."
    )


def _annotate_sandbox_denial(result: str, profile: SandboxProfile) -> str:
    """If the result looks like a sandbox denial, append a hint for the LLM.

    We only annotate when there's evidence of sandbox rejection — not every
    EPERM is sandbox-caused, but the marker + an active profile is a strong
    enough signal to flag.

    NB: we deliberately do NOT look for the literal "sandbox-exec" because
    the wrapped command string is echoed back by the background-start path
    and by the inactivity-timeout path, which would false-positive on every
    successful background launch.

    The hint is kept whole and the result is what gives way, so the two together
    stay within ``_MAX_OUTPUT_CHARS``. The foreground path reserves the hint's
    length up front, so this cut is normally a no-op there.
    """
    if any(m in result for m in _SANDBOX_DENIAL_MARKERS):
        return _cap_result(result, _sandbox_hint(profile))
    return _cap_result(result)


def _cap_result(body: str, suffix: str = "") -> str:
    """*body* + *suffix* in at most ``_MAX_OUTPUT_CHARS``; the suffix is kept whole.

    The last check before the tool returns. Every path is budgeted on its own
    already; this covers the ones whose length comes from somewhere else — a
    refusal reason, a start error — so that no shell result reaches the result
    layer's threshold, past which it is saved to disk as the command's
    "Full output".
    """
    if len(body) + len(suffix) <= _MAX_OUTPUT_CHARS:
        return body + suffix
    # Head and tail, not the tail alone: what reaches this cut is mostly an error, and
    # its label ("Error: hardline:…") is at the front.
    return _fit_stream(_Stream(body), _MAX_OUTPUT_CHARS - len(suffix)) + suffix


def _split_budget(first: int, second: int, available: int) -> Tuple[int, int]:
    """Budgets for two streams that need *first* and *second* characters.

    Both whole when they fit. Otherwise the smaller first, whole if it fits in
    half, and the rest to the larger: a proportional split handed a 50-char
    error beside megabytes of stdout three characters, which is not the error
    any more.
    """
    if first + second <= available:
        return first, second
    if not second:
        return available, 0
    if not first:
        return 0, available
    if first <= second:
        first_budget = min(first, available // 2)
        return first_budget, available - first_budget
    second_budget = min(second, available // 2)
    return available - second_budget, second_budget


class ShellTool(Tool):
    """Tool for executing shell commands."""

    @property
    def name(self) -> str:
        return "run_shell_command"

    @property
    def description(self) -> str:
        background_instructions = (
            "To run a command in the background, set is_background=true. "
            "The command will start, run briefly to check for immediate errors, "
            "then detach. The returned PGID can be used to stop it later."
        )
        efficiency_guidelines = (
            "\n\nEfficiency Guidelines:\n"
            "- Quiet flags: prefer silent/quiet flags to reduce output volume "
            "(e.g. `npm install --silent`, `git --no-pager`, `pip install -q`).\n"
            "- Pagination: always disable terminal pagination so commands terminate "
            "(e.g. `git --no-pager`, `PAGER=cat`)."
        )
        returned_info = (
            "\n\nThe following information is returned:\n"
            "- Output: stdout and stderr (shown separately). "
            "Can be empty or partial on timeout.\n"
            "- Exit code: only included if non-zero (command failed).\n"
            "- Signal: only included if the process was killed by a signal.\n"
            "- Background PGID: only included when is_background=true."
        )
        shell_desc = self._invocation()
        stop_desc = (
            "taskkill /F /PID <PID>" if IS_WINDOWS
            else "`kill -- -PGID` or signaled as `kill -s SIGNAL -- -PGID`"
        )
        return (
            f"This tool executes a given shell command as `{shell_desc}`. "
            f"{background_instructions} "
            "Command is executed as a subprocess that leads its own process group. "
            f"Command process group can be terminated as {stop_desc}."
            f"{efficiency_guidelines}"
            f"{returned_info}"
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        f"Exact command to execute. Runs as `{self._invocation()}`."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Brief description of what this command does, shown to the user "
                        "in the confirmation prompt and progress indicator. "
                        "Be specific and concise. Ideally one sentence, no line breaks."
                    ),
                },
                "working_directory": {
                    "type": "string",
                    "description": (
                        "Directory to run the command in. Must be an existing directory. "
                        "Defaults to the current working directory."
                    ),
                },
                "timeout": {
                    "type": "number",
                    "description": (
                        "Inactivity timeout in seconds (default: 120). "
                        "Resets whenever the command produces output. "
                        "Use is_background=true for commands that should run indefinitely."
                    ),
                    "default": 120,
                },
                "is_background": {
                    "type": "boolean",
                    "description": (
                        "If true, the command is started, allowed to run briefly to catch immediate errors, "
                        "then detached to the background. Returns the process group ID (PGID) immediately; "
                        "stdout/stderr are discarded. "
                        "Use for long-running servers or file watchers."
                    ),
                    "default": False,
                },
            },
            "required": ["command"],
        }

    def _invocation(self) -> str:
        """How this tool's command will actually be invoked, in one phrase.

        Built from the resolved spec rather than from ``sys.platform``, because
        the description is the model's only statement of which syntax to write.
        Telling it ``cmd /c`` while PowerShell reads the text is not a cosmetic
        mismatch — it is the model choosing the wrong syntax on every call, and
        cmd and PowerShell disagree about quoting, redirection and every
        builtin's name.
        """
        spec = self.shell_spec
        if isinstance(spec, ShellSpec) and spec.dialect is ShellDialect.POWERSHELL:
            return f"{display_name(spec, IS_WINDOWS)} -NoProfile -Command <command>"
        if isinstance(spec, ShellSpec) and spec.interpreter:
            # ``/c`` for a configured cmd on Windows: that is the switch ``shell=True``
            # composes there, and the description is the model's only statement of how its
            # text is handed over.
            switch = "/c" if IS_WINDOWS and spec.dialect is ShellDialect.CMD else "-c"
            return f"{spec.interpreter} {switch} <command>"
        if IS_WINDOWS:
            return "cmd /c <command>"
        # ``shell_display_name()`` and not the imported resolver: the platform default has one
        # definition point, in the module that also decides it, so the description cannot say
        # bash while the executor starts ``/bin/sh``.
        return f"{shell_display_name()} -c <command>"

    @property
    def requires_confirmation(self) -> bool:
        return True

    def execute(
        self,
        command: str,
        description: str = "",
        working_directory: str = ".",
        timeout: float = 120,
        is_background: bool = False,
        _sandbox_profile: Optional[SandboxProfile] = None,
        _decided: Optional[DecidedCall] = None,
    ) -> str:
        """Execute shell command.

        `_sandbox_profile` is a private parameter (not exposed via `parameters`)
        that ToolRunner injects when a macOS sandbox policy is active. When
        set, the command is wrapped in `sandbox-exec` before spawning.
        """
        spec: "ShellSpec | Exhausted | None" = None
        if _decided is not None:
            # What was judged is what runs. Re-reading ``command`` here would be a
            # second source for the text, which is a channel that decides one command and
            # launches another through the same plan.
            if isinstance(_decided.verdict, Deny):
                return _cap_result(f"Error: {_decided.verdict.reason}")
            command, cwd = _decided.body, Path(_decided.cwd)
            # The spec too. Re-reading the provider down in
            # ``_legacy_launch`` would be that same second source one field over — the launch
            # would carry the fingerprint of whatever re-resolution had swapped in since,
            # while the floor's verdict was computed against the frozen one.
            spec = _decided.spec
        else:
            try:
                cwd = self.resolve_cwd(working_directory)
            except PathPolicyError as e:
                return _cap_result(f"Error: {e}")
            except (OSError, ValueError) as e:
                # Resolving stats the path, and a path the OS refuses to stat raises: on
                # Windows a name past its length limit is a ValueError, not an OSError.
                return _unusable_working_directory(working_directory, e)
            # No frozen record — a host calling ``execute`` directly. Read the provider
            # *here*, once, rather than leaving it to ``_launch``: leaving it there resolved
            # the interpreter for the spawn and left every reader of ``spec`` below holding
            # ``None``, so a PowerShell launch was built and then formatted as if it were not
            # one — the model read the raw CLIXML envelope instead of the error in it.
            # Guarded, because moving the read up here also moved it out from behind the
            # spawn's own ``except Exception``: a host provider that raises is a refusal, the
            # same answer the planner gives it, not an exception out of a tool.
            try:
                spec = self.shell_spec
            except Exception as e:  # noqa: BLE001 - a provider failure refuses the call
                return _cap_result(f"Error: shell spec provider raised: {e}")
        # Only validate cwd against the local filesystem when using the default
        # local executor. An injected ShellExecutor (Docker, remote host, …)
        # may accept a container/remote path that does not exist locally; let
        # that executor validate the cwd itself.
        if isinstance(self._get_shell(), LocalShellExecutor):
            shown = _clip_command(working_directory, "the path")
            # ``is_dir`` answers False only for the errors pathlib chooses to ignore
            # (not found, not a directory, …); anything else the stat raises — a path
            # longer than the OS allows, a permission denied on a parent — came out of
            # the tool as an exception. It is the same refusal, with the OS's reason.
            try:
                usable = cwd.is_dir()
            except (OSError, ValueError) as e:
                return _unusable_working_directory(working_directory, e)
            if not usable:
                return _cap_result(
                    f"Error: working_directory '{shown}' does not exist "
                    "or is not a directory."
                )

        if _sandbox_profile is not None:
            wrapped = _wrap_with_sandbox(command, _sandbox_profile)
        else:
            wrapped = command

        if is_background:
            result = self._run_background(wrapped, cwd, spec)
        else:
            # The hint is appended after formatting, so its room is set aside here.
            reserve = len(_sandbox_hint(_sandbox_profile)) if _sandbox_profile is not None else 0
            result = self._run_foreground(wrapped, cwd, timeout, spec, reserve=reserve)

        if _sandbox_profile is not None:
            return _annotate_sandbox_denial(result, _sandbox_profile)
        return _cap_result(result)

    # ------------------------------------------------------------------
    # The shell spec, and the launch built from it
    # ------------------------------------------------------------------

    def resolve_cwd(self, working_directory: str) -> Path:
        """This call's working directory, canonical. One spelling, two readers.

        The planner freezes this into the decided record and ``execute`` starts the child in
        it. Resolving it twice by two routes is how the directory a decision was made against
        stops being the directory the child actually runs in.
        """
        return PathPolicy.for_tool(self).contain_directory(working_directory)

    # Set on the instance the first time an undeclaring executor is seen. Class attributes
    # rather than ``__init__`` state so a host that builds the tool the old way still works.
    _fallback_spec: "ShellSpec | None" = None
    _fallback_for: object = None

    @property
    def shell_spec(self) -> "ShellSpec | Exhausted":
        """Which interpreter this call will reach, read once per call.

        Delegated to the executor, which is the party that knows: a Docker or remote executor
        starts a different interpreter on a different filesystem. An executor predating this
        member is read as today's platform default rather than as a refusal — a refusal here
        would deny every shell call on hosts that have changed nothing.

        The declaration is probed without evaluating it, then read directly: ``getattr`` with
        a default swallows an ``AttributeError`` raised *inside* a host's property, which
        would read here as "declares nothing" and quietly report the platform default for an
        executor whose resolution actually failed. A raising provider must reach the planner,
        which turns it into ``Exhausted``.

        The fallback is memoised per executor. A call holds one spec object until
        re-resolution swaps it, and minting a fresh one on every read would put PowerShell
        discovery — a filesystem walk — on the permission path of every shell command.
        """
        executor = self._get_shell()
        if _declares_shell_spec(executor):
            declared = executor.shell_spec
            if isinstance(declared, (ShellSpec, Exhausted)):
                return declared
            # An executor that declares the member and answers with something else has not
            # said "I am the platform default" — it has failed to answer. Falling back here
            # would report a dialect nobody stands behind, which is the same misread the
            # ``getattr``-with-a-default spelling above is avoided for.
            return Exhausted(
                f"{type(executor).__name__}.shell_spec returned "
                f"{type(declared).__name__}, not a ShellSpec or Exhausted"
            )
        if self._fallback_spec is None or self._fallback_for is not executor:
            self._fallback_for = executor
            self._fallback_spec = default_spec()
        return self._fallback_spec

    def _launch(
        self, command: str, cwd: Path, spec: "ShellSpec | Exhausted | None" = None,
    ) -> LaunchRequest:
        """The launch this call runs: a named interpreter if one was resolved, else today's.

        Named means ``WindowsLaunch`` — the image fixed by path, the command line built here.
        PowerShell takes it because the body has to be encoded; a POSIX interpreter on Windows
        takes it because ``LegacyLaunch`` there is ``shell=True``, which composes cmd's
        ``/c``. Everything else is ``LegacyLaunch``, which is exactly what shipped.

        The environment is ``build_child_env()`` on both paths — inherited minus agentao's own
        provider credentials, so a command the model wrote cannot read the key back out — and
        it is rebuilt per call, because an installer that edits ``PATH`` does not reach a
        process that is already running.

        ``spec`` is the one the decision was frozen against. It is a parameter rather than a
        second read of the provider because a second read is a second answer: one spec governs
        the decision *and* the launch, and re-resolving here is how a body judged as
        PowerShell ends up in cmd.
        """
        if spec is None:
            spec = self.shell_spec
        env = MappingProxyType(build_child_env())
        if isinstance(spec, ShellSpec) and spec.dialect is ShellDialect.POWERSHELL:
            if not spec.interpreter:
                # Refused rather than fallen back on. ``LegacyLaunch`` below means "the
                # platform's own shell", which on Windows is ``%COMSPEC% /c`` — and cmd
                # reading a body written for PowerShell does not fail, it means something
                # else. Reachable only from a host executor that declares the dialect
                # without naming the image; ``default_spec`` answers ``Exhausted`` instead.
                raise LaunchRefused(Deny(
                    "hardline:no-shell-opaque: the resolved spec names the powershell "
                    "dialect but no interpreter path, and there is no shell to fall back to "
                    "that reads PowerShell"
                ))
            line = ps.command_line(spec.interpreter, command)
            oversize = ps.oversize(line)
            if oversize is not None:
                # Refused rather than truncated or spilled to a temporary script: a cut inside
                # the base64 changes what runs, and writing a file would run something the
                # floor never scanned under a name nobody chose.
                raise LaunchRefused(Deny(f"command not launchable: {oversize}"))
            return WindowsLaunch(
                application_name=AbsPath(spec.interpreter),
                command_line=line,
                cwd=AbsPath(str(cwd)),
                env=env,
            )
        if (
            IS_WINDOWS
            and isinstance(spec, ShellSpec)
            and spec.dialect is ShellDialect.POSIX
            and spec.interpreter
        ):
            # A POSIX interpreter on Windows cannot go through ``LegacyLaunch``. Windows
            # ``shell=True`` composes ``{executable} /c "<command>"`` (CPython substitutes
            # ``executable`` for ``ComSpec``), and ``/c`` is cmd's switch — Git Bash reads it
            # as the name of a script to run. The named-interpreter shape says ``-c``, which
            # is the flag the configured shell actually takes.
            return WindowsLaunch(
                application_name=AbsPath(spec.interpreter),
                command_line=subprocess.list2cmdline([spec.interpreter, "-c", command]),
                cwd=AbsPath(str(cwd)),
                env=env,
            )
        return LegacyLaunch(
            command=command,
            cwd=AbsPath(str(cwd)),
            env=env,
            # The interpreter the user named travels all the way to the spawn. It used to stop
            # at the spec, so ``shell.path`` chose an interpreter that never ran.
            executable=spec.interpreter if isinstance(spec, ShellSpec) else None,
        )

    @staticmethod
    def _is_powershell(spec: "ShellSpec | Exhausted | None") -> bool:
        return isinstance(spec, ShellSpec) and spec.dialect is ShellDialect.POWERSHELL

    # ------------------------------------------------------------------
    # Background execution
    # ------------------------------------------------------------------

    def _run_background(
        self, command: str, cwd: Path, spec: "ShellSpec | Exhausted | None" = None,
    ) -> str:
        """Start command detached; return PID (and PGID on Unix) immediately.

        Everything reported back names ``command`` — the body the model wrote — never the
        launch's own command line, which for PowerShell is base64 and tells a reader nothing.
        """
        try:
            handle: BackgroundHandle = self._get_shell().run_background(
                ShellRequest(launch=self._launch(command, cwd, spec))
            )
        except NotImplementedError:
            return (
                "Error: shell executor does not support background execution. "
                "Run this command with is_background=false."
            )
        except LaunchRefused as refusal:
            # This is a denial, and it must
            # not read as a transient start failure. The broad handler below would wrap it in
            # "Error starting background command: …", which is the shape of something a model
            # retries — so it is caught first and reported exactly like the frozen record's
            # own DENY (`Error: hardline:…`).
            return f"Error: {refusal.deny.reason}"
        except Exception as e:
            return f"Error starting background command: {e}"

        if IS_WINDOWS or handle.pgid is None:
            return (
                f"Background process started.\n"
                f"PID: {handle.pid}\n"
                f"Command: {_clip_command(command)}\n"
                f"Working directory: {cwd}\n"
                f"To stop: taskkill /F /T /PID {handle.pid}"
            )
        return (
            f"Background process started.\n"
            f"PID: {handle.pid}\n"
            f"PGID: {handle.pgid}\n"
            f"Command: {_clip_command(command)}\n"
            f"Working directory: {cwd}\n"
            f"To stop: kill -- -{handle.pgid}"
        )

    # ------------------------------------------------------------------
    # Foreground execution with inactivity timeout
    # ------------------------------------------------------------------

    def _run_foreground(
        self, command: str, cwd: Path, timeout: float,
        spec: "ShellSpec | Exhausted | None" = None, reserve: int = 0,
    ) -> str:
        """Run command, killing it after `timeout` seconds without any output.

        ``reserve`` is room left out of ``_MAX_OUTPUT_CHARS`` for what the caller
        appends afterwards.
        """
        try:
            result: ShellResult = self._get_shell().run(
                ShellRequest(
                    launch=self._launch(command, cwd, spec),
                    timeout=timeout,
                    on_chunk=self.output_callback,
                )
            )
        except LaunchRefused as refusal:
            # Same as the background face: a launch-stage refusal surfaces in the
            # floor's vocabulary, never as "Error starting command: …".
            return f"Error: {refusal.deny.reason}"
        except Exception as e:
            return f"Error starting command: {e}"

        if result.timed_out:
            # The two streams are decoded apart and only then joined: a CLIXML wrapper appears
            # on whichever one it appears on, and merging first would leave the extraction
            # looking for a structure that no longer starts where it starts. Partial output is
            # exactly where the wrapper is unterminated, so this usually reports the truncation
            # rather than unwrapping — which is the honest answer and better than raw XML.
            powershell = self._is_powershell(spec)
            streams = [
                _stream_of(
                    getattr(result, name), _omitted(result, name), _omitted_at(result, name),
                    powershell,
                )
                for name in ("stdout", "stderr")
            ]
            # Capped like every other output path. A command that emits megabytes and then
            # stalls is the ordinary shape of a timeout, and this branch used to be the one
            # place the tool handed all of it straight to the model. The cap covers the whole
            # message, command echo included; the echo is cut first, since the model wrote it.
            # Each stream is fitted on its own, so each notice sits inside the stream it
            # belongs to.
            msg = (
                f"Command timed out after {timeout:.0f}s of inactivity.\n"
                f"Command: {_clip_command(command)}"
            )
            needs = [_stream_need(st) for st in streams]
            if any(needs):
                prefix = "\n\nPartial output before timeout:\n"
                whole = [_fit_stream(st, n) for st, n in zip(streams, needs)]
                separator = "\n" if all(whole) and not whole[0].endswith("\n") else ""
                available = _MAX_OUTPUT_CHARS - reserve - len(msg) - len(prefix) - len(separator)
                budgets = _split_budget(needs[0], needs[1], available)
                fitted = [_fit_stream(st, bud) for st, bud in zip(streams, budgets)]
                msg += prefix + separator.join(f for f in fitted if f)
            return msg

        return self._format_result(
            result.returncode, result.stdout, result.stderr, powershell=self._is_powershell(spec),
            stdout_omitted=_omitted(result, "stdout"),
            stderr_omitted=_omitted(result, "stderr"),
            stdout_omitted_at=_omitted_at(result, "stdout"),
            stderr_omitted_at=_omitted_at(result, "stderr"),
            reserve=reserve,
        )

    # ------------------------------------------------------------------
    # Output formatting
    # ------------------------------------------------------------------

    def _format_result(
        self, returncode: int, stdout_raw: bytes, stderr_raw: bytes,
        powershell: bool = False, stdout_omitted: int = 0, stderr_omitted: int = 0,
        reserve: int = 0, stdout_omitted_at: int = 0, stderr_omitted_at: int = 0,
    ) -> str:
        stdout = _stream_of(stdout_raw, stdout_omitted, stdout_omitted_at, powershell)
        stderr = _stream_of(stderr_raw, stderr_omitted, stderr_omitted_at, powershell)
        stdout_need = _stream_need(stdout)
        stderr_need = _stream_need(stderr)

        status = ""
        if returncode < 0:
            status = f"Signal: {-returncode}"
        elif returncode != 0:
            status = f"Exit code: {returncode}"

        # The cap is on the string returned, not on the streams alone: headers, the status
        # line and every notice come out of the same budget. Past it, the result layer saves
        # this already-cut text to disk and calls it the command's "Full output".
        sections = [h for h in (stdout_need and "STDOUT:\n", stderr_need and "STDERR:\n", status) if h]
        available = (
            _MAX_OUTPUT_CHARS - reserve
            - sum(len(h) for h in sections) - 2 * (len(sections) - 1)
        )
        stdout_budget, stderr_budget = _split_budget(stdout_need, stderr_need, available)
        stdout_str = _fit_stream(stdout, stdout_budget) if stdout_need else ""
        stderr_str = _fit_stream(stderr, stderr_budget) if stderr_need else ""

        parts = []
        if stdout_str:
            parts.append(f"STDOUT:\n{stdout_str}")
        if stderr_str:
            parts.append(f"STDERR:\n{stderr_str}")
        if status:
            parts.append(status)

        return "\n\n".join(parts) if parts else "Command completed with no output."
