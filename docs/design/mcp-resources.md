# MCP Resources — Design

**Status:** **Proposal rev 3.4 (2026-10-02), not approved.** Nothing here is implemented. Two
decisions remain for the maintainer (§10); the rest are written as defaults. The primary reference
implementation is **pi 1.0** (`earendil-works/pi` @ `v1.0.0`, `a13d35a`, released 2026-10-01).

**Related:** [Pi 1.0 lessons for agentao](pi-1.0-lessons-for-agentao.zh.md) — the comparison this
proposal follows from. Its §8 names pi as the reference for ordinary resources and separates them
from remote skills; its §3 (retrying reads but never tool calls) bears on §5.4; its §2 (on-demand
tool loading) bears on §5.2; its §9 ranks "MCP Resources and remote Skills" fifth of six, to be
reviewed and protocol-verified separately — which this document is the start of. That analysis
does not replace this document's decisions, and this document does not change its ordering.
[MCP Skills](mcp-skills.md) is the companion proposal for the extension (§13).

**Spec under design:** MCP base protocol, *Server Features → Resources*, revisions
[2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/resources) and
[2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/server/resources), both
fetched 2026-10-02 15:50 UTC with the 2026-07-28 changelog. Requirements quoted below are from
that fetch.

---

## TL;DR

- **The gap.** agentao connects MCP servers for their tools only. It never calls `resources/list`,
  `resources/templates/list` or `resources/read`, and what a *tool result* says about a resource is
  mostly thrown away: a `resource_link` block becomes the literal string `[resource_link]` — the
  URI is gone — and an embedded binary resource becomes `[resource: <uri>]` with its bytes dropped
  (`mcp/client.py:1197-1212`). A server that answers "your report is at `report://q3`" hands the
  model nothing it can follow.
- **This was deferred as demand-gated** (`docs/releases/v0.4.9.md:179-180`, "codex-style host
  surface is the reference if demand lands"). Two things changed: pi 1.0 shipped the same three
  tools codex, gemini-cli and opencode already have, so agentao is now the only one of the five
  without them (the [pi 1.0 lessons](pi-1.0-lessons-for-agentao.zh.md) §8: "Agentao 当前缺少相应资源发现和
  读取入口；已有的工具结果资源块处理不能替代它们"); and the Skills extension (`mcp-skills.md`) reads
  files over `resources/read`, so the read path is needed anyway.
- **The shape — pi's, which is codex's (§5).** Three built-in tools with the names models are
  trained on: `list_mcp_resources(server?, cursor?)`, `list_mcp_resource_templates(server?,
  cursor?)`, `read_mcp_resource(server, uri)`. Registered at construction when at least one
  connected server declares the `resources` capability, and governed by `disable_tools` /
  `enabled_tools` like built-ins (which takes an explicit change, §5.2); read-only; listings are
  live (no cache to go stale); every item tagged with its server. `read_mcp_resource` always takes
  the server — never inferred from the URI, as gemini-cli does.
- **Plus a fix that stands alone (§6):** tool results keep a `resource_link`'s URI, name and type,
  and point at `read_mcp_resource` with the server; an embedded binary resource is saved under
  `.agentao/tool-outputs/` and its path given.
- **What agentao does differently from pi (§7):** it never fetches an `https://` resource itself
  (the spec says a client "may"; doing so would bypass `url_policy`); binary content is saved under
  the session's working directory — passed down by the tool layer, never the process cwd — not the
  system temp dir, every blob is size-checked before it is decoded, and saved files are pruned with
  the oversized-result files.
- **First version, kept small:** no HTTP-status retry beyond the existing reconnect, and no Skills
  cross-origin gate — that gate belongs to `mcp-skills.md`; this
  design only keeps the interface it needs (§7).
- **Both SDK majors, both protocol eras.** Unlike Skills, resources are in the base protocol of
  every revision agentao speaks; `ClientSession.list_resources / list_resource_templates /
  read_resource` exist on 1.26 and 2.0 alike.
- **The plan (§12):** one PR — code, tests and the docs it needs.

---

## 1. Scope

**In:** model-driven discovery and reading of resources from connected servers (the three tools);
keeping what tool results say about resources; a `/mcp resources` listing for the user (§5.6); a
public manager API the tools and the command are built on, which an embedded host can call too.

**Out:** user-driven attachment of a
resource to a prompt (`@server:uri`, opencode and gemini-cli have it); subscriptions and
`list_changed` (§5.5); the completion API for template arguments; HTTP-status retry for reads
(§5.4 — later); MCP prompts; MCP Apps (`ui://`) resources, which only a host that renders them can
use.

---

## 2. Current state (grep-verified on `main` @ `0ec2895`)

| Fact | Evidence |
|---|---|
| No `resources/*` request anywhere | `grep -rn "list_resources\|read_resource\|resources/read" agentao` → no match |
| Deferred as demand-gated, with codex named as the reference | `docs/releases/v0.4.9.md:179-180`; `v0.4.10.md:179`; `v0.4.14.md:160`; `docs/design/mcp-tool-list-pagination.md:456-459` |
| A tool result's `resource_link` loses its URI (`[resource_link]`), an embedded blob its bytes (`[resource: uri]`), an image its data (`[image: mime]`) | `mcp/client.py:1197-1212` |
| Server capabilities are not kept, so "does this server have resources?" cannot be answered today | `mcp/client.py:823`, `:856`, `:879` (shared with `mcp-skills.md` §2) |
| `tools/list` paging has four bounds (100 pages, 1024 items, 64 KiB cursor, no repeated cursor) | `mcp/client.py:249-251`, `:689-757` |
| Calls reconnect once on a dropped session or transport | `mcp/client.py:1092-1130` |
| Tool results over 40,000 chars are saved to `<wd>/.agentao/tool-outputs/` with head and tail kept; the formatter falls back to the process cwd when it has no working directory | `runtime/tool_result_formatter.py:29-37`, `:73-98`, `:149-163`, `:232` |
| The 7-day pruning of that directory matches `*.txt` only | `runtime/tool_result_formatter.py:42`, `:48-70` (`out_dir.glob("*.txt")`) |
| `disable_tools` filters only the built-in list in `register_builtin_tools`, and rejects a name outside `BUILTIN_TOOL_NAMES`; `enabled_tools` prunes every non-`mcp_*` name at the end, but its typo guard accepts only registered names and `BUILTIN_TOOL_NAMES` | `tooling/registry.py:36-64`, `:131-138`; `agent.py:533-537`; `tooling/registry.py:172-222` |
| MCP tools are registered without the session binding built-ins get (`working_directory`, `filesystem`, `shell`) | `tooling/mcp_tools.py:124-144` vs `tooling/registry.py:67-82` |
| Every tool result's model-bound copy is stripped of Unicode tag characters | `runtime/tool_result_formatter.py:282-290` |
| `mcp_*` is the reserved MCP tool prefix; `origin="mcp"` tools reach sub-agents as the parent's instances | `agent.py:472`; `agents/tools/_wrapper.py:616-617` |
| An untrusted server's tool calls always confirm; its annotations are ignored | `mcp/tool.py:95-115` |
| Both SDK majors have `list_resources`, `list_resource_templates`, `read_resource`, `subscribe_resource` on `ClientSession` | probed on 1.26.0 and 2.0.0 |

---

## 3. What the spec says (2026-07-28, with 2025-11-25 differences)

- **Application-driven.** "Resources in MCP are designed to be application-driven, with host
  applications determining how to incorporate context based on their needs" — UI selection,
  search, or "automatic context inclusion, based on heuristics or the AI model's selection". The
  protocol "does not mandate any specific user interaction model". A model-callable tool is one
  conforming choice; it is the one every peer made.
- **Capability.** Servers that support resources MUST declare `resources`, optionally with
  `listChanged` and `subscribe`.
- **Listing** is paginated and cacheable; on 2026-07-28 the set "MUST NOT vary per-connection or as
  a side effect of other requests", and `ttlMs` / `cacheScope` are required on `resources/list`,
  `resources/read` and `resources/templates/list` (SEP-2549).
- **Reading** may return several contents ("the contents of several files when a directory resource
  is read"), text or base64 `blob`, and on 2026-07-28 may answer with an `InputRequiredResult`.
- **`https://` URIs:** "if the scheme of uri is https://, clients may fetch the resource directly
  from the web" — a permission, not a requirement.
- **Templates** are RFC 6570 URI templates; arguments may be completed through the completion API.
- **Errors:** not found is `-32602` on 2026-07-28; clients "SHOULD also accept -32002", the earlier
  code. A server "MUST NOT return an empty contents array for a non-existent resource".
- **Subscriptions:** 2026-07-28 replaces `resources/subscribe` / `unsubscribe` with
  `subscriptions/listen` (SEP-2575).
- **Annotations:** `audience` (`user` / `assistant`), `priority`, `lastModified` — hints.

---

## 4. Peers

| Peer | Tools | Server argument | Notes |
|---|---|---|---|
| **pi 1.0** (`packages/coding-agent/src/extensions/mcp/resources.ts`, 342 lines) | `list_mcp_resources`, `list_mcp_resource_templates`, `read_mcp_resource` — named after codex's "so models trained on those tools use them unchanged" (:1-4) | required on read (:72-77); on list, optional — with it one page and `cursor` continues, without it every page of every server, and a `cursor` without a server is refused (:231-240) | JSON listings `{server?, resources:[{server, …}], nextCursor?, errors?}`, failures per server in `errors` (:242-253); `_meta` and icons removed (:56-59); MCP App resources (`ui://`, `profile=mcp-app`) filtered (:50-53); read-only annotation (:201); text → text, image → image, other binary → a 0600 temp file whose path the model gets (`tools.ts:182-194`); text over 20 KB cut in the middle with the full text saved (`tools.ts:46`, `:122-143`); a `resource_link` in a tool result names `read_mcp_resource` and the server (`tools.ts:171-181`); reads and lists retried once after a transient HTTP error, tool calls never (`runtime.ts:286-300`); a server without `resources/templates/list` has no templates (`runtime.ts:123-131`); 1,000-page cap and duplicate-cursor check (`packages/mcp/src/client.ts:39`, `:355-375`); tools reach every enabled, non-hidden server with resources (`index.ts:419`) |
| **codex** (@ `4cedd0c`) | the same three names (`core/src/tools/handlers/mcp_resource/`) | required on read: `required: ["server", "uri"]` (`core/src/tools/handlers/mcp_resource_spec.rs:91`) | The source of the names and of the JSON listing shape (its tests assert output starting `{"server":…`) |
| **gemini-cli** | `list_mcp_resources(serverName?)`, `read_mcp_resource(uri)` | **none on read** — "Locates the resource and its associated server by URI" (`docs/tools/mcp-resources.md`) | Two servers serving the same URI are ambiguous; no confirmation, kinds `Search` / `Read` |
| **opencode** | the same three names, grouped as reads in its permission table (`permission/index.ts:206`) | — | Also user-driven: a resource attached to a prompt is read and inlined (`session/prompt.ts:703-715`), blobs capped at 10 MiB (`:65`) |

---

## 5. Design

### 5.1 The manager API

`McpClientManager` gains three public sync methods, beside `call_tool`, run on its loop like every
other call, with the same reconnect-once:

```python
list_resources(server: str, cursor: str | None = None) -> ResourcePage
list_resource_templates(server: str, cursor: str | None = None) -> TemplatePage
read_resource(server: str, uri: str) -> ResourceRead
```

`server` is the `mcp.json` key — the host-assigned label, never `serverInfo.name`. Every
resource call checks in a fixed order, so that a dropped connection is recovered rather than
refused:

1. **Static — `resources_allowed(server)`:** the server exists in the manager and its config does
   not say `"resources": false` (§5.2). Fails → typed error, no request, no reconnect.
2. **Connection — the existing recovery:** the same `_ensure_connected` path `call_tool` takes when
   its session is gone or the client is not `CONNECTED` (`mcp/client.py:1117-1135`), including a
   reconnect another call already started. Fails → the same connection error a tool call gets.
3. **Capability — on the live connection:** the connection now in hand declares `resources`
   (capabilities are reset on disconnect with `protocol_version`, so this is read after step 2,
   never before). Fails → typed error, no request.

Then the request is sent, with §5.4's reconnect-once on a drop mid-request. Checking "connected"
up front would refuse exactly the call the reconnect exists for — one arriving after another call
dropped the session. The tools, `/mcp resources` and the §6 read hint share this order; no new
connection state is added. These are the layer the tools
and `/mcp resources` are built on, and an embedded host can call them directly (D7). The Skills
extension (`mcp-skills.md` §5.5) reuses the client-level `resources/read` underneath, not these
gated methods: its reads are bound to a skill's own server and governed by `skills`, not by
`resources` (§13).

**The manager returns data, never files.** `ResourceRead` carries each content's URI, MIME type and
either its text or its still-encoded base64 `blob`; nothing is decoded to disk here. The manager
is session-agnostic — a host may inject one and share it, and it holds no working directory — so
it cannot know where a session's files belong (§5.4).

**Capabilities** are kept on `McpClient` at connect (`server_capabilities`, reset with
`protocol_version`) — the change `mcp-skills.md` §5.2 needs too; whichever lands first carries it.

### 5.2 The tools

Names, parameters and listing shape follow pi and codex exactly, so a model trained on either uses
them unchanged:

| Tool | Parameters | Result |
|---|---|---|
| `list_mcp_resources` | `server?`, `cursor?` | JSON `{server?, resources: [{server, uri, name, title?, description?, mimeType?, size?}], nextCursor?, errors?}` |
| `list_mcp_resource_templates` | `server?`, `cursor?` | JSON `{server?, resourceTemplates: [{server, uriTemplate, name, title?, description?, mimeType?}], nextCursor?, errors?}` |
| `read_mcp_resource` | `server`, `uri` (both required) | the contents (§5.4) |

- **With `server`:** one page; `cursor` continues it. **Without:** every page of every server with
  resources, servers sorted by label, each server's failure reported in `errors` rather than
  failing the whole call. A `cursor` without a `server` is refused (there is no cross-server
  cursor). The all-servers walk reuses `tools/list`'s four bounds per server; one-page mode checks
  the returned cursor's size.
- **Each listed item** carries its `server`; `_meta`, `icons` and `annotations` are dropped (pi
  drops the first two; annotations are display hints for a host UI). MCP App resources are
  filtered as pi does.
- **Registration.** At construction, in `tooling/mcp_tools.py`, when at least one connected server
  declares `resources` — like MCP tools, fixed for the process (`mcp-oauth.md` D8, `mcp-skills.md`
  D8). `origin="mcp"`, so a sub-agent gets the parent's instances over the parent's connections, as
  it gets MCP tools. Each tool is bound to the agent's `working_directory` at registration, as
  `_bind_and_register` binds a built-in (§5.4 needs it).
- **`disable_tools` and `enabled_tools` — an explicit change, not automatic.** The names do not
  start with `mcp_`, but that alone does not put them under either knob: `disable_tools` only
  filters `register_builtin_tools`'s list and rejects any name outside `BUILTIN_TOOL_NAMES`
  (`agent.py:533-537`), and these register elsewhere. `enabled_tools` does prune them (its pass
  skips only `mcp_*`, plan and extra tools), but its typo guard would reject
  `enabled_tools={"read_mcp_resource"}` on an agent whose servers declare no resources. So: the
  three names join the set both knobs validate against — a sibling of `BUILTIN_TOOL_NAMES`
  (`MCP_RESOURCE_TOOL_NAMES`), registration-eligible like `web_search` without `[web]` — and the
  registration in `mcp_tools.py` skips any name in `agent._disable_tools` itself. They are not
  added to `BUILTIN_TOOL_NAMES` itself, which `test_builtin_tool_names_constant_in_sync` pins to
  what `register_builtin_tools` produces.
- **On-demand loading.** In pi the three tools take the *widest* exposure of the servers they reach
  (`direct`, then `codemode` or `deferred`; `docs/mcp.md:220`), so they are declared up front
  whenever any such server is. agentao declares every registered tool today
  (`ToolRegistry.to_openai_format`, `tools/base.py:370-390`). If the on-demand loading the
  [pi 1.0 lessons](pi-1.0-lessons-for-agentao.zh.md) §2 recommends is built, these three are three
  small, server-independent definitions and should stay directly declared, as pi's are for a
  `direct` server — the catalogue they open is the resources, not more tools.
- **Which servers.** Every configured server that passes §5.1's checks: allowed by config (no
  `"resources": false`, D2) and, on its connection, declaring `resources`. A listing without
  `server` walks every allowed server, recovering a dropped connection as a single-server call
  would; one that fails to reconnect, or turns out to declare no resources, goes into `errors` or
  is omitted respectively. Servers added by ACP
  `session/new` are included like their tools. **A disabled server is excluded from all three
  tools, including when the model names it explicitly:** a listing without `server` skips it, and
  `list_*` or `read_mcp_resource` with `server` set to it is refused with "resources are disabled
  for server '<label>'" — the same refusal whether the model guessed the name or took it from a
  tool result. `/mcp resources` names such a server as disabled by config rather than listing it.
  The flag hides generic resource access only; an MCP skill on the same server (`mcp-skills.md`)
  reads its own files through the client-level read under its `skills` setting (§13).
- **Read-only.** `is_read_only` is true: allowed in read-only mode and plan mode. No confirmation by
  default, whatever the server's `trust` (D3) — a read has no side effect to confirm, and its
  content enters the model as an ordinary, untrusted tool result. A permission rule naming the tool
  still applies, as for any tool.

### 5.3 Listing is live

Every list call goes to the server; nothing is listed at connect and nothing is cached. pi lists at
connect for the counts in its `/mcp` view, and refreshes on `notifications/resources/list_changed`;
agentao has neither the notification path nor a reason to pay for a listing nobody asked for.
`ttlMs` is therefore unused.

### 5.4 Reading

`resources/read` on the named server. The result is rendered for the model by the tool, from the
manager's `ResourceRead`:

- **Text** contents become text. Several contents are each labelled with their URI (pi, `:319-323`).
- **Every blob is size-checked before it is decoded or saved.** The decoded size is computed from
  the base64 length (`len(b64) * 3 // 4`, less padding) and compared with **10 MiB** (opencode's
  cap). Over it: nothing is decoded, and the model is told the URI, type and size. This is the
  only new limit, and it applies to textual and binary blobs alike — the 40,000-character spill
  happens after decoding, so it cannot bound the decode's memory. Plain-text contents need no new
  limit: they arrived as a JSON string and are already in memory.
- **Invalid content is an explicit error, not a guess.** Base64 that does not decode
  (`binascii.Error`, decoding with `validate=True`) reports "the server returned malformed base64
  for `<uri>`"; a blob with a text type that is not valid UTF-8 reports that, naming the type —
  never a silent `errors="replace"`.
- **Blobs with a text type** (`text/*`, `application/json`, `+json`, `+xml` — pi's list,
  `tools.ts:158-163`) are then decoded as UTF-8 text.
- **Other blobs**, images included (agentao tool results are text), are saved under
  `<working_directory>/.agentao/tool-outputs/`, mode 0600, as
  `mcp-resource_<ts>_<uid><ext>` (`<ext>` from the URI or MIME type when it is a short
  alphanumeric suffix, else none); the model gets
  `[Binary resource <uri> (<mime>, <size>) saved to <path>]`.
- **Where the file goes is the tool's to say.** The working directory comes from the tool's own
  binding (§5.2) — the session's, which differs per ACP session — passed as an explicit output
  directory to the save helper. The manager is never bound to a directory. When the tool has no
  working directory, the blob is **not saved**: the model is told its URI, type and size. There is
  no fallback to the process cwd (the formatter's `_TOOL_OUTPUT_DIR` fallback is not reused).
- **Pruning.** `_prune_tool_outputs` widens from `*.txt` to `*.txt` plus `mcp-resource_*`, so the
  saved blobs share the 7-day retention without a second mechanism. The prefix is fixed and
  generated, so the wider glob can only match files agentao wrote. **The glob alone is not enough:**
  pruning runs today only when the formatter spills an oversized text result
  (`runtime/tool_result_formatter.py:241-245`), and a binary save returns a short path line that
  never spills — a session that only reads binaries would never prune. So the save helper also
  calls the same `_prune_tool_outputs` on the directory it writes into. How often (every save, or
  once per directory) is left to the implementation; the function is best-effort and a glob is
  cheap.
- **Size of text.** Text goes through the ordinary tool-result path, which already saves anything
  over 40,000 characters and keeps head and tail; no second limit.
- **Empty contents** read as "the server returned no content for `<uri>`" (the spec forbids an empty
  array for a missing resource, so this is a server that has nothing, not a miss).
- **Not found** (`-32602`, or `-32002` from an older server) is reported as not found, naming the
  server, with a hint to list.
- **`InputRequiredResult`** is reported through the existing `_explain_input_required`
  (`mcp/client.py:1193-1196`).
- **Retry.** Reads and lists reuse the existing reconnect-once on a dropped session or transport
  (`mcp/client.py:1092-1130`), and nothing more in this version. pi also retries a read once on a
  transient HTTP status (408, 429, 5xx; `runtime.ts:286-300`); that is a sound addition for reads —
  the [pi 1.0 lessons](pi-1.0-lessons-for-agentao.zh.md) §3 rule, retry what cannot have acted —
  and is left to a later change of its own. This design adds no retry to tool calls and does
  **not** change `call_tool`'s reconnect-and-retry-once, which that section flags as unaudited; that
  audit is the lessons doc's first-ranked item (§9).

The URI may be any string the server accepts — a listed URI, one from a tool result's
`resource_link`, or one the model expanded from a template. agentao does not check it against a
listing (templates exist precisely to produce unlisted URIs).

### 5.5 Not built

- **Subscriptions and `list_changed`.** Listings are live (§5.3); a resource read is current when
  read. 2026-07-28 also replaced `resources/subscribe` with `subscriptions/listen`, so building on
  the 1.x-era subscribe now would be building on a removed method.
- **Template argument completion.** The model expands the template itself.
- **Annotations** (`audience`, `priority`) for automatic inclusion — there is no automatic
  inclusion.

### 5.6 CLI

`/mcp resources [server]` lists resources and templates for the user — server, URI, name and type —
by calling the §5.1 manager methods on demand, with the same paging bounds and per-server error
reporting as the tools. It spends no model turn and no tokens: it is how a user checks that a
server really declares `resources` and sees which URIs to name in a prompt. A server that declares
no resources says so; with no argument, every server that does is listed. `/mcp list` shows
`resources` among each server's capabilities. Attaching a resource to a prompt
(`@docs:report://q3`) is later (D6), and would be built on the same methods.

---

## 6. Tool results that mention resources

Independent of the tools, and fixable today. (The [pi 1.0 lessons](pi-1.0-lessons-for-agentao.zh.md) §8
is right that fixing this does not replace the tools: a link the model can read only helps if
something can read it.)

- **`resource_link`** → `[Resource <uri> "<title or name>" (<mime>, <size>): <description>. Read it
  with read_mcp_resource(server="<label>", uri=...)]` (pi, `tools.ts:171-181`). The read hint is
  given only when **all** hold at render time: `read_mcp_resource` is in the registry right now
  (so not after `disable_tools`, an `enabled_tools` prune, or when no server qualified),
  `resources_allowed(<label>)`, and the connection the result just arrived on declares `resources`
  (§5.1 steps 1 and 3 — step 2 is moot, the call has just succeeded over it). Otherwise the line stops after the description — the link's
  URI is kept, but the model is not pointed at a tool it cannot call or a server it would be
  refused by. The registry checked is the one `McpTool` was registered in; a sub-agent runs the
  parent's instances, so one whose `tools:` omits `read_mcp_resource` can still see the hint and
  gets tool-not-found if it follows it — one wasted turn, accepted rather than threading the
  executing registry into the tool.
- **Embedded `resource` with `text`** — unchanged (the text), labelled with its URI when the result
  has more than one.
- **Embedded `resource` with `blob`** → handled as in §5.4: size-checked first, decoded as text when
  its type is textual, else saved and its path given.
- **`image`** — unchanged (`[image: <mime>]`); agentao tool results carry no images. Recording the
  dropped image's size in the placeholder is the only change.

The label in the hint is the `mcp.json` key the tool belongs to, so the model reads back from the
server that produced the link.

**Where this runs.** Not in `McpClient.call_tool`, which has no working directory — and its
public shape does not change: `call_tool(...) -> str` stays as it is for hosts and tests. Today it
does three things beside the block loop — the `structuredContent` fallback when `content` is empty,
the `MCP tool error:` prefix on `isError`, and the string answers for transport errors and
`InputRequiredResult` (`mcp/client.py:1188-1237`) — and returning bare content blocks would lose the
first two. Instead:

- An internal `call_tool_result(...)` runs the same call with the same reconnect and returns the
  **whole** SDK result (`CallToolResult`), or the existing error string for the paths that never
  produce one (transport failure, `InputRequiredResult`).
- One shared renderer, `render_call_result(result, *, save=None, read_hint=None) -> str`, holds
  what the block loop does today plus the §6 changes, the `structuredContent` fallback and the
  `isError` prefix. `call_tool` becomes `call_tool_result` + `render_call_result` with no `save` —
  an embedded blob is then described by URI, type and size, not saved — so its output differs
  from today's only by the richer `resource_link` / blob placeholders.
- `McpTool.execute` calls `call_tool_result` and renders with `save` bound to its output directory
  and `read_hint` as above, so embedded blobs are saved through the same helper as
  `read_mcp_resource`'s. No new result type. That needs `McpTool` to
carry the session binding: `register_mcp_tools` binds `working_directory` as `_bind_and_register`
does for built-ins (§2 — today it binds nothing). A sub-agent uses the parent's `McpTool`
instances, so its saves land in the parent's working directory, as its MCP calls already run over
the parent's connections.

---

## 7. Security

- **Server-authored text.** Listings and contents are tool results: they reach the model through
  the formatter's Unicode tag strip and are covered by the system prompt's "Untrusted Input
  Boundary" (which names MCP tools; it gains MCP resources). Descriptions are not placed in the
  system prompt — they arrive only when the model lists — so nothing new enters the stable prefix.
- **No direct `https://` fetch.** The spec lets a client fetch an `https://` resource "directly from
  the web". agentao does not: a read always goes through the server. A direct fetch would be an
  outbound request with a server-chosen URL that skips `security/url_policy.py`; the model already
  has `web_fetch` for URLs, under its own rules.
- **The server is always named.** `read_mcp_resource` requires `server`; there is no lookup by URI
  (gemini-cli's shape), so two servers serving the same URI cannot be confused, and a server cannot
  receive a read meant for another by publishing a matching URI.
- **The Skills extension's origin rule — an interface constraint here, implemented there.** The
  extension says: "A model-callable resource-read surface is a cross-server confused-deputy vector
  when driven by untrusted skill content. Hosts MUST bind such reads to the skill's originating
  server … Any cross-origin read MUST be gated behind explicit per-call user approval naming both
  servers." Both of `mcp-skills.md`'s rules for this tool (its §6.1, rev 3) are built there, not
  here: a cross-origin read is tightened to ASK (an existing DENY kept), and a same-origin read
  inside a loaded skill's directory is verified against the held manifest — unlisted files
  refused — so the generic tool cannot bypass `read_skill_file`'s checks. What this design
  guarantees is what those rules need: every read names its `server` as a plain argument; the call
  goes through the ordinary planner, where a gate can tighten it; `read_mcp_resource` resolves the
  read through one function, before any request, where Skills can route a URI to its verifier; and
  the tool has no path into the skill registry, so a read is never a skill load ("Hosts MUST NOT
  treat a resources/read of a SKILL.md that arrives by any other route as a load").
- **Saved files** are mode 0600 in the session working directory's `tool-outputs/`, under a fixed
  generated name, never executed or opened by agentao; reading them back goes through `read_file`
  and its own rules.
- **Secrets.** A resource can carry credentials (a config file, a token). Tool results are already
  written to `agentao.log` and replays through the secret scanner; resource reads take the same
  path and need nothing new.

---

## 8. SDK and protocol eras

Resources are base protocol in 2025-11-25 and 2026-07-28 alike, so — unlike Skills — they need no
modern-era escalation and work on mcp 1.26 through 2.x. On 2.x, `ClientSession.list_resources`,
`list_resource_templates` and `read_resource` validate `ttlMs` / `cacheScope` on the modern era
themselves. On a 1.x server that does not implement `resources/templates/list`, `-32601` means "no
templates", as pi treats it (`runtime.ts:123-131`).

---

## 9. Spikes before the PR

| # | Question | How |
|---|---|---|
| S1 | Do 1.26, 1.30 and 2.0's `read_resource` and `list_resources` behave identically for text, blob and multi-content results, and on `-32602` / `-32002`? | The SDK's own FastMCP server with static, templated and binary resources |
| S2 | How does a modern-era `InputRequiredResult` arrive from `read_resource` on 2.0 — raised, or returned? | 2.0 test server |
| S3 | Real servers: what do the reference `filesystem`, `everything` and `github` servers list, and how large are their listings and reads? | The servers the docs already name |

---

## 10. Defaults and decisions

**Written as defaults** — no real alternative was in contention; each can still be reopened in
review:

| # | Default | Why |
|---|---|---|
| D1 | codex / pi's three tool names and listing shape, unchanged | Models are trained on them, four peers converge on them, and the `mcp_` prefix is reserved for server tools anyway |
| D4 | Binary content saved under the session's `<wd>/.agentao/tool-outputs/`, 0600, `mcp-resource_` prefix, 10 MiB cap checked before decoding; not saved without a working directory | The directory and pruning oversized results already use, under the project the user is in (pi uses the system temp dir) |
| D5 | No caching; every list and read is live | Nothing is listed unasked, so there is nothing stale to serve |
| D6 | User-driven attachment (`@server:uri`) later | An input feature on top of the manager API; the tools and `/mcp resources` close the loop without it |
| D7 | The three manager methods are public | The manager is already host-constructible; an embedded host building its own picker needs exactly these |
| D8 | Annotations dropped from listings | UI hints, and agentao has no UI for them yet |

**For the maintainer:**

| # | Decision | Options | Recommendation |
|---|---|---|---|
| **D2** | Which servers | (a) every server declaring `resources`, per-server `"resources": false` to hide · (b) opt-in | **(a).** A listing is model-initiated and read-only; every peer exposes them by default |
| **D3** | Confirmation | (a) none — read-only, any trust level · (b) confirm reads from untrusted servers | **(a).** A read has no side effect; the content is an untrusted tool result like any other. A permission rule can still require a prompt |

---

## 11. Test plan

Built from real `mcp.types` models over a real server (the SDK's FastMCP, both majors) — no
`MagicMock`.

- **Registration:** the three tools exist iff a connected server declares `resources`; the tools
  reach sub-agents; each tool carries the agent's `working_directory`.
- **`"resources": false`:** the server is skipped by both listings without `server`; `list_*` and
  `read_mcp_resource` naming it explicitly are refused before any request (asserted on the server's
  method log); `/mcp resources` shows it as disabled; a `resource_link` from that server's tool
  result carries no read hint.
- **`disable_tools` / `enabled_tools`:** `disable_tools={"read_mcp_resource"}` is accepted and
  leaves that tool unregistered while the other two register; `enabled_tools={"read_mcp_resource"}`
  is accepted with **no** resource-capable server connected (eligible, not a typo) and prunes the
  other two when one is; `BUILTIN_TOOL_NAMES` stays pinned to `register_builtin_tools`.
- **Listing:** one page with `server` and `cursor`; all servers without, sorted, with one failing
  server in `errors` and the rest listed; a cursor without a server refused; each paging bound;
  `_meta` / icons / annotations removed; `ui://` and `profile=mcp-app` filtered; every item tagged
  with its server; nothing listed at connect (asserted on the server's method log).
- **Templates:** listed; `-32601` from `resources/templates/list` means no templates.
- **Reading:** text; several contents labelled; textual blob decoded; binary blob saved 0600 under
  `tool-outputs/` as `mcp-resource_*` and its path returned; a blob — **textual and binary both** —
  over 10 MiB reported without decoding (asserted: no decode call / no file); malformed base64 and
  non-UTF-8 textual blob each an explicit error; empty contents; `-32602` and `-32002` as not found;
  an unknown server or one without `resources` refused before any request; a long text spilled by
  the existing formatter.
- **Output directory:** two agents with different working directories sharing one injected manager
  save into their own directories; a tool with no working directory saves nothing and writes
  nothing under the process cwd (asserted with cwd set to a temp dir).
- **Pruning:** an `mcp-resource_*.png` older than 7 days is removed by `_prune_tool_outputs`; a
  fresh one and a non-matching file are kept. **Without any text spill:** a session whose only
  tool-output write is one binary `read_mcp_resource` save prunes a stale `mcp-resource_*` file
  and a stale `*.txt` in that directory (fails if the helper does not call the pruner).
- **Retry:** a dropped session is reconnected once for a read; a tool call's behaviour is unchanged.
- **Recovery before the capability check:** with a server connected and then dropped (its session
  closed by an earlier call, client no longer `CONNECTED`), `read_mcp_resource` and
  `list_mcp_resources(server=…)` reconnect and succeed; the same with `"resources": false` is
  refused with no reconnect attempt (asserted on the connect count); a server whose reconnected
  session declares no `resources` is refused after the reconnect, before any `resources/*`
  request.
- **`/mcp resources`:** lists one server and all servers through the manager methods, one failing
  server reported and the rest listed; a server without `resources` says so; no model call is
  made; `/mcp list` shows the capability.
- **`https://`:** a read of an `https://` URI goes to the server, and no outbound request is made by
  agentao (asserted with the network blocked).
- **Tool results:** a `resource_link` keeps its URI and names the tool and the server; no read hint
  when `read_mcp_resource` is not registered (`disable_tools`) or the server is disabled; an
  embedded binary resource is saved into the calling tool's working directory; an embedded text
  resource is unchanged.
- **`call_tool` unchanged:** still returns `str`; a result with empty `content` and a
  `structuredContent` payload renders as its JSON; an `isError` result keeps the `MCP tool error:`
  prefix — both through `call_tool` and through `McpTool.execute`; transport errors and
  `InputRequiredResult` keep their strings.
- **Permissions:** allowed in read-only and plan mode; a deny rule naming `read_mcp_resource`
  applies.
- **Both majors:** the suite runs on mcp 1.26, 1.30 and 2.x.

Each fix-shaped item (the `disable_tools` path, the pre-decode cap, the pruning glob, the no-cwd
fallback) gets a test that fails with the change reverted.

---

## 12. Implementation plan

**One PR — code, tests and the docs it needs.**

- `mcp/client.py`: keep capabilities; `resources_allowed` and the §5.1 check order (config → `_ensure_connected` →
  capability); the three manager methods with bounds
  and reconnect; the internal `call_tool_result` returning the whole result, with `call_tool -> str`
  kept as `call_tool_result` + `render_call_result`.
- `mcp/resources.py` (new): listing shape, the size check, `render_call_result` and resource
  content rendering, and the binary save helper, which takes an explicit output directory and runs
  the pruner once per directory.
- `tools/mcp_resources.py` (new): the three tools.
- `mcp/tool.py`: `McpTool.execute` renders a result's blocks through the same helper (§6).
- `tooling/mcp_tools.py`: register the three tools, skip names in `disable_tools`, and bind
  `working_directory` on them and on `McpTool`s.
- `tooling/registry.py` / `agent.py`: `MCP_RESOURCE_TOOL_NAMES`, accepted by both knobs' validation.
- `runtime/tool_result_formatter.py`: prune `mcp-resource_*` too.
- `mcp/config.py`: `"resources": false`.
- `prompts/sections.py`: MCP resources in the untrusted-input section.
- `cli/commands/mcp.py`: `/mcp resources [server]`, `resources` in `/mcp list`; `cli/help_text.py`.
- Docs: `docs/reference/configuration.md` (`resources` in `mcp.json`, both twins); the developer
  guide's MCP chapter (both languages); CHANGELOG `[Unreleased]`; CLAUDE.md's MCP section; and the
  demand-gated notes this closes.

If `mcp-skills.md` is approved, its PR 1 builds on this one's capability retention and manager API,
and it adds the §7 cross-origin gate itself.

---

## 13. Effect on `mcp-skills.md`

That proposal (rev 3.1) already reflects this one: its §6.1 states the two rules for
`read_mcp_resource` — same-origin reads inside a loaded skill's directory verified against the held
manifest, reads on any server other than the loaded skills' origin tightened to ASK (every read, once skills from two servers are loaded) — and its PR 2 builds them with their tests, keyed on
its session-wide held-entry map rather than on `active_skills`. Skills supporting files are
still read through `read_skill_file`. A server's `"resources": false` hides generic resource access
only (§5.2): skill-file reads go through the client-level read, governed by that server's `skills`
setting.

---

## Appendix A — Revision history

- **rev 3.4 (2026-10-02).** Follows mcp-skills rev 3: §7 states both of its rules for this tool
  (same-origin verification inside a loaded skill, cross-origin ASK keeping DENY) and the one
  resolve function they hook into; §13 rewritten to match. No change to this design's own
  behaviour.

- **rev 3.3 (2026-10-02).** From the maintainer's third review: rev 3.2's `resources_enabled`
  included "connected" and refused before any request, so a call arriving after another call had
  dropped the session would fail instead of taking §5.4's reconnect. §5.1 now orders the checks —
  config, then the existing `_ensure_connected` recovery, then the live connection's capability —
  and a test covers recovering a dropped server. Also simplified: the binary-save pruning just
  calls `_prune_tool_outputs`, frequency left to the implementation (no process-level set).

- **rev 3.2 (2026-10-02).** From the maintainer's re-review (three P2s, each checked against the
  code first):
  - **`call_tool` keeps `-> str`** (§6, §12): rev 3 had it return content blocks, which would drop
    the `structuredContent` fallback and the `isError` prefix (`mcp/client.py:1214-1235`). Now an
    internal `call_tool_result` returns the whole result and one shared renderer keeps both.
  - **Pruning runs on binary saves** (§5.4): the wider glob was not enough, since pruning fires only
    on a text spill (`tool_result_formatter.py:241`). The save helper calls the pruner itself; a
    test covers a session with no spill.
  - **`"resources": false` is one rule** (§5.1, §5.2, §6): a `resources_enabled` predicate shared by
    the manager methods, the tools (explicit `server` refused too), `/mcp resources` and the read
    hint, which also requires `read_mcp_resource` to be in the registry.

- **rev 3.1 (2026-10-02).** `/mcp resources` restored to the first version at the maintainer's
  call (§1, §5.6, §11, §12, D6): it costs no tokens and is how a user checks a server.

- **rev 3 (2026-10-02).** From a maintainer review (four P2s, all grep-verified before acting, and
  a request to shrink the first version):
  - **`disable_tools` / `enabled_tools`** (§5.2): rev 2 inferred both governed the tools because
    their names lack `mcp_`. Wrong — `disable_tools` filters only the built-in list and rejects
    names outside `BUILTIN_TOOL_NAMES`; `enabled_tools`' typo guard would reject the names when no
    server declares resources. Now: an `MCP_RESOURCE_TOOL_NAMES` set both validate against, and an
    explicit skip at registration.
  - **Pruning** (§5.4): the 7-day cleanup rev 2 claimed to reuse matches `*.txt` only. Saved blobs
    now use a fixed `mcp-resource_` prefix and the glob widens to it.
  - **Blob size** (§5.4): rev 2 capped only non-text blobs, after decoding textual ones. Every blob
    is now checked against 10 MiB from its base64 length before any decode; malformed base64 and
    non-UTF-8 text are explicit errors.
  - **Output directory** (§5.1, §5.4, §6): rev 2 saved embedded blobs inside `call_tool`, which has
    no working directory, under a manager a host may share. The manager now returns data; the tool
    layer saves into its bound working directory and does not fall back to the process cwd.
    `register_mcp_tools` gains the binding.
  - **Scope:** the Skills cross-origin gate moved to `mcp-skills.md`,
    leaving an interface constraint here; HTTP-status retry deferred, the existing reconnect
    reused; one PR instead of two; D1 and D4–D8 written as defaults, D2 and D3 left open.

- **rev 2 (2026-10-02).** Cross-referenced with
  [pi-1.0-lessons-for-agentao.zh.md](pi-1.0-lessons-for-agentao.zh.md): a *Related* note naming
  which of its sections this document applies (§2, §3, §8) and how it sits in its ordering (§9);
  §5.2 gains how the tools behave under on-demand loading; §5.4 ties the read retry to its §3 rule
  and states that `call_tool`'s own retry is left to that audit; §6 cites its §8. No design change.

- **rev 1 (2026-10-02).** First proposal, from the Resources spec (2025-11-25 and 2026-07-28,
  fetched 2026-10-02), pi 1.0's MCP extension (`resources.ts`, `tools.ts`, `runtime.ts`,
  `packages/mcp/src/client.ts`, read in full where cited), codex, gemini-cli and opencode, and a
  grep of agentao's MCP client — which turned up the `resource_link` loss in tool results (§6).
