"""SdkTransport — programmatic transport backed by optional callbacks.

Also provides ``build_compat_transport`` to wrap the legacy 8-callback
API that existed before the Transport abstraction was introduced.
"""

import warnings
from typing import Any, Callable, Dict, List, Optional

from .events import AgentEvent, EventType
from .null import NullTransport


class SdkTransport:
    """A transport driven by optional Python callbacks.

    Suitable for embedding Agentao in scripts, tests, or other programs
    that want structured events without a terminal UI.

    All callback parameters are optional; unset ones fall back to the
    ``NullTransport`` behaviour (ignore / auto-approve).

    Example::

        events = []
        transport = SdkTransport(on_event=events.append)
        agent = Agentao(transport=transport)
        agent.chat("hello")
    """

    def __init__(
        self,
        on_event: Optional[Callable[[AgentEvent], None]] = None,
        confirm_tool: Optional[Callable[[str, str, Dict[str, Any]], bool]] = None,
        ask_user: Optional[Callable[[str], str]] = None,
        on_max_iterations: Optional[Callable[[int, List], dict]] = None,
    ) -> None:
        self._on_event = on_event
        self._confirm_tool = confirm_tool
        self._ask_user = ask_user
        self._on_max_iterations = on_max_iterations

    # ── Transport protocol ────────────────────────────────────────────────────

    def emit(self, event: AgentEvent) -> None:
        if self._on_event:
            try:
                self._on_event(event)
            except Exception:
                pass  # never let a callback crash the runtime

    def confirm_tool(self, tool_name: str, description: str, args: dict) -> bool:
        if self._confirm_tool:
            return self._confirm_tool(tool_name, description, args)
        return True  # auto-approve when no callback

    def ask_user(self, question: str) -> str:
        if self._ask_user:
            return self._ask_user(question)
        return "[ask_user: not available in non-interactive mode]"

    def on_max_iterations(self, count: int, messages: list) -> dict:
        if self._on_max_iterations:
            return self._on_max_iterations(count, messages)
        return {"action": "stop"}


# ── Backward-compatibility shim ───────────────────────────────────────────────

_NULL = NullTransport()


def build_compat_transport(
    confirmation_callback=None,
    step_callback=None,
    thinking_callback=None,
    ask_user_callback=None,
    output_callback=None,
    tool_complete_callback=None,
    llm_text_callback=None,
    on_max_iterations_callback=None,
) -> "SdkTransport":
    """Wrap the legacy 8-callback API into a single ``SdkTransport``.

    Called automatically by ``Agentao.__init__`` when old-style callbacks
    are passed without a ``transport`` argument.  All parameters are optional.
    """

    def _on_event(event: AgentEvent) -> None:
        t = event.type
        d = event.data
        if t == EventType.TURN_START:
            if step_callback:
                step_callback(None, {})
        elif t == EventType.TOOL_START:
            if step_callback:
                step_callback(d.get("tool"), d.get("args", {}))
        elif t == EventType.TOOL_OUTPUT:
            if output_callback:
                output_callback(d.get("tool", ""), d.get("chunk", ""))
        elif t == EventType.TOOL_COMPLETE:
            if tool_complete_callback:
                tool_complete_callback(d.get("tool", ""))
        elif t == EventType.THINKING:
            if thinking_callback:
                thinking_callback(d.get("text", ""))
        elif t == EventType.LLM_TEXT:
            if llm_text_callback:
                llm_text_callback(d.get("chunk", ""))

    def _confirm(name: str, desc: str, args: dict) -> bool:
        if confirmation_callback:
            return confirmation_callback(name, desc, args)
        return True

    def _ask(question: str) -> str:
        if ask_user_callback:
            return ask_user_callback(question)
        return "[ask_user: not available in non-interactive mode]"

    def _max_iter(count: int, messages: list) -> dict:
        if on_max_iterations_callback:
            return on_max_iterations_callback(count, messages)
        return {"action": "stop"}

    return SdkTransport(
        on_event=_on_event,
        confirm_tool=_confirm,
        ask_user=_ask,
        on_max_iterations=_max_iter,
    )
