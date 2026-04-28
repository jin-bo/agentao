"""Issue #9 — file/search tools route reads/writes through an injected FileSystem.

A swappable :class:`agentao.capabilities.FileSystem` means embedded hosts
can redirect tool IO through Docker exec, virtual filesystems, or audit
proxies without monkey-patching ``open()`` / ``Path``. The tests below
confirm the wire-up: a fake FS captures every call the tool routes
through it, and the default tools never touch the real disk when one is
injected.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import pytest

from agentao.capabilities import FileEntry, FileStat, FileSystem
from agentao.tools.file_ops import (
    EditTool,
    ReadFileTool,
    ReadFolderTool,
    WriteFileTool,
)
from agentao.tools.search import FindFilesTool, SearchTextTool


class FakeFS:
    """Minimal in-memory :class:`FileSystem` used to assert call routing."""

    def __init__(self, files: Dict[str, str] | None = None):
        self._files: Dict[str, bytes] = {
            k: v.encode("utf-8") for k, v in (files or {}).items()
        }
        self.calls: List[str] = []

    def read_bytes(self, path: Path) -> bytes:
        self.calls.append(f"read_bytes:{path}")
        if str(path) not in self._files:
            raise FileNotFoundError(str(path))
        return self._files[str(path)]

    def read_partial(self, path: Path, n: int) -> bytes:
        self.calls.append(f"read_partial:{path}:{n}")
        if str(path) not in self._files:
            raise FileNotFoundError(str(path))
        return self._files[str(path)][:n]

    def open_text(self, path: Path):
        self.calls.append(f"open_text:{path}")
        if str(path) not in self._files:
            raise FileNotFoundError(str(path))
        text = self._files[str(path)].decode("utf-8")
        for line in text.splitlines(keepends=True):
            yield line

    def write_text(self, path: Path, data: str, *, append: bool = False) -> None:
        self.calls.append(f"write_text:{path}:append={append}")
        if append and str(path) in self._files:
            self._files[str(path)] = self._files[str(path)] + data.encode("utf-8")
        else:
            self._files[str(path)] = data.encode("utf-8")

    def list_dir(self, path: Path) -> List[FileEntry]:
        self.calls.append(f"list_dir:{path}")
        return [
            FileEntry(name="a.txt", is_dir=False, is_file=True, size=3),
            FileEntry(name="sub", is_dir=True, is_file=False, size=0),
        ]

    def glob(self, base: Path, pattern: str, *, recursive: bool):
        self.calls.append(f"glob:{base}:{pattern}:recursive={recursive}")
        return [base / "a.txt"]

    def stat(self, path: Path) -> FileStat:
        self.calls.append(f"stat:{path}")
        size = len(self._files.get(str(path), b""))
        return FileStat(size=size, mtime=0.0, is_dir=False, is_file=True)

    def exists(self, path: Path) -> bool:
        s = str(path)
        if s in self._files or s.endswith("/dir"):
            return True
        # Treat any prefix path of a known file as an existing directory.
        return any(f.startswith(s + "/") or f == s for f in self._files)

    def is_dir(self, path: Path) -> bool:
        s = str(path)
        if s.endswith("/dir"):
            return True
        if s in self._files:
            return False
        return any(f.startswith(s + "/") for f in self._files)

    def is_file(self, path: Path) -> bool:
        return str(path) in self._files


def test_read_file_routes_through_fake_fs(tmp_path):
    fs = FakeFS({str(tmp_path / "hi.txt"): "hello\nworld\n"})
    tool = ReadFileTool()
    tool.filesystem = fs
    tool.working_directory = tmp_path

    out = tool.execute(file_path="hi.txt")

    assert "hello" in out
    # ReadFileTool sniffs for binary then streams the text; both routes
    # must land on the injected FS.
    assert any(c.startswith("read_partial:") for c in fs.calls), fs.calls
    assert any(c.startswith("open_text:") for c in fs.calls), fs.calls


def test_write_and_edit_route_through_fake_fs(tmp_path):
    fs = FakeFS()
    write_tool = WriteFileTool()
    write_tool.filesystem = fs
    write_tool.working_directory = tmp_path
    write_tool.execute(file_path="out.txt", content="abc\n")
    assert any("write_text" in c for c in fs.calls), fs.calls

    fs2 = FakeFS({str(tmp_path / "x.txt"): "hello world\n"})
    edit = EditTool()
    edit.filesystem = fs2
    edit.working_directory = tmp_path
    edit.execute(file_path="x.txt", old_text="hello", new_text="goodbye")
    assert any("read_bytes" in c for c in fs2.calls)
    assert any("write_text" in c for c in fs2.calls)


def test_glob_and_search_route_through_fake_fs(tmp_path):
    fs = FakeFS({str(tmp_path / "a.txt"): "needle in haystack\n"})
    glob_tool = FindFilesTool()
    glob_tool.filesystem = fs
    glob_tool.working_directory = tmp_path
    glob_tool.execute(pattern="*.txt")
    assert any(c.startswith("glob:") for c in fs.calls)

    fs2 = FakeFS({str(tmp_path / "a.txt"): "needle in haystack\n"})
    search_tool = SearchTextTool()
    search_tool.filesystem = fs2
    search_tool.working_directory = tmp_path
    # Force the python-fallback path: pretend we're not in a git repo so
    # the tool doesn't shell out to git grep (which would bypass the FS).
    search_tool._is_git_repo = lambda d: False  # type: ignore[assignment]
    out = search_tool.execute(pattern="needle", directory=str(tmp_path))
    assert "needle" in out


def test_list_directory_routes_through_fake_fs(tmp_path):
    fs = FakeFS()
    tool = ReadFolderTool()
    tool.filesystem = fs
    tool.working_directory = tmp_path
    # Use a directory that FakeFS reports as existing:
    out = tool.execute(directory_path=str(tmp_path) + "/dir")
    assert "a.txt" in out
    assert "sub" in out
    assert any(c.startswith("list_dir:") for c in fs.calls)
