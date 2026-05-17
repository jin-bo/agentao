# CLI · Terminal Reference

Agentao ships with a terminal-first interface — `agentao` in your shell, with slash commands, plan mode, sub-agents, memory, and replay all built in. This section documents that interface.

## Install in 60 seconds

```bash
# Clone or install
uv sync                  # installs the project
cp .env.example .env     # paste your OPENAI_API_KEY

# Start the agent
uv run agentao
# or:  ./run.sh
```

You drop into a chat REPL. Type a normal sentence to talk to the agent. Type `/` followed by a command to control the session itself.

## Coverage

- [**1. Getting Started**](./1-getting-started) — `/help`, `/clear`, `/new`, `/status`, `/exit` · the minimum loop
- [**2. Models & Providers**](./2-models-providers) — `/model`, `/provider`, `/temperature` · switch LLMs and credentials at runtime
- [**3. Permissions & Modes**](./3-permissions-modes) — `/mode`, tool-confirmation UI, `/sandbox` (macOS) · how the agent asks before doing dangerous things
- [**4. Plan Mode**](./4-plan-mode) — `/plan` workflow · read-only "think first, then commit" loop
- [**5. Skills & Crystallize**](./5-skills-crystallize) — `/skills`, `/crystallize` · activate skills and distill new ones from a session
- [**6. Memory**](./6-memory) — `/memory` · what gets remembered, where it lives, how to inspect and clear it
- [**7. Context & Status**](./7-context-status) — `/context`, `/compact`, `/status` · token budget, compaction, session size
- [**8. MCP / ACP / Plugins**](./8-mcp-acp-plugins) — `/mcp`, `/acp`, `/plugins` · attach external tool servers
- [**9. Replay & Output**](./9-replay-output) — `/replay`, `/copy`, `/markdown` · record sessions, copy answers, render control
- [**10. Configuration Reference**](./10-config-reference) — every config file the CLI reads, with paths and precedence
- [**11. Sessions, Agents & Tasks**](./11-sessions-agents) — `/sessions`, `/agent`, `/agents`, `/todos`, `/tools` · restore and parallel workbench
- [**12. Non-Interactive Entry Points**](./12-non-interactive) — `agentao init`, `-p`, `--resume`, `--acp`, `agentao doctor`, `agentao config validate` · scripts, CI checks, and host integration

## How to read

| Your situation | Recommended path |
|----------------|------------------|
| First time using `agentao` | [1. Getting Started](./1-getting-started) → [3. Permissions & Modes](./3-permissions-modes) |
| Coming from another agent CLI (Claude Code, codex, gemini, etc.) | [4. Plan Mode](./4-plan-mode) → [5. Skills & Crystallize](./5-skills-crystallize) |
| I want to plug in my company's tools | [8. MCP / ACP / Plugins](./8-mcp-acp-plugins) → [Part 5.3 MCP](/en/part-5/3-mcp) |
| The agent ate my budget / context blew up | [7. Context & Status](./7-context-status) → [6. Memory](./6-memory) |
| I want to resume a session / inspect background agents | [11. Sessions, Agents & Tasks](./11-sessions-agents) |
| I'm shipping the CLI to my team | [3. Permissions & Modes](./3-permissions-modes) → [10. Configuration Reference](./10-config-reference) |
| I want to call Agentao from scripts, CI, or an IDE | [12. Non-Interactive Entry Points](./12-non-interactive) → [Part 3 · ACP Protocol](/en/part-3/) |
| I want to embed this engine into my own app | [Part 1 · Getting Started](/en/part-1/) (different audience — start there) |

## Mental model

> The CLI is a thin REPL on top of the Agentao harness.
> Slash commands manipulate the **session** (history, model, mode, plan, memory).
> Plain messages are sent to the **agent** (tools, skills, MCP, ACP).
> Everything you see in the terminal — confirmation prompts, streaming events, tool results, memory recall — is exactly what an embedding host receives through the event stream.

If you understand the CLI, you already understand most of what an embedder builds against.

→ [Start with 1. Getting Started →](./1-getting-started)

---

::: info Where this fits
The CLI is **one consumer** of the Agentao harness. The same harness can be embedded into your own application — see [Part 2 · Python In-Process Embed](/en/part-2/) or [Part 3 · ACP Protocol](/en/part-3/). What you learn here about permissions, skills, MCP, memory and replay applies identically when you embed.
:::

::: tip Authoritative help
The single source of truth for command syntax is `/help` inside the CLI, backed by [`agentao/cli/help_text.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/help_text.py). The pages here explain the *why* and *how to use*; if anything ever disagrees, trust `/help`.
:::
