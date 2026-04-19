# 2.3 生命周期管理

一个 `Agentao` 实例 = 一次完整的对话会话。掌握它的生老病死对生产嵌入至关重要。

## 标准生命周期

```python
agent = Agentao(...)          # 1. 构造（开 MCP、加载技能）
reply1 = agent.chat("hello")   # 2. 第一轮对话
reply2 = agent.chat("continue")# 3. 同一会话再聊
# ...
agent.close()                  # 4. 释放 MCP 连接、事件循环
```

**核心不变量**：同一个 `agent` 实例贯穿整个会话。不要"每轮重建"——你会丢上下文。

## `chat()` 详解

```python
def chat(
    self,
    user_message: str,
    max_iterations: int = 100,
    cancellation_token: Optional[CancellationToken] = None,
) -> str
```

| 参数 | 说明 |
|------|------|
| `user_message` | 用户这轮的输入 |
| `max_iterations` | 工具调用回合上限（防死循环） |
| `cancellation_token` | 支持外部取消（详见 2.6 节） |

**返回值**：Agent 的最终文本回复（字符串）。

**特殊返回值**：
- `"[Interrupted by user]"` — 捕获到 `KeyboardInterrupt`
- `"[Cancelled: <reason>]"` — cancellation token 被触发
- `"[Blocked by hook] ..."` / `"[Hook stopped] ..."` — 插件 hook 拦截

这些情形下 `chat()` 不会抛异常，而是优雅返回——你的调用方需要识别前缀。

**阻塞特性**：`chat()` 是**同步阻塞**调用，LLM 流式输出期间会持续调用 `transport.emit(...)`。如果你的宿主是 async 框架，参见 2.6 节的线程池包装。

## `add_message()`

```python
agent.add_message("user", "补一条历史消息")
```

用于**在调用 `chat()` 前手动注入消息**，比如恢复持久化的会话历史：

```python
# 从你的数据库把历史灌回去
for row in db.load_conversation(session_id):
    agent.add_message(row.role, row.content)
# 然后正常聊天
reply = agent.chat("根据上面的对话继续")
```

⚠️ 注意：`add_message` 直接修改 `self.messages`，不会触发 LLM 调用。只在需要**恢复历史**时使用。

## `clear_history()`

```python
agent.clear_history()
```

作用：
- 清空 `self.messages`
- 反激活所有已激活的技能
- 清空 todo 列表
- **不**清空记忆（MemoryManager 持久化到 SQLite，跨会话存活）

典型场景：用户在你的 UI 上点"新对话"。

## `close()`

```python
agent.close()
```

必须调用。作用：
- 断开所有 MCP 客户端连接
- 关闭 MCP 管理器的事件循环线程

不调用会**泄漏 MCP 子进程和线程**。在 try/finally 或 context manager 中处理：

```python
from contextlib import contextmanager

@contextmanager
def agent_session(**kwargs):
    agent = Agentao(**kwargs)
    try:
        yield agent
    finally:
        agent.close()

with agent_session(working_directory=Path("/tmp/x")) as agent:
    reply = agent.chat("hi")
```

## 运行时切换 LLM

两个方法可以在会话中途更换模型或凭据：

```python
# 整套切换（kit + 模型）
agent.set_provider(
    api_key="sk-new",
    base_url="https://api.deepseek.com",
    model="deepseek-chat",
)

# 只换模型（保留凭据）
agent.set_model("gpt-5.4")

# 查询当前模型
current = agent.get_current_model()  # -> "gpt-5.4"
```

用途：
- 用户在 UI 点"换模型"下拉
- A/B 测试不同模型对同一个会话的答复
- 把便宜模型用于普通问题、贵模型用于复杂推理

⚠️ 切换不会清空历史——下一次 `chat()` 就用新模型继续同一段上下文。

## 会话状态自检

```python
# 消息数（不含 system prompt，system prompt 每轮重建）
len(agent.messages)

# LLM 压缩后的会话摘要（用于调试或持久化）
summary = agent.get_conversation_summary()

# 当前工作目录（只读）
agent.working_directory     # Path 对象

# 已激活的技能
agent.skill_manager.active_skills  # dict
```

## 并发模型

**单个 `Agentao` 实例不是线程安全的**。同一实例不能被两个线程同时 `chat()`。

正确做法：每会话一个 Agent 实例。

| 宿主场景 | 推荐模式 |
|---------|---------|
| 同步 Web（Flask、WSGI） | 请求进来 → 构造 / 从池拿 agent → `chat()` → 还回 / close |
| 异步 Web（FastAPI、asyncio） | `chat()` 包 `asyncio.to_thread(agent.chat, msg)` |
| 后台批处理 | 每个 job 一个 agent，顺序跑 |
| 多租户 SaaS | 按 tenant 缓存，TTL 过期后 `close()` |

## 完整生命周期示例（FastAPI + 异步 + 会话池）

```python
from asyncio import to_thread, Lock
from pathlib import Path
from fastapi import FastAPI, HTTPException
from agentao import Agentao
from agentao.transport import SdkTransport

app = FastAPI()

# session_id -> (agent, lock)
_sessions: dict = {}

async def get_or_create(session_id: str, workdir: Path) -> tuple[Agentao, Lock]:
    if session_id not in _sessions:
        agent = Agentao(
            working_directory=workdir,
            transport=SdkTransport(),  # 简单起见不订阅事件
        )
        _sessions[session_id] = (agent, Lock())
    return _sessions[session_id]

@app.post("/chat")
async def chat_endpoint(session_id: str, message: str):
    try:
        agent, lock = await get_or_create(session_id, Path(f"/tmp/{session_id}"))
    except Exception as e:
        raise HTTPException(500, str(e))

    async with lock:               # 同一会话串行，避免并发 chat()
        reply = await to_thread(agent.chat, message)
    return {"reply": reply}

@app.delete("/session/{session_id}")
async def end_session(session_id: str):
    entry = _sessions.pop(session_id, None)
    if entry:
        await to_thread(entry[0].close)
    return {"ok": True}
```

关键要点：

- `Lock` 保证同一会话**串行**（不能并发 `chat()` 同一实例）
- `asyncio.to_thread` 让阻塞的 `chat()` 不堵事件循环
- 显式 `DELETE` 端点触发 `close()` 释放 MCP

生产部署下你还需要加 TTL 淘汰、内存上限、崩溃重启等——见 [第 7 部分](/zh/part-7/)。

→ 更多参数与扩展：[第 5 部分 · 扩展点](/zh/part-5/)（撰写中）
