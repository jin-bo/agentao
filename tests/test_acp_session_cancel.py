"""Tests for the ACP ``session/cancel`` handler (Issue 09).

Three layers of coverage:

1. **Unit-level**: param validation, session lookup, idempotency, the
   no-op paths (no active turn / already cancelled / closed session).
2. **Integration**: bind a fresh :class:`CancellationToken` to a
   :class:`AcpSessionState`, fire ``session/cancel``, observe that the
   token's ``is_cancelled`` flips and that the token's reason is set.
3. **End-to-end**: drive a real :class:`AcpServer` through the
   concurrent dispatcher with a stalled :class:`StallingFakeAgent`
   inside ``session/prompt``, send ``session/cancel`` over stdin, and
   verify that the prompt response surfaces ``stopReason: "cancelled"``
   and that ``run()`` exits cleanly without hanging.

Test doubles and server builders live in :mod:`tests.support.acp_agents`
and :mod:`tests.support.acp_server`.
"""

from __future__ import annotations

import io
import json
import queue
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest

from agentao.acp import initialize as acp_initialize
from agentao.acp import session_cancel as acp_session_cancel
from agentao.acp import session_new as acp_session_new
from agentao.acp import session_prompt as acp_session_prompt
from agentao.acp.models import AcpSessionState
from agentao.acp.protocol import (
    ACP_PROTOCOL_VERSION,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_SESSION_CANCEL,
    METHOD_SESSION_PROMPT,
    SERVER_NOT_INITIALIZED,
)
from agentao.acp.server import AcpServer, JsonRpcHandlerError
from agentao.cancellation import CancellationToken

from .support.acp_agents import StallingFakeAgent
from .support.acp_server import make_initialized_server, make_server


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def server():
    return make_server()


@pytest.fixture
def initialized_server():
    return make_initialized_server()


def _register_session(
    server: AcpServer,
    session_id: str = "sess_test",
    *,
    with_token: bool = False,
) -> AcpSessionState:
    """Create a bare session state and register it on the server.

    If ``with_token`` is True, attach a fresh :class:`CancellationToken`
    so the cancel handler has something to fire.
    """
    state = AcpSessionState(session_id=session_id)
    if with_token:
        state.cancel_token = CancellationToken()
    server.sessions.create(state)
    return state


# ===========================================================================
# Pre-initialize guard
# ===========================================================================


def test_cancel_before_initialize_raises_server_not_initialized(server):
    with pytest.raises(JsonRpcHandlerError) as exc:
        acp_session_cancel.handle_session_cancel(
            server, {"sessionId": "sess_x"}
        )
    assert exc.value.code == SERVER_NOT_INITIALIZED
    assert "initialize" in exc.value.message.lower()


# ===========================================================================
# Param shape validation
# ===========================================================================


class TestParamValidation:
    def test_params_must_be_dict(self, initialized_server):
        with pytest.raises(TypeError, match="JSON object"):
            acp_session_cancel.handle_session_cancel(initialized_server, [])

    def test_params_none_raises(self, initialized_server):
        with pytest.raises(TypeError, match="JSON object"):
            acp_session_cancel.handle_session_cancel(initialized_server, None)

    def test_missing_session_id_raises(self, initialized_server):
        with pytest.raises(TypeError, match="sessionId must be a non-empty string"):
            acp_session_cancel.handle_session_cancel(initialized_server, {})

    def test_empty_session_id_raises(self, initialized_server):
        with pytest.raises(TypeError, match="sessionId must be a non-empty string"):
            acp_session_cancel.handle_session_cancel(
                initialized_server, {"sessionId": ""}
            )

    def test_non_string_session_id_raises(self, initialized_server):
        with pytest.raises(TypeError, match="sessionId must be a non-empty string"):
            acp_session_cancel.handle_session_cancel(
                initialized_server, {"sessionId": 123}
            )


# ===========================================================================
# Session lookup
# ===========================================================================


class TestSessionLookup:
    def test_unknown_session_raises_invalid_request(self, initialized_server):
        with pytest.raises(JsonRpcHandlerError) as exc:
            acp_session_cancel.handle_session_cancel(
                initialized_server, {"sessionId": "sess_does_not_exist"}
            )
        assert exc.value.code == INVALID_REQUEST
        assert "unknown sessionId" in exc.value.message

    def test_closed_session_is_silent_noop(self, initialized_server, caplog):
        state = _register_session(initialized_server, with_token=True)
        state.closed = True
        # Should NOT raise — closed sessions absorb cancels harmlessly.
        result = acp_session_cancel.handle_session_cancel(
            initialized_server, {"sessionId": state.session_id}
        )
        assert result is None
        # Token must NOT be fired (we never touched it).
        assert state.cancel_token.is_cancelled is False


# ===========================================================================
# No active turn — idempotent quiet success
# ===========================================================================


class TestNoActiveTurn:
    def test_session_with_no_token_is_silent_noop(self, initialized_server):
        state = _register_session(initialized_server, with_token=False)
        assert state.cancel_token is None
        result = acp_session_cancel.handle_session_cancel(
            initialized_server, {"sessionId": state.session_id}
        )
        assert result is None

    def test_double_cancel_is_idempotent(self, initialized_server):
        """Cancel twice in a row — second call must be a clean no-op."""
        state = _register_session(initialized_server, with_token=True)
        token = state.cancel_token

        acp_session_cancel.handle_session_cancel(
            initialized_server, {"sessionId": state.session_id}
        )
        assert token.is_cancelled is True

        # Same token still attached — second cancel is a no-op but must
        # not raise or change anything.
        acp_session_cancel.handle_session_cancel(
            initialized_server, {"sessionId": state.session_id}
        )
        assert token.is_cancelled is True

    def test_cancel_after_token_cleared_is_noop(self, initialized_server):
        """If session_prompt's finally cleared cancel_token before cancel
        arrives, the handler must absorb it silently."""
        state = _register_session(initialized_server, with_token=True)
        # Simulate session_prompt finishing and clearing the token.
        state.cancel_token = None
        # Should not raise.
        acp_session_cancel.handle_session_cancel(
            initialized_server, {"sessionId": state.session_id}
        )


# ===========================================================================
# Token firing — the happy path
# ===========================================================================


class TestTokenFiring:
    def test_active_token_is_fired_with_acp_reason(self, initialized_server):
        state = _register_session(initialized_server, with_token=True)
        token = state.cancel_token
        assert token.is_cancelled is False

        result = acp_session_cancel.handle_session_cancel(
            initialized_server, {"sessionId": state.session_id}
        )
        assert result is None
        assert token.is_cancelled is True
        # Reason is set so the runtime can attribute the cancel.
        assert "acp" in token._reason  # noqa: SLF001  — internal accessor for test

    def test_token_reference_is_not_dropped_by_handler(self, initialized_server):
        """Defensive: the handler must not clear cancel_token itself.
        That's session_prompt's finally clause's job."""
        state = _register_session(initialized_server, with_token=True)
        token_before = state.cancel_token
        acp_session_cancel.handle_session_cancel(
            initialized_server, {"sessionId": state.session_id}
        )
        assert state.cancel_token is token_before  # still attached


# ===========================================================================
# Notification mode — errors silenced by dispatcher
# ===========================================================================


class TestNotificationMode:
    def test_notification_dispatch_swallows_errors(self):
        """A malformed session/cancel notification must NOT produce a
        response. The handler raises TypeError, the dispatcher catches
        it (because the request had no id), and stdout stays clean."""
        stdin = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "method": METHOD_SESSION_CANCEL, "params": {}})
            + "\n"
        )
        stdout = io.StringIO()
        server = AcpServer(stdin=stdin, stdout=stdout)
        # Initialize so the handler doesn't bail out on the init guard.
        acp_initialize.handle_initialize(
            server, {"protocolVersion": ACP_PROTOCOL_VERSION, "clientCapabilities": {}}
        )
        acp_session_cancel.register(server)

        server.run()

        # Notification → no response written.
        assert stdout.getvalue() == ""

    def test_notification_with_unknown_session_writes_no_response(self):
        stdin = io.StringIO(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": METHOD_SESSION_CANCEL,
                    "params": {"sessionId": "sess_no_such"},
                }
            )
            + "\n"
        )
        stdout = io.StringIO()
        server = AcpServer(stdin=stdin, stdout=stdout)
        acp_initialize.handle_initialize(
            server, {"protocolVersion": ACP_PROTOCOL_VERSION, "clientCapabilities": {}}
        )
        acp_session_cancel.register(server)
        server.run()
        assert stdout.getvalue() == ""

    def test_notification_against_active_token_still_fires_it(self):
        stdin = io.StringIO("")
        stdout = io.StringIO()
        server = AcpServer(stdin=stdin, stdout=stdout)
        acp_initialize.handle_initialize(
            server, {"protocolVersion": ACP_PROTOCOL_VERSION, "clientCapabilities": {}}
        )
        state = _register_session(server, with_token=True)
        token = state.cancel_token

        # Build a notification (no id) with our session id.
        cancel_line = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": METHOD_SESSION_CANCEL,
                "params": {"sessionId": state.session_id},
            }
        )
        server._in = io.StringIO(cancel_line + "\n")
        server._out = io.StringIO()
        acp_session_cancel.register(server)
        server.run()

        assert token.is_cancelled is True
        # Notification → no response.
        assert server._out.getvalue() == ""


# ===========================================================================
# Request mode — explicit response
# ===========================================================================


class TestRequestMode:
    def test_request_with_id_returns_null_result(self):
        """Some clients send session/cancel as a request. Honor it."""
        stdin = io.StringIO("")
        stdout = io.StringIO()
        server = AcpServer(stdin=stdin, stdout=stdout)
        acp_initialize.handle_initialize(
            server, {"protocolVersion": ACP_PROTOCOL_VERSION, "clientCapabilities": {}}
        )
        state = _register_session(server, with_token=True)

        line = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 17,
                "method": METHOD_SESSION_CANCEL,
                "params": {"sessionId": state.session_id},
            }
        )
        server._in = io.StringIO(line + "\n")
        server._out = io.StringIO()
        acp_session_cancel.register(server)
        server.run()

        response = json.loads(server._out.getvalue().strip())
        assert response["id"] == 17
        assert response["result"] is None

    def test_request_with_bad_params_returns_invalid_params(self):
        stdin = io.StringIO("")
        stdout = io.StringIO()
        server = AcpServer(stdin=stdin, stdout=stdout)
        acp_initialize.handle_initialize(
            server, {"protocolVersion": ACP_PROTOCOL_VERSION, "clientCapabilities": {}}
        )

        line = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": METHOD_SESSION_CANCEL,
                "params": {},  # missing sessionId
            }
        )
        server._in = io.StringIO(line + "\n")
        server._out = io.StringIO()
        acp_session_cancel.register(server)
        server.run()

        resp = json.loads(server._out.getvalue().strip())
        assert resp["error"]["code"] == INVALID_PARAMS

    def test_request_with_unknown_session_returns_invalid_request(self):
        stdin = io.StringIO("")
        stdout = io.StringIO()
        server = AcpServer(stdin=stdin, stdout=stdout)
        acp_initialize.handle_initialize(
            server, {"protocolVersion": ACP_PROTOCOL_VERSION, "clientCapabilities": {}}
        )

        line = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": METHOD_SESSION_CANCEL,
                "params": {"sessionId": "sess_unknown"},
            }
        )
        server._in = io.StringIO(line + "\n")
        server._out = io.StringIO()
        acp_session_cancel.register(server)
        server.run()

        resp = json.loads(server._out.getvalue().strip())
        assert resp["error"]["code"] == INVALID_REQUEST


# ===========================================================================
# Registration helper
# ===========================================================================


def test_register_populates_handler_dict(initialized_server):
    acp_session_cancel.register(initialized_server)
    assert METHOD_SESSION_CANCEL in initialized_server._handlers


# ===========================================================================
# End-to-end: cancel a turn that is mid-flight
# ===========================================================================


class BlockingStdin:
    """Queue-backed stdin for end-to-end tests.

    StringIO returns ``''`` immediately on EOF, which races the worker
    pool. A queue lets the test control exactly when EOF lands so we
    can guarantee the cancel arrives before run() exits.
    """

    def __init__(self) -> None:
        self._q: "queue.Queue[Optional[str]]" = queue.Queue()
        self._closed = False

    def push_line(self, line: str) -> None:
        if not line.endswith("\n"):
            line += "\n"
        self._q.put(line)

    def push_eof(self) -> None:
        self._q.put(None)

    def readline(self) -> str:
        if self._closed:
            return ""
        item = self._q.get()
        if item is None:
            self._closed = True
            return ""
        return item


class TestEndToEndCancel:
    def _build_server(self, tmp_path) -> Tuple[AcpServer, str, StallingFakeAgent]:
        server = AcpServer(stdin=io.StringIO(""), stdout=io.StringIO())
        acp_initialize.handle_initialize(
            server,
            {"protocolVersion": ACP_PROTOCOL_VERSION, "clientCapabilities": {}},
        )

        agent = StallingFakeAgent()

        def factory(**kwargs: Any) -> StallingFakeAgent:
            return agent

        acp_session_new.handle_session_new(
            server,
            {"cwd": str(tmp_path), "mcpServers": []},
            agent_factory=factory,
        )
        sid = server.sessions.session_ids()[0]
        acp_session_prompt.register(server)
        acp_session_cancel.register(server)
        return server, sid, agent

    def test_cancel_unblocks_in_flight_prompt(self, tmp_path):
        """Drive a session/prompt that stalls until cancelled. Send a
        session/cancel notification while it's in flight; expect the
        prompt to respond with stopReason=cancelled and run() to exit
        cleanly without hanging."""
        server, sid, agent = self._build_server(tmp_path)

        prompt_line = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": METHOD_SESSION_PROMPT,
                "params": {
                    "sessionId": sid,
                    "prompt": [{"type": "text", "text": "stall please"}],
                },
            }
        )
        cancel_line = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": METHOD_SESSION_CANCEL,
                "params": {"sessionId": sid},
            }
        )

        stdin = BlockingStdin()
        server._in = stdin  # type: ignore[assignment]
        server._out = io.StringIO()

        # Helper thread that pushes the prompt, waits until the agent is
        # actually inside chat(), then pushes the cancel and finally EOF.
        def driver() -> None:
            stdin.push_line(prompt_line)
            assert agent.entered.wait(5.0), "chat() never entered"
            stdin.push_line(cancel_line)
            # Give the cancel time to propagate through the dispatcher
            # → handler → token.cancel(); the worker's chat poll is on
            # a 5ms cycle so 100ms is plenty.
            time.sleep(0.1)
            stdin.push_eof()

        t = threading.Thread(target=driver, daemon=True)
        t.start()
        try:
            server.run()
        finally:
            t.join(timeout=5.0)

        # The agent observed the cancellation flag.
        assert agent.observed_cancellation is True
        # And run()'s shutdown closed the session-owned agent.
        assert agent.close_calls == 1

        # The session/prompt response surfaces stopReason=cancelled.
        lines = [ln for ln in server._out.getvalue().splitlines() if ln.strip()]
        parsed = [json.loads(ln) for ln in lines]
        prompt_resp = next((p for p in parsed if p.get("id") == 1), None)
        assert prompt_resp is not None
        assert prompt_resp["result"] == {"stopReason": "cancelled"}

    def test_repeated_cancels_are_safe(self, tmp_path):
        """Three cancels in a row for the same in-flight turn must not
        crash, hang, or change the outcome."""
        server, sid, agent = self._build_server(tmp_path)

        prompt_line = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": METHOD_SESSION_PROMPT,
                "params": {
                    "sessionId": sid,
                    "prompt": [{"type": "text", "text": "stall again"}],
                },
            }
        )
        cancel_line = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": METHOD_SESSION_CANCEL,
                "params": {"sessionId": sid},
            }
        )

        stdin = BlockingStdin()
        server._in = stdin  # type: ignore[assignment]
        server._out = io.StringIO()

        def driver() -> None:
            stdin.push_line(prompt_line)
            assert agent.entered.wait(5.0)
            # Three back-to-back cancels.
            stdin.push_line(cancel_line)
            stdin.push_line(cancel_line)
            stdin.push_line(cancel_line)
            time.sleep(0.1)
            stdin.push_eof()

        t = threading.Thread(target=driver, daemon=True)
        t.start()
        try:
            server.run()
        finally:
            t.join(timeout=5.0)

        # All three cancels reach the dispatcher; only the first does
        # real work. The other two log and return None as notifications,
        # writing nothing to stdout.
        assert agent.observed_cancellation is True

        lines = [ln for ln in server._out.getvalue().splitlines() if ln.strip()]
        parsed = [json.loads(ln) for ln in lines]
        # Only one response — the prompt's. Cancel notifications produce
        # no response lines.
        responses = [p for p in parsed if "result" in p or "error" in p]
        assert len(responses) == 1
        assert responses[0]["id"] == 2
        assert responses[0]["result"]["stopReason"] == "cancelled"

    def test_cancel_for_session_with_no_active_turn_is_silent(self, tmp_path):
        """Cancel a session that's idle. No prompt is in flight; the
        cancel must be a quiet no-op and run() must exit cleanly."""
        server, sid, agent = self._build_server(tmp_path)

        cancel_line = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": METHOD_SESSION_CANCEL,
                "params": {"sessionId": sid},
            }
        )

        # No prompt — just a cancel followed by EOF.
        stdin = BlockingStdin()
        server._in = stdin  # type: ignore[assignment]
        server._out = io.StringIO()
        stdin.push_line(cancel_line)
        stdin.push_eof()

        server.run()

        # Notification → no response and no crash.
        assert server._out.getvalue() == ""
        assert agent.chat_calls == 0


# ===========================================================================
# Cancellation reaches the active turn even when issued before chat enters
# ===========================================================================


class TestCancelTimingRaces:
    def test_token_cancelled_post_handler_short_circuit(self, initialized_server):
        """If the cancel runs after session_prompt set up the token but
        before chat() polls it, the very first poll catches the
        cancellation. This is the basic guarantee that ``CancellationToken``
        provides — verify it via direct token interaction so any future
        refactor of the token type would break this test loudly."""
        state = _register_session(initialized_server, with_token=True)
        token = state.cancel_token

        acp_session_cancel.handle_session_cancel(
            initialized_server, {"sessionId": state.session_id}
        )

        # Imagine chat() running its first iteration just now.
        with pytest.raises(Exception) as exc_info:
            token.check()
        # CancellationToken raises AgentCancelledError on check().
        assert "AgentCancelled" in type(exc_info.value).__name__
