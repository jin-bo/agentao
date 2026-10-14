# 2.3 生命周期管理

> **本节你会学到**
> - 标准 4 步生命周期（`Agentao()` → `chat()` → … → `close()`）
> - 单实例为什么不是线程安全的，以及如何正确串行化
> - 一个能正确处理并发会话的 FastAPI 模板

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
- `"[Interrupted by user]"` — 捕获到 `KeyboardInterrupt`。历史里写入一条 assistant 消息：中断前模型已流式输出的文本，后接 `[Interrupted]`；中断没落在流式输出中时只有 `[Interrupted]`（本轮更早调用的文本已在历史里）
- `"[Cancelled: <reason>]"` — cancellation token 被触发
- `"[Blocked by hook] ..."` / `"[Hook stopped] ..."` — 插件 hook 拦截。`agent.last_turn.incomplete_reason` 为 `"hook_stop"`，`is_answer` 为 `False`

这些情形下 `chat()` 不会抛异常，而是优雅返回——你的调用方需要识别前缀。

**阻塞特性**：`chat()` 是**同步阻塞**调用，LLM 流式输出期间会持续调用 `transport.emit(...)`。异步宿主改用 `await agent.arun(...)`（见[下文](#异步宿主-arun-与轮次线程池)）。

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
- 最先取消这个 agent 启动的后台子代理，并最多等 5 秒让它们停下。子代理在下一个安全点才停，所以卡在一个不理会取消的阻塞调用里的子代理，等完之后仍可能在运行。这时 `agentao.log` 会记一条 warning 写明它，`close()` 继续往下走。`close()` 之后，这个 agent 的子代理工具会拒绝新的后台启动（`BackgroundOwnerClosedError`）。同一个 `bg_store` 上由别的 agent 启动的任务不受影响
- 断开 agent 自己连接的 MCP 客户端，并关闭该管理器的事件循环线程
- 关闭 agent 自己打开的 memory 存储
- 关闭 `<working_directory>/agentao.log`：除非传了 `logger=` 或自己的 `llm_client=`，agent 会一直打开这个文件（Windows 不能删除打开着的文件，所以在 `close()` 之前删除临时工作目录会报 `WinError 32`）

你传入的管理器（`mcp_manager=`、`memory_manager=`、`llm_client=`），以及之后赋值给 `agent.mcp_manager` / `agent.memory_manager` 的管理器，归你所有：它可能被其他 agent 共用，`close()` 不会关闭它，用完后由你释放（`disconnect_all()`、`close()`）。`build_from_environment()` 会把它创建的 memory 管理器交给 agent，所以那个由 `close()` 释放。

使用临时工作目录时，先关闭 agent，再删除目录：把 `with Agentao(...)` 嵌在 `with tempfile.TemporaryDirectory()` 内部。`logger=` 只让 `agentao.log` 不写进该目录，不能代替 `close()`。

不调用会**泄漏 MCP 子进程和线程**。`Agentao` 本身就是 context manager，用 `with` 时，无论是否出错，退出时都会关闭：

```python
with Agentao(working_directory=Path("/tmp/x")) as agent:
    reply = agent.chat("hi")
```

异步代码里用 `async with`（或 `await agent.aclose()`），它在工作线程上运行 `close()`，断开 MCP 时不会阻塞事件循环，类似 `await asyncio.to_thread(agent.close)`，区别是取消 `await` 也不会阻止 `close()` 执行：

```python
from agentao.embedding import build_from_environment

async def main() -> None:
    async with build_from_environment(working_directory=Path("/tmp/x")) as agent:
        reply = await agent.arun("hi")
```

关闭前先结束 agent 的轮次：两种写法都不会等待或取消正在进行的轮次。用 `async with` 包住 `await agent.arun(...)` 时，即使任务被取消（客户端断开、超时）也满足这一点：被取消的 `arun()` 会取消本轮，并最多等 5 秒让它停下，然后才退出代码块。超过这个时间仍在运行的轮次（例如不检查取消令牌的阻塞工具）会在运行中被关闭；第二次取消打断了这段等待时也是如此。`close()` 可以重复调用，所以在块内显式调用 `close()` 也无妨。agent 的生命周期超出一个代码块时（例如按会话缓存），仍可用 `try/finally` 调用 `close()`。

同一进程里有多个 agent 时，`agentao.log` 的记录写到最近构造、且仍未关闭的那个 agent。关掉它，日志会交还给下一个。见 [6.6](/zh/part-6/6-observability#同一进程里的多个-agent)。

## 异步宿主：`arun()` 与轮次线程池

`arun()` 的参数与 `chat()` 相同（`user_message`、`max_iterations`、`cancellation_token`、`images`），跑的是同一轮，但不阻塞事件循环：

```python
async def handle_request(req):
    async with build_from_environment(working_directory=Path(req.workdir)) as agent:
        return await agent.arun(req.prompt)
```

- **轮次跑在 Agentao 自己的线程池上**，不在事件循环的默认执行器上。线程名为 `agentao-arun-*`。池大小是 `min(32, os.cpu_count() + 4)`，和 asyncio 自己会选的一样。进程内所有 agent 共用这一个池，首次调用 `arun()` 时才创建，所以只用同步接口的宿主不会为它付出代价。
- **为什么不用默认执行器。** 一轮会占住它的工作线程直到结束。中途它可能要等一个跑回你事件循环上的异步工具。如果轮次占满了默认执行器，循环上其他需要工作线程的东西就会饿死，其中包括 `loop.getaddrinfo`，而每个 `httpx.AsyncClient` 按主机名建连都要用它。这样一来，轮次等待的工作永远开始不了（[`_get_arun_pool`](https://github.com/jin-bo/agentao/blob/main/agentao/agent.py)）。
- **容量。** `loop.set_default_executor(...)` 改变不了同时运行的轮次数。要限制并发轮次，在 `arun()` 外面套你自己的信号量。
- **取消。** 取消正在 await 的任务（超时、客户端断开）会通过令牌取消本轮，`arun()` 最多等 5 秒让本轮收尾。也可以传入 `CancellationToken`，从别处取消（[2.6](./6-cancellation-timeouts)）。
- **不要用 `asyncio.to_thread` 包 `chat()`。** 那会把轮次放到默认执行器上，正是上面说的问题。

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
| 异步 Web（FastAPI、asyncio） | `await agent.arun(msg)` |
| 后台批处理 | 每个 job 一个 agent，顺序跑 |
| 多租户 SaaS | 按 tenant 缓存，TTL 过期后 `close()` |

## 完整生命周期示例（FastAPI + 异步 + 会话池）

```python
from asyncio import Lock
from pathlib import Path
from fastapi import FastAPI, HTTPException
from agentao import Agentao
from agentao.embedding import build_from_environment
from agentao.transport import SdkTransport

app = FastAPI()

# session_id -> (agent, lock)
_sessions: dict = {}

async def get_or_create(session_id: str, workdir: Path) -> tuple[Agentao, Lock]:
    if session_id not in _sessions:
        agent = build_from_environment(   # 凭据来自环境变量 / .env
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
        reply = await agent.arun(message)
    return {"reply": reply}

@app.delete("/session/{session_id}")
async def end_session(session_id: str):
    entry = _sessions.pop(session_id, None)
    if entry:
        agent, lock = entry
        try:
            async with lock:       # 等持锁的请求结束（被取消的请求会在
                pass               # 本轮仍在运行时就释放锁）
        finally:                   # 本请求在等待时被取消也要关闭：
            await agent.aclose()   # 条目已经弹出
    return {"ok": True}
```

关键要点：

- `Lock` 保证同一会话**串行**（不能并发 `chat()` 同一实例）
- `arun()` 让本轮跑在 Agentao 自己的线程池上，不堵事件循环
- 显式 `DELETE` 端点触发 `close()` 释放 MCP

生产部署下你还需要加 TTL 淘汰、内存上限、崩溃重启等——见 [第 7 部分](/zh/part-7/)。

## TL;DR

- **一个 agent = 一个有状态会话**。不要每轮重建——会丢上下文。
- `chat()` 是**阻塞且非线程安全**的。异步宿主用同会话锁 + `await agent.arun(...)`，它跑在 Agentao 自己的线程池上，不占事件循环的默认执行器。
- 永远要关闭 agent：用 `with` / `async with`，或调用 `close()` / `await aclose()`——否则会泄露 MCP 子进程和 DB 句柄。
- `clear_history()` 只清 `messages`；**记忆 DB 故意保留**。
- 用 `set_provider()` / `set_model()` 运行时切换模型；历史不变。

→ 更多参数与扩展：[第 5 部分 · 扩展 Agent 行为](/zh/part-5/)
