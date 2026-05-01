# 1.2 核心概念

> **本节你会学到**
> - 后续章节都依赖的 6 个核心名词
> - 一张图看清它们如何协作
> - 每个名词在代码里的锚点，方便你深挖

在开始写任何集成代码之前，先熟悉 Agentao 的 6 个核心名词。后面所有章节都会反复用到它们。

## 概念地图

```
┌─────────────────────────────────────────────────────────────┐
│                        你的应用（Host）                       │
│                                                             │
│   ┌──────────┐ confirm/ask/event                            │
│   │ Transport│◄────────────────┐                            │
│   └────┬─────┘                 │                            │
│        │ drive                 │                            │
│        ▼                       │                            │
│   ┌──────────────────────────────────────────────────┐      │
│   │                   Agent (Agentao)                │      │
│   │                                                  │      │
│   │   Session ──► Working Directory                  │      │
│   │      │                                           │      │
│   │      ▼                                           │      │
│   │   Tools ◄── Skills ◄── System Prompt            │      │
│   │      │                                           │      │
│   │      ▼                                           │      │
│   │   LLM Client (OpenAI/Anthropic/Gemini/…)          │      │
│   └──────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────┘
```

## 1. Agent（Agentao 实例）

Python 类 `agentao.Agentao`，一个**有状态、单会话**的对象。它维护：

- 对话历史 `self.messages`
- 工具注册表 `self.tools`
- 技能管理器 `self.skill_manager`
- 记忆管理器 `self.memory_manager`
- 一个 `working_directory`（文件操作的根）

一个 Agent 实例 = 一个会话。多用户/多会话场景下，**每个会话应构造独立实例**（参见第 7 部分）。

## 2. Tool（工具）

Agent 能调用的"动作"。每个工具有：

- `name` 唯一标识
- `description` 给 LLM 看的说明
- `parameters` JSON Schema 参数定义
- `execute(**kwargs) -> str` 真实执行逻辑
- `requires_confirmation` 是否需要用户批准

Agentao 自带几十个工具（`read_file`, `write_file`, `run_shell_command`, `web_fetch`, `grep`, `glob` ...）。**你的业务 API 需要暴露给 Agent 时，就以工具形式包装**（见第 5.1 节）。

## 3. Skill（技能）

一段**按需加载**的领域知识或流程说明。每个技能是一个目录：

```
skills/my-skill/
├── SKILL.md            # 主文件，YAML frontmatter + 说明
└── reference/*.md       # 可选：激活后按需加载
```

技能不是代码，是**指导 LLM 行为的 markdown 文档**。对比：

|  | Tool | Skill |
|---|------|-------|
| 形态 | Python 类 | Markdown 文件 |
| 何时生效 | 注册即可用 | 需激活（`activate_skill` 工具或 `/skills` 命令） |
| 典型用途 | "打开 API 做事" | "按我们的规范做事" |

## 4. Transport（传输层）

Agent 与宿主之间的**双向通道**。一个 Transport 实现 4 个方法：

- `emit(event)` — Agent 向宿主推事件（流式文本、工具开始/结束、思考过程…）
- `confirm_tool(name, desc, args) -> bool` — Agent 问宿主"这个工具可以跑吗？"
- `ask_user(question) -> str` — Agent 向用户反问
- `on_max_iterations(count, messages) -> dict` — 达到最大轮次时的兜底策略

Agentao 内置 3 个 Transport：
- `NullTransport` — 静默，自动批准一切（测试用）
- `SdkTransport` — 可配置回调，**库/服务嵌入的首选**
- CLI 的 Rich Transport — 终端用户交互

## 5. Session / Working Directory

**Session** = 一次从 `Agentao()` 构造到 `close()` 之间的完整生命周期，对应一段对话历史。

**Working Directory** = 这个会话"看到的项目根"。文件工具、Shell、技能、`AGENTAO.md` 加载都相对它。

⚠️ **多实例嵌入时必须显式传入 `working_directory=Path(...)`**。默认会用 `Path.cwd()`，在服务器进程里这是全局共享的，会造成会话串扰。

## 6. System Prompt（动态系统提示）

不是静态字符串，而是**每次 `chat()` 重新拼装**的：

1. `AGENTAO.md`（项目说明，从 working_directory 读）
2. Agent 基础能力描述
3. 当前日期、可用技能清单
4. 激活的技能全文
5. 记忆召回块 `<memory-context>`
6. 任务列表（todos）

宿主可以通过写 `AGENTAO.md` 或自定义技能在这里注入业务知识（第 5.6 节）。

## 快速对照表

| 概念 | 对应源码位置 | 第三方开发者的主要接触点 |
|------|------------|----------------------|
| Agent | `agentao/agent.py` | `Agentao(...)` 构造器 |
| Tool | `agentao/tools/base.py` | 继承 `Tool` 写业务工具 |
| Skill | `agentao/skills/manager.py` | 写 `SKILL.md` 文件 |
| Transport | `agentao/transport/` | 实例化 `SdkTransport` |
| Session | `agent.messages` | 管理生命周期、并发 |
| System Prompt | `agent._build_system_prompt()` | 写 `AGENTAO.md` |

## TL;DR

- **Agent = 一个有状态的会话**。每个用户/对话一个实例，新会话重新构造。
- **Tool = 能力单元**。Python 类，含 `name` / `description` / `parameters` / `execute()`。把业务 API 包成工具。
- **Skill = 按需的提示知识**。Markdown 包，需要显式激活；它不是代码。
- **Transport = UI 桥**。4 个回调：emit / confirm / ask_user / on_max_iterations。
- **Working directory = 进程内会话根目录**。**永远显式传 `Path`**。
- **System prompt = 每轮重新组装**：AGENTAO.md + 日期 + 技能 + 记忆。

下一节：[1.3 两种集成模式 →](./3-integration-modes)
