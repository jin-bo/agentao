# 17 Plugin Fixture Library

Parent phases:

- [Phase 1: Manifest And Loader](../phase-1-manifest-and-loader.md)
- [Phase 2: Skills And Commands](../phase-2-skills-and-commands.md)
- [Phase 3: Agents](../phase-3-agents.md)
- [Phase 4: MCP Integration](../phase-4-mcp-integration.md)
- [Phase 5: UserPromptSubmit And Hook Core](../phase-5-user-prompt-submit-and-hook-core.md)
- [Phase 6: Session Tool Hooks And CLI Diagnostics](../phase-6-session-tool-hooks-and-cli.md)

Issue index: [Plugin System MVP Issues](README.md)

## Goal

建立统一的 plugin fixture 目录集合，供 manifest、loader、runtime、hooks、CLI 测试复用。

## Scope

- `tests/fixtures/plugins/`
- 基础 plugin fixture 目录
- hook script fixture layout
- fixture-to-test mapping

## Deliverables

- fixture directories and files
- fixture usage notes

## Dependencies

- 无

## Proposed Fixtures

- `minimal-plugin`
- `full-plugin`
- `inline-config-plugin`
- `unsupported-fields-plugin`
- `invalid-json-plugin`
- `path-traversal-plugin`
- `duplicate-name-global`
- `duplicate-name-project`
- `skills-only-plugin`
- `commands-only-plugin`
- `skills-and-commands-collision-plugin`
- `agents-only-plugin`
- `malformed-agent-plugin`
- `mcp-file-plugin`
- `mcp-inline-plugin`
- `mcp-collision-plugin-a`
- `mcp-collision-plugin-b`
- `user-prompt-submit-command-plugin`
- `user-prompt-submit-prompt-plugin`
- `user-prompt-submit-blocking-plugin`
- `session-hooks-plugin`
- `tool-hooks-plugin`
- `unsupported-hook-type-plugin`
- `unsupported-hook-event-plugin`

## Related Fixtures

- `minimal-plugin`
- `full-plugin`
- `inline-config-plugin`
- `unsupported-fields-plugin`
- `invalid-json-plugin`
- `path-traversal-plugin`
- `duplicate-name-global`
- `duplicate-name-project`
- `skills-only-plugin`
- `commands-only-plugin`
- `skills-and-commands-collision-plugin`
- `agents-only-plugin`
- `malformed-agent-plugin`
- `mcp-file-plugin`
- `mcp-inline-plugin`
- `mcp-collision-plugin-a`
- `mcp-collision-plugin-b`
- `user-prompt-submit-command-plugin`
- `user-prompt-submit-prompt-plugin`
- `user-prompt-submit-blocking-plugin`
- `session-hooks-plugin`
- `tool-hooks-plugin`
- `unsupported-hook-type-plugin`
- `unsupported-hook-event-plugin`

## Tests

- fixture paths stable
- fixture minimal content valid for intended tests

## Acceptance Criteria

1. 所有后续测试都能复用统一 fixtures
2. fixture 命名和用途清晰

## Out Of Scope

- runtime implementation
