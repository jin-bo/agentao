"""``agentao --login``: exit statuses and argument dispatch (issue #380).

A Terminal Auth client reads only the login process's exit status, so every
outcome is pinned here: ``0`` only when a complete configuration is on disk
afterwards. Dispatch is pinned too — a client may append ``--login`` to its
``--acp`` launch args or replace them, and a flag ``parse_known_args`` used to
swallow would otherwise start a server that exits 0 at end of input.
"""

from __future__ import annotations

import json
import sys

import pytest

import agentao.cli as cli
from agentao.cli import login as login_mod
from agentao.cli.entrypoints import entrypoint
from agentao.embedding.llm_config import load_user_llm_config

_SETTINGS = ("DEEPSEEK", "sk-typed", "https://api.deepseek.com/v1", "deepseek-chat")


@pytest.fixture
def config_path(tmp_path):
    return tmp_path / "home" / ".agentao" / "llm.json"


def _answers(monkeypatch, *, settings=_SETTINGS, replace=None):
    calls = {"prompt": 0}

    def prompt(*, hide_key=False):
        calls["prompt"] += 1
        calls["hide_key"] = hide_key
        if isinstance(settings, BaseException):
            raise settings
        return settings

    monkeypatch.setattr("agentao.cli._llm_prompts._prompt_llm_settings", prompt)

    def prompt_format(provider, previous):
        calls["previous"] = previous
        return "openai-completions"

    monkeypatch.setattr(login_mod, "_prompt_api_format", prompt_format)
    if replace is not None:
        monkeypatch.setattr(login_mod.Confirm, "ask", staticmethod(lambda *a, **k: replace))
    return calls


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# Exit statuses
# ---------------------------------------------------------------------------

def test_a_completed_login_saves_and_exits_zero(monkeypatch, config_path):
    calls = _answers(monkeypatch)

    assert login_mod.run_login(config_path) == 0

    assert calls["hide_key"] is True
    assert load_user_llm_config(config_path) == {
        "provider": "DEEPSEEK", "api_key": "sk-typed",
        "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat",
        "api_format": "openai-completions",
    }


@pytest.mark.parametrize(
    "interrupt, status",
    [(KeyboardInterrupt(), login_mod.EXIT_CANCELLED), (EOFError(), login_mod.EXIT_FAILED)],
)
def test_a_cancelled_login_is_non_zero_and_writes_nothing(
    monkeypatch, config_path, capsys, interrupt, status
):
    _answers(monkeypatch, settings=interrupt)

    assert login_mod.run_login(config_path) == status

    assert not config_path.exists()
    out = capsys.readouterr()
    assert "cancelled" in out.out
    assert "Traceback" not in out.out + out.err


def test_a_failed_save_is_non_zero_without_a_traceback(monkeypatch, config_path, capsys):
    _answers(monkeypatch)

    def refuse(*a, **k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(login_mod, "save_user_llm_config", refuse)

    assert login_mod.run_login(config_path) == login_mod.EXIT_FAILED
    out = capsys.readouterr()
    assert "Permission denied" in out.out
    assert "Traceback" not in out.out + out.err
    assert "sk-typed" not in out.out + out.err


def test_keeping_a_complete_existing_login_is_success(monkeypatch, config_path):
    _write(config_path, {"provider": "OPENAI", "api_key": "k", "base_url": "u", "model": "m"})
    calls = _answers(monkeypatch, replace=False)

    assert login_mod.run_login(config_path) == 0
    assert calls["prompt"] == 0


@pytest.mark.parametrize(
    "existing",
    [
        {"provider": "OPENAI", "api_key": "k"},  # incomplete
        "{not json",                              # unusable
    ],
)
def test_declining_to_replace_an_unusable_login_is_failure(monkeypatch, config_path, existing):
    """Exit 0 here would send the client back into the auth_required it came from."""
    _write(config_path, existing)
    _answers(monkeypatch, replace=False)

    assert login_mod.run_login(config_path) == login_mod.EXIT_FAILED


def test_replacing_an_existing_login_overwrites_it(monkeypatch, config_path):
    _write(config_path, {"provider": "OPENAI", "api_key": "old", "base_url": "u", "model": "m"})
    _answers(monkeypatch, replace=True)

    assert login_mod.run_login(config_path) == 0
    assert load_user_llm_config(config_path)["api_key"] == "sk-typed"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

@pytest.fixture
def dispatch(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "run_login", lambda: seen.setdefault("login", 0) or 0, raising=False)
    monkeypatch.setattr(
        cli, "run_acp_mode",
        lambda resume=None: seen.setdefault("acp", resume if resume is not None else "<none>"),
        raising=False,
    )

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["agentao", *argv])
        try:
            entrypoint()
        except SystemExit as exc:
            seen["exit"] = exc.code
        return seen

    return run


@pytest.mark.parametrize("argv", [("--login",), ("--acp", "--login"), ("--acp", "--stdio", "--login")])
def test_login_takes_precedence_over_the_server(dispatch, argv):
    """Both the spec's appended form and the Registry's replacing form log in."""
    seen = dispatch(*argv)

    assert "login" in seen and "acp" not in seen
    assert seen["exit"] == 0


@pytest.mark.parametrize(
    "argv, resume",
    [
        (("--acp", "--stdio"), "<none>"),           # how DeepChat launches Agentao
        (("--acp",), "<none>"),
        (("--acp", "--stdio", "--resume"), ""),
        (("--acp", "--resume", "abc"), "abc"),
    ],
)
def test_existing_acp_launch_commands_still_start_the_server(dispatch, argv, resume):
    seen = dispatch(*argv)

    assert seen.get("acp") == resume
    assert "login" not in seen


@pytest.mark.parametrize("argv", [("--acp", "--bogus"), ("--login", "--bogus"), ("--acp", "--login", "-x")])
def test_unknown_arguments_are_refused_in_acp_and_login_mode(dispatch, argv, capsys):
    seen = dispatch(*argv)

    assert seen["exit"] == 2
    assert "acp" not in seen and "login" not in seen
    assert "unrecognized arguments" in capsys.readouterr().err


def test_the_login_exit_status_reaches_the_process(monkeypatch):
    monkeypatch.setattr(cli, "run_login", lambda: login_mod.EXIT_CANCELLED, raising=False)
    monkeypatch.setattr(sys, "argv", ["agentao", "--acp", "--login"])

    with pytest.raises(SystemExit) as info:
        entrypoint()

    assert info.value.code == login_mod.EXIT_CANCELLED


# ---------------------------------------------------------------------------
# Wire protocol
# ---------------------------------------------------------------------------

def _default_of_format_prompt(monkeypatch, provider, previous=None):
    seen = {}

    def ask(prompt, *, choices, default):
        seen.update(choices=choices, default=default)
        return default

    monkeypatch.setattr(login_mod.Prompt, "ask", staticmethod(ask))
    assert login_mod._prompt_api_format(provider, previous) == seen["default"]
    return seen


def test_anthropic_defaults_to_its_native_wire(monkeypatch):
    """Unset would mean Chat Completions — a login that "succeeds" and then fails."""
    seen = _default_of_format_prompt(monkeypatch, "ANTHROPIC")

    assert seen["default"] == "anthropic-messages"
    assert "openai-responses" in seen["choices"]


def test_other_providers_default_to_chat_completions(monkeypatch):
    assert _default_of_format_prompt(monkeypatch, "DEEPSEEK")["default"] == "openai-completions"


def test_replacing_a_login_keeps_its_explicit_format_as_the_default(monkeypatch):
    previous = {"provider": "OPENAI", "api_format": "openai-responses"}

    assert _default_of_format_prompt(monkeypatch, "OPENAI", previous)["default"] == "openai-responses"
    # ... but not across providers.
    assert _default_of_format_prompt(monkeypatch, "DEEPSEEK", previous)["default"] == "openai-completions"


def test_the_replaced_configuration_is_offered_to_the_format_prompt(monkeypatch, config_path):
    _write(config_path, {"provider": "OPENAI", "api_key": "old", "base_url": "u",
                         "model": "m", "api_format": "openai-responses"})
    calls = _answers(monkeypatch, replace=True)

    assert login_mod.run_login(config_path) == 0
    assert calls["previous"]["api_format"] == "openai-responses"


def test_custom_provider_reasks_an_empty_url_and_model(monkeypatch):
    # rich's Prompt.ask answers an empty reply with ``default`` itself, and a
    # CUSTOM provider has none: that ``None`` used to crash on ``.strip()``.
    from agentao.cli import _llm_prompts

    answers = iter(["CUSTOM", "MYAPI", "sk-x", None, "https://x.test/v1", None, "m1"])
    asked = []

    def ask(prompt, **kwargs):
        asked.append(prompt)
        return next(answers)

    monkeypatch.setattr(_llm_prompts.Prompt, "ask", staticmethod(ask))

    assert _llm_prompts._prompt_llm_settings() == ("MYAPI", "sk-x", "https://x.test/v1", "m1")
    assert asked.count("MYAPI_BASE_URL") == 2 and asked.count("MYAPI_MODEL") == 2
