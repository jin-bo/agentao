"""A turn cancelled before its request goes out sends nothing.

The adapters first read the cancellation token *inside* their event loop —
after the POST went out and the first event came back. So a turn cancelled in
the gap still sent the request and paid for the whole prompt, and then hung up
on the first event. ``prepare()`` is what made the gap seconds wide: the
Models API lookup holds the thread and cannot be interrupted.

What is promised is "a cancelled task is not continued once the lookup
returns", not that the lookup itself is interrupted.
"""

from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest

from agentao import Agentao
from agentao.cancellation import CancellationToken
from agentao.llm.client import LLMClient
from tests.support.anthropic_wire import (
    Wire, attach, message_end, message_start, stream_of, text_block,
)

# ``LLMClient`` opens ``agentao.log`` in the process cwd: see ``isolated_cwd``.
pytestmark = pytest.mark.usefixtures("isolated_cwd")

HELLO = [{"role": "user", "content": "hi"}]
MODEL_INFO = {
    "id": "claude-test", "type": "model", "display_name": "Claude Test",
    "created_at": "2026-06-29T00:00:00Z",
    "max_input_tokens": 1_000_000, "max_tokens": 128_000,
}


def _ok() -> bytes:
    return stream_of(message_start(input_tokens=5), text_block(0, "ok"), message_end())


class _CancelDuringLookup(Wire):
    """Answers the Models route, and the host cancels while it does."""

    def __init__(self, token: CancellationToken, *responses, **kwargs) -> None:
        super().__init__(*responses, **kwargs)
        self._token = token

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            self._token.cancel("host stopped the task")
        return super().__call__(request)


def _anthropic_llm() -> LLMClient:
    return LLMClient(api_key="k", base_url="https://api.example.test", model="claude-test",
                     api_format="anthropic-messages")


def test_a_turn_cancelled_during_the_lookup_sends_no_generation_request():
    token = CancellationToken()
    llm = _anthropic_llm()
    wire = attach(llm, _CancelDuringLookup(token, _ok(), models={"claude-test": MODEL_INFO}))

    response = llm.chat_stream(HELLO, cancellation_token=token)

    assert wire.model_lookups == ["claude-test"]  # the lookup did run, to the end
    assert wire.requests == []                    # and nothing followed it
    # The exit a mid-stream ``break`` takes: a built response that does not
    # claim the model finished. ``runtime/turn.py`` reads the token, not this.
    assert response.finish_reason_reported is False
    assert not response.choices[0].message.content
    assert not response.choices[0].message.tool_calls
    assert llm.total_prompt_tokens == 0


def test_the_lookup_still_counts_so_the_next_turn_does_not_repeat_it():
    token = CancellationToken()
    llm = _anthropic_llm()
    wire = attach(llm, _CancelDuringLookup(token, _ok(), models={"claude-test": MODEL_INFO}))
    llm.chat_stream(HELLO, cancellation_token=token)

    llm.chat_stream(HELLO, cancellation_token=CancellationToken())

    assert wire.model_lookups == ["claude-test"]
    assert llm.model_input_limit == 1_000_000
    assert len(wire.requests) == 1


def test_a_turn_already_cancelled_does_not_start_the_lookup():
    token = CancellationToken()
    token.cancel("already stopped")
    llm = _anthropic_llm()
    wire = attach(llm, Wire(_ok(), models={"claude-test": MODEL_INFO}))

    response = llm.chat_stream(HELLO, cancellation_token=token)

    assert wire.model_lookups == [] and wire.requests == []
    assert response.finish_reason_reported is False


def test_an_uncancelled_token_changes_nothing():
    llm = _anthropic_llm()
    wire = attach(llm, Wire(_ok(), models={"claude-test": MODEL_INFO}))

    response = llm.chat_stream(HELLO, cancellation_token=CancellationToken())

    assert len(wire.requests) == 1
    assert response.choices[0].message.content == "ok"
    assert response.finish_reason_reported is True


def test_the_chat_completions_wire_sends_nothing_either():
    """The gap is not the Models API's: it is between the runner's own check
    and the adapter's first one, on every wire."""
    token = CancellationToken()
    token.cancel("already stopped")
    llm = LLMClient(api_key="k", base_url="https://api.example.test/v1", model="gpt-test")
    sent = []
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: sent.append(kwargs) or iter(()),
    )))

    response = llm.chat_stream(HELLO, cancellation_token=token)

    assert sent == []
    assert response.finish_reason_reported is False
    assert not response.choices[0].message.content


def test_a_real_turn_cancelled_during_the_lookup_ends_cancelled_with_nothing_sent():
    token = CancellationToken()
    agent = Agentao(
        api_key="test-key", base_url="https://api.example.test", model="claude-test",
        api_format="anthropic-messages", working_directory=Path.cwd(),
    )
    wire = attach(agent.llm, _CancelDuringLookup(token, _ok(), models={"claude-test": MODEL_INFO}))
    try:
        agent.chat("hi", cancellation_token=token)
        assert wire.requests == []
        assert agent.last_turn.status == "cancelled"
        assert agent.last_turn.is_answer is False
    finally:
        agent.close()
