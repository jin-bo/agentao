# 4.7 The Embedded Harness Contract — Your Stable Host API

> **What you'll learn**
> - **Why** there's a separate `agentao.harness` package alongside Transport / AgentEvent
> - The **three surfaces** it exposes (events, policy snapshot, capability protocols) and which problem each solves
> - **`agent.events()` vs. `Transport(on_event=…)`** — when to use which (or both)
> - **End-to-end**: a tenant-scoped audit pipeline in ~30 lines that won't break on the next Agentao release

If you've read [4.2 AgentEvent](./2-agent-events) and the `:::warning` told you "use `HarnessEvent` instead for production", this chapter is the **how** behind that advice.

## 4.7.1 The problem this solves

You're shipping a multi-tenant SaaS. Every action the agent takes has to produce an audit row: tenant, user, tool name, args, was it approved, did it succeed.

You build it on top of `AgentEvent` (Part 4.2), it works, you ship. Three months later Agentao 0.5.0 lands. `EventType.MEMORY_WRITE` got renamed. Two `data` fields moved. Your audit pipeline silently misses rows for a week before someone notices the ETL count diverging.

This is the **churn problem**. `AgentEvent` is the runtime's internal event bus — it powers the CLI, debug UI, and replay machinery, all of which need rich detail and can absorb churn release-by-release. **A production host can't.**

The **embedded harness contract** is the answer: a deliberately small surface at `agentao.harness` that's:

- **Frozen as Pydantic models** — fields and types are part of the public contract
- **Schema-snapshotted** in `docs/schema/harness.events.v1.json` — byte-equality enforced in CI
- **A redacted projection** of internal events — e.g. user prompt text isn't in the audit body
- **Versioned** — adding optional fields is backwards-compatible; removing or renaming requires a schema bump

If your code only touches `agentao.harness` (plus the documented `Agentao(...)` constructor and `chat()` / `events()` / `active_permissions()` methods), you stay forward-compatible.

## 4.7.2 The three surfaces

`agentao.harness` exposes three distinct surfaces. They live in one package because they share the "stable host contract" promise, but they solve different problems:

| Surface | What you call | What it gives you | Used in |
|---------|---------------|-------------------|---------|
| **Events** | `agent.events()` async iterator | A stream of `HarnessEvent` (tool / sub-agent / permission lifecycle) | Audit pipelines, observability, real-time UI |
| **Policy snapshot** | `agent.active_permissions()` | A JSON-safe `ActivePermissions` (mode + rules + sources) | Settings UI, audit-log enrichment, compliance reports |
| **Capability protocols** | `from agentao.harness.protocols import FileSystem, ShellExecutor` | Runtime-checkable Protocols you implement to inject Docker / virtual FS / audit proxies | See [2.2 Tier 3 · filesystem / shell](/en/part-2/2-constructor-reference#tier-3-advanced-injections) and [6.4](/en/part-6/4-multi-tenant-fs) |

This chapter focuses on **events** and **policy snapshot** — the parts most readers reach for first. Capability protocols are already covered in their construction-time context.

## 4.7.3 The three event types

Three orthogonal lifecycle facts. Each is a Pydantic model carrying just enough context for an audit row, with a **discriminator** field (`event_type`) so you can `isinstance`-dispatch.

| Event | Phases | Fires on |
|-------|--------|---------|
| `ToolLifecycleEvent` | `started` · `completed` · `failed` | Every tool call (built-in or custom). Cancellation surfaces as `phase="failed", outcome="cancelled"`. |
| `PermissionDecisionEvent` | (no phases — single decision per call) | Every permission decision: `allow` / `deny` / `prompt`. **Consumers must drain even allow events** — the audit row needs them. |
| `SubagentLifecycleEvent` | `spawned` · `completed` · `failed` · `cancelled` | Sub-agent task lifecycle. Note: `cancelled` is a **distinct phase** here (unlike tools). |

`HarnessEvent` is the discriminated union of these three. Use `isinstance` to branch:

```python
from agentao.harness import (
    HarnessEvent,
    ToolLifecycleEvent,
    SubagentLifecycleEvent,
    PermissionDecisionEvent,
)

async for ev in agent.events():
    if isinstance(ev, ToolLifecycleEvent):
        ...
    elif isinstance(ev, PermissionDecisionEvent):
        ...
    elif isinstance(ev, SubagentLifecycleEvent):
        ...
```

Full field list: [Appendix A.10](/en/appendix/a-api-reference#a-10-embedded-harness-contract). The schemas live at [`docs/schema/harness.events.v1.json`](https://github.com/jin-bo/agentao/blob/main/docs/schema/harness.events.v1.json).

## 4.7.4 `agent.events()` vs. `Transport(on_event=…)` — when to use which

Both deliver events, but they're for different jobs. Don't pick one *instead of* the other — pick the right one for each consumer:

| Question | Use `agent.events()` (harness) | Use `Transport(on_event=…)` |
|----------|-------------------------------|------------------------------|
| Is this a **production host** that needs forward compatibility? | ✅ | ❌ — fields will churn |
| Do you need **streaming text chunks** for a UI (`LLM_TEXT`, `THINKING`)? | ❌ — projected out | ✅ — that's exactly its job |
| Building an **audit pipeline** / SIEM feed / billing meter? | ✅ | ❌ |
| Building **CLI / debug tooling** that wants every internal detail? | ❌ — too redacted | ✅ |
| Need **async pull** semantics with backpressure? | ✅ — `async for` with bounded queue | ❌ — push callback |
| Need **multiple concurrent consumers**? | ⚠️ MVP: one stream per `Agentao` | ✅ — fan out via your own dispatcher |

Most production deployments use **both**: Transport drives the streaming UI; `events()` drives the audit / observability pipeline. They share zero code paths so they don't fight each other.

## 4.7.5 End-to-end: tenant-scoped audit pipeline

::: tip Two runnable starting points
- **First taste** — [`examples/harness_events.py`](https://github.com/jin-bo/agentao/blob/main/examples/harness_events.py): minimal, prints each `HarnessEvent` to stdout. ~50 lines, run with `OPENAI_API_KEY=sk-... uv run python examples/harness_events.py`.
- **Production pattern** — [`examples/harness_audit_pipeline.py`](https://github.com/jin-bo/agentao/blob/main/examples/harness_audit_pipeline.py): the full audit-loop below, with SQLite persistence + after-turn table dump.

Read on for the schema-stable pattern; clone either example to get hands-on output in 60 seconds.
:::

Here's a complete pattern. Every tool call, permission decision, and sub-agent action gets one row in your audit table — schema-stable across Agentao releases. Field names below are the actual ones in [`agentao/harness/models.py`](https://github.com/jin-bo/agentao/blob/main/agentao/harness/models.py); compare with [Appendix A.10](/en/appendix/a-api-reference#a-10-embedded-harness-contract) for the full type signatures.

```python
"""Tenant audit pipeline. Run alongside agent.arun()."""
import asyncio
import json
from agentao import Agentao
from agentao.harness import (
    ToolLifecycleEvent,
    PermissionDecisionEvent,
    SubagentLifecycleEvent,
)

async def audit_loop(agent: Agentao, tenant_id: str, db):
    """Drain harness events and write one audit row per fact."""
    async for ev in agent.events():
        row = {
            "tenant_id":  tenant_id,
            "session_id": ev.session_id,
            "event_type": ev.event_type,  # discriminator
        }

        if isinstance(ev, ToolLifecycleEvent):
            # started_at always set; completed_at set on completion/failure.
            ts = ev.completed_at or ev.started_at
            row.update({
                "ts":           ts,
                "tool_call_id": ev.tool_call_id,
                "tool_name":    ev.tool_name,
                "phase":        ev.phase,        # started | completed | failed
                "outcome":      ev.outcome,      # ok | error | cancelled
                "summary":      ev.summary,      # redacted host-facing string
                "error_type":   ev.error_type,
            })
        elif isinstance(ev, PermissionDecisionEvent):
            row.update({
                "ts":             ev.decided_at,
                "tool_call_id":   ev.tool_call_id,
                "tool_name":      ev.tool_name,
                "decision_id":    ev.decision_id,
                "outcome":        ev.outcome,    # allow | deny | prompt
                "mode":           ev.mode,
                "matched_rule":   ev.matched_rule,    # dict or None
                "loaded_sources": ev.loaded_sources,  # list[str]
                "reason":         ev.reason,
            })
        elif isinstance(ev, SubagentLifecycleEvent):
            row.update({
                "ts":                ev.completed_at or ev.started_at,
                "child_session_id":  ev.child_session_id,
                "child_task_id":     ev.child_task_id,
                "phase":             ev.phase,    # spawned|completed|failed|cancelled
                "task_summary":      ev.task_summary,
            })

        await db.execute(
            "INSERT INTO agent_audit (tenant_id, session_id, ts, event_type, payload) "
            "VALUES ($1, $2, $3, $4, $5)",
            row["tenant_id"], row["session_id"], row["ts"],
            ev.event_type, json.dumps(row),
        )

# Wire it up alongside arun()
async def handle_request(tenant_id: str, message: str, db):
    agent = make_agent_for_session(tenant_id, ...)  # your factory
    audit = asyncio.create_task(audit_loop(agent, tenant_id, db))
    try:
        reply = await agent.arun(message)
        return reply
    finally:
        audit.cancel()                # cancellation releases queue/subscription
        agent.close()
```

**Why this pattern is robust**:

- Same-session ordering is guaranteed by the contract — your audit row order matches event order.
- `PermissionDecisionEvent` precedes the matching `ToolLifecycleEvent(phase="started")` (same `tool_call_id`) so downstream views can stitch them.
- Dropping a slow consumer doesn't drop events: backpressure is host-pulled via a bounded queue. Events block the producer rather than silently get dropped.
- When Agentao 0.5 ships and adds a new internal event variant, your audit pipeline doesn't notice — that event isn't projected to harness, and any new `HarnessEvent` variant the projection *does* gain only adds optional fields.

## 4.7.6 `agent.active_permissions()` — policy snapshots

When your settings UI shows "this session can: read / write / fetch from these domains", you don't want to peek into the internal `PermissionEngine`. Use the public snapshot:

```python
snap = agent.active_permissions()

snap.mode             # Literal: "read-only" | "workspace-write" | "full-access" | "plan"
snap.rules            # list[dict] — the resolved rules
snap.loaded_sources   # list[str] — provenance labels
```

`loaded_sources` carries stable string labels:

- `preset:<mode>` — built-in preset (e.g. `preset:workspace-write`)
- `project:<path>` — project-scoped JSON loaded from `<wd>/.agentao/permissions.json`
- `user:<path>` — user-scoped JSON from `~/.agentao/permissions.json`
- `injected:<name>` — host-supplied policy via `add_loaded_source()`
- `default:no-engine` — fallback when no engine is configured

Pin this snapshot into your audit log on session start so you can later answer "what policy was active when this happened?" without replaying the whole engine.

## 4.7.7 Forward-compatibility guarantees

What `agentao.harness` promises:

- **Adding a field** to any model = backwards-compatible. Your code keeps working.
- **Removing or renaming a field** requires a schema version bump (`harness.events.v1.json` → `v2`) and a clear migration in the changelog.
- **Internal types** (`agentao.transport.AgentEvent`, `agentao.tools.ToolExecutionResult`, `agentao.permissions.PermissionEngine`) may change in any release. **Don't import them directly into production code paths.**
- **Schema snapshots are CI-enforced** via `tests/test_harness_schema.py` — a model change that shifts the wire form fails the build until both the model and the snapshot are updated together.

What this gives you operationally: **you can pin `agentao>=0.4.0,<1.0` in production** with confidence that the harness contract is the contract you'll have at 0.9.x.

## 4.7.8 What's *not* in the contract

The harness deliberately doesn't expose:

- Public agent graph / descendants store API
- Host-facing hooks list/disable API
- Host-facing MCP reload / lifecycle events
- Local plugin export/import; remote plugin share
- External session import
- Generated client SDKs

The CLI may build on the same events for its own UI, but its stores and commands are **not promoted** to the harness API.

If you find yourself wanting to reach past `agentao.harness` for something missing, file an issue rather than relying on internal types.

## 4.7.9 Quick decision flow

```
Q: I need to react to agent events. Which surface?
│
├─ Streaming UI (text chunks, thinking, in-flight tool view)?
│      → Transport(on_event=…)         (Part 4.3)
│
├─ Audit / SIEM / billing / compliance?
│      → agent.events()                (this chapter)
│
├─ Showing the active policy in a settings UI?
│      → agent.active_permissions()    (§ 4.7.6)
│
├─ Routing IO through Docker / virtual FS / audit proxy?
│      → from agentao.harness.protocols import FileSystem, ShellExecutor
│        (Part 2.2 / Part 6.4)
│
└─ Anything else? → check Appendix A.10, then file an issue.
```

## TL;DR

- **`agentao.harness` is the stable, schema-snapshotted, forward-compatible host surface.** Pin to it for production code.
- **Three surfaces**: `events()` for streams, `active_permissions()` for policy snapshots, `harness.protocols` for capability injection.
- **`events()` is not a replacement for Transport** — they're complementary. Use Transport for UI streaming, `events()` for audit / observability.
- **`isinstance`-dispatch on `HarnessEvent`** to route to the right handler. The three event types are orthogonal lifecycle facts, not a hierarchy.
- **30 lines + a database** is all it takes to ship a tenant audit pipeline that survives release upgrades.

→ Reference dive: [Appendix A.10 · Embedded Harness Contract](/en/appendix/a-api-reference#a-10-embedded-harness-contract)
→ Schemas: [`docs/schema/harness.events.v1.json`](https://github.com/jin-bo/agentao/blob/main/docs/schema/harness.events.v1.json)
→ Design rationale: [`docs/design/embedded-harness-contract.md`](https://github.com/jin-bo/agentao/blob/main/docs/design/embedded-harness-contract.md)

→ Next: [Part 5 · Extensibility](/en/part-5/)
