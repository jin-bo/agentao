"""Pre-LLM-call compaction steps for ``ChatLoopRunner``.

The runner's loop body invokes both methods on every iteration; each checks
its own threshold against ``ContextManager`` and short-circuits when no
compaction is needed. Everything past that check — the ``PreCompact`` hook,
the circuit-breaker gate, the transform itself, and both events — belongs to
``CompactionCoordinator``, which the API-overflow ladder and manual
``/compact`` go through as well. These two methods are threshold detectors
now, and nothing else.

Mixed into :class:`ChatLoopRunner`; relies on ``self._agent``.
"""

from __future__ import annotations

from typing import Tuple

from ...compaction.coordinator import CompactionRequest


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
        run = agent.compaction_coordinator.run(
            CompactionRequest("auto", "microcompact", "microcompact_threshold"),
            system_prompt=system_prompt,
            messages_with_system=messages_with_system,
            measure_system_tokens=True,
        )
        return run.messages_with_system, run.system_prompt

    def _maybe_full_compress(
        self,
        messages_with_system: list,
        system_prompt: str,
        tokens: int | None = None,
    ) -> Tuple[list, str]:
        agent = self._agent
        if not agent.context_manager.needs_compression(messages_with_system, tokens=tokens):
            return messages_with_system, system_prompt
        run = agent.compaction_coordinator.run(
            CompactionRequest("auto", "full", "compression_threshold"),
            system_prompt=system_prompt,
            messages_with_system=messages_with_system,
            measure_system_tokens=True,
        )
        return run.messages_with_system, run.system_prompt
