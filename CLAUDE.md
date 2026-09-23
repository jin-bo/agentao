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

Core deps live in `[project.dependencies]`; the heavyweight UI / fetch / tokenization deps are opt-in extras. A bare `pip install agentao` gets a library-only install; `pip install 'agentao[cli]'` is the smallest interactive CLI. Both wire SDKs — `openai` and `anthropic` — are core, imported lazily. (The `[pdf]` / `[excel]` / `[image]` / `[crypto]` / `[google]` extras were removed as dead weight — zero in-tree consumers; see `docs/design/optimization-opportunities-review.md` T1.1.)

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
uv run python -m pytest tests/ -n logical  # Same, in parallel (pytest-xdist) — how CI's Windows job runs it
uv run python -m pytest -m slow      # Clean-install smoke tests
uv run ruff check .                  # Lint gate — required CI check
```

The `slow` marker is excluded by default (`pyproject.toml :: tool.pytest.ini_options.addopts = "--tb=short -m 'not slow'"`). CI runs it in the **build** job on Python 3.12, right after `uv build` — the only job with a `dist/*.whl` to install — so `-m slow` is a required check too, and it needs `uv build` locally first.

**`ruff check .` is a required CI check, so a green pytest run is not enough before pushing.** The gate is deliberately narrow — defect rules only (`E9`, `F401`, `F402`, `F405`, `F811`, `F821`), no style — and the rules *and* scope both live in `pyproject.toml`, so the command above is character-for-character what CI runs. Two non-obvious parts: `F405` is selected because `F821` is silently inert in a star-import module without it (one is left since 0.5.0 removed `agentao/harness/`: `agentao/tool_runner.py`), and `F401` is exempted for `agentao/` because a name re-exported for downstream embedders is indistinguishable from dead code to a single-file linter. Suppress with a reason (`# noqa: F401 — pytest fixture injection`), never bare. See [docs/design/lint-gate.md](docs/design/lint-gate.md).

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
| `agentao/llm/` | `LLMClient` — the retry / logging shell — over a wire adapter: `_openai_completions.py` (Chat Completions, the default), `_anthropic_messages.py` (Anthropic Messages) or `_openai_responses.py` (OpenAI Responses), selected by `_api_format.py`. `_stream_response.py` is the one response duck-type all three build. See *LLM wire protocols*. |
| `agentao/compaction/` | Compaction orchestration. `types.py` is the contract (`CompactionOutcome`, `CompactionDecisionContext`, `CompactionDecision`, `CompactionController`, and the `trigger`/`kind`/`reason` vocabulary) and **imports nothing but the standard library**; `coordinator.py` holds `CompactionCoordinator`. `__init__.py` must never re-export `coordinator` — see Common gotchas. |
| `agentao/host/` | **Public host contract.** `HostEvent`, `ToolLifecycleEvent`, `SubagentLifecycleEvent`, `PermissionDecisionEvent`, `EventStream`, `ActivePermissions`. Stability boundary for embedded hosts. |
| `agentao/embedding/` | Host-side construction: `build_from_environment()` (env / dotenv / `.agentao/*.json` reads routed through explicit kwargs), `permission_loader`, `sessions`, `plugins/` (manifest loader, validators, MCP merge, resolvers). |
| `agentao/plugins/` | Plugin **runtime path** only — models, hooks, skill/agent validators. Loader lives in `embedding/plugins/`. `plugins/hooks/` is the hook subsystem: `_parser` (shape detection + contract), `_profile` (the capability tables, data), `_resolve` (five stdout states + the precedence function), `_dispatcher` (execute, partition, merge), plus `_budget` / `_paths` / `_diagnostics` / `_profile_payload`. |
| `agentao/permissions.py` + `permissions_hardline/` | `PermissionEngine` + the command floor. `_scanner.py` holds `generic_floor()` (the dialect-independent patterns, heredoc/context/decoder aware) and `hardline_check()`, the entry that runs it on **every** dialect and adds the PowerShell table on top. `_windows.py` is that table plus PowerShell alias resolution; `_powershell.py` is tree-sitter lowering. |
| `agentao/cli/` | Interactive CLI **package** (was `cli.py` before 0.4.x). `app.py` (`AgentaoCLI`), `entrypoints.py` (argparse + `main`), `run.py` (`agentao run`), `commands/` (per-slash-command handlers), `subcommands.py`, `diagnostics_cli.py`. |
| `agentao/prompts/`, `agentao/agents/`, `agentao/plan/`, `agentao/capabilities/`, `agentao/tooling/`, `agentao/security/`, `agentao/context_manager.py` | Supporting modules — prompt assembly (`SystemPromptBuilder`), sub-agent runners, plan-mode state, capability declarations (incl. `capabilities/process.py::run_captured` — the shared hardened subprocess runner; see Common gotchas), tool registration (`tooling/registry.py::register_builtin_tools` + agent/MCP tool wiring), security utilities (`security/secret_scan.py` — the shared credential-pattern scanner behind `agentao.log`, `.agentao/tool-outputs/`, `MemoryGuard`, and replay; plus `path_policy.py` / `url_policy.py` — the latter exports **paired sync/async** surfaces, `validate_outbound_url`/`guarded_get` and `validate_outbound_url_async`/`guarded_get_async`; the async validator delegates to the sync one on a bounded daemon thread rather than reimplementing the policy, so there is exactly one copy of it; and `unicode_tags.py` — invisible-character smuggling defense, see below), context-window compaction. Session save/load is `agentao/embedding/sessions.py`. |

### Tool system

All tools inherit from `Tool` (sync) or `AsyncToolBase` (async) in `agentao/tools/base.py`. Both are registered through the same `ToolRegistry` which converts them to OpenAI function-calling format.

`AsyncToolBase` dispatches through `runtime_loop` with a `CancellationToken`; cleanup-ack uses `_bridged()` `finally` + `threading.Event` so the runtime can cancel mid-tool. `RegistrableTool = Tool | AsyncToolBase`.

**`web_fetch` is the only built-in `AsyncToolBase`** (0.4.18) — everything else is a sync `Tool`. It had to be: it drives Playwright's async API, and a sync `execute()` reaching that from inside a caller's loop can only do it by blocking the loop. `WebSearchTool` deliberately stays sync (it never drove its own loop). `WebFetchTool.execute()` survives as a sync convenience wrapper for non-async embedders — it blocks a running loop, by construction, and a test asserts that so the cost stays documented. See Common gotchas for the thread-pool rules that come with it.

**Sub-agents execute from one registry** (`agents/tools/_wrapper.py::_narrow_tools`, #238). It is the parent's live registry at spawn, narrowed to the definition's `tools:` list:
- **Built-ins** are the child's own instances, bound to the parent's `filesystem` / `shell`. `save_memory` is the one exception to "its own": at spawn its write target is rebound to the parent's `MemoryManager`, because a *long-term* memory has to land where the parent's memories land — user store and host-injected manager included (#260). Only that attribute moves; the child keeps its own manager for its session id, the session summaries and crystallized proposals its own compaction writes, and the stores its `close()` releases, so sharing the manager outright would mix the child's session into the parent's and close the parent's stores when the sub-task ended. That manager is built on a **transient** store (`_child_memory_manager`, #234) — left to the default it opened `working_directory/.agentao/memory.db`, the parent's own project file, so both of those writes landed in the parent's database: the summary surfaced through the cross-session tail as an earlier session, and the proposals sat in a review queue no bulk remedy reaches (`/memory review reject <id>`, one at a time). Transient cuts the read side with them, and that is the decision: **a sub-agent reads no memories at all** — no stable block, no recall — and is briefed by its `parent_context` instead, which carries the parent's recent *messages* and no memories, while still being able to write one it cannot read back. Restoring reads means a `MemoryManager` child view sharing the parent's stores, whose `close()` must then not close them. A rebind that cannot be made leaves `save_memory` out **by name**, like an undeclared host tool: absent, the model is told the tool does not exist, which is true; present and unrebound it answers "Saved memory: x" for a write nothing will read.
- **`mcp_*`** are the parent's instances, over the parent's connections. The child is built with an empty `InMemoryMCPRegistry()` and connects nothing (#239).
- **Skills** are the parent's catalogue, via `SkillManager.child_view()` read at spawn (#254) — derived, never re-scanned: a plugin's skills are registered onto the parent's manager in memory (some with no file at all) and a host-injected manager suppressed the scan, so a re-scan would show a sub-agent a different catalogue than the parent advertises, and would re-run the unlocked bundled-skill `copytree` per spawn. `active_skills` starts empty and stays the child's — the parent's activations are not inherited, and a background child's do not reach the parent's prompt.
- **Host tools** reach a sub-agent **only by declaring `copies_to_subagents`** on the tool object (default `False`), and then as one `copy.copy` made at spawn and registered with origin `host` (SUB-03 / PR-b). An undeclared tool is left out **by name** — the built-in it replaced does not reappear underneath it — and so is one whose copy or whose declaration *raises*, one whose declaration is a bound method (a missing `@property` is truthy whatever it returns, the only fail-*open* misreading), and one whose `__copy__` answers with `self` or with a differently-named tool; each logs a warning. The copy is per spawn, not per call, so a tool keeps state across one sub-task; it exists because the executor rebinds `output_callback` on the instance per call under a lock scoped to **one batch**, and a sub-agent's batch holds a different one. The declaration does not outrank the definition's `tools:` list.
- **Left out:** agent tools and plan tools. Which is which comes from the **origin recorded at registration** (`ToolRegistry.register(origin=)`, default `host`, read back with `.origin(name)`), never from the class: a host's `WebSearchTool(backend=...)` under `web_search` is a host tool, and is never swapped back for the built-in (#256). A new in-repo registration site must pass its origin, or its tools read as host tools and need a declaration to reach a sub-agent.

The contents are swapped **in place**, because the runner and planner hold the registry object: reassigning `sub_agent.tools` changes only what the model sees, which was the bug. The permission engine had the same trap: the planner holds its own reference, so a sub-agent's engine goes through `ToolRunner.set_permission_engine`. That engine is `parent_engine.snapshot()`, never a re-read of `permissions.json`: a host's `rules=` and an `agentao run` spec's rules exist only on the engine. A **background** sub-agent gets its own `SdkTransport(confirm_tool=lambda *_: False)`, so a call that asks is refused while explicit `ALLOW` rules and modes still apply. `NullTransport`'s approve-everything stays as the headless-host default. **Tool-name repair resolves by spelling, never by similarity** (`runtime/name_repair.py`, #261): it normalises padding, case, separators, camelCase and a trailing `Tool` suffix, and answers only when a normalisation reproduces an offered name **exactly** — on either side, since an MCP server's `getFileContents` is reachable from `get_file_contents` only when the *offered* names are normalised too (fail-closed when two of them share a spelling). There is no `difflib` pass, so a name the runtime withheld has no candidate to be repaired into — `read_file` never becomes `write_file`, and a host tool a sub-agent was denied never becomes one it was granted. The one residual reading is the suffix strip itself: `deploy_tool` resolves to an offered `deploy`, so a deployment that names two *different* tools `x` and `x_tool` cannot rely on the withheld one staying unreachable. Candidate order is by **fidelity**, not alphabetical (`PatchTool` → `patch_tool`, not `patch`, when both are registered); ties inside a tier are `sorted()` so nothing varies with `PYTHONHASHSEED`. Two earlier rounds guarded the similarity pass instead (refuse names spelling a known-but-unoffered tool; then rank the pool over the known names too) and each closed only the inputs in front of it. An unresolved name returns the tool-not-found error, which already lists the available tools, and the model re-issues the call — one extra turn, unless the model repeats the identical misspelling, which trips the raw-name-keyed doom-loop counter and halts the turn. Peers do the same: codex answers `unsupported call`, gemini-cli errors with a `did you mean` it never dispatches.

**Registration**: `agent.py::_register_tools()` is a thin delegation — the real wiring lives in `agentao/tooling/registry.py::register_builtin_tools()` (`agent_tools.py` / `mcp_tools.py` cover sub-agent and MCP tools). Note `agentao/tools/goal.py` is *not* registered by default (the CLI injects it via `add_tool` when a `/goal` is active).

**Confirmation / permissions**: `PermissionEngine` evaluates rules from `.agentao/permissions.json` (project) + `<home>/.agentao/permissions.json` (user). Precedence in `runtime/tool_planning.py::_decide` is three-tier, in this order: (1) the **read-only mode preset** short-circuits to `DENY` for any non-read-only tool *before* the engine is consulted at all (`:606-617`, reason `mode-preset:read-only`) — a permissions.json `allow` cannot override it; (2) otherwise the engine runs for **every** tool call, not only tools with `requires_confirmation=True`, and an `ALLOW`/`DENY` is final (`:626-629`); (3) only engine `ASK`-or-no-match falls through to the tool's own `requires_confirmation` (`:634-641`). Tier 2 matters for MCP: a rule can match `mcp_*` by name and govern tools whose `requires_confirmation` is `False` (which is what a `trust: true` server's tools return unless the server set `destructiveHint`) — so that attribute is a *fallback*, not the trigger. The engine itself does **no file I/O** — `agentao/embedding/permission_loader.py::load_permission_rules()` reads and passes `(rules, sources)` in. Default presets auto-allow common docs domains (`.github.com`, `.docs.python.org`, …) and auto-deny SSRF targets (`localhost`, `127.0.0.1`, `169.254.169.254`, …).

### Permission modes (replaces the old `allow_all_tools` flag)

`/mode read-only | workspace-write | full-access` switches the runtime's permission posture. `plan` is the fourth posture — entered via `/plan` interactively (not `/mode plan`), or `--permission-mode plan` on `agentao run`:

- `read-only` — blocks all write and shell tools. `activate_skill` and `todo_write` are allowed (session state only); `save_memory` is not. The mode has **two switches**, the runner's flag and the engine's mode (whose `read-only` preset is an empty list), and `ToolRunner.readonly_active` honours either. ACP `session/set_mode`, a host's `set_mode` and a sub-agent's engine snapshot set only the engine's mode; before 0.5.4 that meant writes and shell got ASK.
- `workspace-write` — allows file writes and safe shell; asks for web (default).
- `full-access` — allows all tools without prompting.
- `plan` — LLM plans, does not execute; entered via `/plan`.

State is on `AgentaoCLI` (`agentao/cli/app.py`) and projected into prompts.

**A switch goes through one function**, `runtime/permission_mode.py::apply_permission_mode` — reached as `Agentao.set_permission_mode(mode, cause=)` from a host, and used directly by `/mode` (`cli/app.py::_apply_mode`, `cause="cli"`), ACP `session/set_mode` (`"acp"`), `agentao run` (`"run"`) and the CLI's two non-`/mode` switches: answering "2" at the confirmation prompt (`cli/transport.py`, `"cli-allow-all"`) and `/plan implement` leaving read-only (`cli/input_loop.py`, `"cli-plan-implement"`). Those two stay off `_apply_mode` on purpose — it resets `allow_all_tools` and persists the mode, and both grants are session-only — but not off the helper. It moves **both** switches and emits `READONLY_MODE_CHANGED` (the flag, only on a real flip) then `PERMISSION_MODE_CHANGED` (`previous` / `current` / `cause`, only when the preset moved). Same reason `runtime/model.py` owns `MODEL_CHANGED`: `PermissionEngine` holds no transport and does no I/O, so a bare `engine.set_mode` records nothing — which is what left an ACP replay showing read-only denials with no transition. A new entry path calls the helper; it does not re-implement the pair. ACP's `current_mode_update` is the separate **client**-facing notification and is not interchangeable: it fires for a non-preset UI `modeId` that changes no posture.

### System prompt composition

**The model's instructions arrive in two messages, and the second one is not in history.** `agent.py::_build_system_prompt()` builds the system message — the stable prefix, byte-identical across the turns of a session, ending at `<memory-stable>` (`builder.py::_build_sections()` is the authoritative order). `agent.py::_build_volatile_tail()` builds the volatile half — active-skill bodies, todos, `<memory-context>`, plan prompt (`builder.py::_build_volatile_sections()`) — wrapped as one `<system-reminder>` and appended to the **outgoing request** as a trailing `user` message. One non-obvious rule inside the prefix: available agents are suppressed in plan mode (delegation contradicts research-only intent).

The split landed in 0.4.26 as stage 0a of `docs/design/llm-api-adapters.md` §2.3, and three of its invariants are easy to break:

- **`messages_with_system` in `runtime/chat_loop/` is the persistent prefix, never the request.** All seven assembly sites build `[system] + agent.messages`; the tail is appended in exactly one place, `_call_llm_with_overflow_recovery`'s `_send`, which is the only caller of `agent._llm_call`. Putting the tail into `messages_with_system` hands it to compaction and to the token anchor as if it were history.
- **The tail is request-only; the other two `<system-reminder>` patterns are persisted.** The date/time and background notifications are appended to `agent.messages` on purpose. A persisted tail would pile one todos snapshot into the transcript per turn.
- **The Tier-1 token anchor is recorded against the persistent prefix**, i.e. `record_api_usage(prompt_tokens, len(persistent), tail_tokens=est(T))`. Anchoring the request instead makes the next slice skip the first new history message *and* re-charge a tail — a whole-tail-sized over-estimate every turn, in the direction that triggers compaction early. `tests/test_volatile_tail_request.py` pins it over 12 turns with the tail size deliberately varied.

Because the tail is rebuilt per *request*, a `todo_write` in one tool iteration is visible to the next — before 0a it waited for a system-prompt rebuild.

**Explicit prompt-cache breakpoints are opt-in** (stage 0b): `LLM_PROMPT_CACHE=anthropic` / `prompt_cache=` puts at most 3 `cache_control` markers on a request (system message, last tool definition, end of stable history), reserving the 4th slot for the endpoint's automatic caching. Marking happens in the wire adapter's `build_request` (reached through `llm/client.py::_build_request_kwargs`) — *below* replay — and is **copy-on-mark** (`llm/_cache_control.py`): the request shares its dicts with `agent.messages`, so an in-place marker would enter history, the session file, replay and compaction, and accumulate one breakpoint per turn. Off by default because SDK pass-through is verified and endpoint acceptance is not; never inferred from a base URL or model name.

**The date/time is in neither of the two.** It is injected per-turn as a `<system-reminder>` prepended to the *user message* (`runtime/chat_loop/_runner.py::run`, `Current Date/Time: YYYY-MM-DD HH:MM:SS (Day)`) — keeping it out of the cached prefix is the whole point. `tests/test_date_in_prompt.py` asserts both halves.

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

`arun()` is the async path; the sync `chat()` wraps it. AsyncTools dispatch on `runtime_loop` so cancellation works inside the LLM-driven turn.

### Compaction

Five entry points detect their own condition and hand off to one
`CompactionCoordinator` (`agentao/compaction/coordinator.py`), reached as
`agent.compaction_coordinator`:

| # | Entry point | `kind` | `reason` |
|---|---|---|---|
| 1 | Microcompaction (`runtime/chat_loop/_compaction.py`) | `microcompact` | `microcompact_threshold` |
| 2 | Threshold full (same file) | `full` | `compression_threshold` |
| 3 | API overflow, rung 1 (`runtime/chat_loop/_runner.py`) | `full` | `api_overflow` |
| 4 | API overflow, rung 2 (same file) | `minimal_history` | `api_overflow_after_compression` |
| 5 | Manual `/compact` (`cli/commands/compact.py`) | `full` | `manual_cli` |

The coordinator owns *whether to run, whose summary to take, and what to
emit*: the circuit-breaker gate, the `PreCompact` hook dispatch, history
assignment, and both events. `ContextManager` owns every content transform —
and **the dependency points one way only: `ContextManager` neither imports
nor holds a coordinator**. That is why the shared types live in the neutral
`types.py`.

`compress_messages` is split at the summarization call:
`prepare_compaction` (pure computation, **no SQLite write, no touch of
`agent.messages`**) → decide → summarize → `commit_compaction` (the two
SQLite writes — `crystallize_user_messages` over the raw user messages, then
`save_session_summary`, two different tables — and the new list).
`_run_compaction` strings those together and owns all three
failure-counting points — they cannot live in commit, which never runs when
summarization returns nothing.

**The circuit breaker is a recoverable state machine, and its state stays in
`ContextManager`.** Three consecutive *automatic* failures pause the threshold
tier; manual `/compact` and `api_overflow` run as half-open **probes** through
an open breaker (`_PROBE_REASONS` in `coordinator.py`), and a successful probe
closes it. `clear_history()` / `/clear` closes it too. The coordinator only
applies policy — it never touches the counter. `Agentao.compact()` is the
public entry.

**`skipped` emits no event.** Three of its four cases re-trigger on every loop
iteration; one event each would be a storm. `CONTEXT_COMPRESSED` fires only on
`success`; `COMPACTION_SETTLED` fires for `success | cancelled | failed`.

**The control plane has two layers and one merge rule.** Command hooks first
(`dispatch_pre_compact_decision`, wire key `hookSpecificOutput.compactionDecision`,
first-cancel-wins), then — only if they all allowed — `compaction_controller=`,
a keyword-only constructor argument. A cancel in either layer is a cancel;
`provide_summary` can only come from the controller. **Everything that is not
an explicit cancel means allow, including a raise**: two entry points are the
overflow recovery ladder, so a control-plane error must never be able to end
the turn it exists to save. A cancelled threshold compaction enters a
coordinator-owned latch keyed `(kind, reason)`, cleared per turn in
`runtime/turn.py`; `manual_cli` and both overflow reasons never enter it. A
cancelled **overflow** returns the provider's context-length error rather than
falling through to the last rung.

**The last rung is not a plain tail slice, and must not be simplified back
into one.** `apply_minimal_history` keeps the newest `keep_tail` messages, but
`_minimal_history_start` first repairs the boundary so the window cannot *open
on* a `role: "tool"` message — the same rule `_find_split_index` enforces for
the summarizing path, and it bites harder here: overflow is normally detected
on the request *after* a batch of results was appended, so a two-message tail
is routinely two results whose `tool_calls` sit one message back, and this rung
gets the turn's last attempt. Repair order is **drop the leading results, and
only if that empties a window that had content, step back to the assistant that
made the calls** (the window is a suffix, so admitting the call admits its
results). `prepare_minimal_history` reports the **effective** count, so
`messages_to_keep` on the decision context is the cut the host is being asked
to approve.

**And the rung stands down when there is no cut worth making.** Because the
boundary is repaired, `success` no longer implies history changed:
`minimal_history_would_help` gates the attempt to `skipped` in three cases —
the smallest valid window is the whole history (one assistant message plus a
large batch of its results, the likeliest shape here), it is empty, or it
cannot answer its own calls (a call whose result was never appended; the
repair closes result→call, never call→result). The gate sits next to the
no-target microcompact gate for the same reason. The runner returns the
provider's context-length error on **any** non-success at this rung, not only
on a cancel: nothing shrank, so retrying spends the turn's last attempt on the
request that just failed.

**Two token units, deliberately named apart.** `CONTEXT_COMPRESSED`'s
`pre_est_tokens` / `post_est_tokens` **include** the system prompt;
`COMPACTION_SETTLED`'s `pre_tokens_history` / `post_tokens_history` exclude
it. Never wire one into the other.

**Two windows, and they are not interchangeable** (three inputs since the Models API lookup: `effective = min(configured, observed, reported)`, where `reported` is `llm.model_input_limit`, read live and type-checked; it narrows and never widens, same rule as observed). `max_tokens` is the
host's configured knob and reads back what the host wrote;
`effective_max_tokens` is `min(configured, observed, reported)`, where `observed` is
learned from a provider overflow error via `parse_observed_context_limit`.
**Every internal budget uses effective**; `get_usage_stats()['max_tokens']`
and ACP's `session/set_model` echo keep returning configured. The parse is
provider-asserted and refuses to adopt anything it is not certain of — most
overflow messages carry *two* numbers and picking the request size instead of
the limit is a permanent silent degradation.

`ContextManager.compress_messages()` survives as the legacy wrapper (its
signature is documented and pinned by tests that call it directly). It returns
a bare list and so cannot say *why* nothing changed; it also bypasses the
host control plane and the breaker's probe policy.

**What the summarizer is fed** (`_format_for_summary`): a
`<previous-summary>` section (the carried summary, **out of the newest-first
eviction pool** — it used to be a block inside it, where it is by construction
the oldest and got dropped first), optionally an `<originating-request>`
section (only when that message did not survive the ordinary spend), then the
live transcript. Two invariants: carry ≤ half the summary-input budget, and
carry + live ≤ the whole budget. The transcript's survivors are always a
**contiguous suffix** — a hole hands the summarizer a history that omits a
step without saying where.

`keep_recent_token_ratio` (default `None`) and `image_token_estimator`
(default `None`) are opt-in knobs, both off because the right values are
per-deployment and unmeasured here.

Design: `docs/design/compaction-orchestration-plan.md`.

### Skills

Auto-discovered from `skills/`. Each subdir has `SKILL.md` (YAML frontmatter `name:` / `description:`) and optional `references/*.md`, which are **not** inlined — activation *enumerates* them by absolute path and tells the model to `read_file` what it needs (`skills/manager.py::activate_skill`). That is the whole point: the always-resident cost stays at name + description. The skill manager (`agentao/skills/`) maintains `available_skills` (all) and `active_skills` (this session). Cross-process locking via `filelock` — installs and updates are safe across concurrent CLI processes.

Activate via the `activate_skill` tool or `/skills activate <name>`.

**The available-skills catalogue renders only for an agent that has `activate_skill`** (`prompts/builder.py::_available_skills_block`) — the block's whole text is an instruction to call that tool, and it can be absent three ways: `disable_tools`, an `enabled_tools` allowlist, or a sub-agent definition whose `tools:` list omits it. The *active*-skills block is deliberately not gated: `/skills activate` calls the manager directly, so a skill can be active for an agent that never had the tool.

**The catalogue lists active skills too, and must keep doing so** (0.4.27). It is in the system message, ahead of `<memory-stable>`; while it listed only the *inactive* skills, every activation rewrote `messages[0]` and with it the provider's cached prefix over the whole history. Now it changes only when the *enabled set* does (enable / disable / install / reload) — events that already rewrite `activate_skill`'s `skill_name` enum in the tools block, so they were rebuilding the prefix anyway. What activation changes is the active-skills block, which rides the volatile tail and is how the model learns which entries are already active. pi-mono and gemini-cli list the same way. `tests/test_skills_prompt.py` pins byte-identity across activate → deactivate. The active bodies are now the tail's large item (one skill ≈ 4k tokens per request here); whether they belong in the prefix depends on *when* in a session skills get activated, which is unmeasured.

### Memory system

**Architecture:** SQLite-backed storage managed by `MemoryManager` (`agentao/memory/manager.py`).

| Database | Path | Content |
|---|---|---|
| Project store | `.agentao/memory.db` | Project-scoped persistent memories + session summaries |
| User store | `<home>/.agentao/memory.db` | Cross-project user-scoped persistent memories |

**Four data types:**

1. **Persistent memories** (`MemoryRecord`) — rows in `memories`. Soft-deleted. Scoped `user` / `project`.
2. **Session summaries** (`SessionSummaryRecord`) — rows in `session_summaries`. Written by microcompaction / full LLM summarization. Scoped to `session_id`.
3. **Recall candidates** (`RecallCandidate`) — transient, in-memory, scored at query time by `MemoryRetriever`. Never stored.
4. **Review items** (`MemoryReviewItem`) — rows in `memory_review_queue`. Written by the rule-based crystallizer (`commit_compaction` step 4b, and `/memory crystallize`); `/memory review approve` promotes one into a real memory. **Neither `/clear` nor `/memory clear` reaches this table** — `/memory review reject <id>`, one at a time.

**A hard wipe is one operation with one implementation:** `MemoryManager.wipe_all()` (#235) — `/clear` and `/memory clear` both call it, the CLI re-implements nothing, and an embedded host gets the same `MemoryWipeResult`. **`ok` is the success signal, not the counts**: `clear_all_session_summaries()` answers 0 both for "nothing to delete" and "the delete was swallowed", so only the summaries half is confirmed by reading the store back. The memories half is not, deliberately — a read-back would race a sub-agent's `save_memory` through the parent's manager (#260) and report a good wipe as failed. Two things `ok` does not promise: the review queue (type 4 above), and erasure — the memories half is a soft delete, so `content` stays in the file.

**Prompt injection (per turn, two blocks):**
- `<memory-stable>` — stable persistent memories (budget-limited), **plus the cross-session tail**: up to 3 summaries from *previous* sessions (`manager.py::get_cross_session_tail`, which keeps every `session_id` that is not the reader's own). Only the **current** session's summaries are excluded — they already live in message history as `[Conversation Summary]` blocks. A sub-agent's summaries never enter the tail because its store is transient and its own (#234); before that they did, and read as an earlier session of the parent's.
- `<memory-context>` — top-k recall candidates against current user message.

**Scope downgrade is no longer silent.** A `user`-scope write on a manager with no user store still lands in the project store — that is the bare-construction default, and a library embedder needs the write to go somewhere — but `upsert` now says so: an explicit `scope="user"` **warns** (a request that was not honoured), an inferred one (`user_` prefix, `preference` / `profile` tag) goes to `agentao.log` at debug, since on a project-only manager that is most writes and a warning on each trains the reader to ignore all of them. Neither logs `key` or `value`. (#260)

**Both backings take the `RLock`, for two different reasons.** The transient `:memory:` backing keeps a single shared connection (closing it would discard the database), so it connects `check_same_thread=False` and the lock is what stops two threads inside that one connection's implicit transaction from committing each other's half-written work. The file backing opens a private connection per statement and was never refused a cross-thread one — but Python's sqlite3 opens **no transaction for a `SELECT`**, so `upsert_memory`'s read-then-write is not atomic across two connections: two concurrent saves of the same key both read "no row" and the second `INSERT` fails `uix_memories_scope_key`. Holding the lock across the whole `_connect` scope closes both **within one process**, which is the scope the sub-agent change created; two agentao processes on the same project `memory.db` can still race that read-then-write, and nothing here claims otherwise (skills take a `filelock` for exactly that reason; memory does not). Tool calls already ran on the executor's worker threads when a batch held more than one; a background sub-agent's `save_memory` now reaches the *parent's* store from the sub-agent's thread unconditionally. A transient store that is reconnected after `close()` re-applies the schema and logs a warning — the data is gone with the connection, and answering "no such table" instead is worse.

**Separation of concerns:** the LLM can only write (`save_memory(key, value, tags?)`). Search, delete, clear are CLI-only (`/memory search|tag|delete|clear|user|project|session|status`) and call `MemoryManager` directly — never exposed as LLM tools.

See `docs/guides/memory-management.md`.

### Replay

`ReplayManager` (`agentao/replay/manager.py`) records every turn to `.agentao/replays/*.jsonl` when enabled. Replay state lives **outside** `Agentao` core — Transport emits `TURN_BEGIN` / `TURN_END` events that the manager subscribes to. Configure via `.agentao/settings.json :: replay.{enabled, max_instances}` or `/replay on|off`.

### Plugin hooks

Shell commands run at **eight** points in a turn — `PreToolUse`, `PostToolUse`,
`PostToolUseFailure`, `UserPromptSubmit`, `Stop`, `SessionStart`, `SessionEnd`,
`PreCompact`. Runtime lives in `agentao/plugins/hooks/`; discovery and manifest
resolution live in `embedding/plugins/` (a plugin's `hooks/hooks.json`, or its
manifest's `hooks` key).

**Two contracts, resolved per *file* by shape** (`_parser.py::_detect_entry_shape`,
`::_resolve_contract`). A file copied out of a Claude Code setup carries no
`contract` key to gate on, so the parser reads the entries: `hooks: []` and no
`type` → official; `type` and no array → flat. Four shape values, not three —
an entry with **both** keys is a contradiction and disables the file, one with
**neither** is just a malformed handler and gets a per-rule warning, because
otherwise a single typo disables every other hook in a working `agentao-v1`
file.

| Contract | Status |
|---|---|
| `agentao-v1` | **Frozen.** Every behaviour change is gated on contract, and both halves get a test |
| `claude-code@profile-1` | An **enumerated capability profile**, not "the Claude hook contract" |

**The profile is enumerated, and that is the load-bearing word.** `_profile.py`
is data — which fields each event accepts, which it ignores, what exit 2 means
there. A key the profile does not implement is **ignored with a one-time
diagnostic naming it, never a schema error**: a hook written for a newer Claude
Code must keep working. Claiming a surface obliges you to enumerate it; if you
add a field, add its row.

**Two axes, and they are not the same axis.** *Profile disposition*
(`accept` / `ignore`) is reported once per (rule, field) per session.
*Delivery* (`honored` / `discarded`) is **silent** — a discard means the hook is
upstream-conformant and agentao simply does not route that field on that event,
so a diagnostic there would misreport conformance as a capability gap.

**Precedence is a function, not a field order** (`_resolve.py::resolve`, over
the five `parse_stdout` states `empty | plain | parse_error | schema_invalid |
valid`): **exit 2 → `continue` → the event's own decision**. Exit 2 is three
outcomes, not a boolean — block, feed the model, notify the user — chosen per
event.

**A stop from a tool worker rides home on the result, and is arbitrated in plan
order.** `PostToolUse*` hooks run inside the worker, three frames below anything
that can end a turn, so the verdict travels on
`ToolExecutionResult.hook_stop_reason` → `ToolRunner.last_hook_stop` →
`INCOMPLETE_HOOK_STOP` (`chat_loop/_runner.py:116`). **Plan order, never
completion order** — the model's own tool-call order — or the surfaced reason
varies run to run for the same batch. Reset `last_hook_stop` at the *top* of
`execute()` (`tool_runner.py:167`): a bottom reset wipes what phase 1.5 wrote
and leaks a stale stop across the early returns. Both seams read a **string**,
never a truthy value — a `MagicMock` runner answers any attribute.

**Mixed-contract dispatch partitions, runs, merges once** (`_dispatcher.py::_merge_pre_tool_use`).
A v1 short-circuit ends only the v1 group: the profile's "every matching handler
runs" rule exists for handlers with side effects. The merge is over the event's
lattice (`deny > ask > allow`), and the reason tie-break ranks **inside the
winning class** by declaration order — surfacing an `ask`'s reason for a `deny`
attributes the verdict to the wrong rule.

**`env=build_child_env({...})` is the only correct spelling** for the three path
placeholders (`_paths.py`, `_dispatcher.py:639`). `env={...}` or
`env=os.environ | {...}` silently deletes the provider-credential scrub — see
Common gotchas, `run_captured`.

Two more that are easy to get subtly wrong: the profile matcher is
`re.fullmatch` with `*` **and `""`** special-cased (`_matchers.py`), measured
against a real `claude` 2.1.251 — `ead` does not match `Read`; and the
diagnostic registry is **session**-scoped and keyed by rule *content*
(`_diagnostics.py::rule_key`), never `id(rule)`, which changes on reload — the
dispatcher is built fresh per dispatch (nine sites across six files, two of
them inside pool workers), so dispatcher-scoped state would dedup nothing and
race while doing it.

Config reference: `docs/reference/configuration.md` §11. Design + the ten gate
closures: `docs/design/hooks-claude-contract-conformance-plan.md`. Measured
upstream behaviour: `docs/reference/hooks-probe-2.1.251.md`.

### MCP

External MCP servers via `.agentao/mcp.json` (project) + `<home>/.agentao/mcp.json` (global):

Transports (`mcp/config.py :: resolve_transport`, fail-closed): `command` → stdio, or `url` → **Streamable HTTP by default** (add `"type": "sse"` for the legacy SSE transport; `"type": "http"` is the explicit form). A bare `url` used to mean SSE — this is a **breaking change**. Tools are registered as `mcp_{server}_{tool}`. The MCP SDK is async-only. `McpClientManager` runs one event loop on its own thread (`agentao-mcp-loop`), and sync callers submit to it with `run_coroutine_threadsafe`, so calls from the executor's parallel tool threads run concurrently over one `ClientSession` (#241) — calling the manager *from* that thread raises instead of deadlocking. Each `McpClient` keeps its connection in an **owner task** that opens and closes the transport in one task, because the SDK's anyio cancel scopes must be exited by the task that entered them (#243). `disconnect_all(timeout=)` is final and bounded: new calls raise `McpManagerClosedError`, calls in flight get `timeout` seconds, then are cancelled. The close runs on the loop, which stops itself at the end (its thread then closes it), so an interrupted `disconnect_all` still finishes and a repeat call waits to the *first* call's deadline.

**Both SDK majors are supported** (`mcp>=1.26.0,<3`). mcp 2.0 renamed every wire field to snake_case, moved to `httpx2`, changed `read_timeout_seconds` from `timedelta` to float, and dropped the third element from the Streamable HTTP stream tuple. `agentao/mcp/_compat.py` absorbs all four by **probing the installed SDK**, never by parsing a version string — read it before touching `client.py` / `tool.py`. Tests must build inputs from real `mcp.types` models: `SimpleNamespace` / `MagicMock` fakes hid every one of those breaks behind a green suite (`MagicMock` is actively harmful here — it answers `hasattr` for any name, so it satisfies a 2.x probe on a 1.x SDK).

**Protocol era: handshake first, escalate on a protocol rejection.** `McpClient._negotiate()` sends `initialize`, and only escalates to the modern era's `server/discover` when the server rejects it — definitely (`-32022`, which names the server's versions) or speculatively (`-32601`, since the modern era has no `initialize` handler at all; a failed speculative probe re-raises the *original* error). **This is deliberately the reverse of upstream's `mode='auto'`** — leading with the probe makes every python-mcp-1.x server reject the unknown method by dumping 258 lines of pydantic union-validation failure to its stderr, which for stdio *is* agentao's stderr (measured; see `docs/design/mcp-streamable-http.md` §5.8.1). The cost is that a dual-era server stays on the handshake era — fine while agentao only does tool discovery + tool calls, which behave identically in both. When that changes, flipping the order in `_negotiate` is the whole switch, and `test_a_dual_era_server_is_left_on_the_handshake_era` is the tripwire. An unresolvable mismatch raises `McpProtocolEraError`, whose *type* suppresses the "try `type: sse`" hint. The negotiated version is on `McpClient.protocol_version` / `get_server_status()["protocol"]` and is a **ceiling, not a constant** — gate on `>=`.

Key files: `agentao/mcp/config.py`, `client.py`, `tool.py`, `_compat.py`.

CLI: `/mcp list`, `/mcp add [--http|--sse] <name> <command|url>`, `/mcp remove <name>`.

### LLM wire protocols

`LLMClient` (`agentao/llm/client.py`) is a retry / logging shell over one **wire adapter**, chosen at construction by `api_format` (`{PROVIDER}_API_FORMAT`): `openai-completions` (`llm/_openai_completions.py`, the default), `anthropic-messages` (`llm/_anthropic_messages.py`; its SDK is a core dependency, imported lazily) or `openai-responses` (`llm/_openai_responses.py`, the same `openai` SDK; 0.5.3). `api_format` is **not** the provider — `LLM_PROVIDER` names a credential block, and the format is never inferred from a URL, a provider name or a model name (`llm/_api_format.py`; an unimplemented format fails closed). After construction only a provider switch changes it — `reconfigure(api_format=)`, reached from `/provider` (the target block's `_API_FORMAT`) and ACP's `provider_resolver` (an optional `api_format` key) — and a wire change is a switch in `runtime/model.py::set_provider`'s sense even with the model name and URL unchanged: it swaps the adapter and clears the same family. Stages 1 and 2 (`openai-responses`) of `docs/design/llm-api-adapters.md`, plus the provider-switch piece of stage 3.

- **History did not change shape, and must not.** `agent.messages` stays OpenAI dicts on every wire; an adapter translates an outbound *copy* and folds the response back into the one duck-type in `llm/_stream_response.py` — every adapter builds through `_StreamAccumulator`. A new wire adds an adapter, never a message model.
- **The Chat Completions adapter is an extraction, held byte-identical** to a request captured before it moved (`tests/test_llm_api_extraction_noop.py`, golden in `tests/data/`). Never regenerate that golden from the current build. Its two latches stay on `LLMClient` because tests and `/temperature` read them there; `LLMClient.client` stays the live SDK object for the same reason, and the adapter reads config back through `owner` at request time because `/model` and `/thinking` mutate the live client.
- **Signed thinking rides a second carrier.** `reasoning_content` is a 500-character display copy; Anthropic's signed blocks go back whole or are rejected, so they ride `anthropic_thinking_blocks` on the assistant dict (`chat_loop/_serialize.py::_attach_thinking_blocks`) — written at the **two** sites that record the model's own output, not at the four synthetic finals, which are a second message built from the same response. The key is untouched by `sanitize_assistant_message` on purpose (a block goes back as it arrived; a corrupted signature is a 400, observed) and is in `purge_thinking_artifacts`. **A new adapter's carrier key goes in `WIRE_CARRIER_KEYS`** (`llm/_stream_response.py`) and nowhere else: `_attach_thinking_blocks` records from that tuple and the purge — which runs on every switch whichever wire is live — drops from it, so a carrier cannot be persisted without being purged. The `openai-responses` wire's is `openai_reasoning_items`: its encrypted reasoning items, whole (`store: false` means the provider keeps nothing, so an item without `encrypted_content` names nothing and is not kept). A function call's `fc_` item id goes back **only beside** the reasoning it was produced with — pi-mono records the API refusing one without the other; api.openai.com with `store: false` accepted every combination (2026-09-19), so this is the conservative side, kept because `call_id` alone pairs the output.
- **The Responses wire is stateless, and its tool id is two ids in one slot.** `store: false`, no `previous_response_id`: compaction rewrites history, `/clear` wipes it and replay replays it, and a server-side conversation would diverge from all three. A function call has a `call_id` and an item `id`; history keeps `call_id|fc_…` (`llm/_tool_ids.py::compose_tool_id` / `split_tool_id`, a module of their own so the default adapter does not import another wire's — split on the **last** `|`, and only when the tail starts `fc_`, so an id minted on another wire is never split). That composite (83 characters as api.openai.com mints it) is longer than the **64** OpenAI's Chat Completions allows — observed; pi-mono's 40 is its own number, so `_openai_completions.py::_with_wire_tool_ids` sends it as the `call_id` in the outbound copy. **It rewrites composite ids only, and returns the same list when there are none** — that identity is what keeps the byte-identical golden true and a gateway's own long ids alone; two calls sharing a `call_id` get a hash, never a counter, so the spelling does not move when compaction changes what is in the request. `error` and `response.failed` are *yielded* by the `openai` SDK's iterator, not raised (the opposite of the `anthropic` SDK) — the adapter raises them, or the turn ends as an empty clean-looking answer. `tests/support/openai_responses_wire.py` validates every scripted event against the SDK's own `ResponseStreamEvent` union; keep new fixtures going through `stream_of`.
- **The `openai` SDK is unpinned above (`>=1.0.0`), so a fresh install gets 3.x while `uv.lock` has 2.24.0.** Measured 2026-09-19 on 3.16.2: the suite passes but for a mid-stream transport error arriving wrapped as `APIConnectionError` (the test accepts both), and 3.x *requires* `input_tokens_details.cache_write_tokens`, read as the cache-write count on both OpenAI wires (`llm/_usage.py::cache_token_counts`). `uv run --with 'openai==3.16.2' python -m pytest tests/ -n logical` is the check; an example directory's fresh `uv lock` is what resolves the new major.
- **Three facts that came from running the SDK, not from the protocol docs.** `anthropic` 1.6.0 has no `temperature` parameter at all, so this wire never sends one. It also refuses a non-streaming request above ~21k `max_tokens` (agentao's default is 65,536 and the summarizer names none), so `chat()` consumes the stream with no callback — there is no non-streaming path to "restore". And an `error` event inside a stream arrives on HTTP 200 as a bare `APIStatusError(status_code=200)`, so retry classification on this wire reads the body.
- **The Models API is asked where the network already is.** The adapter's `prepare()` is called by `LLMClient.chat` / `chat_stream` **before** `_build_request_kwargs`, so the logged `max_tokens` is the sent one — never at construction (an embedded host's constructor does no I/O) and never inside `build_request` (tests and the golden call it with no socket). A definite answer (200 or a 4xx) is final per model, a transient failure gets one more try, and `reset_latches` re-arms both; any failure or non-positive-int field leaves behaviour as it was, because Anthropic-compatible gateways answer 404. `tests/support/anthropic_wire.py::Wire` answers the route apart from its script, so a test's `requests` stay the Messages calls.
- **`usage.prompt_tokens` is the whole prompt** — `input_tokens + cache_creation + cache_read`. Anthropic's `input_tokens` is the uncached remainder, and the Tier-1 anchor takes `prompt_tokens` as the size of what was sent. Gemini's native field already includes the cache; do not copy the mapping across adapters.
- **Test a wire adapter against the real SDK.** `tests/support/anthropic_wire.py` replaces only the socket (`httpx2.MockTransport`), so request bodies are what the SDK serialized and events/exceptions are the SDK's own — that is how the `temperature` and non-streaming facts above were found. The four things the socket could not show were then observed on `api.anthropic.com` (2026-09-18, `claude-sonnet-5`; design doc, *live results*) — which also turned up what no fixture would: that model rejects `thinking.type.enabled` (it wants `adaptive` + `output_config.effort`) and returns empty thinking text beside the signature.

### Logging

`agentao.log` captures every LLM request/response, all tool calls with formatted JSON arguments, tool results, token usage, timestamps. Nothing is truncated. Logger lives in `agentao/llm/client.py` — read this file first when debugging tool execution or LLM behavior.

On the `anthropic-messages` wire the request is logged as the **canonical** OpenAI-shaped list, not the translated body — the logger is incremental over message indices and translation merges messages.

Content is **not verbatim**: the file handler carries a `_RedactingFormatter` that rewrites credential-shaped strings to `[REDACTED:<kind>]` using the shared patterns in `agentao/security/secret_scan.py`. It is a `Formatter`, not a `Filter`, deliberately — a `Filter` mutates the shared `LogRecord` and would leak the redaction into every other handler on the logger, including an embedded host's own. If a debugging session needs the raw bytes, that formatter is the single place to bypass.

### CLI slash commands

The authoritative list with full subcommand syntax lives in `agentao/cli/help_text.py`; `/help` renders it. The high-impact commands to know about when reasoning about agent behavior:

- `/mode read-only|workspace-write|full-access` — permission posture (see Permission modes above).
- `/plan` / `/plan implement` / `/plan show` — plan mode (LLM plans, does not execute).
- `/goal <objective> [--for 30m] [--turns 10] [--unbounded]` — long-task auto-continuation with a time/turn budget; subcommands `show|budget|pause|resume|edit|clear`. Host-owned loop in `cli/input_loop.py::run_goal_continuation`; state in `.agentao/goal.json` (`cli/goal_state.py`); `update_goal` tool injected via `add_tool` (`tools/goal.py`). See Common gotchas for `--turns` vs `max_iterations`.
- `/clear` — saves current session, clears conversation + **all memories**, starts a new one. Shares `MemoryManager.wipe_all()` with `/memory clear` (see Memory system), so a change to what a wipe *does* belongs there; only what the CLI *says* about it lives in `cli/_utils.py::wipe_all_memories`.
- `/model`, `/provider`, `/temperature`, `/thinking` — LLM config. `/thinking [minimal|low|medium|high|off]` sets thinking depth (`reasoning_effort`) on the live client's `extra_body` passthrough (`cli/commands/provider.py::handle_thinking_command`); `off` clears it. On the `anthropic-messages` wire it writes `output_config.effort` instead (levels from the Models API's `capabilities.effort`, else `low|medium|high|xhigh|max`; an unlisted word is refused, not stored); on `openai-responses` it writes `reasoning.effort`, leaves the host's other `reasoning` keys alone, and drops a carried-in `reasoning_effort`, which that API rejects. No auto-recovery — a model that rejects `reasoning_effort` fails until `off` (see `docs/design/host-llm-extra-params.md`).

## Adding new components

Adding a built-in tool or a skill to this repo: see [docs/guides/adding-components.md](docs/guides/adding-components.md). The non-obvious part is that tools register in `agentao/tooling/registry.py::register_builtin_tools()`, not in `agent.py`. A user-visible behaviour change or new public API is not done until `CHANGELOG.md` `[Unreleased]` carries an entry and every doc naming the old behaviour is updated in **both** twins.

## Common gotchas

- **A capability probe must check the *answer*, not the attribute.** `hasattr` / `getattr(obj, name, None)` is the fail-*open* misreading available at every duck-typed seam: the object answers, the call "succeeds", and the caller reports work that never happened. Twice now — a host tool declaring `copies_to_subagents` with a bare `def` instead of `@property` (a bound method is truthy whatever it returns), and `wipe_all_memories` believing anything that merely answered `wipe_all`, which made `/clear` print "all memories cleared" with `clear()` never called. Type-check what came back and fail closed. `_REQUIRED_AGENT_ATTRS` (`cli/app.py`) asserts only that the attribute *exists*.
- **Docs come in en/zh twins** — `.zh.md` alongside `.md` under `docs/`, plus `README.zh.md` and `developer-guide/en` + `/zh`. A doc change is not done until both twins say the same thing, and the stale one is not always the zh. Check whether a twin exists first: parts of `docs/guides/` are written in Chinese with no English counterpart at all.
- **Three ways a memory test passes without testing anything.** A fixture that puts the parent's store anywhere but `wd/.agentao/memory.db` is not the CLI's layout — that path is also what a bare `Agentao(...)` opens, so a same-file bug cannot fail such a test. And any read from a `:memory:` store *after* `close()` sees a fresh empty schema, so a post-close assertion holds whatever the store contained. And a swallowed failure has to be injected *below* the layer that swallows it: patching `MemoryManager.clear_all_session_summaries` rather than the store's `clear_session_summaries` raises into the caller's `except` and never runs the read-back the test exists for — still green, for the wrong reason.
- **`cli.py` was split into the `cli/` package** in 0.4.x. Older docs and design notes may still say `cli.py` — grep `agentao/cli/` for the actual handler.
- **`agentao.harness` is gone** (0.5.0; renamed to `agentao.host` in 0.4.2). Use `agentao.host.HostEvent`, `export_host_acp_json_schema`, etc. Older design notes still say `harness` for the package; the *word* also survives on purpose for the concept — Agentao running embedded in a host. So is `agentao.session`: session persistence is `agentao.embedding.sessions`, and `project_root` is required on every entry point — `None` is refused in `_session_dir`, not only by the signatures, because it used to mean the process cwd.
- **`allow_all_tools` is gone.** Use `/mode full-access` (or the equivalent host-API call) instead.
- **`agentao -p` is a shim** over `agentao run`. New automation should target `agentao run` directly — that's where the spec schema, Jinja2 templating, and exit codes are documented.
- **`Agentao(...)` has no callback kwargs, and only five positional parameters** (0.5.0). The eight legacy callbacks (`confirmation_callback`, `step_callback`, …) are a `TypeError`; `agentao.embedding.compat.build_compat_transport` is the surviving migration surface, and the sub-agent factory uses it too. Everything after `max_tokens` is keyword-only **on purpose**: the callbacks sat interleaved with `max_context_tokens` / `permission_engine` / `transport` / `plan_session`, so deleting them alone would have re-bound a positional caller's arguments silently. Same rule as adding one — new `__init__` parameters go after the `*`. Migration guide: `docs/migration/0.4.x-to-0.5.0.md`.
- **Don't intuition-audit architecture.** Before recommending borrowed patterns or claiming a gap exists, grep agentao to verify; subpackage `__init__.py` docstrings document intentional shims and rename trails.
- **`/goal --turns` is NOT `max_iterations`.** `--turns` caps the *outer* continuation count (how many `chat()` calls the goal loop drives); `max_iterations` caps the *inner* tool-call loop within a single `chat()`. They are orthogonal — both stay in force. The goal loop is host-owned (`cli/input_loop.py`), deliberately not the plugin `Stop`/`force_continue` path. Design: `docs/design/codex-goal-mechanism-review.md` §11.
- **Never move `arun` back onto the loop's default executor, and don't reach for `asyncio.to_thread` from an async tool.** `agent.py::arun` runs `chat()` on agentao's own `_get_arun_pool()`. This looks like pointless ceremony over `run_in_executor(None, ...)` and is not: a turn holds its worker for the *whole turn*, and partway through it blocks in `tool_executor._run_async_tool` waiting on a tool coroutine running on the host loop. Once concurrent turns reach the pool's worker count, anything on that loop needing a default-executor worker can never get one — and the turns are waiting on precisely that work. That includes code agentao does not control: `loop.getaddrinfo` submits to the default executor, so **every `httpx.AsyncClient` connect to a hostname** goes through it (measured with `trust_env=False`; with proxy env vars set httpx never resolves at all, which is how two earlier attempts at this measurement "proved" the opposite). Same reason `web.py` parses HTML on its own `_get_cpu_pool()`. Three pools, no contention: `agentao-arun-*` (turns), `agentao-web-html-*` (parsing), loop default (left free for httpx). Tests: `tests/test_web_fetch_async_tool.py` starves a one-worker default executor and asserts both still complete.

- **`agentao/compaction/__init__.py` must not re-export `coordinator`.** That `__init__` runs on *every* import of anything in the package — including the stdlib-only `from ...compaction.types import CompactionKind` in `agentao/plugins/hooks/_payload.py` — so re-exporting the coordinator would put `coordinator` → `context_manager` → the whole LLM stack behind a vocabulary import. Same reason `types.py` imports only the standard library: `context_manager.py` and `coordinator.py` both import from it, so both edges point down. **This rule is held by review, not by a test:** `agentao.host` does *not* re-export the compaction types (`docs/reference/host-api.md` points hosts at `agentao.compaction.types` directly), so import-layering rule 5 — "`import agentao.host` must not drag in the runtime or the LLM stack" — never reaches this file and would not catch a regression here.

- **Unicode tag stripping is structural, not a range filter — don't "simplify" it.** `security/unicode_tags.py::strip_unicode_tags` removes invisible U+E0000–E007F characters (ASCII smuggling: they render as nothing, tokenize losslessly, and let a web page or MCP result carry instructions only the model sees). Applied at four boundaries: the model-bound copy of every tool result (`runtime/tool_result_formatter.py::_format_one`, *after* the replay emit so the audit record keeps the original bytes), model output re-entering the runtime (`runtime/sanitize.py::sanitize_text_field`), and **two** terminal displays — both through `security/terminal_text.py::sanitize_terminal_text`, which adds the bidi controls and the terminal-escape strip on top of the tag pass. `acp_client/render.py::_sanitize_terminal_text` (an alias, kept because AC5's tests import that name) covers server-authored text; `cli/transport.py::_display` covers model-authored text at the tool-confirmation prompt, `ask_user`, reasoning, and the max-iterations pending list. **At a Rich boundary the strip is only half the job**: `[black on black]` needs no control byte at all — Rich turns it into `\x1b[30;40m` and the text renders invisible — so `_display` pairs the strip with `rich.markup.escape`. That pairing is why `sanitize_terminal_text` lives in `security/` (a leaf, per `tests/test_import_layering.py` rule 3) and does *not* import `rich` itself. It is a transform applied at named boundaries — **not** an ambient guarantee; skill/MCP descriptions inlined into the system prompt do not pass through it.
  - The block's one legitimate use is RGI emoji tag sequences, so a blind range filter destroys every subdivision flag — 🏴󠁧󠁢󠁳󠁣󠁴󠁿/🏴󠁧󠁢󠁷󠁬󠁳󠁿/🏴󠁧󠁢󠁥󠁮󠁧󠁿 all collapse to 🏴 (a live defect in goose's own fix, #10745). A run survives only as `U+1F3F4` + ≤5 lowercase-alnum tag chars + `U+E007F`. **Both bounds are load-bearing**: the per-sequence cap alone bounds nothing, since chaining N valid sequences yields N×5 hidden characters — hence `_MAX_TAG_SEQUENCES` caps how many survive per string.
  - `strip_unicode_tags` must return **the same object** when nothing was dropped (compare the result, not just "are there tag chars"), because `_sanitize_str_field` turns a non-identical return into a logged security warning — otherwise ordinary text containing 🏴󠁧󠁢󠁳󠁣󠁴󠁿 reports as sanitized.
  - **`tool_calls[*].id` and `function.arguments` are exempt, and the two sanitize paths (`_normalize_one`, `sanitize_assistant_message`) must agree on that** — strip on one path only and the history id stops matching the answering `role: "tool"` message, which strict APIs reject. `id` must round-trip byte-for-byte (this is also why agentao needs no duplicate-id collision guard, unlike goose); `arguments` is raw JSON *text* whose escaping is provider-dependent, so stripping there is vacuous under `ensure_ascii=True` and mutates decoded values otherwise. Doing `arguments` properly means stripping post-decode, in the argument-parsing path.

- **A model/provider switch purges thinking artifacts from history.** `runtime/model.py::purge_thinking_artifacts` drops `reasoning_content` and `thought_signature` — **at both levels, the `tool_calls[*]` entry and its `function`**, because `_serialize_tool_call` uses `model_dump()` precisely so the field survives wherever the provider put it, and the real Gemini shape puts it on the entry. Wired into `set_model` / `set_provider` when the model *or* the endpoint changes (a bare credential rotation leaves history alone), and into both wholesale-history-restore sites (`cli/commands/sessions.py` `/resume`, `acp/session_load.py`) — `/resume` deliberately does not restore the persisted model, which makes it a model switch in all but name. Both fields are minted by one specific model and rejected or ignored by another, and the OpenAI SDK does **not** strip unknown message keys, so without this they go back on the wire verbatim. The purge is unconditional rather than provenance-stamped on purpose: it runs *at the switch*, so everything in history was by definition minted by the model being left behind, and a provenance key would itself be forwarded to the API and need filtering back off. Same clear-on-switch family as the tiktoken encoding, the cached token anchor (which the purge also invalidates, since history just shrank) and the capability latches.

- **Cancellation budgets have to fit inside `tool_executor._ASYNC_CANCEL_ACK_TIMEOUT_S` (5s).** That is how long the AsyncTool dispatcher waits for a cancelled coroutine's cleanup before emitting `TOOL_COMPLETE` regardless. Cleanup that runs longer detaches exactly the work it was accounting for, with the invocation already reported done — so `web.py`'s parse drain (3s) and its *cancelled-path* browser/driver teardown (2s each) both sit under it. Two tests pin the relation, because `web.py` importing from `runtime` would invert the layering. Note the normal (non-cancelled) browser close keeps its generous 10s: nothing is waiting on a deadline there, and a slow-but-working close should not become a killed driver.

- **A thread hand-off cancels the awaiter, never the worker.** Nothing interrupts a running Python call from outside it. `web.py::_in_worker` is the pattern to copy: `submit()` directly (so you hold the `concurrent.futures.Future`), cancel it if it hasn't started, otherwise wait for it under a bounded budget and log if you give up. `loop.run_in_executor` hides that future, which is why it isn't used there.

- **Don't call `subprocess.run` for batch commands — use `agentao/capabilities/process.py::run_captured()`.** A bare `subprocess.run(timeout=)` only kills the direct child on timeout, so a grandchild holding the captured pipe (Windows `git` credential helpers, a user hook backgrounding a process) hangs `communicate()` past the timeout — and over ACP-stdio a hung tool wedges the turn until the client times out and drops the connection. `run_captured` runs the child in its own process group/session, feeds/detaches stdin explicitly (`input=` over a pipe, else `DEVNULL` so a child can't read the JSON-RPC channel), kills the whole tree via `kill_process_tree()` on timeout (`taskkill /T` / `killpg(pid)` — never `getpgid`, which races a zombie child), and decodes with `errors="replace"`. It also defaults `env=` to `build_child_env()`, which strips agentao's own provider credentials (`HARNESS_ENV_KEYS`) from the child — so a plugin hook that shells out to the provider needs an explicit `env=` or `AGENTAO_SCRUB_CHILD_ENV=0`. `search_file_content` and the plugin hook dispatcher route through `run_captured`; `LocalShellExecutor.run` keeps its own streaming + inactivity-timeout loop but shares `kill_process_tree` and the same scrubbed base env. (PRs #73/#74/#75.)
