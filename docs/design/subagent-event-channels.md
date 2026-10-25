# Sub-agent event channels: a child's events on its own session

**Status:** Review complete (rev 3). §8 steps 1 (#580, #582), 2 (child session id, #583) and 3 (relay) implemented.
**Anchors:** agentao `main@ed52674b` (2026-10-24, after #579). codex `816fa702ec6`. pi-mono `136b87e8a`. Line numbers are at those commits.
**Related:** `subagent-event-bridge.md` (the transport forwarder this doc keeps); `embedded-host-contract.md` (the public host contract); #578 / #579 (`child_task_id` on transport events); #580 (`parent_tool_call_id` on `SubagentLifecycleEvent`).
**Revision history:**
- rev 1: first draft.
- rev 2 (review of rev 1):
  - Replay skips relayed child events in the first version (§5.5). rev 1 assumed foreground child rows land on the sub-agent's replay turn. That is false for a parallel child batch, because the replay sub-agent stack is thread-local.
  - The order "`spawned` before the run's child events" is guaranteed, not left to hosts (§5.9).
  - The detach boundary is explicit: drive ends, detach, terminal event, close (§5.4.3).
  - Invariant 1 no longer claims an unchanged stream with the relay off. Lifecycle events gain the linking fields (§7).
  - Decided: one instance-level switch, a private session adopter, the existing observer hook with an amended docstring. Dropped: `relay_to()`, a per-call switch, a snapshot at attach, replay grouping.
- rev 3 (review of rev 2):
  - The ordering guarantee names the subscription it holds for: a `session_id=None` subscription that exists before `spawned` and stays open (§5.9, invariant 5).
  - The detach boundary states only the necessary constraint: detach after the drive ends, and before both the terminal event and the close. Each path keeps its current order of close and terminal event (§5.4.3). rev 2 wrongly said the foreground path already publishes the terminal event before the close.
  - The replay skip set has a fixed order: register before attach, detach before unregister, on every path (§5.5).
  - The three open questions are decided (§11).

## 1. Summary

A foreground sub-agent's events reach the parent in one way only: the transport forwarder copies five event types into the parent's transport stream (`agents/tools/_wrapper.py::_make_forwarder`). A background sub-agent sends no progress at all. This doc proposes a second path: **the child's public host events on the parent's `events()` stream, under the child's own `session_id`.** The parent's stream keeps only pointer events for the run (`SubagentLifecycleEvent`). The transport forwarder stays unchanged, as the compatibility path for the CLI, ACP and transport-only hosts.

This is the model codex and pi-mono use (§3). It removes three costs of mixing (§2) for hosts that read `events()`. It also gives background sub-agents a progress channel. Most of the machinery exists already: the child `Agentao` has its own `EventStream`, and `EventStream` has a synchronous observer hook and a `session_id` filter (§4).

The relay is opt-in, with one instance-level switch (§5.4). Replay does not record relayed events in the first version (§5.5). The link from the parent's tool row to the run comes from #580.

## 2. The problem

Mixing child events into the parent's stream has these costs. Each one is in the code today.

| # | Cost | Where |
|---|---|---|
| 1 | A consumer must tag child events to tell them from the parent's. It parsed the `[name n/max]` label, and since #579 it can read `child_task_id`. Every consumer must know the convention. | `_make_forwarder`; #579 |
| 2 | A child's provider call id enters the parent's id scope. `tool_call_id` is unique only within `(session_id, turn_id, tool_call_id)`. The CLI and ACP key rows by `call_id`. A reused provider id can close the wrong row. Not reproduced. | `subagent-event-bridge.md` §4.2; `runtime/identity.py` docstring |
| 3 | The forwarder must be a whitelist. `TURN_BEGIN` / `TURN_END` / `LLM_CALL_*` / `TOOL_RESULT` would make replay record the child's turns as the parent's. A new event type needs a forwarding decision. | `_make_forwarder` docstring; bridge doc invariant 6 |
| 4 | `TOOL_START` carries the label, `TOOL_OUTPUT` / `TOOL_COMPLETE` the bare name. Harmless while every consumer keys on `call_id`. | bridge doc §4.3 |
| 5 | A host sink that raises on a forwarded event loses the event silently. The child's `SdkTransport.emit` swallows the exception with no log. | `transport/sdk.py:91-96` |
| 6 | Delivery is synchronous on the child's thread. A slow sink slows the child. | `_make_forwarder` |

Costs 1–4 come from **one namespace**, not from the delivery mechanism. A queue in place of the direct call would fix 6 and change 5. It would not fix 1–4. A separate channel per child fixes 1–4. So this doc is about channel topology, not about adding a queue (agentao has one: §4.2).

## 3. Peers

**codex** (`codex-rs/`):
- Every sub-agent is its own thread with its own `ThreadId`. `spawn_agent` returns at once (`core/src/tools/handlers/multi_agents_v2/spawn.rs`).
- The parent's turn gets `SubAgentActivity` items only: `Started` / `Interacted` / `Interrupted` / `Completed`, with `agent_thread_id`, `agent_path`, and `id` = the parent's tool `call_id` (`protocol/src/items.rs:361`, `multi_agents_v2.rs:44`).
- The child's result reaches the model as a mailbox message that does not start a turn (`core/src/agent/control/completion.rs`, `trigger_turn = false`). Delivery is best effort, with a metric.
- The child's own events stay on the child's thread.

**pi-mono durable** (`packages/durable/`):
- Every background sub-agent is its own durable conversation (`test/examples/22-subagent-background.ts`).
- The spawning tool result returns `details: { name, conversationId }`. A UI attaches with `watchEvents(harness, conversationId)`.
- Nested tool events carry `parentToolCallId` / `parentTaskId` (`src/harness/events.ts:98-111`).
- `watchEvents` takes a snapshot at attach, and on overflow replaces undelivered batches with one snapshot. This is how it handles a late subscriber.

Both peers: the parent's stream holds pointers, the child's stream holds the detail, and the pointer carries the parent's call id.

## 4. What exists today

### 4.1 The child has its own host stream

`_build_sub_agent` constructs a full `Agentao`. Its constructor creates `self._host_events = EventStream()` (`agent.py:781`) and three emitters bound to it (`agent.py:1028-1042`). So the child's `ToolRunner` already publishes `ToolLifecycleEvent` and `PermissionDecisionEvent` for every child tool call, with the child's `session_id` and `turn_id`. Nobody subscribes, so `publish` drops them (`host/events.py`, "No subscriber").

### 4.2 `EventStream` is a queue with a session filter

`host/events.py`:
- Each subscriber has its own bounded `asyncio.Queue`.
- **Off-loop publish waits until the event is in the queue.** It calls `put_nowait`, or `run_coroutine_threadsafe(queue.put(...))` and then `fut.result()` (`:313-318`). A full queue therefore blocks the publisher (host-pulled backpressure).
- **On-loop publish does not wait.** On a full queue it schedules a put task and returns. Past `max_queue_size` pending puts it drops the event with a warning.
- `subscribe(session_id=...)` filters by `session_id`. `None` subscribes to every session the instance owns. One subscriber per filter. The docstring: "Hosts that need per-session iterators should subscribe to all sessions and filter client-side."
- `add_observer(callback)` fires synchronously inside `publish`, before the subscriber check and whether or not a subscriber exists. Observers run outside the lock, from a snapshot of the list. An observer that raises is logged at WARNING and isolated. `Agentao.remove_host_event_observer` detaches one, idempotently, but does not wait for a publisher that already took the snapshot.
- Same-session ordering is guaranteed. Cross-session ordering is not promised.

### 4.3 The lifecycle event already points at a child session

`SubagentLifecycleEvent` has `child_session_id: Optional[str]` (`host/models.py:139`). `HostSubagentEmitter.spawned()` takes `child_session_id=` and sets the wire `session_id` to `child_session_id or parent_session_id` (`host/projection.py`, comment: "so a host that filters by session sees the child's events together"). No caller passes `child_session_id` today, so the field is always `None`.

### 4.4 The child's session id is minted late

The child's `_session_id` is a fresh UUID from its constructor (`agent.py:779`). `spawned` is published before the child is built (`_wrapper.py`, `execute()` and `_launch_background`). So at `spawned` the child's session id does not exist yet. Inside `agent.py`, `_session_id` is read only through the emitters' lazy providers (`agent.py:1030-1041`). Outside `agent.py`, no module reads `agent._session_id` directly (grep of `\._session_id` outside `agent.py`: only `MemoryManager`'s and `ToolRunner`'s own attributes, which are different fields).

### 4.5 Replay's sub-agent stack is thread-local

`ReplayAdapter` keeps its sub-agent stack in `threading.local` (`replay/adapter.py:44-48`). `_current_turn_id()` returns the stack top, else the parent's `_turn_id`. A child's parallel batch runs on new `ThreadPoolExecutor` workers (`runtime/tool_executor.py:300`), whose stack is empty. `contextvars.copy_context()` there does not carry it, because the stack is not a contextvar.

## 5. Design

### 5.1 Overview

```
parent events()  ── ToolLifecycleEvent(session=P, tool_call_id=c1, tool=agent_x)        parent's own row
                 ── SubagentLifecycleEvent(session=P, child_task_id=k,
                                           child_session_id=S, parent_tool_call_id=c1,
                                           phase=spawned)                                 pointer (#580)
                 ── ToolLifecycleEvent(session=S, turn=t', tool_call_id=x1, ...)          child's rows (relayed)
                 ── PermissionDecisionEvent(session=S, ...)                               child's decisions (relayed)
                 ── SubagentLifecycleEvent(session=P, child_task_id=k, phase=completed)   pointer
transport        ── AGENT_START / forwarded child events / AGENT_END                     unchanged (§5.6)
```

A host joins the three levels by ids it already has: `tool_call_id` → `parent_tool_call_id`, then `child_session_id` → the child's `session_id`.

### 5.2 Mint the child's session id before `spawned`

- The wrapper mints `child_session_id` with `runtime.identity.new_session_id()` at the same point it mints `child_task_id` (#579), before `AGENT_START`. Background runs mint it before `_spawn_subagent_event` in `_launch_background`.
- It passes it to `spawned(child_session_id=...)`.
- It gives it to the child through **a private adopter on `Agentao`**, next to `_adopt_memory_manager`. The adopter sets `_session_id` once, after construction and before the first `chat()`, and refuses a second call. The emitters read the id lazily (§4.4), so every child event uses it.
- Not a constructor parameter: that would be public API for an internal need.

### 5.3 Keep the pointer events in the parent's session

`spawned()` set `session_id = child_session_id or parent_session_id` before step 2 (§8). With §5.2 that becomes the child's session for every run, and a host that subscribes with `events(session_id=P)` would stop seeing its runs start and end. So:

- **Change the rule: the lifecycle event's `session_id` is always the parent's.** `child_session_id` carries the pointer.
- This changes only a path no caller reaches today (§4.3), so no current event changes its `session_id`.
- Alternative considered: keep the rule and let lifecycle events live in the child's session. Rejected: a parent-filtered subscriber would see the parent's `agent_*` row and nothing about the run. codex puts the activity in the parent's thread for the same reason.

### 5.4 Relay the child's host events to the parent's stream (opt-in)

#### 5.4.1 The relay

- After `_build_sub_agent` constructs the child, and before the drive starts, the wrapper calls `child.add_host_event_observer(relay)`. `relay(event)` calls the parent's `self._host_events.publish(event)` unchanged. The event already carries the child's `session_id`.
- **Which events:** every `HostEvent` the child publishes, that is `ToolLifecycleEvent` and `PermissionDecisionEvent`. A child cannot spawn sub-agents, so it publishes no `SubagentLifecycleEvent`. A test pins that the relay forwards whatever the child publishes, so a new public event type needs no relay change.
- The wrapper keeps the `relay` object as the handle for `remove_host_event_observer`.

#### 5.4.2 The switch

- `Agentao(..., relay_subagent_events: bool = False)`, keyword-only after the `*`. One switch per instance. No per-call switch.
- Off (default): no observer, and no child-session event on the parent's stream.
- On: a subscriber with `session_id=None` gets the child events.
- Reason for opt-in: today a host that subscribes with `None` and counts `ToolLifecycleEvent`s as the parent's calls would start counting child calls. Flipping the default is a separate decision.
- Sub-agents read the switch from the parent at spawn. A child never relays its own children, since it has none.

#### 5.4.3 The detach boundary

The constraint on every path: **detach after the drive ends (`_drive_sub_agent` returns or raises), and before both the terminal `SubagentLifecycleEvent` and the child's close.** The order of the close and the terminal event is not part of the constraint. Each path keeps the order it has today.

- Foreground: `_run_sync`'s `finally` closes the child, and `execute()` publishes the terminal event after `_run_sync` returns. So today's order is drive → close → terminal. The detach goes at the top of that `finally`, before `_close_sub_agent`: drive → detach → close → terminal.
- Background: `_launch_background`'s worker publishes the terminal event inside its `try`, and closes in its `finally`. So today's order is drive → terminal → close. The detach goes right after the drive, before the terminal publish, on all three of its terminal paths (completed or incomplete, `AgentCancelledError`, `Exception`): drive → detach → terminal → close. Its `finally` keeps a second, idempotent detach for a `BaseException`.
- `remove_observer` does not wait for a publisher that already took the observer snapshot (§4.2). The boundary therefore rests on this fact: **when the drive has ended, no child thread can publish a host event.** Grounds:
  - a parallel batch leaves its `with ThreadPoolExecutor(...)` block only after every worker has finished (`tool_executor.py:300`);
  - a single tool runs inline on the drive's thread;
  - an async tool's worker can outlive a cancel ("cancels the awaiter, never the worker", CLAUDE.md), but the executor publishes host events on the calling thread, not in the tool worker.

  A test pins the result: no relayed event after the terminal event (§9). No extra synchronisation is added.

#### 5.4.4 Backpressure

`relay` runs on the child's thread (observers are inline). The parent's `publish` off-loop blocks for capacity. So a slow host consumer slows the child, as it already slows the parent. A background child can block too. This is the contract `events()` documents.

`add_observer`'s docstring says an observer "must be cheap and non-blocking". The relay can block. The block is the same one the parent's own emit sites take, on the same kind of thread (a tool worker). **Amend the docstring:** an internal relay that republishes into another `EventStream` may block under that stream's backpressure. The rule for host observers stays.

### 5.5 Replay skips relayed events (first version)

`HostReplaySink` is an observer of the parent's stream (`replay/manager.py:119-123`). With the relay on, it would also see the child's events. It cannot place them correctly:
- foreground: a child's parallel batch publishes from pool workers whose thread-local sub-agent stack is empty (§4.5), so their rows would land on the parent's turn;
- background: rows would land on whatever parent turn is open when they arrive, which can be an unrelated later turn.

So **the first version does not record relayed events.**
- The wrapper registers each live run's `child_session_id` in a set on the parent. **Register before attaching the relay, and detach the relay before unregistering**, so no relayed event can reach the sink while its session is unregistered. The exception paths clean up in the same order. No management layer beyond the set.
- `HostReplaySink` skips an event whose `session_id` is in that set.
- Not "skip any event whose `session_id` differs from the parent's": the parent's session id can be rebound (ACP `session/load`), which `projection.py` already guards against.
- Replay keeps exactly the rows it records today. An explicit mapping of child rows to a replay turn is future work, when a need exists.

### 5.6 The transport forwarder stays

`_make_forwarder`, `AGENT_START` / `AGENT_END`, the `[name n/max]` label and #579's `child_task_id` do not change. The CLI, ACP and transport-only hosts (the #578 host) keep working. This doc does not deprecate the forwarder. Moving the CLI or ACP to the host channel is out of scope.

### 5.7 Confirmation and `ask_user` stay synchronous

They are request and response, and the child waits for the answer. They keep the transport path (`_child_transport`: `confirm` with the `[name]` prefix, `ask_user`). The relay carries only the child's `PermissionDecisionEvent`, which reports a decision after it is made.

### 5.8 Late subscribers

`events()` has no replay: a subscriber gets only events published after it attached.
- A host that subscribes once with `session_id=None` sees every child event from the start, because it attached before the run existed. **This is the supported way to read child channels**, and it matches `subscribe()`'s own advice (§4.2).
- A host that subscribes with `session_id=S` on seeing `spawned` can miss the child's first events. The host docs say so.
- No snapshot at attach.

### 5.9 Ordering: `spawned` before the run's child events

The guarantee is causal, not global, and holds for one kind of subscription: **for a `session_id=None` subscription that exists before `spawned` is published and stays open, on the off-loop path, a run's events arrive as `spawned` → its child-session events → its terminal event.** No ordering across unrelated sessions is promised, and hosts need no buffering protocol.

It does not hold for other subscriptions, and the host docs say so:
- a `session_id=S` subscription never receives `spawned` or the terminal event, which are in the parent's session;
- a subscription made after `spawned` may have missed it (§5.8).

Why it holds for that subscription:
- Both paths publish `spawned` before the child exists: foreground on the same thread, before `_run_sync`; background before `Thread.start()`.
- Off-loop, `publish` returns only when the event is in every matching subscriber's queue, also when that queue was full (§4.2). Each queue is FIFO. A child event is published later, so it is queued after `spawned`.

The limit: **on-loop**, `publish` does not wait, and past the cap it drops. A host reaches that path only by calling the synchronous `chat()` on the loop thread that `events()` is bound to. The host docs state the guarantee for the off-loop path and name that edge. The same reasoning gives "the run's child events before its terminal event" through §5.4.3.

## 6. What this fixes, and what it does not

| # | Cost (§2) | With the relay, for an `events()` host |
|---|---|---|
| 1 | Tagging | Fixed. The `session_id` is the attribution. |
| 2 | Id scope | Fixed. The child's `tool_call_id`s are in the child's `(session_id, turn_id, …)` scope. |
| 3 | Whitelist | Fixed for this channel. The projection layer already decides what is public. No forwarding decision per event type. |
| 4 | Two names | Fixed for this channel. `ToolLifecycleEvent.tool_name` is the bare name on every phase. |
| 5 | Silent loss | Improved. An observer that raises is logged. A full queue blocks or, on-loop, drops with a warning. |
| 6 | Synchronous | Unchanged. Backpressure blocks the child, by contract. |

For transport hosts, nothing changes: #579 stays their tool. Replay sees no change (§5.5).

## 7. Invariants

| # | Invariant |
|---|---|
| 1 | Relay off (default): the parent's `events()` stream gets no child-session event. Lifecycle events gain the linking fields (`child_session_id`, and #580's `parent_tool_call_id`). Nothing else changes. |
| 2 | Every `SubagentLifecycleEvent` has the parent's `session_id`, and `child_session_id` set for every run, foreground and background. |
| 3 | The child's `session_id` on every relayed event equals the run's `child_session_id`. |
| 4 | Relay on: every `HostEvent` the child publishes reaches the parent's stream once, unchanged. |
| 5 | For a `session_id=None` subscription that exists before `spawned` and stays open, off-loop: a run's `spawned` precedes its relayed events, and its relayed events precede its terminal event. |
| 6 | On every path, the detach happens after the drive ends and before both the terminal event and the close. Each path keeps its current order of close and terminal event. |
| 7 | Two runs have different `child_session_id`s. |
| 8 | Replay records no relayed event. Its rows are the same as with the relay off. |
| 9 | The transport forwarder's output is unchanged (bridge doc invariants 1–11 hold). |
| 10 | Confirmation and `ask_user` still go through the transport, with the `[name]` prefix. |
| 11 | A relay whose `publish` raises does not fail the child's run (observer isolation in `EventStream.publish`). |

## 8. Plan

1. **#580** (`parent_tool_call_id`, `host.events.v3.json`, replay 1.6). Prerequisite for the first join in §5.1.
2. **Child session id** (§5.2, §5.3): mint before `spawned`, the private adopter, populate `child_session_id`, pin the lifecycle `session_id` to the parent. No schema change. Can ship with #580.
3. **Relay** (§5.4, §5.5): the switch, the observer, the detach boundary, the replay skip set, the observer docstring amendment. Foreground and background. Docs: `docs/reference/host-api.md` + zh (a section on child sessions, with the §5.8 and §5.9 limits), developer guide part 4 / part 5 (en + zh), `CHANGELOG.md`.

## 9. Tests

- **Child session id:** `spawned` and the terminal event carry the same `child_session_id`; it equals the `session_id` of the child's own `ToolLifecycleEvent`s; foreground and background; two runs differ; the lifecycle `session_id` is the parent's.
- **Relay off:** a `None` subscriber sees no child-session event (invariant 1).
- **Relay on:**
  - a child tool call produces relayed `started` / `completed` `ToolLifecycleEvent`s with the child's `session_id`;
  - a child permission decision is relayed;
  - a `session_id=P` subscriber sees none of them;
  - the relay forwards an event type it was not written for (a synthetic `HostEvent` on the child stream).
- **Ordering:** for a `None` subscriber, `spawned` before the first relayed event, and the last relayed event before the terminal event. Also with a queue of size 1 and a consumer slower than the child, so the full-queue path is taken. Foreground and background.
- **Detach:** nothing relayed after the terminal event, on success, incomplete, exception, raised cancel, and a background run of each kind. Assert that the detach precedes both the terminal event and the close, foreground and background.
- **Skip set:** the run's `child_session_id` is registered before the relay is attached and unregistered after it is detached, also when the drive raises. After each run, that run's `child_session_id` is gone from the set. The set is empty once every run has ended, also with several background runs alive at once.
- **A raising relay** does not fail the run.
- **Replay with the relay on:** a child with a **parallel** batch (read-only mode) and a background child. The replay file has the same rows as with the relay off. Pins invariant 8 on exactly the cases rev 1 got wrong.
- The existing forwarder tests (`tests/test_subagent_tool_call_id.py`, `tests/test_subagent_child_task_id.py`) pass unchanged.
- Drive the real child `ToolRunner`, not a fake emitter: the relay's value is that it carries what the child really publishes.

## 10. Non-goals

- No change to the transport forwarder, its whitelist or its payloads.
- No per-child transport channel (a child `AgentEvent` firehose). It would carry raw, unprojected payloads (`LLM_CALL_IO`, reasoning), and a subscriber could not attach before the child's transport exists.
- No snapshot at attach for late subscribers (§5.8).
- No `EventStream.relay_to()`. The docstring amendment covers the relay (§5.4.4).
- No per-call relay switch.
- No replay recording or grouping of child rows (§5.5).
- No global ordering across sessions (§5.9).
- No child-to-parent result delivery change (background notifications stay as they are).
- No nested sub-agents.

## 11. Decided questions

1. §5.4.2: the switch is named `relay_subagent_events`.
2. §5.9: the host docs recommend `arun()` in every async environment. The synchronous `chat()` on the bound loop is named as the edge where the ordering guarantee does not hold.
3. Child summaries (counts, usage) stay on the terminal `SubagentLifecycleEvent`, as today. It remains the source; relayed rows are not summed into it.
