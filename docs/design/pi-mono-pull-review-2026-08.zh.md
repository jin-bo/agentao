# pi-mono Pull 评审（v0.80.6 → v0.83.0）

**状态：** 决策记录。2026-08-02 起草，基于对 `../pi-mono` 中 434 个 commit / 730 个文件（`34582ef34..aa0ec808b`，即 2026-07-10 那次 pull 之后的增量）的梳理，每个候选项都对照 agentao 当前代码核实过。
**读者：** 决定从 pi-mono 借鉴什么的 Agentao 维护者。
**对应文档：** `pi-mono-pull-review-2026-08.md`。
**先前记录：** `pi-mono-borrow-review.md`（v0.66 → v0.73）、`pi-mono-tools-review.md`、`pi-mono-openai-stream-fix.md`。
**方法：** 先给增量分类 → 按 harness/product 边界筛出候选 → 每条在推荐前先 grep 核实 agentao 现状 → 要么落地，要么写明理由推迟，要么记为不适用并附上判定它的查询。

## TL;DR

三条已落地，两条作为契约决策推迟，一条架构缺口已记录但按需求门控，八条核实为不适用。

| 处置 | 条目 |
|---|---|
| **已落地** | edit 模糊匹配加 NFKC（PR #159） |
| **已落地** | `EventBroadcaster` 记录监听器异常（PR #160） |
| **已落地** | `TurnOutcome.finish_reason_missing`（PR #161） |
| **推迟——契约决策** | `finish_reason_missing` 的子 agent 边界 |
| **推迟——契约决策** | `finish_reason_missing` 的 ACP 通道 |
| **已记录，需求门控** | `watch()`——原子快照 + 无缝订阅 |
| **是决策，不是推荐** | Lanes / 对话树；带崩溃恢复的持久化操作 |
| **不适用（8 条）** | 见文末表格，每条附判定查询 |

这 434 个 commit 的主体是 pi 自己的产品面：TUI alt-screen 重写、session 存储迁到 SQLite 并加 repository 门面、以及三个实现远程 session 线协议的新包（`protocol`、`server`、`client`）。这些都没有越过 agentao 的 harness 边界。

## 已落地

### NFKC 前置于码点表 —— PR #159

**来源：** `packages/agent/src/harness/tools/edit-diff.ts::normalizeForFuzzyMatch`。

`EditTool` 的 tier-3 匹配此前只走一张码点表（破折号、引号、空格，抄自 codex-rs `seek_sequence.rs`）。这张表没有 Unicode**兼容形式**的条目，所以当 `old_text` 与文件只差在全角标点时，会直接落到 `_not_found_hint`。在全角半角混排的中文代码库里这是常态而非特例：`print（"你好"）；` 从 `print("你好");` 匹配不到。

两遍都需要，互不包含——NFKC 折叠全角形式、连字和所有空格变体，但完全不动智能引号和 en/em dash（它们没有兼容分解），而后者正是码点表覆盖的。

顺序是承重的，不是风格问题。扫过整个 Unicode 平面后，恰好有五个字符 NFKC 之后才落进表里、而自身不在表中——`U+207B ⁻`、`U+208B ₋`、`U+FE31 ︱`、`U+FE32 ︲`、`U+FE58 ﹘`，后三个是 CJK 兼容破折号。表在前会让这五个都停在离 ASCII 一步之遥的地方。

agentao 不需要 pi 那套 `applyReplacementsPreservingUnchangedLines`：`line_transform` 只用来构造逐行比较键，而前缀表是从原始 `content_lines` 长度建的，所以 splice 的 span 仍然索引原文。

### 监听器异常记录 —— PR #160

**来源：** pi 的 `handler_error` 事件（`packages/agent/docs/harness-v2.md` §10）。注意它在 pi 那边**只存在于设计稿**——`packages/agent/src` 和 `packages/coding-agent/src` 都是零命中；今天真正跑着的是更窄的、仅面向扩展的 `ExtensionError`。

`EventBroadcaster.notify` 此前用裸 `except Exception: pass` 吞掉每个订阅者异常——没有日志，没有计数。吞掉是对的，保留；对此保持静默不对。WARNING-and-swallow 本来就是这份契约上另一个旁路 sink 的既定约定（`HostReplaySink`，见 `docs/reference/host-api.md`），`broadcast.py` 只是唯一没照做的地方。

三个值得保留的决定：

- **记日志，而不是再发一个事件。** 事件会重入 `notify`，于是一个对所有事件都抛的监听器会无限循环——这正是 pi 的 `handler_error` 需要显式递归守卫的原因。日志调用没有这条边，所以 agentao 不需要那套机制。
- **只记 `event.type`，绝不记 `event.data`。** 凭据脱敏是挂在 agentao 自己 file handler 上的 `Formatter`，刻意不是 `Filter`，正是为了不渗进嵌入宿主的 handler——反过来说，这里若记了 payload，宿主的 handler 收到的就是**未脱敏**的原文。
- **`exc_info=True`。** 一个被吞掉的异常如果没有栈，比静默好不了多少。

pi 那套设计的 hook 一半，agentao 早已有对应物，未作改动：插件 hook 是子进程，超时 / 起不来 / 非零退出各自已在 `agentao/plugins/hooks/_dispatcher.py` 记录。

### `TurnOutcome.finish_reason_missing` —— PR #161

**来源：** `2c3041242 fix(ai): support streams without finish reasons`——**反向采纳**。

pi 的默认行为是：流结束而没有 `finish_reason` 就直接报错，并提供 per-model 的 `supportsFinishReason: false` 逃生阀。agentao 反过来：只上报事实，不做任何分类。理由是 `INCOMPLETE_ANSWER_REASONS` 里的每个值都会变成 CLI 的 error envelope，所以加进那个集合就等于让每个不发该字段的 provider 的每个 turn 都硬失败。这个标记走自己的轴，跟 `max_iterations` 一样，且**不影响** `is_answer`。想要严格语义的宿主自己写 `o.is_answer and not o.finish_reason_missing`。

不必重新讨论的设计点：

- `finish_reason` 的线上值保留 `"stop"` 兜底。改成 `None` 会让所有省略该字段的 provider 的 LLM_CALL_COMPLETED 载荷和 replay 渲染都发生位移。
- 检测判的是 **falsy**，不是 `is None`，因为流式记录侧是按真值门控的。判 `is None` 会让同一个 `""` 因为这次 turn 走的是流式还是 Gemini/回退旁路而给出不同答案——而这是宿主看不见的传输细节。
- 在一个 turn 内跨所有 LLM 调用粘性。中间那次调用如果没有 finish_reason，它的工具调用参数可能被截断而完全无从检测：`_is_length_truncation` 不会触发，参数照样执行。
- compaction 摘要调用完全绕过 chat loop，所以它把自己的观察记在 context manager 上，由两个 compaction 调用点折进来。那是唯一一次输出会**永久改写历史**的调用。
- 被取消的 turn 上抑制（取消本身已解释了缺失）；出错的 turn 上照常上报，因为错误并不解释它。

对这个改动做的 xhigh 评审产出 15 条经验证的发现，合并前修掉 13 条——多数是覆盖缺口：这个新事实没能到达 `agentao.log`、LLM_CALL_COMPLETED、replay 的 `turn_completed` 记录，以及 `agentao run` 的 JSON 信封。

## 推迟——是契约决策，不是缺陷

两条都来自 PR #161 的 xhigh 评审。两条都是真问题；都不是**那次改动**的缺陷，且都需要先定形状。

**1. 子 agent 边界。** 子 agent 的 `finish_reason_missing` 随子进程消失，不进父 turn。`AgentToolWrapper` 刻意不持有父 agent 引用（`agentao/agents/tools/_wrapper.py` 只接收 getter），所以传播它意味着在那个接缝上开一条新通道。注意 `max_iterations` 这个先例**并不精确**：它流进的是子 agent 自己的 `_classify_subagent_outcome`，让子 agent 的结果对父 LLM 显示为未完成——它不写父 turn 的标记。三个候选形状：父 `TurnOutcome`、子 agent 渲染结果里的提示、或 `SubagentLifecycleEvent`。

**2. ACP 通道。** `handle_session_prompt` 只返回 `{"stopReason": …}`，`acp/transport.py::_build_update` 也没有 TURN_END 分支，所以没有任何 `session/update` 携带这个标记。加一条会扩大 ACP 面，而项目对 ACP 的划界刻意保持收窄（fs/terminal proxy 已明确列为 non-goal）。

## 已记录，需求门控：`watch()`

唯一一条真正属于架构层面的缺口。pi 的 `watch()`（`harness-v2.md` §9）在**一步之内**抓取快照并开始缓冲；随后 `start(listener)` 按序冲刷缓冲并切到实时。他们的原话：*"No sequence numbers, no registration race."* 动机明写了是代理场景——服务端必须在任何事件上线之前把快照交给客户端。

agentao 没有快照原语。`agentao/host/events.py` 把这个缺口作为契约属性写着：*"Subscriber starts after events were emitted: no replay; only future events are delivered."* 在 `host/`、`transport/`、`acp/` 里 grep `snapshot` 只命中权限快照、schema 快照和 list 拷贝注释。

宿主**无法**在一个普通 `subscribe()` 之上自建这个能力：先读状态再订阅会丢掉中间的事件，先订阅再读状态会重复计数。它只能是 harness 的原语。

agentao 其实已经撞过这个竞态一次并点状解决了——`agentao/acp/session_load.py` 把 session 注册放在 replay 完成**之后**，正是为了防止流水线过来的 prompt 让实时更新与重放历史交错。一个实例，临时处理，没有通用原语。

**门控：** 只有当宿主挂到**正在运行的** turn 上时才会咬人。今天的 CLI 与 ACP 流程都是先构造后运行，碰不到。

最直观的触发条件是 `agentao serve`——但要注意它**并非**只是"还没开始做"：`path-a-roadmap.md` 把它列在"推迟到 P2 或移入独立项目"那一档，写的是 `✗ agentao serve daemon — clashes with "in-process harness" positioning`。所以这不是一个等着某个排期功能的原语；按现行策略，那个功能不会来。

现实的触发条件更窄，且与 `serve` 无关：某个**嵌入宿主**在 turn 进行中给 `Agentao` 挂上观察者——比如 `/goal` 循环或 `agentao run` 正在跑时，一个 Web UI 重连上来。这完全落在既定定位之内，所以它可以在 `serve` 永不存在的情况下发生。真发生时，应该建这个原语，而不是再来一个像 `session_load.py` 那样的点状修补。

## 是决策，不是推荐

**Lanes / 对话树。** pi 把 harness 重构成一棵 append-only 的 entry 树加上 *lane*——树上的具名位置，各自串行一个操作，彼此并行，以外部身份（如 Slack thread id）为键。它买到的是共享历史前缀和 per-lane 模型配置。agentao 隐含的答案是 N 个 harness 实例：`examples/slack-bot/src/bot.py` 每条消息新建一个 `Agentao`。两者都自洽；pi 的代价是单写者纪律，收益是 token 复用。只有当 agentao 要面向多线程宿主时才值得重新考虑。

**带崩溃恢复的持久化操作。** pi 中被接受的 prompt 就是一个持久操作，且"不存在部分结果"——崩溃后要么"没发生过"，要么"恢复能把它做完"——背后是一份严格排除在对话树之外的操作日志。agentao 的 replay 明确是 core 之外的观测通道，session 是存档快照；turn 中途崩溃就丢了这个 turn。这对 `/goal` 循环和 `agentao run` 有实际意义，但支撑它的是 pi 的整个 Part II（记录目录、provisioned id、恢复归约）——不是能切一小块拿走的东西。

## 不适用——已核实

| pi 的改动 | agentao 现状 | 判定查询 |
|---|---|---|
| `7af8533c6` 可中断的 provider 重试 | 早已实现，先于 pi | SDK `max_retries=0`（`llm/client.py`）+ 自有 `_retry.py::_interruptible_sleep` |
| 6 个 `preserve raw stop reasons` | 从不归一化，原样透传 | `_LENGTH_FINISH_REASONS` 本就是多厂商拼写集合 |
| `f4e9ca746` 把日期移出 system prompt | agentao 更靠前——pi 是**直接删掉**；agentao 移到 user message 的 per-turn `<system-reminder>` | `tests/test_date_in_prompt.py` |
| `cced6a21d` nested worktree 重复加载 AGENTS.md | 构造上不可能 | `prompts/helpers.py` 只读 `working_directory / "AGENTAO.md"`，无祖先遍历 |
| `5d548ae96` rpc bash 绕过权限门 | 没有第二入口 | `grep LocalShellExecutor` → 只有 `tools/shell.py` 与 `tools/base.py` 的 lazy accessor；ACP 无 terminal/exec 方法 |
| `bd2cfabc5` 拒绝循环引用 | CBOR 特有 | Python `json.dumps` 默认就抛 `Circular reference detected` |
| `74caa2649` 校验 package manifest | 已足够严 | `embedding/plugins/manifest.py` 逐字段 `isinstance` 校验 |
| Append-only context 不变量 | 已成立 | `grep 'messages.insert\|messages\[:0\]'` → 无命中；只有整体替换（compaction），正是 pi 自己点名的例外 |

**值得守住的刻意分道：** pi 新增了 `protocol` + `server` + `client`——自研 CBOR 线协议与 Unix socket 传输——现在跑两套远程面。agentao 只有一条（ACP），且是刻意收窄的——见 `acp-server-conformance-review.md` §4，那里的非 IDE / chat-automation 目标客户端裁定，正是「窄」之所以是正确而非残缺的依据。这不是可借鉴项，是一条要守的分界。

**一条观察，不是候选：** `3d8f74357 message-anchored tool loading` 把新增工具锚定到 tool result 的位置而非缓存前缀，从而避免中途加工具冲掉整段缓存。agentao 有同样的形态（`/goal` 的 `add_tool` 注入、skill 激活），但该机制依赖 Anthropic / OpenAI-Responses 的缓存锚点能力，而 agentao 是 chat.completions 形状。搬不过来。

## 过程记录

PR #161 的 xhigh 评审抓出四条我自己写的弱测试，其中包括一个正向 fixture 复用了累加器自身的 `"stop"` 兜底值，导致它的断言根本不可能失败。另外，我有两次反事实检查一开始**通过**了，而它们本该失败——falsy-vs-`None` 那条修复和陈旧摘要标记的重置都处于无保护状态，因为我最初的测试分别只走了 producer 和一个不做 compaction 的 turn，两者都碰不到被改动的那一行。

这条教训超出这个 PR：写出测试不等于做了检查。把修复改回去、确认测试变红，才是检查；而且必须按每一处独立逻辑逐条做——这里就有两个反事实在同时施加时互相掩盖了对方。
