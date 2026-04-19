# 3.5 Zed / IDE Integration Walkthrough

Zed's external-agent feature speaks ACP natively — meaning Agentao drops into Zed with no custom glue. The same pattern applies to any IDE / editor that supports external ACP agents. This section shows the end-to-end wiring.

## 3.5.1 What "ACP integration" means in an IDE

An IDE that implements the ACP client side can:

1. Launch any ACP server as a subprocess
2. Route user chat to `session/prompt`
3. Render `session/update` notifications as a scrolling chat UI
4. Pop `session/request_permission` as a modal
5. Forward editor context (open files, selection) via `cwd` + prompt text

The user just adds your agent in the IDE's settings — everything else is transport.

## 3.5.2 Registering Agentao with Zed

Zed reads its agent config from `~/.config/zed/settings.json` (or the UI's **Settings → Agents**). Add an entry that points to `agentao --acp --stdio`:

```json
{
  "agents": [
    {
      "name": "agentao",
      "command": "agentao",
      "args": ["--acp", "--stdio"],
      "env": {
        "OPENAI_API_KEY": "sk-...",
        "OPENAI_MODEL": "gpt-5.4"
      }
    }
  ]
}
```

Restart Zed, open any project, and Agentao appears in the agent picker.

### Notes

- `command` must be on `PATH`. If you installed via `uv tool install agentao`, it's already there. Otherwise use the absolute path: `/Users/you/.local/bin/agentao`
- The `env` block is merged with Zed's own environment. Prefer pulling API keys from `~/.zshenv` / `~/.bashrc` and leaving them out of `settings.json`
- Zed passes the workspace root as `cwd` in `session/new` — so `AGENTAO.md`, `.agentao/mcp.json`, skills, memory are all workspace-scoped automatically

## 3.5.3 End-to-end wire trace

What happens when the user types a message in Zed's agent pane:

```
USER types "find TODOs" in Zed                               time
                                                              ────▶
Zed → agentao:  initialize (once per launch)
agentao → Zed:  capabilities + extensions

Zed → agentao:  session/new  {cwd: "/workspace"}
agentao → Zed:  {sessionId: "sess-1"}

Zed → agentao:  session/prompt  {sessionId, prompt:[{"type":"text",
                                                      "text":"find TODOs"}]}

agentao → Zed:  session/update  agent_message_chunk  "Let me search..."
agentao → Zed:  session/update  tool_call            {title:"grep -r TODO"}
agentao → Zed:  session/update  tool_call_update     {output:"src/x.py:42: TODO ..."}
agentao → Zed:  session/update  agent_message_chunk  "Found 3 TODOs in..."
agentao → Zed:  response        {stopReason: "end_turn"}

(ongoing)       session/update  notifications stream to Zed's UI
```

Zed renders each `session/update` as you'd expect: text chunks stream into the reply, tool calls show as inline cards, permission requests pop as modals.

## 3.5.4 Required Zed capabilities

Agentao advertises the following in `initialize`:

```json
{
  "agentCapabilities": {
    "loadSession": true,
    "promptCapabilities": { "image": false, "audio": false, "embeddedContext": false },
    "mcpCapabilities":   { "http": false, "sse": true }
  }
}
```

Meaning for Zed:

- ✅ `loadSession`: can restore prior conversations from disk
- ❌ `image` / `audio`: text-only prompts in v1
- ❌ `embeddedContext`: Zed can't push embedded resource fetches
- ✅ `sse`: Zed can forward SSE MCP servers
- ❌ `http`: Agentao doesn't support HTTP MCP transport

Zed falls back gracefully on unsupported capabilities.

## 3.5.5 Multi-workspace & multi-window

Zed spawns **one `agentao` subprocess per agent instance**, and creates separate sessions via `session/new` for each project. That means:

- Multiple Zed windows share one `agentao` process
- Each workspace's `.agentao/memory.db` is isolated by `cwd`
- MCP servers spawned via per-session `mcpServers` are scoped to that session

If you prefer one process per workspace (tighter isolation, higher RAM), configure Zed to spawn a separate agent per workspace — consult Zed's own docs for the latest toggle name.

## 3.5.6 VS Code / Cursor / other editors

The pattern repeats:

1. Launch `agentao --acp --stdio` as a child process
2. Speak NDJSON JSON-RPC 2.0 over stdio
3. Wire `session/update` → UI, `session/request_permission` → modal

A VS Code extension using the TypeScript `ACPClient` from [3.3.4](./3-host-client-architecture#3-3-4-typescript-node-reference-implementation) is outlined in [Blueprint B](/en/part-7/2-ide-plugin).

### JetBrains / IntelliJ

JetBrains IDEs can spawn subprocesses from plugins; the ACP client can be implemented in Kotlin or Java using the same three-loop pattern. No JetBrains-specific complications.

### Neovim

Use `vim.fn.jobstart()` (Lua) or similar to spawn `agentao --acp --stdio`. Forward `session/update` to a floating window. Community plugins for LSP transport make this straightforward.

## 3.5.7 Environment & secrets

Across every IDE:

- **Never** commit API keys into workspace settings
- Prefer OS keychain (`security` on macOS, Credential Manager on Windows, `secret-tool` on Linux) + a small wrapper script that reads the key and execs `agentao`
- Or: require the user to set `OPENAI_API_KEY` in their shell profile, and let the IDE inherit the environment

Example wrapper (macOS):

```bash
#!/usr/bin/env bash
# /usr/local/bin/agentao-wrapper
export OPENAI_API_KEY="$(security find-generic-password -ws openai-api-key)"
exec agentao "$@"
```

Point the IDE at `agentao-wrapper` instead of `agentao`.

## 3.5.8 Debugging integration issues

When things go wrong in the IDE:

| Symptom | Where to look |
|---------|---------------|
| Agent doesn't appear in picker | IDE's own log (e.g. Zed's `Help → Open Log`) |
| Agent crashes on first message | `<workspace>/agentao.log` |
| Tool call hangs forever | IDE is probably not responding to `session/request_permission` — look at its permission UI code |
| All text comes as one big blob at end | IDE isn't treating `session/update` as streamed; check its UI rendering |
| MCP server doesn't show up | `mcpCapabilities.http` is false — use stdio or SSE only |
| Conversation disappears after restart | IDE isn't calling `session/load` — feature may not be implemented yet |

Capture the wire trace by launching Agentao manually and piping JSON in:

```bash
# Send hand-crafted JSON via stdin
agentao --acp --stdio < trace.ndjson > output.ndjson 2> agentao.stderr.log
```

This bisects whether the issue is in your JSON, in Agentao's handling, or in the IDE's rendering.

## 3.5.9 Upgrading Agentao cleanly

When a user updates the `agentao` binary:

- Zed / VS Code spawn a new process on next launch — no action needed
- `protocolVersion` negotiation handles version mismatch: if the IDE sends version `2` and Agentao supports `1`, Agentao returns `1` and the IDE either continues or disconnects
- Skills, MCP, and memory are **workspace-scoped**, so they survive binary upgrades

Breaking protocol changes are avoided. If you need to pin a specific version, ask users to install with `uv tool install agentao==0.2.11`.

## 3.5.10 Checklist for a new IDE integration

- [ ] ACP client implemented (three-loop architecture from [3.3](./3-host-client-architecture))
- [ ] Handshake sends `protocolVersion: 1` as an integer
- [ ] `session/update` rendered as streaming UI (chunks appear immediately)
- [ ] `session/request_permission` responses within the timeout, never silently drop
- [ ] `session/cancel` wired to a "Stop" button
- [ ] Subprocess cleanup on IDE shutdown (no orphan processes)
- [ ] Error path: subprocess exits → show error + allow reconnect
- [ ] Log the raw wire trace at debug level for support purposes

---

End of Part 3. Next: [Part 4 · Event layer & UI integration](/en/part-4/).
