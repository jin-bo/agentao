# 04 Session Prompt And Cancel Flow

Parent doc: [ACP Client And Project-Local Servers](../../ACP_CLIENT_PROJECT_SERVERS.md)

Issue index: [ACP Client Project-Local Servers Issues](README.md)

## Goal

补齐用户向 ACP server 发送消息和取消活跃请求的最小交互闭环。

## Scope

- `session/prompt`
- `session/cancel`
- `ensure_started()`
- 未启动时自动启动后发送 prompt
- `busy -> ready` 状态切换
- 为后续 server->user interaction 预留活跃请求与交互挂起的状态接口

## Deliverables

- `agentao/acp_client/client.py`
- `agentao/acp_client/manager.py`
- prompt / cancel 相关测试

## Dependencies

- 02
- 03

## Design Notes

- `/acp send` 的高层语义依赖本 issue
- `send_prompt(text)` 应在必要时自动：
  - 启动进程
  - `initialize`
  - `session/new`
- 同一 server v1 不支持多并发 prompt
- `cancel_active_turn()` 只取消当前 server 的活跃请求
- 如果后续某轮 prompt 进入 `waiting_for_user`，取消语义仍应能终止该轮

## Tests

- 未启动时可自动启动并发送 prompt
- 活跃请求期间状态为 `busy`
- 正常结束后回到 `ready`
- cancel 后状态可恢复
- server 在请求期间崩溃时状态进入 `failed`

## Acceptance Criteria

1. 一个 server 可以稳定完成多轮 send
2. 取消语义可预期
3. 自动启动路径与显式启动路径行为一致

## Out Of Scope

- CLI 命令解析
- inbox 渲染
- server 请求用户确认或输入时的桥接命令
