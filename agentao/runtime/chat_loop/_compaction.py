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
    ) -> Tuple[list, str]:
        agent = self._agent
        if not agent.context_manager.needs_microcompaction(messages_with_system):
            return messages_with_system, system_prompt
        self._dispatch_pre_compact(
            compaction_type="microcompact",
            reason="microcompact_threshold",
        )
        t0 = time.monotonic()
        pre_tokens = agent.context_manager.estimate_tokens(messages_with_system)
        pre_msgs = len(agent.messages)
        agent.messages = agent.context_manager.microcompact_messages(agent.messages)
        agent.context_manager.invalidate_token_anchor()  # tool results truncated in place
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
    ) -> Tuple[list, str]:
        agent = self._agent
        if not agent.context_manager.needs_compression(messages_with_system):
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
