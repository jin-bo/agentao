"""``/sessions resume`` ends the "2 = allow all this session" grant.

Answering "2" at a confirmation prompt switches the session to full-access
without saving it. ``/clear`` and ``/new`` end that grant, but a resume kept
it, so the resumed conversation ran every tool without asking. A successful
resume now restores the saved mode (the grant was never saved); a failed one
changes nothing; ``settings.json`` is never written.
"""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path
from unittest.mock import patch

import pytest

from agentao.embedding import build_from_environment, save_session
from agentao.permissions import PermissionMode
from agentao.runtime.permission_mode import apply_permission_mode


@pytest.fixture
def cli(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    with patch("agentao.cli.app.safe_load_dotenv"), \
            patch("agentao.cli.subcommands._load_and_register_plugins"):
        from agentao.cli import AgentaoCLI
        cli = AgentaoCLI(agent_factory=partial(
            build_from_environment, working_directory=tmp_path / "proj"))
    yield cli
    cli.agent.close()


def _grant_all(cli) -> None:
    """What answering "2" at the confirmation prompt does (``cli/transport.py``)."""
    cli.allow_all_tools = True
    cli.current_mode = PermissionMode.FULL_ACCESS
    apply_permission_mode(cli.agent, PermissionMode.FULL_ACCESS, cause="cli-allow-all")
    cli.readonly_mode = False


def _saved(cli) -> Path:
    save_session([{"role": "user", "content": "from yesterday"}], "m",
                 session_id="older", project_root=cli.agent.working_directory)
    return Path(cli.agent.working_directory) / ".agentao" / "settings.json"


def test_a_resume_ends_the_grant_and_restores_the_saved_mode(cli):
    from agentao.cli.commands import resume_session

    settings = _saved(cli)
    cli._apply_mode(PermissionMode.READ_ONLY)              # saved by the user
    before = settings.read_text()
    _grant_all(cli)

    resume_session(cli, "older")

    assert cli.allow_all_tools is False
    assert cli.current_mode == PermissionMode.READ_ONLY
    assert cli.agent.permission_engine.active_mode == PermissionMode.READ_ONLY
    assert cli.agent.tool_runner.readonly_active() is True
    assert cli.readonly_mode is True
    assert settings.read_text() == before                  # nothing written


def test_a_failed_resume_keeps_the_grant(cli):
    from agentao.cli.commands import resume_session

    _saved(cli)
    _grant_all(cli)

    resume_session(cli, "no-such-session")

    assert cli.allow_all_tools is True
    assert cli.agent.permission_engine.active_mode == PermissionMode.FULL_ACCESS


def test_a_resume_without_a_grant_leaves_the_mode_alone(cli):
    from agentao.cli.commands import resume_session

    _saved(cli)
    cli._apply_mode(PermissionMode.FULL_ACCESS)             # chosen with /mode, and saved

    resume_session(cli, "older")

    assert cli.current_mode == PermissionMode.FULL_ACCESS
    assert cli.agent.permission_engine.active_mode == PermissionMode.FULL_ACCESS


def test_a_grant_held_for_plan_exit_is_dropped_too(cli):
    from agentao.cli.commands import resume_session

    settings = _saved(cli)
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"mode": "workspace-write"}))
    _grant_all(cli)
    cli._plan_controller.enter(cli.current_mode, cli.allow_all_tools)
    cli.allow_all_tools = False

    resume_session(cli, "older")
    restored, restore_allow_all = cli._plan_controller.exit_plan_mode()

    assert restored == PermissionMode.WORKSPACE_WRITE
    assert restore_allow_all is False


def test_the_mode_restored_is_the_one_in_force_when_2_was_pressed(cli):
    """Not a re-read of settings.json, which may not hold it (a save that
    failed, another process's edit): restoring that could loosen the mode."""
    from agentao.cli.commands import resume_session

    settings = _saved(cli)
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"mode": "workspace-write"}))
    cli.current_mode = PermissionMode.READ_ONLY            # in force, not saved
    apply_permission_mode(cli.agent, PermissionMode.READ_ONLY, cause="cli")
    _press_2(cli)

    resume_session(cli, "older")

    assert cli.current_mode == PermissionMode.READ_ONLY
    assert cli.agent.permission_engine.active_mode == PermissionMode.READ_ONLY


def test_a_grant_given_during_plan_is_dropped_and_plan_stays(cli):
    """"2" pressed inside /plan moves the engine off the PLAN preset; resume
    puts it back, and the grant held for plan exit goes too."""
    from agentao.cli.commands import resume_session

    settings = _saved(cli)
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"mode": "workspace-write"}))
    cli.current_mode = PermissionMode.READ_ONLY            # in force, not saved
    apply_permission_mode(cli.agent, PermissionMode.READ_ONLY, cause="cli")
    _press_2(cli)
    cli._plan_controller.enter(cli.current_mode, cli.allow_all_tools)
    cli.allow_all_tools = False
    _press_2(cli)                                          # again, inside plan

    resume_session(cli, "older")

    assert cli.allow_all_tools is False
    assert cli.agent.permission_engine.active_mode == PermissionMode.PLAN
    restored, restore_allow_all = cli._plan_controller.exit_plan_mode()
    # The mode before the first "2", not the saved one and not full-access.
    assert restored == PermissionMode.READ_ONLY and restore_allow_all is False


def _press_2(cli) -> None:
    """Press "2" through the real prompt in ``cli/transport.py``."""
    from agentao.cli.transport import confirm_tool_execution

    with patch("agentao.cli.transport.readchar.readkey", return_value="2"):
        assert confirm_tool_execution(cli, "write_file", "d", {"file_path": "x"}) is True
