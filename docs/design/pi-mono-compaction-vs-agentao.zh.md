# pi-mono 压缩机制与 Agentao 压缩机制对照

> **⚠️ 仅为分析记录，全文任何一条都未获授权实施。** §1 的分级是**分析结论的优先级排序**，不是工单，
> 也不是排期。引用那张表时请连同这一行一起引用——它防止下一个读者把排序读成迭代计划。


> **⚠️ 阈值已变更（2026-08-23，本文锚点之后）：** `COMPRESSION_THRESHOLD` 已由 **0.65 提高到 0.80**
> （`agentao/context_manager.py:69`），廉价层区间随之由 `(55%, 65%]` 扩为 `(55%, 80%]`。
> 正文各处的 65% 描述按锚点原样保留，**不要**当作当前值读。

**状态：** 分析，rev 6（2026-08-23）。未授权实施。
**rev 3 经维护者评审，14 条修正，逐条回源复核后全部成立；rev 4 经第二轮评审，把这些修正真正
*折叠进*正文、表格与 §13，并**删除**被取代的旧裁决，而不是在旁边加注。** 因此 §14 是**历史记录**，
不是需要叠加阅读的勘误层——正文自身即为准，任何一节都不需要读 §14 才能读对。**rev 5（第三轮评审）**
收紧了三处仍然绝对化的量词：跳跃式增长**并不保证**能进廉价层（该带是左开右闭区间 `(55%, 65%]`）、codex 的静默溢出盲区
是**取决于阈值**而非全盲、pi-mono 的会话持久化是**默认行为**而非无条件（`--no-session` 为内存态）。
**rev 6（第四轮评审）** 修掉 rev 5 自身收紧动作带出的两处一致性错误：该带是**左开右闭区间**
`(55%, 65%]`，不是"闭区间"——且所引行号指的是 `needs_compression`（`:256`）而非
`needs_microcompaction`（`:278-279`）；以及 §13 仍写着跳跃增长"廉价层仍会触发"。
最大的一处：rev 2 的第三条 P1（"上下文窗口不随模型走"）**降级为 P2 并重新界定**——窗口是
**有文档记载的宿主所有旋钮**，不是内部缺陷（§3.4）；真正成立的是缺少校验、告警与协调。
**锚点：** pi-mono `a69bef789`（2026-08-23）；agentao `main@a996395`（2026-08-23）；codex
`openai/codex@2151d3a5b7`（2026-08-21，**仅** §3.3–3.4）。
**方法：** 各侧均读源码；每条主张就地附 `file:line`。凡属推理而非实测的结论，文中明确标注。
**rev 2（三边触发合并）：** 本文是 **两边** pi-mono↔agentao 对照，**唯独 §3.3–3.4** 把 codex 作为
触发轴上的第三个数据点引入。不要把 codex 读进其余任何章节；完整的 codex 压缩对照见
`codex-compaction-vs-agentao.zh.md`。

> **rev 3 撤回——"一个对照方不同是设计选择，两个对照方以同一方式不同就是缺口"。** 这条推理按其
> 字面表述不成立，而它正是 rev 2 那条第三 P1 的来源。两个对照方趋同，说明的是某个设计**流行**；
> 当第三方采用的是一个有文档、有意为之、并自带配置面的所有权设计时（§3.4），趋同并不能判它有缺陷。
> 同侪趋同是关于**选项空间**的证据，不是对占据空间中另一点的设计的判决。这句话不要再复用。
**相关：** `codex-compaction-vs-agentao.zh.md`（同一问题对第三方的对照——复用其结论前先读它的 §13 勘误表）、
`pi-mono-borrow-review.zh.md`、`path-a-roadmap.zh.md`。
**孪生：** `pi-mono-compaction-vs-agentao.md`。

---

## 1. 结论表（优先级排序，非排期）

**一句话差异：agentao 压得早，把投入花在"喂给摘要模型的输入有多保真"上；pi-mono 压得晚，把投入花在
"被压掉的东西一件都不销毁"上。**

| 若实施，优先级 | 内容 | 章节 |
|---|---|---|
| **P1** | `PreCompact` 插件钩子是只读通知（`-> None`）。嵌入方能观察压缩，但**不能否决、也不能替换结果**。pi-mono 的 `session_before_compact` 两者都能做。 | §8 |
| **P1** | 摘要失败熔断器**没有复位路径**：一旦打开就在任何尝试之前短路，于是永远不可能成功，于是永远不复位。压缩——**包括手动 `/compact`**——在该 `ContextManager` 实例的余生里报废。 | §9 |
| **P2** *（rev 2 曾列为 P1）* | **上下文窗口由宿主所有，但无人校验。** `max_context_tokens` 是**四个**面上都有文档的宿主旋钮，所以"不随模型走"是设计选择而非缺陷。真正成立的是：CLI 对所有模型套用同一个 `200_000` 默认值（`cli/app.py:278`），且 `/model` **不做任何校验、告警或协调**。配置窗口**大于**模型真实窗口时，两级设计在**渐进增长下**退化成只剩应急阶梯；跳跃式增长**可能**落进廉价层区间，也可能直接越过它进入计划内的全量压缩，所以廉价层并非绝对不可达。恢复同样没有保证。 | §3.4 |
| **P2** | `KEEP_RECENT_MESSAGES = 20` 是**消息条数**，不是 token 预算。20 条可能是 500 token，也可能是 200K。 | §5 |
| **P2** | 上一份摘要被塞进**从新到旧的分配器**里当作一个块，于是与实时消息争抢淘汰——正是这个形状导致了 index-1 缺陷，并逼出 `carry_index` + `_clip_carry_summary` 两处补丁。pi-mono 没有这样的分配器：它把旧摘要用 `<previous-summary>` 标签无条件追加在转录之后，配专用 UPDATE 提示词。（两者都只发一个扁平字符串；差别在淘汰池，不在传输格式。） | §6.2 |
| **P3** | 切点落在回合中间时，**不保证**源头用户请求能幸存——它随 `messages[:split_index]` 进入摘要器，但没有任何预算为它保留。pi-mono 用一次自带预算的独立调用生成回合前缀摘要。 | §5.2 |
| **P3** | 图片按 **0 token** 估算——`_count_message_tokens` 只累加 `type == "text"` 块。pi-mono 记 4 800 字符（约 1 200 token）。 | §4.2 |
| **不要借鉴** | pi-mono 的 `chars/4` 估算器、无上限的摘要输入、只留头部的工具结果截断、没有廉价层 | §10 |
| **不要借鉴** | codex 那两个**独立**触发点 `ModelDownshift` / `CompHashChanged`——agentao 的每轮检查会自行用上被校正后的窗口 | §3.4 |
| **观察** | 每轮 vs 每回合检查：agentao 与 codex 持平、**优于 pi-mono**；无需改动 | §3.3 |
| **观察** | 压缩不可逆，**且在默认配置下没有任何留档**：会话摘要行不存原始消息、replay 默认关闭、普通 session 保存的是已压缩的列表。rev 2 的"取证没有丢东西"是错的。 | §7 |
| **待定夺** | 65% 这个阈值本身。agentao 是三边最保守的，差 25–27 个百分点，且"精度补偿"的论证站不住（§3.2）。**已确定**的是比例不可配，而两个对照方都可配 | §3.2、§3.3 |

§11 列出**已核对过的对等项**，不要重复上报。

---

## 2. 架构定位：harness 内核 vs 宿主会话层

| | agentao | pi-mono |
|---|---|---|
| 内核 | `agentao/context_manager.py`（1287 行，单类） | `core/compaction/{compaction,utils,branch-summarization}.ts`（1541 行，纯函数） |
| 编排 | `runtime/chat_loop/_compaction.py`（127 行）；溢出阶梯在 `runtime/chat_loop/_runner.py:1117` | `core/agent-session.ts::_checkCompaction`（`:2050`）、`::_runAutoCompaction`（`:2166`）、`::compact`（`:1864`） |
| 层级 | **harness 内核**——就地替换 `agent.messages` | **宿主会话层**——向会话树追加一个条目（默认持久化；`--no-session` 下为内存态），再由上下文构建器重建 |
| 手动入口 | `/compact` → `cli/commands/compact.py`（仅交互式 CLI） | `/compact` → `AgentSession.compact()`，RPC 与扩展同样可达 |

倒数第二行是后面几乎所有差异的根。agentao 的压缩**就是**那次改写；pi-mono 的压缩是一条**改写记录**，
由上下文构建器去遵守它。

对 agentao 自身定位的分层后果值得注意：压缩位于 harness 内部，这正是嵌入方对它没有发言权的原因（§8）。
pi-mono 把它放在会话/宿主侧，这正是扩展能整体接管它的原因。

---

## 3. 触发点与阈值

### 3.1 检查在哪里跑

- **agentao——工具循环的每一轮。** `_runner.py:365` 算出一份 `_threshold_token_estimate`，同时喂给
  `_maybe_microcompact`（`:366`）和 `_maybe_full_compress`（`:369`），在每次 LLM 调用之前。
- **pi-mono——只在回合边界。** `_checkCompaction(msg)` 在 `agent_end` 之后触发
  （`agent-session.ts:1109`），以及提交提示词之前再触发一次（`:1220`，此处 `skipAbortedCheck = false`，
  所以被中止的响应也计入）。

双向后果：agentao 能在**一个回合内部的工具调用之间**压缩——当单个回合跑 50 次工具调用时这很关键；
pi-mono 结构上做不到，那种情况只能靠溢出路径兜底（§9.3）。反过来，agentao 的每轮检查会在回合中途改写
已发送的前缀，这会使**改写点之后**的缓存前缀失效或拉低命中率——稳定的系统提示前缀仍可能被复用，
实际损失多少取决于 provider。

### 3.2 阈值本身

| | agentao | pi-mono |
|---|---|---|
| 全量压缩 | `est > max_tokens × 0.65`（`context_manager.py:69`） | `contextTokens > contextWindow − reserveTokens`（`compaction.ts:235`） |
| 廉价层 | `0.55 – 0.65` 区间（`context_manager.py:70`） | 无 |
| `reserveTokens` 默认值——是**响应余量**，不是原文保留量 | — | `16384`（`compaction.ts:132`、`settings-manager.ts:839`）。**原文**保留量的旋钮是 `keepRecentTokens` = `20000`（§5.1），两者不可混为一谈 |
| 200K 窗口下的实际触发点 | **65%** | **约 91.8%** |
| 可配置性 | 只有 `max_context_tokens`（`agent.py:310`）；比例是类常量 | settings.json 中的 `compaction.{enabled, reserveTokens, keepRecentTokens}`（`settings-manager.ts:826,839,843`） |

这是两者之间最大的单项行为差异，而且它是取舍，不是任何一侧的 bug。agentao 换来余量和一个能工作的廉价层，
代价是更多摘要调用和更频繁的 prompt cache 失效。pi-mono 多保留约 27% 的窗口原文、cache 存活久得多，
代价是余量更薄——`reserveTokens` = 16 384 的存在正是为了守住这层余量。

**不作断言：pi-mono 的溢出恢复是"常规路径"。** rev 2 从一个*阈值*推出了一个*频率*，而源码无法确立
这一点——一个会话到底多久越一次墙取决于负载，而那道余量正是用来阻止它发生的机制。可辩护的表述是风险
表述：在约 92% 处，用于吸收估算误差的余量是 16 384 token，因此"窗口后段来一条大工具结果"比 agentao
的 65% 更可能直接打到 API。实际是否如此，**本文未实测**。

**唯一不属于取舍的是可配置性**：agentao 的比例是硬编码的类属性，于是一个把 agentao 嵌到 32K 模型上的宿主，
和一个嵌到 1M 模型上的宿主，拿到的是同一套 65%/55%，且无从调整。

**关于 pi-mono 数的是什么（rev 3 更正）：** `calculateContextTokens`（`compaction.ts:146`）是
`usage.totalTokens || input + output + cacheRead + cacheWrite`。rev 2 说 `output` 一项使这个数字
"不是 prompt 大小"，理由是输出不属于下一次请求——**这是错的**：assistant 的可见输出会作为历史被重新
发送，因此 `input(N) + output(N)` 相当贴近下一次请求的 prompt。真正可能多计的范围要窄得多：计入
output 却不会重发的**隐藏 reasoning token**，以及 provider 特有的 output 记账。这一点本文未实测。

### 3.3 三边：把 codex 作为触发轴上的第三个数据点

**范围限于本小节与 §3.4。** codex 锚点 `2151d3a5b7`。

**codex 的阈值**是 `ModelInfo::auto_compact_token_limit()`（`protocol/src/openai_models.rs:486`）：
`min(配置值, resolved_context_window × 9 / 10)`——**窗口的 90% 是硬顶**，配置值只能把它调低
（用户的 `model_auto_compact_token_limit` 先被折进 `ModelInfo`，`models-manager/src/model_info.rs:35`）。
判定在 `core/src/session/context_window.rs:77`：
`scope_tokens >= scope_limit + fallback_buffer || active_tokens >= full_context_window`；
计量口径默认 `Total`（整个活动上下文，`protocol/src/config_types.rs:50`），
`fallback_buffer` 默认 0，只有配了 `token_budget.auto_compact_fallback_prompt` 才非零。

**codex 的触发点。** 其中**五个自动入口**汇入同一个 `run_auto_compact`（`session/turn.rs:1178`），
它**之后**才做四路实现分发。**手动 `/compact` 不走这条路**：`handlers::compact` 派生一个独立的
`CompactTask`（`core/src/session/handlers.rs:244`），其 `run()` 自己做一套**平行的**四路分发
（`core/src/tasks/compact.rs:29`）。rev 2 说六处全部汇入，rev 3 更正。两条路径可达的仍是同样四种
实现，所以"触发与实现无关"的结论成立——只是它经由两条路而非一条：

| # | 时机 | reason | 位置 | 条件 |
|---|---|---|---|---|
| 1 | `PreTurn` | `ContextLimit` | `turn.rs:1012,1024` | 每个 turn 采样前，`token_limit_reached` |
| 2 | `MidTurn` | `ContextLimit` | `turn.rs:458` | `needs_follow_up && (新窗口请求 \|\| token_limit_reached)` |
| 3 | `PreTurn` | `CompHashChanged` | `turn.rs:1100` | 上一 turn 与本 turn 的 `comp_hash` 不同——**完全与 token 无关** |
| 4 | `PreTurn` | `ModelDownshift` | `turn.rs:1145` | 换到更小窗口的模型，且现有上下文已超新模型上限 |
| 5 | `StandaloneTurn` | `UserRequested` | `handlers.rs:244` → `tasks/compact.rs:29` | 手动 `/compact`——**平行分派，不经 `run_auto_compact`** |
| 6 | 经 #2 | — | `tools/handlers/new_context_window.rs:35` | **模型自己调 `new_context_window` 工具**；仅在 `Feature::TokenBudget` 下注册（`tools/spec_plan.rs:1055`），且语义是开新窗口**不摘要** |

三点结构性说明。#2 有一道 `needs_follow_up` 门：即使已过限，只要模型不打算继续，codex **不会**在
turn 中途压，留给下一次 #1。codex **没有 error→compact 的直连路径**：`ContextWindowExceeded` 走
`set_total_tokens_full`（`turn.rs:1405` → `session/mod.rs:4075`），把用量钉死在窗口上限，让**下一次**
检查自然触发；agentao 的就地阶梯是另一种形状，不是缺失。第三点：codex 基于 usage 的阈值**并不等于**
对静默溢出天然免疫——但边界比"截断后上报的 usage 不可见"要窄。codex 是在*上报*的 usage 越过它的
90%／全窗口阈值时触发，所以一个先截断、仍上报比如 99% 的 provider **照样会**触发它；只有截断后上报的
usage *低于*自动压缩阈值时才会漏掉。pi-mono 那条 `isRecoverableLength`
（`ai/src/utils/overflow.ts:171`）同样不是通解：它要求 `stopReason === "length"` 且输出低于预期上限，
所以一个静默截断后返回 `stop` 的 provider 也在它覆盖范围之外。

**三边触发全表：**

| 轴 | agentao | pi-mono | codex |
|---|---|---|---|
| 检查时机 | 工具循环每一轮 | 仅回合边界 | 采样前 + 采样后（后者受门控） |
| 阈值 | 静态 `max_tokens` × 0.65 | `contextWindow − 16384`（约 92%） | `min(配置, 窗口 × 90%)` |
| **窗口大小从哪来** | **构造参数，静态**（`agent.py:104` 默认 200 000；CLI 读 `AGENTAO_CONTEXT_TOKENS`，`cli/app.py:278`） | `this.model?.contextWindow`——**随模型**（`agent-session.ts:2057`） | `model_info.resolved_context_window()`——**随模型** |
| 换模型时 | 只重置 tiktoken 编码 + token 锚点（`runtime/model.py:170-171`） | `sameModel` 守卫，旧模型的溢出不会误触发新模型 | `ModelDownshift` + `CompHashChanged` 两个触发点 |
| API 溢出 | 就地 **2 级**阶梯（`_runner.py:1167`、`:1204`） | 检出后 compact + retry 一次 | `set_total_tokens_full` → 下次检查触发 |
| 静默（不报错）溢出 | 检不出 | 覆盖 2 类 provider，**另有** `isRecoverableLength`（`agent-session.ts:2076`）——它要求 `stopReason === "length"`，故静默截断后返回 `stop` 的 provider 不在其内 | **部分覆盖**——只要*上报*的 usage 越过阈值就触发（截断后报 99% 照样触发）；仅当上报值*低于*自动压缩阈值时才漏掉 |
| 廉价层 | 微压缩 55–65% | 无 | 无 |
| 阈值可配 | **否**（类常量） | 是（settings.json） | 是（`model_auto_compact_token_limit` + `_scope`） |

放到三份实现里读：在检查节奏上 agentao 与 codex 持平、优于 pi-mono，所以 §3.1 的"取舍"定性成立；
在窗口来源上两个对照方都按模型解析而 agentao 不然——但 rev 3 不再把这一点读成判决（见文首的撤回）。
agentao 占据的是选项空间里另一个有文档的点：**显式的宿主所有权**，而非自动解析。§3.4 说的是
agentao 占据这个点的**方式**里真正错的地方。

### 3.4 P2：上下文窗口由宿主所有，但无人校验

> **rev 3 重新界定。** rev 2 把它定为无条件的内部 P1。所有权判断错了，后果也说过头了。两处均在下文
> 更正；§14-1 与 §14-3 记录了改动。

**窗口是有文档记载的宿主所有旋钮，共四个面：**

| 面 | 出处 |
|---|---|
| 公开构造参数 | `agent.py:104`（`max_context_tokens: int = 200_000`） |
| 嵌入 factory 覆盖 | `embedding/factory.py:132`（`build_from_environment(**overrides)`） |
| CLI 环境策略——**文档就是这么命名的** | `docs/design/cli-host-agent-factory.zh.md:104`：所有者 = "CLI 环境策略" |
| ACP 三个刻意独立的旋钮之一 | `docs/history/implementation/acp-stdio-auth-fix-plan.md:99-110`："三个旋钮**互不覆盖**……只带 `model` 的请求不得静默重设已有的 `contextLength`" |

所以 rev 2 拿来当**缺陷证据**的那段 ACP 行为，恰恰相反：它是白纸黑字的契约、有意为之、且写明了理由
（把错误的字段接到那里"会让压缩阈值坍塌"）。**agentao 不是没能解析窗口，而是把解析权分配给了宿主。**
这是选项空间里与 codex、pi-mono 的自动解析**不同的一个点**，不是它们的残缺版本。

**真正错的地方，而且确实成立：没有任何东西检查宿主是否配对了。**

- CLI 对**所有模型套用同一个默认值**——`int(os.getenv("AGENTAO_CONTEXT_TOKENS", "200000"))`
  （`cli/app.py:278`）——于是一个用小窗口模型的用户，除非知道要设那个环境变量，否则从第一个 turn
  起就处于误配置状态。
- `set_model`（`runtime/model.py:156`）重置了 tiktoken 编码（`:170`）、token 锚点（`:171`）并清理
  thinking 产物，但对窗口**不做校验、不告警、不协调**。换模型时静默沿用旧数值。

**后果——按条件陈述，因为 rev 2 在这里说过头了（§14-3）。** 当配置窗口**大于**模型真实窗口时
（例如配 `200_000`、真实 32K ⇒ 阈值 110K / 130K）：

- **渐进式**增长确实到不了微压缩带，因为 API 会先在 32K 拒绝。**跳跃式**增长则可能进、也可能不进：
  `needs_microcompaction` 是一个**左开右闭区间** `(55%, 65%]`——`est > 0.55 × max` **且**
  `est <= 0.65 × max`（`context_manager.py:278-279`）——所以一条超大工具结果若把估算值落在
  110K–130K 之间，廉价层会触发；
  若一跃越过 130K，则跳过廉价层、进入计划内的全量压缩。无论哪种，该带都**并非绝对不可达**；
  "廉价层报废"只对渐进式成立。
- 一次被拒的调用代价是一次往返加一次摘要。这**不是每个 turn 都发生**——压缩之后历史又变小了，
  只有当它再次越过真实窗口时才会重现。
- 阶梯通常能恢复，但**没有保证**：`messages[-2:]` 自身也可能超窗（一条巨大的工具结果），此时第三次
  调用直接返回错误（§9.3）。

当配置窗口**小于**模型真实窗口时，根本不会溢出——代价只是浪费窗口（200K 模型上在 20.8K 就压）。
rev 2 的 README 条目写成"任何非 200K 模型"，在这个方向上是错的。

对于静默截断而非报错的 provider，agentao 同样收不到拒绝，于是错误的窗口值既得不到纠正**又检测不到**。
但**这类 provider 究竟丢弃哪一端，本文并未查证**——rev 2 在无证据的情况下断言了"从头部丢失"（§14-3）。

**不要用移植 codex 的 #3/#4 来修这个。** codex 需要 `ModelDownshift` 与 `CompHashChanged` 作为**独立
触发点**，是因为它的检查在回合边界，且它想用**旧模型**去做那次压缩（历史与旧模型的 `comp_hash` 匹配）。
agentao 每轮都检查，所以窗口一旦被纠正，下一次迭代自然就会用上。

**可选路线（刻意不收敛为一条）。** 注意 rev 2 的路线 (c)"做成可配"**其实早已实现**——见上面四个面。
剩下的工作是校验与解析：

- **(a) 从模型元数据推窗口。** 需要一张 agentao 目前没有的模型→窗口表；而 agentao 是 provider-neutral
  的，这张表会持续过期。
- **(b) 从 API 错误里回收真实上限。** 溢出消息通常带着它——Anthropic `"213462 tokens > 200000 maximum"`、
  xAI `"maximum prompt length is 131072"`——而 `_OVERFLOW_PATTERNS`（`context_manager.py:1235`）已经在
  匹配这些串，只是取了布尔值、把数字丢掉了。第一次撞墙后自愈，无表可维护。
- **(c′) 校验并告警。** 保留宿主所有权；在 `set_model` 时、以及启动时各提示一次：配置窗口正在未经
  验证地跨模型沿用。改动最小，而且是**顺着**已文档化的所有权走，不是与它对抗。

(b) 与 (c′) 不互斥。(a) 在别处可能另有价值（`/model` 补全、成本估算）——**未核查**，故不作任何论断。

---

## 4. Token 记账

### 4.1 估算栈

| 层 | agentao | pi-mono |
|---|---|---|
| 1 | 真实 API `prompt_tokens`，锚定到产生它的消息条数，之后只对新追加的部分做局部估算（`context_manager.py:130,153`） | 最近一条有效 assistant 消息的 `usage`（`compaction.ts:202`） |
| 2 | tiktoken，按模型族（`o200k_base` / `cl100k_base`） | — |
| 3 | **CJK 感知启发式**：ASCII 0.25 token/字符，非 ASCII 1.3 token/字符（`context_manager.py:40`） | 尾部消息一律 **`chars/4`**（`compaction.ts:266`） |

agentao 在这一项上明显更强，而且差距最大的恰恰是最容易变长的那类历史：`chars/4` 对中文低估约 5 倍。
agentao 的第 1 层锚点设计也更谨慎——它会把上一回合的系统提示词多计一个回合然后自愈，这个取舍在原处
就有说明（`context_manager.py:157-168`）。

### 4.2 图片

`_count_message_tokens`（`context_manager.py:192`）遍历列表型 content 时只累加 `type == "text"` 的块，
图片块贡献 **0**。agentao 确实承载图片——`_runner.py::_render_image_reference_fallback` 的存在正是为了
给非视觉模型做降级——所以这是活的低估，不是理论上的。

pi-mono 记 `ESTIMATED_IMAGE_CHARS = 4800`（`compaction.ts:244`），即每张图约 1 200 token。

后果，**rev 3 收窄（§14-13）**：这只会偏置估算中**本地计算**的那一部分。`_threshold_token_estimate`
（`:153`）对已发送前缀按真实 API `prompt_tokens` 计费，而那个数字**是**包含 provider 的图片成本的——
所以低估只作用于**上一次锚定之后**新追加的图片，而非整段历史。在图片密集的会话里阈值检查逐轮仍会读低、
压缩比预期更晚触发，但误差被限制在一个回合的新增图片内，不会累积。

---

## 5. 切点与保留窗口

### 5.1 保留多少

- **agentao——按条数。** `keep_count = min(KEEP_RECENT_MESSAGES, max(4, int(len × 0.60)))`
  （`context_manager.py:522`），随后 `_find_split_index`（`:434`）前进到第一条非 `tool` 消息，优先选
  `user`。拒绝落在 `role: "tool"` 上是正确性约束——切在那里会让工具结果与它的 `tool_calls` 失联。
- **pi-mono——按 token 预算。** `findCutPoint`（`compaction.ts:403`）反向累加 `estimateTokens`，
  超过 `keepRecentTokens`（默认 20 000）后吸附到最近的合法切点。`findValidCutPoints` 出于同一正确性
  理由排除 `toolResult`。

pi-mono 的形状更好，且改动不大：agentao 已经有 `_count_message_tokens`，把条数换成反向 token 游走是
`compress_messages` 内部的局部改动。按现行规则，20 条简短往返和 20 条里夹着四个 40K 工具结果，是被同等对待的。

### 5.2 拆分回合

pi-mono 会检测切点落在回合中间（`CutPointResult.isSplitTurn`），回溯到该回合起始的用户消息
（`findTurnStartIndex`），用专用提示词（`TURN_PREFIX_SUMMARIZATION_PROMPT`，`compaction.ts:821`）
以一半的响应预算**单独**摘要该前缀，再以 `**Turn Context (split turn):**` 标题合并
（`compaction.ts:911`）。

agentao 没有对应机制。`_find_split_index` **优先** `user` 边界，但会回退到任意非 tool 消息——而这个回退
的存在有原处记录的真实理由：强制要求 `user` 边界会在尾部没有 user 消息时让压缩变成静默且永久的空操作
（连续 20 条 assistant/tool 消息 ≈ 一个回合里 10 次工具调用）。所以回退本身是对的；缺的是对它的补偿。
源头那条 user 消息通常就在 `messages[:split_index]` 里（`context_manager.py:558`），而那正是送进
摘要器的部分，所以它**在摘要里有体现**。agentao 缺的是**保证**：没有专用的回合前缀摘要，于是这条
请求和其他内容一样去争转录预算（§6.3），再听凭摘要器自身的压缩决定它剩下多少。pi-mono 恰恰为此
预留了一次自带预算的独立调用。（rev 2 说"完全没有记录"，那话太重——§14-9。）

---

## 6. 摘要请求

### 6.1 提示词

| | agentao | pi-mono |
|---|---|---|
| 形状 | 9 节，两段式：先 `<analysis>` 后 `<summary>`（`context_manager.py:_SUMMARIZE_SYSTEM_PROMPT`） | 7 节，单段（`compaction.ts:467`） |
| 章节 | 请求与意图、技术概念、文件与代码、错误与修复、问题求解、用户消息、待办、当前工作、下一步 | Goal、Constraints & Preferences、Progress（Done/In Progress/Blocked）、Key Decisions、Next Steps、Critical Context |
| 工具调用防护 | 调用时 `tools=None` | `toolChoice: "none"`，**并且**响应里出现 `toolCall` 块就抛错（`compaction.ts:706`） |
| 响应预算 | 未显式设置 | `min(0.8 × reserveTokens, model.maxTokens)`（`compaction.ts:659`） |

两者都合理。agentao 的更细、更明确面向代码工作；pi-mono 的 `Progress` 分 Done/In Progress/Blocked，
对"迭代式更新"是更合适的形状——这就是下一点。

### 6.2 上一份摘要如何往下传

这是 pi-mono 在本模块里最好的一个想法。

- **pi-mono：** 上一份摘要是**无条件追加在转录之后的一个专用标签块**，同时把指令块换成
  `UPDATE_SUMMARIZATION_PROMPT`（`:537`），其第一条规则就是"保留上一份摘要中的全部既有信息"。
  **rev 3 更正（§14-8）：** rev 2 称之为"独立的结构化输入"，并非如此——两个标签被拼成**同一个字符串**，
  作为单条 user message 发出（`compaction.ts:670-680`），与 agentao 的形状相同。真正不同的是
  **竞争发生在哪一层**，见下。
- **agentao：** 上一份摘要被**内联**塞回，作为转录里的又一条消息（`_format_for_summary` 检测
  `[Conversation Summary]` 前缀并剥掉结束标记）。

agentao 的形状正是逼出两处独立补丁的原因：`_join_within_budget`（`context_manager.py:1018`）里
`carry_index` 被豁免出预算淘汰，因为被携带的摘要按构造是**最老**的块，而从新到旧的分配会第一个丢掉它；
`_clip_carry_summary` 又把它封顶在半个预算内，防止它饿死实时尾部。两处都是正确的修复。

**但 pi-mono 真正规避掉的东西比 rev 2 说的要窄。** 它没有本地的从新到旧分配器，所以被携带的摘要从来
不是一个**参与淘汰竞争的块**——它总是被追加在转录之后。这消掉了 agentao 的淘汰失效模式。它**并没有**
消掉竞争：两半仍然共享同一份 provider context，而 pi-mono 对两者都不设上限（§6.3），所以一份很大的
旧摘要仍可能在 provider 那一层挤压转录。准确的说法是"pi-mono 没有那个会把淘汰顺序搞错的分配器"，
而不是"旧摘要不参与竞争"。

相应地，rev 2 给出的采纳代价也是错的：pi-mono 同样只构造一个扁平字符串，所以采纳它的形状**不需要**
结构化的第二输入。真正的改动是那份专用 UPDATE 提示词，加上把被携带的摘要移出淘汰池。

### 6.3 摘要输入的上限

| | agentao | pi-mono |
|---|---|---|
| 转录总量上限 | `max(2000 tok, max_tokens × 0.10)`（`context_manager.py:750`），**从新到旧**分配，保证幸存者是连续后缀，接缝处放一个省略标记（`_join_within_budget`，`:1018`） | **无** |
| 单条工具结果 | 1 000 字符只留头部；**命中 `_FAILURE_MARKERS` 时 4 000 字符头+尾**（`:723,734,943,774`） | 2 000 字符，**只留头部**（`utils.ts:89,95`） |
| 单条普通消息 | 2 000 字符 | 无上限 |
| assistant thinking 块 | 计入消息预算 | 无上限（`utils.ts:133`） |
| 携带的旧摘要 | 8 000 字符，且再封顶到半个预算 | 无上限；追加在转录之后，不参与淘汰竞争（§6.2） |

由此有两点。

**pi-mono 对发给摘要模型的内容没有全局上限。** 唯一的限制器是单条工具结果 2 000 字符；用户文本、
assistant 文本、thinking 块都是整条序列化的。在 184K 触发、保留 20K 的情形下，`messagesToSummarize`
覆盖约 164K token 的历史。这在实践中是否真的溢出，取决于工具结果与散文的比例，而**我没有实测**——
工具结果通常是大头，每条封顶 2 000 字符很可能已经把序列化文本压得够小。但结构性论断与此无关：没有东西
限制它。agentao 的 `_SUMMARY_INPUT_BUDGET_RATIO` 之所以存在，正是因为摘要失败会让熔断器计数加一，
把一个"输入过大"的问题变成一次"压缩停摆"。

**pi-mono 的只留头部截断砍错了那一端。** `truncateForSummary` 就是 `text.slice(0, maxChars)`。
一条失败命令的诊断信息——traceback、断言、非零退出——都在**末尾**。而它自己的提示词要求
"Preserve exact file paths, function names, and error messages"；只留头部正是丢掉这些东西的方式。
agentao 的 `_FAILURE_MARKERS` 分级（`context_manager.py:774`）刻意锚定在诊断**形状**而非裸词上，
就是为了避免过度分级（实测：形状正则在 272 个源文件里命中 9 个，而
`traceback|exception|\berror\b` 的裸词扫描命中 169 个）。

---

## 7. 存储模型：破坏性改写 vs 会话树

| | agentao | pi-mono |
|---|---|---|
| 压缩产出什么 | 一个新列表 `[boundary_marker, summary, file_hint?, pinned…, kept…]` 替换 `agent.messages`（`context_manager.py:477`） | 一个 `CompactionEntry{summary, firstKeptEntryId, tokensBefore, details, usage}` 追加进会话树（`session-manager.ts:1097`） |
| 模型随后看到什么 | 那个列表 | `buildSessionContext()`（`session-manager.ts:461`）渲染 `[compactionSummary]` + 从 `firstKeptEntryId` 起的每个条目（`:404-452`） |
| 原始消息去哪了 | 从内存消失，且**在默认配置下完全消失**——SQLite 行不存消息、replay 关闭、session 保存写的是压缩后的列表（见下） | **仍挂在树上、可寻址**——默认会写入会话文件；`--no-session` 下（`SessionManager.inMemory`，`main.ts:358`、`session-manager.ts:1569`）只存在于运行期树中，进程退出即消失 |
| 后果 | 压缩是单向的 | 可以跨越压缩边界往回导航；`branch-summarization.ts` 的存在就是为了给你离开的那条分支做摘要 |

**在默认配置下，被丢弃的原文在任何地方都没有持久副本：**

- `session_summaries` 只存 `summary_text` 与计数——**不存原始消息**（`memory/storage.py:44`）。
- replay **默认关闭**（`replay/config.py:36`，`REPLAY_DEFAULTS = {"enabled": False, …}`）。
- 普通 session 保存写入的是**当前**消息列表（`embedding/sessions.py:145`），压缩之后即为压缩后的那份。

所以 agentao 的压缩不只是对**恢复**而言单向；在默认配置下那些文本就是没了。（rev 2 曾称它留存于
replay + SQLite、"就取证而言没有丢东西"——§14-2。）打开 replay 能得到最接近的一份，但**不是字节级原样**：每条事件在落盘前都要过 `sanitize_event`
（`replay/recorder.py:135`），其中包含一个始终开启的凭据扫描器，会就地改写命中项（`replay/sanitize.py`，
经 `replay/redact.py::scan_recursive`）。所以 replay 副本是一份**脱敏**记录，足以审计，不保证足以重建。
这比本节原先的描述**扩大**了差距而非缩小，也是 §1 结论表现在把它列为观察项的原因。它是否应该改变，是关于 agentao 会话模型的产品问题，不是压缩代码的缺陷。

pi-mono 还在条目上串了一个带类型的 `details` 泛型，那是扩展的逃生舱（§8）——扩展可以把 artifact 索引
或版本标记存在摘要旁边。

---

## 8. 可扩展性——P1

| | agentao | pi-mono |
|---|---|---|
| 压缩前钩子 | `PreCompact` 插件钩子，`_dispatch_pre_compact(...) -> None`，注释写明"side-effect only"（`runtime/chat_loop/_hook_dispatch.py:163`） | `session_before_compact`，返回 `SessionBeforeCompactResult { cancel?, compaction? }`（`extensions/types.ts:592,1133`） |
| 能否否决？ | 不能 | 能——`result.cancel` 中止压缩（`agent-session.ts:1903`） |
| 能否替换结果？ | 不能 | 能——`result.compaction` 原样取代内置摘要器（`agent-session.ts:1907`） |
| 事后事件 | `CONTEXT_COMPRESSED` 宿主事件 | `session_compact`（`types.ts:606`）、`session_compact_failed`（`:617`），外加带 `reason: manual\|threshold\|overflow` 的 `compaction_start` / `compaction_end` UI 事件 |
| 可取消 | 无 | 每次压缩一个 `AbortController`，手动与自动分开（`agent-session.ts:332`），`abortCompaction()`（`:2017`） |
| reason 词表 | 钩子载荷上的 `compaction_type` + `reason` 字符串 | 每个事件上带类型的 `"manual" \| "threshold" \| "overflow"`，外加 `willRetry` |

官方参考扩展（`examples/extensions/custom-compaction.ts`）演示了这种替换：把摘要模型换成 Gemini
Flash，并把**两半**（`messagesToSummarize + turnPrefixMessages`）合成一次调用来摘要，而非两次。

**rev 3 更正（§14-10）。** rev 2 说它"只留摘要"。并非如此——它原样返回
`preparation.firstKeptEntryId`，注释就写着"Use firstKeptEntryId from preparation to keep recent
messages"（`custom-compaction.ts:100-107`），近期窗口与默认路径一样被完整保留。这里有个陷阱值得点名：
该示例**自己的文件头注释**声称它"完全丢弃所有旧回合、只留摘要"，与它的代码矛盾。rev 2 信了注释、
没读返回值——错仍然在我，但请在别人重蹈之前记下这处上游不一致。

**为什么这一条对 agentao 尤其是 P1。** agentao 的自我定位是带宿主稳定边界的嵌入式 harness
（`docs/design/embedded-host-contract.md`）。压缩是唯一一个**永久改写宿主对话**的操作，也是唯一一个
宿主没有发言权的操作。agentao 里其他每一项可比的策略——权限、工具白名单、LLM 额外参数——都是可注入的。
只有这一个是只读。

但这不是一条"移植 `session_before_compact`"的工单。pi-mono 的版本被它的树存储塑形：`CompactionResult`
带 `firstKeptEntryId`，在 agentao 的扁平列表里没有对应物。适配到 agentao 的问题更窄：**可替换的最小单元
是什么？** 合理答案从"允许钩子否决"到"允许钩子提供摘要文本"再到"允许钩子提供整份替换消息列表"都有，
它们的影响半径差别很大，没有哪个显然正确——那是一次设计决策，不是一次移植。

---

## 9. 失败处理

### 9.1 摘要调用失败时

- **agentao：** `_summarize_messages`（`context_manager.py:832`）吞掉一切异常并返回 `""`。调用方把
  `_consecutive_compact_failures` 加一（`:590`）并原样返回历史。**不重试。**
- **pi-mono：** 每次摘要都走 `completeSummarization`（`compaction.ts:565`），它用会话配置的重试策略把
  调用包进 `retryAssistantCall`，于是一次瞬时流中断不会让整次压缩失败。

### 9.2 熔断器——P1

`compaction_circuit_open` 在连续 3 次失败后返回 true（`context_manager.py:423,72`），而
`compress_messages` **在做任何尝试之前**就据此短路（`:507`）。唯一的复位是 `:593` 处的
`self._consecutive_compact_failures = 0`，它在摘要成功**之后**——也就是在短路的下游。

于是：一旦打开就不再尝试；不尝试就没有成功；没有成功就不会复位。压缩在该 `ContextManager`
**实例**的余生里被禁用——不是进程：新建一个 `Agentao` 会是干净的，而 `/clear` 虽然开新会话，
**并不重建 context manager**，所以也不会复位它。CLI 里没有任何地方复位它
（`grep -rn '_consecutive_compact_failures' agentao/` → 8 处命中，全在 `context_manager.py` 内）。

**而且它同样拦住手动 `/compact`。** 熔断检查位于 `context_manager.py:506`，在 `compress_messages`
顶部、任何 `is_auto` 分支之上且与之无关；`:590` 的摘要失败计数同样是无条件的，所以一次失败的手动
压缩也会推进计数。`:539-546` 的 `is_auto` 豁免**只**覆盖"无安全切点"那个计数。于是，用户唯一可能
用来从熔断状态里恢复的路径，本身就被它挡住。

这一点是已知的——`runtime/chat_loop/_compaction.py:88` 的注释明确写着"the counter has no reset path"，
那处 stand-down 之所以还要打一条 warning，正因为那行日志是唯一的信号。这里记录它，是因为 pi-mono 的设计
没有对应物：它按次重试、不带永久闩锁，所以一串瞬时失败无法让整个会话的压缩失效。

加重试包装和加复位路径都能处理它，而且两者独立——任一单独实施都有用，它们不是二选一。

### 9.3 溢出恢复

| | agentao | pi-mono |
|---|---|---|
| 检测 | 对抛出的异常做 `is_context_too_long_error(exc)`——21 条正向模式 + 4 条负向守卫（`context_manager.py:1235-1290`） | 对 assistant 消息做 `isContextOverflow(message, contextWindow)`——25 条正向 + 3 条负向，外加两种非错误情形（`ai/src/utils/overflow.ts`） |
| 非错误型溢出 | 未覆盖 | **已覆盖**：静默溢出（`stopReason === "stop"` 但 `input + cacheRead > contextWindow`，z.ai）与 length-stop 溢出（`stopReason === "length"`、`output === 0`、input ≥ 窗口的 99%，小米 MiMo）。与这两者**互相独立**，`isRecoverableLength`（`agent-session.ts:2076`）还会把"输出低于模型本来意图上限"的 length-stop 也导入同一条 compact-and-retry——所以 pi-mono 的覆盖面**比表里那个 99% 情形更宽**（§14-7） |
| 恢复 | **2 级**：一次 `compress_messages`（`_runner.py:1167`）→ 重试 → 再次溢出则 `messages[-2:]`（`:1204`）→ 重试 → 报错。rev 2 写成 3 级（§14-7） | 1 次 compact-and-retry，由 `_overflowRecoveryAttempted` 闩住（`agent-session.ts:2090`） |
| 守卫 | — | `sameModel`——消息来自不同 provider/模型时跳过溢出压缩（`agent-session.ts:2079`）；陈旧边界检查跳过早于最近一个压缩条目的消息（`:2070`） |

agentao 的阶梯更深，但它的最后一级**并不保证前进**：`messages[-2:]` 自身也可能超窗——一条超大工具
结果就够了——此时第三次调用把错误返回给调用方。pi-mono 的检测更宽：那两种非错误型溢出对应的是
"接受超长 prompt 且不报错"的 provider，agentao 基于异常的检测按构造就看不见它们；而
`isRecoverableLength`（`agent-session.ts:2076`）又在两者之外加了一条语义路径。

`context_manager.py:1234` 的注释已经把两级"正向 + 守卫"结构的出处记在 pi-mono `overflow.ts` 名下，
说明模式表此前已核对过一次；§11 记录了增量差。

---

## 10. agentao 更强之处——不要反向借鉴

记录在此，以免后来者在这些点上向 pi-mono"对齐"：

1. **CJK token 估算**（§4.1）。`chars/4` 对中文低估约 5 倍。
2. **摘要输入有界**（§6.3）。pi-mono 只约束了响应。
3. **失败感知的头+尾裁剪**（§6.3）。pi-mono 只留头部会丢掉 traceback。
4. **微压缩**——55–65% 区间一个完全不调 LLM 的廉价层（`context_manager.py:377`），带不动点保证，
   并有 `microcompact_would_mutate` stand-down，避免空操作也每轮 fork 一个钩子子进程。
5. **通用的落盘外溢。** `.agentao/tool-outputs/` 在 40 000 字符处生效，经由结果格式化层覆盖**所有**工具
   （`runtime/tool_result_formatter.py:29,33`），摘录里还邀请模型 `read_file` 取回。pi-mono 的
   `fullOutputPath` 只有 bash 有（`core/tools/bash.ts:55`）。
6. **溢出阶梯的最后一级**（§9.3）。
7. **转录里的工具调用参数渲染。** `_format_tool_call_args`（`context_manager.py:1088`）解析 JSON 并
   按值长度升序输出，于是一个超长的 `write_file` body 无法把它旁边的 `file_path` 挤掉。pi-mono 的
   `serializeConversation` 按插入顺序渲染 `k=JSON.stringify(v)`，且没有单值上限。

---

## 11. 已核对、判定对等——不要重复上报

> 手动 `/compact` 在 rev 2 曾列在本节，它**并不对等**——agentao 的手动路径会被熔断器拦住，pi-mono
> 不会。已移至 §9.2（§14-5）。

- **工具结果失联。** 两侧都拒绝切在工具结果上。agentao：`_find_split_index` 跳过 `role == "tool"`
  （`context_manager.py:434`）。pi-mono：`isCutPointMessage` 对 `toolResult` 返回 false
  （`compaction.ts:308`）。
- **摘要期间抑制工具调用。** agentao 传 `tools=None`；pi-mono 设 `toolChoice: "none"`，并额外在返回
  `toolCall` 块时抛错。严格程度不同，对合规 provider 的结果相同。
- **溢出模式表。** 结构上是同一套两级设计，`context_manager.py:1234` 已注明出处。增量差：pi-mono 多出
  GitHub Copilot、MiniMax、DS4、Cerebras（`400/413 (no body)`）、z.ai 的
  `model_context_window_exceeded`；agentao 多出阿里/DashScope 的 `internalerror.algo.invalidparameter`。
  两份表互不包含。
- **摘要持久化。** agentao 写一行 SQLite `session_summaries`（`memory/manager.py::save_session_summary`）；
  pi-mono 在**会话被持久化时**把 `CompactionEntry` 写进会话文件——`--no-session` 下只在内存里。
  机制不同；在各自的默认路径上都是持久的。
- **文件操作抽取。** 两侧都从被摘要窗口里收集路径往下传：agentao 的 `_extract_recently_read_files`
  （最近 10 个 `read_file` 路径，渲染成一条 system 提示）；pi-mono 的 `extractFileOpsFromMessage`
  （read/written/edited 三个集合，渲染成 `<read-files>` / `<modified-files>` XML，并通过 `details`
  跨压缩累积）。pi-mono 的更丰富——区分读与改，且跨边界累积——但这是程度差异，不是缺口。

---

## 12. 不建议

- **pi-mono 约 92% 的晚阈值。** 它与 pi-mono 的树存储、以及它在产出时就设上限的输出策略是自洽的。
  只抄数字不抄其余，等于在保留 agentao 单向改写的同时抹掉它的余量。
- **分支摘要**（`branch-summarization.ts`）。它服务于树导航。agentao 没有会话树，也就没有可供它摘要的
  分支间隙。
- **为对齐 pi-mono 的单层结构而砍掉廉价层。** 微压缩是 agentao 更强的地方之一（§10.4）。
- **codex 那两个独立触发点 `ModelDownshift` / `CompHashChanged`**（`turn.rs:1100,1145`）。它们存在是
  因为 codex 在回合边界检查、且要用**旧模型**做压缩；agentao 每轮检查，窗口配置一经校正就会自行用上，
  它们即为重复机制。
- **把 agentao 的检查改到回合边界**以对齐 pi-mono。这根轴上 agentao 与 codex 持平、优于 pi-mono
  （§3.3），改了只会白白失去回合内压缩能力。

---

## 13. 什么会推翻本文结论

- **§8 的 P1** 若 agentao 通过别的宿主侧手段获得了对压缩的控制权（例如经嵌入面注入
  `ContextManager` 子类），力度就会减弱。再次断言前先查 `agentao/embedding/`。
- **§9.2 的 P1** 是无条件的——它是内部自相矛盾，即使一行 pi-mono 都不借鉴也仍然成立。
- **§6.3 的"pi-mono 无上限"** 是结构性论断，非实测。若有人实测真实 pi-mono 会话，发现 2 000 字符的
  工具结果上限已经把请求压得够小，那么其中的**风险**主张会减弱；**结构**主张不会。
- **§3.4 是一条关于校验的 P2，不是所有权上的缺陷。** 窗口在设计上、文档上都归宿主所有；缺的是
  "有没有东西检查宿主配对了"。若已存在本文遗漏的宿主侧约定或 doctor 检查能暴露窗口不匹配，这条会减弱
  ——本文没找到，但请重跑 grep 而不是信这一行。反之，若有人举出"CLI 默认值与某个常用模型静默不匹配"
  的常见部署，这条会**加强**。
- **§3.4 的退化路径是从所引常量推导的，不是在会话里实测的。** 算术（130K 阈值 vs 32K 窗口）直接成立；
  但被拒调用的**频率**、以及真实负载是渐进增长（跳过廉价层）还是跳跃增长（可能落入廉价层，也可能
  直接越过进入全量压缩），两者都未实测。埋点跑一次会话即可定论。
- **§3.3 的 codex 材料仅限触发轴**，锚点 `2151d3a5b7`。不要把此处任何 codex 论断外推到其他章节；
  `codex-compaction-vs-agentao.zh.md` §13 是更完整那份对照的勘误表，里面列着已被撤回过的结论。
- **锚点会过期。** pi-mono 迭代很快（`a69bef789` 只是 2026-08-23 当天若干提交之一），codex 更快。
  据此行动前，重新核对每一处 `file:line`。
- **§14 是勘误表。** rev 2 有 14 条主张被撤回或重新界定。再次提出任何结论前先读它，不要凭记忆重复
  上报已撤回的主张。

---

## 14. rev 3 勘误——rev 2 错在哪里

14 条全部由维护者评审提出，且**每一条都回源复核后才被接受**，没有一条是凭断言采信的。编号与评审一致。

> **本表是历史，不是覆盖层。** rev 3 把修正记在了这里，却把其中若干条错误表述留在了 §1、§3.3、§6.3、
> §7、§9、§13 里——于是全文同时断言了一条主张和它的反驳。rev 4 重写了那些位置；保留本表，只是为了
> 让还记得 rev 2 结论的读者知道它为什么消失了。**不要从本表反推任何现行主张**——去读它指向的那一节。

| # | rev 2 的主张 | 为什么是错的 | 复核位置 |
|---|---|---|---|
| 1 | "上下文窗口不随模型走"是无条件的内部 **P1** | 窗口是**有文档记载的宿主所有旋钮**，共四个面；而被当作缺陷证据的那段 ACP 行为恰是白纸黑字、有理由记录的契约。降级为 **P2**，并重新界定为*无校验／无告警／无协调*。定性句"两个对照方以同一方式不同就是缺口"**予以撤回**——同侪趋同描述的是选项空间，不能据以判定占据另一点的设计有罪 | `agent.py:104`；`embedding/factory.py:132`；`cli-host-agent-factory.zh.md:104`；`acp-stdio-auth-fix-plan.md:99-110` |
| 2 | 被丢弃的原文在 replay + SQLite 里留存，故"取证没有丢东西" | **在默认配置下不成立。** `session_summaries` 只有 `summary_text` 与计数；replay 默认 `enabled: False`；普通 session 保存写入的是已压缩的列表 | `memory/storage.py:44`；`replay/config.py:36`；`embedding/sessions.py:145` |
| 3 | 退化路径被写成无条件结论："每个 turn"被拒、廉价层"永远到不了"、"阶梯会恢复"、静默截断"从头部丢失" | 每一条都需要条件。不是每个 turn——只有历史再次越过真实窗口时才会。廉价层只在**渐进**增长下被跳过，跳跃式增长仍可进入该带。`messages[-2:]` 自身也可能超窗，第三次调用直接报错。静默截断的 provider 丢哪一端**未经查证** | §3.4，已重写 |
| 4 | codex 六个触发点全部汇入 `run_auto_compact` | **五个自动入口**如此。手动 `/compact` 派生独立 `CompactTask`，在任务内做自己那套平行四路分派 | `codex .../session/handlers.rs:244`；`.../tasks/compact.rs:29` |
| 5 | agentao 手动 `/compact` 绕开自动路径的失败记账（曾列为**对等项**） | 熔断检查位于 `compress_messages` 顶部、在任何 `is_auto` 分支之上，因此手动同样被拦；摘要失败计数是无条件的。`is_auto` 豁免只覆盖"无安全切点"那个计数。已移出 §11 并并入 §9.2——它是加强而非限定 | `context_manager.py:506`、`:590`、`:539-546` |
| 6 | pi-mono 的 `output` 项使其数字"不是 prompt 大小" | assistant 的可见输出会成为历史并**被重发**，故 `input+output` 相当贴近下一次 prompt。真正多计的范围更窄：隐藏 reasoning token 与 provider 特有的 output 记账 | `compaction.ts:146` |
| 7 | agentao 溢出阶梯是 3 级（压缩→再压缩→`messages[-2:]`）；pi-mono 的非错误覆盖即那个 99% 窗口情形 | **2 级**——恢复路径里只有一次 `compress_messages`。且 pi-mono 另有 `isRecoverableLength` 也导入 compact-and-retry，覆盖面比表里更宽 | `_runner.py:1167`、`:1204`；`agent-session.ts:2076` |
| 8 | pi-mono 把旧摘要作为"独立的结构化输入"，"压根不在同一份分配里竞争" | 它把 `<conversation>` 与 `<previous-summary>` 拼成**一个字符串**、作为单条 user message 发送，与 agentao 形状相同。它规避的是 agentao 的*本地从新到旧分配器*，不是 provider context 层面的竞争。所引的采纳代价（"需要结构化的第二输入"）同样是错的 | `compaction.ts:670-680` |
| 9 | 回合中间切分后，保留窗口"没有任何关于源头请求的记录" | 源头 user 消息通常就在 `messages[:split_index]` 里，而那正是送进摘要器的部分，故在摘要中有体现。准确的缺口是：没有*专用*前缀摘要，因而不**保证**它挺过预算裁剪与摘要压缩 | `context_manager.py:558` |
| 10 | 官方示例"全部摘要、只留摘要" | 它原样返回 `preparation.firstKeptEntryId`，**保留近期窗口**。值得点名的陷阱：该示例自己的文件头注释与其代码矛盾 | `custom-compaction.ts:100-107` |
| 11 | 表格把 `16384` 标成笼统的"保留量" | 它是 `reserveTokens`，即**响应余量**。原文保留量是 `keepRecentTokens = 20000`，混为一谈会误读设计 | `compaction.ts:132`；`settings-manager.ts:839` |
| 12 | 熔断器打开会让压缩"在整个进程剩余生命周期内"报废 | 它是 `ContextManager` **实例**状态。新建 `Agentao` 是干净的；`/clear` 不重建 manager，故也不复位 | `context_manager.py:91` |
| 13 | 图片会普遍低估估算值 | 范围限于**上一次 API 锚定之后**新追加的图片——已锚定的前缀按真实 `prompt_tokens` 计费，其中已含 provider 的图片成本 | `context_manager.py:153`、`:192` |
| 14 | README 索引条目写"任何非 200K 模型"，且 P1 数量自相矛盾 | 只有窗口**小于**配置值才会退化成应急阶梯；大于配置值只是过早压缩。数量随 §14-1 的降级一并修正 | `docs/design/README.md` |

**方法记录。** 第 1、8、10 条有一个共同的根因值得点名：rev 2 读的是**注释或文档行**，据以推断行为，
而没有去读产生该行为的代码路径——ACP 的那段注释（1）、prompt 构造里的标签名（8）、示例的文件头注释
（10，而它恰恰与自己的 return 语句矛盾）。教训就是本仓库既有的那一条：**验证的是 sink，不是贴在它上面
的标签。**
