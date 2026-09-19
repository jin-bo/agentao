"""A stream that fails part-way still counts what the server said it cost.

The session totals were added after ``consume_stream`` *returned*, so an
attempt that raised — an ``error`` event inside the stream, a connection that
dropped — added nothing, though ``message_start`` had already stated the whole
input count. 0.5.1's release notes admitted it ("a request that failed
mid-stream may go uncounted"); this closes the part of it that can be closed.

Three things it deliberately does not claim:

* it is what the server **reported**, not what it billed;
* a failed attempt and the retry after it are **two requests** and both count —
  what must not happen is one attempt counted twice;
* the Chat Completions wire states usage only in its last chunk, so a stream
  that dies before it has nothing to keep. That wire is unchanged.
"""

import json

import anthropic
import httpx
import openai
import pytest

import agentao.llm.client as client_mod
from agentao.cancellation import CancellationToken
from agentao.llm.client import LLMClient
from tests.support.anthropic_wire import (
    Wire, attach, error_event, message_end, message_start, sse, stream_of, text_block,
)

# ``LLMClient`` opens ``agentao.log`` in the process cwd: see ``isolated_cwd``.
pytestmark = pytest.mark.usefixtures("isolated_cwd")

HELLO = [{"role": "user", "content": "hi"}]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(client_mod.time, "sleep", lambda *_a, **_k: None)


def _llm() -> LLMClient:
    return LLMClient(api_key="k", base_url="https://api.example.test", model="claude-test",
                     api_format="anthropic-messages")


def _totals(llm):
    return (llm.total_prompt_tokens, llm.total_completion_tokens,
            llm.total_cache_read_tokens, llm.total_cache_creation_tokens)


def _ok(text="ok", **usage) -> bytes:
    usage.setdefault("input_tokens", 50)
    return stream_of(message_start(**usage), text_block(0, text),
                     message_end("end_turn", output_tokens=5))


def test_a_stream_that_errors_after_showing_text_still_counts_its_input():
    """Not retried (text was shown), so before this the whole request — 5,100
    input tokens the server had already reported — was in nobody's total."""
    llm = _llm()
    attach(llm, Wire(stream_of(
        message_start(input_tokens=900, cache_read_input_tokens=4000,
                      cache_creation_input_tokens=200),
        text_block(0, "partial")[:2],
        error_event("overloaded_error"),
    )))
    with pytest.raises(anthropic.APIStatusError):
        llm.chat_stream(HELLO, on_text_chunk=lambda _chunk: None)
    # Output is whatever the server had last said: ``message_start``'s count.
    assert _totals(llm) == (5100, 1, 4000, 200)


def test_a_failed_attempt_and_its_retry_are_two_requests_and_both_count():
    llm = _llm()
    wire = attach(llm, Wire(
        stream_of(message_start(input_tokens=700), error_event("overloaded_error")),
        _ok("recovered", input_tokens=700),
    ))
    assert llm.chat_stream(HELLO).choices[0].message.content == "recovered"
    assert len(wire.requests) == 2
    assert _totals(llm) == (700 + 700, 1 + 5, 0, 0)


def test_one_attempt_is_never_counted_twice():
    """The accumulator is fresh per attempt, and has to be: the count happens
    in a ``finally``, so an attempt that reported *nothing* would otherwise
    re-add whatever the attempt before it had left behind.

    The shape matters. Three attempts that each report 700 cannot tell the two
    apart — a reused accumulator is simply overwritten — which is what the
    first version of this test did, and it passed against the mutation."""
    llm = _llm()
    attach(llm, Wire(
        stream_of(message_start(input_tokens=700), error_event("overloaded_error")),
        sse(error_event("overloaded_error")),      # reports no usage at all
        _ok(input_tokens=50),
    ))
    llm.chat_stream(HELLO)
    assert llm.total_prompt_tokens == 700 + 0 + 50


def test_an_error_before_any_usage_was_reported_adds_nothing():
    llm = _llm()
    attach(llm, Wire(sse(error_event("overloaded_error")), _ok(input_tokens=50)))
    llm.chat_stream(HELLO)
    assert _totals(llm) == (50, 5, 0, 0)


def test_a_rejected_request_adds_nothing():
    """A 400 never became a stream: there is no usage, and none is invented."""
    llm = _llm()
    attach(llm, Wire((400, {"type": "error", "error": {
        "type": "invalid_request_error", "message": "no"}})))
    with pytest.raises(anthropic.BadRequestError):
        llm.chat_stream(HELLO)
    assert _totals(llm) == (0, 0, 0, 0)


def test_a_stream_that_simply_stops_counts_what_it_had_said():
    """Truncated, no exception: builds a response that does not claim a finish.
    Counted before this change too — pinned so the ``finally`` did not move it."""
    llm = _llm()
    attach(llm, Wire(stream_of(message_start(input_tokens=300), text_block(0, "cut")[:2])))
    response = llm.chat_stream(HELLO)
    assert response.finish_reason_reported is False
    assert llm.total_prompt_tokens == 300


def test_a_cancelled_stream_is_counted_once():
    token = CancellationToken()
    llm = _llm()
    attach(llm, Wire(_ok("never shown in full", input_tokens=400)))
    llm.chat_stream(HELLO, cancellation_token=token,
                    on_text_chunk=lambda _chunk: token.cancel("stop"))
    assert llm.total_prompt_tokens == 400


def test_a_successful_request_is_still_counted_exactly_once():
    llm = _llm()
    attach(llm, Wire(_ok(input_tokens=50), _ok(input_tokens=60)))
    llm.chat_stream(HELLO)
    llm.chat_stream(HELLO)
    assert _totals(llm) == (110, 10, 0, 0)


def test_the_chat_completions_wire_has_nothing_to_keep_from_a_dead_stream():
    """Usage arrives in the last chunk only. A stream that dies before it has
    reported nothing, and nothing is made up for it."""
    def chunk(**body):
        base = {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": "gpt-x"}
        return f"data: {json.dumps({**base, **body})}\n\n".encode()

    class Dies(httpx.SyncByteStream):
        def __iter__(self):
            yield chunk(choices=[{"index": 0, "delta": {"role": "assistant", "content": "par"},
                                  "finish_reason": None}])
            raise httpx.ReadError("connection dropped")

    llm = LLMClient(api_key="k", base_url="http://wire.test/v1", model="gpt-x")
    llm.client = openai.OpenAI(
        api_key="k", base_url="http://wire.test/v1", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Dies()))),
    )
    # The transport's own error, and marked as having streamed: a broken
    # fixture (an SDK signature change, say) would raise something else before
    # any chunk was read, and must not pass for "a dead stream adds nothing".
    with pytest.raises(httpx.ReadError) as raised:
        llm.chat_stream(HELLO, on_text_chunk=lambda _chunk: None)
    assert raised.value.streamed is True
    assert _totals(llm) == (0, 0, 0, 0)


def test_the_non_streaming_entry_counts_a_failed_attempt_too():
    """``chat()`` is a stream on this wire as well (the summarizer's path), and
    it counts from the *response* — which a raising attempt never produces."""
    llm = _llm()
    attach(llm, Wire(
        stream_of(message_start(input_tokens=700), error_event("overloaded_error")),
        _ok("recovered", input_tokens=700),
    ))
    assert llm.chat(HELLO).choices[0].message.content == "recovered"
    assert _totals(llm) == (700 + 700, 1 + 5, 0, 0)
