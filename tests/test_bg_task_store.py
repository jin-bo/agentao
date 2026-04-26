"""Tests for the background-task persistence layer + BackgroundTaskStore.recover.

Covers:
- ``agents/store.py`` save/load round-trips, version handling, atomic writes
- ``BackgroundTaskStore.recover`` reclassifying interrupted tasks
- Each ``Agentao`` instance recovering its own store at construction time
- Two stores rooted at different project dirs do not see each other's tasks
"""

import json
import time
from pathlib import Path

import pytest

from agentao.agent import Agentao
from agentao.agents.bg_store import (
    BackgroundTaskStore,
    _reset_recovery_guard_for_tests,
)
from agentao.agents.store import (
    _STORE_VERSION,
    load_bg_task_store,
    save_bg_task_store,
)


@pytest.fixture(autouse=True)
def _reset_recovery_guard():
    """Per-process recovery guard is module-level state; clear it between tests."""
    _reset_recovery_guard_for_tests()
    yield
    _reset_recovery_guard_for_tests()


# ---------------------------------------------------------------------------
# load / save roundtrip
# ---------------------------------------------------------------------------

def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "bg.json"
    tasks = {
        "abc123": {
            "agent_name": "worker",
            "status": "completed",
            "task": "do something",
            "result": "done",
            "error": None,
            "created_at": 1000.0,
            "started_at": 1001.0,
            "finished_at": 1005.0,
            "turns": 3,
            "tool_calls": 1,
            "tokens": 500,
            "duration_ms": 4000,
        }
    }
    save_bg_task_store(path, tasks)
    assert load_bg_task_store(path) == tasks


def test_load_returns_empty_if_missing(tmp_path):
    path = tmp_path / "nonexistent.json"
    assert load_bg_task_store(path) == {}


def test_load_returns_empty_if_corrupt_json(tmp_path):
    path = tmp_path / "bg.json"
    path.write_text("not valid json {{", encoding="utf-8")
    assert load_bg_task_store(path) == {}


def test_load_returns_empty_if_wrong_version(tmp_path):
    path = tmp_path / "bg.json"
    path.write_text(
        json.dumps({"version": 99, "tasks": {"x": {"status": "completed"}}}),
        encoding="utf-8",
    )
    assert load_bg_task_store(path) == {}


def test_load_returns_empty_if_tasks_not_dict(tmp_path):
    path = tmp_path / "bg.json"
    path.write_text(
        json.dumps({"version": _STORE_VERSION, "tasks": ["list", "not", "dict"]}),
        encoding="utf-8",
    )
    assert load_bg_task_store(path) == {}


# ---------------------------------------------------------------------------
# atomic write safety
# ---------------------------------------------------------------------------

def test_atomic_write_does_not_corrupt_existing_file(tmp_path):
    path = tmp_path / "bg.json"
    tasks_v1 = {"t1": {"status": "completed"}}
    tasks_v2 = {"t1": {"status": "completed"}, "t2": {"status": "failed"}}

    save_bg_task_store(path, tasks_v1)
    assert load_bg_task_store(path) == tasks_v1

    save_bg_task_store(path, tasks_v2)
    assert load_bg_task_store(path) == tasks_v2

    tmp_files = list(path.parent.glob("*.tmp"))
    assert tmp_files == [], f"leftover tmp files: {tmp_files}"


def test_saved_file_is_valid_json(tmp_path):
    path = tmp_path / "bg.json"
    tasks = {"t1": {"status": "pending", "created_at": 1000.0}}
    save_bg_task_store(path, tasks)
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed["version"] == _STORE_VERSION
    assert parsed["tasks"] == tasks


# ---------------------------------------------------------------------------
# BackgroundTaskStore.recover
# ---------------------------------------------------------------------------

def _make_task(status: str) -> dict:
    return {
        "agent_name": "worker",
        "status": status,
        "task": "do stuff",
        "result": None,
        "error": None,
        "created_at": time.time(),
        "started_at": time.time() if status != "pending" else None,
        "finished_at": time.time() if status in ("completed", "failed", "cancelled") else None,
        "turns": 0, "tool_calls": 0, "tokens": 0, "duration_ms": 0,
    }


def _seed_persisted_store(project_root: Path, tasks: dict) -> None:
    """Write tasks to the canonical store path under ``project_root``."""
    save_bg_task_store(project_root / ".agentao" / "background_tasks.json", tasks)


def test_recover_marks_pending_as_failed(tmp_path):
    _seed_persisted_store(tmp_path, {"p1": _make_task("pending")})
    store = BackgroundTaskStore(persistence_dir=tmp_path)

    assert store.recover() is True

    rec = store.get("p1")
    assert rec["status"] == "failed"
    assert "process exited" in rec["error"]
    assert rec["finished_at"] is not None


def test_recover_marks_running_as_failed(tmp_path):
    _seed_persisted_store(tmp_path, {"r1": _make_task("running")})
    store = BackgroundTaskStore(persistence_dir=tmp_path)

    store.recover()

    rec = store.get("r1")
    assert rec["status"] == "failed"
    assert "process exited" in rec["error"]


def test_recover_leaves_completed_untouched(tmp_path):
    _seed_persisted_store(tmp_path, {"c1": _make_task("completed")})
    store = BackgroundTaskStore(persistence_dir=tmp_path)

    store.recover()

    assert store.get("c1")["status"] == "completed"


def test_recover_leaves_failed_untouched(tmp_path):
    rec = _make_task("failed")
    rec["error"] = "original error"
    _seed_persisted_store(tmp_path, {"f1": rec})
    store = BackgroundTaskStore(persistence_dir=tmp_path)

    store.recover()

    out = store.get("f1")
    assert out["status"] == "failed"
    assert out["error"] == "original error"


def test_recover_leaves_cancelled_untouched(tmp_path):
    _seed_persisted_store(tmp_path, {"x1": _make_task("cancelled")})
    store = BackgroundTaskStore(persistence_dir=tmp_path)

    store.recover()

    assert store.get("x1")["status"] == "cancelled"


def test_recover_writes_corrected_state_back_to_disk(tmp_path):
    _seed_persisted_store(tmp_path, {
        "p1": _make_task("pending"),
        "c1": _make_task("completed"),
    })
    store = BackgroundTaskStore(persistence_dir=tmp_path)

    store.recover()

    on_disk = load_bg_task_store(tmp_path / ".agentao" / "background_tasks.json")
    assert on_disk["p1"]["status"] == "failed"
    assert on_disk["c1"]["status"] == "completed"


def test_recover_is_noop_when_no_file(tmp_path):
    store = BackgroundTaskStore(persistence_dir=tmp_path)
    assert store.recover() is False
    assert store.list() == []


def test_recover_is_noop_when_persistence_disabled():
    store = BackgroundTaskStore(persistence_dir=None)
    assert store.recover() is False


def test_delete_updates_persisted_store(tmp_path):
    store = BackgroundTaskStore(persistence_dir=tmp_path)
    store.register("delete-me", "worker", "do stuff")
    store.update("delete-me", status="completed", result="done")

    msg = store.delete("delete-me")

    assert "Deleted background agent" in msg
    on_disk = load_bg_task_store(tmp_path / ".agentao" / "background_tasks.json")
    assert "delete-me" not in on_disk


# ---------------------------------------------------------------------------
# Multi-store isolation: two project roots cannot see each other's tasks
# ---------------------------------------------------------------------------

def test_two_stores_with_different_dirs_have_independent_files(tmp_path):
    a_root = tmp_path / "project-a"
    b_root = tmp_path / "project-b"
    a_root.mkdir()
    b_root.mkdir()

    a = BackgroundTaskStore(persistence_dir=a_root)
    b = BackgroundTaskStore(persistence_dir=b_root)

    a.register("only-in-a", "worker", "task")
    b.register("only-in-b", "worker", "task")

    a_disk = load_bg_task_store(a_root / ".agentao" / "background_tasks.json")
    b_disk = load_bg_task_store(b_root / ".agentao" / "background_tasks.json")

    assert "only-in-a" in a_disk and "only-in-b" not in a_disk
    assert "only-in-b" in b_disk and "only-in-a" not in b_disk


# ---------------------------------------------------------------------------
# Agentao construction wires up the store and runs recovery
# ---------------------------------------------------------------------------

def test_agentao_init_recovers_persisted_interrupted_tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-dummy-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.4")

    _seed_persisted_store(tmp_path, {
        "p1": _make_task("pending"),
        "r1": _make_task("running"),
    })

    agent = Agentao(working_directory=tmp_path)

    p1 = agent.bg_store.get("p1")
    r1 = agent.bg_store.get("r1")
    assert p1["status"] == "failed"
    assert r1["status"] == "failed"
    assert p1["error"] == "process exited before task finished"
    assert r1["error"] == "process exited before task finished"


def test_second_recover_on_same_path_does_not_reclassify_live_tasks(tmp_path):
    """A second store anchored to the same persistence file must not reclassify
    pending/running tasks the first store wrote — those threads may still be alive."""
    # First store: simulate Agentao init order (recover then register live task).
    first = BackgroundTaskStore(persistence_dir=tmp_path)
    first.recover()
    first.register("live-task", "worker", "long-running")
    first.mark_running("live-task")
    assert first.get("live-task")["status"] == "running"

    # Second store on the same path. recover() must skip orphan reclassification.
    second = BackgroundTaskStore(persistence_dir=tmp_path)
    assert second.recover() is False

    # Disk state for the live task is still "running", not "failed".
    on_disk = load_bg_task_store(tmp_path / ".agentao" / "background_tasks.json")
    assert on_disk["live-task"]["status"] == "running"
    assert on_disk["live-task"].get("error") is None

    # The first store's in-memory view is also unchanged.
    assert first.get("live-task")["status"] == "running"


def test_second_store_flush_does_not_drop_first_stores_tasks(tmp_path):
    """A second store on the same persistence path must preserve the first
    store's persisted tasks when it flushes after registering its own."""
    first = BackgroundTaskStore(persistence_dir=tmp_path)
    first.recover()
    first.register("first-task", "worker", "task A")
    first.mark_running("first-task")

    # Second store on the same path: recover loads the snapshot but skips
    # orphan reclassification.
    second = BackgroundTaskStore(persistence_dir=tmp_path)
    assert second.recover() is False
    assert second.get("first-task") is not None
    assert second.get("first-task")["status"] == "running"

    # Now the second store registers its own task and flushes.
    second.register("second-task", "worker", "task B")

    on_disk = load_bg_task_store(tmp_path / ".agentao" / "background_tasks.json")
    # First store's task must still be on disk after second's flush.
    assert "first-task" in on_disk
    assert on_disk["first-task"]["status"] == "running"
    assert on_disk["first-task"].get("error") is None
    assert "second-task" in on_disk


def test_two_agentao_instances_each_own_their_store(tmp_path, monkeypatch):
    """Two Agentao instances pointed at different project roots must have
    independent stores."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-dummy-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.4")

    a_root = tmp_path / "project-a"
    b_root = tmp_path / "project-b"
    a_root.mkdir()
    b_root.mkdir()

    agent_a = Agentao(working_directory=a_root)
    agent_b = Agentao(working_directory=b_root)

    assert agent_a.bg_store is not agent_b.bg_store

    agent_a.bg_store.register("a-task", "worker", "do A")
    assert agent_a.bg_store.get("a-task") is not None
    assert agent_b.bg_store.get("a-task") is None


def test_register_writes_store_file(tmp_path):
    """Registering a task creates the store file immediately."""
    store = BackgroundTaskStore(persistence_dir=tmp_path)
    store.register("flush-test-001", "test-agent", "do stuff")

    store_path = tmp_path / ".agentao" / "background_tasks.json"
    assert store_path.exists()

    data = json.loads(store_path.read_text(encoding="utf-8"))
    assert data["version"] == _STORE_VERSION
    assert "flush-test-001" in data["tasks"]
    assert data["tasks"]["flush-test-001"]["status"] == "pending"


# ---------------------------------------------------------------------------
# Cross-store flush merge: two concurrently-constructed stores must not
# overwrite each other's tasks on disk.
# ---------------------------------------------------------------------------

def test_concurrent_stores_register_independently(tmp_path):
    """Regression: two stores anchored to the same path are constructed
    before either registers. The second store's flush must not drop the
    first store's task. Previously _flush_to_disk wrote only its own
    in-memory _tasks dict, so the later flush replaced the file."""
    a = BackgroundTaskStore(persistence_dir=tmp_path)
    b = BackgroundTaskStore(persistence_dir=tmp_path)
    a.recover()  # acquires the per-process recovery guard
    b.recover()  # guarded path: loads (empty) without reclassification

    a.register("a-task", "worker-a", "task A")
    b.register("b-task", "worker-b", "task B")

    on_disk = load_bg_task_store(tmp_path / ".agentao" / "background_tasks.json")
    assert "a-task" in on_disk, "store B's flush dropped store A's task"
    assert "b-task" in on_disk

    # Subsequent state changes on either side preserve both records.
    a.mark_running("a-task")
    on_disk = load_bg_task_store(tmp_path / ".agentao" / "background_tasks.json")
    assert on_disk["a-task"]["status"] == "running"
    assert on_disk["b-task"]["status"] == "pending"


def test_concurrent_store_delete_does_not_drop_sibling_task(tmp_path):
    """Deleting a task on one store leaves the sibling store's task on disk."""
    a = BackgroundTaskStore(persistence_dir=tmp_path)
    b = BackgroundTaskStore(persistence_dir=tmp_path)
    a.recover()
    b.recover()

    a.register("a-task", "worker-a", "task A")
    a.update("a-task", status="completed", result="done")
    b.register("b-task", "worker-b", "task B")

    a.delete("a-task")

    on_disk = load_bg_task_store(tmp_path / ".agentao" / "background_tasks.json")
    assert "a-task" not in on_disk
    assert "b-task" in on_disk


def test_cancel_pending_sibling_owned_task_refuses_and_does_not_persist(tmp_path):
    """A second store on the same path must not silently 'cancel' a pending
    task owned by a sibling store: the change cannot be persisted without
    ownership, so the owning store would still start the task."""
    first = BackgroundTaskStore(persistence_dir=tmp_path)
    first.recover()
    first.register("sibling-pending", "worker", "task")
    assert first.get("sibling-pending")["status"] == "pending"

    second = BackgroundTaskStore(persistence_dir=tmp_path)
    second.recover()
    assert second.get("sibling-pending")["status"] == "pending"

    msg = second.cancel("sibling-pending")
    assert "owned by another Agentao instance" in msg

    on_disk = load_bg_task_store(tmp_path / ".agentao" / "background_tasks.json")
    assert on_disk["sibling-pending"]["status"] == "pending"
    assert first.get("sibling-pending")["status"] == "pending"


def test_cancel_running_sibling_owned_task_without_token_reports_failure(tmp_path):
    """Cancelling a running task whose cancellation token lives in another
    store's process memory must report failure, not falsely claim a signal
    was sent."""
    first = BackgroundTaskStore(persistence_dir=tmp_path)
    first.recover()
    first.register("sibling-running", "worker", "task")
    first.mark_running("sibling-running")

    second = BackgroundTaskStore(persistence_dir=tmp_path)
    second.recover()
    assert second.get("sibling-running")["status"] == "running"

    msg = second.cancel("sibling-running")
    assert "running in another Agentao instance" in msg

    on_disk = load_bg_task_store(tmp_path / ".agentao" / "background_tasks.json")
    assert on_disk["sibling-running"]["status"] == "running"


def test_sibling_owned_task_status_refreshes_on_read(tmp_path):
    """A second store must not serve a stale 'running' snapshot for a
    sibling-owned task after the owning store has marked it completed.
    Otherwise /agent status and the live dashboard loop indefinitely on
    the second instance until restart."""
    first = BackgroundTaskStore(persistence_dir=tmp_path)
    first.recover()
    first.register("sibling-task", "worker", "long task")
    first.mark_running("sibling-task")

    second = BackgroundTaskStore(persistence_dir=tmp_path)
    second.recover()
    assert second.get("sibling-task")["status"] == "running"
    assert second.list()[0]["status"] == "running"

    # Owning store finishes the task.
    first.update("sibling-task", status="completed", result="done")

    # The sibling-side reads must reflect the new disk state.
    rec = second.get("sibling-task")
    assert rec["status"] == "completed"
    assert rec["result"] == "done"

    listed = next(t for t in second.list() if t["id"] == "sibling-task")
    assert listed["status"] == "completed"


def test_sibling_store_picks_up_newly_registered_task_on_read(tmp_path):
    """A second store loads on-disk tasks lazily on read. Tasks registered
    by the first store after the second store recovered must surface the
    next time the second store calls list()/get()."""
    first = BackgroundTaskStore(persistence_dir=tmp_path)
    first.recover()

    second = BackgroundTaskStore(persistence_dir=tmp_path)
    second.recover()
    assert second.list() == []

    first.register("late-task", "worker", "registered after sibling recover")

    rec = second.get("late-task")
    assert rec is not None
    assert rec["status"] == "pending"
    assert any(t["id"] == "late-task" for t in second.list())


def test_sibling_store_drops_deleted_sibling_task_on_refresh(tmp_path):
    """When the owning store deletes a finished task, the sibling store's
    in-memory copy must also disappear on the next read."""
    first = BackgroundTaskStore(persistence_dir=tmp_path)
    first.recover()
    first.register("doomed", "worker", "task")
    first.update("doomed", status="completed", result="ok")

    second = BackgroundTaskStore(persistence_dir=tmp_path)
    second.recover()
    assert second.get("doomed") is not None

    first.delete("doomed")

    assert second.get("doomed") is None
    assert all(t["id"] != "doomed" for t in second.list())


def test_owned_task_state_not_clobbered_by_refresh(tmp_path):
    """Refreshing sibling tasks must not overwrite the owning store's
    in-memory view of its own task — the in-memory copy is authoritative
    while a flush merges changes."""
    first = BackgroundTaskStore(persistence_dir=tmp_path)
    first.recover()
    first.register("mine", "worker", "task")
    first.mark_running("mine")

    # Simulate a stale on-disk state for the same task (e.g. from a prior
    # write that hasn't yet been re-merged). The owning store must trust
    # its own in-memory state, not the disk copy.
    save_bg_task_store(
        tmp_path / ".agentao" / "background_tasks.json",
        {"mine": {**_make_task("pending"), "agent_name": "worker"}},
    )

    rec = first.get("mine")
    assert rec["status"] == "running"


def test_orphan_reclassification_after_concurrent_sibling_register(tmp_path):
    """First store reclassifies orphans on recover; later registers from
    a sibling store on the same path coexist with reclassified orphans."""
    _seed_persisted_store(tmp_path, {"orphan": _make_task("running")})

    first = BackgroundTaskStore(persistence_dir=tmp_path)
    assert first.recover() is True  # reclassifies orphan → failed

    second = BackgroundTaskStore(persistence_dir=tmp_path)
    assert second.recover() is False  # guarded
    second.register("sibling", "worker", "concurrent")

    on_disk = load_bg_task_store(tmp_path / ".agentao" / "background_tasks.json")
    assert on_disk["orphan"]["status"] == "failed"
    assert on_disk["orphan"]["error"] == "process exited before task finished"
    assert "sibling" in on_disk
    assert on_disk["sibling"]["status"] == "pending"


def test_delete_refreshes_sibling_state_before_status_check(tmp_path):
    """Regression: deleting a sibling-owned task must refresh from disk so
    a stale 'running' snapshot doesn't make the task un-deletable forever
    after the owning store has flushed completion."""
    first = BackgroundTaskStore(persistence_dir=tmp_path)
    first.recover()
    first.register("shared-task", "worker", "task")
    first.mark_running("shared-task")

    second = BackgroundTaskStore(persistence_dir=tmp_path)
    second.recover()
    # Second store's snapshot is "running" at this point.
    assert second.get("shared-task")["status"] == "running"

    # Owning store completes the task (flushes to disk).
    first.update("shared-task", status="completed", result="done")

    # Now invoke delete() *without* any prior get()/list() that would have
    # refreshed sibling state. Pre-fix this would refuse with "still running".
    msg = second.delete("shared-task")
    assert "Deleted background agent" in msg

    # The task is gone from disk.
    on_disk = load_bg_task_store(tmp_path / ".agentao" / "background_tasks.json")
    assert "shared-task" not in on_disk


def test_owning_store_does_not_resurrect_sibling_deleted_task(tmp_path):
    """Codex review regression: when a sibling deletes one of our finished
    tasks from disk, our next flush (e.g. triggered by registering an
    unrelated task) must not write the deleted record back."""
    a = BackgroundTaskStore(persistence_dir=tmp_path)
    a.recover()
    a.register("doomed", "worker", "task")
    a.update("doomed", status="completed", result="done")

    b = BackgroundTaskStore(persistence_dir=tmp_path)
    b.recover()
    b.delete("doomed")

    on_disk = load_bg_task_store(tmp_path / ".agentao" / "background_tasks.json")
    assert "doomed" not in on_disk

    # A's next flush (triggered by an unrelated registration) must not
    # resurrect the deleted record.
    a.register("fresh", "worker", "later")

    on_disk = load_bg_task_store(tmp_path / ".agentao" / "background_tasks.json")
    assert "doomed" not in on_disk
    assert "fresh" in on_disk


def test_persistence_dir_provider_follows_cwd_lazily(tmp_path):
    """Default Agentao() sessions construct the bg_store with a provider so
    that a process chdir after construction retargets persistence to the
    new project. The provider is consulted on every flush/read, so file
    and shell tools (which follow lazy cwd) and the bg-task history stay
    pointing at the same project after chdir."""
    project_a = tmp_path / "project_a"
    project_b = tmp_path / "project_b"
    project_a.mkdir()
    project_b.mkdir()

    cwd_holder = {"cwd": project_a}
    store = BackgroundTaskStore(
        persistence_dir_provider=lambda: cwd_holder["cwd"],
    )
    store.register("task-a", "worker", "in project A")
    assert (project_a / ".agentao" / "background_tasks.json").exists()
    assert not (project_b / ".agentao" / "background_tasks.json").exists()

    # Simulate chdir into project_b after construction. Subsequent
    # operations must flush under project_b — pre-fix they would still
    # write to project_a's file frozen at construction time.
    cwd_holder["cwd"] = project_b
    store.register("task-b", "worker", "in project B")

    assert (project_b / ".agentao" / "background_tasks.json").exists()
    b_state = load_bg_task_store(project_b / ".agentao" / "background_tasks.json")
    assert "task-b" in b_state
    # Project A's task must not have leaked into project B's snapshot
    # (Codex review regression).
    assert "task-a" not in b_state


def test_cwd_change_does_not_leak_state_into_new_project(tmp_path):
    """Codex review regression: when a provider-backed store sees the
    cwd change to a new project, in-memory ``_tasks`` and ``_owned_ids``
    from the old project must not be flushed into the new project's
    ``.agentao/background_tasks.json``, and ``list()`` in the new cwd
    must not surface old-project tasks. Equivalently: after rebind,
    only the new project's on-disk snapshot drives state."""
    project_a = tmp_path / "project_a"
    project_b = tmp_path / "project_b"
    project_a.mkdir()
    project_b.mkdir()

    cwd_holder = {"cwd": project_a}
    store = BackgroundTaskStore(
        persistence_dir_provider=lambda: cwd_holder["cwd"],
    )

    # Populate project A.
    store.register("task-a", "worker", "in project A")
    store.update("task-a", status="completed", result="done in A")
    a_state_before = load_bg_task_store(
        project_a / ".agentao" / "background_tasks.json"
    )
    assert "task-a" in a_state_before

    # Process chdir to project B. No new operations have happened yet,
    # so nothing has rebound.
    cwd_holder["cwd"] = project_b

    # /agent status (i.e. list()) in the new cwd must not surface
    # project A's task.
    listed = store.list()
    assert listed == []

    # A flush in the new cwd (any state mutation triggers one) must not
    # write project A's task into project B's file.
    store.register("task-b", "worker", "in project B")
    b_state = load_bg_task_store(
        project_b / ".agentao" / "background_tasks.json"
    )
    assert "task-a" not in b_state
    assert "task-b" in b_state

    # Project A's file must remain intact — the old project's history
    # is preserved exactly as it was, not silently overwritten.
    a_state_after = load_bg_task_store(
        project_a / ".agentao" / "background_tasks.json"
    )
    assert a_state_after == a_state_before

    # Returning to project A must surface its tasks again.
    cwd_holder["cwd"] = project_a
    relisted = {rec["id"]: rec for rec in store.list()}
    assert "task-a" in relisted
    assert "task-b" not in relisted


def test_persistence_dir_and_provider_are_mutually_exclusive(tmp_path):
    with pytest.raises(ValueError):
        BackgroundTaskStore(
            persistence_dir=tmp_path,
            persistence_dir_provider=lambda: tmp_path,
        )


def test_in_flight_task_update_after_rebind_lands_in_original_project(tmp_path):
    """Codex review regression: an in-flight background task whose owner
    Agentao session sees a process cwd change must still have its
    eventual mark_running()/update() persist to the *original* project's
    file and push a completion notification. Pre-fix, the rebind cleared
    ``_tasks``/``_owned_ids`` and the worker thread's update silently
    no-op'd, leaving the original row stuck pending and dropping the
    user-visible result."""
    project_a = tmp_path / "project_a"
    project_b = tmp_path / "project_b"
    project_a.mkdir()
    project_b.mkdir()

    cwd_holder = {"cwd": project_a}
    store = BackgroundTaskStore(
        persistence_dir_provider=lambda: cwd_holder["cwd"],
    )

    # Register task in project_a and mark it running, mimicking the
    # state of a worker thread mid-execution.
    store.register("inflight", "worker", "long-running task")
    store.mark_running("inflight")
    a_path = project_a / ".agentao" / "background_tasks.json"
    assert load_bg_task_store(a_path)["inflight"]["status"] == "running"

    # The user navigates the agent to a different project. No new
    # operations have triggered a rebind yet.
    cwd_holder["cwd"] = project_b

    # The worker thread (still alive) finishes and reports completion.
    # This goes through _check_persistence_rebind() → must NOT lose the
    # task's record.
    store.update("inflight", status="completed", result="finished work")

    # The completion landed in project_a's file (where the task was
    # registered), not project_b's.
    a_state = load_bg_task_store(a_path)
    assert a_state["inflight"]["status"] == "completed"
    assert a_state["inflight"]["result"] == "finished work"

    # project_b must not have a stray inflight row leaking from project_a.
    b_path = project_b / ".agentao" / "background_tasks.json"
    if b_path.exists():
        assert "inflight" not in load_bg_task_store(b_path)

    # The completion notification was queued — the user can drain it
    # once they navigate back, instead of losing it silently.
    notifications = store.drain_notifications()
    assert any("inflight" in msg and "completed" in msg for msg in notifications)


def test_inflight_task_pinned_path_survives_multiple_rebinds(tmp_path):
    """A task pinned to project_a must keep its pinned path across
    multiple cwd changes (A → B → C) so its eventual completion still
    writes back to project_a."""
    project_a = tmp_path / "a"
    project_b = tmp_path / "b"
    project_c = tmp_path / "c"
    for p in (project_a, project_b, project_c):
        p.mkdir()

    cwd_holder = {"cwd": project_a}
    store = BackgroundTaskStore(
        persistence_dir_provider=lambda: cwd_holder["cwd"],
    )
    store.register("pinned", "worker", "task")
    store.mark_running("pinned")

    cwd_holder["cwd"] = project_b
    store.list()  # triggers rebind
    cwd_holder["cwd"] = project_c
    store.list()  # triggers rebind again

    # Worker thread reports done. The completion must still target
    # project_a.
    store.update("pinned", status="completed", result="ok")

    a_state = load_bg_task_store(project_a / ".agentao" / "background_tasks.json")
    assert a_state["pinned"]["status"] == "completed"
    for proj in (project_b, project_c):
        path = proj / ".agentao" / "background_tasks.json"
        if path.exists():
            assert "pinned" not in load_bg_task_store(path)


def test_inflight_task_hidden_from_other_projects_view(tmp_path):
    """While a task pinned to project_a is in-flight, navigating to
    project_b must not surface it in project_b's status. (It's still
    tracked internally so the worker thread's update can land.)"""
    project_a = tmp_path / "a"
    project_b = tmp_path / "b"
    project_a.mkdir()
    project_b.mkdir()

    cwd_holder = {"cwd": project_a}
    store = BackgroundTaskStore(
        persistence_dir_provider=lambda: cwd_holder["cwd"],
    )
    store.register("hidden", "worker", "in A")
    store.mark_running("hidden")

    cwd_holder["cwd"] = project_b
    assert store.list() == []
    assert store.get("hidden") is None

    # Worker still completes successfully — task survived internally.
    store.update("hidden", status="completed", result="done")
    assert (
        load_bg_task_store(project_a / ".agentao" / "background_tasks.json")[
            "hidden"
        ]["status"]
        == "completed"
    )
