"""The three model-callable MCP resource tools (docs/design/mcp-resources.md §5.2).

Names, parameters and listing shape are codex's and pi's, so a model trained
on either uses them unchanged. They live beside ``McpTool`` rather than in
``agentao/tools/``: they wrap the MCP manager, and ``tools`` importing ``mcp``
would close an import cycle (``mcp/tool.py`` already imports ``tools.base``).
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, Optional

from ..tools.base import Tool
from .resources import (
    McpResourceError,
    list_everywhere,
    render_read,
    resource_item,
    saver_for,
    template_item,
    visible_resources,
    visible_templates,
)

#: Every name these tools register under. Accepted by ``disable_tools`` and
#: ``enabled_tools`` validation beside ``BUILTIN_TOOL_NAMES``.
LIST_RESOURCES = "list_mcp_resources"
LIST_TEMPLATES = "list_mcp_resource_templates"
READ_RESOURCE = "read_mcp_resource"
MCP_RESOURCE_TOOL_NAMES = frozenset({LIST_RESOURCES, LIST_TEMPLATES, READ_RESOURCE})

_SERVER_PARAM = {
    "type": "string",
    "description": "MCP server name. Omit to list every server's resources.",
}
_TEMPLATE_SERVER_PARAM = {
    "type": "string",
    "description": "MCP server name. Omit it to list the templates of every server.",
}
_CURSOR_PARAM = {
    "type": "string",
    "description": "Pagination cursor from a previous call's nextCursor. Requires server.",
}


class _McpResourceTool(Tool):
    """Shared shape: read-only, no confirmation, bound to one manager."""

    def __init__(self, manager: Any):
        super().__init__()
        self._manager = manager

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def requires_confirmation(self) -> bool:
        # A read has no side effect to confirm, whatever the server's ``trust``;
        # its content enters the model as an ordinary untrusted tool result. A
        # permission rule naming the tool still applies (D3).
        return False


class _ListTool(_McpResourceTool):
    _templates = False

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "server": _TEMPLATE_SERVER_PARAM if self._templates else _SERVER_PARAM,
                "cursor": _CURSOR_PARAM,
            },
        }

    def execute(self, server: Optional[str] = None, cursor: Optional[str] = None, **_: Any) -> str:
        key = "resourceTemplates" if self._templates else "resources"
        if server is None or server == "":
            if cursor is not None:
                return (
                    "Error: cursor requires server — there is no cursor across servers. "
                    "Pass the server the cursor came from."
                )
            items, errors = list_everywhere(self._manager, templates=self._templates)
            payload: Dict[str, Any] = {key: self._render_items(items)}
            if errors:
                payload["errors"] = errors
            return json.dumps(payload, ensure_ascii=False)
        try:
            if self._templates:
                page = self._manager.list_resource_templates(server, cursor)
                items, next_cursor = page.templates, page.next_cursor
            else:
                page = self._manager.list_resources(server, cursor)
                items, next_cursor = page.resources, page.next_cursor
        except McpResourceError as e:
            return f"Error: {e}"
        payload = {"server": server, key: self._render_items(items)}
        if next_cursor is not None:
            payload["nextCursor"] = next_cursor
        return json.dumps(payload, ensure_ascii=False)

    def _render_items(self, items: list) -> list:
        if self._templates:
            return [template_item(i) for i in visible_templates(items)]
        return [resource_item(i) for i in visible_resources(items)]


class ListMcpResourcesTool(_ListTool):
    @property
    def name(self) -> str:
        return LIST_RESOURCES

    @property
    def description(self) -> str:
        return (
            "List resources (files, documents, data) that connected MCP servers expose. "
            "With server, returns one page (continue with cursor); without, every "
            "server's resources. Each item names its server; read one with "
            "read_mcp_resource."
        )


class ListMcpResourceTemplatesTool(_ListTool):
    _templates = True

    @property
    def name(self) -> str:
        return LIST_TEMPLATES

    @property
    def description(self) -> str:
        return (
            "List parameterized resource templates (RFC 6570 URI templates) that "
            "connected MCP servers expose. Expand a template into a URI and read it "
            "with read_mcp_resource."
        )


class ReadMcpResourceTool(_McpResourceTool):
    #: The session's ``McpSkills``, set when a Skills server is connected
    #: (docs/design/mcp-skills.md §6.1). ``None``: every read is ordinary.
    skill_session: Any = None
    #: The conversation generation of ``skill_session`` this instance reads;
    #: a sub-agent's copy is bound to its own (:meth:`for_view`).
    skill_view: Optional[int] = None

    def for_view(self, view: Optional[int]) -> "ReadMcpResourceTool":
        clone = copy.copy(self)
        clone.skill_view = view
        return clone

    @property
    def name(self) -> str:
        return READ_RESOURCE

    @property
    def description(self) -> str:
        return (
            "Read a resource from an MCP server by URI. The server is required — "
            "use the server an item was listed under, or the one a tool result's "
            "resource link names. Binary content is saved to a file whose path is "
            "returned."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "MCP server name."},
                "uri": {"type": "string", "description": "Resource URI to read."},
            },
            "required": ["server", "uri"],
        }

    def execute(self, server: str = "", uri: str = "", **_: Any) -> str:
        if not server or not uri:
            return "Error: read_mcp_resource requires both server and uri."
        try:
            # One routing decision for both the read and its rendering: a
            # second look could disagree (a concurrent load or ``/clear``)
            # and render verified skill content as an ordinary, saved read.
            read, is_skill = self._resolve(server, uri)
        except McpResourceError as e:
            return f"Error: {e}"
        if not is_skill:
            rendered = render_read(read, saver_for(self.working_directory))
            from ..skills.provenance import is_skill_result

            if is_skill_result(READ_RESOURCE, rendered):
                # Server text shaped like the wrapper only this tool writes for
                # verified skill content: it is not provenance. Led by a line
                # of ours so compaction, a restore and the spill path do not
                # read it as a loaded skill's content.
                rendered = f"[Resource {uri} from MCP server '{server}']\n" + rendered
            return rendered
        # A loaded skill's content: this tool never saves it as a file (a saved
        # copy would outlive the approval, readable with ``read_file`` and no
        # gate), and it is marked so a session restore withholds it.
        from .skills import wrap_skill_file

        return wrap_skill_file(server, uri, render_read(read, _refuse_to_save))

    def resolve_read(self, server: str, uri: str):
        """The one function every read goes through, before any request.

        The Skills extension (docs/design/mcp-skills.md §6.1) routes a read
        inside the directory of a skill loaded from ``server`` to the skill's
        verifier here: a manifest file comes back verified, an unlisted one
        is refused before any request. Every other read is ordinary — a raw
        read of a SKILL.md loads nothing.
        """
        return self._resolve(server, uri)[0]

    def _resolve(self, server: str, uri: str):
        """``(read, is_skill_read)`` — :meth:`resolve_read` and how it routed."""
        session = self.skill_session
        if session is not None and self._manager.resources_allowed(server):
            # Config first, as for every resource call: ``"resources": false``
            # refuses the generic tool even for a loaded skill's files, which
            # stay readable through ``read_skill_file``.
            verified = session.generic_read(server, uri, self.skill_view)
            if verified is not None:
                return verified(), True
        return self._manager.read_resource(server, uri), False


def _refuse_to_save(_data: bytes, _uri: str, _mime: Any) -> str:
    raise OSError("MCP skill content is not saved to disk; read it with read_skill_file")


def resource_tools(manager: Any) -> list:
    return [
        ListMcpResourcesTool(manager),
        ListMcpResourceTemplatesTool(manager),
        ReadMcpResourceTool(manager),
    ]
