"""ACP ``session/list_models`` handler.

Lets the front end refresh the available-models catalog after the
``initialize`` handshake. Reuses ``agent.list_available_models()``.

Failure mode: on provider lookup failure the handler returns the cached
list (or empty) plus a ``warning`` field, rather than a JSON-RPC error.
A transient provider outage should not block the UI from rendering the
last-known list. The cache lives on ``AcpSessionState.last_known_models``
so it dies with the session — no module-level dict, no cross-session
leakage, no manual eviction.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

from ._handler_utils import require_active_session
from .protocol import METHOD_SESSION_LIST_MODELS

if TYPE_CHECKING:
    from .server import AcpServer

logger = logging.getLogger(__name__)


def handle_session_list_models(server: "AcpServer", params: Any) -> Dict[str, Any]:
    session = require_active_session(server, params, METHOD_SESSION_LIST_MODELS)

    try:
        models = list(session.agent.list_available_models())
        session.last_known_models = models
        return {"models": models}
    except Exception as e:
        logger.warning(
            "acp: session/list_models for %s failed: %s — returning cached list",
            session.session_id,
            e,
        )
        cached = session.last_known_models or []
        return {"models": list(cached), "warning": f"Could not fetch model list: {e}"}


def register(server: "AcpServer") -> None:
    server.register(
        METHOD_SESSION_LIST_MODELS,
        lambda params: handle_session_list_models(server, params),
    )
