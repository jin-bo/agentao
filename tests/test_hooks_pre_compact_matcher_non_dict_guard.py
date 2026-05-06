"""Non-dict matcher must be dropped at parse time and treated as no-match
at runtime.

Earlier drafts of this plan suggested normalizing a bad matcher to
``None``. Because the runtime contract is ``rule.matcher is None`` ≡
"match everything", silently doing that would invert the user's filter
intent: a misconfigured rule would suddenly match every event. This
test pins the safer behavior — drop at parse time, no-match at runtime.
"""

from __future__ import annotations

from agentao.plugins.hooks import (
    ClaudeHookPayloadAdapter,
    ClaudeHooksParser,
    PluginHookDispatcher,
)
from agentao.plugins.models import ParsedHookRule, PluginWarning


def test_string_matcher_dropped_at_parse_time():
    parser = ClaudeHooksParser()
    rules, warnings = parser.parse_dict({
        "hooks": {
            "PreCompact": [
                {"type": "command", "command": "echo", "matcher": "auto"},
            ],
        },
    }, plugin_name="t")

    assert rules == []
    assert len(warnings) == 1
    assert isinstance(warnings[0], PluginWarning)
    assert "non-object matcher" in warnings[0].message
    assert "PreCompact" in warnings[0].message
    assert warnings[0].field == "hooks"


def test_list_matcher_dropped_at_parse_time():
    parser = ClaudeHooksParser()
    rules, warnings = parser.parse_dict({
        "hooks": {
            "PreCompact": [
                {"type": "command", "command": "echo", "matcher": ["auto"]},
            ],
        },
    }, plugin_name="t")

    assert rules == []
    assert len(warnings) == 1
    assert "non-object matcher" in warnings[0].message


def test_runtime_non_dict_matcher_returns_false_no_match(tmp_path):
    """Bypass the parser to verify the runtime defense-in-depth guard:
    a directly-constructed rule with a non-dict matcher must be treated
    as no-match (NOT match-everything)."""
    rule = ParsedHookRule(
        event="PreCompact",
        hook_type="command",
        command="echo",
        matcher="auto",  # bypassing parser; bad shape
        plugin_name="t",
    )
    payload = ClaudeHookPayloadAdapter().build_pre_compact(
        cwd=tmp_path,
        compaction_type="full",
        reason="compression_threshold",
    )

    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    assert dispatcher._matches(rule, payload) is False


def test_dict_matcher_passes_parser():
    """Sanity: the valid object form still loads."""
    parser = ClaudeHooksParser()
    rules, warnings = parser.parse_dict({
        "hooks": {
            "PreCompact": [
                {"type": "command", "command": "echo", "matcher": {"trigger": "manual|auto"}},
            ],
        },
    }, plugin_name="t")

    assert warnings == []
    assert len(rules) == 1
    assert rules[0].matcher == {"trigger": "manual|auto"}
