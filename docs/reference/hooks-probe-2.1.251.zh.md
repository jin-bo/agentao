# Claude Code hook 行为 —— 对 2.1.251 的实测记录

> **这是什么。** 一份真实 `claude` 二进制**实际做了什么**的记录，针对的是
> `hooks-claude-contract-conformance-plan.zh.md` 无法从文档判定的那些问题。它十个设计门里有六个写着
> 「探测真实 CLI」是唯一的定案方式。这就是那次探测。
>
> **它是证据，不是契约。** agentao 自己的承诺是 `claude-code@profile-1`，其出处是那份抓取的参考页面
> （计划 §3）。本文说的是**某一个二进制、在某一个平台、某一天**的行为，且下面每条结论都写明了它
> **不能**证明什么。

**被测：** `claude --version` → `2.1.251 (Claude Code)`，macOS 15（Darwin 25.6.0），2026-08-29。
**方法：** 每个探测是一个一次性项目目录、自带 `.claude/settings.json`，以
`claude -p '<prompt>' --model haiku --output-format {json | stream-json --verbose}` 运行。用户的
`~/.claude/settings.json` **只读取**（用以确认它没有声明任何 hooks，从而不会污染结果），从未修改。
**与 profile-1 的关系：** 计划的快照是 2026-08-28 服务的那份页面（`c984f918…`），当时它的 changelog 头部
已是 2.1.251、而页面里还没有 2.1.251 的新增。所以这些测量对应的二进制**位于该快照页面的当时或之后** ——
两者接近但无法证明相同，而这恰恰就是 profile 以 agentao 自己命名、而不是以产品版本号命名的理由。

## 结果

| # | 问题 | 门 | 实测 |
|---|---|---|---|
| A | 命令 hook 默认由哪个 shell 执行？ | G5 | **`sh`** —— `$0` 是 `/bin/sh`，`posix` 为 `on` |
| A | handler 上的 `shell: "bash"` 会被兑现吗？ | G5 | **不会** —— 同样是 `/bin/sh`（macOS 上） |
| B | `SessionStart` 上的 `continue: false` 被认吗？ | G7 | **不认 —— 丢弃。** 会话照常开始，turn 照跑 |
| C | `PostToolUseFailure` 上的顶层 `decision: "block"` 被认吗？ | G7 | **认**，且属*反馈*：reason 进模型、原始错误保留、turn 继续 |
| D | 不符合工具 schema 的 `updatedInput` 会怎样？ | G8 | **该调用被拒**（`tool_use_error`）；**原输入从未执行** |
| F | 每个事件的 stdin 上到底是什么？ | G7（§5.3） | 六份 payload 逐字节捕获 —— 见下 |

---

## A —— shell（G5）

参考文档在同一页自相矛盾：*"Exec form and shell form"* 一节说命令字符串交给 `sh -c`，而
*"Command hook fields"* 的 `shell` 行说该字段*"默认为 `bash`"*。计划 §2.4 拒绝自己选，交给了 G5。

一个 `SessionStart` matcher 组里放两个 handler，一个不写 `shell`，一个写 `"shell": "bash"`：

```
# 不写 shell                       # "shell": "bash"
dollar0=[/bin/sh]                 dollar0=[/bin/sh]
bashver=[3.2.57(1)-release]
shellopt=[posix          	on]
```

**结论。** 描述实现的是 *shell form* 那句；`shell` 行的默认值不是。`BASH_VERSION` 有值只是因为 macOS 的
`/bin/sh` **就是** POSIX 模式下的 bash 3.2 —— 判别依据是 `$0` 和 `posix on`，不是 `BASH_VERSION`。

**对 agentao 的后果。** 它 `shell=True` 的基线在 POSIX 上就是 `/bin/sh`（`_dispatcher.py:353`），所以
**基线是合规的** —— 计划里第 10 条偏离的前提被撤回。而且既然上游是**忽略**显式 `shell` 而不是拒绝该规则，
agentao 就必须**忽略该字段并给一条诊断，而不是拒绝规则**：拒绝会让一个在上游能跑的 hook 失效，这正是
profile 存在要防的那个方向上的合规倒退。

**它不能证明什么。** Windows —— 参考文档在那里点名 Git Bash 与 PowerShell，而 agentao 没有 Windows CI。
本文没有任何一句话说 `shell: "powershell"` 在那边是否被兑现。

## B —— `SessionStart` 与 `continue: false`（G7，存疑行之一）

参考文档的 Decision-control 表把 `SessionStart` 标为*"Context only … No blocking or decision control"*
（`hooks.md:1009`），而**其他**每一个丢弃 `continue` 的事件都在自己那节里也说了一遍 —— 共十五次 ——
唯独 `SessionStart` 没有。计划 §5.1 依「代价不对称」取了窄读法（`discarded`），并把该行标为存疑。

hook 输出：`{"continue": false, "stopReason": "PROBE_STOP_B", "systemMessage": "PROBE_SYSMSG_B"}`，
另加一个标记文件证明 hook 确实跑了。

```
hook 是否运行        : 是（标记文件已写）
result              : "BRAVO"        ← turn 完整跑完
subtype/terminal    : success / completed，num_turns = 1
"PROBE_STOP_B"      : 在该次运行输出中出现 0 次
```

**结论：丢弃。** 窄读法**被实测证实**。该行不再存疑；§5.1 的 `SessionStart` / `continue` 格按证据写作
`discarded`，翻案清单里「若探测发现它认这个停止」那一支**不触发**。

**它不能证明什么。** 不能证明 `systemMessage` 在那里**也**被丢弃。`--output-format json` 承载的是结果、
不是用户通知通道，所以它没出现在那份 JSON 里并不构成证据 —— 见文末方法说明，这一类假阴性本次就发生过一回。

## C —— `PostToolUseFailure` 与 `decision: "block"`（G7，存疑行之二）

全局 Decision-control 表点名了这个事件（`hooks.md:999`）；它自己那节只定义了 `additionalContext`
（`:2043-2046`）。计划 §5.1 把它称作本文档无法自行了结的那一行，并要求探测回答**四个**问题 —— 因为那条
全局行钉的是 wire 形状，而该行成员的效果彼此互不相容。

设置：一次会失败的 `Read`，外加一个打印 `{"decision": "block", "reason": "PROBE_C_REASON"}` 的
`PostToolUseFailure` hook。

**对照组（C2，先跑，且是有效对照）：** 同一事件、同一失败工具，hook 打印
`{"unrelated_key": "PROBE_C2_MARKER"}` —— 标记文件证明 hook 已触发，而该字符串到达模型 **0 次**。
所以 hook 的原始 stdout 不会被整体回灌；进模型的是**被识别的字段**。

模型实际收到的内容（按要求逐字复述）：

```
> File does not exist. Note: your current working directory is <PROJECT>.

> PostToolUseFailure:Read hook blocking error from command: "<hook 命令>": PROBE_C_REASON
```

**四问逐条：**

1. **认不认？** **认。** 该事件上 `hooks.md:999` 的宽读法成立。
2. **`reason` 去哪？** **进模型**，作为独立的一行
   （`<事件>:<工具名> hook blocking error from command: "<命令>": <reason>`）。
3. **原始错误是否保留？** **保留** —— 两行都在，原始错误在前。
4. **turn 是否继续？** **继续** —— 助手正常作答；`subtype: success`，`num_turns: 2`。

所以效果是**反馈并继续**，与 `PostToolUse` 的 `block` 同类。而这正是计划拒绝假设的那件事：现在它是被
测出来的，不是从兄弟事件横移过来的。

**同族测量（C3）：** 同一事件上的 `hookSpecificOutput.additionalContext` 以

```
<system-reminder>
PostToolUseFailure:Read hook additional context: PROBE_C3_CONTEXT
```

的形式投递给模型，追加在被保留的原始错误之后。

## D —— 不合法的 `updatedInput`（G8）

计划 §4.4 记着：原本用来了结这个问题的那句话**从来不在参考文档里** —— 一处编造的引文，rev 11 已删 ——
因此计划自己的答案（拒绝该调用；绝不回退到原输入）当时只是 agentao 的选择，等一次探测。

一个作用于 `Bash` 的 `PreToolUse` hook 返回
`{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": {"not_a_real_field": 123}}}`，
prompt 是*「运行：touch D_RAN.txt」*：

```
tool_result is_error = True
  "PreToolUse hook for Bash returned updatedInput that failed schema validation:
   Bash failed due to the following issue:
   The required parameter `command` is missing"

D_RAN.txt            : 不存在   ← 原命令从未执行
```

**结论。** 上游**按工具 schema 校验改写结果，并拒绝该调用**。计划出于安全做的选择与实测行为一致，因此它
作为合规落地、而不是作为「偏离安全的书面声明」。注意这次确认的形状：那句编造的话所描述的**行为**是真的，
尽管那句话不在页面上 —— 这正是「要核的是主张，不是措辞」的理由。

## F —— 真实 stdin payload

以 `cat > payload_<Event>.json` 作为整个 hook 体捕获，一次运行、一个会话。绝对路径已归一为
`<HOME>` / `<PROJECT>`；id 来自一次性会话。

```jsonc
// SessionStart                          // SessionEnd
{ "session_id", "transcript_path",       { "session_id", "transcript_path",
  "cwd", "hook_event_name",                "cwd", "prompt_id",
  "source": "startup" }                    "hook_event_name", "reason": "other" }

// UserPromptSubmit                      // PreToolUse
{ …, "prompt_id", "permission_mode":     { …, "prompt_id", "permission_mode": "default",
      "auto", "hook_event_name",           "hook_event_name", "tool_name": "Read",
  "prompt": "Read the file ./notes.txt…" } "tool_input": {…}, "tool_use_id": "toolu_…" }

// PostToolUse                           // Stop
{ …, "tool_name", "tool_input",          { …, "permission_mode": "default",
  "tool_response": { "type": "text",       "hook_event_name": "Stop",
    "file": { "filePath", "content",       "stop_hook_active": false,
      "numLines", "startLine",             "last_assistant_message": "FOXTROT",
      "totalLines" } },                    "background_tasks": [],
  "tool_use_id", "duration_ms": 3 }        "session_crons": [] }
```

八条观察，每条要么确认、要么修正计划 §5.3 的一格：

| 观察 | 对 §5.3 的作用 |
|---|---|
| `permission_mode` 在 `UserPromptSubmit`、`PreToolUse`、`PostToolUse`、`Stop` 上**存在** | 确认矩阵，包括它称为「最容易漏」的 `Stop` 那行 |
| `permission_mode` 在 `SessionStart`、`SessionEnd` 上**不存在** | 确认两个 `—` 格 |
| 取值是 `"default"`，而同一会话的 `UserPromptSubmit` 上是 `"auto"` | 确认枚举是上游那套、不是 agentao 那套。同会话内取值不同**尚无解释**，记为观察、不作规则 |
| `prompt_id` 在五个「用户输入之后」的事件上都有，`SessionStart` 上**没有** | 确认*"absent until the first user input"* |
| `effort` 本次**处处缺席** | 与「取决于模型是否支持」一致；不构成正面测量 |
| `agent_id` / `agent_type` **处处缺席** | 确认两个 **forbidden** 列 |
| `transcript_path` 是真实、持续写入的 `<HOME>/.claude/projects/<slug>/<session>.jsonl` | agentao 没有对应物；G7 在「造一个」与「写明 `null`」之间的选择不变，但目标形状现在已知 |
| `tool_response` 是**结构化对象**（`{type, file:{filePath, content, numLines, …}}`） | 确认 §5.3 最难那行：agentao 的工具返回 `str`，所以这是真实的类型分歧，不是命名分歧 |
| `Stop` 带 `background_tasks: []` 与 `session_crons: []` —— **存在且为空**，不是省略 | 上游对闲置特性发空数组。agentao 两个特性都没有，按 §1 省略 —— 一处现在有据可依、而非假定的差异 |

## 方法说明 —— 本次探测在得出真结果之前，先得出过两个假结果

记下来，因为两者都很容易重演，且都不会自报。

1. **一个从未运行被控对象的对照组。** C2 第一次运行的 prompt 里带了 `PROBE` 字样，模型判断自己正在被测试
   于是**拒绝调用工具**，`PostToolUseFailure` 根本没触发，「标记没进模型」这个读数什么也没测到。修法是
   中性 prompt **加**一个证明 hook 已触发的标记文件。**对照组自己也需要可达性证明。**
2. **一个依赖模型的检测方法造成的假阴性。** C3 第一次报告 `additionalContext` 到达模型 **0 次**；改用
   「请把你收到的全部内容逐字复述」的 prompt 重跑后是 **3 次**。靠模型复述来检测，只有在模型主动提到该
   字符串时才会触发，所以它给出的 0 不构成缺席的证据 —— 与「一次否定 grep 除非能找到已知正例、否则证明
   不了任何东西」是同一个形状。

以上就是上面每条结论都附带「它不能证明什么」的原因。
