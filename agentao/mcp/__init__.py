"""MCP (Model Context Protocol) support for Agentao.

The :mod:`mcp` SDK (httpx + click + rich + pydantic_settings + starlette)
is heavy; ``McpClientManager`` and ``McpTool`` lazy-load it on first
attribute access (PEP 562). Importing :mod:`agentao.mcp` itself only
brings in the registry + config types, which the harness needs to
present a Protocol surface to embedded hosts even when no MCP server is
attached.
"""

from typing import TYPE_CHECKING

from ..capabilities.mcp import MCPRegistry
from .config import load_mcp_config
from .registry import FileBackedMCPRegistry, InMemoryMCPRegistry

__all__ = [
    "load_mcp_config",
    "MCPRegistry",
    "FileBackedMCPRegistry",
    "InMemoryMCPRegistry",
    "McpClientManager",
    "McpTool",
]

if TYPE_CHECKING:
    from .client import McpClientManager  # noqa: F401
    from .tool import McpTool  # noqa: F401


def __getattr__(name: str):
    if name == "McpClientManager":
        from .client import McpClientManager

        return McpClientManager
    if name == "McpTool":
        from .tool import McpTool

        return McpTool
    raise AttributeError(f"module 'agentao.mcp' has no attribute {name!r}")
