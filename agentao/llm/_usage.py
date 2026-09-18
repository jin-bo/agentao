"""Reading cache counts off a ``response.usage``, whichever wire built it.

Both counts are **parts of** ``prompt_tokens``, never additions to it: the
``anthropic-messages`` adapter folds them in (``_Usage``), and on Chat
Completions ``prompt_tokens_details.cached_tokens`` is a subset by definition.
They exist because a provider bills them at a different rate than the rest of
the input — the totals cannot be turned into a cost without them. agentao
reports the four quantities and stops there; prices are the host's.

Standard library only, and nothing here raises: a usage object is whatever a
provider, a gateway or a test handed back.
"""

from typing import Any, Tuple


def positive_int(value: Any) -> int:
    """``value`` if it is a positive ``int``, else 0. A ``bool`` is an ``int``."""
    ok = isinstance(value, int) and not isinstance(value, bool) and value > 0
    return value if ok else 0


def cache_token_counts(usage: Any) -> Tuple[int, int]:
    """``(cache_read, cache_creation)`` for one response, 0 where not stated.

    Read, in order: Anthropic's own field — which is also what an
    OpenAI-compatible gateway in front of Anthropic tends to pass through —
    then OpenAI's ``prompt_tokens_details.cached_tokens``. The first that is
    stated wins; they are two names for one quantity, so they are never
    summed. Chat Completions has no cache-write count.
    """
    read = positive_int(getattr(usage, "cache_read_input_tokens", None))
    if not read:
        details = getattr(usage, "prompt_tokens_details", None)
        read = positive_int(getattr(details, "cached_tokens", None))
    creation = positive_int(getattr(usage, "cache_creation_input_tokens", None))
    return read, creation
