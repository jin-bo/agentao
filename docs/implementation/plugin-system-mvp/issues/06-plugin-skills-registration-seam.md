# 06 Plugin Skills Registration Seam

Parent phase: [Phase 2: Skills And Commands](../PHASE_2_SKILLS_AND_COMMANDS.md)

Issue index: [Plugin System MVP Issues](README.md)

## Goal

给 `SkillManager` 增加 plugin-provided skills 的注册入口。

## Scope

- plugin skill source registration seam
- `skills/` 目录扫描
- source metadata 标记

## Deliverables

- `agentao/skills/manager.py`
- skills integration 单测

## Dependencies

- 05
- 17

## Fixtures

- `skills-only-plugin`
- `full-plugin`

## Related Fixtures

- `skills-only-plugin`
- `full-plugin`

## Tests

- `skills/` discovery
- plugin source metadata preserved
- skill visible in runtime listing

## Acceptance Criteria

1. plugin skill 能被 Agentao 发现
2. skill source 能标记 plugin origin

## Out Of Scope

- commands mapping
- collision policy finalization beyond basic checks
