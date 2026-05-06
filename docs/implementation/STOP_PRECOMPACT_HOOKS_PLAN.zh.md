# Stop / PreCompact Hook 事件 —— 实施计划

**日期：** 2026-05-04（rev 2026-05-05 评审一/二/三/四/五/六/七/八/九/十/十一/十二/十三/十四/十五/十六/十七/十八/十九/二十/二十一/二十二/二十三轮 —— 见文末「修订备忘」）
**状态：** 草案。由 `docs/design/pi-mono-borrow-review.md` §KEEP P2 触发。
**源设计：** `docs/design/pi-mono-borrow-review.md` §KEEP P2（"plugin-hook 体系增加 `Stop` / `PreCompact` 事件"）。
**配套文档：** `STOP_PRECOMPACT_HOOKS_PLAN.md`（英文版）。
**范围：** 在现有 plugin-hook 表面新增两个 lifecycle 事件（`Stop`、`PreCompact`），分两层：事件表面（Phase A）+ control-aware gate（Phase B）。

---

## 摘要

- **Phase A —— 事件表面（约 1.5 天）。** 把 `Stop` 和 `PreCompact` 加入 `SUPPORTED_HOOK_EVENTS`。在 `chat_loop` 的 turn-end 边界（见「语义」）和**所有**压缩触发点 emit。**针对这两个事件采用 Claude Code 的平铺 snake_case wire 形态**（其它 adapter 方法仍用 Agentao 现有信封——本计划不动），让按 Claude Code Stop / PreCompact stdin 形态写的 hook 脚本原样可用。两者都走 `_dispatch_lifecycle`（side-effect-only）+ 一个 `_matches` 扩展支持 PreCompact 的 `manual|auto` matcher。哪些兼容、哪些不兼容，见下方「Claude Code 兼容性矩阵」。
- **Phase B —— Control-aware gate（约 2 天）。** 把 `Stop` 从 side-effect-only 升级为 control-aware result，**对齐 Claude Code Stop 的完整控制面**：exit code 2（block + stderr 作 reason）、JSON `decision: "block"` + `reason`、以及公共 output 字段（`continue`、`stopReason`、`suppressOutput`、`systemMessage`、`hookSpecificOutput.additionalContext`）。`chat_loop` 根据 `force_continue` 决定是否真正结束本回合。`PreCompact` blocking（Claude Code 通过 exit 2 / `decision: "block"` 支持）**明确不在范围内**——这条 gap 在矩阵和「PreCompact gate」一节中写明，不再用「deferred」字样。

两个 PR 互相独立。如果触发场景仅是可观察性（成本 gate、审计日志），PR-1 自身就可以发布。如果触发场景是 `shouldStopAfterTurn` 对齐（post-turn reviewer、autonomous-loop validator），则需要 PR-2。

---

## 为什么分两层（回顾）

`_dispatch_lifecycle`（`agentao/plugins/hooks.py:369-390`）的文档明确说明是 side-effect-only：非零退出码只记一条 warning，stdout 被收进 dispatcher 返回的 `list[HookAttachmentRecord]` 作为 `hook_success` 记录（**所有调用点目前都丢弃这个返回值——见 A6 附件 caveat**），dispatcher 不解析 `preventContinuation` / `blockingError` / `continue=false`。控制结果解析目前只在 `UserPromptSubmit` 路径上（`_parse_command_output`，`hooks.py:537+`）。

因此把 `Stop` 单纯塞进 `_dispatch_lifecycle` 只能拿到事件表面（host 可观察），拿不到 gate（host 可改流程）。下面的两层设计把"host 能看到事件"和"host 能改 loop 行为"分开，让 Phase A 在不绑定 Phase B 设计选择的情况下先发布。

---

## 语义 —— 每个事件标记的是哪个边界？

hook **名字**为了对齐 Claude Code 沿用，但仅靠名字回答不了「Stop 什么？」/「Compact 什么？」。本节把两个事件的定义钉死，让 payload 字段和 emit 位置保持一致。

**`Stop` = `BeforeTurnEnd`。** 当**当前用户 turn** 的 agentic loop 即将结束、且最终 assistant 消息**尚未提交**到 `agent.messages` 时触发。**不**是 session-end（用 `SessionEnd`）；**不**是 process-stop；**不**会在用户 turn 中途 Ctrl-C 时触发（没有干净的 turn 边界）。本计划的**三**个 emit 位置对应 `chat_loop` 中 turn 结束的三条路径：

- 模型不再返回 `tool_calls`（自然完成）；payload `turn_end_reason="final_response"`。
- loop 撞 `max_iterations` 且 `on_max_iterations` 回调返回 `"stop"`；payload `turn_end_reason="max_iterations"`。
- `ToolRunner.execute(...)` 返回 `doom_loop_triggered=True`，loop `break` 跳出（`chat_loop.py:271-272`）；payload `turn_end_reason="doom_loop"`。doom-loop 检测器是在 `tool_runner.py` / `tool_planning.py` 内部独立的安全网（见 `_DOOM_HALT_MESSAGE` / `result.doom_loop_triggered`）；它**不是**与 `max_iterations` 相同的条件（模型自我重复时第 2 轮就可能触发），所以它**有自己的 discriminator 值**而不是被并进 `"max_iterations"`。

需要区分「真实回答」和「迭代上限」和「模型行为异常」的 host **必须**读 `turn_end_reason`（snake_case —— 与 Claude 通用字段并列、作为顶层 Claude-flat key，见 A3），仅靠 hook 事件名是不够的。

**`PreCompact` = `BeforeMessagesMutation`。** 在任何**因上下文体积**而即将修改 `agent.messages` 的代码路径之前触发，此时即将被丢弃的历史还完整可观察。本计划的四个 emit 位置在 A4 列出；host 通过顶层 `compaction_type` 与 `reason` payload 字段区分（snake_case —— 这些是 Claude-flat 顶层 key，见 A3）。**不**会因非压缩类的修改触发（工具调用 append、用户消息 append、hook 注入的用户消息）；**不**在压缩**之后**触发——现有内部 `EventType.CONTEXT_COMPRESSED` 已覆盖那条边界。

以上定义是 A3（payload 字段）与 A4（emit 位置）唯一的权威来源。

---

## Claude Code 兼容性矩阵

**本计划目标：** 按 Claude Code 公布的 Stop / PreCompact 契约写的 hook 脚本，通过本计划的 adapter 加载后**原样可用**——仅限下表标 ✅ 的维度。标 🟡 是带说明的部分兼容；标 ❌ 是有明确理由的有意 gap。

本矩阵是兼容性的权威陈述；A3、A4、A6、B1、B2、B5 据此实现。

| 维度 | Claude Code（参照） | Agentao（本计划） | 状态 |
|---|---|---|---|
| 事件名 | `Stop`、`PreCompact` | 同 | ✅ |
| Wire input 形态（Stop、PreCompact） | 顶层平铺 snake_case | 通过专门的 `build_*` 输出顶层平铺 snake_case（A3） | ✅ |
| Wire input 形态（其它事件） | 顶层平铺 snake_case | Agentao 信封 `{event, data}` | ❌ —— 预先存在的全面 gap；**不在本计划范围内**，另开追踪 |
| 通用输入字段（key 形态） | `session_id`、`transcript_path`、`cwd`、`permission_mode`、`hook_event_name` | Stop / PreCompact 这五个全部作为顶层 key 提供（A3） | ✅（`transcript_path` 当前为 null —— 见 Open Question 1） |
| `permission_mode` 取值空间 | Claude Code 值：`"default" \| "plan" \| "acceptEdits" \| "auto" \| "dontAsk" \| "bypassPermissions"` | Agentao 值：`"read-only" \| "workspace-write" \| "full-access" \| "plan"`（来自 `agent.permission_engine.active_permissions().mode`） | 🟡 —— **字段形态对得上，但取值词汇不一致**。仅 `"plan"` 一致。按 `if permission_mode == "acceptEdits": ...` 分支的 Claude hook 脚本会看到 `"workspace-write"` 而走到 `else` 分支。值空间映射决定见 Open Question 5。 |
| Stop 输入 `stop_hook_active` | 有 | 有（A3） | ✅ |
| Stop 输入 `last_assistant_message` | 有 | 有（A3，A4 从 `assistant_content` 注入） | ✅ |
| PreCompact 输入 `trigger` | `"manual"` \| `"auto"` | 同枚举，但**`"manual"` 永不发出**（Agentao 没有 manual `/compact` CLI） | 🟡 —— 取值面更窄；A3 标注 |
| PreCompact 输入 `custom_instructions` | 有（manual 触发的 payload） | 有；始终为空（无 manual 触发） | 🟡 —— 字段在但永远空 |
| Exit code 0 | 继续 | 继续 | ✅ |
| Exit code 2 —— Stop | block + stderr 作为 reason 反馈 | 通过 Stop 专用 runner 兑现（B2） | ✅ |
| Exit code 2 —— PreCompact | block 压缩 | **不兑现** —— Phase A 对 PreCompact 是 observe-only | ❌ —— 见下方「PreCompact gate」 |
| 其它非零 exit | 非阻塞 warning | 同（沿用 `_run_command_hook` 现有行为） | ✅ |
| JSON `continue: false` | 停止 agent（覆盖默认继续） | Stop 路径兑现；PreCompact 不兑现（无 gate） | 🟡 |
| JSON `stopReason` | reason 文本 | 兑现（B2） | ✅ |
| JSON `suppressOutput`（Claude 语义 —— 把 hook 原始 stdout / debug log 从 transcript 隐藏） | 把 hook stdout 从 transcript / debug-log 通道隐藏 | **在原始 stdout 通道上当下是真空兑现。** Agentao **不**把 hook stdout 投影到 `PLUGIN_HOOK_FIRED`（emit 只携带 verdict + 计数 —— `outcome`、`matched_rule_count`、`added_context_count`、`suppress_output` 等），当前 chat-loop 也没有把 hook stdout 渲染进 user-visible transcript 的展示路径。所以**没有 stdout 正文可以隐藏**；字段被记录在 `StopHookResult.suppress_output` 上，并发到 `PLUGIN_HOOK_FIRED.suppress_output` 供 replay 保真，但 Claude「把 stdout 从 transcript 隐藏」的意图找不到作用对象。如果未来 Agentao 出现展示或投影 hook stdout 的通道，那个通道**必须**查询 `suppress_output` 才能保持 Claude 兼容。 | 🟡 —— **当前在该通道真空**；字段忠实记录但当前不 gate 任何展示路径 |
| Agentao 对 `suppressOutput` 的扩展（gate `additional_contexts` 回显） | 不在 Claude 文档语义内 | `True` 时 B3 还会跳过把 `<stop-hook>...additional_contexts...</stop-hook>` 块拼到助手最终回答（B1 docstring + B3 自然 turn allow 路径接线） | 🟡 —— **Agentao 自家的重新解读，不是 Claude parity。** Claude 的 `hookSpecificOutput.additionalContext` 是另一条结构化通道，文档说**不**受 `suppressOutput` 影响。我们在 Agentao 上有意把 `suppressOutput` 扩展为也 gate 回显，是因为「审计 hook 附 replay note 但不想污染用户可见答复」这个用例真实存在；另起一个 flag 会无谓增加配置面。想要严格 Claude 语义的 host：不要把 `suppressOutput: true` 与 `additionalContext` 写在同一份 hook output 上 —— 拆到两次 hook 调用里。 |
| JSON `systemMessage` | 系统消息字符串 | 映射到 `additional_contexts`（B2） | ✅ |
| JSON `decision: "block"`（Stop） | block this stop；`reason` 作 follow-up | 映射为 `force_continue` + `follow_up_message`（B2） | ✅ |
| JSON `decision: "block"`（PreCompact） | block 压缩 | **不兑现** | ❌ —— 见「PreCompact gate」 |
| JSON `hookSpecificOutput.additionalContext`（Stop） | 追加上下文 | 兑现（B2） | ✅ |
| Matcher（Stop） | 无 —— Stop hook 始终触发 | 同 | ✅ |
| Matcher（PreCompact）—— 运行时 regex 求值 | 对 `manual\|auto` 的 regex | 通过 `_matches` 扩展兑现（A2），针对 `trigger` 字段使用 **`re.fullmatch`** —— `manual\|auto` 这类 alternation 模式可以匹配 | ✅ 仅指运行时语义，**前提是 matcher 以 Agentao 对象形态 `{"trigger": "manual\|auto"}` 到达**。 |
| Matcher（PreCompact）—— 配置文件形态 | 顶层字符串字段：`{"matcher": "manual\|auto", ...}` | A2 / A1 要求**对象**形态；字符串 matcher（`"matcher": "auto"`）在解析期作为 `PluginWarning` 被丢弃，规则**不会**加载 | 🟡 —— **运行时语义 ✅，配置形态 ❌**。一份原样移植的 Claude `hooks.json` 中 PreCompact matcher 会被丢掉。host 必须自行预翻译为 `{"trigger": "..."}`，或等待配置翻译层（被矩阵已有的「Hook 配置文件路径 / 形态」❌ 行吞掉）。 |
| Hook 类型 —— `command`（Stop、PreCompact） | 两个事件都支持 | 支持 | ✅ |
| Hook 类型 —— `http`（Stop） | 支持 | **不支持** —— `"http"` 在 `KNOWN_UNSUPPORTED_HOOK_TYPES`（`agentao/plugins/models.py:210`）；A1 parser warn 后跳过 | ❌ —— 预先存在的 Agentao gap，与本计划无关。需要 HTTP 回调 Stop hook 的 host 须等 Agentao 提供 HTTP-hook runner；不在范围内。 |
| Hook 类型 —— `http`（PreCompact） | 支持 | 同 Stop 一样的拒绝 | ❌ —— 预先存在的 Agentao gap。 |
| Hook 类型 —— `mcp_tool`（Stop） | 支持 | **不识别** —— `"mcp_tool"` 既不在 `SUPPORTED_HOOK_TYPES` 也不在 `KNOWN_UNSUPPORTED_HOOK_TYPES`；parser 落到「Unknown hook type」分支并 warn 跳过 | ❌ —— 预先存在的 Agentao gap。增加 `mcp_tool` 需要新 runner 桥接到现有 MCP client（`agentao/mcp/client.py`）—— 另起一个独立计划。 |
| Hook 类型 —— `mcp_tool`（PreCompact） | 支持 | 同 Stop 一样的拒绝 | ❌ —— 预先存在的 Agentao gap。 |
| Hook 类型 —— `prompt`（Stop） | 支持（Claude 允许 prompt 型 Stop hook） | A1 的 `SUPPORTED_HOOK_TYPES_BY_EVENT` map **在解析期拒绝**（Stop 只允许 `command`） | ❌ —— **有意为之**，不是「还没做」：理由与迁移路径见上面「为什么 Stop / PreCompact 不支持 prompt 型 hook」一节。带 `{event: "Stop", hook_type: "prompt", ...}` 的 Claude `hooks.json` 在 Agentao 不会加载；改写成 `command` shim 即可。 |
| Hook 类型 —— `agent`（Stop） | 支持 | **不支持** —— `"agent"` 在 `KNOWN_UNSUPPORTED_HOOK_TYPES`，A1 也按事件拒绝 | ❌ —— 预先存在的 Agentao gap，与 Stop 无关。 |
| Hook 类型 —— `prompt`（PreCompact） | **Claude 在 PreCompact 上不支持**（仅 `command`/`http`/`mcp_tool` 有文档） | 解析期拒绝 | N/A —— **不构成兼容性 gap**，因为两边都不支持。列在矩阵里仅为完整性，无迁移问题。 |
| Hook 类型 —— `agent`（PreCompact） | **Claude 在 PreCompact 上不支持** | 不支持 | N/A —— 同上。 |
| Hook 配置文件路径 / 形态 | `~/.claude/settings.json`（Claude 专属 schema） | Agentao 读自己的 `permissions.json` / hook config；**形态与发现路径都不同** | ❌ —— 预先存在；**不在范围内**。需要 drop-in Claude config 文件的 host 须自行预翻译。 |

**本计划范围内有意保持 ❌ 的条目：**

- **PreCompact 的 blocking 路径**（exit 2 / `decision: "block"`）。PreCompact 的 emit 位置是就地修改 `agent.messages`、且周边 overflow-recovery 代码假设压缩最终成功；接受 host「拒绝」却没有「host 拒绝且仍然超长」兜底，会产生不可恢复的失控行为。下方「PreCompact gate」一节把这条钉成 gap，而非 roadmap。
- **prompt / agent hook 类型，仅针对 Stop** —— Claude 支持但我们选择不做（与 command hook 能力重复，见「为什么 Stop / PreCompact 不支持 prompt 型 hook」一节）。PreCompact 的 prompt/agent 行在矩阵中标 `N/A` 而非 ❌，因为 Claude 自己在 PreCompact 上**不**文档化支持这些（仅 `command` / `http` / `mcp_tool`）—— 不存在「我们考虑后决定不做」的兼容性 gap。

其它 ❌ 行（其它事件的 wire shape；config 文件 shape）**不在本计划范围内**；前者要动每个 adapter 方法，后者要新增配置翻译层，都是更大的 refactor。

---

## 为什么 Stop / PreCompact 不支持 prompt 型 hook

本节给矩阵中**仅针对 Stop** 的「拒绝 `hook_type ∈ {prompt, agent}`」❌ 行提供理由。（PreCompact 的 prompt/agent 在矩阵中标 `N/A`，因为 Claude 自己在 PreCompact 上**不**支持 —— 评审八轮已订正了把它误标为 ❌ 的早期草稿；见修订备忘。）评审五轮加了解析期拒绝（A1 的 `SUPPORTED_HOOK_TYPES_BY_EVENT`）；评审六轮把这条拒绝在矩阵上显式列出；本节钉死**为什么**我们不实现 Claude Code 对 Stop 支持的能力——让以后某位维护者问「干脆加上吧？」时能在文档里找到答案，而不需要重新推导。

### Stop 的情况——能力上与 command hook 重复

Claude Code 的 prompt 型 Stop hook 让 host 在 turn-end 注入一段模板化的 prompt 给模型（典型用法：post-turn reviewer 问「你真做完了吗」）。在 Agentao 里，**同样的效果**通过 `command` 型 Stop hook 完全可达，且歧义严格更少：

- reviewer host 写一个 command 型 Stop hook 子进程，在子进程内自行调用 LLM（用 host 自己的凭证和模型——这通常是 host 想要的，因为审阅模型可以与 agent 模型不同）。
- 子进程在 stdout 上发出 Claude 兼容的 Stop JSON：`{"decision": "block", "reason": "...你跳过了测试步骤"}` 触发 force-continue；`{"hookSpecificOutput": {"additionalContext": "..."}}` 附 review note；`{"continue": false}` 无条件接受 stop。这三条路径在 B2 parser 表里已端到端可用。
- host 完全控制审阅用哪个模型、用什么 system prompt、给多少预算——这些参数在「把模板 prompt 喂给 agent 自己的模型」这种设计里**根本无法暴露**。

如果反过来原生支持 prompt 型 Stop，**每一个** Claude Stop output 字段都会冒出一个未答的设计问题：模型对 prompt 的回复落到哪？

| 若 prompt-hook 的模型回复说…… | ……应该写进 `force_continue`？`follow_up_message`？`additional_contexts`？`system_message`？`blocking_error`？ |
|---|---|
| 「你应该继续」 | 直观读应是 `force_continue=True`，但怎么把它和「模型只是闲聊」区分开？ |
| 「看起来不错」 | `additional_contexts`？还是 no-op？ |
| 「你漏了一个测试」 | `blocking_error`？`follow_up_message`？两个都写？ |

Claude Code 文档里**没有**把自由文本回复映射到结构化 Stop output schema 的标准答案；Claude Code 自身回避这个问题的方式是把 prompt-hook 的回复**作为一条普通会话消息**注入。如果 Agentao 要复刻这条路径，就得在 `force_continue` 与 `additional_contexts` 之外再加第三个 Stop 控制面（直接注入会话）。**已有的 command-hook 路径以更低的设计成本覆盖了所有具体的 reviewer 用例。**

**结论：** 拒绝。想做 reviewer 的 host 写一个内部调 LLM 的 `command` 型 hook。失去的兼容性属于「Claude 配置文件移植 gap」，矩阵中「Hook 配置文件路径 / 形态」那一行本来就标 ❌——也就是说，**这条小 gap 已被一个预先存在的更大 gap 吞掉**，并不是新引入的破口。

### PreCompact 的情况——Claude 自己也不支持 prompt/agent

**对本计划早期草稿的更正。** 早期评审轮次把这一节写成「Claude 支持 prompt 型 PreCompact，我们选择不做」。这个前提**是错的**：Claude Code 文档化的 hook-type 矩阵列出 PreCompact 仅支持 `command` / `http` / `mcp_tool` —— `prompt` 与 `agent` 在 PreCompact 上**本来就不是 Claude 的特性**。所以这里根本不存在「Claude 说 yes，我们说 no」的 gap 可讨论。

对矩阵的影响：

- `prompt`（PreCompact）：N/A。Claude 与 Agentao 都不支持。矩阵保留这一行只是为了让读者不疑惑「这个评估是不是漏了」。
- `agent`（PreCompact）：N/A。同上。
- `http` 与 `mcp_tool`（PreCompact）：❌ **仅在 Agentao 一侧** —— Claude 支持。这两条是真实的 Claude vs Agentao PreCompact hook-type gap，矩阵中已显式列出。

上面 Stop 一节的论述继续成立：Claude 对 Stop **确实**支持 prompt/agent（文档里有 Stop prompt-hook 示例），我们在 Agentao 中**确实**因为「能力与 command-hook 重复」的理由选择不实现。

### 这对从 Claude Code 迁移意味着什么

带 `{event: "Stop", hook_type: "prompt", prompt: "..."}` 的 `hooks.json` 在本计划下不会在 Agentao 加载。迁移路径：

1. 写一个 `command` 型 Stop hook 脚本（几行 bash / Python）。
2. 在脚本里调 host 想用于 review 的 LLM。
3. 在 stdout 发 Claude Code Stop JSON（`decision`、`additionalContext` 等）—— 与 Claude 自家文档相同的形态。

这是一次性的逐脚本转换，并且产出的 hook 严格更强（独立模型选择、独立预算、其输出如何被解读毫无歧义）。矩阵的 prompt/agent Stop 与 PreCompact ❌ 行、A1 的解析期拒绝、本节，三者共同构成「我们考虑过、为什么不做、绕开方法在哪」的成文记录。

---

## Phase A —— 事件表面

### A1. 加入支持事件集合 + 按事件校验 hook 类型

`agentao/plugins/models.py:197`：

```python
SUPPORTED_HOOK_EVENTS: set[str] = {
    "UserPromptSubmit",
    "SessionStart",
    "SessionEnd",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",        # 新增
    "PreCompact",  # 新增
}

# 每个事件允许的 hook 类型。Stop / PreCompact 有意排除 "prompt"：
# 运行期 `_dispatch_lifecycle` 对这两个事件只会调用 command 类型 hook
# （B2 还为 Stop fork 了专用 runner，不认 prompt hook），因此一条
# prompt 类型的 Stop / PreCompact 规则会被解析为「支持」、却在
# dispatch 时被悄悄丢掉。改成在解析期就拒绝，让配置错误以 parser
# warning 形式响亮地暴露，而不是变成静默 no-op。
SUPPORTED_HOOK_TYPES_BY_EVENT: dict[str, set[str]] = {
    "UserPromptSubmit": {"command", "prompt"},
    "SessionStart": {"command"},
    "SessionEnd": {"command"},
    "PreToolUse": {"command"},
    "PostToolUse": {"command"},
    "PostToolUseFailure": {"command"},
    "Stop": {"command"},        # 新增 —— 显式排除 "prompt"
    "PreCompact": {"command"},  # 新增 —— 显式排除 "prompt"
}
```

**扩展 `ParsedHookRule.is_supported`**（`agentao/plugins/models.py:226`）。当前：

```python
@property
def is_supported(self) -> bool:
    return self.hook_type in SUPPORTED_HOOK_TYPES and self.event in SUPPORTED_HOOK_EVENTS
```

改为：

```python
@property
def is_supported(self) -> bool:
    if self.event not in SUPPORTED_HOOK_EVENTS:
        return False
    allowed = SUPPORTED_HOOK_TYPES_BY_EVENT.get(self.event, SUPPORTED_HOOK_TYPES)
    return self.hook_type in allowed
```

`get(...)` 默认回退到 `SUPPORTED_HOOK_TYPES`，让以后新增的事件即便没在 map 中登记也保持向后兼容。

**parser 侧的按事件检查（`agentao/plugins/hooks.py:120-140`）。** 上面的 `is_supported` 扩展是运行时谓词，但矩阵的「Rejected at parse time」声明与 A6「parser 发出 warning + 解析期拒绝」的要求，需要**parser** 在规则到达 `rules.append` 之前就丢掉它。当前 parser 只检查 `hook_type in SUPPORTED_HOOK_TYPES`（`hooks.py:132`），会让一条 `{event: "Stop", type: "prompt"}` 的规则通过、最终以 `is_supported == False` 进入 `rules`——运行时悄无声息地空转，与「解析期拒绝」相违。

在现有 `hook_type in SUPPORTED_HOOK_TYPES` 分支**之后**、`rules.append` **之前**加一条按事件的检查：

```python
# 现有检查（不动）：
if hook_type in KNOWN_UNSUPPORTED_HOOK_TYPES:
    warnings.append(PluginWarning(... "not supported — skipped" ...))
    continue
if hook_type not in SUPPORTED_HOOK_TYPES:
    warnings.append(PluginWarning(... "Unknown hook type — skipped" ...))
    continue

# 本计划新加的按事件检查：
allowed_for_event = SUPPORTED_HOOK_TYPES_BY_EVENT.get(
    event_name, SUPPORTED_HOOK_TYPES,
)
if hook_type not in allowed_for_event:
    warnings.append(
        PluginWarning(
            plugin_name=plugin_name,
            message=(
                f"Hook type '{hook_type}' is not supported for event "
                f"'{event_name}' — skipped. (Allowed for this event: "
                f"{sorted(allowed_for_event)})"
            ),
            field="hooks",
        )
    )
    continue

# ... 后续 timeout / matcher 检查，然后 rules.append(...)
```

效果：prompt 型的 Stop 或 PreCompact 规则在解析期就以 `PluginWarning` 形式被丢弃，对齐矩阵承诺。运行时 `is_supported` 扩展保留为纵深防御 —— 与 A2 中运行时 matcher guard 一致角色 —— 防住任何在 loader 之外直接构造 `ParsedHookRule` 的代码路径。

A6 的 `test_hooks_stop_precompact_reject_prompt_type.py` 断言**解析期丢弃**（`rules` 列表不含该 prompt 型规则、`warnings` 含按事件类型组合的专门消息），而不仅是运行时 `is_supported` 的翻转。

### A2. dispatcher 入口方法 + 扩展 `_matches` 支持 PreCompact 的 `manual|auto`

`agentao/plugins/hooks.py`，与 `dispatch_session_start` 等并列：

```python
def dispatch_stop(self, *, payload, rules) -> list[HookAttachmentRecord]:
    return self._dispatch_lifecycle("Stop", payload, rules)

def dispatch_pre_compact(self, *, payload, rules) -> list[HookAttachmentRecord]:
    return self._dispatch_lifecycle("PreCompact", payload, rules)
```

**Phase B 会替换 `dispatch_stop` 的返回类型**为 `StopHookResult`（见 B2）。这是 PR-1 与 PR-2 之间**有意为之的破坏性签名变更**：`dispatch_pre_compact` 保持 `list[HookAttachmentRecord]`（PreCompact 仍 observe-only —— 见 B5），但 `dispatch_stop` 升级以承载 gate 信号。PR-2 的 checklist（Sequencing 一节）明确包括把 A6 的 dispatcher 测试（`test_hook_dispatcher_stop.py`）从断言裸 list 改为 walk `result.messages` —— 见 B2 的「测试影响」一段。Phase-A list 形态目前没有 host 依赖（dispatcher 是内部 API），破坏面被局限在 Agentao 内部。

`_dispatch_lifecycle` 本身不动。**`_matches` 需要扩展**以处理 Stop / PreCompact 引入的三件新事实：

1. 这两个事件的 payload 是**顶层平铺 snake_case**（Claude Code 对齐——见 A3），**不**是 agentao `{event, data}` 信封，所以事件为 `Stop` / `PreCompact` 时 `_matches` 必须从顶层取字段。
2. Claude Code 的 PreCompact matcher 是对 `trigger`（`manual|auto`）的 **regex**，**既不是 glob 也不是 `toolName`**。现有的 `_glob_match` helper（`agentao/plugins/hooks.py:832-844`）不支持 regex alternation：`manual|auto` 这种模式不带 `*`，会落到 exact-equality 分支，对 `"manual"` / `"auto"` 都不会命中。我们在 PreCompact 局部新增一个 `_regex_match_full` helper，使用 `re.fullmatch`，让 Claude 风格模式可用。
3. Stop **没有官方 matcher** —— Claude Code 的 Stop hook 始终触发。即便规则配错带了非空 `matcher`，仍应触发（矩阵「Matcher (Stop): 无 —— 始终触发」一行）；Stop 路径忽略 matcher 内容，恒返回 True。

```python
import re

def _regex_match_full(pattern: str, value: str) -> bool:
    """Claude-compat 事件 matcher 用的锚定 fullmatch regex。"""
    try:
        return re.fullmatch(pattern, value) is not None
    except re.error:
        # 模式格式错误时降级为 exact-equality，避免规则被悄悄丢掉；
        # 解析期能 warn 就 warn。
        return pattern == value

def _matches(self, rule: ParsedHookRule, payload: dict[str, Any]) -> bool:
    if rule.matcher is None:
        return True

    # 类型 guard：`rule.matcher` 取自 parse 期 `entry.get("matcher")`
    # （`agentao/plugins/hooks.py:161`），parser 当前不强制 dict 形态。
    # 用户（或 Claude config 翻译层）可能给出 `"manual|auto"` 这样的
    # 字符串、甚至 list。如不加防护，下面的 `.get(...)` 会 AttributeError。
    # 两条可选反应：
    #   (i) 把非 dict 当成「匹配一切」—— 太宽容，hostmin 期望 matcher
    #       本来是用来过滤的
    #   (ii) 拒绝该规则（视为不匹配）并 warn
    # 选 (ii)：配错的 matcher 不应悄悄扩大规则覆盖面。A1 parser 在能
    # 提前判别类型时也会 warn —— 见下面的 A1 caveat。
    if not isinstance(rule.matcher, dict):
        logger.warning(
            "Hook rule for event %r has non-dict matcher %r; "
            "treating as no-match. Matchers must be objects, e.g. "
            "{\"trigger\": \"manual|auto\"}.",
            rule.event, rule.matcher,
        )
        return False

    # Claude 平铺事件：从顶层取字段。
    event = payload.get("hook_event_name") or rule.event
    if event in {"Stop", "PreCompact"}:
        if event == "PreCompact":
            trigger_pattern = rule.matcher.get("trigger")
            if trigger_pattern is not None:
                payload_trigger = payload.get("trigger", "")
                # Claude Code 语义：regex（不是 glob）。
                if not _regex_match_full(trigger_pattern, payload_trigger):
                    return False
        # Stop：matcher 在 Claude Code 中未定义；恒触发。
        return True

    # Agentao 信封事件（UserPromptSubmit / SessionStart /
    # PreToolUse / PostToolUse / PostToolUseFailure）—— 行为不变；
    # 仍走 glob，`_glob_match` 不动。
    data = payload.get("data", {})
    tool_name_pattern = rule.matcher.get("toolName")
    if tool_name_pattern is not None:
        payload_tool = data.get("toolName", "")
        if not _glob_match(tool_name_pattern, payload_tool):
            return False
    return True
```

**A1 解析期的 matcher 类型检查（与上面运行时 guard 配套）。** 加载器在 `agentao/plugins/hooks.py:161` 当前是把 `entry.get("matcher")` 直通进去的。在那里加类型检查，**整条规则丢弃**（而不是把坏值改写）—— 原因是现有运行时契约在 `hooks.py:394` 是 `if rule.matcher is None: return True`，即 `None` 意味着「匹配所有事件」；如果默默把坏 matcher 改成 `None`，就把 warning 的本意完全反转了：本来应该「不匹配」的配错，会突然变成「匹配每个事件」。「丢弃整条规则」对齐 Claude Code 的「坏规则不会加载」语义，也避开这个反转。

使用现有的 `PluginWarning` 模型 —— 加载器的 `warnings` 列表类型是 `list[PluginWarning]`（`hooks.py:82`），追加裸 f-string 会破坏类型：

```python
matcher = entry.get("matcher")
if matcher is not None and not isinstance(matcher, dict):
    warnings.append(
        PluginWarning(
            plugin_name=plugin_name,
            message=(
                f"Hook rule under '{event_name}' has non-object matcher "
                f"of type {type(matcher).__name__}; matcher must be an object "
                f"like {{\"trigger\": \"manual|auto\"}} — rule skipped."
            ),
            field="hooks",
        )
    )
    continue  # 整条 rules.append 跳过；规则不加载。

rules.append(
    ParsedHookRule(
        event=event_name,
        hook_type=hook_type,
        # ... matcher=matcher（上面已校验为 dict 或 None）
    )
)
```

**为什么是「丢弃规则」而不是 `matcher = None`。** 运行时契约里 `None` ≡「无 matcher，每个事件都触发」。一个写下 `"matcher": "auto"` 的用户显然是想**过滤**事件——把它默默变成「匹配一切」与他的意图正相反。丢弃规则是保守解读：host 看到 parser warning、规则根本不跑，host 拿到信息去修配置。

**`_matches` 顶部的运行时 guard 现在是真正的纵深防御，而非主防线。** 解析期已经丢掉畸形规则后，运行时的 `isinstance(rule.matcher, dict)` 检查只在「将来某条代码绕开 loader 直接构造 `ParsedHookRule`」时触发。仍值得保留——但 host 应该依赖解析期的丢弃来获得可见性。

**匹配规则计数（Phase A emit-gate 依赖）。** Phase A 的 `PLUGIN_HOOK_FIRED` payload（A5）携带 `matched_rule_count` 并按 `matched_rule_count > 0` 守住 emit；但上面给出的 Phase A `dispatch_stop` / `dispatch_pre_compact` 签名返回 `list[HookAttachmentRecord]` —— attachment 计数不是 rule 计数的可靠代理（具体失败模式见下面「为什么不用 `len(attachments)`」一段）。为在不动 lifecycle dispatch 返回类型的前提下暴露 count，向 `PluginHookDispatcher` 加一个小型公共工具：

```python
def select_matching_rules(
    self, event: str, payload: dict[str, Any], rules: list[ParsedHookRule],
) -> list[ParsedHookRule]:
    """Stop / PreCompact 的规范选择过滤器
    （event + is_supported + _matches）。调用方既用它给 A5 emit gate
    计数，又把已过滤列表喂给对应的 `dispatch_*` 方法。

    为何用 `is_supported` 而不是直接 `hook_type == "command"`，以及
    两者对 Stop / PreCompact 在实际中为什么等价 —— 见下面
    「与 `_dispatch_lifecycle` 的对齐说明」。"""
    return [
        r for r in rules
        if r.event == event and r.is_supported and self._matches(r, payload)
    ]
```

Phase A chat-loop helper（`_dispatch_stop` / `_dispatch_pre_compact`，见 A4）按四步使用：

1. 经 `ClaudeHookPayloadAdapter.build_stop(...)` / `build_pre_compact(...)` 构造 Claude-flat payload。
2. `matched = dispatcher.select_matching_rules(<event>, payload, agent._plugin_hook_rules)`。
3. **若 `len(matched) == 0`，提前返回，不调 `dispatch_*` 也不 emit `PLUGIN_HOOK_FIRED`** —— 这就是 A5 引用的 no-emit gate。
4. 否则调用对应的 lifecycle dispatch —— Stop 走 `dispatcher.dispatch_stop(payload=payload, rules=matched)`，PreCompact 走 `dispatcher.dispatch_pre_compact(payload=payload, rules=matched)`（传入已过滤列表让 dispatcher 内部 re-filter 在两个事件下都退化为 no-op），并以 `matched_rule_count=len(matched)` emit `PLUGIN_HOOK_FIRED`。

**与 `_dispatch_lifecycle` 的对齐说明。** lifecycle runner 在 `agentao/plugins/hooks.py:381` 按 event + `hook_type == "command"` + `_matches` 过滤。这里的 `is_supported` 是严格超集（它允许 `hook_type in {"command", "prompt"}`），对支持 prompt-type 规则的事件两者会分叉 —— `select_matching_rules` **不是** `_dispatch_lifecycle` 内部循环过滤的逐字复刻。对 Stop / PreCompact 来说，这个分叉被 A1 的按事件 hook-type 拒绝关掉：任一事件下的 prompt-type 规则在解析期被丢弃，运行时 `is_supported` 也翻为 `False`，所以 loader 产出的所有 Stop / PreCompact 规则上 `is_supported` 与 `hook_type == "command"`必然一致。这里保留 `is_supported`（而不写死 `hook_type == "command"`），是为了让本工具与 Agentao 通用的「这条规则到底能不能跑」谓词保持一致；未来若有事件合法地支持 prompt-type 规则，不必再 fork 一个新的选择过滤器。docstring 里「Stop / PreCompact 的规范选择过滤器」才是权威规格；与 `_dispatch_lifecycle` 的近似对齐是巧合，由 A1 兜住 —— 实现者应依赖 A1 的解析期拒绝（与它支撑的运行时 `is_supported` 检查），而不是 lifecycle runner 的过滤形态。

**Phase B 衔接。** `dispatch_stop` 在 B2 升级为返回 `StopHookResult` 时，结果对象也携带 `matched_rule_count` —— 用同一套 event/is_supported/matcher 过滤式从 dispatcher 内部算出，作为纵深防御。helper 仍通过 `select_matching_rules` 预先计算，保证 no-emit early-return 在 `dispatch_stop` 进入之前生效（不开 subprocess、不打扰 transport）。两路 count 来自同一过滤式必然一致；若未来 refactor 让它们分叉，B2 的 `matched_rule_count` 是 dispatcher 侧权威。

**为什么不用 `len(attachments)`。** Stop command hook 干净退出 0 + 空 stdout，会产生一条 `hook_success` 附件 —— 所以**碰巧**那一种情况下 attachment 数等于 rule 数；但这是巧合。一个吐多个 `additionalContext` 的 hook 会让 attachment 数大于 rule 数；反过来，未来若 refactor 把 clean-exit 的 `hook_success` 附件去掉，attachment 数会瘪到 0。gate 真正想守的是「这个事件上有没有任何 hook 触发」，`select_matching_rules` 是唯一的权威来源。

**每个事件的 matcher 方言。** PreCompact 的 `trigger` matcher 用 **regex（`re.fullmatch`）** 对齐 Claude Code；现有的 `toolName` matcher 继续用 glob。两个方言**不**全局统一——统一会破坏现有 Agentao hook 配置中针对 `toolName` 的 `*` glob 用法。这种不对称在矩阵的 matcher 行已记录、A6 已加测试。

Stop / PreCompact 走平铺 + PreCompact 用 regex matcher、其它事件走信封 + glob matcher 的不对称是本计划有意为之——只有这两个新事件需要 Claude 兼容；改写所有 adapter **以及**所有 matcher 方言是另一个 refactor（见兼容性矩阵）。

### A3. payload adapter —— Stop / PreCompact 用 Claude Code 平铺 snake_case

扩展 `ClaudeHookPayloadAdapter`（`agentao/plugins/hooks.py:213+`），新增 `build_stop` 和 `build_pre_compact`。**这两个 builder 返回 Claude Code 顶层平铺 snake_case schema**，**不**是现有 agentao `{event, data}` 信封——这样按 Claude Code stdin 形态读取的 hook 脚本能原样收到自己期望的 keys。

**为何与同类 builder 不一致。** `build_user_prompt_submit` / `build_session_start` / `build_pre_tool_use` 等返回 `{"event": "...", "data": {camelCase}}`。把整个 adapter 切到平铺 snake_case 会破坏所有现有事件消费方（以及 `_matches` 的 `data["toolName"]` 路径）——属于跨切面 refactor，不在本计划范围内。Stop 和 PreCompact 是全新事件，wire 形态由我们决定，于是从一开始就做 Claude 兼容。A2 的 `_matches` 扩展处理这种双形态。

**通用字段（顶层 snake_case）—— 两事件共用：**

| 字段 | 来源 | 说明 |
|---|---|---|
| `hook_event_name` | `"Stop"` 或 `"PreCompact"` | Claude 通用输入要求 |
| `session_id` | `agent._session_id` | 未设置时为空字符串 |
| `transcript_path` | `null`（Open Question 1） | Agentao 当前没有单一权威 transcript 文件；OQ1 选 (a) |
| `cwd` | `str(agent.working_directory)` | |
| `permission_mode` | `agent.permission_engine.active_permissions().mode`（`"read-only" \| "workspace-write" \| "full-access" \| "plan"`） | engine 缺失时回退 `"workspace-write"` |

**Stop 专属字段（顶层）：**

| 字段 | 取值 | 来源 |
|---|---|---|
| `stop_hook_active` | `bool` | Phase A 为 `False`；Phase B 在同一次 `chat()` 调用中第 2 次及以后 dispatch 设为 `True`，让 hook 能识别自己被前一次 `force_continue` 重入 |
| `last_assistant_message` | `str` | 即将定稿的助手回答文本——自然 turn：`assistant_content`；max-iter：`assistant_content_max`（B3 在 dispatch 之前都构造好了）。Stop hook **不解析 transcript** 就能审最终回答，靠的就是这个字段。 |
| `turn_end_reason` | `"final_response" \| "max_iterations" \| "doom_loop"` | Agentao 自加字段（评审三轮引入，评审十五轮扩展以覆盖 doom-loop break —— 见「语义」一节），与 Claude 通用字段并存。仅关心 Claude 对齐的 host 可忽略。 |

**PreCompact 专属字段（顶层）：**

| 字段 | 取值 | 说明 |
|---|---|---|
| `trigger` | `"manual" \| "auto"` | Claude Code 对齐。**本计划始终 `"auto"`** —— Agentao 没有 `/compact` CLI，`"manual"` 永不发出。A2 的 matcher 仍接受 `"manual"` 模式，只是永不命中。 |
| `custom_instructions` | `str` | Claude Code 对齐。始终为空（无 manual 触发）。 |
| `compaction_type` | `"microcompact" \| "full" \| "minimal_history"` | Agentao 自加字段，在 `trigger="auto"` 下细分，对齐 `_emit_context_compressed` 的 `compression_type` 实参（`chat_loop.py:534, 561`）。区分启发式压缩与失败兜底用。Claude Code schema 没有此字段。 |
| `reason` | `str` | Agentao 自加字段。**直接镜像每个 emit 位置已传给 `_emit_context_compressed` 的 `reason=` 实参**，让压缩前/后审计事件无需规范化即可对齐。A4 的稳定取值（已对照 `chat_loop.py` 第 413、443、536、563 行验证）：`"microcompact_threshold"`、`"compression_threshold"`、`"api_overflow"`、`"api_overflow_after_compression"`。 |

**builder 形态（示意）：**

```python
def build_stop(
    self, *, session_id, cwd, last_assistant_message,
    stop_hook_active,
    turn_end_reason: Literal["final_response", "max_iterations", "doom_loop"],
    permission_mode,
) -> dict[str, Any]:
    return {
        "hook_event_name": "Stop",
        "session_id": session_id or "",
        "transcript_path": None,  # OQ1 (a)
        "cwd": str(cwd or Path.cwd()),
        "permission_mode": permission_mode or "workspace-write",
        "stop_hook_active": bool(stop_hook_active),
        "last_assistant_message": last_assistant_message or "",
        "turn_end_reason": turn_end_reason,
    }

def build_pre_compact(
    self, *, session_id, cwd, compaction_type, reason, permission_mode,
) -> dict[str, Any]:
    return {
        "hook_event_name": "PreCompact",
        "session_id": session_id or "",
        "transcript_path": None,
        "cwd": str(cwd or Path.cwd()),
        "permission_mode": permission_mode or "workspace-write",
        "trigger": "auto",
        "custom_instructions": "",
        "compaction_type": compaction_type,
        "reason": reason,
    }
```

### A4. `chat_loop` 中的 emit 位置

`agentao/runtime/chat_loop.py`。每个位置都通过新增的 `self._dispatch_stop(...)` / `self._dispatch_pre_compact(...)` helper 调用，形态对照 `_dispatch_user_prompt_submit`，但返回 side-effect-only 的列表。Stop helper 把作用域内已有的 `assistant_content` 注入 payload 的 `last_assistant_message` 字段；两个 helper 的 `permission_mode` 都从 `agent.permission_engine.active_permissions().mode` 读（engine 缺失时回退 `"workspace-write"`）。每个位置的 discriminator 字段：

**Stop**（**三个**位置——都在「即将定稿的 assistant 消息提交之前」；B3 把 `agent.messages.append(final_msg)` 移到 dispatch **之后**才 commit）：

| 位置 | `chat_loop.py` 行 | `turn_end_reason` | `last_assistant_message` 来源 |
|---|---|---|---|
| 自然 turn 结束（不再有 `tool_calls`） | ~306（迭代 loop 的 final-answer `else` 分支） | `"final_response"` | `assistant_content` |
| max-iterations 出口（`on_max_iterations` 返回 `"stop"` 之后） | ~185（`else: # "stop"` 分支内 —— 见 B3 钉住的位置） | `"max_iterations"` | `assistant_content_max`（B3 在同一分支里构造） |
| doom-loop break（`if doom_triggered: break`） | ~271-272（tool-call 分支内、`agent.messages.extend(tool_results)` 之后立刻判定） | `"doom_loop"` | `assistant_content_doom` —— 通常是空内容（产生违规 tool_calls 的那条 assistant_message），所以回退到 `"Tool execution halted by doom-loop detection."` |

**`stop_hook_active` 接线（Phase B）。** `_dispatch_stop` helper 从 B4 引入的 chat-loop 实例计数器算 `stop_hook_active = (self._stop_reentries > 0)`。Phase A 始终传 `stop_hook_active=False`（计数器未启用、每次 `chat()` 调用 `_stop_reentries` 从 0 起）。Phase B `force_continue` 路径递增 `_stop_reentries`（见 B3 自然 turn / max-iter / doom-loop 三个 force_continue 接线点）后，下一次 dispatch 自动从 `False` 翻 `True`。按 Claude Code 文档语义「True 即「我正被自己上一次 force-continue 重入」」写的 hook 脚本，**不需要 host 侧额外接线**就能看到匹配值。B6 测试 `test_hooks_stop_hook_active_reentry.py` 端到端验证这次 false→true 翻转。

**PreCompact**（四个位置——所有因上下文体积而修改 `agent.messages` 的代码路径，**修改之前** emit）：

| 位置 | `chat_loop.py` 行 | `trigger` | `compaction_type` | `reason` |
|---|---|---|---|---|
| `_maybe_microcompact`（顶部，`needs_microcompaction(...)` 返回 `True` 之后） | ~396 | `"auto"` | `"microcompact"` | `"microcompact_threshold"`（对齐 `chat_loop.py:413`） |
| `_maybe_full_compress`（顶部，修改之前） | ~422 | `"auto"` | `"full"` | `"compression_threshold"`（对齐 `chat_loop.py:443`） |
| `_call_llm_with_overflow_recovery` —— API context-overflow 后第一次强制压缩 | ~528 | `"auto"` | `"full"` | `"api_overflow"`（对齐 `chat_loop.py:536`） |
| `_call_llm_with_overflow_recovery` —— 第二次连续 overflow 后的 minimal-history 截断 | ~557（`agent.messages = agent.messages[-2:]`） | `"auto"` | `"minimal_history"` | `"api_overflow_after_compression"`（对齐 `chat_loop.py:563`） |

minimal-history 这条是失败后兜底：API 刚拒绝了一份新压缩过的上下文，loop 在第三次 LLM 调用前把历史砍到最后 2 条。从 host 视角看它**就是**一次压缩事件（历史要丢），所以 Phase A 在那里也 emit `PreCompact`。`compaction_type="minimal_history"` 这个 discriminator 让 host 能区分常规压缩和应急截断；做取证回放快照的 host 两个都要看。Phase A 是 side-effect-only，从异常 handler 内部 emit 不会引入新的失败模式（hook 崩溃只会被 log 并吞掉）。字段名走 snake_case 以对齐 A3 的 Claude-flat 顶层形态 —— 内部 `_emit_context_compressed` 的实参是 `compression_type`，但发到 hook 的 wire 形态用 `compaction_type`。

### A5. Replay 事件 projection

复用现有 `EventType.PLUGIN_HOOK_FIRED` 通道（`agentao/transport/events.py:39`）。Phase A 只发 `outcome="allow"`（暂无控制语义）。

**Phase A emit payload（最小 schema）。** Phase A 下 `PLUGIN_HOOK_FIRED` 的 on-wire `event.data`：

- **Stop：** `{hook_name: "Stop", outcome: "allow", turn_end_reason: <来自 A4>, at_max_iter: bool, matched_rule_count: int}`。`at_max_iter` 仅在 max-iter 位置为 `True`；`turn_end_reason` 取 `"final_response" | "max_iterations" | "doom_loop"`，与 A4 位置表一一对应。**`matched_rule_count` 是本轮被选入 dispatch 的 `Stop` 规则数** —— 即经 `event` + `is_supported` + `_matches` 过滤后的 `len(dispatcher.select_matching_rules("Stop", payload, agent._plugin_hook_rules))`。**这是「被选数」，不是「实际执行数」**：B2 的 run loop 会在 `blocking_error` / `force_continue` 上短路，所以 3 条选中、第 1 条短路时 `matched_rule_count` 仍报 `3`。若未来 host 需要实际运行数，应另加一个 `executed_rule_count` 字段，**不要**改写本字段语义。`matched_rule_count == 0` 时，**完全不 emit 事件**（避免在没有 Stop hook 的 turn 里制造 replay 噪音）。A6 测试 `test_hooks_stop_no_emit_when_no_stop_rules.py` 钉的就是这个 gate。
- **PreCompact：** `{hook_name: "PreCompact", outcome: "allow", compaction_type: <来自 A4>, trigger: "auto", matched_rule_count: int}`。同样的 `matched_rule_count == 0` no-emit gate。`compaction_type` 取 `"microcompact" | "full" | "minimal_history"`（与 A4 位置表对齐）；`trigger` 在本计划下恒为 `"auto"`。

Phase A → Phase B **只增不改**：Phase B 的 Stop emit dict（B7）在以上 schema 上叠加 `added_context_count` 和 `suppress_output` 以支撑五值 outcome 矩阵；没有字段改名，没有字段删除。PR-1 实现者按上面的 schema 即可满足 A6，**不必**前向引用 B7。

**Emit 归属。** Phase A 在 helper 内部 emit（`_dispatch_stop` / `_dispatch_pre_compact`），因为 `outcome` 恒为 `"allow"`。Phase B（B7）**仅对 Stop** 拆分归属：helper 返回 `StopHookResult`，chat-loop 调用点根据自己的分支语境算出五值之一（`allow | block | continue | continue_at_max_iter | reentry_capped`），由专用 `_emit_stop_hook_fired` helper 负责 emit。PreCompact 在两个 phase 下都留在 helper 内部，因为它没有控制语义。

**可见性范围。** 这是 **transport / replay** 事件，**不**是 host-public 事件。`agentao.host.EventStream` 当前的 discriminated union 只包含 `ToolLifecycleEvent | SubagentLifecycleEvent | PermissionDecisionEvent`（`agentao/host/events.py:53`、`agentao/host/models.py:157`），**不**包含 plugin-hook 事件。订阅 `Agentao.events()` 的 host 不会从本计划获得 `Stop` / `PreCompact` 推送；只有 transport/replay 层（以及读取 transport 队列的测试）能看到。把 plugin-hook 提升为 host public 模型属于另一个 Public-Event-Promotion 工单，明确不在本计划范围内（见「不在范围内」）。

### A6. 测试

**附件归宿（先看这一段）。** `_dispatch_lifecycle` 返回 `list[HookAttachmentRecord]`，但**所有现有调用点都丢弃这个返回值**——见 `agentao/runtime/tool_executor.py::_dispatch_pre_tool_hook`（约 591 行，丢弃返回）和 `agentao/cli/session.py::_dispatch_session_start_hooks`（约 79 行，丢弃返回）。今天没有任何「附件落到 turn」的统一接线。Phase A 沿用这一契约：fire `Stop` / `PreCompact` 的 chat-loop helper **不会**消费附件列表，因此附件仅在 dispatcher 边界可观察（以及通过 transport `PLUGIN_HOOK_FIRED` 的 outcome label 透露 verdict + 计数，但**不**带附件 payload）。把附件透出到 conversation/replay 层是一个跨切面工作，应同时改动所有 lifecycle 事件；强行塞进本计划会撑爆范围，独立追踪为 `PLUGIN_HOOK_ATTACHMENT_PIPELINE_PLAN`（不在本计划范围内）。

新增 `tests/` 文件：

- `test_hook_dispatcher_stop.py` —— 直接调用 `dispatcher.dispatch_stop(...)` 配合命中规则，断言返回的列表里包含一个 `hook_success` 的 `HookAttachmentRecord`。Phase A 下 dispatcher 是附件的权威观察点。
- `test_hooks_stop_event.py` —— 注册一个 `Stop` command hook 进真实 chat turn，断言：subprocess 被调用、transport emit `PLUGIN_HOOK_FIRED`（`hook_name="Stop"`、`outcome="allow"`、**`turn_end_reason="final_response"`**、`at_max_iter=False`）、最终回答未变。`turn_end_reason` 断言守住 B7 的 disambiguation 契约 —— 没有它，未来若 refactor 把字段从 `_emit_stop_hook_fired` emit dict 上拆掉，仪表盘消费方会被悄悄打破（该字段在 transport 通道上的唯一目的就是让跨发射位置的 `outcome="continue"` 可区分）。
- `test_hooks_pre_compact_event.py` —— 触发 microcompact 阈值，断言 hook 在修改 `agent.messages` **之前**触发，不论 hook 结果如何 messages 都仍被压缩（side-effect-only 契约）。
- **`test_hooks_stop_payload_claude_shape.py`** —— 抓取 Stop hook subprocess 实际收到的 stdin JSON，断言顶层 keys **正好**是 `{hook_event_name, session_id, transcript_path, cwd, permission_mode, stop_hook_active, last_assistant_message, turn_end_reason}` 且**没有** `data` key。断言 `last_assistant_message` 与 fixture 中的 `assistant_content` 一致。
- **`test_hooks_pre_compact_payload_claude_shape.py`** —— 同样思路，PreCompact 顶层 keys 正好是 `{hook_event_name, session_id, transcript_path, cwd, permission_mode, trigger, custom_instructions, compaction_type, reason}`，矩阵下每个 emit 位置的 `trigger == "auto"`。
- **`test_hooks_pre_compact_matcher_trigger.py`** —— 注册四条 PreCompact 规则，分别断言四种命中决策：(a) `matcher: {"trigger": "manual"}` **不**触发（字面不匹配，本计划只发出 `"auto"`）；(b) `matcher: {"trigger": "auto"}` 触发；(c) `matcher: {"trigger": "manual|auto"}` 触发（这就是 Claude Code 兼容的关键 case —— alternation regex）；(d) `matcher: {"trigger": ".*"}` 触发。这能证明 matcher 是 regex（`re.fullmatch`）而非现有 glob —— 后两条 case 在 `_glob_match` 下都会失败。
- **`test_hooks_pre_compact_matcher_non_dict_guard.py`** —— 三个子用例。(a) 解析一份 `hooks.json`，其中 `"matcher": "auto"`（字符串而非对象）：断言 parser 发出一条 `PluginWarning`（**不是**裸字符串 —— warnings 列表类型是 `list[PluginWarning]`）命名了违规事件/类型组合，并断言加载后的 `rules` 列表为**空**（规则被**丢弃**，不是用规范化后的 matcher 加载）。(b) 同样但 `"matcher": ["auto"]`（list）：同样期望 —— 发出 `PluginWarning`、规则丢弃。(c) 绕过 parser 直接构造 `ParsedHookRule(matcher="auto")` 并调 `_matches`：断言返回 `False`（运行时 guard 把非 dict 当成不匹配）并发出运行时 warning。(a)(b) 验证解析期丢弃；(c) 验证运行时纵深防御 —— 若将来某条代码绕开 loader 直接构造 `ParsedHookRule` 也不会 `AttributeError`。**关键**：(a)(b) 必须断言规则**没有**加载 —— 本计划早期草稿曾建议把坏 matcher 改写为 `None`，由于 `_matches` 对 `None` 返回 `True`，那会把一条配错的过滤器悄悄变成「匹配一切」的规则。
- **`test_hooks_stop_precompact_reject_prompt_type.py`** —— 喂给 `HookConfigParser.parse_dict` 真实 `hooks.json` 形态（外层事件名 + entry 内的 `"type"` 字段，见 `agentao/plugins/hooks.py:63-78` 的 docstring），每个事件一条：

  ```python
  raw_stop       = {"hooks": {"Stop":       [{"type": "prompt", "prompt": "..."}]}}
  raw_precompact = {"hooks": {"PreCompact": [{"type": "prompt", "prompt": "..."}]}}
  ```

  对每个断言：(a) 返回的 `rules` 列表**为空**（规则在解析期被 drop，**不**加载）；(b) 返回的 `warnings` 列表含一条 `PluginWarning`，其 message **同时**点名违规事件名与违规 hook 类型（A1 引入的按事件拒绝分支，**不是**已有的通用 `"Unknown hook type"` 分支——通过 message 文本可区分）；(c) `field == "hooks"`。再断言：把空规则列表注册到 dispatcher 后 emit `Stop` / `PreCompact`，**没有**任何 subprocess 被调用。

  **纵深防御子用例。** 绕过 parser，直接构造 `ParsedHookRule(event="Stop", hook_type="prompt", ...)`，断言 `rule.is_supported is False`（运行时 `is_supported` 扩展兜住任何未来从 loader 之外构造规则的代码路径）。`event="PreCompact"` 同样断言。这一步钉住矩阵与 A1 都引用的「双层防御（解析期 drop + 运行时谓词）」。

  如果不显式断言**按事件**的 warning 文本，该测试在 A1 旧版（已有通用 `"Unknown hook type"` fallback、本来就会 drop 规则）下也能通过；测试**必须**证明新加的按事件分支真的命中。
- **`test_hooks_stop_no_emit_when_no_stop_rules.py`** —— 钉住 A5 的 Phase A no-emit gate（由 A2 的 `select_matching_rules` 驱动）。三个子用例。(a) `agent._plugin_hook_rules == []`（完全无 plugin 规则）：跑一次自然完成的真实 chat turn；断言**没有任何** `hook_name == "Stop"` 的 `PLUGIN_HOOK_FIRED` 事件被 emit。(b) `agent._plugin_hook_rules == [<UserPromptSubmit 规则>]`（规则非空但没有 Stop 规则）：同样断言 —— 没有 `Stop`-tag 的事件。这个 case 是 `_dispatch_user_prompt_submit:332-333` 的早返回**抓不到**的，所以 Stop gate 必须独立按 `select_matching_rules("Stop", ...)` 过滤一次。(c) 正向对照 —— `agent._plugin_hook_rules == [<Stop 规则>]`：断言**正好一条** `hook_name == "Stop"`、`outcome == "allow"`、`matched_rule_count == 1`、`turn_end_reason == "final_response"` 的 `PLUGIN_HOOK_FIRED`。(c) 中 `matched_rule_count == 1` 这一断言是为了堵住未来 refactor 改用 `len(attachments)` 的可能（clean-exit 0 的 hook 在那个特例下也等于 1，但其它一切情况都会偏 —— 见 A2「为什么不用 `len(attachments)`」）。本测试在 PR-1 落地；B7 在 Phase B 下**不需要修改**就能复用，因为 `select_matching_rules`（helper 侧）与 `StopHookResult.matched_rule_count`（dispatcher 侧，B1）报的是同一个数。
- **`test_hooks_pre_compact_no_emit_when_no_rules.py`** —— PreCompact 的对称 gate 测试（A5 的 no-emit 子句同样适用）。三个子用例。(a) 完全无 plugin 规则，强行触发 microcompact：断言**零**个 `hook_name == "PreCompact"` 的 `PLUGIN_HOOK_FIRED`。(b) 规则非空但都不是 PreCompact（比如 Stop 规则）：同上。(c) 正向对照 —— `[<PreCompact 规则>]`：断言**正好一条** `hook_name == "PreCompact"`、`outcome == "allow"`、`matched_rule_count == 1`、`compaction_type == "microcompact"`、`trigger == "auto"`。PreCompact 需要单独的 gate 测试，因为 A6 现有的 `test_hooks_pre_compact_event.py` 无条件注册规则、从不走「空 matched 规则」分支。同样守住 attachment-count 退化风险。
- 已有的 `test_hook_dispatcher.py`（如存在）补充 `Stop` / `PreCompact` 规则匹配用例。

### A7. 文档

- `docs/implementation/plugin-system-mvp/PHASE_6_SESSION_TOOL_HOOKS_AND_CLI.md` —— 在支持事件列表中追加新增两个事件。
- `docs/CONFIGURATION.md` —— 若枚举了 hook 事件清单，同步更新。
- `CLAUDE.md`（仓库根）—— 仅当其中也枚举了 hook 事件清单时才更新。

---

## Phase B —— Control-aware gate

### B1. 新结果模型

`agentao/plugins/models.py`：

```python
@dataclass
class StopHookResult:
    """单次 Stop 事件聚合后的所有 hook 结果。

    字段语义有意与 UserPromptSubmitResult 不同：
    - blocking_error：形态相同——把 hook 失败抛给用户。
    - force_continue：为 True 时，loop 拒绝结束当前回合；
      `follow_up_message` 作为用户消息追加进会话，loop 再发
      一次 LLM 调用。
    - additional_contexts：作为 system-reminder 附加在助手最终
      回答后（罕见，可观察用例）。
    """
    # --- 真正被 chat-loop 接线消费的 Stop 语义字段 ---
    blocking_error: str | None = None
    force_continue: bool = False
    follow_up_message: str | None = None
    additional_contexts: list[str] = field(default_factory=list)
    stop_reason: str | None = None
    # Claude Code output 对齐字段（见兼容性矩阵）。
    # `suppress_output`：双重语义。
    #   - Claude parity 部分：把 hook 原始 stdout 从任何
    #     user-visible / debug-log 通道隐藏。**在 Agentao 当下这是
    #     真空兑现**——hook stdout 从未被投影到 `PLUGIN_HOOK_FIRED`
    #     （emit 只携带 verdict + 计数：`outcome`、`matched_rule_count`、
    #     `added_context_count`、`suppress_output` 等，见 B7 helper），
    #     chat-loop 也不把 hook stdout 渲染进 user-visible transcript。
    #     字段仍然如实记录在 result 上，并发到
    #     `PLUGIN_HOOK_FIRED.suppress_output` 供 replay 保真，
    #     但当前没有任何展示路径会消费它。如果未来 Agentao 出现
    #     surfacing hook stdout 的展示通道，那个通道**必须**查询
    #     `suppress_output` 才能保持 Claude 兼容。矩阵行
    #     「JSON `suppressOutput`」标 🟡 正是因为这种「当前真空、
    #     但忠实记录」的姿态。
    #   - Agentao 扩展（矩阵中 🟡，**不是** 严格 Claude parity）：
    #     `True` 时 B3 还会跳过把 `<stop-hook>...</stop-hook>` 回显
    #     `additional_contexts` 到助手最终回答。上下文仍写到 transport
    #     `PLUGIN_HOOK_FIRED.added_context_count` 让 replay 不丢统计，
    #     但 user-visible 答复保持干净。
    #   要严格 Claude 语义（suppressOutput 只影响 stdout、不影响
    #   additionalContext 回显）的 host：不要把这个 flag 与
    #   `additionalContext` 写在同一份 hook output 上 —— 拆开。
    # `system_message`：从 JSON `systemMessage` 读出。同时也会被
    #     append 到 `additional_contexts`（与该字段共享通道，见 B2）；
    #     单独保留这个字段是为了 replay 保真。
    suppress_output: bool = False
    system_message: str | None = None

    # --- runner 内部 scratch / 旧字段容忍 ---
    # B2 fork 了 Stop 专用 runner（`_run_stop_command_hook`）和
    # 专用 parser（`_parse_stop_command_output`）；StopHookResult
    # **不**与 `_run_command_hook` / `_parse_command_output` 共享代码。
    # 下面两个字段承载 runner 内部状态，chat-loop 接线（B3）不直接读。
    #
    # `messages`：`_run_stop_command_hook` 产生的 HookAttachmentRecord
    #     列表（timeout warning、exit-2 附件、其它 nonzero warning、
    #     JSON 路径下的 "hook_success" 等）并返回给 dispatcher。
    #     按 A6 附件 caveat，今天 dispatcher 边界是唯一观察点。
    # `prevent_continuation`：parser 写入 Agentao 内部 legacy
    #     `preventContinuation: true` JSON 字段的目标位 —— 仅用于
    #     让按 UserPromptSubmit 形态写的 hook 脚本不至于把 Stop runner
    #     弄崩。B2 parser 表把 `preventContinuation: true` 翻译为
    #     `force_continue=True`（受 `continue: false` 优先级规则节制）；
    #     scratch 字段本身**不**被 chat-loop 接线消费。
    messages: list[HookAttachmentRecord] = field(default_factory=list)
    prevent_continuation: bool = False

    # --- replay 发射 gate ---
    # `matched_rule_count`：本次 dispatch 中**被选入**的 Stop 规则数
    #     （B2 `dispatch_stop` 设值，等于
    #     `len(self.select_matching_rules("Stop", payload, rules))`）。
    #     这是**「被选数」**，不是「执行数」：`dispatch_stop` 的
    #     run loop 会在 `blocking_error` / `force_continue` 上短路，
    #     所以 3 条规则被选中、第 1 条短路时这里仍记 `3`。字段名保留
    #     不改（不重命名为 `selected_rule_count`）以保 replay 流的
    #     向后兼容；若未来 host 需要实际运行数，应另加 sibling
    #     `executed_rule_count`，而**不是**改写本字段语义。B7 的
    #     `_emit_stop_hook_fired` 依据这个字段判定是否发射 —— 为 0
    #     就**不发** `PLUGIN_HOOK_FIRED`。形态对齐
    #     `_dispatch_user_prompt_submit` 在 `chat_loop.py:332-333` 的
    #     「`agent._plugin_hook_rules` 为空就早返回」，但额外覆盖了
    #     「规则非空但没有任何条针对 Stop」这种情况（我们不希望从一个
    #     选中 0 条 Stop hook 的回合发出 `hook_name="Stop",
    #     outcome="allow"` —— 那既吵又语义错乱）。
    matched_rule_count: int = 0
```

命名说明：用 `force_continue`（不用 `prevent_continuation`）作为**有意义**的字段，原因——`Stop` 是**当 loop 即将结束时**触发，host 想要的是**阻止结束**，即"强制继续"。复用 `prevent_continuation` 作为有意义信号会让极性反转（在 `UserPromptSubmitResult` 里它表示"阻止开始"），并且与 Claude Code 的 `{"continue": false}` JSON 约定语义错位。上面的 scratch `prevent_continuation` 仅用于吸收一份配错 hook 脚本写下的 `preventContinuation: true` —— **从不**被 chat-loop 接线消费；B2 把 parser 的 `preventContinuation` 写入翻译为 `force_continue`（受 B2 不变量中钉死的 `continue: false` 优先级规则节制）。

### B2. `Stop` 的 control-aware dispatcher —— Stop 专用 runner，对齐 Claude Code 语义

上一稿建议复用 `_run_command_hook` 与 `_parse_command_output`（UserPromptSubmit 的 runner）。**这条路径不能给 Claude Code 兼容**——Claude Code 在 Stop 上对 exit code 2 的定义是「block this stop 并把 stderr 反馈作 follow-up reason」，而现有 runner 对 nonzero + 空 stdout 只写一条 warning attachment（`hooks.py:520-533`）。复用就等于把 Claude Stop hook 最常见的控制信号悄悄丢掉。

**单独 fork 一个 Stop 专用 runner。** `dispatch_stop` 调用 `_run_stop_command_hook`，**不**调用 `_run_command_hook`：

```python
def dispatch_stop(self, *, payload, rules) -> StopHookResult:
    result = StopHookResult()
    # 复用 A2 的 select_matching_rules，让 helper 侧与 dispatcher 侧
    # 的计数来自同一过滤式（event + is_supported + _matches）。对已
    # 过滤的输入是幂等的 —— B7 helper 已预先过滤、这一步成 no-op；
    # 对直接调用方（如 test_hook_dispatcher_stop.py）是唯一的过滤
    # 关卡。
    stop_rules = self.select_matching_rules("Stop", payload, rules)
    # 在 run loop **之前**就把 replay gate 字段设好——这样它记的是
    # **「被选数」**，不是「执行数」：下面的循环会在 blocking_error /
    # force_continue 上 break 出来，但本字段仍报完整的被选总数（见
    # B1 docstring 的契约 —— 被选数 ≠ 运行数是有意设计；若未来 host
    # 需要后者，应另加 `executed_rule_count`）。
    result.matched_rule_count = len(stop_rules)
    for rule in stop_rules:
        if rule.hook_type == "command":
            self._run_stop_command_hook(rule, payload, result)
        # 本阶段 Stop 不支持 prompt-type hook
        if result.blocking_error or result.force_continue:
            break
    return result

def _run_stop_command_hook(
    self, rule, payload, result: StopHookResult,
) -> None:
    """Stop 专用 runner —— 对齐 Claude Code exit code 2 + JSON 契约。"""
    if not rule.command:
        return
    payload_json = json.dumps(payload)
    try:
        proc = subprocess.run(
            rule.command, input=payload_json, capture_output=True, text=True,
            timeout=rule.timeout, shell=True, cwd=str(self._cwd),
        )
    except subprocess.TimeoutExpired:
        result.messages.append(_make_attachment(
            "hook_success", {"warning": f"Hook timed out after {rule.timeout}s"},
            hook_name=rule.command, hook_event="Stop",
        ))
        return
    except OSError as exc:
        logger.warning("Stop hook failed to run: %s (%s)", rule.command, exc)
        return

    # Exit code 2 —— Claude Code blocking 信号，stderr 是 reason。
    if proc.returncode == 2:
        stderr = (proc.stderr or "").strip() or "Stop hook blocked via exit 2"
        result.force_continue = True
        result.follow_up_message = stderr
        result.stop_reason = stderr
        result.messages.append(_make_attachment(
            "hook_stop_blocked_via_exit2",
            {"stderr": stderr[:500]},
            hook_name=rule.command, hook_event="Stop",
        ))
        return

    # 其它 nonzero + 空 stdout —— 沿用现有 runner 的 warning 行为，
    # 不当控制信号。
    if proc.returncode != 0 and not (proc.stdout or "").strip():
        result.messages.append(_make_attachment(
            "hook_success",
            {"warning": f"Hook exited with code {proc.returncode}",
             "stderr": (proc.stderr or "")[:500]},
            hook_name=rule.command, hook_event="Stop",
        ))
        return

    # JSON 路径 —— Claude Code Stop output schema。
    self._parse_stop_command_output(proc.stdout, rule, result)
```

**`_parse_stop_command_output` —— Claude Code Stop JSON 契约。** 上面的 runner 把 stdout 交给一个 Stop 专用 parser。它是 `_parse_command_output` 去掉 UserPromptSubmit-only 分支的近副本，再加上 Stop 兑现的 Claude output 字段。**parser 必须按下表的顺序求值** —— Claude Code 文档明确 `continue: false` 优先于任何事件专属 decision 字段，所以 hook 返回 `{"continue": false, "decision": "block"}` 是**接受 stop**，**不**触发继续。

| 顺序 | hook stdout 上的 JSON 字段 | StopHookResult 映射 |
|---|---|---|
| 1 | `continue: false` | **优先级覆盖。** 在 parser scratch 状态上置一个 `continue_false` 标志，并对所有后续会写 `force_continue` 的分支**清除/拒绝**写入（即下面的 `decision: "block"` 与 `preventContinuation: true` 在 `continue_false` 情况下变成 no-op，但仍记录 attachment）。结果：Stop 被遵守 —— 与默认 Stop 语义相同。replay 仍记录字段到达，但 loop 本来就要结束本回合。 |
| 2 | `continue: true`（默认/缺省） | no-op |
| 3 | `decision: "block"` + `reason: "<text>"` | 若未置 `continue_false`：`force_continue=True`、`follow_up_message="<text>"`、`stop_reason="<text>"`。若已置 `continue_false`：只记录 `stop_reason="<text>"`（供 replay）、跳过 `force_continue`。 |
| 4 | `stopReason: "<text>"` | `stop_reason="<text>"`（无 `decision: "block"` 时被 `force_continue` follow-up 合成器使用）；与 `continue_false` 无关。 |
| 5 | `suppressOutput: true` | `suppress_output=True` |
| 6 | `systemMessage: "<text>"` | `system_message="<text>"`，并 append 到 `additional_contexts` |
| 7 | `hookSpecificOutput.additionalContext: <str\|list>` | 追加到 `additional_contexts`（Claude Code 文档化的 Stop 上下文通道） |
| 8 | `additionalContext: <str\|list>`（legacy / 顶层） | 追加到 `additional_contexts`（容忍旧脚本） |
| 9 | `blockingError: "<text>"` | `blocking_error="<text>"`（Agentao 内部字段；不在 Claude schema，但保留供已经写过的 UserPromptSubmit 风格脚本对齐）。与 `continue_false` 无关 —— `blocking_error` 以显式错误消息结束本回合，与 `continue: false` 的「停」语义一致。 |
| 10 | `preventContinuation: true` | 若未置 `continue_false`：`force_continue=True`、`stop_reason=data.get("stopReason", "Hook prevented continuation")`、`follow_up_message=data.get("stopReason") or "Stop hook requested continuation"`（Agentao 内部字段；Stop 上是病态情形）。若已置 `continue_false`：跳过 `force_continue`。 |
| 任意路径 | `messages.append(_make_attachment(...))` |

**不变量：**
1. 任何把 `force_continue=True` 写下的分支都必须同时产出非空 `follow_up_message`（hook 没给的话，从 `stopReason` 合成）。B3 在使用点也用 `stop_reason` 和通用兜底再托底一次，两侧都把契约钉住。
2. exit code 2 **永远**翻译为 `force_continue` —— 这是 Claude Code 契约，也是这个 runner 必须独立于 `_run_command_hook` 的第一个理由。注意 exit code 2 在 JSON parser 运行**之前**判定，所以 stdout 中的 `continue: false` 不能反制它。Claude Code 文档化的优先级也是这样（exit code 优先于 JSON output）。
3. **`continue: false` 覆盖同一份 hook output 中任何会产生 `force_continue` 的字段。** 这是第 1 行的优先级规则，也是这个 runner 必须独立的第三个理由。想让 agent 不管其它字段都停下的 hook，写 `{"continue": false}`。
4. Parser **不**重新实现 UserPromptSubmit-only 分支（`prevent_continuation` 作为有意义信号、`additional_contexts` 注入下条 user prompt）。`StopHookResult` 上的 scratch `prevent_continuation` 字段（B1）只是为了让一份配错事件的 hook 脚本不至于把 runner 弄崩。

**`_run_command_hook` 不动。** UserPromptSubmit hook 继续用它，语义不变。

**测试影响 —— A6 的 `test_hook_dispatcher_stop.py` 必须在 PR-2 中更新。** A2 把 `dispatch_stop` 声明为返回 `list[HookAttachmentRecord]`，A6 写了一条测试调 `dispatcher.dispatch_stop(...)` 并断言返回的 list 含 `hook_success` 记录。B2 升级签名为 `StopHookResult` 后，那条断言不再编译。PR-2 补丁：

1. 测试改用新签名调用：`result = dispatcher.dispatch_stop(...)`（现在是 `StopHookResult`）。
2. 附件断言改 walk `result.messages` 而不是裸 list（`StopHookResult.messages` 是 B1 中定义的 parser-shared scratch 字段，承载与 Phase-A list 同样的 `HookAttachmentRecord` payload）。
3. 新增针对 gate 信号字段（`force_continue`、`blocking_error`、`suppress_output` 等 Phase-A 不存在的字段）的子用例。

这是 PR-1 与 PR-2 之间**有意为之的破坏性变更** —— `dispatch_stop` 是 Agentao 内部 API（目前没有 host 直接调用），破坏面被局限在内部。Sequencing 中的 PR-2 条目把这条测试重写显式列出，避免 PR-2 评审时被遗漏。`dispatch_pre_compact` **不**受影响 —— PreCompact 仍 observe-only（B5），PR-2 之后仍返回 `list[HookAttachmentRecord]`。

### B3. chat-loop 接线

现有 finalization 在 `chat_loop.py:294-306` 处先 `agent.messages.append(final_msg)` 再 `return assistant_content`。如果在 append 之后再 dispatch Stop，hook 返回 `blocking_error` 时会出现「被阻止但已落历史」的局面——用户看到 `[Blocked by Stop hook] ...`，但原始回答仍留在 transcript 中、并且会在下一回合再喂给模型。

修法：先构造 `final_msg`，dispatch Stop，再决定提交什么。把裸 `return` 与 `agent.messages.append(final_msg)` 同时改成：

```python
else:
    agent.llm.logger.info(f"Reached final response in iteration {iteration}")
    assistant_content = assistant_message.content or ""
    reasoning_content = getattr(assistant_message, "reasoning_content", None)
    final_msg: Dict[str, Any] = {"role": "assistant", "content": assistant_content}
    _attach_reasoning(final_msg, reasoning_content)
    if sanitize_assistant_message(final_msg):
        agent.llm.logger.warning(
            "Sanitised lone surrogates in final assistant message "
            "(iteration %d)", iteration,
        )

    # 注意：此刻**不要** append final_msg —— Stop hook 可能改写其
    # content（blocking_error）或延长本回合（force_continue）。
    # `_dispatch_stop` 内部自己构造 payload（assistant_content →
    # `last_assistant_message`；`turn_end_reason` → Claude-flat 顶层
    # key）；**调用方不传 pre-built dict**——签名与边界 rationale 见 B7。
    # 调用方负责在分支判断之后用正确的 outcome label 发 PLUGIN_HOOK_FIRED。
    stop_result = self._dispatch_stop(
        agent, assistant_content,
        turn_end_reason="final_response", at_max_iter=False,
    )

    if stop_result.blocking_error:
        blocked = f"[Blocked by Stop hook] {stop_result.blocking_error}"
        final_msg["content"] = blocked
        agent.messages.append(final_msg)
        self._emit_stop_hook_fired(
            agent, outcome="block", at_max_iter=False,
            turn_end_reason="final_response",
            stop_result=stop_result,
        )
        return blocked

    if stop_result.force_continue:
        # 防御 follow_up_message 为空的翻译路径（例如 B2 合成之前的
        # preventContinuation 路径）。force_continue 是权威信号，
        # 注入文本由优先有内容的字段合成，最后兜底通用字符串。
        follow_up = (
            stop_result.follow_up_message
            or stop_result.stop_reason
            or "Stop hook requested continuation"
        )
        if self._stop_reentries >= self._stop_reentry_cap:
            agent.llm.logger.warning(
                "Stop hook reentry cap (%d) hit; ending turn.",
                self._stop_reentry_cap,
            )
            agent.messages.append(final_msg)
            self._emit_stop_hook_fired(
                agent, outcome="reentry_capped", at_max_iter=False,
                turn_end_reason="final_response",
                stop_result=stop_result,
            )
            return assistant_content
        self._stop_reentries += 1
        agent.messages.append(final_msg)  # 保留被「继续」前的那条回答
        agent.messages.append({
            "role": "user",
            "content": f"<system-reminder>Stop hook injected this</system-reminder>\n"
                       f"{follow_up}",
        })
        messages_with_system = [
            {"role": "system", "content": system_prompt}
        ] + agent.messages
        iteration = 0  # 新子回合的诚实预算重置
        self._emit_stop_hook_fired(
            agent, outcome="continue", at_max_iter=False,
            turn_end_reason="final_response",
            stop_result=stop_result,
        )
        continue

    # Allow 路径。可选 additional_contexts 以 system-reminder 形式
    # 拼到助手最终回答之后，让记录与用户看到的一致 —— **除非** hook
    # 返回了 `suppressOutput: true`，那种情况下上下文仍然会写到
    # transport `PLUGIN_HOOK_FIRED.added_context_count` 供 replay 计数，
    # 但 user-visible 答复保持干净。
    #
    # 注意：这条 gate 是 **Agentao 自家对 suppressOutput 的扩展**，
    # 不是 Claude parity。Claude 文档下的 `suppressOutput` 仅指
    # stdout/debug-log 隐藏；结构化的 `hookSpecificOutput.additionalContext`
    # 是另一条通道，在 Claude 中不受这个 flag 影响。我们在这里扩展语义
    # 是因为「审计 hook 附 replay note 但不想污染答复」用例真实存在，
    # 另起一个 flag 会无谓增加配置面。详见 B1 docstring 与矩阵 🟡 行
    # 「Agentao 对 suppressOutput 的扩展」。
    if stop_result.additional_contexts and not stop_result.suppress_output:
        extra = "\n".join(
            f"<stop-hook>\n{ctx}\n</stop-hook>"
            for ctx in stop_result.additional_contexts
        )
        final_msg["content"] = f"{assistant_content}\n{extra}"
        assistant_content = final_msg["content"]
    agent.messages.append(final_msg)
    self._emit_stop_hook_fired(
        agent, outcome="allow", at_max_iter=False,
        turn_end_reason="final_response",
        stop_result=stop_result,
    )
    return assistant_content
```

下面的 max-iterations allow 路径**有意**不在 `final_msg_max` 上回显 `additional_contexts` —— 在迭代上限处 user-visible 的答复就是助手最后一段部分输出（或 `"Maximum tool call iterations reached."`），用 hook 上下文装饰它不大可能是合适的 UX。如果将来有 host 明确需要在 max-iter 也回显，必须以 `not stop_result.suppress_output` 加 gate，与上面自然 turn 路径保持一致。

**max-iterations dispatch 位置（钉死）。** post-while finalization（`chat_loop.py:308-324`）**不**适合做 Stop dispatch——`force_continue` 在 `while True` 之外没办法重新进 loop（除非动结构）。把 dispatch 钉死在 **`chat_loop.py:185-186` 处 `iteration >= max_iterations` 块的 `else: # "stop"` 分支内**，替换那个裸 `break`：

```python
if iteration >= max_iterations:
    pending = [...]
    _handler = getattr(agent.transport, "on_max_iterations", None)
    result = _handler(max_iterations, pending) if callable(_handler) else {"action": "stop"}
    action = result.get("action", "stop")
    if action == "continue":
        iteration = 0
    elif action == "new_instruction":
        ...
    else:  # "stop"
        # 在这里就把 max-iter 的 final_msg 建出来，让 Stop 在
        # commit 历史前先 dispatch。形态对齐自然 turn 的路径。
        assistant_content_max = (
            assistant_message.content if assistant_message else None
        ) or "Maximum tool call iterations reached."
        final_msg_max: Dict[str, Any] = {
            "role": "assistant", "content": assistant_content_max,
        }
        _attach_reasoning(
            final_msg_max,
            getattr(assistant_message, "reasoning_content", None) if assistant_message else None,
        )
        sanitize_assistant_message(final_msg_max)

        stop_result = self._dispatch_stop(
            agent, assistant_content_max,
            turn_end_reason="max_iterations", at_max_iter=True,
        )

        if stop_result.blocking_error:
            blocked = f"[Blocked by Stop hook] {stop_result.blocking_error}"
            final_msg_max["content"] = blocked
            agent.messages.append(final_msg_max)
            self._emit_stop_hook_fired(
                agent, outcome="block", at_max_iter=True,
                turn_end_reason="max_iterations",
                stop_result=stop_result,
            )
            return blocked

        if stop_result.force_continue:
            # cap-check **先做**——与自然 turn 路径对称。没有这条
            # 显式分支，max-iter cap-hit 会悄悄落到下面的 allow 路径
            # 并发 outcome="allow"，等于把病态 hook 静默掩盖（同 B2
            # 之前 exit code 2 被悄悄降级的故事）。
            if self._stop_reentries >= self._stop_reentry_cap:
                agent.llm.logger.warning(
                    "Stop hook reentry cap (%d) hit at max-iterations; ending turn.",
                    self._stop_reentry_cap,
                )
                agent.messages.append(final_msg_max)
                self._emit_stop_hook_fired(
                    agent, outcome="reentry_capped", at_max_iter=True,
                    turn_end_reason="max_iterations",
                    stop_result=stop_result,
                )
                return assistant_content_max
            follow_up = (
                stop_result.follow_up_message
                or stop_result.stop_reason
                or "Stop hook requested continuation"
            )
            self._stop_reentries += 1
            agent.llm.logger.warning(
                "Stop hook force_continue at max-iterations; "
                "resetting iteration counter (outcome=continue_at_max_iter)."
            )
            agent.messages.append(final_msg_max)
            agent.messages.append({
                "role": "user",
                "content": (
                    f"<system-reminder>Stop hook injected this</system-reminder>\n"
                    f"{follow_up}"
                ),
            })
            messages_with_system = [
                {"role": "system", "content": system_prompt}
            ] + agent.messages
            iteration = 0
            self._emit_stop_hook_fired(
                agent, outcome="continue_at_max_iter", at_max_iter=True,
                turn_end_reason="max_iterations",
                stop_result=stop_result,
            )
            continue

        # Allow 路径（无 force_continue）。max-iter **有意**不在
        # final_msg_max 上回显 additional_contexts —— 理由见自然 turn
        # 段后的散文说明。
        agent.messages.append(final_msg_max)
        self._emit_stop_hook_fired(
            agent, outcome="allow", at_max_iter=True,
            turn_end_reason="max_iterations",
            stop_result=stop_result,
        )
        return assistant_content_max
```

**doom-loop dispatch 位置（第三个出口，钉死）。** `ToolRunner.execute(...)` 返回 `(doom_loop_triggered, tool_results)`；`doom_loop_triggered` 为 True 时，chat loop 当前是 `if doom_triggered: break`（`chat_loop.py:271-272`）然后落到 post-while finalization。评审十五轮之前的 B3 草稿漏了这第三个出口；PR-2 删 post-while 时 doom-loop 返回路径会失踪。把裸 `break` 替换为内联 Stop dispatch，形态对照自然 turn 块、做三处替换：

```python
agent.messages.extend(tool_results)
if doom_triggered:
    # 在这里就地构造 final_msg（mirrors max-iter pattern）。
    # 此时 assistant_message 是产生违规 tool_calls 的那条，
    # content 通常是空，所以兜底字符串承担 user-facing 信息。
    assistant_content_doom = (
        assistant_message.content if assistant_message else None
    ) or "Tool execution halted by doom-loop detection."
    final_msg_doom: Dict[str, Any] = {
        "role": "assistant", "content": assistant_content_doom,
    }
    _attach_reasoning(
        final_msg_doom,
        getattr(assistant_message, "reasoning_content", None) if assistant_message else None,
    )
    sanitize_assistant_message(final_msg_doom)

    stop_result = self._dispatch_stop(
        agent, assistant_content_doom,
        turn_end_reason="doom_loop", at_max_iter=False,
    )

    # 分支结构与上面自然 turn 块**完全对称**（block / cap-hit /
    # continue / allow），做以下替换：
    #   - `assistant_content` → `assistant_content_doom`
    #   - `final_msg` → `final_msg_doom`
    #   - `at_max_iter=False`（**不**是 max-iter；outcome 用
    #     `continue`，**不是** `continue_at_max_iter`）
    #   - **四处 `_emit_stop_hook_fired(...)` 都传
    #     `turn_end_reason="doom_loop"`** —— 这是仪表盘解析
    #     PLUGIN_HOOK_FIRED 时区分「doom-arm 的 continue」与
    #     「自然 turn 的 continue」的依据（见 B7 outcome 表）
    #   - cap-hit 的 WARNING 文案点名「doom-loop」而不是自然 turn
    #     的措辞，方便 triage
    # 四个 `_emit_stop_hook_fired(...)` 调用点齐全 ——
    # block / reentry_capped / continue / allow，与自然 turn 一致。
    #
    # **doom 计数器重置说明**：在 doom 位置兑现 force_continue 时，
    # **不**重置 ToolRunner 的 doom-loop 计数器 —— 那个计数器属于
    # ToolRunner planner 自己的状态，本计划无权擅改；而且若 host
    # 坚持继续而模型仍在异常，再次撞 doom 是合理结果（re-entry
    # 封顶最终会终止）。
    ...  # 此处略去完整代码块；按上面替换照抄自然 turn 形态即可，
         # B6 新增回归测试钉住正确性
```

如果未来 refactor 抽取 `_finalize_with_stop_hook(...)` 把三处共通结构吸收进去，那是 Phase-B 实现层面的清理，不是规范变化 —— 契约保持「三个位置、相同的 StopHookResult 处理、不同的 `turn_end_reason`」。

**结果——死代码删除。** 改完之后，`while True` 的**全部三**个出口（自然 final-response `return`、max-iter `else: # "stop"` 分支 `return`、doom-loop `if doom_triggered:` 分支 `return`）都在 loop body 内终止，post-while finalization（`chat_loop.py:308-324`）变成不可达。PR-2 顺手删除那段。loop 三个生还出口：(1) 自然 turn 的 `return assistant_content`；(2) max-iter `else: # "stop"` 分支里新加的 `return assistant_content_max`；(3) doom-loop `if doom_triggered:` 分支里新加的 `return assistant_content_doom`。

### B4. 重入封顶

在 chat-loop 实例上加两个字段，每次 `chat()` 调用时重置：

- `_stop_reentries: int` —— 计数器，初值 0。
- `_stop_reentry_cap: int` —— **chat loop 的构造参数，硬编码默认值为 `3`**。Phase B **不**从 `.agentao/settings.json` 读取这个值。该文件目前只有两个读者（`embedding/factory.py::_load_settings`、`plan/controller.py::_load_settings`，见 `docs/CONFIGURATION.md` §3）；为一个真实 host 撞封顶之前没人会调的旋钮再加第三个 one-key reader，是过早暴露配置面。如果将来某 host 明确要求运行时调参，再把字段提升为 `stop_hook_reentry_max` settings key、走那时已有的统一 settings loader。

封顶触发时：emit 一个 `PLUGIN_HOOK_FIRED` transport 事件，`outcome="reentry_capped"`（新 label），并 log 一条 `WARNING`。**不**写附件——见 A6 的附件 caveat，本计划不引入新的附件写入路径。

### B5. PreCompact gate —— **Claude Code 兼容性 gap，明确不在范围内**

Claude Code 的 PreCompact 支持 exit code 2 与 JSON `decision: "block"` 来阻止压缩。**本计划不实现这一点。** PreCompact 仍走 `_dispatch_lifecycle`（side-effect-only）—— 与 Phase A 一致的 dispatcher 路径。兼容性矩阵中标 ❌ 的 PreCompact blocking 两行就是指这件事；本节给出理由。

理由：

- `_maybe_microcompact` 与 `_maybe_full_compress` 直接 in-place 修改 `agent.messages`；没有「跳过压缩」分支，周边 overflow-recovery 代码（`_call_llm_with_overflow_recovery`，`chat_loop.py:525-578`）假设压缩最终成功。minimal-history 路径（`chat_loop.py:557`）本身就是常规压缩失败后的兜底。
- 接受 host 「拒绝」却没有「host 拒绝且仍然超长」的恢复分支是不安全的：下一次 LLM 调用会再次触发同样的 overflow，要么死循环，要么仍然走到本来就会触发的 minimal-history 截断。
- 压缩代码路径对异常恢复敏感（部分跑在 `except` 块内）；把 host 控的 gate 塞进异常处理本身就是另一场设计讨论。

这是**有意保留的兼容性 gap**，不是 roadmap 的「下一步」。一份用 `decision: "block"` 的 Claude Stop hook 在 Agentao 里能工作；同样模式的 Claude PreCompact hook 会被观察到，但 block 决策会被丢弃。host 不能在没有显式验证的前提下假设 Claude PreCompact 脚本在 Agentao 里有 gate。

如果某 host 真的需要 PreCompact gate，相关工作放进单独计划（`PRECOMPACT_GATE_PLAN.md`），先把「host 拒绝、仍然超长」的恢复路径解决了再做。本计划范围外。

### B6. 测试

兼容性矩阵给 `suppressOutput`、`systemMessage`、`hookSpecificOutput.additionalContext`、exit code 2 都标了 ✅；评审五轮正确指出这些声明需要专门的测试覆盖。下面每一项 ✅ 至少对应一条测试。

- `test_hooks_stop_force_continue_decision_block.py` —— Stop hook stdout `{"decision": "block", "reason": "needs more work"}`；断言 chat loop 追加 `follow_up_message`（带 system-reminder 前缀）、再发一次 LLM 调用、用户能看到结果。**额外**断言对应的 transport `PLUGIN_HOOK_FIRED` 事件携带 `outcome="continue"`、**`turn_end_reason="final_response"`**、`at_max_iter=False` —— 这两条加在一起钉死 B7 outcome 矩阵中自然 turn 的 `(turn_end_reason, outcome)` 对，防止未来 refactor 把自然 turn 的 `continue` 悄悄路由到 doom-loop 或 max-iter 发射通道。对应矩阵行「JSON `decision: \"block\"`（Stop）」。
- `test_hooks_stop_blocking_error.py` —— Stop hook 返回 `blockingError`；断言最终回答是 block 消息、不再发 LLM 调用。对应 Agentao 内部 blockingError 的容忍分支。
- `test_hooks_stop_reentry_cap.py` —— 病态 hook 始终 `force_continue`；断言封顶生效、transport 上能看到 `reentry_capped` 事件（`hook_name="Stop"`、`outcome="reentry_capped"`、**`turn_end_reason="final_response"`** —— 本测试从自然 turn 路径触发封顶；max-iter 与 doom-loop 的 cap-hit 在 `test_hooks_stop_doom_loop_dispatch.py` 与下面新增的 max-iter 测试里钉）、loop 终止。
- **`test_hooks_stop_hook_active_reentry.py`** —— 注册一条 Stop hook，记录每次调用 stdin payload 中的 `stop_hook_active` 值；首次 dispatch 返回 `force_continue`，第二次接受 stop。断言：(a) 第一次 dispatch 的 payload `stop_hook_active == False`；(b) 第二次 dispatch 的 payload `stop_hook_active == True`；(c) 计数器重置后，新一次 `chat()` 调用的第一次 dispatch 又回到 `False`。这条测试钉死 `stop_hook_active = (self._stop_reentries > 0)` 接线与「每个 `chat()` 调用重置」的语义。没有这条测试，A3 中 `stop_hook_active` 字段的声明（矩阵 ✅）就没有保险 —— 评审九轮正确指出之前的 B6 只校验 key 存在。
- **`test_hooks_stop_exit_code_2.py`** —— Stop hook 脚本 `exit 2` 且 stderr 为 `"please retry"`；断言 `force_continue=True`、`follow_up_message="please retry"`、并且 `<system-reminder>Stop hook injected this</system-reminder>\nplease retry` 这条 user message 被追加。**额外**断言 transport `PLUGIN_HOOK_FIRED` 事件携带 `outcome="continue"`、**`turn_end_reason="final_response"`**（本测试从自然 turn 路径触发）、`at_max_iter=False`。这是 Claude Code Stop 最常见的控制信号，旧稿因复用 runner 把它降级成了 warning。对应矩阵行「Exit code 2 —— Stop」。
- **`test_hooks_stop_suppress_output.py`** —— Stop hook 返回 `{"hookSpecificOutput": {"additionalContext": "audit-note"}, "suppressOutput": true}`；断言 `final_msg["content"]` **保持不变**（不追加 `<stop-hook>` 块），但 transport `PLUGIN_HOOK_FIRED` 事件仍记录 `added_context_count == 1`。配套负向测试：`"suppressOutput": false`（或省略）时，`<stop-hook>` 块**会**被追加。对应矩阵行「JSON `suppressOutput`」+ B1 的 `suppress_output` 字段 + B3 接线新加的 guard。
- **`test_hooks_stop_system_message.py`** —— Stop hook 返回 `{"systemMessage": "ran lint, all clean"}`；断言 `result.system_message == "ran lint, all clean"`、同一字符串被 append 到 `additional_contexts`、user-visible 答复以 `<stop-hook>` 形式带上（受 `suppressOutput` 节制）。对应矩阵行「JSON `systemMessage`」。
- **`test_hooks_stop_hook_specific_additional_context.py`** —— Stop hook 分别返回 `{"hookSpecificOutput": {"additionalContext": ["a", "b"]}}`（list 形态）与 `{"hookSpecificOutput": {"additionalContext": "c"}}`（str 形态）；断言两种都正确 append 到 `additional_contexts`、user-visible 答复反映每个元素。第三个子用例：legacy 顶层 `{"additionalContext": "d"}`（没有 `hookSpecificOutput` 信封），断言 B2 parser 表中那条 Agentao 容忍分支被触发。对应矩阵行「JSON `hookSpecificOutput.additionalContext`（Stop）」。
- **`test_hooks_stop_continue_false_precedence.py`** —— 三个子用例覆盖 B2 parser 不变量中的优先级规则。(a) Stop hook 返回 `{"continue": false, "decision": "block", "reason": "ignore me"}`：断言 `force_continue == False`、loop 结束本回合，`stop_reason == "ignore me"` 仍记录给 replay。(b) Stop hook 返回 `{"continue": false, "preventContinuation": true, "stopReason": "noop"}`：断言 `force_continue == False`、回合结束。(c) Stop hook 返回 `{"continue": false, "blockingError": "lint failed"}`：断言 `blocking_error == "lint failed"`、最终回答是 block 消息 —— `continue:false` **不**压制 `blockingError`，因为两者都是「停」（B2 不变量 #3 写明）。这条测试是 Claude Code「common output 字段优先于事件专属 decision 字段」契约的回归保险。
- **`test_hooks_stop_payload_common_fields_precedence.py`** —— 让真实 chat turn 把 Claude 五个通用输入字段（`session_id`、`transcript_path`、`cwd`、`permission_mode`、`hook_event_name`）round-trip 一遍；断言：当 engine 接好时，`permission_mode` 反映实际的 `agent.permission_engine.active_permissions().mode` 而非 `"workspace-write"` 兜底；当 engine 缺失时，`permission_mode == "workspace-write"`（文档化兜底）；`cwd == str(agent.working_directory)`；`session_id` 已设置时 `== agent._session_id`、未设置时 `== ""`；`transcript_path is None`（OQ1 (a)）。这条测试钉住「实际 engine 值 vs 兜底」之间的优先级，让以后的 refactor 不能悄悄退回兜底。对应矩阵行「通用输入字段」。
- **`test_hooks_stop_no_emit_when_no_stop_rules.py`** —— 已在 PR-1 的 A6 中落地（它钉的是 no-emit gate，本就是 Phase A 行为，由 A2 的 `select_matching_rules` 驱动）。Phase B 不动该测试就能复用：gate 现在同时也住在 `_emit_stop_hook_fired`（B7）里，按 `stop_result.matched_rule_count > 0` 守住；这个数值由构造方式保证与 helper 侧一致（B1 dispatcher 字段由同一过滤式赋值）。本层不需要新增断言 —— 现有 (a)/(b)/(c) 三个子用例的 gate 语义在 Phase A 与 Phase B 下完全相同。
- **`test_hooks_stop_doom_loop_dispatch.py`** —— 钉住 doom-loop 发射位置。强行让 `ToolRunner` 撞 doom-loop（例如 monkeypatch `tool_planning` 在第二次调用时把 `result.doom_loop_triggered` 置 True），跑一次注册了 Stop hook 的真实 chat turn。断言：(a) Stop subprocess 在 stdin payload 中收到 `turn_end_reason == "doom_loop"`（证明第三个 reason 值通过 `build_stop` 正确串入）；(b) 当 `assistant_message.content` 为空时，`last_assistant_message == "Tool execution halted by doom-loop detection."`（证明兜底字符串被装进 payload）；(c) 落到 transport 上**正好一条** `hook_name == "Stop"`、`outcome == "allow"`、**`at_max_iter == False`** 的 `PLUGIN_HOOK_FIRED`（证明 doom 位置**不**冒充 max-iter），**且 emit dict 上 `turn_end_reason == "doom_loop"`** —— 这是 B7 disambiguation 契约，也是仪表盘消费 `PLUGIN_HOOK_FIRED` 时区分 doom-arm `continue` 与自然 turn `continue` 的依据；(d) `agent.messages` 中末条 assistant 消息是 `final_msg_doom`、`run()` 返回值是 `assistant_content_doom`。子用例 (e) —— Stop hook 在 doom 位置返回一次 `force_continue`：断言 loop 再发一次 LLM 调用、transport 事件携带 `outcome == "continue"`（**不是** `"continue_at_max_iter"`，`at_max_iter` 保持 False）**且 `turn_end_reason == "doom_loop"`**（唯一能识别 doom-arm `continue` 的判别字段）、`_stop_reentries` 自增、ToolRunner 的 doom 计数器**未**被 chat loop 重置。子用例 (f) —— Stop hook 返回 `force_continue` 但封顶已满：断言 transport 事件携带 `outcome == "reentry_capped"` **且 `turn_end_reason == "doom_loop"`**、WARNING 文案点名「doom-loop」、回合结束。这条测试钉死第三个出口的完整 Stop 语义、B3 的四个发射点（与自然 turn 对称）、以及每个 doom-arm emit 上的 `turn_end_reason` 字段 —— 没有它，未来 refactor 可能把 doom 分支并进 max-iter，或者把 `turn_end_reason` 从 emit dict 上拆掉，让 host 的 replay 事件被悄悄错分类。
- **`test_hooks_stop_max_iter_dispatch.py`** —— 对称地钉住 max-iter 发射位置。把 chat loop 配置低 `max_iterations`，注册一个 Stop hook 与一个 `on_max_iterations` transport 让其返回 `{"action": "stop"}`。把 loop 推过迭代上限，断言：(a) Stop subprocess 在 stdin payload 中收到 `turn_end_reason == "max_iterations"`；(b) transport `PLUGIN_HOOK_FIRED` 事件携带 `hook_name == "Stop"`、`outcome == "allow"`、`at_max_iter == True`、**且 emit dict 上 `turn_end_reason == "max_iterations"`**（这是唯一一条把「自然⟂max-iter 判别字段」钉到 transport 通道上的测试 —— `test_hooks_stop_force_continue_decision_block.py` 覆盖自然 turn 的对、`test_hooks_stop_doom_loop_dispatch.py` 覆盖 doom-loop）。子用例 (b) —— Stop hook 返回 `force_continue` 且封顶未满：断言 emit 上 `outcome == "continue_at_max_iter"` **且 `turn_end_reason == "max_iterations"`**。子用例 (c) —— `force_continue` 且封顶已满：断言 emit 上 `outcome == "reentry_capped"` **且 `turn_end_reason == "max_iterations"`**。这条测试加上自然 turn 测试与 doom-loop 测试，三处 transport-event 断言一起覆盖完整 3×5 outcome 矩阵的 `turn_end_reason` 判别字段在线上的形态。

### B7. Replay 事件 projection（Stop）

`PLUGIN_HOOK_FIRED`（transport/replay 通道——可见性 caveat 同 A5）由 **B3 的 chat-loop 接线发出，而不是 `_dispatch_stop` helper**。helper 只知道 hook **请求**了什么（`force_continue=True/False`、`blocking_error`）；它**不**知道：调用方是自然 turn 位置还是 max-iter 位置；调用方是否兑现 `force_continue`，还是命中了 re-entry 封顶。这四个 label（`continue`、`continue_at_max_iter`、`reentry_capped`、`allow`）只能在每个终止分支、cap 检查跑完之后才能决定。

**helper / 接线职责切分：**

- `_dispatch_stop(self, agent, assistant_content, *, turn_end_reason, at_max_iter)` —— **内部自己构造 Claude-flat Stop payload**：实例化 `ClaudeHookPayloadAdapter()`、调用 `adapter.build_stop(...)`（A3 的 builder 内部把 `transcript_path` 固定为 `None` —— helper **不**传这个字段），随后用 `PluginHookDispatcher(cwd=...)` 的 `dispatch_stop(...)` 跑 hook、返回 `StopHookResult`。**不**发 `PLUGIN_HOOK_FIRED`。**形态完全照抄** `_dispatch_user_prompt_submit`（`chat_loop.py:330-348`）—— adapter 与 dispatcher 都是局部作用域（runner 唯一的持久属性是 `self._agent`，见 `chat_loop.py:112-113`），规则列表从 `agent._plugin_hook_rules` 读。**调用方不构造 `payload_for_stop` dict**。

  ```python
  def _dispatch_stop(
      self, agent: "Agentao", assistant_content: str, *,
      turn_end_reason: Literal["final_response", "max_iterations", "doom_loop"],
      at_max_iter: bool = False,
  ) -> StopHookResult:
      """构造 payload、跑 Stop hook、返回聚合结果。
      **不**发 PLUGIN_HOOK_FIRED —— outcome 取决于调用方分支判断
      （cap 检查、at_max_iter 区分），helper 没有这些信息。
      发射逻辑见 B7。
      """
      if not agent._plugin_hook_rules:
          return StopHookResult()
      from ..plugins.hooks import (
          ClaudeHookPayloadAdapter,
          PluginHookDispatcher,
      )
      cwd = agent.working_directory
      perm = (
          agent.permission_engine.active_permissions().mode
          if agent.permission_engine is not None else "workspace-write"
      )
      adapter = ClaudeHookPayloadAdapter()
      # 注意：build_stop **不**接受 transcript_path 入参
      # （A3 builder 把它固定为 None / OQ1 (a)），所以这里不传。
      # 传了会 TypeError。
      payload = adapter.build_stop(
          session_id=agent._session_id,
          cwd=cwd,
          permission_mode=perm,
          last_assistant_message=assistant_content,
          turn_end_reason=turn_end_reason,
          stop_hook_active=(self._stop_reentries > 0),
      )
      dispatcher = PluginHookDispatcher(cwd=cwd)
      # Phase B 衔接（A2「匹配规则计数」）：先用 select_matching_rules
      # 预过滤，保证 no-emit early-return 在 dispatch_stop 进入之前就
      # 生效（不开 subprocess、不打扰 transport）。dispatcher 内部
      # （B2）的过滤对已过滤列表退化为 no-op。空匹配时返回空
      # StopHookResult()，其 matched_rule_count == 0 会让 B3 各终止
      # 分支的 PLUGIN_HOOK_FIRED 发射全部被压住。
      matched = dispatcher.select_matching_rules(
          "Stop", payload, agent._plugin_hook_rules,
      )
      if not matched:
          return StopHookResult()
      return dispatcher.dispatch_stop(payload=payload, rules=matched)
  ```

- `_emit_stop_hook_fired(self, agent, *, outcome, at_max_iter, stop_result)` —— B3 在每个终止分支调用的 chat-loop 小 helper。`PLUGIN_HOOK_FIRED` 在 Stop 上的 payload 形态由它独占。把 dict 包进 `AgentEvent(EventType.PLUGIN_HOOK_FIRED, {...})` —— 因为 transport `emit(self, event: AgentEvent)` 协议（`agentao/transport/base.py:28`）只接受被包装的 event，直接传 dict 就是类型错误。再把 emit 调用包进 `try/except Exception: pass`，遵守协议的「不得抛异常」契约（与 `chat_loop.py:368-369` 现有 UserPromptSubmit emit 形态一致）。**`stop_result.matched_rule_count > 0` 才发**（B1 字段 —— **被选数**，不是执行数；见 B1 docstring）—— 当本回合没有任何 Stop 规则被选入 dispatch 时，helper 静默返回，避免一个 0 条匹配 Stop hook 的回合也发 `hook_name="Stop", outcome="allow"`（既吵又语义错乱）。这与 `_dispatch_user_prompt_submit` 在 `chat_loop.py:332-333` 的早返回行为对齐，并额外覆盖了「规则非空但没有针对 Stop 的」这种早返回看不到的情况。只读 B3 调用点就能枚举本计划能发的所有 outcome label，没有任何隐藏在 `_dispatch_stop` 里的发射点。

  ```python
  # chat_loop.py 顶部 imports —— `AgentEvent` 与 `EventType`
  # 已经为现有 emit 位置 import 过；不需要新增 import。
  def _emit_stop_hook_fired(
      self, agent: "Agentao", *,
      outcome: Literal["allow", "block", "continue",
                       "continue_at_max_iter", "reentry_capped"],
      at_max_iter: bool,
      turn_end_reason: Literal["final_response", "max_iterations", "doom_loop"],
      stop_result: StopHookResult,
  ) -> None:
      # Replay gate：没有 Stop 规则被选入 dispatch 时跳过发射
      # （**被选数**，不是执行数 —— 见 B1 docstring）。
      # 否则一个 0 条匹配 Stop hook 的回合会发 outcome="allow" ——
      # 既吵又语义错乱。
      if stop_result.matched_rule_count == 0:
          return
      try:
          # `turn_end_reason` 必须出现在 emit payload 上，让仪表盘
          # 能区分自然 turn 的 outcome="continue" 与 doom-loop 的
          # outcome="continue" —— 详见下面 outcome 枚举表的
          # disambiguation 规则。
          agent.transport.emit(AgentEvent(EventType.PLUGIN_HOOK_FIRED, {
              "hook_name": "Stop",
              "outcome": outcome,
              "at_max_iter": at_max_iter,
              "turn_end_reason": turn_end_reason,
              "matched_rule_count": stop_result.matched_rule_count,
              "added_context_count": len(stop_result.additional_contexts),
              "suppress_output": stop_result.suppress_output,
          }))
      except Exception:
          pass
  ```

**outcome 枚举（最终，唯一权威定义 —— 三个发射位置、五个 label）。**

五个 outcome label 与三个发射位置组成 3×5 表面、不是所有组合都填充。下表枚举**实际发射的每对 (位置, label)**；解析 `PLUGIN_HOOK_FIRED` 的读者/仪表盘应当用 `(turn_end_reason, at_max_iter, outcome)` 三元组判别，不是只看 `outcome`。

| Label | 自然 turn（`turn_end_reason="final_response"`、`at_max_iter=False`） | max-iter（`turn_end_reason="max_iterations"`、`at_max_iter=True`） | doom-loop（`turn_end_reason="doom_loop"`、`at_max_iter=False`） | 何时使用 |
|---|---|---|---|---|
| `allow` | ✅ allow 路径（约 `chat_loop.py:730-737`） | ✅ allow 路径（约 `chat_loop.py:805-810`） | ✅ allow 路径（doom-arm allow 尾，约 `chat_loop.py:271+`） | hook 跑完干净，无 force_continue、无 blocking_error；hook 附 `additional_contexts` 且 `suppress_output=False` 时 `added_context_count > 0`（`suppress_output=True` 时仍记 count，但 `final_msg` 不拼 `<stop-hook>` 块） |
| `block` | ✅ block 分支（`return blocked`，约 `chat_loop.py:680-681`） | ✅ block 分支（`return blocked`，约 `chat_loop.py:778`） | ✅ block 分支（doom-arm 内的 `return blocked`） | 有 `blocking_error`；由 hook 提供的错误消息结束本回合；与封顶无关 |
| `continue` | ✅ force_continue 分支，**仅在** `_stop_reentries < _stop_reentry_cap` 通过之后（约 `chat_loop.py:711-712`） | ❌ N/A —— max-iter 用 `continue_at_max_iter` 替代 | ✅ force_continue 分支，**仅在**封顶检查通过之后（doom-arm；`at_max_iter=False`，所以用此 label，**不**用 `continue_at_max_iter`） | 在非 max-iter 位置追加一次 re-entry |
| `continue_at_max_iter` | ❌ N/A —— 自然 turn 用 `continue` | ✅ force_continue 分支，**仅在**封顶检查通过之后（约 `chat_loop.py:805-806`） | ❌ N/A —— doom 位置用 `continue`（它**不是** max-iter；判别用 `turn_end_reason`，**不是** outcome） | 在 max-iterations 处追加一次 re-entry；与 `continue` 区分让仪表盘能 flag 这个可疑场景（B3） |
| `reentry_capped` | ✅ cap-hit 分支（约 `chat_loop.py:694-700` 的提前返回） | ✅ cap-hit 分支（B3 为 max-iter 显式写出的 `if force_continue and _stop_reentries >= _stop_reentry_cap` 分支） | ✅ cap-hit 分支（doom-arm 内对称的 cap-hit 分支；B3 doom-loop 节） | `force_continue` 申请但已 `_stop_reentries >= _stop_reentry_cap`；loop 拒绝 re-entry，本回合结束 |

**消费侧 disambiguation 规则。** 仅靠 `outcome="continue"` 不能告诉仪表盘 re-entry 发生在自然 turn 还是 doom-loop —— 这两者**操作风险不同**（doom-loop 上的 force_continue 更接近「host 在模型异常下还坚持继续」、风险高于自然 turn 的 force_continue）。在意这一点的消费方应当**同时**读 `turn_end_reason`、`outcome`、`at_max_iter`。三选 `turn_end_reason` 是「**哪个**出口」的唯一权威；`outcome` 是「**hook 说了什么**」的唯一权威；`at_max_iter` 是供下游过滤的冗余字段。

**为什么不在 `_dispatch_stop` 里发。** 把 cap / `at_max_iter` 决策下沉到 helper，会迫使 helper 读 `self._stop_reentries` 与 `self._stop_reentry_cap`，并要知道调用方是自然位置还是 max-iter 位置 —— 那是 chat-loop 接线的职责，不是 dispatcher 的。这次切分让 `_dispatch_stop` 能孤立测试（B6 的 dispatcher 测试可以断言 `StopHookResult` 形态而不需要起 chat loop），并防住「未来调用方忘了把 `at_max_iter` 接上」这一类 bug。

`_dispatch_user_prompt_submit` 的 `"modify"` label 在这里**不**适用——Stop 的 `additional_contexts` 是拼到助手最终回答（B3），不是注入下一条用户 prompt，所以以 `added_context_count > 0` 的形态搭乘 `"allow"`。Open Q4 中提到的 `continue_at_max_iter` 在此正式纳入枚举。

**测试影响。** `test_hooks_stop_reentry_cap.py` 已经断言 `reentry_capped` 落到 transport。评审十六轮的 disambiguation 契约（emit dict 上的 `turn_end_reason`）由**三**条 transport-event 测试钉住，每个发射位置一条：自然 turn 由 `test_hooks_stop_event.py` / `test_hooks_stop_force_continue_decision_block.py` / `test_hooks_stop_exit_code_2.py` / `test_hooks_stop_no_emit_when_no_stop_rules.py` 钉（`turn_end_reason="final_response"`）；max-iter 由新增的 `test_hooks_stop_max_iter_dispatch.py` 钉（`"max_iterations"`）；doom-loop 由 `test_hooks_stop_doom_loop_dispatch.py` 钉（`"doom_loop"`）。三者一起覆盖 `turn_end_reason` 在三处发射位置的取值，防住未来 refactor 把字段从 emit dict 上拆掉（这种修改会让仪表盘按 disambiguation 规则做事的代码失效）。

---

## 不在范围内（明确列出）

- `Notification`、`SubagentStop`、`PostToolBatch`、`StopFailure` —— 在 `pi-mono-borrow-review.md` 内跟踪。各自需要独立的 emit 位置和形态决策，不并入本计划。
- 手工触发的压缩（`/compact` 类 CLI 命令）。CLI 当前未暴露此命令；将来出现时再处理。PreCompact 的 `trigger="manual"` 因此永不发出（兼容性矩阵 🟡）。
- 把 `PreCompact` 提升为公开 ACP 事件。内部 `EventType.CONTEXT_COMPRESSED` 已覆盖压缩之后；之前阶段的提升属于另一个 Public-Event-Promotion 类工单。
- 把 plugin-hook 事件提升进 `agentao.host.EventStream` 的 discriminated union（今天是 `ToolLifecycleEvent | SubagentLifecycleEvent | PermissionDecisionEvent`）。本计划两个阶段的 `PLUGIN_HOOK_FIRED` 都只走 internal transport/replay 通道；跨切面 host 提升是独立工单。
- 把 `_dispatch_lifecycle` 的 `HookAttachmentRecord` 列表透出到 conversation / replay 层（**任何** lifecycle 事件）。所有现有调用点都丢弃返回值（见 A6 caveat），统一修正属于 `PLUGIN_HOOK_ATTACHMENT_PIPELINE_PLAN`。
- `Stop` / `PreCompact` 的 prompt-type hook（`hook_type == "prompt"`）。当前只有 UserPromptSubmit 支持 prompt 型；扩展该表面与本计划独立。**A1 在解析期主动拒绝**这两个事件上的 prompt 类型规则（通过 `SUPPORTED_HOOK_TYPES_BY_EVENT`），让配置错误以 parser warning 形式现身，而不是被静默接收后再在 dispatch 层丢掉。**理由见上面「为什么 Stop / PreCompact 不支持 prompt 型 hook」一节** —— Stop 的能力与 command hook 重复；PreCompact 在我们的 observe-only 契约下没有 prompt 回复的目的地；迁移路径是改写为 `command` 型 shim（脚本内部调 LLM、发 Claude 兼容 Stop JSON）。
- **PreCompact blocking（Claude Code 兼容性 gap）** —— exit 2 / `decision: "block"` 在 PreCompact 上仅观察、不兑现。详见 B5。
- **Stop / PreCompact 之外其它事件的 Claude Code wire 兼容。** UserPromptSubmit / SessionStart / PreToolUse / PostToolUse / PostToolUseFailure 仍用 agentao `{event, data}` 信封；改造它们是有消费方影响的跨切面 refactor。详见兼容性矩阵。
- **Claude Code 配置文件兼容**（`~/.claude/settings.json` 形态）。Agentao 读自家 `permissions.json` / hook config；想要 drop-in Claude config 文件的 host 须自行预翻译。不在范围内。

---

## 待解决问题

1. **`transcript_path` 字段。** Claude Code 传递磁盘上的 transcript 路径。agentao 的 session 日志分散在 `agentao.log` 与内存中的 `agent.messages`，没有单一权威文件。选项：(a) 直接 `transcript_path = null`；(b) 每次 dispatch 时把 `agent.messages` 快照写到临时文件；(c) 干脆不传该字段。**建议：** Phase A 用 (a) —— 字段**存在**（保留 Claude 兼容的顶层 key），值为 `null`。Phase A 新加的 `last_assistant_message` 已经覆盖最常见的 Stop hook 用例（审最终回答）、不再需要 transcript 文件。如有 host 提出实际需求再考虑 (b)。
2. **force-continue 的 follow-up —— user role 还是 system role？** Claude Code 用 user-role 注入 reason。照搬即获得即插即用兼容性，但来源信息会被模糊。**建议：** user role + `<system-reminder>Stop hook injected this</system-reminder>` 前缀，让模型与 transcript 阅读者都能看到来源。
3. **重入封顶默认值。** 3 是估的。**已结：** 在 chat loop 上以构造常量形式硬编码 3。**不**在本计划引入 `stop_hook_reentry_max` settings key——见 B4 理由（`.agentao/settings.json` 当前只有两个读者，为一个无人调的旋钮再加第三个 reader 是过早暴露配置面）。等首位真实 host 撞封顶后再调或暴露。
4. **max-iterations 出口的 Stop —— gate 还是 observe？** 在那里强制继续可能掩盖真正的 loop 故障。**已结：** 允许，但 emit `WARNING` 并用专门 label `outcome="continue_at_max_iter"`（见 B7 枚举），让仪表盘能 flag 出来。
5. **`permission_mode` 取值空间映射。** 字段形态与 Claude Code 一致，但取值词汇不同（矩阵标 🟡）。三个选项：
   - **(a) 原样发出 Agentao 取值**（`"read-only" \| "workspace-write" \| "full-access" \| "plan"`）。hook 脚本须熟悉 Agentao 词汇；provenance 保真。**Phase A 推荐。**
   - **(b) emit 前翻译到最接近的 Claude 值**（`"read-only" → "default"`、`"workspace-write" → "acceptEdits"`、`"full-access" → "bypassPermissions"`、`"plan" → "plan"`）。按这些字符串分支的 Claude 脚本可以即插即用，但映射有主观判断且丢信息。
   - **(c) 同时 emit 两个字段：`permission_mode`（翻译后的 Claude 风格）+ `agentao_permission_mode`（原始）。** wire 膨胀；只有当真实 host 撞上 gap 时才值得做。
   **建议：** Phase A 用 (a)，并在矩阵与本 OQ 中记录差异；等某 host 真要 (b) 或 (c) 再开。**不要**默默翻译——没有显式映射表的 (b) 会模糊 provenance。

---

## 排期

- **PR-1（Phase A，约 1.5 天）：** A1–A7。包含 `_matches` 对 PreCompact `manual|auto` matcher 的扩展、新增的平铺 snake_case builder。本 PR 中 `dispatch_stop` 返回 `list[HookAttachmentRecord]`。独立 commit，可单独发布。
- **PR-2（Phase B，约 2 天）：** B1–B4、B6–B7。新增 Stop 专用 runner（兑现 exit code 2 与 Claude Code JSON 解析）。依赖 PR-1 已合入，但不修改 A 的 emit 位置。
   - **本 PR 内的破坏性签名变更：** `dispatch_stop` 从 `list[HookAttachmentRecord]` 升级到 `StopHookResult`（见 B2 的「测试影响」一段）。A6 的 `test_hook_dispatcher_stop.py` 改写为 walk `result.messages` 并覆盖新加的 gate 信号字段。`dispatch_pre_compact` 保留 Phase-A 返回类型 —— PreCompact 仍 observe-only（B5）。

合计约 3.5 个开发日（触发后）。PreCompact blocking 是 Claude Code 兼容性 gap（B5），不在 roadmap 上；如某 host 必须用，相关工作进 `PRECOMPACT_GATE_PLAN.md`。

---

## 修订备忘

**rev 2026-05-05 —— 评审二十三轮（`select_matching_rules` 声称与 `_dispatch_lifecycle` 是「同一 filter」，但两式并不等同）。** 一项低风险措辞订正：

1. **P3 —— 工具 docstring 写「对外暴露 `_dispatch_lifecycle` 内部使用的同一套 event/is_supported/matcher 过滤」，但 `_dispatch_lifecycle` 实际按 event + `hook_type == "command"` + `_matches` 过滤（`agentao/plugins/hooks.py:381`），工具自己用的是 event + `is_supported` + `_matches`。** `is_supported` 是 `hook_type == "command"` 的严格超集 —— 它允许 `hook_type in {"command", "prompt"}` —— 所以对支持 prompt-type 的事件，两式可能分叉。正常 loader 路径下这个分叉被 A1 的按事件 hook-type 拒绝关掉（Stop / PreCompact 的 prompt-type 规则在解析期被丢弃，运行时 `is_supported` 也翻为 False），因此本计划新增的两个事件下 loader 产出的规则上两式对每一条都等价 —— 但 docstring 上「同一 filter」是字面错误：单看本节的实现者可能会以此为由把工具改写成 `hook_type == "command"`，把它从「规范选择过滤器」退化为「dispatcher 内部 helper」，违背 A2 的原意。解法：(a) 收紧 docstring，把工具显式定位为 **Stop / PreCompact 的规范选择过滤器**（不再声称与 `_dispatch_lifecycle` 字面对齐）；(b) 在四步流之后新增**「与 `_dispatch_lifecycle` 的对齐说明」**段，写出 lifecycle 实际过滤条件、解释 `is_supported` 是超集、点名 A1 的按事件拒绝是把分叉关掉的桥梁、告诉实现者应依赖 A1 的解析期拒绝而非 lifecycle runner 的形态；(c) 保留 `is_supported`（不切换为 `hook_type == "command"`），以便未来若有事件合法支持 prompt-type 不需 fork 新的选择过滤器。**没有行为变更** —— 本计划下 loader 产出的所有 Stop / PreCompact 规则上 `is_supported` 与 `hook_type == "command"` 一致，on-wire 选择集合等价。

**没有源代码变更 —— 评审二十三轮纯粹是 docstring + 规格叙述收紧。** 工具的过滤表达式和 dispatcher 的行为一概不动；只是把规格中关于工具与 `_dispatch_lifecycle` 关系的措辞订正到与代码一致。

**rev 2026-05-05 —— 评审二十二轮（A2 四步流的第 4 步把 `dispatch_stop` 写死，让 PreCompact helper 失去 dispatch 出口）。** 一项低风险措辞订正：

1. **P3 —— A2「四步流」前置语写「Phase A chat-loop helper（`_dispatch_stop` / `_dispatch_pre_compact`）都用此流程」，但第 4 步只写 `dispatcher.dispatch_stop(payload=payload, rules=matched)`。** 按四步流实现 `_dispatch_pre_compact` 的人在权威步骤上看不到 PreCompact 的 dispatch 方法名；照抄第 4 步会把 PreCompact 已过滤的规则塞进 `dispatch_stop`，被 B2 的 Stop-only 事件过滤式（`select_matching_rules("Stop", ...)`）再次过滤后变成空列表 —— 每个 PreCompact hook subprocess 都被悄悄丢掉。解法：把第 4 步改写为同时列出两个方法：「否则调用对应的 lifecycle dispatch —— Stop 走 `dispatcher.dispatch_stop(payload=payload, rules=matched)`，PreCompact 走 `dispatcher.dispatch_pre_compact(payload=payload, rules=matched)`（传入已过滤列表让 dispatcher 内部 re-filter 在两个事件下都退化为 no-op），并以 `matched_rule_count=len(matched)` emit `PLUGIN_HOOK_FIRED`」。「在两个事件下都退化为 no-op」这个补充也顺手堵上一个小规格缺口：之前 no-op 主张对 PreCompact 是隐式的（靠 `_dispatch_lifecycle` 内部 `_matches` 对预过滤列表恒返回 True），只对 Stop 显式（B2 用 `select_matching_rules("Stop", ...)` 显式 re-filter）；现在两侧对称。

**没有源代码变更 —— 评审二十二轮纯粹是 A2 四步流的措辞订正。** 没有设计变更：A4 已正确点名两个 helper 并各自路由到 `dispatch_*`；B7 也正确使用 `dispatch_stop`；只是 A2 前置语的第 4 步例子是 Stop 专用。

**rev 2026-05-05 —— 评审二十一轮（`matched_rule_count` 语义与 Stop 短路执行不一致 + A2 attachment 计数自相矛盾）。** 两项发现已落入计划：

1. **P2 —— `matched_rule_count` 被写为「实际执行的规则数」，但 B2 run loop 在 `blocking_error` / `force_continue` 上短路，所以字段实际报的是「dispatch 之前被选数」。** A5（488 行）写「the number of `Stop` rules that ran」；B1 docstring 写「matched-and-ran」；B7 散文与伪代码注释写「actually ran」/「actually matched-and-ran」。但 B2 在 run loop **之前**就把 `result.matched_rule_count = len(stop_rules)` —— 字段赋值点本身的注释明说这是为了「单条短路时仍能拿到正确计数」。结果：3 条选中、第 1 条短路时字段报 `3`，与每一处「ran」说法都矛盾。两种修法：(i) 把赋值挪到循环**之后**、只数实际跑完的规则 —— 但这违背字段目的（`_emit_stop_hook_fired` gate 会把 force_continue 路径下确实跑过一条规则的回合压住，错误）；(ii) 重新钉死文档语义为「被选数」，赋值位置保持不变。选 (ii)。更新：**A5**（规范 schema 定义：「**`matched_rule_count` 是被选入 dispatch 的 Stop 规则数** —— 即 `len(dispatcher.select_matching_rules(...))` —— **不是**执行数；若未来 host 需要实际运行数，应另加 sibling `executed_rule_count`，**不要**改写本字段语义」）；**B1 docstring**（把「matched-and-ran in this dispatch」换成显式的「**被选数**，不是执行数」框架 + 字段名稳定性说明，保 replay 向后兼容）；**B2 dispatcher 注释**（把「拿到正确的匹配数」改为显式「被选数，不是执行数」措辞）；**B7 散文**（「actually ran」→「selected for dispatch」并附 B1 docstring 契约指针）；**B7 伪代码注释**（同口径换词）。on-wire 字段名保留为 `matched_rule_count`，以保 replay 流向后兼容 —— 在隔离场景里改名为 `selected_rule_count` 可能更干净，但任何已经按字段名 key 的下游消费者都会失效。
2. **P3 —— A2「为什么不用 `len(attachments)`」与本节开头的括号说法自相矛盾。** A2 开头段写「attachment 计数不是 rule 计数的可靠代理（clean-run 的 command hook 不带 `additionalContext` 时附件数为 0，但 matched 数仍非 0）」，但同一节 25 行之后的专用子节却（正确地）写「Stop command hook 干净退出 0 + 空 stdout，会产生一条 `hook_success` 附件 —— 所以**碰巧**那一种情况下 attachment 数等于 rule 数」。开头的「附件数为 0」说法是错的 —— `_run_lifecycle_command` 当前行为对 clean-exit-0 **会**写一条 `hook_success` 附件。专用子节里正确的反例（多个 `additionalContext` 撑高、未来 hook_success 移除时瘪到 0）已经把代理论证带住了，无须开头那条错误举例。解法：把开头段不准确的括号删掉，改为前向指针（「具体失败模式见下面『为什么不用 `len(attachments)`』一段」）。专用子节不动。

**没有源代码变更 —— 评审二十一轮纯粹是语义重新钉死 + 一处事实订正。** on-wire schema、dispatcher 逻辑、chat-loop 接线一概不动。`matched_rule_count` 字段的名字与取值原样保留；只是把文档化的契约从松散且错误的「ran」收紧为精确且正确的「selected for dispatch」。

**rev 2026-05-05 —— 评审二十轮（`select_matching_rules` 没有贯穿到 B7 / B2 —— 权威伪代码违反同 filter 不变量）。** 两项发现已落入计划，都是闭合评审十九轮 A2 契约打开的缺口：

1. **P2 —— B7 的 `_dispatch_stop` helper 还在把未过滤规则传给 `dispatch_stop`。** 评审十九轮 A2 加了 `select_matching_rules` 并钉死 Phase A 四步用法（build → select → 空则提前返回 → 否则把过滤后列表传给 dispatch）。它还显式声明了 Phase B 衔接：「helper 仍通过 `select_matching_rules` 预先计算，保证 no-emit early-return 在 `dispatch_stop` 进入之前生效（不开 subprocess、不打扰 transport）」。但 B7 给出的 `_dispatch_stop` 权威伪代码没改，仍在直接调 `dispatcher.dispatch_stop(payload=payload, rules=agent._plugin_hook_rules)`。实现者照抄 B7 会丢掉「不开 subprocess」这条保证 —— 每个 Stop-emit 位置在每个回合都会 fork 一次 subprocess，哪怕 plugin 一条规则都没有（或规则非空但没有 Stop 规则）。`_emit_stop_hook_fired` 上的 gate（`matched_rule_count > 0`）仍能压住**transport 事件**，但**subprocess 代价**已经付了。解法：把 B7 的 `_dispatch_stop` body 重写为：构造 payload → `matched = dispatcher.select_matching_rules("Stop", payload, agent._plugin_hook_rules)` → `if not matched: return StopHookResult()`（提前返回）→ `return dispatcher.dispatch_stop(payload=payload, rules=matched)`（用过滤后列表 dispatch）。提前返回路径返回 `matched_rule_count == 0` 的空 `StopHookResult()`，B3 各终止分支上 `_emit_stop_hook_fired` 现有的 gate 就能直接把这些位置的 emit 压住 —— no-emit 语义全程保留，无需再改接线。
2. **P3 —— B2 `dispatch_stop` 的 filter 表达式与 A2 `select_matching_rules` 分叉。** A2 把规范 filter 定义为 `event + is_supported + _matches`；A2 的叙述（「两路 count 来自同一过滤式必然一致」）只在两侧表达式真的相同时成立。但 B2 伪代码写的是 `[r for r in rules if r.event == "Stop" and r.is_supported]`，**少了 `_matches`**。实际效果对 Stop 是无害的，因为 A2 的 `_matches` 对 Stop 恒返回 `True`（Claude Code 没定义 Stop matcher）；但文档化的不变量比实际行为更响亮：未来若往这条 dispatcher 路径加一个带真实 matcher 的事件，同 filter 契约会被静默打破。解法：把 B2 的内联 list-comp 换成 `self.select_matching_rules("Stop", payload, rules)`。dispatcher 内部过滤现在对 B7 预过滤后的输入幂等，对直接调用方（如 `test_hook_dispatcher_stop.py` 构造混合规则后直接调 `dispatch_stop`）则是唯一的过滤关卡。`matched_rule_count = len(stop_rules)` 现在与 A2 helper 用的是同一过滤式，「同 filter 表达式」不变量从「散文断言」升级为「语法强制」。

**没有源代码变更 —— 评审二十轮纯粹是 PR-2 的伪代码 vs 契约校准。** 评审十九轮引入的 `select_matching_rules` 现在真的在 A2 契约要求的每个位置都被用到（Phase A helper、Phase B helper、dispatcher 内部过滤）；照抄 B7 / B2 的实现者不再会绕过 early-return 保证、也不会把 filter 表达式拆成两份。

**rev 2026-05-05 —— 评审十九轮（Phase A `matched_rule_count` 来源 + gate 测试归属 + A4 最后一处双站点措辞）。** 三项发现已落入计划：

1. **P2 —— Phase A 没有可落地的 `matched_rule_count` 规格。** 评审十八轮把 `matched_rule_count` 加进了 Phase A emit schema（A5），并把 no-emit gate 挂到这个字段上；但底层 Phase A `dispatch_stop` / `dispatch_pre_compact`（A2）仍返回 `list[HookAttachmentRecord]`，`_dispatch_lifecycle` 也没在任何地方暴露「匹配并运行的 rule 数」。PR-2 的 `StopHookResult.matched_rule_count`（B1）只解决 Phase B Stop —— 不解决 Phase A Stop、也不解决 PreCompact（PreCompact 在两个 phase 下都是 observe-only）。没有具体来源，实现者要么退回 `len(attachments)`（clean exit-0 hook 上恰好对、其它一切情形都偏 —— 见 A2 新增子节「为什么不用 `len(attachments)`」），要么直接跳过 gate。解法：在 A2 新增「匹配规则计数（Phase A emit-gate 依赖）」子节，引入一个小型公共 dispatcher 工具 `select_matching_rules(event, payload, rules) -> list[ParsedHookRule]`（沿用 `_dispatch_lifecycle` 内部的同一套 event/`is_supported`/`_matches` 过滤），并钉死 Phase A helper 的四步用法：构造 payload → `select_matching_rules` → 空则提前返回 → 否则把过滤后列表传给 dispatch、并以 `matched_rule_count=len(matched)` emit。Phase B 衔接：`dispatch_stop` 升级为返回 `StopHookResult` 时，helper 侧（`select_matching_rules`）与 dispatcher 侧（`StopHookResult.matched_rule_count`）同源同过滤式，必然一致；helper 仍预先计算，保证 no-emit early-return 在 subprocess fork 之前发生。
2. **P2 —— gate 测试归属在 A5/A6/B6 之间不一致。** A5 写「这就是 A6 测试 `test_hooks_stop_no_emit_when_no_stop_rules.py` 钉的 gate」，但 A6 测试列表里**没有**该测试 —— 它住在 B6。按 §Sequencing 的说法 PR-1 是可独立交付的；如果严格解读计划，PR-1 出厂时 no-emit gate **没有任何测试覆盖**。解法：把测试从 B6 迁到 A6，并改写为 Phase A 视角（它由 `select_matching_rules` 驱动、不依赖 `_emit_stop_hook_fired`，所以归属 PR-1）；同时新增 `test_hooks_pre_compact_no_emit_when_no_rules.py` 兄弟测试（PreCompact 之前**完全没有** gate 测试 —— 现有 `test_hooks_pre_compact_event.py` 永远注册规则，从不走「空 matched」分支）。B6 的原条目改写为一条交叉引用，说明测试在 A6 出厂、PR-2 不需修改即可复用，因为 `select_matching_rules` 与 `StopHookResult.matched_rule_count` 同源。
3. **P3 —— A4 内最后一处「自然 turn 与 max-iter 两处接线」双站点措辞。** 评审十八轮的 P3 改了 Stop helper 段的括号注解，但漏掉了两段之后的 `stop_hook_active` 接线句子，那句还在写「见 B3 自然 turn 与 max-iter 两处接线」。doom-loop 是一个完整的 Stop re-entry 位置（见 B3 doom-loop 子节 —— 它和另两个一样会递增 `_stop_reentries`）。解法：改为「见 B3 自然 turn / max-iter / doom-loop 三个 force_continue 接线点」，与计划其它部分的口径一致。

**没有源代码变更 —— 评审十九轮纯粹是 PR-1 规格自洽闭合。** Phase A `matched_rule_count` 来源现在是一个有名字的公共工具（`select_matching_rules`）而非隐式约定；gate 测试归属在 A5（引用方）、A6（测试列表）、B6（交叉引用）之间一致；最后一处双站点措辞残留也已清除。

**rev 2026-05-05 —— 评审十八轮（Phase A replay emit 规格补全 + A4 残余措辞）。** 两项发现已落入计划：

1. **P2 —— A5 没把 Phase A `PLUGIN_HOOK_FIRED` payload 写够，导致 PR-1 实现者必须前向引用 B7。** A5 只写了 `outcome="allow"` 是唯一 label，但 A6 的 `test_hooks_stop_event.py`（评审十七轮已重写）已经在 transport emit dict 上断言 `turn_end_reason="final_response"`、`at_max_iter=False` —— 而这些字段的 schema 只出现在 B7 的 `_emit_stop_hook_fired` body（Phase B）。Sequencing 段落说 PR-1 是可独立交付的；可单看 A5，没有 on-wire dict 的权威 schema：实现者要么 (a) 漏发（不 emit `turn_end_reason` / `at_max_iter`），导致 A6 失败；要么 (b) 把 B7 的 Phase B 全量形态前移，模糊 PR-1/PR-2 边界。解法：在 A5 新增两小节 —— **「Phase A emit payload（最小 schema）」** 把 Stop 和 PreCompact 的 key/type/value 三元组逐一列出（`hook_name`、`outcome`，加上来自 A4 的 discriminators —— Stop 的 `turn_end_reason` + `at_max_iter`、PreCompact 的 `compaction_type` + `trigger` —— 以及共享的 `matched_rule_count` no-emit gate）；**「Emit 归属」** 明确 Phase A 在 helper 内部 emit（因为 outcome 恒为 `allow`），Phase B 仅对 Stop 把 emit 拆给专用 `_emit_stop_hook_fired`。同时显式声明 Phase A → Phase B **只增不改**（没有字段改名、没有字段删除）。
2. **P3 —— A4 内 "(already in scope at both sites)" 残余措辞。** 评审十五轮把 doom-loop 加为第三个 Stop emit 位置，但 Stop helper 段的括号注解还在说「both sites」（二元）。不影响实现，但读者可能误以为第三个位置不是同等一等出口。解法：改为 "(already in scope at each site)"。

**没有源代码变更 —— 评审十八轮把评审十七轮 transport-event 断言暴露出来的 PR-1 规格自洽缺口闭合。** Phase A emit schema 现在在 A5 一处写清，不再由 A6 的测试 + B7 的 helper body 隐式定义。

**rev 2026-05-05 —— 评审十七轮（B6 transport-event `turn_end_reason` 覆盖缺口）。** 一项低风险发现已落入计划；补齐评审十六轮 disambiguation 契约带来的 B6 测试覆盖空白：

1. **B6 测试在 hook stdin payload 上断言了 `turn_end_reason`，但**没有**对应在 transport `PLUGIN_HOOK_FIRED` emit dict 上做断言。** 评审十六轮把 `PLUGIN_HOOK_FIRED.turn_end_reason` 提为消费方区分 doom-arm `continue` 与自然 turn `continue` 的判别字段（B7 outcome 表）。但 B6 只在**输入侧**（Stop hook stdin payload，经 `build_stop` 串入）钉了 `turn_end_reason`；**输出侧**（transport emit dict）没有任何针对性断言。未来 refactor 把 `turn_end_reason` 从 `_emit_stop_hook_fired` 的 emit dict 上拆掉 —— 输入侧测试照样通过、dispatcher 测试照样通过、所有 `outcome=...` 测试照样通过 —— 但下游消费的仪表盘会被悄悄打破。解法：
   - **五条已有测试新增显式 `PLUGIN_HOOK_FIRED["turn_end_reason"]` 断言：**
     - `test_hooks_stop_event.py` —— 钉 `"final_response"`（自然 turn allow 路径）
     - `test_hooks_stop_force_continue_decision_block.py` —— 钉 `"final_response"` + `outcome="continue"`（自然 turn force_continue）
     - `test_hooks_stop_exit_code_2.py` —— 钉 `"final_response"` + `outcome="continue"`（自然 turn exit-2 force_continue）
     - `test_hooks_stop_reentry_cap.py` —— 钉 `"final_response"` + `outcome="reentry_capped"`（自然 turn cap-hit）
     - `test_hooks_stop_no_emit_when_no_stop_rules.py`（子用例 c）—— 在正向对照上钉 `"final_response"`
   - **`test_hooks_stop_doom_loop_dispatch.py` 新增 transport 侧断言：** doom-arm 三个子用例（allow / continue / reentry_capped）现在都断言 emit dict 上 `turn_end_reason == "doom_loop"`，不只是 hook stdin。
   - **新增 `test_hooks_stop_max_iter_dispatch.py`** —— 之前没有专门的 max-iter Stop dispatch 测试。三个子用例钉 transport 上的 `(turn_end_reason, outcome, at_max_iter)` 三元组：`("max_iterations", "allow", True)`、`("max_iterations", "continue_at_max_iter", True)`、`("max_iterations", "reentry_capped", True)`。没有这条测试，max-iter transport-event 仅靠 `test_hooks_stop_reentry_cap.py` 的隐含覆盖，无法判别究竟是哪个发射位置。
   - **B7「测试影响」段落**重写，把每个发射位置映射到对应的测试，让覆盖矩阵显式：自然 turn → 4 条测试、max-iter → 1 条新测试、doom-loop → 1 条测试。

**没有设计变更 —— 评审十七轮纯粹补足评审十六轮 disambiguation 契约的测试覆盖。** 无源代码或生产行为变更。

**rev 2026-05-05 —— 评审十六轮（评审十五轮 follow-on：helper Literal 类型 + outcome 表都还忽略了 doom-loop）。** 两项发现已落入计划；都是把评审十五轮**起了头但没接完**的 doom-loop 集成补到位：

1. **P1 —— `_dispatch_stop` 的 Literal 类型仍是 `["final_response", "max_iterations"]`，缺 `"doom_loop"`。** 评审十五轮把 A3 builder 签名类型化为三值，B3 的 doom-loop 调用点也传 `turn_end_reason="doom_loop"`，但 B7 helper body 内的 Literal 类型没动。实现者照抄 B7 body，第三个调用点会过不了类型检查；或者更糟，把类型放宽成 `str`、把另两个位置的保护一起丢了。解法：把 `"doom_loop"` 加进 Literal，三个调用点与类型契约一致。
2. **P2 —— outcome 枚举表仍然只列自然 turn / max-iter 的来源，忽略 doom-loop。** 评审十五轮把 doom-loop 分支加到 B3 与 `test_hooks_stop_doom_loop_dispatch.py`（断言 `outcome="allow" / "continue" / "reentry_capped"` 在 doom-loop），但 B7 outcome 表（章节标题仍叫「**唯一权威定义**」）从未长出 doom-loop 的列。三个下游危险：(a) 接 doom 分支的实现者没有正式参考能查「哪个位置发哪个 label」；(b) 仅按 `outcome` 解析的仪表盘无法区分 doom-loop 与自然 turn（都发 `continue`）；(c) 表与 B6 测试计划自相矛盾。解法：把 outcome 表重写为 3×5 矩阵（自然 turn / max-iter / doom-loop 三列 × 五行 label），每对发射的位置×label 用 `(✅ at <行>)`、三个结构上不可能的组合（max-iter 上的 `continue`、自然 turn 上的 `continue_at_max_iter`、doom-loop 上的 `continue_at_max_iter`）用 `❌ N/A`；新增「**消费侧 disambiguation 规则**」段落告知解析方应当**同时**读 `(turn_end_reason, at_max_iter, outcome)`，而不是只看 `outcome`。为让规则真正可执行，把 `turn_end_reason` 加进 `_emit_stop_hook_fired` helper 签名与 emit dict；B3 现有八个调用点（自然 turn 4 + max-iter 4）全部更新；doom-loop 小节里替换列表新增一条 bullet 告知实现者四个 doom-arm emit 都传 `turn_end_reason="doom_loop"`。

**保留 vs 删除决策：** 保留 `continue_at_max_iter` 作为独立 label，不合并成 `continue`+`turn_end_reason`。理由：它在前面三轮里已经被记录与测试过；现在改 label 方案会让半打测试断言失效、并改变可能已被某些 host 接进 dashboard 的 wire-format 语义。doom-loop 位置用 `continue` + `turn_end_reason="doom_loop"`，因为没有任何 prior commitment 到 `continue_at_doom_loop` 这个 label，且把 enum 控制在 5 个、用 `turn_end_reason` 作为「**哪个出口**」的判别字段——这是面向未来出口扩展的形态。

**没有设计变更 —— 评审十六轮把评审十五轮起头的 doom-loop 集成接完。** Literal 类型修复是机械的；outcome 表重写让「唯一权威定义」声明真正成立；helper 签名变更让消费方能据此 disambiguation 规则做事。

**rev 2026-05-05 —— 评审十五轮（第三个 loop 出口 —— doom-loop break —— 被「死代码删除」声明孤立）。** 一项 P1 发现已落入计划；订正之前各稿都漏看的实施级危险：

1. **`while True` 有**三**个出口，不是两个；doom-loop break（`chat_loop.py:271-272`）在 B3 接线与 A4 emit 表中都缺席。** 评审十五轮之前 B3 写：「`if iteration >= max_iterations` 的每个分支要么 continue 要么 return，post-while finalization（`chat_loop.py:308-324`）变成不可达。PR-2 顺手删除那段。」这句**不成立**——`chat_loop.py:271-272` 还有 `if doom_triggered: break`，这是第三个出口：**不**在 `iteration >= max_iterations` 块内，**不**是自然-final-response `else` 分支，当下依赖 post-while finalization 来 commit `final_msg` 并 return。如果按计划删除 post-while，doom-loop 触发时返回路径会失踪——`run()` 会从函数尾部掉出来、返回 `None`。两种修法可选：(a) 留一个 doom 专用的 post-while fallback（不对称、削弱「PR-2 删那段」的声明）；(b) doom 也在 loop body 内联处理，三个出口完全对称、post-while 真正变成不可达。**选 (b)**，与现有自然 turn / max-iter 形态对称。

   具体新增：
   - **「语义」一节** 现在列三个 Stop emit 位置（新增 doom-loop bullet），`turn_end_reason` 段落点名三个 discriminator 值。
   - **A3 builder 签名** 类型化为 `turn_end_reason: Literal["final_response", "max_iterations", "doom_loop"]`。
   - **A4 Stop emit 表** 多加一行：doom-loop break 在 `chat_loop.py:271-272`、`turn_end_reason="doom_loop"`、`last_assistant_message` 在 assistant_message 内容为空（产生违规 tool_calls 时常态）下回退到 `"Tool execution halted by doom-loop detection."`。
   - **B3** 新增「doom-loop dispatch 位置」小节，对照 max-iter 形态做三处替换（`assistant_content` → `assistant_content_doom`、`final_msg` → `final_msg_doom`、`at_max_iter=False`），并显式说明 **doom 计数器重置**是 ToolRunner planner 自己的状态、**不**由 chat loop 越权重置；在 doom 位置兑现 `force_continue` 可能再次撞 doom，但 re-entry 封顶最终会终止。
   - **B3「结果——死代码删除」** 现在如实列**三**个生还出口，PR-2 删 post-while 不再说谎。
   - **B7 outcome 枚举** 不变 —— doom 位置与自然 turn 共用同样五个 label（**不**用 `continue_at_max_iter`，因为 `at_max_iter=False`）。
   - **B6** 新增 `test_hooks_stop_doom_loop_dispatch.py`，六个子用例钉住：payload `turn_end_reason`、兜底 last_assistant_message、emit 上 `at_max_iter=False`、`final_msg_doom` commit、doom 处兑现 force_continue（outcome="continue"）、doom 处封顶（outcome="reentry_capped" + WARNING 文案）。

**没有设计变更 —— 评审十五轮订正一处真实的出口缺漏**，不是契约变更。第三个位置与另外两个共享同样的 Stop 语义；唯一新增的词汇是 `"doom_loop"` `turn_end_reason` 值与 `final_msg_doom` 兜底字符串。

**rev 2026-05-05 —— 评审十四轮（replay 发射 gate + suppressOutput stdout 声明订正）。** 两项发现已落入计划；都是把上一稿宣称、但实际**没真正交付**的不变量补到位：

1. **P1 —— 没有任何 plugin 规则匹配时，`_dispatch_stop` 返回空 `StopHookResult()`，B3 在每个终止分支照常 emit `PLUGIN_HOOK_FIRED`。** 两种失败模式：(a) `agent._plugin_hook_rules == []` —— helper 仍返回 result；B3 仍调 `_emit_stop_hook_fired(..., outcome="allow", ...)`；replay 通道收到一条来自零 hook 代码回合的吵闹 `hook_name="Stop", outcome="allow"` 事件。(b) 规则非空但没有任何条针对 Stop —— `dispatch_stop` 过滤后 `stop_rules` 为空、返回空 result；B3 仍 emit；同样吵闹。现有 `_dispatch_user_prompt_submit`（`chat_loop.py:332-333`）通过「先早返回、后 emit」躲掉了 (a)，但看不到 (b)，因为规则列表本身非空。解法：在 `StopHookResult` 上加新字段 `matched_rule_count: int = 0`（B1），由 `dispatch_stop` 在 run loop **之前**赋值为 `len(stop_rules)`（让单条规则因 `blocking_error`/`force_continue` 短路时也能拿到正确计数），并把发射 gate 放进 `_emit_stop_hook_fired`：`stop_result.matched_rule_count > 0` 才发（B7）。emit payload 顶层也新加 `matched_rule_count` 字段供 replay 观察。新增回归测试 `test_hooks_stop_no_emit_when_no_stop_rules.py` 覆盖三种子用例（无规则 / 规则非空但无 Stop / 有 Stop 规则）。
2. **P2 —— `suppressOutput` 声称会隐藏 `PLUGIN_HOOK_FIRED.stdout`，但 Agentao 从未在那里 emit `stdout` 字段。** B1 docstring 写「Claude-parity 部分把 hook 原始 stdout / debug-log 从 user-visible transcript 与 `PLUGIN_HOOK_FIRED.stdout` 隐藏」；矩阵「JSON `suppressOutput`」行写「相应的 `PLUGIN_HOOK_FIRED` payload 不带 stdout 正文」。`_emit_stop_hook_fired` 的 body 只发 `hook_name`、`outcome`、`at_max_iter`、`matched_rule_count`、`added_context_count`、`suppress_output` —— 根本没有 stdout 字段、从来都没有，`StopHookResult` 也没有 raw stdout 字段。两条修法可选：(i) 给 result 加 stdout / raw-output 字段并据此 gate；(ii) 老实记录 Claude-parity 部分在该通道当下是真空的。选了 (ii) —— 加 stdout 投影会撑爆范围、把可能含敏感信息的 subprocess output 默认推到 replay 通道，并让矩阵原本已是 🟡 的行漂成一个新特性。矩阵第 68 行改写为「在原始 stdout 通道上当下是真空兑现。Agentao **不**把 hook stdout 投影到 `PLUGIN_HOOK_FIRED`（emit 只携带 verdict + 计数）……」，状态从「✅（仅指原始 stdout 这一层语义）」降级为「🟡 —— 当前在该通道真空；字段忠实记录但当前不 gate 任何展示路径」。B1 docstring 同步改写：Claude-parity 部分注明「**在 Agentao 当下这是真空兑现**」，并显式列出 emit 真正携带的字段。Agentao 扩展（gate `additional_contexts` 回显）部分**不变** —— 它仍是 `suppress_output=True` 当下产生的**唯一**具体行为。

**没有设计变更 —— 评审十四轮把两条「计划在说但实际没交付」的伪不变量订正到位。** 发现 #1 引入新字段 `matched_rule_count`，但只为记录与强制计划已宣称的契约（「emission 留在 B3，因为 outcome label 取决于调用方分支」—— 一个跑了 0 条 Stop 规则的回合悄悄发 `outcome="allow"` 与那条契约的精神相违）。发现 #2 是把文档对齐到代码，不是反过来。

**rev 2026-05-05 —— 评审十三轮（B7 helper 实现 vs 真实 chat_loop / hooks / transport 表面）。** 三项 P1 发现已落入计划；全部属于「helper body 伪代码 vs 真实接口」的漂移，照抄会在第一次 hook dispatch 时直接抛运行时错：

1. **`_dispatch_stop` 引用了 `self._adapter` / `self._dispatcher` / `self._plugin_hook_rules` —— `ChatLoopRunner` 上根本没有这三个属性。** 评审十一轮把 helper 提到 B7 时引入了这些属性引用，但 `ChatLoopRunner.__init__(agent)`（`agentao/runtime/chat_loop.py:112-113`）只存 `self._agent`。现有 `_dispatch_user_prompt_submit`（`chat_loop.py:330-348`）的做法才是对的：在方法内部 lazy import `ClaudeHookPayloadAdapter` 与 `PluginHookDispatcher`、把它们当局部变量实例化（`adapter = ClaudeHookPayloadAdapter()`、`dispatcher = PluginHookDispatcher(cwd=cwd)`）、规则列表读 `agent._plugin_hook_rules`。B7 的 `_dispatch_stop` body 现在原样照抄这种模式 —— 否则把它复制进 chat_loop.py、第一次 hook dispatch 就会 AttributeError。
2. **`build_stop(...)` 被以 `transcript_path=None` 调用，但 A3 builder 签名根本没有这个参数。** A3 `build_stop(self, *, session_id, cwd, last_assistant_message, stop_hook_active, turn_end_reason, permission_mode)`（行 396-399）在 dict 字面量**内部**写死 `"transcript_path": None` —— 没有对应入参。评审十一/十二轮的 helper body 把 `transcript_path=None` 当 kwarg 传，第一次调用就会 TypeError。helper body 删除该 kwarg；加内联注释指出「该字段由 builder 独占，OQ1 (a) 决议」。Builder 签名保持不变（字段值的唯一权威点）。
3. **`_emit_stop_hook_fired` 写成 `agent.transport.emit(EventType.X, {...})`，但 Transport 协议是 `emit(self, event: AgentEvent)`。** `agentao/transport/base.py:28` 的协议只接受单个 `AgentEvent` 参数；把 type 和 dict 分开传就是类型错误。`chat_loop.py` 现有所有 emit 位置（如行 360 的 UserPromptSubmit hook）都是包成 `AgentEvent(EventType.PLUGIN_HOOK_FIRED, {...})`。helper body 改为包装。再加 `try/except Exception: pass` 兜底，以遵守协议的「不得抛异常」契约 —— 与 `chat_loop.py:368-369` 现有 UserPromptSubmit emit 形态一致。imports 说明：`AgentEvent` 与 `EventType` 已经为 `chat_loop.py` 现有 emit 位置 import 过，不需要新增 import；helper 代码块顶部显式说明这一点。

**无设计变更——评审十三轮让 helper 实现真的能在真实接口（`ChatLoopRunner` 属性、`build_stop` 签名、`Transport.emit` 协议）上跑通。** 不修这三处，评审十一轮的 helper 代码孤立地看像样，但 Stop hook 一发就会过不了三道运行时检查。

**rev 2026-05-05 —— 评审十二轮（B3 可调用面 + 发射可见性 + max-iter cap 对称性）。** 三项发现已落入计划；全部属于「规范 vs 伪代码」漂移、若不修就会写出错代码：

1. **B3 调用点的 `payload_for_stop` 是未定义符号。** B3 自然 turn 伪代码（约 686 行）与 max-iter 伪代码（约 782 行）都直接 `self._dispatch_stop(payload_for_stop, ...)`，前面没有任何 `payload_for_stop = ...` 的赋值。A4 说 helper 内部构造 payload；评审十一轮的 B7 又把 helper 签名写成 `_dispatch_stop(payload, assistant_content, *, at_max_iter)`、把 payload 当入参。两处自相矛盾，实现者只能猜测「到底谁负责构造」。解法：合并到 A4 契约——helper 签名改为 `_dispatch_stop(self, agent, assistant_content, *, turn_end_reason, at_max_iter)`，内部调用 `self._adapter.build_stop(...)`。A4 「helper 构造 payload」从此是计划中**唯一**的 payload 构造点；B7 给出 helper 完整实现。B3 调用点改为传 `turn_end_reason="final_response"` / `"max_iterations"` —— 文档里再没有 `payload_for_stop` 这个变量。
2. **B3 伪代码全程没有 `transport.emit(...)` —— 实现者会**完全漏掉** B7 的发射要求。** 评审十一轮在散文里把发射归属移到 B3，但 B3 代码块里一行 emit 都没有。从上往下读 B3 的实现者会写出「每个终止分支 return / continue、零次 PLUGIN_HOOK_FIRED」的代码，然后被 `test_hooks_stop_event.py` / `test_hooks_stop_reentry_cap.py` 等 transport 事件断言打回。B3 现在在每个 `return` / `continue` **之前**显式调用 `self._emit_stop_hook_fired(agent, outcome="...", at_max_iter=..., stop_result=...)`。B7 同时定义了 `_emit_stop_hook_fired` 小 helper，让发射 payload 形态只有一个来源。五个 outcome × 两侧（自然 turn + max-iter，allow 路径上 `additional_contexts` 装饰共用一个发射点）—— 全部以具体调用位置呈现。
3. **max-iter cap-hit 是隐式 fall-through，与自然 turn 路径不对称。** B3 自然 turn 块有显式 `if self._stop_reentries >= self._stop_reentry_cap:` 分支，会 log WARNING 并发 `reentry_capped`。max-iter 块以前用 `if force_continue and reentries < cap:`，让 cap-hit 静默落到 「Allow OR cap-hit」 这条返回路径上。意思是：(a) max-iter cap-hit 不发 WARNING（与自然 turn 不对称）；(b) 在评审十二轮 #2 之前，cap-hit 路径**也不发** `PLUGIN_HOOK_FIRED`（被 allow tail 吞掉）；(c) 注释 「Allow OR cap-hit（cap-hit 经 B7 走 outcome=reentry_capped）」 是谎言——后面没有任何代码区分这两者。解法：max-iter 的 `if stop_result.force_continue:` 内部现在显式 `if reentries >= cap:`，里面 log WARNING 并 `_emit_stop_hook_fired(..., outcome="reentry_capped", at_max_iter=True, ...)`，与自然 turn 完全对称。原本那条悬挂的 「Allow OR cap-hit」 注释删除；尾部路径改名为 「Allow 路径（无 force_continue）」。

B7 outcome 表的 `reentry_capped` 行同步更新——从旧的「max-iter cap-hit 落到 allow 路径前的位置」改为「B3 现在显式写出的 `if force_continue and reentries >= cap` 分支」。

**没有设计变更——评审十二轮把已经决定的事让伪代码追上来。** 三项发现都是「伪代码 vs 散文/契约」的对齐问题；契约不变，伪代码补上。

**rev 2026-05-05 —— 评审十一轮（B7 发射归属 + Semantics 字段名 + A6 测试 schema）。** 三项发现已落入计划；全部是「规范 vs 实现」的真实漂移，不修就会在 PR-2 时落出错代码：

1. **`PLUGIN_HOOK_FIRED` 发射点从 `_dispatch_stop` helper 移到 B3 各终止分支。** 评审九轮把 `_dispatch_stop` 串成 Stop dispatch 的唯一 helper，评审七轮的 B7 让它顺便 populate `PLUGIN_HOOK_FIRED`。但 helper 只能看到 hook **请求**了什么（`force_continue`、`blocking_error`）；`continue` / `continue_at_max_iter` / `reentry_capped` / `allow` 这四个 label 取决于：调用方是自然 turn 位置还是 max-iter 位置，**以及** `_stop_reentries < _stop_reentry_cap` 是否还成立。两个判断都在 helper 之外。B7 现在显式拆分职责：`_dispatch_stop(*, at_max_iter)` 返回 `StopHookResult`；chat-loop 接线在 B3 的每个终止分支用正确的 outcome label emit `PLUGIN_HOOK_FIRED`。这也让 helper 能孤立单测（B6 dispatcher 测试不再需要起 chat-loop 实例）。两条 B6 测试新增 outcome label 断言，防止这次拆分被回退。
2. **Semantics 一节的 `turnEndReason`（camelCase）→ `turn_end_reason`（snake_case）。** A3 payload 字段表、builder 代码、A4 emit 表、A6 payload-shape 测试都已用 snake_case（与评审十轮的对齐一致）。Semantics 第 34/35/37 行仍写 `turnEndReason`，与 A3 「Claude-flat 顶层 snake_case key」契约相悖。从上往下读的实现者会把 camelCase 写进 payload builder。EN 三个 Semantics 位置都已修复；ZH 同步。
3. **A6 的 `test_hooks_stop_precompact_reject_prompt_type.py` 改用真实 `hooks.json` schema。** 评审五轮的描述用了伪记法 `{event: "Stop", hook_type: "prompt"}`，但若按字面 JSON 解读，**没有** `"type"` key —— `entry.get("type", "")` 会返回 `""`、规则会落到现有的 `"Unknown hook type"` 分支，**不**是 A1 新加的按事件 `SUPPORTED_HOOK_TYPES_BY_EVENT` 分支。原始测试在 A1 改动之前就能通过，无法证明 A1 真的命中。重写为：喂 `parse_dict` 真实形态（`{"hooks": {"Stop": [{"type": "prompt", ...}]}}`），断言 (a) `rules` 为空；(b) warning 文本**同时**点名事件名与类型（与通用 fallback 区分）；(c) `field == "hooks"`；外加纵深防御子用例：直接构造 `ParsedHookRule` 并断言 `is_supported is False`。原样镜像到 ZH。

无代码或行为变更 —— 发射归属澄清（#1）属于纯规范修正（B3 已经持有 cap-check 逻辑；helper 不该自称发射）。#2 与 #3 都是文档对齐、防止错代码被写出。

**rev 2026-05-05 —— 评审十轮（字段名 + parser 分支 + B1 注释对齐）。** 三项发现已落入计划；全部属于「文档/代码规范不对齐、会让实现者白白浪费时间」类问题：

1. **当前式叙述中统一使用 `compaction_type`（snake_case）。** Semantics 一节与 A4 emit 表里出现的是 `compactionType`（camelCase），而 A3 payload 字段表、builder 代码、A6 payload-shape 测试都已经用 `compaction_type`。这种不一致违反了 A3 的「Claude-flat 顶层 snake_case」契约，会让实现者在 emit 位置写 `compactionType`、第一次跑 A6 测试就挂掉。修复 EN Semantics 第 39 行、A4 表头、A4 散文段；ZH 同三处。历史修订备忘保持不动 —— 它们如实记录写作当时的字段名状态。
2. **parser 侧的按事件检查现在显式写出，而不仅是 `is_supported`。** 评审五轮加了 `SUPPORTED_HOOK_TYPES_BY_EVENT` 并扩展运行时 `is_supported` 谓词。但现有 parser 在 `agentao/plugins/hooks.py:120-140` 只查裸 `SUPPORTED_HOOK_TYPES`，所以一条 `{event: "Stop", type: "prompt"}` 的规则会以 `is_supported == False` 进入 `rules` —— 运行时悄然空转，与矩阵「Rejected at parse time」行以及 A6「parser logs warning」断言相违。A1 现在在裸类型检查**之后**、`rules.append` **之前**显式加一段：查 `SUPPORTED_HOOK_TYPES_BY_EVENT.get(event_name, SUPPORTED_HOOK_TYPES)`、emit `PluginWarning`、`continue`。运行时 `is_supported` 保留为纵深防御（与 A2 运行时 matcher guard 同角色）。A6 的 prompt-type 拒绝测试断言**解析期 drop**，不仅是运行时 flag 翻转。
3. **B1 scratch-field 注释改写以对齐 B2 现实。** 评审二轮写下「`messages` 和 `prevent_continuation` 存在是为了让 `_run_command_hook` / `_parse_command_output` 复用而不抛 `AttributeError`」，但评审四轮的 B2 已**显式 fork** Stop 专用 runner（`_run_stop_command_hook`）与 parser（`_parse_stop_command_output`），原因正是「复用 UserPromptSubmit 代码会把 Claude exit-code-2 契约悄悄降级」。B1 注释成了过期信息，会把实现者引导到（已被否决的）复用路径。改写为：`messages` 承载 Stop 专用 runner 产生的附件；`prevent_continuation` 是 parser 对 legacy `preventContinuation: true` JSON 的写入容忍位；两者**都不被 chat-loop 接线消费**。

无代码或行为变更 —— 纯文档/规范对齐，让 PR-1 / PR-2 实现者不会被陈旧字段名、缺失的 parser 分支、或指向已废弃 runner 复用路径的过期注释带偏。

**rev 2026-05-05 —— 评审九轮（文档一致性 + 测试设计）。** 四项发现已落入计划；本轮未引入新行为决策，但闭环三处真实的文档/测试 gap，并清理一处遗留措辞：

1. **PreCompact matcher 行拆分：运行时 ✅ vs 配置形态 🟡。** 评审六轮把 matcher 行写成 ✅，混淆了两个问题：(i) regex 求值器跑得起来吗？—— 跑得起来，`re.fullmatch` 对 Agentao 形态的 matcher dict；(ii) 把 `"matcher"` 写成顶层字符串的 Claude `hooks.json` 能加载吗？—— 不能，A1/A2 要求对象形态，评审八轮把字符串 matcher 改为解析期丢弃。矩阵现在并列两行：「Matcher（PreCompact）—— 运行时 regex 求值」✅（假定 matcher 是 Agentao 对象形态），与「Matcher（PreCompact）—— 配置文件形态」🟡（字符串 matcher 解析期丢弃；归到已有的配置形态 ❌ 行）。这避免 Claude 迁移方读到「PreCompact matcher ✅」却惊讶地发现自己的 `"matcher": "auto"` 规则被悄悄丢掉。
2. **`dispatch_stop` 签名 A→B 变更已文档化 + A6 测试改写已显式列出。** A2 声明 `dispatch_stop -> list[HookAttachmentRecord]`；B2 重新声明为 `-> StopHookResult`。A6 测试 `test_hook_dispatcher_stop.py` 是按 A 签名写的，PR-2 下编译不过。计划现在显式：(a) 在 A2 代码块里点出签名变更（「Phase B 会替换 dispatch_stop 的返回类型」），并在 B2 新增「测试影响」一段；(b) 在 PR-2 的 Sequencing 项中列出测试改写。`dispatch_pre_compact` **不**受影响（PreCompact 仍 observe-only —— B5）。破坏面被局限在 Agentao 内部（无 host 公开 API）。
3. **`stop_hook_active` 接到 `_stop_reentries` + B6 re-entry 测试已加。** A3 的 payload 字段表声明 `stop_hook_active` 在同一 `chat()` 调用的第二次及之后 dispatch 翻 false→true，但 B3 伪代码从未展示接线、B6 也只校验 key 存在。A4 Stop emit 子节现在写明 `stop_hook_active = (self._stop_reentries > 0)` 在 dispatch helper 中的接线，B6 新增 `test_hooks_stop_hook_active_reentry.py` 覆盖：(a) 首次 dispatch = `False`；(b) `force_continue` 后再次 dispatch = `True`；(c) 计数器重置后新一次 `chat()` = 又 `False`。这条测试给「字段声明」上了保险，而不是把它留作未守卫的承诺。
4. **遗留「三条 ❌ 行」/「Stop 与 PreCompact 的 prompt/agent」当前式措辞已修正。** 评审八轮把 PreCompact prompt/agent 从 ❌ 改为 N/A（Claude 自己也不支持）。但两条**当前式**陈述仍写着「有意保持 ❌ ……Stop / PreCompact 的 prompt/agent hook 类型」（矩阵前言）与「矩阵中三条……拒绝 Stop 与 PreCompact 的 prompt/agent」（理由小节开头）。两处都改成「仅针对 Stop」。历史修订备忘（评审六/七轮）保留原样 —— 它们准确反映写作当时的状态，而评审八轮的勘误已单独记录。

**rev 2026-05-05 —— 评审八轮（parser 正确性 + 矩阵前提勘误）。** 五项发现已落入计划；本轮修正早期草稿引入的两条真实 bug 与矩阵中三条对 Claude 文档的误读：

1. **解析期 matcher 修复不再反转 warning 的本意。** 评审六轮加的 parser 分支在检测到非 dict matcher 时把 `matcher = None`，warning 写「rule will not match」。但运行时契约在 `agentao/plugins/hooks.py:394` 是 `if rule.matcher is None: return True` —— 即 `None` ≡「匹配每个事件」。六轮的修复会把一条配错的过滤器**默默变成「匹配一切」**的规则。八轮把 parser 改为**整条规则丢弃**（跳过 `rules.append` 直接 `continue`），对齐 Claude Code「坏规则不会加载」的语义。`_matches` 顶部的运行时 guard 现在是真正的纵深防御（仅在「将来某条代码绕开 loader 直接构造 `ParsedHookRule`」时触发）。A6 测试改为断言**规则没有加载**，并显式注明早期草稿那条「规范化为 None」就是这条 guard 要防住的 bug。
2. **parser warning 用 `PluginWarning`，不是裸 f-string。** loader 的 `warnings` 列表类型是 `list[PluginWarning]`（`hooks.py:82`）；六轮的 `warnings.append(f"...")` 会破坏类型。八轮明写 `PluginWarning(plugin_name=plugin_name, message=..., field="hooks")`，与 loader 中其它 warning 发出点保持一致。
3. **兼容性矩阵新增 `http` 与 `mcp_tool` hook-type 行。** 早期评审只列 `command` / `prompt` / `agent`。Claude Code 文档化的 hook-type 集合是 `command` / `http` / `mcp_tool` / `prompt` / `agent`；Stop 支持全部五种，PreCompact 支持 `command` / `http` / `mcp_tool`。Agentao 仅识别 `command` / `prompt`，`http` 与 `agent` 在 `KNOWN_UNSUPPORTED_HOOK_TYPES`，`mcp_tool` 完全不识别。矩阵现在列出 `http`（Stop、PreCompact）❌ 与 `mcp_tool`（Stop、PreCompact）❌ 作为预先存在的 Agentao gap，并指出 `mcp_tool` 需要新 runner 桥接到 `agentao/mcp/client.py`。从 Claude `hooks.json` 迁移的 host 现在能看到完整的「哪些 hook type 会加载」画面。
4. **PreCompact `prompt`/`agent` 行从「我们说不」改为「两边都不支持」。** 早期评审把这些行写成「Claude 支持，Agentao 选择不做」并配了专门的理由小节。这个前提**是错的**：Claude 文档化的 hook-type 矩阵列出 PreCompact 仅支持 `command` / `http` / `mcp_tool` —— `prompt`/`agent` 在 PreCompact 上**不是 Claude 特性**。矩阵现在标 `N/A —— 不构成兼容性 gap`（保留行只为完整性）。「为什么 Stop / PreCompact 不支持 prompt 型 hook」一节中的 PreCompact 子节已重写承认这条勘误；Stop 子节仍然成立。
5. **`suppressOutput` 从 ✅ 拆为两行（✅ + 🟡）。** 矩阵现在并列两行：「Claude 语义 —— 隐藏原始 stdout / debug-log」✅，与「Agentao 扩展 —— gate `additional_contexts` 在助手最终回答上的回显」🟡。Claude 文档下的 `suppressOutput` **仅**覆盖原始 stdout / debug-log；结构化的 `hookSpecificOutput.additionalContext` 是另一条通道。B3 对 `<stop-hook>` 回显的 gate 是 Agentao 自家的扩展、不是 parity。B1 docstring + B3 接线注释都改写承认这点；矩阵告知想要严格 Claude 语义的 host：把 `suppressOutput` 与 `additionalContext` 拆到不同 hook output 上。

**rev 2026-05-05 —— 评审七轮（prompt 型拒绝的理由成文）。** 无新发现；本轮把评审五/六轮中「Stop / PreCompact 上 prompt / agent 型 hook 在解析期被拒绝」从一条**有记录的行为**升格为**有记录的决定**。在兼容性矩阵与 Phase A 之间新增顶层一节「为什么 Stop / PreCompact 不支持 prompt 型 hook」，说明：

- **Stop：** 与 `command` 型 hook 在能力上重复。reviewer 用例完全由「内部调 LLM、emit Claude Code Stop JSON」的 command-hook shim 覆盖；原生支持 prompt 型会迫使 Agentao 定义第三个 Stop 控制面（直接注入会话），而 Claude 文档本身**没有**把自由文本回复映射到结构化 output schema 的标准答案。失去的移植性被矩阵已存在的「Hook 配置文件路径 / 形态」❌ 行吞掉。
- **PreCompact：** 在我们的 observe-only PreCompact 契约下（B5），prompt-hook 的回复**无目的地**：不能 gate 压缩、不能改向压缩，而用 LLM 调用产生审计信号是错的工具（`PLUGIN_HOOK_FIRED` 已经覆盖观察）。仅当 PreCompact gating 真的落地（当前在单独的 `PRECOMPACT_GATE_PLAN.md` follow-up，不在 roadmap 上）时再回头看。

矩阵中三条 prompt/agent Stop 与 PreCompact 的 ❌ 行现在指向这一节，并改写为「**有意为之**，不是『还没做』」。Out-of-scope 中的 prompt-type 条目同样链接到这一节。无代码或测试改动 —— 本轮纯属文档正确性收口，让「为什么」与「是什么」并列可见。

**rev 2026-05-05 —— 评审六轮（矩阵完整性 + 优先级 + 类型安全）。** 四项发现已落入计划；未撤销任何更早的决定。本轮收口兼容性矩阵中遗漏的项，并把两条之前隐含的契约钉死：

1. **Stop / PreCompact 的 prompt / agent hook 类型在矩阵上有显式 ❌ 行。** 评审五轮加了解析期拒绝（A1 的 `SUPPORTED_HOOK_TYPES_BY_EVENT`），但矩阵没列出后果。Claude Code hooks reference 明确支持 prompt 型 Stop hook（且给了示例），因此一份带 `{event: "Stop", hook_type: "prompt"}` 的 Claude `hooks.json` 在 Agentao 上会**默默加载失败**。新增三行 ❌（Stop prompt、Stop agent、PreCompact prompt/agent），把它列为 load-time 不兼容，并指出绕开方法（把 prompt 改成 emit `additionalContext` 的 command shim）。
2. **`permission_mode` 从 ✅ 降为 🟡，值空间分歧已记录并跟踪。** 矩阵之前对 common input 整体打 ✅，但 Agentao 取值（`"read-only" | "workspace-write" | "full-access" | "plan"`）只与 Claude Code（`"default" | "plan" | "acceptEdits" | "auto" | "dontAsk" | "bypassPermissions"`）共享 `"plan"` 一项。按 `permission_mode == "acceptEdits"` 分支的 Claude 脚本在 Agentao 上会走错分支。矩阵拆为两行（key 形态 ✅、值空间 🟡），新增 Open Question 5 列出三个映射选项（原样发出 / 翻译到 Claude 词汇 / 双发字段），Phase A 选「原样发出 + 文档化分歧」；该 Open Question 显式禁止「悄悄翻译」。
3. **`continue: false` 优先级在 B2 钉死，B6 加测试。** Claude Code 文档明确 `continue: false` 优先于事件专属 decision 字段。B2 之前的 parser 表把 `decision: "block"` 与 `continue: false` 平铺、未规定顺序，因此 hook 返回 `{"continue": false, "decision": "block"}` 可能先把 `force_continue=True` 写下、永远到不了 `continue: false` 那行。B2 现在加上「顺序」列，第 1 行是 `continue: false`，置一个 `continue_false` scratch 标志，压制后面所有会写 `force_continue` 的分支（`decision: "block"`、`preventContinuation`）。新不变量 #3 把规则写明；新 B6 测试 `test_hooks_stop_continue_false_precedence.py` 覆盖三个组合（block、preventContinuation、blockingError），按文档预期断言。`blockingError` **有意**不被 `continue: false` 压制 —— 两者都是「停」语义，已在表中和测试中明确。
4. **matcher 非 dict 类型 guard。** 加载器在 `hooks.py:161` 把 `entry.get("matcher")` 直通进去；A2 的 `rule.matcher.get("trigger")` 在字符串 matcher（例如某个 Claude config 翻译层把 `{"trigger": "auto"}` 拍扁成 `"auto"`）下会 `AttributeError`。两层防御：(i) A1 加载器补丁在解析期 warn 并把非 dict matcher 折叠为 `None`；(ii) `_matches` 顶部加运行时 guard，warn 后返回 `False`（不匹配），让将来若有代码绕开 parser 也不会崩。新增 A6 测试 `test_hooks_pre_compact_matcher_non_dict_guard.py` 三个子用例（字符串 matcher、list matcher、parser-bypass）覆盖两层。

**rev 2026-05-05 —— 评审五轮（Claude Code 兼容正确性）。** 五项发现全部已验证、已落入计划；未撤销任何更早的决定，只是收紧了既有兼容性声明：

1. **`suppressOutput` 在 B3 接线里现在真的兑现了。** B1 声明了字段、B3 自然 turn 的 allow 路径却无条件 append `<stop-hook>` 块。已在 echo 块外加 `not stop_result.suppress_output` 判断（B3 自然 turn 位）。max-iterations 出口路径**不**回显 `additional_contexts` —— 这条不对称已在文档化、并为将来若加上回显预先约束 gate。
2. **PreCompact matcher 用 regex（`re.fullmatch`），不再是 glob。** A2 之前把 `rule.matcher["trigger"]` 走 `_glob_match`（`hooks.py:832-844`），而该 helper 不支持 regex alternation：Claude 风格的 `manual|auto` 模式会被悄悄判为不匹配。新增 PreCompact 局部 `_regex_match_full` helper 用于 `trigger` 字段；其它事件的 `toolName` matcher 保持 `_glob_match`（按事件分方言，A2 已记录）。A6 的 `test_hooks_pre_compact_matcher_trigger.py` 现在覆盖四个 case（`"manual"`、`"auto"`、`"manual|auto"`、`".*"`）—— 后两个在 `_glob_match` 下都会失败。
3. **PreCompact 的 `reason` 取值与 `_emit_context_compressed` 实参对齐。** A4 之前把两个阈值位都写成 `"size_threshold"`，而 `chat_loop.py:413` 实际是 `"microcompact_threshold"`、`chat_loop.py:443` 是 `"compression_threshold"`。A3 写下的不变量（「`reason` 镜像现有实参」）现在在 A4 表里真的成立；A3 的稳定值列表也同步修正。`"api_overflow"` 与 `"api_overflow_after_compression"` 本就正确。
4. **Stop / PreCompact 的 prompt-type hook 在解析期就被拒绝。** `SUPPORTED_HOOK_TYPES = {"command", "prompt"}`（`models.py:207`）加 `is_supported = hook_type in SUPPORTED_HOOK_TYPES and event in SUPPORTED_HOOK_EVENTS`（`models.py:226`），原本会把 `{event: "Stop", hook_type: "prompt"}` 判定为 supported，然后被 `_dispatch_lifecycle` 静默丢弃（只走 command 分支）。A1 引入 `SUPPORTED_HOOK_TYPES_BY_EVENT` 并扩展 `is_supported`；A6 的测试验证拒绝行为 + parser warning。
5. **B6 覆盖 B2 声称支持的所有 Claude JSON output 字段。** 评审四轮把 `suppressOutput`、`systemMessage`、`hookSpecificOutput.additionalContext`、exit code 2 加进矩阵和 B2 parser 表，但 B6 只测试了 `decision: "block"` + `blockingError` + 重入封顶。B6 新增五条测试（A6 还加一条 common-fields 优先级测试），让矩阵里每一行 ✅ 都有直接的测试覆盖。这条尤其重要——评审五轮的发现 #1 已经证明：「声明支持」与「在代码里接通」可以脱钩。

**rev 2026-05-05 —— 评审四轮（Claude Code 兼容性转向）。** 产品目标更新为：按 Claude Code Stop / PreCompact 写的 hook 脚本，在 Agentao 里**原样可用**。六项发现已落入计划；并**撤销**评审二轮的一项决定：

1. **撤销评审二轮的 #2**（「payload 信封锁定到现有 adapter 形态；Claude flat schema 不在范围」）。仅针对 Stop 与 PreCompact，wire 形态改为 Claude Code 顶层平铺 snake_case schema（A3）。其它 adapter 方法（UserPromptSubmit / SessionStart 等）仍走 agentao `{event, data}` 信封——全面改造仍不在范围。`_matches` 扩展（A2）处理双形态，新增的「Claude Code 兼容性矩阵」一节记录这种不对称。
2. **新增「Claude Code 兼容性矩阵」一节。** 权威 ✅ / 🟡 / ❌ 表，覆盖事件名、wire input、通用输入字段、exit code、JSON output 字段、matcher、decision/gate 语义、配置文件形态。A3、A4、A6、B1、B2、B5 据此实现。
3. **Stop payload 增加 `last_assistant_message`**（A3、A4）。从 `assistant_content`（自然 turn）与 `assistant_content_max`（max-iter）注入，发生在它们 append 进历史**之前**。这是 Stop hook 的首要用例（审最终回答而不解析 transcript），原稿缺失。
4. **Stop 兑现 exit code 2（B2）。** Stop dispatcher 单 fork 一个 `_run_stop_command_hook`；复用 `_run_command_hook` 会把 `exit 2` 悄悄降级为 warning attachment（`hooks.py:520-533`）。Stop runner 把 `exit 2 + stderr` 翻译为 `force_continue` + `follow_up_message`，对齐 Claude Code 文档化契约。
5. **Stop JSON parser 覆盖 Claude Code 完整 Stop output schema（B2）。** `decision: "block"` + `reason`、`continue`、`stopReason`、`suppressOutput`、`systemMessage`、`hookSpecificOutput.additionalContext` 全部映射进 `StopHookResult`。`StopHookResult` 增 `suppress_output` 与 `system_message` 字段（B1）。Legacy 顶层 `additionalContext` 与 Agentao 内部 `blockingError` / `preventContinuation` 仍容忍。
6. **PreCompact matcher 扩展（A2）。** Claude Code 用 `trigger ∈ {manual, auto}` 做 PreCompact 规则匹配；`_matches` 之前只读 `toolName`。Phase A 扩展。`trigger="manual"` 永不发出（无 `/compact` CLI），manual matcher 规则永不命中——矩阵标 🟡，A6 加专门测试。
7. **PreCompact blocking 改写为 Claude Code 兼容性 gap（B5）。** 之前的「deferred」措辞过于乐观——没有把 PreCompact gate 加进 roadmap 的计划，因为「host 拒绝、仍然超长」是另一个独立的设计讨论。host **不能**在没有显式验证的前提下假设 Claude PreCompact `decision: "block"` 脚本在 Agentao 里有 gate。

**rev 2026-05-05 —— 评审三轮。** 四项后续发现已落入计划：

1. **`force_continue` 不再因 `follow_up_message` 为空而漏出（B2、B3）。** 评审二轮的 `preventContinuation` 翻译会写 `force_continue=True` 但留空 `follow_up_message`，而 B3 检查 `force_continue and follow_up_message`，导致翻译被悄无声息地 fall through 到 `allow` 路径。两侧加固：B2 在 `preventContinuation` 行同时合成 `follow_up_message`（来自 `stopReason`）；B3（自然 turn 与 max-iter 两处）改为以 `force_continue` 为权威，使用点上以 `follow_up_message or stop_reason or "Stop hook requested continuation"` 合成注入文本。
2. **Stop / PreCompact 语义显式定义（新「语义」一节、A3、A4）。** 顶层新增「语义 —— 每个事件标记的是哪个边界？」一节，钉住 `Stop = BeforeTurnEnd`、`PreCompact = BeforeMessagesMutation`，并明确划清与 session/process 边界。Stop payload 增加 `turnEndReason: "final_response" | "max_iterations"`，让 hook 不靠名字也能回答「stop what？」。A4 用 `turnEndReason` 标注两个 Stop 位置。
3. **minimal-history 应急截断也 emit `PreCompact`（A3、A4）。** `chat_loop.py:557`（连续两次 context-overflow 后的 `agent.messages = agent.messages[-2:]`）是评审二轮漏掉的第四个压缩位置。A4 emit 表已纳入；PreCompact payload 增加 `compactionType: "microcompact" | "full" | "minimal_history"` 与 `reason: str`（对齐 `_emit_context_compressed`），让 host 能区分常规压缩与失败兜底。
4. **TL;DR 措辞修正。** 「已经实现 Claude-Code 风格 hook 的 host 即插即用获得可观察性」与 A3 已澄清「wire 信封不是 Claude Code flat schema」相左、卖过头了。改为「hook 事件**名**沿用 Claude Code 的，但 wire 信封是 Agentao 现有的 `{event, data}` 形态（见 A3）；只引入可观察性，不引入任何新的控制语义」。

**rev 2026-05-05 —— 评审二轮。** 四项后续发现已落入计划：

1. **`StopHookResult` 现在 parser-safe（B1、B2）。** 上一稿的 `StopHookResult` 缺 `messages`（与 `prevent_continuation`），但 `_run_command_hook` / `_parse_command_output` 在所有路径上都写 `result.messages.append(...)`（`hooks.py:507, 525, 546, 561, 578, 591, 607, 618`）、并在 `hooks.py:589` 写 `result.prevent_continuation = True`。两个字段现在作为 scratch 字段加进 `StopHookResult`，让现有 parser 直接复用而不抛 `AttributeError`；B2 写明 `isinstance` 类型分支：把 `decision: "block"` → `force_continue`、`preventContinuation` → `force_continue`。
2. **payload 信封锁定到现有 adapter 形态（A3）。** 之前列的是 `hook_event_name` / snake_case 字段，与所有现有 `build_*` 返回 `{"event": "...", "data": {camelCase}}` 的事实相左（且 `_matches` 读的是 `payload["data"]`）。A3 现在明确 agentao 信封，并标注：把整个 adapter 切到 Claude Code 的平铺 snake_case schema 是预先存在的跨切面 refactor，**不在本计划范围内**。
3. **max-iterations dispatch 位置钉死（B3）。** 之前的"exit block"措辞在「顶部 max-iter 检查」与「post-while finalization」之间含糊。dispatch 现在钉到 `chat_loop.py:185-186` 处 `else: # "stop"` 分支——这是唯一能让 `force_continue` 重新进 loop 的位置。post-while finalization（`chat_loop.py:308-324`）在 PR-2 下变成不可达，会被删除。
4. **「附件 attached to turn」措辞修正。** §「为什么分两层（回顾）」第 22 行原写「stdout 作为 hook_success record 透传」，现已改为「stdout 被收进 dispatcher 返回的 `list[HookAttachmentRecord]`（所有调用点目前都丢弃返回值——见 A6 caveat）」，与 A6 的实际契约一致。

**rev 2026-05-05 —— 评审一轮。** 五项发现已落入计划：

1. **附件归宿明确（A6）。** `_dispatch_lifecycle` 返回 `list[HookAttachmentRecord]`，但每个现有调用点（`tool_executor.py:591`、`cli/session.py:79`）都丢弃返回值。Phase A 沿用此契约，附件仅在 dispatcher 边界断言。跨切面附件管道工作另开 `PLUGIN_HOOK_ATTACHMENT_PIPELINE_PLAN`（不在范围内）。
2. **Stop gate 不再污染历史（B3）。** `final_msg` 改为在 dispatch **之前**构造，hook 结果出来后才 append；`blocking_error` 会改写 `final_msg.content`，`force_continue` 在 follow-up user message 之前先把原回答留进历史。
3. **`PLUGIN_HOOK_FIRED` 可见性收口（A5、B7、不在范围）。** 「host EventStream 可见」表述错误——它在今天是 transport/replay 事件；`agentao.host.EventStream` 不含 plugin-hook。提升属于独立工单。
4. **outcome 枚举统一（B7）。** 最终集合：`{"allow", "block", "continue", "continue_at_max_iter", "reentry_capped"}`。删去 `"modify"`（Stop 的 `additional_contexts` 以 `added_context_count > 0` 搭乘 `"allow"`）；Open Q4 的 `continue_at_max_iter` 正式入列。
5. **重入封顶为构造常量（B4、Open Q3）。** 砍掉 `.agentao/settings.json` 接线——该文件目前只有两个读者，为未调的旋钮加第三个 reader 是过早暴露配置面。默认 3 落在 chat loop 上；如有真实 host 需求再提升为 settings key。
