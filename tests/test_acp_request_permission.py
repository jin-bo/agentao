"""Tests for ACP ``session/request_permission`` — Issue 08.

Covers three layers:

1. **AcpServer plumbing**: ``call()`` writes a JSON-RPC request, the
   returned :class:`_PendingRequest` wakes when ``_route_response`` fires,
   errors and cancellations raise the expected exception types.
2. **ACPTransport.confirm_tool**: the full mapping from Agentao's
   ``(tool_name, description, args)`` call signature to an ACP
   ``session/request_permission`` request, outcome → bool translation,
   session-level overrides, and deterministic failure on disconnect /
   error.
3. **End-to-end via the concurrent dispatcher**: a real ``AcpServer.run``
   instance receives a ``session/prompt`` from stdin, the handler kicks
   off a chat() on a worker thread, the worker synthesizes a
   confirm_tool call, the main read loop routes the injected permission
   response, and the worker returns the correct bool.

All tests use a :class:`FakeAgent` to avoid loading the real LLM stack.
"""

from __future__ import annotations

import io
import json
import queue
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import pytest

from agentao.acp import initialize as acp_initialize
from agentao.acp import session_new as acp_session_new
from agentao.acp import session_prompt as acp_session_prompt
from agentao.acp.models import AcpSessionState
from agentao.acp.protocol import (
    ACP_PROTOCOL_VERSION,
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_REQUEST_PERMISSION,
    METHOD_SESSION_NEW,
    METHOD_SESSION_PROMPT,
)
from agentao.acp.server import (
    AcpServer,
    JsonRpcHandlerError,
    PendingRequestCancelled,
    _PendingRequest,
)
from agentao.acp.transport import (
    ACPTransport,
    PERMISSION_ALLOW_ALWAYS,
    PERMISSION_ALLOW_ONCE,
    PERMISSION_REJECT_ALWAYS,
    PERMISSION_REJECT_ONCE,
    _build_permission_options,
)
from agentao.cancellation import CancellationToken


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class RecordingServer:
    """Stand-in for :class:`AcpServer` that records ``call()`` invocations.

    The transport under test calls ``server.call(method, params)`` and
    blocks on the returned pending. By default, calls return a canned
    outcome supplied in the constructor. Tests that want to simulate
    disconnect or error can substitute a failing ``canned`` factory.
    """

    def __init__(
        self,
        outcome: Optional[Dict[str, Any]] = None,
        *,
        error: Optional[Exception] = None,
        cancelled: bool = False,
    ) -> None:
        self.outcome = outcome
        self.error_to_raise = error
        self.cancelled = cancelled
        self.calls: List[tuple] = []  # list of (method, params)
        # Session registry: the transport looks this up to read/write overrides.
        from agentao.acp.session_manager import AcpSessionManager
        self.sessions = AcpSessionManager()

    def call(self, method: str, params: dict) -> _PendingRequest:
        self.calls.append((method, params))
        pending = _PendingRequest(f"srv_test_{len(self.calls)}")
        if self.cancelled:
            pending.cancelled = True
            pending.event.set()
            return pending
        if self.error_to_raise is not None:
            raise self.error_to_raise
        # Canned success — event is already set so ``wait()`` returns
        # synchronously without blocking the caller's thread.
        pending.result = self.outcome
        pending.event.set()
        return pending


# ---------------------------------------------------------------------------
# Tiny helpers
# ---------------------------------------------------------------------------


def _make_transport(
    server: Any, session_id: str = "sess_test"
) -> ACPTransport:
    return ACPTransport(server=server, session_id=session_id)


def _register_session(server: Any, session_id: str = "sess_test") -> AcpSessionState:
    state = AcpSessionState(session_id=session_id)
    server.sessions.create(state)
    return state


class BlockingStdin:
    """File-like stdin that blocks on readline until a line is pushed.

    ``StringIO`` won't work for the end-to-end permission tests because
    ``StringIO.readline()`` returns ``''`` immediately once EOF is
    reached, so ``AcpServer.run`` bails out and cancels every pending
    outbound request before any injector thread has a chance to route a
    response. A queue-backed stdin lets the test control when EOF lands
    — we push the permission response line and then push a sentinel
    ``None`` to signal EOF explicitly.
    """

    def __init__(self) -> None:
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._closed = False

    def push_line(self, line: str) -> None:
        if not line.endswith("\n"):
            line = line + "\n"
        self._queue.put(line)

    def push_eof(self) -> None:
        self._queue.put(None)

    def readline(self) -> str:
        if self._closed:
            return ""
        item = self._queue.get()
        if item is None:
            self._closed = True
            return ""
        return item


# ===========================================================================
# Part 1 — AcpServer: pending registry + call() + response routing
# ===========================================================================


class TestAcpServerCall:
    def test_call_writes_server_to_client_request_to_stdout(self):
        stdin = io.StringIO("")
        stdout = io.StringIO()
        server = AcpServer(stdin=stdin, stdout=stdout)

        pending = server.call(
            METHOD_REQUEST_PERMISSION,
            {"sessionId": "sess_test", "toolCall": {}, "options": []},
        )

        # The request landed on stdout as a single NDJSON line.
        line = stdout.getvalue().strip()
        payload = json.loads(line)
        assert payload["jsonrpc"] == "2.0"
        assert payload["method"] == METHOD_REQUEST_PERMISSION
        assert payload["id"] == pending.request_id
        assert payload["params"]["sessionId"] == "sess_test"
        # The pending slot is registered and not yet resolved.
        assert pending.request_id in server._pending_requests
        assert not pending.event.is_set()

    def test_call_ids_are_unique_per_call(self):
        server = AcpServer(stdin=io.StringIO(""), stdout=io.StringIO())
        ids = {server.call("m", {}).request_id for _ in range(100)}
        assert len(ids) == 100

    def test_route_response_fills_pending_and_wakes_waiter(self):
        server = AcpServer(stdin=io.StringIO(""), stdout=io.StringIO())
        pending = server.call("m", {})

        result_container: Dict[str, Any] = {}

        def waiter() -> None:
            try:
                result_container["value"] = pending.wait(timeout=5.0)
            except Exception as e:  # pragma: no cover
                result_container["error"] = e

        t = threading.Thread(target=waiter, daemon=True)
        t.start()

        # Simulate the client replying by feeding a response line into
        # the read-loop entry point.
        response_line = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": pending.request_id,
                "result": {"outcome": {"outcome": "selected", "optionId": "allow_once"}},
            }
        )
        server._handle_line(response_line)

        t.join(timeout=5.0)
        assert t.is_alive() is False
        assert result_container["value"] == {
            "outcome": {"outcome": "selected", "optionId": "allow_once"}
        }
        # The pending slot is cleared after routing.
        assert pending.request_id not in server._pending_requests

    def test_route_response_with_error_raises_jsonrpc_handler_error(self):
        server = AcpServer(stdin=io.StringIO(""), stdout=io.StringIO())
        pending = server.call("m", {})

        server._handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": pending.request_id,
                    "error": {"code": -32000, "message": "bad thing"},
                }
            )
        )

        with pytest.raises(JsonRpcHandlerError) as exc:
            pending.wait(timeout=1.0)
        assert exc.value.code == -32000
        assert exc.value.message == "bad thing"

    def test_unknown_response_id_is_dropped_without_crashing_read_loop(self, caplog):
        server = AcpServer(stdin=io.StringIO(""), stdout=io.StringIO())
        # No pending call — a response for an unknown id should log and drop.
        import logging as _logging
        with caplog.at_level(_logging.WARNING, logger="agentao.acp.server"):
            server._handle_line(
                json.dumps(
                    {"jsonrpc": "2.0", "id": "srv_bogus", "result": {"x": 1}}
                )
            )
        assert any("unknown id" in r.message for r in caplog.records)

    def test_cancel_all_pending_raises_cancelled_on_waiter(self):
        server = AcpServer(stdin=io.StringIO(""), stdout=io.StringIO())
        pending = server.call("m", {})

        raised: Dict[str, Any] = {}

        def waiter() -> None:
            try:
                pending.wait(timeout=5.0)
            except PendingRequestCancelled as e:
                raised["exc"] = e

        t = threading.Thread(target=waiter, daemon=True)
        t.start()
        time.sleep(0.02)  # give the waiter a chance to enter wait()
        server._cancel_all_pending_requests("test")
        t.join(timeout=5.0)
        assert "exc" in raised

    def test_pending_wait_with_timeout_raises_timeout(self):
        server = AcpServer(stdin=io.StringIO(""), stdout=io.StringIO())
        pending = server.call("m", {})
        with pytest.raises(TimeoutError):
            pending.wait(timeout=0.05)

    def test_run_finally_cancels_pending_so_disconnect_is_deterministic(self):
        """EOF on stdin must unblock any worker stuck in pending.wait()."""
        stdin = io.StringIO("")  # immediate EOF
        stdout = io.StringIO()
        server = AcpServer(stdin=stdin, stdout=stdout)

        # Pre-register a pending request before run() starts so that the
        # finally clause must cancel it.
        pending = server.call("m", {})
        server.run()
        # run() should have set the pending slot to cancelled.
        assert pending.cancelled is True
        assert pending.event.is_set()


# ===========================================================================
# Part 2 — _handle_line classification
# ===========================================================================


class TestHandleLineClassifiesResponses:
    def test_response_envelope_routed_to_pending(self):
        server = AcpServer(stdin=io.StringIO(""), stdout=io.StringIO())
        pending = server.call("m", {})
        server._handle_line(
            json.dumps({"jsonrpc": "2.0", "id": pending.request_id, "result": "ok"})
        )
        assert pending.wait(timeout=1.0) == "ok"

    def test_message_without_method_or_result_still_returns_invalid_request(self):
        # The legacy test — ``{"jsonrpc":"2.0","id":1}`` has neither method
        # nor result/error. Must stay an INVALID_REQUEST rather than being
        # mis-routed as a response.
        stdout = io.StringIO()
        server = AcpServer(
            stdin=io.StringIO('{"jsonrpc":"2.0","id":1}\n'), stdout=stdout
        )
        server.run()
        parsed = json.loads(stdout.getvalue().strip())
        assert parsed["error"]["code"] == INVALID_REQUEST


# ===========================================================================
# Part 3 — ACPTransport.confirm_tool unit tests
# ===========================================================================


class TestConfirmToolUnit:
    def _allow_once(self) -> Dict[str, Any]:
        return {"outcome": {"outcome": "selected", "optionId": PERMISSION_ALLOW_ONCE}}

    def _reject_once(self) -> Dict[str, Any]:
        return {"outcome": {"outcome": "selected", "optionId": PERMISSION_REJECT_ONCE}}

    def _allow_always(self) -> Dict[str, Any]:
        return {"outcome": {"outcome": "selected", "optionId": PERMISSION_ALLOW_ALWAYS}}

    def _reject_always(self) -> Dict[str, Any]:
        return {"outcome": {"outcome": "selected", "optionId": PERMISSION_REJECT_ALWAYS}}

    def _cancelled(self) -> Dict[str, Any]:
        return {"outcome": {"outcome": "cancelled"}}

    # --- Outcome → bool mapping ------------------------------------------

    def test_allow_once_returns_true(self):
        server = RecordingServer(outcome=self._allow_once())
        _register_session(server)
        transport = _make_transport(server)
        assert transport.confirm_tool("write_file", "write to disk", {"path": "/tmp/x"}) is True

    def test_reject_once_returns_false(self):
        server = RecordingServer(outcome=self._reject_once())
        _register_session(server)
        transport = _make_transport(server)
        assert transport.confirm_tool("bash", "run a shell", {"cmd": "rm -rf /"}) is False

    def test_cancelled_outcome_returns_false(self):
        server = RecordingServer(outcome=self._cancelled())
        _register_session(server)
        transport = _make_transport(server)
        assert transport.confirm_tool("bash", "", {}) is False

    def test_unknown_optionid_returns_false(self):
        server = RecordingServer(
            outcome={"outcome": {"outcome": "selected", "optionId": "bogus"}}
        )
        _register_session(server)
        transport = _make_transport(server)
        assert transport.confirm_tool("bash", "", {}) is False

    def test_unknown_outcome_kind_returns_false(self):
        server = RecordingServer(
            outcome={"outcome": {"outcome": "wat"}}
        )
        _register_session(server)
        transport = _make_transport(server)
        assert transport.confirm_tool("bash", "", {}) is False

    def test_non_dict_result_returns_false(self):
        server = RecordingServer(outcome="not a dict")  # type: ignore[arg-type]
        _register_session(server)
        transport = _make_transport(server)
        assert transport.confirm_tool("bash", "", {}) is False

    def test_flat_outcome_shape_also_accepted(self):
        """Some clients flatten to ``{"outcome": "selected", "optionId": ...}``."""
        server = RecordingServer(
            outcome={"outcome": "selected", "optionId": PERMISSION_ALLOW_ONCE}
        )
        _register_session(server)
        transport = _make_transport(server)
        assert transport.confirm_tool("bash", "", {}) is True

    # --- Request payload shape -------------------------------------------

    def test_sent_request_matches_acp_shape(self):
        server = RecordingServer(outcome=self._allow_once())
        _register_session(server, session_id="sess_payload")
        transport = _make_transport(server, session_id="sess_payload")
        transport.confirm_tool(
            "read_file",
            "read a file off disk",
            {"file_path": "/tmp/x.md"},
        )

        assert len(server.calls) == 1
        method, params = server.calls[0]
        assert method == METHOD_REQUEST_PERMISSION
        assert params["sessionId"] == "sess_payload"

        tool_call = params["toolCall"]
        assert tool_call["title"] == "read_file"
        assert tool_call["kind"] == "read"
        assert tool_call["status"] == "pending"
        assert tool_call["rawInput"] == {"file_path": "/tmp/x.md"}
        assert tool_call["toolCallId"].startswith("call_")
        # Description is attached as a text content entry.
        assert tool_call["content"] == [
            {"type": "content", "content": {"type": "text", "text": "read a file off disk"}}
        ]

        # All four options are always offered.
        option_ids = [o["optionId"] for o in params["options"]]
        assert PERMISSION_ALLOW_ONCE in option_ids
        assert PERMISSION_ALLOW_ALWAYS in option_ids
        assert PERMISSION_REJECT_ONCE in option_ids
        assert PERMISSION_REJECT_ALWAYS in option_ids

    def test_empty_description_omits_content_field(self):
        server = RecordingServer(outcome=self._allow_once())
        _register_session(server)
        transport = _make_transport(server)
        transport.confirm_tool("bash", "", {})
        _, params = server.calls[0]
        assert "content" not in params["toolCall"]

    # --- Session overrides -----------------------------------------------

    def test_allow_always_updates_session_overrides(self):
        server = RecordingServer(outcome=self._allow_always())
        state = _register_session(server)
        transport = _make_transport(server)
        assert transport.confirm_tool("bash", "", {}) is True
        # The override is recorded for next time.
        assert state.permission_overrides == {"bash": True}

    def test_reject_always_updates_session_overrides(self):
        server = RecordingServer(outcome=self._reject_always())
        state = _register_session(server)
        transport = _make_transport(server)
        assert transport.confirm_tool("bash", "", {}) is False
        assert state.permission_overrides == {"bash": False}

    def test_allow_always_short_circuits_subsequent_calls(self):
        """After allow_always, the next call must NOT hit the wire."""
        server = RecordingServer(outcome=self._allow_always())
        _register_session(server)
        transport = _make_transport(server)

        assert transport.confirm_tool("bash", "", {}) is True
        assert len(server.calls) == 1  # first call hit the wire

        # Second call on the same tool: should short-circuit.
        assert transport.confirm_tool("bash", "", {}) is True
        assert len(server.calls) == 1  # still 1 — no extra request sent

    def test_reject_always_short_circuits_subsequent_calls(self):
        server = RecordingServer(outcome=self._reject_always())
        _register_session(server)
        transport = _make_transport(server)
        assert transport.confirm_tool("bash", "", {}) is False
        assert transport.confirm_tool("bash", "", {}) is False
        assert len(server.calls) == 1

    def test_overrides_are_scoped_per_tool_name(self):
        """allow_always for tool A must NOT leak to tool B."""
        server = RecordingServer(outcome=self._allow_always())
        _register_session(server)
        transport = _make_transport(server)

        transport.confirm_tool("bash", "", {})  # now allow-always
        # Same transport, different tool → must hit the wire again.
        transport.confirm_tool("write_file", "", {})
        assert len(server.calls) == 2
        assert server.calls[0][1]["toolCall"]["title"] == "bash"
        assert server.calls[1][1]["toolCall"]["title"] == "write_file"

    # --- Failure modes ---------------------------------------------------

    def test_cancelled_pending_returns_false(self):
        """Client disconnect while waiting → deterministic False."""
        server = RecordingServer(outcome=None, cancelled=True)
        _register_session(server)
        transport = _make_transport(server)
        assert transport.confirm_tool("bash", "", {}) is False

    def test_server_raises_on_call_returns_false(self):
        server = RecordingServer(error=RuntimeError("write failed"))
        _register_session(server)
        transport = _make_transport(server)
        assert transport.confirm_tool("bash", "", {}) is False

    def test_missing_session_returns_false(self):
        """Defensive: confirm_tool without a registered session must not crash."""
        server = RecordingServer(outcome=self._allow_once())
        # Intentionally DO NOT register a session.
        transport = _make_transport(server, session_id="sess_unknown")
        assert transport.confirm_tool("bash", "", {}) is False
        # And no request was sent since we short-circuited before call().
        assert server.calls == []

    def test_none_server_returns_false(self):
        transport = ACPTransport(server=None, session_id="sess_test")
        assert transport.confirm_tool("bash", "", {}) is False

    def test_error_response_returns_false(self):
        """A JSON-RPC error response from the client → False, no crash."""

        class ErrorServer:
            def __init__(self) -> None:
                from agentao.acp.session_manager import AcpSessionManager
                self.sessions = AcpSessionManager()
                self.calls: List[tuple] = []

            def call(self, method: str, params: dict) -> _PendingRequest:
                self.calls.append((method, params))
                pending = _PendingRequest("srv_err")
                from agentao.acp.models import JsonRpcError
                pending.error = JsonRpcError(code=-32001, message="client borked")
                pending.event.set()
                return pending

        server = ErrorServer()
        _register_session(server)
        transport = _make_transport(server)
        assert transport.confirm_tool("bash", "", {}) is False


# ===========================================================================
# Part 4 — Serialization: multiple permission prompts for one session
# ===========================================================================


class TestPermissionPromptSerialization:
    def test_sequential_calls_are_naturally_serial(self):
        """confirm_tool in Agentao's tool_runner is called in Phase 2 one
        tool at a time. Verify that each call's override persists and
        correctly short-circuits the next one — which is the serialization
        contract that matters."""
        from agentao.acp.transport import PERMISSION_ALLOW_ALWAYS

        server = RecordingServer(
            outcome={"outcome": {"outcome": "selected", "optionId": PERMISSION_ALLOW_ALWAYS}}
        )
        state = _register_session(server)
        transport = _make_transport(server)

        # Three consecutive confirmations for the same tool — the first
        # hits the wire, the other two short-circuit.
        for _ in range(3):
            assert transport.confirm_tool("write_file", "", {}) is True

        assert len(server.calls) == 1  # only the first went out
        assert state.permission_overrides == {"write_file": True}

    def test_interleaved_different_tools_each_get_their_own_override(self):
        """allow_always on two tools in sequence leaves both recorded."""
        from agentao.acp.transport import PERMISSION_ALLOW_ALWAYS

        server = RecordingServer(
            outcome={"outcome": {"outcome": "selected", "optionId": PERMISSION_ALLOW_ALWAYS}}
        )
        state = _register_session(server)
        transport = _make_transport(server)

        transport.confirm_tool("write_file", "", {})
        transport.confirm_tool("bash", "", {})
        # Both subsequent calls short-circuit.
        transport.confirm_tool("write_file", "", {})
        transport.confirm_tool("bash", "", {})

        assert len(server.calls) == 2
        assert state.permission_overrides == {"write_file": True, "bash": True}


# ===========================================================================
# Part 5 — End-to-end: real AcpServer + executor + concurrent dispatch
# ===========================================================================


class ConfirmingFakeAgent:
    """A FakeAgent that triggers one confirm_tool call during chat().

    Used in the end-to-end tests to exercise the full worker → call →
    pending.wait() → routed response → worker wakes → handler returns
    round-trip through a real :class:`AcpServer` instance.
    """

    def __init__(
        self,
        tool_name: str = "bash",
        tool_desc: str = "run a shell command",
        tool_args: Optional[Dict[str, Any]] = None,
        transport_ref: Optional[Callable[[], ACPTransport]] = None,
    ) -> None:
        self.tool_name = tool_name
        self.tool_desc = tool_desc
        self.tool_args = tool_args or {"cmd": "echo hi"}
        self.transport_ref = transport_ref
        self.confirm_result: Optional[bool] = None
        self.chat_calls = 0
        self.close_calls = 0

    def chat(
        self,
        user_message: str,
        max_iterations: int = 100,
        cancellation_token: Optional[CancellationToken] = None,
    ) -> str:
        self.chat_calls += 1
        # Resolve the transport at call time — the factory wires it.
        transport = self.transport_ref() if self.transport_ref else None
        assert transport is not None
        self.confirm_result = transport.confirm_tool(
            self.tool_name, self.tool_desc, self.tool_args
        )
        return f"done (confirm={self.confirm_result})"

    def close(self) -> None:
        self.close_calls += 1


class TestEndToEndRequestPermission:
    def _setup_server_with_confirming_agent(
        self, tmp_path, inject_response: Callable[[AcpServer, str], Optional[str]]
    ) -> tuple:
        """Build a full server + session + confirming agent that will
        issue a confirm_tool() during chat(). Returns (server, agent,
        pending_id_holder) so the test can assert post-run state.
        """
        server = AcpServer(stdin=io.StringIO(""), stdout=io.StringIO())
        acp_initialize.handle_initialize(
            server,
            {
                "protocolVersion": ACP_PROTOCOL_VERSION,
                "clientCapabilities": {},
            },
        )
        agent_holder: Dict[str, Any] = {}

        def factory(**kwargs: Any) -> ConfirmingFakeAgent:
            transport = kwargs["transport"]
            agent = ConfirmingFakeAgent(transport_ref=lambda: transport)
            agent_holder["agent"] = agent
            agent_holder["transport"] = transport
            return agent

        acp_session_new.handle_session_new(
            server,
            {"cwd": str(tmp_path), "mcpServers": []},
            agent_factory=factory,
        )
        sid = server.sessions.session_ids()[0]
        acp_session_prompt.register(server)

        return server, agent_holder, sid

    def _run_with_response_injection(
        self,
        server: AcpServer,
        request_line: str,
        response_outcome: Dict[str, Any],
    ) -> str:
        """Drive a ``session/prompt`` turn and inject a canned permission
        response once the permission request lands on stdout.

        Uses a :class:`BlockingStdin` so the read loop stays alive while
        the worker is inside ``transport.confirm_tool``. A helper thread
        polls ``server._pending_requests`` for the outbound permission
        request, pushes the matching response envelope into stdin, then
        pushes EOF so the server shuts down cleanly.
        """
        stdin = BlockingStdin()
        server._in = stdin  # type: ignore[assignment]
        server._out = io.StringIO()

        # Kick off the prompt first. The worker will eventually block in
        # call() while waiting on a permission response — at which point
        # our injector thread will notice and push the response line.
        stdin.push_line(request_line)

        def injector() -> None:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                with server._pending_lock:
                    pending_ids = [
                        pid
                        for pid in server._pending_requests.keys()
                        if pid.startswith("srv_")
                    ]
                if pending_ids:
                    response = {
                        "jsonrpc": "2.0",
                        "id": pending_ids[0],
                        "result": response_outcome,
                    }
                    stdin.push_line(json.dumps(response))
                    # Give the handler a moment to write its session/prompt
                    # response, then close stdin to end run().
                    time.sleep(0.2)
                    stdin.push_eof()
                    return
                time.sleep(0.005)
            # Timeout — force EOF so run() exits.
            stdin.push_eof()

        t = threading.Thread(target=injector, daemon=True)
        t.start()

        try:
            server.run()
        finally:
            t.join(timeout=5.0)

        return server._out.getvalue()

    def test_end_to_end_allow_once(self, tmp_path):
        server, holder, sid = self._setup_server_with_confirming_agent(
            tmp_path, inject_response=lambda s, pid: None
        )
        request_line = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 42,
                "method": METHOD_SESSION_PROMPT,
                "params": {
                    "sessionId": sid,
                    "prompt": [{"type": "text", "text": "run bash"}],
                },
            }
        )

        stdout = self._run_with_response_injection(
            server,
            request_line,
            {"outcome": {"outcome": "selected", "optionId": PERMISSION_ALLOW_ONCE}},
        )

        # Parse every line of stdout. Expect:
        #   - one session/request_permission request (no id from client POV)
        #   - one session/prompt response with stopReason=end_turn
        lines = [ln for ln in stdout.splitlines() if ln.strip()]
        parsed = [json.loads(ln) for ln in lines]

        # Find the outbound permission request and the prompt response.
        perm_req = next(
            (p for p in parsed if p.get("method") == METHOD_REQUEST_PERMISSION),
            None,
        )
        prompt_resp = next(
            (p for p in parsed if p.get("id") == 42),
            None,
        )
        assert perm_req is not None, "server never sent session/request_permission"
        assert prompt_resp is not None, "no response for the session/prompt request"
        assert prompt_resp["result"]["stopReason"] == "end_turn"

        # The FakeAgent saw True from confirm_tool.
        assert holder["agent"].confirm_result is True

    def test_end_to_end_reject_once(self, tmp_path):
        server, holder, sid = self._setup_server_with_confirming_agent(
            tmp_path, inject_response=lambda s, pid: None
        )
        request_line = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": METHOD_SESSION_PROMPT,
                "params": {
                    "sessionId": sid,
                    "prompt": [{"type": "text", "text": "please reject"}],
                },
            }
        )
        stdout = self._run_with_response_injection(
            server,
            request_line,
            {"outcome": {"outcome": "selected", "optionId": PERMISSION_REJECT_ONCE}},
        )
        lines = [ln for ln in stdout.splitlines() if ln.strip()]
        parsed = [json.loads(ln) for ln in lines]
        prompt_resp = next(p for p in parsed if p.get("id") == 7)
        assert prompt_resp["result"]["stopReason"] == "end_turn"
        assert holder["agent"].confirm_result is False

    def test_end_to_end_disconnect_rejects_tool(self, tmp_path):
        """Client disconnect before permission response → deterministic False.

        Race safety: we use a :class:`BlockingStdin` and a helper that
        waits for the pending permission request to appear, *then* pushes
        EOF. This guarantees the worker has actually created its pending
        slot (and is blocked on ``wait()``) before we drop the connection.
        """
        server, holder, sid = self._setup_server_with_confirming_agent(
            tmp_path, inject_response=lambda s, pid: None
        )
        request_line = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 99,
                "method": METHOD_SESSION_PROMPT,
                "params": {
                    "sessionId": sid,
                    "prompt": [{"type": "text", "text": "disconnect me"}],
                },
            }
        )

        stdin = BlockingStdin()
        server._in = stdin  # type: ignore[assignment]
        server._out = io.StringIO()
        stdin.push_line(request_line)

        def disconnector() -> None:
            # Wait until we see the outbound permission request, then EOF.
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                with server._pending_lock:
                    has_pending = any(
                        pid.startswith("srv_")
                        for pid in server._pending_requests.keys()
                    )
                if has_pending:
                    stdin.push_eof()
                    return
                time.sleep(0.005)
            stdin.push_eof()

        t = threading.Thread(target=disconnector, daemon=True)
        t.start()
        try:
            server.run()
        finally:
            t.join(timeout=5.0)

        # The session/prompt must still respond. Two things happen on
        # disconnect (in this order, see ``AcpServer.run``'s finally):
        #   1. _cancel_all_pending_requests unblocks ``confirm_tool`` →
        #      it returns False so the worker records "tool rejected".
        #   2. cancel_all_active_turns trips the per-turn cancel token →
        #      the next iteration of the agent loop raises
        #      ``AgentCancelledError`` and the handler reports
        #      ``stopReason: cancelled``.
        # The previous behavior (``end_turn``) relied on the cancel token
        # NOT being tripped on shutdown, which left workers mid-turn
        # without a stop signal and could hang shutdown indefinitely if
        # they were not blocked on a pending request.
        lines = [ln for ln in server._out.getvalue().splitlines() if ln.strip()]
        parsed = [json.loads(ln) for ln in lines]
        prompt_resp = next((p for p in parsed if p.get("id") == 99), None)
        assert prompt_resp is not None
        assert prompt_resp["result"]["stopReason"] == "cancelled"

        # confirm_tool still saw False because its pending was cancelled
        # before the per-turn token was tripped.
        assert holder["agent"].confirm_result is False


# ===========================================================================
# Part 6 — _build_permission_options sanity check
# ===========================================================================


def test_build_permission_options_returns_all_four_kinds():
    opts = _build_permission_options()
    assert len(opts) == 4
    kinds = {o["kind"] for o in opts}
    assert kinds == {
        PERMISSION_ALLOW_ONCE,
        PERMISSION_ALLOW_ALWAYS,
        PERMISSION_REJECT_ONCE,
        PERMISSION_REJECT_ALWAYS,
    }
    # optionId must equal kind so clients can echo it back unambiguously.
    for o in opts:
        assert o["optionId"] == o["kind"]
        assert isinstance(o["name"], str) and o["name"]
