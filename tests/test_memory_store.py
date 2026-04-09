"""Tests for SQLiteMemoryStore: CRUD, session summaries, soft delete."""

import uuid
from pathlib import Path

import pytest

from agentao.memory.storage import SQLiteMemoryStore
from agentao.memory.models import MemoryRecord, MemoryReviewItem, SessionSummaryRecord


def _make_store(tmp_path: Path) -> SQLiteMemoryStore:
    return SQLiteMemoryStore(str(tmp_path / "test.db"))


def _make_record(
    scope="project",
    type_="note",
    key="test_key",
    title="Test Entry",
    content="some content",
    tags=None,
    keywords=None,
    source="explicit",
    record_id=None,
) -> MemoryRecord:
    return MemoryRecord(
        id=record_id or uuid.uuid4().hex[:8],
        scope=scope,
        type=type_,
        key_normalized=key,
        title=title,
        content=content,
        tags=tags or [],
        keywords=keywords or [],
        source=source,
        confidence="explicit_user",
        sensitivity="normal",
        created_at="2026-04-08T10:00:00",
        updated_at="2026-04-08T10:00:00",
    )


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------


class TestUpsertAndGet:
    def test_insert_and_get_by_id(self, tmp_path):
        store = _make_store(tmp_path)
        rec = _make_record(title="hello", content="world")
        saved = store.upsert_memory(rec)
        assert saved.id == rec.id
        assert saved.title == "hello"
        assert saved.content == "world"

        fetched = store.get_memory_by_id(rec.id)
        assert fetched is not None
        assert fetched.title == "hello"

    def test_upsert_updates_existing_by_scope_key(self, tmp_path):
        store = _make_store(tmp_path)
        r1 = _make_record(key="my_key", title="v1", content="original")
        store.upsert_memory(r1)

        r2 = _make_record(key="my_key", title="v2", content="updated", record_id="new_id_xx")
        saved = store.upsert_memory(r2)

        # Should keep original ID
        assert saved.id == r1.id
        assert saved.content == "updated"
        assert saved.title == "v2"

        # Only one record should exist
        all_recs = store.list_memories()
        assert len(all_recs) == 1

    def test_get_by_scope_key(self, tmp_path):
        store = _make_store(tmp_path)
        rec = _make_record(scope="project", key="my_key")
        store.upsert_memory(rec)

        fetched = store.get_memory_by_scope_key("project", "my_key")
        assert fetched is not None
        assert fetched.id == rec.id

    def test_get_nonexistent_returns_none(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.get_memory_by_id("nonexistent") is None
        assert store.get_memory_by_scope_key("project", "nope") is None

    def test_tags_roundtrip(self, tmp_path):
        store = _make_store(tmp_path)
        rec = _make_record(tags=["python", "backend", "testing"])
        saved = store.upsert_memory(rec)
        assert saved.tags == ["python", "backend", "testing"]


# ---------------------------------------------------------------------------
# List / Search / Filter
# ---------------------------------------------------------------------------


class TestListAndSearch:
    def test_list_all(self, tmp_path):
        store = _make_store(tmp_path)
        store.upsert_memory(_make_record(key="a", title="alpha"))
        store.upsert_memory(_make_record(key="b", title="beta"))
        records = store.list_memories()
        assert len(records) == 2

    def test_list_by_scope(self, tmp_path):
        store = _make_store(tmp_path)
        store.upsert_memory(_make_record(scope="project", key="p"))
        store.upsert_memory(_make_record(scope="user", key="u"))
        proj = store.list_memories(scope="project")
        user = store.list_memories(scope="user")
        assert len(proj) == 1
        assert len(user) == 1
        assert proj[0].scope == "project"
        assert user[0].scope == "user"

    def test_search_by_title(self, tmp_path):
        store = _make_store(tmp_path)
        store.upsert_memory(_make_record(key="a", title="fastapi setup", content="x"))
        results = store.search_memories("fastapi")
        assert len(results) == 1

    def test_search_by_content(self, tmp_path):
        store = _make_store(tmp_path)
        store.upsert_memory(_make_record(key="a", title="build", content="uv run pytest"))
        results = store.search_memories("pytest")
        assert len(results) == 1

    def test_search_by_tag(self, tmp_path):
        store = _make_store(tmp_path)
        store.upsert_memory(_make_record(key="a", title="lang", tags=["language", "preference"]))
        results = store.search_memories("language")
        assert len(results) == 1

    def test_search_no_match(self, tmp_path):
        store = _make_store(tmp_path)
        store.upsert_memory(_make_record(key="a", title="hello"))
        assert store.search_memories("zzznomatch") == []

    def test_search_by_key_normalized(self, tmp_path):
        """A descriptive key with a terse title should still be findable."""
        store = _make_store(tmp_path)
        store.upsert_memory(_make_record(
            key="user_preferred_python_version",
            title="ver",
            content="3.12",
        ))
        results = store.search_memories("preferred_python")
        assert len(results) == 1
        assert results[0].key_normalized == "user_preferred_python_version"

    def test_search_by_key_partial_substring(self, tmp_path):
        """LIKE-based search must match any substring of the key."""
        store = _make_store(tmp_path)
        store.upsert_memory(_make_record(key="api_endpoint_staging", title="t"))
        store.upsert_memory(_make_record(key="api_endpoint_prod", title="t"))
        store.upsert_memory(_make_record(key="db_url_prod", title="t"))
        results = store.search_memories("endpoint")
        assert len(results) == 2
        assert {r.key_normalized for r in results} == {"api_endpoint_staging", "api_endpoint_prod"}

    def test_search_by_key_case_insensitive(self, tmp_path):
        """Both query and key are lowered before LIKE matching."""
        store = _make_store(tmp_path)
        store.upsert_memory(_make_record(
            key="user_preferred_editor",
            title="t",
            content="vim",
        ))
        for query in ("USER_PREFERRED", "User_Preferred", "user_PREFERRED"):
            assert len(store.search_memories(query)) == 1, query

    def test_search_by_key_with_scope_filter(self, tmp_path):
        """Scope filter narrows the result set when searching by key."""
        store = _make_store(tmp_path)
        store.upsert_memory(_make_record(scope="project", key="x_redis_proj", title="t"))
        store.upsert_memory(_make_record(scope="user",    key="x_redis_user", title="t"))
        proj = store.search_memories("redis", scope="project")
        user = store.search_memories("redis", scope="user")
        assert [r.key_normalized for r in proj] == ["x_redis_proj"]
        assert [r.key_normalized for r in user] == ["x_redis_user"]

    def test_search_by_key_no_match_returns_empty(self, tmp_path):
        store = _make_store(tmp_path)
        store.upsert_memory(_make_record(key="unrelated_key", title="t"))
        assert store.search_memories("nothing_here") == []

    def test_search_by_keywords_json(self, tmp_path):
        """A keyword stored only in keywords_json (not title/content/tags) is searchable."""
        store = _make_store(tmp_path)
        store.upsert_memory(_make_record(
            key="a",
            title="setup",
            content="bootstrap",
            tags=["init"],
            keywords=["fastapi", "uvicorn", "postgres"],
        ))
        results = store.search_memories("uvicorn")
        assert len(results) == 1

    def test_search_dedupes_when_match_in_multiple_columns(self, tmp_path):
        """A query that matches title, key_normalized, tag, and keyword on the
        same record returns it once (DISTINCT)."""
        store = _make_store(tmp_path)
        store.upsert_memory(_make_record(
            key="fastapi_setup",
            title="fastapi installation",
            content="install fastapi",
            tags=["fastapi"],
            keywords=["fastapi", "asgi"],
        ))
        results = store.search_memories("fastapi")
        assert len(results) == 1

    def test_search_unified_finds_record_via_any_field(self, tmp_path):
        """Five separate records, each only matching a different field, all surface."""
        store = _make_store(tmp_path)
        store.upsert_memory(_make_record(key="a", title="alpha redis", content="x", tags=[], keywords=[]))
        store.upsert_memory(_make_record(key="b", title="beta", content="bravo redis charlie", tags=[], keywords=[]))
        store.upsert_memory(_make_record(key="c_redis_cluster", title="cee", content="x", tags=[], keywords=[]))
        store.upsert_memory(_make_record(key="d", title="delta", content="x", tags=["redis"], keywords=[]))
        store.upsert_memory(_make_record(key="e", title="echo", content="x", tags=[], keywords=["redis"]))
        results = store.search_memories("redis")
        assert len(results) == 5

    def test_filter_by_tag(self, tmp_path):
        store = _make_store(tmp_path)
        store.upsert_memory(_make_record(key="a", tags=["python", "backend"]))
        store.upsert_memory(_make_record(key="b", tags=["frontend"]))
        results = store.filter_by_tag("python")
        assert len(results) == 1

    def test_filter_by_tag_case_insensitive(self, tmp_path):
        store = _make_store(tmp_path)
        store.upsert_memory(_make_record(key="a", tags=["Python"]))
        results = store.filter_by_tag("python")
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Soft delete
# ---------------------------------------------------------------------------


class TestSoftDelete:
    def test_soft_delete(self, tmp_path):
        store = _make_store(tmp_path)
        rec = _make_record()
        store.upsert_memory(rec)
        assert store.soft_delete_memory(rec.id) is True
        assert store.get_memory_by_id(rec.id) is None
        assert store.list_memories() == []

    def test_soft_delete_nonexistent(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.soft_delete_memory("nonexistent") is False

    def test_soft_delete_idempotent(self, tmp_path):
        store = _make_store(tmp_path)
        rec = _make_record()
        store.upsert_memory(rec)
        assert store.soft_delete_memory(rec.id) is True
        assert store.soft_delete_memory(rec.id) is False

    def test_clear_memories_all(self, tmp_path):
        store = _make_store(tmp_path)
        for i in range(3):
            store.upsert_memory(_make_record(key=f"k{i}"))
        count = store.clear_memories()
        assert count == 3
        assert store.list_memories() == []

    def test_clear_memories_by_scope(self, tmp_path):
        store = _make_store(tmp_path)
        store.upsert_memory(_make_record(scope="project", key="p"))
        store.upsert_memory(_make_record(scope="user", key="u"))
        count = store.clear_memories(scope="project")
        assert count == 1
        remaining = store.list_memories()
        assert len(remaining) == 1
        assert remaining[0].scope == "user"


# ---------------------------------------------------------------------------
# Session summaries
# ---------------------------------------------------------------------------


class TestSessionSummaries:
    def test_save_and_list(self, tmp_path):
        store = _make_store(tmp_path)
        summary = SessionSummaryRecord(
            id="sum1",
            session_id="sess1",
            summary_text="The user discussed testing.",
            tokens_before=500,
            messages_summarized=10,
            created_at="2026-04-08T10:00:00",
        )
        store.save_session_summary(summary)
        results = store.list_session_summaries(session_id="sess1")
        assert len(results) == 1
        assert results[0].summary_text == "The user discussed testing."

    def test_list_by_session_id(self, tmp_path):
        store = _make_store(tmp_path)
        for i, sid in enumerate(["s1", "s1", "s2"]):
            store.save_session_summary(SessionSummaryRecord(
                id=f"sum{i}",
                session_id=sid,
                summary_text=f"summary {i}",
                tokens_before=0,
                messages_summarized=0,
                created_at=f"2026-04-08T10:0{i}:00",
            ))
        s1 = store.list_session_summaries(session_id="s1")
        s2 = store.list_session_summaries(session_id="s2")
        assert len(s1) == 2
        assert len(s2) == 1

    def test_list_all_with_limit(self, tmp_path):
        store = _make_store(tmp_path)
        for i in range(10):
            store.save_session_summary(SessionSummaryRecord(
                id=f"s{i}",
                session_id="sess",
                summary_text=f"text {i}",
                tokens_before=0,
                messages_summarized=0,
                created_at=f"2026-04-08T10:{i:02d}:00",
            ))
        results = store.list_session_summaries(limit=5)
        assert len(results) == 5

    def test_clear_session_summaries(self, tmp_path):
        store = _make_store(tmp_path)
        store.save_session_summary(SessionSummaryRecord(
            id="s1", session_id="sess1", summary_text="x",
            tokens_before=0, messages_summarized=0, created_at="2026-04-08T10:00:00",
        ))
        store.save_session_summary(SessionSummaryRecord(
            id="s2", session_id="sess2", summary_text="y",
            tokens_before=0, messages_summarized=0, created_at="2026-04-08T10:00:00",
        ))
        count = store.clear_session_summaries(session_id="sess1")
        assert count == 1
        assert len(store.list_session_summaries()) == 1

    def test_clear_all_session_summaries(self, tmp_path):
        store = _make_store(tmp_path)
        for i in range(3):
            store.save_session_summary(SessionSummaryRecord(
                id=f"s{i}", session_id="sess", summary_text="x",
                tokens_before=0, messages_summarized=0, created_at="2026-04-08T10:00:00",
            ))
        count = store.clear_session_summaries()
        assert count == 3


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------


def _make_review_item(
    scope="project",
    type_="preference",
    key="preference_dark_mode",
    title="Preference: dark mode",
    content="dark mode",
    item_id=None,
    occurrences=1,
    status="pending",
) -> MemoryReviewItem:
    return MemoryReviewItem(
        id=item_id or uuid.uuid4().hex[:12],
        scope=scope,
        type=type_,
        key_normalized=key,
        title=title,
        content=content,
        tags=[type_],
        evidence="user said: I prefer dark mode",
        source_session="sess1",
        occurrences=occurrences,
        confidence="auto_summary",
        status=status,
        created_at="2026-04-08T10:00:00",
        updated_at="2026-04-08T10:00:00",
    )


class TestReviewQueue:
    def test_upsert_inserts_new_item(self, tmp_path):
        store = _make_store(tmp_path)
        item = _make_review_item()
        saved = store.upsert_review_item(item)
        assert saved.id == item.id
        assert saved.status == "pending"
        assert saved.occurrences == 1

    def test_upsert_increments_occurrences_on_duplicate(self, tmp_path):
        store = _make_store(tmp_path)
        item1 = _make_review_item(item_id="a1")
        store.upsert_review_item(item1)
        # Same scope/key but different id → should fold into existing pending row
        item2 = _make_review_item(item_id="a2")
        saved = store.upsert_review_item(item2)
        assert saved.id == "a1"  # original id preserved
        assert saved.occurrences == 2

    def test_upsert_raises_confidence_on_repetition(self, tmp_path):
        store = _make_store(tmp_path)
        store.upsert_review_item(_make_review_item(item_id="a1"))
        saved = store.upsert_review_item(_make_review_item(item_id="a2"))
        assert saved.confidence == "inferred"

    def test_upsert_partial_field_change_only_updates_diff(self, tmp_path):
        """Folding still works when only one of the presentation fields changes
        — the other fields take the new value (which equals the old) and the
        row stays consistent."""
        store = _make_store(tmp_path)
        first = MemoryReviewItem(
            id="x1",
            scope="project",
            type="preference",
            key_normalized="preference_dark_mode",
            title="Preference: dark mode",
            content="dark mode",
            tags=["preference"],
            evidence="user said: I prefer dark mode",
            source_session="sess1",
            occurrences=1,
            confidence="auto_summary",
            status="pending",
            created_at="2026-04-08T10:00:00",
            updated_at="2026-04-08T10:00:00",
        )
        store.upsert_review_item(first)

        # Only `tags` changes; everything else identical
        second = MemoryReviewItem(
            id="x2",
            scope="project",
            type="preference",
            key_normalized="preference_dark_mode",
            title="Preference: dark mode",
            content="dark mode",
            tags=["preference", "ui"],  # extra tag
            evidence="user said: I prefer dark mode",
            source_session="sess1",
            occurrences=1,
            confidence="auto_summary",
            status="pending",
            created_at="2026-04-08T10:00:00",
            updated_at="2026-04-08T10:00:00",
        )
        saved = store.upsert_review_item(second)
        assert saved.id == "x1"
        assert saved.tags == ["preference", "ui"]
        assert saved.title == "Preference: dark mode"
        assert saved.content == "dark mode"
        assert saved.occurrences == 2

    def test_upsert_does_not_fold_into_approved_item(self, tmp_path):
        """Once a review item is approved (or rejected), the unique index no
        longer matches and a new pending row is created instead of mutating
        the historical decision."""
        store = _make_store(tmp_path)
        original = _make_review_item(item_id="orig", key="preference_fish")
        store.upsert_review_item(original)
        store.update_review_status("orig", "approved")

        # Same scope+key arrives again later
        duplicate = _make_review_item(item_id="dup", key="preference_fish")
        saved = store.upsert_review_item(duplicate)

        # The approved row is untouched
        approved = store.get_review_item("orig")
        assert approved is not None
        assert approved.status == "approved"
        assert approved.occurrences == 1

        # And a new pending row exists for the same key
        assert saved.id == "dup"
        assert saved.status == "pending"
        assert saved.occurrences == 1

        # Both rows visible if we list by status
        assert len(store.list_review_items(status="pending")) == 1
        assert len(store.list_review_items(status="approved")) == 1

    def test_upsert_does_not_fold_into_rejected_item(self, tmp_path):
        """Same rule for rejected: a new pending row is created."""
        store = _make_store(tmp_path)
        store.upsert_review_item(_make_review_item(item_id="orig", key="preference_x"))
        store.update_review_status("orig", "rejected")

        store.upsert_review_item(_make_review_item(item_id="dup", key="preference_x"))

        rejected = store.get_review_item("orig")
        assert rejected.status == "rejected"
        assert len(store.list_review_items(status="pending")) == 1
        assert len(store.list_review_items(status="rejected")) == 1

    def test_crystallize_user_messages_refreshes_existing_pending_item(self, tmp_path):
        """Manager-level: re-running crystallize when the user repeats the
        same preference folds into the existing pending row, refreshes
        evidence/source_session, increments occurrences, and raises
        confidence to ``inferred`` — instead of stacking duplicates."""
        from agentao.memory.manager import MemoryManager
        mgr = MemoryManager(project_root=tmp_path / ".agentao", global_root=None)

        # First extraction — first session
        mgr.crystallize_user_messages([{
            "role": "user",
            "content": "I prefer dark mode",
        }])
        items_v1 = mgr.list_review_items()
        assert len(items_v1) == 1
        first_id = items_v1[0].id
        assert items_v1[0].content == "dark mode"
        assert items_v1[0].confidence == "auto_summary"

        # User repeats the same preference in a later message (same key,
        # same captured content, possibly different surrounding context).
        mgr.crystallize_user_messages([{
            "role": "user",
            "content": "Reminder: I prefer dark mode",
        }])
        items_v2 = mgr.list_review_items()
        # Folded into one row, not stacked
        assert len(items_v2) == 1
        assert items_v2[0].id == first_id
        # Counters reflect the second hit
        assert items_v2[0].occurrences == 2
        assert items_v2[0].confidence == "inferred"

    def test_upsert_refreshes_presentation_fields_on_duplicate(self, tmp_path):
        """Re-hitting the same (scope, key) folds new title/content/tags/type
        into the existing pending row — the reviewer must always see the
        latest extraction, not the first one."""
        store = _make_store(tmp_path)
        original = MemoryReviewItem(
            id="orig",
            scope="project",
            type="preference",
            key_normalized="preference_dark_mode",
            title="Preference: dark mode",
            content="dark mode",
            tags=["preference"],
            evidence="user said: I prefer dark mode",
            source_session="sess1",
            occurrences=1,
            confidence="auto_summary",
            status="pending",
            created_at="2026-04-08T10:00:00",
            updated_at="2026-04-08T10:00:00",
        )
        store.upsert_review_item(original)

        # Refined re-extraction: longer phrasing, richer tags, type changed
        refined = MemoryReviewItem(
            id="dup",
            scope="project",  # same scope+key → folds into the original
            type="constraint",  # type reclassified
            key_normalized="preference_dark_mode",
            title="Preference: dark mode in every editor",
            content="dark mode in every editor",
            tags=["preference", "ui", "editor"],
            evidence="user said: I prefer dark mode in every editor",
            source_session="sess2",
            occurrences=1,
            confidence="auto_summary",
            status="pending",
            created_at="2026-04-09T10:00:00",
            updated_at="2026-04-09T10:00:00",
        )
        saved = store.upsert_review_item(refined)

        # Original row id is preserved
        assert saved.id == "orig"
        # All presentation fields refreshed to the new extraction
        assert saved.type == "constraint"
        assert saved.title == "Preference: dark mode in every editor"
        assert saved.content == "dark mode in every editor"
        assert saved.tags == ["preference", "ui", "editor"]
        assert saved.evidence == "user said: I prefer dark mode in every editor"
        assert saved.source_session == "sess2"
        # Counters and confidence still behave as before
        assert saved.occurrences == 2
        assert saved.confidence == "inferred"
        # Identity / lifecycle fields are preserved
        assert saved.scope == "project"
        assert saved.key_normalized == "preference_dark_mode"
        assert saved.created_at == "2026-04-08T10:00:00"
        assert saved.status == "pending"

    def test_get_review_item_by_id(self, tmp_path):
        store = _make_store(tmp_path)
        item = _make_review_item(item_id="abc")
        store.upsert_review_item(item)
        fetched = store.get_review_item("abc")
        assert fetched is not None
        assert fetched.title == item.title

    def test_get_review_item_missing_returns_none(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.get_review_item("nope") is None

    def test_list_review_items_default_pending(self, tmp_path):
        store = _make_store(tmp_path)
        store.upsert_review_item(_make_review_item(item_id="a", key="preference_a"))
        store.upsert_review_item(_make_review_item(item_id="b", key="preference_b"))
        items = store.list_review_items()
        assert len(items) == 2
        assert all(it.status == "pending" for it in items)

    def test_list_review_items_filters_by_status(self, tmp_path):
        store = _make_store(tmp_path)
        store.upsert_review_item(_make_review_item(item_id="a", key="preference_a"))
        store.upsert_review_item(_make_review_item(item_id="b", key="preference_b"))
        store.update_review_status("a", "approved")

        pending = store.list_review_items(status="pending")
        assert [it.id for it in pending] == ["b"]
        approved = store.list_review_items(status="approved")
        assert [it.id for it in approved] == ["a"]

    def test_list_review_items_ordered_by_occurrences_desc(self, tmp_path):
        store = _make_store(tmp_path)
        # First item: 1 occurrence
        store.upsert_review_item(_make_review_item(item_id="lo", key="preference_lo"))
        # Second item: 3 occurrences (insert + 2 duplicates)
        store.upsert_review_item(_make_review_item(item_id="hi", key="preference_hi"))
        store.upsert_review_item(_make_review_item(item_id="hi2", key="preference_hi"))
        store.upsert_review_item(_make_review_item(item_id="hi3", key="preference_hi"))
        items = store.list_review_items()
        assert items[0].key_normalized == "preference_hi"
        assert items[0].occurrences == 3

    def test_update_review_status_to_approved(self, tmp_path):
        store = _make_store(tmp_path)
        store.upsert_review_item(_make_review_item(item_id="x"))
        ok = store.update_review_status("x", "approved")
        assert ok is True
        assert store.get_review_item("x").status == "approved"

    def test_update_review_status_to_rejected(self, tmp_path):
        store = _make_store(tmp_path)
        store.upsert_review_item(_make_review_item(item_id="x"))
        ok = store.update_review_status("x", "rejected")
        assert ok is True
        assert store.get_review_item("x").status == "rejected"

    def test_update_review_status_missing_returns_false(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.update_review_status("nope", "approved") is False


