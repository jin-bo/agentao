"""Tenant-scoped audit pipeline using the embedded harness contract.

Runnable companion to §4.7.5 of the developer guide. Demonstrates the
canonical "drain ``agent.events()`` into a database" pattern:

- Pin ``agent.active_permissions()`` once per session (audit-log
  enrichment).
- Stream :class:`agentao.host.HostEvent` into a local SQLite
  ``agent_audit`` table while ``agent.arun(...)`` is running.
- Print the table after the turn so you can see the schema-stable shape
  that survives Agentao release upgrades.

The schema only references fields documented in
``agentao/host/models.py``. New optional fields landing in future
minor releases are *additive* — this script (and your real audit
pipeline) keep working without changes.

Run from the repository root::

    OPENAI_API_KEY=sk-... uv run python examples/host_audit_pipeline.py

Without ``OPENAI_API_KEY`` (or another ``LLM_PROVIDER``-prefixed
credential), the script exits with code 2 and instructions rather than
crashing at the first LLM call.

For the full host-facing contract — delivery semantics, schema
snapshots, redaction rules — see ``developer-guide/en/part-4/7-host-contract.md``
and ``docs/api/host.md``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

from agentao.embedding import build_from_environment
from agentao.host import (
    HostEvent,
    PermissionDecisionEvent,
    SubagentLifecycleEvent,
    ToolLifecycleEvent,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TENANT_ID = "tenant-acme"
PROMPT = (
    "List the files directly under the current working directory. "
    "Just call the tool once and summarise what you found in one short line."
)


# ---------------------------------------------------------------------------
# Audit schema — minimal, host-stable
# ---------------------------------------------------------------------------

AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    ts          TEXT NOT NULL,         -- RFC3339UTCString from the harness model
    event_type  TEXT NOT NULL,         -- 'tool_lifecycle' | 'permission_decision' | 'subagent_lifecycle'
    payload     TEXT NOT NULL          -- JSON dump of the projected row
);

CREATE TABLE IF NOT EXISTS active_policy_snapshot (
    session_id      TEXT PRIMARY KEY,
    mode            TEXT NOT NULL,
    rules_json      TEXT NOT NULL,
    loaded_sources  TEXT NOT NULL
);
"""


def _project_event(tenant_id: str, ev: HostEvent) -> Dict[str, Any]:
    """Map a HostEvent to a JSON-safe audit row.

    Field selection mirrors the documented Pydantic models exactly — no
    invented fields, no internal types. Future minor releases may add
    fields; this projection picks only the documented ones, so unknown
    additions are silently ignored.
    """
    base: Dict[str, Any] = {
        "tenant_id": tenant_id,
        "session_id": ev.session_id,
        "event_type": ev.event_type,
    }

    if isinstance(ev, ToolLifecycleEvent):
        base.update(
            {
                # ToolLifecycleEvent timestamps: started_at always set,
                # completed_at set on completion/failure. Use whichever is
                # most recent so audit ordering matches event ordering.
                "ts": ev.completed_at or ev.started_at,
                "tool_call_id": ev.tool_call_id,
                "tool_name": ev.tool_name,
                "phase": ev.phase,
                "outcome": ev.outcome,
                "summary": ev.summary,
                "error_type": ev.error_type,
                "turn_id": ev.turn_id,
            }
        )
    elif isinstance(ev, PermissionDecisionEvent):
        base.update(
            {
                "ts": ev.decided_at,
                "tool_call_id": ev.tool_call_id,
                "tool_name": ev.tool_name,
                "decision_id": ev.decision_id,
                "outcome": ev.outcome,           # 'allow' | 'deny' | 'prompt'
                "mode": ev.mode,
                "matched_rule": ev.matched_rule,
                "loaded_sources": ev.loaded_sources,
                "reason": ev.reason,
                "turn_id": ev.turn_id,
            }
        )
    elif isinstance(ev, SubagentLifecycleEvent):
        base.update(
            {
                "ts": ev.completed_at or ev.started_at,
                "child_session_id": ev.child_session_id,
                "child_task_id": ev.child_task_id,
                "parent_session_id": ev.parent_session_id,
                "parent_task_id": ev.parent_task_id,
                "phase": ev.phase,
                "task_summary": ev.task_summary,
                "error_type": ev.error_type,
            }
        )
    else:
        # Forward-compatible fallback: a future release ships a new
        # HostEvent variant. Record the discriminator so downstream
        # ETL can route it later, but never crash.
        base.update({"ts": "", "raw": ev.model_dump()})

    return base


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


async def audit_loop(agent, tenant_id: str, db: sqlite3.Connection) -> None:
    """Drain harness events until the iterator closes.

    Same-session ordering is guaranteed by the contract, so the
    INSERT order matches the event order. Permission decisions for a
    given ``tool_call_id`` always precede the matching tool-call
    ``phase="started"`` row, so downstream views can stitch them.
    """
    async for ev in agent.events():
        row = _project_event(tenant_id, ev)
        db.execute(
            "INSERT INTO agent_audit "
            "(tenant_id, session_id, ts, event_type, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                row["tenant_id"],
                row["session_id"],
                row["ts"],
                row["event_type"],
                json.dumps(row, ensure_ascii=False),
            ),
        )
        db.commit()


def pin_active_policy(agent, db: sqlite3.Connection, session_id: str) -> None:
    """Snapshot the active permission policy at session start.

    This is the audit-log enrichment pattern: even if the policy is
    later mutated mid-session via ``add_loaded_source(...)`` /
    ``set_mode(...)``, the audit log records what was in effect when
    the session began.
    """
    snap = agent.active_permissions()
    db.execute(
        "INSERT OR REPLACE INTO active_policy_snapshot "
        "(session_id, mode, rules_json, loaded_sources) VALUES (?, ?, ?, ?)",
        (
            session_id,
            snap.mode,
            json.dumps(snap.rules, ensure_ascii=False),
            json.dumps(snap.loaded_sources, ensure_ascii=False),
        ),
    )
    db.commit()
    print(f"[active_permissions] mode={snap.mode!r}")
    print(f"[active_permissions] loaded_sources={snap.loaded_sources}")


def dump_audit_table(db: sqlite3.Connection) -> None:
    """Print the audit table after the turn so you can see the result."""
    print("\n=== agent_audit ===")
    rows = list(
        db.execute(
            "SELECT id, ts, event_type, payload "
            "FROM agent_audit ORDER BY id"
        )
    )
    if not rows:
        print("(empty — no harness events fired during this turn)")
        return
    for id_, ts, event_type, payload in rows:
        print(f"#{id_:02d}  {ts}  {event_type}")
        decoded = json.loads(payload)
        skip = {"tenant_id", "session_id", "event_type", "ts"}
        for key, value in decoded.items():
            if key in skip:
                continue
            if value in (None, [], {}):
                continue
            print(f"       {key}: {value}")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


async def amain(workdir: Path, db_path: Path) -> int:
    print(f"[setup] working_directory={workdir}")
    print(f"[setup] audit_db={db_path}")

    db = sqlite3.connect(db_path)
    db.executescript(AUDIT_SCHEMA)

    agent = build_from_environment(working_directory=workdir)
    try:
        # Real hosts pass in their own business session id (the same one
        # they store in their session table). Each event carries
        # ``ev.session_id`` from the runtime, so policy_snapshot rows
        # join to event rows on whatever id you choose to standardise on.
        business_session_id = "demo-session-0"
        pin_active_policy(agent, db, business_session_id)
        print()

        consumer = asyncio.create_task(audit_loop(agent, TENANT_ID, db))
        try:
            reply = await agent.arun(PROMPT)
        finally:
            consumer.cancel()
            try:
                await consumer
            except asyncio.CancelledError:
                pass

        print(f"\n[reply] {reply.strip()}")
        dump_audit_table(db)
        return 0
    finally:
        db.close()
        agent.close()


def main() -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        print(
            "OPENAI_API_KEY is not set. Configure it (or any "
            "LLM_PROVIDER-prefixed credential) before running this example.",
            file=sys.stderr,
        )
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="agentao-audit-"))
    db_path = workdir / "audit.sqlite"
    return asyncio.run(amain(workdir, db_path))


if __name__ == "__main__":
    raise SystemExit(main())
