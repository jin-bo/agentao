"""Tool execution pipeline for Agentao."""

from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Tuple

from ..capabilities.shell_spec import Deny
from ..permissions import PermissionDecision, PermissionEngine, PermissionMode
from ..sandbox import SandboxPolicy
from ..tools import ToolRegistry
from ..transport import AgentEvent, EventType
from .name_repair import repair_tool_name
from .sanitize import normalize_tool_calls as _normalize_tool_calls
from .tool_executor import ToolExecutor
from .tool_planning import (
    ToolCallDecision,
    ToolCallPlanner,
    _decided_call,
    _denied,
    _shell_spec_of,
    _synth,
    make_tool_result_message,
    pre_tool_hook_reason,
)
from .tool_result_formatter import ToolResultFormatter

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from ..host.projection import HostPermissionEmitter, HostToolEmitter


_DOOM_HALT_MESSAGE = "Tool not executed (halted by doom-loop detection)."

# Maps the planner's routing enum to the public PermissionDecisionEvent
# outcome literal. ``CANCELLED`` is intentionally absent: the public
# event is emitted pre-Phase 2, before any user-cancel mutation.
_DECISION_OUTCOME = {
    ToolCallDecision.ALLOW: "allow",
    ToolCallDecision.DENY: "deny",
    ToolCallDecision.ASK: "prompt",
}


#: Intersection order for the re-decide: stricter wins, never looser.
_STRICTNESS = {
    ToolCallDecision.ALLOW: 0,
    ToolCallDecision.ASK: 1,
    ToolCallDecision.DENY: 2,
    ToolCallDecision.CANCELLED: 3,
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
        host_tool_emitter: Optional["HostToolEmitter"] = None,
        host_permission_emitter: Optional["HostPermissionEmitter"] = None,
    ):
        self._tools = tools
        self._permission_engine = permission_engine
        self._transport = transport
        self._logger = logger
        self._sandbox_policy = sandbox_policy
        self._host_tool_emitter = host_tool_emitter
        self._host_permission_emitter = host_permission_emitter
        self._planner = ToolCallPlanner(tools, permission_engine, logger)
        self._executor = ToolExecutor(
            transport, logger, sandbox_policy,
            host_tool_emitter=host_tool_emitter,
        )
        self._formatter = ToolResultFormatter(transport, logger)
        self.readonly_mode: bool = False
        #: The ``continue: false`` a ``PreToolUse`` / ``PostToolUse*`` hook
        #: returned for the batch that just ran, in **plan order**, or ``None``.
        #: Read by the chat loop immediately after ``execute()``; reset at the
        #: top of every call so a stop can never leak into the next batch and
        #: so phase 1.5's write survives to the end of the pipeline.
        self.last_hook_stop: Optional[str] = None
        # Plugin hook rules — set by the agent after plugin loading.
        self._plugin_hook_rules: list = []
        # Session working directory for hook dispatchers (set by cli after plugin loading).
        self._working_directory: Optional[Path] = None
        # Session ID for hook payloads (set by cli after session start).
        self._session_id: Optional[str] = None

    def set_permission_engine(self, engine: Optional[PermissionEngine]) -> None:
        """Decide tool calls with ``engine`` from the next batch on.

        The planner makes the decision and holds its own reference. Assigning
        only ``_permission_engine`` on the runner changed nothing, which is how
        a sub-agent ran without any of its permission rules.
        """
        self._permission_engine = engine
        self._planner._permission_engine = engine

    def set_readonly_mode(self, enabled: bool) -> None:
        """Set the runner's read-only flag, one of the mode's **two** switches.

        ``True`` denies every tool whose ``is_read_only`` is ``False``.
        ``False`` only clears *this* switch: read-only still applies while the
        permission engine's ``active_mode`` is ``READ_ONLY``, because
        :meth:`readonly_active` honours either. Leaving read-only therefore
        means switching the engine's mode too — every in-tree caller
        (``cli/app.py::_apply_mode``, ``/plan``, the transport's "allow all"
        answer, ``cli/run.py``) sets the engine first and the flag second.

        ``READONLY_MODE_CHANGED`` reports this flag, not the effective
        posture: it is the replay record of the switch the caller flipped.
        """
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

    def readonly_active(self) -> bool:
        """Whether read-only mode applies: the flag above, or the engine's mode.

        The engine's ``read-only`` preset is an empty rule list; this gate is
        what enforces it. A caller that set only the engine's mode (ACP
        ``session/set_mode``, an embedded host calling ``set_mode``, a
        sub-agent's engine snapshot) got the empty preset alone, so writes
        and shell fell through to ASK and ``save_memory`` ran.

        Public because it is the supported read of the *effective* posture:
        ``readonly_mode`` answers only for the flag, and the sub-agent
        wrapper's ``readonly_mode_getter`` has to propagate the effective
        one (``tooling/agent_tools.py``).
        """
        # ``getattr`` absorbs a planner or engine without the attribute, and
        # ``is`` against the member means only a real READ_ONLY turns the gate
        # on. An engine whose ``active_mode`` *raises* fails **closed**: this
        # is called at the top of ``execute()``, outside any deny path, so
        # propagating would end the turn instead of restricting it — and an
        # engine that cannot report its mode is not permission to write.
        engine = getattr(self._planner, "_permission_engine", None)
        if self.readonly_mode:
            return True
        try:
            return getattr(engine, "active_mode", None) is PermissionMode.READ_ONLY
        except Exception as exc:
            self._logger.warning(
                "permission engine could not report active_mode (%s); "
                "treating the session as read-only", exc,
            )
            return True

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
        # Reset **here**, at the top, not after phase 4: every early return
        # below (doom-loop, no plans) skips the tail, so a bottom reset leaves
        # the previous batch's stop live for the next one — and it also wiped
        # the stop a ``PreToolUse`` hook sets during phase 1.5, which is the
        # only place that value is ever written before phase 3.
        self.last_hook_stop = None

        # --- Phase 1: Planning (sequential, no I/O) ---
        # Doom-loop detection, JSON parse, tool lookup, and the
        # permission decision are all delegated to ToolCallPlanner.
        # Read once for the batch: phase 3 labels a denial "[Readonly mode]"
        # from this value, and a mode switch during phase 2 (the CLI's "allow
        # all" answer, a host thread calling ``set_mode``) must not make that
        # label disagree with the reason phase 1 recorded.
        readonly = self.readonly_active()
        planning = self._planner.plan(tool_calls, readonly_mode=readonly)
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

        # --- Phase 1.5: PreToolUse hook policy (decision-capable) ---
        # A PreToolUse hook may deny a tool call outright or downgrade it
        # to "ask" (which then flows through the same Phase 2 confirmation
        # path). This runs *before* the PermissionDecisionEvent emit and
        # *before* any tool starts, so a hook-derived decision lands in the
        # public event ordering (decision precedes started) without
        # special-casing. A hook ``allow`` is a no-op — it never downgrades
        # an engine deny/ask or a tool's own requires_confirmation ask.
        if self._plugin_hook_rules:
            # The batch's value, not a second live read: a hook's
            # ``updatedInput`` re-decides the call, and re-reading here would
            # judge the rewrite under a posture phase 1 never saw and phase 3
            # will not label.
            self._apply_pre_tool_use_hooks(_plans, readonly_mode=readonly)

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
            readonly_mode=readonly,
            hook_rules=self._plugin_hook_rules,
            hook_cwd=self._working_directory,
            hook_session_id=self._session_id,
        )

        # --- Phase 4: Result formatting (delegated to ToolResultFormatter) ---
        result_messages.extend(self._formatter.format_batch(_plans, _exec_results))

        # A ``PostToolUse*`` hook's ``continue: false`` is a **turn-level** stop
        # computed inside a worker, three frames below anything that can act on
        # it. It rides home on the result and is surfaced here, on the runner,
        # rather than as a third tuple element: ``execute``'s 2-tuple has
        # callers whose tests are not about hooks, and ``Agentao.last_turn`` is
        # the codebase's own precedent for "read it off the object right after
        # the call".
        #
        # Arbitration is **plan order** — the model's own tool-call order — and
        # never completion order, which would make the surfaced reason vary run
        # to run for the same batch.
        # A ``PreToolUse`` stop was recorded in phase 1.5 and is *earlier*, so
        # it wins; only look at the Post* verdicts when nothing has claimed the
        # slot yet.
        if self.last_hook_stop is None:
            for _plan in _plans:
                _info = _exec_results.get(_plan.tool_call_id)
                if _info is not None and _info.hook_stop_reason is not None:
                    self.last_hook_stop = _info.hook_stop_reason
                    break

        # `systemMessage` and the one-shot field diagnostics a `PostToolUse*`
        # hook produced go to the **user**, and the worker that computed them
        # has no user surface. They ride home on the result and are emitted on
        # the same `PLUGIN_HOOK_FIRED` payload `UserPromptSubmit` uses (G1's
        # transport half) — otherwise they are computed and dropped, which is
        # the "a sink is not a route" defect this event set out to close.
        _notices: List[str] = []
        for _plan in _plans:
            _info = _exec_results.get(_plan.tool_call_id)
            if _info is not None:
                _notices.extend(_info.hook_user_notices or [])
        if _notices:
            try:
                self._transport.emit(AgentEvent(EventType.PLUGIN_HOOK_FIRED, {
                    # `PostToolUse*`, not `PostToolUse`: the batch mixes both
                    # events — a failing call routes through
                    # `PostToolUseFailure` — and the notices are aggregated
                    # across it, so naming one of the two would be wrong for
                    # every notice the other produced.
                    "hook_name": "PostToolUse*",
                    "rule_count": len(self._plugin_hook_rules),
                    "outcome": "notice",
                    "user_notices": _notices,
                }))
            except Exception:
                pass

        return False, result_messages

    # ------------------------------------------------------------------
    # PreToolUse hook policy (Phase 1.5)
    # ------------------------------------------------------------------

    def _apply_pre_tool_use_hooks(
        self, plans, *, readonly_mode: Optional[bool] = None,
    ) -> None:
        """Let PreToolUse hooks deny / downgrade-to-ask each planned call.

        Mutates ``plan.decision`` / ``plan.permission_detail`` in place so
        the downstream PermissionDecisionEvent and Phase 2 confirmation see
        the post-hook state. Hook-derived decisions are attributed via the
        existing ``reason`` field (prefixed ``pre-tool-hook``); no new
        public field is introduced. Dispatch errors are swallowed with a
        warning — a broken hook must not wedge tool execution.

        ``readonly_mode`` is the batch's posture, forwarded to
        :meth:`_apply_updated_input` so a rewrite is re-decided under the
        same value phase 1 used. ``None`` (direct callers / tests) reads it
        live, which is what this method did before the argument existed.
        """
        pre_rules = [
            r for r in self._plugin_hook_rules
            if r.event == "PreToolUse" and r.is_supported
        ]
        if not pre_rules:
            return

        from ..plugins.hooks import ClaudeHookPayloadAdapter, PluginHookDispatcher
        from ..plugins.hooks._profile import LEGACY_CONTRACT_ID

        adapter = ClaudeHookPayloadAdapter()
        dispatcher = PluginHookDispatcher(cwd=self._working_directory)
        profile_rules = [r for r in pre_rules if r.contract != LEGACY_CONTRACT_ID]

        for plan in plans:
            # The reference is explicit: **permission denials fire PreToolUse**.
            # Skipping them is a sound optimization only while a hook can merely
            # *tighten* a verdict — it stops being sound the moment the contract
            # says the hook must observe the call. An audit, notifier or metrics
            # hook registered here never saw denied calls, which is precisely the
            # population such a hook exists for, and nothing told its author.
            #
            # `agentao-v1` keeps the skip (frozen); a profile rule sees the call.
            already_denied = plan.decision not in (
                ToolCallDecision.ALLOW, ToolCallDecision.ASK,
            )
            rules_for_plan = profile_rules if already_denied else pre_rules
            if not rules_for_plan:
                continue
            payload = adapter.build_pre_tool_use(
                tool_name=plan.function_name,
                tool_input=plan.function_args,
                session_id=self._session_id,
                # Required in the input matrix and already in hand: the
                # normalized call id the runner assigned this plan, and the
                # session working directory the Post* events already send —
                # without it this one event reported ``Path.cwd()`` instead.
                tool_use_id=plan.tool_call_id or "",
                cwd=self._working_directory,
            )
            try:
                hook_result = dispatcher.dispatch_pre_tool_use_decision(
                    payload=payload, rules=rules_for_plan,
                )
            except Exception as exc:  # pragma: no cover - defensive
                self._logger.warning("PreToolUse hook dispatch error: %s", exc)
                continue
            if hook_result.matched_rule_count == 0:
                continue

            # Replay parity with the other hook sites.
            try:
                self._transport.emit(AgentEvent(EventType.PLUGIN_HOOK_FIRED, {
                    "hook_name": "PreToolUse",
                    "tool": plan.function_name,
                    "outcome": hook_result.decision or "allow",
                    "matched_rule_count": hook_result.matched_rule_count,
                    "added_context_count": len(hook_result.additional_contexts),
                }))
            except Exception:
                pass

            if hook_result.additional_contexts:
                # Deviation 6: parsed and *logged* was not a route. It rides
                # beside this call's result, where the model actually sees it.
                plan.hook_tool_contexts.extend(hook_result.additional_contexts)

            if hook_result.stop_reason is not None:
                # `continue: false` ends the **turn**, not the call. Recording it
                # as a `deny` because a verdict field is already there is the
                # mis-implementation §5.2.2 names.
                self.last_hook_stop = hook_result.stop_reason or "Hook stopped the turn"

            if hook_result.updated_tool_input is not None and not already_denied:
                self._apply_updated_input(
                    plan, hook_result.updated_tool_input,
                    readonly_mode=readonly_mode,
                )

            reason = pre_tool_hook_reason(hook_result.reason)
            if hook_result.decision == "deny":
                plan.decision = ToolCallDecision.DENY
                plan.permission_detail = _synth(PermissionDecision.DENY, reason)
            elif hook_result.decision == "ask" and plan.decision == ToolCallDecision.ALLOW:
                plan.decision = ToolCallDecision.ASK
                plan.permission_detail = _synth(PermissionDecision.ASK, reason)
            # ``allow`` / no decision → no-op (must not downgrade an existing
            # engine deny/ask or a tool's own requires_confirmation ask).

    def _apply_updated_input(
        self, plan, updated: dict, *, readonly_mode: Optional[bool] = None,
    ) -> None:
        """Replace the call's arguments and **re-decide** on what will run.

        `updatedInput` "replaces the entire input object", so the verdict
        computed in phase 1 describes arguments that no longer exist. Storing the
        rewrite and executing it would hand the executor a command the permission
        engine never saw — carrying an ALLOW computed on the original, with the
        hardline shell scanner running *inside* that verdict rather than
        downstream of it.

        The re-decided verdict and whatever the hook asks for combine by taking
        the **stricter**: a hook `allow` cannot lift a re-computed DENY, and a
        pre-existing DENY stays DENY. Phase 2's confirmation then shows the
        modified input, because it reads these same arguments — which is the
        reference's own pairing of `updatedInput` with `"ask"`.

        Not validated first: agentao has no pre-execution schema check (§1's
        stated non-promise), so a rewrite the tool cannot accept fails inside the
        tool instead of being refused here.

        The re-decide runs **before** the arguments are swapped in, and the swap
        happens only if it returned. Mutating first and bailing out of the
        ``except`` left the plan holding rewritten arguments under a verdict
        computed on the originals — the precise state the paragraph above says
        must never exist.

        And when the re-decide cannot be completed, the call is **denied**. The
        two tempting alternatives are both states the plan rules out: keeping
        the rewrite runs arguments no verdict covers, and dropping it runs the
        original — the input the hook was replacing, which is the unsafe branch
        §9's G8 entry rejects by name and §12 pins as the security property
        (*"the original arguments never reach the executor"*). A hook that
        rewrites a command has already said the original must not run; an
        engine failure is not permission to run it anyway.

        ``readonly_mode`` is the value ``execute()`` read **once** for the
        batch. Re-reading it here would let a mode switch between phase 1 and
        phase 1.5 judge the rewrite under a posture the recorded reason and
        phase 3's ``[Readonly mode]`` label do not describe. ``None`` reads it
        live, for direct callers that never ran phase 1.
        """
        if readonly_mode is None:
            readonly_mode = self.readonly_active()
        previous = plan.decision
        candidate = dict(updated)
        # The re-decision reads the *same* spec the first decision was
        # frozen against, never a second read of the provider. Omitting it entirely was the
        # real defect: with ``shell_spec=None`` the floor skips ``_spec_refusal`` (an
        # ``Exhausted`` provider stops denying) and falls back to the POSIX regex patterns,
        # so a hook's rewrite is judged in a grammar it may not be written in — precisely the
        # re-judgement this method exists to perform.
        shell_spec = (
            plan.decided.spec if plan.decided is not None else _shell_spec_of(plan.tool)
        )
        try:
            # Same order as the first decision: the record is built from the *candidate*
            # arguments and carries the floor's verdict, and the permission layer reads it.
            # Deciding first and rebuilding after would judge the rewrite against a record
            # computed for the original, which is the state this method exists to prevent.
            candidate_record = _decided_call(plan.tool, shell_spec, candidate)
            new_decision, new_detail = self._planner._decide(
                plan.tool, plan.function_name, candidate, readonly_mode, shell_spec,
                candidate_record,
            )
        except Exception as exc:  # pragma: no cover - defensive
            self._logger.warning(
                "re-decide after updatedInput failed, denying the call: %s", exc,
            )
            plan.decision = ToolCallDecision.DENY
            # ``_synth``, not a bare string: ``permission_detail`` is read as a
            # ``PermissionDecisionDetail`` (``.matched_rule`` / ``.reason``) by
            # the host-event emitter, so a string here trades a security hole
            # for an ``AttributeError`` on the path that reports the denial.
            plan.permission_detail = _synth(
                PermissionDecision.DENY,
                "PreToolUse hook rewrote this call's input and the permission "
                f"re-decision could not be computed ({exc}); denied rather than "
                "running either the rewrite or the original",
            )
            if plan.decided is not None:
                # The record still holds the *original* body, and the shell tool launches
                # what the record says. Marking it denied is what makes the paragraph above
                # true on this path too: neither the rewrite nor the original runs.
                plan.decided = replace(plan.decided, verdict=Deny(plan.permission_detail.reason))
            return
        plan.function_args = candidate
        if _STRICTNESS.get(new_decision, 0) > _STRICTNESS.get(previous, 0):
            plan.decision = new_decision
            plan.permission_detail = new_detail
        if plan.decided is not None:
            # Replaced whole, never edited field by field, and it is the record the
            # re-decision was actually made against — swapping the arguments and leaving the
            # record alone would launch the body the hook was replacing, under the verdict
            # computed for the one that replaced it. The spec is carried over rather than
            # re-read: one spec object governs the decision and the launch.
            plan.decided = (
                _denied(candidate_record)
                if plan.decision is ToolCallDecision.DENY
                else candidate_record
            )

    # ------------------------------------------------------------------
    # Public-event helpers
    # ------------------------------------------------------------------

    def _should_emit_permission_events(self) -> bool:
        """Skip per-plan emit when no host is listening.

        Avoids ``new_decision_id`` + ``ActivePermissions`` + Pydantic
        construction per tool call in the (common) no-listener case.
        ``_has_listeners`` covers async subscribers and sync observers;
        the older ``_has_subscribers`` is a fallback for custom event
        streams that haven't been updated. Falls back to ``True`` when
        the emitter has no stream handle to introspect — better to
        emit than silently drop.
        """
        emitter = self._host_permission_emitter
        if emitter is None:
            return False
        stream = getattr(emitter, "_stream", None)
        check = (
            getattr(stream, "_has_listeners", None)
            or getattr(stream, "_has_subscribers", None)
        )
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
            self._host_permission_emitter.emit(
                tool_name=plan.function_name,
                tool_call_id=plan.tool_call_id,
                decision_id=new_decision_id(),
                outcome=outcome,
                matched_rule=matched_rule,
                reason=reason,
            )
        except Exception:
            pass
