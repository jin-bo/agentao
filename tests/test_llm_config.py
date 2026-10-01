"""Per-session LLM resolution and the login file (``agentao.embedding.llm_config``).

The resolver is what keeps one ACP process from carrying a project's
credentials into another project's session, so these tests pin its two rules
directly: the provider is chosen *before* any field is read (an API key's
mere presence never picks one), and fields come only from that provider's
prefix and a login file for that same provider.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from agentao.embedding.llm_config import (
    LLMConfigError,
    load_user_llm_config,
    resolve_session_llm_config,
    save_user_llm_config,
)


def _write_login(path: Path, **fields: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fields), encoding="utf-8")
    return path


def _deepseek_login(tmp_path: Path) -> Path:
    return _write_login(
        tmp_path / "home" / "llm.json",
        provider="DEEPSEEK",
        api_key="ds-key",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
    )


def _project(tmp_path: Path, dotenv: str = "") -> Path:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    if dotenv:
        (project / ".env").write_text(dotenv, encoding="utf-8")
    return project


def test_an_api_key_in_the_launch_env_does_not_choose_the_provider(tmp_path):
    """A DeepSeek login stays DeepSeek although the shell exports an OpenAI key.

    ``OPENAI`` is the default provider, so a resolver that let "this layer has
    a key" decide would silently send the session to OpenAI.
    """
    resolved = resolve_session_llm_config(
        _project(tmp_path),
        launch_env={"OPENAI_API_KEY": "openai-key-for-another-tool"},
        user_config_path=_deepseek_login(tmp_path),
    )

    assert resolved.provider == "DEEPSEEK"
    kwargs = resolved.llm_kwargs()
    assert kwargs["api_key"] == "ds-key"
    assert kwargs["model"] == "deepseek-chat"
    assert "openai-key-for-another-tool" not in resolved.env.values()


def test_layers_rank_launch_env_over_project_over_login(tmp_path):
    login = _deepseek_login(tmp_path)
    project = _project(tmp_path, "DEEPSEEK_MODEL=project-model\nLLM_MAX_TOKENS=4096\n")

    resolved = resolve_session_llm_config(
        project,
        launch_env={"DEEPSEEK_MODEL": "launch-model"},
        user_config_path=login,
    )

    kwargs = resolved.llm_kwargs()
    assert kwargs["model"] == "launch-model"           # launch env wins
    assert kwargs["max_tokens"] == 4096                 # project over login
    assert kwargs["api_key"] == "ds-key"               # login fills the rest


def test_a_shell_key_is_not_sent_to_the_logins_endpoint(tmp_path):
    """Key and base URL come as a pair, from the highest layer with a key.

    Field by field, the exported key outranked the login's and went to the
    login's gateway — and logging in again could never change that.
    """
    login = _write_login(
        tmp_path / "home" / "llm.json",
        provider="OPENAI", api_key="gateway-key",
        base_url="https://gateway.corp/v1", model="gpt-x",
    )

    resolved = resolve_session_llm_config(
        _project(tmp_path),
        launch_env={"OPENAI_API_KEY": "real-openai-key"},
        user_config_path=login,
    )

    # The launch layer's key has no URL beside it, so it is not paired with
    # the login's gateway: the URL is missing instead.
    assert resolved.env["OPENAI_API_KEY"] == "real-openai-key"
    assert resolved.missing_fields() == ("base_url",)
    assert "https://gateway.corp/v1" not in resolved.env.values()


def test_key_and_url_from_the_same_layer_are_used_together(tmp_path):
    login = _deepseek_login(tmp_path)
    project = _project(tmp_path, "DEEPSEEK_API_KEY=proj-key\nDEEPSEEK_BASE_URL=https://proj/v1\n")

    kwargs = resolve_session_llm_config(
        project, launch_env={"DEEPSEEK_BASE_URL": "https://launch/v1"}, user_config_path=login,
    ).llm_kwargs()

    # The launch URL has no key beside it, so it does not redirect the
    # project's key either.
    assert (kwargs["api_key"], kwargs["base_url"]) == ("proj-key", "https://proj/v1")


def test_a_logins_wire_format_does_not_follow_a_project_endpoint(tmp_path):
    """The format belongs to the endpoint the key and URL came from.

    A project's OpenAI-compatible gateway under the ANTHROPIC prefix, with no
    format of its own, kept working until an Anthropic login (which saves
    ``anthropic-messages``) lent it the wrong protocol.
    """
    login = _write_login(
        tmp_path / "home" / "llm.json",
        provider="ANTHROPIC", api_key="sk-ant", base_url="https://api.anthropic.com",
        model="claude-x", api_format="anthropic-messages",
    )
    project = _project(tmp_path, (
        "LLM_PROVIDER=ANTHROPIC\nANTHROPIC_API_KEY=gw-key\n"
        "ANTHROPIC_BASE_URL=https://gateway.corp/v1\n"
    ))

    kwargs = resolve_session_llm_config(project, launch_env={}, user_config_path=login).llm_kwargs()

    assert (kwargs["api_key"], kwargs["base_url"]) == ("gw-key", "https://gateway.corp/v1")
    assert "api_format" not in kwargs  # the default wire, not the login's
    assert kwargs["model"] == "claude-x"  # the model still layers


def test_the_login_format_applies_to_the_logins_own_endpoint(tmp_path):
    login = _write_login(
        tmp_path / "home" / "llm.json",
        provider="ANTHROPIC", api_key="sk-ant", base_url="https://api.anthropic.com",
        model="claude-x", api_format="anthropic-messages",
    )

    kwargs = resolve_session_llm_config(
        _project(tmp_path), launch_env={}, user_config_path=login,
    ).llm_kwargs()

    assert kwargs["api_format"] == "anthropic-messages"


def test_a_key_without_a_url_in_its_layer_leaves_the_url_missing(tmp_path):
    login = _deepseek_login(tmp_path)
    project = _project(tmp_path, "DEEPSEEK_API_KEY=proj-key\n")

    resolved = resolve_session_llm_config(project, launch_env={}, user_config_path=login)

    assert resolved.missing_fields() == ("base_url",)


def test_an_explicit_provider_in_a_higher_layer_wins(tmp_path):
    project = _project(
        tmp_path,
        "LLM_PROVIDER=GEMINI\nGEMINI_API_KEY=g\nGEMINI_BASE_URL=https://g/v1\nGEMINI_MODEL=gem\n",
    )

    resolved = resolve_session_llm_config(
        project, launch_env={}, user_config_path=_deepseek_login(tmp_path)
    )

    assert resolved.provider == "GEMINI"
    assert resolved.llm_kwargs()["api_key"] == "g"


def test_a_login_for_another_provider_contributes_nothing(tmp_path):
    """Never pair one provider's key with another's endpoint."""
    resolved = resolve_session_llm_config(
        _project(tmp_path),
        launch_env={"LLM_PROVIDER": "OPENAI", "OPENAI_MODEL": "gpt-x"},
        user_config_path=_deepseek_login(tmp_path),
    )

    assert resolved.provider == "OPENAI"
    assert set(resolved.missing_fields()) == {"api_key", "base_url"}
    assert "ds-key" not in resolved.env.values()


@pytest.mark.parametrize("blank", ["", "   "])
def test_an_empty_value_does_not_mask_a_real_one(tmp_path, blank):
    """Claude Code exports ``ANTHROPIC_API_KEY=""`` to its children; empty is unset."""
    resolved = resolve_session_llm_config(
        _project(tmp_path),
        launch_env={"LLM_PROVIDER": blank, "DEEPSEEK_API_KEY": blank},
        user_config_path=_deepseek_login(tmp_path),
    )

    assert resolved.provider == "DEEPSEEK"
    assert resolved.llm_kwargs()["api_key"] == "ds-key"


def test_nothing_configured_defaults_to_openai_with_every_field_missing(tmp_path):
    resolved = resolve_session_llm_config(
        _project(tmp_path), launch_env={}, user_config_path=tmp_path / "absent.json"
    )

    assert resolved.provider == "OPENAI"
    assert resolved.missing_fields() == ("api_key", "base_url", "model")


def test_the_project_dotenv_is_read_without_touching_os_environ(tmp_path, monkeypatch):
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    project = _project(tmp_path, "DEEPSEEK_API_KEY=from-project\nJINA_API_KEY=jina\n")

    resolved = resolve_session_llm_config(
        project, launch_env={}, user_config_path=_deepseek_login(tmp_path)
    )

    assert resolved.llm_kwargs()["api_key"] == "from-project"
    assert "JINA_API_KEY" not in resolved.env  # only LLM keys are taken
    assert "JINA_API_KEY" not in os.environ
    assert "DEEPSEEK_API_KEY" not in os.environ


def test_dotenv_references_expand_against_the_launch_snapshot(tmp_path, monkeypatch):
    """Not against the live ``os.environ``, which can change after startup."""
    monkeypatch.setenv("TEAM_KEY", "changed-after-startup")
    project = _project(tmp_path, (
        "LLM_PROVIDER=DEEPSEEK\n"
        "DEEPSEEK_API_KEY=${TEAM_KEY}\n"
        "HOST=proxy.corp\n"
        "DEEPSEEK_BASE_URL=https://${HOST}/v1\n"
        "DEEPSEEK_MODEL=${MISSING:-fallback-model}\n"
    ))

    kwargs = resolve_session_llm_config(
        project, launch_env={"TEAM_KEY": "snapshot-key"},
        user_config_path=tmp_path / "absent.json",
    ).llm_kwargs()

    assert kwargs["api_key"] == "snapshot-key"
    assert kwargs["base_url"] == "https://proxy.corp/v1"  # earlier file value
    assert kwargs["model"] == "fallback-model"


def test_no_dotenv_above_the_project_root_is_read(tmp_path):
    """The old fallback searched upward from the process cwd."""
    (tmp_path / ".env").write_text("OPENAI_API_KEY=from-parent\n", encoding="utf-8")
    project = _project(tmp_path)

    resolved = resolve_session_llm_config(
        project, launch_env={}, user_config_path=tmp_path / "absent.json"
    )

    assert "api_key" in resolved.missing_fields()


def test_a_malformed_number_is_a_config_error_naming_the_variable(tmp_path):
    project = _project(tmp_path, "LLM_TEMPERATURE=warm\n")

    resolved = resolve_session_llm_config(
        project, launch_env={}, user_config_path=_deepseek_login(tmp_path)
    )

    with pytest.raises(LLMConfigError, match="LLM_TEMPERATURE"):
        resolved.llm_kwargs()


def test_resolve_provider_answers_only_for_the_sessions_provider(tmp_path):
    resolved = resolve_session_llm_config(
        _project(tmp_path), launch_env={}, user_config_path=_deepseek_login(tmp_path)
    )

    assert resolved.resolve_provider("deepseek") == {
        "api_key": "ds-key",
        "base_url": "https://api.deepseek.com/v1",
        "api_format": None,
    }
    with pytest.raises(LookupError):
        resolved.resolve_provider("openai")


# ---------------------------------------------------------------------------
# The login file
# ---------------------------------------------------------------------------

def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "home" / "llm.json"

    save_user_llm_config(
        path, provider="DEEPSEEK", api_key="k", base_url="https://u", model="m",
    )

    assert load_user_llm_config(path) == {
        "provider": "DEEPSEEK", "api_key": "k", "base_url": "https://u", "model": "m",
    }
    assert not list(path.parent.glob(".llm.json.*.tmp"))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_save_writes_owner_only_whatever_the_umask(tmp_path):
    path = tmp_path / "llm.json"
    old = os.umask(0)
    try:
        save_user_llm_config(path, provider="P", api_key="k", base_url="u", model="m")
    finally:
        os.umask(old)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_save_narrows_an_existing_world_readable_file(tmp_path):
    path = tmp_path / "llm.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o644)

    save_user_llm_config(path, provider="P", api_key="k", base_url="u", model="m")

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_load_returns_none_for_a_missing_file(tmp_path):
    assert load_user_llm_config(tmp_path / "absent.json") is None


@pytest.mark.parametrize(
    "content, match",
    [
        ("{not json", "not valid JSON"),
        ("[1, 2]", "JSON object"),
        ('{"api_key": 42}', "api_key"),
    ],
)
def test_load_rejects_an_unusable_file_without_echoing_values(tmp_path, content, match):
    path = tmp_path / "llm.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(LLMConfigError, match=match) as info:
        load_user_llm_config(path)
    assert "42" not in str(info.value)
