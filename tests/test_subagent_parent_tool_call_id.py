"""A sub-agent run's public events name the parent's call that started it (#580).

A host saw the parent's ``agent_*`` call as a ``ToolLifecycleEvent`` and the
run as a ``SubagentLifecycleEvent``, with no field joining the two, so it
paired them by order. Every phase of the run now carries
``parent_tool_call_id``, equal to the ``tool_call_id`` of that call.

These tests drive the real wrapper through the real ``ToolExecutor``, so the
id reaches the wrapper the production way, and record the tool and sub-agent
events in one list.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, List

import pytest

from agentao.agent import Agentao
from agentao.agents.bg_store import BackgroundTaskStore
from agentao.cancellation import AgentCancelledError, CancellationToken
from agentao.host.models import SubagentLifecycleEvent, ToolLifecycleEvent
from agentao.host.projection import HostSubagentEmitter, HostToolEmitter
from agentao.outcome import TurnOutcome
from agentao.permissions import PermissionEngine
from agentao.runtime.tool_executor import ToolExecutor

from tests.support.host_events import NullTransport, make_plan


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows


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


class _Stream:
    def __init__(self) -> None:
        self.events: List[Any] = []
        self.settled = threading.Event()

    def publish(self, event: Any) -> None:
        self.events.append(event)
        if isinstance(event, SubagentLifecycleEvent) and event.phase != "spawned":
            self.settled.set()

    def tool(self) -> List[ToolLifecycleEvent]:
        return [e for e in self.events if isinstance(e, ToolLifecycleEvent)]

    def subagent(self) -> List[SubagentLifecycleEvent]:
        return [e for e in self.events if isinstance(e, SubagentLifecycleEvent)]


def _setup(wrapper):
    stream = _Stream()
    wrapper._subagent_emitter = HostSubagentEmitter(
        stream, parent_session_id_provider=lambda: "parent-s",
    )
    wrapper._event_sink = None
    executor = ToolExecutor(
        NullTransport(), logging.getLogger("test.parent_tool_call_id"),
        sandbox_policy=None,
        host_tool_emitter=HostToolEmitter(
            stream, session_id_provider=lambda: "parent-s",
            turn_id_provider=lambda: "t-1",
        ),
    )
    return stream, executor


def _patch_chat(monkeypatch, *, last_turn=None, raises: BaseException = None):
    def chat(self, *args, **kwargs):
        if raises is not None:
            raise raises
        self._last_turn_outcome = last_turn
        return "answer"

    monkeypatch.setattr(Agentao, "chat", chat)


def _run(executor, wrapper, call_id, **args):
    executor.execute_batch([
        make_plan(wrapper, args={"task": "x", **args}, call_id=call_id),
    ])


def _assert_joined(stream, call_id, phases):
    (started, *_) = stream.tool()
    assert started.tool_call_id == call_id
    runs = stream.subagent()
    assert [e.phase for e in runs] == phases
    assert {e.parent_tool_call_id for e in runs} == {call_id}


# ── foreground: every outcome ──────────────────────────────────────────────


def test_a_completed_run_names_its_call(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    stream, executor = _setup(wrapper)
    _patch_chat(monkeypatch)

    _run(executor, wrapper, "call-a")

    _assert_joined(stream, "call-a", ["spawned", "completed"])


def test_an_incomplete_run_names_its_call(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    stream, executor = _setup(wrapper)
    _patch_chat(monkeypatch, last_turn=TurnOutcome(
        text="", status="ok", incomplete_reason="no_output", tool_count=0,
    ))

    _run(executor, wrapper, "call-a")

    _assert_joined(stream, "call-a", ["spawned", "failed"])


def test_a_run_that_raises_names_its_call(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    stream, executor = _setup(wrapper)
    _patch_chat(monkeypatch, raises=RuntimeError("boom"))

    _run(executor, wrapper, "call-a")

    _assert_joined(stream, "call-a", ["spawned", "failed"])
    assert stream.tool()[-1].phase == "failed"


def test_a_run_that_raises_a_cancel_names_its_call(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    stream, executor = _setup(wrapper)
    _patch_chat(monkeypatch, raises=AgentCancelledError("user-cancel"))

    _run(executor, wrapper, "call-a")

    _assert_joined(stream, "call-a", ["spawned", "cancelled"])


def test_a_run_that_returns_cancelled_names_its_call(parent):
    """The real ``chat()`` returns ``status="cancelled"`` at its first check."""
    wrapper = parent.tools.tools["agent_generalist"]
    stream, executor = _setup(wrapper)
    token = CancellationToken()
    token.cancel("user-cancel")
    wrapper._cancellation_token = token

    _run(executor, wrapper, "call-a")

    _assert_joined(stream, "call-a", ["spawned", "cancelled"])


# ── the id is per call ─────────────────────────────────────────────────────


def test_two_calls_in_one_batch_each_name_their_own(parent, monkeypatch):
    """The executor runs them in parallel, serialized by the tool's lock; the
    id is set under that lock, so neither run reads the other's."""
    wrapper = parent.tools.tools["agent_generalist"]
    stream, executor = _setup(wrapper)
    _patch_chat(monkeypatch)

    executor.execute_batch([
        make_plan(wrapper, args={"task": "x"}, call_id="call-a"),
        make_plan(wrapper, args={"task": "y"}, call_id="call-b"),
    ])

    by_run = {}
    for e in stream.subagent():
        by_run.setdefault(e.child_task_id, set()).add(e.parent_tool_call_id)
    assert sorted(by_run.values(), key=sorted) == [{"call-a"}, {"call-b"}]


def test_a_later_call_without_the_executor_names_no_call(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    stream, executor = _setup(wrapper)
    _patch_chat(monkeypatch)

    _run(executor, wrapper, "call-a")
    stream.events.clear()
    wrapper.execute(task="y")

    runs = stream.subagent()
    assert [e.phase for e in runs] == ["spawned", "completed"]
    assert {e.parent_tool_call_id for e in runs} == {None}


def test_a_denied_call_publishes_no_run(parent, monkeypatch):
    from agentao.runtime.tool_planning import ToolCallDecision

    wrapper = parent.tools.tools["agent_generalist"]
    stream, executor = _setup(wrapper)
    _patch_chat(monkeypatch)

    executor.execute_batch([make_plan(
        wrapper, args={"task": "x"}, call_id="call-a",
        decision=ToolCallDecision.DENY,
    )])

    assert stream.subagent() == []
    assert wrapper._parent_tool_call_id is None


def test_a_stray_attribute_does_not_opt_a_tool_in(parent):
    """Only a class-level ``True`` opts in; an instance attribute, as on a
    mock, does not."""
    from tests.test_host_tool_events import _SyncEcho

    tool = _SyncEcho()
    tool._takes_parent_tool_call_id = True
    stream, executor = _setup(parent.tools.tools["agent_generalist"])

    executor.execute_batch([make_plan(tool, call_id="call-a")])

    assert not hasattr(tool, "_parent_tool_call_id")


# ── background ─────────────────────────────────────────────────────────────


def test_a_background_run_names_the_call_that_launched_it(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    wrapper._bg_store = BackgroundTaskStore()
    stream, executor = _setup(wrapper)
    _patch_chat(monkeypatch)

    _run(executor, wrapper, "call-a", run_in_background=True)
    assert stream.settled.wait(10)

    started, completed = stream.tool()
    assert completed.phase == "completed"  # the launching call ends at launch
    _assert_joined(stream, "call-a", ["spawned", "completed"])
    assert stream.subagent()[0].parent_task_id  # the background agent id


def test_a_background_run_cancelled_before_it_starts_names_its_call(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    store = BackgroundTaskStore()
    monkeypatch.setattr(store, "mark_running", lambda agent_id: False)
    wrapper._bg_store = store
    stream, executor = _setup(wrapper)
    _patch_chat(monkeypatch)

    _run(executor, wrapper, "call-a", run_in_background=True)
    assert stream.settled.wait(10)

    _assert_joined(stream, "call-a", ["spawned", "cancelled"])


def test_a_call_that_never_enters_execute_leaves_no_id(parent, monkeypatch):
    """Arguments ``execute()`` refuses raise before it reads the id; the
    executor clears it, so a later direct call reports none."""
    wrapper = parent.tools.tools["agent_generalist"]
    stream, executor = _setup(wrapper)
    _patch_chat(monkeypatch)

    _run(executor, wrapper, "call-a", bogus=1)
    assert stream.subagent() == []
    assert wrapper._parent_tool_call_id is None

    wrapper.execute(task="y")
    assert {e.parent_tool_call_id for e in stream.subagent()} == {None}
