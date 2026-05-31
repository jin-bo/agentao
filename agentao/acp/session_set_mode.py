"""ACP ``session/set_mode`` handler.

The ACP-standard field is ``modeId`` (not ``mode``), and a ``modeId`` is a
free-form UI/behavioural selector — not necessarily an Agentao permission
preset. This handler therefore:

  - reads ``modeId`` (the standard field name);
  - **accepts unknown values** — a ``modeId`` that does not match an Agentao
    :class:`~agentao.permissions.PermissionMode` (e.g. DeepChat's ``code`` /
    ``ask``) is persisted on the session and echoed back, *without* changing
    permission posture, rather than being rejected;
  - maps to a permission preset **only on an exact match**, calling
    ``permission_engine.set_mode(...)``.

Deferred (their own design — see the patch-revision doc): splitting the
permission axis from the UI mode axis, and advertising ``availableModes`` /
``currentModeId`` + the ``current_mode_update`` notification. This PR is the
minimal field rename + accept-unknown so a DeepChat-style client is not
rejected.

Per-session: each session owns its own ``PermissionEngine``, so a preset
change on session A never affects session B.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

from agentao.permissions import PermissionMode

from ._handler_utils import hold_idle_turn_lock, require_active_session
from .protocol import INVALID_REQUEST, METHOD_SESSION_SET_MODE
from .server import JsonRpcHandlerError

if TYPE_CHECKING:
    from .server import AcpServer

logger = logging.getLogger(__name__)


def _parse_mode_id(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError("session/set_mode.modeId must be a non-empty string")
    return value


def _as_permission_mode(mode_id: str) -> Optional[PermissionMode]:
    """Return the matching preset, or ``None`` for a non-preset modeId."""
    try:
        return PermissionMode(mode_id)
    except ValueError:
        return None


def handle_session_set_mode(server: "AcpServer", params: Any) -> Dict[str, Any]:
    session = require_active_session(server, params, METHOD_SESSION_SET_MODE)
    mode_id = _parse_mode_id(params.get("modeId"))

    preset = _as_permission_mode(mode_id)

    # Hold turn_lock so an in-flight tool call cannot consult the engine
    # mid-decision while we swap the active preset.
    with hold_idle_turn_lock(session, METHOD_SESSION_SET_MODE):
        if preset is not None:
            # A recognized preset actually changes permission posture, so an
            # engine is required for it. Unknown modeIds need no engine — they
            # are pure UI state we persist and echo.
            if session.agent.permission_engine is None:
                raise JsonRpcHandlerError(
                    code=INVALID_REQUEST,
                    message=(
                        f"session {session.session_id} has no permission engine "
                        f"to apply modeId {mode_id!r}"
                    ),
                )
            session.agent.permission_engine.set_mode(preset)
        else:
            logger.info(
                "acp: session %s set non-preset modeId %r (permission posture "
                "unchanged)",
                session.session_id,
                mode_id,
            )
        session.mode_id = mode_id
        return {"modeId": session.mode_id}


def register(server: "AcpServer") -> None:
    server.register(
        METHOD_SESSION_SET_MODE,
        lambda params: handle_session_set_mode(server, params),
    )
