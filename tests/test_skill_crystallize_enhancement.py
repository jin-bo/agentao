"""Tests for the /crystallize enhancement: evidence model, feedback history,
backward-compatible draft load, and new prompt builders.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentao.cli.commands_ext import (
    collect_crystallize_evidence,
    render_available_skills_summary,
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


# ---------------------------------------------------------------------------
# render_available_skills_summary
#
# CLI-level builder for the `available_skills_text` block fed into
# suggest_prompt. Exercises sort order (active → recent → alpha) and the
# truncation hint when the budget overflows.
# ---------------------------------------------------------------------------


def _fake_cli_with_skills(tmp_path: Path, skills: list, active: list) -> SimpleNamespace:
    """Build a minimal CLI stub whose `agent.skill_manager` exposes the
    shape ``render_available_skills_summary`` reads."""
    available = {}
    for name, desc, mtime in skills:
        skill_dir = tmp_path / name
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n", encoding="utf-8")
        os.utime(skill_md, (mtime, mtime))
        available[name] = {
            "name": name,
            "description": desc,
            "path": str(skill_md),
        }
    active_dict = {n: {} for n in active}
    sm = SimpleNamespace(
        list_available_skills=lambda: list(available.keys()),
        get_active_skills=lambda: active_dict,
        get_skill_info=lambda name: available.get(name),
    )
    return SimpleNamespace(agent=SimpleNamespace(skill_manager=sm))


def test_render_available_skills_empty_when_no_skill_manager():
    cli = SimpleNamespace(agent=SimpleNamespace())  # no skill_manager
    assert render_available_skills_summary(cli) == ""


def test_render_available_skills_empty_when_no_skills(tmp_path: Path):
    cli = _fake_cli_with_skills(tmp_path, skills=[], active=[])
    assert render_available_skills_summary(cli) == ""


def test_render_available_skills_active_sorted_first(tmp_path: Path):
    # alpha order would be a, b, c; recency would be c (newest), b, a.
    # active="b" must override both — b first.
    cli = _fake_cli_with_skills(
        tmp_path,
        skills=[
            ("a-skill", "Alpha skill", 1_000_000),
            ("b-skill", "Bravo skill", 1_000_500),
            ("c-skill", "Charlie skill", 1_001_000),
        ],
        active=["b-skill"],
    )
    out = render_available_skills_summary(cli)
    lines = [line for line in out.splitlines() if line.startswith("- ")]
    assert lines[0].startswith("- b-skill"), lines  # active first
    # Among inactive, c (most recent) should come before a (oldest)
    rest = [line for line in lines if not line.startswith("- b-skill")]
    assert rest[0].startswith("- c-skill"), rest
    assert rest[1].startswith("- a-skill"), rest


def test_render_available_skills_truncation_appends_hint(tmp_path: Path):
    # Create enough skills with long descriptions to blow past the 2000-char
    # budget. Each line ~150 chars × 30 skills ≈ 4500 chars.
    skills = [
        (f"skill-{i:02d}", "Long " + "x" * 140, 1_000_000 + i)
        for i in range(30)
    ]
    cli = _fake_cli_with_skills(tmp_path, skills=skills, active=[])
    out = render_available_skills_summary(cli)
    assert "showing top" in out
    assert "of 30 installed skills" in out
    assert "be conservative on duplication" in out


def test_render_available_skills_no_hint_when_within_budget(tmp_path: Path):
    cli = _fake_cli_with_skills(
        tmp_path,
        skills=[("only-skill", "Short desc", 1_000_000)],
        active=[],
    )
    out = render_available_skills_summary(cli)
    assert "only-skill" in out
    assert "showing top" not in out


def test_render_available_skills_total_length_respects_budget(tmp_path: Path):
    """The hint suffix must be counted against the 2000-char budget.

    Regression: an earlier version reserved 0 bytes for the hint, so a
    truncated block consistently overran the budget by ~110 chars. The
    rendered total length (including the hint) must stay <= 2000.
    """
    from agentao.cli.commands_ext.crystallize import _AVAILABLE_SKILLS_BUDGET

    skills = [
        (f"skill-{i:02d}", "Long " + "x" * 140, 1_000_000 + i)
        for i in range(30)
    ]
    cli = _fake_cli_with_skills(tmp_path, skills=skills, active=[])
    out = render_available_skills_summary(cli)
    assert "showing top" in out  # truncation actually fired
    assert len(out) <= _AVAILABLE_SKILLS_BUDGET, (
        f"rendered {len(out)} chars, budget is {_AVAILABLE_SKILLS_BUDGET}"
    )
