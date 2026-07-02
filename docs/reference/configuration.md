# Configuration Reference

> **Purpose.** This is the *reference* for every configuration surface Agentao reads at startup or runtime: file paths, schema, defaults, and precedence. It deliberately does **not** explain *why* a feature exists or *how* to use it — each section links back to the corresponding feature doc for that.
>
> If you find yourself writing a paragraph of motivation here, move it to the feature doc and leave a one-line link.

中文版本：[CONFIGURATION.zh.md](configuration.zh.md)。Both docs share the same structure, section numbering, and field tables — when editing one, update the other in the same change.

> **Quick check.** To verify any of the files below without booting an agent, run `agentao config validate` (or `agentao doctor` for plugin / ACP / optional-dep coverage as well). Both support `--json`; errors exit 1, warnings exit 0. See [developer-guide CLI ch. 12](https://github.com/jin-bo/agentao/blob/main/developer-guide/en/cli/12-non-interactive.md#agentao-doctor-health-snapshot).

---

## 1. Configuration surfaces at a glance

User-facing configuration files (the surfaces you may hand-edit):

| # | Surface | Project path | User (global) path | Loader | Feature doc |
|---|---|---|---|---|---|
| 1 | LLM env | `.env` (cwd) | shell env | `dotenv.load_dotenv` → `discover_llm_kwargs` | — (see `.env.example`) |
| 2 | Runtime mode + builtin agents | `.agentao/settings.json` | — | `embedding/factory.py::_load_settings`, `plan/controller.py::_load_settings` | [TOOL_CONFIRMATION_FEATURE.md](../guides/tool-confirmation.md) |
| 3 | Tool permissions | — *(user-only; project file is ignored)* | `~/.agentao/permissions.json` | `permissions.py::PermissionEngine` | [TOOL_CONFIRMATION_FEATURE.md](../guides/tool-confirmation.md) |
| 4 | MCP servers | `.agentao/mcp.json` *(add-only — cannot override user-scope names)* | `~/.agentao/mcp.json` | `mcp/FileBackedMCPRegistry` (see `mcp/config.py`) | `CLAUDE.md` § MCP |
| 5 | ACP subagents | `.agentao/acp.json` | — *(project-only)* | `acp_client/config.py` | [acp-client.md](../guides/acp-client.md) / [acp-embedding.md](../guides/acp-embedding.md) |
| 6 | Skills disable list | `.agentao/skills_config.json` | — | `skills/manager.py` | [SKILLS_GUIDE.md](../guides/skills.md) |
| 7 | Project instructions | `AGENTAO.md` (cwd) | — | `agent.py::_build_system_prompt` | [CHATAGENT_MD_FEATURE.md](../guides/chatagent-md.md) |
| 8 | Memory store | `.agentao/memory.db` | `~/.agentao/memory.db` | `memory/manager.py::MemoryManager` | [memory-management.md](../guides/memory-management.md) |
| 9 | Run spec (`agentao run`) | any path passed to `--spec` (or stdin) | — | `cli/run_models.py::RunSpec`, `cli/run_template.py::render_spec` | [run-spec-parameters.md](../design/run-spec-parameters.md) |

Internal state files (auto-managed; documented for awareness, not for editing):

| Surface | Path | Owner | Notes |
|---|---|---|---|
| Background sub-agent task state | `.agentao/background_tasks.json` | `agents/bg_store.py::BackgroundTaskStore` | Anchored to `working_directory`; in-memory only when no `persistence_dir`. Hand-edits will desync running threads. |
| Replay events | `.agentao/replay/*.jsonl` | `replay/` | See [session-replay.md](../guides/session-replay.md). |
| Sessions / plans / tool outputs | `.agentao/sessions/`, `.agentao/plan-history/`, `.agentao/tool-outputs/` | various | Per-session artifacts. |
| `/goal` continuation state | `.agentao/goal.json` | `cli/goal_state.py::GoalState` | One long-task goal (objective, status, time/turn caps, usage). Survives restarts; corrupt/missing → treated as no goal. See [goal.md](../guides/goal.md). |

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
| `LLM_TEMPERATURE` | no | `0.2` | Range `0.0`–`2.0`. Malformed value **raises** at startup (`float()`). |
| `LLM_MAX_TOKENS` | no | — | Provider-default if unset. Malformed value **raises** at startup (`int()`). |
| `LLM_EXTRA_BODY` | no | — | JSON **object** forwarded verbatim to the LLM `.create()` call as the SDK's `extra_body` request option — the escape hatch for params the closed request build does not expose (`reasoning_effort`, `top_p`, `seed`, `response_format`, and provider-specific fields). Example: `LLM_EXTRA_BODY='{"reasoning_effort":"high"}'`. Unlike the two above, a malformed *or* valid-but-non-object value is **warned and skipped** (not a startup error). The host configures its own endpoint; the SDK/provider validates the values. Logged with credential-like keys redacted. See `docs/design/host-llm-extra-params.md`. |
| `BOCHA_API_KEY` | no | — | `web_search` runs an ordered fallback chain that retreats to the next backend on **error** (not on an empty result): `jina` (if `JINA_API_KEY`) → `bocha` (if `BOCHA_API_KEY`) → `duckduckgo` (keyless, always the last resort). Each fallback is surfaced in the result and logged. Pinning `WebSearchTool(backend="bocha"\|"jina"\|"duckduckgo")` selects exactly that one backend with **no** auto-fallback. |
| `AGENTAO_WEB_FETCH_FALLBACK` | no | `none` | JS-rendering fallback for `web_fetch`. Allowed: `none` / `jina` / `crawl4ai`. Default `none` — the tool never silently proxies user-supplied URLs through a third party. `jina` sends the URL to `https://r.jina.ai` (disclosed in tool description + result `Fallback:` line). `crawl4ai` requires `pip install 'agentao[crawl4ai]'` + `playwright install chromium`. Read once at `WebFetchTool` construction; invalid values warn and degrade to `none`. |
| `AGENTAO_WEB_FETCH_ALLOW_CIDRS` | no | — | Opt-in SSRF allowlist for `web_fetch`: comma/space-separated CIDRs (or bare IPs) that the URL policy permits **even though they are not globally routable**. The escape hatch for hosts behind a fake-IP proxy (Clash/V2Ray map every domain to a reserved range, typically `198.18.0.0/15`, which the guard otherwise blocks) or a trusted internal service. Applied to the initial URL **and every redirect hop**; the allowlist is *scoped* — listing `198.18.0.0/15` does **not** also permit `169.254.169.254`. **Relaxes a security control** — logged once at startup, surfaced in the tool description; keep it narrow, never `0.0.0.0/0`. Default empty = fully strict. (Cleaner fix: switch the proxy DNS from `fake-ip` to `redir-host`/real-IP.) See `agentao/security/url_policy.py`. |
| `JINA_API_KEY` | no | — | Optional Jina key, sent as `Authorization: Bearer <key>` for higher rate limits. Used by **`web_search`** (the `jina` backend via `s.jina.ai` — works keyless, the key only lifts the rate limit; presence of the key also adds `jina` to the auto fallback chain) **and** by `web_fetch` when `AGENTAO_WEB_FETCH_FALLBACK=jina` (`r.jina.ai`). |

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
  },
  "goal": {
    "enabled": true,
    "default_max_turns": 25,
    "default_time_budget": "120m"
  }
}
```

| Key | Type | Default | Allowed values | Notes |
|---|---|---|---|---|
| `mode` | string | `"workspace-write"` (when key absent) | `"read-only"`, `"workspace-write"`, `"full-access"` | `"plan"` is internal — set by `/plan` flow, never written by users. `"full-access"` disables all per-tool prompting; use deliberately. |
| `agents.enable_builtin` | bool | `false` | — | Enables the built-in sub-agent set. Legacy top-level alias `enable_builtin_agents` (bool) is still honored. |
| `goal.enabled` | bool | `true` (when key absent) | — | Master switch for the `/goal` long-task continuation. Set `false` to disable the command. |
| `goal.default_max_turns` | int | `25` | positive int, or `0` for no turn cap | Turn cap applied when `/goal` is set without `--turns` (and not `--unbounded`). One *turn* = one outer continuation `chat()` — **not** `max_iterations` (which bounds the inner tool loop). Primary runaway guard. |
| `goal.default_time_budget` | string | `"120m"` | duration `90s` / `30m` / `2h` / `1h30m`; empty/absent → no time cap default | Active wall-clock cap applied when `/goal` is set without `--for`. Sized **above** the turn cap so it guards only wall-clock pathology and does not shadow the turn cap (see `docs/design/codex-goal-mechanism-review.md` §11.1 C). |

> `/goal` state itself is **not** stored in `settings.json` — it lives in `.agentao/goal.json` (see §1 file table). Per-goal caps set with `/goal <obj> --for/--turns` or `/goal budget` override these defaults; `--unbounded` opts out of them entirely.

See [TOOL_CONFIRMATION_FEATURE.md](../guides/tool-confirmation.md) for what each `mode` actually permits.

> **Tool-set selection is construction-only — not a `settings.json` field (v1).** Which tools the model sees is chosen at `Agentao(...)` construction, not via any config file:
> - `enabled_tools={...}` — additive allowlist: keep only the named built-in / agent-path tools (`extra_tools`, MCP, plan-only always kept). `None` = disabled; the empty set still *enables* it. Mutually exclusive with `disable_tools`.
> - `disable_tools={...}` — subtractive: skip named built-ins.
> - `extra_tools=[...]` — inject pre-built `Tool` / `AsyncToolBase` instances.
>
> These are pure data (names) or instances, not JSON surfaces — wiring them through `settings.json` is deliberately deferred (demand-gated; see `host-tool-allowlist.md` §8). Schema + semantics: [`docs/reference/host-api.md`](host-api.md), [`host-tool-allowlist.md`](../design/host-tool-allowlist.md), [`host-tool-injection.md`](../design/host-tool-injection.md).

---

## 4. `permissions.json` — per-tool permission rules

- **Paths.**
  1. `~/.agentao/permissions.json` (user-level) — the only file-based rule source.
  2. `<cwd>/.agentao/permissions.json` (project-level) — **ignored** with a warning. See "Why no project scope?" below.
- **Loader.** `permissions.py::PermissionEngine._load_file`. Missing file or malformed JSON → empty rule list (no startup error). A file that loads successfully contributes a `loaded_sources` label (`user:<path>`) returned from `PermissionEngine.active_permissions()` and `Agentao.active_permissions()` — see [`docs/reference/host-api.md`](host-api.md).
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

Full rule taxonomy, examples, and runtime semantics → [TOOL_CONFIRMATION_FEATURE.md](../guides/tool-confirmation.md).

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
      "url": "https://.../mcp",
      "headers": { "Authorization": "Bearer $API_KEY" },
      "timeout": 30
    }
  }
}
```

| Transport | `type` | Required keys | Optional |
|---|---|---|---|
| stdio subprocess | `"stdio"` (or infer from `command`) | `command`, `args` | `env`, `trust`, `cwd`, `timeout` |
| Streamable HTTP | `"http"` (or infer from `url`) | `url` | `headers`, `timeout`, `trust` |
| SSE (legacy) | `"sse"` | `url` | `headers`, `timeout`, `trust` |

**Transport selection (`mcp/config.py :: resolve_transport`).** The optional
`type` field selects the transport: `"stdio"` / `"sse"` / `"http"` (aliases
`"streamable-http"` / `"streamable_http"` fold to `"http"`). When `type` is
omitted it is inferred: `command` → stdio, `url` → **Streamable HTTP**. So a
bare `{"url": ...}` means Streamable HTTP — set `"type": "sse"` for the legacy
SSE transport. An unknown `type`, or a transport missing its required key,
**fails closed** (`McpTransportConfigError`) rather than silently connecting to
the wrong protocol.

> **Migration (breaking).** A bare `url` server previously meant SSE; it now
> defaults to the Streamable HTTP transport (the spec's replacement for the
> now-deprecated HTTP+SSE transport). If your server is a legacy SSE endpoint,
> add `"type": "sse"` to its entry.

**`timeout`** accepts two forms (`mcp/config.py :: resolve_timeouts`):

- **int / float** (legacy) — seconds for the *connect / startup* phase (default `60`): bounds the URL-transport HTTP-connection open **and** the `initialize()` / `list_tools()` handshake (all transports). Per-request tool calls stay **unbounded** (the MCP SDK default). Existing configs keep their behavior.
- **object `{ "startup": int, "request": int }`** — both keys optional. `startup` is the connect/handshake bound above (default `60`); `request` bounds *each tool call* after init (omit → unbounded). Without `request`, a hung tool call never self-terminates over stdio, and over SSE only drops after ~300 s of inter-event silence — set `request` to cap it deterministically (a `request` above 300 s also raises the SSE stream's read timeout to match).

```json
"slow-server": { "url": "https://...", "timeout": { "startup": 15, "request": 90 } }
```

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

Full ACP semantics → [acp-client.md](../guides/acp-client.md) and [acp-embedding.md](../guides/acp-embedding.md).

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

See [SKILLS_GUIDE.md](../guides/skills.md) for skill discovery and activation rules.

---

## 8. `AGENTAO.md` — project instructions

- **Path.** `<cwd>/AGENTAO.md`. Optional.
- **Loader.** `agent.py::_build_system_prompt` — content is prepended to the system prompt when present.
- **Schema.** Free-form Markdown; no required structure.

See [CHATAGENT_MD_FEATURE.md](../guides/chatagent-md.md) for prompt-composition rules and conventions.

---

## 9. Memory stores (`memory.db`)

- **Paths.**
  - Project: `<cwd>/.agentao/memory.db`
  - User: `~/.agentao/memory.db`
- **Format.** SQLite; schema owned by `memory/manager.py::MemoryManager`. Not hand-edited.
- **Precedence.** Both DBs are read independently; both are visible to the prompt renderer. Project memory does not override user memory.

Full schema, tables, and lifecycle → [memory-management.md](../guides/memory-management.md).

---

## 10. Run spec — `agentao run` invocation

- **Paths.** No fixed location — `agentao run --spec PATH` (YAML or JSON) or piped on stdin.
- **Loader.** `agentao/cli/run.py::_load_spec` + `_parse_spec_text` → `RunSpec.model_validate`. Templating: `cli/run_template.py::render_spec`.
- **Scope.** Per-invocation; not merged with any global file.

### Schema (top-level)

| Key | Type | Notes |
|---|---|---|
| `prompt` | `str` | Required at run-time (after CLI merge + render). Templated. |
| `instructions` | `str?` | Templated. When non-empty after `.strip()`, routes to `Agentao(project_instructions=…)` and short-circuits `AGENTAO.md`. |
| `parameters` | `list[RunParameter]?` | See sub-schema below. Duplicates rejected. |
| `cwd` | `str?` | Working directory; defaults to `os.getcwd()`. |
| `model` / `base_url` | `str?` | LLM overrides. |
| `permission_mode` | enum | `read-only` / `workspace-write` / `full-access` / `plan`. |
| `interaction_policy` | `"reject"` | Only `reject` accepted in M0. |
| `permissions` | `{allow, deny}` | Lists of `RunPermissionRule`. Spec writers never author `action:` — the loader injects it. |
| `max_iterations` | `int?` | Default 100. |
| `skills` | `list[str]?` | Must already exist; missing skills exit 2. |
| `replay` | `bool?` | Authoritative; bypasses the factory's disk auto-load when set. |
| `output` | `{format: "text"\|"json"}?` | Same effect as `--format`. |

`extra="forbid"` on `RunSpec` — unknown top-level fields fail loudly.

### `parameters[]` sub-schema (`RunParameter`)

| Key | Type | Notes |
|---|---|---|
| `name` | `str` | ASCII identifier (`[A-Za-z_][A-Za-z0-9_]*`) AND not a Jinja-reserved name (constants `true/True/false/False/none/None`, keywords `for/if/in/set/...`, runtime-injected `self/parent`). |
| `required` | `bool` | Mutually exclusive with `default`. |
| `default` | `str?` | String-only in v1. Must be in `choices` if both are set. |
| `choices` | `list[str]?` | Enum-style validation. |

`extra="forbid"` on `RunParameter` — typos like `requierd:` fail validation.

### `--param KEY=VALUE` (CLI)

- Repeatable. Splits on the **first** `=` only — value may contain further `=` chars.
- Empty key or missing `=` → exit 2, "expected KEY=VALUE".
- Duplicate key → exit 2, "supplied multiple times" (no last-wins).
- Non-identifier key → exit 2 with the identifier-rule message.

### Templating

- Jinja2 `SandboxedEnvironment` + `StrictUndefined` + `keep_trailing_newline=True`, `autoescape=False`.
- Only `spec.prompt` and `spec.instructions` are rendered.
- Trigger rule (no Jinja2 call when both empty):

  | `spec.parameters` | CLI `--param` | Behavior |
  |---|---|---|
  | empty / unset | empty | No-op (literal `{{ }}` passes through). |
  | empty / unset | non-empty | exit 2 (unknown parameter). |
  | non-empty | any | Validate + render. |

- Render errors → exit 2 / `invalid_spec`:
  - `SecurityError` → "sandbox-blocked operation".
  - `UndefinedError` (StrictUndefined) → "template uses undefined variable 'X' (declare it in spec.parameters)".
  - Anything else from `template.render()` (e.g. `ZeroDivisionError`, `TypeError`, `TemplateNotFound`) → "template error in spec.\<field\>: …".

### Exit codes (`agentao run`)

| Code | Meaning |
|---|---|
| 0 | OK |
| 1 | Runtime error |
| 2 | Invalid usage / invalid spec |
| 3 | Permission denied or interaction required |
| 4 | Max iterations |
| 130 | Interrupted (SIGINT) |

Full design rationale → [run-spec-parameters.md](../design/run-spec-parameters.md).

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
