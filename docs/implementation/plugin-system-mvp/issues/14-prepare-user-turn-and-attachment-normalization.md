# 14 Prepare User Turn And Attachment Normalization

Parent phase: [Phase 5: UserPromptSubmit And Hook Core](../PHASE_5_USER_PROMPT_SUBMIT_AND_HOOK_CORE.md)

Issue index: [Plugin System MVP Issues](README.md)

## Goal

实现 `prepare_user_turn()` 和 Claude-compatible attachment normalization。

## Scope

- `HookAttachmentRecord`
- `PreparedTurnMessage`
- `PreparedUserTurn`
- attachment -> meta message normalization
- final user-message append decision

## Deliverables

- `agentao/agent.py` prepare path
- normalization helpers
- tests

## Dependencies

- 12
- 13

## Fixtures

- `user-prompt-submit-command-plugin`
- `user-prompt-submit-prompt-plugin`
- `user-prompt-submit-blocking-plugin`

## Related Fixtures

- `user-prompt-submit-command-plugin`
- `user-prompt-submit-prompt-plugin`
- `user-prompt-submit-blocking-plugin`

## Tests

- attachments preserved before flattening
- reminder templates stable
- original user message appended only when should_query=True
- blocking/preventContinuation path returns early

## Acceptance Criteria

1. `prepare_user_turn()` 成为唯一 `UserPromptSubmit` 入口
2. message injection 语义与设计一致
3. original prompt 不会被直接拼接污染

## Out Of Scope

- session hooks
- tool hooks
