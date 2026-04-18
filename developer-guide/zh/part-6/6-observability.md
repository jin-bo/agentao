# 6.6 可观测性与审计

Agent 是"长尾出 bug"的典型——90% 时间好好的，10% 出现让你无从下手的行为。没有观测就没法诊断，没法诊断就没法改进。

## 四个观测维度

```
┌─────────────────────────────────────────────┐
│ 1. 结构化日志：发生了什么？                  │
│    agentao.log + 你的应用日志                │
├─────────────────────────────────────────────┤
│ 2. 指标：多少、多快、多贵？                  │
│    调用次数 / 延迟 / token / 失败率           │
├─────────────────────────────────────────────┤
│ 3. 事件流：按会话重放                        │
│    AgentEvent 存档                           │
├─────────────────────────────────────────────┤
│ 4. 分布式追踪：一次请求的全链路              │
│    OpenTelemetry                             │
└─────────────────────────────────────────────┘
```

## 维度一：结构化日志

### Agentao 自带的 agentao.log

默认写到 `<working_directory>/agentao.log`，内容**非常详尽**：

- 每次 LLM 请求/响应（完整 content、tokens、模型）
- 每次工具调用参数和结果
- MCP server 启停
- 插件 hook 分发
- 上下文压缩触发

这是你**调试 Agent 行为**的最重要工具。生产上：

1. 把它挂到持久化卷（容器重启不丢）
2. 按天切分 + 保留 7-30 天
3. 做[脱敏](./5-secrets-injection#四日志脱敏)
4. 按租户分文件（`working_directory` 天然分）

### 接管 Agentao 的 logger

```python
import logging

# 全部事件都在这个 logger 下
agentao_logger = logging.getLogger("agentao")

# 加一个 JSON 格式的 handler 推给 Loki / CloudWatch / ELK
import pythonjsonlogger.jsonlogger as jl
handler = logging.StreamHandler()
handler.setFormatter(jl.JsonFormatter())
agentao_logger.addHandler(handler)
agentao_logger.setLevel(logging.INFO)
```

### 关键字段

```python
# 在你的 on_event 里补充业务字段
def on_event(ev):
    logger.info("agent_event", extra={
        "event_type": ev.type.value,
        "session_id": current_session_id(),
        "tenant_id": current_tenant_id(),
        "user_id": current_user_id(),
        **ev.data,
    })
```

**session_id / tenant_id / user_id** 是查问题时最常用的过滤维度。

## 维度二：指标

### 必须打的指标

| 指标 | 类型 | 含义 |
|------|-----|------|
| `agent.turn.count` | counter | 每轮 chat() 次数 |
| `agent.turn.duration_ms` | histogram | 每轮耗时 |
| `agent.tool.calls` | counter by tool | 每个工具调用次数 |
| `agent.tool.failures` | counter by tool | 每个工具失败次数 |
| `agent.tool.duration_ms` | histogram by tool | 每个工具耗时 |
| `agent.llm.tokens.prompt` | counter | prompt token 累计 |
| `agent.llm.tokens.completion` | counter | completion token 累计 |
| `agent.llm.tokens.cached` | counter | prompt cache 命中 token |
| `agent.llm.errors` | counter by error_type | LLM 错误 |
| `agent.confirm.requests` | counter by outcome | 确认请求/允许/拒绝/超时 |
| `agent.max_iterations.hits` | counter | 兜底触发次数 |

### Prometheus 接入样板

```python
from prometheus_client import Counter, Histogram

turn_dur = Histogram("agent_turn_duration_ms", "Turn duration",
                     buckets=[100, 500, 1000, 3000, 10_000, 30_000])
tool_calls = Counter("agent_tool_calls", "Tool invocations", ["tool", "status"])

def on_event(ev):
    if ev.type == EventType.TOOL_COMPLETE:
        tool_calls.labels(tool=ev.data["tool"], status=ev.data["status"]).inc()

start = time.time()
reply = agent.chat(msg)
turn_dur.observe((time.time() - start) * 1000)
```

### 告警阈值

| 指标 | 常见阈值 |
|------|--------|
| 工具失败率 > 10% | 工具坏了或权限配错 |
| LLM 5xx 率 > 2% | LLM 厂商有问题 |
| max_iterations 命中率 > 5% | Agent 卡死模式 |
| cache 命中率 < 30% | 系统提示在抖 |
| confirm 超时率 > 10% | UI 问题或用户流失 |

## 维度三：事件流存档

每会话的 `AgentEvent` 流如果能**完整保存**，你能做：

- **按会话重放**（线上 UI 重建问题现场）
- **回溯调试**（看 LLM 在哪一步做了错决定）
- **合规审计**（用户 X 在时间 Y 让 Agent 做了 Z）

### 存档格式

一行一个事件的 JSONL：

```json
{"ts": 1704067200.1, "session": "sess-123", "type": "turn_start", "data": {}}
{"ts": 1704067200.3, "session": "sess-123", "type": "tool_start", "data": {"tool": "get_customer_orders", "args": {"customer_id": "c-42"}, "call_id": "..."}}
{"ts": 1704067200.8, "session": "sess-123", "type": "tool_complete", "data": {"tool": "get_customer_orders", "status": "ok", "duration_ms": 500, "call_id": "..."}}
```

### 实现

```python
import json, time
from pathlib import Path

class EventArchiver:
    def __init__(self, path: Path, session_id: str, tenant_id: str):
        self.f = path.open("a", encoding="utf-8")
        self.session_id = session_id
        self.tenant_id = tenant_id

    def __call__(self, ev):
        self.f.write(json.dumps({
            "ts": time.time(),
            "session": self.session_id,
            "tenant": self.tenant_id,
            "type": ev.type.value,
            "data": ev.data,
        }) + "\n")
        self.f.flush()

    def close(self):
        self.f.close()

archiver = EventArchiver(
    path=Path(f"/data/tenant-{tenant.id}/events.jsonl"),
    session_id=session_id, tenant_id=tenant.id,
)
transport = SdkTransport(on_event=archiver)
```

## 维度四：分布式追踪

当 Agent 嵌入你的 Web 服务时，一次用户请求可能横跨：

```
浏览器 → 你的 API → Agent.chat() → LLM API → Agent → 自定义工具 → 数据库
```

**OpenTelemetry** 把这些串成一个 trace：

```python
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

@app.post("/chat")
async def chat(req: ChatRequest):
    with tracer.start_as_current_span("user_chat") as span:
        span.set_attribute("user.id", req.user_id)
        span.set_attribute("session.id", req.session_id)
        with tracer.start_as_current_span("agent_chat"):
            reply = await asyncio.to_thread(agent.chat, req.message)
        return {"reply": reply}
```

更深度接入：包装 LLMClient / Tool 的 execute，把每次调用都埋点。

### LLM 调用的 span 属性建议

- `gen_ai.system` = "openai"
- `gen_ai.request.model` = 模型名
- `gen_ai.usage.prompt_tokens` / `completion_tokens`
- `gen_ai.response.finish_reason`

参考 OpenTelemetry GenAI 语义约定。

## 审计与合规

### 必须留的审计事件

| 场景 | 触发 | 保留多久 |
|------|-----|--------|
| 用户发起会话 | 构造 Agent | 90-365 天 |
| 用户批准危险工具 | confirm_tool = True | 180-365 天 |
| 权限规则拒绝 | decide = DENY | 90 天 |
| Agent 修改用户数据 | 业务工具执行 | 业务规定（常 1-7 年） |
| 用户请求"遗忘" | memory.clear_all | 永久（合规证据） |

### 脱敏与留档

审计日志**不应**脱敏（否则失去证据效力），但应**加密 at-rest** 和**严格访问控制**。

合规要求下：日志改动/删除需要 append-only 存储（如 WORM 存储）。

## 最小可部署观测栈

预算有限时：

1. `agentao.log` → 每租户独立文件、日切、保 14 天
2. `prometheus_client` → 上面 5 个关键指标、Grafana 面板
3. 事件 JSONL → 每会话一文件，保 7 天
4. 无 OpenTelemetry

这套够 99% 的中小 SaaS 用。上规模后再加 APM。

→ [6.7 资源治理与并发](./7-resource-concurrency)
