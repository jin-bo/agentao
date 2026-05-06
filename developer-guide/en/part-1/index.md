# Part 1 · Getting Started & Mental Model

This part helps you answer three questions quickly: what Agentao is, whether it fits your integration shape, and which path to start with.

::: info Key terms in this Part
You only need these terms on the first pass. Full definitions live in [Appendix G](/en/appendix/g-glossary).
- **Agentao** — an embeddable Python agent runtime; call it directly from Python or drive it from another language through ACP · [§1.1](./1-what-is-agentao)
- **Tool / Skill / MCP** — the common capability extensions: business functions, LLM-side instructions, external tool ecosystem · [§1.2](./2-core-concepts), [Part 5](/en/part-5/)
- **Transport** — the bridge between runtime and UI: streaming events, tool confirmation, user questions, max-iteration fallback · [§1.2](./2-core-concepts), [Part 4](/en/part-4/)
- **Python SDK** — in-process embedding for Python backends, data services, and batch jobs · [§1.3](./3-integration-modes), [Part 2](/en/part-2/)
- **ACP** — stdio JSON-RPC protocol embedding for IDE plugins, Node/Go/Rust hosts, and process-isolated deployments · [§1.3](./3-integration-modes), [Part 3](/en/part-3/)
:::

## Coverage

- [**1.1 What is Agentao**](./1-what-is-agentao) — product boundary, built-in capabilities, where it fits and where it does not
- [**1.2 Core Concepts**](./2-core-concepts) — Agent, Tool, Skill, Transport, Session, Working Directory
- [**1.3 Integration Modes**](./3-integration-modes) — Python SDK vs ACP, chosen by host language and isolation needs
- [**1.4 Hello Agentao in 5 min**](./4-hello-agentao) — run a minimal working session first
- [**1.5 Requirements**](./5-requirements) — Python version, extras, credentials, OS, network, and disk layout

## How to read

| Your situation | Recommended path |
|----------------|------------------|
| I just want to run it | [1.4 Hello](./4-hello-agentao) → [1.2 Core Concepts](./2-core-concepts) |
| I need to decide whether it fits | [1.1 What is Agentao](./1-what-is-agentao) → [1.3 Integration Modes](./3-integration-modes) |
| My host is Python | [1.4 Hello](./4-hello-agentao) → [Part 2](/en/part-2/) |
| My host is not Python / I need process isolation | [1.3 Integration Modes](./3-integration-modes) → [Part 3](/en/part-3/) |
| I am preparing for production | [1.5 Requirements](./5-requirements) → [Part 6](/en/part-6/) |

## Mental model

> Agentao is not a chat UI and not a one-shot function call.
> It is the agent runtime inside your application:
> session state, tool calls, permissions, memory, event streams, and cross-language protocol.
> Your host owns the product experience, business policy, and deployment boundary.

→ [Start with 1.1 →](./1-what-is-agentao)
