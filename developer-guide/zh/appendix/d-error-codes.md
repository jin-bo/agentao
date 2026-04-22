# 附录 D · AcpErrorCode 参考

当 Agentao 被当作 **ACP 客户端**（`from agentao.acp_client import ACPManager`）使用时，所有失败都会以 `AcpClientError`（或其子类）抛出，携带结构化 `code: AcpErrorCode`。分支判断请用 `code` 而非消息字符串——消息不稳定。

```python
from agentao.acp_client import ACPManager, AcpClientError, AcpErrorCode

try:
    result = manager.prompt_once(name="x", prompt="hi", timeout=30)
except AcpClientError as e:
    if e.code is AcpErrorCode.REQUEST_TIMEOUT:
        ...   # 给用户看 "请重试"
    elif e.code is AcpErrorCode.HANDSHAKE_FAIL:
        ...   # 配置问题——把 details 交给运维。
              # `e.details["underlying_code"]` 保留了原始根因
              # （超时 / 断连 / 协议错）。
    elif e.details.get("phase") == "handshake":
        ...   # 握手阶段抛出的 AcpRpcError——见 §D.7
```

## D.1 错误码表

| 代码 | 典型原因 | 处置 |
|------|----------|------|
| `config_invalid` | `.agentao/acp.json` 格式错、缺必填字段、env 变量展开失败 | 校验 JSON；打印 `e.details`——含 `server` 及出问题的字段 |
| `server_not_found` | `prompt_once(name=...)` / `start_server(name=...)` 传了未声明的名字 | `ACPManager().get_status()` 看已声明名字 |
| `process_start_fail` | `command` 不在 PATH、无可执行权限、子进程启动时崩 | 看 `e.cause` 与 `e.details['stderr']`；手动跑 `command` + `args` 复现 |
| `handshake_fail` | 服务器进程启动了但 `initialize` / `session/new` 阶段失败（setup 阶段的 protocol/transport/timeout）。**manager 对非 RPC `AcpClientError` 会自动重分类**：把 `code` 从 `PROTOCOL_ERROR` / `TRANSPORT_DISCONNECT` / `REQUEST_TIMEOUT` 改成 `HANDSHAKE_FAIL`，同时把原 code 保存到 `details["underlying_code"]`——这样 `case HANDSHAKE_FAIL` 分支继续命中，调用方也能进一步区分底层原因（见 §D.7）。`AcpRpcError` **不会**被改 code（见 §D.2 合约）；RPC 握手失败请用 `isinstance(err, AcpRpcError) and err.details["phase"] == "handshake"` 识别。 | 查服务器日志；`details["underlying_code"]` 告诉你根因是超时、断连、还是协议错 |
| `request_timeout` | RPC 超出你传入（或默认的）`timeout=` | 加大超时，或排查服务器是否卡在长工具调用 |
| `transport_disconnect` | 服务器子进程中途退出、管道关闭、stdio 帧损坏 | 看 `e.details['exit_code']` 与 stderr 尾部；OOM 被杀、服务器 bug 最常见 |
| `interaction_required` | 非交互调用（`interactive=False`，`prompt_once` 默认如此），但服务器发起了权限/输入请求 | 改用交互会话，或用 `PermissionEngine` 规则预批 |
| `protocol_error` | 服务器发了非法 JSON-RPC 报文、意外方法、ID 不匹配 | 升级服务器或报 bug，几乎一定是服务器缺陷 |
| `server_busy` | 同一服务器已有 turn 在跑，而调用是 fail-fast（`prompt_once` 一律是）。headless 场景中，这是 Week 1 **单服务器单活跃 turn、不排队** 合约（见 [`docs/features/headless-runtime.md`](../../../docs/features/headless-runtime.md)）下固定的失败形态 | 等待后重试；没有隐式队列——调用方自己轮询 `get_status()` 并门禁提交 |

## D.2 JSON-RPC 数值码 vs `AcpErrorCode`

两个命名空间，别搞混：

| 层 | 类型 | 例 | 在哪读 |
|----|------|-----|--------|
| **结构化分类** | `AcpErrorCode`（字符串枚举） | `AcpErrorCode.REQUEST_TIMEOUT` | 非 RPC 错：`err.code`；`AcpRpcError`：`err.error_code` |
| **JSON-RPC 线上码** | `int` | `-32603`（Internal error）、`-32601`（Method not found） | `AcpRpcError.rpc_code`（为兼容老调用者，也挂在 `err.code` 上） |

`AcpRpcError` 的 `error_code` 永远是 `AcpErrorCode.PROTOCOL_ERROR`——那是它的分类。需要数值码时明确读 `rpc_code`：

```python
from agentao.acp_client import AcpRpcError

try:
    ...
except AcpRpcError as e:
    print(e.rpc_code, e.rpc_message)    # 例如 -32601, "Method not found"
    print(e.error_code)                  # 永远是 AcpErrorCode.PROTOCOL_ERROR
```

## D.3 Details 字典

每个 `AcpClientError` 都带一个 `details: dict`，内容跟 code 配套：

| 代码 | 典型 `details` 键 |
|------|-------------------|
| `server_not_found` | `server` |
| `process_start_fail` | `server`、`command`、`args`、`stderr`（尾部） |
| `handshake_fail` | `server`、`protocol_version`、`phase` |
| `request_timeout` | `server`、`method`、`timeout` |
| `transport_disconnect` | `server`、`exit_code` |
| `interaction_required` | `server`、`method`、`prompt`、`options` |
| `server_busy` | `server` |

特殊键——**`phase`**：只要异常是在 `initialize` / `session/new` 阶段抛出的 `AcpClientError`（含 `AcpRpcError`），`details["phase"] == "handshake"` 都会被打上。它是跨子类统一的"是否握手阶段失败"规范信号——见 §D.7。

特殊键——**`underlying_code`**：manager 把非 RPC `AcpClientError` 的 `code` 重分类成 `handshake_fail` 时，会把原始 `AcpErrorCode`（`PROTOCOL_ERROR` / `TRANSPORT_DISCONNECT` / `REQUEST_TIMEOUT` 之一）保存到这里。`AcpRpcError` 不写这个键——它的底层细节在 `rpc_code` / `rpc_message` 上。

日志里 **永远** 连 `details` 一起打，这样事后无需复现就能诊断。

## D.4 异常类层级

```
AcpClientError                           # 基类——有 .code / .details / .cause
├── AcpServerNotFound (同时继承 KeyError) # code = server_not_found
├── AcpRpcError                           # code: int（JSON-RPC 线上码）；error_code = protocol_error
└── AcpInteractionRequiredError          # code = interaction_required
```

`AcpServerNotFound` 继承 `KeyError`，迁移期老代码里的 `except KeyError` 仍能兜住。

`AcpRpcError` 是唯一一个 `.code` **不是** `AcpErrorCode` 的子类。它保留原始 JSON-RPC 数值码以兼容老调用者（见 §D.2），结构化分类永远是 `error_code = AcpErrorCode.PROTOCOL_ERROR`。握手阶段的重分类是**非对称**的：非 RPC `AcpClientError` 会被 manager 把 `code` 改成 `HANDSHAKE_FAIL`（原 code 保存到 `details["underlying_code"]`），而 `AcpRpcError` 不动以维持类合约。两条路径都会打 `details["phase"] = "handshake"`，这个键是跨子类的规范判定信号。

## D.5 状态与错误合约（headless）

在 headless / 守护进程集成模式下，状态面（`ACPManager.get_status()`、`ACPManager.readiness(name)`）和错误面是**两路独立信号**。消费顺序必须固定：

1. **先看 `state`（或 `readiness(name)`）**，它是"现在能不能提交 turn"的权威信号。
2. **再看 `last_error` / `last_error_at`** 作为诊断补充——它们描述"最近一次出过什么错"，不是"当前是否出错"。

记录错误面的关键语义：

- `last_error` **不会**在 turn 成功后被自动清空。这是有意设计：一个每分钟轮询一次的 host 仍然能看到最近一次失败。
- 如需显式清空（例如把错误转发给外部日志系统之后），调用 `ACPManager.reset_last_error(name)`。新错误会自动覆盖旧错误。
- `last_error_at` 是带 `tzinfo=timezone.utc` 的 `datetime`，**赋值时刻是错误被存入 manager 的那一刻**，不是 raise 的那一刻。请据此判断错误是否陈旧：`state == "ready"` + 一个非常旧的 `last_error_at` 等价于"历史错误，不阻塞调度"。
- 有两个 code **不会**写入 `last_error`，因为它们是调用方侧信号：`SERVER_BUSY`（每次重试都覆盖真实失败就没意义了）、`SERVER_NOT_FOUND`（根本没有对应的 server 状态可以挂）。其他 code 都会被记录。

## D.6 重试策略

| 代码 | 可重试？ | 策略 |
|------|----------|------|
| `request_timeout` | 可（幂等调用） | 指数退避，设上限 |
| `transport_disconnect` | 可（需重启进程） | `ACPManager.stop_server()` → `start_server()` → 重试 |
| `server_busy` | 可 | 等当前 turn 完成；`get_status()` 轮询 |
| `process_start_fail` | 否 | 需要运维介入 |
| `handshake_fail` | 否（通常） | 需要运维介入。直接命中非 RPC 握手失败；RPC 情况下，也把任何在 `details["phase"] == "handshake"` 分支内的异常按同样处理（见 §D.7）。 |
| `config_invalid` | 否 | 修配置 |
| `server_not_found` | 否 | 改调用处 |
| `protocol_error` | 否 | 报 bug。**注意**：握手阶段抛出的 `AcpRpcError` 始终带 `error_code = PROTOCOL_ERROR`——先看 `details["phase"]` 是否是 `"handshake"` 来区分是握手失败（配置/运维介入）还是稳态服务端 bug。 |
| `interaction_required` | — | 不是重试问题——改用交互模式 |

## D.7 识别握手阶段失败（规范写法）

握手 / session-setup 失败按子类分两种，manager 的分类方式是**非对称**的：

- **非 RPC `AcpClientError`**——例如服务端还没回应前的超时、transport 断连、或协议层问题。manager 会把 `code` 重分类为 `AcpErrorCode.HANDSHAKE_FAIL`，**同时**把原始 `AcpErrorCode` 保存到 `details["underlying_code"]`——老写法的 `case HANDSHAKE_FAIL:` 照常命中，调用方还能进一步细分根因。
- **`AcpRpcError`**——服务端对 `initialize` / `session/new` 回了 JSON-RPC 错误。类合约不允许改 `code`（int 线上码）或 `error_code`（`PROTOCOL_ERROR`），manager 不动。识别用 `isinstance(err, AcpRpcError)` + `details["phase"] == "handshake"`。

两支都会打 `details["phase"] = "handshake"`，所以这个键是跨子类的规范判定——而老代码里的 `case AcpErrorCode.HANDSHAKE_FAIL:` 对非 RPC 路径依旧继续工作。

```python
from agentao.acp_client import AcpClientError, AcpErrorCode, AcpRpcError

try:
    manager.connect_server("x", timeout=30)
except AcpRpcError as e:
    # RPC 层握手拒绝（也会命中稳态 RPC 错——用 `details["phase"]` 区分）。
    if e.details.get("phase") == "handshake":
        ...   # 服务端拒绝握手——e.rpc_code / e.rpc_message
    else:
        ...   # 已建连会话上的 JSON-RPC 错
except AcpClientError as e:
    # 非 RPC 握手失败：老分支照常命中
    if e.code is AcpErrorCode.HANDSHAKE_FAIL:
        # `details["underlying_code"]` 保留原始根因
        underlying = e.details.get("underlying_code")
        if underlying is AcpErrorCode.REQUEST_TIMEOUT:
            ...   # init 超时——加大 timeout 或检查服务器健康
        elif underlying is AcpErrorCode.TRANSPORT_DISCONNECT:
            ...   # 子进程在 setup 阶段退出
        else:
            ...   # 协议层握手失败
    elif e.code is AcpErrorCode.REQUEST_TIMEOUT:
        ...   # 已建连会话上的稳态超时
```

如果想要*一条*统一判定，用 `details.get("phase") == "handshake"`——两种子类都覆盖。上面的两分支写法是为了让已经按 `case HANDSHAKE_FAIL` 分发的宿主（Part 3 §3.4.8 等示例）能直接扩展到 RPC 情况。

---

→ [附录 G · 术语表](./g-glossary)
