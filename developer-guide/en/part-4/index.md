# Part 4 · Event Layer & UI Integration

The only interface between the agent runtime and your UI is the **Transport**. This part bridges the agent event stream to any UI shape (CLI, web, native mobile, background batch).

::: info Key terms in this Part
- **Transport** — push interface (`emit` / `ask_confirmation` / `ask_user` / `bailout`); the only seam between runtime and your UI · [§4.1](/en/part-4/1-transport-protocol), [G.2](/en/appendix/g-glossary#g-2-extension-points)
- **AgentEvent** — internal event types (text chunk, tool started/completed, LLM call) — debug only, **not stable across releases** · [§4.2](/en/part-4/2-agent-events), [G.6](/en/appendix/g-glossary#g-6-event-types-quick-reference)
- **HostEvent** — Pydantic-typed lifecycle event (tool / permission / subagent); **stable**, with schema snapshots · [§4.7](/en/part-4/7-host-contract), [G.1](/en/appendix/g-glossary#g-1-core-concepts)
- **`agent.events()`** — async pull iterator on the *stable* `agentao.host` surface; use for audit / SIEM / billing · [§4.7](/en/part-4/7-host-contract)
- **`active_permissions()`** — JSON-safe snapshot of the effective policy; use for "who can do what" UIs · [§4.7](/en/part-4/7-host-contract#the-active-permissions-snapshot), [G.5](/en/appendix/g-glossary#g-5-security-vocabulary)
:::

## Coverage

- [**4.1 Transport Protocol**](./1-transport-protocol) — Four methods, three implementation paths, threading and async rules
- [**4.2 AgentEvent Reference**](./2-agent-events) — UI, tool, LLM, replay, and state-change events
- [**4.3 SdkTransport Bridging**](./3-sdk-transport) — Best practices and pitfalls for the official callback bridge
- [**4.4 Streaming UI**](./4-streaming-ui) — End-to-end SSE and WebSocket examples
- [**4.5 Tool Confirmation UI**](./5-tool-confirmation-ui) — CLI, web modal, mobile, unattended patterns
- [**4.6 Max-Iterations Fallback**](./6-max-iterations) — Five strategies + "stuck agent" detection heuristics
- [**4.7 Embedded Harness Contract**](./7-host-contract) — `agent.events()` + `active_permissions()` — the **stable host API** for production audit / observability pipelines

## Before you start

- [2.2 Constructor Reference](/en/part-2/2-constructor-reference) — semantics of the `transport` parameter
- [2.3 Lifecycle](/en/part-2/3-lifecycle) — `chat()` blocking semantics

## Mental model

> Transport is your "UI spokesperson" —
> the agent interacts with the outside world only through it:
> emit events, ask for confirmation, ask the user, report bailouts.
> The stronger your Transport, the steadier your UX.

→ [Start with 4.1 →](./1-transport-protocol)
