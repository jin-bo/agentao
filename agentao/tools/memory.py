"""Memory tool for saving important information to persistent layered storage."""

from typing import Any, Dict, TYPE_CHECKING

from .base import Tool

if TYPE_CHECKING:
    from ..memory.manager import MemoryManager


class SaveMemoryTool(Tool):
    """Thin wrapper: delegates to MemoryManager.save_from_tool."""

    def __init__(self, memory_manager: "MemoryManager"):
        self.memory_manager = memory_manager

    @property
    def name(self) -> str:
        return "save_memory"

    @property
    def description(self) -> str:
        return (
            "Save a fact to long-term memory, for use in later conversations. "
            "Save a fact only in these cases:\n"
            "- The user asks you to remember something.\n"
            "- The user clearly states a durable fact or preference that would be useful "
            "across sessions. Examples: a preferred language, a coding style, a recurring "
            "workflow.\n\n"
            "Do not save details that apply only to this session. Do not save general "
            "project context. If you are not sure that a fact is worth saving, ask the "
            "user in your reply text. Use a descriptive snake_case key, for example "
            "'user_preferred_language'."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": (
                        "A short snake_case identifier, for example "
                        "'user_preferred_language' or 'user_coding_style'."
                    ),
                },
                "value": {
                    "type": "string",
                    "description": "The information to remember",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags for categorizing this memory",
                },
                "scope": {
                    "type": "string",
                    "enum": ["user", "project"],
                    "description": "Optional: 'user' for cross-project facts, 'project' for project-specific. Auto-inferred if omitted.",
                },
                "type": {
                    "type": "string",
                    "enum": ["preference", "profile", "project_fact", "workflow", "decision", "constraint", "note"],
                    "description": "Optional: memory type. Auto-inferred from tags if omitted.",
                },
            },
            "required": ["key", "value"],
        }

    @property
    def requires_confirmation(self) -> bool:
        return False

    def execute(self, key: str, value: str, tags: list = None, scope: str = None, type: str = None) -> str:
        """Save information to memory."""
        return self.memory_manager.save_from_tool(key, value, tags or [], scope=scope, type=type)
