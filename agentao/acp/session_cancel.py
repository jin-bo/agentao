"""ACP ``session/cancel`` handler (Issue 09).

Cancels the active turn on a given ACP session by firing the per-turn
:class:`CancellationToken` that :mod:`agentao.acp.session_prompt` bound
during ``session/prompt`` (Issue 06). Almost all the work for this issue
already shipped in earlier issues:

- Issue 06 added ``AcpSessionState.cancel_token`` and rotates a fresh
  token per turn so this handler always has something to fire.
- Issue 06 reused Agentao's existing :class:`CancellationToken`, which
  is plumbed through ``agent.chat`` → tool runner → LLM streaming →
  sub-agents. Firing the token here propagates to all of them.
- Issue 08 made the dispatcher concurrent: ``session/prompt`` runs on a
  worker thread, so a ``session/cancel`` arriving on the read loop is
  routed without blocking on the in-flight prompt.

Wire shape
----------

ACP defines ``session/cancel`` as a notification (no ``id``). Robust
clients still occasionally send it as a request, so this handler returns
``None`` either way — the dispatcher writes ``{"result": null}`` for the
request case and drops the response for the notification case.

Idempotency
-----------

The handler tolerates four "harmless" states without raising:

1. **No turn in flight** — ``session.cancel_token is None`` because the
   client cancelled between turns. We log and return successfully so
   double-cancels are safe.
2. **Token already cancelled** — :meth:`CancellationToken.cancel` is
   already idempotent ("first call wins"); a second call is a no-op.
3. **Session is closed** — the runtime is being torn down and any
   in-flight token was already cancelled by :meth:`AcpSessionState.close`.
4. **Repeated cancels for the same turn** — naturally absorbed because
   the token is single-shot.

Hard errors that DO raise (mapped to ``-32602`` / ``-32600`` for
clients that sent the cancel as a request):

- Bad ``params`` shape (not a dict, missing ``sessionId``, etc.) →
  :class:`TypeError`
- Unknown ``sessionId`` → :class:`JsonRpcHandlerError(INVALID_REQUEST)`

Notification errors are swallowed by the dispatcher per JSON-RPC 2.0,
so a malformed notification cancel is silently dropped — that is the
correct spec behavior.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .protocol import METHOD_SESSION_CANCEL
from ._handler_utils import resolve_session

if TYPE_CHECKING:
    from .server import AcpServer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handle_session_cancel(server: "AcpServer", params: Any) -> None:
    """Cancel the active turn on the named ACP session.

    Returns ``None`` regardless of internal state — every "no-op" path
    (no active turn, already cancelled, session closed) succeeds quietly
    so the client can fire-and-forget cancels without coordination.

    Hard failure modes (raise so request-mode clients get a clean error):

      - ``server.state.initialized`` is False → ``SERVER_NOT_INITIALIZED``
      - ``params`` is not a dict → ``TypeError`` → ``INVALID_PARAMS``
      - ``sessionId`` missing/empty → ``TypeError`` → ``INVALID_PARAMS``
      - ``sessionId`` unknown → ``INVALID_REQUEST`` (so the client can
        distinguish "I asked the wrong server" from "the turn already
        ended")

    Why "unknown sessionId" raises but "no active turn" does not: the
    former is a client bug worth surfacing; the latter is a routine
    race between cancel and turn completion that should never produce
    a noisy error.
    """
    session = resolve_session(server, params, METHOD_SESSION_CANCEL)
    session_id = session.session_id

    if session.closed:
        # Already torn down — close() fired the token if there was one.
        # Quietly succeed so a cancel-during-shutdown race is harmless.
        logger.debug(
            "acp: session/cancel for already-closed session %s — no-op",
            session_id,
        )
        return None

    token = session.cancel_token
    if token is None:
        # No active turn. Could be a stale cancel, a cancel after the
        # turn already ended naturally, or a "preemptive" cancel before
        # the next prompt. All three are routine — log and succeed.
        logger.info(
            "acp: session/cancel for %s but no active turn — no-op",
            session_id,
        )
        return None

    if token.is_cancelled:
        # Token already fired. CancellationToken.cancel() is itself
        # idempotent ("first call wins"), so calling cancel() again
        # would also be a no-op — but skipping it avoids a redundant
        # log line and reads more clearly to anyone tracing this path.
        logger.debug(
            "acp: session/cancel for %s but token already cancelled — no-op",
            session_id,
        )
        return None

    # The interesting path: there's a live turn and we're firing the
    # cancel for the first time. The token's cancel signal will be
    # observed by:
    #
    #   * the next ``token.check()`` inside the agent loop,
    #   * any LLM streaming callback that polls ``is_cancelled``,
    #   * the tool runner between phases,
    #   * any sub-agent created with the same token.
    #
    # ``session_prompt``'s finally block then clears
    # ``session.cancel_token`` and releases ``turn_lock``, so the next
    # session/prompt can proceed.
    token.cancel("acp-session-cancel")
    logger.info("acp: cancelled active turn on session %s", session_id)
    return None


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------

def register(server: "AcpServer") -> None:
    """Register the ``session/cancel`` handler on an :class:`AcpServer`."""
    server.register(
        METHOD_SESSION_CANCEL,
        lambda params: handle_session_cancel(server, params),
    )
