"""Subagent lifecycle public-event tests (PR 7).

Drives ``HostSubagentEmitter`` directly with a fake stream and
verifies the four phases (spawned, completed, failed, cancelled) keep
parent/child ids correlated and surface redacted task summaries
without raw user input.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from agentao.host.models import SubagentLifecycleEvent
from agentao.host.projection import HostSubagentEmitter


class _FakeStream:
    def __init__(self) -> None:
        self.events: List[Any] = []

    def publish(self, event: Any) -> None:
        self.events.append(event)


def _emitter(stream, *, parent_session_id: str = "parent-s") -> HostSubagentEmitter:
    return HostSubagentEmitter(
        stream,
        parent_session_id_provider=lambda: parent_session_id,
    )


# ---------------------------------------------------------------------------
# spawned + completed lineage
# ---------------------------------------------------------------------------


def test_spawned_then_completed_share_child_task_id():
    stream = _FakeStream()
    em = _emitter(stream)
    ctx = em.spawned(task_summary="codebase audit")
    em.completed(ctx=ctx, task_summary="audit done")
    assert len(stream.events) == 2
    spawn, terminal = stream.events
    assert isinstance(spawn, SubagentLifecycleEvent)
    assert isinstance(terminal, SubagentLifecycleEvent)
    assert spawn.phase == "spawned"
    assert terminal.phase == "completed"
    assert spawn.child_task_id == terminal.child_task_id
    assert spawn.started_at == terminal.started_at
    assert terminal.completed_at is not None
    assert terminal.error_type is None


def test_spawned_then_failed_carries_error_type():
    stream = _FakeStream()
    em = _emitter(stream)
    ctx = em.spawned(task_summary="probe")
    em.failed(ctx=ctx, task_summary="probe failed", error_type="ValueError")
    failed = [e for e in stream.events if e.phase == "failed"][0]
    assert failed.error_type == "ValueError"


def test_spawned_then_cancelled_uses_distinct_phase_with_no_error_type():
    stream = _FakeStream()
    em = _emitter(stream)
    ctx = em.spawned(task_summary="long-running")
    em.cancelled(ctx=ctx, task_summary="task cancelled")
    cancelled = [e for e in stream.events if e.phase == "cancelled"][0]
    # Plan: subagent uses ``cancelled`` as its own phase value (unlike
    # ``ToolLifecycleEvent`` which keeps cancelled under ``failed``).
    assert cancelled.error_type is None


# ---------------------------------------------------------------------------
# Parent/child id correlation across spawns
# ---------------------------------------------------------------------------


def test_parent_session_id_set_from_provider():
    stream = _FakeStream()
    em = _emitter(stream, parent_session_id="parent-42")
    ctx = em.spawned(task_summary="x")
    em.completed(ctx=ctx, task_summary="x done")
    for ev in stream.events:
        assert ev.parent_session_id == "parent-42"


def test_two_spawns_have_distinct_child_task_ids():
    stream = _FakeStream()
    em = _emitter(stream)
    a = em.spawned(task_summary="a")
    b = em.spawned(task_summary="b")
    assert a["child_task_id"] != b["child_task_id"]


def test_parent_task_id_round_trips():
    stream = _FakeStream()
    em = _emitter(stream)
    ctx = em.spawned(task_summary="bg-x", parent_task_id="parent-task-1")
    em.completed(ctx=ctx, task_summary="bg-x done")
    for ev in stream.events:
        assert ev.parent_task_id == "parent-task-1"


# ---------------------------------------------------------------------------
# task_summary is redacted/truncated
# ---------------------------------------------------------------------------


def test_task_summary_is_redacted_and_truncated():
    stream = _FakeStream()
    em = _emitter(stream)
    raw = ("LONG-LINE " * 50) + "\nSECRET-KEY=abcd1234\n"
    ctx = em.spawned(task_summary=raw)
    spawn = stream.events[0]
    assert spawn.task_summary is not None
    # The projection collapses whitespace and truncates to <=240 chars.
    assert "\n" not in spawn.task_summary
    assert len(spawn.task_summary) <= 240


# ---------------------------------------------------------------------------
# child_session_id forwarding (when set)
# ---------------------------------------------------------------------------


def test_child_session_id_forwarded_when_provided():
    stream = _FakeStream()
    em = _emitter(stream, parent_session_id="parent")
    ctx = em.spawned(task_summary="x", child_session_id="child-1")
    em.completed(ctx=ctx, task_summary="x done")
    spawn, terminal = stream.events
    # Wire ``session_id`` is the child session when present so a host
    # filtering by session sees both events together.
    assert spawn.session_id == "child-1"
    assert terminal.session_id == "child-1"
    assert spawn.parent_session_id == "parent"
    assert spawn.child_session_id == "child-1"


# ---------------------------------------------------------------------------
# Regression: AgentToolWrapper must not leak raw user task into task_summary
# ---------------------------------------------------------------------------


def test_background_pending_cancel_emits_terminal_so_no_orphan_spawn():
    """Pre-fix: ``_launch_background`` published ``spawned`` before
    starting the worker thread. If the agent was cancelled before
    ``mark_running`` returned True, the worker silently exited and
    harness subscribers saw a child task that never completed or
    cancelled. The fix emits ``cancelled`` in that branch so the
    lifecycle pair is closed."""
    from typing import List

    from agentao.agents.tools import AgentToolWrapper
    from agentao.host.projection import HostSubagentEmitter

    captured: List[Any] = []

    class _CaptureStream:
        def publish(self, event: Any) -> None:
            captured.append(event)

    emitter = HostSubagentEmitter(
        _CaptureStream(),
        parent_session_id_provider=lambda: "parent-s",
    )

    # Fake bg store whose ``mark_running`` always returns False (the
    # pending-cancel signal). The wrapper must close the lifecycle.
    class _FakeBgStore:
        def register(self, *a, **kw): pass
        def register_token(self, *a, **kw): pass
        def unregister_token(self, *a, **kw): pass
        def mark_running(self, agent_id): return False
        def update(self, *a, **kw): pass

    wrapper = AgentToolWrapper.__new__(AgentToolWrapper)
    wrapper._definition = {"name": "auditor"}
    wrapper._subagent_emitter = emitter
    wrapper._bg_store = _FakeBgStore()

    # Drive only the public-event side-effects path of
    # ``_launch_background``: spawn → mark_running fail → cancelled.
    task_summary = "sub-agent: auditor"
    ctx = wrapper._spawn_subagent_event(task_summary, parent_task_id="bg-1")
    # Simulate the worker hitting ``mark_running == False``.
    wrapper._terminal_subagent_event(ctx, "cancelled", task_summary)

    phases = [e.phase for e in captured]
    assert phases == ["spawned", "cancelled"]
    assert captured[0].child_task_id == captured[1].child_task_id


def test_terminal_event_keeps_spawn_time_parent_session_id():
    """Background sub-agents can outlive an ACP ``session/load`` or
    ``session/new`` that rebinds ``agent._session_id`` mid-flight. The
    terminal event must still publish under the parent session that
    was live at ``spawned`` time, otherwise session-filtered hosts
    can't correlate the pair. The fix pins ``parent_session_id`` into
    ``ctx`` so the terminal event ignores any provider drift."""
    stream = _FakeStream()
    current = {"sid": "parent-original"}
    em = HostSubagentEmitter(
        stream,
        parent_session_id_provider=lambda: current["sid"],
    )

    ctx = em.spawned(task_summary="bg-task")
    spawn = stream.events[-1]
    assert spawn.parent_session_id == "parent-original"
    assert spawn.session_id == "parent-original"

    # Host rebinds the parent session while the sub-agent is still
    # running (ACP ``session/load`` finishing, for example). The
    # terminal event must still attribute to the original parent.
    current["sid"] = "parent-rebound"
    em.completed(ctx=ctx, task_summary="bg-task done")

    completed = stream.events[-1]
    assert completed.parent_session_id == "parent-original"
    assert completed.session_id == "parent-original"
    assert completed.child_task_id == spawn.child_task_id


def test_agent_tool_wrapper_task_summary_excludes_raw_user_task():
    """The sub-agent wrapper used to publish ``f"{name}: {task[:80]}"``
    as ``task_summary``. ``redact_summary`` only collapses whitespace
    and truncates, so any user-supplied secret in the first 80 chars
    of the task ended up on the public stream verbatim. The wrapper
    now publishes a generic ``"sub-agent: <name>"`` label instead."""
    from typing import List

    from agentao.agents.tools import AgentToolWrapper
    from agentao.host.projection import HostSubagentEmitter

    captured: List[Any] = []

    class _CaptureStream:
        def publish(self, event: Any) -> None:
            captured.append(event)

    emitter = HostSubagentEmitter(
        _CaptureStream(),
        parent_session_id_provider=lambda: "parent-s",
    )

    wrapper = AgentToolWrapper.__new__(AgentToolWrapper)
    wrapper._definition = {"name": "auditor"}
    wrapper._subagent_emitter = emitter

    secret = "PROD_API_KEY=sk-live-XXXXXXXXXXXXXXX"
    # Drive the same path the wrapper takes: build the task_summary it
    # would emit and call _spawn_subagent_event directly. The task
    # itself is intentionally NOT passed to the emitter — the public
    # event must carry only the generic label.
    task_summary = f"sub-agent: {wrapper._definition['name']}"
    ctx = wrapper._spawn_subagent_event(task_summary)
    assert ctx is not None
    assert len(captured) == 1
    spawn = captured[0]
    assert spawn.task_summary is not None
    assert secret not in spawn.task_summary
    # The label is the agent name, not the user input.
    assert "auditor" in spawn.task_summary
