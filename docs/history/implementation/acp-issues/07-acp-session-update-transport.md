# Implement ACP Transport Adapter For Session Update Notifications

## Problem

Agentao emits internal runtime events, but ACP clients expect `session/update` notifications using ACP update types.

## Scope

- Implement `ACPTransport`
- Map internal `AgentEvent` values to ACP update notifications

## Implementation Checklist

- [ ] Map `EventType.LLM_TEXT` to ACP agent message updates
- [ ] Map `EventType.TOOL_START` to ACP tool call updates
- [ ] Map `EventType.TOOL_OUTPUT` to incremental tool call content updates
- [ ] Map `EventType.TOOL_COMPLETE` to terminal tool call updates
- [ ] Decide v1 behavior for `EventType.THINKING`
- [ ] Decide v1 behavior for sub-agent lifecycle events
- [ ] Ensure all emitted payloads are JSON-serializable

## Acceptance Criteria

- [ ] ACP client receives progressive `session/update` notifications during a turn
- [ ] Tool start/output/completion are visible in the ACP event stream
- [ ] Final assistant output appears through ACP update flow

## Dependencies

- Depends on: `01-acp-module-skeleton-and-jsonrpc-server.md`
- Depends on: `04-acp-session-new.md`
- Strongly related to: `06-acp-session-prompt.md`
