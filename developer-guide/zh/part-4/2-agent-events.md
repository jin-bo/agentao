# 4.2 AgentEvent 事件清单

> **本节你会学到**
> - 什么时候用 `AgentEvent`（UI / 调试 / 回放）vs `HostEvent`（稳定的宿主合约）
> - 全部事件类型的清单：触发条件、`data` 负载、典型用途
> - 怎样把事件安全地序列化到 SSE / WebSocket

Agent 在运行过程中通过 `transport.emit(event)` 推送结构化事件。本节是**全量事件参考**——每个事件的触发时机、`data` 负载、典型用法。

::: warning 在做生产审计流水线？请用 `HostEvent`，不是本页的 `AgentEvent`
**本页**的事件是**内部 transport 事件** —— 驱动 CLI、replay、调试工具，字段和枚举值会随版本演进。它们适合做**流式 UI**（LLM_TEXT 文本块、THINKING 气泡、in-flight 工具视图）。

如果你在做**生产审计 / 可观测 / SIEM 流水线**，请用 **[4.7 嵌入式 Harness 合约](./7-host-contract)** 的稳定宿主表面。快速对照：

| 面 | 在哪里 | 稳定性 | 何时使用 |
|---|---|---|---|
| `agentao.transport.AgentEvent`（本页） | `Transport.emit()` 推送回调 | 内部 —— 随版本可能变 | CLI / 流式 UI 需要细节 |
| `agentao.host.HostEvent`（[4.7](./7-host-contract)） | `agent.events()` 异步 pull 迭代器 | **稳定**，schema 快照、CI 强制 | 生产审计、计费、多租户合规 |

两个面**互补，不是二选一** —— 大多数生产部署**两者都用**：Transport 给 UI，`events()` 给审计。它们零代码路径共享。
:::

## `AgentEvent` 数据结构

```python
@dataclass
class AgentEvent:
    type: EventType              # 枚举值
    data: Dict[str, Any] = ...   # 必须是 JSON 可序列化的
```

**JSON 可序列化** 约束意味着所有 `data` 字段都能直接转发到 SSE / WebSocket / JSON-RPC，无需额外处理。

## 事件分组

```
TURN_BEGIN -> (用户消息到达——turn 开始；携带 user 文本)
└── TURN_START -> (LLM 调用开始；重置流式 UI)
    ├── LLM_CALL_STARTED        (调用 provider 前的元数据)
    ├── THINKING *              (可选，0 或多次)
    ├── LLM_TEXT *              (用户可见的流式 chunk)
    ├── LLM_CALL_DELTA          (本次调用新增的 messages)
    ├── LLM_CALL_COMPLETED      (usage + finish reason)
    ├── TOOL_START              (工具开始)
    │   ├── TOOL_CONFIRMATION   (可选，确认弹窗镜像事件)
    │   ├── TOOL_OUTPUT *       (流式 chunk)
    │   ├── TOOL_COMPLETE       (状态 + 耗时)
    │   └── TOOL_RESULT         (最终内容 / hash / 落盘元数据)
    ├── AGENT_START / AGENT_END (sub-agent 生命周期)
    ├── ERROR                   (可选，出错时)
    └── replay-only observability events
TURN_END   -> (turn 结束；携带最终 assistant 文本 + status/error)
```

`TURN_BEGIN` / `TURN_END` **每个用户驱动的 turn 各发一次**；`TURN_START` 是 turn 内**每次 LLM 迭代**都发（一个 turn 内可能很多次）。Replay 录制器通过 `Transport.subscribe()`（见 [4.1](./1-transport-protocol)）订阅外层这一对，替代了过去从 agent 内部状态直接调用 replay adapter 的路径。

大多数 UI 只需要处理 `LLM_TEXT`、`THINKING`、`TOOL_START`、`TOOL_OUTPUT`、`TOOL_COMPLETE`、`TOOL_CONFIRMATION`、`AGENT_START`、`AGENT_END` 和 `ERROR`。
其他事件主要服务于 session replay、审计、指标和调试。

## 单事件详解

### `TURN_BEGIN`

| 字段 | 说明 |
|------|------|
| 触发时机 | 每个用户驱动 turn 开始时**一次**，在任何 LLM 迭代之前 |
| `data` | `{"user_message": "..."}` |
| 典型用法 | 在 replay 日志 / 审计流中开一个新的 turn 帧；通过 `Transport.subscribe()` 订阅 |

与 `TURN_START`（每个 LLM 迭代各发一次）语义不同。`TURN_BEGIN` 携带用户输入，并与 `TURN_END` 1:1 配对。

### `TURN_END`

| 字段 | 说明 |
|------|------|
| 触发时机 | 每个用户驱动 turn 结束时**一次**，在最终 assistant 回复之后（或出错 / 被取消时） |
| `data` | `{"final_text": "...", "status": "ok"\|"error"\|"cancelled", "error": None, "tool_count": 3}` |
| 典型用法 | 关闭 turn 帧；刷出 per-turn 指标 —— `tool_count` 是这个 turn 内所有迭代里 LLM 发起的工具调用总数，宿主无需重放每个 `TOOL_START` 就能掂量这一 turn 的规模 |

Replay 录制器靠它和 `TURN_BEGIN` 配对来界定一个 turn。它替代了运行时直接调用 replay adapter 的旧路径。

### `TURN_START`

| 字段 | 说明 |
|------|------|
| 触发时机 | turn 内**每次 LLM 迭代**前（同一个 turn 可能发很多次） |
| `data` | `{}` 空对象 |
| 典型用法 | 重置 UI 显示、切换 spinner 到 "Thinking…" |

```python
if event.type == EventType.TURN_START:
    ui.spinner.text = "Thinking..."
    ui.reset_streaming_buffer()
```

### `THINKING`

| 字段 | 说明 |
|------|------|
| 触发时机 | LLM 返回 reasoning/thought 内容时（如 o1、Claude thinking） |
| `data` | `{"text": "Let me think..."}` |
| 典型用法 | 渲染到折叠的 "思考过程" 面板 |

```python
if event.type == EventType.THINKING:
    ui.thinking_panel.append(event.data["text"])
```

### `LLM_TEXT`

| 字段 | 说明 |
|------|------|
| 触发时机 | LLM 流式返回正文 chunk |
| `data` | `{"chunk": "Sure, I can help"}` |
| 典型用法 | 逐 chunk 追加到用户可见的回复区域 |

```python
if event.type == EventType.LLM_TEXT:
    ui.response_area.append(event.data["chunk"])
```

⚠️ `chunk` 可能是几个字母、半个词，也可能是一整段——只保证顺序，不保证切分粒度。

### `TOOL_START`

| 字段 | 说明 |
|------|------|
| 触发时机 | 准备执行一个工具 |
| `data` | `{"tool": "run_shell_command", "args": {...}, "call_id": "uuid"}` |
| 典型用法 | 在 UI 插入 "Running tool X..." 卡片；记录 `call_id` 做关联 |

`call_id` 是这次调用的唯一键，后续 `TOOL_OUTPUT`、`TOOL_COMPLETE` 和 `TOOL_RESULT` 会带同一个 id，方便在 UI 里把流式输出挂到正确的卡片上。

### `TOOL_CONFIRMATION`

| 字段 | 说明 |
|------|------|
| 触发时机 | 即将调用 `confirm_tool()` 问用户前 |
| `data` | `{"tool": "run_shell_command", "args": {...}}` |
| 典型用法 | **可选**的 "镜像" 事件——给纯只读的观察者一次看到"要弹确认框了"的机会 |

通常你不需要处理这个事件——真正的确认逻辑走 `confirm_tool()` 方法调用。`TOOL_CONFIRMATION` 更多用于审计/日志"事件流完整性"。

### `TOOL_OUTPUT`

| 字段 | 说明 |
|------|------|
| 触发时机 | 工具执行过程中产生流式输出 |
| `data` | `{"tool": "...", "chunk": "...", "call_id": "uuid"}` |
| 典型用法 | 把 chunk 追加到对应工具卡片 |

典型流式工具：`run_shell_command`（命令 stdout/stderr 边产生边 emit）、长耗时 `web_fetch`、自定义的"分页抓取"工具。

### `TOOL_COMPLETE`

| 字段 | 说明 |
|------|------|
| 触发时机 | 工具执行结束（成功/失败/取消） |
| `data` | `{"tool": "...", "call_id": "uuid", "status": "ok"\|"error"\|"cancelled", "duration_ms": 123, "error": None}` |
| 典型用法 | 关闭工具卡片 spinner，根据 `status` 变色；记录执行时长 |

```python
if event.type == EventType.TOOL_COMPLETE:
    d = event.data
    ui.close_tool_card(d["call_id"],
                       status=d["status"],
                       duration=d["duration_ms"])
```

### `TOOL_RESULT`

| 字段 | 说明 |
|------|------|
| 触发时机 | 工具最终结果可用后 |
| `data` | `{"tool": "...", "call_id": "uuid", "content": "...", "content_hash": "sha256:...", "original_chars": 123, "saved_to_disk": false, "disk_path": null, "status": "ok"\|"error"\|"cancelled", "duration_ms": 123, "error": None}` |
| 典型用法 | 不依赖流式 chunk，持久化或检查工具最终输出 |

普通 UI spinner 优先看 `TOOL_COMPLETE`。`TOOL_RESULT` 更适合 replay、审计、结果 hash 和大输出场景。

### `LLM_CALL_STARTED` / `LLM_CALL_COMPLETED`

| 字段 | 说明 |
|------|------|
| 触发时机 | 每次 provider 调用前后 |
| `data` | 调用前元数据；调用后的 usage / finish 元数据。`LLM_CALL_COMPLETED` 携带 `duration_ms`、`model_latency_ms`（`duration_ms` 的稳定别名，命名更贴合意图）、`first_token_ms`（首 token 时延，毫秒；当本次调用没有流式文本——例如纯工具调用响应、或首个 delta 之前就失败——为 `null`）、`prompt_tokens`、`completion_tokens`、`finish_reason`，错误路径上还有 `status` / `error_class` / `error_message` / `streamed` |
| 典型用法 | 指标、成本统计、调试模型行为 —— `first_token_ms` 与 `model_latency_ms` 把排队/TTFT 与总生成时间区分开 |

### `LLM_CALL_DELTA`

| 字段 | 说明 |
|------|------|
| 触发时机 | 一次 LLM 调用向历史新增 messages 后 |
| `data` | 相比上次调用新增的 messages |
| 典型用法 | 用较紧凑的方式做 session replay |

### `LLM_CALL_IO`

| 字段 | 说明 |
|------|------|
| 触发时机 | 仅 deep capture 开启时 |
| `data` | 该次 LLM 调用的完整 prompt / tool payload |
| 典型用法 | 离线调试；按敏感内容处理 |

### `ERROR`

| 字段 | 说明 |
|------|------|
| 触发时机 | 运行时捕获到异常（LLM 报错、网络错误、MCP 断开…） |
| `data` | `{"message": "...", "detail": "..."}` |
| 典型用法 | 弹 toast、写日志；**不必停止会话**——Agent 会自行决定是否继续 |

```python
if event.type == EventType.ERROR:
    logger.error(event.data["message"], extra=event.data)
    ui.toast(event.data["message"])
```

### `AGENT_START`

| 字段 | 说明 |
|------|------|
| 触发时机 | Agent 启动一个 sub-agent（如 `codebase-investigator`、`Explore` 等） |
| `data` | `{"agent": "codebase-investigator", "task": "...", "max_turns": 15}` |
| 典型用法 | 在 UI 里开一个"子任务"折叠面板 |

### `AGENT_END`

| 字段 | 说明 |
|------|------|
| 触发时机 | sub-agent 完成 |
| `data` | `{"agent": "...", "state": "completed"\|"...", "turns": 3, "tool_calls": 5, "tokens": 1200, "duration_ms": 8000, "error": None}` |
| 典型用法 | 折叠子任务面板、显示汇总 (3 轮/5 次工具/8s) |

### Replay 可观测性事件

这些事件主要用于 session replay 和运行审计。大多数交互式 UI 可以忽略。

| 事件 | 典型 payload / 用途 |
|------|---------------------|
| `ASK_USER_REQUESTED` / `ASK_USER_ANSWERED` | 记录 `ask_user()` 的问题和回答 |
| `BACKGROUND_NOTIFICATION_INJECTED` | 后台通知被注入当前 turn |
| `CONTEXT_COMPRESSED` | 发生上下文压缩 |
| `SESSION_SUMMARY_WRITTEN` | 会话摘要已写入 |
| `SKILL_ACTIVATED` / `SKILL_DEACTIVATED` | Skill 生命周期 |
| `MEMORY_WRITE` / `MEMORY_DELETE` / `MEMORY_CLEARED` | Memory 变更 |
| `MODEL_CHANGED` | 运行时模型切换 |
| `PERMISSION_MODE_CHANGED` / `READONLY_MODE_CHANGED` | 运行时安全模式变化 |
| `PLUGIN_HOOK_FIRED` | 插件 hook 已运行。`data["hook_name"]` 取值为 `UserPromptSubmit` / `SessionStart` / `SessionEnd` / `PreToolUse` / `PostToolUse` / `PostToolUseFailure` / `Stop` / `PreCompact` 之一。各 hook_name 携带不同的字段——例如 `Stop` 带 `turn_end_reason ∈ {"final_response", "max_iterations", "doom_loop"}`、`at_max_iter`、`added_context_count` 与 `suppress_output`；`PreCompact` 带 `compaction_type ∈ {"microcompact", "full", "minimal_history"}` 与 `trigger="auto"`。所有 emit 都带 `outcome` 与 `matched_rule_count`（被选派的规则数；为 0 时**完全不发出事件**）。`Stop` 的 `outcome ∈ {"allow", "block", "continue", "continue_at_max_iter", "reentry_capped"}`，反映该退出点的 chat-loop 判决（`continue` 与 `continue_at_max_iter` 用来区分是哪一个退出点接受了 `force_continue`；`reentry_capped` 表示循环拒绝再次重入）。`PreCompact` 的 `outcome` 仍恒为 `"allow"`（仅观察）。规则作者视角的完整指南见 [§5.7 插件 Hooks](/zh/part-5/7-plugin-hooks)。 |

## 枚举值的字符串形态

`EventType` 是 `str` 的子类，所以你可以：

```python
>>> EventType.LLM_TEXT
<EventType.LLM_TEXT: 'llm_text'>
>>> str(EventType.LLM_TEXT)
'llm_text'
>>> EventType.LLM_TEXT == "llm_text"
True
```

方便直接把 `event.type` 序列化到 SSE/WebSocket 字段：

```python
json.dumps({"type": event.type, "data": event.data})
# → {"type": "llm_text", "data": {"chunk": "..."}}
```

## 事件过滤器样板

嵌入场景里你往往只关心**几个关键事件**：

```python
TEXT_EVENTS = {EventType.LLM_TEXT, EventType.TOOL_OUTPUT}
CONTROL_EVENTS = {EventType.TOOL_START, EventType.TOOL_COMPLETE,
                  EventType.AGENT_START, EventType.AGENT_END}

def on_event(event):
    if event.type in TEXT_EVENTS:
        stream_to_ui(event.data.get("chunk", ""))
    elif event.type in CONTROL_EVENTS:
        update_structural_ui(event)
    elif event.type == EventType.ERROR:
        log_and_toast(event)
    # 其他事件忽略
```

## 事件到 JSON 的完整转换

用于 SSE / WebSocket / 消息队列：

```python
def event_to_json(event: AgentEvent) -> str:
    return json.dumps({
        "type": event.type.value,   # "llm_text" / "tool_start" / ...
        "data": event.data,
        "ts": time.time(),
    })
```

反向（从 JSON 重建 AgentEvent，多用于测试 replay）：

```python
from agentao.transport import AgentEvent, EventType

def event_from_json(j: str) -> AgentEvent:
    obj = json.loads(j)
    return AgentEvent(type=EventType(obj["type"]), data=obj["data"])
```

## TL;DR

- `AgentEvent` 是**内部接口**——字段和 `EventType` 取值在版本间可能变。需要稳定宿主面（审计 / 可观测）的请用 `HostEvent`，见 **[4.7 嵌入式 Harness 合约](./7-host-contract)**。
- 最常处理的几种类型：`LLM_TEXT`（流式文本）、`TOOL_START` / `TOOL_COMPLETE`、`THINKING`、`ERROR`。
- 对未知类型保持防御——版本演进会新增。永远要有 default 分支。
- 序列化用 `event.type.value` + `event.data`（已经 JSON 安全）——不要 pickle。

→ 下一节：[4.3 SdkTransport 快速桥接](./3-sdk-transport)
