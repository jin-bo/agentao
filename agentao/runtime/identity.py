"""Internal id generation and normalization helpers for the runtime.

Public events depend on a small set of stable id fields that the
:mod:`agentao.harness` schema treats as the compatibility contract:

- ``session_id`` — the harness session id (persisted session id when
  available, construction-time UUID fallback otherwise).
- ``turn_id`` — minted at turn entry; one user-submitted agentic loop.
- ``tool_call_id`` — preferred from the LLM tool-call object; UUID4
  fallback when absent or empty.
- ``decision_id`` — UUID4 per permission decision.
- ``child_task_id`` / ``child_session_id`` — captured at sub-agent spawn
  time, not inferred from global state at completion.

These helpers stay internal: ``agentao.harness`` exports the resulting id
fields on public models, but never exposes the generators or normalizers.
That keeps host-facing surface narrow and lets us evolve id provenance
without breaking the public contract.

Uniqueness scope for ``tool_call_id`` is ``(session_id, turn_id, tool_call_id)``;
provider-generated ids are not assumed globally unique.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Optional


def new_session_id() -> str:
    """Allocate a fresh harness session id (UUID4)."""
    return str(uuid.uuid4())


def new_turn_id() -> str:
    """Allocate a fresh per-turn id (UUID4)."""
    return str(uuid.uuid4())


def new_decision_id() -> str:
    """Allocate a fresh per-permission-decision id (UUID4)."""
    return str(uuid.uuid4())


def new_child_task_id() -> str:
    """Allocate a fresh sub-agent task id (UUID4)."""
    return str(uuid.uuid4())


def normalize_tool_call_id(provided: Optional[str]) -> str:
    """Return a stable, non-empty ``tool_call_id`` for the public surface.

    The LLM tool-call object's ``id`` is preferred when it is a non-empty
    string; otherwise a UUID4 fallback is generated. Callers should
    invoke this once at the planning boundary and reuse the returned
    value across permission decisions, tool lifecycle events, and result
    formatting for the same call.
    """
    if isinstance(provided, str) and provided.strip():
        return provided
    return str(uuid.uuid4())


def utc_now_rfc3339() -> str:
    """Return the current UTC time as a canonical ``Z``-suffix RFC 3339 string.

    Public timestamp fields use this exact form (matching
    :data:`agentao.harness.models.RFC3339UTCString`); offsets such as
    ``+00:00`` are intentionally rejected to keep snapshot diffs and
    host parsing stable.
    """
    now = _dt.datetime.now(_dt.timezone.utc)
    # ``isoformat`` produces e.g. ``2026-04-30T01:02:03.456789+00:00``; we
    # trim microseconds to milliseconds and replace the offset with ``Z``
    # so the wire form is canonical and snapshot-friendly.
    iso = now.strftime("%Y-%m-%dT%H:%M:%S")
    millis = f"{now.microsecond // 1000:03d}"
    return f"{iso}.{millis}Z"
