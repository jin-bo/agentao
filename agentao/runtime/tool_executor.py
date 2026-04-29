"""Phase 3 of the tool execution pipeline: parallel single-tool execution.

Owns: per-tool-instance locks, sandbox profile injection, output_callback
wiring, exception classification (TaskComplete, SandboxMisconfiguredError,
generic), TOOL_START / TOOL_COMPLETE event emission, and plugin hook
lifecycle (pre / post / post-failure).

The executor itself is stateless w.r.t. plugin hook configuration —
``hook_rules``, ``hook_cwd``, ``hook_session_id`` are passed into
``execute_batch`` by the runner. This keeps mutable per-session state on
the runner (where the cli/agent already manages it) and lets the executor
be reused across batches.

Hook empty-rules guards live inside the dispatch helpers (``_dispatch_*``)
so the hot ``_execute_one`` path is free of ``if rules:`` noise.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..agents import TaskComplete
from ..sandbox import SandboxMisconfiguredError, SandboxPolicy
from ..tools.base import AsyncToolBase
from ..transport import AgentEvent, EventType
from .tool_planning import ToolCallDecision, ToolCallPlan


# Bounded ack timeout for AsyncTool cancellation. Long enough for typical
# async cleanup (closing client sessions, rolling back transactions);
# short enough that a stalled host loop cannot hang the worker thread
# indefinitely. Module-level so tests can monkeypatch.
_ASYNC_CANCEL_ACK_TIMEOUT_S = 5.0


# Reason string set on ``CancellationToken`` when ``Agentao.arun()`` forwards
# an ``asyncio.CancelledError``, and the fallback emitted by the dispatcher
# when the token has no reason of its own. Shared so the agent surface, the
# dispatcher, and tests stay in lock-step on the single canonical value.
ASYNC_CANCEL_REASON = "async-cancel"


@dataclass
class ToolExecutionResult:
    """Outcome of a single tool invocation."""

    fn_name: str
    result: str
    status: str  # "ok" | "error" | "cancelled"
    duration_ms: int
    error: Optional[str] = None


@dataclass
class _AsyncToolOutcome:
    """Internal: shape returned by :meth:`ToolExecutor._run_async_tool`.

    Exactly one field is meaningful per call:

    - ``result_text`` carries the success string (``cancel_result`` is
      None) — propagates back into the existing success/error tail of
      ``_execute_one``.
    - ``cancel_result`` carries the fully-formed cancelled
      :class:`ToolExecutionResult` (``result_text`` is unused) —
      ``TOOL_COMPLETE`` has already been emitted from inside the
      helper, so ``_execute_one`` must short-circuit the success/error
      tail to avoid double-emit.
    """

    result_text: str = ""
    cancel_result: Optional[ToolExecutionResult] = None


class ToolExecutor:
    """Phase 3: run a batch of confirmed plans, return per-call results."""

    def __init__(
        self,
        transport,
        logger,
        sandbox_policy: Optional[SandboxPolicy] = None,
    ):
        self._transport = transport
        self._logger = logger
        self._sandbox_policy = sandbox_policy
        # Adapter is stateless; reuse a single instance across calls.
        from ..plugins.hooks import ClaudeHookPayloadAdapter
        self._hook_adapter = ClaudeHookPayloadAdapter()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def execute_batch(
        self,
        plans: List[ToolCallPlan],
        *,
        cancellation_token=None,
        readonly_mode: bool = False,
        hook_rules: Optional[list] = None,
        hook_cwd: Optional[Path] = None,
        hook_session_id: Optional[str] = None,
    ) -> Dict[str, ToolExecutionResult]:
        """Execute a batch of plans in parallel; preserve fast-path for size 1.

        Per-tool-instance locks serialize concurrent calls to the same tool
        within this batch (protects ``output_callback`` state). Different
        tool instances run in parallel on a thread pool (max 8 workers).
        """
        # Build per-batch locks: same tool instance → same lock.
        tool_locks: Dict[int, threading.Lock] = {}
        for plan in plans:
            tid = id(plan.tool)
            if tid not in tool_locks:
                tool_locks[tid] = threading.Lock()

        results: Dict[str, ToolExecutionResult] = {}

        # Fix #6 (preserved): skip ThreadPoolExecutor overhead for the
        # common single-tool case.
        if len(plans) == 1:
            call_id, info = self._execute_one(
                plans[0],
                tool_locks,
                cancellation_token=cancellation_token,
                readonly_mode=readonly_mode,
                hook_rules=hook_rules,
                hook_cwd=hook_cwd,
                hook_session_id=hook_session_id,
            )
            results[call_id] = info
        else:
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = {
                    pool.submit(
                        self._execute_one,
                        p,
                        tool_locks,
                        cancellation_token=cancellation_token,
                        readonly_mode=readonly_mode,
                        hook_rules=hook_rules,
                        hook_cwd=hook_cwd,
                        hook_session_id=hook_session_id,
                    ): p
                    for p in plans
                }
                for future in as_completed(futures):
                    call_id, info = future.result()
                    results[call_id] = info

        return results

    # ------------------------------------------------------------------
    # Single-tool execution
    # ------------------------------------------------------------------

    def _execute_one(
        self,
        plan: ToolCallPlan,
        tool_locks: Dict[int, threading.Lock],
        *,
        cancellation_token,
        readonly_mode: bool,
        hook_rules: Optional[list],
        hook_cwd: Optional[Path],
        hook_session_id: Optional[str],
    ) -> Tuple[str, ToolExecutionResult]:
        fn = plan.function_name
        args = plan.function_args
        tool = plan.tool
        tc = plan.tool_call
        decision = plan.decision
        call_id = tc.id
        t0 = time.monotonic()

        self._transport.emit(AgentEvent(EventType.TOOL_START, {
            "tool": fn, "args": args, "call_id": call_id,
        }))

        if decision == ToolCallDecision.DENY:
            self._logger.info(f"Tool {fn} denied")
            if readonly_mode and not tool.is_read_only:
                result_text = (
                    f"[Readonly mode] Tool '{fn}' is blocked — "
                    f"only read-only tools are permitted in readonly mode."
                )
            else:
                result_text = (
                    f"Tool execution denied: '{fn}' is not permitted "
                    f"by the current permission rules."
                )
            self._emit_complete(fn, call_id, "cancelled", 0, "denied by permission engine")
            return call_id, ToolExecutionResult(
                fn_name=fn, result=result_text, status="cancelled",
                duration_ms=0, error="denied by permission engine",
            )

        if decision == ToolCallDecision.CANCELLED:
            result_text = (
                f"Tool execution cancelled by user. "
                f"The user declined to execute {fn}."
            )
            self._emit_complete(fn, call_id, "cancelled", 0, "cancelled by user")
            return call_id, ToolExecutionResult(
                fn_name=fn, result=result_text, status="cancelled",
                duration_ms=0, error="cancelled by user",
            )

        # ALLOW path
        # Propagate cancellation token so AgentToolWrapper can pass it down
        # to nested sub-agent chat() calls.
        if cancellation_token and hasattr(tool, "_cancellation_token"):
            tool._cancellation_token = cancellation_token

        # Pre-execution cancellation check (e.g. Ctrl+C fired while other
        # parallel tools were executing).
        if cancellation_token and cancellation_token.is_cancelled:
            result_text = f"[Operation Cancelled] {cancellation_token.reason}"
            duration_ms = round((time.monotonic() - t0) * 1000)
            self._emit_complete(fn, call_id, "cancelled", duration_ms, "cancelled by user")
            return call_id, ToolExecutionResult(
                fn_name=fn, result=result_text, status="cancelled",
                duration_ms=duration_ms, error="cancelled by user",
            )

        self._dispatch_pre_tool_hook(
            fn, args, rules=hook_rules, cwd=hook_cwd, session_id=hook_session_id,
        )

        # Inject macOS sandbox profile for run_shell_command when policy is
        # enabled. Private kwarg — never exposed to LLM or to plugin
        # Pre/Post hooks (which JSON-serialize tool_args). If the policy is
        # enabled but broken, resolve() raises and we fail-closed rather
        # than silently running unsandboxed.
        call_args = args
        sandbox_error: Optional[SandboxMisconfiguredError] = None
        if self._sandbox_policy is not None and fn == "run_shell_command":
            try:
                profile = self._sandbox_policy.resolve(fn, args)
            except SandboxMisconfiguredError as sbe:
                sandbox_error = sbe
            else:
                if profile is not None:
                    call_args = {**args, "_sandbox_profile": profile}

        errored = False
        error_msg: Optional[str] = None
        # ``_emit_complete`` has already fired inside ``_run_async_tool``
        # when this is set; the success/error tail below must short-circuit.
        async_cancel_outcome: Optional[ToolExecutionResult] = None
        with tool_locks[id(tool)]:
            if hasattr(tool, "output_callback"):
                tool.output_callback = (
                    lambda chunk, _name=fn, _cid=call_id: self._transport.emit(
                        AgentEvent(EventType.TOOL_OUTPUT, {
                            "tool": _name, "chunk": chunk, "call_id": _cid,
                        })
                    )
                )
            try:
                if sandbox_error is not None:
                    raise sandbox_error
                if isinstance(tool, AsyncToolBase):
                    async_outcome = self._run_async_tool(
                        plan=plan,
                        call_args=call_args,
                        t0=t0,
                        cancellation_token=cancellation_token,
                    )
                    if async_outcome.cancel_result is not None:
                        async_cancel_outcome = async_outcome.cancel_result
                        # Fall through to ``finally`` for output_callback
                        # cleanup; ``_execute_one`` short-circuits below.
                    else:
                        result_text = async_outcome.result_text
                else:
                    result_text = tool.execute(**call_args)
            except TaskComplete as tc_exc:
                result_text = tc_exc.result
            except SandboxMisconfiguredError as sbe:
                errored = True
                error_msg = "sandbox misconfigured"
                result_text = (
                    f"[Sandbox error] {sbe}\n\n"
                    f"The command was NOT executed. This is fail-closed "
                    f"behavior: running shell commands without the "
                    f"sandbox the user enabled would be worse than "
                    f"refusing to run them."
                )
            except Exception as exc:
                errored = True
                error_msg = str(exc)[:200]
                result_text = f"Error executing {fn}: {str(exc)}"
            finally:
                if hasattr(tool, "output_callback"):
                    tool.output_callback = None

        # AsyncTool token-cancel short-circuit: TOOL_COMPLETE already
        # emitted inside _run_async_tool; bypass the success/error tail
        # (no post-tool hook, matches DENY / pre-cancel paths).
        if async_cancel_outcome is not None:
            return call_id, async_cancel_outcome

        status = "error" if errored else "ok"
        duration_ms = round((time.monotonic() - t0) * 1000)
        self._emit_complete(fn, call_id, status, duration_ms, error_msg)

        if errored:
            self._dispatch_post_tool_failure_hook(
                fn, args, error_msg,
                rules=hook_rules, cwd=hook_cwd, session_id=hook_session_id,
            )
        else:
            self._dispatch_post_tool_hook(
                fn, args, result_text,
                rules=hook_rules, cwd=hook_cwd, session_id=hook_session_id,
            )

        return call_id, ToolExecutionResult(
            fn_name=fn, result=result_text, status=status,
            duration_ms=duration_ms, error=error_msg,
        )

    # ------------------------------------------------------------------
    # AsyncTool dispatch
    # ------------------------------------------------------------------

    def _run_async_tool(
        self,
        *,
        plan: ToolCallPlan,
        call_args: Dict[str, Any],
        t0: float,
        cancellation_token,
    ) -> _AsyncToolOutcome:
        """Bridge an :class:`AsyncToolBase` invocation onto the host loop.

        Two paths:

        1. ``cancellation_token.runtime_loop`` is a running loop (set by
           :meth:`Agentao.arun`): schedule the coroutine via
           :func:`asyncio.run_coroutine_threadsafe` and block on the
           returned future. A token ``add_done_callback`` arms
           ``fut.cancel()`` so token-driven cancel propagates onto the
           host loop. A bridged coroutine signals a ``threading.Event``
           in its ``finally`` so that on cancel we can wait for the user
           coroutine's own cleanup before reporting ``TOOL_COMPLETE``.

        2. No host loop captured (sync :meth:`Agentao.chat` path):
           fallback to :func:`asyncio.run`. Supports only
           loop-independent async tools; tools that hold loop-affine
           state must be invoked via ``arun()``.
        """
        tool = plan.tool
        assert isinstance(tool, AsyncToolBase)
        fn = plan.function_name
        call_id = plan.tool_call.id

        runtime_loop = (
            cancellation_token.runtime_loop
            if cancellation_token is not None
            else None
        )

        # Sync fallback — fresh loop on the worker thread; no token
        # cancellation hookup is possible (asyncio.run owns the loop).
        if runtime_loop is None or not runtime_loop.is_running():
            result_text = asyncio.run(tool.async_execute(**call_args))
            return _AsyncToolOutcome(result_text=result_text)

        ack = threading.Event()

        async def _bridged():
            try:
                return await tool.async_execute(**call_args)
            finally:
                # Runs after the user coroutine's own try/finally cleanup
                # on the host loop. Signals the worker thread that it is
                # safe to surface the cancel to TOOL_COMPLETE.
                ack.set()

        fut = asyncio.run_coroutine_threadsafe(_bridged(), runtime_loop)
        remove: Optional[Callable[[], None]] = None
        if cancellation_token is not None:
            remove = cancellation_token.add_done_callback(lambda: fut.cancel())

        try:
            try:
                result_text = fut.result()
                # Success: _bridged()'s finally has already run, so ack is set.
                return _AsyncToolOutcome(result_text=result_text)
            except concurrent.futures.CancelledError:
                # Token-driven cancel. Wait for _bridged()'s finally to
                # run on the host loop so the user coroutine's cleanup
                # has completed before we report TOOL_COMPLETE. Proceed
                # (with a logged warning) past the bounded timeout so a
                # stalled host loop cannot hang the worker indefinitely.
                if not ack.wait(timeout=_ASYNC_CANCEL_ACK_TIMEOUT_S):
                    self._logger.warning(
                        "AsyncTool %s: cancel ack timeout after %.1fs; "
                        "emitting TOOL_COMPLETE without confirmed "
                        "coroutine cleanup.",
                        fn,
                        _ASYNC_CANCEL_ACK_TIMEOUT_S,
                    )
                duration_ms = round((time.monotonic() - t0) * 1000)
                # Match the existing executor convention of putting a
                # short human-readable reason in the error field. Fall
                # back when the token has no reason (it may be cancelled
                # by something other than arun() forwarding
                # asyncio.CancelledError).
                reason = (
                    cancellation_token.reason
                    if cancellation_token is not None and cancellation_token.reason
                    else ASYNC_CANCEL_REASON
                )
                self._emit_complete(
                    fn, call_id, "cancelled", duration_ms, reason,
                )
                return _AsyncToolOutcome(
                    cancel_result=ToolExecutionResult(
                        fn_name=fn,
                        result="Tool execution cancelled.",
                        status="cancelled",
                        duration_ms=duration_ms,
                        error=reason,
                    ),
                )
            # Other exceptions (TypeError, RuntimeError, user
            # ValueError, TaskComplete, SandboxMisconfiguredError, ...)
            # propagate to the caller — `_execute_one`'s outer
            # try/except classifies them exactly like sync tools.
        finally:
            if remove is not None:
                remove()

    def _emit_complete(
        self,
        fn: str,
        call_id: str,
        status: str,
        duration_ms: int,
        error: Optional[str],
    ) -> None:
        self._transport.emit(AgentEvent(EventType.TOOL_COMPLETE, {
            "tool": fn, "call_id": call_id, "status": status,
            "duration_ms": duration_ms, "error": error,
        }))

    # ------------------------------------------------------------------
    # Plugin hook dispatch (each helper short-circuits on empty rules)
    # ------------------------------------------------------------------

    def _dispatch_pre_tool_hook(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        *,
        rules: Optional[list],
        cwd: Optional[Path],
        session_id: Optional[str],
    ) -> None:
        if not rules:
            return
        try:
            from ..plugins.hooks import PluginHookDispatcher
            payload = self._hook_adapter.build_pre_tool_use(
                tool_name=tool_name, tool_input=tool_args, session_id=session_id,
            )
            dispatcher = PluginHookDispatcher(cwd=cwd)
            dispatcher.dispatch_pre_tool_use(payload=payload, rules=rules)
        except Exception as exc:
            self._logger.warning("PreToolUse hook dispatch error: %s", exc)

    def _dispatch_post_tool_hook(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        result: str,
        *,
        rules: Optional[list],
        cwd: Optional[Path],
        session_id: Optional[str],
    ) -> None:
        if not rules:
            return
        try:
            from ..plugins.hooks import PluginHookDispatcher
            payload = self._hook_adapter.build_post_tool_use(
                tool_name=tool_name, tool_input=tool_args,
                tool_output=result if isinstance(result, str) else str(result),
                session_id=session_id,
            )
            dispatcher = PluginHookDispatcher(cwd=cwd)
            dispatcher.dispatch_post_tool_use(payload=payload, rules=rules)
        except Exception as exc:
            self._logger.warning("PostToolUse hook dispatch error: %s", exc)

    def _dispatch_post_tool_failure_hook(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        error: Optional[str],
        *,
        rules: Optional[list],
        cwd: Optional[Path],
        session_id: Optional[str],
    ) -> None:
        if not rules:
            return
        try:
            from ..plugins.hooks import PluginHookDispatcher
            payload = self._hook_adapter.build_post_tool_use_failure(
                tool_name=tool_name, tool_input=tool_args, error=error,
                session_id=session_id,
            )
            dispatcher = PluginHookDispatcher(cwd=cwd)
            dispatcher.dispatch_post_tool_use_failure(
                payload=payload, rules=rules,
            )
        except Exception as exc:
            self._logger.warning("PostToolUseFailure hook dispatch error: %s", exc)
