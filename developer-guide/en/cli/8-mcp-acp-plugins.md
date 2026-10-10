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
      "url": "https://api.example.com/mcp",
      "headers": { "Authorization": "Bearer $API_KEY" },
      "timeout": 30
    }
  }
}
```

Three transports:

| Transport | Trigger | What runs |
|-----------|---------|-----------|
| stdio | `"command": "..."` present | Local subprocess, stdio JSON-RPC |
| Streamable HTTP | `"url": "..."` present (default), or `"type": "http"` | Remote Streamable HTTP endpoint |
| SSE (legacy) | `"type": "sse"` with `"url"` | Remote SSE endpoint |

A bare `url` now defaults to **Streamable HTTP** — add `"type": "sse"` for a legacy SSE endpoint. (Breaking change; see [MCP deep dive](/en/part-5/3-mcp).)

Env vars in the config use `$VAR_NAME` and are expanded at load time from your shell environment / `.env`.

`"trust": true` skips the confirmation UI for tools from this server. **Don't set this on a server that calls external APIs with your credentials.**

### Subcommands

```text
> /mcp                                  # alias for /mcp list
> /mcp list                             # list all configured servers
> /mcp add github npx -y @modelcontextprotocol/server-github
> /mcp add remote https://api.example.com/mcp
> /mcp remove github
> /mcp resources                       # list resources and templates (no model turn)
> /mcp resources docs                  # one server only
> /mcp login remote                     # authorize an OAuth server in the browser
> /mcp login remote --no-browser        # print the URL, paste the redirect back
> /mcp logout remote                    # delete its stored credential
```

`/mcp list` output:

```text
MCP Servers (3):

  ● filesystem  command — connected, 12 tool(s)
  ● github      command — connected, 24 tool(s) (trusted)
  ● remote      url     — failed
    Connection refused
```

For a server with `"skills": true`, `/mcp list` adds a line under it: the number of skills it serves, or why its skills are unavailable.

`/mcp add` writes to the **project** config (`.agentao/mcp.json`) — it never touches the user-global one. If that file exists but cannot be read as a JSON object, `/mcp add` and `/mcp remove` print an error and leave it unchanged rather than replace it and lose the servers in it. Fix the file, or move it aside, and run the command again.

`/mcp remove` deletes the entry from the project config but **the change requires restart** (the message tells you so). The current session keeps the running connection.

### `/mcp login` and `/mcp logout` — OAuth servers

A URL server (Streamable HTTP or SSE) uses OAuth by default. Nothing in `mcp.json` turns it on. It does not apply to a stdio server, to a server with `"oauth": false`, or to a server whose `headers` already set `Authorization`. When such a server answers 401, agentao does not open a browser by itself. The server shows as `needs login` in `/mcp list`, and at startup the REPL prints:

```text
MCP server 'remote' needs login — run /mcp login remote
```

Run the login:

1. `/mcp login remote` prints the authorization URL and opens your browser. It waits up to 300 s for the redirect. Ctrl+C cancels.
2. You approve the request in the browser. The browser is sent back to a listener on `127.0.0.1` (`/callback/<server>`), and the page says you can close it.
3. agentao stores the credential and reconnects the server in the running session: `Logged in to 'remote' — connected, 5 tool(s).`

With `--no-browser`, or when no browser can open (for example over SSH with no display), agentao prints the URL and asks for `Redirect URL:`. Open the URL on any machine, approve, and paste the full address the browser was sent to, even if that page did not load. The input is hidden. The listener keeps running, so an SSH port forward also works.

Tools load only when agentao starts. If the server had no tools when the session started, the login ends with `Restart agentao to load the tools of 'remote'.` If it had tools, the next call uses the new token.

`/mcp logout remote` deletes the stored credential and disconnects the server. The server's tools stay listed. If the server requires login, calls to them fail and show the login hint until you log in again. With no credential stored, it says `'remote' had no stored credential.`

From a shell, `agentao mcp login <name> [--no-browser]` and `agentao mcp logout <name>` do the same. They work in a library-only install too. Exit codes: `0` connected, `1` failed (or stored but the reconnect failed), `2` usage error, `130` cancelled. Use them for a headless `agentao run` or an ACP session, which never start a login themselves. Those processes read the new credential on the server's next connect or call.

What to know:

- **Credentials** are stored in `~/.agentao/mcp-oauth/`, one file per server URL and profile (mode 0600, directory 0700). They are plain JSON, not in an OS keyring. Two server names with the same URL and no profile share one credential, in every project.
- **Refresh** is automatic: 60 s before expiry, and once more after a 401. If the authorization server rejects the refresh, the server goes back to `needs login`. A network failure is reported as an ordinary error and does not ask you to log in.
- **Settings** go in an optional `oauth` object on the server: `client_id`, `client_secret` (needs `client_id`, `$VAR` is expanded), `callback_port` (1–65535), `redirect_host` (`localhost` or a loopback address), and `profile` (below). Any other key is an error. There is no `scopes` key: the login asks for the scope the server's 401 names. Without `client_id`, agentao registers itself with the server as public client `agentao`.
- **Two accounts on one server.** Give each entry its own `oauth.profile`. Each profile is a separate login for that URL, so both stay logged in at once:

  ```json
  {
    "mcpServers": {
      "tracker-work":     { "url": "https://mcp.example.com/mcp", "oauth": { "profile": "work" } },
      "tracker-personal": { "url": "https://mcp.example.com/mcp", "oauth": { "profile": "personal" } }
    }
  }
  ```

  Run `/mcp login tracker-work` and `/mcp login tracker-personal` once each. `/mcp logout` removes only that entry's profile. The profile names the account, not the entry: entries in two projects with the same URL and profile share one login, and renaming an entry keeps it. An entry with a profile never uses the URL's no-profile login, so it starts at `needs login` even when that login exists. Logins from before profiles existed keep working for entries without one. The browser decides which account you sign in with. If it signs you straight back in as the other account, sign out there first, or use `--no-browser` and open the printed link in another browser profile. A profile is a non-empty string with no leading or trailing spaces, and the comparison is exact: `Work` and `work` are two profiles.
- **ACP-supplied servers** (from an editor's `session/new`) never use agentao's OAuth. The editor handles their authorization.
- **Limitations.**
  - On mcp 1.x, the SDK does not check the authorization server's `iss` (RFC 9207). On mcp 1.26, each login also registers a new client. Use mcp 2.x for both.
  - A `403 insufficient_scope` is reported with the scope the server wants, but a new login asks only for the scope in the server's 401. Logging in again may not clear it.
  - The login reads internal fields of the SDK. If a future SDK release moves them, the login stops with a message, and servers that are already logged in keep working.

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
> /acp logs <name> [lines]      # tail stderr (default last 50)
> /acp login <name> [method-id] # run the server's terminal login, then restart + reconnect
> /acp registry search <keyword>  # search the official ACP Registry
> /acp registry add <id> [name]   # add a Registry agent (npx / uvx) to acp.json
```

### Adding agents from the ACP Registry (0.5.8+)

`/acp registry add <id>` shows the agent, its pinned version and launch command, and writes an `npx` / `uvx` entry to `.agentao/acp.json` only after you confirm. Nothing is launched until the first `/acp send` or `/acp start`, which downloads the package (Node.js or uv must already be installed). Binary-only entries are refused.

An agent without credentials answers `auth_required`; `/acp` then lists it as `needs login`. If it offers a `terminal` auth method, `/acp login <name>` runs that login in your terminal, and on exit status `0` restarts the server and connects again. Other auth methods are done outside Agentao, followed by `/acp restart <name>`.

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
- `needs login` (yellow) — the server answered `auth_required`; shown instead of `failed` / `stopped` until a session opens

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
