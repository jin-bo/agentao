"""Shared builders for in-memory :class:`AcpServer` test setup.

Plain functions rather than pytest fixtures — constructing an
``AcpServer`` from ``io.StringIO`` streams has no implicit dependencies,
so tests read more clearly when the call is explicit. Prefer

    server = make_initialized_server()

over a fixture whose body the reader has to scroll up to see. Only
wrap these in ``@pytest.fixture`` when parameterising over ``tmp_path``
or similar pytest-injected values.

The ``client_capabilities`` default mirrors the "minimal client" shape
used by the prompt / load / cancel tests. Multi-session and session_new
tests pass the full ``{"fs": ..., "terminal": True}`` shape explicitly.
"""

from __future__ import annotations

import io
from typing import Any, Dict, Optional

from agentao.acp import initialize as acp_initialize
from agentao.acp.protocol import ACP_PROTOCOL_VERSION
from agentao.acp.server import AcpServer


_DEFAULT_CLIENT_INFO = {"name": "test-client", "version": "0.0.1"}


def make_server() -> AcpServer:
    """Return a fresh server wired to empty in-memory ``stdin``/``stdout``.

    The streams are writable — tests that need to drive input replace
    ``server._in`` or assert on ``server._out.getvalue()``.
    """
    return AcpServer(stdin=io.StringIO(""), stdout=io.StringIO())


def initialize_params(
    *,
    protocol_version: int = ACP_PROTOCOL_VERSION,
    client_capabilities: Optional[Dict[str, Any]] = None,
    client_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build an ``initialize`` params dict with sensible defaults.

    Tests that want to pin a specific capability shape (e.g. the
    multi-session tests that set ``{"fs": ..., "terminal": True}``) pass
    ``client_capabilities=``; tests that only need the handshake to
    succeed can call with no args.
    """
    return {
        "protocolVersion": protocol_version,
        "clientCapabilities": client_capabilities if client_capabilities is not None else {},
        "clientInfo": client_info if client_info is not None else _DEFAULT_CLIENT_INFO,
    }


def make_initialized_server(
    *,
    protocol_version: int = ACP_PROTOCOL_VERSION,
    client_capabilities: Optional[Dict[str, Any]] = None,
    client_info: Optional[Dict[str, Any]] = None,
) -> AcpServer:
    """Return a server that has already completed the ``initialize`` handshake."""
    server = make_server()
    acp_initialize.handle_initialize(
        server,
        initialize_params(
            protocol_version=protocol_version,
            client_capabilities=client_capabilities,
            client_info=client_info,
        ),
    )
    return server
