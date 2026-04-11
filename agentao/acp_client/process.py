"""Per-server subprocess handle for ACP client.

Each configured ACP server gets one :class:`ACPProcessHandle` that owns the
``subprocess.Popen`` instance and tracks the runtime state machine.

This module handles **only** process lifecycle (start / stop / restart) and
stderr consumption.  JSON-RPC framing, handshake, and request routing are
layered on top in Issues 03–04.
"""

from __future__ import annotations

import collections
import logging
import os
import subprocess
import threading
from typing import List, Optional

from .models import AcpProcessInfo, AcpServerConfig, ServerState

# Default capacity for the stderr ring buffer (number of lines).
_STDERR_RING_CAPACITY = 200

logger = logging.getLogger("agentao.acp_client")


class ACPProcessHandle:
    """Manages the lifecycle of a single ACP server subprocess.

    The handle owns:
    - The ``Popen`` object (and therefore stdin/stdout/stderr pipes).
    - A background thread that drains stderr so the subprocess never blocks.
    - The :class:`AcpProcessInfo` snapshot visible to the CLI.

    Thread safety: all state mutations go through :meth:`_set_state` which is
    guarded by ``_lock``.  Public methods (``start``, ``stop``, ``restart``)
    acquire the lock at the entry point.
    """

    def __init__(self, name: str, config: AcpServerConfig) -> None:
        self.name = name
        self.config = config
        self.info = AcpProcessInfo()
        self._proc: Optional[subprocess.Popen] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        # Bounded ring buffer that keeps the most recent stderr lines.
        self._stderr_ring: collections.deque = collections.deque(
            maxlen=_STDERR_RING_CAPACITY
        )

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _set_state(self, state: ServerState, error: Optional[str] = None) -> None:
        """Transition to *state*, optionally recording an error."""
        self.info.state = state
        if error is not None:
            self.info.last_error = error
        self.info.touch()

    @property
    def state(self) -> ServerState:
        return self.info.state

    @property
    def pid(self) -> Optional[int]:
        return self.info.pid

    @property
    def stdin(self):
        """Raw stdin pipe of the subprocess (used by JSON-RPC layer)."""
        return self._proc.stdin if self._proc else None

    @property
    def stdout(self):
        """Raw stdout pipe of the subprocess (used by JSON-RPC layer)."""
        return self._proc.stdout if self._proc else None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch the server subprocess.

        Idempotent: if the process is already running, this is a no-op.
        After a successful ``start`` the state is ``STARTING``.  The caller
        (or Issue 03) is responsible for advancing to ``INITIALIZING`` /
        ``READY`` once the ACP handshake completes.

        Raises:
            RuntimeError: If the process fails to start (state → ``FAILED``).
        """
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                # Already running — no-op.
                return

            self._set_state(ServerState.STARTING)

            env = dict(os.environ)
            env.update(self.config.env)

            try:
                self._proc = subprocess.Popen(
                    [self.config.command, *self.config.args],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=self.config.cwd,
                    env=env,
                )
            except Exception as exc:
                self._set_state(ServerState.FAILED, str(exc))
                raise RuntimeError(
                    f"acp server '{self.name}': failed to start: {exc}"
                ) from exc

            self.info.pid = self._proc.pid

            # Drain stderr in background so the child never blocks on a full
            # pipe buffer.
            self._stderr_thread = threading.Thread(
                target=self._drain_stderr,
                name=f"acp-stderr-{self.name}",
                daemon=True,
            )
            self._stderr_thread.start()

            # Check for immediate crash (e.g. bad executable path resolved
            # by the OS but the binary exits instantly).
            try:
                self._proc.wait(timeout=0.05)
            except subprocess.TimeoutExpired:
                pass  # Still running — good.
            else:
                rc = self._proc.returncode
                self._set_state(
                    ServerState.FAILED,
                    f"process exited immediately with code {rc}",
                )
                raise RuntimeError(
                    f"acp server '{self.name}': process exited immediately "
                    f"with code {rc}"
                )

            self.info.touch()
            logger.info(
                "acp server '%s' started (pid %d)", self.name, self._proc.pid
            )

    def stop(self) -> None:
        """Gracefully stop the subprocess.

        Sends SIGTERM, waits up to 5 s, then kills.  Idempotent.
        """
        with self._lock:
            self._stop_unlocked()

    def _stop_unlocked(self) -> None:
        """Inner stop without lock (called from ``stop`` and ``restart``)."""
        if self._proc is None:
            self._set_state(ServerState.STOPPED)
            return

        if self._proc.poll() is not None:
            # Already exited.
            self._set_state(ServerState.STOPPED)
            self._cleanup_proc()
            return

        self._set_state(ServerState.STOPPING)

        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning(
                "acp server '%s' did not stop within 5 s — killing", self.name
            )
            self._proc.kill()
            self._proc.wait(timeout=2)
        except Exception as exc:
            logger.error(
                "acp server '%s': error during stop: %s", self.name, exc
            )

        self._set_state(ServerState.STOPPED)
        self._cleanup_proc()

    def restart(self) -> None:
        """Stop (if running) then start a fresh subprocess."""
        with self._lock:
            self._stop_unlocked()
        # start() acquires its own lock.
        self.start()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cleanup_proc(self) -> None:
        """Release references to the old process and stderr thread."""
        self.info.pid = None
        self._proc = None
        self._stderr_thread = None

    def get_stderr_tail(self, n: int = 50) -> List[str]:
        """Return the last *n* lines captured from the server's stderr.

        Thread-safe.  Returns an empty list if the process has never been
        started or no stderr output has been produced.
        """
        lines = list(self._stderr_ring)
        return lines[-n:] if len(lines) > n else lines

    def _drain_stderr(self) -> None:
        """Read stderr line-by-line until EOF.  Runs in a daemon thread."""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for raw_line in proc.stderr:
                line = (
                    raw_line.decode("utf-8", errors="replace").rstrip()
                    if isinstance(raw_line, bytes)
                    else raw_line.rstrip()
                )
                if line:
                    self._stderr_ring.append(line)
                    logger.debug("acp[%s] stderr: %s", self.name, line)
        except Exception:
            # Process gone or pipe broken — expected during teardown.
            pass
