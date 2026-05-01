# 7.2 蓝图 B · IDE / 编辑器插件

::: tip ⚡ 端到端可跑
**产出** —— VS Code 扩展启动 `agentao --acp --stdio` 子进程，在编辑器里看到 Agent 流式输出、工具审批、`session/load` 恢复。
**技术栈** —— TypeScript · VS Code Extension API · 走 stdio 的 ACP JSON-RPC 2.0 · NDJSON 帧。
**源代码** —— [`examples/ide-plugin-ts/`](https://github.com/jin-bo/agentao/tree/main/examples/ide-plugin-ts)
**运行** —— `npm install && npm run compile`，再在 VS Code 里按 **F5** 启动扩展宿主。
:::

**场景**：你在做一个 VS Code / Zed / JetBrains / Neovim 插件，想给编辑器加"跟代码库对话"的能力。你希望进程隔离（每个 workspace 一个 Agentao），语言无关的胶水（插件可能是 TypeScript），并且 IDE 重启后能接着对话。

## 谁 & 为什么

- **产品形态**：用 TS / Kotlin / Rust 写的编辑器扩展
- **用户**：希望在 IDE 里直接获得 agentic 辅助的开发者
- **为什么用 ACP 而不是 SDK**：插件运行时不是 Python；每个 workspace 一个子进程能带来干净的隔离和崩溃遏制

## 架构

```
IDE 主进程
   │
   ├─ 扩展宿主 (Node / JVM / Rust)
   │    │
   │    ▼
   │   ACPClient  (stdio JSON-RPC)
   │    │  ▲
   │    ▼  │
   │   子进程: `agentao --acp --stdio`
   │      ├─ working_directory = workspace 根目录
   │      ├─ MCP: filesystem + git (从 .agentao/mcp.json 自动加载)
   │      └─ Skills: 仓库约定 (从 .agentao/skills/)
   │
   └─ UI: 聊天面板、行内建议、diff 审核
```

## 关键代码（TypeScript 客户端）

### 1 · 启动 & 初始化

```ts
// acp-client.ts
import { spawn, ChildProcessWithoutNullStreams } from "node:child_process";
import readline from "node:readline";

export class ACPClient {
  private proc: ChildProcessWithoutNullStreams;
  private rl: readline.Interface;
  private nextId = 1;
  private pending = new Map<number, (r: any) => void>();
  private notifHandlers = new Map<string, (p: any) => void>();

  constructor(workspaceRoot: string) {
    this.proc = spawn("agentao", ["--acp", "--stdio"], {
      cwd: workspaceRoot,
      env: { ...process.env, AGENTAO_WORKING_DIRECTORY: workspaceRoot },
    });
    this.rl = readline.createInterface({ input: this.proc.stdout });
    this.rl.on("line", (line) => this.handleLine(line));
    this.proc.stderr.on("data", (d) => console.error("[agentao]", d.toString()));
  }

  async initialize(): Promise<any> {
    return this.request("initialize", {
      protocolVersion: 1,
      clientCapabilities: { fs: { readFile: true, writeFile: true } },
    });
  }

  async newSession(cwd: string): Promise<string> {
    const r = await this.request("session/new", { cwd, mcpServers: [] });
    return r.sessionId;
  }

  async prompt(sessionId: string, text: string): Promise<any> {
    return this.request("session/prompt", {
      sessionId,
      prompt: [{ type: "text", text }],
    });
  }

  async cancel(sessionId: string): Promise<void> {
    await this.request("session/cancel", { sessionId });
  }

  onNotification(method: string, handler: (p: any) => void) {
    this.notifHandlers.set(method, handler);
  }

  private request(method: string, params: any): Promise<any> {
    return new Promise((resolve, reject) => {
      const id = this.nextId++;
      this.pending.set(id, (msg) => {
        if (msg.error) reject(Object.assign(new Error(msg.error.message), msg.error));
        else resolve(msg.result);
      });
      this.proc.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
    });
  }

  private handleLine(line: string) {
    const msg = JSON.parse(line);
    if (msg.id !== undefined && this.pending.has(msg.id)) {
      this.pending.get(msg.id)!(msg);
      this.pending.delete(msg.id);
    } else if (msg.method) {
      const h = this.notifHandlers.get(msg.method);
      if (h) h(msg.params);
    }
  }
}
```

### 2 · 接进 VS Code 聊天面板

```ts
// extension.ts
import * as vscode from "vscode";
import { ACPClient } from "./acp-client";

export async function activate(ctx: vscode.ExtensionContext) {
  const ws = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!ws) return;

  const client = new ACPClient(ws);
  await client.initialize();
  const sessionId = await client.newSession(ws);

  client.onNotification("session/update", (p) => {
    const { update } = p;
    if (update.sessionUpdate === "agent_message_chunk") {
      chatPanel.append(update.content.text);
    } else if (update.sessionUpdate === "tool_call_start") {
      chatPanel.showToolSpinner(update.toolCall);
    }
  });

  client.onNotification("session/request_permission", async (p) => {
    const tool = p.toolCall.toolName;
    const pick = await vscode.window.showQuickPick(
      ["本次允许", "始终允许", "拒绝"],
      { placeHolder: `Agentao 想要执行 ${tool}` }
    );
    return { outcome: pick === "拒绝" ? { outcome: "cancelled" }
                                      : { outcome: "selected", optionId: "allow" } };
  });

  ctx.subscriptions.push(
    vscode.commands.registerCommand("agentao.ask", async () => {
      const q = await vscode.window.showInputBox({ prompt: "问 Agentao" });
      if (q) await client.prompt(sessionId, q);
    }),
    vscode.commands.registerCommand("agentao.cancel", () => client.cancel(sessionId)),
  );
}
```

### 3 · IDE 重启后的会话恢复

ACP 的 `session/load`（由 `initialize` 里的 `loadSession: true` 宣告）允许你在重启后用同一个 `sessionId`，Agentao 会把历史重新注入 `agent.messages`。

```ts
// 启动时
const saved = ctx.globalState.get<string>("agentao.sessionId");
const sessionId = saved
  ? (await client.request("session/load", { sessionId: saved }), saved)
  : await client.newSession(ws);
ctx.globalState.update("agentao.sessionId", sessionId);
```

## 插件自带的配置文件

在 workspace 根目录放 `.agentao/acp.json`，用户无需改扩展代码就能定制：

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]
    }
  },
  "permissions": {
    "mode": "WORKSPACE_WRITE",
    "rules": [
      { "tool": "run_shell_command", "action": "ask" }
    ]
  }
}
```

## ⚠️ 陷阱

::: warning IDE 插件真实部署中的 Day-2 bug
下面每一行都是一次真实的生产事故。**上线前先扫一遍**——现在改便宜，事后查代价大。
:::

| 上线第二天的 bug | 根因 | 修法 |
|------------------|------|------|
| 崩溃后插件一直卡住 | 子进程 SIGKILL 后成为孤儿 | 监听 `exit` / `stderr close`，指数退避重启 |
| 超长 stdout 行让 readline 崩溃 | NDJSON 帧超出默认缓冲 | `readline.createInterface({ input, crlfDelay: Infinity })` + 调大上限 |
| 权限弹窗堆积 | 用户回复到一半又取消了，级联没做干净 | `session/cancel` **会** 把所有未答复的权限请求以 `cancelled` 拒绝 |
| 工具参数里带路径穿越 | 调了工作区外的路径 | 依赖 `working_directory` 锁定（6.4 黄金规则） |
| 多根 workspace | 一个 ACPClient 不能服务两个根 | 每个根启一个子进程 |

## 可运行代码

完整项目就在主仓 [`examples/ide-plugin-ts/`](https://github.com/jin-bo/agentao/tree/main/examples/ide-plugin-ts)——参考本页顶部的 "运行此例" 链接。

```bash
cd examples/ide-plugin-ts
npm install && npm run compile
# 在 VS Code 里打开此目录，按 F5 启动 extension host
```

---

→ [7.3 工单自动化](./3-ticket-automation)
