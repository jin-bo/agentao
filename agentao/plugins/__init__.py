"""Agentao plugin system — manifest loading, discovery, and diagnostics."""

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
