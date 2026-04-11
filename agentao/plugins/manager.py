"""Plugin discovery, precedence resolution, and loading."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .manifest import PluginManifestParser
from .models import (
    LoadedPlugin,
    PluginCandidate,
    PluginLoadError,
    PluginWarning,
)

logger = logging.getLogger(__name__)

SOURCE_RANK = {"global": 0, "project": 1, "inline": 2}


def _version_sort_key(d: Path) -> tuple:
    """Return a sort key for semantic version comparison.

    Parses ``"1.2.3"`` into ``(True, (1, 2, 3))`` so that numeric parts
    compare correctly (e.g. ``10.0.0 > 2.0.0``).  Non-semver names fall
    back to ``(False, name)`` and sort after all valid versions.
    """
    parts = d.name.split(".")
    try:
        return (True, tuple(int(p) for p in parts))
    except ValueError:
        return (False, d.name)


def _find_project_root(start: Path) -> Path:
    """Walk up from *start* to find the nearest *project-level* ``.agentao``.

    This ensures that plugins and ``plugins_config.json`` in the project
    root are discovered even when ``agentao`` is launched from a
    subdirectory.  The user's home directory is excluded so that the
    global ``~/.agentao`` is never mistaken for a project root.

    Falls back to *start* itself if no project-level ``.agentao`` is
    found.
    """
    home = Path.home().resolve()
    current = start.resolve()
    while True:
        if current != home and (current / ".agentao").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return start.resolve()


class PluginManager:
    """Discovers, resolves, and loads plugins from the three-layer hierarchy.

    Layers (lowest to highest precedence):
      1. global   — ``~/.agentao/plugins``
      2. project  — ``<cwd>/.agentao/plugins``
      3. inline   — explicit ``--plugin-dir`` paths
    """

    def __init__(
        self,
        *,
        cwd: Path | None = None,
        inline_dirs: list[Path] | None = None,
    ) -> None:
        self._cwd = _find_project_root(cwd or Path.cwd())
        self._inline_dirs = [d.resolve() for d in (inline_dirs or [])]
        self._parser = PluginManifestParser()

        self._loaded: list[LoadedPlugin] = []
        self._warnings: list[PluginWarning] = []
        self._errors: list[PluginLoadError] = []
        self._precedence_groups: dict[str, list[PluginCandidate]] = {}
        self._loaded_done = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def discover_candidates(self) -> list[PluginCandidate]:
        """Scan all plugin directories and return parsed candidates."""
        candidates: list[PluginCandidate] = []

        # 1. global
        global_dir = Path.home() / ".agentao" / "plugins"
        candidates.extend(self._scan_dir(global_dir, "global"))

        # 2. project
        project_dir = self._cwd / ".agentao" / "plugins"
        candidates.extend(self._scan_dir(project_dir, "project"))

        # 3. inline
        for d in self._inline_dirs:
            # Each inline dir IS a plugin root (not a directory containing plugins).
            candidate = self._try_parse(d, "inline")
            if candidate is not None:
                candidates.append(candidate)

        return candidates

    def filter_disabled(self, candidates: list[PluginCandidate]) -> list[PluginCandidate]:
        """Remove candidates that are disabled via ``plugins_config.json``."""
        disabled = self._load_disabled_set()
        if not disabled:
            return candidates

        result: list[PluginCandidate] = []
        for c in candidates:
            if c.name in disabled or (c.qualified_name and c.qualified_name in disabled):
                logger.info("Plugin '%s' is disabled via plugins_config.json", c.qualified_name or c.name)
            else:
                result.append(c)
        return result

    def resolve_precedence(self, candidates: list[PluginCandidate]) -> list[PluginCandidate]:
        """For each plugin name keep only the highest-precedence candidate.

        Precedence: global(0) < project(1) < inline(2).

        All candidates for each name are retained internally so that
        ``load_plugins`` can fall back to a lower-precedence candidate
        when the winner fails to load.
        """
        # Group by bare name so that inline overrides (--plugin-dir) can
        # replace any same-named installed plugin regardless of marketplace.
        # Within each name group, same-marketplace candidates compete on
        # source_rank; different-marketplace candidates all survive (they
        # have distinct qualified_names and are considered separate plugins).
        by_name: dict[str, list[PluginCandidate]] = {}
        for c in candidates:
            by_name.setdefault(c.name, []).append(c)
        for group in by_name.values():
            group.sort(key=lambda c: c.source_rank, reverse=True)

        self._precedence_groups = by_name

        winners: list[PluginCandidate] = []
        for name, group in by_name.items():
            top = group[0]
            # If the highest-precedence candidate is inline (no marketplace),
            # it overrides all installed copies — single winner.
            if top.marketplace is None:
                if len(group) > 1:
                    self._warnings.append(
                        PluginWarning(
                            plugin_name=top.name,
                            message=(
                                f"Plugin '{top.name}' from {top.source} overrides "
                                f"{group[1].source} ({group[1].root_path})"
                            ),
                        )
                    )
                winners.append(top)
            else:
                # All candidates are installed (marketplace != None).
                # The runtime keys everything by bare plugin name, so we
                # can only keep one winner — picking the highest-priority
                # candidate and warning about the rest.
                if len(group) > 1:
                    for c in group[1:]:
                        self._warnings.append(
                            PluginWarning(
                                plugin_name=c.name,
                                message=(
                                    f"Plugin '{c.name}' from {top.source} overrides "
                                    f"{c.source} ({c.root_path})"
                                ),
                            )
                        )
                winners.append(top)

        return winners

    def load_plugin(self, candidate: PluginCandidate) -> LoadedPlugin:
        """Build a ``LoadedPlugin`` from a resolved candidate."""
        manifest = candidate.manifest
        root = candidate.root_path.resolve()

        # Validate paths
        path_issues = self._parser.validate_paths(manifest, plugin_root=root)
        path_errors = [i for i in path_issues if isinstance(i, PluginLoadError)]
        path_warnings = [i for i in path_issues if isinstance(i, PluginWarning)]

        if path_errors:
            raise PluginPathError(path_errors)

        # Resolve component paths.
        skill_roots = _resolve_paths(root, manifest.skills)
        agent_paths = _resolve_paths(root, manifest.agents)
        command_paths = _resolve_command_paths(root, manifest.commands)
        hook_specs = _collect_hook_specs(manifest.hooks)
        # Auto-discover hooks/hooks.json when manifest doesn't declare hooks.
        if manifest.hooks is None:
            default_hooks = root / "hooks" / "hooks.json"
            if default_hooks.is_file():
                hook_specs = ["./hooks/hooks.json"]
        mcp_warnings: list[PluginWarning] = []
        mcp_servers = _resolve_mcp_servers(root, manifest.mcp_servers, warnings=mcp_warnings)

        all_warnings = candidate.warnings + path_warnings + mcp_warnings

        return LoadedPlugin(
            name=candidate.name,
            version=manifest.version,
            root_path=root,
            source=candidate.source,
            manifest=manifest,
            marketplace=candidate.marketplace,
            qualified_name=candidate.qualified_name,
            skill_roots=skill_roots,
            command_paths=command_paths,
            agent_paths=agent_paths,
            hook_specs=hook_specs,
            mcp_servers=mcp_servers,
            warnings=all_warnings,
        )

    def load_plugins(self) -> list[LoadedPlugin]:
        """Run the full pipeline: discover -> filter -> precedence -> load.

        If the highest-precedence candidate for a name fails to load,
        falls back to the next candidate in the precedence group so a
        broken override doesn't suppress a valid lower-precedence plugin.
        """
        if self._loaded_done:
            return self._loaded

        candidates = self.discover_candidates()
        candidates = self.filter_disabled(candidates)
        candidates = self.resolve_precedence(candidates)

        loaded: list[LoadedPlugin] = []
        for c in candidates:
            plugin = self._try_load_with_fallback(c)
            if plugin is not None:
                loaded.append(plugin)

        self._loaded = loaded
        self._loaded_done = True
        return loaded

    def _try_load_with_fallback(self, candidate: PluginCandidate) -> LoadedPlugin | None:
        """Try to load *candidate*; on failure, try lower-precedence alternatives."""
        full_group = self._precedence_groups.get(candidate.name, [candidate])
        # Only fall back to candidates from the same marketplace (or same
        # inline status) so cross-marketplace plugins don't steal each other's
        # fallback slots.
        group = [c for c in full_group if c.marketplace == candidate.marketplace]
        for c in group:
            try:
                plugin = self.load_plugin(c)
                self._warnings.extend(plugin.warnings)
                logger.info("Loaded plugin '%s' from %s (%s)", c.name, c.source, c.root_path)
                return plugin
            except PluginPathError as exc:
                for err in exc.errors:
                    self._errors.append(err)
                logger.warning("Plugin '%s' rejected (path safety): %s", c.name, exc.errors)
            except Exception as exc:
                self._errors.append(
                    PluginLoadError(plugin_name=c.name, message="Unexpected error loading plugin", exception=exc)
                )
                logger.warning("Plugin '%s' failed to load: %s", c.name, exc)
            # If this wasn't the last candidate, log the fallback attempt.
            if c is not group[-1]:
                logger.info("Falling back to next candidate for '%s'", c.name)
        return None

    def list_plugins(self) -> list[LoadedPlugin]:
        """Return loaded plugins (calls ``load_plugins`` if not yet done)."""
        if not self._loaded_done:
            self.load_plugins()
        return self._loaded

    def get_warnings(self) -> list[PluginWarning]:
        return list(self._warnings)

    def get_errors(self) -> list[PluginLoadError]:
        return list(self._errors)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _scan_dir(
        self, plugins_dir: Path, source: str
    ) -> list[PluginCandidate]:
        """Scan *plugins_dir* for plugins organised by marketplace.

        Expected layout::

            plugins_dir/
            ├── {marketplace-id}/{plugin-name}/{version}/   # marketplace
            ├── local/{plugin-name}/                        # local manual
            └── plugins_config.json
        """
        if not plugins_dir.is_dir():
            return []

        candidates: list[PluginCandidate] = []
        for child in sorted(plugins_dir.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if child.name == "local":
                candidates.extend(self._scan_local_dir(child, source))
            else:
                candidates.extend(self._scan_marketplace_dir(child, source))
        return candidates

    def _scan_local_dir(
        self, local_dir: Path, source: str
    ) -> list[PluginCandidate]:
        """Scan ``local/`` for manually managed plugins (two-level)."""
        candidates: list[PluginCandidate] = []
        for child in sorted(local_dir.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                candidate = self._try_parse(child, source, marketplace="local")
                if candidate is not None:
                    candidates.append(candidate)
        return candidates

    def _scan_marketplace_dir(
        self, mp_dir: Path, source: str
    ) -> list[PluginCandidate]:
        """Scan a marketplace directory (three-level: ``{mp}/{plugin}/{version}/``).

        For each plugin, selects the latest version using semantic version
        comparison (falls back to lexical sort for non-semver names).
        """
        marketplace_id = mp_dir.name
        candidates: list[PluginCandidate] = []
        for plugin_dir in sorted(mp_dir.iterdir()):
            if not plugin_dir.is_dir() or plugin_dir.name.startswith("."):
                continue
            version_dirs = [
                d for d in plugin_dir.iterdir() if d.is_dir() and not d.name.startswith(".")
            ]
            if not version_dirs:
                continue
            latest = max(version_dirs, key=_version_sort_key)
            # Pass the plugin folder name so auto-discovery uses the
            # plugin name (not the version dir name like "1.0.0").
            candidate = self._try_parse(
                latest, source, marketplace=marketplace_id, name_override=plugin_dir.name,
            )
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _try_parse(
        self, plugin_root: Path, source: str, *,
        marketplace: str | None = None, name_override: str | None = None,
    ) -> PluginCandidate | None:
        manifest, warnings, errors = self._parser.parse_file(plugin_root)

        if errors:
            for err in errors:
                self._errors.append(err)
            return None

        # For marketplace plugins without plugin.json the manifest name
        # defaults to the version directory name (e.g. "1.0.0").  Use the
        # plugin folder name instead.
        if name_override and manifest.name == plugin_root.name:
            manifest.name = name_override

        qualified = f"{manifest.name}@{marketplace}" if marketplace else None

        return PluginCandidate(
            name=manifest.name,
            root_path=plugin_root,
            source=source,  # type: ignore[arg-type]
            source_rank=SOURCE_RANK[source],
            manifest=manifest,
            marketplace=marketplace,
            qualified_name=qualified,
            warnings=warnings,
        )

    def _load_disabled_set(self) -> set[str]:
        """Merge global and project ``plugins_config.json`` disable lists.

        Project config has higher priority — if it explicitly enables a
        plugin that global config disables, the plugin stays enabled.
        """
        global_cfg = self._read_config(Path.home() / ".agentao" / "plugins_config.json")
        project_cfg = self._read_config(self._cwd / ".agentao" / "plugins_config.json")

        disabled: set[str] = set()

        # Global disables
        for name, entry in global_cfg.items():
            if isinstance(entry, dict) and entry.get("disabled"):
                disabled.add(name)
            elif entry is False:
                disabled.add(name)

        # Project overrides
        for name, entry in project_cfg.items():
            if isinstance(entry, dict) and entry.get("disabled"):
                disabled.add(name)
            elif entry is False:
                disabled.add(name)
            elif isinstance(entry, dict) and not entry.get("disabled", True):
                disabled.discard(name)
            elif entry is True:
                disabled.discard(name)

        return disabled

    @staticmethod
    def _read_config(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PluginPathError(Exception):
    """Raised when path safety validation fails."""

    def __init__(self, errors: list[PluginLoadError]) -> None:
        self.errors = errors
        super().__init__(f"Path safety errors: {errors}")


# ---------------------------------------------------------------------------
# Path resolution helpers
# ---------------------------------------------------------------------------

def _resolve_paths(root: Path, ref: str | list[str] | None) -> list[Path]:
    if ref is None:
        return []
    if isinstance(ref, str):
        return [(root / ref).resolve()]
    return [(root / p).resolve() for p in ref]


def _resolve_command_paths(root: Path, ref: Any) -> list[Path]:
    if ref is None:
        return []
    if isinstance(ref, str):
        return [(root / ref).resolve()]
    if isinstance(ref, list):
        return [(root / p).resolve() for p in ref]
    if isinstance(ref, dict):
        paths: list[Path] = []
        for meta in ref.values():
            if hasattr(meta, "source") and meta.source:
                paths.append((root / meta.source).resolve())
        return paths
    return []


def _collect_hook_specs(ref: Any) -> list[Any]:
    if ref is None:
        return []
    if isinstance(ref, (str, dict)):
        return [ref]
    if isinstance(ref, list):
        return list(ref)
    return []


def _resolve_mcp_servers(
    root: Path, ref: str | dict[str, Any] | None, *, warnings: list | None = None
) -> dict[str, dict[str, Any]]:
    if ref is None:
        return {}
    if isinstance(ref, dict):
        return ref
    if isinstance(ref, str):
        # Path reference — read the JSON file.
        mcp_path = (root / ref).resolve()
        if not mcp_path.exists():
            if warnings is not None:
                from .models import PluginWarning
                warnings.append(PluginWarning(
                    plugin_name=root.name,
                    message=f"mcpServers file not found: {ref}",
                    field="mcpServers",
                ))
            return {}
        try:
            data = json.loads(mcp_path.read_text(encoding="utf-8"))
            servers = data.get("mcpServers", data)
            if not isinstance(servers, dict):
                if warnings is not None:
                    from .models import PluginWarning
                    warnings.append(PluginWarning(
                        plugin_name=root.name,
                        message=f"mcpServers file {ref} does not contain a valid server dict",
                        field="mcpServers",
                    ))
                return {}
            return servers
        except (json.JSONDecodeError, OSError) as exc:
            if warnings is not None:
                from .models import PluginWarning
                warnings.append(PluginWarning(
                    plugin_name=root.name,
                    message=f"Failed to parse mcpServers file {ref}: {exc}",
                    field="mcpServers",
                ))
            return {}
    return {}
