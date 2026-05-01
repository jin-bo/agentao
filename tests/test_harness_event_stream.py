"""Delivery-contract tests for the harness EventStream (PR 4).

Asserts the lifecycle matrix from
``docs/implementation/EMBEDDED_HARNESS_CONTRACT_IMPLEMENTATION_PLAN.md``:

- no subscriber → events are dropped immediately
- mid-turn subscriber → only future events
- bounded queue → backpressure for matching events
- iterator cancellation → resources released
- one subscriber per filter → second concurrent subscriber rejected
- same-session ordering preserved
- runtime identity helpers expose stable contracts

The repo doesn't use ``pytest-asyncio``; tests drive their async cases
via ``asyncio.run(...)`` to match the existing pattern in
``tests/test_async_chat.py``.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from agentao.harness.events import (
    DEFAULT_SUBSCRIBER_QUEUE_SIZE,
    EventStream,
    StreamSubscribeError,
)
from agentao.harness.models import ToolLifecycleEvent
from agentao.runtime import identity as runtime_identity


def _ev(session_id: str, *, tool_call_id: str = "tc", phase: str = "started"):
    return ToolLifecycleEvent(
        session_id=session_id,
        tool_call_id=tool_call_id,
        tool_name="run_shell_command",
        phase=phase,  # type: ignore[arg-type]
        started_at=runtime_identity.utc_now_rfc3339(),
    )


# ---------------------------------------------------------------------------
# No-subscriber drop
# ---------------------------------------------------------------------------


def test_publish_without_subscriber_does_not_block():
    """Empty subscriber list short-circuits before touching the loop."""
    stream = EventStream()
    t0 = time.monotonic()
    for _ in range(100):
        stream.publish(_ev("s-1"))
    assert time.monotonic() - t0 < 0.1


# ---------------------------------------------------------------------------
# Mid-turn subscription only sees future events
# ---------------------------------------------------------------------------


def test_mid_turn_subscriber_sees_only_future_events():
    stream = EventStream()
    stream.publish(_ev("s-1", tool_call_id="dropped-before-subscribe"))

    async def runner():
        received: list[ToolLifecycleEvent] = []

        async def consume():
            async for event in stream.subscribe(session_id="s-1"):
                received.append(event)
                if len(received) == 1:
                    return

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0)

        loop = asyncio.get_running_loop()
        # Producer runs on a worker thread; the runtime treats publish
        # as a sync call from the LLM/executor pool.
        await loop.run_in_executor(
            None, lambda: stream.publish(_ev("s-1", tool_call_id="future-1"))
        )
        await asyncio.wait_for(consumer, timeout=2.0)
        return [e.tool_call_id for e in received]

    received = asyncio.run(runner())
    assert received == ["future-1"]


# ---------------------------------------------------------------------------
# Same-session ordering
# ---------------------------------------------------------------------------


def test_same_session_ordering_preserved():
    stream = EventStream()

    async def runner():
        received: list[str] = []

        async def consume():
            async for event in stream.subscribe(session_id="s-1"):
                received.append(event.tool_call_id)
                if len(received) == 5:
                    return

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0)
        loop = asyncio.get_running_loop()

        def producer():
            for i in range(5):
                stream.publish(_ev("s-1", tool_call_id=f"call-{i}"))

        await loop.run_in_executor(None, producer)
        await asyncio.wait_for(consumer, timeout=2.0)
        return received

    received = asyncio.run(runner())
    assert received == [f"call-{i}" for i in range(5)]


# ---------------------------------------------------------------------------
# Cross-session events do not reach a filtered subscriber
# ---------------------------------------------------------------------------


def test_other_session_events_do_not_reach_filtered_subscriber():
    stream = EventStream()

    async def runner():
        received: list[str] = []

        async def consume():
            async for event in stream.subscribe(session_id="s-1"):
                received.append(event.session_id)
                if len(received) == 1:
                    return

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0)
        loop = asyncio.get_running_loop()

        def producer():
            stream.publish(_ev("s-other", tool_call_id="x"))
            stream.publish(_ev("s-1", tool_call_id="y"))

        await loop.run_in_executor(None, producer)
        await asyncio.wait_for(consumer, timeout=2.0)
        return received

    received = asyncio.run(runner())
    assert received == ["s-1"]


# ---------------------------------------------------------------------------
# Bounded backpressure
# ---------------------------------------------------------------------------


def test_full_queue_blocks_producer_until_consumer_drains():
    stream = EventStream(max_queue_size=2)

    async def runner():
        # Drive the subscribe so the subscriber installs and the loop binds.
        gen = stream.subscribe(session_id="s-1")
        # Step the generator once on a separate task so it is parked
        # awaiting the first ``queue.get`` — that registers the
        # subscriber and lets the producer thread schedule puts.
        consume_task = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0)

        publish_done = threading.Event()
        publish_started = threading.Event()

        def producer():
            publish_started.set()
            stream.publish(_ev("s-1", tool_call_id="a"))
            stream.publish(_ev("s-1", tool_call_id="b"))
            stream.publish(_ev("s-1", tool_call_id="c"))
            stream.publish(_ev("s-1", tool_call_id="d"))
            publish_done.set()

        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(None, producer)
        # Wait until the producer has actually started before we begin
        # draining; this gives backpressure a chance to engage.
        publish_started.wait(timeout=1.0)
        received: list[str] = []
        first = await asyncio.wait_for(consume_task, timeout=2.0)
        received.append(first.tool_call_id)
        for _ in range(3):
            ev = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
            received.append(ev.tool_call_id)
        await asyncio.wait_for(asyncio.wrap_future(fut), timeout=2.0)
        await gen.aclose()
        return received

    received = asyncio.run(runner())
    assert received == ["a", "b", "c", "d"]


# ---------------------------------------------------------------------------
# Iterator cancellation releases the subscriber slot
# ---------------------------------------------------------------------------


def test_cancelling_iterator_releases_subscriber():
    stream = EventStream()

    async def runner():
        async def consume_once():
            async for event in stream.subscribe(session_id="s-1"):
                return event

        consumer = asyncio.create_task(consume_once())
        await asyncio.sleep(0)
        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass
        return stream._has_subscribers()

    has_subs = asyncio.run(runner())
    assert has_subs is False
    # Publishing into the empty stream stays a no-op.
    stream.publish(_ev("s-1"))


# ---------------------------------------------------------------------------
# Single subscriber per filter
# ---------------------------------------------------------------------------


def test_second_subscriber_for_same_filter_rejected():
    stream = EventStream()

    async def runner():
        async def first():
            async for _ in stream.subscribe(session_id="s-1"):
                return

        async def second():
            gen = stream.subscribe(session_id="s-1")
            await gen.__anext__()

        first_task = asyncio.create_task(first())
        await asyncio.sleep(0)
        with pytest.raises(StreamSubscribeError):
            await second()
        first_task.cancel()
        try:
            await first_task
        except asyncio.CancelledError:
            pass

    asyncio.run(runner())


# ---------------------------------------------------------------------------
# Runtime identity contract
# ---------------------------------------------------------------------------


def test_session_id_construction_time_fallback():
    """``Agentao`` must allocate a non-empty ``_session_id`` at construction."""
    from agentao.runtime.identity import new_session_id

    a = new_session_id()
    b = new_session_id()
    assert isinstance(a, str) and a
    assert isinstance(b, str) and b
    assert a != b


def test_normalize_tool_call_id_prefers_provider_id_with_uuid_fallback():
    assert runtime_identity.normalize_tool_call_id("call_abc") == "call_abc"
    assert runtime_identity.normalize_tool_call_id("") not in ("", None)
    assert runtime_identity.normalize_tool_call_id(None) not in ("", None)
    a = runtime_identity.normalize_tool_call_id(None)
    b = runtime_identity.normalize_tool_call_id(None)
    assert a != b


def test_decision_id_unique_per_call():
    seen = {runtime_identity.new_decision_id() for _ in range(100)}
    assert len(seen) == 100


def test_utc_now_rfc3339_canonical_format():
    ts = runtime_identity.utc_now_rfc3339()
    # Pydantic timestamp regex must accept the helper's output.
    ToolLifecycleEvent(
        session_id="s",
        tool_call_id="tc",
        tool_name="t",
        phase="started",
        started_at=ts,
    )


def test_default_queue_size_is_bounded_and_documented():
    assert isinstance(DEFAULT_SUBSCRIBER_QUEUE_SIZE, int)
    assert 1 <= DEFAULT_SUBSCRIBER_QUEUE_SIZE <= 1024


# ---------------------------------------------------------------------------
# Turn-id propagation through runtime.turn.run_turn
# ---------------------------------------------------------------------------


def test_turn_id_minted_at_turn_entry_and_cleared(tmp_path, monkeypatch):
    """Verify that ``run_turn`` mints a fresh turn id and clears it on exit.

    The agent's _chat_inner is patched so we can inspect
    ``agent._current_turn_id`` mid-turn without spinning up the LLM.
    """
    from agentao.agent import Agentao

    captured: list[str | None] = []

    def fake_inner(self, msg, mi, token):
        captured.append(self._current_turn_id)
        return "ok"

    monkeypatch.setattr(Agentao, "_chat_inner", fake_inner)

    agent = Agentao(
        api_key="k", base_url="https://example", model="x",
        working_directory=tmp_path,
    )

    # Two turns; each should mint a fresh id and clear afterward.
    agent.chat("hello")
    first_turn_id = captured[-1]
    assert isinstance(first_turn_id, str) and first_turn_id
    assert agent._current_turn_id is None  # cleared in finally

    agent.chat("again")
    second_turn_id = captured[-1]
    assert isinstance(second_turn_id, str) and second_turn_id
    assert second_turn_id != first_turn_id


def test_agent_session_id_default_set_at_construction(tmp_path):
    """``Agentao`` populates ``_session_id`` before any session/replay logic runs."""
    from agentao.agent import Agentao

    agent = Agentao(
        api_key="k", base_url="https://example", model="x",
        working_directory=tmp_path,
    )
    assert isinstance(agent._session_id, str)
    assert agent._session_id  # non-empty


def test_agent_events_returns_async_iterator(tmp_path):
    from agentao.agent import Agentao

    agent = Agentao(
        api_key="k", base_url="https://example", model="x",
        working_directory=tmp_path,
    )
    iterator = agent.events(session_id=None)
    # Async generator object — has ``__aiter__`` and ``aclose``.
    assert hasattr(iterator, "__aiter__")
    assert hasattr(iterator, "aclose")
    # Closing the iterator immediately must not raise (no loop bound yet).
    asyncio.run(iterator.aclose())


# ---------------------------------------------------------------------------
# Regression: publish() must not deadlock when invoked from the bound loop
# ---------------------------------------------------------------------------


def test_publish_from_bound_loop_does_not_deadlock():
    """A coroutine running on the bound loop calling ``publish`` synchronously
    must not block the loop on ``run_coroutine_threadsafe(...).result()``."""
    stream = EventStream()

    async def runner():
        gen = stream.subscribe(session_id="s-1")
        # Park the consumer at the first ``queue.get`` so the subscriber
        # registers and the loop binds to the running loop.
        consume_task = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0)

        # Direct sync call from the loop's own thread — pre-fix this
        # path scheduled onto the same loop and blocked on .result(),
        # deadlocking the loop. ``asyncio.wait_for`` surfaces the hang
        # as a TimeoutError instead of letting the test wedge.
        stream.publish(_ev("s-1", tool_call_id="from-loop"))

        ev = await asyncio.wait_for(consume_task, timeout=1.0)
        assert ev.tool_call_id == "from-loop"
        await gen.aclose()

    asyncio.run(asyncio.wait_for(runner(), timeout=3.0))


# ---------------------------------------------------------------------------
# Regression: a fresh loop may attach after all subscribers have been cleaned up
# ---------------------------------------------------------------------------


def test_resubscribe_from_fresh_loop_after_cleanup():
    """Hosts and tests often run repeated ``asyncio.run(...)`` calls; once
    the previous run's subscriber has been torn down the stream must accept
    a new subscriber from the new loop without raising."""
    stream = EventStream()

    async def first_run():
        gen = stream.subscribe(session_id="s-1")
        consume_task = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0)
        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass
        await gen.aclose()

    async def second_run():
        gen = stream.subscribe(session_id="s-1")
        consume_task = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0)
        stream.publish(_ev("s-1", tool_call_id="x"))
        ev = await asyncio.wait_for(consume_task, timeout=1.0)
        assert ev.tool_call_id == "x"
        await gen.aclose()

    asyncio.run(first_run())
    assert stream._has_subscribers() is False
    # New ``asyncio.run`` → brand-new loop. Pre-fix this raised
    # RuntimeError("EventStream already bound to a different event loop").
    asyncio.run(second_run())


# ---------------------------------------------------------------------------
# Regression: producer must unblock when subscriber is cancelled mid-put
# ---------------------------------------------------------------------------


def test_producer_unblocks_when_subscriber_cancelled_with_full_queue():
    """A producer thread blocked on a full subscriber queue must be released
    when the subscriber's iterator is cancelled. Pre-fix the scheduled
    ``queue.put`` future kept awaiting capacity forever (no consumer left
    to drain), so ``fut.result()`` hung the runtime/tool thread."""
    stream = EventStream(max_queue_size=2)

    async def runner():
        gen = stream.subscribe(session_id="s-1")
        # Park the consumer at the first ``queue.get`` so the
        # subscriber registers and the loop binds.
        consume_task = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0)

        publish_started = threading.Event()
        publish_returned = threading.Event()

        def producer():
            publish_started.set()
            # First two fill the queue once the consumer pulls one.
            stream.publish(_ev("s-1", tool_call_id="a"))
            stream.publish(_ev("s-1", tool_call_id="b"))
            # Third blocks because the queue is full; we expect the
            # subscriber cancellation below to release it.
            stream.publish(_ev("s-1", tool_call_id="c"))
            publish_returned.set()

        loop = asyncio.get_running_loop()
        prod_fut = loop.run_in_executor(None, producer)
        publish_started.wait(timeout=1.0)

        # Pull the first event so the queue makes room for "a","b" but
        # then immediately fills (max_queue_size=2). The producer's
        # third ``publish("c")`` will then park awaiting capacity.
        first = await asyncio.wait_for(consume_task, timeout=2.0)
        assert first.tool_call_id == "a"
        # Yield enough times for "b" to land and "c" to be parked
        # awaiting capacity on the loop.
        for _ in range(20):
            await asyncio.sleep(0)

        # Cancel the consumer mid-put. With the fix, the subscriber's
        # cleanup cancels the pending ``queue.put`` future and the
        # producer thread returns; without the fix the executor future
        # never completes and the test times out.
        gen_close = asyncio.create_task(gen.aclose())
        await asyncio.wait_for(gen_close, timeout=1.0)

        await asyncio.wait_for(
            asyncio.wrap_future(prod_fut), timeout=2.0,
        )
        assert publish_returned.is_set(), (
            "Producer thread was not released after subscriber cancellation"
        )

    asyncio.run(asyncio.wait_for(runner(), timeout=5.0))


# ---------------------------------------------------------------------------
# Regression: subagent emitter must be wired into AgentToolWrapper
# ---------------------------------------------------------------------------


def test_on_loop_publish_caps_pending_puts(caplog):
    """An on-loop publisher that emits faster than the consumer drains
    must not grow ``pending_puts`` without bound. Once the cap is
    reached events are dropped (with a warning). The bound is
    ``max_queue_size`` so total in-flight stays at most ``2 *
    max_queue_size``."""
    import logging
    stream = EventStream(max_queue_size=2)

    async def runner():
        gen = stream.subscribe(session_id="s-1")
        # Park consumer so the queue can fill, but never drain it
        # during the burst. The cap is hit purely by sync publishes.
        consume_task = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0)

        with caplog.at_level(logging.WARNING, "agentao.harness.events"):
            # max_queue_size=2 → cap on pending tasks is 2. Total
            # in-flight ceiling: 2 (queue) + 2 (pending) = 4.
            # Publishing 6 should drop at least 2 events.
            for i in range(6):
                stream.publish(_ev("s-1", tool_call_id=f"e{i}"))

        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass
        await gen.aclose()
        return [r for r in caplog.records if "exceeded backpressure" in r.getMessage()]

    drop_warnings = asyncio.run(asyncio.wait_for(runner(), timeout=2.0))
    # At least one warning fired — pre-fix this would have been zero
    # (unbounded growth) and post-fix it is at least 6 - 4 = 2.
    assert len(drop_warnings) >= 2


def test_on_loop_publish_does_not_drop_when_queue_full():
    """A producer running on the bound loop must not drop events when
    the subscriber's queue is full. Pre-fix the path used
    ``put_nowait`` and silently dropped overflow; the contract for a
    live subscriber is bounded backpressure, not lossy delivery. The
    fix schedules an ``asyncio.Task`` so the put awaits capacity in
    order — the consumer's next drain releases it."""
    stream = EventStream(max_queue_size=2)

    async def runner():
        gen = stream.subscribe(session_id="s-1")
        consume_task = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0)

        # All three publishes happen from the loop's own thread. The
        # third one parks awaiting capacity instead of being dropped.
        stream.publish(_ev("s-1", tool_call_id="a"))
        stream.publish(_ev("s-1", tool_call_id="b"))
        stream.publish(_ev("s-1", tool_call_id="c"))

        received: list[str] = []
        first = await asyncio.wait_for(consume_task, timeout=1.0)
        received.append(first.tool_call_id)
        for _ in range(2):
            ev = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
            received.append(ev.tool_call_id)
        await gen.aclose()
        return received

    received = asyncio.run(asyncio.wait_for(runner(), timeout=3.0))
    # Order preserved AND no drop — pre-fix this would have been
    # ``["a", "b"]`` with "c" silently dropped on overflow.
    assert received == ["a", "b", "c"]


def test_subagent_tools_capture_live_harness_emitter(tmp_path, monkeypatch):
    """``Agentao.__init__`` previously created the harness subagent emitter
    AFTER ``_register_agent_tools`` ran, so every ``AgentToolWrapper`` saw
    ``None`` and the lifecycle emitter was a no-op. Hoisting the harness
    setup before agent-tool registration restores the wiring; this test
    pins that ordering by inspecting the registered wrappers."""
    from agentao.agent import Agentao
    from agentao.agents.tools import AgentToolWrapper

    agent = Agentao(
        api_key="k", base_url="https://example", model="x",
        working_directory=tmp_path,
        enable_builtin_agents=True,
    )
    wrappers = [
        t for t in agent.tools.tools.values() if isinstance(t, AgentToolWrapper)
    ]
    assert wrappers, "expected at least one built-in agent tool"
    for w in wrappers:
        assert w._subagent_emitter is agent._harness_subagent_emitter, (
            "AgentToolWrapper captured a stale subagent emitter — "
            "harness setup must run before _register_agent_tools()"
        )
