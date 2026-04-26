# 3.2 Agentao as an ACP Server

Drive Agentao as a black-box ACP server — the integration path for hosts in **any language** (Node, Go, Rust, Kotlin, Swift, C#…).

## Launch command

```bash
agentao --acp --stdio
```

- `--acp` enables ACP mode
- `--stdio` declares the transport (v1 supports stdio only, but the flag is required)
- The process runs until stdin closes or `SIGTERM` is received

**Environment variables**: same as CLI / SDK (`OPENAI_API_KEY`, etc.). The ACP layer does not transport credentials.

**Logs**: Agentao writes to `<session-cwd>/agentao.log` (the `cwd` from the handshake). Hosts can tail / inspect it for debugging.

## Full method catalog

### Host → Agent (5 request methods)

| Method | Purpose | Key params |
|--------|---------|------------|
| `initialize` | Handshake + capability negotiation | `protocolVersion:int, clientCapabilities:obj, clientInfo?:obj` |
| `session/new` | Start a new session | `cwd:string, mcpServers?:array` |
| `session/prompt` | Send one user turn | `sessionId:string, prompt:array<PromptChunk>` |
| `session/cancel` | Cancel an in-flight prompt | `sessionId:string` |
| `session/load` | Restore from history | `sessionId:string, history:array` |

### Agent → Host (1 request, 1 notification, 1 extension)

| Method | Kind | Purpose |
|--------|------|---------|
| `session/update` | Notification | Streamed events: text chunks, thinking, tool-call status |
| `session/request_permission` | Request (needs client response) | Ask to approve a risky tool (file write, shell, etc.) |
| `_agentao.cn/ask_user` | Request (extension) | Ask the user a free-form question |

## Handshake `initialize`

**Request**:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": 1,
    "clientCapabilities": {},
    "clientInfo": {
      "name": "my-ide",
      "version": "1.0.0"
    }
  }
}
```

**Agentao response**:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": 1,
    "agentCapabilities": {
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
    },
    "authMethods": [],
    "agentInfo": {
      "name": "agentao",
      "title": "Agentao",
      "version": "0.2.14"
    },
    "extensions": [
      {
        "method": "_agentao.cn/ask_user",
        "description": "Request free-form text input from the user."
      }
    ]
  }
}
```

### Rules

- `protocolVersion` must be an **integer** (`"1"` as string or `True` are rejected)
- Version negotiation: if Agentao supports your version, it echoes it back; otherwise it returns its highest supported version. **Never errors** — the client decides whether to continue
- `clientCapabilities` is required as an object (can be `{}`)

## Session creation `session/new`

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "session/new",
  "params": {
    "cwd": "/path/to/user/project",
    "mcpServers": [
      {
        "name": "github",
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": [{"name":"GITHUB_TOKEN","value":"<secret>"}]
      }
    ]
  }
}
```

**Response**:

```json
{"jsonrpc":"2.0","id":2,"result":{"sessionId":"sess-a1b2c3"}}
```

### Notes

- `cwd` sets this session's **working directory** — file tools, `AGENTAO.md`, `.agentao/` all resolve against it. Keep it unique per concurrent session
- `mcpServers` only accepts `"type":"stdio"` or `"type":"sse"` (because `mcpCapabilities.http=false`)
- Internally these map to the [`extra_mcp_servers` constructor param](/en/part-2/2-constructor-reference#session-scoped-mcp-servers)

## Sending a prompt `session/prompt`

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "session/prompt",
  "params": {
    "sessionId": "sess-a1b2c3",
    "prompt": [
      {"type": "text", "text": "Find the 3 largest .py files"}
    ]
  }
}
```

In v1, `prompt` array entries may only be `{"type":"text", "text": ...}`.

**Final response** (returned after all streaming updates complete):

```json
{
  "jsonrpc":"2.0",
  "id":3,
  "result":{
    "stopReason":"end_turn"
  }
}
```

`stopReason` values: `end_turn` (normal), `max_tokens`, `cancelled`, `refusal`, `error`, etc.

## Streaming updates `session/update` (notification)

Before `session/prompt` returns, Agentao emits **many** notifications like:

```json
{
  "jsonrpc":"2.0",
  "method":"session/update",
  "params":{
    "sessionId":"sess-a1b2c3",
    "update":{
      "sessionUpdate":"agent_message_chunk",
      "content":{"type":"text","text":"Let me help"}
    }
  }
}
```

Key `sessionUpdate` values:

| Value | Meaning |
|-------|---------|
| `agent_message_chunk` | Streamed text chunk |
| `agent_thought_chunk` | Thinking/reasoning (when enabled) |
| `tool_call` | Tool invocation started |
| `tool_call_update` | Tool progress/output |

Hosts must **not** respond to notifications (JSON-RPC 2.0: notifications have no `id`).

## Tool confirmation `session/request_permission` (request)

When Agentao attempts a `requires_confirmation=True` tool:

```json
{
  "jsonrpc":"2.0",
  "id":42,
  "method":"session/request_permission",
  "params":{
    "sessionId":"sess-a1b2c3",
    "toolCall":{
      "toolCallId":"call-x",
      "status":"pending",
      "title":"Run: rm -rf build/",
      ...
    },
    "options":[
      {"optionId":"allow_once","name":"Allow once","kind":"allow_once"},
      {"optionId":"reject_once","name":"Reject","kind":"reject_once"}
    ]
  }
}
```

**The host must respond** (otherwise the agent blocks until timeout):

```json
{
  "jsonrpc":"2.0",
  "id":42,
  "result":{
    "outcome":{"outcome":"selected","optionId":"allow_once"}
  }
}
```

Recommended host UI flow:

1. On request → show a modal with `title` and `toolCall` details
2. On user choice → respond immediately with `result`
3. On timeout → respond with `{"outcome":"cancelled"}` and optionally send `session/cancel`

## Cancellation `session/cancel`

```json
{"jsonrpc":"2.0","id":99,"method":"session/cancel","params":{"sessionId":"sess-a1b2c3"}}
```

Effect: the in-flight `session/prompt` turn finishes with `stopReason:"cancelled"`. Idempotent — repeat calls don't error.

## Restoring sessions `session/load`

For **persistent session** scenarios: store `sessionId` + history in your DB, restore after a process restart. Agentao advertises `loadSession:true` in the handshake.

```json
{
  "jsonrpc":"2.0",
  "id":5,
  "method":"session/load",
  "params":{
    "sessionId":"sess-restored",
    "cwd":"/path/to/project",
    "history":[
      {"role":"user","content":[{"type":"text","text":"previous question"}]},
      {"role":"assistant","content":[{"type":"text","text":"previous answer"}]}
    ]
  }
}
```

## Common pitfalls

1. **Framing**: NDJSON always — **no raw newlines inside a JSON object**. Use your JSON library's compact mode.
2. **Stdout pollution**: Agentao routes all logs to `agentao.log` + stderr, never stdout. Your client reads pure JSON from stdout.
3. **Stdin backpressure**: if the client ignores stdout after sending a request, the server's stdout buffer will fill. Use async I/O or a dedicated reader thread.
4. **Version field typing**: must be `int` (see above).
5. **Don't reply to `session/update`**: JSON-RPC 2.0 prohibits responses to notifications.

## End-to-end minimal client (Python, for demo)

Even if your host isn't Python, this snippet clarifies the wire flow:

```python
"""Minimal ACP client (Python) driving agentao --acp --stdio."""
import json, subprocess, threading, queue, uuid

class AcpClient:
    def __init__(self):
        self.proc = subprocess.Popen(
            ["agentao", "--acp", "--stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            bufsize=0, text=True,
        )
        self._pending: dict = {}
        self._notifications: queue.Queue = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for line in self.proc.stdout:
            msg = json.loads(line)
            if "id" in msg and "method" not in msg:        # response
                fut = self._pending.pop(msg["id"], None)
                if fut: fut.put(msg)
            elif "method" in msg and "id" not in msg:       # notification
                self._notifications.put(msg)
            elif "method" in msg and "id" in msg:           # server → client request
                self._notifications.put(msg)

    def call(self, method, params):
        id_ = str(uuid.uuid4())
        fut: queue.Queue = queue.Queue(maxsize=1)
        self._pending[id_] = fut
        msg = {"jsonrpc":"2.0","id":id_,"method":method,"params":params}
        self.proc.stdin.write(json.dumps(msg) + "\n"); self.proc.stdin.flush()
        return fut.get()

    def respond(self, id_, result):
        msg = {"jsonrpc":"2.0","id":id_,"result":result}
        self.proc.stdin.write(json.dumps(msg) + "\n"); self.proc.stdin.flush()

# Usage
cli = AcpClient()
print(cli.call("initialize", {"protocolVersion":1,"clientCapabilities":{}}))
r = cli.call("session/new", {"cwd":"/tmp"})
sid = r["result"]["sessionId"]
# Listen to notifications asynchronously while sending prompts
cli.call("session/prompt", {
    "sessionId": sid,
    "prompt":[{"type":"text","text":"List 3 largest files"}],
})
```

Production-grade patterns (error handling, UI bridging, timeouts) live in [3.3](#) (coming soon).

## Key source locations

| Topic | File |
|-------|------|
| Launch entrypoint | `agentao/cli/entrypoints.py:254-389` |
| Protocol constants | `agentao/acp/protocol.py:18, 47-58` |
| Handshake | `agentao/acp/initialize.py` |
| Capability block | `agentao/acp/initialize.py:53-76` |
| Session creation | `agentao/acp/session_new.py` |
| Prompt handling | `agentao/acp/session_prompt.py` |

→ [Part 4 · Event Layer & UI Integration](/en/part-4/) (coming soon)
