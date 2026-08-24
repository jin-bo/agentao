"""Pre-LLM-call compaction steps for ``ChatLoopRunner``.

The runner's loop body invokes both methods on every iteration; each
checks its own threshold against ``ContextManager`` and short-circuits
when no compaction is needed. Both fire a ``PreCompact`` plugin hook
before mutating history so plugin authors can react to the imminent
context change.

Mixed into :class:`ChatLoopRunner`; relies on
``self._dispatch_pre_compact`` (provided by ``_HookDispatchMixin``) and
``self._agent``.
"""

from __future__ import annotations

import time
from typing import Tuple


class _CompactionMixin:
    """Mix-in providing microcompaction + full compression steps."""

    def _maybe_microcompact(
        self,
        messages_with_system: list,
        system_prompt: str,
        tokens: int | None = None,
    ) -> Tuple[list, str]:
        agent = self._agent
        if not agent.context_manager.needs_microcompaction(messages_with_system, tokens=tokens):
            return messages_with_system, system_prompt
        if not agent.context_manager.microcompact_would_mutate(agent.messages):
            # Same stand-down as the open breaker below, one tier cheaper.
            # Being *in* the 55-80% band says nothing about there being
            # anything left to shorten: once every old tool result is at or
            # under the limit, every further iteration in the band is a no-op —
            # and running the preamble anyway forks a PreCompact hook
            # subprocess per iteration and emits a CONTEXT_COMPRESSED reporting
            # pre == post, on top of two full-history token estimates.
            return messages_with_system, system_prompt
        self._dispatch_pre_compact(
            compaction_type="microcompact",
            reason="microcompact_threshold",
        )
        t0 = time.monotonic()
        pre_tokens = agent.context_manager.estimate_tokens(messages_with_system)
        pre_msgs = len(agent.messages)
        agent.messages = agent.context_manager.microcompact_messages(agent.messages)
        if agent.context_manager.last_microcompact_mutated:
            # Only a pass that actually shortened something invalidates the
            # already-sent prefix. Dropping the anchor unconditionally forced a
            # full re-encode of the entire history on every iteration spent in
            # the microcompact band — precisely when it is most expensive.
            agent.context_manager.invalidate_token_anchor()
        messages_with_system = [
            {"role": "system", "content": system_prompt}
        ] + agent.messages
        agent._emit_context_compressed(
            compression_type="microcompact",
            reason="microcompact_threshold",
            pre_msgs=pre_msgs,
            post_msgs=len(agent.messages),
            pre_tokens=pre_tokens,
            post_tokens=agent.context_manager.estimate_tokens(messages_with_system),
            duration_ms=round((time.monotonic() - t0) * 1000),
        )
        return messages_with_system, system_prompt

    def _maybe_full_compress(
        self,
        messages_with_system: list,
        system_prompt: str,
        tokens: int | None = None,
    ) -> Tuple[list, str]:
        agent = self._agent
        if not agent.context_manager.needs_compression(messages_with_system, tokens=tokens):
            return messages_with_system, system_prompt
        if agent.context_manager.compaction_circuit_open:
            # Every attempt now returns history unchanged, so announcing the
            # compaction would fork a PreCompact hook subprocess per iteration
            # for something that never happens — and emit a CONTEXT_COMPRESSED
            # reporting pre == post. Stand down and let the API-overflow
            # recovery path in ``_runner`` own it from here.
            #
            # Still log it: standing down before ``compress_messages`` skips
            # the breaker warning *it* used to emit, and that line was the only
            # signal that auto-compaction is dead for the rest of the session
            # (the counter has no reset path).
            agent.llm.logger.warning(
                "Compact circuit breaker open — skipping auto-compaction; "
                "context stays over threshold until the API-overflow path recovers it"
            )
            return messages_with_system, system_prompt
        self._dispatch_pre_compact(
            compaction_type="full",
            reason="compression_threshold",
        )
        agent.llm.logger.info("Context compression triggered inside loop")
        t0 = time.monotonic()
        pre_tokens = agent.context_manager.estimate_tokens(messages_with_system)
        pre_msgs = len(agent.messages)
        agent.messages = agent.context_manager.compress_messages(agent.messages, is_auto=True)
        # The summarization LLM call bypasses the runner's detector (it goes
        # straight to ``llm_client``), so fold its observation in here. This is
        # the call whose output permanently rewrites history, so a turn that
        # compacted against an unconfirmed summary must say so.
        if agent.context_manager.last_summary_finish_reason_missing:
            agent._turn_finish_reason_missing = True
        agent.context_manager.invalidate_token_anchor()  # prefix rewritten; real count is stale
        system_prompt = agent._build_system_prompt()
        messages_with_system = [
            {"role": "system", "content": system_prompt}
        ] + agent.messages
        agent.llm.logger.info(f"Context compressed to {len(agent.messages)} messages")
        agent._emit_context_compressed(
            compression_type="full",
            reason="compression_threshold",
            pre_msgs=pre_msgs,
            post_msgs=len(agent.messages),
            pre_tokens=pre_tokens,
            post_tokens=agent.context_manager.estimate_tokens(messages_with_system),
            duration_ms=round((time.monotonic() - t0) * 1000),
        )
        agent._last_session_summary_id = agent._emit_session_summary_if_new(
            agent._last_session_summary_id,
        )
        return messages_with_system, system_prompt
