"""Reasoning text is kept whole, and every turn shows it.

Two defects, one turn each:

- History cut ``reasoning_content`` to 500 characters. On ``openai-completions``
  history goes out as it is, so DeepSeek, Kimi and similar thinking backends got
  back a chain of thought that stopped mid-sentence inside their own tool loop.
- Only the tool-call path emitted ``THINKING``. A turn that answered without a
  call showed its reasoning nowhere but ``agentao.log``: not in the CLI, not as
  an ACP ``agent_thought_chunk``, not in replay.

A real ``Agentao`` turn over the real ``openai`` SDK; only the socket is
scripted, so the second request is read off the wire.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import openai
import pytest

from agentao import Agentao
from agentao.transport import EventType, SdkTransport

# Agentao here writes to the process cwd: see ``isolated_cwd`` in conftest.py.
pytestmark = pytest.mark.usefixtures("isolated_cwd")

LONG_THOUGHT = "weigh the options, then read the note. " * 40  # ~1600 chars
FINAL_THOUGHT = "the note answers it. " * 40                   # ~840 chars


def _sse(*deltas: dict, finish: str) -> bytes:
    out = b""
    for i, delta in enumerate(deltas):
        chunk = {
            "id": "c", "object": "chat.completion.chunk", "created": 1, "model": "m",
            "choices": [{
                "index": 0,
                "delta": delta,
                "finish_reason": finish if i == len(deltas) - 1 else None,
            }],
        }
        out += b"data: " + json.dumps(chunk).encode() + b"\n\n"
    return out + b"data: [DONE]\n\n"


class _Wire:
    def __init__(self, *bodies: bytes) -> None:
        self._bodies = list(bodies)
        self.requests: list[dict] = []

    def __call__(self, request: Any) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=self._bodies.pop(0))


@pytest.fixture
def turn():
    Path("note.txt").write_text("hello from disk", encoding="utf-8")
    args = json.dumps({"file_path": str(Path("note.txt").resolve())})
    wire = _Wire(
        _sse({"reasoning_content": LONG_THOUGHT},
             {"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                              "function": {"name": "read_file", "arguments": args}}]},
             finish="tool_calls"),
        _sse({"reasoning_content": FINAL_THOUGHT}, {"content": "It says hello."},
             finish="stop"),
    )
    events: list = []
    agent = Agentao(api_key="k", base_url="http://wire.test/v1", model="m",
                    working_directory=Path.cwd(),
                    transport=SdkTransport(on_event=events.append))
    agent.llm.client = openai.OpenAI(
        api_key="k", base_url="http://wire.test/v1", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(wire)))
    try:
        reply = agent.chat("read the note")
        yield agent, wire, events, reply
    finally:
        agent.close()


def test_the_model_gets_its_reasoning_back_whole(turn):
    agent, wire, _, reply = turn
    assert reply == "It says hello."
    sent = [m for m in wire.requests[1]["messages"] if m.get("tool_calls")]
    assert len(sent) == 1
    assert sent[0]["reasoning_content"] == LONG_THOUGHT
    # History holds the same whole text, the final answer's included.
    assert agent.messages[-1]["reasoning_content"] == FINAL_THOUGHT


def test_a_turn_that_answers_without_a_call_shows_its_reasoning(turn):
    _, _, events, _ = turn
    thoughts = [e.data["text"] for e in events if e.type == EventType.THINKING]
    assert LONG_THOUGHT in thoughts
    assert thoughts.count(FINAL_THOUGHT) == 1


def test_reasoning_beside_a_wire_carrier_is_counted_once(turn):
    """Whole text is now as long as the carrier's, so counting both would
    double every thinking turn's estimate on the other two wires."""
    agent = turn[0]
    cm = agent.context_manager
    plain = {"role": "assistant", "content": "ok", "reasoning_content": LONG_THOUGHT}
    carried = dict(plain, anthropic_thinking_blocks=[
        {"type": "thinking", "thinking": LONG_THOUGHT, "signature": "S=="}])
    bare = {"role": "assistant", "content": "ok"}
    thought = cm._count_message_tokens(plain) - cm._count_message_tokens(bare)
    assert thought > 0
    assert cm._count_message_tokens(carried) == cm._count_message_tokens(plain)


def test_a_synthetic_final_keeps_only_an_echo():
    """The four synthetic finals repeat a response already recorded whole on
    its tool-call message; a second whole copy would send it twice."""
    from agentao.runtime.chat_loop._serialize import (
        SYNTHETIC_REASONING_ECHO_CHARS,
        _attach_reasoning_echo,
    )
    msg: dict = {"role": "assistant", "content": "halted"}
    _attach_reasoning_echo(msg, LONG_THOUGHT)
    assert msg["reasoning_content"] == LONG_THOUGHT[:SYNTHETIC_REASONING_ECHO_CHARS] + "..."
    short: dict = {"role": "assistant", "content": "halted"}
    _attach_reasoning_echo(short, "brief")
    assert short["reasoning_content"] == "brief"
    none: dict = {"role": "assistant", "content": "halted"}
    _attach_reasoning_echo(none, None)
    assert "reasoning_content" not in none
