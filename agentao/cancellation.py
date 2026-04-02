"""Cancellation token — Python equivalent of AbortSignal/AbortController."""

import threading
from typing import Optional


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
    """

    __slots__ = ("_event", "_reason")

    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason = ""

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self, reason: str = "user-cancel") -> None:
        """Cancel this token. Idempotent — first call wins."""
        if not self._event.is_set():
            self._reason = reason
            self._event.set()

    def check(self) -> None:
        """Raise AgentCancelledError if this token has been cancelled."""
        if self._event.is_set():
            raise AgentCancelledError(self._reason)

    @property
    def reason(self) -> str:
        return self._reason
