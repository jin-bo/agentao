"""Rendering helpers for ``/replays`` output.

Lifted out of ``agentao/cli/commands.py`` so the core slash-command module
stays focused on dispatch. Everything here is pure formatting over a
dict-shaped replay event stream — no agent state, no side effects beyond
writing to the provided ``Console``.

Layering (each row only depends on rows above):
    _fmt        ← _preview / _json_preview / _format_ms /
                  _duration_between / _format_ts_local / _event_counts
    _summary    ← _summarize_replay_event
    _flags      ← _ShowFlags / _parse_show_flags
    _grouping   ← _group_events_into_turns / _collect_tool_rows
    _banners    ← _print_session_banner / _print_footer_banner
    _turn       ← _print_turn
    _views      ← _render_replay_raw / _render_replay_grouped
"""

from __future__ import annotations

from ._banners import _print_footer_banner, _print_session_banner
from ._flags import _ShowFlags, _parse_show_flags
from ._fmt import (
    _duration_between,
    _event_counts,
    _format_ms,
    _format_ts_local,
    _json_preview,
    _preview,
)
from ._grouping import _collect_tool_rows, _group_events_into_turns
from ._summary import _summarize_replay_event
from ._turn import _print_turn
from ._views import _render_replay_grouped, _render_replay_raw

__all__ = [
    "_ShowFlags",
    "_collect_tool_rows",
    "_duration_between",
    "_event_counts",
    "_format_ms",
    "_format_ts_local",
    "_group_events_into_turns",
    "_json_preview",
    "_parse_show_flags",
    "_preview",
    "_print_footer_banner",
    "_print_session_banner",
    "_print_turn",
    "_render_replay_grouped",
    "_render_replay_raw",
    "_summarize_replay_event",
]
