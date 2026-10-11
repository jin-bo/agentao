# Sub-agent event bridge: callbacks out, events back in

**Status:** proposal, **not authorized**. §5 (option A) is a small defect fix that does not depend on the rest. §6 (option B) is a refactor with no user-visible change; do it only when this code is next opened for another reason.
**Anchors:** agentao `main@60fcca9` (2026-10-11). Line numbers below are at that commit.
**Related:** `0-5-0-removal-checklist.md` §C (why the sub-agent factory uses `build_compat_transport` today); `host-tool-injection.md`; `embedded-host-contract.md` (the public `SubagentLifecycleEvent`, which this doc does not change).

## 1. Summary

A foreground sub-agent reports its progress to the parent through six legacy callbacks. Inside the child, `build_compat_transport` turns the child's `AgentEvent`s *into* those callbacks. In the parent, `tooling/agent_tools.py` turns the callbacks back *into* `AgentEvent`s on the parent transport. Two private conventions ride the callback layer to survive the trip: the `__agent_start__` / `__agent_end__` sentinel names and an `__call_id__` key smuggled into the tool arguments.

An external review (2026-10-10) proposed removing the round trip. Reading the code shows the round trip is narrower than that review said (§3), and it turned up one confirmed defect (§4.1). This doc records the facts, the invariants a change must keep (§7), and two options:

- **A — fix the lifecycle gap (recommended now).** A sub-agent that raises sends `AGENT_START` and never `AGENT_END`. A few lines in `_wrapper.py`, independent of any refactor.
- **B — replace the callback bridge with an event sink (deferred).** The wrapper hands the parent one forwarding function instead of six callbacks. It removes the sentinels, the `__call_id__` smuggling and about 85 lines of translation, with no behaviour change. Worth doing only together with other work in this area.

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

A background sub-agent uses `SdkTransport(confirm_tool=lambda *_: False)` with no event handler, so none of its events reach the parent (`_wrapper.py:613-616`).

The public host event `SubagentLifecycleEvent` is a separate path (`subagent_emitter`, `_wrapper.py:390-430`) and is out of scope.

## 3. What actually round-trips

The review said `__agent_start__`, `__agent_end__` and `__call_id__` all flow through the child transport. Only one does.

| Item | Path | Round trip? |
|---|---|---|
| `__agent_start__` / `__agent_end__` | wrapper → parent `step_callback` → `AgentEvent` | **No.** A one-way sentinel on the callback API. The `AGENT_START` → `"__agent_start__"` mapping in `build_compat_transport` (`sdk.py:251-277`) is **dead for sub-agents**: a child never gets agent tools (CLAUDE.md, *Sub-agents execute from one registry*), so it never emits `AGENT_START`. That branch serves only external users of `build_compat_transport`. |
| `__call_id__` | child `TOOL_START` → `step_callback` args → popped in `agent_tools.py:84` | **Yes.** Event → callback → event. |
| step / confirmation labels, `confirm_tool` prefix | child event → wrapper transform → parent | Not a round trip, but a real transform that any design must keep (§7). |
| `TOOL_OUTPUT` / `TOOL_COMPLETE` `call_id`, `status`, `error` | `_accepted_meta_keywords` signature check (`sdk.py:142-181`) → keywords → parent `AgentEvent` | **Yes.** Event → callback → event. Correct today, kept so by signature introspection. |

So removing the round trip means changing the callback interface between `AgentManager.create_agent_tools` and the parent (`agents/manager.py:137-160`). That is **internal**: `AgentManager`, `AgentToolWrapper` and `SubagentProgress` appear in no reference doc or guide, and `Agentao` builds its own `AgentManager` (`agent.py:885`). `build_compat_transport` itself is public (the 0.5.0 migration surface) and stays.

## 4. Findings while reading

### 4.1 `AGENT_END` is missing when a foreground sub-agent raises (confirmed)

`execute()` sends `__agent_start__` before `_run_sync` and `__agent_end__` only after it returns. Both `except` branches (`_wrapper.py:326-341`) send the public terminal `SubagentLifecycleEvent` and re-raise, but no `__agent_end__`.

Probe, at `60fcca9`: a wrapper whose `llm_config_getter` returns `{}` makes `_run_sync` raise `KeyError('api_key')`. The step callback saw `['__agent_start__']` and nothing else.

Effects, by consumer:
- **CLI:** `_on_agent_end` never runs, so the closing rule with the run's state and statistics is not printed (`cli/display.py:676`).
- **ACP:** the client gets `[sub-agent started: name]` and never `[sub-agent finished: …]`.
- **Replay:** `SUBAGENT_STARTED` is recorded with no `SUBAGENT_COMPLETED`, and the entry stays on `_subagent_stack` (`replay/adapter.py:555-590`) for the rest of the session.

Reachable causes include a sub-agent that cannot be built (bad model config) and a cancellation raised outside the turn (the comment at `_wrapper.py:327-331`).

### 4.2 A child's `call_id` is forwarded into the parent's id scope (hypothesis, not reproduced)

`runtime/identity.py` states the uniqueness scope of `tool_call_id` as `(session_id, turn_id, tool_call_id)` and does not assume provider ids are globally unique. The bridge forwards a child's provider id unchanged into the parent's turn, where the CLI and ACP key tool rows by `call_id`. If a provider issued the same id to a parent call and a child call (for example sequential `call_0` ids), a child `TOOL_COMPLETE` could close the parent's `agent_*` row. I have not found a provider that does this, so this is a design risk, not a defect. Option B is where a fix would go (§6.3).

### 4.3 The tool name differs between start and completion (note only)

`TOOL_START` carries the label `[name n/max] tool`; `TOOL_OUTPUT` and `TOOL_COMPLETE` carry the bare `tool`. All three consumers key on `call_id` first (`cli/display.py:460-471`), so this is harmless today. It matters only to a new consumer that keys on the name.

## 5. Option A — close the lifecycle gap

Send `AGENT_END` on the two raising paths in `execute()`, with `state="cancelled"` or `"failed"` and `error=type(exc).__name__` for a failure. The cleanest form is a `try/finally` that sends exactly one end, so a future raising path cannot reopen the gap.

- **Tests:** the probe in §4.1 as a regression test (a raising `_run_sync` sends exactly one start and one end, in that order), plus a cancel variant.
- **Size:** one function; no interface change; CHANGELOG entry under *Fixed*.

This does not depend on option B, and B should keep the same guarantee.

## 6. Option B — an event-sink bridge

### 6.1 Shape

`create_agent_tools` takes three things from the parent instead of six callbacks plus `confirmation_event_callback`:

```python
event_sink: Callable[[AgentEvent], None]        # agent.transport.emit
confirm_tool: Callable[[str, str, dict], bool]  # agent.transport.confirm_tool
ask_user: Callable[..., str]                    # transport_ask_user(agent), unchanged
```

The foreground child gets `SdkTransport(on_event=forward, confirm_tool=prefixed_confirm, ask_user=ask_user, …)`, where `forward` is a **whitelist**:

| Child event | Forwarded as |
|---|---|
| `TURN_START` | unchanged; advances the turn counter unless `display_reset` |
| `TOOL_START`, `TOOL_CONFIRMATION` | `data["tool"]` replaced by the `[name n/max] tool` label |
| `TOOL_OUTPUT`, `TOOL_COMPLETE` | unchanged (`call_id`, `status`, `duration_ms`, `error` intact) |
| anything else | **dropped** (§7, invariant 6) |

The wrapper emits `AgentEvent(AGENT_START | AGENT_END)` itself through `event_sink`, so the sentinels and `SubagentProgress` leave the internal path.

### 6.2 What it removes

- `tooling/agent_tools.py:59-112`: the three parent callbacks that rebuild events (54 lines), replaced by passing `agent.transport.emit`.
- The `__call_id__` injection (`sdk.py:217-226`) and its pop (`agent_tools.py:84`).
- The sentinel constants and calls in `_wrapper.py` (`261-262`, `300-304`, `347-365`).
- The sub-agent's dependence on `_accepted_meta_keywords` signature introspection.

What it does **not** remove: `build_compat_transport`, its `AGENT_START` mapping and `SubagentProgress` stay for external callers. Whether to deprecate them is a separate decision.

### 6.3 Optional: namespace child ids

With the forwarder in one place, §4.2 has an obvious fix: forward `call_id` as `f"{parent_call_id}/{child_call_id}"`. It needs the wrapper to know its own parent call id, which `ToolRunner` does not pass today (it injects only the cancellation token). Do it only if §4.2 is ever reproduced; it changes the ids ACP clients see.

### 6.4 Cost

- About 85 lines of production code removed (54 in `agent_tools.py`, about 25 of sentinel code in `_wrapper.py`, 5 in `sdk.py`); the forwarder adds about 30.
- `tests/test_subagent_tool_call_id.py` (310 lines) builds its fixtures from `build_compat_transport` and `_make_prefixed_step_callback`, and fakes `create_agent_tools` by keyword name. Most of it must be rewritten against the new seam, which is where the risk is: a rewritten test must still fail on the bugs the old one pinned. It is the only test that passes callbacks to `AgentToolWrapper`; three others construct one without callbacks, and `tests/test_async_tool.py` reads the `create_agent_tools` type hints.
- No user-visible change, so no CHANGELOG entry is required; no doc twins name this API.

## 7. Invariants any change must keep

| # | Invariant | Pinned by |
|---|---|---|
| 1 | Each child tool row keeps its own `call_id` across start, confirmation, output and completion, including a same-named parallel batch. | `test_same_name_parallel_completions_keep_distinct_call_ids`, `test_tool_output_carries_call_id`, `test_confirmation_event_keeps_subagent_call_id` |
| 2 | `TOOL_COMPLETE` forwards the real `status` and `error`; a missing id falls back to the tool name. | `test_completion_forwards_real_status_and_error`, `test_completion_without_call_id_falls_back_to_name` |
| 3 | A confirmation opening carries the same `[name n/max]` label as the `TOOL_START` that follows; a rejected confirmation (`display_reset`) does not advance the counter. | `test_confirmation_opening_carries_the_subagent_label`, `test_a_rejected_confirmation_does_not_advance_the_turn_label` |
| 4 | `TOOL_CONFIRMATION` is forwarded only when start events are, or ACP opens a call nothing closes. | `test_confirmation_is_not_forwarded_without_a_step_callback` |
| 5 | `confirm_tool` reaches the parent's transport with the name prefixed `[name]`, so the MCP Skills gate note and the CLI prompt see the same path as today. | not pinned — the prefix is untested today; add a test with B |
| 6 | Nothing else the child emits reaches the parent: no `THINKING` or `LLM_TEXT` (sub-agent reasoning is not shown), no `TURN_BEGIN` / `TURN_END` / `LLM_CALL_*` / `TOOL_RESULT` (replay would record the child's turns as the parent's), no permission or memory events. Today this holds because `build_compat_transport` ignores unlisted types; B must make it an explicit whitelist. | not pinned — add a test with B |
| 7 | A background sub-agent refuses every confirmation and sends no events. | refusal: `test_background_subagent_approval.py::test_a_call_that_needs_confirmation_runs_only_where_someone_can_approve_it`; "no events": not pinned |
| 8 | Exactly one `AGENT_END` follows every `AGENT_START` (after option A). | added by option A |

## 8. Recommendation

1. **Do option A now.** It is a confirmed defect with three visible symptoms and a one-function fix.
2. **Defer option B** until this code is opened for another reason (for example a change to how sub-agent progress is displayed, or a second consumer of child events). On its own it trades about 55 net lines for a test rewrite with real regression risk.
3. **Leave §4.2 as a watched risk.** Act only on a reproduction.
