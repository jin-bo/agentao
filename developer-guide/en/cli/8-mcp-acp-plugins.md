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

Live config: `.agentao/acp.json` in the project root. Each entry under `servers` describes a full agent process that speaks ACP over stdio (JSON-RPC 2.0, one JSON message per line).

```json
{
  "servers": {
    "planner": {
      "command": "node",
      "args": ["./agents/planner/index.js"],
      "env": { "LOG_LEVEL": "info" },
      "cwd": ".",
      "description": "Planning agent"
    },
    "reviewer": {
      "command": "python",
      "args": ["-m", "review_agent"],
      "env": { "REVIEW_API_KEY": "${REVIEW_API_KEY}" },
      "cwd": "./agents/reviewer",
      "description": "Code review agent",
      "requestTimeoutMs": 120000
    }
  }
}
```

`command`, `args`, `env` and `cwd` are required. A relative `cwd` resolves against the project root. Values in `env` expand `$VAR` / `${VAR}` from your shell or `.env`, so a secret need not live in the file. Every key, with its default, is in [Appendix B.3.4](/en/appendix/b-config-keys#b-3-4-acp-json).

What to know:

- **Project only.** There is no user-global `~/.agentao/acp.json`.
- **Nothing starts by itself.** The CLI loads `acp.json` on the first `/acp` command or routed message, not at startup. It does not launch servers because `autoStart` is `true`. The first `/acp start` or `/acp send` launches a server, and `/acp send` also connects it.
- **Edits are picked up.** When `acp.json` changes, the next routed message reloads it.
- **Replies stay separate.** A server's output goes to the ACP inbox and your screen. It never enters the main agent's conversation.
- **Timeouts.** `startupTimeoutMs` is how long to wait for the `initialize` answer. The wait is never shorter than 30 s, whatever the value. `requestTimeoutMs` (default 60 s) bounds one `/acp send` turn.

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

The [ACP Registry](https://github.com/agentclientprotocol/registry) lists ACP agents and how to launch them. Agentao reads its stable index at `https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json`.

```text
> /acp registry search claude
> /acp registry add claude-acp            # server name defaults to the id
> /acp registry add gemini my-gemini      # or choose one
```

`add` shows the agent, its version and the launch command. It writes nothing until you confirm. Neither command launches the agent. The first `/acp send` or `/acp start` does, and that first launch downloads the package. The new entry works at once, without a CLI restart, and running servers are left alone.

`add` writes an ordinary `acp.json` entry:

```json
"claude-acp": {
  "command": "npx",
  "args": ["--yes", "@agentclientprotocol/claude-agent-acp@0.84.0"],
  "env": {},
  "cwd": ".",
  "autoStart": false,
  "startupTimeoutMs": 120000,
  "description": "Claude Agent 0.84.0 (ACP Registry: claude-acp)"
}
```

The rules:

- **`npx` and `uvx` only.** Node.js (for `npx`) or [uv](https://docs.astral.sh/uv/) (for `uvx`) must already be on `PATH`. Agentao installs no runner. When an entry offers both, `npx` wins. A binary-only entry is refused.
- **Pinned versions only.** The package spec must pin the entry's own version: `name@1.2.3`, `@scope/name@1.2.3`, or `name==1.2.3` (uv `[extras]` allowed). Any other spelling is refused, not guessed at.
- **No silent overwrite.** A name that is already configured is refused, so pass another name. An `acp.json` that cannot be read or is invalid is refused, not rewritten. Other entries and keys are kept, and the file is replaced atomically.
- **`env` values that contain `$` are refused.** `acp.json` expands `$VAR` in `env` and has no escape for a literal `$`. Add such an agent by hand.
- **No credentials are written.** See [Authentication and `/acp login`](#authentication-and-acp-login).

Hosts that embed Agentao can run the same steps without the CLI. See [3.4.5 · Adding a server at runtime](/en/part-3/4-reverse-acp-call#adding-a-server-at-runtime). Source: [`agentao/acp_client/registry.py`](https://github.com/jin-bo/agentao/blob/main/agentao/acp_client/registry.py).

### Authentication and `/acp login`

An agent that needs credentials answers `session/new` with `auth_required` (JSON-RPC `-32000`). Agentao reports this with the methods the agent offers. It does not count as a handshake failure, so sending again before you log in does not mark the server failed. Until a session opens, `/acp list` and `/acp status` show the server as `needs login`.

#### Terminal login

If the agent offers a `terminal` auth method, run:

```text
> /acp login <name>              # one terminal method: runs it
> /acp login <name> <method-id>  # several: pick one (or choose from a numbered list)
```

How it runs:

- **The command is the server's own.** Agentao runs the server's `command`, `args` and `cwd`, appends the method's `args`, and applies the method's `env` over the server's environment. This follows the ACP authentication spec.
- **It owns the terminal.** On macOS and Linux the login runs as a foreground job in its own process group. Ctrl+C cancels the login and does not interrupt Agentao. On Windows the login shares the console and runs in a job object.
- **A failure cleans up.** A cancelled or failed login is ended together with anything it started, such as the children of an `npx` or `uvx` runner.
- **Exit status `0` is success.** Anything else, a cancel, or a launch failure is reported as a failure, and nothing restarts.
- **After a success**, Agentao restarts the server, initializes it again and opens a session. A success also clears the recorded `auth_required` error. If the agent still asks for authentication, you see that once. The login does not run again.
- **The server is reserved.** While a login runs, a turn on that server or a second login gets `SERVER_BUSY`. A login is refused while a turn is active. Other servers are not affected.
- **A terminal is required.** Agentao declares Terminal Auth to agents only when stdin and stdout are a TTY. An agent may offer a `terminal` method only to a client that declared it. Thus `/acp login` needs an interactive terminal session.

Source: [`agentao/cli/commands_ext/acp_login.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands_ext/acp_login.py).

#### Other methods

Only `terminal` methods run in Agentao. For an `agent` method (the default when a method has no `type`) or any other type, authenticate outside Agentao. Then run `/acp restart <name>`.

#### Provider keys are not inherited

Agentao starts ACP servers without its own provider keys in the environment (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, …), the same as MCP servers and hooks. An agent that reads its key from the environment therefore answers `auth_required`, even when the key is set in your shell. Log in, or give the key to that server in its `env` block:

```json
"env": { "GEMINI_API_KEY": "${GEMINI_API_KEY}" }
```

A terminal login gets the same environment, so the same rule applies.

### Server lifecycle

```
configured → starting → initializing → ready → busy → ready
                                          ↘   waiting_for_user → ready
                                            ↘ stopping → stopped
                                              ↘ failed
```

- `initializing` is the ACP handshake (`initialize`, then `session/new`).
- `busy` means a turn is running. `waiting_for_user` means the server asked you something (see [Permission and input requests](#permission-and-input-requests)).
- A server that dies is rebuilt on the next call, at most `maxRecoverableRestarts` times while idle (default 3). A successful turn resets the count. A kill by a signal, two handshake failures in a row, or too many restarts leave it `failed` until you run `/acp restart <name>` or `/acp start <name>`.

`/acp list` shows the running count plus the inbox and pending-interaction queues:

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
- `needs login` (yellow) — the server answered `auth_required`. It is shown instead of `configured` / `stopped` / `failed` until a session opens

### Permission and input requests

A server can ask you to approve one of its tool calls, or ask for free text (the [`_agentao.cn/ask_user`](/en/appendix/c-acp-messages#c-11-agentao-cn-ask-user-⇠-request-extension) extension). The request becomes a **pending interaction**, and the CLI asks you inline. It asks during `/acp send`, and otherwise at the next idle point.

A permission request shows the tool, its kind and up to six of its arguments. Answer with one key:

```text
 1. Approve once  2. Approve all  3. Reject once  4. Reject all
 Press 1-4 · Esc to reject
```

For an input request, type a reply at the `>` prompt. An empty reply cancels the request.

`/acp status <name>` lists the requests that are still pending, and `/status` counts them. The time you spend on an answer does not count against `requestTimeoutMs`. The timer restarts after each answer.

### Routing a message to a server

You can hand a turn to a configured server without `/acp send`. Start the message with the server's name in one of these forms:

```text
> @reviewer check the latest diff
> reviewer: check the latest diff
> 让 reviewer 检查最新的 diff
> 请 reviewer 检查最新的 diff
```

The CLI prints `ACP Delegation → reviewer` and runs the turn exactly as `/acp send` does, with permission and input requests handled inline.

- **Only configured names match.** An unknown name goes to the main agent as an ordinary message. When names overlap, the longest wins (`qa.bot` before `qa`).
- **`让` / `请` need a space** before the name, so ordinary Chinese prose such as `请问…` is not routed. The colon form also accepts the full-width `：`.
- **No task, no turn.** A name with no task after it prints a usage hint.
- **A broken `acp.json`.** The `@`, `让` and `请` forms print the load error and consume the message, so your delegation never reaches the main agent by mistake. The `name:` form is common in prose (`Note: …`), so it falls through to the main agent after a one-time warning.
- **The main agent does not see the result.** Routing means "hand this turn to that server", not "tell the main agent". The reply stays in the ACP inbox.

Nothing is routed by guesswork. Only these prefix forms count. (A design that pushed server results into the main conversation was dropped before it shipped.) Source: [`agentao/acp_client/router.py`](https://github.com/jin-bo/agentao/blob/main/agentao/acp_client/router.py).

### Logs and status

Agentao keeps the last 200 lines of each server's stderr.

```text
> /acp logs reviewer        # last 50 lines
> /acp logs reviewer 100    # last 100 lines
```

`/acp status <name>` shows the state, PID, last error, last activity, session ID, protocol version, pending interactions and the last 5 stderr lines. When servers are configured, `/status` adds a summary:

```text
ACP servers: 1/2 running
ACP inbox: 3 queued
ACP interactions: 1 pending
```

### When to use ACP vs MCP

| You want… | Use |
|-----------|-----|
| Tool calls (read a file, query a DB) | MCP |
| Another agent to think and respond about a sub-problem | ACP |
| Cross-language interop (your agent in Python, theirs in Go) | ACP |
| To compose existing public tool servers | MCP |

### Pitfalls

- **`/acp send` blocks the REPL** — you can't talk to your local agent until the turn finishes or `requestTimeoutMs` runs out. Press Ctrl+C to cancel the turn. A routed `@name` message blocks the same way.
- **`waiting_for_user` means the server is asking you something** — the CLI shows the question inline. You don't answer it with `/acp send`.
- **The inbox is bounded** — it holds 256 messages, and the oldest is dropped when it is full. Messages appear at idle points: before the prompt, after a slash command, and after an agent reply.
- **A server is `failed` after repeated crashes** — automatic rebuilds stop at `maxRecoverableRestarts`. Read `/acp logs <name>`, fix the cause, then `/acp restart <name>`.

### Troubleshooting

**The server fails to start.**
1. Read the error in `/acp status <name>`.
2. Read the stderr in `/acp logs <name>`.
3. Check that `command` exists and is executable, and that `cwd` is a directory.

**`requires authentication`.**
- The agent answered `auth_required`. Run `/acp login <name>` if it offers a terminal login. Otherwise authenticate outside Agentao and run `/acp restart <name>`.
- An agent that reads a provider key from the environment does not see your shell's key. See [Provider keys are not inherited](#provider-keys-are-not-inherited).

**The first launch of a Registry agent times out.**
- The first `npx` or `uvx` run downloads the package. Registry entries wait up to 120 s for `initialize`. Raise `startupTimeoutMs` for a slow network, or run the launch command once in a shell to fill the runner's cache.
- `npx` or `uvx` must be on `PATH`.

**The server starts, but the handshake fails.**
- The server must answer `initialize` with a valid ACP response, and `session/new` with a `sessionId`.
- Look for protocol errors in `/acp logs <name>`.

**A turn reports an active turn already.**
- `/acp send` cancels the stale turn and sends once more. If that fails too, run `/acp cancel <name>`, or `/acp restart <name>`.

**`Timeout waiting for response`.**
- The turn ran longer than `requestTimeoutMs`, so the CLI cancelled it. Raise `requestTimeoutMs` for that server.

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
- ACP: the CLI keeps its own `ACPManager`. A host builds one with `ACPManager.from_project()` for the same status, send and cancel. See [3.4 · Calling external ACP agents](/en/part-3/4-reverse-acp-call).
- Plugins: `PluginManager` (in `agentao.embedding.plugins.manager`) — the diagnostic report is generated by `agentao.embedding.plugins.diagnostics.build_diagnostics`, which a host can also invoke. See [Part 5.7](/en/part-5/7-plugin-hooks) for the full programmatic interface.
:::

::: tip Authoritative help
Command syntax: `/help`. Behavior anchors:
- [`agentao/cli/commands/mcp.py:handle_mcp_command`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands/mcp.py)
- [`agentao/cli/commands_ext/acp.py:handle_acp_command`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands_ext/acp.py)
- [`agentao/cli/subcommands.py:_handle_plugins_interactive`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/subcommands.py)
:::
