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

Core deps live in `[project.dependencies]`; the heavyweight UI / fetch / tokenization deps are opt-in extras. A bare `pip install agentao` gets a library-only install; `pip install 'agentao[cli]'` is the smallest interactive CLI. (The `[pdf]` / `[excel]` / `[image]` / `[crypto]` / `[google]` extras were removed as dead weight — zero in-tree consumers; see `docs/design/optimization-opportunities-review.md` T1.1.)

## Running

```bash
./run.sh                              # Quick start (interactive)
uv run agentao                        # Interactive CLI
uv run python -m agentao              # Same, via module entrypoint
uv run agentao run --prompt "..."     # Non-interactive automation (M0)
uv run agentao --acp --stdio          # ACP server (Issue 12)
```

`agentao run` is the canonical non-interactive surface. Exit codes: `0` ok, `1` runtime, `2` invalid usage, `3` permission/interaction, `4` max iterations, `130` interrupted. See `agentao/cli/run.py` and `docs/reference/configuration.md`. The legacy `agentao -p "..."` is now a thin shim over `agentao run`.

## Testing

```bash
uv run python -m pytest tests/       # Default suite
uv run python -m pytest -m slow      # Clean-install smoke tests
uv run ruff check .                  # Lint gate — required CI check
```

The `slow` marker is excluded by default (`pyproject.toml :: tool.pytest.ini_options.addopts = "--tb=short -m 'not slow'"`).

**`ruff check .` is a required CI check, so a green pytest run is not enough before pushing.** The gate is deliberately narrow — defect rules only (`E9`, `F401`, `F402`, `F405`, `F811`, `F821`), no style — and the rules *and* scope both live in `pyproject.toml`, so the command above is character-for-character what CI runs. Two non-obvious parts: `F405` is selected because `F821` is silently inert in the 8 star-import modules without it, and `F401` is exempted for `agentao/` because a name re-exported for downstream embedders is indistinguishable from dead code to a single-file linter. Suppress with a reason (`# noqa: F401 — pytest fixture injection`), never bare. See [docs/design/lint-gate.md](docs/design/lint-gate.md).

## Configuration

```bash
cp .env.example .env       # Edit with OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
```

**Reference for all config files** (`.env`, `.agentao/settings.json`, `permissions.json`, `mcp.json`, `acp.json`, `skills_config.json`, `AGENTAO.md`, memory DBs): see [docs/reference/configuration.md](docs/reference/configuration.md) for paths, schema, defaults, and precedence rules.

## Architecture

Agentao is an **embedded agent harness**: the same runtime drives the interactive CLI, the `agentao run` automation surface, and the ACP server, with hosts free to embed `Agentao(...)` directly. The package boundary between "host-facing contract" and "internal runtime" is load-bearing — see `docs/design/embedded-host-contract.md` and `docs/reference/host-api.md`.

> **Embedding Agentao into a *different* project?** (e.g. a coding agent asked to "add Agentao" to another codebase.) Read the distilled playbook at `docs/guides/embed-for-agents.md` — construction skeletons, import rules, and an integration checklist. Note that *this* `CLAUDE.md` and `AGENTAO.md` are for working inside the Agentao repo, not for the embedding target.

### Subpackage map

| Path | Purpose |
|---|---|
| `agentao/agent.py` | `Agentao` class — sync `chat()` and async `arun()`. Construction wires LLM, tools, skills, plugins, permissions, replay. |
| `agentao/runtime/` | Per-turn machinery extracted from `Agentao` — `ChatLoopRunner` (loop body), `ToolRunner` (4-phase tool pipeline: plan / execute / format / sanitize), `run_llm_call`, model/provider switching. |
| `agentao/host/` | **Public host contract.** `HostEvent`, `ToolLifecycleEvent`, `SubagentLifecycleEvent`, `PermissionDecisionEvent`, `EventStream`, `ActivePermissions`. Stability boundary for embedded hosts. |
| `agentao/harness/` | **Deprecated alias for `agentao.host`** (renamed in 0.4.2). Re-exports with old names + `DeprecationWarning`; scheduled for removal in 0.5.0. |
| `agentao/embedding/` | Host-side construction: `build_from_environment()` (env / dotenv / `.agentao/*.json` reads routed through explicit kwargs), `permission_loader`, `sessions`, `plugins/` (manifest loader, validators, MCP merge, resolvers). |
| `agentao/plugins/` | Plugin **runtime path** only — models, hooks, skill/agent validators. Loader lives in `embedding/plugins/`. |
| `agentao/permissions.py` + `permissions_hardline/` | `PermissionEngine` + shell-pattern hardline scanner (heredoc, contexts, decoder). |
| `agentao/cli/` | Interactive CLI **package** (was `cli.py` before 0.4.x). `app.py` (`AgentaoCLI`), `entrypoints.py` (argparse + `main`), `run.py` (`agentao run`), `commands/` (per-slash-command handlers), `subcommands.py`, `diagnostics_cli.py`. |
| `agentao/prompts/`, `agentao/agents/`, `agentao/plan/`, `agentao/capabilities/`, `agentao/tooling/`, `agentao/security/`, `agentao/session.py`, `agentao/context_manager.py` | Supporting modules — prompt assembly (`SystemPromptBuilder`), sub-agent runners, plan-mode state, capability declarations (incl. `capabilities/process.py::run_captured` — the shared hardened subprocess runner; see Common gotchas), tool registration (`tooling/registry.py::register_builtin_tools` + agent/MCP tool wiring), security utilities (`security/secret_scan.py` — the shared credential-pattern scanner behind `agentao.log`, `.agentao/tool-outputs/`, `MemoryGuard`, and replay; plus `path_policy.py` / `url_policy.py` — the latter exports **paired sync/async** surfaces, `validate_outbound_url`/`guarded_get` and `validate_outbound_url_async`/`guarded_get_async`; the async validator delegates to the sync one on a bounded daemon thread rather than reimplementing the policy, so there is exactly one copy of it; and `unicode_tags.py` — invisible-character smuggling defense, see below), session save/load, context-window compaction. |

### Tool system

All tools inherit from `Tool` (sync) or `AsyncToolBase` (async) in `agentao/tools/base.py`. Both are registered through the same `ToolRegistry` which converts them to OpenAI function-calling format.

`AsyncToolBase` dispatches through `runtime_loop` with a `CancellationToken`; cleanup-ack uses `_bridged()` `finally` + `threading.Event` so the runtime can cancel mid-tool. `RegistrableTool = Tool | AsyncToolBase`.

**`web_fetch` is the only built-in `AsyncToolBase`** (0.4.18) — everything else is a sync `Tool`. It had to be: it drives Playwright's async API, and a sync `execute()` reaching that from inside a caller's loop can only do it by blocking the loop. `WebSearchTool` deliberately stays sync (it never drove its own loop). `WebFetchTool.execute()` survives as a sync convenience wrapper for non-async embedders — it blocks a running loop, by construction, and a test asserts that so the cost stays documented. See Common gotchas for the thread-pool rules that come with it.

**Registration**: `agent.py::_register_tools()` is a thin delegation — the real wiring lives in `agentao/tooling/registry.py::register_builtin_tools()` (`agent_tools.py` / `mcp_tools.py` cover sub-agent and MCP tools). Note `agentao/tools/goal.py` is *not* registered by default (the CLI injects it via `add_tool` when a `/goal` is active).

**Confirmation / permissions**: `PermissionEngine` evaluates rules from `.agentao/permissions.json` (project) + `<home>/.agentao/permissions.json` (user). Precedence in `runtime/tool_planning.py::_decide` is three-tier, in this order: (1) the **read-only mode preset** short-circuits to `DENY` for any non-read-only tool *before* the engine is consulted at all (`:381-389`, reason `mode-preset:read-only`) — a permissions.json `allow` cannot override it; (2) otherwise the engine runs for **every** tool call, not only tools with `requires_confirmation=True`, and an `ALLOW`/`DENY` is final (`:391-399`); (3) only engine `ASK`-or-no-match falls through to the tool's own `requires_confirmation` (`:404-411`). Tier 2 matters for MCP: a rule can match `mcp_*` by name and govern tools whose `requires_confirmation` is `False` (which is what a `trust: true` server's tools return unless the server set `destructiveHint`) — so that attribute is a *fallback*, not the trigger. The engine itself does **no file I/O** — `agentao/embedding/permission_loader.py::load_permission_rules()` reads and passes `(rules, sources)` in. Default presets auto-allow common docs domains (`.github.com`, `.docs.python.org`, …) and auto-deny SSRF targets (`localhost`, `127.0.0.1`, `169.254.169.254`, …).

### Permission modes (replaces the old `allow_all_tools` flag)

`/mode read-only | workspace-write | full-access` switches the runtime's permission posture. `plan` is the fourth posture — entered via `/plan` interactively (not `/mode plan`), or `--permission-mode plan` on `agentao run`:

- `read-only` — blocks all write and shell tools.
- `workspace-write` — allows file writes and safe shell; asks for web (default).
- `full-access` — allows all tools without prompting.
- `plan` — LLM plans, does not execute; entered via `/plan`.

State is on `AgentaoCLI` (`agentao/cli/app.py`) and projected into prompts.

### System prompt composition

Built fresh on every `chat()` — `agent.py::_build_system_prompt()` delegates to `agentao/prompts/` (`SystemPromptBuilder`). Sections are ordered to keep the **stable prefix byte-identical across turns** so provider prompt-caching can reuse it; everything that changes within a session sits below that line. `builder.py::_build_sections()` is the authoritative order. The stable prefix ends at `<memory-stable>`; skills, todos, `<memory-context>` and the plan prompt form the volatile suffix. One non-obvious rule: available agents are suppressed in plan mode (delegation contradicts research-only intent).

**The date/time is *not* in the system prompt.** It is injected per-turn as a `<system-reminder>` prepended to the *user message* (`runtime/chat_loop/_runner.py::run`, `Current Date/Time: YYYY-MM-DD HH:MM:SS (Day)`) — keeping it out of the cached prefix is the whole point. `tests/test_date_in_prompt.py` asserts both halves.

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

Auto-discovered from `skills/`. Each subdir has `SKILL.md` (YAML frontmatter `name:` / `description:`) and optional `references/*.md`, which are **not** inlined — activation *enumerates* them by absolute path and tells the model to `read_file` what it needs (`skills/manager.py::activate_skill`). That is the whole point: the always-resident cost stays at name + description. The skill manager (`agentao/skills/`) maintains `available_skills` (all) and `active_skills` (this session). Cross-process locking via `filelock` — installs and updates are safe across concurrent CLI processes.

Activate via the `activate_skill` tool or `/skills activate <name>`.

### Memory system

**Architecture:** SQLite-backed storage managed by `MemoryManager` (`agentao/memory/manager.py`).

| Database | Path | Content |
|---|---|---|
| Project store | `.agentao/memory.db` | Project-scoped persistent memories + session summaries |
| User store | `<home>/.agentao/memory.db` | Cross-project user-scoped persistent memories |

**Three data types:**

1. **Persistent memories** (`MemoryRecord`) — rows in `memories`. Soft-deleted. Scoped `user` / `project`.
2. **Session summaries** (`SessionSummaryRecord`) — rows in `session_summaries`. Written by microcompaction / full LLM summarization. Scoped to `session_id`.
3. **Recall candidates** (`RecallCandidate`) — transient, in-memory, scored at query time by `MemoryRetriever`. Never stored.

**Prompt injection (per turn, two blocks):**
- `<memory-stable>` — stable persistent memories (budget-limited). Session summaries are intentionally excluded — they live in message history as `[Conversation Summary]` blocks.
- `<memory-context>` — top-k recall candidates against current user message.

**Separation of concerns:** the LLM can only write (`save_memory(key, value, tags?)`). Search, delete, clear are CLI-only (`/memory search|tag|delete|clear|user|project|session|status`) and call `MemoryManager` directly — never exposed as LLM tools.

See `docs/guides/memory-management.md`.

### Replay

`ReplayManager` (`agentao/replay/manager.py`) records every turn to `.agentao/replays/*.jsonl` when enabled. Replay state lives **outside** `Agentao` core — Transport emits `TURN_BEGIN` / `TURN_END` events that the manager subscribes to. Configure via `.agentao/settings.json :: replay.{enabled, max_instances}` or `/replay on|off`.

### MCP

External MCP servers via `.agentao/mcp.json` (project) + `<home>/.agentao/mcp.json` (global):

Transports (`mcp/config.py :: resolve_transport`, fail-closed): `command` → stdio, or `url` → **Streamable HTTP by default** (add `"type": "sse"` for the legacy SSE transport; `"type": "http"` is the explicit form). A bare `url` used to mean SSE — this is a **breaking change**. Tools are registered as `mcp_{server}_{tool}`. The MCP SDK is async-only; `McpClientManager` runs a dedicated event loop and bridges into sync Agentao via `run_until_complete()`.

**Both SDK majors are supported** (`mcp>=1.26.0,<3`). mcp 2.0 renamed every wire field to snake_case, moved to `httpx2`, changed `read_timeout_seconds` from `timedelta` to float, and dropped the third element from the Streamable HTTP stream tuple. `agentao/mcp/_compat.py` absorbs all four by **probing the installed SDK**, never by parsing a version string — read it before touching `client.py` / `tool.py`. Tests must build inputs from real `mcp.types` models: `SimpleNamespace` / `MagicMock` fakes hid every one of those breaks behind a green suite (`MagicMock` is actively harmful here — it answers `hasattr` for any name, so it satisfies a 2.x probe on a 1.x SDK).

**Protocol era: handshake first, escalate on a protocol rejection.** `McpClient._negotiate()` sends `initialize`, and only escalates to the modern era's `server/discover` when the server rejects it — definitely (`-32022`, which names the server's versions) or speculatively (`-32601`, since the modern era has no `initialize` handler at all; a failed speculative probe re-raises the *original* error). **This is deliberately the reverse of upstream's `mode='auto'`** — leading with the probe makes every python-mcp-1.x server reject the unknown method by dumping 258 lines of pydantic union-validation failure to its stderr, which for stdio *is* agentao's stderr (measured; see `docs/design/mcp-streamable-http.md` §5.8.1). The cost is that a dual-era server stays on the handshake era — fine while agentao only does tool discovery + tool calls, which behave identically in both. When that changes, flipping the order in `_negotiate` is the whole switch, and `test_a_dual_era_server_is_left_on_the_handshake_era` is the tripwire. An unresolvable mismatch raises `McpProtocolEraError`, whose *type* suppresses the "try `type: sse`" hint. The negotiated version is on `McpClient.protocol_version` / `get_server_status()["protocol"]` and is a **ceiling, not a constant** — gate on `>=`.

Key files: `agentao/mcp/config.py`, `client.py`, `tool.py`, `_compat.py`.

CLI: `/mcp list`, `/mcp add [--http|--sse] <name> <command|url>`, `/mcp remove <name>`.

### Logging

`agentao.log` captures every LLM request/response, all tool calls with formatted JSON arguments, tool results, token usage, timestamps. Nothing is truncated. Logger lives in `agentao/llm/client.py` — read this file first when debugging tool execution or LLM behavior.

Content is **not verbatim**: the file handler carries a `_RedactingFormatter` that rewrites credential-shaped strings to `[REDACTED:<kind>]` using the shared patterns in `agentao/security/secret_scan.py`. It is a `Formatter`, not a `Filter`, deliberately — a `Filter` mutates the shared `LogRecord` and would leak the redaction into every other handler on the logger, including an embedded host's own. If a debugging session needs the raw bytes, that formatter is the single place to bypass.

### CLI slash commands

The authoritative list with full subcommand syntax lives in `agentao/cli/help_text.py`; `/help` renders it. The high-impact commands to know about when reasoning about agent behavior:

- `/mode read-only|workspace-write|full-access` — permission posture (see Permission modes above).
- `/plan` / `/plan implement` / `/plan show` — plan mode (LLM plans, does not execute).
- `/goal <objective> [--for 30m] [--turns 10] [--unbounded]` — long-task auto-continuation with a time/turn budget; subcommands `show|budget|pause|resume|edit|clear`. Host-owned loop in `cli/input_loop.py::run_goal_continuation`; state in `.agentao/goal.json` (`cli/goal_state.py`); `update_goal` tool injected via `add_tool` (`tools/goal.py`). See Common gotchas for `--turns` vs `max_iterations`.
- `/clear` — saves current session, clears conversation + **all memories**, starts a new one.
- `/model`, `/provider`, `/temperature`, `/thinking` — LLM config. `/thinking [minimal|low|medium|high|off]` sets thinking depth (`reasoning_effort`) on the live client's `extra_body` passthrough (`cli/commands/provider.py::handle_thinking_command`); `off` clears it. No auto-recovery — a model that rejects `reasoning_effort` fails until `off` (see `docs/design/host-llm-extra-params.md`).

## Adding new components

Adding a built-in tool or a skill to this repo: see [docs/guides/adding-components.md](docs/guides/adding-components.md). The non-obvious part is that tools register in `agentao/tooling/registry.py::register_builtin_tools()`, not in `agent.py`.

## Common gotchas

- **`cli.py` was split into the `cli/` package** in 0.4.x. Older docs and design notes may still say `cli.py` — grep `agentao/cli/` for the actual handler.
- **`agentao.harness` → `agentao.host`** rename in 0.4.2. The old name is a deprecated alias scheduled for removal in 0.5.0. Use `agentao.host.HostEvent`, `export_host_acp_json_schema`, etc.
- **`allow_all_tools` is gone.** Use `/mode full-access` (or the equivalent host-API call) instead.
- **`agentao -p` is a shim** over `agentao run`. New automation should target `agentao run` directly — that's where the spec schema, Jinja2 templating, and exit codes are documented.
- **`Agentao` constructor takes 8 legacy callbacks** (`confirmation_callback`, `step_callback`, …) that emit `DeprecationWarning`. They will be removed in 0.5.0 — `agentao.embedding.compat` is the documented migration surface.
- **Don't intuition-audit architecture.** Before recommending borrowed patterns or claiming a gap exists, grep agentao to verify; subpackage `__init__.py` docstrings document intentional shims and rename trails.
- **`/goal --turns` is NOT `max_iterations`.** `--turns` caps the *outer* continuation count (how many `chat()` calls the goal loop drives); `max_iterations` caps the *inner* tool-call loop within a single `chat()`. They are orthogonal — both stay in force. The goal loop is host-owned (`cli/input_loop.py`), deliberately not the plugin `Stop`/`force_continue` path. Design: `docs/design/codex-goal-mechanism-review.md` §11.
- **Never move `arun` back onto the loop's default executor, and don't reach for `asyncio.to_thread` from an async tool.** `agent.py::arun` runs `chat()` on agentao's own `_get_arun_pool()`. This looks like pointless ceremony over `run_in_executor(None, ...)` and is not: a turn holds its worker for the *whole turn*, and partway through it blocks in `tool_executor._run_async_tool` waiting on a tool coroutine running on the host loop. Once concurrent turns reach the pool's worker count, anything on that loop needing a default-executor worker can never get one — and the turns are waiting on precisely that work. That includes code agentao does not control: `loop.getaddrinfo` submits to the default executor, so **every `httpx.AsyncClient` connect to a hostname** goes through it (measured with `trust_env=False`; with proxy env vars set httpx never resolves at all, which is how two earlier attempts at this measurement "proved" the opposite). Same reason `web.py` parses HTML on its own `_get_cpu_pool()`. Three pools, no contention: `agentao-arun-*` (turns), `agentao-web-html-*` (parsing), loop default (left free for httpx). Tests: `tests/test_web_fetch_async_tool.py` starves a one-worker default executor and asserts both still complete.

- **Unicode tag stripping is structural, not a range filter — don't "simplify" it.** `security/unicode_tags.py::strip_unicode_tags` removes invisible U+E0000–E007F characters (ASCII smuggling: they render as nothing, tokenize losslessly, and let a web page or MCP result carry instructions only the model sees). Applied at three boundaries: the model-bound copy of every tool result (`runtime/tool_result_formatter.py::_format_one`, *after* the replay emit so the audit record keeps the original bytes), model output re-entering the runtime (`runtime/sanitize.py::sanitize_text_field`), and the terminal display (`acp_client/render.py::_sanitize_terminal_text`, alongside the bidi controls). It is a transform applied at named boundaries — **not** an ambient guarantee; skill/MCP descriptions inlined into the system prompt do not pass through it.
  - The block's one legitimate use is RGI emoji tag sequences, so a blind range filter destroys every subdivision flag — 🏴󠁧󠁢󠁳󠁣󠁴󠁿/🏴󠁧󠁢󠁷󠁬󠁳󠁿/🏴󠁧󠁢󠁥󠁮󠁧󠁿 all collapse to 🏴 (a live defect in goose's own fix, #10745). A run survives only as `U+1F3F4` + ≤5 lowercase-alnum tag chars + `U+E007F`. **Both bounds are load-bearing**: the per-sequence cap alone bounds nothing, since chaining N valid sequences yields N×5 hidden characters — hence `_MAX_TAG_SEQUENCES` caps how many survive per string.
  - `strip_unicode_tags` must return **the same object** when nothing was dropped (compare the result, not just "are there tag chars"), because `_sanitize_str_field` turns a non-identical return into a logged security warning — otherwise ordinary text containing 🏴󠁧󠁢󠁳󠁣󠁴󠁿 reports as sanitized.
  - **`tool_calls[*].id` and `function.arguments` are exempt, and the two sanitize paths (`_normalize_one`, `sanitize_assistant_message`) must agree on that** — strip on one path only and the history id stops matching the answering `role: "tool"` message, which strict APIs reject. `id` must round-trip byte-for-byte (this is also why agentao needs no duplicate-id collision guard, unlike goose); `arguments` is raw JSON *text* whose escaping is provider-dependent, so stripping there is vacuous under `ensure_ascii=True` and mutates decoded values otherwise. Doing `arguments` properly means stripping post-decode, in the argument-parsing path.

- **Cancellation budgets have to fit inside `tool_executor._ASYNC_CANCEL_ACK_TIMEOUT_S` (5s).** That is how long the AsyncTool dispatcher waits for a cancelled coroutine's cleanup before emitting `TOOL_COMPLETE` regardless. Cleanup that runs longer detaches exactly the work it was accounting for, with the invocation already reported done — so `web.py`'s parse drain (3s) and its *cancelled-path* browser/driver teardown (2s each) both sit under it. Two tests pin the relation, because `web.py` importing from `runtime` would invert the layering. Note the normal (non-cancelled) browser close keeps its generous 10s: nothing is waiting on a deadline there, and a slow-but-working close should not become a killed driver.

- **A thread hand-off cancels the awaiter, never the worker.** Nothing interrupts a running Python call from outside it. `web.py::_in_worker` is the pattern to copy: `submit()` directly (so you hold the `concurrent.futures.Future`), cancel it if it hasn't started, otherwise wait for it under a bounded budget and log if you give up. `loop.run_in_executor` hides that future, which is why it isn't used there.

- **Don't call `subprocess.run` for batch commands — use `agentao/capabilities/process.py::run_captured()`.** A bare `subprocess.run(timeout=)` only kills the direct child on timeout, so a grandchild holding the captured pipe (Windows `git` credential helpers, a user hook backgrounding a process) hangs `communicate()` past the timeout — and over ACP-stdio a hung tool wedges the turn until the client times out and drops the connection. `run_captured` runs the child in its own process group/session, feeds/detaches stdin explicitly (`input=` over a pipe, else `DEVNULL` so a child can't read the JSON-RPC channel), kills the whole tree via `kill_process_tree()` on timeout (`taskkill /T` / `killpg(pid)` — never `getpgid`, which races a zombie child), and decodes with `errors="replace"`. It also defaults `env=` to `build_child_env()`, which strips agentao's own provider credentials (`HARNESS_ENV_KEYS`) from the child — so a plugin hook that shells out to the provider needs an explicit `env=` or `AGENTAO_SCRUB_CHILD_ENV=0`. `search_file_content` and the plugin hook dispatcher route through `run_captured`; `LocalShellExecutor.run` keeps its own streaming + inactivity-timeout loop but shares `kill_process_tree` and the same scrubbed base env. (PRs #73/#74/#75.)
