"""Read-only status / readiness accessors for :class:`ACPManager`.

Provides :class:`StatusMixin`.  Owns the typed ``get_status`` snapshot,
the ``readiness`` classifier that consumers use to gate submissions,
and the simple property accessors for config / handles / clients.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Literal, Optional

from ..client import ACPClient, AcpClientError, AcpServerNotFound
from ..models import AcpClientConfig, ServerState, ServerStatus
from ..process import ACPProcessHandle


class StatusMixin:
    """Accessors + diagnostic snapshots for :class:`ACPManager`."""

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def config(self) -> AcpClientConfig:
        return self._config

    @property
    def server_names(self) -> List[str]:
        return list(self._handles.keys())

    def get_handle(self, name: str) -> Optional[ACPProcessHandle]:
        return self._handles.get(name)

    def get_client(self, name: str) -> Optional[ACPClient]:
        return self._clients.get(name)

    # ------------------------------------------------------------------
    # Diagnostics (Issue 07)
    # ------------------------------------------------------------------

    def get_server_logs(self, name: str, n: int = 50) -> List[str]:
        """Return the last *n* stderr lines for a server.

        Raises:
            KeyError: If *name* is not a configured server.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)
        return handle.get_stderr_tail(n)

    # ------------------------------------------------------------------
    # Typed status snapshot
    # ------------------------------------------------------------------

    def get_status(self) -> List[ServerStatus]:
        """Return a typed status snapshot for every configured server.

        Returns one :class:`ServerStatus` per server registered with the
        manager. ``has_active_turn`` is derived from the manager's active
        turn slot (not from handle state), so it stays ``True`` during
        in-flight interactions that would otherwise surface as
        ``WAITING_FOR_USER`` on the handle.

        Week 2 adds diagnostic fields additively. The four v1 fields
        (``server``, ``state``, ``pid``, ``has_active_turn``) retain
        their Week 1 semantics. New fields — ``active_session_id``,
        ``last_error``, ``last_error_at``, ``inbox_pending``,
        ``interaction_pending``, ``config_warnings`` — are read from
        the authoritative manager-owned stores (client connection info,
        ``_last_errors``, the shared :class:`Inbox`, the interaction
        registry, and ``_config_warnings``). ``last_error_at`` is a
        timezone-aware UTC ``datetime`` whose value is set at the
        moment the error is stored, not at the moment it was raised.
        """
        result: List[ServerStatus] = []
        with self._active_turns_lock:
            active_names = set(self._active_turns.keys())
        with self._last_errors_lock:
            last_errors = dict(self._last_errors)
        # Inbox counts are derived from a peek snapshot — cheap because
        # the default capacity is small (256) and the filter only reads
        # an immutable ``server`` field on each message.
        inbox_by_server: Dict[str, int] = {}
        for msg in self.inbox.peek():
            inbox_by_server[msg.server] = inbox_by_server.get(msg.server, 0) + 1
        for name, handle in self._handles.items():
            # Eagerly revalidate cached process liveness so a crashed
            # server is never reported as "ready" or "busy" when it is
            # actually dead. The probe runs for READY, BUSY, and
            # WAITING_FOR_USER — all states where the process is expected
            # to be alive but may have silently exited.
            if handle.info.state in (
                ServerState.READY,
                ServerState.BUSY,
                ServerState.WAITING_FOR_USER,
            ):
                proc = handle._proc
                if proc is not None and proc.poll() is not None:
                    try:
                        self._check_cached_client_alive(name, count_restart=False)
                    except AcpClientError:
                        pass
            info = handle.info
            active_session_id: Optional[str] = None
            client = self._clients.get(name)
            if client is not None:
                active_session_id = client.connection_info.session_id or None
            if active_session_id is None:
                with self._ephemeral_lock:
                    eph = self._ephemeral_clients.get(name)
                if eph is not None:
                    active_session_id = eph.connection_info.session_id or None
            err = last_errors.get(name)
            # Fall back to the handle-level error (populated by
            # ``ACPProcessHandle._set_state(FAILED, ...)``) when the
            # manager-side store has nothing. Otherwise startup failures
            # that never hit ``_record_last_error`` — ``start_server`` /
            # ``start_all`` exceptions, crashes before any prompt — leave
            # ``state == "failed"`` with ``last_error == None`` on the
            # typed snapshot, forcing embedders back to ``handle.info``.
            # ``last_error_at`` stays ``None`` in the fallback path because
            # the handle does not record a distinct error timestamp.
            if err:
                last_error_msg: Optional[str] = err[0]
                last_error_ts: Optional[datetime] = err[1]
            else:
                last_error_msg = info.last_error
                last_error_ts = None
            result.append(ServerStatus(
                server=name,
                state=info.state.value,
                pid=info.pid,
                has_active_turn=name in active_names,
                active_session_id=active_session_id,
                last_error=last_error_msg,
                last_error_at=last_error_ts,
                inbox_pending=inbox_by_server.get(name, 0),
                interaction_pending=len(self.interactions.list_pending(server=name)),
                config_warnings=list(self._config_warnings.get(name, [])),
            ))
        return result

    # ------------------------------------------------------------------
    # Week 2: readiness classifier
    # ------------------------------------------------------------------

    def readiness(
        self, name: str,
    ) -> Literal["ready", "busy", "failed", "not_ready"]:
        """Return a typed readiness classification for *name*.

        Consumers that only want to know whether to submit a new turn
        should call this rather than string-matching on ``state``. The
        mapping is intentionally coarse and stable; it **is not** a
        replacement for the raw ``state`` — use ``get_status()`` for
        diagnostics.

        Returned values:

        - ``"ready"`` — server is up and has no active turn; safe to
          submit a new ``prompt_once`` / ``send_prompt``.
        - ``"busy"`` — a turn is in flight (manager slot occupied) or
          the handle is in ``BUSY`` / ``WAITING_FOR_USER``.
        - ``"failed"`` — server requires explicit recovery before it will
          accept turns again. This covers: sticky-fatal servers
          (``is_fatal()`` is True), servers whose process never launched
          (e.g. missing executable), and servers whose process has already
          exited while the handle is in ``FAILED``.
        - ``"not_ready"`` — server is still coming up or winding down
          (``CONFIGURED`` / ``STARTING`` / ``INITIALIZING`` /
          ``STOPPING`` / ``STOPPED``), or the handle is in a transient
          ``FAILED`` state with a live process that will self-resolve on
          the next call (e.g. a recoverable ``session/new`` error with a
          bad ``cwd`` override).

        Raises :class:`AcpServerNotFound` if *name* is not configured.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)
        state = handle.info.state
        if state is ServerState.FAILED:
            # Sticky-fatal: manager gave up; requires explicit recovery.
            with self._recovery_lock:
                is_sticky = name in self._fatal_servers
            if is_sticky:
                return "failed"
            # Process never launched (e.g. missing executable) or has
            # already exited — durable failure, not a transient per-call
            # error that can self-resolve on the next send_prompt.
            proc = handle._proc
            if proc is None or proc.poll() is not None:
                return "failed"
            # Live process in FAILED: transient per-call error (e.g. a
            # bad cwd override on session/new) that will self-resolve.
            return "not_ready"
        with self._active_turns_lock:
            has_active = name in self._active_turns
        if has_active or state in (
            ServerState.BUSY,
            ServerState.WAITING_FOR_USER,
        ):
            # Revalidate: the subprocess may have died mid-turn. If so,
            # report the real state rather than leaving callers stuck on
            # "busy" forever.
            proc = handle._proc
            if proc is not None and proc.poll() is not None:
                try:
                    self._check_cached_client_alive(name, count_restart=False)
                except AcpClientError:
                    return "failed"
                return "not_ready"
            return "busy"
        if state is ServerState.READY:
            proc = handle._proc
            if proc is not None and proc.poll() is not None:
                # Idle-crash guard: handle state lingers at READY until
                # ``_check_cached_client_alive`` runs. Eagerly classify
                # and evict the stale client here so a subsequent
                # ``prompt_once`` / ``ensure_connected`` triggers a
                # transparent restart rather than hitting the dead
                # transport. Fatal crashes raise → "failed". Recoverable
                # crashes evict + bump the counter and return "not_ready"
                # so callers know not to submit; a prompt submission
                # (``prompt_once`` / ``send_prompt``) will restart the
                # server automatically.
                try:
                    self._check_cached_client_alive(name, count_restart=False)
                except AcpClientError:
                    return "failed"
                return "not_ready"
            return "ready"
        return "not_ready"

    def is_ready(self, name: str) -> bool:
        """Shortcut for ``readiness(name) == "ready"``."""
        return self.readiness(name) == "ready"
