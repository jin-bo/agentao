"""Plugin MCP server resolution and merge."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import LoadedPlugin, PluginLoadError, PluginWarning

logger = logging.getLogger(__name__)


@dataclass
class McpMergeResult:
    """Result of merging plugin MCP servers into a base config."""

    servers: dict[str, dict[str, Any]]
    warnings: list[PluginWarning]
    errors: list[PluginLoadError]


def resolve_plugin_mcp_servers(
    plugin: LoadedPlugin,
) -> tuple[dict[str, dict[str, Any]], list[PluginWarning]]:
    """Collect MCP server definitions from a single plugin.

    Sources (in order):
      1. Default ``.mcp.json`` in plugin root (if ``manifest.mcp_servers`` is None)
      2. ``manifest.mcp_servers`` — path string (to a JSON file) or inline dict

    Returns ``(servers_dict, warnings)``.
    """
    warnings: list[PluginWarning] = []

    # Already resolved by the loader into plugin.mcp_servers dict.
    if plugin.mcp_servers:
        return dict(plugin.mcp_servers), warnings

    # If manifest.mcp_servers is None, try default .mcp.json
    if plugin.manifest.mcp_servers is None:
        default_path = plugin.root_path / ".mcp.json"
        if default_path.is_file():
            servers = _read_mcp_json(plugin.name, default_path, warnings)
            return servers, warnings

    return {}, warnings


def merge_plugin_mcp_servers(
    base_config: dict[str, dict[str, Any]],
    plugins: list[LoadedPlugin],
) -> McpMergeResult:
    """Merge MCP servers from all plugins into a base config.

    Merge order:
      1. Start with *base_config* (global + project MCP config).
      2. For each plugin (in load order), add its MCP servers.
      3. Same-name collision with base or another plugin is fatal for that
         plugin — no silent override or rename.

    Returns a ``McpMergeResult`` with the final merged dict, warnings, and
    errors.
    """
    merged = dict(base_config)
    # Track which source contributed each server name.
    origin: dict[str, str] = {name: "base" for name in merged}
    all_warnings: list[PluginWarning] = []
    all_errors: list[PluginLoadError] = []

    for plugin in plugins:
        servers, warnings = resolve_plugin_mcp_servers(plugin)
        all_warnings.extend(warnings)

        plugin_had_collision = False
        for server_name, server_cfg in servers.items():
            if server_name in origin:
                all_errors.append(
                    PluginLoadError(
                        plugin_name=plugin.name,
                        message=(
                            f"MCP server '{server_name}' from plugin '{plugin.name}' "
                            f"collides with server from '{origin[server_name]}'"
                        ),
                    )
                )
                plugin_had_collision = True
            else:
                merged[server_name] = server_cfg
                origin[server_name] = plugin.name

        # If this plugin had a collision, remove all servers it contributed
        # (atomic reject per plugin).
        if plugin_had_collision:
            for server_name in servers:
                if origin.get(server_name) == plugin.name:
                    merged.pop(server_name, None)
                    origin.pop(server_name, None)

    return McpMergeResult(
        servers=merged,
        warnings=all_warnings,
        errors=all_errors,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_mcp_json(
    plugin_name: str, path: Path, warnings: list[PluginWarning]
) -> dict[str, dict[str, Any]]:
    """Read a ``.mcp.json`` file and return the ``mcpServers`` dict."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        warnings.append(
            PluginWarning(
                plugin_name=plugin_name,
                message=f"Could not parse {path}: {exc}",
                field="mcpServers",
            )
        )
        return {}

    if not isinstance(data, dict):
        warnings.append(
            PluginWarning(
                plugin_name=plugin_name,
                message=f"{path} is not a JSON object",
                field="mcpServers",
            )
        )
        return {}

    # Accept both top-level dict-of-servers and { "mcpServers": {...} } wrapper.
    servers = data.get("mcpServers", data)
    if not isinstance(servers, dict):
        return {}

    return servers
