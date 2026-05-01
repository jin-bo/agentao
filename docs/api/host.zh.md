# 嵌入式 Harness API

**包名:** `agentao.host`
**状态:** Stable，自 0.3.1 起。
**设计文档:** [`docs/design/embedded-host-contract.md`](../design/embedded-host-contract.md)
**实施计划（历史档）:** [`docs/implementation/EMBEDDED_HARNESS_CONTRACT_IMPLEMENTATION_PLAN.md`](../implementation/EMBEDDED_HARNESS_CONTRACT_IMPLEMENTATION_PLAN.md)

Harness API 是宿主应用嵌入 Agentao 时面向外部的兼容性边界。运行时内部
类型（`AgentEvent`、`ToolExecutionResult`、`PermissionEngine`）有意不
对外暴露。

> **覆盖范围。** 本包是宿主嵌入 Agentao 的稳定合约边界，包含三个支柱：
> **观测事件**（`ToolLifecycleEvent`、`SubagentLifecycleEvent`、
> `PermissionDecisionEvent`）、**ACP schema 面**（对外的请求/响应/通知
> 模型）、**权限状态**（`ActivePermissions`）。它**不是**完整的聊天
> runtime——驱动一轮执行请用 `Agentao.arun()`，做流式聊天 UI 请用
> `Transport` 或 ACP（assistant 文本、reasoning、原始工具 I/O 有意不在
> 本合约内）。

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
- MVP 仅支持每个 `Agentao` 实例一个公共流消费者。

| 状态 | 语义 |
|---|---|
| 无订阅者 | 公共事件直接丢弃，不阻塞 Agent 循环。 |
| 事件已发出后才出现订阅者 | 不重放，仅接收未来事件。 |
| 订阅队列有容量 | 按发射顺序入队匹配事件。 |
| 订阅队列已满 | 阻塞生产者直到出现容量或流被取消。 |
| 订阅取消 / 迭代器关闭 | 释放队列资源；后续事件遵循 "无订阅者" 行。 |

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
