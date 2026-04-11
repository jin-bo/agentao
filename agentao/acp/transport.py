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

Tool confirmation (Issue 08)
----------------------------

:meth:`ACPTransport.confirm_tool` is how the Agentao tool runner asks the
user "is this tool call OK to run?". For ACP clients the answer has to
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

import json as _json
import logging
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional

from agentao.transport.events import AgentEvent, EventType

from .protocol import (
    ASK_USER_UNAVAILABLE_SENTINEL,
    METHOD_ASK_USER,
    METHOD_REQUEST_PERMISSION,
    METHOD_SESSION_UPDATE,
)


# Regex used to strip ``<system-reminder>...</system-reminder>`` blocks
# from replayed user messages. The agent injects these on every turn for
# date/time, hooks, plan-mode reminders, etc. — they are an internal
# implementation detail of the runtime and should never appear in the
# replayed view shown to a freshly attached client.
_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)

if TYPE_CHECKING:
    from .server import AcpServer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ACP permission option kinds (closed enum from the ACP spec)
# ---------------------------------------------------------------------------

PERMISSION_ALLOW_ONCE = "allow_once"
PERMISSION_REJECT_ONCE = "reject_once"
PERMISSION_ALLOW_ALWAYS = "allow_always"
PERMISSION_REJECT_ALWAYS = "reject_always"

_OUTCOME_SELECTED = "selected"
_OUTCOME_CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Tool name → ACP tool-call kind
# ---------------------------------------------------------------------------

_TOOL_KIND_MAP: Dict[str, str] = {
    # read-only file operations
    "read_file": "read",
    "read_folder": "read",
    "list_directory": "read",
    # mutations
    "write_file": "edit",
    "edit_file": "edit",
    "edit": "edit",
    # search
    "find_files": "search",
    "search_text": "search",
    "grep": "search",
    "glob": "search",
    "google_web_search": "search",
    # execution
    "run_shell_command": "execute",
    "bash": "execute",
    "shell": "execute",
    # network
    "web_fetch": "fetch",
    # everything else (skills, memory, ask_user, sub-agents, MCP tools) → "other"
}


def _tool_kind(tool_name: str) -> str:
    """Map an Agentao tool name to an ACP ``tool_call.kind`` enum value.

    Unknown tools (including all ``mcp_*`` tools) fall back to ``"other"``.
    """
    return _TOOL_KIND_MAP.get(tool_name, "other")


# ---------------------------------------------------------------------------
# JSON safety coercion
# ---------------------------------------------------------------------------

def _json_safe(value: Any) -> Any:
    """Recursively coerce a value into a JSON-serializable form.

    Handles the common offenders we expect to see in tool ``args``:
    :class:`pathlib.Path`, sets, tuples, and arbitrary objects. Anything
    already JSON-native passes through unchanged.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(v) for v in value]
    return str(value)


# ---------------------------------------------------------------------------
# Content block helpers
# ---------------------------------------------------------------------------

def _text_block(text: str) -> Dict[str, Any]:
    """Build an ACP ``ContentBlock`` wrapping plain text."""
    return {"type": "text", "text": text}


def _tool_content_text(text: str) -> Dict[str, Any]:
    """Build a ``ToolCallContent`` entry that wraps plain text.

    Per ACP spec, ``tool_call.content`` is an array of
    ``{type: "content", content: ContentBlock}`` entries (plus diff and
    terminal variants we do not use in v1).
    """
    return {"type": "content", "content": _text_block(text)}


def _coerce_message_text(content: Any) -> str:
    """Flatten a persisted message ``content`` field to a plain string.

    Persisted messages can carry ``content`` as one of:

    - ``str`` — return verbatim
    - ``list`` of OpenAI ``content_part`` dicts — concatenate ``text``
      fields, skip non-text parts (image / tool_use / etc.)
    - ``None`` — return empty string
    - anything else — best-effort ``str()``

    Used by :meth:`ACPTransport.replay_history` so a single helper
    handles all the shapes the runtime might persist.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for entry in content:
            if isinstance(entry, dict) and isinstance(entry.get("text"), str):
                parts.append(entry["text"])
        return "".join(parts)
    return str(content)


def _strip_system_reminder_blocks(text: str) -> str:
    """Remove ``<system-reminder>...</system-reminder>`` blocks from a string.

    The runtime injects these into every persisted user message
    (date/time, plan-mode hints, hooks, etc.). They are an internal
    implementation detail of the agent loop and would confuse a client
    browsing replayed history. ``re.DOTALL`` so a multi-line reminder
    block is matched as a unit.
    """
    if not text:
        return text
    return _SYSTEM_REMINDER_RE.sub("", text)


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

class ACPTransport:
    """Adapter: Agentao runtime events → ACP ``session/update`` notifications.

    Implements the structural :class:`~agentao.transport.base.Transport`
    protocol. Bound to a specific ACP session id so the adapter can stamp
    the correct ``sessionId`` on every outgoing notification.
    """

    def __init__(self, server: "AcpServer", session_id: str) -> None:
        self._server = server
        self._session_id = session_id

    # -- One-way events ----------------------------------------------------

    def emit(self, event: AgentEvent) -> None:
        """Map an Agentao event to an ACP ``session/update`` notification.

        Never raises — transport failures are logged and swallowed so a
        misbehaving client or a JSON-safety slip cannot interrupt an
        in-progress turn.
        """
        try:
            update = self._build_update(event)
            if update is None:
                return  # silent event (e.g. TURN_START)
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

    # -- History replay (Issue 10) -----------------------------------------

    def replay_history(self, messages: Iterable[Dict[str, Any]]) -> int:
        """Replay a persisted message history as ACP ``session/update`` events.

        Used by :func:`agentao.acp.session_load.handle_session_load` to
        reconstruct a previous conversation on the client side after a
        ``session/load`` request. Walks the persisted OpenAI-format
        message list and emits one (or more) notifications per message:

        ===================  ============================================
        Persisted role       ACP ``session/update.update.sessionUpdate``
        ===================  ============================================
        ``system``           *(skipped — rebuilt by Agentao at runtime)*
        ``user``             ``user_message_chunk`` with text content
        ``assistant`` text   ``agent_message_chunk`` with text content
        ``assistant``        ``tool_call`` (status="completed", title=
        ``tool_calls``         tool_name, kind=mapped, rawInput=parsed)
        ``tool``             ``tool_call_update`` (status="completed",
                               content=text result)
        ===================  ============================================

        Tool calls in the persisted assistant message are emitted as
        ``tool_call`` rather than ``tool_call`` + ``tool_call_update``
        because the historical state is fully resolved — there is no
        "pending" period to animate. The matching ``tool`` message
        produces a ``tool_call_update`` carrying the recorded result so
        clients that key on ``toolCallId`` can attach the output to the
        right call.

        Returns the number of notifications written. Never raises:
        a single malformed historical entry is logged and skipped so a
        bad checkpoint cannot break a load.

        ``user`` content runs through :func:`_strip_system_reminder_blocks`
        because the runtime injects ``<system-reminder>...</system-reminder>``
        blocks (date/time, plan mode, etc.) into every persisted user
        message. They are an internal implementation detail and would
        confuse a client browsing the replayed history.
        """
        if self._server is None:
            logger.error(
                "acp: replay_history called with no server bound (session %s)",
                self._session_id,
            )
            return 0

        emitted = 0
        for index, raw in enumerate(messages):
            if not isinstance(raw, dict):
                logger.warning(
                    "acp: replay_history skipping non-dict entry at index %d", index
                )
                continue
            try:
                emitted += self._replay_one(raw)
            except Exception:
                logger.exception(
                    "acp: replay_history failed on entry %d (role=%r) — skipping",
                    index,
                    raw.get("role"),
                )
                continue
        return emitted

    def _replay_one(self, msg: Dict[str, Any]) -> int:
        """Emit notifications for a single persisted message.

        Returns the count emitted (0 for skipped roles like ``system``).
        """
        role = msg.get("role")

        # System messages are *not* persisted user data — they are the
        # rendered system prompt, rebuilt fresh on every chat() call.
        # Replaying one would inject stale skill / memory / date context
        # into the client's view, which is misleading.
        if role == "system":
            return 0

        if role == "user":
            content = msg.get("content", "")
            text = _coerce_message_text(content)
            text = _strip_system_reminder_blocks(text).strip()
            if not text:
                return 0
            self._emit_update(
                {
                    "sessionUpdate": "user_message_chunk",
                    "content": _text_block(text),
                }
            )
            return 1

        if role == "assistant":
            count = 0
            content = msg.get("content", "")
            text = _coerce_message_text(content)
            if text:
                self._emit_update(
                    {
                        "sessionUpdate": "agent_message_chunk",
                        "content": _text_block(text),
                    }
                )
                count += 1

            tool_calls = msg.get("tool_calls") or []
            for tc in tool_calls:
                count += self._replay_assistant_tool_call(tc)
            return count

        if role == "tool":
            return self._replay_tool_result(msg)

        # Anything else (function, developer, ...) is not part of the
        # OpenAI shapes Agentao actually persists. Log and skip.
        logger.debug("acp: replay_history skipping unknown role %r", role)
        return 0

    def _replay_assistant_tool_call(self, tc: Any) -> int:
        """Emit a ``tool_call`` (status="completed") for a persisted tool call.

        Persisted shapes vary across LLM providers — the OpenAI-style
        dict is the canonical one Agentao writes, but we accept anything
        with a ``function`` field for resilience.
        """
        if not isinstance(tc, dict):
            return 0
        function = tc.get("function") or {}
        if not isinstance(function, dict):
            return 0
        tool_name = str(function.get("name") or "unknown")
        args_raw = function.get("arguments", "{}")
        # arguments is typically a JSON-encoded string. Parse so we can
        # surface real structured ``rawInput`` to the client; fall back
        # to the raw string if it's not valid JSON.
        try:
            args = _json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            if not isinstance(args, dict):
                args = {"_value": args}
        except (ValueError, TypeError):
            args = {"_raw": str(args_raw)}

        tool_call_id = str(
            tc.get("id") or f"replay_{uuid.uuid4().hex[:12]}"
        )
        self._emit_update(
            {
                "sessionUpdate": "tool_call",
                "toolCallId": tool_call_id,
                "title": tool_name,
                "kind": _tool_kind(tool_name),
                # Replayed tool calls are by definition complete — there
                # is no live execution to animate, and the matching
                # ``tool`` message will follow with the result.
                "status": "completed",
                "rawInput": _json_safe(args),
            }
        )
        return 1

    def _replay_tool_result(self, msg: Dict[str, Any]) -> int:
        """Emit a ``tool_call_update`` carrying a persisted tool's result text."""
        tool_call_id = str(msg.get("tool_call_id") or "")
        if not tool_call_id:
            logger.debug(
                "acp: replay_history skipping tool message with no tool_call_id"
            )
            return 0
        text = _coerce_message_text(msg.get("content", ""))
        update: Dict[str, Any] = {
            "sessionUpdate": "tool_call_update",
            "toolCallId": tool_call_id,
            "status": "completed",
        }
        if text:
            update["content"] = [_tool_content_text(text)]
        self._emit_update(update)
        return 1

    def _emit_update(self, update: Dict[str, Any]) -> None:
        """Helper: write a single ``session/update`` notification.

        Wraps :meth:`AcpServer.write_notification` so the replay path
        and the live event path share identical envelope construction.
        Errors are logged and swallowed — replay must be best-effort.
        """
        try:
            self._server.write_notification(
                METHOD_SESSION_UPDATE,
                {"sessionId": self._session_id, "update": update},
            )
        except Exception:
            logger.exception(
                "acp: failed to write replay session/update for session %s",
                self._session_id,
            )

    # -- Request-response interactions -------------------------------------

    def confirm_tool(self, tool_name: str, description: str, args: dict) -> bool:
        """Ask the ACP client to approve a tool call.

        Called by :class:`~agentao.tool_runner.ToolRunner` when a tool with
        ``requires_confirmation=True`` is about to execute. Returns ``True``
        if the client approved, ``False`` if they rejected or the
        connection failed while we were waiting.

        Behavior:

          1. **Session overrides.** If the client previously answered
             ``allow_always`` / ``reject_always`` for this ``tool_name``,
             return the remembered decision without any network round trip.
             This is how Issue 08's "optional allow_session" requirement is
             satisfied.
          2. **Build a toolCall payload** that mirrors Issue 07's
             ``tool_call`` session/update shape — ACP clients can share
             rendering between the two.
          3. **Send** ``session/request_permission`` via
             :meth:`AcpServer.call` and **block** the caller until the
             client responds, the request is cancelled (shutdown), or a
             hard failure occurs.
          4. **Map the outcome.** ``allow_once`` / ``allow_always`` → True;
             ``reject_once`` / ``reject_always`` / ``cancelled`` → False.
             ``*_always`` outcomes also update
             :attr:`AcpSessionState.permission_overrides`.

        This method is *defensively* robust: every failure mode
        (missing session, missing server, wait-cancelled, JSON-RPC
        error response from the client, malformed outcome) resolves to
        ``False`` rather than raising, because a crashing confirmation
        path would propagate up through :meth:`chat` and crash the turn
        with an unhelpful traceback.
        """
        # Late import: Issue 04's session_new constructs the transport
        # before the session is registered, so an import-time binding
        # would create a cycle. Resolved lazily on first call.
        from .session_manager import SessionNotFoundError

        if self._server is None:
            logger.error(
                "acp: confirm_tool called with no server bound (session %s, tool %s)",
                self._session_id,
                tool_name,
            )
            return False

        try:
            session = self._server.sessions.require(self._session_id)
        except SessionNotFoundError:
            logger.error(
                "acp: confirm_tool — session %s not found (tool %s)",
                self._session_id,
                tool_name,
            )
            return False
        except Exception:
            logger.exception(
                "acp: confirm_tool — unexpected error looking up session %s",
                self._session_id,
            )
            return False

        # 1) Fast path: session override already decided this tool.
        with session.permission_lock:
            if tool_name in session.permission_overrides:
                decided = session.permission_overrides[tool_name]
                logger.debug(
                    "acp: confirm_tool short-circuit for %s → %s (session override)",
                    tool_name,
                    "allow" if decided else "reject",
                )
                return decided

        # 2) Build the request payload.
        tool_call_id = f"call_{uuid.uuid4().hex[:12]}"
        tool_call_payload: Dict[str, Any] = {
            "toolCallId": tool_call_id,
            "title": tool_name,
            "kind": _tool_kind(tool_name),
            "status": "pending",
            "rawInput": _json_safe(args),
        }
        # Tool description becomes a single content entry so ACP clients
        # that render a confirmation dialog can show what the tool does.
        if description:
            tool_call_payload["content"] = [_tool_content_text(description)]

        options: List[Dict[str, str]] = _build_permission_options()
        params: Dict[str, Any] = {
            "sessionId": self._session_id,
            "toolCall": tool_call_payload,
            "options": options,
        }

        # 3) Send and wait.
        try:
            pending = self._server.call(METHOD_REQUEST_PERMISSION, params)
        except Exception:
            logger.exception(
                "acp: confirm_tool — failed to send request_permission for %s",
                tool_name,
            )
            return False

        # Import here to avoid a cycle: server.py imports transport.py via
        # ACPTransport constructor usage in session_new, so we keep the
        # exception types out of transport.py's module-level namespace.
        from .server import JsonRpcHandlerError, PendingRequestCancelled

        try:
            result = pending.wait()
        except PendingRequestCancelled:
            logger.info(
                "acp: confirm_tool — permission request cancelled for %s "
                "(connection closed or explicit cancel); rejecting tool",
                tool_name,
            )
            return False
        except JsonRpcHandlerError as e:
            logger.error(
                "acp: confirm_tool — client returned error %d for %s: %s",
                e.code,
                tool_name,
                e.message,
            )
            return False
        except Exception:
            logger.exception(
                "acp: confirm_tool — unexpected error waiting for %s permission",
                tool_name,
            )
            return False

        # 4) Map outcome → bool.
        return self._apply_permission_outcome(session, tool_name, result)

    def _apply_permission_outcome(
        self,
        session: Any,
        tool_name: str,
        raw_result: Any,
    ) -> bool:
        """Translate an ACP ``RequestPermissionResponse`` into a bool.

        Spec: the result object has ``outcome`` ∈ {``selected``,
        ``cancelled``}. Selected carries an ``optionId`` matching one of
        the options we sent. Unrecognized shapes resolve to ``False`` —
        we would rather reject a tool than silently allow it on a
        malformed response.
        """
        if not isinstance(raw_result, dict):
            logger.warning(
                "acp: request_permission for %s returned non-object result: %r",
                tool_name,
                raw_result,
            )
            return False

        outcome_obj = raw_result.get("outcome")
        # ACP spec wraps the outcome in ``{"outcome": {...}}`` where the
        # inner object has an ``outcome`` discriminator. Some clients
        # flatten it to ``{"outcome": "selected", "optionId": ...}`` —
        # handle both shapes.
        if isinstance(outcome_obj, dict):
            kind = outcome_obj.get("outcome")
            option_id = outcome_obj.get("optionId")
        else:
            kind = outcome_obj
            option_id = raw_result.get("optionId")

        if kind == _OUTCOME_CANCELLED:
            logger.info(
                "acp: permission cancelled by client for %s — rejecting tool",
                tool_name,
            )
            return False

        if kind != _OUTCOME_SELECTED:
            logger.warning(
                "acp: request_permission for %s returned unknown outcome %r",
                tool_name,
                kind,
            )
            return False

        if option_id == PERMISSION_ALLOW_ONCE:
            return True
        if option_id == PERMISSION_REJECT_ONCE:
            return False
        if option_id == PERMISSION_ALLOW_ALWAYS:
            with session.permission_lock:
                session.permission_overrides[tool_name] = True
            logger.info(
                "acp: %s granted allow_always for session %s",
                tool_name,
                self._session_id,
            )
            return True
        if option_id == PERMISSION_REJECT_ALWAYS:
            with session.permission_lock:
                session.permission_overrides[tool_name] = False
            logger.info(
                "acp: %s denied reject_always for session %s",
                tool_name,
                self._session_id,
            )
            return False

        logger.warning(
            "acp: request_permission for %s returned unknown optionId %r",
            tool_name,
            option_id,
        )
        return False

    def ask_user(self, question: str) -> str:
        """Ask the ACP client for free-form user input via ``_agentao.cn/ask_user``.

        Sends the extension method as a JSON-RPC request and blocks until
        the client responds.  All failure modes resolve to the sentinel
        string ``"(user unavailable)"`` rather than raising, so a broken
        ask_user path cannot crash a turn in progress.

        Returns:
            The user's text answer, or the sentinel on any failure.
        """
        if self._server is None:
            logger.error(
                "acp: ask_user called with no server bound (session %s)",
                self._session_id,
            )
            return ASK_USER_UNAVAILABLE_SENTINEL

        params = {
            "sessionId": self._session_id,
            "question": question,
        }

        try:
            pending = self._server.call(METHOD_ASK_USER, params)
        except Exception:
            logger.exception(
                "acp: ask_user — failed to send %s", METHOD_ASK_USER
            )
            return ASK_USER_UNAVAILABLE_SENTINEL

        from .server import PendingRequestCancelled, JsonRpcHandlerError

        try:
            result = pending.wait()
        except PendingRequestCancelled:
            logger.info(
                "acp: ask_user — request cancelled (connection closed)"
            )
            return ASK_USER_UNAVAILABLE_SENTINEL
        except JsonRpcHandlerError as e:
            logger.error(
                "acp: ask_user — client returned error %d: %s",
                e.code, e.message,
            )
            return ASK_USER_UNAVAILABLE_SENTINEL
        except Exception:
            logger.exception("acp: ask_user — unexpected error")
            return ASK_USER_UNAVAILABLE_SENTINEL

        if not isinstance(result, dict):
            logger.warning(
                "acp: ask_user — non-object result: %r", result
            )
            return ASK_USER_UNAVAILABLE_SENTINEL

        outcome = result.get("outcome", "")
        if outcome == "answered":
            text = result.get("text", "")
            return text if text else ASK_USER_UNAVAILABLE_SENTINEL
        if outcome == "cancelled":
            return ASK_USER_UNAVAILABLE_SENTINEL

        logger.warning(
            "acp: ask_user — unknown outcome %r", outcome
        )
        return ASK_USER_UNAVAILABLE_SENTINEL

    def on_max_iterations(self, count: int, messages: list) -> dict:
        """Conservative default: stop the turn when max iterations is reached.

        ACP mode has no interactive menu, so the safe default is to stop.
        """
        logger.info(
            "acp: max iterations (%d) reached on session %s — stopping",
            count,
            self._session_id,
        )
        return {"action": "stop"}


# ---------------------------------------------------------------------------
# Permission option builder
# ---------------------------------------------------------------------------

def _build_permission_options() -> List[Dict[str, str]]:
    """Return the standard ACP permission options for a tool confirmation.

    Agentao offers all four ACP option kinds so clients can present a
    rich confirmation dialog. ``optionId`` deliberately equals the
    ``kind`` — clients that echo the id back in the outcome give us a
    unambiguous mapping with no extra lookups.
    """
    return [
        {"optionId": PERMISSION_ALLOW_ONCE, "name": "Allow once", "kind": PERMISSION_ALLOW_ONCE},
        {"optionId": PERMISSION_ALLOW_ALWAYS, "name": "Always allow", "kind": PERMISSION_ALLOW_ALWAYS},
        {"optionId": PERMISSION_REJECT_ONCE, "name": "Reject once", "kind": PERMISSION_REJECT_ONCE},
        {"optionId": PERMISSION_REJECT_ALWAYS, "name": "Always reject", "kind": PERMISSION_REJECT_ALWAYS},
    ]
