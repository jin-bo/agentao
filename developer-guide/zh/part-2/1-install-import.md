# 2.1 安装与包导入

## 安装

```bash
# 推荐：pin 一个精确版本
pip install 'agentao==0.2.14'

# uv 用户
uv add 'agentao==0.2.14'

# 带可选工具包
pip install 'agentao[pdf,excel,tokenizer]==0.2.14'
```

可选 extras 见 [1.5 运行环境要求](/zh/part-1/5-requirements)。

## 两个核心导入

```python
from agentao import Agentao                  # Agent 主类
from agentao.transport import SdkTransport    # 回调桥接，库嵌入首选
```

这两行基本就是嵌入的"门票"。其他符号按需：

```python
from agentao.transport import (
    Transport,         # Protocol 基类（自定义 Transport 时实现它）
    AgentEvent,        # 事件对象
    EventType,         # 事件枚举
    NullTransport,     # 静默 Transport（测试用）
)

from agentao.permissions import PermissionEngine, PermissionMode
from agentao.cancellation import CancellationToken, AgentCancelledError
from agentao.tools.base import Tool, ToolRegistry  # 写自定义工具时
```

## 懒加载优化

`agentao/__init__.py` 使用 **PEP 562 `__getattr__`** 做了懒加载，自 0.3.4（P0.5）起延迟范围进一步扩大——`from agentao import Agentao` 不再拉入 OpenAI SDK、BeautifulSoup、jieba、filelock、rich、prompt_toolkit、readchar、click、pygments、starlette、uvicorn。

当前还会被 lazy 的依赖（首次运行时再加载）：

| 库 | 首次触发点 |
|---|---|
| `openai` | `LLMClient(...)` 构造（仅默认 LLM 客户端会用到——宿主自己注入 `llm_client=` 永远不加载） |
| `bs4` / `httpx` | `WebFetchTool.execute()` / `WebSearchTool.execute()` |
| `jieba` | 首次进入 `MemoryRetriever` 的 recall 打分 |
| `filelock` | `SkillRegistry.save()`（CLI / `agentao plugin install`） |
| `mcp` SDK（`McpClientManager`、`McpTool`） | 首次接入 MCP server（`init_mcp` 或宿主传 `mcp_manager=`） |
| `rich`、`prompt_toolkit`、`readchar`、`click`、`pygments` | 只有 `agentao/cli/*` 会加载——嵌入路径完全不碰 |

对嵌入者意味着：

- `import agentao` 本身**很轻**——上述依赖都不会进入你的 import 时图。
- 访问 `agentao.Agentao` 会加载 agent 模块，但仍然不触发上面列出的 lazy 依赖。
- 宿主自己传入 `llm_client=` / `mcp_registry=` / filesystem / shell 时，OpenAI SDK 与 MCP SDK 永远不会被加载。
- `from agentao.memory import ...` 可以独立使用而不触发 LLM 栈导入。
- 不变量由两个测试守住：`tests/test_no_cli_deps_in_core.py`（AST 扫描；任何 lazy 依赖被顶层 import 到 `agentao/cli/` 之外即失败）与 `tests/test_import_cost.py`（子进程 `python -X importtime`；`import agentao` 跑出来的 trace 里不能出现这些名字）。

这让你可以把 Agentao 当作"条件依赖"——只有真正调用 Agent 功能时才承担导入成本。例如：

```python
# 你的 FastAPI app 模块级
import agentao  # 轻量，没有实际副作用

def get_agent():
    from agentao import Agentao  # 加载 agent 模块；openai / bs4 / jieba 仍不会被拉
    return Agentao(...)
```

## 版本与 `__version__`

```python
import agentao
print(agentao.__version__)   # "0.2.14"
```

生产代码建议在启动时校验：

```python
import agentao
from packaging.version import Version

MIN = Version("0.2.14")
if Version(agentao.__version__) < MIN:
    raise RuntimeError(f"Need agentao >= {MIN}, got {agentao.__version__}")
```

## 最小嵌入样板

```python
"""你的产品里嵌入 Agentao 的最小代码量。"""
from pathlib import Path
from agentao import Agentao
from agentao.transport import SdkTransport

def make_agent(workdir: Path, on_token=None) -> Agentao:
    transport = SdkTransport(
        on_event=lambda ev: on_token(ev.data.get("chunk", ""))
                             if on_event_is_text(ev) else None,
    )
    return Agentao(transport=transport, working_directory=workdir)

def on_event_is_text(ev) -> bool:
    return ev.type.name == "LLM_TEXT"
```

每个参数的含义见下一节。

→ [2.2 构造器完整参数表](./2-constructor-reference)
