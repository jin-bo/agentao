# 07 Plugin Commands Mapping

Parent phase: [Phase 2: Skills And Commands](../PHASE_2_SKILLS_AND_COMMANDS.md)

Issue index: [Plugin System MVP Issues](README.md)

## Goal

把 plugin `commands` 映射为 Agentao skill-like prompt entries。

## Scope

- `commands/*.md`
- manifest `commands` path list
- manifest `commands` object mapping
- runtime namespacing
- collision diagnostics

## Deliverables

- plugin command mapping glue
- commands integration 单测

## Dependencies

- 05
- 06
- 17

## Fixtures

- `commands-only-plugin`
- `skills-and-commands-collision-plugin`
- `full-plugin`

## Related Fixtures

- `commands-only-plugin`
- `skills-and-commands-collision-plugin`
- `full-plugin`

## Tests

- markdown commands discovered
- mapping-format commands discovered
- namespaced runtime names stable
- collision fails clearly

## Acceptance Criteria

1. commands 能作为 skill-like entries 激活
2. object mapping 和 file-based commands 都能工作
3. collision 有 clear diagnostics

## Out Of Scope

- agents
- hooks
