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


@pytest.fixture
def acp_short_startup_window(monkeypatch):
    """Give ``ACPProcessHandle.start()`` the POSIX startup window on every platform.

    ``start()`` waits ``_IMMEDIATE_EXIT_WINDOW_S`` for a server that crashes on launch, and
    a healthy server pays the whole window. On Windows that is 1 s, deliberately, because
    process creation outlasts 50 ms. The suites that opt in with ``pytestmark`` test the
    layers above the handle against fakes that never crash on launch, so there the wait
    checks nothing, and they start about 90 servers.

    ``test_acp_client_process.py`` does not opt in. It tests the check itself, and its
    Windows run is the one that has to see the real window.
    """
    from agentao.acp_client import process as process_mod

    monkeypatch.setattr(process_mod, "_IMMEDIATE_EXIT_WINDOW_S", 0.05)


@pytest.fixture
def isolated_cwd(tmp_path, monkeypatch):
    """Run the test from ``tmp_path``, so what agentao writes to its cwd lands there.

    ``Agentao(working_directory=Path.cwd())`` and ``AgentaoCLI()`` open the project memory
    store at ``<cwd>/.agentao/memory.db``, and ``LLMClient``'s default ``log_file`` opens
    ``<cwd>/agentao.log``. From the repository root that is the developer's own project
    store: a suite run left ``test_key``, ``order_probe`` and ``suffix_probe`` there as live
    project memories, which agentao then injects into ``<memory-stable>``. Modules that
    build an agent that way opt in with ``pytestmark``; ``pytest_sessionfinish`` below
    fails the run when a test writes there anyway.
    """
    monkeypatch.chdir(tmp_path)


_REPO_ROOT = Path(__file__).resolve().parent.parent
#: What agentao writes to its cwd unless told otherwise.
_CWD_ARTIFACTS = (".agentao", "agentao.log")
_preexisting_cwd_artifacts = pytest.StashKey[frozenset]()
_leaked_cwd_artifacts = pytest.StashKey[list]()


def pytest_sessionstart(session):
    session.config.stash[_preexisting_cwd_artifacts] = frozenset(
        name for name in _CWD_ARTIFACTS if (_REPO_ROOT / name).exists()
    )


def pytest_sessionfinish(session, exitstatus):
    """Fail the run when tests created agentao's cwd artifacts in the repository root.

    Only what did not exist at session start counts. On CI neither ever does, so a leak
    fails there; in a checkout where someone has run agentao they may already exist, and
    the guard stays out of the way rather than mistake that person's own files for a leak.
    """
    if hasattr(session.config, "workerinput"):  # an xdist worker; the controller checks
        return
    before = session.config.stash.get(_preexisting_cwd_artifacts, frozenset())
    leaked = [
        name for name in _CWD_ARTIFACTS
        if name not in before and (_REPO_ROOT / name).exists()
    ]
    if leaked:
        session.config.stash[_leaked_cwd_artifacts] = leaked
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    leaked = config.stash.get(_leaked_cwd_artifacts, None)
    if leaked:
        terminalreporter.write_sep("=", "tests wrote agentao's cwd artifacts", red=True)
        terminalreporter.write_line(
            f"created in {_REPO_ROOT}: {', '.join(leaked)}. A test built an agent against "
            "the process cwd; give it tmp_path, or opt its module into ``isolated_cwd``."
        )


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
