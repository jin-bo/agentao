"""Low-level formatting utilities shared across the replay renderer.

Pure functions, no console / no event-shape knowledge — anything that
takes an event dict belongs in a sibling module instead.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional


def _preview(text: str, limit: int) -> tuple[str, str]:
    if len(text) <= limit:
        return text, ""
    return text[:limit], f"  [dim]…(+{len(text) - limit} chars)[/dim]"


def _json_preview(value: object, limit: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        text = str(value)
    preview, more = _preview(text, limit)
    return preview + (" ..." if more else "")


def _format_ms(value: object) -> str:
    try:
        if value is None:
            return "?ms"
        ms = int(value)
    except (TypeError, ValueError):
        return "?ms"
    if ms >= 1000:
        return f"{ms / 1000:.2f}s"
    return f"{ms}ms"


def _duration_between(start: object, end: object) -> Optional[int]:
    if not start or not end:
        return None
    try:
        start_dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, round((end_dt - start_dt).total_seconds() * 1000))


def _format_ts_local(value: object, *, with_date: bool = True) -> str:
    """Convert a stored ISO-8601 UTC timestamp to the device's local
    timezone for display. Returns the original string verbatim if parsing
    fails so pre-existing replays don't render as empty values.
    """
    if not value:
        return ""
    text = str(value)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    local_dt = dt.astimezone()
    fmt = "%Y-%m-%d %H:%M:%S" if with_date else "%H:%M:%S"
    return local_dt.strftime(fmt)


def _event_counts(events: list) -> dict:
    counts: dict = {}
    for e in events:
        kind = e.get("kind", "")
        counts[kind] = counts.get(kind, 0) + 1
    return counts
