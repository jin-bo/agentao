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
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import mcp.types as mcp_types
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams
from mcp.shared.message import SessionMessage
from mcp.types import ErrorData, JSONRPCError, JSONRPCResponse
from mcp.types import (
    CallToolResult,
    ImageContent,
    Implementation,
    InitializeResult,
    ListToolsResult,
    ServerCapabilities,
    TextContent,
    Tool,
)

from agentao.mcp._compat import field
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


def tool_def(name: str) -> Tool:
    """A real ``mcp.types.Tool``."""
    return Tool(name=name, description=f"tool {name}", inputSchema={"type": "object"})


def tools_result(names: List[str], next_cursor: Optional[str] = None) -> ListToolsResult:
    """A real ``ListToolsResult`` page for a fake session's ``list_tools()``.

    Never hand a ``MagicMock`` here. ``McpClient`` reads the cursor through
    ``_compat.field``, whose tail case is a ``hasattr`` probe — and a mock
    answers ``hasattr`` for *any* name, so it would yield a truthy mock cursor
    and send the pagination loop somewhere no real server could. Constructed
    with the camelCase ``nextCursor``, a field name on 1.x and an alias on 2.x,
    so one call site works on both majors.
    """
    return ListToolsResult(tools=[tool_def(n) for n in names], nextCursor=next_cursor)


def paging_session(pages: List[ListToolsResult], calls: Optional[List[Any]] = None):
    """A fake paginating server: page N+1 is served only for page N's cursor.

    **Cursor-driven, not call-counted.** Each page is keyed by the cursor that
    must be presented to obtain it — ``None`` for the first, and thereafter the
    ``nextCursor`` the previous page advertised. Presenting any other cursor
    raises, so a client that drops, blanks or reorders a cursor fails here
    instead of being handed the next page anyway.

    That distinction is the whole value of the helper. An index-counting fake
    serves page 2 no matter what the client asked for, which makes it blind to
    the ``if cursor`` truthiness bug — the very defect these tests exist to
    catch — leaving only an explicit ``calls`` assertion to notice. Keying on
    the cursor means every pagination test gets that check for free.

    ``calls`` (when given) additionally collects the ``params`` of every call,
    so a test can assert page 1 went out as ``params=None`` — the property that
    keeps single-page servers byte-identical to the pre-pagination request.
    """
    by_cursor = {}
    expected: Optional[str] = None
    for page in pages:
        by_cursor[expected] = page
        # ``field``, not ``hasattr``: 1.x spells it ``nextCursor``, 2.x
        # ``next_cursor``. Same shim the client reads it through.
        expected = field(page, "nextCursor", "next_cursor")

    class _Session:
        async def initialize(self):
            return initialize_result()

        async def list_tools(self, *, params=None):
            if calls is not None:
                calls.append(params)
            cursor = None if params is None else params.cursor
            if cursor not in by_cursor:
                raise AssertionError(
                    f"fake server got cursor {cursor!r}, which no page in this "
                    f"fixture is keyed by (known: {sorted(map(repr, by_cursor))})"
                )
            return by_cursor[cursor]

    return _Session()


# ---------------------------------------------------------------------------
# Real-wire harness: a JSON-RPC peer behind a genuine ``ClientSession``.
#
# Hoisted here from ``test_mcp_protocol_negotiation.py`` so pagination tests can
# reach it too. A faked *session* cannot pin anything about the SDK's own
# request-building — the `list_tools(params=…)` → `{"cursor": …}` mapping, or
# which spelling of the cursor field the installed major emits — because the
# fake's signature is whatever the test author wrote. Only a real session over a
# real transport can fail when that contract shifts between SDK majors.
# ---------------------------------------------------------------------------


def wrap_message(message: Any) -> SessionMessage:
    """Put a concrete JSON-RPC model into whatever ``SessionMessage`` takes.

    1.x wraps every message in the ``JSONRPCMessage`` RootModel; 2.0 made
    ``SessionMessage.message`` a plain union and demoted ``JSONRPCMessage`` to a
    bare type alias, which is not callable. Probed by attempting the call, not
    by a version check.
    """
    try:
        return SessionMessage(mcp_types.JSONRPCMessage(message))
    except TypeError:
        return SessionMessage(message)


def unwrap_message(session_message: SessionMessage) -> Any:
    """The concrete JSON-RPC model inside a received ``SessionMessage``."""
    message = session_message.message
    return getattr(message, "root", message)


class FakeMcpServer:
    """A JSON-RPC peer for the far end of a real ``ClientSession``.

    ``handlers`` maps a method name to its reply — a result ``dict``, an
    ``ErrorData``, or a **callable** taking the request's ``params`` dict and
    returning either. The callable form is what lets a handler answer
    ``tools/list`` differently per cursor. Unmapped methods answer ``-32601``,
    which is what a spec-compliant handshake-era server does with
    ``server/discover``.
    """

    def __init__(self, handlers: Dict[str, Any]):
        self._handlers = handlers
        self.methods: List[str] = []  # every method that crossed the wire, in order
        self.params: List[Any] = []  # the params of each, positionally aligned

    async def serve(self, read_stream, write_stream) -> None:
        async for item in read_stream:
            if isinstance(item, Exception):
                continue
            message = unwrap_message(item)
            method = getattr(message, "method", None)
            request_id = getattr(message, "id", None)
            if method is None or request_id is None:
                continue  # a notification (``initialized``) — nothing to answer
            params = getattr(message, "params", None)
            self.methods.append(method)
            self.params.append(params)

            reply = self._handlers.get(
                method,
                ErrorData(code=-32601, message=f"Unknown method {method}"),
            )
            if callable(reply):
                reply = reply(params)
            if isinstance(reply, ErrorData):
                out: Any = JSONRPCError(jsonrpc="2.0", id=request_id, error=reply)
            else:
                out = JSONRPCResponse(jsonrpc="2.0", id=request_id, result=reply)
            await write_stream.send(wrap_message(out))


@asynccontextmanager
async def client_against(server: FakeMcpServer):
    """An ``McpClient`` whose ``_session`` is a real ``ClientSession`` on *server*."""
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        serving = asyncio.ensure_future(server.serve(*server_streams))
        try:
            async with ClientSession(*client_streams) as session:
                client = McpClient("fake", {"command": "echo"})
                client._session = session
                yield client
        finally:
            serving.cancel()
            try:
                await serving
            except asyncio.CancelledError:
                pass


def handshake_result(version: str = FAKE_PROTOCOL_VERSION) -> Dict[str, Any]:
    """An ``initialize`` result as it appears **on the wire** — a dict.

    :func:`initialize_result` returns the model, for a fake *session*;
    :class:`FakeMcpServer` answers on the wire and needs the dumped form.
    Dumped from the real model so the keys are whatever the installed SDK
    validates, rather than a hand-typed guess at the wire shape.
    """
    return initialize_result(version).model_dump(
        by_alias=True, mode="json", exclude_none=True
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
