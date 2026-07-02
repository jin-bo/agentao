"""Streamable HTTP transport support for McpClient.

Covers the design in ``docs/design/mcp-streamable-http.md``:

- ``resolve_transport`` — the ``type`` selector, alias folding, the D2 default
  (bare ``url`` → Streamable HTTP), and **fail-closed** behavior on an unknown
  ``type`` or a missing required key.
- ``McpClient.transport_type`` — display-only, never raises.
- ``connect()`` dispatch — ``type:"http"`` and bare ``url`` route to
  ``_connect_streamable_http`` (3-tuple unpack), ``type:"sse"`` to
  ``_connect_sse``; timeout / ``sse_read_timeout`` / ``terminate_on_close``
  wiring.
- The §5.7 bare-``url``-defaulted-to-http connect hint and its gating.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agentao.mcp.client import (
    _DEFAULT_SSE_READ_TIMEOUT,
    McpClient,
    NonMcpEndpointError,
    ServerStatus,
)
from agentao.mcp.config import McpTransportConfigError, resolve_transport
from tests.support.mcp import run_async

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
    async def initialize(self):
        return None

    async def list_tools(self):
        return SimpleNamespace(tools=[])


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
    http_streams=("r", "w", lambda: "sid"),
    http_aenter_exc=None,
    sse_aenter_exc=None,
    preflight_exc=None,
):
    """Run ``client.connect()`` with both transports, the http-client factory,
    ClientSession, and the preflight stubbed. Returns ``(client, captured)``:
    ``captured["http"]`` records the ``streamable_http_client`` args (url,
    terminate_on_close), ``captured["client"]`` the ``create_mcp_http_client``
    args (headers, timeout), ``captured["sse"]`` the ``sse_client`` args.
    """
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


def test_streamable_http_three_tuple_unpacked():
    # A 3-tuple (with a get_session_id callback) must connect cleanly; the
    # callback is discarded, never required.
    client, _ = _drive_connect(
        {"type": "http", "url": "https://h/mcp"},
        http_streams=("r", "w", lambda: "the-sid"),
    )
    assert client.status == ServerStatus.CONNECTED


def test_streamable_http_timeout_and_terminate_on_close():
    _, captured = _drive_connect(
        {"type": "http", "url": "https://h/mcp", "timeout": {"startup": 15, "request": 600}}
    )
    # startup → connect timeout; request (>default) → the stream read timeout.
    timeout = captured["client"]["timeout"]
    assert timeout.connect == 15.0
    assert timeout.read == 600.0
    assert captured["http"]["terminate_on_close"] is True


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


def test_bad_type_fails_closed_at_connect_no_hint_no_dispatch():
    client, captured = _drive_connect({"type": "bogus", "url": "https://h/mcp"})
    assert client.status == ServerStatus.ERROR
    assert "Unknown MCP transport" in (client.error_message or "")
    assert _HINT_MARKER not in (client.error_message or "")
    assert captured == {}  # resolve_transport raised before any factory ran
