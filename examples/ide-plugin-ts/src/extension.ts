/**
 * VS Code extension entry point — drives `agentao --acp --stdio` per workspace.
 *
 * Commands:
 *   agentao.ask     — prompt the agent with a one-line input
 *   agentao.cancel  — cancel the current turn
 *
 * On startup, restores the previous `sessionId` from globalState if available
 * so conversations survive IDE restarts (via `session/load`).
 */
import * as vscode from "vscode";

import { ACPClient } from "./acp-client";

let client: ACPClient | undefined;
let sessionId: string | undefined;
const output = vscode.window.createOutputChannel("Agentao");

export async function activate(ctx: vscode.ExtensionContext): Promise<void> {
  const ws = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!ws) {
    output.appendLine("no workspace open; extension idle");
    return;
  }

  client = new ACPClient({ cwd: ws });
  await client.start();

  const saved = ctx.globalState.get<string>("agentao.sessionId");
  if (saved) {
    try {
      await client.loadSession(saved, ws, []);
      sessionId = saved;
      output.appendLine(`restored session ${saved}`);
    } catch (err) {
      output.appendLine(`session/load failed (${err}); starting fresh`);
      sessionId = await client.newSession(ws);
    }
  } else {
    sessionId = await client.newSession(ws);
  }
  await ctx.globalState.update("agentao.sessionId", sessionId);
  output.appendLine(`session ready: ${sessionId}`);

  client.onNotification("session/update", (params) => {
    const update = (params as { update?: { sessionUpdate?: string; content?: { text?: string }; toolCall?: unknown } }).update;
    if (!update) return;
    switch (update.sessionUpdate) {
      case "agent_message_chunk":
        output.append(update.content?.text ?? "");
        break;
      case "tool_call":
        output.appendLine(`\n[tool_call] ${JSON.stringify(update.toolCall)}`);
        break;
      case "tool_call_update":
        output.appendLine(`[tool_call_update] ${JSON.stringify(update.toolCall)}`);
        break;
      default:
        break;
    }
  });

  client.onServerRequest(async (method, params) => {
    if (method === "session/request_permission") {
      const toolCall = (params as { toolCall?: { title?: string } }).toolCall;
      const title = toolCall?.title ?? "unknown tool";
      const pick = await vscode.window.showWarningMessage(
        `Agentao wants to run: ${title}`,
        { modal: true },
        "Allow once",
        "Reject",
      );
      const optionId = pick === "Allow once" ? "allow_once" : "reject_once";
      return {
        result: {
          outcome:
            pick === undefined
              ? { outcome: "cancelled" }
              : { outcome: "selected", optionId },
        },
      };
    }
    return { result: { outcome: { outcome: "cancelled" } } };
  });

  ctx.subscriptions.push(
    vscode.commands.registerCommand("agentao.ask", async () => {
      if (!client || !sessionId) return;
      const q = await vscode.window.showInputBox({ prompt: "Ask Agentao" });
      if (!q) return;
      output.show(true);
      output.appendLine(`\n> ${q}\n`);
      try {
        await client.prompt(sessionId, q);
      } catch (err) {
        output.appendLine(`[error] ${err}`);
      }
    }),
    vscode.commands.registerCommand("agentao.cancel", async () => {
      if (!client || !sessionId) return;
      await client.cancel(sessionId);
      output.appendLine("[cancel sent]");
    }),
    {
      dispose: () => {
        client?.close().catch(() => undefined);
      },
    },
  );
}

export function deactivate(): Thenable<void> | undefined {
  return client?.close();
}
