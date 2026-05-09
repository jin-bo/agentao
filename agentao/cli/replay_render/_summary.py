"""One-line per-event summaries used by both raw and grouped views."""

from __future__ import annotations

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
