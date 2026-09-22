"""Drive commands through their real handler without putting keys in history."""
import io
import json
from types import SimpleNamespace

import pytest
from rich.console import Console


@pytest.fixture
def command(tmp_path, monkeypatch):
    from agentao.cli.commands import jev
    from agentao.recommendations import JevSkillRecommender

    output = io.StringIO()
    monkeypatch.setattr(jev, "console", Console(file=output, color_system=None))
    monkeypatch.setattr("agentao.embedding.jev.user_root", lambda: tmp_path / "user")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    cli = SimpleNamespace(agent=SimpleNamespace(
        working_directory=tmp_path, skill_recommender=JevSkillRecommender(api_key="existing-secret"),
        _skill_suggestion=None))
    return jev, cli, output


def test_on_off_session_only_save_explicitly(command, tmp_path):
    jev, cli, output = command
    jev.handle_jev_command(cli, "on")
    assert cli.agent.skill_recommender.config.enabled
    assert not (tmp_path / ".agentao" / "settings.json").exists()
    jev.handle_jev_command(cli, "save")
    path = tmp_path / ".agentao" / "settings.json"
    assert json.loads(path.read_text())["jev"]["enabled"] is True
    jev.handle_jev_command(cli, "off")
    assert not cli.agent.skill_recommender.config.enabled
    assert json.loads(path.read_text())["jev"]["enabled"] is True
    jev.handle_jev_command(cli, "status")
    assert "existing-secret" not in output.getvalue()


def test_first_enable_collects_hidden_key_and_saves_only_when_chosen(command, monkeypatch, tmp_path):
    jev, cli, output = command
    cli.agent.skill_recommender.api_key = ""
    monkeypatch.setattr(jev, "read_hidden_key", lambda: "new-secret")
    monkeypatch.setattr(jev.Confirm, "ask", lambda *a, **k: True)
    jev.handle_jev_command(cli, "on")
    assert cli.agent.skill_recommender.config.enabled
    assert cli.agent.skill_recommender.api_key == "new-secret"
    saved = json.loads((tmp_path / "user" / "credentials.json").read_text())
    assert saved["typesafe_api_key"] == "new-secret"
    assert "new-secret" not in output.getvalue()


def test_setup_session_only_does_not_write_key(command, monkeypatch, tmp_path):
    jev, cli, _ = command
    monkeypatch.setattr(jev, "read_hidden_key", lambda: "temporary-secret")
    monkeypatch.setattr(jev.Confirm, "ask", lambda *a, **k: False)
    jev.handle_jev_command(cli, "setup")
    assert cli.agent.skill_recommender.api_key == "temporary-secret"
    assert not (tmp_path / "user" / "credentials.json").exists()


def test_abort_does_not_enable_or_leak(command, monkeypatch):
    jev, cli, output = command
    cli.agent.skill_recommender.api_key = ""

    def abort():
        raise EOFError("do-not-echo-secret")

    monkeypatch.setattr(jev, "read_hidden_key", abort)
    jev.handle_jev_command(cli, "on")
    assert not cli.agent.skill_recommender.config.enabled
    assert "do-not-echo-secret" not in output.getvalue()


def test_key_as_command_argument_is_rejected_without_echo(command):
    jev, cli, output = command
    jev.handle_jev_command(cli, "setup secret-in-argument")
    assert "secret-in-argument" not in output.getvalue()
    assert cli.agent.skill_recommender.api_key == "existing-secret"


def test_getpass_refuses_echo_fallback(monkeypatch):
    import getpass
    import warnings
    from agentao.cli.commands.jev import read_hidden_key

    def fallback(*a, **k):
        warnings.warn("Unable to hide input", getpass.GetPassWarning)
        pytest.fail("should stop before echoing")

    monkeypatch.setattr(getpass, "getpass", fallback)
    with pytest.raises(getpass.GetPassWarning):
        read_hidden_key()


@pytest.mark.parametrize("action", ["setup", "save"])
def test_locked_config_does_not_crash_or_echo_secret(command, monkeypatch, action):
    from filelock import Timeout

    jev, cli, output = command
    monkeypatch.setattr(jev, "read_hidden_key", lambda: "new-secret")
    monkeypatch.setattr(jev.Confirm, "ask", lambda *a, **k: True)

    def locked(*a, **k):
        raise Timeout("never-echo-this-secret")

    monkeypatch.setattr(jev, "save_typesafe_key", locked)
    monkeypatch.setattr(jev, "save_jev_settings", locked)
    jev.handle_jev_command(cli, action)
    assert "Could not save" in output.getvalue()
    assert "never-echo-this-secret" not in output.getvalue()
    assert cli.agent.skill_recommender.api_key == "existing-secret"
