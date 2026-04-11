"""Tests for the ACP ``session/prompt`` handler (Issue 06).

Uses a ``FakeAgent`` test double so the LLM stack is not loaded and no
``OPENAI_API_KEY`` is required. Sessions are created end-to-end through
the real ``session/new`` handler with an injected fake agent factory,
which also exercises the factory DI path from Issue 04.
"""

from __future__ import annotations

import io
import json
import threading
from typing import Any, Callable, List, Optional, Tuple

import pytest

from agentao.acp import initialize as acp_initialize
from agentao.acp import session_new as acp_session_new
from agentao.acp import session_prompt as acp_session_prompt
from agentao.acp.models import AcpSessionState
from agentao.acp.protocol import (
    ACP_PROTOCOL_VERSION,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_INITIALIZE,
    METHOD_SESSION_NEW,
    METHOD_SESSION_PROMPT,
    SERVER_NOT_INITIALIZED,
)
from agentao.acp.server import AcpServer, JsonRpcHandlerError
from agentao.acp.transport import ACPTransport
from agentao.cancellation import CancellationToken
from agentao.transport.events import AgentEvent, EventType


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeAgent:
    """Minimal Agentao replacement.

    Captures each ``chat()`` invocation as a ``(text, token)`` tuple so
    tests can assert the handler passed the right payload. ``side_effect``
    is an optional callable that runs before the configured reply is
    returned — used for cancellation tests (to fire the token) and
    concurrency tests (to block until a barrier is released).
    """

    def __init__(
        self,
        reply: str = "ok",
        side_effect: Optional[Callable[[CancellationToken], None]] = None,
    ) -> None:
        self.reply = reply
        self.side_effect = side_effect
        self.chat_calls: List[Tuple[str, CancellationToken]] = []
        self.close_calls = 0

    def chat(
        self,
        user_message: str,
        max_iterations: int = 100,
        cancellation_token: Optional[CancellationToken] = None,
    ) -> str:
        # Defensive — handler must always pass a token.
        assert cancellation_token is not None, "handler must supply a token"
        self.chat_calls.append((user_message, cancellation_token))
        if self.side_effect is not None:
            self.side_effect(cancellation_token)
        return self.reply

    def close(self) -> None:
        self.close_calls += 1


def make_factory(agent: FakeAgent) -> Callable[..., FakeAgent]:
    """Return an agent factory that always yields the given FakeAgent."""

    def factory(**kwargs: Any) -> FakeAgent:
        return agent

    return factory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def server():
    stdin = io.StringIO("")
    stdout = io.StringIO()
    return AcpServer(stdin=stdin, stdout=stdout)


@pytest.fixture
def initialized_server(server):
    acp_initialize.handle_initialize(
        server,
        {
            "protocolVersion": ACP_PROTOCOL_VERSION,
            "clientCapabilities": {},
            "clientInfo": {"name": "test-client", "version": "0.0.1"},
        },
    )
    return server


@pytest.fixture
def session_with_agent(initialized_server, tmp_path):
    """Create an ACP session bound to a FakeAgent.

    Yields ``(server, session_id, fake_agent)``.
    """
    fake = FakeAgent(reply="assistant answer")
    acp_session_new.handle_session_new(
        initialized_server,
        {"cwd": str(tmp_path), "mcpServers": []},
        agent_factory=make_factory(fake),
    )
    # We just created exactly one session; grab its id directly.
    session_ids = initialized_server.sessions.session_ids()
    assert len(session_ids) == 1
    return initialized_server, session_ids[0], fake


def _prompt_params(session_id: str, text: str = "hello") -> dict:
    return {
        "sessionId": session_id,
        "prompt": [{"type": "text", "text": text}],
    }


# ---------------------------------------------------------------------------
# Pre-initialize guard
# ---------------------------------------------------------------------------

def test_session_prompt_before_initialize_raises_server_not_initialized(server):
    with pytest.raises(JsonRpcHandlerError) as exc_info:
        acp_session_prompt.handle_session_prompt(
            server, _prompt_params("sess_nothing")
        )
    assert exc_info.value.code == SERVER_NOT_INITIALIZED
    assert "initialize" in exc_info.value.message.lower()


# ---------------------------------------------------------------------------
# Params shape validation
# ---------------------------------------------------------------------------

def test_params_must_be_dict(initialized_server):
    with pytest.raises(TypeError, match="JSON object"):
        acp_session_prompt.handle_session_prompt(initialized_server, [])


def test_missing_session_id_raises_type_error(initialized_server):
    with pytest.raises(TypeError, match="sessionId must be a non-empty string"):
        acp_session_prompt.handle_session_prompt(
            initialized_server, {"prompt": [{"type": "text", "text": "hi"}]}
        )


def test_empty_session_id_raises_type_error(initialized_server):
    with pytest.raises(TypeError, match="sessionId must be a non-empty string"):
        acp_session_prompt.handle_session_prompt(
            initialized_server,
            {"sessionId": "", "prompt": [{"type": "text", "text": "hi"}]},
        )


def test_missing_prompt_raises_type_error(initialized_server):
    with pytest.raises(TypeError, match="prompt must be a JSON array"):
        acp_session_prompt.handle_session_prompt(
            initialized_server, {"sessionId": "sess_x"}
        )


def test_prompt_not_list_raises_type_error(initialized_server):
    with pytest.raises(TypeError, match="prompt must be a JSON array"):
        acp_session_prompt.handle_session_prompt(
            initialized_server, {"sessionId": "sess_x", "prompt": "hello"}
        )


def test_prompt_empty_list_raises_type_error(initialized_server):
    with pytest.raises(TypeError, match="prompt must not be empty"):
        acp_session_prompt.handle_session_prompt(
            initialized_server, {"sessionId": "sess_x", "prompt": []}
        )


# ---------------------------------------------------------------------------
# Session lookup
# ---------------------------------------------------------------------------

def test_unknown_session_id_raises_invalid_request(initialized_server):
    with pytest.raises(JsonRpcHandlerError) as exc_info:
        acp_session_prompt.handle_session_prompt(
            initialized_server, _prompt_params("sess_nonexistent")
        )
    assert exc_info.value.code == INVALID_REQUEST
    assert "unknown sessionId" in exc_info.value.message


def test_closed_session_raises_invalid_request(session_with_agent):
    server, sid, _ = session_with_agent
    # Directly mark the session closed (mimicking a mid-teardown state).
    state = server.sessions.require(sid)
    state.closed = True
    with pytest.raises(JsonRpcHandlerError) as exc_info:
        acp_session_prompt.handle_session_prompt(server, _prompt_params(sid))
    assert exc_info.value.code == INVALID_REQUEST
    assert "closed" in exc_info.value.message


def test_session_without_agent_raises_internal_error(initialized_server):
    """Defensive guard: agent=None shouldn't happen but must not crash silently."""
    state = AcpSessionState(session_id="sess_noagent", agent=None)
    initialized_server.sessions.create(state)
    with pytest.raises(JsonRpcHandlerError) as exc_info:
        acp_session_prompt.handle_session_prompt(
            initialized_server, _prompt_params("sess_noagent")
        )
    assert exc_info.value.code == INTERNAL_ERROR


# ---------------------------------------------------------------------------
# ContentBlock parsing — happy paths
# ---------------------------------------------------------------------------

def test_single_text_block_passes_text_to_chat(session_with_agent):
    server, sid, fake = session_with_agent
    result = acp_session_prompt.handle_session_prompt(
        server, _prompt_params(sid, text="hello world")
    )
    assert result == {"stopReason": "end_turn"}
    assert len(fake.chat_calls) == 1
    text, token = fake.chat_calls[0]
    assert text == "hello world"
    assert isinstance(token, CancellationToken)


def test_multiple_text_blocks_joined_with_blank_lines(session_with_agent):
    server, sid, fake = session_with_agent
    params = {
        "sessionId": sid,
        "prompt": [
            {"type": "text", "text": "a"},
            {"type": "text", "text": "b"},
            {"type": "text", "text": "c"},
        ],
    }
    acp_session_prompt.handle_session_prompt(server, params)
    assert fake.chat_calls[0][0] == "a\n\nb\n\nc"


def test_resource_link_rendered_with_title(session_with_agent):
    server, sid, fake = session_with_agent
    params = {
        "sessionId": sid,
        "prompt": [
            {"type": "text", "text": "look at this"},
            {
                "type": "resource_link",
                "uri": "file:///tmp/readme.md",
                "name": "readme.md",
                "title": "Project README",
            },
        ],
    }
    acp_session_prompt.handle_session_prompt(server, params)
    assert fake.chat_calls[0][0] == (
        "look at this\n\n[Resource: Project README](file:///tmp/readme.md)"
    )


def test_resource_link_falls_back_to_name_then_uri(session_with_agent):
    server, sid, fake = session_with_agent
    # No title → uses name.
    params_name = {
        "sessionId": sid,
        "prompt": [{"type": "resource_link", "uri": "file:///a", "name": "A"}],
    }
    acp_session_prompt.handle_session_prompt(server, params_name)
    assert fake.chat_calls[-1][0] == "[Resource: A](file:///a)"

    # No title, no name → uses uri itself as label.
    params_uri = {
        "sessionId": sid,
        "prompt": [{"type": "resource_link", "uri": "file:///b"}],
    }
    acp_session_prompt.handle_session_prompt(server, params_uri)
    assert fake.chat_calls[-1][0] == "[Resource: file:///b](file:///b)"


# ---------------------------------------------------------------------------
# ContentBlock parsing — rejection
# ---------------------------------------------------------------------------

def test_non_object_block_raises_type_error(initialized_server):
    with pytest.raises(TypeError, match="prompt\\[0\\] must be a JSON object"):
        acp_session_prompt.handle_session_prompt(
            initialized_server,
            {"sessionId": "sess_x", "prompt": ["not an object"]},
        )


def test_text_block_missing_text_raises(initialized_server):
    with pytest.raises(TypeError, match="\\.text must be a string"):
        acp_session_prompt.handle_session_prompt(
            initialized_server,
            {"sessionId": "sess_x", "prompt": [{"type": "text"}]},
        )


def test_resource_link_missing_uri_raises(initialized_server):
    with pytest.raises(TypeError, match="uri must be a non-empty string"):
        acp_session_prompt.handle_session_prompt(
            initialized_server,
            {
                "sessionId": "sess_x",
                "prompt": [{"type": "resource_link", "name": "x"}],
            },
        )


def test_image_block_rejected_explicitly(initialized_server):
    with pytest.raises(TypeError, match="not yet supported"):
        acp_session_prompt.handle_session_prompt(
            initialized_server,
            {
                "sessionId": "sess_x",
                "prompt": [{"type": "image", "data": "base64..."}],
            },
        )


def test_audio_block_rejected_explicitly(initialized_server):
    with pytest.raises(TypeError, match="not yet supported"):
        acp_session_prompt.handle_session_prompt(
            initialized_server,
            {
                "sessionId": "sess_x",
                "prompt": [{"type": "audio", "data": "base64..."}],
            },
        )


def test_embedded_resource_rejected_explicitly(initialized_server):
    with pytest.raises(TypeError, match="not yet supported"):
        acp_session_prompt.handle_session_prompt(
            initialized_server,
            {
                "sessionId": "sess_x",
                "prompt": [{"type": "resource", "resource": {}}],
            },
        )


def test_unknown_block_type_rejected(initialized_server):
    with pytest.raises(TypeError, match="unknown block type"):
        acp_session_prompt.handle_session_prompt(
            initialized_server,
            {
                "sessionId": "sess_x",
                "prompt": [{"type": "mystery"}],
            },
        )


# ---------------------------------------------------------------------------
# Cancellation token plumbing
# ---------------------------------------------------------------------------

def test_token_bound_to_session_during_chat(session_with_agent):
    server, sid, fake = session_with_agent
    captured: dict = {}

    def side_effect(token: CancellationToken) -> None:
        state = server.sessions.require(sid)
        captured["bound"] = state.cancel_token
        captured["token_arg"] = token

    fake.side_effect = side_effect
    acp_session_prompt.handle_session_prompt(server, _prompt_params(sid))
    # The token captured inside chat() is the same one the handler bound
    # onto the session — that's what Issue 09 will use to cancel.
    assert captured["bound"] is captured["token_arg"]


def test_token_cleared_after_normal_return(session_with_agent):
    server, sid, fake = session_with_agent
    acp_session_prompt.handle_session_prompt(server, _prompt_params(sid))
    state = server.sessions.require(sid)
    assert state.cancel_token is None


def test_token_cleared_after_chat_raises(session_with_agent):
    server, sid, fake = session_with_agent

    def blow_up(token: CancellationToken) -> None:
        raise RuntimeError("simulated failure")

    fake.side_effect = blow_up
    with pytest.raises(RuntimeError, match="simulated failure"):
        acp_session_prompt.handle_session_prompt(server, _prompt_params(sid))
    state = server.sessions.require(sid)
    assert state.cancel_token is None


# ---------------------------------------------------------------------------
# Serialization / turn_lock
# ---------------------------------------------------------------------------

def test_concurrent_prompt_rejected_with_invalid_request(session_with_agent):
    """While turn_lock is held, a second prompt must fail fast."""
    server, sid, fake = session_with_agent

    barrier = threading.Event()
    release = threading.Event()
    first_result: dict = {}
    second_error: dict = {}

    def slow_chat(token: CancellationToken) -> None:
        barrier.set()      # signal that the first call has entered chat()
        release.wait(5.0)  # block until the main thread fires the second call

    fake.side_effect = slow_chat

    def run_first() -> None:
        first_result["value"] = acp_session_prompt.handle_session_prompt(
            server, _prompt_params(sid)
        )

    t = threading.Thread(target=run_first, daemon=True)
    t.start()

    # Wait for the first call to be inside chat() (turn_lock held).
    assert barrier.wait(5.0), "first chat() never entered"

    try:
        acp_session_prompt.handle_session_prompt(server, _prompt_params(sid))
        second_error["raised"] = False
    except JsonRpcHandlerError as e:
        second_error["raised"] = True
        second_error["code"] = e.code
        second_error["message"] = e.message

    release.set()
    t.join(5.0)

    assert second_error.get("raised") is True
    assert second_error["code"] == INVALID_REQUEST
    assert "active turn" in second_error["message"]
    assert first_result["value"] == {"stopReason": "end_turn"}


def test_lock_released_on_normal_return(session_with_agent):
    """A second sequential prompt after a clean first prompt must succeed."""
    server, sid, fake = session_with_agent
    acp_session_prompt.handle_session_prompt(server, _prompt_params(sid, "first"))
    result = acp_session_prompt.handle_session_prompt(
        server, _prompt_params(sid, "second")
    )
    assert result == {"stopReason": "end_turn"}
    assert [c[0] for c in fake.chat_calls] == ["first", "second"]


def test_lock_released_on_exception(session_with_agent):
    """If chat() raises, the next prompt must still succeed — lock was freed."""
    server, sid, fake = session_with_agent

    def blow_up_once(token: CancellationToken) -> None:
        fake.side_effect = None
        raise RuntimeError("simulated failure")

    fake.side_effect = blow_up_once
    with pytest.raises(RuntimeError, match="simulated failure"):
        acp_session_prompt.handle_session_prompt(server, _prompt_params(sid))

    # Next call must succeed, proving the lock was released.
    result = acp_session_prompt.handle_session_prompt(
        server, _prompt_params(sid, "after failure")
    )
    assert result == {"stopReason": "end_turn"}


# ---------------------------------------------------------------------------
# stopReason mapping
# ---------------------------------------------------------------------------

def test_stop_reason_end_turn_on_normal_return(session_with_agent):
    server, sid, _ = session_with_agent
    result = acp_session_prompt.handle_session_prompt(server, _prompt_params(sid))
    assert result == {"stopReason": "end_turn"}


def test_stop_reason_cancelled_when_token_fired(session_with_agent):
    server, sid, fake = session_with_agent

    def cancel_during_chat(token: CancellationToken) -> None:
        token.cancel("test-cancel")

    fake.side_effect = cancel_during_chat
    result = acp_session_prompt.handle_session_prompt(server, _prompt_params(sid))
    assert result == {"stopReason": "cancelled"}


# ---------------------------------------------------------------------------
# Registration / dispatcher wire
# ---------------------------------------------------------------------------

def test_register_populates_handler_dict(initialized_server):
    acp_session_prompt.register(initialized_server)
    assert METHOD_SESSION_PROMPT in initialized_server._handlers


def test_end_to_end_wire(tmp_path):
    """Full stdin→dispatcher→stdout round-trip for session/prompt."""
    server = AcpServer(stdin=io.StringIO(""), stdout=io.StringIO())
    # Initialize, register, create a session all against this server first.
    acp_initialize.handle_initialize(
        server,
        {
            "protocolVersion": ACP_PROTOCOL_VERSION,
            "clientCapabilities": {},
        },
    )
    fake = FakeAgent(reply="done")
    acp_session_new.handle_session_new(
        server,
        {"cwd": str(tmp_path), "mcpServers": []},
        agent_factory=make_factory(fake),
    )
    sid = server.sessions.session_ids()[0]
    acp_session_prompt.register(server)

    # Dispatch synchronously via _handle_line to avoid the race between
    # the executor worker and the shutdown path that fires cancel tokens
    # when stdin hits EOF inside server.run().
    request = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": METHOD_SESSION_PROMPT,
        "params": {
            "sessionId": sid,
            "prompt": [{"type": "text", "text": "ping"}],
        },
    }
    server._out = io.StringIO()
    server._handle_line(json.dumps(request))

    response = json.loads(server._out.getvalue().strip())
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 7
    assert response["result"] == {"stopReason": "end_turn"}
    # And the agent really was invoked.
    assert fake.chat_calls[0][0] == "ping"


# ---------------------------------------------------------------------------
# Transport.emit() no-op (Issue 06 patch)
# ---------------------------------------------------------------------------

def test_transport_emit_is_no_op():
    """Precondition for session/prompt to actually run: emit must not raise."""
    transport = ACPTransport(server=None, session_id="sess_test")
    # Should not raise NotImplementedError any more — these are the events
    # agent.chat() actually emits.
    transport.emit(AgentEvent(EventType.TURN_START, {}))
    transport.emit(AgentEvent(EventType.LLM_TEXT, {"chunk": "hi"}))
    transport.emit(AgentEvent(EventType.TOOL_START, {"tool": "read_file"}))
