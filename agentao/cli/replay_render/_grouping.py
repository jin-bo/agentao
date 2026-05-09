"""Event grouping helpers — partition the flat event stream by turn,
and aggregate per-call_id rows for the tools table.
"""

from __future__ import annotations

from ._fmt import _json_preview


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
