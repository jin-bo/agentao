"""Replay-observability helpers (v1.1) extracted from ``agentao/agent.py``.

These emit ``CONTEXT_COMPRESSED`` and ``SESSION_SUMMARY_WRITTEN``
transport events each time the chat loop rewrites history. Keeping
them here (rather than inline on ``Agentao``) lets new observability
events live in one place alongside other replay primitives.

The agent keeps thin facade methods (``_emit_context_compressed``,
``_emit_session_summary_if_new``, ``_latest_session_summary_id``) so
``runtime/chat_loop.py`` and any external test patches continue to
work unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ..transport import AgentEvent, EventType

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from ..agent import Agentao


def latest_session_summary_id(agent: "Agentao") -> Optional[str]:
    """Return the id of the most recent session summary, or ``None``."""
    if agent.memory_manager is None:
        return None
    try:
        rows = agent.memory_manager.get_recent_session_summaries(limit=1)
    except Exception:
        return None
    return rows[0].id if rows else None


def emit_context_compressed(
    agent: "Agentao",
    *,
    compression_type: str,
    reason: str,
    pre_msgs: int,
    post_msgs: int,
    pre_tokens: Optional[int] = None,
    post_tokens: Optional[int] = None,
    duration_ms: Optional[int] = None,
) -> None:
    """Emit a ``CONTEXT_COMPRESSED`` event over the agent's transport."""
    agent.transport.emit(AgentEvent(EventType.CONTEXT_COMPRESSED, {
        "type": compression_type,
        "reason": reason,
        "pre_msgs": pre_msgs,
        "post_msgs": post_msgs,
        "pre_est_tokens": pre_tokens,
        "post_est_tokens": post_tokens,
        "duration_ms": duration_ms,
    }))


def emit_session_summary_if_new(
    agent: "Agentao",
    previous_summary_id: Optional[str],
) -> Optional[str]:
    """Emit ``SESSION_SUMMARY_WRITTEN`` when the latest summary id changed.

    Returns the (possibly unchanged) latest summary id so a caller can
    keep polling across multiple compression events in one turn.
    """
    if agent.memory_manager is None:
        return previous_summary_id
    try:
        rows = agent.memory_manager.get_recent_session_summaries(limit=1)
    except Exception:
        return previous_summary_id
    if not rows:
        return previous_summary_id
    current = rows[0]
    if current.id == previous_summary_id:
        return previous_summary_id
    agent.transport.emit(AgentEvent(EventType.SESSION_SUMMARY_WRITTEN, {
        "summary_id": current.id,
        "session_id": agent._session_id,
        "tokens_before": current.tokens_before,
        "messages_summarized": current.messages_summarized,
        "summary_size": len(current.summary_text or ""),
    }))
    return current.id
