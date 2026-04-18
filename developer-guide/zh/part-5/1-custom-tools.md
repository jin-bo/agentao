# 5.1 自定义工具（Custom Tools）

**自定义工具是让 Agent 调用你的业务 API 的首选方式**——比起让 LLM 读你的 OpenAPI 规范再生成 HTTP 请求，直接写一个 `Tool` 子类既更可靠又更安全。

## Tool 基类回顾

源码：`agentao/tools/base.py:11-115`

```python
from abc import ABC, abstractmethod
from typing import Dict, Any
from agentao.tools.base import Tool

class MyTool(Tool):
    @property
    def name(self) -> str:
        return "unique_tool_name"          # 全局唯一

    @property
    def description(self) -> str:
        return "给 LLM 看的一句话描述——决定 LLM 是否调用这个工具"

    @property
    def parameters(self) -> Dict[str, Any]:
        """JSON Schema，描述参数（会直接传给 LLM 做 function calling）。"""
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "..."},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        }

    @property
    def requires_confirmation(self) -> bool:
        return True     # 写操作、网络、跑命令都应 True

    @property
    def is_read_only(self) -> bool:
        return False    # 纯读返回 True，可优化权限判断

    def execute(self, **kwargs) -> str:
        """真实逻辑。返回字符串，LLM 会读它做下一步决策。"""
        query = kwargs["query"]
        limit = kwargs.get("limit", 10)
        ...
        return f"Found {len(results)} items: {results}"
```

**6 个必须掌握的要点**：

| 属性/方法 | 必填 | 说明 |
|---------|------|------|
| `name` | ✅ | 全局唯一标识；冲突会被 override 并打 warning |
| `description` | ✅ | **LLM 唯一的决策依据**——写清楚"什么时候用、参数含义、返回什么" |
| `parameters` | ✅ | JSON Schema；任何 OpenAI function calling 支持的 schema 都能用 |
| `execute(**kwargs) -> str` | ✅ | 返回纯字符串；不能返回 dict/bytes |
| `requires_confirmation` | ❌ | 写/网络/危险操作应 True，走 confirm_tool 流程 |
| `is_read_only` | ❌ | 纯读时 True；权限引擎/Plan 模式据此优化 |

## 为什么 `execute` 只能返回字符串？

因为工具结果要注入到 LLM 消息历史（OpenAI function calling 的 `role:tool` 消息）。非字符串结果不兼容。正确做法：

```python
def execute(self, **kwargs) -> str:
    data = call_my_api(kwargs)
    # 用 JSON 字符串表达结构化数据，LLM 会解析
    return json.dumps({
        "status": "ok",
        "data": data,
        "count": len(data),
    }, ensure_ascii=False)
```

大返回（> 几十 KB）应**先截断或分页**，否则占爆上下文窗口。

## 怎样写一个 "能让 LLM 用对的" description

这是比写代码更关键的环节。差的描述让 LLM 误调用；好的描述让 LLM 知道什么时候该用、用完怎么处理。

### ❌ 反例

```python
description = "Get orders"
```

LLM 不知道：什么叫 order？给谁的？参数传什么？返回格式？

### ✅ 正面示范

```python
description = """
查询客户订单。用于：用户询问"我的订单""最近一单""订单详情"时。

参数：
- `customer_id` (必填): 客户 ID，从用户会话里拿，不要凭空猜
- `status`: 订单状态过滤 ("pending" / "shipped" / "delivered" / "all")，默认 "all"
- `limit`: 返回条数，默认 10，最大 50

返回：JSON 字符串，含 orders 数组；每条包含 id/status/total/created_at 字段。

注意：
- 不要把 customer_id 暴露给用户；响应里只讲订单内容
- 如果 orders 为空，直接告知用户"无订单"
"""
```

经验：**用第二人称写给 LLM 本身看**——"当用户说 X 时，调用我"。

## 路径解析助手

Tool 基类提供两个辅助方法处理文件路径：

```python
class MyFileTool(Tool):
    def execute(self, path: str, **kw) -> str:
        # _resolve_path: 支持 ~ 展开；绝对路径原样；相对路径拼 working_directory
        p = self._resolve_path(path)
        return p.read_text()
```

`self.working_directory` 是 Agentao 在注册时自动绑定的——**多实例场景下**每个 Agent 的工具各自指向不同目录，不会串线。用这两个助手而不是直接 `Path(raw)` 能免费获得多租户隔离。

## 注册工具

Agentao 目前不接受构造时传入自定义工具。**注册时机：**

```python
from pathlib import Path
from agentao import Agentao
from agentao.transport import SdkTransport

# 1. 先构造 Agent（此时已注册完所有内置工具）
agent = Agentao(
    working_directory=Path("/tmp/session-x"),
    transport=SdkTransport(),
)

# 2. 追加你的自定义工具
my_tool = MyTool()
my_tool.working_directory = agent.working_directory   # 显式绑定（否则用进程 cwd）
agent.tools.register(my_tool)

# 3. 正常使用
agent.chat("帮我查客户 123 的订单")
```

⚠️ **注意**：
- `agent.tools` 是公开字段（`ToolRegistry` 实例）——随时可 `register`
- 但**必须在第一次 `chat()` 之前**注册，否则 LLM 看不到（openai 工具列表是每轮重建的，理论上热加也可以，但未稳定）
- 冲突时后注册的覆盖先注册的，日志里会打 warning

## 完整示例：调用业务 API 的工具

```python
"""你的 SaaS 后端把订单查询暴露给 Agent。"""
import json
from typing import Dict, Any
from agentao.tools.base import Tool

class GetCustomerOrdersTool(Tool):
    def __init__(self, backend_client, tenant_id: str):
        super().__init__()
        self.backend = backend_client
        self.tenant_id = tenant_id   # 绑定到会话

    @property
    def name(self) -> str:
        return "get_customer_orders"

    @property
    def description(self) -> str:
        return (
            "Query this tenant's customer orders. "
            "Use when the user asks about 'my orders', 'order status', etc. "
            "Args: customer_id (required), status (optional: pending/shipped/delivered/all, default all), "
            "limit (optional int, max 50, default 10). "
            "Returns JSON: {status, orders:[{id, status, total, created_at}]}. "
            "Never expose internal tenant_id or api tokens in your reply."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "shipped", "delivered", "all"],
                    "default": "all",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            "required": ["customer_id"],
        }

    @property
    def requires_confirmation(self) -> bool:
        return False    # 读 API，不需要用户额外确认

    @property
    def is_read_only(self) -> bool:
        return True

    def execute(self, **kwargs) -> str:
        try:
            orders = self.backend.list_orders(
                tenant_id=self.tenant_id,
                customer_id=kwargs["customer_id"],
                status=kwargs.get("status", "all"),
                limit=min(kwargs.get("limit", 10), 50),
            )
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})
        return json.dumps({
            "status": "ok",
            "orders": [o.to_dict() for o in orders],
        }, ensure_ascii=False)


# --- 在 Web 处理里的使用 ---
def make_agent_for_tenant(tenant, backend):
    agent = Agentao(
        working_directory=Path(f"/tmp/{tenant.id}"),
        transport=SdkTransport(...),
    )
    # 注入业务工具（每会话一个独立实例）
    agent.tools.register(GetCustomerOrdersTool(backend, tenant.id))
    agent.tools.register(CreateRefundTool(backend, tenant.id))
    agent.tools.register(SendEmailTool(backend, tenant.id))
    return agent
```

## 写 Tool 的常见陷阱

### ❌ 在 `execute` 里抛异常

```python
def execute(self, **kwargs) -> str:
    return self.backend.create_invoice(...)  # 抛 HTTPError 怎么办？
```

抛出去会把 Agent 整个 `chat()` 打断。**捕获并返回错误字符串**让 LLM 看见并决定怎么处理：

```python
def execute(self, **kwargs) -> str:
    try:
        result = self.backend.create_invoice(...)
        return json.dumps({"status": "ok", "id": result.id})
    except BackendError as e:
        return json.dumps({"status": "error", "message": str(e)})
```

### ❌ 描述过于宽泛

`"Do things with customer data"` —— LLM 会乱用它。**每个工具只做一件事，描述只讲这件事。**

### ❌ 忘记 `requires_confirmation=True`

写操作、退款、发邮件、跑命令、删数据——**一切有副作用的都应确认**。没加这个就等于给 LLM 一把没有保险的枪。

### ❌ 参数没做边界检查

LLM 可能传 `limit=99999`。工具里务必 clamp/校验：

```python
limit = min(max(1, kwargs.get("limit", 10)), 50)
```

### ❌ 返回数据太大

```python
return json.dumps(all_1000_orders)   # 可能 500KB
```

大返回占爆 context 且 LLM 处理慢。**先截断、分页、摘要**，让 LLM 决定要不要翻下一页：

```python
return json.dumps({
    "status": "ok",
    "orders": orders[:10],
    "total_count": len(orders),
    "has_more": len(orders) > 10,
    "next_cursor": cursor if len(orders) > 10 else None,
})
```

## 工具 vs 技能 vs MCP：怎么选

| 你的需求 | 用什么 |
|---------|-------|
| 调 HTTP API / 数据库 / 内存对象 | **工具**（本节） |
| 教 LLM "按公司规范做事" | **技能**（[5.2](./2-skills)） |
| 集成外部现成的工具服务（GitHub、文件系统、数据库） | **MCP**（[5.3](./3-mcp)） |

一个产品通常三者混用：工具封自己的业务、MCP 接第三方、技能统一风格。

→ 下一节：[5.2 技能与插件目录](./2-skills)
