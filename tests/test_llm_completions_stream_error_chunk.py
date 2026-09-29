"""An error chunk inside a Chat Completions stream is retried like its status.

An OpenAI-compatible gateway has already sent ``200 OK`` when the upstream
fails, so the failure arrives as a chunk — ``{"error": {...}}`` — and the
``openai`` SDK raises it as a bare ``APIError``: no status, so the shared
classifier called it permanent and the turn ended on a transient overload.
The other two wires already map their in-stream errors to a status
(opencode #40718 fixed the same thing on its OpenAI-compatible path).

Two spellings are real: OpenAI's own vocabulary (``type: "server_error"``,
``code: "rate_limit_exceeded"``) and OpenRouter's documented mid-stream shape,
whose ``code`` is the HTTP status as a number. Everything goes through the real
SDK; only the socket is scripted.
"""

from __future__ import annotations

import json
import logging
from typing import Any, List

import httpx
import openai
import pytest

from agentao.llm import client as client_mod
from agentao.llm.client import MAX_RETRY_ATTEMPTS, LLMClient

# ``LLMClient`` opens ``agentao.log`` in the process cwd: see ``isolated_cwd``.
pytestmark = pytest.mark.usefixtures("isolated_cwd")

HELLO = [{"role": "user", "content": "hi"}]


@pytest.fixture(autouse=True)
def slept(monkeypatch) -> List[float]:
    delays: List[float] = []
    monkeypatch.setattr(client_mod.time, "sleep", delays.append)

    def record(delay, _token=None):
        delays.append(delay)
        return True

    monkeypatch.setattr(client_mod, "_interruptible_sleep", record)
    return delays


class _Script:
    def __init__(self, *bodies: bytes) -> None:
        self.bodies = list(bodies)
        self.requests = 0

    def __call__(self, request: Any) -> Any:
        self.requests += 1
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=self.bodies.pop(0),
        )


def _sse(payload: dict) -> bytes:
    return b"data: " + json.dumps(payload).encode() + b"\n\n"


def _chunk(finish_reason=None, **delta: Any) -> dict:
    return {
        "id": "c", "object": "chat.completion.chunk", "created": 1, "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def _answer(text: str) -> bytes:
    return _sse(_chunk(content=text, finish_reason="stop")) + b"data: [DONE]\n\n"


def _error_chunk(error: dict, *, before: bytes = b"") -> bytes:
    return before + _sse({"error": error})


# OpenRouter's documented mid-stream error, verbatim in shape
# (openrouter.ai/docs/api-reference/errors, "Mid-stream errors").
def _openrouter(code: int, error_type: str) -> bytes:
    payload = _chunk(finish_reason="error", content="")
    payload["provider"] = "OpenAI"
    payload["error"] = {"code": code, "message": "x", "metadata": {"error_type": error_type}}
    return _sse(payload)


def _client(script: _Script) -> LLMClient:
    llm = LLMClient(api_key="k", base_url="http://wire.test/v1", model="m",
                    logger=logging.getLogger("test.stream_error_chunk"))
    llm.client = openai.OpenAI(
        api_key="k", base_url="http://wire.test/v1", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(script)),
    )
    return llm


@pytest.mark.parametrize("body, status", [
    (_error_chunk({"type": "server_error", "code": "server_error", "message": "x"}), 500),
    (_error_chunk({"type": "requests", "code": "rate_limit_exceeded", "message": "x"}), 429),
    (_openrouter(502, "provider_unavailable"), 502),
    (_openrouter(429, "rate_limit_exceeded"), 429),
], ids=["openai-server_error", "openai-rate_limit", "openrouter-502", "openrouter-429"])
def test_an_error_chunk_before_any_text_is_retried(body, status, slept):
    script = _Script(body, _answer("recovered"))
    llm = _client(script)
    retries: List[Any] = []
    response = llm.chat_stream(HELLO, on_text_chunk=lambda _c: None,
                               on_retry=lambda *a: retries.append(a))
    assert response.choices[0].message.content == "recovered"
    assert script.requests == 2
    assert len(slept) == 1


def test_the_classifier_reports_the_mapped_status():
    script = _Script(_openrouter(503, "provider_unavailable"))
    llm = _client(script)
    with pytest.raises(openai.APIError) as raised:
        list(llm.client.chat.completions.create(model="m", messages=HELLO, stream=True))
    assert type(raised.value) is openai.APIError
    assert llm._adapter.classify_retry(raised.value) == (True, 503, None)


@pytest.mark.parametrize("error", [
    {"type": "invalid_request_error", "code": "context_length_exceeded", "message": "too long"},
    {"type": "insufficient_quota", "code": "insufficient_quota", "message": "pay up"},
    {"code": 400, "message": "bad"},
    {"code": 402, "message": "no credits"},
    {"message": "no code at all"},
], ids=["context-length", "quota", "openrouter-400", "openrouter-402", "bare"])
def test_an_error_chunk_that_is_not_transient_is_still_permanent(error):
    script = _Script(_error_chunk(error), _answer("must not be requested"))
    with pytest.raises(openai.APIError):
        _client(script).chat_stream(HELLO, on_text_chunk=lambda _c: None)
    assert script.requests == 1


def test_no_retry_once_text_was_shown():
    """A retry would show the text twice — the rule every wire keeps."""
    body = _error_chunk({"type": "server_error", "message": "x"},
                        before=_sse(_chunk(content="half")))
    script = _Script(body, _answer("must not be requested"))
    shown: List[str] = []
    with pytest.raises(openai.APIError) as raised:
        _client(script).chat_stream(HELLO, on_text_chunk=shown.append)
    assert shown == ["half"]
    assert raised.value.streamed is True
    assert script.requests == 1


def test_an_error_chunk_that_never_clears_spends_the_attempts_and_raises():
    script = _Script(*[
        _error_chunk({"type": "server_error", "message": "x"})
        for _ in range(MAX_RETRY_ATTEMPTS)
    ])
    with pytest.raises(openai.APIError):
        _client(script).chat_stream(HELLO, on_text_chunk=lambda _c: None)
    assert script.requests == MAX_RETRY_ATTEMPTS

