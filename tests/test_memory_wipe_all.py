"""``MemoryManager.wipe_all`` — the hard reset, as an embedded host sees it (#235).

The CLI could already tell a failed wipe from an empty store; a host could
not. ``clear_all_session_summaries`` returns 0 both for "nothing to delete"
and for "the delete was swallowed", and the developer guide told hosts to
wipe with exactly that call. These tests pin the contract that replaced it:
failures come back *in the result*, the counts are not the success signal,
and ``ok`` is.
"""

from __future__ import annotations

from agentao.memory import MemoryManager, SQLiteMemoryStore
from agentao.memory.models import MemoryReviewItem, SaveMemoryRequest


def _raise(*_a, **_k):
    raise RuntimeError("database is locked")


def _manager(tmp_path, *, user_store: bool = False):
    return MemoryManager(
        project_store=SQLiteMemoryStore.open_or_memory(tmp_path / "memory.db"),
        user_store=(
            SQLiteMemoryStore.open_or_memory(tmp_path / "user.db")
            if user_store
            else None
        ),
    )


def _save(mgr, key="k", value="v", scope="project"):
    return mgr.upsert(SaveMemoryRequest(key=key, value=value, tags=[], scope=scope))


def test_an_empty_store_is_confirmed_empty(tmp_path):
    result = _manager(tmp_path).wipe_all()

    assert (result.memories_cleared, result.summaries_cleared) == (0, 0)
    assert result.not_cleared == ()
    assert result.ok is True


def test_everything_present_is_cleared_and_counted(tmp_path):
    mgr = _manager(tmp_path)
    _save(mgr)
    mgr.save_session_summary("s", tokens_before=1, messages_summarized=1)

    result = mgr.wipe_all()

    assert (result.memories_cleared, result.summaries_cleared) == (1, 1)
    assert result.ok is True
    assert mgr.get_all_entries() == []


def test_both_scopes_go_not_just_the_project_store(tmp_path):
    """The user store is the cross-project one — a "forget me" has to reach it.

    Asserted positively and separately from the failure cases below: every
    other user-store test here patches it to raise, so a regression narrowing
    the wipe to ``clear(scope="project")`` would pass all of them.
    """
    mgr = _manager(tmp_path, user_store=True)
    _save(mgr, key="proj", scope="project")
    _save(mgr, key="prefers dark mode", scope="user")
    assert len(mgr.get_all_entries()) == 2

    result = mgr.wipe_all()

    assert result.memories_cleared == 2
    assert result.ok is True
    assert mgr.get_all_entries() == []
    assert mgr.get_all_entries(scope="user") == []


def test_both_halves_failing_names_both_in_order(tmp_path, monkeypatch):
    """The two-part failure, which is what the CLI joins into one sentence.

    Order is pinned because ``/clear`` renders ``', '.join(not_cleared)`` and
    branches on ``"memories" not in not_cleared`` (``commands/reset.py``).
    """
    mgr = _manager(tmp_path)
    _save(mgr)
    mgr.save_session_summary("s", tokens_before=1, messages_summarized=1)
    monkeypatch.setattr(mgr.project_store, "clear_memories", _raise)
    monkeypatch.setattr(mgr.project_store, "clear_session_summaries", _raise)

    result = mgr.wipe_all()

    assert result.not_cleared == ("memories", "session summaries")
    assert result.ok is False


def test_the_memories_half_is_soft_and_the_rows_stay_in_the_file(tmp_path):
    """``ok`` is not an erasure guarantee — pins what the docs now promise.

    The developer guide sells this as the "forget me" primitive, so the gap
    between "no read path returns it" and "the bytes are gone" is part of the
    contract rather than an implementation detail.
    """
    import sqlite3

    mgr = _manager(tmp_path)
    _save(mgr, key="k", value="still-on-disk")

    assert mgr.wipe_all().ok is True
    assert mgr.get_all_entries() == []

    conn = sqlite3.connect(tmp_path / "memory.db")
    try:
        rows = conn.execute("SELECT content, deleted_at FROM memories").fetchall()
    finally:
        conn.close()   # Windows will not let tmp_path go while it is open
    assert [r[0] for r in rows] == ["still-on-disk"]
    assert rows[0][1]                                   # deleted_at is set


def test_a_swallowed_summary_delete_is_reported_not_counted(tmp_path, monkeypatch):
    """The whole reason the read-back exists.

    The failure is injected at the *store*, not on the manager: patching
    ``clear_all_session_summaries`` instead would raise into ``wipe_all``'s
    ``except`` and never run the read-back, which is the thing under test.
    And a summary has to be there first — against an empty store the read-back
    answers "clear" for an unrelated reason and this case cannot fail.
    """
    mgr = _manager(tmp_path)
    _save(mgr)
    mgr.save_session_summary("survivor", tokens_before=1, messages_summarized=1)
    monkeypatch.setattr(mgr.project_store, "clear_session_summaries", _raise)

    result = mgr.wipe_all()

    assert result.summaries_cleared == 0        # same 0 as "nothing to delete"
    assert result.not_cleared == ("session summaries",)
    assert result.ok is False
    assert result.memories_cleared == 1         # the half that worked still counts

    mgr.archive_session()
    assert "survivor" in mgr.get_cross_session_tail()  # why it matters


def test_a_store_that_cannot_be_read_cannot_confirm_the_wipe(tmp_path, monkeypatch):
    mgr = _manager(tmp_path)
    monkeypatch.setattr(mgr.project_store, "list_session_summaries", _raise)

    result = mgr.wipe_all()

    assert result.not_cleared == ("session summaries",)
    assert result.ok is False


def test_memories_left_behind_are_named_and_the_summaries_still_go(tmp_path, monkeypatch):
    """A user store that raises after the project store committed.

    Part of the memories are still there, so the wipe is not ok — and the
    second half runs anyway, because ``/clear`` calls this mid-reset.
    """
    mgr = _manager(tmp_path, user_store=True)
    mgr.save_session_summary("s", tokens_before=1, messages_summarized=1)
    monkeypatch.setattr(mgr.user_store, "clear_memories", _raise)

    result = mgr.wipe_all()

    assert result.not_cleared == ("memories",)
    assert result.ok is False
    assert mgr.session_summaries_remain() is False


def test_the_wipe_does_not_reach_the_review_queue(tmp_path):
    """Pins the gap the docstring names, so the two cannot drift.

    Crystallized candidates carry an excerpt of the messages they came from
    and stay visible to ``/memory review`` after a wipe. If this ever starts
    failing, the docstring and the developer guide are what to update.
    """
    mgr = _manager(tmp_path)
    mgr.project_store.upsert_review_item(
        MemoryReviewItem(
            id="r1",
            scope="project",
            type="preference",
            key_normalized="k",
            title="t",
            content="c",
            evidence="what the user actually said",
        )
    )

    assert mgr.wipe_all().ok is True
    assert [i.id for i in mgr.list_review_items()] == ["r1"]
