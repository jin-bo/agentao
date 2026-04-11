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
/acp send <name> <message>    # Send a prompt (auto-connects)
/acp cancel <name>            # Cancel active turn
/acp status <name>            # Detailed status
/acp logs <name> [lines]      # View stderr output
/acp approve <name> <id>      # Approve a permission request
/acp reject <name> <id>       # Reject a permission request
/acp reply <name> <id> <text> # Reply to an input request
```

## Configuration Reference

### Server Config Fields

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `command` | string | yes | — | Executable to launch |
| `args` | string[] | yes | — | Command arguments |
| `env` | object | yes | — | Extra environment variables |
| `cwd` | string | yes | — | Working directory (relative to project root) |
| `autoStart` | boolean | no | `true` | Auto-start on first `/acp` |
| `startupTimeoutMs` | integer | no | `10000` | Startup timeout in ms |
| `requestTimeoutMs` | integer | no | `60000` | Per-request timeout in ms |
| `capabilities` | object | no | `{}` | Server capability hints |
| `description` | string | no | `""` | Human-readable description |

### Key Design Decisions

- **Project-only config.** No global `<home>/.agentao/acp.json` — ACP servers are project-scoped.
- **No auto-send.** Messages are never automatically routed to ACP servers. Use `/acp send` explicitly.
- **ACP responses stay separate.** Server output appears in the ACP inbox, not in the main Agentao conversation context.
- **Lazy initialization.** The ACP manager is created on first `/acp` command, not at startup.

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

When an ACP server needs user input (permission confirmation or free-form text), it sends a notification that becomes a **pending interaction**. These appear in the inbox and can be resolved via CLI commands:

```
/acp approve planner abc123     # Grant permission
/acp reject planner abc123      # Deny permission
/acp reply planner abc123 main  # Reply with text
```

Pending interactions are visible in `/acp status <name>` and `/status`.

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
- Use `/acp approve` / `/acp reject` / `/acp reply` promptly.

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
