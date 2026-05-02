"""MCP tool wrapper that adapts MCP-discovered tools to the Agentao Tool interface."""

import re
from typing import Any, Dict, Optional

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
    """Wraps an MCP-discovered tool as a Agentao Tool."""

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
    def mcp_annotations(self) -> Dict[str, Any]:
        """Return the MCP tool annotations as a plain dict.

        ``ToolAnnotations`` is a Pydantic model in the MCP SDK; we
        flatten it so hosts and tests can introspect hints without
        depending on the SDK's internal types. Returns an empty dict
        when the server provided no annotations.
        """
        ann = getattr(self._mcp_tool, "annotations", None)
        if ann is None:
            return {}
        try:
            return ann.model_dump(exclude_none=True)
        except AttributeError:
            return dict(ann) if isinstance(ann, dict) else {}

    @property
    def is_read_only(self) -> bool:
        """Honor ``readOnlyHint`` only when the server is trusted.

        Per the MCP spec: clients must not make tool-use decisions
        based on annotations from untrusted servers — a malicious
        server could lie about being read-only.
        """
        if not self._trusted:
            return False
        return self.mcp_annotations.get("readOnlyHint") is True

    @property
    def requires_confirmation(self) -> bool:
        """Apply trust hints with a security-positive bias.

        - Untrusted server: confirm (ignore hints; spec says so).
        - Trusted server with ``destructiveHint=True``: confirm anyway
          — the server itself flagged the call as destructive, so the
          ``trusted=True`` blanket ought to step aside for that op.
        - Trusted server otherwise: skip confirmation (current
          behavior). ``readOnlyHint`` does not need to suppress
          confirmation here because trusted servers already do.
        """
        if not self._trusted:
            return True
        if self.mcp_annotations.get("destructiveHint") is True:
            return True
        return False

    def execute(self, **kwargs) -> str:
        return self._call_fn(self._server_name, self._mcp_tool.name, kwargs)
