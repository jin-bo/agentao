# Skill Crystallization Refine Implementation Plan

## Goal

在现有 `/crystallize suggest|create` 基础上，增加一个显式的 `refine` 阶段，让 skill draft 可以复用随 `agentao` 发布的内置 `skill-creator` 经验做增强，但不把这套较重的 workflow 绑进 memory crystallization 主路径。

目标效果：

- 保持 `suggest` 轻量、快速、低成本
- 保持 `create` 语义纯粹，只负责落盘
- 新增 `refine`，把 `skill-creator` 用作 draft 增强器
- 不修改 `MemoryCrystallizer` 的职责边界

## Current State

当前相关实现分成两条链路：

- `MemoryCrystallizer`：规则驱动，只扫描原始用户消息，把候选写入 review queue，不直接写入 live memory
- `SkillCrystallizer`：当前只负责把已有 `SKILL.md` 内容写入 skills 目录
- `/crystallize suggest`：通过一个轻量 LLM prompt，从当前会话生成单个 skill draft
- `/crystallize create [name]`：将生成出的 skill 直接写入目录

当前代码位置：

- [`agentao/memory/crystallizer.py`](../../agentao/memory/crystallizer.py)
- [`agentao/cli/commands_ext.py`](../../agentao/cli/commands_ext.py)
- [`agentao/skills/manager.py`](../../agentao/skills/manager.py)
- bundled skill: [`skills/skill-creator/SKILL.md`](../../skills/skill-creator/SKILL.md)

## Design Decision

采用三段式流程：

```bash
/crystallize suggest
/crystallize refine
/crystallize create [name]
```

同时补两个辅助命令：

```bash
/crystallize status
/crystallize clear
```

不采用下列设计：

- 不把 `skill-creator` 接入 `MemoryCrystallizer`
- 不让 `/crystallize create` 隐式执行 refine
- 不把 skill draft 放进 memory review queue
- 不在 `/crystallize` 中自动跑 `skill-creator` 的完整 eval workflow

原因：

- memory crystallization 目标是保守、可解释、低误报
- `skill-creator` 是较重的 skill authoring / optimization workflow
- 这两条链路的成本模型和职责边界不同，强耦合会让系统变得更慢、更难控

## Proposed UX

### 1. `/crystallize suggest`

行为：

- 从当前 session transcript 生成一个最小可用的 `SKILL.md` draft
- 输出 draft 到终端
- 将 draft 保存为当前项目的 pending draft

成功后提示：

```text
Draft saved.
Use /crystallize refine to improve it with skill-creator.
Use /crystallize create [name] to save it.
```

若没有明显的可复用模式：

```text
No clear repeatable skill pattern found in the current session.
```

### 2. `/crystallize refine`

行为：

- 读取 pending draft
- 读取当前 session transcript 的截断片段
- 读取 bundled `skill-creator` guidance
- 让模型输出改进后的完整 `SKILL.md`
- 覆盖保存 pending draft，并记录该 draft 已经过 `skill-creator` 风格 refine

如果没有 pending draft：

```text
No pending skill draft. Run /crystallize suggest first.
```

### 3. `/crystallize create [name]`

行为：

- 读取 pending draft
- 可选使用传入的 `name` 覆写 frontmatter 中的 `name:`
- 将 draft 写入目标 skills 目录
- 成功后清除 pending draft

这一步不再隐式生成或优化内容，只负责最终持久化。

### 4. `/crystallize status`

行为：

- 显示当前是否存在 pending draft
- 若存在，显示：
  - suggested name
  - source
  - refined_with
  - updated_at

### 5. `/crystallize clear`

行为：

- 删除当前项目的 pending draft
- 若不存在则输出无 pending draft

## Draft Storage

### Storage Location

新增一个项目级 draft 文件：

```text
<project-root>/.agentao/crystallize/skill_draft.json
```

不放全局目录，原因：

- `/crystallize` 明显依赖当前仓库 / 当前会话上下文
- 项目级存储符合现有 `SkillManager` 对 project-scoped state 的处理习惯
- 避免跨项目 draft 污染

### Draft Schema

第一版建议使用轻量 JSON：

```json
{
  "session_id": "sess_123",
  "created_at": "2026-04-14T12:00:00",
  "updated_at": "2026-04-14T12:05:00",
  "source": "suggest",
  "refined_with": null,
  "suggested_name": "python-testing",
  "content": "---\nname: python-testing\ndescription: ...\n---\n..."
}
```

说明：

- `source` 初始值为 `suggest`
- `refined_with` 在 refine 成功后变为 `"skill-creator"`
- `suggested_name` 便于 `status` 和 `create` 使用
- `content` 存完整 `SKILL.md`

第一版不额外存完整 transcript，避免无必要的重复与膨胀。

## Module Layout

### New Module

新增：

- [`agentao/skills/drafts.py`](../../agentao/skills/drafts.py)

职责：

- skill draft 的项目级持久化
- draft 的读取 / 覆盖 / 删除
- 少量 frontmatter helper

建议接口：

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

@dataclass
class SkillDraft:
    session_id: str
    created_at: str
    updated_at: str
    source: str
    refined_with: Optional[str]
    suggested_name: str
    content: str

def get_skill_draft_path(working_directory: Path | None = None) -> Path: ...
def save_skill_draft(draft: SkillDraft, working_directory: Path | None = None) -> Path: ...
def load_skill_draft(working_directory: Path | None = None) -> Optional[SkillDraft]: ...
def clear_skill_draft(working_directory: Path | None = None) -> bool: ...
def extract_skill_name(skill_md: str) -> str | None: ...
def replace_skill_name(skill_md: str, new_name: str) -> str: ...
```

### Existing Modules

#### `agentao/memory/crystallizer.py`

保留现有 `MemoryCrystallizer` 不变。

对于 skill crystallization，只做增量扩展：

- 保留现有 `SUGGEST_SYSTEM_PROMPT`
- 保留现有 `suggest_prompt(...)`
- 新增 `REFINE_SYSTEM_PROMPT`
- 新增 `refine_prompt(...)`

`SkillCrystallizer` 继续只承担“把 skill 文本写到目标目录”的职责，不负责 draft 状态管理。

#### `agentao/cli/commands_ext.py`

扩展 `/crystallize` 子命令分发：

- `suggest`
- `refine`
- `create`
- `status`
- `clear`

CLI 层负责：

- 调用 LLM
- 读写 draft store
- 错误提示与命令输出

## Prompting Strategy

### Suggest Prompt

`/crystallize suggest` 继续使用当前轻量 prompt。

要求：

- 输出只能是完整 `SKILL.md`
- 若没有模式则输出 `NO_PATTERN_FOUND`
- 保持“从 transcript 中提炼单个最有价值 pattern”的定位

### Refine Prompt

`/crystallize refine` 使用新的受控 prompt，而不是“激活 skill 后自由发挥”。

建议系统提示语义：

- 你在优化一个已有 Agentao skill draft
- 可以参考 bundled `skill-creator` 中关于：
  - description 触发性
  - when to use
  - steps 结构
  - skill 写作风格
- 必须保留 draft 原始 intent
- 不要引入 transcript 不支持的新能力
- 如果 draft 已经足够好，只做最小改动
- 输出只能是完整、合法的 `SKILL.md`

建议用户 prompt 提供三块输入：

1. current draft
2. recent transcript excerpt
3. selected `skill-creator` guidance excerpt

注意：

- 不要把完整 `skills/skill-creator/SKILL.md` 原样塞入 prompt
- 只取与 draft 编写、description、steps、test case guidance 直接相关的片段
- transcript 继续沿用现有截断策略，例如最近 3000 到 5000 字符

## Command Semantics

### `/crystallize suggest`

流程：

1. 读取当前 session transcript
2. 调用现有 suggest prompt
3. 校验输出
4. 提取 frontmatter 中的 `name`
5. 写入 draft store
6. 输出结果和下一步提示

### `/crystallize refine`

流程：

1. 加载 draft
2. 若不存在则报错
3. 读取 transcript excerpt
4. 读取 `skill-creator` 指导片段
5. 调用 refine prompt
6. 校验输出是否为合法 skill 文本
7. 覆盖保存 draft，并设置 `refined_with="skill-creator"`
8. 输出 refined 结果

失败时：

- 保留旧 draft，不覆盖

### `/crystallize create [name]`

流程：

1. 加载 draft
2. 若传入 `name`，覆写 frontmatter 的 `name:`
3. 调用 `SkillCrystallizer.create(...)`
4. 成功后清除 draft
5. 提示写入路径

### `/crystallize status`

输出建议：

```text
Pending skill draft:
  name: python-testing
  source: suggest
  refined_with: skill-creator
  updated_at: 2026-04-14T12:05:00
```

### `/crystallize clear`

输出建议：

```text
Pending skill draft cleared.
```

或：

```text
No pending skill draft.
```

## Frontmatter Handling

`create [name]` 需要可靠修改 frontmatter 中的 `name:`。

建议通过专用 helper 处理 YAML frontmatter 第一段，而不是用简单正则硬替整篇文本。

最小要求：

- 能提取 `name:`
- 能在保留其他 frontmatter 字段的前提下替换 `name:`
- 若没有 frontmatter，则返回明确错误

## Testing Plan

建议补充三类测试。

### 1. Draft Storage Tests

新增：

- `tests/test_skill_drafts.py`

覆盖：

- save / load / clear 正常工作
- draft 文件写到项目级 `.agentao/crystallize/`
- `extract_skill_name(...)` 正常提取
- `replace_skill_name(...)` 正常替换

### 2. Crystallizer Tests

扩展：

- `tests/test_crystallizer.py`

覆盖：

- `REFINE_SYSTEM_PROMPT` 存在并约束输出为完整 `SKILL.md`
- `refine_prompt(...)` 会包含 draft 与 transcript excerpt
- refine 失败时不应覆盖旧 draft

### 3. CLI / Integration Tests

扩展：

- `tests/test_skill_cli.py`
- 或 `tests/test_skill_integration.py`

覆盖：

- `/crystallize suggest` 会保存 draft
- `/crystallize status` 能显示 draft 元信息
- `/crystallize clear` 删除 draft
- `/crystallize refine` 在无 draft 时报错
- `/crystallize refine` 成功后 `refined_with == "skill-creator"`
- `/crystallize create foo-bar` 会覆写 frontmatter name 并写入正确目录
- `/crystallize create` 成功后 draft 被清除

## Recommended Implementation Order

建议按以下顺序落地：

1. 新增 `agentao/skills/drafts.py`
2. 让 `/crystallize suggest` 自动保存 pending draft
3. 实现 `/crystallize status`
4. 实现 `/crystallize clear`
5. 新增 `REFINE_SYSTEM_PROMPT` 和 `refine_prompt(...)`
6. 实现 `/crystallize refine`
7. 改造 `/crystallize create [name]` 从 draft 读取并支持 name override
8. 补测试

## Explicit Non-Goals

第一版不做：

- skill draft 持久化到 SQLite memory
- 把 skill draft 走 memory review queue
- 自动运行 `skill-creator` 的 benchmark / eval / iteration workflow
- `/crystallize create` 隐式调用 refine
- 跨项目共享 pending draft

## Rationale Summary

这个方案的核心是把 `skill-creator` 放在 skill crystallization 的第二阶段，而不是 memory crystallization 的底层依赖。

这样可以同时保留两种系统特性：

- memory crystallization 继续保守、稳定、低成本
- skill crystallization 获得一个显式、可控、可演进的增强阶段

对现有代码的侵入面也最小：

- `MemoryCrystallizer` 不动
- `SkillCrystallizer` 仍然保持简单
- 主要变更集中在 CLI 路由、prompt 设计和一个很小的 draft store
