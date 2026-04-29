# 元认知边界：注入式协议设计

**状态：** 设计记录。决策时间 2026-04-29。实现设计推迟。
**读者：** agentao 维护者，以及考虑 self-vs-project 边界语义的宿主集成方。
**配套：** `metacognitive-boundary.md`（英文版）。

## 问题

LLM 驱动的 Agent 普遍把两类操作混淆：

- **配置自身**：skills、memory、system prompt、工具权限、MCP。
- **修改宿主项目**：源码、测试、配置、依赖、脚本。

具体翻车场景：

- 用户说"改下你的回答风格"——Agent 直接修改了项目里的 prompt 模板，而不是更新自身配置。
- 用户贴了 GitHub URL 让"安装这个 skill"——Agent 把 URL 写成项目里一个 TODO 注释，
  而不是安装到 Agent 的 skills 目录。
- 在 Agent 框架自己的源码仓库里（比如 agentao 本身），用户说"调一下你的 tool 执行
  循环"——"你"在 (a) Claude Code 自己的循环 与 (b) 正在开发的 agentao 模块 之间
  真的有歧义。

"元认知边界"的目标是显式区分：哪些操作针对 **Agent 层**，哪些针对 **项目层**，哪些
属于其它类别。

## 实证依据

调研 13 家在产 Agent（Cursor、Copilot Chat、Aider、Continue.dev、Devin、Windsurf
Cascade、Claude Code、openai/codex、sst/opencode、charmbracelet/crush、block/goose、
princeton-nlp/SWE-agent、All-Hands-AI/OpenHands）的系统提示。

| 模式 | 观察到的家数 | 备注 |
|---|---|---|
| 身份开头 "You are {AGENT_NAME}, …" | 除 SWE-agent 和 Aider 外都是 | SWE-agent 用 1 句话任务化，Aider 用 "Act as"（角色而非身份） |
| "you" = Agent，"USER" = 用户 | 全部一致；只有 Cursor 和 Cascade 显式声明 | Cursor 原话："Refer to the USER in the second person and yourself in the first person." |
| 用 prose 声明 self-config-vs-project 边界 | **0 家** | 全部靠结构或隐式约束 |
| 用结构表达 self-config-vs-project 边界 | Claude Code（文件系统锚点：`~/.claude/`、CLAUDE.md、hooks-as-user、Skills RPC）；Continue.dev（chat/plan/agent 三模式）；Goose（main/subagent/tiny 三层）；Devin（web 应用 vs 沙箱分层，产品级强制）；OpenHands（`<UNTRUSTED_CONTENT>` XML 信封） | 主流模式 |
| 常驻执行前自检 | 0 家 | 全部交给单工具门控、模式权限、或隐式信任 |
| 提示里硬编码平台路径 | 0 家 | 路径由 harness 在装配时注入 |
| 承认 "harness 自我开发" 递归 | **0 家** —— 包括公开"用 Devin 开发 Devin"的 Cognition、本身就是可编辑 npm 包的 Claude Code | 行业普遍盲区 |

**核心结论：** 真正成功界定边界的 Agent 都不靠 prose 声明，而是把每个 Agent 层概念
**绑定到一个可寻址的结构句柄**——路径、模式、tier、信封。声明式的"你不应该……"列表
**不是工作系统的实际做法**。

完整调研报告归档在用户本地 plan 文件中；关键引用直接收录在文末参考一节。

## 决策

agentao **不** 在系统提示里硬编码一段固定的元认知边界。
agentao 提供：

1. **Schema**——每个宿主的边界都必须满足的不变量。
2. **默认内容集**——宿主什么都不做时注入的默认值。
3. **宿主覆盖协议**——嵌入方提供身份、路径、词汇表、消歧默认的契约。

理由：agentao 的核心定位是**嵌入式 harness**。调研的 13 家全部是单租户 CLI，掌控
完整 UX，所以可以把边界焊死。agentao 跑在异构宿主里（CLI、IDE 插件、内部数据工具、
面向 C 端的 SaaS），UX 语义差异巨大——什么算"项目对象"、"agent home"在哪里，本质上
就是宿主决定的。硬编码会逼所有宿主套用 CLI 形态的心智模型。能力注入本来就是
agentao 在工具、skills、memory backend、MCP 上的纪律；元认知边界属于同一类问题。

## Schema（agentao 定义的不变量）

跨宿主稳定——宿主无法 opt out，只能填充。

1. **对象分类。** 每个操作恰好归入下列之一：
   - **项目对象**——宿主声明的工作区下的文件。
   - **Agent 对象**——宿主声明的 agent home 下的 Agent 层状态（skills、memory、
     工具配置、系统提示）。
   - **外部对象**——通过 WebFetch / API / MCP 访问的资源。
   - **用户意图对象**——不需要落盘的文本产出。
2. **结构优先于声明。** 边界活在*路径和类型*里，不活在禁令里。注入的提示应该*描述
   东西在哪里*，而不是说教什么不该做。
3. **歧义触发追问，不默认归类。** 当指代真正歧义（"你"代词、横跨工作区与 agent
   home 的路径、没有目标的 *install* 类动词）时，必须追问，不得静默默认。
4. **代词规则。** "你"指 Agent，"USER"指用户——除非宿主显式声明项目里有同名标识符。
5. **Harness-self-development opt-in。** 当宿主声明工作区是某个 Agent 框架的源码
   （包括 agentao 自己，或其它 Agent 项目），该工作区的优先级反转：项目内
   匹配 Agent 层名称的文件（`skills/`、`memory/`、`agent.py` 等）是**项目对象**，
   不是 Agent 对象。修改 Agent 自身状态依然走 agent home。

## Content（宿主注入）

宿主在嵌入时填入 schema 槽位的值：

| 槽位 | 宿主职责 | 示例 |
|---|---|---|
| 身份语句 | Agent 在这个产品里是什么 | `"我是 Foo 助手，嵌入在 Foo Workbench 里。"` |
| 工作区描述 | "项目" 在这里指什么 | `"位于 /workspaces/{user}/{project} 的活跃工作区"` |
| Agent home 位置 | Agent 层状态在哪里 | `"~/.foo/agent/"`，或远程，或无 |
| 项目词汇表 | 项目对象的领域名称 | `"数据集"`、`"notebook"`、`"flow"` |
| 消歧默认 | "你" 在这个 UI 里如何解析 | 通常是 Agent；某些产品可能不同 |
| Self-development 标志 | 工作区是不是 Agent 框架的源码？ | 布尔；切换 schema 不变量 #5 |

## 默认内容集

agentao 为不覆盖的宿主提供 CLI 形态的默认值。大致结构（约 25 行；不是逐字粘贴用的
prompt 片段，而是默认值生成出来的形态）：

> 我是 agentao，运行在 `${WORKING_DIR}`。我代表 USER 行动——不解释怎么做，而是直接做。
>
> "你"指我（agentao）；"USER"指人，除非项目代码用了同名标识符。
>
> 操作类按位置分：
> - **项目对象**——`${WORKING_DIR}` 下的文件。
> - **Agent 对象**——`${AGENT_HOME}`（默认 `~/.agentao/`）下的 skills、memory、
>   工具配置、系统提示。
> - **外部对象**——通过 WebFetch / API / MCP 访问。
> - **用户意图对象**——我产出的不落盘文本。
>
> 反转条款：当 `${WORKING_DIR}` 本身就是某个 Agent 框架的源码时，项目内的 `skills/`
> 、`memory/`、`agent.py` 是项目对象。修改 Agent 自身仍走 `${AGENT_HOME}`。
>
> 触发追问，不要默认：
> - "你"在当前工作区有歧义；
> - 操作横跨 `${WORKING_DIR}` 与 `${AGENT_HOME}`；
> - 安装/配置目标宿主无法解析。

这份是**默认值**，不是硬约束。

## 差异化

调研的 13 家全部把边界焊死在自家专有 system prompt 里。对一个嵌入式 harness 来说
这条路结构上走不通——除非强迫所有宿主套用同一种 UX。把边界做成**注入协议**
（schema 固定、content 由宿主提供）在调研到的先验技术里没有同类。

Schema 第 5 条 harness-self-development opt-in 在调研到的提示里也无人涉及，尽管它
直接和好几家相关（Cognition 的"用 Devin 开发 Devin"、Claude Code 自己作为可编辑
npm 包、Aider 与 Continue.dev 作为开源可自我开发的 harness）。

## 本文档不是

- 不是实现计划。协议接口签名、`agent.py::_build_system_prompt` 集成点、把任何边界
  内容从 `AGENTAO.md` 里迁出、各宿主的默认值调优——都推迟。
- 不是成稿 prompt。默认内容集那段是示意，不是可直接出货的文案。
- 不是建议宿主必须覆盖每个槽位。多数宿主会用默认值；协议存在的意义是给 UX 必须
  偏离的少数宿主用。

## 参考资料

- **原始提案**（触发本设计评审的约 80 行元认知边界草案）。在对话记录中。
- **Cursor** 泄露提示：<https://github.com/jujumilk3/leaked-system-prompts/blob/main/cursor-ide-sonnet_20241224.md>
- **GitHub Copilot Chat** 泄露提示（2024）：<https://github.com/jujumilk3/leaked-system-prompts/blob/main/github-copilot-chat_20240930.md>
- **Aider** 提示源：<https://github.com/Aider-AI/aider/blob/main/aider/coders/base_prompts.py>、<https://github.com/Aider-AI/aider/blob/main/aider/coders/editblock_prompts.py>
- **Continue.dev** 模式提示：<https://github.com/continuedev/continue/blob/main/core/llm/defaultSystemMessages.ts>
- **Devin / Cognition** "用 Devin 开发 Devin"：<https://cognition.ai/blog/how-cognition-uses-devin-to-build-devin>
- **Windsurf Cascade** 泄露提示：<https://github.com/jujumilk3/leaked-system-prompts/blob/main/codeium-windsurf-cascade_20241206.md>
- **Claude Code** 泄露提示（第三方转录）：<https://github.com/asgeirtj/system_prompts_leaks/blob/main/Anthropic/claude-code.md>
- **openai/codex** 提示：`codex-rs/core/gpt_5_1_prompt.md` 见 <https://github.com/openai/codex>
- **sst/opencode** 提示路由：`packages/opencode/src/session/system.ts` 见 <https://github.com/sst/opencode>
- **charmbracelet/crush** 提示模板：`internal/agent/templates/coder.md.tpl` 见 <https://github.com/charmbracelet/crush>
- **block/goose** 提示：`system.md`、`subagent_system.md`、`tiny_model_system.md` 见 <https://github.com/block/goose>
- **princeton-nlp/SWE-agent** 提示：`config/default.yaml` 见 <https://github.com/princeton-nlp/SWE-agent>
- **All-Hands-AI/OpenHands** 提示：`openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2` 见 <https://github.com/All-Hands-AI/OpenHands>
