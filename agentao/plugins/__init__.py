"""Agentao plugin system — runtime-path modules.

This package holds the dataclasses, hook dispatcher, and validator
surface invoked from the agent runtime:

* :mod:`.models` — dataclasses imported by ``runtime/chat_loop.py``.
* :mod:`.hooks` — hook dispatcher invoked per turn / per tool call.
* :mod:`.skills` / :mod:`.agents` — ``validate_no_external_collisions``
  invoked by ``SkillManager.register_plugin_skills`` /
  ``AgentManager.register_plugin_agents`` on agent init.

The plugin loader (manifest parser, discovery, diagnostics, MCP merge,
resolvers) lives at :mod:`agentao.embedding.plugins` per Phase 5b of
``docs/design/core-boundary-review.md``: the loader is reached only
from CLI subcommands and the loader phase of
``_load_and_register_plugins``, so it sits in the embedding/factory
layer rather than core.
"""

from .models import (
    LoadedPlugin,
    PluginAgentDefinition,
    PluginAuthor,
    PluginCandidate,
    PluginCommandMetadata,
    PluginDependencyRef,
    PluginLoadError,
    PluginManifest,
    PluginSkillEntry,
    PluginWarning,
)

__all__ = [
    "LoadedPlugin",
    "PluginAgentDefinition",
    "PluginAuthor",
    "PluginCandidate",
    "PluginCommandMetadata",
    "PluginDependencyRef",
    "PluginLoadError",
    "PluginManifest",
    "PluginSkillEntry",
    "PluginWarning",
]
