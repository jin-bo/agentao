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

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from ..permissions import PermissionDecision, PermissionDecisionDetail, PermissionEngine
from ..tools import RegistrableTool, ToolRegistry
from . import identity as _identity
from .arg_repair import parse_tool_arguments
from .name_repair import repair_tool_name


# Repeating identical (name, args_raw) this many times trips doom-loop.
DOOM_LOOP_THRESHOLD = 3

# Per-tool consecutive parse failures (with *different* malformed strings
# each time, so the identical-args counter doesn't catch them) that trip
# a parse-doom-loop. Without this, a model that keeps inventing fresh
# garbage JSON for the same tool would loop forever.
PARSE_FAILURE_THRESHOLD = 3


def _synth(
    decision: PermissionDecision,
    reason: str,
) -> PermissionDecisionDetail:
    """Synthesize a public-event detail for paths with no matched rule."""
    return PermissionDecisionDetail(
        decision, matched_rule=None, reason=reason,
    )


def _ensure_tool_call_id(tool_call: Any) -> str:
    """Return a stable, non-empty ``tool_call_id`` for ``tool_call``.

    The OpenAI SDK's ``ChatCompletionMessageToolCall`` is mutable, so we
    write the normalized id back onto the upstream object too. The
    assistant message in conversation history references the same object,
    so the API tool_result we send next round shares a matching id even
    when the provider returned ``None`` or an empty string. Mutation is
    best-effort: read-only or unusual shapes fall through with the
    normalized value still returned for the planner to store on the plan.
    """
    raw = getattr(tool_call, "id", None)
    normalized = _identity.normalize_tool_call_id(raw)
    if raw != normalized:
        try:
            tool_call.id = normalized
        except (AttributeError, TypeError):
            pass
    return normalized


def make_tool_result_message(
    tool_call_id: str, name: str, content: str,
) -> Dict[str, Any]:
    """Build a Chat-Completions ``role: tool`` message.

    Used wherever a tool_call must be answered without (or before) the
    tool actually running — early errors, doom-loop placeholders, etc.
    Centralised so the field set stays in lock-step with what strict
    APIs require.
    """
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": name,
        "content": content,
    }


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
    tool: RegistrableTool
    decision: ToolCallDecision
    # Normalized, non-empty ``tool_call_id`` for downstream phases. The
    # planner computes this once via :func:`identity.normalize_tool_call_id`
    # and best-effort mirrors it back onto ``tool_call.id`` so the API
    # tool_result message and the public lifecycle events share the same
    # identifier even for providers that omit ids. Always a string.
    tool_call_id: str = ""
    # Public-event provenance: the structured permission detail (matched
    # rule, reason, raw outcome) the runtime needs to emit a
    # :class:`PermissionDecisionEvent`. ``None`` means the engine
    # produced no rule match and the runner fell back to the tool's
    # own ``requires_confirmation`` attribute — in that case the event
    # still fires (with ``matched_rule=None``), classified by the
    # decision the planner finally settled on.
    permission_detail: Optional[PermissionDecisionDetail] = None

    def __post_init__(self) -> None:
        # Direct construction sites (tests, custom planners that bypass
        # ``ToolCallPlanner``) may leave ``tool_call_id`` unset. Derive
        # it from the upstream tool_call.id so production paths keep
        # working without forcing every callsite to repeat the planner's
        # normalization step. The planner's own callsite always passes a
        # non-empty value, so this branch is a no-op there.
        if not self.tool_call_id:
            self.tool_call_id = _identity.normalize_tool_call_id(
                getattr(self.tool_call, "id", None),
            )


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
        # Counts *consecutive* parse failures per tool name; reset on the
        # first successful parse for that tool. Distinct from
        # ``_doom_counter`` (which keys on identical-args repeats).
        self._consecutive_parse_failures: Counter = Counter()

    def reset(self) -> None:
        """Clear the doom-loop counter. Call between ``chat()`` invocations."""
        self._doom_counter.clear()
        self._consecutive_parse_failures.clear()

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
            # Normalize the provider-supplied tool_call_id once per call so
            # every downstream phase (permission events, lifecycle events,
            # tool-result formatting, late doom-loop placeholders) shares
            # the same stable string. Best-effort mirror onto ``tool_call.id``
            # keeps the API tool_result message in sync with the assistant
            # message that ToolRunner echoed back to the LLM.
            normalized_id = _ensure_tool_call_id(tool_call)

            doom_key = (function_name, function_args_raw)
            self._doom_counter[doom_key] += 1
            if self._doom_counter[doom_key] >= DOOM_LOOP_THRESHOLD:
                self._logger.warning(
                    f"Doom-loop detected: {function_name} called "
                    f"{DOOM_LOOP_THRESHOLD}+ times with identical args"
                )
                result.early_messages.append(make_tool_result_message(
                    normalized_id, function_name,
                    f"[Doom-loop detected] Tool '{function_name}' was called "
                    f"{DOOM_LOOP_THRESHOLD} times with identical arguments. "
                    f"Execution stopped to prevent an infinite loop. "
                    f"Please try a different approach or tool.",
                ))
                result.doom_loop_triggered = True
                return result

            try:
                function_args, repair_tags = parse_tool_arguments(function_args_raw)
            except ValueError as exc:
                self._consecutive_parse_failures[function_name] += 1
                self._logger.warning(
                    f"Tool '{function_name}' received unparseable arguments: {exc}"
                )
                if self._consecutive_parse_failures[function_name] >= PARSE_FAILURE_THRESHOLD:
                    self._logger.warning(
                        f"Parse-failure doom-loop: '{function_name}' produced "
                        f"unparseable arguments {PARSE_FAILURE_THRESHOLD}+ times"
                    )
                    result.early_messages.append(make_tool_result_message(
                        normalized_id, function_name,
                        f"[Parse-failure doom-loop] Tool '{function_name}' "
                        f"produced unparseable arguments "
                        f"{PARSE_FAILURE_THRESHOLD} times. Stopping to "
                        f"prevent an infinite loop. Try a different tool "
                        f"or approach.",
                    ))
                    result.doom_loop_triggered = True
                    return result
                result.early_messages.append(make_tool_result_message(
                    normalized_id, function_name,
                    f"Error: could not parse arguments for '{function_name}': {exc}. "
                    f"Please retry with valid JSON.",
                ))
                continue
            self._consecutive_parse_failures.pop(function_name, None)
            if repair_tags:
                self._logger.warning(
                    f"Tool '{function_name}' arguments repaired via "
                    f"{'+'.join(repair_tags)}"
                )

            try:
                tool = self._tools.get(function_name)
            except KeyError as exc:
                repaired = repair_tool_name(function_name, self._tools.tools)
                if repaired is not None:
                    self._logger.warning(
                        "Tool name '%s' repaired to '%s'", function_name, repaired,
                    )
                    function_name = repaired
                    tool = self._tools.get(repaired)
                else:
                    self._logger.warning(str(exc))
                    result.early_messages.append(make_tool_result_message(
                        normalized_id, function_name, str(exc),
                    ))
                    continue

            decision, permission_detail = self._decide(
                tool, function_name, function_args, readonly_mode,
            )

            result.plans.append(ToolCallPlan(
                tool_call=tool_call,
                function_name=function_name,
                function_args=function_args,
                tool=tool,
                decision=decision,
                tool_call_id=normalized_id,
                permission_detail=permission_detail,
            ))

        return result

    def _decide(
        self,
        tool: RegistrableTool,
        function_name: str,
        function_args: Dict[str, Any],
        readonly_mode: bool,
    ) -> tuple[ToolCallDecision, Optional[PermissionDecisionDetail]]:
        """Return both the routing decision and the public-event detail.

        For the readonly-mode short-circuit and for the
        ``requires_confirmation`` fallback we synthesize a detail with
        ``matched_rule=None`` so the public event still fires with the
        right ``outcome``.
        """
        if readonly_mode and not tool.is_read_only:
            return ToolCallDecision.DENY, _synth(
                PermissionDecision.DENY,
                "readonly mode blocks non-read-only tools",
            )

        engine_detail = (
            self._permission_engine.decide_detail(function_name, function_args)
            if self._permission_engine is not None
            else None
        )
        if engine_detail is not None and engine_detail.decision is PermissionDecision.ALLOW:
            return ToolCallDecision.ALLOW, engine_detail
        if engine_detail is not None and engine_detail.decision is PermissionDecision.DENY:
            return ToolCallDecision.DENY, engine_detail

        # Engine returned ASK or no match: fall through to the tool's
        # own confirmation setting, preserving the engine detail so the
        # public event still reports any matched rule.
        if tool.requires_confirmation:
            return ToolCallDecision.ASK, engine_detail or _synth(
                PermissionDecision.ASK,
                "tool requires_confirmation fallback",
            )
        return ToolCallDecision.ALLOW, engine_detail or _synth(
            PermissionDecision.ALLOW,
            "no rule matched; tool does not require confirmation",
        )
