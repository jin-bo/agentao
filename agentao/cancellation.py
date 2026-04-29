"""Cancellation token — Python equivalent of AbortSignal/AbortController."""

from __future__ import annotations

import logging
import threading
from typing import Callable, List, Optional


_logger = logging.getLogger(__name__)


class AgentCancelledError(Exception):
    """Raised when a CancellationToken has been cancelled."""

    def __init__(self, reason: str = "user-cancel"):
        self.reason = reason
        super().__init__(f"[Cancelled] {reason}")


class CancellationToken:
    """Lightweight per-turn cancellation token.

    Created at the start of each agent.chat() invocation and passed through
    the entire call stack: LLM streaming → tool execution → sub-agents.

    Usage:
        token = CancellationToken()
        token.cancel("user-cancel")   # signal cancellation
        token.check()                 # raises AgentCancelledError if cancelled
        token.is_cancelled            # non-throwing check

    The token also carries an optional ``runtime_loop`` set by
    :meth:`Agentao.arun` — the host event loop captured at async entry,
    needed by the AsyncTool dispatcher to bridge coroutines back onto the
    loop that owns any host-affine resources (aiohttp sessions, async DB
    pools, anyio task groups). Sync ``Agentao.chat`` callers leave it
    ``None``.
    """

    __slots__ = (
        "_event",
        "_reason",
        "_callbacks",
        "_cb_lock",
        "runtime_loop",
    )

    def __init__(self, runtime_loop=None) -> None:
        self._event = threading.Event()
        self._reason = ""
        # Callbacks fire synchronously on the thread that calls ``cancel()``.
        # Used by the AsyncTool dispatcher to invoke ``fut.cancel()`` on
        # the ``run_coroutine_threadsafe`` future without polling.
        self._callbacks: List[Callable[[], None]] = []
        self._cb_lock = threading.Lock()
        # Host event loop captured by ``Agentao.arun()``; None for sync
        # ``chat()`` callers. Read by the AsyncTool dispatcher.
        self.runtime_loop = runtime_loop

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self, reason: str = "user-cancel") -> None:
        """Cancel this token. Idempotent — first call wins.

        Registered callbacks fire synchronously on the calling thread,
        outside the internal lock so they can't deadlock against
        ``add_done_callback``. Callback exceptions are caught and logged
        — one misbehaving callback can never block another.
        """
        with self._cb_lock:
            if self._event.is_set():
                return
            self._reason = reason
            self._event.set()
            # Snapshot under the lock; invoke outside.
            callbacks = list(self._callbacks)
            self._callbacks.clear()

        for cb in callbacks:
            try:
                cb()
            except Exception:
                _logger.exception("CancellationToken callback raised")

    def check(self) -> None:
        """Raise AgentCancelledError if this token has been cancelled."""
        if self._event.is_set():
            raise AgentCancelledError(self._reason)

    @property
    def reason(self) -> str:
        return self._reason

    def add_done_callback(
        self, fn: Callable[[], None]
    ) -> Callable[[], None]:
        """Register ``fn`` to run synchronously when ``cancel()`` is called.

        If the token is already cancelled, ``fn`` runs immediately on the
        calling thread before this method returns.

        Returns an unregister callable so callers can detach the callback
        once their critical section ends. Idempotent — calling the
        unregister callable twice is a no-op.
        """
        with self._cb_lock:
            if self._event.is_set():
                already_cancelled = True
            else:
                already_cancelled = False
                self._callbacks.append(fn)

        if already_cancelled:
            try:
                fn()
            except Exception:
                _logger.exception("CancellationToken callback raised")

            def _noop_unregister() -> None:
                return None

            return _noop_unregister

        unregistered = False

        def _unregister() -> None:
            nonlocal unregistered
            if unregistered:
                return
            unregistered = True
            with self._cb_lock:
                try:
                    self._callbacks.remove(fn)
                except ValueError:
                    # Already fired (cancel happened between add and unregister)
                    # or never present — both fine.
                    pass

        return _unregister
