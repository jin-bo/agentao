"""Plugin loader — manifest parsing, discovery, MCP merge, and diagnostics.

Phase 5b of ``docs/design/core-boundary-review.md``: the plugin loader
lives here, alongside the rest of the embedding/factory layer, because
it is reached only from CLI subcommands and the loader phase of
``_load_and_register_plugins`` — never from the runtime hot path.

Runtime-path modules stay in :mod:`agentao.plugins`:

* :mod:`agentao.plugins.models` — dataclasses imported by
  ``runtime/chat_loop.py`` (``StopHookResult``).
* :mod:`agentao.plugins.hooks` — hook dispatcher invoked per turn /
  per tool call.
* :mod:`agentao.plugins.skills` /
  :mod:`agentao.plugins.agents` — ``validate_no_external_collisions``
  invoked by ``SkillManager.register_plugin_skills`` /
  ``AgentManager.register_plugin_agents`` on agent init.

This package re-exports the symbols a CLI/embedded host actually
consumes; first-party callers may also import the submodules
directly when the convenience surface isn't enough.
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
