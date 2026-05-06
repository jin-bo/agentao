"""Shared fixtures for host-event / tool-executor tests.

``NullTransport`` and ``make_plan`` were previously copy-pasted across
``test_cli_host_events.py``, ``test_host_tool_events.py``, and
``test_tool_executor_context_propagation.py``. Centralizing them here
keeps the surface stable as the executor / transport contracts evolve.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from agentao.runtime.tool_planning import ToolCallDecision, ToolCallPlan
from agentao.transport import AgentEvent, EventType


class NullTransport:
    """A no-op transport that satisfies the executor's protocol.

    Tests that don't care about transport-side events (CLI prompts,
    confirmations, max-iter dialogs) use this to keep the wiring
    minimal — the public host-event stream is the surface under test.
    """

    def emit(self, _event: Any) -> None:
        pass

    def confirm_tool(self, *_a: Any, **_kw: Any) -> bool:
        return True

    def ask_user(self, _q: Any) -> str:
        return ""

    def on_max_iterations(self, _c: Any, _m: Any) -> Dict[str, str]:
        return {"action": "stop"}


class CapturingTransport:
    """Transport that records every emitted event for later assertions.

    Drop-in for ``NullTransport`` whenever a test needs to inspect the
    event stream. ``by_type`` filters by ``EventType``; ``by_name``
    filters by the runtime ``hook_name`` payload field used by
    ``PLUGIN_HOOK_FIRED``.
    """

    def __init__(self) -> None:
        self.events: List[AgentEvent] = []

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)

    def confirm_tool(self, *_a: Any, **_kw: Any) -> bool:
        return True

    def ask_user(self, *_a: Any, **_kw: Any) -> str:
        return ""

    def on_max_iterations(self, *_a: Any, **_kw: Any) -> Dict[str, str]:
        return {"action": "stop"}

    def by_type(self, event_type: EventType) -> List[AgentEvent]:
        return [e for e in self.events if e.type == event_type]

    def hook_fired_events(self, hook_name: Optional[str] = None) -> List[AgentEvent]:
        events = self.by_type(EventType.PLUGIN_HOOK_FIRED)
        if hook_name is None:
            return events
        return [e for e in events if e.data.get("hook_name") == hook_name]


def make_plan(
    tool: Any,
    *,
    decision: ToolCallDecision = ToolCallDecision.ALLOW,
    args: Optional[Dict[str, Any]] = None,
    call_id: str = "call-1",
) -> ToolCallPlan:
    """Build a ``ToolCallPlan`` around ``tool`` for executor tests.

    Mirrors the shape the runtime gets from a real OpenAI tool-call
    response, with a ``SimpleNamespace`` standing in for the SDK's
    ``ChatCompletionMessageToolCall`` so tests don't need to import it.
    """
    tc = SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=tool.name, arguments="{}"),
    )
    return ToolCallPlan(
        tool_call=tc,
        function_name=tool.name,
        function_args=args or {},
        tool=tool,
        decision=decision,
    )
