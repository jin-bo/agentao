# 4.4 构建流式 UI：SSE / WebSocket 转译

> **本节你会学到**
> - 线程 / 事件循环边界问题，以及如何干净地桥接
> - 单向场景用 SSE（服务端 → 浏览器）
> - 需要双向（用户边接收边输入）时用 WebSocket
> - 稳定的文本路径 `agent.astream()`，以及什么时候仍需要内部 `AgentEvent` 流

Agent 事件是**后端内部**的 Python 对象。要让前端实时看到 Agent 的响应，需要把它们翻译成网络协议。本节给出 SSE 与 WebSocket 两种典型桥接。

**assistant 文本**请先用 `agent.astream()`——它属于稳定的宿主合约。下面的桥接转发的是内部 `AgentEvent` 流（`LLM_TEXT`、`THINKING`、`TOOL_OUTPUT`……）；UI 还要展示 reasoning 或原始工具输出时再用它，并且要知道它的字段可能随版本变化。

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

## 稳定的文本路径：`agent.astream()`

`agent.astream(prompt)` 运行一轮，运行中产出 `TextDelta`，最后一项是本轮的 `TurnOutcome`。它不需要线程桥接，也不需要 transport 回调——异步 SSE 端点可以直接转发（复用下面模式 A 里的 `make_session`）：

```python
import json
from contextlib import aclosing  # Python 3.10+
from fastapi.responses import StreamingResponse
from agentao.host import TextDelta

@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    agent, _ = _sessions.get(req.session_id) or make_session(req.session_id)
    # 每个 agent 一次一轮：同一会话上重叠的第二个请求会从流里抛
    # TurnInProgressError。请求可能重叠时，按会话加锁串行化（见 2.7）。

    async def gen():
        async with aclosing(agent.astream(req.message)) as stream:
            async for item in stream:
                if isinstance(item, TextDelta):
                    yield f"data: {json.dumps({'type': 'text', 'text': item.text})}\n\n"
                else:  # TurnOutcome，总是最后一项
                    yield f"data: {json.dumps({'type': 'done', 'status': item.status, 'text': item.text, 'is_answer': item.is_answer})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
```

对 UI 要紧的几条规则：

- **增量用于显示，结果以 outcome 为准。** 所有增量拼起来不等于 `TurnOutcome.text`。本轮里每一次 LLM 调用都会流出文本，包括以工具调用结束的那次，所以"我先看一下文件"这类说明文字会流出来，却不在最终文本里；最终文本也可能是从未流出的占位（`[No response]`）、中止说明或 `[LLM API error: …]`。增量实时渲染，要保存或据以行动的取 `TurnOutcome.text`，并先用 `.is_answer` 检查。
- **提前离开时要关闭流。** 单用 `break` 不会关闭异步生成器：它要等被垃圾回收或事件循环关闭时才关闭，只要还有引用持有它，本轮就不会结束：它一直流到队列满（64 个增量），然后停在那里等待，同时一直占着 agent，之后的轮次会抛 `TurnInProgressError`。`aclosing(...)` 会关闭它；关闭流，或取消消费它的任务，都会取消本轮，并像被取消的 `arun()` 一样有界地等待清理。
- **不要为了抓文本按请求替换 `agent.transport`。** `astream()` *订阅*现有的 transport（上面的 `SdkTransport` 就可以）。工具执行器持有自己的 transport 引用，换进来的 transport 收不到工具事件和确认，replay 也会丢掉它的适配器。
- **流里没有的：** reasoning 文本、工具 / 权限事件（用 `agent.events()`——见 [4.7](./7-host-contract)），以及子 agent 的文本。队列有界：客户端慢，本轮就变慢，内存不会增长。

完整契约：[4.7 · 流式文本](./7-host-contract#streaming-text-agent-astream)。

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
        await agent.aclose()
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

- assistant 文本用 `aclosing(...)` 包住的 `agent.astream()`：显示 `TextDelta`，把最后的 `TurnOutcome` 当作回答。不要为了抓文本替换 transport。
- reasoning / 原始工具输出走内部事件流的桥接：Agent 循环跑在 worker 线程，事件循环跑在主线程。用 `loop.call_soon_threadsafe(queue.put_nowait, ev)` 桥接。
- **SSE** 适合常规场景（单向流式、浏览器自动重连、简单）。
- **WebSocket** 适合用户在流式过程中需要打字 / 取消 / 确认。
- 永远要发周期性 keep-alive（SSE 用 `: keepalive\n\n`，WS 用 ping/pong）——代理和浏览器会杀掉 idle 长连接。
- 客户端断连时干净取消：FastAPI 用 `request.is_disconnected()`，WS 用 close handler，并调 `token.cancel()`。

→ 下一节：[4.5 工具确认 UI](./5-tool-confirmation-ui)
