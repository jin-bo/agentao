"""Mixed-contract dispatch — step 6b (G9).

One event's rule list can hold **both** contracts: the contract is file-scoped
and `resolve_all_hook_rules` concatenates every plugin's every spec into one flat
list. Every earlier revision of the design described the two modes as if a
session only ever had one.

The property under test is the same on every event: **a v1 rule's short-circuit
ends only the v1 group.** Its side effects — an audit line, a notification, a
marker file — are the whole reason the all-handlers rule exists, and nothing
tells their author when they silently stop happening.

**Two shapes, not one template.** On the four events where both contracts carry a
decision, the v1 rule blocks and the profile rule behind it must still run. On
`PostToolUse` / `PostToolUseFailure` that setup is unconstructible — `agentao-v1`
routes both through `_dispatch_lifecycle` and gives them no stdout decision
surface — so there the v1 rule contributes an observable **side effect** and the
profile rule contributes the control.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from agentao.permissions import PermissionEngine
from agentao.plugins.hooks import PluginHookDispatcher
from agentao.plugins.hooks._profile import LEGACY_CONTRACT_ID, PROFILE_ID
from agentao.plugins.models import ParsedHookRule
from agentao.runtime.tool_runner import ToolRunner
from agentao.tools.base import Tool, ToolRegistry
from tests.support.tool_calls import make_tool_call


def rule(event, command, contract):
    return ParsedHookRule(event=event, hook_type="command", command=command,
                          contract=contract, plugin_name="p", timeout=30)


def marker(tmp_path, name):
    """A hook that proves it ran, and a reader for the proof."""
    path = tmp_path / name
    return path, f"echo ran > {path}"


# --------------------------------------------------------------------------
# Shape 1 — both contracts carry a decision
# --------------------------------------------------------------------------

def test_pre_tool_use_a_v1_deny_does_not_suppress_the_profile_rule(tmp_path):
    proof, side_effect = marker(tmp_path, "profile_ran.txt")
    rules = [
        # v1 first in declaration order, and it denies.
        rule("PreToolUse", """echo '{"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "v1 says no"}}'""",
             LEGACY_CONTRACT_ID),
        rule("PreToolUse", f"""{side_effect}; echo '{{"hookSpecificOutput": {{"hookEventName": "PreToolUse", "permissionDecision": "ask"}}}}'""",
             PROFILE_ID),
    ]
    dispatcher = PluginHookDispatcher(cwd=tmp_path)

    result = dispatcher.dispatch_pre_tool_use_decision(
        payload={"hook_event_name": "PreToolUse", "tool_name": "Bash"}, rules=rules,
    )

    assert proof.exists(), "the profile rule must run even though a v1 rule denied"
    assert result.decision == "deny"                     # deny > ask
    assert result.reason and "v1 says no" in result.reason   # declaration-order winner


def test_user_prompt_submit_a_v1_block_does_not_suppress_the_profile_rule(tmp_path):
    proof, side_effect = marker(tmp_path, "ups_profile_ran.txt")
    rules = [
        rule("UserPromptSubmit", """echo '{"blockingError": "v1 blocked"}'""", LEGACY_CONTRACT_ID),
        rule("UserPromptSubmit", f"""{side_effect}; echo '{{"systemMessage": "profile note"}}'""", PROFILE_ID),
    ]
    dispatcher = PluginHookDispatcher(cwd=tmp_path)

    result = dispatcher.dispatch_user_prompt_submit(
        payload={"hook_event_name": "UserPromptSubmit"}, rules=rules,
    )

    assert proof.exists()
    assert result.blocking_error == "v1 blocked"
    assert result.user_notices == ["profile note"]       # channels are orthogonal


def test_stop_a_v1_blocking_error_does_not_suppress_the_profile_rule(tmp_path):
    """`Stop` is the one place the two contracts mean **opposite** things: v1's
    `blockingError` ends the turn, the profile's continuation keeps it going."""
    proof, side_effect = marker(tmp_path, "stop_profile_ran.txt")
    rules = [
        rule("Stop", """echo '{"blockingError": "v1 ends the turn"}'""", LEGACY_CONTRACT_ID),
        rule("Stop", f"""{side_effect}; echo '{{"hookSpecificOutput": {{"hookEventName": "Stop", "additionalContext": "keep going"}}}}'""",
             PROFILE_ID),
    ]
    dispatcher = PluginHookDispatcher(cwd=tmp_path)

    result = dispatcher.dispatch_stop(payload={"hook_event_name": "Stop"}, rules=rules)

    assert proof.exists()
    assert result.blocking_error == "v1 ends the turn"


def test_pre_compact_a_v1_cancel_does_not_suppress_the_profile_rule(tmp_path):
    proof, side_effect = marker(tmp_path, "precompact_profile_ran.txt")
    rules = [
        rule("PreCompact", """echo '{"hookSpecificOutput": {"compactionDecision": "cancel"}}'""",
             LEGACY_CONTRACT_ID),
        rule("PreCompact", side_effect, PROFILE_ID),
    ]
    dispatcher = PluginHookDispatcher(cwd=tmp_path)

    result = dispatcher.dispatch_pre_compact_decision(
        payload={"hook_event_name": "PreCompact", "trigger": "manual"}, rules=rules,
    )

    assert proof.exists()
    assert result.decision == "cancel"


# --------------------------------------------------------------------------
# Shape 2 — only the profile has a decision
# --------------------------------------------------------------------------

class _EchoTool(Tool):
    @property
    def name(self) -> str:
        return "echo_tool"

    @property
    def description(self) -> str:
        return "echoes"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {"text": {"type": "string"}},
                "required": ["text"]}

    def execute(self, text: str) -> str:
        return f"echoed {text}"


class _NullTransport:
    def emit(self, event):
        pass


def _runner(tmp_path, rules):
    registry = ToolRegistry()
    registry.register(_EchoTool())
    runner = ToolRunner(registry, PermissionEngine(project_root=tmp_path),
                        _NullTransport(), logging.getLogger("test.mixed"))
    runner._plugin_hook_rules = rules
    return runner


def _one_call():
    return [make_tool_call("c1", "echo_tool", json.dumps({"text": "hi"}))]


def test_post_tool_use_block_preserves_the_output_and_continues(tmp_path):
    """`decision:"block"` on this event is **feedback**: the original output is
    preserved, the reason rides beside it, and the turn continues. Only
    `continue:false` stops — the word "block" is the trap."""
    proof, side_effect = marker(tmp_path, "v1_side_effect.txt")
    runner = _runner(tmp_path, [
        rule("PostToolUse", side_effect, LEGACY_CONTRACT_ID),
        rule("PostToolUse",
             """echo '{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "annotated"}}'""",
             PROFILE_ID),
    ])

    _, messages = runner.execute(_one_call())

    assert proof.exists(), "the v1 handler must have run"
    assert "echoed hi" in messages[0]["content"]      # output preserved
    assert "annotated" in messages[0]["content"]      # reason beside it
    assert runner.last_hook_stop is None              # the turn continues


def test_post_tool_use_continue_false_ends_the_turn(tmp_path):
    """The second branch on the same event, because its two controls mean
    opposite things."""
    proof, side_effect = marker(tmp_path, "v1_side_effect2.txt")
    runner = _runner(tmp_path, [
        rule("PostToolUse", side_effect, LEGACY_CONTRACT_ID),
        rule("PostToolUse", """echo '{"continue": false, "stopReason": "enough"}'""", PROFILE_ID),
    ])

    _, messages = runner.execute(_one_call())

    assert proof.exists()
    assert runner.last_hook_stop == "enough"
    assert messages[0]["tool_call_id"] == "c1"        # the invariant still holds


def test_post_tool_use_failure_continue_false_is_unconditional(tmp_path):
    """`continue:false` reaches this event through the **universal** row, not
    through its contested event-level `decision`."""
    runner = _runner(tmp_path, [
        rule("PostToolUseFailure", """echo '{"continue": false, "stopReason": "stop after failure"}'""",
             PROFILE_ID),
    ])

    runner.execute([make_tool_call("c1", "echo_tool", "{}")])   # missing `text` → fails

    assert runner.last_hook_stop == "stop after failure"


# --------------------------------------------------------------------------
# The three the lattice exists for
# --------------------------------------------------------------------------

def test_ask_survives_against_allow(tmp_path):
    """`deny > ask > allow`: a hook `allow` is a no-op and must not downgrade."""
    rules = [
        rule("PreToolUse", """echo '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}'""", PROFILE_ID),
        rule("PreToolUse", """echo '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask"}}'""", PROFILE_ID),
    ]
    dispatcher = PluginHookDispatcher(cwd=tmp_path)

    result = dispatcher.dispatch_pre_tool_use_decision(
        payload={"hook_event_name": "PreToolUse", "tool_name": "Bash"}, rules=rules,
    )

    assert result.decision == "ask"


def test_a_stop_and_a_deny_are_recorded_separately(tmp_path):
    """`continue:false` ends the turn; `deny` blocks one call. Two controls, two
    outcomes for the user — the merge must not collapse them."""
    rules = [
        rule("PreToolUse", """echo '{"continue": false, "stopReason": "halt"}'""", PROFILE_ID),
        rule("PreToolUse", """echo '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "nope"}}'""", PROFILE_ID),
    ]
    dispatcher = PluginHookDispatcher(cwd=tmp_path)

    result = dispatcher.dispatch_pre_tool_use_decision(
        payload={"hook_event_name": "PreToolUse", "tool_name": "Bash"}, rules=rules,
    )

    assert result.stop_reason == "halt"
    assert result.decision == "deny"


def test_the_reason_tie_break_ranks_only_inside_the_winning_class(tmp_path):
    """Declaration-order winner *among the rules that produced the winning
    control*. Surfacing an `ask`'s reason for a `deny` would attribute the
    verdict to a rule that did not produce it."""
    rules = [
        rule("PreToolUse", """echo '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": "the ask reason"}}'""", PROFILE_ID),
        rule("PreToolUse", """echo '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "the deny reason"}}'""", PROFILE_ID),
    ]
    dispatcher = PluginHookDispatcher(cwd=tmp_path)

    result = dispatcher.dispatch_pre_tool_use_decision(
        payload={"hook_event_name": "PreToolUse", "tool_name": "Bash"}, rules=rules,
    )

    assert result.decision == "deny"
    assert result.reason == "the deny reason"


def test_a_pure_v1_setup_still_short_circuits(tmp_path):
    """The frozen behavior: with no profile rule in the list, the first deny
    still stops the walk and the second v1 hook does not run."""
    proof, side_effect = marker(tmp_path, "second_v1.txt")
    rules = [
        rule("PreToolUse", """echo '{"hookSpecificOutput": {"permissionDecision": "deny"}}'""", LEGACY_CONTRACT_ID),
        rule("PreToolUse", side_effect, LEGACY_CONTRACT_ID),
    ]
    dispatcher = PluginHookDispatcher(cwd=tmp_path)

    dispatcher.dispatch_pre_tool_use_decision(
        payload={"hook_event_name": "PreToolUse", "tool_name": "Bash"}, rules=rules,
    )

    assert not proof.exists()
