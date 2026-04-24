"""Secret-pattern redaction for replay payloads.

Runs a small, ordered set of regexes over every string value that flows
into a replay event. Matches are replaced with ``[REDACTED:<kind>]`` and
a per-kind counter is returned so the recorder can roll the numbers up
into ``replay_footer.redaction_hits``.

This is layer 1 of the replay sanitization pipeline. It never drops a
field (that is layer 2's job in ``sanitize.py``) and never raises — the
recorder swallows any failure path and keeps writing events.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


# Ordered from most specific to most general so a JWT or private-key
# block isn't partially eaten by a later, looser pattern.
SECRET_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    (
        "private_key_block",
        re.compile(
            r"-----BEGIN [A-Z ]+KEY-----[\s\S]+?-----END [A-Z ]+KEY-----"
        ),
    ),
    ("anthropic_api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{40,}")),
    ("openai_api_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}\b")),
    (
        "jwt",
        re.compile(
            r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"
        ),
    ),
    (
        # Two shapes, same ``bearer`` kind: (1) a standalone ``Bearer <tok>``
        # token and (2) an ``Authorization: [Bearer ]<tok>`` header. The
        # original single-pattern form failed to strip the ``Bearer ``
        # literal before the token, so ``Authorization: Bearer <tok>``
        # slipped through unredacted.
        "bearer",
        re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=+/]{20,}"),
    ),
    (
        "bearer",
        re.compile(
            r"(?i)\bauthorization\s*[:=]\s*(?:bearer\s+)?[A-Za-z0-9_\-\.=+/]{20,}"
        ),
    ),
    (
        # Inline key=value / key: value pairs. Uses a negative lookbehind on
        # word chars so "xapi_key=..." doesn't match (false-positive-heavy
        # outside of secret contexts). The quoted value is captured to keep
        # the key visible in the redacted output for readability.
        "kv_secret",
        re.compile(
            r"(?i)(?<![A-Za-z0-9_])(api[_-]?key|token|secret|password|passwd)"
            r"\s*[:=]\s*[\"']?([^\s\"']{8,})[\"']?"
        ),
    ),
]


# Strings shorter than this cannot possibly contain any of the tokens
# above (shortest real match is the ``AKIA...`` 20-char AWS key). Short
# strings skip the regex loop — a cheap win for the many small string
# fields (tool names, statuses, call ids, etc.) that flow through every
# event.
_MIN_SCAN_LEN = 20


def scan_and_redact(text: str) -> Tuple[str, Dict[str, int]]:
    """Return ``(redacted_text, hits_by_kind)`` for *text*.

    ``hits_by_kind`` counts how many times each pattern fired.  An empty
    dict means the string was already clean.
    """
    if not isinstance(text, str) or len(text) < _MIN_SCAN_LEN:
        return text, {}
    hits: Dict[str, int] = {}
    for kind, pattern in SECRET_PATTERNS:
        if pattern.search(text) is None:
            continue

        def _sub(_match, _kind: str = kind) -> str:
            hits[_kind] = hits.get(_kind, 0) + 1
            return f"[REDACTED:{_kind}]"

        text = pattern.sub(_sub, text)
    return text, hits


def merge_hits(*hits_list: Dict[str, int]) -> Dict[str, int]:
    """Sum multiple ``hits_by_kind`` counters into a fresh dict."""
    merged: Dict[str, int] = {}
    for h in hits_list:
        if not h:
            continue
        for k, v in h.items():
            merged[k] = merged.get(k, 0) + int(v)
    return merged


def scan_recursive(value: Any) -> Tuple[Any, Dict[str, int]]:
    """Apply :func:`scan_and_redact` to every string inside *value*.

    Lists, tuples, and dicts are walked recursively.  Non-string leaves
    pass through untouched. The returned value mirrors the shape of the
    input (tuples become lists, matching JSON serialization).
    """
    if isinstance(value, str):
        return scan_and_redact(value)
    if isinstance(value, (list, tuple)):
        out: List[Any] = []
        hits: Dict[str, int] = {}
        for item in value:
            cleaned, h = scan_recursive(item)
            out.append(cleaned)
            if h:
                hits = merge_hits(hits, h)
        return out, hits
    if isinstance(value, dict):
        out_d: Dict[str, Any] = {}
        hits = {}
        for k, v in value.items():
            cleaned, h = scan_recursive(v)
            out_d[str(k)] = cleaned
            if h:
                hits = merge_hits(hits, h)
        return out_d, hits
    return value, {}
