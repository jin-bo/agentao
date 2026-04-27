"""ACP ``session/set_mode`` handler.

Updates a session's permission preset by calling
``agent.permission_engine.set_mode(...)``. Per-session — each session owns
its own ``PermissionEngine``, so a mode change on session A never affects
session B.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

from agentao.permissions import PermissionMode

from ._handler_utils import hold_idle_turn_lock, require_active_session
from .protocol import INVALID_REQUEST, METHOD_SESSION_SET_MODE
from .server import JsonRpcHandlerError

if TYPE_CHECKING:
    from .server import AcpServer

logger = logging.getLogger(__name__)


def _parse_mode(value: Any) -> PermissionMode:
    if not isinstance(value, str) or not value:
        raise TypeError("session/set_mode.mode must be a non-empty string")
    try:
        return PermissionMode(value)
    except ValueError:
        valid = ", ".join(sorted(m.value for m in PermissionMode))
        raise TypeError(f"session/set_mode.mode must be one of: {valid}")


def handle_session_set_mode(server: "AcpServer", params: Any) -> Dict[str, Any]:
    session = require_active_session(server, params, METHOD_SESSION_SET_MODE)
    mode = _parse_mode(params.get("mode"))

    if session.agent.permission_engine is None:
        raise JsonRpcHandlerError(
            code=INVALID_REQUEST,
            message=f"session {session.session_id} has no permission engine to update",
        )

    # Hold turn_lock so an in-flight tool call cannot consult the engine
    # mid-decision while we swap the active preset.
    with hold_idle_turn_lock(session, METHOD_SESSION_SET_MODE):
        session.agent.permission_engine.set_mode(mode)
        return {"mode": session.agent.permission_engine.active_mode.value}


def register(server: "AcpServer") -> None:
    server.register(
        METHOD_SESSION_SET_MODE,
        lambda params: handle_session_set_mode(server, params),
    )
