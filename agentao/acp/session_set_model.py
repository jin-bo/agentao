"""ACP ``session/set_model`` handler.

Three knobs, kept strictly separate. Wiring ``maxTokens`` to the context
window would collapse the compression threshold (200K → a few K) and
trigger runaway compression — that is the load-bearing reason this
handler exists rather than a single overloaded "max_tokens" field::

    model          -> agent.set_model()                 (active model id)
    contextLength  -> agent.context_manager.max_tokens  (compression window)
    maxTokens      -> agent.llm.max_tokens              (per-request cap)

Returns the post-update values so the front end can confirm what landed
when only a subset of fields was sent.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

from ._handler_utils import hold_idle_turn_lock, require_active_session
from .protocol import METHOD_SESSION_SET_MODEL

if TYPE_CHECKING:
    from .server import AcpServer

logger = logging.getLogger(__name__)


def _parse_positive_int(value: Any, field: str) -> int:
    # ``bool`` is a subclass of ``int`` in Python — reject explicitly so
    # ``True`` / ``False`` don't slip through as 1 / 0.
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"session/set_model.{field} must be an integer")
    if value <= 0:
        raise TypeError(f"session/set_model.{field} must be > 0")
    return value


def handle_session_set_model(server: "AcpServer", params: Any) -> Dict[str, Any]:
    session = require_active_session(server, params, METHOD_SESSION_SET_MODEL)

    model = params.get("model")
    context_length = params.get("contextLength")
    max_tokens = params.get("maxTokens")

    if model is None and context_length is None and max_tokens is None:
        raise TypeError(
            "session/set_model requires at least one of model / contextLength / maxTokens"
        )

    if model is not None and (not isinstance(model, str) or not model):
        raise TypeError("session/set_model.model must be a non-empty string")
    if context_length is not None:
        context_length = _parse_positive_int(context_length, "contextLength")
    if max_tokens is not None:
        max_tokens = _parse_positive_int(max_tokens, "maxTokens")

    agent = session.agent

    # Apply each knob independently — a request carrying only ``model``
    # must not reset the caller's existing contextLength / maxTokens.
    # Holding turn_lock prevents an in-flight session/prompt from
    # observing a model/window/cap change mid-stream.
    with hold_idle_turn_lock(session, METHOD_SESSION_SET_MODEL):
        if model is not None:
            agent.set_model(model)
        if context_length is not None:
            agent.context_manager.max_tokens = context_length
        if max_tokens is not None:
            agent.llm.max_tokens = max_tokens

        return {
            "model": agent.llm.model,
            "contextLength": agent.context_manager.max_tokens,
            "maxTokens": agent.llm.max_tokens,
        }


def register(server: "AcpServer") -> None:
    server.register(
        METHOD_SESSION_SET_MODEL,
        lambda params: handle_session_set_model(server, params),
    )
