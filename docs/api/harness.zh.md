# 嵌入式 Harness API

**包名:** `agentao.harness`
**状态:** Stable，自 0.3.1 起。
**设计文档:** [`docs/design/embedded-harness-contract.md`](../design/embedded-harness-contract.md)
**实施计划（历史档）:** [`docs/implementation/EMBEDDED_HARNESS_CONTRACT_IMPLEMENTATION_PLAN.md`](../implementation/EMBEDDED_HARNESS_CONTRACT_IMPLEMENTATION_PLAN.md)

Harness API 是宿主应用嵌入 Agentao 时面向外部的兼容性边界。运行时内部
类型（`AgentEvent`、`ToolExecutionResult`、`PermissionEngine`）有意不
对外暴露。

> **导入纪律。** 所有公共类型只活在 `agentao.harness` 模块下，**没有**
> 从顶层 `agentao` 包再 re-export。请始终
> `from agentao.harness import ...`；不要假定 `agentao.ToolLifecycleEvent`
> 之类的快捷路径存在。

## 公共导出

| 符号 | 用途 |
|---|---|
| `ActivePermissions` | 当前权限策略的只读快照。 |
| `ToolLifecycleEvent` | 一次工具调用生命周期的对外信封。 |
| `SubagentLifecycleEvent` | 子 Agent 任务/会话的血统事件。 |
| `PermissionDecisionEvent` | 每次权限决策的对外投影。 |
| `HarnessEvent` | 上述三种事件的判别联合。 |
| `RFC3339UTCString` | 所有公共事件使用的受限时间戳类型。 |
| `export_harness_event_json_schema()` | 事件 + 权限的规范 JSON schema。 |
| `export_harness_acp_json_schema()` | 对外的 ACP 负载规范 JSON schema。 |

## Schema 快照策略

每个发布版都附带一份签入仓库的 JSON schema 快照：

- `docs/schema/harness.events.v1.json` —— 事件 + 权限
- `docs/schema/harness.acp.v1.json` —— ACP 负载

`tests/test_harness_schema.py` 会从 Pydantic 模型重新生成 schema，
并使用规范化 JSON（`json.dumps(..., sort_keys=True)`）逐字节比对快照。
任何会改变线格式的模型变更，必须在同一个 PR 内同时更新模型与快照。

兼容性规则：

- 增加可选字段：向后兼容。
- 删除字段、重命名字段、变更枚举值或字段语义：必须升 schema 版本并
  写发布说明。
- 公共事件**不得**直接复用内部 `AgentEvent.data` 负载；脱敏/裁剪在
  `agentao/harness/projection.py` 内统一进行。
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

`Agentao.events(session_id: str | None = None)` 返回 `HarnessEvent`
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
