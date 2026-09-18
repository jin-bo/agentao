"""A scripted socket under the real ``anthropic`` SDK.

Only the transport is replaced, so the request bodies these tests read are
what the SDK serialized and the response is parsed by the SDK's own event
types. That is the point of testing this way: a hand-written fake client
would happily accept a ``temperature`` the real one has no parameter for.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import anthropic
import httpx2
import pytest


def _sse(*events: Dict[str, Any]) -> bytes:
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
    ).encode()


def _answer(text: str, **usage: int) -> bytes:
    """One streamed assistant message saying ``text``."""
    usage.setdefault("input_tokens", 10)
    usage.setdefault("output_tokens", 1)
    return _sse(
        {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-test",
            "content": [], "stop_reason": None, "stop_sequence": None, "usage": usage}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
         "usage": {"output_tokens": 7}},
        {"type": "message_stop"},
    )


class Socket:
    """Answers ``POST /v1/messages`` from a script; 404s the Models API, as
    Anthropic-compatible gateways do (Agentao then keeps its configured limits)."""

    def __init__(self, *bodies: bytes) -> None:
        self._bodies = list(bodies)
        self.requests: List[Dict[str, Any]] = []
        self.urls: List[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            return httpx2.Response(404, json={"type": "error", "error": {
                "type": "not_found_error", "message": "no such route"}})
        self.urls.append(str(request.url))
        self.requests.append(json.loads(request.content))
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                               content=self._bodies.pop(0))


@pytest.fixture
def answer():
    """``answer(text, **usage)`` builds one streamed assistant message."""
    return _answer


@pytest.fixture
def attach():
    """``attach(agent, *bodies)`` points the agent's client at a scripted socket."""
    def _attach(agent: Any, *bodies: bytes) -> Socket:
        socket = Socket(*bodies)
        agent.llm.client = anthropic.Anthropic(
            api_key="test-key", base_url="http://socket.test", max_retries=0,
            http_client=httpx2.Client(transport=httpx2.MockTransport(socket)),
        )
        return socket
    return _attach
