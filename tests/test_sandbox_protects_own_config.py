"""A sandboxed command cannot rewrite the sandbox's own configuration.

The workspace-write profiles allow writes anywhere under the workspace. With a
working directory of ``~`` that includes ``~/.agentao/sandbox.json``: one
command could write ``{"enabled": false}`` and every later command ran
unsandboxed. The same holds for a ``profiles_dir`` (or the built-in profiles)
inside the workspace, and for the rest of ``~/.agentao`` (``permissions.json``,
``mcp.json``), which is read unsandboxed later.

The resolved profile now denies writes to those paths, after the profile's own
rules, so the deny wins.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from agentao.sandbox import SandboxPolicy
from agentao.sandbox.policy import _BUILTIN_PROFILES_DIR

MACOS_ONLY = pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec is macOS only")


def _home(tmp_path: Path, **cfg) -> Path:
    home = tmp_path / "home"
    (home / ".agentao").mkdir(parents=True)
    (home / ".agentao" / "sandbox.json").write_text(json.dumps({"enabled": True, **cfg}))
    return home


def _profile(home: Path, workspace: Path, **cfg):
    config = {"enabled": True, "default_profile": "workspace-write", **cfg}
    with patch.object(Path, "home", return_value=home):
        policy = SandboxPolicy(project_root=workspace, config=config)
        return policy.resolve("run_shell_command", {"command": "x"})


def _run(profile, script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        profile.as_args() + ["/bin/sh", "-c", script],
        capture_output=True, text=True, timeout=30,
    )


@MACOS_ONLY
def test_a_command_in_home_cannot_switch_the_sandbox_off(tmp_path):
    home = _home(tmp_path)
    cfg = home / ".agentao" / "sandbox.json"
    profile = _profile(home, workspace=home)

    r = _run(profile, f"""
        echo '{{"enabled": false}}' > '{cfg}'; echo write=$?
        rm -f '{cfg}'; echo rm=$?
        mv '{cfg}' '{home}/moved.json'; echo mv=$?
        mv '{home}/.agentao' '{home}/gone'; echo mvdir=$?
        echo '{{}}' > '{home}/.agentao/mcp.json'; echo mcp=$?
        echo ok > '{home}/notes.txt'; echo other=$?
    """)

    assert "write=1" in r.stdout and "rm=1" in r.stdout
    assert "mv=1" in r.stdout and "mvdir=1" in r.stdout and "mcp=1" in r.stdout
    assert "other=0" in r.stdout                      # the rest of the workspace is writable
    assert json.loads(cfg.read_text())["enabled"] is True
    assert not (home / ".agentao" / "mcp.json").exists()


@MACOS_ONLY
def test_a_profiles_dir_inside_the_workspace_is_protected(tmp_path):
    home = _home(tmp_path)
    proj = tmp_path / "proj"
    prof_dir = proj / "sb"
    prof_dir.mkdir(parents=True)
    builtin = _BUILTIN_PROFILES_DIR
    (prof_dir / "mine.sb").write_text((builtin / "workspace-write.sb").read_text())
    profile = _profile(home, workspace=proj, profiles_dir=str(prof_dir), default_profile="mine")

    r = _run(profile, f"echo '(version 1)(allow default)' > '{prof_dir}/mine.sb'; echo write=$?; "
                      f"mv '{prof_dir}' '{proj}/x'; echo mv=$?; echo ok > '{proj}/a.txt'; echo other=$?")

    assert "write=1" in r.stdout and "mv=1" in r.stdout and "other=0" in r.stdout


@MACOS_ONLY
def test_a_workspace_inside_the_config_dir_protects_only_the_config_files(tmp_path):
    home = _home(tmp_path)
    workspace = home / ".agentao"
    profile = _profile(home, workspace=workspace)

    r = _run(profile, f"echo x > '{workspace}/sandbox.json'; echo write=$?; "
                      f"echo ok > '{workspace}/notes.txt'; echo other=$?")

    assert "write=1" in r.stdout and "other=0" in r.stdout


@MACOS_ONLY
def test_a_path_with_quotes_still_loads(tmp_path):
    home = _home(tmp_path / 'we"ird\\dir')
    profile = _profile(home, workspace=home)

    r = _run(profile, "echo ran")

    assert r.returncode == 0, r.stderr
    assert "ran" in r.stdout


def test_the_user_config_is_denied_whatever_the_workspace(tmp_path):
    """No guess about where a profile allows writes (``/tmp``, a custom
    profile's own subpaths): the user config is always on the deny list."""
    home = _home(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    profile = _profile(home, workspace=proj)

    assert str(home / ".agentao") in profile.protected_subpaths
    assert "-f" not in profile.as_args()


def test_home_as_workspace_denies_the_config_dir_and_names_its_real_path(tmp_path):
    home = _home(tmp_path)
    profile = _profile(home, workspace=home)

    assert str(home / ".agentao") in profile.protected_subpaths
    args = profile.as_args()
    assert args[args.index("-p") + 1].count("(deny file-write*") == 1


@MACOS_ONLY
def test_the_shell_tool_wrapper_carries_the_deny(tmp_path):
    """Through ``_wrap_with_sandbox``, the string ``run_shell_command`` runs."""
    from agentao.tools.shell import _wrap_with_sandbox

    home = _home(tmp_path)
    cfg = home / ".agentao" / "sandbox.json"
    profile = _profile(home, workspace=home)

    wrapped = _wrap_with_sandbox(f"echo '{{\"enabled\": false}}' > '{cfg}'", profile)
    r = subprocess.run(wrapped, shell=True, capture_output=True, text=True, timeout=30)

    assert r.returncode != 0
    assert "Operation not permitted" in r.stderr
    assert json.loads(cfg.read_text())["enabled"] is True


def test_a_config_dir_in_a_temp_area_is_protected_whatever_the_workspace(tmp_path):
    """tmp_path is under /private/var/folders: every built-in profile writes there."""
    home = _home(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    profile = _profile(home, workspace=proj)

    assert str(home / ".agentao") in profile.protected_subpaths


@MACOS_ONLY
def test_a_symlinked_config_dir_cannot_be_replaced(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    real = tmp_path / "realcfg"
    real.mkdir()
    (real / "sandbox.json").write_text(json.dumps({"enabled": True}))
    (home / ".agentao").symlink_to(real)
    profile = _profile(home, workspace=home)

    r = _run(profile, f"rm '{home}/.agentao'; echo rm=$?")

    assert "rm=1" in r.stdout
    assert (home / ".agentao").is_symlink()


@MACOS_ONLY
def test_a_new_profile_cannot_be_added_to_a_profiles_dir_that_is_the_workspace(tmp_path):
    """A new ``workspace-write.sb`` there would shadow the built-in one."""
    home = _home(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    policy_cfg = {"profiles_dir": str(proj)}
    profile = _profile(home, workspace=proj, **policy_cfg)

    r = _run(profile, f"echo '(version 1)(allow default)' > '{proj}/workspace-write.sb'; echo write=$?; "
                      f"echo ok > '{proj}/notes.txt'; echo other=$?")

    assert "write=1" in r.stdout and "other=0" in r.stdout
    assert not (proj / "workspace-write.sb").exists()


def _fake_install(monkeypatch, directory: Path) -> Path:
    """Point the built-in profile directory at ``directory``, holding copies."""
    from agentao.sandbox import policy as policy_mod

    directory.mkdir(parents=True)
    for f in _BUILTIN_PROFILES_DIR.glob("*.sb"):
        (directory / f.name).write_text(f.read_text())
    monkeypatch.setattr(policy_mod, "_BUILTIN_PROFILES_DIR", directory)
    return directory


@MACOS_ONLY
def test_a_built_in_profile_rewritten_or_deleted_after_import_changes_nothing(tmp_path, monkeypatch):
    from agentao.sandbox.policy import _BUILTIN_PROFILES

    home = _home(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    installed = _fake_install(monkeypatch, proj / "site" / "profiles")
    (installed / "workspace-write.sb").write_text("(version 1)\n(allow default)\n")
    (installed / "readonly.sb").unlink()

    profile = _profile(home, workspace=proj)
    with patch.object(Path, "home", return_value=home):
        listed = SandboxPolicy(project_root=proj, config={"enabled": True}).list_profiles()

    assert profile.text == _BUILTIN_PROFILES["workspace-write"]
    assert "(allow default)" not in profile.as_args()[-1]
    assert "readonly" in listed                         # still there, from the snapshot
    r = _run(profile, f"echo x > '{home}/.agentao/sandbox.json'; echo write=$?")
    assert "write=1" in r.stdout


@MACOS_ONLY
def test_a_venv_holding_agentao_can_be_deleted_but_the_user_config_cannot(tmp_path, monkeypatch):
    home = _home(tmp_path)
    proj = tmp_path / "proj"
    venv = proj / ".venv"
    _fake_install(monkeypatch, venv / "lib" / "site-packages" / "agentao" / "sandbox" / "profiles")
    profile = _profile(home, workspace=proj)

    r = _run(profile, f"rm -rf '{venv}'; echo rm=$?; "
                      f"echo x > '{home}/.agentao/sandbox.json'; echo write=$?")

    assert "rm=0" in r.stdout and not venv.exists()
    assert "write=1" in r.stdout
