# 7.1 蓝图 A · SaaS 产品内置助手

> **运行此例**：[`examples/saas-assistant/`](https://github.com/jin-bo/agentao/tree/main/examples/saas-assistant) —— `uv sync && uv run uvicorn app.main:app --reload`

**场景**：你做的是一款项目管理 SaaS。用户希望在产品里说"帮我排一下这个项目的计划"或者"总结上周任务"。你想把 Agentao 当作大脑嵌进去，只暴露它需要的那几个工具，流式返回给前端。

## 谁 & 为什么

- **产品形态**：已有的 Web 应用（前端 + 后端 API）
- **用户**：登录态的租户，每个租户看到的数据有隔离
- **痛点**：目前用户自己跑去 ChatGPT 写提示词，然后把结果粘回来——数据泄漏、产品集成缺失

## 架构

```
Browser (React)
   │
   │  POST /chat  { session_id, message }     ── SSE 流回
   ▼
FastAPI 后端
   ├─ 鉴权中间件  (tenant_id, user_id)
   ├─ 会话池      (见 6.7)
   │    │
   │    ▼
   └─ 每个 (tenant_id, session_id) 一个 Agentao 实例
        ├─ working_directory = /data/tenants/{tenant_id}/{session_id}
        ├─ 自定义工具: list_projects, create_task, assign_user
        ├─ PermissionEngine: 默认 READ_ONLY，经确认后放宽 WORKSPACE_WRITE
        └─ SdkTransport → SSE 桥接
```

## 关键代码

### 1 · 打通你自己后端的自定义工具

```python
# tools/project_tools.py
from agentao.tools.base import Tool
import httpx

class ListProjectsTool(Tool):
    def __init__(self, tenant_id: str, api_client: httpx.Client):
        self._tenant_id = tenant_id
        self._api = api_client

    @property
    def name(self): return "list_projects"
    @property
    def description(self): return "列出当前用户可见的项目"
    @property
    def parameters(self):
        return {"type": "object", "properties": {
            "status": {"type": "string", "enum": ["active", "archived", "all"]}
        }}
    @property
    def is_read_only(self): return True

    def execute(self, status: str = "active") -> str:
        r = self._api.get(f"/api/v1/projects",
                          params={"tenant_id": self._tenant_id, "status": status},
                          timeout=10)
        r.raise_for_status()
        return r.text
```

```python
class CreateTaskTool(Tool):
    def __init__(self, tenant_id: str, api_client: httpx.Client):
        self._tenant_id = tenant_id
        self._api = api_client

    @property
    def name(self): return "create_task"
    @property
    def description(self): return "在项目中创建任务"
    @property
    def parameters(self):
        return {"type": "object", "required": ["project_id", "title"], "properties": {
            "project_id": {"type": "string"},
            "title": {"type": "string"},
            "assignee_email": {"type": "string"},
            "due_date": {"type": "string", "format": "date"},
        }}
    @property
    def requires_confirmation(self): return True   # 写操作

    def execute(self, **kwargs) -> str:
        r = self._api.post(f"/api/v1/tasks",
                           json={"tenant_id": self._tenant_id, **kwargs},
                           timeout=10)
        r.raise_for_status()
        return r.text
```

### 2 · FastAPI 接口 + SSE 流式

```python
# app.py
from fastapi import FastAPI, Depends
from fastapi.responses import StreamingResponse
from agentao import Agentao
from agentao.transport import SdkTransport
from agentao.transport.events import AgentEvent, EventType
from pathlib import Path
import asyncio, httpx, json

from .tools.project_tools import ListProjectsTool, CreateTaskTool
from .auth import current_user
from .pool import get_or_create_agent

app = FastAPI()
api_client = httpx.Client(base_url="http://internal-api")

@app.post("/chat")
async def chat(payload: dict, user=Depends(current_user)):
    session_id = payload["session_id"]
    message = payload["message"]
    workdir = Path(f"/data/tenants/{user.tenant_id}/{session_id}")
    workdir.mkdir(parents=True, exist_ok=True)

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_event(ev: AgentEvent):
        loop.call_soon_threadsafe(queue.put_nowait, ev)

    transport = SdkTransport(on_event=on_event)
    agent = await get_or_create_agent(
        session_id=f"{user.tenant_id}:{session_id}",
        workdir=workdir,
        tenant_id=user.tenant_id,
        transport=transport,
    )

    async def run():
        reply = await asyncio.to_thread(agent.chat, message)
        await queue.put({"type": "done", "text": reply})

    asyncio.create_task(run())

    async def sse():
        while True:
            ev = await queue.get()
            if isinstance(ev, dict) and ev.get("type") == "done":
                yield f"event: done\ndata: {json.dumps(ev)}\n\n"
                return
            yield f"data: {json.dumps({'type': ev.type.value, **ev.data})}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")
```

### 3 · 会话池接线

```python
# pool.py
from agentao import Agentao
from agentao.permissions import PermissionEngine, PermissionMode
from .tools.project_tools import ListProjectsTool, CreateTaskTool

async def get_or_create_agent(session_id, workdir, tenant_id, transport):
    existing = _pool.get(session_id)
    if existing:
        existing.transport = transport
        return existing
    engine = PermissionEngine(project_root=workdir)
    engine.set_mode(PermissionMode.READ_ONLY)
    agent = Agentao(
        working_directory=workdir,
        transport=transport,
        permission_engine=engine,
    )
    agent.tools.register(ListProjectsTool(tenant_id, api_client))
    agent.tools.register(CreateTaskTool(tenant_id, api_client))
    _pool[session_id] = agent
    return agent
```

TTL + LRU 驱逐用 [6.7 Pattern B](/zh/part-6/7-resource-concurrency#pattern-b-会话池-ttl-驱逐) 里的完整 `AgentPool`。

## 前端骨架

```ts
const es = new EventSource(`/chat`, { method: "POST", body: JSON.stringify({session_id, message}) });
es.onmessage = (e) => {
  const ev = JSON.parse(e.data);
  if (ev.type === "llm_text") append(ev.chunk);
  if (ev.type === "tool_start") showSpinner(ev.tool);
  if (ev.type === "tool_confirmation") showConfirmModal(ev);  // 见 4.5
};
es.addEventListener("done", (e) => { finalize(JSON.parse(e.data).text); es.close(); });
```

## 陷阱

| 上线第二天的 bug | 根因 | 修法 |
|------------------|------|------|
| 跨租户数据泄漏 | Tool 构造时捕获了 `tenant_id`，但会话池把 agent 复用给了另一个租户 | 每个 `(tenant_id, session_id)` 一个 agent，不跨租户复用 |
| "我的任务不见了！" | SDK 重置时调了 `clear_history()`，但后端 memory DB 还保留了用户维度的笔记，污染到了另一个会话 | 只用 project 作用域；如果挂 user 作用域，必须用 `tenant_id+user_id` 做 key |
| Agent 永远卡住 | 没有单轮超时 | 用 `asyncio.wait_for` 包 `chat()`（[6.7 控制 3](/zh/part-6/7-resource-concurrency#控制-3-单轮超时)） |
| SSE 流中断 | 前端重连缓冲不匹配，浏览器空闲断开 | 每 15 秒发 `: keep-alive\n\n` 心跳 |
| 确认弹窗永远回不来 | `confirm_tool` 跑在了事件循环线程上 | 参考 [4.5 Web 模态](/zh/part-4/5-tool-confirmation-ui#模式-2-web-模态-asyncio-to-thread-桥接)，用 `asyncio.run_coroutine_threadsafe` |

## 可运行代码

完整项目就在主仓 [`examples/saas-assistant/`](https://github.com/jin-bo/agentao/tree/main/examples/saas-assistant)——参考本页顶部的 "运行此例" 链接。

```bash
cd examples/saas-assistant
uv sync && uv run uvicorn app.main:app --reload
```

---

→ [7.2 IDE 插件（ACP）](./2-ide-plugin)
