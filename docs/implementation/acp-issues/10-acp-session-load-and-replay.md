# Implement ACP Session Load And Replay Saved Session History As ACP Updates

## Problem

Agentao can persist and load sessions locally, but ACP requires protocol-level session loading and replay through `session/update`.

## Scope

- Implement `session/load`
- Reuse current session persistence
- Replay historical messages in ACP format

## Implementation Checklist

- [ ] Reuse `agentao/session.py` load path
- [ ] Define mapping from persisted messages to ACP replay events
- [ ] Replay user messages as user message updates
- [ ] Replay assistant messages as agent message updates
- [ ] Decide v1 handling for persisted tool messages
- [ ] Return success only after replay completes
- [ ] Return clear error when requested session does not exist

## Acceptance Criteria

- [ ] ACP client can load an existing session by ID
- [ ] Conversation history is replayed before the request completes
- [ ] Loaded session can continue receiving new prompts

## Dependencies

- Depends on: `03-acp-session-registry.md`
- Depends on: `07-acp-session-update-transport.md`
