"""Regression tests for sub-agent routing primitives (agentao/agents/tools.py)."""

import threading
import time

import pytest

from agentao.agents.tools import (
    BgTaskStatus,
    CancelBackgroundAgentTool,
    CompleteTaskTool,
    SubagentProgress,
    TaskComplete,
    _VALID_BG_STATUSES,
    _bg_lock,
    _bg_task_tokens,
    _bg_token_lock,
    _bg_tasks,
    _bg_notifications,
    _flush_to_disk,
    _cancel_bg_task,
    _delete_bg_task,
    _mark_bg_task_running,
    _register_bg_task,
    _push_bg_notification,
    _update_bg_task,
    drain_bg_notifications,
)
from agentao.cancellation import AgentCancelledError, CancellationToken


@pytest.fixture(autouse=True)
def _clear_bg_globals():
    """Reset module-level background-agent state before every test."""
    _bg_tasks.clear()
    _bg_notifications.clear()
    _bg_task_tokens.clear()
    yield
    _bg_tasks.clear()
    _bg_notifications.clear()
    _bg_task_tokens.clear()


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

def test_register_bg_task_initial_fields():
    agent_id = "test-agent-001"
    _register_bg_task(agent_id, "worker", "do stuff")
    assert _bg_tasks[agent_id]["status"] == "pending"
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


# ---------------------------------------------------------------------------
# Unified status set — type & validation tests
# ---------------------------------------------------------------------------

def test_valid_bg_statuses_contains_expected_values():
    assert _VALID_BG_STATUSES == {"pending", "running", "completed", "failed", "cancelled"}


def test_subagent_progress_accepts_all_valid_states():
    for state in ("pending", "running", "completed", "failed", "cancelled"):
        p = SubagentProgress(agent_name="a", state=state)
        assert p.state == state


def test_subagent_progress_failed_not_error():
    """SubagentProgress uses 'failed', not 'error', for failure state."""
    p = SubagentProgress(agent_name="a", state="failed", error="something went wrong")
    assert p.state == "failed"
    assert p.error == "something went wrong"


def test_update_bg_task_rejects_invalid_status():
    agent_id = "test-invalid-status"
    _register_bg_task(agent_id, "worker", "do stuff")
    with pytest.raises(AssertionError, match="Invalid bg task status"):
        _update_bg_task(agent_id, status="error")  # type: ignore[arg-type]


def test_update_bg_task_accepts_completed():
    agent_id = "test-completed"
    _register_bg_task(agent_id, "worker", "do stuff")
    _update_bg_task(agent_id, status="completed", result="done")
    assert _bg_tasks[agent_id]["status"] == "completed"


def test_update_bg_task_accepts_failed():
    agent_id = "test-failed"
    _register_bg_task(agent_id, "worker", "do stuff")
    _update_bg_task(agent_id, status="failed", error="boom")
    assert _bg_tasks[agent_id]["status"] == "failed"
    assert _bg_tasks[agent_id]["error"] == "boom"


def test_error_string_not_used_in_module():
    """Regression: 'error' must not appear as a state value in agents/tools.py."""
    import inspect
    import agentao.agents.tools as mod
    source = inspect.getsource(mod)
    # Ensure the old sentinel is gone from SubagentProgress instantiations
    assert 'state="error"' not in source


# ---------------------------------------------------------------------------
# Phase 2: pending initial state + created_at / started_at semantics
# ---------------------------------------------------------------------------

def test_register_bg_task_initial_status_is_pending():
    agent_id = "p2-pending"
    _register_bg_task(agent_id, "worker", "do stuff")
    assert _bg_tasks[agent_id]["status"] == "pending"


def test_register_bg_task_started_at_is_none():
    agent_id = "p2-started-none"
    _register_bg_task(agent_id, "worker", "do stuff")
    assert _bg_tasks[agent_id]["started_at"] is None


def test_register_bg_task_created_at_is_set():
    import time
    before = time.time()
    agent_id = "p2-created-at"
    _register_bg_task(agent_id, "worker", "do stuff")
    after = time.time()
    created = _bg_tasks[agent_id]["created_at"]
    assert isinstance(created, float)
    assert before <= created <= after


def test_mark_bg_task_running_sets_status_and_started_at():
    import time
    agent_id = "p2-mark-running"
    _register_bg_task(agent_id, "worker", "do stuff")
    assert _bg_tasks[agent_id]["status"] == "pending"
    assert _bg_tasks[agent_id]["started_at"] is None

    before = time.time()
    _mark_bg_task_running(agent_id)
    after = time.time()

    assert _bg_tasks[agent_id]["status"] == "running"
    started = _bg_tasks[agent_id]["started_at"]
    assert started is not None
    assert before <= started <= after


def test_mark_bg_task_running_does_not_revive_cancelled_task():
    agent_id = "p2-cancelled-stays-cancelled"
    _register_bg_task(agent_id, "worker", "do stuff")
    _update_bg_task(agent_id, status="cancelled")

    started = _mark_bg_task_running(agent_id)

    assert started is False
    assert _bg_tasks[agent_id]["status"] == "cancelled"
    assert _bg_tasks[agent_id]["started_at"] is None


def test_update_bg_task_with_none_started_at_does_not_crash():
    """_update_bg_task must not crash when started_at is None (pending → failed)."""
    agent_id = "p2-none-started"
    _register_bg_task(agent_id, "worker", "do stuff")
    assert _bg_tasks[agent_id]["started_at"] is None
    # Should not raise
    _update_bg_task(agent_id, status="failed", error="immediate failure")


# ---------------------------------------------------------------------------
# Phase 3: cancellation token registry + cancel tool
# ---------------------------------------------------------------------------

def test_cancel_pending_agent_sets_cancelled():
    agent_id = "p3-pending-cancel"
    _register_bg_task(agent_id, "worker", "do stuff")
    token = CancellationToken()
    with _bg_token_lock:
        _bg_task_tokens[agent_id] = token

    result = _cancel_bg_task(agent_id)

    assert _bg_tasks[agent_id]["status"] == "cancelled"
    assert "cancelled before it started" in result
    with _bg_token_lock:
        assert agent_id not in _bg_task_tokens


def test_cancel_running_agent_signals_token():
    agent_id = "p3-running-cancel"
    _register_bg_task(agent_id, "worker", "do stuff")
    _mark_bg_task_running(agent_id)
    token = CancellationToken()
    with _bg_token_lock:
        _bg_task_tokens[agent_id] = token

    result = _cancel_bg_task(agent_id)

    assert token.is_cancelled
    assert "Cancellation signal sent" in result


def test_cancel_rechecks_state_before_claiming_prestart():
    agent_id = "p3-pending-race"
    _register_bg_task(agent_id, "worker", "do stuff")
    token = CancellationToken()
    with _bg_token_lock:
        _bg_task_tokens[agent_id] = token
    _mark_bg_task_running(agent_id)

    result = _cancel_bg_task(agent_id)

    assert "Cancellation signal sent" in result
    assert token.is_cancelled
    assert _bg_tasks[agent_id]["status"] == "running"


def test_cancel_completed_agent_is_noop():
    agent_id = "p3-completed-noop"
    _register_bg_task(agent_id, "worker", "do stuff")
    _mark_bg_task_running(agent_id)
    _update_bg_task(agent_id, status="completed", result="done")

    result = _cancel_bg_task(agent_id)

    assert "already completed" in result
    assert _bg_tasks[agent_id]["status"] == "completed"


def test_cancel_nonexistent_agent_returns_error():
    result = _cancel_bg_task("does-not-exist")
    assert "No background agent found" in result


def test_agent_cancelled_error_not_swallowed_as_failed():
    """Simulate the _run() exception dispatch: AgentCancelledError → 'cancelled', not 'failed'."""
    agent_id = "p3-cancelled-dispatch"
    _register_bg_task(agent_id, "worker", "do stuff")
    _mark_bg_task_running(agent_id)

    # Reproduce the exact exception handling logic from _run()
    try:
        raise AgentCancelledError("user-cancel")
    except AgentCancelledError:
        _update_bg_task(agent_id, status="cancelled")
    except Exception as exc:
        _update_bg_task(agent_id, status="failed", error=str(exc))

    assert _bg_tasks[agent_id]["status"] == "cancelled"


def test_token_removed_from_registry_after_completion():
    """Token registry entry is cleaned up in the finally block after completion."""
    agent_id = "p3-token-cleanup"
    _register_bg_task(agent_id, "worker", "do stuff")
    token = CancellationToken()
    with _bg_token_lock:
        _bg_task_tokens[agent_id] = token

    # Simulate the finally block that _run() runs
    try:
        _update_bg_task(agent_id, status="completed", result="done")
    finally:
        with _bg_token_lock:
            _bg_task_tokens.pop(agent_id, None)

    with _bg_token_lock:
        assert agent_id not in _bg_task_tokens


def test_cancel_background_agent_tool_name():
    assert CancelBackgroundAgentTool().name == "cancel_background_agent"


def test_cancel_background_agent_tool_delegates_to_helper():
    agent_id = "p3-tool-delegate"
    _register_bg_task(agent_id, "worker", "do stuff")
    token = CancellationToken()
    with _bg_token_lock:
        _bg_task_tokens[agent_id] = token

    result = CancelBackgroundAgentTool().execute(agent_id=agent_id)
    assert "cancelled before it started" in result


def test_delete_completed_agent_removes_task():
    agent_id = "p3-delete-completed"
    _register_bg_task(agent_id, "worker", "do stuff")
    _update_bg_task(agent_id, status="completed", result="done")

    result = _delete_bg_task(agent_id)

    assert "Deleted background agent" in result
    assert agent_id not in _bg_tasks


def test_delete_running_agent_is_rejected():
    agent_id = "p3-delete-running"
    _register_bg_task(agent_id, "worker", "do stuff")
    _mark_bg_task_running(agent_id)

    result = _delete_bg_task(agent_id)

    assert "cannot be deleted" in result
    assert _bg_tasks[agent_id]["status"] == "running"


def test_delete_nonexistent_agent_returns_error():
    result = _delete_bg_task("does-not-exist")
    assert "No background agent found" in result


# ---------------------------------------------------------------------------
# Phase 4: cancelled display path via CheckBackgroundAgentTool
# ---------------------------------------------------------------------------

def test_check_bg_agent_tool_cancelled_status():
    """CheckBackgroundAgentTool returns 'was cancelled' for a cancelled task."""
    from agentao.agents.tools import CheckBackgroundAgentTool
    agent_id = "p4-check-cancelled"
    _register_bg_task(agent_id, "worker", "do stuff")
    _update_bg_task(agent_id, status="cancelled")

    result = CheckBackgroundAgentTool().execute(agent_id=agent_id)
    assert "was cancelled" in result


def test_check_bg_agent_tool_list_shows_cancelled():
    """CheckBackgroundAgentTool list branch includes cancelled tasks."""
    from agentao.agents.tools import CheckBackgroundAgentTool
    agent_id = "p4-list-cancelled"
    _register_bg_task(agent_id, "worker", "do stuff")
    _update_bg_task(agent_id, status="cancelled")

    result = CheckBackgroundAgentTool().execute(agent_id="")
    assert "cancelled" in result


def test_check_bg_agent_tool_list_does_not_label_cancelled_before_start_as_queued():
    from agentao.agents.tools import CheckBackgroundAgentTool

    agent_id = "p4-cancelled-before-start"
    _register_bg_task(agent_id, "worker", "do stuff")
    _update_bg_task(agent_id, status="cancelled")

    result = CheckBackgroundAgentTool().execute(agent_id="")
    assert "cancelled before start" in result
    assert "queued" not in result


# ---------------------------------------------------------------------------
# Phase 6: notification queue coverage for all terminal states
# ---------------------------------------------------------------------------

def test_cancelled_task_pushes_notification():
    agent_id = "p6-cancel-notify"
    _register_bg_task(agent_id, "worker", "do stuff")
    drain_bg_notifications()  # clear any from register

    _update_bg_task(agent_id, status="cancelled")

    notes = drain_bg_notifications()
    assert len(notes) == 1
    assert "cancelled" in notes[0]
    assert agent_id in notes[0]


def test_failed_task_pushes_notification():
    agent_id = "p6-fail-notify"
    _register_bg_task(agent_id, "worker", "do stuff")
    drain_bg_notifications()

    _update_bg_task(agent_id, status="failed", error="something broke")

    notes = drain_bg_notifications()
    assert len(notes) == 1
    assert "failed" in notes[0]


def test_completed_without_result_does_not_push_notification():
    """The existing guard (result is not None) must be preserved."""
    agent_id = "p6-complete-no-result"
    _register_bg_task(agent_id, "worker", "do stuff")
    drain_bg_notifications()

    _update_bg_task(agent_id, status="completed", result=None)

    notes = drain_bg_notifications()
    assert notes == []


def test_flush_to_disk_serializes_writes(monkeypatch):
    entered_first = False
    release_first = False
    second_entered = False
    snapshots = []

    def fake_save(snapshot):
        nonlocal entered_first, release_first, second_entered
        snapshots.append(snapshot)
        if not entered_first:
            entered_first = True
            deadline = time.time() + 1
            while not release_first and time.time() < deadline:
                time.sleep(0.01)
        else:
            second_entered = True

    _register_bg_task("flush-1", "worker", "first")
    monkeypatch.setattr("agentao.agents.store.save_bg_task_store", fake_save)

    t1 = threading.Thread(target=_flush_to_disk)
    t1.start()

    deadline = time.time() + 1
    while not entered_first and time.time() < deadline:
        time.sleep(0.01)

    with _bg_lock:
        _bg_tasks["flush-1"]["status"] = "completed"

    t2 = threading.Thread(target=_flush_to_disk)
    t2.start()

    time.sleep(0.05)
    assert second_entered is False

    release_first = True
    t1.join()
    t2.join()

    assert snapshots[0]["flush-1"]["status"] == "pending"
    assert snapshots[1]["flush-1"]["status"] == "completed"
