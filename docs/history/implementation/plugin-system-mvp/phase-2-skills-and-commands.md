# Phase 2: Skills And Commands

## Goal

把插件中的 `skills` 和 `commands` 接入 Agentao 的 skill/prompt 运行时，使 Claude-compatible prompt assets 能在 Agentao 中被发现、列出并激活。

## Why This Phase Exists

`skills` 和 `commands` 是 Claude-style plugins 中最直接、最常用、风险也最低的一类能力。先把它们接入，可以尽快验证 plugin packaging 层是否真的能映射到 Agentao runtime。

## Scope

本阶段包含：

- plugin `skills/` 目录加载
- manifest `skills` 路径加载
- plugin `commands/` markdown 发现
- manifest `commands` 路径和 object mapping 解析结果接入
- plugin prompt assets 映射为 Agentao skill-like entries
- namespacing
- collision diagnostics

本阶段不包含：

- agents
- MCP
- hooks
- tool lifecycle
- CLI diagnostics polish

## Dependencies

本阶段依赖 Phase 1 已完成：

- `LoadedPlugin`
- path-safe manifest parsing
- discovery / precedence
- diagnostics snapshot

## Runtime Contract

- plugin `skills/` 按 Agentao skill source 接入
- plugin `commands/` 统一映射为 skill-like prompt entries
- plugin 内部产出的 runtime name 必须稳定
- plugin 内部 collision 为 fatal
- plugin 与现有 skill 同名 collision 为 fatal
- 不做 silent override

## Naming Rules

建议默认 namespacing：

- skills:
  - `plugin_name:skill_name`
- commands:
  - `plugin_name:command_name`

示例：

- `code-review-assistant:review-summary`
- `code-review-assistant:release-check`

## Input Forms

`skills` 支持：

- 默认 `skills/`
- manifest `skills`

`commands` 支持：

- 默认 `commands/*.md`
- manifest `commands` path
- manifest `commands` mapping

对象映射示例：

```json
{
  "commands": {
    "review-summary": {
      "source": "./commands/review-summary.md",
      "description": "Summarize the current patch"
    },
    "release-check": {
      "content": "# Release Check\n\nValidate release readiness"
    }
  }
}
```

## Proposed Runtime API

```python
class SkillManager:
    def register_plugin_skills(
        self,
        plugin: LoadedPlugin,
        entries: list["PluginSkillEntry"],
    ) -> None: ...
```

建议新类型：

```python
@dataclass
class PluginSkillEntry:
    runtime_name: str
    plugin_name: str
    source_kind: Literal["plugin-skill", "plugin-command"]
    source_path: Path | None
    content: str | None
    description: str | None = None
    argument_hint: str | None = None
    model: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
```

## Discovery Rules

`skills`：

- 扫描 `skills/**/SKILL.md`
- 使用 plugin metadata 打标

`commands`：

- 扫描 `commands/*.md`
- 对象映射中的 `content` 可直接生成内存 entry
- 对象映射中的 `source` 指向 markdown 文件

## Collision Rules

- 同一 plugin 内 skill/command runtime 名冲突：fatal
- plugin skill 与现有 built-in/project skill 重名：fatal
- 同名 plugin 不在本阶段处理，必须在 Phase 1 已经完成原子覆盖

## Fixture Coverage

建议主要使用：

- `skills-only-plugin`
- `commands-only-plugin`
- `skills-and-commands-collision-plugin`
- `full-plugin`

## Lifecycle Sequence

```text
PluginManager.load_plugins()
  -> for each LoadedPlugin
  -> collect skill roots and command definitions
  -> convert to PluginSkillEntry list
  -> validate namespacing and collisions
  -> SkillManager.register_plugin_skills(...)
```

## Issue Backlog

1. 为 SkillManager 增加 plugin registration seam
2. 实现 `skills/` 扫描和 metadata 打标
3. 实现 `commands/` markdown 到 skill entry 的映射
4. 实现 mapping-format commands 到内存 skill entry 的映射
5. 实现 collision diagnostics
6. 编写 skills/commands 集成测试

## Tests

- `skills/` 中 `SKILL.md` 成功注入
- `commands/*.md` 成功映射
- mapping-format `commands` 成功映射
- namespaced runtime name 正确
- source metadata 正确
- skill/command collision correctly fails

## Acceptance Criteria

1. plugin `skills` 能出现在 skill list
2. plugin `commands` 能作为 skill-like entries 激活
3. runtime names 稳定且可诊断
4. collision 报 clear error
5. 不破坏现有 built-in/project skills

## Out Of Scope

- agents
- MCP
- hook dispatch
- `UserPromptSubmit`

## Related Issues

- [06 Plugin Skills Registration Seam](issues/06-plugin-skills-registration-seam.md)
- [07 Plugin Commands Mapping](issues/07-plugin-commands-mapping.md)
