"""Protocol-era negotiation: ``initialize``, escalating to ``server/discover``.

mcp 2.0 added a second protocol era. The legacy handshake can only ever report
the version the *client* proposed or older, so the modern era (2026-07-28+) is
unreachable through it by construction; ``server/discover`` is the only
round-trip that returns a server's full ``supportedVersions`` list.

agentao leads with the handshake and escalates only when the server refuses it
with ``-32022``. Leading with the probe instead would cost every handshake-era
server an unknown-method rejection — 258 lines of pydantic union-validation
dump on stderr for a python mcp 1.x server, measured — to reach an era nothing
agentao does needs yet. See :meth:`McpClient._negotiate`.

**The peer here is a real JSON-RPC server, not a fake session.** Every era
difference this negotiation turns on is visible only in the messages that cross
the transport — which method was sent, which error came back, what the SDK
adopted. A stubbed ``session.discover()`` would restate agentao's assumptions
about all three. The tests therefore drive a genuine ``ClientSession`` over the
SDK's own in-memory streams and answer it with wire dicts dumped from real
``mcp.types`` models.

Escalation tests are skipped on mcp 1.x, which has no ``server/discover`` at
all; :func:`test_no_probe_is_ever_sent_to_a_handshake_server` covers both cells.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

import mcp.types as mcp_types
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams
from mcp.shared.message import SessionMessage
from mcp.types import ErrorData, JSONRPCError, JSONRPCResponse

from agentao.mcp._compat import SUPPORTS_MODERN_ERA, McpProtocolError
from agentao.mcp.client import McpClient, McpProtocolEraError, ServerStatus
from tests.support.mcp import connected_client, run_async

modern_only = pytest.mark.skipif(
    not SUPPORTS_MODERN_ERA, reason="installed mcp SDK has no modern protocol era"
)

# Deliberately *not* the newest handshake version: a test that asserted the
# latest would also pass if agentao echoed its own offer back instead of
# reading the server's pick.
HANDSHAKE_VERSION = "2025-06-18"

# Spec wire codes, written out because these tests author the *server* side.
# The client-side probe of -32022 lives in ``_compat`` and is ``None`` on 1.x.
METHOD_NOT_FOUND = -32601
UNSUPPORTED_PROTOCOL_VERSION = -32022


def _modern_version() -> str:
    """The newest modern version the installed SDK knows (2.x only)."""
    from mcp.types.version import LATEST_MODERN_VERSION

    return LATEST_MODERN_VERSION


# ---------------------------------------------------------------------------
# Wire plumbing
# ---------------------------------------------------------------------------

def _wrap(message: Any) -> SessionMessage:
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


def _unwrap(session_message: SessionMessage) -> Any:
    """The concrete JSON-RPC model inside a received ``SessionMessage``."""
    message = session_message.message
    return getattr(message, "root", message)


def _initialize_result(version: str = HANDSHAKE_VERSION) -> Dict[str, Any]:
    """A handshake result as it appears on the wire.

    Dumped from the real model so the keys are whatever the installed SDK
    actually validates, rather than a hand-typed guess at the wire shape.
    """
    return mcp_types.InitializeResult(
        protocolVersion=version,
        capabilities=mcp_types.ServerCapabilities(),
        serverInfo=mcp_types.Implementation(name="fake-server", version="1.0"),
    ).model_dump(by_alias=True, mode="json", exclude_none=True)


def _discover_result(versions: Optional[List[str]] = None) -> Dict[str, Any]:
    """A modern-era discover result as it appears on the wire (2.x only)."""
    return mcp_types.DiscoverResult(
        supportedVersions=versions if versions is not None else [_modern_version()],
        capabilities={"tools": {}},
    ).model_dump(by_alias=True, mode="json", exclude_none=True)


class _FakeMcpServer:
    """A JSON-RPC peer for the far end of a real ``ClientSession``.

    ``handlers`` maps a method name to its reply — a result ``dict`` or an
    ``ErrorData``. Unmapped methods answer ``-32601``, which is what a
    spec-compliant handshake-era server does with ``server/discover``.
    """

    def __init__(self, handlers: Dict[str, Any]):
        self._handlers = handlers
        self.methods: List[str] = []  # every method that crossed the wire, in order

    async def serve(self, read_stream, write_stream) -> None:
        async for item in read_stream:
            if isinstance(item, Exception):
                continue
            message = _unwrap(item)
            method = getattr(message, "method", None)
            request_id = getattr(message, "id", None)
            if method is None or request_id is None:
                continue  # a notification (``initialized``) — nothing to answer
            self.methods.append(method)

            reply = self._handlers.get(
                method,
                ErrorData(code=METHOD_NOT_FOUND, message=f"Unknown method {method}"),
            )
            if isinstance(reply, ErrorData):
                out: Any = JSONRPCError(jsonrpc="2.0", id=request_id, error=reply)
            else:
                out = JSONRPCResponse(jsonrpc="2.0", id=request_id, result=reply)
            await write_stream.send(_wrap(out))


@asynccontextmanager
async def _client_against(server: _FakeMcpServer):
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


def _negotiate(server: _FakeMcpServer) -> McpClient:
    """Run ``_negotiate`` against *server* and hand back the client."""

    async def run() -> McpClient:
        async with _client_against(server) as client:
            await client._negotiate()
            return client

    return run_async(run())


def _unsupported_version_error(supported: List[str]) -> ErrorData:
    """The ``-32022`` a modern-only server answers the legacy handshake with."""
    return ErrorData(
        code=UNSUPPORTED_PROTOCOL_VERSION,
        message="unsupported protocol version",
        data={"supported": supported, "requested": "2025-11-25"},
    )


def _raise_protocol_error(error: ErrorData) -> None:
    """Raise the SDK's JSON-RPC exception across the constructor split.

    1.x is ``McpError(error: ErrorData)``; 2.x is ``MCPError(code, message,
    data)``. The ``_compat`` alias is only safe for ``except`` and ``.error``
    reads (see its docstring), so a test that needs to *raise* one probes here
    rather than assuming either shape.
    """
    try:
        raise McpProtocolError(
            code=error.code, message=error.message, data=error.data
        )
    except TypeError:
        raise McpProtocolError(error) from None


# ---------------------------------------------------------------------------
# The common path: handshake only, no probe, on either SDK major
# ---------------------------------------------------------------------------

def test_no_probe_is_ever_sent_to_a_handshake_server():
    """One round-trip, and the server's own pick is what gets recorded.

    The absence of ``server/discover`` on the wire is the assertion that
    matters: sending it is what would make a python mcp 1.x server dump 258
    lines of validation failure onto agentao's stderr.
    """
    server = _FakeMcpServer({"initialize": _initialize_result()})

    client = _negotiate(server)

    assert server.methods == ["initialize"]
    assert client.protocol_version == HANDSHAKE_VERSION


@modern_only
def test_a_dual_era_server_is_left_on_the_handshake_era():
    """A server offering both eras is *not* escalated — that is the tradeoff.

    It answers ``server/discover`` perfectly well; agentao never asks, because
    nothing it does needs the modern era. Pinned as a test so the day that
    stops being true, flipping the order is a deliberate edit and not a
    surprise.
    """
    server = _FakeMcpServer(
        {
            "initialize": _initialize_result(),
            "server/discover": _discover_result(),
        }
    )

    client = _negotiate(server)

    assert server.methods == ["initialize"]
    assert client.protocol_version == HANDSHAKE_VERSION


# ---------------------------------------------------------------------------
# Escalation: -32022 is the only trigger
# ---------------------------------------------------------------------------

@modern_only
def test_modern_only_server_is_reached_by_escalating_on_32022():
    """The connect this whole feature exists to stop failing.

    A server speaking only ``2026-07-28`` rejects the legacy handshake with
    ``-32022``; before the escalation that was an outright connect failure.
    """
    modern = _modern_version()
    server = _FakeMcpServer(
        {
            "initialize": _unsupported_version_error([modern]),
            "server/discover": _discover_result([modern]),
        }
    )

    client = _negotiate(server)

    assert client.protocol_version == modern
    assert server.methods == ["initialize", "server/discover"]


def test_handshake_error_other_than_32022_does_not_escalate():
    """A plain handshake failure surfaces; it is not retried as modern.

    Not ``@modern_only``: the guarantee is "errors propagate", which holds on
    both majors and is exactly what a future over-broad ``except`` would break.
    Gating it on the modern era would leave that unverified on the two 1.x CI
    cells — where the escalation is inert and the propagation is *all* there is.
    """
    server = _FakeMcpServer(
        {"initialize": ErrorData(code=-32603, message="internal server error")}
    )

    async def run():
        # Asserted *inside* the session context: an exception escaping
        # ``ClientSession.__aexit__`` comes back wrapped in an anyio
        # ``ExceptionGroup``, which would obscure what was actually raised.
        async with _client_against(server) as client:
            with pytest.raises(McpProtocolError, match="internal server error"):
                await client._negotiate()
            return client

    client = run_async(run())

    assert client.protocol_version is None
    assert server.methods == ["initialize"]


@modern_only
def test_a_self_contradictory_server_surfaces_its_own_verdict():
    """-32022 followed by a handshake-only advertisement has no fallback left.

    The server said the handshake era is not an option, then advertised nothing
    but handshake versions. There is no third era to try, so the connect fails —
    and the report has to carry *both* halves: ``adopt()``'s "no mutual version"
    (a ``RuntimeError``, not an ``MCPError``) and the handshake rejection, which
    is the only one that names what the server said it supports.
    """
    server = _FakeMcpServer(
        {
            "initialize": _unsupported_version_error(["2026-07-28"]),
            "server/discover": _discover_result(["2025-06-18", "2025-11-25"]),
        }
    )

    async def run():
        async with _client_against(server) as client:
            with pytest.raises(McpProtocolEraError) as excinfo:
                await client._negotiate()
            return client, str(excinfo.value)

    client, message = run_async(run())

    assert "unsupported protocol version" in message
    assert "No mutually supported" in message
    assert client.protocol_version is None
    assert server.methods == ["initialize", "server/discover"]


# ---------------------------------------------------------------------------
# A dead transport is surfaced, not reinterpreted
# ---------------------------------------------------------------------------

def test_transport_failure_is_surfaced_not_swallowed():
    """Only a protocol rejection escalates; an outage is not an era verdict."""

    class _DeadSession:
        async def initialize(self):
            raise ConnectionResetError("stream closed")

        async def discover(self):  # pragma: no cover - must never be reached
            raise AssertionError("escalated on a transport failure")

    client = McpClient("fake", {"command": "echo"})
    client._session = _DeadSession()

    with pytest.raises(ConnectionResetError):
        run_async(client._negotiate())

    assert client.protocol_version is None


def test_a_session_without_discover_is_never_escalated_into():
    """The escalation gate asks the *live session*, not the imported class.

    Hosts (and several tests in this repo) inject their own session object. On
    the 2.x cell a class-level probe alone would send ``discover()`` to a
    delegate that has none and report the resulting ``AttributeError`` as the
    server's error, burying the real protocol rejection.
    """

    class _NoDiscoverSession:
        async def initialize(self):
            _raise_protocol_error(_unsupported_version_error(["2026-07-28"]))

    client = McpClient("fake", {"command": "echo"})
    client._session = _NoDiscoverSession()

    with pytest.raises(Exception) as excinfo:
        run_async(client._negotiate())

    assert "AttributeError" not in repr(excinfo.value)
    assert "unsupported protocol version" in str(excinfo.value)
    assert client.protocol_version is None


def test_a_server_extra_cannot_shadow_the_validated_version():
    """A server-supplied ``protocol_version`` key must not outrank the field.

    ``InitializeResult`` is ``extra='allow'`` on both majors, so on mcp 1.x —
    where ``protocol_version`` is not a field — an attribute probe resolves to
    the server's *unvalidated* extra. Whatever lands in
    ``McpClient.protocol_version`` is rendered into Rich markup by ``/mcp list``
    and is what a host gates capabilities on.
    """
    result = mcp_types.InitializeResult.model_validate(
        {
            "protocolVersion": HANDSHAKE_VERSION,
            "capabilities": {},
            "serverInfo": {"name": "evil", "version": "1"},
            "protocol_version": "[/dim]NOT-THE-REAL-VERSION[bold red]",
        }
    )

    client = McpClient("fake", {"command": "echo"})
    client._record_handshake_version(result)

    assert client.protocol_version == HANDSHAKE_VERSION


# ---------------------------------------------------------------------------
# The negotiated version reaches the rest of the runtime
# ---------------------------------------------------------------------------

@modern_only
def test_tools_are_listable_after_escalating_to_the_modern_era():
    """Adopting the modern era must not break the tool listing that follows."""
    modern = _modern_version()
    server = _FakeMcpServer(
        {
            "initialize": _unsupported_version_error([modern]),
            "server/discover": _discover_result([modern]),
            # Dumped from the model, not hand-typed: the modern era validates
            # server results per version and *requires* ``ttlMs`` /
            # ``cacheScope`` / ``resultType`` that the handshake era defaults.
            "tools/list": mcp_types.ListToolsResult(tools=[]).model_dump(
                by_alias=True, mode="json", exclude_none=True
            ),
        }
    )

    async def run():
        async with _client_against(server) as client:
            result = await client._handshake()
            return client, result

    client, result = run_async(run())

    assert result.tools == []
    assert client.protocol_version == modern
    assert server.methods == ["initialize", "server/discover", "tools/list"]


@modern_only
def test_a_failed_escalation_still_names_the_handshake_rejection():
    """The server's own -32022 is the actionable error — it must survive.

    A modern-only server behind a gateway that does not forward the new method
    answers ``initialize`` with -32022 and then rejects ``server/discover``.
    Reporting only the probe's error ("Unknown method server/discover") hides
    both the version mismatch and the ``supported`` list the server named.
    """
    server = _FakeMcpServer(
        {"initialize": _unsupported_version_error(["2026-07-28"])}
        # no server/discover handler → the peer answers -32601
    )

    async def run():
        async with _client_against(server) as client:
            with pytest.raises(McpProtocolEraError) as excinfo:
                await client._negotiate()
            return str(excinfo.value)

    message = run_async(run())

    assert "unsupported protocol version" in message  # the handshake verdict
    assert "server/discover" in message  # and what the escalation hit


@modern_only
def test_method_not_found_on_initialize_escalates_speculatively():
    """A modern-only server has no ``initialize`` handler at all.

    The modern era *replaces* the handshake, so -32601 is the spec answer such
    a server gives — the mirror image of the 1.x server answering -32601 to
    ``server/discover`` that decided this whole ordering.
    """
    modern = _modern_version()
    server = _FakeMcpServer(
        {  # no "initialize" handler → the peer answers -32601
            "server/discover": _discover_result([modern]),
        }
    )

    client = _negotiate(server)

    assert client.protocol_version == modern
    assert server.methods == ["initialize", "server/discover"]


@modern_only
def test_a_speculative_escalation_failure_keeps_the_original_error():
    """-32601 is a guess, so its probe must not replace the server's verdict.

    A URL that is simply not an MCP endpoint answers -32601 to everything.
    Reporting the probe's error there would rename a "this isn't MCP" failure
    after a method the user has never heard of.
    """
    server = _FakeMcpServer({})  # every method answers -32601

    async def run():
        async with _client_against(server) as client:
            with pytest.raises(McpProtocolError) as excinfo:
                await client._negotiate()
            return str(excinfo.value)

    message = run_async(run())

    assert "Unknown method initialize" in message
    assert "server/discover" not in message


def test_protocol_era_mismatch_suppresses_the_try_sse_hint():
    """A version mismatch is not a transport mistake.

    ``connect()`` appends "if this is a legacy SSE endpoint, set type: sse" to
    failures on an *inferred* http URL. Following it after a protocol rejection
    earns a second identical failure, so the hint has to stay off — the same
    call the block already makes for NonMcpEndpointError and auth failures.
    """

    async def fake_connect_http(self, startup_timeout, request_timeout):
        class _Session:
            async def initialize(self):
                _raise_protocol_error(_unsupported_version_error(["2026-07-28"]))

        self._session = _Session()

    client = McpClient("svr", {"url": "https://h/mcp"})  # bare url → inferred http
    with patch.object(McpClient, "_connect_streamable_http", fake_connect_http):
        run_async(client.connect())

    assert client.status == ServerStatus.ERROR
    assert "unsupported protocol version" in (client.error_message or "")
    assert '"type": "sse"' not in (client.error_message or "")
    # And the failed connect leaves no version or session behind.
    assert client.protocol_version is None
    assert client._session is None


def test_call_tool_reports_a_failed_reconnect_instead_of_crashing():
    """``connect()`` signals failure by status, not by raising.

    Without checking that, ``call_tool`` calls straight into a ``None`` session
    and hands the model ``'NoneType' object has no attribute 'call_tool'``
    instead of the reason the connection failed.
    """

    async def failing_connect(self):
        self.status = ServerStatus.ERROR
        self.error_message = "connection refused"
        self._session = None

    client = McpClient("svr", {"url": "https://h/mcp"})
    with patch.object(McpClient, "connect", failing_connect):
        result = run_async(client.call_tool("echo", {}))

    assert "connection refused" in result
    assert "NoneType" not in result


def test_mcp_list_renders_the_protocol_and_survives_hostile_text():
    """The feature's only user-visible surface, plus its markup hazard.

    Everything on that line is parsed as Rich markup, and ``protocol`` and
    ``error`` are strings a third-party server wrote. One unmatched "[/b]" used
    to raise ``MarkupError`` out of the command and take the whole listing with
    it — including the healthy servers.
    """
    from unittest.mock import MagicMock

    from agentao.cli.commands.mcp import handle_mcp_command
    from agentao.mcp.client import McpClientManager

    manager = McpClientManager({})
    for name, status, version, error in (
        ("good", ServerStatus.CONNECTED, "2026-07-28", None),
        ("hostile", ServerStatus.ERROR, None, "boom [/b] [green]connected[/green]"),
        ("quiet", ServerStatus.DISCONNECTED, None, None),
    ):
        client = McpClient(name, {"command": "echo"})
        client.status = status
        client._protocol_version = version
        client.error_message = error
        manager._clients[name] = client

    cli = MagicMock()
    cli.agent.mcp_manager = manager

    from agentao.cli._globals import console

    with console.capture() as captured:
        handle_mcp_command(cli, "list")
    out = captured.get()

    assert "2026-07-28" in out  # present when negotiated
    assert "quiet" in out and "2026-07-28" not in out.split("quiet")[1]  # absent otherwise
    # The hostile row printed literally rather than blowing up the listing.
    assert "boom [/b] [green]connected[/green]" in out


@modern_only
def test_an_input_required_result_is_explained_not_leaked():
    """The SDK's raise is written for a programmer; the model must not see it.

    On the modern era a server needing sampling / elicitation / roots returns
    ``InputRequiredResult`` from ``tools/call``. agentao provides none of the
    three (in either era), so the call cannot complete — but the default SDK
    behaviour is ``RuntimeError("… pass allow_input_required=True … retry
    call_tool(…)")``, which ``call_tool``'s broad ``except`` would hand to the
    LLM verbatim as the tool's result.
    """
    result = mcp_types.InputRequiredResult(
        # A mapping, not a list: the keys are the ids a resolving client would
        # echo back in ``input_responses``.
        inputRequests={
            "q1": mcp_types.ElicitRequest(
                params=mcp_types.ElicitRequestFormParams(
                    message="Which account?",
                    requestedSchema={"type": "object", "properties": {}},
                )
            ),
            "q2": mcp_types.ListRootsRequest(),
        },
        requestState="opaque-resume-token",
    )
    captured: Dict[str, Any] = {}
    client = connected_client(result, capture=captured)

    text = run_async(client.call_tool("do_thing", {}))

    # Opted in to *receive* the result rather than string-matching a raise.
    assert captured["allow_input_required"] is True
    assert "do_thing" in text
    assert "elicitation" in text and "roots" in text
    # None of the SDK's programmer-facing instruction survives.
    assert "allow_input_required=True" not in text
    assert "InputRequiredResult" not in text


@modern_only
def test_an_unclaimed_extension_result_is_explained_not_leaked():
    """The sibling arm: same leak, same fix, one line away in the SDK."""
    from agentao.mcp._compat import UnexpectedClaimedResult

    client = McpClient("svr", {"command": "echo"})
    client.status = ServerStatus.CONNECTED

    class _Session:
        async def call_tool(self, name, arguments, **kwargs):
            raise UnexpectedClaimedResult(mcp_types.EmptyResult())

    client._session = _Session()

    text = run_async(client.call_tool("do_thing", {}))

    assert "do_thing" in text
    assert "extension" in text
    assert "allow_claimed" not in text
    assert "Client(extensions=" not in text


def test_protocol_version_is_reported_and_cleared():
    from agentao.mcp.client import McpClientManager

    client = McpClient("fake", {"command": "echo"})
    client.status = ServerStatus.CONNECTED
    client._protocol_version = HANDSHAKE_VERSION

    manager = McpClientManager({})
    manager._clients["fake"] = client
    assert manager.get_server_status()[0]["protocol"] == HANDSHAKE_VERSION

    run_async(client.disconnect())

    # The version belongs to the session, not the config.
    assert client.protocol_version is None
    assert manager.get_server_status()[0]["protocol"] is None
