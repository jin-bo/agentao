# 附录 C · ACP 消息字段参考

Agentao 的 ACP 服务器/客户端发出的每种消息的字段级速查。端到端叙事（握手 → 提示 → 流式 → 取消）见 [Part 3](/zh/part-3/)。本附录是你在调试畸形消息时打开的那一页。

**约定**：
- `→` 请求；`←` 响应；`⇠` 服务器发给客户端（通知，或客户端必须应答的请求）
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
| `authMethods` | `[object]` | client 声明 Terminal Auth（`clientCapabilities.auth.terminal` 或旧式 `_meta["terminal-auth"]`，布尔 `true`）时为一个 `{id: "agentao-login", type: "terminal", args: ["--login"], …}`；否则为 `[]`。Agentao 没有 `authenticate` handler——登录在独立进程中完成（0.5.8+） |
| `agentInfo` | `object` | `{name: "agentao", title: "Agentao", version}` |
| `_meta["_agentao.cn/extensions"]` | `[{method, description}]` | 扩展方法，放在 `_meta` 下（ACP 标准的扩展通道）；含 `_agentao.cn/ask_user` |

### `agentCapabilities` 字段（v0.2.x）

| 字段 | 值 | 含义 |
|------|-----|------|
| `loadSession` | `true` | 支持 `session/load`——见 [7.2](/zh/part-7/2-ide-plugin#_3-·-ide-重启后的会话恢复) |
| `promptCapabilities.image` | `true` | 0.4.8+：内联 `{data, mimeType}` 图片块；非视觉模型的退化行为见 [A.1](/zh/appendix/a-api-reference#图片输入与视觉退化) |
| `promptCapabilities.audio` | `false` | |
| `promptCapabilities.embeddedContext` | `false` | |
| `mcpCapabilities.sse` | `true` | 可用 SSE 传输（legacy） |
| `mcpCapabilities.http` | `true` | 可用 HTTP（Streamable HTTP）传输 |

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
| `type` | `"stdio"` / `"http"` / `"sse"`，或省略 | 省略即 `"stdio"`，需要 `command`。带 `url` 的条目必须写 `"http"` 或 `"sse"`：和 `mcp.json` 不同，这里裸 `url` 不代表 HTTP。其他值 → `-32602` |
| `command` | `string` | 仅 stdio |
| `args` | `[string]` | 仅 stdio |
| `env` | `[{name, value}]` | 仅 stdio——注意**是键值对对象数组**，不是 map |
| `url` | `string` | 仅 sse / http |
| `headers` | `[{name, value}]` | 仅 sse / http |

每个条目都带 `trust: false` 和 `oauth: false`，会替换 `.agentao/mcp.json` 中的同名条目，且只在本会话存活期间有效。见 [3.2 → 客户端注入的 MCP 服务器](/zh/part-3/2-agentao-as-server#客户端注入的-mcp-服务器)。

### ← 响应

| 字段 | 类型 | 说明 |
|------|------|------|
| `sessionId` | `string` | 不透明 id，目前是 `sess_` + 32 位十六进制。收好——后续每个调用都要用 |
| `configOptions` | `[object]` | 模型/提供方选择项（`configId: "model"`）；通过 `session/set_config_option` 触发切换。无法枚举目录时为空数组 |

### 常见失败

| JSON-RPC code | 原因 |
|---------------|------|
| `-32002` | 在 `initialize` 之前调（SERVER_NOT_INITIALIZED） |
| `-32602` | `cwd` 不是绝对/不存在，或 `mcpServers` 缺失或格式错 |
| `-32000` | 没有配置 LLM 提供方（`auth_required`） |
| `-32603` | `~/.agentao/llm.json` 或项目 `.env` 不可用（「LLM configuration error」） |

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
| `type` | `"text"` \| `"resource_link"` \| `"image"` | 其他类型以 `-32602` 拒绝 |
| `text` | `string` | `text` 块：用户消息 |
| `uri`、`name`? | `string` | `resource_link` 块：作为引用写进提示词 |
| `data`、`mimeType` | `string` | `image` 块：base64 字节与图片类型 |

- 多个 `text` 块以空行拼接。`resource_link` 渲染为指向其 URI 的 Markdown 链接，链接文字是 `Resource: <title、name 或 uri>`。Agentao 不会去取这个 URI。
- `image` 必须内联：`mimeType` 是 `image/*`，`data` 是单图大小上限内的 base64，字节是 PNG、JPEG、GIF 或 WEBP，一个 prompt 最多 16 张。`annotations` 和 `_meta` 接受但忽略。带 `uri` 或其他键 → `-32602`。标签错了（JPEG 字节标成 `image/png`）会被纠正，并在 `agentao.log` 里记一条警告。不支持视觉的模型会改收文本标签——见 [A.1](/zh/appendix/a-api-reference#图片输入与视觉退化)。

### ← 响应

| 字段 | 类型 | 说明 |
|------|------|------|
| `stopReason` | `string` | ACP v1 五个取值之一：`"end_turn"`、`"max_tokens"`、`"max_turn_requests"`、`"cancelled"`、`"refusal"`（从不发出）。模型调用失败的那一轮改为返回 JSON-RPC 错误——`-32603`，`data: {"reason": "llm_error"}`。 |

## C.4 `session/update` ⇠ 通知

服务器到客户端在一轮内的流式更新。

### Params

| 字段 | 类型 | 说明 |
|------|------|------|
| `sessionId` | `string` | 活跃会话 |
| `update` | `object` | 按 `sessionUpdate` 分支 |
| `update.schema_version` | `int` | Agentao 运行时载荷版本（`1`），只出现在实时更新上。与 `protocolVersion` 无关 |

### 变体（`update.sessionUpdate`）

| 值 | 额外字段 |
|----|----------|
| `user_message_chunk` | `content: {type:"text", text}`（仅回放） |
| `agent_message_chunk` | `content: {type:"text", text}` |
| `agent_thought_chunk` | `content: {type:"text", text}` |
| `tool_call` | `toolCallId`、`title`、`kind`、`status:"pending"`、`rawInput`；文件编辑另带一个只含 `diff` 条目的 `content[]` |
| `tool_call_update` | `toolCallId`、`status`，可选 `title`，可选 `content[]` —— 它**替换**整个集合，所以每条更新都要把集合整个重述一遍 |
| `plan` | `entries: [{content, priority:"medium", status}]`——每次都是完整清单 |

### 事件映射

运行时事件到线上的实时映射，见 [`agentao/acp/transport.py`](https://github.com/jin-bo/agentao/blob/main/agentao/acp/transport.py)。映射内部出错只记日志并吞掉，绝不打断这一轮。

| Agentao 事件 | `sessionUpdate` | 说明 |
|--------------|-----------------|------|
| `TURN_START` | — | 不发送 |
| `LLM_TEXT` | `agent_message_chunk` | 一个文本 chunk |
| `THINKING` | `agent_thought_chunk` | 形状相同，便于客户端把推理单独渲染 |
| `TOOL_CONFIRMATION` | `tool_call` | 在 `session/request_permission` 之前先打开这次调用；请求里的 `toolCall.toolCallId` 就是同一个 id。没有 call id 时不发送 |
| `TOOL_START` | `tool_call` | 若该调用已由确认打开，则改为同一 id 下的 `tool_call_update`：重述 `title` 和 `content`，不带 `status`，所以被拒绝的调用在完成前一直是 `pending` |
| `TOOL_OUTPUT` | `tool_call_update` | `status:"in_progress"`，把目前为止的全部输出作为一条文本条目。有节流——见下文*流式工具输出* |
| `TOOL_COMPLETE` | `tool_call_update` | `ok` → `status:"completed"`；`error` 或 `cancelled` → `"failed"`。调用有过流式输出或有错误行时重述 `content`；否则省略，开头的 `diff` 保持不动 |
| `todo_write`（合法清单） | `plan` | 在 `TOOL_COMPLETE` 时发送，且只在调用生效时发送；不会打开 `tool_call`。清单格式不对时退回普通 `tool_call` |
| `AGENT_START` | `agent_thought_chunk` | `[sub-agent started: <name>] <task>` |
| `AGENT_END` | `agent_thought_chunk` | `[sub-agent finished: <name> (<state>, <N> turns)]`；轮数未知时省略 `, <N> turns` |
| `PLUGIN_HOOK_FIRED`（用户通知） | `agent_message_chunk` | 每条钩子通知一行，以 `⚠ ` 开头 |
| `ERROR` | `agent_message_chunk` | `Error: <message>`，有 detail 时再加 ` — <detail>` |

### 文件编辑以 diff 送达

`replace` 和追加模式的 `write_file` 打开 `tool_call` 时带一个 `diff` 条目；对应的 `session/request_permission` 也以同一条目开头，让用户批准的是一份看得懂的改动：

```json
{"sessionUpdate":"tool_call","toolCallId":"c1","title":"replace","kind":"edit","status":"pending",
 "rawInput":{"file_path":"src/config.py","old_text":"DEBUG = False","new_text":"DEBUG = True"},
 "content":[{"type":"diff","path":"/home/u/proj/src/config.py","oldText":"DEBUG = False","newText":"DEBUG = True"}]}
```

- `diff` 是**一个 hunk，不是整份文件**。纯追加时 `oldText` 为 `null`。会话 `cwd` 已知时 `path` 是绝对路径。
- 它展示的是**请求**的改动。`replace` 对空白和排版做宽松匹配，`replace_all` 可能命中多处，所以实际改动的文本可能不同。是否生效看终态更新。
- **非追加的 `write_file` 不带 diff。** 它的参数说明不了文件是否已存在，`oldText: null` 可能把覆盖显示成新建。
- 不发送 `locations`（跟随高亮）。

### 流式工具输出

只有 `run_shell_command` 会流式输出。ACP 每次更新都**替换**工具调用的 `content`，所以每条更新都要重述目前为止的全部输出。有两个上限，都在 [`agentao/acp/_tool_call_content.py`](https://github.com/jin-bo/agentao/blob/main/agentao/acp/_tool_call_content.py)：

| 上限 | 值 | 效果 |
|------|-----|------|
| `FLUSH_CHARS` | 4 000 | 第一个 chunk 总会发出（它把 `pending` 变成 `in_progress`）。之后每再积累 4 000 字符才发一次。暂缓的文本跟着下一次更新或终态更新发出 |
| `MAX_CHARS` / `HEAD_CHARS` | 16 000 / 4 000 | 客户端那份保留开头 4 000 和结尾 12 000 字符，中间用 `[… N characters elided …]` 隔开 |

- 上限**只作用于客户端那份**。模型仍拿到完整结果（最多 80 000 字符，或 `.agentao/tool-outputs/` 下的文件），回放也会完整记录。
- 调用完成之后才到的 chunk 会被丢弃，绝不会重新打开这次调用。

### `tool_call.kind`（封闭枚举）

`read`、`edit`、`delete`、`move`、`search`、`execute`、`think`、`fetch`、`switch_mode`、`other`。Agentao 会把内部工具名映射到这枚举——`agentao/acp/_transport_helpers.py` 里的 `_TOOL_KIND_MAP`，有测试保证它对 `BUILTIN_TOOL_NAMES` 穷尽。表里没有的工具（宿主注入的、所有 `mcp_*`）落到 `other`，这也是 ACP v1 自己的默认值。Agentao 目前不会产出 `delete`、`move`、`switch_mode`。

### `content[]` 条目（`ToolCallContent`）

| `type` | 形状 | 何时 |
|--------|------|------|
| `content` | `{content: {type:"text", text}}` | 确认对话框里的工具描述；流式输出（每次整体重述）；失败时末尾的 `Error: …` 行；回放的结果 |
| `diff` | `{path, oldText?, newText}` | 文件编辑，发在 `status:"pending"`——是一个 hunk 而非整份文件；没有被替换的内容时 `oldText` 为 `null` |

ACP v1 还有第三种 `terminal`。Agentao 从不发它：它不调用 `terminal/create`（G1 的 fs/terminal 代理是明确的非目标）。

### `tool_call_update.status`

| 值 | 时机 |
|----|------|
| `in_progress` | 输出流式片段 |
| `completed` | 工具成功（回放的调用也一律是它） |
| `failed` | 工具抛错、返回错误或被取消。ACP 的工具调用没有 cancelled 状态 |

## C.5 `session/request_permission` ⇠ 请求

服务器请客户端确认工具调用。

### Params

| 字段 | 类型 | 说明 |
|------|------|------|
| `sessionId` | `string` | |
| `toolCall` | `object` | 形状同 `session/update.tool_call`，id 就是那条 `tool_call` 打开时用的 id。文件编辑时 `content[]` 先放 `diff`，再放工具描述 |
| `options` | `[{optionId, name, kind}]` | `allow_once`、`allow_always`、`reject_once`、`reject_always`（`optionId` 与 `kind` 相同）。MCP Skills 闸门发起的确认只给两个 `*_once` 选项 |

### 期望响应（客户端 → 服务器）

| 字段 | 类型 | 说明 |
|------|------|------|
| `outcome.outcome` | `"selected"` 或 `"cancelled"` | |
| `outcome.optionId` | `string` | 当 `selected` 时必填 |

**没有超时**：server 一直等到客户端应答或连接关闭。`session/cancel` 不会替你应答挂起的请求；取消之后，请按 ACP 规范对每个挂起的请求回复 `{"outcome":"cancelled"}`。连接关闭、JSON-RPC 错误应答、格式不对的结果以及 `cancelled`，都按拒绝该工具处理。

**记住的应答**：`allow_always` / `reject_always` 在该会话内按工具名保存，之后的调用不再发请求。MCP Skills 闸门发起的确认会忽略已保存的 `allow_always`，也不保存任何应答。

## C.6 `session/cancel`

中止当前轮。挂起的 `session/request_permission` 和 `_agentao.cn/ask_user` 请求会一直保持，直到客户端应答。

### → 请求

| 字段 | 类型 | 说明 |
|------|------|------|
| `sessionId` | `string` | |

### ← 响应

ACP 把 `session/cancel` 定义为 notification（无 `id`），所以服务器不回复。客户端若以 request 形式发送（带 `id`），会收到 `"result": null`。进行中的 `session/prompt` 最终以 `stopReason: "cancelled"` 解决。

没有进行中的轮次、这一轮已取消过、会话已关闭：静默无操作。未知 `sessionId`：以 request 发送时返回 `-32600`，以通知发送时直接丢弃。

## C.7 `session/load`

按 id 恢复会话。仅当 agent 在能力里声明 `loadSession: true` 时可用。

### → 请求

| 字段 | 类型 | 说明 |
|------|------|------|
| `sessionId` | `string` | 已保存会话的 id，且不能在本连接上仍存活 |
| `cwd` | `string` | 绝对路径。会话从 `<cwd>/.agentao/sessions/` 读取 |
| `mcpServers` | `[object]` | 必填（`[]` 可接受），形状同 `session/new`。为加载出的会话重新挂接 |

### ← 响应

`{configOptions: [...]}`——与 `session/new` 相同的模型/提供方选择项（无目录时为空数组）。Agent 还会通过 `session/update` 通知回放历史轮次（从持久化历史重建），之后才进入可接受下一轮 `session/prompt` 的状态。

### 常见失败

| JSON-RPC code | 原因 |
|---------------|------|
| `-32600` | 未知 id、会话文件无法读取或已损坏，或该 id 在本连接上仍存活 |
| `-32000` | 没有配置 LLM 提供方（`auth_required`） |

### 历史回放

应答之前，server 逐条遍历已保存的消息，每条发出一条或多条通知。等待响应的 ACP 客户端因此会在下一轮之前看到完整历史。

| 保存的消息 | `sessionUpdate` |
|------------|-----------------|
| `system` | —（跳过） |
| `user` | `user_message_chunk`，去掉其中的 `<system-reminder>` 块 |
| `assistant` 文本 | `agent_message_chunk` |
| `assistant` 的 `tool_calls` | 每个调用一条 `tool_call`，`status:"completed"`；文件编辑带上与实时一致的 `diff` |
| `assistant` 的 `todo_write` 调用 | `plan`；它对应的 `tool` 结果跳过 |
| `tool` 结果 | `tool_call_update`，`status:"completed"`，结果文本——若该调用有 `diff`，排在 `diff` 之后 |

保存的消息里没有成功或失败的信息，所以失败或被拒绝的调用回放时同样是 `completed`。

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

- **`provider/model`**：Agentao 调 `provider_resolver(provider_id)` → `{api_key, base_url?}`，然后整套切换 provider + model。**默认** resolver 只接受配置的 `LLM_PROVIDER`（大小写不敏感），读 `{PREFIX}_API_KEY` / `{PREFIX}_BASE_URL`；其它 provider → `-32600` `cannot resolve provider '<id>'`。要支持更多提供方，注入更丰富的 resolver。若 resolver 名下的 provider 说的是不同的**线路协议**（0.5.0：`openai-completions` / `anthropic-messages`），就在返回值里加一个可选的 `api_format`，切换时会话的线路随之改变（清掉的状态与切换模型或端点时相同）。未知或尚未实现的值会在任何切换发生之前以 `-32600` 被拒绝。不带这个键的 resolver 得到的是**默认**协议 `openai-completions` —— 与未设置 `{PROVIDER}_API_FORMAT` 同义，而不是「会话当前的协议」：只给 Anthropic provider 标了协议的 resolver 必须还能切回来。默认 resolver 会返回该块自己的 `{PREFIX}_API_FORMAT`。
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

## C.11 `_agentao.cn/ask_user` ⇠ 请求（扩展）

Agentao 独有，服务器向用户问一个自由文本问题。

### Params

| 字段 | 类型 | 说明 |
|------|------|------|
| `sessionId` | `string` | |
| `question` | `string` | 自由文本 |

### 期望响应

| 字段 | 类型 | 说明 |
|------|------|------|
| `outcome` | `"answered"` \| `"cancelled"` | 用户作答为 `"answered"`，关闭提问为 `"cancelled"` |
| `text` | `string` | `"answered"` 时：用户的回答，一个字符串 |

其他形状一律拒绝（`extra="forbid"`）。取消、`text` 为空、错误响应或格式不对的结果，模型收到的都是哨兵 `"(user unavailable)"`（常量 `ASK_USER_UNAVAILABLE_SENTINEL`）。

## C.12 JSON-RPC 错误码速查

| 代码 | 名 | 含义 |
|------|-----|------|
| `-32700` | Parse error | 服务器收到非法 JSON |
| `-32600` | Invalid Request | JSON 合法但不是 JSON-RPC Request |
| `-32601` | Method not found | 方法不存在或不可用 |
| `-32602` | Invalid params | 方法参数非法 |
| `-32603` | Internal error | 内部 JSON-RPC 错 |
| `-32002` | Server not initialized | `initialize` 之前调了 session 方法 |
| `-32000` | Auth required | 未配置 LLM provider 时调用 `session/new` / `session/load`（0.5.8+）；执行广告的终端登录后重连 |

对照 `AcpErrorCode` 见 [附录 D.2](./d-error-codes#d-2-json-rpc-数值码-vs-acperrorcode)。

---

→ [附录 D · 错误码](./d-error-codes) · [附录 E · 迁移](./e-migration)
