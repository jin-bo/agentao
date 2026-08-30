"""Golden stdin payloads for ``claude-code@profile-1`` — §5.3's input matrix.

The rule every assertion here enforces: **a field agentao cannot source is
absent or documented, never fabricated — and a field upstream does not define is
not sent at all.** A golden that only checks presence would pass today on `Stop`,
where the old code sent the constant `"workspace-write"` for `permission_mode`.

The expected shapes were cross-checked against six payloads captured from a real
`claude` 2.1.251 (`docs/reference/hooks-probe-2.1.251.md` §F).
"""

from __future__ import annotations

import pytest

from agentao.plugins.hooks._payload import ClaudeHookPayloadAdapter
from agentao.plugins.hooks._profile_payload import (
    EVENTS_WITH_PERMISSION_MODE,
    PERMISSION_MODE_MAP,
    effort_field,
    to_profile_payload,
)
from agentao.plugins.hooks._profile import PROFILE_EVENTS

A = ClaudeHookPayloadAdapter()
COMMON = {"session_id", "transcript_path", "cwd", "hook_event_name"}


def profile(payload):
    return to_profile_payload(payload)


# --------------------------------------------------------------------------
# The eight goldens
# --------------------------------------------------------------------------

def test_session_start_golden():
    sent = profile(A.build_session_start(session_id="s1", cwd="/proj", source="resume"))
    assert sent == {
        "session_id": "s1",
        "transcript_path": None,
        "cwd": "/proj",
        "hook_event_name": "SessionStart",
        "source": "resume",
    }


def test_session_end_golden():
    sent = profile(A.build_session_end(session_id="s1", cwd="/proj", reason="clear"))
    assert sent == {
        "session_id": "s1",
        "transcript_path": None,
        "cwd": "/proj",
        "hook_event_name": "SessionEnd",
        "reason": "clear",
    }


def test_user_prompt_submit_golden():
    sent = profile(A.build_user_prompt_submit(user_message="hi", session_id="s1", cwd="/proj"))
    assert sent == {
        "session_id": "s1",
        "transcript_path": None,
        "cwd": "/proj",
        "hook_event_name": "UserPromptSubmit",
        "prompt": "hi",
    }


def test_pre_tool_use_golden():
    sent = profile(A.build_pre_tool_use(
        tool_name="read_file", tool_input={"path": "x"}, session_id="s1",
        tool_use_id="toolu_1", cwd="/proj",
    ))
    assert sent == {
        "session_id": "s1",
        "transcript_path": None,
        "cwd": "/proj",
        "hook_event_name": "PreToolUse",
        "tool_name": "Read",           # the alias resolver maps agentao → Claude
        "tool_input": {"path": "x"},
        "tool_use_id": "toolu_1",
    }


def test_post_tool_use_golden():
    sent = profile(A.build_post_tool_use(
        tool_name="read_file", tool_input={"path": "x"}, tool_output="contents",
        session_id="s1", tool_use_id="toolu_1", duration_ms=3, cwd="/proj",
    ))
    assert sent == {
        "session_id": "s1",
        "transcript_path": None,
        "cwd": "/proj",
        "hook_event_name": "PostToolUse",
        "tool_name": "Read",
        "tool_input": {"path": "x"},
        "tool_response": "contents",
        "tool_use_id": "toolu_1",
        "duration_ms": 3,
    }


def test_post_tool_use_failure_golden():
    sent = profile(A.build_post_tool_use_failure(
        tool_name="read_file", tool_input={"path": "x"}, error="File does not exist.",
        session_id="s1", tool_use_id="toolu_1", duration_ms=9, is_interrupt=False,
        cwd="/proj",
    ))
    assert sent == {
        "session_id": "s1",
        "transcript_path": None,
        "cwd": "/proj",
        "hook_event_name": "PostToolUseFailure",
        "tool_name": "Read",
        "tool_input": {"path": "x"},
        "tool_use_id": "toolu_1",
        "error": "File does not exist.",
        "is_interrupt": False,
        "duration_ms": 9,
    }


def test_stop_golden():
    sent = profile(A.build_stop(session_id="s1", cwd="/proj",
                                last_assistant_message="done", stop_hook_active=True))
    assert sent == {
        "session_id": "s1",
        "transcript_path": None,
        "cwd": "/proj",
        "hook_event_name": "Stop",
        "stop_hook_active": True,
        "last_assistant_message": "done",
    }


def test_pre_compact_golden():
    sent = profile(A.build_pre_compact(
        session_id="s1", cwd="/proj", trigger="manual",
        compaction_type="full", reason="manual_cli", custom_instructions="keep tests",
    ))
    assert sent == {
        "session_id": "s1",
        "transcript_path": None,
        "cwd": "/proj",
        "hook_event_name": "PreCompact",
        "trigger": "manual",
        "custom_instructions": "keep tests",
    }


# --------------------------------------------------------------------------
# The forbidden column is an assertion too
# --------------------------------------------------------------------------

def test_stop_no_longer_carries_agentaos_own_field():
    sent = profile(A.build_stop(session_id="s", cwd="/p", turn_end_reason="final_response"))
    assert "turn_end_reason" not in sent


def test_pre_compact_drops_three_private_fields():
    """Three agentao-private fields rode on a flat Claude-shaped payload — the
    forbidden column of the matrix, with the citation as proof."""
    sent = profile(A.build_pre_compact(
        session_id="s", cwd="/p", trigger="auto",
        compaction_type="microcompact", reason="microcompact_threshold",
        permission_mode="workspace-write",
    ))
    for forbidden in ("compaction_type", "reason", "permission_mode"):
        assert forbidden not in sent


@pytest.mark.parametrize("builder", [
    lambda: A.build_session_start(session_id="s", cwd="/p"),
    lambda: A.build_session_end(session_id="s", cwd="/p"),
    lambda: A.build_user_prompt_submit(user_message="m", session_id="s", cwd="/p"),
    lambda: A.build_pre_tool_use(tool_name="t", session_id="s", cwd="/p"),
    lambda: A.build_post_tool_use(tool_name="t", session_id="s", cwd="/p"),
    lambda: A.build_post_tool_use_failure(tool_name="t", session_id="s", cwd="/p"),
    lambda: A.build_stop(session_id="s", cwd="/p"),
    lambda: A.build_pre_compact(session_id="s", cwd="/p", trigger="auto",
                                compaction_type="full", reason="manual_cli"),
])
def test_the_two_subagent_fields_are_never_sent(builder):
    """Forbidden for two different reasons — no sub-agent fires hooks at all, and
    agentao has no named-agent session mode."""
    sent = profile(builder())
    assert "agent_id" not in sent
    assert "agent_type" not in sent


def test_prompt_id_is_omitted_rather_than_faked():
    """agentao has a *turn* id, and the reference gives `turn_id` to a different
    event; reusing one for the other invents a correlation that does not hold."""
    sent = profile(A.build_pre_tool_use(tool_name="t", session_id="s", cwd="/p"))
    assert "prompt_id" not in sent


# --------------------------------------------------------------------------
# permission_mode: mapped, or omitted — never agentao's own vocabulary
# --------------------------------------------------------------------------

@pytest.mark.parametrize("agentao_mode,expected", [
    ("plan", "plan"),                       # exact
    ("full-access", "bypassPermissions"),   # near-exact
])
def test_permission_mode_is_mapped_where_a_mapping_exists(agentao_mode, expected):
    sent = profile(A.build_stop(session_id="s", cwd="/p", permission_mode=agentao_mode))
    assert sent["permission_mode"] == expected


@pytest.mark.parametrize("agentao_mode", ["workspace-write", "read-only"])
def test_an_unmappable_mode_is_omitted_not_coerced(agentao_mode):
    """`workspace-write` is not `acceptEdits`, and `read-only` has no upstream
    analogue. A hook branching on the documented values would match no arm."""
    sent = profile(A.build_stop(session_id="s", cwd="/p", permission_mode=agentao_mode))
    assert "permission_mode" not in sent


def test_permission_mode_never_carries_agentaos_vocabulary():
    for mode in ("workspace-write", "read-only", "plan", "full-access"):
        sent = profile(A.build_stop(session_id="s", cwd="/p", permission_mode=mode))
        assert sent.get("permission_mode") in (None, *PERMISSION_MODE_MAP.values())


def test_the_events_that_owe_permission_mode_are_the_five_upstream_names():
    assert EVENTS_WITH_PERMISSION_MODE == {
        "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop",
    }
    assert not (EVENTS_WITH_PERMISSION_MODE & {"SessionStart", "SessionEnd", "PreCompact"})


@pytest.mark.parametrize("effort,expected", [
    ("low", {"level": "low"}), ("high", {"level": "high"}), ("max", {"level": "max"}),
    ("minimal", None), ("off", None), (None, None),
])
def test_effort_maps_only_upstream_levels(effort, expected):
    """Coercing `off` into a level would tell a hook that thinking is on."""
    assert effort_field(effort) == expected


# --------------------------------------------------------------------------
# Structural invariants
# --------------------------------------------------------------------------

@pytest.mark.parametrize("builder,event", [
    (lambda: A.build_session_start(session_id="s", cwd="/p"), "SessionStart"),
    (lambda: A.build_session_end(session_id="s", cwd="/p"), "SessionEnd"),
    (lambda: A.build_user_prompt_submit(user_message="m", session_id="s", cwd="/p"), "UserPromptSubmit"),
    (lambda: A.build_pre_tool_use(tool_name="t", session_id="s", cwd="/p"), "PreToolUse"),
    (lambda: A.build_post_tool_use(tool_name="t", session_id="s", cwd="/p"), "PostToolUse"),
    (lambda: A.build_post_tool_use_failure(tool_name="t", session_id="s", cwd="/p"), "PostToolUseFailure"),
    (lambda: A.build_stop(session_id="s", cwd="/p"), "Stop"),
    (lambda: A.build_pre_compact(session_id="s", cwd="/p", trigger="auto",
                                 compaction_type="full", reason="manual_cli"), "PreCompact"),
])
def test_every_event_carries_the_four_common_fields(builder, event):
    """`session_id`, `cwd` and `hook_event_name` are required everywhere;
    `transcript_path` is required too and is explicitly null, so a hook indexing
    it branches instead of raising."""
    sent = builder()
    sent = to_profile_payload(sent)
    assert COMMON <= set(sent)
    assert sent["hook_event_name"] == event
    assert sent["transcript_path"] is None


def test_all_eight_events_have_a_golden():
    """A missing event should be a test failure, not silence."""
    covered = {"SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse",
               "PostToolUse", "PostToolUseFailure", "Stop", "PreCompact"}
    assert covered == PROFILE_EVENTS


def test_tool_response_stays_a_string_as_a_documented_divergence():
    """Upstream passes a structured object; agentao's tools return `str` and
    declare no output schema, so wrapping it would be a third contract."""
    sent = profile(A.build_post_tool_use(tool_name="t", tool_output="plain text",
                                         session_id="s", cwd="/p"))
    assert sent["tool_response"] == "plain text"
    assert isinstance(sent["tool_response"], str)
