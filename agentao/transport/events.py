"""Structured event types emitted by the Agentao runtime."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict


class EventType(str, Enum):
    TURN_START    = "turn_start"    # about to call LLM (reset display, spinner to "Thinking…")
    TOOL_START    = "tool_start"    # tool execution starting
    TOOL_OUTPUT   = "tool_output"   # streaming chunk from a tool
    TOOL_COMPLETE = "tool_complete" # tool execution finished (status + duration)
    TOOL_RESULT   = "tool_result"   # tool execution final result (content + hash + disk meta)
    THINKING      = "thinking"      # LLM reasoning / thought text
    LLM_TEXT      = "llm_text"      # LLM response text chunk (streaming)
    LLM_CALL_STARTED   = "llm_call_started"    # metadata before hitting the LLM
    LLM_CALL_COMPLETED = "llm_call_completed"  # usage + finish_reason after the call
    LLM_CALL_DELTA     = "llm_call_delta"      # messages NEW since the previous call
    LLM_CALL_IO        = "llm_call_io"         # full messages + tools (opt-in deep capture)
    ERROR         = "error"         # runtime error
    AGENT_START       = "agent_start"       # sub-agent started (replaces __agent_start__ magic string)
    AGENT_END         = "agent_end"         # sub-agent finished (replaces __agent_end__ magic string)
    TOOL_CONFIRMATION = "tool_confirmation" # about to ask user to confirm a tool call
    # Step 5 — interaction + history lifecycle (replay observability)
    ASK_USER_REQUESTED = "ask_user_requested"
    ASK_USER_ANSWERED  = "ask_user_answered"
    BACKGROUND_NOTIFICATION_INJECTED = "background_notification_injected"
    CONTEXT_COMPRESSED = "context_compressed"
    SESSION_SUMMARY_WRITTEN = "session_summary_written"
    # Step 6 — runtime state changes
    SKILL_ACTIVATED         = "skill_activated"
    SKILL_DEACTIVATED       = "skill_deactivated"
    MEMORY_WRITE            = "memory_write"
    MEMORY_DELETE           = "memory_delete"
    MEMORY_CLEARED          = "memory_cleared"
    MODEL_CHANGED           = "model_changed"
    PERMISSION_MODE_CHANGED = "permission_mode_changed"
    READONLY_MODE_CHANGED   = "readonly_mode_changed"
    PLUGIN_HOOK_FIRED       = "plugin_hook_fired"


@dataclass
class AgentEvent:
    """A single structured event emitted by the Agentao runtime.

    All data values must be JSON-serializable so transports can forward
    events over SSE / WebSocket without extra marshalling.

    ``schema_version`` is the runtime-payload version contract for the
    wire form (independent of ACP's protocol version); bump it when a
    payload field's shape or semantics change.

    Common data payloads:
        TURN_START    {}
        TOOL_START    {"tool": "run_shell_command", "args": {...}, "call_id": "uuid"}
        TOOL_OUTPUT   {"tool": "run_shell_command", "chunk": "hello\\n", "call_id": "uuid"}
        TOOL_COMPLETE {"tool": "run_shell_command", "call_id": "uuid",
                       "status": "ok"|"error"|"cancelled",
                       "duration_ms": 123, "error": None}
        TOOL_RESULT   {"tool": "run_shell_command", "call_id": "uuid",
                       "content": "…full raw result…",
                       "content_hash": "sha256:abcd…",
                       "original_chars": 12345,
                       "saved_to_disk": True|False,
                       "disk_path": ".agentao/tool-outputs/…"|None,
                       "status": "ok"|"error"|"cancelled",
                       "duration_ms": 123}
        THINKING      {"text": "Let me think..."}
        LLM_TEXT      {"chunk": "Sure, I can help"}
        ERROR         {"message": "...", "detail": "..."}
        AGENT_START       {"agent": "codebase-investigator", "task": "...", "max_turns": 15}
        AGENT_END         {"agent": "codebase-investigator", "state": "completed",
                       "turns": 3, "tool_calls": 5, "tokens": 1200,
                       "duration_ms": 8000, "error": None}
        TOOL_CONFIRMATION {"tool": "run_shell_command", "args": {...}}
    """

    type: EventType
    data: Dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to the JSON-safe wire shape ``{type, schema_version, data}``.

        ``data`` aliases ``self.data`` (no copy); callers must not mutate it.
        """
        return {
            "type": self.type.value,
            "schema_version": self.schema_version,
            "data": self.data,
        }
