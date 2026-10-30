"""Every transport event of a foreground sub-agent run carries its ``child_task_id`` (#578).

A host that shows sub-agent progress from transport events paired them by
agent name, which is correct only while two calls to the same sub-agent never
overlap. Each run now has one id, on ``AGENT_START``, on every forwarded child
event and on ``AGENT_END``, and the same id is on the run's public
``SubagentLifecycleEvent``.

These tests drive the real wrapper against a real parent. The patched
``chat()`` emits through the sub-agent's own transport, so the events take the
production forwarder.
"""

from __future__ import annotations

from typing import Any, List, Tuple

import pytest

from agentao.agent import Agentao
from agentao.cancellation import AgentCancelledError, CancellationToken
from agentao.host.projection import HostSubagentEmitter
from agentao.outcome import TurnOutcome
from agentao.permissions import PermissionEngine
from agentao.transport import AgentEvent, EventType

from tests.test_subagent_tool_call_id import _wrapper


pytestmark = pytest.mark.usefixtures("isolated_home", "isolated_skill_dirs")


class _Stream:
    def __init__(self, log: List[Tuple[str, Any]]) -> None:
        self._log = log

    def publish(self, event: Any) -> None:
        self._log.append(("public", event))


@pytest.fixture
def parent(tmp_path):
    (tmp_path / "user").mkdir(exist_ok=True)
    agent = Agentao(
        working_directory=tmp_path, api_key="k",
        base_url="https://test.local/v1", model="m",
        enable_builtin_agents=True,
        permission_engine=PermissionEngine(
            project_root=tmp_path, user_root=tmp_path / "user",
        ),
    )
    try:
        yield agent
    finally:
        agent.close()


def _watch(wrapper, *, emitter: Any = "real", start_raises: bool = False):
    """Record the events the wrapper sends the parent and, with an emitter,
    its public events, in one list: ``(type value | "public", payload)``."""
    log: List[Tuple[str, Any]] = []
    if emitter == "real":
        wrapper._subagent_emitter = HostSubagentEmitter(
            _Stream(log), parent_session_id_provider=lambda: "parent-s",
        )
    else:
        wrapper._subagent_emitter = emitter

    def sink(event):
        log.append((event.type.value, event.data))
        if start_raises and event.type is EventType.AGENT_START:
            raise RuntimeError("host sink broke")

    wrapper._event_sink = sink
    return log


_FORWARDED = {"turn_start", "tool_confirmation", "tool_start", "tool_output", "tool_complete"}


def _child_emits(self) -> None:
    """One child turn with one tool row: all five forwarded event types."""
    emit = self.transport.emit
    emit(AgentEvent(EventType.TURN_START))
    emit(AgentEvent(EventType.TOOL_CONFIRMATION, {
        "tool": "read_file", "call_id": "c1", "args": {},
    }))
    emit(AgentEvent(EventType.TOOL_START, {"tool": "read_file", "call_id": "c1", "args": {}}))
    emit(AgentEvent(EventType.TOOL_OUTPUT, {"tool": "read_file", "call_id": "c1", "chunk": "x"}))
    emit(AgentEvent(EventType.TOOL_COMPLETE, {"tool": "read_file", "call_id": "c1"}))
    # Rejected confirmation: a display reset, whose ``data`` was empty.
    emit(AgentEvent(EventType.TURN_START, {"display_reset": True}))


def _patch_chat(monkeypatch, *, last_turn=None, raises: BaseException = None):
    def chat(self, *args, **kwargs):
        _child_emits(self)
        if raises is not None:
            raise raises
        self._last_turn_outcome = last_turn
        return "answer"

    monkeypatch.setattr(Agentao, "chat", chat)


def _transport_events(log):
    return [(kind, data) for kind, data in log if kind != "public"]


def _ids(log):
    """The ``child_task_id`` of every transport event, in order."""
    return [data.get("child_task_id") for _, data in _transport_events(log)]


def _assert_one_id_on_every_event(log) -> str:
    events = _transport_events(log)
    kinds = [kind for kind, _ in events]
    assert kinds[0] == "agent_start" and kinds[-1] == "agent_end"
    assert _FORWARDED <= set(kinds)
    ids = set(_ids(log))
    assert len(ids) == 1, ids
    (child_task_id,) = ids
    assert isinstance(child_task_id, str) and child_task_id
    return child_task_id


def _public(log):
    return [payload for kind, payload in log if kind == "public"]


# ── one id per run, on every outcome ───────────────────────────────────────


def test_a_completed_run_carries_one_id_and_the_public_event_has_it(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    log = _watch(wrapper)
    _patch_chat(monkeypatch)

    wrapper.execute(task="x")

    child_task_id = _assert_one_id_on_every_event(log)
    assert _transport_events(log)[-1][1]["state"] == "completed"
    public = _public(log)
    assert [e.phase for e in public] == ["spawned", "completed"]
    assert {e.child_task_id for e in public} == {child_task_id}


def test_an_incomplete_run_carries_one_id(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    log = _watch(wrapper)
    _patch_chat(monkeypatch, last_turn=TurnOutcome(
        text="", status="ok", incomplete_reason="no_output", tool_count=0,
    ))

    wrapper.execute(task="x")

    child_task_id = _assert_one_id_on_every_event(log)
    assert _transport_events(log)[-1][1]["state"] == "failed"
    assert {e.child_task_id for e in _public(log)} == {child_task_id}


def test_a_run_that_raises_carries_one_id(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    log = _watch(wrapper)
    _patch_chat(monkeypatch, raises=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        wrapper.execute(task="x")

    child_task_id = _assert_one_id_on_every_event(log)
    assert _transport_events(log)[-1][1]["state"] == "failed"
    assert {e.child_task_id for e in _public(log)} == {child_task_id}


def test_a_run_that_raises_a_cancel_carries_one_id(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    log = _watch(wrapper)
    _patch_chat(monkeypatch, raises=AgentCancelledError("user-cancel"))

    with pytest.raises(AgentCancelledError):
        wrapper.execute(task="x")

    child_task_id = _assert_one_id_on_every_event(log)
    assert _transport_events(log)[-1][1]["state"] == "cancelled"
    assert {e.child_task_id for e in _public(log)} == {child_task_id}


def test_a_run_that_returns_cancelled_carries_one_id(parent):
    """The ordinary cancel: the real ``chat()`` returns ``status="cancelled"``
    at its first check, before any model request or forwarded event."""
    wrapper = parent.tools.tools["agent_generalist"]
    log = _watch(wrapper)
    token = CancellationToken()
    token.cancel("user-cancel")
    wrapper._cancellation_token = token

    wrapper.execute(task="x")

    events = _transport_events(log)
    assert [kind for kind, _ in events][::len(events) - 1] == ["agent_start", "agent_end"]
    assert events[-1][1]["state"] == "cancelled"
    (child_task_id,) = set(_ids(log))
    assert child_task_id
    assert {e.child_task_id for e in _public(log)} == {child_task_id}


# ── the id does not depend on the emitter ──────────────────────────────────


def test_without_an_emitter_the_id_is_still_there(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    log = _watch(wrapper, emitter=None)
    _patch_chat(monkeypatch)

    wrapper.execute(task="x")

    _assert_one_id_on_every_event(log)
    assert _public(log) == []


def test_with_an_emitter_that_raises_the_id_is_still_there(parent, monkeypatch):
    class _Broken:
        def spawned(self, **kwargs):
            raise RuntimeError("emitter broke")

    wrapper = parent.tools.tools["agent_generalist"]
    log = _watch(wrapper, emitter=_Broken())
    _patch_chat(monkeypatch)

    wrapper.execute(task="x")

    _assert_one_id_on_every_event(log)


def test_a_raising_start_sink_publishes_no_spawned(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    log = _watch(wrapper, start_raises=True)
    _patch_chat(monkeypatch)

    with pytest.raises(RuntimeError, match="host sink broke"):
        wrapper.execute(task="x")

    assert [kind for kind, _ in log] == ["agent_start"]
    assert log[0][1]["child_task_id"]


def test_two_calls_get_different_ids(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    log = _watch(wrapper)
    _patch_chat(monkeypatch)

    wrapper.execute(task="x")
    first = set(_ids(log))
    log.clear()
    wrapper.execute(task="y")
    second = set(_ids(log))

    assert len(first) == len(second) == 1
    assert first != second


# ── the forwarder ──────────────────────────────────────────────────────────


def test_interleaved_runs_keep_their_own_ids():
    """Two runs of the same sub-agent, driven alternately: each forwarder
    stamps its own id. The serialization lock is not lifted, so the two
    forwarders are driven directly."""
    seen: List[AgentEvent] = []
    wrapper = _wrapper("reviewer", event_sink=seen.append)
    a = wrapper._make_forwarder(15, child_task_id="run-a")
    b = wrapper._make_forwarder(15, child_task_id="run-b")

    for forward, call_id in ((a, "a1"), (b, "b1"), (a, "a2"), (b, "b2")):
        forward(AgentEvent(EventType.TOOL_START, {"tool": "read_file", "call_id": call_id}))
        forward(AgentEvent(EventType.TOOL_COMPLETE, {"tool": "read_file", "call_id": call_id}))

    by_call = {(e.data["call_id"], e.data["child_task_id"]) for e in seen}
    assert by_call == {
        ("a1", "run-a"), ("a2", "run-a"), ("b1", "run-b"), ("b2", "run-b"),
    }


def test_the_forwarder_sets_the_id_over_the_childs_and_leaves_the_child_event_alone():
    seen: List[AgentEvent] = []
    wrapper = _wrapper("reviewer", event_sink=seen.append)
    forward = wrapper._make_forwarder(15, child_task_id="run-a")
    original = AgentEvent(EventType.TOOL_CONFIRMATION, {
        "tool": "read_file", "call_id": "c1", "child_task_id": "spoofed",
    })

    forward(original)

    (sent,) = seen
    assert sent.data["child_task_id"] == "run-a"
    assert sent.data["tool"] == "[reviewer 0/15] read_file"
    assert sent.data["call_id"] == "c1"
    # The child's subscribers hold the same object; it is not changed.
    assert original.data == {
        "tool": "read_file", "call_id": "c1", "child_task_id": "spoofed",
    }


def test_the_parents_own_events_carry_no_id(parent, monkeypatch):
    """Only forwarded child events and the run's start and end carry the id."""
    seen: List[AgentEvent] = []
    parent.transport.emit = seen.append
    wrapper = parent.tools.tools["agent_generalist"]
    wrapper._subagent_emitter = None
    _patch_chat(monkeypatch)

    parent.transport.emit(AgentEvent(EventType.TOOL_START, {"tool": "agent_generalist", "call_id": "p1"}))
    wrapper.execute(task="x")

    assert "child_task_id" not in seen[0].data
    assert all("child_task_id" in e.data for e in seen[1:])
