# 会话生命周期 hook 的取值：codex #44349 对照与 agentao 的三个 surface

> **⚠️ 本文所述内容已全部实施。** §1 曾是结论的优先级排序，现在是一张对照表：
> 每一行都已落地，取值以 `docs/reference/configuration.zh.md` §11 为准。

**状态：** **全文已实施**（2026-09-10，工作树；套件 4991 通过）——§6.1 / §6.2（CLI 取值 + 恢复路径）、
§4（ACP）、§6.3（压缩），三批各经一次维护者评审后分别放行。本文从此是**已落地行为的依据**，不再是提案。
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
| **已实施** | ACP 两个事件都不派发 —— 修法**不是**按 new/load 各派发一对，见 §4 | §4 |
| **已实施** | 压缩后不派发 `SessionStart` —— 范围限定为**成功的 full**，见 §6.3 | §6.3 |
| **不采纳** | codex 新增的 `fork` source | §3 |

**实施批次：** 前四行（CLI 取值 + 恢复路径）一批，§4（ACP）一批，§6.3（压缩）一批，
各经一次维护者评审放行。后两批的前置问题都在各自小节里给了答案。

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

## 4. ACP：已实施，但不是「加三个派发调用」

**原缺口。** `agentao/acp/` 下对 `SessionStart` / `SessionEnd` **零引用**，而交互式 CLI 和
`agentao run` 两个事件都派发，且这个分歧没有文档、注释或测试支撑。

**rev 2 在这里提过一个错误方案**（「`session/new` 与 `session/load` 各派发一对」），已撤回。
实施时按维护者评审定下的三条约束落地：

**1. Start 必须先于首轮 prompt，且在恢复历史之后。** 注入的 context 是 append 到
`agent.messages` 的：先发 Start 再恢复历史，内容会被整体覆盖；先注册会话再发 Start，
流水线跟上来的 `session/prompt` 可能抢在前面开turn。所以派发口放在
`AcpSessionManager.create` 新增的 `before_publish` 回调里 —— **在重复检查之后、发布之前**，
两半都吃紧：

- *在重复检查之后*：`SessionStart` hook 是任意用户命令，为一个随后因 id 重复而失败的
  `session/load` 跑一遍副作用是不可接受的。
- *在发布之前*：`session/load` 的 id 由客户端提供，它可以把 prompt 流水线跟在 load 后面；
  而 `turn_lock` 是**非阻塞**获取的 —— 抢到的 prompt 会被直接拒绝而不是排队，所以
  「先发布再派发」会把一个 hook 变成一次伪错误。

代价是明确的：回调期间其他会话的查找会阻塞（共用同一把注册锁），上界是 hook 超时，
且只在会话创建时付一次。复用现有注册锁，没有新状态机。

**2. End 跟真正的关闭走。** 放在 `AcpSessionState.close()` 的幂等守卫之后、释放任何资源之前，
`reason="other"`（ACP 没有对应的上游具名原因，`other` 正是上游给「不属于任何具名原因」的取值）。
**不跟 new/load 走**——ACP 同时持有多个会话，新建或加载一个不意味着另一个结束；
**也不跟取消单轮走**——取消一个 turn 不是会话结束。构建失败时只清理 agent，不补发 End；
强杀进程不承诺派发。

**3. 复用派发逻辑，但不导入 CLI。** 与终端无关的派发和上下文注入移到
`agentao/plugins/hooks/lifecycle.py`（`fire_session_start` / `fire_session_end`），
CLI 的两个 `dispatch_plugin_session_*` 变成它的薄别名并保留打印，ACP 通过
`agentao/acp/_lifecycle.py` 接入。用户提示走新增的
`_transport_helpers.write_user_notice`，以 `session/update` 分片送达 —— ACP 没有普通
hook notice 通道，而 exit 2 在这两个事件上**就是**用户通道。

**已知弱点（接受而非绕开）：** `session/new` 上提示先于那条告知客户端新 sessionId 的响应写出，
严格的客户端可能丢弃它。事件的实质通道是注入历史的 context，不受影响；把一条诊断缓存到
可能永远不来的首轮，是拿「可能被丢」换「肯定不到」。

**落地取值：** `session/new` → `startup`；`session/load` 与启动恢复成功 → `resume`；
启动恢复回退到新建 → `startup`（取值跟实际发生的事走，不跟方法名走）；真正关闭 → `other`；
加载失败 / 重复加载 / 取消单轮 → 不派发。测试见
`tests/test_acp_session_lifecycle_hooks.py`。

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

### 6.3 压缩：已实施，范围限定为「成功的 full」

前置问题不是层次，是**范围：哪些压缩算生命周期重建？** 答案是只有 full，且只有成功的：

| 场景 | 派发 `SessionStart(source="compact")` |
|---|---|
| 手动 `/compact` 成功 | 一次 |
| 自动阈值 full 压缩成功 | 一次 |
| API 溢出后的 full 压缩成功 | 一次 |
| `microcompact`、`minimal_history` | 不派发 |
| 失败、取消、跳过 | 不派发 |

`microcompact` 在它的带内几乎每轮迭代都跑，把启动 hook 挂上去等于反复注入同一段上下文；
`minimal_history` 是溢出阶梯的最后一级，存在的意义是把一个已经被拒两次的请求**缩小**，不是重新播种。
两者都不是重建。**这也正是不去订阅 `CONTEXT_COMPRESSED` 的原因** —— 那个事件不按 kind 过滤
（发射口只滤 `status == "skipped"`），订阅它会让每次轻量裁剪都触发一遍启动 hook。

**派发位置是这条里唯一精细的地方。** `coordinator.py` 的成功分支先替换历史、再组装
`messages_with_system`，而那份快照就是调用方接下来要发出去的请求 —— **API 溢出的两级会立刻拿它重试**。
所以派发口插在这两步之间：晚于历史替换（否则注入的内容会被整体覆盖），早于快照组装
（否则那次重试拿到的请求里根本没有 hook 的上下文）。注入内容因此自然计入
`post_est_tokens`；若仍然溢出，走现有的 `minimal_history` 回退，不为 hook 另设重试或保留机制。

**不调用 `on_session_start`。** 压缩保留原 session id，不发 `SessionEnd`，不重启 replay，
不做记忆会话归档 —— 只有插件派发适用，所以直接调 §4 抽出的
`plugins/hooks/lifecycle.py::fire_session_start`。

**hook 失败不能推翻已成功的压缩。** 派发口整体吞异常：走到这里时历史已经被重写，而三个调用方里有两个
是溢出恢复阶梯，一个 hook 故障绝不能反过来终结它本来要拯救的那一轮。用户提示走
`PLUGIN_HOOK_FIRED`，和 `UserPromptSubmit` / `PreCompact` 同一个宿主通道。

测试见 `tests/test_compaction_session_start_hook.py`。

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
