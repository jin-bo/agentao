# Appendix A · API Reference

This is a **surface reference** — the public symbols that form Agentao's embedding contract. Anything not listed here is an implementation detail and may change without notice. For behavior and examples, follow the links back to the main guide.

Authoritative `__all__`:

- `from agentao import ...` → `Agentao`, `SkillManager`
- `from agentao.transport import ...` → `AgentEvent`, `EventType`, `Transport`, `NullTransport`, `SdkTransport`, `build_compat_transport`
- `from agentao.tools.base import ...` → `Tool`, `ToolRegistry`
- `from agentao.permissions import ...` → `PermissionEngine`, `PermissionMode`, `PermissionDecision`
- `from agentao.memory.manager import MemoryManager`
- `from agentao.cancellation import ...` → `CancellationToken`, `AgentCancelledError`
- `from agentao.acp_client import ...` → `ACPManager`, `ACPClient`, `AcpClientError`, `AcpErrorCode`, `AcpRpcError`, `AcpInteractionRequiredError`, `AcpClientConfig`, `AcpServerConfig`, `AcpConfigError`, `PromptResult`, `ServerState`, `load_acp_client_config` (and lower-level re-exports — see `agentao.acp_client.__init__.py` docstring for which are "stable embedding surface" vs. "implementation detail")

## A.1 `Agentao`

The core class — see [Part 2.2](/en/part-2/2-constructor-reference) for the full constructor parameter table.

### Constructor

```python
Agentao(
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    transport: Transport | None = None,
    working_directory: Path | None = None,
    extra_mcp_servers: list[dict] | None = None,
    permission_engine: PermissionEngine | None = None,
    max_context_tokens: int = 200_000,
    plan_session: bool = False,
    # legacy callbacks (prefer `transport=`)
    output_callback: Callable[[str], None] | None = None,
    confirmation_callback: Callable[[str, str, dict], bool] | None = None,
    input_callback: Callable[[str], str] | None = None,
    on_max_iterations_callback: Callable[[int, list], dict] | None = None,
    # ...
)
```

### Methods

| Method | Signature | Purpose |
|--------|-----------|---------|
| `chat` | `chat(user_message: str, max_iterations: int = 100, cancellation_token: CancellationToken | None = None) -> str` | Run one turn. Returns final assistant text. |
| `clear_history` | `clear_history() -> None` | Reset `self.messages`; does not touch memory DB. |
| `close` | `close() -> None` | Release MCP subprocesses, close DB handles. Call in `finally:`. |
| `set_provider` | `set_provider(api_key: str, base_url: str | None = None, model: str | None = None) -> None` | Runtime LLM swap. |
| `set_model` | `set_model(model: str) -> str` | Swap model only; returns the previous id. |

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

Ten `EventType` values — full payload reference in [4.2](/en/part-4/2-agent-events).

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
```

### `ToolRegistry`

| Method | Purpose |
|--------|---------|
| `register(tool: Tool)` | Add a tool; warns if name collides |
| `get(name: str) -> Tool` | Raises `KeyError` with available-tools list |
| `list_tools() -> list[Tool]` | |
| `to_openai_format() -> list[dict]` | OpenAI function-calling schemas |

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
PermissionEngine(*, project_root: Path | None = None)
```

Rules are auto-loaded from `<project_root>/.agentao/permissions.json` + `~/.agentao/permissions.json` at construction time. Switch the preset mode after construction with `set_mode()`.

| Method | Signature | Purpose |
|--------|-----------|---------|
| `decide` | `decide(tool_name: str, tool_args: dict) -> PermissionDecision | None` | `None` means "fall through to caller default" |
| `set_mode` | `set_mode(mode: PermissionMode) -> None` | Switch active preset (`READ_ONLY`, `WORKSPACE_WRITE`, `FULL_ACCESS`, `PLAN`) |
| `active_mode` | attribute (read) | Currently active `PermissionMode` |

Subclass and override `decide()` to integrate company IAM — see [7.3](/en/part-7/3-ticket-automation) for a confidence-gated example.

## A.5 Memory

### `MemoryManager`

```python
MemoryManager(
    project_root: Path,
    global_root: Path | None = None,
    guard: MemoryGuard | None = None,
)
```

- `project_root` — directory where `memory.db` for project-scoped memories lives (usually `<cwd>/.agentao`)
- `global_root` — directory for the cross-project user-scoped DB (usually `~/.agentao`); `None` disables user-scope memory
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
| `get_stable_entries(...)` | Render into `<memory-stable>` system-prompt block |

## A.6 Cancellation

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

## A.7 ACP client

### `ACPManager`

Typical host-side entry point for driving external ACP servers declared in `.agentao/acp.json`.

| Method | Signature | Purpose |
|--------|-----------|---------|
| `from_project` | `@classmethod from_project(project_root=None) -> ACPManager` | Read `.agentao/acp.json` |
| `server_names` | `-> list[str]` | Declared servers |
| `start_all` | `start_all(only_auto=True)` | Spawn all auto-start servers |
| `start_server(name)` / `stop_server(name)` / `restart_server(name)` | | |
| `ensure_connected(name, cwd=?, mcp_servers=?)` | | Idempotent connect + session |
| `send_prompt(name, prompt, timeout=?)` | `-> PromptResult` | Interactive turn |
| `prompt_once(name, prompt, cwd=?, mcp_servers=?, timeout=?, interactive=False, stop_process=True)` | `-> PromptResult` | Fail-fast one-shot, cleans up |
| `send_prompt_nonblocking` / `finish_prompt_nonblocking` / `cancel_prompt_nonblocking` | | Lower-level async variants |
| `stop_all()` | | Shut down all subprocesses |
| `get_status()` / `get_client(name)` / `get_handle(name)` | | Introspection |
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

## A.8 Skills

### `SkillManager`

Loaded lazily via `from agentao import SkillManager`. Most callers reach it via `agent.skill_manager`.

| Method | Purpose |
|--------|---------|
| `list_available_skills() -> list[str]` | Names currently discoverable |
| `list_all_skills() -> list[str]` | Includes disabled |
| `get_skill_info(name) -> dict | None` | Returns `{name, description, path, ...}` |
| `activate_skill(name, task_description)` | Turn on — injects SKILL.md + active reference files into system prompt |
| `enable_skill(name)` / `disable_skill(name)` | Persistent enable/disable in config |

---

→ [Appendix F · FAQ](./f-faq)
