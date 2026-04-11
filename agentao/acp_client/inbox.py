"""Async message inbox for ACP client notifications.

Messages from ACP servers arrive asynchronously on background reader threads.
The :class:`Inbox` queues them in FIFO order with a bounded capacity so the
CLI can drain and render them at safe idle points — never during user input.

Thread safety: all public methods are safe to call from any thread.
"""

from __future__ import annotations

import time
import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

# Default maximum messages before back-pressure drops the oldest.
DEFAULT_CAPACITY = 256


class MessageKind(str, Enum):
    """Classification of an inbox message."""

    RESPONSE = "response"           # Normal agent response text.
    NOTIFICATION = "notification"   # Generic server notification.
    PERMISSION = "permission"       # Server requesting user permission.
    INPUT = "input"                 # Server requesting free-text input.
    ERROR = "error"                 # Error / failure from server.


@dataclass(frozen=True)
class InboxMessage:
    """A single queued message from an ACP server.

    Immutable after creation so the reader thread and the CLI thread can
    safely share references without copying.
    """

    server: str
    session_id: str
    kind: MessageKind
    text: str
    timestamp: float = field(default_factory=time.time)
    raw: Optional[Dict[str, Any]] = None
    # The ``sessionUpdate`` type from the ACP ``session/update`` notification
    # (e.g. ``tool_call``, ``agent_message_chunk``).  Used by the render layer
    # to decide what to display vs log-only.
    update_kind: str = ""

    @property
    def is_interaction(self) -> bool:
        """Whether this message represents a pending user interaction."""
        return self.kind in (MessageKind.PERMISSION, MessageKind.INPUT)


class Inbox:
    """Bounded FIFO queue of :class:`InboxMessage` items.

    The writer side (notification callbacks on reader threads) calls
    :meth:`push`.  The consumer side (CLI idle flush) calls :meth:`drain`
    to atomically retrieve and remove all queued messages.

    When the queue reaches ``capacity``, the oldest message is silently
    dropped to keep memory bounded.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._capacity = capacity
        self._queue: deque[InboxMessage] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._dropped = 0

    # ------------------------------------------------------------------
    # Writer side
    # ------------------------------------------------------------------

    def push(self, msg: InboxMessage) -> None:
        """Enqueue a message.  Drops the oldest if at capacity."""
        with self._lock:
            if len(self._queue) == self._capacity:
                self._dropped += 1
            self._queue.append(msg)

    # ------------------------------------------------------------------
    # Consumer side
    # ------------------------------------------------------------------

    def drain(self) -> List[InboxMessage]:
        """Atomically remove and return all queued messages in FIFO order."""
        with self._lock:
            items = list(self._queue)
            self._queue.clear()
            return items

    def peek(self) -> List[InboxMessage]:
        """Return a snapshot of queued messages without consuming them."""
        with self._lock:
            return list(self._queue)

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._queue)

    @property
    def pending_interactions(self) -> List[InboxMessage]:
        """Return pending permission / input requests without consuming."""
        with self._lock:
            return [m for m in self._queue if m.is_interaction]

    @property
    def dropped_count(self) -> int:
        """Number of messages silently dropped due to capacity."""
        return self._dropped

    @property
    def is_empty(self) -> bool:
        with self._lock:
            return len(self._queue) == 0
