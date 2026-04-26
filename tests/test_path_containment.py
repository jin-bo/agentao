"""Integration tests: PathPolicy applied through real tool instances.

These tests exercise the eight scenarios scoped for P0 — write_file /
replace / shell each handling relative-inside, absolute-inside, ``..``
escape, and symlink escape — to lock in that the wiring in
``file_ops.py`` and ``shell.py`` correctly surfaces ``PathPolicyError``
to the LLM as ``Error: ...`` strings.

Each test gets its own ``tmp_path`` (pytest fixture). To represent
"outside" the project root, we use ``tmp_path/project`` as the root and
``tmp_path/outside`` as a sibling — both inside the test's isolated
``tmp_path`` so concurrent or sequential tests cannot interfere with each
other's fixtures.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentao.tools.file_ops import EditTool, WriteFileTool
from agentao.tools.shell import ShellTool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project_root(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    return root


@pytest.fixture
def outside(tmp_path):
    out = tmp_path / "outside"
    out.mkdir()
    return out


def _bind(tool, root: Path):
    tool.working_directory = root.resolve()
    return tool


# ---------------------------------------------------------------------------
# WriteFileTool
# ---------------------------------------------------------------------------


def test_write_file_relative_inside_succeeds(project_root):
    tool = _bind(WriteFileTool(), project_root)
    result = tool.execute(file_path="hello.txt", content="hi")
    assert "Successfully" in result
    assert (project_root / "hello.txt").read_text() == "hi"


def test_write_file_absolute_inside_succeeds(project_root):
    tool = _bind(WriteFileTool(), project_root)
    target = project_root / "deep" / "hello.txt"
    result = tool.execute(file_path=str(target), content="hi")
    assert "Successfully" in result
    assert target.read_text() == "hi"


def test_write_file_dotdot_escape_rejected(project_root, outside):
    tool = _bind(WriteFileTool(), project_root)
    bad = outside / "outside.txt"
    result = tool.execute(file_path="../outside/outside.txt", content="x")
    assert result.startswith("Error:")
    assert "PathPolicy" in result
    assert not bad.exists()


def test_write_file_absolute_outside_rejected(project_root, outside):
    tool = _bind(WriteFileTool(), project_root)
    bad = outside / "outside.txt"
    result = tool.execute(file_path=str(bad), content="x")
    assert result.startswith("Error:")
    assert not bad.exists()


def test_write_file_symlink_escape_rejected(project_root, outside):
    """Symlink inside root pointing outside must be refused."""
    target_outside = outside / "outside.txt"
    target_outside.write_text("original")
    link = project_root / "link.txt"
    os.symlink(target_outside, link)

    tool = _bind(WriteFileTool(), project_root)
    result = tool.execute(file_path="link.txt", content="overwritten")

    assert result.startswith("Error:")
    assert target_outside.read_text() == "original"  # untouched


# ---------------------------------------------------------------------------
# EditTool
# ---------------------------------------------------------------------------


def test_edit_tool_now_requires_confirmation():
    """Regression guard for the adjacent gap: EditTool gained
    requires_confirmation=True in P0."""
    assert EditTool().requires_confirmation is True


def test_edit_relative_inside_succeeds(project_root):
    target = project_root / "doc.txt"
    target.write_text("hello world")
    tool = _bind(EditTool(), project_root)
    result = tool.execute(file_path="doc.txt", old_text="world", new_text="there")
    assert "Replaced" in result
    assert target.read_text() == "hello there"


def test_edit_absolute_inside_succeeds(project_root):
    target = project_root / "doc.txt"
    target.write_text("hello world")
    tool = _bind(EditTool(), project_root)
    result = tool.execute(file_path=str(target), old_text="world", new_text="there")
    assert "Replaced" in result


def test_edit_dotdot_escape_rejected(project_root, outside):
    target_outside = outside / "outside.txt"
    target_outside.write_text("hello world")

    tool = _bind(EditTool(), project_root)
    result = tool.execute(
        file_path="../outside/outside.txt", old_text="world", new_text="x"
    )

    assert result.startswith("Error:")
    assert "PathPolicy" in result
    assert target_outside.read_text() == "hello world"  # untouched


def test_edit_symlink_escape_rejected(project_root, outside):
    target_outside = outside / "outside.txt"
    target_outside.write_text("hello world")
    link = project_root / "link.txt"
    os.symlink(target_outside, link)

    tool = _bind(EditTool(), project_root)
    result = tool.execute(file_path="link.txt", old_text="world", new_text="x")

    assert result.startswith("Error:")
    assert target_outside.read_text() == "hello world"


# ---------------------------------------------------------------------------
# ShellTool
# ---------------------------------------------------------------------------


def test_shell_cwd_relative_inside_succeeds(project_root):
    (project_root / "sub").mkdir()
    tool = _bind(ShellTool(), project_root)
    result = tool.execute(command="pwd", working_directory="sub")
    assert "Error:" not in result.splitlines()[0]
    assert str((project_root / "sub").resolve()) in result


def test_shell_cwd_dotdot_escape_rejected(project_root):
    tool = _bind(ShellTool(), project_root)
    result = tool.execute(command="pwd", working_directory="../outside")
    assert result.startswith("Error:")
    assert "PathPolicy" in result


def test_shell_cwd_absolute_outside_rejected(project_root):
    tool = _bind(ShellTool(), project_root)
    result = tool.execute(command="pwd", working_directory="/")
    assert result.startswith("Error:")
    assert "PathPolicy" in result


def test_shell_cwd_symlinked_to_outside_rejected(project_root, outside):
    link = project_root / "linkdir"
    os.symlink(outside, link)

    tool = _bind(ShellTool(), project_root)
    result = tool.execute(command="pwd", working_directory="linkdir")
    assert result.startswith("Error:")
    assert "PathPolicy" in result


# ---------------------------------------------------------------------------
# Legacy CLI (no bound working_directory) — uses process cwd snapshot
# ---------------------------------------------------------------------------


def test_legacy_cli_write_inside_cwd_succeeds(project_root, monkeypatch):
    """When working_directory is unbound, the policy snapshots Path.cwd().
    A write inside that cwd should still go through."""
    monkeypatch.chdir(project_root)
    tool = WriteFileTool()  # working_directory left as None
    result = tool.execute(file_path="hello.txt", content="hi")
    assert "Successfully" in result
    assert (project_root / "hello.txt").read_text() == "hi"


def test_legacy_cli_write_outside_cwd_rejected(project_root, outside, monkeypatch):
    """Legacy CLI must still refuse escapes from the snapshot cwd."""
    monkeypatch.chdir(project_root)
    bad = outside / "outside.txt"
    tool = WriteFileTool()
    result = tool.execute(file_path="../outside/outside.txt", content="x")
    assert result.startswith("Error:")
    assert not bad.exists()
