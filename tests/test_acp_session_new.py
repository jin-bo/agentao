"""Tests for the ACP ``session/new`` handler (Issue 04).

Uses a fake agent factory so we don't pull the LLM stack or need
``OPENAI_API_KEY`` at test time. The factory receives the same kwargs the
default factory does, so the test double also validates that the handler
passes the expected arguments.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from agentao.acp import initialize as acp_initialize
from agentao.acp import session_new as acp_session_new
from agentao.acp.models import AcpSessionState
from agentao.acp.protocol import (
    ACP_PROTOCOL_VERSION,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_INITIALIZE,
    METHOD_SESSION_NEW,
    SERVER_NOT_INITIALIZED,
)
from agentao.acp.server import AcpServer
from agentao.acp.session_manager import DuplicateSessionError
from agentao.acp.transport import ACPTransport


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeAgent:
    """Minimal Agentao replacement — only ``close`` is called by the registry."""

    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class ExplodingAgent(FakeAgent):
    """Fake agent whose close() raises — validates cleanup robustness."""

    def close(self) -> None:
        self.close_calls += 1
        raise RuntimeError("simulated MCP teardown failure")


def make_recording_factory():
    """Return ``(factory, calls)`` — the factory records its kwargs for assertions."""
    calls: list[dict] = []

    def factory(**kwargs) -> FakeAgent:
        calls.append(kwargs)
        return FakeAgent()

    return factory, calls


def make_failing_factory(exc: Exception):
    def factory(**kwargs):
        raise exc
    return factory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def server():
    """A fresh AcpServer attached to in-memory streams."""
    stdin = io.StringIO("")
    stdout = io.StringIO()
    return AcpServer(stdin=stdin, stdout=stdout)


@pytest.fixture
def initialized_server(server):
    """A server that has already completed the ``initialize`` handshake."""
    acp_initialize.handle_initialize(
        server,
        {
            "protocolVersion": ACP_PROTOCOL_VERSION,
            "clientCapabilities": {
                "fs": {"readTextFile": True, "writeTextFile": True},
                "terminal": True,
            },
            "clientInfo": {"name": "test-client", "version": "0.0.1"},
        },
    )
    return server


@pytest.fixture
def abs_tmp_dir(tmp_path) -> str:
    """An absolute, existing, writable directory path as a string."""
    return str(tmp_path)


def _minimal_params(cwd: str) -> dict:
    return {"cwd": cwd, "mcpServers": []}


# ---------------------------------------------------------------------------
# Pre-initialize guard
# ---------------------------------------------------------------------------

def test_session_new_before_initialize_raises_server_not_initialized(server, abs_tmp_dir):
    factory, _ = make_recording_factory()

    from agentao.acp.server import JsonRpcHandlerError
    with pytest.raises(JsonRpcHandlerError) as exc_info:
        acp_session_new.handle_session_new(
            server, _minimal_params(abs_tmp_dir), agent_factory=factory
        )

    assert exc_info.value.code == SERVER_NOT_INITIALIZED
    assert "initialize" in exc_info.value.message.lower()


def test_pre_initialize_error_over_wire(server, abs_tmp_dir):
    """End-to-end: JsonRpcHandlerError must map to its specific code on the wire."""
    factory, _ = make_recording_factory()
    acp_session_new.register(server, agent_factory=factory)
    acp_initialize.register(server)

    stdin = io.StringIO(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": METHOD_SESSION_NEW,
                "params": _minimal_params(abs_tmp_dir),
            }
        )
        + "\n"
    )
    server._in = stdin
    stdout = io.StringIO()
    server._out = stdout
    server.run()

    response = json.loads(stdout.getvalue().strip())
    assert "error" in response
    assert response["error"]["code"] == SERVER_NOT_INITIALIZED


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_happy_path_returns_session_id(initialized_server, abs_tmp_dir):
    factory, calls = make_recording_factory()

    result = acp_session_new.handle_session_new(
        initialized_server, _minimal_params(abs_tmp_dir), agent_factory=factory
    )

    assert "sessionId" in result
    assert isinstance(result["sessionId"], str)
    assert result["sessionId"].startswith("sess_")
    assert len(result["sessionId"]) > len("sess_")
    # Factory was called exactly once.
    assert len(calls) == 1


def test_happy_path_registers_session_in_manager(initialized_server, abs_tmp_dir):
    factory, _ = make_recording_factory()

    result = acp_session_new.handle_session_new(
        initialized_server, _minimal_params(abs_tmp_dir), agent_factory=factory
    )

    session_id = result["sessionId"]
    assert session_id in initialized_server.sessions
    state = initialized_server.sessions.require(session_id)
    assert isinstance(state, AcpSessionState)
    assert state.session_id == session_id
    assert state.cwd == Path(abs_tmp_dir)


def test_session_state_has_acp_transport_bound_to_session(initialized_server, abs_tmp_dir):
    factory, calls = make_recording_factory()

    result = acp_session_new.handle_session_new(
        initialized_server, _minimal_params(abs_tmp_dir), agent_factory=factory
    )

    session_id = result["sessionId"]
    transport = calls[0]["transport"]
    assert isinstance(transport, ACPTransport)
    assert transport._session_id == session_id
    assert transport._server is initialized_server


def test_session_state_captures_client_capabilities_snapshot(initialized_server, abs_tmp_dir):
    factory, calls = make_recording_factory()

    acp_session_new.handle_session_new(
        initialized_server, _minimal_params(abs_tmp_dir), agent_factory=factory
    )

    # Factory received the same capabilities the handshake recorded.
    assert calls[0]["client_capabilities"] == initialized_server.state.client_capabilities


def test_capabilities_snapshot_is_independent_of_server_state(initialized_server, abs_tmp_dir):
    """Mutating server.state after session creation must not leak into the session."""
    factory, _ = make_recording_factory()

    result = acp_session_new.handle_session_new(
        initialized_server, _minimal_params(abs_tmp_dir), agent_factory=factory
    )
    session_id = result["sessionId"]

    initialized_server.state.client_capabilities["terminal"] = False
    initialized_server.state.client_capabilities["mutated_later"] = True

    state = initialized_server.sessions.require(session_id)
    assert state.client_capabilities.get("terminal") is True
    assert "mutated_later" not in state.client_capabilities


def test_factory_receives_permission_engine(initialized_server, abs_tmp_dir):
    from agentao.permissions import PermissionEngine

    factory, calls = make_recording_factory()

    acp_session_new.handle_session_new(
        initialized_server, _minimal_params(abs_tmp_dir), agent_factory=factory
    )

    engine = calls[0]["permission_engine"]
    assert isinstance(engine, PermissionEngine)


def test_multiple_sessions_have_unique_ids(initialized_server, abs_tmp_dir):
    factory, _ = make_recording_factory()

    ids = {
        acp_session_new.handle_session_new(
            initialized_server, _minimal_params(abs_tmp_dir), agent_factory=factory
        )["sessionId"]
        for _ in range(10)
    }

    assert len(ids) == 10  # all unique
    assert all(i.startswith("sess_") for i in ids)
    assert len(initialized_server.sessions) == 10


# ---------------------------------------------------------------------------
# cwd validation
# ---------------------------------------------------------------------------

def test_cwd_missing_raises(initialized_server):
    factory, _ = make_recording_factory()
    with pytest.raises(TypeError):
        acp_session_new.handle_session_new(
            initialized_server, {"mcpServers": []}, agent_factory=factory
        )


def test_cwd_non_string_raises(initialized_server):
    factory, _ = make_recording_factory()
    with pytest.raises(TypeError):
        acp_session_new.handle_session_new(
            initialized_server, {"cwd": 42, "mcpServers": []}, agent_factory=factory
        )


def test_cwd_empty_string_raises(initialized_server):
    factory, _ = make_recording_factory()
    with pytest.raises(TypeError):
        acp_session_new.handle_session_new(
            initialized_server, {"cwd": "", "mcpServers": []}, agent_factory=factory
        )


def test_cwd_relative_path_raises(initialized_server):
    factory, _ = make_recording_factory()
    with pytest.raises(TypeError, match="absolute"):
        acp_session_new.handle_session_new(
            initialized_server,
            {"cwd": "relative/path", "mcpServers": []},
            agent_factory=factory,
        )


def test_cwd_nonexistent_raises(initialized_server):
    factory, _ = make_recording_factory()
    with pytest.raises(TypeError, match="does not exist"):
        acp_session_new.handle_session_new(
            initialized_server,
            {"cwd": "/definitely/not/a/real/path/xyz123", "mcpServers": []},
            agent_factory=factory,
        )


def test_cwd_that_is_a_file_raises(initialized_server, tmp_path):
    file_path = tmp_path / "foo.txt"
    file_path.write_text("hi", encoding="utf-8")
    factory, _ = make_recording_factory()
    with pytest.raises(TypeError, match="not a directory"):
        acp_session_new.handle_session_new(
            initialized_server,
            {"cwd": str(file_path), "mcpServers": []},
            agent_factory=factory,
        )


# ---------------------------------------------------------------------------
# mcpServers parsing
# ---------------------------------------------------------------------------

def test_empty_mcp_servers_list_is_accepted(initialized_server, abs_tmp_dir):
    factory, _ = make_recording_factory()
    result = acp_session_new.handle_session_new(
        initialized_server, {"cwd": abs_tmp_dir, "mcpServers": []}, agent_factory=factory
    )
    assert "sessionId" in result


def test_mcp_servers_missing_raises(initialized_server, abs_tmp_dir):
    factory, _ = make_recording_factory()
    with pytest.raises(TypeError, match="mcpServers"):
        acp_session_new.handle_session_new(
            initialized_server, {"cwd": abs_tmp_dir}, agent_factory=factory
        )


def test_mcp_servers_non_list_raises(initialized_server, abs_tmp_dir):
    factory, _ = make_recording_factory()
    with pytest.raises(TypeError, match="array"):
        acp_session_new.handle_session_new(
            initialized_server,
            {"cwd": abs_tmp_dir, "mcpServers": {}},
            agent_factory=factory,
        )


def test_mcp_servers_stdio_entry_accepted(initialized_server, abs_tmp_dir):
    factory, _ = make_recording_factory()
    params = {
        "cwd": abs_tmp_dir,
        "mcpServers": [
            {
                "name": "fs",
                "command": "/usr/local/bin/mcp-fs",
                "args": ["--root", "/tmp"],
                "env": [{"name": "LOG_LEVEL", "value": "info"}],
            }
        ],
    }
    result = acp_session_new.handle_session_new(
        initialized_server, params, agent_factory=factory
    )
    assert "sessionId" in result


def test_mcp_servers_http_entry_rejected(initialized_server, abs_tmp_dir):
    """``type: "http"`` is rejected at parse time.

    The agent advertises ``mcpCapabilities.http: false`` because
    :class:`agentao.mcp.client.McpClient` cannot dispatch http (only stdio
    + sse). Accepting the entry and silently routing it through
    ``sse_client`` would fail at first tool invocation; rejecting at parse
    time surfaces the misconfiguration immediately as ``INVALID_PARAMS``.
    """
    factory, _ = make_recording_factory()
    with pytest.raises(TypeError, match="http"):
        acp_session_new.handle_session_new(
            initialized_server,
            {
                "cwd": abs_tmp_dir,
                "mcpServers": [
                    {
                        "type": "http",
                        "name": "remote",
                        "url": "https://mcp.example.com",
                        "headers": [{"name": "Authorization", "value": "Bearer abc"}],
                    }
                ],
            },
            agent_factory=factory,
        )


def test_mcp_servers_sse_entry_accepted(initialized_server, abs_tmp_dir):
    factory, _ = make_recording_factory()
    params = {
        "cwd": abs_tmp_dir,
        "mcpServers": [
            {
                "type": "sse",
                "name": "streaming",
                "url": "https://mcp.example.com/sse",
                "headers": [],
            }
        ],
    }
    result = acp_session_new.handle_session_new(
        initialized_server, params, agent_factory=factory
    )
    assert "sessionId" in result


def test_mcp_servers_entry_missing_name_raises(initialized_server, abs_tmp_dir):
    factory, _ = make_recording_factory()
    with pytest.raises(TypeError, match="name"):
        acp_session_new.handle_session_new(
            initialized_server,
            {"cwd": abs_tmp_dir, "mcpServers": [{"command": "/bin/true"}]},
            agent_factory=factory,
        )


def test_mcp_servers_stdio_missing_command_raises(initialized_server, abs_tmp_dir):
    factory, _ = make_recording_factory()
    with pytest.raises(TypeError, match="command"):
        acp_session_new.handle_session_new(
            initialized_server,
            {"cwd": abs_tmp_dir, "mcpServers": [{"name": "fs"}]},
            agent_factory=factory,
        )


def test_mcp_servers_unknown_transport_type_raises(initialized_server, abs_tmp_dir):
    factory, _ = make_recording_factory()
    with pytest.raises(TypeError, match="type"):
        acp_session_new.handle_session_new(
            initialized_server,
            {
                "cwd": abs_tmp_dir,
                "mcpServers": [{"type": "websocket", "name": "foo", "url": "wss://..."}],
            },
            agent_factory=factory,
        )


def test_mcp_servers_sse_missing_url_raises(initialized_server, abs_tmp_dir):
    """SSE entries still need a non-empty url field.

    Note: ``type: "http"`` would no longer reach the url check — it is
    rejected at the type-validation step (see
    ``test_mcp_servers_http_entry_rejected``). This test now exercises the
    same shape failure for the supported sse transport.
    """
    factory, _ = make_recording_factory()
    with pytest.raises(TypeError, match="url"):
        acp_session_new.handle_session_new(
            initialized_server,
            {"cwd": abs_tmp_dir, "mcpServers": [{"type": "sse", "name": "remote"}]},
            agent_factory=factory,
        )


def test_mcp_servers_env_not_list_raises(initialized_server, abs_tmp_dir):
    factory, _ = make_recording_factory()
    with pytest.raises(TypeError, match="env"):
        acp_session_new.handle_session_new(
            initialized_server,
            {
                "cwd": abs_tmp_dir,
                "mcpServers": [
                    {
                        "name": "fs",
                        "command": "/bin/true",
                        "env": {"LOG": "info"},  # dict — wrong shape
                    }
                ],
            },
            agent_factory=factory,
        )


def test_mcp_servers_env_item_missing_value_raises(initialized_server, abs_tmp_dir):
    factory, _ = make_recording_factory()
    with pytest.raises(TypeError, match="value"):
        acp_session_new.handle_session_new(
            initialized_server,
            {
                "cwd": abs_tmp_dir,
                "mcpServers": [
                    {
                        "name": "fs",
                        "command": "/bin/true",
                        "env": [{"name": "LOG"}],  # missing value
                    }
                ],
            },
            agent_factory=factory,
        )


def test_mcp_servers_args_not_strings_raises(initialized_server, abs_tmp_dir):
    factory, _ = make_recording_factory()
    with pytest.raises(TypeError, match="args"):
        acp_session_new.handle_session_new(
            initialized_server,
            {
                "cwd": abs_tmp_dir,
                "mcpServers": [
                    {"name": "fs", "command": "/bin/true", "args": [1, 2, 3]}
                ],
            },
            agent_factory=factory,
        )


# ---------------------------------------------------------------------------
# Factory failure — cleanup
# ---------------------------------------------------------------------------

def test_factory_failure_surfaces_and_does_not_register(initialized_server, abs_tmp_dir):
    factory = make_failing_factory(RuntimeError("no LLM available"))

    with pytest.raises(RuntimeError, match="no LLM available"):
        acp_session_new.handle_session_new(
            initialized_server, _minimal_params(abs_tmp_dir), agent_factory=factory
        )

    # No partial session leaked into the registry.
    assert len(initialized_server.sessions) == 0


def test_factory_failure_over_wire_returns_internal_error(initialized_server, abs_tmp_dir):
    """Generic exception from the factory → -32603 INTERNAL_ERROR."""
    factory = make_failing_factory(RuntimeError("boom"))
    acp_session_new.register(initialized_server, agent_factory=factory)

    stdin = io.StringIO(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": METHOD_SESSION_NEW,
                "params": _minimal_params(abs_tmp_dir),
            }
        )
        + "\n"
    )
    stdout = io.StringIO()
    initialized_server._in = stdin
    initialized_server._out = stdout
    initialized_server.run()

    response = json.loads(stdout.getvalue().strip())
    assert "error" in response
    # Handler didn't raise JsonRpcHandlerError or TypeError, so it's mapped
    # to INTERNAL_ERROR.
    assert response["error"]["code"] == INTERNAL_ERROR


# ---------------------------------------------------------------------------
# Registration / dispatcher wiring
# ---------------------------------------------------------------------------

def test_register_wires_handler_into_dispatcher(initialized_server, abs_tmp_dir):
    factory, _ = make_recording_factory()
    acp_session_new.register(initialized_server, agent_factory=factory)

    stdin = io.StringIO(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 99,
                "method": METHOD_SESSION_NEW,
                "params": _minimal_params(abs_tmp_dir),
            }
        )
        + "\n"
    )
    stdout = io.StringIO()
    initialized_server._in = stdin
    initialized_server._out = stdout
    initialized_server.run()

    response = json.loads(stdout.getvalue().strip())
    assert response["id"] == 99
    assert "result" in response
    assert response["result"]["sessionId"].startswith("sess_")


def test_bad_params_over_wire_return_invalid_params(initialized_server):
    factory, _ = make_recording_factory()
    acp_session_new.register(initialized_server, agent_factory=factory)

    stdin = io.StringIO(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": METHOD_SESSION_NEW,
                "params": {"cwd": "relative", "mcpServers": []},
            }
        )
        + "\n"
    )
    stdout = io.StringIO()
    initialized_server._in = stdin
    initialized_server._out = stdout
    initialized_server.run()

    response = json.loads(stdout.getvalue().strip())
    assert response["error"]["code"] == INVALID_PARAMS


# ---------------------------------------------------------------------------
# Session id generation
# ---------------------------------------------------------------------------

def test_generate_session_id_format():
    sid = acp_session_new._generate_session_id()
    assert sid.startswith("sess_")
    assert len(sid) == len("sess_") + 32  # 32 hex chars


def test_generate_session_id_is_unique():
    ids = {acp_session_new._generate_session_id() for _ in range(1000)}
    assert len(ids) == 1000
