# 2.2 构造器完整参数表

`Agentao.__init__` 的签名（`agentao/agent.py`，`Agentao.__init__`）：

```python
Agentao(
    api_key:          Optional[str]    = None,
    base_url:         Optional[str]    = None,
    model:            Optional[str]    = None,
    temperature:      Optional[float]  = None,
    # ── 下面 8 个是已废弃的 legacy 回调（仍保留向后兼容） ──
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
    working_directory:  Optional[Path]                     = None,
    extra_mcp_servers:  Optional[Dict[str, Dict[str, Any]]] = None,
)
```

## LLM 凭据（前 4 个参数）

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `api_key` | `str` | 从环境读 `OPENAI_API_KEY` | LLM 凭据 |
| `base_url` | `str` | 环境 `OPENAI_BASE_URL` 或官方默认 | 切换兼容端点（DeepSeek / Gemini gateway / vLLM 等） |
| `model` | `str` | 环境 `OPENAI_MODEL` 或厂商默认 | 模型 ID |
| `temperature` | `float` | 环境 `LLM_TEMPERATURE` 或 `0.2` | 采样温度 |

**推荐做法**：全部显式传入，不依赖环境变量。这样嵌入你的产品时，调试和审计都更清晰：

```python
agent = Agentao(
    api_key=settings.openai_api_key,
    base_url=settings.openai_base_url,
    model=settings.openai_model,
    temperature=0.1,
    ...
)
```

多租户/多会话场景下：每个会话可以有**不同的凭据**（比如按客户区分），详见第 7.2 节。

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
agent = Agentao(transport=transport, ...)
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

**这 8 个参数仍然被接收并工作**——Agentao 内部 `build_compat_transport()` 会把它们翻译成一个 `SdkTransport`。但新写的代码应直接用 Transport 路径，避免双通道混淆。

## 运行时行为控制

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `max_context_tokens` | `int` | `200_000` | 超过触发上下文压缩（第 7.3 节） |
| `plan_session` | `PlanSession` | `None` | 启用 Plan 模式；一般宿主无需设置 |
| `permission_engine` | `PermissionEngine` | 从 `.agentao/permissions.json` 加载 | 权限规则引擎（第 5.4 / 6.3 节） |

## 会话隔离（关键！）

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `working_directory` | `Path` | `None`（= 运行时动态 `Path.cwd()`） | **多实例嵌入必须显式传入** |

**为什么重要**：
- `None` 时，Agent 每次访问当前目录都读实时 `Path.cwd()` ——这是 CLI 行为，用户 `cd` 进去就生效
- 传入具体 `Path` 时，Agent 在构造时**冻结**到这个目录，之后所有文件操作、`AGENTAO.md`、`.agentao/` 配置、Shell CWD 全部相对它

嵌入场景（Web 服务器、ACP sessions）里，`Path.cwd()` 是进程全局状态；两个并发会话会**互相污染**。务必为每个 Agent 实例显式 `working_directory=`。

```python
# ❌ 不好：两个会话会串目录
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

## 全量示例：生产嵌入模板

```python
from pathlib import Path
import logging
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
    # 每会话独立的权限引擎（可按租户不同）
    engine = PermissionEngine(project_root=tenant_workdir)
    engine.set_mode(PermissionMode.WORKSPACE_WRITE)

    transport = SdkTransport(
        on_event=on_event,
        confirm_tool=confirm_tool,
        on_max_iterations=lambda n, _msgs: {"action": "stop"},
    )

    return Agentao(
        # LLM
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_BASE_URL"),
        model="gpt-5.4",
        temperature=0.1,
        # 交互
        transport=transport,
        # 隔离
        working_directory=tenant_workdir,
        # 资源治理
        max_context_tokens=128_000,
        # 安全
        permission_engine=engine,
        # 会话级 MCP
        extra_mcp_servers={
            "gh": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": tenant_token},
            },
        },
    )
```

下一节：[2.3 生命周期管理 →](./3-lifecycle)
