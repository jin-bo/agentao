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

Also here: the three stream doubles the end-to-end ACP tests drive
``AcpServer.run`` with — :class:`BlockingStdin`, :class:`CapturingStdout`
and :class:`RecordingServer`.
"""

from __future__ import annotations

import io
import json
import queue
import threading
from typing import Any, Dict, List, Optional, Tuple

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


class BlockingStdin:
    """File-like stdin that blocks on ``readline`` until a line is pushed.

    ``StringIO`` cannot drive the end-to-end tests: its ``readline``
    returns ``''`` the moment EOF is reached, so ``AcpServer.run`` bails
    out and cancels every pending outbound request before an injector
    thread has a chance to route a response — and it races the worker
    pool on the cancel tests. A queue-backed stdin lets the test decide
    exactly when EOF lands: push the response lines, then ``push_eof()``.
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


class CapturingStdout:
    """Stdout double that lets a driver thread poll for completed lines."""

    def __init__(self) -> None:
        self._buf = io.StringIO()
        self._lock = threading.Lock()

    def write(self, data: str) -> int:
        with self._lock:
            return self._buf.write(data)

    def flush(self) -> None:
        with self._lock:
            self._buf.flush()

    def getvalue(self) -> str:
        with self._lock:
            return self._buf.getvalue()


class RecordingServer:
    """Stand-in for :class:`AcpServer` that captures notifications.

    Used by the ``ACPTransport`` unit tests so notifications can be
    inspected without spinning up a real server. Params are round-tripped
    through JSON on the way in, so a payload carrying a non-serializable
    value fails loudly here rather than at the wire.

    Note ``test_acp_request_permission`` defines its own, unrelated class
    of the same name — that one records ``call()`` and returns a
    ``_PendingRequest``. Different surface, deliberately not merged.
    """

    def __init__(self) -> None:
        self.notifications: List[Tuple[str, Dict[str, Any]]] = []

    def write_notification(self, method: str, params: Dict[str, Any]) -> None:
        encoded = json.dumps(params, separators=(",", ":"))
        self.notifications.append((method, json.loads(encoded)))
