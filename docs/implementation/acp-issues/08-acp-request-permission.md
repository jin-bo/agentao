# Map Agentao Tool Confirmations To ACP Session Request Permission

## Problem

Agentao tool confirmation is currently synchronous and transport-local. ACP requires a protocol-level permission request flow.

## Scope

- Implement `ACPTransport.confirm_tool`
- Translate tool confirmation to ACP request/response flow

## Implementation Checklist

- [ ] Emit `session/request_permission` from ACP transport
- [ ] Offer at least `allow_once` and `reject_once`
- [ ] Optionally support `allow_session` in v1 if feasible
- [ ] Block until client responds or timeout occurs
- [ ] Translate selected option to ToolRunner-compatible boolean result
- [ ] If `allow_session` is supported, update session permission state
- [ ] Ensure multiple permission prompts for one session are serialized

## Acceptance Criteria

- [ ] ACP client can approve a tool call once
- [ ] ACP client can reject a tool call once
- [ ] Timeout or disconnect produces deterministic cancellation behavior

## Dependencies

- Depends on: `07-acp-session-update-transport.md`
