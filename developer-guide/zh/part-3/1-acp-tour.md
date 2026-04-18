# 3.1 ACP 协议速览

**ACP = Agent Client Protocol**——一个标准化的 stdio JSON-RPC 2.0 协议，让任何语言的宿主（"Client"）能够驱动 Agent 运行时（"Server"）。由 Zed Industries 领衔推动，目标与 LSP 在编辑器/语言服务之间的角色类似：**"让 IDE 和 Agent 之间说同一种话"**。

官方规范：<https://agentclientprotocol.com/>

## 与 MCP 的关系

MCP 和 ACP 是**互补**而非竞争：

| 协议 | 方向 | 典型客户端 | 典型服务端 | 角色 |
|------|------|----------|----------|------|
| **ACP** | Host ↔ Agent | IDE、Web UI、CLI | Agent 运行时（如 Agentao） | 把 Agent 暴露给 UI |
| **MCP** | Agent ↔ Tools | Agent 运行时 | 工具/数据源（文件系统、GitHub、数据库…） | 把工具暴露给 Agent |

```
┌────────────┐   ACP    ┌────────────┐   MCP    ┌─────────────┐
│   Client    │◄────────►│   Agent     │◄────────►│ MCP Tools  │
│ (你的宿主)   │           │  (Agentao)  │           │ (资源/API) │
└────────────┘           └────────────┘           └─────────────┘
```

Agentao 同时是 **ACP Server**（被宿主驱动）和 **MCP Client**（驱动外部工具）。

## 为什么选 ACP

| 诉求 | ACP 的满足方式 |
|------|--------------|
| 非 Python 宿主 | stdio + JSON，任意语言都能集成 |
| 进程隔离 | Agent 跑在子进程里，崩溃不影响宿主 |
| 可替换 Agent 实现 | Agentao、Claude Code、Zed's built-in agent 等均符合同一协议 |
| 可审计 | JSON 线上报文天然可被 dump / replay / diff |

## 协议特征

- **传输层**：stdin/stdout（v1 仅此一种）
- **帧格式**：NDJSON——每行一条完整 JSON 对象，`\n` 分隔
- **RPC 规范**：JSON-RPC 2.0
- **连接模型**：单客户端 ↔ 单服务端，长连接
- **协议版本**：整数 `ACP_PROTOCOL_VERSION = 1`（严格类型，不是日期字符串）
- **能力协商**：`initialize` 握手时双方宣告各自支持的特性

## 消息四象限

```
         Request (有 id)                Notification (无 id)
        ─────────────────────────── ────────────────────────────
Client  initialize, session/new,      (v1 未定义)
 →      session/prompt, session/cancel,
Server  session/load
        ─────────────────────────── ────────────────────────────
Server  session/request_permission,   session/update
 →      _agentao.cn/ask_user           (流式文本、工具事件、思考…)
Client
```

**关键点**：
- Client → Server 是**主动驱动**（发起会话、发送提示、取消）
- Server → Client 既发**通知**（连续的流式更新），也发**请求**（要求用户批准某个工具）
- 所有方向都在**同一对 stdio**上多路复用，靠 JSON-RPC 的 `id` 字段区分请求/响应

## 典型一次完整交互

```
Client                                              Server
  │                                                   │
  │  → {"jsonrpc":"2.0","id":1,"method":"initialize",  │
  │     "params":{"protocolVersion":1,...}}           │
  │                                                   │
  │  ← {"jsonrpc":"2.0","id":1,"result":              │
  │     {"protocolVersion":1,"agentCapabilities":{...}│
  │     ,"agentInfo":{"name":"agentao",...}}}        │
  │                                                   │
  │  → session/new {cwd, mcpServers?}                 │
  │  ← {sessionId}                                    │
  │                                                   │
  │  → session/prompt {sessionId, prompt:[...]}       │
  │                                                   │
  │  ← session/update {stream: thinking}              │
  │  ← session/update {stream: text chunk}            │
  │  ← session/update {stream: tool_call started}     │
  │  → session/request_permission {id, tool, args}   │
  │  ← (client responds: {granted:true})             │
  │  ← session/update {stream: tool_call completed}  │
  │  ← session/update {stream: text chunk}            │
  │                                                   │
  │  ← {jsonrpc, id:<prompt_id>, result:{stopReason}}│
  │                                                   │
  │  → session/cancel (可选，中途取消)                  │
  │  → session/new ...（下一段对话）                    │
```

## 扩展：`_agentao.cn/ask_user`

协议标准里 Server 只能**请求权限**而不能**向用户追问文本**。Agentao 通过 `extensions` 机制宣告了一个私有扩展方法 `_agentao.cn/ask_user`，用来从 Server 向 Client 反问任意问题。Client 可以：

- 实现它：把问题弹给用户、拿到答复后返回字符串
- 不实现：Agent 会 fallback 到 `"[ask_user: not available in non-interactive mode]"`

## ACP v1 的边界

v1 协议的**明确限制**（Agentao 能力字段如实反映）：

- `promptCapabilities.image = false`、`audio = false`、`embeddedContext = false`——提示体仅纯文本
- `mcpCapabilities.http = false`、`sse = true`——MCP 仅支持 stdio + SSE
- `authMethods = []`——协议层不做认证；凭据走环境变量

未来版本会扩展这些能力。Client 代码应**检查握手响应**再决定传什么格式的提示。

## 下一步

- **3.2** 手把手用 Agentao 当 ACP Server，含完整消息线
- **3.3** 构建宿主 ACP Client 的骨架
- **3.4** 反向：Agentao 调用其他 ACP Server
- **3.5** Zed 真机集成

→ [3.2 Agentao 作为 ACP Server](./2-agentao-as-server)
