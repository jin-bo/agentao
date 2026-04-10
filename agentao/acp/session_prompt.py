"""ACP ``session/prompt`` handler (Issue 06).

Parses ACP ``ContentBlock[]`` into a plain-text user message, acquires
the session's per-turn lock, binds a fresh :class:`CancellationToken`,
runs the Agentao chat loop, and returns ``{"stopReason": ...}``.

Scope vs. sibling issues:

- **Issue 07** will replace :meth:`ACPTransport.emit` with real
  ``session/update`` mapping. Until then emit is a debug no-op, so ACP
  clients see only the final ``stopReason`` from this handler, not the
  streamed intermediate text. Tests verify that ``chat()`` was called
  with the expected text, not that output streamed.
- **Issue 08** will implement ``transport.confirm_tool``. Until then,
  any prompt whose LLM decides to call a confirmation-requiring tool
  will crash the turn with ``NotImplementedError``. Out of scope here.
- **Issue 09** will add a ``session/cancel`` handler. The cancellation
  *plumbing* (fresh per-turn token bound to ``session.cancel_token``)
  ships in this issue so Issue 09 is a trivial one-handler addition.

ContentBlock support (v1):

- ``{"type": "text", "text": "..."}`` → appended verbatim
- ``{"type": "resource_link", "uri": "...", "name"?: "...", ...}`` →
  rendered as ``[Resource: {title or name or uri}]({uri})``. This
  preserves the URI in the LLM's view so it can ask follow-up questions
  about the referenced resource; dereferencing is future work because it
  will often require an ACP ``fs/read_text_file`` round-trip.
- Any other block type (``image``, ``audio``, embedded ``resource``,
  unknown types) raises :class:`TypeError`, which the dispatcher maps
  to ``-32602`` ``INVALID_PARAMS``. No silent degradation.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, TYPE_CHECKING

from agentao.cancellation import CancellationToken

from .protocol import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_SESSION_PROMPT,
    SERVER_NOT_INITIALIZED,
)
from .server import JsonRpcHandlerError
from .session_manager import SessionNotFoundError

if TYPE_CHECKING:
    from .server import AcpServer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parameter parsing
# ---------------------------------------------------------------------------

def _parse_session_id(raw: Any) -> str:
    """Validate the ``sessionId`` field.

    Raises :class:`TypeError` so the dispatcher maps to ``-32602``.
    """
    if not isinstance(raw, str) or not raw:
        raise TypeError("session/prompt.sessionId must be a non-empty string")
    return raw


def _parse_prompt(raw: Any) -> str:
    """Validate an ACP ``ContentBlock[]`` and render it to plain text.

    Multiple blocks are joined with blank lines so the LLM reads them as
    paragraph-separated input. Unsupported block types raise
    :class:`TypeError`, which the dispatcher maps to ``-32602``
    ``INVALID_PARAMS``.
    """
    if not isinstance(raw, list):
        raise TypeError(
            "session/prompt.prompt must be a JSON array of ContentBlocks"
        )
    if not raw:
        raise TypeError("session/prompt.prompt must not be empty")

    rendered: List[str] = []
    for i, block in enumerate(raw):
        if not isinstance(block, dict):
            raise TypeError(f"session/prompt.prompt[{i}] must be a JSON object")
        btype = block.get("type")
        if btype == "text":
            text = block.get("text")
            if not isinstance(text, str):
                raise TypeError(
                    f"session/prompt.prompt[{i}].text must be a string"
                )
            rendered.append(text)
        elif btype == "resource_link":
            uri = block.get("uri")
            if not isinstance(uri, str) or not uri:
                raise TypeError(
                    f"session/prompt.prompt[{i}].uri must be a non-empty string"
                )
            # Prefer title, then name, then uri as the display label.
            label = block.get("title") or block.get("name") or uri
            if not isinstance(label, str):
                raise TypeError(
                    f"session/prompt.prompt[{i}].title/name must be a string"
                )
            rendered.append(f"[Resource: {label}]({uri})")
        elif btype in ("image", "audio", "resource"):
            raise TypeError(
                f"session/prompt.prompt[{i}]: block type {btype!r} is not yet "
                f"supported (Issue 06 supports only 'text' and 'resource_link')"
            )
        else:
            raise TypeError(
                f"session/prompt.prompt[{i}]: unknown block type {btype!r}"
            )

    return "\n\n".join(rendered)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handle_session_prompt(server: "AcpServer", params: Any) -> Dict[str, Any]:
    """Execute a single ACP turn on an existing session.

    Flow:
      1. Guard: ``initialize`` must have been called first.
      2. Parse ``sessionId`` and the ``prompt`` content blocks.
      3. Look up the session (missing id → ``INVALID_REQUEST``).
      4. Refuse if the session is closed or the agent is missing.
      5. Non-blocking acquire of ``session.turn_lock``; already held →
         ``INVALID_REQUEST`` (the session is busy with another turn).
      6. Create a fresh :class:`CancellationToken`, bind it to
         ``session.cancel_token`` so Issue 09 can find it.
      7. Invoke ``agent.chat(user_text, cancellation_token=token)``.
      8. Map the outcome to ``stopReason`` and return
         ``{"stopReason": ...}``. ``cancel_token`` is cleared and
         ``turn_lock`` is released in the ``finally`` block regardless of
         outcome, so a failed turn cannot leave the session stuck.
    """
    if not server.state.initialized:
        raise JsonRpcHandlerError(
            code=SERVER_NOT_INITIALIZED,
            message="session/prompt called before initialize handshake",
        )

    if not isinstance(params, dict):
        raise TypeError("session/prompt params must be a JSON object")

    session_id = _parse_session_id(params.get("sessionId"))
    user_text = _parse_prompt(params.get("prompt"))

    try:
        session = server.sessions.require(session_id)
    except SessionNotFoundError:
        raise JsonRpcHandlerError(
            code=INVALID_REQUEST,
            message=f"unknown sessionId: {session_id}",
        )

    if session.closed:
        raise JsonRpcHandlerError(
            code=INVALID_REQUEST,
            message=f"session {session_id} is closed",
        )
    if session.agent is None:
        # Defensive: should not happen if ``session/new`` succeeded.
        raise JsonRpcHandlerError(
            code=INTERNAL_ERROR,
            message=f"session {session_id} has no agent runtime",
        )

    # Non-blocking acquire so a misbehaving client cannot silently queue
    # turns on a busy session. The second concurrent prompt fails fast
    # with a clear error the client can retry or surface.
    if not session.turn_lock.acquire(blocking=False):
        raise JsonRpcHandlerError(
            code=INVALID_REQUEST,
            message=f"session {session_id} already has an active turn",
        )

    token = CancellationToken()
    session.cancel_token = token
    try:
        reply = session.agent.chat(user_text, cancellation_token=token)
        logger.debug(
            "acp: session %s turn finished, reply length=%d",
            session_id,
            len(reply) if isinstance(reply, str) else -1,
        )
        # TODO(Issue 07): surface max_tokens / max_turn_requests / refusal
        # once agent.chat() returns structured termination metadata. For
        # now we can only distinguish "cancelled" (via the token state)
        # from "normal completion".
        stop_reason = "cancelled" if token.is_cancelled else "end_turn"
    finally:
        session.cancel_token = None
        session.turn_lock.release()

    return {"stopReason": stop_reason}


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------

def register(server: "AcpServer") -> None:
    """Register the ``session/prompt`` handler on an :class:`AcpServer`."""
    server.register(
        METHOD_SESSION_PROMPT,
        lambda params: handle_session_prompt(server, params),
    )
