"""Permission decision public-event tests (PR 6).

Drives the runtime ``ToolRunner`` directly, pumping a real
``HostPermissionEmitter`` and ``HostToolEmitter`` over the same
fake stream so we can assert ordering: the
``PermissionDecisionEvent`` for one ``tool_call_id`` must precede the
matching ``ToolLifecycleEvent(phase="started")``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from agentao.host.models import (
    ActivePermissions,
    PermissionDecisionEvent,
    ToolLifecycleEvent,
)
from agentao.host.projection import (
    HostPermissionEmitter,
    HostToolEmitter,
)
from agentao.permissions import PermissionEngine
from agentao.runtime.tool_runner import ToolRunner
from agentao.tools import Tool, ToolRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeStream:
    def __init__(self) -> None:
        self.events: List = []

    def publish(self, event):
        self.events.append(event)


class _ConfirmingTransport:
    """Test transport whose ``confirm_tool`` is configurable."""

    def __init__(self, confirm: bool = True) -> None:
        self._confirm = confirm
        self.emitted = []

    def emit(self, event):
        self.emitted.append(event)

    def confirm_tool(self, *_a, **_kw):
        return self._confirm

    def ask_user(self, _q):
        return ""

    def on_max_iterations(self, _c, _m):
        return {"action": "stop"}


class _ReadOnlyTool(Tool):
    @property
    def name(self) -> str: return "read_thing"
    @property
    def description(self) -> str: return "read"
    @property
    def parameters(self) -> Dict[str, Any]: return {"type": "object"}
    @property
    def is_read_only(self) -> bool: return True
    def execute(self, **kwargs) -> str: return "ok"


class _WriteTool(Tool):
    @property
    def name(self) -> str: return "write_thing"
    @property
    def description(self) -> str: return "write"
    @property
    def parameters(self) -> Dict[str, Any]: return {"type": "object"}
    @property
    def requires_confirmation(self) -> bool: return True
    def execute(self, **kwargs) -> str: return "ok"


def _build_runner(
    *,
    permission_engine: PermissionEngine,
    confirm: bool = True,
    tools=None,
):
    stream = _FakeStream()
    registry = ToolRegistry()
    for t in (tools or [_ReadOnlyTool(), _WriteTool()]):
        registry.register(t)

    perms_provider = lambda: permission_engine.active_permissions()
    perm_emitter = HostPermissionEmitter(
        stream,
        session_id_provider=lambda: "s-1",
        turn_id_provider=lambda: "t-1",
        active_permissions_provider=perms_provider,
    )
    tool_emitter = HostToolEmitter(
        stream,
        session_id_provider=lambda: "s-1",
        turn_id_provider=lambda: "t-1",
    )
    transport = _ConfirmingTransport(confirm=confirm)
    runner = ToolRunner(
        tools=registry,
        permission_engine=permission_engine,
        transport=transport,
        logger=logging.getLogger("test.harness_perm_events"),
        sandbox_policy=None,
        host_tool_emitter=tool_emitter,
        host_permission_emitter=perm_emitter,
    )
    return runner, stream, transport


def _tool_call(name: str, *, call_id: str = "tc-1", arguments: str = "{}"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


# ---------------------------------------------------------------------------
# Allow / deny / prompt
# ---------------------------------------------------------------------------


def test_allow_decision_emits_allow_event_and_precedes_started(tmp_path):
    engine = PermissionEngine(project_root=tmp_path)
    runner, stream, _ = _build_runner(permission_engine=engine)
    runner.execute([_tool_call("read_thing")])

    perm_events = [e for e in stream.events if isinstance(e, PermissionDecisionEvent)]
    tool_events = [e for e in stream.events if isinstance(e, ToolLifecycleEvent)]
    assert len(perm_events) == 1
    assert perm_events[0].outcome == "allow"
    assert perm_events[0].tool_call_id == "tc-1"
    started = next(e for e in tool_events if e.phase == "started")
    # Ordering: permission event must come before ``started`` for the
    # same tool_call_id.
    perm_idx = stream.events.index(perm_events[0])
    started_idx = stream.events.index(started)
    assert perm_idx < started_idx
    # ``loaded_sources`` is non-empty (preset at minimum).
    assert perm_events[0].loaded_sources
    # decision_id is populated and non-empty.
    assert perm_events[0].decision_id


def test_deny_decision_emits_deny_event_and_no_started(tmp_path):
    user_root = tmp_path / "user"
    user_root.mkdir()
    (user_root / "permissions.json").write_text(json.dumps({
        "rules": [{"tool": "write_thing", "action": "deny"}]
    }))
    engine = PermissionEngine(project_root=tmp_path, user_root=user_root)
    runner, stream, _ = _build_runner(permission_engine=engine)
    runner.execute([_tool_call("write_thing")])

    perm_events = [e for e in stream.events if isinstance(e, PermissionDecisionEvent)]
    tool_events = [e for e in stream.events if isinstance(e, ToolLifecycleEvent)]
    assert len(perm_events) == 1
    assert perm_events[0].outcome == "deny"
    # Matched rule projects the deny rule with at least the tool name.
    assert perm_events[0].matched_rule is not None
    assert perm_events[0].matched_rule.get("tool") == "write_thing"
    # No started event for a denied tool — only the cancelled terminal.
    assert all(e.phase != "started" for e in tool_events)


def test_prompt_decision_emits_prompt_event_for_ask_path(tmp_path):
    user_root = tmp_path / "user"
    user_root.mkdir()
    # ASK rule on a tool that ``requires_confirmation``: the planner
    # routes this to ToolCallDecision.ASK, so the public event must
    # surface ``outcome="prompt"`` before user confirmation runs.
    (user_root / "permissions.json").write_text(json.dumps({
        "rules": [{"tool": "write_thing", "action": "ask"}]
    }))
    engine = PermissionEngine(project_root=tmp_path, user_root=user_root)
    runner, stream, transport = _build_runner(permission_engine=engine, confirm=True)
    runner.execute([_tool_call("write_thing")])

    perm_events = [e for e in stream.events if isinstance(e, PermissionDecisionEvent)]
    assert len(perm_events) == 1
    # The runtime fires "prompt" before user confirmation; cancellation
    # outcome is reflected later via ``ToolLifecycleEvent``.
    assert perm_events[0].outcome == "prompt"
    assert perm_events[0].matched_rule is not None
    assert perm_events[0].matched_rule.get("tool") == "write_thing"


# ---------------------------------------------------------------------------
# Decision ID uniqueness
# ---------------------------------------------------------------------------


def test_decision_ids_are_unique_per_decision(tmp_path):
    engine = PermissionEngine(project_root=tmp_path)
    runner, stream, _ = _build_runner(permission_engine=engine)
    # Three calls in a single batch — three decisions, three ids.
    runner.execute([
        _tool_call("read_thing", call_id="a", arguments='{"i": 1}'),
        _tool_call("read_thing", call_id="b", arguments='{"i": 2}'),
        _tool_call("read_thing", call_id="c", arguments='{"i": 3}'),
    ])
    perm_events = [e for e in stream.events if isinstance(e, PermissionDecisionEvent)]
    decision_ids = {e.decision_id for e in perm_events}
    assert len(decision_ids) == len(perm_events) == 3


# ---------------------------------------------------------------------------
# Engine fallback (no JSON rule, requires_confirmation tool)
# ---------------------------------------------------------------------------


def test_requires_confirmation_fallback_emits_prompt_with_no_matched_rule(tmp_path):
    """When no JSON rule matches a confirmation-requiring tool,
    the runtime falls back to ``ASK``. The public event still fires
    with ``outcome="prompt"`` and ``matched_rule=None``."""
    engine = PermissionEngine(project_root=tmp_path)
    # WorkspaceWrite preset has a ``write_file`` allow rule by default;
    # use a custom tool name not covered by any preset so the engine
    # returns no match and the runner falls back to the tool's
    # requires_confirmation flag.
    class _CustomConfirmTool(Tool):
        @property
        def name(self) -> str: return "custom_confirm"
        @property
        def description(self) -> str: return "custom"
        @property
        def parameters(self) -> Dict[str, Any]: return {"type": "object"}
        @property
        def requires_confirmation(self) -> bool: return True
        def execute(self, **kwargs) -> str: return "ok"

    runner, stream, _ = _build_runner(
        permission_engine=engine,
        tools=[_CustomConfirmTool()],
    )
    runner.execute([_tool_call("custom_confirm")])
    perm_events = [e for e in stream.events if isinstance(e, PermissionDecisionEvent)]
    assert len(perm_events) == 1
    assert perm_events[0].outcome == "prompt"
    # No JSON-rule match → matched_rule is None.
    assert perm_events[0].matched_rule is None


# ---------------------------------------------------------------------------
# loaded_sources reflects the engine's current snapshot
# ---------------------------------------------------------------------------


def test_loaded_sources_is_engine_snapshot(tmp_path):
    user_root = tmp_path / "user"
    user_root.mkdir()
    (user_root / "permissions.json").write_text(json.dumps({
        "rules": [{"tool": "read_thing", "action": "allow"}]
    }))
    engine = PermissionEngine(project_root=tmp_path, user_root=user_root)
    runner, stream, _ = _build_runner(permission_engine=engine)
    runner.execute([_tool_call("read_thing")])
    perm_events = [e for e in stream.events if isinstance(e, PermissionDecisionEvent)]
    # The engine now records user-scope rules only; project-scope is
    # ignored by design (see ``permissions.py`` class docstring).
    assert any(s.startswith("user:") for s in perm_events[0].loaded_sources)
    assert all(not s.startswith("project:") for s in perm_events[0].loaded_sources)
