# Skill Crystallize Enhancement Plan

## Goal

在现有 `/crystallize suggest|refine|create` 基础上，扩展一版更完整的 skill crystallization 工作流，解决三个问题：

1. `/crystallize` 生成 skill 时纳入必要的 LLM 返回和 Tool 返回，而不是只看纯会话文本。
2. `/crystallize` 过程中支持用户通过会话持续提供修改建议，并驱动 draft 迭代。
3. `/help` 明确展示 `/crystallize` 的完整用法和推荐流程。

目标效果：

- 生成出的 skill 更接近真实完成任务时的操作路径，而不是对聊天内容的复述。
- 用户可以在 draft 生成后继续“纠偏”，而不是只能被动接受 `suggest/refine` 的结果。
- `/crystallize` 的能力和推荐用法可以直接从 CLI 发现，不依赖 README 或源码阅读。

## Current State

当前 `/crystallize` 的实现特点：

- `_collect_session_content()` 只拼接 session summary 和 `user/assistant` 文本。
- `assistant.tool_calls` 和 `role="tool"` 的结果虽然保存在消息历史中，但 `/crystallize` 没有使用。
- `SkillDraft` 当前只保存：
  - `session_id`
  - `created_at`
  - `updated_at`
  - `source`
  - `refined_with`
  - `suggested_name`
  - `content`
- `/crystallize` 只有 `suggest|refine|create|status|clear`，还没有用户 feedback 的明确入口。
- `/help` 需要明确展示 `/crystallize` 的完整用法，而不仅依赖命令补全。

当前相关代码位置：

- `agentao/cli/commands_ext.py`
- `agentao/skills/drafts.py`
- `agentao/memory/crystallizer.py`
- `agentao/cli/_utils.py`

## Design Principles

### 1. 保持职责边界清晰

- `MemoryCrystallizer` 继续只负责 memory crystallization，不引入 skill authoring 的复杂逻辑。
- `/crystallize create` 保持“最终落盘”的纯语义。
- 用户 feedback 和 skill-authoring guidance 是两种不同输入，不应混成同一个概念。

### 2. 优先结构化 evidence，而不是堆原始 transcript

- 不直接把所有消息、所有工具输出完整喂给模型。
- 先做 evidence 收集和摘要，再用于 `suggest` / `feedback` / `refine`。

### 3. 保守迭代

第一版只做：

- tool call / tool result evidence 纳入
- feedback 驱动的 draft 重写
- help 与补全增强

不做：

- 自动 diff draft 修改建议
- 自动评估 skill 质量
- 完整 skill-creator eval workflow 融入 `/crystallize`

## Proposed UX

建议 `/crystallize` 工作流扩展为：

```bash
/crystallize
/crystallize suggest
/crystallize feedback <text>
/crystallize revise
/crystallize refine
/crystallize status
/crystallize clear
/crystallize create [name]
```

### `/crystallize`

等价于 `/crystallize suggest`。

### `/crystallize suggest`

行为：

- 收集当前会话的结构化 evidence
- 基于 evidence 生成初版 `SKILL.md`
- 保存 pending draft

### `/crystallize feedback <text>`

行为：

- 给当前 draft 追加一条用户修改意见
- 基于 `draft + evidence + feedback_history` 重新生成完整 draft

用途：

- “不要写成 pytest 专用，改成更通用的测试 skill”
- “把 shell 步骤强调得更明确”
- “这个 skill 应该偏项目级，不要写成个人偏好”

### `/crystallize revise`

行为：

- 在 CLI 中交互式输入一段修改意见
- 内部复用 `feedback` 逻辑

### `/crystallize refine`

行为：

- 读取当前 draft
- 读取 structure evidence 摘要
- 读取 bundled `skill-creator` guidance
- 输出更完整、表达更成熟的 `SKILL.md`

说明：

- `refine` 侧重“作者经验增强”
- `feedback` 侧重“用户定制纠偏”

### `/crystallize status`

行为：

- 显示当前 pending draft 的基础信息
- 增加 feedback / evidence 的统计

建议显示：

- `name`
- `source`
- `refined_with`
- `updated_at`
- `feedback_count`
- `tool_call_count`
- `tool_result_count`
- `workflow_step_count`

### `/crystallize clear`

行为：

- 清除当前 pending draft

### `/crystallize create [name]`

行为：

- 读取当前 draft
- 可选使用 `name` 覆盖 frontmatter 中的 `name:`
- 将 draft 写入 skills 目录
- 成功后清除 pending draft

兼容策略：

- 保留当前 one-shot fallback：当不存在 draft 时，`create` 仍可从当前会话即时生成并保存
- 但 CLI 文案应提示推荐走：
  - `suggest`
  - `feedback` / `refine`
  - `create`

## Evidence Model

建议新增结构化 evidence 模型。

```python
@dataclass
class SkillEvidence:
    user_goals: list[str]
    assistant_conclusions: list[str]
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    key_files: list[str]
    workflow_steps: list[str]
    outcome_signals: list[str]
```

### Evidence Sources

#### 1. User messages

提取：

- 用户目标
- 约束
- 偏好
- 任务成功标准

#### 2. Assistant messages

提取：

- 结论性解释
- 明确的 workflow 建议
- 最终的总结性输出

说明：

- 不应盲目保留所有 reasoning
- 只保留与 skill 可复用性直接相关的文本

#### 3. Assistant tool calls

来源：

- `assistant` message 中的 `tool_calls`

提取：

- tool 名称
- 参数摘要
- 调用顺序

用途：

- 还原 workflow
- 辅助识别技能的核心操作模式

#### 4. Tool result messages

来源：

- `role="tool"` 消息

提取：

- 结果摘要
- 成功 / 错误信号
- 关键 artifact path
- 关键输出片段

说明：

- 长输出只保留 excerpt 和路径
- 不把整个 tool output 原样塞入 prompt

#### 5. Outcome signals

提取：

- 修改了哪些关键文件
- 是否写出了某个产物
- 是否通过了测试
- 是否生成了可复用脚本 / 命令 / 配置

## Draft Schema Changes

建议扩展 `SkillDraft`。

```python
@dataclass
class SkillFeedbackEntry:
    author: str
    content: str
    created_at: str


@dataclass
class SkillDraft:
    session_id: str
    created_at: str
    updated_at: str
    source: str
    refined_with: str | None
    suggested_name: str
    content: str
    evidence: SkillEvidence
    feedback_history: list[SkillFeedbackEntry]
    open_questions: list[str]
```

对应 JSON 示例：

```json
{
  "session_id": "sess_x",
  "created_at": "2026-04-21T10:00:00",
  "updated_at": "2026-04-21T10:05:00",
  "source": "suggest",
  "refined_with": "skill-creator",
  "suggested_name": "python-testing",
  "content": "---\nname: python-testing\n...\n",
  "evidence": {
    "user_goals": [],
    "assistant_conclusions": [],
    "tool_calls": [],
    "tool_results": [],
    "key_files": [],
    "workflow_steps": [],
    "outcome_signals": []
  },
  "feedback_history": [
    {
      "author": "user",
      "content": "不要写成 pytest 专用，改成通用测试 skill",
      "created_at": "2026-04-21T10:03:00"
    }
  ],
  "open_questions": []
}
```

兼容要求：

- 老 draft JSON 依然可读取
- 缺失的新字段使用默认值回填

## Prompt Design

建议拆成三类 prompt。

### 1. `suggest_prompt(evidence_text)`

输入：

- 结构化 evidence 摘要

输出：

- 最小可用 `SKILL.md`

约束：

- 必须基于 evidence
- 不得虚构不存在的工具或流程
- 偏通用、可复用、低耦合

### 2. `feedback_prompt(draft_content, evidence_text, latest_feedback, feedback_history_text)`

输入：

- 当前 draft
- 结构化 evidence
- 最新用户 feedback
- 历史 feedback

输出：

- 重写后的完整 `SKILL.md`

约束：

- 优先满足最新用户 feedback
- 与 evidence 冲突时，不得臆造事实
- 必要时可在文档中保留 assumptions / notes

### 3. `refine_prompt(draft_content, evidence_text, guidance)`

输入：

- 当前 draft
- 结构化 evidence
- `skill-creator` guidance

输出：

- 更成熟、表达更清晰的完整 `SKILL.md`

说明：

- `refine` 不代替 `feedback`
- 两者分别服务于不同的优化目标

## CLI Integration

### New helpers

建议新增：

- `collect_crystallize_evidence(cli) -> SkillEvidence`
- `render_crystallize_context(evidence, draft_content=None, feedback_history=None) -> str`
- `append_skill_feedback(draft, text, author="user") -> SkillDraft`
- `summarize_draft_status(draft) -> dict`

### Command behavior

#### `suggest`

1. 收集 evidence
2. 生成 evidence 摘要文本
3. 调用 LLM 生成 draft
4. 将 draft 和 evidence 一起保存

#### `feedback`

1. 加载 draft
2. 校验 feedback 非空
3. 追加 `feedback_history`
4. 基于 `draft + evidence + feedback` 调用 LLM
5. 保存新 draft

#### `revise`

1. 加载 draft
2. 交互式输入 feedback
3. 内部复用 `feedback` 逻辑

#### `refine`

1. 加载 draft
2. 读取 evidence 摘要
3. 读取 `skill-creator` guidance
4. 调用 LLM 输出 refined draft

#### `status`

显示：

```text
Pending skill draft:
  name: python-testing
  source: suggest
  refined_with: skill-creator
  updated_at: 2026-04-21T10:20:00
  feedback_count: 2
  tool_call_count: 5
  tool_result_count: 5
  workflow_step_count: 4
```

## Help and Discoverability

`/help` 需要明确列出 `/crystallize` 用法：

```text
/crystallize                     Draft a skill from the current session
/crystallize suggest             Analyze the current session and generate a skill draft
/crystallize feedback <text>     Add feedback and rewrite the current skill draft
/crystallize revise              Interactively enter feedback and rewrite the draft
/crystallize refine              Improve the current draft with skill-creator guidance
/crystallize status              Show current pending draft status
/crystallize clear               Clear the current pending draft
/crystallize create [name]       Save the draft into skills/ and reload skills
```

建议增加推荐流程说明：

```text
Recommended flow:
  /crystallize suggest
  /crystallize feedback <text>   (optional, repeatable)
  /crystallize refine            (optional)
  /crystallize create [name]
```

还需要同步更新：

- `agentao/cli/_utils.py`
  - `_SLASH_COMMANDS`
  - `_SLASH_COMMAND_HINTS`
- `README.md`
- `README.zh.md`
- 可选：`docs/QUICK_REFERENCE.md`

## File-Level Change List

### `agentao/cli/commands_ext.py`

- 保留 `_collect_session_content()` 用于兼容旧逻辑
- 新增 `collect_crystallize_evidence()`
- 新增 `feedback` / `revise` 子命令
- 扩展 `status` 输出
- 调整 `suggest` / `refine` 使用 evidence

### `agentao/skills/drafts.py`

- 扩展 `SkillDraft`
- 新增 `SkillEvidence` / `SkillFeedbackEntry`
- 支持新 schema 的读写
- 提供 feedback append helper
- 保证向后兼容

### `agentao/memory/crystallizer.py`

- 保持 `MemoryCrystallizer` 职责不变
- 增加新的 prompt builder：
  - `feedback_prompt`
  - 更新后的 `suggest_prompt`
  - 更新后的 `refine_prompt`

### `agentao/cli/_utils.py`

- 增加：
  - `/crystallize feedback`
  - `/crystallize revise`
- 增加参数提示：
  - `/crystallize feedback`: `<text>`

### Help rendering code

- 在 `/help` 的输出中加入完整 `/crystallize` 文案

### Documentation

- 更新 `README.md`
- 更新 `README.zh.md`
- 如有命令速查文档，也同步更新

## Test Plan

### Evidence collection

- 能从 `assistant.tool_calls` 提取 tool 名和参数摘要
- 能从 `role="tool"` 消息提取结果摘要
- 超长 tool 输出不会被完整塞入 prompt

### Draft persistence

- 新 schema 能正常读写
- 旧 draft 能回填默认字段并正常加载
- `feedback_history` 可追加并持久化

### CLI commands

- 无 draft 时 `/crystallize feedback` 给出明确提示
- `/crystallize feedback <text>` 能触发 draft 重写
- `/crystallize revise` 能交互式输入 feedback
- `/crystallize status` 能显示 feedback/evidence 计数

### Help and completion

- `/help` 输出包含完整 `/crystallize` 用法
- `_SLASH_COMMANDS` 包含新子命令
- `_SLASH_COMMAND_HINTS` 包含 `feedback <text>`

### Compatibility

- 旧流程 `suggest -> refine -> create` 仍然可用
- 直接 `create` 的 fallback 不崩

## Recommended Rollout

### Phase 1

- 扩展 draft schema
- 新增 evidence builder
- `suggest` / `status` 接入 evidence
- `/help` 和补全更新

### Phase 2

- 新增 `/crystallize feedback <text>`
- 支持 feedback 驱动的 draft 重写
- 更新状态展示

### Phase 3

- 新增 `/crystallize revise`
- 优化 evidence 压缩和 prompt 质量
- README 与相关文档全面同步

## Acceptance Criteria

满足以下条件即可认为这轮增强完成：

- `/crystallize suggest` 生成的 skill 能反映真实工具使用流程
- 用户可以通过 `/crystallize feedback ...` 连续修订 draft
- `/crystallize status` 能显示 feedback 和 evidence 摘要
- `/help` 中可以直接看到 `/crystallize` 的完整用法
- 现有 `suggest/refine/create` 工作流不回归
