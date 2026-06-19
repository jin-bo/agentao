"""ACP transport adapter — maps Agentao runtime events to ``session/update``.

This is the Agentao-side :class:`~agentao.transport.base.Transport`
implementation that translates internal :class:`AgentEvent` values into
ACP ``session/update`` notifications. Issue 06 put a debug-log no-op
here so ``agent.chat()`` could run; Issue 07 replaces that no-op with
the real mapping defined below.

Mapping summary
---------------

=====================  ==============================================
Internal event         ACP ``session/update.update.sessionUpdate``
=====================  ==============================================
``TURN_START``         *(no notification — purely internal bookkeeping)*
``LLM_TEXT``           ``agent_message_chunk`` with text content
``THINKING``           ``agent_thought_chunk`` with text content
``TOOL_START``         ``tool_call`` (toolCallId, title, kind, status="pending", rawInput)
``TOOL_OUTPUT``        ``tool_call_update`` (content append, status="in_progress")
``TOOL_COMPLETE``      ``tool_call_update`` (status="completed" or "failed")
``AGENT_START``        ``agent_thought_chunk`` with a "[sub-agent started: …]" marker
``AGENT_END``          ``agent_thought_chunk`` with a "[sub-agent finished: …]" marker
``ERROR``              ``agent_message_chunk`` with an "Error: …" marker
``TOOL_CONFIRMATION``  *(no notification — Issue 08's ``session/request_permission``)*
=====================  ==============================================

Design notes
------------

- **Sub-agent events are flattened into thought chunks** rather than
  synthesized into nested ``tool_call`` updates. Nested tool calls would
  require synthesizing collision-free ``toolCallId`` values across
  concurrent sub-agents and would add mapping state to the transport.
  Text markers give the user visibility with zero state. A later issue
  may upgrade this if ACP clients want structured sub-agent timelines.

- **Tool kind mapping**: ACP's ``tool_call.kind`` is a closed enum
  (``read``, ``edit``, ``delete``, ``move``, ``search``, ``execute``,
  ``think``, ``fetch``, ``other``). :func:`_tool_kind` maps Agentao tool
  names to those values; unknown tools fall back to ``"other"``.

- **JSON safety**: agent.py's emit sites already use only JSON-native
  values, but tool ``args`` may contain :class:`pathlib.Path` or other
  repr-friendly types. :func:`_json_safe` recursively coerces anything
  non-native to ``str`` so ``json.dumps`` in the server never chokes on
  a stray Path.

- **Never raise**: the :class:`~agentao.transport.base.Transport`
  protocol says ``emit()`` must not propagate exceptions, because
  transport failures should never crash a turn in progress. Every emit
  path is wrapped in a single top-level try/except that logs and drops.

- **Thread safety**: :meth:`AcpServer.write_notification` serializes all
  stdout writes under a single lock, so this transport can be called
  from any thread (LLM streaming worker, tool-output worker, etc.)
  without additional synchronization here.

Module layout
-------------

The class is assembled from focused mixins so each concern lives in its
own module:

- :mod:`agentao.acp._transport_helpers` — shared content-block / JSON-safety
  helpers (``_tool_kind``, ``_json_safe``, ``_text_block``, …).
- :mod:`agentao.acp._transport_replay` — :class:`_ReplayMixin`, the
  ``session/load`` history replay path.
- :mod:`agentao.acp._transport_interaction` — :class:`_InteractionMixin`,
  the blocking ``confirm_tool`` / ``ask_user`` round trips.

Names that callers historically imported from this module
(``_json_safe``, ``_tool_kind``, the ``PERMISSION_*`` constants,
``_build_permission_options``, ``_coerce_message_text``,
``_strip_system_reminder_blocks``) are re-exported here for compatibility.

Tool confirmation (Issue 08)
----------------------------

:meth:`_InteractionMixin.confirm_tool` is how the Agentao tool runner asks
the user "is this tool call OK to run?". For ACP clients the answer has to
come over the wire via a ``session/request_permission`` JSON-RPC request.
The flow is:

  1. Tool runner calls ``transport.confirm_tool(name, desc, args)`` on a
     worker thread (the one running ``agent.chat()`` — see the concurrent
     dispatcher in :class:`AcpServer`).
  2. Check the session's permission overrides; if ``allow_always`` /
     ``reject_always`` already answered for this tool, return immediately.
  3. Send ``session/request_permission`` via :meth:`AcpServer.call`, which
     returns a :class:`_PendingRequest` the worker can block on.
  4. Main read thread receives the client's response envelope, routes it
     to the pending slot, and wakes the worker.
  5. Map the outcome to a bool and (for ``*_always`` outcomes) update the
     session overrides so subsequent calls short-circuit.

Deterministic failure modes:

  - Client disconnects mid-permission → :meth:`run` cancels every pending
    request, :meth:`wait` raises :class:`PendingRequestCancelled`, we
    return ``False`` (reject the tool).
  - Client returns a JSON-RPC error → we log it and return ``False``.
  - No session context available (defensive) → return ``False``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

from agentao.transport.events import AgentEvent, EventType

from .protocol import METHOD_SESSION_UPDATE
from ._transport_helpers import (
    _json_safe,
    _text_block,
    _todo_write_plan,
    _tool_content_text,
    _tool_kind,
)
from ._transport_interaction import _InteractionMixin, _build_permission_options
from ._transport_interaction import (  # re-exported for back-compat
    PERMISSION_ALLOW_ALWAYS,
    PERMISSION_ALLOW_ONCE,
    PERMISSION_REJECT_ALWAYS,
    PERMISSION_REJECT_ONCE,
)
from ._transport_replay import _ReplayMixin
from ._transport_replay import (  # re-exported for back-compat
    _coerce_message_text,
    _strip_system_reminder_blocks,
)

if TYPE_CHECKING:
    from .server import AcpServer

logger = logging.getLogger(__name__)

# Re-exported above for callers that import these names from this module
# (tests, sibling ACP modules). Referenced here so linters keep the
# compatibility imports.
__all__ = [
    "ACPTransport",
    "PERMISSION_ALLOW_ONCE",
    "PERMISSION_REJECT_ONCE",
    "PERMISSION_ALLOW_ALWAYS",
    "PERMISSION_REJECT_ALWAYS",
    "_json_safe",
    "_tool_kind",
    "_todo_write_plan",
    "_text_block",
    "_tool_content_text",
    "_build_permission_options",
    "_coerce_message_text",
    "_strip_system_reminder_blocks",
]


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

class ACPTransport(_ReplayMixin, _InteractionMixin):
    """Adapter: Agentao runtime events → ACP ``session/update`` notifications.

    Implements the structural :class:`~agentao.transport.base.Transport`
    protocol. Bound to a specific ACP session id so the adapter can stamp
    the correct ``sessionId`` on every outgoing notification.

    History replay (``session/load``) comes from :class:`_ReplayMixin` and
    the blocking ``confirm_tool`` / ``ask_user`` round trips come from
    :class:`_InteractionMixin`.
    """

    def __init__(self, server: "AcpServer", session_id: str) -> None:
        self._server = server
        self._session_id = session_id
        from ..transport.broadcast import EventBroadcaster
        self._broadcast = EventBroadcaster()
        # call_id → deferred ACP ``plan`` update for an in-flight ``todo_write``.
        # The plan is built at TOOL_START but only emitted at TOOL_COMPLETE if
        # the call applied (status "ok"), so a denied/failed checklist update
        # never renders as if it took effect. Entries are popped on completion;
        # TOOL_START/TOOL_COMPLETE always pair, so this stays bounded.
        self._todo_plan_calls: Dict[str, Dict[str, Any]] = {}

    # -- One-way events ----------------------------------------------------

    def emit(self, event: AgentEvent) -> None:
        """Map an Agentao event to an ACP ``session/update`` notification.

        Never raises — transport failures are logged and swallowed so a
        misbehaving client or a JSON-safety slip cannot interrupt an
        in-progress turn.
        """
        try:
            update = self._build_update(event)
            if update is not None:
                # Stamp the runtime payload version (independent of ACP_PROTOCOL_VERSION).
                update["schema_version"] = event.schema_version
                self._server.write_notification(
                    METHOD_SESSION_UPDATE,
                    {"sessionId": self._session_id, "update": update},
                )
        except Exception:
            logger.exception(
                "acp: failed to emit session/update for event %s on session %s",
                event.type,
                self._session_id,
            )
        # Always notify subscribers (replay recorder, etc.) — including
        # for events the ACP wire intentionally drops (TURN_START,
        # TOOL_CONFIRMATION). Subscribers see the full runtime stream.
        self._broadcast.notify(event)

    def subscribe(self, listener):
        return self._broadcast.subscribe(listener)

    # -- Mapping -----------------------------------------------------------

    def _build_update(self, event: AgentEvent) -> Dict[str, Any] | None:
        """Return the ``update`` object for an event, or ``None`` to drop it.

        Extracted from :meth:`emit` so tests can assert on the mapping
        without going through the server's write path.
        """
        data = event.data or {}
        etype = event.type

        if etype == EventType.TURN_START:
            return None
        if etype == EventType.TOOL_CONFIRMATION:
            # Issue 08 owns tool confirmation via session/request_permission.
            return None

        if etype == EventType.LLM_TEXT:
            chunk = data.get("chunk", "")
            return {
                "sessionUpdate": "agent_message_chunk",
                "content": _text_block(str(chunk)),
            }

        if etype == EventType.THINKING:
            text = data.get("text", "")
            return {
                "sessionUpdate": "agent_thought_chunk",
                "content": _text_block(str(text)),
            }

        if etype == EventType.TOOL_START:
            tool = str(data.get("tool", "unknown"))
            call_id = str(data.get("call_id", ""))
            raw_args = data.get("args", {})
            if tool == "todo_write":
                # Surface the task checklist as a native ACP ``plan`` rather
                # than a ``tool_call`` — but DEFER it to TOOL_COMPLETE so a
                # denied (read-only mode) or failed call never renders a plan
                # as if it applied. Stash the validated plan keyed by call_id;
                # it is emitted from the TOOL_COMPLETE branch on status "ok".
                # If the todos are empty/malformed, fall through to the normal
                # tool_call mapping (which then completes normally below).
                plan = _todo_write_plan(raw_args)
                if plan is not None:
                    self._todo_plan_calls[call_id] = plan
                    return None
            return {
                "sessionUpdate": "tool_call",
                "toolCallId": call_id,
                "title": tool,
                "kind": _tool_kind(tool),
                "status": "pending",
                "rawInput": _json_safe(raw_args),
            }

        if etype == EventType.TOOL_OUTPUT:
            call_id = str(data.get("call_id", ""))
            chunk = str(data.get("chunk", ""))
            # Incremental tool output: append a content entry and mark the
            # call in_progress so ACP clients can animate spinners.
            return {
                "sessionUpdate": "tool_call_update",
                "toolCallId": call_id,
                "status": "in_progress",
                "content": [_tool_content_text(chunk)],
            }

        if etype == EventType.TOOL_COMPLETE:
            call_id = str(data.get("call_id", ""))
            if str(data.get("tool", "")) == "todo_write":
                plan = self._todo_plan_calls.pop(call_id, None)
                if plan is not None:
                    # A deferred plan: emit it only if the call actually
                    # applied. On a denied/failed/cancelled call, emit nothing
                    # — the checklist never changed and TOOL_START emitted no
                    # opening ``tool_call`` to close, so the sequence stays
                    # consistent.
                    return plan if data.get("status", "ok") == "ok" else None
                # No deferred plan for this call_id → TOOL_START emitted a real
                # ``tool_call`` (the empty/malformed fallback), so let it
                # complete normally below rather than orphan a pending call.
            status = data.get("status", "ok")
            # Agentao uses "ok" | "error" | "cancelled"; ACP uses
            # "completed" | "failed". Map conservatively — "cancelled"
            # surfaces as "failed" because ACP has no cancelled variant
            # for tool calls (only for turns via stopReason).
            acp_status = "completed" if status == "ok" else "failed"
            update: Dict[str, Any] = {
                "sessionUpdate": "tool_call_update",
                "toolCallId": call_id,
                "status": acp_status,
            }
            error = data.get("error")
            if error:
                update["content"] = [_tool_content_text(f"Error: {error}")]
            return update

        if etype == EventType.AGENT_START:
            agent_name = str(data.get("agent", "unknown"))
            task = str(data.get("task", ""))
            marker = f"[sub-agent started: {agent_name}]"
            if task:
                marker += f" {task}"
            return {
                "sessionUpdate": "agent_thought_chunk",
                "content": _text_block(marker),
            }

        if etype == EventType.AGENT_END:
            agent_name = str(data.get("agent", "unknown"))
            state = str(data.get("state", "finished"))
            turns = data.get("turns")
            marker = f"[sub-agent finished: {agent_name} ({state}"
            if turns is not None:
                marker += f", {turns} turns"
            marker += ")]"
            return {
                "sessionUpdate": "agent_thought_chunk",
                "content": _text_block(marker),
            }

        if etype == EventType.ERROR:
            message = str(data.get("message", ""))
            detail = data.get("detail")
            text = f"Error: {message}" if not detail else f"Error: {message} — {detail}"
            return {
                "sessionUpdate": "agent_message_chunk",
                "content": _text_block(text),
            }

        # Unknown event type — log but don't raise.
        logger.debug("acp: no mapping for event type %s", etype)
        return None
