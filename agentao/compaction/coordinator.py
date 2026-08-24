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
    CompactionKind,
    CompactionOutcome,
    CompactionReason,
    CompactionTrigger,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..agent import Agentao


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
        self.dispatch_pre_compact(request)

        t0 = time.monotonic()
        pre_msgs = len(agent.messages)
        if messages_with_system is None:
            messages_with_system = self._with_system(system_prompt)
        pre_est_tokens = (
            cm.estimate_tokens(messages_with_system) if measure_system_tokens else None
        )

        outcome = self._transform(request, keep_tail=keep_tail)

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

        if outcome.status == "success":
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

        if request.kind == "full" and cm.compaction_circuit_open:
            # Announcing this would fork a PreCompact hook subprocess per
            # iteration for something that never happens, and emit a
            # CONTEXT_COMPRESSED reporting pre == post. Stand down and let
            # the API-overflow recovery path own it from here.
            #
            # Still log it: standing down before the transform skips the
            # breaker warning it used to emit, and that line is the only
            # signal that auto-compaction is dead for the rest of the session
            # (the counter has no reset path).
            self._warn(
                "Compact circuit breaker open "
                f"({cm.circuit_breaker_failures} consecutive failures) — "
                "skipping compaction; context stays over threshold until the "
                "API-overflow path recovers it"
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
        self, request: CompactionRequest, *, keep_tail: int,
    ) -> CompactionOutcome:
        agent = self._agent
        cm = agent.context_manager

        if request.kind == "full":
            # Private on purpose, and shared with the legacy
            # ``compress_messages`` wrapper: it is the only layer that can
            # produce an authoritative ``status`` for this kind, and it owns
            # the failure counter, which the coordinator must never touch.
            return cm._run_compaction(
                agent.messages,
                is_auto=(request.trigger == "auto"),
                reason=request.reason,
                decide=None,
            )

        if request.kind == "microcompact":
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

    # ------------------------------------------------------------------
    # Host dispatch
    # ------------------------------------------------------------------

    def dispatch_pre_compact(self, request: CompactionRequest) -> None:
        """Fire matching ``PreCompact`` command hooks (side-effect only).

        One implementation for all five entry points. There used to be two —
        the chat loop's and the CLI's — which is how manual ``/compact`` came
        to emit a replay event saying ``manual`` beside a hook payload saying
        ``auto`` for the same compaction.
        """
        agent = self._agent
        rules = getattr(agent, "_plugin_hook_rules", None)
        if not rules:
            return
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
                return
            dispatcher.dispatch_pre_compact(payload=payload, rules=matched)
            agent.transport.emit(AgentEvent(EventType.PLUGIN_HOOK_FIRED, {
                "hook_name": "PreCompact",
                "outcome": "allow",
                "compaction_type": request.kind,
                "trigger": request.trigger,
                "matched_rule_count": len(matched),
            }))
        except Exception:
            pass

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
        ``skipped`` never reaches here. ``CONTEXT_COMPRESSED`` fires only on
        success — that is the change from today, where the overflow path
        emitted it unconditionally and therefore reported compactions that
        returned history unchanged.
        """
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
