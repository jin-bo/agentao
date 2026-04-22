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
import queue
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
        # Stdout routing: one feeder per process lifetime puts raw lines here;
        # the current ACPClient subscribes its own queue via subscribe_stdout().
        self._stdout_subscriber: Optional[queue.Queue] = None
        self._subscriber_lock = threading.Lock()
        # When a feeder thread reaches EOF with no current subscriber it parks
        # the proc reference here.  subscribe_stdout() checks this and delivers
        # the EOF sentinel immediately so fast-crash clients don't hang.
        self._stdout_eof_pending: Optional[subprocess.Popen] = None

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

            # One feeder per process lifetime: reads stdout and routes each
            # line to whoever is currently subscribed via subscribe_stdout().
            # This guarantees only one consumer is ever attached to the pipe.
            feeder = threading.Thread(
                target=self._feed_stdout,
                args=(self._proc,),
                name=f"acp-feeder-{self.name}",
                daemon=True,
            )
            feeder.start()

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
    # Stdout subscription (used by ACPClient)
    # ------------------------------------------------------------------

    def subscribe_stdout(self, q: "queue.Queue[Optional[bytes]]") -> None:
        """Route stdout lines to *q* until :meth:`unsubscribe_stdout` is called."""
        with self._subscriber_lock:
            self._stdout_subscriber = q
            # If the feeder already reached EOF for the current process without
            # a subscriber present, deliver the sentinel immediately so the
            # caller's read loop doesn't block until an RPC timeout.
            if self._stdout_eof_pending is not None and (
                self._stdout_eof_pending is self._proc or self._proc is None
            ):
                self._stdout_eof_pending = None
                q.put(None)

    def unsubscribe_stdout(self, q: "queue.Queue[Optional[bytes]]") -> None:
        """Stop routing to *q*. No-op if *q* is not the current subscriber."""
        with self._subscriber_lock:
            if self._stdout_subscriber is q:
                self._stdout_subscriber = None

    def _feed_stdout(self, proc: subprocess.Popen) -> None:
        """Daemon thread: read stdout of *proc* and route lines to subscriber.

        Captures *proc* by argument so the feeder keeps reading from the
        correct pipe even after ``self._proc`` is reassigned during restart.
        Sends ``None`` (EOF sentinel) to the active subscriber when stdout
        closes so ``ACPClient._read_loop`` can exit cleanly.
        """
        stdout = proc.stdout
        if stdout is None:
            return
        last_sub = None
        try:
            for raw_line in stdout:
                with self._subscriber_lock:
                    sub = self._stdout_subscriber
                # Only route to subscriber if this feeder's process is still
                # the active one.  After a restart self._proc points to the new
                # Popen, so proc is self._proc becomes False and stale output
                # from the old process is never injected into the new client.
                if sub is not None and proc is self._proc:
                    sub.put(raw_line)
                    last_sub = sub
        except Exception:
            pass
        # Deliver the EOF sentinel so ACPClient._read_loop exits promptly.
        with self._subscriber_lock:
            current_sub = self._stdout_subscriber
            if last_sub is not None:
                # Send only to the subscriber that received data from this
                # process; a new client after restart must not get a spurious
                # EOF that would break its handshake.
                if last_sub is current_sub:
                    last_sub.put(None)
            elif current_sub is not None:
                # No lines delivered (process died before writing anything).
                # Send EOF only when no restart has taken place.
                if proc is self._proc or self._proc is None:
                    current_sub.put(None)
            else:
                # No subscriber at all when feeder exited.  Park the proc so
                # subscribe_stdout() can deliver the sentinel to the next
                # caller instead of letting it block until an RPC timeout.
                if proc is self._proc or self._proc is None:
                    self._stdout_eof_pending = proc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cleanup_proc(self) -> None:
        """Release references to the old process and stderr thread."""
        self.info.pid = None
        self._proc = None
        self._stderr_thread = None
        with self._subscriber_lock:
            self._stdout_eof_pending = None

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
