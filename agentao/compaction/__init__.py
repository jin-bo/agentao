"""Compaction orchestration — vocabulary types, and (later) the coordinator.

Only ``types`` is re-exported here. ``coordinator`` must **not** be, even
once it exists: this ``__init__`` runs on *every* import of anything in the
package, so re-exporting the coordinator would drag ``coordinator`` ->
``context_manager`` -> the LLM stack in behind a bare
``from agentao.compaction.types import CompactionKind`` — which
``agentao/plugins/hooks/_payload.py`` does, and which is why ``types``
itself imports nothing but the standard library.

Note what this is **not**: ``agentao.host`` does not re-export these types
(``docs/reference/host-api.md`` points hosts at ``agentao.compaction.types``
directly), so import-layering rule 5 — "``import agentao.host`` must not
drag in the runtime or the LLM stack" — does not reach this file and cannot
catch a regression here. The rule above is held by review, not by a test.
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
