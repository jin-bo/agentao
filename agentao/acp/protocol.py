"""ACP protocol constants.

Centralizes the Agent Client Protocol version we speak, the JSON-RPC 2.0
standard error codes, and the method-name string constants used across the
``agentao.acp`` subpackage. Keeping these here prevents magic numbers and
stringly-typed method names from leaking into dispatcher code.

Later issues (initialize handshake, session/new, etc.) will reference these
constants rather than reintroducing their own.
"""

# ---------------------------------------------------------------------------
# Protocol version
# ---------------------------------------------------------------------------

#: ACP protocol version Agentao implements. Issue 02 will validate the
#: client-provided version against this constant in the ``initialize`` handler.
ACP_PROTOCOL_VERSION = 1


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 standard error codes (https://www.jsonrpc.org/specification)
# ---------------------------------------------------------------------------

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# ---------------------------------------------------------------------------
# Server error codes (implementation-defined, -32000..-32099 per spec)
# ---------------------------------------------------------------------------

#: Returned when a session-level method (``session/new``, ``session/prompt``,
#: etc.) is called before the client has completed the ``initialize``
#: handshake. LSP uses the same ``-32002`` code for the same meaning; we
#: mirror that convention so existing JSON-RPC tooling interprets it correctly.
SERVER_NOT_INITIALIZED = -32002


# ---------------------------------------------------------------------------
# ACP method names
# ---------------------------------------------------------------------------

# Client → server requests
METHOD_INITIALIZE = "initialize"
METHOD_SESSION_NEW = "session/new"
METHOD_SESSION_PROMPT = "session/prompt"
METHOD_SESSION_CANCEL = "session/cancel"
METHOD_SESSION_LOAD = "session/load"

# Server → client notifications
METHOD_SESSION_UPDATE = "session/update"
METHOD_REQUEST_PERMISSION = "session/request_permission"

# Agentao extension methods (private, prefixed with underscore per ACP spec)
METHOD_ASK_USER = "_agentao.cn/ask_user"

# Sentinel returned when the user is unavailable for ask_user.
ASK_USER_UNAVAILABLE_SENTINEL = "(user unavailable)"
