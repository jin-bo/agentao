"""FileSystem capability protocol and local default.

Defines a narrow IO contract that file/search tools route through. The
default :class:`LocalFileSystem` keeps byte-equivalent behavior with
the pre-capability code (``Path.glob``, ``os.scandir`` ordering,
``open(...)``); embedded hosts can replace it with an in-memory or
remote-backed implementation without monkey-patching.

Only operations that file/search tools actually use are exposed.
The shell tool keeps its own ``ShellExecutor`` so the two surfaces
do not get tangled.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Protocol, runtime_checkable


@dataclass(frozen=True)
class FileStat:
    """Subset of ``os.stat_result`` exposed to tools."""

    size: int
    mtime: float
    is_dir: bool
    is_file: bool


@dataclass(frozen=True)
class FileEntry:
    """Single directory entry returned by :meth:`FileSystem.list_dir`."""

    name: str
    is_dir: bool
    is_file: bool
    size: int


@runtime_checkable
class FileSystem(Protocol):
    """IO contract used by file and search tools.

    Implementations must accept absolute paths only — relative path
    resolution is the tool's responsibility (see ``Tool._resolve_path``).
    Methods raise the underlying ``OSError`` family on failure; tools
    catch and turn them into user-facing error strings.
    """

    def read_bytes(self, path: Path) -> bytes:
        ...

    def read_partial(self, path: Path, n: int) -> bytes:
        """Read up to ``n`` bytes from the start of ``path``.

        Used for binary detection without materializing huge files.
        Implementations should never read more than ``n`` bytes.
        """
        ...

    def open_text(self, path: Path) -> Iterator[str]:
        """Iterate ``path`` line by line as text.

        Streaming primitive: tools that scan large files (search,
        grep) should not materialize the whole content. Implementations
        must yield lines including their trailing newline so callers
        can preserve byte offsets if needed.
        """
        ...

    def write_text(self, path: Path, data: str, *, append: bool = False) -> None:
        ...

    def list_dir(self, path: Path) -> List[FileEntry]:
        ...

    def glob(self, base: Path, pattern: str, *, recursive: bool) -> List[Path]:
        ...

    def stat(self, path: Path) -> FileStat:
        ...

    def exists(self, path: Path) -> bool:
        ...

    def is_dir(self, path: Path) -> bool:
        ...

    def is_file(self, path: Path) -> bool:
        ...


class LocalFileSystem:
    """Default :class:`FileSystem` backed by ``pathlib`` / ``os``.

    Behavior matches the pre-capability tool code so that the refactor
    is byte-equivalent: ``Path.glob`` symlink semantics, ``os.scandir``
    ordering, and the same set of stat fields. Tools that need
    ordering or symlink follow-through behavior must rely on those same
    semantics.
    """

    def read_bytes(self, path: Path) -> bytes:
        with open(path, "rb") as f:
            return f.read()

    def read_partial(self, path: Path, n: int) -> bytes:
        with open(path, "rb") as f:
            return f.read(n)

    def open_text(self, path: Path) -> Iterator[str]:
        with open(path, "r", encoding="utf-8") as f:
            yield from f

    def write_text(self, path: Path, data: str, *, append: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with open(path, mode, encoding="utf-8") as f:
            f.write(data)

    def list_dir(self, path: Path) -> List[FileEntry]:
        entries: List[FileEntry] = []
        with os.scandir(path) as it:
            for de in it:
                try:
                    is_dir = de.is_dir()
                    is_file = de.is_file()
                    size = de.stat().st_size if is_file else 0
                except OSError:
                    is_dir = False
                    is_file = False
                    size = 0
                entries.append(
                    FileEntry(
                        name=de.name,
                        is_dir=is_dir,
                        is_file=is_file,
                        size=size,
                    )
                )
        return entries

    def glob(self, base: Path, pattern: str, *, recursive: bool) -> List[Path]:
        if recursive:
            return list(base.rglob(pattern))
        return list(base.glob(pattern))

    def stat(self, path: Path) -> FileStat:
        st = path.stat()
        return FileStat(
            size=st.st_size,
            mtime=st.st_mtime,
            is_dir=path.is_dir(),
            is_file=path.is_file(),
        )

    def exists(self, path: Path) -> bool:
        return path.exists()

    def is_dir(self, path: Path) -> bool:
        return path.is_dir()

    def is_file(self, path: Path) -> bool:
        return path.is_file()
