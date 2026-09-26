# Background sub-agents: wake an idle CLI

**Status:** A, the run-host change and C **implemented** 2026-09-26 (unreleased, 0.5.6 cycle), with the host-api recipe; B remains a separate, unauthorized follow-up.
**Anchors:** agentao `main@22104ff`; codex `30fc6864cc1` (2026-09-23); goose `9adae14b6`
(2026-09-25); gemini-cli `87de0b6369` (2026-09-24); pi-mono `d5629e204` (2026-09-24).
**Related:** `codex-subagent-v2-vs-agentao.zh.md` §4.2 (the polling observation this doc
answers) and §3 (the concurrency-cap P1, deliberately **not** folded in here).

## 1. Summary

The stuck session is now visible in `../dstation/agentao.log` (§8). The parent waited for
three background sub-agents with `run_shell_command("sleep 300; …")`. At 08:46:34 its third
identical shell call tripped the doom-loop check; this was **not** a
`check_background_agent` loop. The last child finished at 09:04:02, but the parent did not
resume until the user sent `continue` at 11:32:03. A foreground sub-agent would return
inside its tool call and is not involved here.

1. **Nothing wakes an idle parent.** A finished task only queues a notification. The queue
   is drained when the parent sends its next LLM request. If the parent's turn has already
   ended, the CLI sits in `prompt()` until the user types something.
2. **The current status tool cannot wait inside a turn.** It returns at once, and the launch
   wording encourages repeated checks. In the observed session the model used a shell sleep
   instead; repeating it ended the turn at the doom-loop check.

Plan: ship A and C for the observed CLI failure, and disable background launch in the
one-shot `agentao run` host. Then implement B as a separate cross-host work item, after
its cancellation and client-timeout checks (§6.2).

- **A (harness).** Tell the model not to wait with shell sleeps or repeated checks; when
  there is no other work, end the turn.
- **B (harness, separate work item).** Add a cancellable, bounded `wait_seconds` to
  `check_background_agent` for the CLI, ACP, and embedded hosts. ACP is the main reason
  to prioritize it because ACP cannot use the CLI's idle wake.
- **C (host: the CLI).** Wake the interactive CLI when it is idle and a notification is
  pending. Document the same recipe for embedded hosts.
- **Run host.** Pass `bg_store=None` to its factory so the one-shot process cannot launch
  daemon background work that outlives its turn.

The first delivery needs no new tool, runtime concept, or public host API. B extends an
existing tool, with no new wait-for-any/all surface.

## 2. The two mechanisms

### 2.1 No wake-up (primary)

- On a terminal `update()`, the store queues
  `Background agent '…' (ID: …) completed.` (`agentao/agents/bg_store.py:432-445`).
- The **only** consumer is `_inject_background_notifications`
  (`agentao/runtime/chat_loop/_runner.py:1217`). It drains the queue at `:1230` while a
  request is being built, and appends one `<system-reminder>` user message.
- So a notification reaches the model only if **another LLM request happens**. In the
  observed session the doom-loop guard stopped the parent's turn; the CLI then blocked in
  `cli._prompt_session.prompt(...)` (`agentao/cli/input_loop.py:63`).
- The status bar ticks to ✓ (`input_loop.py:108-128`), but nothing starts a turn.

### 2.2 No in-turn wait (secondary)

- `check_background_agent` returns immediately, whatever the task's state
  (`agentao/agents/tools/_bg_tools.py:59-120`).
- The `run_in_background` description explicitly says to poll
  (`agentao/agents/tools/_wrapper.py:658`); the launch message (`:1545`) also directs the
  model to call `check_background_agent` for the result.
- Repeating any identical tool call, including the observed shell sleep, trips the
  doom-loop check on the **third** call (`agentao/runtime/tool_planning.py:36`, `:487-501`).
  The counter spans the whole `chat()`, so the calls need not be consecutive. Varying
  arguments can instead use up `max_iterations`, after which the CLI asks the user whether
  to continue (`agentao/cli/transport.py:234`).

## 3. What the peers do

| | How a parent waits for a child | What happens when the parent is idle and a child finishes |
|---|---|---|
| codex | `wait_agent(timeout_ms)`: default 30 s, minimum 10 s, hard maximum (`codex-rs/core/src/tools/handlers/multi_agents_common.rs:19-21`, `core/src/config/mod.rs:255`) | **Nothing is triggered.** The completion is sent with `trigger_turn: false` (`codex-rs/core/src/agent/control.rs:494`) or injected with `inject_fragment_without_turn` (`:512`) |
| goose | `load(source: task_id)` **blocks** until the task finishes (`crates/goose/src/agents/platform_extensions/summon.rs:711`) | Nothing is triggered. A status block is added on each turn (`get_moim`) |
| gemini-cli | Sub-agents are synchronous only (`packages/core/src/agents/agent-tool.ts:235`) | Doesn't apply |
| pi-mono | The sub-agent example runs `parallel` / `chain` inside **one blocking tool call** (`packages/coding-agent/examples/extensions/subagent/index.ts:164`, `:219`) | Doesn't apply |

Two readings:

- **Step B has precedent twice**: codex's `wait_agent` and goose's blocking `load`, both in
  the harness. The shell sleep shows a desire to wait in-turn; A plus C gives the CLI a
  way to proceed, while B gives ACP and embedded hosts that do not auto-wake an in-turn path.
- **Step C has no precedent in any of the four harnesses.** codex deliberately doesn't start
  a turn. That is consistent with §5: waking is a host decision, not a harness one.

## 4. Answering `codex-subagent-v2-vs-agentao.zh.md` §4.2

§4.2 recorded "no park primitive → polling" and asked how a blocking wait handles
cancellation, foreground blocking, executor threads, and host resumption. B answers the
first three with a bounded, token-aware tool and the parallel-batch interrupt change in
§6.2. A waiting `arun()` turn already holds one worker for its whole duration; B does not
take an additional shared `arun` worker or use the event loop's default executor. A
parallel batch does use its own short-lived pool. Host resumption remains separate: C lets
the CLI start a new turn when it is idle and has a pending notice (§6.3).

## 5. Layering: which side of the host/harness line each step is on

| Step | Changes | Layer | First delivery |
|---|---|---|---|
| A: wording | `agents/tools/_wrapper.py` | Harness | Yes, for hosts that expose background launch |
| B: `wait_seconds` | `agents/bg_store.py`, `agents/tools/_bg_tools.py`, `runtime/tool_executor.py` | Harness | Separate follow-up for CLI, ACP, and embedded hosts |
| C: idle wake | `cli/input_loop.py`, private store peek; host-api recipe | Host | CLI wake; embedded hosts get a recipe |
| Run host | `cli/run.py` factory argument | Host | Disable background launch |

**In-turn waiting belongs in the harness tool.** B is available to every host that exposes
background launch. ACP motivates the follow-up because it cannot use the CLI's idle wake.
It is not needed to keep `agentao run` safe: that host can
simply remove background launch from its tool schema.

| Host | After the first delivery |
|---|---|
| Interactive CLI | A plus C first; B later allows one bounded in-turn wait when the result is needed before proceeding |
| Embedded host | A plus an optional host-api continuation recipe first; B later allows in-turn waiting |
| ACP | A first; B then lets the parent wait within one prompt turn when the client permits a long turn |
| `agentao run` | Background launch hidden by `bg_store=None`; foreground sub-agents still return within the turn |

**Waking is host work.** Starting a turn nobody asked for is part of the session lifecycle,
which the host owns:

- ACP only lets the client start a prompt turn, so the harness couldn't do it there anyway;
- codex's harness deliberately doesn't do it (`trigger_turn: false`), and neither does goose;
- an embedded host may not want the model running unprompted, for example because of
  billing, a UI that isn't ready, or turn-level quotas.

So the harness never starts a turn on its own. It only makes the fact available.

**The harness already exposes what a host needs to decide.** No new API is required:

- `Agentao.events()` (`agentao/agent.py:861`) emits `SubagentLifecycleEvent`.
- A **background** task's event carries `parent_task_id` (its `agent_id`,
  `_wrapper.py:1426`). The foreground path spawns without one (`:720`).
- The final `phase` is `completed` / `failed` / `cancelled`.
- **Ordering of production is guaranteed on terminal paths that queue a notice.** The
  notice is queued before the final event is sent:
  - the normal path: `bg_store.update()` at `_wrapper.py:1481` comes before
    `_terminal_subagent_event` at `:1502-1509`;
  - the exception paths follow the same order;
  - cancelling a task that hasn't started queues its notice inside `cancel()`
    (`agentao/agents/bg_store.py:518-522`), before the worker sends `cancelled`.
- This does **not** guarantee that a notice is still pending when the host receives the
  event. An active parent turn may have drained it in between; a conversation reset may
  have cleared or suppressed it. A host must serialize its own turns, match the original
  session, and treat the event as a cue to decide whether continuation is needed.

**The CLI doesn't use that event,** for a practical reason. `events()` is an async iterator
with backpressure: a slow reader blocks whatever is producing the events (docstring at
`agent.py:862-874`). A CLI subscriber would need its own loop that reads every event. C
instead reads the store the CLI already reads every second for its status bar. That is an
**internal CLI shortcut, not part of the host contract**, and it is kept out of
`host-api.md` (§6.3).

## 6. Plan

### 6.1 Step A (harness): wording

- `_wrapper.py:658` says "poll"; the launch message at `:1545` directs the model to call
  `check_background_agent`. Change both to roughly: *"Do not wait with sleep or repeated
  status checks. Continue other work; if there is nothing else to do, end this turn. A
  background agent update can be read when this session next runs. Use
  `check_background_agent(agent_id=…)` only to inspect status when needed."* Do not promise
  that every host will automatically start the next turn.
- The `check_background_agent` description already says "Check", not "poll"; keep it
  factual. When B ships, distinguish one bounded wait from repeated immediate checks.

### 6.2 Step B (separate cross-host work item): bounded in-turn wait

- In the CLI, B and C coexist: use B when the parent needs a child result before it can
  proceed within this turn; use C when the parent can end its turn and resume on completion.
  The same B tool is exposed to ACP and embedded hosts with background launch enabled.
- Add optional integer `wait_seconds` to `check_background_agent`, default `0`, for one
  `agent_id`. The candidate maximum is **30 minutes**. The store can wait on a
  `threading.Condition` over its task lock, checking the turn's cancellation token at
  intervals no longer than 0.5 s. At each interval, release the condition lock and call
  `get()` to refresh records written by another store sharing the persistence file; this
  store's condition receives no signal from that store. Return immediately for an unknown
  or out-of-project id. Preserve the
  current result format when the default is used; on timeout, report that the child is
  still running and tell the model to end the turn or cancel the child instead of issuing
  the same wait again.
- When the wait returns a terminal result, accept the short completion preview being
  injected again with the next LLM request. This already happens after an immediate
  `check_background_agent` result; avoiding it would require a larger notification-queue
  change.
- **Cancel the wait, not the child.** `session/cancel` calls the ACP turn token from a
  separate dispatcher thread (`acp/session_cancel.py:137`); a waiting tool can then return
  within one check interval. The child keeps running unless
  `cancel_background_agent(agent_id)` is called.
- **CLI parallel-batch Ctrl+C:** place an interrupt handler *inside* the
  `ThreadPoolExecutor` context in `runtime/tool_executor.py:231-248`, around both
  submission and `as_completed`. On `KeyboardInterrupt`, cancel the turn token before
  re-raising, so the waiting worker does not hold pool exit for the full `wait_seconds`.
  Another non-cooperative tool in the same batch can still delay pool exit. The existing
  `turn.py:189-190` handler closes the turn. Test this path; Windows interruption of a
  blocked `as_completed` remains unverified.
- **No extra `arun` worker:** an `arun()` turn already occupies one worker for its whole
  duration (`agent.py:1253-1304`). The wait uses that thread for a single tool or the
  batch's own pool for parallel tools; it does not use the event loop's default executor.
- **Doom-loop guard stays.** Different ids are distinct calls. A sufficiently long wait
  can cover this case's 7–17 minutes of *remaining* child work in one call. A child can
  still exceed 30 minutes, especially if waited on immediately after launch; after a
  timeout the model should end its turn or cancel it, not repeat the same wait until the
  third call trips the guard. This is a bounded wait, not a guarantee that every child
  finishes within one turn.
- **ACP gate:** exercise a long prompt with the intended client to learn its own turn
  timeout and verify how it displays progress. The executor already binds tool
  `output_callback`, and ACP forwards `TOOL_OUTPUT` as throttled `tool_call_update` events.
  Each update resends all accumulated content, so emit progress sparingly, for example
  one line per minute; avoid adding a second progress API. The maximum remains a
  candidate until the client checks pass.

### 6.3 Step C (host): wake an idle host

**The CLI, in code** (`cli/input_loop.py` plus one private store snapshot):

- In `get_user_input`'s existing `_ticker` loop (`input_loop.py:55-58`, 1 s period), wake
  when **all** of these hold:
  - the input buffer is empty;
  - no images are staged;
  - plan mode is off;
  - no `/goal` loop is running (it already drives turns);
  - a **new** notification is pending.
- Add a private store snapshot method that returns `(queue_nonempty, push_sequence)` under
  `_notify_lock`, without draining. Increment the monotonic sequence on every actual append
  in both `push_notification()` and `_push_task_notification()`; a suppressed notification
  does not increment it. Do not reset the sequence on drain or conversation reset.
  A terminal record in `list()` is **not** an equivalent signal: `update()` sets the status
  before it queues a notice, and a reset can suppress that notice altogether.
- Keep `last_auto_wake_sequence` on the CLI across prompt calls. Wake only when the queue
  is nonempty and `push_sequence > last_auto_wake_sequence`; record the observed sequence
  when the prompt is actually exited for `_BG_WAKE`. If the resulting turn returns before
  `_runner.py:423` drains notifications (for example, `UserPromptSubmit` rejects it at
  `:309-310`), the unchanged queue does not cause another automatic turn. A later user turn
  can still drain it; a newly appended notice can trigger one more wake.
- When the ticker sees a pending notice, schedule a callback with
  `app.loop.call_soon_threadsafe(...)`. On the prompt event-loop thread, that callback
  **rechecks** the input buffer, staged images, plan state, and new-notice sequence immediately
  before `app.exit(result=_BG_WAKE)`. If the user has started typing, leave the prompt
  alone. `Application.exit` itself is not thread-safe.
- In `run_loop`, on `_BG_WAKE`:
  - handle the sentinel before the current blank-input skip;
  - print a dim `⟳ background agent finished — continuing`;
  - run one normal turn with a fixed message, e.g.
    `[Background agent finished — review the update and continue]`;
  - the existing drain at `_runner.py:1230` puts the result into that turn;
  - no other change to turn handling.
- **Opt-out:** a CLI setting, `background_agents.auto_wake` in `settings.json`, default
  `true`. This proposal chooses on by default for the interactive CLI. No slash command.
- Wake on a pending **batch** of notices, without waiting for all launched tasks. One turn
  drains all notices queued by its next LLM request. If another task finishes after that
  turn has ended, it can trigger another wake. Completions during a running turn may be
  consumed by that turn or batched into its next wake; there is no one-turn-per-task
  guarantee.

**One-shot `agentao run` host** (`cli/run.py`): pass `bg_store=None` in the
`build_from_environment` call. The factory otherwise creates a store by default
(`embedding/factory.py:264-268`); `None` removes `run_in_background` from the agent tool
schema (`agents/tools/_wrapper.py:645-653`). `run.py` does not wait after the turn, and
background workers are daemon threads (`_wrapper.py:1539`), so a launched child can be
cut off at process exit. This is a code-derived risk, not reproduced in a one-shot run.

**Embedded hosts, documentation only** (`docs/reference/host-api.md` and `.zh.md`):

- Add a short recipe: on a terminal `SubagentLifecycleEvent` with `parent_task_id` set,
  schedule a continuation only if the original session is still active, no turn is running,
  and that completion was not already handled by the current turn. The event is a cue, not
  proof that a notice remains queued (§5). Run `chat()` on the host's normal turn driver,
  rather than directly in the event callback.
- Whether to wake at all remains the host's policy. A reset may silence the notice while
  the terminal event still arrives.
- **No harness code change** comes with this.

## 7. Out of scope

- **Wait-for-any / wait-for-all:** codex V2's mailbox wait. Revisit only if one-id waits
  prove insufficient.
- **The background concurrency cap:** `codex-subagent-v2-vs-agentao.zh.md` §3 P1, still
  unauthorized. It is a separate decision.
- **Any harness-driven turn:** no auto-continue in the runtime, on any transport (§5).
- **ACP auto-wake:** a prompt turn starts from the client. It may use the embedded-host
  recipe; this proposal does not add a server-initiated prompt.
- **Exempting waits from the doom-loop check:** no evidence for changing this safeguard.

## 8. Session evidence and decision

- **Q1 is answered by the stuck session.** The parent hit a shell-sleep doom loop at
  08:46:34; the three child runs reached final responses at 08:53:53, 08:56:01, and
  09:04:02. The parent resumed only on the user's `continue` at 11:32:03, when it read
  all three queued updates. A and C address that CLI gap; B adds in-turn waiting to the
  CLI as well as ACP and embedded hosts.

  The source is a local, rotating file outside this repository,
  `../dstation/agentao.log` (2026-09-26). Decisive lines, with unrelated lines omitted:

  ```text
  08:46:34 Doom-loop detected: run_shell_command called 3+ times with identical args
  08:53:53 Reached final response in iteration 56
  08:56:01 Reached final response in iteration 35
  09:04:02 Reached final response in iteration 55
  11:32:03   Message 107 [tool]:
        [Doom-loop detected] Tool 'run_shell_command' was called 3 times with identical arguments. Execution stopped to prevent an infinite loop. Please try a different approach or tool.
  11:32:03   Message 108 [assistant]:
        part-02 已开始落盘（125/127 行，仍在写入中）。继续等待最后三片。
  11:32:03   Message 109 [user]:
        continue
  11:32:03   Message 110 [user]:
        Background agent update:
        Background agent 'generalist' (ID: 053a1038) completed.
        Background agent 'generalist' (ID: 638e9198) completed.
        Background agent 'generalist' (ID: 83cb4e27) completed.
  ```

  Parent and children share one logger, so `req_N` counters interleave. The 11:32 parent
  request's messages 107–108 identify the 08:46 failure as the parent's. Its turn had
  ended before the three child final responses. The local file's former line numbers
  (1105, 9160, 10623, 13984, 13993–14043) are only supplemental anchors.
- **Default:** `auto_wake=true` for the interactive CLI: its status bar already announces
  completion, and this finishes the waiting workflow without another user prompt. Embedded
  hosts choose their own policy; the recipe is opt-in.

## 9. Tests

- **Store (A/C):** the snapshot reflects the queue after `update()`, drain, and
  `start_new_conversation()`; the sequence increments on actual appends from both push
  paths, never on suppressed pushes or drains. A terminal record without a notice does
  not wake the CLI.
- **B:** verify completion, pending cancellation, timeout, turn cancellation (including
  ACP `session/cancel`), unchanged output at `wait_seconds=0`, and that a CLI Ctrl+C in a
  parallel batch cancels the token before pool exit. Check that cancelling a wait leaves
  the child running; verify that a second store's completion is seen within a check
  interval, unknown/out-of-project ids return immediately, and a terminal result can be
  followed by its short queued preview. Test the intended ACP client's long-turn timeout
  and progress display before fixing the 30-minute maximum.
- **Ordering (new regression tests):** add tests for notice-before-terminal-event on each
  deliverable path; also cover a parent turn draining the notice before an embedded host
  handles the event, so the recipe does not assume the queue is still populated.
- **CLI (host):**
  - the wake fires with a pending notification and an empty buffer;
  - one pending completion wakes without waiting for other running tasks; notices accumulated
    during a turn are handled by that turn or the next wake;
  - a `UserPromptSubmit` hook that blocks the wake turn before notification drain does not
    cause another wake until a new notice is appended;
  - typing between the ticker check and the event-loop callback wins; the prompt stays open;
  - it doesn't fire while typing, in plan mode, during `/goal`, or with `auto_wake: false`;
  - `_BG_WAKE` bypasses the blank-input skip and runs exactly one turn.
- **Run host:** assert its factory receives `bg_store=None` and its agent tool schema omits
  `run_in_background`.
- **Wording:** check the two model-facing launch texts; no test is needed solely to pin a
  particular verb.

A/C and the run-host restriction are user-visible changes: add concise `CHANGELOG.md`
`[Unreleased]` entries, with the `agentao run` restriction under **Changed** (daemon
background work could be cut off at process exit; no migration is needed). Add the
host-api recipe in both twins. Ship A plus the run-host change first, then C, then the
host-api recipe; B is a separate cross-host follow-up with its own entry and tests.
