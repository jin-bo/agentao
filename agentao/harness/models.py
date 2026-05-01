"""Public Pydantic payload models for the embedded harness contract.

The harness API treats these models as the host-facing compatibility
boundary. Adding optional fields is backwards-compatible; removing a
field, renaming a field, changing enum values, or changing field
semantics requires a schema version bump.

Internal runtime events from :mod:`agentao.transport.events` stay
unchanged — these models are a deliberate, redacted projection.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Constrained string types
# ---------------------------------------------------------------------------

# Public timestamp fields require canonical ``Z``-suffix RFC 3339 UTC
# strings. Offsets such as ``+00:00`` are intentionally rejected so that
# schema snapshots and host parsing stay stable across runs.
RFC3339UTCString = Annotated[
    str,
    Field(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$",
        description=(
            "RFC 3339 UTC timestamp with the canonical 'Z' suffix; offsets "
            "such as +00:00 are intentionally rejected for canonical form."
        ),
    ),
]


# ---------------------------------------------------------------------------
# Active permissions snapshot
# ---------------------------------------------------------------------------


class ActivePermissions(BaseModel):
    """Read-only projection of the currently active permission policy.

    ``loaded_sources`` carries stable string labels:

    - ``preset:<mode>``
    - ``project:<relative-or-absolute-path>``
    - ``user:<path>``
    - ``injected:<name>``

    MVP intentionally does not expose per-rule provenance; hosts that
    need user-facing provenance can combine ``loaded_sources`` with
    their own injected policy metadata.
    """

    mode: Literal["read-only", "workspace-write", "full-access", "plan"]
    rules: List[Dict[str, Any]]
    loaded_sources: List[str]

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Lifecycle / decision events
# ---------------------------------------------------------------------------


class ToolLifecycleEvent(BaseModel):
    """Public lifecycle envelope for a single tool call.

    Must not include full tool args, raw stdout/stderr, raw diffs, MCP
    raw responses, or unredacted large outputs. ``summary`` is a
    redacted/truncated host-facing string.

    ``phase="failed"`` covers both execution errors and cancellation;
    hosts read ``outcome`` to distinguish error from cancelled. For
    cancellation, ``error_type`` is ``None``.
    """

    event_type: Literal["tool_lifecycle"] = "tool_lifecycle"
    session_id: str
    turn_id: Optional[str] = None
    tool_call_id: str
    tool_name: str
    phase: Literal["started", "completed", "failed"]
    started_at: RFC3339UTCString
    completed_at: Optional[RFC3339UTCString] = None
    outcome: Optional[Literal["ok", "error", "cancelled"]] = None
    summary: Optional[str] = None
    error_type: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class SubagentLifecycleEvent(BaseModel):
    """Public lineage fact for a sub-agent task/session.

    Unlike :class:`ToolLifecycleEvent`, this model exposes ``cancelled``
    as a distinct phase because sub-agent lineage tracking benefits from
    explicit cancellation in the phase value. ``ToolLifecycleEvent``
    keeps cancellation under ``phase="failed", outcome="cancelled"`` to
    keep the tool-call shape compact.

    ``task_summary`` is redacted/truncated, never raw user input or raw
    child-agent prompt text.
    """

    event_type: Literal["subagent_lifecycle"] = "subagent_lifecycle"
    session_id: str
    parent_session_id: Optional[str] = None
    parent_task_id: Optional[str] = None
    child_session_id: Optional[str] = None
    child_task_id: str
    phase: Literal["spawned", "completed", "failed", "cancelled"]
    task_summary: Optional[str] = None
    started_at: RFC3339UTCString
    completed_at: Optional[RFC3339UTCString] = None
    error_type: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class PermissionDecisionEvent(BaseModel):
    """Public projection of a single permission decision.

    Fires on **every** decision; hosts that do not render ``allow``
    decisions must still drain the iterator to avoid backpressure.

    ``matched_rule`` intentionally has no per-rule source label in MVP.
    Use ``loaded_sources`` for global context. ``reason`` is a
    redacted/truncated host-facing string.
    """

    event_type: Literal["permission_decision"] = "permission_decision"
    session_id: str
    turn_id: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_name: str
    decision_id: str
    outcome: Literal["allow", "deny", "prompt"]
    mode: Literal["read-only", "workspace-write", "full-access", "plan"]
    matched_rule: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None
    loaded_sources: List[str]
    decided_at: RFC3339UTCString

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Discriminated union
# ---------------------------------------------------------------------------


HarnessEvent = Annotated[
    Union[ToolLifecycleEvent, SubagentLifecycleEvent, PermissionDecisionEvent],
    Field(discriminator="event_type"),
]


__all__ = [
    "ActivePermissions",
    "HarnessEvent",
    "PermissionDecisionEvent",
    "RFC3339UTCString",
    "SubagentLifecycleEvent",
    "ToolLifecycleEvent",
]
