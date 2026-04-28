"""Issue #10 — ``build_from_environment()`` is the single env / disk read site.

The factory wraps the implicit reads (``.env``, ``LLM_PROVIDER``,
``working_directory or Path.cwd()``, ``.agentao/permissions.json``,
``.agentao/mcp.json``, memory roots) into one explicit call so the
agent constructor itself never has to.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from agentao.embedding import build_from_environment


def test_factory_freezes_working_directory(tmp_path, monkeypatch):
    """A path passed in is the path the agent reports — no Path.cwd() leak."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")

    with patch("agentao.agent.LLMClient") as mock_llm_cls:
        mock_llm = Mock()
        mock_llm.logger = Mock()
        mock_llm.model = "gpt-test"
        mock_llm.api_key = "test-key"
        mock_llm.base_url = "https://api.example.com/v1"
        mock_llm.temperature = 0.2
        mock_llm_cls.return_value = mock_llm

        agent = build_from_environment(working_directory=tmp_path)

    assert agent.working_directory == tmp_path.resolve()


def test_factory_routes_provider_env_to_constructor(tmp_path, monkeypatch):
    """LLM_PROVIDER + provider-prefixed env vars are picked up here, not by Agentao."""
    monkeypatch.setenv("LLM_PROVIDER", "DEEPSEEK")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deep-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")

    captured = {}

    with patch("agentao.agent.LLMClient") as mock_llm_cls:
        mock_llm = Mock()
        mock_llm.logger = Mock()
        mock_llm.model = "deepseek-chat"
        mock_llm.api_key = "deep-key"
        mock_llm.base_url = "https://api.deepseek.com/v1"
        mock_llm.temperature = 0.2
        mock_llm_cls.return_value = mock_llm

        def _capture(*args, **kwargs):
            captured.update(kwargs)
            return mock_llm

        mock_llm_cls.side_effect = _capture

        build_from_environment(working_directory=tmp_path)

    assert captured.get("api_key") == "deep-key"
    assert captured.get("model") == "deepseek-chat"


def test_factory_overrides_win_over_discovered(tmp_path, monkeypatch):
    """Caller-supplied kwargs take priority over factory-discovered values."""
    monkeypatch.setenv("OPENAI_API_KEY", "discovered")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "discovered-model")

    captured = {}

    with patch("agentao.agent.LLMClient") as mock_llm_cls:
        mock_llm = Mock()
        mock_llm.logger = Mock()
        mock_llm.model = "explicit-model"
        mock_llm.api_key = "discovered"
        mock_llm.base_url = "https://api.example.com/v1"
        mock_llm.temperature = 0.2
        mock_llm_cls.return_value = mock_llm

        def _capture(*args, **kwargs):
            captured.update(kwargs)
            return mock_llm

        mock_llm_cls.side_effect = _capture

        build_from_environment(working_directory=tmp_path, model="explicit-model")

    assert captured.get("model") == "explicit-model"
