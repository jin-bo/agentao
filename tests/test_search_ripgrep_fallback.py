"""ripgrep fallback for ``search_file_content``.

Tests monkey-patch the *decision* function (``_is_git_repo``,
``_find_executable``) rather than mocking subprocess wholesale; for
argv assertions we use the ``capture_subprocess_run`` fixture from
``tests/conftest.py``.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import List

import pytest

from agentao.tools import search as search_mod
from agentao.tools.search import SearchTextTool


def _seed_tree(root: Path) -> None:
    (root / "a.txt").write_text("alpha needle\n", encoding="utf-8")
    (root / "b.txt").write_text("beta needle\n", encoding="utf-8")


class _BranchProbe:
    """Captures which engine method ``execute()`` reaches; stubs prevent real subprocess work."""

    def __init__(self) -> None:
        self.calls: List[str] = []

    def install(self, tool: SearchTextTool) -> None:
        def _git(*a, **kw):
            self.calls.append("git_grep")
            return "Found 0 match(es):\n\n"  # any non-None returns short-circuits

        def _rg(*a, **kw):
            self.calls.append("ripgrep")
            return "Found 0 match(es):\n\n"

        tool._git_grep = _git  # type: ignore[assignment]
        tool._ripgrep = _rg  # type: ignore[assignment]


def test_git_grep_chosen_when_in_git_repo_and_git_present(monkeypatch, tmp_path, search_tool):
    _seed_tree(tmp_path)
    search_tool._is_git_repo = lambda d: True  # type: ignore[assignment]
    monkeypatch.setattr(search_mod, "_find_executable", lambda name: f"/usr/bin/{name}")
    probe = _BranchProbe()
    probe.install(search_tool)

    search_tool.execute(pattern="needle", directory=str(tmp_path))

    assert probe.calls == ["git_grep"]


def test_ripgrep_chosen_when_not_in_git_repo(monkeypatch, tmp_path, search_tool):
    """Plain directory (e.g. /tmp tarball) — git grep skipped, rg used."""
    _seed_tree(tmp_path)
    search_tool._is_git_repo = lambda d: False  # type: ignore[assignment]
    monkeypatch.setattr(
        search_mod, "_find_executable", lambda name: "/usr/bin/rg" if name == "rg" else None
    )
    probe = _BranchProbe()
    probe.install(search_tool)

    search_tool.execute(pattern="needle", directory=str(tmp_path))

    assert probe.calls == ["ripgrep"]


def test_ripgrep_chosen_when_in_git_repo_but_git_grep_yields_none(monkeypatch, tmp_path, search_tool):
    """In a repo, but git grep errored or git is missing: rg picks up."""
    _seed_tree(tmp_path)
    search_tool._is_git_repo = lambda d: True  # type: ignore[assignment]
    search_tool._git_grep = lambda *a, **kw: None  # type: ignore[assignment]
    monkeypatch.setattr(
        search_mod, "_find_executable", lambda name: "/usr/bin/rg" if name == "rg" else None
    )

    captured: List[str] = []

    def _rg(*a, **kw):
        captured.append("ripgrep")
        return "Found 0 match(es):\n\n"

    search_tool._ripgrep = _rg  # type: ignore[assignment]

    search_tool.execute(pattern="needle", directory=str(tmp_path))

    assert captured == ["ripgrep"]


def test_python_fallback_when_neither_engine_available(monkeypatch, tmp_path, search_tool):
    _seed_tree(tmp_path)
    search_tool._is_git_repo = lambda d: False  # type: ignore[assignment]
    monkeypatch.setattr(search_mod, "_find_executable", lambda name: None)

    out = search_tool.execute(pattern="needle", directory=str(tmp_path))

    # Python fallback ran and found both seed files.
    assert "a.txt" in out
    assert "b.txt" in out


def test_ripgrep_cmd_default_flags(search_tool, capture_subprocess_run, tmp_path):
    search_tool._ripgrep(
        directory=tmp_path,
        pattern="needle",
        file_pattern="**/*",
        case_sensitive=True,
        regex=False,
    )

    assert len(capture_subprocess_run) == 1
    cmd = capture_subprocess_run[0]
    assert cmd[0] == "rg"
    assert "--line-number" in cmd
    assert "--no-heading" in cmd
    assert "-F" in cmd  # default: literal mode
    assert "-i" not in cmd  # default: case-sensitive
    # No file_pattern glob when default; skip-dir exclusions only fire when
    # the caller passes a non-empty ``skip`` set (this test passes none).
    assert "--glob" not in cmd
    # `--` terminates options before the pattern (CVE-style hardening).
    assert cmd[-3:] == ["--", "needle", "."]


def test_ripgrep_cmd_skip_dirs_become_negative_globs(search_tool, capture_subprocess_run, tmp_path):
    """``skip`` translates into ``--glob '!<dir>'`` *before* ``--``.

    Source-level exclusion matters most in the non-git branch where there's
    no ``.gitignore`` to prune heavy dirs. Post-filtering rg's output (the
    older approach) still scanned them.
    """
    search_tool._ripgrep(
        directory=tmp_path,
        pattern="needle",
        file_pattern="**/*",
        case_sensitive=True,
        regex=False,
        skip=frozenset({"node_modules", ".git"}),
    )

    cmd = capture_subprocess_run[0]
    # Both negative globs present, in sorted order for determinism.
    assert "--glob" in cmd
    glob_args = [cmd[i + 1] for i, t in enumerate(cmd) if t == "--glob"]
    assert "!.git" in glob_args
    assert "!node_modules" in glob_args
    # All globs sit before the option terminator.
    dash_dash_idx = cmd.index("--")
    for i, t in enumerate(cmd):
        if t == "--glob":
            assert i < dash_dash_idx
            assert i + 1 < dash_dash_idx


def test_ripgrep_cmd_case_insensitive(search_tool, capture_subprocess_run, tmp_path):
    search_tool._ripgrep(
        directory=tmp_path,
        pattern="needle",
        file_pattern="**/*",
        case_sensitive=False,
        regex=False,
    )

    assert "-i" in capture_subprocess_run[0]


def test_ripgrep_cmd_regex_drops_fixed_string_flag(search_tool, capture_subprocess_run, tmp_path):
    search_tool._ripgrep(
        directory=tmp_path,
        pattern=r"need\w+",
        file_pattern="**/*",
        case_sensitive=True,
        regex=True,
    )

    assert "-F" not in capture_subprocess_run[0]


def test_ripgrep_cmd_file_pattern_passes_through_to_glob(search_tool, capture_subprocess_run, tmp_path):
    search_tool._ripgrep(
        directory=tmp_path,
        pattern="needle",
        file_pattern="**/*.py",
        case_sensitive=True,
        regex=False,
    )

    cmd = capture_subprocess_run[0]
    assert "--glob" in cmd
    glob_idx = cmd.index("--glob")
    assert cmd[glob_idx + 1] == "**/*.py"


def test_ripgrep_cmd_skip_globs_after_file_pattern_glob(search_tool, capture_subprocess_run, tmp_path):
    """Skip exclusions must come AFTER any positive file-pattern glob.

    rg gives the *last* matching glob precedence: if ``--glob '**/*.js'``
    were placed after ``--glob '!node_modules'``, the include would
    re-enable matches under ``node_modules/`` and silently defeat the
    skip set.
    """
    search_tool._ripgrep(
        directory=tmp_path,
        pattern="needle",
        file_pattern="**/*.js",
        case_sensitive=True,
        regex=False,
        skip=frozenset({"node_modules", "build"}),
    )

    cmd = capture_subprocess_run[0]
    glob_args = [cmd[i + 1] for i, t in enumerate(cmd) if t == "--glob"]
    pos_idx = glob_args.index("**/*.js")
    assert glob_args.index("!build") > pos_idx
    assert glob_args.index("!node_modules") > pos_idx


def test_ripgrep_internal_error_returns_none(monkeypatch, tmp_path, search_tool):
    """rg exit code 2 => fall through to caller's Python fallback."""

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=2, stdout="", stderr="rg: error")

    monkeypatch.setattr(search_mod.subprocess, "run", fake_run)

    result = search_tool._ripgrep(
        directory=tmp_path,
        pattern="needle",
        file_pattern="**/*",
        case_sensitive=True,
        regex=False,
    )

    assert result is None


def test_ripgrep_filenotfound_returns_none(monkeypatch, tmp_path, search_tool):
    """rg vanished between probe and exec — no crash, fall through."""

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("rg")

    monkeypatch.setattr(search_mod.subprocess, "run", fake_run)

    result = search_tool._ripgrep(
        directory=tmp_path,
        pattern="needle",
        file_pattern="**/*",
        case_sensitive=True,
        regex=False,
    )

    assert result is None


def test_ripgrep_skip_filter_applied_to_lines(monkeypatch, tmp_path, search_tool):
    """Lines whose path component is in the skip set are dropped."""
    stdout = (
        "src/main.py:1:hit one\n"
        "node_modules/lodash/index.js:1:hit two\n"
        ".git/config:1:hit three\n"
    )

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(search_mod.subprocess, "run", fake_run)

    out = search_tool._ripgrep(
        directory=tmp_path,
        pattern="hit",
        file_pattern="**/*",
        case_sensitive=True,
        regex=False,
        skip=search_mod.DEFAULT_SKIP_DIRS,
    )

    assert out is not None
    assert "src/main.py" in out
    assert "node_modules" not in out
    assert ".git/config" not in out


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
def test_ripgrep_end_to_end(tmp_path, search_tool):
    (tmp_path / "needle.txt").write_text("the needle is here\n", encoding="utf-8")
    (tmp_path / "other.txt").write_text("nothing relevant\n", encoding="utf-8")

    out = search_tool._ripgrep(
        directory=tmp_path,
        pattern="needle",
        file_pattern="**/*",
        case_sensitive=True,
        regex=False,
    )

    assert out is not None
    assert "needle.txt" in out
    assert "the needle is here" in out
    assert "other.txt" not in out


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
def test_execute_uses_ripgrep_outside_git_repo(tmp_path, search_tool):
    """End-to-end through ``execute`` in a non-git directory."""
    (tmp_path / "f.txt").write_text("the needle is here\n", encoding="utf-8")

    # Ensure we don't accidentally land on the git grep branch (tmp_path
    # likely isn't a git repo, but be explicit).
    search_tool._is_git_repo = lambda d: False  # type: ignore[assignment]

    out = search_tool.execute(pattern="needle", directory=str(tmp_path))

    assert "f.txt" in out
    assert "the needle is here" in out
