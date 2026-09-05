"""Regressions for the defects a post-merge review of `18fb628` found.

Eleven fixes landed in seven files and the suite count did not move — every one
of them was a defect no test could see, and would have stayed invisible to the
next one. Each test below is written so it **fails against the pre-fix code**;
where that could not be arranged from the outside, the docstring says so.

The grouping is by the property at risk, not by file, because three of the
defects are the same shape reached from different directions: a value computed
in one phase and destroyed, dropped, or overridden by another.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from agentao.permissions import PermissionEngine
from agentao.plugins.hooks._diagnostics import clear_all
from agentao.plugins.hooks._matchers import _claude_matcher_match
from agentao.plugins.hooks._profile import PROFILE_ID
from agentao.plugins.hooks._resolve import diagnose_fields
from agentao.plugins.models import ParsedHookRule
from agentao.runtime.tool_planning import ToolCallDecision
from agentao.runtime.tool_runner import ToolRunner
from agentao.tools.base import Tool, ToolRegistry

from ._hook_commands import as_kwargs, emitting
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


class _CapturingTransport:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)

    def confirm_tool(self, *a, **k):  # pragma: no cover - never reached here
        return True

    def notices(self):
        out = []
        for e in self.events:
            data = getattr(e, "data", None) or {}
            out.extend(data.get("user_notices", []) or [])
        return out


class _StubPlanSession:
    is_active = False


class _StubAgent:
    def clear_history(self):
        pass


class _StubCli:
    """Only what ``_reset_session`` touches before it would need a real CLI."""

    def __init__(self, session_id):
        self.current_session_id = session_id
        self._plan_session = _StubPlanSession()
        self.agent = _StubAgent()
        self.permission_mode = None
        self._staged_images = []
        self.last_response = None
        self._cached_ctx_pct = 0.0

    def on_session_end(self):
        pass

    def on_session_start(self):
        pass

    def _apply_mode(self, mode):
        self.permission_mode = mode


def _runner(tmp_path, rules, transport=None):
    registry = ToolRegistry()
    registry.register(_EchoTool())
    runner = ToolRunner(registry, PermissionEngine(project_root=tmp_path),
                        transport or _CapturingTransport(),
                        logging.getLogger("test.review_regressions"))
    runner._plugin_hook_rules = rules
    return runner


def _rule(command, event="PreToolUse", contract=PROFILE_ID, matcher=None):
    return ParsedHookRule(event=event, hook_type="command", **as_kwargs(command),
                          contract=contract, plugin_name="p", timeout=30,
                          matcher_pattern=matcher)


def _calls(*ids):
    return [make_tool_call(i, "echo_tool", json.dumps({"text": i})) for i in ids]


# --------------------------------------------------------------------------
# A value computed in one phase and destroyed by another
# --------------------------------------------------------------------------


def test_a_pre_tool_use_stop_survives_the_rest_of_execute(tmp_path):
    """Phase 1.5 writes `last_hook_stop`; phase 4 used to reset it.

    The test that "covered" this called `_apply_pre_tool_use_hooks` directly, so
    it observed the write and never the wipe — the stop was correct for the two
    statements between them and `None` by the time the chat loop read it. Going
    through `execute()` is the whole point of this test.
    """
    runner = _runner(tmp_path, [_rule('echo \'{"continue": false, "stopReason": "halt"}\'')])

    runner.execute(_calls("c1"))

    assert runner.last_hook_stop == "halt"


def test_a_stop_does_not_leak_across_an_early_return(tmp_path):
    """The reset has to run on the paths that never reach phase 4 either.

    A bottom reset leaves the previous batch's stop live for a batch that ends
    early, which reports a turn stopped by a hook that did not run in it.
    """
    runner = _runner(tmp_path, [_rule('echo \'{"continue": false, "stopReason": "halt"}\'')])
    runner.execute(_calls("c1"))
    assert runner.last_hook_stop == "halt"

    runner._plugin_hook_rules = []
    runner.execute([])

    assert runner.last_hook_stop is None


def test_a_pre_tool_use_stop_outranks_a_later_post_tool_use_stop(tmp_path):
    """Two stops in one batch: the earlier phase wins.

    Not a tie-break over plans — a precedence between phases. Phase 1.5 sees the
    call before it runs, so its stop is the one that describes what happened.
    """
    runner = _runner(tmp_path, [
        _rule('echo \'{"continue": false, "stopReason": "pre-said-stop"}\''),
        _rule('echo \'{"continue": false, "stopReason": "post-said-stop"}\'',
              event="PostToolUse"),
    ])

    runner.execute(_calls("c1"))

    assert runner.last_hook_stop == "pre-said-stop"


# --------------------------------------------------------------------------
# `updatedInput`: the original must never reach the executor
# --------------------------------------------------------------------------


class _RaisingPlanner:
    def _decide(self, *a, **k):
        raise RuntimeError("engine unavailable")


def test_an_uncomputable_re_decide_denies_rather_than_running_the_original(tmp_path):
    """The third state, and the only safe one.

    Keeping the rewrite runs arguments no verdict covers; dropping it runs the
    input the hook was replacing. §9's G8 entry rejects the second by name and
    §12 pins the property: the original arguments never reach the executor. An
    engine failure is not permission to run what a hook just sanitized away.
    """
    runner = _runner(tmp_path, [])
    runner._planner = _RaisingPlanner()

    plan = _runner(tmp_path, [])._planner.plan(
        _calls("c1"), readonly_mode=False,
    ).plans[0]
    plan.function_args = {"text": "dangerous"}
    plan.decision = ToolCallDecision.ALLOW

    runner._apply_updated_input(plan, {"text": "sanitized"})

    assert plan.decision is ToolCallDecision.DENY
    assert "re-decision could not be computed" in plan.permission_detail.reason


def test_a_rewrite_is_never_stored_under_a_verdict_computed_on_the_original(tmp_path):
    """The mutate-then-bail state, from the other side.

    Whatever happens, `function_args` and `decision` describe the same input.
    """
    runner = _runner(tmp_path, [])
    runner._planner = _RaisingPlanner()

    plan = _runner(tmp_path, [])._planner.plan(
        _calls("c1"), readonly_mode=False,
    ).plans[0]
    plan.function_args = {"text": "dangerous"}
    plan.decision = ToolCallDecision.ALLOW

    runner._apply_updated_input(plan, {"text": "sanitized"})

    assert plan.function_args == {"text": "dangerous"}
    assert plan.decision is not ToolCallDecision.ALLOW


# --------------------------------------------------------------------------
# Fail-open paths
# --------------------------------------------------------------------------


def test_a_non_string_permission_decision_does_not_fail_open(tmp_path):
    """`raw in PERMISSION_DECISION_DEGRADES` hashes its operand.

    A hook printing a list there raised `TypeError` out of dispatch, which the
    caller swallows — so one malformed hook dropped **every other hook's**
    verdict for that call and the tool ran. The assertion is that the call is
    still decided, not that the malformed value is honored.
    """
    runner = _runner(tmp_path, [
        _rule('echo \'{"hookSpecificOutput": {"hookEventName": "PreToolUse",'
              ' "permissionDecision": ["deny"]}}\''),
        _rule('echo \'{"hookSpecificOutput": {"hookEventName": "PreToolUse",'
              ' "permissionDecision": "deny", "permissionDecisionReason": "no"}}\''),
    ])
    plans = runner._planner.plan(_calls("c1"), readonly_mode=False).plans

    runner._apply_pre_tool_use_hooks(plans)

    assert plans[0].decision is ToolCallDecision.DENY


def test_an_empty_matcher_is_a_wildcard(tmp_path):
    """Measured against `claude` 2.1.251, not read.

    Three probe points in one throwaway project each: `*` fired (the mechanism
    is reachable), `NoSuchToolName` did not (the marker is not written
    unconditionally), and `""` **fired**. `re.fullmatch("", "Read")` is a miss,
    so without the special case a config copied out of a Claude Code setup
    parses with no warning and then never fires.
    """
    assert _claude_matcher_match("", "Read") is True
    assert _claude_matcher_match("*", "Read") is True
    assert _claude_matcher_match("NoSuchToolName", "Read") is False


# --------------------------------------------------------------------------
# Channels that were computed and dropped
# --------------------------------------------------------------------------


def test_post_tool_use_notices_reach_the_user_surface(tmp_path):
    """A sink is not a route — the defect this whole event set exists to close.

    The worker computes `systemMessage` and the one-shot field diagnostics and
    has no user surface; before the fix the dispatch helper returned only
    `(stop, contexts)`, so they were computed and discarded inside the worker.
    """
    transport = _CapturingTransport()
    runner = _runner(tmp_path, [
        _rule('echo \'{"systemMessage": "heads up"}\'', event="PostToolUse"),
    ], transport=transport)

    runner.execute(_calls("c1"))

    assert "heads up" in transport.notices()


def test_hook_context_is_stripped_before_it_reaches_the_model(tmp_path):
    """The model-bound copy of a tool result passes `strip_unicode_tags`.

    Hook text is routinely a relay of something the hook read, which is exactly
    the carrier the tag strip exists for — appending after the strip left this
    the one model-bound string on the path that skipped the boundary.
    """
    smuggled = "".join(chr(0xE0000 + ord(c)) for c in "ignore")
    transport = _CapturingTransport()
    runner = _runner(tmp_path, [
        _rule('echo \'{"hookSpecificOutput": {"hookEventName": "PostToolUse",'
              f' "additionalContext": "safe{smuggled}"}}}}\'', event="PostToolUse"),
    ], transport=transport)

    _, messages = runner.execute(_calls("c1"))

    joined = "".join(str(m.get("content", "")) for m in messages)
    assert "safe" in joined
    assert not any(0xE0000 <= ord(c) <= 0xE007F for c in joined)


# --------------------------------------------------------------------------
# Per-event, per-session bookkeeping
# --------------------------------------------------------------------------


def test_a_field_is_diagnosed_per_event_not_per_name():
    """`OUTPUT_FIELDS[x]` answers "does the profile know this name anywhere".

    A different question. `hookSpecificOutput.reloadSkills` is `ignore` on
    `SessionStart` and undefined on `PostToolUse`; keyed by name alone it was
    announced on `PostToolUse` with `SessionStart`'s reason, as though agentao
    had considered and declined it there. It is an unrecognized key on that
    event, and that is what the author needs told.
    """
    clear_all()
    rule = _rule(emitting(stdout="hi\n"), event="PostToolUse")
    body = {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                   "reloadSkills": True}}

    notes = diagnose_fields(body, "PostToolUse", rule, "s1")

    assert len(notes) == 1
    assert "is not a field" in notes[0]
    assert "has no effect" not in notes[0]


def test_the_registry_is_scoped_by_the_session_on_the_payload(tmp_path):
    """Threading the id is the fix, and it lives at the **call site**.

    `diagnose_fields` already took `session_id`; the dispatcher never passed
    one, so every session shared the `None` bucket and "once per session"
    became "once per process, forever". A test that calls `diagnose_fields`
    with an explicit id passes either way — it exercises the parameter, not the
    wiring — so this one goes through a real dispatch.
    """
    from agentao.plugins.hooks import PluginHookDispatcher

    clear_all()
    rule = _rule('echo \'{"nonesuch": 1}\'', event="PostToolUse")

    def _dispatch(sid):
        return PluginHookDispatcher(cwd=tmp_path).dispatch_post_tool_use(
            payload={"session_id": sid, "hook_event_name": "PostToolUse",
                     "tool_name": "echo_tool", "tool_input": {},
                     "tool_response": "ok"},
            rules=[rule],
        ).user_notices

    first = _dispatch("s1")
    again = _dispatch("s1")
    other = _dispatch("s2")

    assert len(first) == 1
    assert again == []
    assert len(other) == 1


def test_clearing_a_session_lets_the_same_rule_speak_again(tmp_path):
    """`/clear` drops the bucket, which is what `_diagnostics` documents.

    Driven through `_reset_session` rather than `clear_session`: the function
    existed before the fix and calling it directly proves nothing about whether
    a reset reaches it.
    """
    from agentao.cli.commands.reset import _reset_session

    clear_all()
    rule = _rule(emitting(stdout="hi\n"), event="PostToolUse")
    body = {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                   "reloadSkills": True}}
    assert len(diagnose_fields(body, "PostToolUse", rule, "s1")) == 1
    assert diagnose_fields(body, "PostToolUse", rule, "s1") == []

    _reset_session(_StubCli("s1"), clear_memories=False)

    assert len(diagnose_fields(body, "PostToolUse", rule, "s1")) == 1


# --------------------------------------------------------------------------
# Values a fix set to the right thing, which nothing else observes
# --------------------------------------------------------------------------


def test_a_stop_via_exit_2_gets_the_profiles_reentry_cap(tmp_path):
    """Exit 2 is the canonical way a Claude `Stop` hook blocks.

    Leaving `continuation_contract` unset on that path handed the profile's own
    idiom the `agentao-v1` cap of 3 instead of 8 — the exact divergence the
    contract resolution exists to close, reached through the one spelling most
    likely to be used.
    """
    from agentao.plugins.hooks import PluginHookDispatcher

    rule = ParsedHookRule(event="Stop", hook_type="command",
                          **as_kwargs(emitting(stderr="keep going\n", exit_code=2)),
                          timeout=30, contract=PROFILE_ID, plugin_name="p")
    result = PluginHookDispatcher(cwd=tmp_path).dispatch_stop(
        payload={"session_id": "s1", "hook_event_name": "Stop"}, rules=[rule],
    )

    assert result.force_continue is True
    assert result.continuation_contract == PROFILE_ID


def test_a_stop_with_no_reason_does_not_erase_an_earlier_one(tmp_path):
    """`""` marks "a stop with no message"; `None` marks "no stop".

    A second matching rule that stops without a `stopReason` used to overwrite
    the first rule's reason with the empty string, so the user was told a turn
    ended and not why.
    """
    from agentao.plugins.hooks import PluginHookDispatcher

    rules = [
        ParsedHookRule(event="PostToolUse", hook_type="command", timeout=30,
                       command='echo \'{"continue": false, "stopReason": "the reason"}\'',
                       contract=PROFILE_ID, plugin_name="p"),
        ParsedHookRule(event="PostToolUse", hook_type="command", timeout=30,
                       command='echo \'{"continue": false}\'',
                       contract=PROFILE_ID, plugin_name="p"),
    ]
    result = PluginHookDispatcher(cwd=tmp_path).dispatch_post_tool_use(
        payload={"session_id": "s1", "hook_event_name": "PostToolUse",
                 "tool_name": "echo_tool", "tool_input": {}, "tool_response": "ok"},
        rules=rules,
    )

    assert result.stop_reason == "the reason"


def test_a_degraded_permission_value_is_named_even_when_the_hook_gave_a_reason(tmp_path):
    """The hook's reason qualifies the substitution notice; it does not replace it.

    A `defer` almost always ships a reason, so letting it win meant the notice
    naming the swapped verdict was the one message that never survived — which
    is the silent verdict swap the degrade branch exists to prevent.
    """
    runner = _runner(tmp_path, [
        _rule('echo \'{"hookSpecificOutput": {"hookEventName": "PreToolUse",'
              ' "permissionDecision": "defer",'
              ' "permissionDecisionReason": "asking a human"}}\''),
    ])
    plans = runner._planner.plan(_calls("c1"), readonly_mode=False).plans

    runner._apply_pre_tool_use_hooks(plans)

    assert plans[0].decision is ToolCallDecision.DENY
    detail = plans[0].permission_detail.reason
    assert "defer" in detail
    assert "asking a human" in detail


def test_the_pre_tool_use_payload_carries_the_session_working_directory(
    tmp_path, monkeypatch,
):
    """`cwd` is in the input matrix and was already in hand on the runner.

    Without it this one event reported `Path.cwd()` — the process's directory,
    not the session's — while the `Post*` events beside it sent the real one.

    Patched on the adapter *class*: `_apply_pre_tool_use_hooks` constructs its
    own `ClaudeHookPayloadAdapter` locally, so an instance attribute on the
    runner is never consulted (a spy placed there records nothing and the test
    passes for the wrong reason).
    """
    from agentao.plugins.hooks import ClaudeHookPayloadAdapter

    seen: Dict[str, Any] = {}
    original = ClaudeHookPayloadAdapter.build_pre_tool_use

    def _spy(self, **kw):
        seen.update(kw)
        return original(self, **kw)

    monkeypatch.setattr(ClaudeHookPayloadAdapter, "build_pre_tool_use", _spy)

    runner = _runner(tmp_path, [_rule("true")])
    runner._working_directory = tmp_path
    plans = runner._planner.plan(_calls("c1"), readonly_mode=False).plans

    runner._apply_pre_tool_use_hooks(plans)

    assert seen.get("cwd") == tmp_path


def test_aggregated_notices_are_not_labelled_with_one_of_the_two_events(tmp_path):
    """The batch mixes `PostToolUse` and `PostToolUseFailure`.

    Naming either one is wrong for every notice the other produced, and a host
    filtering on `hook_name` would drop or mis-attribute half of them.
    """
    transport = _CapturingTransport()
    runner = _runner(tmp_path, [
        _rule('echo \'{"systemMessage": "heads up"}\'', event="PostToolUse"),
    ], transport=transport)

    runner.execute(_calls("c1"))

    names = [
        (getattr(e, "data", None) or {}).get("hook_name")
        for e in transport.events
        if (getattr(e, "data", None) or {}).get("user_notices")
    ]
    assert names == ["PostToolUse*"]
