"""HTTP-level retry policy for ``LLMClient``.

Owns the policy end-to-end: a fixed retryable-status allowlist,
``Retry-After`` honored when present, otherwise jittered exponential
backoff with a per-step ceiling and a global wall-clock budget so a
misbehaving provider can't pin the caller forever.

Constants live here but are also re-exported from
:mod:`agentao.llm.client` so that ``monkeypatch.setattr(client_mod,
"MAX_TOTAL_RETRY_SECONDS", 1.0)`` updates the binding the chat-loop
actually reads (Python ``LOAD_GLOBAL`` resolves free variables against
the function's owning module). Tests patch via ``client_mod``; do not
break that contract by moving the reads into this module.
"""

from __future__ import annotations

import random
import time
from typing import Any, Optional, Tuple


# OpenAI SDK's built-in retry is disabled (max_retries=0) so this layer owns
# the policy end-to-end. Two layers of retry would otherwise compound (SDK
# default is 2) and ignore Retry-After the way our caller expects.
RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
MAX_RETRY_ATTEMPTS = 5            # total attempts including the first (≤ 4 retries)
BASE_BACKOFF_SECONDS = 1.5        # 1.5 * 2^attempt
MAX_BACKOFF_SECONDS = 30.0        # per-step ceiling
MAX_TOTAL_RETRY_SECONDS = 60.0    # wall-clock budget across all attempts
JITTER_FRACTION = 0.3             # uniform(0, base * 0.3) added on top of base


# Phrases providers actually use when they reject ``stream=True``. We match
# against full phrases (not bare "stream"/"streaming") so that proxy errors
# whose messages contain words like "upstream" — common in 502/503 — don't
# accidentally trigger the non-streaming fallback and bypass the retry
# policy that would have honored ``Retry-After``.
_STREAMING_UNSUPPORTED_PHRASES = (
    "does not support streaming",
    "does not support stream",
    "streaming is not supported",
    "stream is not supported",
    "streaming not supported",
    "stream not supported",
    "stream=true is not supported",
    "stream=true not supported",
    "streaming mode is not supported",
)


def _classify_retry(exc: BaseException) -> Tuple[bool, Optional[int], Optional[str]]:
    """Decide whether ``exc`` is worth retrying.

    Returns ``(retryable, status_code, retry_after_header)``. Network-level
    failures (``APIConnectionError`` / ``APITimeoutError``) are retryable
    with no status. ``APIStatusError`` is retryable only when its status is
    in :data:`RETRYABLE_STATUS_CODES`. Anything else (auth, validation,
    non-OpenAI exceptions) is not retryable so the caller raises it.
    """
    try:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            RateLimitError,
        )
    except ImportError:
        return (False, None, None)

    if isinstance(exc, RateLimitError):
        retry_after = None
        if getattr(exc, "response", None) is not None:
            retry_after = exc.response.headers.get("retry-after")
        return (True, 429, retry_after)

    if isinstance(exc, APIStatusError):
        status = getattr(exc, "status_code", None)
        if status in RETRYABLE_STATUS_CODES:
            retry_after = None
            if getattr(exc, "response", None) is not None:
                retry_after = exc.response.headers.get("retry-after")
            return (True, status, retry_after)
        return (False, status, None)

    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return (True, None, None)

    return (False, None, None)


def _parse_retry_after(header: Optional[str]) -> Optional[float]:
    """Parse a ``Retry-After`` header (seconds or HTTP-date) into seconds."""
    if not header:
        return None
    try:
        return max(0.0, float(header))
    except (TypeError, ValueError):
        pass
    try:
        from datetime import datetime, timezone
        from email.utils import parsedate_to_datetime
        target = parsedate_to_datetime(header)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        delta = (target - datetime.now(timezone.utc)).total_seconds()
        if delta > 0:
            return delta
    except (TypeError, ValueError):
        pass
    return None


def _is_streaming_unsupported(err_str: str) -> bool:
    """True when an error message explicitly says streaming is unsupported."""
    return any(phrase in err_str for phrase in _STREAMING_UNSUPPORTED_PHRASES)


def _is_temperature_unsupported(err_str: str) -> bool:
    """True when an error says the model rejects the ``temperature`` param.

    Reasoning models (o1/o3/gpt-5, …) return messages like
    ``Unsupported value: 'temperature' does not support 0.2 with this model``,
    ``'temperature' is not supported with this model``, or
    ``temperature is deprecated for this model``. Require the param name *and*
    a rejection indicator so a generic 400 that merely mentions temperature
    does not trip the fix-up.
    """
    s = err_str.lower()
    if "temperature" not in s:
        return False
    return (
        "does not support" in s
        or "not supported" in s
        or "unsupported" in s
        or "deprecated" in s
    )


def _compute_backoff_delay(attempt: int, retry_after_header: Optional[str] = None) -> float:
    """Compute the next sleep duration. Honors ``Retry-After`` when present."""
    parsed = _parse_retry_after(retry_after_header)
    if parsed is not None:
        return min(parsed, MAX_BACKOFF_SECONDS)
    base = min(BASE_BACKOFF_SECONDS * (2 ** attempt), MAX_BACKOFF_SECONDS)
    jitter = base * JITTER_FRACTION * random.random()
    return min(base + jitter, MAX_BACKOFF_SECONDS)


def _interruptible_sleep(delay: float, cancellation_token: Optional[Any] = None) -> bool:
    """Sleep up to ``delay`` seconds; return False if cancelled mid-sleep.

    Polls ``cancellation_token.is_cancelled`` every 100ms so that a Ctrl+C
    or ACP cancel during a long ``Retry-After`` window doesn't strand the
    user. With no token this is a plain ``time.sleep``.
    """
    if delay <= 0:
        return True
    if cancellation_token is None:
        time.sleep(delay)
        return True
    deadline = time.monotonic() + delay
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        if getattr(cancellation_token, "is_cancelled", False):
            return False
        time.sleep(min(0.1, remaining))


def _mark_streamed(exc: BaseException, value: bool) -> None:
    """Tag ``exc`` with ``.streamed`` for ``runtime/llm_call.py`` to read.

    Best-effort: SDK exception classes that pin ``__slots__`` will raise on
    assignment, in which case the host falls back to counting ``LLM_TEXT``
    events. Mirrors the same defensive pattern used in
    ``runtime/tool_planning.py`` for foreign-object mutation.
    """
    try:
        exc.streamed = value  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass
