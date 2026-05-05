"""Shared fixtures for host-event / tool-executor tests.

``NullTransport`` and ``make_plan`` were previously copy-pasted across
``test_cli_host_events.py``, ``test_host_tool_events.py``, and
``test_tool_executor_context_propagation.py``. Centralizing them here
keeps the surface stable as the executor / transport contracts evolve.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Optional

from agentao.runtime.tool_planning import ToolCallDecision, ToolCallPlan


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
