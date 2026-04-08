"""Tests for agentao/agents/store.py — background task persistence layer."""

import json
import threading
import time
from pathlib import Path

import pytest

from agentao.agent import Agentao
from agentao.agents.tools import _delete_bg_task, _register_bg_task
from agentao.agents.store import (
    _STORE_VERSION,
    _reset_bg_task_recovery_for_tests,
    load_bg_task_store,
    recover_bg_task_store,
    save_bg_task_store,
)


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    """Run each test with its own temp working directory so no test writes to the real repo."""
    monkeypatch.chdir(tmp_path)
    _reset_bg_task_recovery_for_tests()
    yield
    _reset_bg_task_recovery_for_tests()


# ---------------------------------------------------------------------------
# load / save roundtrip
# ---------------------------------------------------------------------------

def test_save_and_load_roundtrip():
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
    save_bg_task_store(tasks)
    loaded = load_bg_task_store()
    assert loaded == tasks


def test_load_returns_empty_if_missing():
    # No file created — directory doesn't even exist
    assert load_bg_task_store() == {}


def test_load_returns_empty_if_corrupt_json():
    store_dir = Path.cwd() / ".agentao"
    store_dir.mkdir(parents=True)
    (store_dir / "background_tasks.json").write_text("not valid json {{", encoding="utf-8")
    assert load_bg_task_store() == {}


def test_load_returns_empty_if_wrong_version():
    store_dir = Path.cwd() / ".agentao"
    store_dir.mkdir(parents=True)
    (store_dir / "background_tasks.json").write_text(
        json.dumps({"version": 99, "tasks": {"x": {"status": "completed"}}}),
        encoding="utf-8",
    )
    assert load_bg_task_store() == {}


def test_load_returns_empty_if_tasks_not_dict():
    store_dir = Path.cwd() / ".agentao"
    store_dir.mkdir(parents=True)
    (store_dir / "background_tasks.json").write_text(
        json.dumps({"version": _STORE_VERSION, "tasks": ["list", "not", "dict"]}),
        encoding="utf-8",
    )
    assert load_bg_task_store() == {}


# ---------------------------------------------------------------------------
# atomic write safety
# ---------------------------------------------------------------------------

def test_atomic_write_does_not_corrupt_existing_file():
    """Original file remains intact if the new write fails partway through.

    We verify this by checking: a successful second save doesn't corrupt the first.
    (True mid-write crash testing requires process-level injection; we verify the
    temp-file pattern by confirming no .tmp files are left behind after a clean save.)
    """
    tasks_v1 = {"t1": {"status": "completed"}}
    tasks_v2 = {"t1": {"status": "completed"}, "t2": {"status": "failed"}}

    save_bg_task_store(tasks_v1)
    assert load_bg_task_store() == tasks_v1

    save_bg_task_store(tasks_v2)
    assert load_bg_task_store() == tasks_v2

    # No leftover .tmp files
    store_dir = Path.cwd() / ".agentao"
    tmp_files = list(store_dir.glob("*.tmp"))
    assert tmp_files == [], f"leftover tmp files: {tmp_files}"


def test_saved_file_is_valid_json():
    tasks = {"t1": {"status": "pending", "created_at": 1000.0}}
    save_bg_task_store(tasks)
    raw = (Path.cwd() / ".agentao" / "background_tasks.json").read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert parsed["version"] == _STORE_VERSION
    assert parsed["tasks"] == tasks


# ---------------------------------------------------------------------------
# recover_bg_task_store
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


def test_recover_marks_pending_as_failed():
    tasks = {"p1": _make_task("pending")}
    save_bg_task_store(tasks)

    bg_tasks: dict = {}
    lock = threading.Lock()
    recover_bg_task_store(bg_tasks, lock)

    assert bg_tasks["p1"]["status"] == "failed"
    assert "process exited" in bg_tasks["p1"]["error"]
    assert bg_tasks["p1"]["finished_at"] is not None


def test_recover_marks_running_as_failed():
    tasks = {"r1": _make_task("running")}
    save_bg_task_store(tasks)

    bg_tasks: dict = {}
    lock = threading.Lock()
    recover_bg_task_store(bg_tasks, lock)

    assert bg_tasks["r1"]["status"] == "failed"
    assert "process exited" in bg_tasks["r1"]["error"]


def test_recover_leaves_completed_untouched():
    tasks = {"c1": _make_task("completed")}
    save_bg_task_store(tasks)

    bg_tasks: dict = {}
    lock = threading.Lock()
    recover_bg_task_store(bg_tasks, lock)

    assert bg_tasks["c1"]["status"] == "completed"


def test_recover_leaves_failed_untouched():
    rec = _make_task("failed")
    rec["error"] = "original error"
    tasks = {"f1": rec}
    save_bg_task_store(tasks)

    bg_tasks: dict = {}
    lock = threading.Lock()
    recover_bg_task_store(bg_tasks, lock)

    assert bg_tasks["f1"]["status"] == "failed"
    assert bg_tasks["f1"]["error"] == "original error"


def test_recover_leaves_cancelled_untouched():
    tasks = {"x1": _make_task("cancelled")}
    save_bg_task_store(tasks)

    bg_tasks: dict = {}
    lock = threading.Lock()
    recover_bg_task_store(bg_tasks, lock)

    assert bg_tasks["x1"]["status"] == "cancelled"


def test_recover_writes_corrected_state_back_to_disk():
    tasks = {"p1": _make_task("pending"), "c1": _make_task("completed")}
    save_bg_task_store(tasks)

    bg_tasks: dict = {}
    lock = threading.Lock()
    recover_bg_task_store(bg_tasks, lock)

    # File should now reflect the corrected state
    on_disk = load_bg_task_store()
    assert on_disk["p1"]["status"] == "failed"
    assert on_disk["c1"]["status"] == "completed"


def test_recover_is_noop_when_no_file():
    bg_tasks: dict = {}
    lock = threading.Lock()
    recover_bg_task_store(bg_tasks, lock)
    assert bg_tasks == {}


def test_delete_bg_task_updates_persisted_store():
    agent_id = "delete-me"
    _register_bg_task(agent_id, "worker", "do stuff")
    tasks = load_bg_task_store()
    tasks[agent_id]["status"] = "completed"
    tasks[agent_id]["result"] = "done"
    tasks[agent_id]["finished_at"] = time.time()
    save_bg_task_store(tasks)

    from agentao.agents.tools import _bg_lock, _bg_tasks
    with _bg_lock:
        _bg_tasks[agent_id]["status"] = "completed"
        _bg_tasks[agent_id]["result"] = "done"
        _bg_tasks[agent_id]["finished_at"] = time.time()

    msg = _delete_bg_task(agent_id)

    assert "Deleted background agent" in msg
    assert agent_id not in load_bg_task_store()


def test_agentao_init_recovers_persisted_interrupted_tasks():
    from agentao.agents.tools import _bg_lock, _bg_tasks

    tasks = {"p1": _make_task("pending"), "r1": _make_task("running")}
    save_bg_task_store(tasks)
    _bg_tasks.clear()

    Agentao()

    with _bg_lock:
        assert _bg_tasks["p1"]["status"] == "failed"
        assert _bg_tasks["r1"]["status"] == "failed"
        assert "process exited before task finished" == _bg_tasks["p1"]["error"]
        assert "process exited before task finished" == _bg_tasks["r1"]["error"]


def test_agentao_init_only_recovers_once_per_process():
    from agentao.agents.tools import _bg_lock, _bg_tasks

    save_bg_task_store({"p1": _make_task("pending")})
    _bg_tasks.clear()

    Agentao()

    live = _make_task("pending")
    with _bg_lock:
        _bg_tasks.clear()
        _bg_tasks["live"] = live
    save_bg_task_store({"live": dict(live)})

    Agentao()

    with _bg_lock:
        assert _bg_tasks["live"]["status"] == "pending"
        assert _bg_tasks["live"]["error"] is None


# ---------------------------------------------------------------------------
# Integration: _flush_to_disk called on register
# ---------------------------------------------------------------------------

def test_flush_to_disk_called_on_register():
    """Registering a task creates the store file immediately."""
    from agentao.agents.tools import _register_bg_task, _bg_tasks, _bg_lock

    # Clean module state for this test
    with _bg_lock:
        _bg_tasks.clear()

    agent_id = "flush-test-001"
    _register_bg_task(agent_id, "test-agent", "do stuff")

    store_path = Path.cwd() / ".agentao" / "background_tasks.json"
    assert store_path.exists(), "store file was not created"

    data = json.loads(store_path.read_text(encoding="utf-8"))
    assert data["version"] == _STORE_VERSION
    assert agent_id in data["tasks"]
    assert data["tasks"][agent_id]["status"] == "pending"

    # Cleanup
    with _bg_lock:
        _bg_tasks.pop(agent_id, None)
