"""Shared fakes for MCP client tests.

Consolidates the CONNECTED-``McpClient``-with-injected-fake-session builder and
the ``asyncio.run`` shim that several MCP test modules used to re-declare
locally. New MCP tests should import from here so a future change to
``ClientSession.call_tool``'s signature is a one-line edit, not an N-file sweep.

The *session* is faked; the **result objects are real** ``mcp.types`` models.
That distinction is load-bearing: these helpers used to hand back
``SimpleNamespace(content=…, structuredContent=…, isError=…)``, which restated
agentao's assumption about the wire shape instead of testing it — so when mcp
2.0 renamed every field to snake_case, all of these tests stayed green while
every real MCP call raised ``AttributeError``. Building from the real models
means the next SDK rename fails the suite instead of production. Construction
still uses the camelCase spec names, which are field names on 1.x and aliases
on 2.x, so one call site works on both majors.
"""

import asyncio
from typing import Any, Dict, List, Optional

from mcp.types import CallToolResult, ImageContent, TextContent

from agentao.mcp.client import McpClient, ServerStatus


def run_async(coro):
    """Run an awaitable to completion (test entry point)."""
    return asyncio.run(coro)


def text_block(text: str) -> TextContent:
    return TextContent(type="text", text=text)


def image_block(mime: str = "image/png") -> ImageContent:
    # ``data`` is required by the model; content is irrelevant to these tests,
    # which only assert the rendered "[image: <mime>]" placeholder.
    return ImageContent(type="image", data="AA==", mimeType=mime)


def tool_result(content: List[Any], structured: Any = None, is_error: bool = False) -> CallToolResult:
    """A real ``CallToolResult`` — see the module docstring on why it is not a fake."""
    return CallToolResult(content=content, structuredContent=structured, isError=is_error)


def connected_client(
    result: Any,
    *,
    config: Optional[Dict[str, Any]] = None,
    capture: Optional[Dict[str, Any]] = None,
) -> McpClient:
    """Build a CONNECTED ``McpClient`` whose session returns ``result``.

    ``capture`` (when given) records the ``read_timeout_seconds`` each
    ``call_tool`` was invoked with, so timeout-passthrough is assertable.
    """
    client = McpClient("svr", config or {"command": "echo"})
    client.status = ServerStatus.CONNECTED

    class _Session:
        async def call_tool(self, tool_name, arguments, read_timeout_seconds=None):
            if capture is not None:
                capture["read_timeout_seconds"] = read_timeout_seconds
            return result

    client._session = _Session()
    return client
