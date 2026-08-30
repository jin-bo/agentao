# Claude Code hooks · codex hooks · Agentao 插件 hooks —— 三方 hook 契约对照

> **⚠️ 仅分析。本文任何内容都未被授权实施。** §1 是**发现的优先级排序**，不是排期表。引用该表时请连
> 同本行一起引。

**状态：** 分析，**rev 5**（2026-08-26）。未授权实施。
**锚点：** Claude Code hooks 参考 `<https://code.claude.com/docs/en/hooks>`，原始 markdown 抓取于
2026-08-26（3532 行）；codex `openai/codex@0d9bb6c34c`（2026-08-24），并以 OpenAI 自己的 hooks 参考
`<https://developers.openai.com/codex/hooks>`（抓取于 2026-08-26）作**次级**锚点；agentao
`main@10b5fb8`（2026-08-24）。本文对 codex 的每一条断言都来自钉住那个 commit 的源码 —— 那份参考是事后
读的，且与断言相互印证：`suppressOutput` 是 *"parsed today but not yet implemented"*，并在
`PreToolUse`、`PermissionRequest`、`PostToolUse` 上被明确列为不支持；`continue:false` 是
*"parsed for compatibility"* 但在 `SubagentStart` 上无效；`SessionEnd` hook 是
*"advisory, so their output won't steer Codex"*；事件数记的也是 11 个。它只在一处沉默、而那处正好要
紧：它给五个事件写了 exit 2，唯独没写 `PermissionRequest` —— 而源码在那里把 exit 2 认作 deny，见 §6
第 5 条。
**方法：** 三方全部读源码。codex 与 agentao 的断言逐条带 `file:line`。Claude Code 的断言引用参考文档
的**小节名**而非行号——抓取到的文件不是可复现产物。**10** 条断言是经真实 dispatcher + 真实子进程
*(实测)* 而非读代码得出，各自就地标注，§9.1 附上能一次产出全部 10 条的探针。
**范围：** 双向的 **hook 契约** —— hook 从 stdin 收到的 JSON（§5.9），以及它能在 stdout 打什么、退出
码是什么含义、各方读哪些字段（§5 其余部分）。发现机制、信任模型与可观测性仅在影响契约处出现；这几条
轴的结构对比见 §6 末节与 §7。
**孪生：** `hooks-three-way-claude-codex-agentao.md`。
**相关：** `codex-compaction-vs-agentao.zh.md`（本文 §4 细化了它的 `PreCompact` 注记）、
`pi-mono-compaction-vs-agentao.zh.md`、`path-a-roadmap.zh.md`。

### 修订历史

四轮评审。每轮一行，因为其中**三轮翻转了表格里某一格的判定** —— 一个改过方向的格子，缺了推动它的那一轮就
显得任意。

| rev | 发现 | 头条 |
|---|---|---|
| 2 | 6 处修正 + 扩范围 | rev 1 只覆盖了**输出**契约，本文自己的头号结论因此证据不足；输入契约成为 §5.9。另外：agentao 的 `Stop` `hSO.additionalContext` 是 **⚠️** 而不是 ✅ —— 它装点答案并结束这一轮，而参考文档是继续对话（§5.8） |
| 3 | 4 处，全在 codex 侧与参考文档侧 | 有两格**朝 codex 方向**翻转：它的纯文本 `UserPromptSubmit` stdout 是 ✅ 不是 `Failed`（只有 `{`/`[` 开头的非法 JSON 才失败）；在 `SessionEnd` 上忽略 `continue:false` 是**对齐**、不是偏离。§6 改成一份*选出来的*集合，不再是一个计数 |
| 4 | 4 处，其中 3 处针对 §5.9 自己的范围 | 输入侧的断裂不只是信封形状 —— 还是**八个事件里七个的事件专属字段改名与缺失**。而 *(measured)* 当时只由两个事件的抽样撑着；探针现在跑全部八条 dispatch 路径 |
| 5 | 1 处量词 + 2 处文案 | `PreCompact` 曾被说成两层都干净，实际是三层里干净两层。最终量化：**信封 6/8、事件专属字段 7/8、通用字段 8/8** —— 没有任何一个事件的 stdin 端到端对上 |


---

## 0. 让这次对照成立的前提

锚点日期的 Claude Code 参考文档记录了 **31 个 hook 事件**。codex 实现 11 个，agentao 8 个。**这个差距
在任何一方都不构成缺陷。** 两者都是对着更早、更小的 Claude Code 面做的，而 `TeammateIdle`、
`WorktreeCreate`、`Elicitation` 这类事件显然是后加的，绑定在两个 peer 都没有的产品特性上。

**真正可比、也是本文要谈的**：对于三方都实现了的那些事件，一个照着文档契约写的 hook 脚本，行为是否一
致？这个问题有可证伪的答案——对 agentao 来说，答案在**九处**是「否」。

第二个前提，因为本文很容易被读成「agentao 落后」：codex 自己也在同一份参考上至少偏离了九处 —— §6 是
一份**精选**清单，不是穷尽计数 —— 而且在两条轴上 agentao 是两个 peer 中**更**贴合规范的那个（§7）。**偏离这份参考是常态，不是例外。** §5 的排
序按**后果**，不按「是否偏离」本身。

---

## 1. 发现表（优先级排序，不是排期）

**一句话差别：Claude Code 定义契约、铺面最广；codex 只实现其中很窄的一片，但把信任边界和可观测性做成
了一等公民；agentao 实现的面最窄、机制最简，代价是有一处 hook 根本收不到它预期的输入，另有三处同一个
wire 字段的含义与它字面上写的不一样。**

| 若实施，优先级 | 发现 | 小节 |
|---|---|---|
| **P1** | **stdin 契约在全部八个事件上都有差异** *(实测，8 条 dispatch 路径全跑)*。三层：**信封**在 8 个里有 6 个不是 Claude 那套 —— 只有 `Stop`/`PreCompact` 是 flat snake_case，其余把一切裹进 `{"event", "data"}` 且内层键为 camelCase（`agentao/plugins/models.py:230`），于是参考文档自己的 `jq -r '.tool_input.command'` 示例返回 `null`；**事件专属字段**在 8 个里有 7 个被改名或缺失 —— `prompt`→`userMessage`、`source` 与 `reason` 没有、`tool_response`→`toolOutput` 还伴随 object→string 的类型变化、`tool_use_id` 处处皆无；**通用字段或其取值**则 8 个全中，含 `PreCompact` —— 硬编码的 `transcript_path: None`，以及一个参考文档在该事件上根本不带的 `permission_mode`。 | §5.9 |
| **P1** | **`UserPromptSubmit` 忽略全部四种文档化输出通道** *(实测)*。`decision:"block"` + `reason`、`hookSpecificOutput.additionalContext`、`continue:false`、exit 2 全被静默丢弃；只有 agentao 自创的 `blockingError` / `preventContinuation` / 顶层 `additionalContext` 生效。这与该模块自己声明的目标直接冲突——「a hook script written against Claude Code can run under Agentao without modification」（`agentao/plugins/hooks/_alias.py:5`）。codex 四种全实现。 | §5.1 |
| **P1** | **`systemMessage` 被送错通道，且在 4 个站点中的 3 个被丢弃。** 参考文档定义它是*展示给用户的警告*。agentao 把它 append 进 `additional_contexts`（`_output_parsing.py:183`），在 `final_response` 这个 Stop 站点被回显进 assistant 答复**并写入 `agent.messages`**（`_runner.py:1051-1053`）——于是模型下一轮会读到它——而在另外三个 Stop 站点被整个丢弃（`_runner.py:222,228,236`）。在 `UserPromptSubmit` 上该字段根本不被解析。 | §5.2 |
| **P2** | **`Stop` 的 `hookSpecificOutput.additionalContext` 不会让对话继续** *(实测)*。参考文档这条非错误反馈通道会在与 `decision:"block"` 相同的循环保护下让本轮继续。agentao 解析了它（`_output_parsing.py:185-187`）却从不设置 `force_continue`，于是文本只装饰最终答复、该轮结束 —— 模型拿到了一条它没有机会执行的反馈。 | §5.8 |
| **P2** | **hook 输出无上限。** 参考文档把 hook 字符串截到 10,000 字符，余量落盘并给出回捞路径；codex 把 `additionalContext` 限在 ~2,500 token 并落盘（`hooks/src/output_spill.rs:12`）。agentao 任何位置都没有上限：一个打印大文件的 `UserPromptSubmit` hook 会把整份内容灌进 prompt。 | §5.3 |
| **P2** | **`PreToolUse` 的 `additionalContext` 解析后只写日志**（`runtime/tool_runner.py:308`）。参考文档把它注入到 tool result 旁边；codex 也注入。 | §5.4 |
| **P2** | **顶层 `continue:false` 只在 `Stop` 生效**（`_output_parsing.py:161`）。参考文档规定它全事件通用但**部分事件会丢弃**，并逐事件点名丢弃场景（`PreCompact`、`PostCompact`、`SessionEnd` 等）；codex 在 **11 个中的 7 个**上执行它，并在两个上**显式拒绝**。三方在这里各不相同 —— 见 §5.5。 | §5.5 |
| **P3** | **exit code 2 只在 `Stop` 生效** *(实测)*（`_dispatcher.py:562`）。参考文档在 14 个事件上认它，含 `PreToolUse`、`UserPromptSubmit`、`PreCompact`；codex 在 **6** 个事件上认。agentao 的 `PreToolUse` 与 `PreCompact` 路径都把这个省略写成了明确的 MVP 范围（`_dispatcher.py:414,208`）。 | §5.6 |
| **P3** | **没有 `${CLAUDE_PLUGIN_ROOT}`。** 参考文档把它规定为路径占位符 + 导出环境变量双通道；codex 为兼容既有 Claude 插件显式设置了 `PLUGIN_ROOT` **和** `CLAUDE_PLUGIN_ROOT`（`hooks/src/engine/discovery.rs:264`）。agentao 一个都不设——全仓 grep 零命中。 | §5.7 |
| *注记* | **`suppressOutput` 被实现了，而参考文档说它无效果。** 并非无害：设了它就会抑制 `<stop-hook>` 回显，于是一个以为该字段无效而设了它的 hook 会丢掉 `systemMessage`/`additionalContext` 的展示。仍归注记，是因为那正是 §5.2/§5.8 的破坏经由第二个字段抵达。 | §5.10 |

---

## 2. 规模（锚点日期）

| | Claude Code（参考） | codex | agentao |
|---|---|---|---|
| 事件数 | **31** | 11（`hooks/src/lib.rs:23`） | 8（`agentao/plugins/models.py:197`） |
| 输入信封 | flat snake_case，全部事件 | flat snake_case，全部事件 | **分裂** *(实测，8/8)*：`Stop`/`PreCompact` 是 flat，其余六个是 `{event,data}` camelCase（`models.py:230`） |
| handler 类型 | 5：`command` / `http` / `mcp_tool` / `prompt` / `agent` | 2 个可跑：`command` / `mcp_tool`；`prompt` 与 `agent` 能解析但加载失败（`discovery.rs:629,639`） | 2：`command` / `prompt`；`http` 与 `agent` 在解析期拒绝（`models.py:233`） |
| matcher 类型 | 字符串，三档求值 | 字符串，三档求值（`events/common.rs:137`） | **dict**，全局只读两个键（`_dispatcher.py:313,323`） |
| 执行 | 全部匹配 hook 并行 | 并行（`engine/dispatcher.rs:122`）+ 上限 8 的 `async` 后台池（`command_runner.rs:45`） | 串行、短路 |
| 跨来源去重 | 同一 handler 定义在多个 settings 文件中只跑一次 | 无（有测试把「保留重复」钉死：`dispatcher.rs::select_handlers_keeps_duplicate_stop_handlers`） | 无 |
| 输出上限 | 10,000 字符 → 落盘 | ~2,500 token → 落盘（`output_spill.rs:12`） | **无** |
| 默认超时 | 600 s；`UserPromptSubmit` 降到 30 s；`SessionEnd` 共享 1.5 s 预算 | 600 s（`discovery.rs:728`）；`SessionEnd` 默认 1 s、硬钳到 3 s（`events/session_end.rs:20,23`） | 全部 60 s（`_parser.py:141`） |
| 配置来源 | 4 层 settings + plugin + skill/subagent frontmatter | 完整 config-layer 栈 + `hooks.json` + `config.toml [hooks]` | **仅插件**（`embedding/plugins/manager.py:66-67`） |
| 安全门 | workspace trust、`disableAllHooks`、`allowManagedHooksOnly` | 逐 hook 信任 hash、managed-only 模式（`discovery.rs:695-697,771`） | **无** |

三列「事件数」不是质量排名——见 §0。

---

## 3. 输出契约矩阵

`hSO` = `hookSpecificOutput`。✅ 按文档实现 · ⚠️ 实现了但行为不同 · ❌ 缺失或无效。**输入**契约在 §5.9，
不在本表。

| 契约点 | Claude Code | codex | agentao |
|---|---|---|---|
| `PreToolUse` `hSO.permissionDecision:"deny"` | ✅ | ✅（reason 为空 → `Failed`） | ✅（reason 为空也接受） |
| … `"ask"` | ✅ | ❌ 拒绝：*"unsupported permissionDecision:ask"*（`output_parser.rs:446`） | ✅ |
| … `"allow"` 单独出现 | ✅ | ❌ 除非配 `updatedInput`，否则判 invalid（`output_parser.rs:442`） | ✅（no-op） |
| … `"defer"` | ✅（仅 `-p`） | ❌ | ❌ |
| … `updatedInput` | ✅ | ✅（仅配 `allow`） | ❌ |
| … `hSO.additionalContext` | ✅ 注入到 tool result 旁 | ✅ 注入 | ⚠️ 解析后只写日志（`tool_runner.py:308`） |
| … 多 hook 优先级 | `deny > defer > ask > allow` | 只有 deny / allow | `deny > ask` |
| … exit 2 阻断 | ✅ | ✅（`events/pre_tool_use.rs:261`） | ❌（`_dispatcher.py:414`） |
| … 已弃用的顶层 `decision` | 有映射，已弃用 | ✅ 保留 legacy 路径 | ❌ |
| `UserPromptSubmit` 顶层 `decision:"block"` + `reason` | ✅ | ✅ | ❌ *(实测)* |
| … `hSO.additionalContext` | ✅ | ✅ | ❌ *(实测)*；只读顶层 `additionalContext`（`_output_parsing.py:90`） |
| … exit 2 阻断并抹掉 prompt | ✅ | ✅（`events/user_prompt_submit.rs:227`） | ❌ *(实测)* |
| … 纯文本 stdout 进 context | ✅ | ✅（`events/user_prompt_submit.rs:217-222`）—— 以 `{`/`[` 开头却**无效**的 JSON 判 `Failed`（`:211`），自 Claude Code v2.1.248 起这与参考文档**一致** | ⚠️ 退化成纯文本 —— **这一行原来的 ✅ 现在才是偏离** |
| `Stop` 顶层 `decision:"block"` + `reason` | ✅ `reason` 必填 | ✅ `reason` 为空 → `Failed` | ⚠️ 空**字符串**被接受 → 用默认文案继续 *(实测)*；只有 `reason` **缺失**或非字符串才被忽略（`_output_parsing.py:165`、`_runner.py:1002-1005`） |
| … exit 2 | ✅ | ✅（`events/stop.rs:297`） | ✅（`_dispatcher.py:562`） |
| … `hSO.additionalContext` 作为非错误反馈 | ✅ 对话继续 | ❌ Stop 的 wire 类型里没有该字段 | ⚠️ 解析了，但该轮**结束** *(实测)* —— §5.8 |
| … 连续 block 上限 | **8**，宿主强制 | **无** —— `stop_hook_active` 会传，但没有任何计数（`core/src/session/turn.rs:524`） | **3**（`_runner.py:157`） |
| 顶层 `continue:false` | 全事件通用，但部分事件丢弃（点名 `PreCompact`、`PostCompact`、`SessionEnd` 等） | ⚠️ 在 **11 个中的 7 个**上执行；在 `PreToolUse`（`output_parser.rs:358`）与 `PermissionRequest`（`:370`）上显式拒绝；在 `SubagentStart`（`events/session_start.rs:272`）与 `SessionEnd` 上忽略 | ⚠️ 仅 `Stop`（`_output_parsing.py:161`） |
| `suppressOutput` | 接受，但**无效果** | ⚠️ 多数事件上解析后丢弃（`let _ =`），但在 `PreToolUse` / `PermissionRequest` / `PostToolUse` 上**判为 unsupported**（`output_parser.rs:362,374,382`） | ⚠️ 实现了 |
| `systemMessage` | 展示给**用户**的警告 | warning entry，面向用户 | ⚠️ 并入模型 context 通道（§5.2） |
| `terminalSequence` | ✅ | ❌ | ❌ |
| `PermissionRequest` `hSO.decision.behavior` | ✅ allow/deny | ✅ | ❌ 无此事件 |
| … exit 2 | **不认**，审批流照常 | ⚠️ 认作 deny（`events/permission_request.rs:249`） | 不适用 |
| `PostToolUse` exit 2 | 不构成阻断；stderr 展示给 Claude | ⚠️ 阻断（`events/post_tool_use.rs:259`） | ❌ 纯副作用 |
| hook 输出上限 | 10,000 字符 | ~2,500 token | 无 |

> **更正，2026-08-28。** 上表「纯文本 stdout 进 context」那一行原文写的是「而参考文档此时会退化成纯文本」。2026-08-28 重抓参考文档后可见，该行为是带版本门槛的：*"when Claude Code tries to parse your stdout as JSON and can't, it reports a non-blocking error on every exit code other than 2 … On the events that add plain-text stdout as context, Claude Code doesn't add the text. **Before v2.1.248**, Claude Code treated that stdout as plain text."* 于是在这一点上 codex 是合规的、agentao 不是 —— 这处对照的方向反了，修正后的五态状态机在 `hooks-claude-contract-conformance-plan.zh.md` §4.2。另外 `{` 开头那条规则也比本文当初设想的更严：只有去掉空白后**既以 `{` 开头又以 `}` 结尾**才会尝试解析，而 `[` 开头的数组无条件是纯文本。本表其余各行**没有**对着这次新抓取重新复验。

---

## 4. `PreCompact` 取消：三种拼法，没有一种合规

参考文档在两个半边都写得很明确：**exit 2 阻断压缩**，或返回顶层 `"decision": "block"`；并且
*"Claude Code discards a PreCompact hook's `systemMessage` and `continue` fields"*（§"PreCompact"、
§"Exit code 2 behavior per event"、§"Decision control"）。

- **codex** 用 `continue:false` 取消（`hooks/src/events/compact.rs:287`）——正好是参考文档说该事件会
  丢弃的那个字段——并且把任何非零退出判成 `Failed` 而非阻断（`compact.rs:313`）。它还在这里把
  `systemMessage` 作为 warning entry 呈现（`compact.rs:279`），也就是同一对被丢弃字段的**另一半**。
- **agentao** 用自创的键 `hookSpecificOutput.compactionDecision:"cancel"`（`_dispatcher.py:229`），
  且完全不读 `continue` *(实测——§9.1)*。
- **两边都不合规，而且彼此也对不上。**

这里还有一个正好镜像的事实，而它很容易只被看到一半：codex 在参考文档说会**丢弃**该字段的那两个事件
（`PreCompact`、`PostCompact`）上执行 `continue:false`，却在参考文档接受该字段的两个事件
（`PreToolUse`、`PermissionRequest`）上把它判为 unsupported。这个字段在 codex 里从来不是「全事件通用」，
它被执行的集合与参考文档的集合只是部分重叠。

有两个后果值得直说。第一，这**不是** agentao 单方面的偏离，不该按单方面偏离归档。第二，与「应当收敛」
的直觉相反：这里**没有可收敛的事实标准**。agentao 的拼法至少是自洽且写进文档的（`CLAUDE.md`，「The
control plane has two layers and one merge rule」），这比建在一个参考文档明说会丢弃的字段上要强。

本节**细化**了 `codex-compaction-vs-agentao.zh.md` 中「官方文档确认 `PreCompact | Yes | Blocks
compaction`」这条注记。该结论在本锚点重新验证后依然成立；新增的是：*JSON* 那条路是顶层
`decision:"block"`，而且 codex 自己的实现也没有用它。

---

## 5. agentao 的偏离

### 5.1 `UserPromptSubmit` 忽略全部四种文档化输出通道 *(实测)* —— P1

`_run_command_hook` → `_parse_command_output`（`_output_parsing.py:26`）只读三个键：`blockingError`
（`:65`）、`preventContinuation`（`:77`）、以及**顶层**的 `additionalContext`（`:90`）。它从不读
`decision`，从不读 `hookSpecificOutput`，从不读 `continue`；而 `_run_command_hook` 会把「非零退出 +
stdout 为空」降级成一条无害警告，所以 exit 2 在这里同样无效。

§9.1 的探针通过真实 dispatcher + 真实子进程端到端确认了每一种情况，其中包含一个真的以 2 退出的 hook：

```
claude documented block          block=None     prevent=False ctx=[]
claude documented ctx            block=None     prevent=False ctx=[]
claude continue:false            block=None     prevent=False ctx=[]
claude exit 2 (stderr)           block=None     prevent=False ctx=[]
agentao-only blockingError       block='nope'   prevent=False ctx=[]
agentao-only additionalContext   block=None     prevent=False ctx=['FROM_AGENTAO_SHAPE']
```

为什么这条评 P1：它是两条**证伪了代码对自己声明**的发现之一。`_alias.py:5` 写着，那张 Claude 工具名别名
表的全部意义就是让「照着 Claude Code 写的 hook 脚本无需修改即可在 Agentao 下运行」。而在最可能被原样从
Claude Code 配置里抄过来的那个事件上，这句话不成立，并且是**静默**失败——hook 退出码 0（或 2）、
dispatcher 记一条 generic success attachment、用户什么都看不到。§5.9 是另一条，而且更宽。

### 5.2 `systemMessage` 进了模型通道，且在 4 个 Stop 站点中的 3 个消失 —— P1

参考文档：*"`systemMessage` —— Warning message shown to the user."* 不是给 Claude 的。

agentao 只在 Stop 解析器里读它，并 append 进 `additional_contexts`（`_output_parsing.py:180-183`）。
而这个列表在下游并不是「用户警告」通道：

- 在 `final_response` 这个 Stop 站点，它被包进 `<stop-hook>` 块、append 到 assistant 的答复上、并
  **写进 `agent.messages`**（`_runner.py:1042-1053`）。用户确实看到了——但模型在此后每一轮也都看到。
- 在 `max_iterations`、`doom_loop`、`length_truncation` 三个站点，站点配置是
  `echo_additional_contexts: False`（`_runner.py:222,228,236`），警告被**整个丢弃**——没有人看到。
- 在 `UserPromptSubmit` 上该字段根本不被解析（§5.1），同样丢弃。

后果不是外观问题。hook 作者写给人看的「警告」，在一条路径上变成了持久的模型输入，在另外四条上变成了沉
默。而以「带外操作者警告」口吻写的文本，恰恰是参考文档在别处警告不要喂给模型的形状（"Text framed as
out-of-band system commands can trigger Claude's prompt-injection defenses"）。

### 5.3 hook 输出无上限 —— P2

参考文档把 hook 输出字符串——`additionalContext`、`systemMessage`、纯 stdout——截到 10,000 字符，把全文
写入文件，再把预览 + 路径交回。codex 做同样的事，用 token 预算、逐 handler 可配、`0` 表示关闭
（`hooks/src/output_spill.rs:12`，`AdditionalContextLimit::from_config`）。

agentao 在任何 hook 站点都不设限。`_parse_command_output` 把 `stdout` 整个 append 进
`additional_contexts`（`_output_parsing.py:49`），`_dispatch_user_prompt_submit` 再把每一条前置到用户
消息上（`_hook_dispatch.py:86-91`）。一个 `cat` 大文件的 hook 会把整份内容放进 prompt，每轮一次。

### 5.4 `PreToolUse` 的 `additionalContext` 解析后只写日志 —— P2

`_apply_pre_tool_use_hooks` 把 `additionalContext` 收进 `hook_result.additional_contexts`，然后写一行
日志就丢掉：*"MVP: recorded, not injected into the model or tool path"*
（`runtime/tool_runner.py:308`）。参考文档把这个字符串注入到 tool result 旁边；codex 也注入。解析、事件
计数、日志行都已经在了——缺的只是那个 sink。

### 5.5 顶层 `continue:false` 只作用于 `Stop` —— P2

`continue_false = data.get("continue") is False` 全仓只出现一次，在 Stop 解析器里
（`_output_parsing.py:161`）。

三方各不相同，而 codex 那一列是最常被写错的，所以这里精确列出：

| | 执行 `continue:false` | 拒绝 | 忽略 |
|---|---|---|---|
| 参考文档 | 全事件通用，*"takes precedence over any event-specific decision fields"* | —— | 逐事件点名为丢弃：`PreCompact`、`PostCompact`、`SessionEnd`，另有约 10 个（§"PreCompact"、§"PostCompact"、§"SessionEnd"） |
| codex | **11 个中的 7 个**：`PreCompact` + `PostCompact`（`events/compact.rs:287`）、`PostToolUse`（`events/post_tool_use.rs:212`）、`SessionStart`（`events/session_start.rs:272`）、`Stop` + `SubagentStop`（`events/stop.rs:250`）、`UserPromptSubmit`（`events/user_prompt_submit.rs:183`） | `PreToolUse`（`output_parser.rs:358`）、`PermissionRequest`（`:370`） | `SubagentStart`、`SessionEnd` |
| agentao | **8 个中的 1 个**：`Stop` | —— | 其余七个 |

注意这个形状：codex 恰好在参考文档点名会丢弃该字段的那两个事件上执行它，又在参考文档接受它的两个事件
上拒绝它。「codex 全事件通用支持」这个说法在两个方向上都是错的。

codex 的两个*忽略*事件里，只有一个算偏离。参考文档给 `SessionEnd` 的是**完全没有决策控制**，并明说
「discards their JSON output fields」（§"SessionEnd"、§"Decision control"），所以 codex 在那里忽略该字
段是**合规**的、不是偏离。真正算偏离的是 `SubagentStart`：它那节写的是可以注入
context，从未点名 `continue` 会被丢弃。

### 5.6 exit code 2 只作用于 `Stop` *(实测)* —— P3

`_run_stop_command_hook` 刻意在解析 JSON **之前**检查 `proc.returncode == 2`，好让 stdout 里的
`continue:false` 无法反制它（`_dispatcher.py:562`）。agentao 其他 dispatcher 都不检查；
`_run_pre_tool_use_command` 与 `_run_pre_compact_command` 各带一条注释说明这一点
（`_dispatcher.py:414`、`:208`），而 §9.1 实测了 `UserPromptSubmit` 那一例。

codex 认 exit 2 的是 **6** 个事件，而不是它出现的 5 个解析器文件数：`Stop` 与 `SubagentStop` 共用一个
解析器，其事件分支同时覆盖两者（`events/stop.rs:216`，exit-2 分支在 `:297`），另外还有 `PreToolUse`、
`UserPromptSubmit`、`PostToolUse`、`PermissionRequest`。

评 P3 而非 P2，是因为 JSON 那条路才是参考文档*首选*的通道（"exit 0 and print JSON for structured
control"），所以在每个受影响事件上 hook 作者都有可用的替代写法。但它仍是可移植性缺口：exit 2 是更短的
惯用法，也是多数公开示例采用的写法。

### 5.7 没有 `${CLAUDE_PLUGIN_ROOT}` —— P3

参考文档规定了三个路径占位符——`${CLAUDE_PROJECT_DIR}`、`${CLAUDE_PLUGIN_ROOT}`、
`${CLAUDE_PLUGIN_DATA}`——它们既替换进 `command`/`args`，**也**导出到 hook 进程环境。codex 设置了
`PLUGIN_ROOT` 与 `PLUGIN_DATA`，外加 `CLAUDE_` 前缀的别名，理由写在注释里：*"For OOTB compat with
existing plugins that use this env var"*（`hooks/src/engine/discovery.rs:264`）。

agentao 一个都不设：`PluginHookDispatcher._run_subprocess` 调用 `run_captured` 时不传 `env=`
（`_dispatcher.py:331`），且 `grep -r 'CLAUDE_PLUGIN_ROOT\|PLUGIN_ROOT' agentao/` 零命中。由于
`${CLAUDE_PLUGIN_ROOT}/scripts/x.sh` 正是 Claude Code 插件引用自带脚本的标准写法，这类 hook 在 agentao
下会直接 file-not-found。

### 5.8 `Stop` 的 `hookSpecificOutput.additionalContext` 不会让对话继续 *(实测)* —— P2

参考文档给 `Stop` 两条反馈通道，两者的区别正是关键所在：`decision:"block"` 是*错误*通道，而
`hookSpecificOutput.additionalContext` 是 *"Non-error feedback for Claude. The conversation continues
so Claude can act on it"* —— 走同一套循环保护（`stop_hook_active`、8 次继续上限），只是被标为 feedback
而非 hook error。

agentao 解析了该字段（`_output_parsing.py:185-187`），但从不由它设置 `force_continue` —— 这个标志只有
三个来源，全是错误形状的：`decision:"block"`（`_output_parsing.py:169`）、`preventContinuation`
（`:220`）、exit 2（`_dispatcher.py:564`）。实测结果：

```
hSO.additionalContext alone      force_continue=False follow_up=None ctx=['run the tests first']
```

于是文本被 append 到答复上，该轮结束。模型拿到了一条它没有机会执行的指引 —— 而这恰恰是参考文档那条
非错误通道存在的用途（"run the test suite before finishing"）。仅凭「解析到了」就标 ✅ 是最容易犯的错；
**解析不等于契约**。

同一个解析器里还有一处相关但独立的修正：`decision:"block"` 配**空字符串** `reason` **不是**静默忽略。
`isinstance(reason, str)` 接受 `""`（`_output_parsing.py:165`），于是 `force_continue` 被设置，runner
再替换成一条默认继续文案（`_runner.py:1002-1005`）。只有 `reason` **缺失**或非字符串才被忽略。实测：

```
decision=block, reason=""        force_continue=True follow_up='' ctx=[]
decision=block, reason missing   force_continue=False follow_up=None ctx=[]
```

这使得 agentao 比两个 peer 都*更宽松* —— 参考文档称 `reason` 必填，codex 则直接把该 hook 判 `Failed`。

### 5.9 stdin 契约：信封 6/8 不对、事件专属字段 7/8 有缺口、通用字段 8/8 有差异 *(实测)* —— P1

这是最大的单点可移植性断裂，而它在输出契约之外：一份只限定在输出契约的分析，会让核心论点——
「hook 脚本无需修改即可运行」——只靠了一半证据。

agentao 发出**两种不同的输入形状**（`_payload.py:7`）。`Stop` 与 `PreCompact` 用参考文档的 flat
snake_case 顶层；其余六个事件把一切裹进 `{"event", "data"}` 信封，内层键为 camelCase。这个分裂是一个具
名常量 `CLAUDE_FLAT_EVENTS`（`agentao/plugins/models.py:230`），dispatcher 的 `_matches` 也据此读两种形
状（`_dispatcher.py:313,323`）。实测方式是用一个 `cat >` hook 驱动**全部八条** dispatch 路径，再读回
hook 进程真正收到的内容 —— 而不是 adapter 的返回值。取样两个事件再由源码外推到八个，正是这里要取代的做法（§9.2 第 4 条）；下面
是完整扫描：

```
UserPromptSubmit    envelope  keys=['cwd', 'sessionId', 'userMessage']
SessionStart        envelope  keys=['cwd', 'sessionId']
SessionEnd          envelope  keys=['cwd', 'sessionId']
PreToolUse          envelope  keys=['sessionId', 'toolInput', 'toolName']
PostToolUse         envelope  keys=['sessionId', 'toolInput', 'toolName', 'toolOutput']
PostToolUseFailure  envelope  keys=['error', 'sessionId', 'toolInput', 'toolName']
Stop                flat      keys=['cwd', 'last_assistant_message', 'permission_mode', 'session_id', 'stop_hook_active', 'transcript_path', 'turn_end_reason']
PreCompact          flat      keys=['compaction_type', 'custom_instructions', 'cwd', 'permission_mode', 'reason', 'session_id', 'transcript_path', 'trigger']
```

参考文档的 `PreToolUse` 输入是 flat 的，其字段是那组**通用**字段 —— `session_id`、`transcript_path`、
`cwd`、`hook_event_name`，外加在该事件小节里确有出现的 `permission_mode`、`prompt_id`、`effort` ——
再加三个事件专属字段：`tool_name`、`tool_input`、`tool_use_id`（§"Common input fields"、
§"PreToolUse"）。它自己那段可运行示例——§"Exit code 2" 里拦 `rm` 的脚本——读的是
`jq -r '.tool_input.command'`。在 agentao 下这个表达式返回 `null`；值在 `.data.toolInput.command`。

**信封只是第一层。** 逐事件对照各自的参考文档小节（`data` / flat 键即上面实测所得）：

| 事件 | 参考文档的事件专属输入 | agentao | 缺口 |
|---|---|---|---|
| `UserPromptSubmit` | `prompt` | `userMessage` | **改名** |
| `SessionStart` | `source`（必需）；`model`、`agent_type`、`session_title` 可选 | —— | `source` **缺失**；`model` 未提供 |
| `SessionEnd` | `reason` | —— | `reason` **缺失** |
| `PreToolUse` | `tool_name`、`tool_input`、`tool_use_id` | `toolName`、`toolInput` | 改名；缺 `tool_use_id` |
| `PostToolUse` | `tool_name`、`tool_input`、`tool_response`（**对象**）、`tool_use_id`、`duration_ms` | `toolName`、`toolInput`、`toolOutput`（**字符串**） | `tool_response` → `toolOutput` 既是改名**又是**类型变化；缺 `tool_use_id`、`duration_ms` |
| `PostToolUseFailure` | `tool_name`、`tool_input`、`tool_use_id`、`error`、`is_interrupt`、`duration_ms` | `toolName`、`toolInput`、`error` | 缺 `tool_use_id`、`is_interrupt`、`duration_ms` |
| `Stop` | `stop_hook_active`、`last_assistant_message`、`background_tasks`、`session_crons` | 前两个有 | 缺 `background_tasks`、`session_crons`；多出 `turn_end_reason` |
| `PreCompact` | `trigger`、`custom_instructions` | 两个都有 | **无**（外加 agentao 自己的 `compaction_type`、`reason`） |

在*这一层*干净的只有 `PreCompact` —— 这张表要严格按它的口径读，它的范围是事件专属字段。**到了通用字段
那一层，没有任何事件是干净的，`PreCompact` 也不例外**，见下面第三条注记。`toolOutput` 值得单独点出来：
它不是 `tool_response` 的 camelCase 写法，
而是另一个字段 —— 参考文档传的是工具的结构化 `Output` 对象（写文件是 `{filePath, success}`），agentao
传的是字符串（`_payload.py:100`）。

三条二阶注记：

- **工具名别名是正确应用的**（`run_shell_command` → `Bash`），所以 matcher 与 payload 是一致的。别名表在
  做它该做的事；出问题的是包在它外面的信封。
- **通用字段，精确表述** —— 这正是最容易说过头的地方。`hook_event_name` 在六个信封事件上全都没有（名字
  由外层 `event` 键携带），`transcript_path` 同样六个全无；`cwd` 在三个工具事件上也缺。至于条件性的通
  用字段：`permission_mode` 欠的是 `UserPromptSubmit`、`PreToolUse`、`PostToolUse`、
  `PostToolUseFailure` 四个 —— 参考文档的 `SessionStart` 与 `SessionEnd` 示例里根本没有这个字段，所以
  那两处缺它不算缺口 —— 而 `effort`（工具上下文、且模型支持时才有）与 `prompt_id`（首次用户输入前不存
  在、且有版本门槛）是条件字段而非保证字段。一刀切地说「除 `Stop`/`PreCompact` 外每个事件上的
  `permission_mode`」说过头了；而「`model` 与 agentao 已实现的事件无关」更是直接错的：agentao 实现
  了 `SessionStart`，`model` 恰恰就在那里，只是没提供。真正不适用的只有 `turn_id` —— 参考文档把它给了
  `MessageDisplay`，而 agentao 没实现该事件。
- **`Stop` 与 `PreCompact` 对齐的是信封布局，不是 payload。** `transcript_path` 被
  硬编码为 `None`（`_payload.py:142,173`），而参考文档记录的是一个路径 —— 常见场景有缓解，因为参考文档
  自己就引导 `Stop` hook 去用 `last_assistant_message`，而这个字段 agentao 是给的；但任何去读 transcript
  的 hook 拿到的是 `null`。`Stop` 还缺 `background_tasks` 与 `session_crons`（§"Stop"）。以及
  `permission_mode` 默认取
  `"workspace-write"`（`_payload.py:144,175`）—— 那是 agentao 自己的模式词表，不属于参考文档的取值
  （`default` / `plan` / `acceptEdits` / `auto` / `dontAsk` / `bypassPermissions`）—— 于是一个照文档枚
  举分支的 hook 一个分支都命不中；而在 `PreCompact` 上，参考文档的输入里压根没有 `permission_mode`，
  所以那是多出来的键、而不是取值不对。这就是三层计数落到 **8/8** 的原因：`PreCompact` 过了信封层、也过
  了自己的事件字段层，仍然带着一个 `None` 的 transcript 路径和一个未定义的键。

这个双形状是刻意的、也有文档 —— `_payload.py:7` 称这个不一致「intentional and load-bearing for
cross-tool portability」，对那两个 flat 事件而言确实如此。本条发现说的是：它只被应用到了八个事件里的两
个，而且即便在那两个上，对齐的也只是信封。

### 5.10 `suppressOutput` 被实现了，而参考文档说它无效 —— 注记

参考文档：*"Has no effect: Claude Code accepts the field but doesn't act on it."* codex 在多数事件上解
析后显式丢弃（`let _ = parsed.universal.suppress_output;`），但在三个事件上**拒绝**它 ——
`PreToolUse`、`PermissionRequest`、`PostToolUse` 都会以 "returned unsupported suppressOutput" 把该
hook 判失败（`output_parser.rs:362,374,382`），于是一个设置了参考文档称为无效字段的 Claude hook，在这
三个事件上会被直接判失败。agentao 会设置 `result.suppress_output`
（`_output_parsing.py:178`）并用它 gate `<stop-hook>` 回显（`_runner.py:1045`），代码里已有一条注释把
它称作「Agentao extension to the Claude semantic」。

归为注记而非单列发现 —— **不是**因为它无害，而「无害」恰恰是最诱人的读法。一个照 Claude
写、以为该字段无效而设了 `suppressOutput: true` 的 `Stop` hook，会整个丢掉它的 `systemMessage` 与
`additionalContext` 展示，因为同一个标志 gate 着 `<stop-hook>` 回显（`_runner.py:1042-1053`）；在 codex
上这样的 hook 更是在三个事件上被直接判失败（§6 第 8 条）。它仍归为注记，是因为它在 agentao 上造成的损
失，恰好就是 §5.2 与 §5.8 已经作为发现记录在案的那两条通道的展示 —— 同一处破坏经由第二个字段抵达，而
不是多出来一处。记录在此，既是为了不被当成新缺陷重新发现，也是为了不被读成无害。

---

## 6. codex 的偏离，用作校准

列出来是为了避免 §5 被读成单方面记分卡。**以下任何一条都不是改动 agentao 的提案。** 这是一份**精选**
集合，不是对 codex 逐条比对参考文档的穷尽审计 —— 它是从 §5 已经触及的事件与字段里攒出来的，所以「九
处」应当读作下界。

1. **`permissionDecision: "ask"` 被直接拒绝**（`output_parser.rs:446`），而单独的 `"allow"` 除非配
   `updatedInput` 否则判 invalid（`:442`）。参考文档定义了四个取值，优先级
   `deny > defer > ask > allow`。agentao 支持 `ask`。
2. **`continue:false` 在 `PreToolUse`（`output_parser.rs:358`）与 `PermissionRequest`（`:370`）上被判
   为 unsupported**（`stopReason` 在这两个事件上同样被拒，`:360`、`:372`），在 `SubagentStart`
   （`events/session_start.rs:272`）上被忽略 —— 而参考文档把该字段视为全事件通用。见 §5.5 的表。
   （codex 在 `SessionEnd` 上也忽略它，但那一条是*合规*的，不属于这份清单。）
3. **Stop 连续 block 没有宿主侧上限。** `stop_hook_active` 会置位并传给 hook
   （`core/src/session/turn.rs:524`），但没有任何东西在计数；参考文档 8 次后结束该轮，agentao 是 3 次。
4. **`PreCompact`/`PostCompact` 执行 `continue:false`**，正是参考文档说这两个事件会丢弃的字段（§4）。
5. **`PermissionRequest` 上的 exit 2 被认作 deny**（`events/permission_request.rs:249`），而参考文档说
   该事件根本不认 exit 2；**`PostToolUse` 上的 exit 2 会阻断**（`events/post_tool_use.rs:259`），而参
   考文档说该事件不能阻断。
6. **`Stop` 没有 `hookSpecificOutput.additionalContext`** ——参考文档的非错误反馈通道。agentao 解析了
   它，尽管并不会让该轮继续（§5.8）。
7. **没有跨来源去重**，而参考文档规定同一 handler 定义在多个 settings 文件中只跑一次。codex 用一个测试
   把相反行为钉死。
8. **`suppressOutput` 在 `PreToolUse`、`PermissionRequest`、`PostToolUse` 上被判为 unsupported**
   （`output_parser.rs:362,374,382`），而参考文档是接受它、只是无效果 —— 于是一个设置了文档化 no-op
   字段的 hook 会在这三个事件上失败（§5.10）。
9. **`systemMessage` 在 `PreCompact`/`PostCompact` 上被呈现出来**（`compact.rs:279`），正是参考文档说
   这两个事件会丢弃的那一对字段里的另一半（§4）。

### wire 契约之外：codex 在结构上领先的地方

- **逐 hook 的信任门。** 每个非 managed handler 都会对一个归一化身份做 hash，只有与已存 hash 匹配时才
  执行（`discovery.rs:695-697,771`），配启动审查 UI 与 `--bypass-hook-trust` 逃生口。参考文档是按
  *workspace* 授信，粒度更粗：它决定某个目录的 hook 能不能跑，而不是**这一个** hook 自你批准以来有没有
  被改过。
- **可观测性。** 逐 handler 的 `HookRunSummary` 状态机——`Running` / `Completed` / `Failed` /
  `Blocked` / `Stopped`、带类型的输出条目、耗时、来源、scope——实时流式推送，外加执行前用 `preview_*`
  把待跑行先渲染出来。agentao 只发一个 `PLUGIN_HOOK_FIRED` 事件携带 verdict 与计数，其自身 docstring 也
  写明 hook 输出在这一层「neither known nor stored」（`_hook_dispatch.py:52-53`）。

---

## 7. agentao 领先的地方

1. **provider 凭证被从 hook 子进程里剥掉。** `_run_subprocess` 走 `run_captured`
   （`_dispatcher.py:349`），后者的 `env=` 默认取 `build_child_env()`，会剥掉 `HARNESS_ENV_KEYS`。codex
   会 `env_clear()` 并重放一份会话环境快照，但只剔除 5 个 launch-context 变量
   （`protocol/src/shell_environment.rs:14-20`）——那里的 hook 会继承 provider API key。参考文档移除
   `OTEL_*`，对 provider 凭证只字未提。
2. **宿主侧的 Stop 重入上限**（`_runner.py:157`），与参考文档的设计意图一致（那边 8、这边 3），而 codex
   没有。
3. **`PreToolUse` 支持 `ask`**，codex 拒绝。
4. **有 `PostToolUseFailure` 事件。** codex 没有对应事件；工具调用失败对 codex 的 hook 不可见。参考文档
   有这个事件。（这也正是「已实现事件的并集是 12 而非 11」的原因 —— 见 §9.3。）
5. **`prompt` handler 真的能跑**（仅 `UserPromptSubmit`，是模板展开而非模型调用）。codex 声明了该类型但
   拒绝加载（`discovery.rs:629`）。

第 1 与第 4 条是引用本对照时值得记住的：它们是 agentao 做了、而两个 peer 都没做的刻意选择。

---

## 8. 已对齐 —— 不要重复上报

在本锚点验证为等价（在三方都有该事件的前提下）：

- `Stop` 通过 exit 2 阻断，stderr 作为继续执行的理由。
- `Stop` 的 `decision:"block"` + **非空** `reason` → 继续该轮。
- `Stop` payload 中带 `stop_hook_active`，重入时置位。
- 多 hook 聚合时 `PreToolUse` 的 `deny` 压过 `ask`。
- `PreCompact` 的 matcher 取值 `manual` / `auto`。
- `Stop` 与 `PreCompact` 两个事件的**输入信封布局**：flat snake_case、`hook_event_name` 在顶层，与参
  考文档一致（§5.9 说的是另外六个事件）。对齐的是*布局*、不是字段集 —— §5.9 第三条注记列出了这两个事
  件上仍存在的字段级缺口，这也正是它们在本节里不能被写成比「布局」更强的任何断言的原因。
- 逐规则 `timeout`，超时杀掉 hook（三方杀的都是**进程树**，不只是直接子进程）。
- stdout 上的 JSON 是主通道；什么都不打印的 hook 是空操作。

---

## 9. 方法

### 9.1 探针

本文有 10 条断言是实测而非读代码得出 —— 9 条行为断言，外加 §5.9 的信封与字段扫描，后者由探针跑遍
**全部八条** dispatch 路径，而不是取一对代表。探针构造真实的 `ParsedHookRule` 对象，经真实的
`PluginHookDispatcher` 打到真实子进程，再打印结果对象。原样附在这里，以便对后续的 `main` 重跑：

```python
import json, tempfile, pathlib
from agentao.plugins.hooks import PluginHookDispatcher, ClaudeHookPayloadAdapter
from agentao.plugins.models import ParsedHookRule

d, A = PluginHookDispatcher(), ClaudeHookPayloadAdapter()
def rule(ev, out=None, sh=None):
    cmd = sh if sh else f"printf %s {json.dumps(json.dumps(out))}"
    return ParsedHookRule(event=ev, hook_type="command", command=cmd, timeout=10)

# §5.1 — four documented UserPromptSubmit channels, plus agentao's own two
p = A.build_user_prompt_submit(user_message="hi")
for name, out, sh in [
    ("claude documented block", {"decision": "block", "reason": "nope"}, None),
    ("claude documented ctx", {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                     "additionalContext": "FROM_CLAUDE_SHAPE"}}, None),
    ("claude continue:false", {"continue": False, "stopReason": "halt"}, None),
    ("claude exit 2 (stderr)", None, "echo blocked >&2; exit 2"),
    ("agentao-only blockingError", {"blockingError": "nope"}, None),
    ("agentao-only additionalContext", {"additionalContext": "FROM_AGENTAO_SHAPE"}, None),
]:
    r = d.dispatch_user_prompt_submit(payload=p, rules=[rule("UserPromptSubmit", out, sh)])
    print(f"{name:32} block={r.blocking_error!r:8} prevent={r.prevent_continuation} "
          f"ctx={r.additional_contexts}")

# §5.8 — Stop feedback channel and empty-vs-missing reason
sp = A.build_stop(last_assistant_message="done")
for name, out in [
    ("hSO.additionalContext alone", {"hookSpecificOutput": {"hookEventName": "Stop",
                                                            "additionalContext": "run the tests first"}}),
    ('decision=block, reason=""', {"decision": "block", "reason": ""}),
    ("decision=block, reason missing", {"decision": "block"}),
]:
    r = d.dispatch_stop(payload=sp, rules=[rule("Stop", out)])
    print(f"{name:32} force_continue={r.force_continue} follow_up={r.follow_up_message!r} "
          f"ctx={r.additional_contexts}")

# §4 — PreCompact cancellation spelling
pc = A.build_pre_compact(trigger="auto", compaction_type="full", reason="compression_threshold")
for name, out in [
    ("claude continue:false", {"continue": False, "stopReason": "no"}),
    ("agentao compactionDecision", {"hookSpecificOutput": {"compactionDecision": "cancel",
                                                           "reason": "no"}}),
]:
    r = d.dispatch_pre_compact_decision(payload=pc, rules=[rule("PreCompact", out)])
    print(f"{name:32} decision={r.decision!r} reason={r.reason!r}")

# §5.9 — what each of the eight events actually writes to the hook's stdin
cap = pathlib.Path(tempfile.mkdtemp()) / "stdin.json"
for ev, dispatch, payload in [
    ("UserPromptSubmit",   d.dispatch_user_prompt_submit,    A.build_user_prompt_submit(user_message="hi")),
    ("SessionStart",       d.dispatch_session_start,         A.build_session_start()),
    ("SessionEnd",         d.dispatch_session_end,           A.build_session_end()),
    ("PreToolUse",         d.dispatch_pre_tool_use_decision, A.build_pre_tool_use(tool_name="run_shell_command", tool_input={"command": "ls"})),
    ("PostToolUse",        d.dispatch_post_tool_use,         A.build_post_tool_use(tool_name="run_shell_command", tool_input={}, tool_output="ok")),
    ("PostToolUseFailure", d.dispatch_post_tool_use_failure, A.build_post_tool_use_failure(tool_name="run_shell_command", tool_input={}, error="boom")),
    ("Stop",               d.dispatch_stop,                  A.build_stop()),
    ("PreCompact",         d.dispatch_pre_compact_decision,  A.build_pre_compact(trigger="auto", compaction_type="full", reason="compression_threshold")),
]:
    dispatch(payload=payload, rules=[rule(ev, sh=f"cat > {cap}")])
    got = json.loads(cap.read_text())
    envelope = set(got) == {"event", "data"}
    keys = sorted(got["data"]) if envelope else sorted(k for k in got if k != "hook_event_name")
    print(f"{ev:19} {'envelope' if envelope else 'flat':9} keys={keys}")
```

10 条实测断言，逐条可对应到一行输出：`UserPromptSubmit` 丢弃 `decision:"block"`、
`hSO.additionalContext`、`continue:false`、exit 2（四条 —— §5.1、§5.5、§5.6）；`Stop` 的
`hSO.additionalContext` 不设置 `force_continue`、空字符串 `reason` 会设置、缺失 `reason` 不设置
（三条 —— §5.8）；`PreCompact` 忽略 `continue:false`、认 `compactionDecision`（两条 —— §4）；那八行信封
扫描是第十条，一次覆盖 6/8 的分裂与每个事件的字段清单（§5.9）。最后这条花了两轮才做诚实 —— 先是打印
adapter 的返回值却标着 *(实测)*，再是换掉了汇报方、却只取样八个事件里的两个就继续外推。这条规则花了两轮才
落实的规则是：**探针必须覆盖断言所量化的那个总体。**

### 9.2 四条方法规则，每一条都是本文的一处错误换来的

保留它们，是因为每一条都产出过一个错误的表格格子，而且至少熬过了一轮评审。

1. **被模型摘要过的规范不是规范。** Claude Code 参考文档最初是通过一次「摘要式抓取」读的，那份摘要有两处
   是错的，若不发现会直接污染 §3：它报告 `continue` / `stopReason` 嵌在 `hookSpecificOutput` 里（实际是顶
   层），并报告 `permissionDecision` 的取值是 allow/deny/**escalate**（实际是 allow/deny/ask/**defer**）。
   因此本文每一条参考文档断言都改为从原始 markdown 源直接读出后重新推导 —— Mintlify 托管的文档在同一 URL
   后加 `.md` 后缀即可取到源文本。当一次对照的结论取决于精确的字段名和嵌套层级时，去抓源格式，自己 grep。
2. **数这个断言真正说的那个单位，别数它的代理量。** codex 对 exit 2 的支持曾按*解析器文件数*（5）来数，而
   不是事件数（6 —— `Stop` 与 `SubagentStop` 共用一个解析器）；「两个 peer 都未实现的事件数」减的是 codex
   的 11 而不是并集的 12；而 `Stop` 的 `additionalContext` 仅凭一次 `parse` 调用就被判为已实现，没有跟到
   行为为止。
3. **参考文档的全局表会被逐事件覆盖，而事件自己那一节才是权威。** 共四处，其中两处出在修前两条时的修正里。
   `model` 与 `turn_id` 是从字段词表里读出来、按到 `PreToolUse` 头上的，而该事件那一节两个都不给 —— 而那次
   修正随后又矫枉过正成「`model` 与 agentao 已实现的事件无关」，这是错的：agentao 实现了 `SessionStart`，
   而 `model` 恰恰就在那里。`permission_mode` 曾被说成六个非 flat 事件都欠，而 `SessionStart` 与
   `SessionEnd` 的示例里根本没有它。以及 codex 被判为「在 `SessionEnd` 上忽略 `continue:false` 属偏离」，
   依据是通用字段表称该字段全事件通用 —— 而该事件自己那一节写的是它没有决策控制、其 JSON 输出被丢弃，所以
   codex 在那里是合规的。一份写着「Every event accepts them, but some events discard them …… Each event's
   section says so」的规范，就是在告诉你全局表不是契约。
   **外加合规计划后来补上的限定词：** 逐事件小节在它*说了不一样的话*时才覆盖；逐事件的**沉默**不是覆盖。
4. **探针要覆盖断言量化的那个总体** —— 见 §9.1 最后一段。

### 9.3 **未**覆盖的内容

- 两个 peer 都未实现的 **19** 个 Claude Code 事件 —— 参考文档 31 个，减去并集 12 个（codex 的 11 个，
  加上 codex 没有的 agentao `PostToolUseFailure`）。若其中某个变得相关，请直接查参考文档。
- `http` 与 `agent` handler 类型：两个 peer 都没有，无从比较。
- codex 的 MCP-tool handler、executor-scoped hook、managed-requirements 层：属于结构而非契约，不在本文
  范围内。
- codex 与参考文档自身的**输入**信封中 §5.9 未点名的字段：两者都是 flat snake_case，仅在 agentao 与之
  不同处做了对照。
- 性能。三方都未做任何计时测量。
