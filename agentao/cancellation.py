"""Cancellation token — Python equivalent of AbortSignal/AbortController."""

from __future__ import annotations

import contextvars
import logging
import threading
from contextlib import contextmanager
from typing import Callable, Iterator, List, Optional


_logger = logging.getLogger(__name__)


class AgentCancelledError(Exception):
    """Raised when a CancellationToken has been cancelled."""

    def __init__(self, reason: str = "user-cancel", *, partial_output: str = ""):
        self.reason = reason
        # What a blocking call had produced when the cancel reached it (a shell
        # command's output so far); the tool executor puts it in the result.
        self.partial_output = partial_output
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


def cancelled_result_header(reason: str) -> str:
    """The first line of a tool result the turn's cancel reached."""
    return f"[Operation Cancelled] {reason}"


# The token of the turn whose tool call is running on this thread. Set by the
# tool executor around a sync ``Tool.execute`` and read by the code that blocks
# inside it (the shell's wait loop, ``McpClientManager``), so a cancelled turn
# reaches a running command or MCP call instead of waiting for it to finish.
# A context variable rather than an attribute on the tool: MCP tool instances
# are shared between a parent and its sub-agents, whose calls run concurrently.
_CURRENT_TOKEN: "contextvars.ContextVar[Optional[CancellationToken]]" = (
    contextvars.ContextVar("agentao_cancellation_token", default=None)
)


def current_cancellation_token() -> Optional[CancellationToken]:
    """The token bound by :func:`bind_cancellation_token` on this thread, if any."""
    return _CURRENT_TOKEN.get()


@contextmanager
def bind_cancellation_token(token: Optional[CancellationToken]) -> Iterator[None]:
    """Make ``token`` the :func:`current_cancellation_token` for the block."""
    reset = _CURRENT_TOKEN.set(token)
    try:
        yield
    finally:
        _CURRENT_TOKEN.reset(reset)
