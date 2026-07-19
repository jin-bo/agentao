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

import errno
import os
import tempfile
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
        """Write ``data`` to ``path``; ``append`` adds instead of replacing.

        Implementations replacing existing content should do so
        atomically — a caller interrupted mid-write must not be able to
        leave the user's file truncated. See
        :meth:`LocalFileSystem.write_text` for the reference approach.
        """
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
    semantics. The one deliberate departure is :meth:`write_text`, which
    replaces existing files atomically rather than truncating in place —
    see its docstring.
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
        """Write ``data`` to ``path``, replacing existing content atomically.

        A plain ``open(path, "w")`` truncates *before* the write, so an
        interruption in between (Ctrl+C, host OOM, ``kill``) leaves the
        user's file empty or half-written. Agentao runs inside a host
        process it does not control, so that window has to be closed:
        for a file that already exists we write a sibling temp file and
        ``os.replace`` it into place, which is atomic at the VFS level —
        a reader sees either the old content or the new one, never a
        torn file.

        Two cases deliberately keep the direct-write path, because
        neither can destroy existing content:

        * ``append=True`` — rewriting the whole file to append is both
          wasteful and wrong for a growing log.
        * the target does not exist yet — an interrupted create leaves a
          partial *new* file, not a damaged one, and going direct keeps
          the ``open()`` umask semantics for fresh files exactly as they
          were.

        Scope note: ``os.replace`` closes the *process-death* window,
        which is the failure this guards. It is not fsync'd, so a power
        loss can still lose the write — durability beyond process death
        is the host's concern and would cost an fsync on every write.

        Symlinks are followed (matching the previous behavior): the temp
        file is staged next to the resolved target so the replace stays
        on one filesystem and the symlink itself survives. Hard links to
        the target do not — the other links keep the old content.

        If the target's directory is not writable the staging step is
        impossible, and rather than fail a write that previously
        succeeded we fall back to the in-place path — but *only* for the
        permission-denied family. A staging failure for any other reason
        (ENOSPC, EMFILE) propagates, because falling back there would
        truncate the file this method exists to protect.

        A read-only target still raises ``PermissionError`` even though
        ``os.replace`` could technically overwrite it: rename permission
        lives on the directory, so without an explicit check an atomic
        write would silently defeat ``chmod 444``.
        """
        path.parent.mkdir(parents=True, exist_ok=True)

        if append or not path.exists():
            mode = "a" if append else "w"
            with open(path, mode, encoding="utf-8") as f:
                f.write(data)
            return

        # Resolve through symlinks so the temp file lands in the same
        # directory as the real file (``os.replace`` cannot cross
        # filesystems) and so we overwrite the target, not the link.
        target = Path(os.path.realpath(path))

        # ``os.replace`` only needs write permission on the *directory*, so
        # staging would silently overwrite a file the user deliberately made
        # read-only (``chmod 444 schema.generated.py``) — something the old
        # direct write refused with PermissionError. Preserve that refusal:
        # the read-only bit is the user telling the agent to keep out, and
        # an atomicity improvement must not quietly revoke it.
        if not os.access(target, os.W_OK):
            raise PermissionError(
                errno.EACCES, "Permission denied (file is not writable)", str(target),
            )

        try:
            fd, tmp_name = tempfile.mkstemp(
                dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
            )
        except OSError as exc:
            # ONLY the "cannot create files in this directory" family falls
            # back. A broader ``except OSError`` would catch ENOSPC/EMFILE and
            # then truncate the target with ``open(target, "w")`` — destroying
            # the file this method exists to protect, and doing it precisely
            # when the disk is full and the write is doomed anyway.
            if exc.errno not in (errno.EACCES, errno.EPERM, errno.EROFS):
                raise
            # Staging needs a writable *directory*; the direct path only
            # needed a writable *file*. Rather than regress a case that
            # used to work (writable file in a read-only directory), fall
            # back to the in-place write — atomicity is best-effort, and
            # refusing the write outright would be the bigger harm.
            with open(target, "w", encoding="utf-8") as f:
                f.write(data)
            return
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            # mkstemp creates 0o600; carry over the original permission
            # bits so an executable script stays executable. If the target
            # vanished while we were staging (a concurrent build step, a
            # ``git checkout``), keep the new content rather than losing the
            # write — the direct path would simply have re-created the file.
            try:
                os.chmod(tmp_path, os.stat(target).st_mode & 0o7777)
            except FileNotFoundError:
                pass
            os.replace(tmp_path, target)
        except BaseException:
            # Includes KeyboardInterrupt — the whole point is that an
            # interrupt must not leave debris or a damaged target.
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise

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
