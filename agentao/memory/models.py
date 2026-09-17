"""Data models for the memory subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Literal

# --- Type aliases ---
MemoryScope = Literal["user", "project"]
MemoryType = Literal["preference", "profile", "project_fact", "workflow", "decision", "constraint", "note"]
MemorySource = Literal["explicit", "auto", "crystallized"]
MemoryConfidence = Literal["explicit_user", "inferred", "auto_summary"]
MemorySensitivity = Literal["normal", "sensitive", "blocked"]
ReviewStatus = Literal["pending", "approved", "rejected"]
# What a hard wipe can leave behind. The two values are the strings the CLI
# has always printed; they are spelled out here so a host can branch on them.
MemoryWipeTarget = Literal["memories", "session summaries"]

# --- Constants ---
STABLE_BLOCK_MAX_CHARS = 2000
MAX_AUTO_ENTRIES_PER_SCOPE = 50
SESSION_TAIL_CHARS = 800
DYNAMIC_RECALL_MAX_TOKENS = 300
DYNAMIC_RECALL_MAX_CHARS = DYNAMIC_RECALL_MAX_TOKENS * 4  # ~4 chars/token proxy


@dataclass
class SaveMemoryRequest:
    """Inbound request to save or update a memory entry."""

    key: str
    value: str
    tags: List[str] = field(default_factory=list)
    scope: MemoryScope | None = None
    type: MemoryType | None = None
    source: MemorySource = "explicit"


@dataclass
class MemoryRecord:
    """A persisted memory entry backed by SQLite."""

    id: str
    scope: MemoryScope
    type: MemoryType
    key_normalized: str
    title: str
    content: str
    tags: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    source: MemorySource = "explicit"
    confidence: MemoryConfidence = "explicit_user"
    sensitivity: MemorySensitivity = "normal"
    created_at: str = ""
    updated_at: str = ""
    deleted_at: str | None = None


@dataclass
class SessionSummaryRecord:
    """A single session compaction summary."""

    id: str
    session_id: str
    summary_text: str
    tokens_before: int
    messages_summarized: int
    created_at: str


@dataclass
class RecallCandidate:
    """A scored recall hit returned by the retriever."""

    memory_id: str
    scope: MemoryScope
    type: MemoryType
    title: str
    excerpt: str
    score: float
    reasons: List[str] = field(default_factory=list)


@dataclass
class CrystallizationProposal:
    """In-memory candidate produced by the rule-based crystallizer.

    Not persisted directly — the manager submits proposals into the
    `memory_review_queue` table as MemoryReviewItem rows.
    """

    scope: MemoryScope
    type: MemoryType  # only preference / constraint / decision / workflow
    key_normalized: str
    title: str
    content: str
    tags: List[str] = field(default_factory=list)
    evidence: str = ""           # excerpt of the summary that triggered the match
    source_session: str = ""     # session_id of the originating summary
    occurrences: int = 1
    confidence: MemoryConfidence = "auto_summary"


@dataclass
class MemoryReviewItem:
    """A persisted crystallization candidate awaiting user approval."""

    id: str
    scope: MemoryScope
    type: MemoryType
    key_normalized: str
    title: str
    content: str
    tags: List[str] = field(default_factory=list)
    evidence: str = ""
    source_session: str = ""
    occurrences: int = 1
    confidence: MemoryConfidence = "auto_summary"
    status: ReviewStatus = "pending"
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class MemoryWipeResult:
    """Outcome of :meth:`agentao.memory.MemoryManager.wipe_all`.

    ``ok`` is the success signal, **not** the counts. A count says how many
    rows were confirmed deleted, and 0 is the honest answer both for a store
    that was already empty and for one whose delete failed. ``not_cleared``
    names each part still in place — including a part that could not be read
    *back*, since a store that cannot be read cannot confirm it is empty.
    """

    memories_cleared: int = 0
    summaries_cleared: int = 0
    not_cleared: tuple[MemoryWipeTarget, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether every part was confirmed cleared."""
        return not self.not_cleared
