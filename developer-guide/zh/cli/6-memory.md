# 6. 记忆

Agent 有一层持久化记忆。它在工作过程中把关于你和项目的事实写下来（`save_memory` 工具），后续轮次召回，跨会话保留，`/new` 也带不走（`/clear` 会清掉）。本页讲怎么查、搜、清这些条目。

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

每一轮，模型会收到两个块：

- **`<memory-stable>`** — 核心、慢变的记忆（你的角色、项目锚点），位于缓存的系统提示末尾。从上往下渲染，有预算上限。
- **`<memory-context>`** — Top-K 召回候选，按当前用户消息评分。易变：每轮重建，放在每次请求的 `<system-reminder>` 里，不在系统提示里。

`<memory-stable>` 还带有本项目*以往*会话的摘要，最多 3 条。当前会话自己的摘要不会重复放进去：它们已经以 `[Conversation Summary]` 块的形式在会话历史里，是压缩时加入的。

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

在 title / content / 规范化键 / tags / 提取的关键词里搜。大小写不敏感、子串匹配。

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

参数先按条目**标题**匹配：必须整个标题匹配，不区分大小写，两个作用域里所有同名条目都会被删除。没有标题匹配时，再把参数当 key 试：先原样匹配，再转小写、空格换成 `_` 匹配。tag 和 ID 都不参与匹配。

软删除：DB 里行还在，只是写了 `deleted_at` 时间戳，从所有可见地方过滤掉。Agent 下一轮看不到。

## `/memory clear` — 软删全部

```text
> /memory clear
Are you sure you want to delete ALL memories? This cannot be undone. [y/N]: y
Successfully cleared 47 memory(ies)
```

需要确认。`memories` 和 `session_summaries` 一起清（前者软删除，后者表清空）。两个作用域都会清——当前项目的记忆*和*用户全局记忆——外加本项目所有会话摘要；其他项目自己的 `.agentao/memory.db` 不受影响。清除有一部分失败时，命令会列出没清掉的部分，不再照常提示成功。

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

显示本会话最近若干份压缩摘要（最多 10 份），只保留末尾 2000 字符。每条摘要是上下文压缩流水线触发时写入的内容 — 想确认 agent 对早期已看不到的轮次"还记得多少"时用。以往会话的摘要不在这里列出。

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
| Session summaries | 本会话写入的压缩摘要数 |
| Recall hits (session) | 本会话中动态块实际注入记忆的次数 |
| Recall errors (session) | 召回查询失败次数（DB 锁、schema 不匹配等）。有过失败时，后面多一行 `Last recall error` |
| Stable block size | 每轮注入的 `<memory-stable>` 字符数 |
| Latest session summary | 本会话最新一份摘要的字符数 |

怀疑记忆把 context 撑爆了，或召回不工作（"agent 应该知道这事啊！"）时用它来排查。

## `/memory crystallize` 和 `/memory review`

这是可选的"记忆 crystallize"工作流 — CLI 扫一遍**当前**会话，找出值得固化下来的事实，放进审查队列。然后你逐条批准或驳回，批准的会作为 `source=crystallized` 类型的记忆条目入库。

每次压缩成功时，这个基于规则的结晶器也会自动跑一遍。它只读你的 `role: "user"` 消息，匹配偏好、约束、决定、工作流这几类模式。assistant 的文字永远不会触发它，所以回复里出现 "I prefer X" 不会产生候选。同一条候选再次匹配时会并入已有的待审条目，`occurrences` 计数加一。

| 命令 | 作用 |
|---|---|
| `/memory crystallize` | 扫当前会话，候选项进审查队列 |
| `/memory review` | 列出待审查项 |
| `/memory review approve <id>` | 把候选提升为真实记忆 |
| `/memory review reject <id>` | 丢弃候选 |

跟第 5 章的 `/crystallize` 不同：那个产出 **skills**；这个产出**记忆条目**。同样的思路，输出对象不同。

## Agent 能拿记忆做什么

LLM 只有**一个**记忆相关工具：`save_memory(key, value, tags?, scope?, type?)`。它能写 — 不能列、不能搜、不能删。这是有意为之 — 搜/删只在 CLI 端，避免 agent 误删自己的上下文。（旧版本曾有 `search_memory` 和 `delete_memory` 工具，现已移除。）

- **作用域：** key 以 `user_` 开头，或 tags 含 `user`、`preference`、`profile`、`personal` 之一，条目存进用户库；其余存进项目库。
- **同一个 key 存新值：** 再存一次同一个 key 会原地更新。让 agent "更新"某条记忆，它就会覆盖旧值。
- **会被拒绝的写入：** 超过 4000 字符的内容，以及看起来像凭据的内容（API key、私钥、`password=`）。

召回是自动的：每轮 retriever 把所有已存记忆按当前消息评分，把 top-K 注入 `<memory-context>`。Agent 没有"召回工具"，它只是读已经摆在它眼前的内容。

## 命令与 `MemoryManager` 方法的对应

每个命令都只是对一个公开方法的薄封装，嵌入式宿主可以照样做出同样的功能（[`commands_ext/memory.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands_ext/memory.py)）：

| 命令 | 方法 |
|------|------|
| `/memory`、`/memory list` | `get_all_entries()` |
| `/memory user`、`/memory project` | `get_all_entries(scope="user")`、`get_all_entries(scope="project")` |
| `/memory search <query>` | `search(query)` |
| `/memory tag <tag>` | `filter_by_tag(tag)` |
| `/memory delete <标题或 key>` | `delete_by_title(title)`；按 key 命中时再 `delete(id)` |
| `/memory clear` | `wipe_all()` |
| `/memory session` | `get_recent_session_summaries(limit=10)` |
| `/memory crystallize` | `crystallize_user_messages(agent.messages)` |
| `/memory review` | `list_review_items(status="pending")` |
| `/memory review approve <id>` | `approve_review_item(id)` |
| `/memory review reject <id>` | `reject_review_item(id)` |

## 容易踩的坑

- **Agent 会自己存你没让它存的东西** — 设计就是这样。存错了或噪声大，用 `/memory delete` 删；有规律的话直接告诉 agent（"别记关于 X 的东西"）。
- **`/memory clear` 会清 user + 当前 project 两个 scope** — 这是软删除；其他项目自己的 `.agentao/memory.db` 不受影响。
- **记忆 ≠ skills** — skill 教 agent 怎么做某类任务；记忆告诉它关于你 / 项目的事实是什么。不要把"行为"固化成记忆 — 那是 skill 的事。
- **把机密存进记忆** — guard 会拒绝明显的凭据，但它只是模式匹配。别让 agent 记密码或 token。
- **把长文档存进记忆** — 一条记忆应该是一句简短的陈述，`<memory-stable>` 里每条 value 最多显示 240 字符。长内容放文件里，把路径存成记忆。
- **手编 `memory.db`** — 知道自己在干嘛就行。schema 在 [`agentao/memory/storage.py`](https://github.com/jin-bo/agentao/blob/main/agentao/memory/storage.py)，Agentao 按 `_SCHEMA_VERSION` 自动迁移，别自己改表结构。多数人就用 `/memory delete` 即可。

## 备份与查看数据库

记忆就是普通的 SQLite。数据库跑在 WAL 模式下，直接 `cp` 可能漏掉最近的写入，所以用 `sqlite3` 客户端的在线备份：

```bash
# 备份（Agentao 运行时也安全）
sqlite3 .agentao/memory.db ".backup .agentao/memory.db.bak"
sqlite3 ~/.agentao/memory.db ".backup $HOME/.agentao/memory.db.bak"

# 查看有效条目
sqlite3 .agentao/memory.db "SELECT scope, type, key_normalized, title FROM memories WHERE deleted_at IS NULL;"

# 恢复（先退出 Agentao）
sqlite3 .agentao/memory.db ".restore .agentao/memory.db.bak"
```

要把记忆搬到另一台机器，把备份文件复制到那边相同的路径即可。

## 故障排查

### 记忆没进 prompt

1. 跑 `/memory status`，确认条目数不是 0、`Recall errors` 为 0。召回出错会以 warning 记进 `agentao.log`。
2. 确认 `.agentao/memory.db` 可写。不可写时 Agentao 会退回内存 store，退出后所有记忆都会丢。
3. `Recall hits` 一直不涨，说明没有条目得分。只有你的消息和条目的标签、标题、关键词或内容有共同的词，它才会被召回。换更贴切的标签或标题重新存一次。
4. 想让某条每轮都出现，就给它一个结构化类型：打上 `decision`、`constraint`、`workflow` 或 `preference` 标签，它就会进 `<memory-stable>`。注意 `preference` 标签同时会让它成为用户作用域条目。

### `/memory delete` 找不到条目

- 先 `/memory list`，复制准确的标题。标题必须整串匹配，不区分大小写。
- 也可以用 key，按存储的写法（`snake_case`）。
- 删除是软删除：行还留在文件里，只是写了 `deleted_at`，所以文件不会变小。会话摘要不受影响。

### 会话摘要不见了

- 只有一次完整压缩**成功**时才会写摘要。短会话根本不压缩，自然没有摘要。
- 自动压缩连续失败三次后，熔断器会暂停自动压缩。这时 `/context` 显示 `Compact breaker: open (3/3 consecutive failures, last: …)`。跑一次 `/compact`：它以探针身份运行，成功即复位熔断器。`/clear` 同样会复位。
- 压缩也可能被有意**取消**：`PreCompact` 命令钩子返回 `{"hookSpecificOutput": {"compactionDecision": "cancel"}}`，或宿主的 `compaction_controller=` 返回取消。取消不计入熔断器，`/context` 里也看不到，去 `agentao.log` 里找 `Compaction cancelled:`。
- `/clear` 和 `/memory clear` 会删掉**所有**会话的摘要。`/new` 不删，以往会话的摘要照样进入 `<memory-stable>`。

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
