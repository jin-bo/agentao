"""Tests for MCP error classification helpers.

The classifier turns the old "retry on any first-attempt exception"
loop into a classified policy: a session the server refused reconnects
and retries, a dropped transport reports the result as unknown and is
not sent again (the server can have run it), auth failures surface
immediately, all other errors surface without reconnecting.
"""

import asyncio
from unittest.mock import patch

import pytest

from mcp.types import INTERNAL_ERROR, INVALID_REQUEST, ErrorData

from agentao.mcp._compat import McpProtocolError
from agentao.mcp.client import (
    McpClient,
    McpErrorKind,
    ServerStatus,
    classify_mcp_error,
)

from tests.support.mcp import text_block, tool_result


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "msg, kind",
    [
        ("session expired", McpErrorKind.SESSION_EXPIRED),
        ("Session Expired", McpErrorKind.SESSION_EXPIRED),
        ("the session not found", McpErrorKind.SESSION_EXPIRED),
        ("Unknown Session id=abc", McpErrorKind.SESSION_EXPIRED),
        ("session terminated by server", McpErrorKind.SESSION_EXPIRED),
        ("401 Unauthorized", McpErrorKind.AUTH),
        ("HTTP 403 forbidden", McpErrorKind.AUTH),
        ("Unauthorized request", McpErrorKind.AUTH),
        ("Forbidden by policy", McpErrorKind.AUTH),
        # AUTH wins when both signals are present (server can stuff
        # multiple signals into one message; retry won't change creds).
        ("401 Unauthorized: session expired", McpErrorKind.AUTH),
        ("connection reset by peer", McpErrorKind.TRANSPORT_DROPPED),
        ("the connection was closed unexpectedly", McpErrorKind.TRANSPORT_DROPPED),
        ("broken pipe", McpErrorKind.TRANSPORT_DROPPED),
        ("transport closed", McpErrorKind.TRANSPORT_DROPPED),
        ("EndOfStream", McpErrorKind.TRANSPORT_DROPPED),
        # ``connection refused`` is the server-not-listening case; a
        # reconnect would fail the same way, so it must NOT classify as
        # transport-dropped.
        ("connection refused", McpErrorKind.OTHER),
        ("tool args invalid", McpErrorKind.OTHER),
        ("", McpErrorKind.OTHER),
    ],
)
def test_classify_mcp_error_by_message(msg, kind):
    assert classify_mcp_error(RuntimeError(msg)) is kind


@pytest.mark.parametrize(
    "type_name",
    ["ClosedResourceError", "BrokenResourceError", "EndOfStream"],
)
def test_classify_mcp_error_by_type_name(type_name):
    """anyio resource errors stringify to an empty body but the type
    name carries the signal — synthesize a class with the same name to
    mimic the surface without depending on anyio at test time.
    """
    exc = type(type_name, (Exception,), {})()
    assert classify_mcp_error(exc) is McpErrorKind.TRANSPORT_DROPPED


def _protocol_error(code: int, message: str) -> Exception:
    """The SDK's JSON-RPC exception, across the 1.x / 2.x constructor split
    (``_compat.McpProtocolError`` is only safe to *catch*; see its docstring)."""
    try:
        return McpProtocolError(code=code, message=message, data=None)
    except TypeError:
        return McpProtocolError(ErrorData(code=code, message=message))


@pytest.mark.parametrize(
    "code, message, kind",
    [
        # The SDK client's spelling of a 404 on a known session: refused.
        (INVALID_REQUEST, "Session terminated", McpErrorKind.SESSION_EXPIRED),
        # The SDK server's answer to an unknown session id: refused.
        (INVALID_REQUEST, "Session not found", McpErrorKind.SESSION_EXPIRED),
        # mcp 2.x's server, for a request in flight when its session ended
        # (``server/streamable_http.py``): the handler had it, so it can have run.
        (
            INTERNAL_ERROR,
            "Session terminated before the request completed",
            McpErrorKind.TRANSPORT_DROPPED,
        ),
    ],
)
def test_a_session_error_is_a_refusal_only_when_the_server_did_not_take_the_request(
    code, message, kind
):
    assert classify_mcp_error(_protocol_error(code, message)) is kind


# ---------------------------------------------------------------------------
# call_tool classified retry behavior
# ---------------------------------------------------------------------------


def _make_client_with_session(call_results):
    """Build a client whose session.call_tool yields the queued results
    (raising on Exception instances). Status starts CONNECTED so the
    connect() branch is skipped on the first attempt.
    """
    cfg = {"command": "echo"}
    client = McpClient("svr", cfg)
    client.status = ServerStatus.CONNECTED

    iter_results = iter(call_results)

    class _FakeSession:
        # ``**kwargs``: agentao passes era-dependent keywords the real
        # ClientSession accepts and mcp 1.x does not (``allow_input_required``).
        # A fixed signature would fail these tests for a reason the SDK never
        # would, and only on the 2.x cell.
        async def call_tool(self, tool_name, arguments, read_timeout_seconds=None, **kwargs):
            outcome = next(iter_results)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    client._session = _FakeSession()
    return client


def _run(coro):
    return asyncio.run(coro)


def _patch_connect_with_ok_session(text: str):
    """Patch McpClient.connect to install a session whose call_tool
    returns a single text block containing ``text``.
    """
    # Real ``mcp.types`` models, not a namespace: a hand-rolled stand-in
    # carries agentao's assumption about the wire field names rather than the
    # SDK's, and silently survives a rename the real code path dies on.
    success_result = tool_result([text_block(text)])

    async def _fake_connect(self_):
        class _SessionOk:
            async def call_tool(self, tool_name, arguments, read_timeout_seconds=None, **kwargs):
                return success_result

        self_._session = _SessionOk()
        self_.status = ServerStatus.CONNECTED

    return patch.object(McpClient, "connect", _fake_connect)


def test_session_expired_triggers_reconnect_and_retry():
    """First exception is session-expired → reconnect-and-retry once."""
    client = _make_client_with_session([RuntimeError("session expired")])
    with _patch_connect_with_ok_session("ok"):
        out = _run(client.call_tool("t", {}))
    assert out == "ok"


def test_auth_failure_does_not_retry_and_surfaces_immediately():
    """401 / 403 / unauthorized must not trigger a reconnect+retry."""
    connect_calls = {"n": 0}

    async def _spy_connect(self_):
        connect_calls["n"] += 1

    client = _make_client_with_session([RuntimeError("401 Unauthorized")])
    with patch.object(McpClient, "connect", _spy_connect):
        out = _run(client.call_tool("t", {}))

    assert out.startswith("MCP auth error:"), out
    assert "401" in out
    # Auth failure is unrecoverable — must not have cycled through reconnect.
    assert connect_calls["n"] == 0


def test_generic_error_does_not_reconnect():
    """Non-session, non-auth errors surface directly without reconnect."""
    connect_calls = {"n": 0}

    async def _spy_connect(self_):
        connect_calls["n"] += 1

    client = _make_client_with_session([RuntimeError("invalid argument: foo")])
    with patch.object(McpClient, "connect", _spy_connect):
        out = _run(client.call_tool("t", {}))

    assert out.startswith("MCP tool error:")
    assert "invalid argument" in out
    assert connect_calls["n"] == 0


def _spy_connect_count():
    calls = {"n": 0}

    async def _spy_connect(self_):
        calls["n"] += 1

    return calls, _spy_connect


def test_transport_dropped_is_not_sent_again():
    """A dropped transport (anyio ClosedResourceError, broken pipe, …) after
    the call went out does not show whether the server ran it. Sending it
    again could run a tool with side effects twice, so the call reports an
    unknown result, drops the session for the next call, and does not
    reconnect itself."""
    calls, spy = _spy_connect_count()
    client = _make_client_with_session([RuntimeError("connection reset by peer")])
    with patch.object(McpClient, "connect", spy):
        out = _run(client.call_tool("t", {}))

    assert out.startswith("MCP tool error: the connection to MCP server 'svr' closed"), out
    assert "The result is unknown" in out
    assert calls["n"] == 0
    assert client._session is None
    assert client.status is ServerStatus.DISCONNECTED


def test_the_call_after_a_dropped_transport_reconnects():
    client = _make_client_with_session([RuntimeError("connection reset by peer")])
    _run(client.call_tool("t", {}))
    with _patch_connect_with_ok_session("ok"):
        assert _run(client.call_tool("t", {})) == "ok"


def test_auth_failure_with_session_wording_does_not_retry():
    """A server may stuff multiple signals into one error string, e.g.
    ``401 Unauthorized: session expired``. Auth must win — retrying with
    the same credentials only produces another 401 and a noisy
    reconnect storm.
    """
    connect_calls = {"n": 0}

    async def _spy_connect(self_):
        connect_calls["n"] += 1

    client = _make_client_with_session(
        [RuntimeError("401 Unauthorized: session expired")]
    )
    with patch.object(McpClient, "connect", _spy_connect):
        out = _run(client.call_tool("t", {}))

    assert out.startswith("MCP auth error:"), out
    assert "401" in out
    assert connect_calls["n"] == 0


def test_anyio_closed_resource_error_class_is_not_sent_again():
    """Even when the exception's str() is empty, the type name
    ``ClosedResourceError`` should still classify as transport-dropped.
    """
    calls, spy = _spy_connect_count()
    closed_err = type("ClosedResourceError", (Exception,), {})()
    client = _make_client_with_session([closed_err])
    with patch.object(McpClient, "connect", spy):
        out = _run(client.call_tool("t", {}))
    assert "The result is unknown" in out, out
    assert calls["n"] == 0


def test_a_session_terminated_mid_request_is_not_sent_again():
    """The ``-32603`` form carries session wording but is not a refusal."""
    calls, spy = _spy_connect_count()
    client = _make_client_with_session(
        [_protocol_error(INTERNAL_ERROR, "Session terminated before the request completed")]
    )
    with patch.object(McpClient, "connect", spy):
        out = _run(client.call_tool("t", {}))
    assert "The result is unknown" in out, out
    assert calls["n"] == 0


def test_a_refused_session_still_reconnects_and_retries():
    client = _make_client_with_session([_protocol_error(INVALID_REQUEST, "Session terminated")])
    with _patch_connect_with_ok_session("ok"):
        assert _run(client.call_tool("t", {})) == "ok"


def test_a_connection_already_closing_is_reconnected_before_the_call_is_sent():
    """Nothing has gone out on a connection whose owner has already seen it
    close, so the call reconnects first instead of failing on it."""
    client = _make_client_with_session([AssertionError("sent on a closing connection")])
    client._gone.set()
    with _patch_connect_with_ok_session("ok"):
        assert _run(client.call_tool("t", {})) == "ok"
