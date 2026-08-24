"""Compaction vocabulary and contract types — standard library only.

This module is deliberately **leaf-shaped**: it imports nothing from
``agentao`` and nothing outside the standard library. ``context_manager.py``
and the coordinator both need these names, and defining them anywhere else
would force one of those two modules to import the other — the dependency
direction ``docs/design/compaction-orchestration-plan.md`` §4.2.1 pins is
that ``ContextManager`` never learns about the coordinator.

The three aliases below are the **whole** vocabulary. ``trigger`` stays
``manual | auto`` for Claude Code parity (§3.2): a host rule written
``{"trigger": "manual|auto"}`` must keep matching every entry point, so the
finer provenance lives in ``kind`` and ``reason`` instead of subdividing
``trigger``.
"""

from __future__ import annotations

from typing import Literal

#: Where the compaction came from, as the PreCompact matcher sees it.
#: Claude Code parity — do not subdivide ``auto``.
CompactionTrigger = Literal["manual", "auto"]

#: Which transform is about to run. Today's ``compaction_type`` field.
CompactionKind = Literal["microcompact", "full", "minimal_history"]

#: Which condition asked for it. Today's ``reason`` field; one value per
#: entry point in §2's table.
CompactionReason = Literal[
    "microcompact_threshold",
    "compression_threshold",
    "api_overflow",
    "api_overflow_after_compression",
    "manual_cli",
]

__all__ = [
    "CompactionKind",
    "CompactionReason",
    "CompactionTrigger",
]
