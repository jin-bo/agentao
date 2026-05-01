# Changelog

All notable changes to Agentao are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

> **Migration heads-up — 0.4.0 break approaching.** `pip install agentao`
> will shrink to an embedding-only core (7 packages). `rich` /
> `prompt-toolkit` / `readchar` / `pygments` move to `[cli]`;
> `beautifulsoup4` to `[web]`; `jieba` to `[i18n]`. The public Python API
> is unchanged. CLI users want `pip install 'agentao[cli]'`. The
> zero-behaviour-change upgrade line is `pip install 'agentao[full]'`.
> Full guide: [`docs/migration/0.3.x-to-0.4.0.md`](docs/migration/0.3.x-to-0.4.0.md).

Targeting **0.4.0** — the single break release of the Path A P0 plan
(see `docs/design/path-a-roadmap.md` §3.2). The break is a packaging
change only; no public Python API is renamed, removed, or signature-
changed. The "no-change" upgrade line is `pip install 'agentao[full]'`,
which reproduces the 0.3.x bundled closure exactly.

Pre-tag protocol per §13.1 (T-7 dress rehearsal) and §13.2 (pre-announce)
must complete before the tag.

### Breaking changes

- **P0.9 dependency split** — `pip install agentao` now installs only
  the core (7 packages) needed to construct an `Agentao()` instance and
  call `chat()` against an OpenAI-compatible endpoint. CLI, web fetch,
  and Chinese tokenization become opt-in extras.

  | 0.3.x direct dep | 0.4.0 location |
  |---|---|
  | `openai` / `httpx` / `pydantic` / `pyyaml` / `mcp` / `python-dotenv` / `filelock` | core |
  | `rich` / `prompt-toolkit` / `readchar` / `pygments` | `[cli]` |
  | `beautifulsoup4` | `[web]` |
  | `jieba` | `[i18n]` |

  Migration matrix:

  | You are… | Install line |
  |---|---|
  | Embedding host (Python `from agentao import Agentao`) | `pip install agentao` |
  | CLI user (`agentao` console script) | `pip install 'agentao[cli]'` |
  | Want zero behaviour change | `pip install 'agentao[full]'` |

  Closure equivalence is enforced by `tests/test_dependency_split.py`
  against `tests/data/full_extras_baseline.txt` (122 packages frozen
  on 2026-05-01). See `docs/migration/0.3.x-to-0.4.0.md` for the full
  guide.

### Added

- **P0.10 friendly missing-dep error** — running the `agentao` CLI in
  a core-only install (no `[cli]` extra) now exits 2 with a one-line
  actionable message instead of crashing with an opaque
  `ModuleNotFoundError: rich`:

  ```
  agentao CLI requires extra packages (missing: rich).
    pip install 'agentao[cli]'   # CLI surface only
    pip install 'agentao[full]'  # 0.3.x-equivalent closure
  See docs/migration/0.3.x-to-0.4.0.md for details.
  ```

  Implementation: `agentao/cli/__init__.py` defines `entrypoint()`
  inline (no module-level imports of rich / prompt_toolkit / readchar /
  pygments) so the module load itself stays free of CLI deps; the
  first heavy import is wrapped in try/except. All other public names
  in `agentao.cli` lazy-load via PEP 562 `__getattr__`. Three new
  slow-marked tests in `tests/test_cli_missing_dep_message.py` cover
  the friendly-message path, the post-`[cli]` boot path, and the
  no-trace-leak invariant.

- **`docs/migration/0.3.x-to-0.4.0.md`** — full migration guide with
  install matrix, dependency map, common project-shape recipes, and
  a `[full]` fallback for any path the migration may have missed.

## [0.3.4] — 2026-05-01

Second release executing the **Path A roadmap** (see
`docs/design/path-a-roadmap.md`). Lands the §11 P0.4–P0.8 working set
in five logical commits. Still fully additive — no required code
change to upgrade from 0.3.3 (the only namespace move,
`agentao/display.py` → `agentao/cli/display.py`, had no in-tree
consumers).

### Added

- **P0.4 typing gate** — `agentao.harness` now ships clean under
  `mypy --strict`. New `agentao.harness.protocols` submodule re-exports
  the capability `Protocol` types (`FileSystem`, `ShellExecutor`,
  `MCPRegistry`, `MemoryStore`) plus their value shapes so embedding
  hosts have one stable import path instead of reaching into
  `agentao.capabilities.*`. CI gains a `Typing gate` job; tests cover
  the package, a downstream-shaped consumer, and `__all__` drift.
- **P0.5 lazy imports** — `from agentao import Agentao` no longer pulls
  in the OpenAI SDK, BeautifulSoup, jieba, filelock, or rich (or their
  transitive click/pygments/starlette/uvicorn closure via the MCP SDK).
  Embedded hosts pay only for what they use; the deferred libs load on
  first runtime use. Two new enforcement tests
  (`tests/test_no_cli_deps_in_core.py`, `tests/test_import_cost.py`)
  catch regressions both statically (AST walk for top-level imports
  outside `agentao/cli/`) and at runtime (`python -X importtime`).
- **P0.7 embedded-contract regression tests** — four new test files
  guard the host-facing properties most likely to silently break:
  `tests/test_no_host_logger_pollution.py` (no root-logger mutation
  through import + construction), `tests/test_multi_agentao_isolation.py`
  (two `Agentao()` instances share no state across messages, tools,
  skills, working_directory, or session_id), `tests/test_arun_events_cancel.py`
  (asyncio cancellation propagates to the chat token; events drain;
  no orphan tasks), and `tests/test_clean_install_smoke.py` (slow,
  CI-only — installs the wheel into a fresh venv and runs the README
  embed snippet). A `slow` pytest marker is registered; default runs
  skip it.
- **P0.8 replay schema v1.2 + harness→replay projection** — the
  replay JSONL format gains three harness-projected event kinds
  (`tool_lifecycle`, `subagent_lifecycle`, `permission_decision`) and
  `start_replay()` auto-wires a `HarnessReplaySink` that observes the
  agent's harness `EventStream` and projects every published event
  into the recorder, so embedded hosts have one audit artifact
  instead of two parallel streams. Each new kind's `oneOf` variant carries a typed payload
  derived from the public Pydantic model in `agentao.harness.models`,
  so a model field rename / removal surfaces as schema drift in CI.
  v1.0 / v1.1 schemas remain frozen and continue to validate older
  replays. New `agentao.harness.replay_projection` module:
  `HarnessReplaySink` (forward projection), `replay_payload_to_harness_event`
  (reverse). The typed payload schemas explicitly allow the sanitizer's
  optional projection metadata (`redaction_hits`, `redacted`,
  `redacted_fields`) so a redacted harness event still validates against
  the v1.2 schema while genuine model drift still surfaces as a property
  mismatch. New `tests/test_harness_to_replay_projection.py` covers the
  round trip, validates produced payloads against the v1.2 schema, and
  verifies a redacted payload (with a planted SECRET_PATTERN-shaped
  string) still passes schema validation. `SCHEMA_VERSION` bumps from
  `1.1` → `1.2`.
- **P0.6 five canonical embedding examples** — minimum-shape samples
  that run end-to-end against a fake LLM (no API key) under their own
  `pyproject.toml`: `examples/fastapi-background/` (per-request agent
  + asyncio background task), `examples/pytest-fixture/` (drop-in
  `agent` / `agent_with_reply` / `fake_llm_client` fixtures),
  `examples/jupyter-session/` (one agent per kernel, `events()`
  driving display, with a runnable `session.ipynb`),
  `examples/slack-bot/` (slack-bolt `app_mention` handler with
  channel-scoped `PermissionEngine` injection), and
  `examples/wechat-bot/` (polling daemon with contact-scoped
  `PermissionEngine`, transport-agnostic via a `WeChatClient`
  Protocol — inspired by `Wechat-ggGitHub/wechat-claude-code`). New CI
  `examples` job matrix runs each example's smoke suite in a fresh
  venv. `examples/README.md` gains a top-of-file table mapping each
  host shape to its directory.

### Changed

- **`agentao/display.py` moved to `agentao/cli/display.py`** — the
  `DisplayController` was used only by the CLI. Hosts that imported
  `agentao.display` directly should now import from `agentao.cli.display`
  (no in-tree consumers were affected).

## [0.3.3] — 2026-04-30

First release executing the **Path A roadmap** (see
`docs/design/path-a-roadmap.md`). Pure-additive patch. No required
code change to upgrade.

### Added

- **PEP 561 `py.typed` marker** — `agentao/py.typed` ships in wheel
  and sdist so downstream `mypy` / `pyright` consumers pick up
  Agentao's type hints instead of treating the package as untyped.

### Changed

- **README leads with embedding (`## Embed in 30 lines`)** — the
  CLI walkthrough is preserved verbatim under `## CLI Quickstart`.
  Reflects the locked Path A positioning: `agentao` is primarily a
  library to embed in Python hosts.

### Internal

- CI smoke job now asserts `py.typed` presence in the installed
  wheel and verifies bare `Agentao(...)` construction (the README
  snippet, verbatim) succeeds without env discovery or network.

## [0.3.1] — 2026-04-30

Added-only patch in the 0.3.x series. Lands the **embedded harness
contract** as the stable host-facing API surface for embedding
Agentao: typed event stream, JSON-safe permission snapshot, and
checked-in JSON schema snapshots for both events and ACP payloads.
No required code change to upgrade from 0.3.0.

### Added

- **`agentao.harness` public package** — the host-facing
  compatibility boundary for embedding Agentao. Exports the
  Pydantic event models, the `EventStream` primitive, the
  `ActivePermissions` snapshot, and schema export helpers:
  ```python
  from agentao.harness import (
      ActivePermissions,
      EventStream,
      StreamSubscribeError,
      HarnessEvent,
      ToolLifecycleEvent,
      SubagentLifecycleEvent,
      PermissionDecisionEvent,
      RFC3339UTCString,
      export_harness_event_json_schema,
      export_harness_acp_json_schema,
  )
  ```
  Internal runtime types (`AgentEvent`, `ToolExecutionResult`,
  `PermissionEngine`) are intentionally **not** re-exported — the
  harness package is the version-stable boundary. Hosts that target
  only `agentao.harness` (plus the `Agentao(...)` constructor and
  the new methods below) stay forward-compatible across releases.

- **`Agentao.events(session_id: str | None = None)`** — async
  iterator over `HarnessEvent`. No replay; bounded backpressure
  (slow consumers block the producer for matching events rather
  than dropping them). Same-session ordering is guaranteed; within
  one `tool_call_id`, `PermissionDecisionEvent` precedes
  `ToolLifecycleEvent(phase="started")`. MVP supports one stream
  consumer per `Agentao` instance; a second concurrent subscriber
  for the same `session_id` filter raises `StreamSubscribeError`.

- **`Agentao.active_permissions() -> ActivePermissions`** — JSON-safe
  snapshot of the active permission policy (`mode`, `rules`,
  `loaded_sources`). Cached; invalidated on `set_mode()` and on
  `add_loaded_source(...)` with a new label.

- **`PermissionEngine.active_permissions()` + `add_loaded_source()`**
  — engine-level snapshot getter and a host-injection point for
  provenance labels. `loaded_sources` carries stable string labels:
  `preset:<mode>`, `project:<path>`, `user:<path>`,
  `injected:<name>`. MVP intentionally does not expose per-rule
  provenance.

- **Three public lifecycle event families:**
  - `ToolLifecycleEvent` — phase ∈ `{started, completed, failed}`;
    cancellation surfaces as `phase="failed", outcome="cancelled",
    error_type=None`. Raw args / outputs are never present on the
    public payload (redacted/truncated `summary` only).
  - `SubagentLifecycleEvent` — phase ∈ `{spawned, completed, failed,
    cancelled}` (cancelled is a distinct phase here). Parent/child
    ids captured at spawn time, not inferred at completion.
  - `PermissionDecisionEvent` — fires on every decision
    (`allow` / `deny` / `prompt`), not only deny/prompt. Per-call
    `decision_id`; `matched_rule` projected when a rule fires,
    `None` on fallback semantics.

- **ACP host-facing Pydantic schema** (`agentao.acp.schema`) —
  `initialize`, `session/new`, `session/load`, `session/prompt`,
  `session/cancel`, `session/setModel`, `session/setMode`,
  `session/listModels`, `session/update` notifications,
  `request_permission`, `ask_user`, and the shared `AcpError`
  envelope as Pydantic models.

- **JSON schema snapshots** under `docs/schema/`:
  `harness.events.v1.json` (events + permissions) and
  `harness.acp.v1.json` (ACP payloads). Generated from the Pydantic
  models, byte-equality-checked by `tests/test_harness_schema.py`
  and `tests/test_acp_schema.py`. A model change that shifts the
  wire form must update the snapshot in the same PR.

- **CI fast-fail schema drift check** —
  `scripts/write_harness_schema.py --check` runs in `.github/workflows/ci.yml`
  Job 0 alongside the existing replay-schema check, so harness
  schema drift fails CI before the test matrix.

- **Runtime identity helpers** (`agentao.runtime.identity`,
  internal) — `session_id` / `turn_id` / `tool_call_id` /
  `decision_id` generation and normalization. Public events depend
  on stable id propagation; the helpers are not re-exported from
  `agentao.harness`.

- **`examples/harness_events.py`** — single-file runnable demo
  showing `agent.events()` + `agent.active_permissions()` wired
  alongside `agent.arun(...)` via `asyncio.gather`. Exits cleanly
  with instructions when `OPENAI_API_KEY` is missing.

- **`docs/api/harness.md`** + `docs/api/harness.zh.md` — public
  API reference, schema-snapshot policy, runtime identity contract,
  and event delivery semantics. **`docs/design/embedded-harness-contract.md`**
  documents the design decision and non-goals.

- **`docs/EMBEDDING.md` §7 "Host-facing harness contract"** — full
  embedding-shaped walkthrough with the `asyncio.gather` pattern;
  §8 migration guide extended with a "From 0.3.0" subsection.

- **Developer guide updates** — Appendix A.10 lists the
  `agentao.harness` exports; A.1 Methods table marks `events()` and
  `active_permissions()` as `(0.3.1+)`; Part 4.2 adds an admonition
  distinguishing `HarnessEvent` (host-stable) from `AgentEvent`
  (internal); Part 5.4 gains a "Reading the active policy from the
  host" subsection.

### Changed

- `agentao.runtime.sanitize.normalize_tool_calls` now synthesizes a
  UUID4 `tool_call_id` when the LLM provider returns a missing or
  empty `id`, using the same `runtime.identity.normalize_tool_call_id`
  helper the planner uses downstream. Strict Chat Completions APIs
  reject mismatched `tool_call_id` between assistant and tool roles;
  before this fix, a missing provider id left the assistant message
  with no id while the planner synthesized one for the tool result,
  producing a 400 on the next turn.

- `cli /status` permission-mode banner now reads from
  `agent.active_permissions()` instead of reaching into private
  `PermissionEngine` state, and displays `loaded_sources` for
  transparency. The CLI consumes the same public surface that
  external embedders see.

- ACP `session/new` and `session/load` now bind the session id onto
  the agent at session creation/load time so harness lifecycle
  events for that session carry the id the host knows it by.

### Dependencies

- **New direct dependency: `pydantic>=2`.** If your environment
  pins Pydantic v1, lift the pin before upgrading.

### Notes

- This is an **Added-only patch** — the 0.3.x series treats
  additive public surfaces as patch-eligible during pre-1.0. Strict
  SemVer consumers should read it as equivalent to a minor bump.
- Public events deliberately omit raw tool args, raw stdout/stderr,
  raw diffs, and MCP raw responses. Only redacted/truncated
  `summary` / `task_summary` / `reason` strings reach hosts.

## [0.3.0] — 2026-04-29

### Added

- **`MCPRegistry` capability protocol** (Issue #17). Embedded hosts
  can now enumerate MCP servers from any source (in-process dict,
  plugin system, dynamic discovery, remote registry) without writing
  to `.agentao/mcp.json`. Two default implementations ship in
  `agentao.mcp.registry`: `FileBackedMCPRegistry` (CLI/ACP default —
  reads `<wd>/.agentao/mcp.json` + `~/.agentao/mcp.json`,
  byte-equivalent to the pre-Protocol behavior) and
  `InMemoryMCPRegistry` (programmatic counterpart for hosts and
  tests). Re-exported from `agentao.capabilities` for symmetry with
  `FileSystem` / `MemoryStore`:
  ```python
  from agentao.capabilities import (
      MCPRegistry, FileBackedMCPRegistry, InMemoryMCPRegistry,
  )
  ```
- `Agentao(mcp_registry=...)` keyword. Mutually exclusive with
  `mcp_manager=` (which is the pre-built construction outcome — the
  registry is the config source for construction). Bare
  `Agentao(working_directory=...)` outside the factory still falls
  back to `load_mcp_config` so existing CLI-shaped scripts keep
  working.

- **`MemoryStore` capability protocol** (Issue #16). Embedded hosts
  can now swap memory backends — Redis, Postgres, in-process dict,
  remote API — without subclassing or forking `MemoryManager`. The
  `SQLiteMemoryStore` default is unchanged and remains the CLI/ACP
  backing store. Re-exported from `agentao.capabilities` for symmetry
  with `FileSystem` / `LocalFileSystem` / `ShellExecutor`:
  ```python
  from agentao.capabilities import MemoryStore, SQLiteMemoryStore
  ```
- `SQLiteMemoryStore.open(path)` — strict path-based constructor that
  creates the parent dir and propagates `OSError` / `sqlite3.Error`
  on failure. Use this for the user-scope store where a failure
  should disable the scope rather than silently degrade.
- `SQLiteMemoryStore.open_or_memory(path)` — graceful constructor
  that degrades to `:memory:` on `OSError` / `sqlite3.Error`. Use
  this for the project-scope store where a missing DB is preferable
  to a crashed agent (matches the pre-#16 ACP fault-tolerance).
  The two classmethods make the asymmetry between
  project-falls-back and user-disables explicit at every call site;
  no boolean disambiguation needed.

### Changed

- `agentao.embedding.build_from_environment()` now constructs a
  `FileBackedMCPRegistry(project_root=wd, user_root=user_root())` and
  passes it to `Agentao` as `mcp_registry=`. CLI and ACP behavior is
  unchanged because the registry resolves the same files. Hosts that
  want programmatic registration pass an explicit `mcp_registry=`
  (or any `MCPRegistry`-compatible object) to override the default.
- `agentao.memory.MemoryStore` is no longer re-exported from
  `agentao.memory` — the canonical home is `agentao.capabilities`.
  Re-exporting it from the memory package would force
  `import agentao.memory` to load all of `agentao.capabilities`,
  which after Issue #17 transitively pulls the MCP SDK and breaks
  the `tests/test_memory_decoupling.py` decoupling guarantee.

- `MemoryManager(project_store=..., user_store=...)` now accepts
  pre-built `MemoryStore` instances. Path-based construction (the
  pre-#16 shape) moves to the call site:
  ```python
  # before:
  mgr = MemoryManager(project_root=p, global_root=g)
  # after:
  mgr = MemoryManager(
      project_store=SQLiteMemoryStore.open_or_memory(p / "memory.db"),
      user_store=SQLiteMemoryStore.open(g / "memory.db") if g else None,
  )
  ```
  CLI and ACP users see no change because the factory
  (`agentao.embedding.build_from_environment()`) absorbs the new
  construction shape internally.
- The `:memory:` fallback for unwritable project DBs has moved from
  `MemoryManager.__init__` into `SQLiteMemoryStore.open_or_memory`.
  Behavior is observably identical: project store still degrades to
  `:memory:` on `OSError` / `sqlite3.OperationalError`, user store
  is still disabled with a warning on the same errors.
- `agentao.memory.MemoryManager` no longer imports `sqlite3` and has
  no filesystem knowledge. Embedded hosts that construct it directly
  with custom stores see zero disk I/O from the manager.

### Removed

- `MemoryManager.__init__(project_root=, global_root=)` — replaced by
  the explicit-store signature above. **Migration:** build the stores
  via `SQLiteMemoryStore.open_or_memory(path)` (or `.open(path)`) and
  pass them as `project_store=` / `user_store=` kwargs.
- `MemoryManager._project_root` / `MemoryManager._global_root` private
  attributes are gone. Tests / introspectors that probed these
  should read `manager.project_store.db_path` (or accept that a
  swapped backend may not expose any path at all).

### BREAKING

- **`Agentao(working_directory=)` is now required** (Issue #14, the
  hard break promised in the 0.2.16 soft-deprecation cycle).
  `working_directory` is a required keyword argument; calling
  `Agentao()` without it raises `TypeError` from Python's signature
  dispatch — there is no longer a `Path.cwd()` lazy fallback. Two
  Agentao instances created with different `working_directory`
  values report independent paths even in the same process; an
  `os.chdir` inside the host has no effect on an already-constructed
  Agentao. **Migration:** pass an explicit `Path` (preferred for
  embedded hosts), or use
  `agentao.embedding.build_from_environment()` for CLI-style
  auto-detection from the surrounding `cwd` / `.env` / `.agentao/`.
  CLI and ACP behavior is unchanged because both already route
  through the factory; the audit confirmed `os.chdir` is never
  called inside `agentao/`, so no mid-process cwd retargeting is
  affected.

### Removed

- `Agentao.__init__` no longer emits a `DeprecationWarning` when
  `working_directory` is missing — the warning was the 0.2.16
  one-cycle migration aid and is now obsolete because the argument
  is required at the signature level.
- `Agentao._explicit_working_directory` private attribute renamed
  to `Agentao._working_directory` (always populated, never
  `Optional`). External code should not read this; callers should
  use the `agent.working_directory` property.
- `Agentao.working_directory` property no longer falls back to
  `Path.cwd()`. The "lazy cwd" branch (`agent.py:376-378` in
  0.2.16) is deleted; the property now returns the frozen value
  unconditionally.

---

## [0.2.16] — 2026-04-28

Maintenance release that completes the **embedded-harness M2/M3
milestones**. `Agentao(...)` is now a pure-injection construction
surface: nothing in the constructor implicitly reads `os.environ`,
`Path.home()`, `Path.cwd()`, or `<wd>/.agentao/*.json` unless the
caller routes through `agentao.embedding.build_from_environment()`.
CLI and ACP both go through the factory, so end-user behavior is
unchanged; embedded hosts get a deterministic, side-effect-free
construction surface plus an `await agent.arun(...)` async path,
opt-in `replay` / `sandbox` / `bg_store`, and a
`DeprecationWarning` for `Agentao()` constructed without
`working_directory=` (a `TypeError` in `0.3.0`).

See [`docs/releases/v0.2.16.md`](docs/releases/v0.2.16.md) for the
release summary and maintainer checklist.

### Added

- **Embedded harness foundations** (Issues #9-#13). Agentao is now
  positioned as an embedded agent runtime that hosts can drop into
  their own apps without the implicit cwd/env/.agentao/ side effects
  the CLI relies on. Headline pieces:
  - `agentao.capabilities.FileSystem` / `ShellExecutor` protocols
    plus `LocalFileSystem` / `LocalShellExecutor` defaults. File,
    search, and shell tools route through them, so embedded hosts
    can swap in Docker exec, virtual filesystems, or remote runners
    without monkey-patching `subprocess` / `pathlib`.
  - `agentao.embedding.build_from_environment(...)` factory that
    captures every implicit `.env` / `.agentao/permissions.json` /
    `.agentao/mcp.json` / cwd read in one place. CLI and ACP route
    through it so subsystem fallbacks become dead code from their
    perspective.
  - `Agentao.__init__` accepts explicit injections for
    `llm_client`, `logger`, `memory_manager`, `skill_manager`,
    `project_instructions`, `mcp_manager`, `filesystem`, and
    `shell`. When `skill_manager` or `project_instructions` is
    injected, the auto-discovery / disk-read paths are skipped.
  - `Agentao.arun(...)` async surface that bridges sync chat
    internals through `loop.run_in_executor`. Async hosts can
    `await agent.arun(...)` without rolling their own thread
    bridge; cancellation, replay, and `max_iterations` behave
    identically across `chat()` and `arun()`.
- Sub-agent construction in `agentao/agents/tools.py` no longer
  re-reads provider env vars (`{PROVIDER}_API_KEY` / `_BASE_URL`).
  Children inherit the parent's already-resolved LLM config so a
  mid-run env mutation cannot create a credential split.

### Changed

- **`Replay` / `Sandbox` / `BackgroundTaskStore` are now opt-in**
  (Issue 9 of the embedded-harness epic). `Agentao.__init__` accepts
  three new keyword-only kwargs: `replay_config`, `sandbox_policy`,
  and `bg_store`. Each defaults to `None`, which now means *fully
  disabled* — embedded hosts that didn't ask for the feature pay
  zero cost. `agentao.embedding.build_from_environment()` constructs
  CLI defaults for all three (anchored to the session's working
  directory) and passes them explicitly, so CLI and ACP behavior is
  unchanged. Callers can pass `bg_store=None` etc. as a factory
  override to disable a feature even on the CLI path.
  - When `bg_store=None`: `check_background_agent` and
    `cancel_background_agent` are not registered, the chat loop's
    background-notification drain short-circuits, and the
    `run_in_background` field is **schema-level removed** from
    sub-agent tool definitions (not expose-then-error). The LLM
    cannot be tempted to call a disabled feature, and ACP / OpenAI
    tool catalogs do not advertise it. `/agent bg|dashboard|cancel|
    delete|logs|result` CLI subcommands short-circuit with a clear
    warning when invoked against an Agentao with `bg_store=None`.
  - When `sandbox_policy=None`: `ToolRunner` runs shell commands
    without the macOS sandbox-exec wrapper.
  - When `replay_config=None`: no `<wd>/.agentao/replay.json` is
    read at construction time; `Agentao._replay_config` falls back
    to the no-op `ReplayConfig()` default.

- **Subsystem constructors no longer fall back to `os.environ` /
  `Path.cwd()` / `Path.home()`** (Issue 5 of the embedded-harness
  epic, PR 3b). Callers must now supply explicit arguments — CLI,
  ACP, and `agentao.embedding.build_from_environment()` already do,
  so end-user behavior is unchanged. Direct constructions in
  embedded-host code or test code may break; the migration is to
  pass the previously-implicit values explicitly.
  - `LLMClient(api_key=, base_url=, model=)` are required keyword
    arguments; `temperature` defaults to `0.2` and `max_tokens` to
    `65536` in code (no more `LLM_TEMPERATURE` / `LLM_MAX_TOKENS`
    env reads). `Agentao` now also accepts a top-level
    `max_tokens=` kwarg that forwards to `LLMClient`. The factory
    is the single place that resolves `LLM_PROVIDER` /
    `*_API_KEY` / `*_BASE_URL` / `*_MODEL` / `LLM_TEMPERATURE` /
    `LLM_MAX_TOKENS`.
  - `PermissionEngine(project_root=)` is required; new keyword-only
    `user_root=` (defaults to `None`) replaces the implicit
    `Path.home() / ".agentao"` user-rules read. The factory and ACP
    `session/new` / `session/load` pass both roots explicitly.
  - `load_mcp_config(project_root=)` is required; new keyword-only
    `user_root=` (defaults to `None`) replaces the implicit
    `Path.home() / ".agentao"` user-scope read. `save_mcp_config()`
    drops `global_config: bool` in favor of an explicit
    `config_dir: Path`. CLI `/mcp add` / `/mcp remove` resolve the
    project directory through `cli.agent.working_directory`
    instead of `Path.cwd()`.
  - `Agentao.__init__` no longer defaults `MemoryManager`'s
    `global_root` to `Path.home() / ".agentao"` when no
    `memory_manager` is injected; pure-injection construction is
    now project-scope only. CLI / ACP receive the user root through
    the factory exactly as before.

### Deprecated

- `Agentao()` without `working_directory=` emits a `DeprecationWarning`
  and will become a `TypeError` in 0.3.0. Pass an explicit `Path` —
  or use `agentao.embedding.build_from_environment()` for CLI-style
  cwd / `.env` / `.agentao/` auto-discovery.

---

## [0.2.15] — 2026-04-27

Maintenance follow-up to `0.2.14`. Headline: **ACP control-plane
parity** — `session/set_model`, `session/set_mode`, and
`session/list_models` handlers land so ACP clients (Zed and others)
can drive model switching, permission-mode toggles, and capability
discovery on a live session. The same release fixes three
correctness gaps around the ACP stdio channel and streaming
`reasoning_content`.

### Added

- **`session/set_model` handler** (`agentao/acp/session_set_model.py`):
  apply `model` / `contextLength` / `maxTokens` independently on a
  running session via `agent.set_model()` and `agent.context_manager.max_tokens`
  / `agent.llm.max_tokens`. Each knob is optional; partial requests
  do not reset untouched fields. Holds the session's idle turn lock
  so an in-flight `session/prompt` cannot observe a mid-stream
  change. Conversation history and tool state are preserved.
- **`session/set_mode` handler** (`agentao/acp/session_set_mode.py`):
  toggle `PermissionEngine` mode (`default` / `acceptEdits` /
  `bypassPermissions` / `plan`) per session via
  `permission_engine.set_mode(...)`.
- **`session/list_models` handler** (`agentao/acp/session_list_models.py`):
  call `agent.list_available_models()` and cache the result on
  `AcpSessionState.last_known_models`. On provider lookup failure,
  returns the cached list plus a `warning` field instead of a
  JSON-RPC error so transient provider outages don't blank the UI.
- **Shared session-validation helper**
  (`agentao/acp/_handler_utils.py`): single point for "does this
  `session_id` exist, is it ours, did the client send a well-formed
  request" so each new handler does not re-derive the contract.
- **Streaming `reasoning_content` capture** (`agentao/llm/client.py`):
  thinking-model output arriving on the streaming `delta` is now
  forwarded the same way as the non-streaming
  `message.reasoning_content` field, so transport `THINKING` events
  no longer drop reasoning text from streaming backends.
- **Test coverage** for all of the above:
  `tests/test_acp_session_set_model.py` (484 lines, 31 cases),
  `tests/test_chat_stream_reasoning.py`,
  `tests/test_llm_handler_marker.py`,
  `tests/test_shell_stdin_devnull.py`.

### Fixed

- **Outsider log handlers preserved across `LLMClient` reconstruction**
  (`agentao/llm/client.py`): the package-root handler eviction now
  only drops handlers tagged with `_agentao_llm_file_handler=True`.
  Previously, every `LLMClient` rebuild (which `set_model` triggers,
  and which the test suite triggers repeatedly) silently evicted
  unrelated handlers — including the `AcpServer` stderr-guard handler
  that protects the ACP JSON-RPC stdout/stdin channel.
- **Shell subprocess no longer inherits parent stdin**
  (`agentao/tools/shell.py`): `Popen(..., stdin=subprocess.DEVNULL)`.
  Children that read from stdin (interactive prompts, `read`-style
  tooling) can no longer consume bytes from the ACP JSON-RPC stdin
  channel that the parent process owns.

### Packaging

- `.gitignore`: ignore rotated `*.log.*` files (avoid tracking the
  bounded-rotation artifacts introduced in `0.2.14`).
- `.github/workflows/ci.yml`: `actions/upload-artifact` pinned at v7
  (v8 does not exist; resolved on-branch in `e84fc0b`).

See [`docs/releases/v0.2.15.md`](docs/releases/v0.2.15.md) for the
release summary and maintainer checklist.

---

## [0.2.14] — 2026-04-25

Maintenance follow-up to `0.2.13` GA. Headline: **tool-call resilience
layer** for local / open-source models that drift from the OpenAI
function-call schema, plus per-session isolation polish, replay schema
drift gating, and the GitHub-Actions Node 24 prep.

### Added

- **Tool-call repair / outbound sanitize subsystem** (`agentao/runtime/`):
  three cooperating modules that sit between the LLM and the tool
  dispatcher so models like GLM, DeepSeek, Kimi and local Ollama still
  land in a runnable shape.
  - `arg_repair.py`: conservative JSON repair for malformed function
    arguments — double-encoded JSON, fenced JSON, lenient Python
    literals, trailing commas, bracket imbalance. No punctuation
    guessing.
  - `name_repair.py`: fuzzy matching that maps near-miss tool names
    (CamelCase / suffix variants) onto a registered tool when the score
    is unambiguous.
  - `sanitize.py`: outbound scrubbing — replaces lone UTF-16 surrogates
    and re-emits canonical compact JSON for repaired arguments before
    assistant / tool messages reach strict provider APIs.
  Wired into `chat_loop`, `tool_planning`, and `tool_runner`; repair is
  invisible to the model itself (only logged), preserving prompt-cache
  behaviour. Coverage: `tests/test_tool_argument_repair.py`,
  `tests/test_tool_name_repair.py`, `tests/test_outbound_sanitize.py`,
  helper `tests/support/tool_calls.py`. Documented in developer-guide
  §5.1 ("Tool-call normalization").
- **Per-instance background-task store** (commit `82edb55`): the
  background-agent registry is now per-`Agentao` instance rather than
  process-global, so concurrent ACP sessions / multi-tenant embeddings
  no longer leak handles across each other. Adds path-containment
  guards and prompt-diagnostics surfacing.
- **Replay JSON Schema export** (commit `5c85179`): `agentao/replay/`
  now ships an exported JSON Schema and a CI drift-detection job
  (`tests/test_replay_schema.py`) that fails fast when
  `agentao/replay/events.py` evolves without the schema being
  regenerated.

### Changed

- **`ToolRunner` split** (commit `f5dc034`): the monolithic
  `tool_runner` decomposed into focused `tool_planning`,
  `tool_runner` (executor), and `tool_result_formatter` modules under
  `agentao/runtime/`. Public `Agentao.chat()` contract preserved.
- **Test scaffolding** (commit `e6ccfee`): ACP test helpers extracted
  into `tests/support/` so individual test files stay focused on
  scenarios rather than fixture wiring.
- **Logging rotation**: `agentao.log` now uses
  `RotatingFileHandler(maxBytes=10_000_000, backupCount=5)` instead of
  a plain `FileHandler`, capping disk footprint at ~60 MB. The home-dir
  fallback (`~/.agentao/agentao.log`) gets the same rotation. Long-
  running sessions that previously grew the log into the hundreds of
  megabytes now self-cap.

### Fixed

- **VitePress docs at custom-domain root** (commit `875e526`): the
  developer-guide deploy now serves correctly at the `agentao.cn`
  custom-domain root rather than under a subpath.

### Packaging / CI

- `actions/upload-artifact` v4 → v7, `actions/download-artifact` v4 →
  v8, `actions/setup-python` v5 → v6 — clears the GitHub Node 24
  default cutover (2026-06-02). (`upload-artifact` has no v8 line yet;
  v7 is the current major.) `setup-uv` had already moved v6 → v7 in
  `0.2.14.dev0`.
- Version pins refreshed from `0.2.13` to `0.2.14` across `docs/ACP.md`
  and the developer-guide install / version-check examples.

---

## [0.2.13] — 2026-04-24

Promotes `0.2.13rc1` to general availability, plus one additive feature
(monorepo skill install) folded into the GA cut.

Headline: **runtime decomposition + session replay subsystem**, now with
**monorepo-aware `skill install`** layered on top. The substantive
Added / Changed breakdown — session replay (`agentao/replay/`), the
`agentao --help` / `-h` entry-point fix, and the four-module runtime
split (`runtime/`, `acp_client/manager/`, `cli/commands_ext/`, new
`prompts/` and `tooling/` packages) — is preserved below from the
`[0.2.13rc1]` soak entry.

The GA cut also carries a packaging + documentation pass: version string
aligned from `0.2.13rc1` → `0.2.13`, `docs/ACP.md` examples bumped,
Quick Start env var guidance synced with the strict provider-gating
behaviour shipped in `0.2.11`, the GitHub Pages workflow switched from
the legacy Jekyll template to the actual VitePress developer-guide
build, and lingering `0.2.10` / `0.2.11` install-pin examples in the
developer guide refreshed to the current line.

### Added

- **Monorepo skill install** (`agentao skill install owner/repo:path[@ref]`): extends the GitHub installer to pull a single skill out of a multi-skill repository — e.g. `agentao skill install anthropics/skills:pptx@main` installs only the `pptx/` subdirectory instead of rejecting the archive for missing a top-level `SKILL.md`. `SourceSpec.package_path` (`agentao/skills/sources.py`) carries the subpath; `GitHubSkillSource.resolve()` parses the `:path` segment and rejects empty / absolute / `.` / `..` components. `SkillInstaller._find_package_root()` (`agentao/skills/installer.py`) validates the subdirectory exists, is a directory, and contains `SKILL.md`; the recorded `source_ref` preserves the full `owner/repo:path@ref` string so `skill update` round-trips. CLI help on `skill install` now advertises the new form. Coverage: `tests/test_skill_installer.py` (+119 lines across success / empty-path / parent-dir-traversal / update paths), `tests/test_skill_cli.py`.
- **Session replay subsystem** (`agentao/replay/`): JSONL timeline of runtime events written to `.agentao/replays/`, with recorder, reader, redaction, retention, and sanitization. Wired through `transport/events.py` and surfaced via the new `cli/replay_commands.py` / `replay_render.py`. Feature docs: `docs/features/session-replay.md`. Tests: `tests/test_replay*`, `tests/test_replay_redact.py`.
- **`agentao --help` / `agentao -h`**: explicit `-h` / `--help` handler on the top-level CLI parser. Prints usage and exits `0` instead of silently falling through to interactive mode (the previous `add_help=False` + `parse_known_args()` combination swallowed the flag). Regression coverage: `tests/test_acp_cli_entrypoint.py::TestEntrypointArgparse::test_help_flag_prints_help_and_exits` and `test_short_help_flag_prints_help_and_exits`.

### Changed

- **Runtime decomposition** — four monolithic modules split into focused packages; public `Agentao.chat()` / `tool_runner` contract preserved (`agentao/tool_runner.py` kept as a compat shim):
  - `agentao/runtime/` (new): `chat_loop`, `tool_runner`, `model`, `llm_call`, `turn` extracted from `agent.py` (~660 net lines removed from `agent.py`).
  - `agentao/acp_client/manager.py` (2938 lines) → `manager/` package (`connection`, `core`, `helpers`, `interactions`, `lifecycle`, `recovery`, `status`, `turns`).
  - `agentao/cli/commands_ext.py` (1688 lines) → `commands_ext/` package (`acp`, `agents`, `crystallize`, `memory`).
  - `agentao/cli/app.py` shrunk by ~800 lines; new CLI modules `input_loop`, `ui`, `acp_inbox`.
  - `agentao/prompts/` (new): `builder` + `sections` + `helpers` for system-prompt composition. `agent._build_system_prompt()` and `agent._load_project_instructions()` retained as thin facades so existing tests and external patches keep working.
  - `agentao/tooling/` (new): `registry`, `agent_tools`, `mcp_tools`.
- **Docs**: `docs/ACP.md` version examples bumped from `0.2.10` to `0.2.13`. Developer-guide `part-2/2-constructor-reference.md`, `part-5/5-memory.md`, `part-5/6-system-prompt.md` (en + zh mirrors) updated to reference the new `prompts/builder.py` location for system-prompt composition.

### Packaging / Release (GA)

- Align package version, changelog, release notes, and publish workflow usage to the final `0.2.13` release line.
- README / `docs/QUICKSTART.md` Quick Start: document all three required provider variables (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`) up front. Previously only `OPENAI_API_KEY` was shown, contradicting the strict-provider-gating behaviour introduced in `0.2.11` — the single-key snippet would raise `ValueError` at startup.
- `.github/workflows/jekyll-gh-pages.yml` replaced by a VitePress build + deploy pipeline pointed at `developer-guide/`. The Jekyll template was a repo-init leftover; the actual docs site is VitePress, so the previous workflow was deploying nothing useful.
- Developer-guide install-pin / version-check examples refreshed from `0.2.10` / `0.2.11` to `0.2.13` in `part-1/5-requirements.md`, `part-2/1-install-import.md`, `part-3/2-agentao-as-server.md` (JSON response example), and `part-3/5-zed-ide-integration.md` (en + zh mirrors). Historical statements ("Since v0.2.10…", "Pre-0.2.10 Agentao used…") are kept — they describe when a surface was introduced, not the current pin.

### Documentation

- Add `docs/releases/v0.2.13.md`.
- `docs/SKILLS_GUIDE.md` and `developer-guide/en|zh/part-5/2-skills.md` document the new monorepo `skill install` form with worked examples against `anthropics/skills` (pptx, docx, xlsx, pdf, doc-coauthoring).

---

## [0.2.12] — 2026-04-22

### Added

- **Headless runtime v1** (`docs/features/headless-runtime.md`): operator-facing contract for `ACPManager` as a non-interactive embedding target — public entry points (`prompt_once`, `send_prompt`), single-active-turn concurrency pinned to `AcpErrorCode.SERVER_BUSY`, typed status snapshot. `send_prompt_nonblocking` family is classified **internal / unstable** and removed from the embedding contract.
- **`ServerStatus` dataclass** (`agentao/acp_client/models.py`, re-exported from `agentao.acp_client`): frozen v1 shape with `server`, `state`, `pid`, `has_active_turn`.
- **`examples/headless_worker.py`**: runnable headless smoke consumer. Spins up an inline mock ACP server, exercises success / non-interactive error / cancel paths, and prints the typed snapshot after each.
- **`tests/test_headless_runtime.py`**: baseline smoke tests pinning the Week 1 contract — typed snapshot shape, `has_active_turn` derivation, `SERVER_BUSY` on concurrent submit, cancel-then-continue, non-interactive reject non-pollution, timeout recovery, session reuse.
- **Headless runtime Week 2 diagnostics** (`docs/features/headless-runtime.md` §3-§4, additive on `ServerStatus`): `active_session_id`, `last_error`, `last_error_at` (tz-aware UTC `datetime` assigned at *store time* inside the manager, not raise time), `inbox_pending`, `interaction_pending` (singular, replaces the pre-v1 `interactions_pending` alias), `config_warnings` (per-server list; Week 3 will populate on legacy config).
- **`ACPManager.readiness(name)` / `.is_ready(name)`**: typed 4-valued classifier (`"ready" | "busy" | "failed" | "not_ready"`) over the combination of handle state and the active-turn slot. Consumers that only need a gating signal should prefer this over string-matching on `state`.
- **`ACPManager.reset_last_error(name)`**: explicit clear for the sticky `last_error` / `last_error_at` surface. A new error overwrites automatically; this method is only needed when the host wants to drop the stored error without waiting for a new one.
- **State-vs-error contract**: the recorded-error surface is diagnostic, not gating — `state` is the authoritative readiness signal, `last_error` is history. `SERVER_BUSY` and `SERVER_NOT_FOUND` are intentionally excluded from the store so fail-fast retries do not overwrite real failures. Pinned by tests (`tests/test_headless_runtime.py::TestLastErrorStore`) including a `datetime`-patch proof that the timestamp is taken inside `_record_last_error`, not pre-computed.
- **`InteractionPolicy` dataclass** (Week 3, Issue 11) re-exported from `agentao.acp_client`. Minimal single-dimension policy model over the non-interactive interaction decision: `InteractionPolicy(mode="reject_all" | "accept_all")`. No other knobs — additional dimensions belong on a new options object.
- **`interaction_policy=` per-call override** on `ACPManager.send_prompt` and `ACPManager.prompt_once`. Accepts `InteractionPolicy` or the bare strings `"reject_all"` / `"accept_all"`. Precedence: per-call override > server default (`nonInteractivePolicy`). `None` falls back to the server default. `send_prompt_nonblocking` is **internal / unstable** per the Week 1 decision and deliberately does **not** accept this kwarg — the Week 3 policy surface is `send_prompt` + `prompt_once` only.
- **Headless runtime Week 4 lifecycle & recovery** (`docs/features/headless-runtime.md` §7). Pins the deterministic release order on every failure path (pending-slot drop → turn-slot clear → lock release → `last_error` record) and introduces the client/process-death classifier.
- **`classify_process_death` pure classifier** exported from `agentao.acp_client`. Maps `(exit_code, signaled, during_active_turn, restart_count, max_recoverable_restarts, handshake_fail_streak)` to `"recoverable"` / `"fatal"` per the Issue 16 decision matrix. Testable in isolation; the manager calls it inside `ensure_connected` to decide whether to lazy-rebuild or flip the server into the sticky fatal state.
- **`ACPManager.is_fatal(name)` / `.restart_count(name)`** surfaces for the recovery state. `is_fatal(name)` is sticky — cleared only by an explicit `restart_server` or `start_server` call (operator action required).
- **`AcpServerConfig.max_recoverable_restarts`** (JSON: `maxRecoverableRestarts`, default 3). Caps consecutive auto-recoveries on recoverable idle non-zero exits before the manager flips the server to fatal. Active-turn deaths bypass the cap; each is always allowed at least one rebuild attempt.
- **Daemon-style regression suite** (`tests/test_headless_runtime.py::TestDaemonRegression`): long session reuse, reject-then-continue, cancel-then-continue, timeout-then-continue, and process-death recovery (both recoverable and fatal). Pinned against the mock ACP server from `test_acp_client_embedding` so the scenarios stay executable in CI.
- **`/crystallize` evidence + feedback loop**: `SkillEvidence` and `SkillFeedbackEntry` dataclasses (`agentao/skills/drafts.py`) extend `SkillDraft` with structured tool-activity grounding (`user_goals`, `assistant_conclusions`, `tool_calls`, `tool_results`, `key_files`, `workflow_steps`, `outcome_signals`), a `feedback_history` rewrite log, and `open_questions`. Drafts persist forward- and backward-compatible JSON — legacy payloads load with empty evidence/history.
- **`collect_crystallize_evidence` / `render_crystallize_context`** (`agentao/cli/commands_ext.py`): pull structured evidence from the live `AgentaoCLI` message history (tool calls + tool results, not just narrated text) and render it as the `# Structured evidence` block consumed by `/crystallize suggest|refine|feedback`.
- **`feedback_prompt` + `FEEDBACK_SYSTEM_PROMPT`** (`agentao/memory/crystallizer.py`): drive user-feedback-driven draft rewrites; `suggest_prompt()` and `refine_prompt()` gained an optional `evidence_text=` parameter so all three prompts share the same evidence grounding. Drafts grounded in tool activity, not just raw transcript.
- **`append_skill_feedback` + `summarize_draft_status`** (`agentao/skills/drafts.py`): durable feedback log and lightweight status view for `/crystallize status`.
- **`tests/test_skill_crystallize_enhancement.py`**: 15 tests covering the new dataclass schema, persistence round-trip, backward-compatible load of legacy drafts, prompt-builder evidence injection, and feedback append/history rendering.
- **Plan doc** `docs/implementation/SKILL_CRYSTALLIZE_ENHANCEMENT_PLAN.md`: design rationale and API surface for the three-problem scope (structured evidence in drafts, user feedback loop, `/help` discoverability).

### Changed

- **Breaking: `ACPManager.get_status()` now returns `list[ServerStatus]`** instead of `list[dict]`. This is a deliberate, once-for-all API convergence — there is no `get_status_typed()` side channel and no permanent dict alias. Migration table and field semantics are in `docs/features/headless-runtime.md#3-status-snapshot-v1--v2`.
  - The legacy `"name"` dict key is renamed to `ServerStatus.server`.
  - Week-1 core fields are `server` / `state` / `pid` / `has_active_turn`. Week 2 adds `active_session_id`, `last_error`, `last_error_at`, `inbox_pending`, `interaction_pending`, `config_warnings` **additively** — the Week 1 shape is unchanged.
  - `has_active_turn` is derived from the manager's active turn slot (not handle state), so it stays `True` across the in-flight interaction phase of non-interactive turns.
  - `last_error` is sticky across successful turns by design (so once-per-minute pollers still see the last-known failure); clear explicitly via `reset_last_error(name)` or wait for a new error to overwrite.
- CLI `/acp list` / session status readouts and the embedding developer-guide pages (part-1 mode 3, part-3 reverse-ACP, appendix A / D / F / G, zh + en mirrors) are migrated to the typed contract.
- **Breaking: `nonInteractivePolicy` bare-string config form is removed** (Week 3, Issue 12). `.agentao/acp.json` must now use the structured object form — `"nonInteractivePolicy": {"mode": "reject_all" | "accept_all"}`. The legacy strings `"reject_all"` / `"accept_all"` as a bare value raise `AcpConfigError` **at config-load time** (`AcpClientConfig.from_dict` / `load_acp_client_config`). There is no silent upgrade and no deferred runtime failure — a drifted config cannot slip through to `send_prompt` execution. Migration: see [developer-guide appendix E.7](./developer-guide/en/appendix/e-migration.md#e7-headless-runtime--noninteractivepolicy-shape-change-week-3) (and the zh mirror).
- `AcpServerConfig.non_interactive_policy` is now typed as `InteractionPolicy` (previously `str`). Downstream callers that read `server_cfg.non_interactive_policy` should read `.mode` instead.

---

## [0.2.11] — 2026-04-19

### Added

- **Multi-provider `web_search`**: `WebSearchTool` now reads `BOCHA_API_KEY` once at startup. When present, all web searches route through Bocha Search API (`POST https://api.bochaai.com/v1/web-search`, Bearer auth, structured JSON results). When absent, the tool falls back to DuckDuckGo — no configuration change required for existing users.

### Changed

- **Strict LLM provider gating** (breaking): `LLMClient.__init__` now raises `ValueError` at startup if any of `{PROVIDER}_API_KEY`, `{PROVIDER}_BASE_URL`, or `{PROVIDER}_MODEL` is absent and was not supplied via constructor args. Previously a missing model silently fell back to a hardcoded default. Migrate: add all three to `.env`.
- `/provider` listing now only shows providers that have all three of `{PROVIDER}_API_KEY`, `{PROVIDER}_BASE_URL`, and `{PROVIDER}_MODEL` set. Switching to an incomplete provider also errors with a clear message.
- Removed `_PROVIDER_DEFAULT_MODELS` internal dict from `LLMClient`.
- `gpt-5.4` added to context-manager tokenizer mapping (`o200k_base` encoding, same as `gpt-4o` family).
- Default model in all examples, templates, and documentation updated from `gpt-4o` → `gpt-5.4`.

### Migration

```bash
# Before (silently used default model fallback):
OPENAI_API_KEY=sk-...

# After (all three required):
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5.4        # or whichever model you target
```

---

## [0.2.10] — 2026-04-15

Promotes the `0.2.10` line to general availability.

The feature set — ACP embedding facade and `/crystallize refine` — is
the same as the `[0.2.10-rc2]` entry below; this GA release is the first
cut that actually ships the feature code. Both `v0.2.10-rc1` and
`v0.2.10-rc2` were tagged against commits that carried only the version
bump and release notes, so the rc tarballs on TestPyPI are effectively
empty. **Do not depend on `v0.2.10-rc1` or `v0.2.10-rc2`** — upgrade
directly from `0.2.9` to `0.2.10`.

### Packaging / Release

- Align package version, changelog, release notes, and publish workflow
  usage to the final `0.2.10` GA line
- Bundle the ACP embedding facade, `/crystallize refine`, skill draft
  helpers, and the associated tests/docs into the GA commit so the
  sdist/wheel actually contains the advertised feature set

### Documentation

- Add `docs/releases/v0.2.10.md`
- Update `docs/ACP.md` version examples from `0.2.9` to `0.2.10`

## [0.2.10-rc2] — 2026-04-15

Re-cut of `0.2.10-rc1`. `rc1` failed the CI tag-vs-package version
consistency check because the `v0.2.10-rc1` tag was pushed against a
commit where `agentao/__init__.py` still reported `0.2.9`. `rc2` carries
the identical feature set with the version string aligned to the tag.

> **Note:** Neither `rc1` nor `rc2` actually shipped the feature code
> described below — both tags pointed at docs-only commits. The feature
> set first ships in the GA `0.2.10` release above.

Prerelease focused on two initiatives: promoting `agentao.acp_client` as a
stable **embedding facade for non-interactive runtimes**, and adding an
explicit **`/crystallize refine` stage** to the skill-crystallization flow.

### Added

- **ACP embedding facade** (`agentao/acp_client/`): non-interactive
  `send_prompt(..., interactive=False)` plus a one-shot `prompt_once(...)`
  entry point for daemon/workflow runtimes. Ephemeral clients created by
  `prompt_once` are tracked separately from durable `_clients` and do not
  appear in `get_status()`.
- **Structured client-side error taxonomy** (`AcpErrorCode`, `AcpClientError`,
  `AcpRpcError`, `AcpInteractionRequiredError`): embedding callers can branch
  on failure category without string matching. `AcpRpcError` preserves the
  raw JSON-RPC numeric `rpc_code` alongside the structured classification.
- **`HANDSHAKE_FAIL` classification**: `initialize` / `session/new` failures
  are re-labelled on both the `connect_server()` path and the ephemeral
  `prompt_once()` path, including RPC errors, so embedders can distinguish
  startup failures from in-session RPC failures uniformly.
- **Per-call `cwd` and `mcp_servers` session reuse**: a mismatch on either
  field triggers a fresh session; otherwise sessions are reused per named
  server under a strict single-active-turn contract.
- **`/crystallize refine`** (`agentao/cli/commands_ext.py`,
  `agentao/skills/drafts.py`): three-stage workflow
  `suggest -> refine -> create`, where `refine` re-runs the draft through
  the bundled `skill-creator` guidance. `suggest` now persists drafts under
  `.agentao/skill-drafts/` so `refine`/`create` can pick them up across
  turns.
- **Skill draft helpers** (`agentao/skills/drafts.py`): `new_draft`,
  `save_skill_draft`, `load_skill_draft`, `clear_skill_draft` with
  session-scoped paths and graceful handling of missing or malformed state.

### Fixed

- **`stop_all()` closes ephemeral clients** — in-flight `prompt_once()`
  callers previously blocked until their request timeout when the manager
  was shut down mid-call; ephemeral slots now receive the synthetic
  transport-closed signal alongside durable clients.
- **`load_skill_draft()` tolerates non-object JSON** — a corrupted draft
  file containing `[]` or a bare string no longer crashes
  `/crystallize status|refine|create`; the helper now returns `None` for
  any non-dict payload.
- **`/crystallize suggest` degrades when the draft directory is not
  writable** — the generated `SKILL.md` is still displayed, the save
  failure is surfaced as a warning, and the user is pointed at
  `/crystallize create [name]` instead of aborting the command.

### Tests

- New `tests/test_acp_client_embedding.py` covering non-interactive
  `send_prompt`, `prompt_once`, session reuse, ephemeral lifecycle,
  cancellation precedence, and handshake error classification.
- New `tests/test_skill_drafts.py` covering draft persistence, session
  scoping, corrupt-file tolerance, and path selection.
- Updated `tests/test_acp_client_cli.py`, `tests/test_acp_client_jsonrpc.py`,
  `tests/test_crystallizer.py`, `tests/test_reliability_prompt.py` for the
  new surfaces.

### Documentation

- Add `docs/features/acp-embedding.md` (embedding facade overview)
- Add `docs/implementation/ACP_EMBEDDING_IMPLEMENTATION_PLAN.md`
- Add `docs/implementation/SKILL_CRYSTALLIZE_REFINEMENT_PLAN.md`
- Add `docs/kanban-acp-embedded-client-issue.md` (design parent doc)
- Add `docs/releases/v0.2.10-rc2.md`

## [0.2.9] — 2026-04-11

Small GA follow-up to `0.2.8` with three independently useful fixes on top
of the ACP client subsystem and the default-model rollout.

### Added

- **Explicit `@server` routing for the ACP client** (`agentao/acp_client/router.py`,
  `agentao/cli/app.py::_try_acp_explicit_route`) — `@server-name <task>`,
  `server-name: <task>`, and `让 / 请 server-name <task>` forms route
  deterministically to the named ACP server from the main CLI input. Longest-first
  name matching handles overlapping names (`qa` vs `qa.bot`). High-confidence shapes
  (`@…`, `让 …`, `请 …`) consume the turn when config is unavailable so delegation
  intent never silently falls back to the main agent; ambiguous colon-prefix shapes
  fall through so `Note:` / `url:` prose is never hijacked. ACP config is re-stat'd
  by mtime each attempt, so new/renamed servers are picked up without a CLI restart.
- **`$VAR` / `${VAR}` expansion in `AcpServerConfig.env`** — API keys and tokens
  can live in `.env` or the shell environment instead of being pasted into
  `.agentao/acp.json`.

### Fixed

- **ACP stdio is now forced to UTF-8 with `errors="strict"` before the server starts**
  (`agentao/acp/__main__.py`). Non-UTF-8 default encodings silently corrupt the
  JSON-RPC stream; the entry point now reconfigures stdin/stdout/stderr, verifies the
  result, and exits with a diagnostic on stderr if the streams cannot be made safe.
- **Default-model messaging realigned with the runtime** across the init wizard,
  `.env.example`, `README.md`, and `README.zh.md`. `LLMClient._PROVIDER_DEFAULT_MODELS`
  is the canonical source; surfaces previously suggested `gpt-5.4` / `gemini-2.0-flash`
  / `claude-opus-4-6`, contradicting the actual defaults (`gpt-5.4`,
  `gemini-flash-latest`, `claude-sonnet-4-6`, `deepseek-chat`). Unknown-provider
  fallback in `LLMClient.__init__` also returns `gpt-5.4` now instead of `gpt-5.4`.

### Documentation

- Add `docs/releases/v0.2.9.md`
- Update `docs/ACP.md` version examples from `0.2.8` to `0.2.9`

## [0.2.8] — 2026-04-11

Promotes `0.2.8-rc1` to general availability.

The substantive Added / Changed / Tests breakdown for the ACP client and CLI
refactor remains in the `[0.2.8-rc1]` entry below. The final 0.2.8 release
locks down release-facing metadata and documentation so the package version,
Git tag, release notes, and maintainer workflow all agree on the GA path.

### Packaging / Release

- Align package version, changelog, release notes, and publish workflow usage
  to the final `0.2.8` release line
- Document a maintainer smoke path (`uv run python -m pytest tests/`,
  `uv build`, `uv run twine check dist/*`) that runs tests, builds
  sdist/wheel, and validates metadata
- Add `build` and `twine` to the dev dependency group so release checks can be
  reproduced from a local source checkout

### Documentation

- Update `.env.example`, quickstart guides, and README snippets to reflect the
  current default model line (`gpt-5.4` / `gpt-5.4` examples) instead of stale
  `gpt-4-turbo-preview` examples
- Add final release notes at `docs/releases/v0.2.8.md`
- Update `docs/ACP.md` version examples from `0.2.8-rc1` to `0.2.8`

## [0.2.8-rc1] — 2026-04-11

Headline: **ACP Client for project-local server management** — Agentao can
now act as an ACP client, connecting to and managing external ACP-compatible
agent processes configured per-project. The old monolithic CLI is refactored
into a modular `agentao/cli/` package for maintainability.

Release intent: **prerelease / TestPyPI path**. Use tag `v0.2.8-rc1` and a
GitHub pre-release so `.github/workflows/publish-testpypi.yml` runs instead
of the full PyPI publish workflow.

### Added

- **ACP client subsystem** (`agentao/acp_client/`, ~2 400 lines)
  - `ACPManager` — top-level façade: lazy init on first `/acp` command,
    config loading, server lifecycle orchestration
  - `ACPClient` — per-server JSON-RPC 2.0 client over stdio with NDJSON
    framing; handles `initialize` + `session/new` handshake, `session/prompt`,
    `session/cancel`, and notification dispatch
  - `ACPProcessHandle` — subprocess lifecycle (spawn, graceful shutdown,
    stderr ring buffer for diagnostics)
  - `Inbox` — bounded message queue with idle-point flush; messages from
    ACP servers stay separate from the main conversation context
  - `InteractionRegistry` — tracks pending permission and input requests
    from servers; supports `approve`, `reject`, and `reply` resolution
  - `AcpServerConfig` / `AcpClientConfig` models with validation
  - `load_acp_client_config()` — reads `.agentao/acp.json` (project-only;
    no global config)
  - Rich-based `render.py` for CLI output formatting
- **`/acp` CLI commands**: `list`, `start`, `stop`, `restart`, `send`,
  `cancel`, `status`, `logs`, `approve`, `reject`, `reply`
- **ACP extension method `_agentao.cn/ask_user`** — advertised in
  `initialize` response `extensions` array; enables ACP servers to request
  free-form text input from the user. `ACPTransport.ask_user()` implemented
  with full error handling (all failures resolve to a sentinel, never crash
  the turn)
- **`ACPTransport.on_max_iterations()`** — conservative default: stops the
  turn when max iterations reached (no interactive menu in ACP mode)
- **Domain-based permission rules for `web_fetch`** in `PermissionEngine`:
  - `_extract_domain()` — URL parsing with missing-scheme handling
  - `_domain_matches()` — supports leading-dot suffix matching
    (`.github.com` matches `github.com` and `api.github.com`) and exact
    matching (`r.jina.ai`)
  - Preset allowlist: `.github.com`, `.docs.python.org`, `.wikipedia.org`,
    `r.jina.ai`, `.pypi.org`, `.readthedocs.io` → auto-allow
  - Preset blocklist: `localhost`, `127.0.0.1`, `0.0.0.0`,
    `169.254.169.254`, `.internal`, `.local`, `::1` → auto-deny
  - Domain rules displayed in `/permissions` output
- **`docs/features/acp-client.md`** — full configuration reference,
  lifecycle, interaction bridge protocol, diagnostics, and troubleshooting

### Changed

- **CLI refactored from monolith to package** — the old single-file CLI (3 246
  lines) replaced by `agentao/cli/` package (~3 800 lines across 12
  modules): `app.py`, `commands.py`, `commands_ext.py`, `entrypoints.py`,
  `session.py`, `subcommands.py`, `transport.py`, `_globals.py`, `_utils.py`
- `PermissionEngine.evaluate()` now checks `domain` rules before falling
  through to regex-based `args` matching
- `PermissionEngine.explain()` renders domain allowlist/blocklist in the
  rule detail output
- README.md / README.zh.md updated with ACP Client section

### Tests

- **7 new test files** (~2 300 lines): `test_acp_client_cli.py`,
  `test_acp_client_config.py`, `test_acp_client_inbox.py`,
  `test_acp_client_jsonrpc.py`, `test_acp_client_process.py`,
  `test_acp_client_prompt.py`, `test_acp_ask_user.py`
- Existing CLI tests updated for the `agentao.cli` → `agentao.cli.app`
  import path change

---

## [0.2.7] — 2026-04-09

Headline: **Agent Client Protocol (ACP)** — Agentao can now be driven as
a headless JSON-RPC agent runtime by ACP-compatible clients (e.g. Zed).
The entire ACP wire protocol, per-session working directory isolation,
session-scoped MCP injection, and multi-session lifecycle are new.

The retriever's CJK tokenization is upgraded from character bigrams to
jieba word segmentation, and the memory subsystem's startup resilience is
hardened so restricted / read-only environments no longer crash the
constructor.

### Added

- **ACP stdio JSON-RPC server** (`agentao/acp/`, ~3 500 lines)
  - Launch with `agentao --acp --stdio` or `python -m agentao --acp --stdio`
  - Methods: `initialize`, `session/new`, `session/prompt`,
    `session/cancel`, `session/load`
  - Server→client `session/request_permission` with `allow_once` /
    `allow_always` / `reject_once` / `reject_always` options
  - Stdout guard: `sys.stdout` redirected to stderr on ACP entry so
    stray `print()` anywhere in the process never corrupts the NDJSON
    wire; JSON-RPC responses use a captured handle to the real stdout
  - Capability advertisement: `text` + `resource_link` content blocks,
    stdio + sse MCP transport, no `fs.*`/`terminal.*` host proxying
  - `AcpServer`, `AcpSessionManager`, `AcpSessionState`, `ACPTransport`
    (maps Agentao transport events to ACP `session/update` notifications)
- **`python -m agentao` module entry point** (`agentao/__main__.py`) so
  the CLI works even when the console script is not on PATH
- **Per-session working directory isolation** (Issue 05)
  - `Agentao(working_directory=Path)` freezes memory, permissions, MCP
    config, AGENTAO.md loading, system-prompt rendering, file tools, and
    shell tool against that path
  - `Agentao.working_directory` property: `None` → lazy `Path.cwd()`
    (CLI compatibility); `Path` → frozen resolved path (ACP sessions)
  - `Tool._resolve_path()` / `_resolve_directory()` helpers on the base
    class; all file, search, and shell tools use them
  - `PermissionEngine(project_root=...)`, `load_mcp_config(project_root=...)`,
    `SkillManager(working_directory=...)`, `save_session(project_root=...)`,
    `load_session(project_root=...)`, `list_sessions(project_root=...)`,
    `delete_session(project_root=...)`, `delete_all_sessions(project_root=...)`
    all accept an explicit project root; `None` falls back to `Path.cwd()`
- **Session-scoped MCP server injection** (Issue 11)
  - `Agentao(extra_mcp_servers=...)` merges in-memory configs on top of
    file-loaded `.agentao/mcp.json` (name-level override, no disk writes)
  - ACP `session/new` `mcpServers` wire field → translated by
    `agentao.acp.mcp_translate.translate_acp_mcp_servers()`
- **LLM log file fallback** — `LLMClient._build_file_handler()` resolves
  `agentao.log` to an absolute path anchored to the working directory;
  when the target is unwritable (ACP launches with cwd `/` on macOS),
  falls back to `<home>/.agentao/agentao.log`
- **jieba word segmentation for CJK retrieval** — `MemoryRetriever` now
  segments Chinese/Japanese/Korean text with jieba instead of character
  bigrams. `"版本管理"` → `{"版本", "管理"}` (was `{"版本", "本管", "管理"}`).
  Single-character CJK tokens filtered out (matches the Latin `len > 1`
  rule). Custom dictionary: `<home>/.agentao/userdict.txt` (lazy-loaded on
  first recall). New dependency: `jieba>=0.42.1`
- **Inverted index in `MemoryRetriever`** — `write_version`-gated
  token → record-ID map so recall scores only records sharing at least
  one query token; avoids full-scan as memory store grows

### Changed

- `MemoryManager.__init__` widened exception handling from `OSError` to
  `(OSError, sqlite3.Error)` on both project-store and user-store init
  branches. The previous `OSError`-only catch missed
  `sqlite3.OperationalError: unable to open database file` raised when
  the directory exists but the DB cannot be opened/WAL-journaled,
  crashing `Agentao()` in restricted environments and killing every ACP
  session spawn. Each fallback now logs a `WARNING` (was silent)
- `_cjk_bigrams()` replaced by `_cjk_segment()` (jieba-backed); bigram
  noise eliminated from CJK recall scoring
- CLI `entrypoint()` extended: `--acp` and `--stdio` flags; `--acp`
  short-circuits to `run_acp_mode()` before any Rich/interactive setup;
  `--stdio` without `--acp` exits with error code 2
- `SkillManager` now resolves project-scoped skill dirs and config files
  from an explicit `working_directory` at construction time; two ACP
  sessions in different repos see independent skill sets and
  disabled-skill state

### Fixed

- **`Agentao()` crash in restricted / non-writable environments** —
  `sqlite3.OperationalError` from the user-scope memory DB now triggers
  the fallback path (user store disabled, project store in-memory) instead
  of propagating as an unhandled exception. Root cause of ACP subprocess
  smoke-test failures and plain `Agentao(api_key='x')` startup failure
  when `<home>/.agentao/memory.db` is unwritable

### Tests

- **336 new ACP tests** across `test_acp_initialize.py`,
  `test_acp_session_new.py`, `test_acp_session_prompt.py`,
  `test_acp_session_cancel.py`, `test_acp_session_load.py`,
  `test_acp_session_manager.py`, `test_acp_protocol.py`,
  `test_acp_mcp_injection.py`, `test_acp_multi_session.py`,
  `test_acp_request_permission.py`, `test_acp_transport.py`,
  `test_acp_cli_entrypoint.py`
- **Per-session cwd isolation tests** in `test_per_session_cwd.py`:
  tool path resolution, memory DB binding, skill isolation, LLM log
  anchoring, ACP factory wiring, and two sqlite-fault-injection
  regressions for the restricted-env crash
- **Memory init fallback regressions** in `test_memory_manager.py`:
  `test_project_store_sqlite_error_falls_back_to_memory`,
  `test_user_store_sqlite_error_leaves_user_store_none`
- Suite total: **1035 tests** (1034 passing, 1 skipped), up from 657

---

## [0.2.6] — 2026-04-09

Promotes 0.2.6-rc1 to general availability. The substantive Added /
Changed / Removed / Fixed / Tests breakdown of the memory subsystem rewrite
lives in the `[0.2.6-rc1]` entry below — that is the content of this
release. The only commits between rc1 and final are CI-only workflow
hardening so the publish pipeline actually succeeds.

### Packaging / CI

- Bump `actions/checkout@v4` → `@v5` and `astral-sh/setup-uv@v5` → `@v6`
  so CI workflows run on the Node.js 24 runner. GitHub deprecated
  Node.js 20 actions on 2025-09-19; bumping to the next major of each
  clears the deprecation warning on every run
- Drop the invalid `--repository` flag from `twine check` in
  `publish-testpypi.yml`. `--repository` is valid for `twine upload` but
  not `twine check`, which only validates dist metadata locally — the
  flag was causing the TestPyPI workflow to exit with code 2 on every
  RC attempt
- Activate the venv with `source .publish-venv/bin/activate` (and
  `.publish-testpypi-venv`) before the publish smoke step instead of
  invoking `.venv/bin/python` directly. Direct-invoke does not put the
  venv's `bin/` on `PATH`, so `shutil.which('agentao')` returned `None`
  and failed the smoke test even though the entry-point script was
  installed correctly. Applied to both `publish.yml` and
  `publish-testpypi.yml`

### Notes

**No library-code changes between 0.2.6-rc1 and 0.2.6.** Memory
subsystem, prompt injection, crystallization pipeline, retriever
scoring, and CLI surface are byte-identical to the RC; only
`.github/workflows/*.yml` was touched.

---

## [0.2.6-rc1] — 2026-04-09

Headline: complete memory subsystem rewrite. SQLite replaces the old JSON
files; persistent memories, session summaries, and dynamic recall candidates
are now distinct, structured data types; conservative rule-based
crystallization sediments user statements into a review queue rather than
silently writing.

### Added

- **SQLite-backed memory subsystem** — `agentao/memory/`
  - Two stores: `.agentao/memory.db` (project) and `<home>/.agentao/memory.db` (user)
  - Schema v3 with `memories`, `session_summaries`, `memory_review_queue`,
    `memory_events`, `schema_meta`
  - Three data types modeled separately: persistent `MemoryRecord`,
    `SessionSummaryRecord`, in-memory `RecallCandidate`
- **Two prompt-injection blocks** built per turn
  - `<memory-stable>`: durable facts (`get_stable_entries()` policy:
    user-scope always, structural types always, project_fact/note capped at
    3 most-recent) plus a pre-reserved cross-session summary tail
  - `<memory-context>`: top-k recall candidates scored against the current
    user query
- **Cross-session summary recall** — `MemoryManager.get_cross_session_tail()`
  surfaces summaries from prior sessions through `<memory-stable>` so
  conversation continuity survives a restart, not only an in-process
  compaction
- **`MemoryRetriever` with five-factor scoring**
  - tag match (4.0, dampened to 1.5/2.5 for ≤2-token queries to prevent
    single-tag over-recall)
  - title Jaccard (3.0)
  - tokenized keyword match (2.0; compound keywords like `agent.py` are
    sub-tokenized so they match a query token `agent`)
  - content snippet match on first 500 chars (1.0)
  - filepath hint from context (2.0)
  - recency / staleness modifiers
  - CJK bigram tokenization, light Latin normalization (plurals, version
    prefixes), Latin↔CJK boundary splitting, dynamic char budget,
    `exclude_ids` parameter so dynamic recall never duplicates stable entries
- **Conservative rule-based crystallization with review queue**
  - `MemoryCrystallizer` rule patterns extract preference / constraint /
    decision / workflow only, in English and Chinese
  - Extraction runs on **raw user messages** (`extract_from_user_messages`),
    never on LLM-generated summary prose — assistant narration that happens
    to contain pattern words can never trigger a false match
  - Candidates land in `memory_review_queue` with `source="crystallized"`,
    not silently into live memories
  - Repetition aggregation: same `(scope, key)` matched in multiple user
    messages folds into one row with incremented `occurrences`; confidence
    is auto-raised to `inferred` at 2+ hits
  - Auto-trigger inside `ContextManager.compress_messages()` (Step 4b),
    against the about-to-be-compacted user-message window
- **CLI memory commands**: `/memory list/search/tag/delete/clear/user/project/session/status/crystallize/review`
  including `/memory review approve <id>` and `/memory review reject <id>`
- **Recall observability**: `/memory status` reports retrieval hits, recall
  errors, last error message, stable block size, and latest session summary
  size
- **`clear_all_session_summaries()`** for hard reset across all sessions
- **Memory subsystem decoupled from the LLM stack** —
  `agentao/__init__.py` uses PEP 562 `__getattr__` for lazy `Agentao` /
  `SkillManager` resolution, so `import agentao.memory` no longer pulls
  `openai`, `mcp`, `agentao.tools.*`, or `agentao.llm.*`. Cold import:
  **334 ms → 35 ms** (~10×); zero heavy modules leaked. Locked in by
  subprocess-isolated regression tests in `tests/test_memory_decoupling.py`

### Changed

- **Search unified across five fields** — `SQLiteMemoryStore.search_memories`
  LIKEs over `title`, `content`, `key_normalized`, `tags_json`, and
  `keywords_json` (was three). `/memory search` and `MemoryRetriever` now
  cover the same surface
- **Stable block budget eviction is recency-priority** — under budget
  pressure, the renderer admits records newest-first (greedy fit walking
  records in reverse) so a fresh decision/constraint is never crowded out
  by long-tail history. Survivors render in created_at-ASC order so the
  prompt-cache prefix stays stable across turns
- **Review queue duplicate folding refreshes ALL presentation fields** —
  re-hits update `type`, `title`, `content`, `tags_json` (not just
  `evidence` / `occurrences`) so the reviewer always sees the latest
  extraction instead of the first one
- **`/memory clear` and `/clear`** now wipe ALL session summaries via
  `clear_all_session_summaries()`. Previously they only deleted the current
  session, leaving prior-session summaries to silently resurface via the
  cross-session tail
- **`MemoryManager.save_session_summary()`** is now a pure persistence call.
  Crystallization moved upstream to `compress_messages()` so it sees raw
  user text instead of LLM-narrated summaries
- **Manager facade methods** rewired: `crystallize_recent_sessions(limit)`
  → `crystallize_user_messages(messages)`; same approve/reject API
- **`MemoryGuard.classify_type` / `classify_scope`** drive tag-based memory
  type and scope inference

### Removed

- `pinned`, `ttl_days`, `expires_at` fields from `MemoryRecord` — added
  speculatively, never had a functional write path. SQL schema bumped to v3
  with a `DROP COLUMN` migration for existing databases (silent skip on
  SQLite < 3.35.0)
- `MemoryCrystallizer.extract_from_sessions()` — operated on LLM-narrated
  session summaries, exactly the regex-on-summary path the new design
  rejects
- `MemoryManager.crystallize_recent_sessions()` — superseded by
  `crystallize_user_messages()`

### Fixed

- **`/new` was wiping the just-finished session's summaries** — the branch
  called `clear_session()` before `archive_session()`, so cross-session
  recall lost the most recent context. `clear_session()` is no longer
  invoked from `/new`; `archive_session()` (in `on_session_start()`) is the
  correct primitive. (Codex P2)
- **`Agentao._extract_context_hints` read the wrong key on text blocks** —
  list-shaped message content had `block.get("content")` instead of
  `block.get("text")`, silently dropping every multimodal/tool-use message
  and breaking `filepath_hint` scoring. Now matches the canonical
  `{"type": "text", "text": ...}` shape used by `_format_for_summary` and
  `_user_message_text`. (Codex P2)
- **Recall errors are now observable** — exceptions inside
  `MemoryRetriever.recall_candidates()` log a WARNING with traceback,
  increment `_error_count`, and record `_last_error` instead of being
  swallowed silently
- **`<memory-stable>` cross-session tail is pre-reserved** so persistent
  facts can never crowd out the previous-session summary
- **Dynamic recall hard budget** — `render_dynamic_block()` enforces
  `DYNAMIC_RECALL_MAX_CHARS` (~1200) and trims candidates that don't fit
- **Stable block budget pre-reservation refactor** uses a deterministic
  greedy fit instead of "stop at first overflow"

### Tests

- ~300 new memory-subsystem tests across `test_memory_store.py`,
  `test_memory_manager.py`, `test_memory_session.py`, `test_memory_renderer.py`,
  `test_retriever.py`, `test_crystallizer.py`, `test_memory_guards.py`,
  `test_memory_injection.py`, and `test_memory_decoupling.py`
- Suite total: **657 passing**, 1 skipped, 0 failing
- Notable regression guards:
  - `test_new_session_flow_preserves_cross_session_recall` (Codex P2 fix)
  - `test_extracts_paths_from_list_text_blocks` (Codex P2 fix)
  - `test_budget_eviction_preserves_newest_decision` (eviction priority)
  - `test_assistant_narration_does_not_trigger` (crystallization safety)
  - `test_clear_all_session_summaries_removes_cross_session_summaries`
  - `test_search_unified_finds_record_via_any_field`

---

## [0.2.5] — 2026-04-07

### Added

- **`agentao init` setup wizard** — first-run interactive bootstrap for
  `.agentao/` config, API keys, and skill discovery
- **Background agent lifecycle** — pending state, cancellation token plumbing,
  on-disk persistence so the dashboard survives restarts
- **`cwd/skills/`** added as a third highest-priority skills layer
  (overrides project and bundled skills); two-layer scan with first-run
  bootstrap of bundled skills
- **Windows compatibility** for the shell tool and terminal handling
- **130 new tests** covering permissions, skills, MCP, and background agents
- README "Minimum Viable Configuration" section

### Changed

- Bundled office / pdf / ocr skills removed from the default install
  (slimmer wheel; users opt in via the `pdf` / `excel` / `image` extras)
- Install path unified to `pip install agentao` across docs and README
- ChatAgent / Claude naming remnants cleaned up

### Packaging / CI

- GitHub Actions workflow with test / build / smoke matrix
- PyPI release workflows
- `main.py` and `.claude/` excluded from sdist
- `skills/skill-creator` included in wheel; other internal skills marked private

### Fixed

- `/plan save` CLI command removed to match the documented plan-mode v2
  contract (model-driven `plan_save` tool only)

---

## [0.2.3] — 2026-04-06

### Added
- **Plan mode v2** — tool-driven save/finalize workflow
  - `plan_save(content)` tool: persists a draft and returns a `draft_id`
  - `plan_finalize(draft_id)` tool: triggers the approval prompt; stale IDs are rejected
  - Approval prompt shows the full plan before asking "Execute this plan? [y/N]"
  - One-shot `consume_approval_request()` flag prevents repeated approval prompts
  - Auto-save fallback skipped when a draft is already finalized
- `agentao/plan/` sub-package: `session.py` (3-state FSM), `controller.py` (single exit path), `prompt.py` (mandatory turn protocol)
- 44 new tests covering FSM transitions, lifecycle, tools, and prompt structure

### Changed
- Plan approval prompt now only appears after the model explicitly calls `plan_finalize`
- `/plan save` removed as a CLI command; saving is now model-driven via the `plan_save` tool

### Fixed
- Finalized drafts can no longer be overwritten by the auto-save fallback path

### Packaging
- Added MIT `LICENSE` file (Bo Jin)
- Heavy optional dependencies (`pymupdf`, `pdfplumber`, `pandas`, `openpyxl`, `Pillow`, `pycryptodome`, `google-genai`) moved to optional extras: `pdf`, `excel`, `image`, `crypto`, `google`; `full` installs everything
- `skills/` and `workspace/` excluded from both wheel and sdist
- `requires-python` lowered from `>=3.12` to `>=3.10`
- Added `authors`, `license`, `keywords`, `classifiers`, `[project.urls]`
- Version is now defined once in `agentao/__init__.py` and read dynamically by hatchling

---

## [0.2.1] — 2026-03-xx

### Added
- **Permission mode system** — three named presets: `read-only`, `workspace-write` (default), `full-access`
- `/mode` command to switch and persist permission mode to `.agentao/settings.json`
- Plan mode enforced via `PLAN` permission preset (no writes, no dangerous shell)
- Mode restored exactly on `/plan implement` or `/plan clear`

### Changed
- Tool confirmation now driven by the active permission mode rather than per-tool flags
- `/clear` resets permission escalation (`allow_all_tools`) back to False

---

## [0.2.0] — 2026-03-xx

### Added
- **Plan mode** — `/plan` enters a read-only research-and-draft workflow; agent proposes a structured Markdown plan before any mutations
- **Display engine v2** — semantic tool headers (`→ read`, `← edit`, `$ shell`, `✱ search`), buffered output, tail-biased truncation, diff rendering, warning consolidation, live elapsed timer
- **Background agent dashboard** — `/agents`, `/agent dashboard`, `/agent status`
- **Transport protocol** — decoupled runtime from UI via `EventType` stream

### Fixed
- Streaming fallback, thinking handler scope, on_max_iterations guard
- Buffer all shell output; robust `\r`/ANSI/CRLF handling

---

## [0.1.11] — 2026-02-xx

### Added
- **Three-tier context compression** — microcompaction (55% usage) + LLM summarization (65%) + circuit breaker after 3 failures
- Structured 9-section LLM summary; partial compaction keeps last 20 messages verbatim
- Three-tier overflow recovery on context-too-long API error
- **Three-tier token counting** — real `prompt_tokens` from API → `count_tokens` API → local estimator (tiktoken / CJK heuristic)
- `/context` command with token breakdown by component
- Background agent push via `CancellationToken`

---

## [0.1.8] — 2026-01-xx

### Added
- **Sub-agent system** — foreground and background sub-agents with parent context injection and stats footer
- `/agent bg <name> <task>` for background execution
- Tool output file saving, head+tail truncation, per-line length limit
- `/new` command; auto `max_completion_tokens`; session lifecycle hooks

---

## [0.1.5] — 2025-12-xx

### Added
- **Task checklist** (`todo_write`) — LLM-managed task list injected into system prompt; visible via `/todos`
- **MCP (Model Context Protocol)** support — stdio and SSE transports; `mcp_*` tool registration
- **Memory management** — persistent `.agentao_memory.json`; `save_memory`, `search_memory`, `delete_memory` tools; `/memory` commands
- **Permission system** — per-tool confirmation with single-key menu; session escalation with **2** (Yes to all)
- Cognitive Resonance — automatic memory recall with injection confirmation before each response
- Session save/resume (`/sessions`)

---

## [0.1.1] — 2025-11-xx

### Added
- Renamed to **Agentao** (Agent + Tao)
- Gemini provider support (`google-genai`)
- `web_fetch` with automatic crawl4ai fallback for JS-heavy pages
- `/confirm`, `/stream`, `/tools`, `/provider` commands
- Sub-agent system (early version)
- `ask_user` tool for LLM-initiated clarification
- `-p` / `--print` flag for non-interactive print mode
- Multi-line paste via `prompt_toolkit`; single-key confirmation via `readchar`

### Changed
- System prompt: reliability principles, structured reasoning (Action / Expectation / If wrong), operational guidelines
- Context management: `ContextManager`, pinned messages, tool result truncation

---

## [0.1.0] — 2025-10-xx

### Added
- Initial release as **ChatAgent**
- CLI chat loop with OpenAI-compatible API
- Tool system: `read_file`, `write_file`, `replace`, `glob`, `grep`, `run_shell_command`, `web_fetch`, `web_search`, `save_memory`
- Skills system — auto-discovery from `skills/` with YAML frontmatter
- `AGENTAO.md` auto-loading for project-specific instructions
- Current date injected as `<system-reminder>`
- Complete LLM interaction logging to `agentao.log`
