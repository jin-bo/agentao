# Agentao Documentation

This directory is the documentation hub for Agentao, a governed agent runtime for local-first, private-first, embeddable AI agents.

Use the root [README.md](../README.md) for the product overview and first-run path. Use this page when you already know you want a document inside `docs/` and need the fastest route through runtime capabilities such as ACP, SDK embedding, governance, memory, and extensibility.

## Layout

```
docs/
  start/        First-run setup, command cheat sheet, demo walkthrough
  guides/       Task-oriented how-to guides + per-feature docs
  reference/    Canonical reference: configuration, host API, replay policy
  design/       Architecture decisions, host-contract records, cross-repo reviews
  schema/       Checked-in JSON Schema snapshots (code-coupled; tests assert equality)
  releases/     Versioned release notes
  migration/    Version-to-version upgrade guides
  history/      Superseded plans, dev-notes, and old change logs (archaeology only)
```

## Start Here

> **Not sure which surface to use?** Read [design/embedding-vs-acp.md](design/embedding-vs-acp.md) first — it disambiguates in-process embedding, ACP server, ACP client, and the ACP schema surface (all of which can combine).

> **Are you a coding agent** (Claude Code, Codex, …) tasked with embedding Agentao into another project? Start with the distilled playbook [guides/embed-for-agents.md](guides/embed-for-agents.md), then follow its links into [guides/embedding.md](guides/embedding.md) and [reference/host-api.md](reference/host-api.md).

Pick the path that matches your goal:

- **Coding agent embedding Agentao into another project:** [guides/embed-for-agents.md](guides/embed-for-agents.md) (distilled, copy-paste playbook)
- Embed in your Python project (primary use case): [guides/embedding.md](guides/embedding.md) → [reference/host-api.md](reference/host-api.md)
- First-time CLI setup: [start/quickstart.md](start/quickstart.md)
- Daily command lookup: [start/quick-reference.md](start/quick-reference.md)
- Cross-language protocol entrypoints: [guides/acp.md](guides/acp.md)
- Configuration reference (every file, env var, public API): [reference/configuration.md](reference/configuration.md)
- Logging and debugging: [guides/logging.md](guides/logging.md)
- Model and provider switching: [guides/model-switching.md](guides/model-switching.md)
- Skills and runtime extension: [guides/skills.md](guides/skills.md)

## Start

| Document | When to use it |
|----------|----------------|
| [start/quickstart.md](start/quickstart.md) | You want the fastest setup path |
| [start/quick-reference.md](start/quick-reference.md) | You need a compact operator cheat sheet |
| [start/demo.md](start/demo.md) | You want a guided walkthrough or demo script |

## Guides

Task-oriented how-to guides and per-feature documentation.

| Document | When to use it |
|----------|----------------|
| [guides/embed-for-agents.md](guides/embed-for-agents.md) | You are a coding agent embedding Agentao into another project — distilled, copy-paste playbook |
| [guides/embedding.md](guides/embedding.md) | You are a developer embedding Agentao in a Python host — full reference |
| [guides/acp.md](guides/acp.md) | You are running Agentao as an ACP server |
| [guides/acp-client.md](guides/acp-client.md) | You manage project-local ACP client/servers |
| [guides/acp-embedding.md](guides/acp-embedding.md) | You embed the ACP surface specifically |
| [guides/logging.md](guides/logging.md) | You are debugging sessions, tool calls, or model behavior |
| [guides/model-switching.md](guides/model-switching.md) | You want to switch providers or models cleanly |
| [guides/skills.md](guides/skills.md) | You are creating, installing, or managing skills |
| [guides/memory-quickstart.md](guides/memory-quickstart.md) | Memory usage from a user perspective |
| [guides/memory-management.md](guides/memory-management.md) | Memory behavior and implementation details |
| [guides/session-replay.md](guides/session-replay.md) | Structured JSONL replay of runtime events |
| [guides/macos-sandbox-exec.md](guides/macos-sandbox-exec.md) | macOS `sandbox-exec` shell isolation |
| [guides/tool-confirmation.md](guides/tool-confirmation.md) | Tool confirmation behavior and safety model |
| [guides/date-context.md](guides/date-context.md) | Date/time context injection |
| [guides/chatagent-md.md](guides/chatagent-md.md) | Project instruction auto-loading (`AGENTAO.md`) |
| [guides/headless-runtime.md](guides/headless-runtime.md) | Non-interactive / headless runtime behavior |
| [guides/funds-data-cleaning-parallelism.md](guides/funds-data-cleaning-parallelism.md) | A feature-specific workflow note |

## Reference

Canonical reference material. The host API is the stable host-facing contract — hosts that target only this surface stay forward-compatible across releases.

| Document | Scope |
|----------|-------|
| [reference/configuration.md](reference/configuration.md) | Every config file, env var, and public API — the canonical schema-level reference ([中文](reference/configuration.zh.md)) |
| [reference/host-api.md](reference/host-api.md) | `agentao.host` package: `ActivePermissions`, `ToolLifecycleEvent`, `SubagentLifecycleEvent`, `PermissionDecisionEvent`, `EventStream`, schema export helpers ([中文](reference/host-api.zh.md)) |
| [reference/replay-schema-policy.md](reference/replay-schema-policy.md) | Stability policy for the replay JSONL schema |
| [schema/host.events.v1.json](schema/host.events.v1.json) | Release snapshot for the public events + permissions surface; `tests/test_host_schema.py` asserts byte-equality |
| [schema/host.acp.v1.json](schema/host.acp.v1.json) | Release snapshot for the host-facing ACP payloads |

Schema snapshots are checked in. A model change that shifts the wire form must update both the Pydantic model and the snapshot in the same PR.

## Design Records

Architecture decisions, host-facing contracts, and cross-repo reviews. These are not implementation plans by themselves, but plans should point back to them when they affect public behavior. Full set under [design/](design).

| Document | Scope |
|----------|-------|
| [design/embedded-host-contract.md](design/embedded-host-contract.md) | Host-facing harness contract: schema discipline, event stream, CLI vs harness boundary |
| [design/embedding-vs-acp.md](design/embedding-vs-acp.md) | Which surface to use: in-process embed vs ACP server vs ACP client |
| [design/metacognitive-boundary.md](design/metacognitive-boundary.md) | Host-injectable self-vs-project boundary protocol |
| [design/codex-reverse-review.md](design/codex-reverse-review.md) | Reverse review of Codex changes: adopt / done / defer |

## Releases & Migration

- [releases/](releases) — versioned release notes
- [migration/0.3.x-to-0.4.0.md](migration/0.3.x-to-0.4.0.md) — upgrade guides

## History

Superseded plans, archived dev-notes, and old change logs. Useful for archaeology and context, **not** the source of truth for current behavior.

- [history/implementation/](history/implementation) — engineering plans, GitHub epics, and per-issue breakdowns
- [history/dev-notes/](history/dev-notes) — archived development summaries and fix notes
- [history/updates/](history/updates) — historical change logs
- `history/headless-runtime-issues.md`, `history/headless-runtime-plan.md`, `history/kanban-acp-embedded-client-issue.md` — older planning notes

## Recommended Reading Paths

### For Users

1. [../README.md](../README.md)
2. [start/quickstart.md](start/quickstart.md)
3. [start/quick-reference.md](start/quick-reference.md)
4. One deeper guide that matches the runtime surface you need: [guides/acp.md](guides/acp.md), [guides/model-switching.md](guides/model-switching.md), [guides/skills.md](guides/skills.md), or another file under [guides/](guides)

### For Contributors

1. [../README.md](../README.md)
2. [../README.zh.md](../README.zh.md) if you maintain both language surfaces
3. The relevant user-facing doc under [guides/](guides) or [reference/](reference)
4. The matching record under [design/](design)
5. The current release note under [releases/](releases)

## Documentation Policy

Use these rules when adding or updating docs:

1. Put current user-facing behavior in the root `README.md` and the matching guide under `docs/guides/`.
2. Put canonical schema-level reference (config, host API) in `docs/reference/`.
3. Put architecture decisions and host-contract records in `docs/design/`.
4. Put release-specific summaries in `docs/releases/` and upgrade guides in `docs/migration/`.
5. Put superseded plans, dev-notes, and old change logs in `docs/history/`, not in the primary docs path.

## Related Entrypoints

- [../README.md](../README.md) for the main project overview
- [../README.zh.md](../README.zh.md) for the Chinese overview
