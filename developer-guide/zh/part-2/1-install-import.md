# 2.1 安装与包导入

## 安装

```bash
# 推荐：pin 一个精确版本
pip install 'agentao==0.2.10'

# uv 用户
uv add 'agentao==0.2.10'

# 带可选工具包
pip install 'agentao[pdf,excel,tokenizer]==0.2.10'
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

Agentao 的 `__init__.py` 使用 **PEP 562 `__getattr__`** 做了懒加载：

```python
# agentao/__init__.py 实际行为
__all__ = ["Agentao", "SkillManager"]
# Agentao / SkillManager 在首次访问时才导入 openai / mcp / tools 栈
```

影响你嵌入时：

- `import agentao` 本身**很轻**（不拉 openai / mcp 依赖）
- 首次访问 `agentao.Agentao` 或 `from agentao import Agentao` 才会触发完整加载
- `from agentao.memory import ...` 可以独立使用而不触发 LLM 栈导入

这让你可以把 Agentao 当作"条件依赖"——只有真正调用 Agent 功能时才承担导入成本。例如：

```python
# 你的 FastAPI app 模块级
import agentao  # 轻量，没有实际副作用

# 只在需要时才实例化
def get_agent():
    from agentao import Agentao  # 此时才真正加载
    return Agentao(...)
```

## 版本与 `__version__`

```python
import agentao
print(agentao.__version__)   # "0.2.11-dev" 或 "0.2.10"
```

生产代码建议在启动时校验：

```python
import agentao
from packaging.version import Version

MIN = Version("0.2.10")
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
