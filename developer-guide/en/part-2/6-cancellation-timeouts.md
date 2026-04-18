# 2.6 Cancellation & Timeouts

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

A running shell command does **not** get killed — the token just stops the next iteration. For true shell kills, wire a timeout into the shell tool itself (`.agentao/sandbox.json`).

## 2.6.3 Wiring from FastAPI / HTTP disconnect

When a client disconnects (closed connection), cancel the turn:

```python
from fastapi import FastAPI, Request
from asyncio import to_thread
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
            reply = await to_thread(agent.chat, message, cancellation_token=token)
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
            reply = await to_thread(agent.chat, message, cancellation_token=token)
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
from asyncio import to_thread, wait_for, TimeoutError
from agentao.cancellation import CancellationToken

async def chat_with_timeout(agent, msg: str, seconds: float) -> str:
    token = CancellationToken()
    try:
        return await wait_for(
            to_thread(agent.chat, msg, cancellation_token=token),
            timeout=seconds,
        )
    except TimeoutError:
        token.cancel("timeout")
        # The thread is still running; it will observe the token on its
        # next checkpoint and return "[Cancelled: timeout]". Your caller
        # already got TimeoutError, so the thread's return value is
        # ignored — but the cancel call ensures it stops soon.
        return "[Cancelled: timeout]"
```

Notes:

- `wait_for` cancels the awaiting coroutine, **not** the underlying thread. That's why we also call `token.cancel()` — otherwise the thread runs to completion and leaks CPU
- For true hard SLAs (e.g. 30s), use the above pattern. For soft SLAs, just use `max_iterations`

## 2.6.6 `max_iterations` — the structural cap

```python
agent.chat("Do 20 things", max_iterations=20)
```

- Counts **tool-call rounds**, not elapsed time
- Default is 100 — that's already generous; lower it for cost control
- On exceeding: Transport's `on_max_iterations()` fires (returning `True` lets the agent continue, `False` stops). See [Part 4](/en/part-4/) (coming soon) for the hook.

If you're paying per token, `max_iterations=20-30` is a good default for a chat UI — your users rarely need 100 tool rounds in one turn, and a runaway loop at $0.50/round adds up fast.

## 2.6.7 Timeouts on tools and MCP

Two other places you can bound work:

- **Shell tool**: default 30 s timeout per command (configurable in `.agentao/sandbox.json`). Processes that exceed the limit are `SIGTERM`-ed. See [6.2](/en/part-6/2-shell-sandbox).
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

---

Next: [2.7 FastAPI / Flask embedding →](./7-fastapi-flask-embed)
