"""Plugin skill/command runtime validators.

Runtime-path module — invoked by
:meth:`agentao.skills.manager.SkillManager.register_plugin_skills` on
agent init / `_load_and_register_plugins`. Resolution
(``resolve_plugin_entries`` and its private helpers) lives in
:mod:`agentao.plugins.resolvers.skills`.
"""

from __future__ import annotations

from .models import PluginLoadError, PluginSkillEntry


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
