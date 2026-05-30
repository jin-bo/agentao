"""ACP vendor method ``_agentao.cn/set_model`` — free-form model entry.

A ``select``-only ``session/set_config_option`` cannot express a free-form
"type any model" string, so this vendor method survives for that UX. Payload
is the minimal ``{sessionId, model}``: secret-free (no ``apiKey`` / ``baseUrl``
/ ``_meta``), model-only (the provider is unchanged). It deliberately reuses
the ``model`` field name (matching ``session/set_model`` and the
``set_config_option`` value) so a DeepChat-style adapter maps its UI
``modelId`` → ``model`` with no ``modelId`` alias on the wire.

The vendor prefix (``_agentao.cn/``) keeps this off the bare ``session/``
namespace, consistent with ``_agentao.cn/ask_user``. It shares the core
``agent.set_model()`` code path with the other model-set surfaces — no logic
fork, so the three entries cannot drift.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

from ._handler_utils import hold_idle_turn_lock, require_active_session
from .protocol import METHOD_AGENTAO_SET_MODEL

if TYPE_CHECKING:
    from .server import AcpServer

logger = logging.getLogger(__name__)

# Secret-free by construction: only sessionId/model are read off the wire.
_ALLOWED_KEYS = frozenset({"sessionId", "model"})


def handle_agentao_set_model(server: "AcpServer", params: Any) -> Dict[str, Any]:
    session = require_active_session(server, params, METHOD_AGENTAO_SET_MODEL)

    extra = set(params) - _ALLOWED_KEYS
    if extra:
        raise TypeError(
            f"{METHOD_AGENTAO_SET_MODEL}: unexpected field(s) {sorted(extra)}; "
            "only sessionId/model are accepted (model-only, secret-free)"
        )

    model = params.get("model")
    if not isinstance(model, str) or not model:
        raise TypeError(f"{METHOD_AGENTAO_SET_MODEL}.model must be a non-empty string")

    # Holding turn_lock prevents an in-flight session/prompt from observing a
    # model change mid-stream.
    with hold_idle_turn_lock(session, METHOD_AGENTAO_SET_MODEL):
        session.agent.set_model(model)
        return {"model": session.agent.llm.model}


def register(server: "AcpServer") -> None:
    server.register(
        METHOD_AGENTAO_SET_MODEL,
        lambda params: handle_agentao_set_model(server, params),
    )
