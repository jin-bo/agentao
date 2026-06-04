# 15 Session And Tool Lifecycle Hooks

Parent phase: [Phase 6: Session Tool Hooks And CLI Diagnostics](../phase-6-session-tool-hooks-and-cli.md)

Issue index: [Plugin System MVP Issues](README.md)

## Goal

实现 `SessionStart`、`SessionEnd`、`PreToolUse`、`PostToolUse`、`PostToolUseFailure` 的 command hook 执行路径。

> `Stop` 与 `PreCompact` 的事件面在 [`STOP_PRECOMPACT_HOOKS_PLAN.md`](../../stop-precompact-hooks-plan.md) PR-1 (Phase A) 单独落地。两者在 dispatcher 层共用 `_dispatch_lifecycle`，但负载使用 Claude flat snake_case 顶层 schema，并且 chat-loop helper 自带 `select_matching_rules` no-emit gate；详见该 plan 的 A2/A3/A5。

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
- `Stop` / `PreCompact`（已在 [`STOP_PRECOMPACT_HOOKS_PLAN.md`](../../stop-precompact-hooks-plan.md) 独立交付；本 issue 仅覆盖 Phase 6 阶段定义的 5 个 lifecycle 事件）
