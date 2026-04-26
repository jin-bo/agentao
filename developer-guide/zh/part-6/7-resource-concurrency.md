# 6.7 资源治理与并发

Agent 是**资源不可预测**的工作负载——一轮简单问答可能 200ms，一轮复杂任务可能 10 分钟、跑几十次工具。生产上不治理，轻则拖垮服务，重则烧钱烧到合规告警。

## 四种资源：要各自限制

| 资源 | 失控后果 | 主要控制点 |
|------|--------|----------|
| LLM token | 账单爆炸 | `max_context_tokens` + `max_iterations` + 预算控制 |
| 时间 | 请求堆积、超时雪崩 | 每 chat() 超时 + 工具超时 |
| 内存 | OOM 崩溃 | 会话池上限 + TTL 淘汰 |
| 文件描述符 / 子进程 | Agent 资源泄漏 | `close()` + MCP 子进程回收 |

## 控制点 1 · 上下文窗口

```python
agent = Agentao(
    max_context_tokens=128_000,   # 默认 200_000
)
```

超过这个数，Agentao 触发**上下文压缩**——用 LLM 总结老消息、保留结构。压缩本身花钱花时间。

**选择经验**：

| 模型 | 推荐 max_context_tokens |
|------|---------------------|
| gpt-5.4 / gpt-4.1 | 100_000–128_000 |
| claude-sonnet-4 | 150_000–200_000 |
| 1M context 类 | 500_000–800_000 |
| 便宜小模型 | 4_000–16_000 |

**不要设成模型的最大**——留 20% 给压缩缓冲。

## 控制点 2 · 迭代上限

```python
reply = agent.chat(msg, max_iterations=50)   # 默认 100
```

配合 `on_max_iterations_callback`（[4.6 节](/zh/part-4/6-max-iterations)）做兜底。

Agentao 也会在单轮内跟踪重复的工具调用失败。完全相同的重复工具调用会触发 doom-loop 保护；同一个工具连续产出无法解析的参数时，会返回 `role:tool` 中止消息，确保下一次 LLM 请求仍满足协议要求，而不是无限重试。

按场景设：

| 任务类型 | max_iterations |
|---------|---------------|
| 简单问答 | 20–30 |
| 代码编辑 / 分析 | 80–150 |
| 研究 / 深度任务 | 200–500 |

## 控制点 3 · 每轮超时

Agentao **本身没有 chat() 超时**——它会一直跑下去。用宿主层加：

```python
import asyncio

async def chat_with_timeout(agent, msg, timeout_s: float = 120):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(agent.chat, msg),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        # 用 cancellation token 优雅取消
        if agent._current_token:
            agent._current_token.cancel("timeout")
        return "[Timeout: agent took too long]"
```

**分级**：

- 用户交互：60-120s（超了用户已经走了）
- 后台任务：5-30min
- 批处理：按任务设

## 控制点 4 · 工具超时

自定义工具**必须**有超时：

```python
class ApiCallTool(Tool):
    def execute(self, **kwargs) -> str:
        try:
            r = httpx.get(kwargs["url"], timeout=10.0)
            return r.text
        except httpx.TimeoutException:
            return "Request timed out"
```

MCP 工具的超时从 `timeout` 字段：

```json
{"mcpServers": {"x": {"command": "...", "timeout": 30}}}
```

## 并发：会话池

### 模式 A · 每请求一实例（简单粗暴）

```python
@app.post("/chat")
async def chat(req):
    workdir = Path(f"/tmp/ephemeral-{uuid.uuid4()}")
    try:
        agent = Agentao(working_directory=workdir)
        return await asyncio.to_thread(agent.chat, req.message)
    finally:
        agent.close()
        shutil.rmtree(workdir)
```

**适合**：无状态场景（每次都是独立问题，不需要跨轮上下文）、低 QPS。
**不适合**：有状态会话、MCP 启动慢（每次要重启所有 MCP 子进程）。

### 模式 B · 会话池 + TTL 淘汰

```python
from time import monotonic
from asyncio import Lock

class AgentPool:
    def __init__(self, max_sessions: int = 500, ttl_s: float = 1800):
        self._pool: dict = {}  # session_id -> (agent, lock, last_used)
        self._global_lock = Lock()
        self.max_sessions = max_sessions
        self.ttl_s = ttl_s

    async def get(self, session_id: str, workdir: Path) -> tuple[Agentao, Lock]:
        async with self._global_lock:
            now = monotonic()
            # 1. 淘汰过期的
            self._evict_expired(now)
            # 2. 淘汰溢出的
            while len(self._pool) >= self.max_sessions:
                self._evict_lru()
            # 3. 创建或返回
            if session_id not in self._pool:
                agent = Agentao(working_directory=workdir)
                self._pool[session_id] = (agent, Lock(), now)
            entry = list(self._pool[session_id])
            entry[2] = now   # touch
            self._pool[session_id] = tuple(entry)
            return entry[0], entry[1]

    def _evict_expired(self, now):
        for sid, (a, _, last) in list(self._pool.items()):
            if now - last > self.ttl_s:
                a.close()
                del self._pool[sid]

    def _evict_lru(self):
        victim = min(self._pool.items(), key=lambda kv: kv[1][2])
        victim[1][0].close()
        del self._pool[victim[0]]
```

**关键点**：

- `Lock()` 保证**同一会话串行**（Agent 不是线程安全的，参见 [2.3](/zh/part-2/3-lifecycle)）
- TTL 淘汰 + LRU 上限一起加，避免无限增长
- 淘汰时记得 `agent.close()` 释放 MCP

### 模式 C · ACP 进程池

ACP 模式下每会话一个子进程——天然进程隔离。适合：

- 多租户 SaaS
- 严格合规场景（崩溃/内存隔离）
- 不同租户用不同 Python 依赖

成本：每个子进程冷启动约 1-2 秒、几十 MB 内存。

## 令牌预算

按用户/租户限总 token 消耗，防账单爆表：

```python
class TokenBudget:
    def __init__(self, daily_limit: int):
        self.daily_limit = daily_limit
        self.used_today: dict = {}  # user_id -> tokens

    def try_reserve(self, user_id: str, tokens: int) -> bool:
        used = self.used_today.get(user_id, 0)
        if used + tokens > self.daily_limit:
            return False
        self.used_today[user_id] = used + tokens
        return True

# 用在 on_max_iterations 和 chat 前
budget = TokenBudget(daily_limit=1_000_000)

def on_event(ev):
    if ev.type == EventType.LLM_TEXT:
        # 简单估 4 字符 ≈ 1 token
        budget.used_today[current_user] = \
            budget.used_today.get(current_user, 0) + len(ev.data["chunk"]) // 4
```

精确计算用 `tiktoken`（`pip install 'agentao[tokenizer]'`）。

## 内存占用

每个 Agent 实例内存：

| 组件 | ~ 大小 |
|------|-------|
| Agent 核心 + tool registry | 3-5 MB |
| 对话历史（上下文满） | 0.5-2 MB |
| Memory DB（打开的 SQLite） | < 1 MB |
| MCP 子进程（stdio 连接） | 50-200 MB（各子进程自己） |

**估算公式**：`总内存 ≈ 会话数 × 10 MB + MCP 数 × 100 MB`

500 个会话 + 3 个常驻 MCP ≈ 5 GB。

## 优雅关闭

```python
import signal
import sys

pool = AgentPool(...)

def shutdown(*_):
    print("Shutting down...")
    for sid, (agent, _, _) in list(pool._pool.items()):
        try:
            agent.close()
        except Exception as e:
            print(f"Error closing {sid}: {e}")
    sys.exit(0)

signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)
```

没这段的话，kill 信号会让 MCP 子进程成孤儿。

## 压测前的 checklist

- [ ] 每用户/租户的并发会话上限
- [ ] 每会话的 token 日预算
- [ ] 每 chat() 的超时
- [ ] max_iterations 的合理默认
- [ ] 会话池 TTL 与 max_sessions
- [ ] 优雅关闭逻辑
- [ ] 监控：活跃会话数、MCP 子进程数、每 session 内存

→ [6.8 容器化与部署](./8-deployment)
