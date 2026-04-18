# 附录 C · ACP 消息字段参考

Agentao 的 ACP 服务器/客户端发出的每种消息的字段级速查。端到端叙事（握手 → 提示 → 流式 → 取消）见 [Part 3](/zh/part-3/)。本附录是你在调试畸形消息时打开的那一页。

**约定**：
- `→` 请求；`←` 响应；`⇠` 服务器到客户端通知
- 所有消息都是 stdio 上的 NDJSON JSON-RPC 2.0
- `{…}` = 对象；`[…]` = 数组；`T|U` = 联合；`?` = 可选

## C.1 `initialize`

握手，MUST 是第一个调用。

### → 请求

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `protocolVersion` | `int` | 是 | Agentao 讲 `1`；`bool` 被显式拒绝 |
| `clientCapabilities` | `object` | 是 | 客户端能力标志（如 `fs: {readFile, writeFile}`） |
| `clientInfo` | `object` | 否 | `{name, version, title}` —— 纯信息性 |

### ← 响应

| 字段 | 类型 | 说明 |
|------|------|------|
| `protocolVersion` | `int` | 协商后——通常回显请求值；版本不匹配也不报错 |
| `agentCapabilities` | `object` | 见下 |
| `authMethods` | `[]` | v1 为空——Agentao 不做 ACP 级认证 |
| `agentInfo` | `object` | `{name: "agentao", title: "Agentao", version}` |
| `extensions` | `[{method, description}]` | 扩展方法；含 `_agentao.cn/ask_user` |

### `agentCapabilities` 字段（v0.2.x）

| 字段 | 值 | 含义 |
|------|-----|------|
| `loadSession` | `true` | 支持 `session/load`——见 [7.2](/zh/part-7/2-ide-plugin#3-ide-重启后的会话恢复) |
| `promptCapabilities.image` | `false` | v1 基线仅文本 |
| `promptCapabilities.audio` | `false` | |
| `promptCapabilities.embeddedContext` | `false` | |
| `mcpCapabilities.sse` | `true` | 可用 SSE 传输 |
| `mcpCapabilities.http` | `false` | **不**支持 HTTP 传输 |

## C.2 `session/new`

新建会话。

### → 请求

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `cwd` | `string` | 是 | **绝对**路径，必须存在且是目录 |
| `mcpServers` | `[object]` | 是（`[]` 可接受） | 每会话的 MCP 服务器 |

### `mcpServers[i]` 形状

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | `string` | 非空 |
| `type` | `"stdio"` / `"http"` / `"sse"` | 有的客户端会声明 `http`，但 agent 按 `mcpCapabilities` 拒 |
| `command` | `string` | 仅 stdio |
| `args` | `[string]` | 仅 stdio |
| `env` | `[{name, value}]` | 仅 stdio——注意**是键值对对象数组**，不是 map |
| `url` | `string` | 仅 sse / http |
| `headers` | `[{name, value}]` | 仅 sse / http |

### ← 响应

| 字段 | 类型 | 说明 |
|------|------|------|
| `sessionId` | `string` | UUID。收好——后续每个调用都要用 |

### 常见失败

| JSON-RPC code | 原因 |
|---------------|------|
| `-32002` | 在 `initialize` 之前调（SERVER_NOT_INITIALIZED） |
| `-32602` | `cwd` 不是绝对/不存在，或 `mcpServers` 格式错 |

## C.3 `session/prompt`

跑一轮用户交互，返回时 agent 已停。

### → 请求

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `sessionId` | `string` | 是 | 从 `session/new` 或 `session/load` 拿 |
| `prompt` | `[ContentBlock]` | 是 | 见下 |

### `ContentBlock` 形状

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | `"text"` | v1 基线仅 text |
| `text` | `string` | 用户消息 |

### ← 响应

| 字段 | 类型 | 说明 |
|------|------|------|
| `stopReason` | `string` | `"end_turn"`、`"max_tokens"`、`"cancelled"` 等 |

## C.4 `session/update` ⇠ 通知

服务器到客户端在一轮内的流式更新。

### Params

| 字段 | 类型 | 说明 |
|------|------|------|
| `sessionId` | `string` | 活跃会话 |
| `update` | `object` | 按 `sessionUpdate` 分支 |

### 变体（`update.sessionUpdate`）

| 值 | Agentao 事件 | 额外字段 |
|----|--------------|----------|
| `user_message_chunk` | （回放） | `content: {type:"text", text}` |
| `agent_message_chunk` | `LLM_TEXT` | `content: {type:"text", text}` |
| `agent_thought_chunk` | `THINKING` / `ERROR` | `content: {type:"text", text}` |
| `tool_call` | `TOOL_START` | `toolCallId`、`title`、`kind`、`status:"pending"`、`rawInput` |
| `tool_call_update` | `TOOL_OUTPUT` / `TOOL_COMPLETE` | `toolCallId`、`status`，可选 `content[]` 追加 |

### `tool_call.kind`（封闭枚举）

`read`、`edit`、`delete`、`move`、`search`、`execute`、`think`、`fetch`、`switch_mode`、`other`。Agentao 会把内部工具名映射到这枚举——见 `agentao/acp/transport.py`。

### `tool_call_update.status`

| 值 | 时机 |
|----|------|
| `in_progress` | 输出流式片段 |
| `completed` | 工具成功 |
| `failed` | 工具抛错或返回错误 |

## C.5 `session/request_permission` ⇠ 通知

服务器请客户端确认工具调用。

### Params

| 字段 | 类型 | 说明 |
|------|------|------|
| `sessionId` | `string` | |
| `toolCall` | `object` | 形状同 `session/update.tool_call` |
| `options` | `[{optionId, name, kind}]` | 通常 `["allow", "allow_always", "reject"]` |

### 期望响应（客户端 → 服务器）

| 字段 | 类型 | 说明 |
|------|------|------|
| `outcome.outcome` | `"selected"` 或 `"cancelled"` | |
| `outcome.optionId` | `string` | 当 `selected` 时必填 |

**取消规则**：如果客户端一直不回，而整轮被取消，agent 会把所有挂起的权限请求以 `cancelled` 解决。见 [7.2 陷阱](/zh/part-7/2-ide-plugin#陷阱)。

## C.6 `session/cancel`

中止当前轮与所有挂起的权限请求。

### → 请求

| 字段 | 类型 | 说明 |
|------|------|------|
| `sessionId` | `string` | |

### ← 响应

空对象 `{}`。进行中的 `session/prompt` 最终以 `stopReason: "cancelled"` 解决。

## C.7 `session/load`

按 id 恢复会话。仅当 agent 在能力里声明 `loadSession: true` 时可用。

### → 请求

| 字段 | 类型 | 说明 |
|------|------|------|
| `sessionId` | `string` | 必须是之前创建过的 |
| `cwd` | `string` | 绝对路径；要与会话原 cwd 匹配 |
| `mcpServers` | `[object]` | 重声明——指纹要与 `session/new` 时一致 |

### ← 响应

空对象。Agent 会通过 `session/update` 通知回放历史轮次（从持久化历史重建），之后才进入可接受下一轮 `session/prompt` 的状态。

### 指纹规则

`session/new` 时 `mcpServers` 会被哈希。如果 `session/load` 传了不同的集合（增/删/重排），Agentao 可能拒绝或静默重建——取决于实现阶段。拿不准就**传原样列表**。

## C.8 `_agentao.cn/ask_user` ⇠ 通知（扩展）

Agentao 独有，服务器向用户问一个自由文本问题。

### Params

| 字段 | 类型 | 说明 |
|------|------|------|
| `sessionId` | `string` | |
| `question` | `string` | 自由文本 |

### 期望响应

| 字段 | 类型 | 说明 |
|------|------|------|
| `answer` | `string` | 用户自由文本回答 |

用户不可达时，客户端可回返哨兵 `"(user unavailable)"`（常量 `ASK_USER_UNAVAILABLE_SENTINEL`）。

## C.9 JSON-RPC 错误码速查

| 代码 | 名 | 含义 |
|------|-----|------|
| `-32700` | Parse error | 服务器收到非法 JSON |
| `-32600` | Invalid Request | JSON 合法但不是 JSON-RPC Request |
| `-32601` | Method not found | 方法不存在或不可用 |
| `-32602` | Invalid params | 方法参数非法 |
| `-32603` | Internal error | 内部 JSON-RPC 错 |
| `-32002` | Server not initialized | `initialize` 之前调了 session 方法 |

对照 `AcpErrorCode` 见 [附录 D.2](./d-error-codes#d-2-json-rpc-数值码-vs-acperrorcode)。

---

→ [附录 D · 错误码](./d-error-codes) · [附录 E · 迁移](./e-migration)
