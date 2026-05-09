"""Plugin-hook dispatch mix-in for ``ChatLoopRunner``.

UserPromptSubmit (entry), Stop (turn boundary), and PreCompact (about
to mutate history) all live here because they share the same payload-
adapter / dispatcher / select_matching_rules pattern and the same
``PLUGIN_HOOK_FIRED`` event shape — keeping them adjacent lets a
reviewer audit the hook surface without flipping between files.

Mixed into :class:`ChatLoopRunner`; reads ``self._agent`` and
``self._stop_reentries`` as if it were a method. The mixin pattern
mirrors ``agentao.acp_client.manager.turns.TurnsMixin`` already in the
codebase.
"""

from __future__ import annotations

from typing import Literal

from ...plugins.models import StopHookResult
from ...transport import AgentEvent, EventType
from ._outcomes import _HookOutcome


class _HookDispatchMixin:
    """Mix-in providing the four plugin-hook dispatchers."""

    def _dispatch_user_prompt_submit(self, user_message: str) -> _HookOutcome:
        agent = self._agent
        if not agent._plugin_hook_rules:
            return _HookOutcome(early_return=None, user_message=user_message)

        from ...plugins.hooks import (
            ClaudeHookPayloadAdapter,
            PluginHookDispatcher,
        )

        cwd = agent.working_directory
        adapter = ClaudeHookPayloadAdapter()
        payload = adapter.build_user_prompt_submit(
            user_message=user_message, session_id=agent._session_id, cwd=cwd,
        )
        dispatcher = PluginHookDispatcher(cwd=cwd)
        ups_result = dispatcher.dispatch_user_prompt_submit(
            payload=payload, rules=agent._plugin_hook_rules,
        )
        # Replay event — plugin hook dispatch outcome. We only surface
        # verdict + counts here; the hook output itself is neither known
        # nor stored at this layer.
        outcome_label = "allow"
        if ups_result.blocking_error:
            outcome_label = "block"
        elif ups_result.prevent_continuation:
            outcome_label = "stop"
        elif ups_result.additional_contexts:
            outcome_label = "modify"
        try:
            agent.transport.emit(AgentEvent(EventType.PLUGIN_HOOK_FIRED, {
                "hook_name": "UserPromptSubmit",
                "rule_count": len(agent._plugin_hook_rules),
                "outcome": outcome_label,
                "blocking_error": ups_result.blocking_error or None,
                "stop_reason": ups_result.stop_reason or None,
                "added_context_count": len(ups_result.additional_contexts or []),
            }))
        except Exception:
            pass

        if ups_result.blocking_error:
            return _HookOutcome(
                early_return=f"[Blocked by hook] {ups_result.blocking_error}",
                user_message=user_message,
            )
        if ups_result.prevent_continuation:
            return _HookOutcome(
                early_return=(
                    f"[Hook stopped] "
                    f"{ups_result.stop_reason or 'Hook prevented continuation'}"
                ),
                user_message=user_message,
            )
        if ups_result.additional_contexts:
            extra = "\n".join(
                f"<user-prompt-submit-hook>\n{ctx}\n</user-prompt-submit-hook>"
                for ctx in ups_result.additional_contexts
            )
            user_message = extra + "\n" + user_message
        return _HookOutcome(early_return=None, user_message=user_message)

    def _dispatch_stop(
        self,
        *,
        turn_end_reason: Literal[
            "final_response", "max_iterations", "doom_loop",
        ],
        last_assistant_message: str,
    ) -> StopHookResult:
        """Run Stop hooks; return the aggregated control signal.

        Does NOT emit ``PLUGIN_HOOK_FIRED`` — the outcome label depends
        on caller-side branching (cap check) the helper does not see.
        See ``_emit_stop_hook_fired``.
        """
        agent = self._agent
        if not agent._plugin_hook_rules:
            return StopHookResult()
        from ...plugins.hooks import (
            ClaudeHookPayloadAdapter,
            PluginHookDispatcher,
        )
        cwd = agent.working_directory
        payload = ClaudeHookPayloadAdapter().build_stop(
            session_id=agent._session_id,
            cwd=cwd,
            last_assistant_message=last_assistant_message or "",
            stop_hook_active=(self._stop_reentries > 0),
            turn_end_reason=turn_end_reason,
            permission_mode=agent.active_permissions().mode,
        )
        dispatcher = PluginHookDispatcher(cwd=cwd)
        # Pre-filter so the early-return path skips the subprocess fork.
        matched = dispatcher.select_matching_rules(
            "Stop", payload, agent._plugin_hook_rules,
        )
        if not matched:
            return StopHookResult()
        return dispatcher.dispatch_stop(payload=payload, rules=matched)

    def _emit_stop_hook_fired(
        self,
        *,
        outcome: Literal[
            "allow", "block", "continue",
            "continue_at_max_iter", "reentry_capped",
        ],
        turn_end_reason: Literal[
            "final_response", "max_iterations", "doom_loop",
        ],
        stop_result: StopHookResult,
    ) -> None:
        """Emit ``PLUGIN_HOOK_FIRED`` for a Stop dispatch.

        Gated on ``matched_rule_count > 0`` so a turn with no matching
        Stop hooks does not emit ``outcome="allow"``. ``turn_end_reason``
        disambiguates ``outcome="continue"`` across the natural-turn
        and doom-loop emit sites.
        """
        if stop_result.matched_rule_count == 0:
            return
        agent = self._agent
        try:
            agent.transport.emit(AgentEvent(EventType.PLUGIN_HOOK_FIRED, {
                "hook_name": "Stop",
                "outcome": outcome,
                "at_max_iter": turn_end_reason == "max_iterations",
                "turn_end_reason": turn_end_reason,
                "matched_rule_count": stop_result.matched_rule_count,
                "added_context_count": len(stop_result.additional_contexts),
                "suppress_output": stop_result.suppress_output,
            }))
        except Exception:
            pass

    def _dispatch_pre_compact(
        self,
        *,
        compaction_type: str,
        reason: str,
    ) -> None:
        """PreCompact dispatch — fires before the about-to-mutate
        compaction site; side-effect only with the same no-emit gate as
        ``_dispatch_stop``.
        """
        agent = self._agent
        if not agent._plugin_hook_rules:
            return
        from ...plugins.hooks import (
            ClaudeHookPayloadAdapter,
            PluginHookDispatcher,
        )
        cwd = agent.working_directory
        payload = ClaudeHookPayloadAdapter().build_pre_compact(
            session_id=agent._session_id,
            cwd=cwd,
            compaction_type=compaction_type,
            reason=reason,
            permission_mode=agent.active_permissions().mode,
        )
        dispatcher = PluginHookDispatcher(cwd=cwd)
        matched = dispatcher.select_matching_rules(
            "PreCompact", payload, agent._plugin_hook_rules,
        )
        if not matched:
            return
        dispatcher.dispatch_pre_compact(payload=payload, rules=matched)
        try:
            agent.transport.emit(AgentEvent(EventType.PLUGIN_HOOK_FIRED, {
                "hook_name": "PreCompact",
                "outcome": "allow",
                "compaction_type": compaction_type,
                "trigger": "auto",
                "matched_rule_count": len(matched),
            }))
        except Exception:
            pass
