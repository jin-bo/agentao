# Codex `/goal` — Teardown + Agentao Candidate Design

**Status:** Two parts. **§§1–9 — mechanism teardown** of an *external* project
(Codex), drafted 2026-06-23 from a direct, line-verified read of the goal slash
command, the `ext/goal` extension crate, the protocol/state model, and the steering
templates (purely descriptive; no recommendation). **§§10–11 — Agentao candidate
design**: an exploratory relevance note (§10) plus a candidate design split with a
drafted `/goal` surface (§11), both **explicitly marked NOT an approved plan / not
implemented**. The teardown stands on its own; within the candidate design, **§11's
concrete claims are grep-verified against `agentao/`** while **§10 stays exploratory
and unverified** — and the whole candidate still needs maintainer sign-off before it
becomes a proposal.
(If §11 ever graduates to an approved plan, split it into its own implementation-plan
doc — keeping it here while it is still a candidate avoids fragmenting the EN/ZH pair.)
**Audience:** Agentao maintainers studying long-running-task / autonomous-continuation
designs; anyone reverse-reviewing Codex.
**Companion:** `codex-goal-mechanism-review.zh.md`.
**Related:**
- `codex-reverse-review.md` — prior reverse review of Codex against Agentao; same
  "what does Agentao actually need, not mirror Codex's product shape" posture.
- `metacognitive-boundary.md` — Agentao's own notion of an injectable per-turn
  protocol; the closest conceptual neighbour to Codex's "steering" injection.

**Source:** Codex local checkout at `../codex`, `main`@`97dce078c5` (2026-06-23).
All file references below are relative to that tree and were line-verified, except
where explicitly marked inferred.

---

## TL;DR

Codex's `/goal` is a **long-task auto-continuation mechanism**, implemented as a
**pluggable extension** (`codex-rs/ext/goal/`) rather than baked into the agent
core. A persisted `ThreadGoal` (per-thread, SQLite-backed, six-state machine) is
driven by three pillars:

1. **Agent-facing tools** — `get_goal` / `create_goal` / `update_goal`, where the
   agent may *only* mark a goal `complete` or `blocked` (under strict, heavily
   prompt-engineered audits).
2. **Steering injection** — hidden, templated context fragments
   (`continuation` / `budget_limit` / `objective_updated`) injected as
   `ResponseItem`s each round; the objective is treated as untrusted, XML-escaped
   data.
3. **The auto-drive loop** — `on_thread_idle → continue_if_idle →
   thread.try_start_turn_if_idle([continuation_item])`. When the thread goes idle
   and the goal is still `Active`, the extension **automatically launches a fresh
   turn** with the continuation prompt. That is the engine that makes the agent
   "not stop."

A semaphore-guarded **accounting** layer charges token and wall-clock deltas to the
active goal, auto-flips it to `BudgetLimited` on exhaustion, and injects a wrap-up
steering item *mid-turn*. Write authority over the state machine is split cleanly:
**user** (create / pause / resume / clear / edit), **agent** (complete / blocked
only), **system** (budget / usage limits).

---

## 1. Three architectural layers

| Layer | Location | Responsibility |
|---|---|---|
| Protocol / state | `codex-rs/state/src/model/thread_goal.rs` | `ThreadGoal` struct + `ThreadGoalStatus` enum, persisted in the thread's SQLite store |
| **Extension engine (core)** | `codex-rs/ext/goal/` (whole crate) | `GoalExtension` hooks thread/turn lifecycle, drives auto-continuation, accounting, tools, steering |
| TUI / UI | `codex-rs/tui/src/...` | `/goal` slash command, menu, display formatting, oversized-objective spill-to-file |

The load-bearing design choice: **goal logic is not hard-coded into the agent loop.**
It is a pluggable extension (`ext/goal`) that attaches via lifecycle hooks
(`on_thread_idle`, `on_turn_start`, `on_token_usage`, `on_tool_finish`, …). The core
agent loop is unaware of "goals."

---

## 2. Data model

`codex-rs/state/src/model/thread_goal.rs:60`

```rust
pub struct ThreadGoal {
    pub thread_id: ThreadId,
    pub goal_id: String,            // UUID
    pub objective: String,          // user-provided objective text
    pub status: ThreadGoalStatus,
    pub token_budget: Option<i64>,  // optional token cap
    pub tokens_used: i64,           // running token total charged to this goal
    pub time_used_seconds: i64,     // running wall-clock seconds
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}
```

`ThreadGoalStatus` (`thread_goal.rs:14`) is a six-variant enum —
`Active | Paused | Blocked | UsageLimited | BudgetLimited | Complete` — with helpers
`is_active()` and `is_terminal()` (the latter true for `BudgetLimited | Complete`,
`thread_goal.rs:39`). Goals exist **only for persisted threads** (ephemeral threads
reject goals, `thread_goal_actions.rs:358`).

---

## 3. Pillar A — agent-facing tools

`codex-rs/ext/goal/src/spec.rs` defines three Responses-API tools:

- `get_goal()` (`spec.rs:13`) — read status / budgets / used token & time / remaining
  budget.
- `create_goal(objective, token_budget?)` (`spec.rs:25`) — "Create a goal only when
  explicitly requested … do not infer goals from ordinary tasks. … Fails if an
  unfinished goal exists."
- `update_goal(status: "complete" | "blocked")` (`spec.rs:60`) — **the agent can only
  mark complete or blocked.**

The critical **control split**: the agent **cannot** pause / resume /
budget-limit / usage-limit its own goal — those are user- or system-only. The tool
descriptions carry heavy prompt-engineering to stop the agent gaming the state:
`blocked` is only legal when "the same blocking condition has recurred for at least
three consecutive goal turns" (`spec.rs:66`), and the agent must not mark `complete`
merely because the budget is nearly exhausted (`spec.rs:81`).

---

## 4. Pillar B — steering / continuation injection

`codex-rs/ext/goal/src/steering.rs` renders one of three embedded templates into a
**hidden context fragment** injected as a `ResponseItem`:

```rust
fn goal_context_input_item(prompt: String) -> ResponseItem {
    ContextualUserFragment::into(InternalModelContextFragment::new(
        InternalContextSource::from_static("goal"), prompt))
}
```

Templates (`codex-rs/ext/goal/templates/goals/`):

- `continuation.md` — a large **"completion audit + blocked audit + fidelity"**
  prompt whose core purpose is to **fight the agent shrinking the objective into the
  easiest-to-pass subtask** ("Do not substitute a narrower, safer, smaller … solution
  because it is more likely to pass current tests"). It also instructs the agent to
  keep `update_plan` current and to treat completion as *unproven* until verified
  requirement-by-requirement.
- `budget_limit.md` — "budget reached; wrap up, summarize progress, leave a clear
  next step."
- `objective_updated.md` — "the user edited the objective; switch to the new one."

**Prompt-injection hardening:** the objective is XML-escaped (`steering.rs:124`),
wrapped in `<objective>` / `<untrusted_objective>` tags, and every template states
explicitly that the objective is *user data, not higher-priority instructions.*

---

## 5. Pillar C — the auto-drive loop (the real "orchestration")

This is the engine. `codex-rs/ext/goal/src/extension.rs:154`:

```rust
fn on_thread_idle(...) {
    runtime.continue_if_idle().await   // fires whenever the thread goes idle
}
```

`codex-rs/ext/goal/src/runtime.rs:359` `continue_if_idle()`:

```rust
// only if tools are visible and the goal is still Active:
let item = continuation_steering_item(&protocol_goal_from_state(goal));
thread.try_start_turn_if_idle(vec![item]).await;   // auto-start a fresh turn
```

So: the agent finishes a turn → thread becomes idle → the extension checks the
goal → if still `Active`, it **automatically starts a new turn injecting the
continuation prompt.** The agent therefore "can't stop" — it keeps pushing until it
marks `complete` / `blocked`, the budget/usage limit trips, or the user pauses /
clears. A `goal_state_permit` semaphore (`runtime.rs:366`) is held across the
read-then-start window so an external set/clear cannot race the continuation launch.

`GoalExtension` implements the full lifecycle hook surface (`extension.rs`):
`on_thread_start / resume / idle / stop`, `on_turn_start / stop / abort / error`,
`on_token_usage`, `on_tool_finish`.

---

## 6. Accounting & automatic budget enforcement

`codex-rs/ext/goal/src/accounting.rs`:

- Dual-track accounting (per-turn token usage + wall-clock), serialized by a
  `progress_accounting_lock` semaphore (`accounting.rs:94`) so concurrent
  tool-completion hooks can't double-charge the same delta.
- Token delta formula: `input − cached + output` (`goal_token_delta_for_usage`,
  `accounting.rs:328`).
- **Plan-mode turns do not charge tokens** (`account_tokens = !matches!(mode, Plan)`,
  `accounting.rs:80`).
- When `tokens_used ≥ token_budget`: the status auto-transitions to `BudgetLimited`,
  and a budget-limit steering item is injected **mid-turn**
  (`extension.rs:400 → runtime.inject_active_turn_steering(item)`) so the agent wraps
  up immediately rather than waiting for the next idle cycle.

---

## 7. Persistence detail — oversized objectives spill to a file

`codex-rs/tui/src/goal_files.rs`. If the objective (after expanding pasted text /
images) exceeds `MAX_THREAD_GOAL_OBJECTIVE_CHARS` (`goal_files.rs:121`), it is written
to `$CODEX_HOME/attachments/<uuid>/goal-objective.md`, and the stored objective field
becomes a reference string:

```
Read the Codex goal objective file at <path> before continuing.
```

`objective_file_path()` (`goal_files.rs:157`) validates the UUID and exact path shape
on the way back in, so an arbitrary path can't be smuggled in to coerce a file read.

---

## 8. UI & lifecycle

`/goal` registration (`codex-rs/tui/src/slash_command.rs`): `SlashCommand::Goal`
(line 42), description "set or view the goal for a long-running task" (line 122),
`supports_inline_args` (line 159), `available_during_task` = true (line 226) — i.e.
it can be invoked **while a turn is running.**

Subcommand dispatch (`codex-rs/tui/src/chatwidget/slash_dispatch.rs`):

| Input | Action |
|---|---|
| `/goal <objective>` | `SetThreadGoalDraft { mode: ConfirmIfExists }` (line 841) |
| `/goal clear` | `ClearThreadGoal` (line 757 → 787) |
| `/goal edit` | `OpenThreadGoalEditor` (line 759) |
| `/goal pause` | `SetThreadGoalStatus(Paused)` (line 767) |
| `/goal resume` | `SetThreadGoalStatus(Active)` (line 768) |
| `/goal` (bare) | show goal menu |

**Confirm-before-replace** (`thread_goal_actions.rs:364`
`should_confirm_before_replacing_goal`): only `Complete` replaces *without* a
confirmation prompt; **`BudgetLimited` still prompts** (despite `is_terminal()` being
true for both), as do `Active / Paused / Blocked / UsageLimited`. (This is a precise
correction of the looser "both terminal states auto-replace" reading.)

Menu / display (`chatwidget/goal_menu.rs`, `goal_display.rs`) show status, objective,
elapsed time (`2h 30m`), tokens used (`63.9K`), and budget; on session resume a
paused/blocked goal triggers a "resume goal?" prompt.

---

## 9. State machine & write authority

```
              user pause              user resume
   ┌────────────────────────────────────────────┐
   ▼                                              │
 Active ──agent update_goal(complete)──▶ Complete (terminal)
   │  │
   │  └─agent update_goal(blocked, ≥3 turns)──▶ Blocked (dormant; resume restarts audit)
   │
   └─system (budget exhausted)──▶ BudgetLimited     system (usage-limit-exceeded)──▶ UsageLimited
```

| Transition | Who |
|---|---|
| create / pause / resume / clear / edit | **user** |
| complete / blocked | **agent** (only, under strict audit) |
| budget_limited / usage_limited | **system** (automatic) |

> **`UsageLimited` is narrow:** Codex maps only the `UsageLimitExceeded` turn error to
> `UsageLimited` (`ext/goal/src/extension.rs:306`); every *other* non-retryable turn
> error maps to `TurnError → Blocked` (same file, `:311` — the comment notes this stops
> auto-continuation from looping and consuming tokens). A generic rate limit does
> **not** by itself drive `UsageLimited`.

---

## 10. Exploratory relevance to Agentao (unverified — the maintainer's call)

This section stays exploratory — on its own it is **not** a proposal; the candidate
design that builds on it is §11 (explicitly marked not-approved). Recorded as prompts
for a grep-first evaluation:

- The closest existing Agentao concept is the **injectable per-turn metacognitive
  boundary** (`metacognitive-boundary.md`) — Codex's "steering" is a concrete,
  shipped instance of "inject a host-controlled protocol fragment each turn." Whether
  Agentao's boundary should grow a *goal-shaped* default is an open design question,
  not a settled gap.
- Codex's auto-continuation lives in the **host/app-server + TUI**, not the model
  loop. Under Agentao's embedded-harness boundary, an equivalent "keep driving turns
  toward an objective" loop would most naturally be a **host responsibility**, with
  the harness exposing the per-turn injection and tool-injection primitives (turn/time
  budgets stay host-side; token accounting would be a future harness primitive — see
  §11) — mirroring the recurring "valuable kernel already exists as a host-contract
  primitive" finding.
  This needs the usual `grep`-in-`agentao` verification before any claim that a real
  gap exists.

Before any of the above turns into a proposal: prove the gap in `agentao/` with
concrete `file:line` evidence (or "no match"), and leave the pain/prioritization
judgment to the maintainer.

---

## 11. Design option under evaluation — three-layer split (NOT an approved plan)

**Status:** Candidate shape only, recorded for evaluation. Grep-verified against
`main` (2026-06-23). Whether to build any of this is the maintainer's call. This is
*not* a commitment.

**Decision (2026-06-23, maintainer):** Token budgets are **out of scope for now**;
goal budgets use **time / turn-count** instead. Consequence: the one harness gap
below (token-usage exposure) is **not** pursued, so **Layer 1 needs no change** — the
split reduces to CLI (reference host) + developer-guide over *existing* primitives.
Revisit only if token budgets are later requested.

A faithful goal mechanism does **not** belong in the harness core as a "goal"
feature. It belongs to the **host**, which already owns the turn cadence. The
candidate split is **three layers**, not two:

### Layer 1 — Harness (core): stays goal-agnostic

The harness owes only **generic, goal-free primitives**; "goal" is never a core
concept. Verified against today's host contract, most already exist:

| Primitive a host needs | Exists today? | Anchor |
|---|---|---|
| Drive multiple turns | ✅ `Agentao.chat()` / `arun()`, callable in a host loop | `agentao/agent.py` |
| Inject per-turn continuation context | ✅ host prepends/joins it into the next message | `agentao/cli/input_loop.py:545` (CLI already does this) |
| Inject goal tools (`get_goal`/…) | ✅ host `extra_tools` construction kwarg | `agentao/agent.py:84` |
| Persist goal state | ✅ host-side (out of scope for the harness) | — |

So the host already has every primitive a **time / turn-count** goal loop needs. The
one thing it lacks matters only for *token* budgets:

**The one harness gap — and why the decision sidesteps it:** token usage is not
surfaced on `agent.events()` / `agentao/host/models.py`, so codex-style **token
budgets** would need a new (goal-agnostic) per-turn usage signal. **Per the decision
above, token budgets are out of scope.** Time / turn-count budgets are **pure
host-side** (wall-clock measured between `chat()` calls; the host counts its own loop
iterations) and need **zero** harness change — so Layer 1 stays not just
goal-agnostic but *untouched*. (If token budgets are ever wanted later, that usage
signal is the single primitive to add, reusable by all hosts.)

### Layer 2 — CLI (reference host): implements `/goal` + the continuation loop

Agentao has three host / front-end surfaces (in-process embedding, the CLI incl.
`agentao run`, and ACP; `docs/design/embedding-vs-acp.md`); the **CLI** is the natural
**reference** host for this design. It implements Agentao's own goal orchestration as a
**host-owned outer loop**:

```text
while goal.active:
    resp = agent.chat(continuation_prompt)      # host-driven, NOT plugin force_continue
    update goal state (elapsed time, turn count)
    if complete | blocked | over time/turn budget: break   # host controls termination
```

This generalizes the existing one-shot precedent at `cli/input_loop.py:545` (the
post-plan-mode auto-continuation). **Explicitly NOT built on** the plugin
`Stop`/`force_continue` path — that is hard-capped at `_stop_reentry_cap` (default 3,
`agentao/runtime/chat_loop/_runner.py:587`) as a runaway guard and injects a *visible*
user message, both wrong for sustained goal pursuit. A host-owned loop is uncapped by
design (the host owns the stop condition).

### Layer 3 — developer-guide: document the host-orchestration pattern

Generalize the CLI implementation into a reusable **host-contract pattern** (outer
loop + state + per-turn injection + tool injection + host-side time/turn accounting),
with the CLI as the worked example.

**Home = part-4 (host contract), not part-5 (extension surfaces).** part-5 is
tools / skills / mcp / permissions / memory / system-prompt / plugin-hooks; host
contract lives in part-4 (`developer-guide/en/part-4/7-host-contract.md`,
`2-agent-events.md`). Proposed new page: `developer-guide/{en,zh}/part-4/8-orchestration-continuation.md`,
EN+ZH per repo convention, cross-linked from the part-5 tool-injection material
(same posture as the §5.8 Host Tool Injection page — keep in sync with `docs/design`
and `a-api-reference`).

### Boundary caution

Do **not** bake "goal" into core. The harness owes three generic primitives (drive
turn / inject context / inject tools); time/turn budgets are host-side and need none.
(A per-turn usage-observation primitive would be a *future, token-budget-only*
addition — out of scope per the decision above, not owed today.) "goal" is a
**host-level product concept** assembled from these — consistent with the recurring
"valuable kernel = host-contract primitive, not a harness feature" finding.

### Open items before this becomes a plan

1. ~~Decide whether token budgets are in scope.~~ **Resolved (2026-06-23): no —
   time / turn-count budgets only; no harness change.**
2. ~~Design the usage signal on the event stream / `chat()` return.~~ **Dropped as a
   consequence of (1); revisit only if token budgets are later requested.**
3. Confirm `cli/input_loop.py:545` is the right precedent to generalize (vs. a fresh
   loop), and decide where CLI goal state should persist.
4. Define the time / turn-count budget surface — **drafted in §11.1 below.**

### 11.1 Interface surface draft — `/goal` time/turn budget (item 4)

**Status:** Draft for evaluation; not approved/implemented. Scope = the CLI (reference
host) command surface + state model + continuation-loop integration. No token budget
(per the decision above). `/goal` is a CLI slash command, **not** an LLM tool; other
hosts implement their own surface per the developer-guide.

**A. Command surface** (matches `agentao/cli/help_text.py` `/cmd [subcommand]` style):

```
/goal [subcommand|<objective>]            manage the long-task goal (time/turn budget)
  /goal                                   show current goal (status/objective/used time·turns/caps)
  /goal <objective>                       set goal (confirm if a non-terminal goal exists)
  /goal <objective> --for 30m             with a time cap
  /goal <objective> --turns 10            with a turn cap
  /goal <objective> --for 1h --turns 20   both (first to trip wins); --unbounded opts out
  /goal budget [--for <d>] [--turns <n>]  set/replace caps on the live goal; --clear removes caps
  /goal pause | /goal resume              pause / resume (paused time is not counted)
  /goal edit                              re-edit the objective (keeps status + caps)
  /goal clear                             remove the goal
```

Flag style (`--for <duration>` / `--turns <n>`) follows `run.py`'s `--max-iterations`
argparse idiom.

**B. Budget semantics — two axes, with the critical distinctions:**

| Axis | Meaning | Must-not-confuse |
|---|---|---|
| `--for <duration>` | cap on **accumulated active wall-clock**. Format `90s` / `30m` / `2h` / `1h30m` | counts **active** time only; `pause` time is **excluded** (mirrors codex `time_used_seconds`) |
| `--turns <n>` | cap on **continuation turns** = host-loop `chat()` calls charged to the goal; the creating call is turn 1 | ⚠️ **NOT** `max_iterations` — that bounds the inner tool-call loop within one `chat()`. The two are **orthogonal**: the goal budget bounds the *outer* continuation loop, `max_iterations` still bounds each *inner* turn |

Either / both / neither may be set; with both, **first to trip wins**.

**C. Defaults (a decision point):** dropping token budgets removes the cost ceiling,
and codex's "run unbounded until complete" model was explicitly rejected
(`force_continue` is hard-capped at 3 as a runaway guard). So the draft **recommends
safety caps ON** — values/whether-on are the maintainer's call:

```jsonc
// .agentao/settings.json
"goal": {
  "default_max_turns": 25,         // applied when --turns is omitted
  "default_time_budget": "120m",   // applied when --for is omitted
  "enabled": true
}
```

The two axes guard **different** risks and are deliberately sized so they do not
shadow each other (recall `--for` / `--turns` are *first-to-trip-wins*, line 425):

- **`--turns` is the primary runaway guard** — one turn is a full `agent.chat()`
  (inner loop up to `max_iterations`), so `25` turns is already a large amount of
  work; its job is to catch a *stuck* agent, not to bound useful progress.
- **`--for` guards only wall-clock pathology** (a hung tool, an infinite wait), so
  it is set **above** where the turn cap would normally finish (`120m`). A time
  default at or below the turn-cap completion point would silently pre-empt the turn
  guard on slow-iteration goals (slow `pytest` / `uv sync` / fetch) and defeat the
  purpose of having both.

`/goal <obj> --unbounded` explicitly opts out of the defaults.

**D. State model** (persist to `.agentao/goal.json`, or hang off the session — open):

```python
@dataclass
class GoalState:
    goal_id: str                       # uuid
    objective: str
    status: GoalStatus                 # see E
    time_budget_seconds: int | None    # None = no time cap
    max_turns: int | None              # None = no turn cap
    time_used_seconds: int             # accumulated active (excludes paused)
    turns_used: int
    created_at: datetime
    updated_at: datetime
```

vs codex: **drops** `tokens_used` / `token_budget` (decision); collapses codex's
`UsageLimited` + `BudgetLimited` into a single `limit_reached`.

**E. State machine & write authority:**

```
            user pause            user resume
   ┌──────────────────────────────────────────┐
   ▼                                            │
 active ──agent marks complete──▶ complete (terminal)
   │  │
   │  └─agent marks blocked──▶ blocked (dormant; resume restarts)
   │
   └─host (time OR turn cap trips)──▶ limit_reached (terminal until user clear/edit/re-budget)
```

| Transition | Who |
|---|---|
| create / pause / resume / clear / edit / budget | **user** |
| complete / blocked | **agent** (via host-injected `update_goal` tool — see **E.1**) |
| limit_reached | **host loop** (automatic) |

**E.1 Agent write surface (the one injected tool).** The loop's
`agent_marked_complete_or_blocked()` (section F) is not magic — it reads the effect of a
single host-injected tool. This is the minimal contract that makes the loop *complete*
rather than command/budget-only:

- **`update_goal(status: "complete" | "blocked")`** — the agent's **only** write into
  goal state. Injected via the host `extra_tools` kwarg (Layer 1, `agent.py:84`).
  **Guarded:** the handler succeeds **only while `goal.status == active`**; after
  `limit_reached` / `paused` / `complete` / `blocked` / cleared it is a **no-op and
  returns an error result** to the model. This protects the terminal `limit_reached`
  (and `paused`) state from being overwritten by an `update_goal` call issued during
  the wrap-up turn — at which point status is already `limit_reached` (see §F). On a
  successful call the handler sets `goal.status` (host-side) and returns; the host loop
  reads that status after each turn (the `agent_marked_complete_or_blocked()` check).
  Mirrors codex `ext/goal/src/spec.rs:60` minus the token-report text; the active-only
  guard is intentionally **stricter** than codex's `budget_limit.md` (which still
  permits a late `complete` after the budget trips) — a deliberate simplification to
  keep terminal states immutable except by user action.
- **`get_goal()`** *(optional)* — read-only introspection (status, caps, used
  time/turns, remaining). Mirrors codex `spec.rs:13`. Nice-to-have; the loop does not
  require it.
- **No `create_goal` tool** — unlike codex, goal creation is **user-driven** here
  (`/goal <objective>`), so the agent never self-creates goals. Add it later only if
  agent-initiated goals are wanted.

**Write-authority split** (this is the §E table, made precise): the **agent** may set
only `complete` / `blocked`, and *only* through `update_goal` — it cannot
pause/resume/clear/re-budget or set `limit_reached`; the **user** owns
create/pause/resume/clear/edit/budget; the **host loop** owns `limit_reached`. Same
posture as codex (`spec.rs` restricts the agent to complete/blocked). The strict
"`blocked` only after ≥3 consecutive turns" audit from codex `continuation.md` is a
**prompt-level** concern carried inside `CONTINUATION_PROMPT`, not enforced by the tool.

**F. "Limit reached → wrap up" behavior** — the host checks the budget *before each
continuation*:

```text
while goal.status == active:
    if goal.time_used >= time_budget or goal.turns_used >= max_turns:
        goal.status = limit_reached                    # no further normal continuation
        agent.chat(WRAP_UP_PROMPT(goal))               # ← exactly one final wrap-up turn
        break
    msg = original_user_msg if goal.turns_used == 0 else CONTINUATION_PROMPT(goal)
    t0 = now()
    resp = agent.chat(msg)                             # inner loop still bounded by max_iterations
    goal.turns_used += 1
    goal.time_used  += now() - t0
    persist(goal)
    if agent_marked_complete_or_blocked():             # i.e. agent called update_goal this turn (see E.1)
        goal.status = complete | blocked; break
```

`WRAP_UP_PROMPT` (codex `budget_limit.md` adapted, token text removed): "You've reached
this goal's time/turn budget. Do not start new substantive work; summarize progress,
list remaining work or blockers, and give the user a clear next step." After the wrap-up
turn the host **stops driving**; status stays `limit_reached` until the user
`clear` / `edit` / `/goal budget` re-budgets.

**G. New helper needed:** a duration parser (`90s|30m|2h|1h30m → seconds`) — none exists
in-tree (grep: no `parse_duration`); rejects unit-less numbers; lives in `agentao/cli/`.

**H. Boundary guards (avoid collisions):**

| Existing | This surface | Relationship |
|---|---|---|
| `max_iterations` (`run.py:69`, `transport.py:121`) | `--turns` | **orthogonal**: inner tool loop vs outer continuation count |
| `force_continue` (`_runner.py:587`, capped at 3) | host outer `while` | **not reused**: goal runs the host loop, not the plugin Stop path |

**Open for the maintainer:** (1) defaults — **values decided (§C): `25` turns /
`120m`, caps on**; still open is whether the caps apply silently or surface a one-time
notice on first goal; (2) state persistence location (`.agentao/goal.json` vs session);
(3) flag names (`--for` / `--turns`).

### 11.2 Commit checklist (conditional — only if §11 is signed off)

> **Not a green light.** This is the **landing-order skeleton** that would migrate into
> the dedicated implementation-plan doc the moment §11 graduates (top-matter, lines
> 13–14); it lives here only so the candidate carries its own "what would shipping
> actually touch" estimate. Every row is grep-anchored to where the code/doc lands.
> Order is dependency order; each commit is independently reviewable and must land
> **green** — no red/UNSTABLE CI, and any semantic-merge is re-tested on the merged tree.

| # | Commit | Lands in (anchor) | Tests | Dep |
|---|---|---|---|---|
| 1 | **Duration parser** (§G) | new `agentao/cli/duration.py` :: `parse_duration("90s\|30m\|2h\|1h30m") → int`; rejects unit-less / non-positive | new `tests/cli/test_duration.py` — units, compound, reject `"30"` / `"-5m"` / `""` | — |
| 2 | **GoalState + persistence** (§D, §E) | new `agentao/cli/goal_state.py` :: `GoalState` dataclass + `GoalStatus` enum; `load()`/`save()` → `.agentao/goal.json` (or session — open); transition methods enforce the §E **state machine** (legal transitions only; *who* may trigger each — the authority split — is enforced by the callers, rows 3/4/5) | round-trip serialize; invalid-transition reject; paused-time-excluded accounting | — |
| 3 | **`update_goal` injected tool** (§E.1) | new `agentao/tools/goal.py` :: guarded `update_goal(status)` (active-only → no-op + error after terminal); **not** registered in `agent.py::_register_tools()` — injected via `extra_tools` (`agent.py:84`); optional read-only `get_goal` | guard returns error when `status != active`; success path sets state; terminal-state immutability | 2 |
| 4 | **`/goal` command** (§A, §C) | new `agentao/cli/commands/goal.py` (argparse subcommands + `--for` / `--turns` / `--unbounded` / `--clear`; confirm-on-replace of a non-terminal goal); edit `agentao/cli/help_text.py` (`/goal` entry); read `goal.{default_max_turns,default_time_budget,enabled}` from settings | new `tests/cli/test_goal_command.py` — flag parse, default application, confirm-on-replace, `--unbounded` opt-out | 1, 2 |
| 5 | **Continuation loop** (§F) — *keystone* | edit `agentao/cli/input_loop.py` — generalize the `:545` one-shot precedent into the host-owned `while goal.status == active`: budget pre-check, inject `update_goal` via `extra_tools`, `CONTINUATION_PROMPT` / `WRAP_UP_PROMPT`, **exactly one** wrap-up turn on trip, `persist()` per turn; explicitly **NOT** `force_continue` (§H) | loop exits on complete/blocked/turn-cap/time-cap; wrap-up fires exactly once; turn-1 uses the original message, later turns use continuation; `--turns` independent of `max_iterations` | 2, 3, 4 |
| 6 | **Config reference** (§C) | edit `docs/reference/configuration.md` — `goal` settings block + `.agentao/goal.json` path / schema / precedence | doc-only | 4 |
| 7 | **Docs** (three audiences) | **(a) end user** — new `docs/guides/goal.md`: drive the CLI `/goal` (subcommands, budgets, examples), parallel to `session-replay.md` (`/replay`) / `memory-management.md` (`/memory`); **(b) host integrator, full** — new `developer-guide/{en,zh}/part-4/8-orchestration-continuation.md`: the orchestration-continuation pattern (outer loop + inject-context + inject-tools + host-side time/turn accounting), CLI as worked example, cross-linked from §5.8 — **source of truth**; **(c) embedding coding agent, distilled** — extend `docs/guides/embed-for-agents.md` with a short "a long-running goal/continuation loop is a *host* job" skeleton that **points to (b)** (no duplication), reinforcing the generic, true-today lesson: the harness ships the three primitives (drive-turn / inject-context / inject-tools — all existing, Layer 1 unchanged), **not** a goal feature; **(d)** edit `CLAUDE.md` slash-command list + a `--turns` ≠ `max_iterations` gotcha | doc-only; developer-guide EN+ZH paired, `docs/guides/*` single-file per repo convention | 5 |

**Staged-rollout guard.** Keep `goal.enabled` **`false`** until commit 5 lands, so
commits 3–4 don't ship a `/goal` command wired to a continuation loop that doesn't
exist yet. Flip it to the §C default (`true`) in commit 5 or a trailing commit.

**Definition of done.** `uv run python -m pytest tests/` green including the new files;
`/help` renders `/goal`; the EN+ZH doc pair is in sync; a regression test asserts the
goal loop does **not** route through `force_continue` and that `--turns` is independent
of `max_iterations` (the two §H boundary guards); the `agentao/cli/` import surface is
unchanged on non-goal paths.
