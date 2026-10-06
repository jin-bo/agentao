"""MCP tool wrapper that adapts MCP-discovered tools to the Agentao Tool interface."""

import copy
import hashlib
import re
from typing import Any, Dict, Optional

from mcp.types import Tool as McpToolDef

from ..tools.base import Tool
from ._compat import annotations_dict, field
from .resources import render_call_result, saver_for

# Characters allowed in tool names (OpenAI function calling)
_INVALID_CHARS_RE = re.compile(r"[^a-zA-Z0-9_]")


def _sanitize_name(name: str) -> str:
    """Replace invalid characters with underscores."""
    return _INVALID_CHARS_RE.sub("_", name)


# OpenAI's Chat Completions refuses a function name longer than 64 characters,
# and the tools array goes out on every request — one long name fails every turn.
MAX_TOOL_NAME_LEN = 64
_HASH_LEN = 8


def _name_hash(server_name: str, tool_name: str) -> str:
    # From the *original* names, so the suffix is the same on every connect and
    # in every process — never from discovery order, or a permission rule
    # naming the tool would match a different tool after a reconnect.
    digest = hashlib.sha256(f"{server_name}\0{tool_name}".encode("utf-8"))
    return digest.hexdigest()[:_HASH_LEN]


def make_mcp_tool_name(server_name: str, tool_name: str) -> str:
    """Create a fully qualified MCP tool name: mcp_{server}_{tool}.

    A name that fits and is ASCII comes back exactly as it always did, so
    existing permission rules keep matching. Two cases get a stable hash of
    the original names appended instead:

    * the name is longer than :data:`MAX_TOOL_NAME_LEN` — it is cut to make
      room for the suffix;
    * a non-ASCII character was replaced — ``查询`` and ``搜索`` would
      otherwise both become ``__`` and collide.
    """
    name = f"mcp_{_sanitize_name(server_name)}_{_sanitize_name(tool_name)}"
    lossy = not (server_name.isascii() and tool_name.isascii())
    if len(name) <= MAX_TOOL_NAME_LEN and not lossy:
        return name
    suffix = f"_{_name_hash(server_name, tool_name)}"
    return name[: MAX_TOOL_NAME_LEN - len(suffix)] + suffix


def parse_mcp_tool_name(fqn: str) -> tuple:
    """Parse 'mcp_{server}_{tool}' back to (server_name, tool_name).

    Uses the first underscore after 'mcp_' as the separator between
    server name and tool name. Best effort only: a hashed name from
    :func:`make_mcp_tool_name` does not round-trip, and calls never rely on
    this — :class:`McpTool` keeps the original names.
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
        *,
        result_fn=None,
        read_hint_fn=None,
    ):
        """
        Args:
            server_name: Name of the MCP server providing this tool.
            mcp_tool: MCP tool definition from the server.
            call_fn: Callable(server_name, tool_name, arguments) -> str.
            trusted: If True, skip confirmation.
            result_fn: Callable(server_name, tool_name, arguments) returning
                the whole ``CallToolResult`` (or an error string). When given,
                it is used instead of ``call_fn`` and the result is rendered
                here, so an embedded binary resource is saved under this
                tool's working directory (docs/design/mcp-resources.md §6).
            read_hint_fn: Callable(server_name) -> bool, asked at render time
                whether a ``resource_link`` may point at ``read_mcp_resource``.
        """
        super().__init__()
        self._server_name = server_name
        self._mcp_tool = mcp_tool
        self._call_fn = call_fn
        self._trusted = trusted
        self._result_fn = result_fn
        self._read_hint_fn = read_hint_fn
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
        schema = field(self._mcp_tool, "inputSchema", "input_schema") or {}
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
        """Return the server-supplied ``ToolAnnotations`` as a plain
        dict so hosts can introspect hints without importing MCP SDK
        types. Empty dict when the server provided no annotations.

        Keys are the camelCase names from the MCP spec on both SDK majors
        (mcp 2.0 renamed the Python attributes to snake_case) — see
        :func:`._compat.annotations_dict`.
        """
        return annotations_dict(self._mcp_tool.annotations)

    @property
    def is_read_only(self) -> bool:
        # Per MCP spec: never honor annotations from an untrusted server.
        # destructiveHint=true overrides readOnlyHint when a server sends
        # both — a contradictory pair must not let the call slip through
        # the read-only gate. Security-positive: assume destructive in doubt.
        if not self._trusted:
            return False
        ann = self.mcp_annotations
        return (
            ann.get("readOnlyHint") is True
            and ann.get("destructiveHint") is not True
        )

    @property
    def requires_confirmation(self) -> bool:
        # Untrusted: always confirm (annotations ignored). Trusted: skip
        # confirmation unless the server itself flagged the op destructive.
        if not self._trusted:
            return True
        return self.mcp_annotations.get("destructiveHint") is True

    def without_read_hint(self) -> "McpTool":
        """A copy that never points a resource link at ``read_mcp_resource``.

        For a sub-agent that does not get that tool: it otherwise shares the
        parent's instance, whose hint is checked against the *parent's*
        registry. The copy still calls through the parent's manager, so it
        opens no connection of its own.
        """
        clone = copy.copy(self)
        clone._read_hint_fn = None
        return clone

    def execute(self, **kwargs) -> str:
        if self._result_fn is None:
            return self._call_fn(self._server_name, self._mcp_tool.name, kwargs)
        result = self._result_fn(self._server_name, self._mcp_tool.name, kwargs)
        if isinstance(result, str):
            return result
        hint = None
        if self._read_hint_fn is not None and self._read_hint_fn(self._server_name) is True:
            hint = self._server_name
        return render_call_result(
            result, save=saver_for(self.working_directory), read_hint=hint
        )
