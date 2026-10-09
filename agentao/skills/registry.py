"""Skill package registry for tracking managed skill installations."""

import dataclasses
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from ..paths import user_home, user_root

# `filelock` is deferred (P0.5): the registry is only touched when an
# embedded host (or the CLI) installs / updates skills. Plain ``Agentao()``
# construction never reaches the lock path, so the wheel cost stays out of
# the import-time budget.
if TYPE_CHECKING:
    from filelock import FileLock as _FileLock_t

logger = logging.getLogger(__name__)

_LOCK_TIMEOUT_S = 10

#: What reading the file can raise. ``ValueError`` covers ``JSONDecodeError``
#: and ``UnicodeDecodeError``; ``RecursionError`` is deeply nested JSON.
_UNREADABLE = (OSError, ValueError, RecursionError)


class SkillRegistryWriteError(Exception):
    """``save()`` did not write the registry; the file is unchanged."""


class _UnreadableRegistry(Exception):
    """The file exists but is not a registry this version can read."""


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


_FIELDS = tuple(f.name for f in dataclasses.fields(InstalledSkillRecord))
#: A record without one of these cannot be updated or removed safely.
_REQUIRED_FIELDS = ("source_type", "source_ref", "install_scope", "install_dir")


def _record_from_entry(key: str, entry: Any) -> Union[InstalledSkillRecord, str]:
    """Build a record from one ``skills`` entry, or say why it is unusable.

    Unknown fields are ignored here; ``save()`` keeps them in the file.
    Missing ``version`` / ``revision`` / ``etag`` / ``installed_at`` read as
    ``""``; a missing required field, a non-string value, a ``name`` that
    differs from the key, or an ``install_dir`` that is not an absolute path
    (``remove`` deletes it — ``""`` would be the current directory) make the
    entry unusable.
    """
    if not isinstance(entry, dict):
        return f"expected an object, got {type(entry).__name__}"
    missing = [f for f in _REQUIRED_FIELDS if f not in entry]
    if missing:
        return f"missing {', '.join(missing)}"
    values = {"name": key}
    for field in _FIELDS:
        if field not in entry:
            continue
        value = entry[field]
        if not isinstance(value, str):
            return f"{field} must be a string, got {type(value).__name__}"
        values[field] = value
    if values["name"] != key:
        return f"name {values['name']!r} does not match its key"
    if not values["install_dir"] or not Path(values["install_dir"]).is_absolute():
        return f"install_dir {values['install_dir']!r} is not an absolute path"
    for field in _FIELDS:
        values.setdefault(field, "")
    return InstalledSkillRecord(**values)


class SkillRegistry:
    """CRUD interface for skills_registry.json.

    Each scope (global / project) has its own registry file. Callers that
    need both scopes instantiate two ``SkillRegistry`` objects.

    Loading is lenient: an unusable entry is skipped with a warning, and a
    file that cannot be read as a whole loads as empty. Saving is strict:
    it re-reads the file under the lock and changes only the names this
    instance added or removed, so entries it skipped, fields it does not
    know, and names another process saved since the load all survive. A
    file it cannot read as a whole is never overwritten —
    :class:`SkillRegistryWriteError` instead, and the pending changes stay
    so ``save()`` can be retried.

    Two processes saving the same name: the last save wins. The lock covers
    the registry file only, not the skill directories.
    """

    def __init__(self, registry_path: Path) -> None:
        self._path = Path(registry_path)
        self._lock_path = self._path.with_suffix(".lock")
        self._skills: Dict[str, InstalledSkillRecord] = {}
        # name -> record to write, or None to delete; applied by save()
        self._pending: Dict[str, Optional[InstalledSkillRecord]] = {}
        self.load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> Dict[str, InstalledSkillRecord]:
        """Load registry from disk, discarding unsaved changes.

        Returns the loaded dict. Never writes the file.
        """
        self._skills.clear()
        self._pending.clear()
        if not self._path.exists():
            return {}
        try:
            skills = self._read_skills()
        except _UnreadableRegistry as exc:
            logger.warning(
                "%s; loading it as empty. Installs and removals will not "
                "overwrite it until it is fixed or removed.", exc,
            )
            return {}
        self._adopt(skills, warn=True)
        return dict(self._skills)

    def save(self) -> None:
        """Merge this instance's changes into the file, under the lock.

        Raises :class:`SkillRegistryWriteError` if nothing was written.
        """
        with self._locked():
            data, skills = self._read_for_write()
            for name, record in self._pending.items():
                if record is None:
                    skills.pop(name, None)
                    continue
                old = skills.get(name)
                extra = (
                    {k: v for k, v in old.items() if k not in _FIELDS}
                    if isinstance(old, dict) else {}
                )
                skills[name] = {**dataclasses.asdict(record), **extra}
            self._write_atomically(data)
        self._pending.clear()
        self._adopt(skills, warn=False)

    def ensure_writable(self) -> None:
        """Raise :class:`SkillRegistryWriteError` if ``save()`` would refuse now.

        For a caller about to change a skill directory: checking first
        keeps a registry that cannot be written from leaving that change
        unrecorded. A lock timeout or a failed write can still make the
        later ``save()`` fail.
        """
        with self._locked():
            self._read_for_write()

    def _parse(self, raw: bytes):
        """Return ``(data, skills)`` from the file's bytes; ``skills`` is
        ``data["skills"]`` (created if absent), so editing it edits *data*."""
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except _UNREADABLE as exc:
            raise _UnreadableRegistry(
                f"skill registry {self._path} is unreadable "
                f"({type(exc).__name__}: {exc})"
            ) from exc
        if not isinstance(data, dict):
            raise _UnreadableRegistry(
                f"skill registry {self._path} is unreadable "
                f"(top level is {type(data).__name__}, not an object)"
            )
        skills = data.setdefault("skills", {})
        if not isinstance(skills, dict):
            raise _UnreadableRegistry(
                f"skill registry {self._path} is unreadable "
                f"('skills' is {type(skills).__name__}, not an object)"
            )
        return data, skills

    def _read_skills(self) -> Dict[str, Any]:
        try:
            raw = self._path.read_bytes()
        except OSError as exc:
            raise _UnreadableRegistry(
                f"skill registry {self._path} is unreadable "
                f"({type(exc).__name__}: {exc})"
            ) from exc
        return self._parse(raw)[1]

    def _read_for_write(self):
        """Return ``(data, skills)`` to merge into; a missing file is empty."""
        try:
            return self._parse(self._path.read_bytes())
        except FileNotFoundError:
            data: Dict[str, Any] = {"skills": {}}
            return data, data["skills"]
        except (OSError, _UnreadableRegistry) as exc:
            reason = (
                exc if isinstance(exc, _UnreadableRegistry)
                else f"skill registry {self._path} is unreadable "
                     f"({type(exc).__name__}: {exc})"
            )
            raise SkillRegistryWriteError(
                f"{reason}; not updating it. Fix the file or move it aside "
                f"— writing it now would lose the records it holds"
            ) from exc

    def _adopt(self, skills: Dict[str, Any], *, warn: bool) -> None:
        """Replace the in-memory view with the usable entries of *skills*."""
        self._skills.clear()
        for name, entry in skills.items():
            record = _record_from_entry(name, entry)
            if isinstance(record, str):
                if warn:
                    logger.warning(
                        "Skipping entry %r in skill registry %s: %s. It is "
                        "kept in the file but not managed until fixed.",
                        name, self._path, record,
                    )
                continue
            self._skills[name] = record

    @contextmanager
    def _locked(self):
        from filelock import FileLock, Timeout

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SkillRegistryWriteError(
                f"could not create {self._path.parent}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        lock = FileLock(str(self._lock_path), timeout=_LOCK_TIMEOUT_S)
        try:
            lock.acquire()
        except Timeout as exc:
            raise SkillRegistryWriteError(
                f"timed out after {_LOCK_TIMEOUT_S}s waiting for "
                f"{self._lock_path}; another agentao process is updating "
                f"the skill registry, try again"
            ) from exc
        except OSError as exc:
            raise SkillRegistryWriteError(
                f"could not lock {self._lock_path}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        try:
            yield
        finally:
            lock.release()

    def _write_atomically(self, data: Dict[str, Any]) -> None:
        """Swap *data* in via a temp file: a reader never sees a torn file."""
        from agentao.capabilities.filesystem import _replace_with_retry

        try:
            fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        except OSError as exc:
            raise SkillRegistryWriteError(
                f"could not write {self._path}: {type(exc).__name__}: {exc}"
            ) from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            # mkstemp creates 0o600; keep the file's own mode (or the
            # 0o644 a plain write gave it) so a shared registry stays
            # readable by whoever could read it before.
            try:
                mode = os.stat(self._path).st_mode & 0o7777
            except FileNotFoundError:
                mode = 0o644
            os.chmod(tmp, mode)
            _replace_with_retry(Path(tmp), self._path)
        except BaseException as exc:
            # BaseException: an interrupt must not leave a temp file behind.
            try:
                os.unlink(tmp)
            except OSError:
                pass
            # ValueError: a lone surrogate kept from the file (json.loads
            # accepts "\ud800") cannot be encoded back as UTF-8.
            if not isinstance(exc, (OSError, ValueError)):
                raise
            raise SkillRegistryWriteError(
                f"could not write {self._path}: {type(exc).__name__}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[InstalledSkillRecord]:
        return self._skills.get(name)

    def add(self, record: InstalledSkillRecord) -> None:
        self._skills[record.name] = record
        self._pending[record.name] = record

    def remove(self, name: str) -> bool:
        """Remove a record. Returns True if it existed."""
        existed = self._skills.pop(name, None) is not None
        if existed:
            self._pending[name] = None
        return existed

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
    home = user_home().resolve()
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
        return user_root() / "skills_registry.json"
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
        return user_root() / "skills" / skill_name
    root = _find_project_root(cwd)
    if root is None:
        root = cwd or Path.cwd()
    return root / ".agentao" / "skills" / skill_name
