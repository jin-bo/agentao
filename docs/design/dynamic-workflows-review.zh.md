# Claude Code Dynamic Workflows —— 引进评估

**状态：** 评审记录（**rev 5**，2026-07-19）。**结论：不引进，不设计替代形态，
且当前不改任何代码。** 全文无行动项；§3 记录一条已验证的事实（批次内同实例默认串行，
该默认是否可绕过尚未定义），并在 §3.5 记录**触发后**的候选实施计划（非立项承诺）。
**这是评审记录，不是已批准的方案。**

**读者：** 关注「多子代理并行扇出 / 中间结果不进上下文」的 agentao 维护者。

**配套：** 英文版 `dynamic-workflows-review.md` **待补**。

**相关：**
- `pi-mono-borrow-review.zh.md` —— **§3 的关键先例**。其 §「Per-tool `executionMode`」认定
  agentao 的 per-tool-instance 锁**已覆盖**「shell / write 在并行 batch 中互斥」的需求。
  本文 rev 3 据此推翻了前两版对该锁的「缺陷」定性。
- `code-mode-ptc-review.zh.md` —— Code Mode / PTC 决策记录（不立项）。其 §4 已把「整脚本
  一次性审批」列为实验前置；本文 §4 与之同向，不另开一条路线。
- `path-a-roadmap.zh.md` —— §2.3 把「跨平台强 sandbox」列为 ✗；§4 的需求门纪律
  （「没有 lighthouse 需求佐证就不开始」）适用于本文全部非行动项。
- `codex-goal-mechanism-review.zh.md` —— `/goal` 是**宿主拥有的顺序外循环**，与扇出正交。

**方法：** 通读 Anthropic 官方 workflows 文档与发布公告；grep 本地 Claude Code 源码副本；
对 agentao 与 `../software-agent-sdk/`（OpenHands SDK）逐项 grep。**所有被引为结论依据的
锚点均经人工读码复核**；仅供背景的锚点在附录 C.1 标注为未复核。

**锚点：** agentao `main`@`8266de1`（2026-07-19）。OpenHands `main`@`4fe56566`（2026-07-17）。
Dynamic Workflows 要求 Claude Code ≥ v2.1.154，2026-05-28 发布。

**方法学警告：** 本仓惯例是 grep 验证式通读同侪源码，**对 Claude Code 做不到** —— 本地
`../claude-code-source-code` 是 v2.1.88，早于 v2.1.154，`ls src/tools/ | grep -i workflow`
无匹配。故其机制描述来自文档与运行时契约，**出处已标到句子级**（附录 A）。

**修订记录：**
- **rev 1** —— 四层分解；层 4 以「Python 宿主做不到」否决；§3 定性为「缺陷」。
- **rev 2** —— 补入 OpenHands 对照；层 4 理由改为「取舍未决」。
- **rev 3** —— **退回精简**。修正三处实质错误：(a) 层 2 的现状判断（agentao 已有子会话
  隔离，§2）；(b) §3 的定性与修复路线（非缺陷，且原修复路线找错了状态）；(c) rev 2 在
  §4 重犯了 rev 1 的二元错误（「唯一形态」）。删除 rev 2 的 §5.1a，压缩 §4 与开放问题。
- **rev 4** —— 小修。(a) §3.2 **收窄适用范围** —— 锁是 `execute_batch()` 的局部变量、
  每批次新建，故只是「批次内安全默认」，rev 3 写成「Tool 并发契约」范围过宽；
  (b) §3.4 **审计范围扩充**至共享工具实例 / `BackgroundTaskStore` / 回调并发 / 权限提示，
  并据 `_wrapper.py:461-464` 证明「复制 wrapper 实例」不成立；删除自相矛盾的「3 行」估算；
  (c) §3.4 **从「唯一行动项」降为「触发后的决策顺序」**，与全文「不提前设计」一致；
  (d) 精简：§5 三条留一条，删除未实测的沙箱逃逸疑点，修正 `_safe_globals()` 表述。
- **rev 5** —— 新增 §3.5「候选实施计划（触发后）」，含基线测试、按严重性排序的审计表、
  端到端验收标准与**非目标清单**。**明确不是立项承诺。** 只记顺序、不记形态 —— 形态由
  审计结论决定。§3.4 的审计表移入 §3.5 第 2 步并重排序（共享工具实例升为第 1 位）。

---

## TL;DR

> **1. 不引进 Dynamic Workflows，也不设计 workflow DSL / runtime / 沙箱 / resume / TUI。**
> 需求门未触发（`path-a-roadmap §4`），且**无论采用哪种实现路线**（进程内 Python、独立
> JS 运行时、OS 隔离、声明式计划）都同此结论 —— 不需要先选路线再决定做不做。
>
> **2. 当前不改任何代码。** 同定义并发在批次内被默认串行，且该默认是否允许绕过**尚未
> 定义**（§3.2）—— 这是一条**记录在案的事实**，不是行动项。若将来真实用例要求它，
> §3.5 给出候选实施计划（顺序，非立项承诺）。
>
> **3. 出现「大规模扇出导致父上下文膨胀」的真实用例后**，再评估宿主侧批处理 API。
> 在此之前不加公开 fan-out API、聚合器、缓存/resume，也不重开 `/goal` 预算决策。

| 层 | 能力 | agentao 现状 | rev 5 判断 |
|---|---|---|---|
| 1 | 并发子代理扇出 | 批次内同实例默认串行（§3）| **默认未分化**，非缺陷；不改 |
| 2 | 中间结果不进上下文 | **PARTIAL** —— transcript 已隔离，批量结果未隔离（§2）| 等真实用例 |
| 3 | 结果缓存 + resume | ABSENT | 不设计 |
| 4 | 模型写编排代码 | ABSENT | 不设计（§4）|

---

## 1. Dynamic Workflows 是什么

一句话：**把编排计划从上下文窗口搬进代码。** Claude 为任务写一段 JavaScript，独立运行时在
会话之外执行；中间结果存在脚本变量里，只有最终答案回到会话。

官方对比表的分界很清楚：subagents / skills / agent teams 都是「Claude 逐轮决定下一步」且
结果落在上下文窗口；workflows 是「脚本决定」，结果落在脚本变量。

事实清单与**逐句出处分级**见附录 A。

---

## 2. 层 2 的现状：transcript 已隔离，批量结果未隔离

**rev 1/rev 2 把层 2 记为 ABSENT，这是错的。** agentao 的子代理早已不回传 transcript：

- `agents/tools/_wrapper.py:606` —— `result = sub_agent.chat(...)`，子代理跑在独立
  `Agentao` 实例上。
- `:653` —— `return result, stats`。
- `_format_result` —— 只拼「result + 一行 stats footer」。

**父会话拿到的是最终字符串，不是子代理的执行历史。** 这与 OpenHands
`task/manager.py:359-363`（仅取 `get_agent_final_response()`）是同一姿态。

**因此 rev 2 §5.1a 的论证无效并已删除** —— 它用 OpenHands 证明「层 2 成本低于估计」，
而它证明的那件事 agentao 本来就有。

**真正缺的是另一件事：** 扇出的 N 个**最终结果**仍逐条作为 tool result 落进编排模型的
上下文。Dynamic Workflows 的增量正在此处 —— 批量结果留在脚本变量，只有聚合结果回到上下文。

**这两件事不能互相佐证。** 子会话隔离便宜且已有；批量结果隔离需要一个持有结果的编排层
（脚本、宿主 API 或聚合器），成本完全不同。**在出现真实的父上下文膨胀用例前，不设计它。**

---

## 3. 层 1：这不是缺陷，是批次内安全默认未分化

### 3.1 现象（已复核）

模型被明确教导并行发工具调用（`prompts/sections.py:201`），批量执行器也开了线程池
（`runtime/tool_executor.py:152`，`ThreadPoolExecutor(max_workers=8)`）。但：

- `tool_executor.py:126-130` —— 锁按 `id(plan.tool)` 建键。
- `tool_executor.py:312` —— 锁**包住整个工具执行**，含子代理的多轮 `_run_sync`。
- `agents/manager.py:157-179` —— **每个 agent 定义恰好一个 `AgentToolWrapper` 实例**。

结果：`agent_codebase_investigator` × 20 个 item **完全串行**；只有跨不同 agent 定义
（不同实例 → 不同锁）才真并行。**扇出最自然的形态退化为顺序执行。**

### 3.2 为什么「缺陷」的定性是错的（rev 4 收窄了适用范围）

`pi-mono-borrow-review.zh.md:50` 记录：agentao 的 per-tool-instance 锁**已经覆盖**
pi-mono 用 `executionMode: "sequential"` 解决的需求 —— 「两次并行的 `run_shell_command`
命中同一把锁、被串行化」。

**准确表述（rev 4 修正）：**

> **同一工具实例在单个模型工具批次内默认串行，这是有意保留的安全默认；
> 是否允许特定工具绕过该默认，尚未定义。**

rev 3 把它写成「承重的 Tool 并发契约」，**范围过宽**。锁字典是
`execute_batch()` 内的**局部变量**，每次调用新建（`tool_executor.py:125-130`，
注释即「Build per-batch locks」；docstring 亦限定「within this batch」）——
**两个独立批次不共享这把锁**。因此 pi-mono 的先例只能证明「当前批次内串行行为覆盖了
shell / write 的需求」，不能证明它已上升为通用 Tool 契约。

即便收窄到批次内，结论不变：真实形状是**一个机制同时承载两个都想要的性质** ——
shell / write 类工具的批次内互斥（要）与 agent 类工具的并发（也要）—— 二者在同一实现上
冲突。这是**默认未分化**，不是 bug。前两版写成「现存能力没兑现承诺」，是没有回看既有
评审记录的结果。

### 3.3 rev 1/rev 2 提的修复路线是错的（已复核）

前两版称「锁可以只包住 `output_callback` 赋值」。**两处都不成立：**

1. **`AgentToolWrapper` 不暴露公开 `output_callback` 属性** —— 只有构造期注入的
   `self._output_callback`（`_wrapper.py:182,205`，转发给子代理于 `:530`）。故
   `tool_executor.py:313` 的 `hasattr(tool, "output_callback")` 对它是 **False**：
   **锁的自述理由根本不覆盖 agent 工具。** 且该私有 callback 是构造期注入、非每调用设置，
   **不是竞态源**。
2. **真正的每调用可变状态是 `_wrapper.py:220` 的 `self._cancellation_token`**，
   注释写明「Set by ToolRunner just before execute() to propagate the per-turn token」。
   两个并发调用会在它上面互相覆盖。

**所以原修复路线既没抓住问题，也修不了它。**

对通用工具而言，锁包住「赋值 + 执行 + 清理」全程是**正确的** —— callback 必须在整个调用
期保持调用级绑定，否则并发调用会互相覆盖、清理，导致流式输出关联到错误的 `call_id`。
**不能简单收窄这把锁。**

### 3.4 当前不改代码；且隔离层不在 wrapper 上

**rev 4 修正：rev 3 把「答契约问题」列为唯一行动项，这仍是提前设计扩展点。**
本文已判定：不是 bug、无 lighthouse 需求、不引进扇出 API。在这三条之下，
**现在不需要回答「声明位还是特例」** —— 那是需求出现后的第一个岔路（§3.5 第 3 步），
不是当下的作业。

> **当前动作：不改代码。** 仅记录一条事实：同定义并发在批次内被默认串行，
> 且该默认**是否允许绕过尚未定义**（§3.2）。

**共享工具实例使「复制 `AgentToolWrapper` 实例」不成立：**
`_wrapper.py:461-464` 把 `self._all_tools` 中的工具对象**原样**注册进子代理的
`scoped_registry`（不是副本）。而每个子代理自己的 `execute_batch()` 会**新建**一份
per-batch 锁字典（§3.2）。故两个并发子代理各持独立锁字典、却指向**同一批工具实例** ——
内层工具之间**完全没有串行**。**隔离必须在工具实例层解决**，复制 wrapper 解决不了。
这一条决定了下面第 2 步的排序。

### 3.5 候选实施计划（触发后；**不是立项承诺**）

> **本节不是立项承诺。** 没有真实的同定义并发需求与验收用例时，**不进入实现**。
> 本节只记录**顺序**（先做什么），不记录**形态**（做成什么样）—— 形态由第 2 步的审计
> 结论决定，不在此预设。

1. **建立基线测试** —— 证明同一 `AgentToolWrapper` 的两个调用当前串行；并发范围限定在
   单个模型工具批次内。
   *（`ThreadPoolExecutor(max_workers=8)` 是整批次共享、覆盖所有工具的既有默认
   （`tool_executor.py:152`），会自动适用 —— 它是**默认值描述，不是已定的验收标准**。
   8 个并发的完整多轮子代理与 8 个并发文件读，资源与账单画像不同，未测过。）*

2. **完成并发安全审计**（按严重性排序，**不预设修法**）：

   | 序 | 面 | 锚点 | 待答问题 |
   |---|---|---|---|
   | 1 | **共享工具实例** | `_wrapper.py:461-464` | 隔离层放在哪？此项决定其余各项是否还有意义 |
   | 2 | wrapper 调用级状态 | `_wrapper.py:220` `_cancellation_token` | 能否改为调用局部？必要但**远不充分** |
   | 3 | 共享 `BackgroundTaskStore` | `agents/bg_store.py:63` | 跨并发子代理的注册表争用 |
   | 4 | 回调并发进入 | `_confirmation_callback` / `_step_callback` / host emitter | 交叉输出、事件错序 |
   | 5 | 交互式权限确认 | —— | 能否并发进入？**不能则确认过程保持串行** |

3. **选择形态** —— 默认安全的 opt-in：普通工具继续按实例串行，仅经审计的
   `AgentToolWrapper` 允许并发。**采用能力声明还是局部判断，由第 2 步结论决定，此处不预选。**

4. **端到端回归测试** —— 两个**真实**子代理同时运行（非模拟任务的耗时重叠），验收：
   结果与 `call_id` 不串线；流式事件、取消、异常彼此隔离；一个子代理失败不影响另一个的结果。

5. **明确非目标**（触发时不得顺带长回来）：
   - 不增加公开 fan-out API，不做结果聚合器；
   - 不做缓存 / resume、DSL、进度 TUI；
   - 不处理后台线程池（`_wrapper.py:761`，§5，应单独立 issue）；
   - 不重开 `/goal` 预算决策。

---

## 4. 层 4：不设计，不必先选路线

模型生成编排代码有多条实现路线，**都存在**：

| 路线 | 代表 | agentao 侧成本 |
|---|---|---|
| 进程内 Python + AST 校验 + 审批 | OpenHands（附录 C.2）| 放弃隔离边界，改用审批门 |
| 独立 JS / QuickJS 受限解释器 | opencode（`code-mode-ptc-review` 附录 F）| 新增运行时依赖 |
| OS / 容器隔离 | —— | `path-a-roadmap §2.3` 已列 ✗（跨平台） |
| 非图灵完备的声明式计划 | —— | 无沙箱问题，丢图灵完备 |

**rev 1 说「Python 做不到」，rev 2 说「唯一形态是批准执行生成的 Python」—— 两次都是二元
错误。** 上表任一行都足以否证。rev 2 尤其自相矛盾：它自己在后文提了声明式 JSON 计划。

**rev 3 的处理：不在路线之间选。**

> **agentao 当前没有「模型生成编排代码」的明确需求。无论采用哪条路线，暂不设计。**

需求门未触发时，路线选择是不必要的前置工作。若需求出现，
`code-mode-ptc-review.zh.md` §4 的五项实验前置是现成的验证清单。

**关于安全边界的一条事实记录**（供将来参考，非当前结论）：能力安全（capability security）
by construction 是 JS 绑定面的性质，不随语言迁移到 Python。OpenHands 接受这一点并改用
审批门承载风险，其工具描述逐字写着「Treat running a workflow as approving generated code
execution」（`workflow/definition.py:132-133`）。这不改变本节结论，只说明**若**将来走
Python 路线，代价是什么。

---

## 5. 一条独立观察（非结论依据）

**后台代理线程无池、无上限。** `_wrapper.py:761` 每个后台代理一条 `threading.Thread`。
与前台工具批次并发**不是同一条路径**，不应因形状相似而并入 §3；此处仅作记录，
以免该已验证事实随本文结论一并丢失。**不是本文的行动项。**

（rev 3 另记的两条 —— OpenHands 的非正常终态处理、`max_budget_per_run` 与 `/goal` 预算的
相反决策 —— 与本文最终决策无关，rev 4 已删除。前者锚点见附录 C.3。）

---

## 6. 结论

> 1. **不引进 Dynamic Workflows，也不设计 workflow DSL / runtime。**
> 2. **当前不改任何代码。** 同定义并发在批次内被默认串行、且该默认是否可绕过尚未定义 ——
>    这是**记录在案的事实**，不是行动项。触发后的候选实施计划见 §3.5
>    （非立项承诺）。**这不是 workflows 项目。**
> 3. **只有出现明确的「大规模扇出导致父上下文膨胀」用例后**，再评估宿主侧批处理 API。
>    缓存、resume、TUI、生成代码执行、JSON DSL 均不提前设计；`/goal` 预算不重开。

**本文状态：rev 5，未批准、未实现。**

---

## 附录 A：Dynamic Workflows 事实清单（逐句出处分级）

**A.1 —— 来源：公开 workflows 文档页**（可复核）

| 事实 | 值 |
|---|---|
| 最低版本 / 发布日期 | v2.1.154 / 2026-05-28 |
| 并发上限 | 16（CPU 核数少时更低）|
| 单次运行 agent 总量上限 | 1000（自述为 runaway-loop backstop）|
| 脚本语言 | JavaScript，顶层 `await` |
| 脚本能力 | **无 fs、无 shell**（「Agents read, write, and run commands」）|
| 脚本持久化 | 每次运行写入 `~/.claude/projects/<session>/` |
| 保存为命令 | `.claude/workflows/` 或 `~/.claude/workflows/` |
| 大运行警告阈值 | > 25 agents 或预计 > 1.5M tokens |
| 子代理权限 | 始终 `acceptEdits`，继承会话 allowlist |
| 运行中用户输入 | 不支持；仅权限提示可暂停 |
| resume 范围 | 仅限同一会话 |
| 内置 workflow | `/deep-research` |

**A.2 —— 来源：本会话内 Workflow 工具契约自述，公开文档页未载**（**不可外部复核**）

| 事实 | 值 |
|---|---|
| 核心原语命名 | `agent()` / `parallel()` / `pipeline()` / `phase()` / `log()` / `args` |
| 结构化输出 | `agent(prompt, {schema})`，校验在工具调用层 |
| resume 粒度 | 「最长未改前缀命中缓存，第一个改动点及其之后实时执行」|
| 确定性移除 | `Date.now()` / `Math.random()` 被移除 |

> **A.2 的使用限制：** 这些是运行时契约的逐字表述，**强于**公开文档页的承诺
> （后者只说「已完成且输入未变的调用返回缓存」）。**不得据 A.2 设计任何兼容层** ——
> 未公开承诺的内部机制随时可变。本文引用 A.2 仅用于描述机制，不用于支撑任何行动项。

**核查记录：** 本地 `../claude-code-source-code` 为 v2.1.88，`grep -i 'workflow\|orchestr'`
于 `src/tools/` **无匹配**，确认无参考实现可读。该副本保留了「之前」的状态：
`src/tools/AgentTool/prompt.ts:271` 教模型「一条消息里发多个 AgentTool 调用」实现并行 ——
即**模型驱动的并行，结果全进上下文**。

## 附录 B：agentao 承重锚点（均已人工复核）

```
agentao/runtime/tool_executor.py:126-130   tool_locks[id(plan.tool)] = threading.Lock()
agentao/runtime/tool_executor.py:152       ThreadPoolExecutor(max_workers=8)
agentao/runtime/tool_executor.py:312       with tool_locks[id(tool)]:   ← 包住整个执行
agentao/runtime/tool_executor.py:313       hasattr(tool, "output_callback") ← 对 wrapper 为 False
agentao/agents/manager.py:157-179          每个 definition 恰好一个 AgentToolWrapper
agentao/agents/tools/_wrapper.py:182,205   self._output_callback 构造期注入（非每调用）
agentao/agents/tools/_wrapper.py:220       self._cancellation_token ← 真正的每调用状态
agentao/agents/tools/_wrapper.py:530       转发 output_callback 给子代理
agentao/agents/tools/_wrapper.py:606,653   result = sub_agent.chat(...) → return result, stats
agentao/agents/tools/_wrapper.py:761       后台代理 threading.Thread，无池无上限
agentao/prompts/sections.py:201            "Call independent tools in parallel"
agentao/host/models.py:97-122              SubagentLifecycleEvent 已含血缘三元组
agentao/cli/goal_state.py:18-20            token 预算被刻意丢弃
docs/design/pi-mono-borrow-review.zh.md:50 per-instance 锁被认定为已覆盖 executionMode 需求
```

## 附录 C：OpenHands SDK 对照（证据附录）

**锚点：** `../software-agent-sdk` `main`@`4fe56566`（2026-07-17）。

**定位：这是借鉴，不是独立收敛。** `workflow` 工具引入于 `6fdc84f6`（2026-06-06），
在 Claude Code 发布（2026-05-28）**之后 9 天**，且沿用同名「dynamic workflow」。
**不得将两者原语的相似性当作独立收敛证据。** 其证据价值仅在于：一个 Python 宿主的同侪
判断该机制值得快速移植，并给出了 Python 侧的一种具体形态。

### C.1 原语对照（**未逐条人工复核**，仅供背景）

| Claude Code | OpenHands SDK | 锚点 |
|---|---|---|
| `agent()` | `wf.run_agent()` | `workflow/impl.py:103` |
| 扇出 | `wf.map_agents()` | `workflow/impl.py:139` |
| `pipeline()`（无 barrier）| `wf.pipeline()`（同样无 barrier）| `workflow/impl.py:191` |
| 综合阶段 | `wf.reduce_agent()` | `workflow/impl.py:225` |
| **resume / journal** | **无对应实现** | —— |

### C.2 沙箱实现（已人工复核）

```
workflow/impl.py:431        exec(compile(script, "<dynamic-workflow>", "exec"), _safe_globals(), namespace)
workflow/impl.py:380-385    AST 层封杀 dunder 名字 + dunder 属性（关掉 __subclasses__ 逃逸族）
workflow/impl.py:28-45      _UNSAFE_CALLS：eval/exec/getattr/setattr/open/__import__/compile/…
workflow/impl.py:459-498    _safe_globals()：显式传入受限 __builtins__（约 30 个白名单 builtins）
workflow/impl.py:342-348    作者主动记录的已知 bypass（x = wf; x._attr）
workflow/impl.py:437-446    _WORKFLOW_TIMEOUT_SECONDS = 3600，asyncio.timeout
workflow/impl.py:154-158    Semaphore(min(max_concurrency, self._max_concurrency)) ← 只能调低
workflow/definition.py:24-42    max_concurrency 默认 8，ge=1 le=64
workflow/definition.py:132-133  "Treat running a workflow as approving generated code execution."
```

（rev 3 曾记录两条**未实测**的沙箱逃逸疑点。它们不支撑本文任何结论，且评审稿不应承担
第三方仓库安全研究备忘录的职责 —— rev 4 已删除。）

### C.3 §2 / §5 引用的锚点（已人工复核）

```
task/manager.py:305-316     子代理独立 LocalConversation
task/manager.py:311         max_budget_per_run 传入子会话 → 预算被子代理继承
task/manager.py:314         delete_on_close=True
task/manager.py:336         sub_agent_llm.reset_metrics()
task/manager.py:359-363     仅取 get_agent_final_response()，从不回传 transcript
task/manager.py:364-368     非 FINISHED 终态报错并保留部分输出
task/manager.py:155-160     _evict_task：pause + close + 置空引用
delegate/impl.py:351-363    裸 threading.Thread，无信号量 ← 与 agentao _wrapper.py:761 同形
delegate/impl.py:41-45      max_children: int = 5
subagent/schema.py:248      max_budget_per_run: float | None（USD）
local_conversation.py:1841/2092/2270   三个预算检查点 → MaxBudgetReached
```
