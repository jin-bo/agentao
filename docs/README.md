# Agentao Documentation

This directory is the documentation hub for Agentao.

Use the root [README.md](../README.md) for the product overview and first-run path. Use this page when you already know you want a document inside `docs/` and need the fastest route.

## Start Here

Pick the path that matches your goal:

- First-time setup: [QUICKSTART.md](QUICKSTART.md)
- Daily command lookup: [QUICK_REFERENCE.md](QUICK_REFERENCE.md)
- ACP server mode: [ACP.md](ACP.md)
- Logging and debugging: [LOGGING.md](LOGGING.md)
- Model and provider switching: [MODEL_SWITCHING.md](MODEL_SWITCHING.md)
- Skills workflow: [SKILLS_GUIDE.md](SKILLS_GUIDE.md)
- Feature-specific behavior: see [Feature Guides](#feature-guides)
- Internal implementation notes: see [Implementation Notes](#implementation-notes)

## Table of Contents

### User Guides

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
- [features/TOOL_CONFIRMATION_FEATURE.md](features/TOOL_CONFIRMATION_FEATURE.md)
- [features/DATE_CONTEXT_FEATURE.md](features/DATE_CONTEXT_FEATURE.md)
- [features/CHATAGENT_MD_FEATURE.md](features/CHATAGENT_MD_FEATURE.md)
- [features/funds-data-cleaning-parallelism.md](features/funds-data-cleaning-parallelism.md)

### Contributor and Internal Notes

- [implementation/TOOL_CONFIRMATION.md](implementation/TOOL_CONFIRMATION.md)
- [implementation/ACP_CLIENT_PROJECT_SERVERS.md](implementation/ACP_CLIENT_PROJECT_SERVERS.md)
- [implementation/PLUGIN_SYSTEM_MVP_PLAN.md](implementation/PLUGIN_SYSTEM_MVP_PLAN.md)
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
4. One deeper guide that matches your task:
   [ACP.md](ACP.md), [MODEL_SWITCHING.md](MODEL_SWITCHING.md), [SKILLS_GUIDE.md](SKILLS_GUIDE.md), or a file under [features/](features/)

### For Contributors

1. [../README.md](../README.md)
2. [../README.zh.md](../README.zh.md) if you maintain both language surfaces
3. The relevant user-facing doc in this directory
4. The matching file under [implementation/](implementation/)
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
| [features/TOOL_CONFIRMATION_FEATURE.md](features/TOOL_CONFIRMATION_FEATURE.md) | Tool confirmation behavior and safety model |
| [features/DATE_CONTEXT_FEATURE.md](features/DATE_CONTEXT_FEATURE.md) | Date/time context injection |
| [features/CHATAGENT_MD_FEATURE.md](features/CHATAGENT_MD_FEATURE.md) | Project instruction auto-loading |
| [features/funds-data-cleaning-parallelism.md](features/funds-data-cleaning-parallelism.md) | A feature-specific workflow note |

## Implementation Notes

These are contributor-oriented design and implementation documents. Some are plans or internal notes rather than current product-facing docs.

Treat these as engineering context, not the canonical user surface:

- [implementation/TOOL_CONFIRMATION.md](implementation/TOOL_CONFIRMATION.md)
- [implementation/ACP_CLIENT_PROJECT_SERVERS.md](implementation/ACP_CLIENT_PROJECT_SERVERS.md)
- [implementation/PLUGIN_SYSTEM_MVP_PLAN.md](implementation/PLUGIN_SYSTEM_MVP_PLAN.md)
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
