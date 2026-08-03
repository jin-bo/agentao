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
        finish_reason_missing
                          — at least one LLM call in this turn ended without
                            the provider reporting *why* generation stopped.
                            See below; ``False`` on a cancelled turn, where the
                            absence is explained by the cancellation itself.
    """

    text: str
    status: str
    incomplete_reason: Optional[str]
    tool_count: int
    error: Optional[str] = None
    #: A separate axis from ``incomplete_reason``, deliberately.
    #:
    #: A stream can end without the provider ever sending ``finish_reason`` —
    #: a gateway closing the SSE body after its own upstream timeout, or a
    #: partially-compatible local server that never emits the field. agentao
    #: falls back to ``"stop"``, so such a turn is otherwise indistinguishable
    #: from a clean completion: the text may be a truncated fragment reported
    #: as a finished answer.
    #:
    #: This flag reports that, and nothing more. It does **not** make the turn
    #: incomplete and does **not** affect :attr:`is_answer`, because for the
    #: servers that simply never send the field, every turn would otherwise
    #: become a failure. Hosts that would rather be strict can treat
    #: ``finish_reason_missing`` as fatal themselves; hosts on a known-lenient
    #: provider can keep ignoring it. agentao does not guess which you are.
    finish_reason_missing: bool = False

    @property
    def is_answer(self) -> bool:
        """True only for a complete, model-authored answer.

        The single check a host needs before treating ``text`` as the model's
        reply: the turn ended ``"ok"`` and nothing classified it as incomplete.
        A cancelled or errored turn, or one the harness could not get an answer
        out of, is ``False``.

        :attr:`finish_reason_missing` is deliberately *not* part of this: the
        answer is complete as far as anything agentao can observe, and the
        providers that omit the field omit it on every call. A host that wants
        the stricter reading writes ``o.is_answer and not
        o.finish_reason_missing``.
        """
        return self.status == "ok" and self.incomplete_reason is None


__all__ = ["TurnOutcome"]
