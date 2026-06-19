# ACP Server —— 标准一致性差距评审

**状态：** 评审记录。起草于 2026-06-18，基于对 `agentao.acp` server 包的逐文件通读，
对照 Agent Client Protocol（ACP，协议版本 1）核对。**这是一份差距分析 + 优先级化的改进
建议，而非已批准的方案。** 它记录 ACP server 实现了什么、在哪些地方偏离标准，并*供维护者
判断*——什么该建、什么该写明文档、什么该有意不做。
**读者：** Agentao 维护者；以及任何把 agentao 嵌入到 ACP client（Zed、DeepChat……）
背后的人。
**对照件：** `acp-server-conformance-review.md`。
**相关：**
- `deepchat-acp-patch-revision.md` —— 本评审据以延续的、此前的 ACP 字段改名 / accept-unknown
  工作（`session/set_mode`、`set_config_option`）。
- `embedding-vs-acp.md` —— 为什么 ACP 只是嵌入式内核之上的一个*前端*，而非内核契约本身。
- `host-fs-policy.md` —— §4（G1）中 fs-proxy 决策必须与之调和的宿主文件系统策略设计。
- `path-a-roadmap.md` —— embed-first 战略；ACP 一致性按真实目标 client 做需求门控，符合 §2.3 非目标。

**方法：** 通读 dispatch 表与每一个注册的 handler，grep 出站 `server.call` 调用点与
`fs/` `terminal/` 用法，对照 ACP v1 的方法/能力面。代码引用锚定于 `main`@`bcdb8e4`
（2026-06-18）。ACP 规范相关陈述带日期、会漂移——据其行动前请重新核实上游 schema。方法归属
已于 2026-06-18 对照官方 ACP v1 schema（`schema/v1/schema.json`）核实；初稿被更正的几处见
TL;DR 下的更正说明。

---

## TL;DR

Agentao 的 ACP server 是**工程质量很高、但只实现了"Agent 一侧"协议**的实现。*核心*的
client→agent 请求面（`initialize`、`session/new|load|prompt|cancel`、mode/config）很健壮
——但 v1 的会话管理方法（`session/list|delete|resume|close`、`logout`）尚未实现。几乎缺席
的，是*消费 client 反向提供的能力*——`fs/*` 与 `terminal/*` 方法。结果是：嵌入真实 ACP
client 时，agentao 更像一个"恰好会说 ACP 的无头 agent"，而非深度集成进编辑器的 agent。

> **更正（2026-06-18，评审后）：** 本文初稿后重新拉取了官方 ACP v1 schema（release 日期
> 2026-06-18）。初稿有三处错误，已在下文修正：(i) 请求面**并非**"完整"——v1 定义了
> `session/list|delete|resume|close` 与 `logout`，agentao 均未实现；(ii) `session/set_model`
> 与 `session/list_models` 是 **Agentao 扩展，不是 ACP v1 方法**；(iii) G2/G3 **并非**纯运行时
> ——agentao 自己冻结的契约（`agentao/acp/schema.py` → `docs/schema/host.acp.v1.json`）当前
> *禁止* `locations`/`diff`，且漏了 `max_tokens` 停止原因，这些也必须一并更新。来源：`main` 上
> 的官方 ACP schema `schema/v1/schema.json`。

> **定位前提（决定 G1 的优先级）。** ACP 把 `fs/*` 与 `terminal/*` 设为 *capability-gated*：
> agent 只有在 client 广告 `fs.readTextFile`/`writeTextFile`/`terminal` 时才用它们。**chat-class
> 的 host 通常不广告**——所以 agentao 现在的"本地文件工具 + 本地 shell"行为在那里**已经正确且
> 合规**，G1 对非 IDE host **根本不是 gap**。G1 只在**编辑器级 client（Zed/Cursor）**广告了这些
> 能力、而 agentao 无视时才咬人。设计文档（`embedding-vs-acp.md:13`）确实把 Zed/Cursor/IDE 列为
> ACP 目标，但唯一*在途*的真实集成是 **DeepChat**（Electron 聊天 UI，
> `deepchat-acp-patch-revision.md`）。按项目自己的 demand-gated 纪律（gap ≠ need），**G1 的
> headline 地位取决于"编辑器级 client 是否成为真实目标"**；没有这个信号时，当前优先级是 client
> 无关 / chat 相关的差距（**G4**、**G3**、以及 G2 的 `diff`）。下表"适用于"列按 client 类别给每个
> 差距打了标签。

G1 是**针对编辑器级目标**的最高杠杆差距，且代码里已"已知但未建"；对 chat/automation host 它属
demand-gated（见前提）。**G3** 与 **G2 的 `diff` 那一半**是本地增强（schema + 发送，不需 client
往返），对*任何* client 都有回报；**G4** 则正是 chat client 会渲染的东西。

| # | 差距 | 适用于 | 严重度 | 投入 |
|---|-----|--------|--------|------|
| G1 | Agent 从不调用 client 的 `fs/*` / `terminal/*` | **仅编辑器级 client**——chat host：现有行为已合规 | 高 *(IDE)* / 不适用 *(chat)* | 高 |
| G2 | `tool_call` 更新缺 `locations` + `diff` | `diff`：任何渲染编辑的 client · `locations`：仅编辑器 | 高 *(IDE)* / 中 *(chat)* | 低-中 |
| G3 | `stopReason` 只有 `end_turn`/`cancelled` | 任何 client + automation 表面 | 中 | 低 |
| G4 | mode / plan / commands 未映射为 ACP 更新 | chat **与**编辑器（chat 会渲染这些） | 中-高 | 中 |
| G5 | 能力面较薄（http MCP、audio、embedded resource） | 需求门控 | 低 | 不一 |
| G6 | 无上游 schema 一致性测试 | 所有 | 中 | 低 |

---

## 1. 范围与此处"标准"的含义

ACP（Agent Client Protocol，agentclientprotocol.com）是 Zed 发起的、位于
**编辑器/client** 与 **agent** 之间的 JSON-RPC 协议，双向：

- **client → agent**：`initialize`、`authenticate`、`logout`、`session/new`、
  `session/load`、`session/prompt`、`session/cancel`（notification）、
  `session/list`、`session/delete`、`session/resume`、`session/close`、
  `session/set_mode`、`session/set_config_option`。（注：`session/set_model` 与
  `session/list_models` **不在** v1 中——它们是 Agentao 扩展。）
- **agent → client**：`session/update`（notification）、`session/request_permission`，
  以及受能力门控的 **`fs/read_text_file`**、**`fs/write_text_file`**、
  **`terminal/create`**、**`terminal/output`**、**`terminal/wait_for_exit`**、
  **`terminal/kill`**、**`terminal/release`**。

协议版本：**1**（`agentao/acp/protocol.py:18`）。

一个*完整*的 ACP agent 做两件事：既回答 client 的请求，**也**驱动 client 的 fs/terminal，
使文件编辑与 shell 命令流经编辑器自己的视图（未保存缓冲区、diff 审阅、终端面板）。agentao
把第一件做得很彻底，第二件几乎没做。

---

## 2. 已实现的部分（Agent 一侧——做得好）

注册的 handler（`agentao/acp/__main__.py:99-108`）：

| 方法 | 文件 | 说明 |
|---|---|---|
| `initialize` | `initialize.py` | 版本协商（echo-or-latest）、能力广告、`agentInfo`、`_meta` 扩展列表 |
| `session/new` | `session_new.py` | `cwd` 校验、MCP server 翻译、`configOptions`、启动 resume seam |
| `session/load` | `session_load.py` | 经 `_ReplayMixin` 做历史回放 |
| `session/prompt` | `session_prompt.py` | `text` / `resource_link` / `image` content block；返回 `stopReason` |
| `session/cancel` | `session_cancel.py` | 规范为 notification；容忍被当 request 发送的 client |
| `session/set_model` ⚠ | `session_set_model.py` | **Agentao 扩展——不在 ACP v1。** 无 vendor 的模型切换 |
| `session/set_config_option` | `session_set_config_option.py` | 标准 config 路径；host `provider_resolver` 使凭据不上线 |
| `session/set_mode` | `session_set_mode.py` | `modeId` 字段；accept-unknown（DeepChat 的 `code`/`ask`） |
| `session/list_models` ⚠ | `session_list_models.py` | **Agentao 扩展——不在 ACP v1。** `{models: [...]}` |
| `_agentao.cn/ask_user` | 扩展 | 按 ACP 用下划线前缀，在 `_meta` 声明 |
| `_agentao.cn/set_model` | `agentao_set_model.py` | 自由文本模型设定（DeepChat"输入任意模型"UX） |

出站（agent → client）：`session/update` notification（事件映射丰富，`transport.py`）与
`session/request_permission`（`_transport_interaction.py:161`）。

**值得保留的工程亮点：**
- **并发派发**（`server.py:22-42`）：handler 跑在 `ThreadPoolExecutor` 上，使阻塞在
  `session/request_permission` 里的 worker 不会卡死 stdin 读循环——这是对"阻塞式
  server→client 请求"问题的正确解法。
- **stdout/log 卫生**：`sys.stdout` 被重定向到 stderr，所有 JSON-RPC 写入经捕获句柄、加锁
  完成，进程内任何走失的 `print` 都污染不了线路。
- **确定性关闭顺序**（`server.py:363-394`）：cancel 出站 pending → trip session 取消令牌
  → drain executor → 关闭 sessions。
- **image block 安全**（`session_prompt.py:148-198`）：运行时镜像 `additionalProperties:false`
  ——除 `{type,data,mimeType}` 外任何键一律拒绝，线路上绝不可能夹带宿主路径或密钥；外加尺寸
  上限与解码前的 base64 校验。
- **凭据绝不上线**：模型/provider 切换的密钥在 server 侧经 host 可注入的 `provider_resolver`
  解析。

---

## 3. 与标准的差距

### G1 —— Agent 不消费 client 的 `fs/*` 与 `terminal/*` 能力 *(最高杠杆——仅对编辑器级 client)*

> **优先级随定位而定（见 TL;DR 的定位前提）。** 因为 ACP 把 `fs/*`/`terminal/*` 门控在 client
> 广告的能力上，一个不广告这些的 chat-class host 从 agentao 拿到的就是*正确、合规*的行为——对这类
> host **本条不是 gap**，本节其余内容也不适用。下文分析假设的是一个*确实*广告了 fs/terminal 的
> **编辑器级 client（Zed/Cursor）**；只有这种情形下 G1 才是真实缺口。

**证据。** 整个 server 只有**两处**出站 `server.call`（`_transport_interaction.py:161,328`）：
`session/request_permission` 与 `_agentao.cn/ask_user`。`agentao/` 中**没有**任何对
`fs/read_text_file`、`fs/write_text_file` 或任意 `terminal/*` 方法的调用（grep：无出站
`fs/` 或 `terminal/` 字符串）。

**后果。**
- **文件系统**：文件读写走 agentao 的*本地*文件工具，而非 client。在 Zed 这类编辑器里，这会
  绕过未保存缓冲区与编辑器自己的 fs 视图；client 无法经其原生路径居中调停、做 diff 或追踪编辑。
- **终端**：shell 命令经本地 `LocalShellExecutor` 运行，只能以纯文本塞进 `session/update`。
  client 的终端面板/终端块、以及标准终端生命周期
  （`create`→`output`→`wait_for_exit`→`release`）完全没用上。

**已知但未建。** `session_new.py:93-95` 已记下这个 seam：*"接受 `client_capabilities`，以便
未来的 factory 能据 `fs.readTextFile: true` 在本地文件工具与 ACP 代理文件工具之间做选择。"*
分支已设计好，只是尚未实现。

**定调。** 这是**有代价的选择，而非单纯 bug。** Agentao 是嵌入式 harness，自带 sandbox、
权限引擎与 provider 中立的运行时（`embedding-vs-acp.md`、`host-fs-policy.md`）。为 ACP 会话
把 fs/terminal 路由给 client，*反转*了这种所有权，且必须与宿主 fs 策略设计调和。**不作为**的
代价是在编辑器级 ACP client 里沦为二等公民；**作为**的代价是要多维护一条 fs/exec 路径并做策略
调和。无论哪条，立场都该被*明确决策并写进文档*，而非默而不宣。

### G2 —— `tool_call` 更新缺少结构化保真度 *(本地：schema + 发送，高 ROI)*

`transport.py:236-247` 发出的 `tool_call` 带 `toolCallId`、`title`（= 工具原名）、`kind`、
`status`、`rawInput`。缺以下三项，而它们 ACP v1 全部支持、编辑器 client 也会渲染：

- **`locations: [{path, line}]`** —— 没有它，agent 读/改文件时 client 无法做"跟随 agent"高亮。
- **`type:"diff"` 的 `content`（`oldText`/`newText`）** —— 编辑类工具的结果被当作纯文本
  （`_tool_content_text`）发出，于是 client 渲染成文本块而非可审阅 diff。**diff 视图是 ACP
  对编辑工具的招牌 UX，agentao 放弃了它。**
- **人类可读 `title`** —— ACP 期望如"Writing config.py"，agentao 发"write_file"。

**这是契约变更，不只是发送变更。** agentao 自己冻结的 ACP schema 比 ACP v1 *更*严格，当前
**禁止**这些形态：`AcpSessionUpdateToolCall` 没有 `locations` 字段且 `extra="forbid"`
（`schema.py:536-552`）；`AcpToolCallContentEntry` **只**接受 `type:"content"` 带内层文本块
——没有 `diff`、没有 `terminal`（`schema.py:630-642`）。本地 `kind` 枚举也只有 6 个值
（`read, edit, search, execute, fetch, other`，`schema.py:547`），而 ACP v1 有 9 个（多
`delete, move, think`）——于是 `transport.py:39` 的 `_tool_kind` 映射已经可能产出一个被自己
schema 拒绝的值。因此实现 G2 意味着：更新 `agentao/acp/schema.py`、重新生成
`docs/schema/host.acp.v1.json`、并更新 schema 快照测试——否则发送会过不了项目自己的契约测试。

相关小项：`TOOL_COMPLETE` 把 agentao 的 `cancelled` 映射成 ACP 的 `failed`
（`transport.py:261-268`），因为 ACP 的 tool call 没有 cancelled 状态——可接受但有损。

### G3 —— `stopReason` 贫乏 *(运行时 + schema 漂移)*

是两层，不是一层：
- **运行时**：`session_prompt.py:287-291` 只返回 `end_turn` 或 `cancelled`。代码自己的 TODO
  承认更丰富的原因未上报，因为 `agent.chat()` 没返回结构化终止元数据。于是 ACP client 无法
  区分"撞迭代上限"与"正常结束"——这对自动化、以及要展示"turn 为何结束"的 UI 都有影响。
- **schema 漂移**：ACP v1 的 `StopReason` 枚举是
  `{end_turn, max_tokens, max_turn_requests, refusal, cancelled}`，而本地
  `AcpSessionPromptResponse.stopReason` 只允许
  `{end_turn, cancelled, max_turn_requests, refusal}`（`schema.py:270`）——**完全漏了
  `max_tokens`**。所以连 schema 也得扩，不只是填运行时。（注意本地 schema *已*允许
  `max_turn_requests`/`refusal`，只是运行时从不发出它们。）

### G4 —— mode / plan / commands 未映射为 ACP 更新

- **Modes**：`session/set_mode` 可用，但 `session/new` 不广告 `availableModes` /
  `currentModeId`，也不发 `current_mode_update` notification。`session_set_mode.py:15-19`
  明确把这点、以及 UI-mode 与权限轴的拆分，列为 deferred。
- **Plan**：ACP 有 `plan` 这种 `sessionUpdate`（带状态的条目，client 渲染成任务勾选清单）。
  agentao 既有 plan 模式**又**有 todo 工具，却都没映射到 `plan`——它们停留在内部。子 agent
  同样被拍平成 `agent_thought_chunk` 文本标记，而非结构化 `tool_call` 时间线
  （`transport.py:30-35,279-301`）。
- **Commands**：无 `available_commands_update`。agentao 丰富的斜杠命令是 CLI-only，从不向
  ACP client 广告。

### G5 —— 能力面较薄 *(多属需求门控)*

来自 `initialize.py`：
- `mcpCapabilities.http:false`、`sse:true`（`:75-78`）—— 只引了 `sse_client`；client 传入的
  streamable-HTTP MCP server 连不上。（与 `project_mcp_connect_preflight` 的 SSE-only 立场一致。）
- `promptCapabilities.audio:false`、`embeddedContext:false`（`:63-67`）—— 无 audio；
  内嵌 `resource` block 被拒（`session_prompt.py:199-203`）。
- `resource_link` 被保留为文本标签但**不解引用**（`session_prompt.py:135-147`）——解引用需要
  `fs/read_text_file` 往返，这又直接回到 **G1**。
- `loadSession:true` **已**实现——好。
- **v1 已确认、未实现**：`session/list`、`session/delete`、`session/close`、`session/resume`、
  `logout` **确实**定义在官方 ACP v1 schema 中（2026-06-18 核实）——它们不是"提案/较新"。agentao
  一个都没注册。仍属需求门控（当目标 client 用到会话管理 UI 时再建），但这是真实的一致性缺口，
  而非未知项。
- `authenticate` 是真实的 ACP 方法且未注册，但既然广告了 `authMethods:[]`
  （`initialize.py:91`），合规的 client 永远不会调用它。给个防御性的干净报错路径是*健壮性*的
  锦上添花，而非一致性差距。

### G6 —— 无上游 schema 一致性测试

`schema_export.py` 把 agentao 自己的 ACP Pydantic 模型导出为 JSON Schema（好基座），测试套件
也断言了*内部*的 event→update 映射——但**没有任何东西拿线路去对照上游 ACP schema 校验**，也
没有让 agentao 跑参考 client。对规范的漂移不会被发现。

---

## 4. 改进建议（按优先级）

每条都按嵌入式 harness 边界来框定：优先选 host 可注入、且不把编辑器假设烤进内核的改动。

**P0 —— 先决定目标 client 类别（它门控以下一切）。** 排定其余优先级的唯一决策：**编辑器级
client（Zed/Cursor）**是否真实目标，还是在途现实是 **chat/automation host（今天的 DeepChat）**？
- 若**编辑器级** → **G1 是 headline**：经 `session_new.py:93-95` seam 按 `client_capabilities`
  门控实现 ACP fs/terminal 代理工具（**先 fs 代理**——未保存缓冲区正确性 + diff 审阅；terminal
  次之），并与 `host-fs-policy.md` 调和。
- 若 **chat/automation** → **G1 属 demand-gated**；agentao 现有的本地 fs/本地 shell 行为已正确
  且合规。可选地写一行非目标声明，让它读起来是选择而非疏漏。在编辑器级 client 出现前不做代理。

以下各项**无论该决策如何都值得做**——它们 client 无关或 chat 相关，而非编辑器专属。

**P1 —— surface plan + modes + commands（G4）。** *(最靠前的 chat 相关项)* 把 plan 模式 / todo
工具映射为 ACP `plan` 更新；广告 `availableModes` 并发 `current_mode_update`；用
`available_commands_update` 广告斜杠命令；拆分 UI-mode 轴与权限轴（已记为 deferred 设计）。这些
正是 chat client 会渲染的东西，DeepChat 的 `set_mode`/`set_model` 工作已经指明了这个需求方向。

**P1 —— 结构化 `stopReason`（G3）。** 两步：(1) 给本地 `StopReason` 枚举（`schema.py:270`）
加 `max_tokens` 并重新生成 schema 快照；(2) 让终止元数据从 `agent.chat()` 透出（如
`max_iterations` → `max_turn_requests`），在 `session_prompt.py` 里映射。client 无关——对
automation 表面和任意 client UI 都重要。

**P1 —— `tool_call` 的 `diff` 内容（G2，diff 那一半）。** 对编辑类工具发 `diff`
（`oldText`/`newText`）——它在 chat client 里也能渲染，不只编辑器。先拓宽冻结契约
（`agentao/acp/schema.py`：给 `AcpToolCallContentEntry` 加 `diff`/`terminal` 变体、把 `kind`
枚举扩到 v1 的 9 个值），重新生成 `docs/schema/host.acp.v1.json`、更新快照测试，然后发送。
**`locations` 那一半是编辑器专属——随 G1 一起延后。**

**P2 —— streamable-HTTP MCP 传输（G5）。** 加 `streamable_http_client` 让
`mcpCapabilities.http:true`。需求门控：仅当目标 client 真在 `session/new` 传入 HTTP MCP server 时。

**P3 —— 上游一致性测试（G6）。** 把 `schema_export.py` 接到官方 ACP schema（或让 agentao 跑在
参考 ACP client 下）作为 CI 检查，使规范漂移被机械捕获。

---

## 5. 需求门控 / 明确"暂不做"

与 `path-a-roadmap.md` §2.3 及项目"需求门控"借鉴纪律一致（gap ≠ need）：
`session/list|delete|close|resume`、audio prompt、内嵌 resource 解引用，以及——**除非编辑器级
client 成为真实目标，否则 G1 的 fs/terminal 代理与 G2 的 `locations`**——都只在某个具体 client 会
消费时才值得建。不要仅以"规范完整性"为由去建。对在途的 chat host（DeepChat），*现在*该做的是
client 无关 / chat 相关的收益——**G4**（plan/modes/commands）、**G3**（stopReason）、**G2 的
`diff`**——它们都不需要编辑器。

---

## 6. 方法覆盖矩阵

`I` = 已实现，`—` = 未实现，`ext` = Agentao 扩展（不在 ACP v1）。
所有 v1 方法归属已于 2026-06-18 对照官方 schema 核实。

| ACP 方法 | 方向 | Agentao | 备注 |
|---|---|---|---|
| `initialize` | c→a | I | 版本协商 + `_meta` 扩展 |
| `authenticate` | c→a | — | `authMethods:[]` ⇒ 永不被调；OK |
| `logout` | c→a | — | v1 方法；未实现（无 auth ⇒ 低优先级） |
| `session/new` | c→a | I | + MCP 翻译、configOptions |
| `session/load` | c→a | I | 历史回放 |
| `session/prompt` | c→a | I | text/resource_link/image；`stopReason` 偏薄 + 漏 `max_tokens`（G3） |
| `session/cancel` | c→a | I | notification；容忍 request |
| `session/set_mode` | c→a | I | 无 `availableModes`/`current_mode_update`（G4） |
| `session/set_config_option` | c→a | I | host `provider_resolver` |
| `session/list`/`delete`/`close`/`resume` | c→a | — | **v1 方法，未实现**；需求门控（G5） |
| `session/set_model` | c→a | ext | **Agentao 扩展，不在 ACP v1** |
| `session/list_models` | c→a | ext | **Agentao 扩展，不在 ACP v1** |
| `_agentao.cn/set_model` | c→a | ext | 自由文本模型设定 |
| `session/update` | a→c | I | 映射丰富；无 `plan`/`diff`/`locations`（G2/G4） |
| `session/request_permission` | a→c | I | 阻塞式、并发安全 |
| `fs/read_text_file` | a→c | — | **G1** |
| `fs/write_text_file` | a→c | — | **G1** |
| `terminal/create` | a→c | — | **G1** |
| `terminal/output` | a→c | — | **G1** |
| `terminal/wait_for_exit` | a→c | — | **G1** |
| `terminal/kill` | a→c | — | **G1** |
| `terminal/release` | a→c | — | **G1** |
| `_agentao.cn/ask_user` | a→c | ext | 在 `initialize._meta` 声明 |

---

## 7. 结论

ACP server 凡已实现处都建得扎实，入站路径在构造上即安全。它的一致性上限**取决于目标 client
类别**。对在途的 chat host（DeepChat），agentao 现有行为已合规——G1 在那里不是 gap——*现在*该做
的是 client 无关 / chat 相关的收益：**G4**（plan/modes/commands）、**G3**（stopReason）、**G2 的
`diff`**，每项都是本地的 schema + 发送改动，不需协议往返。**G1**（驱动 client 的 fs/terminal）只
*在追求*编辑器级 client（Zed/Cursor）时才成为 headline。所以先定目标类别，优先级顺序随之而出。
其余一切都属需求门控，应等到真有 client 来要时再做。
