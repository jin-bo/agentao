# 12 Explicit Routing And Push Delegation

Parent doc: [ACP Client And Project-Local Servers](../../ACP_CLIENT_PROJECT_SERVERS.md)

Issue index: [ACP Client Project-Local Servers Issues](README.md)

## Status

- **Part A — Explicit Server Routing**：✅ 已落地。
- **Part B — Experimental Push Delegation**：❌ **已决定不实现**，原设计整体从代码库移除。

Part B 依赖一个私有 `sessionUpdate: "task_complete"` 扩展，**不属于 ACP 标准枚举**（标准集合仅含 `user_message_chunk` / `agent_message_chunk` / `agent_thought_chunk` / `tool_call` / `tool_call_update` / `plan`）。为了不让 Agentao 作为 ACP client 的行为偏离标准，并避免对 server 端引入非标扩展义务，决定彻底去掉该能力，而不是保留一个默认关闭但私有的路径。以下关于 Part B 的章节作为历史设计保留，不再代表当前实现。

## Goal

在现有 project-local ACP client 基础上，补两个增强能力：

1. **显式指定目标服务器**
   当用户消息中明确点名某个 ACP server 时，直接把该消息路由给对应 server，而不是继续走主 Agent 的普通 `chat()`。

2. ~~**后台 ACP server 主动 push 通知并触发后续委派**~~ *(已取消，见 Status)*
   当特定 server 开启实验性开关后，如果它通过 `session/update` 主动推送 `task_complete` 类型通知，Agentao 会在安全空闲点把结果注入主对话上下文，并自动触发一轮后续处理。

## Scope

本 issue 只覆盖：

- CLI 用户输入的显式目标解析
- 复用现有 `/acp send` 路径执行显式路由
- ACP notification 到主对话上下文的实验性桥接
- 配置模型扩展
- 去重、限流、线程边界和安全空闲点约束

本 issue 不覆盖：

- 自然语言隐式猜测式 server 选择
- 多 server 自动编排
- 通用 notification → agent 对话注入
- 直接在 reader 线程里触发 `agent.chat()`

## Part A: Explicit Server Routing

### User Stories

- `@code-reviewer 帮我审查最近一次 git diff`
- `security-auditor: 检查当前项目的依赖漏洞`
- `让 security-auditor 检查当前项目的依赖漏洞`

### Trigger Rules

v1 建议只做确定性匹配，不做模糊猜测：

1. `@server-name <task>`
2. `server-name: <task>`

可选扩展规则（v1.1 或 v2）：

3. `让 server-name ...`
4. `请 server-name ...`
5. `server-name 帮我 ...`

### Behavior

命中显式路由时：

1. 不进入主 Agent 的普通 `agent.chat(user_input)` 路径
2. 直接复用 `/acp send <server> <task>` 的执行逻辑
3. CLI 显示明确的路由提示，如：
   - `ACP Delegation → code-reviewer`
4. 任务文本中应移除路由前缀，仅保留真正的任务内容

### Failure Handling

- 未命中已配置 server 名称：继续按普通用户输入处理
- 同时命中多个 server：拒绝自动路由，提示用户使用 `@server-name`
- 路由后任务内容为空：提示用户补充任务内容

### Proposed Data Model

新增一个轻量解析结果类型：

```python
@dataclass(frozen=True)
class AcpExplicitRoute:
    server: str
    task: str
    syntax: str   # at_mention | colon | zh_explicit
    raw_prefix: str
```

### Proposed Files

- `agentao/acp_client/router.py`
  - `detect_explicit_route(text: str, server_names: list[str]) -> Optional[AcpExplicitRoute]`

- `agentao/cli/app.py`
  - 在主输入分发、`self.agent.chat(user_input)` 之前插入预路由逻辑

- `agentao/cli/commands_ext.py`
  - 抽取 `/acp send` 主体为可复用 helper

### Proposed Flow

```text
user input
  ↓
detect_explicit_route()
  ├─ no match → normal agent.chat(user_input)
  └─ match    → run_acp_prompt_inline(cli, route.server, route.task)
```

### Why This Shape

- 用户意图已显式指定，不需要 LLM 再二次判断
- 可完全复用现有 ACP prompt runner
- 风险低，不会引入新的多 agent 自动编排语义

## Part B: Experimental Push Delegation (Dropped)

> ⚠️ 本节仅作为历史设计保留，**不在代码库中实现**。原因见顶部 Status：`task_complete` 不属于 ACP 标准 `sessionUpdate` 枚举，继续保留会迫使 server 侧引入私有扩展。

### Goal

允许特定 ACP server 在后台主动推送“任务完成”结果，并把结果交给主 Agent 做后续决策。

### Config Shape

在 `.agentao/acp.json` 的具体 server 下增加实验性开关：

```json
{
  "servers": {
    "security-auditor": {
      "command": "python",
      "args": ["-m", "security_server"],
      "env": {},
      "cwd": ".",
      "experimental": {
        "pushTaskCompleteToAgent": true
      }
    }
  }
}
```

### Why Under `experimental`

- 不污染主配置面
- 明确该能力不是默认稳定语义
- 为后续实验功能保留统一扩展位

### Wire Contract

复用已有 `session/update`，约定私有 `sessionUpdate` 类型：

```json
{
  "method": "session/update",
  "params": {
    "sessionId": "sess_xxx",
    "update": {
      "sessionUpdate": "task_complete",
      "taskId": "audit-20250412-001",
      "title": "Dependency audit finished",
      "content": {
        "type": "text",
        "text": "Found 3 vulnerable packages..."
      },
      "metadata": {
        "severity": "high"
      }
    }
  }
}
```

### Detection Rules

仅当同时满足以下条件时，才触发 push delegation：

1. `method == "session/update"`
2. `params.update.sessionUpdate == "task_complete"`
3. 对应 server 开启 `experimental.pushTaskCompleteToAgent`

否则：

- 继续按普通 inbox 消息处理
- 不注入主对话上下文

### Threading Rule

**禁止**在 ACP reader 线程或 notification callback 线程内直接调用 `agent.chat()`。

正确做法：

1. notification callback 识别符合条件的 `task_complete`
2. 放入一个单独的 delegate queue
3. 仅在 CLI 主线程的 safe idle point 消费该 queue
4. 由主线程执行 synthetic message 注入和后续 `agent.chat()`

### Proposed Queue Model

```python
@dataclass(frozen=True)
class ACPDelegateEvent:
    server: str
    session_id: str
    task_id: str
    title: str
    text: str
    metadata: dict[str, Any]
    timestamp: float
```

新增：

- `ACPDelegateQueue`
- `ACPManager.delegate_queue`
- `ACPManager.drain_delegate_events()`

### Proposed Main-Thread Injection

在 safe idle point 把事件转成 synthetic user message：

```text
<system-reminder>
ACP background task complete

server: security-auditor
task_id: audit-20250412-001
title: Dependency audit finished

result:
Found 3 vulnerable packages...
</system-reminder>

An ACP background server finished a task. Incorporate this update and decide whether any follow-up action is needed.
```

然后由主线程触发一轮：

```python
self.agent.chat(synthetic_message)
```

### Why Use Synthetic User Message

- 与现有 `agent.chat(user_message)` 主路径兼容
- 能自然进入记忆、上下文压缩、日志与展示链路
- 不需要为 ACP 单独造一套“后台结果上下文注入”机制

### Safety Constraints

#### 1. Dedup

`task_complete` 应强制要求 `taskId`。

去重键：

- `(server, task_id)`

若缺失 `taskId`：

- v1 直接降级为普通 inbox 消息
- 不触发自动注入

#### 2. Safe Idle Only

只允许在这些空闲点消费 delegate queue：

- 输入提示前
- slash command 执行后
- agent 响应打印后

不得在：

- ACP reader 线程
- `/acp send` 的中间等待线程
- 任意嵌套工具回调线程

#### 3. Batch Merge

同一空闲点如有多个 `task_complete` 事件：

- 建议合并成一个 synthetic message
- 只触发一轮 `agent.chat()`

#### 4. No Recursive ACP Routing

synthetic message 不参与 explicit route 检测。

即：

- 显式 ACP 路由只对真实用户输入生效
- 后台 injected message 只进入主 Agent

#### 5. Rate Limit

建议增加简单限流：

- 每个 server 在单位时间内最多自动触发 N 次
- 超出后只进 inbox，不再自动注入

v1 可先不实现复杂配额，只保留 hook 点。

## Proposed Code Changes

### Config / Models

- `agentao/acp_client/models.py`
  - 为 `AcpServerConfig` 增加实验开关解析

建议新增字段：

```python
experimental_push_task_complete_to_agent: bool = False
```

或：

```python
experimental: dict[str, Any] = field(default_factory=dict)
```

更推荐前者在内部落成稳定布尔字段，避免下游处处读裸 dict。

### Routing

- `agentao/acp_client/router.py`
  - 显式目标 server 检测

- `agentao/cli/app.py`
  - 在普通用户输入分发处插入 route detect

- `agentao/cli/commands_ext.py`
  - 提取共享 runner：
    - `run_acp_prompt_inline(cli, name, message)`

### Push Delegation

- `agentao/acp_client/delegate.py`
  - queue + event dataclass

- `agentao/acp_client/manager.py`
  - 识别 `task_complete`
  - 仍推 inbox
  - 额外推 delegate queue

- `agentao/cli/app.py`
  - safe idle 点调用 `drain_delegate_events()`
  - 合并事件
  - 注入 synthetic message
  - 触发 `self.agent.chat(...)`

## Testing Plan

### Explicit Routing

1. `@server task` 命中并走 ACP runner
2. `server: task` 命中并走 ACP runner
3. 未命中 server 时回退普通 chat
4. 空任务内容时报错
5. 多 server 冲突时报错

### Push Delegation

1. 未开 experimental 开关时，`task_complete` 只进 inbox
2. 开启开关时，`task_complete` 进入 delegate queue
3. safe idle 点消费后，触发一轮主 Agent follow-up
4. 相同 `(server, task_id)` 不重复注入
5. 缺失 `taskId` 时只渲染 inbox，不自动注入
6. 多个事件同轮合并为一次 follow-up

## Rollout Plan

### Phase 1 (shipped)

- 显式 server 路由
- 支持 `@server` / `server:` / `让 server` / `请 server`
- 共享 `/acp send` runner

### Phase 2 — dropped

- ~~实验性 `task_complete` push delegation~~ — 见顶部 Status。

## Documentation Impact

需要同步更新：

- `docs/features/acp-client.md`
- 根 README 中 ACP client 的摘要描述（如行为变化对用户可见）

其中用户文档应明确：

- 显式 server 路由是“用户主动点名 server”的能力
- push delegation 是实验性功能，需 per-server 开关启用
