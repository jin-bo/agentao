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


# ---------------------------------------------------------------------------
# Domain-based web_fetch permission rules
# ---------------------------------------------------------------------------

def test_domain_allowlist_suffix_match(tmp_path, monkeypatch):
    """URLs matching allowlist domains are auto-allowed."""
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("web_fetch", {"url": "https://api.github.com/repos/foo"}) == PermissionDecision.ALLOW


def test_domain_allowlist_exact_root_match(tmp_path, monkeypatch):
    """'.github.com' also matches 'github.com' itself (no subdomain)."""
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("web_fetch", {"url": "https://github.com/foo/bar"}) == PermissionDecision.ALLOW


def test_domain_allowlist_exact_entry(tmp_path, monkeypatch):
    """Exact domain entry (no leading dot) matches only that exact host."""
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("web_fetch", {"url": "https://r.jina.ai/https://example.com"}) == PermissionDecision.ALLOW


def test_domain_allowlist_no_false_suffix(tmp_path, monkeypatch):
    """'.github.com' should NOT match 'notgithub.com'."""
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("web_fetch", {"url": "https://notgithub.com/foo"}) == PermissionDecision.ASK


def test_domain_blocklist_localhost(tmp_path, monkeypatch):
    """Localhost is auto-denied."""
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("web_fetch", {"url": "http://localhost:8080/api"}) == PermissionDecision.DENY


def test_domain_blocklist_loopback_ip(tmp_path, monkeypatch):
    """127.0.0.1 is auto-denied."""
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("web_fetch", {"url": "http://127.0.0.1/secret"}) == PermissionDecision.DENY


def test_domain_blocklist_metadata_endpoint(tmp_path, monkeypatch):
    """Cloud metadata IP is auto-denied (SSRF protection)."""
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("web_fetch", {"url": "http://169.254.169.254/latest/meta-data"}) == PermissionDecision.DENY


def test_domain_blocklist_internal_suffix(tmp_path, monkeypatch):
    """Domains ending in .internal are auto-denied."""
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("web_fetch", {"url": "https://api.corp.internal/data"}) == PermissionDecision.DENY


def test_domain_blocklist_zero_ip(tmp_path, monkeypatch):
    """0.0.0.0 is auto-denied."""
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("web_fetch", {"url": "http://0.0.0.0:5000/"}) == PermissionDecision.DENY


def test_domain_fallthrough_to_ask(tmp_path, monkeypatch):
    """URLs not in allowlist or blocklist get ASK."""
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("web_fetch", {"url": "https://example.com/page"}) == PermissionDecision.ASK


def test_domain_case_insensitive(tmp_path, monkeypatch):
    """Domain matching is case-insensitive."""
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("web_fetch", {"url": "HTTPS://GITHUB.COM/foo"}) == PermissionDecision.ALLOW


def test_domain_missing_scheme(tmp_path, monkeypatch):
    """URL without scheme is handled gracefully."""
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("web_fetch", {"url": "docs.python.org/3/library/os.html"}) == PermissionDecision.ALLOW


def test_domain_port_stripping(tmp_path, monkeypatch):
    """Port numbers don't interfere with domain matching."""
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("web_fetch", {"url": "http://localhost:3000/health"}) == PermissionDecision.DENY


def test_domain_ip_exact_match_no_suffix(tmp_path, monkeypatch):
    """IP '127.0.0.1' does not match via suffix (e.g. '1.127.0.0.1' should not match)."""
    e = _engine(tmp_path, monkeypatch)
    # This domain is not in any list, so should fall through to ask
    assert e.decide("web_fetch", {"url": "http://1.127.0.0.1/"}) == PermissionDecision.ASK


def test_domain_userinfo_bypass_attempt(tmp_path, monkeypatch):
    """URL with userinfo (evil.com@github.com) resolves to the actual host."""
    e = _engine(tmp_path, monkeypatch)
    # urlparse treats github.com as the hostname here
    assert e.decide("web_fetch", {"url": "http://evil.com@github.com/path"}) == PermissionDecision.ALLOW


def test_domain_custom_allowlist_overrides_preset_blocklist(tmp_path, monkeypatch):
    """Project-level custom domain allowlist can override preset blocklist."""
    rules = [{"tool": "web_fetch", "domain": {"allowlist": ["localhost"]}, "action": "allow"}]
    e = _engine(tmp_path, monkeypatch, project_rules=rules)
    # Custom rule evaluated first in workspace-write mode
    assert e.decide("web_fetch", {"url": "http://localhost:8080/"}) == PermissionDecision.ALLOW


def test_domain_plan_mode_allowlist(tmp_path, monkeypatch):
    """Plan mode also has domain allowlist for web_fetch."""
    e = _engine(tmp_path, monkeypatch)
    e.set_mode(PermissionMode.PLAN)
    assert e.decide("web_fetch", {"url": "https://docs.python.org/3/"}) == PermissionDecision.ALLOW


def test_domain_plan_mode_blocklist(tmp_path, monkeypatch):
    """Plan mode also has domain blocklist for web_fetch."""
    e = _engine(tmp_path, monkeypatch)
    e.set_mode(PermissionMode.PLAN)
    assert e.decide("web_fetch", {"url": "http://127.0.0.1/admin"}) == PermissionDecision.DENY


def test_domain_display_shows_allowlist(tmp_path, monkeypatch):
    """get_rules_display shows domain allowlist entries."""
    rules = [{"tool": "web_fetch", "domain": {"allowlist": [".example.com"]}, "action": "allow"}]
    e = _engine(tmp_path, monkeypatch, project_rules=rules)
    display = e.get_rules_display()
    assert "domain allowlist" in display
    assert ".example.com" in display
