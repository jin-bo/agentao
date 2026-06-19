# ACP G4 —— Plan、Modes、Commands 的 session/update 设计

**状态：** 设计——**已在 PR-1 实现**（分支 `feat/acp-g4-modes-plan`，2026-06-18）。是
`acp-server-conformance-review.md` 中 **G4** 的落地设计——在维护者把目标 client 类别定为
**chat/automation**（故 G1 fs/terminal 为非目标、G4/G3/G2-diff 为 now-work）之后，G4 是最靠前的
chat 相关 ACP 差距。`modes` 部分按设计落地（且 `--resume` 路径同样播报）。`plan` 部分在**实现阶段
的 `/code-review` 中被纠正**：plan 现**延迟到 `TOOL_COMPLETE`（仅 `status=="ok"` 时发）**，而非在
`TOOL_START`——后者在**权限判定之前**触发（被拒的 `todo_write` 会显示一个幻象「已生效」plan），且会
把空/畸形回落的 `tool_call` 变成孤儿；校验改为**全有或全无**。§4.2 与 §7 描述最终落地设计。
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
| `plan` 更新 —— `Plan{entries:[PlanEntry{content, priority, status}]}` | `todo_write` 工具（`tools/todo.py`）；`todos:[{content,status}]`，status 枚举完全一致 | 高——仅缺 `priority` | **第二做**——transport 映射，**延迟到 `TOOL_COMPLETE`-on-`ok`**，合成 `priority:"medium"` |
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

**Transport 映射**（`transport.py::_build_update`）：把 `todo_write` 调用呈现为原生 ACP `plan`——
但**在 `TOOL_COMPLETE` 发，而非 `TOOL_START`**（原因见下方纠正说明）。流程按 `call_id` 走，transport
上有一个很小的 per-call 暂存（`self._todo_plan_calls`）：

- **`TOOL_START`**：从 `rawInput.todos` 构建 plan、**暂存**（`self._todo_plan_calls[call_id] = plan`），
  **不发**任何东西。若 todos 校验不过（空，或**任一**条畸形），不暂存——回落到普通 `tool_call` 映射。
- **`TOOL_COMPLETE`**：pop 暂存。若有 plan，**仅当 `status == "ok"`**（调用确实生效）时发；被拒/
  失败/取消则**什么都不发**——`TOOL_START` 没开任何东西，故无需收尾。若没有暂存的 plan，说明这个
  `call_id` 走了回落 `tool_call`，让它正常 complete（一条终结 `tool_call_update`）。

**校验是全有或全无**（`_transport_helpers.py::_todo_write_plan`）：`plan` 每次更新替换**整个**清单，
故静默丢一条畸形项会让一个真实任务从 client 视图消失。`content` 必须是字符串、`status` 须为
`pending|in_progress|completed`；若列表为空或**任一**条不合格 → 返回 `None`，回落到 `tool_call`
映射（带完整 raw args），而不是发截断或空 plan。

> **为什么延迟到 `TOOL_COMPLETE`（实现评审的纠正）。** 最初设计在 `TOOL_START` 发 plan、并无条件
> drop `TOOL_COMPLETE`。`/code-review` 发现两个 bug：
> 1. **被拒/失败的 plan 显示为已生效。** `TOOL_START` 在**权限判定之前**触发
>    （`runtime/tool_executor.py`），所以 read-only 下被拒（或失败）的 `todo_write` 仍发了一条 client
>    会显示为「生效」的 plan——即便 `execute()` 根本没跑。
> 2. **回落 `tool_call` 成孤儿。** 空/畸形 todos 时 `TOOL_START` 回落成真实 `tool_call`，但无条件
>    drop `TOOL_COMPLETE` 又把它**永久挂起**（没有终结更新）。
>
> 延迟到 `TOOL_COMPLETE`-on-`ok` 同时修好两者：被拒/失败不发 plan，回落 `tool_call` 正常 complete。
> 代价是 per-`call_id` 暂存——有界，因为 `TOOL_START`/`TOOL_COMPLETE` 必成对、完成时即 pop。
> （`todo_write` 是无 `output_callback` 的纯同步工具，两者之间不会发 `TOOL_OUTPUT`，故无中间孤儿风险。）

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
- `agentao/acp/transport.py`：`todo_write`→`plan`，**延迟到 `TOOL_COMPLETE`-on-`ok`**（在
  `TOOL_START` 按 `call_id` 暂存；全有或全无校验；被拒/失败 → 不发 plan；空/畸形 → 回落 `tool_call`
  并正常 complete）。可选地也映射 `PERMISSION_MODE_CHANGED`→`current_mode_update` 求完整——但它对
  ACP 路径不是承重的（见 §4.1）。
- `agentao/acp/_transport_helpers.py`：`_todo_write_plan`（全有或全无校验，合成 `priority:"medium"`）。
- `agentao/acp/session_new.py`：响应里发 typed `modes`。
- `agentao/acp/session_load.py`：`resume_session_on_new` 同样播报 typed `modes`，让 `--resume` 的
  client 与新建会话一样拿到模式选择器。
- `agentao/acp/session_set_mode.py`：在 `session.mode_id = mode_id` 之后**从 handler 发
  `current_mode_update`**；保留返回 `{modeId}` 给 DeepChat。
- 测试：`tests/test_acp_transport.py`（plan 在 `TOOL_START` 暂存、`TOOL_COMPLETE`-`ok` 时才发；被拒/
  失败 → 不发 plan；空/畸形 → 回落 `tool_call` 并 complete；模式变更发 `current_mode_update`）、
  `tests/test_acp_session_new.py` + `tests/test_acp_resume_on_startup.py` 的 typed-modes 断言、schema 快照。

**Commands：** 延后，**不规划 PR**（见 §4.3）。

**验证：** 用重生成的 `host.acp.v1.json` 校验发出的通知；理想情况下也对照上游 ACP schema（关联 G6）。

---

## 7. 评审中已决（2026-06-18）

1. **`current_mode_update` 触发** —— *已决。* `PermissionEngine.set_mode()` 不 emit、ACP handler
   也什么都不发（`permissions.py:382`、`session_set_mode.py`），所以事件映射路线会漏掉 ACP 路径。
   **从 handler 发**（§4.1）；transport 事件映射可选、非承重。
2. **Plan 发出时机与 `TOOL_COMPLETE`** —— *已决（两次修订；最终 = 实现）。* 设计经历三个位置：
   **(a)** 在 `TOOL_START` 发 `plan`、失败时保留 `tool_call_update:failed`；
   **(b)** 在 `TOOL_START` 发 `plan`、**一律 drop** `TOOL_COMPLETE`（第二轮评审——那条 failed 更新是孤儿）；
   **(c) 最终、已实现：** **把 `plan` 延迟到 `TOOL_COMPLETE`、仅 `status=="ok"` 时发**（§4.2）。实现
   阶段 `/code-review` 指出 (b) 仍在 `TOOL_START` 发——那在**权限判定之前**——read-only 下被拒的
   `todo_write` 会显示幻象「已生效」plan；且空/畸形回落的 `tool_call` 被无条件 drop 弄成孤儿。(c)
   同时修好：被拒/失败不发 plan，回落 `tool_call` 正常 complete。
3. **priority** —— *已决。* 全发 `medium`。给 `todo_write` 加 `priority` 字段属**超范围**——它把一个
   ACP 适配改动扩成 runtime/工具契约改动（§4.2）。
4. **Plan 校验** —— *已决（实现）。* `_todo_write_plan` 是**全有或全无**，不是逐条过滤：`plan` 是
   全量替换，逐条过滤会静默截断清单。任一条畸形（或空列表）→ 回落到带完整 raw args 的 `tool_call`。
5. **Resume 一致性** —— *已决（实现）。* `resume_session_on_new`（`--resume` 在首个 `session/new`
   触发的路径）播报与新建会话相同的 typed `modes`；否则被 resume 的 client 拿不到模式选择器。
