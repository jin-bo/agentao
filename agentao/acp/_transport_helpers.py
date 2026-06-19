"""Shared content-block / JSON-safety helpers for the ACP transport.

Used by the live event mapping (:mod:`agentao.acp.transport`), the history
replay path (:mod:`agentao.acp._transport_replay`), and the request/response
interactions (:mod:`agentao.acp._transport_interaction`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


# ---------------------------------------------------------------------------
# Tool name → ACP tool-call kind
# ---------------------------------------------------------------------------

_TOOL_KIND_MAP: Dict[str, str] = {
    # read-only file operations
    "read_file": "read",
    "read_folder": "read",
    "list_directory": "read",
    # mutations
    "write_file": "edit",
    "edit_file": "edit",
    "edit": "edit",
    # search
    "find_files": "search",
    "search_text": "search",
    "grep": "search",
    "glob": "search",
    "web_search": "search",
    # execution
    "run_shell_command": "execute",
    "bash": "execute",
    "shell": "execute",
    # network
    "web_fetch": "fetch",
    # everything else (skills, memory, ask_user, sub-agents, MCP tools) → "other"
}


def _tool_kind(tool_name: str) -> str:
    """Map an Agentao tool name to an ACP ``tool_call.kind`` enum value.

    Unknown tools (including all ``mcp_*`` tools) fall back to ``"other"``.
    """
    return _TOOL_KIND_MAP.get(tool_name, "other")


# ---------------------------------------------------------------------------
# todo_write → ACP ``plan`` update
# ---------------------------------------------------------------------------

_PLAN_ENTRY_STATUS = frozenset({"pending", "in_progress", "completed"})


def _todo_write_plan(raw_args: Any) -> Dict[str, Any] | None:
    """Map ``todo_write`` tool args to an ACP ``plan`` update, or ``None``.

    The ``todos`` list comes from the LLM and may be malformed, so it is
    validated rather than trusted: every entry must be a dict with a string
    ``content`` and a ``status`` in the ACP ``PlanEntryStatus`` set. agentao
    todos have no priority while ACP requires one, so every entry is emitted
    as ``"medium"``.

    Validation is **all-or-nothing**: an ACP ``plan`` replaces the *entire*
    checklist on each update, so silently dropping a malformed entry would
    make a real task vanish from the client's view. If the list is empty or
    *any* entry is malformed, return ``None`` so the caller falls back to the
    normal ``tool_call`` mapping (which carries the full raw args) instead of
    emitting a truncated or empty plan.
    """
    todos = raw_args.get("todos") if isinstance(raw_args, dict) else None
    if not isinstance(todos, list) or not todos:
        return None
    entries = []
    for t in todos:
        if not (
            isinstance(t, dict)
            and isinstance(t.get("content"), str)
            and t.get("status") in _PLAN_ENTRY_STATUS
        ):
            return None  # any malformed entry → fall back, never truncate
        entries.append(
            {"content": t["content"], "priority": "medium", "status": t["status"]}
        )
    return {"sessionUpdate": "plan", "entries": entries}


# ---------------------------------------------------------------------------
# JSON safety coercion
# ---------------------------------------------------------------------------

def _json_safe(value: Any) -> Any:
    """Recursively coerce a value into a JSON-serializable form.

    Handles the common offenders we expect to see in tool ``args``:
    :class:`pathlib.Path`, sets, tuples, and arbitrary objects. Anything
    already JSON-native passes through unchanged.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(v) for v in value]
    return str(value)


# ---------------------------------------------------------------------------
# Content block helpers
# ---------------------------------------------------------------------------

def _text_block(text: str) -> Dict[str, Any]:
    """Build an ACP ``ContentBlock`` wrapping plain text."""
    return {"type": "text", "text": text}


def _tool_content_text(text: str) -> Dict[str, Any]:
    """Build a ``ToolCallContent`` entry that wraps plain text.

    Per ACP spec, ``tool_call.content`` is an array of
    ``{type: "content", content: ContentBlock}`` entries (plus diff and
    terminal variants we do not use in v1).
    """
    return {"type": "content", "content": _text_block(text)}
