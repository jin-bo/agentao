"""``/diff`` and ``cli/git_changes.py`` (#662), against real git repos."""

from __future__ import annotations

import io
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from agentao.capabilities.process import run_captured
from agentao.cli import git_changes
from agentao.cli.commands import diff as diff_cmd
from agentao.cli.git_changes import collect_changes

pytestmark = [
    pytest.mark.usefixtures("isolated_home"),
    pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed"),
]

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell hook")


def _git(repo: Path, *args: str) -> str:
    proc = run_captured(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=str(repo), timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    # Keep git from finding a repo above tmp_path.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "a.txt").write_text("one\ntwo\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _run_diff(cwd: Path, monkeypatch, args: str = "") -> str:
    buf = io.StringIO()
    monkeypatch.setattr(diff_cmd, "console", Console(file=buf, width=200, color_system=None))
    cli = SimpleNamespace(agent=SimpleNamespace(working_directory=cwd))
    diff_cmd.handle_diff_command(cli, args)
    return buf.getvalue()


# ── collection ──────────────────────────────────────────────────────────────


def test_not_a_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    plain = tmp_path / "plain"
    plain.mkdir()
    assert collect_changes(plain) is None
    assert "Not a Git repository" in _run_diff(plain, monkeypatch)


def test_clean_tree(repo, monkeypatch):
    changes = collect_changes(repo)
    assert changes is not None and changes.clean
    assert "Working tree clean" in _run_diff(repo, monkeypatch)


def test_staged_unstaged_and_untracked_are_kept_apart(repo, monkeypatch):
    (repo / "a.txt").write_text("one\nTWO\n")
    _git(repo, "add", "a.txt")
    (repo / "a.txt").write_text("one\nTWO\nthree\n")
    (repo / "new.txt").write_text("fresh\n")
    index = repo / ".git" / "index"
    before = (index.read_bytes(), index.stat().st_mtime_ns)

    changes = collect_changes(repo)

    assert changes is not None and not changes.clean
    assert "+TWO" in changes.staged.text and "+three" not in changes.staged.text
    assert "+three" in changes.unstaged.text and "+TWO" not in changes.unstaged.text
    assert changes.untracked == ["new.txt"]
    # Read-only: the index is neither rewritten nor touched.
    assert (index.read_bytes(), index.stat().st_mtime_ns) == before
    assert (repo / "a.txt").read_text() == "one\nTWO\nthree\n"

    out = _run_diff(repo, monkeypatch)
    for heading in ("Staged changes", "Unstaged changes", "Untracked files", "new.txt"):
        assert heading in out


def test_staged_before_first_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    _git(tmp_path, "init", "-q")
    (tmp_path / "x.txt").write_text("hello\n")
    _git(tmp_path, "add", "x.txt")
    changes = collect_changes(tmp_path)
    assert changes is not None and "+hello" in changes.staged.text


def test_subdirectory_reports_the_whole_repo(repo):
    (repo / "sub").mkdir()
    (repo / "top.txt").write_text("x\n")
    changes = collect_changes(repo / "sub")
    assert changes is not None
    assert changes.root.resolve() == repo.resolve()
    assert "top.txt" in changes.untracked


def test_binary_file_is_one_line(repo):
    (repo / "b.bin").write_bytes(b"\0\1\2" * 10)
    _git(repo, "add", "b.bin")
    _git(repo, "commit", "-q", "-m", "bin")
    (repo / "b.bin").write_bytes(b"\0\3\4" * 10)
    changes = collect_changes(repo)
    assert changes is not None and "Binary files" in changes.unstaged.text


# ── bounds ──────────────────────────────────────────────────────────────────


def test_long_diff_is_cut_and_says_so(repo, monkeypatch):
    monkeypatch.setattr(git_changes, "MAX_DIFF_LINES", 20)
    (repo / "a.txt").write_text("".join(f"line {i}\n" for i in range(100)))
    changes = collect_changes(repo)
    assert changes is not None
    assert len(changes.unstaged.text.splitlines()) == 20
    assert changes.unstaged.omitted_lines > 0
    out = _run_diff(repo, monkeypatch)
    assert f"{changes.unstaged.omitted_lines} more lines omitted" in out


def test_oversized_diff_is_not_shown_in_part(repo, monkeypatch):
    monkeypatch.setattr(git_changes, "MAX_COLLECT_BYTES", 200)
    (repo / "a.txt").write_text("x" * 5000 + "\n")
    changes = collect_changes(repo)
    assert changes is not None and changes.unstaged.too_large and not changes.clean
    assert "not shown" in _run_diff(repo, monkeypatch)


def test_untracked_list_is_capped(repo, monkeypatch):
    monkeypatch.setattr(git_changes, "MAX_UNTRACKED", 3)
    for i in range(5):
        (repo / f"n{i}.txt").write_text("x\n")
    changes = collect_changes(repo)
    assert changes is not None
    assert len(changes.untracked) == 3 and changes.omitted_untracked == 2
    assert "… and 2 more" in _run_diff(repo, monkeypatch)


# ── display safety ──────────────────────────────────────────────────────────


@posix_only  # ``[`` and ESC in a file name
def test_repo_text_cannot_style_or_drive_the_terminal(repo, monkeypatch):
    (repo / "a.txt").write_text("one\n\x1b[2Jtwo [bold red]x[/bold red]\n")
    (repo / "[blink]n.txt").write_text("x\n")
    out = _run_diff(repo, monkeypatch)
    assert "\x1b" not in out
    assert "[bold red]x[/bold red]" in out  # printed literally, not applied
    assert "[blink]n.txt" in out


def test_arguments_are_refused(repo, monkeypatch, capsys):
    (repo / "new.txt").write_text("x\n")
    out = _run_diff(repo, monkeypatch, "HEAD~1")
    assert "new.txt" not in out
    assert "takes no arguments" in capsys.readouterr().out


# ── repo-configured commands do not run ─────────────────────────────────────


def _hook(repo: Path, name: str) -> tuple[Path, str]:
    marker = repo.parent / f"{name}-ran"
    hook = repo.parent / f"{name}.sh"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    hook.chmod(0o755)
    return marker, hook.as_posix()


@posix_only
def test_planted_drivers_do_not_run(repo):
    fs_marker, fs_hook = _hook(repo, "fsmonitor")
    ext_marker, ext_hook = _hook(repo, "external")
    tc_marker, tc_hook = _hook(repo, "textconv")
    (repo / "a.txt").write_text("changed\n")
    _git(repo, "add", "a.txt")
    (repo / "a.txt").write_text("changed again\n")
    # Planted last: the setup's own ``git add`` would run the fsmonitor.
    _git(repo, "config", "core.fsmonitor", fs_hook)
    _git(repo, "config", "diff.external", ext_hook)
    _git(repo, "config", "diff.conv.textconv", tc_hook)
    (repo / ".gitattributes").write_text("*.txt diff=conv\n")

    changes = collect_changes(repo)

    assert changes is not None and "+changed again" in changes.unstaged.text
    assert not fs_marker.exists()
    assert not ext_marker.exists()
    assert not tc_marker.exists()
    # Control: a plain ``git diff`` does run the planted driver, so the
    # assertions above are not passing by default.
    run_captured(["git", "diff"], cwd=str(repo), timeout=10, env={**os.environ})
    assert ext_marker.exists() and fs_marker.exists()
    run_captured(["git", "diff", "--no-ext-diff"], cwd=str(repo), timeout=10, env={**os.environ})
    assert tc_marker.exists()


# ── display regressions ─────────────────────────────────────────────────────


def test_trailing_backslashes_print_verbatim(repo, monkeypatch):
    # rich.markup.escape drops a backslash from "\\\\" at the end of a line
    # and prints the closing tag for "\\\\\\"; Text output keeps them.
    (repo / "a.txt").write_text("one\\\\\ntwo\\\\\\\n")
    out = _run_diff(repo, monkeypatch)
    assert "+one\\\\\n" in out
    assert "+two\\\\\\\n" in out
    assert "[/" not in out


def test_removed_line_starting_with_dashes_is_not_a_header(repo, monkeypatch):
    (repo / "a.txt").write_text("-- note\nkeep\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "dashes")
    (repo / "a.txt").write_text("keep\n")
    buf = io.StringIO()
    monkeypatch.setattr(
        diff_cmd, "console",
        Console(file=buf, width=200, force_terminal=True, color_system="standard"),
    )
    cli = SimpleNamespace(agent=SimpleNamespace(working_directory=repo))
    diff_cmd.handle_diff_command(cli, "")
    line = next(ln for ln in buf.getvalue().splitlines() if "--- note" in ln)
    assert "\x1b[31m" in line  # red, not bold


@posix_only
def test_untracked_name_with_newline_is_one_entry(repo, monkeypatch):
    (repo / "evil\n  fake.txt").write_text("x")
    out = _run_diff(repo, monkeypatch)
    assert "  evil\\n  fake.txt" in out
    assert "\n  fake.txt" not in out


def test_missing_working_directory_is_not_git_missing(tmp_path, monkeypatch):
    out = _run_diff(tmp_path / "gone", monkeypatch)
    assert "does not exist" in out
    assert "not installed" not in out


def test_rev_parse_failure_other_than_no_repo_is_an_error(repo, monkeypatch):
    out = _run_diff(repo / ".git", monkeypatch)
    assert "Not a Git repository" not in out
    assert "/diff:" in out
