"""Shared pytest fixtures for the agentao test suite."""

import os
from pathlib import Path
from types import SimpleNamespace
from typing import List

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


@pytest.fixture
def search_tool(tmp_path: Path):
    """SearchTextTool wired to ``tmp_path`` as its working directory."""
    from agentao.tools.search import SearchTextTool

    tool = SearchTextTool()
    tool.working_directory = tmp_path
    return tool


@pytest.fixture
def capture_subprocess_run(monkeypatch) -> List[List[str]]:
    """Replace ``search._run_capture`` with an argv-capturing stub.

    Returns the list that captured argv lists are appended to.  The stub
    returns ``returncode=1`` so the caller hits the "no matches" branch
    and exits without real I/O — keeping tests focused on argv shape.

    ``_run_capture`` (not ``subprocess.run``) is the seam: the search
    tool runs every external engine through it for stdin-detach /
    process-group / kill-the-tree-on-timeout hardening.
    """
    from agentao.tools import search as search_mod

    captured: List[List[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(search_mod, "_run_capture", fake_run)
    return captured


@pytest.fixture(scope="session", autouse=True)
def _prompt_toolkit_without_a_console():
    """Give prompt_toolkit somewhere to write when the machine has no console.

    A Windows CI runner has no console screen buffer, so prompt_toolkit's default output
    factory raises ``NoConsoleScreenBufferError`` the moment anything constructs a
    ``PromptSession`` — which the CLI does at construction time. That is a fact about the
    runner, not about agentao: a person running the CLI on Windows has a console.

    Installed only when the real output cannot be created, so on a machine that has one
    nothing changes and the tests keep exercising the same code they always did.
    """
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.output.defaults import create_output

    try:
        create_output()
    except Exception:  # noqa: BLE001 - any failure to reach a terminal is the same answer
        with create_app_session(output=DummyOutput()):
            yield
        return
    yield
