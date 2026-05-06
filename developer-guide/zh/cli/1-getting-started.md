# 1. 起步

最小循环就 4 个命令：启动、对话、结束当前会话开新的、退出。这四步过了，其他 slash 命令都是可选的。

## 启动

```bash
uv run agentao
# 或
./run.sh
```

进 REPL。当前 shell 的工作目录会成为 agent 的 `working_directory` — agent 跑的所有 file / glob / shell 工具都以这里为根。**启动前先 `cd` 到你要让 agent 操作的项目目录。**

```text
🌟 Agentao — terminal agent
Working dir: /Users/you/projects/my-app
Model: gpt-5.4 · Mode: workspace-write

输入消息，或 /help 看命令列表。
>
```

## 普通消息 vs. slash 命令

| 你输入 | 发生了什么 |
|---|---|
| `找出最大的 3 个文件` | 发给 agent。触发 LLM 循环（思考 → 工具 → 观察 → 回答）。 |
| `/help` | **不**发给 agent。CLI 本地处理。 |

规则就这一条。以 `/` 开头的是 CLI 命令（会话级别）；其他都是 agent 的一轮对话（agent 级别）。

## `/help` — 列出所有命令

```text
> /help
```

打印完整的 slash 命令参考 + agent 拥有的工具清单。第一次运行时通读一遍即可，不必记 — `/help` 永远只差一个键盘距离。

## `/status` — 我现在是什么状态

```text
> /status
```

按这个顺序展示：

1. **会话摘要** — 模型名、消息数、token 估算、激活的 skills
2. **权限模式** — `read-only` / `workspace-write` / `full-access` / `plan` 之一，附一行说明（详见 [3. 权限与模式](./3-permissions-modes)）
3. **规则来源** — 当前权限规则从哪些文件加载（defaults / project / user）
4. **Markdown 渲染** — `ON` / `OFF`（用 `/markdown` 切换，见 [9. 回放与输出](./9-replay-output)）
5. **任务列表** — 如果本次会话 agent 用过 `todo_write` 工具，会显示 `X/Y completed`
6. **ACP 服务器** — `X/Y 运行中`，加 inbox / pending interaction 计数（见 [8. MCP / ACP / 插件](./8-mcp-acp-plugins)）

`/status` 不改任何东西 — 纯只读诊断。任何时候搞不清现在用的什么模型 / 模式 / skills，敲一下就行。

## `/clear` 与 `/new` — 开一个新会话

这两个命令都会先保存当前会话，然后启动一个新的空对话，但保留范围不同：

| 命令 | 清掉什么 | 保留什么 |
|---|---|---|
| `/new` | 当前对话历史、上下文计数 | 长期记忆、模型、已发现的 skills |
| `/clear` | 当前对话历史、全部记忆（user + project）、会话摘要 | 模型、已发现的 skills |

两者都会把权限模式重置成 `workspace-write`。`/clear all` 只是 `/clear` 的兼容别名。

什么时候用：
- 当前任务做完，不想让它的 context 污染下一个任务
- token 用量在攀升（见 [7. 上下文与状态](./7-context-status) 里的 `/context`）
- 改主意了想从空状态开始；如果还要保留长期记忆，用 `/new`

要恢复或删除已保存会话，用 `/sessions` — 见 [11. 会话、子 Agent 与任务](./11-sessions-agents)。

## `/exit` 与 `/quit` — 干净地离开

```text
> /exit
```

两个等价：把进行中的工作刷盘、关掉 MCP / ACP 子进程、记忆持久化，然后回到 shell。**不要用 `Ctrl+C` 退出** — 会跳过清理、留下子进程残留。`Ctrl+C` 留给"取消当前这一轮"用（agent 停，REPL 继续开着）。

## 第一天容易踩的坑

| 现象 | 原因 |
|---|---|
| Slash 命令打到一半被当成消息发出去了 | Slash 命令只在**第一个非空白字符**就是 `/` 时才生效 |
| Agent 看不见你确认存在的文件 | 启动目录不对 — `working_directory` 在启动那一刻就锁定了 |
| 不小心 `/clear` 了，想找回 | 会话是持久化的；用 `/sessions list` 和 `/sessions resume <id>` |
| `/help` 列出来的命令用不了 | 跑的是老版本 — `uv sync` 后重启 |

## 接下来读什么

| 想做的事 | 读 |
|---|---|
| 切换模型 / provider | [2. 模型与 Provider](./2-models-providers) |
| 看懂确认提示 | [3. 权限与模式](./3-permissions-modes) |
| 先想好再动手 | [4. Plan 模式](./4-plan-mode) |

---

::: info 这一章在体系里的位置
本页这些命令（`/help` `/status` `/clear` `/new` `/exit`）都是纯 CLI 会话控制。嵌入式宿主用 `Agentao(...)` / `agent.close()` 管理生命周期，用 `active_permissions()` / 事件流读状态。见 [Part 2 · 生命周期](/zh/part-2/3-lifecycle) 和 [Part 4 · Host 合约](/zh/part-4/7-host-contract)。
:::

::: tip 真相源头
本页讲行为；命令语法的权威定义在 `/help` 和 [`agentao/cli/help_text.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/help_text.py)。两边不一致以 `/help` 为准。
:::
