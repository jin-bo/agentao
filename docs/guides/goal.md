# Long-Task Goals (`/goal`)

`/goal` gives the CLI a **long-task continuation loop**: you state an objective
once, and Agentao keeps driving the agent toward it across multiple turns —
without you having to type "continue" — until the agent reports the objective
**complete** or **blocked**, or a **time / turn budget** trips.

It is a CLI (host) feature built on the harness's existing primitives (drive a
turn, inject a per-turn message, inject a tool). The harness has no "goal"
concept of its own; other hosts assemble the same pattern themselves — see
[developer-guide part-4 / orchestration-continuation](../../developer-guide/en/part-4/8-orchestration-continuation.md).
Design record: [`docs/design/codex-goal-mechanism-review.md`](../design/codex-goal-mechanism-review.md).

## Setting a goal

```
/goal Migrate every call site off the deprecated auth API and make the suite green
```

Agentao sets the goal and immediately starts driving it. Each *turn* is a full
agent turn (the agent reads, edits, runs tests, …); between turns the loop
re-prompts the agent to keep going. The loop stops when:

- the agent calls `update_goal(status="complete")` — objective achieved;
- the agent calls `update_goal(status="blocked")` — it needs you (missing
  credentials, an ambiguous decision);
- a budget cap trips — one final **wrap-up** turn summarizes progress and
  remaining work, then the loop stops.

## Budgets — two axes

```
/goal <objective> --for 30m          # cap on accumulated active wall-clock
/goal <objective> --turns 10         # cap on continuation turns
/goal <objective> --for 1h --turns 20  # both — first to trip wins
/goal <objective> --unbounded        # no caps (opt out of defaults)
```

- **`--for <duration>`** — accumulated **active** wall-clock. Formats: `90s`,
  `30m`, `2h`, `1h30m`. A unit is required (`30` alone is rejected). Time spent
  while a goal is **paused** is not counted.
- **`--turns <n>`** — number of continuation turns. ⚠️ This is **not**
  `max_iterations`: `max_iterations` bounds the inner tool-call loop *within one
  turn*; `--turns` bounds the *outer* number of turns. They are independent.

When you omit a cap, the defaults from `.agentao/settings.json` apply (see
below). `--unbounded` opts out of both.

> **Why the time default is large (`120m`) and turns small (`25`).** The two
> axes guard different risks: `--turns` is the primary runaway guard (a stuck
> agent looping), while `--for` only guards wall-clock pathology (a hung tool).
> The time default is sized *above* where the turn cap normally finishes so it
> does not silently shadow the turn cap on slow-iteration goals.

## Subcommands

```
/goal                                 show the current goal (status, used / cap)
/goal show                            same as above
/goal budget [--for <d>] [--turns <n>]  set/replace caps on the live goal
/goal budget --clear                  remove caps (make it unbounded)
/goal pause                           pause (active wall-clock stops accruing)
/goal resume                          resume a paused — or blocked — goal
/goal edit <new objective>            re-edit the objective (keeps status + caps)
/goal clear                           remove the goal
```

- **Re-budgeting a limit-reached goal** reactivates it: `/goal budget --turns 50`
  after hitting a 25-turn cap bumps the cap and resumes driving.
- **`resume`** revives a `paused` goal or restarts a `blocked` one. A
  `limit_reached` goal is revived by re-budgeting (above) or `/goal clear`, not
  by `resume`. A `complete` goal is terminal.

## States

| Status | Meaning | How it ends / continues |
|---|---|---|
| `active` | being driven by the loop | agent completes/blocks, or a cap trips |
| `paused` | you paused it; time stops accruing | `/goal resume` |
| `blocked` | agent needs your input | address it, then `/goal resume` |
| `complete` | objective achieved (terminal) | `/goal clear` to start fresh |
| `limit_reached` | a budget cap tripped | `/goal budget …` to continue, or `/goal clear` |

Only the agent sets `complete` / `blocked` (via the injected `update_goal`
tool, and only while the goal is active); only you set
pause/resume/clear/edit/budget; only the host loop sets `limit_reached`.

## Settings

`.agentao/settings.json`:

```jsonc
"goal": {
  "enabled": true,            // master switch for the /goal command
  "default_max_turns": 25,    // applied when --turns is omitted (0 = no turn cap)
  "default_time_budget": "120m" // applied when --for is omitted
}
```

See [configuration.md](../reference/configuration.md) §3 for the full schema.

## Persistence

The goal is stored in `.agentao/goal.json` (one goal at a time) and survives
process restarts — re-launch and `/goal` shows where it left off. A corrupt or
missing file is treated as "no goal". This file is per-project working
directory; it is safe to delete by hand (equivalent to `/goal clear`).

## Interrupting

Press **Ctrl-C** during a goal run to **pause** it (the current turn stops and
the goal is saved as `paused`). Resume later with `/goal resume`.

## Relationship to other surfaces

- **Not** built on the plugin `Stop` / `force_continue` hook (which is
  hard-capped at 3 re-entries and injects a visible message) — `/goal` runs a
  host-owned loop with its own stop condition.
- Plan mode (`/plan`) is a *single* post-approval continuation; `/goal` is a
  *sustained* one. They are independent.
