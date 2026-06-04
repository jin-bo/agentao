# Support Session-Scoped MCP Server Injection From ACP Session New

## Problem

ACP session creation may include MCP server configuration, but Agentao currently loads MCP mostly from local config files at startup.

## Scope

- Convert ACP-provided MCP config into Agentao MCP client config
- Attach MCP tools to the session runtime

## Implementation Checklist

- [ ] Define config translation from ACP `mcpServers` to Agentao MCP config
- [ ] Merge or override local MCP config with session-level config
- [ ] Connect MCP servers for the session runtime
- [ ] Register discovered MCP tools for that session
- [ ] Surface MCP connection failures in a non-fatal and observable way

## Acceptance Criteria

- [ ] ACP session can expose tools from request-provided MCP servers
- [ ] MCP failures do not necessarily kill the whole server process
- [ ] Session-level MCP config does not leak between sessions

## Dependencies

- Depends on: `04-acp-session-new.md`
