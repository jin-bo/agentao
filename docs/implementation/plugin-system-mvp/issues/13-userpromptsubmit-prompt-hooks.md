# 13 UserPromptSubmit Prompt Hooks

Parent phase: [Phase 5: UserPromptSubmit And Hook Core](../PHASE_5_USER_PROMPT_SUBMIT_AND_HOOK_CORE.md)

Issue index: [Plugin System MVP Issues](README.md)

## Goal

实现 `UserPromptSubmit` 的 prompt hook 执行路径。

## Scope

- prompt hook runner integration
- structured JSON output parsing
- invalid free-form output warning
- recursion guard

## Deliverables

- prompt hook execution path
- prompt hook tests

## Dependencies

- 10
- 11
- 12
- 17

## Fixtures

- `user-prompt-submit-prompt-plugin`

## Related Fixtures

- `user-prompt-submit-prompt-plugin`

## Tests

- prompt hook executes
- structured JSON parsed
- additionalContext attachment emitted
- invalid output warns
- recursion prevented

## Acceptance Criteria

1. `UserPromptSubmit.prompt` 可执行
2. 必须使用结构化输出
3. 不会递归触发 `UserPromptSubmit`

## Out Of Scope

- non-`UserPromptSubmit` prompt hooks
