"""Resolve plugin agent definitions from LoadedPlugin agent_paths."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from .models import (
    LoadedPlugin,
    PluginAgentDefinition,
    PluginLoadError,
    PluginWarning,
)

logger = logging.getLogger(__name__)


def resolve_plugin_agents(
    plugin: LoadedPlugin,
) -> tuple[list[PluginAgentDefinition], list[PluginWarning], list[PluginLoadError]]:
    """Convert a LoadedPlugin's agent_paths into PluginAgentDefinition objects.

    Also scans the default ``agents/`` directory if ``manifest.agents`` is
    None and the directory exists.

    Returns ``(definitions, warnings, errors)``.
    """
    defs: list[PluginAgentDefinition] = []
    warnings: list[PluginWarning] = []
    errors: list[PluginLoadError] = []

    if plugin.manifest.agents is None:
        # No manifest agents — try default agents/ directory.
        default_dir = plugin.root_path / "agents"
        if default_dir.is_dir():
            found, warns = _scan_agents_dir(plugin.name, default_dir)
            defs.extend(found)
            warnings.extend(warns)
    else:
        # Manifest-declared agent paths (already resolved in plugin.agent_paths).
        for agent_path in plugin.agent_paths:
            if agent_path.is_dir():
                found, warns = _scan_agents_dir(plugin.name, agent_path)
                defs.extend(found)
                warnings.extend(warns)
            elif agent_path.is_file() and agent_path.suffix == ".md":
                defn = _parse_agent_md(plugin.name, agent_path)
                if defn is not None:
                    defs.append(defn)
                else:
                    warnings.append(
                        PluginWarning(
                            plugin_name=plugin.name,
                            message=f"Could not parse agent file: {agent_path}",
                            field="agents",
                        )
                    )
            else:
                warnings.append(
                    PluginWarning(
                        plugin_name=plugin.name,
                        message=f"Agent path not found or not a .md file: {agent_path}",
                        field="agents",
                    )
                )

    # Internal collision check.
    collision_errors = _check_internal_collisions(plugin.name, defs)
    errors.extend(collision_errors)

    return defs, warnings, errors


def validate_no_external_collisions(
    plugin_name: str,
    defs: list[PluginAgentDefinition],
    existing_names: set[str],
) -> list[PluginLoadError]:
    """Check that no agent runtime_name conflicts with existing agents."""
    errors: list[PluginLoadError] = []
    for defn in defs:
        if defn.runtime_name in existing_names:
            errors.append(
                PluginLoadError(
                    plugin_name=plugin_name,
                    message=(
                        f"Plugin agent '{defn.runtime_name}' collides with "
                        f"an existing built-in or project agent"
                    ),
                )
            )
    return errors


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _scan_agents_dir(
    plugin_name: str, agents_dir: Path
) -> tuple[list[PluginAgentDefinition], list[PluginWarning]]:
    """Scan a directory for ``*.md`` agent definition files."""
    defs: list[PluginAgentDefinition] = []
    warnings: list[PluginWarning] = []

    for md_file in sorted(agents_dir.glob("*.md")):
        defn = _parse_agent_md(plugin_name, md_file)
        if defn is not None:
            defs.append(defn)
        else:
            warnings.append(
                PluginWarning(
                    plugin_name=plugin_name,
                    message=f"Could not parse agent file: {md_file}",
                    field="agents",
                )
            )

    return defs, warnings


def _parse_agent_md(
    plugin_name: str, md_file: Path
) -> PluginAgentDefinition | None:
    """Parse an agent markdown file and produce a PluginAgentDefinition."""
    try:
        content = md_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    frontmatter, body = _parse_yaml_frontmatter(content)
    agent_name = str(frontmatter.get("name", md_file.stem))
    description = str(frontmatter.get("description", ""))
    runtime_name = f"{plugin_name}:{agent_name}"

    return PluginAgentDefinition(
        runtime_name=runtime_name,
        plugin_name=plugin_name,
        source_path=md_file,
        raw_markdown=content,
        description=description or None,
    )


def _check_internal_collisions(
    plugin_name: str, defs: list[PluginAgentDefinition]
) -> list[PluginLoadError]:
    """Check for duplicate runtime_names within a single plugin."""
    seen: set[str] = set()
    errors: list[PluginLoadError] = []

    for defn in defs:
        if defn.runtime_name in seen:
            errors.append(
                PluginLoadError(
                    plugin_name=plugin_name,
                    message=f"Duplicate agent runtime name '{defn.runtime_name}'",
                )
            )
        else:
            seen.add(defn.runtime_name)

    return errors


def _parse_yaml_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter, preserving native types (lists, ints, etc.).

    Unlike the skill frontmatter parser which coerces everything to str,
    the agent parser must keep list values (e.g. ``tools: [read_file]``)
    as lists so that ``AgentManager`` can interpret them correctly.
    """
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    try:
        fm = yaml.safe_load(parts[1]) or {}
        if not isinstance(fm, dict):
            fm = {}
    except yaml.YAMLError:
        fm = {}

    return fm, parts[2].strip()
