"""Stop hook ``suppressOutput`` + ``systemMessage`` +
``hookSpecificOutput.additionalContext`` parser fields.

Each Claude-documented common output field maps to the corresponding
``StopHookResult`` field. The list / string / legacy
``additionalContext`` forms all funnel into ``additional_contexts``.
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


def test_suppress_output_field_set():
    assert _parse({"suppressOutput": True}).suppress_output is True


def test_suppress_output_default_false():
    assert _parse({"suppressOutput": False}).suppress_output is False


def test_system_message_recorded_and_appended_to_contexts():
    result = _parse({"systemMessage": "ran lint, all clean"})
    assert result.system_message == "ran lint, all clean"
    # systemMessage also appends to additional_contexts.
    assert "ran lint, all clean" in result.additional_contexts


def test_hook_specific_additional_context_string():
    result = _parse({"hookSpecificOutput": {"additionalContext": "audit-note"}})
    assert "audit-note" in result.additional_contexts


def test_hook_specific_additional_context_list():
    result = _parse({"hookSpecificOutput": {"additionalContext": ["a", "b"]}})
    assert "a" in result.additional_contexts
    assert "b" in result.additional_contexts


def test_legacy_top_level_additional_context():
    """Agentao tolerance for older hook scripts that use the top-level
    ``additionalContext`` (no ``hookSpecificOutput`` envelope)."""
    result = _parse({"additionalContext": "legacy-note"})
    assert "legacy-note" in result.additional_contexts
