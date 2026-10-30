"""PermissionEngine: modes, presets, custom rules, error handling, domain matching.

Split out from the original monolithic ``test_permissions.py``. The
hardline floor and sensitive-write preset live in their own modules.
"""

import pytest

from agentao.permissions import PermissionDecision, PermissionEngine, PermissionMode

from tests.support.permissions import allow, deny, make_engine as _engine


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

@pytest.mark.parametrize("tool, args, decision", [
    pytest.param("write_file", {"path": "x"}, PermissionDecision.ALLOW, id="allows-write-file"),
    pytest.param("replace", {"path": "x", "old": "a", "new": "b"}, PermissionDecision.ALLOW,
                 id="allows-replace"),
    pytest.param("run_shell_command", {"command": "git status"}, PermissionDecision.ALLOW,
                 id="allows-safe-shell-git-status"),
    pytest.param("run_shell_command", {"command": "ls -la"}, PermissionDecision.ALLOW,
                 id="allows-safe-shell-ls"),
    pytest.param("run_shell_command", {"command": "cat README.md"}, PermissionDecision.ALLOW,
                 id="allows-safe-shell-cat"),
    pytest.param("run_shell_command", {"command": "grep -r foo src/"}, PermissionDecision.ALLOW,
                 id="allows-safe-shell-grep"),
    pytest.param("run_shell_command", {"command": "rm -rf /"}, PermissionDecision.DENY,
                 id="denies-rm-rf"),
    pytest.param("run_shell_command", {"command": "sudo apt-get install x"}, PermissionDecision.DENY,
                 id="denies-sudo"),
    pytest.param("run_shell_command", {"command": "curl https://example.com"}, PermissionDecision.ASK,
                 id="asks-unknown-shell"),
    pytest.param("web_fetch", {"url": "https://example.com"}, PermissionDecision.ASK,
                 id="asks-web-fetch"),
    pytest.param("web_search", {"query": "python"}, PermissionDecision.ASK, id="asks-web-search"),
    pytest.param("unknown_tool_xyz", {}, None, id="unknown-tool-returns-none"),
])
def test_workspace_write_preset(tmp_path, monkeypatch, tool, args, decision):
    e = _engine(tmp_path, monkeypatch)
    assert e.decide(tool, args) is decision


# ---------------------------------------------------------------------------
# FULL_ACCESS mode
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool, args", [
    pytest.param("write_file", {"path": "x"}, id="write-file"),
    pytest.param("web_fetch", {"url": "https://x.com"}, id="web-fetch"),
])
def test_full_access_allows(tmp_path, monkeypatch, tool, args):
    e = _engine(tmp_path, monkeypatch)
    e.set_mode(PermissionMode.FULL_ACCESS)
    assert e.decide(tool, args) is PermissionDecision.ALLOW


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


# ---------------------------------------------------------------------------
# PLAN mode
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool, args, decision", [
    pytest.param("plan_save", {"content": "# Plan"}, PermissionDecision.ALLOW, id="allows-plan-save"),
    pytest.param("plan_finalize", {"draft_id": "20250101"}, PermissionDecision.ALLOW,
                 id="allows-plan-finalize"),
    pytest.param("write_file", {"path": "x"}, PermissionDecision.DENY, id="denies-write-file"),
    pytest.param("replace", {"path": "x", "old": "a", "new": "b"}, PermissionDecision.DENY,
                 id="denies-replace"),
    pytest.param("save_memory", {"key": "k", "value": "v"}, PermissionDecision.DENY,
                 id="denies-save-memory"),
    pytest.param("todo_write", {"todos": []}, PermissionDecision.DENY, id="denies-todo-write"),
    pytest.param("run_shell_command", {"command": "git diff HEAD"}, PermissionDecision.ALLOW,
                 id="allows-read-only-shell"),
    pytest.param("run_shell_command", {"command": "pip install x"}, PermissionDecision.DENY,
                 id="denies-arbitrary-shell"),
    pytest.param("web_fetch", {"url": "https://x.com"}, PermissionDecision.ASK, id="asks-web-fetch"),
    # Plan mode also has the domain allowlist and blocklist for web_fetch.
    pytest.param("web_fetch", {"url": "https://docs.python.org/3/"}, PermissionDecision.ALLOW,
                 id="domain-allowlist"),
    pytest.param("web_fetch", {"url": "http://127.0.0.1/admin"}, PermissionDecision.DENY,
                 id="domain-blocklist"),
])
def test_plan_mode_preset(tmp_path, monkeypatch, tool, args, decision):
    e = _engine(tmp_path, monkeypatch)
    e.set_mode(PermissionMode.PLAN)
    assert e.decide(tool, args) is decision


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

def test_invalid_json_user_config_fails_closed(tmp_path):
    """Breaking-change acceptance test (was ``..._graceful_fallback``).

    Until 0.4.20 this asserted the opposite — engine construction had to
    survive an unparseable policy file with ``rules == []``. Silently
    dropping rules is not a neutral degradation: a user ``deny`` on a
    shell tool degrades to ASK, and on an ``mcp_*`` tool it degrades to
    no prompt at all. The policy file now fails closed.
    """
    from agentao.embedding.permission_loader import PermissionConfigError

    user_root = tmp_path / "home" / ".agentao"
    user_root.mkdir(parents=True)
    bad = user_root / "permissions.json"
    bad.write_text("not valid json", encoding="utf-8")

    with pytest.raises(PermissionConfigError) as excinfo:
        PermissionEngine(project_root=tmp_path, user_root=user_root)

    assert excinfo.value.path == bad
    assert str(bad) in str(excinfo.value)


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

@pytest.mark.parametrize("url, decision", [
    pytest.param("https://api.github.com/repos/foo", PermissionDecision.ALLOW,
                 id="allowlist-suffix-match"),
    # '.github.com' also matches 'github.com' itself (no subdomain).
    pytest.param("https://github.com/foo/bar", PermissionDecision.ALLOW,
                 id="allowlist-exact-root-match"),
    # An exact domain entry (no leading dot) matches only that exact host.
    pytest.param("https://r.jina.ai/https://example.com", PermissionDecision.ALLOW,
                 id="allowlist-exact-entry"),
    # '.github.com' must NOT match 'notgithub.com'.
    pytest.param("https://notgithub.com/foo", PermissionDecision.ASK,
                 id="allowlist-no-false-suffix"),
    pytest.param("http://localhost:8080/api", PermissionDecision.DENY, id="blocklist-localhost"),
    pytest.param("http://127.0.0.1/secret", PermissionDecision.DENY, id="blocklist-loopback-ip"),
    # Cloud metadata IP (SSRF protection).
    pytest.param("http://169.254.169.254/latest/meta-data", PermissionDecision.DENY,
                 id="blocklist-metadata-endpoint"),
    pytest.param("https://api.corp.internal/data", PermissionDecision.DENY,
                 id="blocklist-internal-suffix"),
    pytest.param("http://0.0.0.0:5000/", PermissionDecision.DENY, id="blocklist-zero-ip"),
    # Not in the allowlist or the blocklist.
    pytest.param("https://example.com/page", PermissionDecision.ASK, id="fallthrough-to-ask"),
    pytest.param("HTTPS://GITHUB.COM/foo", PermissionDecision.ALLOW, id="case-insensitive"),
    # A URL without a scheme is handled gracefully.
    pytest.param("docs.python.org/3/library/os.html", PermissionDecision.ALLOW,
                 id="missing-scheme"),
    # Port numbers don't interfere with domain matching.
    pytest.param("http://localhost:3000/health", PermissionDecision.DENY, id="port-stripping"),
    # IP '127.0.0.1' does not match by suffix: '1.127.0.0.1' is in no list.
    pytest.param("http://1.127.0.0.1/", PermissionDecision.ASK, id="ip-exact-match-no-suffix"),
    # With userinfo (evil.com@github.com), urlparse takes github.com as the host.
    pytest.param("http://evil.com@github.com/path", PermissionDecision.ALLOW,
                 id="userinfo-resolves-to-the-real-host"),
])
def test_domain_preset_for_web_fetch(tmp_path, monkeypatch, url, decision):
    e = _engine(tmp_path, monkeypatch)
    assert e.decide("web_fetch", {"url": url}) is decision


def test_domain_custom_allowlist_overrides_preset_blocklist(tmp_path, monkeypatch):
    """User-level custom domain allowlist can override preset blocklist."""
    rules = [{"tool": "web_fetch", "domain": {"allowlist": ["localhost"]}, "action": "allow"}]
    e = _engine(tmp_path, monkeypatch, user_rules=rules)
    # Custom rule evaluated first in workspace-write mode
    assert e.decide("web_fetch", {"url": "http://localhost:8080/"}) == PermissionDecision.ALLOW


def test_domain_display_shows_allowlist(tmp_path, monkeypatch):
    """get_rules_display shows domain allowlist entries."""
    rules = [{"tool": "web_fetch", "domain": {"allowlist": [".example.com"]}, "action": "allow"}]
    e = _engine(tmp_path, monkeypatch, user_rules=rules)
    display = e.get_rules_display()
    assert "domain allowlist" in display
    assert ".example.com" in display
