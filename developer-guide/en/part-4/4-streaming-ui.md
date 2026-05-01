# 4.4 Streaming UI: SSE / WebSocket Bridging

> **What you'll learn**
> - The thread / event-loop boundary problem and how to bridge it cleanly
> - SSE for unidirectional server → browser streaming
> - WebSocket when you need bidirectional (e.g., user types while streaming)

Agent events are **in-process Python objects**. To make them visible to a browser frontend, translate `AgentEvent` into a wire protocol. This section shows SSE and WebSocket bridges.

## Overall architecture

```
┌─────────────┐        ┌──────────────┐       ┌──────────────┐
│   Browser   │◄──────►│  Web server  │◄─────►│ Agent instance│
│ EventSource │  SSE   │  (FastAPI)   │  emit │  Agentao()   │
│    or WS    │        │  Transport  │        │              │
└─────────────┘        └──────────────┘       └──────────────┘
```

Key design decisions:

- **One queue per session**: agent thread pushes events; web handler pulls them for the browser
- **Backpressure**: a slow browser must not stall the agent. Use `queue.Queue(maxsize=N)` with an overflow policy
- **JSON-serializable**: `AgentEvent.data` is already guaranteed to serialize

## Pattern A · Server-Sent Events (SSE)

**SSE fits** unidirectional event streams, pure push, no client-to-server in the stream, with built-in reconnection.

### Backend (FastAPI)

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
            pass  # drop on overflow

    transport = SdkTransport(
        on_event=on_event,
        confirm_tool=lambda *a: True,   # production: route to approval API (4.5)
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
    """Kick off a turn; events flow via /events."""
    entry = _sessions.get(req.session_id) or make_session(req.session_id)
    agent, _ = entry
    asyncio.create_task(asyncio.to_thread(agent.chat, req.message))
    return {"ok": True}


@app.get("/events/{session_id}")
async def events(session_id: str):
    """SSE endpoint; client opens with EventSource."""
    _, q = _sessions.get(session_id) or make_session(session_id)

    async def gen():
        while True:
            try:
                ev = await asyncio.to_thread(q.get, True, 15)   # 15s timeout
                yield f"data: {json.dumps(ev)}\n\n"
            except queue.Empty:
                yield ": keep-alive\n\n"                          # heartbeat

    return StreamingResponse(gen(), media_type="text/event-stream")
```

### Frontend (browser)

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
es.onerror = () => { /* EventSource auto-reconnects */ };

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

### SSE gotchas

- **Reverse proxy**: Nginx buffers SSE by default. Add `proxy_buffering off;` and a generous `proxy_read_timeout`
- **Keep-alive**: send a heartbeat when idle (the `: keep-alive\n\n` comment in the example) or Nginx / Cloudflare will drop the connection
- **Reconnect**: built-in; combine with `Last-Event-ID` for resume
- **Ephemeral streams**: if "one turn = one request", return the SSE from POST and let it close when `chat()` ends

## Pattern B · WebSocket (bidirectional)

**WebSocket fits** cases where the browser must send messages back (tool confirm, cancel, ask-user answer), low latency, multiplexing on one connection.

### Backend (FastAPI + websockets)

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

    pending_confirms: dict = {}  # call_id -> Future

    def on_event(ev):
        asyncio.run_coroutine_threadsafe(
            websocket.send_json({"type": ev.type.value, "data": ev.data}),
            loop,
        )

    def confirm_tool(name, desc, args):
        call_id = args.get("__call_id__") or name
        fut: asyncio.Future = asyncio.run_coroutine_threadsafe(
            _async_confirm(websocket, call_id, name, desc, args),
            loop,
        )
        return fut.result(timeout=60)   # 60s user response window

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

### Frontend

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
  // ... other events
};

function send(text) {
  ws.send(JSON.stringify({type: "chat", message: text}));
}
</script>
```

### WebSocket gotchas

- **Cross-thread sync**: the agent's `confirm_tool` is a blocking Python thread call; bridge to the async loop with `asyncio.run_coroutine_threadsafe` + `Future.result(timeout=...)`
- **Timeouts**: when the user doesn't respond, `confirm_tool` must **time out and return False** — never wait forever
- **Reconnect**: browsers don't auto-reconnect WebSockets; handle reconnect logic on the frontend and match `session_id` on the server side

## Performance

| Symptom | Cause | Fix |
|---------|-------|-----|
| Frontend lag | Queue backlog | Coalesce `LLM_TEXT` chunks (merge several before sending) |
| Memory blow-up | Unbounded queue | `queue.Queue(maxsize=N)` + drop `TOOL_OUTPUT`-type events |
| CPU hot | JSON serialization | Replace stdlib with `orjson` or `msgspec` |
| Out-of-order events | Multi-thread / async races | Add a sequence number in `on_event`; reorder on frontend |

## Observability: dual sink

Push the same event stream to both the user UI and your monitoring:

```python
def on_event(ev):
    user_queue.put_nowait({"type": ev.type.value, "data": ev.data})
    logger.info("agent_event", extra={"type": ev.type.value, **ev.data})
    metrics.counter(f"agent.{ev.type.value}").inc()
```

## TL;DR

- The agent loop runs on a worker thread; the event loop runs on the main thread. Bridge with `loop.call_soon_threadsafe(queue.put_nowait, ev)`.
- **SSE** for the common case (one-way streaming, browsers handle reconnect, simple).
- **WebSocket** when the user needs to type / cancel / confirm mid-stream.
- Always send periodic keep-alive frames (`: keepalive\n\n` for SSE, ping/pong for WS) — proxies and browsers kill idle long-polls.
- Cancel cleanly when the client disconnects: `request.is_disconnected()` (FastAPI) or `ws.close()` event handler — and call `token.cancel()`.

→ Next: [4.5 Tool Confirmation UI](./5-tool-confirmation-ui)
