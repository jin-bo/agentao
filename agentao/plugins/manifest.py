"""Plugin manifest parser and path safety validation."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .models import (
    PluginAuthor,
    PluginCommandMetadata,
    PluginDependencyRef,
    PluginLoadError,
    PluginManifest,
    PluginWarning,
    PluginWarningSeverity,
)

logger = logging.getLogger(__name__)

# Fields known to Claude Code but not (yet) supported by Agentao.
KNOWN_UNSUPPORTED_FIELDS: set[str] = {
    "outputStyles",
    "lspServers",
    "settings",
    "channels",
    "userConfig",
}

# Top-level fields recognised by Agentao.
SUPPORTED_FIELDS: set[str] = {
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "dependencies",
    "commands",
    "skills",
    "agents",
    "hooks",
    "mcpServers",
}


class PluginManifestParser:
    """Parses and validates ``plugin.json`` files."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_file(self, plugin_root: Path) -> tuple[PluginManifest, list[PluginWarning], list[PluginLoadError]]:
        """Parse ``plugin.json`` from *plugin_root*.

        Returns ``(manifest, warnings, errors)``.  If *errors* is non-empty
        the manifest may be partially populated but should not be trusted.
        """
        manifest_path = plugin_root / "plugin.json"
        plugin_name = plugin_root.name  # fallback before we know the real name

        if not manifest_path.exists():
            return (
                PluginManifest(name=plugin_name),
                [PluginWarning(
                    plugin_name=plugin_name,
                    message=f"No plugin.json in {plugin_root} — using auto-discovery (name={plugin_name!r})",
                    severity=PluginWarningSeverity.INFO,
                )],
                [],
            )

        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return (
                PluginManifest(name=plugin_name),
                [],
                [PluginLoadError(plugin_name=plugin_name, message="Failed to parse plugin.json", exception=exc)],
            )

        if not isinstance(raw, dict):
            return (
                PluginManifest(name=plugin_name),
                [],
                [PluginLoadError(plugin_name=plugin_name, message="plugin.json must be a JSON object")],
            )

        return self.parse_dict(raw, plugin_root=plugin_root)

    def parse_dict(
        self, raw: dict[str, Any], *, plugin_root: Path
    ) -> tuple[PluginManifest, list[PluginWarning], list[PluginLoadError]]:
        """Parse a raw dict (already deserialised from JSON).

        Returns ``(manifest, warnings, errors)``.
        """
        warnings: list[PluginWarning] = []
        errors: list[PluginLoadError] = []

        plugin_name = raw.get("name", plugin_root.name)

        # --- name validation ---
        if "name" not in raw or not isinstance(raw["name"], str):
            errors.append(PluginLoadError(plugin_name=plugin_name, message="'name' is required and must be a string"))
            return PluginManifest(name=plugin_name), warnings, errors

        name = raw["name"].strip()
        if not name:
            errors.append(PluginLoadError(plugin_name=plugin_name, message="'name' must not be empty"))
            return PluginManifest(name=plugin_name), warnings, errors

        if " " in name:
            errors.append(PluginLoadError(plugin_name=name, message="'name' must not contain spaces"))
            return PluginManifest(name=name), warnings, errors

        # --- unsupported / unknown fields ---
        unsupported: dict[str, Any] = {}
        for key in raw:
            if key in KNOWN_UNSUPPORTED_FIELDS:
                unsupported[key] = raw[key]
                warnings.append(
                    PluginWarning(
                        plugin_name=name,
                        message=f"Field '{key}' is not supported by Agentao and will be ignored",
                        severity=PluginWarningSeverity.WARNING,
                        field=key,
                    )
                )
            elif key not in SUPPORTED_FIELDS:
                unsupported[key] = raw[key]
                warnings.append(
                    PluginWarning(
                        plugin_name=name,
                        message=f"Unknown field '{key}' will be ignored",
                        severity=PluginWarningSeverity.INFO,
                        field=key,
                    )
                )

        # --- scalar metadata ---
        version = _opt_str(raw, "version")
        description = _opt_str(raw, "description")
        homepage = _opt_str(raw, "homepage")
        repository = _opt_str(raw, "repository")
        license_ = _opt_str(raw, "license")
        keywords = _opt_str_list(raw, "keywords") or []

        # --- author ---
        author = self._parse_author(raw.get("author"), name, warnings)

        # --- dependencies ---
        dependencies = self._parse_dependencies(raw.get("dependencies"), name, warnings)

        # --- component references (raw values, not resolved) ---
        commands = self._parse_commands(raw.get("commands"), name, errors)
        skills = self._parse_path_ref(raw.get("skills"), "skills", name, errors)
        agents = self._parse_path_ref(raw.get("agents"), "agents", name, errors)
        hooks = self._parse_hooks(raw.get("hooks"), name, errors)
        mcp_servers = self._parse_mcp_servers(raw.get("mcpServers"), name, errors)

        manifest = PluginManifest(
            name=name,
            version=version,
            description=description,
            author=author,
            homepage=homepage,
            repository=repository,
            license=license_,
            keywords=keywords,
            dependencies=dependencies,
            commands=commands,
            skills=skills,
            agents=agents,
            hooks=hooks,
            mcp_servers=mcp_servers,
            unsupported_fields=unsupported,
        )

        return manifest, warnings, errors

    def validate_paths(
        self, manifest: PluginManifest, *, plugin_root: Path
    ) -> list[PluginWarning | PluginLoadError]:
        """Validate that all paths in *manifest* are safe (within *plugin_root*).

        Returns a mixed list of warnings and errors.  Any ``PluginLoadError``
        in the result means the plugin should be rejected.
        """
        issues: list[PluginWarning | PluginLoadError] = []
        resolved_root = plugin_root.resolve()

        paths_to_check: list[tuple[str, str]] = []  # (field_name, path_string)

        # Collect all path strings from manifest components.
        for p in _collect_path_strings(manifest.skills, "skills"):
            paths_to_check.append(p)
        for p in _collect_path_strings(manifest.agents, "agents"):
            paths_to_check.append(p)
        for p in _collect_command_paths(manifest.commands):
            paths_to_check.append(p)
        for p in _collect_hook_paths(manifest.hooks):
            paths_to_check.append(p)
        if isinstance(manifest.mcp_servers, str):
            paths_to_check.append(("mcpServers", manifest.mcp_servers))

        for field_name, path_str in paths_to_check:
            issue = _check_path_safety(manifest.name, field_name, path_str, resolved_root)
            if issue is not None:
                issues.append(issue)

        return issues

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_author(
        raw: Any, plugin_name: str, warnings: list[PluginWarning]
    ) -> PluginAuthor | None:
        if raw is None:
            return None
        if isinstance(raw, str):
            return PluginAuthor(name=raw)
        if isinstance(raw, dict):
            author_name = raw.get("name")
            if not isinstance(author_name, str):
                warnings.append(
                    PluginWarning(plugin_name=plugin_name, message="author.name must be a string", field="author")
                )
                return None
            return PluginAuthor(
                name=author_name,
                email=raw.get("email"),
                url=raw.get("url"),
            )
        warnings.append(
            PluginWarning(plugin_name=plugin_name, message="author must be a string or object", field="author")
        )
        return None

    @staticmethod
    def _parse_dependencies(
        raw: Any, plugin_name: str, warnings: list[PluginWarning]
    ) -> list[PluginDependencyRef]:
        if raw is None:
            return []
        if not isinstance(raw, list):
            warnings.append(
                PluginWarning(plugin_name=plugin_name, message="dependencies must be an array", field="dependencies")
            )
            return []
        result: list[PluginDependencyRef] = []
        for item in raw:
            if isinstance(item, str):
                result.append(PluginDependencyRef(plugin_name=item))
            elif isinstance(item, dict):
                dep_name = item.get("name") or item.get("plugin_name")
                if not isinstance(dep_name, str):
                    warnings.append(
                        PluginWarning(
                            plugin_name=plugin_name,
                            message="dependency entry missing 'name'",
                            field="dependencies",
                        )
                    )
                    continue
                result.append(
                    PluginDependencyRef(
                        plugin_name=dep_name,
                        version=item.get("version"),
                        marketplace=item.get("marketplace"),
                    )
                )
        return result

    @staticmethod
    def _parse_commands(
        raw: Any, plugin_name: str, errors: list[PluginLoadError]
    ) -> str | list[str] | dict[str, PluginCommandMetadata] | None:
        if raw is None:
            return None
        if isinstance(raw, str):
            return raw
        if isinstance(raw, list):
            if all(isinstance(x, str) for x in raw):
                return raw
            errors.append(PluginLoadError(plugin_name=plugin_name, message="commands list must contain only strings"))
            return None
        if isinstance(raw, dict):
            result: dict[str, PluginCommandMetadata] = {}
            for cmd_name, cmd_val in raw.items():
                if isinstance(cmd_val, dict):
                    result[cmd_name] = PluginCommandMetadata(
                        source=cmd_val.get("source"),
                        content=cmd_val.get("content"),
                        description=cmd_val.get("description"),
                        argument_hint=cmd_val.get("argumentHint"),
                        model=cmd_val.get("model"),
                        allowed_tools=cmd_val.get("allowedTools", []),
                    )
                elif isinstance(cmd_val, str):
                    result[cmd_name] = PluginCommandMetadata(source=cmd_val)
                else:
                    errors.append(
                        PluginLoadError(
                            plugin_name=plugin_name,
                            message=f"commands.{cmd_name} must be a string or object",
                        )
                    )
            return result
        errors.append(
            PluginLoadError(plugin_name=plugin_name, message="commands must be a string, array, or object")
        )
        return None

    @staticmethod
    def _parse_path_ref(
        raw: Any, field_name: str, plugin_name: str, errors: list[PluginLoadError]
    ) -> str | list[str] | None:
        if raw is None:
            return None
        if isinstance(raw, str):
            return raw
        if isinstance(raw, list):
            if all(isinstance(x, str) for x in raw):
                return raw
            errors.append(
                PluginLoadError(plugin_name=plugin_name, message=f"{field_name} list must contain only strings")
            )
            return None
        errors.append(
            PluginLoadError(plugin_name=plugin_name, message=f"{field_name} must be a string or array of strings")
        )
        return None

    @staticmethod
    def _parse_hooks(
        raw: Any, plugin_name: str, errors: list[PluginLoadError]
    ) -> str | dict[str, Any] | list[str | dict[str, Any]] | None:
        if raw is None:
            return None
        if isinstance(raw, (str, dict)):
            return raw
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, (str, dict)):
                    errors.append(
                        PluginLoadError(
                            plugin_name=plugin_name,
                            message="hooks array items must be strings or objects",
                        )
                    )
                    return None
            return raw
        errors.append(
            PluginLoadError(plugin_name=plugin_name, message="hooks must be a string, object, or array")
        )
        return None

    @staticmethod
    def _parse_mcp_servers(
        raw: Any, plugin_name: str, errors: list[PluginLoadError]
    ) -> str | dict[str, Any] | None:
        if raw is None:
            return None
        if isinstance(raw, (str, dict)):
            return raw
        errors.append(
            PluginLoadError(plugin_name=plugin_name, message="mcpServers must be a string or object")
        )
        return None


# ---------------------------------------------------------------------------
# Path safety helpers
# ---------------------------------------------------------------------------

def _check_path_safety(
    plugin_name: str, field_name: str, path_str: str, resolved_root: Path
) -> PluginLoadError | None:
    """Return an error if *path_str* is unsafe, else ``None``."""
    # Must be relative and start with ./
    if path_str.startswith("/"):
        return PluginLoadError(
            plugin_name=plugin_name,
            message=f"Absolute path not allowed in '{field_name}': {path_str}",
        )

    if ".." in Path(path_str).parts:
        return PluginLoadError(
            plugin_name=plugin_name,
            message=f"Path traversal (..) not allowed in '{field_name}': {path_str}",
        )

    if not path_str.startswith("./"):
        return PluginLoadError(
            plugin_name=plugin_name,
            message=f"Paths must start with './' in '{field_name}': {path_str}",
        )

    # Resolve and verify containment.
    resolved = (resolved_root / path_str).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        return PluginLoadError(
            plugin_name=plugin_name,
            message=f"Path escapes plugin root in '{field_name}': {path_str}",
        )

    return None


# ---------------------------------------------------------------------------
# Collecting paths from various manifest shapes
# ---------------------------------------------------------------------------

def _collect_path_strings(raw: str | list[str] | None, field_name: str) -> list[tuple[str, str]]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [(field_name, raw)]
    return [(field_name, p) for p in raw]


def _collect_command_paths(
    raw: str | list[str] | dict[str, PluginCommandMetadata] | None,
) -> list[tuple[str, str]]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [("commands", raw)]
    if isinstance(raw, list):
        return [("commands", p) for p in raw]
    if isinstance(raw, dict):
        result: list[tuple[str, str]] = []
        for cmd_name, meta in raw.items():
            if isinstance(meta, PluginCommandMetadata) and meta.source:
                result.append((f"commands.{cmd_name}", meta.source))
        return result
    return []


def _collect_hook_paths(
    raw: str | dict[str, Any] | list[str | dict[str, Any]] | None,
) -> list[tuple[str, str]]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [("hooks", raw)]
    if isinstance(raw, list):
        return [("hooks", item) for item in raw if isinstance(item, str)]
    # dict hooks are inline — no file paths to validate
    return []


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------

def _opt_str(d: dict[str, Any], key: str) -> str | None:
    val = d.get(key)
    return val if isinstance(val, str) else None


def _opt_str_list(d: dict[str, Any], key: str) -> list[str] | None:
    val = d.get(key)
    if isinstance(val, list) and all(isinstance(x, str) for x in val):
        return val
    return None
