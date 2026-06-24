# Codex `/goal` — 拆解 + Agentao 候选设计

**状态:** 两部分。**§§1–9 —— 机制拆解**:对**外部项目**(Codex)的拆解,2026-06-23 起草,
基于对 goal 斜杠命令、`ext/goal` 扩展 crate、协议/状态模型、引导(steering)模板的逐行核对
阅读(纯描述,不含建议)。**§§10–11 —— Agentao 候选设计**:探索性的关联说明(§10)+ 带
`/goal` 接口草拟的候选设计拆分(§11),两者均**明确标注为非已批准计划 / 未实现**。拆解部分
独立成立;候选设计中,**§11 的具体论断已对 `agentao/` 做 grep 核实**,而 **§10 保持探索性、
未经验证**;整个候选成为提案前仍需维护者签字。(若 §11 日后
升级为已批准计划,再拆成独立的实现计划文档 —— 在它仍是候选阶段时留在此处,以免割裂
EN/ZH 配对。)
**读者:** 研究长任务/自主续航设计的 Agentao 维护者;任何对 Codex 做反向审阅的人。
**配套:** `codex-goal-mechanism-review.md`(英文版)。
**相关:**
- `codex-reverse-review.md` —— 此前对 Codex 的反向审阅,同样秉持"看 Agentao 真正需要
  什么,而非照搬 Codex 的产品形态"的立场。
- `metacognitive-boundary.md` —— Agentao 自己的"可注入的每轮协议"构想;是与 Codex
  "steering 注入"概念上最接近的近邻。

**来源:** Codex 本地检出 `../codex`,`main`@`97dce078c5`(2026-06-23)。下文所有文件引用
均相对该树,且经逐行核对,除非显式标注为"推断"。

---

## 一句话概括

Codex 的 `/goal` 是一套**长任务自动续航机制**,作为**可插拔扩展**
(`codex-rs/ext/goal/`)实现,而非硬编码进 agent 内核。一个持久化的 `ThreadGoal`
(线程级、SQLite 落库、六态状态机)由三大支柱驱动:

1. **面向 agent 的工具** —— `get_goal` / `create_goal` / `update_goal`,其中 agent
   **只能**把目标标记为 `complete` 或 `blocked`(且受严格的、重度 prompt 工程约束的审计)。
2. **引导注入(steering)** —— 每轮把隐藏的模板化上下文片段
   (`continuation` / `budget_limit` / `objective_updated`)作为 `ResponseItem` 注入;
   objective 被当作不可信、XML 转义的数据。
3. **自动续航循环** —— `on_thread_idle → continue_if_idle →
   thread.try_start_turn_if_idle([continuation_item])`。线程一空闲、目标仍 `Active`,
   扩展就**自动发起新一轮 turn**并带上续航提示。这就是让 agent"停不下来"的引擎。

一个由信号量保护的**记账层**把 token 与墙钟增量计到当前目标头上,耗尽时自动翻转为
`BudgetLimited`,并在 turn **进行中**注入收尾引导。状态机写权限被干净切分:
**用户**(创建/暂停/恢复/清除/编辑)、**agent**(仅 complete/blocked)、**系统**(预算/用量上限)。

---

## 1. 三个架构层

| 层 | 位置 | 职责 |
|---|---|---|
| 协议/状态 | `codex-rs/state/src/model/thread_goal.rs` | `ThreadGoal` 结构 + `ThreadGoalStatus` 枚举,持久化到线程的 SQLite 库 |
| **扩展引擎(核心)** | `codex-rs/ext/goal/`(整个 crate) | `GoalExtension` 挂线程/turn 生命周期,驱动自动续航、记账、工具、引导 |
| TUI/UI | `codex-rs/tui/src/...` | `/goal` 斜杠命令、菜单、显示格式化、超长 objective 落盘 |

承重的设计选择:**目标逻辑不硬编码进 agent 循环。** 它是一个通过生命周期钩子
(`on_thread_idle`、`on_turn_start`、`on_token_usage`、`on_tool_finish` …)接入的可插拔
扩展(`ext/goal`)。agent 内核对"目标"无感知。

---

## 2. 数据模型

`codex-rs/state/src/model/thread_goal.rs:60`

```rust
pub struct ThreadGoal {
    pub thread_id: ThreadId,
    pub goal_id: String,            // UUID
    pub objective: String,          // 用户给的目标文本
    pub status: ThreadGoalStatus,
    pub token_budget: Option<i64>,  // 可选 token 上限
    pub tokens_used: i64,           // 计到本目标的 token 累计
    pub time_used_seconds: i64,     // 墙钟秒累计
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}
```

`ThreadGoalStatus`(`thread_goal.rs:14`)是六态枚举 ——
`Active | Paused | Blocked | UsageLimited | BudgetLimited | Complete`,带辅助方法
`is_active()` 与 `is_terminal()`(后者对 `BudgetLimited | Complete` 为真,
`thread_goal.rs:39`)。目标**仅对持久化线程存在**(临时线程拒绝目标,
`thread_goal_actions.rs:358`)。

---

## 3. 支柱 A —— 面向 agent 的工具

`codex-rs/ext/goal/src/spec.rs` 定义三个 Responses-API 工具:

- `get_goal()`(`spec.rs:13`)—— 读状态/预算/已用 token 与时间/剩余预算。
- `create_goal(objective, token_budget?)`(`spec.rs:25`)—— "仅当显式被要求时创建目标……
  不要从普通任务臆造目标。……若存在未完成目标则失败。"
- `update_goal(status: "complete" | "blocked")`(`spec.rs:60`)——
  **agent 只能标记 complete 或 blocked。**

关键的**控制权切分**:agent **不能**自己 pause/resume/budget-limit/usage-limit —— 那些
只能由用户或系统改。工具描述里有大量 prompt 工程以阻止 agent 钻状态机的空子:
`blocked` 只有在"同一阻塞条件连续 ≥3 个 goal turn 复现"时才合法(`spec.rs:66`);也不准
仅因"预算快用完"就标 `complete`(`spec.rs:81`)。

---

## 4. 支柱 B —— 引导/续航注入

`codex-rs/ext/goal/src/steering.rs` 将三个内嵌模板之一渲染为**隐藏上下文片段**,作为
`ResponseItem` 注入:

```rust
fn goal_context_input_item(prompt: String) -> ResponseItem {
    ContextualUserFragment::into(InternalModelContextFragment::new(
        InternalContextSource::from_static("goal"), prompt))
}
```

模板(`codex-rs/ext/goal/templates/goals/`):

- `continuation.md` —— 一大段**"完成审计 + 受阻审计 + 保真度"**提示,核心目的是
  **对抗 agent 把目标缩水成最容易通过的子任务**("Do not substitute a narrower, safer,
  smaller … solution because it is more likely to pass current tests")。还要求 agent
  保持 `update_plan` 同步,并在逐条核验前把"完成"视为**未经证明**。
- `budget_limit.md` —— "预算到顶;收尾、总结进展、给用户清晰的下一步。"
- `objective_updated.md` —— "用户改了目标;切换到新目标。"

**防 prompt 注入加固:** objective 被 XML 转义(`steering.rs:124`)、包进
`<objective>` / `<untrusted_objective>` 标签,且每个模板都明确声明 objective 是
*用户数据,不是更高优先级指令。*

---

## 5. 支柱 C —— 自动续航循环(真正的"编排")

这是引擎。`codex-rs/ext/goal/src/extension.rs:154`:

```rust
fn on_thread_idle(...) {
    runtime.continue_if_idle().await   // 线程一空闲就触发
}
```

`codex-rs/ext/goal/src/runtime.rs:359` `continue_if_idle()`:

```rust
// 仅当工具可见且目标仍 Active:
let item = continuation_steering_item(&protocol_goal_from_state(goal));
thread.try_start_turn_if_idle(vec![item]).await;   // 自动开新一轮 turn
```

即:agent 干完一轮 → 线程空闲 → 扩展检查目标 → 若仍 `Active`,就**自动开新 turn 并注入
续航提示。** 于是 agent"停不下来" —— 它会一直推进,直到标记 `complete` / `blocked`、
触发预算/用量上限、或用户暂停/清除。一个 `goal_state_permit` 信号量(`runtime.rs:366`)
横跨"读目标—起 turn"窗口持有,防止外部 set/clear 与续航启动竞态。

`GoalExtension` 实现了完整的生命周期钩子面(`extension.rs`):
`on_thread_start / resume / idle / stop`、`on_turn_start / stop / abort / error`、
`on_token_usage`、`on_tool_finish`。

---

## 6. 记账与自动预算限流

`codex-rs/ext/goal/src/accounting.rs`:

- 双轨记账(每 turn token 用量 + 墙钟),由 `progress_accounting_lock` 信号量串行化
  (`accounting.rs:94`),防止并发的 tool-完成钩子重复记同一笔增量。
- token 增量公式:`input − cached + output`(`goal_token_delta_for_usage`,
  `accounting.rs:328`)。
- **Plan 模式的 turn 不计 token**(`account_tokens = !matches!(mode, Plan)`,
  `accounting.rs:80`)。
- 当 `tokens_used ≥ token_budget`:状态自动转 `BudgetLimited`,并在 turn **进行中**注入
  预算上限引导(`extension.rs:400 → runtime.inject_active_turn_steering(item)`),让 agent
  立即收尾,而非等下一个空闲周期。

---

## 7. 持久化细节 —— 超长 objective 落盘

`codex-rs/tui/src/goal_files.rs`。若 objective(展开粘贴文本/图片后)超过
`MAX_THREAD_GOAL_OBJECTIVE_CHARS`(`goal_files.rs:121`),就写到
`$CODEX_HOME/attachments/<uuid>/goal-objective.md`,而存进目标的 objective 字段变成一句
引用串:

```
Read the Codex goal objective file at <path> before continuing.
```

`objective_file_path()`(`goal_files.rs:157`)在反向解析时校验 UUID 与精确路径形状,因此
无法塞进任意路径来诱导读文件。

---

## 8. UI 与生命周期

`/goal` 注册(`codex-rs/tui/src/slash_command.rs`):`SlashCommand::Goal`(行 42)、
描述 "set or view the goal for a long-running task"(行 122)、`supports_inline_args`
(行 159)、`available_during_task` = true(行 226)—— 即**可在 turn 执行中调用。**

子命令分发(`codex-rs/tui/src/chatwidget/slash_dispatch.rs`):

| 输入 | 动作 |
|---|---|
| `/goal <objective>` | `SetThreadGoalDraft { mode: ConfirmIfExists }`(行 841) |
| `/goal clear` | `ClearThreadGoal`(行 757 → 787) |
| `/goal edit` | `OpenThreadGoalEditor`(行 759) |
| `/goal pause` | `SetThreadGoalStatus(Paused)`(行 767) |
| `/goal resume` | `SetThreadGoalStatus(Active)`(行 768) |
| `/goal`(裸) | 弹目标菜单 |

**替换前确认**(`thread_goal_actions.rs:364` `should_confirm_before_replacing_goal`):
只有 `Complete` 替换时**不弹**确认;**`BudgetLimited` 仍会弹**(尽管两者 `is_terminal()`
都为真),`Active / Paused / Blocked / UsageLimited` 也都弹。(这是对"两个终态都自动替换"
这一较粗读法的精确订正。)

菜单/显示(`chatwidget/goal_menu.rs`、`goal_display.rs`)展示状态、objective、已用时间
(`2h 30m`)、已用 token(`63.9K`)、预算;会话 resume 时,paused/blocked 的目标触发
"是否恢复目标?"提示。

---

## 9. 状态机与写权限

```
              用户 pause              用户 resume
   ┌────────────────────────────────────────────┐
   ▼                                              │
 Active ──agent update_goal(complete)──▶ Complete(终态)
   │  │
   │  └─agent update_goal(blocked, ≥3轮)──▶ Blocked(休眠;resume 重启审计)
   │
   └─系统(预算耗尽)──▶ BudgetLimited      系统(usage-limit-exceeded 轮次错误)──▶ UsageLimited
```

| 状态变更 | 谁能做 |
|---|---|
| 创建/暂停/恢复/清除/编辑 | **用户** |
| complete / blocked | **agent**(仅此,且受严格审计) |
| budget_limited / usage_limited | **系统**(自动) |

> **`UsageLimited` 很窄:** Codex 只把 `UsageLimitExceeded` 这一种轮次错误映射到
> `UsageLimited`(`ext/goal/src/extension.rs:306`);*其它*不可重试的轮次错误都映射为
> `TurnError → Blocked`(同文件 `:311`,注释说明这是为阻止自动续航空转、消耗 token)。
> 一般的限流本身**不会**触发 `UsageLimited`。

---

## 10. 对 Agentao 的探索性关联(未经验证 —— 由维护者裁定)

本节保持探索性 —— 其自身**不是**提案;在它之上的候选设计是 §11(已明确标注非批准)。仅
记录供 grep-first 评估的引子:

- Agentao 现有最接近的概念是**可注入的每轮元认知边界**
  (`metacognitive-boundary.md`)—— Codex 的 "steering" 正是"每轮注入一段由 host 控制的
  协议片段"的一个已落地的具体实例。Agentao 的边界是否该长出一个*目标形状*的默认值,是
  开放设计问题,而非已坐实的缺口。
- Codex 的自动续航活在 **host/app-server + TUI**,不在模型循环里。按 Agentao 的嵌入式
  harness 边界,等价的"持续朝目标推进 turn"的循环最自然应是 **host 的职责**,由 harness
  暴露"每轮注入 + 工具注入"原语(时间/轮次预算属 host 侧;token 记账则是未来的 harness
  原语,见 §11)—— 与反复出现的"有价值的内核往往已作为 host-contract 原语
  存在"这一结论一致。任何"存在真实缺口"的论断之前,都需照例在 `agentao/` 里 grep 验证。

在上述任何一条变成提案之前:用具体的 `file:line` 证据(或"无匹配")在 `agentao/` 里
证明缺口,并把痛点/优先级的裁定留给维护者。

---

## 11. 待评估设计选项 —— 三层拆分(非已批准计划)

**状态:** 仅候选形态,记录供评估。对 `main`(2026-06-23)做过 grep 验证。是否要建其中
任何一层由维护者裁定。**这不是承诺。**

**决策(2026-06-23,维护者):** Token 预算**暂不纳入范围**;goal 预算改用**时间/轮次**。
后果:下文那个唯一的 harness 缺口(token 用量暴露)**不再追求**,因此**第 1 层无需改动**
—— 拆分收敛为「CLI(参考 host)+ developer-guide」,且全部建在*现有*原语之上。仅当日后
明确要求 token 预算时再重启。

忠实的 goal 机制**不**应作为"goal" feature 烤进 harness 内核。它属于已经掌握 turn 节奏
的 **host**。候选拆分是**三层**,不是两层:

### 第 1 层 —— Harness(core):保持 goal-agnostic

harness 只欠**通用、与 goal 无关的原语**;"goal" 永不进核心。对照今天的 host 契约,大部分
已存在:

| host 需要的原语 | 今天有没有? | 锚点 |
|---|---|---|
| 驱动多轮 turn | ✅ `Agentao.chat()` / `arun()`,可在 host 循环里反复调 | `agentao/agent.py` |
| 每轮注入续航上下文 | ✅ host 拼进下一条消息 | `agentao/cli/input_loop.py:545`(CLI 已这么做) |
| 注入 goal 工具(`get_goal`/…) | ✅ host `extra_tools` 构造 kwarg | `agentao/agent.py:84` |
| 持久化 goal 状态 | ✅ host 侧(不属 harness) | —— |

也就是说,**时间/轮次** goal 循环所需的原语 host 全已具备;唯一缺的那项只对 *token* 预算才有意义:

**唯一的 harness 缺口 —— 以及决策为何绕开它:** token 用量没暴露在 `agent.events()` /
`agentao/host/models.py` 上,所以 codex 式的 **token 预算**需要补一个(goal-agnostic 的)
每轮用量信号。**按上述决策,token 预算不在范围内。** 时间/轮次预算**纯属 host 侧**
(`chat()` 调用之间量墙钟;host 自己数循环轮次),**零** harness 改动 —— 所以第 1 层不只
goal-agnostic,而是*完全不动*。(若日后想要 token 预算,这个用量信号就是唯一要加的原语,
所有 host 复用。)

### 第 2 层 —— CLI(参考 host):实现 `/goal` + 续航循环

Agentao 有三个 host / 前端面(in-process embedding、CLI(含 `agentao run`)、ACP;
`docs/design/embedding-vs-acp.md`);其中 **CLI** 是本设计天然的**参考** host。它把 Agentao
自己的 goal 编排实现为 **host 持有的外层循环**:

```text
while goal.active:
    resp = agent.chat(continuation_prompt)      # host 驱动,非插件 force_continue
    更新 goal 状态(已用时间、轮次)
    if complete | blocked | 超时间/轮次预算: break   # host 控制终止
```

这是把 `cli/input_loop.py:545` 已有的一次性先例(plan 模式后的自动续航)泛化。**明确不**
建在插件 `Stop`/`force_continue` 路径上 —— 那被硬上限 `_stop_reentry_cap`(默认 3,
`agentao/runtime/chat_loop/_runner.py:587`)卡住作为防失控护栏,且注入的是**可见** user
消息,两点都不适合持续追目标。host 持有的循环按设计**不设上限**(终止条件由 host 掌握)。

### 第 3 层 —— developer-guide:把 host 编排范式文档化

把 CLI 实现泛化为可复用的 **host 契约范式**(外层循环 + 状态 + 每轮注入 + 工具注入 +
经 host 侧时间/轮次记账的预算),用 CLI 当 worked example。

**位置 = part-4(host 契约),不是 part-5(扩展面)。** part-5 是
tools / skills / mcp / permissions / memory / system-prompt / plugin-hooks;host 契约在
part-4(`developer-guide/en/part-4/7-host-contract.md`、`2-agent-events.md`)。建议新页面:
`developer-guide/{en,zh}/part-4/8-orchestration-continuation.md`,按仓库约定 EN+ZH 双语,
并从 part-5 的 tool-injection 材料互链(与 §5.8 Host Tool Injection 同样的姿态 —— 与
`docs/design` 和 `a-api-reference` 保持同步)。

### 边界提醒

**别**把 "goal" 烤进 core。harness 只欠三个通用原语(驱动 turn / 注入上下文 / 注入工具);
时间/轮次预算属 host 侧、无需 harness 原语。(每轮用量观测原语是*未来、仅 token 预算才需*
的补充 —— 按上述决策不在范围内,今天不欠。)"goal" 是用这些拼出来的 **host 级产品概念**
—— 与反复出现的"有价值的内核 = host-contract 原语,不是 harness feature"这一结论一致。

### 变成计划前的待定项

1. ~~先定 token 预算是否在范围内。~~ **已决(2026-06-23):否 —— 仅时间/轮次预算,不改
   harness。**
2. ~~设计事件流 / `chat()` 返回上的用量信号。~~ **作为 (1) 的后果搁置;仅当日后要求 token
   预算时再议。**
3. 确认 `cli/input_loop.py:545` 是否就是要泛化的先例(还是另起新循环),以及 CLI goal
   状态该存哪。
4. 定义时间/轮次预算的接口面 —— **已草拟于下方 §11.1。**

### 11.1 接口面草拟 —— `/goal` 时间/轮次预算(待定项 ④)

**状态:** 供评估的草案;未批准/未实现。范围 = **CLI(参考 host)** 的命令面 + 状态模型 +
续航循环集成。无 token 预算(按上述决策)。`/goal` 是 CLI 斜杠命令,**不是 LLM 工具**;其它
host 照 developer-guide 自行实现命令面。

**A. 命令面**(对齐 `agentao/cli/help_text.py` 的 `/cmd [subcommand]` 风格):

```
/goal [subcommand|<objective>]            管理长任务目标(时间/轮次预算)
  /goal                                   显示当前目标(状态/objective/已用 时间·轮次/上限)
  /goal <objective>                       设定目标(已有未终态目标则需确认替换)
  /goal <objective> --for 30m             带时间上限
  /goal <objective> --turns 10            带轮次上限
  /goal <objective> --for 1h --turns 20   两者(先到者触发);--unbounded 显式不设限
  /goal budget [--for <d>] [--turns <n>]  改/设当前目标的上限;--clear 清除上限
  /goal pause | /goal resume              暂停 / 恢复(暂停期间时间不计)
  /goal edit                              重编 objective(保留状态与上限)
  /goal clear                             清除目标
```

旗标风格(`--for <duration>` / `--turns <n>`)沿用 `run.py` 的 `--max-iterations`(argparse)习惯。

**B. 预算语义 —— 两条轴,及关键区分:**

| 轴 | 含义 | 不可混淆 |
|---|---|---|
| `--for <duration>` | **累计 active 墙钟**上限。格式 `90s` / `30m` / `2h` / `1h30m` | 只计 **active** 时间;`pause` 期间**不计**(对齐 codex `time_used_seconds`) |
| `--turns <n>` | **续航轮次**上限 = 计入本目标的 host-loop `chat()` 次数;创建那次 = 第 1 轮 | ⚠️ **不是** `max_iterations` —— 后者管单次 `chat()` 内的工具调用循环。两者**正交**:goal 预算管*外层*续航,`max_iterations` 仍管每个*内层* turn |

可单设/同设/都不设;同设时**先到者触发**。

**C. 默认值(决策点):** 放弃 token 预算 ⇒ 没有成本天花板兜底,而 codex「不设限干到完成」
的模型已被明确否决(`force_continue` 本就钉死在 3 次防失控)。故草案**建议默认开启安全上限**
—— 取值/是否默认开由维护者定:

```jsonc
// .agentao/settings.json
"goal": {
  "default_max_turns": 25,         // 未给 --turns 时套用
  "default_time_budget": "120m",   // 未给 --for 时套用
  "enabled": true
}
```

两条轴防的是**不同**风险,且刻意定尺寸以免互相遮蔽(回想 `--for` / `--turns`
**先到者触发**,行 425):

- **`--turns` 是主失控护栏** —— 一个 turn 是一次完整 `agent.chat()`(内层循环上限
  `max_iterations`),所以 `25` 轮已是相当大的工作量;它的活是抓*卡死*的 agent,
  不是限制有用进度。
- **`--for` 只防墙钟病态**(工具挂起、无限等待),故设在轮次上限**正常完成点之上**
  (`120m`)。若时间默认等于或低于轮次完成点,在迭代慢的目标上(慢 `pytest` /
  `uv sync` / fetch)它会悄悄抢先于轮次护栏,使"两条护栏"形同虚设。

`/goal <obj> --unbounded` 显式退出默认上限。

**D. 状态模型**(持久化到 `.agentao/goal.json`,或挂当前 session —— 开放):

```python
@dataclass
class GoalState:
    goal_id: str                       # uuid
    objective: str
    status: GoalStatus                 # 见 E
    time_budget_seconds: int | None    # None = 时间不设限
    max_turns: int | None              # None = 轮次不设限
    time_used_seconds: int             # 累计 active(不含 paused)
    turns_used: int
    created_at: datetime
    updated_at: datetime
```

对比 codex:**删去** `tokens_used` / `token_budget`(决策);把 codex 的 `UsageLimited` +
`BudgetLimited` 合并为单个 `limit_reached`。

**E. 状态机与控制权:**

```
            user pause            user resume
   ┌──────────────────────────────────────────┐
   ▼                                            │
 active ──agent 标记 complete──▶ complete(终态)
   │  │
   │  └─agent 标记 blocked──▶ blocked(休眠;resume 重启)
   │
   └─host(时间或轮次到顶)──▶ limit_reached(终态,直到 user clear/edit/重设预算)
```

| 变更 | 谁 |
|---|---|
| 创建 / pause / resume / clear / edit / budget | **用户** |
| complete / blocked | **agent**(经 host 注入的 `update_goal` 工具 —— 见 **E.1**) |
| limit_reached | **host loop**(自动) |

**E.1 agent 的写入面(唯一注入工具)。** F 节里的 `agent_marked_complete_or_blocked()`
不是凭空而来 —— 它读的是单个 host 注入工具的效果。这是让循环*完整*(而非仅命令/预算)所需
的最小契约:

- **`update_goal(status: "complete" | "blocked")`** —— agent 对 goal 状态的**唯一**写入。
  经 host `extra_tools` kwarg 注入(第 1 层,`agent.py:84`)。**带守卫:** handler **仅当
  `goal.status == active` 时才成功**;在 `limit_reached` / `paused` / `complete` /
  `blocked` / 已清除之后,它是**no-op 并向模型返回错误结果**。这保护了终态 `limit_reached`
  (及 `paused`)不被收尾轮里发出的 `update_goal` 覆盖 —— 收尾轮时状态已是 `limit_reached`
  (见 §F)。成功调用时 handler 写 `goal.status`(host 侧)后返回;host 循环在每轮后读这个
  状态(即 `agent_marked_complete_or_blocked()` 检查)。对齐 codex `ext/goal/src/spec.rs:60`,
  去掉 token 上报文案;这个"仅 active"守卫**比 codex `budget_limit.md` 更严**(后者在预算
  触发后仍允许补一个 `complete`)—— 这是刻意简化,让终态除用户操作外不可变。
- **`get_goal()`**(可选)—— 只读自省(状态、上限、已用时间/轮次、剩余)。对齐 codex
  `spec.rs:13`。锦上添花,循环不依赖它。
- **无 `create_goal` 工具** —— 与 codex 不同,这里目标创建是**用户驱动**(`/goal <objective>`),
  agent 不自建目标。仅当日后需要 agent 自发目标时再加。

**写权限切分**(即把 §E 表精确化):**agent** 只能设 `complete` / `blocked`,且*只能*经
`update_goal` —— 不能 pause/resume/clear/重设预算,也不能设 `limit_reached`;**用户**掌
创建/pause/resume/clear/edit/budget;**host loop** 掌 `limit_reached`。姿态同 codex
(`spec.rs` 把 agent 限死在 complete/blocked)。codex `continuation.md` 里"`blocked` 须连续
≥3 轮"的严格审计是**提示层**的事,写在 `CONTINUATION_PROMPT` 内,不由工具强制。

**F. "到顶 → 收尾" 行为** —— host 在**每次续航之前**查预算:

```text
while goal.status == active:
    if goal.time_used >= time_budget or goal.turns_used >= max_turns:
        goal.status = limit_reached                    # 不再发普通续航
        agent.chat(WRAP_UP_PROMPT(goal))               # ← 只发一轮收尾
        break
    msg = original_user_msg if goal.turns_used == 0 else CONTINUATION_PROMPT(goal)
    t0 = now()
    resp = agent.chat(msg)                             # 内层仍受 max_iterations 约束
    goal.turns_used += 1
    goal.time_used  += now() - t0
    persist(goal)
    if agent_marked_complete_or_blocked():             # 即 agent 本轮调用了 update_goal(见 E.1)
        goal.status = complete | blocked; break
```

`WRAP_UP_PROMPT`(对齐 codex `budget_limit.md`,去掉 token 文案):「已达本目标的时间/轮次
预算。不要开新的实质性工作;总结进展、列出剩余工作或阻塞、给用户一个清晰的下一步。」收尾后
host **停止驱动**;状态停在 `limit_reached`,直到用户 `clear` / `edit` / `/goal budget` 重设。

**G. 需新增小工具:** duration 解析器(`90s|30m|2h|1h30m → 秒`)—— 树里**无**(grep 无
`parse_duration`);拒绝无单位数字;放 `agentao/cli/`。

**H. 边界防撞:**

| 已有 | 本接口 | 关系 |
|---|---|---|
| `max_iterations`(`run.py:69`、`transport.py:121`) | `--turns` | **正交**:内层工具循环 vs 外层续航轮数 |
| `force_continue`(`_runner.py:587`,上限 3) | host 外层 `while` | **不复用**:goal 走 host 循环,不走插件 Stop 路径 |

**待维护者拍板:**(1)默认值 —— **已定(§C):`25` 轮 / `120m`,默认开上限**;尚开放的是
上限"静默套用"还是"首次设目标时给一次性提示";(2)状态持久化落点(`.agentao/goal.json`
vs session);(3)旗标命名(`--for` / `--turns`)。

### 11.2 提交清单(有条件 —— 仅当 §11 获批后)

> **这不是放行信号。** 这是 §11 一旦获批就会迁入专门实现计划文档的**落地顺序骨架**
> (见篇首,行 13–14);放在此处只是让候选方案自带一份"真正落地会动到哪些东西"的估算。
> 每一行都用 grep 锚定到代码/文档落点。顺序即依赖顺序;每个提交独立可审,且必须**绿着**
> 落地 —— 不留红/UNSTABLE CI,任何语义合并都要在合并后的树上重新跑测。

| # | 提交 | 落点(锚) | 测试 | 依赖 |
|---|---|---|---|---|
| 1 | **duration 解析器**(§G) | 新增 `agentao/cli/duration.py` :: `parse_duration("90s\|30m\|2h\|1h30m") → int`;拒绝无单位 / 非正数 | 新增 `tests/cli/test_duration.py` —— 单位、复合、拒 `"30"` / `"-5m"` / `""` | — |
| 2 | **GoalState + 持久化**(§D、§E) | 新增 `agentao/cli/goal_state.py` :: `GoalState` dataclass + `GoalStatus` 枚举;`load()`/`save()` → `.agentao/goal.json`(或 session —— 待定);转移方法落实 §E **状态机**(只管合法转移;*由谁触发*的写权限切分由调用方落实 —— 行 3/4/5) | round-trip 序列化;非法转移拒绝;paused 时间不计的计量 | — |
| 3 | **`update_goal` 注入工具**(§E.1) | 新增 `agentao/tools/goal.py` :: 带守卫的 `update_goal(status)`(仅 active → 终态后 no-op + 错误结果);**不**在 `agent.py::_register_tools()` 注册 —— 经 `extra_tools` 注入(`agent.py:84`);可选只读 `get_goal` | `status != active` 时守卫返回错误;成功路径置状态;终态不可变 | 2 |
| 4 | **`/goal` 命令**(§A、§C) | 新增 `agentao/cli/commands/goal.py`(argparse 子命令 + `--for` / `--turns` / `--unbounded` / `--clear`;替换非终态目标时确认);改 `agentao/cli/help_text.py`(`/goal` 条目);从 settings 读 `goal.{default_max_turns,default_time_budget,enabled}` | 新增 `tests/cli/test_goal_command.py` —— 旗标解析、默认套用、替换确认、`--unbounded` 退出 | 1、2 |
| 5 | **续航循环**(§F)—— *关键件* | 改 `agentao/cli/input_loop.py` —— 把 `:545` 的一次性先例泛化为 host 自有 `while goal.status == active`:预算前置检查、经 `extra_tools` 注入 `update_goal`、`CONTINUATION_PROMPT` / `WRAP_UP_PROMPT`、触限时**恰好一次**收尾轮、每轮 `persist()`;明确**不**走 `force_continue`(§H) | 循环在 complete/blocked/轮次上限/时间上限退出;收尾恰好一次;首轮用原始消息,之后用续航消息;`--turns` 与 `max_iterations` 独立 | 2、3、4 |
| 6 | **配置参考**(§C) | 改 `docs/reference/configuration.md` —— `goal` settings 块 + `.agentao/goal.json` 路径 / schema / 优先级 | 纯文档 | 4 |
| 7 | **文档**(三类读者) | **(a) 终端用户** —— 新增 `docs/guides/goal.md`:如何用 CLI `/goal`(子命令、预算、示例),与 `session-replay.md`(`/replay`)/ `memory-management.md`(`/memory`)平行;**(b) host 集成方·完整** —— 新增 `developer-guide/{en,zh}/part-4/8-orchestration-continuation.md`:编排续航模式(外层循环 + 注入上下文 + 注入工具 + host 侧时间/轮次计量),CLI 作范例,交叉链 §5.8 —— **唯一真源**;**(c) 嵌入型 coding agent·精简** —— 扩充 `docs/guides/embed-for-agents.md`,加一段"长任务 goal/续航是 *host* 的活"的骨架,**指向 (b)**(不重复),强调当下即真的通用结论:harness 只给三个原语(drive-turn / 注入上下文 / 注入工具 —— 均已存在,Layer 1 不变),**不**给 goal 功能;**(d)** 改 `CLAUDE.md` 斜杠命令列表 + 一条 `--turns` ≠ `max_iterations` 的 gotcha | 纯文档;developer-guide EN+ZH 配对,`docs/guides/*` 按仓库惯例单文件 | 5 |

**分阶段上线护栏。** 在提交 5 落地前把 `goal.enabled` 保持 **`false`**,以免提交 3–4
发出一个接到尚不存在的续航循环的 `/goal` 命令。到提交 5 或收尾提交再翻为 §C 默认(`true`)。

**完成判定。** `uv run python -m pytest tests/` 含新文件全绿;`/help` 渲染出 `/goal`;
EN+ZH 文档对同步;一条回归测试断言 goal 循环**不**经 `force_continue`、且 `--turns`
与 `max_iterations` 独立(§H 两条边界护栏);非 goal 路径上 `agentao/cli/` 的 import
面不变。
