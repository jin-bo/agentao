# CLI · 终端命令手册

Agentao 自带一个终端优先的交互界面 — 在你的 shell 里 `agentao` 启动，slash 命令、plan 模式、子 agent、记忆、回放全部内置。本章就是这个界面的完整文档。

## 60 秒装好

```bash
# 克隆或安装
uv sync                  # 安装项目依赖
cp .env.example .env     # 把 OPENAI_API_KEY 填进去

# 启动 agent
uv run agentao
# 或：./run.sh
```

你会进到一个聊天 REPL。直接打字就是和 agent 对话；以 `/` 开头则是控制会话本身的命令。

## 章节总览

- [**1. 起步**](./1-getting-started) — `/help` `/clear` `/new` `/status` `/exit` · 最小循环
- [**2. 模型与 Provider**](./2-models-providers) — `/model` `/provider` `/temperature` · 运行时切换 LLM 和凭证
- [**3. 权限与模式**](./3-permissions-modes) — `/mode`、工具确认 UI、`/sandbox`（macOS） · agent 干危险事前怎么问你
- [**4. Plan 模式**](./4-plan-mode) — `/plan` 工作流 · 只读的"先想清楚再动手"循环
- [**5. Skills 与 Crystallize**](./5-skills-crystallize) — `/skills` `/crystallize` · 激活技能 / 从会话中析出新技能
- [**6. 记忆**](./6-memory) — `/memory` · 什么被记住、存在哪里、怎么查、怎么清
- [**7. 上下文与状态**](./7-context-status) — `/context` `/compact` `/status` · token 预算、压缩、会话规模
- [**8. MCP / ACP / 插件**](./8-mcp-acp-plugins) — `/mcp` `/acp` `/plugins` · 接入外部工具服务器
- [**9. 回放与输出**](./9-replay-output) — `/replay` `/copy` `/markdown` · 录会话、复制答案、控制渲染
- [**10. 配置文件参考**](./10-config-reference) — CLI 读取的所有配置文件、路径与优先级
- [**11. 会话、子 Agent 与任务**](./11-sessions-agents) — `/sessions` `/agent` `/agents` `/todos` `/tools` · 恢复与并行工作台
- [**12. 非交互入口**](./12-non-interactive) — `agentao init` `-p` `--resume` `--acp` `agentao doctor` `agentao config validate` · 脚本、CI 校验、宿主集成入口

## 怎么读

| 你的情况 | 推荐路径 |
|---|---|
| 第一次用 `agentao` | [1. 起步](./1-getting-started) → [3. 权限与模式](./3-permissions-modes) |
| 从别的 agent CLI 过来（Claude Code / codex / gemini 等） | [4. Plan 模式](./4-plan-mode) → [5. Skills 与 Crystallize](./5-skills-crystallize) |
| 想接入公司自有工具 | [8. MCP / ACP / 插件](./8-mcp-acp-plugins) → [Part 5.3 MCP](/zh/part-5/3-mcp) |
| Agent 吃掉了预算 / context 爆了 | [7. 上下文与状态](./7-context-status) → [6. 记忆](./6-memory) |
| 想找回旧会话 / 看后台子 agent | [11. 会话、子 Agent 与任务](./11-sessions-agents) |
| 我要把 CLI 推给团队用 | [3. 权限与模式](./3-permissions-modes) → [10. 配置文件参考](./10-config-reference) |
| 想在脚本、CI、IDE 里调用 | [12. 非交互入口](./12-non-interactive) → [第三部分 · ACP 协议嵌入](/zh/part-3/) |
| 我要把这个引擎嵌进自己的应用 | [第一部分 · 起步](/zh/part-1/)（不同读者群 — 从那边开始） |

## 心智模型

> CLI 是套在 Agentao harness 之上的一层薄薄的 REPL。
> Slash 命令操作的是**会话**（历史、模型、模式、计划、记忆）。
> 普通消息发给的是 **agent**（工具、技能、MCP、ACP）。
> 你在终端看到的一切 — 确认提示、流式事件、工具结果、记忆召回 — 都是嵌入式宿主通过事件流会收到的同一份内容。

理解了 CLI，你就理解了嵌入开发者面对的大部分东西。

→ [从第 1 章 · 起步开始 →](./1-getting-started)

---

::: info 这一章在体系里的位置
CLI 只是 Agentao harness 的**一种消费者**。同一个 harness 可以嵌入到你自己的应用里 — 看 [第二部分 · Python 进程内嵌入](/zh/part-2/) 或 [第三部分 · ACP 协议嵌入](/zh/part-3/)。这里学到的权限、技能、MCP、记忆、回放，等你嵌入时一样适用。
:::

::: tip 真相源头
命令的语法权威来源是 CLI 里直接执行 `/help`，背后是 [`agentao/cli/help_text.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/help_text.py)。这里的文档讲 *为什么这样设计* 和 *怎么用*；如果任何文字与 `/help` 不一致，以 `/help` 为准。
:::
