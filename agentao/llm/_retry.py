"""HTTP-level retry policy for ``LLMClient``.

Owns the policy end-to-end: a fixed retryable-status allowlist,
``Retry-After`` honored when present, otherwise jittered exponential
backoff with a per-step ceiling. Retries are bounded by **count**, as
codex's are: the worst case is four waits of at most
:data:`MAX_BACKOFF_SECONDS` each, plus the attempts themselves.

There was a 60 s wall-clock budget until 0.5.4, and it was measured from
before the first attempt — so it charged the failed request's own
duration to the retries. A request that hung until the SDK's read timeout
had spent the budget by the time it failed, and was never retried, which
is precisely the network failure a retry is for.

Constants live here but are also re-exported from
:mod:`agentao.llm.client` so that ``monkeypatch.setattr(client_mod,
"MAX_RETRY_ATTEMPTS", 2)`` updates the binding the chat-loop
actually reads (Python ``LOAD_GLOBAL`` resolves free variables against
the function's owning module). Tests patch via ``client_mod``; do not
break that contract by moving the reads into this module.
"""

from __future__ import annotations

import importlib
import random
import time
from typing import Any, Optional, Tuple


# OpenAI SDK's built-in retry is disabled (max_retries=0) so this layer owns
# the policy end-to-end. Two layers of retry would otherwise compound (SDK
# default is 2) and ignore Retry-After the way our caller expects.
RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
MAX_RETRY_ATTEMPTS = 5            # total attempts including the first (≤ 4 retries)
# 7.5 → 15 → 30 → 60: the fourth and last retry waits the full ceiling, so
# the four waits span ~2 minutes (~112 s before jitter) — long enough to ride
# out a gateway restart or a network switch, at the cost of 7.5 s before even
# the first retry of a momentary blip.
BASE_BACKOFF_SECONDS = 7.5        # 7.5 * 2^attempt
MAX_BACKOFF_SECONDS = 60.0        # per-step ceiling, Retry-After included
JITTER_FRACTION = 0.3             # uniform(0, base * 0.3) added on top of base

# A 429 that waiting cannot clear: the account is out of quota, credit or
# spend allowance, so every retry is one more request that fails the same way.
# These are OpenAI's error codes, matched exactly, as codex does (#44492); a
# provider that says the same thing some other way is still retried. That is
# the safe side to miss on — an unrecognised code costs the backoff it always
# did, while a wrong match would end a turn on a rate limit it could have
# waited out.
QUOTA_EXHAUSTED_CODES = frozenset({
    "insufficient_quota",
    "credit_balance_exhausted",
    "organization_spend_limit_exceeded",
    "project_spend_limit_exceeded",
    "organization_usage_limit_exceeded",
})



class StreamEndedEarlyError(Exception):
    """A stream that closed before the event its protocol ends every stream with.

    Raised by the wires whose terminal event is mandatory — Anthropic's
    ``message_delta`` with a ``stop_reason``, the Responses API's
    ``response.completed`` / ``.incomplete`` — and only when nothing reached
    the host and the turn was not cancelled. The connection was dropped
    (usually a proxy or gateway closing an idle SSE body) cleanly enough that
    no transport exception says so, and what was accumulated is a fragment:
    an empty answer, or a tool call whose arguments stop mid-JSON. codex
    retries the same condition ("stream closed before response.completed").

    Chat Completions does not raise it: there the field is optional in
    practice, and a missing ``finish_reason`` stays a reported flag.
    """


def _is_dropped_connection(exc: BaseException) -> bool:
    """True for a transport failure raised while a response body is read.

    Before the response arrives, both SDKs wrap an ``httpx`` failure as their
    own ``APIConnectionError`` / ``APITimeoutError``. Once they have handed
    back a stream they do not: ``openai`` 2.x (over ``httpx``) and
    ``anthropic`` (over ``httpx2``) let the raw exception out of the
    iterator — ``RemoteProtocolError: peer closed connection without sending
    complete message body`` is the common one. Measured on openai 2.24.0 /
    anthropic with ``MockTransport``; openai 3.x wraps it instead, which the
    SDK branch already reads.

    The same three families either way — timeouts, network errors, and the
    server breaking the protocol. ``LocalProtocolError``, ``ProxyError`` and
    ``UnsupportedProtocol`` are left out: each says the request or the
    configuration is wrong, and sending it again fails the same way.
    """
    for module_name in ("httpx", "httpx2"):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        families = tuple(
            cls for cls in (
                getattr(module, "TimeoutException", None),
                getattr(module, "NetworkError", None),
                getattr(module, "RemoteProtocolError", None),
            ) if isinstance(cls, type)
        )
        if families and isinstance(exc, families):
            return True
    return False


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
    failures (``APIConnectionError`` / ``APITimeoutError``, and a raw
    transport error or a truncated stream while the body is read —
    :func:`_is_dropped_connection`, :class:`StreamEndedEarlyError`) are
    retryable with no status. ``APIStatusError`` is retryable only when its status is
    in :data:`RETRYABLE_STATUS_CODES`, and a 429 only when it is not a
    quota error (:func:`_is_quota_exhausted`). Anything else (auth,
    validation, non-OpenAI exceptions) is not retryable so the caller
    raises it.
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
        if _is_quota_exhausted(exc):
            return (False, 429, None)
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

    if isinstance(exc, StreamEndedEarlyError) or _is_dropped_connection(exc):
        return (True, None, None)

    return (False, None, None)


def _is_quota_exhausted(exc: BaseException) -> bool:
    """True when a 429 says the account is out of quota, not rate-limited.

    The SDK lifts ``code`` and ``type`` off the response's ``error`` object
    (both 2.x and 3.x). Each is checked to be a string before it is
    compared, so an error object that merely answers the attribute is not
    read as a quota error.
    """
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in QUOTA_EXHAUSTED_CODES:
        return True
    error_type = getattr(exc, "type", None)
    return isinstance(error_type, str) and error_type == "insufficient_quota"


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


def _is_image_unsupported(err_str: str) -> bool:
    """True when an error says the model rejects image / vision input."""
    s = err_str.lower()
    provider_multimodal_rejections = (
        "unexpected item type in content" in s
        or ("unknown variant image_url" in s and "expected text" in s)
    )
    if provider_multimodal_rejections:
        return True
    image_mentioned = (
        "image_url" in s
        or "image input" in s
        or "image inputs" in s
        or "images" in s
        or "vision" in s
        or "multimodal" in s
    )
    if not image_mentioned:
        return False
    return (
        "does not support" in s
        or "do not support" in s
        or "not supported" in s
        or "unsupported" in s
        or "only supported" in s
        or "invalid content type" in s
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

    The token is read **before** the deadline on every pass, including the
    first: a ``False`` here is what stops the caller sending another request,
    so a zero delay (``Retry-After: 0``) and a cancel landing in the last
    slice must both still report it. Checking the deadline first let either
    one through as ``True``, and the retry loop went on to re-send a request
    the user had already cancelled.
    """
    if cancellation_token is None:
        if delay > 0:
            time.sleep(delay)
        return True
    deadline = time.monotonic() + delay
    while True:
        if getattr(cancellation_token, "is_cancelled", False):
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
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
