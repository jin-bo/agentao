"""The ``openai-responses`` wire, against the real ``openai`` SDK.

Only the socket is scripted (``tests/support/openai_responses_wire.py``), so a
request body asserted here is what the SDK serialized, and an event the
adapter reads is the SDK's own model — every scripted event is validated
against the SDK's event union before it is served.

What this file cannot show is what a live server *does*: event order, which
fields it fills, which ids it accepts back. Those are observed separately.
"""

import json
from pathlib import Path

import httpx
import openai
import pytest

import agentao.llm.client as client_mod
from agentao import Agentao
from agentao.cancellation import CancellationToken
from agentao.llm._api_format import API_FORMATS, resolve_api_format
from agentao.llm._openai_responses import (
    ResponsesStreamError, compose_tool_id, split_tool_id, translate_messages,
)
from agentao.llm.client import LLMClient
from tests.support.openai_responses_wire import (
    ChunkedBody, Wire, attach, completed, created, error_event, failed,
    function_call_events, function_call_item, incomplete, message_item,
    reasoning_events, reasoning_item, stream_of, text_events, usage,
)

# ``LLMClient`` opens ``agentao.log`` in the process cwd: see ``isolated_cwd``.
pytestmark = pytest.mark.usefixtures("isolated_cwd")

HELLO = [{"role": "user", "content": "hi"}]
READ_FILE = [{"type": "function", "function": {
    "name": "read_file", "description": "Read a file.",
    "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}}},
}}]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(client_mod.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(client_mod, "_interruptible_sleep", lambda *_a, **_k: True)


def _llm(**kwargs) -> LLMClient:
    return LLMClient(api_key="k", base_url="http://wire.test/v1", model="gpt-test",
                     api_format="openai-responses", **kwargs)


def _says(text: str = "ok", **kwargs) -> bytes:
    return stream_of(created(), text_events(0, text), completed([message_item(text)], **kwargs))


def _totals(llm):
    return (llm.total_prompt_tokens, llm.total_completion_tokens,
            llm.total_cache_read_tokens, llm.total_cache_creation_tokens)


# -- the format ---------------------------------------------------------------


def test_the_format_is_implemented_and_a_misspelling_still_fails_closed():
    assert resolve_api_format(" OpenAI-Responses ") == "openai-responses"
    assert "openai-responses" in API_FORMATS
    with pytest.raises(ValueError, match="openai-responses"):
        resolve_api_format("openai-response")


# -- the request --------------------------------------------------------------


def test_the_request_is_stateless_streamed_and_posted_to_responses():
    llm = _llm()
    wire = attach(llm, Wire(_says()))
    llm.chat_stream([{"role": "system", "content": "be brief"}, *HELLO], max_tokens=4000)
    (body,) = wire.requests
    assert wire.urls == ["http://wire.test/v1/responses"]
    assert body["store"] is False and body["stream"] is True
    assert "previous_response_id" not in body
    assert body["max_output_tokens"] == 4000 and body["temperature"] == llm.temperature
    # ``system`` stays an item in place; nothing is hoisted into ``instructions``.
    assert "instructions" not in body
    assert body["input"] == [{"role": "system", "content": "be brief"},
                             {"role": "user", "content": "hi"}]


def test_a_cap_below_the_apis_floor_is_raised_to_it_and_none_names_none():
    llm = _llm()
    wire = attach(llm, Wire(_says(), _says()))
    llm.chat_stream(HELLO, max_tokens=5)
    llm.chat(HELLO)  # the summarizer's call names no cap
    assert wire.requests[0]["max_output_tokens"] == 16
    assert "max_output_tokens" not in wire.requests[1]


def test_tool_definitions_are_flat_and_not_strict():
    """Strict is the API's default for a function tool, and strict mode
    rejects a schema that does not require every property."""
    llm = _llm()
    wire = attach(llm, Wire(_says()))
    llm.chat_stream(HELLO, tools=READ_FILE)
    assert wire.requests[0]["tools"] == [{
        "type": "function", "name": "read_file", "description": "Read a file.",
        "parameters": READ_FILE[0]["function"]["parameters"], "strict": False,
    }]


def test_extra_body_reaches_the_body():
    llm = _llm(extra_body={"prompt_cache_key": "session-1"})
    wire = attach(llm, Wire(_says()))
    llm.chat_stream(HELLO)
    assert wire.requests[0]["prompt_cache_key"] == "session-1"


def test_history_becomes_items_rebuilt_key_by_key():
    """An assistant message fans out into a message item and one item per
    call; keys this wire has no field for are left behind, since an unknown
    key is a 400."""
    items = translate_messages([
        {"role": "user", "content": [
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]},
        {"role": "assistant", "content": "Reading.", "reasoning_content": "hmm",
         "anthropic_thinking_blocks": [{"type": "thinking", "thinking": "x", "signature": "s"}],
         "tool_calls": [{"id": "call_1|fc_1", "type": "function",
                         "function": {"name": "read_file", "arguments": '{"file_path": "a"}'}}]},
        {"role": "tool", "tool_call_id": "call_1|fc_1", "name": "read_file",
         "content": "hello", "cache_control": {"type": "ephemeral"}},
        {"role": "system", "content": "[Conversation Summary] earlier"},
    ])
    assert items == [
        {"role": "user", "content": [
            {"type": "input_text", "text": "look"},
            {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
        ]},
        {"role": "assistant", "content": "Reading."},
        {"type": "function_call", "call_id": "call_1",
         "name": "read_file", "arguments": '{"file_path": "a"}'},
        {"type": "function_call_output", "call_id": "call_1", "output": "hello"},
        {"role": "system", "content": "[Conversation Summary] earlier"},
    ]


def test_translation_does_not_touch_history():
    history = [{"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1|fc_1", "type": "function",
         "function": {"name": "t", "arguments": "{}"}}]}]
    before = json.dumps(history)
    translate_messages(history)
    assert json.dumps(history) == before


# -- one id slot, two wire ids ------------------------------------------------


@pytest.mark.parametrize("call_id, item_id, stored", [
    ("call_1", "fc_1", "call_1|fc_1"),
    ("call_1", None, "call_1"),          # a gateway that sends no item id
    ("a|b", "fc_1", "a|b|fc_1"),         # split on the *last* separator
    ("call_1", "item-7", "call_1"),      # not an item id this API issued
])
def test_the_two_ids_share_one_slot_and_come_back_apart(call_id, item_id, stored):
    assert compose_tool_id(call_id, item_id) == stored
    kept = item_id if stored != call_id else None
    assert split_tool_id(stored) == (call_id, kept)


@pytest.mark.parametrize("foreign", ["toolu_01|x", "call_abc", "a|b", "|fc_1", ""])
def test_an_id_minted_on_another_wire_is_never_split(foreign):
    """History outlives a provider switch. Half of someone else's id as the
    ``call_id`` would unpair the call from its output."""
    assert split_tool_id(foreign) == (foreign, None)


def test_a_call_id_goes_back_as_it_arrived():
    """The round trip: what the model issued is what the next request names,
    on the call item and on its output."""
    llm = _llm()
    wire = attach(llm, Wire(
        stream_of(created(), function_call_events(0, "call_9", "read_file", '{"file_path":', '"a"}'),
                  completed([function_call_item("call_9", "read_file", '{"file_path":"a"}')])),
        _says("done"),
    ))
    call = llm.chat_stream(HELLO, tools=READ_FILE).choices[0].message.tool_calls[0]
    assert (call.id, call.function.arguments) == ("call_9|fc_1", '{"file_path":"a"}')
    llm.chat_stream([*HELLO,
                     {"role": "assistant", "content": None, "tool_calls": [{
                         "id": call.id, "type": "function",
                         "function": {"name": "read_file", "arguments": call.function.arguments}}]},
                     {"role": "tool", "tool_call_id": call.id, "content": "hello"}], tools=READ_FILE)
    assert wire.requests[1]["input"][1:] == [
        {"type": "function_call", "call_id": "call_9",
         "name": "read_file", "arguments": '{"file_path":"a"}'},
        {"type": "function_call_output", "call_id": "call_9", "output": "hello"},
    ]


# -- the response -------------------------------------------------------------


def test_text_streams_through_the_callback_and_is_not_said_twice():
    """The deltas deliver it; the finished item and the terminal response
    both restate it, and neither may append it again."""
    llm = _llm()
    attach(llm, Wire(stream_of(created(model="gpt-test-2026"), text_events(0, "Hel", "lo"),
                               completed([message_item("Hello")]))))
    heard = []
    response = llm.chat_stream(HELLO, on_text_chunk=heard.append)
    assert heard == ["Hel", "lo"]
    assert response.choices[0].message.content == "Hello"
    assert response.choices[0].finish_reason == "stop" and response.finish_reason_reported
    assert response.model == "gpt-test-2026"


def test_a_function_call_is_assembled_and_ends_the_turn_as_tool_calls():
    llm = _llm()
    attach(llm, Wire(stream_of(
        created(), text_events(0, "Reading."),
        function_call_events(1, "call_1", "read_file", '{"file_', 'path": "a"}'),
        function_call_events(2, "call_2", "read_file", '{}', item_id="fc_2"),
        completed([message_item("Reading."),
                   function_call_item("call_1", "read_file", '{"file_path": "a"}'),
                   function_call_item("call_2", "read_file", "{}", item_id="fc_2")]),
    )))
    response = llm.chat_stream(HELLO, tools=READ_FILE)
    calls = response.choices[0].message.tool_calls
    assert [(c.id, c.function.name, c.function.arguments) for c in calls] == [
        ("call_1|fc_1", "read_file", '{"file_path": "a"}'),
        ("call_2|fc_2", "read_file", "{}"),
    ]
    assert response.choices[0].finish_reason == "tool_calls"


def test_a_gateway_that_states_everything_only_in_the_terminal_response():
    """No item events at all: the terminal output is the only copy."""
    llm = _llm()
    attach(llm, Wire(stream_of(created(), completed([
        message_item("Reading."), function_call_item("call_1", "read_file", '{"a": 1}', item_id=None),
    ]))))
    heard = []
    response = llm.chat_stream(HELLO, tools=READ_FILE, on_text_chunk=heard.append)
    message = response.choices[0].message
    assert message.content == "Reading." and heard == ["Reading."]
    assert [(c.id, c.function.arguments) for c in message.tool_calls] == [("call_1", '{"a": 1}')]


def test_an_incomplete_response_is_a_truncation_not_an_answer():
    llm = _llm()
    attach(llm, Wire(stream_of(created(), text_events(0, "The ans"),
                               incomplete([message_item("The ans", status="incomplete")]))))
    response = llm.chat_stream(HELLO)
    assert response.choices[0].finish_reason == "length" and response.finish_reason_reported


def test_a_stream_that_just_stops_does_not_claim_a_finish():
    llm = _llm()
    attach(llm, Wire(stream_of(created(), text_events(0, "cut")[:3])))
    response = llm.chat_stream(HELLO)
    assert response.choices[0].message.content == "cut"
    assert response.finish_reason_reported is False


def test_a_reasoning_summary_is_kept_as_the_display_copy():
    llm = _llm()
    attach(llm, Wire(stream_of(
        created(), reasoning_events(0, "rs_1", "weigh ", "it"), text_events(1, "ok"),
        completed([reasoning_item("rs_1", summary="weigh it"), message_item("ok")]),
    )))
    message = llm.chat_stream(HELLO).choices[0].message
    assert (message.reasoning_content, message.content) == ("weigh it", "ok")


def test_two_summary_parts_are_not_run_together():
    llm = _llm()
    where = {"item_id": "rs_1", "output_index": 0}
    part = {"type": "summary_text", "text": ""}
    attach(llm, Wire(stream_of(
        created(),
        {"type": "response.reasoning_summary_part.added", **where, "summary_index": 0, "part": part},
        {"type": "response.reasoning_summary_text.delta", **where, "summary_index": 0, "delta": "Plan"},
        {"type": "response.reasoning_summary_part.added", **where, "summary_index": 1, "part": part},
        {"type": "response.reasoning_summary_text.delta", **where, "summary_index": 1, "delta": "Check"},
        text_events(1, "ok"), completed([message_item("ok")]),
    )))
    assert llm.chat_stream(HELLO).choices[0].message.reasoning_content == "Plan\n\nCheck"


def test_a_call_that_was_only_opened_takes_its_arguments_from_the_terminal_output():
    """``added`` and then nothing: the terminal response is the only place the
    arguments are stated, and an opened call must not shadow it."""
    llm = _llm()
    opened = function_call_events(0, "call_1", "read_file", '{"a": 1}')[:1]
    attach(llm, Wire(stream_of(created(), opened, completed([
        function_call_item("call_1", "read_file", '{"a": 1}'),
    ]))))
    call = llm.chat_stream(HELLO, tools=READ_FILE).choices[0].message.tool_calls[0]
    assert (call.id, call.function.arguments) == ("call_1|fc_1", '{"a": 1}')


def test_usage_is_the_whole_input_with_the_cached_part_beside_it():
    """``input_tokens`` includes the cached part on this wire — the opposite
    of Anthropic's — so it is the prompt count as it stands."""
    llm = _llm()
    attach(llm, Wire(_says(usage_=usage(5100, 40, cached=4096, reasoning=12))))
    response = llm.chat_stream(HELLO)
    assert (response.usage.prompt_tokens, response.usage.completion_tokens,
            response.usage.total_tokens) == (5100, 40, 5140)
    assert _totals(llm) == (5100, 40, 4096, 0)


def test_the_non_streaming_entry_is_the_same_stream():
    llm = _llm()
    wire = attach(llm, Wire(_says("summary", usage_=usage(300, 9))))
    assert llm.chat(HELLO).choices[0].message.content == "summary"
    assert wire.requests[0]["stream"] is True
    assert _totals(llm) == (300, 9, 0, 0)


# -- failures -----------------------------------------------------------------


def test_an_error_event_is_raised_though_the_sdk_only_yields_it():
    """The SDK hands ``error`` out of its iterator like any other event. Read
    only the events it knows, and an adapter returns an empty clean response."""
    llm = _llm()
    wire = attach(llm, Wire(stream_of(created(), error_event("invalid_prompt", "no"))))
    with pytest.raises(ResponsesStreamError, match="invalid_prompt: no"):
        llm.chat_stream(HELLO)
    assert len(wire.requests) == 1  # permanent: not retried


def test_a_transient_failure_inside_the_stream_is_retried():
    llm = _llm()
    wire = attach(llm, Wire(
        stream_of(created(), error_event("server_error")),
        stream_of(created(), failed("rate_limit_exceeded", "slow down")),
        _says("recovered"),
    ))
    assert llm.chat_stream(HELLO).choices[0].message.content == "recovered"
    assert len(wire.requests) == 3


def test_a_failed_response_still_counts_the_usage_it_stated():
    llm = _llm()
    attach(llm, Wire(stream_of(created(), failed("invalid_prompt", "no", usage_=usage(700, 0)))))
    with pytest.raises(ResponsesStreamError):
        llm.chat_stream(HELLO)
    assert _totals(llm) == (700, 0, 0, 0)


@pytest.mark.parametrize("scripted", [
    # ``response.failed``'s code is a closed enum with no quota member; the
    # bare ``error`` event's is a free string, and is where one would arrive.
    stream_of(created(), error_event("insufficient_quota", "pay up")),
    (429, {"error": {"message": "quota", "type": "insufficient_quota",
                     "code": "insufficient_quota"}}),
], ids=["in-stream", "http"])
def test_an_exhausted_balance_is_not_waited_on(scripted):
    llm = _llm()
    wire = attach(llm, Wire(scripted, _says()))
    with pytest.raises((ResponsesStreamError, openai.RateLimitError)):
        llm.chat_stream(HELLO)
    assert len(wire.requests) == 1


def test_an_http_failure_is_the_sdks_exception_and_takes_the_shared_retry_table():
    llm = _llm()
    wire = attach(llm, Wire((503, {"error": {"message": "busy", "type": "server_error"}}), _says()))
    llm.chat_stream(HELLO)
    assert len(wire.requests) == 2


def test_a_failure_after_text_was_shown_is_not_retried():
    llm = _llm()
    wire = attach(llm, Wire(stream_of(created(), text_events(0, "par")[:3],
                                      error_event("server_error")), _says()))
    with pytest.raises(ResponsesStreamError) as raised:
        llm.chat_stream(HELLO, on_text_chunk=lambda _chunk: None)
    assert raised.value.streamed is True and len(wire.requests) == 1


def test_a_model_that_rejects_temperature_is_asked_once_without_it():
    llm = _llm()
    wire = attach(llm, Wire(
        (400, {"error": {"message": "Unsupported parameter: 'temperature' is not supported "
                                    "with this model.", "type": "invalid_request_error",
                         "param": "temperature", "code": "unsupported_parameter"}}),
        _says(), _says(),
    ))
    llm.chat_stream(HELLO)
    llm.chat_stream(HELLO)
    assert ["temperature" in body for body in wire.requests] == [True, False, False]


def test_a_context_overflow_keeps_the_providers_words():
    """The runtime's overflow ladder reads the error text."""
    llm = _llm()
    attach(llm, Wire((400, {"error": {
        "message": "Your input exceeds the context window of this model.",
        "type": "invalid_request_error", "code": "context_length_exceeded"}})))
    with pytest.raises(openai.BadRequestError, match="context window"):
        llm.chat_stream(HELLO)


def test_a_dead_connection_has_reported_nothing_and_nothing_is_counted():
    class Dies(httpx.SyncByteStream):
        def __iter__(self):
            yield stream_of(created())
            raise httpx.ReadError("connection dropped")

    llm = _llm()
    llm.client = openai.OpenAI(
        api_key="k", base_url="http://wire.test/v1", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Dies()))))
    with pytest.raises(Exception) as raised:
        llm.chat_stream(HELLO)
    assert isinstance(raised.value, (httpx.ReadError, openai.APIConnectionError))
    assert _totals(llm) == (0, 0, 0, 0)


def test_a_cancel_stops_reading_and_releases_the_connection():
    log = []
    events = stream_of(created(), text_events(0, "one", "two", "three"),
                       completed([message_item("onetwothree")]))
    pieces = [piece + b"\n\n" for piece in events.split(b"\n\n") if piece]
    token = CancellationToken()
    llm = _llm()
    attach(llm, Wire(ChunkedBody(pieces, log)))
    response = llm.chat_stream(HELLO, cancellation_token=token,
                               on_text_chunk=lambda _chunk: token.cancel("stop"))
    assert response.choices[0].message.content == "one"
    assert response.finish_reason_reported is False
    assert log[-1] == "closed" and len(log) < len(pieces)


# -- through the runtime ------------------------------------------------------


def test_a_tool_calling_turn_runs_end_to_end_on_this_wire():
    Path("note.txt").write_text("hello from disk", encoding="utf-8")
    agent = Agentao(api_key="k", base_url="http://wire.test/v1", model="gpt-test",
                    api_format="openai-responses", working_directory=Path.cwd())
    try:
        arguments = json.dumps({"file_path": str(Path("note.txt").resolve())})
        wire = attach(agent.llm, Wire(
            stream_of(created(), function_call_events(0, "call_1", "read_file", arguments),
                      completed([function_call_item("call_1", "read_file", arguments)],
                                usage_=usage(900, 20))),
            _says("The note says hello.", usage_=usage(1000, 8, cached=896)),
        ))
        assert agent.chat("read the note") == "The note says hello."
        agent_history_ids = [
            call["id"] for m in agent.messages for call in m.get("tool_calls") or []
        ] + [m["tool_call_id"] for m in agent.messages if m.get("role") == "tool"]
    finally:
        agent.close()
    second = wire.requests[1]["input"]
    call = next(item for item in second if item.get("type") == "function_call")
    output = next(item for item in second if item.get("type") == "function_call_output")
    # History keeps both ids; the request names only the one that pairs the
    # output, since the item id is tracked against a reasoning item not sent.
    assert call["call_id"] == "call_1" and "id" not in call
    assert agent_history_ids == ["call_1|fc_1", "call_1|fc_1"]
    assert output["call_id"] == "call_1" and "hello from disk" in output["output"]
    # Every item is one the API has a shape for.
    assert {item.get("type", "message") for item in second} == {
        "message", "function_call", "function_call_output"}
    assert _totals(agent.llm) == (1900, 28, 896, 0)
