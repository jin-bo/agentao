# Host API ergonomics review: can embedding be simpler?

**Status:** Review, 2026-10-06. Every item below is a **proposal**; none is authorized or implemented. Evidence is cited at `main` @ `2750e16`.
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
| F1 | A headless host approves every ASK, and the docs do not say so; an engine-less agent also cannot switch mode | (a) docs; (b) default engine; (c) maintainer decision on the headless default | (a) none; (b) behaviour change; (c) breaking |
| F2 | Streaming text is outside the contract, so every chat example imports internals | `Agentao.astream()` over a small public event union | Additive; schema snapshot grows |
| F3 | Imports are spread over 8 modules; `set_permission_mode`'s argument type is not public | Accept string modes; publish `PermissionMode` / `CancellationToken` | Additive |
| F4 | No `with` / `async with`; every host writes `try/finally close()` | `__enter__/__exit__`, `aclose()`, `__aenter__/__aexit__` | Additive |
| F5 | `chat()` returning a string does not mean the model answered | Covered by F2's final event; `chat()` unchanged | n/a |
| F6 | Duplicate observer aliases; 32 constructor parameters | Deprecate the aliases; leave the constructor alone | Deprecation only |

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

**Not a bug in the runtime.** Approving on ASK is a recorded decision: CLAUDE.md says "`NullTransport`'s approve-everything stays as the headless-host default", and background sub-agents already use a deny-on-ASK transport. The clear defect is in the docs. The default itself is the maintainer's call.

**Options. These are not mutually exclusive.**
- **(a) Docs only.** Say plainly in `embed-for-agents.md` §1/§5 and `embedding.md` §2 that `NullTransport` answers yes to every ASK. Show the fail-closed form, `SdkTransport(confirm_tool=lambda *_: False)`. Fix §5 so it no longer promises an engine default to a skeleton that has none. No code change.
- **(b) A default engine.** When `permission_engine=None`, construct `PermissionEngine(project_root=working_directory)`. The engine does no file I/O, so pure injection stays side-effect-free. Effects: the preset's DENY rules apply, `set_permission_mode` works, and `active_permissions()` reports real rules. This is a behaviour change: some calls that used to be allowed become denies, and the source label changes. It needs a CHANGELOG entry and both doc twins.
- **(c) Change the headless default to deny-on-ASK.** This is breaking for every headless host that relies on today's behaviour. Only with an explicit maintainer decision and a migration note.

**Recommendation:** (a) now, (b) as its own PR, and (c) only if decided.

### F2. Streaming text is outside the contract

**Contract.** The `agentao.host` docstring and `host-api.md:27` say assistant text and reasoning are available only through the internal `Transport` / `AgentEvent` stream. The guide's §3 then lists `agentao.transport.AgentEvent` and `Transport.emit` under "DO NOT import".

**Practice.** The chat-shaped examples all reach past that line:
- `saas-assistant/app/main.py:33,140-143`, `data-workbench/src/workbench.py` and `batch-scheduler/src/daily_digest.py` import `SdkTransport`, and two of them import `EventType.LLM_TEXT` (`transport/events.py:23`).
- `saas-assistant` assigns `agent.transport = SdkTransport(...)` on a pooled agent for each request (`main.py:143`).

Each of them rebuilds the same plumbing:
1. a callback on the worker thread;
2. `loop.call_soon_threadsafe`;
3. an `asyncio.Queue`;
4. a consumer;
5. a disconnect watcher that trips `token.cancel`.

**Proposal: `Agentao.astream(prompt, *, images=None, cancellation_token=None)`.** It returns an async iterator over a small, closed union published in `agentao.host`:
- `TextDelta`;
- optionally `ReasoningDelta`;
- the existing `ToolLifecycleEvent` / `PermissionDecisionEvent`;
- `TurnFinished(outcome: TurnOutcome)`.

It can be built on what already exists, `SdkTransport.subscribe` (`transport/sdk.py:99`) and `arun()`, without touching the runtime. Closing the iterator trips the turn's token, the same as cancelling an `arun()` task.

**Costs and open points:**
- The new event types enter `docs/schema/host.events.v1.json` under the snapshot policy.
- `host-api.md` left text out on purpose (payload size, raw tool I/O). The proposal carries text deltas only, never raw tool I/O, but whether assistant text belongs in the stable contract at all is a maintainer decision.

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
- Two import `agentao.transport.events.EventType`, which is on the "do not import" side.

**Proposal:**
1. `set_permission_mode` also accepts the mode's string value (`"read-only"`, `"workspace-write"`, `"full-access"`, `"plan"`), validated against `PermissionMode`. An unknown string raises.
2. Publish `PermissionMode` and `CancellationToken` on the documented stable surface. Both modules are light (`permissions.py` imports only the stdlib and `permissions_hardline`; `cancellation.py` only the stdlib). Lazy re-exports through `agentao.host`'s PEP 562 `__getattr__` would still keep `test_import_agentao_host_stays_off_the_runtime_stack` (`tests/test_import_layering.py:477`) honest.
3. Move the examples onto the stable imports once F2 exists.

Open point: whether these names should live in `agentao.host`, which has the typing gate, or in the top-level `agentao`, which is shorter.

### F4. No context-manager lifecycle

`agent.py` has no `__enter__` / `__exit__` / `__aenter__` / `aclose` (grep finds no match). Every example writes `try/finally: agent.close()`. Async hosts write `await asyncio.to_thread(agent.close)`, as in `saas-assistant/app/main.py`, `embed-for-agents.md` §2 and `embedding.md`.

**Proposal:** add `__enter__/__exit__` that calls `close()`, plus `aclose()` and `__aenter__/__aexit__`. These are purely additive.

Open point: which thread `aclose()` runs `close()` on. `to_thread` matches what the guides already recommend. The `agentao-arun-*` pool would queue behind running turns.

### F5. A returned string does not mean the model answered

`chat()` / `arun()` return `str`. Whether the model actually answered is on `agent.last_turn`: `TurnOutcome.status` / `incomplete_reason` (`runtime/outcome.py:22-37`). Guide §6.1 exists to warn about this.

**Proposal:** no change to `chat()`. Changing its return type is breaking and the guide already covers it. F2's `TurnFinished` delivers the outcome together with the text.

### F6. Redundant aliases; constructor breadth

- **Aliases.** `add_event_observer` / `remove_event_observer` (`agent.py:1010-1016`) are aliases of `add_host_event_observer` / `remove_host_event_observer`. One in-repo caller remains: `cli/run.py:743`. Proposal: move that caller over, add a `DeprecationWarning`, and remove them in a later minor release.
- **Constructor.** `Agentao.__init__` takes 32 parameters: 5 positional and 27 keyword-only. The LLM can be configured two ways, through the raw-config family or through `llm_client=`; they are mutually exclusive (`_validate_construction_args`). **Not proposed for change:**
  - keyword-only already bounds the misuse risk;
  - grouping parameters into config objects would churn every doc, example and test without closing a defect;
  - the guide already leads with one form.

## 4. Recommended order

1. **One small PR:** F1(a) docs, F3(1) string modes, F4 context managers, F6 alias deprecation. Additive apart from the docs.
2. **F1(b) default engine:** its own PR, with CHANGELOG and both doc twins.
3. **F2 `astream`:** settle the event union and the schema decision first, then implement, then move the examples and do F3(3).
4. **F1(c):** only on an explicit maintainer decision.

## 5. Deliberately not proposed

- Splitting the constructor into config objects (F6).
- Changing `chat()`'s return type (F5).
- Moving goal / continuation loops into the harness. That stays the host's job (`embed-for-agents.md` §7b; `docs/design/codex-goal-mechanism-review.md` §11).

## 6. Questions for the maintainer

1. **F1:** is approve-on-ASK the intended long-term headless default? Is (b) wanted even if (c) is not?
2. **F2:** should assistant text enter the stable contract, and under which schema version?
3. **F3:** should the re-exports live in `agentao.host` or in top-level `agentao`?
