"""Reading cache and reasoning counts off a ``response.usage``, whichever wire built it.

Both cache counts are **parts of** ``prompt_tokens``, never additions to it: the
``anthropic-messages`` adapter folds them in (``_Usage``), and on Chat
Completions ``prompt_tokens_details.cached_tokens`` is a subset by definition.
They exist because a provider bills them at a different rate than the rest of
the input — the totals cannot be turned into a cost without them. agentao
reports the counts and stops there; prices are the host's.

The reasoning count is likewise a **part of** ``completion_tokens``: the output
the model spent thinking, which a thinking-mode provider bills as output but
never shows. Only the OpenAI-shaped wires state it. Anthropic's
``output_tokens`` includes thinking with no separate count, so there it is 0.

Standard library only, and nothing here raises: a usage object is whatever a
provider, a gateway or a test handed back.
"""

import inspect
from typing import Any, Dict, Tuple


#: The five session totals, by the names ``LLMClient.usage_snapshot()``,
#: ``agentao run``'s ``usage`` and ``SubagentUsage`` all use.
USAGE_KEYS = (
    "prompt_tokens", "completion_tokens", "cache_read_tokens",
    "cache_creation_tokens", "reasoning_tokens",
)


def positive_int(value: Any) -> int:
    """``value`` if it is a positive ``int``, else 0. A ``bool`` is an ``int``."""
    ok = isinstance(value, int) and not isinstance(value, bool) and value > 0
    return value if ok else 0


def usage_totals(counts: Any, llm: Any) -> Dict[str, int]:
    """The five totals from a ``usage_snapshot()`` result, attributes filling gaps.

    A key the snapshot lacks (a host's from before ``reasoning_tokens``), or
    a snapshot that is not a dict, is read from ``llm.total_<key>``, so a
    count the client keeps is not lost. Each value goes through
    :func:`positive_int`, because ``llm`` may be a mock that answers any
    attribute.
    """
    if not isinstance(counts, dict):
        counts = {}
    return {
        key: positive_int(counts[key] if key in counts else getattr(llm, f"total_{key}", None))
        for key in USAGE_KEYS
    }


def cache_token_counts(usage: Any) -> Tuple[int, int]:
    """``(cache_read, cache_creation)`` for one response, 0 where not stated.

    Read, in order: Anthropic's own field — which is also what an
    OpenAI-compatible gateway in front of Anthropic tends to pass through —
    then OpenAI's ``prompt_tokens_details.cached_tokens``. The first that is
    stated wins; they are two names for one quantity, so they are never
    summed. The write count is read the same way: Anthropic's field, then
    ``prompt_tokens_details.cache_write_tokens`` — which the ``openai`` SDK
    states from 3.x on, for Chat Completions and the Responses API both, and
    which an older SDK or a gateway simply does not have.
    """
    read = positive_int(getattr(usage, "cache_read_input_tokens", None))
    if not read:
        details = getattr(usage, "prompt_tokens_details", None)
        read = positive_int(getattr(details, "cached_tokens", None))
    creation = positive_int(getattr(usage, "cache_creation_input_tokens", None))
    if not creation:
        details = getattr(usage, "prompt_tokens_details", None)
        creation = positive_int(getattr(details, "cache_write_tokens", None))
    return read, creation


def reasoning_token_count(usage: Any) -> int:
    """The reasoning part of ``completion_tokens``, 0 where not stated.

    ``completion_tokens_details.reasoning_tokens``: what Chat Completions
    states, and where the ``openai-responses`` adapter's usage puts the
    Responses API's ``output_tokens_details.reasoning_tokens``.
    """
    details = getattr(usage, "completion_tokens_details", None)
    return positive_int(getattr(details, "reasoning_tokens", None))


def accepts_keyword(func: Any, name: str) -> bool:
    """Whether ``func`` takes ``name=`` (or ``**kwargs``). False when unknown.

    For a usage add or sink written before a count's keyword existed: passing
    the keyword to it would raise ``TypeError`` and lose the whole roll-up,
    prompt and completion counts included. An un-introspectable callable is
    assumed to predate the keyword, as ``transport/sdk.py`` assumes a legacy
    callback.
    """
    try:
        params = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        p.kind is p.VAR_KEYWORD
        or (p.name == name and p.kind in (p.KEYWORD_ONLY, p.POSITIONAL_OR_KEYWORD))
        for p in params
    )
