"""A scripted OpenAI Responses endpoint for tests — below the real SDK.

Same bargain as ``anthropic_wire.py``: everything above this file is the real
thing — the ``openai`` client, its SSE decoder, its event models, its exception
mapping — and only the socket is replaced, by an ``httpx.MockTransport`` (this
SDK is on ``httpx``; the Anthropic one is on ``httpx2``).

One thing a scripted socket cannot give, and this file does not pretend to: the
*order* and *fill* of events a live server sends. Every event built here is
checked against the SDK's own ``ResponseStreamEvent`` union (``validated``), so
a fixture cannot drift from the SDK's shapes — but that a server omits
``encrypted_content`` from an item event and states it only in the terminal
response is a fact about a server, scripted here because pi-mono recorded it
(``docs/design/llm-api-adapters.md`` appendix A.6), not observed.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx
import openai
from openai.types.responses import ResponseStreamEvent
from pydantic import TypeAdapter

_EVENT = TypeAdapter(ResponseStreamEvent)

Event = Dict[str, Any]


def usage(input_tokens: int = 10, output_tokens: int = 5, *, cached: int = 0,
          reasoning: int = 0, cache_write: int = 0) -> Dict[str, Any]:
    return {
        "input_tokens": input_tokens,
        # ``cache_write_tokens`` is required by ``openai`` 3.x and unknown to
        # 2.x, which keeps it as an extra field; the fixtures validate on both.
        "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": cache_write},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": reasoning},
        "total_tokens": input_tokens + output_tokens,
    }


def response_obj(
    output: Optional[List[Dict[str, Any]]] = None, *, status: str = "completed",
    model: str = "gpt-test", usage_: Optional[Dict[str, Any]] = None,
    incomplete_reason: Optional[str] = None, error: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    return {
        "id": "resp_test", "object": "response", "created_at": 0, "model": model,
        "status": status, "output": output or [], "parallel_tool_calls": True,
        "tool_choice": "auto", "tools": [], "usage": usage_,
        "incomplete_details": {"reason": incomplete_reason} if incomplete_reason else None,
        "error": error,
    }


# -- output items -------------------------------------------------------------


def message_item(text: str, *, item_id: str = "msg_1", status: str = "completed") -> Dict[str, Any]:
    return {
        "id": item_id, "type": "message", "role": "assistant", "status": status,
        "content": [{"type": "output_text", "text": text, "annotations": []}] if text else [],
    }


def function_call_item(call_id: str, name: str, arguments: str, *,
                       item_id: Optional[str] = "fc_1", status: str = "completed") -> Dict[str, Any]:
    item = {"type": "function_call", "call_id": call_id, "name": name,
            "arguments": arguments, "status": status}
    if item_id is not None:
        item["id"] = item_id
    return item


def reasoning_item(item_id: str = "rs_1", *, summary: str = "",
                   encrypted: Optional[str] = None) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "id": item_id, "type": "reasoning",
        "summary": [{"type": "summary_text", "text": summary}] if summary else [],
    }
    if encrypted is not None:
        item["encrypted_content"] = encrypted
    return item


# -- event sequences ----------------------------------------------------------


def _added(index: int, item: Dict[str, Any]) -> Event:
    return {"type": "response.output_item.added", "output_index": index, "item": item}


def _done(index: int, item: Dict[str, Any]) -> Event:
    return {"type": "response.output_item.done", "output_index": index, "item": item}


def text_events(index: int, *chunks: str, item_id: str = "msg_1") -> List[Event]:
    """A message item arriving as text deltas."""
    text = "".join(chunks)
    part = {"type": "output_text", "text": "", "annotations": []}
    where = {"item_id": item_id, "output_index": index, "content_index": 0}
    return [
        _added(index, message_item("", item_id=item_id, status="in_progress")),
        {"type": "response.content_part.added", **where, "part": part},
        *[{"type": "response.output_text.delta", **where, "delta": c, "logprobs": []}
          for c in chunks],
        {"type": "response.output_text.done", **where, "text": text, "logprobs": []},
        {"type": "response.content_part.done", **where, "part": {**part, "text": text}},
        _done(index, message_item(text, item_id=item_id)),
    ]


def function_call_events(index: int, call_id: str, name: str, *json_chunks: str,
                         item_id: Optional[str] = "fc_1") -> List[Event]:
    arguments = "".join(json_chunks)
    where = {"item_id": item_id or "", "output_index": index}
    return [
        _added(index, function_call_item(call_id, name, "", item_id=item_id, status="in_progress")),
        *[{"type": "response.function_call_arguments.delta", **where, "delta": c}
          for c in json_chunks],
        {"type": "response.function_call_arguments.done", **where,
         "arguments": arguments, "name": name},
        _done(index, function_call_item(call_id, name, arguments, item_id=item_id)),
    ]


def reasoning_events(index: int, item_id: str = "rs_1", *summary_chunks: str,
                     encrypted: Optional[str] = None,
                     encrypted_on_item_event: bool = True) -> List[Event]:
    """A reasoning item. ``encrypted_on_item_event=False`` scripts the server
    that states ``encrypted_content`` only in the terminal response."""
    summary = "".join(summary_chunks)
    where = {"item_id": item_id, "output_index": index, "summary_index": 0}
    events: List[Event] = [_added(index, reasoning_item(item_id))]
    if summary_chunks:
        events.append({"type": "response.reasoning_summary_part.added", **where,
                       "part": {"type": "summary_text", "text": ""}})
        events += [{"type": "response.reasoning_summary_text.delta", **where, "delta": c}
                   for c in summary_chunks]
        events.append({"type": "response.reasoning_summary_text.done", **where, "text": summary})
        events.append({"type": "response.reasoning_summary_part.done", **where,
                       "part": {"type": "summary_text", "text": summary}})
    events.append(_done(index, reasoning_item(
        item_id, summary=summary, encrypted=encrypted if encrypted_on_item_event else None)))
    return events


def created(**kwargs: Any) -> Event:
    return {"type": "response.created",
            "response": response_obj(status="in_progress", **kwargs)}


def completed(output: List[Dict[str, Any]], **kwargs: Any) -> Event:
    kwargs.setdefault("usage_", usage())
    return {"type": "response.completed", "response": response_obj(output, **kwargs)}


def incomplete(output: List[Dict[str, Any]], reason: str = "max_output_tokens",
               **kwargs: Any) -> Event:
    kwargs.setdefault("usage_", usage())
    return {"type": "response.incomplete", "response": response_obj(
        output, status="incomplete", incomplete_reason=reason, **kwargs)}


def failed(code: str = "server_error", message: str = "boom", **kwargs: Any) -> Event:
    return {"type": "response.failed", "response": response_obj(
        status="failed", error={"code": code, "message": message}, **kwargs)}


def error_event(code: str = "server_error", message: str = "boom") -> Event:
    return {"type": "error", "code": code, "message": message, "param": None}


def validated(events: List[Event]) -> List[Event]:
    """``events`` numbered, each checked against the SDK's own event union.

    Strict validation, not the SDK's lenient ``construct``: a fixture with a
    misspelled or missing field must fail here, in the fixture, rather than
    reach the adapter as an attribute that is quietly ``None``.
    """
    numbered = [{**event, "sequence_number": i} for i, event in enumerate(events)]
    for event in numbered:
        _EVENT.validate_python(event)
    return numbered


def stream_of(*parts: Union[Event, List[Event]]) -> bytes:
    flat: List[Event] = []
    for part in parts:
        flat.extend(part if isinstance(part, list) else [part])
    return b"".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode("utf-8")
        for event in validated(flat)
    )


class ChunkedBody(httpx.SyncByteStream):
    """A response body delivered piece by piece, with a log of when."""

    def __init__(self, pieces: List[bytes], log: List[str]) -> None:
        self._pieces = pieces
        self.log = log

    def __iter__(self):
        for i, piece in enumerate(self._pieces):
            self.log.append(f"yield{i}")
            yield piece

    def close(self) -> None:
        self.log.append("closed")


Scripted = Union[bytes, ChunkedBody, Tuple[int, Dict[str, Any]],
                 Tuple[int, Dict[str, Any], Dict[str, str]]]


class Wire:
    """Serves scripted responses in order and records every request body."""

    def __init__(self, *responses: Scripted) -> None:
        self._responses = list(responses)
        self.requests: List[Dict[str, Any]] = []
        self.urls: List[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        self.requests.append(json.loads(request.content))
        scripted = self._responses.pop(0)
        if isinstance(scripted, bytes):
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=scripted)
        if isinstance(scripted, ChunkedBody):
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=scripted)
        status, body, *rest = scripted
        return httpx.Response(status, json=body, headers=rest[0] if rest else None)


def attach(llm: Any, wire: Wire) -> Wire:
    """Point an ``openai-responses`` ``LLMClient`` at ``wire``."""
    llm.client = openai.OpenAI(
        api_key="test-key", base_url="http://wire.test/v1", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(wire)),
    )
    return wire
