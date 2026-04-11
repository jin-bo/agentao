"""Tests for _agentao.cn/ask_user ACP extension method (Issue 11)."""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from agentao.acp.protocol import (
    ASK_USER_UNAVAILABLE_SENTINEL,
    METHOD_ASK_USER,
)
from agentao.acp.transport import ACPTransport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_transport(
    *,
    call_result: Any = None,
    call_exception: Exception | None = None,
    wait_result: Any = None,
    wait_exception: Exception | None = None,
) -> ACPTransport:
    """Build a transport with a mocked server."""
    mock_server = MagicMock()
    transport = ACPTransport(mock_server, "sess_test")

    mock_pending = MagicMock()
    if wait_exception is not None:
        mock_pending.wait.side_effect = wait_exception
    else:
        mock_pending.wait.return_value = wait_result

    if call_exception is not None:
        mock_server.call.side_effect = call_exception
    else:
        mock_server.call.return_value = mock_pending

    return transport


# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------


class TestProtocolConstants:
    def test_method_name(self) -> None:
        assert METHOD_ASK_USER == "_agentao.cn/ask_user"

    def test_sentinel(self) -> None:
        assert ASK_USER_UNAVAILABLE_SENTINEL == "(user unavailable)"


# ---------------------------------------------------------------------------
# ask_user — success paths
# ---------------------------------------------------------------------------


class TestAskUserSuccess:
    def test_answered(self) -> None:
        t = _make_transport(
            wait_result={"outcome": "answered", "text": "feature/acp-client"}
        )
        result = t.ask_user("Branch name?")
        assert result == "feature/acp-client"
        t._server.call.assert_called_once()
        args = t._server.call.call_args
        assert args[0][0] == METHOD_ASK_USER

    def test_answered_empty_text_returns_sentinel(self) -> None:
        t = _make_transport(
            wait_result={"outcome": "answered", "text": ""}
        )
        result = t.ask_user("question?")
        assert result == ASK_USER_UNAVAILABLE_SENTINEL


# ---------------------------------------------------------------------------
# ask_user — failure paths
# ---------------------------------------------------------------------------


class TestAskUserFailures:
    def test_cancelled_returns_sentinel(self) -> None:
        t = _make_transport(wait_result={"outcome": "cancelled"})
        assert t.ask_user("q?") == ASK_USER_UNAVAILABLE_SENTINEL

    def test_no_server_returns_sentinel(self) -> None:
        t = ACPTransport(None, "sess")
        assert t.ask_user("q?") == ASK_USER_UNAVAILABLE_SENTINEL

    def test_call_exception_returns_sentinel(self) -> None:
        t = _make_transport(call_exception=RuntimeError("broken"))
        assert t.ask_user("q?") == ASK_USER_UNAVAILABLE_SENTINEL

    def test_pending_cancelled_returns_sentinel(self) -> None:
        # Simulate PendingRequestCancelled
        from agentao.acp.server import PendingRequestCancelled
        t = _make_transport(wait_exception=PendingRequestCancelled())
        assert t.ask_user("q?") == ASK_USER_UNAVAILABLE_SENTINEL

    def test_jsonrpc_error_returns_sentinel(self) -> None:
        from agentao.acp.server import JsonRpcHandlerError
        t = _make_transport(
            wait_exception=JsonRpcHandlerError(-32600, "bad request")
        )
        assert t.ask_user("q?") == ASK_USER_UNAVAILABLE_SENTINEL

    def test_non_dict_result_returns_sentinel(self) -> None:
        t = _make_transport(wait_result="just a string")
        assert t.ask_user("q?") == ASK_USER_UNAVAILABLE_SENTINEL

    def test_unknown_outcome_returns_sentinel(self) -> None:
        t = _make_transport(wait_result={"outcome": "weird"})
        assert t.ask_user("q?") == ASK_USER_UNAVAILABLE_SENTINEL


# ---------------------------------------------------------------------------
# on_max_iterations — ACP default
# ---------------------------------------------------------------------------


class TestOnMaxIterations:
    def test_returns_stop(self) -> None:
        t = _make_transport()
        result = t.on_max_iterations(100, [])
        assert result == {"action": "stop"}


# ---------------------------------------------------------------------------
# Capability advertisement (Issue 11)
# ---------------------------------------------------------------------------


class TestCapabilityAdvertisement:
    def test_initialize_includes_extensions(self) -> None:
        from agentao.acp.initialize import handle_initialize

        mock_server = MagicMock()
        mock_server.state = MagicMock()
        mock_server.state.initialized = False

        result = handle_initialize(mock_server, {
            "protocolVersion": 1,
            "clientCapabilities": {},
        })

        assert "extensions" in result
        ext = result["extensions"]
        assert any(e["method"] == METHOD_ASK_USER for e in ext)
