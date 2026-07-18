"""``TurnOutcome`` — the structured result of a single turn.

``Agentao.chat()`` / ``arun()`` return the turn's text as a ``str`` (a stable,
backward-compatible contract). That string alone cannot tell a real answer from
the ``[No response]`` placeholder, a harness abort notice, or an ``[LLM API
error: …]`` string. ``TurnOutcome`` is the companion a host reads afterwards via
``agent.last_turn`` to get the missing fact.

It is a plain frozen dataclass, importable without pulling the LLM stack, and
mirrors the ``TURN_END`` transport payload field-for-field — so a host that
cannot (or does not want to) subscribe to the internal ``Transport`` still has
the same facts through a simple synchronous read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TurnOutcome:
    """What the most recent turn produced, beyond its text.

    Fields (mirroring ``TURN_END``):
        text              — the turn's final text (same value ``chat()`` returned)
        status            — ``"ok"`` | ``"error"`` | ``"cancelled"``
        incomplete_reason — why the turn has no complete model answer, or
                            ``None`` for a real answer. A single closed
                            vocabulary: ``no_output`` / ``reasoning_only`` /
                            ``length_truncated`` / ``doom_loop`` / ``llm_error``.
        tool_count        — tool calls the model made across the turn
        error             — error detail when ``status != "ok"``, else ``None``
    """

    text: str
    status: str
    incomplete_reason: Optional[str]
    tool_count: int
    error: Optional[str] = None

    @property
    def is_answer(self) -> bool:
        """True only for a complete, model-authored answer.

        The single check a host needs before treating ``text`` as the model's
        reply: the turn ended ``"ok"`` and nothing classified it as incomplete.
        A cancelled or errored turn, or one the harness could not get an answer
        out of, is ``False``.
        """
        return self.status == "ok" and self.incomplete_reason is None


__all__ = ["TurnOutcome"]
