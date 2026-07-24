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
    _MCP_USER_AGENT,
    _with_default_user_agent,
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
