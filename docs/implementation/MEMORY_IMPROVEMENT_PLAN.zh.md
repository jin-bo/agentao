# Memory 改进计划

状态：供后续继续研究与实施设计的草案

本文档记录当前已经达成一致的 Agentao Memory 改进方案，目标是围绕现有架构做
渐进式增强，而不是一次性重写。

相关代码：

- `agentao/memory/retriever.py`
- `agentao/memory/manager.py`
- `agentao/memory/render.py`
- `agentao/memory/crystallizer.py`
- `agentao/memory/storage.py`

## 目标

1. 在不替换当前存储后端的前提下提升召回质量。
2. 修正不适合覆盖写入的 memory 类型，保留历史演化。
3. 提升 episodic memory 的提取质量，同时避免把 memory 子系统扩张成一个大型异步平台项目。
4. 在 memory 数量增长后，仍然把 prompt 体积控制在可接受范围内。
5. 将 embedding、后台 consolidation 这类高成本能力延后到低风险改动验证完成之后。

## 非目标

- 第一阶段不引入 embedding pipeline。
- 第一阶段不做 ANN / 向量索引。
- 第一阶段不做后台 consolidation worker 系统。
- 第一阶段不做完整 graph memory 设计。

## Roadmap

### P0a：稳定记忆块预算与确定性截断

问题：

- stable memory 增长会挤压系统 prompt。
- 在引入 append-only memory 类型后，这个问题会更快暴露。

要求：

- 为 `<memory-stable>` 保持硬预算。
- 让优先级规则显式且可测试。
- 在输入集合不变时保持输出稳定。

优先级：

1. `user` scope memory
2. `project` structural memory
3. incidental project memory

建议视为 structural 的类型：

- `preference`
- `profile`
- `constraint`
- `workflow`

`decision` 在 append-only 落地后需要重新评估。如果它改成 append-only，则 stable block
更适合只保留最新的、未 superseded 的 decision，而不是所有历史 decision。

实现说明：

- budget enforcement 保持在 `agentao/memory/render.py`
- selection policy 保持在 `agentao/memory/manager.py`
- renderer 侧截断逻辑与 manager 侧筛选逻辑必须保持一致，避免漂移
- P1 落地后，默认从 stable block 中排除 superseded 记录

测试要求：

- 在 `tests/test_memory_renderer.py` 中加入 golden tests
- 覆盖预算紧张时的优先级保留逻辑
- 覆盖输入不变时的稳定输出顺序
- 覆盖未来 superseded 记录与 stable block 的交互

### P0b：不替换后端的召回质量升级

问题：

- 当前召回本质上仍然是 lexical retrieval。
- 当查询词与已存 memory 词面重叠较弱时，当前打分容易失效。

范围：

- 保持现有内存倒排索引设计不变。
- 本阶段不迁移到 SQLite FTS5。

计划改动：

1. 扩展 `content` 匹配范围，不再只看前 500 字符。
2. 在现有倒排索引上加入 BM25 风格权重。
3. 保留并融合当前已有信号：
   - tag match
   - title overlap
   - keyword match
   - content match
   - filepath hint
   - recency
4. 加入具体、可实现的 entity-hint 规则：
   - 识别文件名和路径片段
   - 识别函数、类、模块标识符
   - 对这些标识符的精确命中给予 boost
5. 加入一张小型静态 alias 表：
   - 只允许有限字典
   - 不做开放式同义词系统
   - 目标规模大约为 10-50 条高价值 alias

可接受的 alias 范围示例：

- `postgres` -> `postgresql`
- `pyproject` -> `pyproject.toml`

本阶段明确不做：

- 模型生成的同义词扩展
- 基于 embedding 的 query expansion
- 动态 ontology 生成

主要文件：

- `agentao/memory/retriever.py`

### P1：为历史型 memory 引入 append-only 语义

问题：

- 当前基于 `scope + key` 的 upsert 语义，会抹掉本应保留历史的 memory 演化过程。

已达成一致的写入语义：

- 继续 upsert：
  - `preference`
  - `profile`
  - `constraint`
- 改为 append-only：
  - `decision`
  - `project_fact`
  - `note`

第一版历史模型：

- 为 `memories` 表增加 `is_superseded` 字段
- 默认查询自动过滤 `is_superseded = 0`
- 之后 CLI 若要显示历史，可通过显式入口绕过该过滤

优先选择 `is_superseded` 而不是 `supersedes` 的原因：

- migration 成本更低
- 查询逻辑更简单
- 足以支撑“当前有效记录 vs 历史记录”的需求

Schema 说明：

- 本阶段需要 schema migration
- migration 必须同时覆盖：
  - project store：`.agentao/memory.db`
  - user store：`<home>/.agentao/memory.db`
- user store 可能不存在，必须安全跳过
- migration 应在 `SQLiteMemoryStore` 初始化时幂等执行

示例 migration 形态：

```sql
ALTER TABLE memories ADD COLUMN is_superseded INTEGER NOT NULL DEFAULT 0;
```

主要文件：

- `agentao/memory/manager.py`
- `agentao/memory/storage.py`
- `agentao/memory/models.py`

### P2：LLM 结构化 episodic 提取 + 确定性的 review queue 入库

问题：

- 单纯依赖 regex 的 crystallization 对强信号偏好/决策句式还行，但对 episodic knowledge 的覆盖很弱。

已达成一致的设计：

- semantic understanding 交给 summarization 阶段的 LLM
- crystallizer 负责确定性的解析、归一化、分类和 review queue 提交

换句话说：

- LLM 负责提取
- crystallizer 负责归一化
- review queue 负责 promotion 前的闸门

计划改动：

1. 扩展 session summarization prompt，使其可以输出结构化 memory block
2. 在 summarization 之后解析这些 block
3. 将解析结果转换为 review candidate
4. 本阶段不做自动直接 promotion 到 live memory

协议要求：

- 在编码前先定义稳定的结构化输出协议

建议方向：

- 使用带标签的 block 或其他 parser-friendly 的结构化文本
- 不要把松散 markdown prose 作为主要交换格式

示意形态：

```text
<episode>
type: workaround
title: uv lock mismatch after python upgrade
context: ...
resolution: ...
confidence: high
</episode>
```

协议至少要定义：

- block 类型集合
- 必填字段
- 可选字段
- 每个字段的长度上限
- parse 失败时的降级行为

建议的 parse-failure 行为：

- 不直接写入 live memory
- 可以降级为 review queue 中的 raw-note candidate
- 绝不让解析失败的脏数据静默污染长期稳定记忆

主要文件：

- `agentao/context_manager.py`
- `agentao/memory/crystallizer.py`
- `agentao/memory/manager.py`

### P3：显式的只读 Memory Recall Tool

问题：

- 当前 agent 只能依赖自动 prompt 注入，无法在自动召回不足时显式查询 memory。

状态：

- 有意后移，等 P0-P2 稳定后再做

建议工具名：

- `recall_memory`

选择这个名字的原因：

- 与现有 `save_memory` 更自然配对
- 比通用的 `search_memory` 更符合系统语义

工具边界：

- 只读
- 不提供 delete
- 不提供 clear
- 不向 LLM 暴露不受限的 memory 管理能力

description 应明确说明：

- 自动 recall 已经会把相关 memory 注入 prompt
- 显式 recall 只在以下场景使用：
  - 用户显式问“你记得什么”
  - 需要跨 session 上下文
  - query 很短或信息不足
  - 需要带过滤条件的 scoped 查询

可能支持的过滤项：

- `scope`
- `type`
- `tag`
- time window

### P4：重新评估 Embedding 与后台 Consolidation

本阶段刻意后移。

只有在 P0-P2 完成并经过验证之后，才重新评估是否需要做这一层。

未来阶段需要回答的问题：

- lexical + BM25 风格检索是否仍然明显不足
- embedding 应该本地生成还是远程调用
- review queue 的 dedupe 是否需要 semantic similarity
- 是否真的存在足够大的痛点，值得引入后台 consolidation

## 设计约束

### 确定性

- 选择与渲染逻辑应尽可能保持确定性
- semantic understanding 可以交给 LLM，但写入链路必须保持可审计、可解释

### 低迁移风险

- 优先选择小而加法式的 schema 变更
- 优先选择启动时幂等 migration
- 避免要求用户手工升级本地数据库

### Prompt 安全

- memory block 仍然是 data，不是 instruction
- stable memory 的增长必须始终有上限

## 测试矩阵

### P0a

- 预算紧张时 stable block 的优先级保留
- 多次 render 时输出稳定
- cross-session summary 与 stable fact 共存
- P1 落地后 superseded 记录不会进入 stable block

### P0b

- 文件名、函数名、类名、模块名的 identifier boost
- alias 表扩展行为
- BM25 风格权重的回归测试
- 对低词面重叠但仍属 lexical 范围的 query 的召回质量测试

### P1

- project DB 的幂等 migration
- user DB 的幂等 migration
- 缺失 user DB 时初始化不失败
- 历史型 memory 的 append-only 写入行为
- 当前状态型 memory 的 upsert 行为不变
- superseded 记录默认不出现在读取结果里

### P2

- parser 能接受合法的结构化 summary block
- parser 能安全拒绝不合法 block
- parse 失败不会写入 live memory
- 结构化 episode 能正确进入 review queue

### P3

- 工具保持只读
- 过滤语义正确
- tool description 会引导稀疏、显式调用，而不是滥用

## 下一步

基于 `P0a`、`P0b`、`P1`、`P2`、`P3`，继续输出一份更细的 implementation proposal，至少包含：

- 具体 schema diff
- 结构化 summary 协议
- migration 策略
- 受影响测试列表
- rollback 考量
