"""Shared pytest fixtures for the agentao test suite."""

import os
import pytest


@pytest.fixture(autouse=True)
def _stub_llm_credentials(monkeypatch):
    """Set dummy LLM credentials for every test that doesn't supply its own.

    LLMClient now requires both {PROVIDER}_API_KEY and {PROVIDER}_MODEL to be
    present at construction time (fail-fast). Tests that construct Agentao()
    without real credentials would fail without these defaults.
    """
    monkeypatch.setenv("OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY", "test-dummy-key"))
    monkeypatch.setenv("OPENAI_BASE_URL", os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    monkeypatch.setenv("OPENAI_MODEL", os.environ.get("OPENAI_MODEL", "gpt-5.4"))
