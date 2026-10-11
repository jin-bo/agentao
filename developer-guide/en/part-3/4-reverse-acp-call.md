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
| `get_status()` | Typed `list[ServerStatus]` snapshot (see 3.4.9) |
| `readiness(name)` / `is_ready(name)` | Coarse "can I submit now?" answer (see [3.4.11](#readiness-classes)) |
| `get_server_logs(name, n=50)` | Last *n* stderr lines of the subprocess |

`send_prompt_nonblocking` / `finish_prompt_nonblocking` /
`cancel_prompt_nonblocking` also exist on `ACPManager` but are
**internal / unstable** — used by Agentao's own interactive CLI
inline-confirmation pipeline. Headless embedders should call
`send_prompt` or `prompt_once` instead. The support level of each
entry point is in [3.4.11](#entry-points-and-support-levels).

Full API details in [Appendix A · ACP Client](/en/appendix/a-api-reference#a-8-acp-client).

### Supported imports

The only semver-stable import root is `agentao.acp_client`. The embedding contract is this set:

```python
from agentao.acp_client import (
    ACPManager,
    AcpClientConfig, AcpServerConfig, load_acp_client_config,
    AcpConfigError, AcpClientError, AcpRpcError, AcpErrorCode,
    AcpInteractionRequiredError,
    InteractionPolicy, PromptResult,
    ServerState, ServerStatus, AcpProcessInfo,
    classify_process_death,
)
```

The package also re-exports `ACPClient`, `ACPProcessHandle`, `AcpConnectionInfo`, `Inbox`, `InteractionRegistry` and the router helpers, but only for older embedders. They are implementation details and can change between releases. If you need one, import it from its submodule (`agentao.acp_client.client`, `.process`, `.inbox`, `.interaction`, `.router`). The full move table is in [Appendix E.10](/en/appendix/e-migration#e-10-acp-client-—-internal-names-move-to-submodules).

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
    interaction_policy: InteractionPolicy | str | None = None,
) -> PromptResult:
```

Semantics:

- Acquires the per-server lock in **fail-fast** mode. If another turn is already running for that server, raises `AcpClientError(code=SERVER_BUSY)` — no waiting and no hidden queue
- Spawns an **ephemeral client** if no long-lived one exists; tears it down on exit. An ephemeral client is never registered as the server's long-lived client
- `stop_process=True` (the default) stops the subprocess on exit **only** if this call started it. A subprocess that was already running survives
- If a long-lived client exists (you called `start_server(name)` earlier), it's reused — and the subprocess survives past this call
- `interactive=False` is the default, so server-initiated permission and input requests follow the non-interactive policy (see [3.4.11](#non-interactive-turns))
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
from agentao.host import Tool

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
- `nonInteractivePolicy` is a structured object `{"mode": "reject_all" | "accept_all"}`. Missing ⇒ implicit `{"mode": "reject_all"}`. The old bare string form (`"reject_all"` / `"accept_all"`) is rejected at config-load time — migrate via [Appendix E](/en/appendix/e-migration).
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

### Adding a server at runtime

A running manager takes new servers without being rebuilt. `add_server` registers the per-server state and leaves the server stopped; running servers are untouched:

```python
from pathlib import Path

from agentao.acp_client.config import add_server_entry
from agentao.acp_client.registry import entry_to_server_config, fetch_registry, find_agent

entry = entry_to_server_config(find_agent(fetch_registry(), "gemini"))  # npx / uvx only
config = add_server_entry("gemini", entry.config, project_root=Path("/app"))  # optional: persist
mgr.add_server("gemini", config)
```

Each step is separate — query, convert, write `acp.json`, register — so a host can stop after any of them. `entry_to_server_config` writes `autoStart: false` and `startupTimeoutMs: 120000` (the first launch downloads the package); `add_server_entry` refuses a name collision or an invalid existing file, and writes atomically. Scope and rules: [CLI 8 · Adding agents from the ACP Registry](/en/cli/8-mcp-acp-plugins#adding-agents-from-the-acp-registry-0-5-8).

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

### Session reuse with `send_prompt()`

Use `send_prompt()` to keep one session across many tasks while each call still passes its own `cwd` and `mcp_servers`:

```python
mgr = ACPManager.from_project()
mgr.start_all()
try:
    raw = mgr.send_prompt(
        "codex-reviewer",
        "Review the diff",
        cwd="/path/to/task-checkout",
        mcp_servers=[],
        timeout=120,
        interactive=False,      # send_prompt defaults to True
    )
    print(raw["stopReason"])    # send_prompt returns the raw result dict
finally:
    mgr.stop_all()
```

- The cached session is reused only if **both** `cwd` and `mcp_servers` match the values it was created with. `None` means the server's configured `cwd` and an empty MCP list, and the comparison uses those resolved values.
- On a mismatch, the manager sends a new `session/new` on the same client and transport. The subprocess does not restart.
- `send_prompt()` defaults to `interactive=True`, which is the CLI's mode. A headless host must pass `interactive=False`.

## 3.4.7 Lifecycle & recovery

Three common failure scenarios have pinned behaviour so embedders don't have to hand-roll recovery:

**Cancel / timeout → next turn is safe.** Turn-slot, per-server lock, and the pending prompt slot all release inside `finally` blocks, in a fixed order. The first `send_prompt` / `prompt_once` after a cancel or timeout sees a ready server with no residual state.

**Recoverable process death → auto-rebuild.** If the subprocess has died between calls (clean exit, idle non-zero within cap, stdio EOF, or death during an active turn), the next `ensure_connected` / `send_prompt` call closes the dead client, bumps `mgr.restart_count(name)`, and rebuilds transparently. `maxRecoverableRestarts` (default 3) caps consecutive auto-rebuilds on idle non-zero exits.

**Fatal process death → sticky, operator action required.** OOM / SIGKILL / `exit 137`, signal-terminated processes, consecutive handshake failures, or idle non-zero exits beyond the cap mark the server as sticky-fatal. An `auth_required` answer is not a handshake failure and never counts (see 3.4.8). `mgr.is_fatal(name)` returns `True`; all calls raise `AcpClientError(code=TRANSPORT_DISCONNECT, details={"recovery": "fatal"})` until `mgr.restart_server(name)` or `mgr.start_server(name)` clears the mark.

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

The classifier is a pure function — `classify_process_death` — exported from `agentao.acp_client` and testable in isolation. The full decision matrix is below.

### Cleanup order after a cancel or timeout

Every failure path of `send_prompt` / `prompt_once` releases its state exactly once, in this order:

1. **Pending prompt slot.** On a timeout, the client drops the pending `session/prompt` slot *before* it sends `session/cancel`. The drop is idempotent and never raises, so a broken pipe cannot leave the client stuck for the next turn.
2. **Turn slot.** The manager's single-active-turn slot is cleared in a `finally` block. This happens on success, timeout, cancel and transport error alike.
3. **`last_error`.** The error is recorded before the lock is released, so a `get_status()` call on the same tick already sees it.
4. **Per-server lock.** The outer `finally` releases the lock, whatever was raised above.

Result: a `cancel_turn` or a timeout never leaves the server busy or locked for the next call. Source: [`agentao/acp_client/manager/turns.py`](https://github.com/jin-bo/agentao/blob/main/agentao/acp_client/manager/turns.py).

### Process-death classification

When the subprocess dies — between calls or under an active turn — the manager decides whether to rebuild or give up:

| Trigger | Classification | What happens |
|---------|----------------|--------------|
| Idle process exits with code 0 | recoverable | Lazy rebuild on the next call |
| Idle process exits non-zero | recoverable up to the cap, then fatal | Bumps `restart_count(name)`. The cap is `maxRecoverableRestarts` (default 3) |
| Process dies during an active turn (any non-signal exit) | recoverable, the cap does not apply | The current turn fails and records `last_error`. The next call rebuilds |
| Stdio transport breaks while the process still runs | recoverable | The client is rebuilt. The process is not respawned |
| Killed by a signal (negative exit code, or exit 137 / 139 / 143 — OOM, SIGKILL, SIGSEGV, SIGTERM) | fatal | No auto-respawn. `state` becomes `failed`. An explicit restart is required |
| Second consecutive handshake failure | fatal | An environment or config problem, not a transient fault |
| `auth_required` (`-32000`) from `initialize` / `session/new` | not counted | The agent needs credentials. Retrying never makes the server fatal (see [Authentication](#authentication-auth-required)) |
| `cancel_turn` from the host | not a death | Handled by the cleanup order above |

How the manager applies it:

- Before it reuses a cached client, the manager checks the process. A recoverable death closes the dead client, bumps the restart count and connects again. A fatal death marks the server sticky-fatal and raises `AcpClientError(code=TRANSPORT_DISCONNECT, details={"recovery": "fatal"})`.
- The restart count and the handshake-failure streak reset to 0 after the first successful turn.
- Only `restart_server(name)` or `start_server(name)` clears the fatal mark. A passing turn never does. An operator must acknowledge a fatal server before it is rebuilt.
- Use `is_fatal(name)` and the `(state, last_error)` pair on `ServerStatus` to tell the two cases apart.

Source: `classify_process_death` in [`agentao/acp_client/models.py`](https://github.com/jin-bo/agentao/blob/main/agentao/acp_client/models.py) and [`manager/recovery.py`](https://github.com/jin-bo/agentao/blob/main/agentao/acp_client/manager/recovery.py).

### Tuning `maxRecoverableRestarts`

```json
{
  "servers": {
    "searcher": {
      "command": "my-searcher",
      "args": [],
      "env": {},
      "cwd": ".",
      "maxRecoverableRestarts": 3
    }
  }
}
```

The default is 3. The value must be a non-negative integer. A negative number or any other type (including `true`) raises `AcpConfigError` when the config loads, not when a turn runs. `0` means the first idle non-zero exit is fatal.

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

`cancel_turn(name)` does not wait for the per-server lock, so you can call it from another thread while the turn runs. It sends `session/cancel` and marks the turn as cancelled. A cancelled turn returns normally, even if a non-interactive interaction error was latched earlier in it.

### `AcpInteractionRequiredError`

A non-interactive turn raises this error when the server asked for permission or input and the policy did not approve it (see [Non-interactive turns](#non-interactive-turns)). Its stable public fields:

| Field | Content |
|-------|---------|
| `server` | Server name |
| `prompt` | The text the server showed with the request |
| `options` | The options the server offered (list of dicts) |
| `code` | Always `AcpErrorCode.INTERACTION_REQUIRED` |

The raw server method (`session/request_permission` or `_agentao.cn/ask_user`) is in `exc.details["method"]`. Use it for logs only, and do not branch on it. The method names can change.

### Authentication (`auth_required`)

An agent without credentials answers `session/new` with `auth_required` — an `AcpRpcError` with `rpc_code == -32000`. The manager stamps `details["auth_required"] = True` and `details["auth_methods"]` (what the agent advertised), and does **not** count it toward the handshake-failure streak, so retrying never turns the server fatal. `mgr.auth_methods(name)` returns the same list at any time after the first `initialize`, and `mgr.needs_login(name)` stays true until a session opens or setup fails for another reason (a restart does not clear it), so a host can show "needs login" rather than `failed`.

```python
from agentao.acp_client import ACPManager, AcpRpcError
from agentao.acp_client.auth import (
    build_terminal_login_command, is_auth_required, terminal_auth_methods,
)

mgr = ACPManager.from_project(terminal_auth=True)  # only if you can run a terminal login
try:
    mgr.connect_server("gemini")
except AcpRpcError as e:
    if not is_auth_required(e):
        raise
    method = terminal_auth_methods(e.details["auth_methods"])[0]
    command = build_terminal_login_command(mgr.get_handle("gemini").config, method)
    with mgr.reserve_for_login("gemini"):     # SERVER_BUSY if a turn is active
        run_interactively(command.argv, command.env, command.cwd)  # your terminal; exit 0 = ok
        mgr.restart_server("gemini")          # the spec: reconnect and reinitialize
        mgr.connect_server("gemini")
```

- `terminal_auth=True` declares Terminal Auth in `initialize` (`clientCapabilities.auth.terminal` and the legacy `_meta["terminal-auth"]`). Agents may offer a `terminal` method only to a client that declared it, so a headless host leaves it `False` (the default).
- `build_terminal_login_command` follows the ACP spec: the server's own command, base args and `cwd`, the method's `args` appended, its `env` over the server's scrubbed launch environment. It refuses any method that is not `terminal`.
- Running the login is the host's job. It needs the user's terminal: inherit stdin/stdout and stay in the host's session (never `start_new_session`). Agentao's CLI runs it as a shell runs a foreground job — its own process group, given the terminal with `tcsetpgrp` and handing it back — so Ctrl+C reaches the login and a cancelled login's whole group can be ended.
- `reserve_for_login` makes turns and connects from other threads raise `SERVER_BUSY` until it exits; other servers are unaffected.
- Only `terminal` methods are supported; for `agent` methods (the default when `type` is omitted), authenticate outside Agentao and restart the server.

## 3.4.9 Health & debugging

`ACPManager.get_status()` returns a typed `list[ServerStatus]`:

```python
from agentao.acp_client import ServerStatus

for s in mgr.get_status():             # each s: ServerStatus
    print(s.server, s.state, s.pid, s.has_active_turn)
    if s.state == ServerState.FAILED.value:
        print(f"{s.server} failed: {s.last_error}")
        print("\n".join(mgr.get_server_logs(s.server, n=50)))
```

Core fields: `server`, `state`, `pid`, `has_active_turn`. Diagnostic fields on the same dataclass: `last_error`, `last_error_at`, `active_session_id`, `inbox_pending`, `interaction_pending`, `config_warnings`. The exact meaning of each field is in [3.4.11](#status-fields). `mgr.get_handle(name).info`, `mgr.inbox` and `mgr.interactions` remain available for the raw handle view. If you still read the old `list[dict]` shape, see [Appendix E.11](/en/appendix/e-migration#e-11-acp-client-—-get-status-returns-serverstatus).

`mgr.get_server_logs(name, n=50)` returns the last *n* stderr lines the manager captured from the subprocess. Look there first when an RPC fails in a daemon. It raises `SERVER_NOT_FOUND` for an unknown name.

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

## 3.4.11 Headless contract

This section is the reference for a host that runs `ACPManager` unattended: a daemon, a CI worker, a queue consumer. It pins what such a host can rely on. Where this page and the code disagree, the code wins. Please file a bug.

The runnable sample is [`examples/headless_worker.py`](https://github.com/jin-bo/agentao/blob/main/examples/headless_worker.py). It starts an inline mock ACP server, runs the success, error and cancel paths, and prints a typed status snapshot after each. Run it from the repository root with `uv run python examples/headless_worker.py`. Exit code `0` means every path worked.

### Entry points and support levels

| Entry point | Support level | Use |
|-------------|---------------|-----|
| `prompt_once(name, prompt, …)` | **public** | One turn with deterministic cleanup. Uses an ephemeral client when no long-lived one exists |
| `send_prompt(name, prompt, …)` | **public** | Turns on a long-lived session |
| `send_prompt_nonblocking(…)` + `finish_prompt_nonblocking` / `cancel_prompt_nonblocking` | **internal / unstable** | The interactive CLI's inline-confirmation pipeline. Signatures can change without notice. These do not take `interaction_policy=` |

A headless host calls `prompt_once` or `send_prompt`, and nothing else, to submit turns.

### One active turn per server

Each named server has **one active turn slot**, and there is **no queue**.

- A turn starts when an entry point takes the per-server lock and installs the turn slot. It ends when the entry point's `finally` clears the slot, on every outcome.
- Both public entry points take the lock in **fail-fast** mode. A second concurrent `send_prompt` or `prompt_once` on the same server raises `AcpClientError(code=SERVER_BUSY)` at once. It never blocks a worker thread behind a slow turn.
- `timeout=` covers the RPC wait (and the handshake RPCs, when the call has to connect first). No time is spent waiting for the lock, because the lock never waits. For a non-interactive turn, `timeout=None` uses the server's `requestTimeoutMs`.
- To wait for capacity, poll `readiness(name)` or `get_status()` and retry. The manager never queues for you.

| Situation | Result |
|-----------|--------|
| Two `send_prompt` calls, same server | The second raises `SERVER_BUSY` |
| Two `prompt_once` calls, same server | The second raises `SERVER_BUSY` |
| `prompt_once` while a `send_prompt` turn runs | `prompt_once` raises `SERVER_BUSY` |
| `cancel_turn` during a non-interactive turn | The latched interaction error is dropped. The prompt RPC runs to completion and returns normally |
| `prompt_once` while a long-lived client exists | Reuses that client. `stop_process=True` does not stop the subprocess |
| Any turn from another thread while `reserve_for_login(name)` is held | `SERVER_BUSY` |

`SERVER_BUSY` is a backpressure signal from the caller's side. It is never written to `last_error` (see [Appendix D.5](/en/appendix/d-error-codes#d-5-state-vs-error-contract-headless)).

### Non-interactive turns

A daemon has no user to ask. With `interactive=False` (the default of `prompt_once`, not of `send_prompt`), the manager answers `session/request_permission` and `_agentao.cn/ask_user` itself, by policy. The handle never enters `WAITING_FOR_USER` for these.

| Policy `mode` | `session/request_permission` | `_agentao.cn/ask_user` |
|---------------|------------------------------|------------------------|
| `reject_all` (default) | Rejected. The turn raises `AcpInteractionRequiredError` | Error reply. The turn raises `AcpInteractionRequiredError` |
| `accept_all` | Approved if the server offered an allow-type option. Otherwise rejected, and the turn raises | Error reply. The turn raises — `accept_all` cannot invent a user's answer |

The policy comes from the per-call `interaction_policy=` if given, else from the server's `nonInteractivePolicy` (see [3.4.5](#per-call-policy-override)). The manager resolves it once, at turn start.

**Latching.** A rejected request does not abort the turn. The `session/prompt` RPC keeps running until the server ends it. The manager keeps the **first** rejected request, and raises it as `AcpInteractionRequiredError` after the RPC ends. If the host calls `cancel_turn` during the turn, the cancel wins and the turn returns normally.

### Status fields

`get_status()` returns one frozen `ServerStatus` per configured server. A `prompt_once` ephemeral client adds no row of its own. While it runs, the server's row shows `has_active_turn` and the ephemeral session id.

| Field | Meaning |
|-------|---------|
| `server` | The name in `.agentao/acp.json` |
| `state` | The `ServerState` value as a string. The primary readiness signal |
| `pid` | OS process id while the subprocess runs, else `None` |
| `has_active_turn` | `True` for the whole life of a turn in the manager's turn slot, including its interaction phase. It comes from the turn slot, not the handle state, so a handle in `WAITING_FOR_USER` does not flip it to `False` |
| `active_session_id` | The current `sessionId` of the long-lived client, or of the ephemeral client of a running `prompt_once`. `None` until a session exists. Set whether or not a turn runs |
| `last_error` | The latest error raised out of a public entry point. If the manager has none, it falls back to the handle's own error (for example a `start_server` failure) |
| `last_error_at` | UTC-aware `datetime`, taken when the manager **stored** the error, not when it was raised. `None` when `last_error` came from the handle fallback |
| `inbox_pending` | Messages in the shared inbox from this server |
| `interaction_pending` | Unresolved permission or input requests from this server |
| `config_warnings` | Reserved for non-fatal per-server config deprecations. Always empty in the current release |

Read `state` (or `readiness()`) first to gate, then `last_error` for diagnosis. `last_error` is sticky across successful turns. The full rules are in [Appendix D.5](/en/appendix/d-error-codes#d-5-state-vs-error-contract-headless).

### Readiness classes

`readiness(name)` returns one of four strings. `is_ready(name)` is `readiness(name) == "ready"`. Prefer these to string-matching on `state`.

| Class | When |
|-------|------|
| `"ready"` | `state` is `ready`, no turn slot is active, and the process is alive. Safe to submit |
| `"busy"` | A turn slot is active, or `state` is `busy` / `waiting_for_user`, and the process is alive. A submit now raises `SERVER_BUSY` |
| `"failed"` | The server is sticky-fatal. Or `state` is `failed` and the process never started or has exited. Or the process just died and the death classifies as fatal. Needs `restart_server` / `start_server` |
| `"not_ready"` | `state` is `configured`, `starting`, `initializing`, `stopping` or `stopped`. Also: `state` is `failed` but the process is still alive — a per-call error such as a rejected `cwd` override, which the next call can clear. Also: the process just died with a recoverable classification — the next submit rebuilds it |

`readiness()` and `get_status()` are not purely passive. If they find a dead process, they classify the death and evict the dead client, so the answer reflects the real state. A poll never charges the restart budget. Source: [`agentao/acp_client/manager/status.py`](https://github.com/jin-bo/agentao/blob/main/agentao/acp_client/manager/status.py).

Out of scope for this contract: multi-dimension policies (per-tool or timeout rules), auto-respawn after a fatal death, and a socket or TCP daemon transport. The runtime stays on stdio.

---

Next: [3.5 Zed / IDE integration →](./5-zed-ide-integration)
