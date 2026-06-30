"""Shared fakes for MCP client tests.

Consolidates the CONNECTED-``McpClient``-with-injected-fake-session builder and
the ``asyncio.run`` shim that several MCP test modules used to re-declare
locally. New MCP tests should import from here so a future change to
``ClientSession.call_tool``'s signature is a one-line edit, not an N-file sweep.
"""

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from agentao.mcp.client import McpClient, ServerStatus


def run_async(coro):
    """Run an awaitable to completion (test entry point)."""
    return asyncio.run(coro)


def text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def image_block(mime: str = "image/png") -> SimpleNamespace:
    return SimpleNamespace(type="image", mimeType=mime)


def tool_result(content: List[Any], structured: Any = None, is_error: bool = False) -> SimpleNamespace:
    """A stand-in for the SDK's ``CallToolResult`` (content / structuredContent / isError)."""
    return SimpleNamespace(content=content, structuredContent=structured, isError=is_error)


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
