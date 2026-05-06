"""Stop hook ``blockingError`` Agentao-internal field.

A Stop hook returning ``{"blockingError": "lint failed"}`` produces a
``StopHookResult`` with ``blocking_error`` set and a
``hook_blocking_error`` attachment. Chat-loop wiring translates this
into a ``[Blocked by Stop hook] ...`` final message and does not
reissue an LLM call.
"""

from __future__ import annotations

import json

from agentao.plugins.hooks import PluginHookDispatcher
from agentao.plugins.models import ParsedHookRule, StopHookResult


def test_blocking_error_recorded_and_short_circuits():
    dispatcher = PluginHookDispatcher()
    rule = ParsedHookRule(
        event="Stop", hook_type="command", command="x", plugin_name="t",
    )
    result = StopHookResult(matched_rule_count=1)
    dispatcher._parse_stop_command_output(
        json.dumps({"blockingError": "lint failed"}), rule, result,
    )

    assert result.blocking_error == "lint failed"
    assert result.force_continue is False
    types = [m.attachment_type for m in result.messages]
    assert "hook_blocking_error" in types
