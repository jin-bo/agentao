"""Model / provider switching helpers.

Extracted from ``Agentao.set_model`` / ``set_provider`` /
``list_available_models``. Behavior is unchanged — each function
mutates the same agent attributes (``llm``, ``context_manager``) and
emits the same ``MODEL_CHANGED`` event payloads so CLI, replay and
ACP observers all keep working.

Kept as module-level functions rather than a class: these are
stateless operations over an ``Agentao`` handle, not a subsystem with
its own lifecycle.
"""

from __future__ import annotations

from typing import List, Optional, TYPE_CHECKING

from ..context_manager import _get_tiktoken_encoding
from ..transport import AgentEvent, EventType

if TYPE_CHECKING:
    from ..agent import Agentao


def set_provider(
    agent: "Agentao",
    api_key: str,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> None:
    """Reconfigure the LLM client with a new provider's credentials.

    Emits ``MODEL_CHANGED`` with ``cause="set_provider"``. The API key
    is intentionally NOT included in the event payload — replay files
    would otherwise capture raw credentials.
    """
    _old_model = agent.llm.model
    _old_base = agent.llm.base_url
    agent.llm.reconfigure(api_key=api_key, base_url=base_url, model=model)
    try:
        agent.transport.emit(AgentEvent(EventType.MODEL_CHANGED, {
            "old_model": _old_model,
            "new_model": agent.llm.model,
            "base_url_changed": base_url is not None and base_url != _old_base,
            "cause": "set_provider",
        }))
    except Exception:
        pass


def set_model(agent: "Agentao", model: str) -> str:
    """Switch the active model on the current provider.

    Also resets the tiktoken encoding and the cached prompt-token
    count on ``context_manager`` — both are model-specific, and a
    stale encoding would miscount tokens for the rest of the session.

    Returns a human-readable status string for CLI display.
    """
    old_model = agent.llm.model
    agent.llm.model = model
    agent.context_manager._encoding = _get_tiktoken_encoding(model)
    agent.context_manager._last_api_prompt_tokens = None
    agent.llm.logger.info(f"Model changed from {old_model} to {model}")
    try:
        agent.transport.emit(AgentEvent(EventType.MODEL_CHANGED, {
            "old_model": old_model,
            "new_model": model,
            "base_url_changed": False,
            "cause": "set_model",
        }))
    except Exception:
        pass
    return f"Model changed from {old_model} to {model}"


def list_available_models(agent: "Agentao") -> List[str]:
    """Fetch the model catalog from the configured endpoint.

    Raises ``RuntimeError`` on failure so CLI / ACP callers can
    surface the underlying reason — the raw exception is also logged
    to ``agentao.log`` for debugging.
    """
    try:
        models_page = agent.llm.client.models.list()
        return sorted([m.id for m in models_page.data])
    except Exception as e:
        agent.llm.logger.warning(f"Failed to fetch models from API: {e}")
        raise RuntimeError(f"Could not fetch model list: {e}") from e
