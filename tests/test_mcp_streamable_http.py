"""Streamable HTTP transport support for McpClient.

Covers the design in ``docs/design/mcp-streamable-http.md``:

- ``resolve_transport`` — the ``type`` selector, alias folding, the D2 default
  (bare ``url`` → Streamable HTTP), and **fail-closed** behavior on an unknown
  ``type`` or a missing required key.
- ``McpClient.transport_type`` — display-only, never raises.
- ``connect()`` dispatch — ``type:"http"`` and bare ``url`` route to
  ``_connect_streamable_http``, ``type:"sse"`` to ``_connect_sse``; timeout /
  ``sse_read_timeout`` / ``terminate_on_close`` wiring.
- The §5.7 bare-``url``-defaulted-to-http connect hint and its gating.
- The stream-tuple **arity split** across SDK majors — see
  :data:`_HTTP_STREAM_SHAPES`.
"""

import asyncio
import importlib.metadata as md
from unittest.mock import patch

import pytest


# The two shapes ``streamable_http_client`` actually yields. mcp 1.x yields
# ``(read, write, get_session_id)``; 2.0 dropped the third element so every
# transport now yields the same 2-tuple (``mcp.client._transport
# .TransportStreams``). Production must accept either, and this used to be
# hardcoded to the 3-tuple — which is why a hard ``ValueError: not enough
# values to unpack`` on mcp 2.0 sailed through a green suite.
_MCP1_STREAMS = ("r", "w", lambda: "the-sid")
_MCP2_STREAMS = ("r", "w")
_HTTP_STREAM_SHAPES = [
    pytest.param(_MCP1_STREAMS, id="mcp1-3tuple"),
    pytest.param(_MCP2_STREAMS, id="mcp2-2tuple"),
]


def _installed_http_streams():
    """The shape the *installed* SDK yields, so the default path tests reality.

    Derived from the distribution version rather than by importing the private
    ``_transport`` alias, which only exists on 2.x.
    """
    return _MCP1_STREAMS if int(md.version("mcp").split(".")[0]) < 2 else _MCP2_STREAMS

from agentao.mcp.client import (
    _DEFAULT_SSE_READ_TIMEOUT,
    _MCP_USER_AGENT,
    _first_failure,
    _is_unfollowed_redirect,
    _with_default_user_agent,
    McpClient,
    NonMcpEndpointError,
    ServerStatus,
)
from agentao.mcp.config import McpTransportConfigError, resolve_transport
from tests.support.mcp import initialize_result, run_async, tools_result

# Distinctive substring of the §5.7 connect hint.
_HINT_MARKER = "tried as Streamable HTTP"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeCM:
    """Async context manager yielding a fixed value, or raising on enter."""

    def __init__(self, value, *, aenter_exc=None):
        self._value = value
        self._aenter_exc = aenter_exc

    async def __aenter__(self):
        if self._aenter_exc is not None:
            raise self._aenter_exc
        return self._value

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    # No ``discover``, and none is needed: these tests are about transport
    # dispatch, and ``_negotiate`` only reaches for it after a protocol
    # rejection this session never produces (``_can_discover`` also checks the
    # live object, so an injected session without it is never called into).
    async def initialize(self):
        return initialize_result()

    # ``params`` is keyword-only on the real ``ClientSession.list_tools`` across
    # every supported SDK major, and agentao always passes it.
    async def list_tools(self, *, params=None):
        return tools_result([])


class _FakeHttpClient:
    """Stand-in for the httpx client from ``create_mcp_http_client`` — entered
    into the exit stack (async CM) and passed to ``streamable_http_client``."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _drive_connect(
    config,
    *,
    http_streams=None,
    http_aenter_exc=None,
    sse_aenter_exc=None,
    preflight_exc=None,
):
    """Run ``client.connect()`` with both transports, the http-client factory,
    ClientSession, and the preflight stubbed. Returns ``(client, captured)``:
    ``captured["http"]`` records the ``streamable_http_client`` args (url,
    terminate_on_close), ``captured["client"]`` the ``create_mcp_http_client``
    args (headers, timeout), ``captured["sse"]`` the ``sse_client`` args.

    ``http_streams`` defaults to whatever the installed SDK really yields, so
    tests that don't care about arity still exercise the true shape.
    """
    if http_streams is None:
        http_streams = _installed_http_streams()
    captured = {}

    def fake_create_client(headers=None, timeout=None, auth=None):
        captured["client"] = dict(headers=headers, timeout=timeout, auth=auth)
        return _FakeHttpClient()

    def fake_http(url, *, http_client=None, terminate_on_close=True):
        captured["http"] = dict(
            url=url, http_client=http_client, terminate_on_close=terminate_on_close
        )
        return _FakeCM(http_streams, aenter_exc=http_aenter_exc)

    def fake_sse(url, headers=None, timeout=None, sse_read_timeout=None):
        captured["sse"] = dict(
            url=url, headers=headers, timeout=timeout, sse_read_timeout=sse_read_timeout
        )
        return _FakeCM(("r", "w"), aenter_exc=sse_aenter_exc)

    def fake_session(read, write):
        return _FakeCM(_FakeSession())

    async def preflight(self, url, headers):
        if preflight_exc is not None:
            raise preflight_exc
        return None

    client = McpClient("svr", config)
    with patch("agentao.mcp.client.streamable_http_client", fake_http), patch(
        "agentao.mcp.client.create_mcp_http_client", fake_create_client
    ), patch("agentao.mcp.client.sse_client", fake_sse), patch(
        "agentao.mcp.client.ClientSession", fake_session
    ), patch.object(
        McpClient, "_preflight_content_type", preflight
    ):
        run_async(client.connect())
    return client, captured


# ---------------------------------------------------------------------------
# resolve_transport — happy paths
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "config,expected",
    [
        ({"command": "echo"}, "stdio"),
        ({"type": "stdio", "command": "echo"}, "stdio"),
        ({"type": "sse", "url": "u"}, "sse"),
        ({"type": "http", "url": "u"}, "http"),
        ({"url": "u"}, "http"),  # D2 default: bare url → Streamable HTTP
        ({"type": "streamable-http", "url": "u"}, "http"),
        ({"type": "streamable_http", "url": "u"}, "http"),
        ({"type": "streamablehttp", "url": "u"}, "http"),
        ({"type": "HTTP", "url": "u"}, "http"),  # case-insensitive
        ({"type": "  Http ", "url": "u"}, "http"),  # trimmed
        ({}, "unknown"),  # no type, no keys
    ],
)
def test_resolve_transport_happy(config, expected):
    assert resolve_transport(config) == expected


def test_resolve_transport_return_source():
    assert resolve_transport({"type": "http", "url": "u"}, return_source=True) == (
        "http",
        "explicit",
    )
    assert resolve_transport({"url": "u"}, return_source=True) == ("http", "inferred")
    assert resolve_transport({"command": "e"}, return_source=True) == (
        "stdio",
        "inferred",
    )


# ---------------------------------------------------------------------------
# resolve_transport — fail closed (Findings 1 & 3)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["see", "streamable", "websocket", "ws", ""])
def test_resolve_transport_unknown_type_fails_closed(bad):
    # A typo must NOT silently become the http default.
    with pytest.raises(McpTransportConfigError):
        resolve_transport({"type": bad, "url": "u"})


def test_resolve_transport_non_string_type_fails_closed():
    with pytest.raises(McpTransportConfigError):
        resolve_transport({"type": 123, "url": "u"})


@pytest.mark.parametrize(
    "config",
    [
        {"type": "http"},  # missing url
        {"type": "sse"},  # missing url
        {"type": "stdio"},  # missing command
        {"type": "http", "command": "echo"},  # http but only command
        {"type": "stdio", "url": "u"},  # stdio but only url
    ],
)
def test_resolve_transport_missing_required_key_fails_closed(config):
    with pytest.raises(McpTransportConfigError):
        resolve_transport(config)


# ---------------------------------------------------------------------------
# transport_type property — display-only, never raises
# ---------------------------------------------------------------------------

def test_transport_type_property_returns_unknown_on_bad_type():
    assert McpClient("svr", {"type": "bogus", "url": "u"}).transport_type == "unknown"


def test_transport_type_property_returns_unknown_on_missing_key():
    assert McpClient("svr", {"type": "http"}).transport_type == "unknown"


def test_transport_type_property_happy():
    assert McpClient("svr", {"url": "u"}).transport_type == "http"
    assert McpClient("svr", {"type": "sse", "url": "u"}).transport_type == "sse"
    assert McpClient("svr", {"command": "e"}).transport_type == "stdio"


# ---------------------------------------------------------------------------
# connect() dispatch + 3-tuple + timeout wiring
# ---------------------------------------------------------------------------

def test_bare_url_dispatches_streamable_http():
    client, captured = _drive_connect({"url": "https://h/mcp"})
    assert client.status == ServerStatus.CONNECTED
    assert "http" in captured and "sse" not in captured
    assert client.transport_type == "http"


def test_explicit_http_dispatches_streamable_http():
    client, captured = _drive_connect({"type": "http", "url": "https://h/mcp"})
    assert client.status == ServerStatus.CONNECTED
    assert "http" in captured and "sse" not in captured


def test_sse_dispatches_sse():
    client, captured = _drive_connect({"type": "sse", "url": "https://h/sse"})
    assert client.status == ServerStatus.CONNECTED
    assert "sse" in captured and "http" not in captured


@pytest.mark.parametrize("http_streams", _HTTP_STREAM_SHAPES)
def test_streamable_http_connects_on_either_stream_arity(http_streams):
    """Both real yield shapes must connect — see :data:`_HTTP_STREAM_SHAPES`.

    On 1.x the trailing ``get_session_id`` is discarded, never required; on
    2.0 it is simply absent. Indexing rather than unpacking in
    ``_connect_streamable_http`` is what makes both work.
    """
    client, _ = _drive_connect(
        {"type": "http", "url": "https://h/mcp"}, http_streams=http_streams
    )
    assert client.status == ServerStatus.CONNECTED
    assert client.error_message is None


def test_streamable_http_timeout_and_terminate_on_close():
    _, captured = _drive_connect(
        {"type": "http", "url": "https://h/mcp", "timeout": {"startup": 15, "request": 600}}
    )
    # startup → connect timeout; request (>default) → the stream read timeout.
    timeout = captured["client"]["timeout"]
    assert timeout.connect == 15.0
    assert timeout.read == 600.0
    # terminate_on_close is False: the teardown DELETE would otherwise reuse the
    # long read timeout and could block disconnect/reconnect for that window.
    assert captured["http"]["terminate_on_close"] is False


def test_streamable_http_default_timeouts():
    _, captured = _drive_connect({"type": "http", "url": "https://h/mcp"})
    timeout = captured["client"]["timeout"]
    assert timeout.connect == 60.0
    assert timeout.read == _DEFAULT_SSE_READ_TIMEOUT


# ---------------------------------------------------------------------------
# §5.7 connect hint gating
# ---------------------------------------------------------------------------

def test_hint_appended_on_inferred_http_handshake_failure():
    client, _ = _drive_connect(
        {"url": "https://h/mcp"}, http_aenter_exc=RuntimeError("boom")
    )
    assert client.status == ServerStatus.ERROR
    assert _HINT_MARKER in (client.error_message or "")


def test_hint_not_appended_for_explicit_http():
    client, _ = _drive_connect(
        {"type": "http", "url": "https://h/mcp"}, http_aenter_exc=RuntimeError("boom")
    )
    assert client.status == ServerStatus.ERROR
    assert _HINT_MARKER not in (client.error_message or "")


def test_hint_not_appended_for_sse():
    client, _ = _drive_connect(
        {"type": "sse", "url": "https://h/sse"}, sse_aenter_exc=RuntimeError("boom")
    )
    assert client.status == ServerStatus.ERROR
    assert _HINT_MARKER not in (client.error_message or "")


def test_hint_not_appended_on_non_mcp_endpoint_error():
    # Preflight verdict already says "not MCP" — don't override it with an SSE
    # suggestion (Finding 4). Runs on the http path (bare url → http).
    client, _ = _drive_connect(
        {"url": "https://h/page"}, preflight_exc=NonMcpEndpointError("looks like html")
    )
    assert client.status == ServerStatus.ERROR
    assert _HINT_MARKER not in (client.error_message or "")
    assert "looks like html" in (client.error_message or "")


def test_hint_not_appended_on_auth_failure():
    # A real Streamable HTTP server that 401s is not fixed by switching to SSE —
    # the hint would send the user down a wrong path (Finding 5).
    client, _ = _drive_connect(
        {"url": "https://h/mcp"}, http_aenter_exc=RuntimeError("401 Unauthorized")
    )
    assert client.status == ServerStatus.ERROR
    assert _HINT_MARKER not in (client.error_message or "")


# mcp 2.2 and 1.30 follow a redirect only within the endpoint's origin. The
# probe is the function that composes the refusal, so the tests below take
# their input from the real producer; on an SDK without it there is nothing to
# refuse.
try:
    from mcp.client.streamable_http import _unfollowed_redirect
except ImportError:  # mcp < 1.30 on 1.x, < 2.2 on 2.x
    _unfollowed_redirect = None

_needs_origin_rule = pytest.mark.skipif(
    _unfollowed_redirect is None, reason="installed mcp follows every redirect"
)


@_needs_origin_rule
@pytest.mark.parametrize(
    "endpoint, location",
    [
        pytest.param("https://h/mcp", "https://other/mcp", id="cross-origin"),
        pytest.param("https://h/mcp", "http://h/mcp/", id="https-downgrade"),
    ],
)
def test_unfollowed_redirect_matches_both_sdk_messages(endpoint, location):
    from agentao.mcp._compat import httpx_for_mcp

    # A client fills in ``next_request``, which is what the SDK reads; a bare
    # ``Response(...)`` has none and would describe no redirect at all.
    transport = httpx_for_mcp.MockTransport(
        lambda request: httpx_for_mcp.Response(307, headers={"location": location})
    )
    with httpx_for_mcp.Client(transport=transport, follow_redirects=False) as http:
        response = http.post(endpoint)
    message = _unfollowed_redirect(response)
    assert message is not None
    assert _is_unfollowed_redirect(RuntimeError(message))


def test_unfollowed_redirect_ignores_other_failures():
    assert not _is_unfollowed_redirect(RuntimeError("connection reset by peer"))
    assert not _is_unfollowed_redirect(RuntimeError("Session terminated"))
    # The frame must lead: a server echoing the phrase mid-message is not it.
    assert not _is_unfollowed_redirect(
        RuntimeError("upstream said: Redirect to x not followed")
    )


@_needs_origin_rule
def test_hint_not_appended_on_unfollowed_redirect():
    # Drives the real SDK transport over a mocked socket: the endpoint answers
    # 307 to another origin, 2.2 / 1.30 refuse to follow it, and connect() must
    # surface the SDK's "use that URL" message without the SSE hint (the SSE
    # client applies the same origin rule, so the hint cannot help).
    from agentao.mcp._compat import httpx_for_mcp

    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx_for_mcp.Response(307, headers={"location": "https://other/mcp"})

    def mock_client(headers=None, timeout=None, auth=None):
        return httpx_for_mcp.AsyncClient(
            headers=headers, timeout=timeout, transport=httpx_for_mcp.MockTransport(handler)
        )

    async def no_preflight(self, url, headers):
        return None

    client = McpClient("svr", {"url": "https://h/mcp", "timeout": 5})  # inferred http
    with patch("agentao.mcp.client.create_mcp_http_client", mock_client), patch.object(
        McpClient, "_preflight_content_type", no_preflight
    ):
        run_async(client.connect())

    assert client.status == ServerStatus.ERROR
    assert (client.error_message or "").startswith("Redirect to https://other/mcp not followed")
    assert _HINT_MARKER not in (client.error_message or "")
    assert seen and all(url.startswith("https://h/") for url in seen)  # never left the origin


def _connect_over_mock_socket(handler, config=None):
    """Run ``connect()`` through the real SDK transport over a mocked socket.

    Only the socket is replaced (and the preflight skipped): the SDK's
    Streamable HTTP client, its task group and ``ClientSession`` are the real
    ones, which is where 1.x and 2.x differ in how a failed request surfaces.
    """
    from agentao.mcp._compat import httpx_for_mcp

    def mock_client(headers=None, timeout=None, auth=None):
        return httpx_for_mcp.AsyncClient(
            headers=headers, timeout=timeout, transport=httpx_for_mcp.MockTransport(handler)
        )

    async def no_preflight(self, url, headers):
        return None

    return (
        patch("agentao.mcp.client.create_mcp_http_client", mock_client),
        patch.object(McpClient, "_preflight_content_type", no_preflight),
        McpClient("svr", config or {"url": "https://h/mcp", "timeout": 5}),
    )


def test_an_http_error_on_the_handshake_is_reported_not_swallowed():
    # On mcp 1.x the transport's task group cancelled the handshake and kept
    # the 500 to itself, so connect() ended DISCONNECTED with no message and
    # call_tool could say only "reconnect failed". Every SDK must now report it.
    from agentao.mcp._compat import httpx_for_mcp

    def handler(request):
        return httpx_for_mcp.Response(500, text="boom")

    client_patch, preflight_patch, client = _connect_over_mock_socket(handler)

    async def run():
        await client.connect()
        return await client.call_tool("anything", {})

    with client_patch, preflight_patch:
        result = run_async(run())

    assert client.status == ServerStatus.ERROR
    assert client.error_message
    if int(md.version("mcp").split(".")[0]) < 2:
        assert "500" in client.error_message  # the transport's own HTTPStatusError
    assert "reconnect failed" not in result
    assert client.error_message.splitlines()[0] in result
    assert client._exit_stack is None


def test_cancelling_a_connect_in_flight_is_still_a_cancel():
    # The recovery above exits the transport with the cancel passed in and
    # relies on anyio absorbing only a cancel its own scope delivered. A
    # cancel from outside — ``_stop_owner`` aborting a connect — must come
    # back out as a cancel, not be reported as a failed connect.
    from agentao.mcp._compat import httpx_for_mcp

    async def handler(request):
        await asyncio.Event().wait()  # the server never answers
        return httpx_for_mcp.Response(200)

    client_patch, preflight_patch, client = _connect_over_mock_socket(handler)

    async def run():
        connecting = asyncio.create_task(client.connect())
        for _ in range(200):
            if client.status == ServerStatus.CONNECTING and client._exit_stack is not None:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)  # let the POST reach the handler
        owner = client._owner
        owner.cancel()
        await asyncio.wait({owner}, timeout=5)
        await asyncio.wait_for(connecting, 5)
        return owner

    with client_patch, preflight_patch:
        owner = run_async(run())

    assert owner.done() and owner.cancelled()
    assert client.status == ServerStatus.DISCONNECTED
    assert client.error_message is None


# The group type anyio raises: the builtin from 3.11, the backport on 3.10.
try:
    _ExceptionGroup = ExceptionGroup
except NameError:  # pragma: no cover - Python 3.10
    from exceptiongroup import ExceptionGroup as _ExceptionGroup


def test_first_failure_digs_through_nested_groups_past_cancellations():
    cause = RuntimeError("Server error '500 Internal Server Error'")
    group = _ExceptionGroup(
        "outer", [_ExceptionGroup("inner", [cause]), ValueError("later")]
    )
    assert _first_failure(group) is cause
    assert _first_failure(cause) is cause
    assert _first_failure(asyncio.CancelledError()) is None


def test_a_failure_raised_as_a_group_is_reported_by_its_cause():
    # mcp 1.x's ``sse_client`` raises a failed open as a task-group error; the
    # message used to be "unhandled errors in a TaskGroup (1 sub-exception)".
    cause = RuntimeError("Server error '500 Internal Server Error' for url 'https://h/sse'")

    async def failing_sse(self, startup_timeout, request_timeout):
        raise _ExceptionGroup("unhandled errors in a TaskGroup", [cause])

    client = McpClient("svr", {"type": "sse", "url": "https://h/sse"})
    with patch.object(McpClient, "_connect_sse", failing_sse):
        run_async(client.connect())

    assert client.status == ServerStatus.ERROR
    assert client.error_message == str(cause)


def test_bad_type_fails_closed_at_connect_no_hint_no_dispatch():
    client, captured = _drive_connect({"type": "bogus", "url": "https://h/mcp"})
    assert client.status == ServerStatus.ERROR
    assert "Unknown MCP transport" in (client.error_message or "")
    assert _HINT_MARKER not in (client.error_message or "")
    assert captured == {}  # resolve_transport raised before any factory ran


# ---------------------------------------------------------------------------
# CLI /mcp add flag parsing
# ---------------------------------------------------------------------------

def _cli_add(tmp_path, args):
    from types import SimpleNamespace

    from agentao.cli.commands.mcp import handle_mcp_command
    from agentao.mcp.config import _load_json_file

    cli = SimpleNamespace(agent=SimpleNamespace(working_directory=tmp_path))
    handle_mcp_command(cli, args)
    cfg = _load_json_file(tmp_path / ".agentao" / "mcp.json")
    return cfg.get("mcpServers", {})


def test_cli_add_bare_url_writes_no_type(tmp_path):
    # Bare url stays "inferred" (no type) so the connect-failure SSE hint can
    # fire if it turns out to be a legacy SSE endpoint (Finding 4).
    servers = _cli_add(tmp_path, "add remote https://h/mcp")
    assert servers["remote"] == {"url": "https://h/mcp"}


def test_cli_add_flag_after_name(tmp_path):
    # The transport flag is honored after the name, not only as the first token
    # (Finding 2) — this must NOT become a stdio {command: "--http"} config.
    servers = _cli_add(tmp_path, "add gh --http https://h/mcp")
    assert servers["gh"] == {"type": "http", "url": "https://h/mcp"}


def test_cli_add_flag_before_name(tmp_path):
    servers = _cli_add(tmp_path, "add --sse legacy https://h/sse")
    assert servers["legacy"] == {"type": "sse", "url": "https://h/sse"}


def test_cli_add_stdio_unaffected(tmp_path):
    servers = _cli_add(tmp_path, "add fs npx -y server")
    assert servers["fs"]["command"] == "npx"
    assert servers["fs"]["args"] == ["-y", "server"]


# ---------------------------------------------------------------------------
# Default User-Agent for URL-transport MCP requests (#34883 borrow)
# ---------------------------------------------------------------------------

def test_user_agent_constant_is_named_and_versioned():
    # Server operators identify agentao by this string; keep the name/version
    # shape so it stays greppable in their logs.
    assert _MCP_USER_AGENT.startswith("agentao-mcp/")
    assert _MCP_USER_AGENT != "agentao-mcp/"  # a real version is appended


def test_with_default_user_agent_adds_when_absent():
    assert _with_default_user_agent(None) == {"User-Agent": _MCP_USER_AGENT}
    assert _with_default_user_agent({}) == {"User-Agent": _MCP_USER_AGENT}


def test_with_default_user_agent_added_alongside_other_headers():
    result = _with_default_user_agent({"Authorization": "Bearer x"})
    assert result["Authorization"] == "Bearer x"
    assert result["User-Agent"] == _MCP_USER_AGENT


@pytest.mark.parametrize("name", ["User-Agent", "user-agent", "USER-AGENT", "User-agent"])
def test_with_default_user_agent_preserves_configured_value_any_casing(name):
    # HTTP header names are case-insensitive: a configured UA under any casing
    # wins, and no duplicate canonical-cased default is added beside it.
    result = _with_default_user_agent({name: "my-client/9"})
    assert result[name] == "my-client/9"
    ua_keys = [k for k in result if k.lower() == "user-agent"]
    assert ua_keys == [name]


def test_with_default_user_agent_does_not_mutate_input():
    original = {"Authorization": "Bearer x"}
    _with_default_user_agent(original)
    assert original == {"Authorization": "Bearer x"}  # UA did not leak back in


def test_streamable_http_sends_default_user_agent():
    _, captured = _drive_connect({"url": "https://h/mcp"})
    assert captured["client"]["headers"]["User-Agent"] == _MCP_USER_AGENT


def test_sse_sends_default_user_agent():
    _, captured = _drive_connect({"type": "sse", "url": "https://h/sse"})
    assert captured["sse"]["headers"]["User-Agent"] == _MCP_USER_AGENT


def test_preflight_probe_receives_default_user_agent():
    # The UA is injected before the preflight call, so the probe identifies
    # agentao too — not only the handshake and tool calls.
    seen = {}

    async def capture_preflight(self, url, headers):
        seen["headers"] = headers

    client = McpClient("svr", {"url": "https://h/mcp"})
    with patch.object(McpClient, "_preflight_content_type", capture_preflight):
        _url, headers, _timeout = run_async(client._prepare_url_connect(60.0, None))
    assert seen["headers"]["User-Agent"] == _MCP_USER_AGENT
    assert headers["User-Agent"] == _MCP_USER_AGENT  # same headers reach the transport


def test_connect_does_not_mutate_configured_headers():
    # The default UA must not leak back into the caller-owned config, which is
    # re-read on every reconnect.
    config = {"url": "https://h/mcp", "headers": {"Authorization": "Bearer x"}}
    _drive_connect(config)
    assert config["headers"] == {"Authorization": "Bearer x"}
