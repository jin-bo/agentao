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
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, List, Optional

# Default maximum messages before back-pressure drops the oldest.
DEFAULT_CAPACITY = 256

# A server may stream its reasoning one ``agent_thought_chunk`` per token.
# Adjacent pieces from one session merge into one message up to this size,
# and at capacity a thought is dropped before anything else, so a long
# thinking phase cannot push the reply, an error or a pending interaction out.
# Thoughts are log-only (``render.py`` logs the first 200 chars), so a small
# cap loses nothing shown and keeps each merge's copy short.
_THOUGHT = "agent_thought_chunk"
THOUGHT_MERGE_MAX_CHARS = 8_000


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
    dropped to keep memory bounded: the oldest thought if there is one, else
    the oldest message. An incoming thought never evicts anything else: with
    no thought queued, it is the one dropped.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._capacity = capacity
        self._queue: deque[InboxMessage] = deque()
        self._lock = threading.Lock()
        self._dropped = 0

    # ------------------------------------------------------------------
    # Writer side
    # ------------------------------------------------------------------

    def push(self, msg: InboxMessage) -> None:
        """Enqueue a message.  Drops the oldest if at capacity.

        A thought that follows a thought from the same session is appended
        to it (``raw`` stays the first piece's).
        """
        with self._lock:
            last = self._queue[-1] if self._queue else None
            if (
                msg.update_kind == _THOUGHT
                and last is not None
                and last.update_kind == _THOUGHT
                and (last.server, last.session_id) == (msg.server, msg.session_id)
                and len(last.text) + len(msg.text) <= THOUGHT_MERGE_MAX_CHARS
            ):
                self._queue[-1] = replace(last, text=last.text + msg.text)
                return
            if len(self._queue) >= self._capacity:
                self._dropped += 1
                for i, queued in enumerate(self._queue):
                    if queued.update_kind == _THOUGHT:
                        del self._queue[i]
                        break
                else:
                    if msg.update_kind == _THOUGHT:
                        return
                    self._queue.popleft()
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
