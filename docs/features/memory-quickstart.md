# Memory Management Quick Start

快速上手 Agentao 的 memory 管理功能。

## 心智模型

Agentao 的 memory 子系统现在由 SQLite 支撑，**职责拆分**很重要：

| 能力 | 谁可以触发 | 接口 |
|------|------------|------|
| **保存** | LLM 或用户 | LLM 工具 `save_memory(key, value, tags?)`，或让 AI 自然语言保存 |
| **召回** | **自动** | 每轮提示词构建时由 `MemoryRetriever` 自动评分并注入 `<memory-context>`，**LLM 不需要也无法显式调用** |
| **搜索 / 过滤 / 删除 / 清空 / 状态 / 审阅** | **仅用户** | CLI 子命令（`/memory search`, `/memory tag`, `/memory delete`, …） |

> **关键变化**：LLM 暴露的 memory 工具只有 `save_memory` 这一个写入接口。
> 历史版本曾允许 LLM 直接调用 `search_memory` / `delete_memory`，**这些工具已经移除**。
> 查询和删除是用户控制能力，避免模型在出错路径里反复擦自己的写入。

存储位置：

| 数据库 | 路径 | 内容 |
|--------|------|------|
| 项目库 | `.agentao/memory.db` | 项目级持久记忆 + 全部会话摘要 + 审阅队列 |
| 用户库 | `<home>/.agentao/memory.db` | 跨项目的用户级偏好 |

两个文件首次启动时自动创建，目录已加入 `.gitignore`。

---

## 5 分钟快速开始

### 1. 保存第一条记忆

让 AI 自然语言保存：

```
You: 请记住这个项目叫 Agentao，用 Python 写的，标签为 project 和 python
AI: [调用 save_memory 工具]
    Saved memory: project_name
```

工具签名（AI 内部调用，仅供参考）：

```python
save_memory(
    key="project_name",
    value="Agentao - Python CLI tool",
    tags=["project", "python"],
)
```

含 `user` 标签的条目会写入用户库（跨项目）；其余写入项目库。

### 2. 查看所有记忆

```bash
/memory          # 列出所有
/memory list     # 同上
/memory user     # 只看用户库
/memory project  # 只看项目库
```

### 3. 搜索记忆（CLI 限定）

```bash
/memory search python
```

搜索覆盖五个字段：title / content / key_normalized / tags / keywords，
所有查询下推到 SQLite（LIKE + json_each），不会全表拉回 Python。

> **不要**期望让 AI 调用搜索：当前 LLM 没有 `search_memory` 工具。
> 取而代之，每轮对话开始时 `MemoryRetriever` 会自动对所有条目评分，把最相关的 top-k 注入到 `<memory-context>` 块；
> 你只需要正常提问，相关记忆会自己出现在模型上下文里。

### 4. 按标签过滤

```bash
/memory tag preference
```

### 5. 删除记忆（CLI 限定）

```bash
/memory delete project_name
```

> AI 同样**没有** `delete_memory` 工具——这是用户控制能力。

### 6. 查看状态

```bash
/memory status
```

输出包含：用户库 / 项目库的条目数、当前会话摘要数、本会话召回命中次数、召回错误次数和最近一次错误、`<memory-stable>` 块字符数、最近一条会话摘要的字符数。

---

## 自动召回是怎么工作的

每轮对话构建系统提示时，Agentao 会注入两个块：

- **`<memory-stable>`** — 稳定前缀，永远包含 user-scope 条目和结构化的 project 类型（`decision` / `constraint` / `workflow` / `preference` / `profile`）；`project_fact` / `note` 中最近更新的 3 条也无条件纳入。预算紧张时按 created_at 倒序保留**最新**条目，不会被陈年记忆挤掉。
- **`<memory-context>`** — 动态召回，基于当前用户消息评分得到 top-k 候选；五路索引：
  - tag 精确匹配 ×4（短查询时降权到 1.5/2.5，避免单 tag 过强）
  - title Jaccard ×3
  - 关键词 tokenized 匹配 ×2（`agent.py` 这种复合关键词会被切分成 `agent` / `py`，子 token 也能命中）
  - 内容片段（前 500 字）×1
  - 文件路径上下文 ×2
  - + 时效性加分；陈旧条目（>90 天）扣分

跨会话连续性：上一会话保存的 `session_summaries` 会通过 `get_cross_session_tail()` 注入到 `<memory-stable>` 尾部，重启后仍能看到。

---

## 常用场景

### 场景 1: 项目信息管理

```
You: 记住这个项目用 uv 管理依赖
AI: ✓ 已保存

# 稍后提问，相关记忆会自动出现在 <memory-context>
You: 这个项目怎么管理依赖的？
AI: 根据保存的偏好，使用 uv 管理依赖。
```

### 场景 2: 用户偏好

```
You: 记住我喜欢用 spaces 而不是 tabs，标签 user, preference
# user 标签 → 自动写入用户库，跨项目共享

# 查看
/memory tag preference
/memory user
```

### 场景 3: 临时笔记

```
You: 记住 API endpoint 是 https://api.example.com，标签 temp
# 用完后删除
/memory delete api_endpoint
```

### 场景 4: 审阅自动晶化的候选

LLM 调用 `save_memory` 是一种写入路径；另一种是规则 crystallizer：
对话压缩时，`MemoryCrystallizer` 会扫描原始用户消息（不会扫 LLM 生成的 summary 文本），匹配 preference / constraint / decision / workflow 模式，把候选写入**审阅队列**而非直接入库。

```bash
/memory review                         # 列出待审条目
/memory review approve <id>            # 批准 → 写入 memories，source=crystallized
/memory review reject <id>             # 拒绝
/memory crystallize                    # 手动对当前会话再跑一次 crystallize
```

晶化只读用户消息——assistant 的叙述里包含的 "I prefer X" 不会触发误报。

---

## 命令速查

```bash
# 查看
/memory                  # 列出所有
/memory list             # 同上
/memory user             # 只看用户库
/memory project          # 只看项目库
/memory session          # 查看本会话摘要
/memory status           # 数量统计 + 召回观测

# 搜索 / 过滤
/memory search <关键词>   # 跨 5 字段搜索（title/content/key/tags/keywords）
/memory tag <标签名>      # 按 tag 精确过滤

# 管理
/memory delete <key>     # 删除单条（按 title/key 匹配）
/memory clear            # 清空所有 memories + 所有 session summaries（需确认）

# 审阅 crystallization 候选
/memory crystallize      # 对当前会话再跑一次 crystallize
/memory review           # 列出待审条目
/memory review approve <id>
/memory review reject <id>
```

---

## 最佳实践

### ✅ 好的做法

1. **使用描述性的 key**
   ```
   ✓ user_python_version
   ✗ temp1
   ```

2. **用标签驱动 scope 和分类**
   ```python
   # 含 user 标签 → 用户库
   save_memory(key="preferred_editor", value="nvim",
               tags=["user", "preference"])

   # 项目级配置
   save_memory(key="api_endpoint", value="https://api.example.com",
               tags=["config", "api"])
   ```

3. **更新记忆**
   ```
   You: 更新 Python 版本偏好为 3.12
   AI: [save_memory 相同 key 自动 upsert]
   ```

### ❌ 避免的做法

1. **不要期望让 AI 调用搜索 / 删除工具**
   - 这些已经从 LLM 工具集中移除；查询走自动召回，删除走 CLI。

2. **不要保存敏感信息**
   - 密码、API key 等不要写入 memory；`MemoryGuard` 会做基础检测，但不要依赖。

3. **不要保存太长的内容**
   - memory 适合短文本（条目内容会被截断到 240 字符用于 stable block）；长内容存文件并把路径作为 keyword。

---

## 备份与迁移

存储是 SQLite 文件，备份就是复制：

```bash
# 备份
cp .agentao/memory.db .agentao/memory.db.bak
cp <home>/.agentao/memory.db <home>/.agentao/memory.db.bak

# 检查内容（需 sqlite3 客户端）
sqlite3 .agentao/memory.db "SELECT scope, type, key_normalized, title FROM memories WHERE deleted_at IS NULL;"

# 恢复
cp .agentao/memory.db.bak .agentao/memory.db
```

> 不要手动改 SQL 表结构。schema 版本由 `agentao/memory/storage.py::_SCHEMA_VERSION` 管理；后续版本会自动迁移。

---

## 疑难解答

### Q: 记忆没有保存？

A: 检查 `.agentao/memory.db` 是否可写（权限问题时会回退到 `:memory:`，重启就丢）。`/memory status` 也会显示当前条目数。

### Q: 搜索找不到记忆？

A:
- 确认关键词拼写
- 用 `/memory tag <tag>` 按标签过滤
- 用 `/memory list` 列出所有条目核对
- 搜索覆盖 5 个字段，但都是子串匹配；记得 `key_normalized` 是 snake_case

### Q: AI 没有引用我之前保存的记忆？

A:
- `/memory status` 看 "Recall hits (session)" 是否在涨；不涨说明分数都是 0
- 检查保存条目的 tags / title 是否和你的提问有 token 重叠
- 重要条目可以通过类型升级到 stable block：把 type 设为 `decision` / `preference` / `constraint` 等结构化类型（用对应的 tag 触发）
- 看 `/memory status` 里 "Recall errors" 是否有累计——出现错误时会记录 WARNING 到 `agentao.log`

### Q: `/memory delete <key>` 没删掉条目？

A:
- 删除是按 title 匹配（大小写不敏感）；先 `/memory list` 确认准确标题
- 删除是软删除（`deleted_at` 标记为非 NULL），所以 SQLite 文件大小不变；不影响 session_summaries

### Q: `/memory clear` 是不是会清掉跨会话记忆？

A: 是的。`/memory clear` 调用 `clear_all_session_summaries()`，会同时清空 `memories`（软删除）和**所有**会话的 `session_summaries`。如果只想开新会话保留长期记忆，用 `/new`。

---

## 下一步

- 📖 完整设计文档: [memory-management.md](./memory-management.md)
- 🧪 测试代码:
  - `tests/test_memory_store.py` — SQLite 层 CRUD
  - `tests/test_memory_manager.py` — facade 行为
  - `tests/test_retriever.py` — 召回评分和 tokenization
  - `tests/test_crystallizer.py` — 规则提取 + 审阅队列
  - `tests/test_memory_session.py` — 会话摘要 + 跨会话清理
  - `tests/test_memory_renderer.py` — 提示词块渲染 + 预算驱逐
- 📝 历史更新: [2024-12-28-memory-management.md](../updates/2024-12-28-memory-management.md)
