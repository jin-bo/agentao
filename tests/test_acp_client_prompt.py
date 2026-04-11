"""Tests for ACP client session/prompt and session/cancel (Issue 04).

Uses a mock ACP server script that handles initialize, session/new,
session/prompt, and session/cancel over NDJSON stdio.
"""

import json
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from agentao.acp_client.client import ACPClient, AcpClientError, AcpRpcError
from agentao.acp_client.manager import ACPManager
from agentao.acp_client.models import AcpClientConfig, AcpServerConfig, ServerState
from agentao.acp_client.process import ACPProcessHandle

# ---------------------------------------------------------------------------
# Mock server script with prompt/cancel support
# ---------------------------------------------------------------------------

_MOCK_SERVER_SCRIPT = textwrap.dedent("""\
    import json
    import sys
    import os
    import time
    import threading
    import queue

    # Use an unbuffered binary stdin wrapper so we can read in a background
    # thread while the main thread processes messages.
    stdin_raw = sys.stdin.buffer if hasattr(sys.stdin, 'buffer') else sys.stdin

    cancel_event = threading.Event()
    msg_queue = queue.Queue()
    write_lock = threading.Lock()

    def respond(rid, result):
        msg = {"jsonrpc": "2.0", "id": rid, "result": result}
        line = json.dumps(msg) + "\\n"
        with write_lock:
            sys.stdout.write(line)
            sys.stdout.flush()

    def respond_error(rid, code, message):
        msg = {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}
        line = json.dumps(msg) + "\\n"
        with write_lock:
            sys.stdout.write(line)
            sys.stdout.flush()

    def reader_thread():
        for raw_line in stdin_raw:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                continue
            method = req.get("method", "")
            # Handle cancel immediately in the reader thread.
            if method == "session/cancel":
                cancel_event.set()
                rid = req.get("id")
                if rid is not None:
                    respond(rid, None)
            else:
                msg_queue.put(req)
        # Signal EOF.
        msg_queue.put(None)

    t = threading.Thread(target=reader_thread, daemon=True)
    t.start()

    while True:
        req = msg_queue.get()
        if req is None:
            break

        method = req.get("method", "")
        rid = req.get("id")
        params = req.get("params", {})

        if method == "initialize":
            respond(rid, {
                "protocolVersion": 1,
                "agentCapabilities": {},
                "agentInfo": {"name": "mock", "title": "Mock", "version": "0.1"},
            })
        elif method == "session/new":
            respond(rid, {"sessionId": "sess_mock"})
        elif method == "session/prompt":
            text = ""
            for block in params.get("prompt", []):
                if block.get("type") == "text":
                    text += block.get("text", "")

            if text == "crash":
                os._exit(1)
            elif text == "slow":
                cancel_event.clear()
                for _ in range(50):
                    if cancel_event.is_set():
                        respond(rid, {"stopReason": "cancelled"})
                        break
                    time.sleep(0.1)
                else:
                    respond(rid, {"stopReason": "end_turn"})
            elif text == "error":
                respond_error(rid, -32600, "bad prompt")
            else:
                respond(rid, {"stopReason": "end_turn"})
        else:
            if rid is not None:
                respond_error(rid, -32601, f"not found: {method}")
""")


def _make_mock_handle(tmp_path: Path) -> ACPProcessHandle:
    script = tmp_path / "mock_acp_prompt.py"
    script.write_text(_MOCK_SERVER_SCRIPT, encoding="utf-8")
    config = AcpServerConfig(
        command=sys.executable,
        args=[str(script)],
        env={},
        cwd=str(tmp_path),
    )
    return ACPProcessHandle("mock", config)


def _connected_client(tmp_path: Path) -> tuple[ACPClient, ACPProcessHandle]:
    """Return a client that has completed initialize + session/new."""
    handle = _make_mock_handle(tmp_path)
    handle.start()
    client = ACPClient(handle)
    client.start_reader()
    client.initialize(timeout=5)
    client.create_session(timeout=5)
    return client, handle


# ---------------------------------------------------------------------------
# send_prompt basics
# ---------------------------------------------------------------------------


class TestSendPrompt:
    def test_basic_prompt(self, tmp_path: Path) -> None:
        client, handle = _connected_client(tmp_path)
        assert handle.state == ServerState.READY

        result = client.send_prompt("hello", timeout=5)
        assert result["stopReason"] == "end_turn"
        assert handle.state == ServerState.READY

        client.close()
        handle.stop()

    def test_multiple_rounds(self, tmp_path: Path) -> None:
        client, handle = _connected_client(tmp_path)

        for i in range(3):
            result = client.send_prompt(f"round {i}", timeout=5)
            assert result["stopReason"] == "end_turn"

        assert handle.state == ServerState.READY
        client.close()
        handle.stop()

    def test_busy_during_prompt(self, tmp_path: Path) -> None:
        client, handle = _connected_client(tmp_path)

        busy_seen = threading.Event()

        def _prompt():
            # "slow" takes a while, so we can observe BUSY state.
            client.send_prompt("slow", timeout=10)

        t = threading.Thread(target=_prompt)
        t.start()

        # Poll for BUSY state.
        for _ in range(50):
            if handle.state == ServerState.BUSY:
                busy_seen.set()
                break
            time.sleep(0.05)

        # Cancel so the prompt finishes.
        client.cancel_active_turn()
        t.join(timeout=10)

        assert busy_seen.is_set(), "state should have been BUSY during prompt"
        assert handle.state == ServerState.READY

        client.close()
        handle.stop()

    def test_no_session_raises(self, tmp_path: Path) -> None:
        handle = _make_mock_handle(tmp_path)
        handle.start()
        client = ACPClient(handle)
        client.start_reader()
        # Skip create_session — no session_id.
        client.initialize(timeout=5)

        with pytest.raises(AcpClientError, match="no active session"):
            client.send_prompt("hi", timeout=5)

        client.close()
        handle.stop()

    def test_concurrent_prompt_rejected(self, tmp_path: Path) -> None:
        client, handle = _connected_client(tmp_path)

        errors: List[Exception] = []

        def _slow_prompt():
            try:
                client.send_prompt("slow", timeout=10)
            except Exception as exc:
                errors.append(exc)

        t = threading.Thread(target=_slow_prompt)
        t.start()

        # Wait until the first prompt is in flight.
        for _ in range(50):
            if client.is_busy:
                break
            time.sleep(0.05)

        # Second concurrent prompt should fail.
        with pytest.raises(AcpClientError, match="already in progress"):
            client.send_prompt("second", timeout=5)

        # Clean up.
        client.cancel_active_turn()
        t.join(timeout=10)

        client.close()
        handle.stop()

    def test_rpc_error_returns_to_ready(self, tmp_path: Path) -> None:
        client, handle = _connected_client(tmp_path)

        with pytest.raises(AcpRpcError, match="bad prompt"):
            client.send_prompt("error", timeout=5)

        assert handle.state == ServerState.READY
        client.close()
        handle.stop()


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


class TestCancelActiveTurn:
    def test_cancel_slow_prompt(self, tmp_path: Path) -> None:
        client, handle = _connected_client(tmp_path)

        result_holder: Dict[str, Any] = {}

        def _prompt():
            r = client.send_prompt("slow", timeout=15)
            result_holder.update(r)

        t = threading.Thread(target=_prompt)
        t.start()

        # Wait until the prompt is confirmed in flight.
        for _ in range(100):
            if client.is_busy:
                break
            time.sleep(0.05)

        # Small extra delay to ensure the server has entered the slow loop.
        time.sleep(0.3)

        client.cancel_active_turn()
        t.join(timeout=10)

        assert result_holder.get("stopReason") == "cancelled"
        assert handle.state == ServerState.READY

        client.close()
        handle.stop()

    def test_cancel_no_turn_is_noop(self, tmp_path: Path) -> None:
        client, handle = _connected_client(tmp_path)
        # No prompt in flight — should not raise.
        client.cancel_active_turn()
        assert handle.state == ServerState.READY
        client.close()
        handle.stop()

    def test_cancel_no_session_is_noop(self, tmp_path: Path) -> None:
        handle = _make_mock_handle(tmp_path)
        client = ACPClient(handle)
        # No session — should not raise.
        client.cancel_active_turn()


# ---------------------------------------------------------------------------
# Auto-start via ACPManager
# ---------------------------------------------------------------------------


class TestManagerAutoStart:
    def test_send_prompt_auto_connects(self, tmp_path: Path) -> None:
        script = tmp_path / "mock_acp_prompt.py"
        script.write_text(_MOCK_SERVER_SCRIPT, encoding="utf-8")

        config = AcpClientConfig(servers={
            "srv": AcpServerConfig(
                command=sys.executable,
                args=[str(script)],
                env={},
                cwd=str(tmp_path),
            ),
        })
        mgr = ACPManager(config)

        # Server is not started yet.
        assert mgr.get_handle("srv").state == ServerState.CONFIGURED

        result = mgr.send_prompt("srv", "hello", timeout=10)
        assert result["stopReason"] == "end_turn"
        assert mgr.get_handle("srv").state == ServerState.READY

        mgr.stop_all()

    def test_ensure_connected_reuses_client(self, tmp_path: Path) -> None:
        script = tmp_path / "mock_acp_prompt.py"
        script.write_text(_MOCK_SERVER_SCRIPT, encoding="utf-8")

        config = AcpClientConfig(servers={
            "srv": AcpServerConfig(
                command=sys.executable,
                args=[str(script)],
                env={},
                cwd=str(tmp_path),
            ),
        })
        mgr = ACPManager(config)

        c1 = mgr.ensure_connected("srv", timeout=10)
        c2 = mgr.ensure_connected("srv", timeout=10)
        assert c1 is c2

        mgr.stop_all()

    def test_cancel_turn_via_manager(self, tmp_path: Path) -> None:
        script = tmp_path / "mock_acp_prompt.py"
        script.write_text(_MOCK_SERVER_SCRIPT, encoding="utf-8")

        config = AcpClientConfig(servers={
            "srv": AcpServerConfig(
                command=sys.executable,
                args=[str(script)],
                env={},
                cwd=str(tmp_path),
            ),
        })
        mgr = ACPManager(config)

        # No client yet — should be a no-op.
        mgr.cancel_turn("srv")

        mgr.stop_all()

    def test_unknown_server_raises(self) -> None:
        mgr = ACPManager(AcpClientConfig())
        with pytest.raises(KeyError, match="no ACP server"):
            mgr.send_prompt("nope", "hi", timeout=5)


# ---------------------------------------------------------------------------
# Server crash during prompt
# ---------------------------------------------------------------------------


class TestServerCrash:
    def test_crash_during_prompt(self, tmp_path: Path) -> None:
        client, handle = _connected_client(tmp_path)

        # "crash" causes the mock server to sys.exit(1).
        # The reader thread will hit EOF, pending slot won't be fulfilled,
        # and we'll get a timeout.
        with pytest.raises(AcpClientError):
            client.send_prompt("crash", timeout=2)

        client.close()
        handle.stop()
