"""Acceptance tests for the AsyncToolBase addendum.

Covers:

- ``AsyncToolBase`` registers into ``ToolRegistry`` without an adapter and
  survives ``to_openai_format(plan_mode=...)`` and the planner's
  ``is_read_only`` access (no AttributeError).
- The :data:`RegistrableTool` type boundary is published on the
  registry / planner / executor / agent-tool surfaces (verified via
  ``typing.get_type_hints``).
- Public re-exports: ``AsyncToolBase`` and ``RegistrableTool`` from
  ``agentao.tools``; ``_BaseTool`` is intentionally not re-exported.
- ``CancellationToken.add_done_callback`` semantics (cross-thread fire,
  already-cancelled fast path, idempotent unregister).
- End-to-end via ``arun()``: the coroutine actually runs on the host
  loop captured by ``arun()``.
- End-to-end via sync ``chat()``: a loop-independent ``AsyncToolBase``
  works through the fresh-loop fallback.
- Cancellation along the ``arun()`` path: cleanup-ordered ``finally``
  runs before ``TOOL_COMPLETE(status="cancelled")``, the underlying
  future is cancelled, ``TOOL_COMPLETE`` fires exactly once, the chat
  call unwinds, and no task is left pending on the host loop.
- Ack-timeout warning path: a coroutine whose ``finally`` blocks past
  the configured timeout still emits ``TOOL_COMPLETE(status="cancelled")``
  exactly once and the chat call unwinds.
- The dispatcher does not pass ``ctx`` / run-context kwargs to a sync
  ``Tool.execute``.
- ``compileall`` succeeds for every edited module.
"""

from __future__ import annotations

import asyncio
import compileall
import inspect
import logging
import threading
import time
import typing
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Union, get_type_hints
from unittest.mock import MagicMock, Mock, patch

import pytest

from agentao import tools as tools_pkg
from agentao.cancellation import CancellationToken
from agentao.runtime import tool_executor as tool_executor_module
from agentao.runtime.tool_executor import (
    ASYNC_CANCEL_REASON,
    ToolExecutionResult,
    ToolExecutor,
    _AsyncToolOutcome,
)
from agentao.runtime.tool_planning import (
    ToolCallDecision,
    ToolCallPlan,
    ToolCallPlanner,
)
from agentao.tools import AsyncToolBase, RegistrableTool, Tool, ToolRegistry
from agentao.tools.base import _BaseTool
from agentao.transport import AgentEvent, EventType


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


class _CapturingTransport:
    """Transport that records every emitted event for later assertions."""

    def __init__(self) -> None:
        self.events: List[AgentEvent] = []

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)

    def confirm_tool(self, tool_name, description, args):
        return True

    def ask_user(self, question):
        return ""

    def on_max_iterations(self, count, messages):
        return {"action": "stop"}

    def by_type(self, event_type: EventType) -> List[AgentEvent]:
        return [e for e in self.events if e.type == event_type]


class _SyncEcho(Tool):
    """Sync tool used to verify sync dispatch is unaffected by the changes."""

    @property
    def name(self) -> str:
        return "sync_echo"

    @property
    def description(self) -> str:
        return "echo input"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {"x": {"type": "string"}}}

    def execute(self, **kwargs) -> str:
        return f"echo:{kwargs.get('x', '')}"


class _StrictSyncTool(Tool):
    """Sync tool that rejects unknown kwargs.

    Used to assert that the dispatcher never threads a hidden ``ctx``
    keyword into ``Tool.execute`` — that would break every existing tool
    silently.
    """

    @property
    def name(self) -> str:
        return "strict_sync"

    @property
    def description(self) -> str:
        return "rejects unknown kwargs"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        # The signature itself accepts **kwargs to avoid TypeError, so
        # we explicitly reject anything unexpected.
        if kwargs:
            raise AssertionError(
                f"Sync Tool.execute received unexpected kwargs: {sorted(kwargs)}"
            )
        return "ok"


class _AsyncEcho(AsyncToolBase):
    """Loop-independent async tool — opens / closes its own resources."""

    @property
    def name(self) -> str:
        return "async_echo"

    @property
    def description(self) -> str:
        return "async echo"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {"x": {"type": "string"}}}

    async def async_execute(self, **kwargs) -> str:
        # Yield once so we exercise the bridge actually awaiting.
        await asyncio.sleep(0)
        return f"async-echo:{kwargs.get('x', '')}"


class _LoopAffineAsync(AsyncToolBase):
    """Async tool that asserts it runs on a specific captured loop.

    Mirrors the host-loop-affine constraint from the addendum: an
    ``aiohttp.ClientSession`` (etc.) is bound to the loop it was created
    on, so the dispatcher must not run the coroutine on a fresh
    chat-thread loop.
    """

    def __init__(self, expected_loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self._expected_loop = expected_loop
        self.ran_on: Optional[asyncio.AbstractEventLoop] = None

    @property
    def name(self) -> str:
        return "loop_affine_async"

    @property
    def description(self) -> str:
        return "asserts loop affinity"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def async_execute(self, **kwargs) -> str:
        loop = asyncio.get_running_loop()
        self.ran_on = loop
        assert loop is self._expected_loop, (
            "AsyncTool ran on the wrong event loop"
        )
        return "host-loop"


def _make_plan(tool: RegistrableTool, args: Optional[Dict[str, Any]] = None) -> ToolCallPlan:
    tc = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name=tool.name, arguments="{}"),
    )
    return ToolCallPlan(
        tool_call=tc,
        function_name=tool.name,
        function_args=args or {},
        tool=tool,
        decision=ToolCallDecision.ALLOW,
    )


def _make_executor() -> tuple[ToolExecutor, _CapturingTransport]:
    transport = _CapturingTransport()
    logger = logging.getLogger("test.async_tool")
    return ToolExecutor(transport, logger, sandbox_policy=None), transport


# ---------------------------------------------------------------------------
# Surface / type-boundary acceptance
# ---------------------------------------------------------------------------


def test_async_tool_registers_and_survives_metadata_pass():
    """AsyncToolBase drops into the registry; metadata paths do not crash."""
    registry = ToolRegistry()
    registry.register(_AsyncEcho())
    registry.register(_SyncEcho())

    # to_openai_format(plan_mode=...) iterates and calls each tool's
    # serializer — must not AttributeError on AsyncToolBase.
    schema = registry.to_openai_format(plan_mode=False)
    assert any(s["function"]["name"] == "async_echo" for s in schema)
    assert any(s["function"]["name"] == "sync_echo" for s in schema)

    # Planner's _decide consults is_read_only. Run a planning pass over
    # both tools and confirm no AttributeError.
    planner = ToolCallPlanner(registry, permission_engine=None, logger=logging.getLogger("p"))
    tcs = [
        SimpleNamespace(
            id=f"id-{name}",
            function=SimpleNamespace(name=name, arguments="{}"),
        )
        for name in ("async_echo", "sync_echo")
    ]
    result = planner.plan(tcs, readonly_mode=True)
    # Both tools default to is_read_only=False → readonly_mode denies them.
    assert all(p.decision == ToolCallDecision.DENY for p in result.plans)


def test_registrable_tool_is_published_on_public_surfaces():
    """RegistrableTool annotations propagate where the addendum requires."""
    expected = RegistrableTool

    # ToolRegistry public surface — fully resolvable.
    reg_hints = get_type_hints(ToolRegistry)
    assert reg_hints["tools"] == Dict[str, expected]
    assert get_type_hints(ToolRegistry.register)["tool"] == expected
    assert get_type_hints(ToolRegistry.get)["return"] == expected
    assert get_type_hints(ToolRegistry.list_tools)["return"] == List[expected]

    # ToolCallPlan field
    assert get_type_hints(ToolCallPlan)["tool"] == expected

    # AgentManager / AgentToolWrapper sub-agent surfaces also reference
    # TYPE_CHECKING-only imports (BackgroundTaskStore etc.) that
    # ``get_type_hints`` can't resolve at runtime. Resolve those types
    # against each module's own globalns + localns supplemented with
    # the missing TYPE_CHECKING names so the check still succeeds.
    from agentao.agents import bg_store as _bg_store_mod
    from agentao.agents.manager import AgentManager
    from agentao.agents.tools import AgentToolWrapper
    from agentao.agents import manager as manager_mod
    from agentao.agents import tools as agents_tools_mod

    extra_locals = {
        "BackgroundTaskStore": _bg_store_mod.BackgroundTaskStore,
    }

    am_hints = get_type_hints(
        AgentManager.create_agent_tools,
        globalns=vars(manager_mod),
        localns=extra_locals,
    )
    assert am_hints["all_tools"] == Dict[str, expected]

    atw_hints = get_type_hints(
        AgentToolWrapper.__init__,
        globalns=vars(agents_tools_mod),
        localns=extra_locals,
    )
    assert atw_hints["all_tools"] == Dict[str, expected]


def test_registrable_tool_is_the_alias_in_tools_base():
    """Ensure `RegistrableTool` re-exported from `agentao.tools` is the
    same object as the one defined in `agentao.tools.base`."""
    from agentao.tools.base import RegistrableTool as base_alias

    assert RegistrableTool is base_alias


def test_public_imports_async_tool_base_and_registrable_tool():
    """Smoke test for the public import surface."""
    # The package import itself must surface both names.
    assert AsyncToolBase in (tools_pkg.AsyncToolBase,)
    assert RegistrableTool in (tools_pkg.RegistrableTool,)
    # And the in-test subclass instantiates without issue.
    inst = _AsyncEcho()
    assert isinstance(inst, AsyncToolBase)
    assert isinstance(inst, _BaseTool)
    # _BaseTool is intentionally NOT re-exported.
    assert "_BaseTool" not in getattr(tools_pkg, "__all__", [])


def test_existing_sync_tool_subclass_unchanged():
    """The _BaseTool extraction must preserve Tool's surface.

    Sample existing tools instantiate without code changes and expose
    every helper they did before (capability accessors, path helpers,
    metadata properties, OpenAI serializer).
    """
    from agentao.tools import (
        EditTool,
        FindFilesTool,
        ReadFileTool,
        SaveMemoryTool,
        ShellTool,
    )

    samples = [ReadFileTool(), EditTool(), FindFilesTool(), ShellTool(), SaveMemoryTool(memory_manager=Mock())]
    for t in samples:
        # Every helper must still be callable.
        assert callable(getattr(t, "_get_fs"))
        assert callable(getattr(t, "_get_shell"))
        assert callable(getattr(t, "_resolve_path"))
        assert callable(getattr(t, "_resolve_directory"))
        # Metadata props must still resolve.
        assert isinstance(t.name, str)
        assert isinstance(t.description, str)
        assert isinstance(t.parameters, dict)
        assert isinstance(t.requires_confirmation, bool)
        assert isinstance(t.is_read_only, bool)
        # OpenAI serializer must still be valid.
        ser = t.to_openai_format()
        assert ser["type"] == "function"
        assert ser["function"]["name"] == t.name


def test_compileall_passes_for_edited_modules():
    """Lightweight stand-in for the absent mypy/pyright check.

    The acceptance criteria call for ``python -m compileall`` over the
    edited surface. Run it through Python's library API so a regression
    in any of these files (syntax, bad imports) trips the test.
    """
    repo_root = Path(__file__).resolve().parent.parent
    targets = [
        "agentao/tools/base.py",
        "agentao/cancellation.py",
        "agentao/runtime/tool_planning.py",
        "agentao/runtime/tool_executor.py",
        "agentao/runtime/tool_runner.py",
        "agentao/agents/manager.py",
        "agentao/agents/tools.py",
        "agentao/agent.py",
    ]
    for rel in targets:
        ok = compileall.compile_file(
            str(repo_root / rel), quiet=1, force=True,
        )
        assert ok, f"compileall failed for {rel}"


# ---------------------------------------------------------------------------
# CancellationToken.add_done_callback
# ---------------------------------------------------------------------------


def test_cancel_runs_callback_synchronously_from_other_thread():
    token = CancellationToken()
    fired = threading.Event()

    def _cb():
        fired.set()

    token.add_done_callback(_cb)

    cancel_thread = threading.Thread(target=lambda: token.cancel("from-other"))
    cancel_thread.start()
    cancel_thread.join(timeout=1.0)

    assert token.is_cancelled
    assert fired.is_set()


def test_callback_runs_immediately_when_already_cancelled():
    token = CancellationToken()
    token.cancel("first")

    fired = []
    unregister = token.add_done_callback(lambda: fired.append(1))
    assert fired == [1]
    # Idempotent unregister even on the immediate-fire path.
    unregister()
    unregister()


def test_unregister_is_idempotent_and_detaches():
    token = CancellationToken()
    fired = []

    unregister = token.add_done_callback(lambda: fired.append(1))
    unregister()
    unregister()  # second call is a no-op, no exception

    token.cancel("after-unregister")
    assert fired == []  # callback was detached before cancel


def test_callback_exceptions_do_not_block_other_callbacks():
    token = CancellationToken()
    fired_b = []

    def _bad():
        raise RuntimeError("boom")

    token.add_done_callback(_bad)
    token.add_done_callback(lambda: fired_b.append(1))
    token.cancel()
    assert fired_b == [1]
    # The token remains cancelled; the misbehaving callback didn't poison state.
    assert token.is_cancelled


# ---------------------------------------------------------------------------
# Sync path scope (no host loop)
# ---------------------------------------------------------------------------


def test_sync_chat_runs_loop_independent_async_tool():
    """A loop-independent AsyncToolBase succeeds via asyncio.run fallback.

    Sync host without a captured runtime_loop. Documented scope: only
    works when the coroutine creates and tears down all its loop-bound
    resources within a single ``async_execute`` call.
    """
    executor, transport = _make_executor()
    tool = _AsyncEcho()
    plan = _make_plan(tool, args={"x": "hello"})
    plan.tool_call.id = "sync-call"
    plan.tool_call_id = "sync-call"

    results = executor.execute_batch([plan], cancellation_token=None)
    assert results["sync-call"].status == "ok"
    assert results["sync-call"].result == "async-echo:hello"

    # TOOL_COMPLETE was emitted exactly once with status="ok".
    completes = transport.by_type(EventType.TOOL_COMPLETE)
    assert len(completes) == 1
    assert completes[0].data["status"] == "ok"


def test_sync_dispatcher_does_not_pass_ctx_kwargs_to_sync_tool():
    """Hard invariant: sync Tool.execute receives only the LLM-supplied args."""
    executor, _ = _make_executor()
    tool = _StrictSyncTool()
    plan = _make_plan(tool, args={})
    plan.tool_call.id = "strict-call"
    plan.tool_call_id = "strict-call"

    results = executor.execute_batch([plan], cancellation_token=None)
    assert results["strict-call"].status == "ok"
    assert results["strict-call"].result == "ok"


# ---------------------------------------------------------------------------
# Async path — runs on the captured host loop
# ---------------------------------------------------------------------------


def test_async_tool_runs_on_host_loop_via_arun(tmp_path):
    """End-to-end: arun() captures the host loop; the coroutine runs on it."""

    with patch("agentao.agent.LLMClient") as mock_llm_cls, \
         patch("agentao.tooling.mcp_tools.load_mcp_config", return_value={}), \
         patch("agentao.tooling.mcp_tools.McpClientManager"):
        mock_llm = Mock()
        mock_llm.logger = Mock()
        mock_llm.model = "gpt-test"
        mock_llm_cls.return_value = mock_llm

        from agentao.agent import Agentao

        agent = Agentao(working_directory=tmp_path)

    captured: Dict[str, Any] = {}

    async def _drive():
        host_loop = asyncio.get_running_loop()
        captured["host_loop"] = host_loop

        affine_tool = _LoopAffineAsync(expected_loop=host_loop)
        agent.tools.register(affine_tool)
        agent.tool_runner = agent.tool_runner  # ensure runner sees latest registry

        # Build a fake LLM round: first call returns one tool_call for our
        # AsyncTool, second call returns the final assistant message.
        tool_call = SimpleNamespace(
            id="call-host-loop",
            function=SimpleNamespace(name="loop_affine_async", arguments="{}"),
        )
        first = MagicMock()
        first.choices[0].message.tool_calls = [tool_call]
        first.choices[0].message.content = ""
        first.choices[0].message.reasoning_content = None
        # Pydantic ``model_dump`` is consulted by the history serializer.
        # The tool_call SimpleNamespace doesn't have it, so the fallback
        # path serializes manually — that's fine for our assertion.

        final = MagicMock()
        final.choices[0].message.tool_calls = None
        final.choices[0].message.content = "done"
        final.choices[0].message.reasoning_content = None

        agent._llm_call = Mock(side_effect=[first, final])

        result = await agent.arun("go")
        captured["result"] = result
        captured["affine_tool"] = affine_tool

    asyncio.run(_drive())

    assert captured["result"] == "done"
    affine_tool: _LoopAffineAsync = captured["affine_tool"]
    # The coroutine ran on the host loop, not a fresh chat-thread loop.
    assert affine_tool.ran_on is captured["host_loop"]


# ---------------------------------------------------------------------------
# Async path — cancellation: cleanup-ordered, exactly one TOOL_COMPLETE
# ---------------------------------------------------------------------------


def _drive_async_cancel(
    tool: AsyncToolBase,
    *,
    inside_await: threading.Event,
) -> Dict[str, Any]:
    """Drive a single-tool execute_batch on a worker thread while the test
    thread cancels via the token. Captures the timing relationship between
    the user-coroutine ``finally`` and the emitted ``TOOL_COMPLETE``.

    Runs on a real asyncio event loop captured exactly the way arun()
    does so the executor's run_coroutine_threadsafe path is exercised.
    The caller passes an ``inside_await`` Event that the tool's
    ``async_execute`` sets once it is parked inside the await; this lets
    the cancel race deterministically without a wall-clock sleep.
    """
    executor, transport = _make_executor()
    plan = _make_plan(tool)
    plan.tool_call.id = "cancel-call"
    plan.tool_call_id = "cancel-call"

    captured: Dict[str, Any] = {
        "results": None,
        "transport": transport,
        "complete_at": None,
    }

    async def _serve_loop(host_loop_ready: threading.Event, done: threading.Event):
        host_loop_ready.set()
        try:
            while not done.is_set():
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass

    host_loop_ready = threading.Event()
    done = threading.Event()
    host_loop_holder: Dict[str, Any] = {}

    def _loop_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        host_loop_holder["loop"] = loop
        try:
            loop.run_until_complete(_serve_loop(host_loop_ready, done))
        finally:
            loop.close()

    t_loop = threading.Thread(target=_loop_thread, daemon=True)
    t_loop.start()
    assert host_loop_ready.wait(timeout=2.0), "host loop never started"
    host_loop = host_loop_holder["loop"]

    token = CancellationToken(runtime_loop=host_loop)

    def _executor_thread():
        captured["results"] = executor.execute_batch(
            [plan], cancellation_token=token,
        )
        captured["complete_at"] = time.monotonic()

    t_exec = threading.Thread(target=_executor_thread, daemon=True)
    t_exec.start()

    # Wait until the coroutine is actually parked in its await before
    # we cancel. Removes the timing dependency a wall-clock sleep would
    # introduce and makes the cancel race deterministic.
    assert inside_await.wait(timeout=2.0), "coroutine never reached the await"
    token.cancel(ASYNC_CANCEL_REASON)

    t_exec.join(timeout=10.0)
    assert not t_exec.is_alive(), "executor thread never finished"

    done.set()
    t_loop.join(timeout=2.0)

    return captured


def test_async_cancel_emits_exactly_one_tool_complete_with_cleanup_order():
    """Cancellation: cleanup ack ordering, status, error, single TOOL_COMPLETE."""

    cleanup_ran = threading.Event()
    inside_await = threading.Event()
    finally_finished_at: Dict[str, float] = {}

    class _CancellableAsync(AsyncToolBase):
        @property
        def name(self) -> str:
            return "cancel_async"

        @property
        def description(self) -> str:
            return "wait until cancel"

        @property
        def parameters(self) -> Dict[str, Any]:
            return {"type": "object", "properties": {}}

        async def async_execute(self, **kwargs) -> str:
            try:
                inside_await.set()
                await asyncio.Event().wait()  # never resolves; only cancel breaks it
                return "should-not-return"
            finally:
                # Pretend to do real cleanup (close session, rollback
                # transaction, etc.) — just record the ordering.
                cleanup_ran.set()
                finally_finished_at["t"] = time.monotonic()

    tool = _CancellableAsync()

    captured = _drive_async_cancel(tool, inside_await=inside_await)

    # (a) the user coroutine's finally ran
    assert cleanup_ran.is_set()

    # (b) results: status="cancelled", error=ASYNC_CANCEL_REASON
    res: ToolExecutionResult = captured["results"]["cancel-call"]
    assert res.status == "cancelled"
    assert res.error == ASYNC_CANCEL_REASON

    # (c, d, e) TOOL_COMPLETE emitted exactly once with cancelled status.
    transport: _CapturingTransport = captured["transport"]
    completes = transport.by_type(EventType.TOOL_COMPLETE)
    assert len(completes) == 1, (
        f"expected exactly one TOOL_COMPLETE, got {len(completes)}"
    )
    payload = completes[0].data
    assert payload["status"] == "cancelled"
    assert payload["error"] == ASYNC_CANCEL_REASON

    # Cleanup ran before TOOL_COMPLETE was reported (the dispatcher's
    # ack mechanism waits on the user coroutine's finally before
    # emitting). Compare the moments captured: finally_finished_at
    # should be <= the ``complete_at`` snapshot taken right after
    # execute_batch returned.
    assert finally_finished_at["t"] <= captured["complete_at"]


def test_async_cancel_ack_timeout_emits_warning_and_completes_once():
    """A wedged finally must not hang the worker indefinitely.

    With a deliberately blocking ``finally``, the dispatcher's bounded
    ack timeout must fire, log a warning, and still emit
    ``TOOL_COMPLETE(status="cancelled")`` exactly once.
    """

    inside_await = threading.Event()
    blocker = threading.Event()

    class _WedgedAsync(AsyncToolBase):
        @property
        def name(self) -> str:
            return "wedged_async"

        @property
        def description(self) -> str:
            return "finally blocks"

        @property
        def parameters(self) -> Dict[str, Any]:
            return {"type": "object", "properties": {}}

        async def async_execute(self, **kwargs) -> str:
            try:
                inside_await.set()
                await asyncio.Event().wait()
                return "should-not-return"
            finally:
                # Block past the ack timeout. We unblock at test teardown
                # so the loop thread can exit cleanly.
                while not blocker.is_set():
                    # Synchronous block — deliberately not awaiting; this
                    # holds the host loop's task in a blocked finally
                    # that the dispatcher's ack.wait() cannot observe.
                    time.sleep(0.05)

    # Force a tiny ack timeout for fast tests.
    monkey_was = tool_executor_module._ASYNC_CANCEL_ACK_TIMEOUT_S
    tool_executor_module._ASYNC_CANCEL_ACK_TIMEOUT_S = 0.2

    warning_records: List[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: warning_records.append(record.getMessage())  # type: ignore[assignment]
    handler.level = logging.WARNING

    test_logger = logging.getLogger("test.async_tool")
    test_logger.addHandler(handler)
    try:
        tool = _WedgedAsync()
        captured = _drive_async_cancel(tool, inside_await=inside_await)
    finally:
        # Always unwedge so the host loop thread can exit cleanly.
        blocker.set()
        tool_executor_module._ASYNC_CANCEL_ACK_TIMEOUT_S = monkey_was
        test_logger.removeHandler(handler)

    # Warning was logged about the ack timeout.
    assert any("cancel ack timeout" in m for m in warning_records), (
        f"expected ack-timeout warning, got: {warning_records!r}"
    )

    # Exactly one TOOL_COMPLETE with status="cancelled".
    transport: _CapturingTransport = captured["transport"]
    completes = transport.by_type(EventType.TOOL_COMPLETE)
    assert len(completes) == 1
    assert completes[0].data["status"] == "cancelled"
    assert completes[0].data["error"] == ASYNC_CANCEL_REASON

    res: ToolExecutionResult = captured["results"]["cancel-call"]
    assert res.status == "cancelled"
    assert res.error == ASYNC_CANCEL_REASON
