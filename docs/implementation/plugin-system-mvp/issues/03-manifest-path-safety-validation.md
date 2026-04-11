# 03 Manifest Path Safety Validation

Parent phase: [Phase 1: Manifest And Loader](../PHASE_1_MANIFEST_AND_LOADER.md)

Issue index: [Plugin System MVP Issues](README.md)

## Goal

实现 manifest path safety 校验，确保 plugin 只能引用 plugin root 内的安全路径。

## Scope

- `./` 前缀要求
- 禁止绝对路径
- 禁止 `../`
- 禁止 symlink escape
- 路径校验 warning/error 归类

## Deliverables

- `PluginManifestParser.validate_paths()`
- path safety 单测

## Dependencies

- 01 Plugin Models And Diagnostics Types
- 02 Plugin Manifest Parser

## Fixtures

- `path-traversal-plugin`
- `full-plugin`

## Related Fixtures

- `path-traversal-plugin`
- `full-plugin`

## Tests

- relative safe path passes
- absolute path fails
- `../` fails
- symlink escape fails

## Acceptance Criteria

1. 所有 manifest path 都经过统一安全校验
2. 不安全路径直接 fail
3. 错误消息能定位到 offending path

## Out Of Scope

- plugin discovery
- runtime registration
