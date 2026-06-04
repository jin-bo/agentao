# Phase 4: MCP Integration

## Goal

把插件中的 `.mcp.json` 和 manifest `mcpServers` 接入 Agentao 的 MCP config merge 和 tool registration 流程。

## Why This Phase Exists

MCP 是插件系统中最容易影响工具层稳定性的能力，必须单独阶段处理，明确 merge 顺序、命名冲突和 diagnostics。

## Scope

本阶段包含：

- 默认 `.mcp.json` 读取
- manifest `mcpServers` path / inline object 支持
- plugin MCP 与现有 MCP config 的 merge
- collision diagnostics
- runtime MCP registration

本阶段不包含：

- hooks
- `UserPromptSubmit`
- tool lifecycle hooks mutation

## Dependencies

本阶段依赖：

- Phase 1 的 plugin loading
- 现有 MCP config assembly path

## Merge Rules

- 支持两类来源：
  - plugin root `.mcp.json`
  - manifest `mcpServers`
- manifest `mcpServers` 可为 path 或 inline object
- plugin MCP 合并发生在插件加载后、runtime 注册前
- 同名 MCP server collision 为 fatal
- 不做 silent rename

## Proposed Runtime API

```python
def merge_plugin_mcp_servers(
    base_config: dict[str, Any],
    plugins: list[LoadedPlugin],
) -> dict[str, Any]: ...
```

建议 diagnostics payload：

```python
@dataclass
class McpCollisionDiagnostic:
    server_name: str
    first_source: str
    second_source: str
    message: str
```

## Example Inputs

file-based：

```json
{
  "name": "localdocs",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "./docs"]
}
```

manifest inline：

```json
{
  "mcpServers": {
    "localdocs": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "./docs"]
    }
  }
}
```

## Fixture Coverage

建议主要使用：

- `mcp-file-plugin`
- `mcp-inline-plugin`
- `mcp-collision-plugin-a`
- `mcp-collision-plugin-b`
- `full-plugin`

## Lifecycle Sequence

```text
PluginManager.load_plugins()
  -> collect plugin mcp server definitions
  -> merge with existing MCP config
  -> validate collisions
  -> register final MCP server set
```

## Issue Backlog

1. 实现 `.mcp.json` 读取
2. 实现 manifest `mcpServers` path / inline parsing
3. 实现 plugin MCP merge order
4. 实现 collision diagnostics
5. 接到现有 MCP runtime registration
6. 编写 MCP 集成测试

## Tests

- plugin `.mcp.json` 被读取
- inline `mcpServers` 被读取
- manifest override 生效
- same-name MCP collision fail
- plugin MCP tools 可注册

## Acceptance Criteria

1. plugin MCP servers 能稳定读入
2. merge 顺序符合设计
3. 名称冲突有 clear diagnostics
4. plugin MCP tools 能在 Agentao runtime 中使用

## Out Of Scope

- hooks
- tool hook mutation
- marketplace

## Related Issues

- [09 Plugin MCP Loading And Merge](issues/09-plugin-mcp-loading-and-merge.md)
