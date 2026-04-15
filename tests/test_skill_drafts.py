"""Tests for agentao.skills.drafts — pending skill draft storage + helpers."""

from pathlib import Path

import pytest

from agentao.skills.drafts import (
    SkillDraft,
    clear_skill_draft,
    extract_skill_name,
    get_skill_draft_path,
    load_skill_draft,
    new_draft,
    replace_skill_name,
    save_skill_draft,
)


_SAMPLE = """\
---
name: python-testing
description: Use when writing pytest suites.
---

# Python Testing

## Steps
1. Identify module
"""


def test_save_and_load_roundtrip(tmp_path: Path):
    draft = new_draft(content=_SAMPLE, suggested_name="python-testing", session_id="sess_1")
    path = save_skill_draft(draft, working_directory=tmp_path)
    # Draft filename derives from session_id when one is present on the draft.
    assert path == tmp_path / ".agentao" / "crystallize" / "skill_draft_sess_1.json"
    assert path.exists()

    loaded = load_skill_draft(working_directory=tmp_path, session_id="sess_1")
    assert loaded is not None
    assert loaded.suggested_name == "python-testing"
    assert loaded.content == _SAMPLE
    assert loaded.source == "suggest"
    assert loaded.refined_with is None
    assert loaded.session_id == "sess_1"


def test_load_returns_none_when_missing(tmp_path: Path):
    assert load_skill_draft(working_directory=tmp_path) is None


def test_clear_skill_draft(tmp_path: Path):
    draft = new_draft(content=_SAMPLE, suggested_name="x")
    save_skill_draft(draft, working_directory=tmp_path)
    assert clear_skill_draft(working_directory=tmp_path) is True
    assert clear_skill_draft(working_directory=tmp_path) is False
    assert load_skill_draft(working_directory=tmp_path) is None


def test_get_skill_draft_path_is_project_scoped(tmp_path: Path):
    path = get_skill_draft_path(working_directory=tmp_path)
    assert path.parts[-3:] == (".agentao", "crystallize", "skill_draft.json")


def test_drafts_are_isolated_per_session(tmp_path: Path):
    d1 = new_draft(content=_SAMPLE, suggested_name="a", session_id="sess_a")
    d2 = new_draft(content=_SAMPLE.replace("python-testing", "js-testing"),
                   suggested_name="b", session_id="sess_b")
    save_skill_draft(d1, working_directory=tmp_path)
    save_skill_draft(d2, working_directory=tmp_path)

    got_a = load_skill_draft(working_directory=tmp_path, session_id="sess_a")
    got_b = load_skill_draft(working_directory=tmp_path, session_id="sess_b")
    assert got_a is not None and got_a.suggested_name == "a"
    assert got_b is not None and got_b.suggested_name == "b"

    # Clearing one session leaves the other intact.
    assert clear_skill_draft(working_directory=tmp_path, session_id="sess_a") is True
    assert load_skill_draft(working_directory=tmp_path, session_id="sess_a") is None
    assert load_skill_draft(working_directory=tmp_path, session_id="sess_b") is not None


def test_session_id_sanitized_into_filename(tmp_path: Path):
    draft = new_draft(content=_SAMPLE, suggested_name="x", session_id="sess/../evil id")
    path = save_skill_draft(draft, working_directory=tmp_path)
    # Must stay under the crystallize/ directory — no path traversal.
    assert path.parent == tmp_path / ".agentao" / "crystallize"
    assert "/" not in path.name and "\\" not in path.name


def test_working_directory_is_respected(tmp_path: Path, monkeypatch):
    # Simulate agent running with cwd != project root.
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    draft = new_draft(content=_SAMPLE, suggested_name="x", session_id="sess_wd")
    save_skill_draft(draft, working_directory=tmp_path)
    assert (tmp_path / ".agentao" / "crystallize").exists()
    assert not (other / ".agentao").exists()
    assert load_skill_draft(working_directory=tmp_path, session_id="sess_wd") is not None


def test_extract_skill_name():
    assert extract_skill_name(_SAMPLE) == "python-testing"
    assert extract_skill_name("no frontmatter here") is None
    assert extract_skill_name("---\ndescription: only\n---\nbody") is None


def test_replace_skill_name_updates_frontmatter():
    out = replace_skill_name(_SAMPLE, "js-testing")
    assert extract_skill_name(out) == "js-testing"
    # body preserved
    assert "# Python Testing" in out
    assert "description: Use when writing pytest suites." in out


def test_replace_skill_name_requires_frontmatter():
    with pytest.raises(ValueError):
        replace_skill_name("no frontmatter", "x")


def test_save_updates_updated_at(tmp_path: Path):
    draft = new_draft(content=_SAMPLE, suggested_name="x")
    save_skill_draft(draft, working_directory=tmp_path)
    first = draft.updated_at
    # Mutate and save again — updated_at should refresh even if equal string
    draft.refined_with = "skill-creator"
    save_skill_draft(draft, working_directory=tmp_path)
    loaded = load_skill_draft(working_directory=tmp_path)
    assert loaded is not None
    assert loaded.refined_with == "skill-creator"
    assert loaded.updated_at >= first
