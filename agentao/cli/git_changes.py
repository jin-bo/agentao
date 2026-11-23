"""Collect a Git working tree's changes without changing it (#662).

``/diff`` shows the result. ``/review`` (#665) is meant to reuse it, so this
module has no Rich or console imports and returns plain data.

Read-only means two things here:

- Nothing is written. ``--no-optional-locks`` stops git from refreshing the
  index's stat cache, which ``git status`` and friends otherwise do.
- Nothing the repository configures is run, as far as a flag can stop it.
  ``core.fsmonitor`` (run when the index is read), an external diff driver
  (``diff.external`` / ``diff.<driver>.command``) and ``textconv`` are all
  named in the repo's own ``.git/config`` or ``.gitattributes``, so
  ``-c core.fsmonitor=false``, ``--no-ext-diff`` and ``--no-textconv`` turn
  them off. A ``filter.<name>.clean`` driver can still run when git
  re-hashes a modified file: it cannot be turned off without knowing its
  name. ``permissions.py`` documents the same residual for ``git diff``.

Output is bounded twice: git's output is capped at ``MAX_COLLECT_BYTES``
per command (past it the section is reported as too large, not shown in
part), and each diff keeps at most ``MAX_DIFF_LINES`` lines, with the rest
counted. Untracked files are listed by name only, so a large or binary new
file costs one line. Git itself prints ``Binary files ... differ`` for a
changed binary file.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from ..capabilities.process import OutputLimitExceeded, build_child_env, run_captured

MAX_COLLECT_BYTES = 4 * 1024 * 1024
MAX_DIFF_LINES = 500
MAX_UNTRACKED = 200
GIT_TIMEOUT_S = 30.0

_GIT = ("git", "--no-pager", "--no-optional-locks", "-c", "core.fsmonitor=false")
_DIFF_FLAGS = ("--no-ext-diff", "--no-textconv", "--no-color")


class GitChangesError(Exception):
    """Git could not report the changes (not installed, timed out, refused)."""


@dataclass
class DiffSection:
    """One diff (staged or unstaged), possibly cut short."""

    text: str = ""
    omitted_lines: int = 0
    too_large: bool = False

    @property
    def empty(self) -> bool:
        return not self.text and not self.too_large


@dataclass
class GitChanges:
    """What ``git diff --cached``, ``git diff`` and the untracked list show."""

    root: Path
    staged: DiffSection = field(default_factory=DiffSection)
    unstaged: DiffSection = field(default_factory=DiffSection)
    untracked: List[str] = field(default_factory=list)
    omitted_untracked: int = 0
    untracked_too_large: bool = False

    @property
    def clean(self) -> bool:
        return (
            self.staged.empty and self.unstaged.empty
            and not self.untracked and not self.untracked_too_large
        )


def _git(args: tuple, cwd: Path, env: Optional[dict] = None) -> "subprocess.CompletedProcess[str]":
    if not cwd.is_dir():
        # ``Popen(cwd=missing)`` raises ``FileNotFoundError`` too, which
        # would read as "git is not installed".
        raise GitChangesError(f"working directory does not exist: {cwd}")
    try:
        return run_captured(
            [*_GIT, *args], cwd=str(cwd), timeout=GIT_TIMEOUT_S,
            max_output_bytes=MAX_COLLECT_BYTES, env=env,
        )
    except FileNotFoundError as exc:
        raise GitChangesError("git is not installed or not on PATH") from exc
    except OSError as exc:
        raise GitChangesError(f"could not run git: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitChangesError(f"git did not answer within {GIT_TIMEOUT_S:.0f}s") from exc


def _checked(proc: "subprocess.CompletedProcess[str]", what: str) -> str:
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        raise GitChangesError(f"{what} failed: {detail[0] if detail else f'exit {proc.returncode}'}")
    return proc.stdout


def _diff(args: tuple, root: Path, what: str) -> DiffSection:
    try:
        text = _checked(_git(("diff", *_DIFF_FLAGS, *args), root), what)
    except OutputLimitExceeded:
        return DiffSection(too_large=True)
    lines = text.splitlines()
    if len(lines) <= MAX_DIFF_LINES:
        return DiffSection(text=text.rstrip("\n"))
    return DiffSection(
        text="\n".join(lines[:MAX_DIFF_LINES]),
        omitted_lines=len(lines) - MAX_DIFF_LINES,
    )


def find_repo_root(cwd: Path) -> Optional[Path]:
    """The work tree's top directory, or ``None`` outside a Git work tree.

    Only git's own "not a git repository" answer means ``None``. Any other
    failure (``safe.directory`` refusing a repo another user owns, running
    inside ``.git``) raises :class:`GitChangesError` with git's message,
    rather than claiming there is no repository. ``LC_ALL=C`` keeps that
    message untranslated so the check can read it.
    """
    proc = _git(
        ("rev-parse", "--show-toplevel"), cwd,
        env=build_child_env({"LC_ALL": "C", "LANGUAGE": "C"}),
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return Path(proc.stdout.strip())
    if "not a git repository" in (proc.stderr or "").lower():
        return None
    _checked(proc, "git rev-parse")
    return None


def collect_changes(cwd: Path) -> Optional[GitChanges]:
    """Staged, unstaged and untracked changes of the repo holding ``cwd``.

    Returns ``None`` when ``cwd`` is not inside a Git work tree. Raises
    :class:`GitChangesError` when git is missing, times out or fails.
    Runs from the repo root, so the untracked list covers the whole repo
    and not only ``cwd``'s subdirectory.
    """
    root = find_repo_root(cwd)
    if root is None:
        return None
    changes = GitChanges(root=root)
    # ``--cached`` with no HEAD (a repo with no commits yet) compares
    # against the empty tree, so it works before the first commit.
    changes.staged = _diff(("--cached",), root, "git diff --cached")
    changes.unstaged = _diff((), root, "git diff")
    try:
        out = _checked(
            _git(("ls-files", "--others", "--exclude-standard", "-z"), root),
            "git ls-files",
        )
    except OutputLimitExceeded:
        changes.untracked_too_large = True
        return changes
    names = [n for n in out.split("\0") if n]
    changes.untracked = names[:MAX_UNTRACKED]
    changes.omitted_untracked = max(0, len(names) - MAX_UNTRACKED)
    return changes
