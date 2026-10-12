# 2.6 Cancellation & Timeouts

> **What you'll learn**
> - The three mechanisms that bound runtime: `CancellationToken` / `asyncio.wait_for` / `max_iterations`
> - How to wire client disconnects and "Stop" buttons to a token
> - Why `max_iterations` alone is insufficient and what to layer on top

`chat()` is a synchronous call that may last minutes — tool loops, LLM streaming, MCP sub-processes. If your host can't stop it mid-flight, you can't offer "Stop" buttons, enforce SLAs, or honor cancellation from client connections. This section is about the three mechanisms that bound runtime, ordered from most-to-least granular.

## 2.6.1 Three bounding mechanisms

| Mechanism | Scope | Who triggers | Response time |
|-----------|-------|--------------|---------------|
| `CancellationToken` | Cooperative, any point | Host code | **Next check** (sub-second for streaming, up to a tool's runtime for tools) |
| `max_iterations` | Tool-call loop count | Agentao itself | Only after N tool rounds |
| Thread-level kill | Last resort | Host supervisor | Immediate but **unsafe** — leaks MCP subprocesses |

Use `CancellationToken` by default. Use `max_iterations` to cap runaway loops. Avoid thread kills.

## 2.6.2 `CancellationToken` — the right way

```python
from agentao.cancellation import CancellationToken, AgentCancelledError

token = CancellationToken()
reply = agent.chat("Crawl the repo and summarize", cancellation_token=token)
```

API:

```python
class CancellationToken:
    def cancel(self, reason: str = "user-cancel") -> None   # idempotent
    def check(self) -> None                                 # raises AgentCancelledError if cancelled
    @property
    def is_cancelled(self) -> bool
    @property
    def reason(self) -> str
```

Internally it wraps a `threading.Event`, so it's safe to call `cancel()` from any thread.

### What `chat()` does on cancellation

`chat()` **does not raise** by default. If the token fires, it:

1. Finishes whatever sub-operation is mid-flight (one LLM chunk, one tool call)
2. Returns the string `"[Cancelled: <reason>]"`

This means your caller recognizes cancellation by **prefix inspection**, not exception handling:

```python
reply = agent.chat(msg, cancellation_token=token)
if reply.startswith("[Cancelled:"):
    # clean UI state, don't treat as real assistant output
    return
```

### Where cancellation is checked

Cancellation propagates through the call stack on a **cooperative** basis — checkpoints are planted at the boundaries between work units:

| Checkpoint | Notes |
|------------|-------|
| Before each LLM streaming call | Cancels mid-thought |
| After each streaming chunk | Streaming stops within one chunk |
| Before every tool call dispatch | Tools that haven't started won't run |
| Inside long tools (shell, web fetch) | Best-effort — some external I/O can't be interrupted |
| MCP forward | MCP subprocess requests are sent; cancellation stops *listening* but the remote server may still do work |

A running foreground shell command **is** killed: the shell tool watches the token and kills the command's whole process tree. A background command (`is_background`) returns at once and is not tied to the turn, so a cancel does not stop it. The shell tool also has its own `timeout` argument, an inactivity timeout of 120 s by default ([6.7](/en/part-6/7-resource-concurrency#control-4-·-tool-timeout)).

## 2.6.3 Wiring from FastAPI / HTTP disconnect

When a client disconnects (closed connection), cancel the turn:

```python
import asyncio

from fastapi import FastAPI, Request
from agentao.cancellation import CancellationToken

@app.post("/chat/{session_id}")
async def chat_endpoint(session_id: str, message: str, request: Request):
    agent, lock = await get_or_create(session_id, ...)
    token = CancellationToken()

    async def watch_disconnect():
        # poll until client goes away, then cancel
        while not await request.is_disconnected():
            await asyncio.sleep(0.5)
        token.cancel("client-disconnected")

    watcher = asyncio.create_task(watch_disconnect())
    try:
        async with lock:
            reply = await agent.arun(message, cancellation_token=token)
    finally:
        watcher.cancel()

    return {"reply": reply}
```

Key points:

- `CancellationToken` is created **per turn**, not per session — reusing a token across turns means the second turn already looks cancelled
- The watcher task must be cancelled in `finally`, otherwise it keeps running after the response is sent

## 2.6.4 Wiring from a "Stop" button

Expose `token.cancel()` through a second endpoint keyed by session:

```python
_active_tokens: dict[str, CancellationToken] = {}

@app.post("/chat/{session_id}")
async def chat_endpoint(session_id: str, message: str):
    agent, lock = await get_or_create(session_id, ...)
    token = CancellationToken()
    _active_tokens[session_id] = token
    try:
        async with lock:
            reply = await agent.arun(message, cancellation_token=token)
    finally:
        _active_tokens.pop(session_id, None)
    return {"reply": reply}

@app.post("/chat/{session_id}/cancel")
async def cancel_endpoint(session_id: str):
    token = _active_tokens.get(session_id)
    if token:
        token.cancel("user-stop-button")
    return {"ok": True}
```

If `/cancel` is called when no turn is running, it's a no-op — that's fine.

## 2.6.5 Hard timeouts

A timeout is a cancellation on a timer. Wrap the call yourself:

```python
import asyncio

async def chat_with_timeout(agent, msg: str, seconds: float) -> str:
    try:
        return await asyncio.wait_for(agent.arun(msg), timeout=seconds)
    except asyncio.TimeoutError:
        return "[Cancelled: timeout]"
```

Notes:

- When the timer fires, `wait_for` cancels the task that awaits `arun()`. `arun()` then cancels the turn's token and waits up to 5 s for the turn to finish (history backfilled, `TURN_END` emitted) before `wait_for` raises. You need no token of your own
- A turn that is still running after those 5 s, such as a blocking tool that does not check the token, keeps running in the background
- One turn at a time per agent: `chat()` / `arun()` on an agent whose previous turn is still running raises `agentao.runtime.turn.TurnInProgressError` instead of sharing `agent.messages` with it. After a timeout, wait for that turn's `TURN_END` before starting the next
- Don't use `asyncio.wait_for(asyncio.to_thread(agent.chat, msg), ...)`. Cancelling it does not stop the thread, and the turn runs on the loop's default executor ([2.3](./3-lifecycle#async-hosts-arun-and-the-turn-pool))
- The deadline is not exact: after the timer fires, `arun()` waits up to 5 s more for the turn to clean up. Use this pattern for a wall-clock limit, and `max_iterations` for a limit on tool rounds

## 2.6.6 `max_iterations` — the structural cap

```python
agent.chat("Do 20 things", max_iterations=20)
```

- Counts **tool-call rounds**, not elapsed time
- Default is 100 — that's already generous; lower it for cost control
- On exceeding: Transport's `on_max_iterations()` fires (returning `True` lets the agent continue, `False` stops). See [4.6 Max-Iterations Fallback](/en/part-4/6-max-iterations).

If you're paying per token, `max_iterations=20-30` is a good default for a chat UI — your users rarely need 100 tool rounds in one turn, and a runaway loop at $0.50/round adds up fast.

## 2.6.7 Timeouts on tools and MCP

Two other places you can bound work:

- **Shell tool**: each command has an inactivity timeout, the tool's `timeout` argument (120 s by default). Any new output restarts the timer. On timeout the tool kills the command's whole process tree (`SIGKILL` to the process group; `taskkill /T` on Windows). See [6.2](/en/part-6/2-shell-sandbox).
- **MCP request timeout**: each MCP server has a `timeout` field (default 60 s). See [5.3 MCP](/en/part-5/3-mcp).

These are lower-layer hard kills — they complement, not replace, `CancellationToken`. A hung MCP server will still be killed by its own timeout regardless of whether the token fires.

## 2.6.8 Checklist

Before shipping a turn-based UI:

- [ ] Every `chat()` call has a `CancellationToken`
- [ ] Recognize `"[Cancelled: ...]"` prefix on the caller side
- [ ] A client disconnect triggers `token.cancel("client-disconnected")`
- [ ] A "Stop" button triggers `token.cancel("user-stop-button")`
- [ ] A per-turn timeout guard exists (either via `wait_for` or a background watcher)
- [ ] `max_iterations` is tuned down from 100 if you pay per call

## TL;DR

- **Three bounding mechanisms, layered**: `CancellationToken` (caller-driven, granular) · `asyncio.wait_for` (hard wall-clock) · `max_iterations` (loop cap, last resort).
- `chat()` does **not** raise on cancel — it returns `"[Cancelled: <reason>]"`. Detect the prefix at the call site.
- Wire a token to: client disconnect events, "Stop" UI buttons, parent-task cancellation. Pass it on every `chat()`.
- `max_iterations` alone isn't enough — a single tool call can hang indefinitely. Always pair it with `wait_for` or a watcher.

---

Next: [2.7 FastAPI / Flask embedding →](./7-fastapi-flask-embed)
