"""A sub-agent's host events on the parent's stream, in the child's session.

``Agentao(relay_subagent_events=True)`` republishes every host event a
sub-agent publishes on the parent's ``events()`` stream, unchanged, with the
run's ``child_session_id`` as its ``session_id``. Off by default. Replay does
not record relayed events (``docs/design/subagent-event-channels.md``
§5.4–§5.5, §9).

The child runs its real chat loop and ``ToolRunner``: only the LLM is stubbed,
answering once with a parallel batch of two ``read_file`` calls (both
read-only, so they run on pool workers) and then with text.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, List

import pytest
from openai.types.chat import ChatCompletion

from agentao.agent import Agentao
from agentao.agents.bg_store import BackgroundTaskStore
from agentao.agents.tools import AgentToolWrapper
from agentao.host.events import EventStream
from agentao.host.models import (
    PermissionDecisionEvent,
    SubagentLifecycleEvent,
    ToolLifecycleEvent,
)
from agentao.host.projection import SubagentRelay
from agentao.llm.client import LLMClient
from agentao.permissions import PermissionEngine


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows


def _response(content=None, calls=()):
    return ChatCompletion.model_validate({
        "id": "r", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{
            "index": 0,
            "finish_reason": "tool_calls" if calls else "stop",
            "message": {
                "role": "assistant", "content": content,
                "tool_calls": [
                    {"id": cid, "type": "function",
                     "function": {"name": "read_file", "arguments": json.dumps({"file_path": "a.txt"})}}
                    for cid in calls
                ] or None,
            },
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })


@pytest.fixture(autouse=True)
def _scripted_llm(monkeypatch):
    """Every child: a parallel batch of two reads, then an answer."""
    calls: dict = {}
    lock = threading.Lock()

    def chat_stream(self, **kwargs):
        with lock:
            n = calls.get(id(self), 0)
            calls[id(self)] = n + 1
        if n == 0:
            return _response(calls=("k1", "k2"))
        return _response(content="done")

    monkeypatch.setattr(LLMClient, "chat_stream", chat_stream)


def _parent(tmp_path, *, relay=True, bg=False):
    (tmp_path / "a.txt").write_text("hi")
    (tmp_path / "user").mkdir(exist_ok=True)
    return Agentao(
        working_directory=tmp_path, api_key="k",
        base_url="https://test.local/v1", model="m",
        enable_builtin_agents=True,
        permission_engine=PermissionEngine(
            project_root=tmp_path, user_root=tmp_path / "user",
        ),
        bg_store=BackgroundTaskStore() if bg else None,
        relay_subagent_events=relay,
    )


@pytest.fixture
def parent(tmp_path):
    agent = _parent(tmp_path)
    try:
        yield agent
    finally:
        agent.close()


def _record(agent) -> List[Any]:
    seen: List[Any] = []
    agent.add_host_event_observer(seen.append)
    return seen


def _lifecycle(events):
    return [e for e in events if isinstance(e, SubagentLifecycleEvent)]


def _relayed(events, child_session_id):
    return [e for e in events if getattr(e, "session_id", None) == child_session_id]


def _wait_terminal(events, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(e.phase != "spawned" for e in _lifecycle(events)):
            return
        time.sleep(0.01)
    raise AssertionError("no terminal SubagentLifecycleEvent")


def _wrapper(agent) -> AgentToolWrapper:
    return agent.tools.tools["agent_generalist"]


# ── relay off / on ─────────────────────────────────────────────────────────


def test_off_by_default_the_parent_sees_no_child_event(tmp_path):
    agent = _parent(tmp_path, relay=False)
    try:
        seen = _record(agent)
        _wrapper(agent).execute(task="x")
        assert {type(e) for e in seen} == {SubagentLifecycleEvent}
        assert {e.session_id for e in seen} == {agent._session_id}
    finally:
        agent.close()


def test_the_switch_must_be_a_bool(tmp_path):
    with pytest.raises(TypeError, match="relay_subagent_events"):
        Agentao(working_directory=tmp_path, api_key="k", model="m",
                base_url="https://test.local/v1", relay_subagent_events="yes")


def test_a_foreground_run_relays_its_tool_and_permission_events(parent):
    seen = _record(parent)

    _wrapper(parent).execute(task="x")

    spawned, terminal = _lifecycle(seen)
    child = spawned.child_session_id
    relayed = _relayed(seen, child)
    tools = [e for e in relayed if isinstance(e, ToolLifecycleEvent)]
    decisions = [e for e in relayed if isinstance(e, PermissionDecisionEvent)]
    assert sorted(e.phase for e in tools) == ["completed", "completed", "started", "started"]
    assert {e.tool_name for e in tools} == {"read_file"}
    assert len(decisions) == 2
    # Nothing else: every child-session event is a relayed one.
    assert len(relayed) == len(seen) - 2
    # Order: spawned, the child's events, the terminal event.
    assert seen[0] is spawned and seen[-1] is terminal


def test_a_background_run_relays_its_events(tmp_path):
    agent = _parent(tmp_path, bg=True)
    try:
        seen = _record(agent)
        _wrapper(agent).execute(task="x", run_in_background=True)
        _wait_terminal(seen)

        spawned, terminal = _lifecycle(seen)
        relayed = _relayed(seen, spawned.child_session_id)
        assert len([e for e in relayed if isinstance(e, ToolLifecycleEvent)]) == 4
        assert seen[0] is spawned and seen[-1] is terminal
    finally:
        agent.close()


def test_the_relay_forwards_any_event_unchanged():
    """A new public event type needs no relay change."""
    parent_stream = EventStream()
    seen: List[Any] = []
    parent_stream.add_observer(seen.append)
    child_stream = EventStream()

    class _Child:
        add_host_event_observer = staticmethod(child_stream.add_observer)
        remove_host_event_observer = staticmethod(child_stream.remove_observer)

    relay = SubagentRelay(parent_stream, enabled=lambda: True)
    handle = relay.attach(_Child(), "child-s")
    synthetic = object()
    child_stream.publish(synthetic)
    relay.detach(handle)
    child_stream.publish(object())

    assert seen == [synthetic]


# ── session filters and ordering (off-loop, through ``events()``) ─────────


def _collect(agent, run, *, session_id=None, queue_size=None, slow=0.0):
    """Subscribe, run ``run`` on a thread, and collect until the run's
    terminal event (or, filtered to a child session, a short quiet spell)."""
    if queue_size is not None:
        agent._host_events._max_queue_size = queue_size

    async def main():
        got: List[Any] = []
        agen = agent.events(session_id=session_id)
        first = asyncio.ensure_future(agen.__anext__())
        while not agent._host_events._subscribers:
            await asyncio.sleep(0)
        worker = threading.Thread(target=run)
        worker.start()
        pending = first
        while True:
            try:
                event = await asyncio.wait_for(pending, timeout=2.0)
            except asyncio.TimeoutError:
                break
            got.append(event)
            if isinstance(event, SubagentLifecycleEvent) and event.phase != "spawned":
                break
            if slow:
                await asyncio.sleep(slow)
            pending = asyncio.ensure_future(agen.__anext__())
        await agen.aclose()
        worker.join(10)
        return got

    return asyncio.run(main())


def test_a_parent_filtered_subscriber_sees_no_relayed_event(parent):
    got = _collect(parent, lambda: _wrapper(parent).execute(task="x"),
                   session_id=parent._session_id)
    assert [type(e) for e in got] == [SubagentLifecycleEvent, SubagentLifecycleEvent]


@pytest.mark.parametrize("queue_size,slow", [(None, 0.0), (1, 0.01)])
def test_spawned_then_child_events_then_terminal_for_a_none_subscriber(parent, queue_size, slow):
    """Also with a queue of one and a consumer slower than the child, so the
    full-queue path is taken."""
    got = _collect(parent, lambda: _wrapper(parent).execute(task="x"),
                   queue_size=queue_size, slow=slow)
    assert isinstance(got[0], SubagentLifecycleEvent) and got[0].phase == "spawned"
    assert isinstance(got[-1], SubagentLifecycleEvent) and got[-1].phase == "completed"
    middle = got[1:-1]
    assert len(middle) == 6
    assert {e.session_id for e in middle} == {got[0].child_session_id}


def test_background_ordering_for_a_none_subscriber(tmp_path):
    agent = _parent(tmp_path, bg=True)
    try:
        got = _collect(
            agent, lambda: _wrapper(agent).execute(task="x", run_in_background=True),
            queue_size=1, slow=0.01,
        )
        assert got[0].phase == "spawned" and got[-1].phase == "completed"
        assert {e.session_id for e in got[1:-1]} == {got[0].child_session_id}
        assert len(got) == 8
    finally:
        agent.close()


# ── the detach boundary ────────────────────────────────────────────────────


def _trace(monkeypatch, wrapper, log):
    relay = wrapper._subagent_relay
    real_detach, real_close = relay.detach, wrapper._close_sub_agent
    real_terminal = wrapper._terminal_subagent_event

    def detach(handle):
        if handle is not None and not handle.detached:
            log.append("detach")
        real_detach(handle)

    def close(sub_agent):
        log.append("close")
        real_close(sub_agent)

    def terminal(*args, **kwargs):
        log.append("terminal")
        real_terminal(*args, **kwargs)

    monkeypatch.setattr(relay, "detach", detach)
    monkeypatch.setattr(wrapper, "_close_sub_agent", close)
    monkeypatch.setattr(wrapper, "_terminal_subagent_event", terminal)


def _raising_drive(monkeypatch, exc):
    real = AgentToolWrapper._drive_sub_agent

    def drive(self, sub_agent, **kwargs):
        real(self, sub_agent, **kwargs)
        raise exc

    monkeypatch.setattr(AgentToolWrapper, "_drive_sub_agent", drive)


@pytest.mark.parametrize("outcome", ["ok", "error", "cancel"])
def test_foreground_detaches_before_the_close_and_the_terminal_event(parent, monkeypatch, outcome):
    from agentao.cancellation import AgentCancelledError

    wrapper = _wrapper(parent)
    log: List[str] = []
    _trace(monkeypatch, wrapper, log)
    if outcome == "error":
        _raising_drive(monkeypatch, RuntimeError("boom"))
    elif outcome == "cancel":
        _raising_drive(monkeypatch, AgentCancelledError("user-cancel"))
    seen = _record(parent)

    try:
        wrapper.execute(task="x")
    except (RuntimeError, AgentCancelledError):
        pass

    assert log == ["detach", "close", "terminal"]
    assert seen[-1] is _lifecycle(seen)[-1]
    assert not wrapper._subagent_relay._live


@pytest.mark.parametrize("outcome", ["ok", "incomplete", "error", "cancel"])
def test_background_detaches_before_the_terminal_event_and_the_close(tmp_path, monkeypatch, outcome):
    from agentao.cancellation import AgentCancelledError

    agent = _parent(tmp_path, bg=True)
    try:
        wrapper = _wrapper(agent)
        log: List[str] = []
        _trace(monkeypatch, wrapper, log)
        if outcome == "incomplete":
            # The child answers with nothing: ``failed`` with ``incomplete:``.
            monkeypatch.setattr(
                LLMClient, "chat_stream", lambda self, **kw: _response(content=""),
            )
        elif outcome == "error":
            _raising_drive(monkeypatch, RuntimeError("boom"))
        elif outcome == "cancel":
            _raising_drive(monkeypatch, AgentCancelledError("user-cancel"))
        seen = _record(agent)

        wrapper.execute(task="x", run_in_background=True)
        _wait_terminal(seen)
        deadline = time.monotonic() + 10
        while "close" not in log and time.monotonic() < deadline:
            time.sleep(0.01)

        assert log == ["detach", "terminal", "close"]
        assert seen[-1] is _lifecycle(seen)[-1]
        assert not wrapper._subagent_relay._live
        if outcome == "incomplete":
            terminal = _lifecycle(seen)[-1]
            assert terminal.phase == "failed"
            assert terminal.error_type.startswith("incomplete:")
    finally:
        agent.close()


def test_an_incomplete_run_detaches_too(parent, monkeypatch):
    """The child answers with nothing: ``failed`` with ``incomplete:``."""
    monkeypatch.setattr(
        LLMClient, "chat_stream", lambda self, **kw: _response(content=""),
    )
    wrapper = _wrapper(parent)
    log: List[str] = []
    _trace(monkeypatch, wrapper, log)
    seen = _record(parent)

    wrapper.execute(task="x")

    assert _lifecycle(seen)[-1].phase == "failed"
    assert log == ["detach", "close", "terminal"]


# ── the skip set ───────────────────────────────────────────────────────────


def test_the_session_is_registered_before_attach_and_unregistered_after_detach():
    relay = SubagentRelay(EventStream(), enabled=lambda: True)
    states = []

    class _Child:
        def add_host_event_observer(self, cb):
            states.append(("attach", relay.is_relayed("c")))

        def remove_host_event_observer(self, cb):
            states.append(("detach", relay.is_relayed("c")))

    handle = relay.attach(_Child(), "c")
    relay.detach(handle)
    relay.detach(handle)  # idempotent

    assert states == [("attach", True), ("detach", True)]
    assert not relay.is_relayed("c")


def test_an_attach_that_raises_unregisters():
    relay = SubagentRelay(EventStream(), enabled=lambda: True)

    class _Child:
        def add_host_event_observer(self, cb):
            raise RuntimeError("no")

    with pytest.raises(RuntimeError):
        relay.attach(_Child(), "c")
    assert not relay.is_relayed("c")


def test_a_publisher_holding_a_stale_snapshot_relays_nothing_after_detach():
    # ``EventStream.publish`` fires observers from a snapshot, so the relay
    # callback can still be called after ``remove_observer``.
    parent_stream = EventStream()
    seen = []
    parent_stream.add_observer(seen.append)
    relay = SubagentRelay(parent_stream, enabled=lambda: True)
    held = []

    class _Child:
        def add_host_event_observer(self, cb):
            held.append(cb)

        def remove_host_event_observer(self, cb):
            pass

    handle = relay.attach(_Child(), "c")
    held[0]("before")
    relay.detach(handle)
    held[0]("after")
    assert seen == ["before"]


def test_off_nothing_is_registered():
    relay = SubagentRelay(EventStream(), enabled=lambda: False)
    assert relay.attach(object(), "c") is None
    assert not relay.is_relayed("c")


def test_several_background_runs_alive_at_once_each_unregister(tmp_path, monkeypatch):
    agent = _parent(tmp_path, bg=True)
    release = threading.Event()
    real = AgentToolWrapper._drive_sub_agent

    def drive(self, sub_agent, **kwargs):
        release.wait(10)
        return real(self, sub_agent, **kwargs)

    monkeypatch.setattr(AgentToolWrapper, "_drive_sub_agent", drive)
    try:
        seen = _record(agent)
        relay = agent._subagent_relay
        for _ in range(3):
            _wrapper(agent).execute(task="x", run_in_background=True)
        deadline = time.monotonic() + 10
        while len(relay._live) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(relay._live) == 3
        release.set()
        deadline = time.monotonic() + 10
        while len([e for e in _lifecycle(seen) if e.phase != "spawned"]) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not relay._live
    finally:
        release.set()
        agent.close()


def test_a_raising_relay_does_not_fail_the_run(parent, monkeypatch):
    stream = parent._host_events
    real = stream.publish
    child_sessions = set()

    def publish(event):
        if getattr(event, "session_id", None) in child_sessions:
            raise RuntimeError("parent stream broke")
        if isinstance(event, SubagentLifecycleEvent) and event.child_session_id:
            child_sessions.add(event.child_session_id)
        real(event)

    monkeypatch.setattr(stream, "publish", publish)
    seen = _record(parent)

    text = _wrapper(parent).execute(task="x")

    assert text.startswith("done")
    assert [e.phase for e in _lifecycle(seen)] == ["spawned", "completed"]


# ── replay ─────────────────────────────────────────────────────────────────


def _replay_rows(tmp_path, *, relay, bg):
    from agentao.replay import ReplayReader, save_replay_enabled

    wd = tmp_path / ("on" if relay else "off")
    wd.mkdir()
    save_replay_enabled(True, wd)
    agent = _parent(wd, relay=relay, bg=bg)
    try:
        agent.reload_replay_config()
        path = agent.start_replay("sess")
        seen = _record(agent)
        _wrapper(agent).execute(task="x", run_in_background=bg)
        if bg:
            _wait_terminal(seen)
            time.sleep(0.1)
        agent.end_replay()
    finally:
        agent.close()
    rows = []
    for e in ReplayReader(path).events():
        p = e.get("payload") or {}
        rows.append((e["kind"], p.get("event_type"), p.get("phase"), p.get("tool_name")))
    return rows


@pytest.mark.parametrize("bg", [False, True])
def test_replay_records_the_same_rows_with_the_relay_on(tmp_path, bg):
    """A parallel child batch (pool workers) and a background child are the
    two cases whose relayed rows could not be put on the right turn."""
    off = _replay_rows(tmp_path, relay=False, bg=bg)
    on = _replay_rows(tmp_path, relay=True, bg=bg)
    # Compared as multisets: the two reads of the child's batch run on pool
    # workers, so their rows interleave in either order.
    assert sorted(on, key=repr) == sorted(off, key=repr)
    assert ("subagent_lifecycle", "subagent_lifecycle", "spawned", None) in on
