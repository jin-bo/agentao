"""MCP client and client manager for connecting to MCP servers."""

import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from datetime import timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from mcp.types import Tool as McpToolDef

from .config import McpServerConfig, resolve_timeouts

logger = logging.getLogger("agentao.mcp")


class McpErrorKind(str, Enum):
    """Outcome of classifying an exception raised by ``ClientSession.call_tool``.

    Drives the retry policy in :meth:`McpClient.call_tool`:
    ``AUTH`` surfaces immediately (creds won't change on retry);
    ``SESSION_EXPIRED`` and ``TRANSPORT_DROPPED`` reconnect-and-retry once;
    ``OTHER`` surfaces without reconnecting.
    """

    AUTH = "auth"
    SESSION_EXPIRED = "session_expired"
    TRANSPORT_DROPPED = "transport_dropped"
    OTHER = "other"


# Each entry: (kind, markers, match_type_name).
# Order matters: AUTH wins over session/transport because a server can
# stuff multiple signals into one message (e.g. ``401 Unauthorized: session
# expired``) and retrying with the same credentials only produces another
# 401/403 and a noisy reconnect storm. ``connection refused`` is
# intentionally absent from TRANSPORT_DROPPED: that means the server isn't
# listening at all, so a reconnect would fail the same way.
_ERROR_RULES: Tuple[Tuple[McpErrorKind, Tuple[str, ...], bool], ...] = (
    (
        McpErrorKind.AUTH,
        ("401", "403", "unauthorized", "forbidden"),
        False,
    ),
    (
        McpErrorKind.SESSION_EXPIRED,
        (
            "session expired",
            "session not found",
            "unknown session",
            "session terminated",
        ),
        False,
    ),
    (
        McpErrorKind.TRANSPORT_DROPPED,
        (
            # anyio resource errors (matched on the type name; str() may be empty)
            "closedresourceerror",
            "brokenresourceerror",
            "endofstream",
            # httpx remote-disconnect (SSE transports)
            "remoteprotocolerror",
            # Common stringified disconnects across stdlib + httpx + httpcore
            "connection reset",
            "connection closed",
            "closed connection",       # "peer closed connection without ..."
            "connection was closed",   # alternate phrasing
            "connection aborted",
            "server disconnected",
            "broken pipe",
            "transport closed",
            "stream closed",
        ),
        True,
    ),
)


def classify_mcp_error(exc: Exception) -> McpErrorKind:
    """Categorize an MCP call_tool exception for retry decisions.

    Some anyio types stringify to an empty body but their class name
    carries the signal, so transport markers are matched against both
    ``str(exc).lower()`` and ``type(exc).__name__.lower()``.
    """
    msg = str(exc).lower()
    type_name = type(exc).__name__.lower()
    haystack_with_type = f"{msg} {type_name}"
    for kind, markers, match_type_name in _ERROR_RULES:
        haystack = haystack_with_type if match_type_name else msg
        if any(marker in haystack for marker in markers):
            return kind
    return McpErrorKind.OTHER


class NonMcpEndpointError(ConnectionError):
    """A configured ``url`` resolves to something that is not an MCP endpoint.

    Raised by the connect-time content-type preflight when a 2xx response
    advertises a body type an MCP server never serves (typically ``text/html``
    — the URL points at a web page or login portal rather than a Streamable
    HTTP / SSE endpoint). Surfacing this fast turns an opaque ~60 s connect
    hang into an immediate, actionable error.
    """


# Allow-list of content types a real MCP Streamable-HTTP / SSE endpoint
# serves. The preflight rejects a 2xx response only when it advertises a
# *definite* type outside this set (text/html, text/plain, application/xml,
# …). A missing/empty content type, a non-2xx status, or any transport error
# passes through — the real handshake stays the source of truth for every
# case except the unambiguous "this is a web page, not MCP" one.
_MCP_CONTENT_TYPES = ("application/json", "text/event-stream")

# Preflight runs on its own short budget, independent of the (default 60 s)
# connect timeout it exists to short-circuit.
_PREFLIGHT_TIMEOUT_SECONDS = 5.0

# The MCP SDK's ``sse_client`` default for ``sse_read_timeout`` (the maximum
# silence between SSE events before the stream is dropped). We only ever raise
# it — never lower it — so a configured per-request budget above this default
# can actually run to completion over SSE, while small per-request budgets
# don't shorten the idle tolerance of the long-lived stream between calls.
_DEFAULT_SSE_READ_TIMEOUT = 300.0


class ServerStatus(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class McpClient:
    """Manages a single MCP server connection."""

    def __init__(self, name: str, config: McpServerConfig):
        self.name = name
        self.config = config
        self.status = ServerStatus.DISCONNECTED
        self.error_message: Optional[str] = None
        self._session: Optional[ClientSession] = None
        self._exit_stack: Optional[AsyncExitStack] = None
        self._tools: List[McpToolDef] = []

    @property
    def transport_type(self) -> str:
        if self.config.get("command"):
            return "stdio"
        if self.config.get("url"):
            return "sse"
        return "unknown"

    @property
    def tools(self) -> List[McpToolDef]:
        return self._tools

    @property
    def is_trusted(self) -> bool:
        return bool(self.config.get("trust", False))

    async def connect(self) -> None:
        """Connect to the MCP server and discover tools."""
        self.status = ServerStatus.CONNECTING
        self.error_message = None

        # Resolve once and thread down (avoids a second parse — and a second
        # malformed-config warning — inside ``_connect_sse``). ``startup``
        # bounds the whole connect: the SSE HTTP open (via ``sse_client(
        # timeout=)``) AND the post-transport handshake below.
        startup_timeout, request_timeout = resolve_timeouts(self.config)

        try:
            self._exit_stack = AsyncExitStack()
            await self._exit_stack.__aenter__()

            if self.config.get("command"):
                await self._connect_stdio()
            elif self.config.get("url"):
                await self._connect_sse(startup_timeout, request_timeout)
            else:
                raise ValueError(f"No transport configured for server '{self.name}' (need 'command' or 'url')")

            # Bound the initialize()/list_tools() handshake so a server that
            # opens the stream (or spawns) but never answers can't hang connect
            # forever — the SSE HTTP-open timeout doesn't cover these request
            # round-trips, and stdio has no transport-level connect bound at
            # all. ``wait_for`` is safe here: these are plain awaits on the
            # already-established session and enter no exit-stack context, so a
            # timeout cancellation never crosses an anyio cancel scope into the
            # transport cleanup that ``connect``'s ``except`` performs.
            try:
                self._tools = (
                    await asyncio.wait_for(self._handshake(), timeout=startup_timeout)
                ).tools
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"MCP server '{self.name}' did not complete the "
                    f"initialize/list_tools handshake within {startup_timeout:g}s"
                ) from None

            self.status = ServerStatus.CONNECTED
            logger.info(f"MCP server '{self.name}' connected via {self.transport_type}, {len(self._tools)} tools")

        except Exception as e:
            self.status = ServerStatus.ERROR
            self.error_message = str(e)
            logger.error(f"Failed to connect to MCP server '{self.name}': {e}")
            # Cleanup on failure
            if self._exit_stack:
                try:
                    await self._exit_stack.__aexit__(None, None, None)
                except Exception:
                    pass
                self._exit_stack = None

    async def _handshake(self):
        """Run the MCP ``initialize()`` + ``list_tools()`` round-trips.

        Factored out so :meth:`connect` can wrap the whole handshake in a
        single ``startup`` budget via ``asyncio.wait_for``.
        """
        await self._session.initialize()
        return await self._session.list_tools()

    async def _connect_stdio(self) -> None:
        """Establish stdio transport."""
        command = self.config["command"]
        args = self.config.get("args", [])

        # Build environment: sanitized base + explicit env vars
        env = dict(os.environ)
        if self.config.get("env"):
            env.update(self.config["env"])

        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=env,
            cwd=self.config.get("cwd"),
        )

        stdio_transport = await self._exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        read_stream, write_stream = stdio_transport
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )

    async def _preflight_content_type(self, url: str, headers: Dict[str, str]) -> None:
        """Probe *url* for an MCP-shaped response before the SDK connects.

        A misconfigured ``url`` pointed at a plain web app returns HTML; the
        MCP SDK then sits on the connection for the full ``timeout`` (default
        60 s) before surfacing an opaque error. A cheap, short-timeout probe
        catches that in ≤ :data:`_PREFLIGHT_TIMEOUT_SECONDS` and raises
        :class:`NonMcpEndpointError` with an actionable message.

        Detection is allow-list based (see :data:`_MCP_CONTENT_TYPES`); only a
        2xx response carrying a *definite* non-MCP content type is rejected.
        Everything else — missing/empty content type, non-2xx (auth challenges,
        transient errors), or any transport/DNS error — passes through, leaving
        the real handshake authoritative.

        Like the SSE handshake itself, this probe makes a direct outbound
        request and is not routed through ``PermissionEngine``; it adds no new
        egress beyond what connecting to ``url`` already entails.
        """
        try:
            import httpx
        except ImportError:  # pragma: no cover - httpx is a core dependency
            return

        client_kwargs = {
            "follow_redirects": True,
            "timeout": httpx.Timeout(_PREFLIGHT_TIMEOUT_SECONDS),
        }
        # Send an MCP-shaped Accept so a content-negotiating server returns its
        # real MCP body (and is allowed through) rather than a default HTML page
        # we would wrongly reject. A caller-supplied Accept wins.
        probe_headers = {"Accept": ", ".join(_MCP_CONTENT_TYPES)}
        probe_headers.update(headers or {})
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                # HEAD is cheapest; fall back to GET when the server doesn't
                # implement it (405 Method Not Allowed / 501 Not Implemented).
                resp = await client.head(url, headers=probe_headers)
                if resp.status_code in (405, 501):
                    resp = await client.get(url, headers=probe_headers)
        except (httpx.HTTPError, httpx.InvalidURL):
            return  # DNS / connect / timeout / bad-URL — let the SDK be authoritative.

        # Only judge successful responses; a 4xx/5xx may be an auth challenge
        # or a transient error the real handshake handles.
        if not (200 <= resp.status_code < 300):
            return

        ct_base = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if not ct_base or ct_base in _MCP_CONTENT_TYPES:
            return

        raise NonMcpEndpointError(
            f"MCP server '{self.name}' at {url} returned Content-Type "
            f"'{ct_base}', not an MCP response (expected one of: "
            f"{', '.join(_MCP_CONTENT_TYPES)}). The URL most likely points at "
            "a web page rather than an MCP endpoint — check it resolves to an "
            "SSE / Streamable HTTP endpoint (e.g. https://host/mcp, not "
            "https://host/)."
        )

    async def _connect_sse(self, startup_timeout: float, request_timeout: Optional[float]) -> None:
        """Establish SSE transport.

        ``startup_timeout`` / ``request_timeout`` are pre-resolved by
        :meth:`connect` (see :func:`resolve_timeouts`).
        """
        url = self.config["url"]
        headers = self.config.get("headers", {})
        # ``startup`` bounds the HTTP connection open. ``sse_read_timeout`` is
        # the max silence between SSE events before the stream drops; the SDK
        # default (300 s) would otherwise cap any per-request budget at ~300 s,
        # so raise it to cover a larger ``request`` (never lower it — see
        # _DEFAULT_SSE_READ_TIMEOUT). The per-request deadline itself is applied
        # in ``call_tool`` via ``read_timeout_seconds``.
        sse_read_timeout = (
            request_timeout
            if request_timeout is not None and request_timeout > _DEFAULT_SSE_READ_TIMEOUT
            else _DEFAULT_SSE_READ_TIMEOUT
        )

        # Fail fast on a URL that points at a web page rather than an MCP
        # endpoint, instead of waiting out the full startup timeout.
        await self._preflight_content_type(url, headers)

        sse_transport = await self._exit_stack.enter_async_context(
            sse_client(
                url,
                headers=headers,
                timeout=startup_timeout,
                sse_read_timeout=sse_read_timeout,
            )
        )
        read_stream, write_stream = sse_transport
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Call a tool on this server and return the result as text.

        Retry policy is driven by :func:`classify_mcp_error`:
        ``AUTH`` surfaces immediately (retrying with the same credentials
        only produces another 401/403); ``SESSION_EXPIRED`` and
        ``TRANSPORT_DROPPED`` reconnect-and-retry once; ``OTHER``
        surfaces without reconnecting.

        A configured per-request ``timeout.request`` (see
        :func:`resolve_timeouts`) bounds each individual tool call; when
        unset the call is unbounded (the MCP SDK default).
        """
        _startup_timeout, request_timeout = resolve_timeouts(self.config)
        read_timeout = (
            timedelta(seconds=request_timeout) if request_timeout is not None else None
        )
        for attempt in range(2):
            if not self._session or self.status != ServerStatus.CONNECTED:
                try:
                    logger.info(f"MCP '{self.name}': reconnecting (attempt {attempt + 1})...")
                    await self.connect()
                except Exception as e:
                    return f"MCP connection error for '{self.name}': {e}"

            try:
                result = await self._session.call_tool(
                    tool_name, arguments, read_timeout_seconds=read_timeout
                )
            except Exception as e:
                kind = classify_mcp_error(e)
                if kind is McpErrorKind.AUTH:
                    return f"MCP auth error: {e}"
                if attempt == 0 and kind in (
                    McpErrorKind.SESSION_EXPIRED,
                    McpErrorKind.TRANSPORT_DROPPED,
                ):
                    logger.warning(
                        f"MCP '{self.name}' transient {type(e).__name__}, "
                        f"retrying after reconnect: {e}"
                    )
                    # Tear down the live transport before reconnecting;
                    # otherwise connect() overwrites _exit_stack and the
                    # old subprocess / SSE stream leaks for the lifetime
                    # of this manager.
                    await self.disconnect()
                    continue
                return f"MCP tool error: {e}"

            # Convert result content to text
            parts = []
            for block in result.content:
                if block.type == "text":
                    parts.append(block.text)
                elif block.type == "image":
                    parts.append(f"[image: {block.mimeType}]")
                elif block.type == "resource":
                    text = getattr(block.resource, "text", None)
                    if text:
                        parts.append(text)
                    else:
                        parts.append(f"[resource: {getattr(block.resource, 'uri', 'unknown')}]")
                else:
                    parts.append(f"[{block.type}]")

            # Fall back to structured output only when there are no content
            # blocks at all. A spec-compliant server returns both ``content``
            # (text/image, for the model) and ``structuredContent`` (JSON);
            # we keep the content in that case and never clobber it. But a
            # server that returns *only* ``structuredContent`` (content == [])
            # would otherwise hand the model an empty string — so serialize
            # the structured payload instead of dropping it.
            if not result.content and result.structuredContent is not None:
                # ensure_ascii=False keeps CJK/emoji readable (codebase-wide
                # convention); default=str makes a non-JSON-native value
                # degrade to its repr instead of raising out of call_tool.
                parts.append(
                    json.dumps(result.structuredContent, ensure_ascii=False, default=str)
                )

            text = "\n".join(parts)

            if result.isError:
                return f"MCP tool error: {text}"
            return text

        return "MCP tool error: failed after reconnect attempt"

    async def disconnect(self) -> None:
        """Disconnect from the server."""
        if self._exit_stack:
            try:
                await self._exit_stack.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"Error disconnecting from MCP server '{self.name}': {e}")
            self._exit_stack = None
        self._session = None
        self._tools = []
        self.status = ServerStatus.DISCONNECTED


class McpClientManager:
    """Manages multiple MCP server connections with sync-async bridge."""

    def __init__(self, server_configs: Dict[str, McpServerConfig]):
        self._configs = server_configs
        self._clients: Dict[str, McpClient] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """Get or create a dedicated event loop for MCP operations."""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _run(self, coro):
        """Run an async coroutine synchronously."""
        loop = self._get_loop()
        return loop.run_until_complete(coro)

    @property
    def clients(self) -> Dict[str, McpClient]:
        return self._clients

    @property
    def server_configs(self) -> Dict[str, McpServerConfig]:
        return self._configs

    def connect_all(self) -> None:
        """Connect to all configured MCP servers."""
        if not self._configs:
            return
        self._run(self._connect_all_async())

    async def _connect_all_async(self) -> None:
        """Connect to all servers concurrently."""
        async def _connect_one(name: str, config: McpServerConfig) -> None:
            client = McpClient(name, config)
            self._clients[name] = client
            try:
                await client.connect()
            except Exception as e:
                logger.error(f"Failed to start MCP server '{name}': {e}")

        await asyncio.gather(
            *[_connect_one(name, cfg) for name, cfg in self._configs.items()],
            return_exceptions=True,
        )

    def get_client(self, name: str) -> Optional[McpClient]:
        return self._clients.get(name)

    def get_all_tools(self) -> List[Tuple[str, McpToolDef]]:
        """Get all tools from all connected servers.

        Returns:
            List of (server_name, tool_definition) tuples.
        """
        tools = []
        for name, client in self._clients.items():
            if client.status == ServerStatus.CONNECTED:
                for tool in client.tools:
                    tools.append((name, tool))
        return tools

    def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Call a tool on a specific server (sync wrapper)."""
        client = self._clients.get(server_name)
        if not client:
            raise RuntimeError(f"MCP server '{server_name}' not found")
        return self._run(client.call_tool(tool_name, arguments))

    def disconnect_all(self) -> None:
        """Disconnect from all servers."""
        if self._clients:
            self._run(self._disconnect_all_async())
        if self._loop and not self._loop.is_closed():
            self._loop.close()
            self._loop = None

    async def _disconnect_all_async(self) -> None:
        for client in self._clients.values():
            await client.disconnect()
        self._clients.clear()

    def get_server_status(self) -> List[Dict[str, Any]]:
        """Get status summary of all servers."""
        result = []
        for name, client in self._clients.items():
            result.append({
                "name": name,
                "status": client.status.value,
                "transport": client.transport_type,
                "tools": len(client.tools),
                "trusted": client.is_trusted,
                "error": client.error_message,
            })
        return result
