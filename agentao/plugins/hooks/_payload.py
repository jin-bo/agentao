"""``ClaudeHookPayloadAdapter`` — build payloads for hook-event delivery.

Two shapes are emitted:

- ``UserPromptSubmit`` / ``Session*`` / ``*ToolUse*`` use the Agentao
  ``{"event": ..., "data": {...}}`` envelope.
- ``Stop`` and ``PreCompact`` use Claude Code's flat snake_case
  top-level schema so a hook script reading ``stdin`` stays
  byte-compatible with Claude Code.

The dispatcher's ``_matches`` resolver handles the dual shape; the
mismatch is intentional and load-bearing for cross-tool portability.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...compaction.types import (
    CompactionKind,
    CompactionReason,
    CompactionTrigger,
)
from ._alias import ToolAliasResolver


class ClaudeHookPayloadAdapter:
    """Build hook payloads in Claude-compatible format."""

    def build_user_prompt_submit(
        self,
        *,
        user_message: str,
        session_id: str | None = None,
        cwd: Path | None = None,
    ) -> dict[str, Any]:
        return {
            "event": "UserPromptSubmit",
            "data": {
                "userMessage": user_message,
                "sessionId": session_id or "",
                "cwd": str(cwd or Path.cwd()),
            },
        }

    def build_session_start(
        self,
        *,
        session_id: str | None = None,
        cwd: Path | None = None,
        source: str = "startup",
        model: str | None = None,
    ) -> dict[str, Any]:
        return {
            "event": "SessionStart",
            "data": {
                "sessionId": session_id or "",
                "cwd": str(cwd or Path.cwd()),
                # ``source`` is required upstream and derivable at both dispatch
                # sites; ``startup`` is the honest default, not a placeholder.
                "source": source,
                "model": model,
            },
        }

    def build_session_end(
        self,
        *,
        session_id: str | None = None,
        cwd: Path | None = None,
        reason: str = "other",
    ) -> dict[str, Any]:
        return {
            "event": "SessionEnd",
            "data": {
                "sessionId": session_id or "",
                "cwd": str(cwd or Path.cwd()),
                # ``other`` is upstream's own value for "none of the named
                # causes", so it is a real value rather than a stand-in.
                "reason": reason,
            },
        }

    def build_pre_tool_use(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, Any] | None = None,
        session_id: str | None = None,
        tool_use_id: str = "",
        cwd: Path | None = None,
    ) -> dict[str, Any]:
        resolver = ToolAliasResolver()
        return {
            "event": "PreToolUse",
            "data": {
                "toolName": resolver.to_claude_name(tool_name),
                "toolInput": tool_input or {},
                "sessionId": session_id or "",
                # Both were "exists, unplumbed" in the field matrix: the id is
                # the normalized ``plan.tool_call_id`` the runner already has,
                # and ``cwd`` is required on all eight events.
                "toolUseId": tool_use_id or "",
                "cwd": str(cwd or Path.cwd()),
            },
        }

    def build_post_tool_use(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, Any] | None = None,
        tool_output: str | None = None,
        session_id: str | None = None,
        tool_use_id: str = "",
        duration_ms: int | None = None,
        cwd: Path | None = None,
    ) -> dict[str, Any]:
        resolver = ToolAliasResolver()
        return {
            "event": "PostToolUse",
            "data": {
                "toolName": resolver.to_claude_name(tool_name),
                "toolInput": tool_input or {},
                "toolOutput": tool_output or "",
                "sessionId": session_id or "",
                "toolUseId": tool_use_id or "",
                "durationMs": duration_ms,
                "cwd": str(cwd or Path.cwd()),
            },
        }

    def build_post_tool_use_failure(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, Any] | None = None,
        error: str | None = None,
        session_id: str | None = None,
        tool_use_id: str = "",
        duration_ms: int | None = None,
        is_interrupt: bool | None = None,
        cwd: Path | None = None,
    ) -> dict[str, Any]:
        resolver = ToolAliasResolver()
        return {
            "event": "PostToolUseFailure",
            "data": {
                "toolName": resolver.to_claude_name(tool_name),
                "toolInput": tool_input or {},
                "error": error or "",
                "sessionId": session_id or "",
                "toolUseId": tool_use_id or "",
                "durationMs": duration_ms,
                "isInterrupt": is_interrupt,
                "cwd": str(cwd or Path.cwd()),
            },
        }

    # Stop / PreCompact use Claude Code's flat snake_case top-level schema
    # rather than the {event, data} envelope used by the events above.
    # This keeps a hook script reading from stdin Claude-compatible.
    # _matches in PluginHookDispatcher handles the dual shape.

    def build_stop(
        self,
        *,
        session_id: str | None = None,
        cwd: Path | None = None,
        last_assistant_message: str = "",
        stop_hook_active: bool = False,
        turn_end_reason: str = "final_response",
        permission_mode: str | None = None,
    ) -> dict[str, Any]:
        return {
            "hook_event_name": "Stop",
            "session_id": session_id or "",
            "transcript_path": None,
            "cwd": str(cwd or Path.cwd()),
            "permission_mode": permission_mode or "workspace-write",
            "stop_hook_active": bool(stop_hook_active),
            "last_assistant_message": last_assistant_message or "",
            "turn_end_reason": turn_end_reason,
        }

    def build_pre_compact(
        self,
        *,
        session_id: str | None = None,
        cwd: Path | None = None,
        trigger: CompactionTrigger,
        compaction_type: CompactionKind,
        reason: CompactionReason,
        custom_instructions: str = "",
        permission_mode: str | None = None,
    ) -> dict[str, Any]:
        """Build the flat Claude-shape ``PreCompact`` payload.

        ``trigger`` is **required and has no default**. It used to be
        hardcoded ``"auto"`` for all five compaction entry points, which
        made ``{"trigger": "manual"}`` a matcher value with no reachable
        producer — a rule written against it could never fire anywhere.
        A default here would let a new entry point silently reacquire that
        bug, so the provenance is stated at every call site instead.
        """
        return {
            "hook_event_name": "PreCompact",
            "session_id": session_id or "",
            "transcript_path": None,
            "cwd": str(cwd or Path.cwd()),
            "permission_mode": permission_mode or "workspace-write",
            "trigger": trigger,
            "custom_instructions": custom_instructions,
            "compaction_type": compaction_type,
            "reason": reason,
        }
