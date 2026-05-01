# Embedded Harness Contract

**Status:** Design record. Decision captured 2026-04-30. Implementation: 0.3.1 (`agentao.host`).
**Audience:** Agentao maintainers and host application integrators.
**Related docs:** `docs/implementation/EMBEDDED_HARNESS_IMPLEMENTATION_PLAN.md`,
`docs/implementation/EMBEDDED_HARNESS_PROTOCOL_PLAN.md`,
`docs/implementation/EMBEDDED_HARNESS_CONTRACT_IMPLEMENTATION_PLAN.md`,
`docs/design/metacognitive-boundary.md`.

## Scope (read this first)

The harness contract covers three pillars: **observability events**
(tool / subagent / permission-decision lifecycle), the **ACP schema
surface** (host-facing request, response, notification models), and
**permission state** (`ActivePermissions` snapshot).

It is **not** a complete chat runtime. To drive an agent turn, hosts
call `Agentao.arun()`. To render streaming chat UI, hosts consume the
internal `Transport` / `AgentEvent` stream or the ACP protocol — those
carry the assistant text, reasoning, and raw tool I/O that this stable
contract intentionally omits. The harness exists so an embedder can
audit, visualise, and gate Agentao without coupling to internal event
shapes; it does not replace the in-process runtime entry points.

When extending this document, do not refer to `HostEvent` as the
whole of the harness contract. The events surface is one of three
pillars, not the contract itself.

## Problem

Agentao's long-term embedding shape is a harness that runs inside another
application, not a Codex-style platform server that owns the whole client state.
That distinction changes the public API design.

A host application usually already owns:

- its task tree and job database;
- its UI and notification model;
- its audit and observability pipeline;
- its permission prompts and product policy;
- its plugin and deployment packaging.

If Agentao exposes many queryable internal stores (`hooks/list`, agent graph
tables, MCP status tables, config write APIs), the host must reconcile two
sources of truth: the host's state and Agentao's state. That is the wrong default
for an embedded harness.

The host-facing contract should be event-first: Agentao emits lifecycle facts;
the host decides what to store, render, ignore, or correlate.

## Decision

Agentao separates two surfaces:

1. **Harness API** — stable host-facing data models, simple getters, and event
   streams. This is the compatibility contract for embedded applications.
2. **CLI/local product surface** — commands, local state stores, diagnostics
   lists, reload commands, and display helpers. These may subscribe to the same
   events, but they are not the harness API.

Store/list/reload features may exist for the CLI, but they must not be promoted
to host-facing API unless a host truly needs Agentao to own that state.

## Non-Goals

The following are explicitly out of scope for the harness contract MVP:

- Codex-style app-server platform APIs.
- Remote plugin sharing.
- External agent session import.
- Generated client SDKs and a full schema-generation pipeline.
- Public agent graph store APIs.
- Public hook list/disable APIs.
- Public MCP reload APIs.
- Per-rule permission provenance in the Codex style.

## Guardrail

Before adding anything to the harness API, ask:

> Does a host application need this stable contract to embed Agentao safely,
> explainably, and without reading Agentao logs or private files?

If the answer is "the CLI wants to display or manage it", it belongs first in the
CLI/local product surface, not in the harness API.

## Harness API MVP

### 1. ACP Pydantic models and schema snapshot

ACP request, response, notification, and error payloads that are part of the
host-facing protocol must be represented as Pydantic models.

Each release should include a JSON schema snapshot derived from those models.
This is a light contract discipline, not a full external spec toolchain.

### 2. `active_permissions()` getter

Expose a small typed getter for the currently active permission policy:

```python
{
    "mode": "workspace-write",
    "rules": [...],
    "loaded_sources": [
        "preset:workspace-write",
        "project:.agentao/permissions.json",
        "injected:host"
    ]
}
```

Rules:

- `loaded_sources` is a list, not a collapsed `source: mixed` field.
- The getter does not promise per-rule provenance in MVP.
- Hosts that need user-facing provenance can combine `loaded_sources` with their
  own injected policy metadata.

### 3. Public event stream MVP

The first public event stream contains exactly these event families:

- `ToolLifecycleEvent`
- `SubagentLifecycleEvent`
- `PermissionDecisionEvent`

These three cover the first questions a host or CLI needs answered:

- What is the agent doing now?
- Did it spawn child work?
- Why was a capability allowed, denied, or prompted?

#### ToolLifecycleEvent

Tool events are public in MVP because the CLI is Agentao's canonical first host.
External hosts should not have to use a different mechanism to render basic
"agent is running a tool" state.

The public payload is a lifecycle envelope, not a dump of tool internals.

Minimum fields:

- `session_id`
- `turn_id`
- `tool_call_id`
- `tool_name`
- `phase`: `started`, `completed`, or `failed`
- `started_at`
- `completed_at`
- `outcome`
- `summary`
- `error_type`

`phase="failed"` covers both execution errors and cancellation. Hosts must read
`outcome` to distinguish `error` from `cancelled`.

Default public events must not include full tool arguments, full stdout/stderr,
full diffs, MCP raw responses, or other potentially large or sensitive fields.
Those belong in redacted summaries or artifact references when needed.

#### SubagentLifecycleEvent

Minimum fields:

- `session_id`
- `parent_session_id`
- `parent_task_id`
- `child_session_id`
- `child_task_id`
- `phase`: `spawned`, `completed`, `failed`, or `cancelled`
- `task_summary`
- `started_at`
- `completed_at`
- `error_type`

Agentao emits lineage facts. Hosts and the CLI may build graph displays or stores
from those facts, but a graph store is not a public harness API.

`cancelled` is a distinct subagent phase because lineage tracking benefits from
explicit cancellation in the phase value. Tool cancellation remains
`phase="failed"` with `outcome="cancelled"` to keep tool-call events compact.

#### PermissionDecisionEvent

Permission decision events fire on every permission decision, not only on deny or
prompt. The payload includes:

- `session_id`
- `turn_id`
- `tool_call_id`
- `tool_name`
- `decision_id`
- `outcome`: `allow`, `deny`, or `prompt`
- `mode`
- `matched_rule`
- `reason`
- `loaded_sources`
- `decided_at`

Hosts that only need prompts or denials can filter the event stream. Emitting
every decision keeps audit and escalation UI paths consistent.

### 4. Event delivery contract

The event stream API is part of the harness contract and must be specified before
adding more public event families.

MVP contract:

- The primary API shape is an async iterator.
- Callback adapters may be added later, but callbacks are not the primary
  contract.
- `session_id=None` in `agent.events(session_id=None)` is a subscription filter,
  not a payload value. Emitted public events always carry a non-empty
  `session_id`.
- Events are scoped by session id.
- Ordering is guaranteed within one session.
- Within one `tool_call_id`, `PermissionDecisionEvent` precedes
  `ToolLifecycleEvent` with `phase="started"`.
- No global ordering is guaranteed across sessions.
- Events emitted before the first subscription are discarded; the stream is not
  a replay mechanism.
- A subscriber that starts mid-turn receives only future events.
- Backpressure is host-pulled: slow consumers apply pressure by not advancing the
  iterator. Agentao must not grow an unbounded queue.
- Cancellation must close the iterator and release session-local resources
  deterministically.
- When a bounded subscription queue is full, the producer blocks for matching
  events rather than silently dropping them. With a session-scoped subscription,
  only that session is affected; with an all-session subscription, all matching
  public events can apply backpressure.
- In the no-subscriber state, public events are dropped immediately and must not
  block the agent loop.

### 5. Schema discipline for all public payloads

Every data structure in the harness surface must be a Pydantic model and must be
included in the release schema snapshot.

This applies equally to ACP payloads and public event payloads. ACP cannot be
strict while events remain ad hoc dictionaries.

Common field rules:

- All timestamp fields use RFC 3339 UTC strings, for example
  `2026-04-30T01:02:03.456Z`.
  Agentao uses the canonical `Z`-suffix form; offsets such as `+00:00` are
  intentionally rejected for stable snapshots and host parsing.
- Public summary fields such as `summary`, `task_summary`, and `reason` are
  redacted and truncated host-facing strings. They must not contain raw user
  input, raw tool arguments, raw tool output, or raw MCP payloads.
- `error_type` is a stable identifier string, such as a Python exception class
  name or a documented error code. Human-readable error text belongs in
  `summary` or `reason`.
- `error_type` is set only for actual failures. Cancellation events carry
  `error_type=None`.

### 6. Required tests

The event contract must have executable tests, not just prose.

Minimum coverage:

- same-session event ordering;
- permission decision before tool lifecycle `started` for one tool call;
- backpressure or bounded-buffer behavior;
- cancellation and cleanup;
- runtime identity propagation for `session_id`, `turn_id`, `tool_call_id`, and
  `decision_id`;
- schema snapshot stability for ACP and public event models;
- RFC 3339 UTC timestamp formatting with canonical `Z` suffix;
- stable `error_type` identifiers for repeatable failures;
- `PermissionDecisionEvent` firing on `allow`, `deny`, and `prompt`.

## Deferred Harness Items

The following are valid harness candidates, but not MVP:

1. **Hook key contract.**
   Write `docs/design/hook-key-contract.md` before exposing host-facing
   `disabled_hook_keys`.
2. **`disabled_hook_keys` constructor/session parameter.**
   Only expose this after hook keys are stable.
3. **MCP lifecycle events.**
   Candidate phases: `starting`, `connected`, `auth_failed`,
   `tool_discovery_failed`, `disconnected`.
4. **Hook lifecycle events.**
   Candidate phases: `started`, `completed`, `failed`, `skipped`.
5. **Per-session stream dispatch and multi-subscriber fan-out.**
   MVP hosts can filter one stream by `session_id`. Separate independent
   iterators require a tested pub/sub layer and are deferred.

## CLI and Local Product Surface

The following are useful, but they are not host-facing harness API:

- hooks list/disable with local persisted state;
- agent graph display/store derived from `SubagentLifecycleEvent`;
- local plugin export/import;
- `/mcp reload`;
- config batch write helpers;
- local diagnostics renderers.

CLI implementations should subscribe to the same public or internal events that
hosts use. They may maintain local stores for display and recovery, but those
stores are implementation details.

## Internal Events

Agentao may have richer internal events for replay, debugging, and CLI rendering.
Internal events can exist before their public schema is frozen.

Rules:

- Internal events must not be documented as harness API.
- Public events must use the schema discipline above.
- Promotion from internal to public requires an explicit schema and compatibility
  review.

## Implementation Notes

The event stream should reuse the existing async runtime and cancellation
mechanisms introduced for embedded harness work. It should not invent an
independent background event loop or cancellation system.

The first implementation should treat the CLI as the canonical first host. If the
CLI needs a public event to render basic runtime state, that event is a good MVP
candidate. If the CLI needs a local management command, that command is not
automatically a harness candidate.

## Acceptance Criteria

This design is implemented when:

- ACP host-facing payloads are Pydantic models with release schema snapshots.
- Public event payloads are Pydantic models with release schema snapshots.
- `active_permissions()` returns mode, rules, and `loaded_sources`.
- The public event stream exposes tool lifecycle, subagent lifecycle, and
  permission decision events.
- `PermissionDecisionEvent` fires on every decision and includes `outcome`.
- Delivery contract tests cover ordering, backpressure, cancellation, and schema
  stability.
- No public graph store, hook list/disable, MCP reload, or remote plugin sharing
  API is introduced as part of the harness MVP.
