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
from ..llm._stream_response import WIRE_CARRIER_KEYS
from ..llm.client import KEEP_BASE_URL
from ..transport import AgentEvent, EventType

if TYPE_CHECKING:
    from ..agent import Agentao


def purge_thinking_artifacts(messages: List[dict]) -> int:
    """Drop provider-minted thinking artifacts from conversation history.

    Removes ``reasoning_content`` and every wire's carrier — the
    ``anthropic-messages`` wire's signed thinking blocks
    (``anthropic_thinking_blocks``), the ``openai-responses`` wire's encrypted
    reasoning items (``openai_reasoning_items``) — from assistant messages,
    and ``thought_signature`` from **both** levels of each tool call — the
    entry itself and its ``function`` object. Returns the number of fields
    removed (0 when history was already clean).

    A new wire adapter that persists a carrier key of its own adds it to
    ``WIRE_CARRIER_KEYS`` (``llm/_stream_response.py``), which this reads: the
    purge is the union of every adapter's keys, and it runs on every switch,
    whichever wire is live. That tuple is also what *records* a carrier, so
    one cannot be persisted without being purged.

    Both levels are load-bearing, not defensive breadth: ``_serialize_tool_call``
    serialises via ``model_dump()`` precisely so the field survives
    "regardless of which level they appear at", and the real Gemini-shaped
    response puts it at the tool-call level. Purging only ``function`` would
    miss the exact case this exists for.

    **Why this is unconditional rather than provenance-tracked.** Both fields
    are minted by one specific model and are only meaningful to it — an
    Anthropic signed thinking block is rejected outright by a different model,
    and a Gemini ``thought_signature`` is validated against the model that
    issued it. agentao serialises both into history (``_attach_reasoning`` and
    ``_serialize_tool_call``, the latter deliberately, so Gemini thinking
    models keep working across turns) and sends history back verbatim — the
    OpenAI SDK does not strip unknown message keys.

    goose solves the same problem by stamping each message with the model that
    produced it and dropping blocks whose stamp differs (#10007). That is the
    right design *there*; here it would mean adding a provenance key to every
    assistant message, which — precisely because unknown keys are not
    stripped — would itself go out on the wire and have to be filtered back
    off. It is also unnecessary: this runs *at the moment of the switch*, and
    everything already in history was, by definition, minted by the model
    being switched away from. "Stale" and "present" are the same set, so no
    stamp is needed to tell them apart.

    Called by :func:`set_model` / :func:`set_provider`, which already curate a
    clear-on-switch list (tiktoken encoding, cached token anchor, capability
    latches); these two fields are the same category of model-specific state.
    """
    removed = 0
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.pop("reasoning_content", None) is not None:
            removed += 1
        for key in WIRE_CARRIER_KEYS:
            if msg.pop(key, None) is not None:
                removed += 1
        tool_calls = msg.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            if tc.pop("thought_signature", None) is not None:
                removed += 1
            fn = tc.get("function")
            if isinstance(fn, dict) and fn.pop("thought_signature", None) is not None:
                removed += 1
    return removed


def _purge_and_log(agent: "Agentao", cause: str) -> None:
    """Run :func:`purge_thinking_artifacts` over the agent's history and log.

    Tolerant of a host that swapped ``messages`` for something exotic — a
    switch must not fail because history hygiene could not run.
    """
    try:
        removed = purge_thinking_artifacts(agent.messages)
    except Exception:  # pragma: no cover — defensive
        agent.llm.logger.warning("%s: could not purge thinking artifacts", cause)
        return
    if removed:
        agent.llm.logger.info(
            "%s: purged %d stale thinking artifact(s) from history "
            "(minted by the previous model)", cause, removed,
        )
        # History just shrank, so any cached prompt-token anchor is stale.
        # The model-change paths invalidate it for their own reasons; the
        # endpoint-only path would otherwise keep counting the bytes we
        # removed for the rest of the session.
        try:
            agent.context_manager.invalidate_token_anchor()
        except Exception:  # pragma: no cover — defensive
            pass


def set_provider(
    agent: "Agentao",
    api_key: str,
    base_url: Any = KEEP_BASE_URL,
    model: Optional[str] = None,
    api_format: Optional[str] = None,
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

    ``api_format`` names the new provider's wire protocol; ``None`` keeps the
    current one. A wire change is a switch in the same sense as a model or
    endpoint change — it clears the same state — and it is only forwarded
    when given, so an injected client whose ``reconfigure`` predates the
    parameter keeps working.
    """
    _old_model = agent.llm.model
    _old_base = agent.llm.base_url
    _old_format = getattr(agent.llm, "api_format", None)
    if api_format is None:
        agent.llm.reconfigure(api_key=api_key, base_url=base_url, model=model)
    else:
        agent.llm.reconfigure(
            api_key=api_key, base_url=base_url, model=model, api_format=api_format,
        )
    _format_changed = getattr(agent.llm, "api_format", None) != _old_format
    if _format_changed:
        # The anchor is a count the *previous* wire reported for this history,
        # and the observed limit was parsed from its error text.
        agent.context_manager.invalidate_token_anchor()
        agent.context_manager.clear_observed_limit("wire protocol switch")
    if model is not None and model != _old_model:
        agent.context_manager._encoding = _get_tiktoken_encoding(agent.llm.model)
        agent.context_manager.invalidate_token_anchor()
        agent.context_manager.clear_observed_limit("model switch")
    elif agent.llm.base_url != _old_base:
        # Same model name behind a different endpoint is a different
        # deployment, and deployments differ in what they will accept.
        agent.context_manager.clear_observed_limit("provider switch")
    # Also purge on an endpoint change with the model name unchanged: the same
    # name behind a different backend is a different signer, so its thinking
    # artifacts are just as stale. A pure credential rotation (same model, same
    # base_url) changes neither and leaves history alone.
    if (
        (model is not None and model != _old_model)
        or agent.llm.base_url != _old_base
        or _format_changed
    ):
        _purge_and_log(agent, "set_provider")
    try:
        agent.transport.emit(AgentEvent(EventType.MODEL_CHANGED, {
            "old_model": _old_model,
            "new_model": agent.llm.model,
            # Compare the resolved endpoints so a clear (-> None) or a switch
            # is reported accurately, regardless of how base_url was passed.
            "base_url_changed": agent.llm.base_url != _old_base,
            # A wire-only switch changes neither of the two above; without
            # this the replay record of it reads as a no-op.
            "api_format_changed": _format_changed,
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
    if model != old_model:
        # Joins the clear-on-switch family beside the encoding and the anchor:
        # the observed limit describes the model that rejected the request.
        agent.context_manager.clear_observed_limit("model switch")
        _purge_and_log(agent, "set_model")
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


# The catalog is fetched while a user waits on ``/model`` or an ACP client waits on
# ``session/list_models``. Left to the SDK's defaults that wait was 600 s a try, three
# tries. One retry keeps a single dropped connection from failing the listing.
_MODEL_LIST_TIMEOUT_S = 10.0
_MODEL_LIST_MAX_RETRIES = 1


def _describe_model_list_failure(e: BaseException) -> str:
    """Name what failed without repeating what the endpoint said.

    ``str()`` of an SDK status error carries the response body, and this text
    reaches an ACP client verbatim and the CLI through Rich markup. The body
    stays in ``agentao.log``, behind its redacting formatter.
    """
    status = getattr(e, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        return f"the models endpoint answered HTTP {status}"
    name = type(e).__name__
    if name == "APITimeoutError" or isinstance(e, TimeoutError):
        return (
            f"the models endpoint did not answer within {_MODEL_LIST_TIMEOUT_S:.0f}s"
            f" ({1 + _MODEL_LIST_MAX_RETRIES} tries)"
        )
    if name == "APIConnectionError":
        return "could not connect to the models endpoint"
    return name


def list_available_models(agent: "Agentao") -> List[str]:
    """Fetch the model catalog from the configured endpoint.

    Raises ``RuntimeError`` on failure so CLI / ACP callers can surface
    what went wrong. Its message names the failure (status, timeout,
    connection) and never carries the response body; the raw exception
    is logged to ``agentao.log`` for debugging.
    """
    try:
        client = agent.llm.client.with_options(
            timeout=_MODEL_LIST_TIMEOUT_S, max_retries=_MODEL_LIST_MAX_RETRIES,
        )
        models_page = client.models.list()
        return sorted([m.id for m in models_page.data])
    except Exception as e:
        agent.llm.logger.warning(f"Failed to fetch models from API: {e}")
        raise RuntimeError(
            f"Could not fetch model list: {_describe_model_list_failure(e)} (details in agentao.log)"
        ) from e
