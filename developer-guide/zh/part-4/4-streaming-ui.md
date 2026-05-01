# 4.4 构建流式 UI：SSE / WebSocket 转译

> **本节你会学到**
> - 线程 / 事件循环边界问题，以及如何干净地桥接
> - 单向场景用 SSE（服务端 → 浏览器）
> - 需要双向（用户边接收边输入）时用 WebSocket

Agent 事件是**后端内部**的 Python 对象。要让前端实时看到 Agent 的响应，需要把 `AgentEvent` 翻译成网络协议。本节给出 SSE 与 WebSocket 两种典型桥接。

## 总体架构

```
┌─────────────┐        ┌──────────────┐       ┌──────────────┐
│  前端浏览器   │◄──────►│  Web 服务器   │◄─────►│  Agent 实例   │
│ EventSource │  SSE   │  (FastAPI)   │  emit │  Agentao()   │
│    or WS    │        │  Transport  │        │              │
└─────────────┘        └──────────────┘       └──────────────┘
```

关键设计：

- **每会话一个队列**：Agent 线程把事件 push 到队列，Web 处理器从队列 pull 推给浏览器
- **背压**：浏览器慢了不能拖垮 Agent；用 `queue.Queue(maxsize=N)` 或溢出丢弃策略
- **JSON 可序列化**：`AgentEvent.data` 已经保证是 JSON 可序列化的

## 模式 A · Server-Sent Events（SSE）

**SSE 适合**：单向流、纯事件推送、不需要客户端回消息、天然支持断线重连。

### 后端（FastAPI）

```python
import asyncio, json, queue
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from agentao import Agentao
from agentao.transport import SdkTransport

app = FastAPI()

# session_id -> (agent, event_queue)
_sessions: dict = {}


def make_session(session_id: str):
    q: queue.Queue = queue.Queue(maxsize=1000)

    def on_event(ev):
        try:
            q.put_nowait({"type": ev.type.value, "data": ev.data})
        except queue.Full:
            pass  # 溢出丢弃

    transport = SdkTransport(
        on_event=on_event,
        confirm_tool=lambda *a: True,  # 生产应走确认 API（见 4.5）
    )
    agent = Agentao(
        transport=transport,
        working_directory=Path(f"/tmp/{session_id}"),
    )
    _sessions[session_id] = (agent, q)
    return agent, q


class ChatRequest(BaseModel):
    session_id: str
    message: str


@app.post("/chat")
async def chat(req: ChatRequest):
    """触发一轮 chat，不阻塞等待——事件流走 /events。"""
    entry = _sessions.get(req.session_id)
    if not entry:
        entry = make_session(req.session_id)
    agent, q = entry
    # 在线程池里跑（chat 是阻塞的）
    asyncio.create_task(asyncio.to_thread(agent.chat, req.message))
    return {"ok": True}


@app.get("/events/{session_id}")
async def events(session_id: str):
    """SSE 端点：客户端用 EventSource 打开。"""
    _, q = _sessions.get(session_id) or make_session(session_id)

    async def gen():
        while True:
            try:
                # 从队列拉事件（阻塞式 get 放线程池）
                ev = await asyncio.to_thread(q.get, True, 15)  # 15s 超时
                yield f"data: {json.dumps(ev)}\n\n"
            except queue.Empty:
                # 心跳——防止代理断连
                yield ": keep-alive\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
```

### 前端（浏览器）

```html
<script>
const SESSION_ID = "sess-123";
const es = new EventSource(`/events/${SESSION_ID}`);

es.onmessage = (e) => {
  const ev = JSON.parse(e.data);
  switch (ev.type) {
    case "llm_text":
      document.getElementById("reply").textContent += ev.data.chunk;
      break;
    case "tool_start":
      appendToolCard(ev.data.call_id, ev.data.tool);
      break;
    case "tool_output":
      appendToolOutput(ev.data.call_id, ev.data.chunk);
      break;
    case "tool_complete":
      closeToolCard(ev.data.call_id, ev.data.status);
      break;
    case "error":
      showToast("Error: " + ev.data.message);
      break;
  }
};
es.onerror = () => { /* EventSource 自动重连 */ };

// 触发 Agent 一轮
async function send(text) {
  document.getElementById("reply").textContent = "";
  await fetch("/chat", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({session_id: SESSION_ID, message: text}),
  });
}
</script>

<input id="msg" type="text" />
<button onclick="send(document.getElementById('msg').value)">Send</button>
<pre id="reply"></pre>
```

### SSE 注意事项

- **反向代理**：Nginx 默认会缓冲 SSE。加上 `proxy_buffering off;`、`proxy_read_timeout` 足够长
- **Keep-alive**：长时间无事件时必须发心跳（上例的 `: keep-alive\n\n` 注释行），否则 Nginx / Cloudflare 会掐连接
- **重连**：EventSource 天然支持；配合 `Last-Event-ID` 可做断点续传
- **一次性响应**：如果是"一轮对话 = 一个请求"，考虑每次 POST 直接返回 SSE，连接随 chat 结束而关闭

## 模式 B · WebSocket（双向）

**WebSocket 适合**：需要浏览器反向发消息（工具确认、取消、user-ask 回答）、低延迟、单连接多路复用。

### 后端（FastAPI + websockets）

```python
import json, asyncio
from fastapi import FastAPI, WebSocket
from agentao import Agentao
from agentao.transport import SdkTransport
from pathlib import Path

app = FastAPI()


@app.websocket("/ws/{session_id}")
async def ws(websocket: WebSocket, session_id: str):
    await websocket.accept()
    loop = asyncio.get_event_loop()

    # 用 Future 作为 confirm_tool 的跨线程响应通道
    pending_confirms: dict = {}  # call_id -> Future

    def on_event(ev):
        # Agent 线程里，调度到 asyncio 循环发消息
        asyncio.run_coroutine_threadsafe(
            websocket.send_json({"type": ev.type.value, "data": ev.data}),
            loop,
        )

    def confirm_tool(name, desc, args):
        call_id = args.get("__call_id__") or name  # 用合适的 key
        fut: asyncio.Future = asyncio.run_coroutine_threadsafe(
            _async_confirm(websocket, call_id, name, desc, args),
            loop,
        )
        return fut.result(timeout=60)  # 60s 内用户必须响应

    async def _async_confirm(ws, call_id, name, desc, args):
        fut = loop.create_future()
        pending_confirms[call_id] = fut
        await ws.send_json({
            "type": "confirm_request",
            "call_id": call_id,
            "tool": name,
            "description": desc,
            "args": args,
        })
        return await fut

    transport = SdkTransport(on_event=on_event, confirm_tool=confirm_tool)
    agent = Agentao(transport=transport, working_directory=Path(f"/tmp/{session_id}"))

    try:
        while True:
            msg = await websocket.receive_json()
            if msg["type"] == "chat":
                asyncio.create_task(asyncio.to_thread(agent.chat, msg["message"]))
            elif msg["type"] == "confirm_response":
                fut = pending_confirms.pop(msg["call_id"], None)
                if fut and not fut.done():
                    fut.set_result(msg["allowed"])
    finally:
        agent.close()
```

### 前端（浏览器）

```html
<script>
const ws = new WebSocket(`wss://${location.host}/ws/sess-123`);

ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.type === "confirm_request") {
    const ok = confirm(`Allow ${msg.tool}?\n\n${JSON.stringify(msg.args)}`);
    ws.send(JSON.stringify({
      type: "confirm_response",
      call_id: msg.call_id,
      allowed: ok,
    }));
  } else if (msg.type === "llm_text") {
    appendText(msg.data.chunk);
  }
  // ... 其他事件
};

function send(text) {
  ws.send(JSON.stringify({type: "chat", message: text}));
}
</script>
```

### WebSocket 注意事项

- **跨线程同步**：Agent 的 `confirm_tool` 是 Python 线程里的阻塞调用，要用 `asyncio.run_coroutine_threadsafe` + `Future.result(timeout=...)` 桥到异步循环
- **超时**：用户不响应时 `confirm_tool` 必须**超时返回** False，不能无限等
- **重连**：浏览器 WS 断开后要有前端重连逻辑；服务端可用 `session_id` 匹配现有 Agent

## 性能调优

| 症状 | 原因 | 解决方案 |
|------|------|---------|
| 前端滞后 | 事件队列积压 | 对 `LLM_TEXT` 做合并（几个 chunk 并一条发） |
| 内存暴涨 | 队列无上限 | `queue.Queue(maxsize=N)` + 溢出丢 `TOOL_OUTPUT` 类事件 |
| CPU 忙 | JSON 序列化瓶颈 | 用 `orjson` / `msgspec` 替代 stdlib |
| 事件乱序 | 多线程 / 异步调度 | 在 `on_event` 内加序号字段，前端按序号重排 |

## 把事件写入可观测性系统

把同一份事件流**同时**推到用户 UI 和后端监控：

```python
def on_event(ev):
    # 1. 用户 UI
    user_queue.put_nowait({"type": ev.type.value, "data": ev.data})
    # 2. 结构化日志
    logger.info("agent_event", extra={"type": ev.type.value, **ev.data})
    # 3. 指标
    metrics.counter(f"agent.{ev.type.value}").inc()
```

## TL;DR

- Agent 循环跑在 worker 线程，事件循环跑在主线程。用 `loop.call_soon_threadsafe(queue.put_nowait, ev)` 桥接。
- **SSE** 适合常规场景（单向流式、浏览器自动重连、简单）。
- **WebSocket** 适合用户在流式过程中需要打字 / 取消 / 确认。
- 永远要发周期性 keep-alive（SSE 用 `: keepalive\n\n`，WS 用 ping/pong）——代理和浏览器会杀掉 idle 长连接。
- 客户端断连时干净取消：FastAPI 用 `request.is_disconnected()`，WS 用 close handler，并调 `token.cancel()`。

→ 下一节：[4.5 工具确认 UI](./5-tool-confirmation-ui)
