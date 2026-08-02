"""Tiny subscriber-broadcast helper for Transport implementations.

Concrete transports compose an :class:`EventBroadcaster` to gain a
``subscribe(listener)`` method without re-implementing dispatch. Each
transport's ``emit`` calls its primary side-effect first, then
``self._broadcast.notify(event)`` so subscribers see the same event
the inner transport just consumed.

Listener errors are swallowed — subscription is a side channel and
must never break the primary emit path — but they are logged at
WARNING, not dropped silently. See :meth:`EventBroadcaster.notify`.
"""

import logging
from typing import Callable, List

from .events import AgentEvent

_logger = logging.getLogger(__name__)

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
        """Call every listener in order; log-and-swallow anything they raise.

        Swallowing keeps a broken subscriber from breaking the emit path.
        Logging is what makes the swallow debuggable: the symptom a host
        sees is "my listener does nothing", and without this record there
        is no trace of the cause anywhere. WARNING-and-swallow is the
        convention the host contract already documents for the other
        side-channel sink, ``HostReplaySink`` (docs/reference/host-api.md).

        Logged rather than re-emitted as an error *event*: an event would
        re-enter this same loop, so a listener that raises on everything
        would spin forever. (pi-mono's ``handler_error`` event carries an
        explicit recursion guard for exactly that reason; a log call has
        no such edge.)

        Only ``event.type`` is recorded, never ``event.data``. Payloads
        carry tool arguments, full tool results and LLM text — large, and
        the credential redaction lives on agentao's *own* file handler, so
        an embedded host's handlers would receive the payload unredacted.
        """
        if not self._listeners:
            return
        # Snapshot iteration keeps mid-notify subscribe/unsubscribe from
        # skipping or double-firing listeners — the change takes effect
        # on the *next* event.
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:
                _logger.warning(
                    "event listener %r raised while handling %s; "
                    "delivery continues to the remaining listeners",
                    listener,
                    event.type.value,
                    exc_info=True,
                )

    def listener_count(self) -> int:
        return len(self._listeners)
