# Embedded Harness API

**Package:** `agentao.host`
**Status:** Stable, since 0.3.1.
**Source design:** [`docs/design/embedded-host-contract.md`](../design/embedded-host-contract.md)
**Implementation plan (historical):** [`docs/implementation/EMBEDDED_HARNESS_CONTRACT_IMPLEMENTATION_PLAN.md`](../implementation/EMBEDDED_HARNESS_CONTRACT_IMPLEMENTATION_PLAN.md)

The harness API is the host-facing compatibility boundary for embedding
Agentao inside another application. Internal runtime types
(`AgentEvent`, `ToolExecutionResult`, `PermissionEngine`) are
intentionally not part of this surface.

> **Scope.** This package is the stability boundary for hosts embedding
> Agentao, covering three pillars: **observability events**
> (`ToolLifecycleEvent`, `SubagentLifecycleEvent`,
> `PermissionDecisionEvent`), the **ACP schema surface** (host-facing
> request/response/notification models), and **permission state**
> (`ActivePermissions`). It is **not** a complete chat runtime — use
> `Agentao.arun()` to drive a turn, and `Transport`/ACP for streaming
> chat UI (assistant text, reasoning, raw tool I/O are intentionally
> outside this contract).

> **Import discipline.** All public types live on the `agentao.host`
> module — they are deliberately **not** re-exported from the top-level
> `agentao` package. Always `from agentao.host import ...`; do not
> rely on `agentao.ToolLifecycleEvent` or similar to exist.

## Public exports

| Symbol | Purpose |
|---|---|
| `ActivePermissions` | Read-only snapshot of the active permission policy. |
| `ToolLifecycleEvent` | Public envelope for one tool call's lifecycle. |
| `SubagentLifecycleEvent` | Lineage fact for a sub-agent task/session. |
| `PermissionDecisionEvent` | Per-decision permission projection. |
| `HostEvent` | Discriminated union of the three event models. |
| `RFC3339UTCString` | Constrained timestamp type used by all public events. |
| `export_host_event_json_schema()` | Canonical JSON schema for the events + permissions surface. |
| `export_host_acp_json_schema()` | Canonical JSON schema for the host-facing ACP payload surface. |
| `agentao.host.replay_projection` | Submodule bridging `EventStream` ⇄ replay JSONL — see [Replay projection](#replay-projection-agentaohostreplay_projection) below. |

## Capability protocols (`agentao.host.protocols`)

Embedded hosts override IO by injecting these `Protocol` types into
`Agentao(filesystem=..., shell=..., mcp_registry=..., memory_store=...)`.
The submodule is a stable re-export of the protocols and their value
shapes; **always import from `agentao.host.protocols` rather than
reaching into `agentao.capabilities.*`** (which is internal and may
move).

```python
from agentao.host.protocols import (
    FileSystem, ShellExecutor, MCPRegistry, MemoryStore,
    FileEntry, FileStat, ShellRequest, ShellResult, BackgroundHandle,
)
```

| Symbol | Purpose |
|---|---|
| `FileSystem` | Protocol for filesystem IO (`read_text`, `write_text`, `iter_dir`, …). |
| `ShellExecutor` | Protocol for shell execution + background handles. |
| `MCPRegistry` | Protocol for MCP server / tool discovery used by the runtime. |
| `MemoryStore` | Protocol for persistent memory storage backends. |
| `FileEntry`, `FileStat` | Value shapes returned by `FileSystem` implementations. |
| `ShellRequest`, `ShellResult`, `BackgroundHandle` | Value shapes for `ShellExecutor` implementations. |

The `Local*` defaults (e.g. `LocalFileSystem`, `LocalShellExecutor`)
remain in `agentao.capabilities` because they are reference
implementations, not part of the public host-injection surface.

## Replay projection (`agentao.host.replay_projection`)

The harness event stream and the replay JSONL are two views of the
same facts. This submodule bridges them so embedded hosts have one
audit artifact instead of two parallel streams.

```python
from agentao.host.replay_projection import (
    HostReplaySink,
    replay_payload_to_host_event,
    host_event_to_replay_kind,
    host_event_to_replay_payload,
)
```

| Symbol | Purpose |
|---|---|
| `HostReplaySink(recorder, *, stream=None)` | Forward projection. Pass `stream=agent._host_events` to auto-register as a synchronous observer; every published `ToolLifecycleEvent` / `SubagentLifecycleEvent` / `PermissionDecisionEvent` is then written into `recorder` as a v1.2 replay event. Errors during write are logged at WARNING and swallowed — audit storage failure never breaks the runtime. |
| `replay_payload_to_host_event(kind, payload)` | Reverse projection. Rehydrates a `HostEvent` Pydantic model from a replay JSONL line. Strips the sanitizer's optional projection metadata (`redaction_hits`, `redacted`, `redacted_fields`) so a redacted line still validates against the public `extra="forbid"` models. |
| `host_event_to_replay_kind(event)` / `host_event_to_replay_payload(event)` | Lower-level helpers used by sinks and tests. Return `None` / `model_dump(mode="json")` respectively. |

`Agentao.start_replay()` auto-instantiates `HostReplaySink` against
the agent's `EventStream`; `end_replay()` detaches and clears the sink.
Hosts that drive the replay subsystem manually can do the same wiring
themselves.

The on-disk shape is the public Pydantic model's `model_dump(mode="json")`
— byte-equivalent to what the v1.2 replay schema's `oneOf` discriminator
matches. See [`docs/replay/schema-policy.md`](../replay/schema-policy.md)
for the version compatibility contract.

## Typing gate

`agentao.host` ships clean under `mypy --strict`:

```
uv run mypy --strict --package agentao.host
```

CI's `Typing gate` job enforces this on every PR. Downstream projects
running `mypy --strict` against their own code paths inherit clean
types from this surface — `tests/test_host_typing.py` includes a
downstream-shaped consumer that exercises every public name.

## Schema snapshot policy

Each release ships a checked-in JSON schema snapshot:

- `docs/schema/host.events.v1.json` — events + permissions
- `docs/schema/host.acp.v1.json` — ACP payloads

`tests/test_host_schema.py` regenerates the schema from the Pydantic
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
  `agentao/host/projection.py`.
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
iterator over `HostEvent`. Pass `session_id=` to filter; pass `None`
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
