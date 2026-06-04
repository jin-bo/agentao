# Implement ACP Session New And Runtime Initialization From Request Params

## Problem

There is no ACP entry point for creating a new session, binding a working directory, or injecting per-session MCP configuration.

## Scope

- Implement `session/new`
- Accept `cwd`
- Accept session-level MCP server config
- Create Agentao instance with ACP transport

## Implementation Checklist

- [ ] Parse `cwd` from request
- [ ] Parse `mcpServers` from request
- [ ] Generate ACP `sessionId`
- [ ] Create `ACPTransport` and inject it into Agentao
- [ ] Create `PermissionEngine` for the session
- [ ] Initialize MCP connections for the session where possible
- [ ] Return `sessionId`
- [ ] Handle invalid cwd and bad MCP config cleanly

## Acceptance Criteria

- [ ] ACP client can create a new session successfully
- [ ] Returned `sessionId` can be used in later requests
- [ ] Session creation failures return structured errors

## Dependencies

- Depends on: `02-acp-initialize-handshake.md`
- Depends on: `03-acp-session-registry.md`
