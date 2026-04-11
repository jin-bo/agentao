# 11 Tool Alias Resolver And Hook Payload Adapters

Parent phase: [Phase 5: UserPromptSubmit And Hook Core](../PHASE_5_USER_PROMPT_SUBMIT_AND_HOOK_CORE.md)

Issue index: [Plugin System MVP Issues](README.md)

## Goal

实现 Claude-compatible tool alias mapping 和 hook payload adapters。

## Scope

- `ToolAliasResolver`
- `build_session_start()`
- `build_session_end()`
- `build_pre_tool_use()`
- `build_post_tool_use()`
- `build_post_tool_use_failure()`
- `build_user_prompt_submit()`

## Deliverables

- payload adapter implementation
- adapter tests

## Dependencies

- 10
- 17

## Design Notes

- `Read` / `Write` / `Edit` / `Bash` 必须稳定
- payload 字段名优先对齐 Claude-compatible contract

## Fixtures

- `session-hooks-plugin`
- `tool-hooks-plugin`
- `user-prompt-submit-command-plugin`

## Related Fixtures

- `session-hooks-plugin`
- `tool-hooks-plugin`
- `user-prompt-submit-command-plugin`

## Tests

- aliases stable
- payload field names stable
- tool input/output/error payload shapes correct

## Acceptance Criteria

1. 核心 alias 稳定
2. payload adapter 输出能直接供 hooks 使用

## Out Of Scope

- actual hook execution
