# Agentao Documentation

This directory is the documentation hub for Agentao, a governed agent runtime for local-first, private-first, embeddable AI agents.

Use the root [README.md](../README.md) for the product overview and first-run path. Use this page when you already know you want a document inside `docs/` and need the fastest route through runtime capabilities such as ACP, SDK embedding, governance, memory, and extensibility.

## Start Here

> **Not sure which surface to use?** Read [architecture/embedding-vs-acp.md](architecture/embedding-vs-acp.md) first — it disambiguates in-process embedding, ACP server, ACP client, and the ACP schema surface (all of which can combine).

Pick the path that matches your goal:

- Embed in your Python project (primary use case): [EMBEDDING.md](EMBEDDING.md) → [api/host.md](api/host.md)
- First-time CLI setup: [QUICKSTART.md](QUICKSTART.md)
- Daily command lookup: [QUICK_REFERENCE.md](QUICK_REFERENCE.md)
- Cross-language protocol entrypoints: [ACP.md](ACP.md)
- Logging and debugging: [LOGGING.md](LOGGING.md)
- Model and provider switching: [MODEL_SWITCHING.md](MODEL_SWITCHING.md)
- Skills and runtime extension: [SKILLS_GUIDE.md](SKILLS_GUIDE.md)
- Feature-specific behavior: see [Feature Guides](#feature-guides)
- Stable host-facing API surface (events, permissions snapshot, schema snapshots): see [API Reference](#api-reference)
- Embedded harness design records: see [Design Records](#design-records)
- Internal implementation notes: see [Implementation Notes](#implementation-notes)

## Table of Contents

### User Guides

- [EMBEDDING.md](EMBEDDING.md)
- [QUICKSTART.md](QUICKSTART.md)
- [QUICK_REFERENCE.md](QUICK_REFERENCE.md)
- [ACP.md](ACP.md)
- [LOGGING.md](LOGGING.md)
- [MODEL_SWITCHING.md](MODEL_SWITCHING.md)
- [SKILLS_GUIDE.md](SKILLS_GUIDE.md)
- [DEMO.md](DEMO.md)

### Feature Guides

- [features/memory-quickstart.md](features/memory-quickstart.md)
- [features/memory-management.md](features/memory-management.md)
- [features/acp-client.md](features/acp-client.md)
- [features/session-replay.md](features/session-replay.md)
- [features/TOOL_CONFIRMATION_FEATURE.md](features/TOOL_CONFIRMATION_FEATURE.md)
- [features/DATE_CONTEXT_FEATURE.md](features/DATE_CONTEXT_FEATURE.md)
- [features/CHATAGENT_MD_FEATURE.md](features/CHATAGENT_MD_FEATURE.md)
- [features/funds-data-cleaning-parallelism.md](features/funds-data-cleaning-parallelism.md)

### API Reference

- [api/host.md](api/host.md) — `agentao.host` host-facing contract: `ActivePermissions`, `ToolLifecycleEvent`, `SubagentLifecycleEvent`, `PermissionDecisionEvent`, `EventStream`
- [api/host.zh.md](api/host.zh.md) — Chinese mirror of the harness API doc
- [schema/host.events.v1.json](schema/host.events.v1.json) — checked-in JSON schema snapshot for the public events + permissions surface
- [schema/host.acp.v1.json](schema/host.acp.v1.json) — checked-in JSON schema snapshot for the host-facing ACP payloads

### Contributor and Internal Notes

- [design/embedded-host-contract.md](design/embedded-host-contract.md)
- [design/metacognitive-boundary.md](design/metacognitive-boundary.md)
- [implementation/TOOL_CONFIRMATION.md](implementation/TOOL_CONFIRMATION.md)
- [implementation/EMBEDDED_HARNESS_CONTRACT_IMPLEMENTATION_PLAN.md](implementation/EMBEDDED_HARNESS_CONTRACT_IMPLEMENTATION_PLAN.md)
- [implementation/ACP_CLIENT_PROJECT_SERVERS.md](implementation/ACP_CLIENT_PROJECT_SERVERS.md)
- [implementation/PLUGIN_SYSTEM_MVP_PLAN.md](implementation/PLUGIN_SYSTEM_MVP_PLAN.md)
- [implementation/SESSION_REPLAY_PLAN.md](implementation/SESSION_REPLAY_PLAN.md)
- [implementation/MARKETPLACE_PLUGIN_ORGANIZATION.md](implementation/MARKETPLACE_PLUGIN_ORGANIZATION.md)
- [implementation/SKILL_INSTALL_UPDATE_PLAN.md](implementation/SKILL_INSTALL_UPDATE_PLAN.md)
- [implementation/ACP_GITHUB_EPIC.md](implementation/ACP_GITHUB_EPIC.md)
- [implementation/READCHAR_IMPLEMENTATION.md](implementation/READCHAR_IMPLEMENTATION.md)
- [implementation/CLEAR_RESETS_CONFIRMATION.md](implementation/CLEAR_RESETS_CONFIRMATION.md)

### Historical Records

- [releases/](releases/)
- [updates/](updates/)
- [dev-notes/](dev-notes/)

## Recommended Reading Paths

### For Users

1. [../README.md](../README.md)
2. [QUICKSTART.md](QUICKSTART.md)
3. [QUICK_REFERENCE.md](QUICK_REFERENCE.md)
4. One deeper guide that matches the runtime surface you need:
   [ACP.md](ACP.md), [MODEL_SWITCHING.md](MODEL_SWITCHING.md), [SKILLS_GUIDE.md](SKILLS_GUIDE.md), or a file under [features/](features/)

### For Contributors

1. [../README.md](../README.md)
2. [../README.zh.md](../README.zh.md) if you maintain both language surfaces
3. The relevant user-facing doc in this directory
4. The matching file under [design/](design/) or [implementation/](implementation/)
5. The current release note under [releases/](releases/)

## User Guides

These are the primary user-facing documents in `docs/`.

| Document | When to use it |
|----------|----------------|
| [QUICKSTART.md](QUICKSTART.md) | You want the fastest setup path |
| [QUICK_REFERENCE.md](QUICK_REFERENCE.md) | You need a compact operator cheat sheet |
| [ACP.md](ACP.md) | You are running Agentao as an ACP server |
| [LOGGING.md](LOGGING.md) | You are debugging sessions, tool calls, or model behavior |
| [MODEL_SWITCHING.md](MODEL_SWITCHING.md) | You want to switch providers or models cleanly |
| [SKILLS_GUIDE.md](SKILLS_GUIDE.md) | You are creating, installing, or managing skills |
| [DEMO.md](DEMO.md) | You want a guided walkthrough or demo script |

## Feature Guides

These documents go deeper on specific shipped features.

| Document | Scope |
|----------|-------|
| [features/memory-quickstart.md](features/memory-quickstart.md) | Memory usage from a user perspective |
| [features/memory-management.md](features/memory-management.md) | Memory behavior and implementation details |
| [features/acp-client.md](features/acp-client.md) | Project-local ACP client/server management |
| [features/session-replay.md](features/session-replay.md) | Structured JSONL replay of runtime events |
| [features/TOOL_CONFIRMATION_FEATURE.md](features/TOOL_CONFIRMATION_FEATURE.md) | Tool confirmation behavior and safety model |
| [features/DATE_CONTEXT_FEATURE.md](features/DATE_CONTEXT_FEATURE.md) | Date/time context injection |
| [features/CHATAGENT_MD_FEATURE.md](features/CHATAGENT_MD_FEATURE.md) | Project instruction auto-loading |
| [features/funds-data-cleaning-parallelism.md](features/funds-data-cleaning-parallelism.md) | A feature-specific workflow note |

## API Reference

These documents are the stable host-facing contract for embedding Agentao. Hosts
that target only this surface stay forward-compatible across releases. Internal
runtime types (`AgentEvent`, `ToolExecutionResult`, `PermissionEngine`) are
intentionally outside this surface — see [Design Records](#design-records) for
the boundary rationale.

| Document | Scope |
|----------|-------|
| [api/host.md](api/host.md) | `agentao.host` package: `ActivePermissions`, `ToolLifecycleEvent`, `SubagentLifecycleEvent`, `PermissionDecisionEvent`, `EventStream`, schema export helpers; runtime identity contract; event delivery semantics |
| [api/host.zh.md](api/host.zh.md) | Chinese mirror of the harness API doc |
| [schema/host.events.v1.json](schema/host.events.v1.json) | Release schema snapshot for the public events + permissions surface; `tests/test_host_schema.py` asserts byte-equality |
| [schema/host.acp.v1.json](schema/host.acp.v1.json) | Release schema snapshot for the host-facing ACP payloads |

Schema snapshots are checked in. A model change that shifts the wire form must
update both the Pydantic model and the snapshot in the same PR.

## Design Records

These documents capture architecture decisions and host-facing contracts. They are
not implementation plans by themselves, but implementation plans should point back
to them when they affect public behavior.

| Document | Scope |
|----------|-------|
| [design/embedded-host-contract.md](design/embedded-host-contract.md) | Host-facing harness contract: schema discipline, event stream MVP, and CLI vs harness boundary |
| [design/metacognitive-boundary.md](design/metacognitive-boundary.md) | Host-injectable self-vs-project boundary protocol |

## Implementation Notes

These are contributor-oriented design and implementation documents. Some are plans or internal notes rather than current product-facing docs.

Treat these as engineering context, not the canonical user surface:

- [design/embedded-host-contract.md](design/embedded-host-contract.md)
- [design/metacognitive-boundary.md](design/metacognitive-boundary.md)
- [implementation/EMBEDDED_HARNESS_CONTRACT_IMPLEMENTATION_PLAN.md](implementation/EMBEDDED_HARNESS_CONTRACT_IMPLEMENTATION_PLAN.md)
- [implementation/TOOL_CONFIRMATION.md](implementation/TOOL_CONFIRMATION.md)
- [implementation/ACP_CLIENT_PROJECT_SERVERS.md](implementation/ACP_CLIENT_PROJECT_SERVERS.md)
- [implementation/PLUGIN_SYSTEM_MVP_PLAN.md](implementation/PLUGIN_SYSTEM_MVP_PLAN.md)
- [implementation/SESSION_REPLAY_PLAN.md](implementation/SESSION_REPLAY_PLAN.md)
- [implementation/MARKETPLACE_PLUGIN_ORGANIZATION.md](implementation/MARKETPLACE_PLUGIN_ORGANIZATION.md)
- [implementation/SKILL_INSTALL_UPDATE_PLAN.md](implementation/SKILL_INSTALL_UPDATE_PLAN.md)
- [implementation/ACP_GITHUB_EPIC.md](implementation/ACP_GITHUB_EPIC.md)
- [implementation/READCHAR_IMPLEMENTATION.md](implementation/READCHAR_IMPLEMENTATION.md)
- [implementation/CLEAR_RESETS_CONFIRMATION.md](implementation/CLEAR_RESETS_CONFIRMATION.md)

## Historical Records

These folders are useful for archaeology, release tracking, and old context, but they are not the primary source for current behavior.

- [releases/](releases/) stores versioned release notes
- [updates/](updates/) stores historical change logs
- [dev-notes/](dev-notes/) stores archived development summaries

## Documentation Policy

Use these rules when adding or updating docs:

1. Put current user-facing behavior in the root `README.md` and the matching guide under `docs/`.
2. Put major shipped features in `docs/features/`.
3. Put design drafts, deep implementation notes, and engineering plans in `docs/implementation/`.
4. Put release-specific summaries in `docs/releases/`.
5. Put historical development logs in `docs/updates/` or `docs/dev-notes/`, not in the main docs path.

## Related Entrypoints

- [../README.md](../README.md) for the main project overview
- [../README.zh.md](../README.zh.md) for the Chinese overview
