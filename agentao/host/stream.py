"""Items of ``Agentao.astream()`` — the stable streaming-text surface.

``astream()`` yields :class:`TextDelta` items while the turn runs, then the
turn's :class:`~agentao.outcome.TurnOutcome` as the last item. Neither type is
a :data:`~agentao.host.models.HostEvent`: they are a delivery API, not an audit
record, so they are not projected into replay and are not in
``docs/schema/host.events.v1.json``.

Standard library only, like the rest of what ``import agentao.host`` loads.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextDelta:
    """A chunk of assistant text, in the order the model produced it.

    Deltas are for display. Joined together they are **not** the turn's
    answer: every LLM call in the turn streams its text, including a call
    that ends in tool calls, so narration before a tool call arrives as
    deltas and is not part of the final text. The answer is the
    ``TurnOutcome.text`` that ends the stream; check ``is_answer`` before
    treating it as one.
    """

    text: str


__all__ = ["TextDelta"]
