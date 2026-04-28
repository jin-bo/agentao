"""Shared pytest fixtures for the agentao test suite."""

import os
import pytest


@pytest.fixture(autouse=True)
def _stub_llm_credentials(monkeypatch):
    """Set dummy LLM credentials for every test that doesn't supply its own.

    Production code resolves provider env vars only inside
    ``agentao.embedding.build_from_environment``. Tests that
    instantiate ``Agentao(working_directory=...)`` directly used to
    rely on those env reads, so we stub them here and have
    ``_agentao_env_default_credentials`` mirror the factory's
    discovery contract through ``discover_llm_kwargs()``.
    """
    monkeypatch.setenv("OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY", "test-dummy-key"))
    monkeypatch.setenv("OPENAI_BASE_URL", os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    monkeypatch.setenv("OPENAI_MODEL", os.environ.get("OPENAI_MODEL", "gpt-5.4"))


@pytest.fixture(autouse=True)
def _agentao_env_default_credentials(monkeypatch, _stub_llm_credentials):
    """Backfill explicit LLM kwargs on ``Agentao(...)`` from env.

    Mirrors what ``build_from_environment`` does, scoped per-test so
    production code under test never sees implicit env reads from
    ``Agentao.__init__`` itself.
    """
    from agentao.agent import Agentao
    from agentao.embedding.factory import discover_llm_kwargs

    _orig_init = Agentao.__init__

    def _patched_init(self, *args, **kwargs):
        if kwargs.get("llm_client") is None:
            for key, value in discover_llm_kwargs().items():
                kwargs.setdefault(key, value)
        _orig_init(self, *args, **kwargs)

    monkeypatch.setattr(Agentao, "__init__", _patched_init)
