# 7. 上下文与状态

`/context` 显示 token 用量并能调上限。`/status` 是会话状态的一屏快照。两者合在一起就是仪表盘。

`/status` 在 [1. 起步](./1-getting-started#status-我现在是什么状态) 已讲过；本页聚焦 `/context` 和"为什么这次会话越用越贵"这个更大的问题。

## `/context` — token 预算仪表盘

```text
> /context

Context Window Status:
  Estimated tokens: 47,231
  Max tokens:       200,000
  Usage:            23.6%
  Messages:         54
  Compact failures: 0/3
  Last compact:     2026-04-30 14:08:12  82,419 → 31,204 tokens | 21 summarized, 18 kept
  Re-injected files: src/auth.py, tests/test_auth.py
```

| 字段 | 含义 |
|---|---|
| Estimated tokens | 当前对话发出去时大约的 token 数 |
| Max tokens | 配置的上限（默认 200,000） |
| Usage | `Estimated / Max`。颜色：绿（<55%）、黄（<65%）、红（≥65%） |
| Messages | `agent.messages` 里的消息数 |
| Compact failures | 本会话中自动压缩失败次数 / 熔断阈值。打到阈值就关掉自动压缩 |
| Last compact | 上次自动压缩的时间、压缩前后 token 数、被摘要的消息数 vs 保留的消息数 |
| Re-injected files | 压缩后被重新挂回上下文的文件（agent 最近读过的，被判为"还活着"） |

::: tip 为什么颜色阈值都低于 100%
不是说"到 100% 才崩"。自动压缩在远未到模型硬上限时就触发了 — 因为预算的一部分要留给下一轮回答和工具输出。看到红色 65%+ 时压缩快来了。
:::

## `/context limit <n>` — 改预算

```text
> /context limit 100000
Context limit set to 100,000 tokens

> /context limit 500000
Context limit set to 500,000 tokens
```

它的作用：

- 设置 `context_manager.max_tokens`，**仅本会话**
- 影响自动压缩触发点（压缩在接近这个值之前就会启动）
- 重启后复位 — 要持久化，设置环境变量 `AGENTAO_CONTEXT_TOKENS`（见 [10. 配置文件参考](./10-config-reference)）

最低值 1,000，再低 CLI 拒绝。

什么时候调低：
- 想让压缩更早触发（更便宜的轮次，代价是更多的摘要）
- 在用真实窗口比 200K 小的小模型

什么时候调高：
- 用 1M 上下文模型，想少触发压缩
- 长时段、文件密集的 plan 会话，模型确实需要更多状态

## 自动压缩到底做了什么

`/context` 用量逼近上限时，context manager 会：

1. 从对话里挑一段更早的消息块
2. 让 LLM 把它们摘要成一个 `[Conversation Summary]` 块
3. 在 `agent.messages` 里把那段消息替换成摘要
4. 保留最近 N 条消息 + 进行中的工具循环不动
5. 把 agent 最近读过的文件内容重新挂回上下文（即 `Re-injected files`）
6. 把摘要写进 `session_summaries` 表（见 [6. 记忆](./6-memory)）

摘要既存在于活的消息历史里（下一轮看得见），又存在 DB 里（未来会话能通过记忆引用）。

"熔断器"是兜底：如果压缩本身失败（LLM 超时、解析错）连续超过 `CIRCUIT_BREAKER_LIMIT` 次，本会话剩余时间自动压缩关闭 — 拒绝一轮总比螺旋崩溃好。

## `/compact` — 手动立即压缩

`/compact` 走的是和自动压缩**完全一样**的全量压缩路径，只是现在就执行，不等用量条爬上去。

```text
> /compact
Compacted history: 54 → 19 messages, ~47,231 → ~12,880 tokens (6.4% of window).
```

发生了什么：

- 调用 `context_manager.compress_messages(..., is_auto=False)` — 把较老的一块消息摘要成 `[Conversation Summary]`，保留最近的消息 + 正在进行的工具循环，重新挂回最近读过的文件。
- 触发和自动压缩相同的 `CONTEXT_COMPRESSED`、session-summary 可观测事件，并派发匹配的 `PreCompact` 插件 hook（`trigger="manual"`）—— 所以 replay 和 hook 看到手动 `/compact` 和看到阈值触发的路径是一样的。
- 刷新 prompt 里显示的上下文用量百分比。

什么时候用：

- **开一个大任务之前** —— 你知道历史已经臃肿，与其让压缩在某一轮中间发生，不如现在就付掉摘要成本。
- **`/sessions` 恢复之后** —— 在第一轮新对话前先把恢复出来的长历史压一下。
- **账单在涨** —— `/context` 显示 50%+ 但还没到自动压缩的触发点。

边界情况：

- 少于 5 条消息 → `Not enough conversation history to compact yet.`（没什么可摘要的）。
- 压缩推进不了 —— 熔断器开着、找不到安全的切分点、或摘要 LLM 调用失败 —— 会得到 `Compaction made no change …`，历史原样不动（看 `agentao.log`）。
- 和自动压缩一样有损：不在摘要里、也不在重新挂回的文件里的东西，从 agent 视角看就没了。

## `/status` 速查（完整在第 1 章）

```text
> /status
```

| 信号 | 建议动作 |
|---|---|
| 会话摘要里消息数巨大 | 考虑 `/clear` 轮换 |
| Permission Mode 是 `full-access` | 考虑切回 `workspace-write` |
| Loaded sources 列出意料之外的路径 | 审一下 `~/.agentao/permissions.json` 和内建 preset |
| Markdown rendering OFF 但你想 ON | `/markdown` 切换 |
| Task List 显示有待办 | Agent 有未完成 todo — 让它继续 |
| ACP servers `0/N running` | 服务器挂了或没启动 — `/acp status` 排查 |

## 结合两者：诊断流

感觉不对劲就两个一起跑：

```text
> /status      # 看什么被加载了 / 怎么运行
> /context     # 看花了多少
```

| 现象 | 先看哪个 |
|---|---|
| 每轮都慢 | `/context` — 用量 % 和上次压缩时间 |
| 账单飙升 | `/context` 看 token，`/status` 看激活的 skills（每个 skill 都加大 prompt） |
| Agent 忘了显然该记得的事 | `/memory status`（第 6 章）— recall errors > 0？ |
| 工具一直失败 | `/status` 看权限模式，`/mcp` / `/acp`（第 8 章） |

## 容易踩的坑

- **`Estimated tokens` 是估计值** — Manager 用按字符的启发式算法，不是模型的 tokenizer。真实 OpenAI/Anthropic 计数可能差 5–15%。用来看趋势，别当精确数字。
- **压缩是有损的** — 没进摘要也没被重新挂回的内容，从 agent 视角就没了。Agent 突然"忘"了某事，看一眼 `Last compact` — 可能在那一刻被摘要掉了。
- **会话中调低 `limit` 会立即触发压缩** — 把上限设到当前用量以下，下一轮会激进压缩。有时是想要的，有时是惊喜。
- **Re-injected files 反映的是"最近"，不是"重要"** — 一个关键文件如果没被碰一段时间，可能在压缩里没保留下来。要保住就让 agent 再读一次。

## 接下来读什么

| 想做的事 | 读 |
|---|---|
| 排查记忆把上下文撑大了 | [6. 记忆](./6-memory) → `/memory status` |
| 在配置里调压缩阈值 | [10. 配置文件参考](./10-config-reference) |
| 理解嵌入式压缩 API | [Part 4 · 事件层](/zh/part-4/) |

---

::: info 这一章在体系里的位置
Context manager 是 `agent.context_manager`。嵌入式宿主可以读 `cm.get_usage_stats(agent.messages)` 来驱动宿主侧的"上下文条" UI，或者直接调 `cm.compact()` 强制压缩。两条路径上自动压缩触发逻辑一致。
:::

::: tip 真相源头
命令语法：`/help`。`/context` 实现：[`agentao/cli/commands/context.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands/context.py)。`/compact` 实现：[`agentao/cli/commands/compact.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands/compact.py)。压缩逻辑：[`agentao/context_manager.py`](https://github.com/jin-bo/agentao/blob/main/agentao/context_manager.py)。
:::
