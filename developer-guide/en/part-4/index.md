# Part 4 · Event Layer & UI Integration

The only interface between the agent runtime and your UI is the **Transport**. This part bridges the agent event stream to any UI shape (CLI, web, native mobile, background batch).

## Coverage

- [**4.1 Transport Protocol**](./1-transport-protocol) — Four methods, three implementation paths, threading and async rules
- [**4.2 AgentEvent Reference**](./2-agent-events) — All 10 event types: triggers, payloads, typical use
- [**4.3 SdkTransport Bridging**](./3-sdk-transport) — Best practices and pitfalls for the official callback bridge
- [**4.4 Streaming UI**](./4-streaming-ui) — End-to-end SSE and WebSocket examples
- [**4.5 Tool Confirmation UI**](./5-tool-confirmation-ui) — CLI, web modal, mobile, unattended patterns
- [**4.6 Max-Iterations Fallback**](./6-max-iterations) — Five strategies + "stuck agent" detection heuristics

## Before you start

- [2.2 Constructor Reference](/en/part-2/2-constructor-reference) — semantics of the `transport` parameter
- [2.3 Lifecycle](/en/part-2/3-lifecycle) — `chat()` blocking semantics

## Mental model

> Transport is your "UI spokesperson" —
> the agent interacts with the outside world only through it:
> emit events, ask for confirmation, ask the user, report bailouts.
> The stronger your Transport, the steadier your UX.

→ [Start with 4.1 →](./1-transport-protocol)
