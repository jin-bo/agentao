# 会话生命周期 hook 的取值：codex #44349 对照与 agentao 的三个 surface

> **⚠️ 本文一部分已实施、其余仅为分析记录 —— 引用 §1 之前先看下面的「状态」行。**
> **§6.1 / §6.2 已实施；本文其余内容仅为分析记录，未获实施授权。** §1 是**结论的优先级排序**，
> 不是工单，其最后两行（ACP、压缩）仍是仅记录的缺口。其中只有一条需要维护者拍板
> （§4 的 ACP 取舍），已实施的那部分是接线、不是决策。引用本文时请一并引用这一行。

**状态：** **§6.1 / §6.2 已实施**（2026-09-10，工作树；套件 4963 通过）——
即「CLI 取值 + 恢复路径」。**§4（ACP）与 §6.3（压缩）仍是仅记录的缺口，未实施。**
发出的取值已写入 `docs/reference/configuration.md` §11。以下正文为 **rev 4** 的分析。**rev 4 补清了 rev 3 遗留的两条 P2 与一处范围表述残留**：
加载失败要分启动恢复与交互恢复（§6.2 最后两行）；§5 对 v1 的「无变化」限定为匹配与执行次数，
输入字段仍然变（v1 拿的是原样 envelope）；§1 不再把压缩列进「接线」。
**rev 3 经维护者评审收窄了实施范围**：本文的方向成立，
但可实施的部分只有 **CLI 取值 + 恢复路径**两件；ACP（§4）与压缩（§6.3）本轮只记录缺口，另行处理。
rev 2 的 §6 有两条 P1 错误，都是「按方案实施会产生重复派发」，已在 §6 就地改正并标注。
**rev 1 的范围错了一半**：它只写了 `SessionStart.source`，
并断言那是字段表里唯一发常量的必填字段。`SessionEnd.reason` 处境完全对称，而 rev 1 所依据的那张
探测表**紧挨着就有它的两行**，rev 1 只引了上半截。文件名同批由 `session-start-source-vs-codex` 改为现名。
同批评审的另一条（`/goal` 无进展熔断）已单独实施，不在本文范围内。
**锚点：** codex `openai/codex@9688359977`（增量 `b7cd519c76..9688359977`，551 commit），关键提交
`e444aa99d7` "Distinguish forked sessions in session-start hooks" (#44349, 2026-09-10)；agentao `main@770c045`。
**方法：** 两侧读源码，每条主张就地附 `file:line`。matcher 语义引用 `hooks-probe-2.1.251.md` §G6 的**实测**结论。
**先前记录：** `hooks-claude-contract-conformance-plan.zh.md` §5.3 的事件字段表已经写了 `source`（第 1517 行）
和 `reason`（第 1518 行）两行；本文不是首报，而是指出它们是那张表里**仅剩的两条未接线必填字段**。

---

## 1. 结论表

| 优先级 | 内容 | 依据 |
|---|---|---|
| **接线（无需决策）** | `SessionStart.source` 恒为 `startup`，四个上游取值只有一个可达 | §2 |
| **接线（无需决策）** | `SessionEnd.reason` 恒为 `other`，五个上游取值只有一个可达 | §2 |
| **接线（无需决策）** | `/clear` **两个事件都误报**，上游两处都该是 `clear` | §2 |
| **接线（无需决策）** | `/resume` 两个事件**都不派发** | §2 |
| **本轮只记录** | ACP 三个 surface 中唯一一个两个事件都不派发 —— 但**不能按 new/load 各派发一对**，见 §4 | §4 |
| **本轮只记录** | 压缩后不派发 `SessionStart` —— 但先要定「哪些压缩算生命周期重建」，见 §6.3 | §6.3 |
| **不采纳** | codex 新增的 `fork` source | §3 |

**可实施范围（rev 3 收窄后）：** 只有前四行，即 **CLI 取值 + 恢复路径**。后两行是已记录的缺口，
各自另有前置问题要先解决。

**一句话：** 这不是待决策的开放行，是掉队的两条。§5.3 那张表现在分三类：

| 类别 | 字段 | 状态 |
|---|---|---|
| 必填、仍发常量 | `SessionStart.source`、`SessionEnd.reason` | **本文的对象** |
| 条件、仍未接线 | `SessionStart.model`（无调用方传）、`PostToolUseFailure.is_interrupt`（**全树零调用方**） | 本文不处理 |
| 原标 "exists, unplumbed"、已接线 | `tool_use_id`（三个派发点，含 `agentao/runtime/tool_runner.py:377`）、`duration_ms`（`tool_executor.py:717`） | 先例 |

第三行是本文的论据：同一张表的同类问题，别的行都接上了。

---

## 2. agentao 现状：两个常量，九个应有取值

两个适配器方法都接受取值参数（`agentao/plugins/hooks/_payload.py:47` 的 `source`、
`:72` 的 `reason`），profile 序列化也都写出去（`_profile_payload.py:100`、`:104`）。
**但四个派发点没有一个传。**

| 触发场景 | `SessionStart` | `SessionEnd` |
|---|---|---|
| 交互式启动 | `startup` ✓ | —— |
| 交互式退出 | —— | `other`（上游 `prompt_input_exit`）**欠报** |
| `agentao run` | `startup` ✓ | `other` **欠报** |
| `/clear` | `startup` **误报**，应为 `clear` | `other` **误报**，应为 `clear` |
| `/resume` | **不派发** | **不派发** |
| 压缩成功后 | **不派发**（应为 `compact`） | —— |
| ACP | **不派发** | **不派发** |

派发点：`agentao/cli/session.py:95`（start）与 `:124`（end）；`agentao/cli/run.py:698` 与 `:827`。
`/clear` 走 `agentao/cli/commands/reset.py:30` 的 `on_session_end()` 与 `:53` 的 `on_session_start()`，
两次都吃默认值。`/resume`（`cli/commands/sessions.py:87`）两个都不调。

**误报与欠报要分开看。** `other` 是上游自己给「不属于任何具名原因」准备的取值，所以
`agentao run` 结束时发 `other` 只是没说得更细；但 `/clear` 是**具名原因**，两个事件上发默认值
就是把一个已知场景报成了别的场景。

**为什么取值会有后果。** 派发器把 `SessionStart` 的 matcher 与 `source` 相比、把 `SessionEnd` 的与
`reason` 相比（`agentao/plugins/hooks/_dispatcher.py:573`、`:575`）。这一点是**实测**的：
`docs/reference/hooks-probe-2.1.251.md` §G6 的表格（281-285 行）记录了真实 `claude` 2.1.251 的行为，
**四行覆盖两个事件** —— matcher `startup` 对 source `startup` 触发，`resume` 对 `startup` 不触发；
matcher `other` 对 reason `other` 触发，`clear` 对 `other` 不触发。所以在 agentao 里：

- `matcher: "resume"` / `"clear"` / `"compact"` / `"logout"` / `"prompt_input_exit"` 的规则永远是死规则，
  **且没有任何诊断** —— profile 的一次性诊断是针对*未实现的字段*的，而这两个字段是实现了的，只是恒为一个值。
- `matcher: "startup"` 与 `matcher: "other"` 的规则会多触发。

**测试现状。** `tests/test_hooks_profile_payloads.py:38` 显式传了 `source="resume"` —— 管道是测过的，
只是没有任何生产调用方去传它。

---

## 3. codex 做了什么，以及它只覆盖一半

`e444aa99d7` (#44349) 的 Why 段原文：fork 出来的 thread 上报 `startup`，导致启动 hook 在上下文其实是
继承来的情况下重跑；带历史 resume 时也报成 `startup` 而不是 `resume`。修法是给 `SessionStart` 加 `fork`
source、在 hook 输入 schema 里暴露它，并按「有 fork parent → `fork`，有历史无 parent → `resume`」定规。

**同一类，不同表现面。** codex 的错误取值来自一个新增的会话形态，agentao 的来自一个从未被传入的参数。
后果一致：hook 作者按上游语义写的 matcher 落空，而 hook 的全部意义就是上游兼容。

**这条 peer commit 是由头，不是边界。** 它只动 `SessionStart`。`SessionEnd` 这半边不是从 codex 抄来的，
是本文 rev 2 在自查 rev 1 时查出来的 —— 读者不要据此以为 codex 对 `SessionEnd` 做过什么或它那边是干净的。

**`fork` 不采纳。** agentao 没有 thread fork 概念；子 agent 走的是一次性 worker
（见 `codex-subagent-v2-vs-agentao.zh.md`），不产生继承上下文的新会话。声明一个永不产生的取值
只会让 profile 的枚举表变长而不变准。

---

## 4. ACP：本轮只记录缺口，不在本文实施

**事实。** `agentao/acp/` 下对 `SessionStart` / `SessionEnd` / `dispatch_plugin_session_*` **零引用**。
交互式 CLI（`cli/session.py`）和 `agentao run`（`run.py:698`、`:827`）两个事件都派发。
配置参考 §11 里没有任何「hook 仅限 CLI」的范围声明，代码里也没有相应注释或测试。

> `agentao/acp/models.py:269` 提到「CLI 在它的 session-end hook 里持久化」，指的是 CLI 内部的
> `on_session_end` 步骤，**不是**插件 `SessionEnd` 事件。不要把它读成一条范围声明。

**rev 2 在这里提过一个错误方案**（「`session/new` 与 `session/load` 各派发一对」），已撤回。两个原因：

1. **ACP 支持多会话并存。** `agentao/acp/session_manager.py:108` 把会话存进一个 dict，
   `session/new` 或 `session/load` 只是多了一个会话，**不意味着另一个会话结束**。
   `SessionEnd` 必须跟实际的关闭路径走（`acp/models.py::close`），不能挂在创建/加载上。
2. **不能按方法名选取值。** `agentao/acp/session_new.py:329-348` 是「启动恢复接缝」：
   服务器带 `--resume` 启动时，**首次 `session/new` 实际执行的是恢复**（hydrate + replay）。
   照方法名给 `session/new` 发 `startup` 就是错的。

**所以 ACP 侧的正确做法需要先回答两个问题**：`SessionEnd` 挂在哪个关闭路径上，以及
`session/new` 如何区分「真新建」与「启动恢复」。这超出本文范围。

**仍然成立的结论：** 三个 surface 里 ACP 是唯一两个事件都不派发的，而这个分歧没有文档、
注释或测试支撑。是接上还是写进文档（`docs/reference/configuration.md` §11 一句范围声明），
仍需维护者拍板 —— 只是这个板要在 ACP 自己的工单里拍，不在本文。

---

## 5. 两种风险要分开：改取值 vs 加派发点

**matcher 症状只影响 profile 契约。** `_dispatcher.py::_matches:509` 只把非 v1 规则送进 Claude
matcher；`CLAUDE_FLAT_EVENTS` 只有 `{Stop, PreCompact}`（`agentao/plugins/models.py:230`），
所以 `agentao-v1` 的 `SessionStart` / `SessionEnd` 规则走 envelope 分支，只按 `toolName` 过滤 ——
也就是**根本不过滤，一律触发**。死规则（matcher 写了 `resume` 却永不触发）只存在于
`claude-code@profile-1`。

**但「v1 不过滤」正是加派发点的风险来源，不是安全保证。** §6 的两类改动风险完全不同：

| 改动类型 | 对 profile 规则 | 对 v1 规则 | 风险 |
|---|---|---|---|
| **改已有事件的取值**（`/clear` 发 `clear`） | 匹配结果变化：`clear` 规则开始触发，`startup` 规则停止触发 | **匹配与执行次数不变**，但**输入字段变了** | 低（非零），且正是修复目标 |
| **新增派发点**（`/resume`、压缩后） | 新增一次执行 | **同样新增一次执行** | **执行次数与副作用改变** |

**第一行的「不变」只限于匹配与次数，不是「v1 看不见」。** v1 规则拿到的是原样的 agentao envelope
（`_dispatcher.py:605-609`：profile 规则转成扁平 payload，v1 规则拿 `payload` 本身），
`data.source` / `data.reason` 就在里面。一个读了这两个字段的 v1 脚本会看到取值从 `startup` 变成
`clear`，**脚本自身的行为可能因此改变**。这不需要新增兼容层 —— 修正取值本来就是目的 ——
但它意味着「改取值对 v1 完全无影响」是错的说法，回归测试要断言的是匹配与次数不变，而不是输入不变。

第二行是本文 rev 3 之前没写的：一个已经写好 `SessionStart` v1 hook 的用户，今天在 `/resume` 时
它不跑；加了派发点之后它会跑，而 v1 规则没有 matcher 可以让他挡掉。**任何新增派发点都要配 v1 回归测试**，
证明既有 v1 hook 的执行次数变化是被认可的、而不是顺带的。

---

## 6. 若实施：收窄后的路线

**范围：CLI 取值 + 恢复路径。** ACP（§4）与压缩（§6.3）不在本轮。

### 6.1 沿现有调用链透传取值

给 `dispatch_plugin_session_start(agent, session_id, *, source=...)` 和
`dispatch_plugin_session_end(agent, session_id, *, reason=...)` 各加一个关键字参数，
**不新增调用点**，只让现有四个各传各的值：

| 入口 | `source` | `reason` |
|---|---|---|
| 交互式启动（`input_loop.py:272`） | `startup` | —— |
| 交互式退出 | —— | `prompt_input_exit` |
| `agentao run` | `startup` | `other`（**保留**，见下） |
| `/clear`（`reset.py:30`/`:53`） | `clear` | `clear` |
| `/new`（`reset.py:63`，**共用 `_reset_session`**） | `clear` | `clear` |

`/new` 这一行要明写：它和 `/clear` 共用同一条 reset 路径，只是不清记忆，上游词表里没有对应取值，
最近的就是 `clear`。不明写就会由实现者随手决定。

`agentao run` 正常结束**保留 `other`**：上游 `prompt_input_exit` 指的是交互式提示符下的退出，
一个非交互 run 结束不是那个原因，`other` 在这里是合法兜底而不是欠报。

### 6.2 修正两种 resume 的事件顺序与次数（rev 2 在此有 P1 错误）

**rev 2 说「在 `resume_session()` 里加两次派发」，这是错的。** 命令行 `--resume` 也走同一个函数：
`entrypoints.py:97-99` 调 `resume_session(...)`，随后 `main()` 进入 `run_loop()`，
而 `run_loop` 在 `input_loop.py:272` **无条件**调用 `on_session_start()`。照 rev 2 实施的结果是
先发 `resume` 再发 `startup`，并且给一个从未开始过的会话发一次 `SessionEnd`。

正确的分场景规则：

| 场景 | `SessionEnd` | `SessionStart` |
|---|---|---|
| 启动恢复（`agentao --resume`） | **不发** —— 没有旧会话 | **只发一次**，由 `run_loop` 现有的那次改报 `resume` |
| 交互中 `/sessions resume` | 发，`reason="resume"` | 发，`source="resume"` |
| 交互中 resume **加载失败**（`sessions.py:116` 的错误分支） | **不发** | **不发** —— 原会话原样保留 |
| 启动恢复**加载失败**（随后照常进入 CLI） | **不发** | **发一次 `startup`** |

启动恢复那一格的实现要点：`resume_session()` 不自己派发，而是在 CLI 上留一个一次性标记，
`run_loop` 现有的 `on_session_start()` 读它决定发 `startup` 还是 `resume`。这样派发点数量不变，
只是取值变了 —— 也就落回 §5 那张表的第一行（低风险），而不是第二行。

**标记只在加载成功后设置**，这正是最后两行的分界。`entrypoints.py:97-100` 是
`_resume(...)` 之后**无条件** `cli.run()`：启动恢复失败时旧会话根本不存在（不该发 End），
而新会话照常开始（该发 Start），只是它不是一次恢复，所以取值是 `startup` 而不是 `resume`。
把「加载失败」笼统写成「两个都不发」会让这个真实开始的会话静默掉。

### 6.3 压缩：本轮不做，且先要定范围

`source="compact"` 的前置问题不是层次，是**范围**：**哪些压缩算生命周期重建？**

`CONTEXT_COMPRESSED` **不按 kind 过滤** —— `coordinator.py` 的发射口只滤掉
`outcome.status == "skipped"`，microcompact 成功时同样发。直接订阅它会让每次轻量裁剪都触发
一遍启动 hook，反复注入上下文。这是 rev 2 的建议里最实际的问题，比它当时写的层次问题更靠前。

层次问题仍然存在：`dispatch_plugin_session_start` 在 `cli/session.py`，runtime 不能反向 import cli
（`tests/test_import_layering.py`）。但要先定范围再选派发位置 —— 大概率只有 `kind == "full"` 才算，
而 `microcompact` / `minimal_history` 不算。定了之后再谈订阅哪个事件。

### 6.4 明确不做

不引入生命周期管理器，不引入通用事件框架，不加 `fork` 枚举（§3），不在本轮碰 ACP（§4）。

### 6.5 测试

围绕**真实入口**组织，而不是「每个枚举值一条」：

- 四个真实入口各一条：启动、`/clear`、`/new`、`agentao run`。
- 两种 resume 各一条，断言**事件顺序与次数**：启动恢复只有一次 Start、没有 End；
  交互中 resume 是 End→Start 各一次。
- 一条断言 `SessionEnd` 与 `SessionStart` 携带的 **session id 分属旧/新会话**。
- **两条失败用例，分开写**：交互中 resume 加载失败 —— 两个事件都不发，原会话保留；
  启动恢复加载失败 —— 不发 End，发一次 `source="startup"`（**不是** `resume`），
  证明一次性标记只在加载成功后设置。
- **v1 回归**（§5）：`/resume` 新增派发点后，既有 `agentao-v1` 的 `SessionStart` 规则
  执行次数的变化是被断言认可的。
- 两条取值回归：`/clear` 不再上报 `startup`、也不再上报 `other`。

### 6.6 本文范围外但顺手可查

`SessionStart.model` 与 `PostToolUseFailure.is_interrupt` 两条条件字段同样未接线（见 §1）。
它们是条件字段，缺失是合规的，接不接是产品判断。
