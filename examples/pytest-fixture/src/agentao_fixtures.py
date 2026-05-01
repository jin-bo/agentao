"""Drop-in pytest fixtures: one ``Agentao`` per test, fake LLM, no API key.

Copy this file (or the three fixtures inside it) into your downstream
project's ``conftest.py``. Tests that need an isolated agent take the
``agent`` fixture; tests that need to script the LLM's reply take
``agent_with_reply``.

Design rules:

- One ``Agentao`` per test, freshly constructed against ``tmp_path``.
  Multi-test state never leaks because each test has its own
  ``working_directory``.
- The LLM is a ``MagicMock`` shaped to satisfy the attributes Agentao
  reads at construction (model, api_key, base_url, etc.) and at
  metric-update time (``total_prompt_tokens``).
- The chat-loop response is injected via ``Agentao._llm_call`` rather
  than via the LLM's HTTP client so the test never touches a network
  stack.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock, patch

import pytest

from agentao import Agentao


def make_fake_llm_client() -> MagicMock:
    """Return a MagicMock shaped like ``LLMClient``.

    Set everything ``Agentao`` reads during construction or metric
    rollups; leave everything else as auto-MagicMock so accidental
    calls in the test do not raise — they just return more MagicMocks.
    """
    fake = MagicMock(name="FakeLLMClient")
    fake.logger = MagicMock(name="FakeLLMLogger")
    fake.model = "fake-model"
    fake.api_key = "fake-key"
    fake.base_url = "http://localhost:1"
    fake.temperature = 0.0
    fake.max_tokens = 256
    fake.total_prompt_tokens = 0
    fake.total_completion_tokens = 0
    return fake


def make_chat_response(content: str = "fixture reply") -> MagicMock:
    """Build a single-shot chat-completion response with no tool calls."""
    response = MagicMock(name="FakeChatResponse")
    response.choices[0].message.tool_calls = None
    response.choices[0].message.content = content
    response.choices[0].message.reasoning_content = None
    response.choices[0].finish_reason = "stop"
    response.usage = None
    return response


@pytest.fixture
def fake_llm_client() -> MagicMock:
    """An LLMClient mock — pass to ``Agentao(llm_client=...)``."""
    return make_fake_llm_client()


@pytest.fixture
def agent(tmp_path: Path, fake_llm_client: MagicMock):
    """One-shot ``Agentao`` for a single test.

    ``Agentao._llm_call`` is patched class-wide for the test's duration,
    so any chat invocation returns the same scripted response. Use the
    ``agent_with_reply`` fixture if you need to script different
    replies per test.
    """
    response = make_chat_response("fixture reply")
    with patch(
        "agentao.agent.Agentao._llm_call",
        lambda self, msgs, tools, token: response,
    ):
        a = Agentao(
            working_directory=tmp_path,
            llm_client=fake_llm_client,
        )
        try:
            yield a
        finally:
            a.close()


@pytest.fixture
def agent_with_reply(tmp_path: Path, fake_llm_client: MagicMock):
    """Yield a factory: ``make_agent("scripted reply")`` returns an Agentao.

    Useful when one test needs the LLM to say different things on
    different turns — call the factory once with the next reply.
    """
    constructed: list[Agentao] = []

    def _make(reply: str) -> Agentao:
        response = make_chat_response(reply)
        # Class-level patch persists for the whole test; later
        # invocations of _make swap the captured response.
        captured: dict[str, MagicMock] = {"r": response}

        def _llm_call(self, messages, tools, token):
            return captured["r"]

        # Stack-track the patches; the fixture teardown undoes all.
        patcher = patch("agentao.agent.Agentao._llm_call", _llm_call)
        patcher.start()
        # Pin the response so subsequent _make() updates point _llm_call
        # at a fresh response object.
        constructed.append(patcher)  # type: ignore[arg-type]

        a = Agentao(
            working_directory=tmp_path / f"agent-{len(constructed)}",
            llm_client=fake_llm_client,
        )
        constructed.append(a)
        return a

    yield _make
    for item in reversed(constructed):
        if isinstance(item, Agentao):
            try:
                item.close()
            except Exception:
                pass
        else:
            try:
                item.stop()  # type: ignore[attr-defined]
            except Exception:
                pass


__all__ = [
    "agent",
    "agent_with_reply",
    "fake_llm_client",
    "make_chat_response",
    "make_fake_llm_client",
]
