"""The reasoning part of ``completion_tokens``, per request and summed (#550).

A thinking-mode provider bills reasoning as output and never shows it, so
``completion_tokens`` alone cannot say how much of a run was thinking. Each
wire is read through its real SDK types: the OpenAI-shaped wires state the
count, and Anthropic counts thinking inside ``output_tokens`` with none.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from openai.types import CompletionUsage
from openai.types.completion_usage import CompletionTokensDetails

from agentao.llm._usage import reasoning_token_count
from agentao.llm.client import LLMClient
from agentao.runtime.llm_call import run_llm_call
from agentao.tooling.agent_tools import _add_usage
from agentao.transport import EventType
from tests.support import anthropic_wire as anthropic
from tests.support import openai_responses_wire as responses

# The wire clients here keep the default ``agentao.log``: see ``isolated_cwd``.
pytestmark = pytest.mark.usefixtures("isolated_cwd")

HELLO = [{"role": "user", "content": "hi"}]


def _chunk(*, content=None, finish_reason=None, usage=None):
    delta = MagicMock()
    delta.content = content
    delta.reasoning_content = None
    delta.tool_calls = None
    choice = MagicMock()
    choice.delta = delta
    choice.finish_reason = finish_reason
    chunk = MagicMock()
    chunk.choices = [choice]
    chunk.model = "gpt-test"
    chunk.usage = usage
    return chunk


def _completions_client(records: list[str] | None = None) -> LLMClient:
    logger = logging.getLogger(f"test_reasoning_tokens.{id(records)}")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    if records is not None:
        class _Collect(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record.getMessage())

        logger.addHandler(_Collect())
    return LLMClient(
        api_key="k", base_url="https://api.example.com/v1", model="gpt-test",
        log_file=None, logger=logger,
    )


def _completions_usage(reasoning: int) -> CompletionUsage:
    return CompletionUsage(
        prompt_tokens=100, completion_tokens=50, total_tokens=150,
        completion_tokens_details=CompletionTokensDetails(reasoning_tokens=reasoning),
    )


def test_chat_completions_states_it_and_the_log_shows_it():
    records: list[str] = []
    llm = _completions_client(records)
    llm.client.chat.completions.create = MagicMock(return_value=[
        _chunk(content="hi"),
        _chunk(finish_reason="stop", usage=_completions_usage(30)),
    ])

    response = llm.chat_stream(messages=HELLO)

    assert reasoning_token_count(response.usage) == 30
    assert llm.total_reasoning_tokens == 30
    assert llm.usage_snapshot()["reasoning_tokens"] == 30
    assert "  Reasoning Tokens: 30" in records


def test_the_responses_wire_carries_it_over():
    llm = LLMClient(api_key="k", base_url="http://wire.test/v1", model="gpt-test",
                    api_format="openai-responses")
    responses.attach(llm, responses.Wire(responses.stream_of(
        responses.created(),
        responses.text_events(0, "ok"),
        responses.completed(
            [responses.message_item("ok")],
            usage_=responses.usage(300, 40, reasoning=12),
        ),
    )))

    response = llm.chat_stream(HELLO)

    assert response.usage.completion_tokens == 40
    assert reasoning_token_count(response.usage) == 12
    assert llm.total_reasoning_tokens == 12


def test_the_anthropic_wire_states_none():
    llm = LLMClient(api_key="k", base_url="http://wire.test", model="claude-test",
                    api_format="anthropic-messages")
    anthropic.attach(llm, anthropic.Wire(anthropic.stream_of(
        anthropic.message_start(input_tokens=90),
        anthropic.text_block(0, "ok"),
        anthropic.message_end("end_turn", output_tokens=40),
    )))

    response = llm.chat_stream(HELLO)

    assert response.usage.completion_tokens == 40
    assert reasoning_token_count(response.usage) == 0
    assert llm.total_reasoning_tokens == 0


def test_llm_call_completed_carries_the_count():
    events = []
    usage = _completions_usage(7)

    def chat_stream(**kwargs):
        choice = SimpleNamespace(
            finish_reason="stop",
            message=SimpleNamespace(content="ok", reasoning_content=None),
        )
        return SimpleNamespace(choices=[choice], usage=usage)

    agent = SimpleNamespace(
        transport=SimpleNamespace(emit=events.append),
        llm=SimpleNamespace(model="m", temperature=0.0, max_tokens=8, chat_stream=chat_stream),
        replay_manager=None,
    )
    run_llm_call(agent, messages=HELLO, tools=[])

    completed = [e for e in events if e.type == EventType.LLM_CALL_COMPLETED]
    assert completed[0].data["reasoning_tokens"] == 7


def test_a_sub_agent_roll_up_reaches_a_host_add_usage_from_before_the_keyword():
    """A host's ``llm_client=`` may have an ``add_usage`` that predates
    ``reasoning_tokens``. A run with none still reaches it."""
    calls = []

    def add_usage(prompt, completion, *, cache_read_tokens=0, cache_creation_tokens=0):
        calls.append((prompt, completion, cache_read_tokens, cache_creation_tokens))

    agent = SimpleNamespace(llm=SimpleNamespace(add_usage=add_usage))
    _add_usage(agent, 10, 2, cache_read_tokens=4, cache_creation_tokens=0, reasoning_tokens=0)
    assert calls == [(10, 2, 4, 0)]


def test_an_old_host_add_usage_keeps_the_rest_when_there_is_reasoning():
    """Passing the keyword it does not take would raise, and the parent would
    lose the prompt and completion counts too."""
    calls = []

    def add_usage(prompt, completion, *, cache_read_tokens=0, cache_creation_tokens=0):
        calls.append((prompt, completion))

    agent = SimpleNamespace(llm=SimpleNamespace(add_usage=add_usage))
    _add_usage(agent, 10, 2, cache_read_tokens=0, cache_creation_tokens=0, reasoning_tokens=300)
    assert calls == [(10, 2)]


def test_a_sub_agent_roll_up_adds_its_reasoning_to_the_parent():
    parent = _completions_client()
    agent = SimpleNamespace(llm=parent)
    _add_usage(agent, 10, 5, cache_read_tokens=0, cache_creation_tokens=0, reasoning_tokens=3)
    assert parent.total_reasoning_tokens == 3
    assert parent.total_completion_tokens == 5
