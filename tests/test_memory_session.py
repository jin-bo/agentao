"""Tests for MemoryManager session summary functionality (SQLite-backed)."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest


def _make_manager(tmp_path: Path):
    from agentao.memory.manager import MemoryManager
    return MemoryManager(project_root=tmp_path / ".agentao", global_root=None)


# ---------------------------------------------------------------------------
# save_session_summary
# ---------------------------------------------------------------------------

def test_save_creates_session_summary(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_session_summary("A summary.", tokens_before=1000, messages_summarized=5)
    summaries = mgr.get_recent_session_summaries()
    assert len(summaries) == 1
    assert "A summary." in summaries[0].summary_text


def test_save_multiple_summaries(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_session_summary("First.", tokens_before=500, messages_summarized=3)
    mgr.save_session_summary("Second.", tokens_before=600, messages_summarized=4)
    summaries = mgr.get_recent_session_summaries(limit=10)
    texts = [s.summary_text for s in summaries]
    assert "First." in texts
    assert "Second." in texts


def test_save_records_metadata(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_session_summary("S.", tokens_before=12345, messages_summarized=7)
    summaries = mgr.get_recent_session_summaries()
    assert summaries[0].tokens_before == 12345
    assert summaries[0].messages_summarized == 7


def test_save_is_noop_on_exception(tmp_path):
    """save_session_summary must not raise even if disk write fails."""
    from agentao.memory.manager import MemoryManager
    mgr = MemoryManager(project_root=Path("/nonexistent/readonly/path"), global_root=None)
    mgr.save_session_summary("S.", tokens_before=0, messages_summarized=0)


# ---------------------------------------------------------------------------
# archive_session
# ---------------------------------------------------------------------------

def test_archive_starts_new_session(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_session_summary("A" * 300, tokens_before=1000, messages_summarized=5)
    old_id = mgr._session_id
    result = mgr.archive_session()
    assert result == old_id
    assert mgr._session_id != old_id


def test_archive_returns_none_when_no_summaries(tmp_path):
    mgr = _make_manager(tmp_path)
    result = mgr.archive_session()
    assert result is None


# ---------------------------------------------------------------------------
# clear_session
# ---------------------------------------------------------------------------

def test_clear_session_removes_current_summaries(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_session_summary("content", tokens_before=100, messages_summarized=3)
    assert len(mgr.get_recent_session_summaries()) == 1
    mgr.clear_session()
    assert mgr.get_recent_session_summaries() == []


def test_clear_session_noop_when_no_summaries(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.clear_session()  # should not raise


def test_new_session_flow_preserves_cross_session_recall(tmp_path):
    """Regression: the ``/new`` lifecycle (archive_session, no clear_session)
    must keep the just-finished session's summaries available via
    ``get_cross_session_tail()`` so cross-session recall survives the
    transition.

    This locks in the fix for a Codex P2: an earlier version of /new called
    ``clear_session()`` BEFORE ``archive_session()``, deleting the current
    session's summaries before they could become "previous-session" data.
    """
    mgr = _make_manager(tmp_path)
    # Build a conversation in the current session and let compaction-like
    # behavior write a session summary.
    mgr.save_session_summary(
        "Session A summary text.",
        tokens_before=120,
        messages_summarized=5,
    )
    assert len(mgr.get_recent_session_summaries()) == 1

    # Simulate the /new lifecycle: archive (advance _session_id) WITHOUT
    # calling clear_session(). on_session_start() in the CLI does exactly this.
    mgr.archive_session()

    # The just-finished session is now a "previous session" — its summary must
    # surface via cross-session recall in the new session.
    assert mgr.get_recent_session_summaries() == []  # current session is empty
    tail = mgr.get_cross_session_tail()
    assert "Session A summary text." in tail


def test_clear_session_only_removes_current_session(tmp_path):
    """clear_session must NOT touch summaries from other sessions —
    those are intentionally preserved for cross-session recall."""
    mgr = _make_manager(tmp_path)
    mgr.save_session_summary("First session summary.", tokens_before=100, messages_summarized=3)
    mgr.archive_session()  # advances _session_id; first summary now belongs to a previous session
    mgr.save_session_summary("Second session summary.", tokens_before=100, messages_summarized=3)
    assert len(mgr.get_recent_session_summaries()) == 1  # current only

    mgr.clear_session()  # clears current session only

    # Current session is empty
    assert mgr.get_recent_session_summaries() == []
    # But the previous session's summary still exists in storage and surfaces via cross-session tail
    assert "First session summary." in mgr.get_cross_session_tail()


# ---------------------------------------------------------------------------
# clear_all_session_summaries — hard reset across every session
# ---------------------------------------------------------------------------


def test_clear_all_session_summaries_removes_current_session(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_session_summary("Only summary.", tokens_before=100, messages_summarized=3)
    deleted = mgr.clear_all_session_summaries()
    assert deleted == 1
    assert mgr.get_recent_session_summaries() == []


def test_clear_all_session_summaries_removes_cross_session_summaries(tmp_path):
    """The bug fix: /clear and /memory clear must wipe summaries from previous
    sessions too, otherwise they would silently resurface via
    get_cross_session_tail() in the next prompt."""
    mgr = _make_manager(tmp_path)
    # Three distinct previous sessions plus the current one
    mgr.save_session_summary("Session A summary.", tokens_before=100, messages_summarized=2)
    mgr.archive_session()
    mgr.save_session_summary("Session B summary.", tokens_before=100, messages_summarized=2)
    mgr.archive_session()
    mgr.save_session_summary("Session C summary.", tokens_before=100, messages_summarized=2)
    mgr.archive_session()
    mgr.save_session_summary("Current session summary.", tokens_before=100, messages_summarized=2)

    # Sanity: cross-session tail sees the three previous sessions
    tail_before = mgr.get_cross_session_tail()
    assert "Session A summary." in tail_before
    assert "Session B summary." in tail_before
    assert "Session C summary." in tail_before

    deleted = mgr.clear_all_session_summaries()

    assert deleted == 4  # one current + three archived
    # Both channels are now empty
    assert mgr.get_recent_session_summaries() == []
    assert mgr.get_cross_session_tail() == ""


def test_clear_all_session_summaries_noop_when_empty(tmp_path):
    mgr = _make_manager(tmp_path)
    assert mgr.clear_all_session_summaries() == 0


def test_clear_all_session_summaries_returns_zero_on_error(tmp_path, monkeypatch):
    """Failures inside the storage layer must be swallowed and return 0."""
    mgr = _make_manager(tmp_path)
    monkeypatch.setattr(
        mgr.project_store,
        "clear_session_summaries",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert mgr.clear_all_session_summaries() == 0


def test_clear_all_session_summaries_wipes_explicit_session_ids(tmp_path):
    """The store has summaries from N independent session_ids (not just chained
    archive_session() calls). clear_all must remove them all in one shot."""
    from agentao.memory.models import SessionSummaryRecord

    mgr = _make_manager(tmp_path)

    # Inject summaries with explicit, distinct session_ids that did NOT pass
    # through this manager's _session_id at all.
    for sid in ("alpha", "beta", "gamma", "delta"):
        mgr.project_store.save_session_summary(SessionSummaryRecord(
            id=f"sum_{sid}",
            session_id=sid,
            summary_text=f"summary from {sid}",
            tokens_before=100,
            messages_summarized=2,
            created_at="2026-04-08T10:00:00",
        ))
    # Plus one from the manager's current session
    mgr.save_session_summary("current session summary.", tokens_before=10, messages_summarized=1)

    # Sanity: cross-session tail sees the foreign sessions
    tail = mgr.get_cross_session_tail()
    assert "alpha" in tail or "beta" in tail or "gamma" in tail or "delta" in tail

    deleted = mgr.clear_all_session_summaries()

    assert deleted == 5
    assert mgr.get_recent_session_summaries() == []
    assert mgr.get_cross_session_tail() == ""
    # And no row in storage from any session_id, current or foreign
    assert mgr.project_store.list_session_summaries(session_id=None, limit=100) == []


def test_clear_all_session_summaries_unblocks_review_queue_starvation(tmp_path):
    """Edge: clearing all summaries does NOT touch the review queue. Review
    items survive a hard summary wipe so the user can still triage them."""
    mgr = _make_manager(tmp_path)
    # Seed a review item via the user-message path
    mgr.crystallize_user_messages([{"role": "user", "content": "I prefer fish shell"}])
    mgr.save_session_summary("some session text", tokens_before=10, messages_summarized=2)
    assert len(mgr.list_review_items()) == 1
    assert len(mgr.get_recent_session_summaries()) == 1

    mgr.clear_all_session_summaries()

    assert mgr.get_recent_session_summaries() == []
    # Review queue is unaffected by summary wipe
    assert len(mgr.list_review_items()) == 1


# ---------------------------------------------------------------------------
# Integration: ContextManager uses memory_manager when provided
# ---------------------------------------------------------------------------

def test_context_manager_writes_session_summary(tmp_path, monkeypatch):
    """compress_messages() writes to SQLite when memory_manager is present."""
    from agentao.context_manager import ContextManager
    from agentao.memory.manager import MemoryManager

    mock_llm = Mock()
    mock_llm.logger = Mock()
    mock_llm.logger.info = Mock()
    mock_llm.model = "test-model"

    mock_choice = Mock()
    mock_choice.message.content = "Compact summary text here."
    mock_choice.message.tool_calls = None
    mock_resp = Mock()
    mock_resp.choices = [mock_choice]
    mock_llm.chat.return_value = mock_resp

    mgr = MemoryManager(project_root=tmp_path / ".agentao", global_root=None)
    mock_memory_tool = Mock()
    mock_memory_tool.execute = Mock()

    cm = ContextManager(
        llm_client=mock_llm,
        memory_tool=mock_memory_tool,
        max_tokens=200_000,
        memory_manager=mgr,
    )

    msgs = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i} " * 50}
        for i in range(30)
    ]

    with patch.object(cm, "estimate_tokens", return_value=150_000):
        with patch.object(cm, "_summarize_messages", return_value="Compact summary text here."):
            cm.compress_messages(msgs)

    summaries = mgr.get_recent_session_summaries(limit=10)
    assert any("Compact summary text here." in s.summary_text for s in summaries)


def test_context_manager_crystallizes_from_raw_user_messages(tmp_path):
    """compress_messages() must crystallize from to_summarize (raw user
    messages), not from the LLM-generated summary text. Assistant prose
    that happens to contain pattern words must be ignored."""
    from agentao.context_manager import ContextManager
    from agentao.memory.manager import MemoryManager

    mock_llm = Mock()
    mock_llm.logger = Mock()
    mock_llm.logger.info = Mock()
    mock_llm.model = "test-model"

    mgr = MemoryManager(project_root=tmp_path / ".agentao", global_root=None)
    cm = ContextManager(
        llm_client=mock_llm,
        memory_tool=Mock(),
        max_tokens=200_000,
        memory_manager=mgr,
    )

    # Build a window where:
    #   - Many user messages contain "I prefer dark mode" → should crystallize
    #   - Many assistant messages contain "Never commit secrets" → MUST be ignored
    msgs = []
    for i in range(15):
        msgs.append({"role": "user", "content": f"msg {i} I prefer dark mode"})
        msgs.append({"role": "assistant", "content": f"reply {i} Never commit secrets to git"})
    msgs.append({"role": "user", "content": "trailing user msg"})

    with patch.object(cm, "estimate_tokens", return_value=150_000):
        with patch.object(cm, "_summarize_messages", return_value="LLM narration with words like 'I prefer X'"):
            cm.compress_messages(msgs)

    items = mgr.list_review_items()

    # The user's preference IS crystallized
    pref_items = [it for it in items if it.type == "preference" and "dark mode" in it.content.lower()]
    assert len(pref_items) == 1, f"expected 1 preference item, got {[it.title for it in items]}"
    assert pref_items[0].occurrences >= 2  # multiple user messages mentioned it

    # The assistant's "Never commit secrets" must NOT have been crystallized
    constraint_items = [it for it in items if it.type == "constraint"]
    assert constraint_items == [], (
        f"Assistant prose leaked into the crystallizer: {[it.title for it in constraint_items]}"
    )

    # And the LLM summary text was NOT scanned (it contained 'I prefer X' but X
    # is generic — would have produced a 'preference_x' key, which we should NOT see)
    leaked = [it for it in items if it.key_normalized.startswith("preference_x")]
    assert leaked == []


# ---------------------------------------------------------------------------
# get_cross_session_tail
# ---------------------------------------------------------------------------


class TestGetCrossSessionTail:
    def test_returns_empty_when_no_summaries(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr.get_cross_session_tail() == ""

    def test_excludes_current_session(self, tmp_path):
        """Summaries from the current session must NOT appear in the tail."""
        mgr = _make_manager(tmp_path)
        mgr.save_session_summary("Current session summary.", tokens_before=100, messages_summarized=3)
        assert mgr.get_cross_session_tail() == ""

    def test_includes_previous_session(self, tmp_path):
        """Summaries from archived (previous) sessions appear in the tail."""
        mgr = _make_manager(tmp_path)
        mgr.save_session_summary("Old summary.", tokens_before=100, messages_summarized=3)
        mgr.archive_session()  # advances _session_id
        tail = mgr.get_cross_session_tail()
        assert "Old summary." in tail

    def test_capped_at_three_sessions(self, tmp_path):
        """Only up to 3 previous-session summaries are included."""
        mgr = _make_manager(tmp_path)
        for i in range(5):
            mgr.save_session_summary(f"Session {i} summary.", tokens_before=100, messages_summarized=1)
            mgr.archive_session()
        tail = mgr.get_cross_session_tail()
        count = tail.count("summary.")
        assert count <= 3

    def test_truncated_to_session_tail_chars(self, tmp_path):
        from agentao.memory.models import SESSION_TAIL_CHARS
        mgr = _make_manager(tmp_path)
        mgr.save_session_summary("X" * 1000, tokens_before=100, messages_summarized=1)
        mgr.archive_session()
        tail = mgr.get_cross_session_tail()
        assert len(tail) <= SESSION_TAIL_CHARS
