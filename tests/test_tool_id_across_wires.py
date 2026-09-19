"""A session that called tools on ``openai-responses`` moves to Chat Completions.

History keeps a Responses call as ``call_id|fc_…`` — one slot, two wire ids —
and the item id alone runs past the 40 characters OpenAI's Chat Completions
allows a ``tool_calls[*].id``. The id is in history, so without a rewrite every
request after the switch is the same 400. pi-mono handles this in
``openai-completions.ts::normalizeToolCallId``; that is where the limit and the
shared-``call_id`` case below come from. The limit itself is recorded there,
not observed here.

Real SDK on both sides of the switch; only the sockets are scripted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import httpx
import openai
import pytest

import agentao.llm.client as client_mod
from agentao import Agentao
from agentao.llm._openai_completions import (
    _TOOL_ID_MAX, _wire_tool_ids, _with_wire_tool_ids,
)
from agentao.llm._openai_responses import compose_tool_id
from tests.support.openai_responses_wire import (
    Wire, attach, completed, created, function_call_events, function_call_item,
    message_item, stream_of, text_events,
)

pytestmark = pytest.mark.usefixtures("isolated_cwd")

#: The shape api.openai.com mints: ``fc_`` + 48 hex, ``call_`` + 24.
ITEM_ID = "fc_" + "0a1b2c3d" * 6
CALL_ID = "call_" + "AbCdEfGh" * 3
COMPOSITE = compose_tool_id(CALL_ID, ITEM_ID)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(client_mod.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(client_mod, "_interruptible_sleep", lambda *_a, **_k: True)


class CompletionsWire:
    """A Chat Completions endpoint that answers every request with ``text``."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.requests: List[Dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        message = {"role": "assistant", "content": self.text}
        base = {"id": "chatcmpl-1", "created": 0, "model": body["model"]}
        if not body.get("stream"):
            return httpx.Response(200, json={
                **base, "object": "chat.completion",
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12},
            })
        chunks = [
            {**base, "object": "chat.completion.chunk",
             "choices": [{"index": 0, "delta": message, "finish_reason": None}]},
            {**base, "object": "chat.completion.chunk",
             "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=sse.encode("utf-8"))


def _call(tool_id: str) -> Dict[str, Any]:
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": tool_id, "type": "function",
         "function": {"name": "read_file", "arguments": "{}"}}]}


def _result(tool_id: str) -> Dict[str, Any]:
    return {"role": "tool", "tool_call_id": tool_id, "content": "ok"}


def _sent_ids(messages: List[Dict[str, Any]]) -> List[str]:
    out = []
    for m in messages:
        out += [c["id"] for c in m.get("tool_calls") or []]
        if m.get("role") == "tool":
            out.append(m["tool_call_id"])
    return out


# -- the switch, end to end ---------------------------------------------------


def test_a_session_with_responses_tool_calls_continues_on_chat_completions():
    args = json.dumps({"file_path": str(Path("note.txt").resolve())})
    Path("note.txt").write_text("hello from disk", encoding="utf-8")
    agent = Agentao(api_key="k", base_url="http://wire.test/v1", model="gpt-test",
                    api_format="openai-responses", working_directory=Path.cwd())
    try:
        attach(agent.llm, Wire(
            stream_of(created(),
                      function_call_events(0, CALL_ID, "read_file", args, item_id=ITEM_ID),
                      completed([function_call_item(CALL_ID, "read_file", args,
                                                    item_id=ITEM_ID)])),
            stream_of(created(), text_events(0, "It says hello."),
                      completed([message_item("It says hello.")])),
        ))
        assert agent.chat("read the note") == "It says hello."
        assert COMPOSITE in _sent_ids(agent.messages) and len(COMPOSITE) > _TOOL_ID_MAX

        agent.set_provider(api_key="k2", base_url="http://other.test/v1",
                           model="gpt-chat", api_format="openai-completions")
        wire = CompletionsWire("Still hello.")
        agent.llm.client = openai.OpenAI(
            api_key="k2", base_url="http://other.test/v1", max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(wire)))
        assert agent.chat("and again?") == "Still hello."
        history = _sent_ids(agent.messages)
    finally:
        agent.close()

    sent = _sent_ids(wire.requests[-1]["messages"])
    assert sent == [CALL_ID, CALL_ID]                 # the call, and its result
    assert all(len(i) <= _TOOL_ID_MAX for i in sent)
    # Outbound copy only: history still holds what the Responses wire needs.
    assert history == [COMPOSITE, COMPOSITE]


# -- the map ------------------------------------------------------------------


def test_a_request_with_no_composite_id_is_the_same_list():
    """Identity, not equality: this adapter's request is held byte-identical to
    the pre-extraction build, and a gateway's own long ids are its business."""
    long_but_foreign = "chatcmpl-tool-" + "f" * 32
    messages = [_call(long_but_foreign), _result(long_but_foreign),
                _call("a|b"), _result("a|b")]
    assert _with_wire_tool_ids(messages) is messages


def test_only_the_messages_that_name_a_composite_id_are_copied():
    user = {"role": "user", "content": "hi"}
    messages = [user, _call(COMPOSITE), _result(COMPOSITE)]
    before = json.dumps(messages)
    out = _with_wire_tool_ids(messages)
    assert out[0] is user and out is not messages
    assert _sent_ids(out) == [CALL_ID, CALL_ID]
    assert json.dumps(messages) == before             # history untouched


def test_calls_that_share_a_call_id_stay_distinct_and_paired():
    """pi-mono records providers whose parallel calls share one ``call_id`` and
    differ by item id; two ``tool_calls`` with one id is a 400 of its own."""
    a, b = compose_tool_id("call_same", "fc_a"), compose_tool_id("call_same", "fc_b")
    messages = [_call(a), _call(b), _result(a), _result(b)]
    sent = _sent_ids(_with_wire_tool_ids(messages))
    assert sent[0] != sent[1]
    assert sent[2:] == sent[:2]                       # each result names its call
    assert all(len(i) <= _TOOL_ID_MAX for i in sent)


def test_a_call_id_already_in_the_request_is_not_taken_twice():
    messages = [_call(CALL_ID), _result(CALL_ID), _call(COMPOSITE), _result(COMPOSITE)]
    sent = _sent_ids(_with_wire_tool_ids(messages))
    assert sent[0] == sent[1] == CALL_ID
    assert sent[2] == sent[3] != CALL_ID


def test_a_call_id_that_is_itself_too_long_is_shortened():
    raw = compose_tool_id("call_" + "x" * 60, "fc_1")
    (wire_id,) = set(_sent_ids(_with_wire_tool_ids([_call(raw), _result(raw)])))
    assert len(wire_id) <= _TOOL_ID_MAX


def test_the_spelling_does_not_depend_on_what_else_is_in_the_request():
    """A hash, not a counter: the order the ids appear in changes nothing. (A
    call whose sibling was compacted away does revert to the bare ``call_id``
    — self-consistent per request, pinned below so it stays a known cost.)"""
    a, b = compose_tool_id("call_same", "fc_a"), compose_tool_id("call_same", "fc_b")
    both = _wire_tool_ids([_call(a), _result(a), _call(b), _result(b)])
    reordered = _wire_tool_ids([_call(b), _result(b), _call(a), _result(a)])
    assert both == reordered
    alone = _wire_tool_ids([_call(a), _result(a)])
    assert alone[a] == "call_same" != both[a]


def test_an_id_that_is_not_a_string_is_left_alone():
    messages = [{"role": "assistant", "tool_calls": [{"id": ["x"]}, "junk"]},
                {"role": "tool", "tool_call_id": None, "content": ""},
                _call(COMPOSITE), _result(COMPOSITE)]
    out = _with_wire_tool_ids(messages)               # must not raise
    assert out[0] is messages[0] and out[1] is messages[1]


# -- /provider, there and back ------------------------------------------------


def test_provider_switches_to_chat_completions_and_back_to_responses(monkeypatch):
    """Read off both sockets. Going out, the id is the ``call_id``; coming
    back, history still holds the composite, so the Responses request names the
    same ``call_id`` — and no ``fc_`` id, its reasoning having gone with the
    switch."""
    from types import SimpleNamespace

    from agentao.cli.commands import provider as provider_cmd

    printed: List[str] = []
    monkeypatch.setattr(provider_cmd.console, "print",
                        lambda *a, **k: printed.append(str(a[0]) if a else ""))
    for name, value in {
        "CHAT_API_KEY": "k2", "CHAT_BASE_URL": "http://other.test/v1", "CHAT_MODEL": "gpt-chat",
        "RESP_API_KEY": "k3", "RESP_BASE_URL": "http://wire.test/v1", "RESP_MODEL": "gpt-test",
        "RESP_API_FORMAT": " OpenAI-Responses ",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("CHAT_API_FORMAT", raising=False)

    args = json.dumps({"file_path": str(Path("note.txt").resolve())})
    Path("note.txt").write_text("hello from disk", encoding="utf-8")
    agent = Agentao(api_key="k", base_url="http://wire.test/v1", model="gpt-test",
                    api_format="openai-responses", working_directory=Path.cwd())
    cli = SimpleNamespace(agent=agent, current_provider="RESP")
    try:
        attach(agent.llm, Wire(
            stream_of(created(),
                      function_call_events(0, CALL_ID, "read_file", args, item_id=ITEM_ID),
                      completed([function_call_item(CALL_ID, "read_file", args,
                                                    item_id=ITEM_ID)])),
            stream_of(created(), text_events(0, "It says hello."),
                      completed([message_item("It says hello.")])),
        ))
        agent.chat("read the note")

        provider_cmd.handle_provider_command(cli, "CHAT")
        assert agent.llm.api_format == "openai-completions"
        assert any("openai-responses → " in line for line in printed)
        chat_wire = CompletionsWire("over completions")
        agent.llm.client = openai.OpenAI(
            api_key="k2", base_url="http://other.test/v1", max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(chat_wire)))
        assert agent.chat("and again?") == "over completions"

        provider_cmd.handle_provider_command(cli, "RESP")
        assert cli.current_provider == "RESP"
        assert agent.llm.api_format == "openai-responses"
        assert agent._llm_config["api_format"] == "openai-responses"
        back = attach(agent.llm, Wire(stream_of(
            created(), text_events(0, "back"), completed([message_item("back")]))))
        assert agent.chat("once more") == "back"
    finally:
        agent.close()

    assert set(_sent_ids(chat_wire.requests[-1]["messages"])) == {CALL_ID}
    assert back.urls[0].endswith("/responses")
    body = back.requests[0]
    (call,) = [i for i in body["input"] if i.get("type") == "function_call"]
    (output,) = [i for i in body["input"] if i.get("type") == "function_call_output"]
    assert call["call_id"] == output["call_id"] == CALL_ID and "id" not in call
    assert "over completions" in json.dumps(body["input"])


def test_the_default_wire_does_not_import_the_responses_adapter():
    """The id helpers live in ``_tool_ids`` so the adapter whose request is
    held byte-identical is not downstream of another wire's module."""
    import subprocess
    import sys

    code = ("import sys, agentao.llm._openai_completions; "
            "sys.exit('agentao.llm._openai_responses' in sys.modules)")
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0
