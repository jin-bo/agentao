# 2.7 FastAPI / Flask 嵌入 — 生产级模板

本节是**可直接复制**的 HTTP API 模板。两种口味：**FastAPI + SSE 流式**（现代异步，推荐）和 **Flask + 长轮询**（还困在 WSGI 上的时候）。两份都包含会话池、取消接线、鉴权、结构化错误。

模板综合了 [2.3 生命周期](./3-lifecycle)、[2.4 会话状态](./4-session-state)、[2.6 取消与超时](./6-cancellation-timeouts) 的模式。遇到不熟悉的原语请回去查。

## 2.7.1 FastAPI + SSE（推荐）

### 你会得到

- `POST /chat/{session_id}` —— SSE 把 token 流回客户端
- `POST /chat/{session_id}/cancel` —— 中止正在跑的轮次
- `DELETE /session/{session_id}` —— 释放该 session 的 MCP 子进程
- 每 session 一把锁（同一 agent 不会并发两轮）
- 每租户一个工作目录（记忆隔离）
- Bearer token 鉴权
- 优雅关停（关闭所有 agent）

### 完整代码

```python
"""app.py —— FastAPI + Agentao + SSE 流式。"""
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
# 会话池
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
# App 装配 + 优雅关停
# --------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = SessionPool(Path(os.environ.get("AGENTAO_ROOT", "/app/tenants")))
    app.state.active_tokens: Dict[str, CancellationToken] = {}
    yield
    await app.state.pool.close_all()

app = FastAPI(lifespan=lifespan)


def auth(authorization: str | None = Header(None)) -> str:
    """从 Bearer token 解出 tenant id；失败返回 401。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization.removeprefix("Bearer ")
    tenant = verify_token(token)          # 换成你自己的 JWT/DB 查询
    if tenant is None:
        raise HTTPException(401, "invalid token")
    return tenant


# --------------------------------------------------------------------------
# /chat —— SSE 流式
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

    # Transport 把事件塞到 asyncio queue 里中转给 SSE。
    queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_event(event: AgentEvent) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    transport = SdkTransport(on_event=on_event)
    agent.transport = transport   # 两轮之间换 transport 是安全的

    async def watch_disconnect():
        while not await request.is_disconnected():
            await asyncio.sleep(0.5)
        token.cancel("client-disconnected")

    async def run_chat():
        async with lock:
            try:
                return await to_thread(agent.chat, message, cancellation_token=token)
            finally:
                await queue.put(None)  # 哨兵 → 关流

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
# 辅助端点
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
# 替换成你自己的鉴权
# --------------------------------------------------------------------------

def verify_token(token: str) -> str | None:
    # ...JWT / DB / 网关查询...
    return "demo-tenant" if token == "dev" else None
```

起服务：

```bash
uv run uvicorn app:app --host 0.0.0.0 --port 8000
```

测试流式：

```bash
curl -N -X POST http://localhost:8000/chat/s-1 \
  -H "Authorization: Bearer dev" -H "Content-Type: application/json" \
  -d '{"message":"列出 /tmp 里 3 个文件"}'
```

### 每个模块的职责

| 块 | 职责 |
|----|------|
| `SessionPool` | 按 session 缓存 `(agent, lock)`，按租户建工作目录 |
| `lifespan` | 关停时关闭所有 agent——**关键**，不然 MCP 泄漏 |
| `auth` 依赖 | 从 Bearer 解出 tenant id；生产上换 JWT/OAuth |
| `SdkTransport(on_event=…)` | 把 agent 事件通过 `call_soon_threadsafe` 送进 asyncio queue |
| `watch_disconnect` | 客户端断连时取消本轮 |
| `sse_stream` | 把事件作为 SSE 帧往外推，最后发 `{type:"done", reply: …}` |

### 注意

- `on_event` 跑在**agent 的那个线程**，不是事件循环里。必须 `loop.call_soon_threadsafe` 做交接
- `SessionPool` 用了 dict + asyncio.Lock。生产上加 TTL 淘汰 + 每租户最大会话数，参见 [Part 7](/zh/part-7/)
- 本模板不持久化消息。要扛重启，把 [2.4](./4-session-state) 的 `save_session` / `load_session` 插进来

## 2.7.2 Flask + 长轮询（WSGI 环境）

如果你跑在 Gunicorn/uWSGI 上，FastAPI 用不了。Flask 也能做流式（靠生成器），但 SSE 体验会糙一些，因为 WSGI 没有原生 async。

### 关键代码

```python
"""wsgi_app.py —— Flask + Agentao。"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from queue import Queue, Empty

from flask import Flask, Response, request, abort, stream_with_context

from agentao import Agentao
from agentao.cancellation import CancellationToken
from agentao.transport import SdkTransport


# 每个 Gunicorn worker 一个独立的池
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
                item = queue.get(timeout=30)     # 30 秒空闲心跳
            except Empty:
                yield b": keep-alive\n\n"        # SSE 注释，保持连接
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

起服务：

```bash
uv run gunicorn --worker-class gthread --threads 8 --workers 2 \
    --bind 0.0.0.0:8000 wsgi_app:app
```

### 为什么要 `--worker-class gthread`？

默认的 `sync` worker 一个 worker 一次只能处理一个请求——长 SSE 流撑不住。`gthread` 允许每 worker 并发多条流。装了 `gevent` 也行。

### 与 FastAPI 版本的差别

- **没断连检测**：WSGI 不给"客户端走了"的干净钩子。靠用户点取消 + 硬超时兜
- **Worker 本地池**：每个 Gunicorn worker 有自己的 `_sessions`。多 worker 部署要把同一个 `session_id` 路由到同一个 worker（nginx `ip_hash`、cookie 路由、反代 sticky session）
- **跨 worker 持久化**：要在 worker 之间共享会话，接 [2.4.3](./4-session-state#2-4-3-持久化-还原配方) 的 DB 落盘方案

## 2.7.3 怎么选

| 评估维度 | FastAPI + SSE | Flask + gthread |
|---------|---------------|------------------|
| 实时流式 UI | ✅ 首选 | ⚠️ 能跑，但脆一些 |
| 客户端断连检测 | ✅ 原生 | ❌ 不可靠 |
| 多 worker 水平扩缩 | ✅ 容易（无状态 async） | ⚠️ 需要 sticky session |
| 已经在 WSGI 栈上 | ❌ 迁移大 | ✅ 无迁移 |
| 团队习惯同步代码 | ⚠️ 学习曲线 | ✅ 熟悉 |

新项目用 FastAPI。已有 Flask 单体用 Flask 版，迁移排到后面。

## 2.7.4 下一步

- 跨重启持久化消息：[2.4 会话状态](./4-session-state)
- 运行时切换模型：[2.5 运行时切换 LLM](./5-runtime-llm-switch)
- 把 SSE 流接到 React UI：[Part 4](/zh/part-4/)（待上线）
- 生产关切（观测、限流、沙箱）：[Part 6](/zh/part-6/) 和 [Part 7](/zh/part-7/)

---

→ 跨语言路径见 [Part 3 · ACP 协议嵌入](/zh/part-3/)
