"""The ``PreToolUse`` lifecycle — step 6 of the plan (G8).

Two halves, and the front one is the half that gets omitted: **when the hook
fires at all**. agentao skipped any call the permission engine had already
denied — sound while a hook can only *tighten* a verdict, and wrong the moment
the contract says the hook must **observe** the call.

The back half is `updatedInput`, which is not a field to carry but a re-entry:
it "replaces the entire input object", so the verdict computed in phase 1
describes arguments that no longer exist.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from agentao.permissions import PermissionEngine
from agentao.plugins.hooks._profile import LEGACY_CONTRACT_ID, PROFILE_ID
from agentao.plugins.models import ParsedHookRule
from agentao.runtime.tool_planning import ToolCallDecision
from agentao.runtime.tool_runner import ToolRunner
from agentao.tools.base import Tool, ToolRegistry

from ._hook_commands import as_kwargs, emits_json, emitting
from tests.support.tool_calls import make_tool_call


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
                        _NullTransport(), logging.getLogger("test.pretool"))
    runner._plugin_hook_rules = rules
    return runner


def _rule(command, contract=PROFILE_ID):
    return ParsedHookRule(event="PreToolUse", hook_type="command", **as_kwargs(command),
                          contract=contract, plugin_name="p", timeout=30)


def _plan(runner, args=None):
    calls = [make_tool_call("c1", "echo_tool", json.dumps(args or {"text": "hi"}))]
    return runner._planner.plan(calls, readonly_mode=False).plans


# --------------------------------------------------------------------------
# When the hook fires
# --------------------------------------------------------------------------

def test_a_denied_call_still_fires_the_hook_under_the_profile(tmp_path):
    marker = tmp_path / "fired.txt"
    runner = _runner(tmp_path, [_rule(f"echo fired > {marker}")])
    plans = _plan(runner)
    plans[0].decision = ToolCallDecision.DENY

    runner._apply_pre_tool_use_hooks(plans)

    assert marker.exists(), "an audit hook must see the calls it exists to record"
    assert plans[0].decision is ToolCallDecision.DENY, "observation is not authority"


def test_a_denied_call_does_not_fire_a_v1_hook(tmp_path):
    """The skip stays under `agentao-v1`, which is frozen. Without this test the
    two modes converge silently the first time someone simplifies the branch."""
    marker = tmp_path / "fired.txt"
    runner = _runner(tmp_path, [_rule(f"echo fired > {marker}", contract=LEGACY_CONTRACT_ID)])
    plans = _plan(runner)
    plans[0].decision = ToolCallDecision.DENY

    runner._apply_pre_tool_use_hooks(plans)

    assert not marker.exists()


# --------------------------------------------------------------------------
# updatedInput is a re-entry, not a field
# --------------------------------------------------------------------------

def test_updated_input_replaces_the_arguments(tmp_path):
    runner = _runner(tmp_path, [_rule(
        emits_json('{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": {"text": "rewritten"}}}')
    )])
    plans = _plan(runner, {"text": "original"})

    runner._apply_pre_tool_use_hooks(plans)

    assert plans[0].function_args == {"text": "rewritten"}


def test_a_rewrite_is_re_decided_and_can_only_get_stricter(tmp_path, monkeypatch):
    """The point of the gate: the verdict must describe what will actually run.
    A hook `allow` cannot lift the re-computed verdict."""
    runner = _runner(tmp_path, [_rule(
        emits_json('{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": {"text": "dangerous"}}}')
    )])
    plans = _plan(runner, {"text": "benign"})

    # The engine denies whatever the rewrite produced.
    # The 5th parameter is the shell spec the first decision was frozen against; the
    # re-decision is required to be made against the same one (TOOL-04/SPEC-08).
    def _deny(tool, name, args, readonly, shell_spec=None, decided=None):
        from agentao.runtime.tool_planning import ToolCallDecision as D
        return (D.DENY, None) if args.get("text") == "dangerous" else (D.ALLOW, None)

    monkeypatch.setattr(runner._planner, "_decide", _deny)

    runner._apply_pre_tool_use_hooks(plans)

    assert plans[0].function_args == {"text": "dangerous"}
    assert plans[0].decision is ToolCallDecision.DENY


def test_the_re_decide_never_loosens_an_existing_verdict(tmp_path, monkeypatch):
    runner = _runner(tmp_path, [_rule(
        emits_json('{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": {"text": "harmless"}}}')
    )])
    plans = _plan(runner)
    plans[0].decision = ToolCallDecision.ASK

    monkeypatch.setattr(runner._planner, "_decide",
                        lambda *a, **k: (ToolCallDecision.ALLOW, None))

    runner._apply_pre_tool_use_hooks(plans)

    assert plans[0].decision is ToolCallDecision.ASK


def test_a_v1_hook_cannot_rewrite_the_input(tmp_path):
    runner = _runner(tmp_path, [_rule(
        emits_json('{"hookSpecificOutput": {"updatedInput": {"text": "rewritten"}}}'),
        contract=LEGACY_CONTRACT_ID,
    )])
    plans = _plan(runner, {"text": "original"})

    runner._apply_pre_tool_use_hooks(plans)

    assert plans[0].function_args == {"text": "original"}


# --------------------------------------------------------------------------
# The other three channels
# --------------------------------------------------------------------------

def test_additional_context_is_injected_beside_the_result(tmp_path):
    """Deviation 6: it used to be parsed and *logged*, which is not a route."""
    runner = _runner(tmp_path, [_rule(
        emits_json('{"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "mind the policy"}}')
    )])

    _, messages = runner.execute([make_tool_call("c1", "echo_tool", json.dumps({"text": "hi"}))])

    assert "echoed hi" in messages[0]["content"]
    assert "mind the policy" in messages[0]["content"]


def test_continue_false_ends_the_turn_and_is_not_recorded_as_a_deny(tmp_path):
    """A stop and a deny are different outcomes for the user: one ends the turn,
    the other blocks a call and lets the model try something else."""
    runner = _runner(tmp_path, [_rule(emits_json('{"continue": false, "stopReason": "halt"}'))])
    plans = _plan(runner)

    runner._apply_pre_tool_use_hooks(plans)

    assert runner.last_hook_stop == "halt"
    assert plans[0].decision is ToolCallDecision.ALLOW


def test_exit_2_denies_the_call(tmp_path):
    runner = _runner(tmp_path, [_rule(emitting(stderr="not allowed\n", exit_code=2))])
    plans = _plan(runner)

    runner._apply_pre_tool_use_hooks(plans)

    assert plans[0].decision is ToolCallDecision.DENY
    assert "not allowed" in (plans[0].permission_detail.reason or "")


def test_defer_degrades_to_deny_with_the_value_named(tmp_path):
    """§1's third rule reaches values: accept, ignore, or degrade to a **named**
    alternative — never a silent substitution of one verdict for another."""
    runner = _runner(tmp_path, [_rule(
        emits_json('{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "defer"}}')
    )])
    plans = _plan(runner)

    runner._apply_pre_tool_use_hooks(plans)

    assert plans[0].decision is ToolCallDecision.DENY
    reason = plans[0].permission_detail.reason or ""
    assert "defer" in reason and "degraded" in reason


def test_a_v1_hook_emitting_defer_is_ignored(tmp_path):
    runner = _runner(tmp_path, [_rule(
        emits_json('{"hookSpecificOutput": {"permissionDecision": "defer"}}'),
        contract=LEGACY_CONTRACT_ID,
    )])
    plans = _plan(runner)

    runner._apply_pre_tool_use_hooks(plans)

    assert plans[0].decision is ToolCallDecision.ALLOW


def test_the_hook_fires_once_and_is_not_re_dispatched(tmp_path):
    counter = tmp_path / "count.txt"
    runner = _runner(tmp_path, [_rule(emits_json(
        '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": '
        '"allow", "updatedInput": {"text": "new"}}}',
        touch=(counter,), touch_text="x",
    ))])
    plans = _plan(runner)

    runner._apply_pre_tool_use_hooks(plans)

    assert counter.read_text().count("x") == 1
