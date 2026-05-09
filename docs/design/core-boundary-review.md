# Core Boundary Review (codex parallel, May 2026)

**Status:** Decision record. Drafted 2026-05-07 after a four-round comparison of agentao's `agentao/*` package layout against the recently-decoupled OpenAI codex `codex-rs/core` crate, fact-checked against current code.
**Audience:** Agentao maintainers planning the next round of "what should leave core" refactors.
**Companion:** `core-boundary-review.zh.md`.
**Method:** Codex baseline survey → agentao core audit → reverse-review against actual imports → deep investigation of the two flagged subpackages (`plugins/`, `session.py`) → revised priority table.
**Related:**
- `docs/design/embedded-host-contract.md` — locks the embedded-harness positioning this review respects.
- `docs/design/path-a-roadmap.md` — broader roadmap; this doc covers one slice.

---

## TL;DR

Four concrete items shouldn't be in agentao's core, ranked by ROI. Three further items raised in earlier rounds turned out to be wrong on inspection and have been retired.

**Do (in order):**

> **Status, 2026-05-07:** items #1, #2 (constructor callback tightening), #3 (session.py migration), #4 (permission engine API redesign), #5a (plugin validator/resolver split), #5b (plugin loader → `embedding/plugins/`), #6 boundary prep (`host` → `acp` lazy delegation) shipped. Commits: `838a952` (#3), `0310eda` (#1), `e467c95` (#2), `0bb4a06` (#4), `c600cd4` (#5a), `010ec4e` (#5b), `3eb5546` (#6 prep). Original priority table preserved below; checkboxes added.

1. **`replay/` → `Transport` subscriber.** ✅ **Done.** Full extraction (so replay disappears from the core facade entirely) covers four artifact classes:
   - **Top-level imports** — 10 names across 3 statements at `agent.py:25,31,36`.
   - **Constructor surface** — drop the `replay_config` parameter at `agent.py:91` and the four instance attributes initialized at `agent.py:339-345` (`_replay_recorder`, `_replay_adapter`, `_host_replay_sink`, `_replay_config`). The factory layer (`embedding/`) wires up `ReplayRecorder` as a Transport subscriber instead.
   - **Facade methods (6)** — 3 lifecycle at `agent.py:516-528` (`start_replay`, `end_replay`, `reload_replay_config`); 3 observability at `agent.py:557-583` (`_latest_session_summary_id`, `_emit_context_compressed`, `_emit_session_summary_if_new`). Plus the `self.end_replay()` call inside `close()` at `agent.py:502` migrates to a Transport-level teardown hook.
   - **Runtime call/attribute sites** — 6 `agent._emit_*(...)` calls in `runtime/chat_loop.py` re-routed through a unified event emit; 2 attribute reads (`turn.py:82` `agent._replay_adapter`, `llm_call.py:59` `agent._replay_config.capture_flags`) migrated to the Transport contract.

   ~3–5 days. The constructor/state removal is non-trivial because `turn.py:82` currently drives `replay_adapter.begin_turn()` / `end_turn()` directly — once those reads become Transport events the recorder can own its own state, but the migration has to land in one PR or replay breaks mid-way.

   **What actually shipped (2026-05-07):**
   - `Transport.subscribe(listener)` added to the Protocol (optional method); composable `EventBroadcaster` helper backs `NullTransport` / `SdkTransport` / `ACPTransport`.
   - Two new events: `EventType.TURN_BEGIN` / `EventType.TURN_END`. Runtime `turn.py` emits them through `agent.transport.emit(...)`; the spliced `ReplayAdapter._mirror` translates them into `begin_turn`/`end_turn` recorder writes. No subscriber-listener bookkeeping needed inside the manager — the splice itself routes them.
   - New `agentao/replay/manager.py::ReplayManager` owns recorder + adapter + host_replay_sink + config; `start(session_id)` / `end()` / `reload_config()` lifecycle.
   - `agent.py`: 4 internal attrs collapsed to 1 (`replay_manager`); 4 read-only `@property` views (`_replay_recorder`, `_replay_adapter`, `_host_replay_sink`, `_replay_config`) preserved as deprecation shims; 6 facade methods (`start_replay`, `end_replay`, `reload_replay_config`, `_latest_session_summary_id`, `_emit_context_compressed`, `_emit_session_summary_if_new`) kept as thin delegates to manager + observability helpers; `close()` now calls `self.replay_manager.end()`. The 10 top-level replay imports moved to `TYPE_CHECKING` only.
   - `runtime/llm_call.py:59` reads `capture_flags` via `agent.replay_manager.config.capture_flags` (returns `{}` when no manager attached).
   - `runtime/chat_loop.py` 5 `agent._emit_*` calls unchanged — they go through the agent's deprecation shims, which lazily import from `replay/observability.py`. Doc's "unified event emit" intent is satisfied (the shims `transport.emit(...)` already).
   - `embedding/factory.py` pops `replay_config` from `overrides` (no longer flows through ctor kwarg), then attaches `agent.replay_manager = ReplayManager(agent, config=replay_config)` post-construction.
   - CLI callers (`cli/session.py`, `cli/replay_commands.py`, `cli/commands.py:635-637`) and 4 test files unchanged — back-compat shims/properties carry them through. Migration to direct manager calls is opportunistic, not required.

   **Tests:** 2549 passed, 2 skipped. No regressions.

   **What did not happen:** the doc imagined the recorder being a pure subscriber on the inner transport. In practice the existing transport-wrap (`ReplayAdapter` wrapping `agent.transport`) is functionally a subscriber that runs *after* the inner emit — refactoring it to a `transport.subscribe(listener)` listener would have required either rewriting all of `_mirror` (487 lines) or a second wrap pattern, and the test surface uses `ReplayAdapter(transport, rec)` directly. The shipped design keeps `ReplayAdapter` as the translator unit, with `Transport.subscribe()` available to *future* non-replay observers (no current consumer).
2. **`Agentao.__init__` callback signature.** ✅ **Done.** Move all 8 deprecated callbacks (the 7 commonly known plus `on_max_iterations_callback`) out of the public constructor, route through `embedding/compat.py`. `build_compat_transport` already lives in `transport/sdk.py:82`; this is API tightening, not physical relocation. ~1 day.

   **What actually shipped (2026-05-07):**
   - New `agentao/embedding/compat.py` — documented public migration surface, re-exports `build_compat_transport` from `transport/sdk.py` (no physical relocation, per the doc's note). Module docstring spells out the recommended migration path: build an `SdkTransport` directly when feasible, or call `embedding.compat.build_compat_transport(...)` to wrap the legacy callback shape into a transport and pass `transport=` to `Agentao(...)`.
   - `Agentao.__init__` keeps the 8 deprecated kwargs accepted for back-compat (removal scheduled for 0.5.0) but now emits **a single `DeprecationWarning`** when any of them is set, naming all eight and pointing at `embedding.compat.build_compat_transport`. Hosts that pre-build a transport (the recommended path) bypass the warning entirely. The internal dispatch still uses `build_compat_transport` so existing tests/CLI keep working without code changes.
   - Docstring updated with the migration recipe + 0.5.0 removal note.
   - `transport/__init__.py` and `transport/sdk.py` unchanged — the symbol `build_compat_transport` remains importable from both `agentao.transport` (legacy) and `agentao.embedding.compat` (recommended).

   **Tests:** 2549 passed, 2 skipped. The four legacy-callback tests (`test_tool_confirmation.py`, `test_reliability_prompt.py`) now emit the new `DeprecationWarning` (visible in pytest's warnings summary) but don't fail; they're testing the deprecated path on purpose and migrate at 0.5.0 alongside the kwarg removal.

   **What did not happen:** the doc's "API tightening" intent stops short of physically removing the kwargs from the signature this PR — that's a hard breaking change blocked behind 0.5.0. This PR makes the deprecation real (warning + clear migration target); the actual signature surgery slots into the 0.5.0 alias-removal release alongside `harness/` and `agentao/session.py`.
3. **`agentao/session.py` → `agentao/embedding/sessions.py`.** Pure disk persistence (305 lines, `.agentao/sessions/*.json` save/load/list/delete + rotation). Zero imports from `agent.py` or `runtime/`. **Production: 5 import sites + 7 call sites** total. Of those 7, **6 need new explicit `project_root` plumbing** — `cli/session.py:55`, `cli/commands.py:532,560,567,575,590,609`. The 7th, `acp/session_load.py:176`, already passes `project_root=cwd` (a local from `_parse_cwd(...)` at L160) and only needs the import-path swap to the new module. **Tests: 4 sites** — `tests/test_session.py:10,11,131`, `tests/test_acp_multi_session.py:80`, `tests/test_acp_session_load.py:55`, `tests/test_acp_mcp_injection.py:42`. Migration order: **(1) one change introduces `agentao/embedding/sessions.py` AND replaces `agentao/session.py` with a wrapper shim** — old-path callers keep working through the shim throughout the migration. (2) Update production callers to pass `project_root` explicitly to the new path. (3) Only **then** tighten the new API: make `project_root` required and drop the `Path.cwd()` fallback on the new path (the shim retains it until 0.5.0). Tests get migrated at 0.5.0 alongside `harness/` alias removal. ~1 day.
4. **Permissions file I/O up to `embedding/`.** ✅ **Done.** This was an **engine API redesign**, not a half-day file move. `PermissionEngine.__init__` previously called `self._load_rules()` itself, which read `<user_root>/permissions.json` directly. Four caller sites passed `project_root` + `user_root` and relied on the engine to load: `embedding/factory.py:141`, `agents/tools.py:585`, `acp/session_new.py:306`, `acp/session_load.py:199`.

   **What actually shipped (2026-05-07):**
   - New `agentao/embedding/permission_loader.py` exposes `load_permission_rules(*, project_root, user_root) -> (rules, sources)`. This is the public migration surface — first-party callers and embedded hosts pre-load through it.
   - `PermissionEngine.__init__` adds two new keyword-only kwargs: `rules: Optional[List[Dict]]` and `loaded_sources: Optional[List[str]]`. When `rules` is provided (the recommended path), the engine performs **no disk I/O** and simply uses what the caller pre-loaded.
   - `_load_rules` and `_load_file` were **deleted** from `PermissionEngine` — file I/O has physically left `agentao/permissions.py` (incl. dropping the now-unused `json` and `logging` imports). The legacy `PermissionEngine(project_root=..., user_root=...)` form still works for back-compat: when `rules is None`, the constructor lazy-imports `agentao.embedding.permission_loader.load_permission_rules` and uses it. The lazy import keeps `permissions.py`'s module-load graph free of any `embedding/` dependency.
   - `embedding/__init__.py` re-exports `load_permission_rules` so hosts can import it from `agentao.embedding`.
   - All four first-party callers now pre-load explicitly: `embedding/factory.py`, `agents/tools.py` (sub-agent permission setup), `acp/session_new.py`, `acp/session_load.py`. Each constructs the engine with `rules=` / `loaded_sources=`, so production runtime never goes through the legacy auto-load path.
   - Tests preserved on the legacy auto-load path — 117+ test sites use `PermissionEngine(project_root=tmp_path, ...)` as a convenience and don't test file loading itself, so no DeprecationWarning was added (would have been wrong-shaped noise). One test (`tests/test_active_permissions.py::test_active_permissions_does_not_re_read_disk`) had to migrate its spy from the now-removed `engine._load_file` method to `agentao.embedding.permission_loader.load_permission_rules`; the test's intent (no disk re-reads on the hot path) is unchanged.

   **Tests:** 2549 passed, 2 skipped. No regressions.

   **What did not happen:** the legacy auto-load constructor path (`PermissionEngine(project_root=..., user_root=...)` without explicit `rules=`) is preserved, not deprecated. Tightening into a hard error is queued for a future release alongside other 0.5.0 API surgery; for now the lazy delegation to `embedding/permission_loader.py` satisfies the boundary intent (file I/O out of core) without breaking 117+ test instantiations and the published examples that use the convenience form.

**Defer (deeper analysis or wheel-split phase):**

5. **`plugins/` partial split.** `plugins/models.py` + `hooks.py` correctly stay in core (runtime depends on them). `plugins/manager.py` + `manifest.py` + `diagnostics.py` are import-graph-isolated from `runtime/` and could externalize, but `plugins/skills.py` and `plugins/agents.py` mix runtime-path validators with CLI-only resolvers and need to split first. 2–3 days; sensitive.
6. **`acp/` wheel split.** ACP → core is one-way (`acp/models.py:25`, `acp/session_new.py:43`, lazy/TYPE_CHECKING). One reverse-import was missed by the round-2 grep: `host/schema.py:21` top-level-imported `..acp.schema` to power the public `export_host_acp_json_schema()` exporter. ✅ **Boundary fix shipped** — implementation moved to `agentao/acp/schema_export.py::build_host_acp_json_schema`; `host/schema.py::export_host_acp_json_schema` is now a thin lazy-import wrapper. `import agentao.host` no longer pulls `agentao.acp` into `sys.modules`; public API unchanged. The actual wheel split (publishing `agentao-acp` separately) remains deferred — packaging-side work, not code.
7. **`harness/` alias removal.** Already on the 0.5.0 schedule.

**Removed from backlog (first-pass errors):**

- "host/ vs harness/ duplicated" — actually a documented 0.4.2→0.5.0 rename shim (`host/__init__.py:30-34`).
- "build_compat_transport in cli/" — already in `transport/sdk.py:82`. The remaining issue is signature, not location (covered by #2).
- "MCP list ops down to `MCPRegistry.list_servers()`" — phantom coupling. agentao has no LLM-facing MCP list tool; `/mcp list` already calls `mcp_manager.get_server_status()` (`cli/commands.py:331`) directly. Swapping to `list_servers()` would lose connection status and registered tool counts. Codex's `#21281` (MCP enumeration → app-server) doesn't map because agentao never had the symptom in the first place. See §4 row G.

---

## 1. Method, and why it kept being wrong

The original opinion went through three rounds before reaching usable form:

- **Round 1**: enumerate everything codex's core does, compare to agentao's tree, flag what looks misplaced. Produced a six-item priority table.
- **Round 2 (reverse review)**: a peer fact-checked round 1 against the actual code and found six factual errors — two items were already done, one was a documented shim mistaken for stale code, the callback count was wrong, file locations were wrong, and one flagged item (`acp/`) had zero imports from core.
- **Round 3**: revised the priority table, kept the framing, dropped the false items.
- **Round 4 (this doc)**: deep investigation of the two items round 2 flagged as gaps (`plugins/`, top-level `session.py`), with grep-verified import maps.

**Lesson folded back into memory:** `feedback_core_boundary_review.md` — in this codebase, intentional structure is documented. Future audits must grep cross-imports and read every subpackage's `__init__.py` docstring before calling code "stale" or "duplicated."

---

## 2. Codex baseline (post-decoupling, May 2026)

Codex's `codex-rs/core` crate has converged on **inference loop + tool dispatch + policy generation**. Everything else moved to sibling crates.

| Stayed in core | Pushed out |
|---|---|
| Turn loop / inference state machine (`session/`, `codex_thread.rs`) | Message history → `message-history` crate (#21278) |
| Tool registry + dispatch + 50+ handlers (`tools/`) | Thread naming → `app-server` (#21260) |
| Approval-request generation (`guardian/`) | `ListSkills` / `ListModels` ops removed (#21282 / #21276) |
| Exec-policy parsing + sandbox translation | MCP server enumeration → `app-server` (#21281) |
| System-prompt assembly (`context/`, `context_manager.rs`) | Plugin loading → `core-plugins` (observed) |
| Multi-agent orchestration (LLM-callable) | Skill loading → `core-skills` (observed; watcher hybrid) |
| MCP tool-call dispatch (thin shell) | Thread summary generation → `app-server` (observed) |
| **Memory injection / session reconstruction** *(memories pipeline still in core — see [`codex-rs/core/src/memories/README.md`](https://github.com/openai/codex/blob/main/codex-rs/core/src/memories/README.md), titled "Memories Pipeline (Core)")* | ~~Persistent memories → `memories` crate~~ — **withdrawn**: an earlier draft listed this without a PR citation; on re-check, codex's main branch keeps the memories pipeline + state DB inside core. There is a `memories-mcp` adapter (#20622), but that is an MCP shell over the same core pipeline, not a relocation |

**Sourcing note:** rows with `(#NNNNN)` reference verified codex PRs. Rows marked **(observed)** are trends extrapolated from commit messages and crate boundaries as of 2026-05-07; before relying on them as load-bearing arguments, re-verify against the latest codex `main`. The memories row above is preserved as a strikethrough so future readers can see what was withdrawn and why.

The boundary rule, in one sentence: **core generates events; siblings persist, enumerate, summarize, and render**.

This is the yardstick we apply to agentao below.

---

## 3. Agentao core audit (verified)

agentao has no explicit `core/` directory; `agentao/agent.py` (765 lines) is the de facto core entry point. Mapping each subpackage against the codex rule:

| Subpackage | Codex parallel | Verdict |
|---|---|---|
| `runtime/` (turn, chat_loop, llm_call, tool_executor) | core ✅ | Keep |
| `tools/` (handler implementations) | core ✅ | Keep |
| `tooling/` (registry, MCP/agent registration helpers) | core ✅ | Keep |
| `capabilities/` (FileSystem/Shell/MCPRegistry injection protocols) | core ✅ (public injection surface) | Keep |
| `permissions.py` + `permissions_hardline.py` | core ✅ (engine), file I/O ❌ | Engine stays; loading moves (#4) |
| `prompts/` (system-prompt builder) | core ✅ | Keep |
| `plan/PlanSession` | core ✅ (LLM-callable) | Keep |
| `agents/AgentManager` | core ✅ (LLM-callable subagents) | Keep |
| `skills/SkillManager` | core ✅ (injection); loading delegated | Keep |
| `memory/MemoryManager` | TYPE_CHECKING injection; already pluggable | Keep |
| `mcp/McpClientManager` | core wrapper, no LLM-facing list op exists in agentao | Keep (see §4 row G) |
| `host/` (events, schema, projection) | core ✅ (public contract package, renamed from `harness/` in 0.4.2) | Keep |
| `harness/` | Deprecation shim, scheduled removal in 0.5.0 (`__init__.py:1-25`) | #7 (already planned) |
| `replay/` (top-level imports — 3 statements at `agent.py:25,31,36`, ending `)` at L30/L35/L40; constructor param L91; 4 instance attrs L339-345; 6 facade methods L516-583; `close()` teardown L502) | App-server-equivalent; doesn't belong in core | #1 |
| `acp/` + `acp_client/` | App-server-equivalent | Defer (#6); ACP imports core (lazy/TYPE_CHECKING `Agentao`), core never reverse-imports ACP |
| `cli/` (22 files / 7175 lines) | tui-crate-equivalent; logically OK, signature couples via 8 callbacks | #2 |
| `embedding/` (`build_from_environment`) | Factory layer, not core | Keep separated |
| `sandbox/profiles` | Small; codex parallel is `codex-sandboxing` crate | Defer |
| `security/` | Audit pending; codex spreads this across guardian + sandboxing | Open question |
| `plugins/` | Codex `core-plugins` parallel; partial overlap, see §5 | #5 |
| `session.py` (top-level) | `message-history` crate parallel; pure disk persistence | #3 |

---

## 4. Round 2 corrections (so they don't get relitigated)

Round 1's priority table contained these errors. They are now retired:

| # | Round 1 claim | Reality | Status |
|---|---|---|---|
| A | `host/` and `harness/` look duplicated / unaligned | `host/__init__.py:30-34` documents the 0.4.2 rename and `harness/__init__.py:1-25` is the deprecation shim with `DeprecationWarning`, scheduled removal in 0.5.0 | False alarm; rename is intentional |
| B | `build_compat_transport` lives in `cli/` and should move to embedding | Already in `transport/sdk.py:82` | The remaining concern is signature surface (#2), not location |
| C | `replay/` is "in core" because of imports + chat_loop subscribes to it | True coupling, four artifact classes: (1) top-level imports — 3 statements at `agent.py:25,31,36` (closing `)` at L30/L35/L40), (2) `Agentao.__init__` `replay_config` param + 4 attrs L91/339-345 + `close()` `end_replay()` L502, (3) 6 facade methods L516-583, (4) 6 `chat_loop` `_emit_*` calls + 2 attr reads (`turn.py:82`, `llm_call.py:59`) | #1 is 3–5 days; constructor/state removal must land in one PR with the runtime migration |
| D | 7 deprecated callbacks in `__init__` | Actually 8 (forgot `on_max_iterations_callback` at L71/247/260) | Off-by-one; #2 work item still valid |
| E | `acp/` is logically coupled to core | Direction matters: `acp/models.py:25` and `acp/session_new.py:43` import `agentao.agent.Agentao` (lazy/TYPE_CHECKING), so ACP → core is a real dependency. **Core → ACP** is what's empty (grep `agent.py` / `runtime/` / `tools/` for `acp` returns zero hits), which is the correct direction for a wheel split | Priority demoted to "wheel-split phase"; phrasing tightened in TL;DR |
| F | CLI is 18 files / 6057 lines | 22 files / 7175 lines (missed `commands_ext/` subfolder) | Counting error |
| G | "MCP list ops are LLM-facing; CLI/ACP can call `MCPRegistry.list_servers()` directly to remove the LLM-facing list operation" | No LLM-facing MCP list tool exists (grep `tools/`, `tooling/` returned nothing). `/mcp list` already calls `mcp_manager.get_server_status()` (`cli/commands.py:331`), not via the LLM. `MCPRegistry.list_servers()` (`mcp/registry.py:45`) returns only configured servers; swapping `/mcp list` to use it would lose connection status and per-server tool counts. Codex's `#21281` (MCP enumeration → app-server) was about removing an op that *did* exist as an LLM tool in codex; agentao never had the symptom | Phantom coupling — codex-title was carried over without verifying agentao's actual entrypoints. Item dropped from priority table |
| H | "replay/ has 4 facade methods on `Agentao`" | Actually 6 — 3 lifecycle (`start_replay` / `end_replay` / `reload_replay_config` at L516-528) + 3 observability (`_latest_session_summary_id` / `_emit_context_compressed` / `_emit_session_summary_if_new` at L557-583) | Off-by-two; #1 work scope unchanged but description corrected |
| I | "session.py update touches five import sites" | Five production import sites; 7 production call sites total; **6 need new explicit `project_root` plumbing** (`cli/session.py:55`, `cli/commands.py:532,560,567,575,590,609`); the 7th, `acp/session_load.py:176`, already passes `project_root=cwd` and only needs the import-path swap; **plus four test files** (`test_session.py`, `test_acp_multi_session.py`, `test_acp_session_load.py`, `test_acp_mcp_injection.py`). Deprecation shim wraps the old permissive signature and delegates to `embedding.sessions` | Re-estimated to ~1 day (was "half-day"); the per-call-site `project_root` plumbing is the bulk; tests cleaned up at 0.5.0 alongside `harness/` alias removal |
| J | "Permissions file I/O is a half-day factory-layer move" | `PermissionEngine.__init__` calls `self._load_rules()` and reads `<user_root>/permissions.json`. Four caller sites construct it with `(project_root, user_root)` (`embedding/factory.py:141`, `agents/tools.py:585`, `acp/session_new.py:306`, `acp/session_load.py:199`). Move requires constructor change + 4 callers updated | Engine API redesign, not cosmetic move; revised to 1–1.5 days, risk Medium |

---

## 5. Deep investigation: `plugins/`

### Size and shape

9 files, 3360 lines. `hooks.py` alone is 1236 lines. Top-level `__init__.py` re-exports only data classes (`LoadedPlugin`, `PluginManifest`, `PluginAgentDefinition`, ...). `PluginManager` and the dispatcher classes are not in `__all__`.

### Reverse-import map (grep-verified)

| Caller | Imports | Coupling |
|---|---|---|
| `runtime/chat_loop.py:26` | `from ..plugins.models import StopHookResult` (module-level) | 🔴 Hard runtime |
| `runtime/chat_loop.py:582,656,726` | lazy `plugins.hooks` (lifecycle dispatch) | 🔴 Per-turn |
| `runtime/tool_executor.py:102,586,608,632` | lazy `plugins.hooks` (per tool call) | 🔴 Per tool |
| `agents/manager.py:106-107` | lazy `plugins.agents`, `plugins.models` | 🟡 Init-time |
| `skills/manager.py:377-378` | lazy `plugins.skills`, `plugins.models` | 🟡 Init-time |
| `cli/session.py:72,91` | lazy `plugins.hooks` (SessionStart/End) | ⚪ CLI |
| `cli/subcommands.py:250-411` | lazy `plugins.{manager, manifest, skills, agents, mcp, diagnostics, hooks}` | ⚪ CLI |
| `cli/entrypoints.py:44,66` | lazy `plugins.hooks` | ⚪ CLI |

### Comparison with codex

Codex separates plugin **loading** (manifest parsing, marketplace sync, install) from plugin **hook dispatch** (lifecycle event firing, trust enforcement). Loading went to `core-plugins`; hook dispatch stayed in core (e.g. commits #19905 compact lifecycle hooks, #20321 hook trust metadata).

Mapping that to agentao:

- `models.py` (322 lines) — needed by `runtime/chat_loop.py:26` for `StopHookResult`. **Stays.**
- `hooks.py` (1236 lines) — needed by every tool call and chat loop iteration. **Stays.**
- `manager.py` (522 lines) — `PluginManager` discovery/loading. Only consumed at init time + by CLI. **Could externalize.**
- `manifest.py` (476 lines) — `PluginManifestParser`. Same shape as manager. **Could externalize.**
- `diagnostics.py` (74 lines) — CLI-only. **Should externalize.**
- `mcp.py` (144 lines) — only `resolve_plugin_mcp_servers` / `merge_plugin_mcp_servers`; **no `validate_no_external_collisions`**. Sole consumer is `cli/subcommands.py:318` (`/plugins sync`). Pure CLI/loader. **Should externalize.**
- `skills.py` (369 lines), `agents.py` (190 lines) — **mixed.** Each contains `validate_no_external_collisions` (called from `skills/manager.py` and `agents/manager.py` at agent init, runtime path) plus `resolve_plugin_entries` / `resolve_plugin_agents` (CLI-only). Cannot externalize wholesale.

### Recommendation

Not a quick win. Two-phase split:

**Phase 5a (sensitive, 2–3 days):** ✅ **Done.** Within `plugins/skills.py` and `plugins/agents.py`, separate validators (runtime path) from resolvers (CLI). Validators stay in core; resolvers move to a new `plugins/resolvers/` or to embedding. `plugins/mcp.py` does not need this split — it has no validator surface.

**What actually shipped (2026-05-07):**
- New `agentao/plugins/resolvers/` package: `resolvers/skills.py` houses `resolve_plugin_entries` plus all eight private helpers (`_resolve_skills`, `_parse_skill_md`, `_resolve_commands`, `_scan_commands_dir`, `_md_file_to_entry`, `_metadata_to_entry`, `_check_internal_collisions`, `_parse_yaml_frontmatter`); `resolvers/agents.py` houses `resolve_plugin_agents` plus its four private helpers. The `__init__.py` re-exports `resolve_plugin_entries` and `resolve_plugin_agents`, and its module docstring spells out the runtime/loader split rationale + the 5b relocation plan.
- `agentao/plugins/skills.py` and `agentao/plugins/agents.py` slimmed to validators-only — each module now exports a single `validate_no_external_collisions` function. Module docstrings call out the runtime path (`SkillManager.register_plugin_skills` / `AgentManager.register_plugin_agents`) and point at the resolvers package for the loader-side functions.
- Dead code dropped: `PluginSkillCollisionError` (defined in the old `plugins/skills.py` but never imported anywhere — verified by grep) was deleted rather than carried into either side of the split.
- Two CLI import sites in `agentao/cli/subcommands.py` (`_plugin_list_cli` and `_load_and_register_plugins`) repointed to `..plugins.resolvers.skills` / `..plugins.resolvers.agents`. Runtime callers (`agentao/skills/manager.py:378`, `agentao/agents/manager.py:106`) keep importing `validate_no_external_collisions` from `agentao.plugins.skills` / `agentao.plugins.agents` — those paths are now validator-only and no longer load any resolution code.
- Three test files migrated their imports: `tests/test_plugin_skills.py` and `tests/test_plugin_agents.py` split their imports between the resolvers and validator modules; `tests/test_plugin_loader.py` repointed two `resolve_plugin_entries` imports.
- No back-compat shim left in `plugins/skills.py` / `plugins/agents.py`. Resolvers and validators are private package surface (not in `agentao.plugins.__all__`); only first-party callers were touched, so a redirect re-export would be wrong-shaped noise.

**Tests:** 2549 passed, 2 skipped. No regressions.

**What did not happen:** Phase 5b is still pending — `manager.py`, `manifest.py`, `diagnostics.py`, `mcp.py`, and the new `resolvers/` package have not yet relocated to `embedding/plugins/`. The split shipped here is the prerequisite that makes 5b mechanical (no validator-vs-resolver entanglement remaining), but the actual move belongs in its own PR per the priority table.

**Phase 5b (mechanical, 1 day):** ✅ **Done.** Once 5a lands, move `manager.py` + `manifest.py` + `diagnostics.py` + `mcp.py` + the new resolvers into `agentao-plugins-loader/` (or `embedding/plugins/`). `runtime/` and `agent.py` will not need any import changes.

**What actually shipped (2026-05-07):**
- 8 files moved via `git mv` into `agentao/embedding/plugins/` (loader + resolvers); `agentao/plugins/` retains models/hooks/validators only. The new package `__init__.py` re-exports the loader surface.
- Cross-package imports inside the moved files switched from `from .models` / `from ..models` to absolute `from agentao.plugins.models`, since the relative paths no longer resolve.
- No back-compat shim at the old paths. The loader symbols were never in `agentao.plugins.__all__` and all callers are first-party — same call as 5a.

**Tests:** 2549 passed, 2 skipped. `runtime/` and `agent.py` imports unchanged, as predicted.

**What did not happen:** No physical wheel split. If a future release publishes `agentao-plugins-loader` as a separate distribution, that's a packaging change layered on top of this move, not a code change.

**Why deferred:** the validator/resolver split requires careful threading through `SkillManager.__init__` and `AgentManager.__init__`. Moving that under time pressure risks breaking plugin discovery without a clear test signal. Items #1–#4 are higher-ROI and should land first.

### Import map after 5a/5b

There is intentionally **no public plugin SDK surface** today. All non-`models` symbols are first-party only — no shim, no facade, no re-export. Future contributors should consult this table before reaching for an `agentao.plugins.*` import:

| Tier | Path | Audience | Notes |
|---|---|---|---|
| Public runtime models | `agentao.plugins`, `agentao.plugins.models` | external + first-party | The only entries in `agentao.plugins.__all__`. |
| First-party runtime helpers | `agentao.plugins.skills`, `agentao.plugins.agents`, `agentao.plugins.hooks` | first-party only | Validators + hook dispatch on the runtime hot path. Not in `__all__`. |
| First-party loader | `agentao.embedding.plugins.{manager, manifest, diagnostics, mcp}`, `agentao.embedding.plugins.resolvers.*` | first-party only (CLI / `_load_and_register_plugins`) | Reached at agent init and from CLI subcommands. Pulls YAML. Must not be imported on the runtime hot path. |

**Boundary contract test:** `tests/test_plugin_boundary_contract.py` asserts in a clean subprocess that `import agentao.plugins` does not transitively load the loader package or YAML. That makes the runtime/loader split an executable invariant rather than a convention.

---

## 6. Deep investigation: top-level `session.py`

### What it is

`agentao/session.py` (305 lines, pure I/O):

- `save_session` → writes `.agentao/sessions/{ts}.json`
- `load_session` / `list_sessions` / `delete_session` / `delete_all_sessions`
- `strip_system_reminders`, `format_session_time_local` utility helpers
- `_MAX_SESSIONS = 10` rotation

### Reverse-import map (grep-verified)

After filtering noise from `plan/.session` and `cli/.session` (different modules), there are **five production consumers** plus **four test consumers** of the top-level `agentao/session.py`:

**Production (5):**

| Caller | Pattern |
|---|---|
| `acp/session_load.py:68` | `from agentao.session import load_session` (module-level) |
| `cli/session.py:52` | `from ..session import save_session` (lazy in `on_session_end`) |
| `cli/commands.py:519` | `from ..session import (delete_all_sessions, delete_session, format_session_time_local, list_sessions)` (lazy in `/sessions`) |
| `cli/commands.py:588` | `from ..session import list_sessions, load_session` (lazy in `/resume`) |
| `cli/replay_commands.py:22` | `from ..session import strip_system_reminders` (module-level) |

**Tests (4):**

| Caller | Pattern |
|---|---|
| `tests/test_session.py:10,11,131` | `import agentao.session as session_module`; `from agentao.session import (...)`; `from agentao.session import _rotate_sessions` |
| `tests/test_acp_multi_session.py:80` | `from agentao.session import save_session` |
| `tests/test_acp_session_load.py:55` | `from agentao.session import save_session` |
| `tests/test_acp_mcp_injection.py:42` | `from agentao.session import save_session` |

All nine are CLI/ACP/tests — none of them are `agent.py` or `runtime/*.py`. Verified by grep. The deprecation shim exposes wrapper functions at the old path that preserve the permissive (`project_root: Optional[Path] = None`) signature and delegate to `embedding.sessions.*` with `Path.cwd()` filled in — so all nine continue working at runtime; the three ACP tests get migrated at 0.5.0 alongside the `harness/` alias removal. **`tests/test_session.py` is the one exception**: its `isolated_session_dir` fixture monkeypatches the private `_session_dir` helper on the old module, and wrapper-shim mechanics provably cannot intercept internal-helper monkeypatching across module boundaries (`embedding.sessions.save_session` resolves `_session_dir` in its own lexical scope). The fixture's patch target had to migrate immediately — a one-line change to point at `agentao.embedding.sessions._session_dir`. Public call signatures and assertions are unchanged.

### Does it overlap with `host/`'s session concept?

No. `host/`'s 30+ `session_id` references are correlation IDs for event-stream filtering and projection lineage (`host/events.py:73,84,89`, `host/projection.py:115-296`, `host/models.py:83,98,111`). They are identity tokens, not persistence. The two layers happen to use the word "session" for different concepts and don't conflict; this should be noted in `docs/api/host.md` to avoid future confusion.

### Comparison with codex

Codex pushed message history out via #21278 (separate `message-history` crate) and thread metadata via `thread-store`. agentao's `session.py` is roughly the union of those two concerns — message history *and* title/timestamp/active-skills metadata, in one JSON file per session.

It is exactly the kind of "transactional persistence" codex moved out.

### Bonus finding: `Path.cwd()` fallback

`session.py:29` resolves `project_root` via `Path.cwd()` when none is passed. This is a global-state read inside what should be a pure function — it conflicts with the embedded-harness "no globals" principle (`project_agentao_embedded_harness` memory). The fallback exists for legacy CLI compatibility and should be removed during the move.

### Recommendation

Move to `agentao/embedding/sessions.py`. Order matters — making `project_root` required without first updating CLI/ACP callers will TypeError every save/list/resume/delete:

1. **Single change**: introduce `agentao/embedding/sessions.py` (305 → ~280 lines after dropping the cwd fallback) AND replace `agentao/session.py` with the wrapper shim described in step 3 below. Both files exist after this change so the old import path never breaks. The new path keeps `project_root` optional in this step so the migration can proceed before tightening.
2. Update five production import sites **and pass `project_root` explicitly at each call** (importing from the new path; the shim is for external/test users only):
   - `cli/session.py:55` — `save_session(..., project_root=cli.agent.working_directory)` (call inside `on_session_end`).
   - `cli/commands.py:532, 560, 590` — `list_sessions(project_root=cli.agent.working_directory)` (`/sessions` and `/resume`).
   - `cli/commands.py:567` — `delete_all_sessions(project_root=cli.agent.working_directory)`.
   - `cli/commands.py:575` — `delete_session(sub_arg, project_root=cli.agent.working_directory)`.
   - `cli/commands.py:609` — `load_session(match["id"], project_root=cli.agent.working_directory)`.
   - `acp/session_load.py:176` — already passes `project_root=cwd` (`cwd` is a local from `_parse_cwd(params.get("cwd"))` at L160); no caller change needed beyond updating the import path to `agentao.embedding.sessions`.
   - `cli/replay_commands.py:22` — only imports `strip_system_reminders` (pure function, no `project_root`); no caller change needed beyond the import path.
3. Leave `agentao/session.py` as a deprecation shim with `DeprecationWarning`, mirroring the `harness/` → `host/` pattern in spirit but **not in mechanics**: pure `from agentao.embedding.sessions import *` re-export would inherit the new path's required-`project_root` signature and break every existing caller. Instead, the shim defines wrapper functions matching the **old** (permissive) signature — `project_root: Optional[Path] = None` — and inside each wrapper supplies `project_root = project_root or Path.cwd()` before delegating to `agentao.embedding.sessions.{save,load,list,delete,...}_session(...)`. This keeps external users and the four test files working without leaking the `Path.cwd()` global into the new path.
4. Test sites: the three ACP tests (`tests/test_acp_multi_session.py`, `tests/test_acp_session_load.py`, `tests/test_acp_mcp_injection.py`) call only public functions through the shim and migrate at 0.5.0 when the shim is removed. **`tests/test_session.py` is the exception** — its `isolated_session_dir` fixture monkeypatches the private `_session_dir` helper, and wrapper-shim mechanics cannot forward private-helper monkeypatching across module boundaries (the shim's wrapper calls `embedding.sessions.save_session`, which resolves `_session_dir` in its own lexical scope). The fixture's patch target migrates **immediately** to `agentao.embedding.sessions._session_dir`; the assertions and public call signatures stay unchanged.
5. **Then** make `project_root` required on the `embedding/sessions.py` API (no default), and remove the `Path.cwd()` fallback from the new path. The shim retains the fallback until 0.5.0.
6. At 0.5.0, delete `agentao/session.py` along with the `harness/` alias; migrate the four test files to import from `agentao.embedding.sessions` and pass `project_root` explicitly.

**Effort:** ~1 day (re-estimated up from "half day" — the per-call-site `project_root` plumbing is the bulk of the work). **ROI:** high — every top-level module that `core` doesn't import but lives in the main package muddies the boundary.

---

## 7. Revised priority table

| # | Action | Effort | Risk | ROI |
|---|---|---|---|---|
| 1 | replay → Transport subscriber: delete 10 top-level imports + **constructor `replay_config` param + 4 instance attrs (L91/339-345)** + **6 facade methods** (L516-583) + `close()` teardown (L502), rewrite `chat_loop` `agent._emit_*(...)` (×6), migrate the 2 attribute reads (`turn.py:82`, `llm_call.py:59`); recorder wired by `embedding/` factory as Transport subscriber | 3–5 days | Medium | 🟢 High |
| 2 | Tighten `Agentao.__init__`: 8 deprecated callbacks → `embedding/compat.py` | 1 day | Low | 🟢 High |
| 3 | `session.py` → `embedding/sessions.py`; per-call-site `project_root` plumbing (6 of 7 production calls need new plumbing; 7th — ACP load — only needs import-path swap); shim keeps `Path.cwd()` fallback + optional `project_root` until 0.5.0; new path drops both | ~1 day | Low | 🟢 High |
| 4 | Permissions file I/O up to `embedding/` — **engine API redesign** (constructor change + 4 caller updates) | 1–1.5 days | Medium | 🟡 Medium |
| 5a | ✅ `plugins/skills.py`, `plugins/agents.py` — validator/resolver split | 2–3 days | Medium | 🟡 Medium |
| 5b | ✅ Externalize `plugins/{manager, manifest, diagnostics, mcp, resolvers}` once 5a lands | 1 day | Low | ⚪ Long-term |
| 6 | `acp/` wheel split — boundary prep ✅ (host→acp lazy delegation), packaging deferred | — | Low | ⚪ Long-term (no logical coupling after prep) |
| 7 | Remove `agentao.harness/` alias in 0.5.0 | ~half hour | Zero | ⚪ Already scheduled |

Item dropped from the table:

- **MCP list ops** (was #4 in earlier drafts) — phantom coupling. agentao has no LLM-facing MCP list tool; `/mcp list` already calls `mcp_manager.get_server_status()` directly. See §4 row G.

Open questions still pending audit:

- `agentao/security/` — what's there, and is the codex parallel (`guardian/` + `codex-sandboxing`) cleaner?
- `agentao/sandbox/profiles` — small enough to keep, but worth confirming nothing belongs in `capabilities/`.

---

## 8. Suggested execution order

Three batches:

**Batch A (high ROI, ~5–7 days, one PR each):** #1, #2, #3 (in any order; they don't depend on each other). #1 dominates the budget — but unlike the earlier scoping, the constructor/state removal must ship together with the runtime migration in one PR (otherwise replay breaks mid-way), so the "split into chat_loop-only PR first" option is **off the table** for this item.

**Batch B (engine surface change, 1–1.5 days):** #4 alone. The constructor signature touches embedding/factory, agents/tools, and both ACP session paths — keep it isolated in its own PR with a clear migration note for embedded hosts that construct `PermissionEngine` directly.

**Batch C (sensitive, separate PR cycle):** #5a then #5b. Don't mix with the others.

#6 and #7 are opportunistic — slot in when wheel splits or release-train work happens.

---

## 9. What this doc deliberately does not cover

- **Whether the `Agentao` class itself should be renamed `Core` and made smaller.** The 765-line facade is real, but most of those lines are constructor-time wiring; reducing surface is a downstream effect of #1–#5, not an independent task.
- **Multi-agent positioning.** Codex's spawn/wait/close primitives don't fit agentao's embedded-harness story. Decided in earlier rounds; not revisiting here.
- **Memory MCP-ization.** Codex shipped `memories-mcp` (#20622). For agentao this would only matter if cross-host memory sharing becomes a real ask; not currently a gap.
- **Goal/budget tooling.** Codex has it; agentao deliberately doesn't (host owns budget). Decided in the borrow review.

These are out of scope so this doc stays a boundary review, not a roadmap.
