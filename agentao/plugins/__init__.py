"""Agentao plugin system — runtime-path modules (models, hooks, validators).

The plugin loader (manifest parser, discovery, diagnostics, MCP merge,
resolvers) lives at :mod:`agentao.embedding.plugins` because it is
reached only from CLI subcommands and the loader phase of
``_load_and_register_plugins`` — see Phase 5b of
``docs/design/core-boundary-review.md`` for the split rationale.
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
