# 10 Server User-Interaction Bridge

Parent doc: [ACP Client And Project-Local Servers](../../ACP_CLIENT_PROJECT_SERVERS.md)

Issue index: [ACP Client Project-Local Servers Issues](README.md)

## Goal

定义当外部 ACP server 请求用户确认或请求用户输入时，Agentao 如何以异步、可控、不破坏当前 CLI 输入体验的方式桥接这些交互。

## Scope

- server -> user permission request
- server -> user free-form input request
- pending interaction 模型
- `waiting_for_user` 状态
- 用户显式响应命令
- 超时与取消策略

## Deliverables

- 交互请求数据模型
- runtime 中 pending interaction registry
- CLI 命令：
  - `/acp approve <name> <request-id>`
  - `/acp reject <name> <request-id>`
  - `/acp reply <name> <request-id> <text>`
- CLI 集成位置：
  - `agentao/cli/app.py`
  - `agentao/cli/commands.py`
  - `agentao/cli/commands_ext.py`
  - `agentao/cli/transport.py`
- 相关测试

## Dependencies

- 03
- 04
- 05
- 06
- 11

## Design Notes

- v1 不直接复用前台 Agentao 的 `confirm_tool()` 和 `ask_user()` 交互
- 这里的“前台 Agentao 交互”在当前代码结构中主要位于：
  - `agentao/cli/transport.py`
  - `agentao/cli/app.py`
- 原因：
  - 它们是同步阻塞模型
  - 会打断 prompt_toolkit 输入态
  - 无法优雅处理多个 ACP server 的并发请求
  - 会混淆“本地 Agentao 在问”与“外部 ACP server 在问”
- 因此外部请求统一转为 `pending interaction`
- 其中自由文本输入对应的 ACP extension method 固定为：
  - `_agentao.cn/ask_user`

建议区分两类 interaction：

- `permission`
  - 例如允许某个外部 server 执行某动作
- `input`
  - 例如要求用户补充 branch name、环境名等自由文本

建议内部模型字段：

- `request_id`
- `server`
- `session_id`
- `kind`
- `prompt`
- `details`
- `created_at`
- `deadline_at`

## Runtime Behavior

### State

每个 server runtime 增加状态：

- `waiting_for_user`

典型流转：

- `busy -> waiting_for_user -> busy`
- `busy -> waiting_for_user -> ready`
- `waiting_for_user -> failed`
- `waiting_for_user -> stopping`

### Visibility

interaction 不应抢占式弹出输入框，而应：

1. 进入 pending registry
2. 写入 inbox
3. 在 CLI 空闲时显示

示例：

```xml
<message from="planner">Permission request: allow tool X with args ...</message>
```

或：

```xml
<message from="planner">Input requested: Please provide branch name</message>
```

## CLI Response Model

用户通过显式命令响应：

- `/acp approve <name> <request-id>`
- `/acp reject <name> <request-id>`
- `/acp reply <name> <request-id> <text>`

设计要求：

- request-id 必须稳定可见
- 若 server 名不匹配或 request 不存在，要返回明确错误
- 回复成功后从 pending registry 中移除

## Timeout Policy

v1 建议保守策略：

- permission request 超时：
  - 默认 `reject`
- input request 超时：
  - 默认 `cancel`

同时用户仍可使用：

- `/acp cancel <name>`

来取消当前 server 的整轮活跃请求。

## Tests

- permission request 进入 pending registry
- input request 进入 pending registry
- approve/reject/reply 可正确返回给 server
- timeout 后按默认策略处理
- 多个 server 同时有 pending interaction 时不会串线
- 不破坏普通 CLI 输入循环

## Acceptance Criteria

1. 外部 ACP server 请求用户参与时不会破坏当前交互界面
2. 用户能显式看见并处理待确认或待输入请求
3. 超时和取消行为可预期

## Out Of Scope

- 抢占式菜单弹窗
- 自动代替用户做确认
- 自动将 interaction 转为当前 Agentao 会话里的 ask_user
- `max_iterations` 的 ACP 扩展设计
