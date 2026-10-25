"""Snapshot + validation tests for the public harness contract (PR 1).

These tests guard the host-facing wire shape: any model change that
shifts the JSON schema must be reflected in the checked-in snapshot at
``docs/schema/host.events.v3.json``. Hosts depend on that file as
the compatibility contract. ``host.events.v1.json`` and ``v2`` are frozen as
published.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from agentao.host import (
    ActivePermissions,
    HostEvent,
    PermissionDecisionEvent,
    SubagentLifecycleEvent,
    ToolLifecycleEvent,
    export_host_event_json_schema,
)
from agentao.host.schema import (
    FROZEN_SNAPSHOTS,
    HOST_EVENTS_SNAPSHOT,
    normalized_schema_json,
    snapshot_digest,
)
from agentao.runtime import identity as runtime_identity


SNAPSHOT_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs" / "schema" / HOST_EVENTS_SNAPSHOT
)


# ---------------------------------------------------------------------------
# Schema snapshot
# ---------------------------------------------------------------------------


def test_event_schema_matches_snapshot():
    """The generated JSON schema must match docs/schema/host.events.v3.json.

    Comparison uses canonical JSON (sort_keys=True) so a Pydantic patch
    release that only reorders ``$defs`` keys does not flap the test.
    To regenerate: run the export helper and overwrite the file.
    """
    generated = normalized_schema_json(export_host_event_json_schema())
    snapshot = SNAPSHOT_PATH.read_text()
    # Re-normalize the snapshot: canonical form must round-trip even if
    # someone hand-edited the file with reordered keys.
    snapshot_norm = normalized_schema_json(json.loads(snapshot))
    assert generated == snapshot_norm, (
        "Generated harness event schema diverged from "
        f"{SNAPSHOT_PATH.relative_to(Path.cwd()) if SNAPSHOT_PATH.is_relative_to(Path.cwd()) else SNAPSHOT_PATH}. "
        "If the change is intentional, regenerate the snapshot via "
        "agentao.host.schema.export_host_event_json_schema() and re-run."
    )


@pytest.mark.parametrize("name", sorted(FROZEN_SNAPSHOTS))
def test_a_published_snapshot_never_changes(name):
    """The models are closed, so a regenerated old snapshot would list a
    field its validators never accepted. A host that saved the published
    file must keep getting that contract (#550)."""
    path = SNAPSHOT_PATH.parent / name
    assert snapshot_digest(path.read_text(encoding="utf-8")) == FROZEN_SNAPSHOTS[name]


def test_v1_rejects_reasoning_tokens_and_v2_lists_it():
    def usage(schema):
        definition = schema["$defs"]["SubagentUsage"]
        assert definition["additionalProperties"] is False
        return definition["properties"]

    v1 = json.loads((SNAPSHOT_PATH.parent / "host.events.v1.json").read_text(encoding="utf-8"))
    assert "reasoning_tokens" not in usage(v1)
    assert "reasoning_tokens" in usage(export_host_event_json_schema())


def test_v2_rejects_parent_tool_call_id_and_v3_lists_it():
    def lifecycle(schema):
        definition = schema["$defs"]["SubagentLifecycleEvent"]
        assert definition["additionalProperties"] is False
        return definition["properties"]

    v2 = json.loads((SNAPSHOT_PATH.parent / "host.events.v2.json").read_text(encoding="utf-8"))
    assert "parent_tool_call_id" not in lifecycle(v2)
    assert "parent_tool_call_id" in lifecycle(export_host_event_json_schema())


# ---------------------------------------------------------------------------
# Discriminated union round-trip
# ---------------------------------------------------------------------------


HARNESS_ADAPTER = TypeAdapter(HostEvent)


def _now() -> str:
    return runtime_identity.utc_now_rfc3339()


def test_discriminated_union_routes_each_event_type():
    """``HostEvent`` validates the right concrete model per event_type."""
    tool_event = HARNESS_ADAPTER.validate_python({
        "event_type": "tool_lifecycle",
        "session_id": "s-1",
        "tool_call_id": "tc-1",
        "tool_name": "run_shell_command",
        "phase": "started",
        "started_at": _now(),
    })
    assert isinstance(tool_event, ToolLifecycleEvent)

    sub_event = HARNESS_ADAPTER.validate_python({
        "event_type": "subagent_lifecycle",
        "session_id": "s-1",
        "child_task_id": "ct-1",
        "phase": "spawned",
        "started_at": _now(),
    })
    assert isinstance(sub_event, SubagentLifecycleEvent)

    perm_event = HARNESS_ADAPTER.validate_python({
        "event_type": "permission_decision",
        "session_id": "s-1",
        "tool_name": "write_file",
        "decision_id": "d-1",
        "outcome": "deny",
        "mode": "read-only",
        "loaded_sources": ["preset:read-only"],
        "decided_at": _now(),
    })
    assert isinstance(perm_event, PermissionDecisionEvent)


def test_discriminated_union_rejects_unknown_event_type():
    with pytest.raises(ValidationError):
        HARNESS_ADAPTER.validate_python({
            "event_type": "mcp_lifecycle",  # not in MVP
            "session_id": "s-1",
            "tool_name": "x",
            "decision_id": "d-1",
            "outcome": "allow",
            "mode": "read-only",
            "loaded_sources": [],
            "decided_at": _now(),
        })


# ---------------------------------------------------------------------------
# RFC3339 UTC timestamp validation
# ---------------------------------------------------------------------------


def test_timestamp_accepts_rfc3339_utc_z_suffix():
    ev = ToolLifecycleEvent(
        session_id="s",
        tool_call_id="tc",
        tool_name="t",
        phase="started",
        started_at="2026-04-30T01:02:03.456Z",
    )
    assert ev.started_at.endswith("Z")


@pytest.mark.parametrize("bad", [
    "2026-04-30T01:02:03",         # no timezone
    "2026-04-30T01:02:03+00:00",   # offset form rejected by design
    "2026-04-30 01:02:03Z",        # space instead of T
    "not-a-timestamp",
])
def test_timestamp_rejects_non_canonical_forms(bad: str):
    with pytest.raises(ValidationError):
        ToolLifecycleEvent(
            session_id="s",
            tool_call_id="tc",
            tool_name="t",
            phase="started",
            started_at=bad,
        )


# ---------------------------------------------------------------------------
# ActivePermissions shape
# ---------------------------------------------------------------------------


def test_active_permissions_loaded_sources_required():
    ap = ActivePermissions(
        mode="workspace-write",
        rules=[{"tool": "*", "action": "ask"}],
        loaded_sources=["preset:workspace-write"],
    )
    assert ap.loaded_sources == ["preset:workspace-write"]


def test_active_permissions_rejects_extra_fields():
    with pytest.raises(ValidationError):
        ActivePermissions(
            mode="workspace-write",
            rules=[],
            loaded_sources=[],
            source="mixed",  # collapsed source label is not part of the contract
        )
