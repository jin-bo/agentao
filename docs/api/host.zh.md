# 嵌入式 Harness API

**包名:** `agentao.host`
**状态:** Stable，自 0.3.1 起。
**设计文档:** [`docs/design/embedded-host-contract.md`](../design/embedded-host-contract.md)
**实施计划（历史档）:** [`docs/implementation/EMBEDDED_HARNESS_CONTRACT_IMPLEMENTATION_PLAN.md`](../implementation/EMBEDDED_HARNESS_CONTRACT_IMPLEMENTATION_PLAN.md)

Harness API 是宿主应用嵌入 Agentao 时面向外部的兼容性边界。运行时内部
类型（`AgentEvent`、`ToolExecutionResult`、`PermissionEngine`）有意不
对外暴露。

> **覆盖范围。** 本包是宿主**进程内**嵌入 Agentao 的稳定合约边界，
> 三个支柱：
>
> - **观测事件**——`ToolLifecycleEvent`、`SubagentLifecycleEvent`、
>   `PermissionDecisionEvent`。
> - **权限状态**——`ActivePermissions` 快照。
> - **ACP schema surface**——ACP wire payload 的版本化 Pydantic 模型，
>   **只**为这种长尾场景导出：in-process 宿主**同时**还要把 Agentao
>   通过 ACP 再暴露给自己的客户端。普通 in-process 宿主用不到这层，
>   完全可以忽略所有 ACP 相关导出。
>
> 本包**不是**完整的聊天 runtime。驱动一轮执行用 `Agentao.arun()`；
> 做流式聊天 UI 从内部的 `Transport` / `AgentEvent` 流取——那里有
> assistant 文本、reasoning、原始工具 I/O，稳定 host contract 有意
> 不包含这些。
>
> > 不确定要用这一层，还是 ACP server（`agentao --acp --stdio`），
> > 还是 ACP client（`ACPManager`）？见
> > [Embedding vs. ACP](../architecture/embedding-vs-acp.zh.md)。

> **导入纪律。** 所有公共类型只活在 `agentao.host` 模块下，**没有**
> 从顶层 `agentao` 包再 re-export。请始终
> `from agentao.host import ...`；不要假定 `agentao.ToolLifecycleEvent`
> 之类的快捷路径存在。

## 公共导出

| 符号 | 用途 |
|---|---|
| `ActivePermissions` | 当前权限策略的只读快照。 |
| `ToolLifecycleEvent` | 一次工具调用生命周期的对外信封。 |
| `SubagentLifecycleEvent` | 子 Agent 任务/会话的血统事件。 |
| `PermissionDecisionEvent` | 每次权限决策的对外投影。 |
| `HostEvent` | 上述三种事件的判别联合。 |
| `RFC3339UTCString` | 所有公共事件使用的受限时间戳类型。 |
| `export_host_event_json_schema()` | 事件 + 权限的规范 JSON schema。 |
| `export_host_acp_json_schema()` | 对外的 ACP 负载规范 JSON schema。 |
| `agentao.host.replay_projection` | 把 `EventStream` 桥接到 replay JSONL 的子模块——见下文 [Replay 投影](#replay-投影agentaohostreplay_projection)。 |

## 能力协议（`agentao.host.protocols`）

嵌入宿主通过向 `Agentao(filesystem=..., shell=..., mcp_registry=..., memory_store=...)`
注入这些 `Protocol` 类型来重定向 IO。该子模块是协议及其值类型的稳定
re-export；**请始终从 `agentao.host.protocols` 导入，不要伸手到内部
的 `agentao.capabilities.*`**（后者是内部实现，可能会移动）。

```python
from agentao.host.protocols import (
    FileSystem, ShellExecutor, MCPRegistry, MemoryStore,
    FileEntry, FileStat, ShellRequest, ShellResult, BackgroundHandle,
)
```

| 符号 | 用途 |
|---|---|
| `FileSystem` | 文件系统 IO 的协议（`read_text`、`write_text`、`iter_dir`、…）。 |
| `ShellExecutor` | Shell 执行 + 后台句柄的协议。 |
| `MCPRegistry` | 运行时使用的 MCP 服务器/工具发现协议。 |
| `MemoryStore` | 持久化记忆存储后端的协议。 |
| `FileEntry`、`FileStat` | `FileSystem` 实现返回的值类型。 |
| `ShellRequest`、`ShellResult`、`BackgroundHandle` | `ShellExecutor` 实现的值类型。 |

`Local*` 默认实现（如 `LocalFileSystem`、`LocalShellExecutor`）保留在
`agentao.capabilities` 中，因为它们是参考实现而非对外注入面的一部分。

## Replay 投影（`agentao.host.replay_projection`）

Harness 事件流和 replay JSONL 是同一组事实的两种视图。本子模块把它们
桥接起来，让嵌入宿主只用维护一份审计产物，而不是两条平行流。

```python
from agentao.host.replay_projection import (
    HostReplaySink,
    replay_payload_to_host_event,
    host_event_to_replay_kind,
    host_event_to_replay_payload,
)
```

| 符号 | 用途 |
|---|---|
| `HostReplaySink(recorder, *, stream=None)` | 正向投影。传入 `stream=agent._host_events` 即可作为同步 observer 注册；之后每个发布的 `ToolLifecycleEvent` / `SubagentLifecycleEvent` / `PermissionDecisionEvent` 会被写入 `recorder` 作为一条 v1.2 replay 事件。写入失败仅记 WARNING 后吞掉——审计存储坏了不能拖垮运行时。 |
| `replay_payload_to_host_event(kind, payload)` | 反向投影。把一行 replay JSONL 还原回 `HostEvent` Pydantic 模型。会先剥掉 sanitizer 注入的可选元字段（`redaction_hits`、`redacted`、`redacted_fields`），让被脱敏过的事件仍能通过公共模型的 `extra="forbid"` 校验。 |
| `host_event_to_replay_kind(event)` / `host_event_to_replay_payload(event)` | 较底层的两个辅助函数，给 sink 与测试使用。分别返回 `None` / `model_dump(mode="json")`。 |

`Agentao.start_replay()` 会自动以 agent 的 `EventStream` 实例化
`HostReplaySink`；`end_replay()` 负责 detach 并清空。手动驱动 replay
子系统的宿主可以照样自己接线。

落盘形状就是公共 Pydantic 模型的 `model_dump(mode="json")`——与 v1.2
replay schema `oneOf` 判别器匹配的字节完全一致。版本兼容契约见
[`docs/replay/schema-policy.md`](../replay/schema-policy.md)。

## 类型门槛

`agentao.host` 在 `mypy --strict` 下保持干净：

```
uv run mypy --strict --package agentao.host
```

CI 的 `Typing gate` Job 在每个 PR 上强制执行。下游项目对自己代码运行
`mypy --strict` 时，可从这一接口面继承到干净的类型；
`tests/test_host_typing.py` 还包含一个模拟下游消费的脚本，覆盖每个
公共名称。

## Schema 快照策略

每个发布版都附带一份签入仓库的 JSON schema 快照：

- `docs/schema/host.events.v1.json` —— 事件 + 权限
- `docs/schema/host.acp.v1.json` —— ACP 负载

`tests/test_host_schema.py` 会从 Pydantic 模型重新生成 schema，
并使用规范化 JSON（`json.dumps(..., sort_keys=True)`）逐字节比对快照。
任何会改变线格式的模型变更，必须在同一个 PR 内同时更新模型与快照。

兼容性规则：

- 增加可选字段：向后兼容。
- 删除字段、重命名字段、变更枚举值或字段语义：必须升 schema 版本并
  写发布说明。
- 公共事件**不得**直接复用内部 `AgentEvent.data` 负载；脱敏/裁剪在
  `agentao/host/projection.py` 内统一进行。
- 公共摘要字段（`summary`、`task_summary`、`reason`）必须经过脱敏与
  截断，永远不得携带原始用户输入、工具参数、工具输出或策略内部细节。
- 所有时间戳采用规范化的 `Z` 后缀，例如 `2026-04-30T01:02:03.456Z`，
  `+00:00` 偏移格式被有意拒绝以保持快照稳定。

## 运行时身份契约

公共事件依赖一组稳定的 id 字段。生成与归一化辅助函数位于
`agentao/runtime/identity.py`，并已在计划、执行、权限决策与子 Agent
派生的运行时边界完成接入。

| 字段 | 来源 |
|---|---|
| `session_id` | 优先使用持久化会话 id；构造时分配 UUID4 兜底。 |
| `turn_id` | 在 `agentao/runtime/turn.py` 入口生成 UUID4，对应一次用户提交的 Agent 循环。 |
| `tool_call_id` | 优先使用 LLM 提供的 id；缺失时生成 UUID4，并在计划阶段一次性归一化复用。 |
| `decision_id` | 每次权限决策生成一个 UUID4。 |
| `child_task_id` / `child_session_id` | 在子 Agent 派生时捕获，不在结束时回溯推断。 |

`tool_call_id` 的唯一性范围是 `(session_id, turn_id, tool_call_id)`；
不假设提供方生成的 id 全局唯一。

## 事件订阅语义

`Agentao.events(session_id: str | None = None)` 返回 `HostEvent`
的异步迭代器。传 `session_id=` 进行过滤；传 `None` 订阅本 `Agentao`
实例下所有会话。

- 同会话顺序保证保留。
- 同一个 `tool_call_id` 内，`PermissionDecisionEvent` 必须在
  `ToolLifecycleEvent(phase="started")` 之前发出。
- 跨会话的全局顺序不做保证。
- 在订阅之前发出的事件直接丢弃 —— **不重放**。中途订阅的消费者只能
  接收到未来事件。
- 背压由宿主拉动；实现不会无界扩张队列；订阅队列满时，生产者会针对
  匹配的事件阻塞。
- 取消迭代器必须释放队列/订阅资源。
- MVP 每个 filter 仅允许 **一个 async 迭代器** 消费者
  （`Agentao.events(session_id=…)`）；同 filter 的第二个迭代器会
  抛出 `StreamSubscribeError`。多 sink 扇出（审计 / 指标 / replay）
  请使用同步 observer，见下文
  [同步 observer 扇出](#同步-observer-扇出)。

下表只描述 async 迭代器投递；observer 投递独立，见下一节。

| 状态 | 语义 |
|---|---|
| 无订阅者 | 公共事件直接丢弃，不阻塞 Agent 循环。 |
| 事件已发出后才出现订阅者 | 不重放，仅接收未来事件。 |
| 订阅队列有容量 | 按发射顺序入队匹配事件。 |
| 订阅队列已满 | 阻塞生产者直到出现容量或流被取消。 |
| 订阅取消 / 迭代器关闭 | 释放队列资源；后续事件遵循 "无订阅者" 行。 |

### 同步 observer 扇出

当宿主需要把每个事件投递到多个轻量 sink（审计日志、指标计数、
replay 记录器、调试打印）时，"单消费者 async 迭代器" 并不是合适
的工具——直接在底层 `EventStream` 上注册同步 observer。

```python
stream = agent._host_events  # 内部访问器，见下方说明

def audit(event: HostEvent) -> None:
    audit_log.write(event.model_dump_json())

def metrics(event: HostEvent) -> None:
    counter.labels(event.event_type).inc()

stream.add_observer(audit)
stream.add_observer(metrics)
```

语义：

- Observer 在 **生产者线程内联** 执行，先于 async 订阅者收到。回调
  必须便宜、不阻塞——任何阻塞 observer 都会对所有 emit 点形成压力。
- Observer 数量 **无上限**；一个事件按注册顺序广播到每个回调。
- Observer 抛出的异常会被捕获、WARNING 级别记录然后丢弃——一个坏
  sink 不会拖垮 runtime。
- Observer 收到 **全部** 事件（无 per-observer filter）；如需过滤，
  在回调内自行检查 `event.session_id`。
- `remove_observer(callback)` 用于解除注册；幂等，重复调用安全。

`HostReplaySink` 就是这套机制的典型用户——见上文
[Replay 投影](#replay-投影-agentaohostreplay_projection)。

> **访问器说明。** runtime 目前通过 `agent._host_events` 暴露底层
> `EventStream`——前置下划线是一个已知的小问题，后续版本会提升为
> 稳定访问器。`add_observer` / `remove_observer` 本身的形状是稳定的。

## 想要更细粒度的事件？内部 `Transport` 通道

上面这套 host contract **故意保持窄**——三类 Pydantic 事件家族、有
版本化 schema 快照、有稳定性承诺。除此之外还有一条**更宽**的事件
通道：内部 `Transport` / `AgentEvent` 流。需要更细可视化（LLM 调用
用量、内存写删、hook 触发、skill 切换、上下文压缩）的宿主，可以在
构造时挂回调：

```python
from agentao import Agentao
from agentao.transport import SdkTransport

events = []
transport = SdkTransport(on_event=events.append)
agent = Agentao(transport=transport, ...)

# 一轮结束后：
for ev in events:
    print(ev.type, ev.data)            # ev 是 AgentEvent dataclass
    wire = ev.to_dict()                # {"type", "schema_version", "data"}
```

### 当前 `Transport` 都流些什么

权威清单见 `agentao/transport/events.py::EventType`。截至目前：

| 家族 | 成员 |
|---|---|
| Turn / loop | `TURN_START` |
| 工具执行（原始） | `TOOL_START`、`TOOL_OUTPUT`、`TOOL_COMPLETE`、`TOOL_RESULT` |
| LLM 调用 | `LLM_CALL_STARTED`、`LLM_CALL_COMPLETED`、`LLM_CALL_DELTA`、`LLM_CALL_IO`、`LLM_TEXT`、`THINKING` |
| 子 Agent（原始） | `AGENT_START`、`AGENT_END` |
| 交互 | `TOOL_CONFIRMATION`、`ASK_USER_REQUESTED`、`ASK_USER_ANSWERED` |
| 历史 | `BACKGROUND_NOTIFICATION_INJECTED`、`CONTEXT_COMPRESSED`、`SESSION_SUMMARY_WRITTEN` |
| Memory | `MEMORY_WRITE`、`MEMORY_DELETE`、`MEMORY_CLEARED` |
| Runtime 状态 | `SKILL_ACTIVATED`、`SKILL_DEACTIVATED`、`MODEL_CHANGED`、`PERMISSION_MODE_CHANGED`、`READONLY_MODE_CHANGED`、`PLUGIN_HOOK_FIRED` |
| 错误 | `ERROR` |

每个 `AgentEvent` 都带 `schema_version: int` 字段；这是载荷形状变化
的**唯一**信号。

### 稳定性——真正决定怎么用的部分

|  | `HostEvent`（本合约） | `AgentEvent`（`Transport`） |
|---|---|---|
| `docs/schema/` 下有 schema 快照？ | ✅ `host.events.v1.json` | ❌ |
| 字段重命名/删除会触发版本 bump？ | ✅ 由 `tests/test_host_schema.py` 强制 | ⚠️ 受影响载荷上 best-effort `schema_version` bump |
| 有脱敏/投影层？ | ✅ `agentao/host/projection.py` 剥掉原始 input/output | ❌ 原始载荷（LLM_CALL_IO 可含完整 prompt 与工具 I/O） |
| 发布前跨版本兼容性审计？ | ✅ 是发布检查表项 | ❌ |
| 适合走长期稳定的 wire 协议？ | ✅ | ⚠️ 仅当你 pin 住 `schema_version` 并自负升级路径 |

### 什么时候用哪个

- **审计、合规、计费、第三方 UI：** `HostEvent`。schema 就是合约。
- **本进程诊断、开发面板、replay 抓包、自家团队用的成本看板：**
  `Transport` / `AgentEvent`。挂上去成本低、无投影开销、所有内部
  事实都能拿到。
- **同时用：** 很常见——`EventStream` 上挂 `add_observer` 喂稳定
  sink，加一个 `SdkTransport(on_event=...)` 喂 firehose。两条路径
  独立运行、互不干扰。

### 已知缺口（两条通道目前都覆盖不到）

- **MCP server 生命周期。** Connect / disconnect / `auth_failed`
  两条通道都不发。MCP 掉线时宿主只能等工具调用开始失败才间接
  察觉。已记录在
  [PUBLIC_EVENT_PROMOTION_PLAN](../implementation/PUBLIC_EVENT_PROMOTION_PLAN.md)。
- **LLM 速率限制信号。** Provider 端 429 只能从 `ERROR` 的文本里
  嗅出来。promote 成结构化的 `LLMCallEvent(error_type="rate_limited")`
  也在同一份 plan 里。

## 非目标

- 公共 Agent 图存储 / 子孙查询 API。
- 对外的 hooks list/disable API。
- 对外的 MCP reload API。
- MCP 和 hook 的公共生命周期事件。
- 本地插件 export/import；远端插件分享。
- 外部会话导入。
- 生成的客户端 SDK。
- 超出签入快照的完整 schema 治理流水线。

这些项目刻意不纳入嵌入式 harness。CLI 可以基于相同事件构建自己的 UI，
但其本地存储与命令不会被提升为 harness API。
