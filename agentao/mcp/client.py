"""MCP client and client manager for connecting to MCP servers."""

import asyncio
import concurrent.futures
import json
import logging
import os
import threading
import time
from contextlib import AsyncExitStack
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import (
    create_mcp_http_client,
    streamable_http_client,
)
from mcp.types import METHOD_NOT_FOUND, PaginatedRequestParams
from mcp.types import Tool as McpToolDef

from .. import __version__
from ..capabilities.process import build_child_env
from ._compat import (
    SUPPORTS_INPUT_REQUIRED,
    SUPPORTS_MODERN_ERA,
    UNSUPPORTED_PROTOCOL_VERSION,
    InputRequiredResult,
    McpProtocolError,
    UnexpectedClaimedResult,
    field,
    httpx_for_mcp,
    read_timeout as _sdk_read_timeout,
)
from .config import (
    McpServerConfig,
    McpTransportConfigError,
    resolve_timeouts,
    resolve_transport,
)

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


class McpCatalogError(ConnectionError):
    """A server's ``tools/list`` catalog violated one of the pagination bounds.

    Its own type is load-bearing for the same reason
    :class:`McpProtocolEraError`'s is: :meth:`connect` appends a "try
    ``type: sse``" hint to failures on an *inferred* http transport, and that
    hint is actively wrong here. Reaching a pagination bound means
    ``initialize`` and at least one ``tools/list`` already succeeded — the
    transport provably works, and the fault is the server's cursor or catalog
    size. Sending the operator to change transports earns them an identical
    failure and hides the real cause.
    """


class McpProtocolEraError(ConnectionError):
    """agentao and the server share no usable MCP protocol era.

    Raised by :meth:`McpClient._negotiate` when a ``-32022`` rejection of the
    legacy handshake cannot be resolved by escalating to ``server/discover`` —
    either because the installed SDK has no modern era (mcp 1.x) or because the
    escalation itself failed. Its own type is load-bearing: :meth:`connect`
    suppresses the "try ``type: sse``" hint for it, since no transport change
    can fix a protocol-version mismatch.
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

# Bounds on the ``tools/list`` pagination loop (see
# docs/design/mcp-tool-list-pagination.md §5.3). A paginating server is an
# untrusted peer driving a loop, so every one of these is a hard failure for
# that server rather than a truncation — a partial catalog is the exact silent
# wrongness the loop exists to remove.
#
# ``_MAX_TOOLS`` bounds the *accumulated catalog*, not the wire: by the time we
# see ``result.tools`` the SDK has already read, parsed and modelled the
# response, so a single oversized page has cost its memory regardless. Checking
# it before ``extend`` keeps that page out of the accumulator rather than
# copying it in and only then objecting.
_MAX_TOOL_PAGES = 100
_MAX_TOOLS = 1024
_MAX_CURSOR_BYTES = 64 * 1024

# Default ``User-Agent`` for URL-transport MCP requests. Without it every SSE /
# Streamable HTTP request (and the content-type preflight) goes out as the bare
# ``python-httpx`` UA, leaving agentao anonymous in server logs and tripping
# servers that filter or rate-limit unknown clients. The UA is informational
# only — not an auth/trust signal — and a user-configured ``User-Agent`` header
# still wins (see :func:`_with_default_user_agent`).
_MCP_USER_AGENT = f"agentao-mcp/{__version__}"


def _with_default_user_agent(headers: Optional[Dict[str, str]]) -> Dict[str, str]:
    """Return *headers* with :data:`_MCP_USER_AGENT` added when none is set.

    A user-supplied ``User-Agent`` — in *any* casing, since HTTP header names
    are case-insensitive — is preserved, so an explicit override wins. The input
    is copied, never mutated: it aliases ``self.config['headers']``, which is
    re-read on every (re)connect.
    """
    result = dict(headers or {})
    if not any(key.lower() == "user-agent" for key in result):
        result["User-Agent"] = _MCP_USER_AGENT
    return result


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
        self._protocol_version: Optional[str] = None
        # The task that owns the live connection and the event that tells it
        # to close (see ``connect``).
        self._owner: Optional["asyncio.Task[None]"] = None
        self._stop: Optional[asyncio.Event] = None
        # Set once the live connection starts to close, for any reason. Calls
        # wait on it alongside their request (see ``_call_on``).
        self._gone = asyncio.Event()
        # Serialises connection *switches* in ``call_tool``, never ordinary
        # requests: concurrent calls share one session. The attempt counter
        # lets a call that waited out someone else's reconnect use its result
        # instead of starting another.
        self._reconnect_lock = asyncio.Lock()
        self._connect_attempts = 0

    @property
    def transport_type(self) -> str:
        # Display-only (status, /mcp list) — must never raise. A fail-closed
        # config error (bad ``type`` / missing key) surfaces as "unknown"
        # here; the actionable message rides ``error_message`` from connect().
        try:
            return resolve_transport(self.config)
        except McpTransportConfigError:
            return "unknown"

    @property
    def tools(self) -> List[McpToolDef]:
        return self._tools

    @property
    def protocol_version(self) -> Optional[str]:
        """MCP protocol version negotiated with this server, or ``None``.

        ``None`` until :meth:`connect` completes its handshake, and again after
        :meth:`disconnect` — the version is a property of the live session, not
        of the config.

        Treat the value as a **ceiling, not a constant**: the server picks it,
        and it can be older than anything agentao offered. Gate features with a
        ``>=`` comparison, never equality.
        """
        return self._protocol_version

    @property
    def is_trusted(self) -> bool:
        return bool(self.config.get("trust", False))

    async def connect(self) -> None:
        """Connect to the MCP server and discover tools.

        Returns once the handshake has settled, connected or not; a failure
        reports through ``status`` / ``error_message`` rather than raising.

        The connection lives in its own **owner task** (#243). The SDK's
        transports and ``ClientSession`` hold anyio task groups, whose cancel
        scopes must be exited by the task that entered them. Entering them here
        and exiting them from ``disconnect()`` — a later call, so a different
        task — raised "Attempted to exit cancel scope in a different task" on
        every disconnect, and cleanup finished only because the SDK happened to
        order its ``finally`` first. The owner opens the connection, waits to be
        told to stop, and closes it, all in one task.
        """
        if self._owner is not None and not self._owner.done():
            await self._stop_owner()
        ready: "asyncio.Future[None]" = asyncio.get_running_loop().create_future()
        self._stop = asyncio.Event()
        self._gone = asyncio.Event()
        self._owner = asyncio.create_task(
            self._own_connection(ready, self._stop, self._gone),
            name=f"agentao-mcp-owner-{self.name}",
        )
        await ready

    async def _own_connection(
        self, ready: "asyncio.Future[None]", stop: asyncio.Event, gone: asyncio.Event
    ) -> None:
        """Open the connection, hold it until ``stop`` is set, then close it."""
        try:
            await self._open()
            if not ready.done():
                ready.set_result(None)
            if self.status is ServerStatus.CONNECTED:
                await stop.wait()
        finally:
            # First, because the close below can take seconds: calls waiting on
            # this connection give up on it now.
            gone.set()
            if not ready.done():
                ready.set_result(None)
            if self.status is ServerStatus.CONNECTING:
                # The open was cancelled before it settled, which ``_open``'s
                # ``except Exception`` does not see. Left as is, the client
                # would report a connect in progress over a half-open session,
                # and the next ``_stop_owner`` would read that stale status as
                # a reason to cancel whichever owner comes next.
                self.status = ServerStatus.DISCONNECTED
                self._session = None
                self._protocol_version = None
            stack, self._exit_stack = self._exit_stack, None
            if stack is not None:
                try:
                    await stack.__aexit__(None, None, None)
                except Exception as e:
                    logger.warning(f"Error disconnecting from MCP server '{self.name}': {e}")

    async def _stop_owner(self) -> None:
        owner, stop = self._owner, self._stop
        if owner is None or stop is None:
            return
        first_request = not stop.is_set()
        stop.set()
        if first_request and self.status is ServerStatus.CONNECTING:
            # Still opening. The owner looks at ``stop`` only once the handshake
            # settles, which can take the whole ``startup`` budget, so a close
            # racing a connect would otherwise wait that long. Abort the open;
            # the owner's ``finally`` still closes whatever it had entered.
            # Only on the first request: a later one would land in that
            # ``finally`` and interrupt the transport shutdown itself.
            owner.cancel()
        # ``wait``, not ``await owner``: a caller that is itself cancelled must
        # not cancel the owner halfway through closing the transport, and an
        # owner that was cancelled (its loop shutting down) must not read as
        # this caller being cancelled.
        await asyncio.wait({owner})
        # Forgotten only once it has finished. A caller cancelled during the
        # wait leaves it recorded, so the next stop (``disconnect_all``'s, say)
        # waits for the close still running instead of returning at once and
        # letting the loop stop under it.
        if self._owner is not owner:
            return  # a concurrent stop already finished this owner
        self._owner = self._stop = None
        if not owner.cancelled() and owner.exception() is not None:
            logger.warning(
                f"MCP server '{self.name}' connection owner failed: {owner.exception()}"
            )

    async def _open(self) -> None:
        """The connect itself: transport, handshake, and cleanup on failure.

        Runs inside the owner task, so the failure cleanup below exits the exit
        stack in the task that entered it.
        """
        self.status = ServerStatus.CONNECTING
        self.error_message = None
        # Stale on a reconnect: the version belongs to the session about to be
        # replaced, and the new one renegotiates from scratch.
        self._protocol_version = None

        # Resolve once and thread down (avoids a second parse — and a second
        # malformed-config warning — inside ``_connect_sse``). ``startup``
        # bounds the whole connect: the URL-transport HTTP open (via
        # ``sse_client`` / ``streamable_http_client``) AND the post-transport
        # handshake below.
        startup_timeout, request_timeout = resolve_timeouts(self.config)

        # Pre-init so the ``except`` can reference them even if
        # ``resolve_transport`` itself raises a config error (in which case the
        # inferred-http hint below must NOT fire — it's a config error, not a
        # handshake failure).
        transport = "unknown"
        source = "inferred"

        try:
            transport, source = resolve_transport(self.config, return_source=True)

            self._exit_stack = AsyncExitStack()
            await self._exit_stack.__aenter__()

            if transport == "stdio":
                await self._connect_stdio()
            elif transport == "sse":
                await self._connect_sse(startup_timeout, request_timeout)
            elif transport == "http":
                await self._connect_streamable_http(startup_timeout, request_timeout)
            else:  # "unknown" — no type and no command/url
                raise ValueError(
                    f"No transport configured for server '{self.name}' "
                    f"(need 'command', or 'url' with type 'sse'/'http')"
                )

            # Bound the whole handshake so a server that opens the stream (or
            # spawns) but never answers can't hang connect forever — the SSE
            # HTTP-open timeout doesn't cover these request round-trips, and
            # stdio has no transport-level connect bound at all. ``wait_for`` is
            # safe here: these are plain awaits on the already-established
            # session and enter no exit-stack context, so a timeout cancellation
            # never crosses an anyio cancel scope into the transport cleanup
            # that ``connect``'s ``except`` performs.
            try:
                self._tools = await asyncio.wait_for(
                    self._handshake(), timeout=startup_timeout
                )
            except asyncio.TimeoutError:
                # Name every round-trip the budget covers: the escalation added
                # ``server/discover``, and pointing the user at a step that
                # already completed sends them looking in the wrong place.
                # ``list_tools`` is plural because it paginates — the budget
                # spans every page, not just the first.
                raise TimeoutError(
                    f"MCP server '{self.name}' did not complete the "
                    f"initialize / server-discover / list_tools (all pages) "
                    f"handshake within {startup_timeout:g}s"
                ) from None

            self.status = ServerStatus.CONNECTED
            logger.info(
                f"MCP server '{self.name}' connected via {transport} "
                f"(protocol {self._protocol_version or 'unknown'}), "
                f"{len(self._tools)} tools"
            )

        except Exception as e:
            self.status = ServerStatus.ERROR
            message = str(e)
            # A bare ``url`` now defaults to Streamable HTTP. If such an
            # *inferred* http connect fails the handshake, the server may
            # actually be a legacy SSE endpoint — surface the one-token fix.
            # Skip it for: an explicit ``type: "http"`` (SSE isn't the likely
            # intent); a NonMcpEndpointError (its own verdict already says the
            # URL isn't MCP at all); an auth failure (switching to SSE won't fix
            # a 401/403 — it would send the user down a wrong path); and a
            # protocol-era mismatch, where the transport was fine and only the
            # version wasn't, so "try SSE" earns a second identical failure; and
            # a catalog/pagination bound, which is reached only *after*
            # ``initialize`` and a ``tools/list`` both succeeded — the transport
            # is proven working and the fault is the server's cursor or catalog.
            if (
                transport == "http"
                and source == "inferred"
                and not isinstance(
                    e, (NonMcpEndpointError, McpProtocolEraError, McpCatalogError)
                )
                and classify_mcp_error(e) is not McpErrorKind.AUTH
            ):
                message += (
                    "  (tried as Streamable HTTP — the default for a bare "
                    "'url'; if this is a legacy SSE endpoint, set "
                    '"type": "sse".)'
                )
            self.error_message = message
            logger.error(f"Failed to connect to MCP server '{self.name}': {message}")
            # Cleanup on failure. The session and the negotiated version belong
            # to the transport being torn down here: a connect that got as far
            # as ``initialize`` and then failed would otherwise leave a version
            # hanging off an ERROR server (and ``call_tool``'s reconnect leg
            # would call into a dead session object).
            self._session = None
            self._protocol_version = None
            if self._exit_stack:
                try:
                    await self._exit_stack.__aexit__(None, None, None)
                except Exception:
                    pass
                self._exit_stack = None

    async def _handshake(self) -> List[McpToolDef]:
        """Settle the protocol era, then collect every ``tools/list`` page.

        Factored out so :meth:`connect` can wrap the whole handshake in a
        single ``startup`` budget via ``asyncio.wait_for`` — which is also what
        bounds the pagination loop as a whole, so it needs no timeout of its
        own.
        """
        await self._negotiate()
        return await self._list_all_tools()

    async def _list_all_tools(self) -> List[McpToolDef]:
        """Collect every page of ``tools/list``, bounded against a hostile peer.

        Reading only the first page — which is what a bare ``list_tools()``
        returns — silently drops every tool a paginating server exposes beyond
        it, with no error anywhere. That is the defect this exists to fix; the
        bounds are what make driving a peer-controlled loop safe.

        ``params=`` is the one spelling that works across every supported SDK:
        mcp 1.x also accepts a positional ``cursor=``, but 2.x dropped it, and
        ``PaginatedRequestParams`` is present from the 1.26.0 floor up. The
        cursor *field* does need :func:`field` — 1.x spells it ``nextCursor``,
        2.x ``next_cursor``.

        Raises ``RuntimeError`` on any bound. :meth:`connect` catches it, marks
        the server ``ERROR`` with the message, and leaves every other server
        untouched.
        """
        tools: List[McpToolDef] = []
        seen_cursors: set = set()
        cursor: Optional[str] = None

        for _ in range(_MAX_TOOL_PAGES):
            # ``params=None`` on the first page keeps the request byte-identical
            # to the single call this replaced. Note ``cursor is not None`` and
            # never ``if cursor``: an MCP cursor is an opaque *string* and ``""``
            # is a legal one. Truthiness would rewrite it back to ``None`` and
            # re-request page 1 — not a hang (the repeated-cursor guard catches
            # it next pass) but a wrong verdict, reporting a compliant server as
            # having repeated a cursor.
            params = (
                PaginatedRequestParams(cursor=cursor) if cursor is not None else None
            )
            result = await self._session.list_tools(params=params)

            # ``result.tools`` is ``list[Tool]``, required, on every supported
            # major — no ``or []`` guard, and no defensive copy: the length is
            # read and the list is immediately extended into the accumulator.
            # Before ``extend``, deliberately — see ``_MAX_TOOLS``.
            if len(tools) + len(result.tools) > _MAX_TOOLS:
                raise McpCatalogError(
                    f"MCP server '{self.name}' exceeded the {_MAX_TOOLS}-tool "
                    f"catalog limit"
                )
            tools.extend(result.tools)

            cursor = field(result, "nextCursor", "next_cursor")
            if cursor is None:
                return tools
            # Byte length, not ``len()``: 64 KiB is a byte budget, and a
            # multibyte cursor would otherwise get up to 4x the allowance.
            if len(cursor.encode("utf-8")) > _MAX_CURSOR_BYTES:
                raise McpCatalogError(
                    f"MCP server '{self.name}' returned a tools/list pagination "
                    f"cursor larger than {_MAX_CURSOR_BYTES} bytes"
                )
            if cursor in seen_cursors:
                raise McpCatalogError(
                    f"MCP server '{self.name}' returned a repeated tools/list "
                    f"pagination cursor"
                )
            seen_cursors.add(cursor)

        # Falling out of the loop still holding a cursor *is* the page-cap
        # failure — no separate counter to keep in sync.
        raise McpCatalogError(
            f"MCP server '{self.name}' exceeded {_MAX_TOOL_PAGES} pages of "
            f"tools/list"
        )

    def _can_discover(self) -> bool:
        """Whether the **live session** can probe the modern era.

        Two independent questions, both of which have to be yes.
        ``SUPPORTS_MODERN_ERA`` answers for the imported ``ClientSession``
        class — the SDK-major gate. The attribute check answers for the object
        actually installed on this client, which an embedding host (or a test)
        may have supplied instead. Gating on the class alone would send
        ``discover()`` to a delegate that has no such method and report the
        resulting ``AttributeError`` as the server's error.
        """
        return SUPPORTS_MODERN_ERA and hasattr(self._session, "discover")

    async def _negotiate(self) -> None:
        """Run the handshake, escalating to the modern era only when refused.

        Two protocol eras exist. The legacy handshake (``2024-11-05`` …
        ``2025-11-25``) is a single ``initialize`` offer the server answers with
        a counter-offer — it never reports a version above the one proposed, and
        the SDK always proposes ``LATEST_HANDSHAKE_VERSION``, so the modern era
        is unreachable through it *by construction*. The modern era
        (``2026-07-28`` and up) replaces it with ``server/discover``, which
        returns the server's whole ``supportedVersions`` list.

        **Handshake first, deliberately** — the reverse of upstream's
        ``mode='auto'``. Leading with ``server/discover`` reaches the newest era
        on a modern server, but a handshake-era server has to reject the unknown
        method, and a server built on the *python* mcp 1.x SDK rejects it by
        dumping a 31-error pydantic union-validation failure — 258 lines,
        measured — to its stderr, which for stdio **is agentao's stderr**. That
        is a per-connect cost on the most common kind of server today, paid for
        a capability with no consumer: agentao does tool discovery and tool
        calls, and both eras serve those identically.

        So the escalation is driven by the server, not by a guess, and it reads
        two rejection codes with deliberately different weight:

        - ``-32022 UnsupportedProtocolVersion`` is **definite** — the server is
          naming the versions it does support. If the escalation then fails,
          that is the era mismatch itself, reported as
          :class:`McpProtocolEraError`.
        - ``-32601 MethodNotFound`` is **speculative**. The modern era replaces
          ``initialize``, so a server built modern-only has no handler for it
          and answers this — but so does a URL that is not an MCP endpoint at
          all. Worth one probe on a connect that has already failed; if the
          probe fails too, the original rejection is what surfaces, unchanged.

        ``discover()`` itself retries at the highest mutual version when the
        server names one, so the escalation is a single call either way.

        What this gives up: a server supporting *both* eras stays on the
        handshake one, and its full ``supportedVersions`` list is never learned.
        The day agentao needs a modern-only capability, the fix is to lead with
        the probe again — this method's ordering is the whole switch. Until
        then it is demand-gated, per the §5.6 watch-item convention in
        ``docs/design/mcp-streamable-http.md``.

        One bound is not ours: the SDK caps each ``server/discover`` send at its
        own ``DISCOVER_TIMEOUT_SECONDS`` (10 s on 2.0.0) with no parameter to
        raise it, so a configured ``timeout.startup`` above that does not extend
        this one round-trip. The enclosing ``startup`` budget in :meth:`connect`
        still bounds the handshake as a whole.
        """
        try:
            self._record_handshake_version(await self._session.initialize())
            return
        except McpProtocolError as exc:
            # Bind outside the handler: ``exc`` is cleared when the block ends.
            handshake_error = exc
            code = getattr(getattr(exc, "error", None), "code", None)

        definite = code == UNSUPPORTED_PROTOCOL_VERSION
        if not (definite or code == METHOD_NOT_FOUND):
            raise handshake_error

        if not self._can_discover():
            if not definite:
                raise handshake_error
            # Two different reasons we cannot escalate, and blaming the wrong
            # one sends the user to the wrong fix.
            why = (
                "this session cannot speak it"
                if SUPPORTS_MODERN_ERA
                else "the installed MCP SDK cannot speak it — mcp 1.x has no "
                "'server/discover'; upgrade to mcp>=2"
            )
            raise McpProtocolEraError(
                f"MCP server '{self.name}' rejected the protocol version agentao "
                f"offered ({handshake_error}). It is asking for the modern "
                f"protocol era and {why}."
            ) from handshake_error

        logger.debug(
            f"MCP '{self.name}': handshake refused ({handshake_error}), "
            "escalating to server/discover"
        )
        try:
            await self._session.discover()
        except Exception as probe_error:
            if not definite:
                # The probe was a guess; keep the server's own verdict.
                raise handshake_error
            # Both eras are now ruled out. Report that, carrying the handshake
            # rejection — it names the versions the server does support, which
            # the probe's own error does not.
            raise McpProtocolEraError(
                f"MCP server '{self.name}' rejected the legacy handshake "
                f"({handshake_error}) and the modern 'server/discover' "
                f"escalation also failed: {probe_error}"
            ) from probe_error
        self._protocol_version = self._session.protocol_version

    def _record_handshake_version(self, result: Any) -> None:
        """Store the version off an ``InitializeResult``.

        Read from the result rather than the session: 1.x's ``ClientSession``
        keeps no public record of what it negotiated. ``field()`` resolves the
        *declared* field, so a server shipping a ``protocol_version`` extra
        cannot shadow the SDK-validated ``protocolVersion`` on 1.x.
        """
        self._protocol_version = field(result, "protocolVersion", "protocol_version")

    async def _connect_stdio(self) -> None:
        """Establish stdio transport."""
        command = self.config["command"]
        args = self.config.get("args", [])

        # Build environment: sanitized base + explicit env vars.
        # The base drops agentao's own provider credentials — an MCP server
        # is a third-party binary and has no business inheriting the key
        # that pays for the LLM. Server-specific vars from mcp.json are
        # applied after the scrub, so a server that genuinely needs a
        # provider key can still be given one explicitly.
        env = build_child_env(self.config.get("env"))

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

    async def _prepare_url_connect(
        self, startup_timeout: float, request_timeout: Optional[float]
    ) -> Tuple[str, Dict[str, str], float]:
        """Shared preamble for the URL transports (SSE + Streamable HTTP).

        Reads ``url`` / ``headers``, computes the ``sse_read_timeout``, and runs
        the content-type preflight. Returns ``(url, headers, sse_read_timeout)``.
        The required ``url`` key is guaranteed present by
        :func:`resolve_transport` (called in :meth:`connect` before dispatch).

        ``sse_read_timeout`` is the max silence between server events before the
        stream drops; the SDK default (300 s) would otherwise cap any per-request
        budget at ~300 s, so raise it to cover a larger ``request`` (never lower
        it — see :data:`_DEFAULT_SSE_READ_TIMEOUT`). The per-request deadline
        itself is applied in ``call_tool`` via ``read_timeout_seconds``.
        """
        url = self.config["url"]
        # Add a default User-Agent (preserving a user-configured one) before the
        # preflight so every agentao MCP request on this URL — probe, handshake,
        # and tool calls — identifies itself consistently.
        headers = _with_default_user_agent(self.config.get("headers"))
        sse_read_timeout = (
            request_timeout
            if request_timeout is not None and request_timeout > _DEFAULT_SSE_READ_TIMEOUT
            else _DEFAULT_SSE_READ_TIMEOUT
        )
        # Fail fast on a URL that points at a web page rather than an MCP
        # endpoint, instead of waiting out the full startup timeout.
        await self._preflight_content_type(url, headers)
        return url, headers, sse_read_timeout

    async def _connect_sse(self, startup_timeout: float, request_timeout: Optional[float]) -> None:
        """Establish the legacy SSE transport (``type: "sse"``).

        ``startup_timeout`` / ``request_timeout`` are pre-resolved by
        :meth:`connect` (see :func:`resolve_timeouts`); ``startup`` bounds the
        HTTP connection open.
        """
        url, headers, sse_read_timeout = await self._prepare_url_connect(
            startup_timeout, request_timeout
        )
        sse_transport = await self._exit_stack.enter_async_context(
            sse_client(
                url,
                headers=headers,
                timeout=startup_timeout,
                sse_read_timeout=sse_read_timeout,
            )
        )
        # ``sse_client`` yields a 2-tuple.
        read_stream, write_stream = sse_transport
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )

    async def _connect_streamable_http(
        self, startup_timeout: float, request_timeout: Optional[float]
    ) -> None:
        """Establish the Streamable HTTP transport (``type: "http"``; the
        default for a bare ``url``).

        Mirrors :meth:`_connect_sse` — same preflight and timeout policy
        (``startup`` bounds the HTTP open; ``sse_read_timeout`` bounds the max
        silence on the long-poll stream) — but the SDK factory is the canonical
        ``streamable_http_client``, which takes a pre-built httpx client rather
        than ``headers`` / ``timeout`` kwargs. We build one with
        ``create_mcp_http_client`` (the SDK's own factory: ``follow_redirects``
        + the recommended defaults) so the semantics match the SSE path exactly.

        One structural difference from SSE: the httpx client is caller-managed
        (entered into the exit stack *before* the transport so the LIFO unwind
        tears the transport down before closing the client).

        The yielded tuple's **arity differs across SDK majors** — mcp 1.x
        yields ``(read, write, get_session_id)``; 2.0 dropped the third element
        so every transport now yields the same 2-tuple (``TransportStreams``).
        agentao never surfaced ``get_session_id``, so the streams are indexed
        rather than unpacked and both shapes work.

        ``terminate_on_close=False``: the SDK's session-terminate ``DELETE``
        would reuse this client, whose ``read`` timeout is raised to cover long
        tool-call budgets (up to ``request``; ≥300 s by default). A server that
        accepts the connection but stalls the ``DELETE`` would then block
        ``disconnect()`` — and the transient-error *reconnect* path — for that
        whole window. SSE and stdio issue no teardown request either, so
        skipping it keeps the transports consistent; the server expires the idle
        session on its own (the terminate ``DELETE`` is a spec SHOULD, not MUST).
        """
        url, headers, sse_read_timeout = await self._prepare_url_connect(
            startup_timeout, request_timeout
        )
        http_client = create_mcp_http_client(
            # ``headers`` always carries at least the default User-Agent (see
            # _prepare_url_connect), so it is never empty — pass it directly.
            headers=headers,
            # ``httpx_for_mcp`` is the flavour the *installed* SDK accepts:
            # httpx on mcp 1.x, httpx2 on 2.x. Passing the wrong one raises
            # ``TypeError: unhashable type: 'Timeout'`` inside httpx2.
            timeout=httpx_for_mcp.Timeout(startup_timeout, read=sse_read_timeout),
        )
        # Caller-managed lifecycle: enter the client first so the LIFO unwind
        # tears down the transport before closing the client.
        await self._exit_stack.enter_async_context(http_client)
        http_transport = await self._exit_stack.enter_async_context(
            streamable_http_client(
                url,
                http_client=http_client,
                terminate_on_close=False,  # see docstring — avoid teardown hang
            )
        )
        # Index, don't unpack: 1.x yields 3 items, 2.0 yields 2 (see docstring).
        # A bare ``a, b, c = …`` raises ValueError on 2.0 and is exactly the
        # kind of break a signature/field-name audit misses — the arity only
        # shows up at the yield.
        read_stream, write_stream = http_transport[0], http_transport[1]
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
        # mcp 1.x wants a timedelta here, 2.x plain float seconds — the shim
        # asks the installed SDK's own signature which one to hand over.
        read_timeout = _sdk_read_timeout(request_timeout)
        # The connect attempts this call has already seen the outcome of. Taken
        # when the call picks up a session, not when it later finds that session
        # gone: another call may have started reconnecting in between.
        seen = self._connect_attempts
        for attempt in range(2):
            if not self._session or self.status != ServerStatus.CONNECTED:
                try:
                    await self._ensure_connected(attempt, seen)
                except Exception as e:
                    # Only reachable for a failure ``connect()`` does not handle
                    # itself (e.g. a malformed ``timeout`` block, parsed before
                    # its try). Everything inside connect() reports through
                    # status/error_message instead of raising — which is why the
                    # check below, not this handler, is what catches a failed
                    # reconnect.
                    return f"MCP connection error for '{self.name}': {e}"
                if self.status != ServerStatus.CONNECTED or not self._session:
                    return (
                        f"MCP connection error for '{self.name}': "
                        f"{self.error_message or 'reconnect failed'}"
                    )

            # The session this attempt runs on. If it drops, only this session
            # is torn down: a concurrent call may already have replaced it.
            session, gone = self._session, self._gone
            seen = self._connect_attempts
            try:
                result = await self._call_on(
                    session, gone, tool_name, arguments, read_timeout
                )
            except Exception as e:
                if UnexpectedClaimedResult is not None and isinstance(
                    e, UnexpectedClaimedResult
                ):
                    # Same shape as the input-required arm below: the SDK's text
                    # tells a *programmer* to register the owning extension.
                    # agentao registers none, so say that instead of forwarding
                    # an API instruction into the model's context.
                    return (
                        f"MCP tool error: '{tool_name}' answered with a protocol "
                        "extension agentao did not negotiate, so its result "
                        "cannot be read."
                    )
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
                    # Tear down the failed transport before reconnecting;
                    # otherwise the old subprocess / stream leaks for the
                    # lifetime of this manager.
                    await self._drop_session(session)
                    continue
                return f"MCP tool error: {e}"

            if InputRequiredResult is not None and isinstance(
                result, InputRequiredResult
            ):
                return self._explain_input_required(tool_name, result)

            # Convert result content to text
            parts = []
            for block in result.content:
                if block.type == "text":
                    parts.append(block.text)
                elif block.type == "image":
                    parts.append(f"[image: {field(block, 'mimeType', 'mime_type')}]")
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
            if not result.content:
                structured = field(result, "structuredContent", "structured_content")
                if structured is not None:
                    # ensure_ascii=False keeps CJK/emoji readable (codebase-wide
                    # convention); default=str makes a non-JSON-native value
                    # degrade to its repr instead of raising out of call_tool.
                    parts.append(
                        json.dumps(structured, ensure_ascii=False, default=str)
                    )

            text = "\n".join(parts)

            if field(result, "isError", "is_error"):
                return f"MCP tool error: {text}"
            return text

        return "MCP tool error: failed after reconnect attempt"

    async def _call_on(
        self,
        session: Any,
        gone: asyncio.Event,
        tool_name: str,
        arguments: Dict[str, Any],
        read_timeout: Any,
    ) -> Any:
        """``session.call_tool``, given up once its connection starts to close.

        The SDK is meant to fail every request pending on a connection that
        closes, and mcp 1.x does not always. When a write to a dead server
        fails, its task group cancels the receive loop that would answer them
        (still so in 1.30). Before 1.30 that loop also raises partway through
        three or more. A request left that way waits forever, since
        ``timeout.request`` is unbounded by default, and concurrent calls
        (#241) made both reachable. The connection's owner does see it close,
        so a call waits on that as well. mcp 2.0 answers them in both cases.
        """
        request = asyncio.ensure_future(
            session.call_tool(
                tool_name, arguments, read_timeout_seconds=read_timeout,
                # Take delivery of an ``InputRequiredResult`` instead of
                # letting the SDK raise on it — see _explain_input_required.
                **({"allow_input_required": True} if SUPPORTS_INPUT_REQUIRED else {}),
            )
        )
        closing = asyncio.ensure_future(gone.wait())
        try:
            await asyncio.wait({request, closing}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            closing.cancel()
            abandoned = not request.done()
            if abandoned:
                request.cancel()
                # Unwinds on its own; whatever it raises doing so is moot.
                request.add_done_callback(
                    lambda task: task.cancelled() or task.exception()
                )
        if abandoned:
            # Classified as a dropped transport: reconnect and retry once.
            raise ConnectionError(f"MCP server '{self.name}': connection closed")
        return request.result()

    #: What the modern era's three input requests are asking agentao to be.
    _INPUT_REQUEST_LABELS = {
        "sampling/createMessage": "an LLM completion (sampling)",
        "elicitation/create": "an answer from the user (elicitation)",
        "roots/list": "the client's roots",
    }

    async def _ensure_connected(self, attempt: int, seen: int) -> None:
        """Connect, unless another call has tried since this one saw ``seen``.

        Several calls can find the connection gone at once. The first to take
        the lock connects; the rest use whatever it got, connected or not,
        rather than each paying for another connect (and, against a dead
        server, another full ``startup`` budget).
        """
        async with self._reconnect_lock:
            if self._connect_attempts != seen:
                return
            if self._session and self.status == ServerStatus.CONNECTED:
                return
            logger.info(f"MCP '{self.name}': reconnecting (attempt {attempt + 1})...")
            # Counted once the attempt has settled, not when it starts: a call
            # that arrives mid-attempt reads the old count, so after waiting it
            # sees the count move and takes the outcome instead of reconnecting
            # again. A cancelled attempt settled nothing and is not counted, so
            # a waiter makes its own rather than reporting a connect still in
            # progress as failed.
            try:
                await self.connect()
            except Exception:
                self._connect_attempts += 1
                raise
            self._connect_attempts += 1

    async def _drop_session(self, failed: Any) -> None:
        """Disconnect ``failed``, unless it has already been replaced.

        Two calls that fail on the same session both land here. Without the
        identity check the second would tear down the connection the first
        had just rebuilt.
        """
        async with self._reconnect_lock:
            if self._session is failed:
                await self.disconnect()

    def _explain_input_required(self, tool_name: str, result: Any) -> str:
        """Turn a modern-era ``InputRequiredResult`` into something the model can use.

        On the modern era (2026-07-28) a server that needs sampling, elicitation
        or roots mid-call no longer opens a back-channel request: ``tools/call``
        *returns* with the list of things it needs plus an opaque
        ``requestState``, expecting the client to answer them and call again.

        agentao wires none of the three client-side resolvers, in either era —
        on the handshake era the SDK's own defaults answer the server with a
        clean "not supported". So the gap is not new; only the failure shape is.
        Left alone, the SDK raises ``RuntimeError("… pass
        allow_input_required=True … retry call_tool(…)")``, an instruction
        addressed to a *programmer*, and ``call_tool``'s broad ``except`` would
        hand that string to the model as the tool's result. Opting in to receive
        the result (rather than string-matching that error) is what lets this
        say something true instead.
        """
        # ``InputRequests`` is a *mapping* — ``dict[str, request]``, keyed by the
        # id the client would echo back in ``input_responses``. Only the values
        # carry the method, and only the methods are of interest here.
        requests = field(result, "inputRequests", "input_requests") or {}
        asked = sorted(
            {
                self._INPUT_REQUEST_LABELS.get(method, method)
                for method in (
                    getattr(req, "method", None) for req in requests.values()
                )
                if method
            }
        )
        wanted = "; ".join(asked) if asked else "input agentao cannot provide"
        logger.info(
            f"MCP '{self.name}': tool '{tool_name}' requires interactive input "
            f"({wanted}) — unsupported, reporting to the model"
        )
        return (
            f"MCP tool error: '{tool_name}' cannot complete without {wanted}. "
            "agentao does not provide sampling, elicitation, or roots to MCP "
            "servers, so this tool is unusable here — use a different tool, or "
            "supply the needed information in the arguments if the server "
            "accepts it that way."
        )

    async def disconnect(self) -> None:
        """Disconnect from the server.

        Stops the owner task, which closes the transport in the task that
        opened it (see ``connect``).
        """
        # Retire the session before waiting out its close, which can take
        # seconds. Calls run concurrently, and one arriving meanwhile would
        # otherwise send its request down a transport being torn down, then
        # retry it on the next connection — running the tool twice when the
        # first request did reach the server. Cleared again below, once the
        # owner has stopped, in case an open being aborted still set it.
        self._session = None
        await self._stop_owner()
        self._session = None
        self._tools = []
        self._protocol_version = None
        self.status = ServerStatus.DISCONNECTED


# ``disconnect_all`` budgets. The first is the caller's (calls in flight get
# this long to finish); the rest bound the steps after it, so a close always
# ends.
_CLOSE_WAIT_S = 5.0
_CANCEL_WAIT_S = 2.0   # a cancelled call unwinding its own ``finally``
_OWNER_STOP_S = 10.0   # every client's transport shutdown, run concurrently
_THREAD_JOIN_S = 2.0
# How often a waiting caller checks that the loop thread is still there.
_CALL_POLL_S = 0.5


class McpManagerClosedError(RuntimeError):
    """A call reached an ``McpClientManager`` after ``disconnect_all``."""


class McpClientManager:
    """Manages multiple MCP server connections with a sync-async bridge.

    The connections live on one event loop that runs on the manager's own
    thread for as long as the manager is open (#241). Synchronous callers hand
    it coroutines with ``run_coroutine_threadsafe`` and wait on the result, so
    calls from different threads run concurrently on one loop, and one
    ``ClientSession`` multiplexes them. The loop used to run only inside a
    caller's ``run_until_complete``: a second caller while it ran got "This
    event loop is already running", and the tool executor runs a batch's calls
    on parallel threads.
    """

    def __init__(self, server_configs: Dict[str, McpServerConfig]):
        self._configs = server_configs
        self._clients: Dict[str, McpClient] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        # Guards ``_closed`` and starting the loop, so a call either gets onto
        # the loop before ``disconnect_all`` starts or is refused.
        self._state_lock = threading.Lock()
        self._closed = False
        # Calls on the loop. Read and written on the loop thread only.
        self._calls: set = set()
        self._closing: Optional[concurrent.futures.Future] = None
        # When the close must be over, set by the first ``disconnect_all``.
        self._close_deadline = 0.0

    def _start_loop(self) -> asyncio.AbstractEventLoop:
        """Start the loop thread on first use. Caller holds ``_state_lock``."""
        if self._loop is None:
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=self._run_loop, args=(loop,),
                name="agentao-mcp-loop", daemon=True,
            )
            thread.start()
            self._loop, self._thread = loop, thread
        return self._loop

    @staticmethod
    def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        finally:
            loop.close()

    def _run(self, coro):
        """Run ``coro`` on the loop thread and wait for its result."""
        with self._state_lock:
            if self._closed:
                coro.close()
                raise McpManagerClosedError("MCP client manager is closed")
            if threading.current_thread() is self._thread:
                coro.close()
                raise RuntimeError(
                    "McpClientManager was called from its own event loop "
                    "thread, which would wait on itself forever"
                )
            future = asyncio.run_coroutine_threadsafe(
                self._tracked(coro), self._start_loop()
            )
            thread = self._thread
        try:
            settled = self._wait(future, thread)
        except BaseException:
            # The caller stopped waiting (Ctrl+C, or anything else raised into
            # it). Cancel the call rather than leave it running unobserved on
            # the loop.
            future.cancel()
            raise
        if not settled:
            raise McpManagerClosedError(
                "MCP client manager closed before the call finished"
            )
        return future.result()

    @staticmethod
    def _wait(future: concurrent.futures.Future, thread: threading.Thread) -> bool:
        """Wait for ``future``; False when the loop thread is gone without it.

        Polled rather than waited on outright: a call still pending when the
        loop stops (it ignored its cancellation during a close, say) is never
        resolved, and its caller would wait forever.
        """
        while True:
            done, _ = concurrent.futures.wait((future,), timeout=_CALL_POLL_S)
            if done:
                return True
            if not thread.is_alive():
                return future.done()

    async def _tracked(self, coro):
        task = asyncio.current_task()
        self._calls.add(task)
        try:
            return await coro
        finally:
            self._calls.discard(task)

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
        # A copy: the loop thread clears ``_clients`` during ``disconnect_all``.
        for name, client in list(self._clients.items()):
            if client.status == ServerStatus.CONNECTED:
                for tool in client.tools:
                    tools.append((name, tool))
        return tools

    def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Call a tool on a specific server (sync wrapper)."""
        return self._run(self._call_tool_async(server_name, tool_name, arguments))

    async def _call_tool_async(
        self, server_name: str, tool_name: str, arguments: Dict[str, Any]
    ) -> str:
        client = self._clients.get(server_name)
        if not client:
            raise RuntimeError(f"MCP server '{server_name}' not found")
        return await client.call_tool(tool_name, arguments)

    def disconnect_all(self, timeout: float = _CLOSE_WAIT_S) -> None:
        """Close the manager. Idempotent; every later call raises
        :class:`McpManagerClosedError`.

        In order: stop accepting calls; give calls in flight ``timeout``
        seconds; cancel the rest and wait for them to unwind; stop every
        client's connection; stop and join the loop thread. Each step after the
        first has its own bound, so a hung server cannot hold the close open.
        A cancelled future is only a request, which is why the loop is not
        stopped straight after cancelling.

        The close runs on the loop, which stops itself at the end of it, and
        the loop's thread then closes the loop. So a caller interrupted while
        waiting here (Ctrl+C) does not leave the thread running, and a later
        call waits for the same close, to the first call's deadline, instead
        of returning at once or stopping the loop under it.
        """
        with self._state_lock:
            if threading.current_thread() is self._thread:
                if self._closed:
                    return
                raise RuntimeError(
                    "McpClientManager.disconnect_all was called from its own "
                    "event loop thread, which would wait on itself forever"
                )
            loop, thread = self._loop, self._thread
            if not self._closed:
                self._closed = True
                self._close_deadline = (
                    time.monotonic() + timeout + _CANCEL_WAIT_S + _OWNER_STOP_S + 1.0
                )
                if loop is not None:
                    # Held so the task is referenced while nobody waits on it.
                    self._closing = asyncio.run_coroutine_threadsafe(
                        self._shutdown(timeout), loop
                    )
            deadline = self._close_deadline
        if loop is None or thread is None:
            self._clients.clear()
            return
        thread.join(max(0.0, deadline - time.monotonic()))
        if not thread.is_alive():
            return
        logger.warning("MCP disconnect did not finish in time; stopping its loop anyway")
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            pass  # the close finished just now and its thread closed the loop
        thread.join(_THREAD_JOIN_S)
        if thread.is_alive():
            logger.warning("MCP event loop thread did not stop; leaving it to exit with the process")

    async def _shutdown(self, timeout: float) -> None:
        try:
            # Every call accepted before the close is registered by now: it
            # was scheduled on this loop before this coroutine was.
            calls = {task for task in self._calls if not task.done()}
            if calls:
                _, pending = await asyncio.wait(calls, timeout=timeout)
                for task in pending:
                    task.cancel()
                if pending:
                    _, stuck = await asyncio.wait(pending, timeout=_CANCEL_WAIT_S)
                    if stuck:
                        logger.warning(
                            f"{len(stuck)} MCP call(s) did not stop when cancelled; "
                            "closing without them"
                        )
            clients = list(self._clients.values())
            if clients:
                stops = [asyncio.ensure_future(client.disconnect()) for client in clients]
                _, pending = await asyncio.wait(stops, timeout=_OWNER_STOP_S)
                if pending:
                    logger.warning(
                        f"{len(pending)} MCP server(s) did not finish disconnecting in time"
                    )
            self._clients.clear()
        except Exception as e:
            logger.warning(f"Error disconnecting MCP servers: {e}")
        finally:
            # Scheduled, not immediate, so callbacks queued by this step (a
            # cancelled call handing its result to its waiting thread) run first.
            loop = asyncio.get_running_loop()
            loop.call_soon(loop.stop)

    def get_server_status(self) -> List[Dict[str, Any]]:
        """Get status summary of all servers."""
        result = []
        # A copy: the loop thread clears ``_clients`` during ``disconnect_all``.
        for name, client in list(self._clients.items()):
            result.append({
                "name": name,
                "status": client.status.value,
                "transport": client.transport_type,
                # None until the handshake settles it (see McpClient.protocol_version).
                "protocol": client.protocol_version,
                "tools": len(client.tools),
                "trusted": client.is_trusted,
                "error": client.error_message,
            })
        return result
