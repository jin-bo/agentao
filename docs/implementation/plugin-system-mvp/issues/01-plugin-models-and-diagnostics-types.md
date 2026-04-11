# 01 Plugin Models And Diagnostics Types

Parent phase: [Phase 1: Manifest And Loader](../PHASE_1_MANIFEST_AND_LOADER.md)

Issue index: [Plugin System MVP Issues](README.md)

## Goal

定义 plugin system 的基础数据结构和 diagnostics 类型，作为后续 manifest、loader、runtime integration 的统一类型底座。

## Scope

- `PluginAuthor`
- `PluginDependencyRef`
- `PluginCommandMetadata`
- `PluginManifest`
- `PluginCandidate`
- `LoadedPlugin`
- `PluginWarning`
- `PluginLoadError`

## Deliverables

- `agentao/plugins/models.py`
- 基础 dataclass 或等价类型定义
- 最小类型单测

## Dependencies

- 无

## Related Fixtures

- 无

## Design Notes

- `plugin.json.name` 是 plugin identity
- `LoadedPlugin` 应保留 source、root_path、warnings
- diagnostics 类型必须同时支持 load-time 和 runtime warning

## Tests

- 类型默认值正确
- `unsupported_fields` 默认空 dict
- warnings/errors 可稳定序列化或格式化

## Acceptance Criteria

1. 后续 manifest parser 和 loader 能直接依赖这些类型
2. 类型字段覆盖当前 staged plan 所需最小信息

## Out Of Scope

- manifest parsing
- path validation
- runtime registration
