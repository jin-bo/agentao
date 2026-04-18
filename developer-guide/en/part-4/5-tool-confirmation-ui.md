# 4.5 Tool Confirmation UI

`confirm_tool(name, desc, args) -> bool` is the agent's safety valve. This section shows how to implement it correctly across different UI shapes.

## Core challenge: synchronous blocking

`confirm_tool` is called **synchronously** from the agent's `chat()` thread — it must return `True`/`False` before execution continues. UI code is usually async, so you must **block in the agent thread while waiting for the async response**.

```
Agent thread                        UI thread / async loop
      │                                     │
      │ call confirm_tool(...)              │
      │─────────────────────┐               │
      │                     ▼               │
      │            schedule "ask user"  ───►│   show modal
      │            block on Future          │   ↓
      │                     ▲               │   click
      │◄────────────────────┘               │
      │       Future.set_result(True)       │
      │                                     │
```

## Pattern A · CLI (terminal)

The simplest case:

```python
import readchar

def confirm_tool(name: str, desc: str, args: dict) -> bool:
    print(f"\n🔧 Tool: {name}")
    print(f"   Desc: {desc}")
    print(f"   Args: {args}")
    print("   [y] allow  [n] reject  [a] allow all")

    while True:
        k = readchar.readkey().lower()
        if k == "y": return True
        if k == "n": return False
        if k == "a":
            global _allow_all
            _allow_all = True
            return True
```

No cross-thread concern — the agent thread is the main thread, `input()` / `readchar` blocks naturally.

## Pattern B · Web modal (async backend)

A FastAPI / asyncio backend needs `confirm_tool` to push to a WebSocket and wait for the response.

### Skeleton

```python
import asyncio, uuid

class WebConfirmBridge:
    def __init__(self, ws, loop: asyncio.AbstractEventLoop, timeout=60):
        self.ws = ws
        self.loop = loop
        self.timeout = timeout
        self._pending: dict = {}

    def confirm_tool(self, name: str, desc: str, args: dict) -> bool:
        fut = asyncio.run_coroutine_threadsafe(
            self._ask(name, desc, args),
            self.loop,
        )
        try:
            return fut.result(timeout=self.timeout)
        except (asyncio.TimeoutError, TimeoutError):
            return False   # timeout = reject

    async def _ask(self, name, desc, args) -> bool:
        req_id = uuid.uuid4().hex
        inner_fut = self.loop.create_future()
        self._pending[req_id] = inner_fut
        await self.ws.send_json({
            "type": "confirm_request",
            "request_id": req_id,
            "tool": name, "description": desc, "args": args,
        })
        return await inner_fut

    def resolve(self, request_id: str, allowed: bool):
        fut = self._pending.pop(request_id, None)
        if fut and not fut.done():
            fut.set_result(allowed)
```

### Frontend

```js
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.type === "confirm_request") {
    showModal({
      title: `Allow "${msg.tool}"?`,
      body: msg.description + "\n\n" + JSON.stringify(msg.args, null, 2),
      onAllow: () => ws.send(JSON.stringify({
        type: "confirm_response", request_id: msg.request_id, allowed: true,
      })),
      onReject: () => ws.send(JSON.stringify({
        type: "confirm_response", request_id: msg.request_id, allowed: false,
      })),
    });
  }
};
```

### Why a timeout, not infinite wait

When the agent calls `confirm_tool`, the entire `chat()` loop is frozen. If the user walks away without clicking, the agent hangs forever — no progress, no heartbeat. **Always set a timeout**, and treat timeout as rejection (most conservative).

## Pattern C · Native mobile app

Mobile WebSocket connections drop constantly (screen off, app switch). Strategy:

1. **Prefer system push notifications** with actionable buttons (e.g. iOS actionable notifications)
2. **Use a long backend timeout** (say 5 min) so the user has time to resume the app
3. **On timeout, persist the call as pending** — the user can catch up on reconnect

```python
async def _ask_mobile(user_id, name, desc, args):
    req_id = uuid.uuid4().hex
    await push_service.send(user_id, {
        "title": f"Agent wants to run {name}",
        "body": desc,
        "actions": ["Allow", "Reject"],
        "data": {"request_id": req_id},
    })
    await db.save_pending_confirm(req_id, user_id, name, args)
    return await wait_for_db_update(req_id, timeout=300)
```

## Pattern D · Unattended / batch

No human in the loop → replace prompts with rules:

```python
READ_ONLY = {"read_file", "glob", "grep", "read_folder"}
ALWAYS_OK = READ_ONLY | {"save_memory", "activate_skill"}
NEVER     = {"run_shell_command"}  # fully blocked

def confirm_tool(name, desc, args):
    if name in NEVER: return False
    if name in ALWAYS_OK: return True
    if name == "write_file":
        path = args.get("path", "")
        return path.startswith("/tmp/sandbox/")
    return False  # default deny
```

For batch jobs, **leave `confirm_tool` out entirely** — `NullTransport` auto-approves everything. Only safe when you've locked the tool set and the filesystem.

## Coordinating with the Permission Engine

Agentao has two defense layers:

```
               ┌──────────────────────┐
tool call ───► │  1. PermissionEngine │──── allow/deny/ask
               │   (rule engine, fast)│
               └──────────┬───────────┘
                          │ if = ask
                          ▼
               ┌──────────────────────┐
               │  2. confirm_tool()   │ ──── your UI
               │   (human, slow)      │
               └──────────────────────┘
```

The `PermissionEngine` makes a fast decision from JSON rules. `confirm_tool` is called only when the rules say "ask". This means:

- Clearly-safe actions (reads in an allowed dir) pass silently
- Clearly-dangerous actions (disallowed commands) are blocked silently
- Edge cases (write outside project, fetch unknown domain) reach `confirm_tool`

Permission engine details: [Part 5.4](/en/part-5/) (coming soon).

## Preview events & UI priming

The `TOOL_CONFIRMATION` event fires through `emit` **before** `confirm_tool` is called — use it to prime the modal:

```python
def on_event(ev):
    if ev.type == EventType.TOOL_CONFIRMATION:
        ui.prepare_modal(ev.data["tool"], ev.data["args"])

def confirm_tool(name, desc, args):
    # The modal is already rendered and focused
    return ui.show_prepared_modal()
```

In slow Web contexts this can shave ~100 ms of first-render delay.

## Composed confirmation strategies

Production typically uses **layered** confirmation:

```python
class SmartConfirm:
    def __init__(self, user_ui, tenant_rules: dict):
        self.ui = user_ui
        self.rules = tenant_rules  # tenant_id -> {allow: [...], deny: [...]}
        self._session_allow_all = False

    def __call__(self, name, desc, args):
        # 1. Session-wide "allow all"
        if self._session_allow_all:
            return True

        # 2. Tenant denylist — always reject
        if name in self.rules.get("deny", []):
            return False

        # 3. Tenant allowlist — always allow
        if name in self.rules.get("allow", []):
            return True

        # 4. Ask the user
        resp = self.ui.ask(name, desc, args)   # "allow_once"/"allow_all"/"reject"
        if resp == "allow_all":
            self._session_allow_all = True
            return True
        return resp == "allow_once"
```

→ Next: [4.6 Max-Iterations Fallback](./6-max-iterations)
