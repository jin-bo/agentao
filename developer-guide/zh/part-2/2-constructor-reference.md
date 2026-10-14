# 2.2 构造器完整参数表

> **本节你会学到**
> - **能跑起来的最小调用**：四个参数，或者传预构造的 `llm_client=` 时两个
> - **一张图看全 33 个参数**，按配置对象分组
> - **生产环境通常会用到的参数**（transport / permissions / MCP …）
> - 给需要完全控制的宿主用的**高级注入面**
> - 两种工厂路径（直接 `Agentao(...)` vs `build_from_environment(...)`）以及如何选

## 两种构造路径

| 路径 | 适用场景 |
|------|---------|
| **直接 `Agentao(...)`** | 想完全显式——构造时不要任何 env / 磁盘副作用。本节主体讲这条 |
| **`agentao.embedding.build_from_environment(...)`** | 宿主沿用 CLI 的项目目录约定。会读 `.env`、`permissions.json`、`mcp.json`、内存目录后帮你把 `Agentao` 拼好。详见 [§ 工厂路径](#工厂路径-build-from-environment) |

| 场景 | 用哪条 |
|------|--------|
| 笔记本或脚本，希望和 CLI 一样读本机配置 | `build_from_environment` |
| 多租户 Web 应用，每个请求或租户一个 agent | `Agentao(...)` |
| 不带 `.env` 的容器或沙箱 | `Agentao(...)` |
| 宿主已自行管理状态的 IDE 插件 | `Agentao(...)` |
| 需要可复现运行的测试框架 | `Agentao(...)` |

两条路径最终都是产出一个 `Agentao` 实例。它们只有一种组合方式：`Agentao` 的每个关键字参数也都是工厂的覆盖项，覆盖项优先于工厂自动发现的值。所以 `build_from_environment(working_directory=wd, permission_engine=my_engine)` 保留其余发现结果，只换掉权限引擎。行不通的是半路混搭：直接调 `Agentao(...)`，再手工复刻工厂的发现逻辑，下个版本就会和工厂对不上。

---

## 33 个参数一览

`Agentao.__init__` 共 33 个参数，只有 `working_directory` 没有默认值。前五个（`api_key`、`base_url`、`model`、`temperature`、`max_tokens`）可以按位置传，其余都只能按关键字传。下面按配置对象分组，用不到的组可以直接跳过。

| 分组 | 参数 | 讲解位置 |
|------|------|----------|
| **会话根目录**（必填） | `working_directory` | 第 1 档 |
| **模型**，原始设置 | `api_key`、`base_url`、`model`（不传 `llm_client` 时三个都必填）；`temperature`、`max_tokens`、`api_format`、`extra_body`、`prompt_cache`、`prompt_cache_ttl` | 第 1–3 档 |
| **模型**，预构造 | `llm_client`（取代整行原始设置；两者同时传抛 `ValueError`） | 第 2 档 |
| **权限与交互** | `transport`、`permission_mode` *或* `permission_engine`、`plan_session` | 第 2–3 档 |
| **上下文与压缩** | `max_context_tokens`、`compaction_controller` | 第 2–3 档 |
| **工具** | `extra_tools`、`disable_tools` *或* `enabled_tools` | 第 2 档 |
| **MCP** | `mcp_manager`，*或* `extra_mcp_servers` 与 `mcp_registry`（这两个可以一起传） | 第 2–3 档 |
| **提示词** | `project_instructions` | 第 2 档 |
| **自带对象** | `logger`、`memory_manager`、`skill_manager`、`filesystem`、`shell` | 第 3 档 |
| **可选子系统**（默认关闭） | `bg_store`、`sandbox_policy`、`replay_config`、`enable_builtin_agents` | 第 3 档 |

用*或*连接的参数、以及两行"模型"，彼此互斥：两边都传会抛 `ValueError`；见[互斥规则](#互斥规则)。

---

## 第 1 档 · 能跑起来的最小调用

**四个参数**，其他都有默认值。

```python
from pathlib import Path
from agentao import Agentao

agent = Agentao(
    api_key="sk-...",
    base_url="https://api.openai.com/v1",
    model="gpt-5.4",
    working_directory=Path("/tmp/my-session"),
)
```

| 参数 | 类型 | 为什么 |
|------|------|--------|
| `api_key` | `str` | LLM 凭据 |
| `base_url` | `str` | 端点：OpenAI、DeepSeek、Gemini gateway、vLLM …… 没有内置默认值 |
| `model` | `str` | 模型 ID |
| `working_directory` | `Path` | 这个会话的项目根目录。**构造时冻结**——文件 / Shell / 记忆全部相对它 |

不传 `llm_client=` 时，`api_key`、`base_url`、`model` 少任何一个都会抛 `ValueError`。直接调用 `Agentao(...)` **不读任何环境变量**：`OPENAI_API_KEY` 等只由 `build_from_environment()` 读取（见[工厂路径](#工厂路径-build-from-environment)）。另一种最小调用是 `Agentao(llm_client=my_client, working_directory=...)`，凭据放在 client 上。

::: warning 千万别省略 `working_directory`
Web 服务 / 多租户进程里 `Path.cwd()` 是**进程全局**——并发会话会互相污染。0.3.0 起这个关键字必传，不传会从 Python 签名分派直接抛 `TypeError`。
:::

---

## 第 2 档 · 生产常用

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

`PermissionEngine(project_root=workdir)` 不传 `rules=` 也不传 `user_root=` 时，不加载任何规则文件：`<workdir>/.agentao/permissions.json` 从不加载，只会发警告。所以上面构造 `engine` 的两行，与不传 `permission_engine=engine`、改传 `permission_mode="workspace-write"` 得到的规则相同。有规则要加载时才自己构造引擎：`rules=[...]`，或用 `user_root=` 加载 `<user_root>/permissions.json`。

| 参数 | 类型 | 默认 | 作用 |
|------|------|------|------|
| `max_tokens` | `int \| None` | `None`——用 client 的 65,536 | 每次调用的输出上限 |
| `temperature` | `float \| None` | `None` | 采样温度。`None` 表示不发送 `temperature`，由 provider 默认值决定（0.5.7；此前为 `0.2`） |
| `extra_body` | `Dict[str,Any]` | `None` | 原样转发给 LLM `.create()` 的 SDK `extra_body` —— 封闭请求构建够不到的参数的逃生舱（`reasoning_effort` / `top_p` / `seed` / `response_format` / provider 专有字段）。**仅关键字。** 子 agent 继承;日志中凭据键脱敏。**与 `llm_client` 互斥**。详见下文 |
| `api_format` | `str` | `"openai-completions"` | 对 `base_url` 说哪种线路协议（0.5.0）：`"openai-completions"`、`"anthropic-messages"`（Anthropic 的 Messages API，走官方 SDK）或 `"openai-responses"`（OpenAI 的 Responses API，0.5.3）。**仅关键字**；显式配置，从不根据 URL 或模型名推断；之后只能由 `set_provider(..., api_format=)` 改变；子代理继承；**与 `llm_client=` 互斥**。未知值抛 `ValueError`。在 `anthropic-messages` 上，`base_url` 是 API 根，不发送 `temperature`，扩展思考走 `extra_body`。环境变量等价物：`{PROVIDER}_API_FORMAT` —— 见[附录 B](/zh/appendix/b-config-keys) |
| `transport` | `CoreTransport` | `NullTransport()` | UI 桥：事件流 + 工具确认 + ask_user + 最大迭代回调，详见 [第 4 部分](/zh/part-4/) |
| `permission_engine` | `PermissionEngine` | `None`——没有引擎，不执行任何规则（`build_from_environment` 会建一个根在 `working_directory` 的） | 规则级权限引擎；权限模式和 `set_permission_mode` 都需要它，详见 [5.4](/zh/part-5/4-permissions) |
| `permission_mode` | `str` | `None`——什么都不建 | `"read-only"` / `"workspace-write"` / `"full-access"`（不接受 `"plan"`）：以该模式构造 `PermissionEngine(project_root=working_directory, rules=[])`，不读取任何规则文件，并以该模式启动、不发事件。与 `permission_engine` 互斥。详见 [5.4](/zh/part-5/4-permissions) |
| `max_context_tokens` | `int` | `200_000` | 超过即触发对话压缩 |
| `extra_mcp_servers` | `Dict[str,Dict]` | `None` | 给单个会话注入 MCP 服务器，不动 `.agentao/mcp.json`；同名会覆盖。适合按租户切换 token |
| `extra_tools` | `Sequence[Tool]` | `None` | 注入 / 替换工具（实例；最后注册，同名覆盖内置）。见 [5.1](/zh/part-5/1-custom-tools) |
| `disable_tools` | `Iterable[str]` | `None` | 按名跳过这些内置工具（未知名 → `ValueError`）。**与 `enabled_tools` 互斥** |
| `enabled_tools` | `Iterable[str]` | `None` | 保留的 agentao 自有工具白名单；`None`=关，任意可迭代含 `set()`=开。**与 `disable_tools` 互斥** |
| `llm_client` | `LLMClient` | （由凭据自动构造） | 注入预构造客户端，完全控制 logger / 日志文件。**与所有原始模型设置互斥**：`api_key` / `base_url` / `model` / `temperature` / `max_tokens` / `extra_body` / `prompt_cache` / `prompt_cache_ttl` / `api_format`（自带 client 的 host 把它们传给该 client） |
| `project_instructions` | `str` | （从 `<wd>/AGENTAO.md` 读） | 直接传 AGENTAO.md 内容，跳过磁盘读 |

::: tip 异步宿主走 `arun()`
`agent.chat(...)` 是同步的。异步宿主用 `await agent.arun(user_message)`，它在 Agentao 自己的工作线程池里跑这一轮，不占用事件循环的默认执行器。取消、replay、`max_iterations` 在两条接口上语义完全一致。线程池为什么独立、对容量规划意味着什么，见 [2.3 异步宿主](./3-lifecycle#异步宿主-arun-与轮次线程池)。
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
| `mcp_registry` | `MCPRegistry` | `None`：读 `<wd>/.agentao/mcp.json` 和 `~/.agentao/mcp.json`，与工厂的 `FileBackedMCPRegistry` 读同样的文件 | `Agentao.__init__` 期间 MCP 初始化读一次 `list_servers()` |
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

**协议**始终从 `agentao.host.protocols` 导入（公共表面）。默认实现住在 `agentao.capabilities` 和 `agentao.memory`。任何槽位传 `None` 表示*回退到本地默认*，**不是** *禁用*；要真正禁用某个能力，请注入一个调用即抛错的实现。MCP 的话，`mcp_registry=InMemoryMCPRegistry()`（来自 `agentao.mcp`）不加载任何服务器。

方法签名对上还不是契约的全部。`FileSystem.write_text` 替换已有文件时必须是**原子的**，这样进程在写到一半被杀时，用户的文件不会被截断。委托给 `LocalFileSystem` 的包装器自动满足这一点。从零写的实现必须先把内容写到别处，再用一次操作换进去，就像 `LocalFileSystem` 用同目录临时文件加 `os.replace` 那样（[`host-api.md`](https://github.com/jin-bo/agentao/blob/main/docs/reference/host-api.md#filesystemwrite_text-must-replace-atomically)）。

::: tip 端到端可运行示例 — [`examples/protocol-injection/`](https://github.com/jin-bo/agentao/tree/main/examples/protocol-injection)
用四个小适配器（内存 FS、审计日志 shell、dict 记忆存储、程序化 MCP registry）替换全部四个槽位，再通过 6 条 smoke 测试断言每个槽位都被实际调用。无需 `OPENAI_API_KEY`，运行 `uv sync --extra dev && PYTHONPATH=. uv run pytest tests/`。
:::

多租户 FS 隔离见 [6.4](/zh/part-6/4-multi-tenant-fs)。
:::

::: details 记忆 / 技能 / MCP 管理器 — `memory_manager` / `skill_manager` / `mcp_manager` / `mcp_registry`
当你不想让 Agentao 用默认值构造某个子系统时——典型场景：管理器是跨会话共享的，或者想用程序化配置而不是磁盘查找。

| 参数 | 替代的默认行为 |
|------|--------------|
| `memory_manager` | 默认会打开 `<wd>/.agentao/memory.db` 的 `MemoryManager`。由你关闭：`agent.close()` 不关它 |
| `skill_manager` | 自带技能扫描 |
| `mcp_manager` | `.agentao/mcp.json` 发现 + 生命周期。由你断开：`agent.close()` 不断开它。**与 `extra_mcp_servers=` 和 `mcp_registry=` 互斥** |
| `mcp_registry` | `load_mcp_config(...)` 配置源。要程序化注册就传 `InMemoryMCPRegistry`。**与 `mcp_manager=` 互斥** |
:::

::: details 可选启用的子系统 — `bg_store` / `sandbox_policy` / `replay_config` / `enable_builtin_agents`
**默认 `None`（或 `False`）= 完全禁用**。不用就不付任何代价。

| 参数 | `None` 时 |
|------|----------|
| `bg_store` | 后台任务工具（`check_background_agent` / `cancel_background_agent`）不注册；子 agent 的工具 schema 抠掉 `run_in_background` 字段；`/agent bg\|dashboard\|cancel\|delete\|logs\|result` 等 CLI 子命令 no-op + 警告 |
| `sandbox_policy` | Shell 不套 macOS `sandbox-exec`。这个策略只包 `run_shell_command`，只在 macOS 上生效，而且只有 `~/.agentao/sandbox.json` 设了 `enabled: true` 才启用（`<wd>/.agentao/sandbox.json` 会被忽略）；它不限制文件工具（[`sandbox/policy.py`](https://github.com/jin-bo/agentao/blob/main/agentao/sandbox/policy.py)） |
| `enable_builtin_agents`（`False`） | 内置子代理（`codebase-investigator`、`generalist`）不注册为委派工具。工厂会读 `settings.json` 里的 `agents.enable_builtin` |
| `replay_config` | 构造时不读 `<wd>/.agentao/settings.json` 里的 `replay` 块；在 `start_replay()` / `reload_replay_config()` 创建 `ReplayManager` 之前不挂它。单独调 `start_replay()` 创建的是关闭 replay 的管理器（返回 `None`）；`reload_replay_config()` 才读这个块，但它本身不开始记录：之后再调 `start_replay()`，且只有其中 `enabled` 为真时才记录 |

传了值时：

- **`bg_store`** 启用后台子代理。`BackgroundTaskStore(persistence_dir=wd)` 把记录存在 `<wd>/.agentao/background_tasks.json`。重启后，仍在运行的任务会被标成 `failed`：记录还在，工作没了。同时最多跑 `max_concurrent` 个（默认 6，`None` 表示不设上限），超出的启动请求直接拒绝，不排队。一次性进程里保持 `None`，`agentao run` 就是这么做的，因为工作线程是守护线程，进程退出时会被切断。细节见 [附录 A.1](/zh/appendix/a-api-reference#构造器)。
- **`replay_config`** 只有在宿主调用 `agent.start_replay()` 之后才往 `<wd>/.agentao/replays/*.jsonl` 写记录。CLI 和 ACP 每个会话都会调一次，工厂和 `Agentao(...)` 都不会。replay 只记录会话，不会重跑会话（[6.6](/zh/part-6/6-observability#维度三-session-replay)）。
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

::: details 很少设置的 — `prompt_cache` / `prompt_cache_ttl` / `compaction_controller` / `plan_session`
| 参数 | 默认 | 作用 |
|------|------|------|
| `prompt_cache` | `None`（关） | `"anthropic"` 会在每个请求上放显式的提示缓存断点（最多 3 个）。默认关闭，因为端点是否支持未经验证。从不根据 URL 或模型名推断。环境变量等价物：`LLM_PROMPT_CACHE`，只由工厂读取 |
| `prompt_cache_ttl` | `None`（provider 默认，`"5m"`） | `"5m"` 或 `"1h"`。`prompt_cache` 关闭时忽略。环境变量等价物：`LLM_PROMPT_CACHE_TTL` |
| `compaction_controller` | `None` | 一个同步可调用对象，决定允许、取消压缩，或提供摘要。失败即放行：抛异常按允许处理。见 [`host-api.zh.md`](https://github.com/jin-bo/agentao/blob/main/docs/reference/host-api.zh.md) |
| `plan_session` | `None`（新建一个 `PlanSession`） | 计划模式状态。CLI 会传自己的；嵌入宿主一般不用管 |
:::

::: details 8 个 legacy 回调（已于 0.5.0 移除）
0.2.10 之前的接口。它们在 0.4.x 期间一直告警，现在**不再是参数**：传入任何一个都会抛 `TypeError`。要么直接走 Transport，要么保留回调、包一层 —— `agentao.embedding.compat.build_compat_transport(...)` 接受同样的八个名字，且没有被废弃：`Agentao(transport=build_compat_transport(confirmation_callback=...), ...)`。

| 已移除的参数 | 替代 |
|-------------|------|
| `confirmation_callback` | `SdkTransport(confirm_tool=...)` |
| `step_callback` | `on_event=` + `TOOL_START` / `TURN_START` |
| `thinking_callback` | `on_event=` + `THINKING` |
| `ask_user_callback` | `SdkTransport(ask_user=...)` |
| `output_callback` | `on_event=` + `TOOL_OUTPUT` |
| `tool_complete_callback` | `on_event=` + `TOOL_COMPLETE` |
| `llm_text_callback` | `on_event=` + `LLM_TEXT` |
| `on_max_iterations_callback` | `SdkTransport(on_max_iterations=...)` |

⚠️ **只有前五个参数可以按位置传**（`api_key`、`base_url`、`model`、`temperature`、`max_tokens`）。那些回调过去夹在 `max_context_tokens`、`permission_engine`、`transport`、`plan_session` 之间，所以这四个现在是仅限关键字 —— 仍把回调传在第六位的调用会得到 `TypeError`，而不是被悄悄改绑。完整指南：[`docs/migration/0.4.x-to-0.5.0.zh.md`](https://github.com/jin-bo/agentao/blob/main/docs/migration/0.4.x-to-0.5.0.zh.md)。
:::

---

## 互斥规则

违反以下任一条都会在构造时抛 `ValueError`：

| 不能同时传 | 原因 |
|------------|------|
| `llm_client=` + 任意 `api_key` / `base_url` / `model` / `temperature` / `max_tokens` / `extra_body` / `prompt_cache` / `prompt_cache_ttl` / `api_format` | 注入的 client 已经是凭据源 —— 改为把这些直接传给该 client |
| `mcp_manager=` + `extra_mcp_servers=` | 会话级合并需要 Agentao 自己构造的 manager |
| `mcp_manager=` + `mcp_registry=` | Registry 是配置源，manager 是构造结果 |
| `permission_engine=` + `permission_mode=` | 一个是引擎，一个会构造引擎——在你自己的引擎上设置模式，或在构造后调用 `set_permission_mode()` |

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

按顺序，它做这些事（[`embedding/factory.py`](https://github.com/jin-bo/agentao/blob/main/agentao/embedding/factory.py)）：

| # | 步骤 | 出错时 |
|---|------|--------|
| 1 | 把 `working_directory` 解析成绝对路径并冻结。不传就是 `Path.cwd()`，嵌入宿主不应依赖它 | — |
| 2 | 读 `<wd>/.agentao/settings.json` 里的工厂开关，例如 `agents.enable_builtin` | 文件格式不对时记日志并忽略 |
| 3 | 把 `<wd>/.env` 载入 `os.environ`。没有这个文件时，从进程 cwd 往上找最近的 `.env`。环境里已有的非空变量优先 | — |
| 4 | 读 LLM 设置：`LLM_PROVIDER`（默认 `OPENAI`）与 `{PROVIDER}_API_KEY` / `_BASE_URL` / `_MODEL` / `_API_FORMAT`，以及 `LLM_TEMPERATURE`、`LLM_MAX_TOKENS`、`LLM_EXTRA_BODY`、`LLM_PROMPT_CACHE`、`LLM_PROMPT_CACHE_TTL`。传了 `llm_client=` 就跳过 | `LLM_TEMPERATURE` / `LLM_MAX_TOKENS` 不是数字时抛错。`LLM_EXTRA_BODY` 有问题时警告并跳过 |
| 5 | 读 `~/.agentao/permissions.json`，用其中规则构造 `PermissionEngine(project_root=wd, user_root=~/.agentao)`。文件里的 `shell` 块决定 shell 执行器 | **失败即关闭**：文件存在但读不了或不合法时抛 `PermissionConfigError`。若悄悄丢掉它，`deny` 规则会变成询问，对 `mcp_*` 工具甚至变成不提示。`<wd>/.agentao/permissions.json` 从不加载，只会警告 |
| 6 | 接上可选子系统：`BackgroundTaskStore(persistence_dir=wd)`、`SandboxPolicy(project_root=wd)`、从 `settings.json` 的 `replay` 块读出的 `replay_config`，以及来自第 2 步的 `enable_builtin_agents` | replay 块格式不对时不启用 replay |
| 7 | 除非你传了 `mcp_registry=` 或 `mcp_manager=`，否则用 `FileBackedMCPRegistry(project_root=wd, user_root=~/.agentao)` | — |
| 8 | 打开记忆库：项目库 `<wd>/.agentao/memory.db`，用户库 `~/.agentao/memory.db` | 项目库回落到内存数据库。用户库打不开时，这次会话就没有用户库：已有的用户记忆读不到，新的用户作用域写入存进项目库，并记一条日志（[5.5](/zh/part-5/5-memory#降级-文件系统不可写怎么办)） |
| 9 | 把以上全部作为显式关键字参数传给 `Agentao(...)`。**你的覆盖项优先**于一切自动发现的值 | 构造抛错时，工厂会关掉它打开的记忆库 |

`bg_store`、`sandbox_policy`、`replay_config` 显式传 `None` 即禁用该子系统。`permission_mode=` 作用在第 5 步的引擎上，所以用户的规则保留。CLI 和 ACP `session/new` 都通过这个工厂构造 agent。

**谁读环境。** 工厂把构造时 CLI 风格的发现集中在一处：环境变量、`.env`、`permissions.json`、`settings.json` 和 `sandbox.json`。直接 `Agentao(...)` 不做这些发现，但在默认值下仍会碰一些文件：

| 默认值 | 读什么 | 想避免就传 |
|--------|--------|-----------|
| `project_instructions=None` | `<wd>/AGENTAO.md` | `project_instructions=` |
| `skill_manager=None` | `~/.agentao/skills/`、`<wd>/.agentao/skills/`、`<wd>/skills/`、`<wd>/.agentao/skills_config.json`。首次运行时还会把内置技能复制到 `~/.agentao/skills/` | `skill_manager=` |
| `memory_manager=None` | 打开 `<wd>/.agentao/memory.db`（只有项目作用域） | `memory_manager=` |
| `mcp_registry=None` | `<wd>/.agentao/mcp.json` 和 `~/.agentao/mcp.json` | `mcp_registry=` 或 `mcp_manager=` |
| `logger=None`（且 `llm_client=None`） | 创建并写入 `<wd>/agentao.log` | `logger=` 或 `llm_client=`（[6.6](/zh/part-6/6-observability#接管-agentao-的-logger)） |

五项都传了，构造 agent 时就不会创建任何文件。这只管构造：轮次里仍可能写盘，比如 replay、溢出到 `.agentao/tool-outputs/` 的工具输出，以及工具本身做的事。

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
        base_url=os.environ["OPENAI_BASE_URL"],
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
- **0.5.0** —— 8 个 legacy 回调 kwarg（`confirmation_callback` / `step_callback` / `thinking_callback` / `ask_user_callback` / `output_callback` / `tool_complete_callback` / `llm_text_callback` / `on_max_iterations_callback`）已从 `Agentao(...)` 签名中**移除**，`max_tokens` 之后的每个参数都变为仅限关键字。请用 `transport=SdkTransport(...)`，或用 `agentao.embedding.compat.build_compat_transport(...)` 把旧回调包起来。
- **0.4.x** —— 8 个 legacy 回调每次构造发一次 `DeprecationWarning`（自 0.4.5 起）。
- **0.3.4** — Capability 协议（`FileSystem` / `ShellExecutor`）在 `agentao.host.protocols` 上 re-export。**始终从这里导入**，不要伸手到内部的 `agentao.capabilities.*`。
- **0.3.0** — `working_directory=` 必传（不传抛 `TypeError`）。新增 `mcp_registry=` 作为稳定的配置源；默认 `FileBackedMCPRegistry` 与 #17 之前的磁盘读一致。
- **0.2.16** — 显式注入面（`memory_manager` / `skill_manager` / `mcp_manager` / `filesystem` / `shell` …）落地；`replay_config` / `sandbox_policy` / `bg_store` 默认改为 `None`。
- **0.2.10** — 核心运行时与 CLI 解耦；8 个 legacy 回调通过 `build_compat_transport()` 仍可用。

从 0.2.15 升级：[`docs/migration/0.2.15-to-0.3.3.md`](https://github.com/jin-bo/agentao/blob/main/docs/migration/0.2.15-to-0.3.3.md)。
:::

## TL;DR

- **33 个参数，只有一个必填**：`working_directory`（`Path`，构造时冻结）。[上面的一览表](#_33-个参数一览)按用途给其余参数分了组。
- **能跑起来的最小调用**：`api_key` + `base_url` + `model` + `working_directory`，或 `llm_client` + `working_directory`。直接调用 `Agentao(...)` 不读环境变量。
- **生产环境常加的**：`transport`、`permission_mode`（或 `permission_engine`）、`max_context_tokens`、`extra_mcp_servers`、`project_instructions`、`temperature`。
- **其他全部是可选 / 高级** —— 能力协议、自定义管理器、提示缓存、压缩控制、沙箱 / replay / 后台 / 子代理子系统。
- **两条工厂**：`build_from_environment()` 走 CLI 约定；直接 `Agentao(...)` 走显式控制。只想改工厂结果的某一部分，就把那个关键字作为覆盖项传给工厂。

→ 下一节：[2.3 生命周期管理](./3-lifecycle)
