"""Tests for PermissionEngine rule evaluation and mode switching."""

import json
from pathlib import Path

import pytest

from agentao.permissions import PermissionDecision, PermissionEngine, PermissionMode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _engine(tmp_path, monkeypatch, project_rules=None, user_rules=None):
    """Build a PermissionEngine with optional user JSON rules in tmp_path.

    ``project_rules`` is accepted for legacy parity but written to a
    file the engine deliberately ignores (see ``permissions.py``); it
    is preserved here so collision/precedence tests can still assert
    that a stray project file does not leak into the rule set.
    """
    user_root = tmp_path / "home" / ".agentao"

    if project_rules is not None:
        cfg = tmp_path / ".agentao"
        cfg.mkdir(exist_ok=True)
        (cfg / "permissions.json").write_text(
            json.dumps({"rules": project_rules}), encoding="utf-8",
        )
    if user_rules is not None:
        user_root.mkdir(parents=True, exist_ok=True)
        (user_root / "permissions.json").write_text(
            json.dumps({"rules": user_rules}), encoding="utf-8",
        )

    return PermissionEngine(project_root=tmp_path, user_root=user_root)


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


def test_workspace_write_asks_web_search(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("web_search", {"query": "python"}) == PermissionDecision.ASK


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


def test_full_access_allows_recoverable_shell(tmp_path, monkeypatch):
    """full-access ALLOWs recoverable-but-costly shell ops (those stay
    outside hardline; the floor only blocks unrecoverable operations).
    """
    e = _engine(tmp_path, monkeypatch)
    e.set_mode(PermissionMode.FULL_ACCESS)
    # rm -rf against a non-system path is recoverable-from-backup; the
    # hardline only catches /, system roots, and home dirs.
    assert e.decide("run_shell_command", {"command": "rm -rf /tmp/scratch"}) == PermissionDecision.ALLOW
    assert e.decide("run_shell_command", {"command": "git reset --hard HEAD~5"}) == PermissionDecision.ALLOW
    assert e.decide("run_shell_command", {"command": "pip install evil-package"}) == PermissionDecision.ALLOW


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

def test_custom_user_rule_allow_overrides_preset(tmp_path, monkeypatch):
    # In workspace-write mode, web_fetch would ASK; custom allow overrides
    e = _engine(tmp_path, monkeypatch, user_rules=[allow("web_fetch")])
    assert e.decide("web_fetch", {"url": "https://x.com"}) == PermissionDecision.ALLOW


def test_custom_user_rule_deny_overrides_preset(tmp_path, monkeypatch):
    # write_file is allowed in workspace-write; custom deny overrides
    e = _engine(tmp_path, monkeypatch, user_rules=[deny("write_file")])
    assert e.decide("write_file", {"path": "x"}) == PermissionDecision.DENY


def test_project_rules_are_ignored_user_wins(tmp_path, monkeypatch, caplog):
    """A stray project-scope rule must not influence decisions; only the
    user rule applies, and the engine logs a warning that the project
    file was ignored. This is the load-bearing invariant from the
    config-trust-boundary change.
    """
    import logging

    with caplog.at_level(logging.WARNING, logger="agentao.permissions"):
        e = _engine(
            tmp_path, monkeypatch,
            project_rules=[allow("web_fetch")],   # would have allowed
            user_rules=[deny("web_fetch")],        # actually applies
        )
    assert e.decide("web_fetch", {"url": "x"}) == PermissionDecision.DENY
    assert any(
        "project-scope permission rules are no longer honored" in rec.getMessage()
        for rec in caplog.records
    )


def test_user_rule_applies_when_no_project_rule(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, user_rules=[allow("web_fetch")])
    assert e.decide("web_fetch", {"url": "x"}) == PermissionDecision.ALLOW


def test_custom_rule_loaded_from_user_config(tmp_path, monkeypatch):
    rules = [allow("web_search")]
    e = _engine(tmp_path, monkeypatch, user_rules=rules)
    assert e.decide("web_search", {"query": "test"}) == PermissionDecision.ALLOW


def test_project_rules_ignored_falls_back_to_preset(tmp_path, monkeypatch):
    """Project-only rule file: rule is ignored, preset semantics apply."""
    e = _engine(tmp_path, monkeypatch, project_rules=[allow("web_fetch")])
    # web_fetch in workspace-write preset is ASK (not ALLOW from the project rule)
    assert e.decide("web_fetch", {"url": "https://x.com"}) == PermissionDecision.ASK
    assert e.rules == []


# ---------------------------------------------------------------------------
# Arg-level rule matching
# ---------------------------------------------------------------------------

def test_arg_level_rule_matches_regex(tmp_path, monkeypatch):
    rules = [{"tool": "run_shell_command", "args": {"command": r"^pytest"}, "action": "allow"}]
    e = _engine(tmp_path, monkeypatch, user_rules=rules)
    assert e.decide("run_shell_command", {"command": "pytest tests/ -q"}) == PermissionDecision.ALLOW


def test_arg_level_rule_no_match_falls_through(tmp_path, monkeypatch):
    rules = [{"tool": "run_shell_command", "args": {"command": r"^pytest"}, "action": "allow"}]
    e = _engine(tmp_path, monkeypatch, user_rules=rules)
    # "make test" doesn't match "^pytest" so falls to preset (workspace-write asks)
    assert e.decide("run_shell_command", {"command": "make test"}) == PermissionDecision.ASK


def test_wildcard_tool_rule(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, user_rules=[{"tool": "*", "action": "allow"}])
    assert e.decide("anything_at_all", {}) == PermissionDecision.ALLOW


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_invalid_json_user_config_graceful_fallback(tmp_path):
    user_root = tmp_path / "home" / ".agentao"
    user_root.mkdir(parents=True)
    (user_root / "permissions.json").write_text("not valid json", encoding="utf-8")
    e = PermissionEngine(project_root=tmp_path, user_root=user_root)  # should not raise
    assert e.rules == []


def test_stray_project_config_does_not_raise(tmp_path):
    """A stale project-scope file must not break startup."""
    cfg = tmp_path / ".agentao"
    cfg.mkdir()
    (cfg / "permissions.json").write_text("not valid json", encoding="utf-8")
    e = PermissionEngine(project_root=tmp_path)
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
    e = _engine(tmp_path, monkeypatch, user_rules=[allow("web_fetch")])
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
    """User-level custom domain allowlist can override preset blocklist."""
    rules = [{"tool": "web_fetch", "domain": {"allowlist": ["localhost"]}, "action": "allow"}]
    e = _engine(tmp_path, monkeypatch, user_rules=rules)
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
    e = _engine(tmp_path, monkeypatch, user_rules=rules)
    display = e.get_rules_display()
    assert "domain allowlist" in display
    assert ".example.com" in display


# ---------------------------------------------------------------------------
# Optional hardline layer
# ---------------------------------------------------------------------------


def test_full_access_default_blocks_hardline_commands(tmp_path):
    """Default construction has hardline ON — protects CLI users and
    embedded hosts that haven't thought through threat modeling.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "rm -rf /",
        "rm -rf /home/somebody",
        "rm -rf /etc/passwd",
        "rm -fr /usr/local",
        "rm -rf ~",
        "rm -rf $HOME",
        "shutdown -h now",
        "reboot",
        "halt",
        "poweroff",
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda",
        ":(){ :|:& };:",
        "kill -9 -1",
        "kill -HUP -1",
        "systemctl poweroff",
        "init 0",
        "telinit 6",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_full_access_with_hardline_off_honors_literal_contract(tmp_path):
    """Explicit opt-out preserves the literal full-access semantic for
    embedded hosts that take the policy responsibility themselves.
    """
    e = PermissionEngine(project_root=tmp_path, enable_hardline=False)
    e.set_mode(PermissionMode.FULL_ACCESS)
    assert e.decide("run_shell_command", {"command": "rm -rf /"}) == PermissionDecision.ALLOW


def test_reason_uses_policy_source_prefix(tmp_path):
    """``reason`` is a policy-source taxonomy, not a user-action discriminator.
    Hardline matches surface a ``hardline:`` prefix so hosts can build
    audit displays without parsing free-form text.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    detail = e.decide_detail("run_shell_command", {"command": "rm -rf /"})
    assert detail is not None
    assert detail.decision == PermissionDecision.DENY
    assert detail.reason.startswith("hardline:"), detail.reason


def test_reason_mode_preset_prefix(tmp_path, monkeypatch):
    """A preset-rule match surfaces ``mode-preset:<rule_tool>``."""
    e = _engine(tmp_path, monkeypatch)
    detail = e.decide_detail("write_file", {"path": "x"})
    assert detail is not None
    assert detail.decision == PermissionDecision.ALLOW
    assert detail.reason.startswith("mode-preset:"), detail.reason
    assert "write_file" in detail.reason


def test_reason_user_rule_prefix(tmp_path, monkeypatch):
    """A user-rule match surfaces ``user-rule:<rule_tool>``."""
    e = _engine(tmp_path, monkeypatch, user_rules=[deny("web_fetch")])
    detail = e.decide_detail("web_fetch", {"url": "https://x.com"})
    assert detail is not None
    assert detail.decision == PermissionDecision.DENY
    assert detail.reason.startswith("user-rule:"), detail.reason


def test_workspace_write_unaffected_by_hardline_flag(tmp_path):
    """Hardline runs *before* mode rules, but workspace-write's existing
    ``rm -rf|sudo|mkfs|dd if=`` deny rule still fires when the floor is
    disabled — proving the layers compose, not depend on each other.
    """
    e = PermissionEngine(project_root=tmp_path, enable_hardline=False)
    # Default mode is workspace-write; the mode preset still denies.
    assert e.decide("run_shell_command", {"command": "rm -rf /tmp/x"}) == PermissionDecision.DENY


def test_no_floor_ask_tier_exists(tmp_path):
    """Sentinel: literal full-access with hardline off must allow
    shell-RC writes. No hidden ASK tier may rematerialize — if a future
    change reintroduces a second floor that catches ``~/.bashrc``, this
    fails and forces the change to either justify itself or back out.
    """
    e = PermissionEngine(project_root=tmp_path, enable_hardline=False)
    e.set_mode(PermissionMode.FULL_ACCESS)
    assert e.decide(
        "run_shell_command",
        {"command": "echo X >> ~/.bashrc"},
    ) == PermissionDecision.ALLOW


def test_hardline_does_not_false_positive_on_innocent_text(tmp_path):
    """Benign mentions of trigger words inside echoed strings or argv must
    not match. The patterns are anchored at command position.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    benign = [
        'echo "reboot logs are great"',
        "grep -r shutdown ./docs",
        "git log --grep=poweroff",
        # rm -rf against an explicit non-system path
        "rm -rf /tmp/somefile",
    ]
    for cmd in benign:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d != PermissionDecision.DENY or "hardline" not in (
            e.decide_detail("run_shell_command", {"command": cmd}).reason
            or ""
        ), f"hardline false-positive on {cmd!r}"


def test_hardline_skips_non_shell_tools(tmp_path):
    """The floor only inspects ``run_shell_command``; other tools route
    normally through mode/preset/user-rule logic.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    # write_file with a sketchy path falls through to full-access ALLOW.
    assert e.decide("write_file", {"path": "/etc/passwd"}) == PermissionDecision.ALLOW


def test_hardline_via_sudo_wrapper(tmp_path):
    """``sudo rm -rf /`` is also caught — the hardline command-position
    anchor allows ``sudo`` / ``env`` wrappers explicitly.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    assert e.decide("run_shell_command", {"command": "sudo rm -rf /"}) == PermissionDecision.DENY


def test_hardline_via_command_separator(tmp_path):
    """Smuggling via ``;`` / ``&&`` / ``||`` still hits the floor."""
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "echo done; rm -rf /",
        "ls && shutdown -h now",
        "false || mkfs.ext4 /dev/sda",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, cmd


def test_hardline_blocks_quoted_rm_targets(tmp_path):
    """Quoted forms of dangerous rm targets must not bypass the floor.

    Shell users routinely write paths in quotes — ``rm -rf "$HOME"`` is
    actually the *correct* way to expand the variable safely. The
    hardline must catch these alongside the bare-literal forms or a
    prompt-injected wipe survives the floor in full-access mode.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        'rm -rf "$HOME"',
        "rm -rf '$HOME'",
        "rm -rf '~'",
        'rm -rf "~"',
        "rm -rf '/'",
        'rm -rf "/"',
        "rm -rf '/home/user'",
        'rm -rf "/etc/passwd"',
        'rm -rf "/usr/local"',
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_quoted_paths_no_false_positive(tmp_path):
    """Quote handling must not turn ``rm -rf "/tmp/scratch"`` into a hit.

    The hardline only covers system roots and home — quoting a
    non-system path keeps it recoverable, so the floor stays out of it.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        'rm -rf "/tmp/scratch"',
        "rm -rf '/tmp/scratch'",
        'rm -rf "./build"',
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ALLOW, f"{cmd!r} should NOT hit hardline"


def test_hardline_blocks_sudo_with_flags(tmp_path):
    """``sudo -n`` / ``sudo --`` / ``sudo -E`` wrappers must not bypass
    the floor. The original lookbehind only matched ``sudo `` literally,
    leaving these common forms in full-access mode as a free wipe.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "sudo -n rm -rf /",
        "sudo -- rm -rf /",
        "sudo -E rm -rf /home/somebody",
        "sudo -uroot rm -rf /etc",
        "sudo -n -- rm -rf /",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_env_with_assignments(tmp_path):
    """``env FOO=bar rm -rf /`` and ``env -i rm -rf /`` must hit the
    floor. The original lookbehind only caught a bare ``env `` prefix.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "env FOO=bar rm -rf /",
        "env -i rm -rf /",
        "env PATH=/x rm -rf /etc",
        "env A=1 B=2 rm -rf $HOME",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_chained_wrappers(tmp_path):
    """``sudo env FOO=bar rm -rf /`` chains two wrappers; both must be
    consumed before the rm pattern is checked.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    assert e.decide(
        "run_shell_command",
        {"command": "sudo env FOO=bar rm -rf /"},
    ) == PermissionDecision.DENY


def test_hardline_blocks_sudo_with_quoted_path(tmp_path):
    """The two bypass classes compose: ``sudo -n rm -rf "$HOME"`` must
    be denied even when both layers (wrapper + quoting) are used.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    assert e.decide(
        "run_shell_command",
        {"command": 'sudo -n rm -rf "$HOME"'},
    ) == PermissionDecision.DENY


def test_hardline_blocks_split_rm_flags(tmp_path):
    """``rm -r -f /`` / ``rm -r --force /`` / ``rm --recursive --force /``
    are the same destructive operation as ``rm -rf /`` and must hit the
    floor. The original matcher required ``r`` and ``f`` to live in the
    same flag token, leaving these standard split forms as bypasses.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "rm -r -f /",
        "rm -f -r /",
        "rm -r --force /",
        "rm --recursive -f /",
        "rm --recursive --force /",
        "rm -r --force /home/user",
        "rm -R --force /etc",
        "rm --no-preserve-root --recursive --force /",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_split_rm_flags_no_false_positive(tmp_path):
    """Half-recursive/half-force is recoverable — only DENY when both
    markers are present.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    # -r alone (no force) won't wipe write-protected files, so the floor
    # stays out of it.
    assert e.decide(
        "run_shell_command", {"command": "rm -r /home/user"},
    ) == PermissionDecision.ALLOW
    # -f alone (no recursive) only deletes a single file.
    assert e.decide(
        "run_shell_command", {"command": "rm -f /etc/foo"},
    ) == PermissionDecision.ALLOW


def test_hardline_blocks_sudo_with_separate_arg_flags(tmp_path):
    """``sudo -u root rm -rf /`` and ``sudo -g group rm -rf /`` are
    common sudo invocations whose value argument lives in a *separate*
    token. The wrapper must consume both halves before the rm pattern
    is checked, otherwise the floor is bypassed in full-access mode.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "sudo -u root rm -rf /",
        "sudo -g group rm -rf /",
        "sudo -u root -E rm -rf /home/user",
        "sudo --user=root rm -rf /",
        "sudo --user root rm -rf /etc",
        "sudo -u root --chdir /tmp rm -rf /",
        "sudo -u root rm -r -f /",  # split flags + sudo separate-arg
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_path_qualified_rm(tmp_path):
    """``/bin/rm -rf /`` and ``/usr/bin/rm -rf /home/user`` perform the
    same unrecoverable delete as bare ``rm`` — and shells routinely use
    the path-qualified form to bypass aliases. The floor must treat them
    identically.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "/bin/rm -rf /",
        "/usr/bin/rm -rf /home/user",
        "/sbin/rm -rf /etc",
        "/usr/sbin/rm -rf /var",
        "/usr/local/bin/rm -rf /",
        "sudo /bin/rm -rf /",
        "/sbin/shutdown -h now",
        "/sbin/mkfs.ext4 /dev/sda1",
        "/usr/sbin/poweroff",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_newline_separator(tmp_path):
    """A literal newline acts as a shell separator under ``shell=True``,
    so a multi-line input like ``echo ok\\nrm -rf /`` runs both lines.
    The floor must treat newline-separated commands the same as
    ``;``-separated ones — otherwise prompt-injected multi-line scripts
    bypass the deny in full-access mode.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "echo ok\nrm -rf /",
        "echo ok\nrm -rf /home/user",
        "echo ok\n\nrm -rf $HOME",
        "echo a\r\nrm -rf /",
        "false\nshutdown -h now",
        "true\nmkfs.ext4 /dev/sda1",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_command_substitution_targets(tmp_path):
    """The path-boundary class must accept ``)`` and backtick so a
    destructive ``rm`` *inside* command substitution still hits the
    floor. ``echo $(rm -rf /)`` runs the inner ``rm`` before the echo
    even sees its result.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "echo $(rm -rf /)",
        "echo $(rm -rf /etc)",
        "x=$(rm -rf /home/user)",
        "echo `rm -rf /`",
        "echo `rm -rf /usr`",
        "result=$(sudo rm -rf /)",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_separator_after_path(tmp_path):
    """``rm -rf /;`` and ``rm -rf / && reboot`` end the path token with
    a shell metacharacter rather than whitespace. Ensure the boundary
    class catches these forms.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "rm -rf /;echo done",
        "rm -rf /etc;echo done",
        "rm -rf /&disown",
        "rm -rf / | tee /tmp/log",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_home_variable_word_boundary(tmp_path):
    """``$HOME`` must be a *whole* token. ``rm -rf $HOMEBREW_CACHE`` and
    ``rm -rf $HOMELESS`` are routine cleanup commands that point at
    NON-home directories — they must not be denied by the floor.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    # Should NOT match the floor — $HOME is just a prefix here.
    for cmd in [
        "rm -rf $HOMEBREW_CACHE",
        "rm -rf $HOMELESS",
        "rm -rf ${HOMEBREW_CACHE}",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ALLOW, f"{cmd!r} should NOT hit hardline"
    # Genuine $HOME / ${HOME} (with proper boundary) still hits.
    for cmd in [
        "rm -rf $HOME",
        "rm -rf $HOME/cache",
        "rm -rf ${HOME}",
        "rm -rf ${HOME}/cache",
        'rm -rf "$HOME"',
        "rm -rf $HOME;echo done",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_no_false_positive_on_quoted_text(tmp_path):
    """``echo "(rm -rf /)"`` and ``printf "cat > /dev/sda"`` are
    benign — they print literal text. Quoted-string content must NOT
    trigger the floor, otherwise the default-on protection breaks
    common docs/log-emitting commands.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        # Grouping-shaped strings inside double quotes
        'echo "(rm -rf /)"',
        'echo "(reboot required)"',
        'echo "{ rm -rf /; }"',
        # Redirect-shaped strings inside double quotes
        'echo "cat image > /dev/sda"',
        'printf "backup > /dev/disk0"',
        # Single quotes (fully literal)
        "echo '(rm -rf /)'",
        "echo 'cat image > /dev/sda'",
        "printf 'shutdown -h now'",
        "echo 'rm -rf /etc'",
        # Separator-shaped strings inside quotes
        'echo "ok; rm -rf /"',
        "echo 'ok; rm -rf /'",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ALLOW, f"{cmd!r} should NOT hit hardline"


def test_hardline_still_catches_dollar_paren_in_double_quotes(tmp_path):
    """``echo "$(rm -rf /)"`` is *not* a false positive — bash executes
    command substitution inside double quotes. The quote-aware filter
    must keep matching ``$(...)`` and `` `...` `` even when they sit
    inside double quotes.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        'echo "$(rm -rf /)"',
        'echo "$(rm -rf /etc)"',
        'echo "`rm -rf /`"',
        'x="$(rm -rf $HOME)"',
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_control_flow_keywords(tmp_path):
    """Bash control-flow keywords (``then`` ``do`` ``else`` ``elif``)
    introduce a fresh command context after their preceding separator,
    so a destructive command tucked into ``if .. then .. fi`` or
    ``while .. do .. done`` runs as normal. The floor must catch them.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "if true; then rm -rf /; fi",
        "while true; do rm -rf /; done",
        "until false; do rm -rf /home/user; done",
        "for i in 1 2 3; do rm -rf /etc; done",
        "if false; then echo a; else rm -rf /; fi",
        "if false; then echo a; elif true; then rm -rf /; fi",
        # `!` negation
        "! rm -rf /",
        "echo a; ! rm -rf /etc",
        # coproc
        "coproc rm -rf /",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_control_flow_keywords_no_false_positive(tmp_path):
    """Quoted text mentioning control-flow keywords must NOT trigger
    the floor — those are literal strings, not real shell syntax.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        'echo "then rm -rf /"',
        "echo 'if true; then rm -rf /; fi'",
        'echo "do rm -rf /"',
        # ! inside double quote is literal in non-interactive shells
        'echo "! rm -rf /"',
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ALLOW, f"{cmd!r} should NOT hit hardline"


def test_hardline_blocks_repeated_slash_root_paths(tmp_path):
    """Linux/macOS collapse consecutive ``/`` to one, so ``rm -rf //`` /
    ``rm -rf ///`` / ``rm -rf ///etc`` all resolve to root or a system
    dir. The floor must catch every spelling.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "rm -rf //",
        "rm -rf ///",
        "rm -rf ////",
        "rm -rf --no-preserve-root //",
        "rm -rf --no-preserve-root ///",
        "rm -rf //.",
        "rm -rf /.//",
        "rm -rf //etc",
        "rm -rf ///etc",
        "rm -rf //usr",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_case_arm_bypass(tmp_path):
    """A ``case`` pattern terminator ``)`` introduces a fresh executable
    command list, so destructive commands tucked into case arms must
    still hit the floor in full-access mode.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "case x in x) rm -rf /;; esac",
        "case $x in foo) rm -rf /;; bar) echo ok;; esac",
        "case x in (x) rm -rf /;; esac",
        "case x in a|b) rm -rf /home/user;; esac",
        "case x in *) reboot;; esac",
        # Multi-statement arm: ``;`` separator handles the second
        # statement, but the first statement after ``)`` is what the
        # bypass relies on going unchecked.
        "case x in x) echo ok; rm -rf /etc;; esac",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_descends_into_cmdsub_sh_c_body(tmp_path):
    """``echo "$(sh -c 'echo ok; rm -rf /')"`` runs the wrapped script
    via command substitution, so the BFS extractor must descend even
    though the match starts at ``$`` inside a double quote — the
    quote is literal text *except* for substitutions, and ``$(`` is one
    of those.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        'echo "$(sh -c \'echo ok; rm -rf /\')"',
        'echo "$(bash -c \'true && rm -rf /etc\')"',
        'x="$(sh -c \'shutdown -h now\')"',
        'echo "`sh -c \'echo a; rm -rf /\'`"',
        # And without the outer double quotes too
        "$(sh -c 'echo a; rm -rf /')",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_rm_with_many_operands(tmp_path):
    """``rm -rf /tmp/a0 /tmp/a1 ... /tmp/aN /`` is still a root wipe —
    rm with ``-f`` ignores missing operands, so padding with extras
    must NOT bypass the floor. The argv lookahead is unbounded.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    pad = " ".join(f"/tmp/a{i}" for i in range(40))
    cmd = f"rm -rf {pad} /"
    assert e.decide("run_shell_command", {"command": cmd}) == PermissionDecision.DENY
    # Path appears at the very end after many flag tokens.
    cmd2 = "rm " + ("-r " * 8) + ("-f " * 8) + " ".join(["/tmp/x"] * 30) + " /etc"
    assert e.decide("run_shell_command", {"command": cmd2}) == PermissionDecision.DENY


def test_hardline_sh_c_extraction_requires_command_position(tmp_path):
    """``echo sh -c 'rm -rf /'`` only runs ``echo`` — ``sh`` is an
    argument, not a command. The body extractor must require the
    wrapper to start at a command position; otherwise harmless echo
    output denies whenever it contains a wrapper-shaped string.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "echo sh -c 'rm -rf /'",
        'echo bash -c "rm -rf /home/user"',
        # Argument to printf
        'printf "%s\\n" sh -c \'rm -rf /\'',
        # Nested in another command's args (no separator before sh)
        "git log --grep='sh -c \"rm -rf /\"'",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ALLOW, f"{cmd!r} should NOT hit hardline"
    # Sanity: real ``sh -c 'rm -rf /'`` (at command pos) still denied.
    assert e.decide(
        "run_shell_command", {"command": "sh -c 'rm -rf /'"},
    ) == PermissionDecision.DENY
    # Sanity: ``; sh -c '...'`` (after separator) still denied.
    assert e.decide(
        "run_shell_command", {"command": "echo a; sh -c 'rm -rf /'"},
    ) == PermissionDecision.DENY


def test_hardline_blocks_root_path_aliases(tmp_path):
    """``rm -rf /./`` and ``rm -rf /../`` resolve to ``/`` at execution
    time — they're aliases for the root delete the floor is intended
    to block. The bare-``/`` arm must accept ``.`` / ``..`` path
    components after the leading slash.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "rm -rf /.",
        "rm -rf /..",
        "rm -rf /./",
        "rm -rf /../",
        "rm -rf /.././",
        "rm -rf /./..",
        'rm -rf "/./"',
        "rm -rf '/../'",
        "sudo rm -rf /./",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_ignores_escaped_substitution_in_double_quotes(tmp_path):
    """``echo "\\$(rm -rf /)"`` only *prints* the literal text
    ``$(rm -rf /)`` — the dollar is escaped, so bash doesn't open a
    command substitution. The filter must not treat the match-start
    ``$`` as an executing opener when the preceding char is ``\\``.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        'echo "\\$(rm -rf /)"',
        'echo "\\`rm -rf /\\`"',
        'printf "Use \\$(rm -rf /) to wipe"',
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ALLOW, f"{cmd!r} should NOT hit hardline"


def test_hardline_double_backslash_dollar_paren_still_executes(tmp_path):
    """``echo "\\\\$(rm -rf /)"`` — two backslashes are a literal ``\\``
    followed by an *unescaped* ``$(``, which IS executed. Detection
    must survive the escape-tracking change.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    assert e.decide(
        "run_shell_command",
        {"command": 'echo "\\\\$(rm -rf /)"'},
    ) == PermissionDecision.DENY


def test_hardline_blocks_rm_options_after_operand(tmp_path):
    """GNU rm parses options after operands — ``rm /home/user -rf`` and
    ``rm / --no-preserve-root -rf`` perform the same destructive delete
    as the canonical ``rm -rf /home/user``. The floor must catch any
    arg-order rather than requiring flags strictly before the path.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "rm /home/user -rf",
        "rm / -rf",
        "rm /etc -rf",
        "rm / --no-preserve-root -rf",
        "rm -v /home/user -rf",
        "rm /home/user -r -f",
        "rm $HOME -rf",
        'rm "$HOME" -rf',
        "sudo rm /home/user -rf",
        "rm -v /home -r --force",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_rm_options_after_operand_no_false_positive(tmp_path):
    """A non-system path with arbitrary flag order is still recoverable
    and must be allowed in full-access.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "rm /tmp/scratch -rf",
        "rm -rf /tmp/scratch",
        "rm /tmp/scratch",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ALLOW, f"{cmd!r} should NOT hit hardline"


def test_hardline_quoted_sh_c_example_no_false_positive(tmp_path):
    """``echo "sh -c 'rm -rf /'"`` only *prints* the dangerous example.
    The recursive sh-c body extraction must respect the outer quote
    context — descending into a literal-quoted wrapper is a false
    positive that breaks docs/log emitters.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        'echo "sh -c \'rm -rf /\'"',
        "echo 'sh -c \"rm -rf /\"'",
        'printf "Run sh -c \'rm -rf /\' to wipe"',
        # But $(sh -c '...') inside double quotes IS executed
        # (separate test below ensures detection survives).
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ALLOW, f"{cmd!r} should NOT hit hardline"


def test_hardline_sh_c_inside_cmdsub_still_detected(tmp_path):
    """``echo "$(sh -c 'rm -rf /')"`` runs the wrapped script via
    command substitution — the quote-aware sh-c extractor must still
    descend when the wrapper is inside ``$(...)``.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        'echo "$(sh -c \'rm -rf /\')"',
        'x=$(bash -c "rm -rf /etc")',
        'echo "`sh -c \'rm -rf /\'`"',
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_catches_destructive_after_separator_in_sh_c(tmp_path):
    """``sh -c 'echo ok; rm -rf /'`` is the standard way to bury a
    destructive operation inside a quoted shell-script body. The outer
    single quote is literal to the *outer* shell, but the inner
    interpreter executes the script — so a destructive command anywhere
    inside it must hit the floor.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "sh -c 'echo ok; rm -rf /'",
        "sh -c 'true && rm -rf /'",
        "sh -c 'false || rm -rf /home/user'",
        'bash -c "true && mkfs.ext4 /dev/sda1"',
        'bash -c "echo ok; shutdown -h now"',
        "/bin/sh -c 'cd /tmp && rm -rf $HOME'",
        "sudo bash -c 'echo a; rm -rf /etc'",
        # Nested wrappers
        "sh -c 'bash -c \"rm -rf /\"'",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_catches_separator_inside_cmdsub(tmp_path):
    """A destructive command after a separator (``;`` ``&&`` ``||``)
    *inside* a command substitution still executes — the cmdsub is real
    shell context regardless of whether it's wrapped in double quotes.
    The filter must not over-aggressively suppress these.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        'echo "$(echo ok; rm -rf /)"',
        'echo "$(true && rm -rf /)"',
        'echo "$(false || rm -rf /)"',
        'echo "$(echo a; shutdown -h now)"',
        'echo "`echo ok; rm -rf /`"',
        'x=$(echo ok; rm -rf /)',
        'x=$(true && rm -rf /home/user)',
        # Nested cmdsub
        'echo "$(echo $(rm -rf /))"',
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_shell_grouping(tmp_path):
    """Subshells (``(rm -rf /)``) and brace groups (``{ rm -rf /; }``)
    run the wrapped command in the current shell context — they're
    syntactic grouping, not actual quoting. The floor must catch the
    inner destructive command the same way it catches ``;``-separated
    forms, otherwise a single pair of parens is enough to bypass it.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "(rm -rf /)",
        "( rm -rf / )",
        "(rm -rf /home/user)",
        "{ rm -rf /; }",
        "{ rm -rf /etc; }",
        "echo a; (rm -rf /)",
        "true && { rm -rf /; }",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_builtin_wrappers(tmp_path):
    """Shell builtins ``command`` / ``builtin`` / ``exec`` and process
    wrappers ``nohup`` / ``setsid`` / ``nice`` / ``time`` / ``busybox``
    all run the next command — frequently used (and intended) for
    bypassing aliases / functions. The floor must consume them the
    same way it consumes ``sudo`` and ``env``.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "command rm -rf /",
        "command -p rm -rf /",
        "exec rm -rf /",
        "exec /bin/rm -rf /",
        "builtin rm -rf /",
        "nohup rm -rf /",
        "setsid rm -rf /",
        "nice rm -rf /",
        "time rm -rf /",
        "busybox rm -rf /",
        "sudo command rm -rf /",
        "command sudo rm -rf /",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_shell_interpreter_wrappers(tmp_path):
    """``sh -c 'rm -rf /'`` is the idiomatic way to nest a destructive
    command, especially under prompt injection. The floor must inspect
    the *script* portion, not just the outer invocation, otherwise the
    bypass is one quoting layer away in default full-access.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "sh -c 'rm -rf /'",
        'sh -c "rm -rf /"',
        "bash -c 'rm -rf /home/user'",
        'bash -c "rm -rf /etc"',
        "zsh -c 'rm -rf $HOME'",
        "dash -c 'rm -rf /'",
        "/bin/sh -c 'rm -rf /'",
        "/bin/bash -c 'rm -rf /'",
        "sudo sh -c 'rm -rf /'",
        "sudo bash -c 'rm -rf /home/user'",
        "bash --noprofile -c 'rm -rf /'",
        "bash -x -c 'rm -rf /'",
        "sh -c 'shutdown -h now'",
        "bash -c 'mkfs.ext4 /dev/sda1'",
        "sh -c 'dd if=/dev/zero of=/dev/sda'",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_shell_interp_no_false_positive(tmp_path):
    """``sh -c 'rm -rf /tmp/scratch'`` is recoverable cleanup — the
    floor only catches root/system/home paths inside ``-c`` scripts,
    just like top-level invocations.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "sh -c 'rm -rf /tmp/scratch'",
        "bash -c 'echo hello'",
        "sh -c 'ls -la'",
        "bash -c 'git status'",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ALLOW, f"{cmd!r} should NOT hit hardline"


def test_hardline_blocks_macos_raw_disk_writes(tmp_path):
    """macOS / BSD expose raw disks as ``/dev/disk0`` and ``/dev/rdisk0``
    — the ``dd`` and redirect floors must catch those alongside the
    Linux device names. Otherwise the protection is silently
    platform-specific.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "dd if=/dev/zero of=/dev/disk0 bs=1m",
        "dd if=/dev/random of=/dev/rdisk0",
        "sudo dd if=/dev/zero of=/dev/disk2 bs=1m",
        "cat /dev/zero > /dev/disk0",
        "cat /dev/zero >/dev/rdisk1",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_quoted_raw_device_targets(tmp_path):
    """Shells strip surrounding quotes before exec, so
    ``dd of="/dev/sda"`` and ``cat image > "/dev/sda"`` perform the same
    raw-device write as the unquoted forms. The floor must catch the
    quoted variants — otherwise prompt-injected wipes survive by simply
    wrapping the device path in quotes.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        'dd if=/dev/zero of="/dev/sda" bs=1m',
        "dd if=/dev/zero of='/dev/sda'",
        'dd if=/dev/random of="/dev/disk0"',
        "dd if=/dev/random of='/dev/rdisk0'",
        'cat image > "/dev/sda"',
        "cat image > '/dev/sda'",
        'cat image > "/dev/disk0"',
        'sudo dd if=/dev/zero of="/dev/disk2"',
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_root_glob_deletes(tmp_path):
    """``rm -rf /*`` is the practical root wipe — bash expands it to
    every top-level entry and rm deletes them in turn. The hardline must
    treat globbed root/system paths the same as the bare-literal forms
    (``rm -rf /``, ``rm -rf /etc``).
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "rm -rf /*",
        "rm -rf /?",
        "rm -rf /etc/*",
        "rm -rf /home/*",
        "rm -rf /usr/*",
        "rm -rf ~/*",
        "rm -rf $HOME/*",
        "rm -rf ${HOME}/*",
        "rm -rf ${HOME:?}/*",
        "sudo rm -rf /*",
        "/bin/rm -rf /*",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_leading_env_assignments(tmp_path):
    """Bash applies ``NAME=VALUE`` tokens at the head of a command to
    that command's environment, so ``PATH=/bin rm -rf /`` and
    ``FOO=bar /bin/rm -rf /home/user`` execute the destructive ``rm``
    just like a bare invocation. The floor must consume those leading
    assignments before checking for the dangerous command.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "PATH=/bin rm -rf /",
        "FOO=bar rm -rf /home/user",
        "FOO=bar BAR=baz rm -rf /etc",
        "PATH=/bin /bin/rm -rf /",
        "FOO=bar sudo rm -rf /",
        "PATH=/bin shutdown -h now",
        "FOO=bar mkfs.ext4 /dev/sda1",
        "FOO=bar /sbin/poweroff",
        "echo hi; PATH=/bin rm -rf /",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_leading_env_no_false_positive(tmp_path):
    """A NAME=VALUE token that *isn't* followed by a dangerous command
    must not turn an innocuous shell into a hardline hit.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "FOO=bar echo hi",
        "PATH=/bin ls -la",
        "FOO=bar git status",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ALLOW, f"{cmd!r} should NOT hit hardline"


def test_hardline_home_parameter_expansion(tmp_path):
    """Bash parameter-expansion forms of ``${HOME}`` (``${HOME:?}``,
    ``${HOME:-default}``, ``${HOME%/*}``, ``${HOME:0:5}``, …) all expand
    to the user's home directory at execution time, so they must trigger
    the floor alongside the bare-literal ``$HOME``. Quoted variants
    (``"${HOME:?}"`` is the *standard* defensively-coded form) must
    work too.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "rm -rf ${HOME:?}",
        'rm -rf "${HOME:?}"',
        "rm -rf ${HOME:?error message}",
        "rm -rf ${HOME:-/tmp}",
        "rm -rf ${HOME:+x}",
        "rm -rf ${HOME%/*}",
        "rm -rf ${HOME##*/}",
        "rm -rf ${HOME:0:5}",
        'rm -rf "${HOME:?}/cache"',
        "rm -rf ${HOME:?};echo done",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_path_qualified_no_false_positive(tmp_path):
    """A user-installed ``./tools/rm`` script must not trigger the floor —
    we only path-qualify the standard system bin locations to keep
    surprises out of full-access. Likewise, words ending in ``rm`` (like
    ``farm``, ``alarm``) must not match.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "./tools/rm -rf /tmp/scratch",
        "echo farm",
        'echo "alarm clock"',
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ALLOW, f"{cmd!r} should NOT hit hardline"


def test_hardline_blocks_shell_quoting_split_command_name(tmp_path):
    """Bash unquotes per-character: ``r"m"``, ``r\\m``, ``'r''m'``,
    ``m"k"fs.ext4``, ``\\rm``, ``""rm""`` all collapse to the same
    command word. The floor must run patterns against a shell-word view
    where these splits are resolved, otherwise an attacker bypasses
    the default-on protection trivially in full-access mode.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        # Inline double-quote splits in command name.
        'r"m" -rf /',
        'r"m" -rf /etc',
        'm"k"fs.ext4 /dev/sda1',
        'shut"down" -h now',
        '"reboot"',
        '""rm"" -rf /',
        # Backslash-escape splits — ``\X`` of a plain char is a no-op
        # in bash, so the result is the same command name.
        r"\rm -rf /",
        r"r\m -rf /",
        r"\m\k\f\s.ext4 /dev/sda1",
        r"\shutdown -h now",
        # Single-quote splits — ``'rm'``, ``'r''m'``, etc.
        "'rm' -rf /",
        "'r''m' -rf /",
        "''rm'' -rf /",
        # Shell-interpreter wrapper with quoted name.
        's"h" -c \'rm -rf /\'',
        '\\sh -c "rm -rf /"',
        # ``dd`` with a quoted name.
        'd"d" if=/dev/zero of=/dev/sda',
        # Bypass attempts inside a real cmdsub still get caught.
        'echo $(\\rm -rf /)',
        'echo $(r"m" -rf /etc)',
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_ansi_c_quoted_shell_body(tmp_path):
    """``bash -c $'rm -rf /'`` and ``zsh -c $'\\nrm -rf /\\n'`` execute
    the destructive command via bash/zsh ANSI-C quoting (``$'...'``).
    The shell-script-wrapper extractor must recognize ``$'...'`` (and
    its ``$"..."`` locale-string sibling) as a body opener and decode
    ``\\n`` / ``\\t`` / ``\\\\`` etc. so an embedded escape that bash
    turns into a real separator (``\\n`` → newline → fresh command
    position) actually creates one in the recursive check.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "bash -c $'rm -rf /'",
        "sh -c $'rm -rf /'",
        "zsh -c $'rm -rf /home/user'",
        # Embedded separators inside the ANSI-C body — the decoder
        # interprets ``\n`` so the recursive check sees a real
        # newline at command position.
        "bash -c $'\\nrm -rf /\\n'",
        "bash -c $'echo ok\\nrm -rf /'",
        "bash -c $'echo ok; rm -rf /'",
        "bash -c $'true && rm -rf /etc'",
        "bash -c $'shutdown -h now'",
        # Locale-string $"..." — bash treats it like "..." with
        # gettext lookup, but the source string already carries the
        # destructive intent.
        'bash -c $"rm -rf /"',
        'bash -c $"shutdown -h now"',
        # Path-qualified interpreter still picks it up.
        "/bin/bash -c $'rm -rf /'",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_ansi_c_no_false_positive(tmp_path):
    """``echo $'rm -rf /'`` only prints the literal text — ``$'...'``
    is just an argument to ``echo``, not a sh -c body. The ANSI-C
    body decoder must only fire for actual ``sh -c $'...'`` /
    ``bash -c $'...'`` wrappers.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "echo $'rm -rf /'",
        "printf $'%s\\n' done",
        'echo $"rm -rf /"',
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ALLOW, f"{cmd!r} should NOT hit hardline"


def test_hardline_blocks_ansi_c_numeric_escapes(tmp_path):
    """ANSI-C numeric/Unicode/control escapes inside ``$'...'`` decode
    to real characters before bash parses the body — ``$'\\x72m\\x20-rf
    \\x20/'`` runs ``rm -rf /``. The decoder must resolve hex / octal /
    Unicode / ``\\cX`` so an encoded destructive command surfaces real
    command-name and separator chars to the recursive check.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        # \xHH — full-hex disk wipe.
        "bash -c $'\\x72m\\x20-rf\\x20/'",
        # \xHH — partial encoding (some chars literal, some hex).
        "bash -c $'\\x72m -rf /'",
        "bash -c $'rm\\x20-rf\\x20/etc'",
        # Octal — \NNN, three digits.
        "bash -c $'\\162m -rf /'",
        # Mixed hex + embedded \n separator (decoded line break makes
        # ``rm`` a fresh command).
        "bash -c $'echo ok\\x0arm -rf /'",
        # Unicode \uHHHH — same destructive intent.
        "bash -c $'\\u0072m -rf /'",
        # Power-state encoded via hex.
        "bash -c $'\\x73hutdown -h now'",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_control_flow_keywords_only_after_separator(tmp_path):
    """Bash parses ``then`` / ``do`` / ``else`` / ``elif`` as control-
    flow keywords ONLY when they sit at a real command position (after
    ``;`` / newline / ``&&`` / ``||`` / start-of-line). Bare argv
    occurrences like ``echo then rm -rf /`` are just words to ``echo``,
    not real shell control flow — those must not trigger the floor.
    Regression test for a false positive where the keywords were
    accepted as separators wherever they appeared.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "echo then rm -rf /",
        "echo do rm -rf /",
        "echo else rm -rf /",
        "echo elif rm -rf /",
        "printf '%s' then rm -rf /",
        "printf -- then do else elif rm -rf /",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ALLOW, f"{cmd!r} should NOT hit hardline"


def test_hardline_blocks_dollar_quote_at_argv_position(tmp_path):
    """Bash ``$'...'`` (ANSI-C quoting) and ``$"..."`` (locale string)
    decode to a single shell word whose decoded value is what bash
    parses as argv — ``$'rm' -rf /`` runs ``rm`` against ``/``, and
    ``rm -rf $'/etc'`` deletes ``/etc``. Without inline decoding in the
    shell-word view, the body chars carry the inner quote context and
    the hardline filter rejects the match as literal. Regression test
    for that bypass: the floor must catch ``$'...'`` / ``$"..."`` at
    any argv position, not just inside a ``sh -c '...'`` body.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        # Command name in $'...' / $"..."
        "$'rm' -rf /",
        '$"rm" -rf /',
        # Path argument in $'...' / $"..."
        "rm -rf $'/'",
        "rm -rf $'/etc'",
        'rm -rf $"/etc"',
        "rm -rf $'/home/user'",
        # Flags / path both encoded
        "$'rm' --no-preserve-root -rf $'/'",
        # Hex-encoded inside ANSI-C path
        "rm -rf $'\\x2fetc'",
        # Power transitions encoded
        "$'shutdown' -h now",
        '$"reboot"',
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_dollar_quote_no_false_positive(tmp_path):
    """Bash treats ``$'...'`` / ``$"..."`` *inside* a literal quote
    span as plain text — ``"$'rm -rf /'"`` only prints the literal
    chars. And ``echo $'rm -rf /'`` is just an argument to ``echo``,
    not a command. The floor must not block these benign forms even
    after the inline-decode fix.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "echo $'rm -rf /'",
        'echo $"rm -rf /"',
        # $-quote inside outer single quotes — fully literal
        "echo '$\\'rm -rf /\\''",
        # Whole destructive line as one $"..." word — bash would try to
        # exec a binary literally named ``rm -rf /etc``, which fails;
        # the hardline conservatively flags this anyway since the
        # decoded text matches the destructive pattern. The point of
        # this test is to make sure the same string as an *echo arg*
        # is not flagged.
        'echo $"rm -rf /etc"',
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ALLOW, f"{cmd!r} should NOT hit hardline"


def test_hardline_blocks_heredoc_to_shell_interpreter(tmp_path):
    """``bash <<EOF\\nrm -rf /\\nEOF`` and ``cat <<EOF | bash`` both
    feed the body to a shell interpreter for *execution* — the body's
    ``\\n`` is a real command separator, not a data line break. The
    hardline mask must NOT zero out the body in this case, otherwise
    the destructive ``rm`` is hidden from the scan and slips through.
    Regression test for a bypass where every here-doc body was masked
    unconditionally regardless of the launching command.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "bash <<'EOF'\nrm -rf /\nEOF",
        "sh <<EOF\nrm -rf /\nEOF",
        "zsh -x <<EOF\nrm -rf /home/user\nEOF",
        "sudo bash <<EOF\nrm -rf /\nEOF",
        # Pipeline: cat reads the body, but pipes it into bash for
        # execution. The body is still shell code.
        "cat <<EOF | bash\nrm -rf /\nEOF",
        # Power-state via shell heredoc.
        "sh <<EOF\nshutdown -h now\nEOF",
        # ``<<-`` strip-leading-tabs form.
        "bash <<-EOF\n\trm -rf /\n\tEOF",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_brace_and_glob_root_paths(tmp_path):
    """Bash brace expansion ``/{etc,usr,bin}`` expands to ``/etc /usr
    /bin`` and glob class ``/[bes]*`` matches anything under root
    starting with those letters — both are destructive when fed to
    ``rm -rf``. The path boundary class must accept ``{`` and ``[`` so
    these forms hit the floor.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "rm -rf /{etc,usr}",
        "rm -rf /{etc,usr,var,bin,home}",
        "rm -rf /[bes]*",
        "rm -rf /[a-z]*",
        # Brace/glob right after a system-dir name.
        "rm -rf /etc[abc]",
        "rm -rf /etc{,backup}",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_brace_glob_no_false_positive(tmp_path):
    """The new ``{``/``[`` boundary chars must not flag harmless
    non-system paths like ``/tmp/[a-z]*`` or ``./[abc]`` — only
    paths that resolve to root or a system directory should hit.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "rm -rf /tmp/[a-z]*",
        "rm -rf /tmp/{a,b,c}",
        "rm -rf ./[abc]",
        "rm -rf ./{old,new}",
        "echo /{etc,usr}",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ALLOW, f"{cmd!r} should NOT hit hardline"


def test_hardline_skips_heredoc_bodies(tmp_path):
    """``cat <<'EOF'\\nrm -rf /\\nEOF`` feeds ``rm -rf /`` as DATA to
    ``cat`` (read from stdin) — the embedded ``\\n`` is a line break
    in the data stream, not a command separator. The hardline
    scanner must skip here-doc body content so harmless documentation
    snippets, generator scripts, or doc-style ``cat <<EOF`` blocks
    don't trip the floor.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        # Plain here-doc.
        "cat <<EOF\nrm -rf /\nEOF",
        # Quoted-tag here-doc (no parameter expansion).
        "cat <<'EOF'\nrm -rf /\nEOF",
        'cat <<"END"\nrm -rf /\nEND',
        # ``<<-`` strip-leading-tab form.
        "cat <<-EOF\n\trm -rf /\n\tEOF",
        # Generator: writing a script with destructive text inline.
        "cat <<EOF > /tmp/script.sh\necho hi\nrm -rf /\nEOF",
        # Multi-line documentation embedded in a tee.
        "tee /tmp/x.md <<DOC\nUsage: rm -rf /\nDOC",
        # Nested inside cmdsub.
        "x=$(cat <<EOF\nrm -rf /\nEOF\n)",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ALLOW, f"{cmd!r} should NOT hit hardline"


def test_hardline_heredoc_does_not_swallow_real_command(tmp_path):
    """Lines OUTSIDE the here-doc body remain shell commands — the
    mask must stop at the closing TAG so a destructive op after the
    here-doc still hits the floor.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        # Real rm -rf / on the line after EOF closer.
        "cat <<EOF\nfoo\nEOF\nrm -rf /",
        # Same on a single line via ;
        "cat <<EOF\nfoo\nEOF\n; rm -rf /",
        # Quoted ``<<EOF`` text inside a quote is NOT a here-doc opener,
        # so the rest of the command is still scanned normally.
        'echo "<<EOF" ; rm -rf /',
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_shell_quoting_no_false_positive(tmp_path):
    """Quoted DATA mentioning a destructive command must not trigger
    the floor: the same shell-word view that catches ``r"m" -rf /``
    must still leave ``echo "rm -rf /"``, ``echo 'rm -rf /'``, and
    ``printf "sh -c rm -rf /\\n"`` alone, because those are arguments
    to ``echo`` / ``printf`` — bash never executes them as a command.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        'echo "rm -rf /"',
        "echo 'rm -rf /'",
        'echo "shutdown -h now"',
        "echo 'mkfs.ext4 /dev/sda1'",
        'printf "sh -c rm -rf /\\n"',
        # Escaped quote chars inside top-level — the literal text is
        # the data, not executable shell.
        r'echo r\"m\" -rf /',
        # Adjacent literal data + benign command name.
        'echo r"m" /tmp/scratch',
        # Double-escaped substitution form: literal ``\$(...)`` text.
        r'echo \$\(rm -rf /\)',
        r"echo '\$(rm -rf /)'",
        'echo "\\$(rm -rf /)"',
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ALLOW, f"{cmd!r} should NOT hit hardline"


def test_hardline_blocks_eval_wrapper(tmp_path):
    """``eval`` re-parses its arguments as shell text and runs them, so
    ``eval rm -rf /`` is just ``rm -rf /`` with one extra layer. The
    indirect form ``eval $cmd`` requires data-flow analysis the floor
    does not attempt and is intentionally out of scope.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "eval rm -rf /",
        'eval "rm -rf /"',
        "eval 'rm -rf /home/user'",
        "eval mkfs.ext4 /dev/sda1",
        "sudo eval rm -rf /",
        "eval shutdown -h now",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_destructive_disk_tools(tmp_path):
    """Disk-destruction tools that take a raw block device argument:
    shred / wipefs / blkdiscard / parted / sgdisk / fdisk /
    cryptsetup luksFormat / diskutil eraseDisk. These are unrecoverable
    in the same sense as ``mkfs`` and ``dd of=`` and belong on the floor.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "shred -n 1 -z /dev/sda",
        "shred /dev/disk0",
        "shred -uvz /dev/nvme0n1",
        "wipefs --all /dev/sda",
        "wipefs -a /dev/sda1",
        "blkdiscard /dev/sda",
        "blkdiscard -f /dev/nvme0n1",
        "parted /dev/sda mklabel gpt",
        "parted -s /dev/sda mklabel gpt",
        "sgdisk -Z /dev/sda",
        "fdisk /dev/sda",
        "cfdisk /dev/sda",
        "gdisk /dev/sda",
        "cryptsetup luksFormat /dev/sda",
        "cryptsetup -y luksFormat /dev/sda1",
        "diskutil eraseDisk JHFS+ x disk0",
        "diskutil secureErase 0 disk0",
        "diskutil zeroDisk disk1",
        "diskutil reformat disk2",
        "tee /dev/disk0 < image.bin",
        "cat image.bin | tee /dev/sda",
        "echo data | tee -a /dev/sda",
        # Quoted device targets (shell strips quotes before exec).
        'shred -n 1 "/dev/sda"',
        "wipefs --all '/dev/disk0'",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_disk_tools_no_false_positive(tmp_path):
    """Read-only inspection forms (``fdisk -l``, ``parted -l``,
    ``sgdisk -p``) and disk tools targeting non-device paths
    (``shred /tmp/secret.txt``, ``tee /var/log/foo``) must NOT hit
    the floor. The list flag is the canonical read-only mode for
    every partitioner; that pattern is well-known to admins, and
    forcing them through ``--no-floor`` for routine inspection is
    the wrong default.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "fdisk -l /dev/sda",
        "fdisk --list /dev/sda",
        "parted -l",
        "sgdisk -p /dev/sda",
        "sgdisk --print /dev/sda",
        "shred /tmp/secret.txt",
        "shred /home/user/private.key",
        "wipefs -n /home/user/img.iso",
        "tee /tmp/log",
        "tee -a /var/log/foo",
        "tee logfile.txt",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ALLOW, f"{cmd!r} should NOT hit hardline"


def test_hardline_blocks_xargs_rm_recursive_force(tmp_path):
    """``xargs rm -rf`` builds the destructive operand from stdin —
    the floor cannot see the operand, but the combination of xargs +
    rm + recursive + force is essentially never benign.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "echo / | xargs rm -rf",
        "echo / | xargs rm -fr",
        "echo / | xargs -n 10 rm -rf",
        "echo / | xargs -P 4 -I _ rm -rf _",
        "xargs -I {} rm -rf {} <<< /",
        "find /tmp -type d -print0 | xargs -0 rm -rf",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_xargs_no_false_positive(tmp_path):
    """``xargs cat``, ``xargs ls``, and plain ``xargs rm`` (without
    both -r and -f) stay outside the floor. Plain rm of an explicit
    list is recoverable via undelete tools and is a normal admin op.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "echo file.txt | xargs cat",
        "echo file.txt | xargs ls",
        "echo file.txt | xargs rm",  # no -rf
        "echo data | xargs echo",
        "xargs cat < list.txt",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ALLOW, f"{cmd!r} should NOT hit hardline"


def test_hardline_blocks_find_destructive_actions(tmp_path):
    """``find <root-or-system-path> ... -delete`` and ``find ... -exec
    rm -rf`` walk and recursively delete the tree. The floor catches
    them when the find argument is a destructive root path; benign
    cleanups like ``find /tmp -delete`` stay allowed because /tmp is
    not in the destructive-path set.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "find / -delete",
        "find /etc -delete",
        "find /home/user -delete",
        "find ~ -delete",
        "find / -name '*.bak' -delete",
        "find / -exec rm -rf {} +",
        r"find / -exec rm -rf {} \;",
        "find /etc -execdir rm -rf {} +",
        "sudo find / -delete",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_find_no_false_positive(tmp_path):
    """``find /tmp -delete``, ``find . -name foo``, ``find / -type f``
    (without -delete or -exec rm) stay outside the floor.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "find /tmp -delete",
        "find . -delete",
        'find /tmp -name "*.bak" -delete',
        "find / -name foo",
        "find / -type f",
        "find / -name '*.log' -mtime +30",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ALLOW, f"{cmd!r} should NOT hit hardline"


def test_hardline_blocks_glob_path_rm(tmp_path):
    """Glob-bracketed paths (``/[b]in/rm``, ``/?in/rm``, ``/u?r/bin/rm``)
    expand at exec time to the system rm binary, so the floor must
    treat them like the bare ``rm`` form. Without this the path-prefix
    check is one bracket-glob away from being bypassed.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "/[b]in/rm -rf /",
        "/?in/rm -rf /",
        "/u?r/bin/rm -rf /",
        "/[bs]in/rm -rf /home/user",
        "sudo /?in/rm -rf /etc",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_extended_tilde_forms(tmp_path):
    """Bash tilde expansions ``~user``, ``~+`` (PWD), ``~-`` (OLDPWD)
    each resolve to a directory at runtime. The original tilde arm
    only caught bare ``~`` and ``~/...``; the extended forms are
    equally destructive when fed to ``rm -rf``.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "rm -rf ~root",
        "rm -rf ~+",
        "rm -rf ~-",
        "rm -rf ~user/important",
        "rm -rf ~admin",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_herestring_to_shell(tmp_path):
    """``shell-interp <<< 'body'`` feeds the body to the interpreter
    on stdin, where it is parsed as shell. This is the inline counter-
    part of the here-doc owner check; without it, ``bash <<< 'rm -rf /'``
    is a one-quoting-layer bypass.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        'bash <<< "rm -rf /"',
        "bash <<< 'rm -rf /'",
        "sh <<< $'rm -rf /'",
        "sh <<< $'rm -rf /\\nshutdown -h now'",
        'bash --noprofile <<< "rm -rf /"',
        '/bin/bash <<< "rm -rf /home/user"',
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_pipe_to_shell(tmp_path):
    """``echo X | sh`` and ``printf X | bash`` print X to stdout where
    a downstream shell reads it as a script. Catching this requires
    re-extracting the echo/printf args region after collapsing the
    outer quotes — otherwise the literal ``"rm -rf /"`` lives in
    quote context and the regular hardline scan rejects it.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        'echo "rm -rf /" | sh',
        "echo 'rm -rf /' | bash",
        'echo "rm -rf /" | /bin/sh',
        'printf "rm -rf /" | sh',
        'echo "rm -rf /home/user" | bash',
        'echo "shutdown -h now" | sh',
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_procsubst_to_shell(tmp_path):
    """``source <(echo SCRIPT)``, ``. <(echo SCRIPT)``,
    ``bash <(echo SCRIPT)`` use process substitution to feed a fifo
    of SCRIPT bytes to a shell loader. The runtime script is the
    inner echo's args.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        'source <(echo "rm -rf /")',
        '. <(echo "rm -rf /")',
        "bash <(echo 'rm -rf /')",
        '. <(printf "rm -rf /home/user")',
        'sh <(echo "shutdown -h now")',
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_blocks_var_expanded_shell_interpreter(tmp_path):
    """``$s -c "rm -rf /"`` (where ``s`` was previously assigned the
    name of a shell) is the variable-indirected form of ``sh -c``.
    The recursion runs the same hardline patterns on the body, so
    ``$EDITOR -c ":wq"`` and other benign ``$VAR -c X`` forms stay
    allowed because their body is not destructive.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        's=sh; $s -c "rm -rf /"',
        "s=bash; ${s} -c 'rm -rf /'",
        'SHELL=bash; $SHELL -c "rm -rf /"',
        'SH=sh; $SH -c "rm -rf /home/user"',
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"
    for cmd in [
        '$EDITOR -c ":wq"',
        '$BROWSER -c "open"',
        '$VIEWER -c "show"',
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ALLOW, f"{cmd!r} should NOT hit hardline"


def test_hardline_blocks_cmdsub_echo_as_command(tmp_path):
    """``$(echo SCRIPT)`` and ``` `echo SCRIPT` ``` at command position
    re-execute SCRIPT — bash captures the inner echo's stdout and
    parses it as the next command. ``bash -c "$(echo rm -rf /)"`` is
    the same construct one wrapper layer down.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "`echo rm -rf /`",
        "$(echo rm -rf /)",
        'bash -c "$(echo rm -rf /)"',
        'sh -c "$(echo rm -rf /)"',
        'echo a; `echo rm -rf /`',
        'true && $(echo rm -rf /)',
        'echo "$(echo rm -rf /)" | bash',
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, f"{cmd!r} should hit hardline"


def test_hardline_indirect_shell_no_false_positive(tmp_path):
    """The echo-pipe / process-substitution / cmdsub recursion must
    not flip benign uses: ``echo done``, ``source ~/.bashrc``,
    ``cat $(echo file)``, ``echo "$(date)"``. The discriminator is
    whether the captured args resolve to a destructive shell command,
    not whether echo/printf or process substitution is present.
    """
    e = PermissionEngine(project_root=tmp_path)
    e.set_mode(PermissionMode.FULL_ACCESS)
    for cmd in [
        "echo done",
        "echo hello world",
        'echo "hello world"',
        "source ~/.bashrc",
        "source script.sh",
        "source <(echo PATH=$PATH)",
        "cat $(echo file)",
        'echo "$(date)"',
        'x=$(date)',
        # Outer echo only PRINTS the cmdsub result, no shell after.
        "echo \"$(echo rm -rf /)\"",
        # Pipe to a non-shell command.
        'echo "rm -rf /" | grep done',
        'printf "%s\\n" "hello"',
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.ALLOW, f"{cmd!r} should NOT hit hardline"
