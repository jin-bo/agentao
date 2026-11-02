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

Tier 3 normalizes in two passes (NFKC, then a codepoint table). The
``TestNfkcFolding`` class below pins the half NFKC contributes — full-width
punctuation, which the table has no entries for and which is the common case
in a CJK source file.
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


@pytest.mark.parametrize("source, old_text, new_text, expected", [
    # The source has smart quotes, the prompt ASCII quotes.
    pytest.param(
        'name = “world”\n', 'name = "world"',
        'name = "earth"', 'name = "earth"\n',
        id='smart-double-quotes',
    ),
    # The source has an em-dash, the prompt an ASCII hyphen.
    pytest.param(
        'a — b\n', 'a - b',
        'a - c', 'a - c\n',
        id='em-dash',
    ),
    # The source has an NBSP between words, the prompt an ASCII space.
    pytest.param(
        'foo\xa0bar\n', 'foo bar',
        'foo qux', 'foo qux\n',
        id='nbsp',
    ),
    # Smart double quote + em-dash + NBSP + ideographic space, against all
    # ASCII equivalents.
    pytest.param(
        'say “hi”—then\xa0pause\u3000end\n', 'say "hi"-then pause end',
        'say "hi" then continue', 'say "hi" then continue\n',
        id='mixed-typography',
    ),
])
def test_typographic_source_matches_an_ascii_prompt(project_root, source, old_text, new_text, expected):
    target = project_root / "doc.txt"
    target.write_text(source, encoding='utf-8')

    tool = _bind(EditTool(), project_root)
    result = tool.execute(file_path="doc.txt", old_text=old_text, new_text=new_text)

    assert "Replaced" in result
    assert _EDIT_SUFFIX_UNICODE in result
    # The file now holds the replacement exactly as new_text spelled it.
    assert target.read_text(encoding='utf-8') == expected


# ---------------------------------------------------------------------------
# NFKC half of tier 3 — compatibility forms the codepoint table cannot reach
# ---------------------------------------------------------------------------


class TestNfkcFolding:
    """Full-width and other compatibility forms must fold before matching.

    The codepoint table only knows dashes, quotes and spaces, so before NFKC
    every one of these fell through tier 3 to ``_not_found_hint``. Borrowed
    from pi-mono ``edit-diff.ts::normalizeForFuzzyMatch``.
    """

    @pytest.mark.parametrize("source, old_text, new_text, expected", [
        # The CJK case: source written with 全角 punctuation, prompt in 半角.
        pytest.param(
            'print（"你好"）；\n', 'print("你好");',
            'print("世界")', 'print("世界")\n',
            id='fullwidth-punctuation-in-source',
        ),
        pytest.param(
            'ＴＯＴＡＬ＝４２\n', 'TOTAL=42',
            'TOTAL=43', 'TOTAL=43\n',
            id='fullwidth-alphanumerics',
        ),
        # Reverse direction: the *model* emitted 全角. Both sides normalize, so
        # tier 3 must still land.
        pytest.param(
            'total = 42\n', 'ｔｏｔａｌ\u3000＝\u3000４２',
            'total = 43', 'total = 43\n',
            id='fullwidth-prompt-ascii-source',
        ),
        # Ordering regression. U+FE58 ``﹘`` (CJK compatibility small em-dash)
        # has no table entry. NFKC folds it to U+2014, which the table then
        # maps to ASCII ``-``. Table-first would strand it one step short and
        # this falls to tier 4.
        pytest.param(
            'a ﹘ b\n', 'a - b',
            'a + b', 'a + b\n',
            id='nfkc-runs-before-the-codepoint-table',
        ),
    ])
    def test_a_compatibility_form_folds(self, project_root, source, old_text, new_text, expected):
        target = project_root / "doc.txt"
        target.write_text(source, encoding='utf-8')

        tool = _bind(EditTool(), project_root)
        result = tool.execute(file_path="doc.txt", old_text=old_text, new_text=new_text)

        assert _EDIT_SUFFIX_UNICODE in result
        assert target.read_text(encoding='utf-8') == expected

    def test_fullwidth_on_both_sides_still_uses_tier_1(self, project_root):
        """NFKC must not steal a byte-exact match. A file and prompt that agree
        on 全角 punctuation is tier 1, and the file keeps whatever new_text says.
        """
        target = project_root / "both_fw.py"
        target.write_text('print（"你好"）\n', encoding='utf-8')

        tool = _bind(EditTool(), project_root)
        result = tool.execute(
            file_path="both_fw.py",
            old_text='print（"你好"）',
            new_text='print（"世界"）',
        )

        assert "Replaced" in result
        assert _EDIT_SUFFIX_UNICODE not in result
        assert _EDIT_SUFFIX_FLEXIBLE not in result
        assert target.read_text(encoding='utf-8') == 'print（"世界"）\n'


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
    target.write_text("a — b\nmiddle\na – b\n", encoding='utf-8')

    tool = _bind(EditTool(), project_root)
    result = tool.execute(
        file_path="mix.txt",
        old_text="a - b",
        new_text="a + b",
        replace_all=True,
    )

    assert _EDIT_SUFFIX_UNICODE in result
    assert "Replaced 2" in result
    assert target.read_text(encoding='utf-8') == "a + b\nmiddle\na + b\n"


def test_unicode_replace_all_count_one_when_replace_all_false(project_root):
    """With replace_all=False, only the first normalized match is replaced
    even if multiple normalized-equivalent spans exist.

    The result string must also *say* that a choice was made on the
    model's behalf: ``old_text`` matched more than one span, and reporting
    a bare "Replaced 1" would hide that the edit may have landed on the
    wrong one.
    """
    target = project_root / "first_only.txt"
    target.write_text("a — b\nmiddle\na – b\n", encoding='utf-8')

    tool = _bind(EditTool(), project_root)
    result = tool.execute(
        file_path="first_only.txt",
        old_text="a - b",
        new_text="a + b",
        replace_all=False,
    )

    assert _EDIT_SUFFIX_UNICODE in result
    assert "first of 2 occurrences" in result
    assert "replace_all=true" in result
    # First (em-dash) occurrence replaced, second (en-dash) untouched
    assert target.read_text(encoding='utf-8') == "a + b\nmiddle\na – b\n"


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
    target.write_text('x = "ascii"\n', encoding='utf-8')

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
    assert target.read_text(encoding='utf-8') == 'x = "changed"\n'


def test_smart_quotes_on_both_sides_use_tier_1(project_root):
    """If user file has smart quotes and prompt also has smart quotes,
    tier 1 (byte-exact substring) must win. Tier 3 should never fire here."""
    target = project_root / "both.py"
    target.write_text('name = “world”\n', encoding='utf-8')

    tool = _bind(EditTool(), project_root)
    result = tool.execute(
        file_path="both.py",
        old_text='name = “world”',  # also smart quotes
        new_text='name = “earth”',
    )

    assert "Replaced" in result
    assert _EDIT_SUFFIX_UNICODE not in result
    assert target.read_text(encoding='utf-8') == 'name = “earth”\n'


def test_trailing_whitespace_uses_tier_2_flexible(project_root):
    """Source has trailing spaces, prompt does not — tier 2 (whitespace-flex)
    must hit first; tier 3 must not steal."""
    target = project_root / "ws.py"
    target.write_text('def foo():   \n    pass\n', encoding='utf-8')  # trailing spaces

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
    target.write_text('alpha\nbeta\ngamma\n', encoding='utf-8')

    tool = _bind(EditTool(), project_root)
    result = tool.execute(
        file_path="nope.py",
        old_text='completely unrelated text',
        new_text='whatever',
    )

    assert result.startswith("Error:")
    assert "not found" in result.lower()
    # File untouched
    assert target.read_text(encoding='utf-8') == 'alpha\nbeta\ngamma\n'


# ---------------------------------------------------------------------------
# Ambiguity reporting must not invite corruption (code-review findings)
# ---------------------------------------------------------------------------


class TestAmbiguityMessageSafety:
    def test_empty_old_text_is_refused(self, project_root):
        """``"" in content`` is always True and ``count("")`` is len+1.

        Left unguarded this reports phantom occurrences and advises
        replace_all, which inserts new_text between every character.
        """
        target = project_root / "e.txt"
        target.write_text("a foo b foo c", encoding='utf-8')

        tool = _bind(EditTool(), project_root)
        result = tool.execute(file_path="e.txt", old_text="", new_text="Z")

        assert result.startswith("Error:")
        assert target.read_text(encoding='utf-8') == "a foo b foo c"

    def test_growing_replacement_counts_remaining_correctly(self, project_root):
        """``foo`` → ``foobar`` does not consume an occurrence.

        ``total - 1`` would claim 1 remains when 2 literally do.
        """
        target = project_root / "g.txt"
        target.write_text("a foo b foo c", encoding='utf-8')

        tool = _bind(EditTool(), project_root)
        result = tool.execute(file_path="g.txt", old_text="foo", new_text="foobar")

        assert target.read_text(encoding='utf-8') == "a foobar b foo c"
        assert "2 occurrence(s) of old_text remain" in result

    def test_growing_replacement_does_not_advise_replace_all(self, project_root):
        """replace_all here would re-edit the site just changed."""
        target = project_root / "g2.txt"
        target.write_text("a foo b foo c", encoding='utf-8')

        tool = _bind(EditTool(), project_root)
        result = tool.execute(file_path="g2.txt", old_text="foo", new_text="foobar")

        assert "replace_all=true to change all" not in result
        assert "re-edit the site just changed" in result

    def test_ordinary_ambiguity_still_advises_replace_all(self, project_root):
        target = project_root / "n.txt"
        target.write_text("a foo b foo c", encoding='utf-8')

        tool = _bind(EditTool(), project_root)
        result = tool.execute(file_path="n.txt", old_text="foo", new_text="bar")

        assert target.read_text(encoding='utf-8') == "a bar b foo c"
        assert "1 occurrence(s) of old_text remain" in result
        assert "replace_all=true to change all" in result
