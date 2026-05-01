"""Tool lifecycle public-event tests (PR 5).

Drives the runtime ``ToolExecutor`` directly with a real
:class:`HarnessToolEmitter` wired to a real :class:`EventStream`, and
asserts the public envelope shape: started/completed/failed phases,
``error_type`` stability across runs, no raw args/output leaks, and the
AsyncTool cancellation path emits exactly one terminal envelope after
cleanup ack.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from agentao.cancellation import CancellationToken
from agentao.harness.events import EventStream
from agentao.harness.models import ToolLifecycleEvent
from agentao.harness.projection import HarnessToolEmitter
from agentao.runtime.tool_executor import ToolExecutor
from agentao.runtime.tool_planning import ToolCallDecision, ToolCallPlan
from agentao.tools import AsyncToolBase, Tool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _NullTransport:
    def emit(self, event):
        pass

    def confirm_tool(self, *_a, **_kw):
        return True

    def ask_user(self, _q):
        return ""

    def on_max_iterations(self, _c, _m):
        return {"action": "stop"}


class _SyncEcho(Tool):
    @property
    def name(self) -> str:
        return "sync_echo"

    @property
    def description(self) -> str:
        return "echo input"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object"}

    def execute(self, **kwargs) -> str:
        return f"echo:{kwargs.get('x', '')}"


class _Boom(Tool):
    @property
    def name(self) -> str:
        return "boom"

    @property
    def description(self) -> str:
        return "always raises"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object"}

    def execute(self, **kwargs) -> str:
        raise ValueError("kaboom")


class _AsyncEcho(AsyncToolBase):
    @property
    def name(self) -> str:
        return "async_echo"

    @property
    def description(self) -> str:
        return "async echo"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object"}

    async def async_execute(self, **kwargs) -> str:
        await asyncio.sleep(0)
        return f"async:{kwargs.get('x', '')}"


def _make_plan(tool, *, decision=ToolCallDecision.ALLOW, args=None, call_id="call-1"):
    tc = SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=tool.name, arguments="{}"),
    )
    return ToolCallPlan(
        tool_call=tc,
        function_name=tool.name,
        function_args=args or {},
        tool=tool,
        decision=decision,
    )


class _Harness:
    """Test harness: collects published events synchronously.

    Avoids the asyncio loop binding inside ``EventStream`` for unit
    testing — we install a fake stream that records ``publish`` calls
    directly. The HarnessToolEmitter behaviour itself is identical
    regardless of the underlying stream.
    """

    def __init__(self, *, session_id="s-1", turn_id="t-1"):
        self.events: List[ToolLifecycleEvent] = []
        # Fake stream with the same publish surface the emitter uses.
        class _FakeStream:
            def publish(_self, event):
                self.events.append(event)
        self.emitter = HarnessToolEmitter(
            _FakeStream(),
            session_id_provider=lambda: session_id,
            turn_id_provider=lambda: turn_id,
        )


def _make_executor(emitter) -> ToolExecutor:
    return ToolExecutor(
        _NullTransport(),
        logging.getLogger("test.harness_tool_events"),
        sandbox_policy=None,
        harness_tool_emitter=emitter,
    )


# ---------------------------------------------------------------------------
# Sync tool: started + completed
# ---------------------------------------------------------------------------


def test_sync_tool_emits_started_and_completed():
    h = _Harness()
    executor = _make_executor(h.emitter)
    tool = _SyncEcho()
    executor.execute_batch([_make_plan(tool, args={"x": "hello"})])
    phases = [e.phase for e in h.events]
    assert phases == ["started", "completed"]
    started, completed = h.events
    assert started.tool_name == "sync_echo"
    assert started.tool_call_id == "call-1"
    assert started.session_id == "s-1"
    assert started.turn_id == "t-1"
    assert completed.outcome == "ok"
    assert completed.error_type is None
    # Completed reuses the started_at timestamp.
    assert completed.started_at == started.started_at


def test_sync_tool_failure_emits_failed_with_stable_error_type():
    h = _Harness()
    executor = _make_executor(h.emitter)
    tool = _Boom()
    executor.execute_batch([_make_plan(tool, call_id="boom-1")])
    h2 = _Harness()
    executor2 = _make_executor(h2.emitter)
    executor2.execute_batch([_make_plan(tool, call_id="boom-2")])
    failed_first = [e for e in h.events if e.phase == "failed"][0]
    failed_second = [e for e in h2.events if e.phase == "failed"][0]
    # Stable identifier — same exception class, same wire value.
    assert failed_first.error_type == "ValueError"
    assert failed_first.error_type == failed_second.error_type
    assert failed_first.outcome == "error"
    # The exception ``str(exc)`` ("kaboom") is internal-only — it can
    # contain argument values and file contents in the wild — so it
    # must NOT leak into the public ``summary``. ``error_type`` already
    # gives hosts the discriminator they need.
    assert "kaboom" not in (failed_first.summary or "")


# ---------------------------------------------------------------------------
# Args / output redaction
# ---------------------------------------------------------------------------


def test_started_event_does_not_leak_raw_args():
    h = _Harness()
    executor = _make_executor(h.emitter)
    tool = _SyncEcho()
    secret = "--SECRET-VALUE-XYZ--"
    executor.execute_batch([_make_plan(tool, args={"x": secret})])
    started = h.events[0]
    # Public envelope intentionally has no ``args`` field.
    serialized = started.model_dump()
    assert "args" not in serialized
    assert secret not in str(serialized)


def test_completed_event_redacts_output():
    h = _Harness()
    executor = _make_executor(h.emitter)
    # Tool output that is huge + multi-line — must be summarized to a
    # bounded single-line string, not echoed verbatim. Raw output is
    # the redaction-boundary contract: hosts get the public ``summary``
    # for status display, never the unscrubbed tool result.
    secret_marker = "secret-output-token-XYZ"
    big_text = ("noise " * 200) + f"\n{secret_marker}\n"

    class _Big(Tool):
        @property
        def name(self) -> str: return "big"
        @property
        def description(self) -> str: return "big"
        @property
        def parameters(self) -> Dict[str, Any]: return {"type": "object"}
        def execute(self, **kwargs) -> str:
            return big_text

    executor.execute_batch([_make_plan(_Big())])
    completed = [e for e in h.events if e.phase == "completed"][0]
    assert completed.summary is not None
    assert len(completed.summary) <= 240
    assert "\n" not in completed.summary  # newlines collapsed
    # The summary must NOT include any of the raw tool output. Hosts
    # that need the full output consume the internal TOOL_RESULT event
    # (with its own redaction policy), not the public harness summary.
    assert secret_marker not in completed.summary
    assert "noise" not in completed.summary


# ---------------------------------------------------------------------------
# DENY / cancelled-by-user paths emit a terminal envelope
# ---------------------------------------------------------------------------


def test_deny_decision_emits_cancelled_terminal_only():
    h = _Harness()
    executor = _make_executor(h.emitter)
    tool = _SyncEcho()
    executor.execute_batch([_make_plan(tool, decision=ToolCallDecision.DENY)])
    phases = [e.phase for e in h.events]
    # Plan: started fires only on the ALLOW path (after permission +
    # pre-cancel guards). DENY emits exactly the cancelled terminal.
    assert phases == ["failed"]
    assert h.events[0].outcome == "cancelled"
    assert h.events[0].error_type is None


def test_user_cancelled_emits_cancelled_terminal_only():
    h = _Harness()
    executor = _make_executor(h.emitter)
    tool = _SyncEcho()
    executor.execute_batch([_make_plan(tool, decision=ToolCallDecision.CANCELLED)])
    phases = [e.phase for e in h.events]
    assert phases == ["failed"]
    assert h.events[0].outcome == "cancelled"


# ---------------------------------------------------------------------------
# AsyncTool: success and cancellation
# ---------------------------------------------------------------------------


def test_async_tool_success_through_arun_path():
    h = _Harness()
    executor = _make_executor(h.emitter)
    tool = _AsyncEcho()

    async def runner():
        loop = asyncio.get_running_loop()
        token = CancellationToken()
        token.runtime_loop = loop
        await loop.run_in_executor(
            None,
            lambda: executor.execute_batch([_make_plan(tool)], cancellation_token=token),
        )

    asyncio.run(runner())
    phases = [e.phase for e in h.events]
    assert phases == ["started", "completed"]
    assert h.events[1].outcome == "ok"


def test_async_tool_cancellation_emits_one_terminal_after_cleanup():
    """The harness terminal envelope must follow the cleanup ack, not race ahead."""
    h = _Harness()
    executor = _make_executor(h.emitter)

    cleanup_done = threading.Event()
    started_running = threading.Event()

    class _SlowAsync(AsyncToolBase):
        @property
        def name(self) -> str: return "slow"
        @property
        def description(self) -> str: return "slow"
        @property
        def parameters(self) -> Dict[str, Any]: return {"type": "object"}
        async def async_execute(self, **kwargs) -> str:
            try:
                started_running.set()
                await asyncio.sleep(10)
                return "never"
            finally:
                cleanup_done.set()

    tool = _SlowAsync()

    async def runner():
        loop = asyncio.get_running_loop()
        token = CancellationToken()
        token.runtime_loop = loop

        def execute_in_thread():
            return executor.execute_batch(
                [_make_plan(tool)], cancellation_token=token,
            )

        fut = loop.run_in_executor(None, execute_in_thread)
        # Wait until the coroutine is actually running on the host loop.
        await loop.run_in_executor(None, started_running.wait, 2.0)
        token.cancel("test-cancel")
        await fut

    asyncio.run(runner())
    assert cleanup_done.is_set(), "user coroutine cleanup must run"
    failed_events = [e for e in h.events if e.phase == "failed"]
    assert len(failed_events) == 1, (
        "Cancelled async tool must emit exactly one terminal envelope, "
        f"got phases {[e.phase for e in h.events]}"
    )
    assert failed_events[0].outcome == "cancelled"
    assert failed_events[0].error_type is None


# ---------------------------------------------------------------------------
# Regression: provider-omitted tool_call.id must not drop lifecycle events
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing_id", [None, "", "   "])
def test_planner_normalizes_missing_tool_call_id_so_events_still_fire(missing_id):
    """A tool_call returned by the LLM with a missing or empty ``id``
    used to forward ``None`` into ``ToolLifecycleEvent.tool_call_id``,
    which is required to be a non-empty string. Pydantic validation
    failed and the started/completed envelopes were silently dropped.

    The planner normalizes the id once via
    :func:`identity.normalize_tool_call_id` and stores it on the plan,
    so the executor emits a correlated pair of events with a stable
    UUID4 fallback string."""
    from agentao.runtime.tool_planning import ToolCallPlanner

    h = _Harness()
    executor = _make_executor(h.emitter)
    tool = _SyncEcho()

    planner = ToolCallPlanner(
        tools=_RegistryStub(tool),
        permission_engine=None,
        logger=logging.getLogger("test.planner"),
    )
    tc = SimpleNamespace(
        id=missing_id,
        function=SimpleNamespace(name=tool.name, arguments="{}"),
    )
    planning = planner.plan([tc])
    assert len(planning.plans) == 1
    plan = planning.plans[0]

    assert isinstance(plan.tool_call_id, str)
    assert plan.tool_call_id  # non-empty string
    # Best-effort mirror onto the upstream tool_call so the API
    # tool_result echoed to the LLM matches the assistant message.
    assert plan.tool_call.id == plan.tool_call_id

    executor.execute_batch([plan])
    phases = [e.phase for e in h.events]
    assert phases == ["started", "completed"], (
        "Lifecycle events must still fire when the provider omits an id; "
        f"got {phases}"
    )
    assert h.events[0].tool_call_id == plan.tool_call_id
    assert h.events[1].tool_call_id == plan.tool_call_id


class _RegistryStub:
    """Minimal stand-in for ``ToolRegistry`` used by the planner test."""

    def __init__(self, tool):
        self._tool = tool
        self.tools = {tool.name: tool}

    def get(self, name):
        if name == self._tool.name:
            return self._tool
        raise KeyError(name)
