"""``_HookOutcome`` — UserPromptSubmit dispatch result.

Returned by ``_dispatch_user_prompt_submit`` so the chat loop can branch
on three outcomes (early-return / unchanged / context-injected) without
the dispatch helper needing to know loop state.
"""

from __future__ import annotations

from typing import Optional


class _HookOutcome:
    """Result of UserPromptSubmit plugin-hook dispatch.

    One of three shapes:

    - ``early_return`` is a string → the loop should return that string
      immediately without calling the LLM (block / stop verdicts).
    - ``early_return`` is ``None`` and ``user_message`` is unchanged →
      no hook fired, or hooks ran with no effect.
    - ``early_return`` is ``None`` and ``user_message`` was rewritten →
      hooks injected additional context that should be prepended.
    """

    __slots__ = ("early_return", "user_message")

    def __init__(self, *, early_return: Optional[str], user_message: str) -> None:
        self.early_return = early_return
        self.user_message = user_message
