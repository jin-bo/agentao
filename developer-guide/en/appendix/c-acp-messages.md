# Appendix C · ACP Message Fields Reference

Field-level lookup for every message Agentao's ACP server and client emit. For the end-to-end story (handshake → prompt → streaming → cancel) see [Part 3](/en/part-3/). This appendix is the speed-lookup you open when you're in the middle of debugging a malformed message.

**Conventions**:
- `→` = request; `←` = response; `⇠` = server-to-client notification
- All messages are JSON-RPC 2.0 over NDJSON on stdio
- `{…}` = object; `[…]` = array; `T|U` = union; `?` = optional

## C.1 `initialize`

Handshake. MUST be first call.

### → Request

| Field | Type | Req | Notes |
|-------|------|-----|-------|
| `protocolVersion` | `int` | yes | Agentao speaks `1`. `bool` rejected explicitly. |
| `clientCapabilities` | `object` | yes | Client-side feature flags (e.g. `fs: {readFile, writeFile}`) |
| `clientInfo` | `object` | no | `{name, version, title}` — purely informational |

### ← Response

| Field | Type | Notes |
|-------|------|-------|
| `protocolVersion` | `int` | Negotiated — usually echoes request; never errors on mismatch |
| `agentCapabilities` | `object` | See below |
| `authMethods` | `[]` | Empty in v1 — Agentao does no ACP-level auth |
| `agentInfo` | `object` | `{name: "agentao", title: "Agentao", version}` |
| `extensions` | `[{method, description}]` | Vendor methods; includes `_agentao.cn/ask_user` |

### `agentCapabilities` block (v0.2.x)

| Field | Value | Meaning |
|-------|-------|---------|
| `loadSession` | `true` | `session/load` supported — see [7.2](/en/part-7/2-ide-plugin#3-persist-resume-across-ide-restart) |
| `promptCapabilities.image` | `false` | v1 baseline text-only |
| `promptCapabilities.audio` | `false` | |
| `promptCapabilities.embeddedContext` | `false` | |
| `mcpCapabilities.sse` | `true` | SSE MCP transport usable |
| `mcpCapabilities.http` | `false` | HTTP MCP transport NOT supported |

## C.2 `session/new`

Create a fresh session.

### → Request

| Field | Type | Req | Notes |
|-------|------|-----|-------|
| `cwd` | `string` | yes | **Absolute** path, must exist and be a directory |
| `mcpServers` | `[object]` | yes (`[]` ok) | Per-session MCP server configs |

### `mcpServers[i]` shape

| Field | Type | Notes |
|-------|------|-------|
| `name` | `string` | Non-empty |
| `type` | `"stdio"` / `"http"` / `"sse"` | `http` advertised by some clients but rejected by agent per `mcpCapabilities` |
| `command` | `string` | stdio only |
| `args` | `[string]` | stdio only |
| `env` | `[{name, value}]` | stdio only — note **list of name/value objects**, not a map |
| `url` | `string` | sse / http only |
| `headers` | `[{name, value}]` | sse / http only |

### ← Response

| Field | Type | Notes |
|-------|------|-------|
| `sessionId` | `string` | UUID. Keep it — needed for every subsequent call |

### Common failures

| JSON-RPC code | Cause |
|---------------|-------|
| `-32002` | Called before `initialize` (SERVER_NOT_INITIALIZED) |
| `-32602` | `cwd` not absolute / doesn't exist / `mcpServers` malformed |

## C.3 `session/prompt`

Run one user turn. Returns when the agent stops.

### → Request

| Field | Type | Req | Notes |
|-------|------|-----|-------|
| `sessionId` | `string` | yes | From `session/new` or `session/load` |
| `prompt` | `[ContentBlock]` | yes | See below |

### `ContentBlock` shape

| Field | Type | Notes |
|-------|------|-------|
| `type` | `"text"` | Only text supported in v1 baseline |
| `text` | `string` | The user message |

### ← Response

| Field | Type | Notes |
|-------|------|-------|
| `stopReason` | `string` | `"end_turn"`, `"max_tokens"`, `"cancelled"`, or other |

## C.4 `session/update` ⇠ notification

Server-to-client streaming updates during a prompt turn.

### Params

| Field | Type | Notes |
|-------|------|-------|
| `sessionId` | `string` | The active session |
| `update` | `object` | Variants keyed by `sessionUpdate` |

### Variants (`update.sessionUpdate`)

| Value | Agentao event | Extra fields |
|-------|---------------|--------------|
| `user_message_chunk` | (replay) | `content: {type:"text", text}` |
| `agent_message_chunk` | `LLM_TEXT` | `content: {type:"text", text}` |
| `agent_thought_chunk` | `THINKING` / `ERROR` | `content: {type:"text", text}` |
| `tool_call` | `TOOL_START` | `toolCallId`, `title`, `kind`, `status:"pending"`, `rawInput` |
| `tool_call_update` | `TOOL_OUTPUT` / `TOOL_COMPLETE` | `toolCallId`, `status`, optional `content[]` append |

### `tool_call.kind` (closed enum)

`read`, `edit`, `delete`, `move`, `search`, `execute`, `think`, `fetch`, `switch_mode`, `other`. Agentao maps its tool names into this enum — see `agentao/acp/transport.py`.

### `tool_call_update.status`

| Value | When |
|-------|------|
| `in_progress` | Streaming output chunk |
| `completed` | Tool succeeded |
| `failed` | Tool raised / returned error |

## C.5 `session/request_permission` ⇠ notification

Server asks client to confirm a tool call.

### Params

| Field | Type | Notes |
|-------|------|-------|
| `sessionId` | `string` | |
| `toolCall` | `object` | Same shape as `session/update.tool_call` |
| `options` | `[{optionId, name, kind}]` | Typically `["allow", "allow_always", "reject"]` |

### Expected response (client → server)

| Field | Type | Notes |
|-------|------|-------|
| `outcome.outcome` | `"selected"` or `"cancelled"` | |
| `outcome.optionId` | `string` | Required when `selected` |

**Cancellation rule**: if the client never answers and the turn is cancelled, the agent resolves all pending permission requests with `cancelled`. See [7.2 pitfall](/en/part-7/2-ide-plugin#pitfalls).

## C.6 `session/cancel`

Abort the current turn and all pending permission requests.

### → Request

| Field | Type | Notes |
|-------|------|-------|
| `sessionId` | `string` | |

### ← Response

Empty object `{}`. The ongoing `session/prompt` eventually resolves with `stopReason: "cancelled"`.

## C.7 `session/load`

Resume a session by id. Only usable when the agent advertises `loadSession: true` in its capabilities.

### → Request

| Field | Type | Notes |
|-------|------|-------|
| `sessionId` | `string` | Must have been created earlier |
| `cwd` | `string` | Absolute path; must match the session's original cwd |
| `mcpServers` | `[object]` | Re-declare — fingerprint must match prior `session/new` |

### ← Response

Empty object. The agent replays prior turns as `session/update` notifications (reconstructed from persisted history) before it's ready for the next `session/prompt`.

### Fingerprint rule

`mcpServers` is hashed during `session/new`. If `session/load` passes a different set (added / removed / reordered), Agentao may either reject the call or silently re-init — depends on implementation phase. When in doubt, use the **same** list you originally passed.

## C.8 `_agentao.cn/ask_user` ⇠ notification (extension)

Agentao-specific. Server asks the user a free-form question.

### Params

| Field | Type | Notes |
|-------|------|-------|
| `sessionId` | `string` | |
| `question` | `string` | Free-form text |

### Expected response

| Field | Type | Notes |
|-------|------|-------|
| `answer` | `string` | Free-form user reply |

If the user is unavailable, clients may return the sentinel `"(user unavailable)"` (constant `ASK_USER_UNAVAILABLE_SENTINEL`).

## C.9 JSON-RPC error codes (quick reference)

| Code | Name | Meaning |
|------|------|---------|
| `-32700` | Parse error | Invalid JSON received by server |
| `-32600` | Invalid Request | JSON is valid but not a JSON-RPC Request |
| `-32601` | Method not found | The method doesn't exist / isn't available |
| `-32602` | Invalid params | Invalid method parameter(s) |
| `-32603` | Internal error | Internal JSON-RPC error |
| `-32002` | Server not initialized | Session method called before `initialize` |

Map these to `AcpErrorCode` per [Appendix D.2](./d-error-codes#d-2-json-rpc-numeric-codes-vs-acperrorcode).

---

→ [Appendix D · Error codes](./d-error-codes) · [Appendix E · Migration](./e-migration)
