"""Unicode-fuzzy match tier in EditTool.

Pyramid (file_ops.py::EditTool.execute):
  1. exact substring match
  2. flexible whitespace match (rstrip per line)
  3. unicode-normalized match  ← under test
  4. _not_found_hint (difflib similarity)

Tier 3 mirrors the fuzzy behaviour of `git apply` — it covers the case where
the LLM emits ASCII punctuation but the source file (or vice versa) contains
typographic Unicode (smart quotes, em-dash, NBSP, …). Higher tiers run first,
so byte-identical edits never reach this code path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentao.tools.file_ops import (
    _EDIT_SUFFIX_FLEXIBLE,
    _EDIT_SUFFIX_UNICODE,
    EditTool,
)


@pytest.fixture
def project_root(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    return root


def _bind(tool: EditTool, root: Path) -> EditTool:
    tool.working_directory = root.resolve()
    return tool


# ---------------------------------------------------------------------------
# Tier 3 hits — Unicode-fuzzy match should fire
# ---------------------------------------------------------------------------


def test_smart_double_quotes_in_source_match_ascii_prompt(project_root):
    target = project_root / "doc.py"
    target.write_text('name = “world”\n')  # source has smart quotes

    tool = _bind(EditTool(), project_root)
    result = tool.execute(
        file_path="doc.py",
        old_text='name = "world"',  # prompt uses ASCII quotes
        new_text='name = "earth"',
    )

    assert "Replaced" in result
    assert _EDIT_SUFFIX_UNICODE in result
    # File now contains the replacement (ASCII quotes from new_text)
    assert target.read_text() == 'name = "earth"\n'


def test_em_dash_in_source_matches_ascii_hyphen(project_root):
    target = project_root / "log.txt"
    target.write_text('a — b\n')  # em-dash

    tool = _bind(EditTool(), project_root)
    result = tool.execute(
        file_path="log.txt",
        old_text='a - b',  # ASCII hyphen
        new_text='a - c',
    )

    assert _EDIT_SUFFIX_UNICODE in result
    assert target.read_text() == 'a - c\n'


def test_nbsp_in_source_matches_ascii_space(project_root):
    target = project_root / "x.txt"
    target.write_text('foo bar\n')  # NBSP between words

    tool = _bind(EditTool(), project_root)
    result = tool.execute(
        file_path="x.txt",
        old_text='foo bar',  # ASCII space
        new_text='foo qux',
    )

    assert _EDIT_SUFFIX_UNICODE in result
    assert target.read_text() == 'foo qux\n'


def test_mixed_typography_normalized(project_root):
    target = project_root / "mixed.md"
    # Source has: smart double quote + em-dash + NBSP + ideographic space
    target.write_text('say “hi”—then pause　end\n')

    tool = _bind(EditTool(), project_root)
    result = tool.execute(
        file_path="mixed.md",
        old_text='say "hi"-then pause end',  # all ASCII equivalents
        new_text='say "hi" then continue',
    )

    assert _EDIT_SUFFIX_UNICODE in result
    assert target.read_text() == 'say "hi" then continue\n'


# ---------------------------------------------------------------------------
# replace_all must catch every normalized-equivalent occurrence
# ---------------------------------------------------------------------------


def test_unicode_replace_all_replaces_all_normalized_variants(project_root):
    """replace_all=True at tier 3 must replace every normalized-equivalent span,
    not just byte-identical copies of the first matched dash variant.

    Regression for a Codex review finding: delegating to ``str.replace`` with
    the first matched span only caught one dash codepoint. Mixed em-dash /
    en-dash file with ASCII-hyphen prompt now matches both."""
    target = project_root / "mix.txt"
    # First occurrence uses em-dash (U+2014), second uses en-dash (U+2013).
    # Both normalize to ASCII '-'.
    target.write_text("a — b\nmiddle\na – b\n")

    tool = _bind(EditTool(), project_root)
    result = tool.execute(
        file_path="mix.txt",
        old_text="a - b",
        new_text="a + b",
        replace_all=True,
    )

    assert _EDIT_SUFFIX_UNICODE in result
    assert "Replaced 2" in result
    assert target.read_text() == "a + b\nmiddle\na + b\n"


def test_unicode_replace_all_count_one_when_replace_all_false(project_root):
    """With replace_all=False, only the first normalized match is replaced
    even if multiple normalized-equivalent spans exist."""
    target = project_root / "first_only.txt"
    target.write_text("a — b\nmiddle\na – b\n")

    tool = _bind(EditTool(), project_root)
    result = tool.execute(
        file_path="first_only.txt",
        old_text="a - b",
        new_text="a + b",
        replace_all=False,
    )

    assert _EDIT_SUFFIX_UNICODE in result
    assert "Replaced 1" in result
    # First (em-dash) occurrence replaced, second (en-dash) untouched
    assert target.read_text() == "a + b\nmiddle\na – b\n"


# ---------------------------------------------------------------------------
# CRLF files must round-trip cleanly through tier 2 / tier 3
# ---------------------------------------------------------------------------


def test_unicode_match_preserves_crlf_line_endings(project_root):
    """CRLF file with em-dash on the second line: tier-3 splice must align
    with the original byte offsets and preserve both CRLF boundaries.

    Regression for a Codex review finding: the prefix table previously assumed
    1-byte line endings, so the start offset drifted left by one for each
    preceding CRLF line, corrupting the splice."""
    target = project_root / "crlf.txt"
    target.write_bytes("header\r\na — b\r\n".encode("utf-8"))

    tool = _bind(EditTool(), project_root)
    result = tool.execute(
        file_path="crlf.txt",
        old_text="a - b",
        new_text="a + b",
    )

    assert _EDIT_SUFFIX_UNICODE in result
    assert target.read_bytes() == "header\r\na + b\r\n".encode("utf-8")


def test_flexible_match_preserves_crlf_line_endings(project_root):
    """Tier 2 (flexible whitespace) on a CRLF file: same shared helper, so the
    same offset / trim fix must apply. Multi-line span starting at line 2."""
    target = project_root / "crlf_ws.txt"
    target.write_bytes(b"top\r\n  foo  \r\n  bar  \r\n")

    tool = _bind(EditTool(), project_root)
    result = tool.execute(
        file_path="crlf_ws.txt",
        old_text="foo\nbar",
        new_text="hello",
    )

    assert _EDIT_SUFFIX_FLEXIBLE in result
    assert target.read_bytes() == b"top\r\nhello\r\n"


# ---------------------------------------------------------------------------
# Higher tiers must hit first — tier 3 must not steal these
# ---------------------------------------------------------------------------


def test_byte_identical_uses_tier_1_not_normalization(project_root):
    target = project_root / "exact.py"
    target.write_text('x = "ascii"\n')

    tool = _bind(EditTool(), project_root)
    result = tool.execute(
        file_path="exact.py",
        old_text='x = "ascii"',
        new_text='x = "changed"',
    )

    assert "Replaced" in result
    # Tier 1 returns plain "Replaced N occurrence(s) in <path>" with NO suffix
    assert _EDIT_SUFFIX_UNICODE not in result
    assert _EDIT_SUFFIX_FLEXIBLE not in result
    assert target.read_text() == 'x = "changed"\n'


def test_smart_quotes_on_both_sides_use_tier_1(project_root):
    """If user file has smart quotes and prompt also has smart quotes,
    tier 1 (byte-exact substring) must win. Tier 3 should never fire here."""
    target = project_root / "both.py"
    target.write_text('name = “world”\n')

    tool = _bind(EditTool(), project_root)
    result = tool.execute(
        file_path="both.py",
        old_text='name = “world”',  # also smart quotes
        new_text='name = “earth”',
    )

    assert "Replaced" in result
    assert _EDIT_SUFFIX_UNICODE not in result
    assert target.read_text() == 'name = “earth”\n'


def test_trailing_whitespace_uses_tier_2_flexible(project_root):
    """Source has trailing spaces, prompt does not — tier 2 (whitespace-flex)
    must hit first; tier 3 must not steal."""
    target = project_root / "ws.py"
    target.write_text('def foo():   \n    pass\n')  # trailing spaces

    tool = _bind(EditTool(), project_root)
    result = tool.execute(
        file_path="ws.py",
        old_text='def foo():\n    pass',  # no trailing spaces
        new_text='def foo():\n    return 1',
    )

    assert _EDIT_SUFFIX_FLEXIBLE in result
    assert _EDIT_SUFFIX_UNICODE not in result


# ---------------------------------------------------------------------------
# All tiers fail — fall through to _not_found_hint
# ---------------------------------------------------------------------------


def test_no_match_returns_hint(project_root):
    target = project_root / "nope.py"
    target.write_text('alpha\nbeta\ngamma\n')

    tool = _bind(EditTool(), project_root)
    result = tool.execute(
        file_path="nope.py",
        old_text='completely unrelated text',
        new_text='whatever',
    )

    assert result.startswith("Error:")
    assert "not found" in result.lower()
    # File untouched
    assert target.read_text() == 'alpha\nbeta\ngamma\n'
