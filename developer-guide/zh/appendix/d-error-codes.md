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
        ...   # 配置问题——把 details 交给运维
```

## D.1 错误码表

| 代码 | 典型原因 | 处置 |
|------|----------|------|
| `config_invalid` | `.agentao/acp.json` 格式错、缺必填字段、env 变量展开失败 | 校验 JSON；打印 `e.details`——含 `server` 及出问题的字段 |
| `server_not_found` | `prompt_once(name=...)` / `start_server(name=...)` 传了未声明的名字 | `ACPManager().get_status()` 看已声明名字 |
| `process_start_fail` | `command` 不在 PATH、无可执行权限、子进程启动时崩 | 看 `e.cause` 与 `e.details['stderr']`；手动跑 `command` + `args` 复现 |
| `handshake_fail` | 服务器进程启动了但 `initialize` 响应没到，或能力不兼容 | 通常是 init 阶段遇到了 `transport_disconnect` / `request_timeout`——查服务器日志 |
| `request_timeout` | RPC 超出你传入（或默认的）`timeout=` | 加大超时，或排查服务器是否卡在长工具调用 |
| `transport_disconnect` | 服务器子进程中途退出、管道关闭、stdio 帧损坏 | 看 `e.details['exit_code']` 与 stderr 尾部；OOM 被杀、服务器 bug 最常见 |
| `interaction_required` | 非交互调用（`interactive=False`，`prompt_once` 默认如此），但服务器发起了权限/输入请求 | 改用交互会话，或用 `PermissionEngine` 规则预批 |
| `protocol_error` | 服务器发了非法 JSON-RPC 报文、意外方法、ID 不匹配 | 升级服务器或报 bug，几乎一定是服务器缺陷 |
| `server_busy` | 同一服务器已有 turn 在跑，而调用是 fail-fast（`prompt_once` 一律是） | 等待后重试，或改用带排队的会话 API |

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

日志里 **永远** 连 `details` 一起打，这样事后无需复现就能诊断。

## D.4 异常类层级

```
AcpClientError                           # 基类——有 .code / .details / .cause
├── AcpServerNotFound (同时继承 KeyError) # code = server_not_found
├── AcpRpcError                           # JSON-RPC 错误响应（protocol_error）
└── AcpInteractionRequiredError          # code = interaction_required
```

`AcpServerNotFound` 继承 `KeyError`，迁移期老代码里的 `except KeyError` 仍能兜住。

## D.5 重试策略

| 代码 | 可重试？ | 策略 |
|------|----------|------|
| `request_timeout` | 可（幂等调用） | 指数退避，设上限 |
| `transport_disconnect` | 可（需重启进程） | `ACPManager.stop_server()` → `start_server()` → 重试 |
| `server_busy` | 可 | 等当前 turn 完成；`get_status()` 轮询 |
| `process_start_fail` | 否 | 需要运维介入 |
| `handshake_fail` | 否（通常） | 需要运维介入 |
| `config_invalid` | 否 | 修配置 |
| `server_not_found` | 否 | 改调用处 |
| `protocol_error` | 否 | 报 bug |
| `interaction_required` | — | 不是重试问题——改用交互模式 |

---

→ [附录 G · 术语表](./g-glossary)
