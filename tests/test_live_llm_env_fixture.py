"""``live_llm_env`` fails up front when the shell exports no provider key."""

import os
import sys

import pytest


def _conftest():
    """The suite's ``conftest`` module, under whatever name pytest imported it."""
    return next(
        m for m in list(sys.modules.values())
        if getattr(m, "__file__", None) and m.__file__.endswith("conftest.py")
        and hasattr(m, "_SHELL_LLM_ENV")
    )


def test_fails_when_the_shell_exports_no_key(monkeypatch, request):
    monkeypatch.setattr(_conftest(), "_SHELL_LLM_ENV", {"LLM_PROVIDER": "anthropic"})
    with pytest.raises(pytest.fail.Exception, match="no ANTHROPIC_API_KEY"):
        request.getfixturevalue("live_llm_env")


def test_restores_the_shell_values_when_the_key_is_exported(monkeypatch, request):
    monkeypatch.setattr(
        _conftest(),
        "_SHELL_LLM_ENV",
        {"LLM_PROVIDER": "ANTHROPIC", "ANTHROPIC_API_KEY": "sk-shell"},
    )
    request.getfixturevalue("live_llm_env")
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-shell"
    assert os.environ["LLM_PROVIDER"] == "ANTHROPIC"
