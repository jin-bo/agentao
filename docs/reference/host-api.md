# Embedded Harness API

**Package:** `agentao.host`
**Status:** Stable, since 0.3.1.
**Coding agents:** for a distilled, copy-paste embedding playbook, start with [`docs/guides/embed-for-agents.md`](../guides/embed-for-agents.md).
**Source design:** [`docs/design/embedded-host-contract.md`](../design/embedded-host-contract.md)
**Implementation plan (historical):** [`docs/history/implementation/embedded-harness-contract-implementation-plan.md`](../history/implementation/embedded-harness-contract-implementation-plan.md)

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
> > See [Embedding vs. ACP](../design/embedding-vs-acp.md).

> **Import discipline.** All public types live on the `agentao.host`
> module — they are deliberately **not** re-exported from the top-level
> `agentao` package. Always `from agentao.host import ...`; do not
> rely on `agentao.ToolLifecycleEvent` or similar to exist.

## Public exports

| Symbol | Purpose |
|---|---|
| `ActivePermissions` | Read-only snapshot of the active permission policy. |
| `ToolLifecycleEvent` | Public envelope for one tool call's lifecycle. |
| `SubagentLifecycleEvent` | Lineage fact for a sub-agent task/session. `phase ∈ {spawned, completed, failed, cancelled}`; `failed` covers both a raised exception and a run that never answered — see [Sub-agent `failed` has two shapes](#sub-agent-failed-has-two-shapes). |
| `PermissionDecisionEvent` | Per-decision permission projection. |
| `HostEvent` | Discriminated union of the three event models. |
| `RFC3339UTCString` | Constrained timestamp type used by all public events. |
| `export_host_event_json_schema()` | Canonical JSON schema for the events + permissions surface. |
| `export_host_acp_json_schema()` | Canonical JSON schema for the host-facing ACP payload surface. |
| `Tool`, `AsyncToolBase` | Base classes for host-supplied tools passed via `Agentao(extra_tools=[...])`. Re-export of the canonical types in `agentao.tools.base` — a stable import path, not a new abstraction layer. |
| `RegistrableTool` | `Union[Tool, AsyncToolBase]` — the type the registry / `extra_tools=` accepts. |
| `agentao.host.replay_projection` | Submodule bridging `EventStream` ⇄ replay JSONL — see [Replay projection](#replay-projection-agentaohostreplay_projection) below. |

### Sub-agent `failed` has two shapes

`SubagentLifecycleEvent(phase="failed")` used to mean exactly one thing:
the sub-agent **raised**. It now also fires when the sub-agent returned
normally but **never produced an answer** — an empty turn, reasoning
with no answer, a length-truncated reply, a halted doom-loop, a failed
LLM call, or an exhausted turn budget. Reporting those as `completed`
would make this contract state something untrue, so they land on
`failed` instead.

**This is a breaking semantic change for hosts that branch on
`phase == "failed"`.** A host that pages on-call, opens an incident, or
retries the parent turn on that phase will now fire on ordinary
non-answers, which are common and usually not incidents. Branch on
`error_type` to separate the two:

| `error_type` | Meaning |
|---|---|
| `"incomplete:<reason>"` | The sub-agent returned without answering. Not an exception. |
| Any other value (e.g. `"ValueError"`, `"TimeoutError"`) | The sub-agent raised; the value is the exception's class name. |

`<reason>` is the `TurnOutcome.incomplete_reason` closed vocabulary
(`no_output`, `reasoning_only`, `length_truncated`, `doom_loop`,
`max_iterations`, `hook_stop`, `llm_error`). **`max_iterations` is a member
of that set, not an addition to it** — an earlier revision of this document
argued the turn budget was a separate axis that got its own key; 0.4.19 folded
it into the vocabulary and this paragraph is the correction. What is still
true on *this* surface is narrower: the sub-agent classifier tests the turn
budget in its own branch, **before** it reads `incomplete_reason`, so
`max_iterations` can reach the suffix without the turn having been classified
that way. Three defensive values
(`cancelled`, `error`, `unknown`) can appear when a turn outcome reports
neither an answer nor a reason; treat any unrecognized suffix as
"stopped short, cause unclassified" rather than matching the list
exhaustively.

```python
if isinstance(ev, SubagentLifecycleEvent) and ev.phase == "failed":
    if (ev.error_type or "").startswith("incomplete:"):
        metrics.non_answer(ev.error_type.split(":", 1)[1])   # expected
    else:
        pager.fire(ev.error_type)                            # a real crash
```

Cancellation is unaffected — it stays on `phase="cancelled"`.

Both emit sites carry this behavior: the foreground sub-agent call and
the background (`run_in_background=True`) worker. The background
`BackgroundTaskStore` record moves in lockstep, so
`check_background_agent` reports `failed` for a non-answer too — with
whatever partial result the run did produce still attached.

### Tool injection methods

Tools are injected at construction and, since the runtime dual landed, mutated
afterwards. All four share one validation + capability-binding path, so an
injected tool is never "bare" (it always inherits the session
`working_directory` / `filesystem` / `shell`).

| Method | Purpose |
|---|---|
| `Agentao(extra_tools=[...], disable_tools={...})` | Construction-time injection — add host tools, skip built-ins. See [`host-tool-injection.md`](../design/host-tool-injection.md). |
| `Agentao(enabled_tools={...})` | Construction-time allowlist — keep only the named built-in / agent-path tools (`extra_tools` / MCP / plan-only always kept). `None` = disabled; empty set = enabled. Mutually exclusive with `disable_tools`. See [`host-tool-allowlist.md`](../design/host-tool-allowlist.md). |
| `Agentao.add_tool(tool, *, replace=False)` | Register a tool after construction. `replace=False` + a name clash raises (stricter than `register`); `replace=True` overrides a built-in / agent / extra tool with an INFO audit line. |
| `Agentao.remove_tool(name) -> bool` | Unregister a tool. Returns whether it existed (unknown name → `False`, non-raising). |

Reserved namespaces — the `mcp_` prefix (MCP lifecycle) and `_PLAN_ONLY_TOOLS`
(`plan_save` / `plan_finalize`, bound to the plan-mode state machine) — are
rejected by `add_tool` (incl. `replace=True`) **and** `remove_tool`.

**Visibility:** the LLM-facing tool *schema* is snapshotted once per
`chat()` / `arun()` call before the inner loop, so what the model **sees**
never changes mid-turn — `add_tool` / `remove_tool` are reflected on the
**next** call. Tool *execution* resolves names against the live registry, so
v1 supports calling these methods **between** turns only (not from a concurrent
task, nor from inside a tool's `execute()` mid-turn — see
[`runtime-tool-injection.md`](../design/runtime-tool-injection.md) §7).

## Compaction (`Agentao.compact`)

```python
outcome = agent.compact()                              # a manual compaction
outcome = agent.compact(reason="api_overflow")         # on behalf of the ladder
```

Returns a `CompactionOutcome`
(`agentao.compaction.types`) — `status` is
`success | cancelled | failed | skipped`, with `detail` naming the case
(`circuit_open`, `history_too_short`, `no_safe_split`, `summary_empty`, …).
**History is byte-identical on every status but `success`.** Both token
fields exclude the system prompt and are `None` where no estimate exists.

`reason` selects which policy applies, not just a label. `manual_cli` (the
default) and `api_overflow` are allowed through an open circuit breaker as
half-open probes; `compression_threshold` is paused by it.

**`ContextManager.compress_messages()` is an internal transform, not this.**
It keeps its signature and its return type, but it hands back a bare list —
it cannot tell you whether anything changed or why it did not — and it
bypasses both the host control plane (`PreCompact` dispatch) and the
breaker's probe policy. It is not deprecated; it is simply the wrong level
for host code.

### Vetoing or replacing a compaction

Two layers, consulted in that order. A cancel in either is a cancel.

**Command hooks** — a `PreCompact` hook that prints this on stdout cancels it:

```json
{"hookSpecificOutput": {"compactionDecision": "cancel",
                        "compactionDecisionReason": "mid-refactor"}}
```

First cancel wins and stops the remaining forks. The key is
`compactionDecision`, not `permissionDecision` — a key that has never existed,
so no script can produce it by accident, which is why no opt-in flag is
needed. **Anything that is not an explicit `cancel` means allow**, including
an unknown value (logged): a typo must not be able to pause compaction until
the context blows up. Exit code 2 stays unhonoured. Hooks cannot supply
summary text — they have no trust boundary, and summary text permanently
rewrites history.

**`compaction_controller=`** (keyword-only constructor argument, at most one):

```python
def controller(ctx: CompactionDecisionContext) -> CompactionDecision:
    if ctx.kind == "full" and ctx.messages_to_summarize > 200:
        return CompactionDecision("provide_summary", summary=my_summary())
    return CompactionDecision("allow")

agent = Agentao(..., compaction_controller=controller)
```

`ctx` carries counts, budgets and recently-read paths — **never message
text**. It is a redaction boundary and it is never serialized; a host that
needs the text reads `agent.messages`. `provide_summary` is legal only when
`ctx.can_provide_summary` (i.e. `kind == "full"`).

The contract is **fail-open, and that is a hard rule**: a raise, an awaitable
(v1 is synchronous), an unknown `action`, or `provide_summary` with no text
are all treated as `allow` with a warning. Two of the five compaction entry
points *are* the API-overflow recovery ladder, so an exception escaping a
controller would turn "context too long" into "the turn crashes". There is no
timeout — if it hangs, it hangs the turn, exactly like the host's other
callbacks.

A host summary is validated before it is committed (non-empty, a `str`, at
most `ctx.max_summary_tokens`, free of the summary end marker). An invalid one
is rejected and the built-in summarizer runs **once**, as if the host had said
`allow`; `outcome.detail` records which check failed. What the circuit breaker
counts is always the built-in summarizer's failure, so a bad controller can
never disable automatic compaction.

**What a cancel means, per entry point.** History is byte-identical in every
case.

| Cancelled | Result |
|---|---|
| Microcompaction | that pass is skipped; no error; not re-asked this turn |
| Threshold | not re-asked this turn; if the API then overflows the host is asked **again**, with `reason=api_overflow` — a different question |
| API overflow (either rung) | the provider's context-length error is returned to the caller. It does **not** fall through to `messages[-2:]` |
| Manual `/compact` | reported as cancelled; an immediate retry dispatches normally (it never latches) |

Arbitrary message-list replacement is **out**: agentao's history is a flat
list where `tool_calls[*].id` must round-trip byte-for-byte, and a host
returning an orphaned tool result would produce a request the provider refuses
at a point where history has already been destroyed.

### Two context windows

| | Meaning | Who writes it |
|---|---|---|
| `context_manager.max_tokens` | what the host **configured** | the host (`max_context_tokens=`, `/context limit`, ACP `contextLength`) |
| `context_manager.effective_max_tokens` | `min(configured, observed)` | derived, read-only |
| `context_manager.observed_limit` | what the **provider asserted**, learned from an overflow error | agentao |

**Every internal budget** — the compaction thresholds, the microcompaction
band, the summary-input budget, `usage_percent` — is denominated in the
*effective* window. **`get_usage_stats()['max_tokens']` and ACP's
`session/set_model` echo keep returning the configured value**: the first so
existing readers are unaffected, the second because `session/set_model` is a
setter and its echo must equal what was just written, or a client reads
agentao's self-healing as a failed write. `effective_max_tokens`,
`observed_limit` and `observed_limit_provenance` are additive keys on
`get_usage_stats()`.

The observed limit can only **narrow**: a provider rejecting at N is evidence
about N, not permission to exceed the host's ceiling. It is discarded on a
model or endpoint switch (with a warning that the window is unverified for the
new model); a pure credential rotation leaves it alone.

**The parse refuses to guess.** Most overflow messages carry two numbers —
the request size and the limit — so every pattern is anchored to the phrase
that *names* the limit, values outside sanity bounds are refused, and two
patterns disagreeing adopts nothing. An overflow error is its only input, so
it cannot prevent the **first** fall into the recovery ladder; it reduces how
often you fall in again.

## Capability protocols (`agentao.host.protocols`)

Embedded hosts override IO by injecting these `Protocol` types into
`Agentao(filesystem=..., shell=..., mcp_registry=..., memory_manager=...)`.
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
| `FileSystem` | Protocol for filesystem IO (`read_bytes`, `read_partial`, `open_text`, `write_text`, `list_dir`, `glob`, `stat`, `exists`, `is_dir`, `is_file`). `write_text` carries an atomicity requirement — see below. |
| `ShellExecutor` | Protocol for shell execution + background handles. |
| `MCPRegistry` | Protocol for MCP server / tool discovery used by the runtime. |
| `MemoryStore` | Protocol for persistent memory storage backends. |
| `FileEntry`, `FileStat` | Value shapes returned by `FileSystem` implementations. |
| `ShellRequest`, `ShellResult`, `BackgroundHandle` | Value shapes for `ShellExecutor` implementations. |

The `Local*` defaults (e.g. `LocalFileSystem`, `LocalShellExecutor`)
remain in `agentao.capabilities` because they are reference
implementations, not part of the public host-injection surface.

### `FileSystem.write_text` must replace atomically

Implementations that **replace existing content** owe the caller an
atomic swap: a reader must see either the old content or the new one,
never a truncated or half-written file. Agentao runs inside a host
process it does not control, so a plain truncate-then-write leaves a
window in which a Ctrl+C, an OOM kill, or a `kill` destroys the user's
file. This is a requirement on **your** implementation, not just on the
default one.

`LocalFileSystem.write_text` is the reference approach, and two of its
observable behaviors matter to hosts wrapping or auditing the FS:

- It stages a **sibling temp file** in the target's directory, named
  `.{name}.*.tmp`, then `os.replace`s it into place. Audit wrappers,
  file watchers, and virtual filesystems will see that create/rename
  pair rather than a single write to the target path.
- A **read-only target raises `PermissionError`**. `os.replace` only
  needs write permission on the *directory*, so an atomic write would
  otherwise silently overwrite a `chmod 444` file that the old direct
  write refused. The refusal is explicit and deliberate.

Two cases keep the direct-write path, because neither can destroy
existing content: `append=True`, and a target that does not exist yet.

Scope: this closes the *process-death* window. It is not fsync'd, so
durability across power loss remains the host's concern.

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
| `HostReplaySink(recorder, *, stream=None)` | Forward projection. `Agentao.start_replay()` wires this automatically; hosts that drive replay manually can pass a stream explicitly. Every published `ToolLifecycleEvent` / `SubagentLifecycleEvent` / `PermissionDecisionEvent` is then written into `recorder` as a v1.2 replay event. Errors during write are logged at WARNING and swallowed — audit storage failure never breaks the runtime. |
| `replay_payload_to_host_event(kind, payload)` | Reverse projection. Rehydrates a `HostEvent` Pydantic model from a replay JSONL line. Strips the sanitizer's optional projection metadata (`redaction_hits`, `redacted`, `redacted_fields`) so a redacted line still validates against the public `extra="forbid"` models. |
| `host_event_to_replay_kind(event)` / `host_event_to_replay_payload(event)` | Lower-level helpers used by sinks and tests. Return `None` / `model_dump(mode="json")` respectively. |

`Agentao.start_replay()` auto-instantiates `HostReplaySink` against
the agent's `EventStream`; `end_replay()` detaches and clears the sink.
Hosts that drive the replay subsystem manually can do the same wiring
themselves.

The on-disk shape is the public Pydantic model's `model_dump(mode="json")`
— byte-equivalent to what the v1.2 replay schema's `oneOf` discriminator
matches. See [`docs/reference/replay-schema-policy.md`](replay-schema-policy.md)
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
synchronous observers on the agent instead.

```python
def audit(event: HostEvent) -> None:
    audit_log.write(event.model_dump_json())

def metrics(event: HostEvent) -> None:
    counter.labels(event.event_type).inc()

agent.add_host_event_observer(audit)
agent.add_host_event_observer(metrics)
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
- `agent.remove_host_event_observer(callback)` detaches; idempotent and safe to call
  twice.

`HostReplaySink` is the canonical user of this mechanism — see
[Replay projection](#replay-projection-agentaohostreplay_projection)
above.

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
| Turn / loop | `TURN_START`, `TURN_BEGIN`, `TURN_END` |
| Tool execution (raw) | `TOOL_START`, `TOOL_OUTPUT`, `TOOL_COMPLETE`, `TOOL_RESULT` |
| LLM call | `LLM_CALL_STARTED`, `LLM_CALL_COMPLETED`, `LLM_CALL_DELTA`, `LLM_CALL_IO`, `LLM_TEXT`, `THINKING` |
| Sub-agent (raw) | `AGENT_START`, `AGENT_END` |
| Interaction | `TOOL_CONFIRMATION`, `ASK_USER_REQUESTED`, `ASK_USER_ANSWERED` |
| History | `BACKGROUND_NOTIFICATION_INJECTED`, `CONTEXT_COMPRESSED`, `COMPACTION_SETTLED`, `SESSION_SUMMARY_WRITTEN` |
| Memory | `MEMORY_WRITE`, `MEMORY_DELETE`, `MEMORY_CLEARED` |
| Runtime state | `SKILL_ACTIVATED`, `SKILL_DEACTIVATED`, `MODEL_CHANGED`, `PERMISSION_MODE_CHANGED`, `READONLY_MODE_CHANGED`, `PLUGIN_HOOK_FIRED` |
| Errors | `ERROR` |

**Reading the two compaction events together.** `CONTEXT_COMPRESSED`
describes only a compaction that **changed history**, and it is emitted
after the change. `COMPACTION_SETTLED` is the terminal event for one
compaction *attempt* and also covers the ones that were vetoed or failed
(`status` is `success | cancelled | failed`). A `skipped` attempt emits
**neither**, deliberately: three of the four skipped cases re-trigger on
every loop iteration, so one event each would be an event storm rather
than a signal.

Their token fields are **different units and are named apart for that
reason**. `CONTEXT_COMPRESSED`'s `pre_est_tokens` / `post_est_tokens`
measure `[system prompt] + messages`; `COMPACTION_SETTLED`'s
`pre_tokens_history` / `post_tokens_history` measure the message list
alone. Do not wire one into the other. Both are `null` on the two
API-overflow rungs and on microcompaction, because filling them in would
mean full-history estimates on the paths where they are most expensive.

Every `AgentEvent` carries a `schema_version: int` field; bumps are
the *only* signal that a payload's shape changed. It is a **single
value shared by every event type**, not a per-payload version — so a
bump moves all of them at once, and a consumer pinned to the old value
starts rejecting events it could otherwise have read. That asymmetry is
why *additive* fields ship without a bump: an unknown key is free to
ignore, whereas a bump is not. Reserve it for a field whose shape or
meaning changed under a name consumers already read.

### Stability — the part that actually matters

|  | `HostEvent` (this contract) | `AgentEvent` (`Transport`) |
|---|---|---|
| Schema snapshot in `docs/schema/`? | ✅ `host.events.v1.json` | ❌ |
| Field rename / removal triggers version bump? | ✅ enforced by `tests/test_host_schema.py` | ⚠️ best-effort `schema_version` bump — global, so it moves every event type |
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
  [PUBLIC_EVENT_PROMOTION_PLAN](../history/implementation/public-event-promotion-plan.md).
- **LLM rate-limit signal.** Provider-side 429 surfaces only as
  `ERROR` text. Promotion to a structured `LLMCallEvent` with
  `error_type="rate_limited"` is part of the same plan.
- **Turn outcome as a streamed event (push).** The outcome itself —
  whether the model actually answered — *is* available synchronously:
  `agent.last_turn` returns a `TurnOutcome` (`text`, `status`,
  `incomplete_reason` over a single closed vocabulary — `no_output`,
  `reasoning_only`, `length_truncated`, `doom_loop`, `max_iterations`,
  `hook_stop`, `llm_error`, or
  `None`; `tool_count`; `error`; `finish_reason_missing`), and
  `.is_answer` folds it into one check.

  `finish_reason_missing` is the one axis genuinely separate from that
  vocabulary (it used to be described as the *third*, alongside
  `max_iterations`, which is now a member rather than an axis): at least one
  LLM call in the turn ended without the
  provider reporting *why* generation stopped, so agentao's `"stop"`
  fallback — not the provider — is what says the answer is complete. It
  does **not** affect `.is_answer`, because the servers that omit the
  field omit it on every call and every turn would become a failure. A
  host that wants the strict reading writes `o.is_answer and not
  o.finish_reason_missing`; one on a known-lenient provider keeps
  ignoring it. That is a **pull** surface: it answers "how did the turn I just
  awaited end?", which covers `chat()` / `arun()` callers, `agentao
  run`, and any embedder. What is **not** on the stable contract is the
  **push** shape — a `HostEvent` an async observer that does *not* drive
  the turn could subscribe to. That gap is now **main-loop-only**: for
  **sub-agent** turns the outcome *is* on the public surface, because a
  child that returned without answering emits
  `SubagentLifecycleEvent(phase="failed", error_type="incomplete:<reason>")`
  — see [Sub-agent `failed` has two shapes](#sub-agent-failed-has-two-shapes).
  For the **main** loop's own turn there is still no public event, so
  such an observer must fall back to the internal `Transport`'s
  `TURN_END` (unprojected, `schema_version` caveat above). This is
  **not** currently tracked in
  [PUBLIC_EVENT_PROMOTION_PLAN](../history/implementation/public-event-promotion-plan.md);
  that plan is scoped to `MCPLifecycleEvent` and `LLMCallEvent`, and a
  turn-outcome event would be a new pillar rather than a scope tweak.

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
