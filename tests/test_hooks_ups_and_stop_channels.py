"""``UserPromptSubmit``'s four channels, ``systemMessage``'s reader, and the
``Stop`` continuation — step 5 of the conformance plan.

Three deviations meet here. agentao honored **one** of `UserPromptSubmit`'s four
output channels (deviation 2). `systemMessage` — a warning for the *user* — was
appended to the model's context instead (deviation 3), and its own field had no
reader at all. And `Stop`'s `hookSpecificOutput.additionalContext` produced no
continuation (deviation 4).

`agentao-v1` keeps all three behaviors: it is frozen, and its hooks were written
against them.
"""

from __future__ import annotations

import json

import pytest

from agentao.plugins.hooks import PluginHookDispatcher
from agentao.plugins.hooks._profile import LEGACY_CONTRACT_ID, PROFILE_ID
from agentao.plugins.models import ParsedHookRule, StopHookResult, UserPromptSubmitResult


def _rule(event, contract=PROFILE_ID):
    return ParsedHookRule(event=event, hook_type="command", command="x",
                          contract=contract, plugin_name="p")


def _ups(payload, contract=PROFILE_ID):
    dispatcher = PluginHookDispatcher()
    result = UserPromptSubmitResult()
    dispatcher._parse_command_output(
        json.dumps(payload), _rule("UserPromptSubmit", contract), result,
    )
    return result


def _stop(payload, contract=PROFILE_ID):
    dispatcher = PluginHookDispatcher()
    result = StopHookResult(matched_rule_count=1)
    dispatcher._parse_stop_command_output(
        json.dumps(payload), _rule("Stop", contract), result,
    )
    return result


# --------------------------------------------------------------------------
# UserPromptSubmit — four channels, not one
# --------------------------------------------------------------------------

def test_decision_block_is_honored():
    result = _ups({"decision": "block", "reason": "off limits"})
    assert result.blocking_error == "off limits"


def test_continue_false_stops_the_turn():
    result = _ups({"continue": False, "stopReason": "enough"})
    assert result.prevent_continuation is True
    assert result.stop_reason == "enough"


def test_hook_specific_additional_context_reaches_the_model():
    result = _ups({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit", "additionalContext": "repo note",
    }})
    assert result.additional_contexts == ["repo note"]


def test_system_message_goes_to_the_user_and_not_to_the_model():
    """Deviation 3. The two readers are different, and the old code sent the
    string to the one the reference says it is not for."""
    result = _ups({"systemMessage": "heads up"})
    assert result.user_notices == ["heads up"]
    assert result.additional_contexts == []


def test_all_four_channels_at_once():
    """They are orthogonal: a hook that blocks *and* notifies does both."""
    result = _ups({
        "decision": "block", "reason": "no",
        "systemMessage": "why",
        "hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                               "additionalContext": "ctx"},
    })
    assert result.blocking_error == "no"
    assert result.user_notices == ["why"]
    assert result.additional_contexts == ["ctx"]


@pytest.mark.parametrize("payload,attr,expected", [
    ({"blockingError": "v1 block"}, "blocking_error", "v1 block"),
    ({"preventContinuation": True, "stopReason": "v1 stop"}, "stop_reason", "v1 stop"),
])
def test_v1_keys_still_work_and_the_profile_keys_do_not_apply(payload, attr, expected):
    result = _ups(payload, contract=LEGACY_CONTRACT_ID)
    assert getattr(result, attr) == expected


def test_a_v1_rule_ignores_the_profile_spellings():
    """Frozen means frozen: `decision` is not a v1 key, so a v1 hook emitting it
    is emitting an unrecognized field — not a block."""
    result = _ups({"decision": "block", "reason": "no"}, contract=LEGACY_CONTRACT_ID)
    assert result.blocking_error is None


def test_exit_2_blocks_the_prompt(tmp_path):
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    rule = ParsedHookRule(event="UserPromptSubmit", hook_type="command",
                          command="echo 'policy violation' >&2; exit 2",
                          contract=PROFILE_ID, plugin_name="p", timeout=30)
    result = UserPromptSubmitResult()

    dispatcher._run_command_hook(rule, {"hook_event_name": "UserPromptSubmit"}, result)

    assert result.blocking_error == "policy violation"


def test_exit_2_prefers_the_json_reason_when_there_is_one(tmp_path):
    """"Claude still reads the JSON, using its blocking reason when it has one
    and stderr otherwise."""
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    rule = ParsedHookRule(
        event="UserPromptSubmit", hook_type="command",
        command="""echo 'from stderr' >&2; echo '{"decision":"block","reason":"from json"}'; exit 2""",
        contract=PROFILE_ID, plugin_name="p", timeout=30,
    )
    result = UserPromptSubmitResult()

    dispatcher._run_command_hook(rule, {"hook_event_name": "UserPromptSubmit"}, result)

    assert result.blocking_error == "from json"


# --------------------------------------------------------------------------
# Stop — the continuation and its cap
# --------------------------------------------------------------------------

def test_additional_context_on_stop_is_a_continuation():
    """Deviation 4: upstream feeds it back and the conversation goes on."""
    result = _stop({"hookSpecificOutput": {
        "hookEventName": "Stop", "additionalContext": "keep going: run the tests",
    }})
    assert result.force_continue is True
    assert result.follow_up_message == "keep going: run the tests"
    assert result.continuation_contract == PROFILE_ID


def test_the_same_output_under_v1_is_context_and_not_a_continuation():
    result = _stop({"hookSpecificOutput": {
        "hookEventName": "Stop", "additionalContext": "note",
    }}, contract=LEGACY_CONTRACT_ID)
    assert result.force_continue is False
    assert "note" in result.additional_contexts


def test_continue_false_beats_the_continuation():
    result = _stop({
        "continue": False, "stopReason": "halt",
        "hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": "keep going"},
    })
    assert result.force_continue is False
    assert result.stop_reason == "halt"


def test_stop_system_message_is_not_double_written_under_the_profile():
    result = _stop({"systemMessage": "note for the user"})
    assert result.system_message == "note for the user"
    assert "note for the user" not in result.additional_contexts


def test_stop_system_message_double_write_survives_under_v1():
    """The defect is frozen where it was documented and tested."""
    result = _stop({"systemMessage": "note"}, contract=LEGACY_CONTRACT_ID)
    assert result.system_message == "note"
    assert "note" in result.additional_contexts


def test_a_block_decision_carries_its_contract_for_the_cap():
    result = _stop({"decision": "block", "reason": "keep working"})
    assert result.force_continue is True
    assert result.continuation_contract == PROFILE_ID


def test_the_profile_cap_is_the_references_number():
    from agentao.runtime.chat_loop._runner import PROFILE_STOP_REENTRY_CAP
    assert PROFILE_STOP_REENTRY_CAP == 8


# --------------------------------------------------------------------------
# The cap is contract-resolved, not a constant read
# --------------------------------------------------------------------------

def _cap_outcome(tmp_path, monkeypatch, *, contract, reentries):
    """Drive ``_resolve_stop_hook`` at ``reentries`` and report whether it capped."""
    from tests.support.stop_precompact import make_runner_with_rules

    rule = ParsedHookRule(event="Stop", hook_type="command", command="echo",
                          plugin_name="t", contract=contract)
    runner, _transport = make_runner_with_rules(tmp_path, rules=[rule])
    runner._stop_reentries = reentries

    monkeypatch.setattr(runner, "_dispatch_stop", lambda **kw: StopHookResult(
        force_continue=True,
        follow_up_message="keep going",
        matched_rule_count=1,
        continuation_contract=contract,
    ))

    step = runner._resolve_stop_hook(
        turn_end_reason="final_response",
        assistant_content="done",
        final_msg={"role": "assistant", "content": "done"},
        system_prompt="",
        incomplete_reason=None,
    )
    # A capped run returns the assistant content unchanged; a continuation does not.
    return step.action == "return" and step.value == "done"


def test_a_v1_continuation_caps_at_three(tmp_path, monkeypatch):
    assert _cap_outcome(tmp_path, monkeypatch,
                        contract=LEGACY_CONTRACT_ID, reentries=3) is True


def test_a_profile_continuation_does_not_cap_at_three(tmp_path, monkeypatch):
    """Keeping 3 under a `claude-code` label would make reentries 4 through 8
    behave differently on the two tools — the divergence the profile closes."""
    assert _cap_outcome(tmp_path, monkeypatch,
                        contract=PROFILE_ID, reentries=3) is False


def test_a_profile_continuation_caps_at_eight(tmp_path, monkeypatch):
    assert _cap_outcome(tmp_path, monkeypatch,
                        contract=PROFILE_ID, reentries=8) is True
