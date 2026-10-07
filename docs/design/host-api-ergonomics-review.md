# Host API ergonomics review: can embedding be simpler?

**Status:** Review, 2026-10-06. **Decided 2026-10-06:** F1 takes route (a), docs only (§3 F1, *Decision*). Streaming text enters the stable contract (F2). New stable types are exported from `agentao.host` only (F3). F1(a) and F3 step 0 are **implemented in PR #423** (docs and example imports only). The code step for F4 is not yet authorized; step 2 (string modes and exports) is merged, and step 3 (`astream`) is implemented, see the end of this line. Evidence is cited at `main` @ `2750e16`. **Revised 2026-10-06 after review:** F2 narrowed to a minimal `astream` with its lifecycle written out, and the `saas-assistant` transport swap recorded as a defect; F4's thread-pool option dropped; F6 deferred; §4 reordered. **Second revision, after re-review:** F2's close order releases pending queue writes first, early exit requires `aclosing`, and the stream is bound to its own turn by token identity. **Third revision (2026-10-06), exports narrowed:** `PermissionMode` is not exported and `TurnFinished` is dropped; new exports are `CancellationToken`, `TextDelta`, `TurnOutcome`; `Agentao(permission_mode=...)` decided. F7 added: keep every export, tier the guides, add `__dir__`. **Step 2 implemented (2026-10-06):** silent start, `"plan"` refused at construction, replay posture as a follow-up; merged as PR #426. **Peer comparison (2026-10-06):** a proposal drawn from Pydantic AI and Strands is reviewed in §7. It adds F2's *deltas are display, the outcome is the answer* rule, F8 (return annotations on the host's own methods) and F9 (a function-tool adapter, after `astream`, on demand). It finds approval-only hosts already served by `SdkTransport(confirm_tool=...)`, and it widens §5. The additions from this comparison (F2's two new bullets, F8, F9, §7) cite `main` @ `ef8a2d2`; everything else still cites `2750e16`, where `agent.py` line numbers are lower, for example `active_permissions()` is `:1036` there and `:1086` at `ef8a2d2`. **Step 3 implemented (2026-10-06):** `astream` as designed in F2, plus `max_iterations=`; see F2, *Decided during implementation*. **Step 4 implemented (2026-10-06):** F8's annotations, with the typing gate extended to a host's use of them; see F8, *Decided during implementation*.
**Audience:** agentao maintainers deciding what to change in the embedded-host surface, and reviewers of any follow-up PR.
**Companions:**
- `docs/design/host-api-ergonomics-review.zh.md`: Chinese version, same content
- `docs/design/embedded-host-contract.md`: where the stability boundary was drawn, and why
- `docs/reference/host-api.md`: the stable contract as it exists
- `docs/guides/embed-for-agents.md`, `docs/guides/embedding.md`: what hosts are told to write

## 1. Question and method

The question was whether the host-facing API could be simpler. This review compares **what a host has to write and import** against **what the stable contract covers**. The sources were `agentao/agent.py`, the Python examples under `examples/`, and the two embedding guides. Each finding gives the `file:line` it rests on.

**Conclusion first:** the number of constructor parameters is not the main cost. The main cost is that **defaults, docs and examples disagree**, and that the most common host need, streaming a chat UI, sits **outside** the stable contract.

## 2. Summary

| # | Finding | Proposal | Compatibility |
|---|---|---|---|
| F1 | A headless host approves every ASK, and the docs do not say so; an engine-less agent also cannot switch mode | **Decided: (a) docs only.** (b) and (c) not adopted | None |
| F2 | Streaming text is outside the contract; every chat example imports internals, and `saas-assistant` swaps the transport per request in a way that misroutes events | Minimal `Agentao.astream()` yielding `TextDelta`, then the `TurnOutcome`; attached by subscription; deltas are display, `TurnOutcome.text` is the answer | Additive; audit schema unchanged |
| F3 | Imports are spread over 8 modules; `set_permission_mode`'s argument type is not public; examples use wrong imports | Fix the examples' imports; string modes (enum not exported); `Agentao(permission_mode=...)`; export `CancellationToken` from `agentao.host` | Additive |
| F4 | No `with` / `async with`; every host writes `try/finally close()` | `__enter__/__exit__`; `aclose()` = `asyncio.to_thread(close)`; host ends its turns first | Additive |
| F5 | `chat()` returning a string does not mean the model answered | Covered by F2's final event; `chat()` unchanged | n/a |
| F6 | Duplicate observer aliases; 32 constructor parameters | **Deferred**; leave the constructor alone | n/a |
| F7 | Guides list `agentao.host` flat and incompletely; `dir()` hides the lazy tool types | Keep every export; tier how the guides present them; add `__dir__` | Additive |
| F8 | `events()` and `active_permissions()`, the two host methods the guides lead with, have no return annotation | Annotate the host-facing methods of `Agentao` | Additive |
| F9 | A host tool needs a class, even for one plain function | A thin function → `Tool` / `AsyncToolBase` adapter, after `astream`, on demand | Additive |

## 3. Findings

### F1. A headless host approves every ASK, and the docs do not say so

**What happens.** `runtime/tool_planning.py::_decide` (`:654-689`) treats an engine `DENY` or `ALLOW` as final. An engine `ASK`, or no matching rule, falls through to the tool's `requires_confirmation`, and from there to `transport.confirm_tool`. When the host passes no transport, the constructor uses `NullTransport()` (`agent.py:371`). Its `confirm_tool` returns `True` for everything except a confirmation raised by the MCP Skills gate (`transport/null.py:29-34`).

So in a headless host, **"ask" means "allowed"**, with or without an engine. Under the `workspace-write` preset (`permissions.py:508-554`) that covers:
- shell commands outside the read-only allowlist;
- `web_fetch` to an unlisted domain, and `web_search`;
- writes into `.git/`, `.agentao/` and credential-shaped paths, which the preset deliberately makes ASK even in this mode.

What still stops a call:
- read-only mode;
- an engine `DENY`: the preset's shell deny pattern and its `web_fetch` domain blocklist, both **only when an engine exists**;
- the hardline command floor;
- the MCP Skills gate;
- `web_fetch`'s own `url_policy` validation.

**An agent with no engine is weaker still:**
- None of the preset's DENY rules are evaluated.
- `set_permission_mode()` raises `ValueError` (`agent.py:1598-1618`, `runtime/permission_mode.py`).
- `active_permissions()` still reports `mode="workspace-write"`, with source `default:no-engine` (`agent.py:1036-1058`), although no rule is evaluated.

**What the docs say:**
- The guide's "copy this for host integration" skeleton (`embed-for-agents.md:67-86`) passes `transport=NullTransport()` and no engine.
- Its §5 then says `workspace-write` is the default and should be set with `agent.set_permission_mode(...)`. On that skeleton, the call raises.
- `embedding.md:193` lists the default transport as `NullTransport()` without saying what it answers.
- A grep of both guides and `host-api.md` for "approve" or "auto-approve" finds no statement that ASK becomes allow.

`build_from_environment` does create an engine (`embedding/factory.py:250-256`), but it also defaults to `NullTransport`. ASK is therefore approved on that path too.

**Not a bug in the runtime.** Approving on ASK is a recorded decision: CLAUDE.md says "`NullTransport`'s approve-everything stays as the headless-host default", and background sub-agents already use a deny-on-ASK transport. The clear defect is in the docs. The default itself is the maintainer's call, made below (*Decision*: route (a)).

**Options. These are not mutually exclusive.**
- **(a) Docs only.** Say plainly in `embed-for-agents.md` §1/§5 and `embedding.md` §2 that `NullTransport` answers yes to every ASK. Show the fail-closed form, `SdkTransport(confirm_tool=lambda *_: False)`. Fix §5 so it no longer promises an engine default to a skeleton that has none. No code change.
- **(b) A default engine.** When `permission_engine=None`, construct `PermissionEngine(project_root=working_directory)`. The engine does no file I/O, so pure injection stays side-effect-free. Effects: the preset's DENY rules apply, `set_permission_mode` works, and `active_permissions()` reports real rules. This is a behaviour change: some calls that used to be allowed become denies, and the source label changes. It needs a CHANGELOG entry and both doc twins.
- **(c) Change the headless default to deny-on-ASK.** This is breaking for every headless host that relies on today's behaviour. Only with an explicit maintainer decision and a migration note.

**Decision (maintainer, 2026-10-06): route (a).** Only the docs change. The runtime keeps today's behaviour:
- `NullTransport` still approves every ASK;
- an engine-less `Agentao(...)` gets no default engine;
- `set_permission_mode()` still raises `ValueError` without one.

What (a) has to deliver:
- `embed-for-agents.md` §1 and §5, and `embedding.md` §2, say plainly that `NullTransport` answers yes to every ASK, and list what still stops a call (the bullets above).
- Show the fail-closed form, `SdkTransport(confirm_tool=lambda *_: False)`, next to the skeleton.
- §5 stops promising a `workspace-write` default and a working `set_permission_mode` to a skeleton with no engine. It says a host that wants modes or preset DENY rules passes `permission_engine=PermissionEngine(project_root=...)`, or uses `build_from_environment`.
- `active_permissions()`'s `default:no-engine` source is explained as "no rule is evaluated".
- Both doc twins wherever a twin exists, and a pass over the examples' READMEs for the same promise.

(b) and (c) are not adopted. Revisiting either needs a new decision recorded here.

**Implemented in PR #423.** Besides the items above, it fixed two statements this review had not listed: `embedding.md` §2 said `permission_engine` "Defaults to a permissive engine" (the default is `None`), and the developer guide's constructor reference (en/zh) gave the factory's engine as `Agentao(...)`'s default. The developer guide already said `NullTransport` auto-approves; the gap was in `docs/guides/`. The *What the docs say* bullets above describe the docs before that PR.

### F2. Streaming text is outside the contract

**Contract.** The `agentao.host` docstring and `host-api.md:27` say assistant text and reasoning are available only through the internal `Transport` / `AgentEvent` stream. The guide's §3 then lists `agentao.transport.AgentEvent` and `Transport.emit` under "DO NOT import".

**Practice.** The chat-shaped examples all reach past that line. `saas-assistant/app/main.py`, `data-workbench/src/workbench.py` and `batch-scheduler/src/daily_digest.py` import `SdkTransport`, and the last two import `EventType.LLM_TEXT` (`transport/events.py:23`). Each of them rebuilds the same plumbing:
1. a callback on the worker thread;
2. `loop.call_soon_threadsafe`;
3. an `asyncio.Queue`;
4. a consumer;
5. a disconnect watcher that trips `token.cancel`.

**`saas-assistant`'s per-request transport swap is wrong, not just internal.** It assigns `agent.transport = SdkTransport(...)` on a pooled agent for each request (`main.py:143`). This was found while checking review comment 1.
- `agent.transport` is only one of the references. The tool runner keeps its own (`runtime/tool_runner.py:80`), and that is what emits `TOOL_CONFIRMATION`, calls `confirm_tool` (`:344-349`) and, through the executor, emits `TOOL_START` (`runtime/tool_executor.py:301`). Replay installs itself by replacing **both** (`replay/manager.py:104-107`). The example replaces only one. Its SSE stream therefore never sees tool events, and tool confirmations still go to the transport the agent was built with.
- With replay on, the swap removes the `ReplayAdapter` from `agent.transport`. LLM events stop being recorded for that turn, while tool events still are.
- The swap happens before `async with lock` (`main.py:143` vs `:151`). A second request on the same session key redirects the first turn's events into the second request's queue.
- It runs `agent.chat` through `asyncio.to_thread`, i.e. the loop's default executor, which `arun()` deliberately avoids (`agent.py:64`, `_get_arun_pool`).

So no design here may require or encourage replacing the transport.

**Proposal: a minimal `Agentao.astream(prompt, *, images=None, cancellation_token=None)`.** Narrowed after review.
- **First-version items:** `TextDelta(text)`, then the turn's `TurnOutcome`, nothing else. Tool and permission events stay on the existing `events()`. Reasoning is added only when a host asks for it.
- **Final item is the `TurnOutcome` itself** (revised 2026-10-06; was a one-field `TurnFinished(outcome)` wrapper). The iterator yields `TextDelta | TurnOutcome`; an `isinstance` check is enough to tell them apart, so there is no event base class and no second schema.
- **Precondition: `TurnOutcome` must become cheap to import.** Measured: `from agentao import TurnOutcome` loads `agentao.runtime.chat_loop` and `agentao.llm.client`. The class itself (`runtime/outcome.py`) imports only `dataclasses` and `typing`; the weight comes from `agentao/runtime/__init__.py`, which imports `chat_loop`, `llm_call`, `tool_runner` and `turn` eagerly. A lazy re-export does not avoid that, because any import of `agentao.runtime.outcome` runs the package `__init__` first. So the definition moves to a lightweight module, and `agentao.runtime.outcome` and top-level `agentao` keep re-exporting **the same class**, so identity checks and existing imports still hold. This also makes true the comment in `agentao/__init__.py` that `TurnOutcome` is "importable without the LLM stack", which it currently is not.
- **Outside the audit schema.** `TextDelta` and `TurnOutcome` are exported from `agentao.host`, but they are not members of the `HostEvent` union, are not projected into replay, and do not enter `docs/schema/host.events.v1.json`. Text already reaches replay through the internal stream; `astream` is a delivery API, not a new audit record.
- **Attach by subscribing to the live transport; never replace it.** `Transport.subscribe` is optional: implementations "may omit this method; consumers should `getattr(transport, "subscribe", None)`" (`transport/base.py:40-49`). `NullTransport`, `SdkTransport`, ACP's transport and `ReplayAdapter` have it; the adapter forwards to its inner transport and returns a no-op when that has none (`replay/adapter.py:231-244`). Two consequences:
  - A live transport with no `subscribe` makes `astream` raise `TypeError`, naming the transport class, **before** the turn starts. There is no fallback to swapping, since a swap changes who answers confirmations and what replay records.
  - Checking for the attribute is not enough. A `ReplayAdapter` always has `subscribe`, but over a subscribe-less inner transport it returns a no-op unsubscribe, which looks like a real one, and no events will ever arrive. The check has to reach the inner transport: either `astream` unwraps the adapter, or the adapter reports whether it forwarded. Which one is decided at implementation; either way that case is refused like the first.
- **Whose text arrives.** Sub-agents run on transports of their own (`agents/tools/_wrapper.py:614-636`), so a subscription on the parent's transport carries only the parent's text.
- **Lifecycle contract:**
  - *No overlap:* already enforced. `run_turn` takes `agent._turn_lock` without waiting and raises `TurnInProgressError` (`runtime/turn.py:72-100`). `astream` inherits this, but the refusal happens **when the worker starts running the turn**, not when `astream` is called. `arun()` goes through the `agentao-arun-*` pool, so with the pool busy, a second request may first wait for a worker and then fail.
  - *Bound to its own turn:* the lock alone does not keep a refused request from seeing another turn's text. `astream` subscribes before its turn starts, so without a filter a second stream could receive the first turn's text and only then be refused. Events carry no turn id. Binding is therefore by token identity:
    - `astream` always mints its own `CancellationToken` for the turn. A caller-supplied token is linked to it through `add_done_callback` (`cancellation.py:102`), with the link removed when the stream ends, and is never used as the turn's token directly. One caller token shared between two calls would otherwise match both.
    - The listener forwards an event only while `agent._current_token is` that token. `run_turn` sets `_current_token` only after taking the turn lock (`runtime/turn.py:139`) and clears it at the end (`:341`). Listeners run inline on the producer thread (`SdkTransport.emit`, `transport/sdk.py:91-97`), so the check sees the turn that is emitting.
    - A request refused with `TurnInProgressError` never had its token installed, so it delivers nothing from another turn.
  - *Queue:* bounded, with the same capacity and full-queue rule as `events()` (`host/events.py:60`; a full queue makes the producer wait). A consumer that stops reading slows the turn; it does not grow memory. The cost is that a producer can be **blocked inside a queue write** when the stream closes, which the close order below has to handle.
  - *Errors:* an exception from the turn is raised from the iterator after the events already queued have been delivered. The `TurnOutcome` is yielded only when the turn returned, as the last item, including the no-answer and `status="cancelled"` outcomes `chat()` returns normally. (Corrected in step 3's review: a `status="error"` turn raises, so it yields no outcome; `last_turn` records it.)
  - *Closing early* (`aclose()`, task cancellation), in this order:
    1. **Mark the stream closed and release pending queue writes.** Under the stream's lock, set `closed` and cancel every pending put. From then on the listener drops events instead of writing. This is the mechanism `EventStream` already uses for exactly this wedge (`host/events.py:76-81`, `:296-320`): reuse its subscriber machinery, or the same pattern, with no new scheduling layer.
    2. **Trip the turn's token.**
    3. **Wait for the turn's cleanup**, bounded, the same way `arun` does (`_await_turn_cleanup`, `agent.py:101`).
    4. **Unsubscribe in `finally`**, whatever happened.

    The order matters. A producer blocked on a full queue sits in a queue write, not at a token check, so tripping the token first would leave the worker blocked while step 3 waits for it. Unsubscribing only removes the listener and does not release a write already in progress. Skipping step 4 leaks the subscription, because the transport holds listeners strongly (`transport/base.py:51-56`).
  - *`break` is not a close.* Leaving an `async for` with `break` does not run an async generator's `finally`. A probe on CPython showed it ran only at event-loop teardown (`asyncio.run`'s shutdown of async generators) while a variable still held the generator; one nothing references is closed as soon as it is garbage-collected (re-probed during step 3's review). Until then the turn keeps running and the producer can block on the full queue. The API docs must therefore require explicit closing when a host leaves early:

    ```python
    from contextlib import aclosing  # Python 3.10+

    async with aclosing(agent.astream(prompt)) as stream:
        async for ev in stream:
            if isinstance(ev, TextDelta):
                send(ev.text)
            if should_stop():
                break  # aclosing runs aclose() on the way out
    ```
- **Deltas are display; the outcome is the answer** (added 2026-10-06, peer comparison §7). The concatenated `TextDelta`s are **not** guaranteed to equal `TurnOutcome.text`, and the docs must say so. `LLM_TEXT` is emitted per chunk of **every** LLM call in the turn (`runtime/llm_call.py:147-152`), including a call that ends in tool calls, so narration such as "Let me check the file" arrives as deltas and is not in the final text. The reverse also holds: `TurnOutcome.text` can be a string no delta carried, such as the `[No response]` placeholder, a harness abort notice or an `[LLM API error: …]` string (`outcome.py:3-7`). A host shows deltas as they arrive and takes `TurnOutcome.text`, checked with `is_answer`, as the result to store or act on. Retries do not duplicate text: a call is retried only while nothing has been shown (`llm_call.py:143-144`, `:155-157`).
- **Why `aclosing`, not a native `async with`.** Pydantic AI's `run_stream_events()` is itself an async context manager and ends with an `AgentRunResultEvent` (checked against its docs, 2026-10-06). `astream` gets the same scoped lifetime from the stdlib's `contextlib.aclosing` over an async generator. That takes no second object type, and the final `TurnOutcome` plays the role of Pydantic's result event. Returning an object that is both an async iterator and an async context manager stays possible later, as an additive change.
- **Where it lives:** above the runtime, as `arun()` plus a subscription. The chat loop does not change.

**Decided during implementation (2026-10-06):**
- **`TurnOutcome` lives in `agentao/outcome.py`**, a standard-library-only module beside `cancellation.py`, and is on the import-layering test's leaf list. `agentao.runtime.outcome` and top-level `agentao` re-export the same class. `TextDelta` is in `agentao/host/stream.py`. The stream itself is `agentao/runtime/astream.py`; `Agentao.astream` is a plain method that checks the transport, then returns the async generator, so a transport with no `subscribe()` raises `TypeError` at the call.
- **The outcome is captured on `TURN_END`, not read from `last_turn` afterwards.** `run_turn` sets `_last_turn_outcome` and emits `TURN_END` while the token is still installed and the lock still held, so the listener takes this turn's outcome. Reading `last_turn` after `arun()` returned would race a later turn on a shared agent, the case §7 names.
- **`ReplayAdapter` is unwrapped** to find the transport that really delivers; the stream subscribes there.
- **Turn binding reads a context variable, not `agent._current_token`** (changed in review). Two agents can share one transport, and then the other agent's events reach the listener while this agent's token is installed; a probe showed its text in the stream. `run_turn` now binds the turn as `(agent, token)` in `cancellation._CURRENT_TURN` from before `TURN_BEGIN` to after `TURN_END`, and the listener compares `current_turn()` in the emitting thread's context: both halves, since a host may pass one token to a nested turn of another agent. Separate from the tool-call `_CURRENT_TOKEN`, whose meaning is unchanged.
- **An emit on the host loop's own thread schedules its queue write instead of blocking** (found in review, probe hung). A host async tool's coroutine runs on the host loop with the turn's context, so if it emits text through the transport, a blocking write would wait for the loop it is blocking. The final drain waits for those scheduled writes. Same rule as `EventStream.publish`.
- **Closing cancels the `arun` task**, which trips the token and runs `arun`'s own bounded cleanup wait, after pending queue writes are released. That is steps 2 and 3 of the close order in one call.
- **`max_iterations=` added**, keyword-only, with `arun`'s meaning and default. The examples that moved to `astream` pass it, and leaving it out would have made `astream` the one entry point without it.
- **Examples moved.** `saas-assistant` no longer swaps the transport; it streams `astream()` deltas inside the per-session lock and keeps its SSE payload (`llm_text` / `chunk`, `done` with `reply`, now with `status`, `is_answer` and `incomplete_reason`). Tool events left that stream: they are on `agent.events()`. `data-workbench` and `batch-scheduler` read text through `astream()` instead of `EventType.LLM_TEXT`.

**Decision (maintainer, 2026-10-06): streaming text enters the stable contract**, in the minimal form above: `astream` yielding `TextDelta` and then the `TurnOutcome`, outside the audit schema. This reverses `host-api.md`'s earlier exclusion of assistant text, for text deltas only; raw tool I/O stays out. When `astream` lands, every statement that text is outside the contract is updated: `host-api.md` (its scope note at `:27`), the `agentao.host` docstring, and `docs/design/embedded-host-contract.md:28-31`.

### F3. Imports are spread out; a public method's argument type is not public

**Spread.** A typical host imports from `agentao`, `agentao.embedding`, `agentao.llm`, `agentao.transport`, `agentao.permissions`, `agentao.cancellation`, `agentao.host` and `agentao.host.protocols`. The most frequent in the examples, by `from … import` lines:

| Import | Lines |
|---|---|
| `agentao` (`Agentao`) | 13 |
| `agentao.embedding` | 6 |
| `agentao.permissions` | 6 |
| `agentao.llm` | 4 |

**The gap:**
- `Agentao.set_permission_mode(mode: PermissionMode)` is public, but `PermissionMode` is in neither the guide's §3 stable list nor `host-api.md` (grep finds no match). Three examples import it from `agentao.permissions`.
- Two examples import `Tool` from `agentao.tools.base` instead of the published `agentao.host.Tool`.
- Two import `agentao.transport.events.EventType`, which is on the "do not import" side. These two can only move once F2 exists. (Moved in step 3.)

**Proposal:**
0. Fix the examples' imports that already have a stable home now (`Tool` from `agentao.host`), together with F1's docs.
1. **Modes are strings at the public entry points.** `set_permission_mode` also accepts `"read-only"`, `"workspace-write"`, `"full-access"`, `"plan"`, the same vocabulary as `ActivePermissions.mode` and `PermissionDecisionEvent.mode` (`Literal[...]`, `host/models.py:58`, `:167`). The entry point validates the string and converts it to the internal enum; an unknown string raises. The enum argument keeps working. **`PermissionMode` is not exported** (revised 2026-10-06): exporting it would give the public contract two spellings of one value.
   - **The return value is unchanged and must be documented as such:** `set_permission_mode()` still returns the previous mode as the internal `PermissionMode` enum (`Optional[PermissionMode]`), not a string. The docs must say so rather than claim "strings everywhere"; changing it would be a compatibility change of its own, and none is made here.
2. **`Agentao(permission_mode=...)`** — decided 2026-10-06 (*Permission posture at construction*, below). A host that only needs a mode imports nothing permission-related.
3. **Export `CancellationToken` from `agentao.host`.** `cancellation.py` imports only the stdlib, so it is a plain import in `agentao/host/__init__.py` and `import agentao.host` still stays off the runtime stack (`tests/test_import_layering.py:477`). Why it is needed: a simple async call can end its turn by cancelling the task, which `arun()` already forwards; anything else (a separate stop button, cancelling across tasks, one cancel signal shared by several calls, a sync host cancelling `chat()` from another thread) passes a token explicitly.
4. ~~Move the examples onto the stable imports once F2 exists.~~ Done in step 3.

**Permission posture at construction — decided 2026-10-06: `Agentao(permission_mode=...)`.** This is not F1's rejected option (b): nothing changes for a host that does not ask.
- **Default `permission_mode=None`:** no engine is created, exactly as today (F1's decision stands).
- **An explicit mode:** `"read-only"`, `"workspace-write"` or `"full-access"` is validated before anything is built, and an engine is created as `PermissionEngine(project_root=working_directory, rules=[])`. No permission file is loaded implicitly; a host that wants `~/.agentao/permissions.json` uses `build_from_environment` or builds its own engine. **`"plan"` is refused** (decided 2026-10-06, see below).
- **The agent starts in the mode silently** (revised 2026-10-06; the first draft applied it through `apply_permission_mode` with `cause="host-init"`). `runtime/permission_mode.py::set_initial_permission_mode` sets the engine's preset and the tool runner's read-only flag directly, after `_wire_tooling`, and emits nothing — neither `PERMISSION_MODE_CHANGED` nor `READONLY_MODE_CHANGED`. A starting state is not a switch: an event would invent a transition from `workspace-write`, reach the host's transport before the constructor returned, and still miss a replay that can only start later. Every `set_permission_mode()` after construction is recorded as before.
- **Decided 2026-10-06: passing both `permission_mode=` and `permission_engine=` raises `ValueError`**, so there is no precedence rule between them to define or document. Same rule as `llm_client=` against the raw LLM config. A host with its own engine sets the mode on it, or calls `set_permission_mode` afterwards.
- **Decided during implementation (2026-10-06):**
  - **`"plan"` is refused by `permission_mode=`**, for `Agentao()` and `build_from_environment()` alike, before any resource is opened. The PLAN preset and a `PlanSession` are separate states, and setting only the preset would deny without the plan prompt telling the model it is planning. The internal `PLAN` mode and existing enum callers are unchanged, and `set_permission_mode("plan")` is still accepted; this adds no new way into plan mode.
  - **One rule for callers: an engine you pass and a mode are mutually exclusive.** `build_from_environment(permission_mode=)` applies the mode to the engine the factory loads from the permission files — an internal engine, not one the caller passed — so the user's rules stay, and starts the agent in it silently too. Found by the second review pass: forwarding the key made the factory always raise.
  - **The parameter type stays `str`** (plus the enum), checked at runtime. Hosts usually read a mode from config, a request or an environment variable, and requiring a `Literal` would make them narrow it first.
  - **Follow-up, separate change: record the starting posture in replay.** Add the permission posture at recording time to `session_started`, so replay shows the actual starting state, including a mode changed after construction and before `start_replay()`. Today `session_started` carries only `session_id`, `cwd` and `model`, so no agent's starting posture reaches replay, injected-engine ones included.
- `PermissionEngine` stays out of `agentao.host` (`host/__init__.py:23-24`, `host-api.md:10-11`). A host that needs `rules=` still constructs one from `agentao.permissions`, as `embed-for-agents.md` §1 shows.

**Decision (maintainer, 2026-10-06): new stable types are exported from `agentao.host` only**, which has the typing gate. There is no second top-level export.

### F4. No context-manager lifecycle

`agent.py` has no `__enter__` / `__exit__` / `__aenter__` / `aclose` (grep finds no match). Every example writes `try/finally: agent.close()`. Async hosts write `await asyncio.to_thread(agent.close)`, as in `saas-assistant/app/main.py`, `embed-for-agents.md` §2 and `embedding.md`.

**Proposal:** add `__enter__/__exit__` that calls `close()`, plus `aclose()` and `__aenter__/__aexit__`. These are purely additive.
- `aclose()` is `await asyncio.to_thread(self.close)`, the form the guides already recommend. No new scheduling.
- Precondition, documented rather than enforced: the host ends its active turns before closing.
- Not the `agentao-arun-*` pool. An earlier draft said `close()` would "queue behind running turns" there. That was wrong: a shared multi-worker pool gives no such ordering, and with a free worker `close()` would run at once, beside the turn.

### F5. A returned string does not mean the model answered

`chat()` / `arun()` return `str`. Whether the model actually answered is on `agent.last_turn`: `TurnOutcome.status` / `incomplete_reason` (`outcome.py:30-52`). Guide §6.1 exists to warn about this.

**Proposal:** no change to `chat()`. Changing its return type is breaking and the guide already covers it. F2's `astream` ends with the `TurnOutcome`, so the outcome arrives together with the text.

### F6. Redundant aliases; constructor breadth

- **Aliases.** `add_event_observer` / `remove_event_observer` (`agent.py:1010-1016`) are aliases of `add_host_event_observer` / `remove_host_event_observer`. One in-repo caller remains: `cli/run.py:743`. **Deferred.** The benefit is small, and deprecating then removing a public name is not additive, so it does not belong in an "additive" PR. If taken up later: move that caller, add a `DeprecationWarning`, then remove the aliases in a later minor release.
- **Constructor.** `Agentao.__init__` takes 32 parameters: 5 positional and 27 keyword-only. The LLM can be configured two ways, through the raw-config family or through `llm_client=`; they are mutually exclusive (`_validate_construction_args`). **Not proposed for change:**
  - keyword-only already bounds the misuse risk;
  - grouping parameters into config objects would churn every doc, example and test without closing a defect;
  - the guide already leads with one form.

### F7. A common task should touch few names

Review conclusion (2026-10-06): **no existing export is worth removing or moving now.** The effective simplification is that a host doing a common task only meets a few names, not a shorter full list.

**Today:**
- The guides list `agentao.host` as one flat line (`embedding.md:728-731`), and that line is incomplete: it omits `Tool`, `AsyncToolBase`, `RegistrableTool`, `StreamSubscribeError` and `SubagentUsage`.
- Neither embedding guide says when to use `Tool` or `AsyncToolBase`; that guidance is only in the developer guide's 5.1 (`developer-guide/en/part-5/1-custom-tools.md`).
- `dir(agentao.host)` does not show the three lazily exported tool types. Measured: `Tool`, `AsyncToolBase` and `RegistrableTool` are all absent, because `agentao.host` defines `__getattr__` but no `__dir__`. Top-level `agentao` already has one (`agentao/__init__.py:85`).

**Proposal: keep every export; tier how the guides present them.**

| Export | Where hosts meet it |
|---|---|
| `Tool`, `AsyncToolBase`, `RegistrableTool` | The guides state the choice: a sync tool subclasses `Tool`, an async tool subclasses `AsyncToolBase`, and `RegistrableTool` is for type annotations (for example a list passed as `extra_tools=`). No new common base class. |
| `EventStream` | Mainly the runtime's. Host guides show `agent.events()` and never ask a host to construct one. |
| `RFC3339UTCString`, `SubagentUsage` | Full reference only; minimal integration examples do not import them. |
| `export_host_event_json_schema`, `export_host_acp_json_schema` | In the schema-export section of the reference, not in the ordinary integration guide. |
| The three events, `HostEvent`, `ActivePermissions`, `StreamSubscribeError` | Clear roles; unchanged. |

**Implementation:** give `agentao.host` a `__dir__()` that includes the lazy exports, the same pattern as `agentao/__init__.py:85`, so interactive discovery sees them. It reuses the existing lazy-export mechanism and imports nothing when called.

### F8. The host's own methods are partly unannotated

`agentao/py.typed` ships, and the typing gate is `mypy --strict --package agentao.host` (`.github/workflows/ci.yml:54-55`). It covers the contract's types, not `Agentao`'s methods. Measured with `inspect.signature` over `Agentao`'s public members (2026-10-06):
- no return annotation: `events()` (`agent.py:1068`, returns `EventStream.subscribe(...)`, an async iterator typed with the private union `_PublishedEvent` (`host/events.py:53-57`, `:328-331`), whose public name is `HostEvent`) and `active_permissions()` (`:1086`, returns `ActivePermissions`), the two methods the embedding guides lead with; also `add_message`, `clear_history`, and the `memory_manager` and `compaction_coordinator` properties;
- unannotated parameters: `__init__`'s `transport`, and the `callback` of the four observer methods.

`chat()`, `arun()`, `add_tool()`, `close()`, `last_turn` and `set_permission_mode()` are annotated. So a host's type checker sees `Any` from the first call a guide shows.

**Proposal:** annotate the host-facing methods, `events()` and `active_permissions()` first. Use `TYPE_CHECKING` imports from `agentao.host`, so `agent.py` gains no runtime import. Whether the typing gate is extended to check a host's use of these methods (for example through `tests/test_host_typing.py`'s downstream consumer) is decided at implementation. Internal accessors such as `compaction_coordinator` are annotated or left as they are, but they are not promoted into the contract by this.

**Decided during implementation (step 4):**
- **Every public member is annotated**, not only the six above: `events() -> AsyncGenerator[HostEvent, None]` (a generator type, so a strict host can `aclosing()` it), `active_permissions() -> ActivePermissions`, the four observer methods take a `Callable[[HostEvent], object]`, and the two `add_*` ones return the callback with its own type (a `TypeVar` bound to that callable), `add_message` / `clear_history` / `__init__` return `None`, `transport` is `Optional[CoreTransport]`, and the two properties return `MemoryManager` and `CompactionCoordinator`. All the names come in under `TYPE_CHECKING`, so `agent.py` loads no new module at runtime (it only adds `Callable` and `TypeVar` to its `typing` import). Annotating the two properties types them and promotes neither into the contract.
- **`transport=` is not typed `Transport`.** `Transport` declares `subscribe()` as a protocol member, so that annotation would make mypy reject a custom transport that implements only the four methods the runtime calls, which runs every API but `astream()` (found by Codex review). The four methods are now their own protocol, `CoreTransport` in `agentao/transport/base.py`, and `Transport` extends it with `subscribe()`, so `Transport` itself is unchanged. The host-usage test constructs `Agentao` with such a transport. `CoreTransport` is exported from `agentao.transport`, beside `Transport`, `NullTransport` and `SdkTransport`, and not from `agentao.host`: F3's rule covers the stable data types, and no transport type is on that surface, so `agentao.host`'s 17 names are unchanged. The `agent.transport` attribute is typed `CoreTransport` with it (it was `Any`), so a strict host that subscribes calls `subscribe()` on the transport object it constructed, not through `agent.transport`. An `isinstance(t, Transport)` check would satisfy the type checker but proves nothing at runtime: it passes for a `ReplayAdapter`, whose `subscribe()` registers nothing when the transport it wraps has none, and for a class that subclasses `Transport` explicitly without defining `subscribe()`, which inherits the protocol's no-op stub (found by `/code-review`). `resolve_subscribe` in `runtime/astream.py` checks both cases for this reason.
- **`compact(reason=)` is narrowed** from `str` to `Literal["manual_cli", "api_overflow", "compression_threshold"]`, the three reasons its docstring names, named `ManualCompactionReason` in `agentao/compaction/types.py` beside `CompactionReason`, where the host docs already point for compaction types, with a test that it stays a subset (found by `/code-review`). The whole `CompactionReason` literal was tried first and rejected in review: it also names `microcompact_threshold` and `api_overflow_after_compression`, which belong to other rungs, while `compact()` always runs a `full` compaction. A host passing a plain `str` variable now gets a mypy error; runtime behaviour is unchanged.
- **One runtime fix rode along** (found by `/code-review` while the guide's `ask_user` signature was corrected): the built-in `ask_user` tool and a sub-agent's questions reached the transport through a `**kw` lambda, which hid the method's signature from `invoke_ask_user_callback`, so a transport whose `ask_user` takes only the question raised `TypeError`. The check now runs against the transport method itself (`tests/test_ask_user_transport_signature.py`).
- **The gate checks a host's use, too.** `tests/test_host_typing.py` gains a second downstream consumer that calls these methods and returns each result through a typed `return`, run as `mypy --strict --follow-imports=silent`: `agent.py` is not held to `--strict` itself, so its own errors stay unreported, but an `Any` reaching the host fails `warn_return_any`, and an unannotated method fails `disallow_untyped_calls`. A second test walks `Agentao`'s public members with `inspect.signature`, so a new one added without annotations fails too. Both tests fail on the pre-change `agent.py`.
- **The suite's autouse fixture that backfills credentials** (`tests/conftest.py`) replaced `Agentao.__init__` with a bare `(*args, **kwargs)` shim. It now uses `functools.wraps`, so `inspect.signature(Agentao.__init__)` reads the real signature inside the suite.
- `mypy agentao/agent.py` reports fewer errors than before (20, from 36) and none new; one would have been, because `_compaction_coordinator` was initialized to an untyped `None`, so it is now annotated `Optional[CompactionCoordinator]`.

### F9. A host tool needs a class

Neither Agentao nor its guides offer a way to turn a plain function into a tool (grep for `from_function`, `function_tool`, `FunctionTool` finds no match). A host writes a `Tool` subclass with `name`, `description`, `parameters` (a hand-written JSON schema) and `execute`, even for "look up an order". Pydantic AI and Strands both build a tool from a function's signature and docstring.

**Proposal, deferred until after `astream` and taken up on demand:** a thin adapter that turns a sync or async function into an ordinary `Tool` or `AsyncToolBase`. It is not a second tool system:
- The result is registered through `add_tool` / `extra_tools=` and goes through the same registry, planner, permission engine, events and executor.
- The parameter schema comes from the annotations. `pydantic>=2` is already a core dependency (`pyproject.toml:37`).
- **Safety attributes are explicit and fail closed.** `Tool`'s defaults are `requires_confirmation=False` and `is_read_only=False` (`tools/base.py:112-131`). The adapter keeps them and never infers read-only from a function's name or signature. It does not set `copies_to_subagents` either, so a function tool reaches a sub-agent only when the host declares it.
- Class tools remain the form for state, resources or a lifecycle.

## 4. Recommended order

Revised after review. Each step is its own PR.

1. ~~**F1(a) docs, plus the examples' existing wrong imports** (F3 step 0). Docs and examples only.~~ **Done in PR #423.**
2. ~~**String permission modes, `Agentao(permission_mode=...)`, and `CancellationToken` exported from `agentao.host`** (F3 steps 1–3), plus F7's `__dir__`.~~ **Done 2026-10-06, merged as PR #426**, after five `/code-review --fix` passes; see *Decided during implementation*. F4 remains open.
3. ~~**A minimal `astream`** (F2): first move `TurnOutcome` to a lightweight module, then `TextDelta` + the final `TurnOutcome`, both exported from `agentao.host`, attached by subscription, with the lifecycle above. Then move the examples off `SdkTransport` / `EventType` and fix the `saas-assistant` swap (F3 step 3).~~ **Implemented 2026-10-06**; see F2, *Decided during implementation*.

4. ~~**Return annotations on the host-facing methods** (F8). Small and additive.~~ **Implemented 2026-10-06**; see F8, *Decided during implementation*.
5. **A function-tool adapter** (F9), when a host asks for it.

F4 stands alone now that step 2 has landed; it is small and additive. F7's `__dir__` landed with step 2; F7's guide tiering lands with the guide changes of step 3 (or earlier, as docs only). F6 is deferred.

## 5. Deliberately not proposed

- Splitting the constructor into config objects (F6).
- Changing `chat()`'s return type (F5).
- Deprecating the observer aliases for now (F6).
- Moving goal / continuation loops into the harness. That stays the host's job (`embed-for-agents.md` §7b; `docs/design/codex-goal-mechanism-review.md` §11).
- From the peer comparison (§7), not proposed:
  - a general Capability or Plugin framework for assembling an agent;
  - a `HarnessClient`, `HostAgent` or builder over `Agentao(...)` and `build_from_environment(...)`;
  - parameters that take `bool | str | dict | Manager`, or a mixed mapping syntax for `enabled_tools` / `disable_tools` / `extra_tools`;
  - a generic `RunResult[T]`, or structured model output tied to the run result;
  - a separate approval callback type, or an approval pause/resume or persistence state machine;
  - typed `deps` or per-call usage budgets.

## 6. Questions for the maintainer

1. ~~**F1:** is approve-on-ASK the intended long-term headless default? Is (b) wanted even if (c) is not?~~ **Answered 2026-10-06: route (a).** Approve-on-ASK stays the headless default, and there is no default engine.
2. ~~**F2:** should assistant text enter the stable contract?~~ **Answered 2026-10-06: yes**, as `TextDelta` then `TurnOutcome`, outside the audit schema.
3. ~~**F3:** should the re-exports live in `agentao.host` or in top-level `agentao`?~~ **Answered 2026-10-06: `agentao.host` only.**
4. ~~**F3:** how does a host set a permission posture without importing `PermissionEngine`?~~ **Answered 2026-10-06: `Agentao(permission_mode=...)`**, default `None`. Passing both `permission_mode=` and `permission_engine=` raises `ValueError` (decided 2026-10-06).

**`agentao.host` after steps 2–3:** the 14 names exported today, unchanged, plus `CancellationToken`, `TextDelta` and `TurnOutcome`. None of the existing 14 is removed: each is on the typed stable surface, and removing one breaks hosts for no real simplification. The simplification is in how the guides present them (F7).

## 7. Peer comparison: Pydantic AI and Strands (2026-10-06)

A proposal compared the host surface with Pydantic AI (`Agent(..., capabilities=[...])`) and the Strands harness SDK (`create_harness(...)`, which returns a plain `strands.Agent`). It concluded that the useful lessons are a short common path, a complete result per call, and a stream with a clear end, and that a general Capability or Plugin framework is not needed. This review agrees with that conclusion.

**Checked.**
- Pydantic AI's `run_stream_events()` is used as `async with … as events: async for event in events`, and the stream ends with an `AgentRunResultEvent` (its agent docs, fetched 2026-10-06).
- The Strands statements (factory returning `Agent`, `AgentResult`, `interventions`, mixed parameter forms) were **not** re-checked here. Nothing below depends on them.
- Every Agentao statement was checked against the code; the evidence is in the findings named below.

| # | Proposal | Verdict | Where |
|---|---|---|---|
| 1 | Keep the factory and the explicit constructor, both returning `Agentao`; no client, wrapper or builder | Agreed; already so | §5 |
| 2 | A stream with a clear end: `aclosing`, `TextDelta \| TurnOutcome`, no `TurnFinished` | Agreed; already designed. Adds the rule that deltas are display and `TurnOutcome.text` is the answer | F2 |
| 3 | Bind the result to the call; keep `chat()/arun() -> str`; `astream` delivers the `TurnOutcome`; separate run status from structured output | Agreed. Already so: F5 and F2 | F2, F5 |
| 4 | Simple form plus advanced injection, mutually exclusive; no mixed-type parameters | Agreed; it is step 2's rule (`permission_mode=` XOR `permission_engine=`) | F3, §5 |
| 5 | A function-tool adapter over the existing tool path | Agreed, after `astream` and on demand, with explicit fail-closed safety attributes | F9 |
| 6 | Separate permission posture from host approval; maybe a thin approval-callback adapter later | The adapter already exists, see below | §7 |
| — | Annotate the host's common methods (from the proposal's closing order) | Agreed; measured | F8 |

**On 3: binding the outcome to the call.** `agent.last_turn` is correct for one caller per agent: the turn lock prevents overlap (`runtime/turn.py:72-100`). With several callers on one pooled agent, another request's turn can replace `last_turn` between a `chat()` returning and its caller reading it. `astream` closes that gap because the outcome travels in the stream. A non-streaming entry that returns the `TurnOutcome` is left until a host needs it, as the proposal says.

**On 6: an approval-only host already has a thin adapter.** `SdkTransport` takes every callback as optional (`transport/sdk.py:76-82`). A host that only approves passes `SdkTransport(confirm_tool=my_policy)` and nothing else. It needs no `on_event`, because `agent.events()` still works through the transport's subscription. Without the other callbacks, `ask_user` answers that it is not available and reaching max iterations stops the turn (`transport/sdk.py:101-131`). This is the same form `embed-for-agents.md` §1 shows for the fail-closed case (`confirm_tool=lambda *_: False`). The contract is as the proposal states it: `permission_mode` (or an engine) sets the posture, and the transport answers what is left as ASK. One documentation gap remains: the guide's §3 list of stable imports names `NullTransport` but not `SdkTransport`, although §1 and `host-api.md` use it. That is a docs fix for the guide-tiering pass (F7), not a new API.

**Order after this review:** `astream` (step 3), then F8, then F9 on demand. F4 stays independent. No existing export is removed. New stable data types stay in `agentao.host`, and no Capability, Plugin or construction-config framework is introduced.
