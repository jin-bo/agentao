"""Plugin loader — manifest parsing, discovery, MCP merge, and diagnostics.

Lives under ``embedding/`` because it is reached only from CLI
subcommands and the loader phase of ``_load_and_register_plugins`` —
never from the runtime hot path. Runtime-path modules (models, hooks,
validators) stay in :mod:`agentao.plugins`. See Phase 5b of
``docs/design/core-boundary-review.md`` for the split rationale.
"""

from .diagnostics import PluginDiagnostics, build_diagnostics
from .manager import PluginManager
from .manifest import PluginManifestParser
from .mcp import merge_plugin_mcp_servers, resolve_plugin_mcp_servers
from .resolvers.agents import resolve_plugin_agents
from .resolvers.skills import resolve_plugin_entries

__all__ = [
    "PluginDiagnostics",
    "PluginManager",
    "PluginManifestParser",
    "build_diagnostics",
    "merge_plugin_mcp_servers",
    "resolve_plugin_agents",
    "resolve_plugin_entries",
    "resolve_plugin_mcp_servers",
]
