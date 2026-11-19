"""Config writers never truncate the file they update.

``Path.write_text`` truncates first, so a crash or a full disk mid-write left
an empty or torn ``settings.json`` / ``mcp.json`` / ``goal.json``. A torn
settings file is then refused by every later save (#495, #497), and a torn
``goal.json`` loads as ``None``. Each writer now goes through
``capabilities.filesystem.write_bytes_atomic``.

The site tests fail the final swap (``_replace_with_retry``): with the old
in-place writes that patch is never reached, the file changes and the tests
fail.
"""

from __future__ import annotations

import errno
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentao.capabilities import filesystem as fs_mod
from agentao.capabilities.filesystem import write_bytes_atomic


def _no_temp_left(directory: Path) -> None:
    assert [p.name for p in directory.iterdir() if p.name.endswith(".tmp")] == []


def _fail_swap(monkeypatch) -> None:
    def boom(tmp, target):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(fs_mod, "_replace_with_retry", boom)


# ---------------------------------------------------------------- the helper


def test_helper_replaces_contents(tmp_path):
    path = tmp_path / "a.json"
    path.write_text("old", encoding="utf-8")
    write_bytes_atomic(path, b"new")
    assert path.read_bytes() == b"new"
    _no_temp_left(tmp_path)


def test_helper_failed_write_keeps_the_original(tmp_path, monkeypatch):
    path = tmp_path / "a.json"
    path.write_text('{"keep": 1}', encoding="utf-8")

    def fsync_fails(fd):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(fs_mod.os, "fsync", fsync_fails)
    with pytest.raises(OSError):
        write_bytes_atomic(path, b'{"keep": 2}')
    assert path.read_text(encoding="utf-8") == '{"keep": 1}'
    _no_temp_left(tmp_path)


def test_helper_interrupt_leaves_no_temp_file(tmp_path, monkeypatch):
    path = tmp_path / "a.json"
    path.write_text("old", encoding="utf-8")

    def interrupted(tmp, target):
        raise KeyboardInterrupt

    monkeypatch.setattr(fs_mod, "_replace_with_retry", interrupted)
    with pytest.raises(KeyboardInterrupt):
        write_bytes_atomic(path, b"new")
    assert path.read_text(encoding="utf-8") == "old"
    _no_temp_left(tmp_path)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_helper_keeps_mode_and_new_file_follows_umask(tmp_path):
    kept = tmp_path / "kept.json"
    kept.write_text("x", encoding="utf-8")
    os.chmod(kept, 0o640)
    write_bytes_atomic(kept, b"y")
    assert kept.stat().st_mode & 0o777 == 0o640

    old_umask = os.umask(0o077)
    try:
        fresh = tmp_path / "fresh.json"
        write_bytes_atomic(fresh, b"y")
        assert fresh.stat().st_mode & 0o777 == 0o600
    finally:
        os.umask(old_umask)

    old_umask = os.umask(0o022)
    try:
        shared = tmp_path / "shared.json"
        write_bytes_atomic(shared, b"y")
        assert shared.stat().st_mode & 0o777 == 0o644
    finally:
        os.umask(old_umask)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores mode bits"
)
def test_helper_refuses_a_read_only_file(tmp_path):
    path = tmp_path / "locked.json"
    path.write_text("keep", encoding="utf-8")
    os.chmod(path, 0o444)
    try:
        with pytest.raises(PermissionError):
            write_bytes_atomic(path, b"new")
        assert path.read_text(encoding="utf-8") == "keep"
        _no_temp_left(tmp_path)
    finally:
        os.chmod(path, 0o644)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges")
def test_helper_updates_a_symlink_target_and_keeps_the_link(tmp_path):
    real_dir = tmp_path / "dotfiles"
    real_dir.mkdir()
    real = real_dir / "settings.json"
    real.write_text("old", encoding="utf-8")
    link = tmp_path / "settings.json"
    link.symlink_to(real)

    write_bytes_atomic(link, b"new")
    assert link.is_symlink()
    assert real.read_bytes() == b"new"
    _no_temp_left(tmp_path)
    _no_temp_left(real_dir)


# ---------------------------------------------------------------- the sites


def _settings(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / ".agentao" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_mode_save_failure_keeps_file_and_warns(tmp_path, monkeypatch):
    from agentao.cli import app as cli_app
    from agentao.permissions import PermissionMode

    original = {"mode": "read-only", "replay": {"enabled": True}}
    path = _settings(tmp_path, original)
    printed = []
    monkeypatch.setattr(cli_app.console, "print", lambda *a, **k: printed.append(a))
    _fail_swap(monkeypatch)

    cli = SimpleNamespace(_project_root=tmp_path, current_mode=PermissionMode.FULL_ACCESS)
    cli_app.AgentaoCLI._save_settings(cli)

    assert json.loads(path.read_text(encoding="utf-8")) == original
    assert "Mode not saved" in str(printed[0][0])
    _no_temp_left(path.parent)


def test_mode_save_writes_mode(tmp_path, monkeypatch):
    from agentao.cli import app as cli_app
    from agentao.permissions import PermissionMode

    path = _settings(tmp_path, {"replay": {"enabled": True}})
    monkeypatch.setattr(cli_app.console, "print", lambda *a, **k: None)
    cli = SimpleNamespace(_project_root=tmp_path, current_mode=PermissionMode.FULL_ACCESS)
    cli_app.AgentaoCLI._save_settings(cli)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"replay": {"enabled": True}, "mode": PermissionMode.FULL_ACCESS.value}


def test_replay_toggle_failure_keeps_file(tmp_path, monkeypatch):
    from agentao.replay.config import save_replay_enabled

    original = {"mode": "read-only", "replay": {"enabled": False}}
    path = _settings(tmp_path, original)
    _fail_swap(monkeypatch)
    with pytest.raises(OSError):
        save_replay_enabled(True, tmp_path)
    assert json.loads(path.read_text(encoding="utf-8")) == original
    _no_temp_left(path.parent)


def test_mcp_config_save_failure_keeps_file(tmp_path, monkeypatch):
    from agentao.mcp.config import save_mcp_config

    config_dir = tmp_path / ".agentao"
    config_dir.mkdir()
    path = config_dir / "mcp.json"
    original = '{"mcpServers": {"a": {"command": "x"}}, "other": 1}'
    path.write_text(original, encoding="utf-8")
    _fail_swap(monkeypatch)
    with pytest.raises(OSError):
        save_mcp_config({}, config_dir=config_dir)
    assert path.read_text(encoding="utf-8") == original
    _no_temp_left(config_dir)


def test_goal_save_failure_keeps_previous_goal(tmp_path, monkeypatch):
    from agentao.cli.goal_state import GoalState, load_goal, save_goal

    assert save_goal(GoalState(objective="first"), tmp_path) is True
    _fail_swap(monkeypatch)
    assert save_goal(GoalState(objective="second"), tmp_path) is False
    loaded = load_goal(tmp_path)
    assert loaded is not None and loaded.objective == "first"
    _no_temp_left(tmp_path / ".agentao")
