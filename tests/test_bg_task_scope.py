"""Which background tasks each view shows, and how many records are kept.

It used to render ``bg_store.list()``: every record the store had ever seen.
A task finished before ``/new`` or ``/clear`` stayed on the bar, and a fresh
CLI process showed every task from ``background_tasks.json`` — the ones cut
off by the previous exit as failed. ``/agent status`` is where history lives;
the bar reads ``list_current()``.
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
    return [t["id"] for t in store.list_current()]


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


# ---------------------------------------------------------------------------
# The model's listing
# ---------------------------------------------------------------------------


def test_model_listing_leaves_out_records_from_an_earlier_run(tmp_path):
    from agentao.agents.tools._bg_tools import CheckBackgroundAgentTool

    before = BackgroundTaskStore(persistence_dir=tmp_path)
    before.recover()
    _running(before, "cut-off")

    _reset_recovery_guard_for_tests()
    after = BackgroundTaskStore(persistence_dir=tmp_path)
    after.recover()
    tool = CheckBackgroundAgentTool(after)
    assert tool.execute(agent_id="") == "No background agents in this conversation."
    # An earlier agent is still readable by its id.
    assert "(cut-off) failed" in tool.execute(agent_id="cut-off")
    _settled(after, "new")
    listing = tool.execute(agent_id="")
    assert "[new]" in listing and "cut-off" not in listing


# ---------------------------------------------------------------------------
# /agent status and the dashboard
# ---------------------------------------------------------------------------


def _run_agent_command(monkeypatch, store, args):
    from agentao.cli.commands_ext import agents as agents_mod

    printed: list = []
    monkeypatch.setattr(
        agents_mod.console, "print", lambda *a, **k: printed.append(str(a[0]) if a else "")
    )
    agents_mod.handle_agent_command(SimpleNamespace(agent=SimpleNamespace(bg_store=store)), args)
    return "\n".join(printed)


def _store_with_history():
    store = BackgroundTaskStore()
    _settled(store, "earlier1")
    store.start_new_conversation()
    _settled(store, "current1")
    return store


def test_agent_status_defaults_to_this_conversation(monkeypatch):
    out = _run_agent_command(monkeypatch, _store_with_history(), "status")
    assert "current1" in out and "earlier1" not in out


def test_agent_status_all_includes_earlier_ones(monkeypatch):
    out = _run_agent_command(monkeypatch, _store_with_history(), "status --all")
    assert "Background Agents (2)" in out
    assert "earlier1" in out and "current1" in out


def test_agent_status_by_id_still_reads_an_earlier_one(monkeypatch):
    out = _run_agent_command(monkeypatch, _store_with_history(), "status earlier1")
    assert "completed" in out


def test_agent_status_points_at_all_when_this_conversation_has_none(monkeypatch):
    store = BackgroundTaskStore()
    _settled(store, "earlier1")
    store.start_new_conversation()
    out = _run_agent_command(monkeypatch, store, "status")
    assert "No background agents in this conversation" in out
    assert "/agent status --all" in out


def test_dashboard_all_flag_reaches_the_history(monkeypatch):
    from agentao.cli.commands_ext import agents as agents_mod

    seen: list = []
    monkeypatch.setattr(
        agents_mod, "_show_agents_dashboard",
        lambda cli, *, show_all=False: seen.append(show_all),
    )
    store = _store_with_history()
    _run_agent_command(monkeypatch, store, "dashboard --all")
    _run_agent_command(monkeypatch, store, "dashboard")
    assert seen == [True, False]


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def _finished_on_disk(tmp_path, n):
    """``n`` finished records from an earlier run, oldest first."""
    store = BackgroundTaskStore(persistence_dir=tmp_path)
    store.recover()
    for i in range(n):
        _settled(store, f"t{i:03d}")
        store._tasks[f"t{i:03d}"]["finished_at"] = 1000.0 + i
    store._flush_to_disk()
    _reset_recovery_guard_for_tests()


def test_startup_keeps_only_the_newest_finished_records(tmp_path):
    from agentao.agents.store import load_bg_task_store

    cap = bg_store_mod._MAX_FINISHED_RECORDS
    _finished_on_disk(tmp_path, cap + 10)
    store = BackgroundTaskStore(persistence_dir=tmp_path)
    store.recover()
    kept = sorted(t["id"] for t in store.list())
    assert kept == [f"t{i:03d}" for i in range(10, cap + 10)]
    on_disk = load_bg_task_store(tmp_path / ".agentao" / "background_tasks.json")
    assert sorted(on_disk) == kept


def test_this_conversations_records_are_never_pruned():
    cap = bg_store_mod._MAX_FINISHED_RECORDS
    store = BackgroundTaskStore()
    for i in range(cap + 5):
        _settled(store, f"t{i:03d}")
    assert len(store.list()) == cap + 5


def test_a_launch_prunes_earlier_conversations_records():
    cap = bg_store_mod._MAX_FINISHED_RECORDS
    store = BackgroundTaskStore()
    for i in range(cap + 5):
        _settled(store, f"t{i:03d}")
        store._tasks[f"t{i:03d}"]["finished_at"] = 1000.0 + i
    store.start_new_conversation()
    _running(store, "fresh")
    ids = {t["id"] for t in store.list()}
    assert "fresh" in ids
    assert len(ids) == cap + 1
    assert "t000" not in ids and f"t{cap + 4:03d}" in ids


def test_running_records_are_never_pruned():
    cap = bg_store_mod._MAX_FINISHED_RECORDS
    store = BackgroundTaskStore()
    _running(store, "busy")
    for i in range(cap + 5):
        _settled(store, f"t{i:03d}")
    store.start_new_conversation()
    _running(store, "fresh")
    assert store.get("busy")["status"] == "running"
