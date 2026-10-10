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
> Agentao **in-process**. Four pillars:
>
> - **Observability events** — `ToolLifecycleEvent`,
>   `SubagentLifecycleEvent`, `PermissionDecisionEvent`.
> - **Permission state** — `ActivePermissions` snapshot.
> - **Streaming text** — `Agentao.astream()` yields `TextDelta`, then
>   the turn's `TurnOutcome`. A delivery API, not an audit record: see
>   [Streaming text](#streaming-text-agentaoastream).
> - **ACP schema surface** — versioned Pydantic models for ACP wire
>   payloads, exported *only* for the long-tail case where an
>   in-process host *also* re-exposes Agentao to its own clients via
>   ACP. Vanilla in-process hosts do not need this surface and can
>   ignore the ACP-related exports entirely.
>
> This package is **not** a complete chat runtime. Drive a turn with
> `Agentao.arun()`, or with `Agentao.astream()` to stream its text.
> Reasoning text and raw tool I/O stay outside the stable contract; they
> are on the internal `Transport` / `AgentEvent` stream.
>
> > Not sure whether you want this surface, the ACP server
> > (`agentao --acp --stdio`), or the ACP client (`ACPManager`)?
> > See [Embedding vs. ACP](../design/embedding-vs-acp.md).

> **Import discipline.** All public types live on the `agentao.host`
> module — they are deliberately **not** re-exported from the top-level
> `agentao` package. Always `from agentao.host import ...`; do not
> rely on `agentao.ToolLifecycleEvent` or similar to exist. The one
> name on both is `TurnOutcome`: `agentao.TurnOutcome` predates this
> export and is the same class.

## Public exports

| Symbol | Purpose |
|---|---|
| `ActivePermissions` | Read-only snapshot of the active permission policy. |
| `ToolLifecycleEvent` | Public envelope for one tool call's lifecycle. |
| `SubagentLifecycleEvent` | Lineage fact for a sub-agent task/session. `phase ∈ {spawned, completed, failed, cancelled}`; `failed` covers both a raised exception and a run that never answered — see [Sub-agent `failed` has two shapes](#sub-agent-failed-has-two-shapes). |
| `SubagentUsage` | The four token counts on a terminal `SubagentLifecycleEvent.usage` (0.5.2) — see [What a sub-agent cost](#what-a-sub-agent-cost). |
| `PermissionDecisionEvent` | Per-decision permission projection. |
| `HostEvent` | Discriminated union of the three event models. |
| `EventStream` | The stream behind `agent.events()` and the observer methods. The runtime constructs it, and the iterator `agent.events()` returns reads from it; `Agentao` keeps it private, so a host reaches it only through those methods. To record an agent's events, see [Replay projection](#replay-projection-agentaohostreplay_projection). See [Event subscription semantics](#event-subscription-semantics). |
| `StreamSubscribeError` | Raised when a second async iterator attaches with the same `session_id` filter while the first is open (has started iterating and is not closed) — on the second iterator's first iteration, not when `agent.events()` is called. Close the first (`aclosing`), or fan out with synchronous observers. |
| `RFC3339UTCString` | Constrained timestamp type used by all public events. |
| `export_host_event_json_schema()` | Canonical JSON schema for the events + permissions surface. |
| `export_host_acp_json_schema()` | Canonical JSON schema for the host-facing ACP payload surface. |
| `Tool`, `AsyncToolBase` | Base classes for host-supplied tools passed via `Agentao(extra_tools=[...])`. Re-export of the canonical types in `agentao.tools.base` — a stable import path, not a new abstraction layer. |
| `RegistrableTool` | `Union[Tool, AsyncToolBase]` — the type the registry / `extra_tools=` accepts. |
| `CancellationToken` | The token `chat()` / `arun()` accept as `cancellation_token=`. Re-export of `agentao.cancellation.CancellationToken` (the same class). A simple async call can end its turn by cancelling the task instead; pass a token for a stop button, cancelling across tasks, a signal shared by several calls, or a sync `chat()` cancelled from another thread. |
| `TextDelta` | One chunk of assistant text from `Agentao.astream()`. For display: joined deltas are not the answer — see [Streaming text](#streaming-text-agentaoastream). |
| `TurnOutcome` | How a turn ended: `text`, `status`, `incomplete_reason`, `tool_count`, `error`, `finish_reason_missing`, and `.is_answer`. The last item of `astream()`, and what `agent.last_turn` returns. Same class as `agentao.TurnOutcome`; defined in the standard-library-only `agentao.outcome`. |
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
that way. Two defensive values
(`error`, `unknown`) can appear when a turn outcome reports
neither an answer nor a reason; treat any unrecognized suffix as
"stopped short, cause unclassified" rather than matching the list
exhaustively. A third, `cancelled`, was reachable before 0.4.24 — see
below.

```python
if isinstance(ev, SubagentLifecycleEvent) and ev.phase == "failed":
    if (ev.error_type or "").startswith("incomplete:"):
        metrics.non_answer(ev.error_type.split(":", 1)[1])   # expected
    else:
        pager.fire(ev.error_type)                            # a real crash
```

**Cancellation has its own phase, and since 0.4.24 it actually gets it.**
A sub-agent cancelled while it was *running* used to arrive as
`phase="failed"` with `error_type="incomplete:cancelled"`, while one
cancelled before it started arrived as `phase="cancelled"` — the same user
action in two phases, depending on timing. Both are now `phase="cancelled"`,
where `error_type` is `None`: a host that parsed `incomplete:cancelled` out
of `error_type` reads the phase instead. `BackgroundTaskStore` records match
(`status="cancelled"`, `incomplete_reason=None`) and keep whatever partial
result and counters the run produced.

### What a sub-agent cost

A terminal `SubagentLifecycleEvent` carries `usage` (0.5.2): a
`SubagentUsage` with `prompt_tokens`, `completion_tokens`,
`cache_read_tokens` and `cache_creation_tokens` — the same four quantities,
under the same names, as `agentao run`'s `usage`. `prompt_tokens` is the
**whole** input and the two cache counts are parts *of* it. It is what the
sub-agent's requests **reported**, summed over its run, a failed or cancelled
one included; agentao applies no prices.

`usage` is `None` on `spawned`, on a run cancelled before it started, and on
one whose construction raised — there was no sub-agent to read. `None` means
"not read", never "zero".

**The same counts are in the parent's session totals before the event is
published**, on both paths. Before 0.5.2 a background sub-agent's terminal
event (and its `BackgroundTaskStore` record) went out first and the totals
were updated afterwards, so a handler that read `agent.llm.total_prompt_tokens`
on `completed` saw a total that still left the run out. Do not add
`event.usage` to a total you read after the event: it is already in there.
The `BackgroundTaskStore` record carries the same dict under `usage`; its
older `tokens` key is something else, a local estimate of how large the
sub-agent's history ended up. A record recovered from a file written before
0.5.2 has no `usage` key at all — read it with `.get("usage")`.

Every count is a non-negative integer, and the schema says so (`minimum: 0`).
They are read from the sub-agent's client in one locked read
(`LLMClient.usage_snapshot()`), so the four numbers describe one moment even
when a cancelled run's stream is still winding down on another thread.

Both emit sites carry this behavior: the foreground sub-agent call and
the background (`run_in_background=True`) worker. The background
`BackgroundTaskStore` record moves in lockstep, so
`check_background_agent` reports `failed` for a non-answer too — with
whatever partial result the run did produce still attached.

### Continuing after a background sub-agent

A background sub-agent's result reaches the parent as a queued notice, and
the queue is drained only into the parent's **next** LLM request. If the
parent's turn has already ended, nothing reads it until something starts
another turn. The runtime never starts one itself — whether to spend a turn
nobody asked for is the host's decision. The interactive CLI decides yes
(`background_agents.auto_wake`, see configuration.md §3); an embedded host
that wants the same can do it from the event stream:

- React to a terminal `SubagentLifecycleEvent` (`completed` / `failed` /
  `cancelled`) whose `parent_task_id` is set — that marks a background task.
- Continue only if the original session is still the active one, no turn is
  running, and the turn that ran since did not already handle it.
- Start the continuation on your normal turn driver (a queue, a task), not
  inside the event handler, with an ordinary user message such as
  `"[Background agent finished — review the update and continue]"`. That
  turn's first request drains every queued notice.

The notice is queued **before** the terminal event is published, on every
background terminal path. That is ordering of production only: **the event
is a cue, not proof that a notice is still queued.** A running parent turn
may have drained it already, and a conversation reset (`clear_history()`,
`/new`) drops queued notices and silences later ones from tasks started
before it, while their terminal events still arrive. A continuation that
finds nothing costs one turn; guard against that with your own session and
turn bookkeeping rather than by reading the queue, which is not public API.

**Or the model waits inside the turn.** `check_background_agent` takes an
optional `wait_seconds` (default `0`, at most 1800): one call blocks until the
child settles and returns its result in the same turn. This is the path for an
ACP client, which only starts turns itself. Cancelling the turn — ACP
`session/cancel`, Ctrl+C, the token passed to `chat()` — ends the wait within
half a second and leaves the child running; only `cancel_background_agent`
stops it. A wait that runs out says so and tells the model not to repeat it.
While waiting, the tool reports progress once a minute through the ordinary
tool-output stream (`TOOL_OUTPUT`, ACP `tool_call_update`). The 1800-second
bound is provisional: check your client's own prompt-turn timeout, since a
wait the client abandons first ends the turn from the outside.

### Permission posture

A mode is spelled as its string value — `"read-only"`, `"workspace-write"`,
`"full-access"`, `"plan"` — the same vocabulary as `ActivePermissions.mode`.
`PermissionMode` is not part of this surface; the enum is still accepted
wherever a string is.

| Entry point | Behaviour |
|---|---|
| `Agentao(permission_mode="read-only")` | `"read-only"`, `"workspace-write"` or `"full-access"`; `"plan"` raises `ValueError` (plan mode is entered through the plan session). The agent starts in that mode with the engine and the read-only gate agreeing, and **no event is emitted** — a starting state is not a switch. Default `None`: no engine. The rule is one for callers: **an engine you pass and a mode are mutually exclusive** (`ValueError`). `Agentao(permission_mode=)` builds `PermissionEngine(project_root=working_directory, rules=[])` and reads no rule file; `build_from_environment(permission_mode=)` applies the mode to the engine the factory loads from the permission files — an internal engine, not one you passed — so the user's rules stay. A bad mode is refused before anything is opened. |
| `Agentao.set_permission_mode(mode)` | Switches the posture and records it (`cause="host"`). Takes the string or the enum; an unknown string raises `ValueError`, another type `TypeError`. **Returns the previous mode as a `PermissionMode`**, not a string. Raises `ValueError` when the agent has no engine. |

Rules (`rules=`) still need a `PermissionEngine` you build yourself, from
`agentao.permissions`. Every remaining *ask* is answered by the transport,
and `NullTransport` answers yes — see [`embedding.md` §2](../guides/embedding.md#permissions-and-the-transport).

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

## Streaming text (`Agentao.astream`)

`Agentao.astream(prompt, *, max_iterations=100, images=None, cancellation_token=None)` runs one
turn and yields its assistant text as it streams, then the turn's
`TurnOutcome`:

```python
from contextlib import aclosing  # Python 3.10+
from agentao.host import TextDelta, TurnOutcome

async with aclosing(agent.astream(prompt)) as stream:
    async for item in stream:
        if isinstance(item, TextDelta):
            send_to_ui(item.text)
        else:                         # TurnOutcome, always the last item
            store(item.text) if item.is_answer else report(item)
```

**Deltas are for display; the outcome is the answer.** Joined `TextDelta`s
are not `TurnOutcome.text`. Every LLM call in the turn streams its text,
including a call that ends in tool calls, so narration such as "Let me
check the file" arrives as deltas and is not in the final text. The final
text can also be a string no delta carried: the `[No response]`
placeholder, an abort notice or an `[LLM API error: …]` string. Show the
deltas; store or act on `TurnOutcome.text`, checked with `is_answer`.

**Close it when you leave early.** `break` alone does not close an async
generator: it is closed when it is garbage-collected or when the event loop
shuts down, so while anything still references it, the turn stays open: it streams until the queue is full (64 deltas), then waits there, still holding the agent, and a later turn raises `TurnInProgressError`.
`aclosing(...)` closes it on the way out, deterministically. Closing, or
cancelling the task that consumes the stream, cancels the turn and waits
for its cleanup, bounded, as a cancelled `arun()` does. A turn cancelled
that way yields nothing more; `agent.last_turn` records it once the turn
has ended. A turn still queued for a worker when the stream closed never
starts, and `agent.last_turn` is not set for it.

**The rest of the contract:**
- `TurnOutcome` is yielded only when the turn returned: an answer, a turn
  with no answer (`is_answer` false, for example an `llm_error`), or a
  cancelled one (`status="cancelled"`). A turn that raised yields no outcome:
  its exception is raised from the iterator after the text already streamed,
  and `agent.last_turn` records it with `status="error"`.
- The outcome is this call's, even if another caller's turn on the same
  agent ran before you read it. `agent.last_turn` is the latest turn's.
- One turn at a time, as with `arun()`: a second turn on the same agent
  raises `TurnInProgressError` from the iterator, when the worker starts it
  (with the `arun` pool busy, that can be after a wait). A refused stream
  yields nothing from the running turn, and since it never started a turn,
  `agent.last_turn` is not set for it.
- `cancellation_token=` is linked to the stream's own token, so cancelling
  it ends the turn; the link is removed when the stream ends. It is one
  way: a turn cancelled by closing the stream does not cancel your token, so
  read the outcome's `status`.
- The queue is bounded like `events()`'s: a consumer that stops reading
  slows the turn instead of growing memory.
- The stream attaches by subscribing to the agent's transport and never
  replaces it, so confirmations and replay are unchanged. A transport with
  no `subscribe()` makes `astream()` raise `TypeError` when called, before
  the turn starts. `NullTransport` and `SdkTransport` both subscribe. A
  subclass of either that overrides `emit()` must call `super().emit()`:
  otherwise no event reaches the stream, and `astream()` raises
  `RuntimeError` after the turn, with no outcome (`agent.last_turn` has it).
- Not included: reasoning text, tool and permission events (they stay on
  `events()`), and a sub-agent's text, which runs on its own transport.
- `TextDelta` and `TurnOutcome` are not `HostEvent` members. They are not
  projected into replay and are not in `docs/schema/host.events.v1.json`;
  the text already reaches replay through the internal stream.

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
half-open probes; `compression_threshold` is paused by it. These three are
the only values `compact()` is typed to accept (`ManualCompactionReason` in
`agentao.compaction.types`); it does not check `reason` at runtime.

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
| `context_manager.effective_max_tokens` | `min(configured, observed, reported)` | derived, read-only |
| `context_manager.observed_limit` | what the **provider asserted**, learned from an overflow error | agentao |
| `context_manager.reported_limit` | what the **provider's Models API states** (`max_input_tokens`), read live from `llm.model_input_limit`; `None` when not told — today only the `anthropic-messages` wire asks, as part of sending its first request for a model, so it is `None` until then | agentao |

**Every internal budget** — the compaction thresholds, the microcompaction
band, the summary-input budget, `usage_percent` — is denominated in the
*effective* window. **`get_usage_stats()['max_tokens']` and ACP's
`session/set_model` echo keep returning the configured value**: the first so
existing readers are unaffected, the second because `session/set_model` is a
setter and its echo must equal what was just written, or a client reads
agentao's self-healing as a failed write. `effective_max_tokens`,
`observed_limit`, `observed_limit_provenance` and `reported_limit` are additive
keys on `get_usage_stats()`.

Both learned limits can only **narrow** — a Models API advertising a larger
window than the host configured is not a reason to use it, and a provider rejecting at N is evidence
about N, not permission to exceed the host's ceiling. The observed limit is discarded on a
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
    LaunchRequest, LegacyLaunch, WindowsLaunch,
    ShellSpec, ShellSpecProvider, ShellBlock, ShellDialect, Exhausted, AbsPath,
)
```

| Symbol | Purpose |
|---|---|
| `FileSystem` | Protocol for filesystem IO (`read_bytes`, `read_partial`, `open_text`, `write_text`, `list_dir`, `glob`, `stat`, `exists`, `is_dir`, `is_file`). `write_text` carries an atomicity requirement — see below. |
| `ShellExecutor` | Protocol for shell execution + background handles. |
| `MCPRegistry` | Protocol for MCP server / tool discovery used by the runtime. |
| `MemoryStore` | Protocol for persistent memory storage backends. |
| `FileEntry`, `FileStat` | Value shapes returned by `FileSystem` implementations. |
| `ShellRequest`, `ShellResult`, `BackgroundHandle` | Value shapes for `ShellExecutor` implementations. `ShellResult.stdout_omitted_bytes` / `stderr_omitted_bytes` (default `0`) say how many bytes of a stream were not kept, and `stdout_omitted_at` / `stderr_omitted_at` (default `0`) the byte offset in `stdout` / `stderr` where they were — `0` meaning the front, so a tail-only executor sets only the count. The local executor keeps the first and last 512 KiB of each stream, and the tool marks the gap where it is. `ShellRequest.cancellation_token` (default `None`) is the turn's `CancellationToken`: the local executor kills the process tree when it fires and returns `ShellResult.cancelled=True` (default `False`); an executor that ignores it keeps running the command to its end or timeout, as before. |
| `LaunchRequest` and its members `LegacyLaunch` / `WindowsLaunch` | What `ShellRequest.launch` carries — see below. |
| `ShellSpec`, `Exhausted`, `ShellSpecProvider` | The optional interpreter declaration an executor may expose — see below. |
| `ShellBlock`, `ShellDialect` | The user-level shell configuration and the syntax vocabulary, for a host resolving a spec itself. |
| `AbsPath` | The `NewType` alias those shapes spell paths with. |

### `ShellRequest` carries a launch, not a command string

**Breaking change.** `ShellRequest` no longer has `command`, `cwd` and `env`
fields; it has a single discriminated `launch: LaunchRequest` (plus the
unchanged transport fields `timeout` and `on_chunk`). Read `request.launch`.
`request.command` and `request.cwd` survive as read-only projections for
display and logging; **`request.env` is gone** — the environment is
`launch.env`, a complete mapping the executor sets verbatim rather than
computing.

Unless the host configured a named interpreter, the payload is a
`LegacyLaunch`, which carries exactly the three fields that used to be on the
request plus an optional `executable`:

```python
def run(self, request: ShellRequest) -> ShellResult:
    launch = request.launch
    if isinstance(launch, LegacyLaunch):
        return self._spawn(
            launch.command, cwd=launch.cwd, env=dict(launch.env),
            shell=True, executable=launch.executable,   # None keeps your default
        )
    # WindowsLaunch: start launch.application_name with launch.command_line,
    # no shell in between.
    ...
```

`launch.executable` is the interpreter the user named in the `shell` block, and
it must win over whatever the executor would otherwise pick — that is the whole
content of the setting. `None` means "keep your own default".

`WindowsLaunch` (`application_name` + `command_line`) is what a PowerShell
launch produces: the image is fixed by path rather than resolved from a name at
spawn time, and the command line is passed through verbatim.

### Declaring the interpreter (optional)

An executor may additionally implement `ShellSpecProvider` — a `shell_spec`
property answering `ShellSpec | Exhausted` — because it is the only party
that knows which interpreter a Docker or remote target actually starts. It is
deliberately **not** a member of `ShellExecutor` (a non-method member makes
`issubclass()` against a `runtime_checkable` Protocol raise). An executor that
declares nothing is read as reporting today's platform default, so existing
hosts keep working unchanged.

A host replacing the **shell tool itself** (`extra_tools` with a tool named
`run_shell_command`) must expose `shell_spec` on the tool: the command floor
gates on that tool's name, and registration refuses a replacement that cannot
name its dialect, because a floor scanning one shell's syntax with another's
patterns reports a clean result.

An executor that declares a `ShellSpec` decides both halves: `dialect` is the
grammar the command floor scans with and the syntax the prompt's shell
guidelines speak, and `interpreter` is the absolute path the launch starts
(`None` means the platform's own answer). `Exhausted` is the refusal arm — a
configured dialect this platform cannot run, or a PowerShell nobody installed —
and every shell call is then denied with that reason rather than falling back to
some other interpreter.

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
| `HostReplaySink(recorder, *, stream=None, turn_id_provider=None)` | Forward projection. `Agentao.start_replay()` wires this automatically, and is the route for hosts. `stream=` takes an `EventStream`, which `Agentao` keeps private; without it the sink is in pull mode, and `agent.add_host_event_observer(sink.record)` feeds it. Every published `ToolLifecycleEvent` / `SubagentLifecycleEvent` / `PermissionDecisionEvent` is then written into `recorder` as a v1.2 replay event. Errors during write are logged at WARNING and swallowed — audit storage failure never breaks the runtime. |
| `replay_payload_to_host_event(kind, payload)` | Reverse projection. Rehydrates a `HostEvent` Pydantic model from a replay JSONL line. Strips the sanitizer's optional projection metadata (`redaction_hits`, `redacted`, `redacted_fields`) so a redacted line still validates against the public `extra="forbid"` models. |
| `host_event_to_replay_kind(event)` / `host_event_to_replay_payload(event)` | Lower-level helpers used by sinks and tests. Return `None` / `model_dump(mode="json")` respectively. |

`Agentao.start_replay()` auto-instantiates `HostReplaySink` against
the agent's `EventStream`; `end_replay()` detaches and clears the sink.
That is the route for hosts. A sink built by hand needs a
`ReplayRecorder` (from `agentao.replay`, not the host surface), and is fed through
`agent.add_host_event_observer(sink.record)`, since `Agentao` keeps its
`EventStream` private; it records the host events only, not the LLM and
tool turns that `start_replay()` also records through its transport
adapter. Without `turn_id_provider=` (which `start_replay()` takes from
that adapter) its lines carry the runtime's own turn ids, which the
replay reader groups as turns of their own (a `SubagentLifecycleEvent`
carries none, so its lines fall outside every turn).
`start_replay()` records only when replay is enabled. A bare `Agentao(...)` starts with
replay off, so its `start_replay()` returns `None`: pass
`replay_config=ReplayConfig(enabled=True)` (from `agentao.replay`) at
construction, or set `replay.enabled` in `.agentao/settings.json` and
call `agent.reload_replay_config()` first. `build_from_environment()`
reads that file for you.

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

`Agentao`'s own public methods and properties are annotated as well,
`events()` and `active_permissions()` included, so a strict host gets
the contract's types from its first call rather than `Any`. A second
consumer in the same test calls them under
`mypy --strict --follow-imports=silent`; `agentao.agent` itself is not
held to `--strict`.

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
  (`Agentao.events(session_id=…)`). A second iterator with the same
  filter raises `StreamSubscribeError` on its first iteration (not when
  `agent.events()` is called) if the first has started iterating and is
  not closed. For multi-sink
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
promise, plus assistant text through `astream()`. A second, **wider** event channel exists alongside it: the
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
| History | `BACKGROUND_NOTIFICATION_INJECTED`, `COMPACTION_STARTED`, `CONTEXT_COMPRESSED`, `COMPACTION_SETTLED`, `SESSION_SUMMARY_WRITTEN`, `IMAGES_REMOVED` |
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
than a signal. An attempt abandoned because **the turn itself was
cancelled** (since 0.5.5) also emits neither: history is untouched and the
outcome is the turn's, reported as a cancelled turn. `status: "cancelled"`
still means only that a hook or `compaction_controller` vetoed the attempt.

Their token fields are **different units and are named apart for that
reason**. `CONTEXT_COMPRESSED`'s `pre_est_tokens` / `post_est_tokens`
measure `[system prompt] + messages`; `COMPACTION_SETTLED`'s
`pre_tokens_history` / `post_tokens_history` measure the message list
alone. Do not wire one into the other. Both are `null` on the two
API-overflow rungs and on microcompaction, because filling them in would
mean full-history estimates on the paths where they are most expensive.

**`COMPACTION_STARTED`** (`trigger`, `kind`, `reason`; 0.5.12+) fires just
before a `full` compaction calls the summarizer, the slow step, so a UI can
show "compacting" instead of "thinking". It fires only then: not for
microcompaction or `minimal_history` (no model call), not when the attempt
is skipped, vetoed or rejected before summarizing, and not when a
`compaction_controller` supplies the summary. Every start is followed by
a `COMPACTION_SETTLED` (`success` or `failed`) for the same attempt, unless
the turn is cancelled meanwhile, so do not wait for a settle to restore
your UI. It is a live signal only and is not recorded to replay;
`COMPACTION_SETTLED.duration_ms` carries the timing.

**`IMAGES_REMOVED`** (`reason`, `images_removed`, `message_indices`) says
image parts in history were replaced with a text note: a provider refused
an image (`provider_rejected`; every image goes, since the provider does
not say which one, and the turn ends with an error), or the model refused
image input (`model_unsupported`; the turn retries without them). It is
the only record of that rewrite: `LLM_CALL_DELTA` carries only the
messages a turn adds.

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
- **A chat UI streaming the answer:** `astream()`, with `events()`
  beside it for tool activity.
- **Local-process diagnostics, dev-tools panels, replay capture,
  cost dashboards owned by the same team:** `Transport` /
  `AgentEvent`. Cheap to attach, no projection cost, every internal
  fact is reachable.
- **Both at once:** common — observers
  (`agent.add_host_event_observer`) for stable sinks, plus `SdkTransport(on_event=...)`
  for the firehose. They run on independent code paths and don't
  interfere.

### Answering a confirmation the MCP Skills gate asks (`gate_note`)

With an MCP skill loaded, some confirmations are **gated**: activating an
MCP skill, `run_shell_command`, spawning a sub-agent that can run shell
commands, and `read_mcp_resource` on another server
(`docs/design/mcp-skills.md`). A gated confirmation needs a person, for this
call only. Read `agentao.transport.gate_note()` inside `confirm_tool`:

- `None`: an ordinary confirmation. Answer it as you always do, including
  from a remembered "always allow".
- A string: a gated confirmation. The string says why the call is gated.
  Show it to the user, do not answer from a remembered "always allow", and do
  not remember the answer. A remembered "always reject" can still reject.

```python
from agentao.transport import NullTransport, gate_note

class MyTransport(NullTransport):
    def confirm_tool(self, name, description, args):
        note = gate_note()
        if note is None and name in self.always_allowed:
            return True
        return self.ui.ask(name, note or description, args)  # never stored when note is set
```

`gate_note()` is thread-local: a foreground sub-agent's confirmation runs on
the sub-agent's thread through the parent's transport, so read it inside
`confirm_tool`, never cache it. The built-in transports already follow this:
`NullTransport`, `SdkTransport` with no `confirm_tool`, and
`build_compat_transport` with no `confirmation_callback` approve every
confirmation **except a gated one, which they refuse**. The CLI asks a gated
confirmation even in `full-access`, and the ACP server offers only
`allow_once` / `reject_once` for it.

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
  run`, and any embedder. A host that drives the turn with `astream()`
  gets the same `TurnOutcome` as the stream's last item. What is **not** on the stable contract is the
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
