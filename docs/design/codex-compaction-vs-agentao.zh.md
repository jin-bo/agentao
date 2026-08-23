# Codex `/compact` 与 Agentao `/compact` 对照

> **⚠️ 对 codex 的部分仅为分析记录，暂不实施。** 第 1 节的分级是**分析结论的优先级排序**，不是工单。
> **例外：唯一一项 P1（§3）是 agentao 自身的缺陷，已于 2026-08-23 实施收口**（见 §3 节末「收口记录」）。
> 其余各项（远端压缩、token-budget 空窗模式、压缩遥测矩阵等）仍**未获授权动工**。
> 引用本文时请一并引用这一行——它防止下一个人把排序当排期。

**状态：** 对 codex 的对照部分仅分析（未授权实施）；**§3 的 P1 已实施**（2026-08-23，rev 9）。2026-08-22 起草；**rev 2 经评审一轮 8 项修正（初稿两项 P1 中撤回一项，另撤回一项「待定契约」结论）；rev 3 落地 §8 勘误横幅并自查出第 9 项；rev 4 再经一轮评审 5 项；rev 5 收 4 项；rev 6 收 1 项；rev 7 收 2 项；rev 8 收 1 项（共 **22** 条修正，六轮评审）**。修正明细见 §13——**再次提出本文任何结论前先读 §13**。
**锚点：** codex `openai/codex@2151d3a5b7`（2026-08-21）；agentao `main@dc11298`（2026-08-21）。英文孪生待写。

**范围（rev 2 新增，rev 4 更正，读本文前必读）：** codex 的压缩入口会**四路分发**（§2.1）。本文 §4 / §5 / §9.3 描述的是其中的 **local 路径**（`core/src/compact.rs`）——**在默认配置下，凡 provider 声明 `RemoteCompactionSupport::V2` 的都命中 remote v2，不是 local**。

**选 local 作为对照面的理由不是「厂商」，而是「机制」（rev 4 更正）：** local 是「客户端自行摘要、不做远端能力协商」的那一种，与 agentao 的做法同构。**不要**把这条边界读成 OpenAI / 非 OpenAI——它是 **provider capability**：Amazon Bedrock 并非 OpenAI，却同样报告 `V2`（`model-provider/src/amazon_bedrock/mod.rs:174-187`）；反过来 agentao 用户完全可以就用官方 OpenAI endpoint（`docs/start/quickstart.md` 要求 `OPENAI_API_KEY` / `OPENAI_BASE_URL`）。

**更根本的一条（rev 4 新增）：agentao 根本不说 Responses 协议。** 它走的是 Chat Completions（`agentao/llm/client.py:455,717` `client.chat.completions.create`），而 remote compaction 是 Responses API 上的操作——所以远端压缩对 agentao 不是「provider 支持与否」的问题，是**协议面不同**，见 §10。

但**「codex 怎么做」这句话在本文里一律指 local 路径**，不可泛化为 codex 的默认行为。

**方法：** 两侧都读源码，每条主张就地附 `file:line`。**唯一的 P1（§3）是 agentao 内部的自相矛盾，与 codex 无关**——即使一条也不借鉴 codex，它仍然成立；它也确实是本文唯一被实施的一条。
**相关：** `codex-subagent-v2-vs-agentao.zh.md`（同一 codex 锚点的另一侧对照）、`codex-goal-mechanism-review.zh.md`、`docs/history/implementation/stop-precompact-hooks-plan.zh.md`（§8 的权威出处）。

---

## 1. 结论表（分析优先级，非排期）

**一句话差异（限 local 路径）：codex 的 local 压缩是一次经过完整采样栈的模型请求，摘要模型看到的是原生结构的 history；agentao 是一次离线摘要调用，摘要模型看到的是被压平并按字符数截断的文本。**

| 若实施，优先级 | 内容 | 依据 |
|---|---|---|
| **~~P1~~ 已收口 2026-08-23** | 摘要**输入端**的 200/500 字符截断，与摘要 prompt 自己要求的 "verbatim / be thorough" 直接冲突。**已实施**：按内容分级 + 从新到旧的总 token 预算；实测常规窗口存活率 15% → 46–76% | §3 |
| **观察** | `/compact` 只在交互式 CLI 可达，`agentao run` / ACP 拿不到 | §7 |
| **观察** | 没有任何路径会从模型推断上下文窗口；`/model` 后 `max_tokens` 保持不变 | §6 |
| **观察** | microcompaction 回写已发送前缀，代价是 provider prompt cache | §9.2 |
| **不建** | 远端压缩（v1/v2）、token-budget 空窗模式、压缩遥测矩阵、「只留 user 消息」的保留策略 | §10 |
| **~~已撤回~~** | ~~「`/model` 与 ACP 同一动作两种结果」~~ —— 不成立，ACP 的 model-only 请求与 CLI 完全相同 | §6、§13-1 |
| **~~已撤回~~** | ~~「PreCompact 不可否决是刻意对齐 Claude Code」~~ —— 前提错误，Claude Code **支持** PreCompact blocking；本仓库早有权威处置 | §8、§13-3 |

§11 列出**已核对过的对等项**，§13 是修正表，两者都不要重复上报。

---

## 2. 架构定位：session task vs slash handler

| | agentao | codex |
|---|---|---|
| 入口 | `cli/input_loop.py:230` → `cli/commands/compact.py:82` `handle_compact_command` | `Op::Compact` → `core/src/tasks/compact.rs:19` `CompactTask`（`TaskKind::Compact`） |
| 身份 | 无。同步阻塞在 REPL 线程 | 有 `turn_id`、`TurnStarted` 事件、`ContextCompaction` turn item，**可中断** |
| 可达面 | 交互式 CLI only | TUI（`tui/src/chatwidget/slash_dispatch.rs:264`）+ app-server（`app-server/src/request_processors/thread_processor.rs:2285`）。**MCP server 不在其列**——它的工具表只有 `codex` / `codex-reply`（`mcp-server/src/message_processor.rs:336-345`），能享受核心的自动压缩，但无法手动触发 |
| 持久化 | `agent.messages` 就地替换；summary 落 SQLite `session_summaries` | `RolloutItem::Compacted{replacement_history, window_number, window_ids}`（`core/src/session/mod.rs:3382`）—— rollout **同时保留压缩前后**，故 resume / fork 可跨压缩边界 |

codex 的压缩窗口是**编号**的（`advance_auto_compact_window`，`compact.rs:360`），并在替换后向 hook 队列推 `SessionStartSource::Compact`。agentao 无「窗口」概念。

### 2.1 codex 的压缩实现矩阵（rev 2 新增）

`tasks/compact.rs:36-70` 与 `session/turn.rs:1178` `run_auto_compact` 用**同一套优先级**四路分发：

| 顺位 | 条件 | 实现 | 是否本文对照面 |
|---|---|---|---|
| 1 | `Feature::TokenBudget` 开启 | `compact_token_budget.rs:26` —— **完全不摘要**，直接开新 context window 并重注入 world state | 否，见 §10 |
| 2 | provider `RemoteCompactionSupport::V2` **且** `Feature::RemoteCompactionV2` 开启 | `compact_remote_v2.rs:71` | 否，见 §10 |
| 3 | provider `RemoteCompactionSupport::V2`（v2 feature 关闭） | `compact_remote.rs:53`（legacy） | 否 |
| 4 | `RemoteCompactionSupport::Unsupported` | `compact.rs`（local） | **是** |

**默认落在哪一条：** `Feature::RemoteCompactionV2` 是 `Stage::Stable, default_enabled: true`（`features/src/lib.rs:1526-1531`）。**哪些 provider 报 `V2` 是按能力而非厂商决定的**，且**不止一处实现**：
- 通用 `ModelProvider` impl —— `is_openai()` 或 Azure Responses provider 报 `V2`，其余走 else 分支报 `Unsupported`（`model-provider/src/provider.rs:343-350`，测试 `:645-671`）；
- **Amazon Bedrock 有自己的 `capabilities()` impl，无条件报 `V2`**（`amazon_bedrock/mod.rs:174-187`）——它不是 OpenAI。

**所以「只有非 OpenAI provider 才走 local」是错的（rev 4 更正）。** 准确说法：**凡不声明 `V2` 能力的 provider 才落到 local**；这是能力协商的结果，与厂商无关。

因此下列说法**只对 local 成立，不可泛化为 codex**：「压缩是一次模型 turn」「只保留 user 消息」「overflow 时逐条删最老项重试」「每次压缩后发 WarningEvent」。

---

## 3. 【P1，已收口 2026-08-23】摘要输入端与摘要 prompt 自相矛盾

**这是 agentao 内部的矛盾，不是「codex 有我们没有」。评审一轮未对本节提出异议。**

> **已实施。** 本节保留原始论证；**结论与实测数据见节末「收口记录」**。下方 (a)–(d) 四条候选路径是当时的待选项，**不再是开放问题**。

`context_manager.py:532` 的 `_SUMMARIZE_SYSTEM_PROMPT` 是一个 9 段式结构化模板，其中：

- `## 3. Files and Code Sections` —— "Be thorough — **this section is critical** for seamless continuation."（`:549`）
- `## 4. Errors and Fixes` —— "Quote error messages **verbatim**."（`:551`）
- 结尾：「Sections 3, 4, and 8 are the most important — prioritize completeness there.」（`:563`）

而喂给它的输入，在 `_format_for_summary`（`:620`）里**先被截断**：

```python
_HIGH_FIDELITY_TOOLS = {"write_file", "replace", "edit_file"}   # :528
_TOOL_RESULT_TRUNCATION = 200                                    # :529
_HIGH_FIDELITY_TRUNCATION = 1_000                                # :530
...
lines.append(f"[Tool Result - {tool_name}]: {str(content)[:limit]}")   # :649
lines.append(f"[{role.upper()}]: {str(content)[:500]}")               # :651
```

- 普通消息（user / assistant）→ **500 字符**
- tool result → **200 字符**；只有 `write_file` / `replace` / `edit_file` 三个 → 1000 字符

**后果：** 一条被砍到 200 字符的 `run_shell_command` 失败输出里，根本没有 verbatim 的错误信息可引；一条 500 字符的 assistant 消息里没有完整代码片段可抄。**prompt 要的东西，输入管线已经先删掉了。** §3/§4/§8 恰好是模板自称「最重要」的三节，也恰好是最依赖原文长度的三节。

**注意这是双重截断：** 走到 `_format_for_summary` 的消息**此前已经过一次 microcompact**（§5），因此一条老 tool result 可能先被截到 3000 字符，再被截到 200 字符。

**这不是照抄 codex 就能解决的**——codex local 路径要重发整份 history，代价完全不同（且**对声明 `V2` 能力的 provider 而言**默认不走 local，§2.1——不声明 `V2` 的 provider 仍直接进 local，`tasks/compact.rs:41-43` 的 V2 分支同时受 capability 与 feature 两道守卫）。可选方向（**不收敛为一条**，需维护者定）：

- (a) **放宽预算**：把 200/500 提到能容纳典型错误栈的量级（代价：摘要调用本身的 token 成本上升，且它走的是主模型）。
- (b) **改 prompt**：删掉 "verbatim" / "be thorough" 这类输入端保证不了的要求，让模板与输入能力一致（零成本，但降低摘要质量上限）。
- (c) **改成 token 预算而非字符上限**：给 `_format_for_summary` 一个总 token 预算，从新到旧分配，最老的部分截断。
- (d) **按内容分级**：tool 失败输出（含 traceback / 非零退出）给高预算，成功输出给低预算——把现有的 `_HIGH_FIDELITY_TOOLS` 从「按工具名」扩成「按内容」。

**建议先做的是测量，不是改码：** 取若干真实 `agentao.log` 里的压缩现场，统计被 200/500 截断掉的比例与截断处的内容类型，再决定走哪条。

### 收口记录（2026-08-23）

**先测量，后改码——测量结果直接淘汰了一半选项。** 数据源：本机 `.agentao/sessions/*.json` 共 10 份真实会话，167 条 tool result、67 条 user/assistant 消息、239KB tool 输出。

| 实测 | 值 |
|---|---|
| tool result 超预算被截 | **125 / 167（75%）** |
| tool result 内容存活率 | **12%** |
| user/assistant 存活率 | 49% |
| `read_file` 占 tool 输出总量 | 173KB / 239KB |
| `write_file`/`replace`/`edit_file` 结果长度中位数 | **114 字符** |

**决定性的一条是最后一行：`_HIGH_FIDELITY_TOOLS` 的 1000 字符预算结构上永远够不着。** `write_file` 返回 `f"Successfully {action} {file_path}"`、`replace` 返回 `f"Replaced {n} occurrence(s) in {file_path}"`（`tools/file_ops.py:260,394`）——受路径长度约束的确认串，不是文件内容。它想保住的东西在 **tool call 的参数里**，而那正是 F1 刚开始渲染的东西。所以 (d) 里说的「把按工具名扩成按内容」不是扩，是**替换**：这一档整个删掉了。

**落地 = (c) + (d)，两条一起，因为它们各自都不完整：**

- **(d) 按内容分级** —— 失败输出（traceback / 非零退出 / `error` / `denied`）3000 字符，普通 tool result 1000（原 200），user/assistant 2000（原 500）。失败很便宜：167 条里 23 条、占 3% 字节。
- **(c) 总预算，从新到旧分配** —— `max_tokens × 10%`，用 `_heuristic_token_count` 按**估算 token** 而不是字符计——CJK 是 1.3 token/字符 vs ASCII 0.25，用字符预算会把中文低估五倍以上，而中文历史恰恰最容易长。
- **单独做 (d) 会制造新缺陷**：抬高单条上限而不封顶，摘要调用自身可能溢出，而摘要失败会 `_consecutive_compact_failures += 1` 触发熔断——把保真度改进变成压缩中断。这正是 code review #8 提的那条，**与本节是同一个决策的两面**，一起收。
- **(a) 单纯放宽预算** 就是「只做 (d)」。**(b) 改 prompt** 是撤退，数据不支持——真正被浪费的预算是那档够不着的 1000，不是模板要求过高。

**一个非显然的实现细节：失败输出要留尾巴。** 命令的诊断信息在**末尾**（traceback、非零退出、assertion），只从头截 3000 字会满足了大预算却仍然丢掉 `## 4. Errors and Fixes` 要求逐字引用的那一句。所以失败档走 head+tail（复用 `MICROCOMPACT_HEAD_RATIO`），且标记扫描扫**全串**——只扫前 N 字符会把「先正常跑一阵再失败」的命令全部误判为普通档，而那是绝大多数。

**实测效果**（同一批会话，按真实 `to_summarize` 窗口，即扣掉尾部 20 条 verbatim）：

| 窗口规模 | 存活率 |
|---|---|
| 常规窗口（6–14 条） | **46% / 51% / 74% / 76%**（原 15%） |
| 一个 165 条的病态窗口 | 16%，且**显式封顶在 19,275 token 并标注省略条数**——原实现在这里**完全无上限** |

**新增 12 条测试。其中一条抓出了我自己的 bug**：第一版契约测试用等长消息，因此没能发现「预算装不下的块被跳过、继续给更老的块花预算」会在 transcript 中间**打洞**。改成随机尺寸的性质测试（200 次试验，断言存活集合恒为连续后缀）后立刻暴露。实现改为遇到第一个装不下的块即停止。

**代码评审（同日，第二轮）又收了 5 条**，其中前两条是**首版实现自己制造的新缺陷**，不是原有问题：

1. **双重截断会吃掉 microcompact 的省略提示。** 首版把 `_ERROR_RESULT_TRUNCATION` 定为 3000，与 `MICROCOMPACT_TOOL_LIMIT` **完全相等**、且两者共用 `MICROCOMPACT_HEAD_RATIO`——于是第二刀的头尾切点与第一刀**逐字符对齐**，正好落在第一刀写下的 `[… 200,000 chars omitted by microcompact …]` 上，把它删掉并改写成「45 chars omitted」。摘要模型被告知这条结果只少了 45 个字符，而实际少了二十万，**而摘要是要永久顶替 history 的**。改为 4000（严格高于 microcompact 上限 + 提示行长度），已被 microcompact 处理过的结果因此原样通过。
2. **老的 `[Conversation Summary]` 会被总预算整条淘汰。** `compress_messages` 返回的是 `[boundary, summary, …, recent]`，所以重新压缩时**上一轮的摘要恒为最老的块**，而预算恰恰从新到旧花——它第一个被丢。丢的是 prompt 第 1 节（「用户提出的每一个目标」）与第 6 节（「所有非平凡用户消息」）唯一的载体：第二次压缩就会把在此之前的全部历史一次性截肢。实测（200K 窗口 / 400 条工具密集历史）确认它被丢掉。修复：摘要块单独计费、永不淘汰，上限为预算的一半（`_clip_carry_summary`），省略标记改放在**接缝处**而非行首（否则读起来像是摘要本身被丢了）。
3. **失败标记的匹配面过宽，而过宽在共享预算下不是免费的。** 首版用 `traceback|exception|\berror\b|…` 裸词匹配，实测命中本仓 **169/272** 个源码文件——也就是三分之二的普通 `read_file` 结果（而 `read_file` 正是 239KB 工具输出里的 173KB）被误判为失败、拿走 4 倍预算，再由「从新到旧」把更老的消息整条挤出去。原注释「误判一次只多花几百字符」因此是错的。改为按**诊断形状**匹配（traceback 头、列 0 的异常行、非零退出、runner 的 FAILED/ERROR 列……），误判降到 **9/272**，同时 traceback / pytest / `command not found` / 非零退出 / `Permission denied` / git `fatal:` / npm `ERR!` / ruff / mypy / `Connection refused` / go `exit status` 全部仍然命中。
4. **普通档的截断不打标记。** 只有失败档标了省略；普通档直接砍到 1000 字符就交出去，摘要模型无从分辨「完整」与「被砍」——这正是同文件 `_clip_args` 已经写明的失败模式（会把半截路径/命令当成事实引用）。现在两档都标。
5. **两处测试是空转的。** `test_a_single_oversized_message_still_produces_a_transcript` 名义上钉住「装不下也要保住最新一条」，但 ASCII 消息被 `_MESSAGE_TRUNCATION` 卡在 2000 字符 ≈ 500 token，永远够不到 2000 token 的预算下限，那条分支根本没被走到（改用 CJK 才真正触发）；`test_write_file_result_no_longer_gets_a_privileged_budget` 断的是 `not hasattr(...)`，任何拼写错误都能让它通过。两条都改成行为断言。另外预算的 token 计量从直接调 `_heuristic_token_count` 改为走 `count_tokens_in_text`，使它与所占比例的 `max_tokens` **同一单位**（有 tiktoken 用 tiktoken，没有则回落到同一个 CJK 启发式）。

**未做：** 没有动 `MICROCOMPACT_TOOL_LIMIT = 3000`，所以双重截断（§本节上文）仍在——只是第二道从 200 抬到了 1000/4000，且两刀的切点不再重合。

---

## 4. 摘要怎么生成（codex local 路径 vs agentao）

**codex local —— 经完整采样栈。** `compact.rs:240` `run_compact_task_inner_impl`：

1. `sess.clone_history()` 取一份 history **副本**（`:252`——**不是**在活 history 上追加），把压缩 prompt（`prompts/templates/compact/prompt.md`，9 行，可被 `config.compact_prompt` 覆盖）`record_items` 进这份副本；
2. `sess.services.model_client.new_session()` **新建**一个 `ModelClientSession`（`:260`），在本次压缩 turn 的多次重试之间复用它（注释说明目的是让 sticky routing / websocket 增量请求跟踪跨重试存活）——**不是**复用调用方的 session；
3. 跑请求：streaming、backoff 重试、rate-limit 更新、token usage 记账全都在；
4. 取本轮最后一条 assistant message 作为 summary，前缀 `SUMMARY_PREFIX` 后写回。

**「摘要模型看到什么」的准确表述（rev 2 修正）：** 是**保留原生结构**（消息/工具调用/推理项各自成项，未被压平成一段文本），**不是「完整无损」**。已记录的 tool 输出在 record 时就已按 `TruncationPolicy` 截断过（§9.2）；`for_prompt` 还会按模态规范化；context overflow 时会 `history.remove_first_item()` 删最老项重试（`:315`）。

**agentao —— 带外（out-of-band）。** `context_manager.py:566` `_summarize_messages`：

1. `_format_for_summary` 把 history **压平成纯文本**（并按内容分级的字符上限 + 从新到旧的总 token 预算裁剪，见 §3「收口记录」；2026-08-23 之前是固定的 200/500 字符截断）；
2. 构造一个全新的 2 条消息会话 `[system=9段式模板, user="Summarize this conversation:\n\n"+flattened]`；
3. `self.llm_client.chat(messages=recall_messages, tools=None)` —— **绕过 `ChatLoopRunner`**；
4. `_format_summary` 剥掉 `<analysis>` 思考块、解包 `<summary>` 标签。

第 3 步的绕过有已知代价，代码里自陈（`context_manager.py:75-85`）：这次调用不经过 runner 的 `finish_reason` 探测器，而它偏偏是**唯一一次输出会永久改写 history 的调用**——一个被 length 截断的摘要会被之后每一轮继承。所以 `last_summary_finish_reason_missing` 必须由压缩调用点手工折回（`_compaction.py:77-78`、`_runner.py:1170-1171`）。这是带外方案的固有税，不是疏漏。

**核心差异因此应表述为：codex local 给摘要模型的是原生结构，agentao 给的是扁平化文本（原 200/500 固定截断，2026-08-23 起为分级上限 + 总预算，见 §3）。** 代价对称：codex 要重发整份 history（前缀命中 prompt cache），agentao 极便宜但输入有损、且要自己补齐 runner 的观测。

---

## 5. 压缩后留下什么

**agentao**（`context_manager.py:326` `compress_messages`）：

```
[Compact Boundary | 元数据] + [Conversation Summary] + 文件提示? + [PIN] 消息 + 最近 ≤20 条
```

- **Step 1 先对整个消息列表跑 microcompact**（`:368`），**然后**才划分 to_keep（`:372`）。因此「最近 ≤20 条」**不是原文**：其中除最近 5 条 tool result 外，任何超过 3000 字符的更早 tool result 都已被头尾截断（`:282` `microcompact_messages`）。
- `KEEP_RECENT_MESSAGES = 20`，且不超过总数的 **60%**（`:372-375`）
- split point 向后推进到下一个 `role == "user"` 边界（`:379-380`）——保证不产生孤儿 tool result；找不到安全切点就原样返回（`:382-383`）
- 额外携带：`[PIN]` 前缀消息、`_extract_recently_read_files` 生成的「压缩前读过的文件」提示列表

**codex local**（`compact.rs:652` `build_compacted_history_with_limit`）：

```
[initial_context?] + 最近的 user 消息（≤20k tokens，从新到旧，最老那条部分截断） + summary
```

- `COMPACT_USER_MESSAGE_MAX_TOKENS = 20_000`（`compact.rs:57`）
- `collect_annotated_user_messages` 只收 user 消息且排除历史 summary（`compact.rs:535/547`）——**所有 assistant / tool item 全部丢弃**
- mid-turn 变体还要把 initial context 精确插到最后一条**真实** user message 之前（`compact.rs:581`），因为模型被训练成「压缩后 summary 必须是 history 最后一项」（`compact.rs:60-68` 注释）

**两种赌注不同：** agentao 保留最近交互的**结构**（assistant + tool 消息仍在，但内容可能已被 microcompact 截断）；codex local 只留用户意图。**「agentao 的赌注在工具密集场景更稳」是一个待验证假设，不是本文的结论**——它依赖「被 microcompact 截断后的 tool result 仍有足够信息量」，而这一点没有测量过。

---

## 6. 【观察】没有任何路径会从模型推断上下文窗口

**rev 2 修正：初稿把这条列为 P1「同一动作两条路径两种结果」，那个说法不成立，已撤回。**

`context_manager.max_tokens` 是压缩阈值的分母（§9.1）。全仓**只有三处**写它：

| 位置 | 何时生效 |
|---|---|
| `context_manager.py:87` | 构造时，来自 `Agentao(max_context_tokens=…)`，CLI 侧取自 `AGENTAO_CONTEXT_TOKENS`（默认 200000，`cli/app.py:278`） |
| `cli/commands/context.py:62` | 用户手动 `/context limit <n>` |
| `acp/session_set_model.py:69` | ACP `session/set_model` **且请求显式携带 `contextLength`** |

**为什么「不对称」不成立：** ACP 的 `session/set_model` 逐个 knob 独立应用（`session_set_model.py:61-71`，注释写明「a request carrying only `model` must not reset the caller's existing contextLength」）。**一个只带 `model` 的 ACP 请求调用的正是 `agent.set_model(model)`——与 CLI `/model` 是同一个函数，行为完全一致，都保留原窗口。** ACP 提供的不是「自动跟随」，而是「在一次调用里原子地显式设置两个独立参数」。两条路径没有分歧。

**剩下的真实事实（弱得多）：** agentao **没有任何**从模型推断窗口的机制，窗口一律由 host 显式给定或用户手动设置。这与 codex 不同——codex 的窗口来自 `turn_context.model_context_window()`（`model_info` 目录），并据此额外提供两个自动触发：`CompactionReason::ModelDownshift` 与 `CompactionReason::CompHashChanged`（`session/turn.rs:1080`）。

**但这更像是设计立场而非缺陷：** codex 能这么做的前提是它持有一份权威的模型→窗口目录；agentao 面向任意 OpenAI 兼容端点，**没有也不该有**这份目录。切到更小窗口的模型后阈值偏大，会先吃一次 API context 超限错误，再由 `_runner.py:1155` 的反应式阶梯兜底（压缩 → 重试 → `messages[-2:]` → 重试）。

若要改善，可选（**不收敛为一条**，且都不引入模型目录）：

- (a) **文档化 + 提示**：明确「窗口是 host 的职责」，并在 `/model` 切换后打印当前 `max_tokens`。
- (b) **给 `/model` 加可选窗口参数**：`/model <name> [--context <n>]`，与 ACP 的 `contextLength` 语义对齐。
- (c) **只补反应式那一侧**：把 `messages[-2:]` 这一级换成逐条从头剥离（codex `history.remove_first_item()` 的思路，`compact.rs:315`），让最坏情况不那么粗暴。与 (a)/(b) 正交，可独立做。

---

## 7. 【观察】`/compact` 的可达面

`/compact` 只出现在一处分发表：`cli/input_loop.py:230  "compact": handle_compact_command`。`agentao/acp/` 下 grep `compact` 只命中两处无关的 "compact JSON"（`acp/server.py:8,530`）。因此：

- **`agentao run`**：拿不到手动压缩；自动压缩照常（它在 `ChatLoopRunner` 里，与前端无关）。
- **ACP**：同上。DeepChat 这类 chat 目标客户端无法给用户一个「立刻压缩」的按钮。

codex 因为把它做成 `Op`，TUI 与 app-server 两个前端零成本共享——但**MCP server 并不在其中**（§2 表脚注）。

**是否要补取决于需求，不取决于差距。** 目前没有 issue 要求它。若要补，最小形态是把 `handle_compact_command` 的主体从 `cli/` 下沉到一个可被 ACP 复用的位置——注意 `cli/commands/compact.py:44` 的 `_dispatch_pre_compact` 已经是 `_hook_dispatch.py:165` 那份的**手工复制**，下沉时应顺手消掉这份重复，而不是变成三份。

---

## 8. `PreCompact` 不可否决 —— 本仓库早有权威处置，本文无新结论

**rev 2 全节重写。初稿的前提「Claude Code 的 PreCompact 本身就是非阻塞的」是错的，结论「跟 Claude Code → 现状正确」随之作废。rev 4 补齐外部依据并**开始**把行号引用换成章节/行名，**rev 5 才换完**（rev 4 曾称「全部换完」，不实——见 §13 #18）。**

**外部事实（rev 4 —— 官方文档，非内部草案）：** Claude Code 的 PreCompact **支持阻塞**。官方 hooks 文档 <https://code.claude.com/docs/en/hooks> 的 *Exit code 2 behavior per event* 表中该行为 `PreCompact | Yes | Blocks compaction`；同页 *Matcher patterns* 表并列列出 `PreCompact, PostCompact`（matcher 取值 `manual` / `auto`），即 **Claude Code 另有 `PostCompact` 事件**。**核对日期 2026-08-22**（页面未显示版本号或 last-updated；此处只声明取证时点，不声明版本）。

> **为什么补这条：** rev 2 把内部的、且自我标注为「草案 / 已废止」的实施计划当作**当前外部契约**的权威来源。那份计划只能证明「agentao 当年据此做过范围决策」，不能独立证明 Claude Code 的真实或当前行为——而这条事实恰好是推翻初稿结论的支点。取证方式不合格，即使结论碰巧成立。

**agentao 一侧（内部记录，用于证明「已决策」而非「外部为真」）** —— `docs/history/implementation/stop-precompact-hooks-plan.zh.md`：

- 该文「Claude Code 兼容性矩阵」中 **`Exit code 2 —— PreCompact`** 与 **`JSON decision: "block"（PreCompact）`** 两行，agentao 均标 **❌ 不兑现**，不是 ✅ 也不是刻意 parity。
- 这是**已定案的范围排除，且明确拒绝使用「deferred」措辞**——见该文顶部 **Phase B 摘要条**与文末**修订备忘「评审四轮」第 7 项**。

> **引用方式（rev 4）：** 上面刻意改用**章节名 / 矩阵行名**而非行号——该文件在 2026-08-22 被插入勘误横幅后正文整体下移 21 行，rev 3 里写死的行号已全部失效。行名不会随横幅漂移。

**排除理由（该文**「Claude Code 兼容性矩阵」后的**范围说明段**，以及 **`B5. PreCompact gate` 一节**）：** PreCompact 的 emit 位置就在就地修改 `agent.messages` 之前，而周边的 overflow-recovery 代码假设压缩最终会成功。接受 host「拒绝」却没有「host 拒绝且仍然超长」的兜底，会产生不可恢复的失控行为。因此它被钉成 **gap 而非 roadmap**；真要做，须先解决那条恢复路径，并另开 `PRECOMPACT_GATE_PLAN.md`。

**本文对此不新增任何结论。** 但核实过程中发现该计划文档存在**落地后漂移**，已于 2026-08-22 以勘误横幅就地标注（该文件是 `docs/history/` 冻结归档，正文与修订备忘一概未改写）：`/compact` 已在本计划之后落地并确实 dispatch PreCompact（`compact.py:44-79`，`reason="manual_cli"`），因此矩阵里以「Agentao 没有 manual `/compact` CLI」为前提的多处措辞已过期。

**注意一处**初稿写反了的事实：`trigger: "manual"` **并非「现在会发出了」**。`build_pre_compact` 把 `"trigger": "auto"` **写死**（`plugins/hooks/_payload.py:160`，无参数可覆盖），所以手动 `/compact` 的 hook payload **自报为 `auto`**。这把原本良性的「取值面更窄」变成了**取值错误**：**agentao 翻译后的 matcher 对象** `{"trigger": "manual"}` 永不命中，而 `{"trigger": "auto"}` 会**错误命中手动压缩**；

> **措辞订正（rev 5）：`{"trigger": "..."}` 不是「Claude matcher」。** Claude Code 的配置形态是**顶层字符串** `{"matcher": "manual|auto"}`；agentao 的解析器要求**对象**形态，字符串 matcher 在解析期即作为 `PluginWarning` 被丢弃、规则**根本不会加载**（见该历史计划矩阵的 **`Matcher（PreCompact）—— 配置文件形态`** 行）。`{"trigger": "..."}` 是 **host 自行预翻译后**的 agentao 对象形态（同矩阵 **`Matcher（PreCompact）—— 运行时 regex 求值`** 行）。把它叫「Claude matcher」会误导配置作者——一份原样移植的 Claude `hooks.json` 在 agentao 里连加载都做不到。

同一次压缩的 replay 事件却发 `"trigger": "manual"`（`compact.py:75`），两者自相矛盾。这是**代码缺陷，不是文档问题**，且 `tests/test_hooks_pre_compact_payload_claude_shape.py:60-74` 的站点清单只列四个位置、不含 `manual_cli`，**现有测试抓不到它**。修它会改变 hook 契约（matcher 命中集合随之变化），**未获授权，未动**。

`PostCompact` 是否新增，是与 PreCompact blocking **相互独立**的决策（它不触及「拒绝后仍超长」那条恢复路径），本文不给建议。

---

## 9. 阈值、触发与失败处理

### 9.1 阈值

**rev 2 修正：初稿把 codex 写成「API 真实用量」、agentao 写成「本地估算」，这个对比是人为拉大的——两侧都是「服务端 anchor + 本地增量估算」。**

| | agentao | codex |
|---|---|---|
| 计数依据 | 服务端 anchor + 本地增量：复用上次 API 的真实 `prompt_tokens` 作为已发前缀，只本地估算其后新增的消息（`context_manager.py:145` `_threshold_token_estimate`） | 服务端 anchor + 本地增量：`last_token_usage.total_tokens` 加上**最后一个 model-generated 项之后**各项的本地估算；`server_reasoning_included` 为假时还额外本地估算历史 reasoning 项（`context_manager/history.rs:415-431`） |
| 阈值 | `COMPRESSION_THRESHOLD = 0.65` / `MICROCOMPACT_THRESHOLD = 0.55`（`:69-70`），分母见 §6 | 双阈值：`auto_compact_token_limit`（scope 可选 `Total` / `BodyAfterPrefix`）+ 硬上限 `model_context_window`，另加 fallback buffer（`session/context_window.rs:23`） |
| 触发点 | 每轮 LLM 调用前（`_compaction.py:23/55`）+ API overflow 后反应式（`_runner.py:1155`） | pre-turn（`turn.rs:1012`）、mid-turn post-sampling（`turn.rs:470`）、模型降级、comp_hash 变化、用户手动 |

**真实差异在于「锚点粒度」与「阈值结构」，不在于「真实 vs 估算」：** codex 的锚点重置粒度是「最后一个 model-generated 项」，agentao 是「上次 API 响应对应的消息条数」；codex 有双阈值 + buffer，agentao 是单阈值两级。

### 9.2 tool 输出截断：回溯式 vs record-time

- **agentao —— 回溯式。** 55% 阈值触发 `microcompact_messages`（`context_manager.py:282`）：3000 字符上限、**20% 头 + 80% 尾**（错误与最终结果通常在尾部）、最近 5 条 tool result 保全文。**它也是 full compression 的 Step 1**（§5）。
- **codex —— record 时。** `context_manager/history.rs:178` `record_items_with_metadata` → `process_item(item, policy)`，`TruncationPolicy` 按模型定。写进 history 的就已经是截断版。

agentao 的更 recency-aware（延迟到真需要时才做），**代价是它改写了已经发送过的前缀**——所以 `_compaction.py:40` 必须 `invalidate_token_anchor()`，注释写着「tool results truncated in place」。同一个改写也会**打掉 provider 的 prompt cache**：cache 前缀是连续的，从第一条被截断的 tool result 起后面全部 miss。codex 的 record-time 截断在这一点上是免费的。

**列为观察而非 P1：** agentao 尚未测量过 microcompaction 的 cache miss 成本，而它换来的收益是真实的。**先测再谈。**

### 9.3 失败处理

- **agentao：** 熔断器 —— 连续 3 次摘要失败后 `compress_messages` 直接原样返回并关闭自动压缩（`CIRCUIT_BREAKER_LIMIT = 3`，`:72`；`:354-364`）；成功即清零（`:417`）。API overflow 阶梯：压缩 → 重试 → `messages[-2:]` → 重试（`_runner.py:1160-1210`）。
- **codex local：** provider 的 `stream_max_retries` + backoff；遇 `ContextWindowExceeded` 时 `history.remove_first_item()` **逐条从头删并重试**（保 prefix cache，`compact.rs:315`），直到只剩 1 条才放弃。**注意 `compact_model_fallback` 不属于 local 路径（rev 4 更正）**：它只被 `compact_remote.rs:11-12` 与 `compact_remote_v2.rs:14-15` 引用，local 分支既不接收也不使用 `fallback_step_context`（`session/turn.rs:1247` 的 local 调用不传该参数）。「previous-model 压缩失败可 fallback 到当前模型重试」是 **remote 路径**的能力，本节此前把它错记在 local 名下。

**互有取舍：** agentao 有熔断器，codex 没有等价物（靠重试预算封顶）；codex local 的逐条剥离比 agentao 的 `[-2:]` 精细得多。见 §6(c)。

---

## 10. 【不建】codex 有而 agentao 不应照抄的

| 项 | codex 位置 | 不建理由 |
|---|---|---|
| **远端压缩**（v1 + v2） | `compact_remote.rs`(521) + `compact_remote_v2.rs`(1104) | **首要理由（rev 4 更正）：协议面不同，与 provider 无关。** remote compaction 是 **Responses API** 上的操作，而 **agentao 走的是 Chat Completions**（`agentao/llm/client.py:455,717`），根本不说这个协议。<br>次要理由：它依赖 `RemoteCompactionSupport` 能力协商，是 **provider-specific、非通用兼容契约**（**不是「OpenAI 特有」——Bedrock 也报 `V2`，§2.1**）。<br>措辞订正：`/responses/compact` 是**公开 API**，不是私有端点。另：codex 的 remote **v2 与 legacy `/responses/compact` 不是同一协议**，v2 另有自己的请求/重试/保留预算（`compact_remote_v2.rs:65` `RETAINED_MESSAGE_TOKEN_BUDGET = 64_000`）。 |
| **token-budget 空窗模式** | `compact_token_budget.rs:26` | **本格理由已错四版**（rev 2 归因 `WorldState`、rev 5 过度断言、rev 6 层次划错、rev 7 把第 ② 层写成无条件随行）——**引用本格前先读完，不要只取标题**。<br>**⓪ 整个模式默认关闭：** `Feature::TokenBudget` 是 `Stage::UnderDevelopment, default_enabled: false`（`features/src/lib.rs:1413-1417`）。下面三层都只在它被显式打开后才存在。<br>**① 换窗核心（本模式自身，无附加前提）：** 清空会话消息、换到新 context window。`start_new_context_window`（`session/mod.rs:3798`）只重建 initial context + 保留的 developer 消息——**会话对话就是丢掉了**。进入点比先前写的宽：手动 `/compact` 直接换窗（`tasks/compact.rs:36`）；**任何**走到 `run_auto_compact` 的原因（ContextLimit / ModelDownshift / CompHashChanged）都会路由到该实现（`session/turn.rs:1189`）；post-sampling 的「模型请求 `new_context` 或撞上限」那条额外受 `needs_follow_up` 约束（`turn.rs:458`）。<br>**② 模型自带的提醒 / 引导（与 ③ 无关，但**不**随 ① 无条件生效）：** `apply_model_defaults`（`session/token_budget.rs:28`）要**三个条件同时成立**才装载模型自带的 `reminder_threshold_tokens` / `reminder_message_template` / `guidance_message` / `auto_compact_fallback_prompt`：<br>　(i) `Feature::TokenBudget` 已开启；<br>　(ii) **没有显式覆盖** —— `has_explicit_settings`（同文件 `:9-26`）为真即提前返回，即 host 一旦自己配了 token-budget prompt/预算（`enabled` 与 `use_history_notes_extension` 两个键不算），模型自带默认值**就不再套用**；<br>　(iii) **当前模型确实提供** `model_messages.token_budget`，否则提前返回 —— 这不是罕见分支：当前 `models-manager/models.json` 里**只有 4/10 条模型条目带它**（`gpt-5.6-*` / `gpt-daybreak-blue` 有，`gpt-5.5` / `gpt-5.4` / `gpt-5.2` 等没有）。<br>三条都过之后，提醒写入 history 与 guidance 注入（`session/world_state.rs:121-126`）确实**不**再检查 ③ 的扩展开关。<br>**③ 可选的 `history` / `notes` 工具（默认关闭 + 三重门控）：** `use_history_notes_extension` **默认 `false`**（`config/mod.rs:1183`），且需该开关 **且** `model_provider.is_openai()` **且** `current_auth_uses_codex_backend()` 三者同时成立才注册（`ext/history-notes/src/extension.rs:33-42`）。<br>**不建的理由（结论**始终**未变——错的一直是理由，不是结论）：** 对 agentao 而言 ① 单独落地就是「丢上下文且无回捞通道」；② 依赖 codex 的**模型自带 prompt 资产**（`models-manager/models.json` 里逐模型定义），agentao 面向任意端点没有这份资产；③ 绑死 OpenAI + Codex backend auth，**本就不是可原样借鉴的东西**。<br>~~rev 2「前提是 codex 有 `WorldState`」~~ 错误归因（#16）。~~rev 5「真正的前提是一整套窗口协议」~~ 过度断言（#19）。~~rev 6 把提醒/引导算进默认关闭的扩展~~ 层次划错（#20）。<br>（附带一条不构成替代的事实：agentao 每轮重建含 memory 的 system prompt——`runtime/chat_loop/_runner.py:320-321`——但那是**跨轮**的稳定注入，不是**跨窗口**的任务续接。） |
| **压缩遥测矩阵** | `CodexCompactionEvent{trigger, reason, implementation, phase, status}` + `retained_image_count` / `cached_input_tokens` 等 | agentao 的 OTel 决策已在 `otel-peer-survey.zh.md` / 2026-08-14 定案为**不建**（dependents 实测为 0）。此处不重开。 |
| **保留策略改成「只留 user 消息」** | `compact.rs:652` | 见 §5——两种赌注不同，且 agentao 一侧的优势目前是**待验证假设**。**除非有真实失败案例，否则不动。** |

一个可以零成本借的 UX 细节：codex local 每次压缩后主动发 `WarningEvent`「Long threads and multiple compactions can cause the model to be less accurate. Start a new thread when possible.」（`compact.rs:390`）。agentao 的 `/compact` 只打印统计行（`cli/commands/compact.py:140`）。**列为可选，不列为 P**；注意它同样只在 local 路径上。

---

## 11. 已核对的对等项（不要重复上报）

- **孤儿 tool result 防护** —— agentao 靠推进到 `user` 边界（`:379`），codex local 靠只保留 user 消息。两者都不会产生孤儿。
- **手动压缩的观测一致性** —— `cli/commands/compact.py` 与 `_compaction.py::_maybe_full_compress` 发同一套 `CONTEXT_COMPRESSED` / session-summary 事件；`_produced_fresh_compaction`（`compact.py:26`）刻意用新加的 `[Compact Boundary` 头判定而非搜 `[Conversation Summary]`，避免旧摘要误判。这段注释已解释原因，**不要"简化"它**。
- **压缩前的 memory crystallization** —— `compress_messages` step 4b（`:404-407`）刻意跑在摘要**之前**，让规则抽取器看到原始 user 文本而非 LLM 的转述。codex 无此机制。
- **`[PIN]` 消息钉选、recently-read-files 提示、SQLite session summary** —— 均为 agentao 独有。
- **两级阈值（55% micro / 65% full）** —— codex 无廉价中间层（它的截断在 record 时无条件生效）。
- **熔断器** —— codex 无等价物。
- **摘要 prompt 的 `<analysis>` / `<summary>` 双块结构** —— agentao 独有（`_format_summary`，`:610`）。
- **两侧的 token 计数都是「服务端 anchor + 本地增量」** —— 见 §9.1，不是「真实 vs 估算」之别。

---

## 12. 若要动手，建议顺序

1. ~~**先测量，不改码**（对应 §3）~~ —— **已于 2026-08-23 完成**：测量走的是 `.agentao/sessions/*.json` 而非 `agentao.log`，结论选定 (c)+(d)，实现与实测数据见 §3「收口记录」。
2. ~~**§8 的文档漂移单独回填**~~ —— **已于 2026-08-22 完成**（勘误横幅，见 §8）。横幅同时记下两项**未解决**、需另行决策的事：(i) `build_pre_compact` 写死 `trigger="auto"` 导致手动压缩自报为 auto 的代码缺陷；(ii) PreCompact 的 payload / matcher 契约**没有在世文档**，目前只存在于那份已废止计划里。
3. §6 / §7 / §9.2 保持观察，等真实需求或实测数据。

**§10 的四项在有新证据前不要重开。**

---

## 13. 评审修正表（rev 2 / 4 / 5 / 6 / 7 / 8）

初稿经维护者**六轮**评审：rev 2 收 8 项，rev 4 收 5 项（#10–#14），rev 5 收 4 项（#15–#18），rev 6 收 1 项（#19），rev 7 收 2 项（#20–#21），rev 8 收 1 项（#22）；#9 是 rev 3 落地勘误横幅时自查发现的。**§10 的 token-budget 一格理由错了四版**（#16 / #19 / #20 / #22），引用该结论前务必读它现在的三层拆分。**初稿两项 P1 中撤回一项（§6），另撤回一项「待定契约」结论（§8），存活一项（§3）。** 再次提出本文任何结论前先读此表。

| # | 初稿说法 | 实际 | 后果 |
|---|---|---|---|
| 1 | `/model` 与 ACP「同一动作两种结果」，列为 **P1** | ACP 只带 `model` 的请求调用的就是 `agent.set_model()`，与 CLI 一致；只有显式带 `contextLength` 才改窗口（`acp/session_set_model.py:61-71`） | **P1 撤回**，§6 降为观察，「内部自相矛盾」措辞删除 |
| 2 | 用 `compact.rs` 代表「codex 的做法」 | 入口四路分发（`tasks/compact.rs:36`）；`RemoteCompactionV2` 默认开启（`features/src/lib.rs:1526`）且 OpenAI/Azure 报告 V2（`provider.rs:343`），**默认命中 remote v2 而非 local** | 新增 §2.1 实现矩阵；全文范围限定为 local 路径并在页首声明 |
| 3 | Claude Code 的 PreCompact 非阻塞，故 agentao 现状是刻意 parity | Claude Code **支持** exit 2 / `decision:"block"` 阻塞压缩；本仓库矩阵标为 ❌ 未兑现，且已定案为**范围排除**（该文「Claude Code 兼容性矩阵」两行 + Phase B 摘要条 + 修订备忘「评审四轮」#7；**rev 4 把此处从行号改为行名**，理由见 #11） | **「待定契约」撤回**，§8 全节重写为指向既有处置；本文不再提出新结论 |
| 4 | codex 把 prompt 加进**活 history**、复用**同一** session、摘要模型看到**完整无损** history | `sess.clone_history()` 取副本（`compact.rs:252`）；`new_session()` 新建（`:260`）；record 时已截断、`for_prompt` 会规范化、overflow 会删最老项 | 准确表述改为「保留原生结构，不经过 200/500 扁平化」 |
| 5 | MCP 与 TUI / app-server 并列可手动压缩 | MCP server 工具表只有 `codex` / `codex-reply`（`mcp-server/src/message_processor.rs:336-345`） | §2 表与 §7 均已剔除 MCP |
| 6 | codex 阈值用「API 真实用量」，与 agentao 的本地估算对立 | `get_total_token_usage` = 服务端 anchor + 最后一个 model-generated 项之后的本地估算（+ 可选的历史 reasoning 本地估算），`history.rs:415-431` | §9.1 改为「两侧同为 anchor + 增量」，真实差异改述为锚点粒度与阈值结构 |
| 7 | agentao 压缩后保留「最近 20 条**原文**」，故工具密集场景更稳 | Step 1 先 microcompact 整个列表再划分 to_keep（`context_manager.py:368,372`），保留的是**已截断**副本 | §5 更正；「更稳」降级为**待验证假设**；§10 对应理由同步 |
| 22 | rev 7 称第 ② 层「随 ① 一起生效」、`apply_model_defaults`「只看 `Feature::TokenBudget`」（§10 + #20 + README） | 还有两道守卫：`has_explicit_settings` 为真即返回（host 显式配置会**压过**模型默认值），以及当前模型必须提供 `model_messages.token_budget`——目前**仅 4/10** 条模型条目有（`session/token_budget.rs:9-26,28-38`） | §10 第 ② 层改为三条件并列；#20 与 README 的同源措辞一并改。**这是同一格的第四版错误** |
| 21 | 从未核对 `Feature::TokenBudget` 自身的默认值（§10，前三版皆缺） | 它是 `Stage::UnderDevelopment, default_enabled: false`（`features/src/lib.rs:1413-1417`）——**整个模式默认关闭** | §10 新增第 ⓪ 层。**这是 #19 教训写下后**一版内的**重复犯**：我查了 `RemoteCompactionV2` 的 spec，却没查 `TokenBudget` 的 |
| 20 | rev 6 把「检查点提醒」归入默认关闭的 history/notes 扩展；触发点只写「`new_context` 或撞上限」（§10） | 提醒/引导是**模型自带默认值**，提醒/引导与扩展开关无关（`apply_model_defaults`，`session/token_budget.rs:28`；guidance 由 `session/world_state.rs:121-126` 注入）——**但「只看 `Feature::TokenBudget`」这个措辞本身又是错的，见 #22**。触发点还包括手动 `/compact`（`tasks/compact.rs:36`）与**任何**进入 `run_auto_compact` 的原因（`turn.rs:1189`），而 post-sampling 那条另受 `needs_follow_up` 约束 | §10 由两层改为**三层**（换窗核心／模型自带提醒引导／可选 history-notes 工具），触发点补全 |
| 19 | rev 5 把 token-budget 的前提写成「`notes`/`history`/提醒/`new_context` 整套窗口协议」（§10） | `use_history_notes_extension` **默认 false**（`config/mod.rs:1183`），且注册还要 OpenAI provider + Codex backend auth 三重门控（`ext/history-notes/src/extension.rs:33-42`）；核心换窗（`turn.rs:458`）**不依赖它** | §10 拆成「①核心机制（无前提）／②可选连续性层（默认关闭+硬门控）」。**这是修 #16 时引入的新错误**：把一种*配置完整的使用形态*当成了模式本身的必要条件 |
| 18 | §8 / §13 称行号「**全部**改用章节名」，但 §8 排除理由仍写 `:91` / `:1016-1028`（rev 4） | 横幅位移后 `:91` 已是无关的 `suppressOutput` 行 | 改为「矩阵后的范围说明段」+「`B5. PreCompact gate` 一节」；**#11 的「全部改完」当时是假的** |
| 17 | 「codex 默认根本不走 local」（§3）/ README「default path is remote v2」 | V2 分支受 **capability + feature 两道**守卫（`tasks/compact.rs:41-43`）；`Unsupported` 的 provider 直接进 local | 两处均限定为「**对声明 `V2` 的 provider 而言**」。**这是 #14 的残留**——#14 改了 §2.1 却漏了另外两处绝对化措辞 |
| 16 | token-budget 模式「前提是 codex 有 `WorldState`（可完整重建的环境快照）」（§10） | `start_new_context_window` 同样清空会话消息（`session/mod.rs:3798` `start_new_context_window`）；codex prompt 明说新窗口不含当前对话。`WorldState` 不是对话检查点 | 「不建」结论保留，**理由整条重写**为「缺 `notes` / `history` / 检查点提醒 / `new_context` 这一整套窗口协议」 |
| 15 | 把 `{"trigger": "manual"}` 称作「Claude matcher」（§8 + 历史计划横幅） | Claude 配置形态是顶层字符串 `{"matcher": "manual\|auto"}`；`{"trigger": ...}` 是 **agentao 预翻译后的对象形态**，原样移植的 Claude `hooks.json` 在 agentao 里连加载都做不到 | 两处均改为「agentao 翻译后的 matcher 对象」并补说明 |
| 14 | 「只有非 OpenAI provider 才走 local」「其余 provider 报 Unsupported」（rev 2 §2.1 / 范围声明） | Bedrock 非 OpenAI 却无条件报 `V2`（`amazon_bedrock/mod.rs:174-187`）；且 agentao 用户可直连官方 OpenAI endpoint。边界是 **provider capability，不是厂商** | 范围声明与 §2.1 重写；选 local 的理由改为「机制同构」；新增「agentao 不说 Responses 协议」这条更根本的依据 |
| 13 | 用内部**已废止草案**证明 Claude Code 当前契约（rev 2 §8） | 该计划只能证明 agentao 当年据此决策，不能独立证明外部行为——而这正是推翻初稿结论的支点 | §8 补官方文档 <https://code.claude.com/docs/en/hooks>（*Exit code 2 behavior per event*：`PreCompact \| Yes \| Blocks compaction`，核对日期 2026-08-22）；结论不变但取证合格。顺带记下 Claude Code 另有 `PostCompact` 事件 |
| 12 | §9.3 把 previous-model fallback 记在 **codex local** 名下 | `compact_model_fallback` 只被 `compact_remote.rs:11-12` / `compact_remote_v2.rs:14-15` 引用；local 分支不接收 `fallback_step_context`（`turn.rs:1247`） | §9.3 更正为 remote 专属 |
| 11 | §8 / §13 写死历史计划的行号（`:14` / `:65` / `:73` / `:91` / `:1370`） | rev 3 的勘误横幅把该文正文整体下移 **21 行**，全部失效 | 改用**章节名 / 矩阵行名**引用，不再随横幅漂移 |
| 10 | 「8 项修正，其中 **2 项使初稿的 P1 作废**」「初稿 3 个 P1」 | 初稿只有**两项** P1（§3、§6）；§8 当时是「待定契约」不是 P1 | 更正为「两项 P1 中撤回一项，另撤回一项待定契约结论」；README 同步 |
| 9 | （rev 3 自查）§8 与 §12 写「`trigger: "manual"` 现在会发出了」 | `_payload.py:160` 把 `"auto"` 写死，无参数可覆盖；手动压缩的 payload 自报 `auto`，与同次压缩的 replay 事件（`compact.py:75` 发 `"manual"`）矛盾 | §8 / §12 已更正；该缺陷记入勘误横幅的「未解决」项，**未动代码** |
| 8 | `/responses/compact` 是「OpenAI 私有端点」 | 是公开 API（官方 API Reference 有记录）；且 remote v2 与 legacy 不是同一协议 | 不建结论保留。~~理由改为「OpenAI 特有」~~ —— **该措辞已被 #14 再次推翻**：应为「provider-specific」，且 rev 4 把首要理由换成「agentao 走 Chat Completions，不说 Responses 协议」 |

**错误类型自查（rev 4 更新）：**

- **过度锐化（#1 / #4 / #6 / #7 / #9）** —— 把代码陈述得比实际更锋利，五次都朝着「让对比更醒目」或「让结论更干脆」的方向。
- **拿局部代表整体（#2 / #12 / #14）** —— 用 fallback 路径代表整个 codex；把 remote 专属能力记在 local 名下；把能力协商的边界读成厂商边界。**三次都是同一个动作：抓到一处实现就当成通则。**
- **取证不合格（#3 / #13）** —— #3 凭记忆断言外部契约，而本仓库就有记录；rev 2 修它时**又用一份自我标注「草案 / 已废止」的内部文档去证明外部当前行为**，方向对了但证据档次仍不够。**同一条事实上连错两次，第二次才是方法问题而非事实问题。**
- **引用脆化（#11 / #18）** —— 跨文件写死行号，而我自己在 rev 3 往那份文件插了横幅，把它们全打漂；rev 4 声称「全部改完」时**并没有改完**，剩一处还在，而且紧挨着那句声明。**声称「全部」之前先 grep 一遍。**
- **改一处不改同类（#17）** —— #14 纠正了 §2.1 的厂商框架，却漏掉别处两句同源的绝对化措辞。修一条错误结论时要 grep 它的**所有**表述，不只是被指出的那处。
- **记账不实（#10）** —— 摘要里的计数没有回去对表。
- **归因到手边的名词（#15 / #16）** —— 看到 `WorldState` 就当成对话检查点，看到 `{"trigger": ...}` 就当成 Claude 的配置形态。**两次都是抓住一个眼前的名字去填因果，没去读它实际承载什么。**
- **修正时过冲（#16→#19→#20→#22，本文最顽固的一处）** —— §10 的同一格理由**连错四版**，每一版都比上一版更详实、也依然错：`WorldState` 归因错 → 「整套窗口协议」过度断言 → 层次划错 → 把有三重守卫的第 ② 层写成「随 ① 一起生效」。同族的还有 #3→#13。**结论：修正本身必须接受与原主张同等的验证，而不是「因为是修正所以更可信」。**
- **爱用「只看 / 只要 / 一起」（#22）** —— 这类绝对化连接词是本文错误的高发点：每次我把一段 early-return 链压缩成一个条件，就丢掉了守卫。**读到 `if … { return; }` 或 `let … else { return; }` 时，把每一条都写进句子，或者干脆不写条件。**
- **写下教训的下一版就重犯（#21）** —— #19 的教训原文是「默认值和门控条件要单独 grep：`default_enabled` / `Default for` / 注册处的 `if`，三样都看过才算读懂一个可选特性」。rev 6 里我照做了——**只对 `use_history_notes_extension` 照做**，却没对**承载它的那个 feature 本身**照做，于是漏掉 `Feature::TokenBudget` 默认就是关的。**教训要作用在整条依赖链上，不是作用在被点名的那一个符号上。**
