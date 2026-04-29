"""Default :class:`MCPRegistry` implementations.

- :class:`FileBackedMCPRegistry` is the CLI/ACP default and matches the
  pre-Protocol behavior (reads ``<wd>/.agentao/mcp.json`` plus
  ``~/.agentao/mcp.json``, project overrides global, env vars expanded).
- :class:`InMemoryMCPRegistry` is the programmatic counterpart for
  embedded hosts and tests; it holds a dict and returns a copy.

These live alongside :mod:`agentao.mcp.config` and
:mod:`agentao.mcp.client` so the MCP subsystem stays cohesive; the
abstract Protocol they satisfy lives in
:mod:`agentao.capabilities.mcp`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from .config import McpServerConfig, load_mcp_config


class FileBackedMCPRegistry:
    """Read MCP server configs from ``.agentao/mcp.json`` (project + user).

    Behavior is byte-equivalent to the pre-#17 implicit file-load path:
    project entries override user entries, env vars are expanded inside
    string fields, and missing files yield an empty dict.

    Each ``list_servers()`` call re-reads disk so an embedded host (or
    the CLI) sees config edits without restarting the agent. Hosts that
    want to cache the result should wrap or copy.
    """

    def __init__(
        self,
        *,
        project_root: Path,
        user_root: Optional[Path] = None,
    ) -> None:
        self._project_root = Path(project_root)
        self._user_root = Path(user_root) if user_root is not None else None

    def list_servers(self) -> Dict[str, McpServerConfig]:
        return load_mcp_config(
            project_root=self._project_root,
            user_root=self._user_root,
        )


class InMemoryMCPRegistry:
    """Return a fixed dict of MCP server configs.

    Designed for embedded hosts that own MCP server discovery
    themselves (plugin systems, dynamic provisioning) and for tests
    that need a programmatic registry without filesystem coupling.

    The dict is shallow-copied on every ``list_servers()`` call so the
    caller can mutate the result without leaking back into the
    registry. Pass ``None`` for an empty registry.
    """

    def __init__(self, servers: Optional[Dict[str, McpServerConfig]] = None) -> None:
        self._servers: Dict[str, McpServerConfig] = (
            {name: dict(cfg) for name, cfg in servers.items()} if servers else {}
        )

    def list_servers(self) -> Dict[str, McpServerConfig]:
        return {name: dict(cfg) for name, cfg in self._servers.items()}
