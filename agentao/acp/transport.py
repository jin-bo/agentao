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
``TOOL_START``         ``tool_call`` (toolCallId, title, kind, status="pending",
                       rawInput, plus a ``diff`` content entry for a file edit)
``TOOL_OUTPUT``        ``tool_call_update`` (status="in_progress", content restated whole)
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
  ``think``, ``fetch``, ``switch_mode``, ``other``). :func:`_tool_kind` maps
  Agentao tool names to those values; unknown tools — host-injected and all
  ``mcp_*`` ones — fall back to ``"other"``, which is the value ACP v1 itself
  makes the default. The agentao half of the table is exhaustive over
  ``BUILTIN_TOOL_NAMES`` by test.

- **Tool call content is a collection, not a stream.** ACP replaces it on
  every update, so each update restates everything accumulated so far; see
  :mod:`agentao.acp._tool_call_content` for the buffer, its two bounds and
  the lock it needs because a shell command streams from two reader threads.

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
from collections import deque
from typing import TYPE_CHECKING, Any, Deque, Dict, List, Set

from agentao.transport.events import AgentEvent, EventType

from ._tool_call_content import ToolCallContentBuffer
from ._transport_helpers import (
    _json_safe,
    _text_block,
    hook_notice_update,
    _todo_write_plan,
    _tool_content_text,
    _tool_kind,
    proposed_tool_diff,
    write_session_update,
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

#: How many finished ``call_id``\s to remember, so a chunk that arrives after
#: its tool call ended is dropped rather than re-opening the call. Comfortably
#: above any one batch's tool-call count; the membership test is a scan, and it
#: runs once per streamed chunk.
_CLOSED_CALL_MEMORY = 64

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
        # tool_call_ids whose persisted ``todo_write`` replayed as a ``plan``
        # during ``session/load`` (see _ReplayMixin), so the matching tool
        # result is skipped — a ``plan`` has no opening ``tool_call`` to close.
        self._replay_plan_call_ids: Set[str] = set()
        # tool_call_id → the ``diff`` a replayed file edit opened with, held
        # until its result is replayed so that update can restate it (ACP
        # replaces the content collection). Cleared per load.
        self._replay_diffs: Dict[str, Dict[str, Any]] = {}
        # call_id → the ACP ``content`` collection accumulated for an
        # in-flight tool call. ACP replaces the collection on every update
        # rather than extending it, so each update has to restate the whole
        # thing; see :mod:`agentao.acp._tool_call_content`. Created lazily on
        # the first ``TOOL_OUTPUT`` and popped at ``TOOL_COMPLETE``, so a call
        # that streams nothing costs nothing.
        self._tool_call_content: Dict[str, ToolCallContentBuffer] = {}
        # call_ids whose ``TOOL_COMPLETE`` has already been seen. A streamed
        # chunk that arrives *after* a call is over must not re-create its
        # buffer: the shell executor joins its two reader threads with a
        # bounded timeout (``capabilities/shell.py``), so a reader still
        # holding a killed grandchild's pipe can deliver a chunk after the
        # tool returned. Without this, that chunk both re-opens a call the
        # client already saw ``completed`` and leaves behind a buffer nothing
        # will ever pop. Bounded ring — and a ``TOOL_START`` that reuses an id
        # clears it, because the sub-agent path falls back to the tool *name*
        # as the call_id, so ids genuinely do repeat.
        self._closed_tool_calls: Deque[str] = deque(maxlen=_CLOSED_CALL_MEMORY)

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
                write_session_update(self._server, self._session_id, update)
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

    def _buffer_for(self, call_id: str) -> ToolCallContentBuffer:
        """This call's content buffer, created on first use.

        The miss path goes through ``setdefault`` rather than a plain
        assignment because two reader threads can deliver the first chunk of
        one command at once (see :mod:`._tool_call_content`) — ``setdefault``
        is atomic, so the loser gets the winner's buffer instead of silently
        replacing it. The ``get`` in front of it is what keeps a 125-chunk
        build log from allocating 125 throwaway buffers.
        """
        buffer = self._tool_call_content.get(call_id)
        if buffer is None:
            buffer = self._tool_call_content.setdefault(
                call_id, ToolCallContentBuffer()
            )
        return buffer

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
            # A new call under a reused id (the sub-agent path falls back to
            # the tool name) is live again, whatever the previous one did.
            while call_id in self._closed_tool_calls:
                self._closed_tool_calls.remove(call_id)
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
            update = {
                "sessionUpdate": "tool_call",
                "toolCallId": call_id,
                "title": tool,
                "kind": _tool_kind(tool),
                "status": "pending",
                "rawInput": _json_safe(raw_args),
            }
            # A file-editing call opens with the edit it proposes, so a client
            # renders a reviewable diff instead of a "Successfully wrote to …"
            # line after the fact. Pinned as the buffer's leading entry: ACP
            # replaces the content collection, so a later streamed update has
            # to restate the diff or it would drop it.
            diff = proposed_tool_diff(self._server, self._session_id, tool, raw_args)
            if diff is not None:
                buffer = self._buffer_for(call_id)
                buffer.add_leading(diff)
                update["content"] = buffer.entries()
                buffer.mark_sent()
            return update

        if etype == EventType.TOOL_OUTPUT:
            call_id = str(data.get("call_id", ""))
            chunk = str(data.get("chunk", ""))
            # Streamed tool output. ACP *replaces* a tool call's content
            # collection on every update rather than extending it, so the
            # update restates everything accumulated so far — sending the
            # bare chunk left a conformant client showing only the latest
            # one. The buffer also throttles: a chunk that does not earn an
            # update is still recorded and rides the next one.
            if call_id in self._closed_tool_calls:
                # A straggler from a reader thread the shell executor stopped
                # waiting on. Re-opening a call the client already saw
                # ``completed`` is worse than dropping the tail of its output,
                # and the model's copy of the result is unaffected either way.
                return None
            buffer = self._buffer_for(call_id)
            if not buffer.append(chunk):
                return None
            update: Dict[str, Any] = {
                "sessionUpdate": "tool_call_update",
                "toolCallId": call_id,
                "status": "in_progress",
            }
            entries = buffer.entries()
            if entries:
                # An empty collection would *clear* the client's copy, so a
                # first chunk that carries no text sends the status alone.
                update["content"] = entries
            return update

        if etype == EventType.TOOL_COMPLETE:
            call_id = str(data.get("call_id", ""))
            # Pop before the ``todo_write`` branch returns, so no path can
            # leave a buffer behind for a call that is over — and remember the
            # id, so a late chunk cannot put one back.
            buffer = self._tool_call_content.pop(call_id, None)
            self._closed_tool_calls.append(call_id)
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
            update = {
                "sessionUpdate": "tool_call_update",
                "toolCallId": call_id,
                "status": acp_status,
            }
            error = data.get("error")
            # The collection is restated when it would change (output the
            # throttle held back, an error line to add) or when the call
            # streamed at all. A call that opened with a diff and streamed
            # nothing omits ``content``, which leaves the opening copy
            # standing — that is what replace semantics mean. Note the error rides *beside* the output it explains;
            # sending it alone used to erase every chunk the tool produced
            # before it failed, which is the output that says why.
            # A call that streamed is always restated, dirty or not: its
            # mid-stream snapshots are written outside the buffer's lock, so
            # two reader threads can deliver them out of order and leave the
            # client holding the older one (see ``ToolCallContentBuffer.
            # streamed``). The terminal update is the one that is ordered
            # after all of them.
            entries: List[Dict[str, Any]] = []
            if buffer is not None and (error or buffer.dirty or buffer.streamed):
                entries = buffer.entries()
            if error:
                entries.append(_tool_content_text(f"Error: {error}"))
            if entries:
                update["content"] = entries
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

        if etype == EventType.PLUGIN_HOOK_FIRED:
            # The user-bound half of a hook's output. The lifecycle events
            # deliver theirs directly (``_lifecycle.py``) because they fire
            # outside any turn; every other hook event is dispatched where
            # there is no client to write to — a chat loop, a tool worker, the
            # compaction coordinator — and this is its only route out.
            # ``None`` for the rest of the payload: counts and verdicts are
            # replay's business, not the client's.
            return hook_notice_update(data.get("user_notices"))

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
