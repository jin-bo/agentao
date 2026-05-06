"""Stop hook ``{"decision": "block", "reason": "..."}`` JSON contract.

Drives the parser directly with a fabricated stdout string and asserts
the aggregated ``StopHookResult`` carries ``force_continue``,
``follow_up_message``, and ``stop_reason`` from the hook output.
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


def test_decision_block_sets_force_continue_and_follow_up():
    result = _parse({"decision": "block", "reason": "needs more work"})
    assert result.force_continue is True
    assert result.follow_up_message == "needs more work"
    assert result.stop_reason == "needs more work"
    assert result.blocking_error is None


def test_decision_block_without_reason_is_noop():
    """``decision: "block"`` without a ``reason`` string is not a force
    signal — the parser only flips ``force_continue`` when ``reason``
    is present (parser table conjunction)."""
    result = _parse({"decision": "block"})
    assert result.force_continue is False
    assert result.follow_up_message is None
