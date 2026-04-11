# 15 Session And Tool Lifecycle Hooks

Parent phase: [Phase 6: Session Tool Hooks And CLI Diagnostics](../PHASE_6_SESSION_TOOL_HOOKS_AND_CLI.md)

Issue index: [Plugin System MVP Issues](README.md)

## Goal

实现 `SessionStart`、`SessionEnd`、`PreToolUse`、`PostToolUse`、`PostToolUseFailure` 的 command hook 执行路径。

## Scope

- session lifecycle dispatch
- tool lifecycle dispatch
- matcher / `if` filtering
- side-effect only behavior

## Deliverables

- `PluginHookDispatcher` lifecycle methods
- tool runner integration
- lifecycle tests

## Dependencies

- 10
- 11
- 17

## Fixtures

- `session-hooks-plugin`
- `tool-hooks-plugin`
- `unsupported-hook-type-plugin`
- `unsupported-hook-event-plugin`

## Related Fixtures

- `session-hooks-plugin`
- `tool-hooks-plugin`
- `unsupported-hook-type-plugin`
- `unsupported-hook-event-plugin`

## Tests

- SessionStart executes
- SessionEnd executes
- PreToolUse alias matching works
- PostToolUse success path works
- PostToolUseFailure error path works
- failures only warn

## Acceptance Criteria

1. 所有受支持 lifecycle hooks 都能执行
2. 不改变 tool 主流程结果
3. alias/matcher 语义符合设计

## Out Of Scope

- tool input/output mutation
- HTTP/agent hooks
