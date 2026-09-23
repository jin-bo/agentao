"""A connection that drops while the response body is read is retried.

Before the response arrives, both SDKs wrap a transport failure as their own
``APIConnectionError``, which was always retried. After it — the stream is
open — they do not: ``openai`` 2.x lets the raw ``httpx`` exception out of the
iterator, ``anthropic`` the raw ``httpx2`` one, and the classifier called both
permanent. A gateway closing an idle SSE body is the everyday case
(``RemoteProtocolError: peer closed connection without sending complete
message body``), and it ended the turn even when nothing had been shown.

The second half is the same drop arriving cleanly: the body just ends. On the
two wires whose terminal event is mandatory that is a truncation, and what was
accumulated can be a tool call cut off mid-JSON. codex retries both
(``CodexErr::Stream`` is retryable; "stream closed before
response.completed").

Everything here goes through the real SDKs; only the socket is scripted.
Nothing changes once text has reached the host — a retry would show it twice.
"""

from __future__ import annotations

import json
import logging
from typing import Any, List

import httpx
import httpx2
import openai
import pytest

from agentao.cancellation import CancellationToken
from agentao.llm import client as client_mod
from agentao.llm._retry import StreamEndedEarlyError, _classify_retry
from agentao.llm.client import MAX_RETRY_ATTEMPTS, LLMClient
from tests.support import anthropic_wire as aw
from tests.support import openai_responses_wire as rw

# ``LLMClient`` opens ``agentao.log`` in the process cwd: see ``isolated_cwd``.
pytestmark = pytest.mark.usefixtures("isolated_cwd")

HELLO = [{"role": "user", "content": "hi"}]
PEER_CLOSED = "peer closed connection without sending complete message body"


@pytest.fixture(autouse=True)
def slept(monkeypatch) -> List[float]:
    delays: List[float] = []
    monkeypatch.setattr(client_mod.time, "sleep", delays.append)

    def record(delay, _token=None):
        delays.append(delay)
        return True

    monkeypatch.setattr(client_mod, "_interruptible_sleep", record)
    return delays


# -- the classifier -------------------------------------------------------------


@pytest.mark.parametrize("module", [httpx, httpx2], ids=["httpx", "httpx2"])
@pytest.mark.parametrize("name", [
    "RemoteProtocolError", "ReadError", "ReadTimeout", "ConnectError", "WriteError",
])
def test_a_transport_failure_is_retryable_from_either_http_stack(module, name):
    assert _classify_retry(getattr(module, name)("x")) == (True, None, None)


@pytest.mark.parametrize("module", [httpx, httpx2], ids=["httpx", "httpx2"])
@pytest.mark.parametrize("name", ["LocalProtocolError", "ProxyError", "UnsupportedProtocol"])
def test_a_transport_failure_that_says_the_request_is_wrong_is_not(module, name):
    assert _classify_retry(getattr(module, name)("x")) == (False, None, None)


def test_the_anthropic_wire_classifies_them_the_same_way():
    llm = _anthropic()
    assert llm._adapter.classify_retry(httpx2.RemoteProtocolError("x")) == (True, None, None)
    assert llm._adapter.classify_retry(StreamEndedEarlyError("x")) == (True, None, None)
    assert llm._adapter.classify_retry(httpx2.LocalProtocolError("x")) == (False, None, None)


# -- scripted bodies ------------------------------------------------------------


def _dies(stack: Any, first: bytes, exc: BaseException) -> Any:
    """A body that sends ``first`` and then loses the connection."""

    class Dies(stack.SyncByteStream):
        def __iter__(self):
            yield first
            raise exc

    return Dies()


class _Script:
    """Serves one scripted body per request, over either HTTP stack."""

    def __init__(self, stack: Any, *bodies: Any) -> None:
        self.stack = stack
        self.bodies = list(bodies)
        self.requests = 0

    def __call__(self, request: Any) -> Any:
        self.requests += 1
        body = self.bodies.pop(0)
        headers = {"content-type": "text/event-stream"}
        if isinstance(body, bytes):
            return self.stack.Response(200, headers=headers, content=body)
        return self.stack.Response(200, headers=headers, stream=body)


def _chunk(**delta: Any) -> bytes:
    return b"data: " + json.dumps({
        "id": "c", "object": "chat.completion.chunk", "created": 1, "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
    }).encode() + b"\n\n"


def _completions_answer(text: str) -> bytes:
    done = json.loads(_chunk(content=text)[len(b"data: "):])
    done["choices"][0]["finish_reason"] = "stop"
    return b"data: " + json.dumps(done).encode() + b"\n\ndata: [DONE]\n\n"


def _completions(script: _Script) -> LLMClient:
    llm = LLMClient(api_key="k", base_url="http://wire.test/v1", model="m",
                    logger=logging.getLogger("test.retry"))
    llm.client = openai.OpenAI(
        api_key="k", base_url="http://wire.test/v1", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(script)),
    )
    return llm


def _responses(script: _Script) -> LLMClient:
    llm = LLMClient(api_key="k", base_url="http://wire.test/v1", model="gpt-test",
                    api_format="openai-responses", logger=logging.getLogger("test.retry"))
    llm.client = openai.OpenAI(
        api_key="k", base_url="http://wire.test/v1", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(script)),
    )
    return llm


def _anthropic() -> LLMClient:
    return LLMClient(api_key="k", base_url="https://api.example.test", model="claude-test",
                     api_format="anthropic-messages", max_tokens=4096,
                     logger=logging.getLogger("test.retry"))


def _responses_answer(text: str) -> bytes:
    return rw.stream_of(rw.created(), rw.text_events(0, text), rw.completed([rw.message_item(text)]))


def _anthropic_answer(text: str) -> bytes:
    return aw.stream_of(aw.message_start(), aw.text_block(0, text), aw.message_end())


# -- a raw transport failure mid-body ---------------------------------------------


def test_chat_completions_retries_a_dropped_stream_that_showed_nothing(slept):
    script = _Script(httpx,
                     _dies(httpx, _chunk(role="assistant"), httpx.RemoteProtocolError(PEER_CLOSED)),
                     _completions_answer("recovered"))
    response = _completions(script).chat_stream(HELLO, on_text_chunk=lambda _c: None)
    assert response.choices[0].message.content == "recovered"
    assert script.requests == 2
    assert len(slept) == 1


def test_chat_completions_does_not_retry_once_text_was_shown():
    script = _Script(httpx,
                     _dies(httpx, _chunk(content="half"), httpx.RemoteProtocolError(PEER_CLOSED)),
                     _completions_answer("must not be requested"))
    shown: List[str] = []
    # openai 3.x wraps it as ``APIConnectionError``; 2.x lets it out raw.
    with pytest.raises((httpx.RemoteProtocolError, openai.APIConnectionError)) as raised:
        _completions(script).chat_stream(HELLO, on_text_chunk=shown.append)
    assert shown == ["half"]
    assert raised.value.streamed is True
    assert script.requests == 1


def test_a_dropped_stream_that_never_recovers_spends_the_attempts_and_raises():
    script = _Script(httpx, *[
        _dies(httpx, _chunk(role="assistant"), httpx.ReadError("reset"))
        for _ in range(MAX_RETRY_ATTEMPTS)
    ])
    with pytest.raises((httpx.ReadError, openai.APIConnectionError)):
        _completions(script).chat_stream(HELLO)
    assert script.requests == MAX_RETRY_ATTEMPTS


def test_the_responses_wire_retries_a_dropped_stream():
    script = _Script(httpx,
                     _dies(httpx, rw.stream_of(rw.created()), httpx.RemoteProtocolError(PEER_CLOSED)),
                     _responses_answer("recovered"))
    response = _responses(script).chat_stream(HELLO)
    assert response.choices[0].message.content == "recovered"
    assert script.requests == 2


def test_the_anthropic_wire_retries_a_dropped_stream():
    llm = _anthropic()
    script = _Script(httpx2,
                     _dies(httpx2, aw.stream_of(aw.message_start()),
                           httpx2.RemoteProtocolError(PEER_CLOSED)),
                     _anthropic_answer("recovered"))
    aw.attach(llm, script)
    assert llm.chat_stream(HELLO).choices[0].message.content == "recovered"
    assert script.requests == 2


# -- a body that just ends before its terminal event ------------------------------


def test_anthropic_retries_a_tool_call_cut_off_mid_json():
    """The fragment used to come back as a finished call with ``{"pa`` for
    arguments, and the runtime executed it."""
    llm = _anthropic()
    wire = aw.attach(llm, aw.Wire(
        aw.stream_of(aw.message_start(), aw.tool_use_block(0, "toolu_1", "read_file", '{"pa')),
        aw.stream_of(aw.message_start(),
                     aw.tool_use_block(0, "toolu_1", "read_file", '{"path": "a"}'),
                     aw.message_end("tool_use")),
    ))
    (call,) = llm.chat_stream(HELLO).choices[0].message.tool_calls
    assert json.loads(call.function.arguments) == {"path": "a"}
    assert len(wire.requests) == 2


def test_anthropic_retries_a_stream_with_nothing_after_message_start():
    llm = _anthropic()
    wire = aw.attach(llm, aw.Wire(aw.stream_of(aw.message_start()), _anthropic_answer("ok")))
    assert llm.chat([{"role": "user", "content": "hi"}]).choices[0].message.content == "ok"
    assert len(wire.requests) == 2


def test_responses_retries_a_tool_call_cut_off_mid_json():
    script = _Script(httpx,
                     rw.stream_of(rw.created(), rw.function_call_events(0, "call_1", "read_file", '{"pa')[:2]),
                     rw.stream_of(rw.created(),
                                  rw.function_call_events(0, "call_1", "read_file", '{"path": "a"}'),
                                  rw.completed([rw.function_call_item("call_1", "read_file", '{"path": "a"}')])))
    (call,) = _responses(script).chat_stream(HELLO).choices[0].message.tool_calls
    assert json.loads(call.function.arguments) == {"path": "a"}
    assert script.requests == 2


def test_a_truncation_that_never_recovers_raises_rather_than_answering():
    llm = _anthropic()
    wire = aw.attach(llm, aw.Wire(*[aw.stream_of(aw.message_start())] * MAX_RETRY_ATTEMPTS))
    with pytest.raises(StreamEndedEarlyError):
        llm.chat_stream(HELLO)
    assert len(wire.requests) == MAX_RETRY_ATTEMPTS


def test_a_cancel_before_anything_was_shown_is_not_a_truncation():
    """Cancelling breaks out of the loop with no terminal event too; that is
    the runtime's cancelled shape, and must not be retried."""
    token = CancellationToken()
    events = rw.stream_of(rw.created(), rw.function_call_events(0, "c", "read_file", '{"a": 1}'),
                          rw.completed([rw.function_call_item("c", "read_file", '{"a": 1}')]))
    pieces = [piece + b"\n\n" for piece in events.split(b"\n\n") if piece]

    class CancelsAfterFirst(httpx.SyncByteStream):
        def __iter__(self):
            yield pieces[0]
            token.cancel("test")
            yield from pieces[1:]

    script = _Script(httpx, CancelsAfterFirst(), _responses_answer("must not be requested"))
    response = _responses(script).chat_stream(HELLO, cancellation_token=token)
    assert response.finish_reason_reported is False
    assert script.requests == 1


def test_chat_completions_still_reports_a_missing_finish_reason_instead():
    """Optional in practice on this wire, so the flag stays a report."""
    script = _Script(httpx, _chunk(role="assistant") + _chunk(content="x") + b"data: [DONE]\n\n")
    response = _completions(script).chat_stream(HELLO)
    assert response.choices[0].message.content == "x"
    assert response.finish_reason_reported is False
    assert script.requests == 1


# -- telling the caller: on_retry → LLM_RETRY → "Reconnecting… n/N" ---------------


def test_on_retry_is_told_each_retry_before_its_sleep(slept):
    seen: List[dict] = []

    def on_retry(info):
        seen.append({**info, "slept_before": len(slept)})

    script = _Script(httpx,
                     _dies(httpx, _chunk(role="assistant"), httpx.RemoteProtocolError(PEER_CLOSED)),
                     _dies(httpx, _chunk(role="assistant"), httpx.ReadTimeout("t")),
                     _completions_answer("ok"))
    _completions(script).chat_stream(HELLO, on_retry=on_retry)
    assert [(s["retry"], s["max_retries"], s["reason"], s["slept_before"]) for s in seen] == [
        (1, MAX_RETRY_ATTEMPTS - 1, "RemoteProtocolError", 0),
        (2, MAX_RETRY_ATTEMPTS - 1, "ReadTimeout", 1),
    ]
    assert [s["delay_s"] for s in seen] == [round(d, 2) for d in slept]


def test_on_retry_reaches_the_non_streaming_path_too():
    llm = _anthropic()
    aw.attach(llm, aw.Wire(
        (503, {"type": "error", "error": {"type": "api_error", "message": "x"}}),
        _anthropic_answer("ok"),
    ))
    seen: List[dict] = []
    llm.chat(HELLO, on_retry=seen.append)
    assert [(s["retry"], s["reason"]) for s in seen] == [(1, "status=503")]


def test_a_callback_that_raises_does_not_stop_the_retry():
    def on_retry(_info):
        raise RuntimeError("display broke")

    script = _Script(httpx,
                     _dies(httpx, _chunk(role="assistant"), httpx.ReadError("reset")),
                     _completions_answer("recovered"))
    response = _completions(script).chat_stream(HELLO, on_retry=on_retry)
    assert response.choices[0].message.content == "recovered"


def test_a_turn_emits_llm_retry_on_the_transport():
    from pathlib import Path

    from agentao import Agentao
    from agentao.transport import EventType

    agent = Agentao(api_key="k", base_url="http://wire.test/v1", model="gpt-test",
                    api_format="openai-responses", working_directory=Path.cwd())
    events: List[Any] = []
    try:
        agent.transport.subscribe(events.append)
        rw.attach(agent.llm, rw.Wire(
            (503, {"error": {"message": "busy", "type": "server_error"}}),
            _responses_answer("hello"),
        ))
        assert agent.chat("hi") == "hello"
    finally:
        agent.close()
    retries = [e.data for e in events if e.type == EventType.LLM_RETRY]
    assert [(r["retry"], r["max_retries"], r["reason"]) for r in retries] == [
        (1, MAX_RETRY_ATTEMPTS - 1, "status=503"),
    ]
    kinds = [e.type for e in events]
    assert kinds.index(EventType.LLM_RETRY) < kinds.index(EventType.LLM_TEXT)


@pytest.fixture
def printed(monkeypatch) -> List[str]:
    lines: List[str] = []
    monkeypatch.setattr("agentao.cli._globals.console.print",
                        lambda text="", *_a, **_k: lines.append(str(text)))
    return lines


def test_the_cli_prints_reconnecting_n_of_n(printed):
    from agentao.cli.transport import emit_event
    from agentao.transport import AgentEvent, EventType

    emit_event(object(), AgentEvent(EventType.LLM_RETRY, {
        "retry": 2, "max_retries": 4, "delay_s": 3.2, "reason": "RemoteProtocolError",
    }))
    (line,) = printed
    assert "Reconnecting… 2/4" in line
    assert "RemoteProtocolError" in line and "3.2s" in line


def test_the_cli_escapes_the_reason_and_ignores_a_malformed_payload(printed):
    from agentao.cli.transport import emit_event
    from agentao.transport import AgentEvent, EventType

    emit_event(object(), AgentEvent(EventType.LLM_RETRY, {
        "retry": 1, "max_retries": 4, "reason": "[black on black]x",
    }))
    emit_event(object(), AgentEvent(EventType.LLM_RETRY, {"retry": "1"}))
    (line,) = printed
    assert "[black on black]" not in line.replace("\\[black on black]", "")


def test_chat_stream_does_not_resend_after_a_cancel_with_zero_retry_after(monkeypatch):
    """Same P2 on the streaming path: the dropped stream is retryable, the
    turn is cancelled while it fails, and ``Retry-After: 0`` made the wait
    zero — the sleep has to report the cancel, not time out."""
    import agentao.llm._retry as retry_mod

    monkeypatch.setattr(client_mod, "_interruptible_sleep", retry_mod._interruptible_sleep)
    monkeypatch.setattr(client_mod, "_compute_backoff_delay", lambda *_a, **_k: 0.0)
    token = CancellationToken()

    class DiesAndCancels(httpx.SyncByteStream):
        def __iter__(self):
            yield _chunk(role="assistant")
            token.cancel("test")
            raise httpx.RemoteProtocolError(PEER_CLOSED)

    script = _Script(httpx, DiesAndCancels(), _completions_answer("must not be requested"))
    with pytest.raises(httpx.RemoteProtocolError):
        _completions(script).chat_stream(HELLO, cancellation_token=token)
    assert script.requests == 1
