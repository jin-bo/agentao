"""Tests for MemoryManager: SQLite-backed CRUD, session summaries."""

import sqlite3
from pathlib import Path

import pytest

from agentao.memory.manager import MemoryManager


def _make_manager(tmp_path: Path, with_global: bool = True) -> MemoryManager:
    proj = tmp_path / ".agentao"
    glob = tmp_path / "global" if with_global else None
    return MemoryManager(project_root=proj, global_root=glob)


# ---------------------------------------------------------------------------
# save_from_tool — basic CRUD
# ---------------------------------------------------------------------------

def test_save_from_tool_creates_project_entry(tmp_path):
    mgr = _make_manager(tmp_path)
    result = mgr.save_from_tool("test cmd", "uv run pytest", [])
    assert "memory" in result.lower()
    entries = mgr.get_all_entries(scope="project")
    assert any(e.title == "test cmd" for e in entries)


def test_save_from_tool_creates_user_entry(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("pref lang", "Python", ["user", "preference"])
    entries = mgr.get_all_entries(scope="user")
    assert any(e.title == "pref lang" for e in entries)


def test_save_from_tool_falls_back_to_project_when_no_global(tmp_path):
    mgr = _make_manager(tmp_path, with_global=False)
    mgr.save_from_tool("fallback pref", "test", ["user"])
    entries = mgr.get_all_entries(scope="project")
    assert any(e.title == "fallback pref" for e in entries)


def test_save_from_tool_updates_existing_by_title(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("my_key", "original", [])
    mgr.save_from_tool("my_key", "updated", [])
    all_entries = mgr.get_all_entries()
    matching = [e for e in all_entries if e.title == "my_key"]
    assert len(matching) == 1
    assert matching[0].content == "updated"


def test_save_from_tool_returns_result_string(tmp_path):
    mgr = _make_manager(tmp_path)
    result = mgr.save_from_tool("k", "v", [])
    assert "memory" in result.lower()


def test_save_from_tool_classifies_by_tag_user(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("pref_key", "some value", ["user", "preference"])
    user_entries = mgr.get_all_entries(scope="user")
    assert any(e.title == "pref_key" for e in user_entries)


def test_save_from_tool_classifies_by_key_prefix(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("user_preferred_language", "Python", [])
    user_entries = mgr.get_all_entries(scope="user")
    assert any(e.title == "user_preferred_language" for e in user_entries)


def test_save_from_tool_default_to_project(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("some_setting", "value", [])
    proj_entries = mgr.get_all_entries(scope="project")
    assert any(e.title == "some_setting" for e in proj_entries)


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

def test_delete_removes_entry(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("to delete", "content", [])
    entries = mgr.get_all_entries()
    assert len(entries) == 1
    assert mgr.delete(entries[0].id) is True
    assert len(mgr.get_all_entries()) == 0


def test_delete_returns_false_for_unknown_id(tmp_path):
    mgr = _make_manager(tmp_path)
    assert mgr.delete("00000000") is False


# ---------------------------------------------------------------------------
# get_all_entries
# ---------------------------------------------------------------------------

def test_get_all_entries_returns_both_scopes(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("proj_entry", "x", ["project"])
    mgr.save_from_tool("user_entry", "y", ["user"])
    all_entries = mgr.get_all_entries()
    titles = {e.title for e in all_entries}
    assert "proj_entry" in titles
    assert "user_entry" in titles


def test_get_all_entries_filter_by_scope(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("proj", "p", ["project"])
    mgr.save_from_tool("user_pref", "u", ["user"])
    proj = mgr.get_all_entries(scope="project")
    usr = mgr.get_all_entries(scope="user")
    assert all(e.scope == "project" for e in proj)
    assert all(e.scope == "user" for e in usr)


# ---------------------------------------------------------------------------
# search / filter_by_tag
# ---------------------------------------------------------------------------

def test_search_finds_by_title(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("fastapi setup", "uvicorn main:app", [])
    results = mgr.search("fastapi")
    assert any(e.title == "fastapi setup" for e in results)


def test_search_finds_by_value(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("build cmd", "uv run pytest --tb=short", [])
    results = mgr.search("pytest")
    assert any(e.title == "build cmd" for e in results)


def test_search_finds_by_tag(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("lang pref", "Python", ["preference", "language"])
    results = mgr.search("language")
    assert any(e.title == "lang pref" for e in results)


def test_search_returns_empty_for_no_match(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("something", "else", [])
    assert mgr.search("zzznomatch") == []


def test_search_finds_by_key_normalized(tmp_path):
    """Manager facade also searches across the normalized key."""
    mgr = _make_manager(tmp_path)
    # save_from_tool uses the title argument as the key source — passing a
    # descriptive key with a terse value tests the key path specifically.
    mgr.save_from_tool("user_preferred_python_version", "3.12", [])
    results = mgr.search("preferred_python")
    assert any("preferred_python" in r.key_normalized for r in results)


def test_search_finds_by_keyword(tmp_path):
    """Manager facade searches keywords_json as well as title/content/tags."""
    mgr = _make_manager(tmp_path)
    # MemoryGuard.extract_keywords pulls keywords from title+tags+content,
    # so 'fastapi' in the content will appear in keywords_json after upsert.
    mgr.save_from_tool("stack info", "fastapi backend", [])
    results = mgr.search("fastapi")
    assert len(results) >= 1


def test_filter_by_tag_returns_matching(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("a", "x", ["python", "backend"])
    mgr.save_from_tool("b", "y", ["frontend"])
    results = mgr.filter_by_tag("python")
    assert len(results) == 1
    assert results[0].title == "a"


def test_filter_by_tag_case_insensitive(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("x", "v", ["Python"])
    results = mgr.filter_by_tag("python")
    assert len(results) == 1


# ---------------------------------------------------------------------------
# delete_by_title / clear
# ---------------------------------------------------------------------------

def test_delete_by_title_removes_entry(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("remove me", "v", [])
    count = mgr.delete_by_title("remove me")
    assert count == 1
    assert len(mgr.get_all_entries()) == 0


def test_delete_by_title_returns_count(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("keep", "v1", [])
    count = mgr.delete_by_title("nonexistent")
    assert count == 0


def test_delete_by_title_case_insensitive(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("MyKey", "v", [])
    count = mgr.delete_by_title("mykey")
    assert count == 1


def test_clear_removes_all_entries(tmp_path):
    mgr = _make_manager(tmp_path)
    for i in range(3):
        mgr.save_from_tool(f"entry_{i}", "v", [])
    count = mgr.clear()
    assert count == 3
    assert mgr.get_all_entries() == []


def test_clear_scoped_removes_only_project(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("proj", "p", ["project"])
    mgr.save_from_tool("user_pref", "u", ["user"])
    count = mgr.clear(scope="project")
    assert count == 1
    remaining = mgr.get_all_entries()
    assert all(e.scope == "user" for e in remaining)


# ---------------------------------------------------------------------------
# Session summaries
# ---------------------------------------------------------------------------

def test_save_session_summary(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_session_summary("A summary.", tokens_before=1000, messages_summarized=5)
    summaries = mgr.get_recent_session_summaries()
    assert len(summaries) == 1
    assert "A summary." in summaries[0].summary_text


def test_save_session_summary_noop_on_exception(tmp_path):
    """save_session_summary must not raise even if store fails."""
    mgr = MemoryManager(project_root=Path("/nonexistent/readonly/path"), global_root=None)
    mgr.save_session_summary("S.", tokens_before=0, messages_summarized=0)


def test_clear_session_removes_summaries(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_session_summary("content", tokens_before=100, messages_summarized=3)
    mgr.clear_session()
    assert mgr.get_recent_session_summaries() == []


def test_archive_session_returns_old_session_id(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_session_summary("content", tokens_before=100, messages_summarized=3)
    old_id = mgr.archive_session()
    assert old_id is not None
    # New session has different id
    assert mgr._session_id != old_id


def test_archive_session_returns_none_when_empty(tmp_path):
    mgr = _make_manager(tmp_path)
    result = mgr.archive_session()
    assert result is None


# ---------------------------------------------------------------------------
# write_version dirty-flag counter
# ---------------------------------------------------------------------------

def test_write_version_increments_on_save(tmp_path):
    mgr = _make_manager(tmp_path)
    v0 = mgr.write_version
    mgr.save_from_tool("k1", "v1", [])
    v1 = mgr.write_version
    mgr.save_from_tool("k2", "v2", [])
    v2 = mgr.write_version
    assert v1 > v0
    assert v2 > v1


def test_write_version_increments_on_update(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("key", "original", [])
    v_before = mgr.write_version
    mgr.save_from_tool("key", "updated", [])
    assert mgr.write_version > v_before


def test_write_version_increments_on_delete(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("key", "val", [])
    v_before = mgr.write_version
    entries = mgr.get_all_entries()
    mgr.delete(entries[0].id)
    assert mgr.write_version > v_before


# ---------------------------------------------------------------------------
# get_stable_entries — stable-block selection policy
# ---------------------------------------------------------------------------

def test_stable_entries_always_includes_user_scope(tmp_path):
    """User-scope entries always appear in stable block."""
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("user_lang", "Python", ["user", "preference"])
    stable = mgr.get_stable_entries()
    assert any(e.title == "user_lang" for e in stable)


def test_stable_entries_always_includes_structural_types(tmp_path):
    """Structural project types (decision, constraint, workflow, profile, preference) always appear."""
    mgr = _make_manager(tmp_path)
    for type_tag, key in [
        ("decision", "arch_decision"),
        ("constraint", "rate_limit"),
        ("workflow", "dev_workflow"),
        ("preference", "code_style"),
    ]:
        mgr.save_from_tool(key, "value", [type_tag])
    stable = mgr.get_stable_entries()
    stable_titles = {e.title for e in stable}
    assert "arch_decision" in stable_titles
    assert "rate_limit" in stable_titles
    assert "dev_workflow" in stable_titles
    assert "code_style" in stable_titles


def test_stable_entries_caps_incidental_types(tmp_path):
    """Incidental project types (note, project_fact) are capped at recent_project_limit."""
    mgr = _make_manager(tmp_path)
    # Save 5 plain notes (type=note, scope=project)
    for i in range(5):
        mgr.save_from_tool(f"note_{i}", f"content {i}", [])
    stable = mgr.get_stable_entries(recent_project_limit=3)
    note_entries = [e for e in stable if e.type == "note"]
    assert len(note_entries) == 3


def test_stable_entries_incidental_takes_most_recent(tmp_path):
    """When capping incidental entries, the most-recently-updated ones are kept."""
    import time
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("old_note", "old", [])
    time.sleep(0.01)  # ensure distinct updated_at
    mgr.save_from_tool("new_note", "new", [])
    stable = mgr.get_stable_entries(recent_project_limit=1)
    stable_titles = {e.title for e in stable}
    assert "new_note" in stable_titles
    assert "old_note" not in stable_titles


def test_stable_entries_no_duplicates(tmp_path):
    """An entry that qualifies under multiple criteria appears only once."""
    mgr = _make_manager(tmp_path)
    # user_scope AND structural type — should not be duplicated
    mgr.save_from_tool("user_decision", "use uv", ["user", "decision"])
    stable = mgr.get_stable_entries()
    ids = [e.id for e in stable]
    assert len(ids) == len(set(ids))


def test_stable_entries_structural_not_capped_by_recent_limit(tmp_path):
    """Structural types are never dropped even when incidental limit is 0."""
    mgr = _make_manager(tmp_path)
    mgr.save_from_tool("must_include", "value", ["decision"])
    mgr.save_from_tool("also_include", "value", ["constraint"])
    stable = mgr.get_stable_entries(recent_project_limit=0)
    stable_titles = {e.title for e in stable}
    assert "must_include" in stable_titles
    assert "also_include" in stable_titles


def test_stable_entries_empty_when_no_entries(tmp_path):
    mgr = _make_manager(tmp_path)
    assert mgr.get_stable_entries() == []


# ---------------------------------------------------------------------------
# Init-time store failure: sqlite3.Error must be caught alongside OSError
# (regression — otherwise Agentao() crashes in restricted/ACP environments
# where the parent directory exists but the DB file cannot be opened.)
# ---------------------------------------------------------------------------

def test_project_store_sqlite_error_falls_back_to_memory(tmp_path, monkeypatch, caplog):
    """A sqlite3.OperationalError from the project store must fall back to :memory:."""
    from agentao.memory import manager as mgr_mod

    real_init = mgr_mod.SQLiteMemoryStore.__init__
    call_paths: list[str] = []

    def fake_init(self, db_path):
        call_paths.append(db_path)
        # First call targets the on-disk project path — simulate a restricted env.
        if db_path != ":memory:":
            raise sqlite3.OperationalError("unable to open database file")
        real_init(self, db_path)

    monkeypatch.setattr(mgr_mod.SQLiteMemoryStore, "__init__", fake_init)

    with caplog.at_level("WARNING", logger="agentao.memory.manager"):
        mgr = MemoryManager(project_root=tmp_path / ".agentao", global_root=None)

    # Construction succeeded with the in-memory fallback in place.
    assert mgr.project_store is not None
    assert ":memory:" in call_paths
    assert any("falling back to transient in-memory store" in rec.message for rec in caplog.records)

    # And the manager is functional — writes round-trip through the fallback.
    result = mgr.save_from_tool("fallback_key", "fallback_value", [])
    assert "memory" in result.lower()
    entries = mgr.get_all_entries(scope="project")
    assert any(e.title == "fallback_key" for e in entries)


def test_user_store_sqlite_error_leaves_user_store_none(tmp_path, monkeypatch, caplog):
    """A sqlite3.OperationalError from the user store must disable user scope only."""
    from agentao.memory import manager as mgr_mod

    proj_root = tmp_path / "proj"
    user_root = tmp_path / "user"
    user_db = str(user_root / "memory.db")

    real_init = mgr_mod.SQLiteMemoryStore.__init__

    def fake_init(self, db_path):
        if db_path == user_db:
            raise sqlite3.OperationalError("unable to open database file")
        real_init(self, db_path)

    monkeypatch.setattr(mgr_mod.SQLiteMemoryStore, "__init__", fake_init)

    with caplog.at_level("WARNING", logger="agentao.memory.manager"):
        mgr = MemoryManager(project_root=proj_root, global_root=user_root)

    # Project store is real; user store is disabled.
    assert mgr.project_store is not None
    assert mgr.user_store is None
    assert any("user-scope memory disabled" in rec.message for rec in caplog.records)

    # A user-scoped save is downgraded to project scope (manager.py:100).
    mgr.save_from_tool("downgraded_pref", "value", ["user", "preference"])
    project_entries = mgr.get_all_entries(scope="project")
    assert any(e.title == "downgraded_pref" for e in project_entries)
    # Nothing leaked into the (nonexistent) user scope.
    assert mgr.get_all_entries(scope="user") == []
