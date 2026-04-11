# 09 Plugin MCP Loading And Merge

Parent phase: [Phase 4: MCP Integration](../PHASE_4_MCP_INTEGRATION.md)

Issue index: [Plugin System MVP Issues](README.md)

## Goal

实现 plugin `.mcp.json` 和 manifest `mcpServers` 的读取、合并和注册。

## Scope

- file-based MCP
- inline `mcpServers`
- merge order
- collision diagnostics

## Deliverables

- MCP merge logic
- MCP integration tests

## Dependencies

- 05
- 17

## Fixtures

- `mcp-file-plugin`
- `mcp-inline-plugin`
- `mcp-collision-plugin-a`
- `mcp-collision-plugin-b`

## Related Fixtures

- `mcp-file-plugin`
- `mcp-inline-plugin`
- `mcp-collision-plugin-a`
- `mcp-collision-plugin-b`

## Tests

- file-based MCP loads
- inline MCP loads
- collision fails clearly
- registered MCP tools usable

## Acceptance Criteria

1. plugin MCP servers merged per design
2. same-name collisions fail with diagnostics

## Out Of Scope

- hooks
