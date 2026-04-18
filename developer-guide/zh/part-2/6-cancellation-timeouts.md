# 2.6 取消与超时

`chat()` 是同步调用，可能跑几分钟——工具回路、LLM 流式、MCP 子进程都在里面。如果你的宿主没法中途叫停它，就做不了"停止"按钮、撑不了 SLA、也响应不了客户端断连。本节介绍三种给运行时设边界的机制，按粒度由细到粗排列。

## 2.6.1 三种边界机制

| 机制 | 粒度 | 由谁触发 | 响应时间 |
|------|------|---------|---------|
| `CancellationToken` | 协作式，任意点 | 宿主代码 | **下一个 check 点**（流式 < 1s；工具要等工具跑完或自己 abort） |
| `max_iterations` | 工具回路数 | Agentao 自己 | N 轮工具之后 |
| 线程级强杀 | 最后手段 | 宿主监督进程 | 立即，但**不安全** —— MCP 子进程会泄漏 |

默认用 `CancellationToken`。`max_iterations` 用来封顶失控回路。线程强杀基本不用。

## 2.6.2 `CancellationToken` — 正规做法

```python
from agentao.cancellation import CancellationToken, AgentCancelledError

token = CancellationToken()
reply = agent.chat("扫仓库并总结", cancellation_token=token)
```

API：

```python
class CancellationToken:
    def cancel(self, reason: str = "user-cancel") -> None   # 幂等
    def check(self) -> None                                 # 已取消则抛 AgentCancelledError
    @property
    def is_cancelled(self) -> bool
    @property
    def reason(self) -> str
```

底层是 `threading.Event`，**任意线程**调 `cancel()` 都安全。

### `chat()` 被取消时会怎样

`chat()` 默认**不抛异常**。token 触发时它会：

1. 完成进行中的单位工作（一个 LLM chunk、一个工具调用）
2. 返回字符串 `"[Cancelled: <reason>]"`

也就是说调用方用**前缀检查**识别取消，而不是 try/except：

```python
reply = agent.chat(msg, cancellation_token=token)
if reply.startswith("[Cancelled:"):
    # 清 UI 状态，不要把这条当真的 assistant 输出
    return
```

### 取消在哪些地方被检查

取消以**协作**方式在调用栈里传播——检查点设在工作单元的边界：

| 检查点 | 说明 |
|-------|------|
| 每次 LLM 流式调用前 | 在"思考"中间也能中断 |
| 每个流式 chunk 之后 | 流式在一个 chunk 内停下 |
| 每次分发工具调用前 | 还没启动的工具不会跑 |
| 长耗时工具内部（shell、web fetch） | 尽力而为——有些外部 IO 打不断 |
| MCP 转发 | 请求已发出；取消只是停止**监听**，远端仍可能在干活 |

**正在跑的 shell 命令不会被强杀**——token 只阻断下一轮。想真杀 shell，给 shell 工具自己设超时（`.agentao/sandbox.json`）。

## 2.6.3 对接 FastAPI / HTTP 断连

客户端断连时取消本轮：

```python
from fastapi import FastAPI, Request
from asyncio import to_thread
from agentao.cancellation import CancellationToken

@app.post("/chat/{session_id}")
async def chat_endpoint(session_id: str, message: str, request: Request):
    agent, lock = await get_or_create(session_id, ...)
    token = CancellationToken()

    async def watch_disconnect():
        while not await request.is_disconnected():
            await asyncio.sleep(0.5)
        token.cancel("client-disconnected")

    watcher = asyncio.create_task(watch_disconnect())
    try:
        async with lock:
            reply = await to_thread(agent.chat, message, cancellation_token=token)
    finally:
        watcher.cancel()

    return {"reply": reply}
```

要点：

- `CancellationToken` **每轮**新建一个，不要跨轮复用——复用意味着第二轮起点就是"已取消"
- `finally` 里一定要 `watcher.cancel()`，不然响应发出后它还在跑

## 2.6.4 对接"停止"按钮

用第二个端点按 session 暴露 `token.cancel()`：

```python
_active_tokens: dict[str, CancellationToken] = {}

@app.post("/chat/{session_id}")
async def chat_endpoint(session_id: str, message: str):
    agent, lock = await get_or_create(session_id, ...)
    token = CancellationToken()
    _active_tokens[session_id] = token
    try:
        async with lock:
            reply = await to_thread(agent.chat, message, cancellation_token=token)
    finally:
        _active_tokens.pop(session_id, None)
    return {"reply": reply}

@app.post("/chat/{session_id}/cancel")
async def cancel_endpoint(session_id: str):
    token = _active_tokens.get(session_id)
    if token:
        token.cancel("user-stop-button")
    return {"ok": True}
```

没有进行中的轮次时调 `/cancel` 就是空操作——OK。

## 2.6.5 硬超时

超时就是"带定时器的取消"。自己包一层：

```python
import asyncio
from asyncio import to_thread, wait_for, TimeoutError
from agentao.cancellation import CancellationToken

async def chat_with_timeout(agent, msg: str, seconds: float) -> str:
    token = CancellationToken()
    try:
        return await wait_for(
            to_thread(agent.chat, msg, cancellation_token=token),
            timeout=seconds,
        )
    except TimeoutError:
        token.cancel("timeout")
        # 线程还在跑；下一个 checkpoint 会观察到 token 并返回
        # "[Cancelled: timeout]"。调用方已经收到 TimeoutError，
        # 所以线程那边的返回值被忽略——但 cancel 保证它尽快停。
        return "[Cancelled: timeout]"
```

注意：

- `wait_for` 取消的是**等待协程**，**不是**底下那个线程。所以还要 `token.cancel()`——不然线程跑到结束，白烧 CPU
- 真正硬 SLA（比如 30s）用这套；软 SLA 用 `max_iterations` 就够了

## 2.6.6 `max_iterations` — 结构上限

```python
agent.chat("干 20 件事", max_iterations=20)
```

- 计**工具回路**数，不是墙钟时间
- 默认 100——已经宽松了；如果按 token 付费请调低
- 超过时触发 Transport 的 `on_max_iterations()`，返回 `True` 让 agent 继续，`False` 停。参见 [Part 4](/zh/part-4/)（待上线）

按次付费的聊天 UI，`max_iterations=20-30` 是不错的默认——一轮里用户很少需要 100 次工具调用，一个失控回路按 \$0.50/轮 很快就烧掉很多钱。

## 2.6.7 工具与 MCP 层的超时

还有两处可以设边界：

- **Shell 工具**：每条命令默认 30 秒超时（可在 `.agentao/sandbox.json` 配）。超时就 `SIGTERM`。详见 [6.2](/zh/part-6/2-shell-sandbox)
- **MCP 请求超时**：每个 MCP server 有 `timeout` 字段（默认 60 秒）。详见 [5.3 MCP](/zh/part-5/3-mcp)

它们是更底层的**强杀**——与 `CancellationToken` 互补，不替代。MCP 卡死时其自身超时会兜底，无论 token 是否触发。

## 2.6.8 上线前自查

发布轮次交互 UI 前：

- [ ] 每次 `chat()` 都带 `CancellationToken`
- [ ] 调用方识别 `"[Cancelled: ...]"` 前缀
- [ ] 客户端断连触发 `token.cancel("client-disconnected")`
- [ ] 停止按钮触发 `token.cancel("user-stop-button")`
- [ ] 每轮有硬超时护栏（`wait_for` 或后台 watcher）
- [ ] 按次付费的场景，把 `max_iterations` 从 100 调下来

---

下一节：[2.7 FastAPI / Flask 嵌入 →](./7-fastapi-flask-embed)
