# Agentao Plugin System MVP Staged Plan

这组文档把原始总设计稿拆成可分阶段实施的版本。

设计母稿仍保留在：

- `docs/history/implementation/plugin-system-mvp-plan.md`

阶段化文档的目标是：

- 每个文件都能单独阅读，不依赖其它阶段文件才能理解
- 每个文件都对应一个明确的实施阶段
- 每个文件内部都包含目标、范围、设计、测试、验收和非目标

## Stage Index

1. [Phase 1: Manifest And Loader](phase-1-manifest-and-loader.md)
2. [Phase 2: Skills And Commands](phase-2-skills-and-commands.md)
3. [Phase 3: Agents](phase-3-agents.md)
4. [Phase 4: MCP Integration](phase-4-mcp-integration.md)
5. [Phase 5: UserPromptSubmit And Hook Core](phase-5-user-prompt-submit-and-hook-core.md)
6. [Phase 6: Session Tool Hooks And CLI Diagnostics](phase-6-session-tool-hooks-and-cli.md)

## Issue Index

- [Issue Files](issues/README.md)

## Shared MVP Contract

所有阶段默认继承以下总约束：

- 插件来源分三层：
  - `<home>/.agentao/plugins`
  - `<project-root>/.agentao/plugins`
  - `--plugin-dir`
- 插件唯一身份使用 `plugin.json.name`
- 同名插件按 global < project < inline 原子覆盖
- 支持的 Claude-compatible 组件：
  - `skills`
  - `commands`
  - `agents`
  - `mcpServers`
  - `hooks`
- `commands` 映射为 Agentao skill/prompt 能力
- `UserPromptSubmit` 支持 `command` 和 `prompt`
- 其它受支持 hook 事件只支持 `command`
- 已知但不支持的能力记录 warning，不 silently ignore

## Recommended Rollout Order

1. Phase 1
2. Phase 2
3. Phase 3
4. Phase 4
5. Phase 5
6. Phase 6

原因：

- 先稳定 schema、discovery 和 registration seam
- 再接 skills / agents / MCP 这些相对低风险能力
- 最后进入最复杂的 hook 与消息注入路径
