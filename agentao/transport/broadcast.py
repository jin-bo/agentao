"""Tiny subscriber-broadcast helper for Transport implementations.

Concrete transports compose an :class:`EventBroadcaster` to gain a
``subscribe(listener)`` method without re-implementing dispatch. Each
transport's ``emit`` calls its primary side-effect first, then
``self._broadcast.notify(event)`` so subscribers see the same event
the inner transport just consumed.

Listener errors are swallowed — subscription is a side channel and
must never break the primary emit path.
"""

from __future__ import annotations

from typing import Callable, List

from .events import AgentEvent

EventListener = Callable[[AgentEvent], None]


class EventBroadcaster:
    """Maintain a list of listeners and notify them in registration order."""

    def __init__(self) -> None:
        self._listeners: List[EventListener] = []

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        """Register ``listener`` and return an idempotent unsubscribe callable."""
        self._listeners.append(listener)

        def _unsubscribe() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return _unsubscribe

    def notify(self, event: AgentEvent) -> None:
        """Call every listener in order; swallow any exceptions they raise.

        Iterating over a snapshot keeps mid-notify subscribe/unsubscribe
        from skipping or double-firing listeners — the new listener (or
        the absent unsubscribed one) takes effect on the *next* event.
        """
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:
                pass

    def listener_count(self) -> int:
        return len(self._listeners)
