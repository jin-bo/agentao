# 3.3 宿主作为 ACP 客户端 — 架构

[3.2](./2-agentao-as-server) 讲了线协议。本节讲**围绕它要搭的那套机器**：子进程生命周期、stdio IO 回路、并发、权限 UI 桥接，以及跨语言的形态。任何能起子进程 + 读写 stdio 的语言都能做——下面给了 Node/TypeScript 和 Go 的示例。

## 3.3.1 一个 ACP 宿主的六件事

生产级 ACP 客户端必须处理：

| 关切 | 要实现的事 |
|-----|-----------|
| **进程生命周期** | 启动、健康检查、崩溃重启、优雅关停 |
| **帧** | stdout 端解 NDJSON、stdin 端编 NDJSON、**stderr 与 stdout 不能混** |
| **分发** | 根据 JSON 形状路由：response / notification / server→client request |
| **匹配** | 把 response 关联回当初那个 request 的 `id` |
| **UI 桥接** | 展示 `session/request_permission` 提示，把结果回传 |
| **观测** | 每个 RPC 都记日志；需要深挖时 tail agent 的 `agentao.log` |

生产环境一个都不能省。

## 3.3.2 三条线程（或协程）

不论什么语言，最小结构都是三个并发回路：

```
┌──────────────────────────────────────────────────────────┐
│  宿主进程                                                 │
│                                                            │
│  ┌───────────────┐   ┌───────────────┐   ┌─────────────┐ │
│  │ 读线程        │   │ 写线程        │   │ 主逻辑      │ │
│  │               │   │               │   │             │ │
│  │ 解 stdout     │   │ 从队列取请求  │   │ call()、     │ │
│  │ → 分发         │   │ 写 stdin      │   │ 处理 UI      │ │
│  └───────────────┘   └───────────────┘   └─────────────┘ │
│         │                    ▲                 │         │
│         │     response 表     │                 │         │
│         └──────────┬─────────┘                 │         │
│                    │                           │         │
│             notifications ◄────────────────────┘         │
└──────────────────────────────────────────────────────────┘
                     ▲ stdio
                     │
           agentao --acp --stdio（子进程）
```

为什么是三个？因为处理消息时**不能**阻塞读端——agent 的下一条通知已经来了。要解耦：

- **读**：单线程，只做"解一条 NDJSON 然后塞给 dispatcher"
- **写**：单线程，把发送队列排空到 stdin（单写者避免交叉）
- **主逻辑**：调 `call(method, params)`（阻塞于 future/promise），驱动 UI，等等

## 3.3.3 按"形状"分发

JSON-RPC 2.0 消息有三种形状；分发逻辑在任何语言里都一样：

```python
def dispatch(msg: dict) -> None:
    has_id     = "id" in msg
    has_method = "method" in msg

    if has_id and not has_method:
        # RESPONSE —— 完成待定请求的 future
        future = pending.pop(msg["id"])
        future.set_result(msg)

    elif has_method and not has_id:
        # NOTIFICATION —— 路由给事件处理器，永不回复
        handle_notification(msg)

    elif has_method and has_id:
        # SERVER → CLIENT REQUEST —— 必须回 result 或 error
        handle_server_request(msg)
```

今天 agent 只会在两种情况下发出 server→client 请求：

- `session/request_permission` —— 工具审批
- `_agentao.cn/ask_user` —— 自由问答（扩展）

**每一个**都必须答复，不然 agent 会一直等。用定时器兜底。

## 3.3.4 TypeScript / Node 参考实现

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
      // agentao 的日志在这里；转给你的 logger
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

在 VS Code 插件里用：

```typescript
const acp = new ACPClient();
acp.onNotification = (msg) => {
  if (msg.method === "session/update") {
    updateWebviewStream(msg.params);     // 推到 UI
  }
};
acp.onServerRequest = async (msg) => {
  if (msg.method === "session/request_permission") {
    const ok = await vscode.window.showWarningMessage(
      msg.params.toolCall.title, { modal: true }, "允许", "拒绝",
    );
    return { result: { outcome: { outcome: "selected", optionId: ok === "允许" ? "allow_once" : "reject_once" } } };
  }
  return { result: { outcome: { outcome: "cancelled" } } };
};
await acp.start();
const { result: { sessionId } } = await acp.call("session/new", { cwd: workspaceFolder });
await acp.call("session/prompt", {
  sessionId, prompt: [{ type: "text", text: "列出文件" }],
});
```

更完整的 VS Code 集成见[蓝图 B](/zh/part-7/2-ide-plugin)。

## 3.3.5 Go 参考实现（骨架）

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

把任意 UI（TUI、Web、原生）接进 `OnNotification` 和 `OnServerReq`。

## 3.3.6 权限 UI 桥接 — 大致形态

`session/request_permission` 是最棘手的一块，因为它**对 agent 来说是同步的**：agent 阻塞等你回复，而你得在 UI 里异步收集用户选择。

### 通用模式

```
agent ──req (id=X)──▶ 读线程 ──enqueue──▶ UI 线程
                                            │
                                            ▼
                                    弹模态，等点击
                                            │
                                            ▼
UI 线程 ──response (id=X)──▶ 写线程 ──▶ agent
```

### 必须具备

- **展示工具详情**：`toolCall.title`、`toolCall.kind`、`rawInput`，让用户知道自己在批什么
- **快速回复**：agent 的工具执行在等你。定时器防止它卡住
- **尊重 `options`**：agent 会告诉你允许的回复值（`allow_once`、`reject_once`…）。别自造
- **用户关窗**：回 `{"outcome":{"outcome":"cancelled"}}`，不要默不作声

完整 schema 见[附录 C.6](/zh/appendix/c-acp-messages#c-6-session-request-permission)。

## 3.3.7 错误处理与重连

ACP server 会死。宿主必须扛得住：

| 故障 | 怎么察觉 | 怎么处理 |
|------|---------|---------|
| 进程非零退出 | 子进程 `exit` 事件 | 抛错上浮；别在不知道为什么时自动重启 |
| stdout EOF / 流关闭 | 读回路收到 EOF | 把所有 pending 请求标记失败；不要泄漏它们的 future |
| 无响应（N 秒没回） | 每请求定时器 | 让那条请求失败；看要不要顺带发 `session/cancel` |
| `initialize` 返回了更低版本 | response payload | Agentao 会回 `protocolVersion=1`；你发了 `2` 也要接受降级 |
| JSON 解析失败 | 读端异常 | 日志大声喊；丢掉那行（别崩） |

重启策略：开发期**别**自动重启（bug 会被掩盖）。生产上指数退避 + 熔断器。每次重启起一个新的 ACP 进程；已有 `sessionId` 会丢——除非重连时用 `session/load` 恢复。

## 3.3.8 观测自查清单

- 每条 outgoing 请求记 `id`、`method`、`params`（大 payload 截断）
- 每条 incoming 响应记 `id` + `duration_ms`
- 在调试面板展示 `session/update` 流
- 需要深挖时 tail `<session_cwd>/agentao.log`——stack trace 在那边
- 告警：RPC 超时率、进程重启次数、权限弹窗超时率

---

下一节：[3.4 反向：调外部 ACP agent →](./4-reverse-acp-call)
