"""Tests for model switching functionality.

This test runs offline in CI and can use the live provider locally.
Set ``AGENTAO_TEST_LIVE_MODELS=0`` to force offline mode locally.
Set ``AGENTAO_TEST_LIVE_MODELS=1`` to force live mode explicitly.
"""

from __future__ import annotations

import os

import pytest

from agentao.agent import Agentao


def _use_live_models() -> bool:
    """Return whether the test should call the configured model API."""
    env = os.getenv("AGENTAO_TEST_LIVE_MODELS")
    if env is not None:
        return env.strip().lower() in {"1", "true", "yes", "on"}
    return os.getenv("GITHUB_ACTIONS") != "true"


def _build_agent() -> Agentao:
    """Create an agent without clobbering any real local credentials."""
    if not any(
        os.getenv(name)
        for name in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
            "DEEPSEEK_API_KEY",
        )
    ):
        os.environ["OPENAI_API_KEY"] = "test-key"
    return Agentao()


def test_model_switching_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _build_agent()
    expected_models = ["claude-sonnet-4-5", "gpt-3.5-turbo", "gpt-4"]

    if not _use_live_models():
        monkeypatch.setattr(agent, "list_available_models", lambda: expected_models)

    models = agent.list_available_models()
    assert isinstance(models, list)
    assert models

    if not _use_live_models():
        assert models == expected_models

    original_model = agent.get_current_model()
    for model in expected_models:
        result = agent.set_model(model)
        current = agent.get_current_model()
        assert current
        assert current == model
        assert model in result

    summary = agent.get_conversation_summary()
    assert isinstance(summary, str)
    assert summary.strip()

    agent.set_model(original_model)
    assert agent.get_current_model() == original_model
