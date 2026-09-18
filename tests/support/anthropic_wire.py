"""A scripted Anthropic Messages endpoint for tests — below the real SDK.

Everything above this file is the real thing: the ``anthropic`` client, its
SSE decoder, its event models, its exception mapping. Only the socket is
replaced, by an ``httpx2.MockTransport``. That is deliberate: a hand-built
event object agrees with whoever built it, and ``MagicMock`` answers any
attribute, so neither can show that the adapter reads the SDK's actual shapes
— or that the request body the SDK serializes is the one the adapter meant.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import anthropic
import httpx2


def sse(*events: Dict[str, Any]) -> bytes:
    """Server-sent-event bytes for a list of Messages stream events."""
    return b"".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode("utf-8")
        for event in events
    )


def message_start(*, model: str = "claude-test", **usage: int) -> Dict[str, Any]:
    usage.setdefault("input_tokens", 10)
    usage.setdefault("output_tokens", 1)
    return {
        "type": "message_start",
        "message": {
            "id": "msg_test", "type": "message", "role": "assistant",
            "model": model, "content": [], "stop_reason": None,
            "stop_sequence": None, "usage": usage,
        },
    }


def _start(index: int, block: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "content_block_start", "index": index, "content_block": block}


def _delta(index: int, delta: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "content_block_delta", "index": index, "delta": delta}


def _stop(index: int) -> Dict[str, Any]:
    return {"type": "content_block_stop", "index": index}


def text_block(index: int, *chunks: str) -> List[Dict[str, Any]]:
    return [
        _start(index, {"type": "text", "text": ""}),
        *[_delta(index, {"type": "text_delta", "text": c}) for c in chunks],
        _stop(index),
    ]


def thinking_block(index: int, chunks: Iterable[str], signature: str) -> List[Dict[str, Any]]:
    events = [_start(index, {"type": "thinking", "thinking": "", "signature": ""})]
    events += [_delta(index, {"type": "thinking_delta", "thinking": c}) for c in chunks]
    if signature:
        events.append(_delta(index, {"type": "signature_delta", "signature": signature}))
    events.append(_stop(index))
    return events


def redacted_thinking_block(index: int, data: str) -> List[Dict[str, Any]]:
    return [_start(index, {"type": "redacted_thinking", "data": data}), _stop(index)]


def tool_use_block(
    index: int, call_id: str, name: str, *json_chunks: str,
    initial_input: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    block = {"type": "tool_use", "id": call_id, "name": name, "input": initial_input or {}}
    return [
        _start(index, block),
        *[_delta(index, {"type": "input_json_delta", "partial_json": c}) for c in json_chunks],
        _stop(index),
    ]


def message_end(stop_reason: str = "end_turn", **usage: int) -> List[Dict[str, Any]]:
    usage.setdefault("output_tokens", 5)
    return [
        {"type": "message_delta",
         "delta": {"stop_reason": stop_reason, "stop_sequence": None},
         "usage": usage},
        {"type": "message_stop"},
    ]


def error_event(kind: str, message: str = "boom") -> Dict[str, Any]:
    return {"type": "error", "error": {"type": kind, "message": message}}


def stream_of(*parts: Union[Dict[str, Any], List[Dict[str, Any]]]) -> bytes:
    """``sse()`` over a mix of single events and event lists."""
    flat: List[Dict[str, Any]] = []
    for part in parts:
        flat.extend(part if isinstance(part, list) else [part])
    return sse(*flat)


class ChunkedBody(httpx2.SyncByteStream):
    """A response body delivered piece by piece, with a log of when.

    ``log`` records ``yield<i>`` as each piece is pulled and ``closed`` when
    the SDK closes the response — which is how a test tells "the callback ran
    before the stream finished" and "cancelling released the connection".
    """

    def __init__(self, pieces: List[bytes], log: List[str]) -> None:
        self._pieces = pieces
        self.log = log

    def __iter__(self):
        for i, piece in enumerate(self._pieces):
            self.log.append(f"yield{i}")
            yield piece

    def close(self) -> None:
        self.log.append("closed")


Scripted = Union[bytes, ChunkedBody, Tuple[int, Dict[str, Any]], Tuple[int, Dict[str, Any], Dict[str, str]]]


class Wire:
    """Serves scripted responses in order and records every request body.

    ``GET /v1/models/{id}`` is answered apart from the script — 404 unless
    ``models`` maps the id to a body — and recorded in ``model_lookups`` only,
    so a test's ``requests`` stay the Messages calls it scripted.
    """

    def __init__(self, *responses: Scripted, models: Optional[Dict[str, Any]] = None) -> None:
        self._responses = list(responses)
        self._models = models or {}
        self.requests: List[Dict[str, Any]] = []
        self.urls: List[str] = []
        self.model_lookups: List[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            model_id = str(request.url).rsplit("/", 1)[-1]
            self.model_lookups.append(model_id)
            body = self._models.get(model_id)
            if isinstance(body, tuple):  # (status, body): a scripted failure
                return httpx2.Response(body[0], json=body[1])
            if body is None:
                return httpx2.Response(404, json={
                    "type": "error",
                    "error": {"type": "not_found_error", "message": "Not support"},
                })
            return httpx2.Response(200, json=body)
        self.urls.append(str(request.url))
        self.requests.append(json.loads(request.content))
        scripted = self._responses.pop(0)
        if isinstance(scripted, bytes):
            return httpx2.Response(
                200, headers={"content-type": "text/event-stream"}, content=scripted,
            )
        if isinstance(scripted, ChunkedBody):
            return httpx2.Response(
                200, headers={"content-type": "text/event-stream"}, stream=scripted,
            )
        status, body, *rest = scripted
        return httpx2.Response(status, json=body, headers=rest[0] if rest else None)


def attach(llm: Any, wire: Wire) -> Wire:
    """Point an ``anthropic-messages`` ``LLMClient`` at ``wire``."""
    llm.client = anthropic.Anthropic(
        api_key="test-key", base_url="http://wire.test", max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(wire)),
    )
    return wire
