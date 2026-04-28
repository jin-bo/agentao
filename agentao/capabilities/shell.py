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
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

IS_WINDOWS = sys.platform == "win32"


@dataclass(frozen=True)
class ShellRequest:
    """Single foreground shell command request."""

    command: str
    cwd: Path
    timeout: float = 120.0
    on_chunk: Optional[Callable[[str], None]] = None
    env: Optional[Dict[str, str]] = None


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
    """

    def run(self, request: ShellRequest) -> ShellResult:
        ...

    def run_background(self, request: ShellRequest) -> BackgroundHandle:
        ...


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


class LocalShellExecutor:
    """Default :class:`ShellExecutor` using ``subprocess.Popen``.

    Mirrors :func:`agentao.tools.shell.ShellTool._run_foreground` /
    ``_run_background`` exactly: shell=True wrapping, stdin detach
    (so children never inherit the ACP JSON-RPC channel), process
    group leadership for clean kill, inactivity-based timeout, and
    ``taskkill`` / ``killpg`` teardown by platform.
    """

    def run(self, request: ShellRequest) -> ShellResult:
        popen_kwargs: Dict[str, Any] = dict(
            shell=True,
            cwd=request.cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if request.env is not None:
            popen_kwargs["env"] = request.env
        if not IS_WINDOWS:
            popen_kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(request.command, **popen_kwargs)
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
                try:
                    if IS_WINDOWS:
                        subprocess.run(
                            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                            stdin=subprocess.DEVNULL,
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

        return ShellResult(
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout=b"".join(stdout_chunks),
            stderr=b"".join(stderr_chunks),
            timed_out=timed_out[0],
        )

    def run_background(self, request: ShellRequest) -> BackgroundHandle:
        popen_kwargs: Dict[str, Any] = dict(
            shell=True,
            cwd=request.cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if request.env is not None:
            popen_kwargs["env"] = request.env

        if IS_WINDOWS:
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
            )
            proc = subprocess.Popen(request.command, **popen_kwargs)
            return BackgroundHandle(
                pid=proc.pid,
                pgid=None,
                command=request.command,
                cwd=request.cwd,
            )

        popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(request.command, **popen_kwargs)
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
