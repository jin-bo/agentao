"""Top-of-replay session banner and end-of-replay footer."""

from __future__ import annotations

from rich.markup import escape as markup_escape

from ._fmt import _format_ts_local


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
