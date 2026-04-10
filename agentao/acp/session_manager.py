"""ACP session registry.

Holds a thread-safe map of ``session_id`` → :class:`AcpSessionState` and
owns the per-session lifecycle: create, look up, delete, and close-on-
shutdown. The registry is deliberately thin — actual session construction
from ``session/new`` request parameters is Issue 04's job; Issue 03 just
guarantees the plumbing is correct, concurrent-safe, and robust to
shutdown.

Thread safety: Issue 01's dispatcher is single-threaded from stdin, but
later issues introduce worker threads:

  - Issue 07 (``ACPTransport``) emits ``session/update`` notifications from
    inside the agent-chat loop, which may run on a worker thread.
  - Issue 08 (``request_permission``) blocks a worker thread on a client
    response while the main loop continues processing other requests.
  - Issue 09 (``session/cancel``) lets the dispatcher cancel a turn that
    is still executing on another thread.

An :class:`threading.RLock` guards the internal dict so lookups during
``create_lazy``-style flows (where the handler holds the session while
still mutating it) remain safe.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, Iterator, List, Optional

from .models import AcpSessionState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SessionNotFoundError(KeyError):
    """Raised when a caller asks for a session that does not exist.

    Subclasses :class:`KeyError` so code using the manager in dict-like
    fashion catches it naturally.
    """

    def __init__(self, session_id: str) -> None:
        super().__init__(session_id)
        self.session_id = session_id

    def __str__(self) -> str:
        return f"ACP session not found: {self.session_id!r}"


class DuplicateSessionError(ValueError):
    """Raised when :meth:`AcpSessionManager.create` is called with an id
    that is already registered."""

    def __init__(self, session_id: str) -> None:
        super().__init__(session_id)
        self.session_id = session_id

    def __str__(self) -> str:
        return f"ACP session already exists: {self.session_id!r}"


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class AcpSessionManager:
    """Thread-safe registry of ACP sessions.

    Typical flow (fully realized in Issue 04 and later):

        state = AcpSessionState(session_id=new_id, agent=agent, ...)
        manager.create(state)
        ...
        state = manager.require(session_id)   # for session/prompt etc.
        ...
        manager.delete(session_id)            # on client-initiated teardown
        manager.close_all()                   # on server shutdown (Issue 01
                                              # entry point wires this into
                                              # AcpServer.run()'s finally)
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, AcpSessionState] = {}
        # Reentrant so the same thread can hold the lock while calling out
        # to a helper that also needs it (e.g. a future lazy-create path).
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Create / lookup / delete
    # ------------------------------------------------------------------

    def create(self, state: AcpSessionState) -> None:
        """Register a new session.

        Raises :class:`DuplicateSessionError` if ``state.session_id`` is
        already registered. Callers are expected to generate collision-free
        ids (Issue 04 will use UUIDs), so a duplicate is a protocol/bug
        signal worth surfacing.
        """
        with self._lock:
            if state.session_id in self._sessions:
                raise DuplicateSessionError(state.session_id)
            self._sessions[state.session_id] = state

    def get(self, session_id: str) -> Optional[AcpSessionState]:
        """Return the session state if present, else ``None``.

        Use :meth:`require` when a missing session is a protocol error.
        """
        with self._lock:
            return self._sessions.get(session_id)

    def require(self, session_id: str) -> AcpSessionState:
        """Return the session state or raise :class:`SessionNotFoundError`.

        Dispatch handlers for ``session/prompt``/``session/cancel``/etc.
        should use this — missing session means the client sent an invalid
        id and we want a clear error, not a silent ``None``.
        """
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                raise SessionNotFoundError(session_id)
            return state

    def delete(self, session_id: str) -> None:
        """Remove a session from the registry and close it.

        Raises :class:`SessionNotFoundError` when the id is not registered
        (mirroring :meth:`require`). The session's ``close`` is called
        *outside* the manager lock so a slow teardown doesn't block other
        operations; the session's own ``close`` is idempotent so concurrent
        deletes cannot double-close.
        """
        with self._lock:
            state = self._sessions.pop(session_id, None)
        if state is None:
            raise SessionNotFoundError(session_id)
        state.close()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def cancel_all_active_turns(self, reason: str = "connection-closed") -> None:
        """Trip every registered session's cancellation token, in place.

        Unlike :meth:`close_all` this does NOT remove sessions from the
        registry, does NOT call ``agent.close()``, and does NOT disconnect
        MCP servers — it only signals "stop the current turn" so any worker
        thread blocked inside an LLM call or tool execution can observe the
        cancellation, unwind, and return. This is what
        :meth:`~agentao.acp.server.AcpServer.run`'s shutdown sequence calls
        before draining the executor: without it, ``shutdown(wait=True)``
        would hang until every in-flight turn finished naturally because
        the worker has no other "stop now" signal.

        ``close_all`` runs after the executor drains so the heavier MCP
        teardown happens once handlers are no longer touching the runtime.
        """
        with self._lock:
            states: List[AcpSessionState] = list(self._sessions.values())

        for state in states:
            token = state.cancel_token
            if token is None:
                continue
            try:
                token.cancel(reason)
            except Exception:
                logger.exception(
                    "acp: error cancelling active turn for session %s during shutdown",
                    state.session_id,
                )

    def close_all(self) -> None:
        """Close every registered session. Idempotent and robust.

        Called from :meth:`~agentao.acp.server.AcpServer.run`'s finally
        block so stdio EOF (or any unhandled exception in the read loop)
        tears down Agentao runtimes cleanly.

        Robustness guarantees:

        - The lock is held only while snapshotting and clearing the
          internal dict, so per-session ``close`` calls can run
          concurrently with new ``get``/``create`` requests (there won't
          be any during real shutdown, but tests exercise it).
        - Exceptions from individual session closes are caught and logged;
          one misbehaving session cannot prevent sibling sessions from
          tearing down.
        """
        with self._lock:
            states: List[AcpSessionState] = list(self._sessions.values())
            self._sessions.clear()

        for state in states:
            try:
                state.close()
            except Exception:
                logger.exception("acp: error closing session %s during shutdown", state.session_id)

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)

    def __contains__(self, session_id: object) -> bool:
        with self._lock:
            return session_id in self._sessions

    def session_ids(self) -> List[str]:
        """Return a snapshot of the current session ids.

        Returns a list (not an iterator) so the caller is decoupled from
        registry mutations after the snapshot was taken.
        """
        with self._lock:
            return list(self._sessions.keys())
