# 3.4 反向：调用外部 ACP Agent

[3.2](./2-agentao-as-server) 和 [3.3](./3-host-client-architecture) 把 Agentao 定位成**服务端**、你是客户端。本节把角色反过来：**Agentao 当客户端**，以子进程形式驱动一个外部 ACP agent。适用场景：你有一个讲 ACP 的专业 agent（搜索机器人、文档爬虫、代码审阅者），想让主 Agentao 把某些轮次委派给它。

## 3.4.1 什么时候用

| 场景 | 为什么 ACP 反向调合适 |
|-----|---------------------|
| 为小众能力配"子 agent" | 隔离：子 agent 崩了不拖垮主 agent |
| 多语言 agent 组合 | 主体 Python，专家 Rust / Go / TS，都讲 ACP |
| 复用已有 ACP agent | Zed 的 agent、你自家内部 agent——本来就是 ACP 形态 |
| 重计算侧工作 | 让专家跑在不同资源配置 / 沙箱下 |

什么时候**别**用：

- 想要一个本地工具——写 `Tool` 子类更简单
- 子 agent 也是 Python 并与你同进程——就在进程内再起一个 `Agentao`

## 3.4.2 `ACPManager` — 公开 API

`ACPManager` 是 Agentao 的 ACP 客户端侧。从 `agentao.acp_client` 导入：

```python
from agentao.acp_client import ACPManager, load_acp_client_config, PromptResult
from agentao.acp_client import AcpClientError, AcpErrorCode, ServerState
```

两种构造方式：

```python
# 1. 从 .agentao/acp.json（自动探测项目根）
mgr = ACPManager.from_project()

# 2. 显式传 config
config = load_acp_client_config(project_root=Path("/app"))
mgr = ACPManager(config)
```

如果你把 `ACPManager` 跑在 CI worker、cron、队列消费者这类**无人值守宿主**里，那么这里说的其实就是 **Headless Runtime**。先把心智模型钉死，后面 API 会更好读：

- **不是第三种模式**：底层还是 ACP 子进程 + stdio + JSON-RPC
- **不是另一套对象模型**：你用的还是 `ACPManager`
- **只是另一种运行轮廓**：宿主不提供人工确认；server 发起交互时，靠 non-interactive policy 决策

带着这个理解去看 API：

1. 用 `prompt_once()` / `send_prompt()` 发 turn
2. 用 `get_status()` / `readiness(name)` 做可用性门禁
3. 把 `last_error` 当诊断历史，不当放行信号
4. 把 `SERVER_BUSY` 当并发背压，不当隐式队列

一句话记忆：**Headless Runtime = 无人值守地使用 `ACPManager`**。

公开接口：

| 方法 | 作用 |
|-----|-----|
| `start_all()` / `start_server(name)` | 启子进程 + 握手 |
| `stop_all()` / `stop_server(name)` | 优雅关停 |
| `prompt_once(name, prompt, ...)` | 一次性打一发——**推荐入口** |
| `send_prompt(name, prompt, ...)` | 长会话变体（子进程常驻） |
| `cancel_turn(name)` | 取消进行中的轮次 |
| `get_status()` | 类型化 `list[ServerStatus]` 快照（见 3.4.9） |
| `readiness(name)` / `is_ready(name)` | 粗粒度回答「现在能不能提交」（见 [3.4.11](#就绪分级)） |
| `get_server_logs(name, n=50)` | 子进程最近 *n* 行 stderr |

`send_prompt_nonblocking` / `finish_prompt_nonblocking` /
`cancel_prompt_nonblocking` 也挂在 `ACPManager` 上，但属于
**internal / unstable**：仅供 Agentao 自己的交互式 CLI inline-confirm
管线使用，签名可能随时变更，headless embedder **不应**依赖。Headless
场景请使用 `send_prompt` 或 `prompt_once`。各入口的支持级别见
[3.4.11](#入口与支持级别)。

完整 API 见[附录 A · ACP 客户端](/zh/appendix/a-api-reference#a-8-acp-客户端)。

### 受支持的导入

唯一承诺 semver 稳定的导入根是 `agentao.acp_client`。嵌入合约就是下面这一组：

```python
from agentao.acp_client import (
    ACPManager,
    AcpClientConfig, AcpServerConfig, load_acp_client_config,
    AcpConfigError, AcpClientError, AcpRpcError, AcpErrorCode,
    AcpInteractionRequiredError,
    InteractionPolicy, PromptResult,
    ServerState, ServerStatus, AcpProcessInfo,
    classify_process_death,
)
```

包根目录还在重新导出 `ACPClient`、`ACPProcessHandle`、`AcpConnectionInfo`、`Inbox`、`InteractionRegistry` 和 router 辅助函数，但只是为了照顾老代码。它们是实现细节，版本之间可能变化。确实要用，就从子模块导入（`agentao.acp_client.client`、`.process`、`.inbox`、`.interaction`、`.router`）。完整迁移表见[附录 E.10](/zh/appendix/e-migration#e-10-acp-客户端——内部名字迁到子模块)。

## 3.4.3 `prompt_once()` — 95% 的情形

```python
def prompt_once(
    self,
    name: str,
    prompt: str,
    *,
    cwd: Optional[str] = None,
    mcp_servers: Optional[List[dict]] = None,
    timeout: Optional[float] = None,
    interactive: bool = False,
    stop_process: bool = True,
    interaction_policy: InteractionPolicy | str | None = None,
) -> PromptResult:
```

语义：

- 按 server 取独占锁，**fail-fast** 模式。若该 server 已有一轮在跑，立即抛 `AcpClientError(code=SERVER_BUSY)`——不等待，也不会偷偷排队
- 若没有长期客户端，本次**临时起一个**客户端；退出时拆掉。临时客户端永远不会登记成该 server 的长期客户端
- `stop_process=True`（默认）只在**本次调用启动了**子进程时才在退出时停掉它；原本就在跑的子进程不受影响
- 若已经有长期客户端（你之前调过 `start_server(name)`），就复用；子进程**跨越**本次调用继续存活
- 默认 `interactive=False`，server 发起的权限 / 输入请求按非交互策略处理（见 [3.4.11](#非交互-turn)）
- 返回 `PromptResult`，含 `stop_reason`、`session_id`、`cwd`、原始 payload

这也是为什么它是 headless 默认入口：语义最收敛。你通常只需要处理三类结果：

- 正常结束：拿到 `PromptResult`
- 并发冲突：收到 `SERVER_BUSY`
- 运行失败：收到其它 `AcpClientError`

### 示例：主 agent 把工作委派给 "searcher"

```python
from agentao.acp_client import ACPManager, AcpClientError, AcpErrorCode

mgr = ACPManager.from_project()

def search_via_subagent(query: str) -> str:
    try:
        result = mgr.prompt_once(
            "searcher",
            prompt=query,
            cwd="/tmp/searcher-workspace",
            timeout=30.0,
        )
        if result.stop_reason != "end_turn":
            return f"[searcher 结束状态：{result.stop_reason}]"
        # 如果你抓取了流式通知，从 result.raw 或通知里提取 assistant 文本
        return "<内容见通知流>"
    except AcpClientError as e:
        if e.code == AcpErrorCode.SERVER_BUSY:
            return "[searcher 忙，稍后重试]"
        raise
```

把它包成 Agentao `Tool`，主 agent 就能像调其它工具一样调它：

```python
from agentao.host import Tool

class SearcherTool(Tool):
    name = "delegate_search"
    description = "把一次 web/docs 搜索委派给专职 ACP agent"
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    requires_confirmation = False

    def __init__(self, mgr: ACPManager):
        self.mgr = mgr

    def execute(self, query: str) -> str:
        return search_via_subagent(query)
```

在主 agent 里注册：

```python
from agentao import Agentao

mgr = ACPManager.from_project()
mgr.start_server("searcher")        # 预热；可选，但能降首次延迟

main = Agentao(tools=[SearcherTool(mgr)])
main.chat("调研 agent-client protocol 并总结。")
```

## 3.4.4 捕获子 agent 的流

`prompt_once()` 会阻塞直到子 agent 结束——适合"问一次拿答案"的流程。但如果你想把子 agent 的输出**流式**转给主 UI，构造 `ACPManager` 时传 `notification_callback`：

```python
def on_notification(server_name: str, method: str, params) -> None:
    if method == "session/update":
        update = params.get("update", {})
        if update.get("sessionUpdate") == "agent_message_chunk":
            text = update["content"]["text"]
            print(f"[{server_name}] {text}", end="", flush=True)

mgr = ACPManager(config, notification_callback=on_notification)
```

回调跑在**读线程**上——要么做得快，要么塞到队列里再交棒。

## 3.4.5 配置格式 — `.agentao/acp.json`

和 Agentao 自己识别的配置 schema 一致：

```json
{
  "servers": {
    "searcher": {
      "command": "my-searcher",
      "args": ["--acp", "--stdio"],
      "env": { "SEARCH_API_KEY": "$SEARCH_API_KEY" },
      "cwd": ".",
      "autoStart": true,
      "startupTimeoutMs": 10000,
      "requestTimeoutMs": 60000,
      "description": "Web + docs 专家",
      "nonInteractivePolicy": { "mode": "reject_all" }
    }
  }
}
```

必填：`command`、`args`、`env`、`cwd`。可选：`autoStart`、`startupTimeoutMs`、`requestTimeoutMs`、`capabilities`、`description`、`nonInteractivePolicy`。

- `cwd` 相对路径相对于项目根（含 `.agentao/` 的目录）解析
- `env` 值里的 `$VAR` / `${VAR}` 会展开成宿主进程的环境变量
- `nonInteractivePolicy` 是结构化对象 `{"mode": "reject_all" | "accept_all"}`。缺省等价于 `{"mode": "reject_all"}`。旧版裸字符串形式（`"reject_all"` / `"accept_all"`）现在在配置加载阶段**直接报错**，迁移见 [附录 E](/zh/appendix/e-migration)
- 生产用 `reject_all`。在 `send_prompt` / `prompt_once` 上传 `interaction_policy=` 可对单次 turn 覆盖 server 默认

完整字段见[附录 B · 配置键](/zh/appendix/b-config-keys)。

### 单次调用策略覆盖

```python
from agentao.acp_client import ACPManager, InteractionPolicy

mgr = ACPManager.from_project()

# 使用 server 默认值（上文为 reject_all）
mgr.send_prompt("searcher", "summarize the docs", interactive=False)

# 针对受信任的批处理单次放行
mgr.send_prompt(
    "searcher", "rebuild the index", interactive=False,
    interaction_policy="accept_all",
)

# 等价的类型化写法
mgr.prompt_once(
    "searcher", "rebuild the index",
    interaction_policy=InteractionPolicy(mode="accept_all"),
)
```

优先级：**per-call override > server default**。`None`（默认值）回退到 server 默认。`send_prompt_nonblocking` 是 internal / unstable，**不**接这个 kwarg。

### 运行时添加 server

运行中的 manager 可以直接添加 server，无需重建。`add_server` 登记该 server 的全部状态，并保持其停止；已在运行的 server 不受影响：

```python
from pathlib import Path

from agentao.acp_client.config import add_server_entry
from agentao.acp_client.registry import entry_to_server_config, fetch_registry, find_agent

entry = entry_to_server_config(find_agent(fetch_registry(), "gemini"))  # 仅 npx / uvx
config = add_server_entry("gemini", entry.config, project_root=Path("/app"))  # 可选：写入文件
mgr.add_server("gemini", config)
```

查询、转换、写 `acp.json`、登记是彼此独立的几步，宿主可以在任一步停下。`entry_to_server_config` 写入 `autoStart: false` 和 `startupTimeoutMs: 120000`（首次启动要下载包）；`add_server_entry` 拒绝重名或已损坏的现有文件，并原子写入。范围与规则见 [CLI 8 · 从 ACP Registry 添加 Agent](/zh/cli/8-mcp-acp-plugins#从-acp-registry-添加-agent-0-5-8)。

## 3.4.6 长驻 vs 临时

`prompt_once()` 是 fail-fast，两种模式都能跑：

| 模式 | 触发 | 进程 | 适合 |
|------|------|------|------|
| **临时** | 没先 `start_server()` 就 `prompt_once()` | 本次启、退出时拆 | 一次性工作流、批处理 |
| **长驻** | 先调了 `start_server(name)` | 跨调用常驻 | 聊天式使用、首包延迟敏感 |

延迟取舍：
- 临时：每次启动 ~200–500 ms
- 长驻：每次 ~10 ms（进程已热）

内存取舍：
- 临时：无残留
- 长驻：每 server ~50–200 MB

规矩：**调用频率每分钟几次以上** → 长驻；其它一律临时。

从 headless 运维视角看，可以进一步简化成：

- **吞吐优先**：先 `start_server()`，走长驻
- **隔离/清洁优先**：直接 `prompt_once()`，让它临时起停
- **拿不准就默认 `prompt_once()`**：状态面更小，排障更直接

### 用 `send_prompt()` 复用会话

想让多个任务共用一个会话、同时每次调用仍各带自己的 `cwd` 和 `mcp_servers`，就用 `send_prompt()`：

```python
mgr = ACPManager.from_project()
mgr.start_all()
try:
    raw = mgr.send_prompt(
        "codex-reviewer",
        "Review the diff",
        cwd="/path/to/task-checkout",
        mcp_servers=[],
        timeout=120,
        interactive=False,      # send_prompt 默认是 True
    )
    print(raw["stopReason"])    # send_prompt 返回原始结果 dict
finally:
    mgr.stop_all()
```

- 只有 `cwd` 和 `mcp_servers` **两者**都和创建会话时一致，才会复用缓存的会话。`None` 表示 server 配置里的 `cwd` 和空的 MCP 列表，比较用的是解析后的值。
- 不一致时，manager 在同一个 client、同一条 transport 上重新发 `session/new`，子进程不会重启。
- `send_prompt()` 默认 `interactive=True`，那是 CLI 的模式。headless 宿主必须显式传 `interactive=False`。

## 3.4.7 生命周期与恢复

三种常见失败场景的行为已被固化，embedder 不用自己卷恢复逻辑：

**取消 / 超时 → 下一次 turn 安全**。turn 槽、per-server 锁、pending prompt 槽都在 `finally` 里按固定顺序释放。取消或超时之后第一个 `send_prompt` / `prompt_once` 看到的是一个 ready、没有残留状态的 server。

**可恢复进程死亡 → 自动重建**。如果子进程在两次调用之间死了（干净退出、idle 非零退出且在上限内、stdio EOF、active turn 期间死亡），下次 `ensure_connected` / `send_prompt` 调用会关掉 dead client、把 `mgr.restart_count(name)` +1、然后透明地重建。`maxRecoverableRestarts`（默认 3）限制 idle 非零退出时连续自动重建的上限。

**致命进程死亡 → sticky，必须运维介入**。OOM / SIGKILL / `exit 137`、信号结束、连续 handshake 失败、或 idle 非零退出超过上限，都会把 server 标记为 sticky-fatal。`auth_required` 不算握手失败，永不计入（见 3.4.8）。`mgr.is_fatal(name)` 返回 `True`，所有调用都抛 `AcpClientError(code=TRANSPORT_DISCONNECT, details={"recovery": "fatal"})`，直到调 `mgr.restart_server(name)` 或 `mgr.start_server(name)` 清除标记。

```python
from agentao.acp_client import ACPManager, AcpClientError, AcpErrorCode

mgr = ACPManager.from_project()

try:
    mgr.prompt_once("searcher", "...")
except AcpClientError as e:
    if e.code is AcpErrorCode.TRANSPORT_DISCONNECT \
       and e.details.get("recovery") == "fatal":
        page_operator()
        # 之后: mgr.restart_server("searcher")
```

classifier 是纯函数——`classify_process_death`——从 `agentao.acp_client` 导出，可以独立测试。完整决策矩阵见下文。

### 取消或超时后的清理顺序

`send_prompt` / `prompt_once` 的每条失败路径都按下面的顺序释放状态，且只释放一次：

1. **pending prompt 槽**。超时时，client 先丢掉挂起的 `session/prompt` 槽，**再**发 `session/cancel`。丢槽幂等、从不抛异常，所以就算管道断了，client 也不会卡住下一次 turn。
2. **turn 槽**。manager 的单活跃 turn 槽在 `finally` 里清掉；成功、超时、取消、传输错误都一样。
3. **`last_error`**。错误在释放锁之前记录，所以同一时刻的 `get_status()` 已经能看到它。
4. **per-server 锁**。外层 `finally` 释放锁，不管上面抛了什么。

结果：`cancel_turn` 或超时之后，server 不会残留 busy 或加锁状态。源码见 [`agentao/acp_client/manager/turns.py`](https://github.com/jin-bo/agentao/blob/main/agentao/acp_client/manager/turns.py)。

### 进程死亡分类

子进程死掉时（两次调用之间，或 turn 进行中），manager 决定是重建还是放弃：

| 触发 | 分类 | 结果 |
|------|------|------|
| 空闲进程以 0 退出 | 可恢复 | 下次调用时惰性重建 |
| 空闲进程非零退出 | 上限内可恢复，超过则致命 | `restart_count(name)` +1；上限是 `maxRecoverableRestarts`（默认 3） |
| active turn 期间进程死亡（非信号退出） | 可恢复，不受上限约束 | 本次 turn 失败并记录 `last_error`；下次调用重建 |
| 进程还活着，但 stdio 传输断了 | 可恢复 | 重建 client，不重启进程 |
| 被信号杀死（退出码为负，或 137 / 139 / 143——OOM、SIGKILL、SIGSEGV、SIGTERM） | 致命 | 不自动重启；`state` 变为 `failed`；必须显式重启 |
| 连续第二次握手失败 | 致命 | 环境或配置问题，不是偶发故障 |
| `initialize` / `session/new` 返回 `auth_required`（`-32000`） | 不计入 | agent 需要凭据；重试永远不会把 server 变成致命（见[认证](#认证-auth-required)） |
| 宿主调用 `cancel_turn` | 不算死亡 | 由上面的清理顺序处理 |

manager 的做法：

- 复用缓存 client 之前，manager 先检查进程。可恢复的死亡：关掉死 client、重启计数 +1、重新连接。致命的死亡：把 server 标成 sticky-fatal，并抛 `AcpClientError(code=TRANSPORT_DISCONNECT, details={"recovery": "fatal"})`。
- 第一次成功的 turn 之后，重启计数和握手失败连击都归零。
- 只有 `restart_server(name)` 或 `start_server(name)` 能清除致命标记，成功的 turn 不会。致命的 server 必须先由运维确认，才会再重建。
- 用 `is_fatal(name)` 以及 `ServerStatus` 上的 `(state, last_error)` 区分两种情况。

源码：[`agentao/acp_client/models.py`](https://github.com/jin-bo/agentao/blob/main/agentao/acp_client/models.py) 里的 `classify_process_death`，以及 [`manager/recovery.py`](https://github.com/jin-bo/agentao/blob/main/agentao/acp_client/manager/recovery.py)。

### 调整 `maxRecoverableRestarts`

```json
{
  "servers": {
    "searcher": {
      "command": "my-searcher",
      "args": [],
      "env": {},
      "cwd": ".",
      "maxRecoverableRestarts": 3
    }
  }
}
```

默认 3。必须是非负整数；负数或其他类型（包括 `true`）会在**加载配置时**抛 `AcpConfigError`，不会拖到运行 turn 时。设成 `0` 表示空闲进程第一次非零退出就判为致命。

## 3.4.8 取消与错误

```python
# 取消进行中的轮次
mgr.cancel_turn("searcher")

# 按 code 区分错误
try:
    mgr.prompt_once("searcher", "...")
except AcpClientError as e:
    match e.code:
        case AcpErrorCode.SERVER_BUSY:       retry_after_delay()
        case AcpErrorCode.SERVER_NOT_FOUND:  log_config_issue()
        case AcpErrorCode.HANDSHAKE_FAIL:    reinstall_sub_agent_binary()
        case AcpErrorCode.REQUEST_TIMEOUT:   raise_alert()
        case _:
            # 握手阶段抛出的 `AcpRpcError` 的 `code` 是 JSON-RPC int
            # （不是 `AcpErrorCode`），不会进 `HANDSHAKE_FAIL` 分支——
            # 如果需要覆盖这种情况，按 `details["phase"]` 判断：
            if e.details.get("phase") == "handshake":
                reinstall_sub_agent_binary()
            else:
                raise
```

完整错误分类（包含 `AcpRpcError` 合约、`details["underlying_code"]` / `details["phase"]` 信号）见[附录 D · 错误码](/zh/appendix/d-error-codes)。

`cancel_turn(name)` 不等 per-server 锁，所以可以在 turn 进行中从别的线程调用。它发出 `session/cancel` 并把 turn 标记为已取消。被取消的 turn 正常返回，即使这一轮早先已经锁存了一个非交互的交互错误。

### `AcpInteractionRequiredError`

非交互 turn 里，server 请求了权限或输入、而策略没有批准时，就抛这个错误（见[非交互 turn](#非交互-turn)）。稳定的公开字段：

| 字段 | 内容 |
|------|------|
| `server` | server 名 |
| `prompt` | server 随请求展示的文字 |
| `options` | server 给出的选项（dict 列表） |
| `code` | 恒为 `AcpErrorCode.INTERACTION_REQUIRED` |

server 的原始方法名（`session/request_permission` 或 `_agentao.cn/ask_user`）在 `exc.details["method"]` 里。只用于日志，不要据此分支——方法名可能会变。

### 认证（`auth_required`）

没有凭据的 agent 会对 `session/new` 回 `auth_required`——即 `rpc_code == -32000` 的 `AcpRpcError`。manager 会打上 `details["auth_required"] = True` 和 `details["auth_methods"]`（agent 声明的方法），并且**不**计入握手失败连击，所以重试永远不会把 server 变成 fatal。首次 `initialize` 之后，随时可用 `mgr.auth_methods(name)` 取到同一份列表；`mgr.needs_login(name)` 在成功打开会话或建会话因其他原因失败之前一直为真（重启也不清除），可用来显示「需要登录」而不是 `failed`。

```python
from agentao.acp_client import ACPManager, AcpRpcError
from agentao.acp_client.auth import (
    build_terminal_login_command, is_auth_required, terminal_auth_methods,
)

mgr = ACPManager.from_project(terminal_auth=True)  # 仅当你能运行终端登录
try:
    mgr.connect_server("gemini")
except AcpRpcError as e:
    if not is_auth_required(e):
        raise
    method = terminal_auth_methods(e.details["auth_methods"])[0]
    command = build_terminal_login_command(mgr.get_handle("gemini").config, method)
    with mgr.reserve_for_login("gemini"):     # 有 turn 在跑时抛 SERVER_BUSY
        run_interactively(command.argv, command.env, command.cwd)  # 你的终端；退出码 0 = 成功
        mgr.restart_server("gemini")          # 规范要求：重连并重新 initialize
        mgr.connect_server("gemini")
```

- `terminal_auth=True` 会在 `initialize` 中声明 Terminal Auth（`clientCapabilities.auth.terminal` 和旧写法 `_meta["terminal-auth"]`）。agent 只对声明了它的客户端提供 `terminal` 方法，所以 headless 宿主保持 `False`（默认）。
- `build_terminal_login_command` 遵循 ACP 规范：沿用 server 自己的命令、基础参数和 `cwd`，追加方法的 `args`，并把方法的 `env` 覆盖在 server 清洗过的启动环境之上。非 `terminal` 方法一律拒绝。
- 运行登录由宿主负责。它需要用户的终端：继承 stdin/stdout，并留在宿主的会话里（不能用 `start_new_session`）。Agentao CLI 像 shell 运行前台作业那样运行它：单独的进程组，用 `tcsetpgrp` 交出终端、结束后收回，这样 Ctrl+C 只送达登录进程，取消时也能结束它的整个进程组。
- `reserve_for_login` 生效期间，其他线程的 turn 和连接都会抛 `SERVER_BUSY`；其他 server 不受影响。
- 只支持 `terminal` 方法；对 `agent` 方法（省略 `type` 时的默认类型），请在 Agentao 之外完成认证，再重启 server。

## 3.4.9 健康检查与排错

`ACPManager.get_status()` 返回类型化的 `list[ServerStatus]`：

```python
from agentao.acp_client import ServerStatus

for s in mgr.get_status():             # 每个 s 都是 ServerStatus
    print(s.server, s.state, s.pid, s.has_active_turn)
    if s.state == ServerState.FAILED.value:
        print(f"{s.server} 失败：{s.last_error}")
        print("\n".join(mgr.get_server_logs(s.server, n=50)))
```

核心字段：`server`、`state`、`pid`、`has_active_turn`。同一个 dataclass 上还有诊断字段：`last_error`、`last_error_at`、`active_session_id`、`inbox_pending`、`interaction_pending`、`config_warnings`。每个字段的确切含义见 [3.4.11](#状态字段)。`mgr.get_handle(name).info`、`mgr.inbox` 和 `mgr.interactions` 仍然保留，作为原始 handle 视图。如果你还在读旧的 `list[dict]` 形态，见[附录 E.11](/zh/appendix/e-migration#e-11-acp-客户端——get-status-返回-serverstatus)。

`mgr.get_server_logs(name, n=50)` 返回 manager 从子进程捕获的最近 *n* 行 stderr。daemon 里 RPC 失败时，先看这里。名字不存在时抛 `SERVER_NOT_FOUND`。

子 agent（如果也是 Agentao 类型）的日志在 `<server cwd>/agentao.log`，其它 agent 可能输出到其它位置。`.agentao/acp.json` 里的 `cwd` 一定要指向可写目录，不然日志会丢。

## 3.4.10 生命周期自查

主 Agentao 进程启动时：

```python
mgr = ACPManager.from_project()
mgr.start_all()            # 或按 name 单独启
```

关停时：

```python
mgr.stop_all()             # 优雅杀子进程
```

用 try/finally 或上下文管理器包起来——热重载时遗留的 ACP 子进程是常见资源泄漏源。

## 3.4.11 Headless 合约

本节是无人值守地运行 `ACPManager`（daemon、CI worker、队列消费者）时的参考，固定宿主可以依赖的行为。本页和代码不一致时以代码为准，并请提 bug。

可运行样板是 [`examples/headless_worker.py`](https://github.com/jin-bo/agentao/blob/main/examples/headless_worker.py)：它在进程内起一个 mock ACP server，依次跑成功、出错、取消三条路径，每条之后打印一次类型化状态快照。在仓库根目录执行 `uv run python examples/headless_worker.py`，退出码 `0` 表示所有路径都通过。

### 入口与支持级别

| 入口 | 支持级别 | 用途 |
|------|---------|------|
| `prompt_once(name, prompt, …)` | **public** | 一次 turn，清理确定；没有长期 client 时用临时 client |
| `send_prompt(name, prompt, …)` | **public** | 在长期会话上跑 turn |
| `send_prompt_nonblocking(…)` + `finish_prompt_nonblocking` / `cancel_prompt_nonblocking` | **internal / unstable** | 交互式 CLI 的 inline-confirm 管线；签名可能随时变更；不接受 `interaction_policy=` |

headless 宿主提交 turn 只用 `prompt_once` 或 `send_prompt`。

### 每个 server 只有一个活跃 turn

每个命名 server 只有**一个活跃 turn 槽**，而且**没有队列**。

- 入口拿到 per-server 锁并装上 turn 槽，turn 就开始；入口的 `finally` 清掉槽，turn 就结束——不论结果如何。
- 两个公开入口都以 **fail-fast** 方式取锁。同一 server 上并发的第二个 `send_prompt` 或 `prompt_once` 立即抛 `AcpClientError(code=SERVER_BUSY)`，绝不会把 worker 线程堵在一个慢 turn 后面。
- `timeout=` 覆盖 RPC 等待（调用需要先连接时，也覆盖握手 RPC）。锁从不等待，所以不存在「等锁的时间」。非交互 turn 的 `timeout=None` 取 server 的 `requestTimeoutMs`。
- 想等到有空位，就轮询 `readiness(name)` 或 `get_status()` 再重试。manager 从不替你排队。

| 情形 | 结果 |
|------|------|
| 同一 server 两个 `send_prompt` | 第二个抛 `SERVER_BUSY` |
| 同一 server 两个 `prompt_once` | 第二个抛 `SERVER_BUSY` |
| `send_prompt` 的 turn 在跑时调 `prompt_once` | `prompt_once` 抛 `SERVER_BUSY` |
| 非交互 turn 期间调 `cancel_turn` | 丢弃锁存的交互错误；prompt RPC 跑完并正常返回 |
| 已有长期 client 时调 `prompt_once` | 复用该 client；`stop_process=True` 不会停掉子进程 |
| 别的线程持有 `reserve_for_login(name)` 期间的任何 turn | `SERVER_BUSY` |

`SERVER_BUSY` 是调用方一侧的背压信号，从不写入 `last_error`（见[附录 D.5](/zh/appendix/d-error-codes#d-5-状态与错误合约-headless)）。

### 非交互 turn

daemon 没有用户可问。`interactive=False`（`prompt_once` 的默认值，`send_prompt` 不是）时，manager 按策略自己回答 `session/request_permission` 和 `_agentao.cn/ask_user`，handle 不会因此进入 `WAITING_FOR_USER`。

| 策略 `mode` | `session/request_permission` | `_agentao.cn/ask_user` |
|-------------|------------------------------|------------------------|
| `reject_all`（默认） | 拒绝；turn 抛 `AcpInteractionRequiredError` | 回错误；turn 抛 `AcpInteractionRequiredError` |
| `accept_all` | server 给了「允许」类选项就批准；否则拒绝，turn 抛错 | 回错误；turn 抛错——`accept_all` 编不出用户的回答 |

策略取自单次调用的 `interaction_policy=`，没传则取 server 的 `nonInteractivePolicy`（见 [3.4.5](#单次调用策略覆盖)）。manager 在 turn 开始时解析一次。

**锁存**。被拒绝的请求不会中止 turn：`session/prompt` RPC 一直跑到 server 结束它。manager 记下**第一个**被拒绝的请求，等 RPC 结束后以 `AcpInteractionRequiredError` 抛出。如果宿主在这一轮里调了 `cancel_turn`，取消优先，turn 正常返回。

### 状态字段

`get_status()` 为每个已配置的 server 返回一个冻结的 `ServerStatus`。`prompt_once` 的临时 client 不会单独占一行；它运行期间，该 server 那一行会显示 `has_active_turn` 和临时会话的 id。

| 字段 | 含义 |
|------|------|
| `server` | `.agentao/acp.json` 里的名字 |
| `state` | `ServerState` 的字符串值；主要的就绪信号 |
| `pid` | 子进程运行时的 OS 进程号，否则 `None` |
| `has_active_turn` | turn 在 manager 的 turn 槽里的整个生命周期内都为 `True`，包括交互阶段。它来自 turn 槽而不是 handle 状态，所以 handle 处于 `WAITING_FOR_USER` 时不会翻成 `False` |
| `active_session_id` | 长期 client 的当前 `sessionId`，或正在运行的 `prompt_once` 临时 client 的。会话建立前为 `None`；有没有 turn 在跑都会填 |
| `last_error` | 公开入口最近一次抛出的错误。manager 这边没有记录时，回退到 handle 自己的错误（例如 `start_server` 失败） |
| `last_error_at` | 带 UTC 时区的 `datetime`，取的是 manager **存入**错误的时刻，不是抛出的时刻。`last_error` 来自 handle 回退时为 `None` |
| `inbox_pending` | 共享 inbox 里来自该 server 的消息数 |
| `interaction_pending` | 该 server 尚未处理的权限 / 输入请求数 |
| `config_warnings` | 预留给非致命的 per-server 配置弃用提示；当前版本恒为空 |

先看 `state`（或 `readiness()`）决定放不放行，再读 `last_error` 做诊断。`last_error` 在成功的 turn 之后依然保留。完整规则见[附录 D.5](/zh/appendix/d-error-codes#d-5-状态与错误合约-headless)。

### 就绪分级

`readiness(name)` 返回四个字符串之一；`is_ready(name)` 等价于 `readiness(name) == "ready"`。优先用它们，不要对 `state` 做字符串匹配。

| 分级 | 条件 |
|------|------|
| `"ready"` | `state` 为 `ready`、没有活跃 turn 槽、进程存活。可以提交 |
| `"busy"` | 有活跃 turn 槽，或 `state` 为 `busy` / `waiting_for_user`，且进程存活。此时提交会抛 `SERVER_BUSY` |
| `"failed"` | server 已 sticky-fatal；或 `state` 为 `failed` 且进程从未启动或已退出；或进程刚死、且被判为致命。需要 `restart_server` / `start_server` |
| `"not_ready"` | `state` 为 `configured`、`starting`、`initializing`、`stopping` 或 `stopped`。另外两种情况：`state` 为 `failed` 但进程还活着——单次调用的错误（如 `cwd` 覆盖被拒），下次调用即可恢复；进程刚死、且被判为可恢复——下次提交会重建 |

`readiness()` 和 `get_status()` 并非纯读取：发现进程已死时，它们会对死亡做分类并清掉死 client，让返回值反映真实状态。轮询从不消耗重启配额。源码见 [`agentao/acp_client/manager/status.py`](https://github.com/jin-bo/agentao/blob/main/agentao/acp_client/manager/status.py)。

不在本合约范围内：多维策略（按工具或按超时细分）、致命死亡后的自动重启、socket 或 TCP 形式的 daemon 传输。运行时只走 stdio。

---

下一节：[3.5 Zed / IDE 集成 →](./5-zed-ide-integration)
