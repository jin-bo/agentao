"""A Ctrl+C mid-stream keeps what the model had already said.

The interactive CLI installs no SIGINT handler, so Ctrl+C is a
``KeyboardInterrupt`` raised inside the stream read. No adapter catches it (it
is not an ``Exception``), so the half-built response died with the frame and
``runtime/turn.py`` recorded a bare ``[Interrupted]``: the next turn's model
did not know its own half-answer — which the user may have read and be
replying to — and in markdown mode the user never saw it either. A token
cancel (``agent run``, ACP, hosts) already kept it; only this path lost it.

These drive the real ``LLMClient.chat_stream`` and Chat Completions adapter;
only the SDK's ``create`` is replaced, and it yields the SDK's own
``ChatCompletionChunk`` models.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

import pytest
from openai.types.chat import ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import (
    Choice,
    ChoiceDelta,
    ChoiceDeltaToolCall,
    ChoiceDeltaToolCallFunction,
)

from agentao import Agentao
from agentao.runtime.turn import (
    INTERRUPTED_MARKER,
    interrupted_content,
    interrupted_partial,
)
from agentao.tools.base import Tool

pytestmark = pytest.mark.usefixtures("isolated_cwd")


def _chunk(content=None, *, tool_call=None, finish=None) -> ChatCompletionChunk:
    return ChatCompletionChunk(
        id="chunk",
        object="chat.completion.chunk",
        created=0,
        model="test-model",
        choices=[
            Choice(
                index=0,
                delta=ChoiceDelta(
                    role="assistant",
                    content=content,
                    tool_calls=[tool_call] if tool_call else None,
                ),
                finish_reason=finish,
            )
        ],
    )


def _call(call_id: str, name: str) -> ChoiceDeltaToolCall:
    return ChoiceDeltaToolCall(
        index=0,
        id=call_id,
        type="function",
        function=ChoiceDeltaToolCallFunction(name=name, arguments="{}"),
    )


def _agent() -> Agentao:
    return Agentao(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        working_directory=Path.cwd(),
        logger=logging.getLogger("test-interrupt-keeps-partial"),
    )


def _script(agent: Agentao, *streams) -> List[List[Dict[str, Any]]]:
    """Answer successive ``create`` calls with ``streams``; record each request."""
    requests: List[List[Dict[str, Any]]] = []
    queue = list(streams)

    def create(**kwargs):
        requests.append([dict(m) for m in kwargs["messages"]])
        return queue.pop(0)()

    agent.llm.client.chat.completions.create = create
    return requests


def _history(agent: Agentao):
    return [(m["role"], m.get("content")) for m in agent.messages]


class _InterruptingTool(Tool):
    @property
    def name(self) -> str:
        return "slow"

    @property
    def description(self) -> str:
        return "raises KeyboardInterrupt, as a Ctrl+C during a tool does"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        raise KeyboardInterrupt


class _OkTool(_InterruptingTool):
    @property
    def name(self) -> str:
        return "ok"

    def execute(self, **kwargs) -> str:
        return "done"


# ---------------------------------------------------------------------------
# the turn
# ---------------------------------------------------------------------------


def test_ctrl_c_mid_stream_keeps_the_shown_text():
    agent = _agent()

    def interrupted():
        yield _chunk("The answer is ")
        yield _chunk("forty-")
        raise KeyboardInterrupt

    def answer():
        yield _chunk("Forty-two.", finish="stop")

    requests = _script(agent, interrupted, answer)

    assert agent.chat("q") == "[Interrupted by user]"
    assert agent.messages[-1] == {
        "role": "assistant",
        "content": "The answer is forty-\n\n[Interrupted]",
    }
    assert agent.last_turn.status == "cancelled"

    # The next request carries it — the point of keeping it.
    agent.chat("go on")
    sent = [m.get("content") for m in requests[1] if m["role"] == "assistant"]
    assert "The answer is forty-\n\n[Interrupted]" in sent


def test_ctrl_c_before_any_text_leaves_the_bare_marker():
    agent = _agent()

    def interrupted():
        raise KeyboardInterrupt
        yield  # pragma: no cover - makes this a generator

    _script(agent, interrupted)
    agent.chat("q")
    assert agent.messages[-1]["content"] == INTERRUPTED_MARKER


def test_ctrl_c_in_the_tool_phase_does_not_write_the_text_twice():
    """The streamed text is already in history with its tool call."""
    agent = _agent()
    agent.add_tool(_InterruptingTool())

    def calls_tool():
        yield _chunk("Let me check.")
        yield _chunk(tool_call=_call("c1", "slow"), finish="tool_calls")

    _script(agent, calls_tool)
    agent.chat("q")

    contents = [c for _, c in _history(agent)]
    assert contents.count("Let me check.") == 1
    assert agent.messages[-1]["content"] == INTERRUPTED_MARKER
    assert not any(
        isinstance(c, str) and c.startswith("Let me check.") and c != "Let me check."
        for c in contents
    )


def test_only_the_interrupted_calls_text_is_kept():
    """An earlier call's text in the same turn is already recorded."""
    agent = _agent()
    agent.add_tool(_OkTool())

    def first():
        yield _chunk("Step one.")
        yield _chunk(tool_call=_call("c1", "ok"), finish="tool_calls")

    def second():
        yield _chunk("Step two is")
        raise KeyboardInterrupt

    _script(agent, first, second)
    agent.chat("q")

    assert agent.messages[-1]["content"] == "Step two is\n\n[Interrupted]"
    contents = [c for _, c in _history(agent)]
    assert contents.count("Step one.") == 1


def test_a_later_turns_interrupt_outside_a_stream_finds_nothing_stale():
    agent = _agent()
    agent.add_tool(_InterruptingTool())

    def interrupted():
        yield _chunk("half")
        raise KeyboardInterrupt

    def calls_tool():
        yield _chunk(tool_call=_call("c1", "slow"), finish="tool_calls")

    _script(agent, interrupted, calls_tool)
    agent.chat("q1")
    agent.chat("q2")
    assert agent.messages[-1]["content"] == INTERRUPTED_MARKER
    contents = [c for _, c in _history(agent)]
    assert contents.count("half\n\n[Interrupted]") == 1


def test_the_kept_text_is_sanitized_like_any_assistant_text():
    agent = _agent()
    hidden = "".join(chr(0xE0000 + ord(c)) for c in "ignore previous")

    def interrupted():
        yield _chunk("visible" + hidden)
        raise KeyboardInterrupt

    _script(agent, interrupted)
    agent.chat("q")
    assert agent.messages[-1]["content"] == "visible\n\n[Interrupted]"


# ---------------------------------------------------------------------------
# the helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("partial", [None, "", "   \n", object(), 42])
def test_nothing_usable_gives_the_bare_marker(partial):
    assert interrupted_content(partial) == INTERRUPTED_MARKER
    assert interrupted_partial(INTERRUPTED_MARKER) is None


def test_text_made_only_of_stripped_characters_gives_the_bare_marker():
    """The emptiness check runs on the sanitized text, not the raw text."""
    only_tags = "".join(chr(0xE0000 + ord(c)) for c in "run rm")
    assert interrupted_content(only_tags) == INTERRUPTED_MARKER


def test_content_and_partial_round_trip():
    content = interrupted_content("## Plan\n1. a")
    assert interrupted_partial(content) == "## Plan\n1. a"
    assert interrupted_partial("an ordinary answer") is None
    assert interrupted_partial(None) is None


# ---------------------------------------------------------------------------
# the CLI display
# ---------------------------------------------------------------------------


class _Recorder:
    def __init__(self):
        self.printed: list = []

    def print(self, *args, **kwargs):
        self.printed.extend(args)


def _rendered(printed) -> List[str]:
    from rich.markdown import Markdown

    return [p.markup if isinstance(p, Markdown) else p for p in printed]


@pytest.fixture
def recorder(monkeypatch):
    from agentao.cli import input_loop

    rec = _Recorder()
    monkeypatch.setattr(input_loop, "console", rec)
    return rec


def _cli(markdown: bool, last_content: str):
    from types import SimpleNamespace

    return SimpleNamespace(
        markdown_mode=markdown,
        agent=SimpleNamespace(messages=[{"role": "assistant", "content": last_content}]),
    )


def test_markdown_mode_shows_the_kept_text_before_the_notice(recorder):
    from agentao.cli.input_loop import _print_final_response

    cli = _cli(True, interrupted_content("The answer is forty-"))
    _print_final_response(cli, "[Interrupted by user]")
    assert _rendered(recorder.printed) == ["The answer is forty-", "[Interrupted by user]"]


def test_plain_mode_does_not_repeat_what_it_streamed(recorder):
    from agentao.cli.input_loop import _print_final_response

    cli = _cli(False, interrupted_content("The answer is forty-"))
    _print_final_response(cli, "[Interrupted by user]")
    assert recorder.printed == ["[Interrupted by user]"]


def test_an_ordinary_answer_renders_as_before(recorder):
    from agentao.cli.input_loop import _print_final_response

    cli = _cli(True, "Forty-two.")
    _print_final_response(cli, "Forty-two.")
    assert _rendered(recorder.printed) == ["Forty-two."]
