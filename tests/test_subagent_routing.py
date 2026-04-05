"""Regression tests for sub-agent routing primitives (agentao/agents/tools.py)."""

import pytest

from agentao.agents.tools import (
    CompleteTaskTool,
    SubagentProgress,
    TaskComplete,
    _bg_tasks,
    _bg_notifications,
    _register_bg_task,
    _push_bg_notification,
    drain_bg_notifications,
)


@pytest.fixture(autouse=True)
def _clear_bg_globals():
    """Reset module-level background-agent state before every test."""
    _bg_tasks.clear()
    _bg_notifications.clear()
    yield
    _bg_tasks.clear()
    _bg_notifications.clear()


# ---------------------------------------------------------------------------
# TaskComplete signal
# ---------------------------------------------------------------------------

def test_task_complete_carries_result_string():
    exc = TaskComplete("my result")
    assert exc.result == "my result"


def test_task_complete_is_exception():
    assert issubclass(TaskComplete, Exception)


def test_complete_task_tool_raises_task_complete():
    tool = CompleteTaskTool()
    with pytest.raises(TaskComplete) as exc_info:
        tool.execute(result="done")
    assert exc_info.value.result == "done"


def test_complete_task_tool_name():
    assert CompleteTaskTool().name == "complete_task"


# ---------------------------------------------------------------------------
# SubagentProgress dataclass
# ---------------------------------------------------------------------------

def test_subagent_progress_required_fields():
    p = SubagentProgress(agent_name="researcher", state="running")
    assert p.agent_name == "researcher"
    assert p.state == "running"


def test_subagent_progress_optional_fields_default_to_none():
    p = SubagentProgress(agent_name="a", state="completed")
    assert p.result is None
    assert p.error is None


def test_subagent_progress_numeric_defaults():
    p = SubagentProgress(agent_name="a", state="running")
    assert p.turns == 0
    assert p.tool_calls == 0
    assert p.tokens == 0
    assert p.duration_ms == 0


# ---------------------------------------------------------------------------
# Background task registry
# ---------------------------------------------------------------------------

def test_register_bg_task_status_is_running():
    agent_id = "test-agent-001"
    _register_bg_task(agent_id, "worker", "do stuff")
    assert _bg_tasks[agent_id]["status"] == "running"
    assert _bg_tasks[agent_id]["agent_name"] == "worker"
    assert _bg_tasks[agent_id]["task"] == "do stuff"
    # cleanup
    del _bg_tasks[agent_id]


# ---------------------------------------------------------------------------
# Notification queue
# ---------------------------------------------------------------------------

def test_drain_bg_notifications_returns_all_and_clears():
    _push_bg_notification("msg-1")
    _push_bg_notification("msg-2")
    drained = drain_bg_notifications()
    assert "msg-1" in drained
    assert "msg-2" in drained
    assert drain_bg_notifications() == []


def test_drain_is_idempotent_when_empty():
    # Ensure queue is empty first
    drain_bg_notifications()
    assert drain_bg_notifications() == []


def test_push_and_drain_order_preserved():
    for i in range(5):
        _push_bg_notification(f"n{i}")
    drained = drain_bg_notifications()
    assert drained == [f"n{i}" for i in range(5)]
