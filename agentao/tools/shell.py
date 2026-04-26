"""Shell command execution tool."""

import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

from .base import Tool
from ..sandbox import SandboxProfile
from ..security import PathPolicy, PathPolicyError

# Maximum combined stdout+stderr before truncation (~10K tokens).
# Matches Gemini CLI's default threshold of 40,000 characters.
_MAX_OUTPUT_CHARS = 40_000

# Number of bytes to sniff for binary detection.
_BINARY_SNIFF_BYTES = 8_192


def _is_binary(data: bytes) -> bool:
    """Return True if data looks like binary (null bytes present)."""
    return b"\x00" in data[:_BINARY_SNIFF_BYTES]


def _decode(raw: bytes) -> str:
    """Decode bytes to str, flagging binary content."""
    if not raw:
        return ""
    if _is_binary(raw):
        return f"[binary output — {len(raw):,} bytes not shown]"
    return raw.decode("utf-8", errors="replace")


def _truncate_tail(text: str, max_chars: int) -> str:
    """Keep the tail of text (most recent output), noting how much was omitted."""
    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return f"[... {omitted:,} chars omitted (showing last {max_chars:,}) ...]\n" + text[-max_chars:]


_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


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

    The resulting string is a single POSIX shell expression that, when run
    under `/bin/sh -c`, will exec `sandbox-exec` with the required -D /-f
    flags and pass the original command to a fresh `/bin/sh -c` inside the
    sandbox. stdout / stderr / exit code propagate normally.
    """
    if not IS_MACOS:
        return command
    prefix = " ".join(shlex.quote(a) for a in profile.as_args())
    return f"{prefix} /bin/sh -c {shlex.quote(command)}"


_SANDBOX_DENIAL_MARKERS = (
    "Operation not permitted",
    "deny file-write",
    "deny network",
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
    """
    if any(m in result for m in _SANDBOX_DENIAL_MARKERS):
        hint = (
            f"\n\n[Sandbox hint] The command ran under macOS sandbox profile "
            f"'{profile.name}'. If the failure looks like a capability denial "
            f"(file-write outside workspace, network access, etc.) rather than "
            f"a real command error, ask the user to run `/sandbox off` or switch "
            f"profile via `/sandbox profile <name>`."
        )
        return result + hint
    return result


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
        shell_desc = "cmd /c <command>" if IS_WINDOWS else "bash -c <command>"
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
                    "Exact command to execute. "
                    "On Unix runs as `bash -c <command>`; "
                    "on Windows runs as `cmd /c <command>`."
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
    ) -> str:
        """Execute shell command.

        `_sandbox_profile` is a private parameter (not exposed via `parameters`)
        that ToolRunner injects when a macOS sandbox policy is active. When
        set, the command is wrapped in `sandbox-exec` before spawning.
        """
        try:
            cwd = PathPolicy.for_tool(self).contain_directory(working_directory)
        except PathPolicyError as e:
            return f"Error: {e}"
        if not cwd.is_dir():
            return (
                f"Error: working_directory '{working_directory}' does not exist "
                "or is not a directory."
            )

        if _sandbox_profile is not None:
            wrapped = _wrap_with_sandbox(command, _sandbox_profile)
        else:
            wrapped = command

        if is_background:
            result = self._run_background(wrapped, cwd)
        else:
            result = self._run_foreground(wrapped, cwd, timeout)

        if _sandbox_profile is not None:
            result = _annotate_sandbox_denial(result, _sandbox_profile)
        return result

    # ------------------------------------------------------------------
    # Background execution
    # ------------------------------------------------------------------

    def _run_background(self, command: str, cwd: Path) -> str:
        """Start command detached; return PID (and PGID on Unix) immediately."""
        try:
            if IS_WINDOWS:
                proc = subprocess.Popen(
                    command,
                    shell=True,
                    cwd=cwd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                )
                return (
                    f"Background process started.\n"
                    f"PID: {proc.pid}\n"
                    f"Command: {command}\n"
                    f"Working directory: {cwd}\n"
                    f"To stop: taskkill /F /T /PID {proc.pid}"
                )
            else:
                proc = subprocess.Popen(
                    command,
                    shell=True,
                    cwd=cwd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,  # equivalent to preexec_fn=os.setsid
                )
                pgid = os.getpgid(proc.pid)
                return (
                    f"Background process started.\n"
                    f"PID: {proc.pid}\n"
                    f"PGID: {pgid}\n"
                    f"Command: {command}\n"
                    f"Working directory: {cwd}\n"
                    f"To stop: kill -- -{pgid}"
                )
        except Exception as e:
            return f"Error starting background command: {e}"

    # ------------------------------------------------------------------
    # Foreground execution with inactivity timeout
    # ------------------------------------------------------------------

    def _run_foreground(self, command: str, cwd: Path, timeout: float) -> str:
        """Run command, killing it after `timeout` seconds without any output."""
        popen_kwargs: Dict[str, Any] = dict(
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if not IS_WINDOWS:
            popen_kwargs["start_new_session"] = True  # equivalent to preexec_fn=os.setsid
        try:
            proc = subprocess.Popen(command, **popen_kwargs)
        except Exception as e:
            return f"Error starting command: {e}"

        stdout_chunks: List[bytes] = []
        stderr_chunks: List[bytes] = []
        last_activity = [time.monotonic()]
        timed_out = [False]
        callback = self.output_callback

        def _read(stream, chunks: List[bytes], on_chunk: Optional[Callable[[str], None]] = None) -> None:
            for chunk in iter(lambda: stream.read(4096), b""):
                chunks.append(chunk)
                last_activity[0] = time.monotonic()
                if on_chunk and not _is_binary(chunk):
                    try:
                        on_chunk(chunk.decode("utf-8", errors="replace"))
                    except Exception:
                        pass  # Never let display errors kill the reader thread

        t_out = threading.Thread(target=_read, args=(proc.stdout, stdout_chunks, callback), daemon=True)
        t_err = threading.Thread(target=_read, args=(proc.stderr, stderr_chunks, callback), daemon=True)
        t_out.start()
        t_err.start()

        # Poll for inactivity timeout
        while proc.poll() is None:
            if time.monotonic() - last_activity[0] > timeout:
                timed_out[0] = True
                try:
                    if IS_WINDOWS:
                        # Kill the entire process tree: shell=True wraps the real
                        # command in cmd.exe, so proc.kill() alone would only
                        # terminate the wrapper and leave children running.
                        subprocess.run(
                            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                            capture_output=True,
                        )
                    else:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    proc.kill()
                break
            time.sleep(0.05)

        t_out.join(timeout=2)
        t_err.join(timeout=2)

        stdout_raw = b"".join(stdout_chunks)
        stderr_raw = b"".join(stderr_chunks)

        if timed_out[0]:
            partial = _decode(stdout_raw + stderr_raw)
            msg = f"Command timed out after {timeout:.0f}s of inactivity.\nCommand: {command}"
            if partial:
                msg += f"\n\nPartial output before timeout:\n{partial}"
            return msg

        return self._format_result(proc.returncode, stdout_raw, stderr_raw)

    # ------------------------------------------------------------------
    # Output formatting
    # ------------------------------------------------------------------

    def _format_result(
        self, returncode: int, stdout_raw: bytes, stderr_raw: bytes
    ) -> str:
        stdout_str = _decode(stdout_raw)
        stderr_str = _decode(stderr_raw)

        # Clean up carriage-return sequences and ANSI codes before sending to LLM.
        # Progress bars use \r to overwrite lines in a terminal; without this,
        # the LLM receives all intermediate states as separate lines of noise.
        stdout_str = _strip_ansi(_collapse_carriage_returns(stdout_str))
        stderr_str = _strip_ansi(_collapse_carriage_returns(stderr_str))

        # Truncate: keep tail of each stream proportionally
        total = len(stdout_str) + len(stderr_str)
        if total > _MAX_OUTPUT_CHARS:
            # Allocate budget proportionally; give at least 1 char if non-empty
            if stdout_str and stderr_str:
                ratio = len(stdout_str) / total
                stdout_budget = max(1, int(_MAX_OUTPUT_CHARS * ratio))
                stderr_budget = max(1, _MAX_OUTPUT_CHARS - stdout_budget)
            elif stdout_str:
                stdout_budget, stderr_budget = _MAX_OUTPUT_CHARS, 0
            else:
                stdout_budget, stderr_budget = 0, _MAX_OUTPUT_CHARS

            stdout_str = _truncate_tail(stdout_str, stdout_budget)
            stderr_str = _truncate_tail(stderr_str, stderr_budget)

        parts = []
        if stdout_str:
            parts.append(f"STDOUT:\n{stdout_str}")
        if stderr_str:
            parts.append(f"STDERR:\n{stderr_str}")

        if returncode < 0:
            parts.append(f"Signal: {-returncode}")
        elif returncode != 0:
            parts.append(f"Exit code: {returncode}")

        return "\n\n".join(parts) if parts else "Command completed with no output."
