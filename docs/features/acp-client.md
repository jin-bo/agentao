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
```

## Configuration Reference

### Server Config Fields

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `command` | string | yes | — | Executable to launch |
| `args` | string[] | yes | — | Command arguments |
| `env` | object | yes | — | Extra environment variables |
| `cwd` | string | yes | — | Working directory (relative to project root) |
| `autoStart` | boolean | no | `true` | Reserved for bulk-start flows; current CLI does not auto-start servers just because `/acp` was opened |
| `startupTimeoutMs` | integer | no | `10000` | Parsed config field; currently not enforced by the CLI runtime |
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

- [docs/implementation/acp-client-project-servers/issues/12-explicit-routing-and-push-delegation.md](../implementation/acp-client-project-servers/issues/12-explicit-routing-and-push-delegation.md)

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

Agentao supports a private ACP extension method `_agentao.cn/ask_user` for requesting free-form text input from the user. This is advertised in the `initialize` response's `extensions` array.

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
