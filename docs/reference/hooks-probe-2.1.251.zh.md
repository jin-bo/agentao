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
| G3 | 字符串 `matcher` 如何求值？ | G3 | **`*` 是通配符；其余一切都是锚定全匹配。** 非锚定那种读法被推翻 |
| G6 | `SessionStart` / `SessionEnd` 的 matcher 拿什么比？ | —— | **`source` 与 `reason`。** 不是空串 —— 那会让这两个事件上除 `*` 外的每个 matcher 都是死的 |

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

## G3 —— 字符串 matcher（G3）

计划 §2.3 依据 codex 的实现与参考文档的措辞，说上游三路求值 matcher —— `*`、精确并列、以及**非锚定**
正则。这是本次探测中唯一**被推翻**的断言。

七个 `PreToolUse` matcher，各自对一次 `Read` 调用：

| Matcher | hook 是否触发 | `re.fullmatch(p, "Read")` | `re.search(p, "Read")` |
|---|---|---|---|
| `*` | **是** | *非法正则* | *非法正则* |
| `Read` | **是** | True | True |
| `^Read$` | **是** | True | True |
| `Read\|Write` | **是** | True | True |
| `Rea.*` | **是** | True | True |
| `ead` | **否** | False | True |
| `Rea\|Wri` | **否** | False | True |

**结论。** 七个点全部与 `re.fullmatch` 一致，最后两个否掉了 `re.search`：工具名的子串匹配不上，前缀并列也
匹配不上。`*` 是特判 —— 它不是合法正则，所以根本没有走到正则引擎。

**对 agentao 的后果。** 它需要的求值器早就有了：`_regex_match_full`（`_matchers.py:30`），外加一个 `*`
分支。**不变**的是 §2.3 的头条 —— 字符串 matcher 仍然不是「换个写法的 dict matcher」，因为 `toolName` 走的
是 `_glob_match`，那里 `Edit|Write` 是一个不含 `*` 的字面串，什么都匹配不到。

**它不能证明什么。** 大小写敏感性、*非法*正则会怎样，以及 MCP 工具名（`mcp__server__tool`）是否走同一条
匹配路径。**空字符串**也不在这七个里面 —— 见 G3b，那是单独的一次运行。

## G3b —— 空 matcher（追加，2026-08-30）

单独记一次运行、而不是在上表加第八行，因为这是另一次测量：驱动的是 *"run: touch RAN.txt"* 引出的 `Bash`
调用，而不是那七个共用的 `Read` 调用。合成一张表等于宣称一个两次运行都没有的覆盖面。

三个一次性项目，各带一个往 marker 文件追加的 `PreToolUse` handler，均在 `--permission-mode
bypassPermissions` 下：

| Matcher | 工具跑了吗 | hook 触发了吗 | 角色 |
|---|---|---|---|
| `*` | 是 | **是** | 正控制组 —— 机制可达 |
| `""` | 是 | **是** | 待答的问题 |
| `NoSuchToolName` | 是 | **否** | 负控制组 —— marker 不是无条件写的 |

**结论。** `""` 和 `*` 一样是**通配符**。两个控制组都在，这正是第一次探测的方法注记要求的。

**对 agentao 的影响。** `re.fullmatch("", "Bash")` 不匹配，所以没有特判的话，一份把通配符写成 `""` 的
Claude Code 配置拷过来会**不带任何告警**地解析、然后永远不触发 —— 与非锚定读法同一种「静悄悄什么都不做」
的失败，只是换了个拼法。`_claude_matcher_match` 对两者都做了特判。

**它不能证明什么。** 省略 `matcher` 键是否同样处理（本次没测），以及 `PreToolUse` 之外的事件。

## G6 —— 会话事件上的 matcher 拿什么比

一次对 agentao 实现的评审提出：`SessionStart` 的 matcher 到底能匹配到什么，毕竟这个事件没有工具名。
参考文档没写；二进制写了。

| 事件 | Matcher | 会话的实际取值 | hook 是否触发 |
|---|---|---|---|
| `SessionStart` | `startup` | `source` = `startup` | **是** |
| `SessionStart` | `resume` | `source` = `startup` | **否** |
| `SessionStart` | `*` | —— | **是** |
| `SessionEnd` | `other` | `reason` = `other` | **是** |
| `SessionEnd` | `clear` | `reason` = `other` | **否** |

**结论。** `SessionStart` 的 matcher 与 `source` 比，`SessionEnd` 的与 `reason` 比 —— 正是这两个事件在
stdin 上带的那两个字段（§F）。两者都遵循 §G3 的全匹配规则。

**对 agentao 的后果。** 第一版实现拿空串去比，于是这两个事件上除 `*` 以外的每一个 matcher 都能解析成功、
然后永远不触发 —— 正是设计文档用来论证「不要在两种形状之间做 matcher 翻译」的那个失败。

**它不能证明什么。** agentao 那八个事件之外的事件上，matcher 拿什么比。
