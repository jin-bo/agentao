"""ChatLoopRunner — owns the per-turn chat loop body.

``Agentao.chat()`` still owns the turn lifecycle (cancellation token,
replay begin_turn/end_turn, ``_current_token`` bookkeeping) and
delegates the loop body here.

Behavioral contract preserved:

- The loop reads and mutates the same agent state (``messages``,
  ``context_manager``, ``_last_session_summary_id``, ``_last_user_message``,
  ``_llm_call_seq`` / ``_llm_call_last_msg_count``) — no shadow copies.
- ``_build_system_prompt``, ``_llm_call``, ``_emit_context_compressed``
  and ``_emit_session_summary_if_new`` are still invoked as agent
  methods so any subclass override or test patch continues to apply.
- All ``transport.emit`` event types and payload shapes are unchanged.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from ..cancellation import AgentCancelledError, CancellationToken
from ..context_manager import is_context_too_long_error
from ..transport import AgentEvent, EventType
from .sanitize import canonicalize_tool_arguments, sanitize_assistant_message

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from ..agent import Agentao


MAX_REASONING_HISTORY_CHARS = 500  # Truncate reasoning_content in history to ~125 tokens


def _attach_reasoning(msg: Dict[str, Any], reasoning_content: Optional[str]) -> None:
    """Attach a truncated copy of ``reasoning_content`` to ``msg`` (in place).

    No-op when ``reasoning_content`` is ``None``. Truncation cap matches
    the prompt-budget assumption that reasoning shouldn't dominate
    history. The trailing ellipsis flags the truncation to the model.
    """
    if reasoning_content is None:
        return
    stored = reasoning_content[:MAX_REASONING_HISTORY_CHARS]
    if len(reasoning_content) > MAX_REASONING_HISTORY_CHARS:
        stored += "..."
    msg["reasoning_content"] = stored


def _serialize_tool_call(tc, *, logger=None) -> dict:
    """Serialize a tool call object to a dict for conversation history.

    Uses model_dump() to preserve ALL Pydantic extra fields at their correct
    level. This handles Gemini's thought_signature (and similar fields)
    regardless of which level they appear at in the response.

    The ``function.arguments`` string is round-tripped through the repair
    pipeline and re-emitted as canonical compact JSON so downstream API
    proxies receive valid JSON even when the model emitted malformed args.
    """
    if hasattr(tc, "model_dump"):
        entry = tc.model_dump()
    else:
        entry: Dict[str, Any] = {
            "id": tc.id,
            "type": "function",
            "function": {
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            },
        }
        thought_sig = getattr(tc.function, "thought_signature", None)
        if thought_sig is None:
            thought_sig = getattr(tc, "thought_signature", None)
        if thought_sig is not None:
            entry["function"]["thought_signature"] = thought_sig

    fn = entry.get("function")
    if isinstance(fn, dict):
        fn["arguments"] = canonicalize_tool_arguments(
            fn.get("arguments", ""),
            tool_name=fn.get("name", "?"),
            logger=logger,
        )
    return entry


class _HookOutcome:
    """Result of UserPromptSubmit plugin-hook dispatch.

    One of three shapes:

    - ``early_return`` is a string → the loop should return that string
      immediately without calling the LLM (block / stop verdicts).
    - ``early_return`` is ``None`` and ``user_message`` is unchanged →
      no hook fired, or hooks ran with no effect.
    - ``early_return`` is ``None`` and ``user_message`` was rewritten →
      hooks injected additional context that should be prepended.
    """

    __slots__ = ("early_return", "user_message")

    def __init__(self, *, early_return: Optional[str], user_message: str) -> None:
        self.early_return = early_return
        self.user_message = user_message


class ChatLoopRunner:
    """Run one ``chat()`` turn for an :class:`agentao.agent.Agentao`."""

    def __init__(self, agent: "Agentao") -> None:
        self._agent = agent

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
                    break

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
                    break
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
                agent.messages.append(final_msg)
                return assistant_content

        # Hit max iterations
        agent.llm.logger.warning(f"Maximum tool call iterations ({max_iterations}) reached")
        assistant_content = (
            assistant_message.content if assistant_message else None
        ) or "Maximum tool call iterations reached."
        reasoning_content = (
            getattr(assistant_message, "reasoning_content", None)
            if assistant_message else None
        )
        final_msg: Dict[str, Any] = {"role": "assistant", "content": assistant_content}
        _attach_reasoning(final_msg, reasoning_content)
        if sanitize_assistant_message(final_msg):
            agent.llm.logger.warning(
                "Sanitised lone surrogates in max-iteration assistant message"
            )
        agent.messages.append(final_msg)
        return assistant_content

    # ------------------------------------------------------------------
    # Plugin-hook dispatch
    # ------------------------------------------------------------------

    def _dispatch_user_prompt_submit(self, user_message: str) -> _HookOutcome:
        agent = self._agent
        if not agent._plugin_hook_rules:
            return _HookOutcome(early_return=None, user_message=user_message)

        from ..plugins.hooks import (
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

    # ------------------------------------------------------------------
    # Context-management steps run before each LLM call
    # ------------------------------------------------------------------

    def _maybe_microcompact(
        self,
        messages_with_system: list,
        system_prompt: str,
    ) -> Tuple[list, str]:
        agent = self._agent
        if not agent.context_manager.needs_microcompaction(messages_with_system):
            return messages_with_system, system_prompt
        t0 = time.monotonic()
        pre_tokens = agent.context_manager.estimate_tokens(messages_with_system)
        pre_msgs = len(agent.messages)
        agent.messages = agent.context_manager.microcompact_messages(agent.messages)
        messages_with_system = [
            {"role": "system", "content": system_prompt}
        ] + agent.messages
        agent._emit_context_compressed(
            compression_type="microcompact",
            reason="microcompact_threshold",
            pre_msgs=pre_msgs,
            post_msgs=len(agent.messages),
            pre_tokens=pre_tokens,
            post_tokens=agent.context_manager.estimate_tokens(messages_with_system),
            duration_ms=round((time.monotonic() - t0) * 1000),
        )
        return messages_with_system, system_prompt

    def _maybe_full_compress(
        self,
        messages_with_system: list,
        system_prompt: str,
    ) -> Tuple[list, str]:
        agent = self._agent
        if not agent.context_manager.needs_compression(messages_with_system):
            return messages_with_system, system_prompt
        agent.llm.logger.info("Context compression triggered inside loop")
        t0 = time.monotonic()
        pre_tokens = agent.context_manager.estimate_tokens(messages_with_system)
        pre_msgs = len(agent.messages)
        agent.messages = agent.context_manager.compress_messages(agent.messages, is_auto=True)
        agent.context_manager._last_api_prompt_tokens = None  # stale after compression
        system_prompt = agent._build_system_prompt()
        messages_with_system = [
            {"role": "system", "content": system_prompt}
        ] + agent.messages
        agent.llm.logger.info(f"Context compressed to {len(agent.messages)} messages")
        agent._emit_context_compressed(
            compression_type="full",
            reason="compression_threshold",
            pre_msgs=pre_msgs,
            post_msgs=len(agent.messages),
            pre_tokens=pre_tokens,
            post_tokens=agent.context_manager.estimate_tokens(messages_with_system),
            duration_ms=round((time.monotonic() - t0) * 1000),
        )
        agent._last_session_summary_id = agent._emit_session_summary_if_new(
            agent._last_session_summary_id,
        )
        return messages_with_system, system_prompt

    def _inject_background_notifications(
        self,
        messages_with_system: list,
        system_prompt: str,
    ) -> list:
        agent = self._agent
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
