"""A scripted socket under the real ``openai`` SDK.

Only the transport is replaced, so the request bodies these tests read are
what the SDK serialized and the events are parsed by the SDK's own types.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx
import openai
import pytest


def _response(output: List[Dict[str, Any]], status: str,
              usage: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {"id": "resp_1", "object": "response", "created_at": 0, "model": "gpt-test",
            "status": status, "output": output, "parallel_tool_calls": True,
            "tool_choice": "auto", "tools": [], "usage": usage,
            "incomplete_details": None, "error": None}


def _answer(text: str, *, reasoning: Optional[str] = None, input_tokens: int = 10,
            cached: int = 0, cache_write: int = 0) -> bytes:
    """One streamed response saying ``text`` — after a reasoning item whose
    encrypted content is ``reasoning``, when given."""
    output: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = [
        {"type": "response.created", "response": _response([], "in_progress", None)}]
    if reasoning is not None:
        item = {"id": "rs_1", "type": "reasoning", "summary": [],
                "encrypted_content": reasoning}
        output.append(item)
        events.append({"type": "response.output_item.done", "output_index": 0, "item": item})
    index = len(output)
    message = {"id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
               "content": [{"type": "output_text", "text": text, "annotations": []}]}
    output.append(message)
    events += [
        {"type": "response.output_text.delta", "item_id": "msg_1", "output_index": index,
         "content_index": 0, "delta": text, "logprobs": []},
        {"type": "response.output_item.done", "output_index": index, "item": message},
        {"type": "response.completed", "response": _response(output, "completed", {
            "input_tokens": input_tokens, "input_tokens_details": {
                "cached_tokens": cached, "cache_write_tokens": cache_write},
            "output_tokens": 7, "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": input_tokens + 7})},
    ]
    return "".join(
        f"event: {e['type']}\ndata: {json.dumps({**e, 'sequence_number': i})}\n\n"
        for i, e in enumerate(events)
    ).encode()


class Socket:
    """Answers ``POST /v1/responses`` from a script."""

    def __init__(self, *bodies: bytes) -> None:
        self._bodies = list(bodies)
        self.requests: List[Dict[str, Any]] = []
        self.urls: List[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        self.requests.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=self._bodies.pop(0))


@pytest.fixture
def answer():
    """``answer(text, reasoning=..., input_tokens=..., cached=..., cache_write=...)``."""
    return _answer


@pytest.fixture
def attach():
    """``attach(agent, *bodies)`` points the agent's client at a scripted socket."""
    def _attach(agent: Any, *bodies: bytes) -> Socket:
        socket = Socket(*bodies)
        agent.llm.client = openai.OpenAI(
            api_key="test-key", base_url="http://socket.test/v1", max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(socket)),
        )
        return socket
    return _attach
