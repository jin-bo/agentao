# 4.1 Transport Protocol

Transport 是 Agent 运行时与宿主 UI/业务逻辑之间的**唯一接口**。理解它的四个方法，你就能在任何 UI 框架下集成 Agentao。

## 协议定义

源码：`agentao/transport/base.py`

```python
@runtime_checkable
class Transport(Protocol):
    # 单向事件（fire-and-forget）
    def emit(self, event: AgentEvent) -> None: ...

    # 阻塞式请求-响应
    def confirm_tool(self, tool_name: str, description: str, args: dict) -> bool: ...
    def ask_user(self, question: str) -> str: ...
    def on_max_iterations(self, count: int, messages: list) -> dict: ...
```

**关键设计**：

- `Transport` 是一个 `Protocol`（PEP 544）——**你不必继承任何基类**，只要实现这 4 个方法就算 Transport
- `@runtime_checkable` 让 `isinstance(x, Transport)` 可用（但不保证方法类型正确；类型检查应依赖静态工具）
- 四个方法一分为二：**1 个单向推送事件** + **3 个同步问答**

## 方法一：`emit(event)` — 推事件

```python
def emit(self, event: AgentEvent) -> None:
    """接收运行时事件。不得抛异常；错误必须吞掉。"""
```

**契约**：
- Agent 在关键节点调用 `emit`（一轮开始、工具开始/输出/结束、LLM 流式文本、思考、错误…）
- 实现**禁止抛异常**——抛了会被上游 try/except 吞掉，但可能破坏状态一致性
- 实现应**快速返回**——这是同步调用，慢了会拖慢整个 Agent

**典型实现**：

```python
def emit(self, event: AgentEvent) -> None:
    try:
        self._queue.put_nowait(event)   # 扔到队列让别的线程处理
    except Exception:
        pass  # 永不抛
```

事件类型全表见 [4.2 AgentEvent 事件清单](./2-agent-events)。

## 方法二：`confirm_tool(name, desc, args)` — 工具确认

```python
def confirm_tool(self, tool_name: str, description: str, args: dict) -> bool:
    """询问是否允许这个工具执行。
    True  → 允许
    False → 取消（Agent 收到 "Tool execution cancelled by user" 字符串，继续推理）
    """
```

**何时被调用**：
- Agent 准备调用某个 `requires_confirmation=True` 的工具时
- 默认触发者：`write_file`、`run_shell_command`、`web_fetch`、`web_search`

**阻塞语义**：这是**同步调用**——在你返回 True/False 前，Agent 的执行线程会停在这里。如果你的宿主是异步 UI，需要在 Transport 实现内部做阻塞等待（见 4.5）。

**返回 False 的后果**：
- 工具**不执行**
- Agent 收到一个"用户取消"的假结果
- LLM 会基于这个结果继续推理（通常会换个思路或停下来汇报）

## 方法三：`ask_user(question)` — 向用户反问

```python
def ask_user(self, question: str) -> str:
    """让 Agent 向用户反问一个开放问题，返回用户的回答。"""
```

**何时被调用**：
- Agent 主动调用内置工具 `ask_user` 时
- 典型场景：信息不足、多选一决策、要求澄清歧义需求

**默认兜底**：`NullTransport` 返回固定字符串 `"[ask_user: not available in non-interactive mode]"`，Agent 收到后会据此决定继续/放弃。

## 方法四：`on_max_iterations(count, messages)` — 迭代上限兜底

```python
def on_max_iterations(self, count: int, messages: list) -> dict:
    """Agent 达到 max_iterations（默认 100）时调用。
    返回 dict，key "action" 必填：
        "continue"        — 再给 N 轮继续跑
        "stop"            — 终止，返回当前结果
        "new_instruction" — 注入一条新 user 消息，需带 "message" 键
    """
```

**经典用法**：

```python
def on_max_iterations(self, count, messages):
    # 自动续一次
    if not hasattr(self, "_continued"):
        self._continued = True
        return {"action": "continue"}
    # 续过了，还卡着 → 让 LLM 总结并停下
    return {
        "action": "new_instruction",
        "message": "请基于目前的信息给出最终答复，不要再调用工具。",
    }
```

详细策略见 [4.6 最大迭代数兜底](./6-max-iterations)。

## 三种实现路径

| 路径 | 适合场景 | 复杂度 |
|------|---------|-------|
| **用 `SdkTransport` + 回调** | 90% 嵌入场景 | 最低 |
| **继承 `NullTransport` 覆盖部分方法** | 只关心某几个事件 | 低 |
| **从零实现 `Transport` Protocol** | 完全自定义（如 ACP、消息队列桥接） | 中 |

### 路径 A · SdkTransport

参见 [4.3](./3-sdk-transport)：

```python
from agentao.transport import SdkTransport

transport = SdkTransport(
    on_event=handle,
    confirm_tool=approve,
    ask_user=prompt,
    on_max_iterations=bail_out,
)
```

### 路径 B · 继承 NullTransport

当你只关心**部分事件**且想显式控制每个方法时：

```python
from agentao.transport import NullTransport, EventType

class MyTransport(NullTransport):
    def __init__(self, on_token):
        self.on_token = on_token

    def emit(self, event):
        if event.type == EventType.LLM_TEXT:
            self.on_token(event.data["chunk"])
        # 其他事件继续走 NullTransport 默认（即 pass）

    def confirm_tool(self, name, desc, args):
        # 只允许读类工具
        return name.startswith("read_") or name == "glob"
```

### 路径 C · 从零实现

最典型的真实例子：**ACP 服务端**。它不继承任何基类，而是把每次 `emit` 转成 `session/update` 通知、把 `confirm_tool` 转成 `session/request_permission` 请求发给 ACP Client。

```python
class MyCustomTransport:
    """把 Agent 事件桥到你自己的消息协议。"""
    def __init__(self, send_to_client):
        self.send = send_to_client

    def emit(self, event):
        self.send({"type": "agent_event",
                   "event": event.type.value,
                   "data": event.data})

    def confirm_tool(self, name, desc, args):
        # 发请求给客户端，同步等响应
        return self.send({"type": "confirm", ...}, wait=True)

    def ask_user(self, q):
        return self.send({"type": "ask", "question": q}, wait=True)

    def on_max_iterations(self, count, msgs):
        return {"action": "stop"}
```

## 线程与异步注意事项

- **同步 Agent 线程里调用 Transport**：所有 4 个方法在 Agent 的 chat() 循环里被**同步调用**
- 如果你的宿主是 asyncio：
  - `emit` 可以 `asyncio.run_coroutine_threadsafe(...)` 回主循环
  - `confirm_tool` / `ask_user` 需要跨线程同步等待（见 4.5 实现模式）

## 测试你的 Transport

```python
from agentao.transport import AgentEvent, EventType

def test_my_transport():
    t = MyTransport()
    # 1. emit 不得抛
    t.emit(AgentEvent(EventType.LLM_TEXT, {"chunk": "hi"}))
    # 2. confirm_tool 必须返回 bool
    assert isinstance(t.confirm_tool("x", "", {}), bool)
    # 3. ask_user 必须返回 str
    assert isinstance(t.ask_user("q?"), str)
    # 4. on_max_iterations 必须返回带 action 的 dict
    r = t.on_max_iterations(100, [])
    assert r["action"] in {"continue", "stop", "new_instruction"}
```

→ 下一节：[4.2 AgentEvent 事件清单](./2-agent-events)
