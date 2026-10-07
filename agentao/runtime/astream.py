"""``Agentao.astream()`` — one turn's text deltas, then its ``TurnOutcome``.

This sits above the runtime: it is ``arun()`` plus a subscription on the
agent's live transport. The chat loop does not know it exists. Design and
the reasons for each rule below: ``docs/design/host-api-ergonomics-review.md``
F2.

The rules, in short:

- **Attach by subscribing, never by replacing the transport.** A transport
  with no ``subscribe`` is refused before the turn starts. ``ReplayAdapter``
  always has ``subscribe``, but over a subscribe-less inner transport it
  returns a no-op, so the check unwraps it.
- **Bound to its own turn by token identity.** The stream mints its own
  token and forwards an event only when the emitting thread's current turn
  (``cancellation.current_turn()``) is this agent with that token. The agent
  half matters because a host may pass one token to a nested turn of another
  agent on the same transport. ``run_turn`` binds
  it after taking the turn lock, for the whole turn, and listeners run inline
  on the emitting thread. So a request refused with ``TurnInProgressError``
  never sees another turn's text, and neither does a stream whose transport
  another agent shares. A caller's token is linked to the stream's own, never
  used as it: two calls sharing one caller token would otherwise both match.
- **The outcome is captured on ``TURN_END``.** ``run_turn`` sets
  ``agent._last_turn_outcome`` and emits ``TURN_END`` while the token is
  still bound and the lock still held, so the listener reads this
  turn's outcome. Reading ``agent.last_turn`` after ``arun()`` returned
  could see a later turn's.
- **Bounded queue, producer waits.** Same capacity and full-queue rule as
  ``Agentao.events()``: a consumer that stops reading slows the turn. Once
  the turn's token is cancelled (a linked caller token included), a write
  from the turn's thread no longer waits: it is dropped, so a cancel ends
  the turn even when nobody is reading.
- **Closing early** (``aclose()``, task cancellation) runs in this order:
  mark closed and cancel pending queue writes; cancel the ``arun`` task,
  which trips the token and waits for the turn's cleanup, bounded; then
  unsubscribe. A producer blocked on a full queue sits in a queue write,
  not at a token check, so releasing the writes has to come first.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable, Dict, List, Optional, Union

from ..cancellation import CancellationToken, current_turn
from ..host.events import DEFAULT_SUBSCRIBER_QUEUE_SIZE
from ..host.stream import TextDelta
from ..outcome import TurnOutcome
from ..transport.events import AgentEvent, EventType

if TYPE_CHECKING:
    from ..agent import Agentao

StreamItem = Union[TextDelta, TurnOutcome]
Subscribe = Callable[[Callable[[AgentEvent], None]], Callable[[], None]]


def resolve_subscribe(transport: Any) -> Subscribe:
    """Return the ``subscribe`` that really delivers ``transport``'s events.

    Raises ``TypeError`` naming the transport class when there is none.
    """
    from ..replay.adapter import ReplayAdapter
    from ..transport.base import Transport

    target = transport
    while isinstance(target, ReplayAdapter):
        target = target.inner
    subscribe = getattr(target, "subscribe", None)
    # A class that subclasses the ``Transport`` Protocol inherits its stub
    # ``subscribe`` (body ``...``): callable, registers nothing, returns None.
    inherited_stub = getattr(type(target), "subscribe", None) is Transport.subscribe
    if not callable(subscribe) or inherited_stub:
        raise TypeError(
            "astream() needs a transport that supports subscribe(); "
            f"{type(target).__name__} does not. NullTransport and "
            "SdkTransport both do."
        )
    return subscribe  # type: ignore[no-any-return]


async def stream_turn(
    agent: "Agentao",
    user_message: str,
    subscribe: Subscribe,
    *,
    max_iterations: int = 100,
    images: Optional[List[Dict[str, str]]] = None,
    cancellation_token: Optional[CancellationToken] = None,
) -> AsyncGenerator[StreamItem, None]:
    loop = asyncio.get_running_loop()
    token = CancellationToken()
    queue: asyncio.Queue[TextDelta] = asyncio.Queue(maxsize=DEFAULT_SUBSCRIBER_QUEUE_SIZE)
    lock = threading.Lock()
    closed = False
    # Writes in flight: a producer thread's future, or a task scheduled by an
    # emit on the loop's own thread. ``_close`` cancels both kinds.
    pending_puts: List[Union[concurrent.futures.Future[None], asyncio.Task[None]]] = []
    outcomes: List[TurnOutcome] = []

    def _listener(event: AgentEvent) -> None:
        # Runs inline on the emitting thread, inside ``emit``, so the turn
        # token bound in that thread's context names the emitting turn.
        # ``agent._current_token`` would not do: a transport shared by two
        # agents delivers the other agent's events while this agent's token
        # is installed.
        turn = current_turn()
        if turn is None or turn[0] is not agent or turn[1] is not token:
            return
        if event.type is EventType.TURN_END:
            outcome = agent._last_turn_outcome
            if outcome is not None:
                outcomes.append(outcome)
            return
        if event.type is not EventType.LLM_TEXT:
            return
        chunk = (event.data or {}).get("chunk")
        if not isinstance(chunk, str) or not chunk:
            return
        delta = TextDelta(chunk)
        if _on_loop_thread():
            # Emitted on the host loop itself, e.g. by a host async tool's
            # coroutine (it inherits the turn's context). Blocking here would
            # wait for a put only this loop can run, so schedule it instead,
            # as ``EventStream.publish`` does.
            with lock:
                if closed:
                    return
                put = loop.create_task(_put_after(scheduled_tail[0], delta))
                scheduled_tail[0] = put
                pending_puts.append(put)
            put.add_done_callback(_forget)
            return
        with lock:
            if closed:
                return
            put_coro = _put_after(scheduled_tail[0], delta)
            try:
                fut = asyncio.run_coroutine_threadsafe(put_coro, loop)
            except RuntimeError:
                # The host's loop is closed: nobody is reading. Close the
                # coroutine, or it is reported as never awaited.
                put_coro.close()
                return
            pending_puts.append(fut)
        # A cancelled turn must not wait for room in the queue: the producer
        # is parked here, not at a token check, so a caller's
        # ``cancellation_token`` (linked to ``token``) could not end a turn
        # whose consumer has stopped reading. The write is dropped instead;
        # the turn's text after a cancel is display-only and incomplete anyway.
        unlink_cancel = token.add_done_callback(fut.cancel)
        try:
            fut.result()
        except (asyncio.CancelledError, concurrent.futures.CancelledError):
            pass
        finally:
            unlink_cancel()
            with lock:
                try:
                    pending_puts.remove(fut)
                except ValueError:
                    pass

    # The last write scheduled on the loop (see ``_listener``). Every write
    # waits for it first: ``asyncio.Queue`` does not serve waiting writers
    # strictly in order once it is full, and the deltas must keep the order
    # the model produced them in.
    scheduled_tail: List[Optional[asyncio.Task[None]]] = [None]

    async def _put_after(prev: Optional[asyncio.Task[None]], delta: TextDelta) -> None:
        if prev is not None and not prev.done():
            await asyncio.wait({prev})
        await queue.put(delta)

    def _on_loop_thread() -> bool:
        try:
            return asyncio.get_running_loop() is loop
        except RuntimeError:
            return False

    def _forget(put: "asyncio.Future[None]") -> None:
        with lock:
            try:
                pending_puts.remove(put)
            except ValueError:
                pass

    def _close() -> None:
        nonlocal closed
        with lock:
            closed = True
            for fut in pending_puts:
                fut.cancel()
            pending_puts.clear()

    unsubscribe = subscribe(_listener)
    if not callable(unsubscribe):
        # Fail closed: a subscribe that returns no unsubscribe did not
        # register anything we could rely on, and could not be undone.
        raise TypeError(
            f"astream(): {subscribe!r} returned {unsubscribe!r}, not an "
            "unsubscribe callable"
        )
    unlink: Optional[Callable[[], None]] = None
    task: Optional[asyncio.Task[str]] = None
    try:
        if cancellation_token is not None:
            caller = cancellation_token
            unlink = caller.add_done_callback(lambda: token.cancel(caller.reason))
        task = loop.create_task(
            agent.arun(
                user_message,
                max_iterations=max_iterations,
                cancellation_token=token,
                images=images,
            )
        )
        while True:
            get = loop.create_task(queue.get())
            try:
                await asyncio.wait({get, task}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                if not get.done():
                    get.cancel()
            if get.done() and not get.cancelled():
                yield get.result()
                continue
            if task.done():
                break
        # A producer thread waits on each write, so those all finished before
        # the turn returned. Writes scheduled on the loop (see ``_listener``)
        # may still be waiting for room in the queue, so drain until they
        # have all landed.
        while True:
            while not queue.empty():
                yield queue.get_nowait()
            with lock:
                scheduled = [p for p in pending_puts if isinstance(p, asyncio.Task)]
            if not scheduled:
                break
            # One at a time: more scheduled writes than the queue holds would
            # otherwise wait for room nobody is making.
            await asyncio.wait(scheduled, return_when=asyncio.FIRST_COMPLETED)
        task.result()  # the turn's exception, if any, after its text
        if not outcomes:
            raise RuntimeError(
                "astream(): the turn ended without its TURN_END reaching the "
                "stream. Either the agent's transport was replaced during the "
                "turn, its emit() does not notify subscribers (a subclass "
                "that overrides emit() must call super().emit()), or "
                "Agentao.chat was replaced, so the turn never ran through "
                "run_turn."
            )
        yield outcomes[-1]
    finally:
        _close()
        try:
            if task is not None and not task.done():
                # ``arun`` forwards the cancel to the token and waits, bounded,
                # for the worker to finish the turn.
                current = asyncio.current_task()
                cancelling = getattr(current, "cancelling", None)
                cancels_before = cancelling() if callable(cancelling) else 0
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    # Expected: this block cancelled ``task``. A cancel of the
                    # consumer's own task during the wait is not ours to
                    # swallow (Python 3.11+ counts it; 3.10 cannot tell).
                    if callable(cancelling) and cancelling() > cancels_before:
                        raise
                except Exception:
                    # The turn's result belongs to nobody now; the cancel that
                    # closed the stream, if any, is re-raised after this block.
                    pass
            elif task is not None and not task.cancelled():
                # Closed after the turn had already ended (while its last
                # deltas were being drained): mark an exception it raised as
                # retrieved, or asyncio logs "Task exception was never
                # retrieved" when the task is collected.
                task.exception()
        finally:
            if unlink is not None:
                unlink()
            unsubscribe()
