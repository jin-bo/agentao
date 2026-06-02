"""Tests for the connect-time MCP content-type preflight.

A misconfigured ``url`` pointing at a plain web page makes the MCP SDK wait
out the full connect timeout (default 60 s) before failing opaquely. The
preflight probes the URL first and fails fast with an actionable
:class:`NonMcpEndpointError` when the response is unambiguously not MCP.

Detection is allow-list based and strictly best-effort: only a 2xx response
advertising a definite non-MCP content type is rejected; everything else
passes through so the real handshake stays authoritative.
"""

import asyncio
from unittest.mock import patch

import httpx
import pytest

from agentao.mcp.client import (
    McpClient,
    NonMcpEndpointError,
    ServerStatus,
)


def _run(coro):
    return asyncio.run(coro)


class _FakeResp:
    def __init__(self, status_code, content_type=None):
        self.status_code = status_code
        self.headers = {}
        if content_type is not None:
            self.headers["content-type"] = content_type


class _FakeClient:
    """Stand-in for ``httpx.AsyncClient`` as an async context manager.

    Records the requests it receives so tests can assert HEAD→GET fallback.
    """

    def __init__(self, head=None, get=None, head_exc=None):
        self._head = head
        self._get = get
        self._head_exc = head_exc
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def head(self, url, headers=None):
        self.calls.append(("head", url, headers))
        if self._head_exc is not None:
            raise self._head_exc
        return self._head

    async def get(self, url, headers=None):
        self.calls.append(("get", url, headers))
        return self._get


def _client():
    return McpClient("svr", {"url": "https://example.com/"})


def _patch_httpx(fake):
    return patch("httpx.AsyncClient", return_value=fake)


# ---------------------------------------------------------------------------
# Reject: 2xx with a definite non-MCP content type
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ct", ["text/html", "text/plain", "application/xml"])
def test_rejects_non_mcp_content_type(ct):
    fake = _FakeClient(head=_FakeResp(200, ct))
    with _patch_httpx(fake):
        with pytest.raises(NonMcpEndpointError) as exc:
            _run(_client()._preflight_content_type("https://example.com/", {}))
    # Actionable: names the offending type and the server.
    assert ct in str(exc.value)
    assert "svr" in str(exc.value)


def test_rejects_html_with_charset_parameter():
    """``content-type`` may carry a ``; charset=…`` suffix — strip it."""
    fake = _FakeClient(head=_FakeResp(200, "text/html; charset=utf-8"))
    with _patch_httpx(fake):
        with pytest.raises(NonMcpEndpointError):
            _run(_client()._preflight_content_type("https://example.com/", {}))


# ---------------------------------------------------------------------------
# Pass through: anything not an unambiguous web page
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ct", ["application/json", "text/event-stream"])
def test_allows_mcp_content_types(ct):
    fake = _FakeClient(head=_FakeResp(200, ct))
    with _patch_httpx(fake):
        # No raise.
        _run(_client()._preflight_content_type("https://example.com/", {}))


def test_allows_mcp_content_type_case_insensitive():
    fake = _FakeClient(head=_FakeResp(200, "Application/JSON"))
    with _patch_httpx(fake):
        _run(_client()._preflight_content_type("https://example.com/", {}))


def test_missing_content_type_passes():
    """No content type advertised → don't second-guess the SDK."""
    fake = _FakeClient(head=_FakeResp(200, None))
    with _patch_httpx(fake):
        _run(_client()._preflight_content_type("https://example.com/", {}))


@pytest.mark.parametrize("status", [401, 403, 404, 500, 503])
def test_non_2xx_passes(status):
    """A 4xx/5xx may be an auth challenge or transient error the real
    handshake handles — the preflight must not pre-empt it, even if the
    error page itself is HTML."""
    fake = _FakeClient(head=_FakeResp(status, "text/html"))
    with _patch_httpx(fake):
        _run(_client()._preflight_content_type("https://example.com/", {}))


def test_transport_error_passes():
    """DNS / connect / timeout errors are the SDK's job — swallow them so a
    real-but-slow endpoint still gets its full connect budget."""
    fake = _FakeClient(head_exc=httpx.ConnectError("name resolution failed"))
    with _patch_httpx(fake):
        _run(_client()._preflight_content_type("https://example.com/", {}))


def test_invalid_url_passes():
    """httpx.InvalidURL is not an HTTPError subclass; it must still pass
    through so the SDK handshake stays authoritative for a bad url."""
    fake = _FakeClient(head_exc=httpx.InvalidURL("no host"))
    with _patch_httpx(fake):
        _run(_client()._preflight_content_type("not-a-url", {}))


# ---------------------------------------------------------------------------
# Accept header: probe as an MCP client to avoid false-positive rejection
# ---------------------------------------------------------------------------

def test_probe_sends_mcp_accept_header():
    """A content-negotiating server must see an MCP-shaped Accept so it
    returns its real body rather than a default HTML page we'd reject."""
    fake = _FakeClient(head=_FakeResp(200, "application/json"))
    with _patch_httpx(fake):
        _run(_client()._preflight_content_type("https://example.com/", {}))
    sent = fake.calls[0][2]
    assert sent["Accept"] == "application/json, text/event-stream"


def test_caller_accept_header_wins():
    """A caller-supplied Accept overrides the probe default."""
    fake = _FakeClient(head=_FakeResp(200, "application/json"))
    with _patch_httpx(fake):
        _run(
            _client()._preflight_content_type(
                "https://example.com/", {"Accept": "application/json"}
            )
        )
    assert fake.calls[0][2]["Accept"] == "application/json"


# ---------------------------------------------------------------------------
# HEAD → GET fallback
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("head_status", [405, 501])
def test_head_falls_back_to_get(head_status):
    fake = _FakeClient(
        head=_FakeResp(head_status),
        get=_FakeResp(200, "text/html"),
    )
    with _patch_httpx(fake):
        with pytest.raises(NonMcpEndpointError):
            _run(_client()._preflight_content_type("https://example.com/", {}))
    assert [c[0] for c in fake.calls] == ["head", "get"]


def test_head_success_skips_get():
    fake = _FakeClient(
        head=_FakeResp(200, "application/json"),
        get=_FakeResp(200, "text/html"),
    )
    with _patch_httpx(fake):
        _run(_client()._preflight_content_type("https://example.com/", {}))
    assert [c[0] for c in fake.calls] == ["head"]


# ---------------------------------------------------------------------------
# Integration: connect() surfaces the preflight verdict as ERROR
# ---------------------------------------------------------------------------

def test_connect_marks_error_and_does_not_raise():
    """A non-MCP URL must leave the client in ERROR with the actionable
    message in ``error_message`` — connect() swallows so connect_all() keeps
    going, and the verdict is visible via get_server_status()."""
    fake = _FakeClient(head=_FakeResp(200, "text/html"))
    client = _client()
    with _patch_httpx(fake):
        _run(client.connect())
    assert client.status is ServerStatus.ERROR
    assert "not an MCP response" in (client.error_message or "")
    assert client.tools == []


def test_connect_does_not_retry_preflight_failure():
    """connect() runs the preflight exactly once — there is no connect-time
    retry loop for it to slip into (non-retryable by construction)."""
    fake = _FakeClient(head=_FakeResp(200, "text/html"))
    client = _client()
    with _patch_httpx(fake):
        _run(client.connect())
    assert [(c[0], c[1]) for c in fake.calls] == [("head", "https://example.com/")]
