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
        self, *, session_id: str | None = None, cwd: Path | None = None
    ) -> dict[str, Any]:
        return {
            "event": "SessionStart",
            "data": {
                "sessionId": session_id or "",
                "cwd": str(cwd or Path.cwd()),
            },
        }

    def build_session_end(
        self, *, session_id: str | None = None, cwd: Path | None = None
    ) -> dict[str, Any]:
        return {
            "event": "SessionEnd",
            "data": {
                "sessionId": session_id or "",
                "cwd": str(cwd or Path.cwd()),
            },
        }

    def build_pre_tool_use(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        resolver = ToolAliasResolver()
        return {
            "event": "PreToolUse",
            "data": {
                "toolName": resolver.to_claude_name(tool_name),
                "toolInput": tool_input or {},
                "sessionId": session_id or "",
            },
        }

    def build_post_tool_use(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, Any] | None = None,
        tool_output: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        resolver = ToolAliasResolver()
        return {
            "event": "PostToolUse",
            "data": {
                "toolName": resolver.to_claude_name(tool_name),
                "toolInput": tool_input or {},
                "toolOutput": tool_output or "",
                "sessionId": session_id or "",
            },
        }

    def build_post_tool_use_failure(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, Any] | None = None,
        error: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        resolver = ToolAliasResolver()
        return {
            "event": "PostToolUseFailure",
            "data": {
                "toolName": resolver.to_claude_name(tool_name),
                "toolInput": tool_input or {},
                "error": error or "",
                "sessionId": session_id or "",
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
        compaction_type: str,
        reason: str,
        permission_mode: str | None = None,
    ) -> dict[str, Any]:
        return {
            "hook_event_name": "PreCompact",
            "session_id": session_id or "",
            "transcript_path": None,
            "cwd": str(cwd or Path.cwd()),
            "permission_mode": permission_mode or "workspace-write",
            "trigger": "auto",
            "custom_instructions": "",
            "compaction_type": compaction_type,
            "reason": reason,
        }
