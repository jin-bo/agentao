"""Regression test for grep/rg argument-injection hardening.

A user-supplied pattern starting with ``-`` (e.g. ``--help``,
``--pre=/tmp/payload.sh``) must be passed as a literal pattern, never
parsed as a flag by the underlying engine.

For ``rg``: ``--`` is the standard option terminator.
For ``git grep``: ``--`` is the **pathspec** separator, not an option
terminator, so the documented safe form is ``-e <pattern>``.

Tests assert argv shape (no real subprocess work).  Helpers come from
``tests/conftest.py``: ``search_tool`` and ``capture_subprocess_run``.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# ripgrep
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern",
    [
        "--help",
        "--pre=/tmp/payload.sh",
        "-e",
        "-Cfoo",
    ],
)
def test_ripgrep_pattern_starting_with_dash_is_passed_as_text(
    search_tool, capture_subprocess_run, tmp_path, pattern
):
    search_tool._ripgrep(
        directory=tmp_path,
        pattern=pattern,
        file_pattern="**/*",
        case_sensitive=True,
        regex=False,
    )

    cmd = capture_subprocess_run[0]
    # ``--`` must appear before the pattern so rg treats the pattern
    # as text, not as a flag.
    assert "--" in cmd, f"missing option terminator in argv: {cmd}"
    dash_dash_idx = cmd.index("--")
    pattern_idx = cmd.index(pattern)
    assert pattern_idx == dash_dash_idx + 1, (
        f"pattern must immediately follow ``--``; got {cmd}"
    )
    # And the search root still ends the argv.
    assert cmd[-1] == "."


def test_ripgrep_pattern_with_file_pattern_still_terminates_options(
    search_tool, capture_subprocess_run, tmp_path,
):
    """``--glob`` is option-style and must not let a leading-dash pattern slip through."""
    search_tool._ripgrep(
        directory=tmp_path,
        pattern="--pre=/etc/passwd",
        file_pattern="**/*.py",
        case_sensitive=True,
        regex=False,
    )

    cmd = capture_subprocess_run[0]
    # --glob ... must come before ``--`` so glob is parsed as a flag,
    # but the pattern is still after ``--``.
    glob_idx = cmd.index("--glob")
    dash_dash_idx = cmd.index("--")
    pattern_idx = cmd.index("--pre=/etc/passwd")
    assert glob_idx < dash_dash_idx < pattern_idx


# ---------------------------------------------------------------------------
# git grep
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern",
    [
        "--help",
        "--pre=/tmp/payload.sh",
        "-Cfoo",
    ],
)
def test_git_grep_pattern_starting_with_dash_uses_dash_e(
    search_tool, capture_subprocess_run, tmp_path, pattern
):
    search_tool._git_grep(
        directory=tmp_path,
        pattern=pattern,
        file_pattern="**/*",  # no-pathspec branch
        case_sensitive=True,
        regex=False,
    )

    cmd = capture_subprocess_run[0]
    # ``-e <pattern>`` is the documented safe form for git grep.
    assert "-e" in cmd, f"missing -e for git grep argv: {cmd}"
    dash_e_idx = cmd.index("-e")
    pattern_idx = cmd.index(pattern)
    assert pattern_idx == dash_e_idx + 1, (
        f"pattern must immediately follow ``-e``; got {cmd}"
    )


def test_git_grep_pattern_with_pathspec_uses_dash_e(
    search_tool, capture_subprocess_run, tmp_path,
):
    """The pathspec branch (file_pattern set) must also use -e."""
    search_tool._git_grep(
        directory=tmp_path,
        pattern="--pre=/etc/passwd",
        file_pattern="**/*.py",  # pathspec branch
        case_sensitive=True,
        regex=False,
    )

    cmd = capture_subprocess_run[0]
    # Order: ... -e <pattern> -- <pathspec>
    dash_e_idx = cmd.index("-e")
    pattern_idx = cmd.index("--pre=/etc/passwd")
    dash_dash_idx = cmd.index("--")
    pathspec_idx = cmd.index("*.py")
    assert dash_e_idx + 1 == pattern_idx
    assert pattern_idx < dash_dash_idx < pathspec_idx


def test_git_grep_pattern_does_not_appear_before_dash_e(
    search_tool, capture_subprocess_run, tmp_path,
):
    """Belt-and-suspenders: the bare ``cmd.append(pattern)`` regression
    would put the pattern at the end without -e. Catch that explicitly."""
    search_tool._git_grep(
        directory=tmp_path,
        pattern="--help",
        file_pattern="**/*",
        case_sensitive=True,
        regex=False,
    )

    cmd = capture_subprocess_run[0]
    # The pattern must be immediately preceded by -e — never standalone.
    pattern_idx = cmd.index("--help")
    assert cmd[pattern_idx - 1] == "-e"
