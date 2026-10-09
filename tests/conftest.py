"""Shared pytest fixtures for the agentao test suite."""

import functools
import os
from pathlib import Path
from types import SimpleNamespace
from typing import List

import pytest


@pytest.fixture(autouse=True)
def _no_dotenv_discovery(monkeypatch):
    """Stop ``safe_load_dotenv()`` with no path from finding a ``.env`` (#471).

    Without a path it calls ``find_dotenv(usecwd=True)``, which walks up from the
    process cwd. From a checkout with no ``.env`` that walk ends at whatever sits
    above it, a developer's ``~/.env`` included. The write goes straight to
    ``os.environ``, outside ``monkeypatch``, so ``LLM_PROVIDER``, ``*_API_FORMAT``
    and other providers' keys stayed for the rest of the session. That came in
    through the factory and the CLI, not only through tests that load a ``.env``
    on purpose. An explicit path still loads, so a test that writes its own
    ``.env`` under ``tmp_path`` is unaffected.
    """
    monkeypatch.setattr("agentao._env.find_dotenv", lambda *args, **kwargs: "")


def _is_llm_env_key(key: str) -> bool:
    """A variable that picks or configures an LLM provider.

    ``discover_llm_kwargs()`` reads ``LLM_*`` and ``{PROVIDER}_API_KEY`` /
    ``_BASE_URL`` / ``_MODEL`` / ``_API_FORMAT`` for whatever ``LLM_PROVIDER``
    names, so the provider-prefixed keys are matched by suffix. The ``openai``
    and ``anthropic`` SDKs read their own ``OPENAI_*`` / ``ANTHROPIC_*``
    (``OPENAI_ORG_ID``, ``ANTHROPIC_AUTH_TOKEN``) when a client gets ``None``.
    """
    return (
        key.startswith(("LLM_", "OPENAI_", "ANTHROPIC_"))
        or key.endswith(("_API_KEY", "_BASE_URL", "_MODEL", "_API_FORMAT"))
    )


#: The shell's LLM settings, read once at collection, before any test ran.
#: Only ``live_llm_env`` puts them back, for a test that opted in to a provider.
_SHELL_LLM_ENV = {k: v for k, v in os.environ.items() if _is_llm_env_key(k)}


@pytest.fixture(autouse=True)
def _scrub_llm_env(monkeypatch):
    """Start every test with no LLM settings from the shell (#470).

    ``_agentao_env_default_credentials`` fills ``Agentao(...)`` in from the
    process environment. Left as the shell had it, ``LLM_PROVIDER=ANTHROPIC``
    gave tests that provider's real key and endpoint, ``OPENAI_API_FORMAT``
    switched their wire, and a malformed ``LLM_TEMPERATURE`` or an unknown
    ``LLM_PROMPT_CACHE`` failed construction on that machine only. A test
    that needs one of these sets it with ``monkeypatch``.
    """
    for key in [k for k in os.environ if _is_llm_env_key(k)]:
        monkeypatch.delenv(key)


@pytest.fixture
def live_llm_env(monkeypatch, _stub_llm_credentials):
    """Put the shell's LLM settings back, for a test that calls a real provider.

    Request it only behind an opt-in gate (``AGENTAO_TEST_LIVE_LLM``,
    ``AGENTAO_TEST_LIVE_MODELS``). It runs after the scrub and the stubs, so the
    shell's values win where it has them and the stubs fill in the rest.
    """
    for key, value in _SHELL_LLM_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def _stub_llm_credentials(monkeypatch, _scrub_llm_env):
    """Set dummy LLM credentials for every test.

    Production code resolves provider env vars only inside
    ``agentao.embedding.build_from_environment``. Tests that
    instantiate ``Agentao(working_directory=...)`` directly used to
    rely on those env reads, so we stub them here and have
    ``_agentao_env_default_credentials`` mirror the factory's
    discovery contract through ``discover_llm_kwargs()``. The shell's own
    values are scrubbed first and never used, outside ``live_llm_env``.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test-dummy-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.4")


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

    # ``wraps`` keeps ``inspect.signature(Agentao.__init__)`` reading the
    # real signature, which tests inspect.
    @functools.wraps(_orig_init)
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


@pytest.fixture
def isolated_skill_dirs(tmp_path, monkeypatch):
    """Keep skill discovery off the developer's home.

    ``_GLOBAL_SKILLS_DIR`` and ``_BUNDLED_SKILLS_DIR`` are module constants bound at
    import time, so a redirected ``HOME`` does not move them: left alone, ``Agentao(...)``
    scans the real ``~/.agentao/skills`` and ``_bootstrap_bundled_skills`` copies this
    repo's ``skills/`` into it, which also makes any catalogue assertion depend on
    whatever that machine happens to hold.
    """
    from agentao.skills import manager as skills_manager

    monkeypatch.setattr(skills_manager, "_GLOBAL_SKILLS_DIR", tmp_path / "home" / "skills")
    monkeypatch.setattr(skills_manager, "_BUNDLED_SKILLS_DIR", tmp_path / "no-bundled-skills")


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
