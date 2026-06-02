"""Hardened subprocess execution shared across the runtime.

A plain ``subprocess.run(..., timeout=)`` is unsafe for an embedded /
ACP-over-stdio host in two ways this module closes:

1. **Timeout reaps only the direct child.** On timeout ``subprocess.run``
   calls ``Popen.kill()``, which signals just the immediate child. A
   process that forked grandchildren (``git`` spawning credential helpers
   on Windows, a user hook running ``mytool &``) leaves them alive — and
   because they inherit the captured pipe's write end, ``communicate()``
   never sees EOF and the caller hangs far past the timeout.
2. **Inherited stdin.** With no ``stdin=`` the child inherits the host's
   stdin, which over ACP is the JSON-RPC channel — a child that reads it
   (a credential prompt) steals protocol bytes.

:func:`run_captured` runs the child in its own process group / session,
detaches or feeds stdin explicitly, and on timeout kills the *whole* tree
before re-raising :class:`subprocess.TimeoutExpired` so callers fall back
exactly as they did under ``subprocess.run``.

``LocalShellExecutor`` (``capabilities/shell.py``) keeps its own
reader-thread run loop for streaming + inactivity-timeout semantics, but
shares :func:`kill_process_tree` for teardown; batch callers
(``search_file_content``, plugin hook commands) use :func:`run_captured`.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Any, Dict, Optional, Sequence, Union

__all__ = ["run_captured", "kill_process_tree"]


def kill_process_tree(proc: "subprocess.Popen[Any]") -> None:
    """Best-effort kill of ``proc`` *and every descendant it spawned*.

    ``Popen.kill()`` only signals the direct child, so a timed-out process
    that forked helpers leaves grandchildren holding the inherited pipe's
    write end, which keeps ``communicate()`` blocked. We address the whole
    tree: ``taskkill /T`` on Windows, the process group via ``killpg``
    elsewhere.
    """
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=5,
            )
            return
        except Exception:
            pass  # fall through to the single-process kill below
    else:
        # ``start_new_session=True`` made the child a session/group leader,
        # so its pgid == its pid. Use the pid directly rather than
        # ``os.getpgid(pid)``: if the direct child already exited (leaving a
        # grandchild holding the pipe), getpgid on the zombie can fail and
        # we'd lose the whole group. The group id stays valid while any
        # member lives, so ``killpg(pid)`` still reaps the grandchild.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except Exception:
            pass  # fall through to the single-process kill below
    try:
        proc.kill()
    except Exception:
        pass


def run_captured(
    cmd: Union[Sequence[str], str],
    *,
    cwd: Optional[str] = None,
    timeout: Optional[float] = None,
    input: Optional[str] = None,
    shell: bool = False,
) -> "subprocess.CompletedProcess[str]":
    """Run ``cmd`` capturing text stdout/stderr, hardened for timeouts.

    Behaves like ``subprocess.run(capture_output=True, text=True, ...)``
    with three differences that matter for an embedded / ACP-over-stdio
    host:

    - The child leads its own process group / session, so a timeout can
      reap the *entire* tree (see :func:`kill_process_tree`) rather than
      just the direct child.
    - stdin is handled explicitly: ``input`` is fed over a pipe when
      given, otherwise stdin is detached (``DEVNULL``) so a child can
      never read — and thereby steal — the host's stdin stream.
    - Output is decoded with ``errors="replace"`` so a non-UTF-8 line
      can't raise ``UnicodeDecodeError`` (which is neither
      ``SubprocessError`` nor ``OSError``, and would escape callers'
      ``except`` clauses and abort the whole operation).

    On timeout the process tree is killed, the pipes drained, and the
    original :class:`subprocess.TimeoutExpired` re-raised. Spawn failures
    (missing binary) propagate as ``FileNotFoundError`` / ``OSError`` from
    :class:`subprocess.Popen`, exactly as ``subprocess.run`` would.
    """
    popen_kwargs: Dict[str, Any] = {
        "cwd": cwd,
        # Feed ``input`` over a pipe when provided; otherwise detach stdin.
        "stdin": subprocess.PIPE if input is not None else subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "errors": "replace",
        "shell": shell,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kwargs)
    try:
        stdout, stderr = proc.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_process_tree(proc)
        # The group is dead now, so the inherited pipe write ends are
        # released and this second drain returns promptly.
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
