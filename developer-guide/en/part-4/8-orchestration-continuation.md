# 4.8 Orchestration Continuation — Long-Task Goals in Your Host

> **What you'll learn**
> - **Why** "keep working until the task is done" is a **host** responsibility, not a harness feature
> - The **three primitives** the harness already gives you to build it — drive a turn, inject a per-turn message, inject a tool
> - A **host-owned continuation loop** with a time/turn budget, end to end (~40 lines)
> - **Why not** `force_continue` (the plugin `Stop` hook), and where the boundary is

Agentao's CLI ships a `/goal` command: state an objective once, and it keeps
driving the agent across many turns until the objective is reported complete or
blocked, or a budget trips. This chapter is the **pattern behind it**, so you
can build the same thing in your own host. The CLI implementation
(`agentao/cli/commands/goal.py` + `agentao/cli/input_loop.py`) is the worked
reference; the design record is
[`docs/design/codex-goal-mechanism-review.md`](https://github.com/jin-bo/agentao/blob/main/docs/design/codex-goal-mechanism-review.md) §11.

## 4.8.1 The harness has no "goal" — and shouldn't

A single `agent.chat(msg)` runs one **turn**: the model thinks, calls tools
(bounded by `max_iterations`), and returns when it has nothing left to do *for
that message*. There is deliberately no harness-level "keep going until the
larger objective is met" — that would bake a product decision (how long? what's
the budget? what does *done* mean?) into the runtime.

Instead the harness exposes **three generic primitives**, and "goal" is what you
get when a host composes them in a loop:

| Primitive | API | Role in continuation |
|---|---|---|
| **Drive a turn** | `agent.chat(message)` / `await agent.arun(...)` | One unit of work toward the objective |
| **Inject per-turn context** | the `message` you pass each turn | Steer the next turn ("keep going / wrap up") |
| **Inject a tool** | `agent.add_tool(tool)` / `agent.remove_tool(name)` | Give the agent a way to signal *done* / *blocked* |

Nothing else is needed. Time and turn budgets are pure host bookkeeping
(wall-clock between `chat()` calls; a counter you increment). Token budgets are
**not** part of this pattern — Agentao deliberately scopes goal budgets to
time/turn, so the harness needs no usage-observation primitive.

## 4.8.2 The continuation loop

The whole pattern is an outer `while`:

```python
import time
from agentao.tools.base import Tool

class UpdateGoalTool(Tool):
    """The agent's ONLY write into goal state: mark complete / blocked."""
    def __init__(self, goal):
        super().__init__()
        self._goal = goal
    @property
    def name(self): return "update_goal"
    @property
    def description(self):
        return ("Call with status='complete' when the objective is fully "
                "achieved, or status='blocked' when you cannot proceed without "
                "the user. Do not mark complete just because a budget is low.")
    @property
    def parameters(self):
        return {"type": "object",
                "properties": {"status": {"type": "string",
                                          "enum": ["complete", "blocked"]}},
                "required": ["status"]}
    def execute(self, status):
        # Active-only guard: a terminal goal is immutable by the agent.
        if goal["status"] != "active":
            return f"ignored: goal is {goal['status']}, not active"
        goal["status"] = status          # 'complete' or 'blocked'
        return f"Goal marked '{status}'."


def run_goal(agent, objective, *, max_turns=25, time_budget_s=7200):
    goal = {"status": "active", "turns": 0, "time": 0.0}

    agent.add_tool(UpdateGoalTool(goal), replace=True)   # inject the write surface
    try:
        while goal["status"] == "active":
            # Budget pre-check → exactly one wrap-up turn, then stop.
            if goal["turns"] >= max_turns or goal["time"] >= time_budget_s:
                goal["status"] = "limit_reached"
                agent.chat(f"You've reached this goal's budget. Do not start new "
                           f"work; summarize progress and remaining work.\n{objective}")
                break

            message = objective if goal["turns"] == 0 else (
                f"Continue working toward this goal. Call update_goal when done "
                f"or blocked.\n<goal>{objective}</goal>")

            t0 = time.monotonic()
            agent.chat(message)                          # drive one turn
            goal["turns"] += 1
            goal["time"] += time.monotonic() - t0

            # The agent may have called update_goal this turn.
            if goal["status"] in ("complete", "blocked"):
                break
    finally:
        agent.remove_tool("update_goal")                 # tool is loop-scoped
    return goal
```

Four invariants make this correct:

1. **First turn uses the objective; later turns use a continuation prompt.**
   Gate on `turns == 0`, not a separate flag.
2. **Budget is checked *before* each turn**, and a trip produces **exactly one**
   wrap-up turn (the agent gets to summarize) — not a hard cut.
3. **The injected tool is the agent's only write**, and it is **guarded to
   `active`** so a wrap-up turn can't overwrite the terminal `limit_reached`
   state.
4. **The tool is registered for the loop's lifetime only** (`finally:
   remove_tool`) so it isn't visible outside a goal.

## 4.8.3 Budgets: time and turns guard different risks

Offer two axes and let the **first to trip win**:

- **Turns** — one turn is a *full* `agent.chat()` (its own inner
  `max_iterations` loop), so a turn cap is the **primary runaway guard** (a
  stuck agent looping). `25` is already a lot of work.
- **Time** — accumulated active wall-clock. This only guards **wall-clock
  pathology** (a hung tool), so size it *above* where the turn cap normally
  finishes (the CLI default is `120m`). A time cap at or below the turn-cap
  completion point silently shadows the turn cap on slow-iteration tasks.

⚠️ **`turns` is not `max_iterations`.** `max_iterations` bounds the inner
tool-call loop *within one* `chat()`; the turn cap bounds the *number of*
`chat()` calls. They are orthogonal — keep both.

## 4.8.4 Why not the `Stop` hook / `force_continue`?

Agentao plugins have a `Stop` hook that can re-enter the loop
(`StopHookResult.force_continue`). It is the **wrong tool** for a goal:

- it is **hard-capped** (`_stop_reentry_cap`, default 3) as a runaway guard —
  fine for "nudge once more", useless for sustained pursuit;
- it injects a **visible user message** into history each time.

A goal is a **host-owned** loop: *you* own the stop condition, the budget, and
the steering. `force_continue` is for a plugin to say "not done yet" inside a
*single* host turn; a goal is the host driving *many* turns. Different layer,
different tool.

## 4.8.5 Persist if you want restart-survival

The CLI writes the goal to `.agentao/goal.json` after every turn so a goal
survives a process restart. Persistence is entirely host-side — the `goal` dict
above becomes a small JSON file; on launch you reload it and, if it's still
`active` or `paused`, offer to resume. The harness is not involved.

## 4.8.6 Checklist for your host

- [ ] An outer `while active` loop calling `agent.chat()` (or `arun`).
- [ ] A per-turn message: objective first, continuation prompt after.
- [ ] One injected `update_goal`-style tool, **guarded to active**, added before
      the loop and removed in `finally`.
- [ ] Budget checked before each turn; **one** wrap-up turn on a trip.
- [ ] (Optional) persist state for restart-survival.
- [ ] **Not** built on `force_continue`.

→ Worked reference: `agentao/cli/input_loop.py::run_goal_continuation`. User
guide: [Long-Task Goals](https://github.com/jin-bo/agentao/blob/main/docs/guides/goal.md).
