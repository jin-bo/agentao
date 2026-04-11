# 12 UserPromptSubmit Command Hooks

Parent phase: [Phase 5: UserPromptSubmit And Hook Core](../PHASE_5_USER_PROMPT_SUBMIT_AND_HOOK_CORE.md)

Issue index: [Plugin System MVP Issues](README.md)

## Goal

实现 `UserPromptSubmit` 的 command hook 执行路径。

## Scope

- hook selection
- 串行执行
- shell runner integration
- structured command result parsing
- warning handling

## Deliverables

- `PluginHookDispatcher.dispatch_user_prompt_submit()` 的 command path
- command hook tests

## Dependencies

- 10
- 11
- 17

## Fixtures

- `user-prompt-submit-command-plugin`
- `user-prompt-submit-blocking-plugin`

## Related Fixtures

- `user-prompt-submit-command-plugin`
- `user-prompt-submit-blocking-plugin`

## Tests

- command hook executes
- additionalContext attachment emitted
- blockingError suppresses query
- preventContinuation suppresses query
- timeout warning
- non-zero exit warning

## Acceptance Criteria

1. `UserPromptSubmit` command hooks 可执行
2. 结构化结果正确映射到 attachment records
3. 错误不阻断主流程

## Out Of Scope

- prompt hooks
- final message normalization
