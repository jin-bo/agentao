"""``claude-code@profile-1``'s capability table, the parse/resolve types, and the
one-shot diagnostic registry — step 2 of the conformance plan.

Table-driven on purpose (§12): a new row must be a data change, and a missing
row a test failure rather than silence. The two rows marked *measured* are
asserted against the probe, not against the reference's prose — they are the two
the document could not settle, and one of them reversed.
"""

from __future__ import annotations

import threading

import pytest

from agentao.plugins.hooks._diagnostics import (
    DEFAULT_SESSION,
    HookDiagnosticRegistry,
    clear_all,
    clear_session,
    get_registry,
    rule_key,
)
from agentao.plugins.hooks._parsed_output import (
    Allow,
    Block,
    BlockDecision,
    ParsedHookOutput,
    PermissionDecision,
    PostToolUseDecision,
    PreToolUseDecision,
    ResolvedHookOutput,
    Stop,
)
from agentao.plugins.hooks._profile import (
    EXIT2_OUTCOME,
    OUTPUT_FIELDS,
    PERMISSION_DECISION_DEGRADES,
    PLAIN_TEXT_CONTEXT_EVENTS,
    PROFILE_EVENTS,
    PROFILE_ID,
    UNIVERSAL_DELIVERY,
    exit2_outcome,
    field_disposition,
    honors_continue,
    honors_system_message,
    ignore_reason,
)
from agentao.plugins.models import SUPPORTED_HOOK_EVENTS, ParsedHookRule


# --------------------------------------------------------------------------
# The table is complete and agrees with what agentao already supports
# --------------------------------------------------------------------------

def test_the_profile_events_are_the_events_agentao_dispatches():
    """§1's event list is a declaration, so it must not drift from the parser's."""
    assert PROFILE_EVENTS == SUPPORTED_HOOK_EVENTS


@pytest.mark.parametrize("event", sorted(PROFILE_EVENTS))
def test_every_event_has_a_row_in_every_table(event):
    assert event in UNIVERSAL_DELIVERY
    assert event in EXIT2_OUTCOME
    # Both accepted universal fields, and only those two: `suppressOutput` and
    # `terminalSequence` are `ignore`, so the delivery axis never runs for them.
    assert set(UNIVERSAL_DELIVERY[event]) == {"continue", "systemMessage"}


def test_ignored_fields_all_carry_a_reason():
    """§1: nothing is excluded silently. An `ignore` with no reason is a silent
    exclusion wearing a table row."""
    for name, spec in OUTPUT_FIELDS.items():
        if spec.disposition == "ignore":
            assert spec.reason, f"{name} is ignored with no reason"


def test_the_two_dispositions_are_the_only_ones():
    """`reject` is a configuration verb and has no meaning for an output field —
    refusing a result would discard every sibling field in the same object."""
    assert {s.disposition for s in OUTPUT_FIELDS.values()} == {"accept", "ignore"}


# --------------------------------------------------------------------------
# Universal is not universal
# --------------------------------------------------------------------------

@pytest.mark.parametrize("event,expected", [
    ("SessionStart", False),        # measured: hooks.md:1009 + probe B
    ("UserPromptSubmit", True),
    ("PreToolUse", True),
    ("PostToolUse", True),
    ("PostToolUseFailure", True),
    ("Stop", True),
    ("PreCompact", False),
    ("SessionEnd", False),
])
def test_honors_continue_per_event(event, expected):
    assert honors_continue(event) is expected


@pytest.mark.parametrize("event,expected", [
    ("SessionStart", True),         # the Decision-control row is silent on notices
    ("PreCompact", False),
    ("SessionEnd", False),
    ("Stop", True),
])
def test_honors_system_message_is_its_own_predicate(event, expected):
    """The two predicates differ on `SessionStart`, which is why there are two."""
    assert honors_system_message(event) is expected


def test_session_start_separates_the_two_fields():
    """The row that cost the plan a method rule: a stop is discarded there while
    a user notice is not."""
    assert honors_continue("SessionStart") is False
    assert honors_system_message("SessionStart") is True


# --------------------------------------------------------------------------
# Exit 2 is not a boolean
# --------------------------------------------------------------------------

def test_exit2_has_three_outcomes_and_all_three_are_live():
    assert set(EXIT2_OUTCOME.values()) == {"block", "model_feedback", "user_notice"}


@pytest.mark.parametrize("event,outcome", [
    ("PreToolUse", "block"),
    ("UserPromptSubmit", "block"),
    ("Stop", "block"),
    ("PreCompact", "block"),
    ("PostToolUse", "model_feedback"),
    ("PostToolUseFailure", "model_feedback"),
    ("SessionStart", "user_notice"),
    ("SessionEnd", "user_notice"),
])
def test_exit2_outcome_per_event(event, outcome):
    """A single `blocks_on_exit_2` predicate would silently discard the stderr on
    exactly the two events where §5.2 promises it reaches the model."""
    assert exit2_outcome(event) == outcome


def test_plain_text_is_context_on_two_events_only():
    assert PLAIN_TEXT_CONTEXT_EVENTS == {"UserPromptSubmit", "SessionStart"}


# --------------------------------------------------------------------------
# The measured rows
# --------------------------------------------------------------------------

def test_post_tool_use_failure_accepts_a_decision():
    """Measured, not inherited: the global row it shares with `PostToolUse` fixes
    a wire shape, not an effect (probe C)."""
    assert field_disposition("decision", "PostToolUseFailure") == "accept"


def test_pre_tool_use_has_no_top_level_decision():
    """It carries `permissionDecision` instead; a `decision` there is an unknown
    key, which is ignored — never a schema failure."""
    assert field_disposition("decision", "PreToolUse") is None
    assert field_disposition("hookSpecificOutput.permissionDecision", "PreToolUse") == "accept"


def test_defer_degrades_to_deny_with_a_named_alternative():
    """§1's third rule reaches values, not just fields: a value agentao cannot
    honor is accept / ignore / degrade-to-X, never "reject"."""
    assert PERMISSION_DECISION_DEGRADES == {"defer": "deny"}


def test_an_unknown_key_is_not_in_the_table_and_that_is_not_an_error():
    assert field_disposition("watchPathsTypo", "SessionStart") is None
    assert ignore_reason("hookSpecificOutput.watchPaths")


# --------------------------------------------------------------------------
# The two types
# --------------------------------------------------------------------------

def test_channels_are_orthogonal_to_the_verdict():
    """A hook that blocks *and* emits a user notice does both — the property a
    verdict-only return value destroys."""
    parsed = ParsedHookOutput()
    parsed.universal.system_message = "watch out"
    parsed.additional_context = ["extra"]
    parsed.decision = BlockDecision(block=True, reason="nope")

    resolved = ResolvedHookOutput(control=Block("nope"))
    resolved.absorb_channels(parsed, "Stop")

    assert resolved.control == Block("nope")
    assert resolved.user_notices == ["watch out"]
    assert resolved.model_contexts == ["extra"]


def test_absorb_respects_the_per_event_exception():
    parsed = ParsedHookOutput()
    parsed.universal.system_message = "notice"
    resolved = ResolvedHookOutput()
    resolved.absorb_channels(parsed, "PreCompact")
    assert resolved.user_notices == []


def test_absorb_does_not_route_additional_context_where_the_field_is_undefined():
    """`hSO.additionalContext` is defined on six events. Delivering it on the
    other two would invent a channel the reference does not give them."""
    parsed = ParsedHookOutput(additional_context=["ctx"])
    for event, expected in [("Stop", ["ctx"]), ("PreCompact", []), ("SessionEnd", [])]:
        resolved = ResolvedHookOutput()
        resolved.absorb_channels(parsed, event)
        assert resolved.model_contexts == expected, event


def test_block_and_stop_are_different_types():
    """On `PreToolUse` they are different outcomes for the user: one ends the
    turn, the other blocks a call and lets the model try something else."""
    assert Block("r") != Stop("r")
    assert isinstance(Allow(), Allow)
    assert PermissionDecision("deny", "why").verdict == "deny"


def test_the_parse_layer_can_hold_what_the_profile_declines():
    """`defer` and `suppressOriginalPrompt` must be representable: a value that
    cannot be parsed cannot be degraded with a reason, only dropped."""
    parsed = ParsedHookOutput(decision=PreToolUseDecision(permission="defer", reason="r"))
    assert parsed.decision.permission == "defer"
    assert parsed.blocking_reason == "r"


def test_blocking_reason_reads_every_decision_shape():
    for decision, expected in [
        (PreToolUseDecision(reason="a"), "a"),
        (PostToolUseDecision(block=True, reason="b"), "b"),
        (BlockDecision(block=True, reason="c"), "c"),
        (None, None),
    ]:
        assert ParsedHookOutput(decision=decision).blocking_reason == expected


def test_profile_id_is_an_agentao_name_not_a_product_version():
    """A product version would assert an upper bound nothing supports."""
    assert PROFILE_ID == "claude-code@profile-1"


# --------------------------------------------------------------------------
# The diagnostic registry (G10)
# --------------------------------------------------------------------------

def _rule(command="x", event="PostToolUse", plugin="p"):
    return ParsedHookRule(event=event, hook_type="command", command=command,
                          plugin_name=plugin)


@pytest.fixture(autouse=True)
def _isolate_registries():
    clear_all()
    yield
    clear_all()


def test_a_field_is_announced_once_per_rule():
    registry = HookDiagnosticRegistry()
    key = rule_key(_rule())
    assert registry.announce(key, "watchPaths") is True
    assert registry.announce(key, "watchPaths") is False
    # A different field on the same rule is a different announcement.
    assert registry.announce(key, "sessionTitle") is True


def test_two_dispatches_of_the_same_rule_share_one_registry():
    """The dispatcher is constructed fresh at six call sites, two inside pool
    workers, so dispatcher-scoped state would dedup nothing."""
    first = get_registry("session-1")
    second = get_registry("session-1")
    assert first is second
    key = rule_key(_rule())
    assert first.announce(key, "classifierContext") is True
    assert second.announce(key, "classifierContext") is False


def test_sessions_do_not_share_state():
    key = rule_key(_rule())
    assert get_registry("a").announce(key, "watchPaths") is True
    assert get_registry("b").announce(key, "watchPaths") is True


def test_the_key_survives_a_reparse_but_not_an_edit():
    """Content-derived, never `id(rule)`: object identity changes on every
    reload, which would silently re-announce everything."""
    assert rule_key(_rule()) == rule_key(_rule())
    assert rule_key(_rule(command="x")) != rule_key(_rule(command="y"))
    assert rule_key(_rule(), 0) != rule_key(_rule(), 1)
    assert rule_key(_rule(plugin="p")) != rule_key(_rule(plugin="q"))


def test_a_reload_makes_a_corrected_hook_announce_again():
    key = rule_key(_rule())
    assert get_registry("s").announce(key, "watchPaths") is True
    clear_session("s")
    assert get_registry("s").announce(key, "watchPaths") is True


def test_concurrent_announcements_produce_exactly_one():
    """Two tool events firing at once must not both announce: the check and the
    insert cannot be separate operations."""
    registry = HookDiagnosticRegistry()
    key = rule_key(_rule())
    results: list[bool] = []
    barrier = threading.Barrier(16)

    def worker():
        barrier.wait()
        results.append(registry.announce(key, "watchPaths"))

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1
    assert len(results) == 16


def test_the_default_bucket_is_separate_from_a_named_session():
    key = rule_key(_rule())
    assert get_registry(None).announce(key, "watchPaths") is True
    assert get_registry(DEFAULT_SESSION).announce(key, "watchPaths") is False
    assert get_registry("real-session").announce(key, "watchPaths") is True
