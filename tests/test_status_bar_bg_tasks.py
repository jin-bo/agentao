"""The CLI status bar shows this conversation's background tasks, not history.

It used to render ``bg_store.list()``: every record the store had ever seen.
A task finished before ``/new`` or ``/clear`` stayed on the bar, and a fresh
CLI process showed every task from ``background_tasks.json`` — the ones cut
off by the previous exit as failed. ``/agent status`` is where history lives;
the bar reads ``_status_bar_tasks()``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentao.agents import bg_store as bg_store_mod
from agentao.agents.bg_store import BackgroundTaskStore, _reset_recovery_guard_for_tests
from agentao.cli.input_loop import get_status_toolbar


@pytest.fixture(autouse=True)
def _reset_recovery_guard():
    _reset_recovery_guard_for_tests()
    yield
    _reset_recovery_guard_for_tests()


def _ids(store: BackgroundTaskStore) -> list:
    return [t["id"] for t in store._status_bar_tasks()]


def _settled(store, agent_id, status="completed"):
    store.register(agent_id, "worker", "task")
    store.mark_running(agent_id)
    store.update(agent_id, status=status, result="done")


def _running(store, agent_id):
    store.register(agent_id, "worker", "task")
    store.mark_running(agent_id)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def test_this_conversations_tasks_show_whatever_their_status():
    store = BackgroundTaskStore()
    _settled(store, "done")
    _settled(store, "bad", status="failed")
    _running(store, "busy")
    store.register("queued", "worker", "task")
    assert _ids(store) == ["done", "bad", "busy", "queued"]


def test_a_task_finished_before_a_reset_is_hidden():
    store = BackgroundTaskStore()
    _settled(store, "old")
    store.start_new_conversation()
    assert _ids(store) == []
    assert [t["id"] for t in store.list()] == ["old"]  # history is still there


def test_a_task_still_running_across_a_reset_stays_until_it_ends():
    store = BackgroundTaskStore()
    _running(store, "busy")
    store.start_new_conversation()
    assert _ids(store) == ["busy"]
    store.update("busy", status="completed", result="done")
    assert _ids(store) == []


def test_records_reloaded_at_startup_are_hidden(tmp_path):
    before = BackgroundTaskStore(persistence_dir=tmp_path)
    before.recover()
    _settled(before, "done")
    _running(before, "cut-off")  # the process "exits" here

    _reset_recovery_guard_for_tests()
    after = BackgroundTaskStore(persistence_dir=tmp_path)
    after.recover()
    assert {t["id"]: t["status"] for t in after.list()} == {
        "done": "completed", "cut-off": "failed",
    }
    assert _ids(after) == []
    _settled(after, "new")
    assert _ids(after) == ["new"]


def test_a_sibling_stores_tasks_are_hidden(tmp_path):
    owner = BackgroundTaskStore(persistence_dir=tmp_path)
    viewer = BackgroundTaskStore(persistence_dir=tmp_path)
    _running(owner, "theirs")
    assert [t["id"] for t in viewer.list()] == ["theirs"]
    assert _ids(viewer) == []


def test_the_status_bar_view_does_not_read_the_persistence_file(tmp_path, monkeypatch):
    store = BackgroundTaskStore(persistence_dir=tmp_path)
    _running(store, "busy")

    def no_disk(_path):
        raise AssertionError("status bar read background_tasks.json")

    monkeypatch.setattr(bg_store_mod.persistence, "load_bg_task_store", no_disk)
    assert _ids(store) == ["busy"]


# ---------------------------------------------------------------------------
# Toolbar
# ---------------------------------------------------------------------------


def _cli(store):
    return SimpleNamespace(
        agent=SimpleNamespace(
            bg_store=store,
            get_current_model=lambda: "m",
            llm=SimpleNamespace(extra_body=None),
        ),
        current_provider="",
        _plan_session=SimpleNamespace(is_active=False),
        current_mode=None,
        _cached_ctx_pct=0.0,
        _acp_manager=None,
    )


def _toolbar(store) -> str:
    return get_status_toolbar(_cli(store)).value


def test_toolbar_drops_a_finished_task_after_new():
    store = BackgroundTaskStore()
    store.register("a1", "summarizer", "task")
    store.mark_running("a1")
    store.update("a1", status="completed", result="done")
    assert "summarizer" in _toolbar(store)
    store.start_new_conversation()
    assert "summarizer" not in _toolbar(store)


def test_toolbar_without_a_store_shows_no_agents():
    assert "⚙" not in _toolbar(None)
