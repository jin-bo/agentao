# Add ACP Session Registry And Per-Session Agentao Runtime Lifecycle

## Problem

ACP is session-based, but Agentao currently does not maintain ACP session state or a registry of runtime instances keyed by ACP session ID.

## Scope

- Add ACP session registry
- Create `AcpSessionState`
- Manage Agentao instance lifecycle per ACP session

## Implementation Checklist

- [ ] Define `AcpSessionState` in `agentao/acp/models.py`
- [ ] Track `session_id`, `agent`, `cwd`, `client_capabilities`, `cancel_token`
- [ ] Implement create/get/delete/close operations in `session_manager.py`
- [ ] Ensure server shutdown closes all session-owned Agentao instances
- [ ] Add protection against duplicate or missing session IDs

## Acceptance Criteria

- [ ] Multiple ACP sessions can exist at once in the same server process
- [ ] Each ACP session has its own Agentao runtime state
- [ ] Session lookup and teardown are reliable

## Dependencies

- Depends on: `01-acp-module-skeleton-and-jsonrpc-server.md`
