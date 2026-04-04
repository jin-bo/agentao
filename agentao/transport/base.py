"""Transport protocol — the single interface between Agentao runtime and UI/transport."""

from typing import Protocol, runtime_checkable

from .events import AgentEvent


@runtime_checkable
class Transport(Protocol):
    """Interface between the Agentao core runtime and any UI or transport layer.

    A Transport has two responsibilities:

    1. **One-way events** (fire-and-forget):
       ``emit(event)`` receives structured events from the runtime.
       The runtime never inspects the return value.

    2. **Request-response interactions** (blocking):
       ``confirm_tool``, ``ask_user``, and ``on_max_iterations`` are called
       when the runtime needs a synchronous decision from the user/caller.

    Implementing all methods is not required — use ``NullTransport`` as a
    base or mixin if you only care about a subset.
    """

    # ── One-way events ────────────────────────────────────────────────────────

    def emit(self, event: AgentEvent) -> None:
        """Receive a runtime event.  Must not raise; errors should be swallowed."""
        ...

    # ── Request-response interactions ────────────────────────────────────────

    def confirm_tool(self, tool_name: str, description: str, args: dict) -> bool:
        """Ask whether the tool may execute.

        Returns:
            True  → allow execution
            False → cancel execution
        """
        ...

    def ask_user(self, question: str) -> str:
        """Ask the user a free-form question and return their answer."""
        ...

    def on_max_iterations(self, count: int, messages: list) -> dict:
        """Called when the runtime reaches its tool-iteration limit.

        Returns a dict with key ``"action"``:
            ``"continue"``        — keep running
            ``"stop"``            — return current response
            ``"new_instruction"`` — inject a new user message; set ``"message"`` key
        """
        ...
