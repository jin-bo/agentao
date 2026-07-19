# 11. 会话、子 Agent 与任务

本页覆盖 REPL 里偏“工作台”的命令：恢复旧会话、查看后台子 agent、看任务列表、检查工具注册表。

## `/sessions` — 已保存会话

`/exit`、`/clear`、`/new` 都会把当前会话保存到 `.agentao/sessions/`。恢复入口是 `/sessions`，不是 replay。

```text
> /sessions
> /sessions resume a1b2c3
> /sessions delete a1b2c3
> /sessions delete all
```

| 命令 | 作用 |
|---|---|
| `/sessions` 或 `/sessions list` | 列出保存的会话，最新在前 |
| `/sessions resume <id>` | 按 session id 前缀恢复；模型和激活 skills 会一起恢复 |
| `/sessions delete <id>` | 删除一个保存的会话 |
| `/sessions delete all` | 删除全部保存会话，单键确认 |

启动时也可以恢复：

```bash
agentao --resume          # 恢复最近会话
agentao --resume a1b2c3   # 恢复指定前缀
```

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
| `/agent bg <name> <task>` | 后台运行；状态显示在底部 toolbar |
| `/agent status` | 列出后台任务状态 |
| `/agent status <id>` | 查看一个后台任务的结果或失败原因。跑起来但没跑完的任务（预算耗尽、无输出）会显示 *Did not finish*，**并且照样打印它的部分结果** |
| `/agent dashboard` 或 `/agents` | 打开实时刷新面板 |
| `/agent cancel <id>` | 取消后台任务 |
| `/agent delete <id>` | 从后台任务列表删除记录 |

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
命令语法：`/help`。会话恢复：[`agentao/cli/commands.py:handle_sessions_command`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands.py)。子 agent：[`agentao/cli/commands_ext/agents.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands_ext/agents.py)。工具列表：[`agentao/cli/commands.py:handle_tools_command`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands.py)。
:::
