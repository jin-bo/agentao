"""NullTransport — silent default used when no transport is configured."""

from .events import AgentEvent


class NullTransport:
    """A transport that silently discards all events and auto-approves all interactions.

    Used as the default when ``Agentao`` is instantiated without a transport,
    enabling headless / programmatic use with no configuration required.
    """

    def emit(self, event: AgentEvent) -> None:
        pass

    def confirm_tool(self, tool_name: str, description: str, args: dict) -> bool:
        return True

    def ask_user(self, question: str) -> str:
        return "[ask_user: not available in non-interactive mode]"

    def on_max_iterations(self, count: int, messages: list) -> dict:
        return {"action": "stop"}
