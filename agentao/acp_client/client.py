"""JSON-RPC 2.0 client for communicating with a local ACP server over stdio.

The client layers on top of :class:`~agentao.acp_client.process.ACPProcessHandle`
and provides:

- NDJSON framing (one JSON object per line)
- Auto-incrementing request IDs
- Pending-request registry with :class:`threading.Event` wake-up
- Background stdout reader thread
- High-level ``initialize`` / ``create_session`` helpers

Issues 03–04 scope: handshake, ``session/prompt``, and ``session/cancel``.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .models import ServerState
from .process import ACPProcessHandle

logger = logging.getLogger("agentao.acp_client")

# Re-use the protocol version constant from the ACP server package so both
# sides agree on the same number.
ACP_PROTOCOL_VERSION = 1

# Default timeout for RPC calls (seconds).
_DEFAULT_TIMEOUT = 30.0


class AcpClientError(Exception):
    """Base error for ACP client operations."""


class AcpRpcError(AcpClientError):
    """The server returned a JSON-RPC error response."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.rpc_message = message
        self.data = data
        super().__init__(f"JSON-RPC error {code}: {message}")


# ---------------------------------------------------------------------------
# Pending request slot
# ---------------------------------------------------------------------------


@dataclass
class _PendingRequest:
    """A slot waiting for a JSON-RPC response."""

    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Connection state (populated by initialize)
# ---------------------------------------------------------------------------


@dataclass
class AcpConnectionInfo:
    """Information gathered from the ``initialize`` handshake."""

    protocol_version: Optional[int] = None
    agent_capabilities: Dict[str, Any] = field(default_factory=dict)
    agent_info: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None


# ---------------------------------------------------------------------------
# ACPClient
# ---------------------------------------------------------------------------


class ACPClient:
    """JSON-RPC 2.0 client bound to one :class:`ACPProcessHandle`.

    After construction, call :meth:`start_reader` to begin consuming the
    server's stdout, then :meth:`initialize` and :meth:`create_session` to
    complete the ACP handshake.

    Thread safety: public methods are safe to call from any thread.  The
    reader thread is the only writer of ``_pending`` result/error fields;
    callers only read after the corresponding event is set.
    """

    def __init__(
        self,
        handle: ACPProcessHandle,
        *,
        notification_callback: Optional[Callable[[str, Any], None]] = None,
        server_request_callback: Optional[Callable[[str, Any, Any], None]] = None,
    ) -> None:
        self._handle = handle
        self._notification_callback = notification_callback
        # Called for server-initiated requests (method + id). The callback
        # receives (method, params, request_id) so the manager can track
        # the request id and later send a response via send_response().
        self._server_request_callback = server_request_callback

        self._next_id = 0
        self._id_lock = threading.Lock()

        self._pending: Dict[int, _PendingRequest] = {}
        self._pending_lock = threading.Lock()

        self._reader_thread: Optional[threading.Thread] = None
        self._closed = False

        # Active turn tracking (one prompt at a time per v1 spec).
        self._active_turn_id: Optional[int] = None
        self._active_turn_lock = threading.Lock()

        self.connection_info = AcpConnectionInfo()

    # ------------------------------------------------------------------
    # Request ID management
    # ------------------------------------------------------------------

    def _alloc_id(self) -> int:
        with self._id_lock:
            rid = self._next_id
            self._next_id += 1
            return rid

    # ------------------------------------------------------------------
    # Wire I/O
    # ------------------------------------------------------------------

    def start_reader(self) -> None:
        """Spawn the background thread that reads NDJSON from server stdout."""
        if self._reader_thread is not None:
            return
        self._reader_thread = threading.Thread(
            target=self._read_loop,
            name=f"acp-reader-{self._handle.name}",
            daemon=True,
        )
        self._reader_thread.start()

    def _send(self, obj: dict) -> None:
        """Write one NDJSON line to the server's stdin."""
        stdin = self._handle.stdin
        if stdin is None:
            raise AcpClientError("server stdin is not available")
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        try:
            stdin.write(line.encode("utf-8"))
            stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise AcpClientError(f"failed to write to server: {exc}") from exc

    def _read_loop(self) -> None:
        """Background loop: read NDJSON lines from stdout, route responses."""
        stdout = self._handle.stdout
        if stdout is None:
            return
        try:
            for raw_line in stdout:
                if self._closed:
                    break
                line = (
                    raw_line.decode("utf-8", errors="replace")
                    if isinstance(raw_line, bytes)
                    else raw_line
                )
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(
                        "acp[%s]: non-JSON line on stdout: %s",
                        self._handle.name,
                        line[:200],
                    )
                    continue
                self._route_message(msg)
        except Exception:
            if not self._closed:
                logger.debug(
                    "acp[%s]: reader thread exiting", self._handle.name
                )

    def _route_message(self, msg: dict) -> None:
        """Dispatch a parsed JSON-RPC message."""
        if "method" in msg and "id" not in msg:
            # Notification (no id).
            self._handle_notification(msg)
            return

        if "method" in msg and "id" in msg:
            # Server-initiated request (e.g. session/request_permission,
            # _agentao.cn/ask_user).  Route to the callback so the manager
            # can register a pending interaction and later reply.
            self._handle_server_request(msg)
            return

        # Response (has id, no method).
        msg_id = msg.get("id")
        if msg_id is None:
            return

        # Convert string IDs back to int if we sent them as int.
        if isinstance(msg_id, str) and msg_id.isdigit():
            msg_id = int(msg_id)

        with self._pending_lock:
            slot = self._pending.get(msg_id)

        if slot is None:
            logger.warning(
                "acp[%s]: response for unknown id %s", self._handle.name, msg_id
            )
            return

        if "error" in msg:
            slot.error = msg["error"]
        else:
            slot.result = msg.get("result")
        slot.event.set()

    def _handle_server_request(self, msg: dict) -> None:
        """Process a server-initiated JSON-RPC request (has both method and id)."""
        method = msg.get("method", "")
        params = msg.get("params")
        request_id = msg.get("id")
        logger.debug(
            "acp[%s]: server request %s (id=%s)",
            self._handle.name, method, request_id,
        )
        if self._server_request_callback is not None:
            try:
                self._server_request_callback(method, params, request_id)
            except Exception:
                logger.exception(
                    "acp[%s]: error in server request callback",
                    self._handle.name,
                )
        else:
            logger.warning(
                "acp[%s]: no handler for server request %s (id=%s)",
                self._handle.name, method, request_id,
            )

    def send_response(self, request_id: Any, result: Any) -> None:
        """Send a JSON-RPC response to a server-initiated request.

        Used by the manager to reply to permission/input requests after
        the user responds via ``/acp approve``, ``/acp reject``, or
        ``/acp reply``.
        """
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result,
        }
        self._send(response)

    def send_error_response(
        self, request_id: Any, code: int, message: str
    ) -> None:
        """Send a JSON-RPC error response to a server-initiated request."""
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
        self._send(response)

    def _handle_notification(self, msg: dict) -> None:
        """Process an incoming server notification."""
        method = msg.get("method", "")
        params = msg.get("params")
        logger.debug(
            "acp[%s]: notification %s", self._handle.name, method
        )
        if self._notification_callback is not None:
            try:
                self._notification_callback(method, params)
            except Exception:
                logger.exception(
                    "acp[%s]: error in notification callback", self._handle.name
                )

    # ------------------------------------------------------------------
    # Public RPC methods
    # ------------------------------------------------------------------

    def call(
        self,
        method: str,
        params: Optional[dict] = None,
        *,
        timeout: Optional[float] = None,
    ) -> Any:
        """Send a JSON-RPC request and wait for the response.

        Args:
            method: RPC method name.
            params: Optional params dict.
            timeout: Seconds to wait (default 30).

        Returns:
            The ``result`` field from the response.

        Raises:
            AcpRpcError: If the server returned a JSON-RPC error.
            AcpClientError: On timeout or I/O failure.
        """
        if timeout is None:
            timeout = _DEFAULT_TIMEOUT

        rid = self._alloc_id()
        slot = _PendingRequest()

        with self._pending_lock:
            self._pending[rid] = slot

        request: dict = {
            "jsonrpc": "2.0",
            "id": rid,
            "method": method,
        }
        if params is not None:
            request["params"] = params

        try:
            self._send(request)
        except AcpClientError:
            with self._pending_lock:
                self._pending.pop(rid, None)
            raise

        if not slot.event.wait(timeout=timeout):
            with self._pending_lock:
                self._pending.pop(rid, None)
            raise AcpClientError(
                f"timeout waiting for response to '{method}' (id={rid})"
            )

        with self._pending_lock:
            self._pending.pop(rid, None)

        if slot.error is not None:
            err = slot.error
            raise AcpRpcError(
                code=err.get("code", -1),
                message=err.get("message", "unknown error"),
                data=err.get("data"),
            )

        return slot.result

    def notify(self, method: str, params: Optional[dict] = None) -> None:
        """Send a JSON-RPC notification (no response expected).

        Args:
            method: RPC method name.
            params: Optional params dict.
        """
        notification: dict = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            notification["params"] = params
        self._send(notification)

    # ------------------------------------------------------------------
    # ACP handshake helpers
    # ------------------------------------------------------------------

    def initialize(self, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Perform the ACP ``initialize`` handshake.

        Sends ``initialize`` with protocol version and minimal client
        capabilities, waits for the response, and stores connection info.

        Returns:
            The full ``initialize`` result dict.
        """
        self._handle._set_state(ServerState.INITIALIZING)

        params = {
            "protocolVersion": ACP_PROTOCOL_VERSION,
            "clientCapabilities": {},
        }

        try:
            result = self.call("initialize", params, timeout=timeout)
        except (AcpRpcError, AcpClientError) as exc:
            self._handle._set_state(ServerState.FAILED, str(exc))
            raise

        self.connection_info.protocol_version = result.get("protocolVersion")
        self.connection_info.agent_capabilities = result.get(
            "agentCapabilities", {}
        )
        self.connection_info.agent_info = result.get("agentInfo")

        self._handle.info.touch()
        return result

    def create_session(
        self,
        *,
        cwd: Optional[str] = None,
        mcp_servers: Optional[List[dict]] = None,
        timeout: Optional[float] = None,
    ) -> str:
        """Create a new ACP session via ``session/new``.

        Args:
            cwd: Working directory for the session.  Defaults to the
                server config's ``cwd``.
            mcp_servers: MCP server configs to pass to the agent.

        Returns:
            The ``sessionId`` string.
        """
        params = {
            "cwd": cwd or self._handle.config.cwd,
            "mcpServers": mcp_servers or [],
        }

        try:
            result = self.call("session/new", params, timeout=timeout)
        except (AcpRpcError, AcpClientError) as exc:
            self._handle._set_state(ServerState.FAILED, str(exc))
            raise

        session_id = result.get("sessionId", "")
        self.connection_info.session_id = session_id

        # Handshake complete — server is ready.
        self._handle._set_state(ServerState.READY)
        return session_id

    # ------------------------------------------------------------------
    # Session prompt / cancel (Issue 04)
    # ------------------------------------------------------------------

    def send_prompt(
        self,
        text: str,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Send a user message to the active ACP session.

        Wraps *text* in a ``session/prompt`` request with a single text
        content block.  Transitions the handle state to ``BUSY`` while the
        server is working and back to ``READY`` when the turn completes.

        v1 constraint: only one prompt may be active per server at a time.

        Args:
            text: Plain-text user message.
            timeout: Seconds to wait for the turn to complete.  Defaults to
                the server's ``request_timeout_ms`` converted to seconds,
                or 60 s if that is not set.

        Returns:
            The ``session/prompt`` result dict (contains ``stopReason``).

        Raises:
            AcpClientError: If no session is established, a turn is already
                active, or the server is unreachable.
            AcpRpcError: If the server returns a JSON-RPC error.
        """
        session_id = self.connection_info.session_id
        if not session_id:
            raise AcpClientError("no active session — call create_session() first")

        with self._active_turn_lock:
            if self._active_turn_id is not None:
                raise AcpClientError(
                    "a prompt is already in progress on this server"
                )
            rid = self._alloc_id()
            self._active_turn_id = rid

        if timeout is None:
            timeout = self._handle.config.request_timeout_ms / 1000.0

        self._handle._set_state(ServerState.BUSY)

        params = {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": text}],
        }

        request: dict = {
            "jsonrpc": "2.0",
            "id": rid,
            "method": "session/prompt",
            "params": params,
        }

        slot = _PendingRequest()
        with self._pending_lock:
            self._pending[rid] = slot

        try:
            self._send(request)
        except AcpClientError:
            with self._pending_lock:
                self._pending.pop(rid, None)
            with self._active_turn_lock:
                self._active_turn_id = None
            self._handle._set_state(ServerState.FAILED, "failed to send prompt")
            raise

        if not slot.event.wait(timeout=timeout):
            with self._pending_lock:
                self._pending.pop(rid, None)
            with self._active_turn_lock:
                self._active_turn_id = None
            self._handle._set_state(ServerState.READY)
            raise AcpClientError(
                f"timeout waiting for session/prompt response (id={rid})"
            )

        with self._pending_lock:
            self._pending.pop(rid, None)
        with self._active_turn_lock:
            self._active_turn_id = None

        if slot.error is not None:
            err = slot.error
            self._handle._set_state(ServerState.READY)
            raise AcpRpcError(
                code=err.get("code", -1),
                message=err.get("message", "unknown error"),
                data=err.get("data"),
            )

        self._handle._set_state(ServerState.READY)
        return slot.result

    # ------------------------------------------------------------------
    # Non-blocking prompt API (for inline interaction handling)
    # ------------------------------------------------------------------

    def send_prompt_nonblocking(
        self,
        text: str,
    ) -> tuple:
        """Send ``session/prompt`` but return immediately with ``(rid, slot)``.

        The caller is responsible for polling ``slot.event``, then calling
        :meth:`finish_prompt` to collect the result and clean up state.

        Returns:
            ``(rid, slot)`` — request id and :class:`_PendingRequest`.
        """
        session_id = self.connection_info.session_id
        if not session_id:
            raise AcpClientError("no active session — call create_session() first")

        with self._active_turn_lock:
            if self._active_turn_id is not None:
                raise AcpClientError(
                    "a prompt is already in progress on this server"
                )
            rid = self._alloc_id()
            self._active_turn_id = rid

        self._handle._set_state(ServerState.BUSY)

        params = {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": text}],
        }
        request: dict = {
            "jsonrpc": "2.0",
            "id": rid,
            "method": "session/prompt",
            "params": params,
        }

        slot = _PendingRequest()
        with self._pending_lock:
            self._pending[rid] = slot

        try:
            self._send(request)
        except AcpClientError:
            with self._pending_lock:
                self._pending.pop(rid, None)
            with self._active_turn_lock:
                self._active_turn_id = None
            self._handle._set_state(ServerState.FAILED, "failed to send prompt")
            raise

        return rid, slot

    def finish_prompt(self, rid: int, slot: "_PendingRequest") -> Dict[str, Any]:
        """Collect the result of a non-blocking :meth:`send_prompt_nonblocking`.

        Call after ``slot.event`` is set.  Cleans up internal state and
        raises the same exceptions as :meth:`send_prompt`.
        """
        with self._pending_lock:
            self._pending.pop(rid, None)
        with self._active_turn_lock:
            self._active_turn_id = None

        if slot.error is not None:
            err = slot.error
            self._handle._set_state(ServerState.READY)
            raise AcpRpcError(
                code=err.get("code", -1),
                message=err.get("message", "unknown error"),
                data=err.get("data"),
            )

        self._handle._set_state(ServerState.READY)
        return slot.result

    def cancel_prompt(self, rid: int) -> None:
        """Clean up a non-blocking prompt without collecting its result.

        Used when the user cancels or a timeout occurs.
        """
        with self._pending_lock:
            self._pending.pop(rid, None)
        with self._active_turn_lock:
            self._active_turn_id = None
        self._handle._set_state(ServerState.READY)
        self.cancel_active_turn()

    def cancel_active_turn(self) -> None:
        """Cancel the currently active prompt, if any.

        Sends ``session/cancel`` as a notification.  Idempotent — safe to
        call even when no turn is in progress.
        """
        session_id = self.connection_info.session_id
        if not session_id:
            return

        self.notify("session/cancel", {"sessionId": session_id})

    @property
    def is_busy(self) -> bool:
        """Whether a prompt is currently in flight."""
        with self._active_turn_lock:
            return self._active_turn_id is not None

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Mark the client as closed so the reader thread exits."""
        self._closed = True
        # Wake up any pending requests so callers don't hang.
        with self._pending_lock:
            for slot in self._pending.values():
                slot.error = {
                    "code": -1,
                    "message": "client closed",
                }
                slot.event.set()
            self._pending.clear()
