"""ACP ``initialize`` handshake (Issue 02).

The ACP spec requires clients to call ``initialize`` once before any session
methods. The agent responds with a negotiated protocol version, its
capabilities, optional agent info, and a list of supported auth methods.

Version negotiation (per https://agentclientprotocol.com/protocol/initialization):

    The agent MUST respond with the same protocolVersion the client sent,
    if it supports it. Otherwise, the agent MUST respond with the latest
    version it supports. The client then decides whether to proceed.

Mismatch is therefore **not** an error — both sides exchange version
numbers and the client makes the final call.

Capability advertisement policy:

- ``loadSession``: advertised as True. Actual handler lands in Issue 10;
  until then a ``session/load`` call will correctly return
  ``METHOD_NOT_FOUND`` from the dispatcher.
- ``promptCapabilities``: baseline v1 is text-only — all sub-flags False.
- ``mcpCapabilities``: reflects actual Agentao MCP support. Agentao's MCP
  client imports ``stdio_client`` and ``sse_client`` from the mcp SDK but
  not ``streamable_http_client``, so ``sse: True`` and ``http: False``.
- ``authMethods``: Agentao performs no ACP-level auth in v1, so an empty
  list. Agentao's own API credentials (OPENAI_API_KEY, etc.) are handled
  out of band via environment variables; they are not ACP auth methods.

This module is deliberately thin: it parses request params, updates
``server.state``, and returns the response dict. All JSON-RPC framing is
handled by :class:`~agentao.acp.server.AcpServer`.
"""

from __future__ import annotations

from typing import Any, Dict, TYPE_CHECKING

from agentao import __version__ as AGENTAO_VERSION

from .protocol import ACP_PROTOCOL_VERSION, METHOD_INITIALIZE

if TYPE_CHECKING:
    from .server import AcpServer


# ---------------------------------------------------------------------------
# Capability constants
# ---------------------------------------------------------------------------

#: Agent-side capability block returned in the ``initialize`` response.
#: Kept as a module constant so tests can assert against it and later
#: issues can flip individual flags without hunting through handler code.
AGENT_CAPABILITIES: Dict[str, Any] = {
    # Issue 10 implements the actual session/load handler. Advertising True
    # commits us to landing that issue in the same v1 milestone.
    "loadSession": True,

    # v1 baseline: text only. Image/audio/embedded-context prompts are a
    # later enhancement and will be gated behind explicit issues.
    "promptCapabilities": {
        "image": False,
        "audio": False,
        "embeddedContext": False,
    },

    # MCP transport capabilities the agent can *connect to* when the client
    # passes ``mcpServers`` in ``session/new``. Reflects the actual imports
    # in ``agentao/mcp/client.py``:
    #   - ``sse_client`` → sse: True
    #   - no ``streamable_http_client`` → http: False
    #   - stdio is always supported but is not exposed as an ACP flag
    "mcpCapabilities": {
        "http": False,
        "sse": True,
    },
}

#: Agent identity block. Optional in the ACP response but useful for clients
#: that want to surface "connected to: <agent name> <version>" in a UI.
AGENT_INFO: Dict[str, str] = {
    "name": "agentao",
    "title": "Agentao",
    "version": AGENTAO_VERSION,
}

#: No ACP-level auth in v1. Agentao provider credentials (OPENAI_API_KEY,
#: etc.) are environment-sourced and are not part of the ACP handshake.
AUTH_METHODS: list = []


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handle_initialize(server: "AcpServer", params: Any) -> Dict[str, Any]:
    """Handle an ACP ``initialize`` request.

    Raises :class:`TypeError` for malformed params so the dispatcher maps
    them to JSON-RPC ``-32602`` (INVALID_PARAMS). All other exceptions
    propagate as ``-32603`` (INTERNAL_ERROR).
    """
    if not isinstance(params, dict):
        raise TypeError("initialize params must be a JSON object")

    # --- protocolVersion (required) ---
    client_version = params.get("protocolVersion")
    if not isinstance(client_version, int) or isinstance(client_version, bool):
        # ``bool`` is an int subclass — reject it explicitly.
        raise TypeError("initialize.protocolVersion must be an integer")

    # --- clientCapabilities (required) ---
    client_capabilities = params.get("clientCapabilities")
    if not isinstance(client_capabilities, dict):
        raise TypeError("initialize.clientCapabilities must be a JSON object")

    # --- clientInfo (optional) ---
    client_info = params.get("clientInfo")
    if client_info is not None and not isinstance(client_info, dict):
        raise TypeError("initialize.clientInfo must be a JSON object when present")

    # --- Negotiate protocol version ---
    # Per spec: echo the client's version if we support it; otherwise return
    # our latest supported version. Never error on mismatch.
    if client_version == ACP_PROTOCOL_VERSION:
        negotiated_version = client_version
    else:
        negotiated_version = ACP_PROTOCOL_VERSION

    # --- Store connection state for later session use ---
    server.state.initialized = True
    server.state.protocol_version = negotiated_version
    server.state.client_capabilities = client_capabilities
    server.state.client_info = client_info

    return {
        "protocolVersion": negotiated_version,
        "agentCapabilities": AGENT_CAPABILITIES,
        "authMethods": AUTH_METHODS,
        "agentInfo": AGENT_INFO,
    }


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------

def register(server: "AcpServer") -> None:
    """Register the ``initialize`` handler on an :class:`AcpServer` instance.

    Called from :mod:`agentao.acp.__main__` so ``python -m agentao.acp`` can
    respond to ``initialize`` out of the box.
    """
    server.register(METHOD_INITIALIZE, lambda params: handle_initialize(server, params))
