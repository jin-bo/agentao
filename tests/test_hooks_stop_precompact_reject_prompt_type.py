"""Per-event hook-type rejection — prompt-type rules under Stop or
PreCompact must be dropped at parse time with an event-specific warning,
and ``ParsedHookRule.is_supported`` must agree at runtime.

This pins A1's two-layer defense: the parser drops the rule before
``rules.append``, AND ``is_supported`` flips to ``False`` for any rule
constructed directly without going through the loader.
"""

from __future__ import annotations

import subprocess

from agentao.plugins.hooks import (
    ClaudeHookPayloadAdapter,
    ClaudeHooksParser,
    PluginHookDispatcher,
)
from agentao.plugins.models import ParsedHookRule, PluginWarning


def test_stop_prompt_type_dropped_at_parse_time():
    parser = ClaudeHooksParser()
    rules, warnings = parser.parse_dict({
        "hooks": {
            "Stop": [
                {"type": "prompt", "prompt": "be careful"},
            ],
        },
    }, plugin_name="t")

    assert rules == []
    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, PluginWarning)
    # Must be the per-event rejection branch, NOT the generic "Unknown
    # hook type" fallback — distinguishable by message text.
    assert "prompt" in w.message
    assert "Stop" in w.message
    assert "is not supported for event" in w.message
    assert w.field == "hooks"


def test_pre_compact_prompt_type_dropped_at_parse_time():
    parser = ClaudeHooksParser()
    rules, warnings = parser.parse_dict({
        "hooks": {
            "PreCompact": [
                {"type": "prompt", "prompt": "fyi"},
            ],
        },
    }, plugin_name="t")

    assert rules == []
    assert len(warnings) == 1
    assert "PreCompact" in warnings[0].message
    assert "is not supported for event" in warnings[0].message


def test_stop_prompt_type_is_supported_returns_false():
    """Defense-in-depth: a directly-constructed rule (bypassing the parser)
    must still flip is_supported to False."""
    rule = ParsedHookRule(event="Stop", hook_type="prompt", prompt="x")
    assert rule.is_supported is False


def test_pre_compact_prompt_type_is_supported_returns_false():
    rule = ParsedHookRule(event="PreCompact", hook_type="prompt", prompt="x")
    assert rule.is_supported is False


def test_stop_command_type_is_supported_true():
    """Sanity: command rules under Stop / PreCompact still load fine."""
    rule = ParsedHookRule(event="Stop", hook_type="command", command="echo")
    assert rule.is_supported is True
    rule2 = ParsedHookRule(event="PreCompact", hook_type="command", command="echo")
    assert rule2.is_supported is True


def test_no_subprocess_invoked_for_empty_rules(tmp_path, monkeypatch):
    """If the parser drops every rule, dispatch with the empty list must
    not invoke any subprocess."""
    parser = ClaudeHooksParser()
    rules, _warnings = parser.parse_dict({
        "hooks": {
            "Stop": [{"type": "prompt", "prompt": "x"}],
        },
    }, plugin_name="t")
    assert rules == []

    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("subprocess.run must not be invoked when rules is empty")

    monkeypatch.setattr(subprocess, "run", fake_run)

    payload = ClaudeHookPayloadAdapter().build_stop(
        cwd=tmp_path,
        last_assistant_message="",
        turn_end_reason="final_response",
    )
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    result = dispatcher.dispatch_stop(payload=payload, rules=rules)

    # Phase B: dispatch_stop returns StopHookResult; empty rule list
    # produces a result with matched_rule_count == 0 and no attachments.
    assert result.matched_rule_count == 0
    assert result.messages == []
    assert calls == []
