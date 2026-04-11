# 18 Diagnostics Renderer And Compatibility Notes

Parent phase: [Phase 6: Session Tool Hooks And CLI Diagnostics](../PHASE_6_SESSION_TOOL_HOOKS_AND_CLI.md)

Issue index: [Plugin System MVP Issues](README.md)

## Goal

整理 plugin diagnostics 输出格式，并补一份简短的 Claude-Code compatibility notes，避免测试、CLI、日志各自重复定义格式。

## Scope

- diagnostics renderer
- warning/error display shape
- compatibility notes doc fragment

## Deliverables

- shared diagnostics formatter
- docs note or README update

## Dependencies

- 05
- 16

## Related Fixtures

- `unsupported-fields-plugin`
- `invalid-json-plugin`
- `duplicate-name-global`
- `duplicate-name-project`

## Tests

- formatted warnings stable
- source path and plugin name included

## Acceptance Criteria

1. diagnostics 格式可复用
2. 用户和开发者都能理解不兼容点

## Out Of Scope

- core runtime behavior
