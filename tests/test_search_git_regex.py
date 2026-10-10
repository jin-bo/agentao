"""Exercise the real git backend's common regular-expression operators."""

import shutil

import pytest

from agentao.capabilities.process import run_captured
from agentao.tools.search import SearchTextTool


@pytest.fixture
def git_search(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    assert run_captured(["git", "init", "-q"], cwd=str(tmp_path), timeout=5).returncode == 0
    (tmp_path / "sample.txt").write_bytes(
        b"foo\nbar\naaa\ncolor\ncolour\n123\nFOO\nValueError\ngetValue(\nwith space\n"
    )
    assert run_captured(["git", "add", "sample.txt"], cwd=str(tmp_path), timeout=5).returncode == 0
    probe = run_captured(["git", "grep", "-P", "-e", "x"], cwd=str(tmp_path), timeout=5)
    if probe.returncode == 128:
        pytest.skip("git was built without PCRE support")
    tool = SearchTextTool()
    tool.working_directory = tmp_path
    return tool


@pytest.mark.parametrize("pattern,expected", [
    ("foo|bar", 2), ("^(foo|bar)$", 2), ("^a+$", 1), ("^colou?r$", 2),
    (r"\d+", 1), (r"\bbar\b", 1), (r"\s", 1), (r"\w+Error\b", 1),
    (r"get\w+\(", 1), ("(?i)FOO", 2),
])
def test_git_regex_operators(git_search, pattern, expected):
    result = git_search._git_grep(
        git_search.working_directory, pattern, "**/*", True, True, frozenset(),
    )
    assert result is not None
    assert result.startswith(f"Found {expected} match(es):"), result


def test_git_literal_mode_keeps_regex_characters_literal(git_search):
    result = git_search._git_grep(
        git_search.working_directory, "foo|bar", "**/*", True, False, frozenset(),
    )
    assert result is not None and result.startswith("No matches")
