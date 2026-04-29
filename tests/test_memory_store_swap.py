"""Issue #16 — MemoryManager routes through an injected MemoryStore.

A swappable MemoryStore means embedded hosts can back memory with any
storage (Redis, Postgres, in-process dict, remote API). The tests
below confirm wire-up: a fake store captures every call and the
manager never reaches for SQLite when a fake is injected.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from agentao.capabilities import MemoryStore  # re-export
from agentao.memory import MemoryManager, SaveMemoryRequest
from agentao.memory.models import (
    MemoryRecord,
    MemoryReviewItem,
    SessionSummaryRecord,
)


# ---------------------------------------------------------------------------
# In-memory test double
# ---------------------------------------------------------------------------


class InMemoryMemoryStore:
    """Pure-Python :class:`MemoryStore` for tests + acceptance criteria.

    Round-trips the model dataclasses through three dicts and records
    every call into ``self.calls`` so tests can assert routing without
    SQLite participation. Search/filter implementations match the
    semantic the SQLite store offers (case-insensitive substring on
    title+content+key+tags+keywords for ``search_memories``;
    case-insensitive exact match on tag for ``filter_by_tag``).
    """

    def __init__(self) -> None:
        self.calls: List[str] = []
        self._memories: Dict[str, MemoryRecord] = {}
        self._summaries: Dict[str, SessionSummaryRecord] = {}
        self._reviews: Dict[str, MemoryReviewItem] = {}

    # --- Memory CRUD ------------------------------------------------------

    def upsert_memory(self, record: MemoryRecord) -> MemoryRecord:
        self.calls.append(f"upsert_memory:{record.scope}:{record.key_normalized}")
        # Mirror SQLite "(scope, key_normalized) is unique among non-deleted"
        for mid, existing in self._memories.items():
            if (
                existing.scope == record.scope
                and existing.key_normalized == record.key_normalized
                and existing.deleted_at is None
            ):
                # Update path: keep id + created_at, refresh everything else.
                merged = MemoryRecord(
                    id=mid,
                    scope=record.scope,
                    type=record.type,
                    key_normalized=record.key_normalized,
                    title=record.title,
                    content=record.content,
                    tags=list(record.tags),
                    keywords=list(record.keywords),
                    source=record.source,
                    confidence=record.confidence,
                    sensitivity=record.sensitivity,
                    created_at=existing.created_at,
                    updated_at=record.updated_at,
                    deleted_at=None,
                )
                self._memories[mid] = merged
                return merged
        self._memories[record.id] = record
        return record

    def get_memory_by_id(self, memory_id: str) -> Optional[MemoryRecord]:
        self.calls.append(f"get_memory_by_id:{memory_id}")
        rec = self._memories.get(memory_id)
        return rec if rec and rec.deleted_at is None else None

    def get_memory_by_scope_key(
        self, scope: str, key_normalized: str
    ) -> Optional[MemoryRecord]:
        self.calls.append(f"get_memory_by_scope_key:{scope}:{key_normalized}")
        for rec in self._memories.values():
            if (
                rec.scope == scope
                and rec.key_normalized == key_normalized
                and rec.deleted_at is None
            ):
                return rec
        return None

    def list_memories(self, scope: Optional[str] = None) -> List[MemoryRecord]:
        self.calls.append(f"list_memories:{scope}")
        out = [
            r for r in self._memories.values()
            if r.deleted_at is None and (scope is None or r.scope == scope)
        ]
        out.sort(key=lambda r: r.created_at)
        return out

    def search_memories(
        self, query: str, scope: Optional[str] = None
    ) -> List[MemoryRecord]:
        self.calls.append(f"search_memories:{query}:{scope}")
        q = query.lower()
        out: List[MemoryRecord] = []
        for r in self._memories.values():
            if r.deleted_at is not None:
                continue
            if scope is not None and r.scope != scope:
                continue
            haystacks = [
                r.title.lower(),
                r.content.lower(),
                r.key_normalized.lower(),
                *[t.lower() for t in r.tags],
                *[k.lower() for k in r.keywords],
            ]
            if any(q in h for h in haystacks):
                out.append(r)
        out.sort(key=lambda r: r.created_at)
        return out

    def filter_by_tag(
        self, tag: str, scope: Optional[str] = None
    ) -> List[MemoryRecord]:
        self.calls.append(f"filter_by_tag:{tag}:{scope}")
        target = tag.lower()
        out: List[MemoryRecord] = []
        for r in self._memories.values():
            if r.deleted_at is not None:
                continue
            if scope is not None and r.scope != scope:
                continue
            if any(t.lower() == target for t in r.tags):
                out.append(r)
        out.sort(key=lambda r: r.created_at)
        return out

    def soft_delete_memory(self, memory_id: str) -> bool:
        self.calls.append(f"soft_delete_memory:{memory_id}")
        rec = self._memories.get(memory_id)
        if rec is None or rec.deleted_at is not None:
            return False
        rec.deleted_at = "2026-04-29T00:00:00"
        return True

    def clear_memories(self, scope: Optional[str] = None) -> int:
        self.calls.append(f"clear_memories:{scope}")
        count = 0
        for r in self._memories.values():
            if r.deleted_at is None and (scope is None or r.scope == scope):
                r.deleted_at = "2026-04-29T00:00:00"
                count += 1
        return count

    # --- Session summaries ------------------------------------------------

    def save_session_summary(self, record: SessionSummaryRecord) -> None:
        self.calls.append(f"save_session_summary:{record.session_id}")
        self._summaries[record.id] = record

    def list_session_summaries(
        self, session_id: Optional[str] = None, limit: int = 20
    ) -> List[SessionSummaryRecord]:
        self.calls.append(f"list_session_summaries:{session_id}:{limit}")
        out = [
            s for s in self._summaries.values()
            if session_id is None or s.session_id == session_id
        ]
        out.sort(key=lambda s: s.created_at, reverse=True)
        return out[:limit]

    def clear_session_summaries(self, session_id: Optional[str] = None) -> int:
        self.calls.append(f"clear_session_summaries:{session_id}")
        if session_id is None:
            count = len(self._summaries)
            self._summaries.clear()
            return count
        targets = [k for k, s in self._summaries.items() if s.session_id == session_id]
        for k in targets:
            del self._summaries[k]
        return len(targets)

    # --- Review queue -----------------------------------------------------

    def upsert_review_item(self, item: MemoryReviewItem) -> MemoryReviewItem:
        self.calls.append(f"upsert_review_item:{item.scope}:{item.key_normalized}")
        # Fold pending duplicates by (scope, key_normalized).
        for existing in self._reviews.values():
            if (
                existing.scope == item.scope
                and existing.key_normalized == item.key_normalized
                and existing.status == "pending"
            ):
                existing.occurrences += max(item.occurrences, 1)
                existing.title = item.title
                existing.content = item.content
                existing.tags = list(item.tags)
                existing.evidence = item.evidence
                existing.source_session = item.source_session
                if existing.occurrences >= 2:
                    existing.confidence = "inferred"
                return existing
        self._reviews[item.id] = item
        return item

    def get_review_item(self, item_id: str) -> Optional[MemoryReviewItem]:
        self.calls.append(f"get_review_item:{item_id}")
        return self._reviews.get(item_id)

    def list_review_items(
        self, status: Optional[str] = "pending", limit: int = 50
    ) -> List[MemoryReviewItem]:
        self.calls.append(f"list_review_items:{status}:{limit}")
        items = [
            i for i in self._reviews.values()
            if status is None or i.status == status
        ]
        items.sort(key=lambda i: (-i.occurrences, i.created_at), reverse=False)
        return items[:limit]

    def update_review_status(self, item_id: str, status: str) -> bool:
        self.calls.append(f"update_review_status:{item_id}:{status}")
        item = self._reviews.get(item_id)
        if item is None:
            return False
        item.status = status
        return True


# Static type check: confirm InMemoryMemoryStore satisfies the Protocol.
def _assert_protocol_compatible(store: InMemoryMemoryStore) -> MemoryStore:
    return store  # if this typechecks, the duck-type contract holds


# ---------------------------------------------------------------------------
# Manager-routing tests
# ---------------------------------------------------------------------------


def _save(mgr: MemoryManager, key: str, value: str = "v", scope: Optional[str] = None) -> None:
    mgr.upsert(SaveMemoryRequest(key=key, value=value, tags=[], scope=scope))


def test_manager_routes_writes_to_injected_store():
    """``MemoryManager.upsert`` calls ``store.upsert_memory`` exactly once."""
    fake = InMemoryMemoryStore()
    mgr = MemoryManager(project_store=fake)
    _save(mgr, "alpha", "first")
    upserts = [c for c in fake.calls if c.startswith("upsert_memory:")]
    assert len(upserts) == 1
    assert "alpha" in upserts[0]


def test_manager_with_no_user_store_downgrades_user_scope():
    """``user_store=None`` plus an explicit ``scope='user'`` write must
    land in the project store (manager.py:120)."""
    fake = InMemoryMemoryStore()
    mgr = MemoryManager(project_store=fake)
    _save(mgr, "user_pref", "value", scope="user")
    project_entries = mgr.get_all_entries(scope="project")
    assert any(e.title == "user_pref" for e in project_entries)
    assert mgr.get_all_entries(scope="user") == []


def test_manager_search_unions_project_and_user_stores():
    """``search`` over both stores returns union when both are present."""
    project = InMemoryMemoryStore()
    user = InMemoryMemoryStore()
    mgr = MemoryManager(project_store=project, user_store=user)
    _save(mgr, "shared_topic_proj", "alpha")
    _save(mgr, "shared_topic_user", "beta", scope="user")
    results = mgr.search("shared_topic")
    titles = {r.title for r in results}
    assert "shared_topic_proj" in titles
    assert "shared_topic_user" in titles


def test_manager_filter_by_tag_unions_both_stores():
    project = InMemoryMemoryStore()
    user = InMemoryMemoryStore()
    mgr = MemoryManager(project_store=project, user_store=user)
    mgr.upsert(SaveMemoryRequest(key="proj_a", value="v", tags=["important"]))
    mgr.upsert(
        SaveMemoryRequest(key="user_b", value="v", tags=["important"], scope="user")
    )
    results = mgr.filter_by_tag("important")
    titles = {r.title for r in results}
    assert "proj_a" in titles
    assert "user_b" in titles


def test_manager_session_summary_cycle():
    """save → list → clear round-trips through the fake."""
    fake = InMemoryMemoryStore()
    mgr = MemoryManager(project_store=fake)
    mgr.save_session_summary("first.", tokens_before=10, messages_summarized=2)
    mgr.save_session_summary("second.", tokens_before=15, messages_summarized=3)
    summaries = mgr.get_recent_session_summaries(limit=10)
    assert {s.summary_text for s in summaries} == {"first.", "second."}
    cleared = mgr.clear_all_session_summaries()
    assert cleared == 2
    assert mgr.get_recent_session_summaries() == []


def test_manager_review_queue_promote_cycle():
    """A review item routed via the manager promotes correctly."""
    fake = InMemoryMemoryStore()
    mgr = MemoryManager(project_store=fake)

    item = MemoryReviewItem(
        id="rev1",
        scope="project",
        type="preference",
        key_normalized="dark_mode",
        title="dark mode",
        content="Likes dark mode.",
        tags=["preference"],
        evidence="user message",
        source_session="sess1",
        occurrences=1,
        confidence="auto_summary",
        status="pending",
        created_at="2026-04-29T00:00:00",
        updated_at="2026-04-29T00:00:00",
    )
    fake.upsert_review_item(item)

    promoted = mgr.approve_review_item("rev1")
    assert promoted is not None
    assert promoted.key_normalized == "dark_mode"
    # Status flipped via the same store
    assert fake.get_review_item("rev1").status == "approved"


def test_manager_does_not_open_any_sqlite_db(monkeypatch):
    """With an injected fake store, ``sqlite3.connect`` is never called."""
    import sqlite3
    real_connect = sqlite3.connect
    seen: list = []

    def trap(*args, **kwargs):
        seen.append(args)
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", trap)

    fake = InMemoryMemoryStore()
    mgr = MemoryManager(project_store=fake)
    _save(mgr, "alpha", "first")
    mgr.search("alpha")
    mgr.delete_by_title("alpha")
    mgr.clear()

    assert seen == [], f"Manager unexpectedly opened SQLite: {seen}"
