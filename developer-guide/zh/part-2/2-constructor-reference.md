# 2.2 构造器完整参数表

> **本节你会学到**
> - **必传的 3 个参数**及为什么必传
> - **生产环境通常会用到的 8 个参数**（transport / permissions / MCP …）
> - 给需要完全控制的宿主用的**高级注入面**
> - 两种工厂路径（直接 `Agentao(...)` vs `build_from_environment(...)`）以及如何选

## 两种构造路径

| 路径 | 适用场景 |
|------|---------|
| **直接 `Agentao(...)`** | 想完全显式——构造时不要任何 env / 磁盘副作用。本节主体讲这条 |
| **`agentao.embedding.build_from_environment(...)`** | 宿主沿用 CLI 的项目目录约定。会读 `.env`、`permissions.json`、`mcp.json`、内存目录后帮你把 `Agentao` 拼好。详见 [§ 工厂路径](#工厂路径-build-from-environment) |

两条路径最终都是产出一个 `Agentao` 实例。**选一条**——不要混用。

---

## 第 1 档 · 最少必备（3 个参数）

**这 3 个必须传，其他都有合理默认。**

```python
from pathlib import Path
from agentao import Agentao

agent = Agentao(
    api_key="sk-...",
    model="gpt-5.4",
    working_directory=Path("/tmp/my-session"),
)
```

| 参数 | 类型 | 为什么必传 |
|------|------|----------|
| `api_key` | `str` | LLM 凭据。也可以走 env `OPENAI_API_KEY`，或传入预构造的 `llm_client=` |
| `model` | `str` | 模型 ID。也可以走 env `OPENAI_MODEL` |
| `working_directory` | `Path` | 这个会话的项目根目录。**构造时冻结**——文件 / Shell / 记忆全部相对它 |

> 端点不是 OpenAI 时（DeepSeek / Gemini gateway / vLLM …）还要传 `base_url`，或走 env `OPENAI_BASE_URL`。

::: warning 千万别省略 `working_directory`
Web 服务 / 多租户进程里 `Path.cwd()` 是**进程全局**——并发会话会互相污染。0.3.0 起这个关键字必传，不传会从 Python 签名分派直接抛 `TypeError`。
:::

---

## 第 2 档 · 生产常用（再加 8 个）

这套搭配能覆盖大多数生产嵌入：

```python
from agentao import Agentao
from agentao.transport import SdkTransport
from agentao.permissions import PermissionEngine, PermissionMode

engine = PermissionEngine(project_root=workdir)
engine.set_mode(PermissionMode.WORKSPACE_WRITE)

transport = SdkTransport(on_event=..., confirm_tool=..., ask_user=...)

agent = Agentao(
    api_key="sk-...",
    base_url="https://api.openai.com/v1",
    model="gpt-5.4",
    temperature=0.1,
    working_directory=workdir,
    transport=transport,
    permission_engine=engine,
    max_context_tokens=128_000,
    extra_mcp_servers={...},
)
```

| 参数 | 类型 | 默认 | 作用 |
|------|------|------|------|
| `base_url` | `str` | OpenAI 默认 | 切换到任意 OpenAI 兼容端点 |
| `temperature` | `float` | `0.2` | 采样温度 |
| `extra_body` | `Dict[str,Any]` | `None` | 原样转发给 LLM `.create()` 的 SDK `extra_body` —— 封闭请求构建够不到的参数的逃生舱（`reasoning_effort` / `top_p` / `seed` / `response_format` / provider 专有字段）。**仅关键字。** 子 agent 继承;日志中凭据键脱敏。**与 `llm_client` 互斥**。详见下文 |
| `transport` | `Transport` | `NullTransport()` | UI 桥：事件流 + 工具确认 + ask_user + 最大迭代回调，详见 [第 4 部分](/zh/part-4/) |
| `permission_engine` | `PermissionEngine` | 工厂自动建一个根在 `working_directory` | 规则级权限引擎，详见 [5.4](/zh/part-5/4-permissions) |
| `max_context_tokens` | `int` | `200_000` | 超过即触发对话压缩 |
| `extra_mcp_servers` | `Dict[str,Dict]` | `None` | 给单个会话注入 MCP 服务器，不动 `.agentao/mcp.json`；同名会覆盖。适合按租户切换 token |
| `extra_tools` | `Sequence[Tool]` | `None` | 注入 / 替换工具（实例；最后注册，同名覆盖内置）。见 [5.1](/zh/part-5/1-custom-tools) |
| `disable_tools` | `Iterable[str]` | `None` | 按名跳过这些内置工具（未知名 → `ValueError`）。**与 `enabled_tools` 互斥** |
| `enabled_tools` | `Iterable[str]` | `None` | 保留的 agentao 自有工具白名单；`None`=关，任意可迭代含 `set()`=开。**与 `disable_tools` 互斥** |
| `llm_client` | `LLMClient` | （由凭据自动构造） | 注入预构造客户端，完全控制 logger / 日志文件。**与 `api_key` / `base_url` / `model` / `temperature` / `max_tokens` / `extra_body` 互斥**（自带 client 的 host 把 `extra_body=` 直接传给该 client） |
| `project_instructions` | `str` | （从 `<wd>/AGENTAO.md` 读） | 直接传 AGENTAO.md 内容，跳过磁盘读 |

::: tip 异步宿主走 `arun()`
`agent.chat(...)` 是同步的。异步宿主用 `await agent.arun(user_message)`，内部通过 `loop.run_in_executor` 桥接。取消、replay、`max_iterations` 在两条接口上语义完全一致。
:::

---

## 第 3 档 · 高级注入

大多数嵌入用不到这些。点开你需要的那个即可。

::: details Capability 协议 — `filesystem` / `shell` / `mcp_registry` / `memory_manager`
四个 host → Agentao 注入槽，覆盖工具会触达的所有 IO 面。替换任何一个，就能把 IO 路由到 Docker exec、虚拟文件系统、审计代理、插件式 MCP 发现，或远程记忆后端。默认实现与 0.2.16 之前的 Agentao 字节级一致。

| 槽位 | 协议 | 默认实现 | 绑定时机 |
|------|------|----------|----------|
| `filesystem` | `FileSystem` | `LocalFileSystem` | 工具注册时拷到每个文件/搜索 tool 的 `tool.filesystem` |
| `shell` | `ShellExecutor` | `LocalShellExecutor` | 工具注册时拷到 shell tool 的 `tool.shell` |
| `mcp_registry` | `MCPRegistry` | `FileBackedMCPRegistry` | `Agentao.__init__` 期间 MCP 初始化读一次 `list_servers()` |
| `memory_manager`（包裹 `MemoryStore`） | `MemoryStore` | `<wd>/.agentao/memory.db` 上的 `SQLiteMemoryStore` | 落到 `agent._memory_manager`；`save_memory` 工具委派给它 |

```python
from agentao import Agentao
from agentao.host.protocols import FileSystem, ShellExecutor, MCPRegistry, MemoryStore
from agentao.memory import MemoryManager

agent = Agentao(
    working_directory=workdir,
    filesystem=MyDockerExecFileSystem(...),         # FileSystem
    shell=MyAuditingShellExecutor(...),             # ShellExecutor
    mcp_registry=MyPluginMCPRegistry(...),          # MCPRegistry
    memory_manager=MemoryManager(                   # MemoryStore 用 manager 包一层
        project_store=MyRedisMemoryStore(...),
    ),
)
```

**协议**始终从 `agentao.host.protocols` 导入（公共表面）。默认实现住在 `agentao.capabilities` 和 `agentao.memory`。任何槽位传 `None` 表示*回退到本地默认*，**不是** *禁用*；要真正禁用某个能力，请注入一个调用即抛错的实现。

::: tip 端到端可运行示例 — [`examples/protocol-injection/`](https://github.com/jin-bo/agentao/tree/main/examples/protocol-injection)
用四个小适配器（内存 FS、审计日志 shell、dict 记忆存储、程序化 MCP registry）替换全部四个槽位，再通过 6 条 smoke 测试断言每个槽位都被实际调用。无需 `OPENAI_API_KEY`，运行 `uv sync --extra dev && PYTHONPATH=. uv run pytest tests/`。
:::

多租户 FS 隔离见 [6.4](/zh/part-6/4-multi-tenant-fs)。
:::

::: details 记忆 / 技能 / MCP 管理器 — `memory_manager` / `skill_manager` / `mcp_manager` / `mcp_registry`
当你不想让 Agentao 用默认值构造某个子系统时——典型场景：管理器是跨会话共享的，或者想用程序化配置而不是磁盘查找。

| 参数 | 替代的默认行为 |
|------|--------------|
| `memory_manager` | 默认会打开 `<wd>/.agentao/memory.db` 的 `MemoryManager` |
| `skill_manager` | 自带技能扫描 |
| `mcp_manager` | `.agentao/mcp.json` 发现 + 生命周期。**与 `extra_mcp_servers=` 和 `mcp_registry=` 互斥** |
| `mcp_registry` | `load_mcp_config(...)` 配置源。要程序化注册就传 `InMemoryMCPRegistry`。**与 `mcp_manager=` 互斥** |
:::

::: details 可选启用的子系统 — `bg_store` / `sandbox_policy` / `replay_config`
**默认 `None` = 完全禁用**。不用就不付任何代价。

| 参数 | `None` 时 |
|------|----------|
| `bg_store` | 后台任务工具（`check_background_agent` / `cancel_background_agent`）不注册；子 agent 的工具 schema 抠掉 `run_in_background` 字段；`/agent bg\|dashboard\|cancel\|delete\|logs\|result` 等 CLI 子命令 no-op + 警告 |
| `sandbox_policy` | Shell 不套 macOS `sandbox-exec` |
| `replay_config` | 不读 `<wd>/.agentao/replay.json`，agent 用空 recorder |
:::

::: details Logger 注入 — `logger`
传 `logger=app.logger` 后跳过 `LLMClient.__init__` 里对包根 logger 的 level/handler 改动，宿主日志栈保持不变。这条路径**也不会**创建默认的 `<wd>/agentao.log` 文件——file handler 分支随这次改写一并跳过。

要单独控制 file handler 这一根开关，自己构造 `LLMClient`，传 `log_file=None`（不写文件）或 `log_file="custom.log"`（重定向）。注意：只传 `log_file=None` 而**不**传 `logger=` 时，`getLogger("agentao")` 仍会被强制设成 `DEBUG`。完整开关矩阵和"完全静默"配方见 [6.6 可观测性 → 接管 Agentao 的 logger](/zh/part-6/6-observability#接管-agentao-的-logger)。
:::

::: details LLM 请求直通 — `extra_body`
Agentao 用一个**封闭字段集**（`model` / `messages` / `temperature` / `tools` / `max_tokens`）构建 OpenAI 兼容请求。当你需要该集没暴露的参数 —— `reasoning_effort`、`top_p`、`seed`、`response_format`，或任何 **provider 专有** body 字段（`top_k`、`repetition_penalty`、厂商扩展）—— 就传 `extra_body`。它原样转发给 SDK 的 `.create(extra_body=...)`，由 SDK 合进 JSON 请求体，绕过有类型的签名。

```python
agent = Agentao(
    api_key="sk-...",
    base_url="https://api.openai.com/v1",
    model="gpt-5.4",
    working_directory=workdir,
    extra_body={"reasoning_effort": "high", "seed": 7},
)
```

- **仅关键字。** 与 `api_key` / `temperature` 不同，`extra_body` 声明在 `*` 之后，绝不移位旧的位置参数。
- **值由 host 负责。** Agentao 不校验值 —— 由 SDK / provider 校验。你配置的是*自己的*端点，故这不是第三方代理。
- **子 agent 继承它**，方式与继承 `temperature` / `max_tokens` 相同。
- **切模型无自动恢复。** 与 `temperature`（被模型拒绝时自动丢弃）不同，切到非推理模型后残留的 `extra_body` 键（如 `reasoning_effort`）会让之后每次调用 400,直到你清掉。切模型时丢弃模型专有键是 host 的责任。
- **日志凭据脱敏。** 若 `extra_body` 嵌套凭据式键（`api_key`、`authorization`、`x-api-key` …），其值在 `agentao.log` 中被掩成 `***`。
- **CLI / 工厂路径：** 把 `LLM_EXTRA_BODY` 环境变量设成 JSON **对象**（如 `LLM_EXTRA_BODY='{"reasoning_effort":"high"}'`）。畸形或非对象值告警并跳过；空值当未设。见 [附录 B](/zh/appendix/b-config-keys)。
- **与 `llm_client=` 互斥。** 自带 `LLMClient` 的 host 改为把 `extra_body=` 传给*那个* client 的构造函数。

`extra_headers` 与 `settings.json` 文件层有意延后；见 `docs/design/host-llm-extra-params.md`。
:::

::: details 已废弃的 8 个回调（仍接收 —— 0.5.0 将移除）
0.2.10 之前的接口。内部由 `build_compat_transport()` 翻译成 `SdkTransport`。**任意一个被传入现在都会发出一次 `DeprecationWarning`**；构造函数签名本身将在 **0.5.0** 移除。新代码请直接走 Transport。

| 旧参数 | 替代 |
|-------|------|
| `confirmation_callback` | `SdkTransport(confirm_tool=...)` |
| `step_callback` | `on_event=` + `TOOL_START` / `TURN_START` |
| `thinking_callback` | `on_event=` + `THINKING` |
| `ask_user_callback` | `SdkTransport(ask_user=...)` |
| `output_callback` | `on_event=` + `TOOL_OUTPUT` |
| `tool_complete_callback` | `on_event=` + `TOOL_COMPLETE` |
| `llm_text_callback` | `on_event=` + `LLM_TEXT` |
| `on_max_iterations_callback` | `SdkTransport(on_max_iterations=...)` |

⚠️ 同时传 `transport=` 和 legacy 回调时，legacy 的会被**忽略**，并发出一次 `DeprecationWarning`，让测试里能看见这些没生效的 kwargs。选一条路。
:::

---

## 互斥规则

违反以下任一条都会在构造时抛 `ValueError`：

| 不能同时传 | 原因 |
|------------|------|
| `llm_client=` + 任意 `api_key` / `base_url` / `model` / `temperature` / `max_tokens` / `extra_body` | 注入的 client 已经是凭据源 —— 改为把 `extra_body=` 直接传给该 client |
| `mcp_manager=` + `extra_mcp_servers=` | 会话级合并需要 Agentao 自己构造的 manager |
| `mcp_manager=` + `mcp_registry=` | Registry 是配置源，manager 是构造结果 |

---

## 工厂路径：`build_from_environment()`

宿主沿用 CLI 约定（项目级 `.env`、`.agentao/` 配置、内存目录）时用：

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

1. 解析 `working_directory`（默认 `Path.cwd()`）并冻结
2. `<wd>/.env` 存在则按它 `load_dotenv`，否则全局回落
3. 读 `LLM_PROVIDER` 与对应的 `*_API_KEY` / `*_BASE_URL` / `*_MODEL`
4. 在 `wd` 上构造 `PermissionEngine`、`MemoryManager`、`FileBackedMCPRegistry`
5. 全部显式传给 `Agentao(...)` —— **调用方的 `**overrides` 优先**

整个代码库**只有这一处**会在启动时读 env / dotenv / `.agentao/*.json`。不希望发生这些副作用的宿主直接构造 `Agentao` 即可。

---

## 完整生产模板

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

或者，宿主已经按 CLI 约定走：

```python
from agentao.embedding import build_from_environment

agent = build_from_environment(
    working_directory=tenant_workdir,
    transport=transport,
    max_context_tokens=128_000,
)
```

---

::: info 版本说明
- **0.5.0（计划）** —— 8 个 legacy 回调 kwarg（`confirmation_callback` / `step_callback` / `thinking_callback` / `ask_user_callback` / `output_callback` / `tool_complete_callback` / `llm_text_callback` / `on_max_iterations_callback`）将从 `Agentao(...)` 签名中**移除**。请在此之前迁移到 `transport=SdkTransport(...)`。
- **0.4.x** —— 8 个 legacy 回调每次构造发一次 `DeprecationWarning`；`agentao.embedding.compat` 是有文档保证的迁移面。
- **0.3.4** — Capability 协议（`FileSystem` / `ShellExecutor`）在 `agentao.host.protocols` 上 re-export。**始终从这里导入**，不要伸手到内部的 `agentao.capabilities.*`。
- **0.3.0** — `working_directory=` 必传（不传抛 `TypeError`）。新增 `mcp_registry=` 作为稳定的配置源；默认 `FileBackedMCPRegistry` 与 #17 之前的磁盘读一致。
- **0.2.16** — 显式注入面（`memory_manager` / `skill_manager` / `mcp_manager` / `filesystem` / `shell` …）落地；`replay_config` / `sandbox_policy` / `bg_store` 默认改为 `None`。
- **0.2.10** — 核心运行时与 CLI 解耦；8 个 legacy 回调通过 `build_compat_transport()` 仍可用。

完整的嵌入接入实践见 [`docs/guides/embedding.md`](https://github.com/jin-bo/agentao/blob/main/docs/guides/embedding.md)。
:::

## TL;DR

- **必传 3 个**：`api_key`、`model`、`working_directory`（`Path`，构造时冻结）。
- **生产常用 8 个**：+ `base_url`、`temperature`、`transport`、`permission_engine`、`max_context_tokens`、`extra_mcp_servers`、`llm_client`、`project_instructions`。
- **其他全部是可选 / 高级** —— 能力协议、自定义管理器、沙箱 / replay / 后台子系统。
- **两条工厂**：`build_from_environment()` 走 CLI 约定；直接 `Agentao(...)` 走显式控制。**不要混用。**

→ 下一节：[2.3 生命周期管理](./3-lifecycle)
