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
| `get_status()` | 类型化 `list[ServerStatus]` 快照（见 3.4.8） |

`send_prompt_nonblocking` / `finish_prompt_nonblocking` /
`cancel_prompt_nonblocking` 也挂在 `ACPManager` 上，但属于
**internal / unstable**：仅供 Agentao 自己的交互式 CLI inline-confirm
管线使用，签名可能随时变更，headless embedder **不应**依赖。Headless
场景请使用 `send_prompt` 或 `prompt_once`。支持级别以
[`docs/features/headless-runtime.md`](../../../docs/features/headless-runtime.md)
为准。

完整 API 见[附录 A · ACP 客户端](/zh/appendix/a-api-reference#a-7-acp-客户端)。

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
) -> PromptResult:
```

语义：

- 按 server 取独占锁，**fail-fast** 模式。若该 server 已有一轮在跑，立即抛 `AcpClientError(code=SERVER_BUSY)`——不等待，也不会偷偷排队
- 若没有长期客户端，本次**临时起一个**客户端；退出时拆掉
- 若已经有长期客户端（你之前调过 `start_server(name)`），就复用；子进程**跨越**本次调用继续存活
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
from agentao.tools.base import Tool

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

## 3.4.7 生命周期与恢复

三种常见失败场景的行为已被固化，embedder 不用自己卷恢复逻辑：

**取消 / 超时 → 下一次 turn 安全**。turn 槽、per-server 锁、pending prompt 槽都在 `finally` 里按固定顺序释放。取消或超时之后第一个 `send_prompt` / `prompt_once` 看到的是一个 ready、没有残留状态的 server。

**可恢复进程死亡 → 自动重建**。如果子进程在两次调用之间死了（干净退出、idle 非零退出且在上限内、stdio EOF、active turn 期间死亡），下次 `ensure_connected` / `send_prompt` 调用会关掉 dead client、把 `mgr.restart_count(name)` +1、然后透明地重建。`maxRecoverableRestarts`（默认 3）限制 idle 非零退出时连续自动重建的上限。

**致命进程死亡 → sticky，必须运维介入**。OOM / SIGKILL / `exit 137`、信号结束、连续 handshake 失败、或 idle 非零退出超过上限，都会把 server 标记为 sticky-fatal。`mgr.is_fatal(name)` 返回 `True`，所有调用都抛 `AcpClientError(code=TRANSPORT_DISCONNECT, details={"recovery": "fatal"})`，直到调 `mgr.restart_server(name)` 或 `mgr.start_server(name)` 清除标记。

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

classifier 是纯函数——`classify_process_death`——从 `agentao.acp_client` 导出，可以独立测试。完整决策矩阵见 [`docs/features/headless-runtime.md` §7.2](../../../docs/features/headless-runtime.md)。

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

## 3.4.9 健康检查与排错

`ACPManager.get_status()` 返回类型化的 `list[ServerStatus]`：

```python
from agentao.acp_client import ServerStatus

for s in mgr.get_status():             # 每个 s 都是 ServerStatus
    print(s.server, s.state, s.pid, s.has_active_turn)
    if s.state == ServerState.FAILED.value:
        info = mgr.get_handle(s.server).info
        print(f"{s.server} 失败：{info.last_error}")
```

核心字段：

- `server: str` — `.agentao/acp.json` 里的 server 名
- `state: str` — `ServerState` 枚举的字符串值
- `pid: int | None`
- `has_active_turn: bool` — 由 manager 的活跃 turn 槽派生；turn
  全生命周期（含 in-flight interaction 阶段）内都为 `True`

诊断字段（同一个 dataclass 上加量式暴露）：
`last_error`、`last_error_at`、`active_session_id`、`inbox_pending`、
`interaction_pending`、`config_warnings`。直接从 `ServerStatus`
上读即可；`mgr.get_handle(name).info` 和 `mgr.inbox` /
`mgr.interactions` 仍然保留，作为原始 handle 视图。完整字段说明与
从旧 dict 形态迁移的映射表见
[`docs/features/headless-runtime.md`](../../../docs/features/headless-runtime.md)。

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

---

下一节：[3.5 Zed / IDE 集成 →](./5-zed-ide-integration)
