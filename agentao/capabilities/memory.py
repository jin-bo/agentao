"""MemoryStore capability protocol.

Embedded hosts inject this protocol to redirect persistent-memory,
session-summary, and review-queue persistence through their own
storage (Redis, Postgres, in-process dict, remote API) without
subclassing or forking :class:`agentao.memory.MemoryManager`. The
default :class:`agentao.memory.storage.SQLiteMemoryStore` keeps
byte-equivalent behavior with the pre-capability code.

Concrete classes (e.g. ``SQLiteMemoryStore``) live next to the rest of
the persistence subsystem in ``agentao/memory/``; only the abstract
contract sits here, mirroring how ``ShellExecutor`` is co-located with
``LocalShellExecutor`` but has no schema-aware logic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Protocol

if TYPE_CHECKING:
    # Annotations only — kept TYPE_CHECKING to break the import cycle
    # between ``agentao.capabilities`` (re-exports ``SQLiteMemoryStore``)
    # and ``agentao.memory`` (re-exports ``MemoryStore``). With
    # ``from __future__ import annotations`` all annotations are
    # strings, so these are never resolved at import time.
    from ..memory.models import (
        MemoryRecord,
        MemoryReviewItem,
        SessionSummaryRecord,
    )


class MemoryStore(Protocol):
    """Persistent memory contract.

    Schema-less: implementations only need to round-trip the model
    dataclasses (:class:`MemoryRecord`, :class:`SessionSummaryRecord`,
    :class:`MemoryReviewItem`). No SQL, no row factories, no
    connection objects bleed across this boundary.

    Soft-delete semantics: ``upsert_memory`` / ``list_memories`` /
    ``search_memories`` / ``filter_by_tag`` / ``get_memory_by_id`` /
    ``get_memory_by_scope_key`` exclude soft-deleted rows.
    ``soft_delete_memory`` / ``clear_memories`` set ``deleted_at`` on
    rows; they do NOT physically remove. Implementations that lack a
    soft-delete primitive should mark the record and filter on read.

    Lifecycle: :class:`MemoryManager` does not call ``close``; the
    factory or embedded host owns the store's lifetime.
    """

    # --- Memory CRUD -----------------------------------------------------
    def upsert_memory(self, record: MemoryRecord) -> MemoryRecord: ...
    def get_memory_by_id(self, memory_id: str) -> Optional[MemoryRecord]: ...
    def get_memory_by_scope_key(
        self, scope: str, key_normalized: str
    ) -> Optional[MemoryRecord]: ...
    def list_memories(self, scope: Optional[str] = None) -> List[MemoryRecord]: ...
    def search_memories(
        self, query: str, scope: Optional[str] = None
    ) -> List[MemoryRecord]: ...
    def filter_by_tag(
        self, tag: str, scope: Optional[str] = None
    ) -> List[MemoryRecord]: ...
    def soft_delete_memory(self, memory_id: str) -> bool: ...
    def clear_memories(self, scope: Optional[str] = None) -> int: ...

    # --- Session summaries -----------------------------------------------
    def save_session_summary(self, record: SessionSummaryRecord) -> None: ...
    def list_session_summaries(
        self, session_id: Optional[str] = None, limit: int = 20
    ) -> List[SessionSummaryRecord]: ...
    def clear_session_summaries(self, session_id: Optional[str] = None) -> int: ...

    # --- Review queue ----------------------------------------------------
    def upsert_review_item(self, item: MemoryReviewItem) -> MemoryReviewItem: ...
    def get_review_item(self, item_id: str) -> Optional[MemoryReviewItem]: ...
    def list_review_items(
        self, status: Optional[str] = "pending", limit: int = 50
    ) -> List[MemoryReviewItem]: ...
    def update_review_status(self, item_id: str, status: str) -> bool: ...
