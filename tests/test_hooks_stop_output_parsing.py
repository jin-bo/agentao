"""How a Stop hook's stdout JSON maps onto ``StopHookResult`` (``agentao-v1``).

Drives the parser directly with a fabricated stdout string. The
``claude-code@profile-1`` reading of the same fields is in
``test_hooks_ups_and_stop_channels.py``; the chat-loop wiring that acts on
the result is in ``test_hooks_stop_wiring_block_and_continue.py``.
"""

from __future__ import annotations

import json

import pytest

from agentao.plugins.hooks import PluginHookDispatcher
from agentao.plugins.hooks._profile import LEGACY_CONTRACT_ID
from agentao.plugins.models import ParsedHookRule, StopHookResult


def _parse(stdout_payload: dict) -> StopHookResult:
    dispatcher = PluginHookDispatcher()
    rule = ParsedHookRule(
        event="Stop", hook_type="command", command="x", plugin_name="t",
        contract=LEGACY_CONTRACT_ID,
    )
    result = StopHookResult(matched_rule_count=1)
    dispatcher._parse_stop_command_output(json.dumps(stdout_payload), rule, result)
    return result


@pytest.mark.parametrize("payload, expected", [
    pytest.param(
        {"decision": "block", "reason": "needs more work"},
        {"force_continue": True, "follow_up_message": "needs more work",
         "stop_reason": "needs more work", "blocking_error": None},
        id="decision-block-with-reason-forces-a-continue",
    ),
    # Only a ``reason`` string makes ``decision: "block"`` a force signal.
    pytest.param(
        {"decision": "block"},
        {"force_continue": False, "follow_up_message": None},
        id="decision-block-without-reason-is-a-noop",
    ),
    # Claude Code documents ``continue: false`` as taking precedence over any
    # event-specific decision field: the stop is accepted, and the reason is
    # still recorded for replay.
    pytest.param(
        {"continue": False, "decision": "block", "reason": "ignore me"},
        {"force_continue": False, "stop_reason": "ignore me"},
        id="continue-false-overrides-decision-block",
    ),
    pytest.param(
        {"continue": False, "preventContinuation": True, "stopReason": "noop"},
        {"force_continue": False},
        id="continue-false-overrides-prevent-continuation",
    ),
    # Both intents agree on "stop the turn", so ``blockingError`` still fires.
    pytest.param(
        {"continue": False, "blockingError": "lint failed"},
        {"blocking_error": "lint failed"},
        id="continue-false-keeps-blocking-error",
    ),
    # Agentao-internal: the chat loop ends the turn with a
    # ``[Blocked by Stop hook] ...`` message and makes no further LLM call.
    pytest.param(
        {"blockingError": "lint failed"},
        {"blocking_error": "lint failed", "force_continue": False},
        id="blocking-error-is-recorded",
    ),
    pytest.param(
        {"suppressOutput": True}, {"suppress_output": True},
        id="suppress-output-true",
    ),
    pytest.param(
        {"suppressOutput": False}, {"suppress_output": False},
        id="suppress-output-false",
    ),
    pytest.param(
        {"systemMessage": "ran lint, all clean"},
        {"system_message": "ran lint, all clean"},
        id="system-message-is-recorded",
    ),
])
def test_a_stop_output_field_sets_the_result(payload, expected):
    result = _parse(payload)
    actual = {name: getattr(result, name) for name in expected}
    assert actual == expected
    # ``==`` takes 1 for True: a boolean field must hold the boolean itself.
    for name, value in expected.items():
        if isinstance(value, bool):
            assert actual[name] is value, name


@pytest.mark.parametrize("payload, contexts", [
    # v1 also hands ``systemMessage`` to the model.
    pytest.param(
        {"systemMessage": "ran lint, all clean"}, ["ran lint, all clean"],
        id="system-message",
    ),
    pytest.param(
        {"hookSpecificOutput": {"additionalContext": "audit-note"}}, ["audit-note"],
        id="hook-specific-string",
    ),
    pytest.param(
        {"hookSpecificOutput": {"additionalContext": ["a", "b"]}}, ["a", "b"],
        id="hook-specific-list",
    ),
    # Tolerated for older hook scripts without the ``hookSpecificOutput`` envelope.
    pytest.param(
        {"additionalContext": "legacy-note"}, ["legacy-note"],
        id="legacy-top-level",
    ),
])
def test_a_stop_output_reaches_additional_contexts(payload, contexts):
    result = _parse(payload)
    for context in contexts:
        assert context in result.additional_contexts


def test_a_blocking_error_carries_its_attachment():
    result = _parse({"blockingError": "lint failed"})
    types = [m.attachment_type for m in result.messages]
    assert "hook_blocking_error" in types
