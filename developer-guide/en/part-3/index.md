# Part 3 · ACP Protocol Embedding

**The cross-language embedding path**: any language that can spawn a subprocess and read/write stdio (Node / Go / Rust / Kotlin / Swift / C# / Java …) can drive Agentao as an ACP server.

## Coverage

- [**3.1 ACP Protocol Tour**](./1-acp-tour) — protocol positioning, relation to MCP, the four message quadrants, v1 capability boundaries
- [**3.2 Agentao as an ACP Server**](./2-agentao-as-server) — launch command, full method catalog, wire traces, minimal client example
- [**3.3 Host as ACP client architecture**](./3-host-client-architecture) — subprocess lifecycle, three-loop I/O, permission UI bridge, TypeScript + Go references
- [**3.4 Reverse: calling external ACP agents**](./4-reverse-acp-call) — `ACPManager.prompt_once()`, delegating sub-agents, `.agentao/acp.json`
- [**3.5 Zed / IDE integration walkthrough**](./5-zed-ide-integration) — Zed config, wire trace, multi-workspace, upgrade path

## Before you start

- [1.3 Integration Modes](/en/part-1/3-integration-modes) — confirm ACP is the right fit
- [1.4 Hello Agentao · Example B](/en/part-1/4-hello-agentao#example-b-acp-protocol-any-language) — hand-fed protocol messages

## Mental model

> ACP is **"LSP for agents"** —
> your host spawns `agentao --acp --stdio` as a subprocess
> and both sides speak NDJSON JSON-RPC 2.0 over the same stdio pair.
> Every message is visible, auditable, and replayable.

→ [Start with 3.1 →](./1-acp-tour)
