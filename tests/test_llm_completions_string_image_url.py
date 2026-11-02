"""A bare-string ``image_url`` goes out as ``{"url": ...}`` on Chat Completions.

#616, measured on Gemini's OpenAI-compatible endpoint (2026-11-02,
``gemini-flash-latest``): the object form returns 200, the string form returns
``400 INVALID_ARGUMENT: Value is not a struct: "data:image/png;base64,…"``.
That body's only "image" is the echoed ``data:image/...`` URL, which the
image-rejection classifier skips on purpose, so no recovery ran. The part is in
history, so every later turn sent it again and failed the same way.

Everything goes through the real ``openai`` SDK. Only the socket is scripted,
and it answers like that endpoint.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, List

import httpx
import openai
import pytest

from agentao import Agentao
from agentao.llm._openai_completions import _with_object_image_urls
from agentao.llm._retry import _is_image_rejection

pytestmark = pytest.mark.usefixtures("isolated_cwd")

DATA_URL = "data:image/png;base64,AAAA"  # placeholder for the probe's 1x1 PNG
PROMPT = "Reply with one word."

#: The probe's error body, image data replaced by the placeholder.
GEMINI_400 = [{"error": {
    "code": 400,
    "message": f'Value is not a struct: "{DATA_URL}"',
    "status": "INVALID_ARGUMENT",
}}]


class GeminiLike:
    """Answers like the measured endpoint: 400 for a string ``image_url``,
    an answer otherwise (streamed when asked). Records every request body."""

    def __init__(self) -> None:
        self.bodies: List[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        for message in body["messages"]:
            for part in message.get("content") or []:
                if isinstance(part, dict) and isinstance(part.get("image_url"), str):
                    return httpx.Response(400, json=GEMINI_400)
        if not body.get("stream"):
            return httpx.Response(200, json={
                "id": "c1", "object": "chat.completion", "created": 0, "model": "gemini-x",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "ok"}}],
            })

        def chunk(delta: dict, finish: Any = None) -> str:
            body = {"id": "c1", "object": "chat.completion.chunk", "created": 0,
                    "model": "gemini-x",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            return f"data: {json.dumps(body)}\n\n"

        sse = chunk({"role": "assistant", "content": "ok"}) + chunk({}, "stop") + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=sse.encode())


def _client(endpoint: GeminiLike) -> openai.OpenAI:
    return openai.OpenAI(
        api_key="k", base_url="http://wire.test/v1", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(endpoint)),
    )


def test_the_measured_400_is_not_read_as_an_image_rejection():
    """Why recovery never ran, pinned on the recorded body: the classifier
    stays narrow on purpose, so the fix is the request shape (#616)."""
    endpoint = GeminiLike()
    with pytest.raises(openai.BadRequestError) as caught:
        _client(endpoint).chat.completions.create(model="gemini-x", messages=[
            {"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": DATA_URL},
            ]},
        ])
    assert _is_image_rejection(caught.value, [PROMPT]) is False


def test_a_string_image_url_no_longer_fails_every_later_turn():
    """Before: both turns ended in the same 400. Now: both are answered, the
    image goes out as an object, and history keeps what the host wrote."""
    agent = Agentao(
        api_key="k", base_url="http://wire.test/v1", model="gemini-x",
        api_format="openai-completions", working_directory=Path.cwd(),
    )
    try:
        agent.add_message("user", [
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": DATA_URL},
        ])
        written = copy.deepcopy(agent.messages[0])
        endpoint = GeminiLike()
        agent.llm.client = _client(endpoint)

        assert agent.chat("first") == "ok"
        assert agent.chat("second") == "ok"

        assert len(endpoint.bodies) == 2
        for body in endpoint.bodies:
            sent = body["messages"][1]["content"]
            assert sent == [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": DATA_URL}},
            ]
        assert agent.messages[0] == written
    finally:
        agent.close()


def test_nothing_to_change_is_the_same_list():
    """The byte-identical golden capture depends on this, as it does for
    ``_with_wire_tool_ids``."""
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": [
            {"type": "text", "text": "t"},
            {"type": "image_url", "image_url": {"url": DATA_URL, "detail": "low"}},
        ]},
        {"role": "assistant", "content": None},
    ]
    assert _with_object_image_urls(messages) is messages


@pytest.mark.parametrize("value", [123, ["https://x.test/a.png"], None, {"url": DATA_URL}],
                         ids=["int", "list", "none", "object"])
def test_only_the_string_shape_changes(value):
    """Any other value goes out as written, and the provider decides."""
    messages = [{"role": "user", "content": [{"type": "image_url", "image_url": value}]}]
    assert _with_object_image_urls(messages) is messages


def test_mixed_content_keeps_order_and_copies_only_what_changes():
    text = {"type": "text", "text": "a"}
    as_string = {"type": "image_url", "image_url": DATA_URL, "cache_control": {"x": 1}}
    as_object = {"type": "image_url", "image_url": {"url": "https://x.test/b.png"}}
    audio = {"type": "input_audio", "input_audio": {"data": "d", "format": "wav"}}
    untouched = {"role": "user", "content": "plain"}
    changed = {"role": "user", "content": [text, as_string, as_object, audio]}
    messages = [untouched, changed]
    before = copy.deepcopy(messages)

    out = _with_object_image_urls(messages)

    assert out is not messages and out[0] is untouched
    parts: List[Any] = out[1]["content"]
    assert parts == [
        text,
        {"type": "image_url", "image_url": {"url": DATA_URL}, "cache_control": {"x": 1}},
        as_object,
        audio,
    ]
    assert parts[0] is text and parts[2] is as_object and parts[3] is audio
    assert messages == before
    assert changed["content"][1] is as_string and as_string["image_url"] == DATA_URL
