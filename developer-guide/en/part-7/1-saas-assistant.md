# 7.1 Blueprint A · SaaS In-Product Assistant

> **Run this example**: [`examples/saas-assistant/`](https://github.com/jin-bo/agentao/tree/main/examples/saas-assistant) — `uv sync && uv run uvicorn app.main:app --reload`

**Scenario**: you run a project-management SaaS. Users want "help me schedule this project plan" or "summarize last week's tasks" inside the product. You want to embed Agentao as the brain, expose only the tools it needs, and stream responses to the browser.

## Who & why

- **Product shape**: existing web app (frontend + backend API)
- **Users**: logged-in tenants, each with scoped data
- **Pain point**: users write their own prompts in scratch copies of ChatGPT and paste results back — data leaks, no in-product integration

## Architecture

```
Browser (React)
   │
   │  POST /chat  { session_id, message }     ── SSE stream back
   ▼
FastAPI backend
   ├─ Auth middleware  (tenant_id, user_id)
   ├─ Session pool     (see 6.7)
   │    │
   │    ▼
   └─ Agentao instance per (tenant_id, session_id)
        ├─ working_directory = /data/tenants/{tenant_id}/{session_id}
        ├─ Custom tools: list_projects, create_task, assign_user
        ├─ PermissionEngine: READ_ONLY by default, WORKSPACE_WRITE after confirm
        └─ SdkTransport → SSE bridge
```

## Key code

### 1 · Custom tools that hit your backend

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
    def description(self): return "List projects visible to the current user"
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
    def description(self): return "Create a task in a project"
    @property
    def parameters(self):
        return {"type": "object", "required": ["project_id", "title"], "properties": {
            "project_id": {"type": "string"},
            "title": {"type": "string"},
            "assignee_email": {"type": "string"},
            "due_date": {"type": "string", "format": "date"},
        }}
    @property
    def requires_confirmation(self): return True   # write action

    def execute(self, **kwargs) -> str:
        r = self._api.post(f"/api/v1/tasks",
                           json={"tenant_id": self._tenant_id, **kwargs},
                           timeout=10)
        r.raise_for_status()
        return r.text
```

### 2 · FastAPI endpoint with SSE streaming

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

### 3 · Pool wiring

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

Use the full `AgentPool` from [6.7](/en/part-6/7-resource-concurrency#pattern-b-session-pool-ttl-eviction) for TTL + LRU eviction.

## Frontend skeleton

```ts
const es = new EventSource(`/chat`, { method: "POST", body: JSON.stringify({session_id, message}) });
es.onmessage = (e) => {
  const ev = JSON.parse(e.data);
  if (ev.type === "llm_text") append(ev.chunk);
  if (ev.type === "tool_start") showSpinner(ev.tool);
  if (ev.type === "tool_confirmation") showConfirmModal(ev);  // see 4.5
};
es.addEventListener("done", (e) => { finalize(JSON.parse(e.data).text); es.close(); });
```

## Pitfalls

| Day-2 bug | Root cause | Fix |
|-----------|------------|-----|
| Cross-tenant data leak | Tool captured `tenant_id` at construction time, but pool reused an agent for another tenant | One agent per `(tenant_id, session_id)` — never share across tenants |
| "My tasks vanished!" | `clear_history()` called when the SDK reset, but backend memory DB retained user-wide notes and bled into another session | Use project-scope memory only; if you mount user-scope, key it by `tenant_id+user_id` |
| Agent "hangs" forever | No per-chat timeout | Wrap `chat()` in `asyncio.wait_for` ([6.7 Control 3](/en/part-6/7-resource-concurrency#control-3-per-turn-timeout)) |
| SSE stream stops mid-reply | Frontend reconnect buffers didn't match server; browser idle kill | Send periodic `: keep-alive\n\n` every 15s |
| Confirm modal blocks forever | `confirm_tool` ran on the event-loop thread | See [4.5 Web modal](/en/part-4/5-tool-confirmation-ui#pattern-2-web-modal-asyncio-to-thread-bridge) — use `asyncio.run_coroutine_threadsafe` |

## Runnable code

The full project lives in-repo at [`examples/saas-assistant/`](https://github.com/jin-bo/agentao/tree/main/examples/saas-assistant) — see the top-of-page "Run this example" link.

```bash
cd examples/saas-assistant
uv sync && uv run uvicorn app.main:app --reload
```

---

→ [7.2 IDE Plugin (ACP)](./2-ide-plugin)
