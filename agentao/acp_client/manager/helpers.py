"""Display-text and option-selection helpers for the ACP manager.

Pure module-level functions with no dependency on manager state. Kept
separate from the mixins so they can be imported without pulling in
the manager class graph.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..interaction import PendingInteraction

logger = logging.getLogger("agentao.acp_client")


def _extract_display_text(method: str, params: Any) -> str:
    """Build a human-friendly display string from ACP notification/request params.

    Extracts a concise, user-readable summary from structured ACP payloads.
    The full raw params are logged at debug level for diagnostics.
    """
    if not isinstance(params, dict):
        return str(params) if params is not None else ""

    # Log full payload for debugging — never show raw dicts to the user.
    logger.debug("acp: %s params: %s", method, params)

    # -- session/request_permission ----------------------------------------
    if method == "session/request_permission":
        return _format_permission_text(params)

    # -- _agentao.cn/ask_user ----------------------------------------------
    if method == "_agentao.cn/ask_user":
        return params.get("question") or params.get("message") or "(input requested)"

    # -- session/update (most common) --------------------------------------
    if method == "session/update":
        return _format_session_update(params)

    # -- Generic fallback --------------------------------------------------
    for key in ("message", "text", "description", "question"):
        val = params.get(key)
        if val:
            return _truncate(str(val), 120)
    return "(notification)"


def _format_permission_text(params: dict) -> str:
    """Format a ``session/request_permission`` payload."""
    tool_call = params.get("toolCall")
    if not isinstance(tool_call, dict):
        return params.get("message") or "(permission requested)"
    title = tool_call.get("title") or "unknown tool"
    kind = tool_call.get("kind", "")
    raw_input = tool_call.get("rawInput")
    parts = [f"Allow {title}"]
    if kind:
        parts[0] += f" ({kind})"
    if isinstance(raw_input, dict):
        arg_items = list(raw_input.items())[:3]
        arg_str = ", ".join(f"{k}={_truncate(str(v), 50)}" for k, v in arg_items)
        if len(raw_input) > 3:
            arg_str += ", ..."
        if arg_str:
            parts.append(arg_str)
    return "?\n  ".join(parts) if len(parts) > 1 else parts[0] + "?"


def _format_session_update(params: dict) -> str:
    """Format a ``session/update`` notification into a concise line."""
    update = params.get("update")
    if not isinstance(update, dict):
        return "(update)"
    kind = update.get("sessionUpdate", "")

    # tool_call: show tool name + args summary
    if kind == "tool_call":
        title = update.get("title", "?")
        tool_kind = update.get("kind", "")
        status = update.get("status", "")
        suffix = f" ({tool_kind})" if tool_kind else ""
        raw = update.get("rawInput")
        if isinstance(raw, dict) and raw:
            args = ", ".join(
                f"{k}={_truncate(str(v), 40)}" for k, v in list(raw.items())[:3]
            )
            return f"{title}{suffix} [{status}]\n  {args}"
        return f"{title}{suffix} [{status}]"

    # tool_call_update: show status
    if kind == "tool_call_update":
        status = update.get("status", "?")
        call_id = update.get("toolCallId", "")
        short_id = call_id[:8] if call_id else ""
        return f"tool {short_id} — {status}"

    # agent_message_chunk: show full text (this is the LLM reply)
    if kind == "agent_message_chunk":
        content = update.get("content")
        if isinstance(content, dict):
            text = content.get("text", "")
            return text if text else ""
        return ""

    # agent_thought_chunk: show reasoning (dimmed in render)
    if kind == "agent_thought_chunk":
        content = update.get("content")
        if isinstance(content, dict):
            text = content.get("text", "")
            return text if text else ""
        return ""

    # user_message_chunk
    if kind == "user_message_chunk":
        content = update.get("content")
        if isinstance(content, dict):
            text = content.get("text", "")
            return _truncate(text, 80) if text else "(user message)"
        return "(user message)"

    return f"({kind})" if kind else "(update)"


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."


def _select_reject_option(options: List[Dict[str, Any]]) -> Optional[str]:
    """Pick the best option id to reject a ``session/request_permission`` request.

    ACP servers may expose a non-standard set of options: the canonical
    ``reject_once`` / ``reject_always`` ids are not guaranteed. Preference
    order:

    1. ``kind`` matches ``reject_once`` (exact match wins; first occurrence).
    2. ``kind`` matches any ``reject_*`` variant.
    3. ``optionId`` / ``id`` / ``name`` contains ``reject``, ``deny``, or
       ``cancel`` (case-insensitive).

    Returns ``None`` when no option looks reject-flavored; callers should
    fall back to an explicit ``cancelled`` outcome so the server does not
    hang waiting for a valid selection.
    """
    if not options:
        return None

    def _opt_id(opt: Dict[str, Any]) -> Optional[str]:
        for key in ("optionId", "id"):
            val = opt.get(key)
            if isinstance(val, str) and val:
                return val
        return None

    # Pass 1: kind == "reject_once" (canonical).
    for opt in options:
        if opt.get("kind") == "reject_once":
            oid = _opt_id(opt)
            if oid:
                return oid
    # Pass 2: any reject_* kind.
    for opt in options:
        kind = opt.get("kind")
        if isinstance(kind, str) and kind.startswith("reject"):
            oid = _opt_id(opt)
            if oid:
                return oid
    # Pass 3: reject/deny/cancel hint in id or name.
    hints = ("reject", "deny", "cancel")
    for opt in options:
        haystack = " ".join(
            str(opt.get(k, "")) for k in ("optionId", "id", "name", "label")
        ).lower()
        if any(h in haystack for h in hints):
            oid = _opt_id(opt)
            if oid:
                return oid
    return None


def _extract_options(interaction: "PendingInteraction") -> List[Dict[str, Any]]:
    """Return the ``options`` list from the original server request params.

    Servers can ship non-canonical option IDs (e.g. ``go_ahead`` /
    ``decline_now``) so the interactive approve / reject paths must
    echo the id the server actually sent rather than assuming the
    ACP-spec canonical ``allow_once`` / ``reject_once``.
    """
    details = interaction.details
    if not isinstance(details, dict):
        return []
    raw = details.get("options")
    if not isinstance(raw, list):
        return []
    return [o for o in raw if isinstance(o, dict)]


def _select_option_by_kind(
    options: List[Dict[str, Any]], preferred_kind: str,
) -> Optional[str]:
    """Return the ``optionId`` for the first option whose ``kind`` matches.

    Used to prefer ``allow_always`` over ``allow_once`` (and similarly
    for reject) without duplicating the broader fallback logic in
    :func:`_select_approve_option` / :func:`_select_reject_option`.
    """
    for opt in options:
        if opt.get("kind") == preferred_kind:
            for key in ("optionId", "id"):
                val = opt.get(key)
                if isinstance(val, str) and val:
                    return val
    return None


def _select_approve_option(options: List[Dict[str, Any]]) -> Optional[str]:
    """Pick the best option id to approve a ``session/request_permission`` request.

    Mirrors :func:`_select_reject_option` but looks for allow/accept/approve
    flavored entries. Returns ``None`` when no such option exists; callers
    should fall back to the reject path rather than send an invalid id.
    """
    if not options:
        return None

    def _opt_id(opt: Dict[str, Any]) -> Optional[str]:
        for key in ("optionId", "id"):
            val = opt.get(key)
            if isinstance(val, str) and val:
                return val
        return None

    # Pass 1: kind == "allow_once" (canonical).
    for opt in options:
        if opt.get("kind") == "allow_once":
            oid = _opt_id(opt)
            if oid:
                return oid
    # Pass 2: any allow_* kind.
    for opt in options:
        kind = opt.get("kind")
        if isinstance(kind, str) and kind.startswith("allow"):
            oid = _opt_id(opt)
            if oid:
                return oid
    # Pass 3: allow/accept/approve hint in id or name.
    hints = ("allow", "accept", "approve")
    for opt in options:
        haystack = " ".join(
            str(opt.get(k, "")) for k in ("optionId", "id", "name", "label")
        ).lower()
        if any(h in haystack for h in hints):
            oid = _opt_id(opt)
            if oid:
                return oid
    return None
