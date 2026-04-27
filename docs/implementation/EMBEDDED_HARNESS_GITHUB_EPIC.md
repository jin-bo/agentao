# Embedded Harness Epic

## Epic

**Title**

Make Agentao a clean embeddable harness via in-place cleanup of construction side effects

**Summary**

Agentao's intended positioning is "an embeddable agent harness inside someone else's app, not a standalone runtime." Today `Agentao.__init__` is a 245-line constructor that implicitly reads env vars, dotenv, `Path.cwd()`, `Path.home()`, and several `.agentao/*.json` files. Embedded hosts (in-process Python, child process, remote service) cannot construct Agentao without inheriting all of those side effects.

This epic does the in-place cleanup: lift implicit environment, cwd/home, and `.agentao/*` reads out of `Agentao.__init__` into a thin `agentao.embedding.build_from_environment()` factory, then remove the remaining fallback reads in staged follow-up issues. By the end of M4, `Agentao(...)` is pure-injection construction. CLI and ACP route through the factory and see zero behavior change. Embedded hosts get a deterministic, side-effect-free construction surface.

We do NOT introduce a parallel `AgentSession` class — `Agentao` remains the single long-term embedding API. We do NOT async-ify the runtime internals — only the public `arun()` surface is async, runtime stays sync.

**Goals**

- Make `Agentao(...)` a pure-injection construction surface with zero implicit env / disk / cwd reads after the capability, factory, fallback-removal, and opt-in subsystem issues land
- Lift env / dotenv / cwd / home / `.agentao/*` reads into `agentao.embedding.factory.build_from_environment()`
- Inject `FileSystem` and `ShellExecutor` capability into existing file/search/shell tools (no tool rewrites)
- Add `schema_version` to `AgentEvent` for versioned wire schema
- Invert `LLMClient` logger ownership so embedded hosts can inject their own logger without `getLogger("agentao")` being mutated
- Add async public surface `Agentao.arun()` (runtime internals stay sync)
- Force explicit `working_directory` in 0.3.0 (minor, BREAKING) after one patch release of soft deprecation in 0.2.16
- Make `Replay` / `Sandbox` / `BgStore` opt-in (None = disabled) for embedded hosts

**Non-goals for v1**

- New parallel `AgentSession` class (rejected — `Agentao` is the long-term embedding API)
- Async runtime internals (`ToolRunner` / `ChatLoopRunner` stay sync)
- `AsyncTransport` (only public `arun()` is async; internal `Transport` Protocol unchanged)
- `MemoryStore` / `MCPRegistry` / `Logger` / `Tracer` Protocol abstraction (deferred to M5)
- Removing `Agentao` 's existing transport / cancellation / replay primitives — they are reused as-is

**Acceptance Criteria**

- An embedded host can construct `Agentao(working_directory=Path(...), llm_config=..., filesystem=..., shell=...)` with **zero** implicit env / disk / cwd reads after M4 completes
- `agentao.embedding.build_from_environment()` reproduces today's CLI behavior end-to-end
- CLI launch (`./run.sh`) and ACP `session/new` behavior unchanged
- File, search, and shell tools route through capability backends; in-memory fakes can replace them
- `AgentEvent.schema_version == 1` ships in all wire payloads (ACP/SSE/host)
- `LLMClient` no longer mutates `getLogger("agentao")` (level or handlers) when caller injects a logger
- `LLMClient(log_file=None, logger=injected)` constructs with zero package-root side effects
- Embedded async host can `await agent.arun(...)` without manual thread bridging
- After 0.3.0, `Agentao()` without `working_directory=` raises `TypeError`

**Dependencies / Reuse**

- Existing runtime layering: `agentao/runtime/` (`ToolRunner`, `ChatLoopRunner`, `ToolExecutor`)
- Existing transport abstraction: `agentao/transport/` (Protocol unchanged)
- Existing tool base: `agentao/tools/base.py`
- Existing memory / permission / MCP / replay / sandbox subsystems retain public APIs; only their fallback paths are removed

**Risks**

- **Factory + fallback cleanup touches many files.** Mitigated by splitting into Issue 4 (factory + CLI/ACP migration, fallbacks intact), Issue 5 (subsystem fallback deletion), and Issue 9 (opt-in replay/sandbox/background subsystems).
- **Hard break of `Agentao()` would surprise PyPI users and break repo's own `examples/`.** Mitigated by Issue 7 soft deprecation cycle (0.2.16 patch) before Issue 8 hard break (0.3.0 minor, standard SemVer breaking).
- **Capability injection threads through every file/search/shell tool.** Mitigated by Issue 3 keeping default `LocalFileSystem` / `LocalShellExecutor` byte-equivalent to today.
- **`Path.cwd()` removal interacts with CLI `cd` semantics.** Factory captures `Path.cwd()` once at startup; CLI users who rely on lazy cwd switching mid-process will see a change. Documented in Issue 8.

## Issue 1

**Title**

Add `schema_version` to `AgentEvent` for versioned wire payloads

**Problem**

`AgentEvent` is a dataclass with only `type` and `data` fields (`agentao/transport/events.py:42-76`). The wire payload that ACP / SSE / embedded host receives carries no schema version, so future schema changes cannot be negotiated. ACP has its own `ACP_PROTOCOL_VERSION = 1` (`agentao/acp/protocol.py:16`), but the runtime event payload itself is unversioned.

**Scope**

- Add `schema_version: int = 1` to `AgentEvent`
- Add `to_dict() -> dict[str, Any]` serializer
- Wire transports emit `schema_version` in their payload

**Implementation Checklist**

- [ ] Add `schema_version: int = 1` field to `AgentEvent` dataclass
- [ ] Add `to_dict()` method returning `{"type": ..., "schema_version": ..., "data": ...}`
- [ ] Update `agentao/transport/sdk.py` to emit `schema_version` in serialized payload
- [ ] Update `agentao/acp/transport.py` to forward `schema_version` in `session/update` notifications
- [ ] Add `tests/test_event_schema_version.py` asserting default value on a concrete event and presence in wire output
- [ ] Audit replay snapshot fixtures (`tests/test_replay*.py`), ACP wire-trace assertions, and any test using `==` on a serialized event dict for two-key `{type, data}` shape strictness — update to expect the three-key shape
- [ ] Document the field on `AgentEvent` docstring as the runtime-payload version contract

**Acceptance Criteria**

- `AgentEvent(EventType.TURN_START).schema_version == 1`
- ACP wire trace shows `schema_version` field in every notification payload
- Existing tests pass after the dict-shape audit; no consumer pins the legacy two-key shape

## Issue 2

**Title**

Invert `LLMClient` logger ownership for embedded hosts

**Problem**

`LLMClient.__init__` writes to the `getLogger("agentao")` package root: it sets level to DEBUG and attaches a `RotatingFileHandler` (`agentao/llm/client.py:142-180`). The current code already tags its own handlers with `_agentao_llm_file_handler=True` and only evicts marked handlers on reconstruction, so it does **not** drop outsider handlers (e.g. AcpServer's stderr guard). The remaining problem is **inversion of control**: an embedded host that injects its own logger still has its package-root level and handler list mutated underneath it. There is no way to say "I own this logger; don't touch it."

**Scope**

- Accept injected `logger` parameter
- When caller provides a logger, do not mutate `getLogger("agentao")` package root (no level set, no handler attach/evict)
- `log_file` becomes optional (None = no file handler)

**Implementation Checklist**

- [ ] Add `logger: logging.Logger | None = None` parameter to `LLMClient.__init__`
- [ ] Make `log_file: str | None = None` (None means no file handler)
- [ ] When `logger` is provided, skip `pkg_logger.setLevel()` / `pkg_logger.removeHandler()` / `addHandler()` entirely
- [ ] Update `Agentao.__init__` to keep current CLI behavior: still pass `log_file=str(self.working_directory / "agentao.log")`
- [ ] Add `tests/test_llm_client_logger_injection.py`:
  - assert `LLMClient(logger=mock)` does not touch `getLogger("agentao").level` or `.handlers`
  - assert repeated reconstruction with `logger=mock` is a no-op against the package root
  - assert `LLMClient(log_file=None)` does not attach any file handler
- [ ] Document the new behavior in `LLMClient` docstring

**Acceptance Criteria**

- Embedded host can pass own logger and Agentao does not mutate `getLogger("agentao")`
- `LLMClient(log_file=None)` is a clean construction with zero package-root side effects
- Existing CLI behavior (file at `<wd>/agentao.log`, package-root DEBUG level) unchanged when `logger=None`

## Issue 3

**Title**

Add `FileSystem` / `ShellExecutor` capability protocols and inject into file/search/shell tools

**Problem**

File, search, and shell tools call local filesystem and process APIs directly (`agentao/tools/file_ops.py`, `agentao/tools/search.py`, `agentao/tools/shell.py`). Embedded hosts cannot route IO through Docker exec, audit logs, virtual FS, remote runners, etc. — the only escape today is monkey-patching.

**Scope**

- Define `FileSystem` and `ShellExecutor` as `Protocol`
- Provide `LocalFileSystem` / `LocalShellExecutor` defaults with byte-equivalent current behavior
- `Agentao.__init__` accepts optional capability instances
- Refactor `ReadFileTool`, `WriteFileTool`, `EditTool`, `ReadFolderTool`, `FindFilesTool`, `SearchTextTool`, `ShellTool` to call capabilities

**Implementation Checklist**

- [ ] Create `agentao/capabilities/__init__.py`
- [ ] Create `agentao/capabilities/filesystem.py`:
  - [ ] `FileEntry`, `FileStat` dataclasses
  - [ ] `class FileSystem(Protocol)` with `read_bytes`, `write_text`, `list_dir`, `glob`, `stat`, `exists`, `is_dir`, `is_file`
  - [ ] `class LocalFileSystem` with byte-equivalent default implementations
- [ ] Create `agentao/capabilities/shell.py`:
  - [ ] `ShellRequest`, `ShellResult`, `BackgroundHandle` dataclasses
  - [ ] `class ShellExecutor(Protocol)` with `run`, `run_background`
  - [ ] `class LocalShellExecutor` reusing `agentao/tools/shell.py:_run_foreground` / `_run_background` logic
- [ ] Refactor `agentao/tools/file_ops.py` to take a `FileSystem` and route through it
- [ ] Refactor `agentao/tools/search.py` to take a `FileSystem` and route glob/search reads through it
- [ ] Refactor `agentao/tools/shell.py` to take a `ShellExecutor` and route through it
- [ ] Add `filesystem: FileSystem | None = None`, `shell: ShellExecutor | None = None` to `Agentao.__init__` (None = local default)
- [ ] Update `agentao/tooling/registry.py::register_builtin_tools` to wire capabilities to tools
- [ ] Add `tests/test_filesystem_capability_swap.py` with in-memory fake FS verifying tool calls route through it
- [ ] Add `tests/test_shell_capability_swap.py` with mock executor capturing all shell invocations
- [ ] Add `tests/test_local_filesystem_byte_equivalence.py`: snapshot fixture-tree listing / glob / stat output before refactor; assert exact equality after `LocalFileSystem` is in place — covers `os.scandir` ordering, `Path.glob` symlink follow, and exposed stat fields

**Acceptance Criteria**

- Default behavior identical to today; CLI / ACP / existing test suite unchanged
- Embedded host can pass an in-memory `FileSystem` and file/search tool reads/writes/listing route through it
- Embedded host can pass a Docker-backed `ShellExecutor` and shell calls route through it
- Existing working-directory-relative path resolution and any tool-side path-restriction logic continue to apply (capability is the IO layer; restriction stays in the tool)

## Issue 4

**Title**

Add `agentao/embedding/factory.py` and migrate CLI/ACP to it

**Problem**

`Agentao.__init__` reads env (`LLM_PROVIDER`, `*_API_KEY`, `*_BASE_URL`, `*_MODEL`), dotenv, `Path.cwd()`, `Path.home()`, `.agentao/permissions.json`, `.agentao/mcp.json`, `.agentao/sandbox.json`, `.agentao/replay.json` implicitly. Embedded hosts cannot construct Agentao without inheriting all those side effects. CLI / ACP currently rely on this implicit behavior.

**Scope**

- Add `agentao.embedding.build_from_environment(...)` that captures all current implicit reads
- CLI and ACP entrypoints route through it
- `Agentao.__init__` continues to accept old keyword arguments (compatibility path) — no breaking change in this issue

**Implementation Checklist**

- [ ] Create `agentao/embedding/__init__.py` exporting `build_from_environment`
- [ ] Create `agentao/embedding/factory.py::build_from_environment(working_directory: Path | None = None, **overrides) -> Agentao`
- [ ] Inside factory:
  - [ ] `load_dotenv()` (reads `.env` from working_directory or fallback)
  - [ ] Read `LLM_PROVIDER` and provider-prefixed env vars → construct LLM config
  - [ ] `working_directory or Path.cwd()` resolved to absolute Path
  - [ ] Load `<wd>/.agentao/permissions.json` + `~/.agentao/permissions.json` → construct `PermissionEngine`
  - [ ] Load `<wd>/.agentao/mcp.json` + `~/.agentao/mcp.json` → MCP server config dict
  - [ ] Compute memory roots `<wd>/.agentao` + `~/.agentao`
  - [ ] Load replay / sandbox / bg_store config from `<wd>/.agentao/`
  - [ ] Pass everything explicitly to `Agentao(...)`
- [ ] `Agentao.__init__` accepts new explicit-injection keyword args alongside the existing legacy args. Precedence rule: an injected fully-constructed object always wins over its raw-config sibling; if both are supplied, raise `ValueError`:
  - [ ] `llm_client: LLMClient | None` (preferred; injected wins) **or** existing `api_key` / `base_url` / `model` / `temperature` (legacy raw config — factory uses these)
  - [ ] `logger: logging.Logger | None` (forwarded to `LLMClient` per Issue 2)
  - [ ] `permission_engine: PermissionEngine | None`
  - [ ] `memory_manager: MemoryManager | None`
  - [ ] `skill_manager: SkillManager | None` (when injected, suppress auto-discovery — see Issue 5)
  - [ ] `project_instructions: str | None` (when injected, suppress `_load_project_instructions` disk read — see Issue 5)
  - [ ] `mcp_manager: McpClientManager | None` (preferred) **or** existing `extra_mcp_servers: dict | None` (factory builds dict from disk)
  - [ ] `filesystem: FileSystem | None` / `shell: ShellExecutor | None`
- [ ] Migrate `agentao/cli/app.py` and `agentao/cli/entrypoints.py` to call `build_from_environment()`
- [ ] Migrate `agentao/acp/session_new.py` to call `build_from_environment(working_directory=request_cwd)`
- [ ] Migrate `agentao/acp/session_load.py` similarly
- [ ] Add `tests/test_factory_build_from_environment.py` with tmpdir fixtures simulating `.env` + `.agentao/*.json`
- [ ] Add a CLI smoke test confirming `./run.sh` startup is unchanged

**Acceptance Criteria**

- CLI launch behavior identical to today
- ACP `session/new` and `session/load` behavior identical to today
- Factory can be invoked with full env stub for deterministic testing
- Existing tests all pass — fallback paths in subsystems remain (deletion is Issue 5)

## Issue 5

**Title**

Tighten core: delete fallbacks from `LLMClient` / `MemoryManager` / `PermissionEngine` / `load_mcp_config`

**Problem**

After Issue 4 ships, the factory always passes explicit values to subsystems, but the underlying modules still have fallback paths (`os.getenv`, `Path.home()`, `Path.cwd()`) that allow implicit construction. These are dead code from CLI / ACP perspective post-Issue-4 but a foot-gun for any embedding host that constructs them directly.

**Scope**

Mostly deletion PR. No CLI / ACP behavior change because Issue 4 already passes subsystem values explicitly through the factory.

**Implementation Checklist**

- [ ] `LLMClient.__init__`: delete `os.getenv` block (`agentao/llm/client.py:108-127`); make `api_key`, `base_url`, `model`, `temperature` required
- [ ] `MemoryManager.__init__`: delete `Path.home()` fallback; make `project_root` and `global_root` required
- [ ] `PermissionEngine.__init__`: delete `Path.cwd()` fallback (`agentao/permissions.py:190-201`); make `project_root` required
- [ ] `load_mcp_config()`: delete `Path.cwd()` fallback (`agentao/mcp/config.py:84-86`); make `project_root` required
- [ ] Remove direct provider env reads from sub-agent construction (`agentao/agents/tools.py`); inherit resolved parent LLM config or use explicit factory-supplied provider config
- [ ] **Skill auto-discovery skip path**: today `agent.py:142-144` always calls `SkillManager(working_directory=...)` which discovers project + bundled skills from disk. When `skill_manager` is injected via Issue 4, the constructor must skip the auto-discovery `SkillManager(...)` call entirely and use the injected instance verbatim
- [ ] **Project-instructions skip path**: today `agent.py:255` unconditionally calls `self._load_project_instructions()` which reads `<wd>/AGENTAO.md`. When `project_instructions` is injected via Issue 4, `Agentao.__init__` must store the override and skip the disk read; `_load_project_instructions` becomes a fallback only used when the injection is `None`
- [ ] Audit and update any test that constructs `LLMClient()` / `MemoryManager()` / `PermissionEngine()` without args
- [ ] Add `tests/test_agent_subsystem_injection.py`: construct `Agentao` directly with explicit subsystem injections and verify a chat round-trip works
- [ ] Add `tests/test_no_subsystem_fallback_reads.py`: monkeypatch `os.environ`, `Path.cwd()`, `Path.home()` to assert these subsystem constructors no longer read them
- [ ] Add `tests/test_skill_manager_injection.py`: assert injected `skill_manager` is used as-is and no project-skill discovery happens
- [ ] Add `tests/test_project_instructions_injection.py`: assert injected `project_instructions` is used as-is and `<wd>/AGENTAO.md` is never read

**Acceptance Criteria**

- Constructing `LLMClient()` / `MemoryManager()` / `PermissionEngine()` without args raises `TypeError`
- CLI and ACP behavior unchanged (factory is the only path that reads env / disk for these subsystems)
- `Agentao` is constructable with explicit subsystem injections and no env / cwd / home side effects from these fallback paths

## Issue 6

**Title**

Add async public surface `Agentao.arun()` (runtime internals stay sync)

**Problem**

`Agentao.chat()` is sync (`agentao/agent.py:459-477`). Async hosts must wrap calls in `loop.run_in_executor` themselves; cancellation, permission, and event interactions all suffer from the sync facade.

**Scope**

Add `async def arun(...)`. Internal runtime stays sync; thread executor bridges through the same turn lifecycle as `chat()`. We deliberately do NOT introduce `AsyncTransport` — that would expand the change footprint without proportional benefit, since runtime is fundamentally sequential I/O.

**Implementation Checklist**

- [ ] Add `async def arun(self, user_message: str, max_iterations: int = 100, cancellation_token: CancellationToken | None = None) -> str`
- [ ] Implementation: `await asyncio.get_running_loop().run_in_executor(None, self.chat, user_message, max_iterations, cancellation_token)`. The chat path today goes through `Agentao.chat()` → `ChatLoopRunner.run()` (`agentao/runtime/chat_loop.py`); reuse that pipeline as-is via the executor bridge, do not bypass into `ChatLoopRunner` internals
- [ ] Keep sync `chat()` on the existing sync path; `chat()` must not call `asyncio.run()` because it would fail inside already-running async hosts
- [ ] Document in `Agentao` docstring: runtime internals are sync; async surface is the public contract for embedded hosts
- [ ] Document that `Transport` Protocol stays sync — host's async event handler should bridge at the transport level if needed
- [ ] Add `tests/test_async_chat.py` running `asyncio.run(agent.arun(...))`
- [ ] Verify cancellation, replay, max_iterations behave identically between sync and async paths

**Acceptance Criteria**

- Async host can `await agent.arun(...)` without manual thread bridge
- Sync `chat()` continues to work for CLI
- Cancellation, replay, max_iterations, transport events behave identically across both surfaces
- All existing tests pass

## Issue 7

**Title**

Soft deprecate `Agentao()` without `working_directory` and migrate internal docs / examples / tests

**Problem**

After Issues 4-6, embedded hosts have a clean factory entry, but `Agentao(working_directory=None)` still falls back to `Path.cwd()` lazily — keeping the implicit cwd dependency for any caller that doesn't follow the new path. README, multiple docs, and bundled `examples/data-workbench` + `examples/batch-scheduler` all currently demonstrate `Agentao()`. Hard break would surprise PyPI users and break repo's own examples.

**Scope**

Emit `DeprecationWarning` when `Agentao()` is constructed without `working_directory`. Migrate internal README, docs, examples, and tests to the explicit form. Plan hard break for the next minor (0.3.0, Issue 8).

**Implementation Checklist**

- [ ] In `Agentao.__init__`, when `working_directory is None`, emit:
  ```python
  warnings.warn(
      "Agentao() without working_directory= is deprecated and will be required "
      "in 0.3.0. Pass an explicit Path, or use "
      "agentao.embedding.build_from_environment() for CLI-style auto-detection "
      "of cwd/.env/.agentao/.",
      DeprecationWarning,
      stacklevel=2,
  )
  ```
- [ ] Sweep all `examples/` (not just data-workbench / batch-scheduler — also `headless_worker.py`, `saas-assistant`, `ticket-automation`, `ide-plugin-ts`) for any `Agentao(...)` construction missing `working_directory=`. Note: `examples/data-workbench/src/workbench.py:86` and `examples/batch-scheduler/src/daily_digest.py:48` already pass `working_directory=workdir` — verify and skip
- [ ] Update bare `Agentao()` recommendations:
  - [ ] `README.md:753` ("just `Agentao()` — it uses `NullTransport` automatically") — rewrite to explicit form or `build_from_environment()`
  - [ ] `README.zh.md:745` (Chinese counterpart)
  - [ ] `docs/MODEL_SWITCHING.md:317` (bare `Agentao()`)
  - [ ] `docs/features/CHATAGENT_MD_FEATURE.md:208` (bare `Agentao()`)
- [ ] Update partial-arg examples missing `working_directory=`:
  - [ ] `README.md:747` (`Agentao(transport=transport)`)
  - [ ] `README.zh.md:739`
  - [ ] `docs/MODEL_SWITCHING.md:177` (`Agentao(model="gpt-5.4")`)
  - [ ] `docs/LOGGING.md:352` (`Agentao(api_key=..., model=...)`)
- [ ] Update tests that construct `Agentao()` without args:
  - [ ] `tests/test_multi_turn.py:21`
  - [ ] `tests/test_skills_prompt.py:14`
  - [ ] `tests/test_skill_integration.py:11`
  - [ ] `tests/test_agentao_md.py:19,52`
  - [ ] `tests/test_tool_confirmation.py:94`
  - [ ] `tests/test_date_in_prompt.py:15`
  - [ ] `tests/test_memory_renderer.py:356,391,424`
- [ ] Add CHANGELOG entry: "0.2.16: `Agentao()` without `working_directory=` is deprecated; will be required in 0.3.0"
- [ ] Run `pytest -W error::DeprecationWarning tests/` to ensure no internal warning leakage

**Acceptance Criteria**

- `Agentao()` without `working_directory` triggers a clear `DeprecationWarning` with migration guidance
- All bundled docs and examples use explicit form or factory
- `pytest -W error::DeprecationWarning tests/` passes — internal code raises zero warnings
- CLI and ACP route through factory and raise no warnings

## Issue 8

**Title**

Hard break: `Agentao` requires `working_directory` (0.3.0)

**Problem**

After one release cycle with deprecation (0.2.16 patch), complete the surface tightening in 0.3.0 minor: `working_directory` becomes required keyword-only. The `Path.cwd()` fallback inside `working_directory` property is removed.

**Scope**

Pure-collapse PR. Remove the deprecation warning, make `working_directory` a required keyword argument, delete the lazy property fallback. Bump version to 0.3.0.

**Implementation Checklist**

- [ ] Remove `warnings.warn` block from `Agentao.__init__` (added in Issue 7)
- [ ] Change signature: `def __init__(self, *, working_directory: Path, ...)`  — keyword-only, required
- [ ] Delete `Path.cwd()` branch in `working_directory` property (`agentao/agent.py:291-306`); property always returns frozen value
- [ ] Delete lazy-cwd providers in subsystems that depended on the lazy fallback:
  - [ ] `bg_store = BackgroundTaskStore(persistence_dir_provider=Path.cwd)` → frozen `persistence_dir=working_directory` (`agent.py:223`)
  - [ ] `sandbox_policy = SandboxPolicy(project_root_provider=Path.cwd)` → frozen `project_root=working_directory` (`agent.py:268`)
- [ ] **CLI cwd-change audit**: grep for `os.chdir` across `agentao/cli*` and decide policy. If the CLI never `chdir`s after startup, document "factory captures cwd once at startup; mid-process `cd` is not retargeted" in the CHANGELOG migration section. If the CLI does `chdir` (e.g. via a `/cd` command), re-invoke `build_from_environment()` from that command path or explicitly disable mid-process retargeting
- [ ] Bump `__version__` in `agentao/__init__.py:8` to `0.3.0`
- [ ] Update CHANGELOG with `BREAKING:` marker explaining the change and migration path, including the cwd-freeze semantic
- [ ] Verify `pytest tests/` passes (Issue 7 should have left no implicit constructions)
- [ ] Manually run `./run.sh` to confirm CLI startup, `/clear`, and post-`cd` behavior matches the documented policy

**Acceptance Criteria**

- `Agentao()` without `working_directory` raises `TypeError`, not warning
- `agentao.embedding.build_from_environment()` continues to work transparently
- CHANGELOG documents the migration step (`Agentao()` → `build_from_environment()` or `Agentao(working_directory=...)`)
- All bundled tests pass
- Version is `0.3.0`

## Issue 9

**Title**

Make `Replay` / `Sandbox` / `BackgroundTaskStore` opt-in (None = disabled)

**Problem**

`Agentao.__init__` always constructs `ReplayConfig`, `SandboxPolicy`, `BackgroundTaskStore` — which read `<wd>/.agentao/*.json` and the cwd (`agentao/agent.py:216-280`). Embedded hosts that don't want persistence or sandboxing cannot disable them cleanly.

**Scope**

Make all three optional in `Agentao.__init__`. None = disabled. Factory enables them per current CLI behavior.

**Implementation Checklist**

- [ ] Add `replay_config: ReplayConfig | None = None` parameter to `Agentao.__init__`
- [ ] Add `sandbox_policy: SandboxPolicy | None = None`
- [ ] Add `bg_store: BackgroundTaskStore | None = None`
- [ ] When None: skip construction; runtime treats feature as off
- [ ] When `bg_store is None`, do not register `check_background_agent` / `cancel_background_agent`
- [ ] When `bg_store is None`, **omit `run_in_background` from the sub-agent tool schema entirely** (do not expose-then-error). Decision: schema-level removal beats runtime error because the LLM can't be tempted to call a disabled feature, and ACP / OpenAI clients won't see a phantom tool. Document in `Agentao` docstring that disabling `bg_store` shrinks the visible tool surface
- [ ] Update factory to construct all three from `<wd>/.agentao/*` and pass them explicitly (preserves CLI behavior)
- [ ] Add `tests/test_agent_subsystems_optional.py` constructing `Agentao` with all three None and verifying chat / tool execution still work
- [ ] Add tests asserting background tools are absent or disabled when `bg_store is None`
- [ ] Document the opt-in semantics in `Agentao` docstring

**Acceptance Criteria**

- Embedded host can construct `Agentao` with replay / sandbox / bg_store all None and run a chat round-trip
- CLI behavior preserved through factory
- No silent default-on for these features in pure-injection mode
- Background-agent tools cannot dereference `None` stores and expose clear disabled semantics

## Issue 10

**Title**

Define `MemoryStore` Protocol and refactor `MemoryManager` to delegate

**Problem**

`MemoryManager` is tightly coupled to SQLite + filesystem. Embedded hosts that want to back memory with their own storage (Redis, postgres, in-memory, remote API) must subclass `MemoryManager` or fork it.

**Scope**

Define `MemoryStore` Protocol covering persistent memory and session-summary CRUD. Provide `SqliteMemoryStore` default. Refactor `MemoryManager` to consume the Protocol. This is intentionally deferred to M5 because protocol-izing the SQLite-shaped API is a larger surface decision than `FileSystem` / `ShellExecutor`.

**Implementation Checklist**

- [ ] Audit `MemoryManager` public surface: write, search, soft-delete, session-summary CRUD, retrieval
- [ ] Define `MemoryStore` Protocol in `agentao/capabilities/memory.py`
- [ ] Implement `SqliteMemoryStore` (current behavior, lifted out of `MemoryManager`)
- [ ] Refactor `MemoryManager` to take `store: MemoryStore` rather than path roots
- [ ] Factory builds `SqliteMemoryStore` from path roots and passes to `MemoryManager`
- [ ] Add `InMemoryMemoryStore` test fake
- [ ] Document Protocol surface and version policy
- [ ] Tests: `tests/test_memory_store_swap.py` swaps in InMemoryMemoryStore and verifies all `MemoryManager` operations

**Acceptance Criteria**

- Embedded host can swap memory backend without changing `Agentao` or `MemoryManager`
- CLI behavior unchanged
- Protocol surface is documented as stable across future minor releases

## Issue 11

**Title**

Define `MCPRegistry` Protocol and refactor `McpClientManager`

**Problem**

`agentao/mcp/config.py` and `McpClientManager` assume disk-loaded MCP server config. Embedded hosts that want to register MCP servers programmatically (e.g., from a host's plugin system, dynamic discovery, remote registry) must monkey-patch.

**Scope**

Define `MCPRegistry` Protocol covering server enumeration + lifecycle. Default `FileBackedMCPRegistry` reads `.agentao/mcp.json` (current behavior).

**Implementation Checklist**

- [ ] Define `MCPRegistry` Protocol in `agentao/capabilities/mcp.py` (`list_servers`, `get_server`, `connect`, `disconnect`)
- [ ] Implement `FileBackedMCPRegistry` reading `<wd>/.agentao/mcp.json` + `~/.agentao/mcp.json`
- [ ] Refactor `McpClientManager` to consume `MCPRegistry` rather than raw config dict
- [ ] Factory constructs `FileBackedMCPRegistry`
- [ ] Embedded host can pass an in-memory or remote `MCPRegistry`
- [ ] Add `tests/test_mcp_registry_swap.py` with programmatic registry

**Acceptance Criteria**

- Embedded host can register MCP servers programmatically
- CLI loads MCP servers from disk via factory unchanged
- ACP `session/new` `mcpServers` injection still works (uses programmatic registry path)

## Issue 12

**Title**

Add `docs/EMBEDDING.md` and update existing docs to point at new patterns

**Problem**

After all surface changes, embedded host integration is non-obvious. Capability injection, factory vs. direct construction, async usage, and the migration from old `Agentao()` to the new explicit form are each documented today only inside source comments and this epic.

**Scope**

Add `docs/EMBEDDING.md` covering end-to-end embedding. Update existing docs (README, MODEL_SWITCHING, LOGGING, CHATAGENT_MD_FEATURE) to point at the new patterns.

**Implementation Checklist**

- [ ] Add `docs/EMBEDDING.md` with sections:
  - [ ] "Minimal embedded construction" (factory path)
  - [ ] "Pure-injection construction" (`Agentao(workspace=..., llm_config=..., filesystem=..., shell=...)`)
  - [ ] "Capability injection" (FileSystem / ShellExecutor examples)
  - [ ] "Async usage" (`await agent.arun()`)
  - [ ] "Replay / Sandbox / BgStore opt-in"
  - [ ] "Migration: 0.2.15 → 0.2.16 → 0.3.0"
- [ ] Update `README.md` to link `docs/EMBEDDING.md` from the embedding section
- [ ] Update `docs/MODEL_SWITCHING.md`, `docs/LOGGING.md`, `docs/features/CHATAGENT_MD_FEATURE.md` to use the new construction patterns
- [ ] Sweep all bundled examples and align with the new patterns: `examples/headless_worker.py`, `examples/saas-assistant`, `examples/ticket-automation`, `examples/ide-plugin-ts`, `examples/data-workbench`, `examples/batch-scheduler`. Each example should either call `build_from_environment()` or pass explicit `working_directory=` + capability injections

**Acceptance Criteria**

- An embedded host author can read `docs/EMBEDDING.md` and integrate Agentao without source-diving
- Migration across 0.2.15 → 0.2.16 → 0.3.0 is documented step-by-step
- Existing docs no longer show pre-cleanup `Agentao()` patterns

## Suggested Milestones

**M1: Cheap fixes (independently shippable, no dependencies)**

- Issue 1 — Versioned `AgentEvent`
- Issue 2 — `LLMClient` logger live bug

**M2: Capability injection + factory (the heart of the epic)**

- Issue 3 — `FileSystem` / `ShellExecutor` capability for file/search/shell tools
- Issue 4 — `agentao/embedding/factory.py` + CLI/ACP migration (fallbacks intact)
- Issue 5 — Tighten subsystem fallbacks (depends on Issue 4)

**M3: Async surface + soft deprecation**

- Issue 6 — `Agentao.arun()`
- Issue 7 — Soft deprecate `Agentao()` (depends on Issue 4 — factory must exist)

**M4: Opt-in subsystems + hard break (0.3.0)**

- Issue 9 — Replay / Sandbox / BgStore opt-in
- Issue 8 — Hard break `working_directory` required (depends on Issue 7 and Issue 9)

**M5: Protocol surface for memory / MCP + docs**

- Issue 10 — `MemoryStore` Protocol
- Issue 11 — `MCPRegistry` Protocol
- Issue 12 — `docs/EMBEDDING.md` and existing-docs migration

## Dependency Graph

```
Issue 1 ──┐
Issue 2 ──┴── M1 (parallel, independent)

Issue 3 ──┐
          ├── M2
Issue 4 ──┤
          │
Issue 5 ──┴── (depends on 4)

Issue 6 ──┐
          ├── M3 (6 independent; 7 depends on 4)
Issue 7 ──┘

Issue 9 ──┐
          ├── M4
Issue 8 ──┘ (depends on 7 and 9)

Issue 10 ─┐
Issue 11 ─┼── M5
Issue 12 ─┘
```

## Cross-References

- Strategy doc: `docs/implementation/EMBEDDED_HARNESS_IMPLEMENTATION_PLAN.md`
- Source feature plan: `workspace/reports/agentao-embedded-harness-feature-review-2026-04-27.md`
- Sibling epic (ACP): `docs/implementation/ACP_GITHUB_EPIC.md`

### Issue ↔ PR mapping (vs. IMPLEMENTATION_PLAN)

| GitHub Issue | PR in IMPLEMENTATION_PLAN |
|---|---|
| Issue 1 (`schema_version`) | PR 1A |
| Issue 2 (logger ownership) | PR 1B |
| Issue 3 (FS / Shell capability) | PR 2 |
| Issue 4 (factory + CLI/ACP migration) | PR 3a |
| Issue 5 (delete subsystem fallbacks) | PR 3b |
| Issue 6 (`Agentao.arun`) | PR 4 |
| Issue 7 (soft deprecate `Agentao()`) | PR 5a |
| Issue 8 (hard break, 0.3.0) | PR 5b |
| Issue 9 (Replay / Sandbox / BgStore opt-in) | PR 6+ |
| Issue 10 (`MemoryStore` Protocol) | PR 6+ |
| Issue 11 (`MCPRegistry` Protocol) | PR 6+ |
| Issue 12 (`docs/EMBEDDING.md`) | PR 6+ |
