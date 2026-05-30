"""Per-turn lifecycle extracted from ``Agentao.chat()``.

Owns the cross-cutting concerns of a single turn:

- Cancellation-token assignment (``agent._current_token``)
- Per-turn LLM-call counter reset
- Session-summary id snapshot
- TURN_BEGIN / TURN_END transport events with status tracking
- ``KeyboardInterrupt`` / ``AgentCancelledError`` / generic exception
  handling around the inner loop

The loop body itself stays in :class:`agentao.runtime.chat_loop.ChatLoopRunner`
— this file only handles lifecycle and error mapping. The agent's
public ``chat()`` method is kept as a thin facade so callers and
tests that target ``Agentao.chat`` continue to work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from ..cancellation import AgentCancelledError, CancellationToken
from ..replay.observability import latest_session_summary_id
from ..transport import AgentEvent, EventType
from .identity import new_turn_id

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from ..agent import Agentao


def run_turn(
    agent: "Agentao",
    user_message: str,
    max_iterations: int = 100,
    cancellation_token: Optional[CancellationToken] = None,
    images: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Run one full ``chat()`` turn and return the assistant's reply.

    Behavior preserved verbatim from the prior inline implementation:

    - A fresh ``CancellationToken`` is minted when the caller didn't
      supply one, stored on ``agent._current_token`` for the duration
      of the turn, and cleared in the ``finally`` block.
    - ``_llm_call_seq`` is reset to 0 so LLM_CALL_* event ``attempt``
      numbers restart at 1 for every turn. ``_llm_call_last_msg_count``
      is seeded to ``1 + len(agent.messages)`` so the first delta
      event emits only messages added inside this turn.
    - ``agent._last_session_summary_id`` is snapshot up-front so the
      inner loop's compression paths can fire
      ``SESSION_SUMMARY_WRITTEN`` exactly once per new summary.
    - TURN_BEGIN / TURN_END transport events frame the turn so the
      ``finally`` TURN_END fires even when ``replay_manager.end()``
      concurrently swaps the adapter out (e.g. ACP session teardown).
    - ``KeyboardInterrupt`` is mapped to a ``[Interrupted by user]``
      assistant message + ``status="cancelled"``.
    - ``AgentCancelledError`` is mapped to ``[Cancelled: <reason>]`` +
      ``status="cancelled"``.
    - All other exceptions are re-raised after recording ``status="error"``.
    """
    token = cancellation_token or CancellationToken()
    agent._current_token = token
    # Public lifecycle events read this via ``agent._current_turn_id``;
    # cleared in ``finally`` so events between turns carry ``turn_id=None``.
    agent._current_turn_id = new_turn_id()
    # Reset the per-turn LLM-call counters so ``attempt`` numbers in
    # LLM_CALL_* events restart at 1 and ``delta_start_index`` tracks
    # only messages added in the current chat() invocation.
    #
    # The first ``_llm_call`` of this turn receives ``[system] + agent.messages``
    # after the new user message is appended. Seeding the baseline to
    # ``1 + len(agent.messages)`` (system + pre-turn history) makes the
    # first LLM_CALL_DELTA emit only the messages added in this turn,
    # instead of replaying the entire accumulated conversation every turn.
    agent._llm_call_seq = 0
    agent._llm_call_last_msg_count = 1 + len(agent.messages)
    # Turn-level tool-call counter. The chat loop bumps this by the
    # number of tool calls in each LLM response; TURN_END reports the
    # total so host telemetry can size a turn without replaying every
    # TOOL_START. Reset per turn (and read defensively in the finally).
    agent._turn_tool_count = 0
    # Snapshot the latest session-summary id so the inner loop can
    # fire SESSION_SUMMARY_WRITTEN each time compress_messages writes
    # a new one. Held on the instance so compression paths inside the
    # retry branches can update it without threading it through args.
    agent._last_session_summary_id = latest_session_summary_id(agent)
    # TURN_BEGIN/TURN_END flow through the transport so concurrent
    # ``replay_manager.end()`` (e.g. ACP session teardown) can swap the
    # adapter out mid-turn without breaking the finally block — the
    # TURN_END below lands on whatever transport is bound at the time.
    try:
        agent.transport.emit(AgentEvent(EventType.TURN_BEGIN, {
            "user_message": user_message,
        }))
    except Exception:
        pass
    final_text = ""
    status = "ok"
    error_detail: Optional[str] = None
    try:
        # Forward ``images`` only when present so the historical
        # three-argument ``_chat_inner`` signature (which subclasses and
        # test stubs still patch by name) keeps working for text turns.
        if images:
            final_text = agent._chat_inner(user_message, max_iterations, token,
                                           images=images)
        else:
            final_text = agent._chat_inner(user_message, max_iterations, token)
        return final_text
    except KeyboardInterrupt:
        token.cancel("user-cancel")
        agent.messages.append({"role": "assistant", "content": "[Interrupted]"})
        final_text = "[Interrupted by user]"
        status = "cancelled"
        error_detail = "user-cancel"
        return final_text
    except AgentCancelledError as e:
        agent.messages.append({"role": "assistant", "content": f"[Cancelled: {e.reason}]"})
        final_text = f"[Cancelled: {e.reason}]"
        status = "cancelled"
        error_detail = e.reason
        return final_text
    except Exception as e:
        status = "error"
        error_detail = str(e)
        raise
    finally:
        try:
            agent.transport.emit(AgentEvent(EventType.TURN_END, {
                "final_text": final_text,
                "status": status,
                "error": error_detail,
                "tool_count": getattr(agent, "_turn_tool_count", 0),
            }))
        except Exception:
            pass
        agent._current_token = None
        agent._current_turn_id = None
