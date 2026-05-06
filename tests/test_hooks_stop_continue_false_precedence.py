"""``continue: false`` precedence rule.

Claude Code documents ``{"continue": false}`` as taking precedence
over any event-specific decision field — so a hook returning
``{"continue": false, "decision": "block"}`` accepts the stop, it
does NOT force a continue. Exception: ``blockingError`` is independent
of ``continue: false`` because both intents agree on "stop the turn."
"""

from __future__ import annotations

import json

from agentao.plugins.hooks import PluginHookDispatcher
from agentao.plugins.models import ParsedHookRule, StopHookResult


def _parse(stdout_payload: dict) -> StopHookResult:
    dispatcher = PluginHookDispatcher()
    rule = ParsedHookRule(
        event="Stop", hook_type="command", command="x", plugin_name="t",
    )
    result = StopHookResult(matched_rule_count=1)
    dispatcher._parse_stop_command_output(json.dumps(stdout_payload), rule, result)
    return result


def test_continue_false_overrides_decision_block():
    """``continue: false`` + ``decision: "block"`` must NOT
    force_continue. The reason is still recorded for replay, but the
    loop ends the turn."""
    result = _parse({
        "continue": False, "decision": "block", "reason": "ignore me",
    })
    assert result.force_continue is False
    assert result.stop_reason == "ignore me"


def test_continue_false_overrides_prevent_continuation():
    result = _parse({
        "continue": False, "preventContinuation": True, "stopReason": "noop",
    })
    assert result.force_continue is False


def test_continue_false_does_not_suppress_blocking_error():
    """Both intents agree on "stop the turn," so blockingError still
    fires."""
    result = _parse({"continue": False, "blockingError": "lint failed"})
    assert result.blocking_error == "lint failed"
