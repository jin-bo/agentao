"""Blueprint A — FastAPI + SSE embed of Agentao.

Endpoints:
    POST   /chat/{session_id}         — SSE stream of the agent's turn
    POST   /chat/{session_id}/cancel  — stop the current turn
    DELETE /session/{session_id}      — close and evict the agent

Test:
    uv run uvicorn app.main:app --reload
    curl -N -X POST http://127.0.0.1:8000/chat/s-1 \\
         -H "Authorization: Bearer dev-alice" \\
         -H "Content-Type: application/json" \\
         -d '{"message":"list my projects"}'
"""
from __future__ import annotations

import asyncio
import json
import os
from asyncio import Lock, to_thread
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from agentao import Agentao  # type alias for the cache values
from agentao.cancellation import CancellationToken
from agentao.embedding import build_from_environment
from agentao.permissions import PermissionEngine, PermissionMode
from agentao.transport import SdkTransport

from .auth import User, current_user
from .tools import CreateTaskTool, ListProjectsTool


# ──────────────────────────────────────────────────────────────────────────
# Session pool
# ──────────────────────────────────────────────────────────────────────────

class SessionPool:
    def __init__(self, root: Path):
        self.root = root
        self._sessions: Dict[str, tuple[Agentao, Lock]] = {}
        self._mu = Lock()

    async def get(self, key: str, tenant_id: str) -> tuple[Agentao, Lock]:
        async with self._mu:
            entry = self._sessions.get(key)
            if entry is not None:
                return entry
            workdir = self.root / tenant_id / key.split(":", 1)[1]
            workdir.mkdir(parents=True, exist_ok=True)

            engine = PermissionEngine(project_root=workdir)
            engine.set_mode(PermissionMode.READ_ONLY)

            agent = build_from_environment(
                working_directory=workdir,
                permission_engine=engine,
            )
            agent.tools.register(ListProjectsTool(tenant_id))
            agent.tools.register(CreateTaskTool(tenant_id))
            entry = (agent, Lock())
            self._sessions[key] = entry
            return entry

    async def close(self, key: str) -> None:
        async with self._mu:
            entry = self._sessions.pop(key, None)
        if entry:
            await to_thread(entry[0].close)

    async def close_all(self) -> None:
        async with self._mu:
            items = list(self._sessions.items())
            self._sessions.clear()
        for _, (agent, _lock) in items:
            await to_thread(agent.close)


# ──────────────────────────────────────────────────────────────────────────
# App
# ──────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    app.state.pool = SessionPool(
        Path(os.environ.get("AGENTAO_ROOT",
                            str(Path(__file__).resolve().parent.parent / "data" / "tenants")))
    )
    app.state.active_tokens: Dict[str, CancellationToken] = {}
    yield
    await app.state.pool.close_all()


app = FastAPI(lifespan=lifespan, title="Agentao · SaaS assistant demo")


# ──────────────────────────────────────────────────────────────────────────
# /chat — SSE streaming
# ──────────────────────────────────────────────────────────────────────────

@app.post("/chat/{session_id}")
async def chat_endpoint(session_id: str, request: Request,
                        user: User = Depends(current_user)):
    body = await request.json()
    message = body.get("message")
    if not message:
        raise HTTPException(422, "missing 'message'")

    key = f"{user.tenant_id}:{session_id}"
    pool: SessionPool = request.app.state.pool
    tokens: Dict[str, CancellationToken] = request.app.state.active_tokens

    agent, lock = await pool.get(key, user.tenant_id)
    token = CancellationToken()
    tokens[key] = token

    queue: asyncio.Queue[Any] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_event(ev):
        loop.call_soon_threadsafe(queue.put_nowait, ev)

    agent.transport = SdkTransport(on_event=on_event)

    async def watch_disconnect():
        while not await request.is_disconnected():
            await asyncio.sleep(0.5)
        token.cancel("client-disconnected")

    async def run_chat():
        async with lock:
            try:
                return await to_thread(agent.chat, message, cancellation_token=token)
            finally:
                await queue.put(None)

    async def sse_stream():
        watcher = asyncio.create_task(watch_disconnect())
        chat_task = asyncio.create_task(run_chat())
        try:
            while True:
                ev = await queue.get()
                if ev is None:
                    break
                payload = {"type": ev.type.value, **(ev.data or {})}
                yield f"data: {json.dumps(payload)}\n\n"
            reply = await chat_task
            yield f"event: done\ndata: {json.dumps({'reply': reply})}\n\n"
        finally:
            watcher.cancel()
            tokens.pop(key, None)

    return StreamingResponse(sse_stream(), media_type="text/event-stream")


@app.post("/chat/{session_id}/cancel")
async def cancel_endpoint(session_id: str, user: User = Depends(current_user)):
    key = f"{user.tenant_id}:{session_id}"
    token = app.state.active_tokens.get(key)
    if token:
        token.cancel("user-stop-button")
    return {"ok": True}


@app.delete("/session/{session_id}")
async def end_session(session_id: str, user: User = Depends(current_user)):
    key = f"{user.tenant_id}:{session_id}"
    await app.state.pool.close(key)
    return {"ok": True}


@app.get("/healthz")
async def healthz():
    return {"ok": True}
