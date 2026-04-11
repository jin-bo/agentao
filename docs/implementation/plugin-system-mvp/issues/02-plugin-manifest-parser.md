# 02 Plugin Manifest Parser

Parent phase: [Phase 1: Manifest And Loader](../PHASE_1_MANIFEST_AND_LOADER.md)

Issue index: [Plugin System MVP Issues](README.md)

## Goal

实现 `PluginManifestParser`，支持 `plugin.json` 的 Claude-compatible subset 解析。

## Scope

- `parse_file()`
- `parse_dict()`
- 结构校验
- known unsupported field 保留

## Deliverables

- `agentao/plugins/manifest.py`
- manifest parser 单测

## Dependencies

- 01 Plugin Models And Diagnostics Types

## Design Notes

- 支持 `commands`、`skills`、`agents`、`hooks`、`mcpServers`
- Python 侧允许 `mcp_servers`，JSON 输入仍接受 `mcpServers`
- `outputStyles`、`lspServers`、`settings`、`channels`、`userConfig` 只 warning

## Fixtures

- `minimal-plugin`
- `full-plugin`
- `inline-config-plugin`
- `unsupported-fields-plugin`

## Related Fixtures

- `minimal-plugin`
- `full-plugin`
- `inline-config-plugin`
- `unsupported-fields-plugin`

## Tests

- valid minimal manifest
- full manifest parse
- inline hooks/MCP parse
- known unsupported fields warning
- malformed field shape fail

## Acceptance Criteria

1. 受支持字段都能正确解析
2. unsupported fields 被保留并带 warning
3. malformed manifest 会产生 clear error

## Out Of Scope

- path safety
- directory discovery
