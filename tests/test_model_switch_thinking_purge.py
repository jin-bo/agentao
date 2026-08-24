"""A model switch must not replay the previous model's thinking artifacts.

``reasoning_content`` and Gemini's ``thought_signature`` are minted by one
specific model and are only meaningful to it. agentao stores both in history
(the latter deliberately, so Gemini thinking models keep working across turns)
and sends history back verbatim — the OpenAI SDK does not strip unknown
message keys. ``/model`` and ``/provider`` therefore have to clear them the
same way they already clear the tiktoken encoding, the cached token anchor and
the capability latches.

The end-to-end test builds its input from the **real producer** (an
``openai`` client parsing a Gemini-shaped response) and asserts against the
**real wire body** (captured through ``httpx.MockTransport``) rather than
against a hand-authored dict. A fixture built by hand here would only restate
what the code under test already believes: it would not have caught that
``model_dump()`` preserves ``thought_signature``, nor that the SDK forwards
both fields untouched.
"""

import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from agentao.llm.client import KEEP_BASE_URL
from agentao.runtime.model import purge_thinking_artifacts, set_model, set_provider


# ---------------------------------------------------------------------------
# Unit: the purge itself
# ---------------------------------------------------------------------------

def _history():
    """History carrying a ``thought_signature`` at BOTH levels.

    ``_serialize_tool_call`` goes through ``model_dump()`` specifically so the
    field survives "regardless of which level they appear at", and the real
    Gemini-shaped response puts it on the tool_call entry, not inside
    ``function`` — so a purge that only walks ``function`` misses the case the
    feature exists for. The end-to-end test below builds this shape from the
    real SDK rather than by hand.
    """
    return [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "old model's private reasoning",
            "tool_calls": [{
                "id": "call_1", "type": "function",
                "thought_signature": "TOP_LEVEL_SIG",
                "function": {
                    "name": "read_file",
                    "arguments": '{"p":"a"}',
                    "thought_signature": "SIG_MINTED_BY_GEMINI",
                },
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "file body"},
    ]


def test_purge_removes_every_artifact_kind_at_both_levels():
    msgs = _history()
    assert purge_thinking_artifacts(msgs) == 3
    assert "reasoning_content" not in msgs[1]
    assert "thought_signature" not in msgs[1]["tool_calls"][0]
    assert "thought_signature" not in msgs[1]["tool_calls"][0]["function"]


def test_purge_preserves_everything_else():
    """The conversation itself must survive — only the artifacts go."""
    msgs = _history()
    purge_thinking_artifacts(msgs)
    assert msgs[0] == {"role": "user", "content": "hi"}
    assert msgs[2] == {"role": "tool", "tool_call_id": "call_1", "content": "file body"}
    fn = msgs[1]["tool_calls"][0]["function"]
    assert fn["name"] == "read_file" and fn["arguments"] == '{"p":"a"}'
    assert msgs[1]["tool_calls"][0]["id"] == "call_1"


def test_purge_is_idempotent_and_reports_zero_on_clean_history():
    msgs = _history()
    purge_thinking_artifacts(msgs)
    assert purge_thinking_artifacts(msgs) == 0


def test_purge_tolerates_malformed_history():
    msgs = [None, "junk", {"role": "assistant", "tool_calls": "not-a-list"},
            {"role": "assistant", "tool_calls": [None, {"function": None}]}]
    assert purge_thinking_artifacts(msgs) == 0


# ---------------------------------------------------------------------------
# Wiring: set_model / set_provider
# ---------------------------------------------------------------------------

class _FakeAgent:
    def __init__(self, model="gemini-3-pro", base_url="https://a.example"):
        self.messages = _history()
        self.llm = SimpleNamespace(
            model=model, base_url=base_url, api_key="k",
            logger=SimpleNamespace(info=lambda *a, **k: None,
                                   warning=lambda *a, **k: None),
            reset_capability_latches=lambda: None,
            reconfigure=self._reconfigure,
        )
        self.context_manager = SimpleNamespace(
            _encoding=None,
            invalidate_token_anchor=lambda: None,
            # Part of the same clear-on-switch family; stubbed rather than
            # spied on because asserting it here would be a spy nobody reads.
            # It is covered against the real ContextManager in
            # tests/test_context_window_validation.py.
            clear_observed_limit=lambda *_a, **_k: None,
        )
        self.transport = SimpleNamespace(emit=lambda _e: None)

    # Mirrors LLMClient.reconfigure's real contract: the KEEP_BASE_URL
    # sentinel means "keep the current endpoint", and an explicit None
    # clears it. Using a local sentinel here instead would make the
    # credential-rotation case look like an endpoint change.
    def _reconfigure(self, api_key=None, base_url=KEEP_BASE_URL, model=None):
        if base_url is not KEEP_BASE_URL:
            self.llm.base_url = base_url
        if model is not None:
            self.llm.model = model


def _has_artifacts(agent):
    m = agent.messages[1]
    tc = m["tool_calls"][0]
    return ("reasoning_content" in m
            or "thought_signature" in tc
            or "thought_signature" in tc["function"])


def test_set_model_purges_on_a_real_switch():
    agent = _FakeAgent()
    set_model(agent, "gpt-5.5")
    assert not _has_artifacts(agent)


def test_set_model_leaves_history_alone_when_the_model_is_unchanged():
    """Re-selecting the active model is a no-op; don't destroy live context."""
    agent = _FakeAgent(model="gemini-3-pro")
    set_model(agent, "gemini-3-pro")
    assert _has_artifacts(agent)


def test_set_provider_purges_on_a_model_change():
    agent = _FakeAgent()
    set_provider(agent, api_key="k2", model="gpt-5.5")
    assert not _has_artifacts(agent)


def test_set_provider_purges_when_only_the_endpoint_changes():
    """Same model name behind a different backend is a different signer."""
    agent = _FakeAgent()
    set_provider(agent, api_key="k2", base_url="https://b.example")
    assert not _has_artifacts(agent)


def test_set_provider_leaves_history_alone_on_a_credential_rotation():
    """Same model, same endpoint — nothing became stale."""
    agent = _FakeAgent()
    set_provider(agent, api_key="rotated-key")
    assert _has_artifacts(agent)


# ---------------------------------------------------------------------------
# End-to-end: real producer -> history -> switch -> real wire body
# ---------------------------------------------------------------------------

@pytest.fixture
def wire():
    """An openai client whose request bodies are captured, not sent."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0,
            "model": "gemini-3-pro",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None,
                "reasoning_content": "I should read the file.",
                "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "thought_signature": "TOP_LEVEL_SIG",
                    "function": {
                        "name": "read_file", "arguments": '{"p":"a"}',
                        "thought_signature": "SIG_MINTED_BY_GEMINI"}}]}}]})

    client = openai.OpenAI(
        api_key="k",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return client, captured


def test_artifacts_reach_the_wire_without_a_purge(wire):
    """Pins the premise. If this ever fails, the purge is no longer needed
    and the ceremony in set_model should be reconsidered rather than kept."""
    from agentao.runtime.chat_loop._serialize import (
        _attach_reasoning, _serialize_tool_call,
    )

    client, captured = wire
    resp = client.chat.completions.create(
        model="gemini-3-pro", messages=[{"role": "user", "content": "hi"}])
    msg = resp.choices[0].message

    history = [{
        "role": "assistant", "content": None,
        "tool_calls": [_serialize_tool_call(msg.tool_calls[0])],
    }]
    _attach_reasoning(history[0], msg.reasoning_content)

    client.chat.completions.create(model="gpt-5.5", messages=history)
    sent = captured["body"]["messages"][0]

    assert "reasoning_content" in sent
    # Both levels reach the wire — the tool_call entry is the level the real
    # Gemini shape uses, and the level a function-only purge would miss.
    assert "thought_signature" in sent["tool_calls"][0]
    assert "thought_signature" in sent["tool_calls"][0]["function"]


def test_purge_keeps_them_off_the_wire_after_a_switch(wire):
    from agentao.runtime.chat_loop._serialize import (
        _attach_reasoning, _serialize_tool_call,
    )

    client, captured = wire
    resp = client.chat.completions.create(
        model="gemini-3-pro", messages=[{"role": "user", "content": "hi"}])
    msg = resp.choices[0].message

    agent = _FakeAgent()
    agent.messages = [{
        "role": "assistant", "content": None,
        "tool_calls": [_serialize_tool_call(msg.tool_calls[0])],
    }]
    _attach_reasoning(agent.messages[0], msg.reasoning_content)

    set_model(agent, "gpt-5.5")

    client.chat.completions.create(model="gpt-5.5", messages=agent.messages)
    sent = captured["body"]["messages"][0]

    assert "reasoning_content" not in sent
    assert "thought_signature" not in sent["tool_calls"][0]
    assert "thought_signature" not in sent["tool_calls"][0]["function"]
    # The tool call itself must still be intact and answerable.
    assert sent["tool_calls"][0]["id"] == "call_1"
    assert sent["tool_calls"][0]["function"]["name"] == "read_file"


def test_endpoint_change_trigger_against_a_real_llm_client():
    """Drive `set_provider` through the real `LLMClient.reconfigure`.

    The `_FakeAgent` above hand-mirrors `reconfigure`'s KEEP_BASE_URL
    semantics, and the purge trigger keys off exactly that behaviour — so
    testing the trigger only against the fake would pin it against a
    restatement of what the author believes `reconfigure` does. (An earlier
    draft of that fake used its own sentinel and made a credential rotation
    look like an endpoint change.) This drives the real object instead.
    """
    from agentao.llm.client import LLMClient

    agent = _FakeAgent()
    agent.llm = LLMClient.__new__(LLMClient)
    agent.llm.api_key = "k"
    agent.llm.base_url = "https://a.example"
    agent.llm.model = "gemini-3-pro"
    agent.llm.temperature = 0.7
    agent.llm.extra_body = {}
    agent.llm.logger = SimpleNamespace(info=lambda *a, **k: None,
                                       warning=lambda *a, **k: None)

    # Credential rotation: real reconfigure keeps the endpoint -> no purge.
    set_provider(agent, api_key="rotated")
    assert agent.llm.base_url == "https://a.example"
    assert _has_artifacts(agent)

    # Endpoint change: real reconfigure swaps base_url -> purge.
    set_provider(agent, api_key="rotated", base_url="https://b.example")
    assert agent.llm.base_url == "https://b.example"
    assert not _has_artifacts(agent)
