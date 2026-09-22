"""Jev configuration never turns a typo into an enabled remote service."""
import json
from dataclasses import asdict

import pytest


def test_settings_default_disabled_and_validate_types():
    from agentao.recommendations import JevConfig

    assert JevConfig().enabled is False
    assert JevConfig().timeout_ms == 10_000
    for value in ({"enabled": "false"}, {"timeout_ms": 0},
                  {"timeout_ms": True}, {"min_confidence": float("nan")},
                  {"min_confidence": 1.1}, {"skill_recommendation": "auto"},
                  {"model": ""}):
        with pytest.raises(ValueError):
            JevConfig.from_dict(value)


def test_key_precedence_and_empty_environment_fallback(tmp_path, monkeypatch):
    from agentao.embedding.jev import load_jev

    project = tmp_path / "project"
    home = tmp_path / "user"
    project.mkdir()
    home.mkdir()
    (home / "credentials.json").write_text(json.dumps({"typesafe_api_key": "saved-key"}))
    (project / ".env").write_text("TYPESAFE_API_KEY=project-key\n")
    monkeypatch.setenv("TYPESAFE_API_KEY", "environment-key")
    assert load_jev(project, user_dir=home).api_key == "environment-key"
    monkeypatch.setenv("TYPESAFE_API_KEY", "  ")
    assert load_jev(project, user_dir=home).api_key == "project-key"
    (project / ".env").unlink()
    assert load_jev(project, user_dir=home).api_key == "saved-key"
    assert "saved-key" not in repr(load_jev(project, user_dir=home))


def test_config_ignores_key_in_project_settings(tmp_path, monkeypatch):
    from agentao.embedding.jev import load_jev

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    d = tmp_path / ".agentao"
    d.mkdir()
    (d / "settings.json").write_text(json.dumps({"jev": {
        "enabled": True, "api_key": "do-not-trust-this", "timeout_ms": 550,
    }}))
    service = load_jev(tmp_path, user_dir=tmp_path / "user")
    assert service.config.enabled
    assert service.config.timeout_ms == 550
    assert not service.api_key


def test_invalid_settings_disable_and_diagnostics_do_not_echo(tmp_path, monkeypatch, caplog):
    from agentao.embedding.jev import load_jev

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    d = tmp_path / ".agentao"
    d.mkdir()
    (d / "settings.json").write_text(json.dumps({"jev": {
        "enabled": True, "timeout_ms": "secret-should-not-appear",
    }}))
    assert not load_jev(tmp_path, user_dir=tmp_path / "user").config.enabled
    assert "secret-should-not-appear" not in caplog.text


def test_save_key_and_settings_preserve_other_entries(tmp_path):
    from agentao.embedding.jev import save_jev_settings, save_typesafe_key
    from agentao.recommendations import JevConfig

    home = tmp_path / "home"
    home.mkdir()
    credential_file = home / "credentials.json"
    credential_file.write_text(json.dumps({"other": "untouched"}))
    save_typesafe_key("new-secret", user_dir=home)
    assert json.loads(credential_file.read_text()) == {
        "other": "untouched", "typesafe_api_key": "new-secret",
    }
    d = tmp_path / ".agentao"
    d.mkdir()
    (d / "settings.json").write_text(json.dumps({"mode": "read-only", "agents": {"enable_builtin": True}}))
    config = JevConfig(enabled=True)
    save_jev_settings(tmp_path, config)
    saved = json.loads((d / "settings.json").read_text())
    assert saved["mode"] == "read-only"
    assert saved["agents"] == {"enable_builtin": True}
    assert saved["jev"] == asdict(config)
    assert "new-secret" not in (d / "settings.json").read_text()


def test_save_refuses_to_overwrite_broken_credentials(tmp_path):
    from agentao.embedding.jev import save_typesafe_key

    path = tmp_path / "credentials.json"
    path.write_text("{broken")
    with pytest.raises(ValueError):
        save_typesafe_key("key", user_dir=tmp_path)
    assert path.read_text() == "{broken"


def test_save_lock_is_bounded_and_preserves_existing_file(tmp_path, monkeypatch):
    import threading
    from filelock import FileLock, Timeout
    from agentao.embedding import jev

    path = tmp_path / "credentials.json"
    path.write_text('{"other": "keep"}')
    monkeypatch.setattr(jev, "_SAVE_LOCK_TIMEOUT", 0.05, raising=False)
    acquired, release = threading.Event(), threading.Event()

    def hold_lock():
        with FileLock(str(path) + ".lock"):
            acquired.set()
            release.wait(0.5)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert acquired.wait(2)
    try:
        with pytest.raises(Timeout):
            jev.save_typesafe_key("new-secret", user_dir=tmp_path)
        assert json.loads(path.read_text()) == {"other": "keep"}
    finally:
        release.set()
        holder.join(2)
