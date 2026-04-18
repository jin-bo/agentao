# 附录 A · API 参考

这是一份**面向表层**的参考——Agentao 嵌入契约里的公开符号。未在此列出的都算实现细节，可能变动。行为与示例请回跳到正文相应章节。

权威 `__all__`：

- `from agentao import ...` → `Agentao`、`SkillManager`
- `from agentao.transport import ...` → `AgentEvent`、`EventType`、`Transport`、`NullTransport`、`SdkTransport`、`build_compat_transport`
- `from agentao.tools.base import ...` → `Tool`、`ToolRegistry`
- `from agentao.permissions import ...` → `PermissionEngine`、`PermissionMode`、`PermissionDecision`
- `from agentao.memory.manager import MemoryManager`
- `from agentao.cancellation import ...` → `CancellationToken`、`AgentCancelledError`
- `from agentao.acp_client import ...` → `ACPManager`、`ACPClient`、`AcpClientError`、`AcpErrorCode`、`AcpRpcError`、`AcpInteractionRequiredError`、`AcpClientConfig`、`AcpServerConfig`、`AcpConfigError`、`PromptResult`、`ServerState`、`load_acp_client_config`（以及更底层的 re-export——哪些属于"稳定嵌入面"、哪些属于"实现细节"请参考 `agentao.acp_client.__init__.py` 的 docstring）

## A.1 `Agentao`

核心类——完整构造参数表见 [Part 2.2](/zh/part-2/2-constructor-reference)。

### 构造器

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
    # 老式回调（建议改用 `transport=`）
    output_callback: Callable[[str], None] | None = None,
    confirmation_callback: Callable[[str, str, dict], bool] | None = None,
    input_callback: Callable[[str], str] | None = None,
    on_max_iterations_callback: Callable[[int, list], dict] | None = None,
    # ...
)
```

### 方法

| 方法 | 签名 | 作用 |
|------|------|------|
| `chat` | `chat(user_message: str, max_iterations: int = 100, cancellation_token: CancellationToken | None = None) -> str` | 跑一轮，返回助手最终文本 |
| `clear_history` | `clear_history() -> None` | 清 `self.messages`；不影响 memory DB |
| `close` | `close() -> None` | 关 MCP 子进程与 DB handle；请放 `finally:` |
| `set_provider` | `set_provider(api_key, base_url=None, model=None) -> None` | 运行时换 LLM |
| `set_model` | `set_model(model: str) -> str` | 只换模型；返回旧 id |

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

10 种 `EventType`——payload 完整参考见 [4.2](/zh/part-4/2-agent-events)。

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
```

### `ToolRegistry`

| 方法 | 作用 |
|------|------|
| `register(tool: Tool)` | 注册；同名会警告 |
| `get(name: str) -> Tool` | 不存在时抛 `KeyError`，带可用工具列表 |
| `list_tools() -> list[Tool]` | |
| `to_openai_format() -> list[dict]` | OpenAI function-calling schemas |

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
PermissionEngine(*, project_root: Path | None = None)
```

构造时自动从 `<project_root>/.agentao/permissions.json` + `~/.agentao/permissions.json` 加载规则。构造后用 `set_mode()` 切换预设模式。

| 方法 | 签名 | 作用 |
|------|------|------|
| `decide` | `decide(tool_name: str, tool_args: dict) -> PermissionDecision | None` | `None` 表示"回到调用方默认" |
| `set_mode` | `set_mode(mode: PermissionMode) -> None` | 切换预设（`READ_ONLY`、`WORKSPACE_WRITE`、`FULL_ACCESS`、`PLAN`） |
| `active_mode` | 属性（只读） | 当前生效的 `PermissionMode` |

继承并 override `decide()` 可对接企业 IAM——确信度门控范例见 [7.3](/zh/part-7/3-ticket-automation)。

## A.5 记忆

### `MemoryManager`

```python
MemoryManager(
    project_root: Path,
    global_root: Path | None = None,
    guard: MemoryGuard | None = None,
)
```

- `project_root` —— 项目作用域 `memory.db` 的所在目录（一般是 `<cwd>/.agentao`）
- `global_root` —— 跨项目的用户作用域 DB 所在目录（一般是 `~/.agentao`）；传 `None` 关闭用户作用域
- `guard` —— 可选的 `MemoryGuard`，落盘前过滤敏感内容

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
| `get_stable_entries(...)` | 注入 `<memory-stable>` 系统提示块 |

## A.6 取消

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

## A.7 ACP 客户端

### `ACPManager`

宿主侧驱动 `.agentao/acp.json` 里声明的外部 ACP 服务器的典型入口。

| 方法 | 签名 | 作用 |
|------|------|------|
| `from_project` | `@classmethod from_project(project_root=None) -> ACPManager` | 读 `.agentao/acp.json` |
| `server_names` | `-> list[str]` | 已声明的服务器 |
| `start_all` | `start_all(only_auto=True)` | 启动所有 auto-start 服务器 |
| `start_server(name)` / `stop_server(name)` / `restart_server(name)` | | |
| `ensure_connected(name, cwd=?, mcp_servers=?)` | | 幂等连接 + 新建会话 |
| `send_prompt(name, prompt, timeout=?)` | `-> PromptResult` | 交互式一轮 |
| `prompt_once(name, prompt, cwd=?, mcp_servers=?, timeout=?, interactive=False, stop_process=True)` | `-> PromptResult` | Fail-fast 一次性，自动清理 |
| `send_prompt_nonblocking` / `finish_prompt_nonblocking` / `cancel_prompt_nonblocking` | | 底层异步版本 |
| `stop_all()` | | 停所有子进程 |
| `get_status()` / `get_client(name)` / `get_handle(name)` | | 自省 |
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

## A.8 技能

### `SkillManager`

通过 `from agentao import SkillManager` 懒加载。一般通过 `agent.skill_manager` 使用。

| 方法 | 作用 |
|------|------|
| `list_available_skills() -> list[str]` | 当前可发现的技能名 |
| `list_all_skills() -> list[str]` | 含已禁用的 |
| `get_skill_info(name) -> dict | None` | 返回 `{name, description, path, ...}` |
| `activate_skill(name, task_description)` | 激活——把 SKILL.md + 活跃的 reference 注入系统提示 |
| `enable_skill(name)` / `disable_skill(name)` | 持久化的启用/禁用 |

---

→ [附录 F · FAQ](./f-faq)
