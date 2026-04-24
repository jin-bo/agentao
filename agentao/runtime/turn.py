"""Per-turn lifecycle extracted from ``Agentao.chat()``.

Owns the cross-cutting concerns of a single turn:

- Cancellation-token assignment (``agent._current_token``)
- Per-turn LLM-call counter reset
- Session-summary id snapshot
- Replay ``begin_turn`` / ``end_turn`` with status tracking
- ``KeyboardInterrupt`` / ``AgentCancelledError`` / generic exception
  handling around the inner loop

The loop body itself stays in :class:`agentao.runtime.chat_loop.ChatLoopRunner`
— this file only handles lifecycle and error mapping. The agent's
public ``chat()`` method is kept as a thin facade so callers and
tests that target ``Agentao.chat`` continue to work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ..cancellation import AgentCancelledError, CancellationToken

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from ..agent import Agentao


def run_turn(
    agent: "Agentao",
    user_message: str,
    max_iterations: int = 100,
    cancellation_token: Optional[CancellationToken] = None,
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
    - The replay adapter (if any) is captured at entry so ``end_turn``
      fires in ``finally`` even when ``end_replay()`` concurrently
      clears ``agent._replay_adapter`` (e.g. ACP session teardown).
    - ``KeyboardInterrupt`` is mapped to a ``[Interrupted by user]``
      assistant message + ``status="cancelled"``.
    - ``AgentCancelledError`` is mapped to ``[Cancelled: <reason>]`` +
      ``status="cancelled"``.
    - All other exceptions are re-raised after recording ``status="error"``.
    """
    token = cancellation_token or CancellationToken()
    agent._current_token = token
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
    # Snapshot the latest session-summary id so the inner loop can
    # fire SESSION_SUMMARY_WRITTEN each time compress_messages writes
    # a new one. Held on the instance so compression paths inside the
    # retry branches can update it without threading it through args.
    agent._last_session_summary_id = agent._latest_session_summary_id()
    # Snapshot the adapter so the finally block can emit end_turn even if
    # end_replay() is called concurrently (e.g. ACP session teardown) and
    # clears agent._replay_adapter before this turn finishes unwinding.
    replay_adapter = agent._replay_adapter
    if replay_adapter is not None:
        try:
            replay_adapter.begin_turn(user_message)
        except Exception:
            pass
    final_text = ""
    status = "ok"
    error_detail: Optional[str] = None
    try:
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
        if replay_adapter is not None:
            try:
                replay_adapter.end_turn(
                    final_text, status=status, error=error_detail,
                )
            except Exception:
                pass
        agent._current_token = None
