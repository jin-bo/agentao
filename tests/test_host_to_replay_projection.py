"""P0.8 round-trip: HostEvent ↔ ReplayRecorder JSONL.

The v1.2 replay schema bundles the three host lifecycle events as
typed JSONL payloads so embedded hosts have one audit artifact instead
of two parallel streams. This test asserts:

1. ``HostReplaySink.record(event)`` writes a JSONL line with the
   v1.2 ``kind`` discriminator and a payload that matches the model
   shape exactly (``model_dump(mode="json")``).
2. Reading the JSONL back and routing the payload through
   ``replay_payload_to_host_event(kind, payload)`` reproduces the
   original Pydantic model byte-for-byte.
3. The generated v1.2 schema validates the produced payloads (catches
   schema drift the same day a model changes).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from agentao.host.models import (
    PermissionDecisionEvent,
    SubagentLifecycleEvent,
    ToolLifecycleEvent,
)
from agentao.host.replay_projection import (
    HostReplaySink,
    host_event_to_replay_kind,
    host_event_to_replay_payload,
    replay_payload_to_host_event,
)
from agentao.replay.events import EventKind
from agentao.replay.recorder import ReplayRecorder
from agentao.replay.schema import build_event_schema
from agentao.runtime import identity as runtime_identity


def _now() -> str:
    return runtime_identity.utc_now_rfc3339()


# ---------------------------------------------------------------------------
# Pure projection helpers — no recorder, no disk
# ---------------------------------------------------------------------------


def test_kind_discriminator_for_each_host_model() -> None:
    """Each public model maps to its v1.2 kind name."""
    tool = ToolLifecycleEvent(
        session_id="s",
        tool_call_id="tc",
        tool_name="run_shell_command",
        phase="started",
        started_at=_now(),
    )
    sub = SubagentLifecycleEvent(
        session_id="s", child_task_id="ct", phase="spawned", started_at=_now(),
    )
    perm = PermissionDecisionEvent(
        session_id="s",
        tool_name="write_file",
        decision_id="d",
        outcome="allow",
        mode="workspace-write",
        loaded_sources=[],
        decided_at=_now(),
    )
    assert host_event_to_replay_kind(tool) == EventKind.TOOL_LIFECYCLE
    assert host_event_to_replay_kind(sub) == EventKind.SUBAGENT_LIFECYCLE
    assert host_event_to_replay_kind(perm) == EventKind.PERMISSION_DECISION


def test_unknown_model_returns_none() -> None:
    """A model that is not on the v1.2 host surface drops silently."""

    class _NotHarness:
        pass

    assert host_event_to_replay_kind(_NotHarness()) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Round trip via ReplayRecorder JSONL
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]


def _payload_lines_for_kind(events: list[dict], kind: str) -> list[dict]:
    return [e["payload"] for e in events if e["kind"] == kind]


def test_recorder_writes_three_kinds_round_trip(tmp_path: Path) -> None:
    """Project three events through a real recorder; rehydrate exactly."""
    recorder = ReplayRecorder.create("sess-x", tmp_path)
    sink = HostReplaySink(recorder)

    tool = ToolLifecycleEvent(
        session_id="sess-x",
        turn_id="turn-1",
        tool_call_id="tc-1",
        tool_name="run_shell_command",
        phase="completed",
        started_at=_now(),
        completed_at=_now(),
        outcome="ok",
        summary="2 lines",
    )
    sub = SubagentLifecycleEvent(
        session_id="sess-x",
        parent_session_id="sess-x",
        child_task_id="ct-1",
        phase="completed",
        started_at=_now(),
        completed_at=_now(),
        task_summary="extracted dates",
    )
    perm = PermissionDecisionEvent(
        session_id="sess-x",
        tool_name="write_file",
        tool_call_id="tc-1",
        decision_id="d-1",
        outcome="allow",
        mode="workspace-write",
        loaded_sources=["preset:workspace-write"],
        decided_at=_now(),
    )
    assert sink.record(tool)
    assert sink.record(sub)
    assert sink.record(perm)
    recorder.close()

    events = _read_jsonl(recorder.path)
    # Plus header + footer; we assert the three projected kinds appear.
    kinds = [e["kind"] for e in events]
    assert kinds.count(EventKind.TOOL_LIFECYCLE) == 1
    assert kinds.count(EventKind.SUBAGENT_LIFECYCLE) == 1
    assert kinds.count(EventKind.PERMISSION_DECISION) == 1

    # Rehydrate and assert byte-equal Pydantic equality (model_dump
    # comparison; Pydantic models compare by field values).
    rehydrated_tool = replay_payload_to_host_event(
        EventKind.TOOL_LIFECYCLE,
        _payload_lines_for_kind(events, EventKind.TOOL_LIFECYCLE)[0],
    )
    assert rehydrated_tool == tool

    rehydrated_sub = replay_payload_to_host_event(
        EventKind.SUBAGENT_LIFECYCLE,
        _payload_lines_for_kind(events, EventKind.SUBAGENT_LIFECYCLE)[0],
    )
    assert rehydrated_sub == sub

    rehydrated_perm = replay_payload_to_host_event(
        EventKind.PERMISSION_DECISION,
        _payload_lines_for_kind(events, EventKind.PERMISSION_DECISION)[0],
    )
    assert rehydrated_perm == perm


def test_sink_without_recorder_is_silent_noop() -> None:
    """A sink with ``recorder=None`` records nothing and reports False."""
    sink = HostReplaySink(None)
    tool = ToolLifecycleEvent(
        session_id="s",
        tool_call_id="tc",
        tool_name="run_shell_command",
        phase="started",
        started_at=_now(),
    )
    assert sink.record(tool) is False


def test_sink_attach_then_record(tmp_path: Path) -> None:
    """``attach_recorder`` swaps an existing sink into write mode."""
    sink = HostReplaySink(None)
    recorder = ReplayRecorder.create("sess-y", tmp_path)
    sink.attach_recorder(recorder)

    tool = ToolLifecycleEvent(
        session_id="sess-y",
        tool_call_id="tc",
        tool_name="run_shell_command",
        phase="started",
        started_at=_now(),
    )
    assert sink.record(tool) is True
    recorder.close()
    events = _read_jsonl(recorder.path)
    assert any(e["kind"] == EventKind.TOOL_LIFECYCLE for e in events)


# ---------------------------------------------------------------------------
# Schema validation: the produced payload must match the v1.2 schema
# ---------------------------------------------------------------------------


def _strip_unsupported_validator_keys(schema: dict) -> dict:
    """Remove ``$schema`` / ``$id`` keys from sub-schemas — Pydantic adds
    metadata that ``jsonschema`` complains about when applied to inline
    payload sub-schemas. The payload variant itself does not need them.
    """
    s = dict(schema)
    s.pop("$schema", None)
    s.pop("$id", None)
    return s


def test_v1_2_schema_validates_each_projected_payload() -> None:
    """The v1.2 schema's per-kind variant must accept the model's JSON dump.

    This is the drift tripwire: a Pydantic field rename / removal that
    is not also reflected in the regenerated ``replay-event-1.2.json``
    fails this test loudly.
    """
    pytest.importorskip("jsonschema", reason="jsonschema not installed")
    import jsonschema

    schema = build_event_schema("1.2")
    variants = {
        v["properties"]["kind"]["const"]: v for v in schema["oneOf"]
    }

    cases = [
        (
            EventKind.TOOL_LIFECYCLE,
            host_event_to_replay_payload(
                ToolLifecycleEvent(
                    session_id="s",
                    tool_call_id="tc",
                    tool_name="run_shell_command",
                    phase="started",
                    started_at=_now(),
                )
            ),
        ),
        (
            EventKind.SUBAGENT_LIFECYCLE,
            host_event_to_replay_payload(
                SubagentLifecycleEvent(
                    session_id="s",
                    child_task_id="ct",
                    phase="spawned",
                    started_at=_now(),
                )
            ),
        ),
        (
            EventKind.PERMISSION_DECISION,
            host_event_to_replay_payload(
                PermissionDecisionEvent(
                    session_id="s",
                    tool_name="write_file",
                    decision_id="d",
                    outcome="allow",
                    mode="workspace-write",
                    loaded_sources=[],
                    decided_at=_now(),
                )
            ),
        ),
    ]
    for kind, payload in cases:
        payload_schema = _strip_unsupported_validator_keys(
            variants[kind]["properties"]["payload"]
        )
        # jsonschema's validate() raises on failure — assertion is implicit.
        jsonschema.validate(instance=payload, schema=payload_schema)


def test_v1_2_schema_validates_redacted_projected_payload(tmp_path: Path) -> None:
    """Sanitizer-injected ``redaction_hits`` etc. must keep the line valid.

    Repro for the codex P2: ``ReplayRecorder.record()`` appends
    sanitization metadata at the top of the payload when SECRET_PATTERNS
    fire. With strict ``additionalProperties: false`` typed payloads,
    the resulting JSONL line previously failed v1.2 validation in
    exactly the redaction scenarios replay is meant to capture.
    """
    pytest.importorskip("jsonschema", reason="jsonschema not installed")
    import jsonschema

    recorder = ReplayRecorder.create("sess-redact", tmp_path)
    sink = HostReplaySink(recorder)

    # ``summary`` is a free-form string on ToolLifecycleEvent; embedding
    # an OpenAI-shaped key here triggers the SECRET_PATTERNS scanner.
    secret = "sk-proj-" + "A" * 32
    tool = ToolLifecycleEvent(
        session_id="sess-redact",
        tool_call_id="tc-redact",
        tool_name="run_shell_command",
        phase="completed",
        started_at=_now(),
        completed_at=_now(),
        outcome="ok",
        summary=f"leaked {secret} in output",
    )
    assert sink.record(tool)
    recorder.close()

    events = _read_jsonl(recorder.path)
    tool_events = [e for e in events if e["kind"] == EventKind.TOOL_LIFECYCLE]
    assert len(tool_events) == 1
    line = tool_events[0]
    payload = line["payload"]

    # The sanitizer DID fire — otherwise this test would not exercise
    # the bug under review.
    assert "redaction_hits" in payload, (
        f"expected sanitizer to add redaction_hits; got: {sorted(payload)}"
    )
    assert secret not in json.dumps(payload), "raw secret must not be on disk"

    # And the resulting line still validates against the v1.2 schema.
    schema = build_event_schema("1.2")
    variants = {v["properties"]["kind"]["const"]: v for v in schema["oneOf"]}
    payload_schema = _strip_unsupported_validator_keys(
        variants[EventKind.TOOL_LIFECYCLE]["properties"]["payload"]
    )
    jsonschema.validate(instance=payload, schema=payload_schema)


def test_start_replay_auto_attaches_host_sink(tmp_path: Path) -> None:
    """``start_replay()`` must wire host events into the recorder.

    Repro for the round-5 codex P2: without the auto-wire, events
    published on ``Agentao._host_events`` were never written to the
    replay JSONL — embedded hosts saw two parallel streams instead of
    one audit artifact.
    """
    from unittest.mock import Mock, patch

    from agentao.replay.config import ReplayConfig

    mock_llm = Mock()
    mock_llm.logger = Mock()
    mock_llm.model = "gpt-test"
    mock_llm.api_key = "fake"
    with patch("agentao.tooling.mcp_tools.McpClientManager"), patch(
        "agentao.tooling.mcp_tools.load_mcp_config", return_value={}
    ):
        from agentao.agent import Agentao

        agent = Agentao(
            working_directory=tmp_path,
            llm_client=mock_llm,
            replay_config=ReplayConfig(enabled=True),
        )
        try:
            replay_path = agent.start_replay(session_id="sess-wired")
            assert replay_path is not None and replay_path.exists()
            assert agent._host_replay_sink is not None

            tool = ToolLifecycleEvent(
                session_id="sess-wired",
                tool_call_id="tc-wired",
                tool_name="run_shell_command",
                phase="started",
                started_at=_now(),
            )
            # Publish via the public stream the runtime actually uses;
            # the sink should observe and project into the recorder.
            agent._host_events.publish(tool)

            # End the replay so the file is flushed and the sink detached.
            agent.end_replay()
            assert agent._host_replay_sink is None
        finally:
            agent.close()

    events = _read_jsonl(replay_path)
    tool_events = _payload_lines_for_kind(events, EventKind.TOOL_LIFECYCLE)
    assert len(tool_events) == 1, (
        "expected the published tool_lifecycle event to land in the "
        f"replay file via the auto-wired sink; got: "
        f"{[e['kind'] for e in events]}"
    )
    rehydrated = replay_payload_to_host_event(
        EventKind.TOOL_LIFECYCLE, tool_events[0]
    )
    assert isinstance(rehydrated, ToolLifecycleEvent)
    assert rehydrated.tool_call_id == "tc-wired"


def test_reverse_projection_strips_sanitizer_metadata(tmp_path: Path) -> None:
    """A redacted projected payload must round-trip through reverse projection.

    Repro for the codex P2: the v1.2 schema allows the sanitizer's
    metadata fields, but the host Pydantic models use
    ``extra="forbid"``. Without explicit stripping in
    :func:`replay_payload_to_host_event`, a redacted
    ``tool_lifecycle`` line raises ``ValidationError`` on rehydration —
    breaking any reader that wants to rehydrate a redacted replay.
    """
    recorder = ReplayRecorder.create("sess-redact-rt", tmp_path)
    sink = HostReplaySink(recorder)

    secret = "sk-proj-" + "Z" * 32
    tool = ToolLifecycleEvent(
        session_id="sess-redact-rt",
        tool_call_id="tc-redact-rt",
        tool_name="run_shell_command",
        phase="completed",
        started_at=_now(),
        completed_at=_now(),
        outcome="ok",
        summary=f"leaked {secret}",
    )
    assert sink.record(tool)
    recorder.close()

    events = _read_jsonl(recorder.path)
    payloads = _payload_lines_for_kind(events, EventKind.TOOL_LIFECYCLE)
    assert len(payloads) == 1
    payload = payloads[0]
    assert "redaction_hits" in payload, "sanitizer must have fired"

    rehydrated = replay_payload_to_host_event(
        EventKind.TOOL_LIFECYCLE, payload
    )
    assert isinstance(rehydrated, ToolLifecycleEvent)
    # Field equality except for the redacted summary, which now carries
    # the placeholder substitution chosen by the scanner.
    assert rehydrated.tool_call_id == tool.tool_call_id
    assert rehydrated.session_id == tool.session_id
    assert rehydrated.summary is not None and secret not in rehydrated.summary
