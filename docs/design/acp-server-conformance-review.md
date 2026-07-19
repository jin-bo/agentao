# ACP Server — Standard-Conformance Gap Review

**Status:** Review record. Drafted 2026-06-18 from a file-by-file read of the
`agentao.acp` server package cross-checked against the Agent Client Protocol
(ACP, protocol version 1). **This is a gap analysis + prioritized improvement
proposal, not an approved plan.** It records what the ACP server implements,
where it diverges from the standard, and — *for the maintainer's judgment* —
what to build, document, or deliberately decline.
**Audience:** Agentao maintainers; anyone embedding agentao behind an ACP
client (Zed, DeepChat, …).
**Companion:** `acp-server-conformance-review.zh.md`.
**Related:**
- `deepchat-acp-patch-revision.md` — the prior ACP field-rename / accept-unknown
  work this review builds on (`session/set_mode`, `set_config_option`).
- `embedding-vs-acp.md` — why ACP is one *frontend* over the embedded core, not
  the core contract.
- `host-fs-policy.md` — the host filesystem-policy design that the fs-proxy
  decision in §4 (G1) must be reconciled with.
- `path-a-roadmap.md` — the embed-first strategy; ACP conformance is demand-gated
  against real target clients, consistent with §2.3 non-goals.

**Method:** read the dispatch table and every registered handler, grep for
outbound `server.call` sites and `fs/` `terminal/` usage, compare against the
ACP v1 method/capability surface. Code references anchored to `main`@`bcdb8e4`
(2026-06-18). ACP-spec claims are dated and will drift — re-verify the upstream
schema before acting. Method membership was verified against the official ACP v1
schema (`schema/v1/schema.json`) on 2026-06-18; see the correction note under
TL;DR for the first-draft claims that fix superseded.

---

## TL;DR

Agentao's ACP server is **high-quality engineering that implements only the
"agent half" of the protocol.** The *core* client→agent request surface
(`initialize`, `session/new|load|prompt|cancel`, mode/config) is robust — though
the v1 session-management methods (`session/list|delete|resume|close`, `logout`)
are unimplemented. What is largely absent is *consuming the capabilities the
client offers back* — the `fs/*` and `terminal/*` methods. The result: embedded
in a real ACP client, agentao behaves like a headless agent that happens to speak
ACP rather than one deeply integrated into the editor.

> **Correction (2026-06-18, post-review):** the official ACP v1 schema (release
> dated 2026-06-18) was re-fetched after this doc's first draft. Three first-draft
> claims were wrong and are fixed below: (i) the request surface is **not**
> "complete" — v1 defines `session/list|delete|resume|close` and `logout`, which
> agentao does not implement; (ii) `session/set_model` and `session/list_models`
> are **Agentao extensions, not ACP v1 methods**; (iii) G2/G3 are **not**
> runtime-only — agentao's own frozen contract (`agentao/acp/schema.py` →
> `docs/schema/host.acp.v1.json`) currently *forbids* `locations`/`diff` and omits
> the `max_tokens` stop reason, so those must be updated too. Source: official ACP
> schema at `schema/v1/schema.json` on `main`.

> **Positioning premise (determines G1's priority).** ACP makes `fs/*` and
> `terminal/*` *capability-gated*: the agent uses them only when the client
> advertises `fs.readTextFile`/`writeTextFile`/`terminal`. A **chat-class host
> typically does not** — so agentao's current local-file/local-shell behavior is
> **already correct and conformant** there, and G1 is **not a gap for non-IDE
> hosts**. G1 only bites when an **editor-class client (Zed/Cursor)** advertises
> those capabilities and agentao ignores them. The design docs
> (`embedding-vs-acp.md:13`) name Zed/Cursor/IDE as ACP targets, but the only
> *live* integration is **DeepChat** (an Electron chat UI,
> `deepchat-acp-patch-revision.md`). By the project's demand-gated rule (gap ≠
> need), **G1's headline status is conditional on an editor-class client being a
> real target**; absent that signal, the now-priorities are the client-agnostic /
> chat-relevant gaps (**G4**, **G3**, and G2's `diff`). The table's "Matters for"
> column tags each gap by client class.

> **Decision (2026-06-18): target-client class = chat/automation.** The maintainer
> has set the positioning to **chat/automation hosts** (DeepChat the live
> integration), not editor-class IDEs. Per §4 P0 option (b), **G1 (fs/terminal
> proxy) is a documented non-goal**: *agentao is a self-contained ACP agent that
> owns its own filesystem and shell (sandbox, permission engine, provider-neutral);
> routing file/terminal operations through the client is intentionally out of
> scope.* Its current local-fs/local-shell behavior is the intended, conformant
> behavior — not an omission. The conditional analysis below is retained as a
> **reactivation clause**: if an editor-class client (Zed/Cursor) is ever pursued,
> G1 returns as the headline. **Now-work: G4, G3, G2's `diff`** (none need an
> editor).

G1 is the highest-leverage gap **for an editor-class target** and is already
acknowledged-but-unbuilt in the code; for a chat/automation host it is
demand-gated (see premise). **G3** and the `diff` half of **G2** are local-only
enrichments (schema + emit, no client round-trip) that pay off for *any* client;
**G4** is what a chat client actually renders.

| # | Gap | Matters for | Severity | Effort |
|---|-----|-------------|----------|--------|
| G1 | Agent never calls client `fs/*` / `terminal/*` | **Non-goal** (chat/automation decided) — reactivates only for editor clients | n/a *(non-goal)* / High *(if IDE)* | High |
| G2 | `tool_call` updates lack `locations` + `diff` | `diff`: any client that renders edits · `locations`: editor only | High *(IDE)* / Med *(chat)* | Low-Med |
| G3 | `stopReason` only `end_turn`/`cancelled` | Any client + automation surface | Medium | Low |
| G4 | mode / plan / commands not surfaced as ACP updates | Chat **and** editor (chat renders these) | Medium-High | Med |
| G5 | Thin capability surface (http MCP, audio, embedded resource) | Demand-gated | Low | Varies |
| G6 | No upstream-schema conformance test | All | Medium | Low |

---

## 1. Scope and what "the standard" means here

ACP (Agent Client Protocol, agentclientprotocol.com) is the Zed-originated
JSON-RPC protocol between an **editor/client** and an **agent**. It is
bidirectional:

- **client → agent**: `initialize`, `authenticate`, `logout`, `session/new`,
  `session/load`, `session/prompt`, `session/cancel` (notification),
  `session/list`, `session/delete`, `session/resume`, `session/close`,
  `session/set_mode`, `session/set_config_option`. (Note: `session/set_model`
  and `session/list_models` are **not** in v1 — they are Agentao extensions.)
- **agent → client**: `session/update` (notification), `session/request_permission`,
  and the capability-gated **`fs/read_text_file`**, **`fs/write_text_file`**,
  **`terminal/create`**, **`terminal/output`**, **`terminal/wait_for_exit`**,
  **`terminal/kill`**, **`terminal/release`**.

Protocol version: **1** (`agentao/acp/protocol.py:18`).

A *complete* ACP agent does two things: it answers the client's requests **and**
it drives the client's fs/terminal so file edits and shell commands flow through
the editor's own view (unsaved buffers, diff review, terminal panel). Agentao
does the first thoroughly and the second almost not at all.

---

## 2. What is implemented (the agent half — done well)

Registered handlers (`agentao/acp/__main__.py:99-108`):

| Method | File | Notes |
|---|---|---|
| `initialize` | `initialize.py` | Version negotiation (echo-or-latest), capability advertisement, `agentInfo`, `_meta` extension list |
| `session/new` | `session_new.py` | `cwd` validation, MCP-server translation, `configOptions`, startup-resume seam |
| `session/load` | `session_load.py` | History replay via `_ReplayMixin` |
| `session/prompt` | `session_prompt.py` | `text` / `resource_link` / `image` content blocks; returns `stopReason` |
| `session/cancel` | `session_cancel.py` | Spec notification; tolerant of clients that send it as a request |
| `session/set_model` ⚠ | `session_set_model.py` | **Agentao extension — not in ACP v1.** Vendor-free model switch |
| `session/set_config_option` | `session_set_config_option.py` | Standard config path; host `provider_resolver` keeps credentials off-wire |
| `session/set_mode` | `session_set_mode.py` | `modeId` field; accept-unknown (DeepChat `code`/`ask`) |
| `session/list_models` ⚠ | `session_list_models.py` | **Agentao extension — not in ACP v1.** `{models: [...]}` |
| `_agentao.cn/ask_user` | extension | Underscore-prefixed per ACP, declared in `_meta` |
| `_agentao.cn/set_model` | `agentao_set_model.py` | Free-form model setter (DeepChat "type any model" UX) |

Outbound (agent → client): `session/update` notifications (rich event mapping,
`transport.py`) and `session/request_permission` (`_transport_interaction.py:161`).

**Engineering strengths worth preserving:**
- **Concurrent dispatch** (`server.py:22-42`): handlers run on a
  `ThreadPoolExecutor` so a worker blocked inside `session/request_permission`
  does not stall the stdin read loop — a correct solution to the blocking
  server→client-request problem.
- **stdout/log hygiene**: `sys.stdout` is redirected to stderr and all JSON-RPC
  writes go through a captured handle under a lock, so a stray `print` anywhere
  in-process can't corrupt the wire.
- **Deterministic shutdown ordering** (`server.py:363-394`): cancel pending
  outbound → trip session cancel tokens → drain executor → close sessions.
- **Image-block security** (`session_prompt.py:148-198`): runtime mirror of
  `additionalProperties:false` — any key other than `{type,data,mimeType}` is
  rejected, so the wire can never smuggle a host path or secret; plus size caps
  and base64 validation before decode.
- **Credentials never on the wire**: model/provider switching resolves secrets
  server-side via the host-injectable `provider_resolver`.

---

## 3. Gaps vs the standard

### G1 — Agent does not consume the client's `fs/*` and `terminal/*` capabilities *(highest leverage — for editor-class clients only)*

> **Resolved as a non-goal (Decision 2026-06-18: chat/automation).** Because ACP
> gates `fs/*`/`terminal/*` on client-advertised capabilities, a chat-class host
> that doesn't advertise them gets the *correct, conformant* behavior from agentao
> today — so with the target class set to chat/automation, **this is a documented
> non-goal, not a gap.** The analysis below is retained as the **reactivation
> clause**: it describes what would need building *if* an editor-class client
> (Zed/Cursor) that advertises fs/terminal ever becomes a target.

**Evidence.** The entire server makes exactly **two** outbound `server.call`
invocations (`_transport_interaction.py:161,328`): `session/request_permission`
and `_agentao.cn/ask_user`. There is **no** call to `fs/read_text_file`,
`fs/write_text_file`, or any `terminal/*` method anywhere in `agentao/` (grep:
no outbound `fs/` or `terminal/` string).

**Consequences.**
- **Filesystem**: file reads/writes go through agentao's *local* file tools, not
  the client. In an editor like Zed this bypasses unsaved buffers and the
  editor's own fs view; the client cannot mediate, diff, or track the edit
  through its native path.
- **Terminal**: shell commands run via the local `LocalShellExecutor` and are
  surfaced only as plain text inside `session/update`. The client's terminal
  panel / terminal blocks and the standard terminal lifecycle
  (`create`→`output`→`wait_for_exit`→`release`) are unused.

**Acknowledged-but-unbuilt.** `session_new.py:93-95` already records the seam:
*"`client_capabilities` is accepted so future factories can route e.g.
`fs.readTextFile: true` to choose between local file tools and ACP-proxied file
tools."* The branch is designed; it is just not implemented.

**Framing.** This is **a choice with a cost, not merely a bug.** Agentao is an
embedded harness that owns its own sandbox, permission engine, and
provider-neutral runtime (`embedding-vs-acp.md`, `host-fs-policy.md`). Routing
fs/terminal through the client *inverts* that ownership for ACP sessions, and
must be reconciled with the host fs-policy design. The cost of **inaction** is
being a second-class citizen in editor-class ACP clients; the cost of **action**
is a second fs/exec path to maintain and a policy reconciliation. Either way the
posture should be *decided and documented*, not left implicit.

### G2 — `tool_call` updates lack structural fidelity *(local-only: schema + emit, high ROI)*

`transport.py:236-247` emits `tool_call` with `toolCallId`, `title` (= the raw
tool name), `kind`, `status`, and `rawInput`. Missing, all of which ACP v1
supports and editor clients render:

- **`locations: [{path, line}]`** — without it the client cannot do
  "follow-the-agent" highlighting as the agent reads/edits files.
- **`content` of `type:"diff"` (`oldText`/`newText`)** — edit-tool results are
  sent as plain text (`_tool_content_text`), so the client renders them as a text
  blob instead of a reviewable diff. **The diff view is ACP's signature UX for
  edit tools; agentao forgoes it.**
- **Human-readable `title`** — ACP expects e.g. "Writing config.py"; agentao
  sends "write_file".

**This is a contract change, not only an emit change.** Agentao's own frozen ACP
schema is *more* restrictive than ACP v1 and currently **forbids** these shapes:
`AcpSessionUpdateToolCall` has no `locations` field and is `extra="forbid"`
(`schema.py:536-552`); `AcpToolCallContentEntry` accepts **only** `type:"content"`
with an inner text block — no `diff`, no `terminal` (`schema.py:630-642`). The
local `kind` enum also has just 6 values (`read, edit, search, execute, fetch,
other`, `schema.py:547`) versus ACP v1's 9 (adds `delete, move, think`) — so the
`_tool_kind` map in `transport.py:39` can already produce a value its own schema
rejects. Implementing G2 therefore means: update `agentao/acp/schema.py`,
regenerate `docs/schema/host.acp.v1.json`, and update the schema snapshot tests —
otherwise the emit will fail the project's own contract test.

Minor, related: `TOOL_COMPLETE` maps agentao's `cancelled`→ACP `failed`
(`transport.py:261-268`) because ACP tool calls have no cancelled status —
acceptable but lossy.

### G3 — `stopReason` is impoverished *(runtime + schema drift)*

> **RESOLVED in 0.4.16.** Both layers are closed. `max_tokens` was added to the
> local enum and the snapshot regenerated; the runtime now maps
> `TurnOutcome.incomplete_reason` (which shipped in 0.4.15 — the metadata the
> TODO below was waiting on) plus a per-turn `max_iterations_hit` flag on the
> ACP transport onto `end_turn` / `cancelled` / `max_tokens` /
> `max_turn_requests`. `refusal` stays unemitted by design. See
> `session_prompt.py::_stop_reason_for` and `docs/guides/acp.md`. The analysis
> below is kept as the record of why.

Two layers, not one:
- **Runtime**: `session_prompt.py:287-291` returns only `end_turn` or `cancelled`.
  The code's own TODO admits the richer reasons are not surfaced because
  `agent.chat()` returns no structured termination metadata. An ACP client
  therefore cannot distinguish "hit the iteration cap" from "finished normally" —
  which matters for automation and for UI that shows *why* a turn ended.
- **Schema drift**: ACP v1's `StopReason` enum is
  `{end_turn, max_tokens, max_turn_requests, refusal, cancelled}`, but the local
  `AcpSessionPromptResponse.stopReason` allows only
  `{end_turn, cancelled, max_turn_requests, refusal}` (`schema.py:270`) — it is
  **missing `max_tokens` entirely**. So even the schema must be extended, not just
  the runtime populated. (Note the local schema *already* permits
  `max_turn_requests`/`refusal`; the runtime simply never emits them.)

### G4 — mode / plan / commands not surfaced as ACP updates

- **Modes**: `session/set_mode` works but `session/new` does not advertise
  `availableModes` / `currentModeId`, and no `current_mode_update` notification
  is emitted. `session_set_mode.py:15-19` explicitly defers this and the
  UI-mode-vs-permission-axis split.
- **Plan**: ACP has a `plan` `sessionUpdate` variant (entries with status that
  clients render as a task checklist). Agentao has plan mode **and** a todo tool,
  but neither is mapped to `plan` — they stay internal. Sub-agents are likewise
  flattened to `agent_thought_chunk` text markers rather than structured
  `tool_call` timelines (`transport.py:30-35,279-301`).
- **Commands**: no `available_commands_update`. Agentao's rich slash-command set
  is CLI-only and never advertised to ACP clients.

### G5 — Thin capability surface *(mostly demand-gated)*

From `initialize.py`:
- `mcpCapabilities.http:false`, `sse:true` (`:75-78`) — only `sse_client` is
  imported; streamable-HTTP MCP servers passed by the client cannot be connected.
  (Consistent with the SSE-only posture in `project_mcp_connect_preflight`.)
- `promptCapabilities.audio:false`, `embeddedContext:false` (`:63-67`) — no audio;
  embedded `resource` blocks are rejected (`session_prompt.py:199-203`).
- `resource_link` is preserved as a text label but **not dereferenced**
  (`session_prompt.py:135-147`) — dereferencing would need an `fs/read_text_file`
  round-trip, which ties straight back to **G1**.
- `loadSession:true` **is** implemented — good.
- **Confirmed-in-v1, unimplemented**: `session/list`, `session/delete`,
  `session/close`, `session/resume`, and `logout` **are** defined in the official
  ACP v1 schema (verified 2026-06-18) — they are not "proposed/newer." Agentao
  registers none of them. Still demand-gated (build when a target client uses the
  session-management UI), but they are a real conformance shortfall, not an
  unknown.
- `authenticate` is a real ACP method and is not registered, but since
  `authMethods:[]` is advertised (`initialize.py:91`) a conformant client will
  never call it. A defensive clean-error path is a *robustness* nicety, not a
  conformance gap.

### G6 — No upstream-schema conformance test

`schema_export.py` exports agentao's own ACP Pydantic models to JSON Schema
(good foundation), and the test suite asserts the *internal* event→update
mapping — but **nothing validates the wire against the upstream ACP schema** or
runs agentao against the reference client. Drift from the spec would not be
caught.

---

## 4. Recommendations (prioritized)

Each is framed against the embedded-harness boundary: prefer changes that are
host-injectable and don't bake editor assumptions into the core.

**P0 — Target-client class: DECIDED = chat/automation (2026-06-18).** This gates
everything below. The decision is **chat/automation hosts** (DeepChat the live
integration), so:
- **G1 is a documented non-goal** — agentao stays a self-contained ACP agent that
  owns its own fs/shell; its current local-fs/local-shell behavior is the intended,
  conformant behavior. No fs/terminal proxy is built.
- **Reactivation clause**: if editor-class clients (Zed/Cursor) ever become a
  target, G1 returns as the headline — implement ACP fs/terminal proxy tools gated
  on `client_capabilities` via the `session_new.py:93-95` seam (fs proxy first;
  terminal second), reconciled with `host-fs-policy.md`.

The items below are the actual now-work — all client-agnostic or chat-relevant,
none editor-specific.

**P1 — Surface plan + modes + commands (G4).** *(top chat-relevant item)* Map
plan mode / the todo tool to ACP `plan` updates; advertise `availableModes` and
emit `current_mode_update`; advertise slash commands via
`available_commands_update`; split the UI-mode axis from the permission axis
(already noted as deferred design). These are exactly what a chat client renders,
and DeepChat's `set_mode`/`set_model` work already signals the demand direction.

**P1 — Structured `stopReason` (G3).** Two steps: (1) add `max_tokens` to the
local `StopReason` enum (`schema.py:270`) and regenerate the schema snapshot; (2)
thread termination metadata out of `agent.chat()` (e.g. `max_iterations` →
`max_turn_requests`) and map it in `session_prompt.py`. Client-agnostic — matters
for the automation surface and any client UI.

**P1 — `tool_call` `diff` content (G2, diff half).** Emit `diff`
(`oldText`/`newText`) for edit tools — it renders in chat clients too, not only
editors. Widen the frozen contract first (`agentao/acp/schema.py`: add the
`diff`/`terminal` variants to `AcpToolCallContentEntry`, extend the `kind` enum to
v1's 9 values), regenerate `docs/schema/host.acp.v1.json`, update the snapshot
tests, then emit. The **`locations` half is editor-only — defer it with G1.**

**P2 — Streamable-HTTP MCP transport (G5).** Add `streamable_http_client` so
`mcpCapabilities.http:true`. Demand-gated: only when a target client actually
passes HTTP MCP servers in `session/new`.

**P3 — Upstream conformance test (G6).** Wire `schema_export.py` to the official
ACP schema (or run agentao under the reference ACP client) as a CI check so spec
drift is caught mechanically.

---

## 5. Demand-gated / explicit non-now

Consistent with `path-a-roadmap.md` §2.3 and the project's demand-gated borrow
discipline (gap ≠ need): `session/list|delete|close|resume`, audio prompts,
embedded-resource dereferencing, and — **unless an editor-class client is a real
target — the G1 fs/terminal proxy and G2's `locations`** are only worth building
when a concrete client consumes them. Do not build them on spec-completeness
grounds alone. For the live chat host (DeepChat), the *now*-work is the
client-agnostic / chat-relevant wins — **G4** (plan/modes/commands), **G3**
(stopReason), and **G2's `diff`** — none of which need an editor.

---

## 6. Method coverage matrix

`I` = implemented, `—` = not implemented, `ext` = Agentao extension (not in ACP
v1). All v1 method membership verified against the official schema on 2026-06-18.

| ACP method | Dir | Agentao | Note |
|---|---|---|---|
| `initialize` | c→a | I | version negotiate + `_meta` extensions |
| `authenticate` | c→a | — | `authMethods:[]` ⇒ never called; OK |
| `logout` | c→a | — | v1 method; unimplemented (no auth ⇒ low priority) |
| `session/new` | c→a | I | + MCP translate, configOptions |
| `session/load` | c→a | I | history replay |
| `session/prompt` | c→a | I | text/resource_link/image; `stopReason` thin + missing `max_tokens` (G3) |
| `session/cancel` | c→a | I | notification; request-tolerant |
| `session/set_mode` | c→a | I | no `availableModes`/`current_mode_update` (G4) |
| `session/set_config_option` | c→a | I | host `provider_resolver` |
| `session/list`/`delete`/`close`/`resume` | c→a | — | **v1 methods, unimplemented**; demand-gated (G5) |
| `session/set_model` | c→a | ext | **Agentao extension, not ACP v1** |
| `session/list_models` | c→a | ext | **Agentao extension, not ACP v1** |
| `_agentao.cn/set_model` | c→a | ext | free-form model setter |
| `session/update` | a→c | I | rich mapping; no `plan`/`diff`/`locations` (G2/G4) |
| `session/request_permission` | a→c | I | blocking, concurrency-safe |
| `fs/read_text_file` | a→c | — | **G1** |
| `fs/write_text_file` | a→c | — | **G1** |
| `terminal/create` | a→c | — | **G1** |
| `terminal/output` | a→c | — | **G1** |
| `terminal/wait_for_exit` | a→c | — | **G1** |
| `terminal/kill` | a→c | — | **G1** |
| `terminal/release` | a→c | — | **G1** |
| `_agentao.cn/ask_user` | a→c | ext | declared in `initialize._meta` |

---

## 7. Bottom line

The ACP server is well-built where it exists and secure by construction on the
inbound path. Its conformance ceiling **depends on the target-client class.** For
the live chat host (DeepChat) agentao's current behavior is already conformant —
G1 is not a gap there — and the now-work is the client-agnostic / chat-relevant
wins: **G4** (plan/modes/commands), **G3** (stopReason), and **G2's `diff`**, each
a local schema + emit change with no protocol round-trip. **G1** (drive the
client's fs/terminal) becomes the headline *only if* an editor-class client
(Zed/Cursor) is pursued. So decide the target class first; the priority order
falls out of that. Everything else is demand-gated and should wait for a real
client to ask.
