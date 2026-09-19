"""Reasoning that survives a turn on the ``openai-responses`` wire.

The wire is stateless here (``store: false``), so the provider keeps nothing:
a reasoning model's earlier reasoning exists on the next request only if it is
sent back. The API returns each reasoning item's content encrypted for exactly
that, and the item is opaque — whole, or not at all.

It rides a second carrier key on the assistant dict,
``openai_reasoning_items``, beside Anthropic's. Two things follow the carrier
around and are what this file is mostly about:

* the ``fc_`` item id of a function call goes back **only beside the reasoning
  it was produced with** — the API pairs the two and refuses one without the
  other;
* whatever rewrites history wholesale — compaction, a model switch, a session
  restore — has to leave a request the API would still take.

Real ``openai`` SDK over a scripted socket throughout. That a live server
accepts what is asserted here is observed separately.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import Mock

import pytest

import agentao.llm.client as client_mod
from agentao import Agentao
from agentao.acp import session_load as acp_session_load
from agentao.cli.commands import sessions as sessions_cmd
from agentao.embedding.sessions import persist_agent_session
from agentao.llm._openai_responses import translate_messages
from agentao.llm._stream_response import (
    ANTHROPIC_THINKING_BLOCKS, OPENAI_REASONING_ITEMS, WIRE_CARRIER_KEYS,
)
from agentao.llm.client import LLMClient
from agentao.runtime.chat_loop._serialize import _attach_thinking_blocks
from agentao.runtime.model import purge_thinking_artifacts
from tests.support.acp_agents import make_factory
from tests.support.acp_server import make_initialized_server
from tests.support.openai_responses_wire import (
    Wire, attach, completed, created, function_call_events, function_call_item,
    message_item, reasoning_events, reasoning_item, stream_of, text_events,
)

# Agentao here writes to the process cwd: see ``isolated_cwd`` in conftest.py.
pytestmark = pytest.mark.usefixtures("isolated_cwd")

HELLO = [{"role": "user", "content": "hi"}]
SUMMARY = "SUMMARY-MARKER: the user had note.txt read; it says hello."


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(client_mod.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(client_mod, "_interruptible_sleep", lambda *_a, **_k: True)


def _llm() -> LLMClient:
    return LLMClient(api_key="k", base_url="http://wire.test/v1", model="gpt-test",
                     api_format="openai-responses")


def _agent() -> Agentao:
    return Agentao(api_key="k", base_url="http://wire.test/v1", model="gpt-test",
                   api_format="openai-responses", working_directory=Path.cwd())


def _args() -> str:
    return json.dumps({"file_path": str(Path("note.txt").resolve())})


def _tool_turn(i: int, *, encrypted_on_item_event: bool = True) -> bytes:
    """Reasoning, then a call: what a reasoning model's tool turn looks like."""
    rs, fc, call = f"rs_{i}", f"fc_{i}", f"call_{i}"
    return stream_of(
        created(),
        reasoning_events(0, rs, "weigh it", encrypted=f"ENC-{i}",
                         encrypted_on_item_event=encrypted_on_item_event),
        function_call_events(1, call, "read_file", _args(), item_id=fc),
        completed([reasoning_item(rs, summary="weigh it", encrypted=f"ENC-{i}"),
                   function_call_item(call, "read_file", _args(), item_id=fc)]),
    )


def _final(text: str = "The note says hello.") -> bytes:
    return stream_of(created(), text_events(0, text), completed([message_item(text)]))


def _rounds(agent: Agentao, n: int, *after: bytes) -> Wire:
    Path("note.txt").write_text("hello from disk", encoding="utf-8")
    script: List[bytes] = []
    for i in range(n):
        script += [_tool_turn(i), _final()]
    wire = attach(agent.llm, Wire(*script, *after))
    for i in range(n):
        agent.chat(f"read the note, round {i}")
    return wire


def _of(body: Dict[str, Any], kind: str) -> List[Dict[str, Any]]:
    return [item for item in body["input"] if item.get("type") == kind]


def _assert_the_api_would_take_it(body: Dict[str, Any]) -> None:
    """The pairing rules a Responses request is rejected for."""
    items = body["input"]
    calls = {item["call_id"] for item in _of(body, "function_call")}
    outputs = [item["call_id"] for item in _of(body, "function_call_output")]
    assert sorted(outputs) == sorted(calls)          # every call answered, once
    for item in _of(body, "reasoning"):
        assert item["encrypted_content"] and item["id"]   # whole, or not there
    # An ``fc_`` id is named only when the reasoning it was paired with is
    # in the same turn, ahead of it.
    seen_reasoning = False
    for item in items:
        kind = item.get("type")
        if kind == "reasoning":
            seen_reasoning = True
        elif kind == "function_call":
            assert ("id" in item) <= seen_reasoning, item
        elif kind != "function_call_output":
            seen_reasoning = False                   # a new message: a new turn


# -- one turn -----------------------------------------------------------------


def test_the_request_asks_for_reasoning_it_can_send_back():
    llm = _llm()
    wire = attach(llm, Wire(_final()))
    llm.chat_stream(HELLO)
    assert wire.requests[0]["include"] == ["reasoning.encrypted_content"]
    assert wire.requests[0]["store"] is False


@pytest.mark.parametrize("on_item_event", [True, False], ids=["item-event", "terminal-only"])
def test_a_reasoning_item_is_kept_whole_wherever_the_server_stated_it(on_item_event):
    """Some servers state ``encrypted_content`` only in the terminal response."""
    llm = _llm()
    Path("note.txt").write_text("x", encoding="utf-8")
    attach(llm, Wire(_tool_turn(3, encrypted_on_item_event=on_item_event)))
    message = llm.chat_stream(HELLO).choices[0].message
    assert message.openai_reasoning_items == [{
        "id": "rs_3", "summary": [{"type": "summary_text", "text": "weigh it"}],
        "encrypted_content": "ENC-3",
    }]
    assert message.reasoning_content == "weigh it"   # the display copy, as before


def test_reasoning_with_nothing_to_send_back_is_not_carried():
    """No ``encrypted_content`` anywhere: under ``store: false`` the provider
    kept nothing under that id, so there is nothing a later request can name."""
    llm = _llm()
    attach(llm, Wire(stream_of(
        created(), reasoning_events(0, "rs_1", "hmm"), text_events(1, "ok"),
        completed([reasoning_item("rs_1", summary="hmm"), message_item("ok")]))))
    message = llm.chat_stream(HELLO).choices[0].message
    assert not hasattr(message, OPENAI_REASONING_ITEMS)


# -- the next request ---------------------------------------------------------


def test_reasoning_goes_back_ahead_of_its_call_and_the_call_names_its_item_id():
    agent = _agent()
    try:
        wire = _rounds(agent, 1)
    finally:
        agent.close()
    body = wire.requests[1]
    _assert_the_api_would_take_it(body)
    kinds = [item.get("type", "message") for item in body["input"]]
    assert kinds[-3:] == ["reasoning", "function_call", "function_call_output"]
    assert _of(body, "reasoning") == [{
        "type": "reasoning", "id": "rs_0",
        "summary": [{"type": "summary_text", "text": "weigh it"}],
        "encrypted_content": "ENC-0",
    }]
    assert _of(body, "function_call")[0]["id"] == "fc_0"


def test_a_call_whose_reasoning_is_gone_does_not_name_its_item_id():
    """Purged by a switch, lost from an edited session file, never issued by
    the endpoint: the API refuses an ``fc_`` id without the ``rs_`` item it
    was produced beside, and ``call_id`` alone still pairs the output."""
    call = {"id": "call_1|fc_1", "type": "function",
            "function": {"name": "read_file", "arguments": "{}"}}
    carried = {"role": "assistant", "content": None, "tool_calls": [call],
               OPENAI_REASONING_ITEMS: [{"id": "rs_1", "summary": [], "encrypted_content": "E"}]}
    bare = {k: v for k, v in carried.items() if k != OPENAI_REASONING_ITEMS}
    assert [i.get("id") for i in translate_messages([carried])] == ["rs_1", "fc_1"]
    assert translate_messages([bare]) == [
        {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": "{}"}]


def test_reasoning_with_nothing_after_it_is_not_sent():
    """A turn cut off while still thinking records reasoning and nothing else;
    the API refuses a reasoning item without the item that follows it."""
    alone = {"role": "assistant", "content": "", OPENAI_REASONING_ITEMS: [
        {"id": "rs_1", "summary": [], "encrypted_content": "E"}]}
    assert translate_messages([alone, {"role": "user", "content": "go on"}]) == [
        {"role": "user", "content": "go on"}]


def test_the_carrier_is_rebuilt_key_by_key():
    """It is persisted to session files a host can edit; an unknown key is a 400."""
    items = translate_messages([{"role": "assistant", "content": "ok", OPENAI_REASONING_ITEMS: [
        {"id": "rs_1", "summary": [{"type": "summary_text", "text": "s", "extra": 1}],
         "encrypted_content": "E", "status": "completed", "note": "edited by hand"},
    ]}])
    assert items == [
        {"type": "reasoning", "id": "rs_1", "encrypted_content": "E",
         "summary": [{"type": "summary_text", "text": "s"}]},
        {"role": "assistant", "content": "ok"},
    ]


@pytest.mark.parametrize("broken", [
    {"id": "rs_2", "summary": []},                       # lost its encrypted content
    {"summary": [], "encrypted_content": "E"},           # lost its id
    "not a dict",
], ids=["no-content", "no-id", "not-a-dict"])
def test_one_entry_that_cannot_go_back_takes_the_carrier_with_it(broken):
    """Sending the rest would still count as reasoning carried, and the call
    would name its ``fc_`` id — produced, perhaps, beside the item dropped.
    The same all-or-nothing the recording side applies."""
    message = {"role": "assistant", "content": None,
               "tool_calls": [{"id": "call_1|fc_1", "type": "function",
                               "function": {"name": "read_file", "arguments": "{}"}}],
               OPENAI_REASONING_ITEMS: [
                   {"id": "rs_1", "summary": [], "encrypted_content": "E"}, broken]}
    assert translate_messages([message]) == [
        {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": "{}"}]


# -- an endpoint that cannot do it --------------------------------------------


def test_an_endpoint_that_rejects_the_field_is_asked_once_without_it():
    """A compatible gateway. The request goes again without the field — and
    without the items and item ids it would have carried back, since an
    endpoint that cannot issue encrypted reasoning cannot take it either."""
    llm = _llm()
    history = [*HELLO, {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "call_1|fc_1", "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"}}],
        OPENAI_REASONING_ITEMS: [{"id": "rs_1", "summary": [], "encrypted_content": "E"}],
    }, {"role": "tool", "tool_call_id": "call_1|fc_1", "content": "hello"}]
    rejection = (400, {"error": {
        "message": "Unknown include value: reasoning.encrypted_content",
        "type": "invalid_request_error", "param": "include"}})
    wire = attach(llm, Wire(rejection, _final(), _final(), _final()))
    llm.chat_stream(history)
    llm.chat_stream(history)
    assert ["include" in body for body in wire.requests] == [True, False, False]
    for body in wire.requests[1:]:
        assert _of(body, "reasoning") == []
        assert "id" not in _of(body, "function_call")[0]
        _assert_the_api_would_take_it(body)
    # The repaired request and the ones built after it spell the rule in two
    # places (the repair is handed a request, not the history): same output.
    assert wire.requests[1]["input"] == wire.requests[2]["input"] == translate_messages(
        history, reasoning=False)
    # Per model, like every capability latch.
    llm._adapter.reset_latches()
    llm.chat_stream(HELLO)
    assert "include" in wire.requests[3]


@pytest.mark.parametrize("error", [
    {"message": "Unknown include value: reasoning.encrypted_content", "param": "include"},
    {"message": "Unknown parameter: 'include'.", "code": "unknown_parameter"},
    {"message": "Unrecognized request argument supplied: include"},
    {"message": "Invalid request.", "param": "include"},
    {"message": "Extra inputs are not permitted",
     "detail": [{"type": "extra_forbidden", "loc": ["body", "include"]}]},
], ids=["value", "unknown-parameter", "unrecognized-argument", "param-only", "pydantic-loc"])
@pytest.mark.parametrize("entry", ["chat_stream", "chat"])
def test_every_way_an_endpoint_says_no_to_the_field_takes_the_fallback(error, entry):
    """The value refused, or the parameter itself — which never mentions the
    value. Every request carries the field, so a shape this misses is an
    endpoint that worked until this adapter started asking.

    Through both entries: ``chat_stream`` lower-cases the error text before
    the repair sees it and ``chat()`` — the summarizer's — does not, so a
    check that works on one can be dead on the other. It was."""
    llm = _llm()
    wire = attach(llm, Wire((400, {"error": {"type": "invalid_request_error", **error}}), _final()))
    getattr(llm, entry)(HELLO)
    assert ["include" in body for body in wire.requests] == [True, False]


@pytest.mark.parametrize("message", [
    "The response must include a tool result for call_1.",
    "Unknown parameter: 'included_fields'.",
    "Unsupported parameter: 'temperature' is not supported with this model.",
], ids=["plain-english", "another-parameter", "temperature"])
def test_an_error_that_merely_says_include_does_not_latch(message):
    """Latched for the client's life, so a bare word must not do it."""
    llm = _llm()
    wire = attach(llm, Wire(
        (400, {"error": {"message": message, "type": "invalid_request_error"}}), _final(), _final()))
    try:
        llm.chat_stream(HELLO)
    except Exception:
        pass
    llm.chat_stream(HELLO)
    assert "include" in wire.requests[-1]


@pytest.mark.parametrize("message", [
    "The encrypted content for item rs_1 could not be verified.",
    # Constructed, not observed: whatever the provider's wording, the *code*
    # decides — even when the text also happens to mention the field.
    "Item rs_1: encrypted_content could not be verified (include was accepted).",
], ids=["plain", "mentions-include"])
def test_one_item_that_cannot_be_decrypted_is_not_the_endpoint_refusing_the_field(message):
    """``invalid_encrypted_content`` names the same words. Read as "field
    unsupported" it would latch, and end reasoning carry-over for the session
    on an endpoint that supports it."""
    llm = _llm()
    wire = attach(llm, Wire((400, {"error": {
        "message": message, "type": "invalid_request_error",
        "code": "invalid_encrypted_content", "param": "input"}}), _final()))
    with pytest.raises(Exception, match="invalid_encrypted_content"):
        llm.chat_stream(HELLO)
    llm.chat_stream(HELLO)
    assert len(wire.requests) == 2      # not re-sent as a "repair"
    assert [("include" in body) for body in wire.requests] == [True, True]


# -- one tuple, for recording and for purging ---------------------------------


@pytest.mark.parametrize("key", WIRE_CARRIER_KEYS)
def test_a_carrier_that_is_recorded_is_also_purged(key):
    """``WIRE_CARRIER_KEYS`` is read by both. A key persisted but not purged
    goes back, after a switch, to a model that did not mint it."""
    response_message = Mock(spec=[key])
    setattr(response_message, key, [{"id": "x", "encrypted_content": "E"}])
    msg: Dict[str, Any] = {"role": "assistant", "content": "ok"}
    _attach_thinking_blocks(msg, response_message)
    assert msg[key] == [{"id": "x", "encrypted_content": "E"}]
    assert purge_thinking_artifacts([msg]) == 1 and key not in msg


def test_both_wires_carriers_are_in_the_tuple():
    assert set(WIRE_CARRIER_KEYS) == {ANTHROPIC_THINKING_BLOCKS, OPENAI_REASONING_ITEMS}


def test_a_model_switch_drops_the_reasoning_and_the_next_request_still_pairs():
    agent = _agent()
    try:
        wire = _rounds(agent, 1, _final("Still hello."))
        assert any(OPENAI_REASONING_ITEMS in m for m in agent.messages)
        agent.set_model("gpt-other")
        assert not any(OPENAI_REASONING_ITEMS in m for m in agent.messages)
        assert agent.chat("and again?") == "Still hello."
    finally:
        agent.close()
    body = wire.requests[-1]
    _assert_the_api_would_take_it(body)
    assert _of(body, "reasoning") == [] and "ENC-" not in json.dumps(body)
    assert [c["call_id"] for c in _of(body, "function_call")] == ["call_0"]


def test_the_summary_is_what_the_local_estimate_counts():
    agent = _agent()
    try:
        bare = {"role": "assistant", "content": "ok"}
        carried = {**bare, OPENAI_REASONING_ITEMS: [{
            "id": "rs_1", "encrypted_content": "E" * 4000,
            "summary": [{"type": "summary_text", "text": "weigh the options. " * 20}]}]}
        estimate = agent.context_manager.estimate_tokens
        extra = estimate([carried]) - estimate([bare])
        assert 0 < extra < 200      # the summary, and not the opaque 4,000 characters
    finally:
        agent.close()


# -- what rewrites history ----------------------------------------------------


def test_the_turn_after_a_compaction_is_a_request_the_api_would_take():
    agent = _agent()
    try:
        wire = _rounds(agent, 4, _final(SUMMARY), _tool_turn(8), _final("Still hello."))
        sent_before = len(wire.requests)
        assert agent.compact().status == "success"
        assert agent.chat("read it once more") == "Still hello."
        kept = {item["id"] for m in agent.messages[:-1]
                for item in m.get(OPENAI_REASONING_ITEMS) or []}
    finally:
        agent.close()
    assert len(wire.requests) == sent_before + 3    # the summarizer, then the turn
    for body in wire.requests[sent_before:]:
        _assert_the_api_would_take_it(body)
    last = wire.requests[-1]
    # Exactly what history still holds, whole; the summarized turns took theirs.
    assert {item["id"] for item in _of(last, "reasoning")} == kept
    assert "rs_8" in kept and "rs_0" not in kept
    assert "SUMMARY-MARKER" in json.dumps(last["input"][:3])


def _saved_session() -> str:
    agent = _agent()
    try:
        _rounds(agent, 2)
        path, session_id = persist_agent_session(agent, project_root=Path.cwd())
    finally:
        agent.close()
    # The precondition: the encrypted items really are on disk, so it is the
    # restore — not the save — that keeps them off the next request.
    assert "ENC-0" in path.read_text(encoding="utf-8")
    return session_id


def _assert_restored_and_continues(agent: Agentao, wire: Wire) -> None:
    assert not any(OPENAI_REASONING_ITEMS in m for m in agent.messages)
    assert agent.chat("what did the note say?") == "It said hello."
    (body,) = wire.requests
    _assert_the_api_would_take_it(body)
    # Minted by whatever model the saved session ran: none goes back, and with
    # it no item id — but the conversation does.
    assert _of(body, "reasoning") == [] and "ENC-" not in json.dumps(body)
    assert [c["call_id"] for c in _of(body, "function_call")] == ["call_0", "call_1"]
    assert all("id" not in c for c in _of(body, "function_call"))


def test_a_resumed_session_continues_without_the_saved_reasoning():
    session_id = _saved_session()
    agent = _agent()
    try:
        cli = Mock()
        cli.agent = agent
        sessions_cmd.resume_session(cli, session_id)
        wire = attach(agent.llm, Wire(_final("It said hello.")))
        _assert_restored_and_continues(agent, wire)
    finally:
        agent.close()


def test_an_acp_loaded_session_continues_without_the_saved_reasoning():
    session_id = _saved_session()
    agent = _agent()
    try:
        acp_session_load.handle_session_load(
            make_initialized_server(),
            {"sessionId": session_id, "cwd": str(Path.cwd()), "mcpServers": []},
            agent_factory=make_factory(agent),
        )
        wire = attach(agent.llm, Wire(_final("It said hello.")))
        _assert_restored_and_continues(agent, wire)
    finally:
        agent.close()
