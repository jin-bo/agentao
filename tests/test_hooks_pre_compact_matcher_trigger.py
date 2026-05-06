"""PreCompact matcher is regex (re.fullmatch), not glob.

Pins the A2 ``_matches`` extension's per-event matcher dispatch:
``manual|auto`` must match ``"auto"`` (alternation), and ``.*`` must
match too. The previous ``_glob_match`` helper would have failed both.
"""

from __future__ import annotations

from agentao.plugins.hooks import (
    ClaudeHookPayloadAdapter,
    PluginHookDispatcher,
)
from agentao.plugins.models import ParsedHookRule


def _payload(tmp_path):
    return ClaudeHookPayloadAdapter().build_pre_compact(
        cwd=tmp_path,
        compaction_type="full",
        reason="compression_threshold",
    )


def _make_rule(matcher):
    return ParsedHookRule(
        event="PreCompact",
        hook_type="command",
        command="echo ok",
        matcher=matcher,
        plugin_name="t",
    )


def test_manual_matcher_does_not_fire_on_auto_payload(tmp_path):
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    rule = _make_rule({"trigger": "manual"})
    assert not dispatcher._matches(rule, _payload(tmp_path))


def test_auto_matcher_fires(tmp_path):
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    rule = _make_rule({"trigger": "auto"})
    assert dispatcher._matches(rule, _payload(tmp_path))


def test_alternation_pattern_fires_claude_parity(tmp_path):
    """`manual|auto` must fire on `"auto"` payload — Claude Code parity."""
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    rule = _make_rule({"trigger": "manual|auto"})
    assert dispatcher._matches(rule, _payload(tmp_path))


def test_wildcard_regex_fires(tmp_path):
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    rule = _make_rule({"trigger": ".*"})
    assert dispatcher._matches(rule, _payload(tmp_path))


def test_empty_matcher_fires(tmp_path):
    """Stop has no matcher in Claude Code; PreCompact with no trigger key
    falls through and fires."""
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    rule = _make_rule({})
    assert dispatcher._matches(rule, _payload(tmp_path))


def test_malformed_regex_degrades_to_exact_equality(tmp_path):
    """An invalid regex must not crash dispatch; degrade to == comparison."""
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    bad = _make_rule({"trigger": "[unclosed"})
    assert not dispatcher._matches(bad, _payload(tmp_path))

    exact = _make_rule({"trigger": "auto"})
    assert dispatcher._matches(exact, _payload(tmp_path))


def test_non_string_trigger_matcher_does_not_crash(tmp_path):
    """A non-string ``trigger`` (list/number/etc) must degrade to no-match,
    not raise ``TypeError`` from ``re.fullmatch``. ParsedHookRule may be
    constructed directly bypassing the parser's matcher-shape guard."""
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    for bad_trigger in (["auto"], 42, {"nested": "x"}, True):
        rule = _make_rule({"trigger": bad_trigger})
        assert dispatcher._matches(rule, _payload(tmp_path)) is False, bad_trigger
