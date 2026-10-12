# ACP Server 内部结构

**状态：** 面向贡献者的内部笔记。对外的协议行为见开发者指南：[第 3.2 节 · Agentao 作为 ACP Server](../../developer-guide/zh/part-3/2-agentao-as-server.md) 与 [附录 C · ACP 消息字段](../../developer-guide/zh/appendix/c-acp-messages.md)。英文对照件：[acp-server-internals.md](acp-server-internals.md)。

本笔记说明 `agentao/acp/` 是怎么搭起来的：进程结构、stdout 守卫、并发分发器及其关闭顺序、文件布局、测试，以及如何新增一个方法 handler。更细的依据在各模块的 docstring 里，改这里的规则前先读它们。

## 进程结构

```
                   ┌────────────────────────────────────────────────┐
                   │                AcpServer (server.py)           │
       stdin ───►  │   read loop ── 区分请求 / 响应                 │
                   │        │                       │               │
                   │        ▼ 提交                  ▼ 路由          │
                   │   ThreadPoolExecutor(8)   _pending_requests    │
                   │   handlers: initialize,   （等待客户端应答的   │
                   │   session/new, /prompt,    server → client     │
                   │   /cancel, /load, …        请求）              │
                   │        │                                       │
                   │        ▼ 持写锁写出                            │
       stdout ◄────┤   JSON-RPC 响应 + 通知                         │
                   │                                                │
                   │   sessions: AcpSessionManager                  │
                   │     └─ AcpSessionState ─ Agentao 运行时 (×N)   │
       stderr ◄────┤   sys.stdout → stderr；logger 输出到 stderr    │
                   └────────────────────────────────────────────────┘
```

`agentao --acp` 经 `agentao/cli/_light.py::run_acp_mode`（不需要 `[cli]` 额外依赖）进入 `agentao/acp/__main__.py::main`。`main()` 把 stdio 强制为 UTF-8，构造 server，注册全部 handler，然后跑读循环。`--resume` 变成 server 上的一个 `ResumeDirective`（`models.py`），由第一个 `session/new` 通过 `session_load.resume_session_on_new` 消费。

## stdout 卫生

`AcpServer()` 在不传 `stdin` / `stdout` 时（生产路径）会：

1. 把真正的 `sys.stdout` 留作私有输出句柄。所有 JSON-RPC 写入都走它，并持同一把锁。
2. 执行 `sys.stdout = sys.stderr`，进程里任何地方的 `print()` 都落到 stderr。
3. 若 `agentao` logger 还没有 handler，就加一个 stderr `StreamHandler`，让 INFO 日志在 `LLMClient` 配好日志之前也能看到。

测试会显式传入流，因此上述三步都不发生，也就不改动全局状态。`tests/test_acp_cli_entrypoint.py` 检查 stdout 上只有 ACP 消息。

## 并发分发

handler 跑在 `ThreadPoolExecutor(max_workers=8)`（`thread_name_prefix="acp-handler"`）上。同步分发会死锁：`confirm_tool` 阻塞等待 `session/request_permission` 的应答，而这个应答恰恰要从读循环进来。读线程只负责解析、提交请求，并把响应路由到 `_pending_requests`。

两个后果：

- 并发请求的响应顺序不确定。客户端按 id 匹配。
- `AcpServer.call()` 的等待**没有超时**。只有客户端应答或 `_cancel_all_pending_requests`（连接关闭）才会结束等待。`session/cancel` 不碰挂起的 server → client 请求。

### 关闭顺序

`AcpServer.run()` 的 `finally` 分四步执行，顺序不能动：

1. **取消挂起的 server → client 请求。** 卡在 `confirm_tool` 里的 worker 收到 `PendingRequestCancelled`，拒绝该工具。
2. **触发每个活动轮次的取消令牌**（`AcpSessionManager.cancel_all_active_turns`）。正在调模型或跑工具的 worker 没有别的停止信号。这一步不断开 MCP，因为 worker 可能还在用。
3. **用 `shutdown(wait=True)` 排空线程池。** 第 1、2 步让每个 worker 都收到停止信号，但这个信号是协作式的，排空也没有超时：worker 若卡在不检查令牌的阻塞调用里（比如同步工具的 `execute()`），关闭仍要等这次调用返回。`run()` 返回即成为所有 handler 写入的 happens-before 屏障。
4. **关闭全部会话**（`AcpSessionManager.close_all`）。MCP 最后断开，此时每个 worker 都已退出。

在第 1 或第 2 步之前排空会让进程挂住。`tests/test_acp_request_permission.py` 覆盖这个顺序。

## 文件布局

```
agentao/acp/
├── __main__.py                 # main()：UTF-8 stdio、注册 handler、运行
├── protocol.py                 # METHOD_*、ACP_PROTOCOL_VERSION、错误码
├── server.py                   # AcpServer、JsonRpcHandlerError、分发、call()
├── session_manager.py          # AcpSessionManager：线程安全注册表、close_all
├── models.py                   # AcpSessionState、AcpConnectionState、ResumeDirective
├── schema.py, schema_export.py # pydantic 线上模型、JSON Schema 导出
├── _handler_utils.py           # session/* 通用守卫、hold_idle_turn_lock
├── initialize.py               # initialize + AGENT_CAPABILITIES
├── llm_auth.py                 # Terminal Auth 方法、按会话解析 LLM 配置
├── session_new.py              # session/new、agent_factory 注入点、cwd/mcpServers 解析
├── session_prompt.py           # session/prompt、ContentBlock 解析、停止原因
├── session_cancel.py           # session/cancel（幂等）
├── session_load.py             # session/load、启动恢复、历史注入
├── session_set_config_option.py, session_set_mode.py,
│   agentao_set_model.py, session_set_model.py, session_list_models.py
├── mcp_translate.py            # ACP mcpServers → 内部 MCP 配置
├── transport.py                # ACPTransport.emit()：事件 → session/update
├── _transport_interaction.py   # confirm_tool、ask_user、权限选项
├── _transport_replay.py        # session/load 的 replay_history
├── _transport_helpers.py       # 工具 kind 映射、diff、plan 映射、通知
├── _tool_call_content.py       # 流式输出缓冲（FLUSH_CHARS、MAX_CHARS）
└── _lifecycle.py               # ACP 侧的 SessionStart / SessionEnd 钩子
```

## 测试

ACP server 的测试都在 `tests/test_acp_*.py`。`test_acp_client_*.py` 测的是 ACP **客户端**（`agentao/acp_client/`），不是这个 server。

| 范围 | 文件 |
|------|------|
| 帧格式、错误码、schema | `test_acp_protocol.py`、`test_acp_schema.py` |
| 握手、Terminal Auth | `test_acp_initialize.py`、`test_acp_terminal_auth.py` |
| 会话注册表、多会话隔离 | `test_acp_session_manager.py`、`test_acp_multi_session.py` |
| `session/new`、MCP 注入 | `test_acp_session_new.py`、`test_acp_mcp_injection.py` |
| `session/prompt`、停止原因 | `test_acp_session_prompt.py`、`test_acp_stop_reason.py` |
| 事件映射、工具内容 | `test_acp_transport.py`、`test_acp_tool_kind.py`、`test_acp_tool_call_diff.py`、`test_acp_tool_call_content.py` |
| 权限、`ask_user` | `test_acp_request_permission.py`、`test_acp_permission_order.py`、`test_acp_ask_user.py` |
| 取消 | `test_acp_session_cancel.py` |
| 加载、恢复、技能 | `test_acp_session_load.py`、`test_acp_session_load_skills.py`、`test_acp_resume_on_startup.py` |
| 切换模型 / 模式 | `test_acp_set_config_option.py`、`test_acp_session_set_model.py` |
| 生命周期钩子 | `test_acp_session_lifecycle_hooks.py` |
| CLI 入口、stdout 卫生 | `test_acp_cli_entrypoint.py` |

```bash
uv run python -m pytest tests/ -k test_acp_
```

## 新增方法 handler

照现有 `session_*.py` 模块的写法：

1. 在 `protocol.py` 里加一个 `METHOD_*` 常量。
2. 写 `handle_<method>(server, params, *, <deps>) -> dict`。常规 `session/*` 守卫用 `_handler_utils.require_active_session`；方法不能在轮次进行中执行时，用 `hold_idle_turn_lock`。
3. 校验参数。形状错误抛 `TypeError`（→ `-32602`），其他错误抛 `JsonRpcHandlerError(code, message)`。其余异常一律变成 `-32603`。
4. 加 `register(server, ...)`，内部调用 `server.register(METHOD_X, lambda params: handle_<method>(server, params, ...))`。
5. 在 `__main__.py::main()` 里调用 `register(server)`。
6. 既直接测 handler，也走线上路径测。通过 `session_new` 暴露的 `agent_factory` 关键字注入假 agent，测试就不用拉起 LLM 栈。
7. 在开发者指南 3.2 和附录 C 里记录该方法，中英文都要写。
