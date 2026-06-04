# Implement ACP Session Cancel Using Existing Agentao Cancellation Flow

## Problem

ACP clients must be able to cancel an active turn. Agentao already has cancellation primitives, but they are not exposed as ACP session methods.

## Scope

- Implement `session/cancel`
- Bind ACP cancellation to `CancellationToken`

## Implementation Checklist

- [ ] Track active turn token per ACP session
- [ ] Implement `session/cancel` handler
- [ ] Make cancellation idempotent
- [ ] Ensure cancellation propagates to LLM and tool execution
- [ ] Define consistent post-cancel completion/update behavior

## Acceptance Criteria

- [ ] ACP client can cancel an active session turn
- [ ] Cancelled turns stop without hanging the server
- [ ] Repeated cancel requests do not crash or corrupt state

## Dependencies

- Depends on: `06-acp-session-prompt.md`
- Strongly related to: `07-acp-session-update-transport.md`
