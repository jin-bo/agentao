# 08 Plugin Agents Registration

Parent phase: [Phase 3: Agents](../PHASE_3_AGENTS.md)

Issue index: [Plugin System MVP Issues](README.md)

## Goal

把 plugin agents 接入 Agentao agent discovery。

## Scope

- 默认 `agents/`
- manifest `agents`
- namespacing
- malformed agent isolation

## Deliverables

- `agentao/agents/manager.py` 接线
- agents integration 单测

## Dependencies

- 05
- 17

## Fixtures

- `agents-only-plugin`
- `malformed-agent-plugin`
- `full-plugin`

## Related Fixtures

- `agents-only-plugin`
- `malformed-agent-plugin`
- `full-plugin`

## Tests

- plugin agents discovered
- runtime names stable
- malformed agent isolated

## Acceptance Criteria

1. plugin agents 出现在 agent list
2. 单个损坏 agent 不拖垮其它 plugins

## Out Of Scope

- MCP
- hooks
