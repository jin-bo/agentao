"""Stage 1 of ``docs/design/llm-api-adapters.md``: the ``anthropic-messages`` wire.

History stays OpenAI-shaped; the adapter translates an outbound copy and folds
the response back into the duck-type the runtime already reads. Every test here
runs the **real** ``anthropic`` SDK over a scripted socket
(``tests/support/anthropic_wire.py``), so the request bodies asserted below are
the JSON the SDK actually serialized and the events are the SDK's own models.
"""

import copy
import json
import logging
import sys

import pytest

import anthropic

from agentao.cancellation import CancellationToken
from agentao.context_manager import (
    ContextManager,
    is_context_too_long_error,
    parse_observed_context_limit,
)
from agentao.llm import client as client_mod
from agentao.llm._anthropic_messages import (
    AnthropicMessagesAdapter,
    translate_messages,
    translate_tools,
)
from agentao.llm._api_format import resolve_api_format
from agentao.llm.client import LLMClient
from tests.support.anthropic_wire import (
    ChunkedBody,
    Wire,
    attach,
    error_event,
    message_end,
    message_start,
    redacted_thinking_block,
    sse,
    stream_of,
    text_block,
    thinking_block,
    tool_use_block,
)

TOOLS = [
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a file.",
        "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}},
                       "required": ["file_path"]}}},
    {"type": "function", "function": {"name": "ping", "description": ""}},
]


def _llm(**kwargs) -> LLMClient:
    kwargs.setdefault("max_tokens", 4096)
    return LLMClient(
        api_key="test-key", base_url="https://api.example.test", model="claude-test",
        api_format="anthropic-messages", logger=logging.getLogger("test.anthropic"),
        **kwargs,
    )


def _ok(text: str = "ok") -> bytes:
    return stream_of(message_start(), text_block(0, text), message_end())


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_api_format_resolves_the_default_and_normalises_spelling():
    assert resolve_api_format(None) == "openai-completions"
    assert resolve_api_format("   ") == "openai-completions"
    assert resolve_api_format(" Anthropic-Messages ") == "anthropic-messages"


@pytest.mark.parametrize("value", ["anthropic", "openai-response", "gemini-api", "messages"])
def test_an_unknown_or_unimplemented_format_fails_closed_and_lists_the_valid_ones(value):
    """A format the design names but nothing implements is not accepted: it
    would mean quietly speaking some other protocol at that endpoint."""
    with pytest.raises(ValueError) as exc:
        LLMClient(api_key="k", base_url="http://x", model="m", api_format=value,
                  logger=logging.getLogger("test.anthropic"))
    assert "openai-completions" in str(exc.value)
    assert "anthropic-messages" in str(exc.value)


def test_the_format_selects_the_adapter_and_the_sdk_client():
    llm = _llm()
    assert llm.api_format == "anthropic-messages"
    assert isinstance(llm._adapter, AnthropicMessagesAdapter)
    assert isinstance(llm.client, anthropic.Anthropic)
    assert llm.client.max_retries == 0


def test_a_base_url_carried_over_from_the_compat_endpoint_loses_its_v1():
    """The SDK posts to ``<base_url>/v1/messages``; ``…/v1/`` would 404."""
    llm = LLMClient(
        api_key="k", base_url="https://api.anthropic.com/v1/", model="m",
        api_format="anthropic-messages", logger=logging.getLogger("test.anthropic"),
    )
    assert str(llm.client.base_url).rstrip("/") == "https://api.anthropic.com"
    assert llm.base_url == "https://api.anthropic.com/v1/"  # what the host configured


def test_reconfigure_with_no_base_url_falls_back_to_the_sdk_default(monkeypatch):
    """``reconfigure(base_url=None)`` is the ACP provider switch's
    "clear the endpoint" path; it must not die on ``None.rstrip``."""
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)  # the SDK reads it
    llm = LLMClient(
        api_key="k", base_url="https://gateway.test/v1", model="m",
        api_format="anthropic-messages", logger=logging.getLogger("test.anthropic"),
    )
    llm.reconfigure(api_key="k2", base_url=None)
    assert llm.base_url is None
    assert str(llm.client.base_url).rstrip("/") == "https://api.anthropic.com"
    assert llm.client.api_key == "k2"


def test_a_missing_sdk_says_what_to_install(monkeypatch):
    """Core dependency or not, a stripped environment can still lack it."""
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(ImportError, match=r"pip install 'anthropic>=1\.6\.0'"):
        _llm()


# ---------------------------------------------------------------------------
# Request translation — the body the SDK put on the wire
# ---------------------------------------------------------------------------

PNG = "iVBORw0KGgo="

HISTORY = [
    {"role": "system", "content": "You are agentao."},
    # A compaction summary: ``role: "system"`` in the middle of history.
    {"role": "system", "content": "[Conversation Summary]\nearlier work"},
    {"role": "user", "content": [
        {"type": "text", "text": "look at this"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG}"}},
    ]},
    {"role": "assistant", "content": "Reading three files.",
     "reasoning_content": "display copy, cut to 500 chars...",
     "tool_calls": [
         {"id": "toolu_a", "type": "function",
          "function": {"name": "read_file", "arguments": "{\"file_path\": \"a\"}"}},
         {"id": "toolu_b", "type": "function",
          "function": {"name": "read_file", "arguments": "{\"file_path\": \"b\"}"}},
         {"id": "toolu_c", "type": "function",
          "function": {"name": "ping", "arguments": "{}"}},
     ]},
    {"role": "tool", "tool_call_id": "toolu_a", "name": "read_file", "content": "A"},
    {"role": "tool", "tool_call_id": "toolu_b", "name": "read_file", "content": "B"},
    {"role": "tool", "tool_call_id": "toolu_c", "name": "ping", "content": ""},
    # A persisted background notification, then stage 0a's request-only tail.
    {"role": "user", "content": "<system-reminder>bg task done</system-reminder>"},
    {"role": "user", "content": "<system-reminder>todos</system-reminder>"},
]


def test_a_full_history_becomes_exactly_this_request():
    llm = _llm(temperature=0.3)  # not sent on this wire: see the test below
    wire = attach(llm, Wire(_ok()))
    before = copy.deepcopy(HISTORY)

    llm.chat_stream(HISTORY, tools=TOOLS, max_tokens=1000)

    assert wire.urls == ["http://wire.test/v1/messages"]
    assert wire.requests[0] == {
        "model": "claude-test",
        "max_tokens": 1000,
        "stream": True,
        "system": "You are agentao.",
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text":
                    "<system-reminder>\n[Conversation Summary]\nearlier work\n</system-reminder>"},
                {"type": "text", "text": "look at this"},
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/png", "data": PNG}},
            ]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Reading three files."},
                {"type": "tool_use", "id": "toolu_a", "name": "read_file",
                 "input": {"file_path": "a"}},
                {"type": "tool_use", "id": "toolu_b", "name": "read_file",
                 "input": {"file_path": "b"}},
                {"type": "tool_use", "id": "toolu_c", "name": "ping", "input": {}},
            ]},
            # Three results, the notification and the tail: five OpenAI
            # messages, one user turn, results first.
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_a", "content": "A"},
                {"type": "tool_result", "tool_use_id": "toolu_b", "content": "B"},
                {"type": "tool_result", "tool_use_id": "toolu_c"},
                {"type": "text", "text": "<system-reminder>bg task done</system-reminder>"},
                {"type": "text", "text": "<system-reminder>todos</system-reminder>"},
            ]},
        ],
        "tools": [
            {"name": "read_file", "description": "Read a file.",
             "input_schema": TOOLS[0]["function"]["parameters"]},
            {"name": "ping", "input_schema": {"type": "object", "properties": {}}},
        ],
    }
    # The translation works on a copy: history is not the adapter's to mutate.
    assert HISTORY == before


def test_a_system_message_between_a_call_and_its_results_lands_after_them():
    """§6.2. The API rejects anything between ``tool_use`` and ``tool_result``."""
    _, turns = translate_messages([
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "toolu_1", "type": "function",
             "function": {"name": "ping", "arguments": "{}"}}]},
        {"role": "system", "content": "mid-loop notice"},
        {"role": "tool", "tool_call_id": "toolu_1", "name": "ping", "content": "pong"},
    ])
    assert [t["role"] for t in turns] == ["user", "assistant", "user"]
    assert [b["type"] for b in turns[2]["content"]] == ["tool_result", "text"]
    assert "mid-loop notice" in turns[2]["content"][1]["text"]


def test_a_history_the_last_overflow_rung_opened_on_an_assistant_gets_a_user_turn():
    """§6.3, built by the real rung: ``apply_minimal_history`` steps back to
    the assistant that made the calls, so the window opens on it."""
    manager = ContextManager(_llm(), memory_tool=None)
    history = [
        {"role": "user", "content": "start"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "toolu_1", "type": "function",
             "function": {"name": "ping", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "toolu_1", "name": "ping", "content": "pong"},
    ]
    window = manager.apply_minimal_history(history, keep_tail=1)
    assert window[0]["role"] == "assistant"  # the shape this rule exists for

    _, turns = translate_messages([{"role": "system", "content": "S"}] + window)
    assert [t["role"] for t in turns] == ["user", "assistant", "user"]
    assert turns[0]["content"][0]["type"] == "text"
    assert turns[2]["content"][0]["tool_use_id"] == "toolu_1"


def test_a_tool_id_from_another_provider_is_rewritten_on_both_sides_alike():
    _, turns = translate_messages([
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "functions.read_file:0", "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "functions.read_file:0",
         "name": "read_file", "content": "x"},
    ])
    call, result = turns[1]["content"][0], turns[2]["content"][0]
    assert call["id"] == result["tool_use_id"] == "functions_read_file_0"


def test_malformed_arguments_in_history_go_out_as_an_empty_object():
    _, turns = translate_messages([
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "toolu_1", "type": "function",
             "function": {"name": "ping", "arguments": "{\"a\": "}}]},
    ])
    assert turns[1]["content"][0]["input"] == {}


def test_a_remote_image_url_is_passed_through_for_the_provider_to_fetch():
    """Raising would be permanent: the part is in history, so every later
    request would raise as well."""
    _, turns = translate_messages([{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "https://example.test/cat.png"}},
    ]}])
    assert turns[0]["content"] == [
        {"type": "image", "source": {"type": "url", "url": "https://example.test/cat.png"}},
    ]


@pytest.mark.parametrize("part", [
    {"type": "image_url", "image_url": {"url": "file:///etc/passwd"}},
    {"type": "input_audio", "input_audio": {"data": "x", "format": "wav"}},
])
def test_a_part_this_wire_cannot_express_fails_loudly_rather_than_vanishing(part):
    with pytest.raises(ValueError, match="anthropic-messages"):
        translate_messages([{"role": "user", "content": [part]}])


def test_ids_that_would_collide_after_the_rewrite_stay_distinct_and_paired():
    """``call.1`` and ``call:1`` both rewrite to ``call_1``, and so do two ids
    that differ only past the 64th character. A duplicate ``tool_use`` id is a
    400 — on every later request, since the ids are in history."""
    long_a, long_b = "x" * 64 + "A", "x" * 64 + "B"
    raw = ["call.1", "call_1", "call:1", long_a, long_b]
    _, turns = translate_messages(
        [{"role": "user", "content": "go"},
         {"role": "assistant", "content": "", "tool_calls": [
             {"id": i, "type": "function", "function": {"name": "ping", "arguments": "{}"}}
             for i in raw]}]
        + [{"role": "tool", "tool_call_id": i, "name": "ping", "content": i} for i in raw]
    )
    calls = [b["id"] for b in turns[1]["content"]]
    results = {b["content"]: b["tool_use_id"] for b in turns[2]["content"]}

    assert len(set(calls)) == 5
    assert all(len(c) <= 64 and c.replace("_", "").replace("-", "").isalnum() for c in calls)
    # The valid id keeps its own spelling even though a rewritten one that
    # wants the same spelling came first: byte-for-byte for ids minted here.
    assert calls[1] == "call_1"
    assert [results[i] for i in raw] == calls  # each result answers its own call


def test_empty_messages_are_dropped_not_sent_as_empty_blocks():
    _, turns = translate_messages([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "   "},
        {"role": "user", "content": "again"},
    ])
    assert turns == [{"role": "user", "content": [
        {"type": "text", "text": "hi"}, {"type": "text", "text": "again"},
    ]}]


def test_tools_translate_from_the_canonical_schema():
    assert translate_tools(TOOLS)[0] == {
        "name": "read_file", "description": "Read a file.",
        "input_schema": TOOLS[0]["function"]["parameters"],
    }


def test_max_tokens_is_always_sent_because_this_wire_requires_it():
    """The summarizer calls ``chat()`` with no cap at all."""
    llm = _llm(max_tokens=2048)
    wire = attach(llm, Wire(_ok()))
    llm.chat([{"role": "user", "content": "summarize"}])
    assert wire.requests[0]["max_tokens"] == 2048


def test_temperature_is_not_a_parameter_of_this_wire():
    """Pinned against the SDK itself, because that is where the fact lives:
    ``messages.create`` has no ``temperature`` and refuses one outright. The
    day an SDK release brings it back, this test says so."""
    with pytest.raises(TypeError, match="temperature"):
        _llm().client.messages.create(
            model="m", max_tokens=10, stream=True, temperature=0.2,
            messages=[{"role": "user", "content": "hi"}],
        )


def test_extra_body_is_merged_into_the_request_body():
    """Extended thinking, and a gateway's ``temperature``, both arrive this way."""
    llm = _llm(extra_body={
        "thinking": {"type": "enabled", "budget_tokens": 2000}, "temperature": 1,
    })
    wire = attach(llm, Wire(_ok()))
    llm.chat_stream([{"role": "user", "content": "hi"}])
    body = wire.requests[0]
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 2000}
    assert body["temperature"] == 1


# ---------------------------------------------------------------------------
# Cache breakpoints (§6.9) — the same opt-in knob, the same three sites
# ---------------------------------------------------------------------------


def _markers(value) -> int:
    if isinstance(value, dict):
        return ("cache_control" in value) + sum(_markers(v) for v in value.values())
    if isinstance(value, list):
        return sum(_markers(v) for v in value)
    return 0


def test_breakpoints_land_on_system_last_tool_and_the_end_of_stable_history():
    llm = _llm(prompt_cache="anthropic", prompt_cache_ttl="1h")
    wire = attach(llm, Wire(_ok()))
    before = copy.deepcopy(HISTORY)
    marker = {"type": "ephemeral", "ttl": "1h"}

    llm.chat_stream(HISTORY, tools=TOOLS, max_tokens=1000, cache_boundary=1)

    body = wire.requests[0]
    assert body["system"] == [
        {"type": "text", "text": "You are agentao.", "cache_control": marker},
    ]
    assert body["tools"][-1]["cache_control"] == marker
    last_turn = body["messages"][-1]["content"]
    # End of *stable* history is the notification; the request-only tail after
    # it carries nothing.
    assert last_turn[-2]["cache_control"] == marker
    assert "cache_control" not in last_turn[-1]
    assert _markers(body) == 3
    assert HISTORY == before


def test_a_breakpoint_on_a_tool_result_is_hoisted_onto_the_result_block():
    llm = _llm(prompt_cache="anthropic")
    wire = attach(llm, Wire(_ok()))
    llm.chat_stream(HISTORY[:6], tools=None, max_tokens=1000, cache_boundary=0)
    result = wire.requests[0]["messages"][-1]["content"][-1]
    assert result == {
        "type": "tool_result", "tool_use_id": "toolu_b",
        "content": [{"type": "text", "text": "B"}],
        "cache_control": {"type": "ephemeral"},
    }


def test_a_call_that_does_not_opt_in_is_not_marked():
    llm = _llm(prompt_cache="anthropic")
    wire = attach(llm, Wire(_ok()))
    llm.chat(HISTORY, tools=TOOLS)  # the summarizer's shape: no cache_boundary
    assert _markers(wire.requests[0]) == 0


# ---------------------------------------------------------------------------
# Response — stream events into the shared duck-type
# ---------------------------------------------------------------------------

LONG_THOUGHT = "step " * 300  # 1500 chars: three times the display copy's cap


def _rich_response() -> bytes:
    return stream_of(
        message_start(input_tokens=12, cache_creation_input_tokens=30,
                      cache_read_input_tokens=400),
        thinking_block(0, [LONG_THOUGHT[:700], LONG_THOUGHT[700:]], "SIG-one=="),
        redacted_thinking_block(1, "ENCRYPTED"),
        text_block(2, "Let me ", "look."),
        tool_use_block(3, "toolu_1", "read_file", '{"file_', 'path": "no', 'te.txt"}'),
        tool_use_block(4, "toolu_2", "ping"),
        message_end("tool_use", output_tokens=77),
    )


def test_a_streamed_response_becomes_the_duck_type_the_runtime_reads():
    llm = _llm()
    attach(llm, Wire(_rich_response()))
    chunks = []

    response = llm.chat_stream(
        [{"role": "user", "content": "hi"}], tools=TOOLS, on_text_chunk=chunks.append,
    )

    message = response.choices[0].message
    assert chunks == ["Let me ", "look."]
    assert message.content == "Let me look."
    assert message.reasoning_content == LONG_THOUGHT
    assert response.choices[0].finish_reason == "tool_calls"
    assert response.finish_reason_reported is True
    assert response.model == "claude-test"
    # Fragmented arguments arrive whole; a call with no arguments is ``{}``.
    assert [(tc.id, tc.function.name, tc.function.arguments) for tc in message.tool_calls] == [
        ("toolu_1", "read_file", '{"file_path": "note.txt"}'),
        ("toolu_2", "ping", "{}"),
    ]
    assert message.anthropic_thinking_blocks == [
        {"type": "thinking", "thinking": LONG_THOUGHT, "signature": "SIG-one=="},
        {"type": "redacted_thinking", "data": "ENCRYPTED"},
    ]


def test_usage_folds_the_cached_prompt_back_in():
    """§6.11. ``input_tokens`` alone is the *uncached* remainder — most of the
    prompt is missing from it on a working cache."""
    llm = _llm()
    attach(llm, Wire(_rich_response()))
    usage = llm.chat_stream([{"role": "user", "content": "hi"}]).usage
    assert usage.prompt_tokens == 12 + 30 + 400
    assert usage.completion_tokens == 77
    assert usage.total_tokens == 442 + 77
    assert (usage.cache_creation_input_tokens, usage.cache_read_input_tokens) == (30, 400)
    assert (llm.total_prompt_tokens, llm.total_completion_tokens) == (442, 77)


def test_text_reaches_the_callback_before_the_stream_has_finished():
    log = []
    body = ChunkedBody([
        stream_of(message_start(), text_block(0, "Hel")[:2]),
        stream_of(text_block(0, "lo")[1:], message_end()),
    ], log)
    llm = _llm()
    attach(llm, Wire(body))

    response = llm.chat_stream(
        [{"role": "user", "content": "hi"}],
        on_text_chunk=lambda chunk: log.append(f"cb:{chunk}"),
    )

    assert response.choices[0].message.content == "Hello"
    assert log.index("cb:Hel") < log.index("yield1")
    assert log[-1] == "closed"


def test_chat_and_chat_stream_return_the_same_response():
    """Both run over the streaming transport — the SDK refuses a non-streaming
    request at agentao's default ``max_tokens`` — so parity is by construction,
    and ``chat()`` must fire no callback."""
    streamed_llm, plain_llm = _llm(), _llm()
    attach(streamed_llm, Wire(_rich_response()))
    wire = attach(plain_llm, Wire(_rich_response()))

    streamed = streamed_llm.chat_stream([{"role": "user", "content": "hi"}], tools=TOOLS)
    plain = plain_llm.chat([{"role": "user", "content": "hi"}], tools=TOOLS)

    def shape(response):
        message = response.choices[0].message
        return (
            message.content, message.reasoning_content,
            message.anthropic_thinking_blocks,
            [(tc.id, tc.function.name, json.loads(tc.function.arguments))
             for tc in message.tool_calls],
            response.choices[0].finish_reason, response.finish_reason_reported,
            response.model, vars(response.usage),
        )

    assert shape(plain) == shape(streamed)
    assert wire.requests[0]["stream"] is True


def test_the_default_max_tokens_does_not_trip_the_sdks_non_streaming_guard():
    """Falsifies the alternative: a true non-streaming ``messages.create`` at
    65,536 raises ``ValueError`` inside the SDK before anything is sent."""
    llm = _llm(max_tokens=65536)
    with pytest.raises(ValueError, match="Streaming is required"):
        llm.client.messages.create(
            model="m", max_tokens=65536, messages=[{"role": "user", "content": "hi"}],
        )
    attach(llm, Wire(_ok("summary")))
    assert llm.chat([{"role": "user", "content": "hi"}]).choices[0].message.content == "summary"


@pytest.mark.parametrize("stop_reason, finish_reason", [
    ("end_turn", "stop"), ("stop_sequence", "stop"), ("pause_turn", "stop"),
    ("max_tokens", "length"), ("model_context_window_exceeded", "length"),
    ("tool_use", "tool_calls"), ("refusal", "content_filter"),
])
def test_stop_reasons_map_onto_the_finish_reasons_the_runtime_gates_on(stop_reason, finish_reason):
    llm = _llm()
    attach(llm, Wire(stream_of(message_start(), text_block(0, "x"), message_end(stop_reason))))
    response = llm.chat_stream([{"role": "user", "content": "hi"}])
    assert response.choices[0].finish_reason == finish_reason
    assert response.finish_reason_reported is True


def test_a_stream_that_just_ends_is_not_reported_as_a_finished_answer():
    llm = _llm()
    attach(llm, Wire(stream_of(message_start(), text_block(0, "half an ans"))))
    # Shown first; before that, the same stream is retried instead
    # (``test_llm_retry_dropped_stream.py``).
    response = llm.chat_stream(
        [{"role": "user", "content": "hi"}], on_text_chunk=lambda _chunk: None,
    )
    assert response.choices[0].message.content == "half an ans"
    assert response.finish_reason_reported is False


def test_an_unsigned_thinking_block_is_not_carried():
    """The signature is the block's last delta. Cut off before it, the block
    is unsigned, and an unsigned block sent back is rejected."""
    llm = _llm()
    attach(llm, Wire(stream_of(
        message_start(), thinking_block(0, ["partial thought"], ""),
        text_block(1, "answer"), message_end(),
    )))
    message = llm.chat_stream([{"role": "user", "content": "hi"}]).choices[0].message
    assert message.reasoning_content == "partial thought"
    assert not hasattr(message, "anthropic_thinking_blocks")


def test_a_gateway_that_sends_the_tool_input_up_front_is_read_too():
    llm = _llm()
    attach(llm, Wire(stream_of(
        message_start(),
        tool_use_block(0, "toolu_1", "read_file", initial_input={"file_path": "a"}),
        message_end("tool_use"),
    )))
    (call,) = llm.chat_stream([{"role": "user", "content": "hi"}]).choices[0].message.tool_calls
    assert json.loads(call.function.arguments) == {"file_path": "a"}


def test_cancelling_mid_stream_returns_the_partial_text_and_closes_the_stream():
    log = []
    token = CancellationToken()
    body = ChunkedBody([
        stream_of(message_start(), text_block(0, "first")[:2]),
        stream_of(text_block(0, " second")[1:], message_end()),
    ], log)
    llm = _llm()
    attach(llm, Wire(body))

    def on_chunk(chunk):
        log.append(f"cb:{chunk}")
        token.cancel("test")

    response = llm.chat_stream(
        [{"role": "user", "content": "hi"}], on_text_chunk=on_chunk,
        cancellation_token=token,
    )

    assert response.choices[0].message.content == "first"
    assert response.finish_reason_reported is False
    assert "cb: second" not in log
    assert log[-1] == "closed"


# ---------------------------------------------------------------------------
# Errors and retry
# ---------------------------------------------------------------------------


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(client_mod.time, "sleep", slept.append)
    return slept


def test_an_overload_inside_the_stream_is_retried_when_nothing_was_shown(no_sleep):
    """It arrives on an HTTP 200, so the SDK raises a bare ``APIStatusError``
    with ``status_code == 200`` — permanent, to a status-only classifier."""
    llm = _llm()
    wire = attach(llm, Wire(sse(error_event("overloaded_error")), _ok("recovered")))
    response = llm.chat_stream([{"role": "user", "content": "hi"}])
    assert response.choices[0].message.content == "recovered"
    assert len(wire.requests) == 2
    assert llm._adapter.classify_retry(
        anthropic.APIStatusError(
            "x", response=_http_response(200),
            body={"type": "error", "error": {"type": "overloaded_error"}},
        )
    ) == (True, 529, None)


def test_an_error_after_text_was_shown_is_not_retried(no_sleep):
    llm = _llm()
    wire = attach(llm, Wire(
        stream_of(message_start(), text_block(0, "partial")[:2], error_event("overloaded_error")),
        _ok("must not be requested"),
    ))
    chunks = []
    with pytest.raises(anthropic.APIStatusError) as exc:
        llm.chat_stream([{"role": "user", "content": "hi"}], on_text_chunk=chunks.append)
    assert chunks == ["partial"]
    assert exc.value.streamed is True
    assert len(wire.requests) == 1


def test_a_rate_limit_is_retried_and_honours_retry_after(no_sleep):
    llm = _llm()
    wire = attach(llm, Wire(
        (429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}},
         {"retry-after": "7"}),
        _ok("after the wait"),
    ))
    assert llm.chat([{"role": "user", "content": "hi"}]).choices[0].message.content == "after the wait"
    assert no_sleep == [7.0]
    assert len(wire.requests) == 2


def test_an_invalid_request_is_not_retried(no_sleep):
    llm = _llm()
    wire = attach(llm, Wire(
        (400, {"type": "error", "error": {"type": "invalid_request_error", "message": "nope"}}),
    ))
    with pytest.raises(anthropic.BadRequestError):
        llm.chat_stream([{"role": "user", "content": "hi"}])
    assert len(wire.requests) == 1
    assert no_sleep == []


def test_an_output_cap_the_model_states_is_adopted_once_and_kept(no_sleep):
    llm = _llm(max_tokens=65536)
    wire = attach(llm, Wire(
        (400, {"type": "error", "error": {
            "type": "invalid_request_error",
            "message": "max_tokens: 65536 > 64000, which is the maximum allowed "
                       "number of output tokens for claude-test"}}),
        _ok(), _ok(), _ok(),
    ))
    llm.chat_stream([{"role": "user", "content": "hi"}], max_tokens=65536)
    llm.chat_stream([{"role": "user", "content": "again"}], max_tokens=65536)
    assert [r["max_tokens"] for r in wire.requests] == [65536, 64000, 64000]
    assert no_sleep == []  # a repair, not a retry

    llm.reset_capability_latches()  # a model switch: the cap was that model's
    llm.chat_stream([{"role": "user", "content": "new model"}], max_tokens=65536)
    assert wire.requests[-1]["max_tokens"] == 65536


def _http_response(status: int):
    import httpx2

    return httpx2.Response(status, request=httpx2.Request("POST", "http://wire.test/v1/messages"))


@pytest.mark.parametrize("message, limit", [
    ("prompt is too long: 213462 tokens > 200000 maximum", 200000),
    ("input length and `max_tokens` exceed context limit: 188240 + 21333 > 200000, "
     "decrease input length or `max_tokens` and try again", 200000),
])
def test_both_native_overflow_errors_are_recognised_and_name_the_window(message, limit):
    """Through the real exception: the runtime matches on ``str(exc)``, and the
    SDK decides what that string contains."""
    llm = _llm()
    attach(llm, Wire(
        (400, {"type": "error", "error": {"type": "invalid_request_error", "message": message}}),
    ))
    with pytest.raises(anthropic.BadRequestError) as exc:
        llm.chat_stream([{"role": "user", "content": "hi"}])
    assert is_context_too_long_error(exc.value)
    assert parse_observed_context_limit(exc.value)[0] == limit


def test_a_callers_message_level_breakpoint_moves_onto_the_last_block():
    """There is no message-level ``cache_control`` on this wire. The marker
    was already charged against the budget, so it must not vanish."""
    marker = {"type": "ephemeral"}
    _, turns = translate_messages([
        {"role": "user", "content": "stable context", "cache_control": marker},
        {"role": "assistant", "content": "", "cache_control": marker,
         "anthropic_thinking_blocks": [
             {"type": "thinking", "thinking": "t", "signature": "S"}]},
    ])
    assert turns[0]["content"] == [
        {"type": "text", "text": "stable context", "cache_control": marker},
    ]
    # An assistant turn that is only thinking has nowhere legal to put one.
    assert turns[1]["content"] == [{"type": "thinking", "thinking": "t", "signature": "S"}]


def test_the_extra_body_shadow_warning_knows_this_wires_structural_fields(caplog):
    """``extra_body`` merges last-wins, so ``system`` there replaces agentao's
    whole system prompt. ``thinking`` and ``temperature`` are how a host is
    *meant* to send those on this wire, and must not warn."""
    with caplog.at_level(logging.WARNING, logger="test.anthropic"):
        _llm(extra_body={"system": "mine", "thinking": {"type": "enabled"}, "temperature": 1})
    (record,) = [r for r in caplog.records if "structural" in r.getMessage()]
    assert "system" in record.getMessage()
    assert "thinking" not in record.getMessage()
    assert "temperature" not in record.getMessage()


def test_a_callers_breakpoint_on_the_system_message_survives_the_lift():
    """The system message leaves the list before the per-message handling
    runs, and ``apply_cache_control`` treats a marked message as already
    placed — so without this the caller's breakpoint and its ttl just vanish,
    even with agentao's own marking switched on."""
    marker = {"type": "ephemeral", "ttl": "1h"}
    history = [
        {"role": "system", "content": "You are agentao.", "cache_control": marker},
        {"role": "user", "content": "hi"},
    ]
    system, _ = translate_messages(history)
    assert system == [{"type": "text", "text": "You are agentao.", "cache_control": marker}]

    llm = _llm(prompt_cache="anthropic")  # configured 5m; the caller's 1h must win
    wire = attach(llm, Wire(_ok()))
    llm.chat_stream(history, cache_boundary=0)
    assert wire.requests[0]["system"] == system
    assert "cache_control" not in history[1]
