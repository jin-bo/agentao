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
| `_meta["_agentao.cn/extensions"]` | `[{method, description}]` | 扩展方法，放在 `_meta` 下（ACP 标准的扩展通道）；含 `_agentao.cn/ask_user` |

### `agentCapabilities` 字段（v0.2.x）

| 字段 | 值 | 含义 |
|------|-----|------|
| `loadSession` | `true` | 支持 `session/load`——见 [7.2](/zh/part-7/2-ide-plugin#3-ide-重启后的会话恢复) |
| `promptCapabilities.image` | `true` | 0.4.8+：内联 `{data, mimeType}` 图片块；非视觉模型的退化行为见 [A.1](/zh/appendix/a-api-reference#图片输入与视觉退化) |
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
| `configOptions` | `[object]` | 模型/提供方选择项（`configId: "model"`）；通过 `session/set_config_option` 触发切换。无法枚举目录时为空数组 |

### 常见失败

| JSON-RPC code | 原因 |
|---------------|------|
| `-32002` | 在 `initialize` 之前调（SERVER_NOT_INITIALIZED） |
| `-32602` | `cwd` 不是绝对/不存在，或 `mcpServers` 格式错 |

> **启动时恢复。** 当 server 以 `agentao --acp --resume [SESSION_ID]` 启动时，**第一个** `session/new` 会恢复持久化会话而非新建空会话：把历史作为 `session/update` 通知重放，并返回持久化的 `sessionId`。未命中（空存储 / 未知 id / 文件损坏 / id 已存活）会静默降级为普通的全新会话。见 [3.2 → 启动时恢复会话](/zh/part-3/2-agentao-as-server#启动时恢复会话)。

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

`{configOptions: [...]}`——与 `session/new` 相同的模型/提供方选择项（无目录时为空数组）。Agent 还会通过 `session/update` 通知回放历史轮次（从持久化历史重建），之后才进入可接受下一轮 `session/prompt` 的状态。

### 指纹规则

`session/new` 时 `mcpServers` 会被哈希。如果 `session/load` 传了不同的集合（增/删/重排），Agentao 可能拒绝或静默重建——取决于实现阶段。拿不准就**传原样列表**。

## C.8 `session/set_config_option`

用 ACP 标准的 config-option 机制切换模型（以及可选地切换提供方）。**凭证绝不上线**——Agentao 在服务端用宿主注入的 `provider_resolver` 解析凭证（默认走环境变量）。

### → 请求

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `sessionId` | `string` | 是 | 活跃会话 |
| `configId` | `string` | 是 | 必须是 `"model"`；其它值 → `-32600` Invalid Request |
| `value` | `string` | 是 | `provider/model`（如 `openai/gpt-4o`）或裸 `model`（保持当前提供方）。按**第一个** `/` 切分；provider 转小写，model 原样保留 |

`apiKey`、`baseUrl`、`_meta` 以及任何其它字段都会被**拒绝**（`-32602`；`extra="forbid"` + handler 白名单）——*"凭证在服务端解析，绝不上线"*。

### ← 响应

| 字段 | 类型 | 说明 |
|------|------|------|
| `configOptions` | `[object]` | 刷新后的选择项（与 `session/new` 同形），反映新的 `currentValue` |

### 解析与错误

- **`provider/model`**：Agentao 调 `provider_resolver(provider_id)` → `{api_key, base_url?}`，然后整套切换 provider + model。**默认** resolver 只接受配置的 `LLM_PROVIDER`（大小写不敏感），读 `{PREFIX}_API_KEY` / `{PREFIX}_BASE_URL`；其它 provider → `-32600` `cannot resolve provider '<id>'`。要支持更多提供方，注入更丰富的 resolver。
- **裸 `model`**：只换模型（provider 不变），与 `_agentao.cn/set_model` 走同一条核心路径。
- resolver 失败时，服务端只记录 provider id + 异常**类型**，绝不记录消息（可能夹带密钥）。

### `ConfigOption` 结构（`configOptions` 里的条目）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `string` | `"model"` |
| `name` | `string` | `"Model"` |
| `category` | `"mode" \| "model" \| "thought_level"` | `"model"` |
| `type` | `"select"` | v1 只有 `select` |
| `currentValue` | `string?` | 当前模型的 `provider/model` |
| `options` | `[{value, name?, description?}]` | 候选项；`value` 是 `provider/model`。默认目录 = 当前环境的单一模型；更丰富的目录由宿主注入 |

## C.9 `_agentao.cn/set_model`（扩展）

Agentao 独有的自由文本换模型——`session/set_config_option` 裸值路径的厂商版兄弟。无密钥；与其共享同一条核心路径。

### → 请求

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `sessionId` | `string` | 是 | 活跃会话 |
| `model` | `string` | 是 | 当前提供方接受的任意模型 id（会 strip）。自由文本——不按目录校验 |

`apiKey`、`baseUrl`、`_meta` 会被**拒绝**（`-32602`）。

### ← 响应

| 字段 | 类型 | 说明 |
|------|------|------|
| `model` | `string` | 切换后的活跃模型 id（`agent.llm.model`） |

## C.10 `session/set_mode`

设置会话的 ACP `modeId`。字段是 ACP 标准的 **`modeId`**（不是 `mode`），且是**开放字符串**——一个 UI/行为选择器，不一定映射到 Agentao 权限预设。

### → 请求

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `sessionId` | `string` | 是 | 活跃会话 |
| `modeId` | `string` | 是 | 非空。精确命中权限预设（`read-only` / `workspace-write` / `full-access` / `plan`）时 Agentao 应用之；其它值（如 `code`、`ask`）会被持久化并回显，但**不改变**权限姿态 |

### ← 响应

| 字段 | 类型 | 说明 |
|------|------|------|
| `modeId` | `string` | 回显持久化的值 |

### 说明

- 命中预设**要求**会话有 `PermissionEngine`，否则 `-32600` Invalid Request。未知 modeId 不需要引擎——纯 UI 状态。
- 按会话隔离：每个会话拥有自己的引擎，会话 A 的预设变更绝不影响会话 B。

## C.11 `_agentao.cn/ask_user` ⇠ 通知（扩展）

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

## C.12 JSON-RPC 错误码速查

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
