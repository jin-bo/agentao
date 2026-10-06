"""MCP client and client manager for connecting to MCP servers."""

import asyncio
import concurrent.futures
import contextvars
import json
import logging
import os
import sys
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
from mcp.types import INTERNAL_ERROR, METHOD_NOT_FOUND, PaginatedRequestParams
from mcp.types import Tool as McpToolDef

from .. import __version__

try:
    _BaseExceptionGroup = BaseExceptionGroup  # Python 3.11+
except NameError:  # pragma: no cover - 3.10: anyio depends on the backport there
    from exceptiongroup import BaseExceptionGroup as _BaseExceptionGroup
from ..cancellation import AgentCancelledError, current_cancellation_token
from ..capabilities.process import build_child_env
from ._compat import (
    SUPPORTS_INPUT_REQUIRED,
    SUPPORTS_MODERN_ERA,
    UNSUPPORTED_PROTOCOL_VERSION,
    InputRequiredResult,
    McpProtocolError,
    READ_SUPPORTS_INPUT_REQUIRED,
    UnexpectedClaimedResult,
    field,
    httpx_for_mcp,
    read_timeout as _sdk_read_timeout,
)
from .config import (
    McpEnvVarError,
    McpServerConfig,
    McpOAuthConfigError,
    McpTransportConfigError,
    check_credential_vars,
    resolve_oauth,
    resolve_timeouts,
    resolve_transport,
)
from .oauth import NEEDS_AUTH as _VERDICT_NEEDS_AUTH
from .resources import (
    MAX_CURSOR_BYTES as _MAX_RESOURCE_CURSOR_BYTES,
    McpResourceError,
    ResourcePage,
    ResourceRead,
    TemplatePage,
    render_call_result,
    resource_content,
    resource_info,
    template_info,
)
from .oauth import AuthVerdict, StoredTokenAuth
from .oauth_store import OAuthRuntime
from . import skills as _skills

logger = logging.getLogger("agentao.mcp")


class McpErrorKind(str, Enum):
    """Outcome of classifying an exception raised by ``ClientSession.call_tool``.

    Drives the retry policy in :meth:`McpClient.call_tool`:
    ``AUTH`` surfaces immediately (creds won't change on retry);
    ``SESSION_EXPIRED`` — the server refused the request, so it did not run —
    reconnects and retries once; ``TRANSPORT_DROPPED`` — the connection went
    with the request possibly delivered — drops the session and reports the
    result as unknown, since a retry could run the tool twice; ``OTHER``
    surfaces without reconnecting. A resource read, which cannot have acted,
    reconnects and retries once on either of the two.
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

    A session error the server sends as ``INTERNAL_ERROR`` is not a refusal:
    mcp 2.x's server answers a request that was in flight when its session
    ended with ``-32603 Session terminated before the request completed``. The
    request had reached the handler, so it is ``TRANSPORT_DROPPED``. The
    refusal forms are the SDK client's 404 ``Session terminated`` (code
    ``INVALID_REQUEST``) and the server's ``Session not found``.
    """
    msg = str(exc).lower()
    type_name = type(exc).__name__.lower()
    haystack_with_type = f"{msg} {type_name}"
    for kind, markers, match_type_name in _ERROR_RULES:
        haystack = haystack_with_type if match_type_name else msg
        if any(marker in haystack for marker in markers):
            if (
                kind is McpErrorKind.SESSION_EXPIRED
                and isinstance(exc, McpProtocolError)
                and getattr(getattr(exc, "error", None), "code", None) == INTERNAL_ERROR
            ):
                return McpErrorKind.TRANSPORT_DROPPED
            return kind
    return McpErrorKind.OTHER


def _is_unfollowed_redirect(exc: Exception) -> bool:
    """True for mcp>=2.2's refusal to follow a redirect off the endpoint's origin.

    From 2.2 the SDK follows a redirect only within the endpoint's origin (plus
    an ``http`` → ``https`` upgrade on the same host), whatever the passed
    client's ``follow_redirects`` says. It exposes no type or code of its own
    for the refusal — it arrives as a JSON-RPC ``INVALID_REQUEST`` whose
    message the SDK composes (``streamable_http._unfollowed_redirect``), both
    variants in the frame ``Redirect to <url> not followed…``. That frame is
    what is matched. A miss only brings back the "try SSE" hint.
    """
    msg = str(exc)
    return msg.startswith("Redirect to ") and " not followed" in msg


def _first_failure(exc: BaseException) -> Optional[Exception]:
    """The first real failure inside an (arbitrarily nested) exception group.

    A task group raises every child failure as a group, and the one-line
    ``error_message`` wants the failure itself, not "unhandled errors in a
    TaskGroup (1 sub-exception)". Cancellations inside the group are the
    fallout of that failure, not a cause, so they are skipped.
    """
    if isinstance(exc, _BaseExceptionGroup):
        for inner in exc.exceptions:
            found = _first_failure(inner)
            if found is not None:
                return found
        return None
    if isinstance(exc, Exception):
        return exc
    return None


#: What mcp 2.0's Streamable HTTP transport says for any non-404 HTTP error on a
#: request (``streamable_http.py``), status and headers gone.
_OPAQUE_HTTP_FAILURE = "Server returned an error response"


def _is_opaque_http_failure(exc: BaseException) -> bool:
    """An HTTP-level failure with nothing more specific to say about itself."""
    return _OPAQUE_HTTP_FAILURE in str(exc)


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
    # The server wants an OAuth login (docs/design/mcp-oauth.md §5.3): a 401
    # with a Bearer challenge and no usable token, or a rejected refresh.
    NEEDS_AUTH = "needs_auth"


class McpClient:
    """Manages a single MCP server connection."""

    def __init__(
        self, name: str, config: McpServerConfig, *, oauth: Optional[OAuthRuntime] = None
    ):
        self.name = name
        self.config = config
        # Shared with the other clients of one manager (its per-record locks
        # and shielded refreshes); a client built on its own gets its own.
        self._oauth_runtime = oauth
        # Rebuilt on every connect, so its verdict starts clear per session.
        self._auth: Optional[StoredTokenAuth] = None
        # The verdict that put this client in NEEDS_AUTH, kept so a call can
        # tell whether a login has happened since (its record ``stamp``).
        self._needs_auth: Optional[AuthVerdict] = None
        self.status = ServerStatus.DISCONNECTED
        self.error_message: Optional[str] = None
        self._session: Optional[ClientSession] = None
        self._exit_stack: Optional[AsyncExitStack] = None
        self._tools: List[McpToolDef] = []
        self._protocol_version: Optional[str] = None
        # The live connection's ``ServerCapabilities``; ``None`` whenever the
        # version is (they describe the same session).
        self._server_capabilities: Any = None
        # Sticky: whether any connection of this client ever declared
        # ``resources``. Lets the all-servers listing recover a *dropped*
        # resource server without reconnecting every server that never had any.
        self._resources_seen = False
        # Skills (docs/design/mcp-skills.md §5.3). Listed at most once per
        # client, at its first connect (D8) — a gate failure there settles it
        # too, since the catalogue is fixed for the session; a reconnect keeps
        # whatever that attempt found.
        self._skills_listed = False
        self._skill_entries: List[Any] = []
        self._skills_unavailable: List[Tuple[str, str]] = []
        # Why this opted-in server's skills are unavailable, or ``None``.
        self._skills_problem: Optional[str] = None
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
    def server_capabilities(self) -> Any:
        """The live connection's ``ServerCapabilities``, or ``None``.

        Reset with :attr:`protocol_version` — read it after any reconnect,
        never before.
        """
        return self._server_capabilities

    @property
    def supports_resources(self) -> bool:
        """Whether the live connection declares the ``resources`` capability."""
        caps = self._server_capabilities
        return caps is not None and getattr(caps, "resources", None) is not None

    @property
    def resources_seen(self) -> bool:
        """Whether any connection of this client has declared ``resources``."""
        return self._resources_seen

    @property
    def skills_requested(self) -> bool:
        """``"skills": true`` in this server's config — the opt-in (§5.1).

        Only ``True`` counts: any other value leaves Skills off.
        """
        return self.config.get("skills") is True

    def skills_gate_problem(self) -> Optional[str]:
        """Why the live connection cannot serve Skills, or ``None`` (§5.2).

        All four conditions, read off the connection now in hand: the SDK can
        speak the modern era, the negotiated version is at least the
        extension's base revision, the extension is declared, and so is
        ``resources``.
        """
        if not SUPPORTS_MODERN_ERA:
            return "skills need mcp>=2 (installed: 1.x)"
        version = self._protocol_version
        if version is None:
            return "the server is not connected"
        if version < _skills.MIN_PROTOCOL_VERSION:
            return (
                f"the server speaks {version}; skills need "
                f"{_skills.MIN_PROTOCOL_VERSION} or later"
            )
        caps = self._server_capabilities
        extensions = getattr(caps, "extensions", None) if caps is not None else None
        if not isinstance(extensions, dict) or _skills.EXTENSION_ID not in extensions:
            return f"the server does not declare {_skills.EXTENSION_ID}"
        if not self.supports_resources:
            return (
                f"the server declares {_skills.EXTENSION_ID} without the "
                "resources capability"
            )
        return None

    @property
    def skill_entries(self) -> List[Any]:
        """The validated entries listed at connect (``SkillEntry``)."""
        return self._skill_entries

    @property
    def skills_unavailable(self) -> List[Tuple[str, str]]:
        """``(uri, reason)`` for listed skills that cannot be loaded."""
        return self._skills_unavailable

    @property
    def skills_problem(self) -> Optional[str]:
        return self._skills_problem

    @property
    def is_trusted(self) -> bool:
        return bool(self.config.get("trust", False))

    @property
    def oauth_runtime(self) -> OAuthRuntime:
        if self._oauth_runtime is None:
            self._oauth_runtime = OAuthRuntime()
        return self._oauth_runtime

    def _auth_verdict(self) -> Optional[AuthVerdict]:
        """What this connection's auth object concluded, if anything.

        Read **before** any error classification: on mcp 2.0 an auth failure
        reaches the caller as "Server returned an error response", with the
        status and headers gone, so nothing else can recognise it.
        """
        return self._auth.verdict if self._auth is not None else None

    def _enter_needs_auth(self, verdict: AuthVerdict) -> None:
        self.status = ServerStatus.NEEDS_AUTH
        self.error_message = verdict.message
        self._needs_auth = verdict

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
                self._server_capabilities = None
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
        self._server_capabilities = None

        # Resolve once and thread down (avoids a second parse — and a second
        # malformed-config warning — inside ``_connect_sse``). ``startup``
        # bounds the whole connect: the URL-transport HTTP open (via
        # ``sse_client`` / ``streamable_http_client``) AND the post-transport
        # handshake below.
        startup_timeout, request_timeout = resolve_timeouts(self.config)
        started = time.monotonic()

        # Pre-init so the ``except`` can reference them even if
        # ``resolve_transport`` itself raises a config error (in which case the
        # inferred-http hint below must NOT fire — it's a config error, not a
        # handshake failure).
        transport = "unknown"
        source = "inferred"

        self._auth = None
        self._needs_auth = None
        try:
            transport, source = resolve_transport(self.config, return_source=True)
            if transport != "stdio":
                # ``headers`` and ``oauth`` are URL-transport fields; a stdio
                # server never sends them, so an unset variable there is inert.
                check_credential_vars(self.config)
            # Every OAuth-eligible URL server gets the auth object, record or
            # not: without one it only observes, and it is the only place a
            # Bearer challenge is still visible (docs/design/mcp-oauth.md §5.4).
            oauth_settings = resolve_oauth(self.config)
            if oauth_settings is not None:
                self._auth = StoredTokenAuth(
                    self.name, self.config["url"], self.oauth_runtime,
                    profile=oauth_settings.get("profile"),
                )

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
            if self.skills_requested and not self._skills_listed:
                await self._list_skills_safely(
                    startup_timeout - (time.monotonic() - started)
                )

        except asyncio.CancelledError:
            cause = await self._unwind_transport_cancel()
            if cause is None:
                raise
            await self._fail_connect(cause, transport, source)
        except Exception as e:
            await self._fail_connect(e, transport, source)

    async def _unwind_transport_cancel(self) -> Optional[Exception]:
        """Recover the real failure behind a cancel the transport raised itself.

        On mcp 1.x the Streamable HTTP transport runs its reader and writer in
        an anyio task group entered in *this* task. When one of them
        fails — a 500 on the ``initialize`` POST, a refused redirect — the
        group cancels its scope, so the handshake here receives a bare
        ``CancelledError`` and the failure itself stays inside the group until
        the group is exited. Left to the owner's ``finally``, that exit logged
        the failure as a *disconnect* warning and the connect ended
        ``DISCONNECTED`` with no ``error_message``: ``call_tool`` could say
        only "reconnect failed". (2.x reports the failure to the handshake
        instead, and never gets here.)

        Exiting the stack here, with the cancel passed in, lets the scope sort
        it out: anyio absorbs only a cancel it delivered itself, and the group
        then raises the child's failure, which is returned. A cancel from
        outside — ``_stop_owner`` aborting a connect, the loop shutting down —
        comes back out of the exit as ``CancelledError``, and ``None`` tells
        the caller to re-raise it. Must be called from the ``except`` that
        caught the cancel: ``sys.exc_info()`` is what reaches the scope.
        """
        stack, self._exit_stack = self._exit_stack, None
        if stack is None:
            return None
        # Out of CONNECTING before the (possibly slow) transport close below:
        # ``_stop_owner`` reads CONNECTING as "abort the open" and would cancel
        # this task in the middle of that close — the interruption its own
        # first-request-only rule exists to prevent. The session and version
        # die with the transport either way; ``_fail_connect`` sets ERROR.
        self.status = ServerStatus.DISCONNECTED
        self._session = None
        self._protocol_version = None
        self._server_capabilities = None
        try:
            suppressed = await stack.__aexit__(*sys.exc_info())
        except asyncio.CancelledError:
            return None
        except Exception as e:
            return e
        if not suppressed:
            return None
        # Absorbed with no failure to show for it — still not a connect.
        return ConnectionError("the MCP transport closed during the handshake")

    async def _fail_connect(self, e: Exception, transport: str, source: str) -> None:
        """Record a failed connect: status, message (with the SSE hint), cleanup.

        A failure raised out of a task group arrives as a group; the message
        and the hint's checks below are about the failure inside it (1.x's
        ``sse_client`` otherwise reported a 500 as "unhandled errors in a
        TaskGroup (1 sub-exception)").
        """
        e = _first_failure(e) or e
        verdict = self._auth_verdict()
        if verdict is not None and verdict.kind == _VERDICT_NEEDS_AUTH:
            self._enter_needs_auth(verdict)
            logger.warning(f"MCP server '{self.name}': {verdict.message}")
            await self._cleanup_failed_connect()
            return
        self.status = ServerStatus.ERROR
        message = str(e)
        if verdict is not None:
            # A refresh that failed for a reason a login would not fix: the
            # transport's own text (often just "Server returned an error
            # response") says less than the refresh did.
            message = f"{verdict.message}; {message}"
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
        # Nor for a redirect the SDK would not follow: its message already
        # names the URL to configure, and the SDK's SSE client applies the
        # same origin rule (2.2 and 1.30 release notes), so "try SSE" cannot fix it.
        # Nor for a malformed ``oauth`` block or an unset credential variable
        # (config errors, not handshake failures) or a failed OAuth refresh
        # (the transport is not at fault).
        if (
            transport == "http"
            and source == "inferred"
            and verdict is None
            and not isinstance(
                e,
                (
                    NonMcpEndpointError,
                    McpProtocolEraError,
                    McpCatalogError,
                    McpOAuthConfigError,
                    McpEnvVarError,
                ),
            )
            and classify_mcp_error(e) is not McpErrorKind.AUTH
            and not _is_unfollowed_redirect(e)
        ):
            message += (
                "  (tried as Streamable HTTP — the default for a bare "
                "'url'; if this is a legacy SSE endpoint, set "
                '"type": "sse".)'
            )
        self.error_message = message
        logger.error(f"Failed to connect to MCP server '{self.name}': {message}")
        await self._cleanup_failed_connect()

    async def _cleanup_failed_connect(self) -> None:
        # Cleanup on failure. The session and the negotiated version belong
        # to the transport being torn down here: a connect that got as far
        # as ``initialize`` and then failed would otherwise leave a version
        # hanging off an ERROR server (and ``call_tool``'s reconnect leg
        # would call into a dead session object).
        self._session = None
        self._protocol_version = None
        self._server_capabilities = None
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
        try:
            return await self._list_all_tools()
        except McpProtocolError as e:
            # A server that serves only resources need not implement
            # ``tools/list`` (docs/design/mcp-resources.md). Its ``-32601`` means
            # "no tools" — but only when it did not declare ``tools``: a server
            # that declared them and then refuses the method is broken, and one
            # that answers without declaring keeps working as before.
            code = getattr(getattr(e, "error", None), "code", None)
            caps = self._server_capabilities
            declares_tools = caps is not None and getattr(caps, "tools", None) is not None
            if code == METHOD_NOT_FOUND and not declares_tools:
                logger.info(f"MCP server '{self.name}' serves no tools/list; no tools")
                return []
            raise

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
        discover_refused = False
        if self.skills_requested and self._can_discover():
            # Discover-first, for an opted-in server only (D9): a dual-era
            # server is reachable on the modern era — the only one Skills
            # are specified for — only by asking first. The cost the
            # handshake-first order avoids (a 1.x server's rejection noise)
            # is paid by servers whose operator asked for Skills.
            try:
                await self._session.discover()
            except (McpProtocolError, RuntimeError) as exc:
                logger.debug(
                    f"MCP '{self.name}': server/discover refused ({exc}); "
                    "falling back to the initialize handshake"
                )
                discover_refused = True
            else:
                self._protocol_version = self._session.protocol_version
                self._record_capabilities(
                    getattr(self._session, "server_capabilities", None)
                )
                return
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

        if discover_refused or not self._can_discover():
            if not definite:
                raise handshake_error
            # Two different reasons we cannot escalate, and blaming the wrong
            # one sends the user to the wrong fix.
            why = (
                "its 'server/discover' was refused as well"
                if discover_refused
                else "this session cannot speak it"
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
        self._record_capabilities(getattr(self._session, "server_capabilities", None))

    def _record_handshake_version(self, result: Any) -> None:
        """Store the version off an ``InitializeResult``.

        Read from the result rather than the session: 1.x's ``ClientSession``
        keeps no public record of what it negotiated. ``field()`` resolves the
        *declared* field, so a server shipping a ``protocol_version`` extra
        cannot shadow the SDK-validated ``protocolVersion`` on 1.x.
        """
        self._protocol_version = field(result, "protocolVersion", "protocol_version")
        self._record_capabilities(getattr(result, "capabilities", None))

    def _record_capabilities(self, capabilities: Any) -> None:
        self._server_capabilities = capabilities
        if self.supports_resources:
            self._resources_seen = True

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
                **({"auth": self._auth} if self._auth is not None else {}),
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
            # The preflight above ran without it, on purpose (§5.4).
            **({"auth": self._auth} if self._auth is not None else {}),
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

        :meth:`call_tool_result` rendered by
        :func:`agentao.mcp.resources.render_call_result` with nothing saved: an
        embedded binary resource is described by URI, type and size.
        """
        result = await self.call_tool_result(tool_name, arguments)
        if isinstance(result, str):
            return result
        return render_call_result(result)

    async def call_tool_result(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call a tool and return the SDK's whole ``CallToolResult``.

        Or, for the paths that never produce one (a transport, auth or
        connection failure, an ``InputRequiredResult``), the error *string*
        :meth:`call_tool` has always returned for them. Rendering is the
        caller's: ``McpTool`` renders with a save directory and a read hint.

        Retry policy is driven by :func:`classify_mcp_error`:
        ``AUTH`` surfaces immediately (retrying with the same credentials
        only produces another 401/403); ``SESSION_EXPIRED`` reconnects and
        retries once; ``TRANSPORT_DROPPED`` drops the session and returns
        :meth:`_outcome_unknown` without a retry; ``OTHER`` surfaces without
        reconnecting.

        The line is whether the call can have been sent. A connection found
        closing *before* the call goes out is reconnected first. Once the call
        is inside the SDK, a dropped transport does not show whether the
        server received it: a server that runs the call and dies before it
        answers looks the same as one that died first. A retry there could
        run a tool with side effects twice, so the model is told the result is
        unknown instead. The cost: a stdio server that died while idle is
        noticed only by the next call, which then reports an unknown result
        for a call that never ran. Neither SDK major records that its
        receive loop has ended, so nothing earlier can tell.

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
        hint = self._needs_auth_unchanged()
        if hint is not None:
            return f"MCP auth error: {hint}"
        for attempt in range(2):
            if self._session is not None and self._gone.is_set():
                # Its connection is already closing, and nothing has been sent
                # on it by this call: reconnecting first is safe.
                await self._drop_session(self._session)
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
                if self.status == ServerStatus.NEEDS_AUTH:
                    return f"MCP auth error: {self.error_message}"
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
                # The auth object's verdict first, before any classification:
                # on mcp 2.0 this failure arrives as "Server returned an error
                # response", which the string classifier files under OTHER.
                verdict = self._auth_verdict()
                if verdict is not None and verdict.kind == _VERDICT_NEEDS_AUTH:
                    self._enter_needs_auth(verdict)
                    return f"MCP auth error: {verdict.message}"
                # A transient refresh failure (REFRESH_FAILED) is the
                # connection's, not necessarily this request's: another call
                # may have hit it while this one failed for its own reason.
                # So it changes nothing about how this error is handled, and
                # only explains it when the error is the opaque HTTP failure a
                # refresh failure would produce.
                refresh_note = verdict.message if verdict is not None else None
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
                explains = refresh_note is not None and (
                    kind is McpErrorKind.AUTH or _is_opaque_http_failure(e)
                )
                suffix = f" ({refresh_note})" if explains else ""
                if kind is McpErrorKind.AUTH:
                    return f"MCP auth error: {e}{suffix}"
                if kind is McpErrorKind.TRANSPORT_DROPPED:
                    # The next call reconnects; this one is not sent again.
                    await self._drop_session(session)
                    return self._outcome_unknown(tool_name, e)
                if attempt == 0 and kind is McpErrorKind.SESSION_EXPIRED:
                    logger.warning(
                        f"MCP '{self.name}' session refused {type(e).__name__}, "
                        f"retrying after reconnect: {e}"
                    )
                    # Tear down the failed transport before reconnecting;
                    # otherwise the old subprocess / stream leaks for the
                    # lifetime of this manager.
                    await self._drop_session(session)
                    continue
                return f"MCP tool error: {e}{suffix}"

            if InputRequiredResult is not None and isinstance(
                result, InputRequiredResult
            ):
                return self._explain_input_required(tool_name, result)

            return result

        return "MCP tool error: failed after reconnect attempt"

    def _outcome_unknown(self, tool_name: str, exc: Exception) -> str:
        """What the model reads when a call's connection drops after it was sent."""
        logger.warning(
            f"MCP '{self.name}': connection dropped during '{tool_name}' "
            f"({type(exc).__name__}: {exc}); not retried, result unknown"
        )
        return (
            f"MCP tool error: the connection to MCP server '{self.name}' closed "
            f"after the call to '{tool_name}' was sent ({exc}). The result is "
            "unknown: the server can have run the call. Check its effect before "
            "you call it again. The next call reconnects."
        )

    def _needs_auth_unchanged(self) -> Optional[str]:
        """The login hint, while nothing could have changed the verdict.

        A server in NEEDS_AUTH is reconnected only once its credential record
        has changed (a login, here or in another process): reconnecting on
        every call would re-ask the server the question it just answered.
        """
        verdict = self._needs_auth
        if self.status != ServerStatus.NEEDS_AUTH or verdict is None:
            return None
        url = self.config.get("url")
        if url and self.oauth_runtime.store.stamp(url, self._oauth_profile()) != verdict.stamp:
            return None
        return verdict.message

    def _oauth_profile(self) -> Optional[str]:
        """``oauth.profile`` from the config, which ``resolve_oauth`` validated at connect."""
        oauth = self.config.get("oauth")
        profile = oauth.get("profile") if isinstance(oauth, dict) else None
        return profile if isinstance(profile, str) else None

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
        return await self._send_on(
            gone,
            session.call_tool(
                tool_name, arguments, read_timeout_seconds=read_timeout,
                # Take delivery of an ``InputRequiredResult`` instead of
                # letting the SDK raise on it — see _explain_input_required.
                **({"allow_input_required": True} if SUPPORTS_INPUT_REQUIRED else {}),
            ),
        )

    async def _send_on(self, gone: asyncio.Event, coro: Any) -> Any:
        """Await ``coro``, given up once its connection starts to close (see ``_call_on``)."""
        request = asyncio.ensure_future(coro)
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
            # Classified as a dropped transport: a tool call reports an unknown
            # result, a resource read reconnects and retries once.
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

    # ------------------------------------------------------------------
    # Resources (docs/design/mcp-resources.md §5.1)
    # ------------------------------------------------------------------

    async def _resource_request(
        self,
        send: Any,
        *,
        not_found_uri: Optional[str] = None,
        method_not_found_ok: bool = False,
        require_skills: bool = False,
        not_found_what: str = "skill",
    ) -> Any:
        """``send(session)`` after the connection recovery and capability check.

        Order is §5.1's: the existing recovery first (``_ensure_connected``,
        the path ``call_tool_result`` takes), *then* the capability of the
        connection now in hand — capabilities are reset with the session, so
        checking before the reconnect would refuse exactly the call the
        reconnect exists for. A drop mid-request reconnects once, as a tool
        call does; a read cannot have acted, so the retry is safe.

        Returns the SDK result, or ``None`` when ``method_not_found_ok`` and the
        server answered ``-32601``. Every failure raises
        :class:`McpResourceError`.
        """
        _startup, request_timeout = resolve_timeouts(self.config)
        seen = self._connect_attempts
        hint = self._needs_auth_unchanged()
        if hint is not None:
            raise McpResourceError(self.name, "auth", f"MCP auth error: {hint}")
        for attempt in range(2):
            if not self._session or self.status != ServerStatus.CONNECTED:
                try:
                    await self._ensure_connected(attempt, seen)
                except Exception as e:
                    raise McpResourceError(
                        self.name, "connection",
                        f"MCP connection error for '{self.name}': {e}",
                    ) from e
                if self.status == ServerStatus.NEEDS_AUTH:
                    raise McpResourceError(
                        self.name, "auth", f"MCP auth error: {self.error_message}"
                    )
                if self.status != ServerStatus.CONNECTED or not self._session:
                    raise McpResourceError(
                        self.name, "connection",
                        f"MCP connection error for '{self.name}': "
                        f"{self.error_message or 'reconnect failed'}",
                    )
            if not self.supports_resources:
                raise McpResourceError(
                    self.name, "unsupported",
                    f"MCP server '{self.name}' does not declare the resources capability",
                )
            if require_skills:
                problem = self.skills_gate_problem()
                if problem is not None:
                    raise McpResourceError(
                        self.name, "unsupported",
                        f"MCP server '{self.name}' cannot serve skills: {problem}",
                    )

            session, gone = self._session, self._gone
            seen = self._connect_attempts
            try:
                coro = send(session)
                if request_timeout is not None:
                    coro = asyncio.wait_for(coro, timeout=request_timeout)
                return await self._send_on(gone, coro)
            except Exception as e:
                # Only our own ``wait_for`` bound reads as "did not answer": with
                # no ``request`` timeout configured, a ``TimeoutError`` is the
                # transport's (on 3.11+ the same class) and is classified below.
                if request_timeout is not None and isinstance(e, asyncio.TimeoutError):
                    raise McpResourceError(
                        self.name, "error",
                        f"MCP server '{self.name}' did not answer within {request_timeout:g}s",
                    ) from None
                verdict = self._auth_verdict()
                if verdict is not None and verdict.kind == _VERDICT_NEEDS_AUTH:
                    self._enter_needs_auth(verdict)
                    raise McpResourceError(
                        self.name, "auth", f"MCP auth error: {verdict.message}"
                    ) from e
                if UnexpectedClaimedResult is not None and isinstance(
                    e, UnexpectedClaimedResult
                ):
                    raise McpResourceError(
                        self.name, "error",
                        f"MCP server '{self.name}' answered with a protocol extension "
                        "agentao did not negotiate",
                    ) from e
                code = getattr(getattr(e, "error", None), "code", None)
                if isinstance(e, McpProtocolError):
                    if method_not_found_ok and code == METHOD_NOT_FOUND:
                        return None
                    if require_skills and not_found_uri is not None and code == -32602:
                        raise McpResourceError(
                            self.name, "not_found",
                            f"MCP server '{self.name}' serves no {not_found_what} at "
                            f"{not_found_uri} ({e})",
                        ) from e
                    # -32602 on 2026-07-28; clients "SHOULD also accept -32002",
                    # the earlier code.
                    if not_found_uri is not None and code in (-32602, -32002):
                        raise McpResourceError(
                            self.name, "not_found",
                            f"Resource not found on MCP server '{self.name}': "
                            f"{not_found_uri} ({e}). List what it serves with "
                            f"list_mcp_resources(server=\"{self.name}\").",
                        ) from e
                kind = classify_mcp_error(e)
                if kind is McpErrorKind.AUTH:
                    raise McpResourceError(self.name, "auth", f"MCP auth error: {e}") from e
                if attempt == 0 and kind in (
                    McpErrorKind.SESSION_EXPIRED,
                    McpErrorKind.TRANSPORT_DROPPED,
                ):
                    logger.warning(
                        f"MCP '{self.name}' transient {type(e).__name__} on a "
                        f"resource request, retrying after reconnect: {e}"
                    )
                    await self._drop_session(session)
                    continue
                raise McpResourceError(
                    self.name, "error", f"MCP resource error from '{self.name}': {e}"
                ) from e
        raise McpResourceError(
            self.name, "connection",
            f"MCP connection error for '{self.name}': failed after reconnect attempt",
        )

    def _checked_cursor(self, result: Any) -> Optional[str]:
        cursor = field(result, "nextCursor", "next_cursor")
        if cursor is not None and len(cursor.encode("utf-8")) > _MAX_RESOURCE_CURSOR_BYTES:
            raise McpResourceError(
                self.name, "catalog",
                f"MCP server '{self.name}' returned a pagination cursor larger "
                f"than {_MAX_RESOURCE_CURSOR_BYTES} bytes",
            )
        return cursor

    async def list_resources(self, cursor: Optional[str] = None) -> ResourcePage:
        """One page of ``resources/list``."""
        params = PaginatedRequestParams(cursor=cursor) if cursor is not None else None
        result = await self._resource_request(lambda s: s.list_resources(params=params))
        return ResourcePage(
            server=self.name,
            resources=[resource_info(self.name, r) for r in result.resources],
            next_cursor=self._checked_cursor(result),
        )

    async def list_resource_templates(self, cursor: Optional[str] = None) -> TemplatePage:
        """One page of ``resources/templates/list``; ``-32601`` means no templates."""
        params = PaginatedRequestParams(cursor=cursor) if cursor is not None else None
        result = await self._resource_request(
            lambda s: s.list_resource_templates(params=params), method_not_found_ok=True
        )
        if result is None:
            return TemplatePage(server=self.name, templates=[])
        templates = field(result, "resourceTemplates", "resource_templates") or []
        return TemplatePage(
            server=self.name,
            templates=[template_info(self.name, t) for t in templates],
            next_cursor=self._checked_cursor(result),
        )

    async def read_resource(self, uri: str) -> ResourceRead:
        """``resources/read``. Contents come back as data; nothing is decoded."""
        extra = {"allow_input_required": True} if READ_SUPPORTS_INPUT_REQUIRED else {}
        result = await self._resource_request(
            lambda s: s.read_resource(uri, **extra), not_found_uri=uri
        )
        if InputRequiredResult is not None and isinstance(result, InputRequiredResult):
            raise McpResourceError(
                self.name, "input_required",
                f"MCP server '{self.name}' cannot return {uri} without interactive "
                "input (sampling, elicitation or roots), which agentao does not provide.",
            )
        return ResourceRead(
            server=self.name,
            uri=uri,
            contents=[resource_content(c) for c in (result.contents or [])],
        )

    # ------------------------------------------------------------------
    # Skills (docs/design/mcp-skills.md §5.3, §5.5)
    # ------------------------------------------------------------------

    async def _list_skills_safely(self, budget: float) -> None:
        """List skills at connect; a failure turns Skills off, never the server.

        Runs after the tools are in hand, in its own ``try`` and with what is
        left of the startup budget, and never raises: ``connect()``'s failure
        path (``_cleanup_failed_connect``) tears the whole connection down,
        and an opted-in server's tools must keep working without its skills.
        Cancellation is the one thing let through.
        """
        self._skills_listed = True
        problem = self.skills_gate_problem()
        if problem is not None:
            self._skills_problem = problem
            logger.warning(f"MCP server '{self.name}': skills unavailable: {problem}")
            return
        if budget <= 0:
            self._skills_problem = "no startup budget was left to list skills"
            logger.warning(
                f"MCP server '{self.name}': skills unavailable: {self._skills_problem}"
            )
            return
        try:
            entries, unavailable = await asyncio.wait_for(
                self._list_all_skills(), timeout=budget
            )
        except asyncio.TimeoutError:
            self._skills_problem = (
                f"skills/list did not finish within the {budget:g}s left of the "
                "startup budget"
            )
        except Exception as e:  # noqa: BLE001 — any failure ends at "no skills"
            self._skills_problem = f"skills/list failed: {e}"
        else:
            self._skill_entries, self._skills_unavailable = entries, unavailable
            logger.info(
                f"MCP server '{self.name}': {len(entries)} skill(s) listed"
                + (f", {len(unavailable)} unavailable" if unavailable else "")
            )
            return
        logger.warning(
            f"MCP server '{self.name}': skills unavailable: {self._skills_problem}"
        )

    async def _list_all_skills(self) -> Tuple[List[Any], List[Tuple[str, str]]]:
        """Every ``skills/list`` page, under ``tools/list``'s bounds. No file is fetched."""
        entries: List[Any] = []
        unavailable: List[Tuple[str, str]] = []
        seen_uris: set = set()
        seen_cursors: set = set()
        dropped = 0
        cursor: Optional[str] = None
        adapter = _skills.result_adapter()
        for _ in range(_skills.MAX_SKILL_PAGES):
            raw = await self._session.send_request(
                _skills.list_skills_request(cursor), adapter
            )
            items = raw.get("skills")
            if not isinstance(items, list):
                raise McpCatalogError(f"MCP server '{self.name}': skills/list has no skills array")
            # ``unavailable`` also holds valid-but-unloadable entries, which
            # are in ``seen_uris`` too; count each listed item once.
            if len(seen_uris) + dropped + len(items) > _skills.MAX_SKILLS:
                raise McpCatalogError(
                    f"MCP server '{self.name}' exceeded the {_skills.MAX_SKILLS}-skill limit"
                )
            for item in items:
                uri = item.get("uri") if isinstance(item, dict) else None
                try:
                    entry = _skills.validate_entry(self.name, item)
                    if entry.uri in seen_uris:
                        raise _skills.SkillEntryError("listed twice")
                except _skills.SkillEntryError as e:
                    # Never silently: named in the log and in ``/skills``.
                    logger.warning(
                        f"MCP server '{self.name}': skill {uri!r} dropped: {e}"
                    )
                    unavailable.append((str(uri), f"invalid entry: {e}"))
                    dropped += 1
                    continue
                seen_uris.add(entry.uri)
                if entry.unavailable:
                    unavailable.append((entry.uri, entry.unavailable))
                entries.append(entry)
            cursor = raw.get("nextCursor")
            if cursor is None:
                return entries, unavailable
            if not isinstance(cursor, str) or len(cursor.encode("utf-8")) > _skills.MAX_CURSOR_BYTES:
                raise McpCatalogError(
                    f"MCP server '{self.name}' returned a malformed or oversized "
                    "skills/list cursor"
                )
            if cursor in seen_cursors:
                raise McpCatalogError(
                    f"MCP server '{self.name}' returned a repeated skills/list cursor"
                )
            seen_cursors.add(cursor)
        raise McpCatalogError(
            f"MCP server '{self.name}' exceeded {_skills.MAX_SKILL_PAGES} pages of skills/list"
        )

    async def get_skill(self, uri: str) -> Any:
        """``skills/get`` → a validated ``SkillEntry``. Raises :class:`McpResourceError`."""
        adapter = _skills.result_adapter()
        raw = await self._resource_request(
            lambda s: s.send_request(_skills.get_skill_request(uri), adapter),
            not_found_uri=uri, require_skills=True,
        )
        item = raw.get("skill") if isinstance(raw, dict) else None
        try:
            entry = _skills.validate_entry(self.name, item)
        except _skills.SkillEntryError as e:
            raise McpResourceError(
                self.name, "invalid", f"MCP server '{self.name}': invalid skill entry for {uri}: {e}"
            ) from None
        if entry.uri != uri:
            raise McpResourceError(
                self.name, "invalid",
                f"MCP server '{self.name}' answered skills/get for {uri} with {entry.uri}",
            )
        return entry

    async def read_skill_resource(self, uri: str) -> ResourceRead:
        """``resources/read`` for a skill file, gated on Skills rather than ``resources``."""
        extra = {"allow_input_required": True} if READ_SUPPORTS_INPUT_REQUIRED else {}
        result = await self._resource_request(
            lambda s: s.read_resource(uri, **extra), not_found_uri=uri, require_skills=True,
            not_found_what="skill file",
        )
        if InputRequiredResult is not None and isinstance(result, InputRequiredResult):
            raise McpResourceError(
                self.name, "input_required",
                f"MCP server '{self.name}' cannot return {uri} without interactive input.",
            )
        return ResourceRead(
            server=self.name,
            uri=uri,
            contents=[resource_content(c) for c in (result.contents or [])],
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
        self._server_capabilities = None
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

    def __init__(
        self,
        server_configs: Dict[str, McpServerConfig],
        *,
        oauth_runtime: Optional[OAuthRuntime] = None,
    ):
        self._configs = server_configs
        self._clients: Dict[str, McpClient] = {}
        # One per manager: its per-record locks are bound to this manager's
        # loop, and its shielded refreshes are what ``_shutdown`` waits for.
        # Between managers (and processes) the record's file lock excludes.
        self._oauth = oauth_runtime or OAuthRuntime()
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
            # Scheduled from an empty context: ``call_soon_threadsafe`` copies
            # the caller's, and the task (and any connection owner task it
            # starts) would otherwise hold the turn's ``CancellationToken`` —
            # and the host loop it references — for as long as it lives.
            future = contextvars.Context().run(
                asyncio.run_coroutine_threadsafe,
                self._tracked(coro), self._start_loop(),
            )
            thread = self._thread
        # A cancelled turn cancels the call, as a raise into the waiter does:
        # the task on the loop is cancelled, and mcp 2.x then sends the server
        # ``notifications/cancelled`` (1.x does not). Without this a turn's
        # cancel waited for the tool — forever, with no ``timeout.request``.
        token = current_cancellation_token()
        unregister = token.add_done_callback(future.cancel) if token is not None else None
        try:
            settled = self._wait(future, thread)
        except BaseException:
            # The caller stopped waiting (Ctrl+C, or anything else raised into
            # it). Cancel the call rather than leave it running unobserved on
            # the loop.
            future.cancel()
            raise
        finally:
            if unregister is not None:
                unregister()
        if not settled:
            raise McpManagerClosedError(
                "MCP client manager closed before the call finished"
            )
        if future.cancelled() and token is not None and token.is_cancelled:
            raise AgentCancelledError(token.reason)
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
            client = McpClient(name, config, oauth=self._oauth)
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

    def call_tool_result(
        self, server_name: str, tool_name: str, arguments: Dict[str, Any]
    ) -> Any:
        """Like :meth:`call_tool`, returning the whole ``CallToolResult``.

        Or the error string :meth:`call_tool` returns for a call that produced
        no result (see :meth:`McpClient.call_tool_result`).
        """
        return self._run(self._call_tool_result_async(server_name, tool_name, arguments))

    async def _call_tool_result_async(
        self, server_name: str, tool_name: str, arguments: Dict[str, Any]
    ) -> Any:
        client = self._clients.get(server_name)
        if not client:
            raise RuntimeError(f"MCP server '{server_name}' not found")
        return await client.call_tool_result(tool_name, arguments)

    # -- resources (docs/design/mcp-resources.md) ------------------------

    def resources_allowed(self, server_name: str) -> bool:
        """Whether ``server_name`` is configured here and its config allows resources.

        Static — config only, no connection state. ``"resources": false``
        hides a server's resources from every generic surface (the three
        tools, ``/mcp resources``, the read hint). Any value but ``true`` or
        absent counts as disabled: a typo must not expose what was meant to
        be hidden.
        """
        config = self._configs.get(server_name)
        if config is None:
            return False
        return config.get("resources", True) is True

    def resource_servers(self) -> List[str]:
        """Servers an all-servers listing walks, sorted by label.

        Allowed by config, and with a connection that has declared
        ``resources`` at some point — so a *dropped* resource server is
        recovered, while servers that never offered any are not reconnected
        on every listing.
        """
        return sorted(
            name
            for name, client in list(self._clients.items())
            if self.resources_allowed(name) and client.resources_seen
        )

    def _resource_client(self, server_name: str) -> McpClient:
        """§5.1 step 1: refuse an unknown or disabled server before any request."""
        if server_name not in self._configs and server_name not in self._clients:
            raise McpResourceError(
                server_name, "unknown_server", f"MCP server '{server_name}' not found"
            )
        if not self.resources_allowed(server_name):
            raise McpResourceError(
                server_name, "disabled",
                f"resources are disabled for server '{server_name}'",
            )
        client = self._clients.get(server_name)
        if client is None:
            # Configured but never connected: the recovery step connects it.
            # ``setdefault``, not a get-then-set: this runs on the caller's
            # thread, and two parallel tool calls racing here would otherwise
            # each install a client, orphaning the first one's connection.
            client = self._clients.setdefault(
                server_name,
                McpClient(server_name, self._configs[server_name], oauth=self._oauth),
            )
        return client

    def list_resources(self, server_name: str, cursor: Optional[str] = None) -> ResourcePage:
        """One page of ``server_name``'s resources. Raises :class:`McpResourceError`."""
        client = self._resource_client(server_name)
        return self._run(client.list_resources(cursor))

    def list_resource_templates(
        self, server_name: str, cursor: Optional[str] = None
    ) -> TemplatePage:
        """One page of ``server_name``'s resource templates. Raises :class:`McpResourceError`."""
        client = self._resource_client(server_name)
        return self._run(client.list_resource_templates(cursor))

    def read_resource(self, server_name: str, uri: str) -> ResourceRead:
        """Read ``uri`` from ``server_name``. Raises :class:`McpResourceError`.

        Always through the server: an ``https://`` URI is never fetched
        directly, which would skip ``security/url_policy.py``.
        """
        client = self._resource_client(server_name)
        return self._run(client.read_resource(uri))

    # -- skills (docs/design/mcp-skills.md) --------------------------------

    def skills_allowed(self, server_name: str) -> bool:
        """``"skills": true`` in ``server_name``'s config (§5.1). Only ``True`` counts."""
        config = self._configs.get(server_name)
        return config is not None and config.get("skills") is True

    def skill_servers(self) -> List[str]:
        """Opted-in servers whose connection passed the §5.2 gate, sorted."""
        return sorted(
            name
            for name, client in list(self._clients.items())
            if self.skills_allowed(name)
            and client.status == ServerStatus.CONNECTED
            and client.skills_gate_problem() is None
            and client.skills_problem is None
        )

    def skill_listing(self, server_name: str) -> Tuple[List[Any], List[Tuple[str, str]], Optional[str]]:
        """``(entries, unavailable, problem)`` recorded at connect."""
        client = self._clients.get(server_name)
        if client is None:
            return [], [], "not connected"
        return list(client.skill_entries), list(client.skills_unavailable), client.skills_problem

    def _skill_client(self, server_name: str) -> McpClient:
        if not self.skills_allowed(server_name):
            raise McpResourceError(
                server_name, "disabled", f"skills are not enabled for MCP server '{server_name}'"
            )
        client = self._clients.get(server_name)
        if client is None:
            raise McpResourceError(
                server_name, "unknown_server", f"MCP server '{server_name}' not found"
            )
        return client

    def get_skill(self, server_name: str, uri: str) -> Any:
        """``skills/get`` on ``server_name`` → ``SkillEntry``. Raises :class:`McpResourceError`."""
        return self._run(self._skill_client(server_name).get_skill(uri))

    def read_skill_resource(self, server_name: str, uri: str) -> ResourceRead:
        """Read one skill file from its own server. Raises :class:`McpResourceError`.

        Gated on ``skills`` rather than ``resources``: ``"resources": false``
        hides a server from the *generic* surfaces, not from its skills.
        """
        return self._run(self._skill_client(server_name).read_skill_resource(uri))

    def login(self, server_name: str, ui: Any) -> ServerStatus:
        """Run an OAuth login for one server, then reconnect it.

        ``ui`` implements :class:`agentao.mcp.oauth.OAuthLoginUI`. The flow runs
        on the manager's loop like every other call, so its commit takes the
        same per-record lock as a refresh. Returns the server's status after
        the reconnect. A server that was never connected has no tools
        registered with the agent; a restart is what loads them (D8).
        """
        return self._run(self._login_async(server_name, ui))

    async def _login_async(self, server_name: str, ui: Any) -> ServerStatus:
        from .oauth import login as _login

        # Before the browser flow, not only at the reconnect after it — and
        # before ``_oauth_client``, whose ``resolve_oauth`` refuses the empty
        # secret an unset ``oauth.client_secret`` variable expands to with a
        # message that does not name the variable.
        known = self._clients.get(server_name)
        config = known.config if known is not None else self._configs.get(server_name)
        if config is not None:
            check_credential_vars(config)
        client = self._oauth_client(server_name)
        settings = resolve_oauth(client.config)
        assert settings is not None  # checked by _oauth_client
        await _login(server_name, client.config, settings, ui, self._oauth)
        # Under the reconnect lock, like every other reconnect: a call that
        # saw the record change would otherwise reconnect concurrently.
        async with client._reconnect_lock:
            await client.disconnect()
            await client.connect()
            client._connect_attempts += 1
        return client.status

    def logout(self, server_name: str) -> bool:
        """Delete a server's stored credential and disconnect it.

        Waits for a refresh in flight on that record, so the refresh cannot
        write the credential back. Returns whether there was one to delete.
        """
        return self._run(self._logout_async(server_name))

    async def _logout_async(self, server_name: str) -> bool:
        from .oauth import logout as _logout

        client = self._oauth_client(server_name)
        settings = resolve_oauth(client.config)
        assert settings is not None  # checked by _oauth_client
        deleted = await _logout(
            client.config["url"], self._oauth, profile=settings.get("profile")
        )
        async with client._reconnect_lock:
            await client.disconnect()
        return deleted

    def _oauth_client(self, server_name: str) -> McpClient:
        client = self._clients.get(server_name)
        if client is None:
            config = self._configs.get(server_name)
            if config is None:
                raise RuntimeError(f"MCP server '{server_name}' not found")
            client = McpClient(server_name, config, oauth=self._oauth)
            self._clients[server_name] = client
        if resolve_oauth(client.config) is None:
            raise RuntimeError(
                f"MCP server '{server_name}' does not use OAuth (a stdio server, "
                "an 'Authorization' header, or \"oauth\": false)"
            )
        return client

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
                    time.monotonic()
                    + timeout
                    + _CANCEL_WAIT_S
                    # Shielded OAuth refreshes the cancelled calls left behind.
                    + self._oauth.token_timeout
                    + _OWNER_STOP_S
                    + 1.0
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
            # A cancelled call does not cancel the OAuth refresh it started:
            # that runs shielded, so the server's rotation reaches disk. The
            # loop must not stop under it (docs/design/mcp-oauth.md §5.3).
            left = await self._oauth.wait_critical(self._oauth.token_timeout)
            if left:
                logger.warning(
                    f"{left} MCP OAuth credential write(s) did not finish in time; "
                    "the next refresh of that server may need a login"
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
                # Declared by the live connection; False while disconnected.
                "resources": client.supports_resources,
                "resources_enabled": self.resources_allowed(name),
                # None for a server without "skills": true; else how many of
                # the skills listed at connect can be loaded (the count the
                # catalogue offers), with ``skills_error`` saying why none.
                "skills": (
                    sum(1 for e in client.skill_entries if not e.unavailable)
                    if client.skills_requested else None
                ),
                "skills_error": client.skills_problem if client.skills_requested else None,
                "trusted": client.is_trusted,
                "error": client.error_message,
            })
        return result
