# 05 Inbox And Idle Flush

Parent doc: [ACP Client And Project-Local Servers](../../ACP_CLIENT_PROJECT_SERVERS.md)

Issue index: [ACP Client Project-Local Servers Issues](README.md)

## Goal

建立异步消息队列和 CLI 空闲时显示机制，确保 ACP 返回不会破坏当前输入体验。

## Scope

- inbox 数据结构
- 消息标准模型
- queue 上限与背压
- CLI idle flush
- `<message from="server-name">...</message>` 渲染
- pending interaction 的可见化展示

## Deliverables

- `agentao/acp_client/inbox.py`
- `agentao/acp_client/render.py`
- `agentao/cli.py` 的 flush 集成
- 相关测试

## Dependencies

- 03
- 04

## Design Notes

- 建议内部消息字段：
  - `server`
  - `session_id`
  - `kind`
  - `text`
  - `timestamp`
  - `raw`
- flush 只发生在安全空闲点：
  - 显示输入提示前
  - slash command 执行完成后
  - Agentao 当前回复完成后
- 不在用户输入过程中抢占式打印
- v1 不把消息注入 Agentao 对话上下文
- 来自 ACP server 的 permission request / input request 也通过 inbox 可见化，但不直接弹同步菜单

## Tests

- inbox FIFO 顺序正确
- 队列上限生效
- flush 后消息被消费
- 渲染格式稳定
- 不破坏 CLI 输入循环

## Acceptance Criteria

1. ACP 异步消息可见但不干扰输入
2. 多 server 消息能按进入顺序排队
3. v1 展示层和上下文注入边界清晰
4. pending interaction 可以被用户发现并定位到具体 server / request

## Out Of Scope

- 自动将消息导入当前会话
- richer UI 分类渲染
