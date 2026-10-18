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

A wire switch moves a level this module wrote, and only that one: the field
is told apart by where it came from, not by its name
(:func:`carry_over_wire_switch`). A field the host set stays as it was.
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

#: The attribute on the client where :func:`set_thinking` records what it
#: wrote, as ``(api_format, level)``. The record, not the field name, is what
#: makes a field agentao's own at a wire switch.
_WRITTEN = "_agentao_thinking_written"


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
    return _read(getattr(llm, "extra_body", None), getattr(llm, "api_format", None))


def _read(extra_body: Any, api_format: Any) -> Tuple[bool, Any]:
    """:func:`current_thinking` for an ``extra_body`` and a wire named apart."""
    if not isinstance(extra_body, Mapping):
        return False, None
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
    of another wire's field the host set: only a level this module wrote
    moves at a wire switch (:func:`carry_over_wire_switch`).
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


def writable_extra_body(llm: Any) -> dict:
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
    if level.lower() == OFF:
        raise ValueError("'off' clears the level; it is not one")
    api_format = getattr(llm, "api_format", None)
    stored = _write(llm, api_format, level)
    _record(llm, (api_format, stored))
    return stored


def _write(llm: Any, api_format: Any, level: str, *, drop_stale: bool = True) -> str:
    """Write a single-word ``level`` into ``api_format``'s field. Returns it.

    ``drop_stale`` also drops a ``reasoning_effort`` on a wire that rejects
    it, as ``/thinking`` does. A wire-switch move passes ``False``: that
    field is not one this module wrote, so it is the host's.
    """
    lowered = level.lower()
    if api_format == "anthropic-messages":
        levels = anthropic_effort_levels(getattr(llm, "model_capabilities", None))
        if lowered not in levels:
            raise ValueError(
                f"{level!r} is not a thinking depth for this model "
                f"({' | '.join(levels)})"
            )
        extra_body = writable_extra_body(llm)
        output_config = extra_body.get("output_config")
        if not isinstance(output_config, dict):
            output_config = {}
            extra_body["output_config"] = output_config
        output_config["effort"] = lowered
        if drop_stale:
            extra_body.pop("reasoning_effort", None)
        return lowered
    value = lowered if lowered in REASONING_LEVELS else level
    extra_body = writable_extra_body(llm)
    if api_format == "openai-responses":
        reasoning = extra_body.get("reasoning")
        if not isinstance(reasoning, dict):
            # Not something this API accepts; replaced. Other keys a host set
            # in a dict (``summary``) are kept.
            reasoning = {}
            extra_body["reasoning"] = reasoning
        reasoning["effort"] = value
        if drop_stale:
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
    _record(llm, None)
    return _remove(extra_body, getattr(llm, "api_format", None))


def _remove(extra_body: dict, api_format: Any, *, drop_stale: bool = True) -> bool:
    """:func:`clear_thinking` for an ``extra_body`` and a wire named apart.

    ``drop_stale`` as in :func:`_write`: on ``anthropic-messages`` and
    ``openai-responses``, ``reasoning_effort`` is not the wire's own field.
    """
    was_set, _ = _read(extra_body, api_format)
    stale = drop_stale and "reasoning_effort" in extra_body
    if drop_stale or api_format not in ("anthropic-messages", "openai-responses"):
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


def _record(llm: Any, written: Optional[Tuple[Any, str]]) -> None:
    # A host's client may refuse a new attribute (``__slots__``). Then a wire
    # switch leaves the level where it is, as it leaves a host's own field.
    try:
        setattr(llm, _WRITTEN, written)
    except (AttributeError, TypeError):
        pass


def carry_over_wire_switch(llm: Any, old_format: Any) -> Optional[str]:
    """Move a level :func:`set_thinking` wrote to the field the new wire reads.

    Called after ``llm.api_format`` changed from ``old_format``. Each wire
    rejects the others' fields on every request, so a level left in the old
    field would fail every request. Only a level this module wrote moves,
    and only while the old field still holds it: a field the host set, or
    one it changed since, is the host's and stays. So does a level the host
    already put in the new wire's field: the host's value wins, and agentao's
    is dropped. A level the new wire does not take (``minimal`` on
    ``anthropic-messages``) is dropped too.

    Returns a line for the log, or ``None`` when nothing moved. Never raises:
    a switch must not fail on this.
    """
    written = getattr(llm, _WRITTEN, None)
    new_format = getattr(llm, "api_format", None)
    if new_format == old_format:
        return None
    # Only a record :func:`_record` wrote counts: a duck-typed client may
    # answer any attribute (``MagicMock``), and that must move nothing.
    if not (isinstance(written, tuple) and len(written) == 2
            and isinstance(written[1], str)):
        return None
    written_format, level = written
    extra_body = getattr(llm, "extra_body", None)
    is_set, value = _read(extra_body, old_format)
    if (
        written_format != old_format
        or not isinstance(extra_body, dict)
        or not is_set
        or value != level
    ):
        _record(llm, None)
        return None
    _remove(extra_body, old_format, drop_stale=False)
    host_set, host_value = _read(extra_body, new_format)
    if host_set:
        _record(llm, None)
        return (f"thinking depth {level!r} dropped: the {new_format} field "
                f"already holds the host's {host_value!r}")
    try:
        stored = _write(llm, new_format, level, drop_stale=False)
    except ValueError as exc:
        _record(llm, None)
        return f"thinking depth {level!r} dropped: {new_format} does not take it ({exc})"
    _record(llm, (new_format, stored))
    return f"thinking depth {stored!r} moved to the field {new_format} reads"
