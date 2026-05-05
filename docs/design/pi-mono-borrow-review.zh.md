# pi-mono 借鉴评审（v0.66 → v0.73）

**状态：** 决策记录。2026-05-04 起草，基于对 `../pi-mono` v0.66 → v0.73 之间约 590 个提交的梳理，并对每个候选项与 agentao 现有代码（`runtime/`、`harness/`、`plugins/`、`tools/`）做了事实核对。
**读者：** agentao 维护者，决定是否（以及哪些）从 pi-mono / pi-coding-agent 借鉴。
**配套文档：** `pi-mono-borrow-review.md`（英文版）。
**方法：** 先列出乐观的候选清单 → 对照 agentao 实际模块做反向评审 → 给出最终保留 / 砍掉 / 重新框定的结论。

## 摘要

12 个候选项，最终处置：

- **立即做（1）：** grep/find `--` 参数注入修复。`agentao/tools/search.py` 已确认存在该漏洞。
- **尽快做（1）：** 项目上下文文件（`AGENTAO.md` / `CLAUDE.md` / `SKILL.md`）的紧凑 `read` 渲染。
- **作为协议补全做（1）：** 在现有 plugin-hook 体系内增加 `Stop` / `PreCompact` 事件类型。**不是**移植——agentao 已经有 Claude-Code 风格的 hook 协议，这是补齐缺失的事件类型。
- **Backlog（4）：** `prepareArguments` per-tool 归一化钩子、stale-extension-context 探测、OSC 9;4 进度、堆叠式 autocomplete。模式有用，但没有当前痛点。
- **砍掉（2）：** `shouldStopAfterTurn`（与现有 hook 重复）、self-update / 批量包更新（npm-only，与 `uv` 无关）。
- **重新框定 / 等待（3）：** `terminate: true` 工具结果提示、per-tool `executionMode = "sequential"`、bash 增量流式输出。每条在 pi-mono 都有真实但**不同的**用例；agentao 对应的需求要么不存在，要么已被另一种机制覆盖。

诚实的元结论：第一轮清单偏向"架构上有意思"，而不是"agentao 真正缺什么"。一旦对照现有的 plugin hooks、AsyncTool 框架和 per-tool-instance 锁，大部分"Tier 1"条目坍缩成冗余或过早优化。

## 反向评审结论（按候选项）

### 砍掉

#### `shouldStopAfterTurn` post-turn callback（pi-agent-core v0.72.0）
**结论：** 砍掉。与现有基础设施重复。

pi-mono 在 turn 边界增加 `shouldStopAfterTurn(...)` 这个低层 callback，是因为它的低层 loop 没有别的扩展点。agentao 已经有完整的、对照 Claude Code 设计的 plugin-hook 协议（`agentao/plugins/hooks.py` + `models.py`）：`UserPromptSubmit`、`PreToolUse`、`PostToolUse`、`PostToolUseFailure`。`chat_loop.py:140` 已经支持在 turn 边界 block / 注入 context / 提前 return。

真正缺的是**事件类型覆盖**，而不是新的 callback 形态。Claude Code 定义了 `Stop` 和 `PreCompact` 两个事件，agentao 还没支持。把这两个加进现有的 `_dispatch_lifecycle` 流水线，能拿到 `shouldStopAfterTurn` 的全部能力，并且只维护一个有文档的协议表面。

这是"补齐自己的协议"，不是"借鉴 pi-mono"。

#### Self-update + 批量包更新（pi-coding-agent v0.68.0 / v0.70.3）
**结论：** 砍掉。不适用。

agentao 走 `uv` + Python。pi-mono 的批量 npm/pnpm 更新和自我重建是 npm 生态特化的工程量，无可借鉴。

### 重新框定 / 等待

#### `terminate: true` 工具结果提示（pi-agent-core v0.69.0）
**结论：** 等待。机制本身合理，但 agentao 缺少会用到它的工具。

pi-mono 的动机是 `structured-output.ts`——返回最终答案、应当结束 run、不要触发 follow-up LLM 调用的工具。agentao 的 `ask_user` **不**匹配：用户回答之后，LLM 合理地需要再来一轮处理这个回答。

真正的候选是 `complete_task` / `final_answer` / `submit_for_review`——这些 agentao **目前都没有**。先决定要不要这些工具，机制本身在工具存在后是一天工作量。投机性移植机制就是过度设计。

#### Per-tool `executionMode = "sequential"` 覆盖（pi-agent-core v0.68.0）
**结论：** 等待。已被另一种机制覆盖。

`agentao/runtime/tool_executor.py:119-152` 用 `ThreadPoolExecutor(max_workers=8)` 配合**per-tool-instance 锁**串行同一工具的并发调用。pi-mono 用 `executionMode: "sequential"` 解决的"shell 或 write_file 在并行 batch 中的独占需求"已经被 per-instance 锁解决。

剩下的 gap 是**跨工具串行**（"shell 在跑时连 write_file 也不能起"），而这不是已声明的需求。等到有人提再说。

#### Bash 增量流式输出 + `OutputAccumulator`（pi-coding-agent v0.73.0）
**结论：** 等待。管道已铺好，但还没消费者需要。

`AsyncToolBase` 和 `EventStream` 已经支持 `tool_execution_update` 通道。`agentao/tools/shell.py` 当前是 capture-then-return。改成增量流式可行，但是：当前没有任何 event-stream 消费者（CLI、未来 IDE host）在读 `tool_execution_update`，并且抽象本身是 ~200 行的 accumulator + 行缓冲 + 二进制处理。

行动：先做一个**真正需要**实时 shell 输出的 host-side demo，再实现。投机做意味着只是为了和 pi-mono 对齐。

#### `after_provider_response` + 结构化 `BuildSystemPromptOptions` 内省
**结论：** 重新框定。当 case study，不当模板。

metacognitive boundary 设计（`docs/design/metacognitive-boundary.md`）已经决定走"schema + default + host-override"形态，不是"在 loop 各处插 callback"。pi-mono 的 hook surface 是有用的**清单**——告诉你 host 想要暴露哪些字段（system-prompt 选项、post-response 审计、消息替换）——但**机制形态**对 agentao 的协议路线是错的。

将来 boundary 工作恢复时，从 pi-mono 挖**hosts 想要哪些字段**，不挖**callback 该插哪里**。

### Backlog（模式好，但没当前痛点）

#### `prepareArguments` per-tool 归一化钩子（pi-agent-core v0.64.0）
agentao 有 `arg_repair.py`（219 行）+ `name_repair.py`（78 行）作为全局启发式。pi-mono 把这些收敛到 per-tool 声明式归一化，更干净。但是：当前没有 bug 报告或新工具被全局启发式卡住。重构纯属代码质量改进，不解锁新功能。延后。

#### Stale-extension-context 探测（pi-coding-agent v0.69.0）
由 session `/fork` / `/clone` / replace 触发。agentao 还没有这些流程。等 session-lifecycle 工作启动时再用这个 pattern。

#### OSC 9;4 终端进度指示（pi-coding-agent v0.69.0）
~50 行代码，默认关闭，在 iTerm2 / WezTerm / Ghostty 是不错的体验。CLI-only，零架构风险。CLI 下次 polish 时顺手做。

#### 堆叠式 autocomplete providers（pi-coding-agent v0.69.0）
仅在 agentao 加交互式编辑器时相关。否则不动。

### 保留——立即 / 尽快做

#### grep/find `--` 参数注入修复（pi-coding-agent v0.71.0, PR #4018）
**结论：** P0。已确认漏洞。

pi-mono 的修复：`rg <pattern> <path>` 改成 `rg -- <pattern> <path>`，`--pre=/tmp/payload.sh` 这样的 pattern 就被当成文本而不是 flag。agentao 同样存在这个洞：

- `agentao/tools/search.py:308` —— ripgrep 路径 `cmd.extend([pattern, "."])`
- `agentao/tools/search.py:269` —— `_git_grep` 在无 file_pattern 分支裸 `cmd.append(pattern)`

两行 patch + 一个回归测试断言 `--pre=…` 模式返回 "no matches" 而不是被执行。零设计权衡，独立于其它评审项做。

#### 项目上下文文件的紧凑 `read` 渲染（pi-coding-agent v0.73.0）
**结论：** P1。纯 UX，零风险。

`read` 读 `AGENTS.md` / `CLAUDE.md` / `SKILL.md`（及对应物）时在交互输出里默认折叠，附行号范围提示。agentao 的 read 工具当前每次都把项目上下文文件全文 dump，浪费屏幕和渲染后 transcript 的 token。机械改动，无协议影响。

#### plugin-hook 体系增加 `Stop` / `PreCompact` 事件
**结论：** P2。协议补全，不是借鉴。

列在这里是为了对比 `shouldStopAfterTurn` 的替代方案。agentao 的 hook 协议相对 Claude Code 公布的接口是不完整的。在现有 `agentao/plugins/hooks.py` 调度器里补齐这两个事件类型是正确的形态：已经实现 Claude-Code 风格 hook 的 host 即插即用，agentao 拿到 pi-mono 用 `shouldStopAfterTurn` 加上的同等表达力，且不需要发明并行的 callback 路径。

延后到具体 host 工作流提出需求（compaction gate、cost gate、post-turn review）。触发时预计 1-2 天。

## 处置表

| 条目 | 初轮分级 | 最终结论 | 原因 |
|---|---|---|---|
| grep/find `--` 注入修复 | T1 | **立即做** | 已确认漏洞 |
| 紧凑 read 渲染 | T3 | **尽快做** | 零风险 UX |
| `Stop` / `PreCompact` hook 事件 | （原：shouldStopAfterTurn T1） | **触发时做** | 重新框定为协议补全，不是移植 |
| `prepareArguments` per-tool 钩子 | T2 | Backlog | 无当前痛点 |
| Stale-extension-context 探测 | T2 | Backlog | 还没 session fork |
| OSC 9;4 进度 | T3 | Backlog | Polish |
| 堆叠式 autocomplete | T3 | Backlog | 没交互式编辑器 |
| `terminate: true` 工具提示 | T1 | 等待 | 先要有对应工具 |
| Per-tool `executionMode` | T1 | 等待 | per-instance 锁已覆盖 |
| Bash 增量流式 | T2 | 等待 | 没消费者需要 |
| `after_provider_response` / 系统提示选项 | T2 | 重新框定为 case study | 机制形态对 boundary 设计是错的 |
| `shouldStopAfterTurn` | T1 | **砍掉** | 与 plugin hooks 重复 |
| Self-update / 批量更新 | T3 | **砍掉** | npm-only，不适用 |

## 跨项目调研的教训

1. **建议之前先做事实核对。** 第一轮清单挑的是"架构上有意思"的东西。一半在对照 `tool_executor.py`、`plugins/hooks.py` 和 `AsyncToolBase` 之后坍缩。
2. **不同的起点对应不同的正确答案。** pi-mono 加低层 callback 是因为它没有高层 hook 协议。agentao 有协议；正确做法是补齐它，不是在旁边再贴一层 callback。
3. **没有用例的机制是过度设计。** `terminate: true` 是一个干净的机制，但 agentao 没有会用它的工具。现在移植是为了一个还不存在的问题创造特性。
4. **真 bug 胜过聪明 feature。** 590 个提交里单点价值最高的发现是一个两行的安全修复，不是任何拳头特性。
