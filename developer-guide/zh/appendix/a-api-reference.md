# 附录 A · API 参考

这是一份**面向表层**的参考——Agentao 嵌入契约里的公开符号。未在此列出的都算实现细节，可能变动。行为与示例请回跳到正文相应章节。

权威 `__all__`：

- `from agentao import ...` → `Agentao`、`SkillManager`
- `from agentao.embedding import ...` → `build_from_environment`
- `from agentao.transport import ...` → `AgentEvent`、`EventType`、`Transport`、`NullTransport`、`SdkTransport`、`build_compat_transport`
- `from agentao.capabilities import ...` → `FileSystem`、`LocalFileSystem`、`FileEntry`、`FileStat`、`ShellExecutor`、`LocalShellExecutor`、`ShellRequest`、`ShellResult`、`BackgroundHandle`、`MemoryStore`、`SQLiteMemoryStore`、`MCPRegistry`、`FileBackedMCPRegistry`、`InMemoryMCPRegistry`
- `from agentao.tools.base import ...` → `Tool`、`ToolRegistry`
- `from agentao.permissions import ...` → `PermissionEngine`、`PermissionMode`、`PermissionDecision`
- `from agentao.memory.manager import MemoryManager`
- `from agentao.cancellation import ...` → `CancellationToken`、`AgentCancelledError`
- `from agentao.acp_client import ...` → `ACPManager`、`ACPClient`、`AcpClientError`、`AcpErrorCode`、`AcpRpcError`、`AcpInteractionRequiredError`、`AcpClientConfig`、`AcpServerConfig`、`AcpConfigError`、`PromptResult`、`ServerState`、`load_acp_client_config`（以及更底层的 re-export——哪些属于"稳定嵌入面"、哪些属于"实现细节"请参考 `agentao.acp_client.__init__.py` 的 docstring）
- `from agentao.host import ...` → `ActivePermissions`、`EventStream`、`StreamSubscribeError`、`HostEvent`、`ToolLifecycleEvent`、`SubagentLifecycleEvent`、`PermissionDecisionEvent`、`RFC3339UTCString`、`export_host_event_json_schema`、`export_host_acp_json_schema` —— 宿主面 harness 合约，详见 [A.10](#a-10-嵌入-harness-合约)

## A.1 `Agentao`

核心类——完整构造参数表见 [Part 2.2](/zh/part-2/2-constructor-reference)。

### 构造器

完整参数表见 [Part 2.2](/zh/part-2/2-constructor-reference)。**0.3.0 起**，不传 `working_directory=` 调用 `Agentao()` 会从 Python 签名分派直接抛 `TypeError`——软废弃周期已结束。完整嵌入式接入实践见 [`docs/EMBEDDING.md`](../../../docs/EMBEDDING.md)。

```python
Agentao(
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    transport: Transport | None = None,
    *,
    working_directory: Path,                     # 0.3.0 起必传
    extra_mcp_servers: dict[str, dict] | None = None,
    permission_engine: PermissionEngine | None = None,
    max_context_tokens: int = 200_000,
    plan_session: PlanSession | None = None,
    # 嵌入式 harness 显式注入
    llm_client: LLMClient | None = None,
    logger: logging.Logger | None = None,
    memory_manager: MemoryManager | None = None,
    skill_manager: SkillManager | None = None,
    project_instructions: str | None = None,
    mcp_manager: McpClientManager | None = None,
    mcp_registry: MCPRegistry | None = None,     # 0.3.0+ (#17)
    filesystem: FileSystem | None = None,
    shell: ShellExecutor | None = None,
    # 可选启用的子系统（None = 完全禁用）
    replay_config: ReplayConfig | None = None,
    sandbox_policy: SandboxPolicy | None = None,
    bg_store: BackgroundTaskStore | None = None,
    # 老式回调（建议改用 `transport=`）
    output_callback: Callable[[str], None] | None = None,
    confirmation_callback: Callable[[str, str, dict], bool] | None = None,
    ask_user_callback: Callable[[str], str] | None = None,
    on_max_iterations_callback: Callable[[int, list], dict] | None = None,
    # ...
)
```

互斥规则（违反时抛 `ValueError`）：

- `llm_client=` 与任何 `api_key=` / `base_url=` / `model=` / `temperature=` 同时传
- `mcp_manager=` 与 `extra_mcp_servers=` 同时传
- `mcp_manager=` 与 `mcp_registry=` 同时传——registry 是配置源，manager 是构造结果

可选子系统语义（默认 `None`）：

- `replay_config=None` —— 构造时不读 `<wd>/.agentao/replay.json`，内部用 no-op 的 `ReplayConfig()`。
- `sandbox_policy=None` —— `ToolRunner` 跑 shell 时不再走 macOS `sandbox-exec` 包装。
- `bg_store=None` —— `check_background_agent` / `cancel_background_agent` 不注册，chat loop 后台通知 drain 短路，子 agent 工具定义里 `run_in_background` 字段在 **schema 层被移除**（LLM 看不到、就不会调用一个被禁用的能力）。`/agent bg|dashboard|cancel|delete|logs|result` 这几个 CLI 子命令也会短路，并打印明确的提示。

`agentao.embedding.build_from_environment()` 会按 CLI 默认行为构造这三个对象（都锚定到当前 session 的工作目录），然后显式传入，所以 CLI / ACP 行为保持不变。嵌入式 host 不主动启用就不会有任何开销。

### `agentao.embedding.build_from_environment`

```python
def build_from_environment(
    working_directory: Path | None = None,
    **overrides,
) -> Agentao:
    ...
```

CLI 风格的自动发现工厂：读 `.env`、`LLM_PROVIDER` 前缀的 env 变量、`<wd>/.agentao/permissions.json`、`<wd>/.agentao/mcp.json`、内存目录，然后用发现到的值构造 `Agentao`。**调用方传入的 `**overrides` 优先**。同样会触发上面构造器的互斥校验（如果 `overrides` 含 `llm_client`，工厂会先把发现到的 `api_key` / `base_url` / `model` 丢掉再转发）。

### 方法

| 方法 | 签名 | 作用 |
|------|------|------|
| `chat` | `chat(user_message: str, max_iterations: int = 100, cancellation_token: CancellationToken | None = None) -> str` | 跑一轮，返回助手最终文本 |
| `arun` | `async arun(user_message: str, max_iterations: int = 100, cancellation_token: CancellationToken | None = None) -> str` | 异步接口——通过 `loop.run_in_executor` 桥到 `chat()`。取消、replay、`max_iterations` 语义与同步版完全一致 |
| `clear_history` | `clear_history() -> None` | 清 `self.messages`；不影响 memory DB |
| `close` | `close() -> None` | 关 MCP 子进程与 DB handle；请放 `finally:` |
| `set_provider` | `set_provider(api_key, base_url=None, model=None) -> None` | 运行时换 LLM |
| `set_model` | `set_model(model: str) -> str` | 只换模型；返回旧 id |
| `events` (0.3.1+) | `events(session_id: str | None = None) -> AsyncIterator[HostEvent]` | 订阅公共 harness 事件（工具/子 Agent/权限决定生命周期）。无 replay；有界背压。详见 [A.10](#a-10-嵌入-harness-合约) |
| `active_permissions` (0.3.1+) | `active_permissions() -> ActivePermissions` | 当前权限策略快照（`mode`、`rules`、`loaded_sources`），JSON-safe。详见 [A.10](#a-10-嵌入-harness-合约) |

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `messages` | `list[dict]` | OpenAI chat 格式的历史；可读，修改风险自担 |
| `tools` | `ToolRegistry` | 构造后调 `agent.tools.register(MyTool())` |
| `skill_manager` | `SkillManager` | `agent.skill_manager.activate_skill(name, task_description)` 激活技能 |
| `transport` | `Transport` | 当前传输层；可重新赋值 |
| `_current_token` | `CancellationToken | None` | 按约定公开；其他线程可读到它并 `.cancel()` |

## A.2 传输层

### `Transport` 协议

Runtime-checkable `Protocol`，四个方法，通过 `NullTransport` 兜底，都可选实现：

```python
def emit(self, event: AgentEvent) -> None: ...
def confirm_tool(self, tool_name: str, description: str, args: dict) -> bool: ...
def ask_user(self, question: str) -> str: ...
def on_max_iterations(self, count: int, messages: list) -> dict: ...
```

`on_max_iterations` 返回 `{"action": "continue" | "stop" | "new_instruction", "message"?: str}`。

### `SdkTransport`

基于回调的适配器——SDK 使用方的首选：

```python
SdkTransport(
    on_event: Callable[[AgentEvent], None] | None = None,
    confirm_tool: Callable[[str, str, dict], bool] | None = None,
    ask_user: Callable[[str], str] | None = None,
    on_max_iterations: Callable[[int, list], dict] | None = None,
)
```

未设置的回调会回落到 `NullTransport` 行为（放行 / 空串 / 停止）。

### `build_compat_transport`

从老式分立回调参数构造 `Transport` 的 helper。一般不会直接用——`Agentao` 在你传 `confirmation_callback=` 等参数时会自动调。

### `AgentEvent` / `EventType`

```python
@dataclass
class AgentEvent:
    type: EventType
    data: dict
```

`EventType` 覆盖 UI 流式输出、工具生命周期、LLM 调用元数据、replay 可观测性和运行时状态变化。payload 完整参考见 [4.2](/zh/part-4/2-agent-events)。

## A.3 工具

### `Tool`（抽象）

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

    # 注册时由 Agentao 注入
    working_directory: Path | None
    output_callback: Callable[[str], None] | None
    filesystem: FileSystem | None
    shell: ShellExecutor | None

    def _get_fs(self) -> FileSystem: ...      # 没注入时 lazy 构造 LocalFileSystem
    def _get_shell(self) -> ShellExecutor: ... # 没注入时 lazy 构造 LocalShellExecutor
```

自定义 Tool 子类需要读写文件时，调用 `self._get_fs()` 而不是直接用 `pathlib`——这样宿主注入的 `FileSystem`（Docker、虚拟 FS、审计代理）会被正确尊重。

### `ToolRegistry`

| 方法 | 作用 |
|------|------|
| `register(tool: Tool)` | 注册；同名会警告 |
| `get(name: str) -> Tool` | 不存在时抛 `KeyError`，带可用工具列表 |
| `list_tools() -> list[Tool]` | |
| `to_openai_format() -> list[dict]` | OpenAI function-calling schemas |

### Capabilities（`agentao.capabilities`）

文件 / 搜索 / Shell 工具的 IO 都路由经过这两个 runtime-checkable `Protocol`。宿主通过 `Agentao(filesystem=..., shell=...)` 注入自定义实现，可以重定向到 Docker exec、虚拟文件系统、审计代理或远程 runner。包还导出与 0.2.16 之前字节级一致的默认实现 `LocalFileSystem` / `LocalShellExecutor`。

```python
class FileSystem(Protocol):
    def read_bytes(self, path: Path) -> bytes: ...
    def read_partial(self, path: Path, n: int) -> bytes: ...
    def open_text(self, path: Path) -> Iterator[str]: ...      # 流式读取
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

冻结的 dataclass：`FileEntry(name, is_dir, is_file, size)`、`FileStat(size, mtime, is_dir, is_file)`、`ShellRequest(command, cwd, timeout, on_chunk, env)`、`ShellResult(returncode, stdout, stderr, timed_out)`、`BackgroundHandle(pid, pgid, command, cwd)`。

宿主无法支持真正的后台执行时，可以在 `run_background` 抛 `NotImplementedError`——`ShellTool` 会把它呈现为普通的工具错误字符串。

## A.4 权限

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

0.2.16 起两个参数都显式传——`project_root=None`（或不传）会抛 `TypeError`。项目规则从 `<project_root>/.agentao/permissions.json` 加载；用户规则从 `<user_root>/permissions.json` 加载（一般 `user_root=Path.home() / ".agentao"`，对应 `~/.agentao/permissions.json`）。传 `user_root=None` 可完全禁用用户态规则。构造后用 `set_mode()` 切换预设模式。

| 方法 | 签名 | 作用 |
|------|------|------|
| `decide` | `decide(tool_name: str, tool_args: dict) -> PermissionDecision | None` | `None` 表示"回到调用方默认" |
| `set_mode` | `set_mode(mode: PermissionMode) -> None` | 切换预设（`READ_ONLY`、`WORKSPACE_WRITE`、`FULL_ACCESS`、`PLAN`） |
| `active_mode` | 属性（只读） | 当前生效的 `PermissionMode` |

继承并 override `decide()` 可对接企业 IAM——确信度门控范例见 [7.3](/zh/part-7/3-ticket-automation)。

## A.5 记忆

### `MemoryManager`（0.3.0 / #16 起）

```python
MemoryManager(
    project_store: MemoryStore,                   # 必传，预先构造好的 store
    user_store: MemoryStore | None = None,        # 跨项目；None 表示禁用用户作用域
    guard: MemoryGuard | None = None,
)
```

构造器接收预先构造好的 `MemoryStore` 实例（嵌入式工厂负责构造并传入）。基于路径的构造下沉到 store 层：

```python
from agentao.memory import MemoryManager, SQLiteMemoryStore

mgr = MemoryManager(
    project_store=SQLiteMemoryStore.open_or_memory(workdir / ".agentao" / "memory.db"),
    user_store=SQLiteMemoryStore.open(home / ".agentao" / "memory.db"),
)
```

- `project_store` —— 必传的项目作用域 `MemoryStore`（工厂用 `SQLiteMemoryStore.open_or_memory(...)`，目录写不进去会降级到 `:memory:` 而不是炸掉）
- `user_store` —— 可选的跨项目 store（工厂用 `SQLiteMemoryStore.open(...)`；传 `None` 完全禁用用户作用域，user-scope 写入会下移到 project）
- `guard` —— 可选 `MemoryGuard`，落盘前过滤敏感内容

| 方法 | 作用 |
|------|------|
| `upsert(req)` | 新增/更新一条记忆 |
| `save_from_tool(...)` | `save_memory` 工具内部调用 |
| `get_entry(id)` / `get_all_entries(scope=?)` | 读 |
| `search(query, scope=?)` / `filter_by_tag(tag, scope=?)` | 搜索 |
| `delete(id)` / `delete_by_title(title)` | 软删除 |
| `clear(scope=?)` | 整个作用域软删除 |
| `save_session_summary(...)` / `get_recent_session_summaries(...)` | 压缩流水线用 |
| `archive_session() / clear_session()` | 会话末尾清理 |
| `clear_all_session_summaries()` | 清掉所有会话的所有摘要 |
| `get_stable_entries(...)` | 注入 `<memory-stable>` 系统提示块 |

### `MemoryStore`（Protocol，0.3.0 / #16）

```python
class MemoryStore(Protocol):
    # 记忆 CRUD
    def upsert_memory(self, record: MemoryRecord) -> MemoryRecord: ...
    def get_memory_by_id(self, memory_id: str) -> Optional[MemoryRecord]: ...
    def get_memory_by_scope_key(self, scope: str, key_normalized: str) -> Optional[MemoryRecord]: ...
    def list_memories(self, scope: Optional[str] = None) -> List[MemoryRecord]: ...
    def search_memories(self, query: str, scope: Optional[str] = None) -> List[MemoryRecord]: ...
    def filter_by_tag(self, tag: str, scope: Optional[str] = None) -> List[MemoryRecord]: ...
    def soft_delete_memory(self, memory_id: str) -> bool: ...
    def clear_memories(self, scope: Optional[str] = None) -> int: ...
    # 会话摘要
    def save_session_summary(self, record: SessionSummaryRecord) -> None: ...
    def list_session_summaries(self, session_id=None, limit=20) -> List[SessionSummaryRecord]: ...
    def clear_session_summaries(self, session_id=None) -> int: ...
    # 复审队列
    def upsert_review_item(self, item: MemoryReviewItem) -> MemoryReviewItem: ...
    def get_review_item(self, item_id: str) -> Optional[MemoryReviewItem]: ...
    def list_review_items(self, status="pending", limit=50) -> List[MemoryReviewItem]: ...
    def update_review_status(self, item_id: str, status: str) -> bool: ...
```

15 个方法，schema-less。实现这套 Protocol 即可把记忆后端换成 Redis / Postgres / 进程内 dict / 远端 API。默认 `SQLiteMemoryStore` 与 #16 之前的实现字节级一致。

### `SQLiteMemoryStore`

```python
SQLiteMemoryStore.open(path)              # 严格；磁盘出错抛
SQLiteMemoryStore.open_or_memory(path)    # 宽容；出错降级到 ":memory:"
```

两个 classmethod 取代了原本 `MemoryManager.__init__` 的 try/except。项目 store 用 `open_or_memory`（宁可在内存里也别把 Agent 启动炸掉），用户 store 用 `open`（写不进去就该禁用，而不是默默把写入路由到项目作用域）。

## A.6 MCP

### `MCPRegistry`（Protocol，0.3.0 / #17）

```python
class MCPRegistry(Protocol):
    def list_servers(self) -> Dict[str, McpServerConfig]: ...
```

MCP 服务器配置的来源。`Agentao(mcp_registry=...)` 是注入点；`mcp_manager=` 与 `mcp_registry=` 互斥（manager 是构造结果，registry 是配置源）。

| 具体实现 | 作用 |
|---|---|
| `FileBackedMCPRegistry(project_root, user_root=None)` | CLI/ACP 默认。每次 `list_servers()` 都重读 `<wd>/.agentao/mcp.json` + `<user_root>/mcp.json`。 |
| `InMemoryMCPRegistry(servers=None)` | 测试 / 嵌入式 host 用的程序化版本。构造入参做浅拷贝。 |

## A.7 取消

### `CancellationToken`

```python
token = CancellationToken()
token.is_cancelled         # bool，不抛
token.cancel("reason")     # 幂等
token.check()              # 已取消则抛 AgentCancelledError
token.reason               # str
```

传给 `agent.chat(..., cancellation_token=token)`，就能从其他线程/异步任务中止。超时模式见 [2.3](/zh/part-2/3-lifecycle)。

### `AgentCancelledError`

token 被取消时在 agent 循环里抛出。`chat()` 会捕获并返回 `[Cancelled: reason]` 字符串，而不是再往外抛。

## A.8 ACP 客户端

### `ACPManager`

宿主侧驱动 `.agentao/acp.json` 里声明的外部 ACP 服务器的典型入口。
如果你在无人值守环境里使用它（CI / worker / cron / queue consumer），那它就是正文里说的 **Headless Runtime**：不是新协议，也不是新类，只是 `ACPManager` 的一种运行方式。

| 方法 | 签名 | 作用 |
|------|------|------|
| `from_project` | `@classmethod from_project(project_root=None) -> ACPManager` | 读 `.agentao/acp.json` |
| `server_names` | `-> list[str]` | 已声明的服务器 |
| `start_all` | `start_all(only_auto=True)` | 启动所有 auto-start 服务器 |
| `start_server(name)` / `stop_server(name)` / `restart_server(name)` | | |
| `ensure_connected(name, cwd=?, mcp_servers=?)` | | 幂等连接 + 新建会话 |
| `send_prompt(name, prompt, timeout=?)` | `-> PromptResult` | 交互式一轮（public） |
| `prompt_once(name, prompt, cwd=?, mcp_servers=?, timeout=?, interactive=False, stop_process=True)` | `-> PromptResult` | Fail-fast 一次性，自动清理（public） |
| `send_prompt_nonblocking` / `finish_prompt_nonblocking` / `cancel_prompt_nonblocking` | | 底层异步版本（**internal / unstable**，不在 embedding contract 内） |
| `stop_all()` | | 停所有子进程 |
| `get_status()` | `-> list[ServerStatus]` | 类型化 headless 快照（Week 1 核心字段冻结 + Week 2 增量字段） |
| `readiness(name)` | `-> Literal["ready","busy","failed","not_ready"]` | 基于 state × active-turn 的类型化可用性分级（Week 2） |
| `is_ready(name)` | `-> bool` | `readiness(name) == "ready"` 的快捷方式 |
| `reset_last_error(name)` | | 清除 manager 上记录的 `last_error` / `last_error_at` |
| `get_client(name)` / `get_handle(name)` | | 自省 |
| `config`（属性） | `-> AcpClientConfig` | |

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

`ACPManager.get_status()` 的类型化返回值。Week 1 核心字段已冻结；
Week 2 按增量方式扩展了诊断字段。

```python
@dataclass(frozen=True)
class ServerStatus:
    # Week 1 —— 核心（冻结）
    server: str
    state: str
    pid: Optional[int]
    has_active_turn: bool

    # Week 2 —— 诊断（增量）
    active_session_id: Optional[str] = None
    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None   # 带 tzinfo=UTC
    inbox_pending: int = 0
    interaction_pending: int = 0
    config_warnings: List[str] = field(default_factory=list)
```

Week 2 字段语义要点：

- `last_error` 在成功 turn 之间**保持**，不会被覆盖成 `None`；如需清空，
  调用 `reset_last_error(name)`。
- `last_error_at` 的时间戳由 manager **存入时**取的
  `datetime.now(timezone.utc)`，不是抛出时的瞬时。消费者应据此判断错误
  是否陈旧，而不是把它当作精确 raise 时刻来使用。
- `SERVER_BUSY` 与 `SERVER_NOT_FOUND` **不**写入 `last_error`，因为它们是
  调用方侧信号，不是服务端状态。

完整 state-vs-error 合约和 readiness 分级见
[`docs/features/headless-runtime.md`](../../../docs/features/headless-runtime.md)。

### 异常类

| 类 | `code` | 说明 |
|----|--------|------|
| `AcpClientError` | 任意 `AcpErrorCode` | 基类 |
| `AcpServerNotFound` | `SERVER_NOT_FOUND` | 同时继承 `KeyError` |
| `AcpRpcError` | `.rpc_code: int` + `.error_code: PROTOCOL_ERROR` | JSON-RPC 响应错 |
| `AcpInteractionRequiredError` | `INTERACTION_REQUIRED` | 带 `prompt` + `options` |

完整错误码表见 [附录 D](./d-error-codes)。

### `load_acp_client_config`

```python
load_acp_client_config(project_root: Path | None = None) -> AcpClientConfig
```

`project_root` 为 `None` 时退回 `Path.cwd()`。

不构造 manager 直接校验 + 加载 `.agentao/acp.json`。写 config lint 工具时好用。

## A.9 技能

### `SkillManager`

通过 `from agentao import SkillManager` 懒加载。一般通过 `agent.skill_manager` 使用。

| 方法 | 作用 |
|------|------|
| `list_available_skills() -> list[str]` | 当前可发现的技能名 |
| `list_all_skills() -> list[str]` | 含已禁用的 |
| `get_skill_info(name) -> dict | None` | 返回 `{name, description, path, ...}` |
| `activate_skill(name, task_description)` | 激活——把 SKILL.md + 活跃的 reference 注入系统提示 |
| `enable_skill(name)` / `disable_skill(name)` | 持久化的启用/禁用 |

## A.10 嵌入 Host 合约

*自 0.3.1 起 Stable。包名于 0.4.2 由 `agentao.harness` 重命名为 `agentao.host`。*

`agentao.host` 是嵌入 Agentao 的 **稳定宿主面 API**。内部运行时类型（`AgentEvent`、`ToolExecutionResult`、`PermissionEngine`）刻意 **不在** 该面内 —— 任何版本都可能改动。只针对 `agentao.host`（加上 `Agentao(...)` 构造器以及上文标注的方法）开发的宿主可在版本升级中保持前向兼容。

> **命名说明。** "Harness" 仍然指 *Agentao 自身嵌入在宿主应用中运行* 这一概念（见 `docs/design/embedded-host-contract.md` 设计语境）；合约包重命名为 `agentao.host` 是为了让 `from agentao.host import HostEvent` 读起来自洽。旧的 `agentao.harness` 导入路径以及旧符号名（`HarnessEvent`、`HarnessReplaySink`、`export_harness_*`）作为弃用别名保留至 0.5.0，首次导入时发一次 `DeprecationWarning`。

完整参考：[`docs/api/host.md`](../../../docs/api/host.md) · [`docs/api/host.zh.md`](../../../docs/api/host.zh.md)。设计动机：[`docs/design/embedded-host-contract.md`](../../../docs/design/embedded-host-contract.md)。

### 公共导出

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

| 符号 | 作用 |
|---|---|
| `ActivePermissions` | 当前权限策略的只读快照（`mode`、`rules`、`loaded_sources`） |
| `ToolLifecycleEvent` | 单次工具调用的公共生命周期信封。`phase ∈ {started, completed, failed}`；取消以 `phase="failed", outcome="cancelled"` 体现 |
| `SubagentLifecycleEvent` | 子 Agent 任务/会话的血缘事实。`phase ∈ {spawned, completed, failed, cancelled}` —— `cancelled` 在这里是独立 phase |
| `PermissionDecisionEvent` | 单次权限决定的投影。在 `allow` / `deny` / `prompt` 都触发；不渲染 allow 的消费者也必须排空迭代器以避免背压 |
| `HostEvent` | 三种事件模型的判别联合（Pydantic discriminator: `event_type`） |
| `RFC3339UTCString` | 受约束的时间戳类型。仅允许标准 `Z` 后缀 —— `+00:00` 偏移会被拒绝 |
| `EventStream` | `Agentao.events()` 的运行时侧。生产者调 `publish()`；消费者迭代 `subscribe()` |
| `StreamSubscribeError` | 同一 `session_id` 过滤器上发起第二个并发订阅时抛出（MVP 每个 `Agentao` 只支持一个公共流消费者） |
| `export_host_event_json_schema()` | 导出事件 + 权限面的标准 JSON schema。`tests/test_host_schema.py` 用它与 `docs/schema/host.events.v1.json` 做字节相等校验 |
| `export_host_acp_json_schema()` | 导出宿主面 ACP 载荷的标准 JSON schema。快照在 `docs/schema/host.acp.v1.json` |

### `agent.events(session_id=None)`

返回 `HostEvent` 的异步迭代器。传 `session_id=` 过滤；传 `None` 订阅该 `Agentao` 实例下所有会话。

```python
async for ev in agent.events():
    if isinstance(ev, ToolLifecycleEvent):
        ...
    elif isinstance(ev, PermissionDecisionEvent):
        ...
    elif isinstance(ev, SubagentLifecycleEvent):
        ...
```

投递契约：

- 同会话顺序保证；跨会话全局顺序不保证。
- 同一 `tool_call_id` 内，`PermissionDecisionEvent` 一定先于 `ToolLifecycleEvent(phase="started")`。
- **不 replay。** 第一次订阅前发出的事件被丢弃。
- 背压走宿主拉取：消费者慢时，生产者会 await 匹配事件的容量，不会丢事件，也不会无限堆队列。
- 取消迭代器会释放队列/订阅资源。
- MVP 每个 `Agentao` 只支持一个公共流消费者。

### `agent.active_permissions()`

返回 JSON-safe 的 `ActivePermissions` 快照：

```python
snap = agent.active_permissions()
# snap.mode            -> "workspace-write"（Literal 类型）
# snap.rules           -> list[dict]
# snap.loaded_sources  -> ["preset:workspace-write",
#                          "project:.agentao/permissions.json",
#                          "user:/Users/me/.agentao/permissions.json",
#                          "injected:host"]
```

`loaded_sources` 是稳定的字符串标签：`preset:<mode>`、`project:<path>`、`user:<path>`、`injected:<name>`。MVP **不** 暴露逐规则 provenance —— 需要细到规则级的宿主请把 `loaded_sources` 与自己注入的策略元数据组合。

未配置 `permission_engine` 时，运行时返回宽松回退：`mode="workspace-write"`、空 `rules`、`loaded_sources=["default:no-engine"]`。该标签明确告诉宿主："看到的是无引擎回退而非配置策略"。

叠加策略的宿主调 `agent.permission_engine.add_loaded_source("injected:<name>")` 让快照反映其 provenance。`set_mode()` 与 `add_loaded_source()` 都会让缓存失效。

### Schema 快照

每个发布版都附带 check-in 的 JSON schema 快照：

- [`docs/schema/host.events.v1.json`](../../../docs/schema/host.events.v1.json) —— 事件 + 权限面
- [`docs/schema/host.acp.v1.json`](../../../docs/schema/host.acp.v1.json) —— 宿主面 ACP 载荷

`tests/test_host_schema.py` 会从 Pydantic 模型重新生成 schema，并与快照做字节相等比对。任何改变 wire form 的 model 变更必须在同一 PR 内同时更新模型与快照。新增 optional 字段向后兼容；删除/重命名需要 schema 版本号 bump。

### 不在合约内（明确不做）

- 公共 agent graph / descendants 存储 API
- 宿主面 hooks list/disable API
- 宿主面 MCP reload / 生命周期事件
- 本地 plugin export/import；远程 plugin 共享
- 外部会话 import
- 生成式客户端 SDK

CLI 可基于同一组事件构建自己的 UI，但其本地存储与命令不会被提升为 host 合约。

---

→ [附录 F · FAQ](./f-faq)
