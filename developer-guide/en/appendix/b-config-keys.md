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

On-disk JSON itself layers: **project `<cwd>/.agentao/*.json` > user `~/.agentao/*.json`** (project wins when both define the same key).

## B.3 On-disk JSON files

All files live in an `.agentao/` directory. **Project** files (in `<working_directory>/.agentao/`) take precedence over **user** files (in `~/.agentao/`). Any file may be absent.

| File | Scope | Section | Purpose |
|------|-------|---------|---------|
| `mcp.json` | project + user | [5.3](/en/part-5/3-mcp) | MCP servers (stdio / SSE) |
| `permissions.json` | project + user | [5.4](/en/part-5/4-permissions) | Permission mode + rules |
| `sandbox.json` | project + user | [6.2](/en/part-6/2-shell-sandbox) | Shell sandbox profile selection |
| `acp.json` | project only | [3.2](/en/part-3/2-agentao-as-server) | ACP server config (when Agentao runs as client) |
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

HTTP transport is **not** supported in v0.2.x; stdio + SSE only.

### B.3.2 `permissions.json`

```json
{
  "mode": "WORKSPACE_WRITE",
  "rules": [
    { "tool": "run_shell_command", "args": { "command": "rm -rf *" }, "action": "deny" },
    { "tool": "web_fetch", "domain": "*.internal", "action": "deny" },
    { "tool": "write_file", "action": "allow" }
  ]
}
```

**Modes**: `READ_ONLY`, `WORKSPACE_WRITE`, `FULL_ACCESS`, `PLAN`.
**Rule actions**: `allow`, `deny`, `ask`.
**Rule keys**: one of `tool` (required), plus optional `args` (partial match) and `domain` (for `web_fetch`).

Evaluation order: explicit rules → mode preset → fall back to `ask` on write tools.

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
| `capabilities` | dict | `{}` | |
| `description` | string | `""` | |
| `nonInteractivePolicy` | `{"mode": "reject_all" \| "accept_all"}` | `{"mode": "reject_all"}` | Structured object (Week 3). **Legacy bare-string form is rejected at config load time** — see [Appendix E](./e-migration). |

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

Constructor always wins. Useful for SaaS hosts that inject per-tenant settings without touching env.

---

→ [Appendix D · Error codes](./d-error-codes)
