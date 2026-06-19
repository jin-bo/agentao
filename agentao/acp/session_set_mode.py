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
from .protocol import INVALID_REQUEST, METHOD_SESSION_SET_MODE, METHOD_SESSION_UPDATE
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


def _emit_current_mode_update(
    server: "AcpServer", session_id: str, mode_id: str
) -> None:
    """Emit a ``current_mode_update`` session/update for the mode change.

    ACP communicates a mode change via this notification — the standard
    ``session/set_mode`` response is empty. Nothing else on the ACP path
    emits it: ``PermissionEngine.set_mode`` is silent and the
    ``PERMISSION_MODE_CHANGED`` event is CLI-only, so the handler must emit
    here. ``mode_id`` is echoed verbatim, including non-preset UI modeIds
    (e.g. DeepChat's ``code``/``ask``) that are not in ``availableModes``.

    Best-effort: a notification failure must not fail the set_mode request.
    """
    try:
        server.write_notification(
            METHOD_SESSION_UPDATE,
            {
                "sessionId": session_id,
                "update": {
                    "sessionUpdate": "current_mode_update",
                    "currentModeId": mode_id,
                },
            },
        )
    except Exception:
        logger.exception(
            "acp: failed to emit current_mode_update for session %s", session_id
        )


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

    # Emit ``current_mode_update`` outside the turn lock — write_notification
    # serializes its own writes, so there's no need to hold turn_lock across
    # the I/O. Keep returning ``{modeId}`` for DeepChat back-compat; a
    # standard client reads the notification and ignores the extra field.
    _emit_current_mode_update(server, session.session_id, mode_id)
    return {"modeId": mode_id}


def register(server: "AcpServer") -> None:
    server.register(
        METHOD_SESSION_SET_MODE,
        lambda params: handle_session_set_mode(server, params),
    )
