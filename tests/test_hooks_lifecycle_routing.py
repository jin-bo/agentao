"""Lifecycle hook channels and their per-surface routes — step 4 of the plan.

A sink is not a route. `SessionStart`, `SessionEnd` and both `PostToolUse*`
events returned `list[HookAttachmentRecord]` and nothing else, so anything a hook
decided was dropped at the call site — and on the one surface where a notice
matters most there was nothing left to carry it by the time the event fired.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentao.cli.session import dispatch_plugin_session_end, dispatch_plugin_session_start
from agentao.plugins.hooks import PluginHookDispatcher
from agentao.plugins.hooks._profile import LEGACY_CONTRACT_ID, PROFILE_ID
from agentao.plugins.models import ParsedHookRule

from ._hook_commands import emits_json, emitting


def rule(event, command, contract=PROFILE_ID):
    # ``command`` is either a shell string or the ``(command, args)`` pair the exec form
    # takes. Fixtures use the pair: cmd.exe shares none of POSIX shell's syntax, and the
    # exec form is the product's own way of not needing a shell at all.
    cmd, args = command if isinstance(command, tuple) else (command, None)
    return ParsedHookRule(event=event, hook_type="command", command=cmd, args=args,
                          contract=contract, plugin_name="p", timeout=30)


def run(event, command, tmp_path, contract=PROFILE_ID, payload=None):
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    return dispatcher._dispatch_lifecycle(
        event, payload or {"hook_event_name": event, "session_id": "s", "cwd": str(tmp_path)},
        [rule(event, command, contract)],
    )


# --------------------------------------------------------------------------
# Exit 2 is three outcomes, and the routing follows the table
# --------------------------------------------------------------------------

@pytest.mark.parametrize("event", ["SessionStart", "SessionEnd"])
def test_exit_2_stderr_reaches_the_user_on_the_session_events(event, tmp_path):
    result = run(event, emitting(stderr="trouble\n", exit_code=2), tmp_path)
    assert result.user_notices == ["trouble"]
    assert result.model_contexts == []          # and Claude does not see it


@pytest.mark.parametrize("event", ["PostToolUse", "PostToolUseFailure"])
def test_exit_2_stderr_reaches_the_model_on_the_tool_events(event, tmp_path):
    """The tool already ran, so there is nothing left to block — the reference
    routes the stderr to the model instead."""
    result = run(event, emitting(stderr="feedback\n", exit_code=2), tmp_path)
    assert result.model_contexts == ["feedback"]
    assert result.user_notices == []


def test_a_non_zero_non_two_exit_is_a_user_notice_with_the_first_stderr_line(tmp_path):
    result = run("SessionStart", emitting(stderr="first\nsecond\n", exit_code=7), tmp_path)
    assert len(result.user_notices) == 1
    notice = result.user_notices[0]
    assert "Failed with non-blocking status code: 7" in notice
    assert "first" in notice and "second" not in notice


# --------------------------------------------------------------------------
# The JSON channels, gated by the profile's tables
# --------------------------------------------------------------------------

def test_additional_context_reaches_the_model_on_session_start(tmp_path):
    result = run(
        "SessionStart",
        emits_json('{"hookSpecificOutput": {"hookEventName": "SessionStart", '
                   '"additionalContext": "repo is a monorepo"}}'),
        tmp_path,
    )
    assert result.model_contexts == ["repo is a monorepo"]


def test_system_message_reaches_the_user_where_the_event_honors_it(tmp_path):
    result = run("SessionStart", emits_json('{"systemMessage": "watch out"}'), tmp_path)
    assert result.user_notices == ["watch out"]


@pytest.mark.parametrize("event", ["PreCompact", "SessionEnd"])
def test_a_discarding_event_delivers_neither_field_and_says_nothing_about_it(event, tmp_path):
    """§5.1's universal-field exception, and the half that drifts: a discard is
    **silent**. The hook is upstream-conformant — the same output does nothing on
    Claude Code either — so a diagnostic here would flag correct code."""
    result = run(
        event,
        emits_json('{"systemMessage": "notice", "continue": false, "stopReason": "halt"}'),
        tmp_path,
    )
    assert result.user_notices == []
    assert result.stop_reason is None


def test_continue_false_is_honored_where_the_matrix_says_so(tmp_path):
    result = run("PostToolUse", emits_json('{"continue": false, "stopReason": "halt"}'), tmp_path)
    assert result.stop_reason == "halt"


def test_continue_false_is_discarded_on_session_start(tmp_path):
    """Measured against claude 2.1.251 (probe §B): the session starts anyway."""
    result = run("SessionStart", emits_json('{"continue": false, "stopReason": "halt"}'), tmp_path)
    assert result.stop_reason is None


def test_a_v1_rule_gets_no_channels_because_v1_is_frozen(tmp_path):
    """These events were side-effect only under `agentao-v1`, and §3 freezes
    that. Only a profile rule gets the new routing."""
    result = run("SessionStart", emitting(stderr="trouble\n", exit_code=2), tmp_path,
                 contract=LEGACY_CONTRACT_ID)
    assert result.user_notices == []
    assert result.model_contexts == []
    assert result.attachments                      # the side effect still records


# --------------------------------------------------------------------------
# End to end, because a resolver-level test passes while the feature is absent
# --------------------------------------------------------------------------

class _FakeAgent:
    def __init__(self, tmp_path, rules):
        self.working_directory = Path(tmp_path)
        self._plugin_hook_rules = rules
        self.messages: list[dict] = []

    def add_message(self, role, content):
        self.messages.append({"role": role, "content": content})


def test_session_end_exit_2_reaches_the_user_end_to_end(tmp_path):
    """Not a resolver unit test: this path discarded the dispatcher's return
    value inside a bare `try/except: pass`, so a resolver-level test would pass
    while the feature did not exist."""
    agent = _FakeAgent(tmp_path, [rule("SessionEnd", emitting(stderr="goodbye-problem\n", exit_code=2))])
    notices = dispatch_plugin_session_end(agent, "s1")
    assert notices == ["goodbye-problem"]


def test_session_start_context_reaches_the_conversation_end_to_end(tmp_path):
    agent = _FakeAgent(tmp_path, [rule(
        "SessionStart",
        emits_json('{"hookSpecificOutput": {"hookEventName": "SessionStart", '
                   '"additionalContext": "house style: no comments"}}'),
    )])
    dispatch_plugin_session_start(agent, "s1")
    assert len(agent.messages) == 1
    assert "house style: no comments" in agent.messages[0]["content"]
    assert agent.messages[0]["role"] == "user"


def test_no_rules_means_no_work_and_no_notices(tmp_path):
    agent = _FakeAgent(tmp_path, [])
    assert dispatch_plugin_session_start(agent, "s1") == []
    assert dispatch_plugin_session_end(agent, "s1") == []
    assert agent.messages == []
