# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Package Management

**Always use `uv` for package management**, not pip:

```bash
uv sync                    # Install dependencies
uv add package-name        # Add a new dependency
uv run python script.py    # Run Python scripts
uv run agentao             # Run the CLI
```

Core deps live in `[project.dependencies]`; the heavyweight UI / format-conversion deps are opt-in extras (`[cli]`, `[web]`, `[i18n]`, `[pdf]`, `[excel]`, `[image]`, `[crypto]`, `[google]`, `[crawl4ai]`, `[tokenizer]`, `[full]`). A bare `pip install agentao` gets a library-only install; `pip install 'agentao[cli]'` is the smallest interactive CLI.

## Running

```bash
./run.sh                              # Quick start (interactive)
uv run agentao                        # Interactive CLI
uv run python -m agentao              # Same, via module entrypoint
uv run agentao run --prompt "..."     # Non-interactive automation (M0)
uv run agentao --acp --stdio          # ACP server (Issue 12)
```

`agentao run` is the canonical non-interactive surface. Exit codes: `0` ok, `1` runtime, `2` invalid usage, `3` permission/interaction, `4` max iterations, `130` interrupted. See `agentao/cli/run.py` and `docs/CONFIGURATION.md`. The legacy `agentao -p "..."` is now a thin shim over `agentao run`.

## Testing

```bash
uv run python -m pytest tests/                       # Default suite
uv run python -m pytest -m slow                      # Clean-install smoke tests
uv run python -m pytest tests/cli/                   # CLI subsuite
uv run python -m pytest tests/test_active_permissions.py   # Single file
```

There are 160+ test files including subdirs (`tests/cli/`, `tests/data/`, `tests/support/`). The `slow` marker is excluded by default (`pyproject.toml :: tool.pytest.ini_options.addopts = "-m 'not slow'"`).

## Configuration

```bash
cp .env.example .env       # Edit with OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
```

**Reference for all config files** (`.env`, `.agentao/settings.json`, `permissions.json`, `mcp.json`, `acp.json`, `skills_config.json`, `AGENTAO.md`, memory DBs): see [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for paths, schema, defaults, and precedence rules.

## Architecture

Agentao is an **embedded agent harness**: the same runtime drives the interactive CLI, the `agentao run` automation surface, and the ACP server, with hosts free to embed `Agentao(...)` directly. The package boundary between "host-facing contract" and "internal runtime" is load-bearing — see `docs/design/embedded-host-contract.md` and `docs/api/host.md`.

> **Embedding Agentao into a *different* project?** (e.g. a coding agent asked to "add Agentao" to another codebase.) Read the distilled playbook at `docs/EMBED_FOR_AGENTS.md` — construction skeletons, import rules, and an integration checklist. Note that *this* `CLAUDE.md` and `AGENTAO.md` are for working inside the Agentao repo, not for the embedding target.

### Subpackage map

| Path | Purpose |
|---|---|
| `agentao/agent.py` | `Agentao` class — sync `chat()` and async `arun()`. Construction wires LLM, tools, skills, plugins, permissions, replay. |
| `agentao/runtime/` | Per-turn machinery extracted from `Agentao` — `ChatLoopRunner` (loop body), `ToolRunner` (4-phase tool pipeline: plan / execute / format / sanitize), `run_llm_call`, model/provider switching. |
| `agentao/host/` | **Public host contract.** `HostEvent`, `ToolLifecycleEvent`, `SubagentLifecycleEvent`, `PermissionDecisionEvent`, `EventStream`, `ActivePermissions`. Stability boundary for embedded hosts. |
| `agentao/harness/` | **Deprecated alias for `agentao.host`** (renamed in 0.4.2). Re-exports with old names + `DeprecationWarning`; scheduled for removal in 0.5.0. |
| `agentao/embedding/` | Host-side construction: `build_from_environment()` (env / dotenv / `.agentao/*.json` reads routed through explicit kwargs), `permission_loader`, `sessions`, `plugins/` (manifest loader, validators, MCP merge, resolvers). |
| `agentao/plugins/` | Plugin **runtime path** only — models, hooks, skill/agent validators. Loader lives in `embedding/plugins/`. |
| `agentao/replay/` | `ReplayManager` + recorder/reader/adapter for persistent turn replay (`.agentao/replays/*.jsonl`). Transport `TURN_BEGIN`/`TURN_END` events. |
| `agentao/acp/` | ACP server (Agent Connection Protocol). `agentao --acp --stdio` mode. Pydantic schemas exported via `agentao.host.export_host_acp_json_schema`. |
| `agentao/acp_client/` | ACP **client** — talk to other ACP servers from inside Agentao. |
| `agentao/tools/` | Tool implementations + `Tool` / `AsyncToolBase` base classes (`base.py`). |
| `agentao/mcp/` | MCP (Model Context Protocol) client — connect to external MCP servers and surface their tools as `mcp_{server}_{tool}`. |
| `agentao/memory/` | SQLite-backed memory store (`MemoryManager`, `MemoryRetriever`, `MemoryPromptRenderer`). |
| `agentao/permissions.py` + `permissions_hardline/` | `PermissionEngine` + shell-pattern hardline scanner (heredoc, contexts, decoder). |
| `agentao/sandbox/` | macOS `sandbox-exec` profile management for shell tool. |
| `agentao/transport/` | Event transport between Agentao core and CLI/ACP frontends. |
| `agentao/cli/` | Interactive CLI **package** (was `cli.py` before 0.4.x). `app.py` (`AgentaoCLI`), `entrypoints.py` (argparse + `main`), `run.py` (`agentao run`), `commands/` (per-slash-command handlers), `subcommands.py`, `diagnostics_cli.py`. |
| `agentao/skills/` | Skill discovery + activation. SKILL.md frontmatter parser. |
| `agentao/prompts/`, `agentao/agents/`, `agentao/plan/`, `agentao/capabilities/`, `agentao/tooling/`, `agentao/security/`, `agentao/session.py`, `agentao/context_manager.py` | Supporting modules — prompt assembly, sub-agent runners, plan-mode state, capability declarations (incl. `capabilities/process.py::run_captured` — the shared hardened subprocess runner; see Common gotchas), tool-arg sanitizers, security utilities, session save/load, context-window compaction. |

### Tool system

All tools inherit from `Tool` (sync) or `AsyncToolBase` (async) in `agentao/tools/base.py`. Both are registered through the same `ToolRegistry` which converts them to OpenAI function-calling format.

```python
class MyTool(Tool):
    name: str
    description: str
    parameters: Dict[str, Any]              # JSON Schema
    requires_confirmation: bool             # True → permission engine gate
    def execute(self, **kwargs) -> str: ...
```

`AsyncToolBase` dispatches through `runtime_loop` with a `CancellationToken`; cleanup-ack uses `_bridged()` `finally` + `threading.Event` so the runtime can cancel mid-tool. `RegistrableTool = Tool | AsyncToolBase`.

**Registration**: in `agent.py::_register_tools()` (line ~522). Built-in tools currently in `agentao/tools/`: `agents.py`, `ask_user.py`, `file_ops.py`, `memory.py`, `plan.py`, `search.py`, `shell.py`, `skill.py`, `todo.py`, `web.py`.

**Confirmation / permissions**: tools with `requires_confirmation=True` are gated by `PermissionEngine`, which evaluates rules from `.agentao/permissions.json` (project) + `<home>/.agentao/permissions.json` (user). The engine itself does **no file I/O** — `agentao/embedding/permission_loader.py::load_permission_rules()` reads and passes `(rules, sources)` in. Default presets auto-allow common docs domains (`.github.com`, `.docs.python.org`, …) and auto-deny SSRF targets (`localhost`, `127.0.0.1`, `169.254.169.254`, …).

### Permission modes (replaces the old `allow_all_tools` flag)

`/mode read-only | workspace-write | full-access | plan` switches the runtime's permission posture:

- `read-only` — blocks all write and shell tools.
- `workspace-write` — allows file writes and safe shell; asks for web (default).
- `full-access` — allows all tools without prompting.
- `plan` — LLM plans, does not execute; entered via `/plan`.

State is on `AgentaoCLI` (`agentao/cli/app.py`) and projected into prompts.

### System prompt composition

Built fresh on every `chat()` in `agent.py::_build_system_prompt()` (line ~605):

1. `AGENTAO.md` (if present in cwd) — project-specific instructions
2. Agent instructions — base Agentao capabilities
3. Current date/time — `YYYY-MM-DD HH:MM:SS (Day)`
4. Memory blocks — `<memory-stable>` + `<memory-context>` (top-k recall scored against current user message)
5. Available skills — names + descriptions
6. Active skills context — full SKILL.md + on-demand `references/*.md`

### Conversation flow

```
Agentao.chat() / Agentao.arun()
  └─ ChatLoopRunner.run()                  # runtime/chat_loop/_runner.py
       loop (max_iterations):
         ├─ run_llm_call(messages, tools)  # runtime/llm_call.py
         ├─ if tool_calls:
         │    └─ ToolRunner.run()          # runtime/tool_runner.py
         │         plan → execute → format → sanitize
         │           (gates: PermissionEngine + confirmation_callback)
         └─ else: return assistant text
```

`arun()` is the async path; the sync `chat()` wraps it. AsyncTools dispatch on `runtime_loop` so cancellation works inside the LLM-driven turn.

### Skills

Auto-discovered from `skills/`. Each subdir has `SKILL.md` (YAML frontmatter `name:` / `description:`) and optional `references/*.md` (loaded on activation). The skill manager (`agentao/skills/`) maintains `available_skills` (all) and `active_skills` (this session). Cross-process locking via `filelock` — installs and updates are safe across concurrent CLI processes.

Activate via the `activate_skill` tool or `/skills activate <name>`.

### Memory system

**Architecture:** SQLite-backed storage managed by `MemoryManager` (`agentao/memory/manager.py`).

| Database | Path | Content |
|---|---|---|
| Project store | `.agentao/memory.db` | Project-scoped persistent memories + session summaries |
| User store | `<home>/.agentao/memory.db` | Cross-project user-scoped persistent memories |

**Three data types:**

1. **Persistent memories** (`MemoryRecord`) — rows in `memories`. Soft-deleted. Scoped `user` / `project`. Types: `preference`, `profile`, `project_fact`, `workflow`, `decision`, `constraint`, `note`. Source: `explicit` / `auto` / `crystallized`.
2. **Session summaries** (`SessionSummaryRecord`) — rows in `session_summaries`. Written by microcompaction / full LLM summarization. Scoped to `session_id`.
3. **Recall candidates** (`RecallCandidate`) — transient, in-memory. Scored at query time by `MemoryRetriever` (keyword/Jaccard/tag/recency). Never stored.

**Prompt injection (per turn, two blocks):**
- `<memory-stable>` — stable persistent memories (budget-limited). Session summaries are intentionally excluded — they live in message history as `[Conversation Summary]` blocks.
- `<memory-context>` — top-k recall candidates against current user message.

**Separation of concerns:** the LLM can only write (`save_memory(key, value, tags?)`). Search, delete, clear are CLI-only (`/memory search|tag|delete|clear|user|project|session|status`) and call `MemoryManager` directly — never exposed as LLM tools.

See `docs/features/memory-management.md`.

### Replay

`ReplayManager` (`agentao/replay/manager.py`) records every turn to `.agentao/replays/*.jsonl` when enabled. Replay state lives **outside** `Agentao` core — Transport emits `TURN_BEGIN` / `TURN_END` events that the manager subscribes to. Configure via `.agentao/settings.json :: replay.{enabled, max_instances}` or `/replay on|off`.

### MCP

External MCP servers via `.agentao/mcp.json` (project) + `<home>/.agentao/mcp.json` (global):

```json
{
  "mcpServers": {
    "server-name": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path"],
      "env": { "TOKEN": "$MY_TOKEN" },
      "trust": false
    },
    "remote-server": {
      "url": "https://api.example.com/sse",
      "headers": { "Authorization": "Bearer $API_KEY" },
      "timeout": 30
    }
  }
}
```

Transports: `command` (stdio subprocess) or `url` (SSE). Tools are registered as `mcp_{server}_{tool}`. The MCP SDK is async-only; `McpClientManager` runs a dedicated event loop and bridges into sync Agentao via `run_until_complete()`.

Key files: `agentao/mcp/config.py`, `client.py`, `tool.py`.

CLI: `/mcp list`, `/mcp add <name> <command|url>`, `/mcp remove <name>`.

### Logging

`agentao.log` captures every LLM request/response (full content, no truncation), all tool calls with formatted JSON arguments, tool results, token usage, timestamps. Logger lives in `agentao/llm/client.py` — read this file first when debugging tool execution or LLM behavior.

### CLI slash commands

The authoritative list with full subcommand syntax lives in `agentao/cli/help_text.py`; `/help` renders it. The high-impact commands to know about when reasoning about agent behavior:

- `/mode read-only|workspace-write|full-access` — permission posture (replaces 0.3.x `allow_all_tools`).
- `/plan` / `/plan implement` / `/plan show` — plan mode (LLM plans, does not execute).
- `/clear` — saves current session, clears conversation + **all memories**, starts a new one.
- `/new` — saves session, starts fresh conversation (keeps memories).
- `/sessions`, `/sessions resume <id>`, `/sessions delete <id>` — manage saved sessions.
- `/skills`, `/skills activate <name>`, `/skills disable <name>` — skill state.
- `/crystallize` — draft a reusable skill from the current session.
- `/memory list|search|tag|delete|clear|user|project|session|status` — memory management.
- `/mcp`, `/sandbox`, `/acp`, `/replay` — subsystem control.
- `/agent <name> <task>`, `/agent bg <name> <task>`, `/agent dashboard` — sub-agent runners.
- `/tools [name]` — list registered tools or show one tool's schema.
- `/model`, `/provider`, `/temperature` — LLM config.
- `/context`, `/compact` — context-window inspection + manual compaction.

## Adding new components

### A tool

1. Create `agentao/tools/<module>.py` and implement `Tool` (or `AsyncToolBase` for async).
2. Set `requires_confirmation=True` for anything dangerous: arbitrary shell, network requests, file writes, deletions.
3. Register in `agent.py::_register_tools()`.

### A skill

1. Create `skills/<my-skill>/SKILL.md` with YAML frontmatter (`name:`, `description:` — the trigger text the model sees).
2. Optionally add `references/*.md` files (loaded only on activation, saves memory).
3. Restart the agent or run `/skills reload`.

## Common gotchas

- **`cli.py` was split into the `cli/` package** in 0.4.x. Older docs and design notes may still say `cli.py` — grep `agentao/cli/` for the actual handler.
- **`agentao.harness` → `agentao.host`** rename in 0.4.2. The old name is a deprecated alias scheduled for removal in 0.5.0. Use `agentao.host.HostEvent`, `export_host_acp_json_schema`, etc.
- **`allow_all_tools` is gone.** Use `/mode full-access` (or the equivalent host-API call) instead.
- **`agentao -p` is a shim** over `agentao run`. New automation should target `agentao run` directly — that's where the spec schema, Jinja2 templating, and exit codes are documented.
- **`Agentao` constructor takes 8 legacy callbacks** (`confirmation_callback`, `step_callback`, …) that emit `DeprecationWarning`. They will be removed in 0.5.0 — `agentao.embedding.compat` is the documented migration surface.
- **Don't intuition-audit architecture.** Before recommending borrowed patterns or claiming a gap exists, grep agentao to verify; subpackage `__init__.py` docstrings document intentional shims and rename trails.
- **Don't call `subprocess.run` for batch commands — use `agentao/capabilities/process.py::run_captured()`.** A bare `subprocess.run(timeout=)` only kills the direct child on timeout, so a grandchild holding the captured pipe (Windows `git` credential helpers, a user hook backgrounding a process) hangs `communicate()` past the timeout — and over ACP-stdio a hung tool wedges the turn until the client times out and drops the connection. `run_captured` runs the child in its own process group/session, feeds/detaches stdin explicitly (`input=` over a pipe, else `DEVNULL` so a child can't read the JSON-RPC channel), kills the whole tree via `kill_process_tree()` on timeout (`taskkill /T` / `killpg(pid)` — never `getpgid`, which races a zombie child), and decodes with `errors="replace"`. `search_file_content` and the plugin hook dispatcher route through `run_captured`; `LocalShellExecutor.run` keeps its own streaming + inactivity-timeout loop but shares `kill_process_tree`. (PRs #73/#74/#75.)

## Key dependencies

Core (`pyproject.toml :: project.dependencies`):
- `openai>=1.0.0` — LLM client (OpenAI-compatible)
- `httpx>=0.25.0` — HTTP client
- `pydantic>=2` — schemas (host contract, ACP, run-spec)
- `pyyaml>=6.0.3` — SKILL.md frontmatter, plugin manifests
- `mcp>=1.26.0` — MCP client SDK
- `python-dotenv>=1.0.0` — `.env` loading
- `filelock>=1.4.0` — cross-process locking for skill registry
- `jinja2>=3.0.0` — `agentao run` spec templating (StrictUndefined)

Extras: see Package Management section above; full list in `pyproject.toml :: project.optional-dependencies`.
