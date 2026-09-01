# 内置工具 —— 四方对照：codex · gemini-cli · pi-mono · agentao

> **⚠️ 仅分析。本文任何内容都未被授权实施。** §1 是**发现的优先级排序**，不是排期表。引用该表时请连
> 同本行一起引。

**状态：** 分析，**rev 12**（2026-09-01）。
**锚点：** codex `openai/codex@b7cd519c76`（2026-08-31）；gemini-cli
`google-gemini/gemini-cli@0bd1d4397`（2026-08-28）；pi-mono `@853a80d26`（2026-08-28）；agentao
`main@afda2ea`（2026-08-31）。四方全部读钉住该 commit 的本地 worktree —— 任何一方都没有把厂商文档
当作一手来源。commit 日期一并写出，好让评审日期与锚点能互相核对；rev 1 自称 2026-08-30 完成，而其中
两个锚点提交于 31 日，不可能成立。
**方法：** 每条断言就地带各自仓库锚点下的 `file:line`。工具**名字**一律解析到字符串字面量、而不是
持有它的常量 —— 四家里有三家经常量间接，而其中一个常量已经漂移（§7）。
**范围：** 各 harness **在树内、且模型可见**的工具有哪些，以及是什么决定某个工具能否在某一轮到达模
型。不在范围内：工具**实现**质量、MCP 传输、权限规则语法 —— 这几项各有专门文档。
**孪生：** `builtin-tools-four-way-codex-gemini-pi-agentao.md`。
**相关：** `host-tool-allowlist.zh.md`、`host-tool-injection.zh.md`、
`hooks-three-way-claude-codex-agentao.zh.md`（同一套四方方法，不同契约）、
`codex-subagent-v2-vs-agentao.zh.md`、`path-a-roadmap.zh.md`。

### 修订记录

| rev | 发现 | 头条 |
|---|---|---|
| 12 | 2 条（P3） | **同一处记账疏漏犯了两次：把一次更正写进了它所更正的那一行。** rev 11 的修复被写进 rev 10 的格子里、没有单开一行，于是修订表第二次少报了评审轮数 —— 本文给自己定的规则是每一轮评审都要有一行，而只改措辞的一轮同样是一轮。另有一处 rev 11 带来的单位错误：代码块里的引用是**两行上的三个 token**，不是「三行」—— 其中两个落在 §10 复核配方的同一行上。 |
| 11 | 1 条（P3） | **对错计的解释本身又算错了。** rev 10 把 180→186 的差额归因为「三条代码块加一条裸写」，那是四，不是六。改用**按位置**重算 —— 标出每一个 `path:line` token，减去被旧正则的匹配完整覆盖的那些 —— 六条是：**三条**写成 `` `path:line :: symbol` ``（收尾反引号不紧跟数字，旧的整片段模式跳过了它们），以及 §10 代码块里**三个**未加反引号的 token。这次失败与它所解释的那次错计同形：类别是**从差额大小倒推**出来的、不是数出来的，还为了凑数编了一个不存在的第四类，而那个数本身还是错的。是下一轮评审追问「剩下两条去哪了」才逼出这笔账。 |
| 10 | 1 条（P3） | **一个自指的计数错了九版，因为产出它的检查器与它所校验的那句断言口径不一致。** README 写的是 180 条 `path:line` 引用，实际是 **186** 条。校验脚本的模式要求引用必须**占满整个**反引号片段，于是少数了。每一轮都报低，而每一轮都把这个数字原样抄下去、没有复核。修法是把**计数口径**与数字写在一起（文件内每一个 `path:line` token，无论有无反引号，含代码块），并把总数拆开：182 条解析到唯一非空行，余下 4 条是有意为之 —— rev 1 的三条*错误*引用被逐字引在 §10 的 basename 方法笔记里，外加 `<repo-root>/pyproject.toml` 占位符。这条教训不止于本文：**一个关于文档的指标，并不豁免于该文档自己的评审纪律**，而检查器的沉默只有它的模式那么宽。|
| 9 | 1 条（P2），**核查它时又找出 1 个同行缺陷** | **rev 8 修好两格投影、却把第三格写反了。** 它称 gemini-cli「每次构建 schema 都重新过滤，所以 `registerTool` 在下一个请求组装时就落地」。并非如此：`GeminiChat` 缓存工具表，`registerTool` 只写 `allKnownTools`、不使任何缓存失效（`core/src/tools/tool-registry.ts:271`），而 `core/src/core/client.ts:801` 虽然每请求都调 `setTools(modelToUse)`，`setTools` 却在**模型未变时早返回**（`core/src/core/client.ts:311-313`）。刷新点是 `startChat`、显式**无参** `setTools()`（绕开该判断 —— 这正是 `PLAN`/`YOLO` 切换有效的原因）、以及换模型。核查这件事又带出**发现 6**：`reloadSkills()` 用新枚举重新注册 `ActivateSkillTool`，却只调 `updateSystemInstructionIfInitialized()`（`core/src/config/config.ts:3693-3699`）、从不调 `setTools()` —— 于是运行时重载技能后模型停在**过期 schema** 上。agentao 到不了这个状态；它在每次 `chat()` 开头重新投影。 |
| 8 | 1 条（P2） | **四阶段表最后一列，对一半的行答错了问题。**「schema 投影」问的是*何时*，而 rev 7 给 pi-mono 和 agentao 填的是*什么* ——「就是激活集，不再过滤」「非 plan 轮次扣掉 plan 专属工具」。时点才是有意思的部分，而且差别很实在：gemini-cli **每次**构建 schema 都重新过滤，所以 `registerTool` 在下一个请求组装时就落地；pi-mono 推迟到**下一个 agent turn**（`setActiveToolsByName` 自己的契约，`core/agent-session.ts:965-971`）；agentao 最严 —— `to_openai_format(...)` 在 `runtime/chat_loop/_runner.py:348` 只跑一次，位于内层工具调用循环**之上**，所以轮中途 `add_tool` 加的工具，无论循环再跑多少次都不可见，这正是 `add_tool` docstring 写明的契约（`agent.py:906-914`）。这一列决定了构建后的注册表变更何时到达模型 —— 把它与变更那一列分开，图的就是这个。 |
| 7 | 3 条（1 条 P1、2 条 P2） | **「代价必然是一张逐模型目录」本身就是给最高优先级发现加的一条无据约束。** rev 5–6 写两个同行维护的都是逐模型表；codex 是（且与 `provider.capabilities()` 混用，`view_image` 两者都不用），但 **gemini-cli 的是正则** —— `/^gemini-2(\.|$)/.test(model)`（`config/models.ts:458`），它自己的注释称之为 *"legacy behavior"*。形态并不定死，所以发现 1 与 §9 现在写作代价是**拥有某条被持续维护的兼容性事实**，把正则、逐 provider 开关留在桌面上。另两条是传导失败：rev 6 把 §4 拆成四阶段，§0 却仍宣布**三个**「四家各在不同一格」，与 §4 自己那句「四列不构成划分」矛盾；§10 的入口三处写错 —— pi-mono 的 `allToolNames` 是名字集合不是构建步骤（入口是 `_buildRuntime`，而 `reload()` **会再跑一遍**，`core/agent-session.ts:2820`，所以表里的「一次」也错了），agentao 的完整入口是 `agent.py::_wire_tooling`（`:578`）而非它内部调用的 `register_builtin_tools`。最后，rev 6 说 `max_tokens`「每次请求都发出去」范围过宽：`chat()`/`chat_stream()` 默认 `None`（`llm/client.py:430,534`），kwarg 只在 `if max_tokens` 时才加（`:419`）—— **主 agent 路径**会转发（`runtime/llm_call.py:138`），压缩摘要不会（`context_manager.py:1573`），所以 65536 的隐患限定在该路径 + 会静默钳制的端点。 |
| 6 | 3 条（1 条 P1、1 条 P2、1 条 P3） | **rev 5 为那个先例所作的辩护错在一处新地方，所以整条论断现在改为直接引源、不再靠推。** rev 5 说 agentao「没有对应物」于 pi-mono 的 `maxTokens`，并拿 `grep -r context_window agentao/` = 0 当证据 —— **搜错了字段**：`maxTokens` 是被请求的*输出*上限，而 agentao 有 `LLMClient.max_tokens`（`llm/client.py:139,188,419-421`），且由 ACP 映射（`acp/session_set_model.py:10`）。真正的差别在**默认值** —— pi 会落到逐模型注册表值（`ai/src/api/simple-options.ts:34`），agentao 落到一律 `65536` —— 所以该借鉴在宿主逐模型设置时可移植、在出厂默认下不安全，那是*默认值*问题不是目录问题。rev 5 还称 `supportsFinishReason` 与目录无关；它可在 **provider 与 model 两级**配置（`test/model-registry.test.ts:771-778`）—— 不属于目录的是 agentao 反向采纳它的*理由*。结论：**本仓没有任何先例裁定过目录问题**，§9 的「provider-neutral 下维护不了」作为无据之词撤回。另：三阶段表把注册表*变更*放进了激活集列，还与自己那句「没有两家在同一阶段变」矛盾 —— 现改为**四**列（初始构建／之后注册表变更／激活选择／schema 投影），四家里三家在构建后仍改注册表；§10 的「每家恰好一个入口」收窄为*初始*构建。 |
| 5 | 4 条（1 条 P1、3 条 P2），**其中 1 条部分存疑** | **这条 P1 是引了一份不存在的文档**，而且它给结论承重：rev 4 用「`isRecoverableLength` 在 `pi-mono-pull-review-2026-08-09` 里被自我否掉」支撑 §4/§9 的目录结论。`docs/design/` 只有 `-2026-08` 和 `-2026-08-21`，**没有 `-08-09`** —— 那次评审是项目记录、不是设计文档，故引用撤回。**但实质结论没有被推翻**，评审的后半段存疑：`isRecoverableLength` 的*函数体*（`ai/src/utils/overflow.ts:171`）确实不含逐模型数据，可它的**调用点**传的是 `this.model?.maxTokens ?? 0`（`core/agent-session.ts:2156`），对应模型类型上的必填字段（`packages/ai/src/types.ts:836`），而 `grep -r context_window agentao/` 是 **0**。这个依赖是真的；只读签名就停下，正是 §10 第二条方法笔记换了个地方。该段现在改写为：确有一次借鉴以目录为由被否，但那针对的是单个谓词 —— 一般问题从未被提出。`supportsFinishReason` 是另一条，被反向采纳是因为 `INCOMPLETE_ANSWER_REASONS` 的取值会变成 CLI error envelope（`pi-mono-pull-review-2026-08.md:58`）。其余三条：`enabled_tools` **接受不了** MCP 名字 —— 保留名守卫在活注册表校验之前就拒掉 `mcp_`（`agent.py:449-452`、`tests/test_host_tool_allowlist.py:138`）；§5 已把动机降为推测，§9 却仍写着 pi-mono/codex 的一致「讲的是 context 成本」；§4 的「每轮还是每会话一次」仍在压平**三个**阶段，而没有任何两家在同一阶段上变 —— 现改为一张按*注册表构建* / *激活集变化* / *schema 投影*分列的表，只有 codex 是逐轮重建。 |
| 4 | 5 条（2 条 P1、2 条 P2、1 条 P3） | **两个数字、一处出处判断错了，还有一条领先项把两套机制并成了一条。**（a）gemini-cli 的 19–20 是**注册**数；`getFunctionDeclarations()` 每次构建都重新过滤（`core/src/tools/tool-registry.ts:601-624`）—— 无 MCP 时藏掉两个资源工具，`enter_`/`exit_plan_mode` 按模式互斥 —— 所以裸会话给模型看到的是 **16–17**。这同时推翻 rev 3 的「整个会话是常量集」（模式切换会调 `setTools()`，`core/src/config/config.ts:2810-2819`）和 §8 的「始终可见、只在执行期管」。（b）codex 的 `ModelInfo` **不是模型自声明**：它是 harness／backend 维护的目录，按 **slug 前缀**匹配（`models-manager/src/manager.rs:617-631`），未命中则带告警回退（`model_info.rs:142`），所以 §9 的反对意见是**要自己养一张逐模型目录**，不是「provider 不发送」。（c）「明确的 context 成本赌注」属动机归因 —— 源码只能证明 pi-mono 压住那三个工具并用 shell 提示词兜底（`core/system-prompt.ts:99-111`），现已标为**推测、未测量**。（d）`enabled_tools` 与 `disable_tools` 用的是**不同**守卫 —— 活注册表 ∪ 常量（`tooling/registry.py:195-205`）vs 只查静态常量（`agent.py:466-472`）。（e）§8 已减到两条，正文却还写着「下面三条保留」。 |
| 3 | 5 条（2 条 P1、3 条 P2） | **rev 2 自己的更正也过宽了，三次。**（a）codex 的*读*工具同样不是 0 —— `view_image` 接收本地路径、按环境 cwd 解析、经沙箱文件系统读取（`handlers/view_image_spec.rs:19`、`handlers/view_image.rs:150-175`），Stable 且默认开（`features/src/lib.rs:889-893`）。该列现在明确限定为**通用文本／源码**读取，活下来的论断是「没有通用读取器」而非「没有读取器」。它还是 **§4 的反例**：`view_image` 注册时不看 `input_modalities`（`spec_plan.rs:1259`），改在执行期拒（`handlers/view_image.rs:97-105`），所以 codex 是混合策略、不是干净的能力门控。（b）pi-mono 默认**根本没有按工具的权限边界** —— 没有处理器时 `beforeToolCall` 返回 `undefined`（`core/agent-session.ts:489-492`），调用照常执行（`agent-loop.ts:617-624`）；权限门是*示例*扩展。（c）`activate_skill` 上 rev 2 既说 gemini-cli「同意 agentao」又说 ask 是第三个位置 —— 自相矛盾；且该 ask 规则带 `interactive = true`（`plan.toml:110`），非交互时落到 catch-all DENY（`:76-80`）。另外：§8 的子 agent 绑定那条**撤回**（gemini-cli 同样继承父注册表并浅克隆，`local-executor.ts:190-200` / `core/src/tools/tools.ts:480`），plan 模式那条**重述** —— §9 曾称 agentao 把 plan 模式挡在工具面外，与 §2.1 自己的 `plan_save` / `plan_finalize` 矛盾；真正的 1/4 是*模式进入/退出*做成模型工具。§8 现在是**两条**领先项。 |
| 2 | 8 条（3 条 P1、4 条 P2、1 条 P3） | **三处表格单元格翻案。**（a）codex **不是**「0 个文件工具」—— `apply_patch` 就是模型可见的工作区写工具，其 handler 把 patch 应用到环境文件系统，并**按目标路径**推导写权限（`core/src/tools/handlers/apply_patch.rs:73,236-270`），因此「权限单位**不可能**是工具」与「3:1 多数派」两条推论**一并撤回**；真正的分歧在**读**那一半（§3）。（b）发现 3 引错了常量 —— `PLAN_MODE_TOOLS` **全仓没有运行时消费者**；真正生效的策略是 `read-only.toml` / `plan.toml`，而它对 `activate_skill` 给的是 **ask** 不是 allow。「三个都不碰工作区」对 `save_memory` 也不成立，它会写入 SQLite（§5）。（c）「每次调用都过引擎」在 read-only 路径上不成立（短路点在引擎**之上**），而 gemini-cli **确实**有统一策略通道（`scheduler.ts:648-652`）—— §8 第一条领先项撤回，其余三条保留。另有：默认计数缺宿主限定（直接嵌入 11/13，走工厂 13/15）；`cli_help` 是公开导出、宿主可注册，「死类」说法过强；gemini-cli **确实**按模型名启发式门控（`isGemini2Model`），与 rev 1 自己的 §2.3 矛盾；`get_internal_docs` 对 `cli_help` 子 agent 的模型可达，既然 §6 把子 agent 作用域的 `complete_task` 纳入范围，§10 就不能把它列为模型不可达。 |

---

## 0. 让这次对照公平的前提

**工具数量不是质量轴，本文也不把它当质量轴。** codex 树内约 50 个工具名，pi-mono 8 个、给模型看 4
个。这不是同一个测量做了两遍 —— 是两个不同的赌注，而且 pi-mono 那个是**刻意的**：它把 `grep`、
`find`、`ls` 都写好了，然后不放进默认集（§5）。把这个差距读成功能缺失，恰好在四家分歧最大的那条轴
上把对照方向搞反了。

真正可比的有三件事，每一件 agentao 都已经做过决定：

1. **文件**读取**是工具，还是一次 shell 调用？**（§3）四家对文件**写**都给了专用工具 —— 包括 codex 的 `apply_patch`。真正分开的是读那一半。
2. **工具集在什么时候定下来？**（§4）这不是一个问题而是**四个**：初始构建、之后的注册表变更、注册
   表之上的激活选择、schema 投影。这四格**不构成划分**，也没有哪一家独占其中一格 —— 四家里有三家在
   初始构建之后还会改注册表。任何关于「工具集何时定下来」的断言，都必须点名说的是哪一格。
3. **模型默认看到什么？**（§5）

第四条轴，**非核心工具住在哪里**（§6），是四家结构上分歧最大、结果上分歧最小的一条。

---

## 1. 发现，按优先级

优先级判据是**能否改变 agentao 的某个决定**，不是底层代码的严重程度。

| # | 发现 | 位置 | 类型 |
|---|---|---|---|
| 1 | agentao **只按宿主配置门控，从不看模型**。codex 是混合的：一部分看 harness 维护的 `ModelInfo` 目录，一部分看 `provider.capabilities()`，而 `view_image` 两者都不看 —— 先放行、执行期再拒。gemini-cli 唯一那道模型门是**模型名正则**（`isGemini2Model`，`config/models.ts:458`），不是目录。空白是真的；填上它的代价是**拥有某条被持续维护的兼容性事实**，形态并不定死 —— 正则、逐 provider 开关都在桌面上。 | §4 | 空白，需求未量化 |
| 2 | **`cli_help` 在 agentao 树内没有任何实例化**，而那句「它们在别处注册」的注释（`tooling/registry.py:44-46`）点名了一个没有定义文件的工具。两个类都仍可经公开的 `extra_tools=` 注入点到达，所以这是**一句过时注释加两个从不默认注册的导出**，不是死代码。 | §7 | 文档/代码漂移，我方 |
| 3 | **`/mode read-only` 拒掉 `activate_skill`、`todo_write`、`save_memory`。** 前两个只改会话状态，`save_memory` 会写 SQLite。这是既定规则的正确推论。对照点在于：gemini-cli **生效中**的只读策略显式放行那些只改内部状态的工具（`tracker_*`、`update_topic`、`complete_task`），注释也这么写；而它对 `activate_skill` 是**交互时 ask、非交互时 deny**（`plan.toml:105-110` 加 `:76-80` 的 catch-all）—— 这个中间位置在 agentao 的布尔 `is_read_only` 门上放不下。 | §5 | 策略问题，我方 |
| 4 | gemini-cli 注册了两个模型可见的工具，却**不在它自己的 `ALL_BUILTIN_TOOL_NAMES` 里**；而它对那个常量唯一的测试，是拿清单跟自己比、方向上不可能失败。agentao 有同形状的常量，而且**有**反方向的那条测试。 | §7 | 同行缺陷；反证我方 |
| 5 | **`complete_task` 在 agentao 和 gemini-cli 里都是子 agent 专属工具**，且是各自独立得出的。两个数据点指向：子 agent 的终态信号该放在 scoped registry，不该进主注册表。 | §6 | 趋同，无动作 |
| 6 | **第二个同行缺陷，而且它反证了 agentao 的每次 `chat()` 快照。** gemini-cli 的 `reloadSkills()` 用新的技能枚举重新注册 `ActivateSkillTool`，然后只调 `updateSystemInstructionIfInitialized()` —— 从不调 `setTools()`，于是缓存的会话 schema 一直留着**过期**枚举，直到换模型或显式刷新。agentao 到不了这个状态：它在每次 `chat()` 开头都从活注册表重新投影。 | §4 | 同行缺陷；反证我方 |

---

## 2. 四方清单

### 2.1 agentao —— 直接嵌入 11 个 / 走 CLI 工厂 13 个，装 `[web]` 各 +2

`agentao/tooling/registry.py::register_builtin_tools()`，保留注册顺序。任何计数都要带两个限定。（a）`web_fetch` / `web_search` 需要 `[web]` extra（`beautifulsoup4`），
而裸装和 `agentao[cli]` 都不会拉它（`<repo-root>/pyproject.toml:50,52`）。（b）`check_background_agent` /
`cancel_background_agent` 需要 `bg_store`，而它的**构造器默认是 `None`**（`agent.py:148`）——
只有 `build_from_environment()` 会去接一个（`embedding/factory.py:231-232`）。所以：直接
`Agentao(...)` 嵌入是 **11**，走 CLI／环境工厂是 **13**，两种情况下装了 extra 各再 **+2**。

| 工具 | 来源 | 门控 | `is_read_only` |
|---|---|---|---|
| `read_file` | `tools/file_ops.py:116` | — | ✅ `:111` |
| `write_file` | `tools/file_ops.py:214` | — | ✗ |
| `replace` | `tools/file_ops.py:270` | — | ✗ |
| `list_directory` | `tools/file_ops.py:514` | — | ✅ `:509` |
| `glob` | `tools/search.py:128` | — | ✅ `:123` |
| `search_file_content` | `tools/search.py:203` | — | ✅ `:198` |
| `run_shell_command` | `tools/shell.py:121` | — | ✗ |
| `web_fetch` | `tools/web.py:748` | 有 `bs4`（`[web]` extra） | ✅ `:743` |
| `web_search` | `tools/web.py:1125` | 同上 | ✅ `:1120` |
| `save_memory` | `tools/memory.py:19` | — | ✗（基类默认） |
| `activate_skill` | `tools/skill.py:21` | — | ✗（基类默认） |
| `ask_user` | `tools/ask_user.py:28` | — | ✅ `:20` |
| `todo_write` | `tools/todo.py:20` | — | ✗（基类默认） |
| `check_background_agent` | `agents/tools/_bg_tools.py:32` | `bg_store is not None` | ✅ `:27` |
| `cancel_background_agent` | `agents/tools/_bg_tools.py:125` | 同上 | ✗ |

在 `BUILTIN_TOOL_NAMES` 之外注册的：

- `agent_codebase_investigator` / `agent_generalist` —— `agents/tools/_wrapper.py:224` 命名为
  `agent_{definition}`，定义在 `agentao/agents/definitions/`。**opt-in，默认关**
  （`agent.py:151 :: enable_builtin_agents: bool = False`、`embedding/factory.py:62`）。
- `complete_task` —— `agents/tools/_complete.py:33`，只注册进子 agent 的 **scoped** registry
  （`_wrapper.py:466`），从不进主注册表。
- `plan_save` / `plan_finalize` —— `tools/plan.py:17,62`，由 CLI 注册（`cli/app.py:336-337`），
  非 plan 模式的轮次不进 schema（`tools/base.py:256,276`）。
- `update_goal` —— `tools/goal.py:34`，`/goal` 活跃期间经 `add_tool` 注入。
- `mcp_{server}_{tool}`，以及宿主 `extra_tools`。

`BUILTIN_TOOL_NAMES`（`tooling/registry.py:48-64`）是 `disable_tools` / `enabled_tools` 的校验集，
由 `tests/test_host_tool_injection.py:220 :: test_builtin_tool_names_constant_in_sync` 钉住真实注册。
它的 docstring 明写范围是**注册资格、不是运行时可用性** —— 所以没装 `[web]` 时 `web_search` 依然
在册。

### 2.2 codex —— 约 50 个名字，没有固定默认

`core/src/tools/spec_plan.rs::build_tool_router()` **每轮**重建工具集。七个来源：

| 来源 | 工具 | 位置 |
|---|---|---|
| Shell | `exec_command`、`write_stdin`、`apply_patch` | `spec_plan.rs:1086`、`:1245` |
| MCP 资源 | `list_mcp_resources`、`list_mcp_resource_templates`、`read_mcp_resource` | `:1134`（仅当配了 server） |
| 核心工具 | `update_plan`、`view_image`、`clock.curr_time`、`clock.sleep`、`request_user_input`、`send_user_message_async`、`request_permissions`、`new_context`、`get_context_remaining`、`wait_for_environment`、`list_available_plugins_to_install`、`request_plugin_install`、`test_sync_tool` | `:1143` |
| 多 agent v1 | `multi_agent_v1.{spawn_agent,send_input,resume_agent,wait_agent,close_agent}` | `:1334` |
| 多 agent v2 | `spawn_agent`、`send_message`、`followup_task`、`wait_agent`、`interrupt_agent`、`list_agents` | `:1291` |
| 发现 / Code Mode | `tool_search`、`exec`、`wait` | `tools/src/tool_discovery.rs:6`、`code-mode-protocol/src/lib.rs:51-52` |
| Hosted + 扩展 | `web_search`（服务端）、`web.run`、`image_gen.imagegen`、`skills.{list,read}`、`memories.{list,read,search,add_ad_hoc_note}`、`history.*`/`notes.*`（9 个）、`get_goal`/`create_goal`/`update_goal` | `tools/hosted_spec.rs:14`；`ext/` 下六个 `ToolContributor` 实现 |

默认值在 `features/src/lib.rs` 里以 `FeatureSpec { stage, default_enabled }` 形式给出。默认开：
`shell_tool`、`unified_exec`、`view_image`、`sleep_tool`、`multi_agent`（v1）、`image_generation`、
`goals`、`skill_search`。默认关：`memories`、`multi_agent_v2`、`token_budget`、
`current_time_reminder`、`standalone_web_search`、`request_permissions_tool`、`deferred_executor`、
`code_mode`。

**codex 不带 `read_file` / `write_file` / `grep` / `glob`。** 全树 grep，`read_file` 这个名字只在
`ext/guardian-v2` 的测试假数据里出现。

有一种受限姿态值得记：guardian reviewer session 只拿到 `exec_command`、`write_stdin`、
`view_image`，且必须是 `PermissionProfile::Managed` —— 否则**一个都不给**（`spec_plan.rs:989-1037`）。

### 2.3 gemini-cli —— 26 个可注册、19–20 个已注册、**模型可见 16–17 个**

`packages/core/src/config/config.ts:3934 :: createToolRegistry()`，**启动时一次性**构建。

无条件（16 个，加 `invoke_agent`）：`read_file`、`write_file`、`replace`、`list_directory`、
`glob`、`grep_search`、`run_shell_command`、`list_background_processes`、`read_background_output`、
`web_fetch`、`google_web_search`、`read_mcp_resource`、`list_mcp_resources`、`ask_user`、
`update_topic`、`activate_skill`、`invoke_agent`。

条件注册：

| 工具 | 门控 | 默认 |
|---|---|---|
| `write_todos` | `useWriteTodos` —— Gemini-2 系 **且** 非 preview 模型 **且** tracker 关（`core/src/config/config.ts:1294`） | 随模型 |
| `enter_plan_mode`、`exit_plan_mode` | `plan`（`core/src/config/config.ts:1135`） | **开** |
| `tracker_*` × 6 | `tracker`（`core/src/config/config.ts:1137`） | **关** |

`grep_search` 是一个名字两套实现：优先 `RipGrepTool`，ripgrep 不可用时回落 `GrepTool`
（`core/src/config/config.ts:3979-4001`）。

有三个名字在 `ALL_BUILTIN_TOOL_NAMES` 里、却从不注册进主注册表 —— `read_many_files`（由 `@` 命令
处理器和 ACP session 使用：`atCommandProcessor.ts:519`、`acpSession.ts:1012`）、
`get_internal_docs`（只发给 `cli-help` 子 agent，`cli-help-agent.ts:89`）、`complete_task`
（`local-executor.ts:272`）。

**注册数不等于可见数，rev 3 写的 19–20 是注册数。** `getFunctionDeclarations()` 每次被**调用**时都
会对注册表重新过滤 —— 而它并不是每个请求调一次，见 §4
（`core/src/tools/tool-registry.ts:601-624`）：topic narration 关掉时丢弃 `update_topic`（默认
**开**，`core/src/config/config.ts:1237`）；没有任何 MCP server 暴露 resource 时丢弃
`read_mcp_resource` 和 `list_mcp_resources`；`enter_plan_mode` / `exit_plan_mode` **互斥** ——
plan 模式内藏 `enter_`，plan 模式外藏 `exit_`。所以一个没有 MCP 的裸默认会话给模型看到的是
**16–17 个**，不是 19–20。

`invoke_agent` 背后的内置子 agent：`codebase_investigator`、`cli_help`、`generalist`、`browser`
（配置门控）—— `agents/registry.ts:286-313`。

### 2.4 pi-mono —— 内置 8 个，**激活 4 个**

两层，不能混为一谈。

**`@earendil-works/pi-agent-core`**（`packages/agent/src/harness/tools/`）给嵌入方四个**可选工厂**
—— `bash`、`read`、`edit`、`write`，外加一个图像处理辅助。没有任何自动注册：`packages/agent/src`
里除 `tools/` 自身以外的全部调用点 grep 为空。

**coding-agent**（`packages/coding-agent/src/core/tools/index.ts:95`）带 8 个：`read`、`bash`、
`powershell`、`edit`、`write`、`grep`、`find`、`ls`。

**默认激活集是 4 个** —— `read`、`bash`、`edit`、`write`（`core/sdk.ts:256`、
`core/agent-session.ts:2801`）。另外 4 个装在包里但模型看不到，除非 `settings.defaultTools` /
`--tools` 点名。`powershell` 在 Windows 上**也不会**自动激活；system prompt 只是判断它在不在激活集
里（`core/system-prompt.ts:98`）。

**没有 MCP 客户端。** `@modelcontextprotocol/sdk` 只作为传递依赖出现在 lockfile 里，仓库内没有任何
`package.json` 声明它。没有 web search、没有 web fetch、没有 todo、没有子 agent、没有 plan 模式工具。
唯一的内置扩展是 `llama.cpp` —— 那是 provider 不是工具（`src/extensions/index.ts:4`）。`todo` 是
`examples/extensions/todo.ts`，示例。

---

## 3. 轴一 —— 文件**读取**是不是工具？

下表的「读」指**通用文本／源码读取或搜索**，该列刻意不含只读媒体的工具 —— rev 2 写成一个光秃秃的 0，
错就错在这个区分上。

| | 写侧文件工具 | 通用读/搜索文件工具 | 其它经工具中介的读 | 权限边界 |
|---|---|---|---|---|
| agentao | 2 个（`write_file`、`replace`） | 4 个（`read_file`、`list_directory`、`glob`、`search_file_content`） | —— | `permissions.json` 按工具挂规则，**过了 read-only preset 的**每一次调用都进引擎 |
| gemini-cli | 2 个（`write_file`、`replace`） | 4 个（`read_file`、`list_directory`、`glob`、`grep_search`） | —— | 统一 `checkPolicy` 通道 + `TOOLS_REQUIRING_NARROWING`（8 个工具需参数级收窄） |
| pi-mono | 2 个（`write`、`edit`） | 4 个（`read`、`grep`、`find`、`ls` —— 后三个默认关） | —— | **默认完全没有策略**；只有一个统一的、可选的扩展钩子 |
| **codex** | **1 个**（`apply_patch`） | **0** | **1 个** —— `view_image`，仅限图片文件 | 沙箱 profile + 审批层，**外加** `apply_patch` 内部按目标路径推导的写权限 |

**rev 1 说 codex 有 0 个文件工具，这是错的。** `apply_patch` 是模型可见的工具，其 handler
*"routes verified patches to the selected environment filesystem"*（`core/src/tools/handlers/apply_patch.rs:73`），而
`write_permissions_for_paths`（`:236-270`）会从 patch 的**目标路径**推导出
`AdditionalPermissionProfile` —— 这是参数级、经工具中介的权限，比起「沙箱是唯一边界」，它更接近
gemini-cli 的 `TOOLS_REQUIRING_NARROWING`。rev 1 从那个 0 推出的两条结论 —— codex 的权限单位**不可
能**是工具、以及三比一让 agentao 站在多数派 —— **一并撤回**。

**rev 2 接着说 codex 有 0 个*读*工具。这一条同样错，而且这才是这条轴真正的形状。** `view_image` 的
`path` 参数文档写的就是 *"Local filesystem path to an image file"*（`handlers/view_image_spec.rs:19`），它
按环境 cwd 解析该路径，并经沙箱文件系统读取 —— 先 `fs.get_metadata(...)` 再
`fs.read_file(&path_uri, ReadFileOptions::default(), Some(&sandbox))`（`handlers/view_image.rs:150-175`）。
它是 `Stage::Stable, default_enabled: true`（`features/src/lib.rs:889-893`），只要存在环境就注册
（`spec_plan.rs:1259`），因此默认会话里模型可见。所以 codex **确实**有一条经工具中介的工作区读取路径。

真正活下来、且宽度正确的说法是：**codex 没有*通用*文本／源码读取或搜索工具。** 没有 `read_file`、
没有 `grep`、没有 `glob`、没有 `list_directory`；对 `core/src/tools`、`ext/`、`tools/src` 做全量名
字扫描，`read_file` 只出现在 `guardian-v2` 的测试假数据、`mcp.rs` 里一个挂在假想 `filesystem`
server 下的示例，以及 `notes.read_file`（history-notes 扩展**自己的**笔记文件，不是工作区）。除图片
以外的一切都走沙箱下的 `exec_command`。这仍是一个真实且少见的立场 —— 但它是「没有通用读取器」，不是
「没有读取器」。

**pi-mono 那一行也要改。** rev 2 写「按工具，`bash` 是逃生口」；根本没有可逃的按工具边界。在没有任何
扩展注册 `tool_call` 处理器时，`beforeToolCall` 直接返回 `undefined`
（`core/agent-session.ts:489-492`），调用随后照常执行（`agent-loop.ts:617-624`）。权限门是
**示例**（`examples/extensions/permission-gate.ts:13`），不是策略。准确的一行是：**默认没有权限策
略；只有一个统一的可选拦截钩子。**

**对 agentao 的含义也相应变窄。** `runtime/tool_planning.py::_decide`（`:487-518`）是三层，而第一层
**不是**引擎：read-only 模式 preset 在 `:487-495` 直接返回 `DENY`，**根本不会去问引擎**。第二层
（引擎，对其余每一次调用）和第三层（`ASK` 或无匹配时落到 `requires_confirmation`）在其后。这套设计
对**读**这一面仍然需要「工具就是权限单位」—— 而这正是 codex 没有东西可借的地方 —— 但它不能作为多数
派的证据，§8 也不再声称在统一通道上领先 gemini-cli（后者有）。

## 4. 轴二 —— 工具集什么时候定下来？

codex 每轮按三组互相独立的输入重算 —— feature flag、**逐模型元数据**
（`model_info.experimental_supported_tools`、`apply_patch_tool_type`、`supports_search_tool`、
`shell_type`）、以及 **provider 能力**（`provider.capabilities().web_search`、`.namespace_tools`）
—— `spec_plan.rs:124-190`、`:1143-1272`。

**这份元数据从哪来很要紧，rev 3 说错了。** rev 3 称它是「模型自己声明的结构化能力」。不是：
`ModelInfo` 来自**harness 与 backend 维护的目录**（内置或远端拉取），配置里的模型串是被
`find_model_by_longest_prefix`（`models-manager/src/manager.rs:617-631`）按**slug 前缀**匹配上去的，不是协商出来的。
目录里没有的 slug 会走 `model_info_from_slug`（`model_info.rs:142`），打一条
*"Unknown model {slug} is used. This will use fallback model metadata"* 的告警并合成一份最小描述符。
全程既没有问过模型，模型也没有发送任何东西。

**但并不统一，rev 2 说过头了。** `view_image` 的注册条件只有
`environment_mode.has_environment() && features.enabled(Feature::ViewImage)`（`spec_plan.rs:1259`）
—— 注册期**根本不看** `input_modalities`。一个纯文本模型照样能在 schema 里看到 `view_image`，然后在
*执行*期被 `FunctionCallError::RespondToModel` 拒掉（`handlers/view_image.rs:97-105`）。`model_info` 确实会
影响那个工具的 *schema*（`can_request_original_image_detail`），但影响不到它在不在。所以对 codex 的
诚实描述是**「一部分工具按声明能力收窄，另一部分先放进来、执行期再拒」**—— 是混合策略，不是干净的能
力门控。

**「每轮还是每会话一次」本身就是错的轴，而 rev 4 仍以它开头。** 本节每一版都写「另外三家都是定死一
次」，然后跟着几条各自承认某种后续变化的要点。rev 5 把它拆成三阶段，却仍把注册表的*变更*放进了激活
集那一列 —— `registerTool` / `unregisterTool` 和 `add_tool` 改的是注册表，不是它上面的一层选择。
应该是**四**个阶段：

| | 初始构建 | 之后注册表是否变更 | 注册表之上的激活选择 | schema 何时投影给模型 |
|---|---|---|---|---|
| codex | **每轮**（`build_tool_router`，`spec_plan.rs:124`） | 不适用 —— 重建本身就是变更 | 不适用 | **每轮**，取该轮刚建好的注册表 |
| gemini-cli | 启动时一次（`createToolRegistry`） | **会** —— 技能发现、MCP 连上时 `registerTool` / `unregisterTool` | 不适用 | **只在 `startChat`、显式无参 `setTools()`、或模型变化时重建** —— `core/src/core/client.ts:801` 每个请求都调 `setTools(modelToUse)`，但模型没变时它**直接早返回**（`core/src/core/client.ts:311-313`）；`PLAN`/`YOLO` 切换调的是无参形式，绕开该判断（`core/src/config/config.ts:2810-2819`） |
| pi-mono | 在 `_buildRuntime`（`core/agent-session.ts:2757`）—— **不是一次**：`reload()` 会再跑一遍（`core/agent-session.ts:2820`） | **会** —— `_refreshToolRegistry` 重建 `_toolRegistry`（`core/agent-session.ts:2664`） | **会** —— `setActiveToolsByName`，由扩展驱动（`core/agent-session.ts:971`） | **下一个 agent turn 生效** —— 该方法会重建系统提示词，其自身契约写着 *"Changes take effect on the next agent turn"*（`core/agent-session.ts:965-971`）；激活集不再过滤直接投影 |
| agentao | 构造期一次（`register_builtin_tools` → MCP → agent → `extra_tools` → `apply_enabled_tools`） | **会** —— `add_tool` 注入（如 `/goal` 活跃时的 `update_goal`） | 不适用 —— `enabled_tools` 只在构造期裁剪一次 | **每次 `chat()` 一次，在内层 LLM 循环之前** —— `to_openai_format(plan_mode=…)` 在 `runtime/chat_loop/_runner.py:348` 调用，为整轮快照 schema；内容上非 plan 轮次会扣掉 plan 专属工具（`tools/base.py:276`） |

rev 5 还写了「没有任何两家是在同一个阶段上变的」，紧接着下一段又说 gemini-cli 与 agentao 都改变投影
—— 同一节里自相矛盾。这几列并不构成划分：**四家里有三家在初始构建之后还会改注册表**，只有 pi-mono
另有一层激活选择，也只有 codex 每轮从头重建。这条轴真正买到的是「必须点名是哪一列」这条纪律：任何关
于「工具集何时定下来」的断言，不说明是四列中的哪一列就没有意义。

**最后一列才决定「构建后的变更何时真正到达模型」**，而 rev 7 对其中两家答的是*内容*不是时点，rev 8
又把 gemini-cli 的时点写反了。时点差别很实在，而 **gemini-cli 是最松的、不是最紧的**：`GeminiChat`
持有一份缓存的工具表；虽然 `core/src/core/client.ts:801` 每个请求都调 `setTools(modelToUse)`，但模型没变时
`setTools` 会短路返回（`core/src/core/client.ts:311-313`）。`registerTool`（`core/src/tools/tool-registry.ts:271`）只写
`allKnownTools`，不使任何缓存失效。所以一次注册表变更到达模型的时机是 `startChat`、显式无参
`setTools()`、或换模型 —— **不是**下一个请求。pi-mono 与 agentao 都推迟到**轮边界**，而 agentao 这
条最严 ——
`agent.tools.to_openai_format(...)` 在 `runtime/chat_loop/_runner.py:348` 只跑一次，位于内层工具调
用循环**之上**，所以轮中途加的工具，哪怕循环再跑二十次也看不见，要等到*下一次* `chat()`。`add_tool`
自己的 docstring 就写着这条契约（`agent.py:906-914`）：schema 每次调用快照一次，而工具*执行*是按活
注册表解析名字的。

**这份松弛在 gemini-cli 里是一个活的缺陷，也就是发现 6。** `Config.reloadSkills()` 会注销
`ActivateSkillTool` 并重新注册一个 schema 里枚举了新发现技能的实例
（`core/src/config/config.ts:3693-3699`），随后只调 `updateSystemInstructionIfInitialized()`。没有
任何地方调 `setTools()`，而每请求那次调用又在模型未变时短路 —— 所以运行时重载技能之后，模型拿到的
仍是**上一版**的 `activate_skill` 枚举，直到别的什么东西强制刷新。agentao 从构造上到不了这个状态：
它在每次 `chat()` 开头都从活注册表重新投影（`runtime/chat_loop/_runner.py:348`）。记为同行观察，不
是行动项 —— 那是 gemini-cli 的 bug；它在这里唯一改变的是：agentao 更严的那次快照是一条*安全*性质，
而不只是「更严」。

gemini-cli 的注册也**并非**纯宿主配置：`write_todos` 门控在 `isGemini2Model(this.model)` 与
`isPreviewModel(...)` 上（`core/src/config/config.ts:1293-1297`）—— 这是**模型名启发式**，在构造期读
一次。rev 1 把 gemini-cli 记成「纯宿主配置」，与它自己的 §2.3 矛盾。

**发现 1。** 这条轴上有三个位置，不是两个。codex 读**它自己的 harness 与 backend 维护的结构化逐模
型目录**，按 slug 前缀匹配、未命中则带告警回退；gemini-cli 读**模型名**并据此推断一个工具；agentao
和 pi-mono **完全不读模型**，只按宿主配置门控（`disable_tools`、`enabled_tools`、mode preset）。
agentao 在最远端；一个吃不下某工具的模型，没有任何途径让这件事变成一份更窄的 schema，故障只能以
「这一轮跑坏了」的形式浮现。

**卡点不在传输、在维护 —— 但「维护什么」不是定死的，而 rev 5 把它写得过于具体。** rev 3 论证说
codex 的答案「需要 agentao 的 provider 并不发送的结构化能力声明」；那个说法在元数据被追到目录侧之后
就作废了。rev 5 转而写「两个同行维护的都是逐模型表」—— 这对 codex 成立，对 gemini-cli **不成立**：
`isGemini2Model` 就是 `/^gemini-2(\.|$)/.test(model)`（`config/models.ts:458`），其注释称之为
*"legacy behavior"* —— 那是正则，不是目录。codex 本身也是混合的：一部分决策看 `ModelInfo`，一部分看
`provider.capabilities()`，而 `view_image` 两者都不看。

所以形态并不是定死的。两个同行真正共有的是：**有人在拥有并持续维护一条兼容性事实** —— 可以是一条目
录条目、一个正则，也可以是一个能力字段。填上这个空白的代价是*某种被拥有、被持续维护的兼容策略*，这
比「一张模型目录」更弱也更诚实，而且把便宜的选项（正则、逐 provider 开关）留在了桌面上。

**连着两版把这个先例弄错，而且方向相反；下面是源码说的话。** rev 4 引的是「`isRecoverableLength`
在 `pi-mono-pull-review-2026-08-09` 里被自我否掉」—— 那份文档不存在（`docs/design/` 只有
`-2026-08` 和 `-2026-08-21`），该引用作废。rev 5 转而辩护实质，说 agentao「没有对应物」，并拿
`grep -r context_window agentao/` = 0 当证据。**那是搜错了字段。** 那里的 `maxTokens` 是被请求的
*输出*上限、不是上下文窗口，而 agentao 有语义相同的旋钮：`LLMClient.max_tokens`
（`llm/client.py:139,188`），宿主可设，并由 ACP 的 `maxTokens` 映射过来
（`acp/session_set_model.py:10`）。**在主 agent 路径上**它是被显式转发的 ——
`runtime/llm_call.py:138` 传 `max_tokens=agent.llm.max_tokens`，随后 `_build_request_kwargs` 发出
`max_tokens` / `max_completion_tokens`（`llm/client.py:419-421`）。但**并非处处转发**：`chat()` 与
`chat_stream()` 的该参数默认都是 `None`（`llm/client.py:430,534`），kwarg 只在 `if max_tokens` 时
才加（`:419`），所以压缩摘要那条路径 —— 它调的是 `chat(messages=…, tools=None)`
（`context_manager.py:1573`）—— 整个字段都不发。rev 6 那句「每次请求都发出去」范围过宽。

真正的差别在**默认值，不在字段**。pi 解析这个数是 `options?.maxTokens ?? model.maxTokens` 再做钳制
（`ai/src/api/simple-options.ts:34`），所以调用方不指定时会落到**逐模型注册表里的值**；agentao 的构
造器默认是对所有模型一律 `65536`（`llm/client.py:139`）。`usage.output < desiredMaxOutput` 只有在这
个数确实等于端点会放行的量时才成立 —— 向一个会**静默钳制**输出到 8192 的端点发 65536，每一次真实的
满输出停止都会满足该谓词。所以这个隐患被两重限定：只在**主 agent 路径**（唯一转发该值的路径），且只
在**静默钳制而非报错**的端点上。诚实的裁定比前两版都窄：这次借鉴**在宿主逐模型设置 `max_tokens` 时
可移植，在 agentao 出厂默认 + 这类端点上不安全** —— 那是个默认值问题，跟能力目录无关。

**`supportsFinishReason` 也是逐模型的**，与 rev 5 所说相反：pi 允许它在 **provider 与 model 两级**
配置（`test/model-registry.test.ts:771-778`）。不属于逐模型的是 agentao 反向采纳它的*理由* ——
`INCOMPLETE_ANSWER_REASONS` 里每个取值都会变成 CLI 的 error envelope，加进去等于让每个不发该字段的
provider 硬失败（`pi-mono-pull-review-2026-08.md:58`）。

**结论：本仓在目录这个问题上没有先例。** 两次逐模型借鉴被否，各有各的理由 —— 一个是默认值隐患、一
个是 error-envelope 隐患 —— 都不是对「agentao 该不该拥有逐模型元数据」的裁定。那个问题从未被提出过。
**记为空白，不是工作项** —— 需求未测量。

## 5. 轴三 —— 默认暴露面

| | 树内 | 默认可见 |
|---|---|---|
| codex | 约 50 | 按构造就不是固定数 |
| gemini-cli | 26 可注册（19–20 已注册） | **16–17** |
| agentao | 15 + 6 个条件/scoped | 嵌入 **11** / CLI 工厂 **13**；装 `[web]` 各 **+2** |
| pi-mono | 8 | **4** |

pi-mono 的 4 个是四家里最克制的，而且这个压制是**刻意且有补偿**的：`grep` / `find` / `ls` 写好了、
测过了，被排除在默认激活集之外（`core/sdk.ts:256`），系统提示词还替它们兜底 —— `grep`/`find` 不在
时会加一条 *"Use bash for file operations like ls, rg, find"*（`core/system-prompt.ts:99-111`）。

**但「为什么压制」不在源码里，rev 3 却照样断言了。** rev 3 称它是「明确的 context 成本赌注」，还说
codex「得出同一结论」，把两者算作两个独立数据点。两条都没有证据：pi-mono 的代码只能证明*工具被压制*
以及*用 shell 顶上*，证明不了原因；而 codex 从没写过这些工具，也不构成任何被记录下来的判断。请把
context 成本这个读法当作**推测、未测量** —— 站得住的只是形状（两个 harness 都给出很小的默认面，并
把通用文件工作导向 shell），不是共同动机。

**发现 3。** agentao 的 `read-only` 模式拒掉任何 `is_read_only` 为 `False` 的工具
（`tool_planning.py:487`，理由 `mode-preset:read-only`），而基类默认就是 `False`
（`tools/base.py:117-126`）。有三个工具从不覆写它，因此在 `/mode read-only` 下被拒：`save_memory`、
`activate_skill`、`todo_write`。机制是有文档的（`agentao/docs/reference/configuration.md:171` ——
「empty preset；`ToolRunner` short-circuits on `tool.is_read_only`」），所以这是既定规则的正确推论、
不是缺陷。

**rev 1 的同行证据错了两处。** 它引 `PLAN_MODE_TOOLS`（`tool-names.ts:283`）当作 gemini-cli 的显式
只读清单 —— 但那个常量**全仓没有任何运行时消费者**；它自己的注释说是用来生成 plan 模式提示词的，而
没有一处读它。真正生效的策略在 TOML 里：`read-only.toml:30-55` 和 `plan.toml`。而那份策略对
`activate_skill` 给的是 **ask** 不是 allow（`plan.toml:105-110`，跟 `ask_user`、`web_fetch` 编在一
组）。rev 1 还写了三者「都不碰工作区」；`save_memory` 经 `MemoryManager.upsert`
（`memory/manager.py:80`）落到项目或用户 SQLite store，这一条对三者之一不成立。

**rev 2 又把这个 ask 说错了。** 它一面说 gemini-cli 在 `activate_skill` 上「同意 agentao」，隔两句又
说 ask 是「第三个位置」—— 两者不能同时成立，而且前者是错的：ASK 不等于 DENY。更要紧的是那条 ask 规
则带 `interactive = true`（`plan.toml:110`），所以**非交互**运行时它不适用，会落到 plan 模式的
catch-all（`toolName = "*"`、`decision = "deny"`，`plan.toml:76-80`）。gemini-cli 的真实行为是
**交互 → ASK，非交互 → DENY**。

**换成正确的证据后，观察仍然成立、而且更窄。** `read-only.toml:30-55` 放行
`tracker_create_task`、`tracker_update_task`、`tracker_get_task`、`tracker_list_tasks`、
`tracker_add_dependency`、`tracker_visualize`、`update_topic`、`complete_task`，注释写的是
*"safe as they only modify internal state"*。agentao 的 `todo_write` 正是这一类的直接对应物，被拒；
`activate_skill` 也被拒 —— 而 gemini-cli 只在非交互时到达 DENY，交互时给的是 ASK。那个中间位置在
**这道门上**表达不出来：read-only preset 分支在布尔字段 `tool.is_read_only` 上
（`tool_planning.py:487`），它只有两个取值。这**不是**说 agentao 表达不了 ASK —— 权限引擎的 `ASK`
在第二层，对每一个被 preset 放行的调用都正常工作。agentao 走到自己的答案靠的是继承默认值、不是做一
次决定。对不对是维护者的判断；本文只记录这件事从没被明确决定过。

## 6. 轴四 —— 非核心工具住在哪里

| | 机制 | 例子 |
|---|---|---|
| codex | feature flag 门控下的 `ToolContributor` 扩展 | `skills.*`、`memories.*`、`history.*`/`notes.*`、`get_goal`/`create_goal`/`update_goal`、`image_gen.imagegen` |
| gemini-cli | 硬编码在 `createToolRegistry` 里、由布尔量门控 | `tracker_*`、plan 模式那一对、`write_todos` |
| agentao | 内置清单 + CLI 注入 + 宿主 `extra_tools` | `activate_skill`、`save_memory`、`todo_write`；`update_goal` 经 `add_tool` |
| pi-mono | **树内什么都没有** —— 只有用户扩展 | `todo` 以示例扩展形式提供 |

两处趋同值得记：

- **`complete_task` 在 agentao 和 gemini-cli 里都是子 agent 作用域的**，且各自独立：
  `agents/tools/_wrapper.py:466` 把它注册进 scoped registry；`local-executor.ts:272` 只发给本地执
  行器。两边都不在主注册表暴露。这就是发现 5 —— 无动作，但这种两仓一致应当抬高「把它提升到主注册
  表」这类提议的门槛。
- **skills 与 memory 作为模型可见工具**：codex 把 `memories.{list,read,search}` 暴露给模型；agentao
  刻意只暴露**写**（`save_memory`），把 search/delete/clear 留在 CLI（`/memory …`）。这个不对称是有
  文档、有意为之的 —— 本次对照不动它，但 codex 是唯一走了反方向的同行，所以这个不对称是一个**有活
  反例**的选择，而不是行业默认。

## 7. 死名字与半注册

四家都会攒这种东西；有意思的是各自的守卫指向哪个方向。

**agentao —— 发现 2。** `agentao/tools/agents.py` 定义了 `CLIHelpAgentTool`（`:8`，名字
`cli_help`）和 `CodebaseInvestigatorTool`（`:43`，名字 `codebase_investigator`）。两者都导出了
（`agentao/tools/__init__.py:10,32`），而且**在 `agentao/` 和 `tests/` 里没有任何一处实例化**。
`tooling/registry.py:44-46` 的注释说 agent-path 工具「(codebase_investigator / cli_help) register
elsewhere and are intentionally out of scope」—— 只对一半。`codebase_investigator` 确实作为 agent
**定义**存在，注册为 `agent_codebase_investigator`（`_wrapper.py:224`），所以注释指的是一个换了名字
的真实东西。`cli_help` 既没有定义文件（`agents/definitions/` 只有 `codebase-investigator.md` 和
`generalist.md`），也没有实例化 —— 它是注释自己编出来的名字。

**这能证明什么、不能证明什么。** 它能证明**树内没有实例化**、没有默认注册 —— 不能证明这两个类不可
达。它们都是 `agentao.tools` 的公开导出，而 `extra_tools=`（`agent.py:194`）是有文档的宿主注入点，
把其中任何一个注册成活的模型工具都是合法用法。所以这是**一句过时注释加两个从不默认注册的导出**，不
是死代码：删掉它们是对公开面的 API 变更，便宜的修法是改注释。rev 1 称其为「死类」，说过头了。

**gemini-cli —— 发现 4。** `save_memory` 死得一模一样：`memoryTool.ts` 现在只剩 GEMINI.md 文件名常
量，全仓没有 `new MemoryTool`。更有用的是反方向已经出事了：`list_background_processes` 和
`read_background_output` 是真正注册的模型工具（`shellBackgroundTools.ts:75,253`，注册于
`core/src/config/config.ts:4028-4037`），却**不在 `ALL_BUILTIN_TOOL_NAMES` 里**，于是 `isValidToolName()` 对两者都
返回 `false`。`agentLoader.ts:103` 把 zod `.refine()` 卡在这个函数上，所以**用户自定义 agent 文件
只要列出这两个名字之一就被整体拒绝**。policy 加载器（`toml-loader.ts:278`）只在近似拼写时告警，而
这两个名字离所有内置名都远，因此那边静默通过。

这件事对 agentao 不只是同行八卦，原因在于：对 `ALL_BUILTIN_TOOL_NAMES` 唯一的那条测试
（`tool-names.test.ts:50`）是遍历常量、断言每一项都合法 —— 它拿清单跟**自己**比，方向上不可能失败。
真正漂移的那个方向（注册表 → 常量）没有测试。agentao 的 `BUILTIN_TOOL_NAMES` 是同形状常量、同一份
职责，而且**有**反方向的那条测试（`test_builtin_tool_names_constant_in_sync`）。那条测试在干实活；
这就是留着它的同行证据。

## 8. agentao 领先的地方

记下来，免得对照变成单向的。**两条**，比 rev 1 的四条、rev 2 的三条又少 —— 每条都在各自锚点上核过，
而且都不是说其他三方没有可比之物，只是说 agentao 的那个形态更严：

> **rev 2 撤回。** rev 1 的头条是「每次工具调用都过的权限引擎……其他三方没有等价的统一通道」。两半
> 都错：agentao 的 read-only preset 在引擎**之上**就返回 `DENY`（`tool_planning.py:487-495`），所以
> 这个通道不是全覆盖；而 gemini-cli 的 scheduler 对每一个通过校验的调用都跑 `checkPolicy`
> （`scheduler.ts:648-652`）。这条轴上 agentao 与 gemini-cli **齐平**，codex 在工具层之前就算完，
> pi-mono 没有引擎 —— 是持平，不是领先。

1. **两个工具选择旋钮都会拒绝未知名字、而不是静默空转** —— 但走的是**两套不同机制**，rev 3 错把它
   们并成了一条。`enabled_tools` 对**活注册表 ∪ `BUILTIN_TOOL_NAMES`** 校验
   （`tooling/registry.py:195-205`），因此还能接受只有接线之后才存在的 **agent-path** 名字 —— 但
   **接受不了 MCP 名字**：带 `mcp_` 前缀或 plan 专属的条目会被更早的保留名守卫拒掉
   （`agent.py:449-452`，由 `tests/test_host_tool_allowlist.py:138` 钉住），根本走不到活注册表那一步。
   rev 4 称它接受 MCP 名字，不成立。`disable_tools` 只对**静态常量**校验（`agent.py:466-472`），这
   也是它的报错只说「只有内置工具可以禁用」的原因。两者都带那条「注册资格 ≠ 运行时可用性」的规则，使得没装 `[web]` 时 `web_search`
   依然合法。pi-mono 的 `--tools` 是静默过滤（`core/sdk.ts:258-263`）；gemini-cli 的 `coreTools`
   按前缀子串匹配（`core/src/config/config.ts:3953-3959`），根本报不出未知名字。
2. **模式的*进入与退出*是宿主命令，不是模型工具。** agentao 用 `/plan` 切换姿态，模型手里从来没有
   一个能改自己权限模式的工具。gemini-cli 把 `enter_plan_mode` / `exit_plan_mode` 做成了模型工具。
   rev 3 称它们「始终可见、只在执行期管」—— 这是**错的**：两者在 schema 里互斥
   （`core/src/tools/tool-registry.ts:617-624` 在 plan 模式内藏 `enter_`、模式外藏 `exit_`），模式切换还会重发列表
   （`core/src/config/config.ts:2810-2819`）。gemini-cli 跟 agentao 一样是从 schema 里扣。**唯一**
   活下来的差别是：它的模型手里终究有一个能改自身权限姿态的工具；codex 和 pi-mono 同样没有，所以是
   3:1。`plan.toml:68-72` 是叠在上面的第二层，不是机制本身。

> **rev 3 撤回。** rev 2 的第二条领先项是「子 agent 工具显式继承父级绑定」。gemini-cli 做的是同一件
> 事：`local-executor.ts:190-200` 用**父级的** `context.toolRegistry` 构建子 agent 注册表，而
> `core/src/tools/tools.ts:480` 的 `clone(messageBus)` 是一次浅拷贝
> （`Object.assign(Object.create(proto), this)`），只替换 message bus，`config`、目标目录、文件系
> 统绑定全部带过去。agentao 的 `_bind_and_register` 是同一个想法换了种语言，不是更严。持平。
>
> rev 2 的第三条领先项是「plan 专属工具从 schema 里扣掉、而不是执行期拦」，而 §9 把它推广成
> 「agentao 把 plan 模式挡在工具面之外」—— 这与 **§2.1 自相矛盾**，那里 `plan_save` /
> `plan_finalize` 在 plan 轮次里就是模型工具（`cli/app.py:336-337`、`tools/base.py:276`）。扣 schema
> 这个机制是真的，但活下来的论断是关于*模式切换*的，也就是上面第 2 条。

## 9. 候选借鉴 —— 一项都未授权

| 候选 | 结论 | 理由 |
|---|---|---|
| codex 的逐轮模型能力门控 | **搁置**，需求门控 | 空白是真的（§4）。要填上它，agentao 就得拥有**某条**被持续维护的兼容性事实 —— rev 5 写的是「一张逐模型表」，那是把形态写死了：gemini-cli 的是正则（`config/models.ts:458`），codex 则是目录 + `provider.capabilities()` 混用。rev 5 还断言 agentao 在 provider-neutral 前提下**维护不了**；本次对照没有任何证据支持，而 §4 记录了这个问题从未被提出 —— 所以这一条按**需求未测量**搁置，既不是按已确立的不可行，也不预设实现形态。野外观测到具体的模型拒绝时再重开。 |
| codex 的 `get_context_remaining` / `new_context` | **不做** | `codex-compaction-vs-agentao.zh.md` 已分析过；token-budget 那一格的结论是「模式默认关闭」。本次对照为该结论再添一个数据点：gemini-cli 和 pi-mono 都没有等价物，所以 codex 在这条上是 1/4，不是常态。 |
| gemini-cli 把**模式进入/退出**做成模型工具 | **不做** | rev 3 重述、rev 4 收窄：1/4 的差别是模型手里**终究有** `enter_plan_mode` / `exit_plan_mode` —— 一个能改自身权限姿态的工具。这**不是**「plan 模式挡在工具面之外」（agentao 的 `plan_save` / `plan_finalize` 在 plan 轮次里就是模型工具，§2.1），也**不是**「扣 schema vs 执行期拦」（gemini-cli 同样按模式扣这一对，`core/src/tools/tool-registry.ts:617-624`）。 |
| pi-mono 的极小默认集 | **不做** | agentao 的 11–15 处于中位。§5 记的那次一致**只在形状上** —— 两个 harness 都给出很小的默认面，并把通用文件工作导向 shell。rev 4 已把共同*动机*降级为推测，而这一行还写着「讲的是 context 成本」，在此一并撤回。agentao 的小面旋钮是 `enabled_tools` / `disable_tools`，属宿主侧，不是出厂默认。不提议改动。 |
| gemini-cli 的 `TOOLS_REQUIRING_NARROWING` | **观察** | 授予会话级批准时要求参数级收窄 —— agentao 的引擎能按参数匹配，但没有「这个工具不经收窄不得一揽子批准」这个概念。今天不算空白，因为 agentao 没有那种形状的会话级授权界面；一旦要加就相关了。 |

## 10. 如何复核

每一方都有一个*初始*构建的入口，从那里开始。但那不是全部 —— §4 的四阶段表列出了变更点与投影点，
四家里有三家在这个入口跑完之后还会改注册表，而 pi-mono 连这个入口本身都会在重载时再跑一遍。

```
codex       codex-rs/core/src/tools/spec_plan.rs::build_tool_router
            → add_core_tool_sources (:985) → 四个 add_* 函数
            → features/src/lib.rs 查每一个 default_enabled
gemini-cli  packages/core/src/config/config.ts::createToolRegistry (:3934)
            → tools/tool-names.ts::ALL_BUILTIN_TOOL_NAMES 拿它自称的清单
            → 两者取差，差集就是 §7
pi-mono     core/agent-session.ts::_buildRuntime (:2757) —— 真正的入口；reload()
            会再跑一遍 (:2820)。tools/index.ts::allToolNames (:95) 只是名字集合，
            不是构建步骤
            → core/sdk.ts:256 + core/agent-session.ts:2801 拿激活集
agentao     agentao/agent.py::_wire_tooling (:578) —— 完整入口；它依次调用
            register_builtin_tools、MCP、agent、extra_tools、apply_enabled_tools
            → tooling/registry.py::BUILTIN_TOOL_NAMES (:48) 及其同步测试
            → cli/app.py:336、tools/goal.py、agents/tools/_wrapper.py 拿其余部分
```

三条方法笔记，都是吃过亏才写下的 —— 第三条来自 rev 2，栽在本文自己的引用上：

- **把名字解析到字符串字面量。** 四家里三家经常量间接，而 gemini-cli 的常量和注册表已经对不上。一份
  常量**名**的清单会把发现 4 整个漏掉。
- **工具类存在 ≠ 工具被注册 —— 而且「被注册」不止一个去处。** rev 1 把四个名字混作一类称「模型不可
  达」；实际只有两个是，且原因不同。`save_memory`（gemini-cli）是真死 —— 全仓无实例化。
  `read_many_files` 有实例化，但由**宿主**调用（`@` 命令处理器和 ACP session），从不进任何模型的工
  具表。`get_internal_docs` **对模型可达** —— `cli-help-agent.ts:88` 把它交给 `cli_help` 子 agent
  自己的模型，而既然 §6 把子 agent 作用域的 `complete_task` 纳入了范围，这一个就不能排除为不可达。
  `cli_help`（agentao）树内无实例化，但它是公开导出、`extra_tools=` 可以注册。所以：先 grep
  **实例化**，再问**它落进哪个注册表** —— 主注册表、子 agent scoped、还是只在宿主侧。

- **引用里的 basename 不是地址。** rev 1 写了 `apply_patch.rs:73`、`config.ts:1135`、
  `registry.py:196`；而 codex 有**四个** `apply_patch.rs`，pi-mono 另有一个 `config.ts`，agentao 的
  `mcp/` 下另有一个 `registry.py`。解析器每一处都指错了文件，最后那处还差一行（拼写守卫起于
  `:195`，`:194` 是空行）。路径要限定到在本仓内唯一，然后**把每一条引用机检回源码** —— 能抓到这类
  错的是把 anchors 跑回源码，不是重读正文。
