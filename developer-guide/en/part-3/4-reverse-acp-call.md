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

Public surface:

| Method | Purpose |
|--------|---------|
| `start_all()` / `start_server(name)` | Spawn subprocesses + handshake |
| `stop_all()` / `stop_server(name)` | Graceful shutdown |
| `prompt_once(name, prompt, ...)` | One fire-and-forget turn — **recommended entry point** |
| `send_prompt(name, prompt, ...)` | Long-lived session variant (keeps subprocess alive) |
| `cancel_turn(name)` | Cancel an in-flight turn |
| `get_status(name=None)` | Observable state snapshot |

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

- Acquires the per-server lock in **fail-fast** mode. If another turn is already running for that server, raises `AcpClientError(code=SERVER_BUSY)` — no waiting
- Spawns an **ephemeral client** if no long-lived one exists; tears it down on exit
- If a long-lived client exists (you called `start_server(name)` earlier), it's reused — and the subprocess survives past this call
- Returns `PromptResult` with `stop_reason`, `session_id`, `cwd`, and the raw payload

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
      "nonInteractivePolicy": "reject_all"
    }
  }
}
```

Required fields: `command`, `args`, `env`, `cwd`. Optional: `autoStart`, `startupTimeoutMs`, `requestTimeoutMs`, `capabilities`, `description`, `nonInteractivePolicy`.

- Relative `cwd` resolves against the project root (the dir containing `.agentao/`)
- `$VAR` / `${VAR}` in `env` values expand against the host process's environment
- `nonInteractivePolicy` = `"reject_all"` (default) or `"accept_all"` — what to do with permission prompts from the sub-agent when there's no human. Use `reject_all` for production.

See [Appendix B · Config keys](/en/appendix/b-config-keys) for the full field reference.

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

## 3.4.7 Cancellation & errors

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
        case _:                              raise
```

Full error taxonomy in [Appendix D · Error codes](/en/appendix/d-error-codes).

## 3.4.8 Health & debugging

```python
status = mgr.get_status()
# -> {"searcher": {"state": "ready", "pid": 8123, "last_activity": 1700000000.0}}

for name, info in status.items():
    if info["state"] == ServerState.FAILED.value:
        print(f"{name} failed: {info['last_error']}")
```

Log files from the sub-agent land in `<server cwd>/agentao.log` (for Agentao-type sub-agents) or wherever that agent chooses. Always set `cwd` in `.agentao/acp.json` to a writable dir so logs don't get lost.

## 3.4.9 Lifecycle checklist

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
