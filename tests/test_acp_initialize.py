"""Tests for the ACP ``initialize`` handshake (Issue 02).

These drive :func:`agentao.acp.initialize.handle_initialize` both directly
(unit-style, asserting on the dict return) and through the full ``AcpServer``
dispatcher (integration-style, asserting on the NDJSON wire output). The
wire tests make sure the handler composes correctly with the Issue 01 server.
"""

import io
import json

import pytest

from agentao import __version__ as AGENTAO_VERSION
from agentao.acp import initialize as acp_initialize
from agentao.acp.protocol import (
    ACP_PROTOCOL_VERSION,
    INVALID_PARAMS,
    METHOD_INITIALIZE,
)
from agentao.acp.server import AcpServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_params(protocol_version: int = ACP_PROTOCOL_VERSION) -> dict:
    return {
        "protocolVersion": protocol_version,
        "clientCapabilities": {
            "fs": {"readTextFile": True, "writeTextFile": True},
            "terminal": True,
        },
        "clientInfo": {
            "name": "test-client",
            "title": "Test Client",
            "version": "0.0.1",
        },
    }


def _run_over_wire(request_body: dict) -> dict:
    """Send one request through a real AcpServer + dispatcher and return the parsed response dict."""
    line = json.dumps({"jsonrpc": "2.0", "id": 1, **request_body}) + "\n"
    stdin = io.StringIO(line)
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)
    acp_initialize.register(server)
    server.run()
    return json.loads(stdout.getvalue().strip())


# ---------------------------------------------------------------------------
# Happy path — unit level
# ---------------------------------------------------------------------------

def test_happy_path_returns_full_response_shape():
    stdin = io.StringIO("")
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)

    result = acp_initialize.handle_initialize(server, _minimal_params())

    assert result["protocolVersion"] == ACP_PROTOCOL_VERSION
    assert "agentCapabilities" in result
    assert "authMethods" in result
    assert "agentInfo" in result


def test_agent_capabilities_shape():
    stdin = io.StringIO("")
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)

    caps = acp_initialize.handle_initialize(server, _minimal_params())["agentCapabilities"]

    # loadSession promised for v1 (handler lands in Issue 10).
    assert caps["loadSession"] is True

    # v1 baseline is text only.
    assert caps["promptCapabilities"] == {
        "image": False,
        "audio": False,
        "embeddedContext": False,
    }

    # Reflects actual MCP support in agentao/mcp/client.py.
    assert caps["mcpCapabilities"] == {"http": False, "sse": True}


def test_agent_info_uses_package_version():
    stdin = io.StringIO("")
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)

    info = acp_initialize.handle_initialize(server, _minimal_params())["agentInfo"]

    assert info["name"] == "agentao"
    assert info["title"] == "Agentao"
    assert info["version"] == AGENTAO_VERSION


def test_auth_methods_is_empty_list_by_default():
    stdin = io.StringIO("")
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)

    result = acp_initialize.handle_initialize(server, _minimal_params())

    assert result["authMethods"] == []


# ---------------------------------------------------------------------------
# Version negotiation
# ---------------------------------------------------------------------------

def test_matching_version_is_echoed():
    stdin = io.StringIO("")
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)

    result = acp_initialize.handle_initialize(
        server, _minimal_params(protocol_version=ACP_PROTOCOL_VERSION)
    )

    assert result["protocolVersion"] == ACP_PROTOCOL_VERSION


def test_mismatched_version_returns_agent_latest_not_error():
    """ACP spec §initialize: agent MUST respond with its own latest version
    if the client-advertised one is unsupported — it is NOT an error."""
    stdin = io.StringIO("")
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)

    result = acp_initialize.handle_initialize(
        server, _minimal_params(protocol_version=999)
    )

    # No exception was raised; agent returns its own supported version.
    assert result["protocolVersion"] == ACP_PROTOCOL_VERSION


def test_mismatched_version_over_wire_is_not_an_error_response():
    """End-to-end: a version mismatch should still produce a JSON-RPC success
    result, not a JSON-RPC error. The client decides what to do next."""
    response = _run_over_wire(
        {"method": METHOD_INITIALIZE, "params": _minimal_params(protocol_version=42)}
    )

    assert "error" not in response
    assert response["result"]["protocolVersion"] == ACP_PROTOCOL_VERSION


# ---------------------------------------------------------------------------
# State capture
# ---------------------------------------------------------------------------

def test_state_is_marked_initialized():
    stdin = io.StringIO("")
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)
    assert server.state.initialized is False

    acp_initialize.handle_initialize(server, _minimal_params())

    assert server.state.initialized is True
    assert server.state.protocol_version == ACP_PROTOCOL_VERSION


def test_client_capabilities_are_stored_verbatim():
    stdin = io.StringIO("")
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)
    params = _minimal_params()

    acp_initialize.handle_initialize(server, params)

    assert server.state.client_capabilities == params["clientCapabilities"]
    # Identity not required — just value equality — since Issue 04 may clone.


def test_client_info_is_stored_when_present():
    stdin = io.StringIO("")
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)

    acp_initialize.handle_initialize(server, _minimal_params())

    assert server.state.client_info == {
        "name": "test-client",
        "title": "Test Client",
        "version": "0.0.1",
    }


def test_client_info_is_none_when_omitted():
    stdin = io.StringIO("")
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)
    params = _minimal_params()
    del params["clientInfo"]

    acp_initialize.handle_initialize(server, params)

    assert server.state.client_info is None


# ---------------------------------------------------------------------------
# Parameter validation — bad inputs should surface as INVALID_PARAMS (-32602)
# via the dispatcher's TypeError → INVALID_PARAMS mapping.
# ---------------------------------------------------------------------------

def test_non_dict_params_raise_typeerror():
    stdin = io.StringIO("")
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)

    with pytest.raises(TypeError):
        acp_initialize.handle_initialize(server, ["not", "a", "dict"])


def test_missing_protocol_version_raises_typeerror():
    stdin = io.StringIO("")
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)
    params = _minimal_params()
    del params["protocolVersion"]

    with pytest.raises(TypeError):
        acp_initialize.handle_initialize(server, params)


def test_non_integer_protocol_version_raises_typeerror():
    stdin = io.StringIO("")
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)
    params = _minimal_params()
    params["protocolVersion"] = "1"  # string, not int

    with pytest.raises(TypeError):
        acp_initialize.handle_initialize(server, params)


def test_bool_protocol_version_is_rejected():
    """``bool`` is an ``int`` subclass in Python but must not be accepted."""
    stdin = io.StringIO("")
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)
    params = _minimal_params()
    params["protocolVersion"] = True

    with pytest.raises(TypeError):
        acp_initialize.handle_initialize(server, params)


def test_missing_client_capabilities_raises_typeerror():
    stdin = io.StringIO("")
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)
    params = _minimal_params()
    del params["clientCapabilities"]

    with pytest.raises(TypeError):
        acp_initialize.handle_initialize(server, params)


def test_empty_client_capabilities_object_is_accepted():
    """Sub-fields of clientCapabilities are all optional per spec."""
    stdin = io.StringIO("")
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)
    params = _minimal_params()
    params["clientCapabilities"] = {}

    result = acp_initialize.handle_initialize(server, params)

    assert result["protocolVersion"] == ACP_PROTOCOL_VERSION
    assert server.state.client_capabilities == {}


def test_non_dict_client_info_raises_typeerror():
    stdin = io.StringIO("")
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)
    params = _minimal_params()
    params["clientInfo"] = "not-a-dict"

    with pytest.raises(TypeError):
        acp_initialize.handle_initialize(server, params)


# ---------------------------------------------------------------------------
# End-to-end — dispatcher integration
# ---------------------------------------------------------------------------

def test_bad_params_map_to_invalid_params_over_wire():
    """TypeError from handler → -32602 INVALID_PARAMS in JSON-RPC response."""
    response = _run_over_wire(
        {"method": METHOD_INITIALIZE, "params": {"protocolVersion": "nope"}}
    )

    assert "error" in response
    assert response["error"]["code"] == INVALID_PARAMS


def test_initialize_over_wire_returns_expected_response_shape():
    response = _run_over_wire(
        {"method": METHOD_INITIALIZE, "params": _minimal_params()}
    )

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    result = response["result"]
    assert result["protocolVersion"] == ACP_PROTOCOL_VERSION
    assert result["agentCapabilities"]["loadSession"] is True
    assert result["agentInfo"]["name"] == "agentao"
    assert result["authMethods"] == []
