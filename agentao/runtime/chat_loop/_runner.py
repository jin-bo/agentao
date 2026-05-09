"""``ChatLoopRunner`` — the inner chat loop body executed by ``Agentao.chat()``.

The loop reads and mutates the same agent state (``messages``,
``context_manager``, ``_last_session_summary_id``, ``_last_user_message``,
``_llm_call_seq`` / ``_llm_call_last_msg_count``) — no shadow copies. The
agent retains the per-turn lifecycle (cancellation token, replay
begin_turn/end_turn, ``_current_token`` bookkeeping) and delegates the
loop body here.

Plugin-hook dispatch and pre-LLM compaction live in sibling mix-in
modules so the file you're reading is mostly the long ``run()`` method
plus the three helpers it calls (``_inject_background_notifications``,
``_call_llm_with_overflow_recovery``, ``_emit_skill_and_memory_diffs``).
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional

from ...cancellation import AgentCancelledError, CancellationToken
from ...context_manager import is_context_too_long_error
from ...transport import AgentEvent, EventType
from ..sanitize import canonicalize_tool_arguments, sanitize_assistant_message
from ._compaction import _CompactionMixin
from ._hook_dispatch import _HookDispatchMixin
from ._outcomes import _HookOutcome
from ._serialize import _attach_reasoning, _serialize_tool_call

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from ...agent import Agentao


class ChatLoopRunner(_CompactionMixin, _HookDispatchMixin):
    """Run one ``chat()`` turn for an :class:`agentao.agent.Agentao`."""

    def __init__(
        self,
        agent: "Agentao",
        *,
        stop_reentry_cap: int = 3,
    ) -> None:
        self._agent = agent
        # Counter resets per chat() because ChatLoopRunner is
        # instantiated fresh by Agentao._chat_inner.
        self._stop_reentries: int = 0
        self._stop_reentry_cap: int = stop_reentry_cap

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        user_message: str,
        max_iterations: int,
        token: CancellationToken,
    ) -> str:
        """Execute the inner chat loop for one turn.

        Raises ``AgentCancelledError`` if cancellation was requested
        mid-turn; returns the assistant's final text otherwise (or a
        bracketed status string for hook short-circuits / max-iterations).
        """
        agent = self._agent
        now = datetime.now()
        system_reminder = (
            f"<system-reminder>\n"
            f"Current Date/Time: {now.strftime('%Y-%m-%d %H:%M:%S')} ({now.strftime('%A')})\n"
            f"</system-reminder>\n"
        )
        agent._last_user_message = user_message

        hook_outcome = self._dispatch_user_prompt_submit(user_message)
        if hook_outcome.early_return is not None:
            return hook_outcome.early_return
        user_message = hook_outcome.user_message

        agent.add_message("user", system_reminder + user_message)

        # Build system prompt (injects all memories)
        system_prompt = agent._build_system_prompt()

        messages_with_system = [
            {"role": "system", "content": system_prompt}
        ] + agent.messages

        tools = agent.tools.to_openai_format(plan_mode=agent._plan_mode)

        # Reset doom-loop counter for this chat() invocation
        agent.tool_runner.reset()

        # System prompt dirty-flag: only rebuild when skills or memories change
        current_active_skills = frozenset(agent.skill_manager.get_active_skills().keys())
        current_memory_version = agent.memory_manager.write_version

        iteration = 0
        assistant_message = None
        while True:
            if iteration >= max_iterations:
                pending = []
                if assistant_message and getattr(assistant_message, "tool_calls", None):
                    for tc in assistant_message.tool_calls:
                        pending.append({"name": tc.function.name, "args": tc.function.arguments})

                _handler = getattr(agent.transport, "on_max_iterations", None)
                result = _handler(max_iterations, pending) if callable(_handler) else {"action": "stop"}
                action = result.get("action", "stop")
                if action == "continue":
                    iteration = 0
                elif action == "new_instruction":
                    iteration = 0
                    new_msg = result.get("message", "")
                    if new_msg:
                        agent.messages.append({"role": "user", "content": new_msg})
                        messages_with_system = [
                            {"role": "system", "content": system_prompt}
                        ] + agent.messages
                else:  # "stop"
                    # Finalize max-iter inside the loop body so a Stop
                    # hook ``force_continue`` can re-enter ``while True``.
                    agent.llm.logger.warning(
                        f"Maximum tool call iterations ({max_iterations}) reached"
                    )
                    assistant_content_max = (
                        assistant_message.content if assistant_message else None
                    ) or "Maximum tool call iterations reached."
                    reasoning_content_max = (
                        getattr(assistant_message, "reasoning_content", None)
                        if assistant_message else None
                    )
                    final_msg_max: Dict[str, Any] = {
                        "role": "assistant",
                        "content": assistant_content_max,
                    }
                    _attach_reasoning(final_msg_max, reasoning_content_max)
                    if sanitize_assistant_message(final_msg_max):
                        agent.llm.logger.warning(
                            "Sanitised lone surrogates in max-iteration assistant message"
                        )

                    stop_result = self._dispatch_stop(
                        turn_end_reason="max_iterations",
                        last_assistant_message=assistant_content_max,
                    )

                    if stop_result.blocking_error:
                        blocked = f"[Blocked by Stop hook] {stop_result.blocking_error}"
                        final_msg_max["content"] = blocked
                        agent.messages.append(final_msg_max)
                        self._emit_stop_hook_fired(
                            outcome="block",
                            turn_end_reason="max_iterations",
                            stop_result=stop_result,
                        )
                        return blocked

                    if stop_result.force_continue:
                        # Cap check FIRST — without it a cap-hit would
                        # fall through to allow and silently mask the
                        # pathological hook.
                        if self._stop_reentries >= self._stop_reentry_cap:
                            agent.llm.logger.warning(
                                "Stop hook reentry cap (%d) hit at max-iterations; ending turn.",
                                self._stop_reentry_cap,
                            )
                            agent.messages.append(final_msg_max)
                            self._emit_stop_hook_fired(
                                outcome="reentry_capped",
                                turn_end_reason="max_iterations",
                                stop_result=stop_result,
                            )
                            return assistant_content_max
                        follow_up = (
                            stop_result.follow_up_message
                            or stop_result.stop_reason
                            or "Stop hook requested continuation"
                        )
                        self._stop_reentries += 1
                        agent.llm.logger.warning(
                            "Stop hook force_continue at max-iterations; "
                            "resetting iteration counter (outcome=continue_at_max_iter)."
                        )
                        agent.messages.append(final_msg_max)
                        agent.messages.append({
                            "role": "user",
                            "content": (
                                "<system-reminder>Stop hook injected this</system-reminder>\n"
                                f"{follow_up}"
                            ),
                        })
                        messages_with_system = [
                            {"role": "system", "content": system_prompt}
                        ] + agent.messages
                        iteration = 0
                        self._emit_stop_hook_fired(
                            outcome="continue_at_max_iter",
                            turn_end_reason="max_iterations",
                            stop_result=stop_result,
                        )
                        continue

                    # Max-iter does not echo additional_contexts —
                    # decorating the cap fallback string would be
                    # unhelpful UX.
                    agent.messages.append(final_msg_max)
                    self._emit_stop_hook_fired(
                        outcome="allow",
                        turn_end_reason="max_iterations",
                        stop_result=stop_result,
                    )
                    return assistant_content_max

            iteration += 1
            agent.llm.logger.info(f"LLM iteration {iteration}/{max_iterations}")

            messages_with_system, system_prompt = self._maybe_microcompact(
                messages_with_system, system_prompt,
            )
            messages_with_system, system_prompt = self._maybe_full_compress(
                messages_with_system, system_prompt,
            )
            messages_with_system = self._inject_background_notifications(
                messages_with_system, system_prompt,
            )

            # Check cancellation before each LLM call (e.g. Ctrl+C fired during
            # tool execution of the previous iteration).
            token.check()

            # Signal transport to reset display before each LLM call
            agent.transport.emit(AgentEvent(EventType.TURN_START, {}))

            llm_outcome = self._call_llm_with_overflow_recovery(
                messages_with_system, system_prompt, tools, token,
            )
            if llm_outcome.error_return is not None:
                return llm_outcome.error_return
            response = llm_outcome.response
            messages_with_system = llm_outcome.messages_with_system
            system_prompt = llm_outcome.system_prompt

            # Tier 1 token count: record real prompt_tokens from API response
            if getattr(response, "usage", None) and getattr(response.usage, "prompt_tokens", None):
                agent.context_manager.record_api_usage(response.usage.prompt_tokens)

            assistant_message = response.choices[0].message

            if assistant_message.tool_calls:
                agent.llm.logger.info(
                    f"Processing {len(assistant_message.tool_calls)} tool call(s) "
                    f"in iteration {iteration}"
                )

                # Pre-pass: clean surrogates and repair tool names so
                # the history serializer and the runner see identical
                # ids/names/arguments (frozen SDK objects otherwise
                # diverge between the two paths).
                clean_tool_calls, tcs_changed = (
                    agent.tool_runner.normalize_tool_calls(
                        assistant_message.tool_calls
                    )
                )

                reasoning_content = getattr(assistant_message, "reasoning_content", None)
                if reasoning_content:
                    agent.transport.emit(AgentEvent(EventType.THINKING, {"text": reasoning_content}))

                reasoning = (assistant_message.content or "").strip()
                if reasoning:
                    agent.transport.emit(AgentEvent(EventType.THINKING, {"text": reasoning}))

                assistant_msg: Dict[str, Any] = {
                    "role": "assistant",
                    "content": assistant_message.content or "",
                    "tool_calls": [
                        _serialize_tool_call(tc, logger=agent.llm.logger)
                        for tc in clean_tool_calls
                    ],
                }
                _attach_reasoning(assistant_msg, reasoning_content)

                msg_sanitized = sanitize_assistant_message(assistant_msg)
                if tcs_changed or msg_sanitized:
                    agent.llm.logger.warning(
                        "Sanitised lone surrogates in outbound assistant "
                        "message (iteration %d)",
                        iteration,
                    )
                agent.messages.append(assistant_msg)

                doom_triggered, tool_results = agent.tool_runner.execute(
                    clean_tool_calls,
                    cancellation_token=token,
                )
                agent.messages.extend(tool_results)
                if doom_triggered:
                    doom_content = (assistant_msg.get("content") or "").strip()
                    assistant_content_doom = (
                        doom_content
                        or "Tool execution halted by doom-loop detection."
                    )
                    final_msg_doom: Dict[str, Any] = {
                        "role": "assistant",
                        "content": assistant_content_doom,
                    }
                    _attach_reasoning(final_msg_doom, reasoning_content)
                    if sanitize_assistant_message(final_msg_doom):
                        agent.llm.logger.warning(
                            "Sanitised lone surrogates in doom-loop assistant message"
                        )

                    stop_result = self._dispatch_stop(
                        turn_end_reason="doom_loop",
                        last_assistant_message=assistant_content_doom,
                    )

                    if stop_result.blocking_error:
                        blocked = f"[Blocked by Stop hook] {stop_result.blocking_error}"
                        final_msg_doom["content"] = blocked
                        agent.messages.append(final_msg_doom)
                        self._emit_stop_hook_fired(
                            outcome="block",
                            turn_end_reason="doom_loop",
                            stop_result=stop_result,
                        )
                        return blocked

                    if stop_result.force_continue:
                        if self._stop_reentries >= self._stop_reentry_cap:
                            agent.llm.logger.warning(
                                "Stop hook reentry cap (%d) hit at doom-loop; ending turn.",
                                self._stop_reentry_cap,
                            )
                            agent.messages.append(final_msg_doom)
                            self._emit_stop_hook_fired(
                                outcome="reentry_capped",
                                turn_end_reason="doom_loop",
                                stop_result=stop_result,
                            )
                            return assistant_content_doom
                        follow_up = (
                            stop_result.follow_up_message
                            or stop_result.stop_reason
                            or "Stop hook requested continuation"
                        )
                        self._stop_reentries += 1
                        # ToolRunner's doom counter is NOT reset here —
                        # re-tripping doom is a reasonable outcome of
                        # "host insisted on continuing despite the model
                        # misbehaving."
                        agent.llm.logger.warning(
                            "Stop hook force_continue at doom-loop"
                        )
                        agent.messages.append(final_msg_doom)
                        agent.messages.append({
                            "role": "user",
                            "content": (
                                "<system-reminder>Stop hook injected this</system-reminder>\n"
                                f"{follow_up}"
                            ),
                        })
                        messages_with_system = [
                            {"role": "system", "content": system_prompt}
                        ] + agent.messages
                        iteration = 0
                        self._emit_stop_hook_fired(
                            outcome="continue",
                            turn_end_reason="doom_loop",
                            stop_result=stop_result,
                        )
                        continue

                    # Doom-loop does not echo additional_contexts —
                    # decorating the halt-detection fallback would be
                    # unhelpful UX.
                    agent.messages.append(final_msg_doom)
                    self._emit_stop_hook_fired(
                        outcome="allow",
                        turn_end_reason="doom_loop",
                        stop_result=stop_result,
                    )
                    return assistant_content_doom
                if token.is_cancelled:
                    raise AgentCancelledError(token.reason)

                new_active_skills = frozenset(agent.skill_manager.get_active_skills().keys())
                new_memory_version = agent.memory_manager.write_version
                if (
                    new_active_skills != current_active_skills
                    or new_memory_version != current_memory_version
                ):
                    self._emit_skill_and_memory_diffs(
                        prev_active=current_active_skills,
                        new_active=new_active_skills,
                        prev_memory_version=current_memory_version,
                        new_memory_version=new_memory_version,
                    )
                    current_active_skills = new_active_skills
                    current_memory_version = new_memory_version
                    system_prompt = agent._build_system_prompt()
                messages_with_system = [
                    {"role": "system", "content": system_prompt}
                ] + agent.messages
            else:
                agent.llm.logger.info(f"Reached final response in iteration {iteration}")
                assistant_content = assistant_message.content or ""
                reasoning_content = getattr(assistant_message, "reasoning_content", None)
                final_msg: Dict[str, Any] = {"role": "assistant", "content": assistant_content}
                _attach_reasoning(final_msg, reasoning_content)
                if sanitize_assistant_message(final_msg):
                    agent.llm.logger.warning(
                        "Sanitised lone surrogates in final assistant message "
                        "(iteration %d)", iteration,
                    )

                # Dispatch Stop before appending so blocking_error can
                # rewrite final_msg.content without leaving the original
                # answer in history.
                stop_result = self._dispatch_stop(
                    turn_end_reason="final_response",
                    last_assistant_message=assistant_content,
                )

                if stop_result.blocking_error:
                    blocked = f"[Blocked by Stop hook] {stop_result.blocking_error}"
                    final_msg["content"] = blocked
                    agent.messages.append(final_msg)
                    self._emit_stop_hook_fired(
                        outcome="block",
                        turn_end_reason="final_response",
                        stop_result=stop_result,
                    )
                    return blocked

                if stop_result.force_continue:
                    follow_up = (
                        stop_result.follow_up_message
                        or stop_result.stop_reason
                        or "Stop hook requested continuation"
                    )
                    if self._stop_reentries >= self._stop_reentry_cap:
                        agent.llm.logger.warning(
                            "Stop hook reentry cap (%d) hit; ending turn.",
                            self._stop_reentry_cap,
                        )
                        agent.messages.append(final_msg)
                        self._emit_stop_hook_fired(
                            outcome="reentry_capped",
                            turn_end_reason="final_response",
                            stop_result=stop_result,
                        )
                        return assistant_content
                    self._stop_reentries += 1
                    # Preserve the answer being continued from.
                    agent.messages.append(final_msg)
                    agent.messages.append({
                        "role": "user",
                        "content": (
                            "<system-reminder>Stop hook injected this</system-reminder>\n"
                            f"{follow_up}"
                        ),
                    })
                    messages_with_system = [
                        {"role": "system", "content": system_prompt}
                    ] + agent.messages
                    iteration = 0  # honest budget reset for the new sub-turn
                    self._emit_stop_hook_fired(
                        outcome="continue",
                        turn_end_reason="final_response",
                        stop_result=stop_result,
                    )
                    continue

                # additional_contexts ride on the answer as a
                # ``<stop-hook>`` block unless the hook set
                # ``suppressOutput: true`` (Agentao extension to the
                # Claude semantic, which documents only stdout suppression).
                if (
                    stop_result.additional_contexts
                    and not stop_result.suppress_output
                ):
                    extra = "\n".join(
                        f"<stop-hook>\n{ctx}\n</stop-hook>"
                        for ctx in stop_result.additional_contexts
                    )
                    final_msg["content"] = f"{assistant_content}\n{extra}"
                    assistant_content = final_msg["content"]
                agent.messages.append(final_msg)
                self._emit_stop_hook_fired(
                    outcome="allow",
                    turn_end_reason="final_response",
                    stop_result=stop_result,
                )
                return assistant_content

    # ------------------------------------------------------------------
    # Plugin-hook dispatch
    # ------------------------------------------------------------------

    def _inject_background_notifications(
        self,
        messages_with_system: list,
        system_prompt: str,
    ) -> list:
        agent = self._agent
        if agent.bg_store is None:
            return messages_with_system
        bg_notes = agent.bg_store.drain_notifications()
        if not bg_notes:
            return messages_with_system
        note_content = "\n\n".join(bg_notes)
        agent.messages.append({
            "role": "user",
            "content": (
                f"<system-reminder>\n"
                f"Background agent update:\n{note_content}\n"
                f"</system-reminder>"
            ),
        })
        messages_with_system = [
            {"role": "system", "content": system_prompt}
        ] + agent.messages
        agent.transport.emit(AgentEvent(EventType.BACKGROUND_NOTIFICATION_INJECTED, {
            "note_count": len(bg_notes),
            "content": note_content,
        }))
        return messages_with_system

    # ------------------------------------------------------------------
    # LLM call with API-overflow recovery
    # ------------------------------------------------------------------

    class _LlmOutcome:
        __slots__ = ("response", "messages_with_system", "system_prompt", "error_return")

        def __init__(
            self,
            *,
            response=None,
            messages_with_system=None,
            system_prompt=None,
            error_return: Optional[str] = None,
        ) -> None:
            self.response = response
            self.messages_with_system = messages_with_system
            self.system_prompt = system_prompt
            self.error_return = error_return

    def _call_llm_with_overflow_recovery(
        self,
        messages_with_system: list,
        system_prompt: str,
        tools: list,
        token: CancellationToken,
    ) -> "ChatLoopRunner._LlmOutcome":
        agent = self._agent
        try:
            response = agent._llm_call(messages_with_system, tools, token)
            return ChatLoopRunner._LlmOutcome(
                response=response,
                messages_with_system=messages_with_system,
                system_prompt=system_prompt,
            )
        except Exception as e:
            if not is_context_too_long_error(e):
                err_msg = f"[LLM API error: {e}]"
                agent.llm.logger.error(f"LLM call failed: {e}")
                agent.messages.append({"role": "assistant", "content": err_msg})
                return ChatLoopRunner._LlmOutcome(error_return=err_msg)
            agent.llm.logger.warning(f"Context overflow from API, forcing compression: {e}")
            self._dispatch_pre_compact(
                compaction_type="full",
                reason="api_overflow",
            )
            t0 = time.monotonic()
            pre_msgs = len(agent.messages)
            agent.messages = agent.context_manager.compress_messages(agent.messages)
            agent.context_manager._last_api_prompt_tokens = None  # stale after compression
            system_prompt = agent._build_system_prompt()
            messages_with_system = [
                {"role": "system", "content": system_prompt}
            ] + agent.messages
            agent._emit_context_compressed(
                compression_type="full",
                reason="api_overflow",
                pre_msgs=pre_msgs,
                post_msgs=len(agent.messages),
                duration_ms=round((time.monotonic() - t0) * 1000),
            )
            agent._last_session_summary_id = agent._emit_session_summary_if_new(
                agent._last_session_summary_id,
            )
            try:
                response = agent._llm_call(messages_with_system, tools, token)
                return ChatLoopRunner._LlmOutcome(
                    response=response,
                    messages_with_system=messages_with_system,
                    system_prompt=system_prompt,
                )
            except Exception as e2:
                if is_context_too_long_error(e2):
                    agent.llm.logger.warning(
                        "Context still too long after compression, keeping minimal history"
                    )
                    self._dispatch_pre_compact(
                        compaction_type="minimal_history",
                        reason="api_overflow_after_compression",
                    )
                    pre = len(agent.messages)
                    agent.messages = agent.messages[-2:]
                    messages_with_system = [
                        {"role": "system", "content": system_prompt}
                    ] + agent.messages
                    agent._emit_context_compressed(
                        compression_type="minimal_history",
                        reason="api_overflow_after_compression",
                        pre_msgs=pre,
                        post_msgs=len(agent.messages),
                    )
                    try:
                        response = agent._llm_call(messages_with_system, tools, token)
                        return ChatLoopRunner._LlmOutcome(
                            response=response,
                            messages_with_system=messages_with_system,
                            system_prompt=system_prompt,
                        )
                    except Exception as e3:
                        err_msg = f"[LLM API error: {e3}]"
                        agent.llm.logger.error(f"LLM call failed after compression: {e3}")
                        agent.messages.append({"role": "assistant", "content": err_msg})
                        return ChatLoopRunner._LlmOutcome(error_return=err_msg)
                else:
                    err_msg = f"[LLM API error: {e2}]"
                    agent.llm.logger.error(f"LLM call failed after compression: {e2}")
                    agent.messages.append({"role": "assistant", "content": err_msg})
                    return ChatLoopRunner._LlmOutcome(error_return=err_msg)

    # ------------------------------------------------------------------
    # Skill / memory diff replay events
    # ------------------------------------------------------------------

    def _emit_skill_and_memory_diffs(
        self,
        *,
        prev_active: frozenset,
        new_active: frozenset,
        prev_memory_version: int,
        new_memory_version: int,
    ) -> None:
        agent = self._agent
        for activated in sorted(new_active - prev_active):
            try:
                agent.transport.emit(AgentEvent(EventType.SKILL_ACTIVATED, {
                    "skill": activated,
                }))
            except Exception:
                pass
        for deactivated in sorted(prev_active - new_active):
            try:
                agent.transport.emit(AgentEvent(EventType.SKILL_DEACTIVATED, {
                    "skill": deactivated,
                }))
            except Exception:
                pass
        if new_memory_version != prev_memory_version:
            try:
                agent.transport.emit(AgentEvent(EventType.MEMORY_WRITE, {
                    "version_before": prev_memory_version,
                    "version_after": new_memory_version,
                    "total_entries": len(agent.memory_manager.get_all_entries()),
                }))
            except Exception:
                pass
