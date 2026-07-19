"""Secret-pattern redaction for replay payloads.

Runs a small, ordered set of regexes over every string value that flows
into a replay event. Matches are replaced with ``[REDACTED:<kind>]`` and
a per-kind counter is returned so the recorder can roll the numbers up
into ``replay_footer.redaction_hits``.

This is layer 1 of the replay sanitization pipeline. It never drops a
field (that is layer 2's job in ``sanitize.py``) and never raises — the
recorder swallows any failure path and keeps writing events.

The scanner itself now lives in :mod:`agentao.security.secret_scan`: the
same patterns guard ``agentao.log`` and ``.agentao/tool-outputs/``, which
are written whether or not replay is enabled, so the implementation
cannot sit inside this optional subsystem. ``SECRET_PATTERNS`` and
``scan_and_redact`` are re-exported here for existing callers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ..security.secret_scan import SECRET_PATTERNS, scan_and_redact


# Re-exported, not merely imported: existing callers (and tests) import
# these from here, so they are part of this module's surface.
__all__ = ["SECRET_PATTERNS", "scan_and_redact", "merge_hits", "scan_recursive"]


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
