"""Connect-path timeout wiring for McpClient (opencode 6/29 review fixes).

- ``_connect_sse`` forwards the resolved *startup* timeout to ``sse_client(timeout=)``
  and raises ``sse_read_timeout`` to cover a large per-request budget (never
  lowering it below the SDK default).
- ``connect()`` bounds the ``initialize()`` / ``list_tools()`` handshake with the
  startup budget, so a server that opens the stream but never answers can't hang
  connect forever.
"""

import asyncio
from contextlib import AsyncExitStack
from unittest.mock import MagicMock, patch

from agentao.mcp.client import (
    _DEFAULT_SSE_READ_TIMEOUT,
    McpClient,
    ServerStatus,
)
from agentao.mcp.config import resolve_timeouts
from tests.support.mcp import run_async


class _FakeCM:
    """Minimal async context manager yielding a fixed value."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc):
        return False


def _connect_sse_capturing(config):
    """Run ``_connect_sse`` with sse_client/ClientSession/preflight stubbed,
    returning the kwargs ``sse_client`` was called with.
    """
    captured = {}

    def fake_sse_client(url, headers=None, timeout=None, sse_read_timeout=None):
        captured.update(url=url, timeout=timeout, sse_read_timeout=sse_read_timeout)
        return _FakeCM(("read", "write"))

    def fake_client_session(read, write):
        return _FakeCM(MagicMock(name="session"))

    async def no_preflight(self, url, headers):
        return None

    client = McpClient("svr", config)
    startup_timeout, request_timeout = resolve_timeouts(config)

    async def run():
        async with AsyncExitStack() as stack:
            client._exit_stack = stack
            with patch("agentao.mcp.client.sse_client", fake_sse_client), patch(
                "agentao.mcp.client.ClientSession", fake_client_session
            ), patch.object(McpClient, "_preflight_content_type", no_preflight):
                await client._connect_sse(startup_timeout, request_timeout)

    run_async(run())
    return captured


# ---------------------------------------------------------------------------
# _connect_sse forwards startup → sse_client(timeout=)  (F9)
# ---------------------------------------------------------------------------

def test_connect_sse_forwards_startup_timeout():
    captured = _connect_sse_capturing({"url": "https://h/mcp", "timeout": {"startup": 15}})
    assert captured["timeout"] == 15.0


def test_connect_sse_legacy_int_is_startup_timeout():
    captured = _connect_sse_capturing({"url": "https://h/mcp", "timeout": 25})
    assert captured["timeout"] == 25.0


def test_connect_sse_default_timeout_when_unset():
    captured = _connect_sse_capturing({"url": "https://h/mcp"})
    assert captured["timeout"] == 60.0


# ---------------------------------------------------------------------------
# sse_read_timeout tracks a large request budget, never lowered  (F3)
# ---------------------------------------------------------------------------

def test_sse_read_timeout_default_when_request_unset():
    captured = _connect_sse_capturing({"url": "https://h/mcp"})
    assert captured["sse_read_timeout"] == _DEFAULT_SSE_READ_TIMEOUT


def test_sse_read_timeout_raised_for_large_request():
    captured = _connect_sse_capturing(
        {"url": "https://h/mcp", "timeout": {"startup": 15, "request": 600}}
    )
    assert captured["sse_read_timeout"] == 600.0


def test_sse_read_timeout_not_lowered_for_small_request():
    captured = _connect_sse_capturing({"url": "https://h/mcp", "timeout": {"request": 10}})
    assert captured["sse_read_timeout"] == _DEFAULT_SSE_READ_TIMEOUT


# ---------------------------------------------------------------------------
# connect() bounds the initialize()/list_tools() handshake  (F2)
# ---------------------------------------------------------------------------

def test_connect_times_out_on_slow_handshake():
    class _SlowSession:
        async def initialize(self):
            await asyncio.sleep(5)

        async def list_tools(self):  # pragma: no cover - never reached
            return MagicMock(tools=[])

    async def fake_connect_sse(self, startup_timeout, request_timeout):
        self._session = _SlowSession()

    client = McpClient(
        "svr", {"type": "sse", "url": "https://h/mcp", "timeout": {"startup": 0.05}}
    )
    with patch.object(McpClient, "_connect_sse", fake_connect_sse):
        run_async(client.connect())

    assert client.status == ServerStatus.ERROR
    assert "handshake" in (client.error_message or "")


def test_connect_succeeds_within_startup_budget():
    tools = [MagicMock()]

    async def fake_connect_sse(self, startup_timeout, request_timeout):
        async def _init():
            return None

        async def _list():
            return MagicMock(tools=tools)

        sess = MagicMock(name="session")
        sess.initialize = _init
        sess.list_tools = _list
        self._session = sess

    client = McpClient(
        "svr", {"type": "sse", "url": "https://h/mcp", "timeout": {"startup": 5}}
    )
    with patch.object(McpClient, "_connect_sse", fake_connect_sse):
        run_async(client.connect())

    assert client.status == ServerStatus.CONNECTED
    assert client.tools == tools
