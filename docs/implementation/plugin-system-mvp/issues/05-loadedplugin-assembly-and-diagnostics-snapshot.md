# 05 LoadedPlugin Assembly And Diagnostics Snapshot

Parent phase: [Phase 1: Manifest And Loader](../PHASE_1_MANIFEST_AND_LOADER.md)

Issue index: [Plugin System MVP Issues](README.md)

## Goal

从 precedence 后的 `PluginCandidate` 构建 `LoadedPlugin`，并提供一份稳定 diagnostics snapshot 给 CLI、日志和测试使用。

## Scope

- `load_plugin()`
- `load_plugins()`
- `list_plugins()`
- `get_warnings()`
- `get_errors()`

## Deliverables

- `agentao/plugins/loader.py`
- `agentao/plugins/diagnostics.py`

## Dependencies

- 01
- 02
- 03
- 04

## Design Notes

- 当前阶段可先只组装 paths，不注册 runtime capability
- diagnostics 输出应可追踪 plugin name、source、root_path

## Fixtures

- `full-plugin`
- `unsupported-fields-plugin`
- `invalid-json-plugin`

## Related Fixtures

- `full-plugin`
- `unsupported-fields-plugin`
- `invalid-json-plugin`

## Tests

- `LoadedPlugin` fields assembled correctly
- warnings/errors snapshot stable
- one plugin failure does not clear successful loaded set

## Acceptance Criteria

1. `PluginManager.load_plugins()` 能产出稳定的 `LoadedPlugin` 列表
2. diagnostics snapshot 可直接被 CLI 与测试消费

## Out Of Scope

- skill/agent/MCP/hook registration
