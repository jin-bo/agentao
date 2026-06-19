# ACP G4 —— Plan、Modes、Commands 的 session/update 设计

**状态：** 设计提案——**2026-06-18 经评审修订；已收敛，待批准。** 是
`acp-server-conformance-review.md` 中 **G4** 的落地设计——在维护者把目标 client 类别定为
**chat/automation**（故 G1 fs/terminal 为非目标、G4/G3/G2-diff 为 now-work）之后，G4 是最靠前的
chat 相关 ACP 差距。评审钉死了 `current_mode_update` 的发出路径（从 handler 发，而非那个不触发的
engine 事件）、给 `todo_write`→`plan` 加了防御性校验、指出 `modes` 已是宽松占位字段、并把 Commands
收敛为干净的延后。第二轮评审又修了 `todo_write` 失败路径（**无条件 drop** `TOOL_COMPLETE`——既然
`TOOL_START` 已变成 `plan`，再发 `tool_call_update` 就成了孤儿）以及通知发送措辞（直接
`server.write_notification`，而非"会话 transport"）。**已批准进入实现；尚未实现。**
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
| `modes`（session/new）+ `current_mode_update` | `PermissionMode{read-only, workspace-write, full-access, plan}`（`permissions.py:75`）；把既有的宽松 `modes` schema 字段 typed 化；`current_mode_update` **从 set_mode handler 发** | 高——4 个 preset → availableModes | **第一做**——最小、一致性收益最高 |
| `available_commands_update` —— `[{name, description, input?}]` | 斜杠命令（`cli/help_text.py`）——但**是 host/CLI 控制，无 agent-runtime 语义** | 低 | **本轮不做**——仅当 DeepChat 要 command palette 时另开设计 |

**顺序：PR-1 = 只做 Modes + Plan。** 两者都是纯 ACP 层 + schema（无 runtime 改动）。Commands
**延后，本轮不规划实现**。

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
- **`EventType.PERMISSION_MODE_CHANGED`**（`transport/events.py:45`）：**由 CLI 发出**
  （`cli/app.py:172`），**不是** `PermissionEngine.set_mode()` 发的（`permissions.py:382`——无 emit），
  也**不是** ACP `session/set_mode` handler 发的。所以在 ACP 路径上这个事件根本不触发——
  `current_mode_update` 必须由 handler 自己发（见 §4.1）。transport *可以*额外映射这个事件以求完整，
  但 ACP 路径不得依赖它。
- **斜杠命令**（`cli/help_text.py`）：`/memory /compact /mcp /sessions /model /mode /skills
  /replay /sandbox …`——已核实是 **host/CLI 子系统控制**，不是 agent-task 命令。这是 G4c 建议的
  关键。

---

## 4. 设计

### 4.1 Modes（第一做）

**Schema**（`agentao/acp/schema.py`）：新增 `AcpSessionMode{id, name, description?}`、
`AcpSessionModeState{currentModeId, availableModes}`、以及
`AcpSessionUpdateCurrentMode{sessionUpdate:"current_mode_update", currentModeId}`；把后者加进
`AcpSessionUpdate` 联合（`schema.py:567`）。注意 `modes` **已存在**于 session/new 响应上、是个
**宽松占位**（`schema.py:198`：`modes: Optional[Dict[str, Any]] = None`）——**用 typed 的
`AcpSessionModeState` 替换它**，不要再加第二个字段。

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

**current_mode_update** —— 从 **handler** 发，而非靠事件。已核实：`PermissionEngine.set_mode()`
**不 emit**（`permissions.py:382`）；`PERMISSION_MODE_CHANGED` 事件只由 CLI 发（`cli/app.py:172`），
ACP `session_set_mode` handler 什么都不发。所以在 transport 里映射这个事件会**完全漏掉 ACP 路径**。
最简正确路线：在 `session_set_mode` 里、`session.mode_id = mode_id` 之后，直接调用（handler 已持有
`server`；`AcpSessionState` **没有** transport 字段）：
```python
server.write_notification(METHOD_SESSION_UPDATE, {
    "sessionId": session.session_id,
    "update": {"sessionUpdate": "current_mode_update", "currentModeId": mode_id},
})
```
或把这一条通知包成一个很小的 helper。（transport *可以*也映射 `PERMISSION_MODE_CHANGED`，让未来
runtime 内部切换也能冒出来，但 handler 不得依赖它。）

**session/set_mode 响应**（`session_set_mode.py:86`）：ACP 标准响应为空 + 经通知传达变更。
**为 DeepChat 兼容保留返回 `{modeId}`**（标准 client 读 `current_mode_update`、忽略这个多余字段），
*同时*发 `current_mode_update`。非 preset modeId（DeepChat 的 `code`/`ask`）仍为 UI-only 状态，即便
不在 `availableModes` 里，也照样在 `current_mode_update` 中回显。

### 4.2 Plan（第二做）

**Schema**：新增 `AcpPlanEntry{content, priority, status}` 与
`AcpSessionUpdatePlan{sessionUpdate:"plan", entries:[AcpPlanEntry]}`；加进 `AcpSessionUpdate` 联合。

**Transport 特判，带防御**（`transport.py::_build_update`）：当工具是 `todo_write` 时，把它的
`TOOL_START`（其 `rawInput.todos` 带着列表）映射成 **`plan`** 更新而非 `tool_call`。`todos` 来自
LLM、可能畸形，所以要**校验**而非信任：
```python
_STATUS = {"pending", "in_progress", "completed"}
if tool == "todo_write":
    raw = data.get("args", {}).get("todos", [])
    entries = [
        {"content": t["content"], "priority": "medium", "status": t["status"]}
        for t in raw
        if isinstance(t, dict)
        and isinstance(t.get("content"), str)
        and t.get("status") in _STATUS
    ]
    # 若校验后一个都不剩，就回落到普通 tool_call 映射，而不是发空/脏 plan。
    if entries:
        return {"sessionUpdate": "plan", "entries": entries}
```
**无条件 drop `todo_write` 的 `TOOL_COMPLETE`。** 既然 `TOOL_START` 已被映射成 `plan`（不是
`tool_call`），完成时再发*任何* `tool_call_update`——包括 `status:"failed"`——都会是**孤儿终结
更新**：一个没有起始 `tool_call` 的 `tool_call_update`，破坏 ACP 消息序列。所以 `todo_write` 的
`TOOL_COMPLETE` **一律 drop**。失败本就几乎不可能（`todo_write` 是同步的内存列表替换）；万一需要冒
出失败，就发一条简短的 `agent_message_chunk` / `agent_thought_chunk` 文本，而非 `tool_call_update`。
必须有测试钉死这点：一个 `todo_write` turn 只发一条 `plan`，且**不发**任何 `tool_call` / 终结
`tool_call_update`。

**priority**：agentao todo 无 priority，而 ACP 必填 → 全部发 `"medium"`。**本 PR 超范围：**给
`todo_write` 工具 schema 加 `priority` 字段——那会把一个 ACP 适配改动扩成 runtime/工具契约改动，不
值得。零 runtime 改动：映射全在 ACP transport 内，符合 `embedding-vs-acp.md`。

### 4.3 Commands —— 延后，本轮不规划实现

agentao 的斜杠命令是 host/CLI 子系统控制，在 ACP 上**对 agent runtime 无语义**；而 ACP 命令的
*调用*是把命令当 `session/prompt` 文本回传（`UnstructuredCommandInput` = "命令名之后的所有文本"），
需要 agent 端解析并 dispatch——这是与"广告"相互独立的一套机制。**不要照搬 CLI 命令列表。**

**决定：** Commands 本轮**不做**。仅当 DeepChat（或其它目标 client）明确要 command palette 时，
另开一份设计——届时的问题是*哪些*对 agent 有意义的命令存在、以及调用如何路由，而不是怎么镜像 CLI。
按 demand-gated 原则，不留投机性的"仅广告"分支。

---

## 5. 一致性附带项（回填进 `acp-server-conformance-review.md`）

1. **ToolKind = 10 个值**，不是 9：`read, edit, delete, move, search, execute, think, fetch,
   switch_mode, other`。review 的 G2 注（以及本地 `kind` 枚举，现 6 个）应对齐到 10。`switch_mode`
   也是把"模式切换"呈现为 tool call 的候选 kind（可选）。
2. **`session/set_mode` 响应**：ACP 标准为空、经 `current_mode_update` 传达变更。agentao 的
   `{modeId}` 是非标准多余字段（为 DeepChat 保留）。G4.1 通过加这条通知来解决。

---

## 6. 落地计划

**PR-1 —— 且只有这一个 PR。两件事：** typed modes + `current_mode_update`，以及防御性的
`todo_write`→`plan` transport 映射。
- `agentao/acp/schema.py`：新增 `AcpSessionMode` / `AcpSessionModeState` /
  `AcpSessionUpdateCurrentMode` / `AcpPlanEntry` / `AcpSessionUpdatePlan`；**用
  `AcpSessionModeState` 替换宽松的 `modes: Optional[Dict[str,Any]]`**（`schema.py:198`）；把两个
  新 update 加进 `AcpSessionUpdate` 联合。重生成 `docs/schema/host.acp.v1.json`；更新 schema 快照测试。
- `agentao/acp/transport.py`：防御性 `todo_write`→`plan`（校验 entries；**无条件** drop `todo_write`
  的 `TOOL_COMPLETE`——不留孤儿终结更新）。可选地也映射 `PERMISSION_MODE_CHANGED`→`current_mode_update` 求完整——但它对
  ACP 路径不是承重的（见 §4.1）。
- `agentao/acp/session_new.py`：响应里发 typed `modes`。
- `agentao/acp/session_set_mode.py`：在 `session.mode_id = mode_id` 之后**从 handler 发
  `current_mode_update`**；保留返回 `{modeId}` 给 DeepChat。
- 测试：`tests/test_acp_transport.py`（plan 正常路径只发一条 `plan`、且**不发** `tool_call`/终结
  `tool_call_update`；畸形 todos 回落到普通 `tool_call` 映射；模式变更发 `current_mode_update`）、
  session/new typed-modes 断言、schema 快照。

**Commands：** 延后，**不规划 PR**（见 §4.3）。

**验证：** 用重生成的 `host.acp.v1.json` 校验发出的通知；理想情况下也对照上游 ACP schema（关联 G6）。

---

## 7. 评审中已决（2026-06-18）

1. **`current_mode_update` 触发** —— *已决。* `PermissionEngine.set_mode()` 不 emit、ACP handler
   也什么都不发（`permissions.py:382`、`session_set_mode.py`），所以事件映射路线会漏掉 ACP 路径。
   **从 handler 发**（§4.1）；transport 事件映射可选、非承重。
2. **`todo_write` 的 `TOOL_COMPLETE`** —— *已决（第二轮修订）。* 早先草稿在失败路径保留
   `tool_call_update:failed`；评审指出那是**孤儿终结更新**（没有起始 `tool_call`，因为 `TOOL_START`
   已成 `plan`），破坏 ACP 序列。**一律 drop** `todo_write` 的 `TOOL_COMPLETE`；那个几乎不可能的失败
   用简短 `agent_message_chunk` 冒出，绝不用 `tool_call_update`（§4.2）。plan 在 `TOOL_START` 发
   （调用时列表即终态；`todo_write` 同步）。由测试钉死。
3. **priority** —— *已决。* 全发 `medium`。给 `todo_write` 加 `priority` 字段属**超范围**——它把一个
   ACP 适配改动扩成 runtime/工具契约改动（§4.2）。
