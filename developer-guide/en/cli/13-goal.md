# 13. Long-Task Goals

`/goal` turns the CLI into a long-task loop. You state an objective once. Agentao then drives the agent toward it, turn after turn, without you typing "continue". The loop stops when the agent reports the objective **complete** or **blocked**, when a **time or turn budget** trips, or when the agent stops making progress.

## The model

```
/goal <objective> ──→ [active] ── loop: one agent turn, then check ──┐
                         ▲                                           │
                         │  /goal resume          agent: update_goal(complete) ──→ [complete]
                         │                        agent: update_goal(blocked)  ──→ [blocked]
                    [paused] ←── Ctrl-C           3 turns without progress     ──→ [blocked]
                                                  budget trips → 1 wrap-up turn ──→ [limit_reached]
```

The CLI (the host) owns this loop. The harness has no "goal" concept. The CLI builds the loop from three plain primitives: it drives a turn with `agent.chat()`, it sends a per-turn message, and it injects a tool. A host that wants the same behaviour builds it the same way. See [Part 4.8 · Orchestration Continuation](/en/part-4/8-orchestration-continuation).

## Setting a goal

```text
> /goal Migrate every call site off the deprecated auth API and make the suite green
Goal set: Migrate every call site off the deprecated auth API and make the suite green
budget: 0/25 turns, 0s/2h
```

The loop starts at once. Each *turn* is one full agent turn: the agent reads files, edits them, runs tests. The first turn sends your objective as the message. Each later turn sends a continuation prompt. It restates the objective and tells the agent how to report the result.

While the goal runs, the CLI adds one tool to the agent: `update_goal`. The agent calls it with `status="complete"` when it has achieved the full objective, or with `status="blocked"` when it cannot continue without you. The CLI removes the tool when the loop ends. `update_goal` counts as read-only, so a goal can finish in `read-only` mode too.

If a goal is already `active` or `paused`, a new `/goal <objective>` asks before it replaces it (press `1` to confirm). A goal that has stopped (`complete`, `blocked` or `limit_reached`) is replaced without a question.

Images that you staged with `/image` go with the first turn of the goal.

### When the loop stops

The loop checks the goal after each turn. It stops when:

- the agent calls `update_goal(status="complete")`. The objective is achieved.
- the agent calls `update_goal(status="blocked")`. The agent needs you, for example for missing credentials or a decision that is yours to make.
- a budget cap trips. The agent gets one final **wrap-up** turn. It summarizes the progress and the remaining work, then the loop stops.
- **three consecutive turns make no progress**. The CLI marks the goal `blocked`.
- you press Ctrl-C. The goal is `paused` (see [Interrupting with Ctrl-C](#interrupting-with-ctrl-c)).
- a turn raises an error. The goal is `paused` and the CLI shows the error.

### The no-progress stop

A turn counts as **no progress** in two cases:

- it produced no answer (`no_output` or `reasoning_only`) and called no tools.
- it failed at the provider (`llm_error`). Tool calls earlier in the same turn do not change this. A provider that keeps failing is not doing useful work.

Every other turn resets the count to zero. That includes a turn that gave an answer, a turn that called a tool, and a turn that the harness halted at a ceiling (`max_iterations`, `doom_loop`, `length_truncated`, `hook_stop`). A halted turn did work, so it is not idle.

The budgets limit how much a goal can *do*. This stop limits how long it can do *nothing*. It is the only stop that still applies with `--unbounded`. When it fires, the CLI tells you that the turns made no progress and names the reason. It does not say "it needs your input", because the agent asked nothing. Check the model or the provider, then `/goal resume`.

## Budgets

A goal has two independent caps. The first one to trip wins.

```text
/goal <objective> --for 30m              # cap on active wall-clock time
/goal <objective> --turns 10             # cap on continuation turns
/goal <objective> --for 1h --turns 20    # both caps
/goal <objective> --unbounded            # no caps, not even the defaults
```

The flags can go anywhere in the line. All other words are the objective.

- **`--for <duration>`** caps the **active** wall-clock time. Formats: `90s`, `30m`, `2h`, `1h30m`. Each segment needs a unit, so `30` alone is an error. The total must be greater than zero. Time while the goal is paused does not count.
- **`--turns <n>`** caps the number of turns. `n` must be a positive integer.

::: warning `--turns` is not `max_iterations`
`max_iterations` caps the inner tool-call loop *inside one turn*. `--turns` caps the *outer* number of `chat()` calls that the goal loop makes. The two are independent.
:::

If you omit a cap, the default from `.agentao/settings.json` applies (see [Settings](#settings)). `--unbounded` turns off both defaults.

The loop checks the budget **before** each turn. A turn that has started always finishes, so a long turn can run past the time cap. The wrap-up turn runs after the cap trips and does not count as a turn.

Why is the time default large (`120m`) and the turn default small (`25`)? The two caps guard different risks. `--turns` is the main guard against a stuck agent that loops. `--for` guards only against wall-clock problems, such as a tool that hangs. The time default is above the time that 25 turns normally take, so it does not hide the turn cap on goals with slow turns.

## Subcommands

```text
/goal                                    show the current goal (status, used / cap)
/goal show                               same as /goal
/goal budget [--for <d>] [--turns <n>]   set or replace caps on the current goal
/goal budget --clear                     remove both caps (same as --unbounded)
/goal pause                              pause the goal
/goal resume                             resume a paused or blocked goal
/goal edit <new objective>               change the objective
/goal clear                              delete the goal
```

- **`/goal budget`** changes only the caps that you give. On a `limit_reached` goal, it also starts the loop again, but only if the new caps leave room. Example: after a 25-turn cap trips, `/goal budget --turns 50` raises the cap and resumes. If the new caps are still used up, the CLI saves them and tells you which cap to raise.
- **`/goal resume`** restarts a `paused` or `blocked` goal. It also restarts a goal that is still `active` on disk because an earlier process was killed during a run. It does not restart a `limit_reached` goal. Use `/goal budget` or `/goal clear` for that. A `complete` goal is final.
- **`/goal edit`** keeps the status, the caps and the time and turns used so far.

`show`, `pause`, `resume` and `clear` take no argument. If text follows them, the whole line is a new objective. So `/goal clear out the temp files` sets a goal. It does not delete one.

## States

| Status | Meaning | What comes next |
|---|---|---|
| `active` | The loop drives the goal | The agent completes or blocks it, or a cap trips |
| `paused` | You paused it. Time does not count | `/goal resume` |
| `blocked` | The agent needs your input, or 3 turns made no progress | Fix the cause, then `/goal resume` |
| `complete` | The objective is achieved. This status is final | `/goal clear`, or set a new goal |
| `limit_reached` | A budget cap tripped | `/goal budget …` to continue, or `/goal clear` |

Who sets each status:

- **The agent** sets `complete` and `blocked`, through `update_goal`. It can do so only while the goal is `active`. On any other status, the tool call changes nothing and returns an error to the model.
- **You** pause, resume, edit, re-budget and clear.
- **The loop** sets `limit_reached`. It also sets `blocked` after three turns without progress. That is the one case the agent cannot report itself, because a model that answers nothing cannot call `update_goal` either.

## Settings

Set the defaults in `.agentao/settings.json`:

```jsonc
"goal": {
  "enabled": true,               // false: /goal cannot start or resume the loop
  "default_max_turns": 25,       // used when --turns is omitted. 0 = no turn cap
  "default_time_budget": "120m"  // used when --for is omitted. "" = no time cap
}
```

| Key | Default | Notes |
|---|---|---|
| `goal.enabled` | `true` | With `false`, setting, resuming or re-budgeting into a run is refused. `/goal show`, `pause`, `edit` and `clear` still work. |
| `goal.default_max_turns` | `25` | `0` or a negative number means no turn cap. A value that is not an integer falls back to `25`. |
| `goal.default_time_budget` | `"120m"` | Same formats as `--for`. An empty value means no time cap. A value that does not parse falls back to `120m`. |

Caps that you give with `--for`, `--turns` or `/goal budget` override these defaults. The full schema is in [configuration.md §3](https://github.com/jin-bo/agentao/blob/main/docs/reference/configuration.md).

A goal also cannot start or resume while plan mode is active, because the agent would only plan and never execute. Leave plan mode first with `/plan implement` or `/plan clear`.

## Persistence and restarts

The goal is stored in `.agentao/goal.json` in the working directory. There is one goal at a time. The CLI writes the file after every turn and after every subcommand that changes the goal.

The goal survives a restart. The CLI does not restart the loop by itself. Launch `agentao`, run `/goal` to see where the goal stopped, and run `/goal resume` to continue.

If the file is missing or cannot be read, the CLI treats it as "no goal". You can delete the file by hand. That has the same effect as `/goal clear`. If the CLI cannot write the file (a read-only directory or a full disk), it warns you once. The goal then runs, but it does not survive a restart.

## Interrupting with Ctrl-C

Press **Ctrl-C** during a goal run to **pause** the goal. The current turn stops, and the CLI saves the goal as `paused`. Time does not count while the goal is paused. Continue later with `/goal resume`.

## Goals, plan mode and Stop hooks

- `/goal` is **not** built on the plugin `Stop` hook and its `force_continue` signal. That path has a small re-entry cap and injects a message that you can see. `/goal` runs its own loop with its own stop conditions.
- Plan mode (`/plan implement`) hands a plan to the agent once. `/goal` keeps driving the agent across many turns. The two are separate, and a goal cannot run while plan mode is on.

## Pitfalls

- **`--turns 10` does not mean "10 tool calls"**. Each turn can make many tool calls, up to `max_iterations`.
- **A unit-less duration fails**. `--for 30` is an error. Write `--for 30m`.
- **`/goal clear <text>` sets a new goal**. To delete the goal, type `/goal clear` with nothing after it.
- **`resume` does not lift a budget stop**. A `limit_reached` goal needs `/goal budget` with more room, or `/goal clear`.
- **The time cap is checked between turns**. One long turn can run past `--for`.

## Where to go next

| Want to… | Read |
|----------|------|
| Build the same loop in your own host | [Part 4.8 · Orchestration Continuation](/en/part-4/8-orchestration-continuation) |
| Plan a task before a goal executes it | [4. Plan Mode](./4-plan-mode) |
| Run a goal without write or shell tools | [3. Permissions & Modes](./3-permissions-modes) |
| Run one non-interactive turn in CI | [12. Non-Interactive Entry Points](./12-non-interactive) |

---

::: info Where this fits
`/goal` is a CLI feature, not a harness feature. The loop is [`run_goal_continuation`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/input_loop.py) in `agentao/cli/input_loop.py`. The goal record and its state machine are in [`agentao/cli/goal_state.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/goal_state.py). The injected tool is [`agentao/tools/goal.py`](https://github.com/jin-bo/agentao/blob/main/agentao/tools/goal.py). The design record is [`docs/design/codex-goal-mechanism-review.md`](https://github.com/jin-bo/agentao/blob/main/docs/design/codex-goal-mechanism-review.md).
:::

::: tip Authoritative help
Command syntax: `/help`. Behaviour: [`agentao/cli/commands/goal.py:handle_goal_command`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands/goal.py). Duration parsing: [`agentao/cli/duration.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/duration.py).
:::
