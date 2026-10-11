# Sub-agent event bridge: callbacks out, events back in

**Status:** **implemented.** A in #514 and #515, B in the PR that adds rev 5 (2026-10-10). Decision (rev 2): implement both options in two steps — A first, then B. rev 3 closed the review of rev 2; rev 4 records how A landed (§5), rev 5 how B landed (§6.4).
**Anchors:** agentao `main@60fcca9` (2026-10-11). Line numbers below are at that commit, before A landed.
**Related:** `0-5-0-removal-checklist.md` §C (why the sub-agent factory uses `build_compat_transport` today); `host-tool-injection.md`; `embedded-host-contract.md` (the public `SubagentLifecycleEvent`, which this doc does not change).
**Revision history:** rev 1 (`af7b014`) deferred B and proposed namespacing child ids. rev 2 adopts A and B in sequence, drops the id namespacing, keeps every line of the public `build_compat_transport` (rev 1 wrongly counted parts of it as removable), and pins the field defaults, the exception scope and the event order. rev 3 makes the parent entry points read `agent.transport` on every call (rev 2 passed bound methods, which replay's transport swap would bypass) and adds `tests/test_subagent_cancelled_terminal_state.py` to the test migration. rev 4 marks A implemented: #514 sends the end event, and #515 replaced its zero counters (below, struck through in §5) with real or omitted ones. rev 5 marks B implemented and names the tests that now pin invariants 4–7, 9 and 11.

## 1. Summary

A foreground sub-agent reports its progress to the parent through six legacy callbacks. Inside the child, `build_compat_transport` turns the child's `AgentEvent`s *into* those callbacks. In the parent, `tooling/agent_tools.py` turns the callbacks back *into* `AgentEvent`s on the parent transport. Two private conventions ride the callback layer to survive the trip: the `__agent_start__` / `__agent_end__` sentinel names and an `__call_id__` key smuggled into the tool arguments.

An external review (2026-10-10) proposed removing the round trip. Reading the code shows the round trip is narrower than that review said (§3), and it turned up one confirmed defect (§4.1). Both steps are adopted:

- **A — close the lifecycle gap.** A sub-agent that raises sends `AGENT_START` and never `AGENT_END`. The fix is in `AgentToolWrapper.execute()` alone (§5).
- **B — forward events directly.** The internal sub-agent path stops going through the callback bridge. The wrapper gives the child an existing `SdkTransport` whose `on_event` is a local forwarding function, and the parent passes its `emit` in (§6). The aim is fewer conversions and explicit rules for which fields pass and which events are dropped. Line counts are not the reason to do it.

A lands and is verified before B starts, so a regression can be traced to one of them.

## 2. The path today

```
child Agentao ──AgentEvent──▶ SdkTransport(on_event=_on_event)        # transport/sdk.py:211 (build_compat_transport)
                                 │ TURN_START        → step_callback(None, {...})
                                 │ TOOL_START        → step_callback(tool, args + {"__call_id__": id})   # sdk.py:217-226
                                 │ TOOL_CONFIRMATION → confirmation_event_callback(event)  (only if step_callback)
                                 │ TOOL_OUTPUT       → output_callback(tool, chunk, call_id=)
                                 │ TOOL_COMPLETE     → tool_complete_callback(tool, call_id=, status=, …)
                                 │ everything else   → dropped
                                 ▼
      AgentToolWrapper prefixes: step label "[name n/max] tool"      # _wrapper.py:1254 _make_prefixed_step_callback
                                 confirmation label, same counter     # _wrapper.py:1278
                                 confirm_tool name "[name] tool"      # _wrapper.py:621-624
                                 ▼
parent tooling/agent_tools.py:59-112 rebuilds AgentEvent(TURN_START | AGENT_START | AGENT_END | TOOL_START | TOOL_OUTPUT | TOOL_COMPLETE)
                                 ▼
parent transport ──▶ CLI display (cli/display.py) · ACP (acp/transport.py) · replay (replay/adapter.py)
```

The wrapper's own lifecycle does **not** go through the child transport. `execute()` calls the parent's `step_callback` directly with `"__agent_start__"` / `"__agent_end__"` and a `SubagentProgress` (`_wrapper.py:300-304`, `347-365`). The parent maps those back to `AGENT_START` / `AGENT_END` (`agent_tools.py:65-81`).

A background sub-agent uses `SdkTransport(confirm_tool=lambda *_: False)` with no event handler, so none of its *progress* events reach the parent (`_wrapper.py:613-616`). It still sends the public `SubagentLifecycleEvent` through its own path (`_wrapper.py:1122` onward).

The public host event `SubagentLifecycleEvent` is a separate path (`subagent_emitter`, `_wrapper.py:390-430`) and is out of scope.

## 3. What actually round-trips

The review said `__agent_start__`, `__agent_end__` and `__call_id__` all flow through the child transport. Only one does.

| Item | Path | Round trip? |
|---|---|---|
| `__agent_start__` / `__agent_end__` | wrapper → parent `step_callback` → `AgentEvent` | **No.** A one-way sentinel on the callback API. The `AGENT_START` → `"__agent_start__"` mapping in `build_compat_transport` (`sdk.py:251-277`) never runs for sub-agents: a child never gets agent tools (CLAUDE.md, *Sub-agents execute from one registry*), so it never emits `AGENT_START`. That branch serves external users of `build_compat_transport`. |
| `__call_id__` | child `TOOL_START` → `step_callback` args → popped in `agent_tools.py:84` | **Yes.** Event → callback → event. |
| step / confirmation labels, `confirm_tool` prefix | child event → wrapper transform → parent | Not a round trip, but a real transform that B must keep (§7). |
| `TOOL_OUTPUT` / `TOOL_COMPLETE` `call_id`, `status`, `error` | `_accepted_meta_keywords` signature check (`sdk.py:142-181`) → keywords → parent `AgentEvent` | **Yes.** Event → callback → event. Correct today, kept so by signature introspection. |

So removing the round trip means changing the callback interface between `AgentManager.create_agent_tools` and the parent (`agents/manager.py:137-160`). That is **internal**: `AgentManager`, `AgentToolWrapper` and `SubagentProgress` appear in no reference doc or guide, and `Agentao` builds its own `AgentManager` (`agent.py:885`).

`build_compat_transport` is **public** (the 0.5.0 migration surface), and so is the behaviour its callers observe: the `__call_id__` key in `step_callback` args, the callback signature check, the lifecycle mapping and the `SubagentProgress` it builds. All of it stays.

## 4. Findings while reading

### 4.1 `AGENT_END` is missing when a foreground sub-agent raises (confirmed)

`execute()` sends `__agent_start__` before `_run_sync` and `__agent_end__` only after it returns. Both `except` branches (`_wrapper.py:326-341`) send the public terminal `SubagentLifecycleEvent` and re-raise, but no `__agent_end__`.

Probe, at `60fcca9`: a wrapper whose `llm_config_getter` returns `{}` makes `_run_sync` raise `KeyError('api_key')`. The step callback saw `['__agent_start__']` and nothing else.

Effects, by consumer:
- **CLI:** `_on_agent_end` never runs, so the closing rule with the run's state and statistics is not printed (`cli/display.py:676`).
- **ACP:** the client gets `[sub-agent started: name]` and never `[sub-agent finished: …]`.
- **Replay:** `SUBAGENT_STARTED` is recorded with no `SUBAGENT_COMPLETED`, and the entry stays on `_subagent_stack` (`replay/adapter.py:555-590`) for the rest of the session.

Reachable causes include a sub-agent that cannot be built (bad model config) and a cancellation raised outside the turn (the comment at `_wrapper.py:327-331`).

### 4.2 A child's `call_id` is forwarded into the parent's id scope (watched risk, not reproduced)

`runtime/identity.py` states the uniqueness scope of `tool_call_id` as `(session_id, turn_id, tool_call_id)` and does not assume provider ids are globally unique. The bridge forwards a child's provider id unchanged into the parent's turn, where the CLI and ACP key tool rows by `call_id`. A provider that reused ids could make a child `TOOL_COMPLETE` close another row: the parent's `agent_*` row, or a row from an earlier turn of the same child, since the parent sees all the child's turns inside one turn of its own.

No such provider has been found. Neither A nor B changes ids: no new `ToolRunner` parameter, no id map, no change to the ids ACP clients see. If a collision is ever reproduced, its fix is a separate decision. (A `parent_call_id/child_call_id` prefix, proposed in rev 1, would not cover the same-child case, so it is dropped.)

### 4.3 The tool name differs between start and completion (note only)

`TOOL_START` carries the label `[name n/max] tool`; `TOOL_OUTPUT` and `TOOL_COMPLETE` carry the bare `tool`. All three consumers key on `call_id` first (`cli/display.py:460-471`), so this is harmless today. B keeps it as is.

## 5. A — close the lifecycle gap

Scope: `AgentToolWrapper.execute()` only. No change to any public host event.

- **Every foreground run that sent `AGENT_START` sends exactly one `AGENT_END`.** That covers a normal return, an ordinary exception and `AgentCancelledError`.
- **Normal return:** unchanged. The existing `_terminal_state()` classification and statistics, including a cancel that returns normally.
- **`AgentCancelledError`:** `state="cancelled"`, no `error` (the same rule as a cancel that returns normally: the state carries the fact).
- **Any other `Exception`:** `state="failed"`, `error=type(exc).__name__`.
- **`BaseException` that is not an `Exception`** (`KeyboardInterrupt`, `SystemExit`): no `AGENT_END`. This matches the existing `except` coverage exactly, and the public terminal event, which is not sent on those either. The end event and the public event stay in step.
- **No `stats` on a raising path.** When construction or the run raises, `_run_sync` returned nothing. Never read `stats` there. ~~The counters take the `SubagentProgress` defaults (0).~~ As landed (#515): zeros showed as real counts, so a run that raised after the sub-agent started reads `turns`, `tool_calls`, `tokens` (the history estimate) and `duration_ms` from the sub-agent's history and clock, the same measures `stats` uses. When the sub-agent never ran, they are `None`, and `AGENT_END` leaves those keys out. A `build_compat_transport` step callback still reads 0.
- **Order: `AGENT_END` first, then the public terminal event, on every path.** That is today's order on the normal path (`_wrapper.py:347` before `367`). On the raising paths, send `AGENT_END` in the `except` branch before `_terminal_subagent_event`.
- **The end event must not mask anything.** Sending `AGENT_END` must not replace the original exception, and must not stop the public terminal event. The CLI and ACP transports already swallow exceptions in `emit` (`cli/transport.py:181`, `acp/transport.py:246`), but an embedding host's transport may not. So wrap the send in `try/except Exception` and log.

Put the send in the existing `except` branches, or a small local helper they call. No lifecycle manager, state machine or global pairing mechanism.

**As landed:** `AgentToolWrapper._send_agent_end` makes the guarded send on all three paths. Tests: `tests/test_subagent_agent_end_on_raise.py`. Public host events are unchanged, but the `AGENT_END` payload is not: the counters may now be absent (developer guide part 4.2).

## 6. B — forward events directly

### 6.1 The seam

The internal factory (`AgentManager.create_agent_tools`, `AgentToolWrapper.__init__`) takes three entry points instead of the progress callback group (`step_callback`, `output_callback`, `tool_complete_callback`, `confirmation_event_callback`, `confirmation_callback`, `ask_user_callback`):

```python
event_sink: Callable[[AgentEvent], None]
confirm_tool: Callable[[str, str, dict], bool]
ask_user: Callable[..., str]
```

The parent passes functions that read `agent.transport` **on every call**, exactly as `tooling/agent_tools.py` does today:

```python
event_sink=lambda event: agent.transport.emit(event),
confirm_tool=lambda *a, **kw: agent.transport.confirm_tool(*a, **kw),
ask_user=transport_ask_user(agent),
```

Never pass the bound methods `agent.transport.emit` / `agent.transport.confirm_tool`. Starting replay replaces `agent.transport` with a `ReplayAdapter` (`replay/manager.py:106-108`), and ending it puts the inner transport back (`replay/manager.py:168-171`), both after the agent tools are registered. A bound method would stay tied to the transport present at registration, so later sub-agent events would skip a replay started afterwards, or keep reaching an adapter that was removed. `transport_ask_user` already reads the transport per call for the same reason. No getter interface or proxy class is needed.

These are still callbacks. `event_sink` receives whole events, and `confirm_tool` / `ask_user` return the user's answer. "Remove the callback bridge" means removing the conversion of events into the legacy callback shapes and back, not removing every callback.

The internal factory and the wrapper switch to the new entry points in one change. There is no period with the old and new internal interfaces side by side, and no adapter between them. Deprecating the public compat interface is out of scope.

### 6.2 The foreground forwarder

The foreground child gets the existing `SdkTransport(on_event=forward, confirm_tool=prefixed_confirm, ask_user=ask_user)`. `forward` is a local function in the wrapper and handles only these events:

| Child event | Forwarded as |
|---|---|
| `TURN_START` | Today's display-reset semantics: forward `{"display_reset": True}` or `{}`; only an event without `display_reset` advances the turn counter. |
| `TOOL_START` | `tool` replaced by the `[name n/max] tool` label; `args` and `call_id` kept. |
| `TOOL_CONFIRMATION` | The same label as the `TOOL_START` that follows; the rest of the data and `call_id` kept. |
| `TOOL_OUTPUT` | `tool`, `chunk` and `call_id` kept. |
| `TOOL_COMPLETE` | `tool`, `call_id`, `status`, `duration_ms` and `error` kept. |
| any other event | **Dropped** (§7, invariant 6). |

When `forward` rewrites a label or fills a default, it builds a new event with a copied `data` dict. `AgentEvent` is a mutable dataclass (`transport/events.py:67`), and the child's own transport hands the same object to its subscribers, so it must not be changed in place.

`prefixed_confirm` prefixes the tool name with `[name]` and calls the parent's `confirm_tool`, as `_wrapper.py:621-624` does today. `SdkTransport` defaults to `{"action": "stop"}` on max iterations, the same as `build_compat_transport`, so a sub-agent that runs out of turns still stops.

The wrapper sends `AGENT_START` and `AGENT_END` itself, as `AgentEvent`s through `event_sink`, with the payloads `agent_tools.py:65-81` builds today, including #515's rule that an unknown counter is left out. The sentinels and `SubagentProgress` leave the internal path. The public `SubagentLifecycleEvent` keeps its own path.

The background child keeps `SdkTransport(confirm_tool=lambda *_: False)` with no event handler. "Sends no events" means progress events from the child transport. The background run's public `SubagentLifecycleEvent` notifications are unchanged.

### 6.3 Field defaults: keep today's, exactly

`forward` keeps the defaults the parent bridge applies today. B does not take the chance to make the edge cases consistent.

| Event | Field | Today's rule | Where |
|---|---|---|---|
| `TOOL_START` | `call_id` | A missing **or empty** id falls back to the **label** `[name n/max] tool`. (`build_compat_transport` injects only a truthy id, and the parent uses `or name`, where `name` is the label.) | `sdk.py:224`, `agent_tools.py:84` |
| `TOOL_OUTPUT` | `call_id` | Only `None` falls back, to the **bare** tool name; an empty string is kept. | `agent_tools.py:97-101` |
| `TOOL_COMPLETE` | `call_id` | As `TOOL_OUTPUT`. | `agent_tools.py:103-112` |
| `TOOL_COMPLETE` | `status` | `None` → `"ok"`. | same |
| `TOOL_COMPLETE` | `duration_ms` | `None` → `0`. | same |
| `TOOL_COMPLETE` | `error` | kept; `None` when absent. | same |

None of these fallbacks fires on the real path. `normalize_tool_call_id` (`runtime/identity.py:51`) gives every planned call a non-empty id, and the executor always sends `status`, `duration_ms` and `error` (`runtime/tool_executor.py:296-304`, `683-688`). The rules matter only for synthetic events, so the tests keep the existing cases and need no new ones.

### 6.4 What B changes, and what stays

Removed from the **internal** path:
- `tooling/agent_tools.py:59-112`: the three parent callbacks that rebuild events, replaced by the per-call `event_sink` lambda in §6.1.
- The sentinel constants and calls in `_wrapper.py` (`261-262`, `300-304`, `347-365`).
- The sub-agent's dependence on `build_compat_transport`, the `__call_id__` key and `_accepted_meta_keywords`.

**Unchanged:** all of `build_compat_transport` in `sdk.py`, including the `__call_id__` injection, the signature check, the `AGENT_START` / `AGENT_END` mapping and the `SubagentProgress` it builds. External callers depend on that code, so none of it counts as removed by B.

Tests: `tests/test_subagent_tool_call_id.py` builds its fixtures from `build_compat_transport` and `_make_prefixed_step_callback`, and fakes `create_agent_tools` by keyword name. Its fixtures move to the real production forwarder (§8). `tests/test_subagent_cancelled_terminal_state.py` also depends on the old entry point: its `_watch()` sets `wrapper._step_callback` directly and `_agent_end()` reads ends by the `__agent_end__` sentinel. Its fixture moves to capturing `AgentEvent`s, with the same cancel-state and end-count assertions. Three other tests construct `AgentToolWrapper` without callbacks, and `tests/test_async_tool.py` reads the `create_agent_tools` type hints.

**As landed (B):** `AgentToolWrapper._child_transport` builds the child's transport and `_make_forwarder` is the whitelist forwarder; `AGENT_START` / `AGENT_END` go through `event_sink` (`_send_agent_end` keeps #515's rule). `tooling/agent_tools.py` passes the three per-call lambdas; `SubagentProgress` is now built only by `build_compat_transport`. `tests/test_subagent_tool_call_id.py` drives the production `_child_transport` from the entry points `register_agent_tools` really passes.

## 7. Invariants

| # | Invariant | Pinned by |
|---|---|---|
| 1 | Each child tool row keeps its own `call_id` across start, confirmation, output and completion, including a same-named parallel batch. | `test_same_name_parallel_completions_keep_distinct_call_ids`, `test_tool_output_carries_call_id`, `test_confirmation_event_keeps_subagent_call_id` |
| 2 | `TOOL_COMPLETE` forwards the real `status`, `duration_ms` and `error`; missing fields take the defaults in §6.3. | `test_completion_forwards_real_status_and_error`, `test_completion_without_call_id_falls_back_to_name` |
| 3 | A confirmation opening carries the same `[name n/max]` label as the `TOOL_START` that follows; a rejected confirmation (`display_reset`) does not advance the counter. | `test_confirmation_opening_carries_the_subagent_label`, `test_a_rejected_confirmation_does_not_advance_the_turn_label` |
| 4 | `TOOL_CONFIRMATION` is forwarded only when start events are, or ACP opens a call nothing closes. In B both are forwarded for a foreground child and neither for a background one. | `tests/test_subagent_tool_call_id.py`: `test_a_foreground_child_without_a_sink_forwards_nothing`, `test_a_background_child_sends_the_parent_nothing_and_refuses` |
| 5 | `confirm_tool` reaches the parent's transport with the name prefixed `[name]`, so the MCP Skills gate note and the CLI prompt see the same path as today. | `test_confirmation_and_questions_reach_the_parent_transport`, `test_a_rejected_confirmation_does_not_advance_the_turn_label` |
| 6 | Nothing else the child emits reaches the parent: no `THINKING` or `LLM_TEXT` (sub-agent reasoning is not shown), no `TURN_BEGIN` / `TURN_END` / `LLM_CALL_*` / `TOOL_RESULT` (replay would record the child's turns as the parent's), no permission or memory events. | `test_events_outside_the_whitelist_stay_with_the_child` |
| 7 | A background sub-agent refuses every confirmation and its child transport sends no progress events to the parent. Its public `SubagentLifecycleEvent`s are unaffected. | refusal: `test_background_subagent_approval.py::test_a_call_that_needs_confirmation_runs_only_where_someone_can_approve_it`; "no progress events": `test_a_background_child_sends_the_parent_nothing_and_refuses` |
| 8 | Exactly one `AGENT_END` follows every `AGENT_START` for a normal return, an `Exception` and `AgentCancelledError`; it comes before the public terminal event; a failing send masks nothing. | `tests/test_subagent_agent_end_on_raise.py` (#514) |
| 9 | A sub-agent that runs out of turns stops. | `test_running_out_of_turns_stops` |
| 10 | The public `build_compat_transport` keeps its contract for external callers. | `tests/test_transport.py` |
| 11 | A parent transport replaced after the agent tools are registered (replay starting or ending) receives the sub-agent's events and confirmations. | `test_a_transport_replaced_after_registration_gets_the_events` |

## 8. Acceptance and tests

**A:**
- Regression tests for an ordinary exception, a construction failure (the §4.1 probe) and a raised `AgentCancelledError`. Each checks the number, order and state of the start and end events, that `AGENT_END` precedes the public terminal event, and that the original exception still propagates.
- A test where sending `AGENT_END` raises: the original exception still propagates and the public terminal event is still sent.
- Keep the existing test for a cancel that returns normally.
- CHANGELOG entry under *Fixed*.

**B:**
- Move the fixtures in `tests/test_subagent_tool_call_id.py` onto the real production forwarder, and keep every behaviour assertion:
  - same-named parallel tools keep separate `call_id`s through start, confirmation, output and completion;
  - real status, error and duration, and the §6.3 defaults, are unchanged;
  - the confirmation and start labels match, and a rejected confirmation does not advance the turn;
  - `confirm_tool` still reaches the parent transport with the `[name]` prefix;
  - an event outside the whitelist does not reach the parent transport;
  - a background child always refuses confirmation, and its child transport sends nothing to the parent;
  - a sub-agent that runs out of turns stops;
  - the public `build_compat_transport` keeps its old contract;
  - after the parent's `agent.transport` is replaced post-registration, sub-agent events and confirmations reach the new transport.
- Move the `_watch()` fixture in `tests/test_subagent_cancelled_terminal_state.py` to capture `AgentEvent`s; keep its cancel-state and exactly-one-end assertions.
- Keep the existing ACP output assertions and the real-`ToolRunner` rejected-confirmation test. Do not replace them with tests that only inspect the forwarder.
- No CHANGELOG entry is needed for B (no behaviour change).

**Order of verification:** the related regression tests first, then the default suite and `ruff check .`.

Implementation reuses the existing transport, event types and state classification. No bridge class, event bus, routing configuration or general field-normalization framework.
