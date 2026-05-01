# 7.2 Blueprint B · IDE / Editor Plugin

::: tip ⚡ Runnable end-to-end
**Outcome** — VS Code extension that spawns `agentao --acp --stdio` as a subprocess and shows the agent's streaming output, tool approvals, and `session/load` restore in the editor.
**Stack** — TypeScript · VS Code Extension API · ACP JSON-RPC 2.0 over stdio · NDJSON framing.
**Source** — [`examples/ide-plugin-ts/`](https://github.com/jin-bo/agentao/tree/main/examples/ide-plugin-ts)
**Run** — `npm install && npm run compile`, then press **F5** in VS Code to launch the extension host.
:::

**Scenario**: you're building a VS Code / Zed / JetBrains / Neovim plugin that adds "chat with your codebase" to the editor. You want process isolation (one Agentao per workspace), language-agnostic glue (your plugin might be TypeScript), and the ability to resume a conversation after the IDE restarts.

## Who & why

- **Product shape**: editor extension written in TS / Kotlin / Rust
- **Users**: developers who want agentic assistance inside their IDE
- **Why ACP not SDK**: the plugin runtime is not Python; one subprocess per workspace gives you clean isolation and crash containment

## Architecture

```
IDE main process
   │
   ├─ Extension host (Node / JVM / Rust)
   │    │
   │    ▼
   │   ACPClient  (stdio JSON-RPC)
   │    │  ▲
   │    ▼  │
   │   subprocess: `agentao --acp --stdio`
   │      ├─ working_directory = workspace root
   │      ├─ MCP: filesystem + git (auto-loaded from .agentao/mcp.json)
   │      └─ Skills: repo conventions (from .agentao/skills/)
   │
   └─ UI: chat panel, inline suggestions, diff review
```

## Key code (TypeScript client)

### 1 · Spawn & initialize

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

### 2 · Wire to the VS Code chat panel

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
      ["Allow once", "Allow always", "Deny"],
      { placeHolder: `Agentao wants to run ${tool}` }
    );
    return { outcome: pick === "Deny" ? { outcome: "cancelled" }
                                      : { outcome: "selected", optionId: "allow" } };
  });

  ctx.subscriptions.push(
    vscode.commands.registerCommand("agentao.ask", async () => {
      const q = await vscode.window.showInputBox({ prompt: "Ask Agentao" });
      if (q) await client.prompt(sessionId, q);
    }),
    vscode.commands.registerCommand("agentao.cancel", () => client.cancel(sessionId)),
  );
}
```

### 3 · Persist + resume across IDE restart

ACP's `session/load` (advertised by `loadSession: true` in `initialize`) lets you hand the same `sessionId` back after a restart. Agentao will replay stored history into `agent.messages`.

```ts
// on startup
const saved = ctx.globalState.get<string>("agentao.sessionId");
const sessionId = saved
  ? (await client.request("session/load", { sessionId: saved }), saved)
  : await client.newSession(ws);
ctx.globalState.update("agentao.sessionId", sessionId);
```

## Configuration file the plugin ships with

Drop `.agentao/acp.json` at workspace root so users can customize without touching extension code:

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

## ⚠️ Pitfalls

::: warning Day-2 bugs from real IDE-plugin deployments
Each row below is a real production incident. Skim them before you ship — the fixes are cheap *now* and expensive *later*.
:::

| Day-2 bug | Root cause | Fix |
|-----------|------------|-----|
| Plugin hangs after crash | Child process orphaned on SIGKILL | On `exit`, `stderr close`, restart with exponential backoff |
| Huge stdout line crashes `readline` | NDJSON frame > default buffer | Use `readline.createInterface({ input, crlfDelay: Infinity })` + raise max |
| Permission prompts stack up | User clicked a pending ask mid-reply; cancel didn't cascade | `session/cancel` **rejects** all outstanding permission requests with `cancelled` |
| Path traversal via tool args | Tool called on a path outside workspace | Rely on `working_directory` pin (6.4 golden rule) |
| Multi-root workspaces | Single ACPClient can't service two roots | Spawn one subprocess per root |

## Runnable code

The full project lives in-repo at [`examples/ide-plugin-ts/`](https://github.com/jin-bo/agentao/tree/main/examples/ide-plugin-ts) — see the top-of-page "Run this example" link.

```bash
cd examples/ide-plugin-ts
npm install && npm run compile
# Open this directory in VS Code, then press F5 to launch the extension host
```

---

→ [7.3 Ticket Automation](./3-ticket-automation)
