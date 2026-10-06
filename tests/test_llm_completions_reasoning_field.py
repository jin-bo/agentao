"""Chat Completions thinking text arrives under two field names.

DeepSeek, MiniMax, Kimi and older vLLM stream it as ``delta.reasoning_content``;
Ollama, newer vLLM and OpenRouter as ``delta.reasoning``. Only the first was
read, so on the second group the thinking text was dropped. Some vLLM
versions send both with the same text, which must not be counted twice.

Everything goes through the real ``openai`` SDK; only the socket is scripted,
so the chunk objects are what the SDK builds from the wire.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import openai
import pytest

from agentao.llm.client import LLMClient

# ``LLMClient`` opens ``agentao.log`` in the process cwd: see ``isolated_cwd``.
pytestmark = pytest.mark.usefixtures("isolated_cwd")

HELLO = [{"role": "user", "content": "hi"}]


def _sse(*deltas: dict, finish: str = "stop") -> bytes:
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


def _client(body: bytes) -> LLMClient:
    def handler(request: Any) -> Any:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    llm = LLMClient(api_key="k", base_url="http://wire.test/v1", model="m",
                    logger=logging.getLogger("test.reasoning_field"))
    llm.client = openai.OpenAI(
        api_key="k", base_url="http://wire.test/v1", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return llm


@pytest.mark.parametrize("field", ["reasoning_content", "reasoning"])
def test_thinking_text_is_kept_under_either_field_name(field):
    body = _sse({field: "Let me "}, {field: "think."}, {"content": "42"})
    message = _client(body).chat_stream(HELLO).choices[0].message
    assert message.reasoning_content == "Let me think."
    assert message.content == "42"


def test_both_fields_with_the_same_text_are_counted_once():
    body = _sse(
        {"reasoning_content": "step 1. ", "reasoning": "step 1. "},
        {"reasoning_content": "step 2.", "reasoning": "step 2."},
        {"content": "done"},
    )
    message = _client(body).chat_stream(HELLO).choices[0].message
    assert message.reasoning_content == "step 1. step 2."


def test_reasoning_alongside_tool_calls():
    body = _sse(
        {"reasoning": "I need the weather."},
        {"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                         "function": {"name": "get_weather", "arguments": ""}}]},
        {"tool_calls": [{"index": 0, "function": {"arguments": '{"city":"SF"}'}}]},
        finish="tool_calls",
    )
    message = _client(body).chat_stream(HELLO).choices[0].message
    assert message.reasoning_content == "I need the weather."
    assert message.tool_calls[0].function.name == "get_weather"


def test_no_thinking_text_leaves_the_field_unset():
    message = _client(_sse({"content": "hello"})).chat_stream(HELLO).choices[0].message
    assert message.reasoning_content is None
