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

from typing import Any, List, Optional, TYPE_CHECKING

from ..context_manager import _get_tiktoken_encoding
from ..llm.client import KEEP_BASE_URL
from ..transport import AgentEvent, EventType

if TYPE_CHECKING:
    from ..agent import Agentao


def set_provider(
    agent: "Agentao",
    api_key: str,
    base_url: Any = KEEP_BASE_URL,
    model: Optional[str] = None,
) -> None:
    """Reconfigure the LLM client with a new provider's credentials.

    Emits ``MODEL_CHANGED`` with ``cause="set_provider"``. The API key
    is intentionally NOT included in the event payload — replay files
    would otherwise capture raw credentials.

    ``base_url`` defaults to the ``KEEP_BASE_URL`` sentinel ("keep the
    current endpoint"); an explicit value (including ``None``, which clears
    it to the SDK default) replaces it — so a cross-provider switch can drop
    a previous provider's custom endpoint.

    When ``model`` changes, the tiktoken encoding and cached prompt-token
    count on ``context_manager`` are reset — the same model-specific state
    ``set_model`` clears. A stale encoding would otherwise miscount tokens
    for the rest of the session after a cross-provider model switch.
    """
    _old_model = agent.llm.model
    _old_base = agent.llm.base_url
    agent.llm.reconfigure(api_key=api_key, base_url=base_url, model=model)
    if model is not None and model != _old_model:
        agent.context_manager._encoding = _get_tiktoken_encoding(agent.llm.model)
        agent.context_manager.invalidate_token_anchor()
    try:
        agent.transport.emit(AgentEvent(EventType.MODEL_CHANGED, {
            "old_model": _old_model,
            "new_model": agent.llm.model,
            # Compare the resolved endpoints so a clear (-> None) or a switch
            # is reported accurately, regardless of how base_url was passed.
            "base_url_changed": agent.llm.base_url != _old_base,
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
    # Built-in LLMClient only; injected host clients need not implement it.
    if hasattr(agent.llm, "reset_capability_latches"):
        agent.llm.reset_capability_latches()
    agent.context_manager._encoding = _get_tiktoken_encoding(model)
    agent.context_manager.invalidate_token_anchor()
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
