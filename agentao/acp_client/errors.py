"""Exception hierarchy for the ACP client.

Split out of :mod:`agentao.acp_client.client` so the transport machinery and
the error contract live in separate modules. The names are re-exported from
``client`` (and from the package ``__init__``), so existing
``from agentao.acp_client.client import AcpClientError`` / ``from
agentao.acp_client import AcpRpcError`` call sites keep working unchanged.

Embedding callers should branch on :class:`AcpClientError.code` (an
:class:`AcpErrorCode`) rather than pattern-matching message strings.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional


class AcpErrorCode(str, Enum):
    """Client-side classification for ACP failures.

    Embedding callers should branch on ``AcpClientError.code`` rather than
    pattern-matching message strings. ``AcpRpcError`` carries the raw
    JSON-RPC numeric code separately on ``rpc_code``.
    """

    CONFIG_INVALID = "config_invalid"
    SERVER_NOT_FOUND = "server_not_found"
    PROCESS_START_FAIL = "process_start_fail"
    HANDSHAKE_FAIL = "handshake_fail"
    REQUEST_TIMEOUT = "request_timeout"
    TRANSPORT_DISCONNECT = "transport_disconnect"
    INTERACTION_REQUIRED = "interaction_required"
    PROTOCOL_ERROR = "protocol_error"
    SERVER_BUSY = "server_busy"


class AcpClientError(Exception):
    """Base error for ACP client operations.

    Carries a structured :class:`AcpErrorCode` so embedding callers can
    branch on failure category without string matching. Existing
    ``except AcpClientError`` handlers keep working because the class
    hierarchy is unchanged.
    """

    def __init__(
        self,
        message: str,
        *,
        code: AcpErrorCode = AcpErrorCode.PROTOCOL_ERROR,
        details: Optional[Dict[str, Any]] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details: Dict[str, Any] = dict(details) if details else {}
        self.cause = cause


class AcpServerNotFound(AcpClientError, KeyError):
    """Raised when a caller references an unknown ACP server by name.

    Inherits from both :class:`AcpClientError` (so embedders can branch
    on ``err.code == AcpErrorCode.SERVER_NOT_FOUND``) and :class:`KeyError`
    (so pre-existing ``except KeyError`` handlers keep working).
    """

    def __init__(self, name: str) -> None:
        super().__init__(
            f"no ACP server named '{name}'",
            code=AcpErrorCode.SERVER_NOT_FOUND,
            details={"server": name},
        )
        self.server_name = name


class AcpRpcError(AcpClientError):
    """The server returned a JSON-RPC error response.

    Preserves the pre-existing public contract: ``code`` is the raw
    JSON-RPC numeric error code (``int``), so existing call sites that
    branch on ``err.code == -32603`` keep working. The same value is
    also available as ``rpc_code`` for call sites that prefer the
    explicit name.

    The structured :class:`AcpErrorCode` classification for RPC
    failures — always ``AcpErrorCode.PROTOCOL_ERROR`` — is available
    on ``error_code``; generic ``except AcpClientError`` handlers can
    use ``getattr(err, "error_code", err.code)`` or
    ``isinstance(err, AcpRpcError)`` to classify RPC failures without
    string matching.

    Handshake-phase note: the manager classifies handshake /
    session-setup failures asymmetrically across subclasses.

    * **Non-RPC** :class:`AcpClientError` raised during
      ``initialize`` / ``session/new`` has its ``code`` flipped to
      :attr:`AcpErrorCode.HANDSHAKE_FAIL`; the original
      :class:`AcpErrorCode` is preserved in
      ``details["underlying_code"]`` so downstream classification
      (``REQUEST_TIMEOUT`` vs. ``TRANSPORT_DISCONNECT`` vs.
      ``PROTOCOL_ERROR``) stays available.
    * :class:`AcpRpcError` — this class — keeps both ``code`` (int)
      and ``error_code`` (``PROTOCOL_ERROR``) unchanged: its public
      shape is rigid. The server-side rejection is already fully
      described by ``rpc_code`` / ``rpc_message``.

    Both paths stamp ``details["phase"] = "handshake"``, which is
    therefore the canonical cross-subclass detector. See
    Appendix D §D.7 for the full pattern.

    The constructor accepts ``rpc_code`` / ``rpc_message`` as the
    primary keyword arguments, with legacy positional ``code`` /
    ``message`` still supported so older call sites keep working.
    """

    def __init__(
        self,
        rpc_code: Optional[int] = None,
        rpc_message: Optional[str] = None,
        data: Any = None,
        *,
        code: Optional[int] = None,
        message: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        if rpc_code is None:
            rpc_code = code if code is not None else -1
        if rpc_message is None:
            rpc_message = message if message is not None else ""
        super().__init__(
            f"JSON-RPC error {rpc_code}: {rpc_message}",
            code=AcpErrorCode.PROTOCOL_ERROR,
            details=details,
        )
        # Backward-compatible numeric code on ``.code``. Overrides the
        # structured value set by the base class so pre-existing
        # ``err.code == <int>`` branches keep working.
        self.code: int = rpc_code  # type: ignore[assignment]
        self.error_code: AcpErrorCode = AcpErrorCode.PROTOCOL_ERROR
        self.rpc_code: int = rpc_code
        self.rpc_message: str = rpc_message
        self.data = data


class AcpInteractionRequiredError(AcpClientError):
    """Raised for non-interactive turns when the server requests user input.

    The raw server request method is stored under ``details["method"]`` and
    is intentionally not exposed as a public attribute — embedding callers
    must branch on ``code`` only, so the internal method name can change
    without breaking the API.
    """

    def __init__(
        self,
        *,
        server: str,
        method: str,
        prompt: str = "",
        options: Optional[List[Dict[str, Any]]] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        merged: Dict[str, Any] = {
            "server": server,
            "method": method,
            "prompt": prompt,
            "options": list(options) if options else [],
        }
        if details:
            merged.update(details)
        super().__init__(
            f"ACP server {server!r} requires interaction",
            code=AcpErrorCode.INTERACTION_REQUIRED,
            details=merged,
        )
        self.server = server
        self.prompt = prompt
        self.options = list(options) if options else []
