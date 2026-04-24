"""LLM-call attempt helper extracted from ``agentao/agent.py``.

One call = one LLM HTTP attempt with streaming + the v1.1 replay-
observability event set (``LLM_CALL_STARTED`` / ``LLM_CALL_DELTA`` /
``LLM_CALL_IO`` / ``LLM_CALL_COMPLETED``). The function mutates
per-turn counters on the agent (``_llm_call_seq`` and
``_llm_call_last_msg_count``) the same way the inline version did, so
the ``attempt`` numbers and ``delta_start_index`` semantics stay
identical to the prior behavior.

The ``Agentao._llm_call`` method is kept as a thin facade so
``ChatLoopRunner`` (which invokes it as ``agent._llm_call(...)``) and
any test patches continue to work unchanged.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..cancellation import CancellationToken
from ..transport import AgentEvent, EventType

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from ..agent import Agentao


def run_llm_call(
    agent: "Agentao",
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    cancellation_token: Optional[CancellationToken] = None,
) -> Any:
    """Run one LLM attempt with replay observability events."""
    agent._llm_call_seq = getattr(agent, "_llm_call_seq", 0) + 1
    attempt = agent._llm_call_seq

    system_text = ""
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "system":
            content = msg.get("content", "")
            if isinstance(content, str):
                system_text = content
            break
    system_prompt_hash = hashlib.sha256(
        system_text.encode("utf-8", errors="replace"),
    ).hexdigest()[:16]

    tool_schemas = tools or []
    tool_names = sorted(
        t.get("function", {}).get("name", "") for t in tool_schemas
    )
    tools_hash = hashlib.sha256(
        json.dumps(tool_names).encode("utf-8"),
    ).hexdigest()[:16]

    capture_flags = agent._replay_config.capture_flags if agent._replay_config else {}

    started_payload: Dict[str, Any] = {
        "attempt": attempt,
        "model": agent.llm.model,
        "temperature": agent.llm.temperature,
        "max_tokens": agent.llm.max_tokens,
        "n_messages": len(messages),
        "n_tool_messages": sum(
            1 for m in messages if isinstance(m, dict) and m.get("role") == "tool"
        ),
        "n_system_reminder_blocks": sum(
            1 for m in messages
            if isinstance(m, dict)
            and m.get("role") == "user"
            and "<system-reminder>" in str(m.get("content", ""))
        ),
        "system_prompt_hash": system_prompt_hash,
        "tools_hash": tools_hash,
        "tool_count": len(tool_names),
    }
    agent.transport.emit(AgentEvent(EventType.LLM_CALL_STARTED, started_payload))

    # Delta capture (default on): just-added messages since the last
    # _llm_call in this turn. The first call of the turn reports the
    # full message list (delta_start_index == 0).
    if capture_flags.get("capture_llm_delta", True):
        delta_start = getattr(agent, "_llm_call_last_msg_count", 0)
        if delta_start > len(messages):
            # Caller shrank history (compression / retry with fewer
            # messages). Treat it as a reset so the reader sees the
            # post-shrink list rather than negative slicing.
            delta_start = 0
        added = messages[delta_start:]
        agent.transport.emit(AgentEvent(EventType.LLM_CALL_DELTA, {
            "attempt": attempt,
            "delta_start_index": delta_start,
            "total_messages": len(messages),
            "added_messages": added,
        }))
        agent._llm_call_last_msg_count = len(messages)

    # Full IO capture (opt-in). Cost is large: every call writes the
    # entire messages array. Scanner still runs inside the recorder.
    if capture_flags.get("capture_full_llm_io", False):
        agent.transport.emit(AgentEvent(EventType.LLM_CALL_IO, {
            "attempt": attempt,
            "messages": messages,
            "tools": tool_schemas,
        }))

    t0 = time.monotonic()
    try:
        response = agent.llm.chat_stream(
            messages=messages,
            tools=tools,
            max_tokens=agent.llm.max_tokens,
            on_text_chunk=lambda chunk: agent.transport.emit(
                AgentEvent(EventType.LLM_TEXT, {"chunk": chunk})
            ),
            cancellation_token=cancellation_token,
        )
    except Exception as exc:
        agent.transport.emit(AgentEvent(EventType.LLM_CALL_COMPLETED, {
            "attempt": attempt,
            "status": "error",
            "duration_ms": round((time.monotonic() - t0) * 1000),
            "error_class": type(exc).__name__,
            "error_message": str(exc)[:500],
            "finish_reason": None,
            "prompt_tokens": None,
            "completion_tokens": None,
        }))
        raise

    finish_reason: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    try:
        choices = getattr(response, "choices", None)
        if choices:
            finish_reason = getattr(choices[0], "finish_reason", None)
        usage = getattr(response, "usage", None)
        if usage is not None:
            prompt_tokens = getattr(usage, "prompt_tokens", None)
            completion_tokens = getattr(usage, "completion_tokens", None)
    except Exception:
        pass

    agent.transport.emit(AgentEvent(EventType.LLM_CALL_COMPLETED, {
        "attempt": attempt,
        "status": "ok",
        "duration_ms": round((time.monotonic() - t0) * 1000),
        "error_class": None,
        "error_message": None,
        "finish_reason": finish_reason,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }))
    return response
