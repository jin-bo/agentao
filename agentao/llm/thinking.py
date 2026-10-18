"""Thinking depth: one level, written where the client's wire reads it.

Each wire spells the field differently, and each rejects the others'
spellings on every request:

- ``openai-completions``: ``reasoning_effort``.
- ``anthropic-messages``: ``output_config.effort``.
- ``openai-responses``: ``reasoning.effort``.

All three live in the client's ``extra_body``, the one place provider
request fields are carried (``docs/design/host-llm-extra-params.md``). There
is no auto-recovery: a model that rejects the level fails every request until
the level is cleared. ``/thinking`` and ``agentao run``'s ``thinking`` both go
through this module, and the CLI adds only the messages.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

#: Canonical reasoning-effort levels, low → high, on the OpenAI-shaped wires.
#: Provider-neutral: the OpenAI o-series / gpt-5 scale that most
#: OpenAI-compatible providers mirror. Not a check: a token outside it is
#: sent as written, and the provider validates it.
REASONING_LEVELS = ("minimal", "low", "medium", "high")

#: The values the Messages API accepts for ``output_config.effort``, when the
#: Models API has not said which ones the model supports.
ANTHROPIC_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

#: The keyword that clears the level. Not a level: a provider whose scale has
#: a literal ``none`` is set with ``none``.
OFF = "off"


def anthropic_effort_levels(capabilities: Any) -> Tuple[str, ...]:
    """The effort levels the Models API lists for the model, weakest first.

    :data:`ANTHROPIC_EFFORT_LEVELS` when the endpoint has not said (the
    capabilities are read on the first request, so before it they are
    ``None``), or when what it said lists none.
    """
    try:
        effort = capabilities["effort"]
        levels = tuple(name for name, value in effort.items()
                       if isinstance(value, dict) and value.get("supported") is True)
    except (KeyError, TypeError, AttributeError):
        return ANTHROPIC_EFFORT_LEVELS
    # The API lists them alphabetically; show them weakest first.
    known = ANTHROPIC_EFFORT_LEVELS
    ordered = tuple(sorted(levels, key=lambda n: known.index(n) if n in known else len(known)))
    return ordered or ANTHROPIC_EFFORT_LEVELS


def current_thinking(llm: Any) -> Tuple[bool, Any]:
    """``(is_set, value)`` for the client's level, as its wire reads it.

    Membership, not ``.get() is None``: a host may set the field to ``None``
    explicitly, which is still sent. A ``reasoning`` that is not an object on
    ``openai-responses`` counts as set to itself: the API rejects it, and
    :func:`set_thinking` / :func:`clear_thinking` replace it.
    """
    extra_body = getattr(llm, "extra_body", None)
    if not isinstance(extra_body, Mapping):
        return False, None
    api_format = getattr(llm, "api_format", None)
    if api_format == "anthropic-messages":
        output_config = extra_body.get("output_config")
        if isinstance(output_config, Mapping) and "effort" in output_config:
            return True, output_config["effort"]
        return False, None
    if api_format == "openai-responses":
        if "reasoning" not in extra_body:
            return False, None
        reasoning = extra_body["reasoning"]
        if not isinstance(reasoning, Mapping):
            return True, reasoning
        if "effort" in reasoning:
            return True, reasoning["effort"]
        return False, None
    if "reasoning_effort" in extra_body:
        return True, extra_body["reasoning_effort"]
    return False, None


def sent_thinking(llm: Any) -> Optional[str]:
    """The level the client's requests carry in the field its wire reads.

    ``None`` when none is set or the value is ``None``. Unlike
    :func:`current_thinking`, a ``reasoning`` that is not an object on
    ``openai-responses`` is not a level: no ``reasoning.effort`` is sent then.

    It reports this wire's field only. It does not say the request is free
    of another wire's field left in ``extra_body`` after a provider switch.
    """
    is_set, value = current_thinking(llm)
    if not is_set or value is None:
        return None
    if getattr(llm, "api_format", None) == "openai-responses" and not isinstance(
        llm.extra_body.get("reasoning"), Mapping
    ):
        return None
    return value if isinstance(value, str) else str(value)


class ThinkingUnsupported(ValueError):
    """The client has nowhere to write a level: a fact about it, not the level."""


def _extra_body(llm: Any) -> dict:
    """The client's ``extra_body``, made a dict when it is ``None``.

    Raises :class:`ThinkingUnsupported` when the client has no
    ``extra_body`` at all, or one that is not a dict: a host's own client may
    lack it, and then there is nowhere to write.
    """
    if not hasattr(llm, "extra_body"):
        raise ThinkingUnsupported("the LLM client has no extra_body passthrough")
    if llm.extra_body is None:
        llm.extra_body = {}
    if not isinstance(llm.extra_body, dict):
        raise ThinkingUnsupported("the LLM client's extra_body is not a dict")
    return llm.extra_body


def set_thinking(llm: Any, level: str) -> str:
    """Write ``level`` where the client's wire reads it. Returns what was stored.

    A known level is stored lowercase. Another single word is stored as
    written on the OpenAI-shaped wires, because the scale is the model's
    (some have ``none`` or ``xhigh``) and a case-sensitive one must not be
    mangled. On ``anthropic-messages`` a level must be one the model lists
    (:func:`anthropic_effort_levels`). A ``reasoning_effort`` carried in
    across a wire switch is dropped, because the other two wires reject it.

    Raises ``ValueError`` for more than one word, an unlisted Anthropic level,
    or a client with nowhere to write. ``off`` is not a level: use
    :func:`clear_thinking`.
    """
    if not isinstance(level, str) or len(level.split()) != 1:
        raise ValueError(f"expected a single level, got {level!r}")
    level = level.strip()
    lowered = level.lower()
    if lowered == OFF:
        raise ValueError("'off' clears the level; it is not one")
    api_format = getattr(llm, "api_format", None)
    if api_format == "anthropic-messages":
        levels = anthropic_effort_levels(getattr(llm, "model_capabilities", None))
        if lowered not in levels:
            raise ValueError(
                f"{level!r} is not a thinking depth for this model "
                f"({' | '.join(levels)})"
            )
        extra_body = _extra_body(llm)
        output_config = extra_body.get("output_config")
        if not isinstance(output_config, dict):
            output_config = {}
            extra_body["output_config"] = output_config
        output_config["effort"] = lowered
        extra_body.pop("reasoning_effort", None)
        return lowered
    value = lowered if lowered in REASONING_LEVELS else level
    extra_body = _extra_body(llm)
    if api_format == "openai-responses":
        reasoning = extra_body.get("reasoning")
        if not isinstance(reasoning, dict):
            # Not something this API accepts; replaced. Other keys a host set
            # in a dict (``summary``) are kept.
            reasoning = {}
            extra_body["reasoning"] = reasoning
        reasoning["effort"] = value
        extra_body.pop("reasoning_effort", None)
        return value
    extra_body["reasoning_effort"] = value
    return value


def clear_thinking(llm: Any) -> bool:
    """Remove the level, so the provider's default applies. True if one was set.

    Also drops a ``reasoning_effort`` that a wire switch carried onto a wire
    that rejects it. Other keys a host set under ``output_config`` or
    ``reasoning`` are kept. A client without ``extra_body`` has nothing to
    clear.
    """
    extra_body = getattr(llm, "extra_body", None)
    if not isinstance(extra_body, dict):
        return False
    was_set, _ = current_thinking(llm)
    api_format = getattr(llm, "api_format", None)
    stale = "reasoning_effort" in extra_body
    extra_body.pop("reasoning_effort", None)
    if api_format == "anthropic-messages":
        output_config = extra_body.get("output_config")
        if isinstance(output_config, dict):
            output_config.pop("effort", None)
            if not output_config:
                extra_body.pop("output_config", None)
        return was_set or stale
    if api_format == "openai-responses":
        reasoning = extra_body.get("reasoning")
        if isinstance(reasoning, dict):
            reasoning.pop("effort", None)
        if "reasoning" in extra_body and (not isinstance(reasoning, dict) or not reasoning):
            extra_body.pop("reasoning", None)
        return was_set or stale
    return was_set
