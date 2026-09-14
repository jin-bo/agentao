"""What `/clear` and `/new` must not carry into the conversation they start.

Two leaks, both in the shared reset path (``cli/commands/reset.py``):

1. **Background-agent notifications.** The chat loop drains the store's queue
   into whatever history exists at the next turn
   (``runtime/chat_loop/_runner.py::_inject_background_notifications``), and
   nothing on the reset path touched the queue — so a task launched before a
   reset reported, result preview included, into the conversation after it.
2. **A failed memory wipe reported as success.** ``clear_all_session_summaries``
   swallows its error and answers 0, the same 0 as "nothing to delete", and
   ``/clear`` printed "all memories cleared" regardless. A surviving summary is
   not inert: ``get_cross_session_tail`` puts it back in the next prompt. And a
   raise from ``MemoryManager.clear()`` abandoned the reset half-way, after the
   history was gone but before the permission mode was restored.
"""

from __future__ import annotations

from functools import partial
from unittest.mock import patch

import pytest
from rich.console import Console

from agentao.agent import Agentao
from agentao.agents.bg_store import BackgroundTaskStore
from agentao.permissions import PermissionMode


@pytest.fixture
def bg_store():
    return BackgroundTaskStore(persistence_dir=None)


# ── the store: a notification belongs to the conversation that launched it ──


def test_a_task_from_before_the_reset_finishes_silently(bg_store):
    bg_store.register("old", "worker", "task")
    bg_store.mark_running("old")

    bg_store.start_new_conversation()
    bg_store.update("old", status="completed", result="secret result")

    assert bg_store.drain_notifications() == []
    # Silenced, not lost: the record still settles for `/agents`.
    rec = bg_store.get("old")
    assert rec["status"] == "completed"
    assert rec["result"] == "secret result"


def test_a_queued_notification_does_not_survive_the_reset(bg_store):
    bg_store.register("done", "worker", "task")
    bg_store.update("done", status="completed", result="finished earlier")

    bg_store.start_new_conversation()

    assert bg_store.drain_notifications() == []


def test_a_task_launched_after_the_reset_still_reports(bg_store):
    bg_store.start_new_conversation()
    bg_store.register("new", "worker", "task")
    bg_store.update("new", status="failed", error="boom")

    notes = bg_store.drain_notifications()
    assert len(notes) == 1 and "new" in notes[0]


def test_a_pending_cancel_after_the_reset_is_silenced_too(bg_store):
    # `cancel()` on a pending task pushes its own notification, separately
    # from `update()` — the second push site.
    bg_store.register("queued", "worker", "task")
    bg_store.start_new_conversation()

    bg_store.cancel("queued")

    assert bg_store.drain_notifications() == []
    assert bg_store.get("queued")["status"] == "cancelled"


def _during_next_flush(store, action):
    """Run ``action`` inside the store's next ``_flush_to_disk`` call.

    ``update()`` settles the status, releases its lock, flushes, and only then
    pushes — so the flush is the window in which another thread can see a
    terminal task. The real flush is restored before ``action`` runs, because
    ``delete()`` and a rebind both flush too.
    """
    real = store._flush_to_disk

    def interleave():
        store._flush_to_disk = real
        action()
        real()

    store._flush_to_disk = interleave


def test_a_delete_during_the_final_flush_cannot_readmit_the_result(bg_store):
    """Found by Codex review: `delete()` accepts the task as soon as its status
    is terminal and drops its generation entry; a push that looked the entry up
    afterwards found nothing and delivered into the new conversation."""
    bg_store.register("old", "worker", "task")
    bg_store.mark_running("old")
    bg_store.start_new_conversation()

    _during_next_flush(bg_store, lambda: bg_store.delete("old"))
    bg_store.update("old", status="completed", result="from the old session")

    assert bg_store.drain_notifications() == []
    assert bg_store.get("old") is None  # the delete did go through


def test_a_rebind_during_the_final_flush_cannot_readmit_the_result(tmp_path):
    """Same window, other remover: a cwd change drops settled tasks, and their
    generation entries with them."""
    project_a, project_b = tmp_path / "a", tmp_path / "b"
    project_a.mkdir()
    project_b.mkdir()
    cwd = {"path": project_a}
    store = BackgroundTaskStore(persistence_dir_provider=lambda: cwd["path"])
    store.register("old", "worker", "task")
    store.mark_running("old")
    store.start_new_conversation()

    def move_to_b():
        cwd["path"] = project_b
        store._check_persistence_rebind()

    _during_next_flush(store, move_to_b)
    store.update("old", status="completed", result="from the old session")

    assert store.drain_notifications() == []


def test_a_task_the_store_never_registered_does_not_notify(bg_store):
    """Fail closed: with no record of which conversation launched a task, the
    current one is not a safe guess."""
    bg_store._tasks["foreign"] = {"agent_name": "worker", "status": "running"}

    bg_store.update("foreign", status="failed", error="boom")

    assert bg_store.drain_notifications() == []


def test_count_in_flight_counts_only_unsettled_tasks(bg_store):
    bg_store.register("pending", "worker", "task")
    bg_store.register("running", "worker", "task")
    bg_store.mark_running("running")
    bg_store.register("done", "worker", "task")
    bg_store.update("done", status="completed", result="r")

    assert bg_store.count_in_flight() == 2


# ── the runtime: clear_history is where every host gets the cutoff ─────────


def test_clear_history_stops_old_tasks_reaching_the_next_turn(tmp_path, bg_store):
    """Driven through the real drain, not the store alone: the leak was in
    what the chat loop appends to history, so that is what is asserted."""
    from agentao.runtime.chat_loop import ChatLoopRunner

    agent = Agentao(
        working_directory=tmp_path, api_key="k",
        base_url="https://test.local/v1", model="m", bg_store=bg_store,
    )
    bg_store.register("old", "worker", "task")
    bg_store.update("old", status="completed", result="from the old session")
    bg_store.register("still-running", "worker", "task")
    bg_store.mark_running("still-running")

    agent.clear_history()
    bg_store.update("still-running", status="completed", result="late result")

    msgs = [{"role": "system", "content": ""}]
    out = ChatLoopRunner(agent)._inject_background_notifications(msgs, system_prompt="")
    assert out is msgs
    assert agent.messages == []


# ── the memory wipe: never report a failure as success ──────────────────────


def _manager(tmp_path):
    from agentao.memory import MemoryManager, SQLiteMemoryStore

    return MemoryManager(
        project_store=SQLiteMemoryStore.open_or_memory(tmp_path / "memory.db"),
    )


def _raise(*_a, **_k):
    raise RuntimeError("database is locked")


def test_wipe_reports_nothing_left_on_success(tmp_path):
    from agentao.cli._utils import wipe_all_memories

    mgr = _manager(tmp_path)
    mgr.save_session_summary("s", tokens_before=1, messages_summarized=1)

    _, summaries, not_cleared = wipe_all_memories(mgr)

    assert summaries == 1
    assert not_cleared == []


def test_wipe_names_summaries_that_survived_a_failed_delete(tmp_path, monkeypatch):
    from agentao.cli._utils import wipe_all_memories

    mgr = _manager(tmp_path)
    mgr.save_session_summary("survivor", tokens_before=1, messages_summarized=1)
    monkeypatch.setattr(mgr.project_store, "clear_session_summaries", _raise)

    _, summaries, not_cleared = wipe_all_memories(mgr)

    # The return count cannot tell this apart from "nothing to delete".
    assert summaries == 0
    assert not_cleared == ["session summaries"]
    mgr.archive_session()
    assert "survivor" in mgr.get_cross_session_tail()  # why it matters


def test_wipe_still_clears_summaries_when_memories_raise(tmp_path, monkeypatch):
    from agentao.cli._utils import wipe_all_memories

    mgr = _manager(tmp_path)
    mgr.save_session_summary("s", tokens_before=1, messages_summarized=1)
    monkeypatch.setattr(mgr, "clear", _raise)

    _, _, not_cleared = wipe_all_memories(mgr)

    assert not_cleared == ["memories"]
    assert mgr.session_summaries_remain() is False


def test_an_unreadable_store_cannot_confirm_the_summaries_are_gone(tmp_path, monkeypatch):
    mgr = _manager(tmp_path)
    monkeypatch.setattr(mgr.project_store, "list_session_summaries", _raise)

    assert mgr.session_summaries_remain() is True


# ── the commands, through the real CLI ──────────────────────────────────────


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """A real AgentaoCLI rooted in ``tmp_path``.

    HOME is redirected for the reason ``tests/test_clear_resets_confirm.py``
    spells out: ``/clear`` reaches ``MemoryManager.clear()``, which also clears
    the *user* store at ``~/.agentao/memory.db``.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows

    from agentao.embedding import build_from_environment

    with patch("agentao.cli.app.safe_load_dotenv"), \
            patch("agentao.cli.subcommands._load_and_register_plugins"):
        from agentao.cli import AgentaoCLI
        return AgentaoCLI(agent_factory=partial(
            build_from_environment, working_directory=tmp_path))


@pytest.fixture
def output(monkeypatch):
    from agentao.cli.commands import reset

    rec = Console(record=True, width=200)
    monkeypatch.setattr(reset, "console", rec)
    return rec


def test_fixture_isolates_the_user_memory_store(cli, tmp_path):
    user_store = cli.agent.memory_manager.user_store
    assert user_store is not None
    assert str(tmp_path) in str(user_store.db_path)


def test_clear_does_not_claim_success_when_summaries_survive(cli, output, monkeypatch):
    from agentao.cli.commands import handle_clear_command

    mgr = cli.agent.memory_manager
    mgr.save_session_summary("survivor", tokens_before=1, messages_summarized=1)
    monkeypatch.setattr(mgr.project_store, "clear_session_summaries", _raise)

    handle_clear_command(cli, "")

    text = output.export_text()
    assert "all memories cleared" not in text
    assert "could not be cleared: session summaries" in text


def test_clear_finishes_the_reset_when_the_memory_wipe_raises(cli, output, monkeypatch):
    from agentao.cli.commands import handle_clear_command

    cli._apply_mode(PermissionMode.FULL_ACCESS)
    monkeypatch.setattr(cli.agent.memory_manager, "clear", _raise)

    handle_clear_command(cli, "")

    assert cli.current_mode == PermissionMode.WORKSPACE_WRITE
    assert cli.current_session_id is not None  # on_session_start ran
    assert "could not be cleared: memories" in output.export_text()


@pytest.mark.parametrize("handler_name", ["handle_clear_command", "handle_new_command"])
def test_reset_warns_about_agents_that_will_now_finish_silently(cli, output, handler_name):
    from agentao.cli import commands

    bg_store = cli.agent.bg_store
    bg_store.register("worker-1", "worker", "task")
    bg_store.mark_running("worker-1")

    getattr(commands, handler_name)(cli, "")

    assert "1 background agent(s) still running" in output.export_text()
    bg_store.update("worker-1", status="completed", result="late")
    assert bg_store.drain_notifications() == []


def test_new_without_background_agents_prints_no_warning(cli, output):
    from agentao.cli.commands import handle_new_command

    handle_new_command(cli, "")

    assert "background agent" not in output.export_text()


# ── review follow-ups ───────────────────────────────────────────────────────


def test_resume_is_a_cutoff_too(monkeypatch, bg_store):
    """`/sessions resume` replaces history without `clear_history()`, so the
    cutoff has to be applied there explicitly or the leak moves one command
    over."""
    from unittest.mock import Mock

    from agentao.cli.commands import sessions as sess_mod

    bg_store.register("old", "worker", "task")
    bg_store.mark_running("old")

    cli = Mock()
    cli.agent.bg_store = bg_store
    cli.agent.working_directory = "."
    monkeypatch.setattr(
        "agentao.embedding.sessions.list_sessions",
        lambda project_root=None: [{"id": "s1", "session_id": "s1", "title": "t"}],
    )
    monkeypatch.setattr(
        "agentao.embedding.sessions.load_session",
        lambda sid, project_root=None: ([{"role": "user", "content": "hi"}], "m", []),
    )

    sess_mod.resume_session(cli, "s1")
    bg_store.update("old", status="completed", result="from the session left behind")

    assert bg_store.drain_notifications() == []


def test_reset_tolerates_a_runtime_without_bg_store():
    """`bg_store` is not in the agent-factory contract, so a conforming runtime
    may lack it; the reset must still finish."""
    from agentao.cli.commands.reset import _reset_session

    class _Plan:
        is_active = False

    class _Agent:
        def clear_history(self):
            pass

    class _Cli:
        current_session_id = "s"
        _plan_session = _Plan()
        agent = _Agent()

        def on_session_end(self, *, reason="other"):
            pass

        def on_session_start(self, *, source="startup"):
            self.started = source

        def _apply_mode(self, mode):
            self.mode = mode

    cli = _Cli()
    outcome = _reset_session(cli, clear_memories=False)

    assert outcome.detached_agents == 0
    assert cli.started == "clear"


def test_wipe_never_raises_from_the_summary_half():
    from agentao.cli._utils import wipe_all_memories

    class _Mgr:
        def clear(self):
            return 2

        def clear_all_session_summaries(self):
            raise RuntimeError("no such method on this host's manager")

    memories, summaries, not_cleared = wipe_all_memories(_Mgr())

    assert (memories, summaries) == (2, 0)
    assert not_cleared == ["session summaries"]


def test_a_partial_clear_still_invalidates_the_recall_index(tmp_path, monkeypatch):
    """Project rows committed before the user store raised: the retriever only
    rebuilds on a version change, so the bump must happen anyway."""
    from agentao.memory import MemoryManager, SQLiteMemoryStore
    from agentao.memory.models import SaveMemoryRequest

    mgr = MemoryManager(
        project_store=SQLiteMemoryStore.open_or_memory(tmp_path / "p.db"),
        user_store=SQLiteMemoryStore.open_or_memory(tmp_path / "u.db"),
    )
    mgr.upsert(SaveMemoryRequest(key="k", value="v", tags=[], scope="project"))
    before = mgr.write_version
    monkeypatch.setattr(mgr.user_store, "clear_memories", _raise)

    with pytest.raises(RuntimeError):
        mgr.clear()

    assert mgr.write_version > before
    assert mgr.get_all_entries(scope="project") == []
