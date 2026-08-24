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

from dataclasses import dataclass
from typing import Literal, Protocol

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
    "CompactionController",
    "CompactionDecision",
    "CompactionDecisionContext",
    "CompactionKind",
    "CompactionOutcome",
    "CompactionReason",
    "CompactionTrigger",
]


@dataclass(frozen=True)
class CompactionOutcome:
    """What one compaction attempt did — the single result contract.

    Every entry point returns this, whatever its ``kind``. The three
    kinds share the **contract**, not a builder: ``kind == "full"`` is
    built by ``ContextManager._run_compaction`` (the only holder of all
    eight fields), while the gate short-circuits, ``microcompact`` and
    ``minimal_history`` are built by the coordinator. Events, by
    contrast, are *always* emitted by the coordinator, so the
    emit-or-stay-silent criterion lives in exactly one place.

    ``status`` in one line: ``skipped`` = nothing was attempted;
    ``failed`` = attempted and did not succeed; ``cancelled`` = vetoed.
    ``skipped`` never counts against the circuit breaker.

    ``messages`` is the **original list object** on every non-success
    status, and a new list on success. Do not infer success from that,
    or from message counts — a successful microcompaction leaves the
    count identical, and a successful full compaction at
    ``len(messages) == 5`` raises it.

    Both token fields **exclude the system prompt**, unlike the
    ``CONTEXT_COMPRESSED`` event's pair. They are ``| None`` because two
    paths genuinely have no number to report: ``minimal_history``
    estimates nothing at all, and a ``no_safe_split`` failure returns
    before the estimate runs.
    """

    status: Literal["success", "cancelled", "failed", "skipped"]
    trigger: CompactionTrigger
    kind: CompactionKind
    reason: CompactionReason
    messages: list[dict]
    pre_tokens: int | None = None
    post_tokens: int | None = None
    detail: str | None = None


@dataclass(frozen=True)
class CompactionDecisionContext:
    """What a host controller is told before a compaction runs.

    Public, redacted, **in-process only**: this object never goes on the
    wire and is never serialized. Command hooks do not get it — they get
    the flat Claude-compatible ``PreCompact`` payload, because that is
    what the matcher matches on.

    It carries counts, never message text. Not because a list reference
    would be expensive (it would not — a dataclass field shares the
    reference), but because this is a redaction boundary, and because
    handing over the text is an invitation to mutate it. A host that
    genuinely needs the text reads ``agent.messages``.

    Several fields are ``None`` outside ``kind == "full"``; see the
    per-kind table in the orchestration plan §4.4.3.
    """

    trigger: CompactionTrigger
    kind: CompactionKind
    reason: CompactionReason
    pre_tokens: int | None = None
    messages_to_summarize: int = 0
    messages_to_keep: int = 0
    recently_read_files: tuple[str, ...] = ()
    summary_input_budget: int | None = None
    max_summary_tokens: int | None = None
    can_provide_summary: bool = False
    tool_results_to_clip: int | None = None


@dataclass(frozen=True)
class CompactionDecision:
    """One control-plane verdict.

    ``provide_summary`` is legal only when
    ``CompactionDecisionContext.can_provide_summary`` is true, i.e. only
    for ``kind == "full"``. Anywhere else it is an invalid decision and
    is treated as ``allow`` with a warning — a misconfigured control
    plane must never be able to drive the context into the overflow
    ladder.
    """

    action: Literal["allow", "cancel", "provide_summary"]
    summary: str | None = None
    reason: str | None = None


class CompactionController(Protocol):
    """The ``compaction_controller=`` contract.

    **Synchronous.** The compaction path runs inside ``ContextManager``,
    which holds no event loop; an awaitable return is handled as an
    unknown return value, with a warning.

    A controller that raises is caught, warned about, and treated as
    ``allow``. That fail-open rule is not politeness: two of the five
    entry points *are* the API-overflow recovery ladder, so an exception
    escaping a controller would turn "context too long" into "the turn
    crashes" — killing the recovery path this design exists to protect.
    """

    def __call__(self, ctx: CompactionDecisionContext) -> CompactionDecision: ...
