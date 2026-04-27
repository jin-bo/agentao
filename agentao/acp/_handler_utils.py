"""Internal helpers shared by ACP session/* handlers.

Centralizes the boilerplate that gates every ``session/*`` request:

  1. server has completed the ``initialize`` handshake
  2. ``params`` is a JSON object
  3. ``sessionId`` is a non-empty string
  4. the session exists and is still active

Existing handlers (``session_cancel``, ``session_load``, ``session_prompt``)
predate this module and continue to inline the same checks; the helper is
used by the newer ``set_model`` / ``set_mode`` / ``list_models`` modules.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, Iterator

from .models import AcpSessionState
from .protocol import INVALID_REQUEST, SERVER_NOT_INITIALIZED
from .server import JsonRpcHandlerError
from .session_manager import SessionNotFoundError

if TYPE_CHECKING:
    from .server import AcpServer


def require_active_session(
    server: "AcpServer", params: Any, method: str
) -> AcpSessionState:
    """Validate the request envelope and return the live session state.

    Raises ``JsonRpcHandlerError(SERVER_NOT_INITIALIZED)`` if the connection
    is pre-handshake, ``TypeError`` for bad param shape (the dispatcher maps
    that to ``INVALID_PARAMS``), or ``JsonRpcHandlerError(INVALID_REQUEST)``
    for unknown / closed sessions.
    """
    if not server.state.initialized:
        raise JsonRpcHandlerError(
            code=SERVER_NOT_INITIALIZED,
            message=f"{method} called before initialize handshake",
        )

    if not isinstance(params, dict):
        raise TypeError(f"{method} params must be a JSON object")

    session_id = params.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        raise TypeError(f"{method}.sessionId must be a non-empty string")

    try:
        session = server.sessions.require(session_id)
    except SessionNotFoundError:
        raise JsonRpcHandlerError(
            code=INVALID_REQUEST,
            message=f"unknown sessionId: {session_id}",
        )

    if session.closed or session.agent is None:
        raise JsonRpcHandlerError(
            code=INVALID_REQUEST,
            message=f"session {session_id} is not active",
        )

    return session


@contextlib.contextmanager
def hold_idle_turn_lock(session: AcpSessionState, method: str) -> Iterator[None]:
    """Hold ``session.turn_lock`` for the duration of a state mutation.

    The ACP dispatcher runs requests on a worker pool, so a model/mode
    update racing an in-flight ``session/prompt`` would let the active
    turn observe runtime changes mid-stream while the client is told the
    switch already landed. Non-blocking acquire mirrors ``session_prompt``
    (see ``session_prompt.py:182``) — we never queue dispatcher workers
    behind a long-running turn; the client gets a clear error instead.
    """
    if not session.turn_lock.acquire(blocking=False):
        raise JsonRpcHandlerError(
            code=INVALID_REQUEST,
            message=(
                f"{method} rejected: session {session.session_id} has an "
                "active turn; retry after it completes"
            ),
        )
    try:
        yield
    finally:
        session.turn_lock.release()
