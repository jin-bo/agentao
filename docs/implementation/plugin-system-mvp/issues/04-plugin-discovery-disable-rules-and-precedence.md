# 04 Plugin Discovery Disable Rules And Precedence

Parent phase: [Phase 1: Manifest And Loader](../PHASE_1_MANIFEST_AND_LOADER.md)

Issue index: [Plugin System MVP Issues](README.md)

## Goal

实现 global/project/inline 三层 plugin discovery、disable 规则和 precedence 决议。

## Scope

- `~/.agentao/plugins`
- `<cwd>/.agentao/plugins`
- `--plugin-dir`
- `plugins_config.json`
- `discover_candidates()`
- `filter_disabled()`
- `resolve_precedence()`

## Deliverables

- `agentao/plugins/manager.py`
- discovery/preference 单测

## Dependencies

- 01
- 02
- 03

## Design Notes

- identity 使用 `plugin.json.name`
- precedence: global < project < inline
- 同名 plugin 做原子覆盖，不做 cross-layer merge

## Fixtures

- `minimal-plugin`
- `duplicate-name-global`
- `duplicate-name-project`
- `invalid-json-plugin`

## Related Fixtures

- `minimal-plugin`
- `duplicate-name-global`
- `duplicate-name-project`
- `invalid-json-plugin`

## Tests

- auto-discovery of global/project
- inline plugin inclusion
- project overrides global
- disabled plugin filtered
- broken plugin does not block others

## Acceptance Criteria

1. precedence 稳定
2. disable rules 生效
3. 同名冲突与来源能在 diagnostics 中解释

## Out Of Scope

- component loading
- runtime registration
