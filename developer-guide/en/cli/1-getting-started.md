# 1. Getting Started

The minimum loop is four commands: launch, chat, end-and-start-fresh, exit. Once you've done these, every other slash command is optional.

## Launch

```bash
uv run agentao
# or
./run.sh
```

You drop into the REPL. The current working directory becomes the agent's `working_directory` — every file/glob/shell tool the agent runs is rooted here. **Always `cd` to the project you want the agent to work in before launching.**

```text
🌟 Agentao — terminal agent
Working dir: /Users/you/projects/my-app
Model: gpt-5.4 · Mode: workspace-write

Type your message or /help for commands.
>
```

## Plain message vs. slash command

| You type | What happens |
|----------|--------------|
| `Find the 3 largest files` | Sent to the agent. Triggers the LLM loop (think → tool → observe → answer). |
| `/help` | **Not** sent to the agent. The CLI handles it locally. |

That's the whole rule. Anything starting with `/` is a CLI command (session-level); anything else is a turn (agent-level).

## `/help` — see every command

```text
> /help
```

Prints the full slash-command reference plus the list of tools the agent has. Read it once on first run; you don't need to memorise it — `/help` is always one keystroke away.

## `/status` — what state am I in

```text
> /status
```

Shows, in this order:

1. **Conversation summary** — model name, message count, token estimate, active skills
2. **Permission Mode** — one of `read-only` / `workspace-write` / `full-access` / `plan`, with a one-line description (full doc: [3. Permissions & Modes](./3-permissions-modes))
3. **Loaded sources** — where the active permission rules came from (defaults, project, user)
4. **Markdown Rendering** — `ON` / `OFF` (toggle via `/markdown` — see [9. Replay & Output](./9-replay-output))
5. **Task List** — `X/Y completed` if the agent has used the `todo_write` tool this session
6. **ACP servers** — `X/Y running`, plus inbox / pending-interaction counts (see [8. MCP / ACP / Plugins](./8-mcp-acp-plugins))

`/status` doesn't change anything — it's read-only diagnostics. Run it any time you've lost track of which model / mode / skills are live.

## `/clear` and `/new` — start a fresh session

Both commands save the current session first, then start an empty conversation. They differ in what they reset:

| Command | Clears | Preserves |
|---------|--------|-----------|
| `/new` | Current conversation history, context counters | Long-term memory, model, discovered skills |
| `/clear` | Current conversation history, all memories (user + project), session summaries | Model, discovered skills |

Both reset permission mode to `workspace-write`. `/clear all` is a backward-compatible alias for `/clear`.

When to use:
- The current task is done; you don't want its context bleeding into the next one
- Token usage is climbing (see `/context` in [7. Context & Status](./7-context-status))
- You changed your mind and want a clean slate; use `/new` if you want to preserve long-term memory

To resume or delete a saved session, use `/sessions` — see [11. Sessions, Agents & Tasks](./11-sessions-agents).

## `/exit` and `/quit` — leave cleanly

```text
> /exit
```

Both do the same thing: flush any in-flight work, close MCP / ACP subprocesses, persist memory, and return to your shell. **Don't `Ctrl+C` to leave** — that bypasses cleanup and can leave child processes around. Save `Ctrl+C` for *cancelling the current turn* (the agent stops, the REPL stays open).

## Pitfalls on day one

| Symptom | Cause |
|---------|-------|
| Slash command typed mid-message gets sent to the agent | Slash commands only count when they're the **first** non-whitespace character |
| Agent can't see a file you know exists | You launched from the wrong directory — `working_directory` is locked at launch |
| `/clear` happened by accident, want it back | Sessions are persisted; use `/sessions list` and `/sessions resume <id>` |
| `/help` shows commands that don't work | You're on an older build — run `uv sync` and relaunch |

## Where to go next

| If you want to… | Read |
|-----------------|------|
| Switch model or provider | [2. Models & Providers](./2-models-providers) |
| Understand the confirmation prompts | [3. Permissions & Modes](./3-permissions-modes) |
| Plan before you act | [4. Plan Mode](./4-plan-mode) |

---

::: info Where this fits
The commands on this page (`/help`, `/status`, `/clear`, `/new`, `/exit`) are pure CLI session control. An embedding host manages lifecycle through `Agentao(...)` / `agent.close()` and reads status from `active_permissions()` / event streams instead. See [Part 2 · Lifecycle](/en/part-2/3-lifecycle) and [Part 4 · Host Contract](/en/part-4/7-host-contract).
:::

::: tip Authoritative help
This page describes behavior; the canonical command syntax lives in `/help` and [`agentao/cli/help_text.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/help_text.py). When in doubt, trust `/help`.
:::
