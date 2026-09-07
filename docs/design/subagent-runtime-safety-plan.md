# Sub-agent runtime safety — the engine shared by identity, the registry rebuilt by origin, MCP ownership and cancellation

> ⚠️ **Status:** this plan was **PR-0** of the old PowerShell plan and was split out verbatim on
> 2026-09-03 (rev 25). It has nothing to do with PowerShell and does not wait for it. **That
> PowerShell design was retired on 2026-09-06** (the lightweight design is in
> `powershell-support-lightweight.md`; the history is in Git), and this plan is not retired with
> it. **The engine half is a measured, live defect that predates the plan** (evidence §2.12: a
> sub-agent has no permission engine, `rm -rf /` is judged ASK, and three transports auto-approve
> it) **and should be fixed now. The MCP half — owner thread, leases, token → task set, call
> context — is not authorised for implementation**; rev 20 found a sentence there with a
> guarantee and no mechanism (§3, item 4). The two halves sit in one PR because rebuilding the
> registry in the factory forces a decision about the form in which MCP tools enter a sub-agent;
> shipping the engine half first and leaving the MCP view to a later PR is allowed, and §5 states
> the cut.

**Date:** 2026-09-03
**Anchors:** agentao `main@3537753` (2026-09-01).
**Evidence:** where this document writes "§2.x" it means the same-numbered subsection of the
then-current `docs/reference/powershell-support-evidence.zh.md` — §2.6, §2.8, §2.9 and
§2.12–§2.19 are the entirety of this plan's measured basis. **That evidence file was retired with
the design set**; retrieve it with
`git show HEAD~1:docs/reference/powershell-support-evidence.zh.md`.
**Review record:** likewise in Git history (`powershell-support-review-log.zh.md`; the lines for
rev 2, 4, 5, 7–11, 20 and 22–24 all touch PR-0). The thirty-two method rules that review produced
live in `docs/design/review-method-rules.md`.
**The old PowerShell plan's dependency on this one retired with it:** the lightweight design does
not require a sub-agent to hold the parent's shell spec by identity.

## 0. Invariant index

A rule identifier is defined once, here; everywhere outside this document cites the identifier
only. The defining text is the body of §2–§4 (moved in verbatim at rev 24, unrewritten); this
table is an index, not a second definition.

| ID | Invariant | Defined in | Gates |
|---|---|---|---|
| **SUB-01** | A sub-agent is built by the internal factory `Agentao._for_subagent(parent, definition)`, never through public constructor arguments; `permission_engine`, the parent's one effective `filesystem` and `shell`, and `working_directory` are shared **by identity** | §2 item 1 | G00, G13b |
| **SUB-02** | The registry re-runs `register_builtin_tools` for the sub-agent, rebuilt origin by origin as the intersection of the parent's **live** registry and the definition's allowlist; `ToolRegistry.register` gains a keyword-only `origin` (default `host`), and a replacement records what it displaced | §2 item 2 and its table | G00, G17 |
| **SUB-03** | A host tool that does not implement `ToolForkable` is absent from the sub-agent, **and so is the name it occupied**; `mcp_*` arrives only through a scoped view, never through `enabled_tools` / `remove_tool` | the table in §2 item 2 | G00 |
| **SUB-04** | Agent tools are registered only when the definition **names them explicitly**; a `None` allowlist implies every non-agent origin and implies no agent origin; `agent_manager = None` is deleted | the table in §2 item 2 | G22 |
| **SUB-05** | The factory skips `__init__`'s registration passes and deletes the four after-the-fact assignments (`sub_agent.tools =`, `tool_runner._permission_engine =`, the file read, `engine.set_mode`); `set_readonly_mode(True)` is kept | §2 items 3 and 4 | G00 |
| **MCP-01** | `McpClientManager` holds one **owner thread** running the loop, and every synchronous bridge becomes `run_coroutine_threadsafe(...).result(timeout)`; `scoped(names) -> McpToolView` is a non-owning read-only view | §3 opening paragraph | G00 |
| **MCP-02** | `bg_store` records the **thread** alongside the token; the sub-agent's body is wrapped in `try/finally`, and the `finally` closes the sub-agent and deregisters its view | §3 items 1 and 2 | G00 |
| **MCP-03** | A lease is one in-flight call and nothing else — taken per call and released in `finally`, whoever the caller is; an agent's lifetime is the registration of its view, not a lease | §3 item 3 | G00 |
| **MCP-04** | Cancellation reaches the call through a **call context** (an explicit argument or a `contextvar`), never through a mutable attribute on the tool instance; the manager registers a **set of tasks** keyed by token; subscription and registration are atomic (`add_done_callback` fires immediately on an already-cancelled token); the `finally` clears the registry in the order "unsubscribe → discard → delete the empty key → release the lease" | §3 item 4 | G00 |
| **MCP-05** | The parent's `close()` runs one sequence and **cancellation comes before waiting**: `CLOSING` → refuse new leases → cancel every token → under one deadline wait for the lease count to reach zero and join every recorded thread → disconnect and stop the loop; a timed-out `result(timeout)` is not a cancellation, so `task.cancel()` still has to be scheduled on the loop | §3 item 5 and the last two paragraphs | G00 |
| **MCP-06** | `close()` tears down only what this agent itself owns; the engine, fs, shell, MCP manager and `bg_store` are shared by identity, and a sub-agent's `close()` deregisters only its own view and touches none of them | §3, the "`close()` tears down only…" paragraph | G00 |
| **ENG-01** | Engine state is one immutable record: the values themselves are frozen (rules normalised to tuples), every mutator loads and assigns a new record under one `threading.Lock`, and every reader reads `self._state` once without the lock | §4 points 1, 3 and 4 | G19 |
| **ENG-02** | The backing objects are never handed out: `rules` and `active_mode` are returned as copies, and the list the constructor accepted cannot change policy | §4 point 2 | G19 |
| **ENG-03** | `_active_cache` is not in the record; it is keyed by record identity and discarded with the record | §4 point 5 | G19 |
| **ENG-04** | `PermissionDecisionDetail` carries the record it decided against; the host projection reports the decision's own snapshot | §4 point 6 | G19 |
| **ENG-05** | The writer lock is taken only by mutators and never inside tool execution; it never nests with the runner's per-tool lock | §4 closing paragraph | G19 |

## 1. The defect

Measured in evidence §2.12: the wrapper builds the sub-agent with a fixed keyword list and does
not pass `permission_engine=`; the runner copies `None` into the planner, and the wrapper's
after-the-fact `tool_runner._permission_engine = engine` writes an attribute the planner never
reads. The result is that a sub-agent judges `rm -rf /` as ASK, and `NullTransport`,
`SdkTransport` and the CLI's `full-access` all auto-approve it (§2.6). §2.13–§2.19 record, one by
one, why "rebuild from disk", "rebuild from the allowlist", "assign afterwards", "share the
instance" and "share the MCP loop" are each not the fix.

## 2. Decision — a sub-agent is built by an internal factory from the parent's live state

**PR-0 — a sub-agent is built by the internal factory `Agentao._for_subagent(parent,
definition)`, never through public constructor arguments.** §2.16 shows that `enabled_tools=`,
`extra_tools=` and `remove_tool` each carry a guard or a semantic that defeats this use. The
factory:

1. **Shares capabilities by identity**: `permission_engine`, the parent's one effective
   `filesystem` and `shell`, and `working_directory` (compared on the resolved value).
2. **Re-runs the real registration channel for the sub-agent, and the registry records the
   origin.** The "snapshot of instances rebuilt per class" road does not exist (§2.19): the six
   builtin tools take their dependencies from the agent, so the only correct construction is the
   one that already exists — `register_builtin_tools(sub_agent)`, with the sub-agent's
   `_disable_tools` set to the names the parent has disabled **plus** every builtin name outside
   the definition's allowlist, which is exactly the filter that channel already honours
   (`agentao/tooling/registry.py:135-136`). Every dependency is then the sub-agent's own: its
   transport backs `AskUserTool`, and its `todo_tool` is its own list. At the same time
   `ToolRegistry.register` gains `origin` — `builtin | host | mcp | agent | plan` — stored
   alongside the instance (`agentao/tools/base.py:209`); a replacement must also record what it
   displaced, which is what makes the fourth row of the table below decidable. **It is a
   keyword-only argument defaulting to `host`, not a required one.** Every registration site in
   the repository passes it explicitly — `_bind_and_register`
   (`agentao/tooling/registry.py:80`), MCP (`agentao/tooling/mcp_tools.py:144`), agent tools
   (`agentao/tooling/agent_tools.py:104`), plan tools (`agentao/cli/app.py:336`), and the
   sub-agent's own registry and `CompleteTaskTool`
   (`agentao/agents/tools/_wrapper.py:465-466`) — but `agent.tools.register(...)` is a road hosts
   already use and this repository's own example calls
   (`examples/ticket-automation/src/triage.py:199-202`), so making it required would cause a
   user-visible break inside the one PR that claims no user-visible change. `host` is also the
   fail-closed default: an unclassified tool is a host tool, and a host tool that cannot fork is
   absent from every sub-agent. Origin by origin, read from the parent's *live* registry and
   intersected with the definition's allowlist:

   | Origin in the parent | In the sub-agent |
   |---|---|
   | builtin, present and in the allowlist | constructed by `register_builtin_tools(sub_agent)` (`agentao/tooling/registry.py:83`) from the sub-agent's own dependencies |
   | builtin, disabled or removed by the parent, or outside the allowlist | **absent** — its name joins the sub-agent's `_disable_tools` |
   | a host tool implementing `ToolForkable` (`extra_tools` or `add_tool`) | a fresh instance from `fork_for_agent()`, bound through `_bind_and_register` (`agentao/tooling/registry.py:77-80`) |
   | a host tool not implementing `ToolForkable` | **absent, and so is the name it occupied** — a host that replaced `read_file` and cannot fork does not get the builtin `read_file` back underneath it; one warning that names it |
   | an agent tool | only when the definition **names it explicitly**, re-registered for the *sub-agent* through `_register_agent_tools` so the wrapper captures the sub-agent's getter (§2.17); otherwise **none at all** — the factory skips `_register_agent_tools()` and `agent_manager = None` (`agentao/agents/tools/_wrapper.py:541`) is deleted. **"In the allowlist" is not enough here:** an absent `tools:` key means *all tools* (`agentao/agents/manager.py:57`), and the builtin generalist happens to omit it (`agentao/agents/definitions/generalist.md:1-4`), so reading a `None` allowlist as "all" would hand `agent_generalist` to itself — restoring exactly the recursion that assignment was there to prevent. A `None` allowlist implies every **non-agent** origin and implies no agent origin |
   | `mcp_*` | only when the allowlist names it, through the scoped MCP view described below; never through `enabled_tools` or `remove_tool`, whose guards (`agentao/agent.py:489`, `agentao/agent.py:953`) stay as they are |
   | plan-only | never |

   Finally `CompleteTaskTool()` is added. The result is the one registry the runner and the
   planner both hold (gate 17).
3. **Skips** `__init__`'s builtin, MCP and agent registration passes; assigns nothing afterwards
   to `sub_agent.tools` (`agentao/agents/tools/_wrapper.py:538` deleted) or to
   `tool_runner._permission_engine` (`agentao/agents/tools/_wrapper.py:570` deleted), reads no
   file (`agentao/agents/tools/_wrapper.py:559-562` deleted), and deletes
   `engine.set_mode(mode)` (`agentao/agents/tools/_wrapper.py:569`).
4. **Keeps** `set_readonly_mode(True)` — the runner's own field
   (`agentao/runtime/tool_runner.py:106-109`), read at planning time — passes
   `project_instructions` and `skill_manager` as arguments
   (`agentao/embedding/factory.py:146-148`), and keeps `llm.omit_temperature` with a comment
   naming its reader.

## 3. Decision — MCP ownership: one owner thread, one non-owning view

**MCP ownership: one owner thread, one non-owning view.** Locking at the bridge is the wrong
instrument — it serialises callers only, and whoever holds the lock may still be a different OS
thread driving the loop (§2.18). `McpClientManager` instead holds an **owner thread** that
creates and runs the loop, and every synchronous bridge becomes
`asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)` in place of the bare
`run_until_complete` (`agentao/mcp/client.py:999`). On top of that comes `scoped(names) ->
McpToolView`, a read-only view over the parent's connections exposing only the allowlisted tools.
That view is what the factory registers; it holds no per-agent state and is **non-owning** — a
sub-agent's `close()` neither disconnects the shared manager nor stops the loop
(`agentao/agent.py:1015-1017`).

**The other direction is the owner closing first, and leases alone cannot do it.** A background
sub-agent runs on a daemon thread (`agentao/agents/tools/_wrapper.py:761`) whose handle is
discarded at `start()`, and what `bg_store` registers per agent is a `CancellationToken`
(`agentao/agents/bg_store.py:490`, `agentao/agents/bg_store.py:494`) rather than the thread — so
nothing in the process can join it, and the manager certainly cannot join something nobody
recorded. There is no release point either: the normal return is `return result, stats`
(`agentao/agents/tools/_wrapper.py:653`), which closes no sub-agent. Five things, all of them
extensions of what already exists rather than a second system beside it:

1. `bg_store` records the **thread** next to the token it already holds; `cancel`
   (`agentao/agents/bg_store.py:380`) keeps its semantics.
2. The sub-agent's body is wrapped in `try/finally` — foreground and background alike — and the
   `finally` closes the sub-agent and deregisters its view.
3. **A lease is one thing: one in-flight call.** It is not an agent's lifetime, and the two
   behave differently. Every MCP call takes a lease for its own duration and releases it in
   `finally`, whoever the caller is — a background sub-agent, a foreground turn, or the embedding
   host's own thread. An agent's lifetime is the registration of its *view*, which is not a
   lease.
4. **Cancelling a token has to actually reach the call, and today nothing carries it there.**
   `McpTool.execute()` is synchronous and takes only `**kwargs` (`agentao/mcp/tool.py:118`), and
   the executor injects the token only into tools that already carry the attribute —
   `if cancellation_token and hasattr(tool, "_cancellation_token")`
   (`agentao/runtime/tool_executor.py:351-352`) — which in-tree is only `AgentToolWrapper`
   (`agentao/agents/tools/_wrapper.py:220`). `McpTool` has no such attribute, so a cancelled
   `bg_store` token never reaches the coroutine on the owner loop: step 5's cancellation is a
   no-op, and the whole deadline is then spent waiting for a lease nobody asked to be released.
   So the synchronous bridge takes a **call context** carrying the token — **an explicit argument
   or a `contextvar`, not the mutable attribute the executor already writes.** That attribute
   hangs on the tool instance, the executor writes it only when the token is truthy
   (`agentao/runtime/tool_executor.py:351-352`), and the per-tool lock serialises only *within one
   batch* (`agentao/runtime/tool_executor.py:200-202`) — so a call arriving without a token
   (which is what a host calling `ToolRunner.execute()` directly is) reads whatever the previous
   call left behind. If the residue is an **already-cancelled** token the consequence is not
   harmless but worst-case: `add_done_callback` fires immediately on a cancelled token, so the new
   call is cancelled the moment it registers. A context is per call and per worker thread and
   leaves nothing behind — and the manager registers that lease's `asyncio.Task` into **a set
   keyed by that token**, not "one token, one task". **One token covers a whole batch:**
   `execute_batch(plans, *, cancellation_token=None, …)`
   (`agentao/runtime/tool_executor.py:188-192`) takes a single token for every plan in the batch,
   so N parallel MCP calls from one sub-agent are N tasks under one key, and a `dict` keeps only
   the last — after cancellation N−1 calls are still running while the manager believes it
   cancelled them. Cancelling the token cancels **every** task in the set, each of them **through
   the loop** (`loop.call_soon_threadsafe(task.cancel)`, never a direct `task.cancel()` on the
   calling thread), and each cancelled coroutine releases its own lease in its own `finally` —
   which is the acknowledgement step 5 waits for. **That `finally` also clears the registry, and
   the order is the only one that works:** unsubscribe the cancellation callback → `discard` the
   task from its set → delete the key when the set is empty → release the lease. A manager that
   only ever adds accumulates task references per call, which on a long-lived owner is a leak
   rather than a wrong verdict, and gate 0 asserts the registry is empty on both the normal and
   the cancelled path. Because the context is per call, none of this depends on "each agent
   registers **its own** `McpToolView` instance" (the scoped view above) — it does, but it is the
   attribute spelling that would have needed it. **And the subscription is atomic with the
   registration rather than after it:** `CancellationToken.add_done_callback` fires immediately
   when the token is already cancelled and hands back an unsubscribe handle for the `finally`
   (`agentao/cancellation.py:97-105`), so a cancellation landing between "lease taken" and "task
   registered" still cancels. The earlier answer — a token with no task under it is a call that
   has not started, and `CLOSING` refuses new leases — covers only `close()`: an ordinary
   `bg_store.cancel()` never enters `CLOSING`, and without atomic subscription the task registered
   a moment later would keep running.
5. The parent's `close()` runs one sequence, and **cancellation comes before waiting**: `CLOSING`
   → refuse new leases → **cancel every token** (which by item 4 cancels every task registered
   under it) → under **one** deadline both wait for the active lease count to reach zero and join
   every recorded thread → disconnect and stop the loop, logging whatever was given up. The order
   matters: a long MCP call releases its lease only when cancelled, so waiting for the count to
   drop first spends the entire budget waiting on something nobody asked to stop. Draining leases
   is the primary wait and joining threads the secondary one: a call from a foreground or host
   thread holds a lease and is in no thread set, and a design that waits only on the `bg_store`
   threads would disconnect underneath exactly the callers it does not know about.

**`close()` tears down only what this agent itself owns, and a sub-agent owns nothing handed to
it.** The store belongs to the parent and is passed in at construction so the sub-agent's registry
can serve `check_background_agent` — *"Inherit the parent's background-task store"*
(`agentao/agents/tools/_wrapper.py:522-527`). Without this rule, step 2 above is not a fix but a
defect: a sub-agent's `finally` running step 5 would cancel its **siblings** and try to join the
very thread it is running on. So ownership is recorded at construction — the engine, filesystem,
shell, MCP manager and `bg_store` are all *shared by identity*, and a sub-agent's `close()`
deregisters its own view and flushes its own state and touches none of them. It has no lease to
release, because a lease is one in-flight call and `close()` is not one. Only an owner's `close()`
runs step 5. Gate 0 asserts that still-running siblings are unaffected.

**And the manager closes in stages, because "cancel" is not "refuse".** A thread that outlives the
join budget is still alive, and would otherwise request a fresh lease after the manager considered
itself finished. `McpClientManager` enters `CLOSING` first and refuses every new lease request in
that state; only then does it cancel, wait out the budget, and disconnect.

A timed-out `result(timeout)` is **not** a cancellation — the coroutine is still running on the
owner loop — so the timeout path also has to schedule `task.cancel()` through that loop, which is
the rule `agentao/tools/web.py:634-639` already follows for its own thread hand-off. Gate 0 checks
both directions along with the callback and todo checks.

## 4. Decision — the engine: one writer lock, lock-free readers, verdicts carrying their snapshot

**The engine: one writer lock, lock-free readers, and a verdict that carries its snapshot.**
Collapsing the engine's mutable fields into one atomically swapped record fixes torn reads
(`agentao/permissions.py:597-598` writes; `agentao/permissions.py:702`,
`agentao/permissions.py:705` and `agentao/permissions.py:712` read) — **not** lost updates: two
mutators load the same old record and each assign a new one, and the loser's change is dropped,
which is `add_run_rules`' deny under a concurrent `set_mode`. So:

- **The values inside the state are immutable, not merely swapped wholesale.** An atomic record
  alone is not enough: `_mode_rules` **is** the module-level preset list rather than a copy of it
  (`agentao/permissions.py:598`), and `add_run_rules` extends those live lists in place
  (`agentao/permissions.py:633`, `agentao/permissions.py:635`) — so a lock-free reader holding a
  record still aliases a list another thread is growing, and whoever receives `rules` can mutate
  the presets of every engine in the process. Rules are normalised to frozen values at the
  validator boundary, the state holds them as tuples, and every mutator builds a new tuple.
- **The backing objects are never handed out.** `rules` and `active_mode` survive as compatibility
  properties but are handed out as copies — the engine has readers of its own
  (`agentao/permissions.py:810`) and there are unknown ones outside — so neither mutating what was
  handed to the caller nor mutating the list it passed to the constructor
  (`agentao/permissions.py:579`) can change any policy.
- **Every mutator** (`set_mode`, `add_run_rules`, `add_loaded_source` and any host setter) runs
  under one `threading.Lock`, loading the current record inside the lock and assigning the new one
  before releasing it.
- **Every reader** (`decide_detail`, `active_permissions`) loads `self._state` once without the
  lock and reads only from that record.
- **`_active_cache` is not in the record.** A cache derivation written back after an updated
  record is installed resurrects the old policy. The cache is keyed by record identity and
  discarded with the record.
- **`PermissionDecisionDetail` carries the record it decided against.** The host projection builds
  its event by calling `active_permissions()` at a later moment
  (`agentao/host/projection.py:245`); it now reports the decision's own snapshot, so the mode and
  rule set the event names are the ones that produced the verdict.

**Why this writer lock cannot deadlock with the runner's:** the runner's per-tool lock is held
during *execution* (`agentao/runtime/tool_executor.py:405`); `decide_detail` is a *planning* call
and takes no lock; the writer lock is taken only by mutators, and no mutator is ever called from
inside tool execution. The two families never nest.

## 5. PR-0

| PR | Content | User-visible | Depends on |
|---|---|---|---|
| **PR-0** | (gates 0, 19, 22) **`Agentao._for_subagent`: the parent's engine, one effective fs/shell, an `origin` recorded on every registration, the registry rebuilt by re-running `register_builtin_tools` for the sub-agent, `ToolForkable`, an MCP owner thread plus a non-owning scoped view, no agent tools registered; engine state immutable behind one writer lock, verdicts carrying their snapshot; the projection reporting the verdict's snapshot** (§2.12–§2.19) | no — it closes a live bypass | — |

**PR-0 needs nothing from the PowerShell plan** — an internal factory, an origin field on the
registry, a protocol, a view, an owner thread, a lock, a "token → task **set**" registry along
with the call context that feeds it, and one field on the decision detail. There was exactly one
dependency in the other direction: the PowerShell plan's PR-1 required a sub-agent to hold the
parent's shell spec by identity (SUB-01), so that plan's ladder listed PR-0 as a prerequisite.

**The permitted cut:** the engine half (SUB-01–SUB-05, ENG-01–ENG-05; the non-MCP assertions in
gate G00, plus G13b, G17, G19 and G22) may ship first, and the MCP half (MCP-01–MCP-06; the MCP
assertions in G00) after. When cut that way, the factory's treatment of `mcp_*` in the first
segment is **absent** (the same row as a host tool that cannot fork) rather than a shared parent
instance — sharing the instance is precisely what §2.15 and §2.18 say cannot be done.

## 6. Gates

- **G00 · PR-0's probe** (§2.12) returns DENY through `NullTransport`, in the foreground and in
   the background; sub-agents produced by a parent with an in-memory deny, a run-scope deny and
   `enable_hardline=False` all honour it; a readonly parent produces a readonly sub-agent; the
   sub-agent's engine, filesystem and shell are the parent's by identity while its tools are not
   the parent's instances; after a background sub-agent has run a tool, the parent's
   `output_callback` and todo list are unchanged; a builtin the parent disabled is absent from the
   sub-agent; a forkable host tool outside the allowlist is absent; a non-forkable host tool that
   replaced `read_file` leaves the sub-agent without `read_file`; a sub-agent whose definition
   names no agent tool has zero `agent_*` tools; a parent and a background sub-agent calling the
   same MCP server concurrently both complete correctly. **The sub-agent's `ask_user` reaches the
   sub-agent's transport and its `todo_write` writes its own list — the six builtins whose
   dependencies come from the agent are constructed from the sub-agent (§2.19); every registered
   tool carries an `origin`, and a host tool that replaced a builtin records what it displaced;
   after a sub-agent's `close()` the parent's MCP connections and loop are still alive, **and
   still-running sibling sub-agents are untouched — it cancels not one token (beyond its own),
   joins not one thread, and above all does not join the one it is itself running on**; the same
   holds when the in-flight call is on a **foreground turn or the host's own thread** (which is in
   no thread set): `close()` waits for the leases to drain and that call finishes; a tool
   registered through a bare `agent.tools.register(tool)` still registers, with origin `host`.
   And the other direction: closing the parent while a background sub-agent is inside a long MCP
   call cancels and joins it before disconnecting, and a timed-out `result(timeout)` does not leave
   the coroutine on the owner loop. **And it asserts that the cancellation *arrived*, not merely
   that it was issued:** a background sub-agent is inside a long MCP call, its `bg_store` token is
   cancelled, the task registered under it is cancelled on the owner loop, its `finally` releases
   the lease, and afterwards no task is alive on the loop — a test that only checks `close()`
   returned would pass on today's code too, and today `McpTool` never receives that token
   (`agentao/mcp/tool.py:118`, `agentao/runtime/tool_executor.py:351-352`). **Two barriers, both
   testing the shape of this registry rather than its existence:** a sub-agent issues **two**
   parallel MCP calls in one batch — one token, two tasks — and both are cancelled and both leases
   released, which a "one token, one task" registry cannot pass; and a token cancelled **before**
   its task registers still cancels that task, asserted by holding the registration at a barrier
   until after `cancel()` returns, along the ordinary `bg_store.cancel()` path that never enters
   `CLOSING`.**

- **G13b ·** a sub-agent holds the parent's engine by identity (the second half of the old gate
   13; the first half, "the snapshot reaches every root", stayed in the PowerShell gate matrix as
   G13).

- **G17 ·** registry identity holds after construction, `add_tool` and `remove_tool`.

- **G19 · concurrency, multiple writers and immutability:** while a background sub-agent decides
    in a tight loop, the parent interleaves `set_mode` ×1000, `add_run_rules` ×100 (**a distinct
    deny each time**) and `active_permissions` ×1000 from three threads; afterwards all 100 denies
    are present, every verdict's snapshot is internally consistent, and every projected event names
    its own verdict's snapshot. Plus a thread-free group: mutate the list passed to the
    constructor, the list returned by `rules`, the returned `ActivePermissions`, and the snapshot a
    verdict carries — every subsequent verdict is unchanged, and a second engine created afterwards
    has unmutated presets.

- **G22 · recursion and the default allowlist:** the builtin generalist's definition
    (`agentao/agents/definitions/generalist.md:1-4`) has no `tools:` key, and its sub-agent has
    every non-agent tool and zero `agent_*` tools, so it cannot spawn itself; a definition that
    explicitly names one agent tool gets that one only.

## 7. Open questions, and what would change this plan

1. **Should a bare `Agentao(...)` construct a default engine?** (formerly §9 q7)
2. **Should `_for_subagent` become a public `Agentao.fork(...)`?** A host that spawns its own
   sub-agents has the same problem the wrapper had. (formerly §9 q8)

What would change this plan: **discovering that some MCP tool wrapper holds per-agent state** —
gate G00's concurrent-call check is where that would surface.
