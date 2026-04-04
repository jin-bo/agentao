"""Structured event types emitted by the Agentao runtime."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict


class EventType(str, Enum):
    TURN_START    = "turn_start"    # about to call LLM (reset display, spinner to "Thinking…")
    TOOL_START    = "tool_start"    # tool execution starting
    TOOL_OUTPUT   = "tool_output"   # streaming chunk from a tool
    TOOL_COMPLETE = "tool_complete" # tool execution finished
    THINKING      = "thinking"      # LLM reasoning / thought text
    LLM_TEXT      = "llm_text"      # LLM response text chunk (streaming)
    ERROR         = "error"         # runtime error


@dataclass
class AgentEvent:
    """A single structured event emitted by the Agentao runtime.

    All data values must be JSON-serializable so transports can forward
    events over SSE / WebSocket without extra marshalling.

    Common data payloads:
        TURN_START    {}
        TOOL_START    {"tool": "run_shell_command", "args": {...}}
        TOOL_OUTPUT   {"tool": "run_shell_command", "chunk": "hello\\n"}
        TOOL_COMPLETE {"tool": "run_shell_command"}
        THINKING      {"text": "Let me think..."}
        LLM_TEXT      {"chunk": "Sure, I can help"}
        ERROR         {"message": "...", "detail": "..."}
    """

    type: EventType
    data: Dict[str, Any] = field(default_factory=dict)
