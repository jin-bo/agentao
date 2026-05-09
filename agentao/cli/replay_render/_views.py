"""Top-level entry points: ``--raw`` flat view and the grouped per-turn view."""

from __future__ import annotations

from typing import Optional

from rich.markup import escape as markup_escape

from ._banners import _print_footer_banner, _print_session_banner
from ._fmt import _format_ts_local
from ._grouping import _group_events_into_turns
from ._summary import _summarize_replay_event
from ._turn import _print_turn


def _render_replay_raw(events: list, meta, console_) -> None:
    """Flat one-line-per-event view (legacy show behavior)."""
    console_.print(
        f"\n[info]{meta.full_id}[/info]  "
        f"[dim]({len(events)} event(s))[/dim]\n"
    )
    for event in events:
        seq = event.get("seq")
        kind = event.get("kind", "")
        ts = _format_ts_local(event.get("ts", ""))
        tid = (event.get("turn_id") or "")[:6]
        summary = _summarize_replay_event(event)
        turn_tag = f" [dim]turn={tid}[/dim]" if tid else ""
        console_.print(
            f"  [dim]#{seq:>4}[/dim]  [cyan]{kind:<28}[/cyan]"
            f"{turn_tag}  [dim]{markup_escape(ts)}[/dim]"
        )
        if summary:
            console_.print(f"    {summary}")


def _render_replay_grouped(
    events: list,
    meta,
    console_,
    *,
    turn_filter: Optional[str] = None,
    errors_only: bool = False,
) -> None:
    """Grouped view: session header + turn-by-turn narrative + footer."""
    header = next((e for e in events if e.get("kind") == "replay_header"), None)
    footer = next((e for e in events if e.get("kind") == "replay_footer"), None)
    session_started = next(
        (e for e in events if e.get("kind") == "session_started"), None,
    )
    turns, top_level = _group_events_into_turns(events)

    _print_session_banner(meta, header, session_started, footer, console_)

    # Non-turn events (session_started / session_ended / errors before any
    # turn / session_loaded / model_changed at session scope). Print once
    # so the reader sees session-level activity.
    if top_level and not turn_filter:
        console_.print("[info]Session-level events:[/info]")
        for e in top_level:
            summary = _summarize_replay_event(e)
            console_.print(
                f"  [cyan]{e.get('kind', ''):<28}[/cyan]  {summary}"
            )
        console_.print()

    for turn in turns:
        if turn_filter and not turn["id"].startswith(turn_filter):
            continue
        if errors_only and not turn["has_error"]:
            continue
        _print_turn(turn, console_)
    shown_turns = [
        turn for turn in turns
        if (not turn_filter or turn["id"].startswith(turn_filter))
        and (not errors_only or turn["has_error"])
    ]
    if not shown_turns:
        active = []
        if turn_filter:
            active.append(f"turn={turn_filter}")
        if errors_only:
            active.append("errors")
        filter_text = f" ({', '.join(active)})" if active else ""
        console_.print(f"[warning]No turns matched{markup_escape(filter_text)}.[/warning]\n")

    if footer:
        _print_footer_banner(footer, console_)

    console_.print()
