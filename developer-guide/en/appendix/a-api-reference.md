# Appendix A · API Reference

This is a **surface reference** — the public symbols that form Agentao's embedding contract. Anything not listed here is an implementation detail and may change without notice. For behavior and examples, follow the links back to the main guide.

Authoritative `__all__`:

- `from agentao import ...` → `Agentao`, `SkillManager`
- `from agentao.embedding import ...` → `build_from_environment`
- `from agentao.transport import ...` → `AgentEvent`, `EventType`, `Transport`, `NullTransport`, `SdkTransport`, `build_compat_transport`
- `from agentao.capabilities import ...` → `FileSystem`, `LocalFileSystem`, `FileEntry`, `FileStat`, `ShellExecutor`, `LocalShellExecutor`, `ShellRequest`, `ShellResult`, `BackgroundHandle`, `MemoryStore`, `SQLiteMemoryStore`, `MCPRegistry`, `FileBackedMCPRegistry`, `InMemoryMCPRegistry`
- `from agentao.tools.base import ...` → `Tool`, `ToolRegistry`
- `from agentao.permissions import ...` → `PermissionEngine`, `PermissionMode`, `PermissionDecision`
- `from agentao.memory.manager import MemoryManager`
- `from agentao.cancellation import ...` → `CancellationToken`, `AgentCancelledError`
- `from agentao.acp_client import ...` → `ACPManager`, `ACPClient`, `AcpClientError`, `AcpErrorCode`, `AcpRpcError`, `AcpInteractionRequiredError`, `AcpClientConfig`, `AcpServerConfig`, `AcpConfigError`, `PromptResult`, `ServerState`, `load_acp_client_config` (and lower-level re-exports — see `agentao.acp_client.__init__.py` docstring for which are "stable embedding surface" vs. "implementation detail")
- `from agentao.host import ...` → `ActivePermissions`, `EventStream`, `StreamSubscribeError`, `HostEvent`, `ToolLifecycleEvent`, `SubagentLifecycleEvent`, `PermissionDecisionEvent`, `RFC3339UTCString`, `export_host_event_json_schema`, `export_host_acp_json_schema` — host-facing harness contract, see [A.10](#a-10-embedded-host-contract)

## A.1 `Agentao`

The core class — see [Part 2.2](/en/part-2/2-constructor-reference) for the full constructor parameter table.

### Constructor

See [Part 2.2](/en/part-2/2-constructor-reference) for the full parameter table. **Since 0.3.0**, `Agentao()` without `working_directory=` raises `TypeError` from Python signature dispatch — the soft-deprecation cycle ended. End-to-end embedding patterns live in [`docs/EMBEDDING.md`](../../../docs/EMBEDDING.md).

```python
Agentao(
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    transport: Transport | None = None,
    *,
    working_directory: Path,                    # required since 0.3.0
    extra_mcp_servers: dict[str, dict] | None = None,
    permission_engine: PermissionEngine | None = None,
    max_context_tokens: int = 200_000,
    plan_session: PlanSession | None = None,
    # Embedded-harness explicit injections
    llm_client: LLMClient | None = None,
    logger: logging.Logger | None = None,
    memory_manager: MemoryManager | None = None,
    skill_manager: SkillManager | None = None,
    project_instructions: str | None = None,
    mcp_manager: McpClientManager | None = None,
    mcp_registry: MCPRegistry | None = None,    # 0.3.0+ (#17)
    filesystem: FileSystem | None = None,
    shell: ShellExecutor | None = None,
    # Opt-in subsystems (None = fully disabled)
    replay_config: ReplayConfig | None = None,
    sandbox_policy: SandboxPolicy | None = None,
    bg_store: BackgroundTaskStore | None = None,
    # Legacy callbacks (prefer `transport=`)
    output_callback: Callable[[str], None] | None = None,
    confirmation_callback: Callable[[str, str, dict], bool] | None = None,
    ask_user_callback: Callable[[str], str] | None = None,
    on_max_iterations_callback: Callable[[int, list], dict] | None = None,
    # ...
)
```

Mutual-exclusion rules (raise `ValueError` if violated):

- `llm_client=` together with any of `api_key=` / `base_url=` / `model=` / `temperature=`
- `mcp_manager=` together with `extra_mcp_servers=`
- `mcp_manager=` together with `mcp_registry=` — the registry is a config source, the manager is the construction outcome

Opt-in subsystem semantics (defaults are `None` since 0.2.16):

- `replay_config=None` — no `<wd>/.agentao/replay.json` is read at construction time; the agent uses a no-op `ReplayConfig()` internally.
- `sandbox_policy=None` — `ToolRunner` runs shell commands without the macOS `sandbox-exec` wrapper.
- `bg_store=None` — `check_background_agent` / `cancel_background_agent` are not registered, the chat loop's background-notification drain short-circuits, and the `run_in_background` field is **schema-level removed** from sub-agent tool definitions (the LLM cannot call a disabled feature). `/agent bg|dashboard|cancel|delete|logs|result` CLI subcommands short-circuit with a clear warning.

`agentao.embedding.build_from_environment()` constructs CLI defaults for all three (anchored to the session's working directory) and passes them explicitly, so CLI / ACP behavior is preserved. Embedded hosts that don't ask for these features pay zero cost.

### `agentao.embedding.build_from_environment`

```python
def build_from_environment(
    working_directory: Path | None = None,
    **overrides,
) -> Agentao:
    ...
```

CLI-style auto-discovery factory: reads `.env`, `LLM_PROVIDER`-prefixed env vars, `<wd>/.agentao/permissions.json`, `<wd>/.agentao/mcp.json`, memory roots; constructs `Agentao` with the discovered values. **Caller-supplied `**overrides` win** over auto-discovered ones. Raises `ValueError` on the same mutual-exclusion conflicts as the constructor (if `llm_client` is in `overrides`, the factory's discovered `api_key` / `base_url` / `model` are dropped before forwarding).

### Methods

| Method | Signature | Purpose |
|--------|-----------|---------|
| `chat` | `chat(user_message: str, max_iterations: int = 100, cancellation_token: CancellationToken | None = None) -> str` | Run one turn. Returns final assistant text. |
| `arun` | `async arun(user_message: str, max_iterations: int = 100, cancellation_token: CancellationToken | None = None) -> str` | Async surface — bridges `chat()` through `loop.run_in_executor`. Same semantics for cancellation, replay, max_iterations. |
| `clear_history` | `clear_history() -> None` | Reset `self.messages`; does not touch memory DB. |
| `close` | `close() -> None` | Release MCP subprocesses, close DB handles. Call in `finally:`. |
| `set_provider` | `set_provider(api_key: str, base_url: str | None = None, model: str | None = None) -> None` | Runtime LLM swap. |
| `set_model` | `set_model(model: str) -> str` | Swap model only; returns the previous id. |
| `events` (0.3.1+) | `events(session_id: str | None = None) -> AsyncIterator[HostEvent]` | Subscribe to public harness events (tool / sub-agent / permission lifecycle). No replay; bounded backpressure. See [A.10](#a-10-embedded-host-contract). |
| `active_permissions` (0.3.1+) | `active_permissions() -> ActivePermissions` | Snapshot of the active permission policy (`mode`, `rules`, `loaded_sources`). JSON-safe. See [A.10](#a-10-embedded-host-contract). |

### Attributes

| Attribute | Type | Notes |
|-----------|------|-------|
| `messages` | `list[dict]` | Conversation history in OpenAI chat format. Safe to read, mutate at your own risk. |
| `tools` | `ToolRegistry` | Call `agent.tools.register(MyTool())` after construction. |
| `skill_manager` | `SkillManager` | `agent.skill_manager.activate_skill(name, task_description)` to turn a skill on. |
| `transport` | `Transport` | The active transport; rebindable. |
| `_current_token` | `CancellationToken | None` | Public by convention; read to call `.cancel()` from another thread. |

## A.2 Transport layer

### `Transport` protocol

Runtime-checkable `Protocol`. Four methods, all optional via `NullTransport` fallback:

```python
def emit(self, event: AgentEvent) -> None: ...
def confirm_tool(self, tool_name: str, description: str, args: dict) -> bool: ...
def ask_user(self, question: str) -> str: ...
def on_max_iterations(self, count: int, messages: list) -> dict: ...
```

`on_max_iterations` returns `{"action": "continue" | "stop" | "new_instruction", "message"?: str}`.

### `SdkTransport`

Callback-based adapter — the typical SDK consumer choice:

```python
SdkTransport(
    on_event: Callable[[AgentEvent], None] | None = None,
    confirm_tool: Callable[[str, str, dict], bool] | None = None,
    ask_user: Callable[[str], str] | None = None,
    on_max_iterations: Callable[[int, list], dict] | None = None,
)
```

Any unset callback falls back to `NullTransport` behavior (allow, empty answer, stop).

### `build_compat_transport`

Helper that builds a `Transport` from the legacy per-callback constructor args. You rarely need this directly — `Agentao` uses it internally when you pass `confirmation_callback=` et al.

### `AgentEvent` / `EventType`

```python
@dataclass
class AgentEvent:
    type: EventType
    data: dict
```

`EventType` covers UI streaming, tool lifecycle, LLM-call metadata, replay observability, and runtime state changes. Full payload reference in [4.2](/en/part-4/2-agent-events).

## A.3 Tools

### `Tool` (abstract)

```python
class Tool(ABC):
    @property @abstractmethod
    def name(self) -> str: ...
    @property @abstractmethod
    def description(self) -> str: ...
    @property @abstractmethod
    def parameters(self) -> dict: ...       # JSON Schema

    @property
    def requires_confirmation(self) -> bool: return False
    @property
    def is_read_only(self) -> bool: return False

    @abstractmethod
    def execute(self, **kwargs) -> str: ...

    # populated by Agentao at registration time
    working_directory: Path | None
    output_callback: Callable[[str], None] | None
    filesystem: FileSystem | None
    shell: ShellExecutor | None

    def _get_fs(self) -> FileSystem: ...      # lazy LocalFileSystem fallback
    def _get_shell(self) -> ShellExecutor: ... # lazy LocalShellExecutor fallback
```

Custom tool subclasses that need to read/write files should call `self._get_fs()` rather than touching `pathlib` directly — that way the host's injected `FileSystem` (Docker, virtual FS, audit proxy) is honored.

### `ToolRegistry`

| Method | Purpose |
|--------|---------|
| `register(tool: Tool)` | Add a tool; warns if name collides |
| `get(name: str) -> Tool` | Raises `KeyError` with available-tools list |
| `list_tools() -> list[Tool]` | |
| `to_openai_format() -> list[dict]` | OpenAI function-calling schemas |

### Capabilities (`agentao.capabilities`)

Runtime-checkable `Protocol`s that file/search/shell tools route IO through. Hosts inject custom implementations via `Agentao(filesystem=..., shell=...)` to redirect through Docker exec, virtual filesystems, audit proxies, or remote runners. The package also exports byte-equivalent `LocalFileSystem` / `LocalShellExecutor` defaults.

```python
class FileSystem(Protocol):
    def read_bytes(self, path: Path) -> bytes: ...
    def read_partial(self, path: Path, n: int) -> bytes: ...
    def open_text(self, path: Path) -> Iterator[str]: ...      # streaming
    def write_text(self, path: Path, data: str, *, append: bool = False) -> None: ...
    def list_dir(self, path: Path) -> list[FileEntry]: ...
    def glob(self, base: Path, pattern: str, *, recursive: bool) -> list[Path]: ...
    def stat(self, path: Path) -> FileStat: ...
    def exists(self, path: Path) -> bool: ...
    def is_dir(self, path: Path) -> bool: ...
    def is_file(self, path: Path) -> bool: ...

class ShellExecutor(Protocol):
    def run(self, request: ShellRequest) -> ShellResult: ...
    def run_background(self, request: ShellRequest) -> BackgroundHandle: ...
```

Frozen dataclasses: `FileEntry(name, is_dir, is_file, size)`, `FileStat(size, mtime, is_dir, is_file)`, `ShellRequest(command, cwd, timeout, on_chunk, env)`, `ShellResult(returncode, stdout, stderr, timed_out)`, `BackgroundHandle(pid, pgid, command, cwd)`.

Hosts that cannot support real backgrounding may raise `NotImplementedError` from `run_background` — `ShellTool` surfaces that as a normal tool error string.

## A.4 Permissions

### `PermissionMode`

```python
class PermissionMode(Enum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    FULL_ACCESS = "full-access"
    PLAN = "plan"
```

### `PermissionDecision`

```python
class PermissionDecision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
```

### `PermissionEngine`

```python
PermissionEngine(
    project_root: Path,
    *,
    user_root: Path | None = None,
)
```

Both arguments are explicit since 0.2.16 — passing `project_root=None` (or omitting it) raises `TypeError`. Project rules load from `<project_root>/.agentao/permissions.json`; user rules load from `<user_root>/permissions.json` (typically `~/.agentao/permissions.json` if `user_root=Path.home() / ".agentao"`). Pass `user_root=None` to disable user-scope rules entirely. Switch the preset mode after construction with `set_mode()`.

| Method | Signature | Purpose |
|--------|-----------|---------|
| `decide` | `decide(tool_name: str, tool_args: dict) -> PermissionDecision | None` | `None` means "fall through to caller default" |
| `set_mode` | `set_mode(mode: PermissionMode) -> None` | Switch active preset (`READ_ONLY`, `WORKSPACE_WRITE`, `FULL_ACCESS`, `PLAN`) |
| `active_mode` | attribute (read) | Currently active `PermissionMode` |

Subclass and override `decide()` to integrate company IAM — see [7.3](/en/part-7/3-ticket-automation) for a confidence-gated example.

## A.5 Memory

### `MemoryManager` (since 0.3.0 / #16)

```python
MemoryManager(
    project_store: MemoryStore,                 # required, pre-built
    user_store: MemoryStore | None = None,      # cross-project; None disables user scope
    guard: MemoryGuard | None = None,
)
```

The constructor takes pre-built `MemoryStore` instances (the embedding factory builds them and passes them in). Path-based construction moved to the store layer:

```python
from agentao.memory import MemoryManager, SQLiteMemoryStore

mgr = MemoryManager(
    project_store=SQLiteMemoryStore.open_or_memory(workdir / ".agentao" / "memory.db"),
    user_store=SQLiteMemoryStore.open(home / ".agentao" / "memory.db"),
)
```

- `project_store` — required `MemoryStore` for project-scoped memories (the factory uses `SQLiteMemoryStore.open_or_memory(...)` so a missing directory degrades to `:memory:` instead of crashing)
- `user_store` — optional cross-project store (the factory uses `SQLiteMemoryStore.open(...)`; `None` disables user-scope memory entirely; user-scope writes are downgraded to project on `None`)
- `guard` — optional `MemoryGuard` that filters sensitive content before persistence

| Method | Purpose |
|--------|---------|
| `upsert(req)` | Insert / update a memory record |
| `save_from_tool(...)` | Called by the `save_memory` tool |
| `get_entry(id)` / `get_all_entries(scope=?)` | Read entries |
| `search(query, scope=?)` / `filter_by_tag(tag, scope=?)` | Search |
| `delete(id)` / `delete_by_title(title)` | Soft delete |
| `clear(scope=?)` | Soft-delete all in scope |
| `save_session_summary(...)` / `get_recent_session_summaries(...)` | Used by the compaction pipeline |
| `archive_session() / clear_session()` | End-of-session house-keeping |
| `clear_all_session_summaries()` | Drop every session summary across all sessions |
| `get_stable_entries(...)` | Render into `<memory-stable>` system-prompt block |

### `MemoryStore` (Protocol, 0.3.0 / #16)

```python
class MemoryStore(Protocol):
    # Memory CRUD
    def upsert_memory(self, record: MemoryRecord) -> MemoryRecord: ...
    def get_memory_by_id(self, memory_id: str) -> Optional[MemoryRecord]: ...
    def get_memory_by_scope_key(self, scope: str, key_normalized: str) -> Optional[MemoryRecord]: ...
    def list_memories(self, scope: Optional[str] = None) -> List[MemoryRecord]: ...
    def search_memories(self, query: str, scope: Optional[str] = None) -> List[MemoryRecord]: ...
    def filter_by_tag(self, tag: str, scope: Optional[str] = None) -> List[MemoryRecord]: ...
    def soft_delete_memory(self, memory_id: str) -> bool: ...
    def clear_memories(self, scope: Optional[str] = None) -> int: ...
    # Session summaries
    def save_session_summary(self, record: SessionSummaryRecord) -> None: ...
    def list_session_summaries(self, session_id=None, limit=20) -> List[SessionSummaryRecord]: ...
    def clear_session_summaries(self, session_id=None) -> int: ...
    # Review queue
    def upsert_review_item(self, item: MemoryReviewItem) -> MemoryReviewItem: ...
    def get_review_item(self, item_id: str) -> Optional[MemoryReviewItem]: ...
    def list_review_items(self, status="pending", limit=50) -> List[MemoryReviewItem]: ...
    def update_review_status(self, item_id: str, status: str) -> bool: ...
```

15 methods, schema-less. Implement the Protocol to back memory with Redis / Postgres / in-process dict / remote API. Default `SQLiteMemoryStore` is byte-equivalent to the pre-Protocol implementation.

### `SQLiteMemoryStore`

```python
SQLiteMemoryStore.open(path)              # strict; raises on disk error
SQLiteMemoryStore.open_or_memory(path)    # graceful; degrades to ":memory:"
```

The classmethods replace the historical `MemoryManager.__init__` try/except. Use `open_or_memory` for the project store (a missing DB beats a crashed agent), `open` for the user store (a failure should disable the scope, not silently re-route writes).

## A.6 MCP

### `MCPRegistry` (Protocol, 0.3.0 / #17)

```python
class MCPRegistry(Protocol):
    def list_servers(self) -> Dict[str, McpServerConfig]: ...
```

Source of MCP server configs. The `Agentao(mcp_registry=...)` kwarg is the injection point; `mcp_manager=` and `mcp_registry=` are mutually exclusive (manager = construction outcome, registry = config source).

| Concrete class | Purpose |
|---|---|
| `FileBackedMCPRegistry(project_root, user_root=None)` | CLI/ACP default. Reads `<wd>/.agentao/mcp.json` + `<user_root>/mcp.json` on every `list_servers()`. |
| `InMemoryMCPRegistry(servers=None)` | Programmatic counterpart for tests / embedded hosts. Constructor input is shallow-copied. |

## A.7 Cancellation

### `CancellationToken`

```python
token = CancellationToken()
token.is_cancelled         # bool, non-throwing
token.cancel("reason")     # idempotent
token.check()              # raises AgentCancelledError if cancelled
token.reason               # str
```

Pass to `agent.chat(..., cancellation_token=token)` to abort from another thread / async task. See [2.3](/en/part-2/3-lifecycle) for the timeout pattern.

### `AgentCancelledError`

Raised inside the agent loop when a token is cancelled. Caught by `chat()`, which returns a `[Cancelled: reason]` string rather than propagating.

## A.8 ACP client

### `ACPManager`

Typical host-side entry point for driving external ACP servers declared in `.agentao/acp.json`.
In unattended environments (CI / workers / cron / queue consumers), this same object is what the guide calls the **Headless Runtime**: not a new protocol or class, just an operating profile of `ACPManager`.

| Method | Signature | Purpose |
|--------|-----------|---------|
| `from_project` | `@classmethod from_project(project_root=None) -> ACPManager` | Read `.agentao/acp.json` |
| `server_names` | `-> list[str]` | Declared servers |
| `start_all` | `start_all(only_auto=True)` | Spawn all auto-start servers |
| `start_server(name)` / `stop_server(name)` / `restart_server(name)` | | |
| `ensure_connected(name, cwd=?, mcp_servers=?)` | | Idempotent connect + session |
| `send_prompt(name, prompt, timeout=?)` | `-> PromptResult` | Interactive turn (public) |
| `prompt_once(name, prompt, cwd=?, mcp_servers=?, timeout=?, interactive=False, stop_process=True)` | `-> PromptResult` | Fail-fast one-shot, cleans up (public) |
| `send_prompt_nonblocking` / `finish_prompt_nonblocking` / `cancel_prompt_nonblocking` | | Lower-level async variants (**internal / unstable** — not part of the embedding contract) |
| `stop_all()` | | Shut down all subprocesses |
| `get_status()` | `-> list[ServerStatus]` | Typed headless snapshot (Week 1 frozen + Week 2 additive fields) |
| `readiness(name)` | `-> Literal["ready","busy","failed","not_ready"]` | Typed classification of state × active-turn (Week 2) |
| `is_ready(name)` | `-> bool` | Shortcut for `readiness(name) == "ready"` |
| `reset_last_error(name)` | | Clear recorded `last_error` / `last_error_at` on the manager |
| `get_client(name)` / `get_handle(name)` | | Introspection |
| `config` (property) | `-> AcpClientConfig` | |

### `PromptResult`

```python
@dataclass
class PromptResult:
    stop_reason: str
    raw: Any
    session_id: str | None
    cwd: str | None
```

### `ServerState`

```python
class ServerState(str, Enum):
    CONFIGURED = "configured"
    STARTING = "starting"
    INITIALIZING = "initializing"
    READY = "ready"
    BUSY = "busy"
    WAITING_FOR_USER = "waiting_for_user"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
```

### `ServerStatus`

Typed return type of `ACPManager.get_status()`. Week 1 core fields are
frozen; Week 2 added diagnostic fields additively.

```python
@dataclass(frozen=True)
class ServerStatus:
    # Week 1 — core (frozen)
    server: str
    state: str
    pid: Optional[int]
    has_active_turn: bool

    # Week 2 — diagnostics (additive)
    active_session_id: Optional[str] = None
    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None   # tz-aware, UTC
    inbox_pending: int = 0
    interaction_pending: int = 0
    config_warnings: List[str] = field(default_factory=list)
```

Week 2 field notes:

- `last_error` is **sticky** across successful turns; clear with
  `reset_last_error(name)`.
- `last_error_at` is assigned **at store time** (inside the manager),
  not at raise time. Use it for staleness judgements.
- `SERVER_BUSY` and `SERVER_NOT_FOUND` are **not** recorded — they are
  caller-side signals.

See [`docs/features/headless-runtime.md`](../../../docs/features/headless-runtime.md)
for the full state-vs-error contract and the readiness classifier.

### Exception classes

| Class | `code` | Notes |
|-------|--------|-------|
| `AcpClientError` | any `AcpErrorCode` | Base |
| `AcpServerNotFound` | `SERVER_NOT_FOUND` | Also inherits `KeyError` |
| `AcpRpcError` | `.rpc_code: int` + `.error_code: PROTOCOL_ERROR` | JSON-RPC response error |
| `AcpInteractionRequiredError` | `INTERACTION_REQUIRED` | Carries `prompt` + `options` |

See [Appendix D](./d-error-codes) for the full code table.

### `load_acp_client_config`

```python
load_acp_client_config(project_root: Path | None = None) -> AcpClientConfig
```

When `project_root` is `None`, it defaults to `Path.cwd()`.

Validate + load `.agentao/acp.json` without constructing a manager. Useful for config-lint tooling.

## A.9 Skills

### `SkillManager`

Loaded lazily via `from agentao import SkillManager`. Most callers reach it via `agent.skill_manager`.

| Method | Purpose |
|--------|---------|
| `list_available_skills() -> list[str]` | Names currently discoverable |
| `list_all_skills() -> list[str]` | Includes disabled |
| `get_skill_info(name) -> dict | None` | Returns `{name, description, path, ...}` |
| `activate_skill(name, task_description)` | Turn on — injects SKILL.md + active reference files into system prompt |
| `enable_skill(name)` / `disable_skill(name)` | Persistent enable/disable in config |

## A.10 Embedded Host Contract

*Stable since 0.3.1. Package renamed `agentao.harness` → `agentao.host` in 0.4.2.*

The `agentao.host` package is the **stable host-facing API surface** for embedding Agentao. Internal runtime types (`AgentEvent`, `ToolExecutionResult`, `PermissionEngine`) are intentionally not part of this surface — they may change in any release. Hosts that target only `agentao.host` (plus the `Agentao(...)` constructor and the methods marked above) stay forward-compatible.

> **Naming.** "Harness" still refers to *Agentao itself running embedded in a host application* (the conceptual framing in `docs/design/embedded-host-contract.md`); the contract package was renamed to `agentao.host` to make `from agentao.host import HostEvent` read consistently. The old `agentao.harness` import path and old symbol names (`HarnessEvent`, `HarnessReplaySink`, `export_harness_*`) remain as a deprecated alias through 0.5.0 and emit one `DeprecationWarning` on first import.

Full reference: [`docs/api/host.md`](../../../docs/api/host.md). Design rationale: [`docs/design/embedded-host-contract.md`](../../../docs/design/embedded-host-contract.md).

### Public exports

```python
from agentao.host import (
    ActivePermissions,
    EventStream,
    StreamSubscribeError,
    HostEvent,
    ToolLifecycleEvent,
    SubagentLifecycleEvent,
    PermissionDecisionEvent,
    RFC3339UTCString,
    export_host_event_json_schema,
    export_host_acp_json_schema,
)
```

| Symbol | Purpose |
|---|---|
| `ActivePermissions` | Read-only snapshot of the active permission policy (`mode`, `rules`, `loaded_sources`). |
| `ToolLifecycleEvent` | Public envelope for one tool call. `phase ∈ {started, completed, failed}`; cancellation surfaces as `phase="failed", outcome="cancelled"`. |
| `SubagentLifecycleEvent` | Lineage fact for a sub-agent task/session. `phase ∈ {spawned, completed, failed, cancelled}` — `cancelled` is a distinct phase here. |
| `PermissionDecisionEvent` | Per-decision projection. Fires on `allow` / `deny` / `prompt`; consumers must drain even allow events. |
| `HostEvent` | Discriminated union of the three event models (Pydantic discriminator: `event_type`). |
| `RFC3339UTCString` | Constrained timestamp type. Canonical `Z` suffix only — `+00:00` offsets are rejected. |
| `EventStream` | Runtime side of `Agentao.events()`. Producers call `publish()`; consumers iterate `subscribe()`. |
| `StreamSubscribeError` | Raised when a second concurrent subscriber for the same `session_id` filter is requested (MVP supports one stream consumer per `Agentao` instance). |
| `export_host_event_json_schema()` | Emit the canonical JSON schema for events + permissions. Used by `tests/test_host_schema.py` for byte-equality against `docs/schema/host.events.v1.json`. |
| `export_host_acp_json_schema()` | Emit the canonical JSON schema for host-facing ACP payloads. Snapshot lives at `docs/schema/host.acp.v1.json`. |

### `agent.events(session_id=None)`

Returns an async iterator over `HostEvent`. Pass `session_id=` to filter; pass `None` to subscribe to every session owned by this `Agentao` instance.

```python
async for ev in agent.events():
    if isinstance(ev, ToolLifecycleEvent):
        ...
    elif isinstance(ev, PermissionDecisionEvent):
        ...
    elif isinstance(ev, SubagentLifecycleEvent):
        ...
```

Delivery contract:

- Same-session ordering is guaranteed; cross-session global ordering is not.
- Within one `tool_call_id`, `PermissionDecisionEvent` precedes `ToolLifecycleEvent(phase="started")`.
- **No replay.** Events emitted before the first subscription are dropped.
- Backpressure is host-pulled via a bounded queue — slow consumers block the producer for matching events rather than dropping them.
- Cancelling the iterator releases queue/subscription resources.
- MVP supports one public stream consumer per `Agentao` instance.

### `agent.active_permissions()`

Returns a JSON-safe `ActivePermissions` snapshot:

```python
snap = agent.active_permissions()
# snap.mode            -> "workspace-write" (Literal-typed)
# snap.rules           -> list[dict]
# snap.loaded_sources  -> ["preset:workspace-write",
#                          "project:.agentao/permissions.json",
#                          "user:/Users/me/.agentao/permissions.json",
#                          "injected:host"]
```

`loaded_sources` carries stable string labels: `preset:<mode>`, `project:<path>`, `user:<path>`, `injected:<name>`. The MVP does **not** expose per-rule provenance — hosts that need it combine `loaded_sources` with their own injected policy metadata.

If no `permission_engine` is configured, the runtime returns a permissive fallback: `mode="workspace-write"`, empty `rules`, `loaded_sources=["default:no-engine"]`. The label tells hosts they're seeing the engine-less fallback rather than a configured policy.

Hosts that layer policy on top of the engine call `agent.permission_engine.add_loaded_source("injected:<name>")` so the snapshot reflects their provenance. The cache is invalidated on `set_mode()` and `add_loaded_source()`.

### Schema snapshots

Each release ships checked-in JSON schema snapshots:

- [`docs/schema/host.events.v1.json`](../../../docs/schema/host.events.v1.json) — events + permissions surface
- [`docs/schema/host.acp.v1.json`](../../../docs/schema/host.acp.v1.json) — host-facing ACP payloads

`tests/test_host_schema.py` regenerates the schemas from the Pydantic models and asserts byte-equality. A model change that shifts the wire form must update both the model and the snapshot in the same PR. Adding an optional field is backwards-compatible; removing or renaming requires a schema version bump.

### Non-goals (explicitly outside the contract)

- Public agent graph / descendants store API
- Host-facing hooks list/disable API
- Host-facing MCP reload / lifecycle events
- Local plugin export/import; remote plugin share
- External session import
- Generated client SDKs

The CLI may build on the same events for its own UI, but its stores and commands are not promoted to the host contract.

---

→ [Appendix F · FAQ](./f-faq)
