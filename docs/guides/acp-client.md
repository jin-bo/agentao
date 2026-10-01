# ACP Client — Project-Local Server Management

Agentao can connect to and manage project-local ACP (Agent Client Protocol) servers. These are external agent processes that communicate over stdio using JSON-RPC 2.0 with NDJSON framing.

## Quick Start

### 1. Create a config file

Create `.agentao/acp.json` in your project root:

```json
{
  "servers": {
    "planner": {
      "command": "node",
      "args": ["./agents/planner/index.js"],
      "env": { "LOG_LEVEL": "info" },
      "cwd": ".",
      "description": "Planning agent",
      "autoStart": true
    },
    "reviewer": {
      "command": "python",
      "args": ["-m", "review_agent"],
      "env": {},
      "cwd": "./agents/reviewer",
      "description": "Code review agent",
      "autoStart": false,
      "requestTimeoutMs": 120000
    }
  }
}
```

### 2. Use `/acp` commands

```
/acp                          # Overview of all servers
/acp list                     # Same as /acp
/acp start <name>             # Start a server
/acp stop <name>              # Stop a server
/acp restart <name>           # Restart a server
/acp send <name> <message>    # Send a prompt (auto-connects; handles permission/input inline)
/acp cancel <name>            # Cancel active turn
/acp status <name>            # Detailed status
/acp logs <name> [lines]      # View stderr output
/acp login <name> [method-id] # Run the server's terminal login, then restart and reconnect
/acp registry search <keyword>  # Search the official ACP Registry
/acp registry add <id> [name]   # Add a Registry agent (npx / uvx) to .agentao/acp.json
```

## Adding Agents from the ACP Registry

The [ACP Registry](https://github.com/agentclientprotocol/registry) lists
ACP agents and how to launch them. Agentao reads its stable index
(`https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json`):

```
/acp registry search claude
/acp registry add claude-acp            # server name defaults to the id
/acp registry add gemini my-gemini      # or choose one
```

`add` shows the agent, version and launch command, and writes nothing until
you confirm. Neither command launches the agent; the first `/acp send` (or
`/acp start`) does, and that first launch downloads the package. The entry is
usable at once, without restarting the CLI, and servers already running are
left alone.

What `add` writes is an ordinary `acp.json` entry:

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

Scope of the first version:

- **`npx` and `uvx` only.** Node.js (for `npx`) or [uv](https://docs.astral.sh/uv/)
  (for `uvx`) must already be installed; Agentao installs no runner. When an
  entry offers both, `npx` is used. Binary-only entries are refused.
- **Pinned versions only.** The package spec must pin the entry's own
  version — `name@1.2.3`, `@scope/name@1.2.3`, `name==1.2.3`, with optional
  uv `[extras]`. Other spellings are refused, not guessed at.
- **No silent overwrite.** A name that is already configured is refused;
  pass another name. An invalid existing `acp.json` is refused rather than
  rewritten; other entries and fields are kept, and the file is replaced
  atomically.
- **`env` values containing `$` are refused**, because `acp.json` expands
  `$VAR` in `env` and has no escape for a literal `$`. Add such an agent by
  hand.
- **No credentials are written.** If the agent needs a provider key, see
  *Authentication* below.

Embedding hosts can use the same pieces without the CLI:
`agentao.acp_client.registry` (`fetch_registry`, `search_registry`,
`find_agent`, `entry_to_server_config`),
`agentao.acp_client.config.add_server_entry`, and
`ACPManager.add_server(name, config)`. Each step is separate, so the host
decides whether to write the file, register the server, or start it.

## Authentication

An agent that needs credentials answers `session/new` with `auth_required`
(JSON-RPC `-32000`). Agentao reports it as such, with the methods the agent
advertised, and never counts it as a handshake failure. So sending again
before logging in does not mark the server fatal.

### Terminal login

If the agent offers a `terminal` auth method, run:

```
/acp login <name>              # one terminal method: runs it
/acp login <name> <method-id>  # several: pick one
```

Following the ACP authentication spec, Agentao runs the server's own
command, arguments and `cwd` with the method's `args` appended and its `env`
applied over the server's environment. The login has the terminal to itself:
it runs as a foreground job, in its own process group, and Ctrl+C cancels it
without interrupting Agentao. A cancelled or failed login is ended together
with anything it started (an `npx` / `uvx` runner's children, for instance);
on Windows the login shares the console and runs in a job object, which
Agentao terminates.
Exit status `0` is success; anything else (including a
cancel or a launch failure) is reported as a failure and nothing is
restarted. After a success Agentao restarts the server, initializes again and
opens a session. If the agent still requires authentication, that is
reported once; the login is not run again.

Until a session opens, `/acp` and `/acp status` show such a server as
`needs login` rather than `failed`, and a successful login clears the
recorded `auth_required` error.

While a login runs, the server is reserved. A turn on it, or a second login,
gets `SERVER_BUSY`; a login is refused while a turn is active. Other servers
are not affected.

Agentao declares Terminal Auth (`clientCapabilities.auth.terminal` and the
legacy `_meta["terminal-auth"]`) only in an interactive terminal. Agents may
offer a `terminal` method only to a client that declared it, so `/acp login`
needs a terminal session.

### Other methods

Only `terminal` methods are supported. For an `agent` method (the default
when a method has no `type`) or any other type, authenticate outside
Agentao, then `/acp restart <name>`.

### Provider keys are not inherited

ACP servers are launched with Agentao's own provider keys removed from the
environment (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, …),
like MCP servers and hooks. An agent that expects its key from the
environment therefore answers `auth_required` even if the key is set in your
shell. Log in, or give the key to that server explicitly in its `env` block
(`"GEMINI_API_KEY": "${GEMINI_API_KEY}"`). A terminal login gets the same
environment, so the same rule applies.

## Configuration Reference

### Server Config Fields

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `command` | string | yes | — | Executable to launch |
| `args` | string[] | yes | — | Command arguments |
| `env` | object | yes | — | Extra environment variables |
| `cwd` | string | yes | — | Working directory (relative to project root) |
| `autoStart` | boolean | no | `true` | Used by `ACPManager.start_all()`; the CLI does not auto-start servers just because `/acp` was opened. Registry entries are written with `false` |
| `startupTimeoutMs` | integer | no | `10000` | How long to wait for the `initialize` answer from a freshly launched server, never less than 30 s (the default request wait). Registry entries are written with `120000` to allow for a first-run package download |
| `requestTimeoutMs` | integer | no | `60000` | Per-request timeout in ms |
| `capabilities` | object | no | `{}` | Server capability hints |
| `description` | string | no | `""` | Human-readable description |

Values in `env` support `$VAR` / `${VAR}` expansion from the process environment, so secrets such as API keys can live in the shell / `.env` rather than in `acp.json`.

### Key Design Decisions

- **Project-only config.** No global `<home>/.agentao/acp.json` — ACP servers are project-scoped.
- **No auto-send.** Messages are never automatically routed to ACP servers. Use `/acp send` explicitly.
- **ACP responses stay separate.** Server output appears in the ACP inbox, not in the main Agentao conversation context.
- **Lazy initialization.** The ACP manager is created on first `/acp` command, not at startup.
- **Inline interaction handling.** Permission and input requests are handled inline during `/acp send` and at safe idle points in the main CLI loop.

## Server Lifecycle

```
configured → starting → initializing → ready ↔ busy → stopping → stopped
                                         ↕
                                   waiting_for_user
```

- **configured**: Config loaded, process not started.
- **starting**: `subprocess.Popen` called.
- **initializing**: ACP handshake (`initialize` + `session/new`) in progress.
- **ready**: Handshake complete, accepting prompts.
- **busy**: Processing a `session/prompt`.
- **waiting_for_user**: Server requested user interaction (permission or input).
- **stopping/stopped**: Graceful shutdown.
- **failed**: Crash or handshake failure.

## Interaction Bridge

When an ACP server needs user input (permission confirmation or free-form text), it becomes a **pending interaction**. These appear in the inbox and are handled inline by the CLI.

```
Permission requests: choose 1 / 2 / 3 / 4
Input requests: type a reply inline at the prompt
```

Pending interactions are visible in `/acp status <name>` and `/status`.

## Explicit Target-Server Routing

User messages that explicitly name a configured ACP server are routed directly to that server instead of going through the normal main-agent turn. Recognised deterministic forms:

- `@server-name <task>`
- `server-name: <task>`
- `让 server-name <task>` / `请 server-name <task>`

On a match the CLI prints `ACP Delegation → <server>` and reuses the same runner as `/acp send` (inline handling of permission / input requests).

Notes:

- Only configured server names match; unknown names fall through to the normal agent path.
- Empty task text after the server name prints a usage hint.
- Results stay in the ACP inbox and are **not** injected into the main Agentao conversation context — the explicit-routing semantics are "hand this turn to the sub-agent", not "let the main agent know".

### Push Delegation (removed)

An earlier design proposed an experimental `pushTaskCompleteToAgent` flag that would bridge private `task_complete` notifications into the main Agentao conversation. It was **dropped** before landing: `task_complete` is not part of the ACP standard enum of `sessionUpdate` kinds, so shipping it would require every compatible server to speak a private extension. The flag, its queue, and the synthetic-message injection path are no longer present in the codebase. See the corresponding design doc for history.

Design doc:

- [docs/history/implementation/acp-client-project-servers/issues/12-explicit-routing-and-push-delegation.md](../history/implementation/acp-client-project-servers/issues/12-explicit-routing-and-push-delegation.md)

## Diagnostics

### Stderr Logs

Server stderr is captured in a bounded ring buffer (200 lines). View with:

```
/acp logs <name>        # Last 50 lines
/acp logs <name> 100    # Last 100 lines
```

### Status

`/status` shows an ACP summary when servers are configured:

```
ACP servers: 1/2 running
ACP inbox: 3 queued
ACP interactions: 1 pending
```

## Troubleshooting

### Server fails to start

1. Check `/acp status <name>` for the error message.
2. Check `/acp logs <name>` for stderr output.
3. Verify the `command` exists and is executable.
4. Verify `cwd` is a valid directory.

### `requires authentication`

- The agent answered `auth_required`. Run `/acp login <name>` if it offers a
  terminal login; otherwise authenticate outside Agentao and
  `/acp restart <name>`.
- An agent that reads a provider key from the environment does not see your
  shell's key (see *Provider keys are not inherited*).

### First launch of a Registry agent times out

- The first `npx` / `uvx` run downloads the package. Registry entries wait up
  to 120 s for `initialize`; raise `startupTimeoutMs` for a slow network, or
  run the launch command once in a shell to fill the runner's cache.
- `npx` / `uvx` must be on `PATH`.

### Server starts but handshake fails

1. The server must respond to `initialize` with a valid ACP response.
2. The server must respond to `session/new` with a `sessionId`.
3. Check `/acp logs <name>` for protocol errors.

### Messages not appearing

- Messages appear at safe idle points (before prompt, after agent response).
- Use `/acp` to see the current inbox count.

### Permission requests timing out

- Default behavior: permission requests that expire are rejected.
- Input requests that expire are cancelled.
- Respond promptly when the inline permission or input prompt appears.

## ACP Extension: `_agentao.cn/ask_user`

Agentao supports a private ACP extension method `_agentao.cn/ask_user` for requesting free-form text input from the user. This is advertised in the `initialize` response under `_meta["_agentao.cn/extensions"]` (ACP's standard channel for extension data).

### Request

```json
{
  "jsonrpc": "2.0",
  "id": "srv_123",
  "method": "_agentao.cn/ask_user",
  "params": {
    "sessionId": "sess_xxx",
    "question": "Please provide branch name"
  }
}
```

### Response

```json
{
  "outcome": "answered",
  "text": "feature/acp-client"
}
```

Or on cancellation:

```json
{
  "outcome": "cancelled"
}
```

If the user is unavailable, the sentinel `"(user unavailable)"` is returned as a conservative fallback — the turn is not crashed.
