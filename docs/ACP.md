# Agent Client Protocol (ACP) Support

Agentao implements a stdio-based [Agent Client Protocol](https://github.com/zed-industries/agent-client-protocol) server so ACP-compatible clients (e.g. Zed) can drive Agentao as their agent runtime. This document covers what ships, how to launch it, and the explicit limits of the v1 implementation.

ACP support landed across `docs/implementation/acp-issues/01` through `14`. Tests live in `tests/test_acp_*.py`. Version examples below track the current release line (`0.2.12` as of this document revision).

---

## Quick Start

### Launch

```bash
# Console script
agentao --acp --stdio

# Module form (works without the console script on PATH)
python -m agentao --acp --stdio
```

Both commands block reading newline-delimited JSON-RPC 2.0 messages from `stdin`, write responses and notifications to `stdout`, and route logs + any stray `print` to `stderr`. Press Ctrl-D (or close stdin) to shut the server down cleanly.

### Smoke test by hand

```bash
OPENAI_API_KEY=sk-... agentao --acp --stdio <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{}}}
{"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"/tmp","mcpServers":[]}}
EOF
```

Expected — two NDJSON response envelopes on stdout:

```json
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":1,"agentCapabilities":{"loadSession":true,"promptCapabilities":{"image":false,"audio":false,"embeddedContext":false},"mcpCapabilities":{"http":false,"sse":true}},"authMethods":[],"agentInfo":{"name":"agentao","title":"Agentao","version":"0.2.10"}}}
{"jsonrpc":"2.0","id":2,"result":{"sessionId":"sess_<32hex>"}}
```

> **Note:** `agentInfo.version` in the response is sourced from
> `agentao.__version__`, so the literal above tracks whatever release
> line you have installed — it is illustrative, not a pinned value.

### From a real ACP client

A reference Zed configuration (`<home>/.config/zed/settings.json`):

```json
{
  "agent_servers": {
    "Agentao": {
      "command": "agentao",
      "args": ["--acp", "--stdio"],
      "env": {
        "OPENAI_API_KEY": "sk-..."
      }
    }
  }
}
```

The same shape works for any client that launches an ACP agent over stdio: pass `--acp --stdio` and an environment with whatever provider key Agentao needs (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.).

---

## Supported Methods

### Client → server (requests Agentao handles)

| Method | Status | Notes |
|---|---|---|
| `initialize` | ✅ | Echoes the client's `protocolVersion` if supported (currently `1`); falls back to ours otherwise. Records `clientCapabilities` per connection. |
| `session/new` | ✅ | Creates a fresh session bound to a per-session `cwd` and (optionally) per-session MCP servers. Returns `{"sessionId": "sess_…"}`. |
| `session/prompt` | ✅ | Runs one Agentao turn against the named session; returns `{"stopReason": "end_turn" \| "cancelled"}`. |
| `session/cancel` | ✅ | Fires the session's active `CancellationToken`. Idempotent; no-op on closed sessions or sessions with no active turn. Accepted both as a notification (no `id`) and as a request. |
| `session/load` | ✅ | Reuses `agentao/session.py`'s persistence layer, hydrates the runtime's message history, and replays each persisted message as a `session/update` notification before responding. |

### Server → client (sent by Agentao)

| Method | Direction | Notes |
|---|---|---|
| `session/update` | notification | Streams turn output: text, thinking, tool calls, sub-agent markers. See [Event Mapping](#event-mapping) below. |
| `session/request_permission` | request | Sent when a tool with `requires_confirmation=True` is about to run. Blocks the turn until the client responds. See [Permissions](#permissions) below. |

### Capabilities Advertised in `initialize`

Source: `agentao/acp/initialize.py`.

```jsonc
{
  "loadSession": true,
  "promptCapabilities": {
    "image": false,
    "audio": false,
    "embeddedContext": false
  },
  "mcpCapabilities": {
    "http": false,
    "sse": true
  }
}
```

`authMethods` is `[]` — Agentao does not implement ACP-level auth in v1. Provider credentials (`OPENAI_API_KEY`, etc.) are read from the launch environment and never travel through the ACP wire.

---

## Scope and Limitations

ACP defines a large surface area; v1 deliberately implements a working subset. Anything in the **Not in v1** column is rejected explicitly rather than silently degraded — clients see a JSON-RPC error or a documented capability flag set to `false`, not unexpected behavior.

### Transport

| Feature | Status | Notes |
|---|---|---|
| stdio | ✅ | The only supported transport in v1. NDJSON framing (one compact JSON object per line). |
| WebSocket / TCP | ❌ | Not in v1. The `--stdio` flag exists for future-proofing; passing it without `--acp` is rejected with exit code 2 so a typo doesn't fall through to interactive mode. |

### `session/prompt` content blocks

Source: `agentao/acp/session_prompt.py::_parse_prompt`.

| Block type | Status | Notes |
|---|---|---|
| `text` | ✅ | Multiple text blocks are joined with `\n\n`. |
| `resource_link` | ✅ | Rendered as `[Resource: {title or name or uri}]({uri})` so the LLM sees the reference; Agentao does **not** dereference the URI in v1. |
| `image` | ❌ | `INVALID_PARAMS`. `promptCapabilities.image` is `false`. |
| `audio` | ❌ | `INVALID_PARAMS`. `promptCapabilities.audio` is `false`. |
| `resource` (embedded) | ❌ | `INVALID_PARAMS`. `promptCapabilities.embeddedContext` is `false`. |

### Client-host capability routing

Per the [ACP epic](implementation/ACP_GITHUB_EPIC.md) non-goals:

| Feature | Status | Notes |
|---|---|---|
| `fs/read_text_file` / `fs/write_text_file` proxy | ❌ | Agentao always reads and writes files **locally** in the session's `cwd`. The client's `fs` capability flags are recorded on the session for future use but not consulted. |
| `terminal/*` proxy | ❌ | Shell commands run locally via Agentao's existing `run_shell_command` tool, not via an ACP terminal session. |
| MCP-over-ACP extension | ❌ | Use `mcpServers` in `session/new` instead — that's the supported injection point. |

### MCP server injection (`session/new` → `mcpServers`)

Source: `agentao/acp/mcp_translate.py`.

| Transport in entry | Status | Notes |
|---|---|---|
| stdio (`{name, command, args, env}`) | ✅ | Translated to Agentao's internal `{command, args, env: {…}}` config. Always created with `trust: false`. |
| sse (`{type:"sse", name, url, headers}`) | ✅ | `mcpCapabilities.sse` is `true`. Headers translated from `[{name,value}]` to `{name: value}`. |
| http (`{type:"http", …}`) | ❌ | Dropped silently with a warning log. `mcpCapabilities.http` is `false` because `agentao/mcp/client.py` only ships `sse_client`, not `streamable_http_client`. |

ACP-provided MCP servers **override** any same-named entries in the project's `.agentao/mcp.json`. They are **session-scoped** — they are torn down when the session closes and never leak to sibling sessions.

### Session management

| Behavior | Status | Notes |
|---|---|---|
| Per-session `cwd` | ✅ | Each session sees its own working directory; the `Agentao` runtime is constructed with `working_directory=cwd` so memory db, AGENTAO.md lookup, MCP config lookup, and file/shell tools all resolve against it. |
| Per-session `agent.messages` history | ✅ | Two sessions on the same server have independent conversations. |
| Per-session permission overrides | ✅ | `allow_always`/`reject_always` decisions are stored on `AcpSessionState.permission_overrides` and never leak to sibling sessions. Cleared on session close. |
| Concurrent prompts on different sessions | ✅ | Issue 08's `ThreadPoolExecutor(max_workers=8)` lets handlers run in parallel; verified end-to-end in `tests/test_acp_multi_session.py::TestTurnLockIsolation`. |
| Concurrent prompts on the **same** session | ❌ | Per-session `turn_lock` is acquired non-blocking; a second concurrent prompt for the same session returns `INVALID_REQUEST`. Queueing was rejected as a DoS footgun. |
| Reload an already-active session id | ❌ | `session/load` for a sessionId already in the registry is rejected with `INVALID_REQUEST`. Cancel and tear down before reloading. |

### `session/prompt` stop reasons

Source: `agentao/acp/session_prompt.py`.

| `stopReason` | Meaning |
|---|---|
| `end_turn` | The Agentao chat loop returned normally. |
| `cancelled` | The session's `CancellationToken` was fired (via `session/cancel`, connection close, or session teardown) before the loop returned. |

ACP defines additional stop reasons (`max_tokens`, `max_turn_requests`, `refusal`) that v1 does not surface — `agent.chat()` currently returns a string without structured termination metadata. Adding them is a follow-up, not a v1 promise.

---

## Architecture Overview

```
                   ┌────────────────────────────────────────────────┐
                   │                AcpServer (server.py)            │
                   │                                                 │
       stdin ───►  │   read loop ──┐                                 │
                   │                │                                │
                   │                ├─► classify request/response    │
                   │                │                                │
                   │                ▼                                │
                   │   ┌─ handler dispatch ─ ThreadPoolExecutor(8) ─┐│
                   │   │                                            ││
                   │   │  initialize  ── initialize.py              ││
                   │   │  session/new  ─ session_new.py             ││
                   │   │  session/prompt session_prompt.py          ││
                   │   │  session/cancel session_cancel.py          ││
                   │   │  session/load   session_load.py            ││
                   │   │                                            ││
                   │   └────┬───────────────────┬───────────────────┘│
                   │        │                   │                    │
                   │        │ writes via        │ server.call(...)   │
                   │        │ write_lock        │ (session/request_  │
                   │        │                   │  permission)       │
                   │        ▼                   ▼                    │
       stdout ◄────┤   JSON-RPC envelopes / notifications            │
                   │                                                 │
                   │   _pending_requests ◄── route response (read    │
                   │   (server → client)     loop fills + wakes)     │
                   │                                                 │
                   │   sessions: AcpSessionManager (session_manager) │
                   │     │                                           │
                   │     ├─ AcpSessionState ─ Agentao runtime        │
                   │     ├─ AcpSessionState ─ Agentao runtime        │
                   │     └─ ...                                      │
                   │                                                 │
       stderr ◄────┤   sys.stdout reassigned to sys.stderr; logger   │
                   │   handler installed on the agentao package      │
                   └────────────────────────────────────────────────┘
```

### Stdout hygiene

When `AcpServer` is constructed with no explicit `stdout`/`stdin` (the production launch path), it:

1. **Captures** the real `sys.stdout` into a private handle that all JSON-RPC writes use.
2. **Reassigns** `sys.stdout = sys.stderr` so any stray `print()` anywhere in the process — application code, third-party libraries, the LLM client's debug output — lands on stderr.
3. **Installs** a `StreamHandler(sys.stderr)` on the `agentao` package logger if no handler is attached yet, so logs are visible from the moment the server starts (before `LLMClient` would normally configure logging).

The acceptance criterion *"stdout contains only ACP messages"* is enforced by `tests/test_acp_cli_entrypoint.py::TestAcpSubprocessSmoke::test_logs_go_to_stderr_not_stdout`.

### Concurrent dispatch

The dispatcher is a `ThreadPoolExecutor(max_workers=8)`. This is required because `transport.confirm_tool` blocks waiting for a `session/request_permission` response — and **that response itself arrives on the read loop**. A synchronous dispatcher would deadlock the moment a tool needed confirmation.

The shutdown sequence (in `AcpServer.run`'s `finally` clause) is order-sensitive:

1. **Cancel pending server→client requests first.** Any worker stuck inside `transport.confirm_tool` wakes up and returns `False` (deterministic "tool rejected").
2. **Drain the executor** with `shutdown(wait=True)`. Workers that just unblocked complete the current request.
3. **Tear down sessions** (`AcpSessionManager.close_all`). Each session's MCP connections disconnect and active turns get their cancel token tripped.

Reversing steps 1 and 2 deadlocks. There's a comment in `server.py:327` and a regression test in `tests/test_acp_request_permission.py` to enforce this.

### File layout

```
agentao/acp/
├── __init__.py
├── __main__.py            # `python -m agentao.acp` entry; main() registers all handlers
├── protocol.py            # METHOD_*, ACP_PROTOCOL_VERSION, error code constants
├── server.py              # AcpServer + JsonRpcHandlerError + concurrent dispatch
├── session_manager.py     # AcpSessionManager: thread-safe registry, close_all
├── models.py              # JsonRpcRequest/Response/Error, AcpSessionState, AcpConnectionState
├── transport.py           # ACPTransport: emit() event mapping + confirm_tool() permission flow
├── mcp_translate.py       # ACP {name, command, args, env} → Agentao internal MCP config
├── initialize.py          # initialize handler + AGENT_CAPABILITIES
├── session_new.py         # session/new handler + agent_factory DI seam
├── session_prompt.py      # session/prompt handler + ContentBlock parser
├── session_cancel.py      # session/cancel handler (idempotent)
└── session_load.py        # session/load handler + history hydration + replay
```

The CLI wiring (`agentao --acp --stdio`) lives in `agentao/cli/entrypoints.py::run_acp_mode` and `agentao/__main__.py`; both delegate to `agentao.acp.__main__.main`.

---

## Annotated NDJSON Transcript

Below is a complete client→server→client conversation. Each line on the wire is exactly one JSON object terminated by `\n`.

```jsonc
// 1. Handshake — required before any session/* method.
→ {"jsonrpc":"2.0","id":1,"method":"initialize","params":{
    "protocolVersion":1,
    "clientCapabilities":{"fs":{"readTextFile":true,"writeTextFile":true},"terminal":true},
    "clientInfo":{"name":"my-acp-client","version":"0.1.0"}
  }}

← {"jsonrpc":"2.0","id":1,"result":{
    "protocolVersion":1,
    "agentCapabilities":{
      "loadSession":true,
      "promptCapabilities":{"image":false,"audio":false,"embeddedContext":false},
      "mcpCapabilities":{"http":false,"sse":true}
    },
    "authMethods":[],
    // agentInfo.version is sourced from agentao.__version__ — tracks the installed release line.
    "agentInfo":{"name":"agentao","title":"Agentao","version":"0.2.10"}
  }}

// 2. Open a session bound to a working directory.
→ {"jsonrpc":"2.0","id":2,"method":"session/new","params":{
    "cwd":"<project-root>",
    "mcpServers":[]
  }}

← {"jsonrpc":"2.0","id":2,"result":{"sessionId":"sess_3a8f1b2c..."}}

// 3. Send a prompt. Agentao runs one chat() turn, streaming events as
//    session/update notifications, then returns a final stopReason.
→ {"jsonrpc":"2.0","id":3,"method":"session/prompt","params":{
    "sessionId":"sess_3a8f1b2c...",
    "prompt":[{"type":"text","text":"List the python files at the repo root."}]
  }}

// While the turn runs, the server emits notifications (no id, method = session/update).
// Each is a one-line NDJSON envelope. Order is preserved per session.
← {"jsonrpc":"2.0","method":"session/update","params":{
    "sessionId":"sess_3a8f1b2c...",
    "update":{
      "sessionUpdate":"agent_message_chunk",
      "content":{"type":"text","text":"I'll list the Python files now.\n"}
    }
  }}

← {"jsonrpc":"2.0","method":"session/update","params":{
    "sessionId":"sess_3a8f1b2c...",
    "update":{
      "sessionUpdate":"tool_call",
      "toolCallId":"call_<12hex>",
      "title":"glob",
      "kind":"search",
      "status":"pending",
      "rawInput":{"pattern":"*.py"}
    }
  }}

← {"jsonrpc":"2.0","method":"session/update","params":{
    "sessionId":"sess_3a8f1b2c...",
    "update":{
      "sessionUpdate":"tool_call_update",
      "toolCallId":"call_<12hex>",
      "status":"completed"
    }
  }}

← {"jsonrpc":"2.0","method":"session/update","params":{
    "sessionId":"sess_3a8f1b2c...",
    "update":{
      "sessionUpdate":"agent_message_chunk",
      "content":{"type":"text","text":"Found main.py, setup.py, and 12 files in agentao/.\n"}
    }
  }}

// Final response to the session/prompt request.
← {"jsonrpc":"2.0","id":3,"result":{"stopReason":"end_turn"}}

// 4. Optional: cancel an in-flight turn (from a different request id while
//    a session/prompt is still streaming notifications). Acceptable as a
//    notification (no id) or as a request that returns null.
→ {"jsonrpc":"2.0","method":"session/cancel","params":{"sessionId":"sess_3a8f1b2c..."}}

// 5. EOF on stdin → clean shutdown. AcpServer.run() returns; the process
//    exits with code 0 after every session has been torn down.
```

---

## Event Mapping

`ACPTransport.emit` (in `agentao/acp/transport.py`) is the single source of truth for how Agentao runtime events become ACP `session/update` notifications. The full table:

| Agentao `EventType` | ACP `sessionUpdate` | Notes |
|---|---|---|
| `TURN_START` | (silent) | Returns `None`; no notification written. |
| `LLM_TEXT` | `agent_message_chunk` | `content` is a single text block carrying the chunk. |
| `THINKING` | `agent_thought_chunk` | Same shape as `agent_message_chunk` but a different `sessionUpdate` so clients can render reasoning differently. |
| `TOOL_CONFIRMATION` | (silent) | Confirmations go via `session/request_permission` (server→client request), not `session/update`. |
| `TOOL_START` | `tool_call` | `status: "pending"`, `kind` mapped from tool name (`read`, `edit`, `search`, `execute`, `fetch`, …), `rawInput` is the JSON-safe argument dict. |
| `TOOL_OUTPUT` | `tool_call_update` | `status: "in_progress"`, `content` appends one text entry with the chunk. |
| `TOOL_COMPLETE` | `tool_call_update` | `status: "completed"` for `ok`, `"failed"` for `error` or `cancelled` (ACP has no cancelled status for tool calls — only for turns via `stopReason`). |
| `AGENT_START` | `agent_thought_chunk` | Sub-agent start marker `[sub-agent started: <name>] <task>`. |
| `AGENT_END` | `agent_thought_chunk` | Sub-agent end marker `[sub-agent finished: <name> (<state>, <N> turns)]`. |
| `ERROR` | `agent_message_chunk` | Prefixed with `Error: `. |

Failures inside `emit()` are logged and swallowed — a misbehaving client or a JSON-safety slip cannot interrupt an in-progress turn.

### History replay (`session/load`)

When `session/load` runs, `ACPTransport.replay_history` walks the persisted message list and emits one notification per entry **before** responding to the load request. The mapping is intentionally a 1:1 walk (no chunking) and `<system-reminder>` blocks are stripped from replayed user messages so internal date/plan-mode reminders don't leak to the client:

| Persisted role | ACP `sessionUpdate` |
|---|---|
| `system` | (skipped) |
| `user` | `user_message_chunk` |
| `assistant` (text) | `agent_message_chunk` |
| `assistant` (with `tool_calls`) | one `tool_call` per call, `status: "completed"` |
| `tool` (result) | `tool_call_update`, `status: "completed"` |

ACP clients that wait for the load response before sending the next prompt will therefore observe the full replayed history before any new turn.

---

## Permissions

When a tool with `requires_confirmation=True` is about to run, `ACPTransport.confirm_tool` sends a server→client `session/request_permission` request and **blocks** until the client responds. The four option ids Agentao always offers:

| `optionId` | Outcome |
|---|---|
| `allow_once` | Allow this single tool call. Next call to the same tool re-prompts. |
| `allow_always` | Allow this tool for the rest of the **session**. Stored in `AcpSessionState.permission_overrides[tool_name] = True`; subsequent calls short-circuit without a round trip. |
| `reject_once` | Reject this single tool call. |
| `reject_always` | Reject this tool for the rest of the session. Stored as `False`; subsequent calls also short-circuit. |

Per-session overrides are **never** shared across sessions and are cleared on session close — see `tests/test_acp_multi_session.py::TestPermissionOverrideIsolation` for the isolation regression tests.

### Failure modes (all resolve to "tool rejected")

- Connection drops while waiting → `PendingRequestCancelled` → `False`
- Client returns a JSON-RPC error response → `False` (logged at error level)
- Wait times out → `False`
- Malformed outcome shape → `False`

A crashing confirmation path would propagate through `chat()` and crash the turn with an unhelpful traceback, so `confirm_tool` is defensively robust.

---

## Cancellation

`session/cancel` fires the session's active `CancellationToken`. The token is bound to the session by `session/prompt` immediately before calling `agent.chat()` and cleared in a `finally` block. The cancellation propagates to:

- The LLM call (via `cancellation_token.is_cancelled` polling between iterations)
- Tool execution (the same token is passed into `ToolRunner`)
- Sub-agents (they share the parent's token)

The handler is **idempotent** and **silent on no-ops**:

| Situation | Behavior |
|---|---|
| Session is closed | Silent no-op (logged) |
| Session has no active turn (`cancel_token is None`) | Silent no-op |
| Token already cancelled | Silent no-op |
| Unknown sessionId | `INVALID_REQUEST` (request mode); silently dropped (notification mode) |

After cancellation, the still-running `session/prompt` returns `{"stopReason": "cancelled"}` once the chat loop's next poll observes the token. End-to-end coverage lives in `tests/test_acp_session_cancel.py::TestEndToEndCancel`.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Client never sees a response to `initialize` | Client is sending pretty-printed JSON across multiple lines | NDJSON requires one compact JSON object per line. Each newline ends a message. |
| `session/new` returns `SERVER_NOT_INITIALIZED` (-32002) | `initialize` was not called, or returned an error | Send `initialize` first and check the response for an `error` field. |
| `session/new` returns `INVALID_PARAMS` (-32602) for `cwd` | `cwd` is not absolute, doesn't exist, or is a file | Pass an absolute path to an existing directory. The check is in `session_new.py::_parse_cwd`. |
| `session/prompt` returns `INVALID_REQUEST` "session already has an active turn" | A second `session/prompt` arrived while the first is still running | Wait for the first turn's response before sending the next, or use a different session id. |
| `session/prompt` with image/audio block returns `INVALID_PARAMS` | Those block types are intentionally not supported in v1 | Use only `text` and `resource_link` blocks. The capability flags in `initialize` advertise this. |
| Server hangs forever waiting for `session/request_permission` | Client is not handling server→client requests | Check that the client routes incoming requests with `srv_*` ids back as JSON-RPC responses. |
| Process exits with garbage on stdout | Some library is calling `print()` and you constructed `AcpServer` with explicit streams | The stdout guard only installs when `AcpServer()` is constructed with no `stdin`/`stdout` arguments. Use `agentao --acp --stdio` for production launches; the test path passes streams explicitly to avoid mutating global state. |
| `python -m agentao --acp --stdio`: `No module named agentao.__main__` | Pre-v0.2.6 install | Upgrade — `agentao/__main__.py` ships from v0.2.6. |

---

## For Contributors

### Tests

Each issue's tests live in a dedicated file. The ACP suite has expanded significantly since the initial v0.2.6 rollout; rely on the current CI or local `pytest` output for exact pass counts rather than the historical numbers from older release notes.

| File | Issue | Coverage focus |
|---|---|---|
| `test_acp_protocol.py` | 01 | NDJSON framing, error code constants |
| `test_acp_session_manager.py` | 03 | Registry create/get/require/delete, `close_all` |
| `test_acp_initialize.py` | 02 | Handshake, capability negotiation, version echo |
| `test_acp_session_new.py` | 04, 05 | `cwd` validation, `mcpServers` parsing, factory DI, capability snapshot |
| `test_acp_session_prompt.py` | 06 | ContentBlock parsing, turn lock, stop reason, end-to-end wire |
| `test_acp_transport.py` | 07 | `emit()` event mapping for every `EventType` |
| `test_acp_request_permission.py` | 08 | Pending registry, server→client `call()`, all 4 option ids, override scoping |
| `test_acp_session_cancel.py` | 09 | Idempotency, no-op paths, end-to-end cancel of an in-flight turn |
| `test_acp_session_load.py` | 10 | History replay mapping, registry collision, hydration before replay |
| `test_acp_mcp_injection.py` | 11 | Translation table for stdio/sse/http, env/headers, per-session isolation |
| `test_acp_cli_entrypoint.py` | 12 | Argparse routing, `--acp` precedence, subprocess smoke tests, stdout hygiene |
| `test_acp_multi_session.py` | 13 | Cross-session invariants: registry/cwd/lock/cancel/permission/messages isolation |

Run them all with:

```bash
uv run python -m pytest tests/test_acp_ -v
```

### Implementation issue specs

The `docs/implementation/acp-issues/` directory holds one Markdown spec per issue (01–14). Each spec includes the design decisions, scope vs. limits, and acceptance criteria as they were defined when the issue shipped — read the spec for the issue you're modifying before changing the code.

The umbrella epic is `docs/implementation/ACP_GITHUB_EPIC.md`, which lists goals, non-goals, and risks for the v1 milestone.

### Adding a new method handler

The pattern (mirrored across `initialize.py`, `session_new.py`, `session_prompt.py`, `session_cancel.py`, `session_load.py`):

1. Define the handler: `def handle_<method>(server, params, *, <deps>) -> dict:`
2. Validate params: raise `TypeError` for shape errors → `-32602`; raise `JsonRpcHandlerError(code, message)` for everything else.
3. Add a `register(server, *, <deps>)` helper that wires the handler into `server._handlers` via `server.register(METHOD_X, lambda params: handle_<method>(server, params, ...))`.
4. Wire `register(server)` into `agentao/acp/__main__.py::main()` so `python -m agentao --acp --stdio` picks it up.
5. Write tests that exercise both the unit-level handler and the end-to-end wire path. Use a `FakeAgent` factory injected via the same `agent_factory` kwarg `session_new` exposes — it lets the test avoid pulling in the LLM stack.
