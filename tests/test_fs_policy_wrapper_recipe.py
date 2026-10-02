"""The ``PolicyFileSystem`` recipe from developer-guide part-6/4, run for real.

The class below is the guide's text; keep the two in step. The tests drive
the built-in write tools through it, because the recipe's claim is about
what ``write_file`` / ``replace`` do with it injected, not about the
wrapper in isolation.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentao.capabilities import LocalFileSystem
from agentao.security import PathPolicy, PathPolicyError
from agentao.tools.file_ops import EditTool, WriteFileTool


# --- the recipe, as printed in the guide -------------------------------------
class PolicyFileSystem:
    """A FileSystem wrapper that only lets writes land where the rule allows."""

    def __init__(self, inner, *, writable, immutable=()):
        self._fs = inner
        self._rule = (tuple(writable), tuple(immutable))

    def set_policy(self, *, writable, immutable=()):
        # One reference swap: a write sees the old rule or the new one, never half.
        self._rule = (tuple(writable), tuple(immutable))

    def write_text(self, path, data, *, append=False):
        writable, immutable = self._rule
        try:
            PathPolicy.contain_any(path, writable=writable, immutable=immutable)
        except PathPolicyError as e:
            raise PermissionError(str(e)) from e
        return self._fs.write_text(path, data, append=append)

    def __getattr__(self, name):  # reads, listing, stat: unchanged
        return getattr(self._fs, name)
# ------------------------------------------------------------------------------


@pytest.fixture
def kb(tmp_path):
    root = tmp_path / "kb"
    (root / "raw").mkdir(parents=True)
    (root / "raw" / "source.txt").write_text("original")
    (root / "AGENTAO.md").write_text("rules")
    return root


@pytest.fixture
def fs(kb):
    return PolicyFileSystem(
        LocalFileSystem(), writable=[kb], immutable=[kb / "raw", kb / "AGENTAO.md"],
    )


def _bind(tool, kb, fs):
    tool.working_directory = kb
    tool.filesystem = fs
    return tool


def test_write_inside_the_workspace_succeeds(kb, fs):
    out = _bind(WriteFileTool(), kb, fs).execute(file_path="notes/a.md", content="x")
    assert out.startswith("Successfully"), out
    assert (kb / "notes" / "a.md").read_text() == "x"


def test_write_into_read_only_dir_is_refused(kb, fs):
    out = _bind(WriteFileTool(), kb, fs).execute(file_path="raw/source.txt", content="bad")
    assert "read-only" in out, out
    assert (kb / "raw" / "source.txt").read_text() == "original"


def test_write_through_leaf_symlink_into_read_only_dir_is_refused(kb, fs):
    (kb / "scratch").mkdir()
    os.symlink(kb / "raw" / "source.txt", kb / "scratch" / "link")
    out = _bind(WriteFileTool(), kb, fs).execute(file_path="scratch/link", content="bad")
    assert "read-only" in out, out
    assert (kb / "raw" / "source.txt").read_text() == "original"


def test_replace_on_read_only_file_is_refused(kb, fs):
    out = _bind(EditTool(), kb, fs).execute(
        file_path="AGENTAO.md", old_text="rules", new_text="no rules",
    )
    assert "read-only" in out, out
    assert (kb / "AGENTAO.md").read_text() == "rules"


def test_set_policy_reaches_an_already_bound_tool(kb, fs):
    tool = _bind(WriteFileTool(), kb, fs)
    assert tool.execute(file_path="drafts/a.md", content="x").startswith("Successfully")

    fs.set_policy(writable=[kb], immutable=[kb / "drafts"])
    out = tool.execute(file_path="drafts/a.md", content="y")
    assert "read-only" in out, out
    assert (kb / "drafts" / "a.md").read_text() == "x"


def test_reads_pass_through(kb, fs):
    assert fs.read_bytes(Path(kb / "raw" / "source.txt")) == b"original"


@pytest.mark.parametrize("protected, attempt", [("raw", "RAW/source.txt"), ("AGENTAO.md", "agentao.md")])
def test_case_variant_cannot_create_a_read_only_path(tmp_path, protected, attempt):
    """The reported reproduction: the protected path does not exist yet."""
    kb = tmp_path / "kb"
    kb.mkdir()
    fs = PolicyFileSystem(LocalFileSystem(), writable=[kb], immutable=[kb / protected])
    out = _bind(WriteFileTool(), kb, fs).execute(file_path=attempt, content="bad")
    assert "read-only" in out, out
    assert not (kb / attempt).exists()
