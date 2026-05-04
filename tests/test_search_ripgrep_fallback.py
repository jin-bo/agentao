"""ripgrep fallback for ``search_file_content``.

Tests monkey-patch the *decision* function (``_is_git_repo``,
``_find_executable``) rather than mocking subprocess wholesale; for
argv assertions we replace ``subprocess.run`` directly.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import List

import pytest

from agentao.tools import search as search_mod
from agentao.tools.search import SearchTextTool


def _new_tool(tmp_path: Path) -> SearchTextTool:
    tool = SearchTextTool()
    tool.working_directory = tmp_path
    return tool


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


def test_git_grep_chosen_when_in_git_repo_and_git_present(monkeypatch, tmp_path):
    _seed_tree(tmp_path)
    tool = _new_tool(tmp_path)
    tool._is_git_repo = lambda d: True  # type: ignore[assignment]
    monkeypatch.setattr(search_mod, "_find_executable", lambda name: f"/usr/bin/{name}")
    probe = _BranchProbe()
    probe.install(tool)

    tool.execute(pattern="needle", directory=str(tmp_path))

    assert probe.calls == ["git_grep"]


def test_ripgrep_chosen_when_not_in_git_repo(monkeypatch, tmp_path):
    """Plain directory (e.g. /tmp tarball) — git grep skipped, rg used."""
    _seed_tree(tmp_path)
    tool = _new_tool(tmp_path)
    tool._is_git_repo = lambda d: False  # type: ignore[assignment]
    monkeypatch.setattr(
        search_mod, "_find_executable", lambda name: "/usr/bin/rg" if name == "rg" else None
    )
    probe = _BranchProbe()
    probe.install(tool)

    tool.execute(pattern="needle", directory=str(tmp_path))

    assert probe.calls == ["ripgrep"]


def test_ripgrep_chosen_when_in_git_repo_but_git_grep_yields_none(monkeypatch, tmp_path):
    """In a repo, but git grep errored or git is missing: rg picks up."""
    _seed_tree(tmp_path)
    tool = _new_tool(tmp_path)
    tool._is_git_repo = lambda d: True  # type: ignore[assignment]
    tool._git_grep = lambda *a, **kw: None  # type: ignore[assignment]
    monkeypatch.setattr(
        search_mod, "_find_executable", lambda name: "/usr/bin/rg" if name == "rg" else None
    )

    captured: List[str] = []

    def _rg(*a, **kw):
        captured.append("ripgrep")
        return "Found 0 match(es):\n\n"

    tool._ripgrep = _rg  # type: ignore[assignment]

    tool.execute(pattern="needle", directory=str(tmp_path))

    assert captured == ["ripgrep"]


def test_python_fallback_when_neither_engine_available(monkeypatch, tmp_path):
    _seed_tree(tmp_path)
    tool = _new_tool(tmp_path)
    tool._is_git_repo = lambda d: False  # type: ignore[assignment]
    monkeypatch.setattr(search_mod, "_find_executable", lambda name: None)

    out = tool.execute(pattern="needle", directory=str(tmp_path))

    # Python fallback ran and found both seed files.
    assert "a.txt" in out
    assert "b.txt" in out


def _capture_rg_cmd(monkeypatch) -> List[List[str]]:
    """Replace ``subprocess.run`` inside the search module with a capture."""
    captured: List[List[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        # Return an exit-1 (no matches) result so _ripgrep returns the
        # canonical "no matches" string — keeps the test focused on argv.
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(search_mod.subprocess, "run", fake_run)
    return captured


def test_ripgrep_cmd_default_flags(monkeypatch, tmp_path):
    captured = _capture_rg_cmd(monkeypatch)
    tool = _new_tool(tmp_path)

    tool._ripgrep(
        directory=tmp_path,
        pattern="needle",
        file_pattern="**/*",
        case_sensitive=True,
        regex=False,
    )

    assert len(captured) == 1
    cmd = captured[0]
    assert cmd[0] == "rg"
    assert "--line-number" in cmd
    assert "--no-heading" in cmd
    assert "-F" in cmd  # default: literal mode
    assert "-i" not in cmd  # default: case-sensitive
    assert "--glob" not in cmd  # default file_pattern is omitted
    assert cmd[-2:] == ["needle", "."]


def test_ripgrep_cmd_case_insensitive(monkeypatch, tmp_path):
    captured = _capture_rg_cmd(monkeypatch)
    tool = _new_tool(tmp_path)

    tool._ripgrep(
        directory=tmp_path,
        pattern="needle",
        file_pattern="**/*",
        case_sensitive=False,
        regex=False,
    )

    assert "-i" in captured[0]


def test_ripgrep_cmd_regex_drops_fixed_string_flag(monkeypatch, tmp_path):
    captured = _capture_rg_cmd(monkeypatch)
    tool = _new_tool(tmp_path)

    tool._ripgrep(
        directory=tmp_path,
        pattern=r"need\w+",
        file_pattern="**/*",
        case_sensitive=True,
        regex=True,
    )

    assert "-F" not in captured[0]


def test_ripgrep_cmd_file_pattern_passes_through_to_glob(monkeypatch, tmp_path):
    captured = _capture_rg_cmd(monkeypatch)
    tool = _new_tool(tmp_path)

    tool._ripgrep(
        directory=tmp_path,
        pattern="needle",
        file_pattern="**/*.py",
        case_sensitive=True,
        regex=False,
    )

    cmd = captured[0]
    assert "--glob" in cmd
    glob_idx = cmd.index("--glob")
    assert cmd[glob_idx + 1] == "**/*.py"


def test_ripgrep_internal_error_returns_none(monkeypatch, tmp_path):
    """rg exit code 2 => fall through to caller's Python fallback."""

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=2, stdout="", stderr="rg: error")

    monkeypatch.setattr(search_mod.subprocess, "run", fake_run)
    tool = _new_tool(tmp_path)

    result = tool._ripgrep(
        directory=tmp_path,
        pattern="needle",
        file_pattern="**/*",
        case_sensitive=True,
        regex=False,
    )

    assert result is None


def test_ripgrep_filenotfound_returns_none(monkeypatch, tmp_path):
    """rg vanished between probe and exec — no crash, fall through."""

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("rg")

    monkeypatch.setattr(search_mod.subprocess, "run", fake_run)
    tool = _new_tool(tmp_path)

    result = tool._ripgrep(
        directory=tmp_path,
        pattern="needle",
        file_pattern="**/*",
        case_sensitive=True,
        regex=False,
    )

    assert result is None


def test_ripgrep_skip_filter_applied_to_lines(monkeypatch, tmp_path):
    """Lines whose path component is in the skip set are dropped."""
    stdout = (
        "src/main.py:1:hit one\n"
        "node_modules/lodash/index.js:1:hit two\n"
        ".git/config:1:hit three\n"
    )

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(search_mod.subprocess, "run", fake_run)
    tool = _new_tool(tmp_path)

    out = tool._ripgrep(
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
def test_ripgrep_end_to_end(tmp_path):
    (tmp_path / "needle.txt").write_text("the needle is here\n", encoding="utf-8")
    (tmp_path / "other.txt").write_text("nothing relevant\n", encoding="utf-8")

    tool = _new_tool(tmp_path)
    out = tool._ripgrep(
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
def test_execute_uses_ripgrep_outside_git_repo(tmp_path):
    """End-to-end through ``execute`` in a non-git directory."""
    (tmp_path / "f.txt").write_text("the needle is here\n", encoding="utf-8")

    tool = _new_tool(tmp_path)
    # Ensure we don't accidentally land on the git grep branch (tmp_path
    # likely isn't a git repo, but be explicit).
    tool._is_git_repo = lambda d: False  # type: ignore[assignment]

    out = tool.execute(pattern="needle", directory=str(tmp_path))

    assert "f.txt" in out
    assert "the needle is here" in out
