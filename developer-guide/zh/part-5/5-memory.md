# 5.5 记忆系统（Memory）

> **本节你会学到**
> - 记忆系统的四种数据：持久化记录、会话摘要、召回候选、待审条目
> - 两个 SQLite 数据库（项目级 + 用户级）以及如何选
> - 怎样干净地清空 / 迁移 / 禁用记忆

记忆系统让 Agent **跨会话记住**用户偏好、项目事实、约定。区别于"对话历史"（本轮聊天内容），**记忆**是跨会话持久化的。

## 记忆的四种数据

| 类型 | 存储位置 | 作用 |
|------|---------|------|
| **持久化记忆** (MemoryRecord) | SQLite `memories` 表 | 偏好、事实、约定——软删除，永不物理删除 |
| **会话摘要** (SessionSummaryRecord) | SQLite `session_summaries` 表 | 上下文压缩时由 LLM 生成的会话摘要 |
| **召回候选** (RecallCandidate) | 仅在内存中 | 按当前用户消息打分出来的 top-k 记忆 |
| **待审条目** (MemoryReviewItem) | SQLite `memory_review_queue` 表 | 结晶器产出的候选，等 `/memory review approve`。结晶器只读 `role: "user"` 消息。没有任何批量清除能碰到这张表——见下方清除警示 |

### 持久化记忆的字段

每次 `save_memory` 都走 `MemoryManager.upsert()`（[`manager.py`](https://github.com/jin-bo/agentao/blob/main/agentao/memory/manager.py)）。调用没给的字段由 guard（[`guards.py`](https://github.com/jin-bo/agentao/blob/main/agentao/memory/guards.py)）补上：

| 字段 | 取值 | 怎么定 |
|------|------|--------|
| `scope` | `user` / `project` | 显式传的 `scope` 优先。否则 key 以 `user_` 开头，或 tags 含 `user`、`preference`、`profile`、`personal` 之一，判为 `user`；其余都是 `project`。 |
| `type` | `preference` / `profile` / `project_fact` / `workflow` / `decision` / `constraint` / `note` | 显式传的 `type` 优先。否则按标签匹配；一个都没匹配上就是 `note`。 |
| `source` | `explicit` / `auto` / `crystallized` | `save_memory` 写的是 `explicit`；批准的待审条目是 `crystallized`。 |
| `confidence` | `explicit_user` / `inferred` / `auto_summary` | `source` 为 `explicit` 时是 `explicit_user`。 |

- **Upsert：** 身份是 `(scope, key_normalized)`。同一个 key 再存一次会原地更新那一行，`created_at` 保持不变。
- **长度上限：** 规范化后的 key 保留 80 字符，标题保留 120 字符。内容超过 4000 字符直接拒绝，不会截断。

### 会话摘要

只有一次完整压缩**成功**时，上下文压缩器才写一条摘要（`ContextManager.commit_compaction`）。被跳过、被取消或失败的压缩什么都不写，microcompaction 也不写摘要。压缩窗口里含 MCP skill 内容时，这条摘要同样不落库。

一条摘要只走一条通道到达模型：

| 摘要来自 | 通道 |
|----------|------|
| 当前会话 | `agent.messages` 里的 `[Conversation Summary]` 块 |
| 以往会话 | `<memory-stable>` 里的 `<session>` 部分 |

`get_cross_session_tail()` 取 `session_id` 不是当前会话的最新 3 条摘要，只保留其中最新的 800 字符（`SESSION_TAIL_CHARS`）。

当前会话 id 由 `archive_session(session_id)` 设置。CLI 和 ACP 都传入对话自己的 id，所以恢复的对话不会把自己的摘要再经尾部注入一遍。**自己恢复对话的宿主也要这样做：**

```python
agent.memory_manager.archive_session(conversation_id)
```

不传参数时，`archive_session()` 会生成一个新的随机 id。被恢复对话自己的摘要就会被当成"以往会话"送回来，模型会看到两遍。

## 两个 SQLite 数据库

```
<working_directory>/.agentao/memory.db  ← 项目级
~/.agentao/memory.db                     ← 用户级（跨项目）
```

项目级保存"这个项目专属的事实"（项目代号、团队约定、部署路径）；用户级保存"用户本人偏好"（喜欢 tabs 还是 spaces、工作时区）。

### 表结构与变更计数

每个数据库里有这些表（[`storage.py`](https://github.com/jin-bo/agentao/blob/main/agentao/memory/storage.py)）：

| 表 | 内容 |
|----|------|
| `memories` | 持久化记录，用 `deleted_at` 软删除 |
| `session_summaries` | 压缩摘要（只用项目库） |
| `memory_review_queue` | 结晶器候选（只用项目库） |
| `memory_events` | 内部的写操作审计日志 |
| `schema_meta` | schema 版本和 `memories_change` 计数器 |

`memories` 上的三个触发器在每次插入、更新、删除时把 `memories_change` 加 1。触发器存在数据库文件里，所以任何写入方都会推进它：另一个会话、另一个进程，或者旧版 Agentao。召回索引以这个计数器为键，所以别的会话写入的记忆，下一次召回就能看到。

`<memory-stable>` 不看这个计数器，只在本会话自己写入时重建，这样缓存的提示词前缀才能保持逐字节不变。

## 降级：文件系统不可写怎么办

ACP 子进程、受限容器、只读文件系统里，记忆 DB 可能写不了。fallback 策略下沉到存储层（`SQLiteMemoryStore.open_or_memory`），由嵌入式工厂负责接进来：

```
SQLiteMemoryStore.open_or_memory(<cwd>/.agentao/memory.db)
 ├─ 成功 → 正常使用
 └─ 失败（OSError / sqlite3.Error）
     → 返回 SQLiteMemoryStore(":memory:")
     → 记一条 warning
     → Agent 继续启动（不崩溃）
```

用户作用域走严格构造 `SQLiteMemoryStore.open(...)`：失败时直接禁用该作用域，而不是降级到 `:memory:`（否则用户作用域写入会被默默路由到项目作用域，混淆两层语义）。**记忆永远不会让 Agent 启动失败**——最差就是丢失跨会话持久化。

## 在嵌入里怎么用

大多数情况你**不需要直接碰 MemoryManager**——Agentao 自己会：

1. 每轮 `chat()` 前把相关记忆注入请求（`<memory-context>` 块）
2. LLM 通过内置 `save_memory` 工具主动存记忆
3. `clear_history()` 时不清记忆（记忆跨会话存在）

### 自定义 DB 路径

如果你要把多个 Agent 实例的记忆**完全隔离**（多租户），每个实例传不同的 `working_directory` 就够了——项目 DB 会落在 `<working_directory>/.agentao/memory.db`，天然隔离。

### 禁用用户级记忆

默认工厂会读写 `~/.agentao/memory.db`。如果你的产品不希望 Agent 沾染"用户"层的任何状态，自己构造 `MemoryManager`、显式 `user_store=None` 传进来即可（#16 之后 `MemoryManager` 接的是预先构造好的 store）：

```python
from agentao.memory import MemoryManager, SQLiteMemoryStore

workdir = Path("/tmp/sess")
agent = Agentao(
    working_directory=workdir,
    memory_manager=MemoryManager(
        project_store=SQLiteMemoryStore.open_or_memory(
            workdir / ".agentao" / "memory.db"
        ),
        # user_store=None — 不要跨项目用户作用域
    ),
    llm_client=...,
)
```

没有用户 store 时，用户作用域的写入会落进项目库。这次降级会记日志，日志里从不带 key 和 value：

- 显式传 `scope="user"` 记一条 warning。
- 推断出来的用户作用域（`user_` 开头的 key、`preference` 标签）记 debug。在只有项目库的管理器上这是常态。

### 完全禁用记忆

最简单的方法：让项目 store 指向 `:memory:`（user_store 默认 `None`，无需显式给）：

```python
from agentao.memory import MemoryManager, SQLiteMemoryStore

agent = Agentao(
    working_directory=Path("/tmp/sess"),
    memory_manager=MemoryManager(
        project_store=SQLiteMemoryStore(":memory:"),
    ),
    llm_client=...,
)
```

会话内工作，进程结束即丢。传入的管理器由你关闭：`agent.close()` 不关它，所以在它之后调用 `agent.memory_manager.close()`——`:memory:` store 在此之前一直占着连接。

### 自定义记忆后端（Redis / Postgres / 远端 API）

`MemoryStore` 能力协议（`agentao.capabilities.MemoryStore`）就是这种场景的官方注入点——实现一次它的 15 个方法，把实例作为 `project_store=` / `user_store=` 传入即可。详见 [2.2 · 第 3 档](/zh/part-2/2-constructor-reference#第-3-档-·-高级注入) 与 `agentao/capabilities/memory.py`。

## 提示词里的两个记忆块

Agentao 注入两种记忆块，而且**它们挂在不同的消息上** —— 这正是拆开它们的意义：

### `<memory-stable>` — 稳定块

在 **system 消息**里，是它的最后一段（`agentao/prompts/builder.py::SystemPromptBuilder.build()`，由 `agent._build_system_prompt()` facade 转发）。放**长期、结构化**的记忆。每轮 `chat()` 都一样，从而享受 **prompt cache**（大多数 LLM 厂商把稳定前缀缓存起来、降费降延迟）。

条目由 `MemoryManager.get_stable_entries()` 挑选：

| 条目 | 规则 |
|------|------|
| 所有 `user` 作用域条目 | 一律纳入 |
| 类型为 `decision`、`constraint`、`workflow`、`profile`、`preference` 的 `project` 条目 | 一律纳入 |
| 类型为 `project_fact`、`note` 的 `project` 条目 | 只取最近更新的 3 条 |
| 其余条目 | 只走动态召回 |

这个块的预算是 2000 字符（`STABLE_BLOCK_MAX_CHARS`），每条事实的 value 最多显示 240 字符。渲染器（[`render.py`](https://github.com/jin-bo/agentao/blob/main/agentao/memory/render.py)）先给以往会话的摘要预留空间，剩下的预算按**最新条目优先**贪心填入，放不下的跳过。保留下来的条目按 `created_at` 顺序输出。被跳过的条目仍可能经动态召回出现。

### `<memory-context>` — 动态召回块

在**仅属于请求的易变尾消息**里（`SystemPromptBuilder.build_volatile_tail()`），那是一条从不落盘的尾部 `user` 消息。每一轮，`MemoryRetriever` 按当前用户消息给已存记忆打分，保留前 5 条；已经在 `<memory-stable>` 里的条目不再重复。这个块的预算约 1200 字符。

分数由这几项组成（[`retriever.py`](https://github.com/jin-bo/agentao/blob/main/agentao/memory/retriever.py) 的 `score()`）：

| 项 | 权重 |
|----|------|
| 标签精确匹配 | ×4（查询只有 2 个 token 时 ×2.5，只有 1 个时 ×1.5） |
| 标题 Jaccard 相似度 | ×3 |
| 关键词匹配（关键词会被切词，`agent.py` 能命中 `agent`） | ×2 |
| 内容匹配（前 500 字符） | ×1 |
| 文件路径提示（最近 10 条消息里的路径命中关键词） | ×2 |
| 时效性，`1 / (1 + 天数/30)` | ×1 |
| 陈旧（超过 90 天未更新） | −2 |

前五项一项都没命中的条目永远不会被召回，再新也不行。每条事实的 `reason` 属性列出命中的项。

它刻意**不**放在 system 消息里：它按查询生成，放进去就会让 `messages[0]` 每轮都变，从而废掉 provider 覆盖整段历史的缓存前缀。见 `developer-guide/zh/part-5/6-system-prompt.md`。

**两块的分工**：
- 稳定块 = "这个用户一直是这样的"
- 动态块 = "这次用户问的跟历史哪几条相关"

## MemoryGuard：敏感信息防护

`MemoryGuard` 在记忆写入前做验证。默认配置（`agentao/memory/guards.py`）拒绝明显的敏感信息：

- API keys 和 token（共用 `agentao/security/secret_scan.py` 的模式）、私钥头，以及 `password=` / `secret=` 这类赋值

它不检测信用卡号、身份证号这类 PII。每次写入依次调用 `validate_title`、`validate_content`、`detect_sensitive(content)`；最后一个抛 `SensitiveMemoryError` 即拒绝写入。

自定义时重写 `detect_sensitive`：

```python
from agentao.memory.guards import MemoryGuard, SensitiveMemoryError

class StrictGuard(MemoryGuard):
    def detect_sensitive(self, content: str) -> None:
        super().detect_sensitive(content)   # 复用默认检查
        # 再加你的规则
        if "internal-only" in content.lower():
            raise SensitiveMemoryError("Cannot store 'internal-only' content")

agent = Agentao(working_directory=Path("/tmp"))
agent.memory_manager.guard = StrictGuard()
```

## LLM 能做什么、不能做什么

Agentao 只给 LLM 暴露了**写**记忆的工具：

```python
# LLM 可以调
save_memory(key="preference", value="user prefers TypeScript strict mode")
```

**LLM 不能**：
- 列出所有记忆
- 删除记忆
- 清空记忆
- 搜索记忆（但它看到的 `<memory-context>` 块已经是召回结果）

这是**故意**的——避免 LLM 被 prompt injection 攻击后读/写/删用户的整个记忆库。所有读/删/清操作只通过 CLI 命令 `/memory search`、`/memory delete`、`/memory clear` 提供，或你的 host 直接调 `MemoryManager` API。旧版本曾有 `search_memory` 和 `delete_memory` 工具，现已移除。

## 子 agent 的记忆边界

子 agent 有自己的 `MemoryManager`，建在一个临时的 `:memory:` store 上，子 agent 关闭时一起丢弃（`agents/tools/_inherit.py::_child_memory_manager`）。

| 内容 | 落到哪里 | 原因 |
|------|----------|------|
| `save_memory` 写入 | **父级**的管理器，包括它的用户库和宿主注入的管理器 | 长期记忆应该落在父级记忆所在的地方 |
| 子 agent 压缩写的会话摘要 | 子 agent 的临时 store | 写进父库的话，会在 `<memory-stable>` 里被当成"以往会话"送回来 |
| 结晶器候选 | 子 agent 的临时 store | 子 agent 的 `user` 消息是父模型写的任务 prompt；一旦批准，就成了用户从没说过的记忆 |
| 读取（`<memory-stable>`、召回） | 无 | 临时 store 是空的。子 agent 的背景交代来自 `parent_context`，里面只有父级最近的对话消息，没有任何记忆 |

最后一行是刻意的取舍：子 agent 可以写入一条自己读不回来的记忆。如果 `save_memory` 改绑失败，子 agent 就干脆拿不到 `save_memory`。

## 在你的 UI 里做记忆管理

宿主可以直接调 `MemoryManager` 给用户提供"查看/删除记忆"的 UI：

```python
mm = agent.memory_manager

# 列出项目级所有记忆（不传 scope 时返回 project + user 合集）
for record in mm.get_all_entries(scope="project"):
    print(record.title, "—", record.content[:60])

# 搜索
for record in mm.search("typescript"):
    print(record)

# 按 id 软删除（按用户友好的 key 删用 ``delete_by_title``）
mm.delete(record_id)

# 完全清空：把所有作用域的 memory 都软删除 + 删掉会话摘要。
# 一定要检查返回值：存储失败通过它回报，既不抛异常也不体现为计数 ——
# 计数 0 同样可能只是「本来就没东西可删」。
result = mm.wipe_all()
if not result.ok:
    print("仍然残留：", ", ".join(result.not_cleared))

# 更细的原语仍然可用。注意 `clear()` 在存储失败时会抛异常，
# 而且 project 库已提交之后抛出，会把你接下来要做的事一起跳过 ——
# 这正是 `wipe_all()` 存在的理由。
mm.clear()                       # 两个作用域（`wipe_all()` 调的就是它）
mm.clear(scope="project")        # 只清一个作用域
```

**合规价值**：给用户"查看 AI 知道关于我的什么"和"一键遗忘"的按钮，是很多 SaaS 场景的硬性要求。

::: warning `wipe_all()` 不等于抹除——做"一键遗忘"之前先读这段
有两样东西能活过它，合规按钮这两样都得处理。

**记忆是软删除。** `wipe_all()` 只是给每一行打上 `deleted_at`；`content` 列原文
还在，行也还在 `memory.db` 里。之后任何读路径都不会再拿到它——召回不会、提示块
不会、`get_all_entries()` 也不会——但 `ok is True` **不代表**那些字节没了。真正的
抹除要你自己 `DELETE` 加 `VACUUM`，或者干脆处理掉整个数据库文件。

**review queue 完全没动。** 规则结晶器写进 `memory_review_queue` 的待审候选是第
四类数据：它们带着被提取消息的原文摘录，清除之后依然存在，`/memory review` 照样
列得出来。唯一的补救是 `reject_review_item(id)`，一条一条退。

它**确实**清掉的：两个作用域的长期记忆（软删除），以及所有会话的会话摘要（硬
`DELETE`）。
:::

备份、查看或迁移记忆数据库，见 [CLI 6 · 备份与查看数据库](/zh/cli/6-memory#备份与查看数据库)。

## 记忆 vs 会话历史 vs AGENTAO.md

| 存放内容 | 用哪个 |
|---------|-------|
| "这次对话刚聊的事" | 会话历史（`agent.messages`；`clear_history()` 清） |
| "用户一直是 Python + tabs 党" | 记忆（`save_memory`） |
| "本项目用 Ruff 做 lint、端口 8080" | `AGENTAO.md`（项目级约束，进 git） |
| "本轮需要的技术方案" | Plan 模式（不跨会话） |

## ⚠️ 常见陷阱

::: warning 上线前先确认这几条
- ❌ **把大文档塞进记忆** —— 召回会把它带进每个 prompt，撑爆上下文
- ❌ **多租户共享记忆** —— 用户作用域 DB 是进程全局，跨租户泄漏只是时间问题
- ❌ **忘记记忆跨 `clear_history()` 存活** —— "新对话"还记得旧事

下面每一条都附完整修法。
:::

### ❌ 把大文档塞进记忆

```python
save_memory("doc", open("readme.md").read())   # 几十 KB
```

每轮都会被召回，爆上下文。记忆应该是**几百字以内的结构化陈述**。大文档放仓库里让 Agent 按需用 `read_file` 读。

### ❌ 多租户共享记忆

两个用户的 Agent 如果都落在默认 `Path.cwd()` 或同一个 `working_directory`，会读写**同一个** DB，互相泄漏信息。多租户必须按用户隔离 `working_directory`。

### ❌ 忘记记忆跨 `clear_history()` 存活

用户在 UI 上点"新对话"→ `agent.clear_history()` 只清会话，不清记忆。如果"新对话"应该忘掉一切，要调 `MemoryManager.wipe_all()`，**并检查 `result.ok`**：一次静默失败的清除会留下会话摘要，而 `get_cross_session_tail()` 会把它们原样塞回下一个 prompt。

## TL;DR

- 记忆 ≠ 对话历史。记忆跨会话存在 SQLite；历史活在 `agent.messages` 上。
- 两个作用域 / 两个 DB：**项目**（`<wd>/.agentao/memory.db`）和**用户**（`~/.agentao/memory.db`）。多租户部署里要么按 `tenant_id+user_id` 拆分用户作用域，要么直接禁用。
- 只读 / 受限 FS 自动降级为内存 store 并 warning——Agent 还是能起。
- `clear_history()` **不**清记忆；这是设计如此。要"新对话"彻底忘记时显式调 `MemoryManager.wipe_all()`，并检查 `result.ok` —— 计数分不清「清除失败」和「本来就是空的」。

→ 下一节：[5.6 系统提示定制](./6-system-prompt)
