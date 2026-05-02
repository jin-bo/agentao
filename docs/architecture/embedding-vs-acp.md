# Embedding vs. ACP — Which Surface Do I Use?

**Audience:** Anyone integrating Agentao into another system.
**TL;DR:** "ACP" names three different things in this repo. They are *orthogonal* to in-process embedding, not alternatives to it. You can combine any of them.

中文版：[embedding-vs-acp.zh.md](embedding-vs-acp.zh.md).

## The four surfaces

| Surface | What it is | Entry point | Reference |
|---|---|---|---|
| **In-process embedding** | Run Agentao as a Python library inside your own process. Drive turns with `Agentao.arun()`; observe with `agent.events()`; gate with the permission engine. | `from agentao import Agentao` | [EMBEDDING.md](../EMBEDDING.md), [api/host.md](../api/host.md) |
| **ACP server** | Run Agentao as a standalone subprocess speaking JSON-RPC 2.0 over stdio. External clients (Zed, Cursor, IDE extensions) launch and drive it. | `agentao --acp --stdio` | [ACP.md](../ACP.md) |
| **ACP client** | Embed Agentao in your runtime *and* have it call out to other ACP-speaking agents (Claude Code, Codex, …) as backends for specific roles. | `from agentao.acp_client import ACPManager` | [features/acp-client.md](../features/acp-client.md) |
| **ACP schema surface** | Versioned Pydantic types for ACP wire payloads, plus the checked-in `host.acp.v1.json` snapshot. Only relevant if your in-process host *also* exposes Agentao via ACP to its own clients. | `from agentao.host import export_host_acp_json_schema` | [api/host.md §Schema snapshot policy](../api/host.md#schema-snapshot-policy) |

## Decision tree

```
Do you control a Python process and want Agentao inside it?
│
├── Yes → In-process embedding.
│         Drive with `Agentao.arun()`, observe with
│         `agent.events()`, gate with the permission engine.
│         Stop here unless one of the boxes below also applies.
│
│         Optional: do you also re-expose your embedded Agentao
│         to your own clients via the ACP wire format?
│         ├── No  → done.
│         └── Yes → also import the ACP schema surface for
│                   versioned Pydantic types.
│
└── No, my host is not a Python process — it's an editor, IDE
    extension, sandbox runner, evaluation harness in another
    language, etc.
    │
    ├── I want to drive Agentao  → ACP server. Launch
    │                               `agentao --acp --stdio` as a
    │                               subprocess and speak ACP to it.
    │
    └── I want Agentao to drive  → ACP client. Embed `ACPManager`
        other agents on my behalf  in your runtime and configure
                                   the upstream agents in
                                   `.agentao/acp.json`.
```

Combinations are common. For example, a Kanban-style workflow runtime might **embed Agentao in-process** *and* use the **ACP client** to delegate "reviewer" roles to a Codex backend.

## Why this confusion exists

Three different code paths share the "ACP" name:

1. `agentao/acp/` — the *server* package. Implements `agentao --acp --stdio`.
2. `agentao/acp_client/` — the *client* package (`ACPManager`). Used by workflow runtimes to delegate to other agents.
3. `agentao/host/` — re-exports the ACP Pydantic models (and only those) so embedded hosts that also speak ACP get versioned types. **This is not a way to talk to Agentao** — Agentao is already in your process.

If you only need in-process embedding, you can ignore (1), (2), and the ACP exports of (3) entirely. The host-contract pillars you'll actually use are `agent.events()`, `agent.active_permissions()`, and the lifecycle event models.

## See also

- [docs/EMBEDDING.md](../EMBEDDING.md) — in-process embedding tutorial.
- [docs/api/host.md](../api/host.md) — public host API reference.
- [docs/design/embedded-host-contract.md](../design/embedded-host-contract.md) — design record for the host contract.
- [docs/ACP.md](../ACP.md) — ACP server reference.
- [docs/features/acp-client.md](../features/acp-client.md) — ACP client reference.
