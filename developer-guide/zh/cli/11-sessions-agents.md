# 11. 会话、子 Agent 与任务

本页覆盖 REPL 里偏“工作台”的命令：恢复旧会话、查看后台子 agent、看任务列表、检查工具注册表。

## `/sessions` — 已保存会话

当前会话在每一轮结束后都会保存到 `.agentao/sessions/`，用 Ctrl-C 中断的轮次和失败的轮次也包括在内。所以关掉终端、进程被杀或崩溃时，之前已完成的轮次都还在。每次保存都会替换同一会话的上一个文件，一个会话仍只占一个文件。`/exit`、`/clear`、`/new` 会再保存一次并结束会话；只有这三者会触发 `SessionEnd` hook，每轮保存不会。恢复入口是 `/sessions`，不是 replay。

```text
> /sessions
> /sessions resume a1b2c3
> /sessions rename Payments refactor
> /sessions clone
> /sessions delete a1b2c3
> /sessions delete all
```

| 命令 | 作用 |
|---|---|
| `/sessions` 或 `/sessions list` | 列出保存的会话，最新在前 |
| `/sessions resume <id>` | 按 session id 前缀恢复；模型和激活 skills 会一起恢复 |
| `/sessions rename <name>` | 给当前会话起名。列表里用这个名字代替从第一条消息生成的标题，之后的保存、退出和恢复都会保留它。改名会立即保存，即使没有新消息；会话还没有消息时，名字随第一条消息一起保存。空白和换行合并成一个空格，超过 60 个字符会被截断 |
| `/sessions clone [id]` | 用新 id 保存当前会话的副本；也可以复制已保存的会话（id 前缀必须只匹配一个会话）。你留在当前会话，用 `/sessions resume <新 id>` 进入副本。之后两份各自继续。有名字的会话，副本叫 `<名字> (copy)` |
| `/sessions delete <id>` | 删除一个保存的会话 |
| `/sessions delete all` | 删除全部保存会话，单键确认 |

启动时也可以恢复：

```bash
agentao --resume          # 恢复最近会话
agentao --resume a1b2c3   # 恢复指定前缀
```

名字只属于一个会话：`/new`、`/clear` 和恢复另一个会话后都不带这个名字。名字功能出现之前保存的会话文件没有 `name` 字段，仍显示生成的标题。恢复仍按 session id 前缀匹配，不按名字。

副本保留消息、压缩摘要、模型、激活的 skills 和 MCP skill 来源。它和原会话共用项目的工作目录和记忆，所以只是把对话历史分开。轮转最多保留 10 个会话文件。已经有 10 个时，`/sessions clone` 会拒绝，而不是把最旧的挤掉；请先删掉一个。

## `/agent` 与 `/agents` — 子 Agent

子 agent 是一组预定义能力，可以前台跑，也可以后台跑。内置定义在 `agentao/agents/definitions/`，插件也可以注册更多 agent。

```text
> /agent
> /agent codebase-investigator 找出认证模块的数据流
> /agent bg generalist 总结 docs/ 目录结构
> /agents
```

| 命令 | 作用 |
|---|---|
| `/agent` 或 `/agent list` | 列出可用子 agent |
| `/agent <name> <task>` | 前台运行；当前 REPL 等结果 |
| `/agent bg <name> <task>` | 后台运行；状态显示在底部 toolbar。toolbar 只显示本次对话的任务和仍在运行的任务（`/new`、`/clear` 之前已结束的、以及之前几次启动 CLI 留下的记录都不显示）。同时最多运行 6 个；超出时拒绝启动并打印提示，不会排队 |
| `/agent status` | 列出本次对话的后台任务和仍在运行的任务 |
| `/agent status --all` | 连同之前的对话和之前几次启动 CLI 的任务一起列出（已结束的最多保留最新 50 条） |
| `/agent status <id>` | 查看一个后台任务的结果或失败原因。跑起来但没跑完的任务（预算耗尽、无输出）会显示 *Did not finish*，**并且照样打印它的部分结果** |
| `/agent dashboard` 或 `/agents` | 打开实时刷新面板（范围同 `/agent status`；加 `--all` 看之前的） |
| `/agent cancel <id>` | 取消后台任务 |
| `/agent delete <id>` | 从后台任务列表删除记录 |

后台任务结束时，如果 REPL 正停在空提示符上，CLI 会打印 `⟳ background agent finished — continuing` 并自动跑一轮，让主 agent 读到结果——无论任务是模型启动的，还是你用 `/agent bg` 启动的。你正在输入、有暂存的图片、或处于 plan 模式时，它会等着；连续唤醒 3 次后，或你在唤醒轮次中按下 Ctrl+C 后，会暂停，直到你输入内容。在 `.agentao/settings.json` 里写 `"background_agents": {"auto_wake": false}` 可以关闭；关闭后，结果会随你的下一条消息交给 agent。

## `/todos` — 当前任务列表

Agent 处理复杂任务时会用 `todo_write` 工具维护任务列表。`/todos` 只是把这份列表打印出来。

```text
> /todos

Task List (2/4 completed):
  ✓ Read CLI docs
  ◉ Patch mismatched paths
  ○ Update /help
```

如果没有任务，说明当前会话还没有触发多步任务规划。

## `/tools` — 工具注册表

`/tools` 列出当前 agent 可调用的工具；`/tools <name>` 打印参数 schema。

```text
> /tools
> /tools run_shell_command
```

这对排查“为什么 agent 没调用某个工具”很有用：先确认工具是否注册，再看权限模式是否拦截。

## 接下来读什么

| 想做的事 | 读 |
|---|---|
| 恢复后复盘事件流 | [9. 回放与输出](./9-replay-output) |
| 用 ACP 接别的 agent 进来 | [8. MCP / ACP / 插件](./8-mcp-acp-plugins) |
| 理解子 agent 在嵌入式宿主里的事件 | [Part 4.2 · AgentEvent](/zh/part-4/2-agent-events) |

---

::: tip 真相源头
命令语法：`/help`。会话恢复：[`agentao/cli/commands/sessions.py:handle_sessions_command`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands/sessions.py)。子 agent：[`agentao/cli/commands_ext/agents.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands_ext/agents.py)。工具列表：[`agentao/cli/commands/tools_intro.py:handle_tools_command`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands/tools_intro.py)。
:::
