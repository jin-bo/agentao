# Create ACP Module Skeleton And Stdio JSON-RPC Server Foundation

## Problem

Agentao does not expose any ACP-compatible server endpoint today. There is no JSON-RPC server, no ACP method dispatcher, and no ACP-specific module boundary.

## Scope

- Create `agentao/acp/`
- Add `protocol.py`
- Add `server.py`
- Add `session_manager.py`
- Add `transport.py`
- Add `models.py`
- Implement JSON-RPC request parsing and response writing over stdio
- Implement method dispatch and standard error responses

## Implementation Checklist

- [ ] Create `agentao/acp/__init__.py`
- [ ] Define ACP constants and supported protocol version in `agentao/acp/protocol.py`
- [ ] Implement stdio read loop in `agentao/acp/server.py`
- [ ] Implement thread-safe JSON-RPC write path in `agentao/acp/server.py`
- [ ] Implement request dispatcher by `method`
- [ ] Implement standard JSON-RPC error handling
- [ ] Ensure logs never pollute stdout JSON-RPC stream

## Acceptance Criteria

- [ ] Process can start in ACP stdio mode without entering interactive CLI mode
- [ ] Server can parse valid JSON-RPC requests and emit valid responses
- [ ] Unknown methods return proper JSON-RPC method-not-found errors

## Notes

- This is the foundation issue. Other ACP issues should build on this module layout rather than creating protocol code ad hoc.
