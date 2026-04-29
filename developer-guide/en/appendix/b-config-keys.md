# Appendix B · Configuration Keys Index

Every knob Agentao reads — environment variables first, then on-disk JSON. All of them are optional unless marked **required**.

## B.1 Environment variables

### LLM credentials

`LLM_PROVIDER` selects a **provider prefix** (default `OPENAI`). The remaining three keys are read as `{PROVIDER}_API_KEY`, `{PROVIDER}_BASE_URL`, `{PROVIDER}_MODEL`.

| Key | Required | Default | Notes |
|-----|----------|---------|-------|
| `LLM_PROVIDER` | — | `OPENAI` | Picks the `{PROVIDER}_*` prefix. Any upper-case name works (e.g. `DEEPSEEK`, `ANTHROPIC`, `GEMINI`) |
| `{PROVIDER}_API_KEY` | **yes** | — | Constructor `api_key=` overrides |
| `{PROVIDER}_BASE_URL` | **yes** | — | Constructor `base_url=` overrides. OpenAI-compatible endpoint |
| `{PROVIDER}_MODEL` | **yes** | — | Constructor `model=` overrides. Runtime-swappable via `agent.set_model()` |

> **Fail-fast rule:** `LLMClient.__init__` raises `ValueError` at startup if any of `{PROVIDER}_API_KEY`, `{PROVIDER}_BASE_URL`, or `{PROVIDER}_MODEL` is absent and was not supplied via the constructor. The `/provider` listing and switching commands apply the same gate — all three must be set for a provider to appear in the list or be switchable.

### Global runtime

| Key | Default | Meaning |
|-----|---------|---------|
| `LLM_TEMPERATURE` | `0.2` | Sampling temperature (0.0–2.0) |
| `LLM_MAX_TOKENS` | unset | Hard cap on LLM completion tokens per call |
| `AGENTAO_CONTEXT_TOKENS` | `200000` | Context budget; triggers compression when exceeded |
| `AGENTAO_WORKING_DIRECTORY` | — | Override working directory at startup (alternative to constructor `working_directory=`) |

### Third-party keys consumed by built-in tools

| Key | Consumed by | Purpose |
|-----|-------------|---------|
| `GITHUB_TOKEN` | Skill catalog fetcher | Higher-rate GitHub API access |
| `BOCHA_API_KEY` | `web_search` tool | Use Bocha Search API instead of DuckDuckGo. If absent, the tool falls back to DuckDuckGo automatically. |

MCP servers and custom tools typically read their own env keys — those are defined in your `.agentao/mcp.json` or your custom-tool code, not here.

## B.2 Precedence order

For every setting: **constructor argument > environment variable > on-disk JSON > hard-coded default**.

On-disk JSON layering varies by surface:

- **`sandbox.json` / `mcp.json`** — project file overrides user file on the same key.
- **`permissions.json`** — both files are loaded; **project rules are prepended** so they evaluate before user rules. Mode preset rules then run last (or first in `full-access` / `plan`, where they cannot be overridden).
- **`memory.db`** — project and user stores are read **independently**; both are visible to the prompt renderer. Project does not override user.
- **`acp.json`, `settings.json`, `skills_config.json`, `AGENTAO.md`** — project-only; no merge.

## B.3 On-disk JSON files

JSON config files live in an `.agentao/` directory (project at `<working_directory>/.agentao/`, user at `~/.agentao/`); the project-level `AGENTAO.md` lives at the project root. Per-surface precedence is described in B.2. Any file may be absent.

| File | Scope | Section | Purpose |
|------|-------|---------|---------|
| `mcp.json` | project + user | [5.3](/en/part-5/3-mcp) | MCP servers (stdio / SSE) |
| `permissions.json` | project + user | [5.4](/en/part-5/4-permissions) | Per-tool permission rules |
| `sandbox.json` | project + user | [6.2](/en/part-6/2-shell-sandbox) | Shell sandbox profile selection |
| `acp.json` | project only | [3.2](/en/part-3/2-agentao-as-server) | ACP subagent registry (when Agentao runs as client) |
| `settings.json` | project only | [6.6](/en/part-6/6-observability) | Persisted permission mode, builtin-agents flag, replay block |
| `skills_config.json` | project only | [5.2](/en/part-5/2-skills) | Disabled-skills list (managed via `/skills disable`) |
| `AGENTAO.md` | project only | [5.6](/en/part-5/6-system-prompt) | Project-specific instructions, prepended to system prompt |
| `memory.db` | project + user | [5.5](/en/part-5/5-memory) | SQLite-backed persistent memory (not JSON; listed for completeness) |

### B.3.1 `mcp.json`

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
      "env": { "LOG_LEVEL": "info" },
      "trust": false,
      "timeout": 30
    },
    "remote": {
      "url": "https://api.example.com/sse",
      "headers": { "Authorization": "Bearer $API_TOKEN" },
      "timeout": 60
    }
  }
}
```

**Field reference**:

| Field | Type | Notes |
|-------|------|-------|
| `command` | string | stdio server — mutually exclusive with `url` |
| `args` | string[] | stdio args |
| `env` | object | expanded (`$VAR` / `${VAR}`) |
| `cwd` | string | stdio subprocess cwd |
| `url` | string | SSE server URL |
| `headers` | object | SSE request headers (`$VAR` expanded) |
| `trust` | bool | Skip confirmation prompt for tools from this server |
| `timeout` | number (s) | Per-tool-call timeout |

HTTP transport is **not** supported; stdio + SSE only.

### B.3.2 `permissions.json`

```json
{
  "rules": [
    { "tool": "run_shell_command", "args": { "command": "^git " }, "action": "allow" },
    { "tool": "run_shell_command", "args": { "command": "rm\\s+-rf" }, "action": "deny" },
    { "tool": "write_file", "action": "ask" },
    {
      "tool": "web_fetch",
      "domain": { "allowlist": [".github.com"], "url_arg": "url" },
      "action": "allow"
    }
  ]
}
```

**Rule fields**:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `tool` | string | yes | Tool name; matched as a regex via `re.fullmatch` (use `"*"` for wildcard) |
| `args` | object | no | Map of `<arg_name>` → regex; **all** entries must `re.search`-match for the rule to fire |
| `domain` | object | no | URL-tools only (`web_fetch`); keys `url_arg` (default `"url"`), `allowlist`, `blocklist`. Patterns starting with `.` do suffix matching (e.g. `.github.com` matches `api.github.com`); otherwise exact match |
| `action` | string | yes | `"allow"` \| `"deny"` \| `"ask"` (case-insensitive) |

The `mode` field is **not** stored in `permissions.json` — it lives in `settings.json` (B.3.5) and is changed at runtime via `/permissions`. Modes: `read-only`, `workspace-write`, `full-access`, `plan` (lowercase-hyphen; `plan` is internal).

Evaluation order:

- `read-only` / `workspace-write`: `[project rules] → [user rules] → [active mode preset]` — first match wins.
- `full-access` / `plan`: `[active mode preset] → [project rules] → [user rules]` — presets cannot be overridden.
- No match → falls back to the tool's own `requires_confirmation` attribute.

### B.3.3 `sandbox.json`

```json
{
  "shell": {
    "enabled": true,
    "default_profile": "workspace-write",
    "allow_network": true,
    "allowed_commands_without_confirm": ["ls", "cat", "head", "git status"],
    "profiles_dir": "~/.agentao/sandbox-profiles"
  }
}
```

**Built-in profiles**: `readonly`, `workspace-write-no-network`, `workspace-write`.
**Fail-closed**: a missing profile file raises `SandboxMisconfiguredError`; the shell tool refuses to run.

### B.3.4 `acp.json`

Only consumed when **Agentao itself is an ACP client** (see [3.4 ACPManager](/en/part-3/) — forthcoming). Structure mirrors `mcp.json` but under `acpServers`. Safe to omit for most integrations.

Per-server keys under `servers.{name}`:

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `command` | string | — | Required |
| `args` | list[str] | — | Required |
| `env` | dict | — | Required; `$VAR` / `${VAR}` expanded |
| `cwd` | string | — | Required; relative paths resolve against project root |
| `autoStart` | bool | `true` | |
| `startupTimeoutMs` | int | `10000` | |
| `requestTimeoutMs` | int | `60000` | |
| `maxRecoverableRestarts` | int | `3` | Cap for auto-restarts after recoverable subprocess deaths; reset on first successful turn |
| `capabilities` | dict | `{}` | |
| `description` | string | `""` | |
| `nonInteractivePolicy` | `{"mode": "reject_all" \| "accept_all"}` | `{"mode": "reject_all"}` | Structured object (Week 3). **Legacy bare-string form is rejected at config load time** — see [Appendix E](./e-migration). |

### B.3.5 `settings.json`

Project-local runtime settings. Read from `<working_directory>/.agentao/settings.json`. Carries persisted permission mode, the built-in-agents opt-in, and the replay block.

```json
{
  "mode": "workspace-write",
  "agents": {
    "enable_builtin": false
  },
  "replay": {
    "enabled": false,
    "max_instances": 20,
    "capture_flags": {
      "capture_llm_delta": true,
      "capture_full_llm_io": false,
      "capture_tool_result_full": false,
      "capture_plugin_hook_output_full": false
    }
  }
}
```

Top-level keys:

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `mode` | string | `"workspace-write"` (when key absent) | Last-known permission mode used for restoration paths and `/permissions` inspection. Allowed: `"read-only"`, `"workspace-write"`, `"full-access"`. (`"plan"` is internal — set by the `/plan` flow, never written by users.) |
| `agents.enable_builtin` | bool | `false` | Enables the built-in sub-agent set. Legacy top-level alias `enable_builtin_agents` (bool) is still honored. |

Replay keys:

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `replay.enabled` | bool | `false` | Enables recording for future sessions. `/replay on` and `/replay off` write this value. |
| `replay.max_instances` | int | `20` | Retention cap under `.agentao/replays/`; does not affect `.agentao/sessions/`. |
| `replay.capture_flags.capture_llm_delta` | bool | `true` | Records messages newly added by each LLM call. |
| `replay.capture_flags.capture_full_llm_io` | bool | `false` | Deep capture of full LLM inputs/outputs; treat as sensitive. |
| `replay.capture_flags.capture_tool_result_full` | bool | `false` | Deep capture of full tool results beyond the normal replay truncation policy. |
| `replay.capture_flags.capture_plugin_hook_output_full` | bool | `false` | Deep capture of plugin hook output. |

Malformed `settings.json` falls back to safe defaults instead of blocking startup.

## B.4 Constructor parameters that shadow these

Every env var and JSON key above has a Python equivalent on `Agentao(...)`:

| JSON / env | Constructor |
|------------|-------------|
| `{PROVIDER}_API_KEY` | `api_key=` |
| `{PROVIDER}_BASE_URL` | `base_url=` |
| `{PROVIDER}_MODEL` | `model=` |
| `AGENTAO_WORKING_DIRECTORY` | `working_directory=` |
| `AGENTAO_CONTEXT_TOKENS` | `max_context_tokens=` |
| `mcp.json` | `extra_mcp_servers=` (merged on top of file) |
| `permissions.json` | `permission_engine=` |
| `sandbox.json` | no direct arg — policy is read at tool-call time |
| `settings.json` replay block | no direct arg — use `/replay on/off` or edit the file |

Constructor always wins. Useful for SaaS hosts that inject per-tenant settings without touching env.

---

→ [Appendix D · Error codes](./d-error-codes)
