"""Forward compatibility — the class a closed-schema parser fails (step 7).

The profile is narrower than the reference by nine fields on these eight events,
so a hook that is perfectly legal upstream routinely emits keys agentao does not
implement. Validating with a closed schema turns every one of them into a
user-visible `hook error`: agentao telling the author their correct hook is
broken, and dropping the fields it *does* implement in the same object.

Three dispositions meet here and they are **not** the same assertion. An
**ignored** field is parsed, diagnosed once, and not acted on. A **discarded**
field is silent — the hook is upstream-conformant and a diagnostic would flag
correct code. A **degraded value** *is* acted on: `defer` becomes `deny`.
"""

from __future__ import annotations

import json

import pytest

from agentao.plugins.hooks import PluginHookDispatcher
from agentao.plugins.hooks._diagnostics import clear_all
from agentao.plugins.hooks._profile import PROFILE_ID
from agentao.plugins.models import ParsedHookRule


@pytest.fixture(autouse=True)
def _isolate():
    clear_all()
    yield
    clear_all()


def _run(tmp_path, event, payload, command=None):
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    rule = ParsedHookRule(
        event=event, hook_type="command",
        command=command or f"echo '{json.dumps(payload)}'",
        contract=PROFILE_ID, plugin_name="p", timeout=30,
    )
    return dispatcher._dispatch_lifecycle(
        event, {"hook_event_name": event, "session_id": "s"}, [rule],
    ), dispatcher, rule


def test_an_unknown_field_is_ignored_and_the_sibling_still_arrives(tmp_path):
    """The assertion that matters is not that the unknown key was refused — it is
    that the `systemMessage` beside it still got delivered."""
    result, _, _ = _run(tmp_path, "SessionStart", {
        "terminalSequence": "\\u001b]0;title\\u0007",
        "systemMessage": "the notice",
    })

    assert "the notice" in result.user_notices
    assert not any("hook error" in n for n in result.user_notices)
    assert any("terminalSequence" in n for n in result.user_notices)


def test_watch_paths_gets_the_same_treatment_as_any_ignored_field(tmp_path):
    """Not a parse rejection: the configuration parser never sees a stdout field,
    and dropping the result would take the `systemMessage` beside it."""
    result, _, _ = _run(tmp_path, "SessionStart", {
        "hookSpecificOutput": {"hookEventName": "SessionStart", "watchPaths": ["src/"]},
        "systemMessage": "still delivered",
    })

    assert "still delivered" in result.user_notices
    assert any("watchPaths" in n for n in result.user_notices)


def test_the_diagnostic_does_not_repeat_on_a_second_invocation(tmp_path):
    """One per (rule, field) per session. Get this wrong in either direction and
    the mechanism inverts: a per-invocation storm, or silence."""
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    rule = ParsedHookRule(
        event="SessionStart", hook_type="command",
        command="""echo '{"hookSpecificOutput": {"hookEventName": "SessionStart", "watchPaths": ["src/"]}}'""",
        contract=PROFILE_ID, plugin_name="p", timeout=30,
    )
    payload = {"hook_event_name": "SessionStart", "session_id": "s"}

    first = dispatcher._dispatch_lifecycle("SessionStart", payload, [rule])
    # A *different* dispatcher object, as every real second invocation has.
    second = PluginHookDispatcher(cwd=tmp_path)._dispatch_lifecycle(
        "SessionStart", payload, [rule],
    )

    assert any("watchPaths" in n for n in first.user_notices)
    assert not any("watchPaths" in n for n in second.user_notices)


@pytest.mark.parametrize("field,value", [
    ("reloadSkills", True),
    ("sessionTitle", "a title"),
])
def test_an_ignored_field_is_parsed_diagnosed_and_not_acted_on(tmp_path, field, value):
    result, _, _ = _run(tmp_path, "SessionStart", {
        "hookSpecificOutput": {"hookEventName": "SessionStart", field: value},
    })

    assert any(field in n for n in result.user_notices)
    assert result.stop_reason is None
    assert result.model_contexts == []


def test_a_discarded_field_is_silent(tmp_path):
    """The other half, and the one that drifts without failing anything: an
    *accepted* field a given event drops earns **no** diagnostic. The hook is
    upstream-conformant — the same output does nothing on Claude Code either."""
    result, _, _ = _run(tmp_path, "SessionStart", {
        "continue": False, "stopReason": "halt",
    })

    assert result.stop_reason is None                 # discarded
    assert result.user_notices == []                  # and silently


def test_a_pre_compact_discard_is_silent_too(tmp_path):
    result, _, _ = _run(tmp_path, "PreCompact", {
        "systemMessage": "notice", "continue": False, "stopReason": "halt",
    })

    assert result.user_notices == []
    assert result.stop_reason is None


def test_the_agentao_namespace_is_simply_an_unknown_key(tmp_path):
    """§3.3: the namespace stood in the design for nine revisions with nothing
    implementing it, so under the unknown-key rule it is just an unrecognized
    field. A sibling `additionalContext` in the same object still arrives."""
    result, _, _ = _run(tmp_path, "PostToolUse", {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "agentao": {"blockingError": "should do nothing"},
            "additionalContext": "sibling survives",
        },
    })

    assert result.model_contexts == ["sibling survives"]
    assert result.stop_reason is None
    assert any("agentao" in n for n in result.user_notices)
