"""Tests for the /crystallize enhancement: evidence model, feedback history,
backward-compatible draft load, and new prompt builders.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentao.cli.commands_ext import (
    collect_crystallize_evidence,
    render_crystallize_context,
)
from agentao.memory.crystallizer import (
    FEEDBACK_SYSTEM_PROMPT,
    feedback_prompt,
    refine_prompt,
    suggest_prompt,
)
from agentao.skills.drafts import (
    SkillDraft,
    SkillEvidence,
    SkillFeedbackEntry,
    append_skill_feedback,
    load_skill_draft,
    new_draft,
    save_skill_draft,
    summarize_draft_status,
)


# ---------------------------------------------------------------------------
# Schema + persistence
# ---------------------------------------------------------------------------


_FM = """\
---
name: python-testing
description: Use when writing pytest suites.
---

# Python Testing

## Steps
1. Identify module
"""


def test_new_draft_default_evidence_is_empty():
    d = new_draft(content=_FM, suggested_name="python-testing")
    assert isinstance(d.evidence, SkillEvidence)
    assert d.feedback_history == []
    assert d.open_questions == []


def test_save_and_load_roundtrip_preserves_evidence_and_feedback(tmp_path: Path):
    ev = SkillEvidence(
        user_goals=["ship the auth refactor"],
        tool_calls=[{"name": "read_file", "args_summary": "file_path=foo.py"}],
        tool_results=[{"name": "read_file", "is_error": False, "excerpt": "…"}],
        workflow_steps=["read_file(file_path=foo.py)"],
        key_files=["foo.py"],
        outcome_signals=["wrote via write_file"],
    )
    d = new_draft(
        content=_FM,
        suggested_name="python-testing",
        session_id="sess_1",
        evidence=ev,
    )
    append_skill_feedback(d, "make it generic, not pytest-only")
    save_skill_draft(d, working_directory=tmp_path, session_id="sess_1")

    loaded = load_skill_draft(working_directory=tmp_path, session_id="sess_1")
    assert loaded is not None
    assert loaded.evidence.user_goals == ["ship the auth refactor"]
    assert loaded.evidence.tool_calls[0]["name"] == "read_file"
    assert loaded.evidence.key_files == ["foo.py"]
    assert loaded.evidence.workflow_steps == ["read_file(file_path=foo.py)"]
    assert len(loaded.feedback_history) == 1
    assert loaded.feedback_history[0].content == "make it generic, not pytest-only"
    assert loaded.feedback_history[0].author == "user"
    assert loaded.feedback_history[0].created_at  # non-empty timestamp


def test_load_old_schema_backfills_defaults(tmp_path: Path):
    """Drafts written by the pre-enhancement code must still load."""
    path = tmp_path / ".agentao" / "crystallize" / "skill_draft.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = {
        "session_id": "",
        "created_at": "2026-04-20T10:00:00",
        "updated_at": "2026-04-20T10:00:00",
        "source": "suggest",
        "refined_with": None,
        "suggested_name": "legacy",
        "content": _FM,
    }
    path.write_text(json.dumps(legacy, indent=2), encoding="utf-8")

    loaded = load_skill_draft(working_directory=tmp_path)
    assert loaded is not None
    assert loaded.suggested_name == "legacy"
    assert isinstance(loaded.evidence, SkillEvidence)
    assert loaded.evidence.tool_calls == []
    assert loaded.feedback_history == []
    assert loaded.open_questions == []


def test_append_skill_feedback_rejects_empty():
    d = new_draft(content=_FM, suggested_name="x")
    with pytest.raises(ValueError):
        append_skill_feedback(d, "   ")


def test_summarize_draft_status_counts_everything():
    ev = SkillEvidence(
        tool_calls=[{"name": "a"}, {"name": "b"}],
        tool_results=[{"name": "a"}],
        workflow_steps=["a", "b", "c"],
        key_files=["f1.py", "f2.py"],
    )
    d = new_draft(content=_FM, suggested_name="foo", evidence=ev)
    append_skill_feedback(d, "tweak")
    info = summarize_draft_status(d)
    assert info["tool_call_count"] == 2
    assert info["tool_result_count"] == 1
    assert info["workflow_step_count"] == 3
    assert info["key_file_count"] == 2
    assert info["feedback_count"] == 1


# ---------------------------------------------------------------------------
# Evidence collection from messages
# ---------------------------------------------------------------------------


def _fake_cli(messages):
    agent = SimpleNamespace(messages=messages)
    return SimpleNamespace(agent=agent)


def test_collect_evidence_extracts_tool_calls_and_results():
    messages = [
        {"role": "user", "content": "refactor tests/test_auth.py so it runs with pytest"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"file_path": "tests/test_auth.py"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "read_file",
            "content": "def test_old(): pass  # snippet",
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {
                        "name": "run_shell_command",
                        "arguments": json.dumps({"command": "pytest tests/test_auth.py"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_2",
            "name": "run_shell_command",
            "content": "1 passed in 0.05s",
        },
        {
            "role": "assistant",
            "content": "Done — the tests pass.",
        },
    ]
    cli = _fake_cli(messages)
    ev = collect_crystallize_evidence(cli)

    assert any(g.startswith("refactor tests/test_auth.py") for g in ev.user_goals)
    names = [tc["name"] for tc in ev.tool_calls]
    assert names == ["read_file", "run_shell_command"]
    assert ev.tool_calls[0]["args_summary"].startswith("file_path=")
    assert len(ev.tool_results) == 2
    assert ev.tool_results[1]["name"] == "run_shell_command"
    assert "tests/test_auth.py" in ev.key_files
    assert any("run_shell_command" in step for step in ev.workflow_steps)
    assert ev.assistant_conclusions and "Done" in ev.assistant_conclusions[0]
    assert "shell command reported success" in ev.outcome_signals


def test_collect_evidence_truncates_long_tool_output():
    big = "A" * 5000
    messages = [
        {"role": "user", "content": "do a thing"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"file_path": "big.txt"}),
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c", "name": "read_file", "content": big},
    ]
    ev = collect_crystallize_evidence(_fake_cli(messages))
    assert ev.tool_results[0]["excerpt"] != big
    assert len(ev.tool_results[0]["excerpt"]) < 400


def test_collect_evidence_skips_malformed_tool_args():
    messages = [
        {"role": "user", "content": "broken"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c",
                    "type": "function",
                    "function": {"name": "mystery_tool", "arguments": "not json"},
                }
            ],
        },
    ]
    ev = collect_crystallize_evidence(_fake_cli(messages))
    assert ev.tool_calls[0]["name"] == "mystery_tool"
    # Falls back to empty summary rather than crashing.
    assert ev.tool_calls[0]["args_summary"] == ""


# ---------------------------------------------------------------------------
# Context rendering + prompt builders
# ---------------------------------------------------------------------------


def test_render_crystallize_context_includes_sections():
    ev = SkillEvidence(
        user_goals=["goal A"],
        workflow_steps=["read_file(file_path=a.py)"],
        tool_calls=[{"name": "read_file", "args_summary": "file_path=a.py"}],
        tool_results=[{"name": "read_file", "is_error": False, "excerpt": "ok"}],
        key_files=["a.py"],
        outcome_signals=["wrote via write_file"],
        assistant_conclusions=["the fix works"],
    )
    out = render_crystallize_context(ev)
    for needle in [
        "User goals",
        "goal A",
        "Workflow",
        "Tool calls",
        "Tool results",
        "Key files",
        "Assistant conclusions",
        "Outcome signals",
    ]:
        assert needle in out


def test_render_crystallize_context_appends_draft_and_feedback():
    ev = SkillEvidence(user_goals=["g"])
    fh = [SkillFeedbackEntry(author="user", content="be generic", created_at="t")]
    out = render_crystallize_context(ev, draft_content="DRAFT", feedback_history=fh)
    assert "Current draft" in out
    assert "DRAFT" in out
    assert "Prior feedback" in out
    assert "be generic" in out


def test_suggest_prompt_includes_evidence_when_provided():
    out = suggest_prompt("transcript text", evidence_text="## Tool calls\n- read_file")
    assert "Structured evidence" in out
    assert "read_file" in out
    assert "transcript text" in out


def test_feedback_prompt_prioritizes_latest_feedback():
    out = feedback_prompt(
        draft_content="DRAFT",
        evidence_text="EVIDENCE",
        latest_feedback="make it generic",
        feedback_history_text="1. [user] older note",
    )
    assert "DRAFT" in out
    assert "EVIDENCE" in out
    assert "older note" in out
    assert "make it generic" in out
    # Latest feedback block comes after history block
    assert out.index("Latest user feedback") > out.index("Prior feedback")


def test_feedback_system_prompt_constrains_output():
    assert "SKILL.md" in FEEDBACK_SYSTEM_PROMPT
    assert "only" in FEEDBACK_SYSTEM_PROMPT.lower()


def test_refine_prompt_accepts_evidence_block():
    out = refine_prompt(
        draft_content="DRAFT",
        session_content="TRANSCRIPT",
        skill_creator_guidance="GUIDE",
        evidence_text="## Workflow\n- step",
    )
    assert "Structured evidence" in out
    assert "step" in out
    assert "TRANSCRIPT" in out
    assert "GUIDE" in out


def test_refine_prompt_still_works_without_evidence():
    # Backward-compat: existing callers without evidence shouldn't break.
    out = refine_prompt("DRAFT", "TRANSCRIPT", "GUIDE")
    assert "Structured evidence" not in out
    assert "DRAFT" in out and "TRANSCRIPT" in out
