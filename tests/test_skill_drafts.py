"""Tests for agentao.skills.drafts — pending skill draft storage + helpers."""

from pathlib import Path

import pytest

from agentao.skills.drafts import (
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


# ---------------------------------------------------------------------------
# Byte-for-byte preservation.
#
# ``replace_skill_name`` rewrites a file the user wrote, so everything except
# the ``name:`` value must survive untouched. An earlier implementation
# rebuilt the frontmatter from the literal f"---\n{block}\n---\n", which
# reformatted every document whose layout differed from that one shape — most
# importantly it ate the blank line between the closing fence and the body,
# which is the layout _SAMPLE and every skill in this repo actually use.
#
# The assertions below compare whole strings, not substrings: the pre-existing
# test above passes against the buggy version precisely because `"# Python
# Testing" in out` cannot see a deleted blank line.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,src",
    [
        ("blank line after closing fence", "---\nname: a\ndesc: d\n---\n\n# Body\n"),
        ("two blank lines after fence", "---\nname: a\n---\n\n\n# Body\n"),
        ("no blank line after fence", "---\nname: a\n---\n# Body\n"),
        ("file ends at the closing fence", "---\nname: a\n---"),
        ("no trailing newline on body", "---\nname: a\n---\n# Body"),
        ("leading blank line before fence", "\n---\nname: a\n---\n# Body\n"),
        ("trailing spaces on opening fence", "---  \nname: a\n---\n# Body\n"),
        ("CRLF throughout", "---\r\nname: a\r\n---\r\n# Body\r\n"),
        ("blank line inside frontmatter", "---\nname: a\n\ndesc: d\n---\n# B\n"),
        ("indented name key", "---\n  name: a\n---\n# B\n"),
        ("spaces around the colon", "---\nname   :   a\n---\n# B\n"),
    ],
)
def test_replace_skill_name_changes_only_the_name_value(label, src):
    """Output must equal the input with the value swapped — nothing else."""
    out = replace_skill_name(src, "z")
    expected = src.replace(": a", ": z").replace(":   a", ":   z")
    assert out == expected, (
        f"{label}: layout was rewritten\n  in : {src!r}\n  out: {out!r}\n  want: {expected!r}"
    )
    assert extract_skill_name(out) == "z"


def test_replace_skill_name_preserves_the_sample_layout_exactly():
    """The repo's own skill layout — the shape the old code corrupted."""
    out = replace_skill_name(_SAMPLE, "js-testing")
    assert out == _SAMPLE.replace("name: python-testing", "name: js-testing")
    # Spelled out, because this is the byte the old implementation deleted:
    assert "---\n\n# Python Testing" in out


def test_replace_skill_name_is_idempotent():
    once = replace_skill_name(_SAMPLE, "z")
    assert replace_skill_name(once, "z") == once


def test_replace_skill_name_prepends_when_no_name_line():
    src = "---\ndesc: d\n---\n\n# Body\n"
    out = replace_skill_name(src, "z")
    assert extract_skill_name(out) == "z"
    assert out == "---\nname: z\ndesc: d\n---\n\n# Body\n"


def test_replace_skill_name_prepend_matches_crlf_line_endings():
    src = "---\r\ndesc: d\r\n---\r\n# Body\r\n"
    out = replace_skill_name(src, "z")
    assert extract_skill_name(out) == "z"
    assert "name: z\r\n" in out
    # No LF-only line ending was introduced into a CRLF document: after
    # removing every CRLF pair, no bare \n may remain.
    assert out.replace("\r\n", "").count("\n") == 0


def test_replace_skill_name_quoted_value_is_replaced():
    out = replace_skill_name('---\nname: "old"\n---\n# B\n', "z")
    assert extract_skill_name(out) == "z"
    assert out == "---\nname: z\n---\n# B\n"


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
