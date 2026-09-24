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
        # ``list_available_models`` raises ``RuntimeError`` with a message already
        # prefixed and free of the endpoint's response body; anything else is
        # named by type only, so a body cannot reach the client this way either.
        warning = (
            str(e) if isinstance(e, RuntimeError)
            else f"Could not fetch model list: {type(e).__name__}"
        )
        return {"models": list(cached), "warning": warning}


def register(server: "AcpServer") -> None:
    server.register(
        METHOD_SESSION_LIST_MODELS,
        lambda params: handle_session_list_models(server, params),
    )
