"""ShellExecutor capability protocol and local default.

Wraps the foreground / background subprocess machinery used by
:class:`agentao.tools.shell.ShellTool` so embedded hosts can route
shell execution through Docker, a remote runner, or an audit proxy
without monkey-patching subprocess.

The default :class:`LocalShellExecutor` shells out via ``subprocess.Popen``
with the same flags (process-group leadership, stdin detach,
inactivity-timeout reads) as the pre-capability tool, so behavior is
byte-equivalent.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple, runtime_checkable

from .process import build_child_env, kill_process_tree
from .shell_spec import (
    Exhausted,
    LaunchRequest,
    LegacyLaunch,
    ShellBlock,
    ShellSpec,
    WindowsLaunch,
    default_spec,
)

IS_WINDOWS = sys.platform == "win32"


@lru_cache(maxsize=1)
def resolve_shell_executable() -> Optional[str]:
    """Path to pass as ``Popen(shell=True, executable=...)``, or ``None``.

    Python hardcodes ``/bin/sh`` for ``shell=True`` on POSIX, so without
    this every ``run_shell_command`` ran under a POSIX shell — dash on most
    Linux distributions — while the tool description promised the model
    bash. Resolving bash explicitly makes the promise true rather than
    walking it back: bashisms the model reaches for by default (process
    substitution ``<(...)``, ``${var//x/y}``) now work everywhere instead
    of only where ``/bin/sh`` happens to be bash.

    ``None`` means "keep Python's default", and it is the whole reason this
    returns an Optional instead of a constant: minimal images (Alpine,
    distroless) ship ``/bin/sh`` and no bash at all, where a hardcoded
    ``executable`` would turn every shell command into a
    ``FileNotFoundError``. Degrading to ``/bin/sh`` is correct there — but
    the *description* has to degrade with it, which is why
    :class:`agentao.tools.shell.ShellTool` builds its text from this
    function rather than from a literal.

    ``/bin/bash`` wins over a PATH lookup so the choice does not shift when
    a user installs a newer bash under ``/opt/homebrew`` or ``/usr/local``.
    Windows answers ``None`` and keeps ``%COMSPEC% /c``: the interpreter is
    chosen there by configuration (``LegacyLaunch.executable``), not by probing
    for a better one.
    """
    if IS_WINDOWS:
        return None
    if os.path.isfile("/bin/bash") and os.access("/bin/bash", os.X_OK):
        return "/bin/bash"
    return shutil.which("bash")


def shell_display_name() -> str:
    """The *platform's* shell, for display when no spec has been resolved.

    Never ``None`` — falls back to the POSIX default that
    :func:`resolve_shell_executable` returning ``None`` selects. A caller that
    holds a spec should use
    :func:`agentao.capabilities.shell_spec.display_name` instead: a configured
    interpreter is invisible from here.
    """
    if IS_WINDOWS:
        return "cmd"
    return resolve_shell_executable() or "/bin/sh"


@dataclass(frozen=True, kw_only=True)
class ShellRequest:
    """A shell run, carrying the launch agentao already decided on.

    The request used to be a command string plus a working directory, which meant the
    executor re-derived *what* would interpret that string, at spawn time, from the platform.
    The decision and the launch could therefore disagree — the floor judged one dialect and
    the process ran another — and nothing in the shape made that visible.

    It now carries a discriminated :data:`~agentao.capabilities.shell_spec.LaunchRequest`
    that names the launch completely. ``timeout`` and ``on_chunk`` stay outside it because
    they are transport concerns, unrelated to what gets started.

    Hosts with a custom ``ShellExecutor`` read ``request.launch`` instead of
    ``request.command`` / ``request.cwd`` / ``request.env``. An unconfigured host always sees
    a :class:`~agentao.capabilities.shell_spec.LegacyLaunch`, carrying exactly the three
    fields that were there before.
    """

    launch: LaunchRequest
    timeout: float = 120.0
    on_chunk: Optional[Callable[[str], None]] = None

    @property
    def command(self) -> str:
        """The text a display or a background handle shows for this launch.

        Kept as a read-only projection rather than a field: display paths and the background
        handle want the text, and re-deriving it at each of those call sites is how two
        spellings of "the command" start to drift.
        """
        if isinstance(self.launch, LegacyLaunch):
            return self.launch.command
        return self.launch.command_line

    @property
    def cwd(self) -> Path:
        """The directory the child starts in — the call's own for a legacy launch."""
        return Path(self.launch.cwd)


@dataclass
class ShellResult:
    """Result of a foreground shell run."""

    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""
    timed_out: bool = False


@dataclass
class BackgroundHandle:
    """Handle to a detached background process."""

    pid: int
    pgid: Optional[int] = None  # None on Windows
    command: str = ""
    cwd: Path = field(default_factory=lambda: Path("."))


@runtime_checkable
class ShellExecutor(Protocol):
    """IO contract for shell execution.

    Two operations: foreground ``run`` (caller waits for completion or
    inactivity timeout) and ``run_background`` (caller gets a handle
    immediately while the process continues detached). Hosts that
    cannot support real backgrounding can raise ``NotImplementedError``
    in ``run_background`` — :class:`agentao.tools.shell.ShellTool`
    surfaces it as a normal tool error.

    **Declaring the interpreter (optional).** An executor may additionally
    implement :class:`~agentao.capabilities.shell_spec.ShellSpecProvider` — a
    ``shell_spec`` property answering ``ShellSpec | Exhausted`` — because it is
    the only party that knows: a Docker or remote executor starts a different
    interpreter, on a different filesystem, as a different subject, and none of
    that is visible from here. It is deliberately *not* a member of this
    protocol: ``ShellExecutor`` is ``@runtime_checkable``, and a non-method
    member makes ``issubclass()`` against it raise ``TypeError`` outright while
    flipping ``isinstance()`` to ``False`` for every executor written before the
    member existed. An executor that does not declare one is read as reporting
    today's platform default, so those hosts keep working unchanged.
    """

    def run(self, request: ShellRequest) -> ShellResult:
        ...

    def run_background(self, request: ShellRequest) -> BackgroundHandle:
        ...


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def _popen_target(launch: LaunchRequest) -> Tuple[Any, Dict[str, Any]]:
    """The ``Popen`` first argument and the launch-shaped keyword arguments.

    One helper, both delivery faces. ``is_background`` used to choose a second
    spawn path with its own ``shell=True`` and its own environment, which meant
    a property proved about one face said nothing about the other.
    """
    if isinstance(launch, LegacyLaunch):
        # ``launch.executable`` is the interpreter the user named, and it wins over the
        # platform's answer on both platforms: POSIX ``shell=True`` runs ``/bin/sh -c`` with
        # ``args[0]`` replaced by ``executable``, and Windows ``shell=True`` builds
        # ``{ComSpec} /c …`` with ``executable`` substituted for ``ComSpec``. Falling back to
        # ``resolve_shell_executable()`` is what an unconfigured host has always got.
        return launch.command, dict(
            shell=True,
            executable=launch.executable or resolve_shell_executable(),
            cwd=str(launch.cwd),
            env=dict(launch.env),
        )
    # A named interpreter: no shell in between, the image fixed by path rather than resolved
    # from a name at spawn time, and the command line passed through verbatim.
    return launch.command_line, dict(
        shell=False,
        executable=str(launch.application_name),
        cwd=str(launch.cwd),
        env=dict(launch.env),
    )


class LocalShellExecutor:
    """Default :class:`ShellExecutor` using ``subprocess.Popen``.

    Everything around the spawn is unchanged from the pre-capability tool: stdin detach (so
    children never inherit the ACP JSON-RPC channel), process-group leadership for a clean
    kill, an inactivity-based timeout, and ``taskkill`` / ``killpg`` teardown by platform.
    What varies is only the spawn itself, and that comes entirely from the launch — see
    :func:`_popen_target`.
    """

    def __init__(self, shell_block: "ShellBlock | None" = None) -> None:
        # The user-level shell block, or ``None`` when nothing supplied one. Held rather than
        # resolved here, because resolving it touches the filesystem and the answer has to be
        # the same one every call sees.
        self._shell_block = shell_block
        self._spec: "ShellSpec | Exhausted | None" = None

    @property
    def shell_spec(self) -> "ShellSpec | Exhausted":
        """What will interpret this executor's commands, resolved once.

        Held rather than recomputed: discovery touches the filesystem, and a
        call that read one answer must not find a different one at the launch.
        """
        if self._spec is None:
            self._spec = default_spec(self._shell_block)
        return self._spec

    def run(self, request: ShellRequest) -> ShellResult:
        target, popen_kwargs = _popen_target(request.launch)
        popen_kwargs.update(
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if not IS_WINDOWS:
            popen_kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(target, **popen_kwargs)
        except Exception as e:
            return ShellResult(
                returncode=-1,
                stdout=b"",
                stderr=f"Error starting command: {e}".encode("utf-8"),
                timed_out=False,
            )

        stdout_chunks: List[bytes] = []
        stderr_chunks: List[bytes] = []
        last_activity = [time.monotonic()]
        timed_out = [False]
        on_chunk = request.on_chunk

        def _read(stream, chunks: List[bytes]) -> None:
            for chunk in iter(lambda: stream.read(4096), b""):
                chunks.append(chunk)
                last_activity[0] = time.monotonic()
                if on_chunk and not _is_binary(chunk):
                    try:
                        on_chunk(chunk.decode("utf-8", errors="replace"))
                    except Exception:
                        pass

        t_out = threading.Thread(target=_read, args=(proc.stdout, stdout_chunks), daemon=True)
        t_err = threading.Thread(target=_read, args=(proc.stderr, stderr_chunks), daemon=True)
        t_out.start()
        t_err.start()

        timeout = request.timeout
        while proc.poll() is None:
            if time.monotonic() - last_activity[0] > timeout:
                timed_out[0] = True
                # Shared teardown: kills the whole tree (taskkill /T or
                # killpg) via the child's pid, so a grandchild holding the
                # captured pipe can't survive the kill — and sidesteps the
                # getpgid-on-a-zombie ProcessLookupError this used to hit.
                kill_process_tree(proc)
                break
            time.sleep(0.05)

        t_out.join(timeout=2)
        t_err.join(timeout=2)

        return ShellResult(
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout=b"".join(stdout_chunks),
            stderr=b"".join(stderr_chunks),
            timed_out=timed_out[0],
        )

    def run_background(self, request: ShellRequest) -> BackgroundHandle:
        # ``is_background`` chooses which method delivers the request, and nothing else. It
        # used to choose a second spawn path with its own ``shell=True`` and its own
        # environment, which meant a property proved about one face said nothing about the
        # other.
        target, popen_kwargs = _popen_target(request.launch)
        popen_kwargs.update(
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if IS_WINDOWS:
            # ``CREATE_NO_WINDOW``, not ``DETACHED_PROCESS``. Measured on a Windows runner:
            # under ``DETACHED_PROCESS`` both ``pwsh`` and ``powershell`` exit **0 with empty
            # stdout and stderr without running the script at all** — a background command
            # reported as started, and silently never run. PowerShell hosts itself in a
            # console and there is none to host it in; ``CREATE_NO_WINDOW`` gives the child
            # its own console that is never shown, and the body runs. The two flags are
            # mutually exclusive, so this is a swap rather than an addition.
            #
            # cmd is unaffected either way, which is why nothing saw this until a dialect
            # arrived that is not cmd. Both are covered by
            # ``tests/test_windows_launch_matrix.py``.
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            )
            proc = subprocess.Popen(target, **popen_kwargs)
            return BackgroundHandle(
                pid=proc.pid,
                pgid=None,
                command=request.command,
                cwd=request.cwd,
            )

        popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(target, **popen_kwargs)
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            pgid = None
        return BackgroundHandle(
            pid=proc.pid,
            pgid=pgid,
            command=request.command,
            cwd=request.cwd,
        )
