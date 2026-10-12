# Appendix C · ACP Message Fields Reference

Field-level lookup for every message Agentao's ACP server and client emit. For the end-to-end story (handshake → prompt → streaming → cancel) see [Part 3](/en/part-3/). This appendix is the speed-lookup you open when you're in the middle of debugging a malformed message.

**Conventions**:
- `→` = request; `←` = response; `⇠` = sent by the server to the client (a notification, or a request the client must answer)
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
| `authMethods` | `[object]` | One `{id: "agentao-login", type: "terminal", args: ["--login"], …}` when the client declared Terminal Auth (`clientCapabilities.auth.terminal` or legacy `_meta["terminal-auth"]`, boolean `true`); otherwise `[]`. Agentao has no `authenticate` handler — the login runs as a separate process (0.5.8+) |
| `agentInfo` | `object` | `{name: "agentao", title: "Agentao", version}` |
| `_meta["_agentao.cn/extensions"]` | `[{method, description}]` | Vendor methods advertised under `_meta` (ACP's standard extension channel); includes `_agentao.cn/ask_user` |

### `agentCapabilities` block (v0.2.x)

| Field | Value | Meaning |
|-------|-------|---------|
| `loadSession` | `true` | `session/load` supported — see [7.2](/en/part-7/2-ide-plugin#_3-·-persist-resume-across-ide-restart) |
| `promptCapabilities.image` | `true` | 0.4.8+: inline `{data, mimeType}` image blocks; non-vision degradation — see [A.1](/en/appendix/a-api-reference#image-input-and-vision-degradation) |
| `promptCapabilities.audio` | `false` | |
| `promptCapabilities.embeddedContext` | `false` | |
| `mcpCapabilities.sse` | `true` | SSE MCP transport usable (legacy) |
| `mcpCapabilities.http` | `true` | HTTP (Streamable HTTP) MCP transport usable |

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
| `type` | `"stdio"` / `"http"` / `"sse"`, or omitted | Omitted means `"stdio"`, which needs `command`. A `url` entry must say `"http"` or `"sse"`: unlike `mcp.json`, a bare `url` is not HTTP here. Any other value → `-32602` |
| `command` | `string` | stdio only |
| `args` | `[string]` | stdio only |
| `env` | `[{name, value}]` | stdio only — note **list of name/value objects**, not a map |
| `url` | `string` | sse / http only |
| `headers` | `[{name, value}]` | sse / http only |

Every entry gets `trust: false` and `oauth: false`, replaces a same-named `.agentao/mcp.json` entry, and lives only as long as the session. See [3.2 → MCP servers from the client](/en/part-3/2-agentao-as-server#mcp-servers-from-the-client).

### ← Response

| Field | Type | Notes |
|-------|------|-------|
| `sessionId` | `string` | Opaque id, currently `sess_` + 32 hex characters. Keep it — needed for every subsequent call |
| `configOptions` | `[object]` | Model/provider selection options (`configId: "model"`); drive a switch via `session/set_config_option`. Empty list if the agent can't enumerate a catalog |

### Common failures

| JSON-RPC code | Cause |
|---------------|-------|
| `-32002` | Called before `initialize` (SERVER_NOT_INITIALIZED) |
| `-32602` | `cwd` not absolute / doesn't exist / `mcpServers` missing or malformed |
| `-32000` | No LLM provider configured (`auth_required`) |
| `-32603` | `~/.agentao/llm.json` or the project `.env` is unusable ("LLM configuration error") |

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
| `type` | `"text"` \| `"resource_link"` \| `"image"` | Any other type is rejected with `-32602` |
| `text` | `string` | For `text`: the user message |
| `uri`, `name`? | `string` | For `resource_link`: written into the prompt as a reference |
| `data`, `mimeType` | `string` | For `image`: base64 bytes and the image type |

- Several `text` blocks are joined with a blank line. A `resource_link` becomes a Markdown link to its URI, with the text `Resource: <title, name or uri>`. Agentao does not fetch the URI.
- An `image` must be inline: `mimeType` is `image/*`, `data` is base64 within the per-image size cap, the bytes are PNG, JPEG, GIF or WEBP, and a prompt has at most 16 images. `annotations` and `_meta` are accepted and ignored. A `uri` or any other key → `-32602`. A wrong label (`image/png` over JPEG bytes) is corrected, with a warning in `agentao.log`. A model without vision gets text tags instead — see [A.1](/en/appendix/a-api-reference#image-input-and-vision-degradation).

### ← Response

| Field | Type | Notes |
|-------|------|-------|
| `stopReason` | `string` | One of ACP v1's five: `"end_turn"`, `"max_tokens"`, `"max_turn_requests"`, `"cancelled"`, `"refusal"` (never emitted). A turn whose model call failed returns a JSON-RPC error instead — `-32603`, `data: {"reason": "llm_error"}`. |

## C.4 `session/update` ⇠ notification

Server-to-client streaming updates during a prompt turn.

### Params

| Field | Type | Notes |
|-------|------|-------|
| `sessionId` | `string` | The active session |
| `update` | `object` | Variants keyed by `sessionUpdate` |
| `update.schema_version` | `int` | Agentao's runtime-payload version (`1`), on live updates only. Independent of `protocolVersion` |

### Variants (`update.sessionUpdate`)

| Value | Extra fields |
|-------|--------------|
| `user_message_chunk` | `content: {type:"text", text}` (replay only) |
| `agent_message_chunk` | `content: {type:"text", text}` |
| `agent_thought_chunk` | `content: {type:"text", text}` |
| `tool_call` | `toolCallId`, `title`, `kind`, `status:"pending"`, `rawInput`, and for a file edit a `content[]` holding one `diff` entry |
| `tool_call_update` | `toolCallId`, `status`, optional `title`, optional `content[]` — **replaces** the collection, so each update restates it whole |
| `plan` | `entries: [{content, priority:"medium", status}]` — the whole checklist each time |

### Event mapping

The live mapping from runtime events, in [`agentao/acp/transport.py`](https://github.com/jin-bo/agentao/blob/main/agentao/acp/transport.py). A failure inside this mapping is logged and swallowed. It never interrupts the turn.

| Agentao event | `sessionUpdate` | Notes |
|---------------|-----------------|-------|
| `TURN_START` | — | Not sent |
| `LLM_TEXT` | `agent_message_chunk` | One text chunk |
| `THINKING` | `agent_thought_chunk` | Same shape, so clients can render reasoning apart |
| `TOOL_CONFIRMATION` | `tool_call` | Opens the call just before `session/request_permission`. The request's `toolCall.toolCallId` is the same id. Not sent without a call id |
| `TOOL_START` | `tool_call` | For a call a confirmation already opened: a `tool_call_update` under the same id instead. It restates `title` and `content` and has no `status`, so a rejected call stays `pending` until it completes |
| `TOOL_OUTPUT` | `tool_call_update` | `status:"in_progress"`, the whole output so far as one text entry. Throttled — see *Streamed tool output* |
| `TOOL_COMPLETE` | `tool_call_update` | `status:"completed"` for `ok`, `"failed"` for `error` or `cancelled`. `content` is restated when the call streamed or has an error line. Otherwise it is left out, so an opening `diff` stays |
| `todo_write` (valid checklist) | `plan` | Sent at `TOOL_COMPLETE`, only when the call applied. No `tool_call` is opened. A malformed checklist falls back to a normal `tool_call` |
| `AGENT_START` | `agent_thought_chunk` | `[sub-agent started: <name>] <task>` |
| `AGENT_END` | `agent_thought_chunk` | `[sub-agent finished: <name> (<state>, <N> turns)]`. `, <N> turns` is left out when the count is unknown |
| `PLUGIN_HOOK_FIRED` (user notices) | `agent_message_chunk` | One `⚠ ` line per hook notice |
| `ERROR` | `agent_message_chunk` | `Error: <message>`, plus ` — <detail>` when there is one |

### A file edit arrives as a diff

`replace` and an appending `write_file` open their `tool_call` with a `diff` entry. The matching `session/request_permission` leads with the same entry, so the user approves a change they can read:

```json
{"sessionUpdate":"tool_call","toolCallId":"c1","title":"replace","kind":"edit","status":"pending",
 "rawInput":{"file_path":"src/config.py","old_text":"DEBUG = False","new_text":"DEBUG = True"},
 "content":[{"type":"diff","path":"/home/u/proj/src/config.py","oldText":"DEBUG = False","newText":"DEBUG = True"}]}
```

- A `diff` is **one hunk, not a whole file**. `oldText` is `null` for a pure append. `path` is absolute when the session `cwd` is known.
- It shows what was **requested**. `replace` matches whitespace and typography flexibly, and `replace_all` can hit several sites, so the applied text can differ. The terminal update says whether the edit applied.
- **A non-append `write_file` gets no diff.** Its arguments do not say whether the file exists, so `oldText: null` could show an overwrite as a creation.
- `locations` (follow-along highlighting) is not sent.

### Streamed tool output

Only `run_shell_command` streams. ACP **replaces** a tool call's `content` on every update, so each update restates the whole output so far. Two bounds apply, both in [`agentao/acp/_tool_call_content.py`](https://github.com/jin-bo/agentao/blob/main/agentao/acp/_tool_call_content.py):

| Bound | Value | Effect |
|-------|-------|--------|
| `FLUSH_CHARS` | 4 000 | The first chunk always goes out (it moves `pending` → `in_progress`). After that, an update goes out once another 4 000 characters have collected. Held-back text rides the next update or the terminal one |
| `MAX_CHARS` / `HEAD_CHARS` | 16 000 / 4 000 | The client's copy keeps the first 4 000 and the last 12 000 characters, with `[… N characters elided …]` between them |

- The cap is for the **client's copy only**. The model still gets the full result (up to 80 000 characters, or a file under `.agentao/tool-outputs/`), and replay records it.
- A chunk that arrives after the call completed is dropped. It never re-opens the call.

### `tool_call.kind` (closed enum)

`read`, `edit`, `delete`, `move`, `search`, `execute`, `think`, `fetch`, `switch_mode`, `other`. Agentao maps its tool names into this enum — `_TOOL_KIND_MAP` in `agentao/acp/_transport_helpers.py`, which a test holds exhaustive over `BUILTIN_TOOL_NAMES`. A tool with no entry (host-injected, every `mcp_*`) is `other`, ACP v1's own default. Agentao does not currently produce `delete`, `move` or `switch_mode`.

### `content[]` entries (`ToolCallContent`)

| `type` | Shape | When |
|--------|-------|------|
| `content` | `{content: {type:"text", text}}` | The tool description in a confirmation dialog, streamed output (restated whole), a trailing `Error: …` line on failure, or a replayed result |
| `diff` | `{path, oldText?, newText}` | A file edit, at `status:"pending"` — a hunk, not a whole file; `oldText` is `null` when nothing is replaced |

ACP v1 has a third variant, `terminal`. Agentao never emits it: it does not call `terminal/create` (the G1 fs/terminal proxy is a documented non-goal).

### `tool_call_update.status`

| Value | When |
|-------|------|
| `in_progress` | Streaming output chunk |
| `completed` | Tool succeeded (and every replayed call) |
| `failed` | Tool raised, returned an error, or was cancelled. ACP has no cancelled status for tool calls |

## C.5 `session/request_permission` ⇠ request

Server asks client to confirm a tool call.

### Params

| Field | Type | Notes |
|-------|------|-------|
| `sessionId` | `string` | |
| `toolCall` | `object` | Same shape as `session/update.tool_call`, under the id that `tool_call` opened. `content[]` leads with the `diff` for a file edit, then the tool description |
| `options` | `[{optionId, name, kind}]` | `allow_once`, `allow_always`, `reject_once`, `reject_always` (`optionId` equals `kind`). A call the MCP Skills gate asks gets only the two `*_once` options |

### Expected response (client → server)

| Field | Type | Notes |
|-------|------|-------|
| `outcome.outcome` | `"selected"` or `"cancelled"` | |
| `outcome.optionId` | `string` | Required when `selected` |

**No timeout**: the server waits until the client answers or the connection closes. `session/cancel` does not answer an open request for you. After a cancel, reply `{"outcome":"cancelled"}` to every open request, as the ACP spec requires. A closed connection, a JSON-RPC error reply, a malformed result and `cancelled` all reject the tool.

**Remembered answers**: `allow_always` / `reject_always` are stored per tool name for that session. Later calls skip the request. A call the MCP Skills gate asks ignores a stored `allow_always` and stores nothing.

## C.6 `session/cancel`

Abort the current turn. Open `session/request_permission` and `_agentao.cn/ask_user` requests stay open until the client answers them.

### → Request

| Field | Type | Notes |
|-------|------|-------|
| `sessionId` | `string` | |

### ← Response

ACP defines `session/cancel` as a notification (no `id`), so the server sends no response. A client that sends it as a request (with an `id`) gets `"result": null`. The ongoing `session/prompt` eventually resolves with `stopReason: "cancelled"`.

No turn running, a turn already cancelled, or a closed session: silent no-op. An unknown `sessionId`: `-32600` as a request, dropped as a notification.

## C.7 `session/load`

Resume a session by id. Only usable when the agent advertises `loadSession: true` in its capabilities.

### → Request

| Field | Type | Notes |
|-------|------|-------|
| `sessionId` | `string` | The id of a saved session. Must not be live on this connection |
| `cwd` | `string` | Absolute path. The session is read from `<cwd>/.agentao/sessions/` |
| `mcpServers` | `[object]` | Required (`[]` ok), same shape as `session/new`. Attached fresh to the loaded session |

### ← Response

`{configOptions: [...]}` — the same model/provider selection options as `session/new` (empty list if no catalog). The agent also replays prior turns as `session/update` notifications (reconstructed from persisted history) before it's ready for the next `session/prompt`.

### Common failures

| JSON-RPC code | Cause |
|---------------|-------|
| `-32600` | Unknown id, unreadable or corrupt session file, or the id is already live on this connection |
| `-32000` | No LLM provider configured (`auth_required`) |

### History replay

Before it answers, the server walks the saved messages and sends one or more notifications per message. ACP clients that wait for the response therefore see the whole history before the next turn.

| Saved message | `sessionUpdate` |
|---------------|-----------------|
| `system` | — (skipped) |
| `user` | `user_message_chunk`, with `<system-reminder>` blocks removed |
| `assistant` text | `agent_message_chunk` |
| `assistant` `tool_calls` | One `tool_call` per call, `status:"completed"`. A file edit carries the same `diff` it carried live |
| `assistant` `todo_write` call | `plan`. Its `tool` result is skipped |
| `tool` result | `tool_call_update`, `status:"completed"`, the result text — after the call's `diff` when it had one |

The saved messages carry no success or failure, so a call that failed or was rejected also replays as `completed`.

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

- **`provider/model`**: the agent calls `provider_resolver(provider_id)` → `{api_key, base_url?}`, then swaps provider + model. The **default** resolver accepts only the configured `LLM_PROVIDER` (case-insensitive) and reads `{PREFIX}_API_KEY` / `{PREFIX}_BASE_URL`; any other provider → `-32600` `cannot resolve provider '<id>'`. Inject a richer resolver to support more providers. A resolver whose providers speak different **wire protocols** (0.5.0: `openai-completions` / `anthropic-messages`) adds an optional `api_format` to what it returns, and the switch changes the session's wire with it (clearing the same state a model or endpoint change clears). An unknown or not-yet-implemented value is refused with `-32600` before anything is switched. A resolver that omits the key gets the **default** protocol, `openai-completions` — the same meaning as an unset `{PROVIDER}_API_FORMAT`, and not "the session's current one": a resolver that marks only its Anthropic provider has to be able to switch back. The default resolver returns the block's own `{PREFIX}_API_FORMAT`.
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

## C.11 `_agentao.cn/ask_user` ⇠ request (extension)

Agentao-specific. Server asks the user a free-form question.

### Params

| Field | Type | Notes |
|-------|------|-------|
| `sessionId` | `string` | |
| `question` | `string` | Free-form text |

### Expected response

| Field | Type | Notes |
|-------|------|-------|
| `outcome` | `"answered"` \| `"cancelled"` | `"answered"` when the user replied, `"cancelled"` when they dismissed the prompt |
| `text` | `string` | With `"answered"`: the user's reply as one string |

Any other shape is refused (`extra="forbid"`). A cancelled prompt, an empty `text`, an error response or a malformed result all reach the model as the sentinel `"(user unavailable)"` (constant `ASK_USER_UNAVAILABLE_SENTINEL`).

## C.12 JSON-RPC error codes (quick reference)

| Code | Name | Meaning |
|------|------|---------|
| `-32700` | Parse error | Invalid JSON received by server |
| `-32600` | Invalid Request | JSON is valid but not a JSON-RPC Request |
| `-32601` | Method not found | The method doesn't exist / isn't available |
| `-32602` | Invalid params | Invalid method parameter(s) |
| `-32603` | Internal error | Internal JSON-RPC error |
| `-32002` | Server not initialized | Session method called before `initialize` |
| `-32000` | Auth required | `session/new` / `session/load` with no LLM provider configured (0.5.8+); run the advertised terminal login, then reconnect |

Map these to `AcpErrorCode` per [Appendix D.2](./d-error-codes#d-2-json-rpc-numeric-codes-vs-acperrorcode).

---

→ [Appendix D · Error codes](./d-error-codes) · [Appendix E · Migration](./e-migration)
