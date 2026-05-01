"""Public event stream primitive for the embedded harness contract.

The :class:`EventStream` is the runtime side of ``Agentao.events()``.
Producers (PR 5/6/7 emit sites) call :meth:`EventStream.publish` with a
fully-validated public event model; consumers iterate via
``async for`` on :meth:`EventStream.subscribe`.

Delivery semantics (matches ``docs/api/harness.md``):

- No subscriber: events are dropped immediately, the agent loop never
  blocks.
- Subscriber starts after events were emitted: no replay; only future
  events are delivered.
- Bounded queue: when full, the producer awaits capacity for matching
  events (host-pulled backpressure).
- Cancellation of the iterator releases queue resources; future events
  follow the "no subscriber" rule.
- Same-session ordering is guaranteed; cross-session global ordering is
  not.
- MVP supports one subscriber per session-filter; a second concurrent
  subscriber for the same filter raises :class:`StreamSubscribeError`.

The producer side is sync (``publish``); emit sites in PR 5/6/7 may run
on the LLM worker thread, an executor pool, or the host loop, so we use
:func:`asyncio.run_coroutine_threadsafe` internally to land the event on
the runtime event loop. The runtime loop must be set via
:meth:`EventStream.bind_loop` before the first ``publish`` call when a
subscriber is active; absent a loop binding (no consumer attached yet)
``publish`` is a no-op drop.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from typing import Any, AsyncIterator, Dict, List, Optional, Union

_logger = logging.getLogger(__name__)

from .models import (
    HarnessEvent,
    PermissionDecisionEvent,
    SubagentLifecycleEvent,
    ToolLifecycleEvent,
)

# A discriminated-union value the stream knows how to route. Using a
# bare ``Union`` here (instead of the ``Annotated`` alias from
# :mod:`models`) lets mypy/IDEs narrow without importing ``Annotated``
# wrappers internally.
_PublishedEvent = Union[
    ToolLifecycleEvent,
    SubagentLifecycleEvent,
    PermissionDecisionEvent,
]


# Default queue capacity per subscriber. Bounded so a slow consumer
# applies backpressure rather than letting the runtime grow an
# unbounded queue. Value is small enough to surface real problems
# during testing but large enough that bursty tool batches do not
# stall a typical UI consumer.
DEFAULT_SUBSCRIBER_QUEUE_SIZE = 64


class StreamSubscribeError(RuntimeError):
    """Raised when a second concurrent subscriber attaches to the same filter."""


class _Subscriber:
    """Per-iterator state. ``filter_session_id=None`` means all sessions.

    ``pending_puts`` tracks producer threads currently blocked inside
    :meth:`EventStream.publish` on a ``run_coroutine_threadsafe`` future
    targeting this subscriber's queue. The stream lock guards both the
    list and the ``closed`` flag so cleanup can cancel every still-pending
    put — without that, a subscriber cancelled while its queue is full
    leaves the producer wedged on ``fut.result()`` forever (the queue
    has no consumer, so the await never completes).
    """

    __slots__ = ("queue", "filter_session_id", "closed", "pending_puts")

    def __init__(
        self,
        queue: "asyncio.Queue[_PublishedEvent]",
        filter_session_id: Optional[str],
    ) -> None:
        self.queue = queue
        self.filter_session_id = filter_session_id
        self.closed = False
        # Pending put handles. Off-loop publishers store the
        # ``concurrent.futures.Future`` returned by
        # ``run_coroutine_threadsafe``; on-loop publishers store the
        # ``asyncio.Task`` produced by ``loop.create_task``. Both
        # expose a ``cancel()`` method, which is the only operation
        # cleanup needs to release blocked / queued work.
        self.pending_puts: List[Any] = []

    def matches(self, event: _PublishedEvent) -> bool:
        if self.filter_session_id is None:
            return True
        return getattr(event, "session_id", None) == self.filter_session_id


class EventStream:
    """Runtime-owned bridge from sync emit sites to async consumers.

    Bind the runtime event loop with :meth:`bind_loop` once the runtime
    knows which loop owns its long-lived async resources. Producers
    call :meth:`publish` from any thread; the bridge schedules delivery
    on the bound loop.
    """

    def __init__(
        self,
        *,
        max_queue_size: int = DEFAULT_SUBSCRIBER_QUEUE_SIZE,
    ) -> None:
        self._max_queue_size = max_queue_size
        self._lock = threading.Lock()
        self._subscribers: List[_Subscriber] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # Producer side
    # ------------------------------------------------------------------

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the runtime event loop. Idempotent; second binding is rejected."""
        with self._lock:
            if self._loop is not None and self._loop is not loop:
                raise RuntimeError(
                    "EventStream already bound to a different event loop; "
                    "construct a new stream rather than rebinding."
                )
            self._loop = loop

    def publish(self, event: _PublishedEvent) -> None:
        """Drop the event if no subscriber is attached; otherwise enqueue.

        Backpressure is host-pulled. If a subscriber's queue is full,
        the producer thread blocks on :meth:`asyncio.Queue.put` via the
        runtime loop until capacity is available. Sync callers that
        cannot block must either keep their queue drained or run on a
        thread that owns this responsibility.

        When ``publish`` is invoked from a coroutine or callback already
        running on the bound loop, the off-loop blocking strategy would
        deadlock (``fut.result()`` waits for the very loop that is
        meant to run the put). In that case the put is scheduled on
        the loop as an :class:`asyncio.Task`. The task awaits capacity
        in submission order, so a slow consumer applies cooperative
        backpressure once the producer coroutine yields — events are
        still queued (not dropped) for live subscribers up to a hard
        cap on outstanding puts. Once outstanding pending puts reach
        ``max_queue_size``, further on-loop events are dropped (with a
        warning) to keep total in-flight bounded at 2x the queue size;
        a tighter bound is impossible without making ``publish`` async
        for on-loop callers. Subscriber cleanup cancels every
        still-pending task, so a cancelled iterator does not leave
        events stranded on the loop.
        """
        # Fast-path: list-truthiness is a single GIL-protected read on
        # CPython, so we can skip the lock+copy in the common case
        # (no subscriber attached). Worst case a subscriber attaches
        # between this check and the lock acquire — they get the next
        # event, which matches the "no replay" contract.
        if not self._subscribers:
            return
        with self._lock:
            loop = self._loop
            targets = [s for s in self._subscribers if s.matches(event) and not s.closed]
        if not targets or loop is None:
            return
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        same_loop = running_loop is loop
        for sub in targets:
            if same_loop:
                # Hot path: try synchronous ``put_nowait`` first. When
                # the queue has space (the common case for a healthy
                # consumer) the event is delivered without scheduling
                # a task, so on-loop publish is as cheap as off-loop
                # ``run_coroutine_threadsafe`` — and we never drop.
                try:
                    sub.queue.put_nowait(event)
                    continue
                except asyncio.QueueFull:
                    pass
                # Queue is genuinely full. Schedule the put as a loop
                # task that awaits capacity in submission order. Cap
                # the outstanding-task list at ``max_queue_size`` so
                # an on-loop producer that never yields cannot grow
                # ``pending_puts`` without bound — total in-flight
                # therefore stays at most 2x the queue size. Drop
                # past the cap with a warning; tighter bounds are
                # impossible without making the sync ``publish`` API
                # asynchronous for on-loop callers.
                with self._lock:
                    if sub.closed:
                        continue
                    if len(sub.pending_puts) >= self._max_queue_size:
                        _logger.warning(
                            "EventStream: dropping event for session %r — "
                            "on-loop publisher exceeded backpressure cap "
                            "(%d pending puts on a full queue).",
                            getattr(event, "session_id", None),
                            self._max_queue_size,
                        )
                        continue
                try:
                    task = loop.create_task(sub.queue.put(event))
                except RuntimeError:
                    # Loop closed between target collection and now —
                    # treat as "no subscriber" and drop quietly.
                    continue
                with self._lock:
                    if sub.closed:
                        task.cancel()
                    else:
                        sub.pending_puts.append(task)

                def _on_done(t: "asyncio.Task[Any]", _sub=sub) -> None:
                    with self._lock:
                        try:
                            _sub.pending_puts.remove(t)
                        except ValueError:
                            pass

                task.add_done_callback(_on_done)
                continue
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    sub.queue.put(event), loop
                )
            except RuntimeError:
                # Loop closed between the lock release and the schedule;
                # treat as "no subscriber" and drop quietly.
                continue
            # Register the put so subscriber cleanup can cancel it.
            # Recheck ``closed`` under the lock — the iterator may have
            # exited between target collection and now, in which case we
            # cancel immediately so the producer is not left waiting on
            # an unconsumed queue.
            with self._lock:
                if sub.closed:
                    fut.cancel()
                else:
                    sub.pending_puts.append(fut)
            # Block the producer to apply backpressure. A subscriber
            # cancelling mid-put surfaces ``CancelledError``; the
            # iterator's ``aclose`` path is the source of truth for
            # cleanup, so swallowing here is correct.
            try:
                fut.result()
            except (asyncio.CancelledError, concurrent.futures.CancelledError):
                pass
            finally:
                with self._lock:
                    try:
                        sub.pending_puts.remove(fut)
                    except ValueError:
                        pass

    # ------------------------------------------------------------------
    # Consumer side
    # ------------------------------------------------------------------

    async def subscribe(
        self,
        session_id: Optional[str] = None,
    ) -> AsyncIterator[_PublishedEvent]:
        """Async iterator yielding future events for the given filter.

        ``session_id=None`` subscribes to events from every session
        owned by the parent runtime; passing a string narrows to one
        session.

        MVP rejects a second concurrent subscriber for the same filter:
        the public surface promises a single consumer. Hosts that need
        per-session iterators should subscribe to all sessions and
        filter client-side.
        """
        loop = asyncio.get_running_loop()
        # Bind on first use so the consumer can attach before any
        # producer thread is ready. ``bind_loop`` is a no-op when the
        # loop matches the previous binding.
        self.bind_loop(loop)
        queue: asyncio.Queue[_PublishedEvent] = asyncio.Queue(
            maxsize=self._max_queue_size,
        )
        sub = _Subscriber(queue, filter_session_id=session_id)
        with self._lock:
            for existing in self._subscribers:
                if existing.filter_session_id == session_id and not existing.closed:
                    raise StreamSubscribeError(
                        "EventStream already has an active subscriber for "
                        f"filter session_id={session_id!r}; MVP supports "
                        "one consumer per filter."
                    )
            self._subscribers.append(sub)
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            with self._lock:
                # Mark ``closed`` *under the lock* so a publisher racing
                # this teardown cannot append a fresh future after we
                # cancel the pending list; the publisher's same-lock
                # recheck of ``closed`` will then cancel its own future.
                sub.closed = True
                try:
                    self._subscribers.remove(sub)
                except ValueError:
                    pass
                # Cancel any producer-thread futures still awaiting
                # capacity on this subscriber's queue. With no consumer
                # left to drain the queue the await would never resolve;
                # cancellation surfaces ``CancelledError`` from the
                # producer's ``fut.result()``, which is already swallowed
                # by ``publish``.
                for pending in sub.pending_puts:
                    pending.cancel()
                sub.pending_puts.clear()
                # Release the loop binding once no subscribers remain,
                # so a later subscription from a different loop (e.g.
                # a fresh ``asyncio.run`` invocation in tests or the
                # host) can rebind without raising.
                if not self._subscribers:
                    self._loop = None

    # ------------------------------------------------------------------
    # Introspection (test hooks; not part of the public contract)
    # ------------------------------------------------------------------

    def _has_subscribers(self) -> bool:
        with self._lock:
            return any(not s.closed for s in self._subscribers)


__all__ = [
    "DEFAULT_SUBSCRIBER_QUEUE_SIZE",
    "EventStream",
    "StreamSubscribeError",
]
