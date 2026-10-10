# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

It keeps the **rules and invariants that are easy to break**. The reasoning behind them lives in the linked design docs, in module docstrings, and in the cited PRs/issues. Read those before changing a rule.

## Package Management

**Always use `uv` for package management**, not pip:

```bash
uv sync                    # Install dependencies
uv add package-name        # Add a new dependency
uv run python script.py    # Run Python scripts
uv run agentao             # Run the CLI
```

Core deps live in `[project.dependencies]`; UI / fetch / tokenization deps are opt-in extras. A bare `pip install agentao` is library-only; `pip install 'agentao[cli]'` is the smallest interactive CLI. Both wire SDKs (`openai`, `anthropic`) are core and imported lazily. The `[pdf]` / `[excel]` / `[image]` / `[crypto]` / `[google]` extras were removed (`docs/design/optimization-opportunities-review.md` T1.1).

## Running

```bash
./run.sh                              # Quick start (interactive)
uv run agentao                        # Interactive CLI
uv run python -m agentao              # Same, via module entrypoint
uv run agentao run --prompt "..."     # Non-interactive automation (M0)
uv run agentao --acp --stdio          # ACP server (Issue 12)
```

`agentao run` is the canonical non-interactive surface. Exit codes: `0` ok, `1` runtime, `2` invalid usage, `3` permission/interaction, `4` max iterations, `130` interrupted. See `agentao/cli/run.py` and `docs/reference/configuration.md`. `agentao -p "..."` is a thin shim over `agentao run`.

## Testing

```bash
uv run python -m pytest tests/       # Default suite
uv run python -m pytest tests/ -n logical  # Parallel (pytest-xdist): CI on Windows, and on Linux for PRs (pushes to main stay serial)
uv run python -m pytest -m slow      # Clean-install smoke tests (needs `uv build` first)
uv run ruff check .                  # Lint gate — required CI check
(cd developer-guide && npm ci && npm run docs:build) && python3 scripts/check_guide_anchors.py developer-guide  # Guide pages + anchors — CI job
```

- `slow` is excluded by default (`addopts = "--tb=short -m 'not slow'"`). CI runs it in the **build** job (Python 3.12, after `uv build`), so it is a required check too.
- **`ruff check .` is required, so a green pytest run is not enough before pushing.** Defect rules only (`E9`, `F401`, `F402`, `F405`, `F811`, `F821`); rules and scope live in `pyproject.toml`, so the command is exactly what CI runs. `F401` is exempt under `agentao/` (re-exports for embedders). Suppress with a reason (`# noqa: F401 — pytest fixture injection`), never bare. Why `F405` is selected: `docs/design/lint-gate.md`.

## Configuration

```bash
cp .env.example .env       # Edit with OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
```

Every config file (`.env`, `.agentao/settings.json`, `permissions.json`, `mcp.json`, `acp.json`, `skills_config.json`, `AGENTAO.md`, memory DBs): paths, schema, defaults and precedence are in [docs/reference/configuration.md](docs/reference/configuration.md).

## Architecture

Agentao is an **embedded agent harness**: the same runtime drives the interactive CLI, `agentao run`, and the ACP server, and hosts may embed `Agentao(...)` directly. The boundary between host-facing contract and internal runtime is load-bearing — `docs/design/embedded-host-contract.md`, `docs/reference/host-api.md`.

> **Embedding Agentao into a *different* project?** Read `docs/guides/embed-for-agents.md`. This `CLAUDE.md` and `AGENTAO.md` are for working inside the Agentao repo.

### Subpackage map

| Path | Purpose |
|---|---|
| `agentao/agent.py` | `Agentao` — sync `chat()`, async `arun()`. Construction wires LLM, tools, skills, plugins, permissions, replay. |
| `agentao/runtime/` | Per-turn machinery: `ChatLoopRunner`, `ToolRunner` (plan / execute / format / sanitize), `run_llm_call`, model/provider switching. |
| `agentao/llm/` | `LLMClient` (retry / logging shell) over a wire adapter — see *LLM wire protocols*. |
| `agentao/compaction/` | `types.py` (contract, **stdlib-only imports**) and `coordinator.py` (`CompactionCoordinator`). `__init__.py` must never re-export `coordinator` — see Common gotchas. |
| `agentao/host/` | **Public host contract**: `HostEvent`, `ToolLifecycleEvent`, `SubagentLifecycleEvent`, `PermissionDecisionEvent`, `EventStream`, `ActivePermissions`, `TextDelta` / `TurnOutcome` (items of `Agentao.astream()`, never in replay). `TurnOutcome` lives in stdlib-only `agentao/outcome.py` because importing anything under `agentao.runtime` loads the chat loop. |
| `agentao/embedding/` | Host-side construction: `build_from_environment()`, `permission_loader`, `sessions`, `plugins/` (manifest loader, validators, MCP merge, resolvers). |
| `agentao/plugins/` | Plugin **runtime** only (models, hooks, validators); the loader is in `embedding/plugins/`. Hook subsystem in `plugins/hooks/`. |
| `agentao/permissions.py` + `permissions_hardline/` | `PermissionEngine` + the command floor. `_scanner.py::hardline_check()` runs `generic_floor()` on **every** dialect, plus the PowerShell table (`_windows.py`, `_powershell.py`). The pattern table is compiled on first use (`_patterns.py::_hardline_patterns_compiled`, #505). |
| `agentao/cli/` | Interactive CLI package: `app.py` (`AgentaoCLI`), `entrypoints.py`, `run.py`, `commands/`, `subcommands.py`, `diagnostics_cli.py`. |
| Supporting | `prompts/` (`SystemPromptBuilder`), `agents/` (sub-agents), `plan/`, `capabilities/` (incl. `process.py::run_captured`), `tooling/registry.py::register_builtin_tools`, `security/` (`secret_scan.py`, `path_policy.py`, `url_policy.py` — paired sync/async surfaces with **one** copy of the policy; `unicode_tags.py`), `context_manager.py`. Sessions: `embedding/sessions.py`. |

### Tool system

All tools inherit from `Tool` (sync) or `AsyncToolBase` (async) in `agentao/tools/base.py`, registered through one `ToolRegistry`. `AsyncToolBase` dispatches on `runtime_loop` with a `CancellationToken`.

- **`web_fetch` is the only built-in `AsyncToolBase`** (it drives Playwright's async API). `WebSearchTool` stays sync. `WebFetchTool.execute()` is a sync convenience wrapper that blocks a running loop by construction; a test pins that.
- **Registration** lives in `tooling/registry.py::register_builtin_tools()`, not `agent.py`. `tools/goal.py` is not registered by default (the CLI injects it while a `/goal` is active).
- **Origin is recorded at registration** (`ToolRegistry.register(origin=)`, default `host`, read with `.origin(name)`) and never inferred from the class (#256). **A new in-repo registration site must pass its origin**, or its tools are treated as host tools.

**Sub-agents execute from one registry**: the parent's live registry at spawn, narrowed to the definition's `tools:` list (`agents/tools/_wrapper.py::_narrow_tools`, `_inherit.py`; #238). Contents are swapped **in place** because the runner and planner hold the registry object. Rules:

- **Built-ins** are the child's own instances bound to the parent's `filesystem` / `shell`. `save_memory`'s write target is rebound to the parent's `MemoryManager` (#260); if the rebind cannot be made, `save_memory` is left out by name.
- **The child's own `MemoryManager` is on a transient store** (`_child_memory_manager`, #234): a sub-agent **reads no memories**, writes none of its session summaries or proposals into the parent's `memory.db`, and is briefed by `parent_context` instead. Restoring reads needs a child *view* whose `close()` does not close the parent's stores.
- **`mcp_*`** are the parent's instances over the parent's connections; the child connects nothing (#239).
- **Skills** come from `SkillManager.child_view()` at spawn — derived, never re-scanned (#254). `active_skills` starts empty and stays the child's.
- **Host tools** reach a sub-agent **only if the tool declares `copies_to_subagents`** (default `False`), as one `copy.copy` per spawn. Fail closed: an undeclared tool, a raising copy or declaration, a bound-method declaration (missing `@property`), or a `__copy__` returning `self` or a differently-named tool is left out by name with a warning. The declaration does not outrank `tools:`. Design: `docs/design/host-tool-injection.md`.
- **Left out:** agent tools and plan tools.
- **Permissions:** the sub-agent's engine is `parent_engine.snapshot()` (never a re-read of `permissions.json`), installed via `ToolRunner.set_permission_engine` because the planner holds its own reference. A **background** sub-agent gets `SdkTransport(confirm_tool=lambda *_: False)`. `NullTransport` approves everything, except a confirmation the MCP Skills gate asked (`transport/confirmation.py::gate_note`) — that one is refused, as are a callback-less `SdkTransport` and a `build_compat_transport()` without `confirmation_callback`.

**Tool-name repair resolves by spelling, never by similarity** (`runtime/name_repair.py`, #261; the docstring has the full reasoning). Normalise padding, case, separators, camelCase and a trailing `Tool` suffix on both sides; answer only on an exact match, fail closed on ties. **No `difflib`** — a withheld tool must never be reachable by a near-miss name. Known residual: `x_tool` resolves to an offered `x`.

**Confirmation / permissions**: `runtime/tool_planning.py::_decide` is three-tier, in order:
1. The **read-only mode preset** returns `DENY` for any non-read-only tool before the engine is consulted (`mode-preset:read-only`); a `permissions.json` allow cannot override it.
2. Otherwise the engine runs for **every** tool call; `ALLOW` / `DENY` is final. Rules can match `mcp_*` by name, including tools whose `requires_confirmation` is `False`.
3. Only engine `ASK` or no match falls through to the tool's `requires_confirmation` — a fallback, not the trigger.

The engine does **no file I/O**; `embedding/permission_loader.py::load_permission_rules()` reads `(rules, sources)` and passes them in. Default presets allow common docs domains and deny SSRF targets (`localhost`, `127.0.0.1`, `169.254.169.254`, …).

### Permission modes

`/mode read-only | workspace-write | full-access`; `plan` is entered via `/plan` (or `--permission-mode plan` on `agentao run`), not `/mode plan`.

- `read-only` — blocks write and shell tools; `activate_skill` and `todo_write` allowed, `save_memory` not. **Two switches** (runner flag + engine mode); `ToolRunner.readonly_active` honours either.
- `workspace-write` — file writes and safe shell; asks for web (default).
- `full-access` — all tools without prompting.
- `plan` — LLM plans, does not execute.

**Every switch goes through `runtime/permission_mode.py::apply_permission_mode`** (`Agentao.set_permission_mode(mode, cause=)` for hosts). It moves both switches and emits `READONLY_MODE_CHANGED` (only on a real flip) then `PERMISSION_MODE_CHANGED`. A new entry path calls the helper; never call `engine.set_mode` alone (it records nothing). Callers: `/mode` (`cli/app.py::_apply_mode`, `"cli"`), ACP `session/set_mode` (`"acp"`), `agentao run` (`"run"`), confirmation answer "2" (`"cli-allow-all"`), `/plan implement` (`"cli-plan-implement"`) — the last two stay off `_apply_mode` because their grants are session-only.

**Exception — a starting state emits nothing:** `Agentao(permission_mode=...)` / `build_from_environment(permission_mode=...)` go through `_set_initial_permission_mode`. `permission_mode=` refuses `"plan"`, builds `PermissionEngine(rules=[])`, and is mutually exclusive with `permission_engine=`. ACP's `current_mode_update` is a separate client-facing notification, not interchangeable.

### System prompt composition

**Instructions arrive in two messages, and the second is not in history.**
- `agent.py::_build_system_prompt()` — the stable prefix, byte-identical across a session's turns, ending at `<memory-stable>` (order: `builder.py::_build_sections()`). Available agents are suppressed in plan mode.
- `agent.py::_build_volatile_tail()` — active-skill bodies, todos, `<memory-context>`, plan prompt (`_build_volatile_sections()`), wrapped as one `<system-reminder>` and appended to the **outgoing request** as a trailing `user` message.

Invariants (`docs/design/llm-api-adapters.md` §2.3):
- **`messages_with_system` is the persistent prefix, never the request.** The tail is appended in exactly one place: `_call_llm_with_overflow_recovery`'s `_send`.
- **The tail is request-only.** The date/time and background-notification reminders are persisted on purpose; the tail never is.
- **The Tier-1 token anchor is recorded against the persistent prefix**: `record_api_usage(prompt_tokens, len(persistent), tail_tokens=est(T))`. Pinned by `tests/test_volatile_tail_request.py`.
- **The date/time is in neither**: it is a `<system-reminder>` prepended to the user message (`chat_loop/_runner.py::run`). Pinned by `tests/test_date_in_prompt.py`.

**Prompt-cache breakpoints are opt-in** (`LLM_PROMPT_CACHE=anthropic` / `prompt_cache=`): at most 3 `cache_control` markers, placed in the adapter's `build_request` and **copy-on-mark** (`llm/_cache_control.py`) so a marker never enters history. Never inferred from a URL or model name.

### Conversation flow

```
Agentao.chat() / Agentao.arun()
  └─ ChatLoopRunner.run()                  # runtime/chat_loop/_runner.py
       loop (max_iterations):
         ├─ run_llm_call(messages, tools)  # runtime/llm_call.py
         ├─ if tool_calls:
         │    └─ ToolRunner.run()          # runtime/tool_runner.py
         │         plan → execute → format → sanitize
         │           (gates: PermissionEngine + transport.confirm_tool)
         └─ else: return assistant text
```

`arun()` is the async path; sync `chat()` wraps it.

### Compaction

Design: `docs/design/compaction-orchestration-plan.md`. Five entry points hand off to one `CompactionCoordinator` (`agent.compaction_coordinator`):

| # | Entry point | `kind` | `reason` |
|---|---|---|---|
| 1 | Microcompaction (`runtime/chat_loop/_compaction.py`) | `microcompact` | `microcompact_threshold` |
| 2 | Threshold full (same file) | `full` | `compression_threshold` |
| 3 | API overflow, rung 1 (`runtime/chat_loop/_runner.py`) | `full` | `api_overflow` |
| 4 | API overflow, rung 2 (same file) | `minimal_history` | `api_overflow_after_compression` |
| 5 | Manual `/compact` (`cli/commands/compact.py`) | `full` | `manual_cli` |

Rules:
- **The coordinator owns whether to run, whose summary to take, and what to emit; `ContextManager` owns every content transform and neither imports nor holds a coordinator.** Shared types live in the neutral `types.py`.
- `compress_messages` is split: `prepare_compaction` (pure — **no SQLite write, no touch of `agent.messages`**) → decide → summarize → `commit_compaction` (the two SQLite writes + the new list). `_run_compaction` owns all three failure-counting points.
- **Circuit breaker** state stays in `ContextManager`: three consecutive automatic failures pause the threshold tier; `manual_cli` and `api_overflow` run as half-open probes (`_PROBE_REASONS`); success or `clear_history()` closes it. The coordinator never touches the counter. Public entry: `Agentao.compact()`.
- **Events:** `skipped` emits nothing. `CONTEXT_COMPRESSED` only on `success`; `COMPACTION_SETTLED` on `success | cancelled | failed`.
- **Turn cancel:** in-turn entry points pass the turn's token; `_run_compaction` checks it before summarizing **and again before the empty-summary failure count and the commit** (a cancelled retry returns an empty summary, which must not charge the breaker). A cancel raises `AgentCancelledError` with history untouched and no event.
- **Control plane:** command hooks first (`dispatch_pre_compact_decision`, first-cancel-wins), then `compaction_controller=` (keyword-only). **Anything but an explicit cancel means allow, including a raise.** `provide_summary` only from the controller. A cancelled threshold compaction is latched per `(kind, reason)` and cleared per turn (`runtime/turn.py`); a cancelled overflow returns the provider's context-length error.
- **The last rung is not a plain tail slice** — don't simplify it back. `_minimal_history_start` repairs the boundary so the window never opens on a `role: "tool"` message (drop leading results; only if that empties it, step back to the calling assistant). `prepare_minimal_history` reports the effective count. `minimal_history_would_help` returns `skipped` when there is no useful cut, and the runner returns the context-length error on **any** non-success at this rung.
- **Two token units:** `CONTEXT_COMPRESSED.pre/post_est_tokens` include the system prompt; `COMPACTION_SETTLED.pre/post_tokens_history` exclude it. Never wire one into the other.
- **Two windows:** `max_tokens` is the host's configured value and reads back unchanged; `effective_max_tokens = min(configured, observed, reported)` (observed from a parsed overflow error, reported from `llm.model_input_limit`; both only narrow). **Every internal budget uses effective**; `get_usage_stats()['max_tokens']` and ACP's `session/set_model` echo return configured. `parse_observed_context_limit` adopts nothing it is not certain of.
- `ContextManager.compress_messages()` is the legacy wrapper (signature pinned by tests); it bypasses the control plane and probe policy.
- **Summarizer input** (`_format_for_summary`): `<previous-summary>` is outside the newest-first eviction pool; carry ≤ half the budget and carry + live ≤ the budget; transcript survivors are always a **contiguous suffix**.
- `keep_recent_token_ratio` and `image_token_estimator` default to `None` (opt-in).

### Skills

Auto-discovered from `skills/`: each subdir has `SKILL.md` (frontmatter `name:` / `description:`) and optional `references/*.md`. **References are not inlined** — activation lists them by absolute path for `read_file` (`skills/manager.py::activate_skill`). `available_skills` (all) vs `active_skills` (this session). Activate via the `activate_skill` tool or `/skills activate <name>`.

- `skills_registry.json` (`skills/registry.py`) loads leniently, saves strictly: re-read under a `filelock`, merge only this instance's changes, `os.replace`, and refuse to overwrite an unreadable file. The lock does not serialize two processes replacing the same skill directory.
- **The available-skills catalogue renders only when the agent has `activate_skill`** (`prompts/builder.py::_available_skills_block`). The active-skills block is not gated.
- **The catalogue lists active skills too, and must keep doing so** (0.4.27): it sits in the cached prefix, so it may change only when the *enabled set* changes, never on activation. Pinned by `tests/test_skills_prompt.py`.

### Memory system

SQLite-backed, `MemoryManager` (`agentao/memory/manager.py`). Guide: `docs/guides/memory-management.md`.

| Database | Path | Content |
|---|---|---|
| Project store | `.agentao/memory.db` | Project memories + session summaries |
| User store | `<home>/.agentao/memory.db` | Cross-project user memories |

Data types: persistent memories (`memories`, soft-deleted, scope `user` / `project`); session summaries (`session_summaries`); recall candidates (in-memory only); review items (`memory_review_queue`, written by the crystallizer — **`/clear` and `/memory clear` do not reach it**; `/memory review reject <id>` one at a time).

- **A hard wipe is `MemoryManager.wipe_all()`** (#235), shared by `/clear`, `/memory clear` and hosts. **Check `ok`, not the counts.** It does not cover the review queue and is not erasure (soft delete).
- **Prompt injection per turn:** `<memory-stable>` (stable memories + up to 3 summaries from *previous* sessions via `get_cross_session_tail`; the current session's are already in history) and `<memory-context>` (top-k recall).
- **Scope downgrade is logged** (#260): a `user`-scope write without a user store lands in the project store; explicit `scope="user"` warns, an inferred one logs at debug. Never log `key` or `value`.
- **Both backings take the `RLock`** across the whole `_connect` scope. It closes races within one process only; two processes can still race `upsert_memory`'s read-then-write. A transient store reconnected after `close()` re-applies the schema and warns.
- **The LLM can only write** (`save_memory`). Search / delete / clear are CLI-only (`/memory ...`) and never LLM tools.

### Replay

`ReplayManager` (`agentao/replay/manager.py`) records turns to `.agentao/replays/*.jsonl`. Replay lives **outside** `Agentao` core: it subscribes to `TURN_BEGIN` / `TURN_END` from the transport. Config: `.agentao/settings.json :: replay.{enabled, max_instances}` or `/replay on|off`. Guide: `docs/guides/session-replay.md`.

### Plugin hooks

Eight events: `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `UserPromptSubmit`, `Stop`, `SessionStart`, `SessionEnd`, `PreCompact`. Runtime in `agentao/plugins/hooks/`; discovery in `embedding/plugins/`. Config: `docs/reference/configuration.md` §11. Design: `docs/design/hooks-claude-contract-conformance-plan.md`. Measured upstream behaviour: `docs/reference/hooks-probe-2.1.251.md`.

- **Contract is resolved per file by shape** (`_parser.py::_detect_entry_shape`): `hooks: []` without `type` → official; `type` without array → flat; **both** → the file is disabled; **neither** → a per-rule warning only.
- `agentao-v1` is **frozen**: every behaviour change is gated on contract, with a test for both halves. `claude-code@profile-1` is an **enumerated capability profile** (`_profile.py`, data). An unimplemented key is **ignored with a one-time diagnostic, never a schema error**. Adding a field means adding its row.
- Profile disposition (`accept` / `ignore`) is reported once per (rule, field); delivery (`honored` / `discarded`) is **silent**.
- **Precedence** is `_resolve.py::resolve`: exit 2 → `continue` → the event's own decision. Exit 2 means block, feed the model, or notify, per event.
- **A stop from a tool worker** rides `ToolExecutionResult.hook_stop_reason` → `ToolRunner.last_hook_stop` → `INCOMPLETE_HOOK_STOP`, arbitrated in **plan order, never completion order**. Reset `last_hook_stop` at the **top** of `execute()`. Both seams read a **string** (a `MagicMock` answers any attribute).
- **Mixed-contract dispatch** partitions, runs, merges once (`_dispatcher.py::_merge_pre_tool_use`): lattice `deny > ask > allow`; the reason tie-break ranks inside the winning class by declaration order.
- **`env=build_child_env({...})` is the only correct spelling** for the path placeholders; `env={...}` or `env=os.environ | {...}` drops the credential scrub.
- The profile matcher is `re.fullmatch` with `*` and `""` special-cased (`_matchers.py`). The diagnostic registry is **session**-scoped and keyed by rule *content* (`_diagnostics.py::rule_key`), never `id(rule)`.

### MCP

Config: `.agentao/mcp.json` (project) + `<home>/.agentao/mcp.json` (global). Key files: `agentao/mcp/config.py`, `client.py`, `tool.py`, `_compat.py`, `resources.py`, `resource_tools.py`, `skills.py`, `skill_tools.py`. CLI: `/mcp list`, `/mcp add [--http|--sse] <name> <command|url>`, `/mcp remove <name>`, `/mcp resources [server]`.

- **Transports** (`config.py::resolve_transport`, fail-closed): `command` → stdio; `url` → **Streamable HTTP by default** (`"type": "sse"` for legacy SSE). A bare `url` used to mean SSE (breaking change). Tools register as `mcp_{server}_{tool}`.
- **Threading:** `McpClientManager` runs one loop on its own thread (`agentao-mcp-loop`); sync callers submit with `run_coroutine_threadsafe` (#241). Calling the manager *from* that thread raises. Each `McpClient` holds its connection in an **owner task** that opens and closes the transport (#243). `disconnect_all(timeout=)` is final and bounded.
- **Both SDK majors** (`mcp>=1.26.0,<3`). `_compat.py` absorbs the 2.0 breaks by **probing the installed SDK, never parsing a version string** — read it before touching `client.py` / `tool.py`. **Tests must use real `mcp.types` models**, never `SimpleNamespace` / `MagicMock` fakes.
- **Protocol era: handshake first, escalate on a protocol rejection** (`McpClient._negotiate()`): `initialize`, then `server/discover` on `-32022` or `-32601`. **Deliberately the reverse of upstream `mode='auto'`** (`docs/design/mcp-streamable-http.md` §5.8.1). Exception: a `"skills": true` server leads with `server/discover`. Tripwire: `test_a_dual_era_server_is_left_on_the_handshake_era`. `McpClient.protocol_version` is a ceiling — gate on `>=`.
- **OAuth** (`docs/design/mcp-oauth.md`): URL servers get `StoredTokenAuth` unless stdio, `"oauth": false`, or an explicit `Authorization` header. Only `/mcp login` / `agentao mcp login` opens a browser; `agentao run` and ACP never do. Records in `~/.agentao/mcp-oauth/` keyed by canonical URL plus optional `oauth.profile` — **no profile = the URL alone; don't "simplify" the key**. ACP-supplied servers are always `"oauth": false`.
- **Resources** (`docs/design/mcp-resources.md`): three read-only tools in `mcp/resource_tools.py` (not `tools/`, to avoid an import cycle), registered only when a server declares `resources`. Each call checks **config → reconnect → live capability**, in that order. A binary is saved under the tool's own bound `working_directory` (no process-cwd fallback). Names in `MCP_RESOURCE_TOOL_NAMES`, outside `BUILTIN_TOOL_NAMES`, accepted by `disable_tools` / `enabled_tools`.
- **Skills** (`docs/design/mcp-skills.md`; mcp 2.x only; opt-in `"skills": true`, only `True` counts). Load-bearing rules:
  - One `McpSkills` per session on `SkillManager.mcp_skills`, **shared by reference** with `child_view()`. Gates read the held-entry map, never the active set. `/clear` starts a new conversation generation rather than wiping it.
  - Gates are `ToolCallPlanner.mcp_skill_gate`, consulted **after** the read-only and engine DENY paths; unapproved skill / `run_shell_command` / a tool with `spawns_shell_capable_agent is True` / cross-origin `read_mcp_resource` become ASK with reason `mcp-skill: <note>`. A gate that raises asks. A gated confirmation is never answered by a standing grant. A batch's own activations count as held while it plans and confirms (`begin_batch` / `end_batch`).
  - **Provenance**: recognised by tool provenance and where the result starts (`skills/provenance.py::is_skill_result`), never by a tag anywhere in the text. Origin markers count only as a whole line, never in an assistant message, and only on `agent_*` / `check_background_agent` results or a restore placeholder (`provenance.marker_origins`). Text handed on has markers stripped (`provenance.strip_markers`) and real origins re-added.
  - **New code that moves messages inside a live session (a compaction kind, a truncation) must carry the marker.** Session files back it up via `mcp_skill_origins`; restores re-activate no MCP skill (`embedding/sessions.py::withhold_mcp_skill_content`).
  - Skill content is never spilled to `.agentao/tool-outputs/`; `read_skill_file` (`mcp/skill_tools.py`, in `MCP_SKILL_TOOL_NAMES`) pages instead (`offset` / `limit`, ≤ 30,000 chars). That is not "never on disk": replay and `agentao.log` keep it verbatim. A `SKILL.md` over `MAX_SKILL_MD_BYTES` (100,000) is refused, not truncated. The `mcp:` name prefix is refused for local and plugin skills.
  - Test server: `tests/support/skills_mcp_server.py`.

### LLM wire protocols

Design: `docs/design/llm-api-adapters.md`. `LLMClient` (`llm/client.py`) is a retry / logging shell over one adapter chosen by `api_format` (`{PROVIDER}_API_FORMAT`): `openai-completions` (default, `_openai_completions.py`), `anthropic-messages` (`_anthropic_messages.py`), `openai-responses` (`_openai_responses.py`).

- **`api_format` is not the provider** and is never inferred from a URL, provider or model name; an unimplemented format fails closed. After construction only a provider switch changes it (`reconfigure(api_format=)`), and a wire change counts as a switch in `runtime/model.py::set_provider`'s sense.
- **History never changes shape**: `agent.messages` stay OpenAI dicts on every wire; adapters translate an outbound copy and build responses through `_StreamAccumulator` (`_stream_response.py`). A new wire adds an adapter, never a message model.
- **The Chat Completions adapter is held byte-identical** to a pre-extraction capture (`tests/test_llm_api_extraction_noop.py`, golden in `tests/data/`). **Never regenerate that golden from the current build.** `LLMClient.client` and its two latches stay on `LLMClient`.
- **Signed thinking**: Anthropic blocks ride `anthropic_thinking_blocks`, Responses reasoning rides `openai_reasoning_items`, attached at the two sites that record the model's own output (`chat_loop/_serialize.py::_attach_thinking_blocks`). **A new carrier key goes in `WIRE_CARRIER_KEYS` and nowhere else** — that tuple drives both recording and the purge on switch.
- **Responses wire is stateless** (`store: false`, no `previous_response_id`). A function call's id is stored as `call_id|fc_…` (`llm/_tool_ids.py`; split on the last `|` only when the tail starts `fc_`). `_with_wire_tool_ids` rewrites composite ids for Chat Completions (64-char limit) and **returns the same list when there are none** (keeps the golden true). A call's `fc_` item id goes back only beside the reasoning it was produced with. The `openai` SDK *yields* `error` / `response.failed`; the adapter must raise them.
- `openai` is unpinned above; a fresh install gets 3.x while `uv.lock` has 2.x. Check: `uv run --with 'openai==3.16.2' python -m pytest tests/ -n logical`.
- Anthropic SDK facts (measured): no `temperature` parameter; no non-streaming request above ~21k `max_tokens` (so `chat()` consumes a stream); an in-stream `error` arrives as `APIStatusError(status_code=200)`.
- **The Models API is asked in `prepare()`**, called before `_build_request_kwargs` — never at construction, never in `build_request`.
- **`usage.prompt_tokens` is the whole prompt** (`input_tokens + cache_creation + cache_read` on Anthropic). Don't copy the mapping across adapters.
- **Test a wire adapter against the real SDK**, replacing only the socket (`tests/support/anthropic_wire.py`, `tests/support/openai_responses_wire.py` — keep fixtures going through `stream_of`).

### Logging

`agentao.log` captures every LLM request/response, tool call, tool result and token usage, untruncated. The logger lives in `agentao/llm/client.py` — read it first when debugging tool execution or LLM behaviour. On `anthropic-messages` the canonical OpenAI-shaped list is logged, not the translated body.

Content is **redacted**: `_RedactingFormatter` rewrites credential-shaped strings using `security/secret_scan.py`. It is a `Formatter`, not a `Filter`, so redaction never leaks into an embedding host's handlers. It is the single place to bypass for raw bytes.

### CLI slash commands

The authoritative list is `agentao/cli/help_text.py` (`/help`). The ones that change agent behaviour:

- `/mode`, `/plan` / `/plan implement` / `/plan show` — see Permission modes.
- `/goal <objective> [--for 30m] [--turns 10] [--unbounded]` (+ `show|budget|pause|resume|edit|clear`) — host-owned loop in `cli/input_loop.py::run_goal_continuation`, state in `.agentao/goal.json`, `update_goal` injected via `add_tool`. See Common gotchas.
- `/clear` — saves the session, clears conversation + **all memories** via `MemoryManager.wipe_all()`; only the CLI's wording lives in `cli/_utils.py::wipe_all_memories`.
- `/model`, `/provider`, `/temperature`, `/thinking [minimal|low|medium|high|off]` (`cli/commands/provider.py::handle_thinking_command`). Per wire: `openai-completions` sets `reasoning_effort` in `extra_body`; `anthropic-messages` sets `output_config.effort` (levels from the Models API's `capabilities.effort`, else `low|medium|high|xhigh|max`; an unlisted word is refused); `openai-responses` sets `reasoning.effort`, keeps the host's other `reasoning` keys, and drops a carried-in `reasoning_effort`. No auto-recovery: a model that rejects the setting fails until `off` (`docs/design/host-llm-extra-params.md`).

## Adding new components

See [docs/guides/adding-components.md](docs/guides/adding-components.md). Tools register in `tooling/registry.py::register_builtin_tools()`, not `agent.py`. **A user-visible behaviour change or new public API is not done until `CHANGELOG.md` `[Unreleased]` has an entry and every doc naming the old behaviour is updated in both language twins.**

## Common gotchas

- **A capability probe must check the answer, not the attribute.** `hasattr` / `getattr(obj, name, None)` fails open at duck-typed seams (a bare-`def` `copies_to_subagents` is truthy; `wipe_all_memories` once trusted anything with a `wipe_all`). Type-check the result and fail closed. (`_REQUIRED_AGENT_ATTRS` in `cli/app.py` asserts only that an attribute exists.)
- **Docs come in en/zh twins** (`.zh.md`, `README.zh.md`, `developer-guide/en` + `/zh`). Update both; the stale one is not always zh. Parts of `docs/guides/` are Chinese-only.
- **Memory tests that pass without testing anything:** (1) the parent's store must be at `wd/.agentao/memory.db`, the CLI's layout; (2) a `:memory:` store read after `close()` is a fresh empty schema; (3) inject a swallowed failure *below* the layer that swallows it (the store's `clear_session_summaries`, not `MemoryManager.clear_all_session_summaries`).
- **Renamed / removed, still mentioned in older docs:** `cli.py` → `agentao/cli/`; `agentao.harness` → `agentao.host` (the word "harness" survives for the concept); `agentao.session` → `agentao.embedding.sessions` (`project_root` required, `None` refused); `allow_all_tools` → `/mode full-access`. `agentao -p` is a shim — target `agentao run`.
- **`Agentao(...)` has no callback kwargs and only five positional parameters** (0.5.0). Legacy callbacks are a `TypeError`; migrate via `agentao.embedding.compat.build_compat_transport` (`docs/migration/0.4.x-to-0.5.0.md`). **New `__init__` parameters go after the `*`.**
- **Don't intuition-audit architecture.** Grep before claiming a gap; subpackage `__init__.py` docstrings document intentional shims and rename trails.
- **`/goal --turns` is not `max_iterations`.** `--turns` caps outer `chat()` calls; `max_iterations` caps the inner tool loop. The goal loop is host-owned, not the plugin `Stop` / `force_continue` path (`docs/design/codex-goal-mechanism-review.md` §11).
- **Never move `arun` onto the loop's default executor, and don't use `asyncio.to_thread` from an async tool.** Turns run on `_get_arun_pool()`; HTML parsing on `web.py::_get_cpu_pool()`; the default executor is left free because `loop.getaddrinfo` (every `httpx.AsyncClient` hostname connect) needs it. Reasoning: `_get_arun_pool`'s docstring. Pinned by `tests/test_web_fetch_async_tool.py`.
- **`agentao/compaction/__init__.py` must not re-export `coordinator`**, and `types.py` imports only the standard library. **Held by review, not by a test** — import-layering rule 5 does not reach this file.
- **Unicode tag stripping is structural, not a range filter** — don't simplify it (`security/unicode_tags.py`; the docstring has the reasoning). Applied at four named boundaries: tool results (`tool_result_formatter.py::_format_one`, *after* the replay emit), model output (`sanitize.py::sanitize_text_field`), and two terminal displays via `security/terminal_text.py::sanitize_terminal_text` (server text in `acp_client/render.py`, model text in `cli/transport.py::_display`). At a Rich boundary also apply `rich.markup.escape` (`[black on black]` hides text with no control byte); `terminal_text.py` stays a leaf and does not import `rich` (`tests/test_import_layering.py` rule 3). Not ambient: skill/MCP descriptions in the system prompt are not stripped.
  - RGI emoji tag sequences survive (`U+1F3F4` + ≤5 tag chars + `U+E007F`), capped by `_MAX_TAG_SEQUENCES`. Both bounds are load-bearing.
  - `strip_unicode_tags` must return **the same object** when nothing was dropped.
  - **`tool_calls[*].id` and `function.arguments` are exempt on both sanitize paths** (`_normalize_one`, `sanitize_assistant_message`); they must agree.
- **A model/provider switch purges thinking artifacts** (`runtime/model.py::purge_thinking_artifacts`): `reasoning_content` and `thought_signature` at both the `tool_calls[*]` entry and its `function`, plus the wire carrier keys. Wired into `set_model` / `set_provider` (model or endpoint change) and both history-restore sites (`/resume`, `acp/session_load.py`). Unconditional on purpose.
- **Cancellation budgets must fit inside `tool_executor._ASYNC_CANCEL_ACK_TIMEOUT_S` (5s)**: `web.py`'s parse drain (3s) and cancelled-path teardown (2s each) sit under it; two tests pin the relation. The normal browser close keeps 10s.
- **A thread hand-off cancels the awaiter, never the worker.** Copy `web.py::_in_worker`: `submit()` directly, cancel if not started, else wait under a bounded budget and log on give-up.
- **Use `capabilities/process.py::run_captured()`, not `subprocess.run`, for batch commands** (PRs #73–#75): `subprocess.run(timeout=)` kills only the direct child, so a grandchild holding the pipe hangs the turn. `run_captured` isolates the process group, detaches stdin (`DEVNULL` unless `input=`), kills the whole tree on timeout (`kill_process_tree`; `killpg(pid)`, never `getpgid`), decodes with `errors="replace"`, and defaults `env=` to `build_child_env()` (strips provider credentials; opt out with explicit `env=` or `AGENTAO_SCRUB_CHILD_ENV=0`). `LocalShellExecutor.run` keeps its own streaming loop but shares `kill_process_tree` and the scrubbed env.
- **`run_loop` (`cli/input_loop.py`) imports `sys` locally further down**, so `sys.exit()` near its top raises `UnboundLocalError` — use `raise SystemExit(...)` there (#507).
