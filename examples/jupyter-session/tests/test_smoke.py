"""Offline smoke for the Jupyter kernel-session example.

Same helpers the notebook (`session.ipynb`) imports, exercised against
a fake LLM so CI runs without an API key.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.kernel_session import build_session, close_session, turn


def _fake_llm():
    fake = MagicMock(name="FakeLLM")
    fake.logger = MagicMock()
    fake.model = "fake-model"
    fake.api_key = "fake-key"
    fake.base_url = "http://localhost:1"
    fake.temperature = 0.0
    fake.max_tokens = 256
    fake.total_prompt_tokens = 0
    fake.total_completion_tokens = 0
    return fake


def _fake_response():
    response = MagicMock()
    response.choices[0].message.tool_calls = None
    response.choices[0].message.content = "notebook reply"
    response.choices[0].message.reasoning_content = None
    return response


def test_build_and_turn_offline(tmp_path: Path) -> None:
    """Construct + one turn against a fake LLM."""
    session = build_session(llm_client=_fake_llm(), working_directory=tmp_path)
    try:
        with patch(
            "agentao.agent.Agentao._llm_call",
            lambda self, msgs, tools, token: _fake_response(),
        ):
            reply, _events = asyncio.run(turn(session, "hello"))
        assert reply == "notebook reply"
        # Message log shape: at least the user + assistant entries.
        roles = [m["role"] for m in session.agent.messages]
        assert "user" in roles and "assistant" in roles
    finally:
        close_session(session)


def test_close_is_idempotent(tmp_path: Path) -> None:
    session = build_session(llm_client=_fake_llm(), working_directory=tmp_path)
    close_session(session)
    close_session(session)  # second call must not raise
