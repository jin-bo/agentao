"""P0.7 regression: ``arun()`` + ``events()`` + ``cancel()`` cooperate cleanly.

Embedded async hosts run a turn via ``await agent.arun(...)`` while a
parallel task drains ``async for event in agent.events(...)``. When the
host cancels (client disconnect, request timeout), the runtime must:

1. Forward the asyncio cancellation through the cancellation token to
   the in-flight ``chat()`` work running in the executor.
2. Drain whatever events were already published — the iterator must not
   hang on a half-full queue.
3. Leave no orphan asyncio tasks behind (every ``create_task`` made by
   either ``arun`` or ``events`` must terminate or be explicitly
   cancelled before the host loop closes).

These are the properties a host like FastAPI relies on to avoid leaked
threads and never-collected futures across many short request lifetimes.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from agentao.cancellation import CancellationToken
from agentao.harness.models import ToolLifecycleEvent
from agentao.runtime import identity as runtime_identity


def _make_agent(tmp_path: Path):
    """Construct an Agentao via the public embedded-host path.

    Pass ``llm_client=`` explicitly — exercising the very seam the test
    documents — instead of leaning on ``conftest.py``'s env-backfill
    that fills missing api/base/model kwargs.
    """
    mock_llm = Mock()
    mock_llm.logger = Mock()
    mock_llm.model = "gpt-test"
    with patch("agentao.tooling.mcp_tools.McpClientManager"), patch(
        "agentao.tooling.mcp_tools.load_mcp_config", return_value={}
    ):
        from agentao.agent import Agentao

        return Agentao(working_directory=tmp_path, llm_client=mock_llm)


def _tool_event(session_id: str, *, tool_call_id: str, phase: str) -> ToolLifecycleEvent:
    return ToolLifecycleEvent(
        session_id=session_id,
        tool_call_id=tool_call_id,
        tool_name="run_shell_command",
        phase=phase,  # type: ignore[arg-type]
        started_at=runtime_identity.utc_now_rfc3339(),
    )


def test_arun_events_cancel_drains_and_propagates(tmp_path: Path) -> None:
    """One turn: subscribe, publish, cancel, assert all three contracts hold."""
    agent = _make_agent(tmp_path)
    session_id = agent._session_id

    seen_token: dict[str, CancellationToken] = {}
    started = asyncio.Event()
    main_loop_holder: dict[str, asyncio.AbstractEventLoop] = {}

    def _fake_chat(user_message, max_iterations, cancellation_token):
        """Stand-in for the chat loop: publish two events, then block until cancelled."""
        seen_token["t"] = cancellation_token
        # Publish two harness events so a subscriber has something to drain.
        agent._harness_events.publish(
            _tool_event(session_id, tool_call_id="tc-1", phase="started")
        )
        agent._harness_events.publish(
            _tool_event(session_id, tool_call_id="tc-1", phase="completed")
        )
        # Signal the loop that we're inside the executor.
        main_loop_holder["loop"].call_soon_threadsafe(started.set)
        # Block until cancellation propagates from the awaiting task.
        cancellation_token._event.wait(timeout=2.0)
        return "should-not-return"

    agent.chat = _fake_chat  # type: ignore[assignment]

    async def _run():
        main_loop_holder["loop"] = asyncio.get_running_loop()
        agent._harness_events.bind_loop(main_loop_holder["loop"])

        received: list[ToolLifecycleEvent] = []

        async def _drain_events():
            async for event in agent.events(session_id=session_id):
                received.append(event)
                if len(received) == 2:
                    return

        # Capture the set of running tasks before we start, so any orphan
        # check at the end can compare against this baseline.
        before_tasks = {t for t in asyncio.all_tasks() if not t.done()}

        consumer = asyncio.create_task(_drain_events())
        runner = asyncio.create_task(agent.arun("hi"))

        # Wait until the executor is inside the chat body.
        await started.wait()

        # Drain both events while the executor is parked.
        await asyncio.wait_for(consumer, timeout=2.0)

        # Now cancel the runner; the cancellation must reach the token.
        runner.cancel()
        try:
            await runner
        except asyncio.CancelledError:
            pass

        # Give the loop a moment for finalizers to run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # Compare task sets: every task we spawned must be done.
        after_tasks = {t for t in asyncio.all_tasks() if not t.done()}
        new_tasks = after_tasks - before_tasks

        return received, new_tasks

    received, new_tasks = asyncio.run(_run())

    # Contract 1 — cancellation reached the executor's token.
    assert "t" in seen_token, "fake chat was never invoked"
    assert seen_token["t"].is_cancelled, (
        "cancellation did not propagate from the awaiting task to the "
        "in-flight chat() token — embedded async hosts depend on this"
    )

    # Contract 2 — events stream drained the two published events cleanly.
    assert len(received) == 2
    phases = [e.phase for e in received]
    assert phases == ["started", "completed"], (
        f"events arrived out of order: {phases}"
    )

    # Contract 3 — no orphan asyncio tasks left around.
    assert not new_tasks, (
        "arun/events left orphan asyncio tasks behind:\n  "
        + "\n  ".join(repr(t) for t in new_tasks)
    )


def test_subscriber_iterator_releases_on_cancel(tmp_path: Path) -> None:
    """Cancelling the events() consumer task must not wedge future publishes.

    A regression where a cancelled consumer leaves its queue attached
    would block every future ``publish`` for matching events on a queue
    nobody drains. This test cancels the consumer first, then publishes
    and asserts the publish does not block.
    """
    agent = _make_agent(tmp_path)
    session_id = agent._session_id

    async def _run():
        agent._harness_events.bind_loop(asyncio.get_running_loop())

        async def _drain():
            async for _event in agent.events(session_id=session_id):
                # Never returns — we cancel from outside.
                pass

        consumer = asyncio.create_task(_drain())
        await asyncio.sleep(0)  # let the subscribe register

        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass

        # After cancellation the subscriber is gone; publishes should be
        # near-instant (drop path), not block.
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        for i in range(50):
            agent._harness_events.publish(
                _tool_event(session_id, tool_call_id=f"tc-{i}", phase="started")
            )
        return loop.time() - t0

    elapsed = asyncio.run(_run())
    assert elapsed < 0.5, (
        f"publishing after consumer cancellation took {elapsed:.3f}s — "
        "the cancelled subscriber's queue was not released"
    )
