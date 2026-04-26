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

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

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
