"""Tests for ACP client CLI commands (Issues 06, 07, 10)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from agentao.acp_client.interaction import (
    InteractionKind,
    InteractionRegistry,
    PendingInteraction,
)
from agentao.acp_client.models import (
    AcpClientConfig,
    AcpProcessInfo,
    AcpServerConfig,
    ServerState,
)
from agentao.acp_client.manager import ACPManager
from agentao.acp_client.process import ACPProcessHandle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(name: str = "test-srv", **overrides) -> AcpServerConfig:
    defaults = dict(
        command="echo",
        args=["hello"],
        env={},
        cwd="/tmp",
        auto_start=False,
        description="Test server",
    )
    defaults.update(overrides)
    return AcpServerConfig(**defaults)


def _make_manager(*names: str) -> ACPManager:
    servers = {}
    for name in names:
        servers[name] = _make_config(name=name, description=f"{name} server")
    config = AcpClientConfig(servers=servers)
    return ACPManager(config)


# ---------------------------------------------------------------------------
# InteractionRegistry
# ---------------------------------------------------------------------------


class TestInteractionRegistry:
    def test_register_and_get(self) -> None:
        reg = InteractionRegistry()
        i = PendingInteraction(
            server="srv",
            kind=InteractionKind.PERMISSION,
            prompt="allow?",
        )
        rid = reg.register(i)
        assert reg.get(rid) is i
        assert reg.pending_count == 1

    def test_resolve(self) -> None:
        reg = InteractionRegistry()
        i = PendingInteraction(server="srv", kind=InteractionKind.INPUT, prompt="name?")
        rid = reg.register(i)
        resolved = reg.resolve(rid, {"outcome": "answered", "text": "foo"})
        assert resolved is i
        assert i.resolved is True
        assert i.response == {"outcome": "answered", "text": "foo"}
        assert reg.pending_count == 0

    def test_resolve_unknown_returns_none(self) -> None:
        reg = InteractionRegistry()
        assert reg.resolve("nope", {}) is None

    def test_resolve_already_resolved_returns_none(self) -> None:
        reg = InteractionRegistry()
        i = PendingInteraction(server="srv", kind=InteractionKind.PERMISSION, prompt="ok?")
        rid = reg.register(i)
        reg.resolve(rid, {"outcome": "approved"})
        assert reg.resolve(rid, {"outcome": "approved"}) is None

    def test_list_pending_filters_by_server(self) -> None:
        reg = InteractionRegistry()
        reg.register(PendingInteraction(server="a", kind=InteractionKind.PERMISSION, prompt="a?"))
        reg.register(PendingInteraction(server="b", kind=InteractionKind.INPUT, prompt="b?"))
        assert len(reg.list_pending(server="a")) == 1
        assert len(reg.list_pending(server="b")) == 1
        assert len(reg.list_pending()) == 2

    def test_expire_overdue(self) -> None:
        reg = InteractionRegistry()
        i = PendingInteraction(
            server="srv",
            kind=InteractionKind.PERMISSION,
            prompt="late?",
            deadline_at=time.time() - 10,
        )
        reg.register(i)
        overdue = reg.expire_overdue()
        assert len(overdue) == 1
        assert overdue[0].request_id == i.request_id

    def test_expire_not_overdue(self) -> None:
        reg = InteractionRegistry()
        i = PendingInteraction(
            server="srv",
            kind=InteractionKind.PERMISSION,
            prompt="soon?",
            deadline_at=time.time() + 3600,
        )
        reg.register(i)
        assert reg.expire_overdue() == []

    def test_remove(self) -> None:
        reg = InteractionRegistry()
        i = PendingInteraction(server="srv", kind=InteractionKind.INPUT, prompt="x?")
        rid = reg.register(i)
        removed = reg.remove(rid)
        assert removed is i
        assert reg.pending_count == 0

    def test_is_empty(self) -> None:
        reg = InteractionRegistry()
        assert reg.is_empty
        reg.register(PendingInteraction(server="s", kind=InteractionKind.INPUT, prompt="?"))
        assert not reg.is_empty


# ---------------------------------------------------------------------------
# Manager interaction bridge (Issue 10)
# ---------------------------------------------------------------------------


class TestManagerInteractionBridge:
    def test_approve_interaction(self) -> None:
        mgr = _make_manager("srv")
        i = PendingInteraction(
            server="srv", kind=InteractionKind.PERMISSION, prompt="allow?"
        )
        mgr.interactions.register(i)
        assert mgr.approve_interaction("srv", i.request_id) is True
        assert i.resolved
        assert i.response["outcome"] == "approved"

    def test_approve_wrong_server(self) -> None:
        mgr = _make_manager("srv")
        i = PendingInteraction(server="srv", kind=InteractionKind.PERMISSION, prompt="?")
        mgr.interactions.register(i)
        assert mgr.approve_interaction("other", i.request_id) is False

    def test_reject_interaction(self) -> None:
        mgr = _make_manager("srv")
        i = PendingInteraction(server="srv", kind=InteractionKind.PERMISSION, prompt="?")
        mgr.interactions.register(i)
        assert mgr.reject_interaction("srv", i.request_id) is True
        assert i.response["outcome"] == "rejected"

    def test_reply_interaction(self) -> None:
        mgr = _make_manager("srv")
        i = PendingInteraction(server="srv", kind=InteractionKind.INPUT, prompt="name?")
        mgr.interactions.register(i)
        assert mgr.reply_interaction("srv", i.request_id, "main") is True
        assert i.response == {"outcome": "answered", "text": "main"}

    def test_reply_unknown_request(self) -> None:
        mgr = _make_manager("srv")
        assert mgr.reply_interaction("srv", "nope", "x") is False


# ---------------------------------------------------------------------------
# Response sent back to server (P1 fix)
# ---------------------------------------------------------------------------


class TestInteractionResponseSentBack:
    """Verify that resolving interactions sends JSON-RPC responses to the server."""

    def _setup_manager_with_mock_client(self):
        mgr = _make_manager("srv")
        mock_client = MagicMock()
        mock_client.connection_info = MagicMock()
        mock_client.connection_info.session_id = "sess_1"
        mgr._clients["srv"] = mock_client
        return mgr, mock_client

    def test_approve_sends_response(self) -> None:
        mgr, mock_client = self._setup_manager_with_mock_client()
        i = PendingInteraction(
            server="srv",
            kind=InteractionKind.PERMISSION,
            prompt="allow?",
            rpc_request_id=42,
        )
        mgr.interactions.register(i)
        mgr.approve_interaction("srv", i.request_id)
        mock_client.send_response.assert_called_once()
        call_args = mock_client.send_response.call_args
        assert call_args[0][0] == 42  # rpc_request_id
        result = call_args[0][1]
        assert result["outcome"]["optionId"] == "allow_once"

    def test_reject_sends_response(self) -> None:
        mgr, mock_client = self._setup_manager_with_mock_client()
        i = PendingInteraction(
            server="srv",
            kind=InteractionKind.PERMISSION,
            prompt="deny?",
            rpc_request_id=99,
        )
        mgr.interactions.register(i)
        mgr.reject_interaction("srv", i.request_id)
        mock_client.send_response.assert_called_once()
        call_args = mock_client.send_response.call_args
        assert call_args[0][0] == 99
        result = call_args[0][1]
        assert result["outcome"]["optionId"] == "reject_once"

    def test_reply_sends_response(self) -> None:
        mgr, mock_client = self._setup_manager_with_mock_client()
        i = PendingInteraction(
            server="srv",
            kind=InteractionKind.INPUT,
            prompt="branch?",
            rpc_request_id="srv_7",
        )
        mgr.interactions.register(i)
        mgr.reply_interaction("srv", i.request_id, "feature/acp")
        mock_client.send_response.assert_called_once()
        call_args = mock_client.send_response.call_args
        assert call_args[0][0] == "srv_7"
        result = call_args[0][1]
        assert result["outcome"] == "answered"
        assert result["text"] == "feature/acp"

    def test_no_rpc_id_skips_response(self) -> None:
        """Interactions from notifications (no rpc_request_id) don't send responses."""
        mgr, mock_client = self._setup_manager_with_mock_client()
        i = PendingInteraction(
            server="srv",
            kind=InteractionKind.PERMISSION,
            prompt="ok?",
            rpc_request_id=None,  # came from a notification, not a request
        )
        mgr.interactions.register(i)
        mgr.approve_interaction("srv", i.request_id)
        mock_client.send_response.assert_not_called()


# ---------------------------------------------------------------------------
# Server-initiated request routing (P1 fix)
# ---------------------------------------------------------------------------


class TestServerRequestRouting:
    """Verify that server-initiated requests (method + id) are routed properly."""

    def test_server_request_creates_interaction_with_rpc_id(self) -> None:
        mgr = _make_manager("planner")
        mock_client = MagicMock()
        mock_client.connection_info = MagicMock()
        mock_client.connection_info.session_id = "sess_x"
        mgr._clients["planner"] = mock_client

        mgr._route_server_request(
            "planner",
            "session/request_permission",
            {"message": "Allow tool X?"},
            request_id=42,
        )

        assert mgr.interactions.pending_count == 1
        pending = mgr.interactions.list_pending(server="planner")
        assert pending[0].rpc_request_id == 42
        assert pending[0].kind == InteractionKind.PERMISSION

    def test_server_request_ask_user_creates_input_interaction(self) -> None:
        mgr = _make_manager("coder")
        mgr._route_server_request(
            "coder",
            "_agentao.cn/ask_user",
            {"question": "Branch?"},
            request_id="srv_5",
        )

        pending = mgr.interactions.list_pending(server="coder")
        assert len(pending) == 1
        assert pending[0].kind == InteractionKind.INPUT
        assert pending[0].rpc_request_id == "srv_5"

    def test_server_request_pushes_to_inbox(self) -> None:
        mgr = _make_manager("srv")
        mgr._route_server_request(
            "srv", "session/request_permission", {"message": "ok?"}, 1
        )
        assert mgr.inbox.pending_count == 1


# ---------------------------------------------------------------------------
# Stderr ring buffer (Issue 07)
# ---------------------------------------------------------------------------


class TestStderrRingBuffer:
    def test_get_stderr_tail_empty(self) -> None:
        cfg = _make_config()
        handle = ACPProcessHandle("test", cfg)
        assert handle.get_stderr_tail() == []

    def test_get_stderr_tail_returns_lines(self) -> None:
        cfg = _make_config()
        handle = ACPProcessHandle("test", cfg)
        for i in range(10):
            handle._stderr_ring.append(f"line-{i}")
        tail = handle.get_stderr_tail(5)
        assert tail == [f"line-{i}" for i in range(5, 10)]

    def test_get_stderr_tail_bounded(self) -> None:
        cfg = _make_config()
        handle = ACPProcessHandle("test", cfg)
        from agentao.acp_client.process import _STDERR_RING_CAPACITY
        for i in range(_STDERR_RING_CAPACITY + 50):
            handle._stderr_ring.append(f"line-{i}")
        assert len(handle._stderr_ring) == _STDERR_RING_CAPACITY
        tail = handle.get_stderr_tail(10)
        assert len(tail) == 10

    def test_manager_get_server_logs(self) -> None:
        mgr = _make_manager("srv")
        handle = mgr.get_handle("srv")
        handle._stderr_ring.append("error: something broke")
        logs = mgr.get_server_logs("srv")
        assert logs == ["error: something broke"]

    def test_manager_get_server_logs_unknown(self) -> None:
        mgr = _make_manager("srv")
        with pytest.raises(KeyError):
            mgr.get_server_logs("nope")


# ---------------------------------------------------------------------------
# Manager notification routing creates interactions (Issue 10)
# ---------------------------------------------------------------------------


class TestNotificationRouting:
    def test_permission_notification_creates_interaction(self) -> None:
        mgr = _make_manager("planner")
        mgr._route_notification(
            "planner",
            "session/request_permission",
            {"message": "Allow tool X?"},
        )
        assert mgr.interactions.pending_count == 1
        pending = mgr.interactions.list_pending(server="planner")
        assert pending[0].kind == InteractionKind.PERMISSION
        assert "Allow tool X?" in pending[0].prompt

    def test_input_notification_creates_interaction(self) -> None:
        mgr = _make_manager("coder")
        mgr._route_notification(
            "coder",
            "_agentao.cn/ask_user",
            {"question": "Branch name?"},
        )
        assert mgr.interactions.pending_count == 1
        pending = mgr.interactions.list_pending(server="coder")
        assert pending[0].kind == InteractionKind.INPUT

    def test_response_notification_no_interaction(self) -> None:
        mgr = _make_manager("srv")
        mgr._route_notification(
            "srv", "session/update", {"message": "done"}
        )
        assert mgr.interactions.pending_count == 0

    def test_waiting_for_user_state_on_permission(self) -> None:
        mgr = _make_manager("srv")
        mgr._route_notification(
            "srv", "session/request_permission", {"message": "ok?"}
        )
        handle = mgr.get_handle("srv")
        assert handle.state == ServerState.WAITING_FOR_USER


# ---------------------------------------------------------------------------
# Manager get_status (Issue 06)
# ---------------------------------------------------------------------------


class TestManagerGetStatus:
    def test_status_includes_all_fields(self) -> None:
        mgr = _make_manager("alpha", "beta")
        statuses = mgr.get_status()
        assert len(statuses) == 2
        for s in statuses:
            assert "name" in s
            assert "state" in s
            assert "interactions_pending" in s
            assert "stderr_lines" in s

    def test_status_shows_interaction_count(self) -> None:
        mgr = _make_manager("srv")
        mgr.interactions.register(
            PendingInteraction(server="srv", kind=InteractionKind.PERMISSION, prompt="?")
        )
        statuses = mgr.get_status()
        assert statuses[0]["interactions_pending"] == 1

    def test_no_config_empty_status(self) -> None:
        mgr = ACPManager(AcpClientConfig(servers={}))
        assert mgr.get_status() == []


# ---------------------------------------------------------------------------
# WAITING_FOR_USER state (Issue 10)
# ---------------------------------------------------------------------------


class TestACPClientSendResponse:
    """Verify ACPClient.send_response / send_error_response write NDJSON."""

    def test_send_response_writes_json(self) -> None:
        from agentao.acp_client.client import ACPClient
        handle = MagicMock()
        handle.stdin = MagicMock()
        handle.name = "test"
        client = ACPClient(handle)

        client.send_response(42, {"outcome": "answered", "text": "yes"})

        handle.stdin.write.assert_called_once()
        import json
        written = handle.stdin.write.call_args[0][0]
        msg = json.loads(written.decode("utf-8"))
        assert msg["jsonrpc"] == "2.0"
        assert msg["id"] == 42
        assert msg["result"]["outcome"] == "answered"

    def test_send_error_response_writes_error(self) -> None:
        from agentao.acp_client.client import ACPClient
        handle = MagicMock()
        handle.stdin = MagicMock()
        handle.name = "test"
        client = ACPClient(handle)

        client.send_error_response("srv_1", -32600, "bad request")

        import json
        written = handle.stdin.write.call_args[0][0]
        msg = json.loads(written.decode("utf-8"))
        assert msg["id"] == "srv_1"
        assert msg["error"]["code"] == -32600


class TestWaitingForUserState:
    def test_state_enum_value(self) -> None:
        assert ServerState.WAITING_FOR_USER.value == "waiting_for_user"

    def test_approve_transitions_to_busy(self) -> None:
        mgr = _make_manager("srv")
        handle = mgr.get_handle("srv")
        handle._set_state(ServerState.WAITING_FOR_USER)
        i = PendingInteraction(server="srv", kind=InteractionKind.PERMISSION, prompt="?")
        mgr.interactions.register(i)
        mgr.approve_interaction("srv", i.request_id)
        assert handle.state == ServerState.BUSY

    def test_reject_transitions_to_ready(self) -> None:
        mgr = _make_manager("srv")
        handle = mgr.get_handle("srv")
        handle._set_state(ServerState.WAITING_FOR_USER)
        i = PendingInteraction(server="srv", kind=InteractionKind.PERMISSION, prompt="?")
        mgr.interactions.register(i)
        mgr.reject_interaction("srv", i.request_id)
        assert handle.state == ServerState.READY
