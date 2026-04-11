"""Skill package registry for tracking managed skill installations."""

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from filelock import FileLock


@dataclasses.dataclass
class InstalledSkillRecord:
    """Metadata for a single managed skill installation."""

    name: str
    source_type: str       # "github"
    source_ref: str        # "owner/repo"
    installed_at: str      # ISO 8601
    install_scope: str     # "global" | "project"
    install_dir: str       # absolute path
    version: str           # from skill.json or ""
    revision: str          # archive digest or commit sha
    etag: str              # HTTP ETag for update checks


class SkillRegistry:
    """CRUD interface for skills_registry.json.

    Each scope (global / project) has its own registry file. Callers that
    need both scopes instantiate two ``SkillRegistry`` objects.
    """

    def __init__(self, registry_path: Path) -> None:
        self._path = Path(registry_path)
        self._lock_path = self._path.with_suffix(".lock")
        self._skills: Dict[str, InstalledSkillRecord] = {}
        self.load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> Dict[str, InstalledSkillRecord]:
        """Load registry from disk. Returns the loaded dict."""
        self._skills.clear()
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                for name, entry in data.get("skills", {}).items():
                    self._skills[name] = InstalledSkillRecord(**entry)
            except (json.JSONDecodeError, TypeError, KeyError):
                # Corrupted file — start fresh but don't overwrite yet
                self._skills.clear()
        return dict(self._skills)

    def save(self) -> None:
        """Persist registry to disk with file locking."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "skills": {
                name: dataclasses.asdict(rec)
                for name, rec in self._skills.items()
            }
        }
        lock = FileLock(self._lock_path, timeout=10)
        with lock:
            self._path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[InstalledSkillRecord]:
        return self._skills.get(name)

    def add(self, record: InstalledSkillRecord) -> None:
        self._skills[record.name] = record

    def remove(self, name: str) -> bool:
        """Remove a record. Returns True if it existed."""
        return self._skills.pop(name, None) is not None

    def list_all(self) -> List[InstalledSkillRecord]:
        return list(self._skills.values())

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: str) -> bool:
        return name in self._skills


# ------------------------------------------------------------------
# Scope helpers
# ------------------------------------------------------------------

_PROJECT_MARKERS = (".git", "pyproject.toml", "package.json", ".agentao")


def _find_project_root(start: Optional[Path] = None) -> Optional[Path]:
    """Walk up from *start* to find the nearest directory containing a project marker.

    At the user's home directory, only ``pyproject.toml`` and ``package.json``
    are considered valid markers.  ``.agentao`` and ``.git`` are ignored at
    ``$HOME`` because the global ``~/.agentao`` config dir and a bare
    ``~/.git`` are not reliable indicators of a project root.  Repositories
    genuinely rooted at ``~`` can be detected via the manifest files.

    Returns ``None`` if no marker is found before reaching the filesystem root.
    """
    home = Path.home().resolve()
    current = (start or Path.cwd()).resolve()
    # Markers that are ambiguous at $HOME (config dirs / bare repos).
    _HOME_SKIP = {".agentao", ".git"}
    while True:
        markers = (
            (m for m in _PROJECT_MARKERS if m not in _HOME_SKIP)
            if current == home
            else _PROJECT_MARKERS
        )
        for marker in markers:
            if (current / marker).exists():
                return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def resolve_default_scope(cwd: Optional[Path] = None) -> str:
    """Return ``'project'`` if a project root is found at or above *cwd*, else ``'global'``."""
    return "project" if _find_project_root(cwd) is not None else "global"


def registry_path_for_scope(scope: str, cwd: Optional[Path] = None) -> Path:
    """Return the ``skills_registry.json`` path for *scope*.

    For project scope, resolves upward to the project root so the
    registry is stable regardless of which subdirectory the command
    is run from.
    """
    if scope == "global":
        return Path.home() / ".agentao" / "skills_registry.json"
    root = _find_project_root(cwd)
    if root is None:
        root = cwd or Path.cwd()
    return root / ".agentao" / "skills_registry.json"


def install_dir_for_scope(
    scope: str, skill_name: str, cwd: Optional[Path] = None
) -> Path:
    """Return the target directory for installing a skill.

    For project scope, resolves upward to the project root.
    """
    if scope == "global":
        return Path.home() / ".agentao" / "skills" / skill_name
    root = _find_project_root(cwd)
    if root is None:
        root = cwd or Path.cwd()
    return root / ".agentao" / "skills" / skill_name
