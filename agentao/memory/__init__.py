"""Memory management subpackage for Agentao.

Note: the :class:`MemoryStore` Protocol lives in
:mod:`agentao.capabilities.memory` and is re-exported from
:mod:`agentao.capabilities` only — pulling it through
``agentao.memory`` here would force ``import agentao.memory`` to load
the rest of ``agentao.capabilities`` (which after Issue #17 includes
the MCP registry concretes that drag the MCP SDK). Keeping memory
import-light is asserted by ``tests/test_memory_decoupling.py``.
"""

from .manager import MemoryManager
from .retriever import MemoryRetriever
from .crystallizer import MemoryCrystallizer, SkillCrystallizer
from .storage import SQLiteMemoryStore
from .guards import MemoryGuard, SensitiveMemoryError
from .render import MemoryPromptRenderer
from .models import (
    CrystallizationProposal,
    MemoryRecord,
    MemoryReviewItem,
    SaveMemoryRequest,
    SessionSummaryRecord,
    RecallCandidate,
)

__all__ = [
    "MemoryManager",
    "MemoryRetriever",
    "MemoryCrystallizer",
    "SkillCrystallizer",
    "SQLiteMemoryStore",
    "MemoryGuard",
    "SensitiveMemoryError",
    "MemoryPromptRenderer",
    "MemoryRecord",
    "MemoryReviewItem",
    "CrystallizationProposal",
    "SaveMemoryRequest",
    "SessionSummaryRecord",
    "RecallCandidate",
]
