# 2.2 构造器完整参数表

现在有**两条稳定的构造路径**，按宿主已掌握的信息选择：

- **`agentao.embedding.build_from_environment(...)`** —— CLI 风格的自动发现：读 `.env`、`LLM_PROVIDER`、`<wd>/.agentao/permissions.json`、`<wd>/.agentao/mcp.json`、内存目录，然后帮你构造 `Agentao`。当宿主沿用 CLI 的项目目录约定时使用。
- **直接 `Agentao(...)`** —— 显式注入：你已经持有 `LLMClient`、`PermissionEngine` 等子系统，构造时不希望发生任何 env / 磁盘副作用。

**重要（0.3.0）**：不传 `working_directory=` 调用 `Agentao()` 现在会从 Python 签名分派直接抛 `TypeError`——软废弃周期已经走完。请显式传入 `Path`，或走 `build_from_environment()`。完整的嵌入式接入实践见 [`docs/EMBEDDING.md`](../../../docs/EMBEDDING.md)。

## 工厂：`build_from_environment()`

```python
from pathlib import Path
from agentao.embedding import build_from_environment

agent = build_from_environment(
    working_directory=Path("/data/tenant-acme"),
    transport=my_transport,
    max_context_tokens=128_000,
)
```

它做的事：

1. 解析 `working_directory`（默认 `Path.cwd()`），冻结成绝对路径。
2. `<wd>/.env` 存在则按它 `load_dotenv`，否则全局回落。
3. 读 `LLM_PROVIDER` 与对应的 `*_API_KEY` / `*_BASE_URL` / `*_MODEL`。
4. 构造 `PermissionEngine(project_root=wd, user_root=user_root())`，构造 `MemoryManager`（项目 store 用 `SQLiteMemoryStore.open_or_memory(wd / ".agentao" / "memory.db")`，用户 store 用 `SQLiteMemoryStore.open(...)`，写不进去就降级到禁用），构造 `FileBackedMCPRegistry(project_root=wd, user_root=user_root())`（#16/#17 起）。
5. 把所有显式参数传入 `Agentao(...)`。**调用方传入的 `**overrides` 优先**。

整个代码库里只有这一处会在启动时读 env / dotenv / `.agentao/*.json`。不希望发生这些副作用的宿主直接构造 `Agentao` 即可。

## `Agentao.__init__` 完整签名（`agentao/agent.py`）

```python
Agentao(
    api_key:          Optional[str]    = None,
    base_url:         Optional[str]    = None,
    model:            Optional[str]    = None,
    temperature:      Optional[float]  = None,
    # ── 已废弃的 legacy 回调（仍保留向后兼容） ──
    confirmation_callback:      Optional[Callable] = None,
    max_context_tokens:         int                = 200_000,
    step_callback:              Optional[Callable] = None,
    thinking_callback:          Optional[Callable] = None,
    ask_user_callback:          Optional[Callable] = None,
    output_callback:            Optional[Callable] = None,
    tool_complete_callback:     Optional[Callable] = None,
    llm_text_callback:          Optional[Callable] = None,
    permission_engine:          Optional[PermissionEngine] = None,
    on_max_iterations_callback: Optional[Callable] = None,
    transport:                  Optional[Transport]        = None,
    plan_session:               Optional[PlanSession]      = None,
    *,
    working_directory:  Path,                                  # 0.3.0 起必传
    extra_mcp_servers:  Optional[Dict[str, Dict[str, Any]]] = None,
    # ── 嵌入式 harness 显式注入参数 ──
    llm_client:           Optional[LLMClient]         = None,
    logger:               Optional[logging.Logger]    = None,
    memory_manager:       Optional[MemoryManager]     = None,
    skill_manager:        Optional[SkillManager]      = None,
    project_instructions: Optional[str]               = None,
    mcp_manager:          Optional[McpClientManager]  = None,
    mcp_registry:         Optional[MCPRegistry]       = None,  # 0.3.0+ (#17)
    filesystem:           Optional[FileSystem]        = None,
    shell:                Optional[ShellExecutor]     = None,
    # ── 可选启用的子系统（None = 完全禁用） ──
    bg_store:             Optional[BackgroundTaskStore] = None,
    sandbox_policy:       Optional[SandboxPolicy]       = None,
    replay_config:        Optional[ReplayConfig]        = None,
)
```

## LLM 凭据（前 4 个参数）

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `api_key` | `str` | —— 必须显式传入或来自 `llm_client=` | LLM 凭据 |
| `base_url` | `str` | —— | 切换兼容端点（DeepSeek / Gemini gateway / vLLM 等） |
| `model` | `str` | —— | 模型 ID |
| `temperature` | `float` | `0.2` | 采样温度 |

要么 4 个全部显式传入，要么传入已经构造好的 `llm_client=`（见下文）。**互斥**：同时传 `llm_client=` 与任意原始 LLM 参数会抛 `ValueError`。

多租户/多会话场景下：每个会话可以有**不同的凭据**（比如按客户区分），详见第 7.2 节。

## 嵌入式 harness 显式注入

不希望 `Agentao()` 用默认值构造某个子系统时，把你自己的实例注入进来：

| 参数 | 类型 | 注入后跳过的行为 |
|------|------|-----------------|
| `llm_client` | `LLMClient` | 不再构造 `LLMClient(...)`，凭据相关的 env 读取被跳过 |
| `logger` | `logging.Logger` | 跳过 `LLMClient.__init__` 里对包根 logger 的 level/handler 改动——你的日志栈保持不变 |
| `memory_manager` | `MemoryManager` | 启动时不再打开 `<wd>/.agentao/memory.db` |
| `skill_manager` | `SkillManager` | 跳过自带技能扫描，按你给的实例原样使用 |
| `project_instructions` | `str` | 跳过 `<wd>/AGENTAO.md` 的磁盘读取，按你给的字符串原样使用 |
| `mcp_manager` | `McpClientManager` | 不读 `.agentao/mcp.json`，由你掌控 MCP 生命周期 |
| `mcp_registry` (0.3.0+) | `MCPRegistry` | 替代 `init_mcp` 里的隐式 `load_mcp_config(...)`。默认 `FileBackedMCPRegistry` 与 #17 之前的磁盘读一致；要程序化注册就传 `InMemoryMCPRegistry`。与 `mcp_manager=` 互斥。 |
| `filesystem` | `FileSystem` | 文件 / 搜索工具走你提供的 `FileSystem`（详见 6.4 节） |
| `shell` | `ShellExecutor` | Shell 工具走你提供的 `ShellExecutor` |
| `bg_store`（可选） | `BackgroundTaskStore` | 后台工具状态持久化。`None` 时 `check_background_agent` 等不注册，子 agent 的 `run_in_background` 字段从 schema 里抠掉。 |
| `sandbox_policy`（可选） | `SandboxPolicy` | Shell 沙箱。`None` 时不套 macOS `sandbox-exec`。 |
| `replay_config`（可选） | `ReplayConfig` | 确定性回放。`None` 时使用空 recorder。 |

这是宿主在"我要 Agentao 的运行时但不要它的 CLI 风格隐式读取"场景下的桥梁。可以自由组合：

```python
from agentao import Agentao
from agentao.llm import LLMClient
from agentao.capabilities import LocalFileSystem

agent = Agentao(
    working_directory=Path("/srv/agent-workdir"),
    llm_client=LLMClient(
        api_key=secrets.openai_api_key,
        base_url="https://api.openai.com/v1",
        model="gpt-5.4",
        log_file=None,            # 不落本地日志
        logger=app.logger,        # 走宿主自己的 logger
    ),
    skill_manager=preloaded_skill_manager,
    filesystem=LocalFileSystem(),  # 或者你自己的沙盒 FS
    transport=my_transport,
)
```

### Capability 协议

`FileSystem` / `ShellExecutor` 是 runtime-checkable `Protocol`。0.3.4 起这些协议在公共 harness 表面有 re-export——**请始终从 `agentao.harness.protocols` 导入**，不要伸手到内部的 `agentao.capabilities.*`（后者是内部实现，可能会移动）。默认实现 `LocalFileSystem` / `LocalShellExecutor` 仍住在 `agentao.capabilities` 中，行为与 0.2.16 之前的 Agentao 字节级一致。宿主把要注入的协议替换为基于 Docker exec、虚拟文件系统、审计代理或远程 runner 的实现即可。

```python
from agentao.harness.protocols import (
    FileSystem, FileEntry, FileStat,
    ShellExecutor, ShellRequest, ShellResult, BackgroundHandle,
)
from agentao.capabilities import LocalFileSystem, LocalShellExecutor  # 默认实现
```

多租户文件隔离方式参考 6.4 节。

## Transport（推荐）

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `transport` | `Transport` | `NullTransport()` | UI 交互与事件流 |

只要传入一个 `Transport` 实现，就涵盖了所有交互：工具确认、用户追问、事件流、最大迭代回调。**构造时不传 = 全部自动批准 + 无事件监听**（`NullTransport` 行为），适合无人值守批处理。

```python
from agentao.transport import SdkTransport

transport = SdkTransport(
    on_event=handle_event,
    confirm_tool=ask_approval,
    ask_user=prompt_user,
    on_max_iterations=lambda n, msgs: {"action": "stop"},
)
agent = Agentao(transport=transport, working_directory=workdir, ...)
```

自己实现 Transport Protocol 参见 [第 4 部分](/zh/part-4/)。

## 已废弃的 8 个回调（legacy）

| 参数 | 替代方案 |
|------|---------|
| `confirmation_callback` | `SdkTransport(confirm_tool=...)` |
| `step_callback` | `on_event=` + 监听 `TOOL_START` / `TURN_START` |
| `thinking_callback` | `on_event=` + 监听 `THINKING` |
| `ask_user_callback` | `SdkTransport(ask_user=...)` |
| `output_callback` | `on_event=` + 监听 `TOOL_OUTPUT` |
| `tool_complete_callback` | `on_event=` + 监听 `TOOL_COMPLETE` |
| `llm_text_callback` | `on_event=` + 监听 `LLM_TEXT` |
| `on_max_iterations_callback` | `SdkTransport(on_max_iterations=...)` |

**这 8 个参数仍然被接收并工作**——Agentao 内部 `build_compat_transport()` 会把它们翻译成一个 `SdkTransport`。但新写的代码应直接用 Transport 路径。

## 运行时行为控制

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `max_context_tokens` | `int` | `200_000` | 超过触发上下文压缩（第 7.3 节） |
| `plan_session` | `PlanSession` | `None` | 启用 Plan 模式；一般宿主无需设置 |
| `permission_engine` | `PermissionEngine` | `None`（工厂会建一个 `project_root=wd` 的） | 权限规则引擎（第 5.4 / 6.3 节） |

## 会话隔离（关键！）

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `working_directory` | `Path` | —— （0.3.0 起必传） | **构造时冻结** |

**为什么重要**（0.3.0 起）：
- 必传关键字参数——软废弃周期已结束。不传会从 Python 签名分派直接抛 `TypeError`。
- 路径在构造时被 `expanduser().resolve()` 一次冻结。之后所有文件操作、`AGENTAO.md`、`.agentao/` 配置、Shell CWD 全部相对它；宿主进程后续 `os.chdir` 不会影响已构造好的 Agent。

嵌入场景（Web 服务器、ACP sessions）里，`Path.cwd()` 是进程全局状态；两个并发会话原本会**互相污染**——必传 `working_directory` 把这个坑封住了。

```python
# ❌ 0.3.0 之前：依赖 Path.cwd()——现在直接 TypeError
agent_a = Agentao(...)
agent_b = Agentao(...)

# ✅ 正确：每个会话独立根
agent_a = Agentao(..., working_directory=Path("/tmp/tenant-a"))
agent_b = Agentao(..., working_directory=Path("/tmp/tenant-b"))
```

## 会话级 MCP 服务器

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `extra_mcp_servers` | `Dict[str, Dict]` | `None` | 程序式注入 MCP 服务器 |

用于给**单个会话**加 MCP 服务器而**不触碰项目的 `.agentao/mcp.json`**。典型场景：按用户身份切换不同 GitHub Token 的 MCP 服务器。

```python
agent = Agentao(
    working_directory=tenant_dir,
    extra_mcp_servers={
        "github-per-tenant": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_TOKEN": tenant.github_token},
        },
    },
)
```

合并策略：**同名键会覆盖** `.agentao/mcp.json` 里的同名服务器。

**与 `mcp_manager=` 互斥**：要么传已经构造好的 manager，要么传待合并的 dict，不能两个都传。

## 异步宿主：`Agentao.arun()`

同步调用方仍然用 `agent.chat(user_message)`；异步宿主用 `await agent.arun(user_message)`。

```python
async def handle_request(request):
    response = await agent.arun(
        request.text,
        cancellation_token=request.cancel_token,
    )
    return {"reply": response}
```

`arun()` 通过 `asyncio.get_running_loop().run_in_executor(None, self.chat, ...)` 把（仍是同步的）chat 循环桥到线程池。取消、replay、`max_iterations` 在两条接口上的语义完全一致。运行时内部刻意保持同步——它本质是顺序 I/O，自上而下的异步会扩大表面但不带来收益。

## 全量示例：生产嵌入模板

```python
from pathlib import Path
import os
from agentao import Agentao
from agentao.transport import SdkTransport
from agentao.permissions import PermissionEngine, PermissionMode

def make_agent_for_session(
    tenant_id: str,
    tenant_workdir: Path,
    tenant_token: str,
    on_event,
    confirm_tool,
) -> Agentao:
    engine = PermissionEngine(project_root=tenant_workdir)
    engine.set_mode(PermissionMode.WORKSPACE_WRITE)

    transport = SdkTransport(
        on_event=on_event,
        confirm_tool=confirm_tool,
        on_max_iterations=lambda n, _msgs: {"action": "stop"},
    )

    return Agentao(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_BASE_URL"),
        model="gpt-5.4",
        temperature=0.1,
        transport=transport,
        working_directory=tenant_workdir,
        max_context_tokens=128_000,
        permission_engine=engine,
        extra_mcp_servers={
            "gh": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": tenant_token},
            },
        },
    )
```

如果宿主沿用 CLI 的目录约定，可以走工厂：

```python
from agentao.embedding import build_from_environment

agent = build_from_environment(
    working_directory=tenant_workdir,
    transport=transport,
    max_context_tokens=128_000,
)
```

下一节：[2.3 生命周期管理 →](./3-lifecycle)
