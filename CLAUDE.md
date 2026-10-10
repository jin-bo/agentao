# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This file keeps the **rules and invariants that are easy to break**. The reasons for them are in the linked design docs, in module docstrings, and in the cited PRs and issues. Read those before you change a rule.

## Package Management

**Always use `uv` for package management.** Do not use pip:

```bash
uv sync                    # Install dependencies
uv add package-name        # Add a new dependency
uv run python script.py    # Run Python scripts
uv run agentao             # Run the CLI
```

- Core dependencies are in `[project.dependencies]`. UI, fetch and tokenization dependencies are opt-in extras. Both wire SDKs (`openai`, `anthropic`) are core, imported lazily.
- A bare `pip install agentao` installs the library only. `pip install 'agentao[cli]'` is the smallest interactive CLI.
- The `[pdf]` / `[excel]` / `[image]` / `[crypto]` / `[google]` extras were removed (`docs/design/optimization-opportunities-review.md` T1.1).

## Running

```bash
./run.sh                              # Quick start (interactive)
uv run agentao                        # Interactive CLI
uv run python -m agentao              # Same, via module entrypoint
uv run agentao run --prompt "..."     # Non-interactive automation (M0)
uv run agentao --acp --stdio          # ACP server (Issue 12)
```

`agentao run` is the canonical non-interactive surface. Its exit codes are `0` ok, `1` runtime, `2` invalid usage, `3` permission/interaction, `4` max iterations, `130` interrupted. See `agentao/cli/run.py` and `docs/reference/configuration.md`. `agentao -p "..."` is a thin shim over `agentao run`.

## Testing

```bash
uv run python -m pytest tests/       # Default suite
uv run python -m pytest tests/ -n logical  # Parallel (pytest-xdist): CI on Windows, and on Linux for PRs (pushes to main stay serial)
uv run python -m pytest -m slow      # Clean-install smoke tests (needs `uv build` first)
uv run ruff check .                  # Lint gate — required CI check
(cd developer-guide && npm ci && npm run docs:build) && python3 scripts/check_guide_anchors.py developer-guide  # Guide pages + anchors — CI job
```

- The default run excludes `slow` (`addopts = "--tb=short -m 'not slow'"`). CI runs it in the **build** job (Python 3.12, after `uv build`). It is a required check too.
- **`ruff check .` is a required check. A green pytest run is not enough before you push.**
  - Defect rules only: `E9`, `F401`, `F402`, `F405`, `F811`, `F821`. Rules and scope are in `pyproject.toml`, so the command matches CI.
  - `F401` does not apply under `agentao/`, because embedders use those re-exports.
  - Suppress a finding with a reason (`# noqa: F401 — pytest fixture injection`). Never use a bare `noqa`.
  - Why the gate selects `F405`: `docs/design/lint-gate.md`.

## Configuration

```bash
cp .env.example .env       # Edit with OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
```

[docs/reference/configuration.md](docs/reference/configuration.md) gives the paths, schema, defaults and precedence of every config file: `.env`, `.agentao/settings.json`, `permissions.json`, `mcp.json`, `acp.json`, `skills_config.json`, `AGENTAO.md` and the memory DBs.

## Architecture

Agentao is an **embedded agent harness**. One runtime drives the interactive CLI, `agentao run` and the ACP server. Hosts can also embed `Agentao(...)` directly. The boundary between the host-facing contract and the internal runtime is load-bearing. See `docs/design/embedded-host-contract.md` and `docs/reference/host-api.md`.

> **Do you embed Agentao into a *different* project?** Read `docs/guides/embed-for-agents.md`. This `CLAUDE.md` and `AGENTAO.md` are for work inside the Agentao repo.

### Subpackage map

| Path | Purpose |
|---|---|
| `agentao/agent.py` | `Agentao`: sync `chat()`, async `arun()`. The constructor wires the LLM, tools, skills, plugins, permissions and replay. |
| `agentao/runtime/` | Per-turn machinery: `ChatLoopRunner`, `ToolRunner` (plan / execute / format / sanitize), `run_llm_call`, model and provider switches. |
| `agentao/llm/` | `LLMClient` (retry and logging shell) over a wire adapter. See *LLM wire protocols*. |
| `agentao/compaction/` | `types.py` (the contract, **stdlib-only imports**) and `coordinator.py` (`CompactionCoordinator`). `__init__.py` must never re-export `coordinator`. See Common gotchas. |
| `agentao/host/` | **Public host contract**: `HostEvent`, `ToolLifecycleEvent`, `SubagentLifecycleEvent`, `PermissionDecisionEvent`, `EventStream`, `ActivePermissions`, `TextDelta` / `TurnOutcome`. The last two are items of `Agentao.astream()` and never go into replay. `TurnOutcome` is in the stdlib-only `agentao/outcome.py`, because any import under `agentao.runtime` loads the chat loop. |
| `agentao/embedding/` | Host-side construction: `build_from_environment()`, `permission_loader`, `sessions`, `plugins/` (manifest loader, validators, MCP merge, resolvers). |
| `agentao/plugins/` | The plugin **runtime** only (models, hooks, validators). The loader is in `embedding/plugins/`. The hook subsystem is in `plugins/hooks/`. |
| `agentao/permissions.py` + `permissions_hardline/` | `PermissionEngine` and the command floor. `_scanner.py::hardline_check()` runs `generic_floor()` on **every** dialect, then the PowerShell table (`_windows.py`, `_powershell.py`). The pattern table compiles on first use (`_patterns.py::_hardline_patterns_compiled`, #505). |
| `agentao/cli/` | Interactive CLI package: `app.py` (`AgentaoCLI`), `entrypoints.py`, `run.py`, `commands/`, `subcommands.py`, `diagnostics_cli.py`. |
| Supporting | `prompts/` (`SystemPromptBuilder`), `agents/` (sub-agents), `plan/`, `capabilities/` (with `process.py::run_captured`), `tooling/registry.py::register_builtin_tools`, `security/`, `context_manager.py`. Sessions: `embedding/sessions.py`. |

`security/` holds `secret_scan.py`, `path_policy.py`, `unicode_tags.py` and `url_policy.py`. `url_policy.py` has paired sync and async surfaces, but only **one** copy of the policy.

### Tool system

All tools inherit from `Tool` (sync) or `AsyncToolBase` (async) in `agentao/tools/base.py`. One `ToolRegistry` registers both kinds. `AsyncToolBase` dispatches on `runtime_loop` with a `CancellationToken`.

- **`web_fetch` is the only built-in `AsyncToolBase`**, because it drives the async Playwright API. `WebSearchTool` stays sync. `WebFetchTool.execute()` is a sync wrapper that blocks a running loop by construction (a test pins it).
- **Registration is in `tooling/registry.py::register_builtin_tools()`**, not in `agent.py`. `tools/goal.py` is not registered by default. The CLI injects it while a `/goal` is active.
- **The registry records the origin at registration** (`ToolRegistry.register(origin=)`, default `host`, read with `.origin(name)`). Never infer the origin from the class (#256).
- **A new in-repo registration site must pass its origin.** Else the runtime treats its tools as host tools.

**Sub-agents execute from one registry.** At spawn, the child gets the parent's live registry, narrowed to the `tools:` list of its definition (`agents/tools/_wrapper.py::_narrow_tools`, `_inherit.py`, #238). The runtime swaps the contents **in place**, because the runner and the planner hold the registry object.

- **Built-ins** are the child's own instances, bound to the parent's `filesystem` and `shell`. The write target of `save_memory` is rebound to the parent's `MemoryManager` (#260). If the rebind fails, the child gets no `save_memory`.
- **The child's own `MemoryManager` uses a transient store** (`_child_memory_manager`, #234). A sub-agent **reads no memories**, writes no session summaries or proposals into the parent's `memory.db`, and gets its briefing from `parent_context`. To add memory reads, use a child *view* whose `close()` does not close the parent's stores.
- **`mcp_*`** tools are the parent's instances, on the parent's connections. The child connects nothing (#239).
- **Skills** come from `SkillManager.child_view()` at spawn. They are derived and never re-scanned (#254). `active_skills` starts empty and stays the child's.
- **A host tool reaches a sub-agent only if it declares `copies_to_subagents`** (default `False`), as one `copy.copy` per spawn (`docs/design/host-tool-injection.md`). The check fails closed. These are left out, each with a warning:
  - a tool without the declaration.
  - a copy or a declaration that raises.
  - a declaration that is a bound method (a missing `@property`).
  - a `__copy__` that returns `self` or a tool with a different name.
- The declaration does not outrank the `tools:` list. **The child never gets** agent tools or plan tools.
- **Permissions:** the child's engine is `parent_engine.snapshot()`, never a re-read of `permissions.json`. Install it with `ToolRunner.set_permission_engine`, because the planner holds its own reference. A **background** sub-agent gets `SdkTransport(confirm_tool=lambda *_: False)`.
- `NullTransport` approves everything, with one exception. It refuses a confirmation that the MCP Skills gate asked (`transport/confirmation.py::gate_note`). A callback-less `SdkTransport` and a `build_compat_transport()` without `confirmation_callback` also refuse it.

**Tool-name repair uses spelling, never similarity** (`runtime/name_repair.py`, #261). The docstring gives the full reasoning.
- Normalise padding, case, separators, camelCase and a trailing `Tool` suffix, on both sides.
- Answer only on an exact match. Fail closed on a tie.
- **Do not use `difflib`.** A near-miss name must never reach a withheld tool.
- Known residual: `x_tool` resolves to an offered `x`.

**Confirmation and permissions:** `runtime/tool_planning.py::_decide` has three tiers, in this order:
1. The **read-only mode preset** returns `DENY` for any tool that is not read-only, before the engine runs (`mode-preset:read-only`). A `permissions.json` allow cannot override it.
2. Then the engine runs for **every** tool call. `ALLOW` and `DENY` are final. Rules can match `mcp_*` by name, also for tools whose `requires_confirmation` is `False`.
3. Only an engine `ASK` or no match falls through to the tool's `requires_confirmation`. That attribute is a fallback, not the trigger.

The engine does **no file I/O**. `embedding/permission_loader.py::load_permission_rules()` reads `(rules, sources)` and passes them in. The default presets allow common docs domains and deny SSRF targets (`localhost`, `127.0.0.1`, `169.254.169.254`, …).

### Permission modes

Use `/mode read-only | workspace-write | full-access` to change modes. To enter `plan`, use `/plan` (or `--permission-mode plan` on `agentao run`), not `/mode plan`.

- `read-only`: blocks write and shell tools. It allows `activate_skill` and `todo_write`, but not `save_memory`. This mode has **two switches**: the runner flag and the engine mode. `ToolRunner.readonly_active` honours either.
- `workspace-write`: allows file writes and safe shell, and asks for web. This is the default.
- `full-access`: allows all tools without a prompt.
- `plan`: the LLM plans and does not execute.

**Every switch goes through `runtime/permission_mode.py::apply_permission_mode`.** Hosts call it as `Agentao.set_permission_mode(mode, cause=)`.
- It moves both switches.
- It emits `READONLY_MODE_CHANGED` (only on a real flip), then `PERMISSION_MODE_CHANGED`.
- A new entry path must call this helper. Never call `engine.set_mode` alone, because it records nothing.

Callers and their `cause`: `/mode` (`cli/app.py::_apply_mode`, `"cli"`), ACP `session/set_mode` (`"acp"`), `agentao run` (`"run"`), the answer "2" at a confirmation prompt (`"cli-allow-all"`), `/plan implement` (`"cli-plan-implement"`). The last two do not use `_apply_mode`, because their grants are for this session only.

**Exception: a starting state emits nothing.**
- `Agentao(permission_mode=...)` and `build_from_environment(permission_mode=...)` go through `_set_initial_permission_mode`.
- `permission_mode=` refuses `"plan"`, builds `PermissionEngine(rules=[])`, and cannot be combined with `permission_engine=`.
- ACP's `current_mode_update` is a different notification, for the client. Do not use one in place of the other.

### System prompt composition

**The model gets its instructions in two messages. The second message is not in history.**
- `agent.py::_build_system_prompt()` builds the stable prefix. The prefix is byte-identical across the turns of a session and ends at `<memory-stable>`. `builder.py::_build_sections()` sets the order. Plan mode suppresses the available agents.
- `agent.py::_build_volatile_tail()` builds the volatile tail: active-skill bodies, todos, `<memory-context>` and the plan prompt (`_build_volatile_sections()`). It wraps them in one `<system-reminder>` and appends it to the **outgoing request** as a trailing `user` message.

Invariants (`docs/design/llm-api-adapters.md` §2.3):
- **`messages_with_system` is the persistent prefix, never the request.** Only one place appends the tail: the `_send` in `_call_llm_with_overflow_recovery`.
- **The tail is for the request only.** History never stores it. The date/time and background-notification reminders go into history on purpose.
- **Record the Tier-1 token anchor against the persistent prefix**: `record_api_usage(prompt_tokens, len(persistent), tail_tokens=est(T))`. `tests/test_volatile_tail_request.py` pins this.
- **The date/time is in neither message.** It is a `<system-reminder>` at the start of the user message (`chat_loop/_runner.py::run`). `tests/test_date_in_prompt.py` pins this.

**Prompt-cache breakpoints are opt-in** (`LLM_PROMPT_CACHE=anthropic` / `prompt_cache=`). A request gets at most 3 `cache_control` markers. The adapter's `build_request` places them **copy-on-mark** (`llm/_cache_control.py`), so a marker never enters history. Never infer this setting from a URL or a model name.

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

`arun()` is the async path. The sync `chat()` wraps it.

### Compaction

Design: `docs/design/compaction-orchestration-plan.md`. Five entry points hand off to one `CompactionCoordinator` (`agent.compaction_coordinator`):

| # | Entry point | `kind` | `reason` |
|---|---|---|---|
| 1 | Microcompaction (`runtime/chat_loop/_compaction.py`) | `microcompact` | `microcompact_threshold` |
| 2 | Threshold full (same file) | `full` | `compression_threshold` |
| 3 | API overflow, rung 1 (`runtime/chat_loop/_runner.py`) | `full` | `api_overflow` |
| 4 | API overflow, rung 2 (same file) | `minimal_history` | `api_overflow_after_compression` |
| 5 | Manual `/compact` (`cli/commands/compact.py`) | `full` | `manual_cli` |

**Ownership:** the coordinator decides whether to run, whose summary to take, and what to emit. `ContextManager` does every content transform, and neither imports nor holds a coordinator. Shared types are in the neutral `types.py`.

**The pipeline:** `compress_messages` has four steps:
1. `prepare_compaction` is pure. It does **no SQLite write and does not touch `agent.messages`**.
2. Decide.
3. Summarize.
4. `commit_compaction` does the two SQLite writes and sets the new list.

`_run_compaction` owns all three failure-counting points.

**The circuit breaker:** its state stays in `ContextManager`, and the coordinator never touches the counter.
- Three consecutive automatic failures pause the threshold tier.
- `manual_cli` and `api_overflow` run as half-open probes (`_PROBE_REASONS`). A success or `clear_history()` closes the breaker.
- The public entry is `Agentao.compact()`.

**Events:** `skipped` emits nothing. `CONTEXT_COMPRESSED` fires only on `success`. `COMPACTION_SETTLED` fires on `success | cancelled | failed`.

**Turn cancel:**
- In-turn entry points pass the turn's token to the summarizer. `_run_compaction` checks it before it summarizes.
- **It checks again before the empty-summary failure count and before the commit.** A cancelled retry returns an empty summary, and that must not charge the breaker.
- A cancel raises `AgentCancelledError`. History stays the same, and no event fires.

**The control plane:**
- Command hooks run first (`dispatch_pre_compact_decision`, first cancel wins). `compaction_controller=` (keyword-only) runs only if every hook allowed.
- **Anything but an explicit cancel means allow. A raise also means allow.**
- Only the controller can give `provide_summary`.
- A cancelled threshold compaction enters a latch, per `(kind, reason)`. `runtime/turn.py` clears the latch each turn.
- A cancelled overflow returns the provider's context-length error.

**The last rung is not a plain tail slice. Do not simplify it back to one.**
- `_minimal_history_start` repairs the boundary, so the window never opens on a `role: "tool"` message.
- The repair drops leading results first. Only if that empties the window does it step back to the assistant that made the calls.
- `prepare_minimal_history` reports the effective count.
- `minimal_history_would_help` returns `skipped` when no cut is useful.
- At this rung, the runner returns the context-length error on **any** result that is not success.

**Two token units:** `CONTEXT_COMPRESSED.pre/post_est_tokens` include the system prompt. `COMPACTION_SETTLED.pre/post_tokens_history` exclude it. Never wire one into the other.

**Two windows:**
- `max_tokens` is the value the host configured. It reads back unchanged.
- `effective_max_tokens = min(configured, observed, reported)`. A parsed overflow error gives `observed`. `llm.model_input_limit` gives `reported`. Both can only make the window smaller.
- **Every internal budget uses the effective value.**
- `get_usage_stats()['max_tokens']` and the ACP `session/set_model` echo return the configured value.
- `parse_observed_context_limit` adopts no value unless it is certain.

- `ContextManager.compress_messages()` is the legacy wrapper. Tests pin its signature. It bypasses the control plane and the probe policy.
- **Summarizer input** (`_format_for_summary`):
  - `<previous-summary>` is outside the newest-first eviction pool.
  - The carried summary uses at most half the budget. Carry plus live uses at most the whole budget.
  - The surviving transcript is always a **contiguous suffix**.
- `keep_recent_token_ratio` and `image_token_estimator` default to `None`. They are opt-in.

### Skills

The skill manager discovers skills in `skills/`. Each subdirectory has a `SKILL.md` (frontmatter `name:` / `description:`) and optional `references/*.md`.
- **Activation does not inline the references.** It lists them by absolute path for `read_file` (`skills/manager.py::activate_skill`).
- `available_skills` holds all skills, `active_skills` those of this session. Activate with the `activate_skill` tool or `/skills activate <name>`.
- `skills_registry.json` (`skills/registry.py`) loads leniently and saves strictly. A save:
  1. re-reads the file under a `filelock`.
  2. merges only the changes of this instance.
  3. swaps the file in with `os.replace`.
  4. refuses to overwrite a file that it cannot read.
- The lock does not serialize two processes that replace the same skill directory.
- **The available-skills catalogue renders only when the agent has `activate_skill`** (`prompts/builder.py::_available_skills_block`). No such gate applies to the active-skills block.
- **The catalogue lists active skills too, and it must continue to do so** (0.4.27). The catalogue is in the cached prefix. It can change only when the *enabled set* changes, never on activation. `tests/test_skills_prompt.py` pins this.

### Memory system

`MemoryManager` (`agentao/memory/manager.py`) stores memory in SQLite. Guide: `docs/guides/memory-management.md`.

| Database | Path | Content |
|---|---|---|
| Project store | `.agentao/memory.db` | Project memories + session summaries |
| User store | `<home>/.agentao/memory.db` | Cross-project user memories |

Data types:
- Persistent memories: table `memories`, soft-deleted, scope `user` or `project`.
- Session summaries: table `session_summaries`.
- Recall candidates: in memory only.
- Review items: table `memory_review_queue`, written by the crystallizer. **`/clear` and `/memory clear` do not reach this table.** Use `/memory review reject <id>`, one item at a time.

- **A hard wipe is `MemoryManager.wipe_all()`** (#235), for `/clear`, `/memory clear` and hosts. **Check `ok`, not the counts.** The wipe does not cover the review queue. It is a soft delete, not erasure.
- **Prompt injection per turn:**
  - `<memory-stable>`: stable memories, plus up to 3 summaries from *previous* sessions (`get_cross_session_tail`). The summaries of the current session are already in history.
  - `<memory-context>`: the top-k recall.
- **A scope downgrade gets a log entry** (#260). A `user`-scope write without a user store goes to the project store. An explicit `scope="user"` logs a warning, an inferred one logs at debug. Never log `key` or `value`.
- **Both backings hold the `RLock`** for the whole `_connect` scope. This stops races in one process only. Two processes can still race on the read-then-write in `upsert_memory`.
- If a transient store reconnects after `close()`, it re-applies the schema and logs a warning.
- **The LLM can only write memories** (`save_memory`). Search, delete and clear are CLI-only (`/memory ...`). Never expose them as LLM tools.

### Replay

`ReplayManager` (`agentao/replay/manager.py`) records turns to `.agentao/replays/*.jsonl`.
- Replay is **outside** the `Agentao` core. It subscribes to `TURN_BEGIN` / `TURN_END` from the transport.
- Configure it in `.agentao/settings.json :: replay.{enabled, max_instances}` or with `/replay on|off`.
- Guide: `docs/guides/session-replay.md`.

### Plugin hooks

There are eight events: `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `UserPromptSubmit`, `Stop`, `SessionStart`, `SessionEnd`, `PreCompact`.
- Runtime: `agentao/plugins/hooks/`. Discovery: `embedding/plugins/`. Config: `docs/reference/configuration.md` §11.
- Design: `docs/design/hooks-claude-contract-conformance-plan.md`. Measured upstream behaviour: `docs/reference/hooks-probe-2.1.251.md`.

**Contract detection.** The parser finds the contract of each file from its shape (`_parser.py::_detect_entry_shape`):
- `hooks: []` without `type`: official.
- `type` without an array: flat.
- **Both**: the parser disables the file.
- **Neither**: the parser gives a warning for that rule only.

**The two contracts:**
- `agentao-v1` is **frozen**. Gate every behaviour change on the contract, and test both halves.
- `claude-code@profile-1` is an **enumerated capability profile** (`_profile.py`, data).
- The dispatcher **ignores an unimplemented key with a one-time diagnostic, never a schema error.** To add a field, add its row.
- The dispatcher reports profile disposition (`accept` / `ignore`) once per (rule, field). Delivery (`honored` / `discarded`) is **silent**.

**Precedence** is `_resolve.py::resolve`: exit 2, then `continue`, then the event's own decision. Exit 2 can mean block, feed the model, or notify, as each event defines.

**A stop from a tool worker:**
- The verdict travels on `ToolExecutionResult.hook_stop_reason`, then `ToolRunner.last_hook_stop`, then `INCOMPLETE_HOOK_STOP`.
- The runner selects it in **plan order, never completion order**.
- Reset `last_hook_stop` at the **top** of `execute()`.
- Both seams read a **string**, because a `MagicMock` answers any attribute.

**Mixed-contract dispatch** partitions, runs, and merges once (`_dispatcher.py::_merge_pre_tool_use`). The lattice is `deny > ask > allow`. The reason tie-break ranks in the winning class, by declaration order.

- **For the path placeholders, the only correct spelling is `env=build_child_env({...})`.** `env={...}` or `env=os.environ | {...}` removes the credential scrub.
- The profile matcher is `re.fullmatch`, with special cases for `*` and `""` (`_matchers.py`).
- The diagnostic registry has **session** scope. Its key is the rule *content* (`_diagnostics.py::rule_key`), never `id(rule)`.

### MCP

- Config: `.agentao/mcp.json` (project) and `<home>/.agentao/mcp.json` (global).
- Key files: `agentao/mcp/config.py`, `client.py`, `tool.py`, `_compat.py`, `resources.py`, `resource_tools.py`, `skills.py`, `skill_tools.py`.
- CLI: `/mcp list`, `/mcp add [--http|--sse] <name> <command|url>`, `/mcp remove <name>`, `/mcp resources [server]`.

**Transports** (`config.py::resolve_transport`, fail-closed):
- `command` gives stdio. `url` gives **Streamable HTTP by default**. Add `"type": "sse"` for legacy SSE.
- A bare `url` used to mean SSE (a breaking change). Tools register as `mcp_{server}_{tool}`.

**Threading:**
- `McpClientManager` runs one loop on its own thread (`agentao-mcp-loop`). Sync callers submit to it with `run_coroutine_threadsafe` (#241).
- A call to the manager *from* that thread raises. Each `McpClient` holds its connection in an **owner task**. That task opens and closes the transport (#243).
- `disconnect_all(timeout=)` is final and bounded.

**SDK versions:**
- Agentao supports both SDK majors (`mcp>=1.26.0,<3`). `_compat.py` absorbs the 2.0 breaks. It **probes the installed SDK and never parses a version string.** Read it before you touch `client.py` or `tool.py`.
- **Tests must use real `mcp.types` models.** Never use `SimpleNamespace` or `MagicMock` fakes.

**Protocol era** (`McpClient._negotiate()`):
- Send `initialize` first. Escalate to `server/discover` only on a protocol rejection (`-32022` or `-32601`).
- **This is deliberately the reverse of upstream `mode='auto'`** (`docs/design/mcp-streamable-http.md` §5.8.1).
- Exception: a server with `"skills": true` starts with `server/discover`. Tripwire test: `test_a_dual_era_server_is_left_on_the_handshake_era`.
- `McpClient.protocol_version` is a ceiling. Gate on `>=`.

**OAuth** (`docs/design/mcp-oauth.md`):
- Every URL server gets `StoredTokenAuth`, except stdio, `"oauth": false`, or a server with its own `Authorization` header.
- Only `/mcp login` or `agentao mcp login` opens a browser. `agentao run` and ACP never do.
- Records are in `~/.agentao/mcp-oauth/`. The key is the canonical URL plus the optional `oauth.profile`.
- **With no profile, the key is the URL alone. Do not "simplify" the key.**
- A server that ACP supplies is always `"oauth": false`.

**Resources** (`docs/design/mcp-resources.md`):
- Three read-only tools are in `mcp/resource_tools.py`, not `tools/`, to prevent an import cycle. They register only when a server declares `resources`.
- Each call checks **config, then reconnect, then live capability**, in that order.
- The tool saves a binary under its own bound `working_directory`. There is no fallback to the process cwd.
- Their names are in `MCP_RESOURCE_TOOL_NAMES`, outside `BUILTIN_TOOL_NAMES`. `disable_tools` and `enabled_tools` accept them.

**Skills** (`docs/design/mcp-skills.md`). They need mcp 2.x and are opt-in with `"skills": true`. Only `True` counts.
- **State:**
  - Each session has one `McpSkills`, on `SkillManager.mcp_skills`. `child_view()` **shares it by reference**.
  - Gates read the held-entry map, never the active set.
  - `/clear` starts a new conversation generation. It does not wipe the map.
- **Gates:** `ToolCallPlanner.mcp_skill_gate`. The planner calls it **after** the read-only and engine DENY paths.
  - These become ASK with reason `mcp-skill: <note>`: an unapproved skill, `run_shell_command`, a tool with `spawns_shell_capable_agent is True`, and a cross-origin `read_mcp_resource`.
  - A gate that raises asks. A standing grant never answers a gated confirmation.
  - The activations of a batch count as held while the batch plans and confirms (`begin_batch` / `end_batch`).
- **Provenance:**
  - Recognise skill content by tool provenance and by where the result starts (`skills/provenance.py::is_skill_result`). Never recognise it by a tag anywhere in the text.
  - An origin marker counts only as a whole line, and never in an assistant message.
  - It counts only on `agent_*` / `check_background_agent` results or on a restore placeholder (`provenance.marker_origins`).
  - Strip the markers from text that you hand on (`provenance.strip_markers`), then re-add the real origins.
  - **New code that moves messages in a live session (a compaction kind, a truncation) must carry the marker.**
  - Session files back up the origins in `mcp_skill_origins`. A restore activates no MCP skill again (`embedding/sessions.py::withhold_mcp_skill_content`).
- **Content limits:**
  - The formatter never spills skill content to `.agentao/tool-outputs/`. `read_skill_file` (`mcp/skill_tools.py`, in `MCP_SKILL_TOOL_NAMES`) pages it instead, with `offset` / `limit` (at most 30,000 chars).
  - That is not "never on disk". Replay and `agentao.log` keep skill content verbatim.
  - The runtime refuses (does not truncate) a `SKILL.md` over `MAX_SKILL_MD_BYTES` (100,000). Local and plugin skills cannot use the `mcp:` name prefix.
- Test server: `tests/support/skills_mcp_server.py`.

### LLM wire protocols

Design: `docs/design/llm-api-adapters.md`.

`LLMClient` (`llm/client.py`) is a retry and logging shell over one adapter. `api_format` (`{PROVIDER}_API_FORMAT`) selects it: `openai-completions` (default, `_openai_completions.py`), `anthropic-messages` (`_anthropic_messages.py`), `openai-responses` (`_openai_responses.py`).

- **`api_format` is not the provider.** Never infer it from a URL, a provider name or a model name. An unimplemented format fails closed.
- After construction, only a provider switch changes the format (`reconfigure(api_format=)`). A wire change is a switch, as `runtime/model.py::set_provider` defines it.
- **History never changes shape.** `agent.messages` stays OpenAI dicts on every wire. Adapters translate an outbound copy and build responses through `_StreamAccumulator` (`_stream_response.py`). A new wire adds an adapter, never a new message model.
- **The Chat Completions adapter stays byte-identical** to a capture made before the extraction (`tests/test_llm_api_extraction_noop.py`, golden file in `tests/data/`).
- **Never regenerate that golden file from the current build.** `LLMClient.client` and its two latches stay on `LLMClient`.

**Signed thinking:**
- Anthropic blocks ride `anthropic_thinking_blocks`. Responses reasoning rides `openai_reasoning_items`.
- The runtime attaches them at the two sites that record the model's own output (`chat_loop/_serialize.py::_attach_thinking_blocks`).
- **A new carrier key goes in `WIRE_CARRIER_KEYS` and nowhere else.** That tuple drives both the recording and the purge on a switch.

**The Responses wire:**
- It is stateless (`store: false`, no `previous_response_id`). History stores a function call's id as `call_id|fc_…` (`llm/_tool_ids.py`). Split on the last `|`, and only when the tail starts with `fc_`.
- `_with_wire_tool_ids` rewrites composite ids for Chat Completions, which allows 64 characters.
- **When there are no composite ids, `_with_wire_tool_ids` returns the same list.** This keeps the golden file true.
- Send a call's `fc_` item id back only with the reasoning it came from.
- The `openai` SDK *yields* `error` and `response.failed`. The adapter must raise them.

**SDK facts:**
- `openai` has no upper pin. A fresh install gets 3.x, but `uv.lock` has 2.x. To check 3.x, run `uv run --with 'openai==3.16.2' python -m pytest tests/ -n logical`.
- Anthropic SDK facts (measured): it has no `temperature` parameter. It refuses a non-streaming request above about 21k `max_tokens`, so `chat()` consumes a stream. An in-stream `error` arrives as `APIStatusError(status_code=200)`.
- **Ask the Models API in `prepare()`**, which runs before `_build_request_kwargs`. Never ask it at construction or in `build_request`.
- **`usage.prompt_tokens` is the whole prompt.** On Anthropic it is `input_tokens + cache_creation + cache_read`. Do not copy this mapping to other adapters.
- **Test a wire adapter against the real SDK.** Replace only the socket (`tests/support/anthropic_wire.py`, `tests/support/openai_responses_wire.py`). Keep fixtures on `stream_of`.

### Logging

`agentao.log` records every LLM request and response, tool call, tool result and token usage, without truncation.
- The logger is in `agentao/llm/client.py`. Read it first when you debug tool execution or LLM behaviour.
- On `anthropic-messages`, the log has the canonical OpenAI-shaped list, not the translated body.
- **The log is redacted.** `_RedactingFormatter` rewrites credential-shaped strings with the patterns in `security/secret_scan.py`. It is a `Formatter`, not a `Filter`, so the redaction never leaks into an embedding host's handlers. To get raw bytes, bypass this formatter (the only place to do so).

### CLI slash commands

`agentao/cli/help_text.py` has the authoritative list (`/help`). These commands change agent behaviour:

- `/mode`, `/plan`, `/plan implement`, `/plan show`: see Permission modes.
- `/goal <objective> [--for 30m] [--turns 10] [--unbounded]`, with `show|budget|pause|resume|edit|clear`. The host owns the loop (`cli/input_loop.py::run_goal_continuation`). State: `.agentao/goal.json`. The CLI injects `update_goal` with `add_tool`. See Common gotchas.
- `/clear`: saves the session, then clears the conversation and **all memories** with `MemoryManager.wipe_all()`. Only the CLI wording is in `cli/_utils.py::wipe_all_memories`.
- `/model`, `/provider`, `/temperature`.
- `/thinking [minimal|low|medium|high|off]` (`cli/commands/provider.py::handle_thinking_command`). Per wire:
  - `openai-completions` sets `reasoning_effort` in `extra_body`.
  - `anthropic-messages` sets `output_config.effort`. The levels come from the Models API's `capabilities.effort`, else `low|medium|high|xhigh|max`. The command refuses a level that is not in the list.
  - `openai-responses` sets `reasoning.effort`. It keeps the host's other `reasoning` keys and drops a carried-in `reasoning_effort`.
  - There is no auto-recovery. A model that rejects the setting fails until you set `off` (`docs/design/host-llm-extra-params.md`).

## Adding new components

See [docs/guides/adding-components.md](docs/guides/adding-components.md). Tools register in `tooling/registry.py::register_builtin_tools()`, not in `agent.py`.

**A user-visible behaviour change or a new public API is not done until:**
- `CHANGELOG.md` `[Unreleased]` has an entry.
- Every doc that names the old behaviour is updated in both language twins.

## Common gotchas

- **A capability probe must check the answer, not the attribute.**
  - `hasattr` and `getattr(obj, name, None)` fail open at duck-typed seams. Examples: a bare-`def` `copies_to_subagents` is truthy. `wipe_all_memories` once trusted any object with a `wipe_all`.
  - Type-check the result and fail closed. `_REQUIRED_AGENT_ATTRS` in `cli/app.py` asserts only that an attribute exists.
- **Docs come in en/zh twins** (`.zh.md`, `README.zh.md`, `developer-guide/en` + `/zh`). Update both. The stale twin is not always zh. Some parts of `docs/guides/` are only in Chinese.
- **Three memory tests that pass without testing anything:**
  1. The parent's store must be at `wd/.agentao/memory.db`, the CLI's layout.
  2. A read from a `:memory:` store after `close()` sees a fresh empty schema.
  3. Inject a swallowed failure *below* the layer that swallows it. Use the store's `clear_session_summaries`, not `MemoryManager.clear_all_session_summaries`.
- **Old names that older docs still use:**
  - `cli.py` is now `agentao/cli/`.
  - `agentao.harness` is now `agentao.host`. The word "harness" stays for the concept.
  - `agentao.session` is now `agentao.embedding.sessions`. `project_root` is required, and `None` is refused.
  - `allow_all_tools` is now `/mode full-access`.
  - `agentao -p` is a shim. Write new automation for `agentao run`.
- **`Agentao(...)` has no callback kwargs and only five positional parameters** (0.5.0). A legacy callback raises `TypeError`. Migrate with `agentao.embedding.compat.build_compat_transport` (`docs/migration/0.4.x-to-0.5.0.md`). **Put new `__init__` parameters after the `*`.**
- **Do not intuition-audit the architecture.** Grep before you claim a gap. Subpackage `__init__.py` docstrings document intentional shims and rename trails.
- **`/goal --turns` is not `max_iterations`.** `--turns` caps the outer `chat()` calls. `max_iterations` caps the inner tool loop. The host owns the goal loop, not the plugin `Stop` / `force_continue` path (`docs/design/codex-goal-mechanism-review.md` §11).
- **Never move `arun` onto the default executor of the loop. Do not use `asyncio.to_thread` from an async tool.**
  - Turns run on `_get_arun_pool()`. HTML parsing runs on `web.py::_get_cpu_pool()`.
  - Keep the default executor free. `loop.getaddrinfo` needs it, and every `httpx.AsyncClient` connect to a hostname calls `loop.getaddrinfo`.
  - The reasoning is in the docstring of `_get_arun_pool`. `tests/test_web_fetch_async_tool.py` pins it.
- **`agentao/compaction/__init__.py` must not re-export `coordinator`.** `types.py` must import only the standard library. **Review holds this rule, not a test.** Import-layering rule 5 does not reach this file.
- **Unicode tag stripping is structural, not a range filter. Do not simplify it** (`security/unicode_tags.py`. The docstring has the reasoning).
  - It applies at four named boundaries:
    1. tool results (`tool_result_formatter.py::_format_one`, *after* the replay emit).
    2. model output (`sanitize.py::sanitize_text_field`).
    3. server text in `acp_client/render.py`, through `security/terminal_text.py::sanitize_terminal_text`.
    4. model text in `cli/transport.py::_display`, through the same function.
  - At a Rich boundary, also apply `rich.markup.escape`. `[black on black]` hides text without a control byte.
  - `terminal_text.py` stays a leaf and does not import `rich` (`tests/test_import_layering.py` rule 3).
  - The strip is not ambient. Skill and MCP descriptions in the system prompt do not go through it.
  - RGI emoji tag sequences survive (`U+1F3F4` + at most 5 tag chars + `U+E007F`). `_MAX_TAG_SEQUENCES` caps their number. Both bounds are load-bearing.
  - `strip_unicode_tags` must return **the same object** when it drops nothing.
  - **`tool_calls[*].id` and `function.arguments` are exempt on both sanitize paths** (`_normalize_one`, `sanitize_assistant_message`). The two paths must agree.
- **A model or provider switch purges thinking artifacts** (`runtime/model.py::purge_thinking_artifacts`).
  - It drops `reasoning_content` and `thought_signature` at the `tool_calls[*]` entry and at its `function`, and the wire carrier keys.
  - Callers: `set_model` / `set_provider` (when the model or the endpoint changes), and both history-restore sites (`/resume`, `acp/session_load.py`). It is unconditional on purpose.
- **Cancellation budgets must fit in `tool_executor._ASYNC_CANCEL_ACK_TIMEOUT_S` (5s).** The parse drain in `web.py` (3s) and the cancelled-path teardown (2s each) are below it, and two tests pin the relation. The normal browser close keeps 10s.
- **A thread hand-off cancels the awaiter, never the worker.** Copy `web.py::_in_worker`:
  1. Call `submit()` directly.
  2. Cancel the future if it has not started.
  3. Else wait for it under a bounded budget, and log if you give up.
- **For batch commands, use `capabilities/process.py::run_captured()`, not `subprocess.run`** (PRs #73–#75).
  - `subprocess.run(timeout=)` kills only the direct child. A grandchild that holds the pipe then hangs the turn.
  - `run_captured` runs the child in its own process group, sets stdin to `DEVNULL` unless you give `input=`, and decodes with `errors="replace"`.
  - On timeout, it kills the whole tree (`kill_process_tree`). Use `killpg(pid)`. Never use `getpgid`.
  - It defaults `env=` to `build_child_env()`, which strips the provider credentials. To opt out, give an explicit `env=` or set `AGENTAO_SCRUB_CHILD_ENV=0`.
  - `LocalShellExecutor.run` keeps its own streaming loop. It shares `kill_process_tree` and the scrubbed env.
- **`run_loop` (`cli/input_loop.py`) imports `sys` locally, further down.** Thus `sys.exit()` near its top raises `UnboundLocalError`. Use `raise SystemExit(...)` there (#507).
