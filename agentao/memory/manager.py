"""MemoryManager: SQLite-backed persistent memory for Agentao.

Orchestrates SQLiteMemoryStore instances (project + optional user scope),
validates through MemoryGuard, and maintains a write_version counter for
dirty-flag detection by callers.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional

from .guards import MemoryGuard, SensitiveMemoryError
from .models import (
    MAX_AUTO_ENTRIES_PER_SCOPE,
    SESSION_TAIL_CHARS,
    MemoryRecord,
    MemoryReviewItem,
    SaveMemoryRequest,
    SessionSummaryRecord,
)
from .storage import SQLiteMemoryStore

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


class MemoryManager:
    """Manages all Agentao memory layers via SQLite.

    project_root: cwd/.agentao   (project-scope DB lives here)
    global_root:  ~/.agentao     (user-scope DB lives here; None = no user scope)
    """

    def __init__(
        self,
        project_root: Path,
        global_root: Optional[Path] = None,
        guard: Optional[MemoryGuard] = None,
    ) -> None:
        self._project_root = Path(project_root)
        self._global_root = Path(global_root) if global_root else None
        self.guard = guard or MemoryGuard()

        # Initialize SQLite stores. The catch intentionally covers both
        # ``OSError`` (mkdir / permission failures) and ``sqlite3.Error``
        # (e.g. ``sqlite3.OperationalError: unable to open database file``
        # raised from within ``SQLiteMemoryStore.__init__`` when the target
        # directory exists but is not writable — common in ACP subprocess
        # launches and other restricted environments). Both branches degrade
        # gracefully rather than crashing ``Agentao()`` construction.
        try:
            self._project_root.mkdir(parents=True, exist_ok=True)
            self.project_store = SQLiteMemoryStore(
                str(self._project_root / "memory.db")
            )
        except (OSError, sqlite3.Error) as exc:
            # Fallback: in-memory store when filesystem is not writable
            logger.warning(
                "Project memory store at %s unavailable (%s: %s); "
                "falling back to transient in-memory store.",
                self._project_root / "memory.db",
                type(exc).__name__,
                exc,
            )
            self.project_store = SQLiteMemoryStore(":memory:")

        self.user_store: Optional[SQLiteMemoryStore] = None
        if self._global_root:
            try:
                self._global_root.mkdir(parents=True, exist_ok=True)
                self.user_store = SQLiteMemoryStore(
                    str(self._global_root / "memory.db")
                )
            except (OSError, sqlite3.Error) as exc:
                logger.warning(
                    "User memory store at %s unavailable (%s: %s); "
                    "user-scope memory disabled for this session.",
                    self._global_root / "memory.db",
                    type(exc).__name__,
                    exc,
                )

        # Session tracking
        self._session_id: str = uuid.uuid4().hex[:12]

        # Monotonic counter incremented on every mutating operation
        self._write_version: int = 0

    @property
    def write_version(self) -> int:
        """Increments on every save/delete/clear -- use for dirty-flag detection."""
        return self._write_version

    # =========================================================================
    # High-level upsert (used by save_from_tool and future guard pipeline)
    # =========================================================================

    def upsert(self, request: SaveMemoryRequest) -> MemoryRecord:
        """Validate, classify, and persist a memory entry.

        Raises:
            ValueError: if key or content fails validation
            SensitiveMemoryError: if content contains secrets
        """
        normalized = self.guard.normalize_key(request.key)
        title = self.guard.validate_title(request.key)
        content = self.guard.validate_content(request.value)
        self.guard.detect_sensitive(content)

        scope = self.guard.classify_scope(normalized, request.tags, request.scope)
        # Downgrade user scope to project when no user store is configured
        if scope == "user" and self.user_store is None:
            scope = "project"
        type_ = self.guard.classify_type(normalized, request.tags, request.type)
        keywords = self.guard.extract_keywords(title, request.tags, content)

        now = _now()
        store = self._store_for_scope(scope)
        existing = store.get_memory_by_scope_key(scope, normalized)

        record = MemoryRecord(
            id=existing.id if existing else uuid.uuid4().hex[:8],
            scope=scope,
            type=type_,
            key_normalized=normalized,
            title=title,
            content=content,
            tags=request.tags,
            keywords=keywords,
            source=request.source,
            confidence="explicit_user" if request.source == "explicit" else "auto_summary",
            sensitivity="normal",
            created_at=existing.created_at if existing else now,
            updated_at=now,
            deleted_at=None,
        )

        saved = store.upsert_memory(record)
        self._write_version += 1

        # Enforce auto-entry limit
        if request.source == "auto":
            self._enforce_auto_limit(scope)

        return saved

    # =========================================================================
    # Tool interface
    # =========================================================================

    def save_from_tool(
        self,
        key: str,
        value: str,
        tags: List[str],
        scope: Optional[str] = None,
        type: Optional[str] = None,
    ) -> str:
        """Route a save_memory LLM tool call through the store."""
        try:
            saved = self.upsert(
                SaveMemoryRequest(
                    key=key,
                    value=value,
                    tags=tags or [],
                    scope=scope,
                    type=type,
                )
            )
            return f"Saved memory: {saved.key_normalized}"
        except SensitiveMemoryError as e:
            return str(e)
        except Exception as e:
            return f"Error saving memory: {e}"

    # =========================================================================
    # Read operations
    # =========================================================================

    def get_entry(self, entry_id: str) -> Optional[MemoryRecord]:
        """Return entry by id from either store."""
        rec = self.project_store.get_memory_by_id(entry_id)
        if rec:
            return rec
        if self.user_store:
            return self.user_store.get_memory_by_id(entry_id)
        return None

    def get_all_entries(
        self, scope: Optional[Literal["user", "project"]] = None
    ) -> List[MemoryRecord]:
        """Return all entries, optionally filtered by scope. Pinned first, then by created_at."""
        if scope == "user":
            return self.user_store.list_memories(scope="user") if self.user_store else []
        if scope == "project":
            return self.project_store.list_memories(scope="project")

        result = self.project_store.list_memories()
        if self.user_store:
            result += self.user_store.list_memories()
        result.sort(key=lambda e: e.created_at)
        return result

    def search(self, query: str, scope: Optional[str] = None) -> List[MemoryRecord]:
        """Case-insensitive search over title, content, and tags."""
        if scope == "user" and self.user_store:
            return self.user_store.search_memories(query, scope="user")
        if scope == "project":
            return self.project_store.search_memories(query, scope="project")

        results = self.project_store.search_memories(query)
        if self.user_store:
            results += self.user_store.search_memories(query)
        return results

    def filter_by_tag(self, tag: str, scope: Optional[str] = None) -> List[MemoryRecord]:
        """Return entries that have the given tag (case-insensitive)."""
        if scope == "user" and self.user_store:
            return self.user_store.filter_by_tag(tag, scope="user")
        if scope == "project":
            return self.project_store.filter_by_tag(tag, scope="project")

        results = self.project_store.filter_by_tag(tag)
        if self.user_store:
            results += self.user_store.filter_by_tag(tag)
        return results

    # =========================================================================
    # Delete operations
    # =========================================================================

    def delete(self, entry_id: str) -> bool:
        """Soft-delete an entry by id. Returns True if found and deleted."""
        if self.project_store.soft_delete_memory(entry_id):
            self._write_version += 1
            return True
        if self.user_store and self.user_store.soft_delete_memory(entry_id):
            self._write_version += 1
            return True
        return False

    def delete_by_title(self, title: str) -> int:
        """Delete all entries whose title matches (case-insensitive). Returns count."""
        count = 0
        for e in self.get_all_entries():
            if e.title.lower() == title.lower():
                if self.delete(e.id):
                    count += 1
        return count

    def clear(self, scope: Optional[str] = None) -> int:
        """Soft-delete all entries in given scope (None = both). Returns count deleted."""
        count = 0
        if scope is None or scope == "project":
            count += self.project_store.clear_memories(scope="project")
        if (scope is None or scope == "user") and self.user_store:
            count += self.user_store.clear_memories(scope="user")
        if count:
            self._write_version += 1
        return count

    # =========================================================================
    # Session summaries
    # =========================================================================

    def save_session_summary(
        self,
        summary: str,
        tokens_before: int = 0,
        messages_summarized: int = 0,
    ) -> None:
        """Persist a compact summary block to SQLite. Never raises.

        This method does NOT crystallize. Crystallization runs upstream in
        ``ContextManager.compress_messages()`` against the raw user messages
        that are about to be summarized away — the LLM-generated summary text
        contains assistant narration and is not safe to regex against.
        """
        try:
            record = SessionSummaryRecord(
                id=uuid.uuid4().hex[:12],
                session_id=self._session_id,
                summary_text=summary.strip(),
                tokens_before=tokens_before,
                messages_summarized=messages_summarized,
                created_at=_now(),
            )
            self.project_store.save_session_summary(record)
        except Exception:
            return

    def get_recent_session_summaries(
        self,
        session_id: Optional[str] = None,
        limit: int = 5,
    ) -> List[SessionSummaryRecord]:
        sid = session_id or self._session_id
        return self.project_store.list_session_summaries(session_id=sid, limit=limit)

    def archive_session(self) -> Optional[str]:
        """Start a new session. Returns old session_id if there were summaries."""
        old_id = self._session_id
        summaries = self.project_store.list_session_summaries(session_id=old_id, limit=1)
        self._session_id = uuid.uuid4().hex[:12]
        if summaries:
            return old_id
        return None

    def clear_session(self) -> None:
        """Delete all session summaries for the **current** session only.

        Low-level primitive — does NOT advance ``_session_id``. Useful when
        a caller wants to discard the current session's in-progress
        summaries without starting a new session.

        ``/new`` does NOT use this: it relies on
        :meth:`archive_session` (called from
        ``AgentaoCLI.on_session_start``) which advances the session id
        without deleting old rows, leaving them visible to
        :meth:`get_cross_session_tail`.

        ``/clear`` does NOT use this either: it uses
        :meth:`clear_all_session_summaries` for a hard reset.
        """
        try:
            self.project_store.clear_session_summaries(session_id=self._session_id)
        except Exception:
            pass

    def clear_all_session_summaries(self) -> int:
        """Delete every session summary across **all** sessions.

        Use this for ``/clear`` and ``/memory clear`` — a hard reset that must
        also strip the cross-session tail; otherwise summaries from prior
        sessions would silently resurface in the next prompt via
        ``get_cross_session_tail()``.

        Returns the number of rows deleted (0 on failure).
        """
        try:
            return self.project_store.clear_session_summaries(session_id=None)
        except Exception:
            return 0

    # =========================================================================
    # Private helpers
    # =========================================================================

    def get_stable_entries(self, recent_project_limit: int = 3) -> List[MemoryRecord]:
        """Return the subset of entries that belong in the stable prompt block.

        Selection policy (evaluated in priority order, deduped):

        1. User-scope entries — always included (cross-project preferences/profile).
        2. Project-scope *structural* types — always included:
           decision, constraint, workflow, profile, preference.
        3. Project-scope *incidental* types (project_fact, note) — only the
           ``recent_project_limit`` most-recently-updated entries; the rest
           surface via dynamic recall when relevant.

        Final order: created_at ascending.
        """
        _STRUCTURAL = frozenset({"decision", "constraint", "workflow", "profile", "preference"})

        seen: set[str] = set()
        stable: list[MemoryRecord] = []
        incidental: list[MemoryRecord] = []

        for r in self.get_all_entries():  # created_at asc
            if r.id in seen:
                continue
            if r.scope == "user" or r.type in _STRUCTURAL:
                seen.add(r.id)
                stable.append(r)
            else:
                incidental.append(r)

        # Take the most-recently-updated incidental project entries
        incidental.sort(key=lambda r: r.updated_at, reverse=True)
        for r in incidental[:recent_project_limit]:
            if r.id not in seen:
                seen.add(r.id)
                stable.append(r)

        stable.sort(key=lambda r: r.created_at)
        return stable

    # =========================================================================
    # Crystallization (review queue facade)
    # =========================================================================

    def crystallize_user_messages(self, messages: list) -> List[MemoryReviewItem]:
        """Run the rule-based crystallizer over **raw user messages** and
        submit any proposals to the review queue.

        Used by:

        - ``ContextManager.compress_messages()`` — passes the about-to-be-
          summarized window so we crystallize before the messages are gone.
        - ``/memory crystallize`` (CLI) — passes ``self.agent.messages`` so
          the user can manually re-run extraction over the live conversation
          buffer.

        Crystallization never touches LLM-narrated summary text; only
        ``role == "user"`` messages are scanned.
        """
        from .crystallizer import MemoryCrystallizer
        crystallizer = MemoryCrystallizer()
        proposals = crystallizer.extract_from_user_messages(messages, self._session_id)
        if not proposals:
            return []
        return crystallizer.submit_to_review(proposals, self)

    def list_review_items(self, status: str = "pending") -> List[MemoryReviewItem]:
        return self.project_store.list_review_items(status=status)

    def approve_review_item(self, item_id: str) -> Optional[MemoryRecord]:
        """Promote a pending review item into live memories. Returns None if
        the item does not exist or is no longer pending."""
        from .crystallizer import MemoryCrystallizer
        item = self.project_store.get_review_item(item_id)
        if not item or item.status != "pending":
            return None
        crystallizer = MemoryCrystallizer()
        return crystallizer.promote(item, self)

    def reject_review_item(self, item_id: str) -> bool:
        """Mark a review item as rejected. Returns True if a row was updated."""
        item = self.project_store.get_review_item(item_id)
        if not item or item.status != "pending":
            return False
        return self.project_store.update_review_status(item_id, "rejected")

    def _store_for_scope(self, scope: str) -> SQLiteMemoryStore:
        if scope == "user" and self.user_store is not None:
            return self.user_store
        return self.project_store

    def get_cross_session_tail(self) -> str:
        """Return a formatted tail of summaries from previous sessions (not the current one).

        Current session's summaries already live in self.messages as [Conversation Summary]
        blocks — they need no separate channel. Summaries from previous sessions have no
        other path to the LLM after a restart, so this method surfaces them for
        cross-session continuity via the <memory-stable> block.

        Returns empty string when there are no prior-session summaries.
        """
        try:
            all_recent = self.project_store.list_session_summaries(session_id=None, limit=10)
            cross = [s for s in all_recent if s.session_id != self._session_id]
            if not cross:
                return ""
            # most-recent-first from DB; take up to 3, reverse to chronological for display
            texts = [s.summary_text for s in cross[:3]]
            combined = "\n\n---\n\n".join(reversed(texts))
            if len(combined) > SESSION_TAIL_CHARS:
                combined = combined[-SESSION_TAIL_CHARS:]
                nl = combined.find("\n")
                if nl != -1:
                    combined = combined[nl + 1:]
            return combined.strip()
        except Exception:
            return ""

    def _enforce_auto_limit(self, scope: str) -> None:
        store = self._store_for_scope(scope)
        records = store.list_memories(scope=scope)
        auto = [r for r in records if r.source == "auto"]
        if len(auto) > MAX_AUTO_ENTRIES_PER_SCOPE:
            auto.sort(key=lambda r: r.updated_at)
            to_remove = auto[: len(auto) - MAX_AUTO_ENTRIES_PER_SCOPE]
            for r in to_remove:
                store.soft_delete_memory(r.id)
