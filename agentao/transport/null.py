"""NullTransport — silent default used when no transport is configured."""

from typing import Callable, List, Optional

from .broadcast import EventBroadcaster
from .events import AgentEvent


class NullTransport:
    """A transport that silently discards all events and auto-approves all interactions.

    Used as the default when ``Agentao`` is instantiated without a transport,
    enabling headless / programmatic use with no configuration required.

    Subscribers still see every event (so a replay recorder attached to a
    Null transport works in headless tests).
    """

    def __init__(self) -> None:
        self._broadcast = EventBroadcaster()

    def emit(self, event: AgentEvent) -> None:
        self._broadcast.notify(event)

    def subscribe(self, listener: Callable[[AgentEvent], None]) -> Callable[[], None]:
        return self._broadcast.subscribe(listener)

    def confirm_tool(self, tool_name: str, description: str, args: dict) -> bool:
        return True

    def ask_user(
        self,
        question: str,
        *,
        header: Optional[str] = None,
        options: Optional[List[str]] = None,
        multiple: bool = False,
        allow_custom: bool = True,
    ) -> str:
        return "[ask_user: not available in non-interactive mode]"

    def on_max_iterations(self, count: int, messages: list) -> dict:
        return {"action": "stop"}
