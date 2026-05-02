# 4.7 嵌入式 Harness 合约 —— 你的稳定宿主 API

> **本节你会学到**
> - **为什么** `agentao.host` 要和 Transport / AgentEvent 并存
> - 它暴露的**三个表面**（事件、策略快照、能力协议）以及各自解决的问题
> - **`agent.events()` vs `Transport(on_event=…)`** —— 何时用哪个（或都用）
> - **端到端**：~30 行写出一条租户审计流水线，跨 Agentao 版本升级也不会断

如果你看完 [4.2 AgentEvent](./2-agent-events) 顶部的 `:::warning` 知道"生产环境用 `HostEvent`"，本章就是这条建议背后的**怎么做**。

## 4.7.1 它解决什么问题

你在做多租户 SaaS。Agent 的每个动作都要落一行审计：租户、用户、工具名、参数、是否批准、是否成功。

你基于 `AgentEvent`（4.2 节）写好了，跑得很顺，上线。三个月后 Agentao 0.5.0 发布，`EventType.MEMORY_WRITE` 改名了、两个 `data` 字段被挪了位置——你的审计流水线**静默地**漏行漏了一周，直到 ETL 计数对不上才被人发现。

这就是**字段漂移问题**。`AgentEvent` 是运行时的内部事件总线——驱动 CLI、调试 UI、replay 机制——这些消费方需要丰富细节，且能在每次发布时承受变更。**生产宿主承受不起这种代价**。

**嵌入式 Harness 合约**就是答案：`agentao.host` 下一组刻意的小表面，它的特征是：

- **以 Pydantic 模型冻结** —— 字段和类型是公开合约的一部分
- **schema 已快照**到 `docs/schema/host.events.v1.json` —— CI 强制字节级一致
- **是内部事件的红线投影** —— 比如用户 prompt 文本不会出现在审计 body 里
- **有版本号** —— 加可选字段向后兼容；删字段或改名要 schema 版本号升级

只要你的代码只触到 `agentao.host`（加上有文档保证的 `Agentao(...)` 构造器和 `chat()` / `events()` / `active_permissions()` 方法），你就能保持向前兼容。

::: tip Harness 是运行时的*边界*，不是运行时本身。
合约把 **观测**、**策略**、**wire schema** 三个表面，*围绕*一次 Agentao session 类型化。它**不是**一个 turnkey 聊天 runtime：驱动一轮还是用 `agent.arun()`（或 `agent.chat()`），流式 assistant 文本 / reasoning / 原始工具 I/O 还是要从 `Transport` 或 ACP 选一个面。把 harness 想成发动机外面的"整车厂连接器"，不是发动机本身。
:::

## 4.7.2 三个表面

`agentao.host` 暴露三个不同的表面。它们住在同一个 package 是因为共享"稳定宿主合约"的承诺，但解决的问题不一样：

| 表面 | 你怎么用 | 拿到什么 | 出处 |
|------|---------|---------|------|
| **事件** | `agent.events()` 异步迭代器 | 一串 `HostEvent`（工具 / 子 agent / 权限三种生命周期） | 审计、可观测、实时 UI |
| **策略快照** | `agent.active_permissions()` | JSON 安全的 `ActivePermissions`（mode + rules + sources） | 设置 UI、审计富化、合规报告 |
| **能力协议** | `from agentao.host.protocols import FileSystem, ShellExecutor, MCPRegistry, MemoryStore` | 可注入 Docker / 虚拟 FS / 审计代理 / 程序化 MCP / 远程记忆后端的运行时 Protocol | 见 [2.2 第 3 档 · 能力协议](/zh/part-2/2-constructor-reference#第-3-档-高级注入) 与 [6.4](/zh/part-6/4-multi-tenant-fs)；端到端示例 [`examples/protocol-injection/`](https://github.com/jin-bo/agentao/tree/main/examples/protocol-injection) |

本章聚焦**事件**和**策略快照**——大多数读者最先用到的两块。能力协议在它们的构造时上下文里已经讲过；想看一次性替换全部四个槽位的可运行端到端形态，见 [`examples/protocol-injection/`](https://github.com/jin-bo/agentao/tree/main/examples/protocol-injection)。

::: info package 里还有第四件东西：ACP schema 面。
`export_host_acp_json_schema()` 暴露的是 Pydantic 化的 wire schema，给那些通过 [ACP stdio 协议](/zh/part-3/1-acp-tour) **进程外**驱动 Agentao 的宿主用（IDE 插件、Node/Go/Rust 前端、微服务）。它不像上面三个那样是"消费 API"——而是给协议实现方的合约产物。**进程内嵌入可以忽略；进程外嵌入应该参考这份 snapshot，而不是从运行时 trace 里反推 payload 形状。**
:::

## 4.7.3 三种事件类型

三个**正交**的生命周期事实。每个都是一个 Pydantic 模型，承载刚好够写一行审计的上下文，并通过 `event_type` 字段（discriminator）让你 `isinstance` 分派。

| 事件 | 阶段 | 何时触发 |
|------|------|---------|
| `ToolLifecycleEvent` | `started` · `completed` · `failed` | 任何工具调用（内置或自定义）。取消会以 `phase="failed", outcome="cancelled"` 形式出现。 |
| `PermissionDecisionEvent` | （单次决策，无阶段） | 每次权限决策：`allow` / `deny` / `prompt`。**消费方必须把 allow 也消化掉**——审计行需要它。 |
| `SubagentLifecycleEvent` | `spawned` · `completed` · `failed` · `cancelled` | 子 agent 任务的生命周期。注意：这里 `cancelled` 是**独立阶段**（与工具事件不同）。 |

`HostEvent` 是这三者的 discriminated union。用 `isinstance` 分支：

```python
from agentao.host import (
    HostEvent,
    ToolLifecycleEvent,
    SubagentLifecycleEvent,
    PermissionDecisionEvent,
)

async for ev in agent.events():
    if isinstance(ev, ToolLifecycleEvent):
        ...
    elif isinstance(ev, PermissionDecisionEvent):
        ...
    elif isinstance(ev, SubagentLifecycleEvent):
        ...
```

完整字段表：[附录 A.10](/zh/appendix/a-api-reference#a-10-嵌入-harness-合约)。schema 文件：[`docs/schema/host.events.v1.json`](https://github.com/jin-bo/agentao/blob/main/docs/schema/host.events.v1.json)。

## 4.7.4 `agent.events()` vs `Transport(on_event=…)` —— 怎么选

两者都送事件，但服务的目的不一样。**别把它们当对立选项**——给每个消费者挑对的那个：

| 问题 | 用 `agent.events()`（harness） | 用 `Transport(on_event=…)` |
|------|-------------------------------|-----------------------------|
| 是给**生产宿主**用的，要前向兼容？ | ✅ | ❌ —— 字段会漂移 |
| 需要**流式文本块**给 UI（`LLM_TEXT` / `THINKING`）？ | ❌ —— 投影时被剔掉了 | ✅ —— 它就是为这个设计的 |
| 在做**审计流水线** / SIEM 摄入 / 计费打表？ | ✅ | ❌ |
| 在做需要内部细节的 **CLI / 调试工具**？ | ❌ —— 投影太瘦 | ✅ |
| 需要**异步 pull** + 背压语义？ | ✅ —— `async for` + 有界队列 | ❌ —— 推送回调 |
| 需要**多个并发消费者**？ | ⚠️ MVP 一个 `Agentao` 一个流 | ✅ —— 自己加分发器扇出 |

大多数生产部署**两者都用**：Transport 驱动流式 UI；`events()` 驱动审计 / 可观测流水线。它们零代码路径共享，互不干扰。

## 4.7.5 端到端：租户级审计流水线

::: tip 两个可直接跑的入口
- **入门** —— [`examples/host_events.py`](https://github.com/jin-bo/agentao/blob/main/examples/host_events.py)：最小版，每条 `HostEvent` 打到 stdout。~50 行，`OPENAI_API_KEY=sk-... uv run python examples/host_events.py` 即跑。
- **生产模式** —— [`examples/host_audit_pipeline.py`](https://github.com/jin-bo/agentao/blob/main/examples/host_audit_pipeline.py)：下面这套审计循环的完整版，带 SQLite 持久化 + 跑完后 dump 审计表。

下面这套是 schema 稳定的代码骨架；任挑一个 example clone 下来 60 秒内就能看到真实输出。
:::

下面是完整模式。每个工具调用、权限决策、子 agent 动作都对应一行审计——schema 跨 Agentao 版本稳定。下面字段名都是 [`agentao/host/models.py`](https://github.com/jin-bo/agentao/blob/main/agentao/host/models.py) 真实定义的；完整类型签名见 [附录 A.10](/zh/appendix/a-api-reference#a-10-嵌入-harness-合约)。

```python
"""租户审计流水线。和 agent.arun() 并行运行。"""
import asyncio
import json
from agentao import Agentao
from agentao.host import (
    ToolLifecycleEvent,
    PermissionDecisionEvent,
    SubagentLifecycleEvent,
)

async def audit_loop(agent: Agentao, tenant_id: str, db):
    """消费 harness 事件，每条事实写一行审计。"""
    async for ev in agent.events():
        row = {
            "tenant_id":  tenant_id,
            "session_id": ev.session_id,
            "event_type": ev.event_type,  # discriminator
        }

        if isinstance(ev, ToolLifecycleEvent):
            # started_at 一定有；completed_at 在结束/失败时被填。
            ts = ev.completed_at or ev.started_at
            row.update({
                "ts":           ts,
                "tool_call_id": ev.tool_call_id,
                "tool_name":    ev.tool_name,
                "phase":        ev.phase,        # started | completed | failed
                "outcome":      ev.outcome,      # ok | error | cancelled
                "summary":      ev.summary,      # 已脱敏的宿主字符串
                "error_type":   ev.error_type,
            })
        elif isinstance(ev, PermissionDecisionEvent):
            row.update({
                "ts":             ev.decided_at,
                "tool_call_id":   ev.tool_call_id,
                "tool_name":      ev.tool_name,
                "decision_id":    ev.decision_id,
                "outcome":        ev.outcome,     # allow | deny | prompt
                "mode":           ev.mode,
                "matched_rule":   ev.matched_rule,    # dict 或 None
                "loaded_sources": ev.loaded_sources,  # list[str]
                "reason":         ev.reason,
            })
        elif isinstance(ev, SubagentLifecycleEvent):
            row.update({
                "ts":                ev.completed_at or ev.started_at,
                "child_session_id":  ev.child_session_id,
                "child_task_id":     ev.child_task_id,
                "phase":             ev.phase,    # spawned|completed|failed|cancelled
                "task_summary":      ev.task_summary,
            })

        await db.execute(
            "INSERT INTO agent_audit (tenant_id, session_id, ts, event_type, payload) "
            "VALUES ($1, $2, $3, $4, $5)",
            row["tenant_id"], row["session_id"], row["ts"],
            ev.event_type, json.dumps(row),
        )

# 与 arun() 一起接线
async def handle_request(tenant_id: str, message: str, db):
    agent = make_agent_for_session(tenant_id, ...)  # 你自己的工厂
    audit = asyncio.create_task(audit_loop(agent, tenant_id, db))
    try:
        reply = await agent.arun(message)
        return reply
    finally:
        audit.cancel()                # cancel 会释放队列和订阅
        agent.close()
```

**这套模式为什么稳**：

- 同会话顺序由合约保证——你的审计行顺序就是事件顺序。
- 同一个 `tool_call_id` 的 `PermissionDecisionEvent` 永远在 `ToolLifecycleEvent(phase="started")` 之前——下游可以拼起来。
- 慢消费者不会丢事件：背压走有界队列，**生产者会被阻塞**而不是默默丢事件。
- Agentao 0.5 发布并新增了内部事件变体时，你的审计流水线根本不会注意到——那种事件不会被投影到 harness；harness 自己 *如果* 增加新变体，也只会加可选字段。

## 4.7.6 `agent.active_permissions()` —— 策略快照

设置页要展示"本会话可以：读 / 写 / 访问这些域名"时，你不想偷看内部 `PermissionEngine`。用公开快照：

```python
snap = agent.active_permissions()

snap.mode             # Literal: "read-only" | "workspace-write" | "full-access" | "plan"
snap.rules            # list[dict] —— 解析后的规则
snap.loaded_sources   # list[str] —— 来源标签
```

`loaded_sources` 用稳定字符串标签：

- `preset:<mode>` —— 内置预设（如 `preset:workspace-write`）
- `user:<path>` —— `~/.agentao/permissions.json` 的用户级
- `injected:<name>` —— 宿主用 `add_loaded_source()` 注入的策略
- `default:no-engine` —— 没配引擎时的兜底

> **没有 `project:<path>` 标签。** 项目级 `<wd>/.agentao/permissions.json` 故意**不**加载 —— 见 [5.4](/zh/part-5/4-permissions)。需要项目感知策略的 host 应通过 `add_loaded_source("injected:<name>")` + 自己的规则层注入。

会话开始时把这个快照钉进审计日志，事后查"那时候到底是什么策略生效"就不用回放整个引擎。

## 4.7.7 前向兼容承诺

`agentao.host` 承诺什么：

- **加字段** = 向后兼容，你的代码继续工作。
- **删字段或改名**要 schema 版本号升级（`host.events.v1.json` → `v2`），并在 changelog 给出明确迁移指南。
- **内部类型**（`agentao.transport.AgentEvent` / `agentao.tools.ToolExecutionResult` / `agentao.permissions.PermissionEngine`）任何版本都可能变。**不要直接 import 进生产代码路径**。
- **schema 快照由 CI 强制**：`tests/test_host_schema.py` 会从 Pydantic 模型重生成 schema，做字节级断言——一个改动同时改了模型和 wire 形状但忘了更新 schema 时，CI 会失败。

运营上这给你什么：**生产环境可以放心 pin `agentao>=0.4.0,<1.0`**，0.9.x 时 harness 合约还是同一份合约。

## 4.7.8 *不在*合约里的东西

Harness 故意不暴露：

- 公开 agent graph / descendants store API
- 宿主侧的 hooks list / disable API
- 宿主侧的 MCP reload / lifecycle 事件
- 本地插件 export/import；远程插件分享
- 外部会话 import
- 生成的客户端 SDK

CLI 可能基于同一套事件构建自己的 UI，但它的 stores 和命令**不会被提升**到 harness API 表面。

如果你发现非伸手到 `agentao.host` 之外才能拿到的东西——**先开 issue**，不要依赖内部类型。

## 4.7.9 决策流速查

```
Q: 我要消费 Agent 事件，用哪个表面？
│
├─ 驱动一轮 / 拿最终回答？
│      → agent.arun() 或 agent.chat()  (Part 2)
│
├─ 流式 UI（文本块、thinking、in-flight 工具视图）？
│      → Transport(on_event=…)         (Part 4.3)
│
├─ 审计 / SIEM / 计费 / 合规？
│      → agent.events()                (本章)
│
├─ 设置 UI 里展示当前策略？
│      → agent.active_permissions()    (§ 4.7.6)
│
├─ 把 IO 路由到 Docker / 虚拟 FS / 审计代理？
│      → from agentao.host.protocols import FileSystem, ShellExecutor
│        (Part 2.2 / Part 6.4)
│
├─ 从非 Python 宿主（IDE、Node、Go、Rust）驱动 Agentao？
│      → ACP stdio 协议                (Part 3.1)
│        （wire 类型用 export_host_acp_json_schema()）
│
└─ 其他？ → 先看附录 A.10，再考虑提 issue。
```

## TL;DR

- **`agentao.host` 是稳定的、schema 快照的、前向兼容的宿主表面。** 生产代码就 pin 这个。
- **三个表面**：`events()` 接事件流、`active_permissions()` 取策略快照、`harness.protocols` 注入能力。
- **`events()` 不是 Transport 的替代** —— 它们互补。UI 流式用 Transport，审计 / 可观测用 `events()`。
- **`isinstance` 分派 `HostEvent`** 把事件路由到对应 handler。三种事件是正交的生命周期事实，不是层级关系。
- **30 行 + 一张数据库表**就能落出能扛住版本升级的租户审计流水线。

→ 参考速查：[附录 A.10 · 嵌入 Harness 合约](/zh/appendix/a-api-reference#a-10-嵌入-harness-合约)
→ Schema：[`docs/schema/host.events.v1.json`](https://github.com/jin-bo/agentao/blob/main/docs/schema/host.events.v1.json)
→ 设计文档：[`docs/design/embedded-host-contract.md`](https://github.com/jin-bo/agentao/blob/main/docs/design/embedded-host-contract.md)

→ 下一节：[第 5 部分 · 扩展点](/zh/part-5/)
