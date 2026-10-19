"""``search_file_content`` in a git work tree finds untracked and ignored-directory files (#563).

``git grep`` reads tracked files only, and its exit 1 used to be a final
"No matches". These tests use a real repository: the earlier tests stub
``_is_git_repo`` / ``_git_grep`` and so could not see the gap.
"""

import os
import shutil

import pytest

from agentao.capabilities.process import run_captured
from agentao.tools import search as search_mod
from agentao.tools.search import SearchTextTool


def _git(cwd, *args):
    result = run_captured(["git", *args], cwd=str(cwd), timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.fixture(params=["rg", "python"])
def engine(request, monkeypatch):
    """The fallback that serves an ignored directory: ripgrep, or the Python scan."""
    if request.param == "rg":
        if shutil.which("rg") is None:
            pytest.skip("ripgrep is not installed")
    else:
        monkeypatch.setattr(search_mod, "_find_executable", lambda name: None)
    return request.param


@pytest.fixture(params=["/kb/\n", "kb/*\n", "/kb/**\n"])
def repo(tmp_path, monkeypatch, request):
    """``outer/`` repo: ``tracked.md`` added, ``kb/`` ignored, ``new.md`` untracked.

    The ``.gitignore`` rules vary: ripgrep reads the parent ``.gitignore``, and
    every rule but ``/kb/`` used to hide the files inside ``kb/`` from it.
    """
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    # A developer's global excludesfile must not decide what is ignored here.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "outer"
    root.mkdir()
    _git(root, "init", "-q")
    (root / ".gitignore").write_text(request.param, encoding="utf-8")
    (root / "tracked.md").write_text("title: tracked\n", encoding="utf-8")
    _git(root, "add", ".gitignore", "tracked.md")
    (root / "new.md").write_text("title: new\n", encoding="utf-8")
    (root / "kb" / "sub").mkdir(parents=True)
    (root / "kb" / "SCHEMA.md").write_text("title: hello\n", encoding="utf-8")
    (root / "kb" / "sub" / "deep.md").write_text("title: deep\n", encoding="utf-8")
    return root


def _search(directory, pattern="title"):
    tool = SearchTextTool()
    tool.working_directory = directory
    return tool.execute(pattern=pattern, directory=".")


def test_ignored_directory_is_searched(repo, engine):
    result = _search(repo / "kb")
    assert result.startswith("Found 2 match(es):"), result
    assert "title: hello" in result and "title: deep" in result


def test_subdirectory_of_ignored_directory_is_searched(repo, engine):
    result = _search(repo / "kb" / "sub")
    assert result.startswith("Found 1 match(es):"), result
    assert "title: deep" in result


def test_untracked_file_is_found_when_nothing_tracked_matches(repo):
    result = _search(repo, pattern="title: new")
    assert result.startswith("Found 1 match(es):"), result
    assert "new.md" in result


def test_tracked_and_untracked_matches_are_both_reported(repo):
    """Falling back only on exit 1 would still drop ``new.md`` here."""
    result = _search(repo)
    assert "tracked.md" in result and "new.md" in result, result


def test_search_from_root_still_skips_ignored_files(repo):
    result = _search(repo)
    assert result.startswith("Found 2 match(es):"), result
    assert "SCHEMA.md" not in result and "deep.md" not in result


def test_true_no_match_in_repo_is_still_reported(repo):
    assert _search(repo, pattern="absent-needle").startswith("No matches found")


def test_no_git_tree_is_searched(tmp_path, engine):
    (tmp_path / "SCHEMA.md").write_text("title: hello\n", encoding="utf-8")
    result = _search(tmp_path)
    assert result.startswith("Found 1 match(es):"), result
