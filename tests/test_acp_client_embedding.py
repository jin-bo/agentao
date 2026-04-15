"""Tests for the ACP embedding public types (Phase 1) and the
non-interactive turn control layer (Phase 2).
"""

from __future__ import annotations

import json
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Tuple

import pytest

from agentao.acp_client import (
    AcpClientError,
    AcpErrorCode,
    AcpInteractionRequiredError,
    AcpRpcError,
    PromptResult,
)
from agentao.acp_client.manager import ACPManager, _TurnContext
from agentao.acp_client.models import (
    AcpClientConfig,
    AcpServerConfig,
    ServerState,
)


# ---------------------------------------------------------------------------
# Mock ACP server that emits server-initiated requests (permission / ask_user)
# mid-turn so we can exercise non-interactive policy behavior.
# ---------------------------------------------------------------------------

_INTERACTION_SERVER_SCRIPT = textwrap.dedent("""\
    import json
    import sys
    import threading
    import queue
    import time

    stdin_raw = sys.stdin.buffer if hasattr(sys.stdin, 'buffer') else sys.stdin

    pending_responses = {}
    response_cv = threading.Condition()
    cancel_event = threading.Event()
    write_lock = threading.Lock()
    next_srv_id = [1000]

    def write(obj):
        line = json.dumps(obj) + "\\n"
        with write_lock:
            sys.stdout.write(line)
            sys.stdout.flush()

    def respond(rid, result):
        write({"jsonrpc": "2.0", "id": rid, "result": result})

    def respond_error(rid, code, message):
        write({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})

    def server_request(method, params):
        sid = next_srv_id[0]
        next_srv_id[0] += 1
        write({"jsonrpc": "2.0", "id": sid, "method": method, "params": params})
        with response_cv:
            while sid not in pending_responses:
                response_cv.wait(timeout=5)
                if sid in pending_responses:
                    break
            return pending_responses.pop(sid)

    def reader_thread():
        for raw_line in stdin_raw:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "method" not in req:
                rid = req.get("id")
                with response_cv:
                    pending_responses[rid] = req
                    response_cv.notify_all()
                continue
            method = req.get("method", "")
            if method == "session/cancel":
                cancel_event.set()
                continue
            msg_queue.put(req)
        msg_queue.put(None)

    msg_queue = queue.Queue()
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
            respond(rid, {"protocolVersion": 1, "agentCapabilities": {},
                          "agentInfo": {"name": "mock"}})
        elif method == "session/new":
            respond(rid, {"sessionId": "sess_mock"})
        elif method == "session/prompt":
            text = ""
            for block in params.get("prompt", []):
                if block.get("type") == "text":
                    text += block.get("text", "")
            cancel_event.clear()
            if text == "permission":
                reply = server_request("session/request_permission", {
                    "toolCall": {"title": "rm -rf /", "kind": "shell"},
                    "options": [
                        {"id": "allow_once", "label": "Allow"},
                        {"id": "reject_once", "label": "Reject"},
                    ],
                })
                respond(rid, {"stopReason": "end_turn",
                              "_interaction": reply.get("result")})
            elif text == "permission_alt":
                # Non-standard option ids: manager must pick the one
                # whose 'kind' signals reject, not a hardcoded id.
                reply = server_request("session/request_permission", {
                    "toolCall": {"title": "x", "kind": "shell"},
                    "options": [
                        {"optionId": "go_ahead", "kind": "allow_once",
                         "name": "Go ahead"},
                        {"optionId": "decline_now", "kind": "reject_once",
                         "name": "Decline"},
                    ],
                })
                respond(rid, {"stopReason": "end_turn",
                              "_interaction": reply.get("result")})
            elif text == "permission_no_options":
                # No options array: manager must send outcome=cancelled,
                # not a bogus optionId.
                reply = server_request("session/request_permission", {
                    "toolCall": {"title": "x"},
                })
                respond(rid, {"stopReason": "end_turn",
                              "_interaction": reply.get("result")})
            elif text == "ask_user":
                try:
                    reply = server_request("_agentao.cn/ask_user", {
                        "question": "what now?",
                    })
                    respond(rid, {"stopReason": "end_turn",
                                  "_interaction": reply.get("result")})
                except Exception:
                    respond(rid, {"stopReason": "end_turn", "_error": True})
            elif text == "permission_then_wait":
                # Auto-reject should happen fast; then server holds the prompt
                # RPC open so tests can observe BUSY before terminal state.
                server_request("session/request_permission", {
                    "toolCall": {"title": "x", "kind": "shell"},
                })
                # Hold until cancel or 3s.
                for _ in range(30):
                    if cancel_event.is_set():
                        respond(rid, {"stopReason": "cancelled"})
                        break
                    time.sleep(0.1)
                else:
                    respond(rid, {"stopReason": "end_turn"})
            elif text == "slow":
                for _ in range(50):
                    if cancel_event.is_set():
                        respond(rid, {"stopReason": "cancelled"})
                        break
                    time.sleep(0.1)
                else:
                    respond(rid, {"stopReason": "end_turn"})
            else:
                respond(rid, {"stopReason": "end_turn"})
        else:
            if rid is not None:
                respond_error(rid, -32601, "unknown")
""")


def _make_mgr(tmp_path: Path, name: str = "srv") -> ACPManager:
    script = tmp_path / "mock_interaction_server.py"
    script.write_text(_INTERACTION_SERVER_SCRIPT, encoding="utf-8")
    cfg = AcpServerConfig(
        command=sys.executable,
        args=[str(script)],
        env={},
        cwd=str(tmp_path),
        request_timeout_ms=10_000,
    )
    return ACPManager(AcpClientConfig(servers={name: cfg}))


class TestAcpErrorCode:
    def test_enum_has_all_v1_codes(self) -> None:
        expected = {
            "config_invalid",
            "server_not_found",
            "process_start_fail",
            "handshake_fail",
            "request_timeout",
            "transport_disconnect",
            "interaction_required",
            "protocol_error",
            "server_busy",
        }
        assert {code.value for code in AcpErrorCode} == expected

    def test_str_enum(self) -> None:
        # Subclassing ``str`` keeps the enum JSON-friendly.
        assert AcpErrorCode.SERVER_BUSY == "server_busy"


class TestAcpClientError:
    def test_default_code_is_protocol_error(self) -> None:
        err = AcpClientError("boom")
        assert err.code is AcpErrorCode.PROTOCOL_ERROR
        assert err.details == {}
        assert err.cause is None
        assert str(err) == "boom"

    def test_structured_fields(self) -> None:
        cause = RuntimeError("underlying")
        err = AcpClientError(
            "transport broken",
            code=AcpErrorCode.TRANSPORT_DISCONNECT,
            details={"method": "session/prompt"},
            cause=cause,
        )
        assert err.code is AcpErrorCode.TRANSPORT_DISCONNECT
        assert err.details == {"method": "session/prompt"}
        assert err.cause is cause

    def test_details_copied_not_aliased(self) -> None:
        d = {"k": 1}
        err = AcpClientError("x", code=AcpErrorCode.PROTOCOL_ERROR, details=d)
        d["k"] = 2
        assert err.details == {"k": 1}


class TestAcpRpcError:
    def test_code_is_raw_jsonrpc_numeric(self) -> None:
        """AcpRpcError preserves the pre-existing public contract:
        ``.code`` is the raw JSON-RPC numeric code. The structured
        :class:`AcpErrorCode` classification lives on ``.error_code``."""
        err = AcpRpcError(rpc_code=-32603, rpc_message="boom")
        assert err.code == -32603
        assert err.rpc_code == -32603
        assert err.error_code is AcpErrorCode.PROTOCOL_ERROR
        assert err.rpc_message == "boom"
        assert err.data is None

    def test_preserves_raw_data(self) -> None:
        err = AcpRpcError(
            rpc_code=-32000, rpc_message="oops", data={"hint": "retry"}
        )
        assert err.data == {"hint": "retry"}

    def test_caught_by_acp_client_error(self) -> None:
        with pytest.raises(AcpClientError):
            raise AcpRpcError(rpc_code=-1, rpc_message="x")

    def test_accepts_legacy_keyword_arguments(self) -> None:
        """Old call sites used ``code=<int>`` / ``message=<str>``. Keep
        that keyword-compatible so upgrading doesn't raise TypeError."""
        err = AcpRpcError(code=-32603, message="boom")
        assert err.rpc_code == -32603
        assert err.rpc_message == "boom"
        assert err.code == -32603
        assert err.error_code is AcpErrorCode.PROTOCOL_ERROR


class TestAcpInteractionRequiredError:
    def test_code_is_interaction_required(self) -> None:
        err = AcpInteractionRequiredError(
            server="worker",
            method="session/request_permission",
            prompt="allow?",
        )
        assert err.code is AcpErrorCode.INTERACTION_REQUIRED

    def test_method_only_in_details_not_public_attr(self) -> None:
        err = AcpInteractionRequiredError(
            server="worker",
            method="_agentao.cn/ask_user",
            prompt="say hi",
            options=[{"id": "ok", "label": "OK"}],
        )
        # public: server/prompt/options stable
        assert err.server == "worker"
        assert err.prompt == "say hi"
        assert err.options == [{"id": "ok", "label": "OK"}]
        # method must live in details, not as a public attribute
        assert err.details["method"] == "_agentao.cn/ask_user"
        assert not hasattr(err, "method")

    def test_options_defaults_to_empty_list(self) -> None:
        err = AcpInteractionRequiredError(
            server="x", method="y", prompt="z"
        )
        assert err.options == []
        assert err.details["options"] == []

    def test_caught_by_acp_client_error(self) -> None:
        with pytest.raises(AcpClientError) as exc_info:
            raise AcpInteractionRequiredError(
                server="s", method="m", prompt=""
            )
        assert exc_info.value.code is AcpErrorCode.INTERACTION_REQUIRED


class TestPromptResult:
    def test_minimal_construction(self) -> None:
        r = PromptResult(stop_reason="end_turn")
        assert r.stop_reason == "end_turn"
        assert r.raw == {}
        assert r.session_id is None
        assert r.cwd is None

    def test_full_construction(self) -> None:
        r = PromptResult(
            stop_reason="end_turn",
            raw={"stopReason": "end_turn"},
            session_id="s1",
            cwd="/tmp/work",
        )
        assert r.session_id == "s1"
        assert r.cwd == "/tmp/work"


class TestPackageExports:
    def test_embedding_surface_present(self) -> None:
        import agentao.acp_client as pkg

        stable = {
            "ACPManager",
            "AcpClientConfig",
            "AcpClientError",
            "AcpConfigError",
            "AcpErrorCode",
            "AcpInteractionRequiredError",
            "AcpProcessInfo",
            "AcpRpcError",
            "AcpServerConfig",
            "PromptResult",
            "ServerState",
            "load_acp_client_config",
        }
        for name in stable:
            assert name in pkg.__all__
            assert hasattr(pkg, name)

    def test_legacy_internal_symbols_still_importable(self) -> None:
        """Root-level names that existed before the stable-surface split
        must remain importable for backward compatibility. Prefer the
        concrete submodule — these are re-exported as a compatibility
        shim, not part of the stable contract."""
        import agentao.acp_client as pkg

        legacy = [
            "ACPClient",
            "ACPProcessHandle",
            "AcpConnectionInfo",
            "AcpExplicitRoute",
            "Inbox",
            "InboxMessage",
            "InteractionKind",
            "InteractionRegistry",
            "MessageKind",
            "PendingInteraction",
            "detect_explicit_route",
        ]
        for name in legacy:
            assert hasattr(pkg, name), (
                f"{name!r} must remain importable from agentao.acp_client "
                "as a backward-compat shim"
            )


class TestConnectionInfoHasSessionCwd:
    def test_field_present_and_defaults_none(self) -> None:
        # AcpConnectionInfo is internal: imported from the submodule,
        # not from the package root.
        from agentao.acp_client.client import AcpConnectionInfo

        info = AcpConnectionInfo()
        assert info.session_cwd is None
        assert info.session_mcp_servers_fingerprint is None


# ---------------------------------------------------------------------------
# Phase 2: non-interactive turn control
# ---------------------------------------------------------------------------


class TestNonInteractivePermission:
    def test_auto_rejects_and_raises_interaction_required(
        self, tmp_path: Path
    ) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            with pytest.raises(AcpInteractionRequiredError) as exc_info:
                mgr.send_prompt("srv", "permission", interactive=False, timeout=5)

            err = exc_info.value
            assert err.code is AcpErrorCode.INTERACTION_REQUIRED
            assert err.server == "srv"
            assert err.details["method"] == "session/request_permission"
            # options extracted
            assert any(
                o.get("id") == "reject_once" for o in err.options
            )
            # no pending interaction registered — non-interactive bypasses it
            assert mgr.interactions.pending_count == 0
        finally:
            mgr.stop_all()

    def test_state_never_durably_waiting_for_user(
        self, tmp_path: Path
    ) -> None:
        """During a non-interactive turn the handle stays BUSY — never
        gets exposed as WAITING_FOR_USER — until prompt completion.
        """
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            handle = mgr.get_handle("srv")

            observed_states = []
            stop_probe = threading.Event()

            def probe():
                while not stop_probe.is_set():
                    observed_states.append(handle.state)
                    time.sleep(0.02)

            t = threading.Thread(target=probe, daemon=True)
            t.start()

            with pytest.raises(AcpInteractionRequiredError):
                mgr.send_prompt(
                    "srv", "permission_then_wait",
                    interactive=False, timeout=5,
                )

            stop_probe.set()
            t.join(timeout=1)

            assert ServerState.WAITING_FOR_USER not in observed_states
            assert ServerState.BUSY in observed_states
        finally:
            mgr.stop_all()


class TestNonInteractiveAskUser:
    def test_auto_rejects_ask_user(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            with pytest.raises(AcpInteractionRequiredError) as exc_info:
                mgr.send_prompt("srv", "ask_user", interactive=False, timeout=5)
            assert exc_info.value.details["method"] == "_agentao.cn/ask_user"
            assert mgr.interactions.pending_count == 0
        finally:
            mgr.stop_all()


class TestInteractivePathUnchanged:
    def test_plain_interactive_prompt_succeeds(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            result = mgr.send_prompt("srv", "hello", timeout=5)
            assert result["stopReason"] == "end_turn"
        finally:
            mgr.stop_all()


class TestPerServerLock:
    def test_concurrent_send_prompt_serializes(self, tmp_path: Path) -> None:
        """At most one active turn per named server at any observed moment."""
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()

            def worker() -> None:
                try:
                    mgr.send_prompt("srv", "slow", timeout=10)
                except Exception:
                    pass

            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)

            max_concurrent = [0]
            stop_probe = threading.Event()

            def probe():
                while not stop_probe.is_set():
                    with mgr._active_turns_lock:
                        n = len(mgr._active_turns)
                    if n > max_concurrent[0]:
                        max_concurrent[0] = n
                    time.sleep(0.01)

            tp = threading.Thread(target=probe, daemon=True)
            tp.start()

            t1.start()
            t2.start()

            # Cancel twice so both turns terminate quickly.
            def cancel_soon():
                time.sleep(0.3)
                mgr.cancel_turn("srv")
                time.sleep(0.5)
                mgr.cancel_turn("srv")

            tc = threading.Thread(target=cancel_soon, daemon=True)
            tc.start()

            t1.join(timeout=15)
            t2.join(timeout=15)
            tc.join(timeout=15)
            stop_probe.set()
            tp.join(timeout=1)

            # Probe observed at least one active turn.
            assert max_concurrent[0] >= 1
            # But never more than one simultaneously.
            assert max_concurrent[0] == 1
        finally:
            mgr.stop_all()


class TestCancelWinsOverInteraction:
    def test_cancel_suppresses_latched_interaction(
        self, tmp_path: Path
    ) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()

            def cancel_after_delay():
                time.sleep(0.4)
                mgr.cancel_turn("srv")

            tc = threading.Thread(target=cancel_after_delay, daemon=True)
            tc.start()

            # permission_then_wait: server emits permission request
            # immediately, auto-reject latches an interaction error,
            # then server holds the RPC until cancel arrives. Without
            # cancellation this would raise AcpInteractionRequiredError;
            # with cancellation we expect a normal (cancelled) result.
            result = mgr.send_prompt(
                "srv", "permission_then_wait",
                interactive=False, timeout=10,
            )
            tc.join(timeout=5)
            assert result["stopReason"] == "cancelled"
        finally:
            mgr.stop_all()


class TestPromptOnce:
    def test_success_returns_prompt_result_and_stops_process(
        self, tmp_path: Path
    ) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            result = mgr.prompt_once("srv", "hello", timeout=5)
            assert isinstance(result, PromptResult)
            assert result.stop_reason == "end_turn"
            assert result.session_id == "sess_mock"
            assert result.cwd == str(tmp_path)
            # Ephemeral: not registered as a long-lived client, not in status.
            assert "srv" not in mgr._clients
            assert mgr._ephemeral_clients == {}
            statuses = mgr.get_status()
            assert statuses[0]["state"] in (
                ServerState.STOPPED.value,
                ServerState.CONFIGURED.value,
                ServerState.FAILED.value,
            )
            # Handle is stopped because no long-lived client existed.
            handle = mgr.get_handle("srv")
            assert handle.info.pid is None or handle.state != ServerState.READY
        finally:
            mgr.stop_all()

    def test_interaction_cleanup(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            with pytest.raises(AcpInteractionRequiredError):
                mgr.prompt_once("srv", "permission", timeout=5)
            # Ephemeral client closed, turn slot cleared.
            assert mgr._ephemeral_clients == {}
            with mgr._active_turns_lock:
                assert mgr._active_turns == {}
        finally:
            mgr.stop_all()

    def test_server_busy_when_long_lived_turn_active(
        self, tmp_path: Path
    ) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            started = threading.Event()
            finished = threading.Event()

            def slow_turn():
                started.set()
                try:
                    mgr.send_prompt("srv", "slow", timeout=10)
                finally:
                    finished.set()

            t = threading.Thread(target=slow_turn, daemon=True)
            t.start()
            started.wait(timeout=2)
            # Give the worker time to enter the non-blocking prompt.
            time.sleep(0.3)

            with pytest.raises(AcpClientError) as exc_info:
                mgr.prompt_once("srv", "hello", timeout=2)
            assert exc_info.value.code is AcpErrorCode.SERVER_BUSY
            # And prompt_once must NOT have created an ephemeral client.
            assert mgr._ephemeral_clients == {}

            # Let the slow turn finish.
            mgr.cancel_turn("srv")
            finished.wait(timeout=10)
            t.join(timeout=10)
        finally:
            mgr.stop_all()

    def test_no_stop_when_long_lived_client_exists(
        self, tmp_path: Path
    ) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            # First establish a long-lived client.
            mgr.send_prompt("srv", "hello", timeout=5)
            assert "srv" in mgr._clients
            # prompt_once reuses the existing client; process must survive.
            r = mgr.prompt_once("srv", "hello", timeout=5)
            assert r.stop_reason == "end_turn"
            assert "srv" in mgr._clients  # long-lived client untouched
            handle = mgr.get_handle("srv")
            assert handle.state == ServerState.READY
        finally:
            mgr.stop_all()


class TestSessionReuseSemantics:
    def test_same_cwd_reuses_session(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            mgr.send_prompt("srv", "hello", cwd=str(tmp_path), timeout=5)
            sid1 = mgr._clients["srv"].connection_info.session_id
            mgr.send_prompt("srv", "hello", cwd=str(tmp_path), timeout=5)
            sid2 = mgr._clients["srv"].connection_info.session_id
            assert sid1 == sid2
        finally:
            mgr.stop_all()

    def test_different_cwd_triggers_fresh_session(
        self, tmp_path: Path
    ) -> None:
        mgr = _make_mgr(tmp_path)
        other = tmp_path / "other"
        other.mkdir()
        try:
            mgr.start_all()
            mgr.send_prompt("srv", "hello", cwd=str(tmp_path), timeout=5)
            assert (
                mgr._clients["srv"].connection_info.session_cwd
                == str(tmp_path)
            )
            # Different cwd: ensure_connected must re-session.
            mgr.send_prompt("srv", "hello", cwd=str(other), timeout=5)
            assert (
                mgr._clients["srv"].connection_info.session_cwd == str(other)
            )
        finally:
            mgr.stop_all()

    def test_different_mcp_servers_triggers_fresh_session(
        self, tmp_path: Path
    ) -> None:
        from agentao.acp_client.client import _fingerprint_mcp_servers

        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            mgr.send_prompt("srv", "hello", mcp_servers=[], timeout=5)
            fp_before = (
                mgr._clients["srv"]
                .connection_info.session_mcp_servers_fingerprint
            )
            assert fp_before == _fingerprint_mcp_servers([])
            mgr.send_prompt(
                "srv", "hello",
                mcp_servers=[{"name": "x", "command": "echo"}],
                timeout=5,
            )
            fp_after = (
                mgr._clients["srv"]
                .connection_info.session_mcp_servers_fingerprint
            )
            assert fp_after == _fingerprint_mcp_servers(
                [{"name": "x", "command": "echo"}]
            )
            assert fp_before != fp_after
        finally:
            mgr.stop_all()


class TestTimeoutDoesNotPoisonClient:
    def test_long_lived_client_usable_after_timeout(
        self, tmp_path: Path
    ) -> None:
        """A non-interactive timeout must fully clear client-side slot state
        so the *same* long-lived client can serve the next prompt."""
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            # Prime a long-lived client.
            mgr.send_prompt("srv", "hello", timeout=5)
            client = mgr._clients["srv"]

            # Force a non-interactive timeout via the 'slow' path
            # (mock holds for 5s; we timeout at 0.3s).
            with pytest.raises(AcpClientError) as exc_info:
                mgr.send_prompt(
                    "srv", "slow", interactive=False, timeout=0.3,
                )
            assert exc_info.value.code is AcpErrorCode.REQUEST_TIMEOUT

            # Client-side slot must be clear for the next call to proceed.
            with client._pending_lock:
                assert client._pending == {}
            with client._active_turn_lock:
                assert client._active_turn_id is None

            # Follow-up prompt on the same client must succeed.
            result = mgr.send_prompt("srv", "hello", timeout=5)
            assert result["stopReason"] == "end_turn"
        finally:
            mgr.stop_all()


class TestSessionDefaultsRestoredAfterOverride:
    def test_cwd_none_reverts_to_config_default(self, tmp_path: Path) -> None:
        """After an explicit cwd override, a later call with cwd=None must
        re-session back to the configured default, not silently reuse the
        overridden session."""
        mgr = _make_mgr(tmp_path)
        other = tmp_path / "other"
        other.mkdir()
        try:
            mgr.start_all()
            # Default (config) cwd is tmp_path.
            mgr.send_prompt("srv", "hello", timeout=5)
            assert (
                mgr._clients["srv"].connection_info.session_cwd
                == str(tmp_path)
            )

            # Override to /tmp/other.
            mgr.send_prompt("srv", "hello", cwd=str(other), timeout=5)
            assert (
                mgr._clients["srv"].connection_info.session_cwd == str(other)
            )

            # cwd=None (i.e. plain send_prompt) must restore the default.
            mgr.send_prompt("srv", "hello", timeout=5)
            assert (
                mgr._clients["srv"].connection_info.session_cwd
                == str(tmp_path)
            )
        finally:
            mgr.stop_all()

    def test_mcp_none_reverts_to_empty(self, tmp_path: Path) -> None:
        from agentao.acp_client.client import _fingerprint_mcp_servers

        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            mgr.send_prompt("srv", "hello", timeout=5)
            assert (
                mgr._clients["srv"]
                .connection_info.session_mcp_servers_fingerprint
                == _fingerprint_mcp_servers([])
            )
            mgr.send_prompt(
                "srv", "hello",
                mcp_servers=[{"name": "x", "command": "echo"}],
                timeout=5,
            )
            assert (
                mgr._clients["srv"]
                .connection_info.session_mcp_servers_fingerprint
                == _fingerprint_mcp_servers(
                    [{"name": "x", "command": "echo"}]
                )
            )
            # mcp_servers=None must revert to [].
            mgr.send_prompt("srv", "hello", timeout=5)
            assert (
                mgr._clients["srv"]
                .connection_info.session_mcp_servers_fingerprint
                == _fingerprint_mcp_servers([])
            )
        finally:
            mgr.stop_all()


class TestNonblockingSerialization:
    def test_prompt_once_server_busy_during_nonblocking_turn(
        self, tmp_path: Path
    ) -> None:
        """An in-flight send_prompt_nonblocking turn must make
        prompt_once fail fast with SERVER_BUSY (same single-active-turn
        contract as send_prompt / prompt_once)."""
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            client, rid, slot = mgr.send_prompt_nonblocking("srv", "slow")
            try:
                with pytest.raises(AcpClientError) as exc_info:
                    mgr.prompt_once("srv", "hello", timeout=2)
                assert exc_info.value.code is AcpErrorCode.SERVER_BUSY
                # Turn slot must record an active interactive turn.
                with mgr._active_turns_lock:
                    assert "srv" in mgr._active_turns
                    assert mgr._active_turns["srv"].interactive is True
            finally:
                mgr.cancel_prompt_nonblocking("srv", client, rid)
            # After cancel: lock released, turn cleared, prompt_once ok.
            with mgr._active_turns_lock:
                assert mgr._active_turns == {}
            r = mgr.prompt_once("srv", "hello", timeout=5)
            assert r.stop_reason in ("end_turn", "cancelled")
        finally:
            mgr.stop_all()

    def test_finish_releases_lock(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            client, rid, slot = mgr.send_prompt_nonblocking("srv", "hello")
            assert slot.event.wait(timeout=5)
            result = mgr.finish_prompt_nonblocking("srv", client, rid, slot)
            assert result["stopReason"] == "end_turn"
            # Lock released: a follow-up send_prompt must succeed.
            mgr.send_prompt("srv", "hello", timeout=5)
        finally:
            mgr.stop_all()


class TestEphemeralSetupFailureCleanup:
    def test_initialize_failure_stops_subprocess(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If initialize()/create_session() raises inside
        _open_ephemeral_client, the subprocess must not outlive the
        failed prompt_once attempt."""
        mgr = _make_mgr(tmp_path)
        try:
            # Force initialize to raise so the ephemeral setup aborts.
            from agentao.acp_client.client import ACPClient

            def _boom(self, *a, **kw):
                raise AcpClientError(
                    "synthetic handshake failure",
                    code=AcpErrorCode.HANDSHAKE_FAIL,
                )

            monkeypatch.setattr(ACPClient, "initialize", _boom)

            with pytest.raises(AcpClientError) as exc_info:
                mgr.prompt_once("srv", "hello", timeout=5)
            assert exc_info.value.code is AcpErrorCode.HANDSHAKE_FAIL

            # Ephemeral registry empty.
            assert mgr._ephemeral_clients == {}
            # No long-lived client created.
            assert "srv" not in mgr._clients
            # Subprocess stopped — handle transitioned to STOPPED/FAILED.
            handle = mgr.get_handle("srv")
            assert handle.state in (
                ServerState.STOPPED,
                ServerState.FAILED,
                ServerState.CONFIGURED,
            )
            assert handle.info.pid is None or handle.state != ServerState.READY
        finally:
            mgr.stop_all()


class TestSelectRejectOption:
    """Unit tests for the reject-option selector (server-provided options)."""

    def setup_method(self) -> None:
        from agentao.acp_client.manager import _select_reject_option

        self.select = _select_reject_option

    def test_empty_returns_none(self) -> None:
        assert self.select([]) is None

    def test_canonical_reject_once_wins(self) -> None:
        opts = [
            {"optionId": "allow_once", "kind": "allow_once"},
            {"optionId": "decline_now", "kind": "reject_once"},
        ]
        assert self.select(opts) == "decline_now"

    def test_prefers_canonical_over_variant(self) -> None:
        opts = [
            {"optionId": "r_always", "kind": "reject_always"},
            {"optionId": "r_once", "kind": "reject_once"},
        ]
        assert self.select(opts) == "r_once"

    def test_reject_variant_used_when_no_canonical(self) -> None:
        opts = [
            {"optionId": "allow_once", "kind": "allow_once"},
            {"optionId": "r_always", "kind": "reject_always"},
        ]
        assert self.select(opts) == "r_always"

    def test_falls_back_to_id_hint(self) -> None:
        opts = [
            {"optionId": "yes", "name": "Yes"},
            {"optionId": "no_thanks", "name": "Reject it"},
        ]
        assert self.select(opts) == "no_thanks"

    def test_cancel_hint(self) -> None:
        opts = [{"optionId": "cancel_action", "name": "Cancel"}]
        assert self.select(opts) == "cancel_action"

    def test_no_reject_anywhere_returns_none(self) -> None:
        opts = [
            {"optionId": "ok", "name": "OK"},
            {"optionId": "proceed", "name": "Proceed"},
        ]
        assert self.select(opts) is None


class TestAutoRejectRespectsServerOptions:
    def test_uses_server_provided_reject_option_id(
        self, tmp_path: Path
    ) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            with pytest.raises(AcpInteractionRequiredError):
                mgr.send_prompt(
                    "srv", "permission_alt", interactive=False, timeout=5,
                )
            # No stray pending interactions on the server.
            assert mgr.interactions.pending_count == 0
        finally:
            mgr.stop_all()

    def test_no_options_falls_back_to_cancelled(
        self, tmp_path: Path
    ) -> None:
        """When the permission request has no options at all, the manager
        replies with outcome=cancelled so the server never hangs waiting
        for a valid selection."""
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            with pytest.raises(AcpInteractionRequiredError):
                mgr.send_prompt(
                    "srv", "permission_no_options",
                    interactive=False, timeout=5,
                )
            assert mgr.interactions.pending_count == 0
        finally:
            mgr.stop_all()


class TestClientCreateSessionFailureClearsMetadata:
    def test_create_session_failure_clears_session_fields(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ACPClient.create_session must reset session_id / session_cwd /
        fingerprint when the underlying ``session/new`` RPC fails — so
        downstream code never reuses stale metadata after a rejected
        cwd/mcp override."""
        from agentao.acp_client.client import ACPClient

        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            mgr.send_prompt("srv", "hello", timeout=5)
            client = mgr._clients["srv"]
            assert client.connection_info.session_id is not None

            original_call = client.call

            def _fail_session_new(method, params=None, *, timeout=None):
                if method == "session/new":
                    raise AcpClientError(
                        "synthetic", code=AcpErrorCode.PROTOCOL_ERROR,
                    )
                return original_call(method, params, timeout=timeout)

            monkeypatch.setattr(client, "call", _fail_session_new)

            with pytest.raises(AcpClientError):
                client.create_session(cwd=str(tmp_path / "x"), timeout=5)

            assert client.connection_info.session_id is None
            assert client.connection_info.session_cwd is None
            assert client.connection_info.session_mcp_servers_fingerprint is None
        finally:
            mgr.stop_all()


class TestReSessionFailureInvalidatesCache:
    def test_retry_after_failed_recreate_runs_session_new_again(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If create_session fails during a cwd/mcp re-session, the next
        call must rerun ``session/new`` rather than reusing the old
        session id — but the existing client (and its reader thread) is
        preserved so we don't spawn competing readers on the same pipes.
        """
        mgr = _make_mgr(tmp_path)
        other = tmp_path / "other"
        other.mkdir()
        try:
            mgr.start_all()
            mgr.send_prompt("srv", "hello", cwd=str(tmp_path), timeout=5)
            client = mgr._clients["srv"]
            assert client.connection_info.session_id is not None

            # Intercept the RPC layer so the first session/new fails;
            # subsequent calls (including the retry) pass through.
            original_call = client.call
            calls = {"session_new": 0}

            def _maybe_fail(method, params=None, *, timeout=None):
                if method == "session/new":
                    calls["session_new"] += 1
                    if calls["session_new"] == 1:
                        raise AcpClientError(
                            "synthetic",
                            code=AcpErrorCode.PROTOCOL_ERROR,
                        )
                return original_call(method, params, timeout=timeout)

            monkeypatch.setattr(client, "call", _maybe_fail)

            with pytest.raises(AcpClientError):
                mgr.send_prompt("srv", "hello", cwd=str(other), timeout=5)

            # Client kept in cache (same transport). Session metadata
            # cleared by ACPClient.create_session so no stale match.
            assert mgr._clients["srv"] is client
            assert client.connection_info.session_id is None

            # Retry: must actually call session/new again — the fix
            # must NOT hand back the now-empty metadata as a match.
            calls_before = calls["session_new"]
            mgr.send_prompt("srv", "hello", cwd=str(tmp_path), timeout=5)
            assert calls["session_new"] == calls_before + 1
            assert client.connection_info.session_id is not None
        finally:
            mgr.stop_all()


class TestPromptOnceReusesSessionlessCachedClient:
    def test_sessionless_cached_client_is_resessioned_not_replaced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If a long-lived cached client's last create_session failed
        (``session_id`` cleared), ``prompt_once`` must reuse that client
        and run ``session/new`` on it — not build a second ACPClient on
        the same handle (which would spawn a competing reader thread)."""
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            mgr.send_prompt("srv", "hello", timeout=5)
            cached = mgr._clients["srv"]

            # Simulate a prior failed create_session: clear the session
            # metadata but leave the client in the cache (this is the
            # state ACPClient.create_session leaves behind on RPC error).
            cached.connection_info.session_id = None
            cached.connection_info.session_cwd = None
            cached.connection_info.session_mcp_servers_fingerprint = None

            # Spy on _open_ephemeral_client: it MUST NOT fire.
            called = {"ephemeral": 0}
            original = mgr._open_ephemeral_client

            def _spy(*args, **kwargs):
                called["ephemeral"] += 1
                return original(*args, **kwargs)

            monkeypatch.setattr(mgr, "_open_ephemeral_client", _spy)

            result = mgr.prompt_once("srv", "hello", timeout=5)
            assert result.stop_reason == "end_turn"
            assert called["ephemeral"] == 0
            # Same cached client, with a fresh session.
            assert mgr._clients["srv"] is cached
            assert cached.connection_info.session_id is not None
            # No ephemeral left behind.
            assert mgr._ephemeral_clients == {}
        finally:
            mgr.stop_all()


class TestCleanupOfTurnSlot:
    def test_active_turn_cleared_after_success(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            mgr.send_prompt("srv", "hello", timeout=5)
            with mgr._active_turns_lock:
                assert mgr._active_turns == {}
        finally:
            mgr.stop_all()

    def test_active_turn_cleared_after_exception(
        self, tmp_path: Path
    ) -> None:
        mgr = _make_mgr(tmp_path)
        try:
            mgr.start_all()
            with pytest.raises(AcpInteractionRequiredError):
                mgr.send_prompt(
                    "srv", "permission", interactive=False, timeout=5
                )
            with mgr._active_turns_lock:
                assert mgr._active_turns == {}
        finally:
            mgr.stop_all()
