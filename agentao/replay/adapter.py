"""ReplayAdapter — subscribes to AgentEvent and writes ReplayEvents.

The adapter wraps an existing :class:`~agentao.transport.base.Transport`.
Every ``emit`` / ``confirm_tool`` call forwards to the inner transport
and also translates the interaction into one or more replay events via
the attached :class:`~agentao.replay.recorder.ReplayRecorder`.

All other runtime layers (CLI, ToolRunner, ACP transport) keep talking
to the Transport interface — they never see the recorder directly. Only
session-lifecycle events that do not flow through AgentEvent (session
started / ended) call the recorder from the Agent directly.
"""

from __future__ import annotations

import threading
import uuid as _uuid
from typing import Any, Dict, List, Optional

from ..transport import AgentEvent, EventType
from .events import EventKind
from .recorder import ReplayRecorder


def _short_id() -> str:
    return _uuid.uuid4().hex[:12]


class ReplayAdapter:
    """Wrap a Transport and mirror its events to a ReplayRecorder."""

    def __init__(self, inner: Any, recorder: ReplayRecorder) -> None:
        self._inner = inner
        self._recorder = recorder
        self._turn_id: Optional[str] = None
        # Per-thread subagent stack. ToolRunner executes tool calls in a
        # ThreadPoolExecutor, so two sub-agents may be running
        # simultaneously on different threads. Keeping the stack in
        # thread-local storage prevents cross-thread turn_id leakage.
        self._tls = threading.local()

    # -- turn management ----------------------------------------------------

    @property
    def recorder(self) -> ReplayRecorder:
        return self._recorder

    def begin_turn(self, user_message: str) -> str:
        """Start a new replay turn and return its ``turn_id``."""
        turn_id = _short_id()
        self._turn_id = turn_id
        self._recorder.record(
            EventKind.TURN_STARTED,
            turn_id=turn_id,
            payload={"has_user_message": bool(user_message)},
        )
        self._recorder.record(
            EventKind.USER_MESSAGE,
            turn_id=turn_id,
            payload={"content": user_message or ""},
        )
        return turn_id

    def end_turn(
        self,
        final_text: str,
        *,
        status: str = "ok",
        error: Optional[str] = None,
    ) -> None:
        turn_id = self._turn_id
        if turn_id is None:
            return
        self._recorder.record(
            EventKind.TURN_COMPLETED,
            turn_id=turn_id,
            payload={
                "final_text": final_text or "",
                "status": status,
                "error": error,
            },
        )
        self._turn_id = None

    # -- subagent stack -----------------------------------------------------

    def _subagent_stack(self) -> List[Dict[str, str]]:
        stack = getattr(self._tls, "stack", None)
        if stack is None:
            stack = []
            self._tls.stack = stack
        return stack

    def _current_turn_id(self) -> Optional[str]:
        stack = self._subagent_stack()
        if stack:
            return stack[-1]["turn_id"]
        return self._turn_id

    def _current_parent_turn(self) -> Optional[str]:
        stack = self._subagent_stack()
        if len(stack) == 1:
            return self._turn_id
        if len(stack) > 1:
            return stack[-2]["turn_id"]
        return None

    # -- Transport protocol -------------------------------------------------

    def emit(self, event: AgentEvent) -> None:
        try:
            self._inner.emit(event)
        except Exception:
            pass
        self._mirror(event)

    def confirm_tool(self, tool_name: str, description: str, args: dict) -> bool:
        # The matching request event is emitted by ToolRunner through
        # ``emit(TOOL_CONFIRMATION)``; this method only needs to record
        # the user's response.
        result = self._inner.confirm_tool(tool_name, description, args)
        try:
            self._recorder.record(
                EventKind.TOOL_CONFIRMATION_RESOLVED,
                turn_id=self._current_turn_id(),
                payload={
                    "tool": tool_name,
                    "approved": bool(result),
                },
            )
        except Exception:
            pass
        return bool(result)

    def ask_user(self, question: str) -> str:
        # v1.1: record both the question and the answer. The answer goes
        # through the scanner + a 500-char truncation policy inside
        # ``sanitize_event`` so an accidental password-in-prompt is
        # redacted on disk.
        try:
            self._recorder.record(
                EventKind.ASK_USER_REQUESTED,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload={"question": question or ""},
            )
        except Exception:
            pass
        answer = self._inner.ask_user(question)
        try:
            self._recorder.record(
                EventKind.ASK_USER_ANSWERED,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload={
                    "question": question or "",
                    "answer": answer if isinstance(answer, str) else str(answer),
                },
            )
        except Exception:
            pass
        return answer

    def on_max_iterations(self, count: int, messages: list) -> dict:
        handler = getattr(self._inner, "on_max_iterations", None)
        if callable(handler):
            return handler(count, messages)
        return {"action": "stop"}

    # -- translation --------------------------------------------------------

    def _mirror(self, event: AgentEvent) -> None:
        data = event.data or {}
        kind = event.type

        if kind == EventType.LLM_TEXT:
            chunk = data.get("chunk", "")
            if chunk:
                self._recorder.record(
                    EventKind.ASSISTANT_TEXT_CHUNK,
                    turn_id=self._current_turn_id(),
                    parent_turn_id=self._current_parent_turn(),
                    payload={"chunk": chunk},
                )
            return

        if kind == EventType.THINKING:
            text = data.get("text", "")
            if text:
                self._recorder.record(
                    EventKind.ASSISTANT_THOUGHT_CHUNK,
                    turn_id=self._current_turn_id(),
                    parent_turn_id=self._current_parent_turn(),
                    payload={"text": text},
                )
            return

        if kind == EventType.TOOL_CONFIRMATION:
            self._recorder.record(
                EventKind.TOOL_CONFIRMATION_REQUESTED,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload={
                    "tool": data.get("tool"),
                    "args": data.get("args", {}),
                },
            )
            return

        if kind == EventType.TOOL_START:
            tool_name = data.get("tool", "")
            self._recorder.record(
                EventKind.TOOL_STARTED,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload={
                    "tool": tool_name,
                    "args": data.get("args", {}),
                    "call_id": data.get("call_id"),
                    "tool_source": _classify_tool(tool_name),
                },
            )
            return

        if kind == EventType.TOOL_OUTPUT:
            # Truncation + secret scanning happen inside the recorder's
            # ``sanitize_event`` path (policy = ScanTruncate, flat meta).
            # The adapter just forwards the raw chunk and lets sanitize
            # own the wire shape.
            self._recorder.record(
                EventKind.TOOL_OUTPUT_CHUNK,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload={
                    "tool": data.get("tool"),
                    "call_id": data.get("call_id"),
                    "chunk": data.get("chunk", ""),
                },
            )
            return

        if kind == EventType.TOOL_COMPLETE:
            self._recorder.record(
                EventKind.TOOL_COMPLETED,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload={
                    "tool": data.get("tool"),
                    "call_id": data.get("call_id"),
                    "status": data.get("status", "ok"),
                    "duration_ms": data.get("duration_ms", 0),
                    "error": data.get("error"),
                },
            )
            return

        if kind == EventType.LLM_CALL_STARTED:
            self._recorder.record(
                EventKind.LLM_CALL_STARTED,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload=dict(data),
            )
            return

        if kind == EventType.LLM_CALL_COMPLETED:
            self._recorder.record(
                EventKind.LLM_CALL_COMPLETED,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload=dict(data),
            )
            return

        if kind == EventType.LLM_CALL_DELTA:
            self._recorder.record(
                EventKind.LLM_CALL_DELTA,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload=dict(data),
            )
            return

        if kind == EventType.LLM_CALL_IO:
            self._recorder.record(
                EventKind.LLM_CALL_IO,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload=dict(data),
            )
            return

        if kind == EventType.BACKGROUND_NOTIFICATION_INJECTED:
            self._recorder.record(
                EventKind.BACKGROUND_NOTIFICATION_INJECTED,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload=dict(data),
            )
            return

        if kind == EventType.CONTEXT_COMPRESSED:
            self._recorder.record(
                EventKind.CONTEXT_COMPRESSED,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload=dict(data),
            )
            return

        if kind == EventType.SESSION_SUMMARY_WRITTEN:
            self._recorder.record(
                EventKind.SESSION_SUMMARY_WRITTEN,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload=dict(data),
            )
            return

        # Step 6: runtime state-change events.
        if kind == EventType.SKILL_ACTIVATED:
            self._recorder.record(
                EventKind.SKILL_ACTIVATED,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload=dict(data),
            )
            return

        if kind == EventType.SKILL_DEACTIVATED:
            self._recorder.record(
                EventKind.SKILL_DEACTIVATED,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload=dict(data),
            )
            return

        if kind == EventType.MEMORY_WRITE:
            self._recorder.record(
                EventKind.MEMORY_WRITE,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload=dict(data),
            )
            return

        if kind == EventType.MODEL_CHANGED:
            self._recorder.record(
                EventKind.MODEL_CHANGED,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload=dict(data),
            )
            return

        if kind == EventType.MEMORY_DELETE:
            self._recorder.record(
                EventKind.MEMORY_DELETE,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload=dict(data),
            )
            return

        if kind == EventType.MEMORY_CLEARED:
            self._recorder.record(
                EventKind.MEMORY_CLEARED,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload=dict(data),
            )
            return

        if kind == EventType.PERMISSION_MODE_CHANGED:
            self._recorder.record(
                EventKind.PERMISSION_MODE_CHANGED,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload=dict(data),
            )
            return

        if kind == EventType.READONLY_MODE_CHANGED:
            self._recorder.record(
                EventKind.READONLY_MODE_CHANGED,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload=dict(data),
            )
            return

        if kind == EventType.PLUGIN_HOOK_FIRED:
            self._recorder.record(
                EventKind.PLUGIN_HOOK_FIRED,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload=dict(data),
            )
            return

        if kind == EventType.TOOL_RESULT:
            # v1.1: canonical per-tool-call final result event. Forwarded
            # verbatim into the recorder where ``sanitize_event`` applies
            # the 8000-char head/tail truncation (policy = ScanTruncate,
            # head_ratio=0.2) and the always-on secrets scanner.
            self._recorder.record(
                EventKind.TOOL_RESULT,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload={
                    "tool": data.get("tool"),
                    "call_id": data.get("call_id"),
                    "content": data.get("content", ""),
                    "content_hash": data.get("content_hash"),
                    "original_chars": data.get("original_chars", 0),
                    "saved_to_disk": bool(data.get("saved_to_disk", False)),
                    "disk_path": data.get("disk_path"),
                    "status": data.get("status", "ok"),
                    "duration_ms": data.get("duration_ms", 0),
                    "error": data.get("error"),
                },
            )
            return

        if kind == EventType.AGENT_START:
            new_turn = _short_id()
            parent_turn = self._current_turn_id()
            self._recorder.record(
                EventKind.SUBAGENT_STARTED,
                turn_id=new_turn,
                parent_turn_id=parent_turn,
                payload={
                    "agent": data.get("agent"),
                    "task": data.get("task"),
                    "max_turns": data.get("max_turns"),
                },
            )
            self._subagent_stack().append({
                "turn_id": new_turn,
                "agent": str(data.get("agent", "")),
            })
            return

        if kind == EventType.AGENT_END:
            stack = self._subagent_stack()
            top = stack.pop() if stack else None
            subagent_turn = top["turn_id"] if top else _short_id()
            self._recorder.record(
                EventKind.SUBAGENT_COMPLETED,
                turn_id=subagent_turn,
                parent_turn_id=self._current_turn_id(),
                payload={
                    "agent": data.get("agent"),
                    "state": data.get("state"),
                    "turns": data.get("turns"),
                    "tool_calls": data.get("tool_calls"),
                    "tokens": data.get("tokens"),
                    "duration_ms": data.get("duration_ms"),
                    "error": data.get("error"),
                },
            )
            return

        if kind == EventType.ERROR:
            self._recorder.record(
                EventKind.ERROR,
                turn_id=self._current_turn_id(),
                parent_turn_id=self._current_parent_turn(),
                payload={
                    "message": data.get("message"),
                    "detail": data.get("detail"),
                },
            )
            return

        # TURN_START is fired per LLM iteration (display reset) rather
        # than per replay turn — intentionally not mirrored to avoid
        # polluting the stream with noise.


def _classify_tool(tool_name: str) -> str:
    """Return ``"mcp"`` for MCP-discovered tools, ``"builtin"`` otherwise."""
    if isinstance(tool_name, str) and tool_name.startswith("mcp_"):
        return "mcp"
    return "builtin"
