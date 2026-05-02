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
> Agentao **in-process**. Three pillars:
>
> - **Observability events** — `ToolLifecycleEvent`,
>   `SubagentLifecycleEvent`, `PermissionDecisionEvent`.
> - **Permission state** — `ActivePermissions` snapshot.
> - **ACP schema surface** — versioned Pydantic models for ACP wire
>   payloads, exported *only* for the long-tail case where an
>   in-process host *also* re-exposes Agentao to its own clients via
>   ACP. Vanilla in-process hosts do not need this surface and can
>   ignore the ACP-related exports entirely.
>
> This package is **not** a complete chat runtime. Drive a turn with
> `Agentao.arun()` and render streaming UI from the internal
> `Transport` / `AgentEvent` stream — that carries assistant text,
> reasoning, and raw tool I/O, which the stable host contract
> intentionally omits.
>
> > Not sure whether you want this surface, the ACP server
> > (`agentao --acp --stdio`), or the ACP client (`ACPManager`)?
> > See [Embedding vs. ACP](../architecture/embedding-vs-acp.md).

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
- MVP supports one **async iterator** consumer per filter
  (`Agentao.events(session_id=…)`); attaching a second iterator with
  the same filter raises `StreamSubscribeError`. For multi-sink
  fan-out (audit, metrics, replay) use synchronous observers — see
  [Synchronous observer fan-out](#synchronous-observer-fan-out) below.

The table below describes async-iterator delivery only; observer
delivery is independent and covered in the next section.

| State | Semantics |
|---|---|
| No subscriber | Drop public events immediately; do not block the agent loop. |
| Subscriber starts after events were emitted | No replay; subscriber only receives future events. |
| Subscriber queue has capacity | Enqueue matching events in emission order. |
| Subscriber queue is full | Block producer for matching events until capacity is available or the stream is cancelled. |
| Subscriber cancels / iterator closes | Release queue resources; future events follow the "No subscriber" row. |

### Synchronous observer fan-out

When a host needs to deliver every event to several cheap sinks
(audit log, metrics counters, replay recorder, debug printer) the
single-consumer async iterator is the wrong tool — register
synchronous observers on the underlying `EventStream` instead.

```python
stream = agent._host_events  # internal accessor; see note below

def audit(event: HostEvent) -> None:
    audit_log.write(event.model_dump_json())

def metrics(event: HostEvent) -> None:
    counter.labels(event.event_type).inc()

stream.add_observer(audit)
stream.add_observer(metrics)
```

Semantics:

- Observers run **inline on the producer thread**, before any async
  subscriber is notified. Keep them cheap and non-blocking — a
  blocking observer applies pressure to every emit site.
- Observer count is **unbounded**; one event fans out to every
  registered callback in registration order.
- Observer exceptions are caught, logged at WARNING, and discarded —
  a broken sink never breaks the runtime.
- Observers receive **every** event (no per-observer filter); filter
  by inspecting `event.session_id` inside the callback if needed.
- `remove_observer(callback)` detaches; idempotent and safe to call
  twice.

`HostReplaySink` is the canonical user of this mechanism — see
[Replay projection](#replay-projection-agentaohostreplay_projection)
above.

> **Accessor note.** The runtime currently exposes the underlying
> `EventStream` via `agent._host_events` — the leading underscore is
> a known wart that will be promoted to a stable accessor in a
> follow-up release. The shape of `add_observer` / `remove_observer`
> itself is stable.

## Need richer events? The internal `Transport` channel

The host contract above is **deliberately narrow** — three Pydantic
event families with versioned schema snapshots and a stability
promise. A second, **wider** event channel exists alongside it: the
internal `Transport` / `AgentEvent` stream. Hosts that need finer
visibility (LLM call usage, memory writes, hook fires, skill swaps,
context compression) attach a transport callback at construction
time:

```python
from agentao import Agentao
from agentao.transport import SdkTransport

events = []
transport = SdkTransport(on_event=events.append)
agent = Agentao(transport=transport, ...)

# After a turn:
for ev in events:
    print(ev.type, ev.data)            # ev is an AgentEvent dataclass
    wire = ev.to_dict()                # {"type", "schema_version", "data"}
```

### What flows through `Transport` today

Definitive list lives in `agentao/transport/events.py::EventType`. As
of this writing:

| Family | Members |
|---|---|
| Turn / loop | `TURN_START` |
| Tool execution (raw) | `TOOL_START`, `TOOL_OUTPUT`, `TOOL_COMPLETE`, `TOOL_RESULT` |
| LLM call | `LLM_CALL_STARTED`, `LLM_CALL_COMPLETED`, `LLM_CALL_DELTA`, `LLM_CALL_IO`, `LLM_TEXT`, `THINKING` |
| Sub-agent (raw) | `AGENT_START`, `AGENT_END` |
| Interaction | `TOOL_CONFIRMATION`, `ASK_USER_REQUESTED`, `ASK_USER_ANSWERED` |
| History | `BACKGROUND_NOTIFICATION_INJECTED`, `CONTEXT_COMPRESSED`, `SESSION_SUMMARY_WRITTEN` |
| Memory | `MEMORY_WRITE`, `MEMORY_DELETE`, `MEMORY_CLEARED` |
| Runtime state | `SKILL_ACTIVATED`, `SKILL_DEACTIVATED`, `MODEL_CHANGED`, `PERMISSION_MODE_CHANGED`, `READONLY_MODE_CHANGED`, `PLUGIN_HOOK_FIRED` |
| Errors | `ERROR` |

Every `AgentEvent` carries a `schema_version: int` field; bumps are
the *only* signal that a payload's shape changed.

### Stability — the part that actually matters

|  | `HostEvent` (this contract) | `AgentEvent` (`Transport`) |
|---|---|---|
| Schema snapshot in `docs/schema/`? | ✅ `host.events.v1.json` | ❌ |
| Field rename / removal triggers version bump? | ✅ enforced by `tests/test_host_schema.py` | ⚠️ best-effort `schema_version` bump on the affected payload only |
| Redaction / projection layer? | ✅ `agentao/host/projection.py` strips raw input/output | ❌ raw payloads (LLM_CALL_IO can contain full prompts and tool I/O) |
| Cross-version compatibility audit before release? | ✅ part of the release checklist | ❌ |
| Safe to forward over a long-lived wire? | ✅ | ⚠️ only after you pin `schema_version` and own the upgrade path |

### When to use which

- **Audit, compliance, billing, third-party UI:** `HostEvent`. The
  schema is the contract.
- **Local-process diagnostics, dev-tools panels, replay capture,
  cost dashboards owned by the same team:** `Transport` /
  `AgentEvent`. Cheap to attach, no projection cost, every internal
  fact is reachable.
- **Both at once:** common — observers (`add_observer`) on
  `EventStream` for stable sinks, plus `SdkTransport(on_event=...)`
  for the firehose. They run on independent code paths and don't
  interfere.

### Known gaps (neither channel covers these today)

- **MCP server lifecycle.** Connect / disconnect / `auth_failed` are
  not emitted on either channel. Hosts learn about an MCP outage
  indirectly when tool calls start failing. Tracked in
  [PUBLIC_EVENT_PROMOTION_PLAN](../implementation/PUBLIC_EVENT_PROMOTION_PLAN.md).
- **LLM rate-limit signal.** Provider-side 429 surfaces only as
  `ERROR` text. Promotion to a structured `LLMCallEvent` with
  `error_type="rate_limited"` is part of the same plan.

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
