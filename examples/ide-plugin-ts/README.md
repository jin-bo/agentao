# Blueprint B · IDE Plugin (VS Code + ACP)

A minimal VS Code extension that drives `agentao --acp --stdio` as a workspace-scoped subprocess. Reference implementation of the "three-loop" ACP client architecture from the developer guide.

Corresponds to [Part 7.2 of the developer guide](../../developer-guide/en/part-7/2-ide-plugin) and the TypeScript client in [Part 3.3.4](../../developer-guide/en/part-3/3-host-client-architecture).

## What it demonstrates

- **`ACPClient` class** — spawn `agentao`, line-oriented stdio reader, request/response correlation with timeouts, notification dispatch, server→client request handling
- **VS Code integration** — two commands (`Agentao: Ask`, `Agentao: Cancel`), streaming output channel, modal permission prompts
- **Session persistence** — `sessionId` saved to `ctx.globalState` and restored via `session/load` on next activation

## How to compile

```bash
npm install
npm run compile       # or: npm run typecheck for no-emit
```

Expected: zero TypeScript errors, `out/` directory created.

## How to run inside VS Code

1. Open this directory in VS Code
2. Ensure `agentao` is on your `PATH` (`uv tool install agentao` or run from this repo with `uv run agentao`)
3. Press **F5** — a new Extension Development Host window opens
4. Open any folder as a workspace in the new window
5. Command Palette → `Agentao: Ask` → type a question
6. The "Agentao" output channel streams the reply; permission prompts appear as modal dialogs

## File map

| Path | Role |
|------|------|
| `src/acp-client.ts` | Stand-alone `ACPClient` — usable in any Node host, not just VS Code |
| `src/extension.ts` | VS Code activation entry: spawn, restore session, register commands |
| `package.json` | Extension manifest + dev deps |
| `tsconfig.json` | Compiles `src/*.ts` → `out/*.js` |

## Not included

- Marketplace publishing (needs `vsce`, icon, category wiring — follow VS Code's own guide)
- Webview chat panel — this example uses the Output channel for brevity; the pattern for a custom webview is routine VS Code work
- Multi-root workspace support — the sample picks the first `workspaceFolder`; spawn one `ACPClient` per root for real multi-root support
- Crash-restart supervisor — production plugins should reconnect with exponential backoff when the subprocess exits
