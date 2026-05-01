"""Field-level sanitization and truncation for replay payloads.

Two entry points:

- :func:`sanitize_payload` — v1.0 legacy path: JSON-coerce only, no
  secret scanning, no per-field policy. Kept so older callers and tests
  keep working unchanged.
- :func:`sanitize_event` — v1.1 orchestrator that the recorder calls.
  Applies, in order:

    1. JSON coercion (inherited v1.0 rule: non-serializable values fall
       through ``str()`` instead of aborting the event).
    2. Per-field policy lookup in :data:`FIELD_POLICIES` — one of
       ``ScanOnly`` (default), ``ScanTruncate(n)``, ``Verbatim``, or
       ``Drop``.
    3. The always-on secret scanner from :mod:`.redact` on every string
       value (recursively) unless the field is ``Verbatim`` / ``Drop``.

Sanitization errors must never break the runtime — every callable here
is written to return a best-effort result rather than raise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from .redact import merge_hits, scan_recursive


# Top-level field names :func:`sanitize_event` may inject into the
# cleaned payload as projection metadata. Shared so the JSON schema
# generator and reverse-projection helpers stay in sync with the
# fields actually written here.
SANITIZER_INJECTED_FIELDS: FrozenSet[str] = frozenset(
    {"redacted", "redacted_fields", "redaction_hits"}
)


# ---------------------------------------------------------------------------
# Truncation caps — shared module-level constants so other modules (tests,
# docs, tool_runner) can refer to them symbolically.
# ---------------------------------------------------------------------------

TOOL_OUTPUT_CHUNK_MAX_CHARS = 4_000
TOOL_RESULT_MAX_CHARS = 8_000
ASK_USER_ANSWER_MAX_CHARS = 500
MEMORY_WRITE_MAX_CHARS = 2_000
PLUGIN_HOOK_OUTPUT_MAX_CHARS = 500

_DEFAULT_HEAD_RATIO = 0.5
_TOOL_RESULT_HEAD_RATIO = 0.2  # errors tend to land at the end of a tool result


# ---------------------------------------------------------------------------
# Policy types
# ---------------------------------------------------------------------------


class _Policy:
    """Base marker class for field policies."""

    __slots__ = ()


class ScanOnly(_Policy):
    """Default: run the secret scanner over strings; keep the value otherwise."""

    __slots__ = ()


class ScanTruncate(_Policy):
    """Scan for secrets, then head/tail-truncate strings longer than *max_chars*.

    ``meta_style`` controls how truncation metadata is rendered:

    - ``"flat"`` writes ``truncated`` / ``original_chars`` / ``omitted_chars``
      as top-level keys alongside the truncated string. Preserves the v1.0
      ``tool_output_chunk`` wire shape.
    - ``"nested"`` (default) writes a single nested object under
      ``{field}_truncation`` with the same three keys. Used for any new
      field in v1.1+ so multiple truncated fields in one payload don't
      collide on the top-level names.
    """

    __slots__ = ("max_chars", "meta_style", "head_ratio")

    def __init__(
        self,
        max_chars: int,
        meta_style: str = "nested",
        head_ratio: float = _DEFAULT_HEAD_RATIO,
    ) -> None:
        self.max_chars = int(max_chars)
        self.meta_style = meta_style
        self.head_ratio = float(head_ratio)


class Verbatim(_Policy):
    """Keep the value unchanged. No secret scan. Intended for hashes / ids."""

    __slots__ = ()


class Drop(_Policy):
    """Discard the field entirely; the dropped name surfaces in stats."""

    __slots__ = ()


_SCAN_ONLY = ScanOnly()


# ---------------------------------------------------------------------------
# Field policy map
# ---------------------------------------------------------------------------
#
# Lookup is ``(kind, field_name)`` with ``(kind, "*")`` as a per-kind
# fallback. Any (kind, field) not listed defaults to :data:`_SCAN_ONLY`.
#
# Decision #4 from the design chat: no field-name blacklist. Only the
# explicit entries below override ScanOnly; everything else falls back
# to the scanner, which does its own regex-based redaction.

FIELD_POLICIES: Dict[Tuple[str, str], _Policy] = {
    # v1.0 legacy: tool output chunk keeps its top-level truncation markers.
    ("tool_output_chunk", "chunk"): ScanTruncate(
        TOOL_OUTPUT_CHUNK_MAX_CHARS, meta_style="flat",
    ),
    # v1.1 event fields.
    ("tool_result", "content"): ScanTruncate(
        TOOL_RESULT_MAX_CHARS, head_ratio=_TOOL_RESULT_HEAD_RATIO,
    ),
    ("tool_result", "content_hash"): Verbatim(),
    ("ask_user_answered", "answer"): ScanTruncate(ASK_USER_ANSWER_MAX_CHARS),
    ("memory_write", "value"): ScanTruncate(MEMORY_WRITE_MAX_CHARS),
    ("plugin_hook_fired", "output_preview"): ScanTruncate(PLUGIN_HOOK_OUTPUT_MAX_CHARS),
}


# Deep-capture override: when the named capture_flag is True, the
# corresponding (kind, field) bypasses its ScanTruncate policy. The
# secret scanner still runs — only the length cap is waived. See
# ``replay.capture_flags`` in developer-guide/*/appendix/b-config-keys.md.
FIELD_FULL_CAPTURE_FLAGS: Dict[Tuple[str, str], str] = {
    ("tool_result", "content"): "capture_tool_result_full",
    ("plugin_hook_fired", "output_preview"): "capture_plugin_hook_output_full",
}


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class SanitizeStats:
    """What a sanitize pass did to one event payload.

    ``redaction_hits`` counts pattern matches per kind (from the scanner).
    ``dropped_fields`` lists field names that failed coercion or were
    policy-dropped. ``truncated_fields`` maps the original field name to
    its original character count (so a reader can tell how much was
    omitted without recomputing).
    """

    redaction_hits: Dict[str, int] = field(default_factory=dict)
    dropped_fields: List[str] = field(default_factory=list)
    truncated_fields: Dict[str, int] = field(default_factory=dict)

    def any_activity(self) -> bool:
        return bool(self.redaction_hits or self.dropped_fields or self.truncated_fields)


# ---------------------------------------------------------------------------
# Orchestrator (v1.1)
# ---------------------------------------------------------------------------


def _lookup_policy(
    kind: str,
    field_name: str,
    capture_flags: Optional[Dict[str, bool]] = None,
) -> _Policy:
    flag_name = FIELD_FULL_CAPTURE_FLAGS.get((kind, field_name))
    if flag_name and capture_flags and capture_flags.get(flag_name):
        # Deep-capture opt-in: bypass the ScanTruncate cap for this
        # field. The scanner still runs via ScanOnly.
        return _SCAN_ONLY
    pol = FIELD_POLICIES.get((kind, field_name))
    if pol is not None:
        return pol
    pol = FIELD_POLICIES.get((kind, "*"))
    if pol is not None:
        return pol
    return _SCAN_ONLY


def _truncate_head_tail(s: str, max_chars: int, head_ratio: float) -> Tuple[str, int, int]:
    total = len(s)
    if total <= max_chars:
        return s, total, 0
    head_len = max(1, int(max_chars * head_ratio))
    tail_len = max(1, max_chars - head_len)
    omitted = total - head_len - tail_len
    head = s[:head_len]
    tail = s[-tail_len:]
    return head + "\n\n[… truncated …]\n\n" + tail, total, max(0, omitted)


def _apply_scan_truncate(
    clean: Dict[str, Any],
    field_name: str,
    value: Any,
    policy: ScanTruncate,
    stats: SanitizeStats,
) -> None:
    """Scan the value, truncate if a long string, record metadata.

    ``original_chars`` reports the pre-scan input length so a reader can
    tell how large the raw value was before any redaction. The scanned
    (post-redaction) string is what actually gets truncated and stored,
    which means a long string that shrank because of secret-replacement
    may not need to be truncated at all.
    """
    # Pre-scan length: the "input size" the user/system handed us. This
    # is the value surfaced to replay readers as ``original_chars``.
    pre_scan_len = len(value) if isinstance(value, str) else 0
    scanned, hits = scan_recursive(value)
    if hits:
        stats.redaction_hits = merge_hits(stats.redaction_hits, hits)
    if not isinstance(scanned, str):
        # ScanTruncate on a non-string behaves like ScanOnly — nothing to
        # truncate, but the scanner still ran recursively.
        clean[field_name] = scanned
        return
    # Truncation decision is driven by the pre-scan length so that a
    # reader always sees the raw-input size in ``original_chars``.
    if pre_scan_len <= policy.max_chars:
        clean[field_name] = scanned
        return
    trunc, _scanned_len, omitted = _truncate_head_tail(
        scanned, policy.max_chars, policy.head_ratio,
    )
    clean[field_name] = trunc
    stats.truncated_fields[field_name] = pre_scan_len
    original = pre_scan_len
    if policy.meta_style == "flat":
        # v1.0 chunk-compatible shape. Only valid when at most one
        # truncated field lives on this payload.
        clean["truncated"] = True
        clean["original_chars"] = original
        clean["omitted_chars"] = omitted
    else:
        clean[f"{field_name}_truncation"] = {
            "truncated": True,
            "original_chars": original,
            "omitted_chars": omitted,
        }


def sanitize_event(
    kind: str,
    payload: Any,
    *,
    capture_flags: Optional[Dict[str, bool]] = None,
) -> Tuple[Dict[str, Any], SanitizeStats]:
    """Apply per-field policy + always-on scanner to one replay event payload.

    Always returns a JSON-serializable dict; never raises. Non-dict
    payloads coerce to ``{}``. Fields that fail coercion are listed in
    ``stats.dropped_fields`` (and a ``redacted_fields`` marker is added
    to the payload for reader visibility).

    ``capture_flags`` is the recorder's ``replay.capture_flags`` snapshot.
    When a flag in :data:`FIELD_FULL_CAPTURE_FLAGS` is enabled, the
    corresponding field bypasses its ScanTruncate cap (scanner still runs)
    so deep-capture opt-ins actually produce full payloads.
    """
    stats = SanitizeStats()
    if not isinstance(payload, dict):
        return {}, stats

    clean: Dict[str, Any] = {}
    for raw_key, raw_value in payload.items():
        key = str(raw_key)
        try:
            coerced = _coerce_value(raw_value)
        except Exception:
            stats.dropped_fields.append(key)
            continue
        policy = _lookup_policy(kind, key, capture_flags)
        if isinstance(policy, Drop):
            stats.dropped_fields.append(key)
            continue
        if isinstance(policy, Verbatim):
            clean[key] = coerced
            continue
        if isinstance(policy, ScanTruncate):
            try:
                _apply_scan_truncate(clean, key, coerced, policy, stats)
            except Exception:
                stats.dropped_fields.append(key)
            continue
        # Default / ScanOnly
        scanned, hits = scan_recursive(coerced)
        if hits:
            stats.redaction_hits = merge_hits(stats.redaction_hits, hits)
        clean[key] = scanned

    if stats.dropped_fields:
        clean["redacted"] = "filter_error"
        # De-dup while preserving first-seen order.
        seen = set()
        ordered: List[str] = []
        for name in stats.dropped_fields:
            if name in seen:
                continue
            seen.add(name)
            ordered.append(name)
        clean["redacted_fields"] = ordered
    if stats.redaction_hits:
        # Per-event copy of the counter so a forensic reader can map a
        # redaction back to the exact event that produced it.
        clean["redaction_hits"] = dict(stats.redaction_hits)
    return clean, stats


# ---------------------------------------------------------------------------
# Legacy v1.0 API (kept unchanged for back-compat)
# ---------------------------------------------------------------------------


def sanitize_payload(payload: Any) -> Tuple[Dict[str, Any], List[str]]:
    """Legacy JSON-coerce path. No secret scanning, no per-field policy.

    Retained so callers that don't care about v1.1 event kinds (and the
    original test suite) keep working without change.
    """
    if not isinstance(payload, dict):
        return {}, []
    clean: Dict[str, Any] = {}
    dropped: List[str] = []
    for key, value in payload.items():
        try:
            clean[str(key)] = _coerce_value(value)
        except Exception:
            dropped.append(str(key))
    if dropped:
        clean["redacted"] = "filter_error"
        clean["redacted_fields"] = dropped
    return clean, dropped


def _coerce_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_coerce_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _coerce_value(v) for k, v in value.items()}
    # Fallback: string repr keeps the event without crashing the writer.
    return str(value)


def truncate_tool_output_chunk(
    chunk: str, max_chars: int = TOOL_OUTPUT_CHUNK_MAX_CHARS,
) -> Dict[str, Any]:
    """Legacy helper — head+tail truncate a tool output chunk.

    Pure v1.0 behavior: no secret scanning; scanning now happens inside
    :func:`sanitize_event` when the recorder writes the event. Kept for
    callers that pre-shape the chunk payload before handing it to the
    recorder (``ReplayAdapter._mirror``).
    """
    if not isinstance(chunk, str):
        chunk = str(chunk)
    total = len(chunk)
    if total <= max_chars:
        return {"chunk": chunk}
    head_len = max(1, int(max_chars * _DEFAULT_HEAD_RATIO))
    tail_len = max(1, max_chars - head_len)
    omitted = total - head_len - tail_len
    head = chunk[:head_len]
    tail = chunk[-tail_len:]
    return {
        "chunk": head + "\n\n[… truncated …]\n\n" + tail,
        "truncated": True,
        "original_chars": total,
        "omitted_chars": max(0, omitted),
    }
