"""Ask user tool for interactive clarification during LLM task execution."""

from typing import Callable, List, Optional

from .base import Tool


class AskUserTool(Tool):
    """Tool that allows the LLM to ask the user a clarifying question.

    The question may be plain free-form text, or it may carry optional
    structured hints (``header`` / ``options`` / ``multiple`` /
    ``allow_custom``) that richer transports can render as a choice
    prompt. The structured fields are advisory: a transport is free to
    ignore them and fall back to a plain text prompt, and the answer is
    always returned as a single string.
    """

    @property
    def is_read_only(self) -> bool:
        return True

    def __init__(self, ask_user_callback: Optional[Callable[..., str]] = None):
        self._callback = ask_user_callback

    @property
    def name(self) -> str:
        return "ask_user"

    @property
    def description(self) -> str:
        return (
            "Ask the user a clarifying question and wait for their response. "
            "Use when you need missing information to proceed, or to confirm ambiguous requirements. "
            "Do NOT use for yes/no confirmations — only use when free-form user input is needed. "
            "Optionally pass `options` to suggest choices (set `multiple` to allow more than one, "
            "and `allow_custom=false` to restrict the answer to the listed options)."
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
                "header": {
                    "type": "string",
                    "description": "Optional short label (a few words) categorizing the question.",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional list of suggested answers to present as choices. "
                        "The user may still type a custom answer unless allow_custom is false."
                    ),
                },
                "multiple": {
                    "type": "boolean",
                    "description": "Whether the user may select more than one option. Default false.",
                },
                "allow_custom": {
                    "type": "boolean",
                    "description": (
                        "Whether the user may type a free-form answer in addition to any options. "
                        "Default true."
                    ),
                },
            },
            "required": ["question"],
        }

    @property
    def requires_confirmation(self) -> bool:
        return False

    def execute(
        self,
        question: str,
        header: Optional[str] = None,
        options: Optional[List[str]] = None,
        multiple: bool = False,
        allow_custom: bool = True,
    ) -> str:
        if self._callback:
            # Forward the structured hints only to callbacks that accept
            # them, so a legacy 1-arg ``AskUserTool(lambda q: ...)`` keeps
            # working instead of raising ``TypeError`` on the new kwargs.
            from ..transport.sdk import invoke_ask_user_callback

            return invoke_ask_user_callback(
                self._callback,
                question,
                {
                    "header": header,
                    "options": options,
                    "multiple": multiple,
                    "allow_custom": allow_custom,
                },
            )
        return "[ask_user: not available in non-interactive mode]"
