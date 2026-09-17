"""MemoryManager: persistent-memory orchestration for Agentao.

Routes reads/writes across one project-scope and one optional
user-scope :class:`MemoryStore`, validates through ``MemoryGuard``,
and maintains a ``write_version`` counter for dirty-flag detection by
callers. The manager itself is storage-agnostic: it never imports
:mod:`sqlite3` and never knows about ``Path`` / disk I/O.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Literal, Optional

from .guards import MemoryGuard, SensitiveMemoryError
from .models import (
    MAX_AUTO_ENTRIES_PER_SCOPE,
    SESSION_TAIL_CHARS,
    MemoryRecord,
    MemoryReviewItem,
    MemoryWipeResult,
    MemoryWipeTarget,
    SaveMemoryRequest,
    SessionSummaryRecord,
)

if TYPE_CHECKING:
    from ..capabilities.memory import MemoryStore

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


class MemoryManager:
    """Manages all Agentao memory layers via injected stores.

    Takes pre-built :class:`MemoryStore` instances rather than disk
    paths; the embedding factory (or any host) constructs the stores
    and passes them in. CLI / ACP wiring lives in
    ``agentao.embedding.build_from_environment``; tests construct
    stores via :meth:`SQLiteMemoryStore.open_or_memory` /
    :meth:`SQLiteMemoryStore.open`.

    Args:
        project_store: project-scope persistent store (always present).
        user_store: optional cross-project user-scope store. ``None``
            downgrades user-scope writes to project scope (matches the
            pre-#16 behavior when ``global_root`` was ``None``), and says so
            in the log rather than silently — see :meth:`upsert`.
        guard: optional :class:`MemoryGuard`; defaults to a fresh one.
    """

    def __init__(
        self,
        project_store: "MemoryStore",
        user_store: Optional["MemoryStore"] = None,
        guard: Optional[MemoryGuard] = None,
    ) -> None:
        self.project_store = project_store
        self.user_store = user_store
        self.guard = guard or MemoryGuard()

        # Session tracking
        self._session_id: str = uuid.uuid4().hex[:12]

        # Monotonic counter incremented on every mutating operation.
        # One manager is now written from more than one thread — a sub-agent's
        # ``save_memory`` writes through its *parent's* manager, and a
        # background sub-agent does it from its own thread (#260) — and
        # ``+= 1`` on an attribute is a read-modify-write the interpreter may
        # split between the load and the store. A lost increment leaves
        # ``MemoryRetriever`` recalling a stale index, so the bump takes a lock.
        self._write_version: int = 0
        self._write_version_lock = threading.Lock()

    @property
    def write_version(self) -> int:
        """Increments on every save/delete/clear -- use for dirty-flag detection."""
        return self._write_version

    def _bump_write_version(self) -> None:
        with self._write_version_lock:
            self._write_version += 1

    def close(self) -> None:
        """Release both stores' resources. Safe to call more than once.

        Only the transient ``:memory:`` backing actually holds a connection between
        calls; this exists so a host can let go of it deterministically rather than
        waiting for the collector.
        """
        for store in (self.project_store, self.user_store):
            closer = getattr(store, "close", None)
            if closer is None:
                continue
            try:
                closer()
            except Exception:   # a store that cannot close must not fail a shutdown
                logger.debug("memory store close failed", exc_info=True)

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
        # Downgrade user scope to project when no user store is configured.
        # The behaviour stays — bare construction is project-scope-only by
        # design, and a library embedder needs the write to land somewhere —
        # but it is no longer silent: nothing in the logs distinguished "saved
        # as project because you asked" from "saved as project because this
        # manager has no user store" (#260).
        #
        # An explicit ``scope="user"`` is a request that was not honoured, so
        # it warns. An inferred one is the classifier's reading of a key or a
        # tag (``user_`` prefix, ``preference`` / ``profile`` tag), which on a
        # project-only manager is the ordinary case rather than a fault and
        # would warn on a large share of writes — that one goes to
        # ``agentao.log`` at debug instead. Neither records ``key`` or
        # ``value``: the requested and the actual scope are the whole point,
        # and the content is what the memory guard exists to keep out of logs.
        if scope == "user" and self.user_store is None:
            scope = "project"
            if request.scope == "user":
                logger.warning(
                    "save_memory asked for user scope and was saved to project "
                    "scope: this MemoryManager has no user store configured",
                )
            else:
                logger.debug(
                    "memory classified as user scope, saved to project scope: "
                    "no user store configured",
                )
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
        self._bump_write_version()

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
            self._bump_write_version()
            return True
        if self.user_store and self.user_store.soft_delete_memory(entry_id):
            self._bump_write_version()
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
        try:
            if scope is None or scope == "project":
                count += self.project_store.clear_memories(scope="project")
            if (scope is None or scope == "user") and self.user_store:
                count += self.user_store.clear_memories(scope="user")
        finally:
            # Bumped even when the user store raises after the project store
            # committed: ``MemoryRetriever`` rebuilds its index only on a
            # version change, so skipping the bump would keep recalling the
            # project rows that are already gone.
            if count:
                self._bump_write_version()
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

        This method does NOT crystallize. Crystallization runs beside it in
        ``ContextManager.commit_compaction()`` against the raw user messages
        that are about to be summarized away — the LLM-generated summary text
        contains assistant narration and is not safe to regex against. Both
        writes live in commit rather than before summarization, so a
        compaction that is cancelled or whose summarization fails leaves the
        store untouched.
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

        Returns the number of rows deleted (0 on failure). A caller that must
        report the outcome cannot tell those two zeros apart — ask
        :meth:`session_summaries_remain` instead.
        """
        try:
            return self.project_store.clear_session_summaries(session_id=None)
        except Exception:
            logger.warning("clearing all session summaries failed", exc_info=True)
            return 0

    def session_summaries_remain(self) -> bool:
        """Whether any session summary, from any session, is still stored.

        The check behind a hard reset's success message: a surviving row is
        not inert, it resurfaces in the next prompt through
        :meth:`get_cross_session_tail`. A store that cannot be read answers
        ``True`` — it cannot confirm the summaries are gone.
        """
        try:
            return bool(self.project_store.list_session_summaries(session_id=None, limit=1))
        except Exception:
            return True

    # =========================================================================
    # Hard reset
    # =========================================================================

    def wipe_all(self) -> MemoryWipeResult:
        """Clear every memory and every session summary. **Never raises.**

        The hard reset behind ``/clear`` and ``/memory clear``, on the manager
        so an embedded host performs the same operation and reads the same
        documented result (#235). A storage failure comes back *in the
        result*, never as an exception and never as a zero: read
        :attr:`~agentao.memory.models.MemoryWipeResult.ok`, because neither
        count can carry that on its own —
        :meth:`clear_all_session_summaries` answers 0 both for "nothing to
        delete" and for "the delete failed", which is why the summaries are
        confirmed by reading the store back rather than by the count.

        Runs the second half even when the first fails: ``/clear`` calls this
        mid-reset, and a raise there would abandon the reset after the history
        is gone but before the permission mode is restored.

        **Clears** persistent memories in both scopes (via :meth:`clear`) and
        session summaries from every session (via
        :meth:`clear_all_session_summaries`).

        **The memories half is a soft delete and is therefore not erasure.**
        Every row keeps its ``content`` and stays in the database file with
        ``deleted_at`` set; no read path returns it again, but ``ok`` being
        true does not mean the text is gone. A host building a "forget me"
        guarantee on this needs its own hard delete or file-level disposal.
        The summaries half is a real ``DELETE``.

        **Does not clear the review queue** (``memory_review_queue``): the
        crystallizer's pending candidates carry excerpts of the messages they
        were extracted from and stay visible to ``/memory review`` after a
        wipe. :meth:`reject_review_item` is the only remedy, one item at a
        time.

        The answer is true as of the read. A background sub-agent's
        ``save_memory`` writes through this manager (#260), and a second
        process can write to the same project store, so a memory saved after
        the wipe is not a failure of it.
        """
        not_cleared: list[MemoryWipeTarget] = []

        memories = 0
        try:
            memories = self.clear()
        except Exception:
            # ``clear`` deliberately does not swallow — it is the one part of
            # this whose failure the caller could already see — so this is
            # where that exception becomes a reportable outcome. A user store
            # that raises after the project store committed still counts as
            # not cleared: part of the memories are still there.
            logger.warning("clearing memories failed", exc_info=True)
            not_cleared.append("memories")

        summaries = 0
        try:
            summaries = self.clear_all_session_summaries()
            remain = self.session_summaries_remain()
        except Exception:
            # Both of those swallow their own errors today, so this is for a
            # subclass or an injected store that raises somewhere they do not
            # expect — the "never raises" promise above has to be structural.
            logger.warning("clearing session summaries failed", exc_info=True)
            remain = True
        if remain:
            not_cleared.append("session summaries")

        return MemoryWipeResult(
            memories_cleared=memories,
            summaries_cleared=summaries,
            not_cleared=tuple(not_cleared),
        )

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

        - ``ContextManager.commit_compaction()`` — passes the about-to-be-
          summarized window so we crystallize before the messages are gone.
          It runs only once a summary exists, so a failed summarization no
          longer writes.
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

    def _store_for_scope(self, scope: str) -> "MemoryStore":
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
