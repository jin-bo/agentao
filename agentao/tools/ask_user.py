"""Ask user tool for interactive clarification during LLM task execution."""

from typing import Callable, Optional

from .base import Tool


class AskUserTool(Tool):
    """Tool that allows the LLM to ask the user a clarifying question."""

    def __init__(self, ask_user_callback: Optional[Callable[[str], str]] = None):
        self._callback = ask_user_callback

    @property
    def name(self) -> str:
        return "ask_user"

    @property
    def description(self) -> str:
        return (
            "Ask the user a clarifying question and wait for their text response. "
            "Use when you need missing information to proceed, or to confirm ambiguous requirements. "
            "Do NOT use for yes/no confirmations — only use when free-form user input is needed."
        )

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user",
                },
            },
            "required": ["question"],
        }

    @property
    def requires_confirmation(self) -> bool:
        return False

    def execute(self, question: str) -> str:
        if self._callback:
            return self._callback(question)
        return "[ask_user: not available in non-interactive mode]"
