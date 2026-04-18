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

公开接口：

| 方法 | 作用 |
|-----|-----|
| `start_all()` / `start_server(name)` | 启子进程 + 握手 |
| `stop_all()` / `stop_server(name)` | 优雅关停 |
| `prompt_once(name, prompt, ...)` | 一次性打一发——**推荐入口** |
| `send_prompt(name, prompt, ...)` | 长会话变体（子进程常驻） |
| `cancel_turn(name)` | 取消进行中的轮次 |
| `get_status(name=None)` | 可观察状态快照 |

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

- 按 server 取独占锁，**fail-fast** 模式。若该 server 已有一轮在跑，立即抛 `AcpClientError(code=SERVER_BUSY)`——不等待
- 若没有长期客户端，本次**临时起一个**客户端；退出时拆掉
- 若已经有长期客户端（你之前调过 `start_server(name)`），就复用；子进程**跨越**本次调用继续存活
- 返回 `PromptResult`，含 `stop_reason`、`session_id`、`cwd`、原始 payload

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
      "nonInteractivePolicy": "reject_all"
    }
  }
}
```

必填：`command`、`args`、`env`、`cwd`。可选：`autoStart`、`startupTimeoutMs`、`requestTimeoutMs`、`capabilities`、`description`、`nonInteractivePolicy`。

- `cwd` 相对路径相对于项目根（含 `.agentao/` 的目录）解析
- `env` 值里的 `$VAR` / `${VAR}` 会展开成宿主进程的环境变量
- `nonInteractivePolicy` = `"reject_all"`（默认）或 `"accept_all"`——无人值守时如何处理子 agent 的权限提示。生产用 `reject_all`

完整字段见[附录 B · 配置键](/zh/appendix/b-config-keys)。

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

## 3.4.7 取消与错误

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
        case _:                              raise
```

完整错误分类见[附录 D · 错误码](/zh/appendix/d-error-codes)。

## 3.4.8 健康检查与排错

```python
status = mgr.get_status()
# -> {"searcher": {"state": "ready", "pid": 8123, "last_activity": 1700000000.0}}

for name, info in status.items():
    if info["state"] == ServerState.FAILED.value:
        print(f"{name} 失败：{info['last_error']}")
```

子 agent（如果也是 Agentao 类型）的日志在 `<server cwd>/agentao.log`，其它 agent 可能输出到其它位置。`.agentao/acp.json` 里的 `cwd` 一定要指向可写目录，不然日志会丢。

## 3.4.9 生命周期自查

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
