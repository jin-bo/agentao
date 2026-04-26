"""Regression tests for sub-agent routing primitives.

Each test gets a fresh in-memory :class:`BackgroundTaskStore` so two
tests cannot see each other's tasks.
"""

import threading
import time

import pytest

from agentao.agents.bg_store import BackgroundTaskStore, _VALID_BG_STATUSES
from agentao.agents.tools import (
    CancelBackgroundAgentTool,
    CheckBackgroundAgentTool,
    CompleteTaskTool,
    SubagentProgress,
    TaskComplete,
)
from agentao.cancellation import AgentCancelledError, CancellationToken


@pytest.fixture
def bg_store():
    """Fresh in-memory store per test (no persistence path)."""
    return BackgroundTaskStore(persistence_dir=None)


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

def test_register_bg_task_initial_fields(bg_store):
    bg_store.register("test-agent-001", "worker", "do stuff")
    rec = bg_store.get("test-agent-001")
    assert rec["status"] == "pending"
    assert rec["agent_name"] == "worker"
    assert rec["task"] == "do stuff"


# ---------------------------------------------------------------------------
# Notification queue
# ---------------------------------------------------------------------------

def test_drain_bg_notifications_returns_all_and_clears(bg_store):
    bg_store.push_notification("msg-1")
    bg_store.push_notification("msg-2")
    drained = bg_store.drain_notifications()
    assert "msg-1" in drained
    assert "msg-2" in drained
    assert bg_store.drain_notifications() == []


def test_drain_is_idempotent_when_empty(bg_store):
    assert bg_store.drain_notifications() == []
    assert bg_store.drain_notifications() == []


def test_push_and_drain_order_preserved(bg_store):
    for i in range(5):
        bg_store.push_notification(f"n{i}")
    drained = bg_store.drain_notifications()
    assert drained == [f"n{i}" for i in range(5)]


# ---------------------------------------------------------------------------
# Multi-store isolation — the regression we're actually preventing
# ---------------------------------------------------------------------------

def test_two_stores_do_not_share_tasks():
    a = BackgroundTaskStore(persistence_dir=None)
    b = BackgroundTaskStore(persistence_dir=None)
    a.register("only-in-a", "worker", "task")
    assert a.get("only-in-a") is not None
    assert b.get("only-in-a") is None


def test_two_stores_do_not_share_notifications():
    a = BackgroundTaskStore(persistence_dir=None)
    b = BackgroundTaskStore(persistence_dir=None)
    a.push_notification("for-a-only")
    assert a.drain_notifications() == ["for-a-only"]
    assert b.drain_notifications() == []


def test_two_stores_do_not_share_tokens():
    a = BackgroundTaskStore(persistence_dir=None)
    b = BackgroundTaskStore(persistence_dir=None)
    tok = CancellationToken()
    a.register_token("aid", tok)
    assert a.get_token("aid") is tok
    assert b.get_token("aid") is None


# ---------------------------------------------------------------------------
# Status validation
# ---------------------------------------------------------------------------

def test_valid_bg_statuses_contains_expected_values():
    assert _VALID_BG_STATUSES == {"pending", "running", "completed", "failed", "cancelled"}


def test_subagent_progress_accepts_all_valid_states():
    for state in ("pending", "running", "completed", "failed", "cancelled"):
        p = SubagentProgress(agent_name="a", state=state)
        assert p.state == state


def test_subagent_progress_failed_not_error():
    p = SubagentProgress(agent_name="a", state="failed", error="something went wrong")
    assert p.state == "failed"
    assert p.error == "something went wrong"


def test_update_bg_task_rejects_invalid_status(bg_store):
    bg_store.register("invalid-status", "worker", "do stuff")
    with pytest.raises(AssertionError, match="Invalid bg task status"):
        bg_store.update("invalid-status", status="error")  # type: ignore[arg-type]


def test_update_bg_task_accepts_completed(bg_store):
    bg_store.register("completed-id", "worker", "do stuff")
    bg_store.update("completed-id", status="completed", result="done")
    assert bg_store.get("completed-id")["status"] == "completed"


def test_update_bg_task_accepts_failed(bg_store):
    bg_store.register("failed-id", "worker", "do stuff")
    bg_store.update("failed-id", status="failed", error="boom")
    rec = bg_store.get("failed-id")
    assert rec["status"] == "failed"
    assert rec["error"] == "boom"


def test_error_string_not_used_in_module():
    """Regression: 'error' must not appear as a state value in agents/tools.py."""
    import inspect
    import agentao.agents.tools as mod
    source = inspect.getsource(mod)
    assert 'state="error"' not in source


# ---------------------------------------------------------------------------
# Pending state + created_at / started_at semantics
# ---------------------------------------------------------------------------

def test_register_bg_task_initial_status_is_pending(bg_store):
    bg_store.register("p2-pending", "worker", "do stuff")
    assert bg_store.get("p2-pending")["status"] == "pending"


def test_register_bg_task_started_at_is_none(bg_store):
    bg_store.register("p2-started-none", "worker", "do stuff")
    assert bg_store.get("p2-started-none")["started_at"] is None


def test_register_bg_task_created_at_is_set(bg_store):
    before = time.time()
    bg_store.register("p2-created-at", "worker", "do stuff")
    after = time.time()
    created = bg_store.get("p2-created-at")["created_at"]
    assert isinstance(created, float)
    assert before <= created <= after


def test_mark_bg_task_running_sets_status_and_started_at(bg_store):
    bg_store.register("p2-mark-running", "worker", "do stuff")
    rec0 = bg_store.get("p2-mark-running")
    assert rec0["status"] == "pending"
    assert rec0["started_at"] is None

    before = time.time()
    bg_store.mark_running("p2-mark-running")
    after = time.time()

    rec1 = bg_store.get("p2-mark-running")
    assert rec1["status"] == "running"
    started = rec1["started_at"]
    assert started is not None
    assert before <= started <= after


def test_mark_bg_task_running_does_not_revive_cancelled_task(bg_store):
    bg_store.register("p2-cancelled-stays-cancelled", "worker", "do stuff")
    bg_store.update("p2-cancelled-stays-cancelled", status="cancelled")

    started = bg_store.mark_running("p2-cancelled-stays-cancelled")

    assert started is False
    rec = bg_store.get("p2-cancelled-stays-cancelled")
    assert rec["status"] == "cancelled"
    assert rec["started_at"] is None


def test_update_bg_task_with_none_started_at_does_not_crash(bg_store):
    """update must not crash when started_at is None (pending → failed)."""
    bg_store.register("p2-none-started", "worker", "do stuff")
    assert bg_store.get("p2-none-started")["started_at"] is None
    bg_store.update("p2-none-started", status="failed", error="immediate failure")


# ---------------------------------------------------------------------------
# Cancellation token registry + cancel
# ---------------------------------------------------------------------------

def test_cancel_pending_agent_sets_cancelled(bg_store):
    bg_store.register("p3-pending-cancel", "worker", "do stuff")
    bg_store.register_token("p3-pending-cancel", CancellationToken())

    result = bg_store.cancel("p3-pending-cancel")

    assert bg_store.get("p3-pending-cancel")["status"] == "cancelled"
    assert "cancelled before it started" in result
    assert bg_store.get_token("p3-pending-cancel") is None


def test_cancel_running_agent_signals_token(bg_store):
    bg_store.register("p3-running-cancel", "worker", "do stuff")
    bg_store.mark_running("p3-running-cancel")
    token = CancellationToken()
    bg_store.register_token("p3-running-cancel", token)

    result = bg_store.cancel("p3-running-cancel")

    assert token.is_cancelled
    assert "Cancellation signal sent" in result


def test_cancel_rechecks_state_before_claiming_prestart(bg_store):
    bg_store.register("p3-pending-race", "worker", "do stuff")
    token = CancellationToken()
    bg_store.register_token("p3-pending-race", token)
    bg_store.mark_running("p3-pending-race")

    result = bg_store.cancel("p3-pending-race")

    assert "Cancellation signal sent" in result
    assert token.is_cancelled
    assert bg_store.get("p3-pending-race")["status"] == "running"


def test_cancel_completed_agent_is_noop(bg_store):
    bg_store.register("p3-completed-noop", "worker", "do stuff")
    bg_store.mark_running("p3-completed-noop")
    bg_store.update("p3-completed-noop", status="completed", result="done")

    result = bg_store.cancel("p3-completed-noop")

    assert "already completed" in result
    assert bg_store.get("p3-completed-noop")["status"] == "completed"


def test_cancel_nonexistent_agent_returns_error(bg_store):
    result = bg_store.cancel("does-not-exist")
    assert "No background agent found" in result


def test_agent_cancelled_error_not_swallowed_as_failed(bg_store):
    """Simulate the _run() exception dispatch: AgentCancelledError → 'cancelled', not 'failed'."""
    bg_store.register("p3-cancelled-dispatch", "worker", "do stuff")
    bg_store.mark_running("p3-cancelled-dispatch")

    try:
        raise AgentCancelledError("user-cancel")
    except AgentCancelledError:
        bg_store.update("p3-cancelled-dispatch", status="cancelled")
    except Exception as exc:
        bg_store.update("p3-cancelled-dispatch", status="failed", error=str(exc))

    assert bg_store.get("p3-cancelled-dispatch")["status"] == "cancelled"


def test_token_removed_from_registry_after_completion(bg_store):
    bg_store.register("p3-token-cleanup", "worker", "do stuff")
    bg_store.register_token("p3-token-cleanup", CancellationToken())

    try:
        bg_store.update("p3-token-cleanup", status="completed", result="done")
    finally:
        bg_store.unregister_token("p3-token-cleanup")

    assert bg_store.get_token("p3-token-cleanup") is None


def test_cancel_background_agent_tool_name(bg_store):
    assert CancelBackgroundAgentTool(bg_store=bg_store).name == "cancel_background_agent"


def test_cancel_background_agent_tool_delegates_to_store(bg_store):
    bg_store.register("p3-tool-delegate", "worker", "do stuff")
    bg_store.register_token("p3-tool-delegate", CancellationToken())

    tool = CancelBackgroundAgentTool(bg_store=bg_store)
    result = tool.execute(agent_id="p3-tool-delegate")
    assert "cancelled before it started" in result


def test_delete_completed_agent_removes_task(bg_store):
    bg_store.register("p3-delete-completed", "worker", "do stuff")
    bg_store.update("p3-delete-completed", status="completed", result="done")

    result = bg_store.delete("p3-delete-completed")

    assert "Deleted background agent" in result
    assert bg_store.get("p3-delete-completed") is None


def test_delete_running_agent_is_rejected(bg_store):
    bg_store.register("p3-delete-running", "worker", "do stuff")
    bg_store.mark_running("p3-delete-running")

    result = bg_store.delete("p3-delete-running")

    assert "cannot be deleted" in result
    assert bg_store.get("p3-delete-running")["status"] == "running"


def test_delete_nonexistent_agent_returns_error(bg_store):
    result = bg_store.delete("does-not-exist")
    assert "No background agent found" in result


# ---------------------------------------------------------------------------
# CheckBackgroundAgentTool display paths
# ---------------------------------------------------------------------------

def test_check_bg_agent_tool_cancelled_status(bg_store):
    bg_store.register("p4-check-cancelled", "worker", "do stuff")
    bg_store.update("p4-check-cancelled", status="cancelled")

    tool = CheckBackgroundAgentTool(bg_store=bg_store)
    result = tool.execute(agent_id="p4-check-cancelled")
    assert "was cancelled" in result


def test_check_bg_agent_tool_list_shows_cancelled(bg_store):
    bg_store.register("p4-list-cancelled", "worker", "do stuff")
    bg_store.update("p4-list-cancelled", status="cancelled")

    tool = CheckBackgroundAgentTool(bg_store=bg_store)
    result = tool.execute(agent_id="")
    assert "cancelled" in result


def test_check_bg_agent_tool_list_does_not_label_cancelled_before_start_as_queued(bg_store):
    bg_store.register("p4-cancelled-before-start", "worker", "do stuff")
    bg_store.update("p4-cancelled-before-start", status="cancelled")

    tool = CheckBackgroundAgentTool(bg_store=bg_store)
    result = tool.execute(agent_id="")
    assert "cancelled before start" in result
    assert "queued" not in result


# ---------------------------------------------------------------------------
# Notification queue coverage for terminal states
# ---------------------------------------------------------------------------

def test_cancelled_task_pushes_notification(bg_store):
    bg_store.register("p6-cancel-notify", "worker", "do stuff")
    bg_store.drain_notifications()

    bg_store.update("p6-cancel-notify", status="cancelled")

    notes = bg_store.drain_notifications()
    assert len(notes) == 1
    assert "cancelled" in notes[0]
    assert "p6-cancel-notify" in notes[0]


def test_failed_task_pushes_notification(bg_store):
    bg_store.register("p6-fail-notify", "worker", "do stuff")
    bg_store.drain_notifications()

    bg_store.update("p6-fail-notify", status="failed", error="something broke")

    notes = bg_store.drain_notifications()
    assert len(notes) == 1
    assert "failed" in notes[0]


def test_completed_without_result_does_not_push_notification(bg_store):
    """Existing guard (result is not None) must be preserved."""
    bg_store.register("p6-complete-no-result", "worker", "do stuff")
    bg_store.drain_notifications()

    bg_store.update("p6-complete-no-result", status="completed", result=None)

    notes = bg_store.drain_notifications()
    assert notes == []


# ---------------------------------------------------------------------------
# Persistence: flush serialization
# ---------------------------------------------------------------------------

def test_flush_to_disk_serializes_writes(tmp_path, monkeypatch):
    """Two concurrent flushes must not interleave: the second waits on
    ``_store_lock`` until the first finishes."""
    store = BackgroundTaskStore(persistence_dir=tmp_path)

    entered_first = False
    release_first = False
    second_entered = False
    snapshots = []

    def fake_save(path, snapshot):
        nonlocal entered_first, release_first, second_entered
        snapshots.append(dict(snapshot))
        if not entered_first:
            entered_first = True
            deadline = time.time() + 1
            while not release_first and time.time() < deadline:
                time.sleep(0.01)
        else:
            second_entered = True

    store.register("flush-1", "worker", "first")  # initial flush goes through real save
    monkeypatch.setattr("agentao.agents.store.save_bg_task_store", fake_save)

    t1 = threading.Thread(target=store._flush_to_disk)
    t1.start()

    deadline = time.time() + 1
    while not entered_first and time.time() < deadline:
        time.sleep(0.01)

    with store._lock:
        store._tasks["flush-1"]["status"] = "completed"

    t2 = threading.Thread(target=store._flush_to_disk)
    t2.start()

    time.sleep(0.05)
    assert second_entered is False

    release_first = True
    t1.join()
    t2.join()

    assert snapshots[0]["flush-1"]["status"] == "pending"
    assert snapshots[1]["flush-1"]["status"] == "completed"
