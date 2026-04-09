"""SQLite-backed memory storage."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import List, Optional

from .models import MemoryRecord, MemoryReviewItem, SessionSummaryRecord

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 3

_INIT_SQL = """\
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    id              TEXT PRIMARY KEY,
    scope           TEXT NOT NULL,
    type            TEXT NOT NULL DEFAULT 'note',
    key_normalized  TEXT NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    tags_json       TEXT NOT NULL DEFAULT '[]',
    keywords_json   TEXT NOT NULL DEFAULT '[]',
    source          TEXT NOT NULL DEFAULT 'explicit',
    confidence      TEXT NOT NULL DEFAULT 'explicit_user',
    sensitivity     TEXT NOT NULL DEFAULT 'normal',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    deleted_at      TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uix_memories_scope_key
    ON memories(scope, key_normalized) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS session_summaries (
    id                   TEXT PRIMARY KEY,
    session_id           TEXT NOT NULL,
    summary_text         TEXT NOT NULL,
    tokens_before        INTEGER NOT NULL DEFAULT 0,
    messages_summarized  INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id  TEXT NOT NULL,
    event_type TEXT NOT NULL,
    timestamp  TEXT NOT NULL,
    detail     TEXT
);

CREATE TABLE IF NOT EXISTS memory_review_queue (
    id              TEXT PRIMARY KEY,
    scope           TEXT NOT NULL,
    type            TEXT NOT NULL,
    key_normalized  TEXT NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    tags_json       TEXT NOT NULL DEFAULT '[]',
    evidence        TEXT NOT NULL DEFAULT '',
    source_session  TEXT NOT NULL DEFAULT '',
    occurrences     INTEGER NOT NULL DEFAULT 1,
    confidence      TEXT NOT NULL DEFAULT 'auto_summary',
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uix_review_scope_key
    ON memory_review_queue(scope, key_normalized) WHERE status='pending';
"""


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


class SQLiteMemoryStore:
    """Single-file SQLite store for memory records and session summaries."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._is_memory = db_path == ":memory:"
        self._persistent_conn: Optional[sqlite3.Connection] = None
        self._init_db()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._is_memory:
            # For in-memory DBs, reuse a single connection to preserve data
            if self._persistent_conn is None:
                self._persistent_conn = sqlite3.connect(":memory:")
                self._persistent_conn.row_factory = sqlite3.Row
            return self._persistent_conn
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_INIT_SQL)
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
                ("version", str(_SCHEMA_VERSION)),
            )
            # Drop columns removed in schema v2 from existing databases.
            # ALTER TABLE … DROP COLUMN requires SQLite >= 3.35.0; silently skip on older.
            for col in ("pinned", "ttl_days", "expires_at"):
                try:
                    conn.execute(f"ALTER TABLE memories DROP COLUMN {col}")
                except Exception:
                    pass
            conn.commit()

    # ------------------------------------------------------------------
    # Memory CRUD
    # ------------------------------------------------------------------

    def upsert_memory(self, record: MemoryRecord) -> MemoryRecord:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM memories WHERE scope=? AND key_normalized=? AND deleted_at IS NULL",
                (record.scope, record.key_normalized),
            ).fetchone()

            now = _now_iso()
            if existing:
                memory_id = existing["id"]
                conn.execute(
                    """UPDATE memories SET
                        title=?, content=?, tags_json=?, keywords_json=?,
                        type=?, source=?, confidence=?, sensitivity=?,
                        updated_at=?
                    WHERE id=?""",
                    (
                        record.title, record.content,
                        json.dumps(record.tags), json.dumps(record.keywords),
                        record.type, record.source, record.confidence,
                        record.sensitivity, now,
                        memory_id,
                    ),
                )
                event_type = "update"
            else:
                memory_id = record.id
                conn.execute(
                    """INSERT INTO memories
                        (id, scope, type, key_normalized, title, content,
                         tags_json, keywords_json, source, confidence,
                         sensitivity, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        record.id, record.scope, record.type,
                        record.key_normalized, record.title, record.content,
                        json.dumps(record.tags), json.dumps(record.keywords),
                        record.source, record.confidence, record.sensitivity,
                        record.created_at or now, now,
                    ),
                )
                event_type = "create"

            conn.execute(
                "INSERT INTO memory_events (memory_id, event_type, timestamp) VALUES (?,?,?)",
                (memory_id, event_type, now),
            )
            conn.commit()

        return self.get_memory_by_id(memory_id)  # type: ignore[return-value]

    def get_memory_by_id(self, memory_id: str) -> Optional[MemoryRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE id=? AND deleted_at IS NULL", (memory_id,)
            ).fetchone()
        return self._row_to_memory(row) if row else None

    def get_memory_by_scope_key(self, scope: str, key_normalized: str) -> Optional[MemoryRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE scope=? AND key_normalized=? AND deleted_at IS NULL",
                (scope, key_normalized),
            ).fetchone()
        return self._row_to_memory(row) if row else None

    def list_memories(self, scope: Optional[str] = None) -> List[MemoryRecord]:
        with self._connect() as conn:
            if scope:
                rows = conn.execute(
                    "SELECT * FROM memories WHERE scope=? AND deleted_at IS NULL ORDER BY created_at ASC",
                    (scope,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM memories WHERE deleted_at IS NULL ORDER BY created_at ASC",
                ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def search_memories(self, query: str, scope: Optional[str] = None) -> List[MemoryRecord]:
        """Case-insensitive search pushed down to SQLite via LIKE + json_each.

        Searches across five fields so the CLI ``/memory search`` and any
        future search consumers cover the same surface that ``MemoryRetriever``
        scores against:

        - ``title``           (LIKE)
        - ``content``         (LIKE)
        - ``key_normalized``  (LIKE — exposes entries the user saved with
          a descriptive key but a terse title)
        - ``tags_json``       (json_each, exact LIKE on each tag value)
        - ``keywords_json``   (json_each, exact LIKE on each extracted keyword)
        """
        pattern = f"%{query.lower()}%"
        with self._connect() as conn:
            if scope:
                rows = conn.execute(
                    """SELECT DISTINCT m.*
                       FROM memories m
                       LEFT JOIN json_each(m.tags_json) AS t
                       LEFT JOIN json_each(m.keywords_json) AS k
                       WHERE m.deleted_at IS NULL
                         AND m.scope = ?
                         AND (LOWER(m.title) LIKE ?
                              OR LOWER(m.content) LIKE ?
                              OR LOWER(m.key_normalized) LIKE ?
                              OR LOWER(t.value) LIKE ?
                              OR LOWER(k.value) LIKE ?)
                       ORDER BY m.created_at ASC""",
                    (scope, pattern, pattern, pattern, pattern, pattern),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT DISTINCT m.*
                       FROM memories m
                       LEFT JOIN json_each(m.tags_json) AS t
                       LEFT JOIN json_each(m.keywords_json) AS k
                       WHERE m.deleted_at IS NULL
                         AND (LOWER(m.title) LIKE ?
                              OR LOWER(m.content) LIKE ?
                              OR LOWER(m.key_normalized) LIKE ?
                              OR LOWER(t.value) LIKE ?
                              OR LOWER(k.value) LIKE ?)
                       ORDER BY m.created_at ASC""",
                    (pattern, pattern, pattern, pattern, pattern),
                ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def filter_by_tag(self, tag: str, scope: Optional[str] = None) -> List[MemoryRecord]:
        """Exact tag match (case-insensitive) pushed down to SQLite via json_each."""
        with self._connect() as conn:
            if scope:
                rows = conn.execute(
                    """SELECT DISTINCT m.*
                       FROM memories m
                       JOIN json_each(m.tags_json) AS t
                       WHERE m.deleted_at IS NULL
                         AND m.scope = ?
                         AND LOWER(t.value) = LOWER(?)
                       ORDER BY m.created_at ASC""",
                    (scope, tag),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT DISTINCT m.*
                       FROM memories m
                       JOIN json_each(m.tags_json) AS t
                       WHERE m.deleted_at IS NULL
                         AND LOWER(t.value) = LOWER(?)
                       ORDER BY m.created_at ASC""",
                    (tag,),
                ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def soft_delete_memory(self, memory_id: str) -> bool:
        now = _now_iso()
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE memories SET deleted_at=?, updated_at=? WHERE id=? AND deleted_at IS NULL",
                (now, now, memory_id),
            ).rowcount
            if changed:
                conn.execute(
                    "INSERT INTO memory_events (memory_id, event_type, timestamp) VALUES (?,?,?)",
                    (memory_id, "soft_delete", now),
                )
            conn.commit()
        return bool(changed)

    def clear_memories(self, scope: Optional[str] = None) -> int:
        now = _now_iso()
        with self._connect() as conn:
            if scope:
                changed = conn.execute(
                    "UPDATE memories SET deleted_at=?, updated_at=? WHERE scope=? AND deleted_at IS NULL",
                    (now, now, scope),
                ).rowcount
            else:
                changed = conn.execute(
                    "UPDATE memories SET deleted_at=?, updated_at=? WHERE deleted_at IS NULL",
                    (now, now),
                ).rowcount
            conn.commit()
        return changed

    # ------------------------------------------------------------------
    # Session summaries
    # ------------------------------------------------------------------

    def save_session_summary(self, record: SessionSummaryRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO session_summaries
                    (id, session_id, summary_text, tokens_before, messages_summarized, created_at)
                VALUES (?,?,?,?,?,?)""",
                (
                    record.id, record.session_id, record.summary_text,
                    record.tokens_before, record.messages_summarized,
                    record.created_at,
                ),
            )
            conn.commit()

    def list_session_summaries(
        self, session_id: Optional[str] = None, limit: int = 20
    ) -> List[SessionSummaryRecord]:
        with self._connect() as conn:
            if session_id:
                rows = conn.execute(
                    "SELECT * FROM session_summaries WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM session_summaries ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_to_summary(r) for r in rows]

    def clear_session_summaries(self, session_id: Optional[str] = None) -> int:
        with self._connect() as conn:
            if session_id:
                changed = conn.execute(
                    "DELETE FROM session_summaries WHERE session_id=?", (session_id,)
                ).rowcount
            else:
                changed = conn.execute("DELETE FROM session_summaries").rowcount
            conn.commit()
        return changed

    # ------------------------------------------------------------------
    # Review queue
    # ------------------------------------------------------------------

    def upsert_review_item(self, item: MemoryReviewItem) -> MemoryReviewItem:
        """Insert a new pending review item OR, if a pending row already exists
        for the same ``(scope, key_normalized)``, fold the new hit into it.

        Folding refreshes **all mutable presentation fields** so the reviewer
        always sees the latest extraction (not the first one):

        - ``title``, ``content``, ``tags_json``, ``type`` — replaced
        - ``evidence``, ``source_session`` — replaced (latest excerpt)
        - ``occurrences`` — incremented
        - ``confidence`` — auto-raised to ``inferred`` once occurrences ≥ 2
        - ``updated_at`` — touched

        ``id``, ``scope``, ``key_normalized``, ``created_at``, ``status`` are
        preserved.
        """
        now = _now_iso()
        with self._connect() as conn:
            existing = conn.execute(
                """SELECT id, occurrences FROM memory_review_queue
                   WHERE scope=? AND key_normalized=? AND status='pending'""",
                (item.scope, item.key_normalized),
            ).fetchone()

            if existing:
                new_occ = int(existing["occurrences"]) + max(item.occurrences, 1)
                # Auto-raise confidence on repetition
                new_conf = "inferred" if new_occ >= 2 else item.confidence
                conn.execute(
                    """UPDATE memory_review_queue SET
                        type=?, title=?, content=?, tags_json=?,
                        occurrences=?, evidence=?, confidence=?,
                        source_session=?, updated_at=?
                    WHERE id=?""",
                    (
                        item.type, item.title, item.content, json.dumps(item.tags),
                        new_occ, item.evidence, new_conf,
                        item.source_session, now, existing["id"],
                    ),
                )
                conn.commit()
                return self.get_review_item(existing["id"])  # type: ignore[return-value]

            conn.execute(
                """INSERT INTO memory_review_queue
                    (id, scope, type, key_normalized, title, content,
                     tags_json, evidence, source_session, occurrences,
                     confidence, status, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item.id, item.scope, item.type, item.key_normalized,
                    item.title, item.content, json.dumps(item.tags),
                    item.evidence, item.source_session, item.occurrences,
                    item.confidence, item.status,
                    item.created_at or now, now,
                ),
            )
            conn.commit()
        return self.get_review_item(item.id)  # type: ignore[return-value]

    def get_review_item(self, item_id: str) -> Optional[MemoryReviewItem]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_review_queue WHERE id=?", (item_id,)
            ).fetchone()
        return self._row_to_review(row) if row else None

    def list_review_items(
        self, status: Optional[str] = "pending", limit: int = 50
    ) -> List[MemoryReviewItem]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    """SELECT * FROM memory_review_queue
                       WHERE status=? ORDER BY occurrences DESC, created_at DESC LIMIT ?""",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM memory_review_queue
                       ORDER BY created_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
        return [self._row_to_review(r) for r in rows]

    def update_review_status(self, item_id: str, status: str) -> bool:
        now = _now_iso()
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE memory_review_queue SET status=?, updated_at=? WHERE id=?",
                (status, now, item_id),
            ).rowcount
            conn.commit()
        return bool(changed)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            scope=row["scope"],
            type=row["type"],
            key_normalized=row["key_normalized"],
            title=row["title"],
            content=row["content"],
            tags=json.loads(row["tags_json"]),
            keywords=json.loads(row["keywords_json"]),
            source=row["source"],
            confidence=row["confidence"],
            sensitivity=row["sensitivity"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            deleted_at=row["deleted_at"],
        )

    @staticmethod
    def _row_to_summary(row: sqlite3.Row) -> SessionSummaryRecord:
        return SessionSummaryRecord(
            id=row["id"],
            session_id=row["session_id"],
            summary_text=row["summary_text"],
            tokens_before=row["tokens_before"],
            messages_summarized=row["messages_summarized"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_review(row: sqlite3.Row) -> MemoryReviewItem:
        return MemoryReviewItem(
            id=row["id"],
            scope=row["scope"],
            type=row["type"],
            key_normalized=row["key_normalized"],
            title=row["title"],
            content=row["content"],
            tags=json.loads(row["tags_json"]),
            evidence=row["evidence"],
            source_session=row["source_session"],
            occurrences=int(row["occurrences"]),
            confidence=row["confidence"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

