# 11. Sessions, Agents & Tasks

This page covers the REPL commands that behave like a workbench: resume saved sessions, inspect background sub-agents, view task lists, and inspect registered tools.

## `/sessions` — saved sessions

`/exit`, `/clear`, and `/new` save the current session under `.agentao/sessions/`. The restore entry point is `/sessions`, not replay.

```text
> /sessions
> /sessions resume a1b2c3
> /sessions delete a1b2c3
> /sessions delete all
```

| Command | Effect |
|---------|--------|
| `/sessions` or `/sessions list` | List saved sessions, newest first |
| `/sessions resume <id>` | Resume by session-id prefix; model and active skills are restored too |
| `/sessions delete <id>` | Delete one saved session |
| `/sessions delete all` | Delete all saved sessions, with single-key confirmation |

You can also resume at launch:

```bash
agentao --resume          # latest session
agentao --resume a1b2c3   # matching prefix
```

## `/agent` and `/agents` — sub-agents

Sub-agents are predefined capabilities that can run in the foreground or background. Built-ins live under `agentao/agents/definitions/`, and plugins can register more.

```text
> /agent
> /agent codebase-investigator trace the auth data flow
> /agent bg generalist summarize the docs/ tree
> /agents
```

| Command | Effect |
|---------|--------|
| `/agent` or `/agent list` | List available sub-agents |
| `/agent <name> <task>` | Run in the foreground; the REPL waits for the result |
| `/agent bg <name> <task>` | Run in the background; status appears in the bottom toolbar |
| `/agent status` | List background task status |
| `/agent status <id>` | Show one background task's result or error |
| `/agent dashboard` or `/agents` | Open the live dashboard |
| `/agent cancel <id>` | Cancel a background task |
| `/agent delete <id>` | Remove a background-task record |

## `/todos` — current task list

For complex work, the agent may use the `todo_write` tool to maintain a task list. `/todos` prints that list.

```text
> /todos

Task List (2/4 completed):
  ✓ Read CLI docs
  ◉ Patch mismatched paths
  ○ Update /help
```

If there are no tasks, this session hasn't triggered multi-step task planning yet.

## `/tools` — tool registry

`/tools` lists every tool the current agent can call. `/tools <name>` prints its parameter schema.

```text
> /tools
> /tools run_shell_command
```

Use this when debugging “why didn't the agent call that tool”: first confirm the tool is registered, then inspect the permission mode.

## Where to go next

| Want to… | Read |
|----------|------|
| Review events after resuming | [9. Replay & Output](./9-replay-output) |
| Attach another agent over ACP | [8. MCP / ACP / Plugins](./8-mcp-acp-plugins) |
| Understand sub-agent events in an embedded host | [Part 4.2 · AgentEvent](/en/part-4/2-agent-events) |

---

::: tip Authoritative help
Command syntax: `/help`. Session restore: [`agentao/cli/commands.py:handle_sessions_command`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands.py). Sub-agents: [`agentao/cli/commands_ext/agents.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands_ext/agents.py). Tool listing: [`agentao/cli/commands.py:handle_tools_command`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands.py).
:::
