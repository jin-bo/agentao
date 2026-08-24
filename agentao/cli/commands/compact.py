"""``/compact`` — manually trigger full conversation-history compaction.

Runs the same path as the threshold-driven tier, on demand: it goes through
``CompactionCoordinator`` with ``trigger="manual"`` / ``reason="manual_cli"``,
which fires the ``PreCompact`` hook, applies the circuit-breaker gate, swaps
in the summarized history, and emits both compaction events. All this file
adds is the console report.

It used to carry its own copy of the hook dispatch and its own success
heuristic — sniffing ``messages[0]`` for a freshly prepended
``[Compact Boundary]`` marker, because ``compress_messages`` returned a bare
list and could not say whether anything happened. Both are gone: the
coordinator returns a ``CompactionOutcome`` with a ``status``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...compaction.coordinator import CompactionRequest
from ...context_manager import ContextManager
from .._globals import console

if TYPE_CHECKING:
    from ..app import AgentaoCLI

# Below this many messages there is nothing worth summarizing. Read off the
# ``ContextManager`` *class*, not the live instance, so this pre-check and
# the ``history_too_short`` guard inside ``prepare_compaction`` cannot
# drift apart — and so a test double standing in for the instance does not
# turn the comparison into a Mock. Checked here as well so the user gets a
# sentence rather than a silent no-op; the guard downstream is what
# actually enforces it.
_MIN_MESSAGES_TO_COMPACT = ContextManager.MIN_MESSAGES_TO_COMPACT

# Why nothing happened, in the user's terms. ``detail`` values come from
# ``CompactionOutcome``; anything unlisted falls back to the log pointer.
# ``circuit_open`` is deliberately absent: an open breaker no longer blocks
# this command. ``/compact`` is a half-open probe — user-driven, non-looping,
# and the one action a user can take to close the breaker again.
_FAILURE_HINTS = {
    "history_too_short": "there is not enough history to summarize yet",
    "no_safe_split": (
        "no safe split point — every candidate boundary would orphan a tool "
        "result"
    ),
    "summary_empty": "the summarization call returned nothing",
    "summary_input_error": (
        "the conversation history could not be rendered into a transcript "
        "for the summarizer (see agentao.log)"
    ),
}


def handle_compact_command(cli: AgentaoCLI, args: str) -> None:
    """Handle ``/compact`` — summarize old history into a compact block."""
    agent = cli.agent
    cm = agent.context_manager

    if len(agent.messages) < _MIN_MESSAGES_TO_COMPACT:
        console.print(
            "\n[info]Not enough conversation history to compact yet.[/info]\n"
        )
        return

    system_prompt = agent._build_system_prompt()
    pre_msgs = len(agent.messages)

    run = agent.compaction_coordinator.run(
        CompactionRequest("manual", "full", "manual_cli"),
        system_prompt=system_prompt,
        measure_system_tokens=True,
    )
    outcome = run.outcome

    if outcome.status == "cancelled":
        why = f" — {outcome.detail}" if outcome.detail else ""
        console.print(
            f"\n[warning]Compaction cancelled by the host{why}.[/warning]\n"
            "[dim]History is unchanged. A PreCompact hook or the configured "
            "compaction controller vetoed it.[/dim]\n"
        )
        return

    if outcome.status != "success":
        hint = _FAILURE_HINTS.get(outcome.detail or "")
        detail = f" — {hint}" if hint else " (see agentao.log)"
        note = ""
        if cm.compaction_circuit_open:
            # The probe was allowed and did not succeed, so nothing closed
            # the breaker. Say so, or the user reads "no change" and has no
            # idea automatic compaction is still paused.
            note = (
                "\n[dim]The compaction circuit breaker is still open — "
                "automatic compaction stays paused until a compaction "
                "succeeds (or /clear).[/dim]"
            )
        console.print(
            f"\n[warning]Compaction made no change{detail}.[/warning]{note}\n"
        )
        return

    post_msgs = len(agent.messages)
    # Measured by the coordinator, in the system-inclusive unit this report
    # has always used. Asked for rather than re-measured: two estimates over
    # the full history is what a manual compaction costs today, and routing
    # through the coordinator must not quietly double that.
    pre_tokens = run.pre_est_tokens or 0
    post_tokens = run.post_est_tokens or 0

    pct = cm.get_usage_stats(agent.messages).get("usage_percent", 0.0)
    cli._cached_ctx_pct = pct

    console.print(
        f"\n[success]Compacted history: {pre_msgs} → {post_msgs} messages, "
        f"~{pre_tokens:,} → ~{post_tokens:,} tokens "
        f"({pct:.1f}% of window).[/success]\n"
    )
