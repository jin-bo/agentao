"""A sub-agent's tool events reach the parent with their own ``call_id``
and their real outcome — through the production forwarder.

Reported bug: a sub-agent running four concurrent ``read_file`` calls emitted
``tool_call`` events with distinct ``toolCallId``s (``call_5d6…``) but every
matching ``tool_complete`` carried ``toolCallId == "read_file"`` — the tool
name, not the call id — so a single tool invocation could not be traced
start→finish. The sub-agent then reported to the parent through legacy
callbacks that dropped the id and hardcoded ``status="ok"``.

A foreground sub-agent now gets an ``SdkTransport`` whose ``on_event`` is the
wrapper's whitelist forwarder (``docs/design/subagent-event-bridge.md`` §6).
These tests build that transport with the wrapper's own
``_child_transport``, from the entry points ``register_agent_tools`` really
passes, and emit into it the events a sub-agent's runtime emits.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

from agentao.agents.tools import AgentToolWrapper
from agentao.tooling.agent_tools import register_agent_tools
from agentao.transport import AgentEvent, EventType

from tests.support.host_events import CapturingTransport


def _registered_entry_points(parent_transport=None) -> Dict[str, Any]:
    """Run ``register_agent_tools`` against a fake parent and capture what it
    passes the factory (``event_sink`` / ``confirm_tool`` / ``ask_user``)."""
    captured: Dict[str, Any] = {}

    def _create_agent_tools(**kwargs):
        captured.update(kwargs)
        return []

    agent = SimpleNamespace(
        agent_manager=SimpleNamespace(create_agent_tools=_create_agent_tools),
        tools=SimpleNamespace(tools=[], register=lambda t: None),
        bg_store=None,
        transport=parent_transport or CapturingTransport(),
        context_manager=SimpleNamespace(max_tokens=100_000),
        _llm_config={},
        messages=[],
        _current_token=None,
        tool_runner=None,
    )
    register_agent_tools(agent)
    captured["_agent"] = agent
    return captured


def _wrapper(name="r", **entry_points) -> AgentToolWrapper:
    return AgentToolWrapper(
        definition={"name": name, "description": "d"},
        all_tools={},
        llm_config_getter=lambda: {},
        working_directory=Path("."),
        **entry_points,
    )


def _child(name="r", max_turns=15, *, background=False):
    """A sub-agent transport wired to a fake parent the production way.
    Returns ``(child_transport, parent_agent)``."""
    cap = _registered_entry_points()
    wrapper = _wrapper(
        name,
        event_sink=cap["event_sink"],
        confirm_tool=cap["confirm_tool"],
        ask_user=cap["ask_user"],
    )
    return wrapper._child_transport(background, max_turns), cap["_agent"]


# ── invariant 1: each row keeps its own call_id ───────────────────────────


def test_same_name_parallel_completions_keep_distinct_call_ids():
    child, parent = _child("basic-info-reviewer", 100)
    call_ids = ["call_5d6", "call_444", "call_008", "call_bd4"]

    # Four concurrent same-name calls start (same turn → identical prefix).
    child.emit(AgentEvent(EventType.TURN_START))
    for cid in call_ids:
        child.emit(AgentEvent(EventType.TOOL_START, {
            "tool": "read_file", "args": {"file_path": f"/x/{cid}.md"}, "call_id": cid,
        }))
    # …then each completes.
    for cid in call_ids:
        child.emit(AgentEvent(EventType.TOOL_COMPLETE, {
            "tool": "read_file", "call_id": cid, "status": "ok",
            "duration_ms": 0, "error": None,
        }))

    starts = parent.transport.by_type(EventType.TOOL_START)
    completes = parent.transport.by_type(EventType.TOOL_COMPLETE)
    assert [e.data["call_id"] for e in starts] == call_ids
    # The regression: completions mirror the starts, not the tool name.
    assert [e.data["call_id"] for e in completes] == call_ids
    # The prefixed display label rode along on the start events.
    assert all(e.data["tool"] == "[basic-info-reviewer 1/100] read_file" for e in starts)
    assert [e.data["args"] for e in starts] == [
        {"file_path": f"/x/{cid}.md"} for cid in call_ids
    ]


def test_confirmation_event_keeps_subagent_call_id():
    child, parent = _child()
    for cid in ("first", "second"):
        child.emit(AgentEvent(EventType.TOOL_CONFIRMATION, {
            "tool": "read_file", "call_id": cid, "args": {"file_path": cid},
        }))
    assert [e.data["call_id"] for e in parent.transport.by_type(EventType.TOOL_CONFIRMATION)] == [
        "first", "second",
    ]


def test_tool_output_carries_call_id():
    child, parent = _child()
    child.emit(AgentEvent(EventType.TOOL_OUTPUT, {
        "tool": "shell", "chunk": "hello\n", "call_id": "call_99",
    }))
    (out,) = parent.transport.by_type(EventType.TOOL_OUTPUT)
    assert out.data == {"tool": "shell", "chunk": "hello\n", "call_id": "call_99"}


# ── invariant 2: the real outcome, and today's defaults ───────────────────


def test_completion_forwards_real_status_and_error():
    # A failed sub-agent tool must surface as a failure, not a hardcoded "ok".
    child, parent = _child()
    child.emit(AgentEvent(EventType.TOOL_COMPLETE, {
        "tool": "shell", "call_id": "call_err", "status": "error",
        "duration_ms": 17, "error": "non-zero exit",
    }))
    (done,) = parent.transport.by_type(EventType.TOOL_COMPLETE)
    assert done.data == {
        "tool": "shell", "call_id": "call_err", "status": "error",
        "duration_ms": 17, "error": "non-zero exit",
    }


def test_completion_without_call_id_falls_back_to_name():
    # A non-normalized emitter that omits the fields gets the defaults the
    # parent always applied: the bare tool name, "ok", 0, no error.
    child, parent = _child()
    child.emit(AgentEvent(EventType.TOOL_COMPLETE, {"tool": "read_file"}))
    (done,) = parent.transport.by_type(EventType.TOOL_COMPLETE)
    assert done.data == {
        "tool": "read_file", "call_id": "read_file", "status": "ok",
        "duration_ms": 0, "error": None,
    }


def test_an_empty_id_is_kept_on_output_and_completion_but_not_on_start():
    """§6.3, kept exactly: only a missing id falls back on output and
    completion, while a start falls back on a missing or empty one — to
    the label, not the bare name."""
    child, parent = _child()
    child.emit(AgentEvent(EventType.TURN_START))
    child.emit(AgentEvent(EventType.TOOL_START, {"tool": "t", "args": {}, "call_id": ""}))
    child.emit(AgentEvent(EventType.TOOL_OUTPUT, {"tool": "t", "chunk": "x", "call_id": ""}))
    child.emit(AgentEvent(EventType.TOOL_OUTPUT, {"tool": "t", "chunk": "x"}))
    child.emit(AgentEvent(EventType.TOOL_COMPLETE, {"tool": "t", "call_id": ""}))

    (start,) = parent.transport.by_type(EventType.TOOL_START)
    assert start.data["call_id"] == "[r 1/15] t"
    assert [e.data["call_id"] for e in parent.transport.by_type(EventType.TOOL_OUTPUT)] == ["", "t"]
    assert parent.transport.by_type(EventType.TOOL_COMPLETE)[0].data["call_id"] == ""


def test_the_childs_event_is_not_changed_in_place():
    """The child's transport hands the same object to its own subscribers,
    so relabelling it would rename the call for them too."""
    child, _ = _child()
    seen: List[AgentEvent] = []
    child.subscribe(seen.append)
    child.emit(AgentEvent(EventType.TURN_START))
    child.emit(AgentEvent(EventType.TOOL_CONFIRMATION, {
        "tool": "read_file", "call_id": "c1", "args": {},
    }))
    child.emit(AgentEvent(EventType.TOOL_START, {
        "tool": "read_file", "call_id": "c1", "args": {},
    }))
    assert [e.data.get("tool") for e in seen[1:]] == ["read_file", "read_file"]


# ── invariant 3: one label for the opening and the start ──────────────────


def test_confirmation_opening_carries_the_subagent_label():
    # ACP opens the call from TOOL_CONFIRMATION and the prefixed TOOL_START
    # only updates it, so the label has to be on the opening — and be the
    # same label, turn counter included, that TOOL_START would have used.
    import io
    import json

    from agentao.acp.models import AcpSessionState
    from agentao.acp.server import AcpServer
    from agentao.acp.transport import ACPTransport

    out = io.StringIO()
    server = AcpServer(stdin=io.StringIO(), stdout=out)
    server.sessions.create(AcpSessionState(session_id="s"))
    acp = ACPTransport(server, "s")
    sent: List[AgentEvent] = []

    def sink(event):
        sent.append(event)
        acp.emit(event)

    child = _wrapper("reviewer", event_sink=sink)._child_transport(False, 15)
    child.emit(AgentEvent(EventType.TURN_START, {}))
    child.emit(AgentEvent(EventType.TURN_START, {}))
    child.emit(AgentEvent(EventType.TOOL_CONFIRMATION, {
        "tool": "run_shell_command", "call_id": "c1", "args": {"command": "ls"},
    }))
    child.emit(AgentEvent(EventType.TOOL_START, {
        "tool": "run_shell_command", "call_id": "c1", "args": {"command": "ls"},
    }))

    opening = json.loads(out.getvalue().splitlines()[0])["params"]["update"]
    assert opening["sessionUpdate"] == "tool_call"
    assert opening["title"] == "[reviewer 2/15] run_shell_command"
    (start,) = [e for e in sent if e.type is EventType.TOOL_START]
    assert start.data["tool"] == opening["title"]


def test_a_rejected_confirmation_does_not_advance_the_turn_label():
    # A rejection makes ToolRunner emit a TURN_START that only resets the
    # spinner. Counted as a turn, it retitled the rejected call itself
    # (1/15 at the prompt, 2/15 at its TOOL_START) and left every later
    # turn one ahead. Driven through the real ToolRunner and the production
    # child transport, so the flag has to survive every hop.
    import logging

    from agentao.runtime.tool_runner import ToolRunner
    from agentao.tools import Tool, ToolRegistry

    class _Asks(Tool):
        name = "write_thing"
        description = "write"
        parameters = {"type": "object"}
        requires_confirmation = True

        def execute(self, **kwargs) -> str:
            return "ok"

    sent: List[AgentEvent] = []
    asked: List[str] = []
    answers = iter([False, True, True])

    def confirm(name, desc, args):
        asked.append(name)
        return next(answers)

    child = _wrapper(
        "r", event_sink=sent.append, confirm_tool=confirm,
    )._child_transport(False, 15)
    registry = ToolRegistry()
    registry.register(_Asks())
    runner = ToolRunner(
        tools=registry, permission_engine=None, transport=child,
        logger=logging.getLogger("test.turn_label"),
    )

    def call(cid):
        return SimpleNamespace(
            # Distinct arguments: identical repeats trip the doom-loop guard.
            id=cid, function=SimpleNamespace(
                name="write_thing", arguments=f'{{"path": "{cid}"}}',
            ),
        )

    child.emit(AgentEvent(EventType.TURN_START, {}))  # LLM iteration 1
    runner.execute([call("a"), call("b")])
    child.emit(AgentEvent(EventType.TURN_START, {}))  # LLM iteration 2
    runner.execute([call("c")])

    def labels(kind):
        return [e.data["tool"] for e in sent if e.type is kind]

    first, second = "[r 1/15] write_thing", "[r 2/15] write_thing"
    assert labels(EventType.TOOL_CONFIRMATION) == [first, first, second]
    assert labels(EventType.TOOL_START) == [first, first, second]
    # The rejection's reset reached the parent as a reset, not a turn.
    assert {"display_reset": True} in [
        e.data for e in sent if e.type is EventType.TURN_START
    ]
    # invariant 5: the parent is asked under the sub-agent's name.
    assert asked == ["[r] write_thing"] * 3


# ── invariant 4 / 7: nothing from a background child, or without a sink ───


def _emit_a_tool_row(child):
    child.emit(AgentEvent(EventType.TURN_START))
    child.emit(AgentEvent(EventType.TOOL_CONFIRMATION, {
        "tool": "read_file", "call_id": "c1", "args": {"file_path": "c1"},
    }))
    child.emit(AgentEvent(EventType.TOOL_START, {
        "tool": "read_file", "call_id": "c1", "args": {"file_path": "c1"},
    }))
    child.emit(AgentEvent(EventType.TOOL_COMPLETE, {"tool": "read_file", "call_id": "c1"}))


def test_a_background_child_sends_the_parent_nothing_and_refuses():
    child, parent = _child(background=True)
    _emit_a_tool_row(child)
    assert parent.transport.events == []
    assert child.confirm_tool("write_file", "d", {}) is False


def test_a_foreground_child_without_a_sink_forwards_nothing():
    # The confirmation and the start are forwarded together or not at all:
    # ACP would open a call from the one that nothing closes.
    wrapper = _wrapper("r")
    assert wrapper._make_forwarder(15) is None
    _emit_a_tool_row(wrapper._child_transport(False, 15))  # and nothing raises


# ── invariant 5: confirmation and questions reach the parent's transport ──


class _AskingTransport(CapturingTransport):
    def __init__(self, answer=True):
        super().__init__()
        self.asked: List[Any] = []
        self._answer = answer

    def confirm_tool(self, name, desc, args):
        self.asked.append(name)
        return self._answer

    def ask_user(self, question, *, header=None, options=None, multiple=False, allow_custom=True):
        self.asked.append((question, header))
        return "answer"


def test_confirmation_and_questions_reach_the_parent_transport():
    parent_transport = _AskingTransport(answer=False)
    cap = _registered_entry_points(parent_transport)
    child = _wrapper(
        "r", confirm_tool=cap["confirm_tool"], ask_user=cap["ask_user"],
    )._child_transport(False, 15)

    assert child.confirm_tool("write_file", "d", {}) is False
    assert child.ask_user("q?", header="H") == "answer"
    assert parent_transport.asked == ["[r] write_file", ("q?", "H")]


# ── invariant 6: a whitelist ──────────────────────────────────────────────


def test_events_outside_the_whitelist_stay_with_the_child():
    child, parent = _child()
    for event_type in EventType:
        if event_type in (
            EventType.TURN_START, EventType.TOOL_START, EventType.TOOL_CONFIRMATION,
            EventType.TOOL_OUTPUT, EventType.TOOL_COMPLETE,
        ):
            continue
        child.emit(AgentEvent(event_type, {"tool": "x", "text": "y", "chunk": "z"}))
    assert parent.transport.events == []


# ── invariant 9: a sub-agent that runs out of turns stops ─────────────────


def test_running_out_of_turns_stops():
    child, _ = _child()
    assert child.on_max_iterations(15, []) == {"action": "stop"}
    background, _ = _child(background=True)
    assert background.on_max_iterations(15, []) == {"action": "stop"}


# ── invariant 11: the parent's transport is read per call ─────────────────


def test_a_transport_replaced_after_registration_gets_the_events():
    """Replay swaps ``agent.transport`` after the agent tools are registered
    (and back when it ends). A bound method would keep the old one."""
    first = _AskingTransport()
    cap = _registered_entry_points(first)
    wrapper = _wrapper(
        "r", event_sink=cap["event_sink"], confirm_tool=cap["confirm_tool"],
        ask_user=cap["ask_user"],
    )
    second = _AskingTransport()
    cap["_agent"].transport = second

    child = wrapper._child_transport(False, 15)
    child.emit(AgentEvent(EventType.TURN_START))
    child.confirm_tool("write_file", "d", {})
    child.ask_user("q?")
    wrapper._send_agent_end("completed")

    assert first.events == [] and first.asked == []
    assert [e.type for e in second.events] == [EventType.TURN_START, EventType.AGENT_END]
    assert second.asked == ["[r] write_file", ("q?", None)]
