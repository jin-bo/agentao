# 8. MCP / ACP / Plugins

These three commands wire **external** tooling into your CLI session.

| Command | What it attaches | Direction |
|---------|------------------|-----------|
| `/mcp` | MCP servers — external tool providers (filesystem, github, db, …) | Agent calls **out** to them |
| `/acp` | ACP servers — full agents speaking the Agent Client Protocol | Agent collaborates with **other agents** |
| `/plugins` | Lifecycle hooks (Stop / PreToolUse / UserPromptSubmit / PreCompact) | Hooks intercept the **agent's own** lifecycle events |

If you only ever use the built-in tools, you'll never need this chapter. The moment you say "I want my agent to talk to my company's GitHub via the official MCP server" or "I want this agent to call into another agent over stdio", you start here.

## `/mcp` — MCP servers

[Model Context Protocol](https://modelcontextprotocol.io) is an open standard for tool servers. An MCP server exposes a set of tools (`fs.read_file`, `github.create_issue`, …) over stdio JSON-RPC or HTTP/SSE; the agent uses them like any other tool.

### Configuration file

Live config: `.agentao/mcp.json` (project) and `~/.agentao/mcp.json` (user-global).

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/me/data"]
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "$GITHUB_TOKEN" },
      "trust": false
    },
    "remote": {
      "url": "https://api.example.com/sse",
      "headers": { "Authorization": "Bearer $API_KEY" },
      "timeout": 30
    }
  }
}
```

Two transports:

| Transport | Trigger | What runs |
|-----------|---------|-----------|
| `command` | `"command": "..."` present | Local subprocess, stdio JSON-RPC |
| `url` | `"url": "..."` present | Remote SSE endpoint |

Env vars in the config use `$VAR_NAME` and are expanded at load time from your shell environment / `.env`.

`"trust": true` skips the confirmation UI for tools from this server. **Don't set this on a server that calls external APIs with your credentials.**

### Subcommands

```text
> /mcp                                  # alias for /mcp list
> /mcp list                             # list all configured servers
> /mcp add github npx -y @modelcontextprotocol/server-github
> /mcp add remote https://api.example.com/sse
> /mcp remove github
```

`/mcp list` output:

```text
MCP Servers (3):

  ● filesystem  command — connected, 12 tool(s)
  ● github      command — connected, 24 tool(s) (trusted)
  ● remote      url     — failed
    Connection refused
```

`/mcp add` writes to the **project** config (`.agentao/mcp.json`) — it never touches the user-global one.

`/mcp remove` deletes the entry from the project config but **the change requires restart** (the message tells you so). The current session keeps the running connection.

### Tool naming

MCP tools are registered as `mcp_{server}_{tool}`. So `filesystem.read_file` becomes `mcp_filesystem_read_file` in the agent's tool list. This is how you spot MCP-sourced tools in `/help`.

### Pitfalls

- **A failed connection doesn't break the CLI** — the server shows up red in `/mcp list`, its tools are unavailable, the rest works
- **`/mcp add` doesn't auto-start** — you may need to restart for some configs (the CLI tells you)
- **Trust is a session decision, not a per-call one** — `"trust": true` means *every* tool call to that server skips confirmation. There's no per-tool granularity here; use the permission engine for finer control
- **stdio servers leak processes if you `Ctrl+C` to exit** — always `/exit`

## `/acp` — ACP servers

[ACP (Agent Client Protocol)](/en/part-3/) is the protocol Agentao uses for agent-to-agent communication. `/acp` lets you start, stop, and talk to other ACP-speaking agents from within your CLI session.

Unlike MCP (which adds tools to *your* agent), ACP attaches **another agent** that you can hand prompts to. Think of it as `gh repo clone` for agents.

### Configuration file

Live config: `.agentao/acp.json`. Format is similar to `mcp.json` but each entry describes a full agent process.

### Subcommands

```text
> /acp                          # alias for /acp list
> /acp list                     # configured servers + state
> /acp start <name>             # launch
> /acp stop <name>              # shut down
> /acp restart <name>
> /acp send <name> <prompt>     # send a turn; permission/input handled inline
> /acp cancel <name>            # cancel an in-flight turn
> /acp status <name>            # detailed status
> /acp logs <name> [lines]      # tail stderr (default last 20)
```

State machine:

```
configured → starting → initializing → ready → busy → ready
                                          ↘   waiting_for_user → ready
                                            ↘ stopping → stopped
                                              ↘ failed
```

`/acp list` shows running count plus inbox / pending interaction queues:

```text
ACP Servers (1/2 running):
Inbox: 3 queued
Pending interactions: 1

  ● local-coder    ready pid=8421  General coding agent
  ● remote-helper  failed          Connection refused
```

The state colors map to:
- `ready` (green), `busy` (cyan), `waiting_for_user` (magenta)
- `starting`/`initializing`/`stopping` (yellow)
- `configured`/`stopped` (dim)
- `failed` (red)

### When to use ACP vs MCP

| You want… | Use |
|-----------|-----|
| Tool calls (read a file, query a DB) | MCP |
| Another agent to think and respond about a sub-problem | ACP |
| Cross-language interop (your agent in Python, theirs in Go) | ACP |
| To compose existing public tool servers | MCP |

### Pitfalls

- **`/acp send` blocks the REPL by default** — long-running ACP turns mean you can't talk to your local agent until done. Use `/acp cancel` if needed.
- **`waiting_for_user` state means the remote needs input from you** — `/acp status <name>` shows the prompt; respond with `/acp send`.
- **Inbox accumulates if you ignore it** — un-handled ACP server messages queue up. Drain with `/acp send` responses or restart.
- **ACP servers with stale PIDs** — happens when the host machine restarts but `acp.json` still references a dead pid. `/acp restart <name>` fixes it.

## `/plugins` — lifecycle hooks

`/plugins` (alias `/plugin`) shows what hook plugins are loaded for the current working directory.

Plugins are external Python packages that hook into agent lifecycle events:

- `UserPromptSubmit` — before the agent sees a new user message
- `PreToolUse` — before a specific tool call runs
- `Stop` — when the agent decides to finish a turn (audit / continuation)
- `PreCompact` — before the context manager compresses history

### What you see

```text
> /plugins
Agentao Plugin Diagnostics

Loaded plugins (2):
  • my-org/audit-logger  v1.2.0
    Hooks: UserPromptSubmit, PreToolUse, Stop
    Source: pip-installed (agentao_plugin_audit_logger)

  • ./plugins/dev-only-injector  (inline)
    Hooks: UserPromptSubmit
    Source: inline

Warnings: 0
Errors: 0
```

The diagnostic report covers:
- Which plugins loaded and where they came from (pip vs inline)
- Which lifecycle hooks each one registered
- Warnings (e.g. plugin claims a hook but failed to register)
- Errors (e.g. import failure)

### When to use

- **Debugging "why is my agent doing X?"** — a plugin may be silently injecting a system prompt or rejecting a tool call
- **Verifying CI / production setup** — the plugin you expected is loaded and registered to the right hooks
- **After updating a plugin** — confirm the new version is in effect

### What `/plugins` is *not*

- It's not a CLI for *managing* plugins — there's no `/plugins install` or `/plugins remove`. Plugins are pip-installed (or inline) and discovered automatically. To uninstall, `pip uninstall <pkg>` and restart.
- It's not the place to *write* plugins. See [Part 5.7 · Plugin Hooks](/en/part-5/7-plugin-hooks).

## Where to go next

| Want to… | Read |
|----------|------|
| Build a custom MCP server for your team | [Part 5.3 · MCP](/en/part-5/3-mcp) |
| Embed an ACP server / drive an agent from another language | [Part 3 · ACP Protocol](/en/part-3/) |
| Write a lifecycle hook plugin | [Part 5.7 · Plugin Hooks](/en/part-5/7-plugin-hooks) |

---

::: info Where this fits
- MCP: `agent.mcp_manager` — embedding hosts can call `manager.get_server_status()` for the same data shown here.
- ACP: `agent.acp_manager` — same for ACP server status, send, cancel.
- Plugins: `PluginManager` (in `agentao.embedding.plugins.manager`) — the diagnostic report is generated by `agentao.embedding.plugins.diagnostics.build_diagnostics`, which a host can also invoke. See [Part 5.7](/en/part-5/7-plugin-hooks) for the full programmatic interface.
:::

::: tip Authoritative help
Command syntax: `/help`. Behavior anchors:
- [`agentao/cli/commands.py:handle_mcp_command`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands.py)
- [`agentao/cli/commands_ext/acp.py:handle_acp_command`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands_ext/acp.py)
- [`agentao/cli/subcommands.py:_handle_plugins_interactive`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/subcommands.py)
:::
