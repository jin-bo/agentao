"""Resolve plugin skills and commands into PluginSkillEntry lists."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from .models import (
    LoadedPlugin,
    PluginCommandMetadata,
    PluginLoadError,
    PluginSkillEntry,
    PluginWarning,
)

logger = logging.getLogger(__name__)


class PluginSkillCollisionError(Exception):
    """Raised when plugin skill/command names collide."""

    def __init__(self, errors: list[PluginLoadError]) -> None:
        self.errors = errors
        super().__init__(f"Skill/command collision: {errors}")


def resolve_plugin_entries(
    plugin: LoadedPlugin,
) -> tuple[list[PluginSkillEntry], list[PluginWarning], list[PluginLoadError]]:
    """Convert a LoadedPlugin's skill_roots and commands into PluginSkillEntry
    objects ready for registration.

    Returns ``(entries, warnings, errors)``.
    """
    entries: list[PluginSkillEntry] = []
    warnings: list[PluginWarning] = []
    errors: list[PluginLoadError] = []

    # --- skills ---
    skill_entries, skill_warns = _resolve_skills(plugin)
    entries.extend(skill_entries)
    warnings.extend(skill_warns)

    # --- commands ---
    cmd_entries, cmd_warns, cmd_errs = _resolve_commands(plugin)
    entries.extend(cmd_entries)
    warnings.extend(cmd_warns)
    errors.extend(cmd_errs)

    # --- collision check within this plugin ---
    collision_errors = _check_internal_collisions(plugin.name, entries)
    errors.extend(collision_errors)

    return entries, warnings, errors


def validate_no_external_collisions(
    plugin_name: str,
    entries: list[PluginSkillEntry],
    existing_names: set[str],
) -> list[PluginLoadError]:
    """Check that no entry runtime_name conflicts with existing skills."""
    errors: list[PluginLoadError] = []
    for entry in entries:
        if entry.runtime_name in existing_names:
            errors.append(
                PluginLoadError(
                    plugin_name=plugin_name,
                    message=(
                        f"Plugin skill '{entry.runtime_name}' collides with "
                        f"an existing built-in or project skill"
                    ),
                )
            )
    return errors


# ---------------------------------------------------------------------------
# Skills resolution
# ---------------------------------------------------------------------------

def _resolve_skills(
    plugin: LoadedPlugin,
) -> tuple[list[PluginSkillEntry], list[PluginWarning]]:
    entries: list[PluginSkillEntry] = []
    warnings: list[PluginWarning] = []

    skill_roots = plugin.skill_roots
    # Auto-discover skills/ directory when manifest doesn't declare skills.
    if not skill_roots and plugin.manifest.skills is None:
        default_dir = plugin.root_path / "skills"
        if default_dir.is_dir():
            skill_roots = [default_dir]

    for skill_root in skill_roots:
        if not skill_root.is_dir():
            warnings.append(
                PluginWarning(
                    plugin_name=plugin.name,
                    message=f"Skills directory does not exist: {skill_root}",
                    field="skills",
                )
            )
            continue

        for skill_dir in sorted(skill_root.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            entry = _parse_skill_md(plugin.name, skill_dir, skill_md)
            if entry is not None:
                entries.append(entry)

    return entries, warnings


def _parse_skill_md(
    plugin_name: str, skill_dir: Path, skill_md: Path
) -> PluginSkillEntry | None:
    """Parse a SKILL.md file and produce a PluginSkillEntry."""
    try:
        content = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    frontmatter, body = _parse_yaml_frontmatter(content)
    skill_name = frontmatter.get("name", skill_dir.name)
    description = frontmatter.get("description", "")
    runtime_name = f"{plugin_name}:{skill_name}"

    return PluginSkillEntry(
        runtime_name=runtime_name,
        plugin_name=plugin_name,
        source_kind="plugin-skill",
        source_path=skill_md,
        content=body,
        description=description,
    )


# ---------------------------------------------------------------------------
# Commands resolution
# ---------------------------------------------------------------------------

def _resolve_commands(
    plugin: LoadedPlugin,
) -> tuple[list[PluginSkillEntry], list[PluginWarning], list[PluginLoadError]]:
    entries: list[PluginSkillEntry] = []
    warnings: list[PluginWarning] = []
    errors: list[PluginLoadError] = []

    manifest = plugin.manifest
    root = plugin.root_path

    if manifest.commands is None:
        # No manifest commands — try default commands/ directory.
        default_cmds_dir = root / "commands"
        if default_cmds_dir.is_dir():
            found, warns = _scan_commands_dir(plugin.name, default_cmds_dir)
            entries.extend(found)
            warnings.extend(warns)
        return entries, warnings, errors

    if isinstance(manifest.commands, str):
        # Single path reference.
        cmds_dir = (root / manifest.commands).resolve()
        if cmds_dir.is_dir():
            found, warns = _scan_commands_dir(plugin.name, cmds_dir)
            entries.extend(found)
            warnings.extend(warns)
        else:
            warnings.append(
                PluginWarning(
                    plugin_name=plugin.name,
                    message=f"Commands path is not a directory: {manifest.commands}",
                    field="commands",
                )
            )
        return entries, warnings, errors

    if isinstance(manifest.commands, list):
        # List of path references to directories.
        for cmd_path_str in manifest.commands:
            cmds_dir = (root / cmd_path_str).resolve()
            if cmds_dir.is_dir():
                found, warns = _scan_commands_dir(plugin.name, cmds_dir)
                entries.extend(found)
                warnings.extend(warns)
            elif cmds_dir.is_file() and cmds_dir.suffix == ".md":
                entry = _md_file_to_entry(plugin.name, cmds_dir)
                if entry is not None:
                    entries.append(entry)
            else:
                warnings.append(
                    PluginWarning(
                        plugin_name=plugin.name,
                        message=f"Commands path not found: {cmd_path_str}",
                        field="commands",
                    )
                )
        return entries, warnings, errors

    if isinstance(manifest.commands, dict):
        # Object mapping — each key is a command name.
        for cmd_name, meta in manifest.commands.items():
            if not isinstance(meta, PluginCommandMetadata):
                continue
            entry = _metadata_to_entry(plugin.name, root, cmd_name, meta)
            if entry is not None:
                entries.append(entry)
            else:
                errors.append(
                    PluginLoadError(
                        plugin_name=plugin.name,
                        message=f"Command '{cmd_name}' has neither 'source' nor 'content'",
                    )
                )
        return entries, warnings, errors

    return entries, warnings, errors


def _scan_commands_dir(
    plugin_name: str, cmds_dir: Path
) -> tuple[list[PluginSkillEntry], list[PluginWarning]]:
    """Scan a directory for ``*.md`` command files."""
    entries: list[PluginSkillEntry] = []
    warnings: list[PluginWarning] = []

    for md_file in sorted(cmds_dir.glob("*.md")):
        entry = _md_file_to_entry(plugin_name, md_file)
        if entry is not None:
            entries.append(entry)

    return entries, warnings


def _md_file_to_entry(
    plugin_name: str, md_file: Path
) -> PluginSkillEntry | None:
    """Convert a single markdown command file to a PluginSkillEntry."""
    try:
        content = md_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    cmd_name = md_file.stem  # e.g. "review-summary.md" -> "review-summary"
    frontmatter, body = _parse_yaml_frontmatter(content)
    description = frontmatter.get("description", "")
    runtime_name = f"{plugin_name}:{cmd_name}"

    return PluginSkillEntry(
        runtime_name=runtime_name,
        plugin_name=plugin_name,
        source_kind="plugin-command",
        source_path=md_file,
        content=body,
        description=description,
    )


def _metadata_to_entry(
    plugin_name: str,
    root: Path,
    cmd_name: str,
    meta: PluginCommandMetadata,
) -> PluginSkillEntry | None:
    """Convert a PluginCommandMetadata (from manifest mapping) to an entry."""
    runtime_name = f"{plugin_name}:{cmd_name}"

    if meta.content is not None:
        # Inline content — no file needed.
        return PluginSkillEntry(
            runtime_name=runtime_name,
            plugin_name=plugin_name,
            source_kind="plugin-command",
            source_path=None,
            content=meta.content,
            description=meta.description,
            argument_hint=meta.argument_hint,
            model=meta.model,
            allowed_tools=meta.allowed_tools,
        )

    if meta.source is not None:
        # File reference.
        source_path = (root / meta.source).resolve()
        if not source_path.exists():
            return None
        try:
            raw_content = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

        # Strip YAML frontmatter so metadata headers are not injected into
        # the prompt body — matching the behavior of _md_file_to_entry().
        _fm, body = _parse_yaml_frontmatter(raw_content)
        description = meta.description or _fm.get("description", "")

        return PluginSkillEntry(
            runtime_name=runtime_name,
            plugin_name=plugin_name,
            source_kind="plugin-command",
            source_path=source_path,
            content=body,
            description=description,
            argument_hint=meta.argument_hint,
            model=meta.model,
            allowed_tools=meta.allowed_tools,
        )

    return None


# ---------------------------------------------------------------------------
# Collision detection
# ---------------------------------------------------------------------------

def _check_internal_collisions(
    plugin_name: str, entries: list[PluginSkillEntry]
) -> list[PluginLoadError]:
    """Check for duplicate runtime_names within a single plugin."""
    seen: dict[str, PluginSkillEntry] = {}
    errors: list[PluginLoadError] = []

    for entry in entries:
        if entry.runtime_name in seen:
            existing = seen[entry.runtime_name]
            errors.append(
                PluginLoadError(
                    plugin_name=plugin_name,
                    message=(
                        f"Duplicate runtime name '{entry.runtime_name}': "
                        f"{entry.source_kind} collides with {existing.source_kind}"
                    ),
                )
            )
        else:
            seen[entry.runtime_name] = entry

    return errors


# ---------------------------------------------------------------------------
# YAML frontmatter helper (shared with SkillManager)
# ---------------------------------------------------------------------------

def _parse_yaml_frontmatter(content: str) -> tuple[dict[str, str], str]:
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    try:
        fm = yaml.safe_load(parts[1]) or {}
        fm = {k: str(v).strip() if v is not None else "" for k, v in fm.items()}
    except yaml.YAMLError:
        fm = {}

    return fm, parts[2].strip()
