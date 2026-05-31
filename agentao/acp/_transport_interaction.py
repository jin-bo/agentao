"""Request/response interaction mixin for the ACP transport.

Implements the blocking ``confirm_tool`` (→ ``session/request_permission``)
and ``ask_user`` (→ ``_agentao.cn/ask_user``) round trips, plus the
``on_max_iterations`` policy. Mixed into
:class:`agentao.acp.transport.ACPTransport`; relies on ``self._server`` /
``self._session_id`` provided by the host class.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from .protocol import (
    ASK_USER_UNAVAILABLE_SENTINEL,
    METHOD_ASK_USER,
    METHOD_REQUEST_PERMISSION,
)
from ._transport_helpers import _json_safe, _tool_content_text, _tool_kind

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


class _InteractionMixin:
    """Blocking request/response interactions for :class:`ACPTransport`.

    All methods operate on ``self._server`` / ``self._session_id`` supplied by
    the concrete transport.
    """

    # -- Request-response interactions -------------------------------------

    def confirm_tool(self, tool_name: str, description: str, args: dict) -> bool:
        """Ask the ACP client to approve a tool call.

        Called by :class:`~agentao.runtime.tool_runner.ToolRunner` when a tool with
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

    def ask_user(
        self,
        question: str,
        *,
        header: Optional[str] = None,
        options: Optional[List[str]] = None,
        multiple: bool = False,
        allow_custom: bool = True,
    ) -> str:
        """Ask the ACP client for user input via ``_agentao.cn/ask_user``.

        Sends the extension method as a JSON-RPC request and blocks until
        the client responds.  All failure modes resolve to the sentinel
        string ``"(user unavailable)"`` rather than raising, so a broken
        ask_user path cannot crash a turn in progress.

        The optional structured hints (``header`` / ``options`` /
        ``multiple`` / ``allow_custom``) are forwarded on the wire; a
        client may ignore them and prompt with plain text. The reply is
        always a single ``text`` string (the client joins ``multiple``
        selections itself).

        Returns:
            The user's text answer, or the sentinel on any failure.
        """
        if self._server is None:
            logger.error(
                "acp: ask_user called with no server bound (session %s)",
                self._session_id,
            )
            return ASK_USER_UNAVAILABLE_SENTINEL

        params: Dict[str, Any] = {
            "sessionId": self._session_id,
            "question": question,
        }
        # Only include structured fields when they carry information, so a
        # plain ask_user call keeps the same minimal wire shape as before.
        if header is not None:
            params["header"] = header
        if options is not None:
            params["options"] = options
        if multiple:
            params["multiple"] = True
        if not allow_custom:
            params["allowCustom"] = False

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
