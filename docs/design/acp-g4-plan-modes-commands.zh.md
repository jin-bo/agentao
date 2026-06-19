# ACP G4 —— Plan、Modes、Commands 的 session/update 设计

**状态：** 设计提案。起草于 2026-06-18，是 `acp-server-conformance-review.md` 中 **G4** 的落地
设计——在维护者把目标 client 类别定为 **chat/automation**（故 G1 fs/terminal 为非目标、
G4/G3/G2-diff 为 now-work）之后，G4 是最靠前的 chat 相关 ACP 差距。**尚未批准或实现。**
**读者：** Agentao 维护者；DeepChat/TensorChat 集成负责人。
**对照件：** `acp-g4-plan-modes-commands.md`。
**相关：**
- `acp-server-conformance-review.md` —— 定义了 G4 与本设计所服务的 chat/automation 决定。
- `deepchat-acp-patch-revision.md` —— `session/set_mode` accept-unknown + 自由文本模型；§4.1 的兼容约束源于此。
- `embedding-vs-acp.md` —— ACP 是嵌入式内核之上的前端；映射应留在 ACP 层，而非 runtime。

**方法：** ACP 形状逐字引自官方 v1 schema（`schema/v1/schema.json`，2026-06-18 拉取）。agentao
原语锚定 `main`@`bcdb8e4`。下文每条映射都对照了真实源码——无凭直觉的映射。

---

## TL;DR

G4 把 Agentao 三个内部概念暴露为 chat client 能原生渲染的标准 ACP `session/update` 通知：
**任务清单**（→ `plan`）、**权限模式**（→ `modes` + `current_mode_update`）、**斜杠命令**
（→ `available_commands_update`）。每个都对应一个现有原语：

| ACP 面（schema 实证） | Agentao 原语 | 契合 | 建议 |
|---|---|---|---|
| `plan` 更新 —— `Plan{entries:[PlanEntry{content, priority, status}]}` | `todo_write` 工具（`tools/todo.py`）；`todos:[{content,status}]`，status 枚举完全一致 | 高——仅缺 `priority` | **第二做**——transport 特判，合成 `priority:"medium"` |
| `modes`（session/new）+ `current_mode_update` | `PermissionMode{read-only, workspace-write, full-access, plan}`（`permissions.py:75`）+ `EventType.PERMISSION_MODE_CHANGED` | 高——4 个 preset → availableModes | **第一做**——最小、一致性收益最高 |
| `available_commands_update` —— `[{name, description, input?}]` | 斜杠命令（`cli/help_text.py`）——但**是 host/CLI 控制，无 agent-runtime 语义** | 低 | **缩范围或延后**（对 DeepChat 做需求门控） |

**顺序：Modes → Plan → Commands。** Modes + Plan 是一个紧凑 PR（纯 ACP 层 + schema，无 runtime
改动）。Commands 单独评估。

核实时顺手发现两个一致性细节（回填进 review doc）：**(1)** ACP `ToolKind` 有 **10** 个值
（`read, edit, delete, move, search, execute, think, fetch, switch_mode, other`）——review 写的是
9。**(2)** ACP 标准 `SetSessionModeResponse` 是**空对象**，模式变更经 `current_mode_update` 传达，
而 agentao 现在返回 `{modeId}`（`session_set_mode.py:86`）。

---

## 1. 范围

G4 是**纯出站、client 无关**的——它发更丰富的 `session/update` 通知（外加一个 session/new 响应
字段），**不**需要 client 回调（与 G1 不同）。对 chat/automation 目标它是恰当的 now-work：chat UI
能原生渲染计划、模式选择器、命令面板，而 DeepChat 既有的 `set_mode`/`set_model` 工作已经指明了这个
方向的需求。

超范围：UI-mode 与权限轴的*拆分*（`session_set_mode.py:15-19` 已延后）——对 chat 目标，把 4 个权限
preset 直接映射为 ACP 模式就是务实的 v1。

---

## 2. 核实到的 ACP 形状（v1 schema，2026-06-18）

```jsonc
// session/update 变体 "plan"  →  Plan
Plan       = { entries: PlanEntry[] }                       // 每次更新 client 替换整个 plan
PlanEntry  = { content: string,                             // 必填
               priority: "high"|"medium"|"low",             // 必填
               status:   "pending"|"in_progress"|"completed" } // 必填

// session/update 变体 "current_mode_update"  →  CurrentModeUpdate
CurrentModeUpdate = { currentModeId: string }

// NewSessionResponse.modes（可选）  →  SessionModeState
SessionModeState = { currentModeId: string, availableModes: SessionMode[] }
SessionMode      = { id: string, name: string, description?: string|null }

// session/set_mode
SetSessionModeRequest  = { sessionId, modeId }
SetSessionModeResponse = {}                                 // 空（仅 _meta）

// session/update 变体 "available_commands_update"  →  AvailableCommandsUpdate
AvailableCommandsUpdate = { availableCommands: AvailableCommand[] }
AvailableCommand        = { name: string, description: string, input?: AvailableCommandInput|null }
AvailableCommandInput   = { hint: string }                 // "命令名之后输入的所有文本即为输入"
```

---

## 3. Agentao 源原语

- **`todo_write`**（`tools/todo.py` —— `TodoWriteTool`）：持有 `self.todos:
  List[{content, status}]`；status 枚举 `pending|in_progress|completed` 与 `PlanEntryStatus`
  **1:1 一致**。`execute()` 替换整个列表（符合 ACP 的"替换整个 plan"）。无 `priority` 字段。
  已有 `get_todos()` 访问器。
- **`PermissionMode`**（`permissions.py:75-79`）：`read-only`、`workspace-write`、
  `full-access`、`plan`。默认 `WORKSPACE_WRITE`。这些字符串值即天然的 `SessionModeId`。
  `session.mode_id` 已持久化（`acp/models.py:255`）。
- **`EventType.PERMISSION_MODE_CHANGED`**（`transport/events.py:45`）：模式变更时触发——是
  `current_mode_update` 的单一钩子（同时覆盖 client 驱动的 `session/set_mode` 与 runtime 内部切换
  如 `/plan implement`）。
- **斜杠命令**（`cli/help_text.py`）：`/memory /compact /mcp /sessions /model /mode /skills
  /replay /sandbox …`——已核实是 **host/CLI 子系统控制**，不是 agent-task 命令。这是 G4c 建议的
  关键。

---

## 4. 设计

### 4.1 Modes（第一做）

**Schema**（`agentao/acp/schema.py`）：新增 `AcpSessionMode{id, name, description?}`、
`AcpSessionModeState{currentModeId, availableModes}`、以及
`AcpSessionUpdateCurrentMode{sessionUpdate:"current_mode_update", currentModeId}`；把后者加进
`AcpSessionUpdate` 联合（`schema.py:567`）；给 session/new 响应模型加 `modes`。

**session/new**（`session_new.py:421`）：从 live engine 组 `modes`——
```python
"modes": {
  "currentModeId": state.agent.permission_engine.active_mode.value,   # 如 "workspace-write"
  "availableModes": [
    {"id": "read-only",       "name": "Read-only",       "description": "无写入与 shell。"},
    {"id": "workspace-write", "name": "Workspace write",  "description": "写入 + 安全 shell；web 需确认。"},
    {"id": "full-access",     "name": "Full access",      "description": "所有工具，无提示。"},
    {"id": "plan",            "name": "Plan",             "description": "只规划，不执行。"},
  ],
}
```
（列表从 `PermissionMode` 枚举 + 名称映射构建，避免漂移。）

**current_mode_update**：在 `transport.py::_build_update` 里把
`EventType.PERMISSION_MODE_CHANGED` → `{sessionUpdate:"current_mode_update", currentModeId:<新模式>}`。
一处映射覆盖所有触发。*需核实* `permission_engine.set_mode()` 是否真的把 `PERMISSION_MODE_CHANGED`
发到会话的 transport 上；若没有，则从 `session_set_mode` handler 直接发 `current_mode_update` 兜底。

**session/set_mode 响应**（`session_set_mode.py:86`）：ACP 标准响应为空 + 经通知传达变更。
**为 DeepChat 兼容保留返回 `{modeId}`**（标准 client 读 `current_mode_update`、忽略这个多余字段），
*同时*发 `current_mode_update`。非 preset modeId（DeepChat 的 `code`/`ask`）仍为 UI-only 状态，即便
不在 `availableModes` 里，也照样在 `current_mode_update` 中回显。

### 4.2 Plan（第二做）

**Schema**：新增 `AcpPlanEntry{content, priority, status}` 与
`AcpSessionUpdatePlan{sessionUpdate:"plan", entries:[AcpPlanEntry]}`；加进 `AcpSessionUpdate` 联合。

**Transport 特判**（`transport.py::_build_update`）：当工具是 `todo_write` 时，把它的 `TOOL_START`
（其 `rawInput.todos` 带着列表）映射成 **`plan`** 更新而非 `tool_call`，并 drop `todo_write` 的
`TOOL_COMPLETE`：
```python
if tool == "todo_write":
    todos = data.get("args", {}).get("todos", [])
    return {"sessionUpdate": "plan",
            "entries": [{"content": t["content"],
                         "priority": "medium",          # agentao todo 无 priority
                         "status": t["status"]} for t in todos]}
```
**priority 合成**：agentao todo 无 priority，而 ACP 必填。v1 全部发 `"medium"`。*可选 follow-up*：
给 `todo_write` schema 加一个可选 `priority` 字段让 LLM 自己设（再透传）。零 runtime 改动——映射全在
ACP transport 内，符合 `embedding-vs-acp.md`。（已考虑的备选：由工具发一个新的
`EventType.PLAN_UPDATED`——解耦更彻底但要动 runtime；除非另有非 ACP 前端也需要 plan 事件，否则延后。）

### 4.3 Commands（缩范围或延后）

**发现：** agentao 的斜杠命令是 host/CLI 子系统控制，在 ACP 上**对 agent runtime 无语义**；而 ACP
命令的*调用*是把命令当 `session/prompt` 文本回传（`UnstructuredCommandInput` = "命令名之后的所有
文本"），需要 agent 端解析并 dispatch——这是与"广告"相互独立的一套机制。

**建议（两档）：**
- **仅广告（廉价、安全）：** 若存在任何对 agent 有意义的命令，在 `session/new` 后发
  `available_commands_update` 列出它们；不做特殊路由时，被选中的命令只是作为 prompt 文本到达、由
  LLM 理解。低风险、价值适中。
- **延后：** host-action 命令（`/memory`、`/compact`、`/sessions`…）需要 prompt-routing + host
  往返，对有自己 UI 的 chat client 价值不大。按 demand-gated 原则，等 DeepChat 真要 slash-command
  自动补全时再做。

净结论：**不要照搬 CLI 命令列表**；commands 是 G4 里最弱的三分之一。

---

## 5. 一致性附带项（回填进 `acp-server-conformance-review.md`）

1. **ToolKind = 10 个值**，不是 9：`read, edit, delete, move, search, execute, think, fetch,
   switch_mode, other`。review 的 G2 注（以及本地 `kind` 枚举，现 6 个）应对齐到 10。`switch_mode`
   也是把"模式切换"呈现为 tool call 的候选 kind（可选）。
2. **`session/set_mode` 响应**：ACP 标准为空、经 `current_mode_update` 传达变更。agentao 的
   `{modeId}` 是非标准多余字段（为 DeepChat 保留）。G4.1 通过加这条通知来解决。

---

## 6. 落地计划

**PR-1（Modes + Plan —— 一个紧凑改动）：**
- `agentao/acp/schema.py`：5 个新模型 + 联合追加 + session/new `modes` 字段。重生成
  `docs/schema/host.acp.v1.json`；更新 schema 快照测试。
- `agentao/acp/transport.py`：`todo_write`→`plan`；`PERMISSION_MODE_CHANGED`→`current_mode_update`。
- `agentao/acp/session_new.py`：响应里发 `modes`。
- `agentao/acp/session_set_mode.py`：发 `current_mode_update`；保留 `{modeId}`。
- 测试：扩 `tests/test_acp_transport.py`（plan + mode 映射）、session/new modes 断言、schema 快照。

**PR-2（Commands —— 仅在 greenlit 时）：** 仅广告极小集合；不照搬 CLI。

**验证：** 用重生成的 `host.acp.v1.json` 校验发出的通知；理想情况下也对照上游 ACP schema（关联 G6）。

---

## 7. 待决问题

1. `permission_engine.set_mode()` 是否把 `PERMISSION_MODE_CHANGED` 发到会话的 ACP transport 上？
   （决定 `current_mode_update` 是一处映射、还是要在 handler 侧补发。）—— **实现时核实。**
2. `plan` 映射是否也应在 `TOOL_COMPLETE` 上触发（万一 client 想要一个终态"计划已定"信号），还是
   `TOOL_START` 一次就够？—— 倾向只在 start（调用时列表即终态；`todo_write` 同步）。
3. priority：先全发 `medium`，还是在同一 PR 里加 `todo_write` 的可选 `priority` 字段？—— 倾向先全
   `medium`，字段作为 fast-follow。
