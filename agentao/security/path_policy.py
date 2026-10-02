"""Workspace containment for tool-supplied paths.

PathPolicy is a narrow security primitive: given a project root, decide
whether a path the LLM (or a tool) wants to write to lands inside that root.
It exists to close the gap where ``Tool._resolve_path`` accepts absolute
paths unchanged and never resolves symlinks, allowing escapes like
``write_file('/etc/passwd', ...)`` or ``write_file('../outside.txt', ...)``.

Scope is deliberately small:

* No capability vocabulary, no permission engine integration.
* Read-only tools are not gated here.
* Shell command **arguments** are not inspected — only the cwd is contained.
  Once the user confirms ``bash -c 'echo x > /tmp/a'`` we cannot block
  command-internal absolute paths without OS-level sandboxing.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from ..tools.base import Tool


class PathPolicyError(ValueError):
    """Raised when a tool-supplied path escapes the project root."""


@dataclass(frozen=True)
class PathPolicy:
    """Containment check rooted at an absolute, resolved project directory."""

    project_root: Path

    @classmethod
    def for_tool(cls, tool: "Tool") -> "PathPolicy":
        """Build a policy from a tool's bound working directory.

        If the tool has no bound cwd (legacy CLI without ACP), snapshot the
        current process cwd. Snapshot is per-call so callers that ``chdir``
        between invocations get the cwd they expect.
        """
        wd = getattr(tool, "working_directory", None)
        if wd is not None:
            cached = getattr(tool, "_path_policy_cache", None)
            if cached is not None and cached[0] == wd:
                return cached[1]
            policy = cls(project_root=Path(wd).expanduser().resolve())
            try:
                tool._path_policy_cache = (wd, policy)
            except AttributeError:
                pass
            return policy
        return cls(project_root=Path.cwd().expanduser().resolve())

    # ------------------------------------------------------------------
    # Containment checks
    # ------------------------------------------------------------------

    def contain_file(self, raw: str) -> Path:
        """Validate that ``raw`` resolves to a path inside ``project_root``.

        Returns the resolved absolute path. Raises :class:`PathPolicyError`
        if the path escapes — by ``..`` traversal, by being absolute and
        outside the root, or by a symlink (in either the parent chain or
        the target itself) pointing outside.

        Works for files that do not yet exist: the parent directory is
        resolved (which fully dereferences any symlinks in the chain) and
        the target name is appended back on. If the target itself exists
        and is a symlink, the dereferenced destination is also checked.
        """
        candidate = Path(raw).expanduser()
        resolved = self._resolve_for_write(candidate)
        self._assert_inside(resolved, raw)

        # If the target itself is a symlink, also verify its destination
        # is inside the root. ``resolved`` above only dereferences parent
        # links; here we follow the leaf link.
        if resolved.is_symlink():
            dereferenced = resolved.resolve(strict=False)
            self._assert_inside(dereferenced, raw)

        return resolved

    def contain_directory(self, raw: str) -> Path:
        """Validate that ``raw`` (an existing directory) is inside the root.

        Returns the resolved absolute path. Symlinks are followed once via
        ``Path.resolve()``. Raises :class:`PathPolicyError` on escape.

        Caller is responsible for the ``is_dir()`` / existence check —
        this method only enforces containment.
        """
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.project_root / candidate
        resolved = candidate.resolve(strict=False)
        self._assert_inside(resolved, raw)
        return resolved

    @classmethod
    def contain_any(
        cls,
        raw: str | Path,
        *,
        writable: Iterable[str | Path],
        immutable: Iterable[str | Path] = (),
    ) -> Path:
        """Validate a write against several roots, with read-only carve-outs.

        For a host's ``FileSystem`` wrapper that needs more than one
        writable root, or read-only subpaths inside a writable one (see
        ``docs/design/host-fs-policy.md``).

        **Provisional public API.** Hosts may call it, but it is not part
        of the ``agentao.host`` stability contract yet and its signature
        may change before it is. It is promoted once the design's
        gate-pushdown step settles whether hosts keep calling it directly,
        or once a second host uses it unchanged for a release cycle. Returns the effective target
        — where ``open()`` would actually write — and raises
        :class:`PathPolicyError` unless it is under some ``writable``
        root and under no ``immutable`` one. Immutable wins.

        ``raw`` must be absolute, as the ``FileSystem`` protocol requires:
        there is no root to resolve a relative path against, and resolving
        it against the process cwd would test a different path from the
        one the tool writes. An empty ``writable`` refuses everything.

        Do not compose this from :meth:`contain_file` per root. That
        checks the parent-resolved path *before* following a leaf
        symlink, so ``cwd/scratch/link -> cwd/raw/secret`` is refused by
        the ``raw`` policy for the wrong reason — as "outside raw" — and a
        wrapper reading that refusal as "not immutable" lets the write
        through the link. Here the leaf is dereferenced first and every
        root is tested against that one target — by path, and for roots
        that exist, also by identity (``st_dev``/``st_ino``) against the
        target's existing ancestors, because on a case-insensitive volume
        ``kb/RAW/x`` is ``kb/raw/x`` on disk but not lexically under it.
        Read-only roots are additionally compared case- and
        normalisation-insensitively, so one that does not exist yet
        cannot be created through a case variant; on a case-sensitive
        volume that over-refuses, never under-refuses. Writable roots get
        no such widening — there it would fail open. A path that cannot
        be resolved (a symlink loop) is refused.
        """
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            raise PathPolicyError(
                f"PathPolicy: refused '{raw}' — not an absolute path"
            )
        try:
            if candidate.name == "..":
                # ``parent / name`` would keep the ``..`` literally, and a
                # lexical ``is_relative_to`` then reads ``root/sub/..`` as
                # inside ``root/sub``.
                target = candidate.resolve(strict=False)
            else:
                target = candidate.parent.resolve(strict=False) / candidate.name
                if target.is_symlink():
                    target = target.resolve(strict=False)

            def _roots(paths: Iterable[str | Path]) -> list[Path]:
                return [Path(p).expanduser().resolve(strict=False) for p in paths]

            writable_roots = _roots(writable)
            immutable_roots = _roots(immutable)
        except (RuntimeError, OSError) as e:
            # A symlink loop raises RuntimeError on 3.12 and OSError on
            # 3.13+. Refuse with *this* type, so a wrapper catching only
            # PathPolicyError still reads it as a refusal.
            raise PathPolicyError(
                f"PathPolicy: refused '{raw}' — cannot resolve: {e}"
            ) from e

        # ``resolve`` does not canonicalise case on macOS, so on a
        # case-insensitive volume ``kb/RAW/x`` is ``kb/raw/x`` on disk but
        # not lexically under ``kb/raw``. Compare existing ancestors by
        # identity too; a lexical match alone would let that write through.
        ancestor_ids = set()
        for p in (target, *target.parents):
            try:
                st = p.stat()
            except OSError:
                continue
            ancestor_ids.add((st.st_dev, st.st_ino))

        def _under(root: Path) -> bool:
            if target.is_relative_to(root):
                return True
            try:
                st = root.stat()
            except OSError:
                return False
            return (st.st_dev, st.st_ino) in ancestor_ids

        if not any(_under(r) for r in writable_roots):
            raise PathPolicyError(
                f"PathPolicy: refused '{raw}' — resolves to '{target}', "
                f"outside every writable root"
            )
        def _fold(path: Path) -> tuple[str, ...]:
            return tuple(
                unicodedata.normalize("NFC", part).casefold() for part in path.parts
            )

        def _under_folded(root: Path) -> bool:
            # Identity only covers a root that exists. A read-only root
            # that does not exist yet (``raw/`` before the first import,
            # ``AGENTAO.md`` before it is written) has no inode, and a
            # case variant would create it. Folded comparison closes that
            # on case- and normalisation-insensitive volumes; on a
            # case-sensitive one it over-refuses ``RAW/x`` beside a
            # read-only ``raw/``, which is the safe direction.
            t, r = _fold(target), _fold(root)
            return t[: len(r)] == r

        for root in immutable_roots:
            if _under(root) or _under_folded(root):
                raise PathPolicyError(
                    f"PathPolicy: refused '{raw}' — resolves to '{target}', "
                    f"inside read-only '{root}'"
                )
        return target

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_for_write(self, candidate: Path) -> Path:
        """Resolve a write target without requiring it to exist.

        Joins relative paths to ``project_root``, resolves the parent (so
        symlinks in the chain are followed), then re-attaches the leaf
        name. The leaf is intentionally not dereferenced here so that
        ``contain_file`` can decide separately whether to follow a leaf
        symlink — relevant for distinguishing a fresh write versus an
        overwrite-via-symlink-escape.
        """
        if not candidate.is_absolute():
            candidate = self.project_root / candidate
        parent = candidate.parent.resolve(strict=False)
        return parent / candidate.name

    def _assert_inside(self, resolved: Path, raw: str) -> None:
        if not resolved.is_relative_to(self.project_root):
            raise PathPolicyError(
                f"PathPolicy: refused '{raw}' — resolves to '{resolved}', "
                f"outside project_root '{self.project_root}'"
            )
