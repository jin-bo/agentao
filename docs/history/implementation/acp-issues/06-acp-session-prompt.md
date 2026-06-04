# Implement ACP Session Prompt With ContentBlock To Agentao Message Conversion

## Problem

ACP prompt requests use structured content blocks, but Agentao currently accepts mostly string user input at its runtime boundary.

## Scope

- Implement `session/prompt`
- Convert ACP content blocks into internal user message text
- Run Agentao chat loop for the session

## Implementation Checklist

- [ ] Parse ACP `ContentBlock[]`
- [ ] Support `text` blocks in v1
- [ ] Support `resource_link` blocks with a documented minimal mapping
- [ ] Reject unsupported block types with a clear error
- [ ] Serialize prompt execution per session
- [ ] Create and bind a fresh cancellation token for each active turn
- [ ] Return final completion state when turn finishes

## Acceptance Criteria

- [ ] ACP text prompts execute successfully
- [ ] Unsupported content types fail explicitly rather than silently degrading
- [ ] Concurrent prompts to the same session do not corrupt runtime state

## Dependencies

- Depends on: `04-acp-session-new.md`
- Strongly related to: `05-per-session-working-directory.md`
