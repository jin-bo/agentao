# 4. Plan Mode

Plan mode is a "think before you act" loop. The agent runs **read-only**, drafts a plan into a file, and only switches back to executing tools when you explicitly approve. Use it whenever a task is large, risky, or unclear.

## The model

```
[normal] ── /plan ──→ [plan mode]
                        │  read-only · drafts to .agentao/plan.md
                        │
                        ├── /plan implement ──→ [normal] (mode restored, plan handed to agent)
                        └── /plan clear     ──→ [normal] (mode restored, plan archived)
```

Plan mode is its own permission level — separate from `read-only`, `workspace-write`, `full-access`. Entering it stashes your prior mode and restores it on exit.

## `/plan` — toggle plan mode

```text
> /plan
[Plan mode ON]  (read-only; LLM will plan, not execute)
Ask what to plan. When done: /plan implement · /plan clear
```

While plan mode is on:

- The agent is forced into read-only — no `write_file`, no `replace`, no `run_shell_command`, no web access
- Confirmation UI doesn't appear (because no risky tools are reachable)
- The agent writes its plan to `.agentao/plan.md` as it thinks
- `/mode` is **locked** (you'll get "Cannot change permission mode while in plan mode")

You ask the agent in plain language what to plan. The conversation is normal — multiple turns, follow-ups, refinements. The plan file gets rewritten as the plan evolves.

```text
> /plan
[Plan mode ON]
> We need to add OAuth login. Walk through the codebase and propose a plan.
[agent reads files, thinks, writes .agentao/plan.md]
> Use the existing session middleware instead of adding a new one.
[agent revises .agentao/plan.md]
```

## `/plan` (when already on) — show status + draft

```text
> /plan          # plan mode is already on
[plan mode is ON]
Saved plan: .agentao/plan.md

# OAuth integration
1. ...
2. ...
```

Same as `/plan show`, plus the "you're still in plan mode" reminder.

## `/plan show` — show the saved plan

```text
> /plan show
```

Prints `.agentao/plan.md` (rendered as Markdown if `/markdown` is on). Works whether plan mode is active or not — useful for revisiting a plan after `/plan implement`.

## `/plan implement` — exit plan mode and run

```text
> /plan implement
Plan mode OFF. Permission mode: workspace-write

Current plan (.agentao/plan.md):
# OAuth integration
1. ...
2. ...

Ask the agent to implement the plan above.
```

What happens:

1. Plan mode flag clears
2. Your prior permission mode is restored (whatever you had before `/plan`)
3. The plan content is reprinted so the next message has visual context
4. The plan file is **kept** — the agent has it, you can `/plan show` later

Then you say "Go" or "Implement step 1" or whatever. The agent executes against tools normally, with the plan as a sticky reference.

## `/plan clear` — discard and archive

```text
> /plan clear
Plan archived and cleared. Plan mode OFF.
```

What happens:

1. Current `.agentao/plan.md` is moved to a timestamped archive (recoverable via `/plan history`)
2. If plan mode was active, it turns off and the prior permission mode is restored
3. The agent loses the plan — next turn starts clean

Use when:

- You decide the plan is wrong and want to start over
- You finished a task and want a clean state for the next plan
- You want plan mode off without implementing this plan

## `/plan history` — browse archived plans

```text
> /plan history

Plan history (most recent first)

  20260505-2240-oauth
    Add OAuth login. Use existing session middleware. Update routes...

  20260505-1830-refactor-tools
    Split ToolRegistry into registration and dispatch...
```

Each archive shows the file stem (timestamped) and a snippet from its `## Context` section. Open the file directly under `.agentao/plan-history/` to read the full plan.

## When to reach for plan mode

| Situation | Why plan mode helps |
|-----------|---------------------|
| The task spans many files | Plan first prevents tool-loop dead ends |
| You're nervous about side effects | Read-only by construction; nothing dangerous can fire |
| Working with a smaller / cheaper model | Plan mode keeps the model on rails (no tool oscillation) |
| Reviewing someone else's design before doing it | The plan file is a shareable artifact |
| You're going to implement across multiple sessions | Plan persists in `.agentao/plan.md` even after `/clear` |

## Pitfalls

- **`/plan implement` doesn't auto-execute** — it just exits plan mode and prints the plan. You still type "go ahead" (or similar) for the agent to start.
- **Editing `.agentao/plan.md` by hand** — fine, the agent re-reads it. But if you edit while the agent is mid-think, your edits may be overwritten. Edit between turns.
- **`/clear` while in plan mode** — the new session starts in *normal* mode (plan flag is per-session). The plan file remains on disk; `/plan show` still works.
- **Confusing `/plan clear` with `/clear`** — different verbs. `/clear` rotates the conversation; `/plan clear` archives the plan file.

## Where to go next

| Want to… | Read |
|----------|------|
| Activate a skill mid-plan | [5. Skills & Crystallize](./5-skills-crystallize) |
| Save a planned approach as a reusable skill | [5. Skills & Crystallize](./5-skills-crystallize) → `/crystallize` |
| Understand the model-side prompting that drives plan mode | [Part 5.6 · System Prompt Customization](/en/part-5/6-system-prompt) |

---

::: info Where this fits
Plan mode is implemented in [`agentao/plan/controller.py`](https://github.com/jin-bo/agentao/blob/main/agentao/plan/controller.py) and exposed identically through the embedded API: `agent.enter_plan_mode()` / `agent.exit_plan_mode()` / `agent.show_plan()`. An IDE host can drive the same workflow with the same artifacts.
:::

::: tip Authoritative help
Command syntax: `/help`. Behavior: [`agentao/cli/commands.py:handle_plan_command`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands.py). Plan file path defaults to `.agentao/plan.md` ([`agentao/plan/session.py`](https://github.com/jin-bo/agentao/blob/main/agentao/plan/session.py)).
:::
