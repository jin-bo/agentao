"""``agent.last_turn`` — the structured read-after companion to ``chat()``.

``chat()`` returns the turn's text as a ``str``; ``last_turn`` is how a host
that cannot subscribe to the internal Transport tells a real answer from a
placeholder / canned notice / LLM-error string. It must mirror the gated
``TURN_END`` classification exactly.
"""

from pathlib import Path
from types import SimpleNamespace

from agentao import Agentao, TurnOutcome
from agentao.cancellation import CancellationToken


def _make_agent() -> Agentao:
    return Agentao(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        working_directory=Path.cwd(),
    )


def _text_response(content, *, finish_reason="stop", reasoning=None):
    message = SimpleNamespace(content=content, tool_calls=None, reasoning_content=reasoning)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=None, model="test-model",
    )


def test_top_level_export():
    """``from agentao import TurnOutcome`` works and needs no LLM stack."""
    assert TurnOutcome.__name__ == "TurnOutcome"


def test_last_turn_is_none_before_first_turn():
    agent = _make_agent()
    assert agent.last_turn is None


def test_real_answer_is_reported_as_an_answer():
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _text_response("the answer is 42")

    reply = agent.chat("hi")

    outcome = agent.last_turn
    assert isinstance(outcome, TurnOutcome)
    assert outcome.text == reply == "the answer is 42"
    assert outcome.status == "ok"
    assert outcome.incomplete_reason is None
    assert outcome.is_answer is True


def test_empty_turn_is_not_an_answer():
    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _text_response("")

    agent.chat("hi")

    outcome = agent.last_turn
    assert outcome.incomplete_reason == "no_output"
    assert outcome.is_answer is False
    assert outcome.text == "[No response]"


def test_llm_error_turn_is_not_an_answer():
    """A swallowed LLM failure ends with status "ok" (no exception propagates),
    so ``is_answer`` must lean on ``incomplete_reason``, not status alone."""
    agent = _make_agent()

    def _boom(messages, tools, token):
        raise RuntimeError("502 Bad Gateway")

    agent._llm_call = _boom

    agent.chat("hi")

    outcome = agent.last_turn
    assert outcome.status == "ok"
    assert outcome.incomplete_reason == "llm_error"
    assert outcome.is_answer is False
    assert "502 Bad Gateway" in outcome.text


def test_cancelled_turn_is_not_reported_as_incomplete():
    """A mid-stream cancellation carries status "cancelled" and, per the wire
    gate, a ``None`` incomplete_reason — not a misclassification as empty."""
    agent = _make_agent()
    token = CancellationToken()

    def _llm(messages, tools, cancellation_token):
        token.cancel("sigint")
        return _text_response("")

    agent._llm_call = _llm

    agent.chat("hi", cancellation_token=token)

    outcome = agent.last_turn
    assert outcome.status == "cancelled"
    assert outcome.incomplete_reason is None
    assert outcome.is_answer is False


def test_last_turn_mirrors_the_turn_end_wire_field():
    """The read-after and the TURN_END event must never disagree — both are
    fed from the same single gated value in ``runtime/turn.py``."""
    from agentao.transport import EventType

    agent = _make_agent()
    agent._llm_call = lambda messages, tools, token: _text_response(
        "partial", finish_reason="length",
    )
    seen = []
    agent.transport.subscribe(
        lambda ev: seen.append((getattr(ev, "data", None) or {}))
        if getattr(ev, "type", None) == EventType.TURN_END else None
    )

    agent.chat("hi")

    wire = [d for d in seen if d][0]
    outcome = agent.last_turn
    assert wire["incomplete_reason"] == outcome.incomplete_reason == "length_truncated"
    assert wire["status"] == outcome.status
    assert wire["final_text"] == outcome.text
    assert wire["tool_count"] == outcome.tool_count


def test_frozen():
    """``TurnOutcome`` is immutable — a host cannot mutate a reported fact."""
    import dataclasses

    outcome = TurnOutcome(text="x", status="ok", incomplete_reason=None, tool_count=0)
    try:
        outcome.text = "y"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:  # pragma: no cover
        raise AssertionError("TurnOutcome should be frozen")


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
