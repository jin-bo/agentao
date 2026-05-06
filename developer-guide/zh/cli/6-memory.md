# 6. 记忆

Agent 有一层持久化记忆。它在工作过程中把关于你和项目的事实写下来（`save_memory` 工具），后续轮次召回，跨会话保留，`/clear` 也带不走。本页讲怎么查、搜、清这些条目。

## 记忆是什么 / 不是什么

| 记忆是 | 记忆不是 |
|---|---|
| 长期、跨会话的知识 | 对话历史（那是会话级的） |
| Agent（LLM 端）写入的 | CLI 写入的 |
| `/memory delete` 是软删除 | 物理擦除（DB 里原始记录还在） |
| 用 `/memory` CLI 命令查看 | CLI 端可编辑（没有 `/memory edit`） |
| 两个作用域：`user`（跨项目）和 `project` | 共用一份文件 |

两个 SQLite 数据库：

| 作用域 | 路径 | 内容 |
|---|---|---|
| Project | `.agentao/memory.db` | 项目级持久记忆 + 会话摘要 |
| User | `~/.agentao/memory.db` | 跨项目的用户级记忆（角色、偏好、profile） |

每个 DB 内：

- **`memories` 表** — 持久条目（`title` `content` `tags` `scope` `type` `source` ...）
- **`session_summaries` 表** — 上下文压缩流水线自动写入的摘要，让被压缩过的会话仍能影响后续轮

召回候选（"这条现在可能有用"的匹配集，会注入到下一轮）是查询时在内存里算出的，从不入库。

## 记忆怎么进入 prompt

每一轮，系统提示里加两个块：

- **`<memory-stable>`** — 核心、慢变的记忆（你的角色、项目锚点）。从上往下渲染，有预算上限。
- **`<memory-context>`** — Top-K 召回候选，按当前用户消息评分。易变，每轮重建。

会话摘要*不*在 `<memory-stable>` 里 — 它们以 `[Conversation Summary]` 块的形式存在于会话历史中，是压缩时塞进去的。

## `/memory` — 列全部

```text
> /memory
Saved Memories (12 total):

  • User Role [user]: Senior backend engineer, currently focused on...
    Tags: profile, role
    Updated: 2026-04-29 14:22:01

  • OAuth integration plan [project]: Use existing session middleware...
    Tags: feature, oauth
    Updated: 2026-04-30 09:15:33

Tag Summary:
  #profile (3)
  #project (2)
  #feature (2)
  ...
```

每条显示标题、作用域（`user` 或 `project`）、内容前 120 字、标签、最后更新时间。底部一个 tag 频次摘要。

## `/memory search <query>` — 关键词搜

```text
> /memory search oauth
Found 2 memory(ies) matching 'oauth':

  • OAuth integration plan [project]: Use existing session middleware...
  • Auth token storage [project]: Tokens go through SealedStorage...
```

在 title / content / tags 里搜。大小写不敏感、子串匹配。

## `/memory tag <tag>` — 按标签过滤

```text
> /memory tag feature
Found 2 memory(ies) with tag 'feature':
  ...
```

标签是 agent 调 `save_memory(tags=[...])` 时写进去的。用 `/memory` 看现有哪些 tag。

## `/memory user` 和 `/memory project` — 单作用域视图

```text
> /memory user        # 仅跨项目的 profile / preference 条目
> /memory project     # 仅当前项目作用域的条目
```

怀疑跨项目污染时（"agent 以为我在做另一个 repo"）— `/memory user` 能看到全局记住的是什么。

## `/memory delete <key>` — 软删一条

```text
> /memory delete OAuth integration plan
Successfully deleted memory: OAuth integration plan
```

参数是条目**标题**，不是 tag 或 ID。完全匹配（大小写敏感）；规范化键作为兜底匹配。

软删除：DB 里行还在，只是写了 `deleted_at` 时间戳，从所有可见地方过滤掉。Agent 下一轮看不到。

## `/memory clear` — 软删全部

```text
> /memory clear
Are you sure you want to delete ALL memories? This cannot be undone. [y/N]: y
Successfully cleared 47 memory(ies)
```

需要确认。`memories` 和 `session_summaries` 一起清（前者软删除，后者表清空）。只影响**当前**作用域和当前项目 — 用户全局记忆除非你在 user 作用域下，否则保留。

::: warning "Cannot be undone" 是从 agent 视角说的
DB 行是软删，懂行的人开 SQLite 浏览器还能找回。但 agent 永远不会再看到，CLI 也没"撤销"按钮。
:::

## `/memory session` — 当前会话摘要

```text
> /memory session
Session Memory (1842 chars, 3 summaries):

[Conversation Summary, 2026-04-30 14:22]
We worked on adding OAuth login. Investigated existing session middleware...

---

[Conversation Summary, 2026-04-30 14:08]
...
```

显示最近若干份压缩摘要（最多 10 份）。每条摘要是上下文压缩流水线触发时写入的内容 — 想确认 agent 对早期已看不到的轮次"还记得多少"时用。

## `/memory status` — 诊断计数

```text
> /memory status

Memory Status:
  Profile  (user):        7 entries
  Project:                12 entries
  Session summaries:      3
  Recall hits (session):  18
  Recall errors (session): 0
  Stable block size:      482 chars
  Latest session summary: 1842 chars
```

每行含义：

| 字段 | 含义 |
|---|---|
| Profile (user) | 用户作用域持久条目数 |
| Project | 项目作用域持久条目数 |
| Session summaries | 已写入的压缩摘要数 |
| Recall hits (session) | 本会话中动态块实际注入记忆的次数 |
| Recall errors (session) | 召回查询失败次数（DB 锁、schema 不匹配等） |
| Stable block size | 每轮注入的 `<memory-stable>` 字节数 |
| Latest session summary | 最近一份 `[Conversation Summary]` 块的字节数 |

怀疑记忆把 context 撑爆了，或召回不工作（"agent 应该知道这事啊！"）时用它来排查。

## `/memory crystallize` 和 `/memory review`

这是可选的"记忆 crystallize"工作流 — CLI 扫一遍**当前**会话，找出值得固化下来的事实，放进审查队列。然后你逐条批准或驳回，批准的会作为 `source=crystallized` 类型的记忆条目入库。

| 命令 | 作用 |
|---|---|
| `/memory crystallize` | 扫当前会话，候选项进审查队列 |
| `/memory review` | 列出待审查项 |
| `/memory review approve <id>` | 把候选提升为真实记忆 |
| `/memory review reject <id>` | 丢弃候选 |

跟第 5 章的 `/crystallize` 不同：那个产出 **skills**；这个产出**记忆条目**。同样的思路，输出对象不同。

## Agent 能拿记忆做什么

LLM 只有**一个**记忆相关工具：`save_memory(key, value, tags?)`。它能写 — 不能列、不能搜、不能删。这是有意为之 — 搜/删只在 CLI 端，避免 agent 误删自己的上下文。

召回是自动的：每轮 retriever 把所有已存记忆按当前消息评分，把 top-K 注入 `<memory-context>`。Agent 没有"召回工具"，它只是读已经摆在它眼前的内容。

## 容易踩的坑

- **Agent 会自己存你没让它存的东西** — 设计就是这样。存错了或噪声大，用 `/memory delete` 删；有规律的话直接告诉 agent（"别记关于 X 的东西"）。
- **`/memory clear` 会清 user + 当前 project 两个 scope** — 这是软删除；其他项目自己的 `.agentao/memory.db` 不受影响。
- **记忆 ≠ skills** — skill 教 agent 怎么做某类任务；记忆告诉它关于你 / 项目的事实是什么。不要把"行为"固化成记忆 — 那是 skill 的事。
- **手编 `memory.db`** — 知道自己在干嘛就行，schema 在 [`agentao/memory/manager.py`](https://github.com/jin-bo/agentao/blob/main/agentao/memory/manager.py)。多数人就用 `/memory delete` 即可。

## 接下来读什么

| 想做的事 | 读 |
|---|---|
| 排查 context 爆炸是不是记忆塞太多 | [7. 上下文与状态](./7-context-status) |
| 理解召回评分公式 | [Part 5.5 · 记忆系统](/zh/part-5/5-memory) |
| 自定义记忆在系统提示里的渲染方式 | [Part 5.6 · 系统提示定制](/zh/part-5/6-system-prompt) |

---

::: info 这一章在体系里的位置
`MemoryManager` 在 agent 上是 `agent.memory_manager`。嵌入式宿主可以直接调 `mgr.get_all_entries()` `mgr.search(...)` `mgr.delete(...)` — 跟 CLI 用的是同一套。两块 prompt 注入是纯运行时细节，嵌入式时同样适用。
:::

::: tip 真相源头
命令语法：`/help`。行为：[`agentao/cli/commands_ext/memory.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands_ext/memory.py)。存储与召回：[`agentao/memory/manager.py`](https://github.com/jin-bo/agentao/blob/main/agentao/memory/manager.py)。
:::
