"""Per-turn rendering — the bulk of /replays show grouped output."""

from __future__ import annotations

from rich.markup import escape as markup_escape

from ._fmt import (
    _duration_between,
    _event_counts,
    _format_ms,
    _format_ts_local,
    _json_preview,
    _preview,
)
from ._grouping import _collect_tool_rows
from ._summary import _summarize_replay_event


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
