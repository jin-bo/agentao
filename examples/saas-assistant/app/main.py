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
from asyncio import Lock
from contextlib import aclosing, asynccontextmanager
from pathlib import Path
from typing import Dict, Set

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from agentao import Agentao  # type alias for the cache values
from agentao.embedding import build_from_environment
from agentao.host import CancellationToken, TextDelta
from agentao.permissions import PermissionEngine, PermissionMode

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

            # Not ``read-only``: that mode denies every tool whose
            # ``is_read_only`` is False — CreateTaskTool included — before
            # any rule is consulted. Deny the built-in writers by rule
            # instead; user rules are evaluated ahead of the
            # workspace-write preset, so these win, and create_task keeps
            # going through ``requires_confirmation``.
            # ``save_memory`` is on the list too: it writes
            # ``.agentao/memory.db`` under this tenant's directory, and
            # read-only mode used to deny it along with the rest.
            engine = PermissionEngine(project_root=workdir, rules=[
                {"tool": "write_file", "action": "deny"},
                {"tool": "replace", "action": "deny"},
                {"tool": "run_shell_command", "action": "deny"},
                {"tool": "save_memory", "action": "deny"},
            ])
            engine.set_mode(PermissionMode.WORKSPACE_WRITE)

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
            agent, lock = entry
            try:
                async with lock:  # let the turn holding it finish:
                    pass          # closing does not wait for one
            finally:              # the entry is popped; close even if cancelled
                await agent.aclose()

    async def close_all(self) -> None:
        async with self._mu:
            items = list(self._sessions.items())
            self._sessions.clear()
        # Concurrently: each close waits on its own MCP disconnect. Shielded:
        # a cancelled gather cancels the closes that have not started yet.
        await asyncio.shield(
            asyncio.gather(
                *(agent.aclose() for _, (agent, _lock) in items),
                return_exceptions=True,  # close() logs its own errors
            )
        )


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
    app.state.active_tokens: Dict[str, Set[CancellationToken]] = {}
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
    tokens: Dict[str, Set[CancellationToken]] = request.app.state.active_tokens

    agent, lock = await pool.get(key, user.tenant_id)
    token = CancellationToken()

    async def watch_disconnect():
        while not await request.is_disconnected():
            await asyncio.sleep(0.5)
        token.cancel("client-disconnected")

    async def sse_stream():
        # Registered here, not in the endpoint: a response whose body never
        # starts never runs this generator's ``finally``, and the token would
        # stay in the set. A set per session: the stop button cancels the
        # running turn and any request still waiting for the session lock.
        tokens.setdefault(key, set()).add(token)
        watcher = asyncio.create_task(watch_disconnect())
        try:
            # One turn per session at a time. Without the lock a second
            # request would get TurnInProgressError instead of waiting.
            async with lock:
                if token.is_cancelled:
                    # Stopped while waiting for the lock. Starting the turn
                    # anyway would still write this prompt and a
                    # "[Cancelled: …]" reply into the session's history.
                    done = {"reply": "", "status": "cancelled",
                            "is_answer": False, "incomplete_reason": None}
                    yield f"event: done\ndata: {json.dumps(done)}\n\n"
                    return
                # ``astream`` subscribes to the agent's transport; it never
                # replaces it, so tool confirmations and replay are unchanged.
                # ``aclosing`` cancels the turn if the response is abandoned.
                async with aclosing(
                    agent.astream(message, cancellation_token=token)
                ) as stream:
                    async for item in stream:
                        if isinstance(item, TextDelta):
                            payload = {"type": "llm_text", "chunk": item.text}
                            yield f"data: {json.dumps(payload)}\n\n"
                        else:
                            # The TurnOutcome. ``reply`` is the answer; the
                            # streamed chunks also carry narration before tool
                            # calls, so do not rebuild the reply from them.
                            done = {
                                "reply": item.text,
                                "status": item.status,
                                "is_answer": item.is_answer,
                                "incomplete_reason": item.incomplete_reason,
                            }
                            yield f"event: done\ndata: {json.dumps(done)}\n\n"
        finally:
            watcher.cancel()
            live = tokens.get(key)
            if live is not None:
                live.discard(token)
                if not live:
                    del tokens[key]

    return StreamingResponse(sse_stream(), media_type="text/event-stream")


@app.post("/chat/{session_id}/cancel")
async def cancel_endpoint(session_id: str, user: User = Depends(current_user)):
    key = f"{user.tenant_id}:{session_id}"
    for token in list(app.state.active_tokens.get(key, ())):
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
