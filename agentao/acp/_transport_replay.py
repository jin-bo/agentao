"""History-replay mixin for :class:`agentao.acp.transport.ACPTransport`.

Reconstructs a persisted OpenAI-format message history as ACP
``session/update`` notifications after a ``session/load`` request. Mixed into
``ACPTransport``; relies on ``self._server`` / ``self._session_id`` provided by
the host class.
"""

from __future__ import annotations

import json as _json
import logging
import re
import uuid
from typing import Any, Dict, Iterable, List

from ._transport_helpers import (
    _json_safe,
    _text_block,
    _todo_write_plan,
    _tool_content_text,
    _tool_kind,
    write_session_update,
)

logger = logging.getLogger(__name__)


# Regex used to strip ``<system-reminder>...</system-reminder>`` blocks
# from replayed user messages. The agent injects these on every turn for
# date/time, hooks, plan-mode reminders, etc. — they are an internal
# implementation detail of the runtime and should never appear in the
# replayed view shown to a freshly attached client.
_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)


def _coerce_message_text(content: Any) -> str:
    """Flatten a persisted message ``content`` field to a plain string.

    Persisted messages can carry ``content`` as one of:

    - ``str`` — return verbatim
    - ``list`` of OpenAI ``content_part`` dicts — concatenate ``text``
      fields, skip non-text parts (image / tool_use / etc.)
    - ``None`` — return empty string
    - anything else — best-effort ``str()``

    Used by :meth:`_ReplayMixin.replay_history` so a single helper
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


class _ReplayMixin:
    """``session/load`` history replay for :class:`ACPTransport`.

    All methods operate on ``self._server`` / ``self._session_id`` supplied by
    the concrete transport.
    """

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

        **``todo_write`` is special-cased to ``plan``** (mirroring the live
        event path): a persisted ``todo_write`` tool call replays as a
        native ACP ``plan`` update instead of a ``tool_call``, and its
        matching ``tool`` result is skipped (a ``plan`` has no opening
        ``tool_call`` to update). So reloading a session shows the task
        checklist as a plan panel, exactly as it rendered live. ``plan`` is
        full-replace, so replaying each ``todo_write`` in order leaves the
        client on the final checklist state. A malformed/empty persisted
        ``todo_write`` falls back to the normal ``tool_call`` rendering.

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

        # Reset the per-load set of tool_call_ids that replay as a ``plan``
        # (a session loads once, but clearing keeps a re-load self-consistent).
        self._replay_plan_call_ids.clear()

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

        # ``todo_write`` replays as a native ACP ``plan`` (mirroring the live
        # path), not a generic tool_call — so a reloaded session shows the
        # task checklist as a plan panel. Record the id so the matching tool
        # result is skipped below (a ``plan`` opens no ``tool_call`` to close).
        # A malformed/empty persisted call yields ``None`` → fall through to
        # the normal tool_call rendering.
        if tool_name == "todo_write":
            plan = _todo_write_plan(args)
            if plan is not None:
                self._emit_update(plan)
                self._replay_plan_call_ids.add(tool_call_id)
                return 1

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
        if tool_call_id in self._replay_plan_call_ids:
            # The assistant call for this id replayed as a ``plan`` — which has
            # no opening ``tool_call`` — so emitting a ``tool_call_update`` here
            # would be an orphan. Skip it.
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
            write_session_update(self._server, self._session_id, update)
        except Exception:
            logger.exception(
                "acp: failed to write replay session/update for session %s",
                self._session_id,
            )
