"""A cancelled ``arun`` waits for its turn to finish, and turns never overlap.

Before this, ``arun`` re-raised ``CancelledError`` the moment the host task
was cancelled, while the worker thread was still unwinding the turn
(backfilling orphaned tool results, emitting ``TURN_END``). A host that
started the next ``arun`` straight away ran it against the same
``agent.messages`` the first turn was still writing.

These drive the real ``Agentao.chat`` → ``run_turn`` path; only the loop body
(``_chat_inner``) is replaced, so the turn lock and ``TURN_END`` are the
production ones.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import threading
import time
from unittest.mock import Mock, patch

import pytest

from agentao.cancellation import AgentCancelledError
from agentao.runtime.turn import TurnInProgressError


def _make_agent(tmp_path):
    with patch("agentao.agent.LLMClient") as mock_llm_cls, \
         patch("agentao.tooling.mcp_tools.load_mcp_config", return_value={}), \
         patch("agentao.tooling.mcp_tools.McpClientManager"):
        mock_llm = Mock()
        mock_llm.logger = Mock()
        mock_llm.model = "gpt-test"
        mock_llm_cls.return_value = mock_llm

        from agentao.agent import Agentao
        return Agentao(working_directory=tmp_path)


def _slow_cleanup_inner(order, started, cleanup_s=0.3):
    """A loop body that, once cancelled, takes ``cleanup_s`` to unwind."""
    def _inner(user_message, max_iterations, token, *rest):
        started.set()
        token._event.wait(timeout=5.0)
        time.sleep(cleanup_s)
        order.append("worker_cleanup_done")
        raise AgentCancelledError(token.reason)
    return _inner


def _run_cancelled(agent, started, order):
    async def _run():
        task = asyncio.create_task(agent.arun("hi"))
        await asyncio.get_running_loop().run_in_executor(None, started.wait)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        order.append("host_sees_cancel")
    asyncio.run(_run())


def test_host_sees_the_cancel_only_after_the_turn_ended(tmp_path):
    agent = _make_agent(tmp_path)
    order, started = [], threading.Event()
    agent._chat_inner = _slow_cleanup_inner(order, started)

    _run_cancelled(agent, started, order)

    assert order == ["worker_cleanup_done", "host_sees_cancel"]
    assert agent._current_turn_id is None
    assert not agent._turn_lock.locked()
    assert agent.messages[-1]["content"].startswith("[Cancelled")


def test_a_second_turn_is_refused_while_one_runs(tmp_path):
    agent = _make_agent(tmp_path)
    release, entered = threading.Event(), threading.Event()

    def _blocking_inner(*args):
        entered.set()
        release.wait(timeout=5.0)
        return "first"

    agent._chat_inner = _blocking_inner
    first = threading.Thread(target=agent.chat, args=("one",))
    first.start()
    try:
        assert entered.wait(timeout=5.0)
        turn_id, messages = agent._current_turn_id, list(agent.messages)
        with pytest.raises(TurnInProgressError):
            agent.chat("two")
        # The refused call touched no turn state.
        assert agent._current_turn_id == turn_id
        assert agent.messages == messages
    finally:
        release.set()
        first.join(timeout=5.0)

    agent._chat_inner = lambda *a: "third"
    assert agent.chat("three") == "third"


def test_cleanup_past_the_budget_leaves_the_turn_locked(tmp_path, caplog):
    # The wait is bounded; past it the host gets its cancel back and the lock
    # is what stops the next turn from overlapping the one still unwinding.
    agent = _make_agent(tmp_path)
    order, started = [], threading.Event()
    agent._chat_inner = _slow_cleanup_inner(order, started, cleanup_s=0.8)

    with patch("agentao.agent._ARUN_CANCEL_CLEANUP_TIMEOUT_S", 0.1), \
         caplog.at_level(logging.WARNING, logger="agentao.agent"):
        _run_cancelled(agent, started, order)

    assert order == ["host_sees_cancel"]
    assert "still running" in caplog.text
    with pytest.raises(TurnInProgressError):
        agent.chat("too soon")

    deadline = time.monotonic() + 5.0
    while agent._turn_lock.locked() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert order == ["host_sees_cancel", "worker_cleanup_done"]
    agent._chat_inner = lambda *a: "after"
    assert agent.chat("later") == "after"


def test_a_cleanup_error_is_logged_and_the_cancel_still_raised(tmp_path, caplog):
    agent = _make_agent(tmp_path)
    started = threading.Event()

    def _broken_chat(user_message, max_iterations, token):
        started.set()
        token._event.wait(timeout=5.0)
        raise RuntimeError("cleanup blew up")

    agent.chat = _broken_chat  # type: ignore[assignment]
    with caplog.at_level(logging.WARNING):
        _run_cancelled(agent, started, [])
        gc.collect()

    assert "cleanup blew up" in caplog.text
    assert "never retrieved" not in caplog.text


def test_a_turn_still_queued_is_dropped_not_run_later(tmp_path):
    # With the pool busy, a cancelled arun's work has not started. The shield
    # must not keep it alive: it would run afterwards and add a ghost turn.
    from concurrent.futures import ThreadPoolExecutor

    agent = _make_agent(tmp_path)
    pool = ThreadPoolExecutor(max_workers=1)
    release, busy = threading.Event(), threading.Event()
    pool.submit(lambda: (busy.set(), release.wait(timeout=5.0)))
    assert busy.wait(timeout=5.0)
    ran = []
    agent._chat_inner = lambda *a: ran.append("ran") or "late"

    async def _run():
        task = asyncio.create_task(agent.arun("queued"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        with patch("agentao.agent._get_arun_pool", return_value=pool):
            asyncio.run(_run())
    finally:
        release.set()
        pool.shutdown(wait=True)

    assert ran == []
    assert agent.messages == []
    assert not agent._turn_lock.locked()


def test_a_late_cleanup_error_is_logged_after_the_host_loop_closed(tmp_path, caplog):
    # Past the wait, ``asyncio.run`` closes the host loop; the worker's error
    # arrives after that and must still be logged.
    agent = _make_agent(tmp_path)
    started, finished = threading.Event(), threading.Event()

    def _late_broken_chat(user_message, max_iterations, token):
        started.set()
        token._event.wait(timeout=5.0)
        time.sleep(0.4)
        try:
            raise RuntimeError("late cleanup failure")
        finally:
            finished.set()

    agent.chat = _late_broken_chat  # type: ignore[assignment]
    with patch("agentao.agent._ARUN_CANCEL_CLEANUP_TIMEOUT_S", 0.05), \
         caplog.at_level(logging.WARNING, logger="agentao.agent"):
        _run_cancelled(agent, started, [])
        assert "late cleanup failure" not in caplog.text
        assert finished.wait(timeout=5.0)
        deadline = time.monotonic() + 2.0
        while "late cleanup failure" not in caplog.text and time.monotonic() < deadline:
            time.sleep(0.02)

    assert "late cleanup failure" in caplog.text


def test_a_late_error_on_an_open_loop_is_retrieved(tmp_path, caplog):
    # Past the wait but with the host loop still running, the error lands on
    # the loop-bound future too; it must be read there as well.
    agent = _make_agent(tmp_path)
    started = threading.Event()

    def _late_broken_chat(user_message, max_iterations, token):
        started.set()
        token._event.wait(timeout=5.0)
        time.sleep(0.2)
        raise RuntimeError("late on open loop")

    agent.chat = _late_broken_chat  # type: ignore[assignment]

    async def _run():
        task = asyncio.create_task(agent.arun("hi"))
        await asyncio.get_running_loop().run_in_executor(None, started.wait)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.5)  # the worker's error lands while we are open

    with patch("agentao.agent._ARUN_CANCEL_CLEANUP_TIMEOUT_S", 0.05), \
         caplog.at_level(logging.WARNING):
        asyncio.run(_run())
        gc.collect()

    assert "late on open loop" in caplog.text
    assert "never retrieved" not in caplog.text
