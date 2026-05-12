"""``/compact`` — manually trigger full conversation-history compaction.

Mirrors the threshold-driven path in
``runtime/chat_loop/_compaction.py::_maybe_full_compress`` but runs on
demand: it calls ``ContextManager.compress_messages(..., is_auto=False)``,
swaps in the summarized history, fires the same ``CONTEXT_COMPRESSED`` /
session-summary observability events, and refreshes the cached context
percentage shown in the prompt.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from .._globals import console

if TYPE_CHECKING:
    from ..app import AgentaoCLI

# Below this many messages there is nothing worth summarizing — matches the
# guard inside ``ContextManager.compress_messages``.
_MIN_MESSAGES_TO_COMPACT = 5


def _produced_fresh_compaction(before: list, after: list) -> bool:
    """True if ``compress_messages`` actually built a new compact block.

    A successful compaction returns ``[boundary_marker, summary, …]`` — a
    brand-new first message. Every failure path (circuit breaker open, no
    safe split, summarization error) instead returns the original list or
    a microcompacted copy that leaves the first message untouched. Probing
    for *any* ``[Conversation Summary]`` would misfire when an older summary
    from a previous compaction is still in history, so key off the freshly
    prepended ``[Compact Boundary]`` marker instead.
    """
    if not after or after[0] is (before[0] if before else None):
        return False
    head = after[0].get("content")
    return isinstance(head, str) and head.startswith("[Compact Boundary")


def _dispatch_pre_compact(agent) -> None:
    """Best-effort ``PreCompact`` plugin-hook dispatch (side-effect only).

    Mirrors ``ChatLoopRunner._dispatch_pre_compact`` — dispatch matching
    rules *and* emit the ``PLUGIN_HOOK_FIRED`` replay event so manual
    ``/compact`` runs keep the same observability contract as the
    threshold-driven path.
    """
    rules = getattr(agent, "_plugin_hook_rules", None)
    if not rules:
        return
    try:
        from ...plugins.hooks import ClaudeHookPayloadAdapter, PluginHookDispatcher
        from ...transport import AgentEvent, EventType

        cwd = agent.working_directory
        payload = ClaudeHookPayloadAdapter().build_pre_compact(
            session_id=agent._session_id,
            cwd=cwd,
            compaction_type="full",
            reason="manual_cli",
            permission_mode=agent.active_permissions().mode,
        )
        dispatcher = PluginHookDispatcher(cwd=cwd)
        matched = dispatcher.select_matching_rules("PreCompact", payload, rules)
        if not matched:
            return
        dispatcher.dispatch_pre_compact(payload=payload, rules=matched)
        agent.transport.emit(AgentEvent(EventType.PLUGIN_HOOK_FIRED, {
            "hook_name": "PreCompact",
            "outcome": "allow",
            "compaction_type": "full",
            "trigger": "manual",
            "matched_rule_count": len(matched),
        }))
    except Exception:
        pass


def handle_compact_command(cli: AgentaoCLI, args: str) -> None:
    """Handle ``/compact`` — summarize old history into a compact block."""
    agent = cli.agent
    cm = agent.context_manager
    messages = agent.messages

    if len(messages) < _MIN_MESSAGES_TO_COMPACT:
        console.print(
            "\n[info]Not enough conversation history to compact yet.[/info]\n"
        )
        return

    _dispatch_pre_compact(agent)

    pre_msgs = len(messages)
    system_prompt = agent._build_system_prompt()
    pre_tokens = cm.estimate_tokens(
        [{"role": "system", "content": system_prompt}] + messages
    )

    t0 = time.monotonic()
    compacted = cm.compress_messages(messages, is_auto=False)

    if not _produced_fresh_compaction(messages, compacted):
        console.print(
            "\n[warning]Compaction made no change — nothing to summarize "
            "(or summarization failed; see agentao.log).[/warning]\n"
        )
        return

    agent.messages = compacted
    cm._last_api_prompt_tokens = None  # stale after compression
    system_prompt = agent._build_system_prompt()
    post_msgs = len(agent.messages)
    post_tokens = cm.estimate_tokens(
        [{"role": "system", "content": system_prompt}] + agent.messages
    )

    agent._emit_context_compressed(
        compression_type="full",
        reason="manual_cli",
        pre_msgs=pre_msgs,
        post_msgs=post_msgs,
        pre_tokens=pre_tokens,
        post_tokens=post_tokens,
        duration_ms=round((time.monotonic() - t0) * 1000),
    )
    # ``_last_session_summary_id`` is created lazily on the first chat turn
    # (runtime/turn.py); a manual /compact may run before that — e.g. right
    # after /sessions resume — so fall back to None.
    agent._last_session_summary_id = agent._emit_session_summary_if_new(
        getattr(agent, "_last_session_summary_id", None),
    )

    pct = cm.get_usage_stats(agent.messages).get("usage_percent", 0.0)
    cli._cached_ctx_pct = pct

    console.print(
        f"\n[success]Compacted history: {pre_msgs} → {post_msgs} messages, "
        f"~{pre_tokens:,} → ~{post_tokens:,} tokens "
        f"({pct:.1f}% of window).[/success]\n"
    )
