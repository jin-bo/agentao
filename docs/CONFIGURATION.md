# Configuration Reference

> **Purpose.** This is the *reference* for every configuration surface Agentao reads at startup or runtime: file paths, schema, defaults, and precedence. It deliberately does **not** explain *why* a feature exists or *how* to use it — each section links back to the corresponding feature doc for that.
>
> If you find yourself writing a paragraph of motivation here, move it to the feature doc and leave a one-line link.

中文版本：[CONFIGURATION.zh.md](CONFIGURATION.zh.md)。Both docs share the same structure, section numbering, and field tables — when editing one, update the other in the same change.

---

## 1. Configuration surfaces at a glance

User-facing configuration files (the surfaces you may hand-edit):

| # | Surface | Project path | User (global) path | Loader | Feature doc |
|---|---|---|---|---|---|
| 1 | LLM env | `.env` (cwd) | shell env | `dotenv.load_dotenv` → `discover_llm_kwargs` | — (see `.env.example`) |
| 2 | Runtime mode + builtin agents | `.agentao/settings.json` | — | `embedding/factory.py::_load_settings`, `plan/controller.py::_load_settings` | [TOOL_CONFIRMATION_FEATURE.md](features/TOOL_CONFIRMATION_FEATURE.md) |
| 3 | Tool permissions | — *(user-only; project file is ignored)* | `~/.agentao/permissions.json` | `permissions.py::PermissionEngine` | [TOOL_CONFIRMATION_FEATURE.md](features/TOOL_CONFIRMATION_FEATURE.md) |
| 4 | MCP servers | `.agentao/mcp.json` *(add-only — cannot override user-scope names)* | `~/.agentao/mcp.json` | `mcp/FileBackedMCPRegistry` (see `mcp/config.py`) | `CLAUDE.md` § MCP |
| 5 | ACP subagents | `.agentao/acp.json` | — *(project-only)* | `acp_client/config.py` | [acp-client.md](features/acp-client.md) / [acp-embedding.md](features/acp-embedding.md) |
| 6 | Skills disable list | `.agentao/skills_config.json` | — | `skills/manager.py` | [SKILLS_GUIDE.md](SKILLS_GUIDE.md) |
| 7 | Project instructions | `AGENTAO.md` (cwd) | — | `agent.py::_build_system_prompt` | [CHATAGENT_MD_FEATURE.md](features/CHATAGENT_MD_FEATURE.md) |
| 8 | Memory store | `.agentao/memory.db` | `~/.agentao/memory.db` | `memory/manager.py::MemoryManager` | [memory-management.md](features/memory-management.md) |

Internal state files (auto-managed; documented for awareness, not for editing):

| Surface | Path | Owner | Notes |
|---|---|---|---|
| Background sub-agent task state | `.agentao/background_tasks.json` | `agents/bg_store.py::BackgroundTaskStore` | Anchored to `working_directory`; in-memory only when no `persistence_dir`. Hand-edits will desync running threads. |
| Replay events | `.agentao/replay/*.jsonl` | `replay/` | See [session-replay.md](features/session-replay.md). |
| Sessions / plans / tool outputs | `.agentao/sessions/`, `.agentao/plan-history/`, `.agentao/tool-outputs/` | various | Per-session artifacts. |

**Precedence rules** (applies only to surfaces with both project and user variants):

- **Permissions** — user-scope file is the *only* rule source. A project-scope `.agentao/permissions.json` is **ignored** with a warning (a checked-in `{"tool": "*", "action": "allow"}` would defeat the user policy on the first match). After custom user rules, the active mode's **preset rules** run last (except in `full-access` / `plan` modes, where presets run **first** and cannot be overridden).
- **MCP** — both files are read. **Add-only for project scope**: a project entry may declare a *new* server name, but cannot override a user entry with the same name (warning + skip on collision). This prevents a checked-in `mcp.json` from silently redirecting a known server name (e.g. `github`) to a different transport.
- **Memory** — project DB and user DB are read **independently**; both are visible in the prompt. Project does not override user.
- All other user-facing surfaces are project-only — no merge.

---

## 2. `.env` — LLM provider configuration

- **Path.** `<cwd>/.env`. Loaded by `dotenv.load_dotenv()` at the top of `embedding/factory.py::build_from_environment`.
- **Loader.** `embedding/factory.py::discover_llm_kwargs`.
- **Mechanism.** Provider-prefixed: `LLM_PROVIDER` (default `OPENAI`) selects which `{PROVIDER}_API_KEY` / `{PROVIDER}_BASE_URL` / `{PROVIDER}_MODEL` triple is read.

### Schema

| Variable | Required | Default | Notes |
|---|---|---|---|
| `LLM_PROVIDER` | no | `OPENAI` | Selects the prefix for the three vars below. Examples: `OPENAI`, `DEEPSEEK`, `GEMINI`, `ANTHROPIC`. |
| `{PROVIDER}_API_KEY` | **yes** | — | Startup fails if missing. |
| `{PROVIDER}_BASE_URL` | **yes** | — | Startup fails with `ValueError` if missing. |
| `{PROVIDER}_MODEL` | **yes** | — | Startup fails with `ValueError` if missing. |
| `LLM_TEMPERATURE` | no | `0.2` | Range `0.0`–`2.0`. |
| `LLM_MAX_TOKENS` | no | — | Provider-default if unset. |
| `BOCHA_API_KEY` | no | — | If set, `web_search` uses Bocha; otherwise falls back to DuckDuckGo. |

> Canonical example: `.env.example` in the repo root.

---

## 3. `.agentao/settings.json` — runtime mode + builtin agents

- **Path.** `<cwd>/.agentao/settings.json` (project-only, no user variant).
- **Loaders.**
  - `embedding/factory.py::_load_settings` — reads `agents.enable_builtin` / `enable_builtin_agents` to default the constructor's `enable_builtin_agents` flag.
  - `plan/controller.py::_load_settings` — reads `mode` when restoring the active permission mode after a plan-mode session ends.
- **Failure mode.** Missing file or malformed JSON → silently treated as `{}` (no startup error).
- **Important.** The factory does **not** apply `mode` to the engine on startup; the `PermissionEngine` always initializes at `workspace-write`. The `mode` field is the *persisted last-known mode* used for restoration paths and CLI inspection — runtime mode changes go through CLI commands or `PermissionEngine.set_mode()`.

### Schema

```json
{
  "mode": "workspace-write",
  "agents": {
    "enable_builtin": false
  }
}
```

| Key | Type | Default | Allowed values | Notes |
|---|---|---|---|---|
| `mode` | string | `"workspace-write"` (when key absent) | `"read-only"`, `"workspace-write"`, `"full-access"` | `"plan"` is internal — set by `/plan` flow, never written by users. `"full-access"` disables all per-tool prompting; use deliberately. |
| `agents.enable_builtin` | bool | `false` | — | Enables the built-in sub-agent set. Legacy top-level alias `enable_builtin_agents` (bool) is still honored. |

See [TOOL_CONFIRMATION_FEATURE.md](features/TOOL_CONFIRMATION_FEATURE.md) for what each `mode` actually permits.

---

## 4. `permissions.json` — per-tool permission rules

- **Paths.**
  1. `~/.agentao/permissions.json` (user-level) — the only file-based rule source.
  2. `<cwd>/.agentao/permissions.json` (project-level) — **ignored** with a warning. See "Why no project scope?" below.
- **Loader.** `permissions.py::PermissionEngine._load_file`. Missing file or malformed JSON → empty rule list (no startup error). A file that loads successfully contributes a `loaded_sources` label (`user:<path>`) returned from `PermissionEngine.active_permissions()` and `Agentao.active_permissions()` — see [`docs/api/host.md`](api/host.md).
- **Public getter.** `PermissionEngine.active_permissions()` returns a cached, JSON-safe `ActivePermissions` snapshot (`mode`, `rules`, `loaded_sources`). Hosts that layer policy on top can call `add_loaded_source("injected:<name>")` so the snapshot reflects their provenance. The cache is invalidated on `set_mode()` and `add_loaded_source()`.
- **Evaluation order.**
  - Modes `read-only` / `workspace-write`: `[user rules] → [active mode preset rules]` (first match wins).
  - Modes `full-access` / `plan`: `[active mode preset rules] → [user rules]` — presets cannot be overridden.
  - No match → `decide()` returns `None`; the runner falls back to the tool's own `requires_confirmation` attribute.

> **Why no project scope?** Permissions are a user/host concern, not a cwd concern — same model OS permissions and IDE workspace-trust use. A checked-in project file with `{"tool": "*", "action": "allow"}` would defeat the entire user policy because the engine returns on the first matching rule. If you need project-aware policy, inject it from the host via `add_loaded_source("injected:<name>")` plus your own rule layer.

### Schema

```json
{
  "rules": [
    {"tool": "run_shell_command", "args": {"command": "^git "}, "action": "allow"},
    {"tool": "write_file", "action": "ask"},
    {"tool": "run_shell_command", "args": {"command": "rm\\s+-rf"}, "action": "deny"},
    {
      "tool": "web_fetch",
      "domain": {"allowlist": [".example.com"], "url_arg": "url"},
      "action": "allow"
    }
  ]
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `tool` | string | yes | Tool name; matched as a regex via `re.fullmatch` (use `"*"` for wildcard). |
| `args` | object | no | Map of `<arg_name>` → regex; **all** entries must `re.search`-match for the rule to fire. Bad regex falls back to literal equality. |
| `domain` | object | no | URL-tool only (`web_fetch`). Keys: `url_arg` (default `"url"`), `allowlist`, `blocklist`. Patterns starting with `.` do suffix matching (e.g. `.github.com` matches `github.com` and `api.github.com`); otherwise exact match. A rule with `domain` matches **only** when the hostname hits one of its lists. |
| `action` | string | yes | `"allow"` \| `"deny"` \| `"ask"` (case-insensitive; unknown values treated as `"ask"`). |

**Built-in presets** live in `permissions.py::_PRESET_RULES` and run after custom rules (or before, in `full-access` / `plan`):

- `workspace-write` — auto-allows `write_file` / `replace`; allowlists ~16 read-only shell commands (`ls`, `cat`, `grep`, `git status|log|diff|show|…`, …); denies `rm -rf` / `sudo` / `mkfs` / `dd if=`; allowlists trusted docs domains (`.github.com`, `.docs.python.org`, `.wikipedia.org`, `.pypi.org`, `.readthedocs.io`, `r.jina.ai`); blocklists SSRF targets (`localhost`, `127.0.0.1`, `0.0.0.0`, `169.254.169.254`, `.internal`, `.local`, `::1`); rest → ask.
- `read-only` — empty preset; `ToolRunner` short-circuits on `tool.is_read_only`.
- `full-access` — single rule `{"tool": "*", "action": "allow"}`.
- `plan` — denies all writes / memory mutations; allows the read-only shell allowlist; web rules identical to `workspace-write`.

Full rule taxonomy, examples, and runtime semantics → [TOOL_CONFIRMATION_FEATURE.md](features/TOOL_CONFIRMATION_FEATURE.md).

---

## 5. `mcp.json` — MCP server registry

- **Paths (load order).**
  1. `~/.agentao/mcp.json` (user-level) — authoritative for any name it declares.
  2. `<cwd>/.agentao/mcp.json` (project-level) — **add-only**: may declare new server names, but cannot override a user-scope name with the same key. Collisions log a warning and skip the project entry.
- **Loader.** `mcp/config.py`. Env vars in values are expanded (`$VAR` form).

### Schema

```json
{
  "mcpServers": {
    "<name>": {
      "command": "...",
      "args": ["..."],
      "env": { "TOKEN": "$MY_TOKEN" },
      "trust": false
    },
    "<remote-name>": {
      "url": "https://...",
      "headers": { "Authorization": "Bearer $API_KEY" },
      "timeout": 30
    }
  }
}
```

| Transport | Required keys | Optional |
|---|---|---|
| stdio subprocess | `command`, `args` | `env`, `trust`, `cwd` |
| SSE | `url` | `headers`, `timeout` |

Tools are registered as `mcp_{server}_{tool}`. See `CLAUDE.md` → "MCP" section for the full lifecycle.

> If MCP grows a dedicated user-facing feature doc (`features/mcp.md`), update this row's link in §1.

---

## 6. `.agentao/acp.json` — ACP subagent registry

- **Path.** `<cwd>/.agentao/acp.json` only. **No user-level variant** — ACP servers are explicitly project-scoped.
- **Loader.** `acp_client/config.py::load_acp_config` (parsed into `AcpServerConfig` via `acp_client/models.py::AcpServerConfig.from_dict`).
- **Failure mode.** Missing `command` / `args` / `env` / `cwd` raises `AcpConfigError` at config load — startup errors out.
- **Hot-reload.** The CLI watches the file's mtime; edits are picked up on the next inbox poll (`cli/acp_inbox.py`).

### Schema

```json
{
  "servers": {
    "<name>": {
      "command": "/abs/or/PATH/binary",
      "args": ["..."],
      "env": { "TOKEN": "$MY_TOKEN" },
      "cwd": ".",
      "description": "human-readable",
      "capabilities": { "chat": true, "web": true },
      "autoStart": true,
      "startupTimeoutMs": 10000,
      "requestTimeoutMs": 60000,
      "maxRecoverableRestarts": 3,
      "nonInteractivePolicy": { "mode": "reject_all" }
    }
  }
}
```

| Key | Type | Required | Default | Notes |
|---|---|---|---|---|
| `command` | string | yes | — | Absolute path or PATH-resolvable executable. |
| `args` | string[] | yes | — | Empty list `[]` is valid. |
| `env` | object | yes | — | Values support `$VAR` env-var expansion. |
| `cwd` | string | yes | — | Resolved to absolute against `project_root` if relative. |
| `description` | string | no | `""` | Free-form. |
| `capabilities` | object | no | `{}` | Free-form key/value (e.g. `chat`, `web`, `role: "worker"`). Not enforced by the loader. |
| `autoStart` | bool | no | `true` | If `false`, server stays cold until first call. |
| `startupTimeoutMs` | int | no | `10000` | Handshake budget. |
| `requestTimeoutMs` | int | no | `60000` | Per-request budget. |
| `maxRecoverableRestarts` | int | no | `3` | Cap for auto-restarts after recoverable subprocess deaths; reset on first successful turn. |
| `nonInteractivePolicy` | object | no | `{"mode": "reject_all"}` | Object form **only** — bare-string form is rejected with a migration error. Allowed `mode`: `"reject_all"`, `"accept_all"`. |

Full ACP semantics → [acp-client.md](features/acp-client.md) and [acp-embedding.md](features/acp-embedding.md).

---

## 7. `.agentao/skills_config.json` — disabled-skills list

- **Path.** `<cwd>/.agentao/skills_config.json` (project-only).
- **Loader.** `skills/manager.py`.

### Schema

```json
{
  "disabled_skills": []
}
```

| Key | Type | Notes |
|---|---|---|
| `disabled_skills` | string[] | Skill names to **exclude** from auto-discovery. Use `/skills disable <name>` from the CLI to manage. |

See [SKILLS_GUIDE.md](SKILLS_GUIDE.md) for skill discovery and activation rules.

---

## 8. `AGENTAO.md` — project instructions

- **Path.** `<cwd>/AGENTAO.md`. Optional.
- **Loader.** `agent.py::_build_system_prompt` — content is prepended to the system prompt when present.
- **Schema.** Free-form Markdown; no required structure.

See [CHATAGENT_MD_FEATURE.md](features/CHATAGENT_MD_FEATURE.md) for prompt-composition rules and conventions.

---

## 9. Memory stores (`memory.db`)

- **Paths.**
  - Project: `<cwd>/.agentao/memory.db`
  - User: `~/.agentao/memory.db`
- **Format.** SQLite; schema owned by `memory/manager.py::MemoryManager`. Not hand-edited.
- **Precedence.** Both DBs are read independently; both are visible to the prompt renderer. Project memory does not override user memory.

Full schema, tables, and lifecycle → [memory-management.md](features/memory-management.md).

---

## Appendix A — Adding a new configuration surface

When adding a new config file, update **both**:

1. This file (row in §1, full section like §3–§10).
2. The corresponding feature doc — keep the *why* and *how to use* there, not here.

Checklist:

- [ ] Row added to §1 with path, scope, loader, feature-doc link.
- [ ] Schema documented with required/optional/default for every key.
- [ ] Precedence rule stated if both project and user variants exist.
- [ ] Loader file path included so readers can verify behavior in code.
- [ ] Feature doc cross-link added (or `<!-- TODO -->` if the doc does not exist yet).
