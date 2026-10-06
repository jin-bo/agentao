"""A replayed thinking block rejected as bound to another prefix is dropped.

Claude Fable 5.1, Opus 5.5 and Sonnet 5.5 bind each thinking block to the
``system`` text and ``tools`` array it was produced under, and agentao changes
both mid-session (a skill install, an ``AGENTAO.md`` edit, ``/goal`` adding
``update_goal``). Where Anthropic enforces the binding (accounts created on or
after 2026-08-31), the next request is a 400 and the turn ended.

The 400 body and the accepted retry are what ``claude-sonnet-5-5`` returned on
2026-10-05; ``block_binding`` without the beta header was refused the same
day. Everything goes through the real ``anthropic`` SDK; only the socket is
scripted.
"""

from __future__ import annotations

import logging

import pytest

from agentao.llm.client import LLMClient
from tests.support.anthropic_wire import (
    Wire, attach, message_end, message_start, stream_of, text_block,
)

# ``LLMClient`` opens ``agentao.log`` in the process cwd: see ``isolated_cwd``.
pytestmark = pytest.mark.usefixtures("isolated_cwd")

HELLO = [{"role": "user", "content": "hi"}]
BETA = "thinking-binding-controls-2026-08-01"
DROP = {"prefix_mismatch_behavior": "drop_block"}

# Verbatim from the live response, request id removed.
STALE = (400, {
    "type": "error",
    "error": {
        "type": "invalid_request_error",
        "message": (
            "messages.1.content.0: Invalid `signature` in `thinking` block. The block is "
            "bound to a different conversation. Remove the block, or set "
            "`thinking.block_binding.prefix_mismatch_behavior` to \"drop_block\". The "
            "`system` prompt differs from the one this block was created with."
        ),
    },
})


def _ok(text: str = "ok") -> bytes:
    return stream_of(message_start(input_tokens=10), text_block(0, text), message_end("end_turn"))


def _llm(**kwargs) -> LLMClient:
    return LLMClient(api_key="k", base_url="https://api.example.test", model="claude-test",
                     api_format="anthropic-messages",
                     logger=logging.getLogger("test.thinking_binding"), **kwargs)


@pytest.mark.parametrize("call", ["chat", "chat_stream"])
def test_a_stale_block_is_retried_with_drop_block(call):
    llm = _llm()
    wire = attach(llm, Wire(STALE, _ok()))
    response = getattr(llm, call)(HELLO)
    assert response.choices[0].message.content == "ok"
    assert len(wire.requests) == 2
    assert "thinking" not in wire.requests[0]
    assert BETA not in wire.headers[0].get("anthropic-beta", "")
    assert wire.requests[1]["thinking"] == {"type": "adaptive", "block_binding": DROP}
    assert BETA in wire.headers[1]["anthropic-beta"]


def test_later_requests_keep_asking_for_the_drop():
    llm = _llm()
    wire = attach(llm, Wire(STALE, _ok(), _ok()))
    llm.chat_stream(HELLO)
    llm.chat_stream(HELLO)
    assert wire.requests[2]["thinking"] == {"type": "adaptive", "block_binding": DROP}
    assert BETA in wire.headers[2]["anthropic-beta"]


def test_a_model_switch_clears_the_latch():
    llm = _llm()
    wire = attach(llm, Wire(STALE, _ok(), _ok()))
    llm.chat_stream(HELLO)
    llm.reset_capability_latches()
    llm.chat_stream(HELLO)
    assert "thinking" not in wire.requests[2]


def test_the_retry_happens_once():
    llm = _llm()
    wire = attach(llm, Wire(STALE, STALE))
    with pytest.raises(Exception, match="bound to a different conversation"):
        llm.chat_stream(HELLO)
    assert len(wire.requests) == 2


def test_the_hosts_thinking_settings_are_kept_and_not_mutated():
    extra = {"thinking": {"type": "adaptive", "display": "summarized"},
             "output_config": {"effort": "low"}}
    llm = _llm(extra_body=extra)
    wire = attach(llm, Wire(STALE, _ok()))
    llm.chat_stream(HELLO)
    assert wire.requests[1]["thinking"] == {
        "type": "adaptive", "display": "summarized", "block_binding": DROP,
    }
    assert wire.requests[1]["output_config"] == {"effort": "low"}
    assert extra == {"thinking": {"type": "adaptive", "display": "summarized"},
                     "output_config": {"effort": "low"}}


@pytest.mark.parametrize("thinking", [
    {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "error"}},
    {"type": "enabled", "budget_tokens": 2048},
])
def test_a_host_binding_or_non_adaptive_thinking_is_not_overridden(thinking):
    llm = _llm(extra_body={"thinking": thinking})
    wire = attach(llm, Wire(STALE))
    with pytest.raises(Exception, match="bound to a different conversation"):
        llm.chat_stream(HELLO)
    assert len(wire.requests) == 1


def test_an_unrelated_400_is_not_retried():
    other = (400, {"type": "error", "error": {
        "type": "invalid_request_error", "message": "messages: at least one message is required",
    }})
    llm = _llm()
    wire = attach(llm, Wire(other))
    with pytest.raises(Exception):
        llm.chat_stream(HELLO)
    assert len(wire.requests) == 1
