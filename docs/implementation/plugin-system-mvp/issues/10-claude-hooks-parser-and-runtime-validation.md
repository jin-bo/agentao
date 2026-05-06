# 10 Claude Hooks Parser And Runtime Validation

Parent phase: [Phase 5: UserPromptSubmit And Hook Core](../PHASE_5_USER_PROMPT_SUBMIT_AND_HOOK_CORE.md)

Issue index: [Plugin System MVP Issues](README.md)

## Goal

实现 `hooks/hooks.json` 的 Claude-compatible subset parser，以及 runtime support validation。

## Scope

- `ClaudeHooksParser.parse_file()`
- `ClaudeHooksParser.parse_dict()`
- matcher schema
- hook type schema
- runtime support validation
- per-event hook-type allowlist (`SUPPORTED_HOOK_TYPES_BY_EVENT`) — added by [`STOP_PRECOMPACT_HOOKS_PLAN.md`](../../STOP_PRECOMPACT_HOOKS_PLAN.md) §A1; rejects `prompt`-type rules under `Stop` / `PreCompact` at parse time
- non-object matcher rejection at parse time — same plan §A2 (drop the rule rather than normalize to `None`, which would silently match every event)

## Deliverables

- `agentao/plugins/hooks.py` 的 parser 部分
- hooks parser tests

## Dependencies

- 05
- 17

## Fixtures

- `user-prompt-submit-command-plugin`
- `user-prompt-submit-prompt-plugin`
- `unsupported-hook-type-plugin`
- `unsupported-hook-event-plugin`

## Related Fixtures

- `user-prompt-submit-command-plugin`
- `user-prompt-submit-prompt-plugin`
- `unsupported-hook-type-plugin`
- `unsupported-hook-event-plugin`

## Tests

- hooks.json parse success
- unsupported hook type warns
- unsupported event warns
- `UserPromptSubmit.prompt` allowed, others skipped
- `Stop` / `PreCompact` `prompt`-type rules dropped at parse time with event-specific warning (see `tests/test_hooks_stop_precompact_reject_prompt_type.py`)
- non-object matcher (string / list) dropped at parse time (see `tests/test_hooks_pre_compact_matcher_non_dict_guard.py`)

## Acceptance Criteria

1. hooks schema 与设计一致
2. runtime validation 能稳定区分 supported vs skipped

## Out Of Scope

- hook payload construction
- hook execution
