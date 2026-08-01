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

from mcp.types import (
    CallToolResult,
    ImageContent,
    Implementation,
    InitializeResult,
    ServerCapabilities,
    TextContent,
)

from agentao.mcp.client import McpClient, ServerStatus

#: Protocol version the shared handshake fake reports. Deliberately *not* the
#: newest one: a fake echoing back the client's own offer would still pass a
#: test against a client that never read the server's answer.
FAKE_PROTOCOL_VERSION = "2025-06-18"


def run_async(coro):
    """Run an awaitable to completion (test entry point)."""
    return asyncio.run(coro)


def initialize_result(version: str = FAKE_PROTOCOL_VERSION) -> InitializeResult:
    """A real ``InitializeResult`` for a fake session's ``initialize()``.

    Fakes must return this rather than ``None`` — ``McpClient`` reads the
    negotiated protocol version straight off the result, so a ``None`` fake
    asserts a contract the SDK does not have.
    """
    return InitializeResult(
        protocolVersion=version,
        capabilities=ServerCapabilities(),
        serverInfo=Implementation(name="fake-server", version="1.0"),
    )


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

    ``capture`` (when given) records every keyword ``call_tool`` was invoked
    with — ``read_timeout_seconds``, and on mcp 2.x ``allow_input_required`` —
    so argument passthrough is assertable.
    """
    client = McpClient("svr", config or {"command": "echo"})
    client.status = ServerStatus.CONNECTED

    class _Session:
        # ``**kwargs``, not a fixed signature: agentao passes era-dependent
        # keywords (``allow_input_required`` exists only on 2.x), and a fake
        # that rejected them would fail for a reason the real SDK never would.
        async def call_tool(self, tool_name, arguments, read_timeout_seconds=None, **kwargs):
            if capture is not None:
                capture["read_timeout_seconds"] = read_timeout_seconds
                capture.update(kwargs)
            return result

    client._session = _Session()
    return client
