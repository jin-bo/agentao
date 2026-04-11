# Agentao Plugin System MVP

## Status

这份文件现在作为插件系统设计的总纲和入口页，不再承载全部实施细节。

canonical 文档已经拆分到：

- staged docs:
  - `docs/implementation/plugin-system-mvp/`
- issue docs:
  - `docs/implementation/plugin-system-mvp/issues/`

后续设计更新应优先落到 staged docs 和 issue docs，避免母稿、阶段文档、issue 文档三处漂移。

## Canonical Entry Points

总入口：

- [plugin-system-mvp/README.md](plugin-system-mvp/README.md)

issue 入口：

- [plugin-system-mvp/issues/README.md](plugin-system-mvp/issues/README.md)

## MVP Summary

目标是在 Agentao 中建立一个 Claude Code compatible 的 plugin host，使本地 Claude-style plugins 能以受控、可诊断、可分阶段扩展的方式运行。

核心边界：

- 插件来源：
  - `<home>/.agentao/plugins`
  - `<project-root>/.agentao/plugins`
  - `--plugin-dir`
- 插件 identity 使用 `plugin.json.name`
- precedence 使用 global < project < inline 的原子覆盖
- 支持的组件：
  - `skills`
  - `commands`
  - `agents`
  - `mcpServers`
  - `hooks`
- `commands` 映射为 Agentao skill/prompt 能力
- `UserPromptSubmit` 支持：
  - `command`
  - `prompt`
- 其它受支持 hook 事件只支持：
  - `command`
- 已知但不支持的 Claude 能力：
  - 记录 warning
  - 不 silently ignore

## Document Structure

### Staged Design Docs

1. [Phase 1: Manifest And Loader](plugin-system-mvp/PHASE_1_MANIFEST_AND_LOADER.md)
2. [Phase 2: Skills And Commands](plugin-system-mvp/PHASE_2_SKILLS_AND_COMMANDS.md)
3. [Phase 3: Agents](plugin-system-mvp/PHASE_3_AGENTS.md)
4. [Phase 4: MCP Integration](plugin-system-mvp/PHASE_4_MCP_INTEGRATION.md)
5. [Phase 5: UserPromptSubmit And Hook Core](plugin-system-mvp/PHASE_5_USER_PROMPT_SUBMIT_AND_HOOK_CORE.md)
6. [Phase 6: Session Tool Hooks And CLI Diagnostics](plugin-system-mvp/PHASE_6_SESSION_TOOL_HOOKS_AND_CLI.md)

### Issue Docs

issue 文件位于：

- [plugin-system-mvp/issues/README.md](plugin-system-mvp/issues/README.md)

当前已拆分为 18 个 issue，覆盖：

- models
- manifest parser
- path safety
- discovery / precedence
- loaded plugin assembly
- skills / commands
- agents
- MCP
- hooks parser
- payload adapters
- `UserPromptSubmit`
- lifecycle hooks
- CLI diagnostics
- fixtures
- diagnostics renderer

## Source Of Truth Policy

为了避免设计漂移，后续按以下规则维护：

1. 分阶段设计以 `docs/implementation/plugin-system-mvp/*.md` 为真源
2. 任务拆解以 `docs/implementation/plugin-system-mvp/issues/*.md` 为真源
3. 本文件只保留：
   - 总目标
   - 关键边界
   - 导航入口
4. 不再把完整细节继续堆回本文件

## Recommended Workflow

设计演进时：

1. 先更新对应 phase 文档
2. 再同步对应 issue 文档
3. 若需要，只在本文件调整摘要或入口

实现时：

1. 从 issue 文档进入
2. 需要完整上下文时回看对应 phase 文档
3. 不再把本文件当作详细实施规格
