"""CLI host-adapter smoke tests (PR 8).

The CLI is the canonical first host of the harness contract. PR 8 wires
the status/mode display through ``Agentao.active_permissions()`` and
demonstrates that a CLI-side consumer can render tool lifecycle state
from ``Agentao.events()`` without reaching into private runtime state.

These tests intentionally drive the public surface only: no internal
``AgentEvent`` paths are touched, no private permission-engine
attributes are read, and no graph store / hooks list / MCP reload API
is exercised — those remain CLI-internal.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from agentao.harness.events import EventStream
from agentao.harness.models import ToolLifecycleEvent
from agentao.harness.projection import HarnessToolEmitter
from agentao.runtime.tool_executor import ToolExecutor
from agentao.runtime.tool_planning import ToolCallDecision, ToolCallPlan
from agentao.tools import Tool


# ---------------------------------------------------------------------------
# A minimal Tool that the CLI host driver renders state for.
# ---------------------------------------------------------------------------


class _Echo(Tool):
    @property
    def name(self) -> str: return "echo"
    @property
    def description(self) -> str: return "echo"
    @property
    def parameters(self) -> Dict[str, Any]: return {"type": "object"}
    def execute(self, **kwargs) -> str:
        return f"got:{kwargs.get('x', '')}"


def _make_plan(tool, *, args=None, call_id="call-1"):
    tc = SimpleNamespace(id=call_id, function=SimpleNamespace(name=tool.name, arguments="{}"))
    return ToolCallPlan(
        tool_call=tc,
        function_name=tool.name,
        function_args=args or {},
        tool=tool,
        decision=ToolCallDecision.ALLOW,
    )


class _NullTransport:
    def emit(self, _ev): pass
    def confirm_tool(self, *_a, **_kw): return True
    def ask_user(self, _q): return ""
    def on_max_iterations(self, _c, _m): return {"action": "stop"}


# ---------------------------------------------------------------------------
# Acceptance: a CLI consumer can render tool-running state from the stream.
# ---------------------------------------------------------------------------


def test_cli_can_render_basic_tool_state_from_harness_event_stream(tmp_path):
    """Demonstrates the PR 8 contract end-to-end:

    A "CLI" reads the public ``EventStream`` and updates its display
    state from the published ``ToolLifecycleEvent`` envelopes — without
    subscribing to the runtime's internal ``Transport``.
    """
    stream = EventStream()
    emitter = HarnessToolEmitter(
        stream,
        session_id_provider=lambda: "s-1",
        turn_id_provider=lambda: "t-1",
    )
    executor = ToolExecutor(
        _NullTransport(),
        logging.getLogger("test.cli_harness_events"),
        sandbox_policy=None,
        harness_tool_emitter=emitter,
    )

    async def runner():
        events: List[ToolLifecycleEvent] = []

        async def consume():
            async for ev in stream.subscribe(session_id="s-1"):
                events.append(ev)
                if ev.phase == "completed":
                    return

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, lambda: executor.execute_batch([_make_plan(_Echo())]),
        )
        await asyncio.wait_for(consumer, timeout=2.0)
        return events

    events = asyncio.run(runner())
    # CLI sees both phases; rendering can light up the spinner on
    # ``started`` and clear it on ``completed``.
    assert [e.phase for e in events] == ["started", "completed"]
    completed = events[-1]
    assert completed.tool_name == "echo"
    assert completed.outcome == "ok"


# ---------------------------------------------------------------------------
# Acceptance: status/mode display reads from active_permissions().
# ---------------------------------------------------------------------------


def test_cli_show_status_reads_from_active_permissions(tmp_path, monkeypatch, capsys):
    """``show_status`` must derive the mode label from the public
    ``active_permissions()`` snapshot, not from private engine
    attributes."""
    from agentao.cli import ui as cli_ui
    from agentao.harness.models import ActivePermissions
    from agentao.permissions import PermissionMode

    snapshot = ActivePermissions(
        mode="full-access",
        rules=[{"tool": "*", "action": "allow"}],
        loaded_sources=[
            "preset:full-access",
            "project:.agentao/permissions.json",
            "injected:host",
        ],
    )

    # Track whether we ever reach into private ``permission_engine``
    # state during rendering. The CLI display path must not touch it.
    private_reads: List[str] = []

    class _SpyEngine:
        def __getattr__(self, name):
            private_reads.append(name)
            raise AttributeError(name)

    fake_agent = SimpleNamespace(
        get_conversation_summary=lambda: "Messages: 0",
        active_permissions=lambda: snapshot,
        todo_tool=SimpleNamespace(get_todos=lambda: []),
        permission_engine=_SpyEngine(),
    )

    fake_cli = SimpleNamespace(
        agent=fake_agent,
        current_mode=PermissionMode.FULL_ACCESS,
        markdown_mode=False,
        permission_engine=fake_agent.permission_engine,
        _acp_manager=None,
    )

    cli_ui.show_status(fake_cli)
    out = capsys.readouterr().out
    assert "full-access" in out
    assert "preset:full-access" in out
    assert "injected:host" in out
    # Must not have reached into private engine attributes for mode.
    assert "active_mode" not in private_reads, (
        "show_status read engine.active_mode; the public snapshot "
        "should be sufficient"
    )
