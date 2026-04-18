/**
 * Minimal ACP client for driving `agentao --acp --stdio` from a VS Code
 * extension (or any Node host). Three-loop architecture: one reader, one
 * writer, main logic calls request() / respond().
 *
 * This file is the runnable companion to Part 3.3.4 of the developer guide.
 */
import {
  spawn,
  ChildProcessByStdio,
} from "node:child_process";
import { Readable, Writable } from "node:stream";
import readline from "node:readline";

type JsonRpcId = number | string;
type JsonObject = Record<string, unknown>;

export interface ACPClientOptions {
  command?: string;      // default: "agentao"
  args?: string[];       // default: ["--acp", "--stdio"]
  cwd: string;           // workspace root
  env?: NodeJS.ProcessEnv;
  rpcTimeoutMs?: number; // default: 60_000
}

export class ACPClient {
  private proc!: ChildProcessByStdio<Writable, Readable, Readable>;
  private nextId = 1;
  private pending = new Map<JsonRpcId, {
    resolve: (msg: JsonObject) => void;
    reject: (err: Error) => void;
    timer: ReturnType<typeof setTimeout>;
  }>();
  private notifHandlers = new Map<string, (params: JsonObject) => void>();
  private serverReqHandler?: (method: string, params: JsonObject) => Promise<JsonObject>;
  private rpcTimeoutMs: number;

  constructor(private readonly opts: ACPClientOptions) {
    this.rpcTimeoutMs = opts.rpcTimeoutMs ?? 60_000;
  }

  async start(): Promise<void> {
    const command = this.opts.command ?? "agentao";
    const args = this.opts.args ?? ["--acp", "--stdio"];
    this.proc = spawn(command, args, {
      cwd: this.opts.cwd,
      env: this.opts.env ?? process.env,
      stdio: ["pipe", "pipe", "pipe"],
    });

    const rl = readline.createInterface({
      input: this.proc.stdout,
      crlfDelay: Infinity,
    });
    rl.on("line", (line) => this.dispatch(line));

    this.proc.stderr.on("data", (chunk: Buffer) => {
      process.stderr.write(`[agentao] ${chunk.toString()}`);
    });

    this.proc.on("exit", (code, signal) => {
      const err = new Error(
        `agentao subprocess exited (code=${code}, signal=${signal})`,
      );
      for (const { reject, timer } of this.pending.values()) {
        clearTimeout(timer);
        reject(err);
      }
      this.pending.clear();
    });

    await this.call("initialize", {
      protocolVersion: 1,
      clientCapabilities: {},
      clientInfo: { name: "agentao-ide-plugin-example", version: "0.1.0" },
    });
  }

  async newSession(cwd: string, mcpServers: unknown[] = []): Promise<string> {
    const r = await this.call("session/new", { cwd, mcpServers });
    return (r as { sessionId: string }).sessionId;
  }

  async loadSession(sessionId: string, cwd: string, history: unknown[] = []): Promise<void> {
    await this.call("session/load", { sessionId, cwd, history });
  }

  async prompt(sessionId: string, text: string): Promise<JsonObject> {
    return this.call("session/prompt", {
      sessionId,
      prompt: [{ type: "text", text }],
    });
  }

  async cancel(sessionId: string): Promise<void> {
    await this.call("session/cancel", { sessionId });
  }

  onNotification(method: string, handler: (params: JsonObject) => void): void {
    this.notifHandlers.set(method, handler);
  }

  onServerRequest(handler: (method: string, params: JsonObject) => Promise<JsonObject>): void {
    this.serverReqHandler = handler;
  }

  async close(): Promise<void> {
    this.proc.stdin.end();
    await new Promise<void>((resolve) => this.proc.once("exit", () => resolve()));
  }

  // ──────────────────────────────────────────────────────────────────────

  call(method: string, params: JsonObject): Promise<JsonObject> {
    return new Promise((resolve, reject) => {
      const id = this.nextId++;
      const timer = setTimeout(() => {
        if (this.pending.delete(id)) {
          reject(new Error(`rpc timeout: ${method}`));
        }
      }, this.rpcTimeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      this.send({ jsonrpc: "2.0", id, method, params });
    });
  }

  private async dispatch(line: string): Promise<void> {
    if (!line.trim()) return;
    let msg: JsonObject;
    try {
      msg = JSON.parse(line) as JsonObject;
    } catch {
      console.error("bad json from agent:", line);
      return;
    }

    const hasId = "id" in msg && msg.id !== undefined && msg.id !== null;
    const hasMethod = typeof msg.method === "string";

    if (hasId && !hasMethod) {
      const entry = this.pending.get(msg.id as JsonRpcId);
      if (entry) {
        this.pending.delete(msg.id as JsonRpcId);
        clearTimeout(entry.timer);
        if (msg.error) {
          entry.reject(
            Object.assign(
              new Error((msg.error as { message?: string }).message ?? "rpc error"),
              msg.error as object,
            ),
          );
        } else {
          entry.resolve((msg.result as JsonObject) ?? {});
        }
      }
    } else if (hasMethod && !hasId) {
      const h = this.notifHandlers.get(msg.method as string);
      if (h) h((msg.params as JsonObject) ?? {});
    } else if (hasMethod && hasId) {
      let response: JsonObject;
      try {
        if (!this.serverReqHandler) {
          response = { result: { outcome: { outcome: "cancelled" } } };
        } else {
          response = await this.serverReqHandler(
            msg.method as string,
            (msg.params as JsonObject) ?? {},
          );
        }
      } catch (err) {
        response = { error: { code: -32603, message: String(err) } };
      }
      this.send({ jsonrpc: "2.0", id: msg.id as JsonRpcId, ...response });
    }
  }

  private send(msg: JsonObject): void {
    this.proc.stdin.write(JSON.stringify(msg) + "\n");
  }
}
