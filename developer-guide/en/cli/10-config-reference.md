# 10. Configuration Reference

This is a CLI-centric **index** to every config file the CLI reads. The schema reference for each file lives in [`docs/CONFIGURATION.md`](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.md) — every row below links to the right section there.

## All config surfaces at a glance

| File | Project path | User-global path | Used by | Schema reference |
|------|--------------|------------------|---------|------------------|
| **LLM credentials** | `.env` (cwd) | shell env | `/model`, `/provider`, `/temperature` | [§2](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.md#2-env--llm-provider-configuration) |
| **Runtime settings** | `.agentao/settings.json` | — | `/mode` (persist), `/replay on/off` | [§3](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.md#3-agentaosettingsjson--runtime-mode--builtin-agents) |
| **Permission rules** | — *(project file ignored)* | `~/.agentao/permissions.json` | `/mode`, `/permission`, tool confirmation UI | [§4](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.md#4-permissionsjson--tool-permission-rules) |
| **Shell sandbox** | `.agentao/sandbox.json` | `~/.agentao/sandbox.json` | `/sandbox` | [Part 6.2](/en/part-6/2-shell-sandbox) |
| **MCP servers** | `.agentao/mcp.json` | `~/.agentao/mcp.json` | `/mcp` | [§5](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.md#5-mcpjson--mcp-server-registry) |
| **ACP servers** | `.agentao/acp.json` | — | `/acp` | [§6](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.md#6-acpjson--acp-subagent-registry) |
| **Skill enable/disable** | `.agentao/skills_config.json` | — | `/skills enable`, `/skills disable` | [§7](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.md#7-skills_configjson--per-project-skill-enabledisable) |
| **Project instructions** | `AGENTAO.md` (cwd) | — | system prompt every turn | [§8](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.md#8-agentaomd--project-instructions) |
| **Memory store** | `.agentao/memory.db` | `~/.agentao/memory.db` | `/memory`, `save_memory` tool | [§9](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.md#9-memorydb--persistent-memory-store) |

## "Where do I change X?" cheat-sheet

| What you want to change | Edit |
|-------------------------|------|
| Default model / API key | `.env` (`OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BASE_URL`) |
| Add a second provider | `.env` (`GEMINI_API_KEY` etc. — see [chapter 2](./2-models-providers)) |
| Default temperature | `.env` (`LLM_TEMPERATURE`) |
| Default permission mode for fresh sessions | `.agentao/settings.json` → `mode` *(persisted-last-known, see [§3](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.md#3-agentaosettingsjson--runtime-mode--builtin-agents))* |
| Allow / deny extra shell commands | `~/.agentao/permissions.json` |
| Allow / deny extra web domains | `~/.agentao/permissions.json` |
| Default sandbox profile (macOS) | `.agentao/sandbox.json` or `~/.agentao/sandbox.json` → `default_profile` |
| Default context window | `AGENTAO_CONTEXT_TOKENS` environment variable |
| Default replay recording state | `.agentao/settings.json` → `replay.enabled` |
| Replay max instances | `.agentao/settings.json` → `replay.max_instances` |
| Add MCP servers | `.agentao/mcp.json` (or `/mcp add` — same file) |
| Add ACP servers | `.agentao/acp.json` |
| Disable a buggy skill globally | `.agentao/skills_config.json` (or `/skills disable <name>` — same file) |
| Project-specific behavior the agent should always follow | `AGENTAO.md` (or copy from [`examples/personas/`](https://github.com/jin-bo/agentao/tree/main/examples/personas)) |

## Runtime overrides vs. config files

A few things you set with slash commands are **session-only** and don't write to disk:

| Slash command | Persisted? | Where if persisted |
|---------------|-----------|---------------------|
| `/model <name>` | No | — (use `OPENAI_MODEL` in `.env` for default) |
| `/provider <name>` | No | — (set provider env triple) |
| `/temperature <n>` | No | `.env` (`LLM_TEMPERATURE`) |
| `/mode <mode>` | No | `.agentao/settings.json` (last-known) |
| `/context limit <n>` | No | — (current process only; restart reads `AGENTAO_CONTEXT_TOKENS`) |
| `/sandbox profile <name>` | No | — (edit `sandbox.json` to persist) |
| `/replay on` / `/replay off` | **Yes** | `.agentao/settings.json` (`replay.enabled`) |
| `/skills disable <name>` / `/skills enable <name>` | **Yes** | `.agentao/skills_config.json` |
| `/skills activate` / `/skills deactivate` | No | — |
| `/mcp add` / `/mcp remove` | **Yes** | `.agentao/mcp.json` (project) |
| `/memory delete` / `/memory clear` | **Yes** | `memory.db` (soft-delete) |

Most "feels temporary" commands are temporary by design. If you want a setting to outlive the session, edit the file.

## Precedence summary

Three things to know:

1. **Permissions are user-only.** A project-level `permissions.json` would let any cloned repo override your security policy, so the loader **ignores** it (with a warning). Edit `~/.agentao/permissions.json` for personal rules.
2. **MCP merges, but project is add-only.** A project entry can declare a *new* server name, but cannot redefine a user-defined `github` to point at a different transport. Same-name collisions are skipped with a warning.
3. **Memory is independent per scope.** Project DB and user DB are both injected into the prompt; project doesn't override user. Use `/memory user` and `/memory project` to inspect each.

For the full precedence rules see [`docs/CONFIGURATION.md` §1](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.md#1-configuration-surfaces-at-a-glance).

## `AGENTAO.md` persona gallery

If you don't want to write an `AGENTAO.md` from scratch, the repo ships a small persona gallery under [`examples/personas/`](https://github.com/jin-bo/agentao/tree/main/examples/personas). Each persona is a single `AGENTAO.md` you copy into your project root.

| Persona | Vibe | Use when |
|---------|------|----------|
| [`daily-driver/`](https://github.com/jin-bo/agentao/blob/main/examples/personas/daily-driver/AGENTAO.md) | Evidence-first, privacy-conscious, workspace-organized | Day-to-day research / coding assistant |
| [`kawaii-buddy/`](https://github.com/jin-bo/agentao/blob/main/examples/personas/kawaii-buddy/AGENTAO.md) | Cute, bilingual chatter, asks how you feel | Emotional-value pocket helper |

```bash
# Pick one and drop into the project you launch agentao from
cp examples/personas/daily-driver/AGENTAO.md /path/to/your/project/AGENTAO.md
```

`AGENTAO.md` is recomposed into the system prompt every turn — edits apply on the next message, no restart needed. Treat the gallery as starting points, not contracts; rewrite freely.

## Internal state files

Files that exist under `.agentao/` but you should **not** hand-edit:

| Path | Purpose |
|------|---------|
| `.agentao/background_tasks.json` | Sub-agent state; in-memory mirror exists |
| `.agentao/replay/*.jsonl` | Replay recordings |
| `.agentao/sessions/` | Per-session artifacts |
| `.agentao/plan.md`, `.agentao/plan-history/` | Plan-mode state |
| `.agentao/tool-outputs/` | Cached tool outputs |

Editing them while the CLI is running can desync state. Stop the CLI first if you must.

## Where to go next

| Want to… | Read |
|----------|------|
| Full schema reference for any of the files above | [`docs/CONFIGURATION.md`](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.md) |
| Change default permission rules with intent | [Part 5.4 · Permissions](/en/part-5/4-permissions) |
| Author an `AGENTAO.md` for your project | [Part 5.6 · System Prompt Customization](/en/part-5/6-system-prompt) |

---

::: info Where this fits
The same files are loaded identically when embedding. `.env` becomes constructor kwargs (or you can pass them directly), `permissions.json` is consumed by the same `PermissionEngine`, `mcp.json` / `acp.json` by the same managers. The split between "host configures programmatically" and "user edits files" is up to your application.
:::

::: tip Authoritative reference
Schemas: [`docs/CONFIGURATION.md`](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.md) (English) · [`docs/CONFIGURATION.zh.md`](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.zh.md) (Chinese). Loaders: see the linked sections above. The schema file is the single source of truth for field names and defaults — this index just tells you which row to look at.
:::
