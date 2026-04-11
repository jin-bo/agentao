# 16 CLI Plugin Discovery And Diagnostics

Parent phase: [Phase 6: Session Tool Hooks And CLI Diagnostics](../PHASE_6_SESSION_TOOL_HOOKS_AND_CLI.md)

Issue index: [Plugin System MVP Issues](README.md)

## Goal

补齐最小 CLI 与 diagnostics 面，让用户能查看当前 plugin 状态。

## Scope

- repeatable `--plugin-dir`
- `agentao plugin list`
- startup warnings surface
- optional reload entry if adopted

## Deliverables

- `agentao/cli.py`
- CLI tests

## Dependencies

- 05
- 15
- 18

## Fixtures

- `minimal-plugin`
- `duplicate-name-global`
- `duplicate-name-project`
- `unsupported-fields-plugin`

## Related Fixtures

- `minimal-plugin`
- `duplicate-name-global`
- `duplicate-name-project`
- `unsupported-fields-plugin`

## Tests

- repeatable `--plugin-dir`
- list command output
- warnings surfaced
- precedence visible in diagnostics

## Acceptance Criteria

1. 用户能通过 CLI 看到当前 loaded plugins
2. warning/error 足够定位问题

## Out Of Scope

- marketplace
- install/remove flows
