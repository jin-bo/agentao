"""A bounded in-turn wait: ``check_background_agent(wait_seconds=…)``.

Design: ``docs/design/background-subagent-wake.md`` §6.2 (step B). The wait
must return with the child's result when it settles, stop within one check
interval when the turn is cancelled — without cancelling the child — and
give up at its bound with an instruction not to repeat itself. A parallel
batch's Ctrl+C must cancel the turn token before the pool joins its workers.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List

import pytest

from agentao.agents.bg_store import BackgroundTaskStore
from agentao.agents.tools import _bg_tools
from agentao.agents.tools._bg_tools import MAX_WAIT_SECONDS, CheckBackgroundAgentTool
from agentao.cancellation import CancellationToken
from agentao.runtime.tool_executor import ToolExecutor
from agentao.tools import Tool

from tests.support.host_events import NullTransport, make_plan


def _running(store: BackgroundTaskStore, agent_id: str = "a1") -> None:
    store.register(agent_id, "worker", "task")
    store.mark_running(agent_id)


def _later(seconds: float, fn, *args, **kwargs) -> threading.Thread:
    t = threading.Thread(target=lambda: (time.sleep(seconds), fn(*args, **kwargs)))
    t.start()
    return t


def _tool(store, token=None) -> CheckBackgroundAgentTool:
    tool = CheckBackgroundAgentTool(store)
    tool._cancellation_token = token
    return tool


# ---------------------------------------------------------------------------
# Schema and argument handling
# ---------------------------------------------------------------------------


def test_schema_offers_a_bounded_optional_wait():
    props = _tool(BackgroundTaskStore()).parameters["properties"]
    assert props["wait_seconds"]["maximum"] == MAX_WAIT_SECONDS
    assert props["wait_seconds"]["minimum"] == 0
    assert _tool(BackgroundTaskStore()).parameters["required"] == ["agent_id"]


def test_default_output_is_unchanged():
    store = BackgroundTaskStore()
    _running(store)
    tool = _tool(store)
    assert tool.execute(agent_id="a1").startswith("Agent 'worker' (a1) is still running")
    assert tool.execute(agent_id="a1") == tool.execute(agent_id="a1", wait_seconds=0)


def test_invalid_wait_is_reported_not_raised():
    store = BackgroundTaskStore()
    _running(store)
    assert "Invalid wait_seconds" in _tool(store).execute(agent_id="a1", wait_seconds="soon")


def test_negative_wait_answers_at_once():
    store = BackgroundTaskStore()
    _running(store)
    t0 = time.monotonic()
    _tool(store).execute(agent_id="a1", wait_seconds=-5)
    assert time.monotonic() - t0 < 0.5


def test_listing_ignores_the_wait():
    store = BackgroundTaskStore()
    _running(store)
    t0 = time.monotonic()
    assert _tool(store).execute(agent_id="", wait_seconds=30).startswith("Background agents:")
    assert time.monotonic() - t0 < 0.5


def test_unknown_id_returns_immediately():
    t0 = time.monotonic()
    out = _tool(BackgroundTaskStore()).execute(agent_id="nope", wait_seconds=30)
    assert out == "No background agent found with ID: nope"
    assert time.monotonic() - t0 < 0.5


# ---------------------------------------------------------------------------
# Settling
# ---------------------------------------------------------------------------


def test_wait_returns_the_result_when_the_child_completes():
    store = BackgroundTaskStore()
    _running(store)
    _later(0.3, store.update, "a1", status="completed", result="the answer")
    t0 = time.monotonic()
    out = _tool(store).execute(agent_id="a1", wait_seconds=30)
    assert time.monotonic() - t0 < 2
    assert out.startswith("Agent 'worker' (a1) completed")
    assert out.endswith("the answer")


def test_a_terminal_result_can_be_followed_by_its_queued_preview():
    """Accepted by design: the notice stays queued and is injected with the
    next request, as after an immediate check."""
    store = BackgroundTaskStore()
    _running(store)
    _later(0.2, store.update, "a1", status="completed", result="the answer")
    _tool(store).execute(agent_id="a1", wait_seconds=30)
    assert any("the answer" in n for n in store.drain_notifications())


def test_wait_sees_a_pending_cancellation():
    store = BackgroundTaskStore()
    store.register("a1", "worker", "task")
    _later(0.3, store.cancel, "a1")
    out = _tool(store).execute(agent_id="a1", wait_seconds=30)
    assert out == "Agent 'worker' (a1) was cancelled."


def test_wait_sees_a_failure():
    store = BackgroundTaskStore()
    _running(store)
    _later(0.2, store.update, "a1", status="failed", error="boom")
    assert _tool(store).execute(agent_id="a1", wait_seconds=30).endswith("failed: boom")


def test_a_sibling_store_completion_is_seen_within_an_interval(tmp_path):
    """Two stores on one persistence file: the waiter's condition gets no
    signal from the owner, so the periodic re-read is what finds it."""
    owner = BackgroundTaskStore(persistence_dir=tmp_path)
    waiter = BackgroundTaskStore(persistence_dir=tmp_path)
    _running(owner)
    assert waiter.get("a1")["status"] == "running"
    _later(0.3, owner.update, "a1", status="completed", result="from the sibling")
    t0 = time.monotonic()
    out = _tool(waiter).execute(agent_id="a1", wait_seconds=30)
    assert time.monotonic() - t0 < 2
    assert out.endswith("from the sibling")


# ---------------------------------------------------------------------------
# Timeout and cancellation
# ---------------------------------------------------------------------------


def test_timeout_reports_running_and_says_not_to_repeat():
    store = BackgroundTaskStore()
    _running(store)
    t0 = time.monotonic()
    out = _tool(store).execute(agent_id="a1", wait_seconds=1)
    assert 0.9 < time.monotonic() - t0 < 2.5
    assert "is still running" in out
    assert "Do not repeat the same wait" in out
    assert store.get("a1")["status"] == "running"


def test_cancelling_the_turn_ends_the_wait_but_not_the_child():
    store = BackgroundTaskStore()
    _running(store)
    child_token = CancellationToken()
    store.register_token("a1", child_token)
    turn = CancellationToken()
    _later(0.3, turn.cancel, "user-cancel")
    t0 = time.monotonic()
    out = _tool(store, turn).execute(agent_id="a1", wait_seconds=30)
    assert time.monotonic() - t0 < 1.5
    assert "this turn was cancelled" in out
    assert "was not cancelled" in out
    assert not child_token.is_cancelled
    assert store.get("a1")["status"] == "running"


def test_a_long_wait_reports_progress(monkeypatch):
    monkeypatch.setattr(_bg_tools, "_WAIT_PROGRESS_SECONDS", 0.3)
    store = BackgroundTaskStore()
    _running(store)
    chunks: List[str] = []
    tool = _tool(store)
    tool.output_callback = chunks.append
    tool.execute(agent_id="a1", wait_seconds=1)
    assert chunks and all(c.startswith("Still waiting for agent 'worker'") for c in chunks)


# ---------------------------------------------------------------------------
# Executor: Ctrl+C in a parallel batch
# ---------------------------------------------------------------------------


class _Interrupter(Tool):
    """Delivers a KeyboardInterrupt the way a worker would surface one."""

    @property
    def name(self) -> str:
        return "interrupter"

    @property
    def description(self) -> str:
        return "raises KeyboardInterrupt"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object"}

    def execute(self, **_kwargs) -> str:
        time.sleep(0.2)
        raise KeyboardInterrupt


def test_ctrl_c_in_a_parallel_batch_cancels_before_the_pool_joins():
    store = BackgroundTaskStore()
    _running(store)
    token = CancellationToken()
    executor = ToolExecutor(NullTransport(), logging.getLogger("test.bg_wait"))
    plans = [
        make_plan(_tool(store), args={"agent_id": "a1", "wait_seconds": 20}, call_id="w"),
        make_plan(_Interrupter(), call_id="i"),
    ]
    t0 = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        executor.execute_batch(plans, cancellation_token=token)
    assert time.monotonic() - t0 < 5
    assert token.is_cancelled


def test_acp_session_cancel_ends_the_wait():
    """``session/cancel`` fires the session's turn token from the dispatcher
    thread; the same token is what the executor hands the waiting tool."""
    from agentao.acp import session_cancel as acp_session_cancel
    from agentao.acp.models import AcpSessionState

    from tests.support.acp_server import make_initialized_server

    server = make_initialized_server()
    state = AcpSessionState(session_id="sess_wait")
    state.cancel_token = CancellationToken()
    server.sessions.create(state)

    store = BackgroundTaskStore()
    _running(store)
    _later(
        0.3, acp_session_cancel.handle_session_cancel, server, {"sessionId": "sess_wait"}
    )
    t0 = time.monotonic()
    out = _tool(store, state.cancel_token).execute(agent_id="a1", wait_seconds=30)
    assert time.monotonic() - t0 < 1.5
    assert "this turn was cancelled" in out
    assert state.cancel_token.reason == "acp-session-cancel"
