"""Rendering helpers for ``/replays`` output.

Lifted out of ``agentao/cli/commands.py`` so the core slash-command module
stays focused on dispatch. Everything here is pure formatting over a
dict-shaped replay event stream — no agent state, no side effects beyond
writing to the provided ``Console``.

Layering:
    _summarize_replay_event  ← one-line summary per event kind
    _preview / _json_preview / _format_ms / _duration_between
    _format_ts_local / _event_counts   ← low-level utilities
    _ShowFlags / _parse_show_flags     ← flag parsing for /replays show
    _group_events_into_turns           ← partitions events into turns
    _collect_tool_rows                 ← aggregates TOOL_* events per call_id
    _render_replay_raw                 ← flat one-line-per-event view
    _render_replay_grouped → _print_session_banner / _print_turn /
                             _print_footer_banner
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from rich.markup import escape as markup_escape


def _summarize_replay_event(event: dict) -> str:
    """Return a short one-line summary for a replay event in /replays show.

    Unknown event kinds fall through to a generic payload-keys preview
    so a v1.2 file still renders against a v1.1 reader without a crash.
    """
    kind = event.get("kind")
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        return ""
    if kind == "replay_header":
        cf = payload.get("capture_flags") or {}
        on = [k for k, v in cf.items() if v]
        flags_part = (
            f", capture=[{markup_escape(','.join(sorted(on)))}]" if on else ""
        )
        return (
            f"[dim]schema={markup_escape(str(payload.get('schema_version', '')))}, "
            f"session={markup_escape(str(payload.get('session_id', ''))[:8])}, "
            f"instance={markup_escape(str(payload.get('instance_id', '')))}"
            f"{flags_part}[/dim]"
        )
    if kind == "replay_footer":
        hits = payload.get("redaction_hits") or {}
        total_hits = sum(int(v) for v in hits.values()) if hits else 0
        trunc = payload.get("truncated_field_hits") or {}
        dropped = payload.get("dropped_field_hits") or {}
        bits = [f"events={payload.get('event_count', 0)}"]
        if total_hits:
            bits.append(f"redactions={total_hits}")
        if trunc:
            bits.append(f"truncated_fields={sum(trunc.values())}")
        if dropped:
            bits.append(f"dropped_fields={sum(dropped.values())}")
        return f"[dim]{markup_escape(', '.join(bits))}[/dim]"
    if kind == "session_started":
        return (
            f"[dim]cwd={markup_escape(str(payload.get('cwd', '')))} "
            f"model={markup_escape(str(payload.get('model', '')))}[/dim]"
        )
    if kind == "user_message":
        text = str(payload.get("content", ""))
        return f"[dim]{markup_escape(text[:120])}[/dim]" + ("…" if len(text) > 120 else "")
    if kind == "assistant_text_chunk":
        chunk = str(payload.get("chunk", ""))
        return f"[dim]{markup_escape(chunk[:120])}[/dim]"
    if kind == "assistant_thought_chunk":
        text = str(payload.get("text", ""))
        return f"[dim]{markup_escape(text[:120])}[/dim]"
    if kind == "tool_started":
        return (
            f"[green]{markup_escape(str(payload.get('tool', '')))}[/green] "
            f"[dim]source={markup_escape(str(payload.get('tool_source', '')))}[/dim]"
        )
    if kind == "tool_output_chunk":
        trunc = " [warning](truncated)[/warning]" if payload.get("truncated") else ""
        return f"[dim]{markup_escape(str(payload.get('tool', '')))}[/dim]{trunc}"
    if kind == "tool_completed":
        status = payload.get("status")
        color = "green" if status == "ok" else "warning"
        return (
            f"[{color}]{status}[/{color}] "
            f"[dim]{markup_escape(str(payload.get('tool', '')))} in {payload.get('duration_ms')}ms[/dim]"
        )
    if kind == "tool_result":
        trunc = (payload.get("content_truncation") or {}).get("truncated")
        saved = payload.get("saved_to_disk")
        chars = payload.get("original_chars")
        bits = [f"{markup_escape(str(payload.get('tool', '')))}"]
        if chars is not None:
            bits.append(f"{chars:,} chars")
        if trunc:
            bits.append("truncated")
        if saved:
            bits.append("saved_to_disk")
        return f"[dim]{markup_escape(', '.join(bits))}[/dim]"
    if kind == "tool_confirmation_requested":
        return f"[dim]ask: {markup_escape(str(payload.get('tool', '')))}[/dim]"
    if kind == "tool_confirmation_resolved":
        return (
            f"[dim]tool={markup_escape(str(payload.get('tool', '')))} approved="
            f"{payload.get('approved')}[/dim]"
        )
    if kind == "subagent_started":
        task = str(payload.get("task", ""))
        return (
            f"[dim]agent={markup_escape(str(payload.get('agent', '')))} "
            f"task={markup_escape(task[:80])}[/dim]"
        )
    if kind == "subagent_completed":
        return (
            f"[dim]agent={markup_escape(str(payload.get('agent', '')))} "
            f"state={markup_escape(str(payload.get('state', '')))} turns={payload.get('turns')}[/dim]"
        )
    if kind == "turn_completed":
        final = str(payload.get("final_text", ""))
        status = payload.get("status", "ok")
        color = "green" if status == "ok" else "yellow"
        return (
            f"[{color}]{status}[/{color}]  "
            f"[green]{markup_escape(final)}[/green]"
        )
    if kind == "error":
        return f"[error]{markup_escape(str(payload.get('message', '')))}[/error]"
    # --- v1.1 runtime observability ---
    if kind == "llm_call_started":
        return (
            f"[dim]attempt={payload.get('attempt')} model="
            f"{markup_escape(str(payload.get('model', '')))} "
            f"msgs={payload.get('n_messages')} tools={payload.get('tool_count')}[/dim]"
        )
    if kind == "llm_call_completed":
        status = payload.get("status", "ok")
        color = "green" if status == "ok" else "error"
        usage = []
        if payload.get("prompt_tokens") is not None:
            usage.append(f"p={payload['prompt_tokens']}")
        if payload.get("completion_tokens") is not None:
            usage.append(f"c={payload['completion_tokens']}")
        return (
            f"[{color}]{status}[/{color}] [dim]"
            f"finish={markup_escape(str(payload.get('finish_reason')))} "
            f"{payload.get('duration_ms')}ms "
            f"{markup_escape(' '.join(usage))}[/dim]"
        )
    if kind == "llm_call_delta":
        return (
            f"[dim]+{len(payload.get('added_messages') or [])} msg(s) "
            f"(total={payload.get('total_messages')})[/dim]"
        )
    if kind == "llm_call_io":
        return f"[dim]deep-capture: full_messages + tools[/dim]"
    if kind == "ask_user_requested":
        q = str(payload.get("question", ""))
        return f"[dim]{markup_escape(q[:120])}[/dim]"
    if kind == "ask_user_answered":
        ans = str(payload.get("answer", ""))
        return f"[dim]answer: {markup_escape(ans[:120])}[/dim]"
    if kind == "background_notification_injected":
        return f"[dim]note_count={payload.get('note_count')}[/dim]"
    if kind == "context_compressed":
        t = payload.get("type", "?")
        return (
            f"[dim]{markup_escape(t)} "
            f"msgs={payload.get('pre_msgs')}→{payload.get('post_msgs')} "
            f"tok={payload.get('pre_est_tokens')}→{payload.get('post_est_tokens')}"
            f"[/dim]"
        )
    if kind == "session_summary_written":
        return (
            f"[dim]id={markup_escape(str(payload.get('summary_id', ''))[:8])} "
            f"msgs={payload.get('messages_summarized')} "
            f"size={payload.get('summary_size')}b[/dim]"
        )
    if kind in ("skill_activated", "skill_deactivated"):
        return f"[dim]{markup_escape(str(payload.get('skill', '')))}[/dim]"
    if kind == "memory_write":
        return (
            f"[dim]v{payload.get('version_before')}→v{payload.get('version_after')} "
            f"entries={payload.get('total_entries')}[/dim]"
        )
    if kind == "memory_delete":
        return (
            f"[dim]key={markup_escape(str(payload.get('key', '')))} "
            f"count={payload.get('deleted_count')}[/dim]"
        )
    if kind == "memory_cleared":
        return (
            f"[dim]memories={payload.get('memories_cleared')} "
            f"summaries={payload.get('session_summaries_cleared')}[/dim]"
        )
    if kind == "model_changed":
        return (
            f"[dim]{markup_escape(str(payload.get('old_model', '')))} → "
            f"{markup_escape(str(payload.get('new_model', '')))} "
            f"({markup_escape(str(payload.get('cause', '')))})[/dim]"
        )
    if kind in ("permission_mode_changed", "readonly_mode_changed"):
        return (
            f"[dim]{markup_escape(str(payload.get('previous')))} → "
            f"{markup_escape(str(payload.get('current')))}[/dim]"
        )
    if kind == "plugin_hook_fired":
        outcome = str(payload.get("outcome", "allow"))
        color = {"block": "error", "stop": "warning", "modify": "yellow"}.get(outcome, "green")
        return (
            f"[{color}]{outcome}[/{color}] "
            f"[dim]{markup_escape(str(payload.get('hook_name', '')))} "
            f"rules={payload.get('rule_count')}[/dim]"
        )
    # Unknown kind — preview payload keys rather than a crash or a blank.
    preview_keys = ", ".join(sorted(str(k) for k in payload.keys())[:5])
    return f"[dim]{markup_escape(preview_keys)}[/dim]"


# ---------------------------------------------------------------------------
# /replays show v2 — grouped view
# ---------------------------------------------------------------------------


class _ShowFlags:
    """Parsed flags for /replays show / tail."""

    __slots__ = ("raw", "turn", "kind", "errors", "rest")

    def __init__(self) -> None:
        self.raw: bool = False
        self.turn: Optional[str] = None
        self.kind: Optional[str] = None
        self.errors: bool = False
        self.rest: list = []


def _parse_show_flags(tokens: list) -> _ShowFlags:
    """Parse the tokens after ``<id>`` into a :class:`_ShowFlags`.

    Accepts both ``--flag value`` and ``--flag=value`` shapes. Unknown
    tokens land in ``flags.rest`` so ``tail`` can still consume its
    numeric argument.
    """
    flags = _ShowFlags()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--raw":
            flags.raw = True
            i += 1
        elif tok.startswith("--turn="):
            flags.turn = tok.split("=", 1)[1]
            i += 1
        elif tok == "--turn" and i + 1 < len(tokens):
            flags.turn = tokens[i + 1]
            i += 2
        elif tok.startswith("--kind="):
            flags.kind = tok.split("=", 1)[1]
            i += 1
        elif tok == "--kind" and i + 1 < len(tokens):
            flags.kind = tokens[i + 1]
            i += 2
        elif tok == "--errors":
            flags.errors = True
            i += 1
        else:
            flags.rest.append(tok)
            i += 1
    return flags


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


def _group_events_into_turns(events: list) -> tuple:
    """Return ``(turns, top_level_events)`` partition.

    Events without a ``turn_id`` (``replay_header``, ``replay_footer``,
    ``session_started``, ``session_ended``, session-scope state changes)
    land in ``top_level_events``.  Everything else is grouped by
    ``turn_id`` preserving insertion order.
    """
    top_level: list = []
    turns: dict = {}
    order: list = []
    for e in events:
        tid = e.get("turn_id")
        kind = e.get("kind", "")
        if not tid or kind in ("replay_header", "replay_footer"):
            if kind not in ("replay_header", "replay_footer"):
                top_level.append(e)
            continue
        if tid not in turns:
            turns[tid] = {
                "id": tid,
                "parent_id": e.get("parent_turn_id"),
                "events": [],
                "has_error": False,
                "start_ts": e.get("ts"),
                "end_ts": e.get("ts"),
            }
            order.append(tid)
        turn = turns[tid]
        turn["events"].append(e)
        turn["end_ts"] = e.get("ts") or turn["end_ts"]
        if kind == "error":
            turn["has_error"] = True
        elif kind == "turn_completed":
            status = (e.get("payload") or {}).get("status")
            if status in ("error", "cancelled"):
                turn["has_error"] = True
        elif kind in ("tool_completed", "llm_call_completed"):
            status = (e.get("payload") or {}).get("status")
            if status in ("error", "cancelled"):
                turn["has_error"] = True
    return [turns[tid] for tid in order], top_level


def _print_session_banner(meta, header, session_started, footer, console_) -> None:
    console_.print(f"\n[info]━━━ Replay {meta.full_id} ━━━[/info]")
    console_.print(
        f"[dim]file={markup_escape(str(meta.path))}  "
        f"updated={markup_escape(_format_ts_local(meta.updated_at))}  "
        f"events={meta.event_count}  turns={meta.turn_count}[/dim]"
    )
    if meta.malformed_lines:
        console_.print(
            f"[warning]malformed_lines={meta.malformed_lines} "
            "skipped while reading[/warning]"
        )
    if header:
        hp = header.get("payload") or {}
        flags = hp.get("capture_flags") or {}
        on = sorted(k for k, v in flags.items() if v)
        deep = [k for k in on if k != "capture_llm_delta"]
        flag_str = ", ".join(on) if on else "—"
        deep_badge = (
            f"  [warning]⚠ deep-capture[/warning]" if deep else ""
        )
        console_.print(
            f"[dim]schema={hp.get('schema_version')}  "
            f"created={markup_escape(_format_ts_local(hp.get('created_at')))}  "
            f"flags=[{markup_escape(flag_str)}][/dim]{deep_badge}"
        )
    if session_started:
        sp = session_started.get("payload") or {}
        console_.print(
            f"[dim]cwd={markup_escape(str(sp.get('cwd', '')))}  "
            f"model={markup_escape(str(sp.get('model', '')))}[/dim]"
        )
    if footer:
        fp = footer.get("payload") or {}
        hits = fp.get("redaction_hits") or {}
        if hits:
            parts = ", ".join(f"{k}={v}" for k, v in sorted(hits.items()))
            console_.print(f"[warning]redactions: {markup_escape(parts)}[/warning]")
    console_.print()


def _print_footer_banner(footer, console_) -> None:
    fp = footer.get("payload") or {}
    console_.print("[info]━━━ End of replay ━━━[/info]")
    bits = [f"events={fp.get('event_count', 0)}"]
    hits = fp.get("redaction_hits") or {}
    if hits:
        bits.append(
            "redactions=" + "+".join(
                f"{k}:{v}" for k, v in sorted(hits.items())
            )
        )
    trunc = fp.get("truncated_field_hits") or {}
    if trunc:
        bits.append(
            "truncated=" + "+".join(
                f"{k}:{v}" for k, v in sorted(trunc.items())
            )
        )
    dropped = fp.get("dropped_field_hits") or {}
    if dropped:
        bits.append(
            "dropped=" + "+".join(
                f"{k}:{v}" for k, v in sorted(dropped.items())
            )
        )
    console_.print(f"[dim]{markup_escape(', '.join(bits))}[/dim]")


def _print_turn(turn: dict, console_) -> None:
    """Default turn render: user message → thinking → tools table → final."""
    events = turn["events"]
    tid = turn["id"][:6]
    header_color = "error" if turn["has_error"] else "info"
    err_tag = "  [error]⚠ error[/error]" if turn["has_error"] else ""
    seqs = [e.get("seq") for e in events if isinstance(e.get("seq"), int)]
    seq_part = f"seq={min(seqs)}..{max(seqs)}" if seqs else "seq=?"
    duration = _duration_between(turn.get("start_ts"), turn.get("end_ts"))
    parent = turn.get("parent_id")
    parent_part = f"  parent={parent[:6]}" if parent else ""
    counts = _event_counts(events)
    count_part = ", ".join(
        f"{name}={counts[name]}"
        for name in ("llm_call_completed", "tool_started", "error")
        if counts.get(name)
    )
    count_part = f"  {count_part}" if count_part else ""
    start_local = _format_ts_local(turn.get("start_ts"))
    end_local = _format_ts_local(turn.get("end_ts"), with_date=False)
    console_.print(
        f"[{header_color}]━ Turn {tid} ━[/{header_color}]{err_tag} "
        f"[dim]{seq_part}  {markup_escape(start_local)}"
        f" → {markup_escape(end_local)}  "
        f"{_format_ms(duration)}{markup_escape(parent_part)}{count_part}[/dim]"
    )

    # User message (first user_message wins — only one per turn by design)
    user_msg = next(
        (e for e in events if e.get("kind") == "user_message"), None,
    )
    if user_msg:
        text = str((user_msg.get("payload") or {}).get("content", ""))
        preview, more = _preview(text, 400)
        console_.print(
            f"  [bold reverse cyan] user [/bold reverse cyan]  "
            f"{markup_escape(preview)}{more} "
            f"[dim]({len(text)} chars)[/dim]"
        )

    # Aggregated thinking chunks
    thinking = "".join(
        str((e.get("payload") or {}).get("text", ""))
        for e in events
        if e.get("kind") == "assistant_thought_chunk"
    )
    if thinking:
        preview, more = _preview(thinking, 400)
        console_.print(
            f"  [bold reverse magenta] think [/bold reverse magenta]  "
            f"[dim]{markup_escape(preview)}{more} "
            f"({len(thinking)} chars)[/dim]"
        )

    # Tool calls → one row per call_id
    tool_rows = _collect_tool_rows(events)
    if tool_rows:
        console_.print(
            f"  [bold]tools[/bold]  [dim]{len(tool_rows)} call(s)[/dim]"
        )
        for row in tool_rows:
            status = row.get("status") or "?"
            color = {
                "ok": "green",
                "error": "error",
                "cancelled": "warning",
            }.get(status, "yellow")
            extras = []
            if row.get("truncated"):
                extras.append("truncated")
            if row.get("output_truncated"):
                extras.append("stream-truncated")
            if row.get("saved_to_disk"):
                extras.append("saved")
            if row.get("confirmation") is False:
                extras.append("denied")
            if row.get("confirmation") is True:
                extras.append("approved")
            if row.get("original_chars") is not None:
                extras.append(f"{int(row['original_chars']):,} chars")
            if row.get("error"):
                extras.append(f"err={str(row['error'])[:40]}")
            extras_str = f"  [dim]({', '.join(extras)})[/dim]" if extras else ""
            console_.print(
                f"    [{color}]●[/{color}] "
                f"[cyan]{markup_escape(row.get('name') or '?'):<28}[/cyan] "
                f"[dim]{_format_ms(row.get('duration_ms'))}  "
                f"id={markup_escape(str(row.get('call_id', ''))[:8])}  "
                f"source={markup_escape(str(row.get('source') or '?'))}[/dim]"
                f"{extras_str}"
            )
            args_preview = row.get("args_preview")
            if args_preview:
                console_.print(f"      [dim]args[/dim] {markup_escape(args_preview)}")
            result_bits = []
            if row.get("content_hash"):
                result_bits.append(f"sha256={str(row['content_hash'])[:12]}")
            if row.get("disk_path"):
                result_bits.append(f"disk={row['disk_path']}")
            if result_bits:
                console_.print(
                    f"      [dim]result[/dim] "
                    f"{markup_escape('  '.join(result_bits))}"
                )

    confirmations = [
        e for e in events
        if e.get("kind") in (
            "tool_confirmation_requested",
            "tool_confirmation_resolved",
        )
    ]
    if confirmations:
        console_.print(f"  [bold]confirm[/bold] [dim]{len(confirmations)} event(s)[/dim]")
        for e in confirmations:
            p = e.get("payload") or {}
            if e.get("kind") == "tool_confirmation_requested":
                args_preview = _json_preview(p.get("args", {}), 180)
                console_.print(
                    f"    [yellow]?[/yellow] "
                    f"[cyan]{markup_escape(str(p.get('tool', '')))}[/cyan] "
                    f"[dim]requested args={markup_escape(args_preview)}[/dim]"
                )
            else:
                approved = bool(p.get("approved"))
                color = "green" if approved else "error"
                label = "approved" if approved else "denied"
                console_.print(
                    f"    [{color}]●[/{color}] "
                    f"[cyan]{markup_escape(str(p.get('tool', '')))}[/cyan] "
                    f"[{color}]{label}[/{color}]"
                )

    # Sub-agent activity
    subagent_events = [
        e for e in events if e.get("kind") == "subagent_started"
    ]
    if subagent_events:
        for sa in subagent_events:
            sp = sa.get("payload") or {}
            console_.print(
                f"  [bold]subagent[/bold]  "
                f"[cyan]{markup_escape(str(sp.get('agent', '')))}[/cyan] "
                f"[dim]task={markup_escape(str(sp.get('task', ''))[:80])}[/dim]"
            )

    # LLM call stats (one line aggregating all attempts in this turn)
    started_calls = [e for e in events if e.get("kind") == "llm_call_started"]
    completed_calls = [e for e in events if e.get("kind") == "llm_call_completed"]
    if completed_calls:
        attempts = len(started_calls) or len(completed_calls)
        total_prompt = sum(
            int((e.get("payload") or {}).get("prompt_tokens") or 0)
            for e in completed_calls
        )
        total_completion = sum(
            int((e.get("payload") or {}).get("completion_tokens") or 0)
            for e in completed_calls
        )
        total_ms = sum(
            int((e.get("payload") or {}).get("duration_ms") or 0)
            for e in completed_calls
        )
        errored = any(
            (e.get("payload") or {}).get("status") == "error"
            for e in completed_calls
        )
        tag = "[error]LLM⚠[/error]" if errored else "[dim]LLM[/dim]"
        models = sorted({
            str((e.get("payload") or {}).get("model"))
            for e in started_calls
            if (e.get("payload") or {}).get("model")
        })
        finishes = sorted({
            str((e.get("payload") or {}).get("finish_reason"))
            for e in completed_calls
            if (e.get("payload") or {}).get("finish_reason") is not None
        })
        model_part = f" model={models[-1]}" if models else ""
        finish_part = f" finish={','.join(finishes)}" if finishes else ""
        console_.print(
            f"  {tag}   [dim]{attempts} call(s), "
            f"p={total_prompt} c={total_completion} {_format_ms(total_ms)}"
            f"{markup_escape(model_part)}{markup_escape(finish_part)}[/dim]"
        )
        for call in completed_calls:
            p = call.get("payload") or {}
            if p.get("status") == "error":
                console_.print(
                    f"    [error]llm_error[/error] "
                    f"[dim]attempt={p.get('attempt')} "
                    f"{markup_escape(str(p.get('error_class') or ''))}: "
                    f"{markup_escape(str(p.get('error_message') or '')[:160])}[/dim]"
                )

    # Context compression / summary events surface as one-liners.
    for e in events:
        kind = e.get("kind")
        if kind == "context_compressed":
            p = e.get("payload") or {}
            console_.print(
                f"  [dim]compact[/dim]  "
                f"[dim]{markup_escape(str(p.get('type', '')))}  "
                f"msgs {p.get('pre_msgs')}→{p.get('post_msgs')}[/dim]"
            )
        elif kind == "session_summary_written":
            p = e.get("payload") or {}
            console_.print(
                f"  [dim]summary[/dim]  "
                f"[dim]id={markup_escape(str(p.get('summary_id', ''))[:8])} "
                f"summarized={p.get('messages_summarized')}[/dim]"
            )
        elif kind == "ask_user_requested":
            p = e.get("payload") or {}
            console_.print(
                f"  [bold]ask[/bold]  "
                f"{markup_escape(str(p.get('question', ''))[:120])}"
            )
        elif kind == "ask_user_answered":
            p = e.get("payload") or {}
            answer = str(p.get("answer", ""))
            preview, more = _preview(answer, 120)
            console_.print(
                f"  [dim]answer[/dim] {markup_escape(preview)}{more}"
            )
        elif kind in ("model_changed", "permission_mode_changed", "readonly_mode_changed"):
            console_.print(
                f"  [dim]state[/dim]   "
                f"[cyan]{kind}[/cyan] {_summarize_replay_event(e)}"
            )
        elif kind == "plugin_hook_fired":
            p = e.get("payload") or {}
            outcome = p.get("outcome", "allow")
            color = {"block": "error", "stop": "warning", "modify": "yellow"}.get(outcome, "green")
            console_.print(
                f"  [dim]hook[/dim]    "
                f"[{color}]{outcome}[/{color}] "
                f"[dim]{markup_escape(str(p.get('hook_name', '')))}[/dim]"
            )
        elif kind == "error":
            p = e.get("payload") or {}
            console_.print(
                f"  [error]error[/error]  "
                f"{markup_escape(str(p.get('message', ''))[:160])}"
            )
            detail = str(p.get("detail", "") or "")
            if detail:
                preview, more = _preview(detail, 240)
                console_.print(
                    f"    [dim]detail[/dim] {markup_escape(preview)}{more}"
                )

    # Aggregated assistant final text. Prefer streamed chunks when
    # present; otherwise fall back to turn_completed.final_text so
    # replay files without capture_llm_delta still show the answer.
    text = "".join(
        str((e.get("payload") or {}).get("chunk", ""))
        for e in events
        if e.get("kind") == "assistant_text_chunk"
    )
    if not text:
        tc = next(
            (e for e in events if e.get("kind") == "turn_completed"), None,
        )
        if tc:
            text = str((tc.get("payload") or {}).get("final_text", ""))
    if text:
        console_.print(
            f"  [bold reverse green] asst [/bold reverse green]  "
            f"{markup_escape(text)} "
            f"[dim]({len(text)} chars)[/dim]"
        )

    # Close line — turn_completed carries the final status.
    tc = next(
        (e for e in events if e.get("kind") == "turn_completed"), None,
    )
    if tc:
        p = tc.get("payload") or {}
        status = p.get("status", "ok")
        color = "green" if status == "ok" else "error"
        err = p.get("error")
        err_part = f"  [dim]err={markup_escape(str(err)[:80])}[/dim]" if err else ""
        console_.print(
            f"  [dim]└─[/dim]  [{color}]{status}[/{color}]{err_part}"
        )
    console_.print()


def _collect_tool_rows(events: list) -> list:
    """Aggregate TOOL_* events per call_id into a list of row dicts."""
    rows: dict = {}
    order: list = []
    for e in events:
        kind = e.get("kind")
        if kind not in (
            "tool_started",
            "tool_completed",
            "tool_result",
            "tool_output_chunk",
            "tool_confirmation_requested",
            "tool_confirmation_resolved",
        ):
            continue
        payload = e.get("payload") or {}
        cid = payload.get("call_id")
        if not cid:
            continue
        if cid not in rows:
            rows[cid] = {"call_id": cid}
            order.append(cid)
        row = rows[cid]
        if kind == "tool_started":
            row["name"] = payload.get("tool", "")
            row["source"] = payload.get("tool_source", "")
            row["args"] = payload.get("args", {})
            row["args_preview"] = _json_preview(payload.get("args", {}), 240)
        elif kind == "tool_confirmation_requested":
            row["name"] = payload.get("tool", row.get("name", ""))
            row["args"] = payload.get("args", row.get("args", {}))
            row["args_preview"] = _json_preview(row.get("args", {}), 240)
        elif kind == "tool_completed":
            row["status"] = payload.get("status")
            row["duration_ms"] = payload.get("duration_ms", 0)
            row["error"] = payload.get("error")
        elif kind == "tool_result":
            row["name"] = payload.get("tool", row.get("name", ""))
            row["status"] = payload.get("status", row.get("status"))
            row["duration_ms"] = payload.get(
                "duration_ms", row.get("duration_ms", 0),
            )
            row["error"] = payload.get("error", row.get("error"))
            row["truncated"] = (payload.get("content_truncation") or {}).get(
                "truncated", False,
            )
            row["saved_to_disk"] = payload.get("saved_to_disk", False)
            row["original_chars"] = payload.get("original_chars")
            row["content_hash"] = payload.get("content_hash")
            row["disk_path"] = payload.get("disk_path")
        elif kind == "tool_output_chunk" and payload.get("truncated"):
            row["output_truncated"] = True
        elif kind == "tool_confirmation_resolved":
            row["confirmation"] = payload.get("approved")
    return [rows[c] for c in order]


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
