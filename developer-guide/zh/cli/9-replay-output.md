# 9. 回放与输出

三个命令：捕获 agent 干了什么 + 控制你看到什么 — `/replay` `/copy` `/markdown`。

## `/replay` — 录制 / 列表 / 检视 / 清理会话

录制开启后，agent 发出的每个事件（LLM 回复、工具调用、工具结果、权限决策、记忆写入...）都被追加到 `.agentao/replay/` 下的 JSONL 文件。之后你可以列表、渲染、看尾部、清理这些文件。

### `/replay on` 和 `/replay off`

```text
> /replay on
Replay recording ON. (max_instances=20)
```

持久化到 `.agentao/settings.json`，下次启动也是这个状态。默认关 — 想要审计轨迹时再开。

### `/replay` 和 `/replay list` — 列出录制

```text
> /replay
Replay recording: on  (max_instances=20)

Saved Replays (3):

  • a1b2c3
    Refactor the auth module to use middleware…
    47 events · 8 turns  ⚠ has errors
    Created: 2026-04-30 14:08  Updated: 2026-04-30 14:42
    File: a1b2c3-2026-04-30T14-08-12.jsonl

  • d4e5f6
    Find the 3 largest files under cwd
    12 events · 2 turns
    Created: 2026-04-30 13:50  Updated: 2026-04-30 13:51
    File: d4e5f6-2026-04-30T13-50-04.jsonl
  ...

Usage: /replay show <id>  or  /replay tail <id> [n]  or  /replay prune
```

每条显示：
- Short ID（6 字符前缀，其他命令都用它）
- 第一条用户消息预览
- 事件 / 轮次计数；有错误事件会标 `⚠ has errors`
- 创建 / 更新时间（本地）
- 底层文件名

最新的在前。

### `/replay show <id>` — 完整渲染

```text
> /replay show a1b2c3
```

按轮分组渲染所有事件。默认视图把相关事件聚到一起（`tool_call` + `tool_result`），加 flag 改切片方式：

| Flag | 效果 |
|---|---|
| `--raw` | 扁平按时间序展示，不分组 |
| `--turn <tid>` | 只看某一轮 |
| `--kind <kind>` | 按事件类型过滤（`tool_call` `permission_decision` `memory_write` ...） |
| `--errors` | 只看标为 error 的事件 |

`<id>` 是 short ID 的**前缀**。`/replay show a1` 在唯一时就能定位；多个 `a1*` 都匹配时 CLI 把候选列出来让你确认。

### `/replay tail <id> [n]` — 尾部 N 个事件

```text
> /replay tail a1b2c3 30
```

最后 `n` 个事件的扁平视图（默认 20）。长 replay 末尾出问题、只关心尾巴时用。

### `/replay prune` — 清理旧 replay

```text
> /replay prune
Pruned 5 replay(s) beyond max_instances=20.
```

删掉超过 `replay.max_instances`（在 `.agentao/settings.json` 配）的最旧 replay。不弹确认 — 有上限保护，安全。

### `/replay delete <id>` 和 `/replay delete all`

```text
> /replay delete a1b2c3
Deleted replay a1b2c3.

> /replay delete all
Are you sure? This deletes all replays except the active one. [y/N]: y
Deleted 18 replays. (Skipped: 1 active)
```

`delete <id>` 删一个具体 replay（前缀匹配，跟 `show` 一样）。`delete all` 抹掉所有 replay 文件**除了**当前正在录的那个（不让你误伤活会话）。`all` 形态需要确认。

## 什么时候用 replay

| 情况 | replay 给你什么 |
|---|---|
| Bug 报告 — "agent 干了件怪事" | 完整事件日志，含工具参数和结果 |
| 成本分析 — "token 都花在哪了" | `--raw` 视图里逐轮 token 计数 |
| 调试自定义插件 | 插件 hook 决策也作为事件被记录 |
| 审计 / 合规 | 每次会话一份 JSONL 轨迹 |
| 重现某次会话做测试 | Replay 文件是嵌入式 `Replay` API 的合法输入 |

## 容易踩的坑

- **录制默认关是为了性能** — 需要时再开；长会话 JSONL 文件会很大
- **`max_instances` 在写入时就 FIFO 淘汰** — 超过上限旧的自动消失；只有看到延迟才用 `/replay prune`
- **Short ID 来自完整 ID** — 重命名不会变，但写脚本的话用完整文件名更稳
- **`delete all` 是真的会删干净** — 没有"replay 的 replay"。要留就先备份

## `/copy` — 复制最近一条助理消息

```text
> /copy
Copied last response to clipboard.
```

把最近一条助理消息（原始 Markdown）复制到系统剪贴板。要把答案粘到文档、工单、聊天里时用。

复制了什么：
- 仅最近一条助理消息 — 不是整段对话
- Markdown 源码，**不是**渲染后的（标题保持 `#`，代码块保持 fence）
- 不含工具调用轨迹和推理摘要

`/copy` 提示 "nothing to copy"，要么会话刚开始，要么最近的不是助理消息。

## `/markdown` — 切换富渲染

```text
> /markdown
Markdown rendering: ON

> /markdown
Markdown rendering: OFF
```

切换助理回复是按 Markdown 渲染（粗体、代码块、标题等）还是按原文显示。

什么时候关：
- CLI 输出要管道送给文件或下游工具
- Markdown 渲染把输出搞乱了（罕见，但奇怪 Unicode 时会有）
- 想看 LLM 一字不差地写了什么

状态是会话级的，不持久化。

## 三个命令一起用

常见工作流：

1. `/replay on` — 开录
2. 做事，看 agent
3. `/copy` — 把最终答案抓去贴 PR / 文档
4. `/replay list` — 找到刚才的录制
5. `/replay show <id>` — 离线复盘
6. 提取完了 `/replay delete <id>`

## 接下来读什么

| 想做的事 | 读 |
|---|---|
| 把 replay 当测试输入用 | [Part 4 · 事件层](/zh/part-4/) |
| 调 replay 存储上限 | [10. 配置文件参考](./10-config-reference) |
| 理解被记录的事件 schema | [Part 4.2 · AgentEvent](/zh/part-4/2-agent-events) |

---

::: info 这一章在体系里的位置
Replay 实现在 `agentao.replay`。嵌入式宿主可以直接读相同的 JSONL 文件，用 `agentao.replay.read_replay()` 反序列化。事件 schema 在 CLI 和嵌入两条路径上一致。CI 里可以用 replay 验证行为改动不破坏过去的会话。
:::

::: tip 真相源头
命令语法：`/help`。行为：[`agentao/cli/replay_commands.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/replay_commands.py)。渲染逻辑：[`agentao/cli/replay_render.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/replay_render.py)。存储：[`agentao/replay/`](https://github.com/jin-bo/agentao/blob/main/agentao/replay/)。
:::
