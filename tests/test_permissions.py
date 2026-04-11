"""Tests for PermissionEngine rule evaluation and mode switching."""

import json
from pathlib import Path

import pytest

from agentao.permissions import PermissionDecision, PermissionEngine, PermissionMode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _engine(tmp_path, monkeypatch, project_rules=None, user_rules=None):
    """Build a PermissionEngine with optional project/user JSON rules in tmp_path."""
    # Redirect cwd and home so _load_rules reads from tmp_path only
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))

    if project_rules is not None:
        cfg = tmp_path / ".agentao"
        cfg.mkdir(exist_ok=True)
        (cfg / "permissions.json").write_text(
            json.dumps({"rules": project_rules}), encoding="utf-8",
        )
    if user_rules is not None:
        home_cfg = tmp_path / "home" / ".agentao"
        home_cfg.mkdir(parents=True, exist_ok=True)
        (home_cfg / "permissions.json").write_text(
            json.dumps({"rules": user_rules}), encoding="utf-8",
        )

    return PermissionEngine()


def allow(tool, **kwargs):
    return {"tool": tool, "action": "allow", **kwargs}

def deny(tool, **kwargs):
    return {"tool": tool, "action": "deny", **kwargs}

def ask(tool, **kwargs):
    return {"tool": tool, "action": "ask", **kwargs}


# ---------------------------------------------------------------------------
# Defaults and mode switching
# ---------------------------------------------------------------------------

def test_default_mode_is_workspace_write(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    assert e.active_mode == PermissionMode.WORKSPACE_WRITE


def test_set_mode_switches_preset(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    e.set_mode(PermissionMode.READ_ONLY)
    assert e.active_mode == PermissionMode.READ_ONLY


# ---------------------------------------------------------------------------
# READ_ONLY mode
# ---------------------------------------------------------------------------

def test_read_only_allows_read_file(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    e.set_mode(PermissionMode.READ_ONLY)
    # READ_ONLY preset has no rules; ToolRunner enforces via is_read_only
    # decide() should return None (no preset rules) for any tool
    result = e.decide("read_file", {"path": "foo.py"})
    assert result is None


def test_read_only_returns_none_for_write_file(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    e.set_mode(PermissionMode.READ_ONLY)
    result = e.decide("write_file", {"path": "foo.py", "content": "x"})
    assert result is None  # enforcement is in ToolRunner


# ---------------------------------------------------------------------------
# WORKSPACE_WRITE mode
# ---------------------------------------------------------------------------

def test_workspace_write_allows_write_file(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("write_file", {"path": "x"}) == PermissionDecision.ALLOW


def test_workspace_write_allows_replace(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("replace", {"path": "x", "old": "a", "new": "b"}) == PermissionDecision.ALLOW


def test_workspace_write_allows_safe_shell_git_status(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("run_shell_command", {"command": "git status"}) == PermissionDecision.ALLOW


def test_workspace_write_allows_safe_shell_ls(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("run_shell_command", {"command": "ls -la"}) == PermissionDecision.ALLOW


def test_workspace_write_allows_safe_shell_cat(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("run_shell_command", {"command": "cat README.md"}) == PermissionDecision.ALLOW


def test_workspace_write_allows_safe_shell_grep(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("run_shell_command", {"command": "grep -r foo src/"}) == PermissionDecision.ALLOW


def test_workspace_write_denies_rm_rf(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("run_shell_command", {"command": "rm -rf /"}) == PermissionDecision.DENY


def test_workspace_write_denies_sudo(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("run_shell_command", {"command": "sudo apt-get install x"}) == PermissionDecision.DENY


def test_workspace_write_asks_unknown_shell(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("run_shell_command", {"command": "curl https://example.com"}) == PermissionDecision.ASK


def test_workspace_write_asks_web_fetch(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("web_fetch", {"url": "https://example.com"}) == PermissionDecision.ASK


def test_workspace_write_asks_google_web_search(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("google_web_search", {"query": "python"}) == PermissionDecision.ASK


def test_workspace_write_unknown_tool_returns_none(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("unknown_tool_xyz", {}) is None


# ---------------------------------------------------------------------------
# FULL_ACCESS mode
# ---------------------------------------------------------------------------

def test_full_access_allows_write_file(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    e.set_mode(PermissionMode.FULL_ACCESS)
    assert e.decide("write_file", {"path": "x"}) == PermissionDecision.ALLOW


def test_full_access_allows_shell(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    e.set_mode(PermissionMode.FULL_ACCESS)
    assert e.decide("run_shell_command", {"command": "rm -rf /"}) == PermissionDecision.ALLOW


def test_full_access_allows_web_fetch(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    e.set_mode(PermissionMode.FULL_ACCESS)
    assert e.decide("web_fetch", {"url": "https://x.com"}) == PermissionDecision.ALLOW


# ---------------------------------------------------------------------------
# PLAN mode
# ---------------------------------------------------------------------------

def test_plan_mode_allows_plan_save(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    e.set_mode(PermissionMode.PLAN)
    assert e.decide("plan_save", {"content": "# Plan"}) == PermissionDecision.ALLOW


def test_plan_mode_allows_plan_finalize(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    e.set_mode(PermissionMode.PLAN)
    assert e.decide("plan_finalize", {"draft_id": "20250101"}) == PermissionDecision.ALLOW


def test_plan_mode_denies_write_file(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    e.set_mode(PermissionMode.PLAN)
    assert e.decide("write_file", {"path": "x"}) == PermissionDecision.DENY


def test_plan_mode_denies_replace(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    e.set_mode(PermissionMode.PLAN)
    assert e.decide("replace", {"path": "x", "old": "a", "new": "b"}) == PermissionDecision.DENY


def test_plan_mode_denies_save_memory(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    e.set_mode(PermissionMode.PLAN)
    assert e.decide("save_memory", {"key": "k", "value": "v"}) == PermissionDecision.DENY


def test_plan_mode_denies_todo_write(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    e.set_mode(PermissionMode.PLAN)
    assert e.decide("todo_write", {"todos": []}) == PermissionDecision.DENY


def test_plan_mode_allows_read_only_shell(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    e.set_mode(PermissionMode.PLAN)
    assert e.decide("run_shell_command", {"command": "git diff HEAD"}) == PermissionDecision.ALLOW


def test_plan_mode_denies_arbitrary_shell(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    e.set_mode(PermissionMode.PLAN)
    assert e.decide("run_shell_command", {"command": "pip install x"}) == PermissionDecision.DENY


def test_plan_mode_asks_web_fetch(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    e.set_mode(PermissionMode.PLAN)
    assert e.decide("web_fetch", {"url": "https://x.com"}) == PermissionDecision.ASK


# ---------------------------------------------------------------------------
# Custom rules
# ---------------------------------------------------------------------------

def test_custom_project_rule_allow_overrides_preset(tmp_path, monkeypatch):
    # In workspace-write mode, web_fetch would ASK; custom allow overrides
    e = _engine(tmp_path, monkeypatch, project_rules=[allow("web_fetch")])
    assert e.decide("web_fetch", {"url": "https://x.com"}) == PermissionDecision.ALLOW


def test_custom_project_rule_deny_overrides_preset(tmp_path, monkeypatch):
    # write_file is allowed in workspace-write; custom deny overrides
    e = _engine(tmp_path, monkeypatch, project_rules=[deny("write_file")])
    assert e.decide("write_file", {"path": "x"}) == PermissionDecision.DENY


def test_custom_user_rule_evaluated_after_project(tmp_path, monkeypatch):
    # project allow wins over user deny for same tool
    e = _engine(
        tmp_path, monkeypatch,
        project_rules=[allow("web_fetch")],
        user_rules=[deny("web_fetch")],
    )
    assert e.decide("web_fetch", {"url": "x"}) == PermissionDecision.ALLOW


def test_user_rule_applies_when_no_project_rule(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, user_rules=[allow("web_fetch")])
    assert e.decide("web_fetch", {"url": "x"}) == PermissionDecision.ALLOW


def test_custom_rule_loaded_from_project_config(tmp_path, monkeypatch):
    rules = [allow("google_web_search")]
    e = _engine(tmp_path, monkeypatch, project_rules=rules)
    assert e.decide("google_web_search", {"query": "test"}) == PermissionDecision.ALLOW


# ---------------------------------------------------------------------------
# Arg-level rule matching
# ---------------------------------------------------------------------------

def test_arg_level_rule_matches_regex(tmp_path, monkeypatch):
    rules = [{"tool": "run_shell_command", "args": {"command": r"^pytest"}, "action": "allow"}]
    e = _engine(tmp_path, monkeypatch, project_rules=rules)
    assert e.decide("run_shell_command", {"command": "pytest tests/ -q"}) == PermissionDecision.ALLOW


def test_arg_level_rule_no_match_falls_through(tmp_path, monkeypatch):
    rules = [{"tool": "run_shell_command", "args": {"command": r"^pytest"}, "action": "allow"}]
    e = _engine(tmp_path, monkeypatch, project_rules=rules)
    # "make test" doesn't match "^pytest" so falls to preset (workspace-write asks)
    assert e.decide("run_shell_command", {"command": "make test"}) == PermissionDecision.ASK


def test_wildcard_tool_rule(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, project_rules=[{"tool": "*", "action": "allow"}])
    assert e.decide("anything_at_all", {}) == PermissionDecision.ALLOW


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_invalid_json_config_graceful_fallback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    cfg = tmp_path / ".agentao"
    cfg.mkdir()
    (cfg / "permissions.json").write_text("not valid json", encoding="utf-8")
    e = PermissionEngine()  # should not raise
    assert e.rules == []


def test_missing_config_file_returns_no_custom_rules(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)  # no files written
    assert e.rules == []


# ---------------------------------------------------------------------------
# get_rules_display
# ---------------------------------------------------------------------------

def test_get_rules_display_contains_mode(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    display = e.get_rules_display()
    assert "workspace-write" in display


def test_get_rules_display_custom_rule_shown(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, project_rules=[allow("web_fetch")])
    display = e.get_rules_display()
    assert "web_fetch" in display
    assert "ALLOW" in display
