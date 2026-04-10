# Implement ACP Initialize Handshake And Capability Negotiation

## Problem

ACP clients require an `initialize` handshake before any session methods. Agentao currently has no ACP capability advertisement layer.

## Scope

- Implement `initialize`
- Negotiate protocol version
- Return `agentCapabilities`, `agentInfo`, and auth metadata

## Implementation Checklist

- [ ] Parse `protocolVersion`, `clientCapabilities`, and `clientInfo`
- [ ] Define supported ACP version constant
- [ ] Return `loadSession` capability
- [ ] Return baseline prompt capabilities for v1
- [ ] Return MCP capability advertisement based on actual support
- [ ] Return `agentInfo` with name, title, version
- [ ] Store client capabilities for later session use

## Acceptance Criteria

- [ ] ACP client can complete `initialize`
- [ ] Version mismatch behavior is deterministic and documented
- [ ] Response shape matches ACP expectations for supported fields

## Dependencies

- Depends on: `01-acp-module-skeleton-and-jsonrpc-server.md`
