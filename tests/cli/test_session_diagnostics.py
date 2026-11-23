"""``/permission`` sources and ``/plugins hooks`` report what the session loaded (#668).

One layered fixture, built on disk before a real ``AgentaoCLI`` starts:

- the project ``settings.json`` picks the mode,
- the user ``permissions.json`` loads,
- a project ``permissions.json`` exists, which the built-in loader does not use,
- plugin ``good`` loads one ``PreToolUse`` hook and one ``SessionStart`` hook,
  and one entry under an unsupported event is skipped,
- plugin ``broken`` fails skill resolution, so its ``SessionEnd`` hook is absent.

Then the files change on disk, and both views must still describe the startup
state. Neither view may dispatch a hook.
"""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agentao.cli.commands import handle_permission_command, handle_plan_command
from agentao.cli.subcommands import handle_plugins_command
from agentao.embedding import build_from_environment
from agentao.permissions import PermissionMode

pytestmark = pytest.mark.usefixtures("isolated_home", "isolated_cwd")

_SECRET_COMMAND = "echo HOOK-COMMAND-TEXT"


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _layout(tmp_path: Path, *, mode="read-only") -> SimpleNamespace:
    home = tmp_path / "home"
    project = tmp_path
    user_rules = home / ".agentao" / "permissions.json"
    _write(user_rules, {"rules": [{"tool": "read_file", "action": "allow"}]})
    project_rules = project / ".agentao" / "permissions.json"
    _write(project_rules, {"rules": [{"tool": "*", "action": "allow"}]})
    settings = project / ".agentao" / "settings.json"
    _write(settings, {"mode": mode})

    plugins = project / ".agentao" / "plugins" / "local"
    good = plugins / "good"
    _write(good / "plugin.json", {"name": "good"})
    _write(good / "hooks" / "hooks.json", {"hooks": {
        "PreToolUse": [{"matcher": "write_file",
                        "hooks": [{"type": "command", "command": _SECRET_COMMAND}]}],
        "SessionStart": [{"hooks": [{"type": "command", "command": "echo start"}]}],
        "NotARealEvent": [{"hooks": [{"type": "command", "command": "echo x"}]}],
    }})
    broken = plugins / "broken"
    _write(broken / "plugin.json", {"name": "broken", "commands": {"x": {"description": "d"}}})
    _write(broken / "hooks" / "hooks.json", {"hooks": {
        "SessionEnd": [{"hooks": [{"type": "command", "command": "echo end"}]}],
    }})
    return SimpleNamespace(
        user_rules=user_rules, project_rules=project_rules, settings=settings,
        good_hooks=good / "hooks" / "hooks.json",
    )


def _start_cli(tmp_path: Path):
    with patch("agentao.cli.app.safe_load_dotenv"):
        from agentao.cli import AgentaoCLI
        return AgentaoCLI(agent_factory=partial(build_from_environment, working_directory=tmp_path))


@pytest.fixture
def printed():
    lines: list = []
    fake = SimpleNamespace(print=lambda *a, **k: lines.append(" ".join(str(x) for x in a)))
    with patch("agentao.cli.commands.permission.console", fake), \
            patch("agentao.cli.subcommands.console", fake):
        yield lines


def _permission_text(cli, printed) -> str:
    printed.clear()
    handle_permission_command(cli, "")
    return "\n".join(printed)


def _hooks_text(cli, printed) -> str:
    printed.clear()
    handle_plugins_command(cli, "hooks")
    return "\n".join(printed)


def test_layered_startup_state_is_reported(tmp_path, printed):
    files = _layout(tmp_path)
    cli = _start_cli(tmp_path)

    perm = _permission_text(cli, printed)
    assert "Mode: read-only" in perm
    assert f"Source: project settings ({files.settings})" in perm
    assert f"user:{files.user_rules}" in perm
    assert "Found at startup, not used by the built-in loader:" in perm
    assert f"{files.project_rules}: project-scope permission file" in perm

    hooks = _hooks_text(cli, printed)
    assert "PreToolUse  matcher=write_file  type=command  plugin=good" in hooks
    assert "SessionStart  matcher=*  type=command  plugin=good" in hooks
    assert "Unsupported hook event 'NotARealEvent'" in hooks
    assert "Plugins not loaded (their hooks are absent):" in hooks
    assert "broken (" in hooks
    assert "Command 'x' has neither 'source' nor 'content'" in hooks
    # The broken plugin's hook is absent from what the runtime dispatches.
    assert {r.event for r in cli.agent._plugin_hook_rules} == {"PreToolUse", "SessionStart"}
    # Metadata only: no hook command text.
    assert "HOOK-COMMAND-TEXT" not in hooks


def test_views_ignore_files_changed_after_startup_and_fire_no_hook(tmp_path, printed):
    files = _layout(tmp_path)
    cli = _start_cli(tmp_path)
    perm_before = _permission_text(cli, printed)
    hooks_before = _hooks_text(cli, printed)

    _write(files.settings, {"mode": "full-access"})
    files.project_rules.unlink()
    files.user_rules.unlink()
    _write(files.good_hooks, {"hooks": {}})

    with patch("agentao.plugins.hooks.PluginHookDispatcher") as dispatcher:
        assert _permission_text(cli, printed) == perm_before
        assert _hooks_text(cli, printed) == hooks_before
        dispatcher.assert_not_called()
        # The probe can see a dispatch: SessionStart does construct one.
        cli.on_session_start(source="startup")
        dispatcher.assert_called()


@pytest.mark.parametrize(("settings", "reason"), [
    ({"mode": "plan"}, "Plan mode in {path} was ignored. Enter it with /plan."),
    ({"mode": "bogus"}, 'invalid config value \'bogus\' for "mode" in {path}'),
    ({"mode": 3}, 'invalid config value 3 for "mode" in {path}'),
    ({}, None),
])
def test_startup_uses_the_default_for_unusable_or_missing_modes(tmp_path, printed, settings, reason):
    path = tmp_path / ".agentao" / "settings.json"
    _write(path, settings)
    before = path.read_bytes()
    cli = _start_cli(tmp_path)

    perm = _permission_text(cli, printed)
    assert cli.current_mode is PermissionMode.WORKSPACE_WRITE
    assert cli.agent.permission_engine.active_mode is PermissionMode.WORKSPACE_WRITE
    assert not cli._plan_session.is_active
    assert path.read_bytes() == before
    assert "Mode: workspace-write" in perm
    assert "Source: default" in perm
    if reason is None:
        assert "Fallback reason" not in perm
    else:
        assert f"Fallback reason: {reason.format(path=path)}" in perm


def test_plan_clear_restores_and_persists_workspace_write_after_startup_fallback(tmp_path):
    path = tmp_path / ".agentao" / "settings.json"
    replay = {"enabled": False}
    _write(path, {"mode": "plan", "replay": replay})
    cli = _start_cli(tmp_path)

    handle_plan_command(cli, "")
    assert cli._plan_session.is_active
    assert cli.agent.permission_engine.active_mode is PermissionMode.PLAN
    handle_plan_command(cli, "clear")

    assert not cli._plan_session.is_active
    assert cli.current_mode is PermissionMode.WORKSPACE_WRITE
    assert cli.agent.permission_engine.active_mode is PermissionMode.WORKSPACE_WRITE
    assert json.loads(path.read_text(encoding="utf-8")) == {"mode": "workspace-write", "replay": replay}


def test_save_settings_skips_plan_mode(tmp_path):
    path = tmp_path / ".agentao" / "settings.json"
    _write(path, {"mode": "read-only"})
    before = path.read_bytes()
    cli = _start_cli(tmp_path)

    cli.current_mode = PermissionMode.PLAN
    cli._save_settings()

    assert path.read_bytes() == before


def test_in_session_switches_name_their_entry_path(tmp_path, printed):
    _layout(tmp_path, mode="workspace-write")
    cli = _start_cli(tmp_path)

    cli._apply_mode(PermissionMode.READ_ONLY)
    assert "Source: /mode in this session" in _permission_text(cli, printed)

    # Entering plan mode is recorded, not a bare ``engine.set_mode``.
    events = []
    real_emit = cli.emit
    with patch.object(cli, "emit", side_effect=lambda e: (events.append(e), real_emit(e))):
        cli._plan_controller.enter(cli.current_mode, cli.allow_all_tools)
    assert [e.data.get("cause") for e in events if e.type.name == "PERMISSION_MODE_CHANGED"] == ["cli-plan"]
    perm = _permission_text(cli, printed)
    assert "Mode: plan" in perm
    assert "Source: /plan (plan mode)" in perm

    cli._plan_controller.exit_plan_mode()
    perm = _permission_text(cli, printed)
    assert "Mode: read-only" in perm
    assert "Source: leaving plan mode" in perm

    with patch("agentao.cli.transport.readchar.readkey", return_value="2"):
        cli._apply_mode(PermissionMode.WORKSPACE_WRITE)
        assert cli.confirm_tool_execution("write_file", "d", {"path": "x"}) is True
    perm = _permission_text(cli, printed)
    assert "Mode: full-access" in perm
    assert 'Source: temporary full-access grant (answered "2"' in perm


def test_plugins_subcommands(tmp_path, printed):
    _layout(tmp_path)
    cli = _start_cli(tmp_path)
    with patch("agentao.cli.subcommands._handle_plugins_interactive") as listing:
        handle_plugins_command(cli, "")
        handle_plugins_command(cli, "list")
    assert listing.call_count == 2
    printed.clear()
    handle_plugins_command(cli, "bogus")
    assert any("Unknown subcommand: bogus" in line for line in printed)


def test_no_plugins_still_records_a_report(tmp_path, printed):
    cli = _start_cli(tmp_path)
    hooks = _hooks_text(cli, printed)
    assert "Hooks loaded at startup: 0 rule(s)" in hooks
    assert "(none)" in hooks


@pytest.mark.parametrize("handler_name", ["handle_clear_command", "handle_new_command"])
def test_a_reset_is_named_even_without_a_mode_change(tmp_path, printed, handler_name):
    """Already in workspace-write, a reset emits no mode event; the source still moves."""
    from agentao.cli import commands

    _layout(tmp_path, mode="workspace-write")
    cli = _start_cli(tmp_path)
    assert cli.current_mode is PermissionMode.WORKSPACE_WRITE
    events = []
    real_emit = cli.emit
    with patch.object(cli, "emit", side_effect=lambda e: (events.append(e), real_emit(e))), \
            patch("agentao.cli._utils.console"), patch("agentao.cli.commands.reset.console"):
        getattr(commands, handler_name)(cli, "")
    assert not [e for e in events if e.type.name == "PERMISSION_MODE_CHANGED"]
    perm = _permission_text(cli, printed)
    assert "Mode: workspace-write" in perm
    assert "Source: session reset (/clear or /new)" in perm


def test_project_rule_file_at_home_is_the_user_file_not_ignored(tmp_path):
    """Run from the home directory, ``<root>/.agentao`` is the user root."""
    from agentao.embedding.permission_loader import ignored_project_rule_file

    (tmp_path / ".agentao").mkdir()
    (tmp_path / ".agentao" / "permissions.json").write_text("{}", encoding="utf-8")
    assert ignored_project_rule_file(tmp_path, tmp_path / ".agentao") is None
    other = tmp_path / "elsewhere"
    assert ignored_project_rule_file(tmp_path, other) == (
        tmp_path / ".agentao" / "permissions.json"
    )
