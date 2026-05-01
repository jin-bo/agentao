"""Tool execution pipeline for Agentao."""

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING, Tuple

from ..permissions import PermissionEngine
from ..sandbox import SandboxPolicy
from ..tools import ToolRegistry
from ..transport import AgentEvent, EventType
from .name_repair import repair_tool_name
from .sanitize import normalize_tool_calls as _normalize_tool_calls
from .tool_executor import ToolExecutor
from .tool_planning import (
    ToolCallDecision,
    ToolCallPlanner,
    make_tool_result_message,
)
from .tool_result_formatter import ToolResultFormatter

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from ..harness.projection import HarnessPermissionEmitter, HarnessToolEmitter


_DOOM_HALT_MESSAGE = "Tool not executed (halted by doom-loop detection)."

# Maps the planner's routing enum to the public PermissionDecisionEvent
# outcome literal. ``CANCELLED`` is intentionally absent: the public
# event is emitted pre-Phase 2, before any user-cancel mutation.
_DECISION_OUTCOME = {
    ToolCallDecision.ALLOW: "allow",
    ToolCallDecision.DENY: "deny",
    ToolCallDecision.ASK: "prompt",
}


class ToolRunner:
    """Encapsulates the 4-phase tool execution pipeline.

    Phase 1: Doom-loop detection + permission decisions → _plans
    Phase 2: User confirmation (sequential, interactive)
    Phase 3: Parallel execution (ThreadPoolExecutor, 8 workers)
    Phase 4: Result ordering + truncation

    Call reset() at the start of each chat() invocation to clear doom-loop state.
    Call execute() for each set of tool_calls within the loop.
    """

    def __init__(
        self,
        tools: ToolRegistry,
        permission_engine: Optional[PermissionEngine],
        transport,  # Transport protocol instance
        logger,
        sandbox_policy: Optional[SandboxPolicy] = None,
        *,
        harness_tool_emitter: Optional["HarnessToolEmitter"] = None,
        harness_permission_emitter: Optional["HarnessPermissionEmitter"] = None,
        # ── Deprecated: kept for backward compatibility ──────────────────────
        confirmation_callback: Optional[Callable[[str, str, Dict[str, Any]], bool]] = None,
        step_callback: Optional[Callable[[Optional[str], Dict[str, Any]], None]] = None,
        output_callback: Optional[Callable[[str, str], None]] = None,
        tool_complete_callback: Optional[Callable[[str], None]] = None,
    ):
        self._tools = tools
        self._permission_engine = permission_engine
        self._transport = transport
        self._logger = logger
        self._sandbox_policy = sandbox_policy
        self._harness_tool_emitter = harness_tool_emitter
        self._harness_permission_emitter = harness_permission_emitter
        self._planner = ToolCallPlanner(tools, permission_engine, logger)
        self._executor = ToolExecutor(
            transport, logger, sandbox_policy,
            harness_tool_emitter=harness_tool_emitter,
        )
        self._formatter = ToolResultFormatter(transport, logger)
        self.readonly_mode: bool = False
        # Plugin hook rules — set by the agent after plugin loading.
        self._plugin_hook_rules: list = []
        # Session working directory for hook dispatchers (set by cli after plugin loading).
        self._working_directory: Optional[Path] = None
        # Session ID for hook payloads (set by cli after session start).
        self._session_id: Optional[str] = None

    def set_readonly_mode(self, enabled: bool) -> None:
        """Enable or disable readonly mode. When enabled, all non-read-only tools are denied."""
        previous = self.readonly_mode
        self.readonly_mode = enabled
        if previous == enabled:
            return
        # Step 6 replay event — only fires when the flag actually flips so
        # a no-op call from the CLI doesn't pollute the timeline.
        try:
            self._transport.emit(AgentEvent(EventType.READONLY_MODE_CHANGED, {
                "previous": previous,
                "current": enabled,
            }))
        except Exception:
            pass

    def reset(self) -> None:
        """Reset doom-loop counter. Call at the start of each chat() invocation."""
        self._planner.reset()

    def normalize_tool_calls(self, tool_calls: Any):
        """Surrogate-sanitize and name-repair tool_calls in one pass.

        Returns ``(cleaned_list, any_changed)``. The list is always safe
        for both history serialization and execution: when an SDK object
        is frozen / read-only, the corresponding entry is a
        ``SimpleNamespace`` proxy with cleaned fields. Mutable SDK
        objects are mutated in place (preserves identity).

        Both consumers (history serializer + ``execute()``) must iterate
        the returned list — never ``assistant_message.tool_calls``
        directly — otherwise frozen tool_calls leave history and
        execution divergent on id/name, which strict APIs reject.
        """
        valid = self._tools.tools
        return _normalize_tool_calls(
            tool_calls,
            repair_name_fn=lambda n: (
                None if n in valid else repair_tool_name(n, valid)
            ),
            logger=self._logger,
        )

    def execute(self, tool_calls, cancellation_token=None) -> Tuple[bool, List[Dict[str, Any]]]:
        """Run the 4-phase tool execution pipeline.

        Args:
            tool_calls: List of tool call objects from the LLM response.

        Returns:
            (doom_loop_triggered, tool_result_messages)
            - doom_loop_triggered: True if execution was halted by doom-loop detection.
            - tool_result_messages: List of {"role": "tool", ...} dicts to append to
              self.messages. Includes placeholder messages if doom-loop was triggered.
        """
        result_messages: List[Dict[str, Any]] = []

        # --- Phase 1: Planning (sequential, no I/O) ---
        # Doom-loop detection, JSON parse, tool lookup, and the
        # permission decision are all delegated to ToolCallPlanner.
        planning = self._planner.plan(tool_calls, readonly_mode=self.readonly_mode)
        result_messages.extend(planning.early_messages)

        if planning.doom_loop_triggered:
            # Strict Chat-Completions APIs reject the next request if any
            # assistant tool_call lacks a corresponding tool result. So
            # emit a placeholder for every tool_call in the batch — both
            # those that already passed planning AND those that were
            # never reached (they came after the offending call).
            #
            # Seed seen_ids from early_messages so we don't double-answer
            # the offending call (which already has its doom-loop message
            # in early_messages) or any prior parse/lookup-error calls.
            seen_ids: set = {
                msg["tool_call_id"] for msg in planning.early_messages
            }
            for _plan in planning.plans:
                result_messages.append(make_tool_result_message(
                    _plan.tool_call_id, _plan.function_name, _DOOM_HALT_MESSAGE,
                ))
                seen_ids.add(_plan.tool_call_id)
            # Calls past the doom-loop trip never reached the planner, so
            # their ids weren't normalized in place. Normalize here too —
            # a provider-omitted id would otherwise produce a placeholder
            # the strict API rejects (tool_call_id must be a string) and
            # break the next round-trip.
            from .identity import normalize_tool_call_id as _norm_id
            for _tc in tool_calls:
                _tc_id = _norm_id(getattr(_tc, "id", None))
                if _tc_id in seen_ids:
                    continue
                _fn = getattr(_tc, "function", None)
                _fn_name = getattr(_fn, "name", "?") if _fn is not None else "?"
                result_messages.append(make_tool_result_message(
                    _tc_id, _fn_name, _DOOM_HALT_MESSAGE,
                ))
                seen_ids.add(_tc_id)
            return True, result_messages

        _plans = planning.plans
        if not _plans:
            return False, result_messages

        # PermissionDecisionEvent must precede the tool's started event
        # for the same tool_call_id; firing here, before Phase 2 / 3,
        # honours that. Skip the per-plan loop entirely when no host is
        # subscribed — the alternative builds Pydantic models the
        # consumer never reads.
        if self._should_emit_permission_events():
            for _plan in _plans:
                self._emit_permission_event(_plan)

        # --- Phase 2: Confirmation (sequential, interactive) ---
        # All user-facing prompts happen here before any execution starts.
        for _plan in _plans:
            if _plan.decision == ToolCallDecision.ASK:
                _fn = _plan.function_name
                self._logger.info(f"Tool {_fn} requires confirmation")
                self._transport.emit(AgentEvent(EventType.TOOL_CONFIRMATION, {
                    "tool": _fn, "args": _plan.function_args,
                }))
                _confirmed = self._transport.confirm_tool(
                    _fn,
                    _plan.tool.description,
                    _plan.function_args,
                )
                if not _confirmed:
                    self._logger.info(f"Tool {_fn} execution cancelled by user")
                    _plan.decision = ToolCallDecision.CANCELLED
                    # No TOOL_START will fire for cancelled tools — reset spinner explicitly.
                    self._transport.emit(AgentEvent(EventType.TURN_START, {}))
                else:
                    self._logger.info(f"Tool {_fn} execution confirmed by user")
                    _plan.decision = ToolCallDecision.ALLOW

        # --- Phase 3: Parallel execution (delegated to ToolExecutor) ---
        _exec_results = self._executor.execute_batch(
            _plans,
            cancellation_token=cancellation_token,
            readonly_mode=self.readonly_mode,
            hook_rules=self._plugin_hook_rules,
            hook_cwd=self._working_directory,
            hook_session_id=self._session_id,
        )

        # --- Phase 4: Result formatting (delegated to ToolResultFormatter) ---
        result_messages.extend(self._formatter.format_batch(_plans, _exec_results))
        return False, result_messages

    # ------------------------------------------------------------------
    # Public-event helpers
    # ------------------------------------------------------------------

    def _should_emit_permission_events(self) -> bool:
        """Skip per-plan emit when no host is listening.

        Avoids ``new_decision_id`` + ``ActivePermissions`` + Pydantic
        construction per tool call in the (common) no-subscriber case.
        Falls back to ``True`` when the emitter has no stream handle to
        introspect — better to emit than silently drop.
        """
        emitter = self._harness_permission_emitter
        if emitter is None:
            return False
        stream = getattr(emitter, "_stream", None)
        check = getattr(stream, "_has_subscribers", None)
        if check is None:
            return True
        try:
            return bool(check())
        except Exception:
            return True

    def _emit_permission_event(self, plan) -> None:
        """Project one plan's permission decision into a public event.

        ``ASK`` maps to ``prompt`` because Phase 2 has not yet resolved
        to allow/cancel; cancellation is captured later by the matching
        :class:`ToolLifecycleEvent`.
        """
        outcome = _DECISION_OUTCOME.get(plan.decision)
        if outcome is None:
            # ``ToolCallDecision.CANCELLED`` only appears post-Phase 2;
            # the helper is called pre-Phase 2 so the branch is dead in
            # practice.
            return
        from ..runtime.identity import new_decision_id
        detail = plan.permission_detail
        matched_rule = detail.matched_rule if detail is not None else None
        reason = detail.reason if detail is not None else None
        try:
            self._harness_permission_emitter.emit(
                tool_name=plan.function_name,
                tool_call_id=plan.tool_call_id,
                decision_id=new_decision_id(),
                outcome=outcome,
                matched_rule=matched_rule,
                reason=reason,
            )
        except Exception:
            pass
