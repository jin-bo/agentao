"""Every sub-agent run has its own session id, minted before ``spawned``.

``SubagentLifecycleEvent.child_session_id`` was always ``None``: the child's
session id came from its constructor, after ``spawned`` was published. The
wrapper now mints the id first, the child adopts it, and both lifecycle
events point at it. The events themselves stay in the parent's session
(``docs/design/subagent-event-channels.md`` §5.2, §5.3).

The child's own host events are checked through its real emitter: the
patched ``chat()`` publishes a tool event on the child's stream.
"""

from __future__ import annotations

import threading
from typing import Any, List

import pytest

from agentao.agent import Agentao
from agentao.agents.bg_store import BackgroundTaskStore
from agentao.host.models import SubagentLifecycleEvent
from agentao.host.projection import HostSubagentEmitter
from agentao.permissions import PermissionEngine


pytestmark = pytest.mark.usefixtures("isolated_home")


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
        self.events: List[SubagentLifecycleEvent] = []
        self.settled = threading.Event()

    def publish(self, event: Any) -> None:
        self.events.append(event)
        if event.phase != "spawned":
            self.settled.set()


def _watch(wrapper) -> _Stream:
    stream = _Stream()
    wrapper._subagent_emitter = HostSubagentEmitter(
        stream, parent_session_id_provider=lambda: "parent-s",
    )
    wrapper._event_sink = None
    return stream


def _patch_chat(monkeypatch, child_events: List[Any]):
    """The child publishes one tool event through its real emitter."""
    def chat(self, *args, **kwargs):
        self.add_host_event_observer(child_events.append)
        self._host_tool_emitter.started(tool_call_id="c1", tool_name="read_file")
        self._last_turn_outcome = None
        return "answer"

    monkeypatch.setattr(Agentao, "chat", chat)


def _assert_points_at_the_child(stream, child_events) -> str:
    spawned, terminal = stream.events
    assert spawned.phase == "spawned"
    child_session_id = spawned.child_session_id
    assert isinstance(child_session_id, str) and child_session_id
    assert terminal.child_session_id == child_session_id
    # The pointer events stay in the parent's session.
    assert spawned.session_id == terminal.session_id == "parent-s"
    # The child's own events are in the session the pointer names.
    assert child_events and {e.session_id for e in child_events} == {child_session_id}
    return child_session_id


def test_a_foreground_run_points_at_its_childs_session(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    stream = _watch(wrapper)
    child_events: List[Any] = []
    _patch_chat(monkeypatch, child_events)

    wrapper.execute(task="x")

    _assert_points_at_the_child(stream, child_events)


def test_a_background_run_points_at_its_childs_session(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    wrapper._bg_store = BackgroundTaskStore()
    stream = _watch(wrapper)
    child_events: List[Any] = []
    _patch_chat(monkeypatch, child_events)

    wrapper.execute(task="x", run_in_background=True)
    assert stream.settled.wait(10)

    _assert_points_at_the_child(stream, child_events)


def test_two_runs_have_different_child_sessions(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    stream = _watch(wrapper)
    _patch_chat(monkeypatch, [])

    wrapper.execute(task="x")
    wrapper.execute(task="y")

    first, second = {e.child_session_id for e in stream.events[:2]}, {
        e.child_session_id for e in stream.events[2:]
    }
    assert len(first) == len(second) == 1
    assert first != second
    assert parent._session_id not in first | second


def test_the_parents_session_is_unchanged(parent, monkeypatch):
    before = parent._session_id
    wrapper = parent.tools.tools["agent_generalist"]
    _watch(wrapper)
    _patch_chat(monkeypatch, [])

    wrapper.execute(task="x")

    assert parent._session_id == before


def test_a_session_id_is_adopted_once(parent):
    parent._adopt_session_id("s-1")
    assert parent._session_id == "s-1"
    assert parent.tool_runner._session_id == "s-1"
    with pytest.raises(RuntimeError, match="already adopted"):
        parent._adopt_session_id("s-2")
    assert parent._session_id == "s-1"


def test_a_session_id_is_not_adopted_after_a_turn_started(parent):
    """The real ``chat()`` starts a turn even when it is cancelled at once;
    events of that turn already carry the old id."""
    from agentao.cancellation import CancellationToken

    token = CancellationToken()
    token.cancel("user-cancel")
    before = parent._session_id
    parent.chat("x", cancellation_token=token)

    with pytest.raises(RuntimeError, match="after a turn started"):
        parent._adopt_session_id("s-1")
    assert parent._session_id == before
