# 11 Ask-User ACP Extension Method

Parent doc: [ACP Client And Project-Local Servers](../../ACP_CLIENT_PROJECT_SERVERS.md)

Issue index: [ACP Client Project-Local Servers Issues](README.md)

## Goal

为 Agentao 在 ACP 模式下补齐自由文本用户输入能力，并将其定义为一个明确的 ACP extension method，而不是伪装成标准 ACP method。

## Scope

- 定义 `_agentao.cn/ask_user` method
- 定义 request / response payload
- 定义 capability / extension advertisement
- 在 ACP transport 中实现 `ask_user()` 桥接
- 定义不支持扩展时的降级行为

## Deliverables

- `agentao/acp/protocol.py`
- `agentao/acp/transport.py`
- ACP 文档更新
- 相关单测

## Dependencies

- 03
- 10

## Design Notes

- `_agentao.cn/ask_user` 是 Agentao 私有 ACP extension method
- 之所以不使用：
  - `session/ask_user`
  - `agentao/ask_user`
  - `ext/agentao/ask_user`
  是因为它们都不符合 ACP 扩展方法的命名约定或容易被误解为标准 method
- 采用 `_agentao.cn/ask_user` 的原因：
  - 以 `_` 开头，符合 ACP 扩展命名约束
  - 使用已注册域名 `agentao.cn` 作为命名空间，冲突风险低

### Request Shape

建议：

```json
{
  "jsonrpc": "2.0",
  "id": "srv_123",
  "method": "_agentao.cn/ask_user",
  "params": {
    "sessionId": "sess_xxx",
    "question": "Please provide branch name"
  }
}
```

### Response Shape

建议成功结果：

```json
{
  "outcome": "answered",
  "text": "feature/acp-client"
}
```

建议取消结果：

```json
{
  "outcome": "cancelled"
}
```

### Transport Mapping

- `ACPTransport.ask_user(question)` 调用 `_agentao.cn/ask_user`
- `answered` -> 返回 `text`
- `cancelled` / error / disconnect / malformed result -> 返回保守 sentinel

建议 sentinel：

- `"(user unavailable)"`

避免抛异常炸掉整轮执行。

### Capability Advertisement

需要在 ACP initialize 结果中显式声明支持的扩展，避免 client 误判。

具体字段形态可在实现时根据 ACP ext schema 定稿，但原则是：

- Agentao 必须显式声明支持 `_agentao.cn/ask_user`
- client 只有在明确支持后，才应尝试完整交互

### Non-goal

- `max_iterations` 不做 ACP extension method

原因：

- 使用频率低
- 不属于核心工作流
- 优先级明显低于 `ask_user`
- 即使缺失，也可在 ACP 模式下保守 stop

## Tests

- `_agentao.cn/ask_user` request 发送正确
- 正常 `answered` 结果映射为返回文本
- `cancelled` 结果映射为 sentinel
- JSON-RPC error 映射为 sentinel
- disconnect 映射为 sentinel
- malformed result 映射为 sentinel

## Acceptance Criteria

1. ACP 模式下的 `ask_user` 不再抛 `NotImplementedError`
2. 扩展方法命名明确且不与标准 ACP method 混淆
3. 不支持该扩展的 client 不会导致 turn 直接崩溃

## Out Of Scope

- `on_max_iterations` 扩展
- 自动把 ask_user 请求转成抢占式 CLI 输入框
