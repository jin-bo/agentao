"""Transport protocol — the single interface between Agentao runtime and UI/transport."""

from typing import Callable, Protocol, runtime_checkable

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

    Optionally a Transport may also expose ``subscribe(listener)`` so
    side-channel observers (e.g. the replay recorder) can mirror the
    event stream without wrapping the transport. The base contract is
    unchanged — implementations that don't care about subscribers may
    omit the method.
    """

    # ── One-way events ────────────────────────────────────────────────────────

    def emit(self, event: AgentEvent) -> None:
        """Receive a runtime event.  Must not raise; errors should be swallowed."""
        ...

    # ── Optional: side-channel observers ─────────────────────────────────────

    def subscribe(self, listener: Callable[[AgentEvent], None]) -> Callable[[], None]:
        """Register ``listener`` to receive every emitted event after the inner emit.

        Returns an idempotent unsubscribe function. Errors raised by the
        listener must be swallowed by the transport; subscription is a
        side channel and never affects the primary emit path.

        Implementations that do not maintain a subscriber list may omit
        this method; consumers should ``getattr(transport, "subscribe", None)``
        before calling.
        """
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
