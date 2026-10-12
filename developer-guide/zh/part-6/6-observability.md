# 6.6 可观测性与审计

> **本节你会学到**
> - 4 个观测维度：结构化日志、指标、追踪、会话 replay
> - 生产部署最少应该上报哪些指标
> - 什么时候只有会话 replay 才能诊断"Agent 为什么会做出 X"这种问题

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

- 每次 LLM 请求/响应（完整 content——不截断，但形似凭据的字符串写入时会脱敏为 `[REDACTED:<kind>]`——以及 tokens、模型）
- 每次工具调用参数和结果
- MCP server 启停
- 插件 hook 分发
- 上下文压缩触发

这是你**调试 Agent 行为**的最重要工具。生产上：

1. 把它挂到持久化卷（容器重启不丢）
2. 按天切分 + 保留 7-30 天
3. 做[脱敏](./5-secrets-injection#四-日志脱敏)
4. 按租户分文件（`working_directory` 天然分）

### 接管 Agentao 的 logger

`LLMClient.__init__` 默认会改写 `logging.getLogger("agentao")` ——
把 level 设成 `DEBUG`、挂一个写到 `<wd>/agentao.log` 的
`RotatingFileHandler`、重建时还会驱逐自己 marker 标记的 handler。
宿主想完全掌控日志栈，应该**显式注入 logger**，让这套改写根本不发生：

```python
import logging
from agentao import Agentao

# 自己的 logger ——JSON handler 推给 Loki / CloudWatch / ELK
import pythonjsonlogger.jsonlogger as jl
my_logger = logging.getLogger("myapp.agentao")
handler = logging.StreamHandler()
handler.setFormatter(jl.JsonFormatter())
my_logger.addHandler(handler)
my_logger.setLevel(logging.INFO)

agent = Agentao(
    api_key=..., base_url=..., model=...,
    working_directory=workdir,
    logger=my_logger,            # ← 跳过对包根 logger 的改写
)
```

只要传了 `logger=`，`LLMClient` 在建 file handler **之前**就早返回，
所以默认那条 `<wd>/agentao.log` 也不会被创建。
完全静默的写法配 `NullHandler`：

```python
quiet = logging.getLogger("myapp.agentao")
quiet.addHandler(logging.NullHandler())
quiet.propagate = False
agent = Agentao(..., logger=quiet)
```

::: warning 踩坑提醒
在 `getLogger("agentao")` 上 `addHandler(...)` 而**不**传 `logger=`，handler 是会生效，
但包根 logger 的 level 仍被强行改成 `DEBUG`，rolling 的 `agentao.log` 文件也照样在
你的 handler 旁边继续写。要彻底关掉那个文件：要么注入自己的 logger（上文），
要么自己构造 `LLMClient` 时传 `log_file=None`。
:::

两个开关都在 `LLMClient.__init__` 上（[`llm/client.py`](https://github.com/jin-bo/agentao/blob/main/agentao/llm/client.py)）：

| 你想要 | 传什么 |
|--------|--------|
| 不生成 `agentao.log` 文件 | 自己构造 `LLMClient` 时传 `log_file=None` |
| 完全不动 `logging.getLogger("agentao")`：不设 level、不挂 handler、不清理 | 在 `Agentao` 或 `LLMClient` 上传 `logger=` |
| 两者都要 | 只传 `logger=` 就够了，它会一并跳过文件 handler |

只传 `log_file=None` 时，`"agentao"` logger 仍被设成 `DEBUG`，所有 `agentao.*` 模块都会以 DEBUG 级别写进上游已挂的 handler。自己构造 client 的宿主可以两项都设：

```python
from agentao.llm import LLMClient

llm = LLMClient(
    api_key=..., base_url=..., model=...,
    log_file=None,     # 不写 agentao.log
    logger=quiet,      # 不动 "agentao" logger
)
agent = Agentao(llm_client=llm, working_directory=workdir)
```

包里其他 logger（`agentao.mcp`、`agentao.tools.web`、`agentao.acp_client` 等）从不自己挂 handler。传了 `logger=` 后，它们跟随宿主已有的传播和 handler 配置。

### 同一进程里的多个 agent

文件 handler 挂在共享的 `"agentao"` logger 上，每个新的 `LLMClient` 都会替换上一个的 handler。所以同一进程里有多个 agent 时，记录写进**最近构造、且仍未关闭的那个 agent** 的 `agentao.log`。关掉这个 agent，logger 会交还给次新的未关闭 agent，其他 agent 照常记日志。没有 `close()` 就被丢弃的 client 不会再拿回日志。

`agent.close()`（或 `with` / `async with` / `aclose()`）会摘下并关闭它自己的 client 挂上的 handler，之后宿主就能删除工作目录，Windows 也一样。你自己构造并以 `llm_client=` 传入的 client 归你管：调用 `llm.close()`。

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

## 维度三：Session Replay

Agentao 可以把每个 session 的运行时间线记录成 `.agentao/replays/` 下的 append-only JSONL。按项目开启：

```bash
/replay on
```

这会写入 `.agentao/settings.json`：

```json
{
  "replay": {
    "enabled": true,
    "max_instances": 20
  }
}
```

录制默认关闭。开关从下一个 session 起生效，不会动正在写的那个文件。执行 `/replay off` 后，已有 replay 文件仍可读取。`settings.json` 坏了会退回默认值，不会挡住启动。

`replay.max_instances` 限制 `.agentao/replays/` 下的文件数（默认 20），不影响 `.agentao/sessions/`。每个 instance 开始和结束时都会清掉最旧的文件。不是计数的值（`true`、`"abc"`、`0`）会被忽略、改用 20，`agentao doctor` 报 error；写成字符串的数字（`"50"`）或带小数的数字（`2.9` 取 2）照样生效，doctor 报 warning。规则见 [`check_max_instances`](https://github.com/jin-bo/agentao/blob/main/agentao/replay/config.py)。

Replay 能支持：

- **按会话重放**（线上 UI 重建问题现场）
- **回溯调试**（看 LLM 在哪一步做了错决定）
- **合规审计**（用户 X 在时间 Y 让 Agent 做了 Z）

### 命令

```bash
/replay list            # 列出 replay instances（裸 /replay 默认也是这个）
/replay on | /replay off  # 开关录制（写入 .agentao/settings.json）
/replay show <id>       # 分组渲染
/replay show <id> --raw
/replay show <id> --turn <turn_id>
/replay show <id> --kind tool_    # 按 kind 前缀过滤
/replay show <id> --errors
/replay tail <id> 50
/replay prune
/replay delete <id> | /replay delete all
```

所有命令都只读到文件当前的末尾，没有 follow / 实时 tail 模式；实时观察请看正常输出和 `agentao.log`。命令细节见 [CLI · 回放与输出](/zh/cli/9-replay-output)。

Replay 文件和保存的 session 是两套东西：`save_session` / `load_session` 恢复可继续对话的 conversation state；replay 记录 runtime 做过什么。ACP `session/load` 沿用逻辑上的 `session_id`，但会新开一个 replay 文件，不会接着旧文件写。

### Replay 文件布局

- **路径：** `.agentao/replays/<session_id>.<instance_id>.jsonl`。session id 里字母、数字、`_`、`-` 以外的字符会换成 `_`。
- **一行一个事件。** 每个事件都有 `event_id`、`session_id`、`instance_id`、`seq`、`ts`、`kind`、`turn_id`、`parent_turn_id` 和 `payload`。
- **第一行**是 `replay_header`：`schema_version`（当前为 `1.4`）、`session_id`、`instance_id`、`created_at`，设置了 capture flag 时还有 `capture_flags`。
- **最后一行**是正常关闭时写的 `replay_footer`：`event_count`，以及按类别累计的 `redaction_hits`、`dropped_field_hits`、`truncated_field_hits`。
- **每次 session 诞生都开新 instance。** `/clear`、`/new`、`/sessions resume`、ACP `session/new` 和 ACP `session/load` 都会新开一个文件，一个文件不会跨越这些边界。

JSON Schema 在 [`schemas/`](https://github.com/jin-bo/agentao/tree/main/schemas) 下，每个 schema 版本一个文件。版本策略见 [`docs/reference/replay-schema-policy.md`](https://github.com/jin-bo/agentao/blob/main/docs/reference/replay-schema-policy.md)。

### Replay 记录了什么

一个 turn 对应一次 `Agentao.chat()` 调用。事件类别分组如下（完整列表见 [`agentao/replay/events.py`](https://github.com/jin-bo/agentao/blob/main/agentao/replay/events.py)）：

| 分组 | 类别 | 说明 |
|------|------|------|
| 文件与 session | `replay_header`、`replay_footer`、`session_started`、`session_ended` | `session_started` 带 `cwd` 和 `model` |
| Turn | `turn_started`、`user_message`、`turn_completed` | `turn_completed` 带 `final_text`、`status`（`ok` / `cancelled` / `error`），可能还有 `incomplete_reason` |
| 模型输出 | `assistant_text_chunk`、`assistant_thought_chunk` | 流式正文与推理 |
| LLM 调用 | `llm_call_started`、`llm_call_completed`、`llm_call_delta`、`llm_call_io` | `llm_call_completed` 带 token 数和耗时；`llm_call_delta` 需要 `capture_llm_delta`，`llm_call_io` 需要 `capture_full_llm_io` |
| 工具 | `tool_confirmation_requested`、`tool_confirmation_resolved`、`tool_started`、`tool_output_chunk`、`tool_completed`、`tool_result` | `tool_started.tool_source`：`mcp_*` 工具为 `mcp`，其余为 `builtin` |
| 宿主投影 | `tool_lifecycle`、`subagent_lifecycle`、`permission_decision` | 结构与 [`agentao.host`](/zh/part-4/7-host-contract) 的模型一致 |
| 子 agent | `subagent_started`、`subagent_completed` | 子 agent 的事件有自己的 `turn_id`，用 `parent_turn_id` 指回父 turn |
| 交互 | `ask_user_requested`、`ask_user_answered`、`background_notification_injected` | |
| 状态变化 | `model_changed`、`permission_mode_changed`、`readonly_mode_changed`、`skill_activated`、`skill_deactivated`、`memory_write`、`memory_delete`、`memory_cleared`、`session_summary_written`、`plugin_hook_fired` | |
| 上下文 | `context_compressed`、`compaction_settled`、`images_removed` | 每次压缩尝试看 `compaction_settled`，成功的看 `context_compressed` |
| 错误 | `error` | |

有三个类别只声明、不发出：`session_saved`、`session_loaded`、`session_forked`。`session_saved` 留给将来显式的保存入口；`/clear`、`/new` 或退出时的自动保存都不会发它。

### 隐私与截断

每个 payload 落盘前都要过一遍 sanitizer（[`agentao/replay/sanitize.py`](https://github.com/jin-bo/agentao/blob/main/agentao/replay/sanitize.py)）：

- **JSON 化。** 值被转成 JSON 原生类型。转不了的字段单独丢掉，事件上会带 `redacted: "filter_error"`，字段名列在 `redacted_fields` 里。
- **凭据扫描。** 每个字符串都会按"长得像凭据"的模式扫描，命中改写成 `[REDACTED:<kind>]`。每个事件在 `redaction_hits` 里记自己的命中数，footer 汇总。这只是模式匹配，不是完整的 secret 扫描：长得不像凭据的凭据会原样通过。扫描器是 [`agentao/security/secret_scan.py`](https://github.com/jin-bo/agentao/blob/main/agentao/security/secret_scan.py)，`agentao.log` 和 `.agentao/tool-outputs/` 也用它。
- **头尾截断。** 过长的字段保留头和尾，并带 `truncated`、`original_chars`、`omitted_chars`。上限：`tool_output_chunk` 4,000 字符，`tool_result` 8,000（头部只占 20%，因为报错多在末尾），`memory_write` 2,000，`ask_user_answered` 500，`plugin_hook_fired` 的输出 500。`assistant_text_chunk` 不截断。

下面的 deep capture 开关可以取消其中部分上限，凭据扫描照常进行。

### 捕获深度

默认 replay 会记录 turn 边界、用户消息、assistant chunk、工具生命周期、权限决策、sub-agent 生命周期、错误、状态变化，以及紧凑的 LLM delta。

Deep capture 开关位于 `.agentao/settings.json` 的 `replay.capture_flags` 下：

| 开关 | 默认 | 风险 |
|------|------|------|
| `capture_llm_delta` | `true` | 普通 replay 历史 delta |
| `capture_full_llm_io` | `false` | 完整 provider payload；敏感 |
| `capture_tool_result_full` | `false` | 完整工具输出；可能很大或敏感 |
| `capture_plugin_hook_output_full` | `false` | 完整 plugin hook 输出 |

### 故障模型与读取

- Replay 绝不会弄坏一个 turn。sanitizer 或写入失败只记一条 warning，然后吞掉。
- 进程崩溃可能留下半行。读取时会跳过它，前面的事件照常返回。
- 在代码里读 replay，用 `agentao.replay.ReplayReader`（以及 `list_replays`）。基于 replay 做的工具都应该经由它读取，而不是从 `agent.messages` 重建时间线。ACP 没有提供 replay 的方法。

### 自定义归档 hook

优先使用内建 replay。只有当你需要把部分事件送入自己的审计管线时，再额外加 `on_event` archiver：

```python
def audit_event(ev):
    if ev.type in {EventType.TOOL_COMPLETE, EventType.ERROR}:
        audit_log.info("agent_event", extra={
            "type": ev.type.value,
            "session_id": session_id,
            "tenant_id": tenant.id,
            **ev.data,
        })

transport = SdkTransport(on_event=audit_event)
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

### 内层 span：用事件流，别 monkey-patch

不用去包 `LLMClient` 或 `Tool.execute`——运行时已经把生命周期发成事件了。两个接入点，取舍不同：

| 接入点 | 拿到什么 | 稳定性 |
|---|---|---|
| `Agentao.events()` / `add_host_event_observer()` | 带类型的 `HostEvent`：工具 / 子代理 / 权限决策，自带 `session_id`、`turn_id`、RFC3339 起止时间戳 | `agentao.host` 公共契约 |
| `Agentao(transport=SdkTransport(on_event=...))` / `transport.subscribe()` | 全部 `AgentEvent`，**含 LLM 调用** | **不在** host 稳定边界内，字段可能随版本变 |

工具 span 用第一个——`ToolLifecycleEvent` 的字段本来就是 span 形状。LLM span 目前只能用第二个：`HostEvent` 联合里没有 LLM 事件（`agentao/host/models.py:158`）。

```python
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from agentao.transport import EventType, SdkTransport

tracer = trace.get_tracer(__name__)
open_spans = {}

def on_event(ev):
    if ev.type == EventType.LLM_CALL_STARTED:
        span = tracer.start_span("chat")
        span.set_attribute("gen_ai.system", "openai")
        span.set_attribute("gen_ai.request.model", ev.data["model"])
        open_spans[ev.data["attempt"]] = span
    elif ev.type == EventType.LLM_CALL_COMPLETED:
        span = open_spans.pop(ev.data["attempt"], None)
        if span is None:
            return
        for attr, key in (
            ("gen_ai.usage.prompt_tokens", "prompt_tokens"),
            ("gen_ai.usage.completion_tokens", "completion_tokens"),
            ("gen_ai.response.finish_reason", "finish_reason"),
        ):
            if ev.data.get(key) is not None:   # 失败的调用这三项都是 None
                span.set_attribute(attr, ev.data[key])
        if ev.data["status"] == "error":
            span.set_status(Status(StatusCode.ERROR, ev.data["error_class"]))
        span.end()

agent = Agentao(working_directory=".", transport=SdkTransport(on_event=on_event))
```

### 属性从哪个事件来

| 属性 | 来源事件 | 载荷键 |
|---|---|---|
| `gen_ai.system` | —— | 按 provider 自己填 |
| `gen_ai.request.model` / `.temperature` / `.max_tokens` | `LLM_CALL_STARTED` | `model` / `temperature`（未设置时为 `None` —— 请求里没发，跳过该属性即可）/ `max_tokens` |
| `gen_ai.usage.prompt_tokens` / `completion_tokens` | `LLM_CALL_COMPLETED` | 同名 |
| `gen_ai.response.finish_reason` | `LLM_CALL_COMPLETED` | `finish_reason`（失败时为 `None`） |
| 工具 span 名 / span id / 起止时间 | `ToolLifecycleEvent` | `tool_name` / `tool_call_id` / `started_at`、`completed_at` |

参考 OpenTelemetry GenAI 语义约定。

### 两个坑

- **原始 `AgentEvent` 不带时间戳，也不带 turn_id。** `TURN_BEGIN` 的载荷只有 `user_message`，所以订阅者要在收到时自己打时间戳，turn 靠 `TURN_BEGIN`…`TURN_END` 成对配。类型化的 `HostEvent` 两样都有——能用第一个接入点的地方就用它。
- **监听器在发射线程上同步执行**，抛出的异常记日志后被吞掉（`agentao/transport/broadcast.py:42`）。导出器必须挂 OTel 的批处理 processor，别在回调里做网络 I/O——那是在给每一轮加延迟。

> **Agentao 自身不依赖 OpenTelemetry，也没有内建 exporter。** 上面全部是宿主侧接线，`opentelemetry-*` 装在你的应用里。这是刻意的边界；同类 harness 的四种做法对照见 `docs/design/otel-peer-survey.zh.md`。

## 审计与合规

### 必须留的审计事件

| 场景 | 触发 | 保留多久 |
|------|-----|--------|
| 用户发起会话 | 构造 Agent | 90-365 天 |
| 用户批准危险工具 | confirm_tool = True | 180-365 天 |
| 权限规则拒绝 | decide = DENY | 90 天 |
| Agent 修改用户数据 | 业务工具执行 | 业务规定（常 1-7 年） |
| 用户请求"遗忘" | `MemoryManager.wipe_all()` | 永久（合规证据） |

### 脱敏与留档

审计日志**不应**脱敏（否则失去证据效力），但应**加密 at-rest** 和**严格访问控制**。

合规要求下：日志改动/删除需要 append-only 存储（如 WORM 存储）。

## 最小可部署观测栈

预算有限时：

1. `agentao.log` → 每租户独立文件、日切、保 14 天
2. `prometheus_client` → 上面 5 个关键指标、Grafana 面板
3. 内建 replay JSONL → `.agentao/replays/`，通过 `replay.max_instances` 控制保留量
4. 无 OpenTelemetry

这套够 99% 的中小 SaaS 用。上规模后再加 APM。

## TL;DR

- **四个维度**：结构化日志（`agentao.log`）、指标（Prometheus / StatsD）、追踪（OpenTelemetry）、会话 replay。
- 最少上报这些指标：按工具名拆的调用率、工具失败率、LLM 5xx 率、confirm 超时率、轮次延迟 p50/p95/p99、最大迭代命中率。
- **会话 replay 是杀手特性**——出现"Agent 为什么会做 X"时，用 `replay_config=` 和 `start_replay()` 记录会话，再查看记录下的运行时间线，逐步排查。replay 只记录运行过程，不会重新执行。
- 把成本作为一等观测目标：跟踪每轮 token 数和按租户的 token 数；突然 2× 飙升通常是切了模型或换了技能。

→ [6.7 资源治理与并发](./7-resource-concurrency)
