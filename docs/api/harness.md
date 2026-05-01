# Embedded Harness API

**Package:** `agentao.harness`
**Status:** Stable, since 0.3.1.
**Source design:** [`docs/design/embedded-harness-contract.md`](../design/embedded-harness-contract.md)
**Implementation plan (historical):** [`docs/implementation/EMBEDDED_HARNESS_CONTRACT_IMPLEMENTATION_PLAN.md`](../implementation/EMBEDDED_HARNESS_CONTRACT_IMPLEMENTATION_PLAN.md)

The harness API is the host-facing compatibility boundary for embedding
Agentao inside another application. Internal runtime types
(`AgentEvent`, `ToolExecutionResult`, `PermissionEngine`) are
intentionally not part of this surface.

> **Import discipline.** All public types live on the `agentao.harness`
> module — they are deliberately **not** re-exported from the top-level
> `agentao` package. Always `from agentao.harness import ...`; do not
> rely on `agentao.ToolLifecycleEvent` or similar to exist.

## Public exports

| Symbol | Purpose |
|---|---|
| `ActivePermissions` | Read-only snapshot of the active permission policy. |
| `ToolLifecycleEvent` | Public envelope for one tool call's lifecycle. |
| `SubagentLifecycleEvent` | Lineage fact for a sub-agent task/session. |
| `PermissionDecisionEvent` | Per-decision permission projection. |
| `HarnessEvent` | Discriminated union of the three event models. |
| `RFC3339UTCString` | Constrained timestamp type used by all public events. |
| `export_harness_event_json_schema()` | Canonical JSON schema for the events + permissions surface. |
| `export_harness_acp_json_schema()` | Canonical JSON schema for the host-facing ACP payload surface. |

## Schema snapshot policy

Each release ships a checked-in JSON schema snapshot:

- `docs/schema/harness.events.v1.json` — events + permissions
- `docs/schema/harness.acp.v1.json` — ACP payloads

`tests/test_harness_schema.py` regenerates the schema from the Pydantic
models and asserts byte-equality with the snapshot using canonical JSON
(`json.dumps(..., sort_keys=True)`). A model change that shifts the
wire form must update both the model and the snapshot in the same PR.

Compatibility rules:

- Adding an optional field is backwards-compatible.
- Removing a field, renaming a field, changing enum values, or
  changing field semantics requires a schema version bump and a release
  note.
- Public events must not reuse the internal `AgentEvent.data` payload
  directly; projection/redaction lives in
  `agentao/harness/projection.py`.
- Public summary fields (`summary`, `task_summary`, `reason`) are
  redacted/truncated host-facing strings — never raw user input,
  arguments, tool output, or policy internals.
- All timestamps use the canonical `Z`-suffix form, e.g.
  `2026-04-30T01:02:03.456Z`. Offsets like `+00:00` are intentionally
  rejected for stable snapshots.

## Runtime identity contract

Public events depend on a small set of stable id fields. The helpers
live in `agentao/runtime/identity.py` and are wired into planning, tool
execution, permission decisions, and sub-agent spawn at the runtime
boundary.

| Field | Source |
|---|---|
| `session_id` | Persisted session id when available; UUID4 fallback at `Agentao` construction. |
| `turn_id` | UUID4 minted at turn entry (`agentao/runtime/turn.py`). One user-submitted agentic loop. |
| `tool_call_id` | LLM-provided tool call id when present, UUID4 fallback otherwise; normalized once at planning and reused. |
| `decision_id` | UUID4 per permission decision. |
| `child_task_id` / `child_session_id` | Captured at sub-agent spawn time, not inferred at completion. |

Uniqueness scope for `tool_call_id` is `(session_id, turn_id, tool_call_id)`;
provider-generated ids are not assumed globally unique.

## Event subscription semantics

`Agentao.events(session_id: str | None = None)` returns an async
iterator over `HarnessEvent`. Pass `session_id=` to filter; pass `None`
to subscribe to every session owned by this `Agentao` instance.

- Same-session ordering is guaranteed.
- Within one `tool_call_id`, `PermissionDecisionEvent` is emitted before
  `ToolLifecycleEvent(phase="started")`.
- Cross-session global ordering is not guaranteed.
- Events emitted before the first subscription are discarded — there is
  **no replay**. A subscriber that starts mid-turn receives only
  future events.
- Backpressure is host-pulled. The implementation does not grow an
  unbounded queue; when a bounded subscription queue is full, the
  producer blocks for matching events.
- Cancellation of the iterator releases queue/subscription resources.
- MVP supports one public stream consumer per `Agentao` instance.

| State | Semantics |
|---|---|
| No subscriber | Drop public events immediately; do not block the agent loop. |
| Subscriber starts after events were emitted | No replay; subscriber only receives future events. |
| Subscriber queue has capacity | Enqueue matching events in emission order. |
| Subscriber queue is full | Block producer for matching events until capacity is available or the stream is cancelled. |
| Subscriber cancels / iterator closes | Release queue resources; future events follow the "No subscriber" row. |

## Non-goals

- Public agent graph store / descendants API.
- Host-facing hooks list/disable API.
- Host-facing MCP reload API.
- MCP and hook lifecycle public events.
- Local plugin export/import; remote plugin share.
- External session import.
- Generated client SDKs.
- A full schema governance pipeline beyond checked-in snapshots.

These are deliberately out of scope to keep the embedded harness narrow.
The CLI may build on the same events for its own UI, but its stores and
commands are not promoted to the harness API.
