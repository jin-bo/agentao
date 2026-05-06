# 5.7 插件 Hooks —— 控制平面扩展

> **本节你会学到**
> - **为什么** Hooks 单独成章——它和前六节扩展点在不同轴上
> - **8 个事件**各自的触发点和能做什么（含 Phase B 的 `Stop` 控制面）
> - **写一条规则**：`hooks.json` 字段、command vs prompt、per-event 限制
> - **裁决**：四种附件、`UserPromptSubmitResult` 与 `StopHookResult`、`matched_rule_count == 0` 的静默规则

§5.1–§5.6 都在能力平面上做文章。这一节换到另一条轴：控制平面。

## 5.7.1 它解决什么问题

前六节的能力平面（Tool / Skill / MCP / Permission / Memory / SystemPrompt）都在回答**同一个问题**：

> "怎么给 agent 加新能力？"

Hooks 在回答**另一个问题**：

> "agent 走到 X 这一步时，我能不能先看一眼 / 拦一下 / 塞点东西进去？"

前者是**能力平面**（capability plane），后者是**控制平面**（control plane）。两条轴正交——同一个 plugin 完全可以一边注册 Tool，一边挂一条 `PreToolUse` hook 来审计这个 Tool 的调用。

::: tip 怎么判断该挑哪条轴
能力平面是"agent 不知道你的业务在做什么；你写一个 X 把业务接进去"。
控制平面是"agent 已经在做某件事了；运行时把这一刻**暴露给你**，你决定是放行、阻断、还是改写"。
:::

关于格式：Agentao 的 hook 系统**对齐 Claude Code 的 `hooks.json` 格式**——在 Agentao 写的 hook 规则 Claude Code 可以直接读，反向也成立。两个例外是 `Stop` 和 `PreCompact`，它们沿用 Claude Code 的 flat snake_case 顶层 schema，而不是 Agentao 的 `{event, data}` 信封（见 `CLAUDE_FLAT_EVENTS`）。

::: warning 本章是**规则作者**视角
你会学到怎么写一条 hook 规则、它何时跑、能输出什么。

宿主侧的 hooks list / disable / hot-reload API **故意不暴露**——那部分不在 [4.7 嵌入式 Harness 合约](/zh/part-4/7-host-contract#4-7-8-不在-合约里的东西) 里。如果你在做 SaaS 平台、想给租户提供"管理 hook 开关"的能力，目前的答案是：在自己的 plugin 装载层做，**不要**绕到 `agentao.host` 里去找 API。
:::

## 5.7.2 八个事件一览

| 事件 | 触发点 | 主要能做的 |
|------|--------|-----------|
| `UserPromptSubmit` | 用户消息进入 turn 之前 | 注入上下文 / 阻断本轮 / 拒绝继续 |
| `SessionStart` | 一个 session 开启 | 初始化、写日志、加载长期上下文 |
| `SessionEnd` | session 关闭 | 清理、归档、上报指标 |
| `PreToolUse` | 工具调用前 | 拦截危险参数、审计、打 trace |
| `PostToolUse` | 工具调用成功后 | 后处理结果、写审计、改 next-step 输入 |
| `PostToolUseFailure` | 工具调用抛错后 | 错误归类、降级、决定要不要终止 turn |
| `Stop` | turn 退出（含 `final_response` / `max_iterations` / `doom_loop`） | `force_continue` 再来一轮 / `suppress_output` / `system_message` |
| `PreCompact` | 上下文压缩之前（`microcompact` / `full` / `minimal_history`） | **观察**——记录、报警，不能拦截或改写 |

来源：`agentao/plugins/models.py` 中的 `SUPPORTED_HOOK_EVENTS`。

::: info `Stop` 是控制点，`PreCompact` 是观测点
Phase B 落地之后，`Stop` 上的 hook 可以让 chat-loop **再发起一轮 LLM**（通过 `force_continue` + `follow_up_message`）——这是真正的**控制信号**，会改变 turn 走向。
`PreCompact` 始终是 observe-only：`outcome` 恒为 `"allow"`，你可以记录"哪种压缩、什么时候触发"，但**不能阻止压缩发生**。
:::

## 5.7.3 写一条规则

Hook 规则住在 plugin 里的 `hooks.json` 文件（路径由 plugin manifest 指定），形状跟 Claude Code 的 `hooks.json` 完全相同：

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "type": "prompt", "prompt": "Always answer in markdown." }
    ],
    "PreToolUse": [
      {
        "type": "command",
        "command": "/usr/local/bin/audit-tool-call.sh",
        "matcher": { "tool_name": "run_shell_command" },
        "timeout": 30
      }
    ]
  }
}
```

每个规则的字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | `"command"` \| `"prompt"` | 是 | 见下文 |
| `command` | string | type=command 时必填 | 要执行的脚本/命令；走 stdin 收 payload，stdout 收附件 |
| `prompt` | string | type=prompt 时必填 | 注入到对话的字面文本 |
| `matcher` | object | 否 | 过滤条件（如 `tool_name`、`trigger`），`null` = 匹配所有 |
| `timeout` | int | 否 | command 类型超时秒数，默认 60 |

manifest 也允许声明多份 hooks 文件；解析器把它们合并成一组 `ParsedHookRule`。

### command vs prompt

**`command`**：跑一个外部进程。运行时把事件 payload 写到子进程 stdin（JSON），子进程的 stdout 是 hook 的"输出附件"。通用、能干脏活，但有进程开销。

**`prompt`**：直接把字面文本作为附件挂回去。**没有副作用**，纯字符串注入，零进程开销。适合"每轮塞一段 system 上下文""根据用户消息决定要不要补一句话"这类纯 LLM 侧逻辑。

### per-event 限制

不是每个事件都接受这两种类型。`SUPPORTED_HOOK_TYPES_BY_EVENT` 里的允许矩阵：

| 事件 | 允许的 type |
|------|------------|
| `UserPromptSubmit` | `command` + `prompt` |
| `SessionStart` / `SessionEnd` | 仅 `command` |
| `PreToolUse` / `PostToolUse` / `PostToolUseFailure` | 仅 `command` |
| `Stop` / `PreCompact` | 仅 `command` |

::: warning Stop 和 PreCompact 故意拒绝 prompt
原因：Phase B 的 Stop runner 和 lifecycle dispatcher 在这些事件上**只**调 command hooks。如果允许 `prompt` 通过，规则解析没问题，但运行时会**静默跳过**——典型的"看起来工作但其实没跑"。

所以解析器在 per-event allowlist 不匹配时直接报 warning 拒绝：

```
Hook type 'prompt' is not supported for event 'Stop' — skipped.
(Allowed for this event: ['command'])
```
:::

### matcher 与 tool 别名

`matcher` 是一个 JSON 对象，最常用的是按 `tool_name` 过滤：

```json
{ "matcher": { "tool_name": "Bash" } }
```

注意 `Bash` 是 Claude Code 的工具名；Agentao 内部叫 `run_shell_command`。运行时通过 [`ToolAliasResolver`](https://github.com/jin-bo/agentao/blob/main/agentao/plugins/hooks.py) 把这两个名字**双向打通**——你写 `Bash` 或 `run_shell_command` 都能匹配同一个工具。

`matcher` 的字符串值支持 glob（`*`、`?`）和**整串 regex**（写成 `^...$` 形式会按 regex 解释）。`null` matcher 匹配该事件的所有调用。

### 不支持但保留的类型

`http` / `agent` 这两种类型登记在 `KNOWN_UNSUPPORTED_HOOK_TYPES` 里——解析器**认识它们**（不会当成"未知错误"），但当前版本不执行，会发一条 warning："此类型暂不可执行，已跳过"。它们是给未来留的接口。

## 5.7.4 输出与裁决

Hook 跑完之后产生**附件**（`HookAttachmentRecord`），运行时根据附件类型决定下一步。

### 四种附件类型

| `attachment_type` | 含义 | 谁发出 |
|-------------------|------|--------|
| `hook_additional_context` | "请把这段加到对话里" | command/prompt 都可发 |
| `hook_success` | "我跑完了，没要补的" | 主要给审计/observability 用 |
| `hook_stopped_continuation` | "请别让 turn 继续" | 仅特定事件（如 `Stop` 上的 `force_continue` 信号） |
| `hook_blocking_error` | "出错了，请把这条作为错误抛出去" | 任何事件；在错误流里触发 `[Blocked by hook]` 标记（见 [2.3 生命周期](/zh/part-2/3-lifecycle)） |

### 聚合结果：`UserPromptSubmitResult`

`UserPromptSubmit` 上跑的所有 hook 会被**聚合**成一个结果：

```python
@dataclass
class UserPromptSubmitResult:
    blocking_error: str | None = None      # 任一 hook 抛 hook_blocking_error
    prevent_continuation: bool = False     # 任一 hook 说"别继续"
    stop_reason: str | None = None
    additional_contexts: list[str] = ...   # 所有要注入的上下文，按 hook 触发顺序拼接
    messages: list[HookAttachmentRecord] = ...
```

聚合规则：**任一 hook 阻断 = 整轮阻断**；`additional_contexts` 按 hook 触发顺序串联。

### 聚合结果：`StopHookResult`（Phase B）

`Stop` 上跑的所有 hook 聚合成：

```python
@dataclass
class StopHookResult:
    blocking_error: str | None = None
    force_continue: bool = False           # 真正的"再来一轮"信号
    follow_up_message: str | None = None   # 作为下一轮的 user 消息
    additional_contexts: list[str] = ...
    stop_reason: str | None = None
    suppress_output: bool = False          # 不要把 additional_contexts echo 到 final answer
    system_message: str | None = None
    messages: list[HookAttachmentRecord] = ...
    matched_rule_count: int = 0
```

`force_continue=True` 时 chat-loop 会把 `follow_up_message` 当作下一轮 user 消息，**重新发一次 LLM 请求**。这是 Stop hook 影响 turn 走向的**唯一**正路——不是阻断，是**继续**。

`suppress_output` 主要是 replay 保真用的，chat-loop 也会拿它当兜底——避免 hook 注入的 `additional_contexts` 被 echo 到 assistant 的最终回答里。

### `matched_rule_count == 0` 的静默规则

::: warning 为什么有时收不到任何 hook 事件
`matched_rule_count` 是**被选派的**规则数（不是执行成功数）。它是 0 时——也就是这次事件没有任何 hook 规则需要跑——运行时**根本不发** `PLUGIN_HOOK_FIRED` 事件。

为什么这么设计：让事件流的音量和实际发生的事情对齐。没人挂 hook 的 session 不应该被 `PLUGIN_HOOK_FIRED` 噪音淹没。

副作用要心里有数：你**不能**把"是否收到 `PLUGIN_HOOK_FIRED`"当作"运行时是否到达过这个生命周期点"——后者要看 `EventType` 的其他成员。
:::

### outcome 枚举

每个 `PLUGIN_HOOK_FIRED` 事件都带 `outcome`，含义按事件不同：

| 事件 | `outcome` 取值 |
|------|---------------|
| `UserPromptSubmit` 及其他事件 | `"allow"` / `"block"` |
| `Stop` | `"allow"` / `"block"` / `"continue"` / `"continue_at_max_iter"` / `"reentry_capped"` |
| `PreCompact` | 恒为 `"allow"`（observe-only） |

`Stop` 上的 `continue` 与 `continue_at_max_iter` 用来区分"是哪一个退出点接受了 `force_continue`"——前者是普通的回合结束，后者是已经撞到 `max_iterations` 但 hook 仍要再来一轮。`reentry_capped` 表示循环已经拒绝再次重入。

完整字段表见 [4.2 AgentEvent · Replay 可观测性事件](/zh/part-4/2-agent-events#replay-可观测性事件)。

## 5.7.5 拦截信号怎么落地到 UI

§5.7.4 讲的是 hook 内部如何裁决，这一节看外部——chat-loop 把结果以两种形态呈现给宿主，UI 侧需要能识别它们。

### 形态一：`additional_contexts` → 包在标签里注入下一轮

UserPromptSubmit hook 给出 `additional_contexts` 而不阻断时，chat-loop 会在用户消息前置一段：

```
<user-prompt-submit-hook>
{ctx[0]}
</user-prompt-submit-hook>
<user-prompt-submit-hook>
{ctx[1]}
</user-prompt-submit-hook>
{原始用户消息}
```

每条上下文独立包一对 `<user-prompt-submit-hook>` 标签——LLM 能识别这是系统注入而不是用户输入的内容。

### 形态二：早退出 marker

当 hook 给出阻断信号时，`chat()` 不会进 LLM 循环，而是**直接返回**一条带 marker 的字符串：

| Marker | 由谁产生 | 字段来源 |
|--------|---------|---------|
| `[Blocked by hook] {message}` | `UserPromptSubmitResult.blocking_error != None` | `blocking_error` 字面 |
| `[Hook stopped] {reason}` | `UserPromptSubmitResult.prevent_continuation == True` | `stop_reason`（缺省时为 `"Hook prevented continuation"`） |

::: tip UI 怎么用
两个 marker 都是**返回值的前缀**，不走错误抛出路径——你的 UI 看到 `chat()` 返回正常字符串、内容以这两个前缀开头时，应该把这一轮渲染成"被拦截"而不是"assistant 回复"。

也参考 [2.3 生命周期 · 错误信号](/zh/part-2/3-lifecycle) 里其他几种 chat-loop 早退出 marker。
:::

::: warning Stop hook 不走 marker
Stop hook 即使 `blocking_error` 非空，也**不会**前缀 `[Blocked by hook]` 到最终回答里。它的影响通过 `force_continue` / `suppress_output` / `system_message` 走另一条路（见 §5.7.4）。

如果你需要把 Stop hook 的错误暴露给用户，方式是返回 `system_message` 或写到 `additional_contexts` —— marker 是 UserPromptSubmit 专属。
:::

## 5.7.6 可观测性 & replay

Hook 留下的痕迹分两层：实时事件流（给 UI / 审计）和 replay 归档（给事后分析）。

### 实时层：`PLUGIN_HOOK_FIRED`

每次 hook 派发完——只要 `matched_rule_count > 0`——运行时会发一条 `PLUGIN_HOOK_FIRED` 到 transport：

```python
async for ev in agent.events_async():
    if ev.type == EventType.PLUGIN_HOOK_FIRED:
        hook_name = ev.data["hook_name"]
        outcome = ev.data["outcome"]
        # ... 按 hook_name 分支处理
```

不同 `hook_name` 携带不同字段（emit shape 在 chat-loop 里固定）：

| `hook_name` | 必带字段 | hook 特有字段 |
|-------------|---------|--------------|
| `UserPromptSubmit` | `outcome` / `matched_rule_count` | `blocking_error` / `stop_reason` / `added_context_count` |
| `Stop` | `outcome` / `matched_rule_count` | `turn_end_reason` / `at_max_iter` / `added_context_count` / `suppress_output` |
| `PreCompact` | `outcome="allow"` / `matched_rule_count` | `compaction_type` / `trigger="auto"` |
| 其他生命周期事件 | `outcome` / `matched_rule_count` | （以最小字段集为主） |

完整字段表：[4.2 AgentEvent · Replay 可观测性事件](/zh/part-4/2-agent-events#replay-可观测性事件)。

### 归档层：replay

Hook 调度也会被 replay 子系统记录。默认捕获 hook 元数据（事件名、规则数、outcome）；hook 的 `output_preview` 字段（command stdout 的预览）默认被截断。

如果你需要在 replay 里看到完整 stdout，把 `.agentao/settings.json` 里的开关打开：

```json
{
  "replay": {
    "capture_flags": {
      "capture_plugin_hook_output_full": true
    }
  }
}
```

::: warning 打开 deep capture 前权衡一下
- **隐私**：command 类型 hook 的 stdout 可能包含 shell 输出、API 凭据、用户数据。Replay 文件落盘后**不会**被自动脱敏。
- **体积**：长 stdout 会让 replay 文件膨胀，replay 服务器加载时间也变长。
- **secret 扫描仍在跑**：deep capture 只绕过长度截断（`ScanTruncate`），secret 扫描器照常工作——但它不是万能的，别当成唯一防线。

完整开关表见 [Appendix B · `replay.capture_flags`](/zh/appendix/b-config-keys#replay-capture-flags)；observability 全景见 [6.6 可观测性](/zh/part-6/6-observability)。
:::

## 5.7.7 边界声明

把前面散落的"故意不做"汇总到一处——这是给"我能不能扩展 X"的提问者的速查表。

### 不开放的宿主面 API

宿主侧的 hooks **list / disable / hot-reload API** 故意不在 [4.7 嵌入式 Harness 合约](/zh/part-4/7-host-contract#4-7-8-不在-合约里的东西) 里。

- ❌ "枚举当前生效的 hook 规则"——没有公开 API
- ❌ "运行时禁用某条规则"——没有
- ❌ "hot-reload `hooks.json`"——没有
- ✅ 想做的话：在自己的 plugin 装载层处理（你控制 manifest，自然就控制了 hooks）

::: info 为什么不开放
"在平台侧管 hook"是个特定场景里才有意义的概念——SaaS 平台想做 tenant 级开关，IDE 想做"测试期禁用"。运行时无法预判你的语义，强行抽象只会做出一个谁都不愿用的中间层。所以这块自由留在你的 plugin 层，宿主合约不掺合。
:::

### 不执行的 hook 类型

`http` / `agent` 在 `KNOWN_UNSUPPORTED_HOOK_TYPES` 里——解析认识、运行时不跑（详见 §5.7.3）。**给未来留的接口**，今天写了只会拿到一条 warning。

### 拒绝某些事件 + 类型组合

`Stop` / `PreCompact` 拒绝 `prompt`（详见 §5.7.3）。原则：能解析不等于能跑，所以在解析期就拒，避免"看起来工作但其实没跑"。

### 承诺过的稳定面

| 面 | 稳定性 |
|----|--------|
| `hooks.json` 字段（`type` / `command` / `prompt` / `matcher` / `timeout`） | **稳定**，对齐 Claude Code |
| `SUPPORTED_HOOK_EVENTS` 集合 | **追加兼容**——会新增事件，但已有事件不会消失或重命名 |
| `HookAttachmentRecord.attachment_type` 取值 | **稳定**——四种之外不会悄悄新增 |
| `PLUGIN_HOOK_FIRED.data` 字段 | **追加兼容**（和 `AgentEvent` 一致；要走稳定合约请用 `HostEvent`，但 host 目前并不投影 `PLUGIN_HOOK_FIRED`，请直接消费 `AgentEvent`） |

## 5.7.8 食谱

### 1 · 每轮注入项目上下文（prompt 类型）

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "type": "prompt",
        "prompt": "项目代号 ATLAS。回答时优先引用 docs/atlas/ 下的设计文档；涉及到部署的问题先查 ops-runbook 频道。"
      }
    ]
  }
}
```

零进程开销，每轮自动注入。适合做"项目身份感知"——agent 一上来就知道自己在哪个项目里。

### 2 · 拦截危险 shell 命令（command + matcher）

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "type": "command",
        "command": "/usr/local/bin/shell-guardrail.py",
        "matcher": { "tool_name": "run_shell_command" },
        "timeout": 5
      }
    ]
  }
}
```

`shell-guardrail.py` 从 stdin 读 payload（含完整命令），如果命中黑名单（`rm -rf /`、`curl | sh` 等）就 stdout 输出 `hook_blocking_error`。chat-loop 见到 `blocking_error` 会终止本次工具调用，UI 侧看到 `[Blocked by hook] {message}`。

::: tip matcher timeout 给小一点
PreToolUse hook 会**阻塞**工具调用——超时设到几秒级别，避免 hook 自己变成性能瓶颈。
:::

### 3 · 空回答时再来一轮（Stop + force_continue）

Reasoning 模型偶尔会以空字符串结束 turn。用 Stop hook 兜一下：

```json
{
  "hooks": {
    "Stop": [
      {
        "type": "command",
        "command": "/usr/local/bin/empty-answer-rescue.sh",
        "timeout": 3
      }
    ]
  }
}
```

`empty-answer-rescue.sh` 检查 stdin 里的 `last_assistant_message` 是否为空。如果空，就输出 `force_continue=true` + `follow_up_message="请基于已有上下文给出最终回答"`。chat-loop 收到信号会再发一次 LLM 请求。

::: warning 务必配合 max_iterations
`force_continue` 会消耗一次循环计数。无限重试就是 doom-loop 的素材——必须设合理的 `max_iterations`，并在 hook 里加上限保护：检查 `at_max_iter` 字段，已到上限就不再发 `force_continue`。详见 [4.6 Max Iterations](/zh/part-4/6-max-iterations)。
:::

### 4 · 压缩前打审计点（PreCompact + command）

```json
{
  "hooks": {
    "PreCompact": [
      {
        "type": "command",
        "command": "/usr/local/bin/audit-compaction.sh",
        "timeout": 2
      }
    ]
  }
}
```

`audit-compaction.sh` 从 stdin 拿到 `compaction_type`（`microcompact` / `full` / `minimal_history`）和 `trigger`，写一行审计日志（哪个 session、什么时间、压缩类型）。

::: info PreCompact 不能阻止压缩
即使 hook 抛 `hook_blocking_error`，`outcome` 仍恒为 `"allow"`——这是 observe-only 的语义。如果你需要"压缩太频繁触发告警"，让 hook 把数据投递到外部 metrics 系统，由 metrics 系统判断阈值，**不要**指望 hook 自己拦下来。
:::

---

→ 下一站：[第六部分 · 安全与生产化部署](/zh/part-6/) —— 把 hooks、permissions、tools 这套组合送上线，需要面对的另一组问题。
