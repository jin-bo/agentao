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
| `_meta["_agentao.cn/extensions"]` | `[{method, description}]` | Vendor methods advertised under `_meta` (ACP's standard extension channel); includes `_agentao.cn/ask_user` |

### `agentCapabilities` block (v0.2.x)

| Field | Value | Meaning |
|-------|-------|---------|
| `loadSession` | `true` | `session/load` supported — see [7.2](/en/part-7/2-ide-plugin#3-persist-resume-across-ide-restart) |
| `promptCapabilities.image` | `true` | 0.4.8+: inline `{data, mimeType}` image blocks; non-vision degradation — see [A.1](/en/appendix/a-api-reference#image-input-and-vision-degradation) |
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
| `configOptions` | `[object]` | Model/provider selection options (`configId: "model"`); drive a switch via `session/set_config_option`. Empty list if the agent can't enumerate a catalog |

### Common failures

| JSON-RPC code | Cause |
|---------------|-------|
| `-32002` | Called before `initialize` (SERVER_NOT_INITIALIZED) |
| `-32602` | `cwd` not absolute / doesn't exist / `mcpServers` malformed |

> **Startup resume.** When the server was launched with `agentao --acp --resume [SESSION_ID]`, the **first** `session/new` resumes a persisted session instead of starting blank: it replays history as `session/update` notifications and returns the persisted `sessionId`. A miss (empty store / unknown id / corrupt file / id already active) silently degrades to a normal fresh session. See [3.2 → Resume a session on startup](/en/part-3/2-agentao-as-server#resume-a-session-on-startup).

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

`{configOptions: [...]}` — the same model/provider selection options as `session/new` (empty list if no catalog). The agent also replays prior turns as `session/update` notifications (reconstructed from persisted history) before it's ready for the next `session/prompt`.

### Fingerprint rule

`mcpServers` is hashed during `session/new`. If `session/load` passes a different set (added / removed / reordered), Agentao may either reject the call or silently re-init — depends on implementation phase. When in doubt, use the **same** list you originally passed.

## C.8 `session/set_config_option`

Switch the model (and optionally the provider) via the ACP-standard config-option mechanism. **Credentials never travel on the wire** — the agent resolves them server-side from a host-injected `provider_resolver` (default: environment).

### → Request

| Field | Type | Req | Notes |
|-------|------|-----|-------|
| `sessionId` | `string` | yes | Active session |
| `configId` | `string` | yes | Must be `"model"`; any other value → `-32600` Invalid Request |
| `value` | `string` | yes | `provider/model` (e.g. `openai/gpt-4o`) or a bare `model` (keeps the current provider). Split on the **first** `/`; provider is lower-cased, model kept as-is |

`apiKey`, `baseUrl`, `_meta`, and any other field are **rejected** (`-32602`; `extra="forbid"` + handler whitelist) — *"credentials resolve server-side and never travel on the wire"*.

### ← Response

| Field | Type | Notes |
|-------|------|-------|
| `configOptions` | `[object]` | Refreshed selection options (same shape as `session/new`), reflecting the new `currentValue` |

### Resolution & errors

- **`provider/model`**: the agent calls `provider_resolver(provider_id)` → `{api_key, base_url?}`, then swaps provider + model. The **default** resolver accepts only the configured `LLM_PROVIDER` (case-insensitive) and reads `{PREFIX}_API_KEY` / `{PREFIX}_BASE_URL`; any other provider → `-32600` `cannot resolve provider '<id>'`. Inject a richer resolver to support more providers.
- **bare `model`**: model-only switch (provider unchanged) via the same path as `_agentao.cn/set_model`.
- On resolver failure the server logs only the provider id + exception **type**, never the message (it could embed a key).

### `ConfigOption` shape (entries in `configOptions`)

| Field | Type | Notes |
|-------|------|-------|
| `id` | `string` | `"model"` |
| `name` | `string` | `"Model"` |
| `category` | `"mode" \| "model" \| "thought_level"` | `"model"` |
| `type` | `"select"` | only `select` in v1 |
| `currentValue` | `string?` | `provider/model` of the live model |
| `options` | `[{value, name?, description?}]` | Catalog choices; `value` is `provider/model`. Default catalog = the single current env model; richer catalog host-injected |

## C.9 `_agentao.cn/set_model` (extension)

Agentao-specific free-form model switch — the vendor sibling of `session/set_config_option`'s bare-value path. Secret-free; shares the same core code path.

### → Request

| Field | Type | Req | Notes |
|-------|------|-----|-------|
| `sessionId` | `string` | yes | Active session |
| `model` | `string` | yes | Any model id the current provider accepts (stripped). Free-form — not validated against a catalog |

`apiKey`, `baseUrl`, `_meta` are **rejected** (`-32602`).

### ← Response

| Field | Type | Notes |
|-------|------|-------|
| `model` | `string` | The active model id after the switch (`agent.llm.model`) |

## C.10 `session/set_mode`

Set the session's ACP `modeId`. The field is the ACP-standard **`modeId`** (not `mode`) and is an **open string** — a UI/behavioural selector that need not map to an Agentao permission preset.

### → Request

| Field | Type | Req | Notes |
|-------|------|-----|-------|
| `sessionId` | `string` | yes | Active session |
| `modeId` | `string` | yes | Non-empty. On an exact match to a permission preset (`read-only` / `workspace-write` / `full-access` / `plan`) the agent applies it; any other value (e.g. `code`, `ask`) is persisted and echoed **without** changing permission posture |

### ← Response

| Field | Type | Notes |
|-------|------|-------|
| `modeId` | `string` | Echoes the persisted value |

### Notes

- A recognized preset **requires** the session to have a `PermissionEngine`, else `-32600` Invalid Request. Unknown modeIds need no engine — they are pure UI state.
- Per-session: each session owns its own engine, so a preset change on session A never affects session B.

## C.11 `_agentao.cn/ask_user` ⇠ notification (extension)

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

## C.12 JSON-RPC error codes (quick reference)

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
