# Appendix F · FAQ & Troubleshooting

Organized by **symptom**, not by part. Each entry links back to the main guide for detail.

## F.1 Setup & startup

### "ImportError: cannot import name 'Agentao'"

- Check you installed the package (`uv add agentao` or `pip install agentao`, not just `uv add openai`)
- Import from the top level: `from agentao import Agentao` (not `from agentao.agent import Agentao` — that path is not guaranteed stable)

### "No module named 'openai' / 'mcp'"

Install with the full extras if you need MCP:

```bash
uv add 'agentao[mcp]'          # or
uv add 'agentao[all]'
```

### "ValueError: OPENAI_API_KEY is not set"

Three resolution options:

1. `.env` at working-directory root, with `OPENAI_API_KEY=…`
2. Process env: `export OPENAI_API_KEY=…`
3. Constructor: `Agentao(api_key="sk-…")`

Constructor wins over env, which wins over `.env`. See [Appendix B](./b-config-keys).

### "Model 'gpt-5.4' not found" (custom endpoint)

The default model id is `gpt-5.4`. If your endpoint offers different models, pass `model=` or set `OPENAI_MODEL`. See [2.2](/en/part-2/2-constructor-reference).

## F.2 Runtime behavior

### Agent says "Tool execution cancelled by user" on every write

You set `PermissionMode.READ_ONLY` (explicitly or by accident). Either:

- Construct an engine and switch modes explicitly: `e = PermissionEngine(); e.set_mode(PermissionMode.WORKSPACE_WRITE); agent = Agentao(permission_engine=e, ...)`
- Or implement a `confirm_tool` callback on the transport so users can approve interactively

### `chat()` never returns

Three likely causes:

1. **Infinite tool loop** — hit `max_iterations`. Lower the limit or wire `on_max_iterations` ([4.6](/en/part-4/6-max-iterations))
2. **Tool hangs** — a custom tool has no timeout. Wrap subprocess / HTTP calls in `timeout=` ([6.7](/en/part-6/7-resource-concurrency#control-4-tool-timeout))
3. **User prompt needs input** — the default `ask_user` waits forever in headless mode. Override via `SdkTransport(ask_user=…)`

Enforce a hard limit at the host:

```python
reply = await asyncio.wait_for(asyncio.to_thread(agent.chat, msg), timeout=120)
```

### "Why does my tool get called with weird paths?"

Tool `execute()` receives whatever the LLM supplied. Validate args, and use `self._resolve_path(raw)` to join against `working_directory` — see [Tool base class](/en/appendix/a-api-reference#a-3-tools).

### Output contains random escape sequences

Some terminals aren't used. Either:

- Disable color in the transport side before display (`rich.console.Console(no_color=True)`)
- Strip with a post-filter; Agentao itself does not enforce a color policy

## F.3 Memory & sessions

### "I cleared history but old context still leaks"

`clear_history()` only resets `self.messages`. The **memory DB** persists — that's intentional. To also wipe memory:

```python
agent.clear_history()
agent.memory.clear(scope="project")
```

### Memory bleeds across tenants

Classic multi-tenant trap. You mounted `~/.agentao/memory.db` user scope across tenants. Either:

- Pin per-tenant working directory AND disable user scope, or
- Key user-scope memories by `tenant_id+user_id`

See [6.4](/en/part-6/4-multi-tenant-fs).

### "Session state lost on restart"

Two-part fix:

- **SDK**: serialize `agent.messages` yourself; on restart `agent.messages = saved_messages`
- **ACP**: use `session/load` with a stored `sessionId` — agent must advertise `loadSession: true` ([7.2 pattern](/en/part-7/2-ide-plugin#3-persist-resume-across-ide-restart))

## F.4 MCP

### "MCP server listed but no tools appear"

Check in order:

1. `/mcp` CLI (or `agent.mcp_manager.get_status()`) — is the server in `ready` state?
2. Subprocess stderr — often stdout-corruption from a server that logs to stdout
3. Tool-name collision — same `{server}_{tool}` registered twice triggers a warning in `agentao.log`

### "'mcp' command not found"

Install the MCP extras. On Linux you may also need a JS runtime if the MCP server is `npx`-launched.

### "Server fails with 'timeout'"

Three layers:

1. Per-tool timeout in `mcp.json` (`"timeout": 30`)
2. Transport default (~30s for stdio, ~60s for SSE)
3. Your wrapping `asyncio.wait_for`

The tightest wins. See [Appendix B.3.1](./b-config-keys#b-3-1-mcp-json).

## F.5 Security & sandbox

### "macOS says sandbox-exec denied"

Open `agentao.log` — the exact denial reason is logged. Common fixes:

- Shell profile too restrictive → switch `default_profile` from `readonly` to `workspace-write-no-network`
- Command outside workspace → use absolute paths inside `working_directory`
- See [6.2](/en/part-6/2-shell-sandbox)

### "Sandbox disabled in production — how do I enforce it?"

Sandbox config is merged: project `.agentao/sandbox.json` overrides user. Mount project config read-only in your container so LLM-led changes to sandbox settings can't persist. See [7.4 pitfall table](/en/part-7/4-data-workbench#pitfalls).

### "Agent tried to fetch 169.254.169.254"

Expected — SSRF attempts hit the built-in blocklist. Check `agentao.log` for the deny record and verify your `PermissionEngine` rules ([6.3](/en/part-6/3-network-ssrf)).

## F.6 ACP integration

### `handshake_fail` on initialize

Likely a version mismatch. Agentao v0.2.x speaks `protocolVersion: 1` (integer). If your client sends a string like `"2025-09-01"`, the server rejects it. See [3.1](/en/part-3/1-acp-tour).

### `server_busy` from `prompt_once`

Fail-fast semantics — someone else is already in a turn. Options:

- Wait + retry
- Use the session-based API (`send_prompt`) if queueing is acceptable
- Spawn a dedicated subprocess per tenant

See [Appendix D.5](./d-error-codes#d-5-retry-guidance).

### "session/cancel doesn't stop my long tool"

Cancellation bubbles through `CancellationToken`, but **your custom tool must cooperate**. Check `self._current_token` inside long loops and call `token.check()` between steps.

## F.7 Deployment & ops

### "Docker image is huge"

Multi-stage build — see [6.8 Dockerfile template](/en/part-6/8-deployment#dockerfile-template). Key move: don't ship `uv` into the runtime stage.

### "Kubernetes pod restarts and loses sessions"

Use `StatefulSet` (not `Deployment`) and a PVC for `/data`. Set Service `sessionAffinity: ClientIP`. See [6.8](/en/part-6/8-deployment#kubernetes-notes).

### "How do I cap token spend per tenant?"

`TokenBudget` pattern — see [6.7](/en/part-6/7-resource-concurrency#token-budgets). For exact counts use `agentao[tokenizer]` (pulls `tiktoken`).

### Cost suddenly doubled overnight

Likely culprits:

- Model version swap (check deployment audit)
- A skill change made the LLM call more tools per turn
- Context compression triggered more often — check `max_context_tokens`

Compare `LLM_TEXT` event token counts from yesterday vs today. Event archive ([6.6](/en/part-6/6-observability#event-stream-archive)) is what makes this feasible.

## F.8 Development & testing

### "How do I unit-test a custom tool?"

Tools are plain classes — `MyTool().execute(**args)`. No Agentao instance needed. For tools that touch disk, pass a `working_directory` temp dir.

### "How do I assert the agent did the right thing?"

Don't assert on LLM output text (non-deterministic). Instead:

- Spy on `EventType.TOOL_START` events via `SdkTransport(on_event=spy)`; assert the tool was called with expected args
- Or mock the tool and assert interactions

### "LLM responses are non-deterministic across test runs"

Lower `temperature=0` when testing, but accept that exact wording will still drift. Test the **effect** (tool calls, final files, return shape), not the prose.

## F.9 Still stuck?

Minimum reproduction for a bug report:

1. Agentao version (`python -c "import agentao; print(agentao.__version__)"`)
2. `OS`, Python version
3. A script that reproduces in ≤ 30 lines
4. Tail of `agentao.log` around the failure
5. For ACP issues: `AcpClientError.code` + `.details`

File at <https://github.com/jin-bo/agentao/issues>.
