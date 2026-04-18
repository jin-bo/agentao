# 5.5 记忆系统（Memory）

记忆系统让 Agent **跨会话记住**用户偏好、项目事实、约定。区别于"对话历史"（本轮聊天内容），**记忆**是跨会话持久化的。

## 记忆的三种数据

源码：`agentao/memory/manager.py`、`agentao/memory/models.py`

| 类型 | 存储位置 | 作用 |
|------|---------|------|
| **持久化记忆** (MemoryRecord) | SQLite `memories` 表 | 偏好、事实、约定——软删除，永不物理删除 |
| **会话摘要** (SessionSummaryRecord) | SQLite `session_summaries` 表 | 上下文压缩时由 LLM 生成的会话摘要 |
| **召回候选** (RecallCandidate) | 仅在内存中 | 按当前用户消息打分出来的 top-k 记忆 |

## 两个 SQLite 数据库

```
<working_directory>/.agentao/memory.db  ← 项目级
~/.agentao/memory.db                     ← 用户级（跨项目）
```

项目级保存"这个项目专属的事实"（项目代号、团队约定、部署路径）；用户级保存"用户本人偏好"（喜欢 tabs 还是 spaces、工作时区）。

## 降级：文件系统不可写怎么办

ACP 子进程、受限容器、只读文件系统里，记忆 DB 可能写不了。Agentao 的策略（`agentao/memory/manager.py:59-90`）：

```
尝试打开 <cwd>/.agentao/memory.db
 ├─ 成功 → 正常使用
 └─ 失败（OSError / sqlite3.Error）
     → fallback 到 SQLiteMemoryStore(":memory:")
     → log warning
     → Agent 继续启动（不崩溃）
```

用户级同理。**记忆永远不会让 Agent 启动失败**——最差就是丢失跨会话持久化。

## 在嵌入里怎么用

大多数情况你**不需要直接碰 MemoryManager**——Agentao 自己会：

1. 每轮 `chat()` 前把相关记忆注入系统提示（`<memory-context>` 块）
2. LLM 通过内置 `save_memory` 工具主动存记忆
3. `clear_history()` 时不清记忆（记忆跨会话存在）

### 自定义 DB 路径

如果你要把多个 Agent 实例的记忆**完全隔离**（多租户），每个实例传不同的 `working_directory` 就够了——项目 DB 会落在 `<working_directory>/.agentao/memory.db`，天然隔离。

### 禁用用户级记忆

默认 Agent 会读写 `~/.agentao/memory.db`。如果你的产品不希望 Agent 沾染"用户"层的任何状态：

```python
# 需要直接替换 memory manager
from agentao.memory import MemoryManager

agent = Agentao(working_directory=Path("/tmp/sess"))
# 构造后替换成只用项目 DB 的版本
agent._memory_manager = MemoryManager(
    project_root=agent.working_directory / ".agentao",
    global_root=None,   # 不用用户级
)
```

⚠️ 这是"内部 API"路径，依赖 `_memory_manager` 属性名，未来版本可能变。生产前建议向上游提 issue 要求公开配置。

### 完全禁用记忆

最简单的方法：让项目 DB 指向只读 / 只写 `:memory:`：

```python
from agentao.memory import MemoryManager
from agentao.memory.storage import SQLiteMemoryStore

agent = Agentao(working_directory=Path("/tmp/sess"))
agent._memory_manager.project_store = SQLiteMemoryStore(":memory:")
agent._memory_manager.user_store = None
```

会话内工作，进程结束即丢。

## 提示词里的两个记忆块

Agentao 在系统提示中注入两种块（源码 `agentao/agent.py::_build_system_prompt()`）：

### `<memory-stable>` — 稳定块

放**长期、结构化**的记忆（类型 `profile` / `constraint` / `decision`）。每轮 `chat()` 都一样，从而享受 **prompt cache**（大多数 LLM 厂商把稳定前缀缓存起来、降费降延迟）。

### `<memory-context>` — 动态召回块

根据本轮 user message 做 top-k 召回，每轮不同。从所有已存记忆里按关键词/Jaccard/标签/时间 等打分挑最相关的。

**两块的分工**：
- 稳定块 = "这个用户一直是这样的"
- 动态块 = "这次用户问的跟历史哪几条相关"

## MemoryGuard：敏感信息防护

`MemoryGuard` 在记忆写入前做验证。默认配置（`agentao/memory/guards.py`）拒绝明显的敏感信息：

- API keys、token、password 字面量
- 信用卡号、身份证号等 PII 模式

自定义：

```python
from agentao.memory.guards import MemoryGuard

class StrictGuard(MemoryGuard):
    def validate(self, content: str, key: str) -> None:
        super().validate(content, key)   # 复用默认检查
        # 再加你的规则
        if "internal-only" in content.lower():
            raise SensitiveMemoryError("Cannot store 'internal-only' content")

agent = Agentao(working_directory=Path("/tmp"))
agent._memory_manager.guard = StrictGuard()
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

这是**故意**的——避免 LLM 被 prompt injection 攻击后读/写/删用户的整个记忆库。所有读/删/清操作只通过 CLI 命令 `/memory search`、`/memory delete`、`/memory clear` 提供，或你的 host 直接调 `MemoryManager` API。

## 在你的 UI 里做记忆管理

宿主可以直接调 `MemoryManager` 给用户提供"查看/删除记忆"的 UI：

```python
mm = agent._memory_manager

# 列出项目级所有记忆
for record in mm.list_memories(scope="project"):
    print(record.title, "—", record.content[:60])

# 搜索
for record in mm.search("typescript"):
    print(record)

# 软删除
mm.soft_delete(record_id)

# 完全清空（含会话摘要）
mm.clear_all()
```

**合规价值**：给用户"查看 AI 知道关于我的什么"和"一键遗忘"的按钮，是很多 SaaS 场景的硬性要求。

## 记忆 vs 会话历史 vs AGENTAO.md

| 存放内容 | 用哪个 |
|---------|-------|
| "这次对话刚聊的事" | 会话历史（`agent.messages`；`clear_history()` 清） |
| "用户一直是 Python + tabs 党" | 记忆（`save_memory`） |
| "本项目用 Ruff 做 lint、端口 8080" | `AGENTAO.md`（项目级约束，进 git） |
| "本轮需要的技术方案" | Plan 模式（不跨会话） |

## 常见陷阱

### ❌ 把大文档塞进记忆

```python
save_memory("doc", open("readme.md").read())   # 几十 KB
```

每轮都会被召回，爆上下文。记忆应该是**几百字以内的结构化陈述**。大文档放仓库里让 Agent 按需用 `read_file` 读。

### ❌ 多租户共享记忆

两个用户的 Agent 如果都落在默认 `Path.cwd()` 或同一个 `working_directory`，会读写**同一个** DB，互相泄漏信息。多租户必须按用户隔离 `working_directory`。

### ❌ 忘记记忆跨 `clear_history()` 存活

用户在 UI 上点"新对话"→ `agent.clear_history()` 只清会话，不清记忆。如果"新对话"应该忘掉一切，要同时调 `MemoryManager.clear_all()`。

→ 下一节：[5.6 系统提示定制](./6-system-prompt)
