# 03 Jsonrpc Client And Handshake

Parent doc: [ACP Client And Project-Local Servers](../../ACP_CLIENT_PROJECT_SERVERS.md)

Issue index: [ACP Client Project-Local Servers Issues](README.md)

## Goal

实现面向本地 ACP server 的 JSON-RPC client，并完成最小握手流程。

## Scope

- request id 管理
- pending request registry
- stdout reader
- response / notification 路由
- `initialize`
- `session/new`

## Deliverables

- `agentao/acp_client/client.py`
- 最小握手 API
- request/response 路由单测

## Dependencies

- 01
- 02

## Design Notes

- 复用现有 ACP method 常量和协议语义
- 采用 NDJSON，一行一个 JSON object
- 后台 reader 线程持续读 server stdout
- 需要支持：
  - `call(method, params)`
  - `notify(method, params)`
  - `initialize()`
  - `create_session()`
- v1 一台 server 在当前 CLI 中仅维护一个活跃 session

## Tests

- request id 唯一
- response 能正确唤醒对应 pending slot
- notification 不进入 response 路由
- `initialize` 成功后记录能力信息
- `session/new` 成功后记录 sessionId

## Acceptance Criteria

1. runtime 能完成 `initialize -> session/new`
2. 非法 JSON 或错误响应能稳定失败
3. 失败状态能反馈到 runtime 层

## Out Of Scope

- `session/prompt`
- `session/cancel`
- CLI idle flush
