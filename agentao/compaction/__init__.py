"""Compaction orchestration — vocabulary types, and (later) the coordinator.

Only ``types`` is re-exported here. ``coordinator`` must **not** be, even
once it exists: ``agentao.host`` re-exports the public subset of this
package, and dragging ``coordinator`` -> ``context_manager`` -> the LLM
stack in through this ``__init__`` would trip import-layering rule 5
(``tests/test_import_layering.py``, "``import agentao.host`` must not drag
in the runtime or the LLM stack").
"""

from __future__ import annotations

from .types import (
    CompactionController,
    CompactionDecision,
    CompactionDecisionContext,
    CompactionKind,
    CompactionOutcome,
    CompactionReason,
    CompactionTrigger,
)

__all__ = [
    "CompactionController",
    "CompactionDecision",
    "CompactionDecisionContext",
    "CompactionKind",
    "CompactionOutcome",
    "CompactionReason",
    "CompactionTrigger",
]
