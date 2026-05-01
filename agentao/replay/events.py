"""ReplayEvent model and event kind constants."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


SCHEMA_VERSION = "1.2"


class EventKind:
    """Stable string identifiers for replay events.

    Using a class of class-variables rather than an Enum keeps the JSONL
    ``kind`` field a plain string with no Python-specific serialization
    (the spec requires JSON-native values).

    Schema version history:

    - 1.0 — initial event set (header, session, turn, user/assistant
      chunks, tool confirm/start/output/complete, subagent, error).
    - 1.1 — adds ``replay_footer`` plus the following emission targets
      (events wired in subsequent steps): ``tool_result``,
      ``llm_call_started`` / ``llm_call_completed`` / ``llm_call_delta``
      / ``llm_call_io``, ``ask_user_requested`` / ``ask_user_answered``,
      ``background_notification_injected``, ``context_compressed``,
      ``session_summary_written``, ``skill_activated`` /
      ``skill_deactivated``, ``memory_write`` / ``memory_delete`` /
      ``memory_cleared``, ``model_changed``,
      ``permission_mode_changed`` / ``readonly_mode_changed``,
      ``plugin_hook_fired``, ``session_loaded`` / ``session_forked``.
    - 1.2 — adds three harness-projected lifecycle kinds so embedded
      hosts have a single audit artifact instead of two parallel
      streams (replay JSONL + harness ``events()``):
      ``tool_lifecycle``, ``subagent_lifecycle``, ``permission_decision``.
      Their payload shapes mirror the public Pydantic models in
      :mod:`agentao.harness.models`; the v1.2 schema embeds those
      payload schemas as the per-kind variant. v1.0 / v1.1 schemas
      remain frozen and continue to validate older replays — see
      ``docs/replay/schema-policy.md``.
    """

    REPLAY_HEADER = "replay_header"
    REPLAY_FOOTER = "replay_footer"  # v1.1: written on recorder.close()
    SESSION_STARTED = "session_started"
    SESSION_ENDED = "session_ended"
    SESSION_SAVED = "session_saved"  # reserved; not emitted in v1
    TURN_STARTED = "turn_started"
    TURN_COMPLETED = "turn_completed"
    USER_MESSAGE = "user_message"
    ASSISTANT_TEXT_CHUNK = "assistant_text_chunk"
    ASSISTANT_THOUGHT_CHUNK = "assistant_thought_chunk"
    TOOL_CONFIRMATION_REQUESTED = "tool_confirmation_requested"
    TOOL_CONFIRMATION_RESOLVED = "tool_confirmation_resolved"
    TOOL_STARTED = "tool_started"
    TOOL_OUTPUT_CHUNK = "tool_output_chunk"
    TOOL_COMPLETED = "tool_completed"
    SUBAGENT_STARTED = "subagent_started"
    SUBAGENT_COMPLETED = "subagent_completed"
    ERROR = "error"

    # v1.1 event kinds — declared here so readers can whitelist them up
    # front, but emission points are added in later implementation steps.
    TOOL_RESULT = "tool_result"
    LLM_CALL_STARTED = "llm_call_started"
    LLM_CALL_COMPLETED = "llm_call_completed"
    LLM_CALL_DELTA = "llm_call_delta"
    LLM_CALL_IO = "llm_call_io"
    ASK_USER_REQUESTED = "ask_user_requested"
    ASK_USER_ANSWERED = "ask_user_answered"
    BACKGROUND_NOTIFICATION_INJECTED = "background_notification_injected"
    CONTEXT_COMPRESSED = "context_compressed"
    SESSION_SUMMARY_WRITTEN = "session_summary_written"
    SKILL_ACTIVATED = "skill_activated"
    SKILL_DEACTIVATED = "skill_deactivated"
    MEMORY_WRITE = "memory_write"
    MEMORY_DELETE = "memory_delete"
    MEMORY_CLEARED = "memory_cleared"
    MODEL_CHANGED = "model_changed"
    PERMISSION_MODE_CHANGED = "permission_mode_changed"
    READONLY_MODE_CHANGED = "readonly_mode_changed"
    PLUGIN_HOOK_FIRED = "plugin_hook_fired"
    SESSION_LOADED = "session_loaded"
    SESSION_FORKED = "session_forked"

    # Versioned partitions. ``V1_0`` is the original event set and is
    # never retroactively grown — adding a kind is always a v1.1+ event.
    # ``V1_1_NEW`` holds only what 1.1 added; readers that want the full
    # 1.1 vocabulary use :data:`V1_1` (the union). The schema generator
    # in :mod:`agentao.replay.schema` consumes these sets directly so
    # that the committed schema files stay in lock-step with the code.
    V1_0 = frozenset({
        REPLAY_HEADER,
        SESSION_STARTED,
        SESSION_ENDED,
        SESSION_SAVED,
        TURN_STARTED,
        TURN_COMPLETED,
        USER_MESSAGE,
        ASSISTANT_TEXT_CHUNK,
        ASSISTANT_THOUGHT_CHUNK,
        TOOL_CONFIRMATION_REQUESTED,
        TOOL_CONFIRMATION_RESOLVED,
        TOOL_STARTED,
        TOOL_OUTPUT_CHUNK,
        TOOL_COMPLETED,
        SUBAGENT_STARTED,
        SUBAGENT_COMPLETED,
        ERROR,
    })

    V1_1_NEW = frozenset({
        REPLAY_FOOTER,
        TOOL_RESULT,
        LLM_CALL_STARTED,
        LLM_CALL_COMPLETED,
        LLM_CALL_DELTA,
        LLM_CALL_IO,
        ASK_USER_REQUESTED,
        ASK_USER_ANSWERED,
        BACKGROUND_NOTIFICATION_INJECTED,
        CONTEXT_COMPRESSED,
        SESSION_SUMMARY_WRITTEN,
        SKILL_ACTIVATED,
        SKILL_DEACTIVATED,
        MEMORY_WRITE,
        MEMORY_DELETE,
        MEMORY_CLEARED,
        MODEL_CHANGED,
        PERMISSION_MODE_CHANGED,
        READONLY_MODE_CHANGED,
        PLUGIN_HOOK_FIRED,
        SESSION_LOADED,
        SESSION_FORKED,
    })

    V1_1 = V1_0 | V1_1_NEW

    # v1.2 event kinds — harness lifecycle projections. Each maps 1:1
    # to a public model in :mod:`agentao.harness.models`; the schema
    # generator embeds the Pydantic-derived payload schema in the v1.2
    # ``oneOf`` variant.
    TOOL_LIFECYCLE = "tool_lifecycle"
    SUBAGENT_LIFECYCLE = "subagent_lifecycle"
    PERMISSION_DECISION = "permission_decision"

    V1_2_NEW = frozenset({
        TOOL_LIFECYCLE,
        SUBAGENT_LIFECYCLE,
        PERMISSION_DECISION,
    })

    V1_2 = V1_1 | V1_2_NEW

    # Back-compat alias. Existing callers that imported ``EventKind.ALL``
    # keep working; new code should pick the version-specific set.
    ALL = V1_2


@dataclass
class ReplayEvent:
    """One structured replay event.

    Always serialized as a single JSON object on its own JSONL line.
    Fields match the schema defined in ``SESSION_REPLAY_PLAN.md``.
    """

    event_id: str
    session_id: str
    instance_id: str
    seq: int
    ts: str
    kind: str
    turn_id: Optional[str] = None
    parent_turn_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "instance_id": self.instance_id,
            "seq": self.seq,
            "ts": self.ts,
            "kind": self.kind,
            "turn_id": self.turn_id,
            "parent_turn_id": self.parent_turn_id,
            "payload": self.payload,
        }
