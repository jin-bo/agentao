"""Phase 1 of the tool execution pipeline: planning.

Pure(-ish) classification of a batch of LLM ``tool_calls`` into typed
``ToolCallPlan`` instances. No I/O, no user prompts, no execution — those
belong to later phases.

The doom-loop counter lives here because doom-loop detection *is* a
planning decision: identical ``(name, args_raw)`` repeated N times means
"stop planning the rest of this batch", which is structurally a planner
concern. ``ToolRunner.reset()`` delegates to ``ToolCallPlanner.reset()``.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from ..permissions import PermissionDecision, PermissionEngine
from ..tools import Tool, ToolRegistry


# Repeating identical (name, args_raw) this many times trips doom-loop.
DOOM_LOOP_THRESHOLD = 3


class ToolCallDecision(Enum):
    """Lifecycle decision for a single tool call.

    Planner emits ``ALLOW`` / ``DENY`` / ``ASK``. The confirmation phase
    converts ``ASK`` into ``ALLOW`` or ``CANCELLED``. The executor only
    ever sees ``ALLOW`` / ``DENY`` / ``CANCELLED``.
    """

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    CANCELLED = "cancelled"


@dataclass
class ToolCallPlan:
    """A single tool call that has passed parsing + lookup + decision."""

    tool_call: Any  # OpenAI tool_call object — exposes .id and .function
    function_name: str
    function_args: Dict[str, Any]
    tool: Tool
    decision: ToolCallDecision


@dataclass
class ToolPlanningResult:
    """Output of one planning pass over a batch of ``tool_calls``."""

    plans: List[ToolCallPlan] = field(default_factory=list)
    # Pre-formed tool result messages for calls that could not be planned
    # at all (JSON parse error, unknown tool, doom-loop trip). Appended by
    # the runner verbatim, in order.
    early_messages: List[Dict[str, Any]] = field(default_factory=list)
    # When True, the runner must add "not executed" placeholder messages
    # for every accepted plan in this batch and return without executing.
    doom_loop_triggered: bool = False


class ToolCallPlanner:
    """Phase 1: classify each ``tool_call`` from the LLM into a plan."""

    def __init__(
        self,
        tools: ToolRegistry,
        permission_engine: Optional[PermissionEngine],
        logger,
    ):
        self._tools = tools
        self._permission_engine = permission_engine
        self._logger = logger
        self._doom_counter: Counter = Counter()

    def reset(self) -> None:
        """Clear the doom-loop counter. Call between ``chat()`` invocations."""
        self._doom_counter.clear()

    def plan(
        self,
        tool_calls,
        *,
        readonly_mode: bool = False,
    ) -> ToolPlanningResult:
        """Classify a batch of tool_calls.

        Iteration order is preserved in both ``plans`` and ``early_messages``
        so the runner can emit tool result messages in the order the LLM
        emitted the calls.
        """
        result = ToolPlanningResult()

        for tool_call in tool_calls:
            function_name = tool_call.function.name
            function_args_raw = tool_call.function.arguments

            # --- Doom-loop detection (early exit for the whole batch) ---
            doom_key = (function_name, function_args_raw)
            self._doom_counter[doom_key] += 1
            if self._doom_counter[doom_key] >= DOOM_LOOP_THRESHOLD:
                self._logger.warning(
                    f"Doom-loop detected: {function_name} called "
                    f"{DOOM_LOOP_THRESHOLD}+ times with identical args"
                )
                result.early_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": function_name,
                    "content": (
                        f"[Doom-loop detected] Tool '{function_name}' was called "
                        f"{DOOM_LOOP_THRESHOLD} times with identical arguments. "
                        f"Execution stopped to prevent an infinite loop. "
                        f"Please try a different approach or tool."
                    ),
                })
                result.doom_loop_triggered = True
                return result

            # --- JSON parse ---
            try:
                function_args = json.loads(function_args_raw)
            except json.JSONDecodeError as exc:
                self._logger.warning(
                    f"Tool '{function_name}' received invalid JSON arguments: {exc}"
                )
                result.early_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": function_name,
                    "content": (
                        f"Error: could not parse arguments for '{function_name}': {exc}. "
                        f"Please retry with valid JSON."
                    ),
                })
                continue

            # --- Tool lookup ---
            try:
                tool = self._tools.get(function_name)
            except KeyError as exc:
                self._logger.warning(str(exc))
                result.early_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": function_name,
                    "content": str(exc),
                })
                continue

            # --- Decision ---
            decision = self._decide(
                tool, function_name, function_args, readonly_mode,
            )

            result.plans.append(ToolCallPlan(
                tool_call=tool_call,
                function_name=function_name,
                function_args=function_args,
                tool=tool,
                decision=decision,
            ))

        return result

    def _decide(
        self,
        tool: Tool,
        function_name: str,
        function_args: Dict[str, Any],
        readonly_mode: bool,
    ) -> ToolCallDecision:
        # Readonly mode short-circuits everything else for non-read-only tools.
        if readonly_mode and not tool.is_read_only:
            return ToolCallDecision.DENY

        if self._permission_engine is not None:
            engine = self._permission_engine.decide(function_name, function_args)
            if engine == PermissionDecision.ALLOW:
                return ToolCallDecision.ALLOW
            if engine == PermissionDecision.DENY:
                return ToolCallDecision.DENY
            # engine returned ASK → fall through to the tool's own setting

        return (
            ToolCallDecision.ASK
            if tool.requires_confirmation
            else ToolCallDecision.ALLOW
        )
