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


def _payload(tmp_path, trigger="auto"):
    return ClaudeHookPayloadAdapter().build_pre_compact(
        cwd=tmp_path,
        trigger=trigger,
        compaction_type="full",
        reason="compression_threshold" if trigger == "auto" else "manual_cli",
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


def test_manual_matcher_fires_on_manual_payload(tmp_path):
    """The producer half of the pair above.

    Before the trigger contract was fixed, ``build_pre_compact`` hardcoded
    ``"auto"`` at all five entry points, so ``{"trigger": "manual"}`` was a
    configuration value **no site could ever produce** — the rule above
    passed while describing a dead matcher. This is the assertion that
    makes it a real pair.
    """
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    rule = _make_rule({"trigger": "manual"})
    assert dispatcher._matches(rule, _payload(tmp_path, trigger="manual"))


def test_auto_matcher_does_not_fire_on_manual_payload(tmp_path):
    """The behaviour change PR-1 makes visible: an ``{"trigger": "auto"}``
    rule used to match manual ``/compact`` too, because every payload said
    ``auto``. It must not any more."""
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    rule = _make_rule({"trigger": "auto"})
    assert not dispatcher._matches(rule, _payload(tmp_path, trigger="manual"))


def test_alternation_pattern_fires_claude_parity(tmp_path):
    """`manual|auto` must fire on **both** payloads — Claude Code parity.

    This is the regression guard for the trigger change: an existing host
    rule written ``{"trigger": "manual|auto"}`` matched everything before
    and must keep matching everything after.
    """
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    rule = _make_rule({"trigger": "manual|auto"})
    assert dispatcher._matches(rule, _payload(tmp_path, trigger="auto"))
    assert dispatcher._matches(rule, _payload(tmp_path, trigger="manual"))


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
