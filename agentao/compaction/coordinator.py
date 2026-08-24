"""``CompactionCoordinator`` — the one place a compaction is decided.

Compaction used to be orchestrated in four unrelated places: two threshold
tiers in the chat loop, a two-rung API-overflow ladder in the runner, and the
manual ``/compact`` command in the CLI. They disagreed about what a trigger is
called, about whether a failure counts, and — worst — about whether
"compacted" means history actually changed: the overflow path announced a
successful compaction even when the circuit breaker had made it a no-op,
emitting ``CONTEXT_COMPRESSED`` with ``pre_msgs == post_msgs``.

This class is the seam that makes them agree. It owns *whether to run, whose
summary to take, how to recover, and what to emit*. It does **not** own how
history is rewritten — every content transform stays behind a
``ContextManager`` method, and the dependency points one way only:
``ContextManager`` neither imports nor holds a coordinator.

The division in one line: policy and observability here, content transforms
there, and one :class:`CompactionOutcome` out of every entry point.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .types import (
    CompactionDecision,
    CompactionDecisionContext,
    CompactionKind,
    CompactionOutcome,
    CompactionReason,
    CompactionTrigger,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..agent import Agentao


#: Reasons allowed through an open breaker as a half-open probe. Both are
#: outside what the breaker describes: it exists to stop the *threshold* tier
#: re-entering every iteration. Manual compaction is user-driven and does not
#: loop; an API overflow has already been rejected by the provider, so
#: blocking it leaves the recovery ladder with nothing but ``messages[-2:]``.
_PROBE_REASONS = frozenset({"manual_cli", "api_overflow"})

#: Reasons a cancellation is remembered for, until the start of the next turn.
#: Exactly the two the loop **re-checks on every iteration** — without a latch,
#: honouring a cancel would mean asking again next iteration, which is the
#: per-iteration hook fork the stand-down gates exist to prevent.
#:
#: ``manual_cli`` is deliberately absent: it is user-driven, does not loop, and
#: **runs outside a turn**, so a turn-reset latch would mean "cancel manually
#: once and every immediate retry stays suppressed until you first run an
#: ordinary turn". Neither overflow reason is here either — a cancelled
#: overflow returns the context-length error and ends the turn on the spot, so
#: there is no re-dispatch to suppress.
_LATCHED_REASONS = frozenset({"microcompact_threshold", "compression_threshold"})


@dataclass(frozen=True)
class CompactionRequest:
    """Which compaction is being asked for.

    All three kinds share this one request type; the difference lives
    entirely in what the transform produces, so the coordinator has a single
    skeleton rather than three.
    """

    trigger: CompactionTrigger
    kind: CompactionKind
    reason: CompactionReason


@dataclass(frozen=True)
class CompactionRun:
    """The outcome plus the post-state the caller has to splice back.

    The chat loop threads ``(messages_with_system, system_prompt)`` through
    every iteration, and a full compaction rewrites the history the prompt is
    built against — so both come back from here rather than being recomputed
    by each caller, which is how the five entry points drifted apart in the
    first place.
    """

    outcome: CompactionOutcome
    system_prompt: str
    messages_with_system: List[Dict[str, Any]]
    # The system-**inclusive** pair, populated only when the caller asked
    # for it via ``measure_system_tokens``. Handed back so a caller that
    # wants to report the numbers does not measure the same history a third
    # and fourth time — this is the unit ``CONTEXT_COMPRESSED`` uses, not
    # the history-only one on the outcome.
    pre_est_tokens: Optional[int] = None
    post_est_tokens: Optional[int] = None


class CompactionCoordinator:
    """Policy, host dispatch, and observability for every compaction."""

    def __init__(self, agent: "Agentao") -> None:
        self._agent = agent
        # ``(kind, reason)`` pairs cancelled during this turn. Owned here and
        # not on ``ContextManager``: unlike the breaker's counter, this has no
        # existing public surface to serve, and its lifetime is one turn.
        self._cancel_latch: set = set()

    # ------------------------------------------------------------------
    # The one entry point
    # ------------------------------------------------------------------

    def run(
        self,
        request: CompactionRequest,
        *,
        system_prompt: str,
        messages_with_system: Optional[List[Dict[str, Any]]] = None,
        measure_system_tokens: bool = False,
        keep_tail: int = 2,
    ) -> CompactionRun:
        """Gate, dispatch, transform, emit — in that order.

        ``measure_system_tokens`` decides whether the legacy
        ``CONTEXT_COMPRESSED`` event carries its system-**inclusive** token
        pair. It is false on both API-overflow rungs, matching today: those
        two never passed tokens, and filling them in would mean two new
        full-history estimates on precisely the path where the context has
        already blown up and the request has just been rejected.

        ``messages_with_system`` is the caller's already-assembled list. It
        is handed straight back whenever history did not change, so a gate
        that fires on every loop iteration does not rebuild it every time.

        ``keep_tail`` applies to ``minimal_history`` only.
        """
        agent = self._agent
        cm = agent.context_manager

        gate = self._gate(request)
        if gate is not None:
            # ``skipped`` is silent by construction: three of the four
            # skipped cases re-trigger on *every* loop iteration, so emitting
            # an event each time is a fresh event storm — the exact thing the
            # stand-down comments this class absorbed were written to stop.
            return CompactionRun(
                outcome=gate,
                system_prompt=system_prompt,
                messages_with_system=(
                    messages_with_system
                    if messages_with_system is not None
                    else self._with_system(system_prompt)
                ),
            )

        # Logged after the gate, not before it: the whole point of the gate
        # is that these attempts do not happen, and the threshold is
        # re-checked every iteration, so announcing first would print a line
        # per iteration for a compaction that never runs.
        self._info(
            f"Compaction triggered: kind={request.kind} reason={request.reason}"
        )
        # Command hooks run **first**, and a cancel from either layer is a
        # cancel. Dispatched here rather than inside the decide step so the
        # hook still fires exactly where it always has: before anything is
        # touched, and once per attempt.
        hook_result = self.dispatch_pre_compact(request)

        t0 = time.monotonic()
        pre_msgs = len(agent.messages)
        if messages_with_system is None:
            messages_with_system = self._with_system(system_prompt)
        pre_est_tokens = (
            cm.estimate_tokens(messages_with_system) if measure_system_tokens else None
        )

        outcome = self._transform(
            request,
            keep_tail=keep_tail,
            decide=self._compose_decide(request, hook_result),
        )

        if outcome.status == "cancelled" and request.reason in _LATCHED_REASONS:
            self._cancel_latch.add((request.kind, request.reason))

        if request.kind == "full" and request.trigger == "auto":
            # The summarization LLM call goes straight to ``llm_client`` and
            # never passes the runner's finish-reason detector, so fold its
            # observation in here. This is the call whose output permanently
            # rewrites history, so a turn that compacted against an
            # unconfirmed summary has to say so. Manual ``/compact`` is
            # exempt because it runs outside a turn — there is no turn flag
            # to set, and setting one would leak into the next turn.
            if cm.last_summary_finish_reason_missing:
                agent._turn_finish_reason_missing = True

        if outcome.status == "success":
            agent.messages = outcome.messages
            if request.kind != "microcompact" or cm.last_microcompact_mutated:
                # Only a microcompact pass that actually shortened something
                # invalidates the already-sent prefix. Dropping the anchor on
                # a no-op pass forces a full re-encode of the entire history
                # on every iteration spent in the microcompact band —
                # precisely when it is most expensive.
                cm.invalidate_token_anchor()
            if request.kind == "full":
                system_prompt = agent._build_system_prompt()
            messages_with_system = self._with_system(system_prompt)

        post_est_tokens = (
            cm.estimate_tokens(messages_with_system)
            if measure_system_tokens and outcome.status == "success"
            else None
        )

        self._emit(
            request,
            outcome,
            pre_msgs=pre_msgs,
            post_msgs=len(agent.messages),
            pre_est_tokens=pre_est_tokens,
            post_est_tokens=post_est_tokens,
            duration_ms=round((time.monotonic() - t0) * 1000),
        )

        if request.kind == "full" and outcome.status == "success":
            # ``_last_session_summary_id`` is created lazily at the start of a
            # turn (``runtime/turn.py``); a manual ``/compact`` may run before
            # that — e.g. right after ``/sessions resume`` — so fall back.
            agent._last_session_summary_id = agent._emit_session_summary_if_new(
                getattr(agent, "_last_session_summary_id", None),
            )

        self._info(
            f"Compaction {outcome.status}: kind={request.kind} "
            f"reason={request.reason} messages {pre_msgs} -> {len(agent.messages)}"
            + (f" ({outcome.detail})" if outcome.detail else "")
        )

        return CompactionRun(
            outcome=outcome,
            system_prompt=system_prompt,
            messages_with_system=messages_with_system,
            pre_est_tokens=pre_est_tokens,
            post_est_tokens=post_est_tokens,
        )

    # ------------------------------------------------------------------
    # Policy
    # ------------------------------------------------------------------

    def _gate(self, request: CompactionRequest) -> Optional[CompactionOutcome]:
        """Return a ``skipped`` outcome when this attempt must not run.

        Nothing here counts against the circuit breaker: ``skipped`` means
        nothing was attempted, which is exactly what the two short-circuits
        it absorbed already did.
        """
        agent = self._agent
        cm = agent.context_manager

        if (request.kind, request.reason) in self._cancel_latch:
            # Silent: no hook dispatch, no controller call, no event. This
            # hits on every iteration for the rest of the turn, which is the
            # entire reason the latch exists.
            return self._skipped(request, "suppressed_by_latch")

        if request.kind == "full" and cm.compaction_circuit_open:
            if request.reason in _PROBE_REASONS:
                # Half-open. The breaker describes *threshold* behaviour —
                # "stop re-entering every iteration" — and neither of these
                # is that. Manual compaction is user-driven and does not
                # loop; an API overflow has already happened, so blocking it
                # leaves the ladder with nothing to fall back on but
                # ``messages[-2:]``. One attempt is allowed; a success
                # closes the breaker (``_run_compaction`` resets on commit),
                # a failure leaves it exactly as it was.
                self._info(
                    f"Compact circuit breaker open ({cm.circuit_breaker_failures} "
                    f"consecutive failures) — allowing {request.reason} as a probe"
                )
            else:
                # Announcing this would fork a PreCompact hook subprocess per
                # iteration for something that never happens, and emit a
                # CONTEXT_COMPRESSED reporting pre == post. Stand down and let
                # the probes above own recovery from here.
                #
                # Still log it: standing down before the transform skips the
                # breaker warning it used to emit, and that line is the only
                # signal that automatic compaction is paused.
                self._warn(
                    "Compact circuit breaker open "
                    f"({cm.circuit_breaker_failures} consecutive failures) — "
                    "pausing automatic compaction; /compact or an API overflow "
                    "runs as a probe and a success resets it"
                )
                return self._skipped(request, "circuit_open")

        if request.kind == "microcompact" and not cm.microcompact_would_mutate(
            agent.messages
        ):
            # Being *in* the band says nothing about there being anything
            # left to shorten: once every old tool result is at or under the
            # limit, every further iteration in the band is a no-op.
            return self._skipped(request, "no_microcompact_targets")

        return None

    # ------------------------------------------------------------------
    # Transforms — all of them behind a ContextManager method
    # ------------------------------------------------------------------

    def _transform(
        self, request: CompactionRequest, *, keep_tail: int, decide=None,
    ) -> CompactionOutcome:
        agent = self._agent
        cm = agent.context_manager

        if request.kind == "full":
            # Private on purpose, and shared with the legacy
            # ``compress_messages`` wrapper: it is the only layer that can
            # produce an authoritative ``status`` for this kind, and it owns
            # the failure counter, which the coordinator must never touch.
            # It runs ``decide`` itself, after prepare and before summarize —
            # the only point where a host summary can replace the LLM call.
            return cm._run_compaction(
                agent.messages,
                is_auto=(request.trigger == "auto"),
                reason=request.reason,
                decide=decide,
            )

        # The other two kinds call no summarizer, write no SQLite and never
        # touch the breaker counter, so they do not go through
        # ``_run_compaction`` — but history is still rewritten behind a
        # ``ContextManager`` method, never here.
        if request.kind == "microcompact":
            prep = cm.prepare_microcompact(agent.messages)
            cancelled = self._ask(
                request,
                decide,
                messages_to_keep=len(agent.messages),
                tool_results_to_clip=prep.tool_results_to_clip,
            )
            if cancelled is not None:
                return cancelled
            return CompactionOutcome(
                status="success",
                trigger=request.trigger,
                kind=request.kind,
                reason=request.reason,
                messages=cm.microcompact_messages(agent.messages),
                # Both null deliberately. Microcompaction runs on every
                # iteration inside its band; two extra full-history estimates
                # there is the cost this design refuses to add. Read the old
                # event for microcompaction tokens — in its own unit.
                pre_tokens=None,
                post_tokens=None,
            )

        prep = cm.prepare_minimal_history(agent.messages, keep_tail=keep_tail)
        cancelled = self._ask(
            request, decide, messages_to_keep=prep.keep_tail,
        )
        if cancelled is not None:
            return cancelled
        return CompactionOutcome(
            status="success",
            trigger=request.trigger,
            kind=request.kind,
            reason=request.reason,
            messages=cm.apply_minimal_history(agent.messages, keep_tail=keep_tail),
            # This path makes no token estimate at all, and it is reached
            # only after the API has rejected the request twice.
            pre_tokens=None,
            post_tokens=None,
        )

    def _ask(
        self,
        request: CompactionRequest,
        decide,
        *,
        messages_to_keep: int,
        tool_results_to_clip: Optional[int] = None,
    ) -> Optional[CompactionOutcome]:
        """Run the decision step for a non-``full`` kind.

        Returns a ``cancelled`` outcome, or ``None`` to proceed.
        ``provide_summary`` is not legal here — ``can_provide_summary`` is
        ``False`` — and an offer of one is downgraded to ``allow`` inside
        ``_consult_controller``, so nothing is dropped silently.
        """
        if decide is None:
            return None
        ctx = CompactionDecisionContext(
            trigger=request.trigger,
            kind=request.kind,
            reason=request.reason,
            pre_tokens=None,
            messages_to_summarize=0,
            messages_to_keep=messages_to_keep,
            recently_read_files=(),
            summary_input_budget=None,
            max_summary_tokens=None,
            can_provide_summary=False,
            tool_results_to_clip=tool_results_to_clip,
        )
        decision = decide(ctx)
        if decision.action != "cancel":
            return None
        return CompactionOutcome(
            status="cancelled",
            trigger=request.trigger,
            kind=request.kind,
            reason=request.reason,
            messages=self._agent.messages,
            pre_tokens=None,
            post_tokens=None,
            detail=decision.reason,
        )

    # ------------------------------------------------------------------
    # Host dispatch
    # ------------------------------------------------------------------

    def dispatch_pre_compact(self, request: CompactionRequest) -> Optional[str]:
        """Fire matching ``PreCompact`` command hooks; return a cancel reason.

        One implementation for all five entry points. There used to be two —
        the chat loop's and the CLI's — which is how manual ``/compact`` came
        to emit a replay event saying ``manual`` beside a hook payload saying
        ``auto`` for the same compaction.

        Returns the reason string if any hook cancelled (``""`` when it gave
        none), or ``None`` for allow. Everything that is not an explicit
        ``{"hookSpecificOutput": {"compactionDecision": "cancel"}}`` on stdout
        means allow, **including a raised exception here**: this whole method
        is wrapped, because two of the five entry points are the API-overflow
        recovery ladder and a control-plane error must never be able to end
        the turn it exists to save.
        """
        agent = self._agent
        rules = getattr(agent, "_plugin_hook_rules", None)
        if not rules:
            return None
        try:
            from ..plugins.hooks import ClaudeHookPayloadAdapter, PluginHookDispatcher
            from ..transport import AgentEvent, EventType

            cwd = agent.working_directory
            payload = ClaudeHookPayloadAdapter().build_pre_compact(
                session_id=agent._session_id,
                cwd=cwd,
                trigger=request.trigger,
                compaction_type=request.kind,
                reason=request.reason,
                permission_mode=agent.active_permissions().mode,
            )
            dispatcher = PluginHookDispatcher(cwd=cwd)
            matched = dispatcher.select_matching_rules("PreCompact", payload, rules)
            if not matched:
                return None
            result = dispatcher.dispatch_pre_compact_decision(
                payload=payload, rules=matched,
            )
            cancelled = result.decision == "cancel"
            agent.transport.emit(AgentEvent(EventType.PLUGIN_HOOK_FIRED, {
                "hook_name": "PreCompact",
                "outcome": "cancel" if cancelled else "allow",
                "compaction_type": request.kind,
                "trigger": request.trigger,
                "matched_rule_count": len(matched),
            }))
            return (result.reason or "") if cancelled else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # The control plane
    # ------------------------------------------------------------------

    def _compose_decide(self, request: CompactionRequest, hook_reason: Optional[str]):
        """Merge the two control layers into one ``decide`` callable.

        Ordering: command hooks first (already run — ``hook_reason`` is their
        verdict), then, **only if they all allowed**, the host controller.
        Asking a trusted host to compute a summary that is about to be thrown
        away is pure waste.

        The merge rule in one line: a cancel in either layer is a cancel, and
        ``provide_summary`` can only come from the controller layer. Command
        hooks cannot provide summary text — they have no trust boundary, and
        summary text permanently rewrites history.
        """
        controller = getattr(self._agent, "compaction_controller", None)
        if hook_reason is None and controller is None:
            return None

        def _decide(ctx: CompactionDecisionContext) -> CompactionDecision:
            if hook_reason is not None:
                return CompactionDecision("cancel", reason=hook_reason or None)
            return self._consult_controller(controller, ctx)

        return _decide

    def _consult_controller(self, controller, ctx: CompactionDecisionContext):
        """Call the host controller, and survive anything it does.

        Every failure mode lands on ``allow``: a raise, an awaitable, an
        unknown ``action``, ``provide_summary`` with no text, or
        ``provide_summary`` where it has no legal meaning. Same direction as
        the hook layer's "any other value is allow", and for the same reason —
        **no control-plane error may be able to drive the context into the
        overflow ladder, let alone end the turn.**

        There is no timeout. It is a synchronous in-process callback; if it
        hangs, it hangs the turn, exactly like the host's other callbacks.
        """
        allow = CompactionDecision("allow")
        try:
            decision = controller(ctx)
        except Exception as exc:
            self._warn(f"compaction_controller raised ({exc!r}); treating as allow")
            return allow

        if not isinstance(decision, CompactionDecision):
            if hasattr(decision, "__await__"):
                # Closed, not just dropped: an un-awaited coroutine warns at
                # GC time, in whatever unrelated code happens to be running.
                try:
                    decision.close()
                except Exception:
                    pass
                self._warn(
                    "compaction_controller returned an awaitable; v1 does not "
                    "support an async controller — treating as allow"
                )
            else:
                self._warn(
                    f"compaction_controller returned {type(decision).__name__}, "
                    "not a CompactionDecision — treating as allow"
                )
            return allow

        if decision.action == "cancel":
            return decision
        if decision.action == "allow":
            return decision
        if decision.action == "provide_summary":
            if not ctx.can_provide_summary:
                self._warn(
                    f"compaction_controller offered a summary for kind={ctx.kind}, "
                    "where provide_summary has no meaning — treating as allow"
                )
                return allow
            if decision.summary is None:
                self._warn(
                    "compaction_controller returned provide_summary with no "
                    "summary — treating as allow"
                )
                return allow
            return decision

        self._warn(
            f"compaction_controller returned an unknown action {decision.action!r} "
            "— treating as allow"
        )
        return allow

    def reset_cancellation_latch(self) -> None:
        """Forget this turn's cancellations. Called at the start of each turn.

        The latch is what makes "not re-dispatched for the rest of the turn"
        a mechanism rather than a promise; this is the other half of it.
        """
        self._cancel_latch.clear()

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def _emit(
        self,
        request: CompactionRequest,
        outcome: CompactionOutcome,
        *,
        pre_msgs: int,
        post_msgs: int,
        pre_est_tokens: Optional[int],
        post_est_tokens: Optional[int],
        duration_ms: int,
    ) -> None:
        """Both events, from the one place that knows the outcome.

        ``COMPACTION_SETTLED`` fires for ``success | cancelled | failed``;
        ``skipped`` is silent. ``CONTEXT_COMPRESSED`` fires only on success —
        that is the change from today, where the overflow path emitted it
        unconditionally and therefore reported compactions that returned
        history unchanged.

        Three of the four skipped cases are decided by :meth:`_gate`, which
        returns before this method is reached. The fourth —
        ``history_too_short`` — is decided *inside* the transform, so it
        arrives here and has to be filtered explicitly. It re-triggers on
        every loop iteration exactly like the gated three (four huge messages
        over the threshold is enough), so letting it through was an event
        storm on the one status documented to stay silent.
        """
        if outcome.status == "skipped":
            return

        agent = self._agent
        from ..transport import AgentEvent, EventType

        try:
            agent.transport.emit(AgentEvent(EventType.COMPACTION_SETTLED, {
                "trigger": outcome.trigger,
                "kind": outcome.kind,
                "reason": outcome.reason,
                "status": outcome.status,
                "pre_msgs": pre_msgs,
                "post_msgs": post_msgs,
                # Named apart from the old event's pair on purpose: these two
                # **exclude** the system prompt and the old ones include it.
                # Two units, two names, so they cannot be wired to each other
                # by accident.
                "pre_tokens_history": outcome.pre_tokens,
                "post_tokens_history": outcome.post_tokens,
                "duration_ms": duration_ms,
                "detail": outcome.detail,
            }))
        except Exception:
            pass

        if outcome.status != "success":
            return

        agent._emit_context_compressed(
            compression_type=request.kind,
            reason=request.reason,
            pre_msgs=pre_msgs,
            post_msgs=post_msgs,
            pre_tokens=pre_est_tokens,
            post_tokens=post_est_tokens,
            duration_ms=duration_ms,
        )

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------

    def _with_system(self, system_prompt: str) -> List[Dict[str, Any]]:
        return [{"role": "system", "content": system_prompt}] + self._agent.messages

    def _skipped(
        self, request: CompactionRequest, detail: str,
    ) -> CompactionOutcome:
        return CompactionOutcome(
            status="skipped",
            trigger=request.trigger,
            kind=request.kind,
            reason=request.reason,
            messages=self._agent.messages,
            detail=detail,
        )

    def _warn(self, message: str) -> None:
        try:
            self._agent.llm.logger.warning(message)
        except Exception:
            pass

    def _info(self, message: str) -> None:
        try:
            self._agent.llm.logger.info(message)
        except Exception:
            pass
