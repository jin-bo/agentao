"""MCP tool wrapper that adapts MCP-discovered tools to the ChatAgent Tool interface."""

import re
from typing import Any, Dict

from mcp.types import Tool as McpToolDef

from ..tools.base import Tool

# Characters allowed in tool names (OpenAI function calling)
_INVALID_CHARS_RE = re.compile(r"[^a-zA-Z0-9_]")


def _sanitize_name(name: str) -> str:
    """Replace invalid characters with underscores."""
    return _INVALID_CHARS_RE.sub("_", name)


def make_mcp_tool_name(server_name: str, tool_name: str) -> str:
    """Create a fully qualified MCP tool name: mcp_{server}_{tool}."""
    return f"mcp_{_sanitize_name(server_name)}_{_sanitize_name(tool_name)}"


def parse_mcp_tool_name(fqn: str) -> tuple:
    """Parse 'mcp_{server}_{tool}' back to (server_name, tool_name).

    Uses the first underscore after 'mcp_' as the separator between
    server name and tool name.
    """
    if not fqn.startswith("mcp_"):
        raise ValueError(f"Not an MCP tool name: {fqn}")
    rest = fqn[4:]  # strip "mcp_"
    idx = rest.find("_")
    if idx == -1:
        return rest, rest
    return rest[:idx], rest[idx + 1:]


class McpTool(Tool):
    """Wraps an MCP-discovered tool as a ChatAgent Tool."""

    def __init__(
        self,
        server_name: str,
        mcp_tool: McpToolDef,
        call_fn,
        trusted: bool = False,
    ):
        """
        Args:
            server_name: Name of the MCP server providing this tool.
            mcp_tool: MCP tool definition from the server.
            call_fn: Callable(server_name, tool_name, arguments) -> str.
            trusted: If True, skip confirmation.
        """
        self._server_name = server_name
        self._mcp_tool = mcp_tool
        self._call_fn = call_fn
        self._trusted = trusted
        self._fqn = make_mcp_tool_name(server_name, mcp_tool.name)

    @property
    def name(self) -> str:
        return self._fqn

    @property
    def description(self) -> str:
        desc = self._mcp_tool.description or f"MCP tool from {self._server_name}"
        return f"[MCP:{self._server_name}] {desc}"

    @property
    def parameters(self) -> Dict[str, Any]:
        schema = self._mcp_tool.inputSchema or {}
        # Ensure it's a valid JSON Schema object
        if not isinstance(schema, dict):
            return {"type": "object", "properties": {}}
        # The MCP SDK may return the schema as-is; ensure it has 'type'
        if "type" not in schema:
            schema = dict(schema)
            schema["type"] = "object"
        return schema

    @property
    def requires_confirmation(self) -> bool:
        return not self._trusted

    def execute(self, **kwargs) -> str:
        return self._call_fn(self._server_name, self._mcp_tool.name, kwargs)
