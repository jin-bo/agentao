"""Wire-level dataclasses for JSON-RPC 2.0 messages used by the ACP server.

These are deliberately minimal — the goal is a typed representation of the
``jsonrpc``, ``id``, ``method``, ``params``, ``result``, ``error`` fields so
``server.py`` can dispatch without sprinkling ``dict.get`` calls everywhere.

``AcpSessionState`` is a forward-declared stub; Issue 3 fills in the real
fields (agent runtime, cwd, client capabilities, cancellation token).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from agentao.cancellation import CancellationToken

if TYPE_CHECKING:
    # Only for type hints — we never import Agentao at runtime from here,
    # because pulling the LLM stack into ``agentao.acp.models`` would make
    # the ACP package unusable without OpenAI credentials configured.
    from agentao.agent import Agentao

logger = logging.getLogger(__name__)

# JSON-RPC `id` may be a string, number, or null. We accept int | str | None.
JsonRpcId = Union[int, str, None]
JsonRpcParams = Union[dict, list, None]


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

@dataclass
class JsonRpcRequest:
    """A parsed JSON-RPC 2.0 request (or notification, if ``id is None``)."""

    method: str
    params: JsonRpcParams = None
    id: JsonRpcId = None
    jsonrpc: str = "2.0"

    @classmethod
    def from_dict(cls, raw: dict) -> "JsonRpcRequest":
        """Build from a parsed JSON object.

        Does **not** validate wire correctness — that's the server's job so it
        can emit a proper ``INVALID_REQUEST`` error response. This constructor
        only extracts fields.
        """
        return cls(
            method=raw.get("method", ""),
            params=raw.get("params"),
            id=raw.get("id"),
            jsonrpc=raw.get("jsonrpc", ""),
        )

    def is_notification(self) -> bool:
        """Per JSON-RPC 2.0 §4.1, a request without ``id`` is a notification.

        Notifications MUST NOT receive a response, even on error.
        """
        return self.id is None


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------

@dataclass
class JsonRpcError:
    code: int
    message: str
    data: Optional[Any] = None

    def to_dict(self) -> dict:
        out: dict = {"code": self.code, "message": self.message}
        if self.data is not None:
            out["data"] = self.data
        return out


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------

@dataclass
class JsonRpcResponse:
    """A JSON-RPC 2.0 response.

    Exactly one of ``result`` or ``error`` must be populated. ``to_dict``
    serializes only the populated field, matching the spec.
    """

    id: JsonRpcId
    result: Any = None
    error: Optional[JsonRpcError] = None
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict:
        out: dict = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            out["error"] = self.error.to_dict()
        else:
            # result may legitimately be ``None``; include it explicitly.
            out["result"] = self.result
        return out


# ---------------------------------------------------------------------------
# Connection-scoped state (populated by the ``initialize`` handshake)
# ---------------------------------------------------------------------------

@dataclass
class AcpConnectionState:
    """Per-connection state set by the ACP ``initialize`` handshake.

    ACP's ``initialize`` method is connection-scoped (one handshake per stdio
    connection), so this state lives on :class:`AcpServer`, not on any
    particular session. Session-scoped state comes later in Issue 3 via
    :class:`AcpSessionState`.

    Fields:
      - ``initialized``: True after a successful ``initialize`` handshake.
      - ``protocol_version``: the version negotiated with the client (either
        the client's version if we support it, or our own latest version).
      - ``client_capabilities``: raw capabilities dict from the client, kept
        verbatim so later session code can inspect ``fs``/``terminal`` flags
        to decide whether to proxy file operations back to the client.
      - ``client_info``: optional name/title/version the client sent about
        itself. Purely informational; safe to log.
    """

    initialized: bool = False
    protocol_version: Optional[int] = None
    client_capabilities: Dict[str, Any] = field(default_factory=dict)
    client_info: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

@dataclass
class AcpSessionState:
    """Per-ACP-session runtime state.

    Each ACP session owns an independent :class:`~agentao.agent.Agentao`
    runtime, a working directory (Issue 05 makes this actually per-session),
    a snapshot of the client capabilities from the connection's
    ``initialize`` handshake, and the cancellation token for the currently
    active turn (if any). :class:`AcpSessionManager` holds these.

    Issue 03 builds the registry and lifecycle around this dataclass. Issue
    04 populates instances from ``session/new`` requests.

    Fields:
      - ``session_id``: the ACP ``sessionId`` the client will pass in every
        subsequent ``session/*`` request.
      - ``agent``: the :class:`Agentao` runtime bound to this session.
        Optional because a session may exist briefly before runtime setup
        (during ``session/new``) or after teardown.
      - ``cwd``: per-session working directory. Issue 05 removes the current
        global ``Path.cwd()`` dependence so this actually takes effect.
      - ``client_capabilities``: a snapshot of the connection-level client
        capabilities (``fs``, ``terminal``) taken at session-creation time.
        Copied so later re-initialization wouldn't retroactively change the
        semantics of an in-flight session.
      - ``cancel_token``: the token for the currently executing turn.
        ``None`` between turns; Issue 06 rotates this per ``session/prompt``
        and Issue 09 calls ``cancel()`` on ``session/cancel``.
      - ``turn_lock``: non-reentrant lock that serializes turn execution on
        this session. ``session/prompt`` (Issue 06) acquires it
        non-blocking; a second concurrent prompt for the same session
        returns an error rather than queuing, because the synchronous
        dispatcher would otherwise deadlock and because unbounded queuing
        is a DoS footgun.
      - ``permission_overrides``: per-session in-memory permission decisions
        keyed by tool name, set when the client answers
        ``session/request_permission`` (Issue 08) with an ``allow_always``
        or ``reject_always`` outcome. ``True`` → auto-allow, ``False`` →
        auto-reject. Checked before each outbound permission request so
        subsequent calls to the same tool short-circuit without a round
        trip. Cleared only on session close — this is explicitly
        session-scoped and does NOT persist across sessions.
      - ``permission_lock``: guards ``permission_overrides``. Taken
        briefly on reads and writes; never held across I/O.
      - ``closed``: set by :meth:`close` to make teardown idempotent.
    """

    session_id: str
    agent: Optional["Agentao"] = None
    cwd: Optional[Path] = None
    client_capabilities: Dict[str, Any] = field(default_factory=dict)
    cancel_token: Optional[CancellationToken] = None
    turn_lock: threading.Lock = field(default_factory=threading.Lock)
    permission_overrides: Dict[str, bool] = field(default_factory=dict)
    permission_lock: threading.Lock = field(default_factory=threading.Lock)
    last_known_models: Optional[List[str]] = None
    closed: bool = False

    def close(self) -> None:
        """Release resources owned by this session. Idempotent.

        Order of operations matters:

        1. Mark ``closed`` first so a concurrent second call (or a close
           during an error unwind) short-circuits immediately.
        2. Cancel the active turn's token if any — this unblocks the LLM
           call and tool execution before we start disconnecting MCP
           servers, so the runtime doesn't try to use a torn-down
           connection mid-turn.
        3. Call ``agent.close()`` to disconnect MCP servers. Wrapped in
           try/except because shutdown must be robust — a single hung
           MCP server cannot prevent other sessions from tearing down.
        """
        if self.closed:
            return
        self.closed = True

        if self.cancel_token is not None:
            try:
                self.cancel_token.cancel("session-closed")
            except Exception:
                logger.exception("acp: error cancelling token for session %s", self.session_id)

        if self.agent is not None:
            try:
                self.agent.close()
            except Exception:
                logger.exception("acp: error closing agent for session %s", self.session_id)
