"""Projection / redaction helpers from runtime state to ``agentao.host`` models.

The host contract treats public payload shapes as the compatibility
boundary. Internal :class:`agentao.transport.events.AgentEvent` payloads,
``ToolExecutionResult`` text, raw MCP responses, raw shell stdout, and
raw policy internals must never reach a host through these models.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Literal, Optional

from pydantic import BaseModel, ValidationError

from ..runtime import identity as _identity
from .events import EventStream
from .models import (
    ActivePermissions,
    PermissionDecisionEvent,
    SubagentLifecycleEvent,
    ToolLifecycleEvent,
)


# Bumping this is a wire-form change for host UIs that pre-allocate
# display space, so it is part of the public contract.
MAX_SUMMARY_CHARS = 240

_logger = logging.getLogger(__name__)


def _truncate(text: str, *, limit: int = MAX_SUMMARY_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def redact_summary(text: Optional[str]) -> Optional[str]:
    """Project an arbitrary internal string into a host-safe summary.

    Policy: collapse whitespace + truncate. Kept deliberately narrow;
    a richer scrubber (path stripping, secret detection) belongs to a
    future PR.
    """
    if text is None:
        return None
    if not isinstance(text, str):
        text = str(text)
    flattened = " ".join(text.split())
    if not flattened:
        return None
    return _truncate(flattened)


def project_matched_rule(rule: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return a JSON-safe shallow copy of a permission rule.

    Drops compiled-regex / callable values defensively. Per-rule source
    labels are intentionally not added — that is a deferred design
    decision (see ``docs/api/host.md``).
    """
    if rule is None:
        return None
    safe: Dict[str, Any] = {}
    for key, value in rule.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, dict):
            safe[key] = {
                k: v
                for k, v in value.items()
                if isinstance(v, (str, int, float, bool, list))
                or v is None
            }
        elif isinstance(value, list):
            safe[key] = [v for v in value if isinstance(v, (str, int, float, bool))]
    return safe


def _safe_publish(stream: EventStream, event: BaseModel) -> None:
    """Publish ``event`` best-effort.

    A misbehaving consumer must not break runtime execution, so we
    swallow downstream errors. ``ValidationError`` is logged at WARNING
    so a projection bug (forgotten required field, wrong enum value)
    does not stay invisible.
    """
    try:
        stream.publish(event)  # type: ignore[arg-type]
    except ValidationError as exc:
        _logger.warning("host projection invalid event: %s", exc)
    except Exception:
        pass


_PermissionOutcome = Literal["allow", "deny", "prompt"]
_SubagentTerminalPhase = Literal["completed", "failed", "cancelled"]


class HostToolEmitter:
    """Bridge from runtime tool execution boundaries to public events.

    The runtime owns the canonical ``(session_id, turn_id, tool_call_id)``
    tuple via the agent. This emitter wraps :class:`EventStream` so the
    executor and runner can publish ``ToolLifecycleEvent`` without
    knowing the projection rules.

    All entry points are best-effort and never raise.
    """

    def __init__(
        self,
        stream: EventStream,
        session_id_provider: Callable[[], str],
        turn_id_provider: Callable[[], Optional[str]],
    ) -> None:
        self._stream = stream
        self._session_id_provider = session_id_provider
        self._turn_id_provider = turn_id_provider

    def started(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
    ) -> str:
        """Emit ``phase="started"``. Returns the canonical ``started_at``.

        The returned timestamp must thread into the matching terminal
        call so ``ToolLifecycleEvent.started_at`` matches the real start
        time, not the moment the terminal event is emitted.
        """
        started_at = _identity.utc_now_rfc3339()
        _safe_publish(self._stream, ToolLifecycleEvent(
            session_id=self._session_id_provider(),
            turn_id=self._turn_id_provider(),
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            phase="started",
            started_at=started_at,
        ))
        return started_at

    def completed(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        started_at: str,
        summary: Optional[str],
    ) -> None:
        _safe_publish(self._stream, ToolLifecycleEvent(
            session_id=self._session_id_provider(),
            turn_id=self._turn_id_provider(),
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            phase="completed",
            started_at=started_at,
            completed_at=_identity.utc_now_rfc3339(),
            outcome="ok",
            summary=redact_summary(summary),
        ))

    def failed(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        started_at: str,
        error_type: Optional[str],
        summary: Optional[str],
    ) -> None:
        _safe_publish(self._stream, ToolLifecycleEvent(
            session_id=self._session_id_provider(),
            turn_id=self._turn_id_provider(),
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            phase="failed",
            started_at=started_at,
            completed_at=_identity.utc_now_rfc3339(),
            outcome="error",
            error_type=error_type,
            summary=redact_summary(summary),
        ))

    def cancelled(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        started_at: str,
        summary: Optional[str] = None,
    ) -> None:
        """Emit ``phase="failed", outcome="cancelled", error_type=None``.

        Cancellation rides on the ``failed`` phase to keep the
        tool-call shape compact; hosts read ``outcome`` to distinguish.
        """
        _safe_publish(self._stream, ToolLifecycleEvent(
            session_id=self._session_id_provider(),
            turn_id=self._turn_id_provider(),
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            phase="failed",
            started_at=started_at,
            completed_at=_identity.utc_now_rfc3339(),
            outcome="cancelled",
            error_type=None,
            summary=redact_summary(summary),
        ))


class HostPermissionEmitter:
    """Bridge from runtime permission boundaries to public events.

    Every decision fires (including ``allow``). The contract requires
    this event to precede any ``ToolLifecycleEvent(phase="started")``
    for the same ``tool_call_id``; the runner enforces ordering.
    """

    def __init__(
        self,
        stream: EventStream,
        session_id_provider: Callable[[], str],
        turn_id_provider: Callable[[], Optional[str]],
        active_permissions_provider: Callable[[], ActivePermissions],
    ) -> None:
        self._stream = stream
        self._session_id_provider = session_id_provider
        self._turn_id_provider = turn_id_provider
        self._active_permissions_provider = active_permissions_provider

    def emit(
        self,
        *,
        tool_name: str,
        tool_call_id: Optional[str],
        decision_id: str,
        outcome: _PermissionOutcome,
        matched_rule: Optional[Dict[str, Any]],
        reason: Optional[str],
    ) -> None:
        try:
            snapshot = self._active_permissions_provider()
        except Exception:
            # If the host engine raises, fall back to a minimal source
            # list so the event still fires — silently dropping would
            # violate the "every decision fires" contract.
            snapshot = ActivePermissions(
                mode="read-only", rules=[], loaded_sources=[],
            )
        _safe_publish(self._stream, PermissionDecisionEvent(
            session_id=self._session_id_provider(),
            turn_id=self._turn_id_provider(),
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            decision_id=decision_id,
            outcome=outcome,
            mode=snapshot.mode,
            matched_rule=project_matched_rule(matched_rule),
            reason=redact_summary(reason),
            loaded_sources=snapshot.loaded_sources,
            decided_at=_identity.utc_now_rfc3339(),
        ))


class HostSubagentEmitter:
    """Bridge from sub-agent spawn/terminal points to public events.

    Each spawn returns a context dict the caller threads back into the
    matching terminal call so ``started_at``, ``child_task_id``, and
    optional ``child_session_id`` line up.
    """

    def __init__(
        self,
        stream: EventStream,
        parent_session_id_provider: Callable[[], str],
    ) -> None:
        self._stream = stream
        self._parent_session_id_provider = parent_session_id_provider

    def spawned(
        self,
        *,
        task_summary: Optional[str],
        parent_task_id: Optional[str] = None,
        child_session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        child_task_id = _identity.new_child_task_id()
        started_at = _identity.utc_now_rfc3339()
        # Pin the parent session at spawn time and thread it through
        # the returned ctx so the matching terminal event uses the
        # same value — background sub-agents can outlive an ACP
        # ``session/load`` or ``session/new`` that rebinds
        # ``agent._session_id``, and re-reading the provider then
        # would publish ``completed``/``failed`` under a different
        # ``parent_session_id`` than ``spawned``, breaking
        # session-filtered hosts and event correlation.
        parent_session_id = self._parent_session_id_provider()
        # Wire ``session_id`` is the child's session when known so a
        # host that filters by session sees the child's events
        # together; otherwise it falls back to the parent.
        session_id = child_session_id or parent_session_id
        _safe_publish(self._stream, SubagentLifecycleEvent(
            session_id=session_id,
            parent_session_id=parent_session_id,
            parent_task_id=parent_task_id,
            child_session_id=child_session_id,
            child_task_id=child_task_id,
            phase="spawned",
            task_summary=redact_summary(task_summary),
            started_at=started_at,
        ))
        return {
            "child_task_id": child_task_id,
            "started_at": started_at,
            "child_session_id": child_session_id,
            "parent_task_id": parent_task_id,
            "parent_session_id": parent_session_id,
        }

    def completed(self, *, ctx: Dict[str, Any], task_summary: Optional[str]) -> None:
        self._publish_terminal(ctx, "completed", task_summary, error_type=None)

    def failed(
        self,
        *,
        ctx: Dict[str, Any],
        task_summary: Optional[str],
        error_type: Optional[str],
    ) -> None:
        self._publish_terminal(ctx, "failed", task_summary, error_type=error_type)

    def cancelled(self, *, ctx: Dict[str, Any], task_summary: Optional[str] = None) -> None:
        self._publish_terminal(ctx, "cancelled", task_summary, error_type=None)

    def _publish_terminal(
        self,
        ctx: Dict[str, Any],
        phase: _SubagentTerminalPhase,
        task_summary: Optional[str],
        *,
        error_type: Optional[str],
    ) -> None:
        child_session_id = ctx.get("child_session_id")
        # Reuse the spawn-time parent session id from ctx so the
        # terminal event correlates with the original ``spawned`` even
        # when the host has rebound ``agent._session_id`` in between
        # (e.g. ACP ``session/load`` finishing while a background
        # sub-agent is still running). Older ctx dicts without the
        # field fall back to the provider for compatibility.
        parent_session_id = (
            ctx.get("parent_session_id")
            or self._parent_session_id_provider()
        )
        session_id = child_session_id or parent_session_id
        _safe_publish(self._stream, SubagentLifecycleEvent(
            session_id=session_id,
            parent_session_id=parent_session_id,
            parent_task_id=ctx.get("parent_task_id"),
            child_session_id=child_session_id,
            child_task_id=ctx["child_task_id"],
            phase=phase,
            task_summary=redact_summary(task_summary),
            started_at=ctx["started_at"],
            completed_at=_identity.utc_now_rfc3339(),
            error_type=error_type,
        ))


__all__ = [
    "HostPermissionEmitter",
    "HostSubagentEmitter",
    "HostToolEmitter",
    "MAX_SUMMARY_CHARS",
    "project_matched_rule",
    "redact_summary",
]
