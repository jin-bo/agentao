"""Regression: sub-agent TOOL_COMPLETE must carry the real ``call_id``
(and the real status/error), not the tool name / a hardcoded success.

Reported bug: a sub-agent running four concurrent ``read_file`` calls emitted
``tool_call`` events with distinct ``toolCallId``s (``call_5d6…``) but every
matching ``tool_complete`` carried ``toolCallId == "read_file"`` — the tool
name, not the call id — so a single tool invocation could not be traced
start→finish.

Two compounding causes, both fixed:

1. ``build_compat_transport`` dropped ``call_id`` (and status/error) on the
   TOOL_COMPLETE / TOOL_OUTPUT translation, forcing the sub-agent bridge to
   reconstruct the id from a tool-*name*-keyed dict and to hardcode
   ``status="ok"``.
2. That dict was written under the *prefixed* label on TOOL_START but read
   under the *raw* name on TOOL_COMPLETE, so the lookup never hit (and even
   when it did, same-named parallel calls collided onto one key).

This test drives the real bridge wiring in ``register_agent_tools`` the way a
sub-agent's events flow through ``build_compat_transport`` — including the real
production prefixed step callback — and asserts each completion keeps its own
id and faithful status.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

from agentao.agents.tools import AgentToolWrapper
from agentao.tooling.agent_tools import register_agent_tools
from agentao.transport import AgentEvent, EventType
from agentao.transport.sdk import build_compat_transport

from tests.support.host_events import CapturingTransport


def _build_bridge_callbacks() -> Dict[str, Any]:
    """Run ``register_agent_tools`` against a fake parent and capture the
    sub-agent callbacks it wires (step / output / tool_complete)."""
    captured: Dict[str, Any] = {}

    def _create_agent_tools(**kwargs):
        captured.update(kwargs)
        return []

    parent_transport = CapturingTransport()
    agent = SimpleNamespace(
        agent_manager=SimpleNamespace(create_agent_tools=_create_agent_tools),
        tools=SimpleNamespace(tools=[], register=lambda t: None),
        bg_store=None,
        transport=parent_transport,
        context_manager=SimpleNamespace(max_tokens=100_000),
        _llm_config={},
        messages=[],
        _current_token=None,
        tool_runner=None,
    )

    register_agent_tools(agent)
    captured["_parent_transport"] = parent_transport
    return captured


def _real_prefixed_step_cb(step_cb, agent_name: str, max_turns: int):
    """The real production prefixed step callback (not a hand-copy), so a
    drift in the label format / turn counting is exercised here too."""
    wrapper = AgentToolWrapper(
        definition={"name": agent_name, "description": "d"},
        all_tools={},
        llm_config_getter=lambda: {},
        working_directory=Path("."),
        step_callback=step_cb,
    )
    return wrapper._make_prefixed_step_callback(max_turns)


def test_same_name_parallel_completions_keep_distinct_call_ids():
    cap = _build_bridge_callbacks()
    parent = cap["_parent_transport"]

    # Wire the captured callbacks into the real legacy bridge, exactly as the
    # sub-agent's Agentao.__init__ does — including the real prefixed step
    # callback (TOOL_START labels get the prefix; tool_complete keeps the raw
    # name, the exact asymmetry that broke name-keyed correlation).
    sub_transport = build_compat_transport(
        step_callback=_real_prefixed_step_cb(cap["step_callback"], "basic-info-reviewer", 100),
        output_callback=cap["output_callback"],
        tool_complete_callback=cap["tool_complete_callback"],
    )

    call_ids = ["call_5d6", "call_444", "call_008", "call_bd4"]

    # Four concurrent same-name calls start (same turn → identical prefix).
    sub_transport.emit(AgentEvent(EventType.TURN_START))
    for cid in call_ids:
        sub_transport.emit(AgentEvent(EventType.TOOL_START, {
            "tool": "read_file", "args": {"file_path": f"/x/{cid}.md"}, "call_id": cid,
        }))
    # …then each completes.
    for cid in call_ids:
        sub_transport.emit(AgentEvent(EventType.TOOL_COMPLETE, {
            "tool": "read_file", "call_id": cid, "status": "ok",
            "duration_ms": 0, "error": None,
        }))

    starts = parent.by_type(EventType.TOOL_START)
    completes = parent.by_type(EventType.TOOL_COMPLETE)

    assert [e.data["call_id"] for e in starts] == call_ids
    # The regression: completions must mirror the starts, not collapse to
    # the tool name.
    assert [e.data["call_id"] for e in completes] == call_ids
    assert all(e.data["call_id"] != "read_file" for e in completes)
    # And the prefixed display label rode along on the start events.
    assert all("[basic-info-reviewer 1/100] read_file" == e.data["tool"] for e in starts)


def test_completion_forwards_real_status_and_error():
    # A failed sub-agent tool must surface as a failure, not a hardcoded "ok".
    cap = _build_bridge_callbacks()
    parent = cap["_parent_transport"]
    sub_transport = build_compat_transport(
        tool_complete_callback=cap["tool_complete_callback"],
    )

    sub_transport.emit(AgentEvent(EventType.TOOL_COMPLETE, {
        "tool": "shell", "call_id": "call_err", "status": "error",
        "duration_ms": 17, "error": "non-zero exit",
    }))

    (done,) = parent.by_type(EventType.TOOL_COMPLETE)
    assert done.data["call_id"] == "call_err"
    assert done.data["status"] == "error"
    assert done.data["duration_ms"] == 17
    assert done.data["error"] == "non-zero exit"


def test_completion_without_call_id_falls_back_to_name():
    # Floor case: a non-normalized emitter that omits call_id falls back to
    # the tool name (preserving the old behaviour) and defaults status to ok.
    cap = _build_bridge_callbacks()
    parent = cap["_parent_transport"]
    sub_transport = build_compat_transport(
        tool_complete_callback=cap["tool_complete_callback"],
    )

    sub_transport.emit(AgentEvent(EventType.TOOL_COMPLETE, {"tool": "read_file"}))

    (done,) = parent.by_type(EventType.TOOL_COMPLETE)
    assert done.data["call_id"] == "read_file"
    assert done.data["status"] == "ok"
    assert done.data["error"] is None


def test_tool_output_carries_call_id():
    cap = _build_bridge_callbacks()
    parent = cap["_parent_transport"]
    sub_transport = build_compat_transport(
        output_callback=cap["output_callback"],
    )

    sub_transport.emit(AgentEvent(EventType.TOOL_OUTPUT, {
        "tool": "shell", "chunk": "hello\n", "call_id": "call_99",
    }))

    (out,) = parent.by_type(EventType.TOOL_OUTPUT)
    assert out.data["call_id"] == "call_99"
