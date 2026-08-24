# 压缩编排收敛——实施计划

> **⚠️ 实施中 —— 2026-08-24。** 六个 PR 全部已实现并以栈的形式开出
> （PR-1 #187 → PR-3 #188 → PR-2 #189 → PR-4 #190 → PR-5 #191 → PR-6 #192），顺序即下方的依赖顺序。
> 被它替换掉的那条横幅写的是「已评审十二轮，**未获授权实施**——全文没有一行已经动工」；那句话已不再成立，
> 此处记录而非删除，因为另有七处文档引用它。
>
> **下面的 PR 顺序仍然是依赖顺序，不是排期。** 有两处有意的偏离，各自 PR 说明里都写明了：PR-3 一并落地了
> `_run_compaction` 的 `cancel` / host 摘要分支（留下一个不兑现 `cancel` 的 `decide` 参数，比 PR 大一点更糟），
> 以及 `COMPACTION_SETTLED` 的 replay 投影（不做的话 PR-3 只删审计记录、不给替代品）。

**日期：** 2026-08-23
**状态：** **实施中**（2026-08-24）——rev 14，十二轮维护者评审已**折叠进正文**（§9 是记录，不是需要叠加阅读的勘误层；正文自身即为准）。
**锚点：** agentao `main@a996395`，外加两处本计划已计入的未提交工作树改动：`COMPRESSION_THRESHOLD`
0.65 → 0.80（`agentao/context_manager.py:69`），以及 `/context` 的配色档位改为读常量
（`cli/commands/context.py:25-29`）。
**方法：** 下述每条前提均读源码核实，就地附 `file:line`。凡属推理而非实测的，文中明说。

**本文即是那份 `PRECOMPACT_GATE_PLAN.md`**——`docs/history/implementation/stop-precompact-hooks-plan.md:1081,1253,1398` 与
`codex-compaction-vs-agentao.zh.md:294` 三处点了名、但从未写出来的占位文档。§4.4 正面回答了那几处
记录在案的排除理由。文件没有叫 `PRECOMPACT_GATE_PLAN.md`，是因为本文范围大于那道 gate；7 处引用**均已改指本文**。
本文放在 `docs/design/` 而不是 `docs/history/implementation/`，因为后者是 2026-06-05 文档重组时对旧
`docs/implementation/` 的一次**冻结快照**——其中每个文件都由那一个提交带入，此后未再有新文件进入。
`docs/design/` 才是文档带着 **Status** 行走完整个生命周期的地方，因此本计划落地时不搬家，只改状态。

**上游分析：** `docs/design/pi-mono-compaction-vs-agentao.zh.md` §8（`PreCompact` 只读通知）与 §9.2
（熔断器无复位路径）是本计划要收口的两条 P1。

---

## TL;DR

压缩今天散在四个互不相关的地方编排——两级阈值、一条两级 API 溢出阶梯、以及手动 `/compact`。它们对
"触发来源叫什么"、"这次失败算不算"、"『压缩了』是否意味着历史真的变了"三件事各执一词。六个 PR 分两批
把它们收敛到一个 coordinator 后面：

```
5 个压缩入口
   ↓
CompactionCoordinator
   ├─ 熔断策略
   ├─ PreCompact / 宿主策略
   ├─ ContextManager 内容变换
   └─ 统一 CompactionOutcome + 事件
```

`ContextManager` 继续负责 token 估算、切点选择与摘要。coordinator 负责**是否执行、采用谁的摘要、
怎样恢复、发什么事件**。

**第一批——PR-1 → PR-3 → PR-2 → PR-4**，收口两条 P1。注意这**不是** PR 的编号顺序：PR-2 依赖 PR-1
（§3.3），且若先于 PR-3 落地会在 PR-3 里返工（§4.3）。

**第二批——PR-5、PR-6**，P2/P3 质量项；阈值改到 0.80 之后 PR-5 的优先级实质上升（§5.1）。

**不要移植 pi-mono 的会话树。** 整份计划都待在 agentao 的扁平消息列表里。

---

## 1. 目标架构

| 层 | 负责 | 不负责 |
|---|---|---|
| `ContextManager` | token 估算、`_threshold_token_estimate`、切点搜索、摘要提示词、工具结果裁剪 | 何时执行、谁可否决、发什么事件 |
| `CompactionCoordinator`（新增） | 触发来源、熔断**策略**、宿主策略派发、`CompactionOutcome` **契约**、事件 | 历史如何被改写；熔断**状态**（留在 `ContextManager`，见 §4.3） |
| 5 个入口 | 只检测**自己那一个**条件并调用 coordinator | 其余全部 |

coordinator 是一道接缝，不是重写——但它**不是零改动**。`microcompact_messages` 保留签名和函数体。
`compress_messages` 不能：它今天在一次调用里做完熔断短路（`context_manager.py:507`）、切点（`:526`）、
存活半边的微压缩（`:565`）、crystallize 落库（`:576-584`）、摘要（`:588`）、失败计数（`:590`）、
session summary 落库（`:598`）和消息组装（`:606-659`），而 PR-4 要在**摘要那一步**插进宿主的文本
（§4.4）。所以它拆成 `prepare_compaction()` / `commit_compaction()` 两半，接缝正好落在 `:588` 之前——
**形状由 PR-4 的需求定，但落地在 PR-3，所以它写在 §4.2.1。**
搬出去的是今天**长在它内部**的策略（`:507` 的熔断短路）和**散在它周围**的策略
（`runtime/chat_loop/_compaction.py:32,78` 两处退让闸门）。"搬出去"指的是**策略归属**：
`prepare_compaction` / `commit_compaction` 自身不做熔断判断，由 coordinator 决定；而
`compress_messages` 作为 legacy wrapper **保留一道等价闸门**，以免改掉它已被 docstring 和测试钉住的行
为（§4.3）。

---

## 2. 现状——5 个入口的实测矩阵

| # | 入口 | 检测 | PreCompact 派发 | 改写 | `compaction_type` | `reason` | `is_auto` |
|---|---|---|---|---|---|---|---|
| 1 | 微压缩 | `_compaction.py:30` | `:41` | `:48` `microcompact_messages` | `microcompact` | `microcompact_threshold` | 不适用 |
| 2 | 阈值全量 | `_compaction.py:76` | `:94` | `:102` `compress_messages` | `full` | `compression_threshold` | `True`（显式） |
| 3 | API 溢出第 1 级 | `_runner.py:1155` | `:1161` | `:1167` `compress_messages` | `full` | `api_overflow` | **`True`（走默认值，未传）** |
| 4 | API 溢出第 2 级 | `_runner.py:1195` | `:1199` | `:1204` `messages[-2:]` | `minimal_history` | `api_overflow_after_compression` | 不适用 |
| 5 | 手动 `/compact` | `compact.py:88` | `:94` | `:103` `compress_messages` | `full` | `manual_cli` | `False`（显式） |

从这张表直接落出三条事实，整份计划都建立在它们之上：

1. **`compaction_type` 与 `reason` 今天就带着** coordinator 需要的词表，而且**已经在 hook 载荷里**
   （`plugins/hooks/_payload.py:162-163`）。缺的不是它们，是另外两件事：`trigger` 对全部五个入口硬编码
   `"auto"`（`:160`），以及 PreCompact 的 matcher **只读 `trigger` 一个键**
   （`_dispatcher.py:206-235`）——所以这两个字段虽然送到了，却**不可被匹配**（§3.1、§4.1）。
2. **入口 3 没有传 `is_auto`。** `compress_messages(self, messages, is_auto: bool = True)`
   （`context_manager.py:480`）意味着在 `ContextManager` 边界上，溢出路径与阈值路径**完全无法区分**：
   同一道熔断闸门、同一个失败计数器。
3. **入口 1、2 会先退让再宣告，入口 3、4 不会。** `_compaction.py:32`（`microcompact_would_mutate`）
   与 `:78`（`compaction_circuit_open`）都在派发 hook、发事件**之前**早返回，注释写明了理由。而
   `_runner.py:1177` 的溢出路径紧跟 `compress_messages` **无条件**发 `CONTEXT_COMPRESSED`——熔断器打开时
   `compress_messages` 原样返回列表，事件照发，`pre_msgs == post_msgs`。

---

## 3. 已核实的前提

### 3.1 `trigger` 是一个**死的 matcher 取值**——比"字段不准"严重

`ClaudeHookPayloadAdapter.build_pre_compact` 对**全部五个**入口硬编码 `"trigger": "auto"`
（`plugins/hooks/_payload.py:160`）。而手动 `/compact` 发出的 `PLUGIN_HOOK_FIRED` 回放事件写的是
`"trigger": "manual"`（`cli/commands/compact.py:75`）——同一次压缩，事件流与 hook 载荷互相打架。

后果比"字段写错"大。hook 规则是按载荷字段做正则匹配的（`PluginHookDispatcher._matches`，
`plugins/hooks/_dispatcher.py:206`，经 `select_matching_rules` `:166-181` 进入），且有一条现存测试把
行为钉死：`test_manual_matcher_does_not_fire_on_auto_payload`
（`tests/test_hooks_pre_compact_matcher_trigger.py:35`）。

> **配了 `matcher: {"trigger": "manual"}` 的 hook，在任何入口、任何配置下都永远不会触发。**
> 这不是"载荷标错了"，而是一个**没有任何生产者能产出的配置取值**。

### 3.2 `trigger` 词表必须保持 `manual|auto`

第一反应是 `CompactionTrigger = manual | threshold | overflow`。**不要这么做。**
`tests/test_hooks_pre_compact_matcher_trigger.py:47`（`test_alternation_pattern_fires_claude_parity`）
把 `manual|auto` 钉为 Claude Code 兼容项。拆掉 `auto` 会让现存规则 `{"trigger": "manual|auto"}` 对阈值
压缩**停止匹配**——用户配置文件里的一次静默回归，而这正是本计划要消灭的那一类失败。

细粒度早就有归宿：`compaction_type` 带着 `microcompact | full | minimal_history`，`reason` 带着 §2 表里
那五个值。所以 PR-1 的实质改动是**一行——把触发来源透传下去**，两个新枚举大半只是在给已有字段命名。

### 3.3 熔断器已经修了一半，而且 PR-2 依赖 PR-1

`compress_messages` 有**两处**失败计数点，且它们今天就不一致：

- `context_manager.py:540`——找不到安全切点。**已经按 `is_auto` 门控。** `:535-539` 的注释写明理由：
  手动 `/compact` 是用户驱动、不会循环，而且"它会触发的熔断器会在本会话余下时间里禁掉*自动*压缩，且没有
  复位路径"。
- `context_manager.py:590`——摘要返回空。**无条件计数。**

所以 PR-2 想要的策略并非新发明，而是把 `:540` 的豁免推广到 `:590`，再加一条探针路径。短路本身在 `:507`，
位于任何 `is_auto` 分支**之上**，这正是手动 `/compact` 也被拦的原因。复位在 `:593`，熔断器一旦打开就到
不了那一行。

`/clear` **不**复位：`cli/commands/reset.py:35` 调 `agent.clear_history()`（`agent.py:1155-1163`），清的是
消息、技能、todo、token 锚点和 token 计数器——不含 `_consecutive_compact_failures`。

**因此 PR-2 依赖 PR-1。** 要让溢出成为紧急探针，coordinator 必须知道它*就是*溢出，而今天它做不到
（§2 事实 2）。

### 3.4 不要在摘要外面再包一层重试

摘要调用的是 `self.llm_client.chat(...)`（`context_manager.py:859`），因此天然继承客户端的重试循环
（`llm/client.py:451`）：`MAX_RETRY_ATTEMPTS = 5`（含首次）、`MAX_TOTAL_RETRY_SECONDS = 60.0` 墙钟预算
（`llm/_retry.py:27,30`）。再套一层是相乘，不是相加。

### 3.5 PreToolUse 决策路径就是 PR-4 的先例

`PluginHookDispatcher.dispatch_pre_tool_use_decision`（`plugins/hooks/_dispatcher.py:90-117`）今天就实现了
PR-4 需要的形状：解析每个 hook 的 **stdout** 里的 `hookSpecificOutput.permissionDecision`（`:353-358`），
按两级合并——**first-deny-wins，其次 first-ask-wins**（`:102-104`）——见到 deny 即停止继续 fork，并且
**刻意不认 exit-code 2**（docstring `:104-105`）。

这一点决定了 PR-4 的放量方式。因为决策走 stdout 里的 JSON 字段，legacy observe-only 脚本什么都不打印 =
静默 `allow`。**所以原计划提的"hook v2 / 显式配置"闸门是不需要的**——前提是 cancel 只认 JSON 形状。
只有当 cancel 认 exit code 时才需要那道闸门，而那恰是先例已经否决过的做法。

**但"沉默即 allow"只证明了一半。** 它证明**什么都不打印**的脚本安全，证明不了某个私有脚本出于别的目的
写出的 `hookSpecificOutput` 安全。§4.4.1 因此不复用 `permissionDecision`，改用一个从未存在过的专用键
`compactionDecision`——那才是"不需要闸门"的完整理由。另外先例只覆盖了 PR-4 需要的**一半**：
`dispatch_pre_compact` 今天是纯 side-effect 的 `_dispatch_lifecycle`（`:158-164`），根本不解析 stdout，
所以那个解析路径要新写一份（§4.4.1）。

---

## 4. 第一批——两条 P1

顺序是 **PR-1 → PR-3 → PR-2 → PR-4**。编号保留原始标号以便追溯到评审对话；要按"次序"列施工。

| 次序 | PR | 内容 | 验收 |
|---|---|---|---|
| 1 | PR-1 | 修正现有 PreCompact 契约 | 五个入口产出的载荷 `trigger` 与事实一致；`{"trigger": "manual"}` 规则在 `/compact` 上触发、且只在它上面触发 |
| 2 | PR-3 | 统一结果与事件，**建立 `CompactionCoordinator` 并落地它依赖的机械底座** | 五个入口全部经 coordinator 返回同一个 `CompactionOutcome`；只有 `status == "success"` 才发 `CONTEXT_COMPRESSED`；`compress_messages` 的 legacy 语义除 §4.3 点名的那一处外不变 |
| 3 | PR-2 | 熔断器改为可恢复 | 三次失败后阈值尝试暂停；手动与溢出可作探针；探针成功即复位 |
| 4 | PR-4 | 宿主控制面（**只在已接通的路径上启用**） | 支持取消与提供摘要；不接受任意消息列表；**不迁移任何入口** |

**coordinator 由 PR-3 建立并接通，机械底座也全在 PR-3——这一条此前无人认领。** PR-1 只修载荷，不引入新
对象；而 PR-3 要求"五个入口返回同一个 `CompactionOutcome`"，它们就必须经过同一条路径。这条要求把一整
套东西拉进 PR-3，因为**少了其中任何一样，PR-3 都满足不了自己的验收**：`full` 路径权威的 `status` 只有
`_run_compaction` 给得出，而 §6 明令禁止从消息身份或条数反推。所以 PR-3 的范围是：

1. 中立类型模块 `agentao/compaction/types.py` 与 `coordinator.py`（§4.2.1 末）。
2. `compress_messages` 拆成 `prepare_compaction` / `commit_compaction`，外加私有的
   `_run_compaction(..., decide=None)`（§4.2.1）——**不含** `decide` 真正被传入的那条路径。
3. legacy wrapper 的 `reason` 映射（`is_auto=True` → `compression_threshold`，`is_auto=False` →
   `manual_cli`），以及 `apply_minimal_history`（§4.2.1、§4.4.3）。
4. 五个入口**原子改接**到 coordinator，含入口 3 交出真实的 `api_overflow`。
5. `CompactionOutcome`、`COMPACTION_SETTLED`、以及 `_compaction.py:32,78` 两处退让闸门的搬入。

PR-4 因此**不再迁移任何入口**：它只在已经接通的路径上启用 `decide`——命令型 hook 的决策协议、
`compaction_controller=`、`provide_summary`、取消语义与抑制闩。PR-2 往里搬熔断策略，PR-5 / PR-6 不碰它。

**代价要说清楚：PR-3 因此是一个大 PR，而且它会独自承担 §4.3 点名的第二处行为变化**（摘要失败时不再
crystallize）。换来的是每个 PR 都能独立验收——把拆分留到 PR-4，PR-3 就只能拿条数或消息身份糊一个
`status`，而那正是本计划要修的缺陷。

### 4.1 PR-1——先修触发来源契约

- `build_pre_compact(..., trigger: str, custom_instructions: str = "")` 显式接收来源，取代硬编码的
  `"auto"`（`_payload.py:160`）。
- **词表——`trigger` 保持 `manual | auto`**（§3.2）。入口 1–4 传 `auto`，入口 5 传 `manual`。
- 已有的两个字段是**加类型**，不是被替换：
  - `CompactionKind = microcompact | full | minimal_history`（今天的 `compaction_type`）
  - `CompactionReason = microcompact_threshold | compression_threshold | api_overflow | api_overflow_after_compression | manual_cli`（今天的 `reason`）
- **加类型不等于可匹配。** `_matches` 对 PreCompact 只比对 `trigger` 一个键
  （`_dispatcher.py:206-235`），所以 `CompactionKind` / `CompactionReason` 落地后，宿主**仍然无法**按它
  们配 matcher。本 PR **不**扩 `_matches`：PreCompact 的 matcher 在 Claude Code 那边就是 trigger-only，
  扩了就是 agentao 独有扩展，要单独立项、单独写文档。这条后果记在案，不是遗漏。
- 覆盖 §2 表里全部五个派发点，外加 `_hook_dispatch.py:200` 与 `cli/commands/compact.py:75` 两处
  `PLUGIN_HOOK_FIRED` 的发射，让事件与载荷说同一件事。
- 中英文插件文档与 matcher 契约**一起**更新——它们是孪生，会漂。

**必须显式测的回归：** 现存规则 `{"trigger": "manual|auto"}` 在改动后必须在每个入口上继续触发。

### 4.2 PR-3——一个结果，诚实的事件

```python
@dataclass(frozen=True)
class CompactionOutcome:
    status: Literal["success", "cancelled", "failed", "skipped"]
    trigger: CompactionTrigger
    kind: CompactionKind
    reason: CompactionReason
    messages: list[dict]
    pre_tokens: int | None
    post_tokens: int | None
    detail: str | None
```

`pre_tokens` 写成 `| None`，但**上一版给的理由是错的**：full 路径今天**就在算**它——
`pre_tokens = self.estimate_tokens(messages)` 在 `context_manager.py:587`，`compress_messages` 内部，
入口 3 走的也是这条。真正的两条理由：

1. **`minimal_history`（入口 4）一个 token 都不估。** `_runner.py:1195-1210` 那条分支没有任何
   `estimate_tokens` 调用。要它填 `pre_tokens`，才是真的新增一次全历史估算——而且是在上下文已经爆掉、
   请求刚被拒过两次的地方。
2. **今天存在两个口径不同的 `pre_tokens`。** `context_manager.py:587` 算的是 `messages`（**不含系统提
   示**）；`_compaction.py:46,100` 算的是 `messages_with_system`（**含**）。一个必填的 `int` 会把两个单
   位悄悄混成一个字段。

定死：**`CompactionOutcome.pre_tokens` 与 `CompactionDecisionContext.pre_tokens` 均为 `int | None`，口
径统一为"不含系统提示"**，与 `prepare` 从 `:587` 拿到的那个值同源。`_emit_context_compressed` 今天的签
名本来就是 `pre_tokens: Optional[int] = None`（`agent.py:1118`），所以这不引入新形状。

这消掉两处猜测：

- 手动 `/compact` 今天靠嗅探 `messages[0]` 上是否新加了 `[Compact Boundary]` 标记来推断成功
  （`cli/commands/compact.py:26-40`）——这个启发式之所以存在，只是因为 `compress_messages` 返回一个裸列表。
- 溢出路径无条件发成功事件（`_runner.py:1177`），包括熔断器让 `compress_messages` 变成 no-op 的时候。

**这不是新工作——是把一个做了一半的修复补完。** PR #181 给*阈值*路径做的正是这件事；
`_compaction.py:32,78` 的退让注释描述的就是同一个缺陷（"宣告这次压缩会为一件从不发生的事每轮 fork 一个
PreCompact 子进程——并发一条 pre == post 的 `CONTEXT_COMPRESSED`"）。溢出路径被漏掉了。

**status 映射表——PR-3 之前必须定死，它是公开可观察契约。**

| 情形 | 今天的行为 | `status` | 计入熔断 |
|---|---|---|---|
| 熔断器已开（`:507`） | 原样返回 | `skipped` | 否 |
| `len(messages) < 5`（`:517`） | 原样返回 | `skipped` | 否 |
| 微压缩无可缩目标（`microcompact_would_mutate` 为假） | 退让（`_compaction.py:32`） | `skipped` | 否 |
| 宿主/hook 取消 | 新增 | `cancelled` | 否 |
| 找不到安全切点（`:528`） | 原样返回 + 计数（已按 `is_auto` 门控，`:540`） | `failed` | 按 PR-2 规则 |
| 摘要返回空（`:589`） | 原样返回 + **无条件**计数（`:590`） | `failed` | 按 PR-2 规则 |
| 宿主摘要非法且内建也失败 | 新增 | `failed` | 按 PR-2 规则（§4.2.1） |
| 正常完成 | 返回新列表 | `success` | 复位（`:593`） |
| 命中抑制闩（§4.4.4） | 新增 | `skipped` | 否 |

判据一句话：**`skipped` = 什么都没试；`failed` = 试了没成；`cancelled` = 被否决。** `skipped` 永不计入
熔断器——这正是今天 `:507` 与 `:517` 的行为，不是新规则。

**`skipped` 是静默的：不发任何事件。** 上表里 `skipped` 共**四种**（熔断器已开、`len < 5`、微压缩无目
标、命中抑制闩），其中**三种**（熔断器已开、微压缩无目标、命中抑制闩）会在**每个 iteration** 重新命
中，逐条发事件就是新的事件风暴——正是 `_compaction.py:32,78` 两处退让
注释要防的东西，而它们今天退让时同样什么都不发。所以终态事件只在 `success | cancelled | failed` 三态
发。

**终态事件——名字与 payload 也一并定死。** 新增 `EventType.COMPACTION_SETTLED = "compaction_settled"`
（加在 `transport/events.py:36` 的 `CONTEXT_COMPRESSED` 旁边）。payload：

```json
{"trigger": "manual|auto", "kind": "microcompact|full|minimal_history",
 "reason": "...", "status": "success|cancelled|failed",
 "pre_msgs": 0, "post_msgs": 0,
 "pre_tokens_history": null, "post_tokens_history": null,
 "duration_ms": 0, "detail": null}
```

**token 字段特意叫 `*_tokens_history`，因为它和旧事件不是同一个口径。** Outcome 里的两个 token 都**不含
系统提示**：`pre_tokens` 与 `context_manager.py:587` 同源，`post_tokens` 与 `:641`
（`post_tokens = self.estimate_tokens(result)`）同源。而旧事件的
`pre_est_tokens` / `post_est_tokens` 算的是 `messages_with_system`，**含**系统提示
（`_compaction.py:46,64`、`:100,121`；`cli/commands/compact.py:98-100,116`）。用两组不同的名字，是为了
让这两个口径**不可能**被误接到一起。

**逐路径的"有值 / 为空"契约——两张表，都要在 PR-3 里钉住。** 统领原则一句话：**本计划不新增任何一次
`estimate_tokens` 调用**，所以两个字段只在 history-only 估算**今天就已经存在**的地方有值。

旧事件 `CONTEXT_COMPRESSED`（含系统提示，**PR-3 前后一个都不改**）：

| 入口 | `pre_est_tokens` | `post_est_tokens` |
|---|---|---|
| 1 微压缩 | `_compaction.py:46` | `:64` |
| 2 阈值全量 | `:100` | `:121` |
| 3 API 溢出第 1 级 | **`null`**——今天就不传（`_runner.py:1177`） | **`null`** |
| 4 API 溢出第 2 级 | **`null`**（`_runner.py:1208-1213`） | **`null`** |
| 5 手动 `/compact` | `cli/commands/compact.py:98-100` | `:116` |

入口 3 / 4 **继续保持 `null`**，不借这次改动"顺手补上"——补上就要新增两次全历史估算，而且正好在上下文已
经爆掉的路径上（§4.2 开头那两条理由）。

新事件 `COMPACTION_SETTLED`（不含系统提示）：

| `kind` × `status` | `pre_tokens_history` | `post_tokens_history` |
|---|---|---|
| `full` / `success` | `:587` | `:641` |
| `full` / `cancelled` | `:587`（prepare 已经算过） | `null`——历史没变，没有"post" |
| `full` / `failed`（摘要为空 / 宿主摘要非法且内建也失败） | `:587` | `null` |
| `full` / `failed`（`no_safe_split`，`:528`） | `null`——`:587` 在它之后才执行 | `null` |
| `microcompact` / 任意 | `null` | `null` |
| `minimal_history` / 任意 | `null` | `null` |
| 任意 / `skipped` | — | —（不发事件） |

**微压缩两列全 `null` 是刻意的。** 它在 55–80% 带里**每个 iteration 都跑**，为它补两次全历史 history-only
估算，正是 `_compaction.py:50-53` 那段注释花力气避免掉的开销（"a full re-encode of the entire history on
every iteration spent in the microcompact band — precisely when it is most expensive"）。要微压缩的 token
就读旧事件，用它含系统提示的那个口径。
**兼容——"超集"那句话是错的，已撤。** 旧事件的键是
`type` / `reason` / `pre_msgs` / `post_msgs` / `pre_est_tokens` / `post_est_tokens` / `duration_ms`
（`replay/observability.py:47-55`）：三个键与新事件**不同名**。映射如下，实现时逐行照着接：

| `CONTEXT_COMPRESSED`（不动） | `COMPACTION_SETTLED`（新） | 口径 |
|---|---|---|
| `type` | `kind` | 同值，改名 |
| `reason` | `reason` | 同 |
| `pre_msgs` / `post_msgs` | 同名 | 同 |
| `pre_est_tokens` / `post_est_tokens` | `pre_tokens_history` / `post_tokens_history` | **不同口径**：旧含系统提示，新不含 |
| `duration_ms` | 同名 | 同 |
| —（无） | `trigger` / `status` / `detail` | 新增 |

**`CONTEXT_COMPRESSED` 的 payload 一个键都不改，两个 token 字段继续走含系统提示的口径。** 绝不从
Outcome 的 history-only 数字里取——那会悄悄改掉一个公开字段的语义，而 `transport/events.py:57-58` 正是
为这种情况要求 bump `schema_version` 的。

`schema_version` 因此**不动**，两条理由分开成立：`CONTEXT_COMPRESSED` 的字段形状与语义一个都没变；
`COMPACTION_SETTLED` 是一个**新类型**，而"加不算变更"是既定规则（`transport/events.py:59-60`）。

**但有一件事确实变了，要在 PR 描述与 CHANGELOG 里点名：`CONTEXT_COMPRESSED` 的发射条件。** 今天它在熔
断器让压缩变成 no-op 时照发（`_runner.py:1177`），PR-3 之后只在 `success` 时发。这是把一个说谎的事件改
成不说谎，不是 payload 契约变更——但它是可观察行为，不能不说。

两个事件都只记数量、token、耗时和失败分类——绝不记原始上下文。

#### 4.2.1 接缝——`compress_messages` 拆成 prepare / commit

> **这道接缝属于 PR-3，所以它排在这里，不在 §4.4 下面。** 它的**形状**由宿主控制面的需求决定——摘要那
> 一步必须可被替换（§4.4）——但**落地时机**由 PR-3 的验收决定：PR-3 要"五个入口经 coordinator 返回同一
> 个 `CompactionOutcome`"，而 `full` 路径权威的 `status` 只有 `_run_compaction` 给得出，§6 又明令禁止从
> 消息身份或条数反推。**本节里只有这三项归 PR-4**：`decide` 真正被传入的那条路径、`cancel` 与两行"宿主
> 摘要"分支、`detail` 的拼接规则。完整分工见 §4 总表下方那段。

宿主的摘要要插进摘要那一步，所以 `compress_messages`（`context_manager.py:477-659`）必须拆开。接缝落在
第 `:588` 行（`summary = self._summarize_messages(to_summarize)`）之前：

| 阶段 | 内容 | 允许的副作用 |
|---|---|---|
| `prepare_compaction(messages, *, trigger, kind, reason) -> PrepareResult` | `len < 5` 守卫（`:517`）、切点（`:526`）、存活半边微压缩（`:565`）、pinned 抽取（`:568`）、最近读文件抽取（`:574`）、摘要输入组装（`_format_for_summary`） | 仅 `last_microcompact_mutated`（`:408`）+ 一行日志（`:411-413`）；**不写 SQLite、不碰 `agent.messages`** |
| controller / hook 决策 | `allow` / `cancel` / `provide_summary(text)` | 零 |
| summarize | `allow` 走 `_summarize_messages`（`:588`）；`provide_summary` **跳过这次 LLM 调用** | 一次 LLM 请求 |
| `commit_compaction(prep, summary) -> list[dict]` | crystallize 落库（`:576-584`）、session summary 落库（`:598`）、消息组装（`:606-659`） | SQLite 写 + 历史改写 |
| coordinator | 熔断**策略**（放行 / 暂停 / 探针）、hook 派发、构造 `decide`、构造闸门 / `microcompact` / `minimal_history` 三类结果、发全部事件、写抑制闩 | 事件 + 闩 |
| `_run_compaction`（`ContextManager` 私有，两个调用方共享） | 串起 prepare → decide → summarize → commit，并在这一层做**三个计数点** | 计数器 |

**prepare 必须能表达"没做成"，所以它返回联合类型。** 上一版让它返回 `PreparedCompaction`，但表里那两个
提前终止（`len < 5` 的 `:517`、找不到安全切点的 `:528`）根本没有 `PreparedCompaction` 可返回：

```python
@dataclass(frozen=True)
class PrepareRejected:
    status: Literal["skipped", "failed"]
    detail: str                # "history_too_short" | "no_safe_split"
    counts_as_failure: bool    # 仅 no_safe_split 为 True

PrepareResult = PreparedCompaction | PrepareRejected
```

- `len < 5`（`:517`）→ `PrepareRejected("skipped", "history_too_short", False)`
- 找不到安全切点（`:528`）→ `PrepareRejected("failed", "no_safe_split", True)`

**三个计数点必须落在同一个函数里——但那个函数不是 coordinator，也不是 commit。** 上一版把"失败计数与
复位"写进 commit 行，那是个洞：**摘要返回空时 commit 根本不会跑**（`:589` 就 return 了），今天 `:590`
那次计数会凭空消失。但把它们搬到 coordinator 同样不成立——`ContextManager.compress_messages()` 是一个可
以被独立调用的 legacy 方法，它手上**没有 coordinator**，而计数器 `_consecutive_compact_failures`
（`:91`）按 §4.3 的定案本来就留在 `ContextManager` 上。

落法：新增一个 **`ContextManager` 私有方法** `_run_compaction`，两个调用方共享：

```python
class ContextManager:
    def _run_compaction(
        self, messages, *, is_auto: bool, reason: CompactionReason,
        decide: Callable[[CompactionDecisionContext], CompactionDecision] | None = None,
    ) -> CompactionOutcome: ...
```

**`trigger` 与 `kind` 不用传——它们是导出的，不是输入。** `trigger` 的词表就是 `manual | auto`
（§3.2），而 `is_auto` 恰好是同一件事的另一种编码：今天三个调用点里，`_compaction.py:102` 与
`_runner.py:1167` 走 `True`（→ `auto`），`cli/commands/compact.py:103` 走 `False`（→ `manual`），一一
对应。所以 **`trigger = "auto" if is_auto else "manual"`**，`kind` 恒为 `full`（`_run_compaction` 只处
理这一种，见本节末）。`is_auto` 之所以仍留在签名里而不换成 `trigger`，是因为 §4.3 钉死了 legacy
`compress_messages(messages, is_auto=...)` 的签名，两个调用方共用这一层。

**因此它直接返回 `CompactionOutcome`，中间类型 `_CompactionRun` 已删。** 上一版为它单设一个中间类型，
理由是"Outcome 还要带 `trigger` / `kind` / `reason`，而 `_run_compaction` 不知道触发来源"——那句话不成
立：`reason` 本来就是入参，`trigger` 与 `kind` 如上导出，八个字段它全知道。而它一旦知道全部，多一层类型
就只剩搬运成本——**那层搬运在 rev 7 / 8 / 9 连续三轮各制造了一个缺陷**（字段映射写错、`detail` 所有权
错、`counted_failure` 越权），删掉它比继续维护映射表划算。`pre_msgs` / `post_msgs` 不受影响：它们本来
就不是 `CompactionOutcome` 的字段，只进事件 payload。

它串起 `prepare → (decide) → summarize → commit`，并在**这一层**做三件事：

1. `PrepareRejected.counts_as_failure` 为真 → 计数（按 PR-2 的 `is_auto` / trigger 门控）。
2. 摘要返回空 → 计数（同一门控）。
3. commit 成功 → 复位（`:593`）。

三个点都在 `_run_compaction` 里，所以既不会被 commit 的提前 return 漏掉，也不需要 coordinator 在场。

**逐分支映射。** `_run_compaction` 必须把"成没成"直说，而不能让上层回去猜消息身份或条数——那正是 §6
已经否决的判据。`CompactionOutcome` 八个字段里，`trigger` / `kind` / `reason` 是常量或入参（见上），
下表只列随分支变化的那四个，外加它内部的计数行为：

| 分支 | `status` | `messages` | `pre_tokens` | `post_tokens` | `detail` | 计数器 |
|---|---|---|---|---|---|---|
| `PrepareRejected("skipped", "history_too_short")`（`:517`） | `skipped` | 原对象 | `None` | `None` | `history_too_short` | 不动 |
| `PrepareRejected("failed", "no_safe_split")`（`:528`） | `failed` | 原对象 | `None` | `None` | `no_safe_split` | 按门控 +1 |
| `decide` 返回 `cancel` | `cancelled` | 原对象 | `:587` | `None` | **无内部原因**（`None`，见下） | 不动 |
| 摘要返回空（`:589`） | `failed` | 原对象 | `:587` | `None` | `summary_empty` | 按门控 +1 |
| 宿主摘要非法 → 内建成功（§4.2.1） | `success` | 新列表 | `:587` | `:641` | `host_summary_rejected:<校验项>` | 复位 |
| 宿主摘要非法 → 内建也失败 | `failed` | 原对象 | `:587` | `None` | `host_summary_rejected:<校验项>+summary_empty` | 按门控 +1 |
| 宿主摘要合法 → 采用 | `success` | 新列表 | `:587` | `:641` | `None` | 复位（`:593`） |
| 正常完成（内建摘要） | `success` | 新列表 | `:587` | `:641` | `None` | 复位（`:593`） |

表中 `detail` 列写的是**内部原因**；`_run_compaction` 在**返回之前**就把它与决策的 `reason` 拼成终值。
规则：内部原因 + `; ` + 决策 `reason`（§4.4.2），任一为空就只留另一个，两者都无 → `None`。这样上面两行
"合法宿主摘要"与"内建摘要"在有 `reason` 时就区分得开，而 `CompactionDecision.reason` 声明的"进
`CompactionOutcome.detail` 与日志"也真正兑现。

**但"决策 `reason`"在两条路径上根本不存在，所以拼接规则不能写成 `decision.reason`。** 一是 `decide` 可
以为 `None`——legacy wrapper 传的就是 `None`（见本节末），压根没有决策对象；二是上表**前两行**的
`PrepareRejected` 在 prepare 阶段就返回了，**还没走到决策那一步**（四阶段表的顺序是 prepare → decide →
summarize → commit）。落法：`decision_reason: str | None = None` 起手，**只有 `decide` 真的被调用、且返
回了一个可用决策时才赋值**；于是那两行的 `detail` 就等于内部原因本身，拼接规则不必为它们开例外。

**`cancel` 那行的内部原因是 `None`，这不是笔误。** 上一版把它写成"hook / controller 的 `reason`"——那就
是 `decision.reason` 本身，再过一遍拼接规则会变成 `reason; reason`。把内部原因留空，规则自然产出
`decision.reason` 单独一份，`cancel` 也就不需要成为拼接规则的例外。

**拼接必须在 `_run_compaction` 里做，不能留给 coordinator。** 唯一持有 `CompactionDecision` **实例**的
就是它——coordinator 交出去的是一个 `decide` **可调用对象**，真正的调用发生在 prepare 之后、summarize
之前（上面的四阶段表），所以 coordinator 手上从来没有那个返回值。上一版写"coordinator 用 `; ` 拼在内部
原因之后"，与紧接着的"`detail` 直接搬进 `CompactionOutcome`"自相矛盾。现在 `detail` 一出
`_run_compaction` 就是终值，"直接搬"这句话才字面成立。

顺带把 `decide` 的内涵说死：它是 coordinator 合成的闭包，**两层控制面都在它里面**——先派发命令型 hook
（§4.4.1，层内 first-cancel-wins），全部 `allow` 再调 `compaction_controller`（§4.4.2），合并后返回
**一个** `CompactionDecision`。所以上表 `cancel` 那行按拼接规则产出的 `detail`，就是这个合并结果的
`reason`。

表里"计数器"那一列描述的是 `_run_compaction` **内部**的行为，不是它返回的字段——见下。

`_run_compaction` **不会**为熔断器已开或命中抑制闩返回 `skipped`——那两种在它**之上**就被拦下了，根本不
会调到它。它唯一会产出的 `skipped` 是 `history_too_short`。

`_run_compaction` 直接返回 `CompactionOutcome` 本身，**没有中间类型、没有字段搬运**（上一版的
`_CompactionRun` 已删，理由见上）。所以这里只剩三件曾经写错、需要留档的事：

- **`counted_failure` 这个字段已经不存在了。** rev 8 加了它，还说 coordinator 据它决定"这次探针
  算不算数、要不要改 `/context` 的熔断展示"——那等于把熔断状态的所有权又拿回 coordinator 手上，与 §4.3
  的"唯一真相源在 `ContextManager`"、以及本节末尾的"coordinator 不碰计数器"两句**同时**打架。而它本来
  就是多余的：计数与复位已经在 `_run_compaction` 里做完了，coordinator 要执行探针策略，读 `status` 与
  `compaction_circuit_open`（`:423`）就够；`/context` 的展示一直走
  `get_usage_stats()['circuit_breaker_failures']`（`:1223`），从不经过任何返回值。
- **`trigger` / `kind` / `reason` 不需要 coordinator 补**——`reason` 是入参，`trigger` 由 `is_auto` 导出，`kind` 恒为 `full`（见上）。上一版写"由 coordinator 补、`_run_compaction` 不知道触发来源"是错的，已撤。
- **`pre_msgs` / `post_msgs` 根本不是 `CompactionOutcome` 的字段**（见 §4.2 的定义），它们只进**事件
  payload**（`CONTEXT_COMPRESSED` 与 `COMPACTION_SETTLED` 都有这两个键），由 coordinator 用两次 `len()`
  算出，不含任何 token 估算。

**`_run_compaction` 只处理 `kind == full`。** 另外两个 kind 不走它，理由是它们没有一样东西需要共享：微
压缩和 `minimal_history` **都不调摘要器、不写 SQLite、不碰熔断计数器**。把它们塞进同一个函数，只会让每
个字段变成可选，却买不到任何复用。因此 `PrepareResult` 保持 `PreparedCompaction | PrepareRejected` 两支
即可。

**但"不走 `_run_compaction`"并不等于"由 coordinator 自己改历史"——上一版这么写，越过了 §1 的分层边
界。** §1 的表把"历史如何被改写"明确列在 coordinator 的**不负责**一栏；而上一版让它自己产出
`PreparedMicrocompact` / `PreparedMinimalHistory` 并执行短变换，微压缩还要去读私有的
`_microcompactable_indices`（`:348`）。这里**不修订 §1**——那条分层是整份计划的地基，为两个两行变换破
例不值当。改法是给 `ContextManager` 加两对窄方法，coordinator 只编排：

```python
class ContextManager:
    # kind == microcompact
    def prepare_microcompact(self, messages) -> PreparedMicrocompact: ...
    #   apply 那一半已经存在：microcompact_messages(messages)（`:377`），签名与函数体不动
    # kind == minimal_history
    def prepare_minimal_history(self, messages, *, keep_tail: int = 2) -> PreparedMinimalHistory: ...
    def apply_minimal_history(self, messages, *, keep_tail: int = 2) -> list[dict]: ...
```

- `prepare_microcompact` 把 `_microcompactable_indices`（`:348`）包在里面，**coordinator 不再碰任何私有
  成员**；它只返回 `tool_results_to_clip = len(targets)` 与 `pre_tokens = None`。既有的公开谓词
  `microcompact_would_mutate`（`:365`）保留——它是 `_compaction.py:32` 那道便宜的前置检查，而
  `prepare_microcompact` 用 `tool_results_to_clip > 0` 表达同一件事。
- `apply_minimal_history` 把今天写在 `_runner.py:1204` 的 `messages[-2:]` 挪到 `ContextManager` 后面。
  它只有一行，但**内容变换按 §1 就该住在那里**，而且这样溢出阶梯的最后一级才有一个具名、可单测的接缝。
  **这一对里 `apply_minimal_history` 归 PR-3**（入口 4 一改接就需要它），两个 `prepare_*` 归 PR-4（它们
  只为拼 `CompactionDecisionContext` 而存在，PR-3 阶段没有读者）。

三个 kind 因此共享的是 **`CompactionOutcome` 契约**与**决策步骤**——**不是同一个构造者**（见下）；历史
改写一律在 `ContextManager` 里发生。

**依赖方向定死：`ContextManager` 不 import、不持有、不知道 `CompactionCoordinator` 的存在。** 控制面是
一个可选的 `decide` 回调注入进来的，仅此而已。coordinator 在 `ContextManager` **之上**：它决定熔断策略
（放行 / 暂停 / 探针）、派发 hook、把 hook 与 controller 的结果合成一个 `decide`、构造**它自己那三类结
果**（闸门短路 / `microcompact` / `minimal_history`，见下）、发**全部**事件、写抑制闩——但它**不碰计数
器**。

**所以共享类型必须住在中立模块里，这条是上面那句依赖方向的前置。** `_run_compaction` 要
`return CompactionOutcome(...)`，`decide` 的入参出参又是 `CompactionDecisionContext` /
`CompactionDecision`；这几个类型只要定义在 coordinator 模块里，`ContextManager` 就必须 import 它，依赖
方向当场作废。落法：新建 `agentao/compaction/` 包——`types.py` 只装类型（三个词表别名、
`CompactionOutcome`、`CompactionDecisionContext`、`CompactionDecision`、`CompactionController`），**只
import 标准库**；`coordinator.py` 装 `CompactionCoordinator`。`context_manager.py` 与 `coordinator.py`
都从 `types.py` 取，两条边都朝下。两条附带约束：

- **`agentao/compaction/__init__.py` 保持不 re-export `coordinator`。** 公开子集（`compaction_controller=`
  是公开构造参数，`Agentao.compact()` 的返回值是 `CompactionOutcome`）要从 `agentao.host` 再导出一份；
  而 `agentao.host` 若沿着 `__init__` 拖到 `coordinator` → `context_manager` → LLM 栈，就会撞上导入分层
  第 5 条（`tests/test_import_layering.py:471`，"`import agentao.host` 不得拖进 runtime / LLM 栈"）。
- **`PreparedCompaction` / `PrepareRejected` / `PreparedMicrocompact` / `PreparedMinimalHistory` 不进
  `types.py`。** 它们是 prepare → commit 的私有快照（本节已标"**私有**"），留在 `context_manager.py`
  旁边；coordinator 只经窄方法拿它们，不需要类型本身出现在公开面上。

于是两个调用方各就各位：

- `compress_messages(messages, is_auto=True)` = 熔断闸门 + `_run_compaction(..., decide=None)`，返回
  `outcome.messages`。**除 §4.3 点名的那两项有意变化（手动路径失败计数、摘要失败时的 crystallize 时
  机）之外，与今天一致。** 它自己没有 `reason` 参数——§4.3 钉死了旧签名——所以 wrapper 按 `is_auto` 定死
  映射：**`is_auto=True` → `compression_threshold`，`is_auto=False` → `manual_cli`**。
- `CompactionCoordinator` = 策略 + 可观测性 + `_run_compaction(..., decide=<合成的决策函数>)`。

**这条 legacy 映射只服务树外调用方——因此入口 3 的改接必须与它在 PR-3 里原子完成。** 入口 3
（`_runner.py:1167`）今天走的正是 wrapper 的默认值 `is_auto=True`（§2 表第 3 行），可它真实的 `reason`
是 `api_overflow`——两行之上那次 hook 派发（`_runner.py:1161`）就是这么写的。若 `reason` 变成必填而入口
3 还留在 wrapper 上，它会自报 `compression_threshold`，跟自己刚发出去的 hook 载荷对不上。而 PR-3 的验收
本来就是"五个入口全部经 coordinator"，所以这不是一条额外条件，只是把"原子"两个字说明白：**同一个 PR 里
既引入必填 `reason`，也把入口 2 / 3 / 5 全部改成经 coordinator 传真实 `reason`**。此后还会触发这条映射
的，只剩树外直接调 `compress_messages()` 的嵌入方，而它们本来也给不出别的信息。

`commit_compaction` 只做落库与组装，返回新的 `list[dict]`。**`CompactionOutcome` 由谁构造，按 kind 分
工**：`kind == full` 的结果由 `_run_compaction` 构造（它手上就是全部八个字段，见上）；闸门短路（熔断器
已开、命中抑制闩）、`microcompact`、`minimal_history` 三类由 coordinator 构造——那三条根本不进
`_run_compaction`（见 §4.4.3 与上文"只处理 `kind == full`"一段）。**事件则一律由 coordinator 发**，不论
这个结果是谁构造的，这样"发不发事件"的判据只有一处（§4.2 的 `skipped` 静默规则）。

**熔断查询不在 prepare 里。** 上一版把它列进 `prepare_compaction`，与 §4.3"coordinator 独占策略"矛盾。
落法：coordinator 在调 prepare **之前**读 `compaction_circuit_open`（`:423`），决定这次是放行、暂停还
是当探针；`prepare_compaction` 自己**不做**任何熔断判断。legacy `compress_messages()` 前面那道闸门是
wrapper 保留的（§4.3），不是 prepare 的一部分。

**prepare 也不是"零副作用"，上一版那样写是错的。** 它调 `microcompact_messages`（`:565`），后者会写
`last_microcompact_mutated`（`:408`）并打一行日志（`:411-413`）。这两项可以接受：该标志唯一的读者是微
压缩入口，而它在自己调用之后立刻读（`_compaction.py:49`），所以一次被取消的 prepare 不会误导任何人。真
正的红线是下面这条。
**`crystallize_user_messages` 必须从 prepare 挪到 commit。** 它今天在摘要**之前**就落库
（`:576-584`，调用在 `:582`）；若留在 prepare 里，一次被 `cancel` 的压缩会满足"历史逐字节不变"却**已经改了记忆库**。
`save_session_summary`（`:598`）同理。**cancel 之前不得发生的副作用，就是这两处 SQLite 写，外加对
`agent.messages` 的任何赋值。** 其余（token 估算、字符串组装）是纯计算，发生了无所谓。

**两个类型，不是一个。** 上一版把它们混成了一个 `CompactionPreparation`，只带索引、计数和预算——那样
`commit_compaction` 拿不到它需要的东西。

`PreparedCompaction`（**私有**，prepare → commit 的快照）：

```python
@dataclass(frozen=True)
class PreparedCompaction:
    trigger: CompactionTrigger
    kind: CompactionKind
    reason: CompactionReason
    is_auto: bool
    split_index: int
    to_summarize: list[dict]     # messages[:split_index]（`:558`）
    to_keep: list[dict]          # 已微压缩的存活半边（`:565`）
    pinned: list[dict]           # （`:568`）
    recently_read: list[str]     # （`:574`）
    summary_messages: list[dict] # to_summarize 去掉 [PIN] 之后（见下）
    summary_input: str           # _format_for_summary(summary_messages) 的结果
    pre_tokens: int              # estimate_tokens(messages)，**不含系统提示**（`:587`）
```

这几个对象 commit 一个都不能少：`crystallize_user_messages(to_summarize)`（`:582`）、
`len(to_summarize)` / `len(to_keep)` 进 `_last_compact_stats`（`:646-647`）、
`result = [boundary, summary] + file_hint + pinned + to_keep`（`:639`）。**"宿主可以自己读
`agent.messages`"解决不了 commit 的数据依赖**——那句话回答的是宿主需要什么，不是 commit 需要什么。

`CompactionDecisionContext`（**公开、脱敏，仅进程内，只给 controller**）：

```python
@dataclass(frozen=True)
class CompactionDecisionContext:
    trigger: CompactionTrigger
    kind: CompactionKind
    reason: CompactionReason
    pre_tokens: int | None             # 不含系统提示；**仅 kind == full 有值**
    messages_to_summarize: int         # 计数，不给原文
    messages_to_keep: int
    recently_read_files: tuple[str, ...]
    summary_input_budget: int | None   # 仅 kind == full 有值
    max_summary_tokens: int | None     # 同上
    can_provide_summary: bool          # 仅 kind == full 为 True
    tool_results_to_clip: int | None   # 仅 kind == microcompact 有值（见 §4.4.3 逐 kind 取值表）
```

**命令型 hook 拿到的不是这个对象，而是既有的 PreCompact 载荷。** 那份扁平的 Claude 兼容载荷由
`build_pre_compact` 构造（`plugins/hooks/_payload.py:145-163`，PR-1 修好 `trigger` 之后的版本），三条理
由：`_matches` 按它的**顶层字段**做匹配（`_dispatcher.py:206-235`），换个形状就要重写 matcher 契约；
hook 只做 `allow` / `cancel`，用不到预算字段；再定义一份 wire schema 就是第二份要长期维护的公开契约。
**`CompactionDecisionContext` 不上线、不序列化**，它是进程内对象，只有 `compaction_controller` 看得见。

**公开对象里不放原始消息文本。** 理由**不是**"会多复制一份历史"——上一版那句话是错的，把列表引用放进
dataclass 只是共享引用，不复制任何东西。真正的理由有两条：一是脱敏边界，controller 与命令型 hook 拿到
的应当是本计划定义、可版本化的一小组字段，而不是整段对话；二是给了原文就等于邀请宿主去改它，而任意消息
列表本节开头已经否决。宿主确实需要原文时读 `agent.messages`——那是它已有的、自负后果的通道。

**宿主摘要的预算与校验。** `max_summary_tokens = _summary_input_budget() // 2`，与
`_clip_carry_summary` 今天给旧摘要的那一半同源（`:983-993`），理由也相同：这份文本下一轮会作为
`<previous-summary>` 再进一次摘要输入，超过一半就会饿死实时尾部。提交前拒绝四种：空串、非 `str`、超
`max_summary_tokens`、以及含 `SUMMARY_END_MARKER`（会破坏下一轮的 carry 剥离，`:904-914`）。

**非法宿主摘要——先降级重跑，再定终态；不存在"既失败又继续"。** 上一版同时写了"返回
`status="failed"`"和"继续跑内建摘要"，一次操作不可能两者都成立。定死的阶梯：

1. 校验不过 → **拒掉这段文本**，把没过的那条校验写进 `detail` 并打 warning。**这不是终态。**
2. 立刻按 `allow` 跑一次内建摘要（`:588`）。
3. 内建摘要成功 → `status="success"`，`detail` 保留"宿主摘要被拒 + 原因"。宿主看得见自己被拒，压缩照样
   完成。
4. 内建摘要也失败（返回空）→ `status="failed"`，并按 PR-2 定的常规规则计数。也就是说：**计进熔断器的
   永远是内建摘要那次失败**，与宿主给没给摘要无关；宿主的非法摘要本身**从不计数**。

这样一个坏 controller 最多让每次压缩多跑一次内建摘要，绝不会在三次调用内把自动压缩关到会话结束——那正
是本条要防的事。

### 4.3 PR-2——熔断器改成可恢复状态机

语义：

- 连续三次失败只暂停**阈值**尝试——够用来止住 `:535-539` 注释描述的每轮重入。
- 手动 `/compact` 始终允许作为 half-open 探针。
- 已经发生的 API 溢出允许一次紧急探针；它不该被一个只描述阈值行为的熔断器挡住。**依赖 PR-1**（§3.3）。
- 探针成功立即复位；失败保持打开。
- `/clear` 调公开的 `ContextManager.reset_compaction_circuit()`（**今天不存在**，本 PR 新增）。
- 给 `:590` 的计数加上 `:540` 已经有的那条 `is_auto` 豁免。

**唯一真相源——状态留在 `ContextManager`，coordinator 只执行策略。** 计数器
`_consecutive_compact_failures`（`context_manager.py:91`）不搬家。理由是搬家要同时重接三处已有的公开
面：`get_usage_stats()['circuit_breaker_failures']`（`:1223`）、`/context` 的渲染
（`cli/commands/context.py:33-40`）、以及 `/clear`；而搬过去买不到任何东西——coordinator 读
`compaction_circuit_open`（`:423`）、决定这次算不算探针、再调 `reset_compaction_circuit()` 就够了。
`closed / open / probing` 里只有 `probing` 是 coordinator 的瞬时状态，不落到 `ContextManager`。§1 的
分层表已按这条口径改写。

**新增 `Agentao.compact(*, reason=...) -> CompactionOutcome` 作为公开入口。** 今天没有任何公开压缩接
口：三个调用点全部直接伸进 `context_manager`（`_compaction.py:102`、`_runner.py:1167`、
`cli/commands/compact.py:103`）。

**兼容口径——保留签名、返回值形状、以及 breaker-open 短路语义。**
`ContextManager.compress_messages()` 落地后等于
`熔断查询 → prepare_compaction → 内建摘要 → commit_compaction` 的组合，闸门仍在最前（今天的 `:507`），
返回值仍是 `list[dict]`。闸门不能去掉——它既写在 docstring 里（`:496-497`），也被**直接调用它**的测试钉
死（`tests/test_context_manager.py:692-701`，`test_compress_messages_no_safe_split_counts_a_failure`
连调三次后断言 `compaction_circuit_open is True`）。上一版写的"直接调它就绕过熔断策略"是错的。

**但"行为逐字节不变"同样说过头了，已收回。** 本计划**有意**改掉两处副作用，两处都要在 PR 里点名并改测
试：

1. **手动路径的失败计数。** PR-2 给 `:590` 加上 `:540` 已有的 `is_auto` 豁免，于是
   `compress_messages(..., is_auto=False)` 摘要失败时**不再**计数——今天会。
2. **摘要失败时的 crystallize 时机。** 今天 crystallize 在 `:582`、摘要在 `:588`，所以**摘要失败也已经
   落过库**。拆成 prepare/commit 后 crystallize 归 commit（§4.2.1，为了让 cancel 真的无副作用），摘要失
   败时 commit 不跑，于是**不再落库**。这是取消语义要求的直接后果，不是意外。**这一项落在 PR-3**，不在
   PR-2 也不在 PR-4：prepare/commit 的边界是 PR-3 划的，而"提交前不得有不可逆副作用"正是这条边界的定
   义。动机来自 PR-4，落地在 PR-3——所以 PR-3 的描述与测试里要点名这处行为变化。

直接调它真正绕过的只有两样：**宿主控制面**（PreCompact 派发、controller）与 coordinator 的**探针策
略**。所以它在文档上降级为内部变换，新代码走 `Agentao.compact()`。这条写进
`docs/reference/host-api.md`，不靠 deprecation warning 传达。

**展示是扩展，不是新铺管线。** `get_usage_stats` 已经导出 `circuit_breaker_failures`
（`context_manager.py:1223`），`/context` 已经把它连同 "(circuit open — auto-compact disabled)" 一起渲染
（`cli/commands/context.py:33-40`）。PR-2 是把计数换成 `closed / open / probing`、最近一次失败分类、以及
恢复方式。

**不要再包一层摘要重试**（§3.4）。

**排序备注：** PR-2 引入 `probing` 这个必须被上报的新状态。它若先于 PR-3 落地，返回值与事件都要在 PR-3
里返工。

### 4.4 PR-4——PreCompact 控制面

首版粒度：**`allow` / `cancel` / `provide_summary(summary_text)`**。

任意替换消息列表——**不做**。agentao 的历史是一个带承重不变量的扁平列表：`tool_calls[*].id` 必须逐字节
往返，才能与回答它的 `role: "tool"` 消息对上，否则严格 API 直接拒绝请求（CLAUDE.md「Unicode tag
stripping」一节）。宿主返回一条孤立的 tool result、一个未知 role 或一个错误边界，产出的就是 provider 会拒
的请求——而此时历史已经被销毁了。pi-mono 敢开更宽的契约，是因为它的 `CompactionResult` 用
`firstKeptEntryId` 寻址一棵持久化会话树；agentao 没有对应物（`pi-mono-compaction-vs-agentao.zh.md` §8）。

接缝本身在 **§4.2.1**（属于 PR-3，本节只在它上面启用 `decide`）。

#### 4.4.1 命令型 hook 的取消协议

`dispatch_pre_compact` 今天是纯 side-effect 的 `_dispatch_lifecycle`（`_dispatcher.py:158-164`），
**根本不解析 stdout**。所以本 PR 要新增兄弟方法 `dispatch_pre_compact_decision`，形状照抄
`dispatch_pre_tool_use_decision`（`:90-117`）。

线上形状——**用一个从未存在过的专用键**：

```json
{"hookSpecificOutput": {"compactionDecision": "cancel", "compactionDecisionReason": "..."}}
```

- 键名 `compactionDecision`，**不复用 `permissionDecision`**。
- 值域 `allow` | `cancel`。缺键、缺 `hookSpecificOutput`、stdout 不是 JSON、脚本什么都不打印——全部等
  于 `allow`。其余取值按 `allow` 处理并 warn——一个拼错的取值不能有能力永久拦住压缩、把上下文一路推进溢出阶梯。
- `compactionDecisionReason`（可选 `str`）进 `CompactionOutcome.detail` 与日志；`reason` 作为回退键再读
  一次，与先例 `:358` 一致。
- 合并规则 **first-cancel-wins**，见到 `cancel` 即停止继续 fork。注意先例是两级——"first-deny-wins，其
  次 first-ask-wins"（`:102-104`）——这里只有一级，因为 v1 没有 `ask`。
- exit-code 2 继续不认，与先例及 `docs/history/implementation/stop-precompact-hooks-plan.md:87` 一致。
- 命令型 hook **不能** `provide_summary`：它没有可信边界，而摘要文本会永久重写历史。`provide_summary`
  只走 §4.4.2 那一层。

**"不需要兼容开关"的理由随之收紧。** 原理由是"沉默即 allow"——但那只证明**什么都不打印**的脚本安全，
证明不了某个私有脚本出于别的目的写出的 `hookSpecificOutput` 安全。真正的理由是键名：
`compactionDecision` 在 agentao 里从未存在过，没有任何既有脚本能碰巧产出它。§8 那条"依赖前先 grep"因此
作废——专用字段不需要 grep 来支撑，grep 也覆盖不到用户本地的私有脚本。

#### 4.4.2 构造参数那一层

**新增构造参数 `compaction_controller=`**，让可信的嵌入式宿主拿到 `CompactionDecisionContext`，可以取
消，或（仅 `kind == full`）用摘要文本作答。新 kwarg 加在 `Agentao.__init__` 的 `*` 之后（插进旧分组中
间会移动 legacy 位置参数）。

**契约——`Protocol`、返回类型、同步、以及失败时的取向。**

```python
class CompactionController(Protocol):
    def __call__(self, ctx: CompactionDecisionContext) -> CompactionDecision: ...

@dataclass(frozen=True)
class CompactionDecision:
    action: Literal["allow", "cancel", "provide_summary"]
    summary: str | None = None      # 仅 action == "provide_summary"
    reason: str | None = None       # 进 CompactionOutcome.detail 与日志
```

- **同步，v1 不收 coroutine。** 压缩路径整条跑在 `ContextManager` 里，那是同步的、手上没有 loop；要支持
  async controller 就得复刻 `AsyncToolBase` 那套 `runtime_loop` 桥接（CLAUDE.md）。返回 awaitable 时按
  **未知返回值**处理（见下），并 warn 说明 v1 不支持。
- **未知返回值**（`None`、别的类型、`action` 不在词表里、`provide_summary` 却 `summary is None`）→ 忽
  略，warn，当作 `allow`。
- **controller 抛异常 → 捕获、warn、当作 `allow`（fail-open），异常不外传。** 这条是硬规则，理由在溢出
  路径上：入口 3/4 是恢复阶梯，controller 里一个 `AttributeError` 若能往外冒，就会把"上下文超长"变成
  "turn 直接崩"，恰好废掉这份计划要修的那条恢复路径。取向与 §4.4.1 的"未知取值按 `allow`"、§4.4.3 的
  "非法决策按 `allow`"完全一致：**控制面的任何错误都不得有能力把上下文推进溢出阶梯，更不得终结 turn。**
- **不设超时。** 同步进程内回调，卡住就卡住整个 turn——与宿主的其他回调（`confirmation_callback` 等）同
  一层语义，不在本计划里单独发明超时机制。
- 至多一个 controller：构造参数不是列表。


**两层的调用顺序与跨层合并——命令型 hook 先跑，任一层 cancel 即终止。**

1. 先派发命令型 hook（`dispatch_pre_compact_decision`），层内 **first-cancel-wins**（§4.4.1）。
2. 任一 hook 返回 `cancel` → 立刻收尾，**不再问 controller**。让可信宿主去算一段马上要被丢掉的摘要是纯
   浪费。
3. 全部 `allow` → 调 `compaction_controller`（至多一个——构造参数不是列表）。它可 `allow`、`cancel`，
   或 `provide_summary(text)`。
4. 跨层合并一句话：**任一层 cancel 即 cancel；`provide_summary` 只可能来自 controller 层。**

#### 4.4.3 五个入口的分型执行计划

上一版只把 `kind == full` 写透了：`split_index`、摘要输入预算这些字段在微压缩和 `minimal_history` 上根
本不存在，`provide_summary` 对它们也没有合法语义。三种 kind 各自的计划：

| `kind` | 入口 | prepare 产出 | 可用决策 | 被 cancel 的结果 |
|---|---|---|---|---|
| `microcompact` | 1 | `prepare_microcompact()` → `PreparedMicrocompact`：`tool_results_to_clip`、`pre_tokens = None`（见下） | `allow` / `cancel` | 跳过这一趟；本回合内不再派发；历史逐字节不变 |
| `full` | 2 / 3 / 5 | `PreparedCompaction`（上文） | `allow` / `cancel` / `provide_summary` | 见下面的"取消语义" |
| `minimal_history` | 4 | `PreparedMinimalHistory`：`keep_tail = 2`、`pre_tokens = None`（这条路径今天不做任何 token 估算，见 §4.2） | `allow` / `cancel` | 返回 context-length 错误，不裁 `messages[-2:]` |

- **三种 kind 共用一个请求类型** `CompactionRequest(trigger, kind, reason)`。差异全落在 prepare 的产出
  与 `can_provide_summary` 上，coordinator 的骨架只有一份。
- **`provide_summary` 只在 `kind == full` 合法。** 另外两种 kind 的
  `CompactionDecisionContext.can_provide_summary` 为 `False`；controller 仍然返回摘要文本时按**非法决
  策**处理：忽略、打 warning、当作 `allow`。取向与 §4.4.1 那条"未知取值按 `allow`"一致——控制面的一个
  配置错误不该有能力把上下文推进溢出阶梯。
- **每个 kind 的字段取值逐一定死**，不留"填什么"的空白：

| 字段 | `microcompact` | `full` | `minimal_history` |
|---|---|---|---|
| `pre_tokens` | **`None`**（见下） | `:587` 的值 | `None`（该路径不做估算，§4.2） |
| `messages_to_summarize` | `0`（不摘要） | `len(to_summarize)` | `0` |
| `messages_to_keep` | `len(messages)`（一条不删，只缩内容） | `len(to_keep)` | `2`（`keep_tail`） |
| `recently_read_files` | `()` | `:574` 的结果 | `()` |
| `summary_input_budget` | `None` | `_summary_input_budget()` | `None` |
| `max_summary_tokens` | `None` | 预算的一半 | `None` |
| `can_provide_summary` | `False` | `True` | `False` |
| `tool_results_to_clip` | `len(targets)` | `None` | `None` |

  最后一个字段是为微压缩专门加的：`messages_to_summarize = 0` / `messages_to_keep = len(messages)` 对它
  没有任何信息量，宿主要判断"这一趟值不值得取消"，需要知道会裁掉几条工具结果。

- **微压缩的 `pre_tokens` 是 `None`，不是 `estimate_tokens(messages)`——上一版这里与 §4.2 打架。**
  §4.2 定死"本计划不新增任何一次 `estimate_tokens` 调用"，而微压缩今天**唯一**存在的那次估算算的是
  `messages_with_system`（`_compaction.py:46`），是**含系统提示**的口径。拿它去填一个声明为"不含系统提
  示"的字段就是混单位——正是 §4.2 特意把新事件字段改名为 `*_tokens_history` 要防的事；另起一次
  history-only 估算则是在 55–80% 带里**每 iteration** 多跑一次全历史编码，正是 `_compaction.py:50-53`
  那段注释花力气避开的开销。所以留 `None`：`tool_results_to_clip` 已经把这个 kind 的决策信息给全了。
  这也与 §4.2 新事件表里"`microcompact` 两列全 `null`"逐字一致。

#### 4.4.4 取消的抑制闩与取消语义

"本回合内不再派发"上一版只是一句承诺，没有机制——而循环**每个 iteration 都会重新检查阈值**
（`_compaction.py:30`、`:76`），没有闩就等于每轮重新问一遍，正是 `:32,78` 那两处退让注释要防的事。定
死：

- **所有者：coordinator**，一个 `set` 实例字段，不落到 `ContextManager`（与熔断状态相反——那个有三处既
  有公开面要伺候，这个没有）。
- **只覆盖两个 reason：`microcompact_threshold` 与 `compression_threshold`。** 只有这两个是循环里**每个
  iteration 重查**的（`_compaction.py:30`、`:76`），也只有它们会重复派发。键仍写成 `(kind, reason)`，因
  为这两条分属不同 kind，要能分别记。
- **`manual_cli` 永不进闩。** 它用户驱动、不循环，而且**跑在 turn 之外**——`/compact` 是斜杠命令分发
  （`cli/input_loop.py:230`），不经 `run_turn`；新增的 `Agentao.compact()`（§4.3）同样可以在 turn 外被
  调用。按 turn 复位的闩套到它们身上，效果是"手动取消一次，之后立刻重试会一直被旧闩压住，直到用户先跑
  完一个普通 turn"——一个纯粹由实现细节造出来的坑。
- **两个 overflow reason 也不进闩。** 被取消的溢出直接把 context-length 错误返回给调用方、turn 就地结
  束（§4.4 取消语义），没有"重复派发"可言。
- **复位点：turn 开始。** 落在 `runtime/turn.py:98-106` 那个既有的 per-turn 复位块里，和
  `_turn_finish_reason_missing`、`last_summary_finish_reason_missing` 并排——那里已经是"每回合清一次的
  标志"的家。收窄到两个 threshold reason 之后，这个复位点就够了：那两条**只**在 turn 内发生。
- **只有 `cancelled` 进闩。** `skipped` / `failed` 不进：前者本来就没试，后者由熔断器负责节流，两套机制
  不叠加。
- **命中闩是静默的：** 不派发 hook、不调 controller、不发事件，只返回
  `CompactionOutcome(status="skipped", detail="suppressed_by_latch")`。取向与 §4.2 那条"`skipped` 不发
  事件"一致，理由也一样——它每个 iteration 都会命中。

**取消语义——这正是当年那条排除理由说的地方。**
`docs/history/implementation/stop-precompact-hooks-plan.md:1081` 之所以把 PreCompact gate 推迟，原话就是"接受宿主『拒绝』却没有
*宿主拒绝且仍然超长*的兜底，会产生不可恢复的失控"，`codex-compaction-vs-agentao.zh.md:294` 记的是同一条
推理。答案：

- 被取消的**阈值**压缩在本回合内不再重复派发——不会每轮 fork hook（机制见上面的抑制闩）。
- 若随后 API 真的溢出，以 `reason=api_overflow` **再问一次**。这是另一个问题，宿主有权分别作答。
- 若**溢出**也被取消，把 context-length 错误返回给调用方。**不要静默落到 `messages[-2:]`。** 当年担心的
  失控来自"取消被忽略"，不来自"取消被兑现并上报"。
- **入口 4（`minimal_history`）被取消**：同样把 context-length 错误返回给调用方，历史逐字节不变。它
  是一条独立的派发点（`_runner.py:1199`），只有在入口 3 被放行、压缩成功了却**仍然**溢出时才走得到，所
  以它需要自己的答案，上一条覆盖不了它。语义与上一条一致：取消被兑现并上报，不静默落到 `messages[-2:]`
  （`:1204`）。
- 被取消的**手动**压缩只报 `cancelled`，历史逐字节不变。
- 被取消的**微压缩**（入口 1）只跳过这一趟，历史逐字节不变；与阈值一样，本回合内不再重复派发。它
  **不**返回错误——微压缩从来不是"不做就过不去"的那一步，55–80% 带下一回合会自己再判一次。

---

## 5. 第二批——P2/P3

### 5.1 PR-5——窗口校验与自愈

`max_context_tokens` 是**四个面上都有文档的宿主所有旋钮**（`agent.py:104`；`embedding/factory.py:132`；
`cli-host-agent-factory.zh.md:104` 指明了归属；ACP 那三个互不覆写的旋钮见
`docs/history/implementation/acp-stdio-auth-fix-plan.md:99-110`）。本 PR **不**收回这份所有权，**不**引入需要长期维护的模型窗口表。

- 保留宿主配置的 `configured_max_tokens`。
- 从高置信度 overflow 错误里解析 provider 明示的上限。
- 运行期使用 `effective_max_tokens = min(configured, observed_limit)`。
- 换模型/provider 时清除 `observed_limit`，并提示"当前窗口未经新模型验证"——绝不自动覆写宿主的值。这条
  加入**已有的** clear-on-switch 家族（thinking artifacts、tiktoken encoding、token 锚点、capability
  latch——见 CLAUDE.md），不是新造机制。
- `/context` 展示 configured、effective、来源与 mismatch 状态。

**迁移规则——`max_tokens` 今天有 8 个读写点，一个都不能靠"应该没人用"糊过去。** 外部写两处：
`/context limit <n>`（`cli/commands/context.py:66`）、ACP `session/set_model` 的 `contextLength`
（`acp/session_set_model.py:69`）。外部读三处：ACP 的回读（`session_set_model.py:75`，它决定客户端看到
哪个值）、`get_usage_stats()['max_tokens']`（`context_manager.py:1219`）、`/context` 的渲染
（`context.py:22`）。内部读五处：`:267`（全量阈值）、`:278-279`（微压缩带）、`:1002`（摘要输入预算）、
`:1215`（`usage_percent`）。定的口径：

- `max_tokens` 这个属性**继续代表 configured**，读写语义一字不变——它是宿主的旋钮，写进去什么就读出什
  么。`effective_max_tokens` 是新增的**只读**属性。
- **内部预算全部改读 effective**：`:267`、`:278-279`、`:1002`。这三处正是"窗口配错"发作的地方。
- **`usage_percent` 也改读 effective**（`:1215`），否则 `/context` 会在 API 已经开始拒绝的时候报 70%。
- **ACP 回读继续返回 configured**（`session_set_model.py:75`）：`session/set_model` 是 setter，回声必须
  等于刚写进去的值，否则客户端会把 agentao 的自愈当成写入失败。
- **`get_usage_stats()` 保留 `max_tokens` 键**、值继续是 configured，另加 `effective_max_tokens` 与
  `observed_limit_provenance` 两个新键。老键语义不变，老宿主不受影响。

**解析的已知边界——要围着它设计，它们不是否决理由。** `_OVERFLOW_PATTERNS`（`context_manager.py:1235`）
的 21 条里，消息自带数字的约占一半（Anthropic `tokens > N maximum`、OpenAI `maximum context length is N`、
xAI `maximum prompt length is N`、OpenRouter、Mistral），另一半不带（`context_length_exceeded`、
`request_too_large`、`reduce the length`、`too many tokens`、`range of input length`）。更麻烦的是带数字的
那些通常带**两个**——Anthropic 的 `213462 tokens > 200000 maximum` 同时有请求量和上限，OpenAI 的也是。
取错方向会把 `effective_max_tokens` 永久压小、直到下次换模型，而这是一次*没有告警的静默降级*——正是本计划
要消灭的那类失败。因此：

- 解析必须带 provider 断言，不是裸抓数字。
- **解析不确定就不采纳。** 退回阶梯是安全结局。
- `/context` 展示该上限是从哪条串学来的。

**优先级备注——以及它挡不住什么。** 这一项的优先级随阈值改动上升了：0.65 时"我们压缩"与"API 拒绝"之
间还有 35 个百分点的窗口，0.80 时只剩 20 个。这段余量正是用来吸收配错的窗口的，也是两级阶梯的回退空间。

但要把 PR-5 **不能**做的事写清楚：`observed_limit` 只能从 overflow 错误里学到，所以**第一次掉进阶梯是
它的输入，不是它能挡的事**。它减少的是此后重复掉进去的次数，不是"挡在窗口配错与阶梯之间"。0.80 抬高的
是两件事的风险——配置正确时估算偏差的风险，以及部分中等窗口错配的风险——PR-5 对前者无能为力，对后者也只
在第一次之后有效。

### 5.2 PR-6——摘要质量，按风险排序

1. **token 化保留窗口——针对"压缩后仍然很重"，不是针对摘要输入预算。** 今天的保留量是
   `keep_count = min(20, max(4, int(len(messages) * 0.60)))`（`context_manager.py:522-525`）；叠一层从
   尾部反向的 token 累积，取的是三者的**交集**，因此它只会让保留量**变小**。

   **上一版把机制说错了，已改。** 保留窗口**不进摘要器**：进摘要器的只有
   `to_summarize = messages[:split_index]`（`:558`），`to_keep`（`:565`）是原样拼进结果的（`:639`）。
   所以很重的尾部撑爆的不是摘要输入预算，而是**压缩后的上下文本身**——一次压缩把旧的那一半换成几百
   token 的摘要，却原样留下几万 token 的尾部，于是马上又越过阈值，下一轮再压一次。注意尾部在 `:565`
   已经过一次微压缩，残余重量主要来自非工具内容和逼近 `MICROCOMPACT_TOOL_LIMIT` 的工具结果。

   它**不**解决对偶的那一半——20 条很短的消息仍然只保留很少 token——那需要抬高
   `KEEP_RECENT_MESSAGES = 20`（`:71`），是一次独立的、要用数据说话的改动，**不在本项范围内**。

   **上一版把"至少 4 条"写成硬下限，那是错的，已改。** `keep_count` 只决定**搜索起点**
   （`split_index = _find_split_index(messages, len(messages) - keep_count)`，`:526`）；
   `_find_split_index` 随后从这个起点**向后**扫到第一个非 `tool` 的下标、并优先挑 `user`
   （`:458-467`），所以最终保留的条数**可以少于 4**（起点那几条恰好都是 `role: "tool"` 时），也可以是
   **0**——`chosen is None or chosen == 0` 时它返回 `None`，压缩整个失败（`:473-474`，`status="failed"`，
   §4.2）。

   **所以公式是 `max`，不是 `min`——上一版写反了。** 三个起点：

   ```
   count_start = len(messages) - min(20, max(4, int(len(messages) * 0.60)))   # 今天的，:522-526
   token_start = 使 estimate_tokens(messages[i:]) <= keep_budget 的最小 i      # 从尾部反向累加
   start       = max(count_start, token_start)
   split_index = _find_split_index(messages, start)                           # :526
   ```

   起点越靠后，保留越少。token 预算是本项**新增的收紧**约束，所以两者要取**更靠后**的那个——取 `min`
   会在重尾部时直接把预算违反掉，而那正是本项要修的事。上一版的 `min(token_start, len - 4)` 还漏掉了
   `keep_count` 里 20 条 / 60% 那两条既有限制，一并补进 `count_start`。

   **后果要认下来：`token_start > count_start` 时保留条数可以少于 4。** `max(4, …)` 被 `max()` 覆盖掉
   了——这与上一轮的结论一致：本来就不存在真正的条数下限。结构上唯一的绝对下限是 **1 条**，来自
   `_find_split_index` 的 `limit = len(messages) - 1`（`:455`），它永不返回 `len(messages)`。掉到 4 条以
   下时记一条 log。要让"至少保留 N 条"成为真不变量，得让边界按**完整 tool-call 组向前扩展**，那是一次
   独立改动，**不在本项范围内**。先走配置灰度，再用数据定默认预算。
2. **把旧摘要移出本地淘汰池——收益是形状与提示词，不是修缺陷。** 作为受独立预算保护的
   `<previous-summary>` 块追加，配 UPDATE 提示词；并删掉这个形状逼出来的局部补丁——`carry_index`
   （`context_manager.py:893,938,940`）、`_clip_carry_summary`（`:983`）、以及 `_join_within_budget`
   里的 `carry_index` 特例（`:1045`）。

   **本项曾写作"删掉缺陷类别"；那句话与代码不符，已删。** 旧摘要今天**已经不会被淘汰**：
   `_join_within_budget` 先扣它的预算、把它加进 `keep`、此后永不驱逐（`:1045-1047`）——而上面那三处补
   丁正是当年修好那个缺陷的东西。所以本项的真实收益是提示词形状（UPDATE 语义）与简化，风险是把当年那个
   缺陷改回来。

   **因此替代上限是硬要求，不是可选项。** 今天有两条不变量，删掉 `_clip_carry_summary`（`:983-993`）会
   同时丢掉：carry ≤ `_summary_input_budget()` / 2，且 carry + live ≤ `_summary_input_budget()`
   （`:1041-1047`）。新形状必须重新写出并测出 `carry_budget + live_budget <= summary_input_budget`
   ——"独立预算"只是换了个记账方式，两块文本仍然进同一个 provider 请求，provider 层面的预算竞争一点没
   少。
3. **P3 补偿——第一条明确标为部分缓解。** 切点落在回合中间时，为源头用户请求预留独立**输入**预算；给锚
   点之后新增的图片加一个可注入的 token 估算器（`_count_message_tokens` 今天只累加 `type == "text"`
   块）。

   前一条只闭合 P3 字面写的那半句——"没有任何预算为它保留"
   （`pi-mono-compaction-vs-agentao.zh.md:51`）。**留出输入预算不等于摘要模型会把它写进输出**，而
   pi-mono 的解法是同一行里的另一半：一次自带预算的**独立前缀摘要调用**。本计划不采用那半边，所以这一
   条在关闭 P3 时要写成**部分缓解**，不写成闭合。真要闭合，三选一：确定性携带（把原始用户请求原文拼进
   结果，不经摘要模型）、输出校验（摘要里找不到就重试一次）、或采纳 pi-mono 的专用前缀摘要步骤。三条都
   超出本 PR 范围。

**第二批期间阈值不动。** `MICROCOMPACT_THRESHOLD = 0.55` 与 `COMPRESSION_THRESHOLD = 0.80`
（`context_manager.py:69-70`）在此不改。先**按 0.80 基线**收集压缩成功率、压缩比、延迟、距下次溢出的
距离、cache-read 变化，再决定是否把比例开放为 `CompactionSettings`。任何在 0.65 下采到的数据都不是这次的
基线。

---

## 6. 发布门槛

```bash
uv run python -m pytest tests/
uv run ruff check .
```

**`uv run mypy agentao` 不是可用门槛，不得写进本计划。** 它今天在 `main` 上就跑出
**1084 errors in 146 files（checked 272 source files）**。原因在 `pyproject.toml:195-199` 里看得见：注释
写着"strict 只覆盖公开宿主边界，其余代码在单独事项抬高标准前不动"，但 `strict = true` 设在顶层
`[tool.mypy]` 表上，于是全局生效。typing ratchet 此前已单独评估并否决
（`docs/design/refactor-audit-2026-07.md`）。修这处配置与意图的错配是正当工作——只是它不是压缩编排的前置。

`ruff check .` 里那个裸 `.` 是有意的：规则**和** scope 都在 `pyproject.toml` 里，所以这条命令
逐字符就是 CI 跑的那条（`docs/design/lint-gate.md`）。收窄成 `agentao tests` 会漏掉 `examples/`、
`scripts/`、`skills/` 和 `developer-guide/`。两种写法今天都过——重点是只有一条是门槛。

### 场景覆盖

- 三次摘要失败后：阈值尝试暂停，手动或溢出探针成功，熔断器复位。
- `/clear` 复位熔断状态。
- 五个入口在 `trigger` / `kind` / `reason`、hook 顺序、事件上一致。
- `{"trigger": "manual"}` 规则只在 `/compact` 上触发；`{"trigger": "manual|auto"}` 规则仍在各处触发。
- 取消压缩后历史逐字节不变。
- 自定义摘要为空、超预算或类型非法时，在提交前被拒。
- 溢出被取消时返回 context-length 错误，**不会**偷偷裁成最后两条。
- token 预算切点不产生孤立 tool result。
- 旧摘要与实时尾部各自遵守预算。
- 换模型不会静默沿用上一个模型已观测的窗口。
- `CONTEXT_COMPRESSED` 只在 `CompactionOutcome.status == "success"` 时发出。**判据不是消息条数。**
  微压缩逐条构造新列表、只缩短 `content`（`context_manager.py:396-405`），成功时 `pre_msgs` 与
  `post_msgs` **必然相等**，按条数判会把每一次成功的微压缩事件都删掉。反方向同样不成立：
  `len(messages) == 5` 时 `keep_count = 4`、`split_index = 1`，一条消息被换成 boundary + summary 两
  条，**成功压缩反而让条数变多**。仓库里已有三处证据说明条数与身份都不可用——`microcompact_messages`
  的 docstring（`:387-389`，"a fresh list is always built, so `result is not messages` says
  nothing"）、为此另设的 `last_microcompact_mutated`（`:112`）、以及 `/compact` 改用
  `[Compact Boundary` 标记嗅探（`cli/commands/compact.py:26-40`）。
- 熔断器打开时溢出入口不发 `CONTEXT_COMPRESSED`（今天无条件发，`_runner.py:1177`）。
- `[PIN]` 消息在压缩结果里**恰好出现一次**，且**不出现在**摘要输入里（`summary_messages` 已剔除
  `[PIN]`，与 `_summarize_messages:847-853` 的既有过滤一致）。
- 阈值压缩被取消后，同一回合内不再派发 PreCompact；但同一回合里随后的 `api_overflow` **仍然**会派发一
  次（抑制闩的键带 `reason`，§4.4.4）。
- 抑制闩在下一回合开头被清掉。
- controller 抛异常时压缩照常进行（fail-open），异常不外传，日志有记录。
- controller 返回未知形状（`None` / awaitable / `action` 不在词表）时当作 `allow` 并 warn。
- 三态 `success | cancelled | failed` 各发一条 `COMPACTION_SETTLED`；**`skipped` 一条都不发**；只有
  `success` 额外发 `CONTEXT_COMPRESSED`。
- `CONTEXT_COMPRESSED` 的七个键与两个 token 的口径（含系统提示）在 PR-3 前后逐字节不变。
- 手动 `/compact` 被取消后**立刻重试仍会正常派发**（`manual_cli` 不进抑制闩）。
- 摘要返回空时失败计数照常 +1（三个计数点都在 `ContextManager._run_compaction` 里，不因 commit 不跑而丢失）。
- 手动 `/compact` 摘要失败后 `_consecutive_compact_failures` **不**增加（PR-2 有意改变，§4.3）。

---

## 7. 明确不做

- **不移植 pi-mono 的会话树。** 全部待在扁平消息列表里。
- **不采用 `chars/4` 估算。** agentao 的 CJK 感知估算器更强（`pi-mono-compaction-vs-agentao.zh.md` §10）。
- **不取消微压缩。**
- **不把检查移到回合边界。** 这根轴上 agentao 与 codex 持平、优于 pi-mono。
- **不增加 codex 那两个独立触发点 `ModelDownshift` / `CompHashChanged`。** agentao 每轮检查，窗口一经
  校正就会自行用上。
- **不把 `trigger` 改成非 Claude 兼容词表**（§3.2）。
- **不在摘要外再包一层重试**（§3.4）。
- **不接受宿主提供的任意消息列表**（§4.4）。

---

## 8. 什么会推翻本计划

- ~~**§3.1 会减弱**：若 `{"trigger": ...}` matcher 在所有已发布插件里都没人用，它仍是契约缺陷，但降为
  P2。降级前请重跑 marketplace manifest 的 grep。~~ **该降级条件已于 2026-08-24 关闭，结论是"按原方法
  不可判定"，§3.1 维持 P1。** 原条件要的是"已发布插件里没人用"的**实测零**；而插件 marketplace 尚未建
  设，**不存在可测的总体**——这是"没有总体"，不是"测出来是零"，只有后者支持降级。何况插件本来就**不经
  marketplace 分发**：`PluginManager` 从 `~/.agentao/plugins`（`embedding/plugins/manager.py:96`）与
  `<cwd>/.agentao/plugins`（`:100`）加载，marketplace 只是那个目录里的**一层组织**
  （`:297-312` 的 docstring："Scan *plugins_dir* for plugins organised by marketplace"）。所以今天真实
  的插件用户群，正是上一条已经承认 grep 覆盖不到的那一群。**并且方向是反的：marketplace 未建是尽早落
  PR-1 的理由，不是降级的理由。** PR-1 对 matcher 是**行为变更**——今天 `{"trigger": "auto"}` 命中包括
  手动 `/compact` 在内的全部五个入口（`_payload.py:160` 全硬编码），PR-1 之后它不再命中手动；在生态成型
  前做，受影响的只有本地手写规则，等公开插件把 `{"trigger": "auto"}` 写进 manifest 之后再做，同一次修
  复就成了对第三方配置的破坏性变更。最后，**有一份已发布文档现在是错的，这与采用率无关**：
  `docs/releases/v0.4.4.md:131` 写着 `trigger | PreCompact only: auto (no manual site exists)`，括号里
  的理由在 0.4.4 成立、手动 `/compact` 落地后失效。**不设重开触发器**——marketplace 上线时这条已无意义。
  顺带：PR-1 的文档工作里应加一行对 `docs/releases/v0.4.4.md:131` 的**勘误**（沿用
  `stop-precompact-hooks-plan` 的既有做法，加勘误而不改写历史陈述）。
- ~~**§3.5 的"不需要开关"会失效**：若有任何已发布的 PreCompact hook 出于别的目的往 stdout 写
  `hookSpecificOutput`。~~ **已由 §4.4.1 的字段选择结构性消除**：`compactionDecision` 是一个从未存在过
  的键，没有任何既有脚本能碰巧产出它。grep 只覆盖得到已发布的插件，覆盖不到用户本地的私有脚本；专用字
  段不需要覆盖任何东西。
- **§4.4 的取消设计会失效**：若某宿主既取消阈值**又**取消溢出、却仍指望这一回合继续跑。它不能——本计划
  返回错误。若这对真实宿主不可接受，那道 gate 需要一个强制压缩的逃生口，本节要重新设计。
- **§5.1 会加强**：若有人举出"CLI 那个 `200_000` 单一默认值（`cli/app.py:278`）与某常用模型静默不匹配"
  的常见部署。若已存在本文遗漏的宿主侧约定能暴露该不匹配，则**减弱**——本文没找到，但请重跑 grep 而不是
  信这一行。
- ~~**§5.2 第 2 项会减弱**：若摘要淘汰缺陷只在没人会用的预算下可达。~~ **已作废**：那个缺陷今天已经
  被 `carry_index` + `_clip_carry_summary` 修好了（`:1045-1047`），本项不是在修它。第 2 项现在唯一会减
  弱的情形是：UPDATE 提示词在实测里并不比现在的 in-band 形状产出更好的摘要——那它就只剩简化价值，应降
  为 P3 或直接不做。
- **锚点会过期。** 动手前重核每一处 `file:line`；本计划写于 `main@a996395` 加两处未提交改动之上。

---

## 9. 评审记录

十二轮评审的条目均已折叠进上文正文（rev 13 / rev 14 是维护者指令与条件关闭，不是评审轮次）。此处仅作历史记录，**不是**覆盖层：§§1–8 自身即为准，任何一节都不需要
读本节才能读对。

### rev 1——9 条

| # | 条目 | 落到哪里 |
|---|---|---|
| 1 | 把 `uv run mypy agentao` 当发布门槛；它今天在 `main` 上就是 1084 errors / 146 files，且 ratchet 早已被否决 | §6 |
| 2 | `CompactionTrigger = manual\|threshold\|overflow` 会打破 Claude 兼容的 `manual\|auto` matcher 词表 | §3.2、§4.1 |
| 3 | `trigger` 这条是**死的 matcher 取值**，不只是载荷字段不准 | §3.1 |
| 4 | 有 `dispatch_pre_tool_use_decision` 的 stdout-JSON 先例，PR-4 的 "hook v2 / 显式开关" 闸门不需要 | §3.5、§4.4 |
| 5 | PR-2 依赖 PR-1（`compress_messages` 默认 `is_auto=True`，溢出与阈值无法区分） | §3.3、§4.3 |
| 6 | 顺序应为 PR-1 → PR-3 → PR-2 → PR-4，不是 1→2→3→4 | §4 |
| 7 | 四处工作量被高估：PR-3 是补完 #181；`is_auto` 豁免在 `:540` 已存在；`/context` 已渲染熔断器；换模型清除已有家族 | §4.2、§4.3、§5.1 |
| 8 | 计划晚了一版——阈值现为 0.80，PR-6 的基线要重设、PR-5 的优先级上升 | §5.1、§5.2 |
| 9 | 把 `ruff check .` 收窄成 `agentao tests` 与 CI 命令不一致 | §6 |

**方法备注。** 其中第 3、5 两条来自读**调用点**而不是读签名：`trigger` 那条的严重性只有在 matcher 测试里
才看得见，PR-1 的依赖关系只有在 `_runner.py:1167` 一个走默认值的关键字参数里才看得见。签名不会告诉你调用
方传了什么。

### rev 2——10 条

第二轮提了 7 条。复核后 5 条完全成立、1 条改判定性、1 条降级为尺度分歧；复核另补 3 条（第 8–10 条）。

| # | 条目 | 复核结论 | 落到哪里 |
|---|---|---|---|
| 1 | §6 拿 `pre_msgs == post_msgs` 当"没发生"的判据，会误删每一次**成功的**微压缩事件 | 成立；定性由阻断降为门槛措辞缺陷 | §6 |
| 2 | `provide_summary` 缺可实施的 prepare/commit 接缝，与 §1"保留函数体"不可兼得 | 成立——**本轮唯一的阻断项** | §1、§4.2.1 |
| 3 | 熔断状态所有权在 §1 与 §4.3 之间不一致 | 成立，但属**未定义**而非冲突：`reset_compaction_circuit()` 今天并不存在，计划只是没说放哪 | §1、§4.3 |
| 4 | 命令 hook 的取消协议没有字段名、值域、reason、合并规则 | 成立，且缺口更大：`dispatch_pre_compact` 今天是纯 side-effect 的 `_dispatch_lifecycle`（`_dispatcher.py:158-164`），根本不解析 stdout | §4.4.1 |
| 5 | configured/effective 改变既有公开 `max_tokens` 契约却无迁移规则；且"挡在误配置与阶梯之间"不成立 | 两半都成立 | §5.1 |
| 6 | PR-6 三项兑现不了各自的目标 | 第 1、2 项成立（且第 2 项**给出的理由本身**也与代码不符）；第 3 项降级为尺度分歧，标为部分缓解 | §5.2 |
| 7 | §2 事实 1 称 `compaction_type` / `reason` 没进 hook 载荷，是事实错误 | 成立，已核实 `_payload.py:162-163` | §2 |
| 8 | 复核补：`!=` 同样不是成功的证据——`len == 5` 时一条消息被换成 boundary+summary 两条，**成功压缩反而让条数变多** | — | §6 |
| 9 | 复核补：PR-1 新加的两个枚举**仍不可被 matcher 匹配**，`_matches` 只读 `trigger` | — | §4.1 |
| 10 | 复核补：溢出入口今天不算 `pre_tokens`，`CompactionOutcome.pre_tokens: int` 必填会在失败路径上强加一次全历史估算 | — | §4.2 |

**方法备注。** 这一轮的第 2、4、7 三条都是读**被引用的那一行本身**读出来的：`_payload.py:162-163` 直接
推翻了 §2 的一条奠基事实，`_dispatcher.py:158-164` 说明先例只覆盖了 PR-4 需要的一半。第 6 项第 2 条更进
一步——计划提议删掉的那个补丁（`carry_index`），正是修好它所声称的那个缺陷的东西。**引用一行的时候要连
它的上下文一起读，否则会把"已经修好的"当成"待修的"。**

### rev 3——6 条

第三轮提了 6 条，**全部成立**，其中两条（第 1 条后半、第 6 条）指的是 rev 2 修订**自己引入**的错误。

| # | 条目 | 复核结论 | 落到哪里 |
|---|---|---|---|
| 1 | `CompactionPreparation` 只带索引与计数，`commit_compaction` 拿不到 `to_summarize` / `to_keep` / `pinned`；且 prepare 声称"零副作用"却调用会写 `last_microcompact_mutated` 并打日志的 `microcompact_messages` | 成立，两半都成立。已拆成私有 `PreparedCompaction` + 公开脱敏 `CompactionDecisionContext`；"零副作用"改为点名两项可接受副作用。"放引用会复制历史"是 rev 2 自己写错的理由，已删 | §4.2.1 |
| 2 | 控制面只把 `full` 写透，未覆盖承诺的 5 个入口；且六个 PR 里没人负责创建 coordinator | 成立 | §4、§4.4.2、§4.4.3 |
| 3 | 非法宿主摘要既"返回 `status="failed"`"又"继续跑内建摘要"，终态自相矛盾 | 成立 | §4.2.1 |
| 4 | 熔断查询被放进 `prepare_compaction`，与"coordinator 独占策略"矛盾；且"直接调 `compress_messages` 绕过熔断策略"改变了既有文档与测试的行为 | 成立。后半有硬证据：`:496-497` 的 docstring + `tests/test_context_manager.py:692-701` | §4.3、§4.2.1 |
| 5 | "20 条巨大尾部撑爆摘要输入预算"是错的——`to_keep` 不进摘要器；且 token 上限与"至少 4 条"在 4 条本身超预算时无法并存 | 成立，两半都成立 | §5.2 |
| 6 | 中英孪生与索引漂移：中文 §8 同时留着新旧两条 §3.5、且丢了 §3.1 那条；`docs/README.md` 仍写 "Reviewed once" | 成立。中文 §8 的重复与丢失都是 rev 2 patch 打错行造成的 | §8、`docs/README.md`、`docs/design/README.md` |

**方法备注。** 这一轮里第 1、4、5 三条都是**顺着数据流往下读一格**读出来的：`commit` 需要什么，看
`:582` / `:639` / `:646-647` 用了哪些局部变量；"绕过熔断"能不能说，看有没有测试直接调它；"撑爆摘要预
算"对不对，看 `to_keep` 到底流向 `_format_for_summary` 还是流向 `result`。**声明一个函数"做什么"之前，
先看它的返回值被谁消费。** 另有一条自省：rev 2 的 §8 是按行号打补丁的，打偏一行就同时制造了"重复"和
"丢失"两个缺陷，而两者在单独读任一条时都不显眼——**按行号改文档必须回读改动前后各一条目**。

### rev 4——8 条

第四轮提了 8 条，**全部成立**。其中第 5 条同时推翻了 rev 2 给 `pre_tokens` 写的**理由**（结论碰巧对，
理由是错的），第 6 条推翻了 rev 3 刚写进去的一条"不变量"。

| # | 条目 | 复核结论 | 落到哪里 |
|---|---|---|---|
| 1 | `summary_input = _format_for_summary(to_summarize)` 会把 `[PIN]` 消息一并送进摘要器，而 commit 又把 `pinned` 原样重注入 | 成立。`_summarize_messages` 在 `:847-853` 先剔除 `[PIN]` 才调 `_format_for_summary`（`:854`），而 `pinned`（`:568`）正是被剔除的那个补集。已新增 `summary_messages` 字段 + 回归场景 | §4.2.1、§6 |
| 2 | "本回合内不再派发"没有闩的所有者、键、复位点 | 成立。循环每 iteration 都重查阈值（`_compaction.py:30,76`）。已定 coordinator 持有、键为 `(kind, reason)`、复位在 `runtime/turn.py:98-106` | §4.4.4 |
| 3 | `compaction_controller` 没有 `Protocol`、返回类型、同步/异步、异常与未知返回值的策略 | 成立。已给出 `CompactionController` / `CompactionDecision`，定为同步 + **fail-open**，并写明理由：controller 的异常若外传就会废掉溢出恢复阶梯 | §4.4.2 |
| 4 | `CompactionOutcome` 缺 status 映射表；"新增终态事件"没有事件名与 payload schema | 成立。已给出 8 行映射表 + `EventType.COMPACTION_SETTLED` 与 payload | §4.2 |
| 5 | `pre_tokens` 可空口径冲突：§4.2 说 `int \| None`，两个新类型却都必填 | 成立，且 §4.2 的**理由**也是错的——full 路径今天就在 `:587` 算 `pre_tokens`，入口 3 也走这条；真正不估的是 `minimal_history`，另外还有"含/不含系统提示"两个口径 | §4.2、§4.2.1、§4.4.3 |
| 6 | "至少保留 4 条"不是当前切点算法能保证的 | 成立。`_find_split_index` 从起点**向后**扫（`:458-467`），最终可少于 4 条、也可 `None`。rev 3 那句"条数下限是硬下限"已撤 | §5.2 |
| 7 | "legacy 行为逐字节不变"过强，与有意改变的 crystallize 时机、手动失败计数冲突 | 成立。已收窄为"签名 + 返回值形状 + breaker-open 短路"，并列出两处有意改变 | §4.3 |
| 8 | `docs/design/README.md` 的 "Active & proposed designs" 仍无本文档 | 成立。rev 3 只改了那份 README 里**上游分析条目**尾部的评审次数，没往活跃清单里加条目 | `docs/design/README.md` |

**方法备注。** 第 1、5、6 三条有一个共同形状：**我引用了一个函数，却没读它在被调用之前先做了什么。**
`_format_for_summary` 前面有一道 `[PIN]` 过滤（`:847-853`）；`estimate_tokens` 在 full 路径上已经被调过
（`:587`）；`_find_split_index` 拿到的 `keep_count` 只是它的**起点**而不是它的**结论**（`:458`）。
**"某某函数的输入是 X"这句话，要从调用点往上读到它真正收到的东西为止。** 第 8 条则是另一类：rev 3 声称
"同步了 `docs/design/README.md`"，实际只改了里面一处字符串——**"改了那个文件"不等于"改对了那件事"。**

### rev 5——5 条

第五轮提了 5 条，**全部成立**。其中第 1、2、3 条各推翻了 rev 4 刚写进去的一处设计。

| # | 条目 | 复核结论 | 落到哪里 |
|---|---|---|---|
| 1 | prepare 的返回类型承载不了 `len < 5` 与"无安全切点"两条提前终止；且表格把摘要失败计数放进 commit | 成立，两半都成立。后半是个真洞：摘要返回空时 `:589` 就 return，commit 根本不跑，那次计数会凭空消失。已改为 `PrepareResult` 联合类型 + **三个计数点全部归 coordinator** | §4.2.1 |
| 2 | PR-6 的切点公式方向反了，且漏掉既有的 20 条 / 60% 限制 | 成立。起点越靠后保留越少，token 预算是收紧约束，必须取 `max`；`min` 会直接违反预算 | §5.2 |
| 3 | `post_tokens` 无口径；旧事件的 token 含系统提示（`_compaction.py:46`），新事件若直接接过去就改了公开字段语义；且新旧键名不同，"超集"不成立 | 成立，三半全成立。旧事件的键是 `type` / `pre_est_tokens` / `post_est_tokens`（`replay/observability.py:47-55`），与我写的 `kind` / `pre_tokens` / `post_tokens` **三个都不同名** | §4.2 |
| 4 | 抑制闩按 turn 复位，会误伤 turn 外的 `/compact` 与新增的 `Agentao.compact()` | 成立。`/compact` 是斜杠命令分发（`cli/input_loop.py:230`），不经 `run_turn`。已把闩收窄到两个 threshold reason；并补上"命中闩是否发事件"的答案（不发） | §4.4.4、§4.2 |
| 5 | `CompactionDecisionContext` 对非 full 入口的字段取值未定；命令 hook 的输入 wire schema 未定 | 成立。已给出逐 kind 的字段取值表（含微压缩专用的 `tool_results_to_clip`），并定死 hook 拿的仍是既有 PreCompact 载荷（`_payload.py:145-163`），context 不上线、不序列化 | §4.2.1、§4.4.3 |

**方法备注。** 第 1、3 两条同一个形状：**我给一个新类型定了字段，却没有走一遍它要覆盖的每条路径。**
prepare 有两条提前 return，我只为成功那条设计了返回值；事件有两个生产者，我只看了自己写的那个 payload，
没打开 `replay/observability.py` 看既有的键叫什么。**定一个类型或一份 schema 之前，先把它要覆盖的分支和
它要兼容的既有形状各列一遍。** 第 2 条则是纯粹的方向错误：`start` 是保留窗口的**起点**，起点越靠后保留
越少——写公式时要拿一个具体数字代进去验一次符号，而不是靠"下限/上限"这两个词的直觉。

### rev 6——4 条

第六轮提了 4 条，**全部成立**。第 1 条推翻的是 rev 5 刚写进去的"计数全部归 coordinator"。

| # | 条目 | 复核结论 | 落到哪里 |
|---|---|---|---|
| 1 | legacy wrapper 与 coordinator 的计数所有权矛盾：`compress_messages()` 可独立调用、手上没有 coordinator，而计数器按 §4.3 留在 `ContextManager`；且"计数行为与今天逐条一致"与 §4.3 点名的两项有意变化冲突 | 成立，两半都成立。已改为**共享的私有方法 `ContextManager._run_compaction`** 承载三个计数点，依赖方向定死（`ContextManager` 不知道 coordinator 存在，控制面靠 `decide` 回调注入）；兼容口径改为"除 §4.3 点名的两项外一致" | §4.2.1 |
| 2 | `CompactionDecisionContext` 缺 `tool_results_to_clip`，与 §4.4.3 的逐 kind 取值表对不上 | 成立。已补为 `int \| None` | §4.2.1 |
| 3 | token 事件缺逐路径的"有值 / 为空"契约：旧事件入口 3/4 今天就是 `null`（`_runner.py:1177`、`:1208-1213`），新事件只给了 full-success 的来源 | 成立。已补两张表——旧事件按 5 个入口、新事件按 `kind × status`——并写死统领原则：**本计划不新增任何一次 `estimate_tokens` 调用** | §4.2 |
| 4 | "上面五种 `skipped`"实际只有四种 | 成立。已改为"四种，其中三种每 iteration 重复" | §4.2 |

**方法备注。** 第 1 条是**所有权在两节之间漂移**：§4.3 把计数器判给 `ContextManager`，§4.2.1 又把"计数"
判给 coordinator——两句话分开读都成立，合起来才露馅。**同一份资源在不同章节被授予两次时，要把两处并排
读一遍。** 第 3、4 条则是同一类粗心：写"五种"之前没数表格的行；写 token 口径之前没把五个入口今天各传什
么列出来。**引用一张表就把它数一遍；写一个字段的契约就把它的每条生产路径列一遍。**

**另记一条本轮的操作事故。** 本轮 zh 的补丁又一次按行号打偏——`can_provide_summary` 实际在 469 行而不是
461 行，结果把 `kind: CompactionKind` 覆盖掉了，同时 402-414 的替换多吃了一行、把"熔断查询不在 prepare
里"的段首吞掉。两处都在提交前的结构复查里发现并修复。这是 rev 3 记过的同一个坑第二次发作，教训升级为：
**按行号改文档时，改完必须回读被改区块的完整上下文，而不只是回读改动前后各一条。**

### rev 7——3 条

第七轮提了 3 条，**全部成立**。前两条都指向 rev 6 自己引入的东西：一个只写了名字没写定义的类型，和一处
与 §4.2 硬约束打架的字段取值。

| # | 条目 | 复核结论 | 落到哪里 |
|---|---|---|---|
| 1 | `_run_compaction` 的返回类型 `_CompactionRun` 全文无定义；且没说它是只管 `full` 还是覆盖三个 kind | 成立。已补 `_CompactionRun` 的六个字段 + 七行逐分支映射表，并定死 **只处理 `kind == full`**（另两个 kind 不调摘要器、不写 SQLite、不碰计数器，没有可共享之物），`PrepareResult` 因此保持两支 | §4.2.1 |
| 2 | 微压缩的 `CompactionDecisionContext.pre_tokens = estimate_tokens(messages)` 与 §4.2 的"不新增 token 估算"及"microcompact 事件 token 全 null"冲突 | 成立。微压缩今天唯一那次估算是 `messages_with_system`（`_compaction.py:46`），口径不对；另起 history-only 估算又是带内每 iteration 一次。已改为 `None`，由 `tool_results_to_clip` 承担决策信息 | §4.4.3、§4.2.1 |
| 3 | 场景覆盖仍写"计数归 coordinator" | 成立，中英文都有残留。已改为"三个计数点都在 `ContextManager._run_compaction` 里" | §6 |

**方法备注。** 第 1、2 条是同一种"改到一半"：rev 6 为了修所有权矛盾**新引入**了 `_run_compaction`，却
只写了它的签名，没写它返回什么、覆盖哪些 kind；同一轮又在 §4.2 立了一条"不新增 token 估算"的全局约束，
却没有回头检查 §4.4.3 那张早就写好的表是否违反它。**新引入一个类型，就要把它的定义、它的分支映射、它的
适用范围一次写完；新立一条全局约束，就要把全文既有的条目按这条约束过一遍。** 后者尤其容易漏——约束是新
的，被约束的文字是旧的，读新写的段落时它不在视野里。

**操作记录。** 本轮两份文档都先做了 pre-flight 行号断言（对每个目标行验证特征串再改），zh 侧零事故；en
侧仍有一处插入点落在段落中间（把"That last field exists…"那段切成两半），在改后回读整块时发现并修复
——rev 6 记的那条"改完回读整块"确实拦住了它。

### rev 8——4 条

第八轮提了 4 条（3 个 P2 + 1 个 P3），**全部成立**，且**全部指向 rev 7 自己新写的段落**。本轮无 P1。

| # | 条目 | 复核结论 | 落到哪里 |
|---|---|---|---|
| 1 | 两条短路径重新越过 §1 的分层边界：coordinator 自己产出 `PreparedMicrocompact` / `PreparedMinimalHistory` 并执行短变换，微压缩还要读私有的 `_microcompactable_indices` | 成立。选评审给的第一条路——**不修订 §1**（那条分层是地基，为两个两行变换破例不值当），改为给 `ContextManager` 加两对窄方法（`prepare_microcompact` / 既有的 `microcompact_messages`；`prepare_minimal_history` / `apply_minimal_history`），coordinator 只编排 | §4.2.1 |
| 2 | `_CompactionRun → CompactionOutcome` 的映射描述不成立：`counted_failure` 不是 Outcome 字段，`pre_msgs` / `post_msgs` 也不是 | 成立。改为**五个同名字段直接搬**；`counted_failure` 仅内部；`trigger` / `kind` / `reason` 由 coordinator 补；`pre_msgs` / `post_msgs` 只进事件 payload | §4.2.1 |
| 3 | 七行映射没覆盖"合法的 `provide_summary`"，且"正常完成"固定 `detail=None` 与 §4.4.2 声明的 `CompactionDecision.reason` 进 `detail` 冲突 | 成立。新增"宿主摘要合法 → 采用"一行，并给出 `detail` 的统一拼接口径（内部原因 + `; ` + `decision.reason`，都无则 `None`） | §4.2.1 |
| 4 | 分型表仍写 `PreparedMicrocompact(..., pre_tokens)`，与下方字段表的 `None` 不一致 | 成立。已改为 `prepare_microcompact()` → `tool_results_to_clip`、`pre_tokens = None` | §4.4.3 |

**方法备注。** 四条都落在 rev 7 新写的两段里，共同形状是**新写的段落没有回头对照它所依赖的既有约定**：
§1 的分层表、§4.2 的 `CompactionOutcome` 字段清单、§4.4.2 的 `CompactionDecision.reason` 语义——三处都
是本文档自己早就写死的东西，而新段落引用它们时凭记忆而非回读。**写一段新设计时，凡是它要对接的既有条
目，都要把那一节重新打开读一遍再落笔**，尤其是自己几轮前写的——那种"我知道那里写了什么"的确信，恰恰是
最不该信的。

**操作记录。** 本轮的 pre-flight 行号断言在 zh、en 各拦住一次偏移（分型表行实际在 676 而非 674；en 段落
结束行是 564 而非 565），两次都在改动前失败、当场纠正，没有产生需要事后修复的破坏——这是 rev 6 / rev 7
两轮事故之后加的护栏第一次完全兑现。

### rev 9——2 条

第九轮提了 2 条 P2，**全部成立**，两条都是**所有权在同一节内自相矛盾**，且都由 rev 8 引入。本轮无 P1。

| # | 条目 | 复核结论 | 落到哪里 |
|---|---|---|---|
| 1 | `detail` 还不是"同名同义、直接搬"：rev 8 让 coordinator 追加 `decision.reason`，但真正调用 `decide`、持有 `CompactionDecision` 的是 `_run_compaction`；紧接着又说 `detail` 直接搬入 Outcome | 成立。改为 **`_run_compaction` 在返回前拼好终值**；并把 `decide` 的内涵说死（coordinator 合成的闭包，两层控制面都在里面，返回一个合并后的 `CompactionDecision`） | §4.2.1 |
| 2 | `counted_failure` 的用途重新侵入熔断状态所有权：既说 coordinator 据它决定探针计数与 `/context` 展示，又说状态与展示真相源都在 `ContextManager`、coordinator 不碰计数器 | 成立。**直接删掉该字段**——计数与复位已在 `_run_compaction` 里做完，coordinator 读 `status` 与 `compaction_circuit_open`（`:423`）足以执行探针策略，`/context` 一直走 `get_usage_stats()`（`:1223`），从不经过返回值 | §4.2.1 |

删掉 `counted_failure` 之后 `_CompactionRun` 恰好只剩五个字段，与 `CompactionOutcome` 一一对应，"直接搬"
这句话第一次字面成立；两个类型仍然分开，是因为 Outcome 还要带 `trigger` / `kind` / `reason` 三个
`_run_compaction` 不知道的东西。

**方法备注。** 两条是同一种错：**给一个字段写用途时，没有回头确认写用途的那一层拿不拿得到它。**
`decision.reason` 的持有者是 `_run_compaction` 而不是 coordinator；熔断计数的真相源是 `ContextManager`
而不是任何返回值。**写"由 X 据此做 Y"之前，先确认 X 手上真的有那个值，且 Y 不属于别人的所有权。** 这也
是连续第三轮出现"所有权漂移"——rev 6 是计数器、rev 8 是历史改写、rev 9 是 `detail` 与熔断展示。三次都发
生在**为修上一条而新写的段落**里：修补丁本身最容易越界，因为写它时注意力全在被修的那一处。

### rev 10——2 条

第十轮提了 2 条 P2，**全部成立**，两条都由 rev 9 引入。本轮无 P1。

| # | 条目 | 复核结论 | 落到哪里 |
|---|---|---|---|
| 1 | `cancel` 分支的内部 `detail` 已经是合并后的 `decision.reason`，再过一次拼接规则会变成 `reason; reason` | 成立。把 `cancel` 行的内部原因设为 `None`，让统一拼接规则自然产出 `decision.reason` 单独一份——**不**为它开例外 | §4.2.1 |
| 2 | `_run_compaction` 的触发元数据流自相矛盾：签名只有 `is_auto` / `reason`，而 `prepare_compaction`、`PreparedCompaction`、`CompactionDecisionContext` 三处都要 `trigger` / `kind` / `reason`，同时又声明它"不知道 trigger" | 成立 | §4.2.1 |

第 2 条采纳评审给的第二条路并再进一步：**`trigger = "auto" if is_auto else "manual"`**——`trigger` 的词
表本来就是 `manual | auto`（§3.2），而三个调用点的 `is_auto` 与它一一对应（`_compaction.py:102` /
`_runner.py:1167` → `auto`，`cli/commands/compact.py:103` → `manual`），两者是同一件事的两种编码；
`kind` 恒为 `full`。"不知道 trigger"那句话已撤。

**再进一步的部分：中间类型 `_CompactionRun` 整个删掉，`_run_compaction` 直接返回 `CompactionOutcome`。**
一旦承认 `trigger` / `kind` 可导出、`reason` 是入参，它就知道全部八个字段，rev 9 给"为什么要两个类型"写
的理由随之作废。而那层字段搬运的账很清楚：**rev 7 / 8 / 9 连续三轮，每轮都在它上面产生一个缺陷**——rev 7
映射写错、rev 8 `detail` 所有权错、rev 9 `counted_failure` 越权。删掉它比继续维护映射表划算。

**方法备注。** 第 1 条是**同一个值被两条规则各处理一次**：cancel 行把 `decision.reason` 当内部原因写死，
拼接规则又要求追加 `decision.reason`——两条规则各自都对，叠起来就重复。**新增一条"统一规则"时，要把所有
既有的特例行拿来跑一遍这条规则，看会不会双重生效。** 第 2 条是**一个事实在四处各写了一遍而没有对账**：
签名、`prepare_compaction` 的参数、`PreparedCompaction` 的字段、`CompactionDecisionContext` 的字段。真正
的教训是第 2 条引出的那个更大的判断——**当一个中间层连续三轮都在同一处出错，问题多半不是每次都手滑，而
是这一层本身不该存在。**

### rev 11——3 条

第十一轮提了 3 条 P2，**全部成立**，三条都是 rev 10「删掉 `_CompactionRun`、`_run_compaction` 直接返回
`CompactionOutcome`」这一步留下的尾巴。本轮无 P1。

| # | 条目 | 复核结论 | 落到哪里 |
|---|---|---|---|
| 1 | `CompactionOutcome` 的构造所有权自相矛盾：§4.2.1 已写明 `_run_compaction` 直接构造并返回，阶段表与后文仍三处声明由 coordinator 构造 | 成立，且实为**四**处：§1 分层表、§4.2.1 阶段表的 coordinator 行、依赖方向段、`commit_compaction` 收尾句 | §1、§4.2.1 |
| 2 | `decide` 可以为 `None`，两个 `PrepareRejected` 分支又在调 `decide` **之前**就返回，而拼接规则要求所有结果都拼 `decision.reason` | 成立。改为 `decision_reason: str \| None = None` 起手，只有 `decide` 真被调用且返回可用决策才赋值 | §4.2.1 |
| 3 | `_run_compaction` 要求必填 `reason`，但保持旧签名的 `compress_messages(messages, is_auto=True)` 没有这个参数，调用关系只写了 `decide=None` | 成立 | §4.2.1 |

**第 1 条按 kind 分工定死，不是"谁都能构造"。** `kind == full` 由 `_run_compaction` 构造——它手上就是全
部八个字段；闸门短路、`microcompact`、`minimal_history` 三类由 coordinator 构造，因为**它们根本不进
`_run_compaction`**（§4.4.3）。三个 kind 共享的是 `CompactionOutcome` **契约**，不是同一个构造者。**事
件仍一律由 coordinator 发**，这样"发不发事件"只有一处判据。

**并且补上了一条本该在依赖方向那句之前就写的前置：共享类型必须住在中立模块。** `_run_compaction` 既然
要 `return CompactionOutcome(...)`，`decide` 的入参出参又是 `CompactionDecisionContext` /
`CompactionDecision`，这几个类型只要定义在 coordinator 模块里，"`ContextManager` 不 import coordinator"
当场作废。落法：新建 `agentao/compaction/` 包，`types.py` 只装类型且只 import 标准库，`coordinator.py`
装 coordinator；外加两条约束（`__init__.py` 不 re-export coordinator，否则 `agentao.host` 的再导出会撞
上导入分层第 5 条 `tests/test_import_layering.py:471`；四个 `Prepared*` 私有快照不进 `types.py`）。

**第 3 条按评审给的映射定死，但补了一个它没提的时序条件。** `is_auto=True` → `compression_threshold`、
`is_auto=False` → `manual_cli` 是对的，可**入口 3（`_runner.py:1167`）今天正是靠 wrapper 的默认
`is_auto=True` 在跑**（§2 表第 3 行），而它真实的 `reason` 是 `api_overflow`（`_runner.py:1161` 的 hook
派发就是这么写的）。所以这条映射只有在"PR-4 把入口 2 / 3 / 5 全部迁到 coordinator"作为**同 PR 前提**时
才自洽，否则入口 3 会自报 `compression_threshold`、与自己刚发的 hook 载荷打架。已写进 §4.2.1。（**rev 12
已把归属改为"入口 3 与 legacy 映射在 PR-3 内原子完成"**——判断本身成立，挂错了 PR。）

顺带修掉两处 rev 10 的残留：两份文档里 legacy wrapper 那条都还写着"返回 `run.messages`"，而 `run` 就是
被删掉的 `_CompactionRun`；英文稿 `counted_failure` 那条 bullet 有一句 "then said the coordinator uses
it" 重复（评审已点出）。

**方法备注。** 三条同源：rev 10 那一步只改了**被删类型所在的那一段**，没有回头扫"谁构造 Outcome""谁提
供 `reason`""`run.` 这个名字还出现在哪"这三条**跨段落的事实**。规则写成：**删掉或合并一个类型之后，要
把它的名字、它承担过的职责、以及它当过唯一来源的每个字段，各自全文 grep 一遍**——`_CompactionRun` 这个
名字我确实 grep 了（所以正文只剩两处有意的 retraction），但"构造 Outcome"这个**职责**和 `run.messages`
这个**用法**都没有跟着 grep。名字好搜，职责不好搜，而这轮 3 条里有 2 条正是职责。

### rev 12——1 条

第十二轮提了 1 条 **P1**，**成立**。本轮无其他条目。

| # | 条目 | 复核结论 | 落到哪里 |
|---|---|---|---|
| 1 | PR-3 与 PR-4 的职责顺序无法成立：总表要求 PR-3 "五个入口经 coordinator 返回可信的 `CompactionOutcome`"，可 `full` 的 Outcome 现在唯一由 `_run_compaction` 构造，而 `_run_compaction` 与 prepare/commit 拆分都归在 PR-4 章节；rev 11 新增的段落又要求"PR-4 同 PR 迁移入口 2/3/5" | 成立。PR-3 拿不到 `full` 路径权威的 `status`，而 §6 又禁止从消息身份或条数反推——它满足不了自己的验收 | §1、§4 总表、§4.3、§4.2.1、§4.4.3 |

**按评审的建议把机械底座前移到 PR-3**，五项：中立 `types.py` + `coordinator.py`；prepare/commit 拆分与
`_run_compaction(..., decide=None)`；legacy `reason` 映射与 `apply_minimal_history`；五入口原子改接；
`CompactionOutcome` 与事件。PR-4 收窄为"只在已接通的路径上启用 `decide`"——命令型 hook 决策协议、
`compaction_controller=`、`provide_summary`、取消语义与抑制闩，**不迁移任何入口**。rev 11 那句"PR-4 同
PR 前提"随之改写为"入口 3 与 legacy 映射在 PR-3 内原子完成"，与总表不再冲突。

顺带把三处此前没有 PR 归属的条目定死，它们都会随这次前移换 PR：

- **§4.3 的第二处行为变化（摘要失败时不再 crystallize）落在 PR-3**，不在 PR-2 也不在 PR-4：prepare/commit
  的边界是 PR-3 划的，而"提交前不得有不可逆副作用"就是这条边界的定义。动机来自 PR-4，落地在 PR-3。
- **`apply_minimal_history` 归 PR-3**（入口 4 一改接就需要它），两个 `prepare_*` 归 PR-4（它们只为拼
  `CompactionDecisionContext` 而存在，PR-3 阶段没有读者）。
- **§1 里"所以它拆成 prepare/commit"那句补上落地 PR**——形状由 PR-4 的需求定，落地在 PR-3。

**接缝那一节当时没有搬家，只在节首加了一条归属横幅**，理由是它约 200 行、被全文交叉引用十余次，整体搬
迁的收益是目录更顺、代价是这份文档最近四轮反复出现的那类缺陷。当时标明这是权衡不是定论。**rev 13 已按维
护者要求整节搬到 §4.2 之下，编号 §4.2.1，原 §4.4.2–§4.4.5 顺延为 §4.4.1–§4.4.4。**

**代价已写进正文，不藏着：PR-3 因此是一个大 PR**，而且它独自承担 §4.3 点名的第二处行为变化。换来的是每
个 PR 都能独立验收；把拆分留在 PR-4，PR-3 就只能拿条数或消息身份糊一个 `status`——那正是本计划要修的缺
陷（§6 已把这条列为发布门槛）。

**方法备注。** 这条与 rev 11 同源，但更深一层：rev 11 我沿着"谁构造 Outcome"这个**职责**扫了正文，却没
沿着它扫**施工计划**。职责改了归属，PR 边界就跟着改——**§4 的总表是一份对全文各节的索引，任何一节里的
所有权变动都必须回到总表对一次账。** 更一般地：这份计划里"某某属于哪个 PR"从来没有被系统性标注过，rev 12
之前只有 §4.3 的一处"排序备注"在做这件事，所以每轮改动都在无声地重排施工顺序而没人核对。

**校验方法也补了一格。** 本轮的行号替换在两份文档里各留下一处**相邻重复行**（替换块把它自己覆盖不到的
两行又抄了一遍）。此前每轮跑的"引用集合逐条相同"是 **distinct 集合**比对，重复行不改变集合，两处都漏
了。改为**引用多重集**（计数）比对 + 相邻重复行扫描后，两处当场暴露并修掉。**按行号改文档，除了回读被
改区块，还要跑一遍不依赖我记忆的机械检查——而检查本身也要能发现"多了一份"，不只是"少了一份"。**

### rev 13——1 条指令

不是评审，是维护者的一条指令：**把 §4.4.1 整节搬到 §4.2 之下。** rev 12 把这处留成了"权衡不是定论"，现
在定论了。

- 接缝那一节整体移到 §4.2（PR-3）名下，编号 **§4.2.1**；原 §4.4.2–§4.4.5 顺延为 **§4.4.1–§4.4.4**。
- 全文 69 处 `§4.4.x` 交叉引用按同一张映射表一次性改完（`4.4.1→4.2.1`、`4.4.2→4.4.1`、`4.4.3→4.4.2`、
  `4.4.4→4.4.3`、`4.4.5→4.4.4`），两份文档各 69 处、映射后仍逐条对齐。
- 节首横幅**反过来写**：从"写在 PR-4 章节下、但属于 PR-3"改成"属于 PR-3，所以排在这里，不在 §4.4 下
  面"，并把归 PR-4 的三项列全（`decide` 真正被传入的那条路径、`cancel` 与两行"宿主摘要"分支、`detail`
  的拼接规则）。§4.4 章节开头加一句指回 §4.2.1。
- §1 那句"落地在 PR-3（§4 总表下方）"改成指向 §4.2.1；rev 12 记录里"整节没有搬家"那段标注为已被本轮推
  翻。

**目录结构现在与 PR 边界一致了**：§4.2 底下是 PR-3 的全部内容（含接缝），§4.4 底下只剩 PR-4 在已接通路
径上启用 `decide` 的那些东西。

**方法备注。** 这类整节搬迁的风险不在"搬"，在**编号漂移**——69 处引用里只要有一处按旧号留着，就是一处静
默的错指。所以没有分五轮 `4.4.5→4.4.4`、`4.4.4→4.4.3` 地替换（那样后一轮会吃掉前一轮的结果），而是**一
次正则、一张映射表**：单次扫描、互不干扰。搬完再跑 rev 12 补的那套机械检查（引用多重集 + 相邻重复行 +
标题数），确认两份文档仍逐条对齐。

**而检查确实抓到了一处。** 中文稿有一句写的是"只走 4.4.3 那一层"——**没有 `§`**，正则扫不到，搬完就成了
静默错指（新号应为 §4.4.2）。发现它的不是引用比对，是**按章节把两份文档的 `§x.y` 引用做多重集比对**：正
文里 en 多一处 §4.4.2、zh 少一处，一减就露。补一条规则：**整节搬迁后，除了替换带 `§` 的引用，还要扫一遍
不带 `§` 的裸章节号**——写作时省掉的那个符号，正是自动替换够不到的地方。修完，正文两侧各 89 处 `§` 引用
逐条相同。

### rev 14——关闭 §8 的第 1 条降级条件

维护者告知：**插件 marketplace 尚未建设。** 这触发的是 §8 第 1 条的复核，而不是它写的那次 grep。结论：
**该条件按原方法不可判定，就地关闭，§3.1 维持 P1。**

判断依据四条，都已对源码核过：

1. **前提没被满足。** 原条件要的是"已发布插件里没人用"的**实测零**；"marketplace 尚未建设"给的是**没有
   可测的总体**。这是两种不同的认知状态，只有前者支持降级。
2. **插件不经 marketplace 分发。** `PluginManager` 从 `~/.agentao/plugins`
   （`embedding/plugins/manager.py:96`）与 `<cwd>/.agentao/plugins`（`:100`）加载；marketplace 只是那个
   目录里的一层组织（`:297-312`）。所以今天真实的插件用户群，正是 §8 下一条已经承认 grep 覆盖不到的那
   一群——用一个够不到目标人群的方法测出"零"，不构成证据。
3. **方向是反的。** 若问的是"眼下损害有多大"，零采用 → 损害小 → 降级；但真正要决的是"契约缺陷什么时候
   修最便宜"，答案是**生态成型之前**。PR-1 是 matcher 的**行为变更**（`{"trigger": "auto"}` 今天命中全
   部五个入口、之后不再命中手动），现在做只影响本地手写规则，marketplace 上线后做就是对第三方配置的破
   坏性变更。
4. **有一份已发布文档现在是错的，与采用率无关。** `docs/releases/v0.4.4.md:131` 的
   `trigger | PreCompact only: auto (no manual site exists)`，括号里的理由在 0.4.4 成立、手动 `/compact`
   落地后失效。**既有测试不但抓不到，还把同一句过期前提写进了自己的名字和 docstring**：
   `test_pre_compact_trigger_always_auto_for_every_emit_site`
   （`tests/test_hooks_pre_compact_payload_claude_shape.py:60`，docstring 写着"no manual surface"），
   它的站点清单（`:62-66`）只有四个位置，不含 `manual_cli`。PR-1 要连这个测试一起改。

**还有一条务实的：这个标签在这里不影响任何决策。** PR-1 在依赖顺序里本来就是第 1 位，降不降级都第一个
做；P1/P2 只影响"要不要做"，不影响"什么时候做"。所以这条关掉即止，不再投入。

顺带记下 PR-1 的一项文档工作：给 `docs/releases/v0.4.4.md:131` 加一行**勘误**——沿用
`stop-precompact-hooks-plan` 的既有做法，加勘误横幅而不改写已发布的历史陈述。

**方法备注。** 这条的教训是**"没有数据"与"数据为零"必须分开记**。§8 原文把两者写成了一句话，于是"没建
marketplace"看上去像是自动满足了降级条件——而它其实连测都没测。规则写成：**一条以"实测为零"为前提的降级
条件，必须同时写明**"用什么总体测"**；总体不存在时，正确结论是"不可判定 + 维持原级别"，不是"零"。** 另一
半同样值得记：**同一份证据在不同问题下会得出相反结论**——"没人用"对"眼下损害"是减轻，对"何时动手最便宜"
是催促。写降级条件时要说清它服务的是哪一个问题。