"""A matcher that is not a valid regex is reported at load, not only at dispatch.

Dispatch compares such a pattern as literal text (``_regex_match_full``), so
``Read|Write(`` matches no tool and a deny hook written with it never runs.
That fallback stays — both contracts answer what they cannot use with a
diagnostic, not an error — but it used to be silent.
"""

from __future__ import annotations

import pytest

from agentao.plugins.hooks import ClaudeHooksParser
from agentao.plugins.hooks._matchers import _claude_matcher_match


def _group(matcher):
    return {"hooks": {"PreToolUse": [
        {"matcher": matcher, "hooks": [{"type": "command", "command": "echo"}]},
    ]}}


def test_invalid_profile_matcher_warns_and_keeps_the_rule():
    rules, warnings = ClaudeHooksParser().parse_dict(_group("Read|Write("), plugin_name="p")

    assert len(rules) == 1 and rules[0].matcher_pattern == "Read|Write("
    assert len(warnings) == 1
    msg = warnings[0].message
    assert "PreToolUse" in msg and "not a valid regular expression" in msg
    assert "'Read|Write('" in msg


@pytest.mark.parametrize("matcher", ["*", "", "Edit|Write", "mcp__.*", None])
def test_valid_and_wildcard_matchers_do_not_warn(matcher):
    rules, warnings = ClaudeHooksParser().parse_dict(_group(matcher), plugin_name="p")
    assert len(rules) == 1
    assert warnings == []


def test_invalid_v1_trigger_warns_and_keeps_the_rule():
    rules, warnings = ClaudeHooksParser().parse_dict({"hooks": {"PreCompact": [
        {"type": "command", "command": "echo", "matcher": {"trigger": "auto|("}},
    ]}}, plugin_name="p")

    assert len(rules) == 1
    assert len(warnings) == 1
    assert "'trigger'" in warnings[0].message and "PreCompact" in warnings[0].message


def test_dispatch_behaviour_is_unchanged():
    # Still literal comparison: the warning is the whole change.
    assert _claude_matcher_match("Read|Write(", "Read") is False
    assert _claude_matcher_match("Read|Write(", "Read|Write(") is True


def _v1(event, trigger):
    return {"hooks": {event: [
        {"type": "command", "command": "echo", "matcher": {"trigger": trigger}},
    ]}}


@pytest.mark.parametrize("trigger", ["*", ""])
def test_v1_trigger_has_no_wildcards(trigger):
    # The legacy dispatcher full-matches ``trigger`` as a plain regex, so the
    # profile's wildcard spellings match neither ``manual`` nor ``auto``.
    rules, warnings = ClaudeHooksParser().parse_dict(_v1("PreCompact", trigger), plugin_name="p")
    assert len(rules) == 1
    assert len(warnings) == 1


def test_v1_trigger_is_checked_only_where_it_is_read():
    # Only PreCompact evaluates ``trigger``; elsewhere it is ignored at dispatch.
    rules, warnings = ClaudeHooksParser().parse_dict(_v1("Stop", "auto|("), plugin_name="p")
    assert len(rules) == 1
    assert warnings == []


def test_valid_v1_trigger_does_not_warn():
    _, warnings = ClaudeHooksParser().parse_dict(_v1("PreCompact", "manual|auto"), plugin_name="p")
    assert warnings == []


@pytest.mark.parametrize("matcher", ["a{99999999999}", "(" * 2000 + ")" * 2000])
def test_matcher_that_compile_rejects_without_re_error_warns_not_raises(matcher):
    # ``re.compile`` raises OverflowError / RecursionError for these, not
    # ``re.error``; either escaping would abort loading the whole file.
    rules, warnings = ClaudeHooksParser().parse_dict(_group(matcher), plugin_name="p")
    assert len(rules) == 1 and len(warnings) == 1
    assert _claude_matcher_match(matcher, "Read") is False
    assert _claude_matcher_match(matcher, matcher) is True
