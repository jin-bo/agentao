"""``resolve()``'s precedence, over the **combinations** — steps 6 and 7.

§12 asks for a matrix rather than per-field assertions, because the interesting
failures live in the crossings: exit 2 × `continue:false` × a decision, per
event. A field-at-a-time suite passes while the ordering is wrong.

Five stdout states, three exit branches, eight events.
"""

from __future__ import annotations

import json

import pytest

from agentao.plugins.hooks._parsed_output import Block, Stop
from agentao.plugins.hooks._profile import EXIT2_OUTCOME, PROFILE_EVENTS
from agentao.plugins.hooks._resolve import parse_stdout, resolve


# --------------------------------------------------------------------------
# One test per stdout state (§4.2's five)
# --------------------------------------------------------------------------

def test_empty_stdout():
    assert parse_stdout("", "Stop")[1] == "empty"
    assert parse_stdout("   \n ", "Stop")[1] == "empty"


def test_plain_text_is_plain():
    assert parse_stdout("just a note", "Stop")[1] == "plain"


def test_a_leading_bracket_is_never_json():
    """"a JSON array or a quoted JSON string included" — writing the rule as
    "does not start with { or [" implies an array is parsed."""
    assert parse_stdout('[1, 2]', "Stop")[1] == "plain"
    assert parse_stdout('"quoted"', "Stop")[1] == "plain"


def test_the_gate_is_both_ends():
    """A `{"decision":` truncated by a dying pipe is *plain text*: it never
    reaches the parser, so it is not a parse error either."""
    assert parse_stdout('{"decision":', "Stop")[1] == "plain"


def test_a_brace_wrapped_string_that_does_not_parse_is_a_parse_error():
    data, state, failure = parse_stdout('{"a": }', "Stop")
    assert (data, state) == (None, "parse_error")
    assert failure


def test_a_known_fields_bad_value_is_schema_invalid():
    _, state, failure = parse_stdout('{"continue": "yes"}', "Stop")
    assert state == "schema_invalid"
    assert "continue" in failure


def test_an_unrecognized_key_is_not_a_schema_failure():
    """The profile is narrower than the reference by nine fields, so a legal
    upstream hook routinely emits keys agentao does not implement. A closed
    schema would tell its author their correct hook is broken."""
    data, state, _ = parse_stdout('{"watchPaths": ["x"], "systemMessage": "hi"}', "Stop")
    assert state == "valid"
    assert data["systemMessage"] == "hi"


def test_a_mismatched_hook_event_name_invalidates_the_whole_object():
    """The discriminator. "The top-level fields still apply" is the reading that
    makes a mismatch harmless, and it is not what the reference says."""
    _, state, failure = parse_stdout(
        json.dumps({"systemMessage": "hi",
                    "hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": "c"}}),
        "PostToolUse",
    )
    assert state == "schema_invalid"
    assert "hookEventName" in failure


# --------------------------------------------------------------------------
# Plain text is context on exit 0, on two events, and never otherwise
# --------------------------------------------------------------------------

@pytest.mark.parametrize("event,expected", [
    ("UserPromptSubmit", ["note"]),
    ("SessionStart", ["note"]),
    ("Stop", []),
    ("PreToolUse", []),
])
def test_plain_text_becomes_context_only_where_the_event_allows_it(event, expected):
    assert resolve(event, 0, "note", "").model_contexts == expected


def test_plain_text_on_a_failing_exit_is_not_context():
    """Gating on the exit code is what stops a `SessionStart` hook that failed
    with exit 1 and printed a diagnostic from having it injected as context."""
    out = resolve("SessionStart", 1, "diagnostic", "boom")
    assert out.model_contexts == []
    assert out.user_notices and "non-blocking status code: 1" in out.user_notices[0]


def test_a_parse_failure_never_becomes_context():
    """Version-gated, and the pre-2.1.248 reading is the one to avoid
    re-deriving: it is a notice, and the text is withheld."""
    out = resolve("UserPromptSubmit", 0, '{"a": }', "")
    assert out.model_contexts == []
    assert out.user_notices and "hook error" in out.user_notices[0]


def test_a_schema_failure_notifies_the_user_and_the_action_proceeds():
    out = resolve("UserPromptSubmit", 0, '{"continue": "yes"}', "")
    assert out.user_notices
    assert out.control is None


def test_a_non_zero_non_two_exit_carries_the_first_stderr_line():
    out = resolve("Stop", 7, "", "first line\nsecond line")
    assert len(out.user_notices) == 1
    assert "first line" in out.user_notices[0]
    assert "second line" not in out.user_notices[0]


# --------------------------------------------------------------------------
# Exit 2 — three outcomes, and it outranks the JSON
# --------------------------------------------------------------------------

@pytest.mark.parametrize("event", sorted(PROFILE_EVENTS))
def test_exit_2_follows_the_events_own_outcome(event):
    out = resolve(event, 2, "", "the reason")
    kind = EXIT2_OUTCOME[event]
    if kind == "block":
        assert out.control == Block("the reason")
    elif kind == "model_feedback":
        assert out.model_contexts == ["the reason"]
    else:
        assert out.user_notices == ["the reason"]


def test_exit_2_beats_an_allow_in_the_json():
    """"exit 2 blocks whether or not you print JSON: even a JSON
    permissionDecision of allow can't override it"."""
    out = resolve("PreToolUse", 2,
                  json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                     "permissionDecision": "allow"}}),
                  "denied anyway")
    assert isinstance(out.control, Block)


def test_exit_2_with_schema_invalid_json_still_blocks():
    """v2.1.214: before it, this combination was a non-blocking error and the
    action proceeded."""
    out = resolve("PreToolUse", 2, '{"continue": "yes"}', "stderr reason")
    assert out.control == Block("stderr reason")
    assert out.user_notices == []          # the block owns the outcome


def test_a_schema_failure_on_exit_2_does_not_also_notify():
    assert resolve("Stop", 2, '{"a": }', "why").user_notices == []


# --------------------------------------------------------------------------
# continue → the event decision, and only where the table says
# --------------------------------------------------------------------------

@pytest.mark.parametrize("event,stops", [
    ("UserPromptSubmit", True), ("PreToolUse", True), ("PostToolUse", True),
    ("PostToolUseFailure", True), ("Stop", True),
    ("SessionStart", False), ("PreCompact", False), ("SessionEnd", False),
])
def test_continue_false_fires_only_where_the_event_honors_it(event, stops):
    out = resolve(event, 0, json.dumps({"continue": False, "stopReason": "halt"}), "")
    assert (out.control == Stop("halt")) is stops


def test_continue_true_is_not_a_stop():
    assert resolve("Stop", 0, json.dumps({"continue": True}), "").control is None


def test_valid_json_takes_effect_on_every_exit_code():
    """"Claude Code reads JSON output fields from stdout on every exit code, not
    just 0"."""
    for code in (0, 1, 3):
        out = resolve("Stop", code, json.dumps({
            "hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": "ctx"},
        }), "")
        assert out.model_contexts == ["ctx"], code


def test_the_channels_survive_a_stop():
    """Orthogonal: a hook that stops the turn *and* notifies does both."""
    out = resolve("Stop", 0, json.dumps({
        "continue": False, "stopReason": "halt", "systemMessage": "why",
        "hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": "ctx"},
    }), "")
    assert out.control == Stop("halt")
    assert out.user_notices == ["why"]
    assert out.model_contexts == ["ctx"]


# --------------------------------------------------------------------------
# The combinations, per event — the grid §12 asks for
# --------------------------------------------------------------------------

@pytest.mark.parametrize("event", sorted(PROFILE_EVENTS))
@pytest.mark.parametrize("code", [0, 2, 7])
@pytest.mark.parametrize("payload", [
    None,                                    # empty
    "plain text",                            # plain
    '{"a": }',                               # parse_error
    '{"continue": "yes"}',                   # schema_invalid
    '{"continue": false, "stopReason": "r"}',   # valid + stop
    '{"systemMessage": "m"}',                # valid, channel only
])
def test_the_grid_never_raises_and_never_contradicts_the_tables(event, code, payload):
    """The matrix's real job: no crossing may raise, and none may produce a
    control the event's own row forbids."""
    out = resolve(event, code, payload or "", "stderr text")

    if isinstance(out.control, Stop):
        from agentao.plugins.hooks._profile import honors_continue
        assert honors_continue(event), f"{event} must not stop"
    if isinstance(out.control, Block):
        assert code == 2 and EXIT2_OUTCOME[event] == "block"
    if out.model_contexts and code != 2:
        # Only two events take plain text, and only on exit 0.
        if payload == "plain text":
            from agentao.plugins.hooks._profile import PLAIN_TEXT_CONTEXT_EVENTS
            assert code == 0 and event in PLAIN_TEXT_CONTEXT_EVENTS
