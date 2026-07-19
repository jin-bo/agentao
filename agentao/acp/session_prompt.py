"""ACP ``session/prompt`` handler (Issue 06).

Parses ACP ``ContentBlock[]`` into a plain-text user message, acquires
the session's per-turn lock, binds a fresh :class:`CancellationToken`,
runs the Agentao chat loop, and returns ``{"stopReason": ...}``.

Scope vs. sibling issues:

- **Issue 07** now provides the real ``session/update`` mapping in
  :class:`ACPTransport`, so prompt turns can stream intermediate text,
  thinking, and tool events to the client while this handler is waiting
  for ``agent.chat()`` to finish. The tests in this module still focus
  on prompt parsing, session lookup, locking, and stop-reason handling;
  transport-level streaming shapes are covered separately in
  ``tests/test_acp_transport.py``.
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
- ``{"type": "image", "data": "<base64>", "mimeType": "..."}``
  → collected as an image attachment and forwarded to
  ``agent.chat(images=[...])``, which surfaces it as an OpenAI
  ``image_url`` part. Inline ``data``/``mimeType`` is required and is the
  *only* shape accepted: any other key (``uri``, ``path``, ``apiKey``,
  ``_meta``, …) is rejected (``-32602``), so the wire can never carry a
  host path or secret — the runtime mirror of the schema's
  ``additionalProperties: false``. The untrusted
  payload is validated: ``mimeType`` must be ``image/*``, ``data`` must be
  valid base64 within the per-image size cap, and a prompt may carry at
  most ``_MAX_IMAGES_PER_PROMPT`` images — each violation is a ``-32602``.
- Any other block type (``audio``, embedded ``resource``, unknown types)
  raises :class:`TypeError`, which the dispatcher maps to ``-32602``
  ``INVALID_PARAMS``. No silent degradation.
"""

from __future__ import annotations

import base64
import binascii
import logging
from typing import Any, Dict, List, Tuple, TYPE_CHECKING

from agentao.cancellation import CancellationToken

from .protocol import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_SESSION_PROMPT,
    SERVER_NOT_INITIALIZED,
)
from .server import JsonRpcHandlerError
from .session_manager import SessionNotFoundError

# Bounds on untrusted inline image input from the ACP wire. The wire carries
# only ``{data, mimeType}`` content — never a path or secret — so these guard
# against a malformed or oversized payload reaching the LLM client as an
# opaque API error instead of a clean ``-32602`` ``INVALID_PARAMS``. The byte
# cap and count are shared with the CLI /image command via media_limits.
from agentao.media_limits import (
    MAX_IMAGE_BYTES as _MAX_IMAGE_BYTES,
    MAX_IMAGES_PER_TURN as _MAX_IMAGES_PER_PROMPT,
)

# base64 expands ~4/3; cap the encoded length so we reject oversized payloads
# *before* decoding them (a decode-to-check would itself be the DoS).
_MAX_IMAGE_B64_LEN = ((_MAX_IMAGE_BYTES + 2) // 3) * 4

# The only keys an inline image block may carry. Anything else (``uri``,
# ``path``, ``apiKey``, ``baseUrl``, ``_meta``, …) is rejected so the wire can
# never smuggle a host path or secret — the runtime mirror of the schema's
# ``additionalProperties: false``.
_IMAGE_BLOCK_KEYS = frozenset({"type", "data", "mimeType"})

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


def _parse_prompt(raw: Any) -> Tuple[str, List[Dict[str, str]]]:
    """Validate an ACP ``ContentBlock[]`` and render it.

    Returns ``(user_text, images)`` where ``user_text`` is the text and
    resource-link blocks joined with blank lines (paragraph-separated for
    the LLM), and ``images`` is the list of image attachments in
    ``{"data", "mimeType"}`` form ready for ``agent.chat(images=...)``.
    Unsupported block types raise :class:`TypeError`, which the dispatcher
    maps to ``-32602`` ``INVALID_PARAMS``.
    """
    if not isinstance(raw, list):
        raise TypeError(
            "session/prompt.prompt must be a JSON array of ContentBlocks"
        )
    if not raw:
        raise TypeError("session/prompt.prompt must not be empty")

    rendered: List[str] = []
    images: List[Dict[str, str]] = []
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
        elif btype == "image":
            # The image wire carries only inline content — {type, data,
            # mimeType}. Reject ANY other key at runtime (by-reference
            # ``uri``, ``path``, ``apiKey``, ``baseUrl``, ``_meta``, …),
            # mirroring the schema's extra="forbid"/additionalProperties:
            # false; the hand-rolled raw-dict parser would otherwise
            # silently forward a host path or secret instead of rejecting it.
            extra_keys = set(block) - _IMAGE_BLOCK_KEYS
            if extra_keys:
                raise TypeError(
                    f"session/prompt.prompt[{i}]: image block has unexpected "
                    f"field(s) {sorted(extra_keys)}; send inline "
                    f"'data'/'mimeType' only"
                )
            data = block.get("data")
            mime_type = block.get("mimeType")
            if not isinstance(data, str) or not data:
                raise TypeError(
                    f"session/prompt.prompt[{i}].data must be a non-empty "
                    f"base64 string"
                )
            if not isinstance(mime_type, str) or not mime_type.startswith("image/"):
                raise TypeError(
                    f"session/prompt.prompt[{i}].mimeType must be an "
                    f"'image/*' type"
                )
            # Reject by encoded length first so a gigabyte payload never
            # reaches the decoder (the decode-to-check would itself be the
            # DoS); then enforce the exact cap on the decoded bytes.
            if len(data) > _MAX_IMAGE_B64_LEN:
                raise TypeError(
                    f"session/prompt.prompt[{i}]: image exceeds the "
                    f"{_MAX_IMAGE_BYTES // (1024 * 1024)} MB limit"
                )
            try:
                decoded = base64.b64decode(data, validate=True)
            except (binascii.Error, ValueError):
                raise TypeError(
                    f"session/prompt.prompt[{i}].data is not valid base64"
                )
            if len(decoded) > _MAX_IMAGE_BYTES:
                raise TypeError(
                    f"session/prompt.prompt[{i}]: image exceeds the "
                    f"{_MAX_IMAGE_BYTES // (1024 * 1024)} MB limit"
                )
            if len(images) >= _MAX_IMAGES_PER_PROMPT:
                raise TypeError(
                    f"session/prompt.prompt: too many image blocks "
                    f"(max {_MAX_IMAGES_PER_PROMPT})"
                )
            images.append({"data": data, "mimeType": mime_type})
        elif btype in ("audio", "resource"):
            raise TypeError(
                f"session/prompt.prompt[{i}]: block type {btype!r} is not yet "
                f"supported (v1 supports 'text', 'resource_link', and 'image')"
            )
        else:
            raise TypeError(
                f"session/prompt.prompt[{i}]: unknown block type {btype!r}"
            )

    return "\n\n".join(rendered), images


# ---------------------------------------------------------------------------
# Stop reason
# ---------------------------------------------------------------------------

#: ``TurnOutcome.incomplete_reason`` → ACP ``StopReason``.
#:
#: Only the two reasons that describe a *budget* the harness enforced map to a
#: non-``end_turn`` value:
#:
#: * ``length_truncated`` → ``max_tokens``. The turn stopped because the model
#:   hit the token limit, which is precisely what ACP's ``max_tokens`` names.
#: * ``doom_loop`` → ``max_turn_requests``. The harness halted a turn that kept
#:   re-issuing the same call. ACP has no "the agent was going in circles"
#:   member, and ``max_turn_requests`` ("maximum number of model requests in a
#:   single turn was exceeded") is the honest neighbour: both are the harness
#:   capping requests within one turn.
#:
#: ``no_output`` and ``reasoning_only`` are deliberately absent, so they fall
#: through to ``end_turn``. That is not a shortfall: the turn genuinely *did*
#: end normally, the model simply produced no prose. ACP's enum describes why a
#: turn *stopped*, not whether it said anything useful — a client that needs
#: that distinction reads the streamed content, which is empty.
#:
#: ``llm_error`` is also absent. See :func:`_stop_reason_for`.
_INCOMPLETE_TO_STOP_REASON = {
    "length_truncated": "max_tokens",
    "doom_loop": "max_turn_requests",
}


def _stop_reason_for(
    *,
    cancelled: bool,
    outcome: Any,
    max_iterations_hit: bool,
) -> str:
    """Map a finished turn onto the ACP ``StopReason`` enum.

    Precedence matters. ``cancelled`` wins over everything: the client asked
    for the turn to stop, and that fact outranks whatever state the turn was in
    when it noticed. ``max_iterations_hit`` is checked next because budget
    exhaustion is a separate axis from ``incomplete_reason`` — a turn can hit
    the iteration cap *and* carry a reason, and the cap is the more specific
    account of why it ended.

    ``llm_error`` maps to ``end_turn``, which is the least-bad option rather
    than a good one: ACP v1's closed enum has no member for "the model call
    failed". ``refusal`` is the only remaining value and it means the agent
    declined on content grounds — reporting an API outage as a refusal would
    trade a vague answer for a false one. The failure is not hidden: the
    ``[LLM API error: …]`` notice is the turn's text and has already been
    streamed to the client. Surfacing it as a JSON-RPC error instead would be
    more truthful, but it changes the response *shape* for a case that
    currently returns a result, so it needs its own decision.
    """
    if cancelled:
        return "cancelled"
    if max_iterations_hit:
        return "max_turn_requests"
    reason = getattr(outcome, "incomplete_reason", None)
    return _INCOMPLETE_TO_STOP_REASON.get(reason, "end_turn")


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
      7. Invoke ``agent.chat(user_text, cancellation_token=token,
         images=...)`` (images present only for ``image`` content blocks).
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
    user_text, images = _parse_prompt(params.get("prompt"))

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
    # Clear before the turn, not after: the flag is set from inside the chat
    # loop, and a session serves many turns. Leaving it set would make every
    # later turn on a long-lived session report max_turn_requests.
    _transport = getattr(session.agent, "transport", None)
    if _transport is not None:
        try:
            _transport.max_iterations_hit = False
        except AttributeError:  # transport that does not accept the attribute
            pass
    try:
        reply = session.agent.chat(
            user_text, cancellation_token=token, images=images or None
        )
        logger.debug(
            "acp: session %s turn finished, reply length=%d",
            session_id,
            len(reply) if isinstance(reply, str) else -1,
        )
        stop_reason = _stop_reason_for(
            cancelled=token.is_cancelled,
            outcome=getattr(session.agent, "last_turn", None),
            max_iterations_hit=bool(
                getattr(_transport, "max_iterations_hit", False)
            ),
        )
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
