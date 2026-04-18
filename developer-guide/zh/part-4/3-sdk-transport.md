# 4.3 SdkTransport 快速桥接

`SdkTransport` 是 Agentao 官方提供的**通用 Transport 实现**——四个回调，覆盖 90% 嵌入场景。

## 构造器

源码：`agentao/transport/sdk.py`

```python
class SdkTransport:
    def __init__(
        self,
        on_event:          Optional[Callable[[AgentEvent], None]]       = None,
        confirm_tool:      Optional[Callable[[str, str, dict], bool]]    = None,
        ask_user:          Optional[Callable[[str], str]]                = None,
        on_max_iterations: Optional[Callable[[int, list], dict]]         = None,
    ) -> None: ...
```

所有参数**都可选**。未提供的方法回退到 `NullTransport` 默认行为：

| 未设 | 回退行为 |
|------|---------|
| `on_event` | 丢弃事件（静默） |
| `confirm_tool` | 自动批准所有工具（返回 `True`） |
| `ask_user` | 返回固定字符串 `"[ask_user: not available in non-interactive mode]"` |
| `on_max_iterations` | `{"action": "stop"}` |

## 最简用法

```python
from agentao.transport import SdkTransport

transport = SdkTransport(
    on_event=lambda ev: print(ev.type.value, ev.data),
)
```

这已经够用来打印整个事件流——你的回调每收到一个事件就打印一行。

## 四个回调的典型实现

### 1) `on_event` — 事件分发器

```python
from agentao.transport import EventType

def on_event(event):
    match event.type:
        case EventType.LLM_TEXT:
            render_chunk(event.data["chunk"])
        case EventType.TOOL_START:
            open_tool_card(event.data)
        case EventType.TOOL_OUTPUT:
            append_tool_output(event.data)
        case EventType.TOOL_COMPLETE:
            close_tool_card(event.data)
        case EventType.ERROR:
            show_error(event.data)
        # 其他事件忽略
```

Python 3.10+ 可用 `match`；老版本用 `if/elif`。

### 2) `confirm_tool` — 确认弹窗

```python
def confirm_tool(tool_name: str, description: str, args: dict) -> bool:
    # 自动批准只读/安全工具
    if tool_name in {"read_file", "glob", "grep"}:
        return True

    # 其他全部弹窗
    return user_confirm_dialog(
        title=f"Allow {tool_name}?",
        details=f"{description}\n\n{json.dumps(args, indent=2)}",
    )
```

⚠️ 这是**同步阻塞**调用。如果你的 UI 是异步（如 Flask 异步/Electron），需要把弹窗调用转到 UI 线程再同步等待结果——详见 [4.5](./5-tool-confirmation-ui)。

### 3) `ask_user` — 文本反问

```python
def ask_user(question: str) -> str:
    return user_text_input_dialog(question) or ""
```

同样是阻塞调用。用户如果关窗不答，返回空字符串让 Agent 优雅处理。

### 4) `on_max_iterations` — 兜底

```python
def on_max_iterations(count: int, messages: list) -> dict:
    # 默认做法：问用户要不要继续
    answer = user_confirm_dialog(
        f"Agent reached {count} iterations. Continue?"
    )
    if answer:
        return {"action": "continue"}
    return {"action": "stop"}
```

## 把所有回调装在一个类里

当回调之间**共享状态**（比如同一个 UI 对象、同一个会话 ID）时，用类封装更干净：

```python
class ChatSession:
    def __init__(self, ui, session_id: str):
        self.ui = ui
        self.session_id = session_id
        self._events = []

    def on_event(self, event):
        self._events.append(event)
        self.ui.push_event(self.session_id, event)

    def confirm_tool(self, name, desc, args):
        return self.ui.ask_approval(self.session_id, name, desc, args)

    def ask_user(self, q):
        return self.ui.ask_text(self.session_id, q)

    def on_max_iterations(self, count, msgs):
        return self.ui.ask_continue(self.session_id, count)

# 用法
session = ChatSession(ui, "sess-123")
transport = SdkTransport(
    on_event=session.on_event,
    confirm_tool=session.confirm_tool,
    ask_user=session.ask_user,
    on_max_iterations=session.on_max_iterations,
)
agent = Agentao(transport=transport, working_directory=Path("/tmp/sess-123"))
```

## 多个订阅者：扇出事件流

一个 `on_event` 可以扇出到多个消费者：

```python
class EventFanout:
    def __init__(self):
        self.subscribers = []

    def subscribe(self, callback):
        self.subscribers.append(callback)

    def __call__(self, event):
        for cb in self.subscribers:
            try:
                cb(event)
            except Exception as e:
                logger.warning(f"Subscriber failed: {e}")

fanout = EventFanout()
fanout.subscribe(write_to_database)
fanout.subscribe(push_to_websocket)
fanout.subscribe(update_ui_state)

transport = SdkTransport(on_event=fanout)
```

## 向后兼容的 8 回调 API

Agentao 0.2.10 之前用 8 个独立回调（`confirmation_callback`, `step_callback`, `thinking_callback`, …）。这种写法**仍被接受**，但内部会通过 `build_compat_transport()` 自动转成一个 `SdkTransport`：

```python
# 老代码（仍能跑）
agent = Agentao(
    confirmation_callback=lambda n, d, a: True,
    llm_text_callback=lambda chunk: print(chunk, end=""),
    step_callback=lambda name, args: print(f"[{name}]"),
)

# 新代码（推荐）
def on_event(ev):
    if ev.type == EventType.LLM_TEXT:
        print(ev.data["chunk"], end="")
    elif ev.type == EventType.TOOL_START:
        print(f"[{ev.data['tool']}]")

agent = Agentao(transport=SdkTransport(
    on_event=on_event,
    confirm_tool=lambda n, d, a: True,
))
```

参见 [2.2 构造器参数表 · 已废弃的 8 个回调](/zh/part-2/2-constructor-reference#已废弃的-8-个回调legacy)。

## 常见陷阱

### ❌ 在 `on_event` 里抛异常

```python
def on_event(ev):
    if ev.type == EventType.LLM_TEXT:
        ui.append(ev.data["chunk"])   # 如果 ui 崩了？
```

`SdkTransport.emit` 会吞掉所有异常保护 Agent，但**下游副作用可能只做了一半**。写 `on_event` 时把每个分支的失败当作独立风险管理：

```python
def on_event(ev):
    try:
        dispatch(ev)
    except Exception as e:
        logger.warning("event dispatch failed", exc_info=e)
```

### ❌ 在 `confirm_tool` 里长时间卡死

如果你的确认弹窗 bug 导致永不返回，Agent 会**永远挂在那**。务必给同步等待加**超时**（见 4.5）。

### ❌ 混用 `transport` 和 legacy callbacks

```python
# 都传了——legacy 会被忽略
agent = Agentao(
    transport=my_transport,
    confirmation_callback=my_callback,  # 不会被调用！
)
```

二选一。`transport` 优先级最高。

## 最小 "什么都处理" 模板

```python
from agentao import Agentao
from agentao.transport import SdkTransport, EventType
from pathlib import Path

class AgentBridge:
    def on_event(self, ev):
        handlers = {
            EventType.TURN_START: self._turn,
            EventType.LLM_TEXT: self._text,
            EventType.THINKING: self._thinking,
            EventType.TOOL_START: self._tool_start,
            EventType.TOOL_OUTPUT: self._tool_out,
            EventType.TOOL_COMPLETE: self._tool_done,
            EventType.ERROR: self._error,
            EventType.AGENT_START: self._sub_start,
            EventType.AGENT_END: self._sub_end,
        }
        h = handlers.get(ev.type)
        if h: h(ev.data)

    def _turn(self, d): pass
    def _text(self, d): print(d["chunk"], end="", flush=True)
    def _thinking(self, d): print(f"\n[💭 {d['text']}]", flush=True)
    def _tool_start(self, d): print(f"\n[🔧 {d['tool']}]")
    def _tool_out(self, d): pass
    def _tool_done(self, d): print(f" ✓ ({d['duration_ms']}ms)")
    def _error(self, d): print(f"\n[❌ {d['message']}]")
    def _sub_start(self, d): print(f"\n[🧭 sub: {d['agent']}]")
    def _sub_end(self, d): print(f" ✓ {d['turns']} turns")

    def confirm_tool(self, name, desc, args):
        return input(f"Allow {name}? [y/N] ").lower() == "y"

    def ask_user(self, q):
        return input(f"Agent asks: {q}\n> ")

    def on_max_iterations(self, count, msgs):
        return {"action": "stop"}


bridge = AgentBridge()
transport = SdkTransport(
    on_event=bridge.on_event,
    confirm_tool=bridge.confirm_tool,
    ask_user=bridge.ask_user,
    on_max_iterations=bridge.on_max_iterations,
)
agent = Agentao(transport=transport, working_directory=Path.cwd())
print(agent.chat("hello"))
agent.close()
```

→ 下一节：[4.4 构建流式 UI](./4-streaming-ui)
