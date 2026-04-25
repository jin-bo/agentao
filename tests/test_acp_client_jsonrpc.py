"""Tests for ACP client JSON-RPC layer (Issue 03).

Uses a tiny in-process mock ACP server script spawned via subprocess to
exercise the real NDJSON wire protocol end-to-end. The mock server and
its handle builder live in :mod:`tests.support.acp_client`.
"""

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from agentao.acp_client.client import (
    ACPClient,
    AcpClientError,
    AcpRpcError,
    _PendingRequest,
)
from agentao.acp_client.models import ServerState

from .support.acp_client import make_jsonrpc_mock_handle as _make_mock_handle


# ---------------------------------------------------------------------------
# Request ID uniqueness
# ---------------------------------------------------------------------------


class TestRequestIdManagement:
    def test_ids_are_unique(self, tmp_path: Path) -> None:
        handle = _make_mock_handle(tmp_path)
        handle.start()
        client = ACPClient(handle)

        ids = [client._alloc_id() for _ in range(100)]
        assert len(set(ids)) == 100

        handle.stop()

    def test_ids_are_sequential(self, tmp_path: Path) -> None:
        handle = _make_mock_handle(tmp_path)
        client = ACPClient(handle)
        assert client._alloc_id() == 0
        assert client._alloc_id() == 1
        assert client._alloc_id() == 2


# ---------------------------------------------------------------------------
# Response routing
# ---------------------------------------------------------------------------


class TestResponseRouting:
    def test_call_returns_result(self, tmp_path: Path) -> None:
        handle = _make_mock_handle(tmp_path)
        handle.start()
        client = ACPClient(handle)
        client.start_reader()

        result = client.call("echo", {"ping": "pong"}, timeout=5)
        assert result == {"ping": "pong"}

        client.close()
        handle.stop()

    def test_call_raises_on_rpc_error(self, tmp_path: Path) -> None:
        handle = _make_mock_handle(tmp_path)
        handle.start()
        client = ACPClient(handle)
        client.start_reader()

        with pytest.raises(AcpRpcError, match="intentional failure") as exc_info:
            client.call("fail", timeout=5)

        assert exc_info.value.rpc_code == -32603

        client.close()
        handle.stop()

    def test_call_timeout(self, tmp_path: Path) -> None:
        handle = _make_mock_handle(tmp_path)
        handle.start()
        client = ACPClient(handle)
        client.start_reader()

        with pytest.raises(AcpClientError, match="timeout"):
            client.call("slow", timeout=0.3)

        client.close()
        handle.stop()

    def test_multiple_concurrent_calls(self, tmp_path: Path) -> None:
        handle = _make_mock_handle(tmp_path)
        handle.start()
        client = ACPClient(handle)
        client.start_reader()

        results: Dict[int, Any] = {}
        errors: List[Exception] = []

        def _call(n: int) -> None:
            try:
                r = client.call("echo", {"n": n}, timeout=5)
                results[n] = r
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_call, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"errors: {errors}"
        assert len(results) == 5
        for i in range(5):
            assert results[i] == {"n": i}

        client.close()
        handle.stop()


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


class TestNotifications:
    def test_notification_callback_invoked(self, tmp_path: Path) -> None:
        handle = _make_mock_handle(tmp_path)
        handle.start()

        received: List[tuple] = []

        def on_notif(method: str, params: Any) -> None:
            received.append((method, params))

        client = ACPClient(handle, notification_callback=on_notif)
        client.start_reader()

        # The "notify_me" method sends a notification then responds.
        result = client.call("notify_me", timeout=5)
        assert result == {"ok": True}

        # Give the reader thread a moment to process.
        time.sleep(0.1)

        assert len(received) >= 1
        assert received[0] == ("session/update", {"status": "hello"})

        client.close()
        handle.stop()

    def test_notify_sends_no_id(self, tmp_path: Path) -> None:
        handle = _make_mock_handle(tmp_path)
        handle.start()
        client = ACPClient(handle)
        client.start_reader()

        # notify() should not block or allocate a pending slot.
        client.notify("some/notification", {"data": 1})
        # No exception = success. No pending slots should exist.
        assert len(client._pending) == 0

        client.close()
        handle.stop()


# ---------------------------------------------------------------------------
# Initialize handshake
# ---------------------------------------------------------------------------


class TestInitialize:
    def test_initialize_success(self, tmp_path: Path) -> None:
        handle = _make_mock_handle(tmp_path)
        handle.start()
        client = ACPClient(handle)
        client.start_reader()

        result = client.initialize(timeout=5)

        assert result["protocolVersion"] == 1
        assert result["agentInfo"]["name"] == "mock"
        assert client.connection_info.protocol_version == 1
        assert client.connection_info.agent_info == {
            "name": "mock",
            "title": "Mock",
            "version": "0.1",
        }

        client.close()
        handle.stop()

    def test_initialize_sets_state(self, tmp_path: Path) -> None:
        handle = _make_mock_handle(tmp_path)
        handle.start()
        client = ACPClient(handle)
        client.start_reader()

        client.initialize(timeout=5)
        # After initialize (but before create_session), state should be
        # INITIALIZING (initialize sets it, create_session moves to READY).
        # Actually our initialize method only calls _set_state(INITIALIZING)
        # at entry — on success it stays INITIALIZING until create_session.
        assert handle.state in (ServerState.INITIALIZING, ServerState.READY)

        client.close()
        handle.stop()


# ---------------------------------------------------------------------------
# session/new
# ---------------------------------------------------------------------------


class TestCreateSession:
    def test_create_session_success(self, tmp_path: Path) -> None:
        handle = _make_mock_handle(tmp_path)
        handle.start()
        client = ACPClient(handle)
        client.start_reader()

        client.initialize(timeout=5)
        session_id = client.create_session(timeout=5)

        assert session_id == "sess_test123"
        assert client.connection_info.session_id == "sess_test123"
        assert handle.state == ServerState.READY

        client.close()
        handle.stop()


# ---------------------------------------------------------------------------
# Close / teardown
# ---------------------------------------------------------------------------


class TestClose:
    def test_close_wakes_pending(self, tmp_path: Path) -> None:
        handle = _make_mock_handle(tmp_path)
        handle.start()
        client = ACPClient(handle)
        client.start_reader()

        errors: List[Exception] = []

        def _slow_call() -> None:
            try:
                client.call("slow", timeout=30)
            except (AcpRpcError, AcpClientError) as exc:
                errors.append(exc)

        t = threading.Thread(target=_slow_call)
        t.start()

        # Give the call time to register.
        time.sleep(0.2)
        client.close()
        t.join(timeout=5)

        assert len(errors) == 1
        err = errors[0]
        assert isinstance(err, AcpClientError)
        assert "transport closed" in str(err)
        from agentao.acp_client import AcpErrorCode

        assert err.code is AcpErrorCode.TRANSPORT_DISCONNECT

        handle.stop()

    def test_stdin_unavailable_raises(self, tmp_path: Path) -> None:
        handle = _make_mock_handle(tmp_path)
        # Don't start — stdin is None.
        client = ACPClient(handle)

        with pytest.raises(AcpClientError, match="stdin"):
            client.call("anything", timeout=1)
