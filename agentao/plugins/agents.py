"""Plugin agent runtime validators.

Runtime-path module — invoked by
:meth:`agentao.agents.manager.AgentManager.register_plugin_agents` on
agent init / `_load_and_register_plugins`. Resolution
(``resolve_plugin_agents`` and its private helpers) lives in
:mod:`agentao.embedding.plugins.resolvers.agents`.
"""

from __future__ import annotations

from .models import PluginAgentDefinition, PluginLoadError


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
