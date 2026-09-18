"""The session totals say how much of the input was cached.

``total_prompt_tokens`` is the whole prompt on every wire, which is what the
compaction anchor needs and exactly what makes it useless for cost: a provider
bills cache reads and cache writes at different rates from the rest of the
input. The counts were already on each Anthropic response (``_Usage``) and on
each OpenAI one (``prompt_tokens_details.cached_tokens``); nothing summed them.

agentao reports the four quantities and stops there — no prices.

The responses here are parsed by the real SDKs: the usage objects are
``anthropic``'s and ``openai``'s own, not stand-ins that answer any attribute.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import openai
import pytest
from openai.types import CompletionUsage

from agentao import Agentao
from agentao.cli.commands import context as context_cmd
from agentao.cli.replay_render._summary import _summarize_replay_event
from agentao.llm._usage import cache_token_counts
from agentao.llm.client import LLMClient
from agentao.transport import EventType
from tests.support.anthropic_wire import (
    Wire, attach, message_end, message_start, stream_of, text_block,
)

# ``LLMClient`` opens ``agentao.log`` in the process cwd: see ``isolated_cwd``.
pytestmark = pytest.mark.usefixtures("isolated_cwd")

HELLO = [{"role": "user", "content": "hi"}]


def _anthropic_answer(**usage: int) -> bytes:
    return stream_of(message_start(**usage), text_block(0, "ok"),
                     message_end("end_turn", output_tokens=40))


def _completions_client(usage: dict) -> openai.OpenAI:
    """A real ``openai`` client over a scripted socket: one streamed answer
    and the usage-only chunk ``stream_options.include_usage`` asks for."""
    def chunk(**body):
        base = {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": "gpt-x"}
        return f"data: {json.dumps({**base, **body})}\n\n"

    sse = chunk(choices=[{"index": 0, "delta": {"role": "assistant", "content": "ok"},
                          "finish_reason": None}])
    sse += chunk(choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}])
    sse += chunk(choices=[], usage=usage) + "data: [DONE]\n\n"
    return openai.OpenAI(
        api_key="k", base_url="http://wire.test/v1", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=sse.encode(),
        ))),
    )


def _totals(llm):
    return (llm.total_prompt_tokens, llm.total_completion_tokens,
            llm.total_cache_read_tokens, llm.total_cache_creation_tokens)


# -- the client's totals ------------------------------------------------------


def test_the_anthropic_wire_sums_cache_reads_and_writes_as_parts_of_the_prompt():
    llm = LLMClient(api_key="k", base_url="https://api.example.test", model="claude-test",
                    api_format="anthropic-messages")
    attach(llm, Wire(
        _anthropic_answer(input_tokens=900, cache_read_input_tokens=4000,
                          cache_creation_input_tokens=200),
        _anthropic_answer(input_tokens=100, cache_read_input_tokens=5000),
    ))
    llm.chat_stream(HELLO)
    llm.chat_stream(HELLO)
    # prompt = input + read + creation, per request; the cache counts sit inside it.
    assert _totals(llm) == (5100 + 5100, 80, 9000, 200)


def test_the_completions_wire_reads_cached_tokens_from_the_details():
    llm = LLMClient(api_key="k", base_url="http://wire.test/v1", model="gpt-x")
    llm.client = _completions_client({
        "prompt_tokens": 1200, "completion_tokens": 30, "total_tokens": 1230,
        "prompt_tokens_details": {"cached_tokens": 1024},
    })
    llm.chat_stream(HELLO)
    assert _totals(llm) == (1200, 30, 1024, 0)  # this wire has no cache-write count


def test_a_response_that_states_no_cache_counts_adds_none():
    llm = LLMClient(api_key="k", base_url="http://wire.test/v1", model="gpt-x")
    llm.client = _completions_client(
        {"prompt_tokens": 1200, "completion_tokens": 30, "total_tokens": 1230})
    llm.chat_stream(HELLO)
    assert _totals(llm) == (1200, 30, 0, 0)


def test_reset_usage_zeroes_all_four():
    llm = LLMClient(api_key="k", base_url="http://wire.test/v1", model="gpt-x")
    llm.add_usage(10, 5, cache_read_tokens=8, cache_creation_tokens=2)
    assert _totals(llm) == (10, 5, 8, 2)
    llm.reset_usage()
    assert _totals(llm) == (0, 0, 0, 0)


# -- reading one usage object -------------------------------------------------


def test_the_two_names_for_a_cache_read_are_never_summed():
    """A gateway in front of Anthropic may state both. They are one quantity."""
    usage = SimpleNamespace(
        cache_read_input_tokens=4000, cache_creation_input_tokens=200,
        prompt_tokens_details=SimpleNamespace(cached_tokens=4000),
    )
    assert cache_token_counts(usage) == (4000, 200)


def test_a_real_sdk_usage_without_details_reads_as_zero():
    bare = CompletionUsage(prompt_tokens=10, completion_tokens=1, total_tokens=11)
    assert bare.prompt_tokens_details is None
    assert cache_token_counts(bare) == (0, 0)


@pytest.mark.parametrize("usage", [None, object(), MagicMock(),
                                   SimpleNamespace(cache_read_input_tokens=True),
                                   SimpleNamespace(cache_read_input_tokens=-3),
                                   SimpleNamespace(prompt_tokens_details="1024")])
def test_anything_that_is_not_a_positive_int_reads_as_zero(usage):
    """``MagicMock`` answers every attribute, and a ``bool`` is an ``int``."""
    assert cache_token_counts(usage) == (0, 0)


# -- where the counts surface -------------------------------------------------


def _agent(**kwargs) -> Agentao:
    return Agentao(api_key="test-key", base_url="https://api.example.test", model="claude-test",
                   api_format="anthropic-messages", working_directory=Path.cwd(), **kwargs)


def test_the_per_call_event_and_the_status_line_carry_them():
    agent = _agent()
    attach(agent.llm, Wire(_anthropic_answer(
        input_tokens=900, cache_read_input_tokens=4000, cache_creation_input_tokens=200)))
    seen = []
    real_emit = agent.transport.emit

    def emit(event):
        if event.type == EventType.LLM_CALL_COMPLETED:
            seen.append(event.data)
        return real_emit(event)

    agent.transport.emit = emit
    try:
        before = agent.get_conversation_summary()
        agent.chat("hi")
        after = agent.get_conversation_summary()
        (payload,) = seen
        assert payload["prompt_tokens"] == 5100
        assert (payload["cache_read_tokens"], payload["cache_creation_tokens"]) == (4000, 200)
        assert "cached" not in before  # nothing to say before anything was cached
        assert "Session: 5,100 prompt (4,000 cached, 200 cache-write) / 40 completion" in after

        agent.clear_history()
        assert _totals(agent.llm) == (0, 0, 0, 0)
    finally:
        agent.close()


def test_the_replay_summary_shows_them_only_when_stated():
    def summary(**cache):
        return _summarize_replay_event({"kind": "llm_call_completed", "payload": {
            "status": "ok", "prompt_tokens": 5100, "completion_tokens": 40, **cache}})

    with_cache = summary(cache_read_tokens=4000, cache_creation_tokens=200)
    # A zero, a ``None``, and a file recorded before the keys existed.
    without = summary(cache_read_tokens=0, cache_creation_tokens=None) + summary()
    assert "cached=4000" in with_cache and "cache-write=200" in with_cache
    assert "cached" not in without and "cache-write" not in without


# -- /context says which quantity its total is --------------------------------


def _context_total_line(monkeypatch, agent) -> str:
    lines = []
    monkeypatch.setattr(context_cmd.console, "print",
                        lambda *a, **k: lines.append(str(a[0]) if a else ""))
    context_cmd.handle_context_command(SimpleNamespace(agent=agent), "")
    (line,) = [text for text in lines if "Estimated tokens:" in text]
    return line


def test_context_labels_a_local_estimate_and_then_the_last_requests_count(monkeypatch):
    agent = _agent()
    attach(agent.llm, Wire(_anthropic_answer(input_tokens=900)))
    try:
        assert "(local estimate)" in _context_total_line(monkeypatch, agent)
        agent.chat("hi")
        line = _context_total_line(monkeypatch, agent)
        assert "900" in line and "(last request, as the API counted it)" in line
    finally:
        agent.close()
