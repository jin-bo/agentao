"""Translate :class:`HostEvent` payloads into replay JSONL events.

The ``agentao.host`` contract gives hosts a typed event stream
(``Agentao.events()``); the replay subsystem gives them a JSONL audit
trail. Without a bridge they are two parallel streams over almost the
same facts. This module is the bridge.

A :class:`HostReplaySink` takes a :class:`ReplayRecorder` and a
:class:`EventStream` and either:

- registers as an *observer* on the stream so every published host
  event also lands in the replay file (push model), or
- accepts ad-hoc :class:`HostEvent` payloads and writes them
  directly (used by tests and by sites that already hold the event
  before publishing).

The kind name in the JSONL file is the public ``event_type`` from the
Pydantic model (``tool_lifecycle`` / ``subagent_lifecycle`` /
``permission_decision``) — the same string the v1.2 replay schema
matches in its ``oneOf`` discriminator. The payload is the model's
``model_dump(mode="json")`` so the JSONL bytes match the schema
exactly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel

from ..replay.events import EventKind
from .models import (
    PermissionDecisionEvent,
    SubagentLifecycleEvent,
    ToolLifecycleEvent,
)

if TYPE_CHECKING:
    from ..replay.recorder import ReplayRecorder
    from .events import EventStream  # noqa: F401
    from .models import HostEvent  # noqa: F401


_logger = logging.getLogger(__name__)


# Mapping from concrete public model class → replay event kind. Keeps
# the dispatch local — the replay-event-1.2 schema's ``oneOf`` is the
# downstream of this same mapping.
_MODEL_TO_KIND = {
    ToolLifecycleEvent: EventKind.TOOL_LIFECYCLE,
    SubagentLifecycleEvent: EventKind.SUBAGENT_LIFECYCLE,
    PermissionDecisionEvent: EventKind.PERMISSION_DECISION,
}


def host_event_to_replay_kind(event: BaseModel) -> Optional[str]:
    """Return the replay-event ``kind`` for a public ``agentao.host`` event.

    Returns ``None`` for any model not on the v1.2 surface — sinks
    treat that as "not a replay-projectable event" and drop silently.
    """
    return _MODEL_TO_KIND.get(type(event))


def host_event_to_replay_payload(event: BaseModel) -> dict[str, Any]:
    """Return the replay-payload JSON dict for a public ``agentao.host`` event.

    The shape is the model's ``model_dump(mode="json")`` output: every
    field, including ``event_type`` and ``None`` values for fields the
    contract documents as ``Optional``. The v1.2 replay schema's
    per-kind variant validates this exact shape.
    """
    return event.model_dump(mode="json")


class HostReplaySink:
    """Best-effort bridge from :class:`EventStream` to :class:`ReplayRecorder`.

    Construction options:

    - ``recorder=None`` makes :meth:`record` a silent no-op, so callers
      can keep the sink in their wiring before a replay is actually
      started.
    - Pass ``stream=`` to auto-register as a synchronous observer on
      that stream so every published host event is also recorded;
      :meth:`detach` removes the registration. Without ``stream`` the
      sink is in pull mode and only writes when callers invoke
      :meth:`record` directly.

    Errors during ``recorder.record()`` are logged at WARNING and
    swallowed — the host contract requires the runtime to keep running
    even when audit storage is broken.
    """

    def __init__(
        self,
        recorder: Optional["ReplayRecorder"],
        *,
        stream: Optional["EventStream"] = None,
    ) -> None:
        self._recorder = recorder
        self._stream: Optional["EventStream"] = None
        self._observer: Optional[Any] = None
        if stream is not None:
            self.attach_stream(stream)

    @property
    def recorder(self) -> Optional["ReplayRecorder"]:
        return self._recorder

    def attach_recorder(self, recorder: Optional["ReplayRecorder"]) -> None:
        """Swap the bound recorder; ``None`` disables future writes."""
        self._recorder = recorder

    def attach_stream(self, stream: "EventStream") -> None:
        """Register as an observer so every published event is recorded.

        Idempotent: a second call with the same (or different) stream
        is rejected loudly so the caller does not silently double-write.
        Use :meth:`detach` first if you really intend to swap streams.
        """
        if self._stream is not None:
            raise RuntimeError(
                "HostReplaySink: stream already attached; call "
                "detach() first to swap. Double-attaching would write "
                "every event twice."
            )
        self._observer = stream.add_observer(self._on_event)
        self._stream = stream

    def detach(self) -> None:
        """Remove the observer registration if any. Safe to call twice."""
        if self._stream is not None and self._observer is not None:
            try:
                self._stream.remove_observer(self._observer)
            except Exception:
                # Stream may have been collected; observer detach is
                # best-effort by design.
                pass
        self._stream = None
        self._observer = None

    def _on_event(self, event: Any) -> None:
        """Synchronous observer callback — drops anything that isn't a model."""
        if isinstance(event, BaseModel):
            self.record(event)

    def record(self, event: BaseModel) -> bool:
        """Write a host event into the replay JSONL.

        Returns ``True`` if a JSONL line was emitted, ``False`` if the
        event was dropped (no recorder bound, or unknown kind).
        """
        if self._recorder is None:
            return False
        kind = host_event_to_replay_kind(event)
        if kind is None:
            return False
        try:
            payload = host_event_to_replay_payload(event)
            turn_id = payload.get("turn_id") if isinstance(payload, dict) else None
            self._recorder.record(
                kind,
                turn_id=turn_id if isinstance(turn_id, str) else None,
                payload=payload,
            )
            return True
        except Exception as exc:
            _logger.warning(
                "HostReplaySink: failed to record %s: %s",
                type(event).__name__,
                exc,
            )
            return False


def replay_payload_to_host_event(
    kind: str, payload: dict[str, Any]
) -> Optional[BaseModel]:
    """Reverse projection: rebuild the public Pydantic model from JSONL.

    Used by tests / replay readers that want to rehydrate a host event
    from disk. Returns ``None`` for kinds that are not on the v1.2
    ``agentao.host`` surface so callers can safely route mixed-kind
    reads through this helper.

    The recorder's sanitizer may have appended top-level metadata
    (``redaction_hits`` / ``redacted`` / ``redacted_fields``) before
    write. Those keys are stripped here because the public Pydantic
    models use ``extra="forbid"`` — without the strip a redacted line
    would raise ``ValidationError`` on rehydration.
    """
    # Lazy import: keeps the public ``agentao.host`` surface independent
    # of the replay subpackage's import cost when reverse projection
    # isn't used.
    from ..replay.sanitize import SANITIZER_INJECTED_FIELDS

    clean_payload = {k: v for k, v in payload.items() if k not in SANITIZER_INJECTED_FIELDS}
    if kind == EventKind.TOOL_LIFECYCLE:
        return ToolLifecycleEvent.model_validate(clean_payload)
    if kind == EventKind.SUBAGENT_LIFECYCLE:
        return SubagentLifecycleEvent.model_validate(clean_payload)
    if kind == EventKind.PERMISSION_DECISION:
        return PermissionDecisionEvent.model_validate(clean_payload)
    return None


__all__ = [
    "HostReplaySink",
    "host_event_to_replay_kind",
    "host_event_to_replay_payload",
    "replay_payload_to_host_event",
]
