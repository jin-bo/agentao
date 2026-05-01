# 4.5 工具确认 UI

> **本节你会学到**
> - 为什么 `confirm_tool` 是同步的——以及如何干净地桥接到异步 UI
> - CLI / Web 弹窗 / IDE / Slack 各种形态的完整代码
> - "本次允许" / "永久允许" / 自动批准阈值的策略选择

`confirm_tool(name, desc, args) -> bool` 是 Agent 的安全阀门——本节讲怎么在**不同 UI 形态**下正确实现它。

## 核心挑战：同步阻塞

`confirm_tool` 在 Agent 的 `chat()` 线程里被**同步调用**，返回 `True`/`False` 才继续。UI 侧是异步的（用户点击按钮需要时间），所以你必须在 Agent 线程里**阻塞等待**异步响应。

```
Agent thread                        UI thread / async loop
      │                                     │
      │ call confirm_tool(...)              │
      │─────────────────────┐               │
      │                     ▼               │
      │            schedule "ask user"  ───►│   弹窗
      │            block on Future          │   ↓
      │                     ▲               │   点击
      │◄────────────────────┘               │
      │       Future.set_result(True)       │
      │                                     │
```

## 模式 A · CLI（终端）

最简单的形态：

```python
import readchar

def confirm_tool(name: str, desc: str, args: dict) -> bool:
    print(f"\n🔧 Tool: {name}")
    print(f"   Desc: {desc}")
    print(f"   Args: {args}")
    print("   [y] allow  [n] reject  [a] allow all")

    while True:
        k = readchar.readkey().lower()
        if k == "y": return True
        if k == "n": return False
        if k == "a":
            global _allow_all
            _allow_all = True
            return True
```

无跨线程问题——Agent 线程就是主线程，`input()` / `readchar` 自然阻塞。

## 模式 B · Web 模态框（异步后端）

FastAPI / asyncio 后端的 `confirm_tool` 需要把请求推给 WebSocket，等前端响应。

### 骨架

```python
import asyncio

class WebConfirmBridge:
    def __init__(self, ws, loop: asyncio.AbstractEventLoop, timeout=60):
        self.ws = ws
        self.loop = loop
        self.timeout = timeout
        self._pending: dict = {}   # request_id -> Future

    def confirm_tool(self, name: str, desc: str, args: dict) -> bool:
        # Agent 线程里——用 run_coroutine_threadsafe 桥到异步循环
        fut = asyncio.run_coroutine_threadsafe(
            self._ask(name, desc, args),
            self.loop,
        )
        try:
            return fut.result(timeout=self.timeout)
        except (asyncio.TimeoutError, TimeoutError):
            return False  # 超时等于拒绝

    async def _ask(self, name, desc, args) -> bool:
        req_id = uuid.uuid4().hex
        inner_fut = self.loop.create_future()
        self._pending[req_id] = inner_fut
        await self.ws.send_json({
            "type": "confirm_request",
            "request_id": req_id,
            "tool": name, "description": desc, "args": args,
        })
        return await inner_fut

    def resolve(self, request_id: str, allowed: bool):
        """前端响应到达后调用"""
        fut = self._pending.pop(request_id, None)
        if fut and not fut.done():
            fut.set_result(allowed)
```

### 前端

```js
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.type === "confirm_request") {
    showModal({
      title: `Allow "${msg.tool}"?`,
      body: msg.description + "\n\n" + JSON.stringify(msg.args, null, 2),
      onAllow: () => ws.send(JSON.stringify({
        type: "confirm_response", request_id: msg.request_id, allowed: true,
      })),
      onReject: () => ws.send(JSON.stringify({
        type: "confirm_response", request_id: msg.request_id, allowed: false,
      })),
    });
  }
};
```

### 为什么用超时而非永等

Agent 调 `confirm_tool` 时它整个 `chat()` 循环就卡住了。如果用户去上厕所没点，Agent 就会永远等着——连心跳日志都不出。**必须有超时兜底**，超时=拒绝最保守。

## 模式 C · 手机原生 App

手机 App 跟后端的 WS 连接可能随时断（息屏、切应用）。策略：

1. **优先显示系统 Push 通知**带确认按钮（iOS actionable notification）
2. **后端设置长超时**（例如 5 分钟）等用户切回 App
3. **超时后保存工具调用为 pending 状态**——App 再次连回时可补看

```python
async def _ask_mobile(user_id, name, desc, args):
    req_id = uuid.uuid4().hex
    # 先发 push 通知
    await push_service.send(user_id, {
        "title": f"Agent wants to run {name}",
        "body": desc,
        "actions": ["Allow", "Reject"],
        "data": {"request_id": req_id},
    })
    # 同时写到 DB（避免在内存里丢失）
    await db.save_pending_confirm(req_id, user_id, name, args)
    # 等用户响应（App 回连时 POST）
    return await wait_for_db_update(req_id, timeout=300)
```

## 模式 D · 无人值守 / 批处理

完全不需要用户交互时，用规则代替人工：

```python
from agentao.permissions import PermissionEngine, PermissionMode

READ_ONLY = {"read_file", "glob", "grep", "read_folder"}
ALWAYS_OK = READ_ONLY | {"save_memory", "activate_skill"}
NEVER     = {"run_shell_command"}  # 完全禁用

def confirm_tool(name, desc, args):
    if name in NEVER: return False
    if name in ALWAYS_OK: return True
    # 中等风险工具需要额外条件判断
    if name == "write_file":
        path = args.get("path", "")
        return path.startswith("/tmp/sandbox/")
    return False  # 默认拒绝
```

对于批处理，**完全不传 `confirm_tool`** 也行——`NullTransport` 会自动批准所有工具。但这只在**绝对信任**的场景（比如你锁死了可用工具集 + 文件沙箱）才安全。

## 和权限引擎的协同

Agentao 有两层防御：

```
              ┌──────────────────────┐
工具调用 ───►│  1. PermissionEngine │──── allow/deny/ask
              │   （规则引擎，快）    │
              └──────────┬───────────┘
                         │ 如果 = ask
                         ▼
              ┌──────────────────────┐
              │  2. confirm_tool()    │ ──── 你的 UI
              │   （人工决策，慢）   │
              └──────────────────────┘
```

`PermissionEngine` 先基于 JSON 规则快速判决，只有规则说"ask"的情况才调 `confirm_tool`。这样：

- 显然安全的操作（读取白名单下的文件）规则直接放行，不打扰用户
- 显然危险的操作（执行被禁止的命令）规则直接拦截
- 边缘场景（写项目外的文件？fetch 未知域名？）才走 `confirm_tool`

权限引擎详见 [5.4 节](/zh/part-5/)（撰写中）。

## 预览事件与 UI 预热

`TOOL_CONFIRMATION` 事件在 `confirm_tool` 调用**之前**就会通过 `emit` 发出——UI 可以用它做**弹窗预热**：

```python
def on_event(ev):
    if ev.type == EventType.TOOL_CONFIRMATION:
        # 提前把模态框 DOM 加载、按钮聚焦
        ui.prepare_modal(ev.data["tool"], ev.data["args"])

def confirm_tool(name, desc, args):
    # 此时模态框已经渲染好，直接显示 + 等回应
    return ui.show_prepared_modal()
```

在响应慢的 Web 场景里这可以省掉 ~100ms 的首次渲染延迟。

## 确认策略组合

生产场景往往是**多级组合**：

```python
class SmartConfirm:
    def __init__(self, user_ui, tenant_rules: dict):
        self.ui = user_ui
        self.rules = tenant_rules  # tenant_id -> {allow: [...], deny: [...]}
        self._session_allow_all = False
        self._denied_until_restart = set()

    def __call__(self, name, desc, args):
        # 1. 本次会话"全部允许"
        if self._session_allow_all:
            return True

        # 2. 租户黑名单——永远拒
        if name in self.rules.get("deny", []):
            return False

        # 3. 租户白名单——永远允
        if name in self.rules.get("allow", []):
            return True

        # 4. 弹给用户
        resp = self.ui.ask(name, desc, args)  # "allow_once"/"allow_all"/"reject"
        if resp == "allow_all":
            self._session_allow_all = True
            return True
        return resp == "allow_once"
```

## TL;DR

- `confirm_tool` 是**阻塞**的——Agent 循环等你返回 bool。永远不要返回 `None`，也不要在里面直接 `await`。
- 异步 UI 桥接：从 worker 线程用 `asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=…)`，让事件循环处理弹窗。
- **永远**给等待加一个有限超时——UI 出 bug 永不响应时，否则整个 Agent 会挂死。
- 配合 [5.4 PermissionEngine](/zh/part-5/4-permissions)，让 90% 的安全调用直接放行，`confirm_tool` 只在真正需要 ASK 时触发。

→ 下一节：[4.6 最大迭代数兜底策略](./6-max-iterations)
