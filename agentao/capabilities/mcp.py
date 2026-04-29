"""MCPRegistry capability protocol.

Embedded hosts inject this protocol to enumerate MCP servers from any
source (in-memory dict, plugin system, dynamic discovery, remote
registry) without writing to ``.agentao/mcp.json``. The default
:class:`agentao.mcp.registry.FileBackedMCPRegistry` reads the same
``<wd>/.agentao/mcp.json`` + ``~/.agentao/mcp.json`` files the
pre-Protocol code consulted.

Concrete classes (e.g. ``FileBackedMCPRegistry``) live in
``agentao/mcp/`` next to the rest of the MCP machinery; only the
abstract contract sits here, mirroring how ``ShellExecutor`` is
co-located with ``LocalShellExecutor`` and how ``MemoryStore`` is
co-located with ``SQLiteMemoryStore``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Protocol

if TYPE_CHECKING:
    # Annotation-only import to keep this module zero-import-cost; with
    # ``from __future__ import annotations`` the alias is a string at
    # runtime and never resolved.
    from ..mcp.config import McpServerConfig


class MCPRegistry(Protocol):
    """Source of MCP server configurations.

    A registry returns a mapping of server name → MCP server config (the
    same ``McpServerConfig`` shape ``McpClientManager`` already accepts).
    Per-name override semantics are the caller's responsibility — the
    registry is purely an enumeration interface.

    Lifecycle: registries are constructed by the embedding factory (or
    the host) and live for the duration of the :class:`Agentao` they
    feed. The default :class:`FileBackedMCPRegistry` reads disk on
    every ``list_servers()`` call so config edits become visible
    without restarting the agent; programmatic registries are free to
    cache.
    """

    def list_servers(self) -> Dict[str, "McpServerConfig"]:
        """Return the current set of MCP server configs.

        Implementations should return an independent dict on each call
        so callers can safely mutate the result before passing it to
        ``McpClientManager``.
        """
        ...
