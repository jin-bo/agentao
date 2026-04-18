# 3.3 Host as ACP Client — Architecture

[3.2](./2-agentao-as-server) showed the wire protocol. This section is about **the machinery you build around it**: subprocess lifecycle, stdio I/O loops, concurrency, permission-UI bridging, and the cross-language shape of it all. Any language that can spawn subprocesses and read/write stdio can implement this — examples below include Node/TypeScript and Go.

## 3.3.1 The six concerns of an ACP host

A production ACP client has to handle:

| Concern | What you need to implement |
|---------|----------------------------|
| **Process lifecycle** | Spawn, health-check, crash-restart, graceful shutdown |
| **Framing** | NDJSON decode on stdout, NDJSON encode on stdin, **never mix stderr/stdout** |
| **Dispatch** | Route incoming JSON by shape: response vs. notification vs. server→client request |
| **Matching** | Correlate responses back to the `id` of the request that sent them |
| **UI bridge** | Show `session/request_permission` prompts, route replies back |
| **Observability** | Log every RPC; tail the agent's `agentao.log` for deep debug |

You can skip none of these in production.

## 3.3.2 The three threads (or coroutines)

Whichever language you pick, the minimal structure is three loops running concurrently:

```
┌──────────────────────────────────────────────────────────┐
│  Host process                                             │
│                                                            │
│  ┌───────────────┐   ┌───────────────┐   ┌─────────────┐ │
│  │ Reader thread │   │ Writer thread │   │ Main logic  │ │
│  │               │   │               │   │             │ │
│  │ parse stdout  │   │ send requests │   │ call(),     │ │
│  │ → dispatch    │   │ from queue    │   │ handle UI   │ │
│  └───────────────┘   └───────────────┘   └─────────────┘ │
│         │                    ▲                 │         │
│         │     response map   │                 │         │
│         └──────────┬─────────┘                 │         │
│                    │                           │         │
│               notifications ◄──────────────────┘         │
└──────────────────────────────────────────────────────────┘
                     ▲ stdio
                     │
           agentao --acp --stdio (subprocess)
```

Why three? Because you **cannot** block the reader while handling a message — the agent's next notification is already arriving. Decouple:

- **Reader**: single-threaded, does nothing but parse one NDJSON line and push it onto an in-memory dispatcher
- **Writer**: single-threaded, drains a send queue into stdin (single writer avoids interleaving)
- **Main logic**: calls `call(method, params)` which blocks on a future/promise, drives the UI, etc.

## 3.3.3 Dispatch by shape

JSON-RPC 2.0 messages come in three shapes; dispatch logic is identical in every language:

```python
def dispatch(msg: dict) -> None:
    has_id     = "id" in msg
    has_method = "method" in msg

    if has_id and not has_method:
        # RESPONSE — complete the pending request future
        future = pending.pop(msg["id"])
        future.set_result(msg)

    elif has_method and not has_id:
        # NOTIFICATION — route to event handler, never reply
        handle_notification(msg)

    elif has_method and has_id:
        # SERVER → CLIENT REQUEST — respond with result or error
        handle_server_request(msg)    # must eventually send a response
```

The agent sends server→client requests only in two cases today:

- `session/request_permission` — tool approval
- `_agentao.cn/ask_user` — free-form question (extension)

Every one of these **must** be answered, or the agent blocks forever waiting. Enforce this with a timer.

## 3.3.4 TypeScript / Node reference implementation

```typescript
// acp-client.ts
import { spawn, ChildProcessByStdio } from "node:child_process";
import { Readable, Writable } from "node:stream";
import readline from "node:readline";

type JsonRpcId = number | string;
type JsonObject = Record<string, any>;

export class ACPClient {
  private proc!: ChildProcessByStdio<Writable, Readable, Readable>;
  private nextId = 1;
  private pending = new Map<JsonRpcId, (msg: JsonObject) => void>();

  onNotification: (msg: JsonObject) => void = () => {};
  onServerRequest: (msg: JsonObject) => Promise<JsonObject> =
    async () => ({ result: { outcome: { outcome: "cancelled" } } });

  async start() {
    this.proc = spawn("agentao", ["--acp", "--stdio"], {
      stdio: ["pipe", "pipe", "pipe"],
    });

    const rl = readline.createInterface({ input: this.proc.stdout });
    rl.on("line", (line) => this.dispatch(line));

    this.proc.stderr.on("data", (chunk) => {
      // agentao writes logs here; forward to your logger
      process.stderr.write(`[agentao] ${chunk}`);
    });

    await this.call("initialize", {
      protocolVersion: 1,
      clientCapabilities: {},
      clientInfo: { name: "my-host", version: "0.1.0" },
    });
  }

  private async dispatch(line: string) {
    if (!line.trim()) return;
    let msg: JsonObject;
    try { msg = JSON.parse(line); }
    catch (e) { console.error("bad json from agent:", line); return; }

    if ("id" in msg && !("method" in msg)) {
      const resolve = this.pending.get(msg.id);
      if (resolve) { this.pending.delete(msg.id); resolve(msg); }
    } else if ("method" in msg && !("id" in msg)) {
      this.onNotification(msg);
    } else if ("method" in msg && "id" in msg) {
      const response = await this.onServerRequest(msg);
      this.send({ jsonrpc: "2.0", id: msg.id, ...response });
    }
  }

  call(method: string, params: JsonObject): Promise<JsonObject> {
    return new Promise((resolve, reject) => {
      const id = this.nextId++;
      this.pending.set(id, resolve);
      setTimeout(() => {
        if (this.pending.delete(id)) reject(new Error(`rpc timeout: ${method}`));
      }, 60_000);
      this.send({ jsonrpc: "2.0", id, method, params });
    });
  }

  private send(msg: JsonObject) {
    this.proc.stdin.write(JSON.stringify(msg) + "\n");
  }

  async close() {
    this.proc.stdin.end();
    await new Promise((r) => this.proc.on("exit", r));
  }
}
```

Usage in a VS Code extension:

```typescript
const acp = new ACPClient();
acp.onNotification = (msg) => {
  if (msg.method === "session/update") {
    updateWebviewStream(msg.params);     // push to UI
  }
};
acp.onServerRequest = async (msg) => {
  if (msg.method === "session/request_permission") {
    const ok = await vscode.window.showWarningMessage(
      msg.params.toolCall.title, { modal: true }, "Allow", "Reject",
    );
    return { result: { outcome: { outcome: "selected", optionId: ok === "Allow" ? "allow_once" : "reject_once" } } };
  }
  return { result: { outcome: { outcome: "cancelled" } } };
};
await acp.start();
const { result: { sessionId } } = await acp.call("session/new", { cwd: workspaceFolder });
await acp.call("session/prompt", {
  sessionId, prompt: [{ type: "text", text: "list files" }],
});
```

A fuller VS Code integration appears in [Blueprint B](/en/part-7/2-ide-plugin).

## 3.3.5 Go reference implementation (skeleton)

```go
// acpclient.go
package acpclient

import (
    "bufio"
    "encoding/json"
    "io"
    "os/exec"
    "sync"
)

type Client struct {
    cmd     *exec.Cmd
    stdin   io.WriteCloser
    stdout  *bufio.Scanner
    nextID  int
    pending map[int]chan json.RawMessage
    mu      sync.Mutex

    OnNotification func(method string, params json.RawMessage)
    OnServerReq    func(method string, params json.RawMessage) json.RawMessage
}

func Start() (*Client, error) {
    cmd := exec.Command("agentao", "--acp", "--stdio")
    stdin, _ := cmd.StdinPipe()
    stdout, _ := cmd.StdoutPipe()
    if err := cmd.Start(); err != nil { return nil, err }

    c := &Client{
        cmd: cmd, stdin: stdin,
        stdout:  bufio.NewScanner(stdout),
        pending: make(map[int]chan json.RawMessage),
    }
    go c.readerLoop()
    return c, nil
}

func (c *Client) readerLoop() {
    for c.stdout.Scan() {
        line := c.stdout.Bytes()
        var head struct {
            ID     *int            `json:"id"`
            Method *string         `json:"method"`
            Params json.RawMessage `json:"params"`
        }
        if err := json.Unmarshal(line, &head); err != nil { continue }

        switch {
        case head.ID != nil && head.Method == nil: // response
            c.mu.Lock()
            ch := c.pending[*head.ID]
            delete(c.pending, *head.ID)
            c.mu.Unlock()
            if ch != nil { ch <- line }
        case head.Method != nil && head.ID == nil: // notification
            if c.OnNotification != nil { c.OnNotification(*head.Method, head.Params) }
        case head.Method != nil && head.ID != nil: // server→client request
            result := c.OnServerReq(*head.Method, head.Params)
            c.sendRaw(map[string]any{"jsonrpc": "2.0", "id": *head.ID, "result": result})
        }
    }
}

func (c *Client) Call(method string, params any) (json.RawMessage, error) {
    c.mu.Lock()
    id := c.nextID; c.nextID++
    ch := make(chan json.RawMessage, 1)
    c.pending[id] = ch
    c.mu.Unlock()
    c.sendRaw(map[string]any{"jsonrpc": "2.0", "id": id, "method": method, "params": params})
    return <-ch, nil
}

func (c *Client) sendRaw(msg any) {
    b, _ := json.Marshal(msg)
    c.stdin.Write(append(b, '\n'))
}
```

Plug any UI (TUI, web, native) into `OnNotification` and `OnServerReq`.

## 3.3.6 Permission UI bridge — the shape of it

`session/request_permission` is the trickiest part because it's **synchronous from the agent's view**: the agent blocks until you respond, yet you have to collect user input asynchronously through your UI.

### Common pattern

```
agent ──req (id=X)──▶ reader thread ──enqueue──▶ UI thread
                                                     │
                                                     ▼
                                           show modal, await click
                                                     │
                                                     ▼
UI thread ──response (id=X)──▶ writer thread ──▶ agent
```

### Must-haves

- **Display the tool details**: `toolCall.title`, `toolCall.kind`, `rawInput` so users know what they're approving
- **Return quickly**: the agent holds tool execution. Timeouts keep it from stalling
- **Respect `options`**: the agent tells you the allowed reply values (`allow_once`, `reject_once`, …). Don't invent your own
- **If the user closes the window**: respond `{"outcome":{"outcome":"cancelled"}}`, not silently

See [Appendix C.6](/en/appendix/c-acp-messages#c-6-session-request-permission) for the full schema.

## 3.3.7 Error handling & reconnection

ACP servers can die. Your host must cope:

| Failure | How you'll notice | What to do |
|---------|-------------------|------------|
| Process exits with non-zero code | `exit` event on child process | Surface error, don't auto-restart unless you capture why |
| stdout EOF / stream closed | Reader loop sees EOF | Mark all pending requests as failed; don't leak their futures |
| Unresponsive (no reply in N seconds) | Per-request timer fires | Fail the specific request; decide whether to also `session/cancel` |
| `initialize` returns a lower version than you sent | Response payload | Agentao echoes `protocolVersion=1`; if you sent `2`, accept the downgrade |
| JSON parse error | Reader catches exception | Log loud; drop the line (don't crash) |

Restart strategy: avoid auto-restart during development (makes bugs invisible). In production, exponential backoff + circuit-breaker. Each restart spawns a fresh ACP process; existing `sessionId`s are lost unless you use `session/load` during reconnection.

## 3.3.8 Observability checklist

- Log every outgoing request with its `id`, `method`, and `params` (truncate large payloads)
- Log every incoming response with its `id` and a `duration_ms`
- Surface `session/update` streams in a debug panel
- Tail `<session_cwd>/agentao.log` for the agent side — that's where stack traces live
- Alert on: RPC timeout rate, process restart count, permission-prompt timeout

---

Next: [3.4 Reverse: calling external ACP agents →](./4-reverse-acp-call)
