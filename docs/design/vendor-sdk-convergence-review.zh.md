# 厂商 SDK 趋同 —— Path A 再审视触发评审

**状态：** 战略评审记录。起草于 2026-06-18，基于一次 2026 年 6 月的竞品格局扫描（CLI
agent、可嵌入 agent-loop SDK、通用 agent 框架）。**这是一次"触发判定"，不是战略反转。**
本记录确认 `path-a-roadmap.md` §16.4 的"外部变化"条件很可能已经满足，列出证据，并
*供维护者判断*——提出什么该变、什么不该变。它**不**单方面退役或重写 Path A；在维护者
做出决定之前，路线图 §1–§8 保持锁定。
**读者：** Agentao 维护者与战略评审者。
**对照件：** `vendor-sdk-convergence-review.md`。
**相关：**
- `path-a-roadmap.md` —— 本评审据以检验的、已锁定的 embed-first 战略（尤其 §2.1 成功指标、§2.3 非目标、§7.1 模式二、§16.4 退役触发条件）。
- `embedded-host-contract.md` —— 本评审确认*并非问题所在*的宿主契约设计。
- `pi-mono-borrow-review.md` / `codex-reverse-review.md` —— 本记录遵循的反向评审纪律（先证据后建议；gap ≠ need）。

**方法：** 竞品扫描 → 找出唯一一个晚于路线图锁定的市场变化 → 逐轴重估 Agentao 护城河
→ 对衍生方向做 keep/cut/gate → 判定 §16.4 是否触发。代码引用锚定于 `main`@`bcdb8e4`
（2026-06-18）；关于外部 SDK 的定位陈述带有日期、会漂移——在据其行动前请重新核实。

---

## TL;DR

Path A 路线图**锁定于 2026-04-30**。此后，两大模型厂商各自交付了可嵌入、受治理、带沙箱、
从宿主自身 Python/TypeScript 进程驱动的 agent loop——**正是 Path A 的字面卖点**：

- **Claude Agent SDK** —— "驱动 Claude Code 的同一个 agent loop……从你自己的 Python/
  TypeScript 程序里驱动"，四层权限管线 + 可选沙箱。
- **OpenAI Agents SDK** —— 沙箱执行 + model-native harness（隔离工作区、文件级权限、
  快照/恢复）。非 OpenAI 模型经 Any-LLM / LiteLLM adapter 接入，官方文档标注为
  **best-effort / beta**，在工具调用、结构化输出、usage 上报上存在能力差异——即覆盖面广的
  adapter，*而非* Agentao 式的一等 provider 中立。

**结论：** "可嵌入的受治理 Python agent loop"已不再是差异化——它已成为 Anthropic 与
OpenAI 凭借更大分发与原生模型集成提供的"标配"。这恰是 `path-a-roadmap.md` §16.4 命名
的再审视/退役触发条件。**嵌入契约不是问题（P0 已证明它干净）；问题是"宿主为何要嵌入
Agentao 而非厂商自家 SDK"这个问题的答案。**

**处置（提议，待维护者批准）：**
- **§16.4 触发：已触发（再审视，非退役）。** embed-first 论点被收窄、而非失效——仍有可守
  护城河（§3）。应召集"幸存护城河"决策，而非直接开 Path B/C 文档。
- **立即做（文案，近零代码）：** 把差异化标题从"可嵌入"（已商品化）改为"provider 中立 +
  本地/隐私优先 + 可治理可审计"（§4 D1）。
- **立即做（分发）：** 修复可发现性/重名拖累（§4 D2、§5）。
- **重构定位（渠道）：** 把已占代码库 ~21.5% 的 ACP 投入当作乘着**早期但真实**生态信号的
  *分发渠道*，而非仅是功能（§4 D3）。
- **重排优先级但仍门控：** 路线图 P1.1（`on_usage_event`）与 P1.2（OTel）重要性上升，原因是
  厂商 SDK *已经*出货 usage/cost 追踪与 OpenTelemetry——所以这是**Agentao 当前缺失的标配补齐项**，
  而非厂商的薄弱点。仅在有灯塔提出时才提优先级（§4 D4）。
- **砍/搁置（与 §2.3 一致）：** CLI/TUI 打磨、托管 SaaS、强跨平台沙箱、无具名采用者的投机性 P1。

---

## 1. 本记录为何存在

`path-a-roadmap.md` §16.4 承诺了三条退役/再审视触发条件，第三条是：*"外部变化使
embed-first 论点失效（例如某个 Python 原生 agent 运行时以标准库级别的分发出货）。"* 路线图
同时承诺（§16.2）在每个检查点重读 §1–§8 并自问*"成功图景是否仍是对的成功图景？外部变化……
可能已改变'嵌入'的含义。"*

2026 年 6 月的扫描浮现出一个足够大、足以检验该触发条件的变化。本记录将其隔离、评估其影响、
提出处置——遵循与借鉴评审相同的"先证据"纪律（`pi-mono-borrow-review.md`：*"第一轮清单偏向
'架构上有趣'而非'确实缺失'"*）。此处对称的风险是对竞品发布反应过度；§3 与 §4 的写法即为防此。

## 2. 竞品格局（2026 年 6 月）

Agentao 处在四个品类的交叉点，每条轴上都被单独挑战：

| 品类 | 2026 领头羊 | Agentao 位置 |
|---|---|---|
| **A. CLI 编码 agent 产品** | opencode（~150k★、~650 万月活）、Claude Code、Codex CLI、Gemini CLI、Aider、Goose（已入 Linux 基金会）、Crush、Continue `cn` | CLI 能打，但 **Path A §2.3 已让出此战场**（"opencode 已赢 TUI"）。 |
| **B. 可嵌入 agent-loop SDK** —— *Path A 的真正战场* | **Claude Agent SDK**、**OpenAI Agents SDK**、Vercel AI SDK、Pi | **被一方厂商 SDK 新挤入。** 即 §3 的变化。 |
| **C. 通用 agent 框架** | pydantic-ai（类型契约）、smolagents（代码优先）、Agno（~39k★）、Strands、LangGraph、Instructor | *自己搭 agent 的工具箱。* Agentao 不同：**开箱即用**（工具+技能+权限+记忆+replay 已接好），非编排套件。真实且稳定的区分。 |
| **D. 编码 agent 平台** | OpenHands、SWE-agent | 托管产品/研究，不在 Path A 范围。 |

要点不是"Agentao 在四场赛跑里落后"，而是 Agentao 的身份是一个*组合*（受治理、provider
中立、开箱即用的编码运行时，同时又是干净的可嵌入库），这一组合仍然独特——尽管**每条单轴都
被挑战**，B 类最为新近。

## 3. 变化：一方厂商 SDK 占据了 Path A 的卖点

以下两个事实均晚于 2026-04-30 路线图锁定：

- **Claude Agent SDK** 让宿主"取用驱动 Claude Code 的同一个 agent loop——同样的工具执行、
  上下文管理、权限系统、子代理机制——从你自己的 Python/TypeScript 程序里驱动"，带四层权限
  管线（deny → mode → allow → `canUseTool`）和可选隔离沙箱。
- **OpenAI Agents SDK** 新增沙箱执行与 model-native harness：带文件系统 + shell 的隔离类
  Unix 工作区、文件级权限、可持久的快照/恢复状态。它经 Any-LLM / LiteLLM adapter 接入非
  OpenAI 模型，官方文档将其标注为 **best-effort / beta**，相对原生 OpenAI 路径在工具调用、
  结构化输出、usage 上报上存在能力差异。

对照 Agentao README 首句——*"本地优先、隐私优先、可嵌入 Python 宿主的 agent harness"*——
其中**"可嵌入/带权限/子代理/沙箱"部分现已被两家厂商对齐**，而它们带来 Agentao 无法企及的
分发与原生模型集成。P0 交付的干净嵌入契约是必要的，但已不再是充分的差异化。战略问题从
*"嵌入契约干不干净？"*（干净）转为*"宿主为何要嵌入 Agentao 而非厂商自家 SDK？"*

## 4. 逐轴重估护城河

### 仍可守 —— 厂商 SDK 结构上难以匹配

1. **天生 provider 中立*且无厂商数据链路*。** OpenAI 兼容的*任意* provider + 运行时
   `/provider` 切换（`agentao/runtime/`、`LLM_PROVIDER`）。需精确区分：厂商 SDK 现已出货
   *广覆盖的第三方 provider adapter*（Claude SDK 可指向其他模型；OpenAI 的 SDK 经
   Any-LLM/LiteLLM 覆盖 100+，best-effort/beta）——所以"能指向另一个模型"**已不再为 Agentao
   独占**。仍独占的是*姿态*：agentao 不是套了 adapter 的厂商 loop、不带厂商 harness 文化、
   不经任何厂商数据链路。护城河是 **no-vendor-loop / no-vendor-telemetry**，而非仅仅"多模型"。
2. **本地优先/隐私优先/无托管基建/无全局状态。** 能力注入、不污染宿主 logger、多实例隔离
   （`tests/test_multi_agentao_isolation.py`、`tests/test_no_host_logger_pollution.py`）。
   *无法*把代码经厂商 loop 路由的宿主，就是楔子。
3. **本地、进程内的审计契约。** 权限模式 + replay JSONL 审计 sink（v1.2，
   `agentao/host/replay_projection.py`）+ `events()` 宿主事件流。**此处不可夸大：**两家厂商
   SDK 均出货一等的治理/可观测——Claude Agent SDK 列出 hooks、permissions、sessions、cost/usage
   追踪、OpenTelemetry、checkpointing；OpenAI 的 SDK 有 tracing、usage、沙箱 permissions/results/
   resume state。Agentao 更窄、可守的优势是其审计轨迹是**在 no-vendor-loop 姿态下的本地 JSONL
   replay 产物**——记录落在宿主自己的文件里，而非厂商的 tracing 后端——这对无法向厂商发送遥测的
   宿主才是相关属性。这是*本地性/所有权*优势，不是"厂商没有审计"优势。
4. **ACP-server 互操作 —— 乘着早期但真实的生态信号。** 代码库中 `acp/` + `acp_client/` 的
   ~21.5%（11,659 / 54,205 LOC，2026-06-18 核实）与一个新兴标准对齐。**今日可核验：**ACP 由
   Zed 创建、Zed 出货 ACP 客户端；Google Gemini CLI 有文档化的 Zed/ACP 集成。**已声称但本记录
   尚无一手来源（按早期信号处理，权衡前先核实）：**JetBrains 全 IDE 支持、GitHub Copilot CLI 的
   ACP 支持、以及"25+ agent"这一计数——这些来自二手汇总，而非厂商文档。净结论：ACP 是"可插进
   ACP 编辑器的、provider 中立的受治理 agent"的一个*可信、增长中的*渠道，但还**不是已确认**的
   顺风。D3 的权重应随上述未核实声称的成立比例而定。
5. **中文生态。** jieba 中文记忆分词、双语文档、`agentao.cn`。被美国厂商 SDK 结构性服务不足；
   契合路线图 §14 灯塔外联计划（中文社区 FastAPI / pytest / Jupyter 候选）。

### 被挑战/被侵蚀

- **单凭"可嵌入"** —— 已成标配（§3）。
- **CLI 体验** —— 已让给 opencode（§2.3，无变化）。
- **沙箱成熟度** —— OpenAI/Claude 已上容器快照；Agentao 的 macOS `sandbox-exec` 更窄。
  **不衍生任何动作**——§2.3 已把强跨平台沙箱列为非目标；厂商此举*印证*而非推翻该判断。

## 5. 一个可量化的分发拖累

**方法快照（供复验）：** 查询 `"agentao" python agent framework github`，于 2026-06-18 经
助手的 `WebSearch` 工具运行（US locale、登出态、前 10 条结果）；首个自然结果是无关的
`github.com/taoagents/agentao`（"ridges-old" 仓库），且**`jin-bo/agentao` 未出现在前列。**
搜索排名随地区、登录状态、时间漂移——引用为现状前请以无痕/登出态重跑并记录引擎 + 日期。截至
本快照：鉴于 §2.1 的成功指标是*"被依赖/被找到"*，这种重名 + 可发现性缺口是对 dependents 目标
的直接、可量化拖累——并独立于 §3 的变化印证了 §7.1 模式二（*"问题在分发，不在技术"*）。与
护城河问题不同，这一条无歧义且行动成本低。

## 6. 衍生方向 —— keep / cut / gate

作为供维护者判断的选项（gap ≠ need；痛点由维护者判定；按需门控仍成立）。按杠杆排序。

| ID | 方向 | 处置 | 理由 |
|---|---|---|---|
| **D1** | 重写差异化标题：以"provider 中立 + 本地/隐私优先 + **可治理可审计**"打头，"可嵌入"降级 | **立即做**（文案，近零代码） | 直接回答新的"为何不用厂商 SDK"；现 README 以已商品化的词打头。 |
| **D2** | 修复可发现性：仓库 topics、PyPI 关键词、一页"vs Claude/OpenAI Agent SDK"对比；主攻中文生态楔子 | **立即做**（分发） | §5 无歧义；§14 已把分发列为最主要的非工程风险。 |
| **D3** | 把 ACP-server 当*渠道*而非仅功能（"插进 ACP 编辑器的、受治理、provider 中立的 agent"） | **重构定位（权重门控于 §4.4 核实）** | 把已有大块代码投入变成分发；乘早期但真实的 ACP 信号。权重应随 §4.4 未核实采用声称（JetBrains / Copilot CLI / "25+ agent"）经一手来源核实的成立比例而定。属 harness 范畴（互操作，类比 `acp_client`）。 |
| **D4** | 路线图 P1.1 `on_usage_event` + P1.2 OTel | **重排优先级，仍门控** | 这是**标配补齐项**——两家厂商 SDK 已出货 usage/cost 追踪 + OpenTelemetry，故是 Agentao 补缺口，而非利用厂商弱点。重要性↑，但**仅在灯塔提出时才启动**——§4 纪律不变。 |
| **D5** | 互操作桥：从 Agentao *驱动* Claude/OpenAI Agent SDK 的 agent，或把 Agentao *暴露*在其工具接口下 | **搁置（按需门控）** | 通过 harness-vs-product 检验（互操作，类比 `acp_client`），但无具名宿主前属投机。记为候选，非承诺。 |
| — | CLI/TUI 打磨 · 托管 SaaS · 强跨平台沙箱 · 投机性 P1 | **砍/搁置（不变）** | 与 §2.3 及既往边界否决一致；§3 的变化强化而非重开这些判断。 |

## 7. §16.4 触发判定

**判定（提议）：§16.4 第三条触发条件已触发——作为再审视，而非退役。**

- 它**不是**失败路径的 §7.2 守门线（那是 PyPI dependents 连续三月平淡；本记录不对指标趋势
  下任何结论——2026-07-31 的 M+3 检查点仍管这件事）。
- 它**不是**成功路径的后继文档（那是 2027-04-30 的战略评审）。
- 它是**外部变化**条款：embed-first *论点*因"可嵌入"商品化而收窄，但仍有可守护城河（§4），
  故正确产出是 Path A 内的*重写标题 + 分发*回应，而非 Path B/C 转向。

按 §16.3，检查点产出为"快照说了什么 / 我们改什么 / 路线图该怎么改写"。本记录不是日历检查点。
为干净地尊重 §16.4 锁定，提议的路线图改动**按所触及的章节拆分**：

- **无需解锁的改动（活章节）：** 在 **§16.4 追加一条记录**，写明外部变化触发条件于 2026-06-18
  触发、以本文档为关联记录。§16 属检查点/指标表述，非锁定战略——若被要求，这是本记录唯一会做的改动。
- ***需要*显式解锁的改动（锁定章节）：** 在 **§2.1** 加一行，说明截至 2026-Q2"可嵌入"已是必要
  非充分。§2.1 处于锁定的 §1–§8 之内，故本记录**不**提议悄悄做此改动——而是把它标为*候选*，仅当
  维护者批准并解锁时才应用。在此之前，§1–§8 逐字保持锁定。

这避免了"勿动 §1–§8"却又改 §2.1 的矛盾：默认动作只是 §16.4 那条记录；§2.1 那行被显式解锁挡在后面。

## 8. 什么会改变这一判定

列出以便本记录可被证伪、而非被辩护：

- **若**某灯塔采用者反馈他们*正是因为*厂商中立或审计而选 Agentao → 护城河（§4）被证明强于评分；
  D1/D2 获验证，加码分发。
- **若**接下来两次月度快照显示 PyPI dependents 因中文生态楔子而上升 → D2 是主导杠杆，其余降级。
- **若**某厂商 SDK 出货一等的 provider 中立 *且* 纯本地无遥测模式 *且* 一等审计契约 → 护城河
  轴 1–3 同时被侵蚀；那才是开 §16.4 为真正失效保留的 Path B/C 文档的信号。
- **若** §4.4 未核实的 ACP 采用声称（JetBrains 全 IDE / GitHub Copilot CLI / "25+ agent"）一手
  核实不成立，或 ACP 采用停滞/碎片化 → 降级 D3，并在下个重型检查点（M+6，2026-10-31）重审 ~21.5% 的分配。

---

*本记录遵循 `pi-mono-borrow-review.md` 与 `codex-reverse-review.md` 的反向评审纪律：先证据后
建议、gap ≠ need，并附明确的证伪条款，使判定能对照现实核验、而非被反复辩论。它记录一个触发、
提出一个回应；是否批准，由维护者决定。*
