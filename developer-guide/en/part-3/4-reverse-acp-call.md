# 3.4 Reverse: Calling External ACP Agents

[3.2](./2-agentao-as-server) and [3.3](./3-host-client-architecture) frame Agentao as the **server** and your host as the client. This section flips the roles: **Agentao is the client**, driving an external ACP-capable agent as a subprocess. Use this when you have a specialist agent (search bot, doc crawler, code reviewer) that exposes ACP and you want your main Agentao to delegate turns to it.

## 3.4.1 When to use this

| Scenario | Why ACP reverse-call fits |
|----------|---------------------------|
| "Sub-agent" for niche skills | Isolation: crash in sub-agent doesn't take down main |
| Multi-language agent composition | Main in Python, specialist in Rust / Go / TS, all speak ACP |
| Reuse existing ACP agent | Zed agents, your own internal agents, etc. — already ACP-shaped |
| Compute-heavy side work | Run the specialist under a different resource profile / sandbox |

When **not** to use this:

- If you just want a local tool — write a `Tool` subclass instead, it's simpler
- If the sub-agent is Python and shares your process — spawn another `Agentao` in-process instead

## 3.4.2 The `ACPManager` — public API

`ACPManager` is Agentao's ACP-client side. Import from `agentao.acp_client`:

```python
from agentao.acp_client import ACPManager, load_acp_client_config, PromptResult
from agentao.acp_client import AcpClientError, AcpErrorCode, ServerState
```

Two construction paths:

```python
# 1. From .agentao/acp.json (auto-discovers project root)
mgr = ACPManager.from_project()

# 2. From an explicit config
config = load_acp_client_config(project_root=Path("/app"))
mgr = ACPManager(config)
```

If you run `ACPManager` inside an unattended host — CI workers, cron jobs, queue consumers — this same object is what the guide calls the **Headless Runtime**. Pin the mental model before reading the API:

- **Not a third mode**: transport is still ACP subprocess + stdio + JSON-RPC
- **Not a different object model**: you are still using `ACPManager`
- **Just a different operating profile**: the host provides no human confirmation, so server-initiated interaction is handled by non-interactive policy

Read the API through that lens:

1. Use `prompt_once()` / `send_prompt()` to submit turns
2. Use `get_status()` / `readiness(name)` to gate submissions
3. Treat `last_error` as diagnostic history, not the admission signal
4. Treat `SERVER_BUSY` as concurrency backpressure, not an implicit queue

Short version: **Headless Runtime = unattended `ACPManager`**.

Public surface:

| Method | Purpose |
|--------|---------|
| `start_all()` / `start_server(name)` | Spawn subprocesses + handshake |
| `stop_all()` / `stop_server(name)` | Graceful shutdown |
| `prompt_once(name, prompt, ...)` | One fire-and-forget turn — **recommended entry point** |
| `send_prompt(name, prompt, ...)` | Long-lived session variant (keeps subprocess alive) |
| `cancel_turn(name)` | Cancel an in-flight turn |
| `get_status()` | Typed `list[ServerStatus]` snapshot (see 3.4.8) |

`send_prompt_nonblocking` / `finish_prompt_nonblocking` /
`cancel_prompt_nonblocking` also exist on `ACPManager` but are
**internal / unstable** — used by Agentao's own interactive CLI
inline-confirmation pipeline. Headless embedders should call
`send_prompt` or `prompt_once` instead. See
[`docs/features/headless-runtime.md`](../../../docs/features/headless-runtime.md)
for the authoritative support-level table.

Full API details in [Appendix A · ACP Client](/en/appendix/a-api-reference#a-7-acp-client).

## 3.4.3 `prompt_once()` — the 95% use case

```python
def prompt_once(
    self,
    name: str,
    prompt: str,
    *,
    cwd: Optional[str] = None,
    mcp_servers: Optional[List[dict]] = None,
    timeout: Optional[float] = None,
    interactive: bool = False,
    stop_process: bool = True,
) -> PromptResult:
```

Semantics:

- Acquires the per-server lock in **fail-fast** mode. If another turn is already running for that server, raises `AcpClientError(code=SERVER_BUSY)` — no waiting and no hidden queue
- Spawns an **ephemeral client** if no long-lived one exists; tears it down on exit
- If a long-lived client exists (you called `start_server(name)` earlier), it's reused — and the subprocess survives past this call
- Returns `PromptResult` with `stop_reason`, `session_id`, `cwd`, and the raw payload

That is also why this is the default headless entry point: the behavior is narrow and predictable. In practice you usually handle only three outcome classes:

- Success: you get a `PromptResult`
- Concurrency conflict: you get `SERVER_BUSY`
- Runtime failure: you get some other `AcpClientError`

### Example: main agent delegating to a "searcher"

```python
from agentao.acp_client import ACPManager, AcpClientError, AcpErrorCode

mgr = ACPManager.from_project()

def search_via_subagent(query: str) -> str:
    try:
        result = mgr.prompt_once(
            "searcher",
            prompt=query,
            cwd="/tmp/searcher-workspace",
            timeout=30.0,
        )
        if result.stop_reason != "end_turn":
            return f"[searcher ended with {result.stop_reason}]"
        # Extract assistant text from result.raw if you captured the stream
        return "<see notification stream for content>"
    except AcpClientError as e:
        if e.code == AcpErrorCode.SERVER_BUSY:
            return "[searcher busy, try again]"
        raise
```

Wrap it as an Agentao `Tool` so your main agent can use it like any other capability:

```python
from agentao.tools.base import Tool

class SearcherTool(Tool):
    name = "delegate_search"
    description = "Delegate a web/docs search to the specialist ACP agent."
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    requires_confirmation = False

    def __init__(self, mgr: ACPManager):
        self.mgr = mgr

    def execute(self, query: str) -> str:
        return search_via_subagent(query)
```

Register it in the main agent:

```python
from agentao import Agentao

mgr = ACPManager.from_project()
mgr.start_server("searcher")        # warm up; optional but reduces latency

main = Agentao(tools=[SearcherTool(mgr)])
main.chat("Research the agent-client protocol and summarize.")
```

## 3.4.4 Capturing the stream from the sub-agent

`prompt_once()` blocks until the sub-agent finishes — useful for simple "ask once, get answer" flows. But if you want to **stream** the sub-agent's output to your main UI, pass a `notification_callback` when constructing `ACPManager`:

```python
def on_notification(server_name: str, method: str, params) -> None:
    if method == "session/update":
        update = params.get("update", {})
        if update.get("sessionUpdate") == "agent_message_chunk":
            text = update["content"]["text"]
            print(f"[{server_name}] {text}", end="", flush=True)

mgr = ACPManager(config, notification_callback=on_notification)
```

Callback runs on the reader thread — keep it fast, or push onto a queue.

## 3.4.5 Config format — `.agentao/acp.json`

Same schema as Agentao's own config-discovered servers:

```json
{
  "servers": {
    "searcher": {
      "command": "my-searcher",
      "args": ["--acp", "--stdio"],
      "env": { "SEARCH_API_KEY": "$SEARCH_API_KEY" },
      "cwd": ".",
      "autoStart": true,
      "startupTimeoutMs": 10000,
      "requestTimeoutMs": 60000,
      "description": "Web + docs specialist",
      "nonInteractivePolicy": { "mode": "reject_all" }
    }
  }
}
```

Required fields: `command`, `args`, `env`, `cwd`. Optional: `autoStart`, `startupTimeoutMs`, `requestTimeoutMs`, `capabilities`, `description`, `nonInteractivePolicy`.

- Relative `cwd` resolves against the project root (the dir containing `.agentao/`)
- `$VAR` / `${VAR}` in `env` values expand against the host process's environment
- `nonInteractivePolicy` is a structured object `{"mode": "reject_all" | "accept_all"}`. Missing ⇒ implicit `{"mode": "reject_all"}`. The pre-Week-3 bare string form (`"reject_all"` / `"accept_all"`) is rejected at config-load time — migrate via [Appendix E](/en/appendix/e-migration).
- Use `reject_all` for production. Per-call `interaction_policy=` on `send_prompt` / `prompt_once` overrides the server default for a single turn.

See [Appendix B · Config keys](/en/appendix/b-config-keys) for the full field reference.

### Per-call policy override

```python
from agentao.acp_client import ACPManager, InteractionPolicy

mgr = ACPManager.from_project()

# Use the server default (reject_all above).
mgr.send_prompt("searcher", "summarize the docs", interactive=False)

# One-off approve for a trusted batch job.
mgr.send_prompt(
    "searcher", "rebuild the index", interactive=False,
    interaction_policy="accept_all",
)

# Equivalent with the typed form.
mgr.prompt_once(
    "searcher", "rebuild the index",
    interaction_policy=InteractionPolicy(mode="accept_all"),
)
```

Precedence: **per-call override > server default**. `None` (the default) falls back to the server default. `send_prompt_nonblocking` is internal / unstable and **does not** accept this kwarg.

## 3.4.6 Long-lived vs. ephemeral clients

`prompt_once()` is fail-fast and can run in either mode:

| Mode | Trigger | Process | Best for |
|------|---------|---------|----------|
| **Ephemeral** | `prompt_once()` without prior `start_server()` | Spawned for this call, torn down on exit | One-shot workflows, batch jobs |
| **Long-lived** | You called `start_server(name)` first | Subprocess stays alive across calls | Chat-like usage, cold-start-sensitive paths |

Latency trade-off:
- Ephemeral: ~200–500 ms startup per call
- Long-lived: ~10 ms per call (subprocess already warm)

Memory trade-off:
- Ephemeral: 0 residual memory
- Long-lived: ~50–200 MB per server held

Rule of thumb: **start long-lived for anything you'll call more than a few times per minute**. Everything else: ephemeral.

From a headless-operations point of view, you can reduce that further:

- **Need throughput**: call `start_server()` first and keep it warm
- **Need isolation / cleanliness**: use plain `prompt_once()` and let it start/stop
- **Unsure**: default to `prompt_once()` because the runtime surface is smaller

## 3.4.7 Lifecycle & recovery

Three common failure scenarios have pinned behaviour so embedders don't have to hand-roll recovery:

**Cancel / timeout → next turn is safe.** Turn-slot, per-server lock, and the pending prompt slot all release inside `finally` blocks, in a fixed order. The first `send_prompt` / `prompt_once` after a cancel or timeout sees a ready server with no residual state.

**Recoverable process death → auto-rebuild.** If the subprocess has died between calls (clean exit, idle non-zero within cap, stdio EOF, or death during an active turn), the next `ensure_connected` / `send_prompt` call closes the dead client, bumps `mgr.restart_count(name)`, and rebuilds transparently. `maxRecoverableRestarts` (default 3) caps consecutive auto-rebuilds on idle non-zero exits.

**Fatal process death → sticky, operator action required.** OOM / SIGKILL / `exit 137`, signal-terminated processes, consecutive handshake failures, or idle non-zero exits beyond the cap mark the server as sticky-fatal. `mgr.is_fatal(name)` returns `True`; all calls raise `AcpClientError(code=TRANSPORT_DISCONNECT, details={"recovery": "fatal"})` until `mgr.restart_server(name)` or `mgr.start_server(name)` clears the mark.

```python
from agentao.acp_client import ACPManager, AcpClientError, AcpErrorCode

mgr = ACPManager.from_project()

try:
    mgr.prompt_once("searcher", "...")
except AcpClientError as e:
    if e.code is AcpErrorCode.TRANSPORT_DISCONNECT \
       and e.details.get("recovery") == "fatal":
        page_operator()
        # Later: mgr.restart_server("searcher")
```

The classifier is a pure function — `classify_process_death` — exported from `agentao.acp_client` and testable in isolation. See [`docs/features/headless-runtime.md` §7.2](../../../docs/features/headless-runtime.md) for the full decision matrix.

## 3.4.8 Cancellation & errors

```python
# Cancel an in-flight turn
mgr.cancel_turn("searcher")

# Distinguish errors by code
try:
    mgr.prompt_once("searcher", "...")
except AcpClientError as e:
    match e.code:
        case AcpErrorCode.SERVER_BUSY:       retry_after_delay()
        case AcpErrorCode.SERVER_NOT_FOUND:  log_config_issue()
        case AcpErrorCode.HANDSHAKE_FAIL:    reinstall_sub_agent_binary()
        case AcpErrorCode.REQUEST_TIMEOUT:   raise_alert()
        case _:
            # `AcpRpcError` raised during handshake keeps `code` as
            # the JSON-RPC int (not an `AcpErrorCode`) and never
            # reaches the `HANDSHAKE_FAIL` arm — detect it via
            # `details["phase"]` when that matters:
            if e.details.get("phase") == "handshake":
                reinstall_sub_agent_binary()
            else:
                raise
```

Full error taxonomy (including the `AcpRpcError` contract and the `details["underlying_code"]` / `details["phase"]` signals) in [Appendix D · Error codes](/en/appendix/d-error-codes).

## 3.4.9 Health & debugging

`ACPManager.get_status()` returns a typed `list[ServerStatus]`:

```python
from agentao.acp_client import ServerStatus

for s in mgr.get_status():             # each s: ServerStatus
    print(s.server, s.state, s.pid, s.has_active_turn)
    if s.state == ServerState.FAILED.value:
        info = mgr.get_handle(s.server).info
        print(f"{s.server} failed: {info.last_error}")
```

Core fields:

- `server: str` — name from `.agentao/acp.json`
- `state: str` — `ServerState` enum value
- `pid: int | None`
- `has_active_turn: bool` — derived from the manager's active turn
  slot; stays `True` for the full lifetime of a turn, including
  in-flight interactions

Diagnostic fields (additive on the same dataclass):
`last_error`, `last_error_at`, `active_session_id`, `inbox_pending`,
`interaction_pending`, `config_warnings`.
Read them directly off `ServerStatus`; `mgr.get_handle(name).info` and
`mgr.inbox` / `mgr.interactions` remain available for the raw handle
view. See
[`docs/features/headless-runtime.md`](../../../docs/features/headless-runtime.md)
for the full field list and migration table from the pre-typed dict
shape.

Log files from the sub-agent land in `<server cwd>/agentao.log` (for Agentao-type sub-agents) or wherever that agent chooses. Always set `cwd` in `.agentao/acp.json` to a writable dir so logs don't get lost.

## 3.4.10 Lifecycle checklist

When your main Agentao process starts:

```python
mgr = ACPManager.from_project()
mgr.start_all()            # or start_server(name) per specialist
```

When it shuts down:

```python
mgr.stop_all()             # kills subprocesses gracefully
```

Wrap with try/finally or a context manager — orphaned ACP subprocesses are a common source of leaked resources on hot reloads.

---

Next: [3.5 Zed / IDE integration →](./5-zed-ide-integration)
