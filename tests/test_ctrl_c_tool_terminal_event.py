"""Ctrl+C inside a tool closes the call's events with ``cancelled`` (codex #51556).

``TOOL_START`` and the host ``started`` fire before the tool runs. A
``KeyboardInterrupt`` from inside it is not an ``Exception``, so it went past
every branch that emits ``TOOL_COMPLETE`` and the host terminal event: ACP,
replay and the CLI saw a call that started and never ended. The executor now
emits ``cancelled`` once, then lets the interrupt go on to ``turn.py``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import pytest

from agentao.host.projection import HostToolEmitter
from agentao.runtime.tool_executor import ToolExecutor
from agentao.tools import Tool
from agentao.transport import EventType

from tests.support.host_events import CapturingTransport, make_plan


class _Interrupted(Tool):
    @property
    def name(self) -> str:
        return "slow"

    @property
    def description(self) -> str:
        return "raises KeyboardInterrupt, as Ctrl+C does on the chat thread"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object"}

    def execute(self, **_kwargs) -> str:
        raise KeyboardInterrupt


class _Fine(_Interrupted):
    @property
    def name(self) -> str:
        return "fine"

    def execute(self, **_kwargs) -> str:
        return "ok"


def _executor():
    host: List[Any] = []

    class _Stream:
        def publish(self, event):
            host.append(event)

    transport = CapturingTransport()
    executor = ToolExecutor(
        transport, logging.getLogger("test.ctrl_c"),
        host_tool_emitter=HostToolEmitter(
            _Stream(), session_id_provider=lambda: "s", turn_id_provider=lambda: "t",
        ),
    )
    return executor, transport, host


def _tool_events(transport, call_id):
    return [
        (e.type, e.data.get("status"))
        for e in transport.events
        if e.type in (EventType.TOOL_START, EventType.TOOL_COMPLETE)
        and e.data.get("call_id") == call_id
    ]


def test_ctrl_c_in_a_single_tool_call_ends_it_as_cancelled():
    executor, transport, host = _executor()

    with pytest.raises(KeyboardInterrupt):
        executor.execute_batch([make_plan(_Interrupted(), call_id="c1")])

    assert _tool_events(transport, "c1") == [
        (EventType.TOOL_START, None), (EventType.TOOL_COMPLETE, "cancelled"),
    ]
    # A cancel rides the ``failed`` phase; ``outcome`` names it.
    assert [(e.phase, e.outcome) for e in host] == [("started", None), ("failed", "cancelled")]
    assert host[1].started_at == host[0].started_at


def test_ctrl_c_in_a_serial_batch_ends_only_the_call_that_started():
    executor, transport, host = _executor()
    plans = [make_plan(_Interrupted(), call_id="c1"), make_plan(_Fine(), call_id="c2")]

    with pytest.raises(KeyboardInterrupt):
        executor.execute_batch(plans)

    assert _tool_events(transport, "c1") == [
        (EventType.TOOL_START, None), (EventType.TOOL_COMPLETE, "cancelled"),
    ]
    assert _tool_events(transport, "c2") == []          # never started, so nothing to end
    assert [(e.tool_call_id, e.phase, e.outcome) for e in host] == [
        ("c1", "started", None), ("c1", "failed", "cancelled"),
    ]
