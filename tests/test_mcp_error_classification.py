"""Tests for MCP error classification helpers.

The classifier turns the old "retry on any first-attempt exception"
loop into a classified retry: session-expired / transport-dropped
errors reconnect-and-retry, auth failures surface immediately, all
other errors surface without reconnecting.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agentao.mcp.client import (
    McpClient,
    McpErrorKind,
    ServerStatus,
    classify_mcp_error,
)


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
        async def call_tool(self, tool_name, arguments):
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
    success_block = SimpleNamespace(type="text", text=text)
    success_result = SimpleNamespace(content=[success_block], isError=False)

    async def _fake_connect(self_):
        class _SessionOk:
            async def call_tool(self, tool_name, arguments):
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


def test_transport_dropped_triggers_reconnect_and_retry():
    """A dropped transport (anyio ClosedResourceError, broken pipe, …)
    must reconnect-and-retry once, the same as a session-expired error.
    The previous behavior retried any first-call failure, and dropping
    that broad retry without covering transport-loss errors lost
    automatic recovery for genuinely recoverable cases.
    """
    client = _make_client_with_session([RuntimeError("connection reset by peer")])
    with _patch_connect_with_ok_session("ok"):
        out = _run(client.call_tool("t", {}))
    assert out == "ok"


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


def test_anyio_closed_resource_error_class_triggers_retry():
    """Even when the exception's str() is empty, the type name
    ``ClosedResourceError`` should still classify as transport-dropped.
    """
    closed_err = type("ClosedResourceError", (Exception,), {})()
    client = _make_client_with_session([closed_err])
    with _patch_connect_with_ok_session("ok-after-reconnect"):
        out = _run(client.call_tool("t", {}))
    assert out == "ok-after-reconnect"
