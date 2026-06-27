# 子 Agent 发现 —— 入口不对称 与 Skill/Plugin 混淆

**状态：** 评审记录。2026-06-23 起草，基于对 agent 定义、skill、plugin 三套发现路径
在三个运行入口（交互式 CLI / `agentao run`、Python 嵌入 `build_from_environment`、
ACP `session/new`）上的 grep 验证式通读。**这是缺口分析 + 优先级建议，不是已批准的
方案。** 它用来回答一个关于子 agent 注册的具体疑问 —— 并在回答过程中，纠正那个疑问
所依据的前提。
**读者：** Agentao 维护者；任何期望 skill 或 plugin 携带的子 agent 可见的嵌入方。
**配套：** `subagent-discovery-entrypoint-review.md`（英文）。
**相关：**
- `embedding-vs-acp.md` —— 为什么 ACP 与 `agentao run` 是嵌入核心之上的*前端*，而非
  核心契约本身。§5 里"plugin 加载该放核心还是前端？"正是这条边界的直接应用。
- `host-tool-injection.md` —— 宿主显式工具注入契约
  （`extra_tools` / `disable_tools` / `enabled_tools`）。§6 的 opt-in plugin 加载
  建议沿用同一姿态："宿主显式 opt-in，默认最小"。
- `acp-server-conformance-review.md` —— 确立 ACP 目标客户端 = chat/automation；与
  "ACP 会话*该不该*继承全局 plugin" 直接相关。

**方法：** 通读 `AgentManager._load_definitions`、`SkillManager._load_skills`、
plugin 发现/解析链（`embedding/plugins/`）及三条构造路径；grep
`_load_and_register_plugins` 的所有调用点；grep 任何把 skill 根目录桥接到 `agents/`
的代码；`find` `skills/` 下的 plugin manifest。代码引用锚定 `main`@`e477ad3`
（2026-06-23）。

---

## TL;DR

触发本评审的报告称：*"skill 携带的子 agent（如
`skills/skill-creator/agents/analyzer.md`）只在 CLI 下被发现注册，嵌入与 ACP 入口
跳过了它们。"*

**这个标题前提是错的。** skill 携带的 `agents/` 子目录被桥接到了**任何地方都没有**——
CLI 没有、嵌入没有、ACP 也没有。`agents/` 自动发现是 **plugin** 的特性；一个 bundled
*skill* 不是 *plugin* 根目录，而 plugin 发现扫描从不进入 `skills/`。所以
`skill-creator` 的三个 agent 文件对**每一个**入口都不可见，**包括交互式 CLI**。报告
把 *skill* 和 *plugin* 混为一谈了。

去掉混淆后**属实**的，是一组更小、也不同的事实 —— 两个独立问题 + 一个尚不存在的特性：

1. **plugin 加载是 CLI 专属**（真实）。`build_from_environment` 和 ACP `session/new`
   加载的 plugin 数量为 **零** —— 不只是 plugin-agent，连 plugin 的 *skill、MCP
   server、hook* 一并不加载。
2. **`AgentManager` 没有全局层**（真实，但小）。它只扫 builtin + 项目
   `.agentao/agents/`；不像 `SkillManager` 有三层，它没有 `~/.agentao/agents/`。
3. **"skill 携带子 agent" 这个特性尚未接线**（缺失特性，不是 bug）。没有任何代码把
   skill 的 `agents/` 映射进 `AgentManager`。

(1) 究竟是 *bug* 还是 *有意的隔离边界*，是维护者的判断 —— 而且有一条真实的信任论据
支持 "CLI-only 是刻意的"（§5）。建议（§6）是：低成本修掉 (2)；把 (1) 当作
**默认关闭的 opt-in** 以保住宿主隔离；若有需求，把 (3) 当独立特性提案另议。

> **反向复核纠正（2026-06-23，对抗性再核）。** 本文已对照自身声明重新验证。中心论点
> （§3 —— skill 的 `agents/` 被桥接到*任何地方都没有*，**包括 CLI**）**活下来且更
> 硬**：`_plugin_inline_dirs` *只*由 CLI `--plugin-dir` 旗标填充
> （`cli/entrypoints.py:371-373`），从不自动注入 `skills/*`，所以 CLI 不会把 skill
> 目录当 inline plugin 加载。三处*次级* framing 被夸大，已就地纠正：
> 1. **问题 B *并非*无边界**（纠正 §4.2 / §6-P1）。`build_from_environment` 已经把
>    连*builtin* agent 都门控在一个默认**关**的 settings 开关后
>    （`_builtin_agents_enabled`，`factory.py:48,238`；`enable_builtin_agents` 默认
>    `False`，`agent.py:102`）。常开扫描用户全局 `~/.agentao/agents/` 会与这个保守
>    姿态冲突 —— 然而 `SkillManager` 的全局层*却是*常开。所以 B 带着一个真实（虽更小）
>    的判断 ——*跟哪个先例*—— 而非"无边界争议"。
> 2. **ACP 已有 plugin 加载逃生口**（纠正 §5 / §6-P2）。`session/new` 和
>    `session/load` 都接受注入的 `agent_factory`（`acp/session_new.py:304`、
>    `acp/session_load.py:122`）。不对称只在*默认* factory 上；宿主已能自带一个加载
>    plugin 的 factory。这削弱了"ACP 是 gap"，也给 P2 一个天然接缝。
> 3. **P2 的 settings 门控默认关模式并不新**（细化 §6-P2）。它正是同一函数里
>    `enable_builtin_agents` 的既有做法 —— 当作先例引用，不是发明。抽取里不平凡的部分
>    有**两处**：把 `_plugin_inline_dirs` 解耦为 `inline_dirs` 参数，**以及**把
>    `PluginManager` 绑定到 factory 冻结的 `cwd`（两者详见 §6-P2 第 1 步）。

---

## 1. 触发报告（前提，原文）

> *skill 在其 `agents/` 子目录携带的子 agent 定义（如
> `skills/skill-creator/agents/analyzer.md`），只有在 CLI 入口下才被发现和注册。
> 另两个入口 —— Python 嵌入（`build_from_environment`）和 ACP（`session/new`）——
> 完全跳过了 plugin/skill-agent 加载，导致 skill 内定义的子 agent 对它们不可见。*

报告随后列了四条机制：`AgentManager` 只扫两个目录；`build_from_environment` 不调用
plugin 加载；ACP 委托给它；`_load_and_register_plugins` 仅 CLI 触发。

这四条*机制*大体准确（§2）。但它们被用来支撑的那个*前提* ——
"skill 子 agent 在 CLI 下可见" —— 不成立（§3）。

## 2. 经验证属实的部分

| 声明 | 结论 | 证据（`main`@`e477ad3`） |
|---|---|---|
| `_load_and_register_plugins` 仅 CLI | ✅ 真 | 定义于 `cli/subcommands.py:283`；非测试调用点只有 `cli/app.py:112` 与 `cli/run.py:543`。 |
| `build_from_environment` 不加载 plugin | ✅ 真 | `grep "plugin" agentao/embedding/factory.py` → 无匹配。构造 `Agentao(**kwargs)` 后直接返回。 |
| ACP `session/new` 不加载 plugin | ✅ 真 | `acp/session_new.py:158` —— `default_agent_factory` 直接返回 `build_from_environment(working_directory=cwd, **overrides)`，无 plugin 步骤。 |
| `AgentManager._load_definitions` 只扫两个目录 | ✅ 真 | `agents/manager.py:31-37` —— builtin `definitions/`（受 `include_builtin_agents` 控制）+ 项目 `.agentao/agents/`。无全局层。 |
| `SkillManager` 扫三层 | ✅ 真 | `skills/manager.py` —— 全局 `~/.agentao/skills`（`:14`）、项目 `.agentao/skills`（`:99`/`104`）、仓库 `skills/`（`:100`/`105`），外加 bundled `_BUNDLED_SKILLS_DIR`（`:17`）。 |

所以报告的*目录对比*表（AgentManager 2 层 vs SkillManager 3 层）正确，
*plugin 加载仅 CLI* 的观察也正确。

## 3. 错误前提：skill 的 `agents/` 没有被桥接到任何地方

报告的例子 —— `skills/skill-creator/agents/analyzer.md` —— 撑起了整个叙事。它过不了
grep。

**(a) `agents/` 发现属于 *plugin* 子系统，不属于 skill。**
唯一把某个根目录拼上 `agents/` 的代码，是 plugin 的 agent 解析器：

```
embedding/plugins/resolvers/agents.py:41   default_dir = plugin.root_path / "agents"
embedding/plugins/resolvers/agents.py:85   def _scan_agents_dir(plugin_name, agents_dir): ...
```

`SkillManager` **完全没有** `agents/` 处理 —— 对 `agentao/skills/manager.py` grep
`agents` 只命中 *skill* 目录常量，从无 `agents/` 子扫描。

**(b) plugin 发现从不进入 `skills/`。**
`PluginManager.discover_candidates`（`embedding/plugins/manager.py:91-110`）只扫
三处来源：

```
:96   全局   ~/.agentao/plugins
:100  项目   <cwd>/.agentao/plugins
:104  inline self._inline_dirs   （仅由 CLI 填充 —— entrypoints.py:373）
```

仓库的 `skills/` 树不在其列。

**(c) `skill-creator` 是 bundled *skill*，不是 *plugin*。**
`find skills/skill-creator` 显示 `SKILL.md` + `agents/{analyzer,comparator,
grader}.md`，且**没有任何 plugin manifest**（整棵树都不存在 `agentao-plugin.json`）。
它经由 `SkillManager` 的 bundled-skill 落盘（`skills/manager.py:17`、`:60`）到达
运行时，被复制进 `~/.agentao/skills/` —— 一个 *skills* 目录，绝非 *plugins* 目录。

**结论。** 要让 `skills/skill-creator/agents/analyzer.md` 被注册，`skill-creator`
必须作为 **plugin** 被加载（一个其 `agents/` 会被自动扫描的 plugin 根）。它不是，而且
没有任何入口把它变成 plugin。因此这些 agent 文件对**三个入口全部**不可见，**包括
CLI** —— 与"CLI 可见、别处缺失"恰好相反。

> 这正是 `CLAUDE.md` 在 *"别凭直觉审计架构"* 下警告的失效模式。`agents/` 这个目录名
> 是 skill 与 plugin 两个子系统共享的词汇，报告假设了一座代码里并不存在的桥。

## 4. 两个真实且独立的问题（外加一个缺失特性）

去掉混淆，剩下三件可分开处理的事：

### 4.1 问题 A —— plugin 加载仅 CLI（真实）

`_load_and_register_plugins`（`cli/subcommands.py:283-372`）不只是 agent 加载器。
它一趟把**整个 plugin 面**注册到 agent 上：

- plugin **skill** → `agent.skill_manager.register_plugin_skills`（`:301`）
- plugin **agent** → `agent.agent_manager.register_plugin_agents`（`:321`），
  随后 `agent._register_agent_tools()`（`:337`）
- plugin **MCP server** → 合并 + MCP manager 重建（`:347-359`）
- plugin **hook** → `agent._plugin_hook_rules` / `tool_runner`（`:361-372`）

因为它住在 `cli/` 且只被 `cli/app.py`、`cli/run.py` 调用，嵌入宿主（以及每个走
`build_from_environment` 的 ACP/IDE 会话）一样都拿不到。不对称是真实的，而且*比报告
所述更宽* —— 缺的是整个 plugin 子系统，不单是子 agent。

### 4.2 问题 B —— `AgentManager` 缺全局层（真实，但小）

`AgentManager._load_definitions` 只扫 builtin + 项目 `.agentao/agents/`
（`agents/manager.py:31-37`）。`SkillManager` 有全局 `~/.agentao/skills/` 层；
agent manager 没有对应的 `~/.agentao/agents/`。用户在 home 配置目录放一个个人 agent
定义 —— 类比用户全局 skill 的用法 —— 什么都不会发生。这与 plugin 问题无关，也是这里
最便宜*实现*的（一句 `_scan_directory(user_root() / "agents")`，按正确
优先级排序）。

> **实现便宜，但*并非*无边界**（反向复核纠正）。代码库对"自动加载环境态 agent"本身
> 就已不一致：`SkillManager` 全局层常开，而 `build_from_environment` 把连*builtin*
> agent 都默认**关**、门控在 settings 开关后（`_builtin_agents_enabled`，
> `factory.py:48,238`；`enable_builtin_agents=False`，`agent.py:102`）。所以
> "用户全局 agent 定义该不该到处自动注册？"是个真实判断 ——*跟常开的 skill 先例，还是
> 跟默认关的 builtin-agent 先例？* 一个 agent 定义比 plugin 风险低（只带 system
> prompt + 工具白名单，仅在 LLM 调用时运行 —— 更接近被动 skill，而非有副作用的 MCP
> server/hook），这倾向 skill 先例；但它不是初稿暗示的那种"无需决策"的改动。

### 4.3 缺失特性 —— "skill 携带子 agent"

报告*假设*存在的能力 —— skill 在自己的 `agents/` 子目录里捆绑子 agent、并在 skill
激活时被注册 —— **尚未实现**。把它做出来是*新特性*（一座 skill→AgentManager 的桥），
不是 bug 修复，且它自带设计问题（skill 的子 agent 在 skill *激活*时注册还是*发现*时
注册？是否按 skill 命名空间隔离？随 skill 停用而停用吗？）。应按需求评估，不要塞进
"入口对等"修复里。

> **一个更锐利的发现**（反向复核注，纠正初稿"惰性载荷"的说法）：报告者的例子从两个
> 层面推翻了那个*机制*。`skill-creator` 的 `agents/{analyzer,comparator,grader}.md`
> **不是 agent 定义** —— 它们没有 YAML frontmatter（`name:`/`description:`），只有一个
> prose H1，即便被扫描，`parse_frontmatter` 得到的 name 也是空的。而且这个 skill **并不
> 想让它们被注册**：其 SKILL.md 把它们当作*运行时读取的参考文档*用（"spawn a grader
> subagent that **reads `agents/grader.md`**"、"**Read `agents/comparator.md`**"、
> "the agents/ directory contains instructions … **Read them when you need to
> spawn** the relevant subagent" —— `SKILL.md:225,327,455`），然后用这些内容作为指令
> 去 spawn 一个*通用* subagent。所以这些文件既非惰性、也不可注册 —— 它们已经在工作，
> 作为指令载荷，恰如上游 skill 格式的本意。这让 C 成为一个**需求门控的假设**：唯一一个
> *看起来*需要 skill→agent 注册桥的在树产物，其实并不需要。何时会改变见 §6-P3。

> **上游打包方式佐证了这一点**（对照 Claude Code 自己的 plugin 安装核实，2026-06-23）。
> Claude Code 把 skill-creator 作为 *plugin* 分发
> （`~/.claude/plugins/installed_plugins.json` → `skill-creator@claude-plugins-official`，
> 带 `.claude-plugin/plugin.json` manifest）—— 可它的
> `agents/{analyzer,comparator,grader}` 在那边**同样不被注册为子 agent**。两个原因，都是
> 结构性的：plugin manifest 没声明 `agents` 字段；而这些文件位于
> `skills/skill-creator/agents/`——*在 skill 内部*，不在 plugin 的 agent 根
> `<plugin>/agents/`（plugin agent 发现扫描的正是后者，与 agentao 同一条
> `plugin.root_path / "agents"` 规则，§3）。实证确认：这些名字不在本会话的可用 agent
> 类型里。所以上游是**刻意**把它们打包成 skill 资产、而非 plugin-agent —— 在 Claude
> Code（skill-creator 是 plugin）和 agentao（它是 bundled skill）*两边*，它们都是 skill
> 内部、运行时读取的参考文档，从不是注册子 agent。报告者的前提——"skill 捆绑的 agent 会
> 被注册"——连它最可能的来源（Claude Code）都不成立。

## 5. 问题 A 是 bug，还是有意的隔离边界？

这是承重判断，且是维护者的决定 —— 并非不言自明的缺陷。

**支持 "CLI-only 是刻意的" 的论据。** plugin 主要来源是用户的**全局**
`~/.agentao/plugins`。让 `build_from_environment` 自动加载它们，意味着**每一个嵌入
宿主、每一个 ACP/IDE 会话，都会静默继承终端用户全局的 agent、MCP server 和 hook。**
对一个*嵌入式 harness* 而言这是信任/隔离上的脚枪：一个嵌入 `Agentao(...)` 去做一件
有界工作的宿主应用，通常*不希望*自己的 agent 面、工具集、外联 MCP 连接，被这台机器的
用户碰巧全局装了什么而悄悄扩张。尤其是 hook —— 一个全局 plugin hook 在嵌入宿主的工具
管线里触发，是宿主从未 opt-in 的注入面。CLI 是唯一一个"加载用户的 plugin"毫无歧义
即用户意图的场景，因为用户*就是*操作者。所以把 plugin 加载走 CLI 层、让核心构造路径
保持无 plugin，是一种站得住脚的 **默认拒绝** 姿态，与 agentao 对待其他环境输入的方式
一致。

**支持 "这是缺口" 的论据。** 这种不对称**没有文档**。嵌入方合理地期望三个入口对等，
而今天没有任何*一等的* host-API 表面能说"是的，加载我的 plugin"。真正的 smell 不是
这个默认 —— 而是 *缺少有文档的 opt-in*，让该行为看起来像意外而非选择。

**一个接缝已经存在，这把天平推向"刻意，只是没写文档"。** ACP `session/new` 与
`session/load` 已接受注入的 `agent_factory`（`acp/session_new.py:304`、
`acp/session_load.py:122`），而嵌入宿主自己构造 `Agentao` —— 所以*两条*非 CLI 路径
今天就能通过提供一个调用加载器的 factory / 构造后步骤来加载 plugin。能力当下可达；缺的
是一个受祝福、有名字、有文档的开关，而非临时变通。在构造接缝处恰好存在一个刻意的注入
点，本身就是"无 plugin 默认是选择而非疏漏"的弱证据。

按项目既定规矩（*"痛"的判断是用户的事*），本文不替你把问题 A 定为"真实痛点"。它摆出
取舍，并推荐一个同时满足两种解读的姿态。

## 6. 建议（按优先级 —— 维护者定夺）

**P1 —— 修问题 B（实现便宜；但要先定一个小的边界判断）。**
给 `AgentManager._load_definitions` 加全局层：
`_scan_directory(user_root() / "agents")` —— 注意 `user_root()` **本身就是**
`~/.agentao`，所以路径是 `~/.agentao/agents`，**不是** `user_root()/".agentao"
/"agents"`（那会扫到 `~/.agentao/.agentao/agents`）。放在能给出正确优先级的位置
（对照 `SkillManager` 的 全局<项目 顺序，并决定是否像 skill 那样项目覆盖全局），并
**加一个覆盖测试**验证覆盖顺序。自包含；可独立发版。**合并前先定那个判断**（见 §4.2
的反向复核纠正）：用户全局 agent
自动加载该跟*常开*的 skill 先例，还是*默认关、settings 门控*的 builtin-agent 先例？
建议：跟 skill（常开）—— 一个 agent *定义*是被动的（system prompt + 工具白名单，仅在
LLM 调用时运行），不像 plugin 那样带有副作用的 MCP/hook —— 但要在 PR 里把它写成显式
决策，而非隐式。

**P2 —— 让 plugin 加载在核心可达，默认关闭（在不破坏隔离的前提下解决问题 A）。**
1. 从 `cli/subcommands.py` 抽一个**窄 helper** 到 `embedding/` —— 例如
   `load_plugins_for_agent(agent, *, cwd, inline_dirs=None)`，包住现在
   `_load_and_register_plugins` 的函数体。搬家大体机械（函数体几乎只 import
   `embedding/plugins/*`），但需要**两处**显式参数化，不是一处：
   - **`inline_dirs`** —— 现在读 CLI 全局 `_plugin_inline_dirs`
     （`cli/subcommands.py:291`）；传进去（默认 `None`，因为 inline 目录只来自 CLI
     `--plugin-dir` 旗标）。
   - **`cwd`** —— `PluginManager` 的项目扫描根默认取 `Path.cwd()`
     （`PluginManager.__init__` → `self._cwd = _find_project_root(cwd or
     Path.cwd())`，`embedding/plugins/manager.py:79`）。CLI 能蒙混过关是因为那里
     cwd == 工作目录，但宿主调 `build_from_environment(working_directory=wd)` 是
     刻意把运行时**冻结**到 `wd`。所以 helper 必须把 `cwd=agent.working_directory`
     传进 `PluginManager(cwd=..., inline_dirs=...)`；否则 project plugin 会按进程
     cwd 扫描，破坏 factory 存在的意义 —— 冻结工作目录契约。

   （`logger` 只是普通模块 logger，干净迁移 —— 不算真耦合。）
2. CLI 默认继续调用该 helper（行为不变）。让 `build_from_environment` 接受显式
   `load_plugins: bool = False`（或读取 `.agentao/settings.json` 的 flag），默认
   **关**；置位时在构造后调用 `load_plugins_for_agent(agent, cwd=wd, ...)`。
   **这个默认关 + settings 门控模式在同一函数里已经存在**：`enable_builtin_agents`
   正是这样解析的（`_builtin_agents_enabled(settings)` → 默认 `False` 的 override，
   `factory.py:48,238`）。沿用这个先例，让开关地道、不是新机制。
3. ACP `session/new` / `session/load` 通过 `default_agent_factory` 免费继承该开关
   （即 §5 提到的注入 `agent_factory` 接缝）；用合适的 ACP/host 配置面暴露它（受
   `acp-server-conformance-review.md` 里 chat/automation 目标决策的约束）。
4. 在 `host-api.md` / `host-tool-injection.md` 记录该开关及默认关闭的理由，让这个姿态
   读起来是*选择*，而非*意外*。

这样既保住安全默认（不静默继承全局 plugin），又给嵌入方和 ACP 宿主一条显式、有文档的
opt-in 路径 —— 与现有宿主工具注入契约同形。

**P3 —— "skill 携带子 agent"（仅在出现明确需求信号时才做）。**
把 §4.3 当作独立特性规格；不要并入 P2。**何时做 —— 三个触发条件，今天一个都没触发：**
1. **某个 skill 真的需要注册。** 一个 skill 以*agent 定义格式*（YAML
   `name:`/`description:` frontmatter）携带 `agents/*.md`，且作者期望它们在 skill
   激活时作为可调用的子 agent 工具出现 —— 却发现没有。唯一的在树例子 `skill-creator`
   明确**不满足**：它的 `agents/` 是运行时读取的指令文档，不是定义（§4.3）。所以当下
   在树需求为**零**。
2. **skill 成为分发单元**（像 plugin 那样发布/共享）。在那之前，任何想要*可分发*的
   捆绑 agent 的人都已有路径 —— 发一个 **plugin**，其 `agents/` 会被自动扫描（§3）。
   只有当 skill-作为-分发 与 plugin-作为-分发 分道扬镳时，P3 的生态位才打开。
3. **宿主/用户直接提出需求。** 按需求门控规则（gap ≠ need），那个请求才是信号 ——
   而非仅仅"存在未桥接的 `agents/` 目录"这一事实。

**现在在纸面上定设计，以后再写代码。** 在动任何代码前先敲定 §4.3 的三个问题
（激活-vs-发现 时机、skill 命名空间、生命周期耦合），让特性在触发条件一出现就能落地。
自然的形态是一座在 skill *激活*时触发的 `SkillManager`→`AgentManager` 桥（让子 agent
与 skill 共享生命周期）—— **而非**教 `PluginManager` 去扫 `skills/`，那会合并两套不同的
信任/生命周期模型（§7）。

## 7. 非目标 / 不要做什么

- **不要**为"修对等"而让 `build_from_environment` 默认自动加载全局 plugin。那会从机器
  全局配置静默扩张每一个嵌入宿主的 agent/MCP/hook 面 —— 即 §5 的隔离脚枪。
- **不要**教 `PluginManager` 把 `skills/` 当 plugin 来源扫描。skill 与 plugin 是两个
  独立子系统，信任与生命周期模型各异；为让一个例子跑通而合并它们，是错的层。
- **不要**把 P1（问题 B）和 P2（问题 A）描述成一次改动。它们独立；P1 只自带一个*小的*
  默认加载决策（§4.2 —— 全局 agent 层跟哪个先例），不应被、也不应受制于管 P2 的更大的
  plugin 隔离决策绑架。
