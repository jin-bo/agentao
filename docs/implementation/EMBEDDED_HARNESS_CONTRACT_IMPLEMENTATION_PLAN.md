# Embedded Harness Contract — Implementation Plan

**Date:** 2026-04-30
**Status:** Draft implementation plan; design locked in
**Source design:** `docs/design/embedded-harness-contract.md`
**Scope:** Turn the embedded harness contract into a staged PR plan with concrete
code targets, tests, and non-goals.

---

## TL;DR

Implement the harness contract as a narrow host-facing layer:

1. Add Pydantic public models and release schema snapshots.
2. Add `active_permissions()` with `loaded_sources`.
3. Define runtime identity fields (`session_id`, `turn_id`, `tool_call_id`,
   `decision_id`, child task/session ids) before emitting events.
4. Add a public async event stream with exactly three MVP families:
   `ToolLifecycleEvent`, `SubagentLifecycleEvent`, and
   `PermissionDecisionEvent`.
5. Define and test delivery semantics: same-session ordering, host-pulled
   backpressure, cancellation cleanup, and schema stability.
6. Use the CLI as the canonical first host, but keep CLI stores/list/reload
   commands out of the harness API.

Do **not** implement public graph stores, hooks list/disable, MCP reload, remote
plugin sharing, external session import, generated SDKs, or a Codex-style
app-server surface in this plan.

---

## Current State

Already shipped or available:

- `Agentao.arun()` async public surface.
- `working_directory` is explicit.
- `build_from_environment()` owns CLI-style env/cwd/home loading.
- `AgentEvent` has `schema_version`.
- `AsyncToolBase` exists and dispatch is host-loop aware.
- Capabilities such as filesystem, shell, memory, MCP registry, replay, sandbox,
  and background task store are injectable.
- Internal structured events already exist in `agentao/transport/events.py`.

Gaps this plan closes:

- ACP payloads are dataclasses/dicts, not public Pydantic schema models.
- Public event payloads are not modeled as stable host-facing schemas.
- There is no host-facing async event stream contract.
- Runtime identity fields required by public events are not yet defined as a
  cross-runtime contract.
- Permission rules do not expose a minimal active policy snapshot with
  `loaded_sources`.
- Runtime events are not yet split into public lifecycle envelopes versus
  internal replay/debug payloads.

---

## Non-Goals

These are intentionally outside this implementation plan:

- Public agent graph store or descendants API.
- Host-facing hooks list/disable API.
- Host-facing MCP reload API.
- MCP lifecycle public events.
- Hook lifecycle public events.
- Local plugin export/import.
- Config batch-write APIs.
- Remote plugin share.
- External session import.
- Generated client SDKs.
- A full schema governance pipeline beyond checked-in schema snapshots.
- Multi-subscriber event fan-out unless a concrete implementation PR proves it
  is needed for the MVP.

---

## Public Module Shape

Add a small public harness package:

```text
agentao/harness/
  __init__.py
  models.py          # Pydantic public payload models
  events.py          # event stream primitives
  projection.py      # redaction/sanitization from runtime state to public models
  schema.py          # schema export helpers

agentao/runtime/
  identity.py        # internal id generation and normalization helpers
```

Exports:

- `ActivePermissions`
- `ToolLifecycleEvent`
- `SubagentLifecycleEvent`
- `PermissionDecisionEvent`
- `HarnessEvent`
- `EventStream`
- `export_harness_event_json_schema()`
- `export_harness_acp_json_schema()`

`agentao/harness/__init__.py` is updated cumulatively across PRs. The export
list above is the final state. PR 1 exports the models and
`export_harness_event_json_schema()` only; PR 2 adds
`export_harness_acp_json_schema()`; PR 4 adds `EventStream`.

Do not export runtime id generation helpers from `agentao.harness`. Runtime ids
are generated and normalized internally in `agentao/runtime/identity.py`; the
harness surface only exposes the resulting id fields on public models.

Dependency rule: add `pydantic>=2` as a direct project dependency in PR 1. The
public model contract uses Pydantic v2 features such as discriminated unions via
`Annotated` and `Field(discriminator=...)`; transitive dependencies from MCP or
any other package are not acceptable for this public API surface.

Add public API docs in PR 1:

```text
docs/api/harness.md
docs/api/harness.zh.md
```

These docs should describe the public models, schema snapshot policy, event
subscription semantics, and the "events are not replayed" rule.

---

## Runtime Identity Contract

Public events depend on ids that are not all stable in the current runtime. This
contract must be implemented before PR 5-7 event emission work starts.

### `session_id`

- Source: the `Agentao` instance/session id already used for persisted sessions
  when available.
- If no persisted session id exists yet, allocate a UUID at `Agentao`
  construction and treat it as the in-memory harness session id.
- Public events always use a string `session_id`; they do not use `None` or an
  empty string.
- ACP sessions should map their ACP `sessionId` to this field when an ACP
  session owns the runtime.

### `turn_id`

- Boundary: one user-submitted agentic loop, i.e. one `Agentao.chat()` or
  `Agentao.arun()` call.
- Generation: create a UUID4 `turn_id` at turn entry in `agentao/runtime/turn.py`
  and store it on turn-local runtime state until the turn exits.
- Public tool and permission events emitted inside that loop carry this `turn_id`.
- Events outside a turn may use `turn_id=None` only if their model explicitly
  allows it.

### `tool_call_id`

- Preferred source: the LLM tool call id when present.
- Fallback: generate a UUID4 when the LLM/tool-call object does not provide a
  stable id.
- The chosen id must be normalized before planning/execution and reused for
  planning, permission decisions, tool lifecycle events, and result formatting.
- PR 1 defines helper types/functions only. The actual wiring into planning,
  permission decisions, tool lifecycle events, and result formatting is owned by
  PR 5 and PR 6 acceptance tests, because those PRs touch the runtime call sites.
- Do not assume provider-generated ids are globally unique; uniqueness is scoped
  to `(session_id, turn_id, tool_call_id)`.

### `decision_id`

- Generation: UUID4 per permission decision.
- Location: generate at the permission-planning boundary where tool name, args,
  current mode, matched rule/fallback, and tool call id are all visible.
- Do not couple `PermissionEngine` directly to event delivery. It may return a
  decision detail object, but event emission belongs at the runtime boundary.

### Child task/session ids

- `child_task_id` comes from the background/sub-agent task id. If the current
  path lacks a task id, generate one at spawn time before the child starts.
- `child_session_id` is populated when the child owns a runtime session. It is
  allowed to be `None` for MVP paths that only have task-level identity.
- Parent ids should be captured at spawn time, not inferred from global state
  when the child completes.

---

## Model Contracts

Timestamp fields use a constrained string type, not unconstrained prose:

```python
RFC3339UTCString = Annotated[
    str,
    Field(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$",
        description=(
            "RFC 3339 UTC timestamp with the canonical 'Z' suffix; offsets such "
            "as +00:00 are intentionally rejected for canonical form."
        ),
    ),
]
```

### ActivePermissions

```python
class ActivePermissions(BaseModel):
    mode: Literal["read-only", "workspace-write", "full-access", "plan"]
    rules: list[dict[str, Any]]
    loaded_sources: list[str]
```

Rules:

- `loaded_sources` uses stable string labels:
  - `preset:<mode>`
  - `project:<relative-or-absolute-path>`
  - `user:<path>`
  - `injected:<name>`
- MVP does not expose per-rule provenance.
- `rules` is a JSON-safe projection, not a mutable reference to
  `PermissionEngine.rules`.

### ToolLifecycleEvent

Public lifecycle envelope only:

```python
class ToolLifecycleEvent(BaseModel):
    event_type: Literal["tool_lifecycle"] = "tool_lifecycle"
    session_id: str
    turn_id: str | None = None
    tool_call_id: str
    tool_name: str
    phase: Literal["started", "completed", "failed"]
    started_at: RFC3339UTCString
    completed_at: RFC3339UTCString | None = None
    outcome: Literal["ok", "error", "cancelled"] | None = None
    summary: str | None = None
    error_type: str | None = None
```

Must not include full tool args, raw stdout/stderr, raw diffs, MCP raw responses,
or unredacted large outputs by default.

`phase="failed"` covers both execution errors and cancellation. Hosts must read
`outcome` to distinguish `outcome="error"` from `outcome="cancelled"`.

`error_type` is a stable identifier, such as a Python exception class name or a
documented error code. It is not a free-form human-readable message.
For cancellation, `error_type` is `None`.

### SubagentLifecycleEvent

```python
class SubagentLifecycleEvent(BaseModel):
    event_type: Literal["subagent_lifecycle"] = "subagent_lifecycle"
    session_id: str
    parent_session_id: str | None = None
    parent_task_id: str | None = None
    child_session_id: str | None = None
    child_task_id: str
    phase: Literal["spawned", "completed", "failed", "cancelled"]
    task_summary: str | None = None
    started_at: RFC3339UTCString
    completed_at: RFC3339UTCString | None = None
    error_type: str | None = None
```

This emits lineage facts. CLI graph display may build a store from these events,
but the store is not a harness API.

`task_summary` is a redacted and truncated host-facing string, not raw user
input or raw child-agent prompt text.

`error_type` follows the same stable-identifier rule as `ToolLifecycleEvent`.
It is set only for `phase="failed"` and is `None` for `phase="cancelled"`.

Unlike `ToolLifecycleEvent`, `SubagentLifecycleEvent` exposes `cancelled` as a
distinct phase because subagent lineage tracking benefits from explicit
cancellation in the phase value. `ToolLifecycleEvent` keeps cancellation under
`phase="failed", outcome="cancelled"` to keep the tool-call shape compact.

### PermissionDecisionEvent

```python
class PermissionDecisionEvent(BaseModel):
    event_type: Literal["permission_decision"] = "permission_decision"
    session_id: str
    turn_id: str | None = None
    tool_call_id: str | None = None
    tool_name: str
    decision_id: str
    outcome: Literal["allow", "deny", "prompt"]
    mode: Literal["read-only", "workspace-write", "full-access", "plan"]
    matched_rule: dict[str, Any] | None = None
    reason: str | None = None
    loaded_sources: list[str]
    decided_at: RFC3339UTCString
```

Semantics:

- Fire on **every** permission decision.
- Hosts filter if they only care about `deny` or `prompt`.
- `prompt` means the runtime needs an approval/confirmation path; it does not
  imply the user has accepted or rejected yet.
- `matched_rule` intentionally has no per-rule source label in MVP. Use
  `loaded_sources` for global context; per-rule provenance is deferred by
  design.
- `reason` is a redacted and truncated host-facing string, not raw user input,
  raw tool args, raw tool output, or raw policy internals.
- This event can be high volume. Hosts that do not render `allow` decisions must
  still drain an active event iterator to avoid applying backpressure.

### HarnessEvent

```python
HarnessEvent = Annotated[
    ToolLifecycleEvent | SubagentLifecycleEvent | PermissionDecisionEvent,
    Field(discriminator="event_type"),
]
```

---

## Event Delivery Contract

MVP API:

```python
async for event in agent.events(session_id=None):
    ...
```

Contract:

- Primary shape is an async iterator.
- `session_id=None` in `agent.events(session_id=None)` is a subscription filter,
  not a payload value. Emitted events always carry a non-empty string
  `session_id`.
- Events are scoped by `session_id`. `agent.events(session_id=None)` subscribes
  to future events from all sessions owned by that `Agentao` instance.
- Same-session ordering is guaranteed.
- Within one `tool_call_id`, `PermissionDecisionEvent` must be emitted before
  `ToolLifecycleEvent` with `phase="started"`.
- Cross-session global ordering is not guaranteed.
- Events emitted before the first subscription are discarded, not buffered for a
  future subscriber.
- A subscriber that starts mid-turn receives only future events.
- Backpressure is host-pulled. The implementation must not grow an unbounded
  queue.
- Cancellation of the iterator must release queue/subscription resources.
- When a bounded subscription queue is full, the producer blocks for matching
  events rather than silently dropping them.
- If a subscriber is scoped to one session, only matching session producers can
  be blocked by that subscriber. If a subscriber listens to all sessions, any
  matching public event can apply backpressure.
- MVP supports one public event stream consumer per `Agentao` instance unless the
  implementation PR explicitly adds tested fan-out. This avoids introducing a
  pub/sub subsystem before there is a real need.
- `Agentao.events()` returns an async iterator bound to the runtime event loop.
  Hosts iterating from a different event loop or a synchronous thread must adapt
  via standard asyncio interop, such as `asyncio.run_coroutine_threadsafe`. MVP
  does not provide a synchronous iterator wrapper.

Subscriber lifecycle and overflow matrix:

| State | Semantics |
|---|---|
| No subscriber | Drop public events immediately. Do not block the agent loop. |
| Subscriber starts after events were emitted | No replay; subscriber only receives future events. |
| Subscriber queue has capacity | Enqueue matching events in emission order. |
| Subscriber queue is full | Block producer for matching events until capacity is available or the stream is cancelled. |
| Subscriber cancels / iterator closes | Release queue resources; future events follow the "No subscriber" row. |

Implementation hint:

- Keep internal `Transport.emit()` working.
- Add a separate harness event publisher that receives sanitized public models.
- Put runtime-to-public projection and redaction in `agentao/harness/projection.py`.
- Do not expose raw `AgentEvent.data` as public harness payload.

---

## PR Sequence

### PR 1 — Public model foundation, ids, and event schema snapshot

Files:

- `pyproject.toml`
- `agentao/harness/__init__.py`
- `agentao/harness/models.py`
- `agentao/harness/projection.py`
- `agentao/harness/schema.py`
- `agentao/runtime/identity.py`
- `tests/test_harness_schema.py`
- `docs/schema/harness.events.v1.json`
- `docs/api/harness.md`
- `docs/api/harness.zh.md`

Work:

1. Add `pydantic>=2` as a direct dependency.
2. Define `ActivePermissions`, the three event models, and the discriminated
   `HarnessEvent`.
3. Define the constrained `RFC3339UTCString` timestamp type used by all public
   timestamp fields.
4. Add runtime id helper functions or types in `agentao/runtime/identity.py` for:
   - `session_id`
   - `turn_id`
   - `tool_call_id`
   - `decision_id`
   - child task/session ids
   These helpers are not exported from `agentao.harness`.
5. Add `export_harness_event_json_schema()`.
6. Add checked-in event schema snapshot:
   `docs/schema/harness.events.v1.json`.
7. Add `docs/api/harness.md` and `docs/api/harness.zh.md` with public model and
   event subscription semantics.
8. Test that generated event schema matches the checked-in snapshot using
   normalized JSON.

Acceptance:

- `uv run pytest tests/test_harness_schema.py` passes.
- `pyproject.toml` directly declares `pydantic>=2`.
- `agentao/runtime/identity.py` owns id generation/normalization helpers; the
  public harness package does not export them.
- `docs/schema/harness.events.v1.json` contains only event/permission harness
  models; ACP has its own snapshot in PR 2.
- Public timestamp fields validate as RFC 3339 UTC strings and reject local or
  timezone-less strings.
- Snapshot comparison uses canonical JSON such as
  `json.dumps(schema, sort_keys=True)` and normalizes `$ref` ordering to reduce
  Pydantic patch-version flapping.
- No runtime behavior changes.

### PR 2 — ACP public schema projection

Files:

- `agentao/acp/models.py`
- `agentao/acp/protocol.py`
- `agentao/harness/__init__.py`
- `agentao/harness/models.py` or `agentao/acp/schema.py`
- `agentao/harness/schema.py`
- `tests/test_acp_schema.py`
- `docs/schema/harness.acp.v1.json`

Work:

1. Add Pydantic models for host-facing ACP payloads:
   - initialize request/response;
   - session/new request/response;
   - session/prompt request/response;
   - session/cancel request/response;
   - request_permission / ask_user payloads;
   - common error payloads.
2. Keep existing dataclass internals if needed; add adapters rather than
   rewriting the whole ACP server in one PR.
3. Export ACP models through `docs/schema/harness.acp.v1.json`.
4. Add `export_harness_acp_json_schema()` for the ACP snapshot path.

Acceptance:

- Existing ACP tests still pass.
- New schema tests cover representative ACP payloads.
- ACP schema snapshot is independent from
  `docs/schema/harness.events.v1.json`.
- ACP schema export uses `export_harness_acp_json_schema()`; event schema export
  uses `export_harness_event_json_schema()`.
- No wire-shape change unless explicitly documented in the PR.

### PR 3 — `active_permissions()` and loaded sources

Files:

- `agentao/permissions.py`
- `agentao/agent.py`
- `agentao/harness/models.py`
- `tests/test_active_permissions.py`
- `docs/CONFIGURATION.md`
- `docs/CONFIGURATION.zh.md`

Work:

1. Track loaded permission sources in `PermissionEngine`.
2. Add `PermissionEngine.active_permissions() -> ActivePermissions`.
3. Add `Agentao.active_permissions()` as the host-facing convenience wrapper.
4. Include preset source for the current mode.
5. Include project/user file sources only when the file was loaded or attempted
   according to the current loader semantics. Be explicit in tests.
6. Do not add per-rule provenance.

Acceptance:

- Getter returns JSON-safe `rules`.
- Getter includes `loaded_sources`, not `source: mixed`.
- Getter returns a cached projection of the active policy. It must not re-read
  permission files on every call, because permission decisions may call it on the
  tool execution hot path.
- Tests cover project-only, user+project, preset-only, and injected/empty cases
  if injection exists in current code.

### PR 4 — Event stream primitive and delivery contract tests

Files:

- `agentao/harness/__init__.py`
- `agentao/harness/events.py`
- `agentao/agent.py`
- `agentao/runtime/turn.py`
- `tests/test_harness_event_stream.py`

Work:

1. Add `EventStream` with async iterator semantics.
2. Add `Agentao.events(session_id: str | None = None)`.
3. Implement the subscriber lifecycle matrix from this plan:
   - no subscriber drops events;
   - mid-turn subscription sees only future events;
   - bounded queues apply backpressure for matching events;
   - cancellation releases subscription resources.
4. Generate and thread `turn_id` at `agentao/runtime/turn.py` entry.
5. Implement the `Agentao`-construction-time UUID fallback for `session_id` when
   no persisted session id exists, per the Runtime Identity Contract.
6. Keep the stream usable without starting a real LLM call in tests.

Acceptance tests:

- same-session ordering is preserved;
- cross-session tests do not assert global order;
- events emitted within one turn share the same `turn_id`;
- events emitted from different turns use different `turn_id` values;
- identity tests cover `Agentao`-construction-time session id fallback
  allocation;
- no-subscriber events do not block and are not replayed;
- mid-turn subscribers receive only future events;
- slow consumer applies bounded backpressure for matching events;
- cancelling the iterator releases resources;
- a second subscriber behavior is explicit and tested:
  - either rejected with a clear error in MVP;
  - or supported with tested fan-out.

### PR 5 — Tool lifecycle public events

Files:

- `agentao/runtime/tool_executor.py`
- `agentao/runtime/tool_runner.py`
- `agentao/harness/projection.py`
- `agentao/harness/models.py`
- `tests/test_harness_tool_events.py`

Work:

1. Emit `ToolLifecycleEvent(started)` before tool execution.
2. Emit `ToolLifecycleEvent(completed)` after success.
3. Emit `ToolLifecycleEvent(failed)` after exceptions or cancelled execution.
4. Use `agentao/harness/projection.py` for redacted/summarized result text.
5. Do not expose raw args or raw outputs in public event payloads.
6. Ensure AsyncTool cancellation emits the terminal public event only after the
   async cleanup acknowledgement has settled.

Acceptance:

- Sync `Tool` and `AsyncToolBase` both emit lifecycle events.
- Failed tools emit `failed` with `error_type`.
- The same exception class produces the same `error_type` value across runs.
- Events include stable normalized `tool_call_id`, using the LLM-provided id when
  available and a generated UUID fallback when absent.
- Permission and tool lifecycle events for the same tool call reuse the same
  `tool_call_id`.
- Tests assert raw tool args/output are not present in the public payload.
- AsyncTool cancellation produces one terminal event with `outcome="cancelled"`
  after cleanup acknowledgement, not before.

### PR 6 — Permission decision public events

Files:

- `agentao/permissions.py`
- `agentao/runtime/tool_planning.py`
- `agentao/runtime/tool_executor.py`
- `agentao/runtime/tool_runner.py`
- `agentao/harness/projection.py`
- `agentao/harness/models.py`
- `tests/test_harness_permission_events.py`

Work:

1. Return enough decision detail from permission evaluation to build a public
   event at the runtime boundary where `session_id`, `turn_id`, and
   `tool_call_id` are known.
2. Generate `decision_id` for every permission decision.
3. Emit `PermissionDecisionEvent` on every decision.
4. Map fallback-to-tool-confirmation to `outcome="prompt"`.
5. Include `loaded_sources` from `active_permissions()`.
6. Avoid coupling `PermissionEngine` to event delivery directly.

Acceptance:

- Tests cover `allow`, `deny`, and `prompt`.
- Events fire for every decision path, not only explicit JSON-rule matches.
- Tests assert `decision_id` uniqueness per decision.
- Tests assert `PermissionDecisionEvent` precedes
  `ToolLifecycleEvent(phase="started")` for the same `tool_call_id`.
- `matched_rule` is present when a rule matched and `None` when the decision came
  from fallback semantics.
- Tests document that `matched_rule` has no source label in MVP.

### PR 7 — Subagent lifecycle public events

Files:

- `agentao/agents/bg_store.py`
- `agentao/tools/agents.py`
- `agentao/agents/tools.py`
- `agentao/harness/projection.py`
- `agentao/harness/models.py`
- `tests/test_harness_subagent_events.py`

Work:

1. Confirm the actual spawn and terminal update points before editing:
   `agentao/agents/bg_store.py` is the persistence/state owner, while
   `agentao/tools/agents.py` and `agentao/agents/tools.py` are candidate call
   sites/consumers.
2. Emit `SubagentLifecycleEvent(spawned)` when a child task/session is created.
3. Emit terminal events for `completed`, `failed`, and `cancelled`.
4. Include parent/child ids captured at spawn time.
5. Keep any CLI graph display/store separate from the harness API.

Acceptance:

- Spawned and terminal events use the same child id.
- Cancelled tasks emit `cancelled`.
- Failed tasks emit `failed` with `error_type`.
- No public descendants/query API is introduced.

### PR 8 — CLI as first-host adapter

Files:

- `agentao/cli/*`
- `agentao/display.py`
- `tests/test_cli_harness_events.py` or focused existing CLI tests

Work:

1. Route the CLI tool-running spinner/status through `ToolLifecycleEvent`.
2. Read the mode/status line from `Agentao.active_permissions()` where that line
   reflects permission mode.
3. Keep CLI-only stores and commands out of `agentao.harness`.
4. Preserve existing user-visible CLI behavior unless a PR explicitly improves it.

Acceptance:

- CLI can render basic tool-running state from `ToolLifecycleEvent`.
- CLI status/mode display uses `active_permissions()` rather than reaching into
  private permission-engine state.
- CLI graph display, if added, is fed by `SubagentLifecycleEvent` but remains
  CLI/local product surface.
- No hooks list/disable, MCP reload, plugin export/import, or graph store is
  exposed as host-facing API by this PR.

---

## Deferred Deliverables

These should be tracked after the MVP above, not mixed into the PRs.

### Hook key contract

Create `docs/design/hook-key-contract.md` before exposing any host-facing
`disabled_hook_keys` parameter.

The doc must define:

- key inputs;
- stability guarantees;
- behavior when plugin path, plugin name, event name, matcher, or rule order
  changes;
- migration behavior for old keys.

### `disabled_hook_keys`

After the hook key contract is stable, add constructor/session-level
`disabled_hook_keys`. Do not add host-facing hooks list/disable as part of this.

### MCP lifecycle events

Candidate public event family after a host/CLI need is confirmed:

- `starting`
- `connected`
- `auth_failed`
- `tool_discovery_failed`
- `disconnected`

### Hook lifecycle events

Candidate public event family after plugin hooks are host-relevant:

- `started`
- `completed`
- `failed`
- `skipped`

### Per-session stream dispatch and multi-subscriber fan-out

MVP allows one public stream consumer per `Agentao` instance. Hosts that want one
independent iterator per UI tab/session should filter the all-session stream in
MVP. Separate per-session iterators and multi-subscriber fan-out are deferred
together because both require a tested pub/sub layer rather than a single
bounded stream.

---

## Compatibility Rules

- Public Pydantic models are the compatibility boundary.
- Adding an optional field is allowed.
- Removing a field, renaming a field, changing enum values, or changing field
  semantics requires a schema version bump and release note.
- Public events must not reuse internal `AgentEvent.data` directly.
- Public event payloads must remain JSON-safe.
- Public events must avoid sensitive and large payloads by default.
- All timestamp fields use RFC 3339 UTC strings, for example
  `2026-04-30T01:02:03.456Z`.
- The timestamp contract is the canonical `Z`-suffix subset of RFC 3339 UTC;
  offsets such as `+00:00` are intentionally rejected for stable snapshots and
  host parsing.
- Public summary fields such as `summary`, `task_summary`, and `reason` are
  redacted and truncated host-facing strings. They must never contain raw user
  input, raw tool arguments, raw tool output, raw MCP payloads, or unbounded
  policy internals.
- `error_type` is a stable identifier string, such as a Python exception class
  name or a documented error code. Localized or human-readable error text
  belongs in `summary` or `reason`.
- `error_type` is set only for actual failures. Tool cancellation uses
  `phase="failed", outcome="cancelled", error_type=None`; subagent cancellation
  uses `phase="cancelled", error_type=None`.

---

## Test Matrix

Required tests across the plan:

- `tests/test_harness_schema.py`
  - event schema snapshot matches generated schema using normalized JSON;
  - discriminated union validates each event type;
  - timestamp fields accept RFC 3339 UTC strings and reject timezone-less
    strings and `+00:00` offsets.
- `tests/test_acp_schema.py`
  - ACP schema snapshot matches generated ACP public models using normalized
    JSON.
- `tests/test_active_permissions.py`
  - mode, rules, and loaded sources are correct across config combinations.
- `tests/test_harness_event_stream.py`
  - ordering;
  - no-subscriber drop and no replay;
  - bounded backpressure behavior;
  - cancellation cleanup;
  - one-subscriber or fan-out semantics.
- `tests/test_harness_identity.py` or focused identity assertions in the event
  tests:
  - `Agentao`-construction-time session id fallback allocation;
  - turn id propagation within one turn and separation across turns;
  - tool call id prefers the LLM id and falls back to UUID;
  - decision id uniqueness per permission decision;
  - `(session_id, turn_id, tool_call_id)` scoped uniqueness.
- `tests/test_harness_tool_events.py`
  - started/completed/failed;
  - sync and async tools;
  - raw args/output not leaked;
  - same exception class produces the same `error_type` value across runs;
  - cancelled executions use `phase="failed"` with `outcome="cancelled"` and
    `error_type=None`.
- `tests/test_harness_permission_events.py`
  - allow/deny/prompt;
  - every decision fires;
  - matched rule is projected correctly;
  - permission decision precedes tool lifecycle started for one tool call.
- `tests/test_harness_subagent_events.py`
  - spawned/completed/failed/cancelled;
  - parent/child ids remain correlated;
  - cancelled tasks use `phase="cancelled"` and `error_type=None`;
  - task summaries are redacted/truncated.

Recommended focused regression runs after each PR:

```bash
uv run pytest tests/test_harness_schema.py
uv run pytest tests/test_active_permissions.py
uv run pytest tests/test_harness_event_stream.py
```

Full regression should run before merging PRs 5-8 because those touch runtime
execution paths:

```bash
uv run pytest
```

---

## Acceptance Criteria

The MVP is complete when:

- `agentao.harness` exports public Pydantic models.
- `docs/schema/harness.events.v1.json` is generated from public event models and
  protected by tests.
- `docs/schema/harness.acp.v1.json` is generated from public ACP models and
  protected by tests.
- ACP host-facing payloads participate in the same schema discipline through
  their own checked-in snapshot.
- `Agentao.active_permissions()` returns `mode`, JSON-safe `rules`, and
  `loaded_sources`.
- `Agentao.events()` exposes an async iterator.
- Public events use stable `session_id`, `turn_id`, `tool_call_id`, and
  `decision_id` semantics as defined in this plan.
- The public stream emits tool lifecycle, permission decision, and subagent
  lifecycle events.
- `PermissionDecisionEvent` fires on every decision with `outcome`.
- Delivery contract tests cover ordering, backpressure/bounded behavior,
  cancellation cleanup, and subscriber semantics.
- CLI can consume public lifecycle events for basic runtime rendering.
- No public graph store, hooks list/disable, MCP reload, remote plugin share, or
  platform API is introduced as part of this MVP.
