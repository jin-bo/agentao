"""Tests for model switching functionality.

This test runs offline by default: the model list is stubbed. Set
``AGENTAO_TEST_LIVE_MODELS=1`` to fetch it from the configured provider instead.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentao.agent import Agentao

# Agentao here writes to the process cwd: see ``isolated_cwd`` in conftest.py.
pytestmark = pytest.mark.usefixtures("isolated_cwd")


def _use_live_models() -> bool:
    """Return whether the test should call the configured model API.

    Opt-in only. Guessing from the key (a placeholder skips, anything else goes
    live) sent a real request whenever a dummy key did not look like one, such
    as ``sk-dummy``, and failed with HTTP 401, or offline with no network (#463).
    """
    return os.getenv("AGENTAO_TEST_LIVE_MODELS", "").strip().lower() in {"1", "true", "yes", "on"}


def _build_agent() -> Agentao:
    return Agentao(working_directory=Path.cwd())


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


def test_llm_config_exposes_omit_temperature() -> None:
    # Sub-agents inherit temperature omission via _llm_config; /temperature off
    # on the parent must be visible to sub-agents launched afterwards.
    agent = _build_agent()
    assert agent._llm_config["omit_temperature"] is False

    agent.llm.omit_temperature = True
    assert agent._llm_config["omit_temperature"] is True


def test_set_model_resets_omit_temperature() -> None:
    # A temperature quirk latched for one model must not stick to the next.
    agent = _build_agent()
    agent.llm.omit_temperature = True
    agent.set_model(agent.get_current_model())
    assert agent.llm.omit_temperature is False
