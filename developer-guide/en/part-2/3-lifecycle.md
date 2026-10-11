# 2.3 Lifecycle

> **What you'll learn**
> - The 4-step standard lifecycle (`Agentao()` → `chat()` → … → `close()`)
> - Why a single agent isn't thread-safe and how to serialize correctly
> - A FastAPI template that handles concurrent sessions without cross-talk

One `Agentao` instance = one conversation. Understanding its lifecycle is essential for production embedding.

## Standard lifecycle

```python
agent = Agentao(...)          # 1. Construct (open MCP, load skills)
reply1 = agent.chat("hello")   # 2. First turn
reply2 = agent.chat("continue")# 3. Same session, next turn
# ...
agent.close()                  # 4. Release MCP connections, event loop
```

**Core invariant**: the same `agent` object spans the whole session. Don't rebuild per turn — you'll lose context.

## `chat()` in detail

```python
def chat(
    self,
    user_message: str,
    max_iterations: int = 100,
    cancellation_token: Optional[CancellationToken] = None,
) -> str
```

| Param | Purpose |
|-------|---------|
| `user_message` | This turn's user input |
| `max_iterations` | Cap on tool-call loops (prevents infinite runs) |
| `cancellation_token` | External cancellation handle (see 2.6) |

**Return value**: the agent's final text reply.

**Special returns**:
- `"[Interrupted by user]"` — `KeyboardInterrupt` caught. History gets one assistant message: the text the model had streamed before the interrupt, then `[Interrupted]` — or `[Interrupted]` alone when the interrupt did not land in a stream (the text of an earlier call in the turn is already in history)
- `"[Cancelled: <reason>]"` — cancellation token fired
- `"[Blocked by hook] ..."` / `"[Hook stopped] ..."` — plugin hook intercepted

`chat()` does **not** raise on these — it returns gracefully. Your caller must recognize the prefixes.

**Blocking**: `chat()` is synchronous. During streaming it calls `transport.emit(...)` repeatedly. Async hosts call `await agent.arun(...)` instead ([below](#async-hosts-arun-and-the-turn-pool)).

## `add_message()`

```python
agent.add_message("user", "restore a historical message")
```

Use to **inject history before calling `chat()`** — e.g. restoring from a database:

```python
for row in db.load_conversation(session_id):
    agent.add_message(row.role, row.content)
# Now chat normally
reply = agent.chat("Given the above, continue.")
```

⚠️ `add_message` mutates `self.messages` directly — it does **not** trigger the LLM. Only use for history restore.

## `clear_history()`

```python
agent.clear_history()
```

Effects:
- Clears `self.messages`
- Deactivates any active skills
- Empties the todo list
- **Does not** clear memory (MemoryManager persists to SQLite across sessions)

Typical trigger: "new conversation" button in your UI.

## `close()`

```python
agent.close()
```

Always call this. It:
- Disconnects the MCP clients the agent connected itself, and stops that manager's event loop thread
- Closes the memory stores the agent opened itself
- Closes `<working_directory>/agentao.log`, which the agent holds open unless you passed `logger=` or your own `llm_client=` (Windows cannot delete an open file, so removing a temporary working directory before `close()` fails with `WinError 32`)

A manager you pass in (`mcp_manager=`, `memory_manager=`, `llm_client=`), or assign to `agent.mcp_manager` / `agent.memory_manager` later, is yours: `close()` leaves it open, since it may be shared with other agents, and you release it (`disconnect_all()`, `close()`) when you are done with it. `build_from_environment()` hands the memory manager it builds to the agent, so `close()` releases that one.

With a temporary working directory, close the agent before deleting the directory: nest `with Agentao(...)` inside `with tempfile.TemporaryDirectory()`. `logger=` keeps `agentao.log` out of the directory, but does not replace `close()`.

Without it you **leak MCP subprocesses and threads**. `Agentao` is a context manager, so `with` closes it on the way out, error or not:

```python
with Agentao(working_directory=Path("/tmp/x")) as agent:
    reply = agent.chat("hi")
```

In async code, `async with` (or `await agent.aclose()`) runs `close()` on a worker thread, so the MCP disconnect does not block the event loop. It is like `await asyncio.to_thread(agent.close)`, except that cancelling the `await` never keeps `close()` from running:

```python
from agentao.embedding import build_from_environment

async def main() -> None:
    async with build_from_environment(working_directory=Path("/tmp/x")) as agent:
        reply = await agent.arun("hi")
```

End the agent's turns before closing: neither form waits for or cancels a turn in progress. Wrapping `await agent.arun(...)` in `async with` meets this even when the task is cancelled (a client disconnect, a timeout): the cancelled `arun()` cancels its turn and waits up to 5 seconds for it to stop before the block exits. A turn still running after that, such as a blocking tool that does not check the cancellation token, is closed while it runs; so is one whose wait a second cancel cuts short. Calling `close()` again is safe, so an explicit `close()` inside the block does no harm. A `try/finally` that calls `close()` still works when the agent outlives one block, such as a per-session cache.

With several agents in one process, `agentao.log` records go to the most recently built agent that is still open. Closing it hands the log back to the next one. See [6.6](/en/part-6/6-observability#several-agents-in-one-process).

## Async hosts: `arun()` and the turn pool

`arun()` takes the same arguments as `chat()` (`user_message`, `max_iterations`, `cancellation_token`, `images`) and runs the same turn without blocking the event loop:

```python
async def handle_request(req):
    async with build_from_environment(working_directory=Path(req.workdir)) as agent:
        return await agent.arun(req.prompt)
```

- **The turn runs on Agentao's own thread pool**, not on the loop's default executor. The threads are named `agentao-arun-*`. The pool has `min(32, os.cpu_count() + 4)` workers, the size asyncio would pick. It is shared by all agents in the process and built on the first `arun()`, so a sync-only host never pays for it.
- **Why not the default executor.** A turn holds its worker for the whole turn. Partway through, it can wait on an async tool that runs back on your loop. If turns filled the default executor, anything else on the loop that needs a worker would starve. That includes `loop.getaddrinfo`, which every `httpx.AsyncClient` connect to a hostname uses. The turns would then wait on work that can never start ([`_get_arun_pool`](https://github.com/jin-bo/agentao/blob/main/agentao/agent.py)).
- **Sizing.** `loop.set_default_executor(...)` does not change how many turns run at once. To bound concurrent turns, use your own semaphore around `arun()`.
- **Cancellation.** Cancelling the awaiting task (a timeout, a client disconnect) cancels the turn through its token, and `arun()` waits up to 5 seconds for the turn to clean up. You can also pass a `CancellationToken` and cancel it from elsewhere ([2.6](./6-cancellation-timeouts)).
- **Don't wrap `chat()` in `asyncio.to_thread`.** That puts the turn on the default executor, which is the problem above.

## Switching LLMs at runtime

Two methods swap models or credentials mid-session:

```python
# Full swap (key + endpoint + model)
agent.set_provider(
    api_key="sk-new",
    base_url="https://api.deepseek.com",
    model="deepseek-chat",
)

# Model only (keep credentials)
agent.set_model("gpt-5.4")

# Query current
current = agent.get_current_model()  # -> "gpt-5.4"
```

Uses:
- User picks from a "model" dropdown in your UI
- A/B-test different models on the same session
- Route cheap model for simple questions, expensive for complex

⚠️ Swapping does **not** clear history — the next `chat()` continues on the same context with the new model.

## Introspection

```python
# Number of messages (excludes system prompt; it's rebuilt each turn)
len(agent.messages)

# LLM-compressed summary (for debugging or persistence)
summary = agent.get_conversation_summary()

# Working directory (read-only)
agent.working_directory     # Path object

# Currently active skills
agent.skill_manager.active_skills  # dict
```

## Concurrency model

**A single `Agentao` instance is not thread-safe.** The same instance cannot serve two concurrent `chat()` calls.

Right approach: **one agent per session**.

| Host type | Pattern |
|-----------|---------|
| Sync web (Flask / WSGI) | Request → construct or pull from pool → `chat()` → return / close |
| Async web (FastAPI / asyncio) | `await agent.arun(msg)` |
| Background batch | One agent per job, sequential |
| Multi-tenant SaaS | Cache by tenant, TTL-evict and `close()` |

## Full example: FastAPI + async + session pool

```python
from asyncio import Lock
from pathlib import Path
from fastapi import FastAPI, HTTPException
from agentao import Agentao
from agentao.embedding import build_from_environment
from agentao.transport import SdkTransport

app = FastAPI()

# session_id -> (agent, lock)
_sessions: dict = {}

async def get_or_create(session_id: str, workdir: Path) -> tuple[Agentao, Lock]:
    if session_id not in _sessions:
        agent = build_from_environment(   # credentials from the env / .env
            working_directory=workdir,
            transport=SdkTransport(),  # no event subscribe for simplicity
        )
        _sessions[session_id] = (agent, Lock())
    return _sessions[session_id]

@app.post("/chat")
async def chat_endpoint(session_id: str, message: str):
    try:
        agent, lock = await get_or_create(session_id, Path(f"/tmp/{session_id}"))
    except Exception as e:
        raise HTTPException(500, str(e))

    async with lock:               # serialize same-session turns
        reply = await agent.arun(message)
    return {"reply": reply}

@app.delete("/session/{session_id}")
async def end_session(session_id: str):
    entry = _sessions.pop(session_id, None)
    if entry:
        agent, lock = entry
        try:
            async with lock:       # wait for the request holding it (a cancelled
                pass               # request releases it while its turn still runs)
        finally:                   # close even if this request is cancelled while
            await agent.aclose()   # waiting: the entry is already popped
    return {"ok": True}
```

Key points:

- The `Lock` **serializes** turns of the same session (never two concurrent `chat()` on one agent)
- `arun()` keeps the turn off the event loop, on Agentao's own pool
- The explicit `DELETE` endpoint triggers `close()` for proper MCP cleanup

For production you still need TTL eviction, memory caps, crash recovery — covered in [Part 7](/en/part-7/).

## TL;DR

- **One agent = one stateful session.** Don't rebuild per turn — you'll lose context.
- `chat()` is **blocking and not thread-safe**. Async hosts use a per-session lock + `await agent.arun(...)`, which runs on Agentao's own pool, not the loop's default executor.
- Always close the agent: `with` / `async with`, or `close()` / `await aclose()` — leaks MCP subprocesses + DB handles otherwise.
- `clear_history()` resets `messages` only; **memory DB persists** by design.
- Swap models at runtime via `set_provider()` / `set_model()`; history continues unchanged.

→ More parameters and extensibility: [Part 5 · Extend Agent Behavior](/en/part-5/)
