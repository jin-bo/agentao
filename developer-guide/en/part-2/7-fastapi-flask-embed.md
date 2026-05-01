# 2.7 FastAPI / Flask Embedding — Production Template

> **What you'll learn**
> - A copy-paste-ready FastAPI + SSE streaming template (modern async, recommended)
> - A Flask + long-polling alternative for WSGI deployments
> - How session pool, cancellation, auth, and structured errors wire together

This section is a **copy-paste-ready** template for exposing Agentao through an HTTP API. Two flavors: **FastAPI + SSE streaming** (modern async, recommended) and **Flask + long-polling** (when you're stuck with WSGI). Both include session pooling, cancellation wiring, authentication, and structured errors.

These templates consolidate the patterns from [2.3 lifecycle](./3-lifecycle), [2.4 session state](./4-session-state), and [2.6 cancellation](./6-cancellation-timeouts). If any primitive here is unfamiliar, jump back.

::: tip Runnable minimum-shape sample
[`examples/fastapi-background/`](https://github.com/jin-bo/agentao/tree/main/examples/fastapi-background) is the offline-smoke companion: a FastAPI route + asyncio background task with one `Agentao` per request, runnable with `uv sync --extra dev && PYTHONPATH=. uv run pytest tests/`. No `OPENAI_API_KEY` needed — uses a fake LLM. Read this chapter for the production template; clone the sample to see the pieces wired together with passing tests.

For the full production blueprint with SSE streaming + session pool + auth, see [`examples/saas-assistant/`](https://github.com/jin-bo/agentao/tree/main/examples/saas-assistant) (Part 7.1).
:::

## 2.7.1 FastAPI + SSE (recommended)

### What you get

- `POST /chat/{session_id}` — streams tokens to the client via Server-Sent Events
- `POST /chat/{session_id}/cancel` — stops an in-flight turn
- `DELETE /session/{session_id}` — releases MCP subprocesses for one session
- Per-session lock (no two concurrent turns on the same agent)
- Per-tenant working directory (memory isolation)
- Bearer-token auth
- Graceful shutdown (closes all agents)

### Full code

```python
"""app.py — FastAPI + Agentao + SSE streaming."""
from __future__ import annotations

import asyncio
import json
import os
from asyncio import Lock, to_thread
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Tuple

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from agentao import Agentao
from agentao.cancellation import CancellationToken
from agentao.transport import SdkTransport
from agentao.transport.events import AgentEvent, EventType


# --------------------------------------------------------------------------
# Session pool
# --------------------------------------------------------------------------

class SessionPool:
    def __init__(self, root: Path):
        self.root = root
        self._sessions: Dict[str, Tuple[Agentao, Lock]] = {}
        self._mu = Lock()

    async def get(self, session_id: str, tenant: str) -> Tuple[Agentao, Lock]:
        async with self._mu:
            entry = self._sessions.get(session_id)
            if entry is None:
                workdir = self.root / tenant
                workdir.mkdir(parents=True, exist_ok=True)
                agent = Agentao(working_directory=workdir)
                entry = (agent, Lock())
                self._sessions[session_id] = entry
            return entry

    async def close(self, session_id: str) -> None:
        async with self._mu:
            entry = self._sessions.pop(session_id, None)
        if entry:
            await to_thread(entry[0].close)

    async def close_all(self) -> None:
        async with self._mu:
            items = list(self._sessions.items())
            self._sessions.clear()
        for _, (agent, _lock) in items:
            await to_thread(agent.close)


# --------------------------------------------------------------------------
# App setup + graceful shutdown
# --------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = SessionPool(Path(os.environ.get("AGENTAO_ROOT", "/app/tenants")))
    app.state.active_tokens: Dict[str, CancellationToken] = {}
    yield
    await app.state.pool.close_all()

app = FastAPI(lifespan=lifespan)


def auth(authorization: str | None = Header(None)) -> str:
    """Return tenant id from a Bearer token, or 401."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization.removeprefix("Bearer ")
    # Replace with your real lookup (JWT / DB / …)
    tenant = verify_token(token)
    if tenant is None:
        raise HTTPException(401, "invalid token")
    return tenant


# --------------------------------------------------------------------------
# /chat — SSE streaming
# --------------------------------------------------------------------------

@app.post("/chat/{session_id}")
async def chat_endpoint(
    session_id: str,
    request: Request,
    tenant: str = Depends(auth),
):
    body = await request.json()
    message = body["message"]

    pool: SessionPool = request.app.state.pool
    tokens: Dict[str, CancellationToken] = request.app.state.active_tokens

    agent, lock = await pool.get(session_id, tenant)
    token = CancellationToken()
    tokens[session_id] = token

    # Transport collects events into an asyncio queue for SSE relay.
    queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_event(event: AgentEvent) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    transport = SdkTransport(on_event=on_event)
    agent.transport = transport  # hot-swap is safe between turns

    async def watch_disconnect():
        while not await request.is_disconnected():
            await asyncio.sleep(0.5)
        token.cancel("client-disconnected")

    async def run_chat():
        async with lock:
            try:
                return await to_thread(agent.chat, message, cancellation_token=token)
            finally:
                await queue.put(None)  # sentinel → close stream

    async def sse_stream():
        watcher = asyncio.create_task(watch_disconnect())
        chat_task = asyncio.create_task(run_chat())
        try:
            while True:
                ev = await queue.get()
                if ev is None:
                    break
                data = {"type": ev.type.value, "data": ev.data}
                yield f"data: {json.dumps(data)}\n\n"
            reply = await chat_task
            yield f"data: {json.dumps({'type': 'done', 'reply': reply})}\n\n"
        finally:
            watcher.cancel()
            tokens.pop(session_id, None)

    return StreamingResponse(sse_stream(), media_type="text/event-stream")


# --------------------------------------------------------------------------
# Ancillary endpoints
# --------------------------------------------------------------------------

@app.post("/chat/{session_id}/cancel")
async def cancel_endpoint(session_id: str, tenant: str = Depends(auth)):
    token = app.state.active_tokens.get(session_id)
    if token:
        token.cancel("user-stop-button")
    return {"ok": True}

@app.delete("/session/{session_id}")
async def end_session(session_id: str, tenant: str = Depends(auth)):
    await app.state.pool.close(session_id)
    return {"ok": True}


# --------------------------------------------------------------------------
# Replace with your real auth
# --------------------------------------------------------------------------

def verify_token(token: str) -> str | None:
    # ...lookup in JWT / DB / API gateway...
    return "demo-tenant" if token == "dev" else None
```

Run it:

```bash
uv run uvicorn app:app --host 0.0.0.0 --port 8000
```

Test streaming:

```bash
curl -N -X POST http://localhost:8000/chat/s-1 \
  -H "Authorization: Bearer dev" -H "Content-Type: application/json" \
  -d '{"message":"list 3 files in /tmp"}'
```

### What each section does

| Block | Responsibility |
|-------|----------------|
| `SessionPool` | Caches `(agent, lock)` per session, creates per-tenant workdir |
| `lifespan` | Closes all agents on graceful shutdown — **critical** for MCP cleanup |
| `auth` dep | Returns tenant id from a Bearer token; use JWT/OAuth in production |
| `SdkTransport(on_event=…)` | Bridges agent events into an asyncio queue via `call_soon_threadsafe` |
| `watch_disconnect` | Cancels the turn if the client closes the connection |
| `sse_stream` | Pumps events as SSE frames, then sends a final `{type:"done", reply: …}` |

### Notes

- `on_event` runs in the **agent's thread**, not the event loop. Always use `loop.call_soon_threadsafe` to hand off.
- `SessionPool` uses simple dict + asyncio.Lock. For production, add TTL eviction and a per-tenant session cap; see [Part 7](/en/part-7/).
- This template doesn't persist messages. For crash recovery, plug in the `save_session` / `load_session` from [2.4](./4-session-state).

## 2.7.2 Flask + long-polling (for WSGI environments)

If you're on Gunicorn/uWSGI, FastAPI isn't an option. Flask can do streaming too (via generators), but the SSE experience is rougher because WSGI has no native async.

### Key code

```python
"""wsgi_app.py — Flask + Agentao."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from queue import Queue, Empty

from flask import Flask, Response, request, abort, stream_with_context

from agentao import Agentao
from agentao.cancellation import CancellationToken
from agentao.transport import SdkTransport


# One pool per worker process — each Gunicorn worker has its own.
_sessions: dict[str, tuple[Agentao, threading.Lock]] = {}
_active_tokens: dict[str, CancellationToken] = {}

app = Flask(__name__)


def _get_agent(session_id: str, tenant: str) -> tuple[Agentao, threading.Lock]:
    if session_id not in _sessions:
        workdir = Path(f"/app/tenants/{tenant}")
        workdir.mkdir(parents=True, exist_ok=True)
        agent = Agentao(working_directory=workdir)
        _sessions[session_id] = (agent, threading.Lock())
    return _sessions[session_id]


def _authenticate() -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        abort(401)
    tenant = verify_token(auth.removeprefix("Bearer "))
    if not tenant:
        abort(401)
    return tenant


@app.post("/chat/<session_id>")
def chat(session_id: str):
    tenant = _authenticate()
    message = request.json["message"]
    agent, lock = _get_agent(session_id, tenant)
    token = CancellationToken()
    _active_tokens[session_id] = token

    queue: Queue = Queue()
    transport = SdkTransport(on_event=lambda ev: queue.put(ev))
    agent.transport = transport

    def worker():
        try:
            with lock:
                reply = agent.chat(message, cancellation_token=token)
                queue.put(("__DONE__", reply))
        except Exception as e:
            queue.put(("__ERROR__", str(e)))

    threading.Thread(target=worker, daemon=True).start()

    @stream_with_context
    def generate():
        while True:
            try:
                item = queue.get(timeout=30)     # idle heartbeat = 30s
            except Empty:
                yield b": keep-alive\n\n"        # SSE comment, keeps conn open
                continue
            if isinstance(item, tuple) and item[0] == "__DONE__":
                yield f"data: {json.dumps({'type': 'done', 'reply': item[1]})}\n\n".encode()
                break
            if isinstance(item, tuple) and item[0] == "__ERROR__":
                yield f"data: {json.dumps({'type': 'error', 'error': item[1]})}\n\n".encode()
                break
            yield f"data: {json.dumps({'type': item.type.value, 'data': item.data})}\n\n".encode()
        _active_tokens.pop(session_id, None)

    return Response(generate(), mimetype="text/event-stream")


@app.post("/chat/<session_id>/cancel")
def cancel(session_id: str):
    _authenticate()
    token = _active_tokens.get(session_id)
    if token:
        token.cancel("user-stop-button")
    return {"ok": True}


@app.delete("/session/<session_id>")
def end_session(session_id: str):
    _authenticate()
    entry = _sessions.pop(session_id, None)
    if entry:
        entry[0].close()
    return {"ok": True}


def verify_token(t: str) -> str | None:
    return "demo-tenant" if t == "dev" else None
```

Run it:

```bash
uv run gunicorn --worker-class gthread --threads 8 --workers 2 \
    --bind 0.0.0.0:8000 wsgi_app:app
```

### Why `--worker-class gthread`?

Default Gunicorn `sync` workers handle one request per worker — unsuitable for long SSE streams. `gthread` allows many concurrent streaming requests per worker. `gevent` is also fine if you install it.

### Notes vs. FastAPI version

- **No disconnect detection**: WSGI doesn't give you a clean "client gone" hook. Rely on user-triggered cancel + a hard timeout
- **Worker-local pool**: each Gunicorn worker has its own `_sessions` dict. For multi-worker deployments, route the same `session_id` to the same worker (nginx `ip_hash`, cookie-based routing, or a reverse proxy with sticky sessions)
- **Cross-worker message persistence**: if you need multi-worker session survival, plug in the DB-backed restore from [2.4.3](./4-session-state#2-4-3-persist-restore-recipe)

## 2.7.3 Which should you pick

| Picking criterion | FastAPI + SSE | Flask + gthread |
|-------------------|---------------|------------------|
| Real-time streaming UI | ✅ preferred | ⚠️ works but more fragile |
| Client-disconnect detection | ✅ native | ❌ not reliable |
| Multi-worker horizontal scale | ✅ easier (stateless async) | ⚠️ needs sticky sessions |
| Already on WSGI stack | ❌ big migration | ✅ no migration |
| Teams used to sync code | ⚠️ learning curve | ✅ familiar |

For a new project, use FastAPI. For an existing Flask monolith, take the Flask template and plan the migration for later.

## 2.7.4 Next steps

- Persist messages across restarts: [2.4 session state](./4-session-state)
- Swap models at runtime: [2.5 runtime LLM switch](./5-runtime-llm-switch)
- Wire the SSE stream into a React UI: [Part 4](/en/part-4/) (coming soon)
- Production concerns (observability, rate-limiting, sandboxing): [Part 6](/en/part-6/) and [Part 7](/en/part-7/)

## TL;DR

- **FastAPI + SSE** is the recommended modern path; **Flask + long-polling** when you're stuck with WSGI.
- Session pool is keyed by `(tenant_id, session_id)` with TTL eviction — never share an agent across tenants.
- Always wire `CancellationToken` to client disconnects (FastAPI) or session timeouts (Flask).
- Errors as structured JSON `{code, message, details}` — never expose stack traces over the wire.

---

→ Move on to [Part 3 · ACP Protocol Embedding](/en/part-3/) for the cross-language path.
