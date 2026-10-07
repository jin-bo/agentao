# 2.7 FastAPI / Flask 嵌入 — 生产级模板

> **本节你会学到**
> - 一份可直接复制的 FastAPI + SSE 流式模板（现代异步，推荐）
> - 一份 Flask + 长轮询的 WSGI 备选
> - 会话池、取消、鉴权、结构化错误如何串成一个整体

本节是**可直接复制**的 HTTP API 模板。两种口味：**FastAPI + SSE 流式**（现代异步，推荐）和 **Flask + 长轮询**（还困在 WSGI 上的时候）。两份都包含会话池、取消接线、鉴权、结构化错误。

模板综合了 [2.3 生命周期](./3-lifecycle)、[2.4 会话状态](./4-session-state)、[2.6 取消与超时](./6-cancellation-timeouts) 的模式。遇到不熟悉的原语请回去查。

::: tip 离线可跑的最小形态样板
[`examples/fastapi-background/`](https://github.com/jin-bo/agentao/tree/main/examples/fastapi-background) 是配套的离线烟雾测试样板：FastAPI 路由 + asyncio 后台任务 + 每请求一个 `Agentao`，`uv sync --extra dev && PYTHONPATH=. uv run pytest tests/` 即跑。**不需要 `OPENAI_API_KEY`** —— 用 fake LLM。本章读生产模板，clone 样板看接线和通过的测试。

要 SSE 流式 + 会话池 + 鉴权的完整生产蓝图，见 [`examples/saas-assistant/`](https://github.com/jin-bo/agentao/tree/main/examples/saas-assistant)（第 7.1 节）。
:::

## 2.7.1 FastAPI + SSE（推荐）

### 你会得到

- `POST /chat/{session_id}` —— 用 SSE 把 assistant 文本流回客户端（`agent.astream()`）
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
from contextlib import aclosing, asynccontextmanager  # aclosing: Python 3.10+
from pathlib import Path
from typing import Dict, Set, Tuple

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from agentao import Agentao
from agentao.host import CancellationToken, TextDelta


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
    app.state.active_tokens: Dict[str, Set[CancellationToken]] = {}
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
    tokens: Dict[str, Set[CancellationToken]] = request.app.state.active_tokens

    agent, lock = await pool.get(session_id, tenant)
    token = CancellationToken()

    async def watch_disconnect():
        while not await request.is_disconnected():
            await asyncio.sleep(0.5)
        token.cancel("client-disconnected")

    async def sse_stream():
        # Registered here, not in the endpoint: a response whose body never
        # starts never runs this generator's ``finally``. A set per session:
        # the stop button cancels the running turn and any request still
        # waiting for the session lock.
        tokens.setdefault(session_id, set()).add(token)
        watcher = asyncio.create_task(watch_disconnect())
        try:
            async with lock:
                if token.is_cancelled:
                    # 等锁期间已被停止。照样开始本轮，仍会把这条提示和一条
                    # "[Cancelled: …]" 回复写进会话历史。
                    data = {"type": "done", "status": "cancelled",
                            "is_answer": False, "reply": ""}
                    yield f"data: {json.dumps(data)}\n\n"
                    return
                # astream() 订阅 agent 的 transport；不要为了抓文本
                # 按请求替换 agent.transport。
                async with aclosing(
                    agent.astream(message, cancellation_token=token)
                ) as stream:
                    async for item in stream:
                        if isinstance(item, TextDelta):   # 用于显示
                            data = {"type": "text", "text": item.text}
                        else:                             # TurnOutcome 总是最后一项：这才是回答
                            data = {"type": "done", "status": item.status,
                                    "is_answer": item.is_answer, "reply": item.text}
                        yield f"data: {json.dumps(data)}\n\n"
        finally:
            watcher.cancel()
            live = tokens.get(session_id)
            if live is not None:
                live.discard(token)
                if not live:
                    del tokens[session_id]

    return StreamingResponse(sse_stream(), media_type="text/event-stream")


# --------------------------------------------------------------------------
# 辅助端点
# --------------------------------------------------------------------------

@app.post("/chat/{session_id}/cancel")
async def cancel_endpoint(session_id: str, tenant: str = Depends(auth)):
    for token in list(app.state.active_tokens.get(session_id, ())):
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
| `agent.astream(…)` | 运行本轮，先产出 `TextDelta`，最后是 `TurnOutcome`——不用线程桥接，也不用换 transport |
| `watch_disconnect` | 客户端断连时取消本轮 |
| `sse_stream` | 把每个增量作为 SSE 帧往外推，最后用 `TurnOutcome` 发 `{type:"done", status, is_answer, reply}` |

### 注意

- **增量用于显示，结果以 outcome 为准（即 `done` 帧）。** 增量拼起来不等于 `reply`：以工具调用结束的 LLM 调用里的说明文字也会流出来，而 `reply` 可能是从未流出的占位或错误字符串。只有 `is_answer` 为真时才保存 `reply`。见 [4.7](/zh/part-4/7-host-contract#streaming-text-agent-astream)
- `aclosing(...)` 很重要：响应生成器被提前关闭时，它会关闭流，从而取消本轮。只写 `break` 会让本轮继续跑
- **不要按请求替换 `agent.transport`。** 工具执行器持有自己的引用，工具事件和确认仍会发到旧 transport，replay 也会丢掉它的适配器。`astream()` 用的是订阅。工具和权限活动在 `agent.events()` 上（[4.7](/zh/part-4/7-host-contract)）；reasoning 文本只在内部 transport 上（[4.3](/zh/part-4/3-sdk-transport)）
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
_sinks: dict[str, Queue] = {}   # session_id -> 正在跑这一轮的请求的队列
_active_tokens: dict[str, CancellationToken] = {}

app = Flask(__name__)


def _get_agent(session_id: str, tenant: str) -> tuple[Agentao, threading.Lock]:
    if session_id not in _sessions:
        workdir = Path(f"/app/tenants/{tenant}")
        workdir.mkdir(parents=True, exist_ok=True)
        # agent 整个生命周期只用一个 transport，构造时设好——
        # 绝不按请求替换。它把事件转给当前持有锁的那个请求。
        def on_event(ev, sid=session_id):
            q = _sinks.get(sid)
            if q is not None:
                q.put(ev)

        agent = Agentao(working_directory=workdir, transport=SdkTransport(on_event=on_event))
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

    def worker():
        try:
            with lock:
                _sinks[session_id] = queue      # 本请求接管该会话的事件
                try:
                    reply = agent.chat(message, cancellation_token=token)
                finally:
                    _sinks.pop(session_id, None)
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

- **用内部事件，不用 `astream()`**：`astream()` 是异步的，所以这个 WSGI 模板转发的是内部 `AgentEvent` 流（字段可能随版本变化），走的是构造时设好的 transport。不要按请求给 `agent.transport` 赋值——工具事件和确认会留在旧 transport 上
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
- 把 SSE 流接到 React UI：[4.4 构建流式 UI](/zh/part-4/4-streaming-ui)
- 生产关切（观测、限流、沙箱）：[Part 6](/zh/part-6/) 和 [Part 7](/zh/part-7/)

## TL;DR

- **FastAPI + SSE** 是推荐的现代路径；**Flask + 长轮询** 用于 WSGI 旧栈。
- 会话池按 `(tenant_id, session_id)` 索引、TTL 淘汰——**绝对不要跨租户共享 agent**。
- 永远把 `CancellationToken` 接到 FastAPI 客户端断连或 Flask 会话超时上。
- 错误统一为结构化 JSON `{code, message, details}`——不要把堆栈跟踪暴露给客户端。

---

→ 跨语言路径见 [Part 3 · ACP 协议嵌入](/zh/part-3/)
