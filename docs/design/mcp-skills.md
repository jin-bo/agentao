# MCP Skills — Design

**Status:** **Proposal rev 3.2 (2026-10-02), not approved.** For issue
[#397](https://github.com/jin-bo/agentao/issues/397). Nothing here is implemented. All ten
decisions are settled (§11): seven as first-version defaults, D1, D9 and D10 chosen by the
maintainer as recommended. The plan in §13 follows them.

**Spec under design:** the MCP Skills extension, `io.modelcontextprotocol/skills`, stable page
<https://skills.extensions.modelcontextprotocol.io/specification/stable/skills>, fetched
2026-10-02 15:22 UTC. Its source is `specification/stable/skills.mdx` in
`modelcontextprotocol/ext-skills` @ `167da6c` (2026-09-28); the SEP is
modelcontextprotocol/modelcontextprotocol#2640. Requirements quoted below are from that fetch; a
later revision of the page may differ.

**Related:** [Pi 1.0 lessons for agentao](pi-1.0-lessons-for-agentao.zh.md) §8, which separates three
things this proposal must not blur — local `SKILL.md` support, ordinary MCP resource reads, and the
MCP Skills extension — and records that pi is a reference for resources but not for remote skills
(pi 1.0 has no host integration of the extension, §4 here). Its §9 ranks "MCP Resources and remote
Skills" fifth of six, each to be reviewed and protocol-verified on its own; this proposal is that
for Skills, and does not change the ordering. [MCP Resources](mcp-resources.md) is the companion
proposal this one builds on (§13).

---

## TL;DR

- **What the extension is.** A server publishes Agent Skills over MCP. `skills/list` (paginated)
  and `skills/get` return *entries*: the SKILL.md's URI, its frontmatter verbatim, and a complete
  manifest of the skill's files with SHA-256 digests and sizes. Files are read with the ordinary
  `resources/read`. It is defined against base revision **2026-07-28**, and the capability is
  declared in `server/discover`.
- **Why agentao cannot see it today — three gaps, all grep-verified (§2).** It keeps none of a
  server's capabilities (`discover()`'s result is discarded, `mcp/client.py:856`); it leaves a server
  that speaks both protocol eras on the handshake era by design (`mcp-streamable-http.md` §5.8.1,
  pinned by `test_a_dual_era_server_is_left_on_the_handshake_era`); and it has never issued a
  `resources/*` request.
- **The extension is a security surface before it is a feature.** The spec calls MCP-served skills
  "a higher-risk surface than remote tool invocation" and puts most of its MUSTs on the host:
  origin tagging, no host-side code execution without per-skill approval, origin-bound reads,
  per-server namespaces with no silent shadowing, digest and size verification on every read,
  field-by-field frontmatter verification, lazy retrieval, and content-bound approval. The design
  is mostly about those.
- **The shape (§5).** Off by default, per server: `"skills": true` in `mcp.json`. Such a server is
  connected **discover-first** (only it pays §5.8.1's negotiation cost) and listed once at connect.
  Its skills join the existing catalogue as `mcp:<server>:<uri>`, tagged with their origin,
  and are activated through `activate_skill` — which, for an MCP skill, asks the user, fetches
  SKILL.md, verifies it, and holds the entry. Supporting files are read through one new tool,
  `read_skill_file`, bound to the skill's own server and manifest; a generic resource read that
  lands in a loaded skill's directory goes through the same verification. Once an MCP skill has
  been loaded in a session — tracked by one map of held entries, kept on deactivation and shared
  with sub-agents — `run_shell_command`, shell-capable sub-agents and cross-origin reads are tightened
  to ask; an existing DENY stays a DENY. A Skills listing failure turns Skills off for that server
  and keeps its tools.
- **SDK.** mcp **2.x only**: 1.x cannot reach the 2026-07-28 revision. No released SDK has a
  Skills API — python-sdk PR #3485 is open — so agentao sends the three methods through
  `ClientSession.send_request` with its own request models (§3).
- **No peer has shipped this** (§4). opencode #52298 and hermes #104353 are open PRs; codex,
  goose, Claude Code and pi 1.0 have none — pi's MCP client stops at protocol 2025-11-25, so it
  cannot reach the extension's base revision at all. hermes' PR is the closest design and is what §5 borrows from;
  opencode's fetches eagerly, which the spec forbids.
- **The plan (§13):** two PRs — negotiation and registry; activation, verification and gates —
  each carrying its own docs, in one release.

---

## 1. Scope

**In:** client-side consumption — discovering skills a connected server publishes, presenting them
with their origin, activating them with consent and verification, and reading their files. This is
the issue's stated scope.

**Out:** serving agentao's own skills over MCP (the issue excludes it); a disk cache (§7.3, D6);
persisted approvals (§7.1, D5); `resources/directory/read` (§6.4); skills whose `resources` is
`"dynamic"` (D4); runtime refresh of the skill list (D8).

---

## 2. Current state (grep-verified on `main` @ `0ec2895`)

| Fact | Evidence |
|---|---|
| Negotiation is handshake-first; it escalates to `server/discover` only on `-32022` (definite) or `-32601` (speculative) from `initialize` | `mcp/client.py:772-869` |
| A dual-era server stays on the handshake era — deliberately, "demand-gated", with the flip named as "the whole switch" | `mcp-streamable-http.md` §5.8.1 (:538-546); `mcp/client.py:809-814`; tripwire `tests/test_mcp_protocol_negotiation.py:138-155` |
| The cost of leading with the probe: a FastMCP server on mcp 1.26.0 wrote **258 lines** to stderr per connect (4 after the flip), and for stdio that is agentao's stderr | `mcp-streamable-http.md` §5.8.1 (:511-520, :535-536) |
| Server capabilities are kept nowhere: only the version is read from `initialize`, and `discover()`'s result is discarded | `mcp/client.py:823`, `:856`, `:879` |
| agentao advertises no client extensions, and turns an unclaimed extension result into an error message | `mcp/client.py:906`, `:1023`, `:1089`; `:1159-1169` |
| No `resources/*`, `prompts/*` or `skills/*` request exists anywhere; MCP resources/prompts were deferred as "demand-gated" | `grep -rn "resources/read\|read_resource\|list_resources\|skills/list"` → no match; `docs/releases/v0.4.9.md:179-180`, `v0.4.14.md:160` |
| `tools/list` paging is bounded (100 pages, 1024 tools, 64 KiB cursor, no repeated cursor) | `mcp/client.py:249-251`, `:689-757` |
| MCP tools are registered once, at construction; a reconnect does not re-register | `agent.py:687-691`; `tooling/mcp_tools.py:124-151` |
| A skill is a dict keyed by **name**; local layers overwrite each other silently | `skills/manager.py:116`, `:576-583`, `:606`, `:613` |
| Plugin skills are namespaced `plugin:skill`, and a collision with an existing name fails the plugin | `embedding/plugins/resolvers/skills.py:112`; `plugins/skills.py:15-33` |
| Activation lists supporting files by **absolute local path**; the active block reads the whole SKILL.md **from disk** | `skills/manager.py:665-689`, `:743-758`, `:896-909` |
| The catalogue is in the stable system message and must stay byte-identical across activations; the `skill_name` enum is rebuilt per turn | `prompts/builder.py:347-406`; `tests/test_skills_prompt.py:146-171`; `tools/skill.py:34-55` |
| Skill descriptions in the catalogue and SKILL.md bodies in the tail pass through **no** sanitizer | `prompts/builder.py:385-390`; `skills/manager.py:790-792`; CLAUDE.md "skill/MCP descriptions inlined into the system prompt do not pass through it" |
| A tool result's model-bound copy is stripped of Unicode tag characters | `runtime/tool_result_formatter.py:282-290` |
| `allowed_tools` is stored for plugin skills and read by nothing | `skills/manager.py:844`; no other match |
| The only built-in tool that executes host code is `run_shell_command` | `tools/shell.py:336-341` |
| Sub-agents use the parent's MCP tool instances and connect nothing themselves | `agents/tools/_wrapper.py:558-563`, `:616-617` |

---

## 3. The SDK

**mcp 2.0.0 (installed) — no Skills API, but everything needed under it.**

- `ServerCapabilities.extensions: dict[str, dict[str, Any]] | None` (`mcp_types/_types.py:485`,
  `:507`), and `ClientSession.server_capabilities` returns the `discover()` result's capabilities,
  else `initialize()`'s (`mcp/client/session.py:792`).
- **The handshake era keeps `extensions` too — measured, not inferred.** The 2025-11-25 wire model
  has no `extensions` field and is `extra="ignore"`, but `send_request` only *validates* against it
  and returns the result parsed into the neutral model, which has one. A raw `initialize` result
  carrying `capabilities.extensions["io.modelcontextprotocol/skills"]` came back with it intact.
  This does not make the handshake era usable: the extension is specified against 2026-07-28, and
  §5.1 does not act on a handshake-era declaration.
- `ClientSession.send_request(request, result_type)` (`mcp/client/session.py:507`) sends any
  `Request` subclass; for a method the era table does not know, result validation is skipped
  (`except KeyError: pass`). So `skills/list`, `skills/get` and `resources/read` need only
  request/result models of agentao's own — `resources/read` already has the SDK's.
- Client-side advertisement exists (`mcp/client/extension.py:145`, `:189`) but the spec asks the
  client to declare nothing; agentao keeps advertising no extensions.

**mcp 1.x** cannot negotiate 2026-07-28 (`LATEST_PROTOCOL_VERSION = "2025-11-25"`, 1.30.0
`mcp/types.py:27`). Skills are therefore 2.x-only; on 1.x an opted-in server gets a diagnostic and
no Skills request (§5.6).

**Upstream:** the newest releases are 2.2.0 and 1.30.0 (2026-09-07). python-sdk PR #3485 ("Add
`Skills` extension", open, head `03e24f0`) adds a `Skills(ClientExtension)` with `list_skills`
(pages to completion, rejects a repeated cursor, dedupes URIs), `get_skill` (checks the returned
URI), `read_skill_uri` and `read_directory`, and a `verify_skill_resource` that checks size and
digest. It has no frontmatter verification, no consent and no cache — its issue (#3486) puts
"filesystem discovery/caching" out of scope. When it ships, §6's wire layer can move onto it; the
host obligations stay agentao's.

---

## 4. Peers (shallow clones, 2026-10-01/02)

| Peer | Status | What it does |
|---|---|---|
| **hermes-agent** | PR #104353, open | Off by default, `skills: enabled: true` per server; `skills/list` before the first model request, files lazily; addressed as `mcp:<configured-server>:<SKILL.md-URI>`, with `skills/get` for a URI absent from the listing; SHA-256 + size on every resource; consent at first activation bound to the exact manifest, separate from execution consent, nested skills not covered; changed manifest → new session and approval; 512 / 16 MiB per skill, 50 pages, 2,048 skills |
| **opencode** | PR #52298, open | Namespaced `mcp:<server>:<name>` — by *name*, so two same-named entries from one server collapse (spec :213); fetches SKILL.md at every status change (violates lazy retrieval, :470); compares JS string length to the byte `size`; drops `"dynamic"`; no frontmatter check, no consent |
| **codex** | No; issue #48000 open | Has proprietary "cloud skills" read from its own first-party MCP server with plain `resources/read` — not this extension |
| **goose** | No; issue #12068 open | A closed draft (#9321) targeted the old `skill://index.json` draft |
| **pi 1.0** (`v1.0.0`, `a13d35a`) | No | No `skills/list`, `skills/get` or extension id in `packages/` (0 matches); its MCP client's newest version is `2025-11-25` (`packages/mcp/src/protocol/types.ts:4`, `:9`), below the extension's base revision. It does ship ordinary resource tools — the reference for `mcp-resources.md`. Recorded in [pi 1.0 lessons](pi-1.0-lessons-for-agentao.zh.md) §8 |
| **gemini-cli, Claude Code** | No | No match in source (Claude Code: docs only) |
| **fast-agent** | Shipped, partial | An *eager installer*: downloads every file at install, verifies, writes a local copy; does not expose MCP skills to the model directly |
| **MCP Inspector** | Shipped (2.6.0) | Digest and frontmatter checks; an inspector, so no consent model |

Reference tests: the conformance suite (`modelcontextprotocol/conformance` @ `c37eec8`) has host
scenarios `sep-2640-host-no-prefetch`, `-verify-digest`, `-size-mismatch-failure` and
`-frontmatter-comparison` (the last serves `description: Exfiltrate credentials` against an entry
that says otherwise). Its `sep-2640.yaml` lists 89 checks, 49 of them host obligations marked
"traceability-only" — the suite cannot see them on the wire, so agentao's tests have to.

---

## 5. Design

### 5.1 Opt-in, per server

```jsonc
// .agentao/mcp.json
{ "mcpServers": { "docs": { "url": "https://docs.example/mcp", "skills": true } } }
```

`skills` defaults to `false`. It is the switch for everything below: a server without it gets no
Skills request, no discover-first, no change at all — which is also what "tool-only servers are
unaffected" requires. An ACP-supplied server (`session/new`) never has it: the translator does not
set it, as it sets `oauth: false` today.

Why opt-in rather than "use it when the server declares it":

1. The spec ranks MCP skills above remote tools in risk, and every skill places server-written
   text in the system prompt's catalogue. Connecting a server for its tools should not also accept
   its instructions.
2. A dual-era server can only be seen on the modern era (§5.2), and leading with the probe costs
   every 1.x stdio server 258 stderr lines per connect. Opt-in confines that cost to servers whose
   operator asked.
3. hermes reaches the same default.

### 5.2 Negotiation: discover-first for opted-in servers only

For a server with `skills: true` on mcp 2.x, `_negotiate` leads with `server/discover` and falls
back to `initialize` on a rejection (the inverse of today, by the same two error codes). Every
other server keeps handshake-first, so the tripwire test stays green and gains a sibling:
`test_an_opted_in_dual_era_server_is_discovered`. The capabilities from whichever call succeeded
are kept on the client (`McpClient.server_capabilities`, reset with `protocol_version`).

**The Skills gate** — all four, checked once per connect:
- the SDK can discover (`SUPPORTS_MODERN_ERA`, `_compat.py:137`);
- the negotiated version is ≥ `2026-07-28` (a ceiling, compared with `>=` as `protocol_version`'s
  docstring requires);
- `extensions["io.modelcontextprotocol/skills"]` is present;
- `resources` is declared (the spec makes it mandatory alongside the extension).

A server that fails the gate gets exactly one diagnostic (§5.6) and no Skills request; its tools
work as before.

### 5.3 Listing and the registry

At connect, after `tools/list`, a gated server is listed with `skills/list` to completion, within
what is left of the same `startup_timeout`. Paging reuses `tools/list`'s four bounds; the skill
count bound is 1,024 per server. **No file is fetched** — the spec's lazy-retrieval MUST covers
connection, listing and approval alike.

**A Skills failure turns off Skills, not the server.** A `skills/list` error, a paging bound, or
running out of the startup budget while listing leaves the connection up and its tools registered;
the server's Skills are marked unavailable with one diagnostic (§5.6) and no catalogue entries.
This cannot reuse the connect failure path: a `McpCatalogError` from `tools/list` today ends in
`_cleanup_failed_connect` (`mcp/client.py:659-661`), which tears down the whole connection. The
listing runs after the tools are in hand, in its own `try`, with its own deadline (the startup
budget's remainder), and never raises into `connect()`. The negotiation itself (§5.2) is not part
of this: a discover-first fallback that fails is a connect failure as today.

Each entry is validated before it is registered; an invalid one is dropped with a log line naming
the server and URI, never silently:
- `uri` ends in `/SKILL.md`, and its final path segment before it equals `frontmatter.name`;
- `name` matches the Agent Skills rules — 1-64 characters, `a-z0-9` and single interior hyphens
  (ASCII, as python-sdk #3485 and Inspector apply them; the reference validator's NFKC + `isalnum`
  accepts more, and is the looser reading);
- `description` is a non-empty string of at most 1,024 characters;
- `resources` is an array (D4: `"dynamic"` is registered as *unavailable*, so the user is told
  why, but never loaded) with an entry for the SKILL.md itself, every `uri` inside the skill
  directory, every `digest` `sha256:` + 64 lowercase hex, every `size` a non-negative integer, and
  no duplicates;
- the 512-entry and 16 MiB limits hold (checkable from the entry, so a skill over either is
  refused with its reason up front).

**Identity** is `(server label, uri)`, where the label is the `mcp.json` key — never
`serverInfo.name` (the spec's MUST). Everything that records or addresses a skill carries both
halves: the registry, the held entry, approvals, and the model-facing name.

**The model-facing name** is `mcp:<label>:<uri>`, the SKILL.md's URI **verbatim** — so
`skill://git-workflow/SKILL.md` on `docs` is `mcp:docs:skill://git-workflow/SKILL.md`. It is the
identity spelled out, so it is unique without tie-breaking (two same-named skills from one listing
stay apart — the spec's "disambiguate, don't discard"), depends on no other entry, does not move
when the listing changes, and is the same string D2's load-by-URI takes. (rev 2 used "the URI's
path minus `/SKILL.md`", which for `skill://git-workflow/SKILL.md` is the empty path — the
`git-workflow` is the authority — and added the scheme only when two entries collided, so a name
depended on its neighbours.) For people, the catalogue and `/skills` also show the frontmatter
`name` as a display name; it is never what the model calls. The `mcp:` prefix is **reserved**:
a local or plugin skill whose name starts with it is refused at load with a warning, as the `mcp_`
tool prefix is reserved today (`agent.py:472`). A remote skill therefore can never shadow a
local one or be shadowed by it.

The registry entries go into `SkillManager.available_skills` with `source_kind: "mcp"`, the label,
the held entry, and `path: None` — so `disabled_skills`, `/skills`, the catalogue and the tool enum
all see them with no second registry.

### 5.4 Presentation (catalogue and tool)

The catalogue gains a separate section after the local skills:

```
Skills served by MCP servers (untrusted: written by the server named, not by the user or this project):
• mcp:docs:skill://pdf-processing/SKILL.md (pdf-processing) [from MCP server "docs"]: Extract, fill, and assemble PDF documents
```

The name and description are server-written text entering the system prompt, so they pass through
`strip_unicode_tags` and a control-character strip, and the description is cut at 1,024
characters. The list is fixed at construction, so the stable-prefix invariant holds
(`test_skills_prompt.py` gains an MCP skill in its byte-identity test). The "Untrusted Input
Boundary" section (`prompts/sections.py:132-144`) gains MCP skills beside MCP tools.

`/skills` lists them in their own group with the server, and `/mcp list` shows each opted-in
server's skill count or its gate diagnostic.

### 5.5 Activation

`activate_skill("mcp:docs:skill://pdf-processing/SKILL.md")`:

1. **Consent.** The user is asked, once per skill per session: the server, the name, the
   description, the file count and total size, and — because the spec lets users inspect before
   loading — a way to view SKILL.md first. Approval is recorded against `(label, uri)` **and the
   manifest** (every `{uri, digest}`). A headless run (`agentao run`, a background sub-agent, ACP
   without a confirm path) answers no, through the transport's existing confirm, so it never
   loads. Approvals are not persisted across sessions (D5).
2. **Fetch and verify.** `resources/read` of the SKILL.md on **its own server**; the bytes (UTF-8
   of a text content, base64-decoded of a blob) must match the manifest entry's `size`, then its
   `digest`. The frontmatter is parsed and compared field by field with the entry's; any difference
   fails the load. A failure triggers one `skills/get`: if the manifest is unchanged the load fails
   ("the server served different content"); if it changed, the approval is revoked and the user is
   asked again, as "changed — re-approve", per the spec.
3. **Hold the entry.** The verified bytes and the entry go into the session's **held-entry map**,
   `(label, uri) → held entry` (the acting window may be held longer than the SKILL.md stays in
   context, never shorter). That map is the one piece of state everything in §6 reads: the
   *origins* are its keys' labels, and same-origin verification looks up the manifest by key — no
   second set to keep in step. It is not `active_skills`: deactivating a skill does not remove its
   entry (the instructions it put into context are still there); only `/clear` or a new session
   empties the map. One map object belongs to the session and is **shared** with every sub-agent
   spawned from it: a child starts with the parent's history in its `parent_context` but an empty
   `active_skills` (CLAUDE.md, *Sub-agents*), so gating on the child's active set would let a
   remote skill's instructions reach a shell with no gate, and a child that cannot see the held
   manifests cannot verify a same-origin read; a child's own MCP activation adds to the same map,
   since its result returns into the parent's context. Nothing tries to infer which text the model
   is currently following.
4. **Return.** The tool result is the body, wrapped with its origin:
   `<mcp-skill server="docs" uri="skill://pdf-processing/SKILL.md">…</mcp-skill>`, plus the file list
   **as paths relative to the skill root** (from the manifest; no `resources/directory/read`), and
   the instruction to read them with `read_skill_file`. The active-skills block in the volatile
   tail renders the held bytes with the same wrapper — never re-fetched, never read from disk.

`allowed-tools` in an MCP skill's frontmatter grants nothing. agentao reads that field for no
skill today; a test pins that this stays true for MCP skills (D3 records the decision not to add an
approval flow for it).

### 5.6 Diagnostics

One line per opted-in server whose skills are unavailable, in `/mcp list`, `agentao doctor` and the
log — never a warning per turn:

| Condition | Message |
|---|---|
| SDK 1.x | `skills need mcp>=2 (installed: 1.x)` |
| Negotiated version < 2026-07-28 | `the server speaks <version>; skills need 2026-07-28 or later` |
| No extension declared | `the server does not declare io.modelcontextprotocol/skills` |
| Listing failed, hit a bound or ran out of the startup budget | the error; the server's tools stay connected (§5.3) |
| Entry invalid / over a limit / `"dynamic"` | per skill, in `/skills` and the log |

---

## 6. Reading files

### 6.1 `read_skill_file(skill, path)`

One new built-in tool, registered when at least one server has `"skills": true` **and passed the
§5.2 gate** — not only when a skill was listed: a server may list nothing and still load a skill
by URI (D2), whose files then need this tool. It reads a file of a **loaded** MCP skill:

- `skill` must be an MCP skill loaded in this session (its held entry exists); `path` is relative
  to its root. The URI
  is resolved against the skill root and must be **in the held manifest** — an unlisted path is a
  verification failure, not a fetch (spec: "resolve reads of the skill's files only to URIs listed
  in the held entry").
- The read goes to the skill's **own** server. There is no parameter that can name another server,
  so the spec's cross-origin read (which would need per-call approval naming both servers) cannot
  be expressed — the confused-deputy rule holds by construction.
- Bytes are verified as in §5.5; a mismatch triggers the same refresh path.
- Text is returned as text (tag-stripped like every tool result); a binary file is described by
  type and size, not inlined.

**With a generic `read_mcp_resource`** ([mcp-resources.md](mcp-resources.md), proposal rev 3.4,
which leaves both rules below to this proposal and keeps only the interface they need — the server
as a plain argument, the call through the ordinary planner):

- **Same origin, inside a loaded skill — verified, not raw.** The spec's acting window binds reads
  of a skill's files to the held entry. Without this rule, a model acting on a skill from `A` could
  read that skill's files from `A` through the generic tool and skip the manifest, size and digest
  checks. So while the held-entry map is non-empty, a `read_mcp_resource(server=A, uri)` whose `uri` is
  the SKILL.md or lies under the directory of a skill **loaded from `A`** (its SKILL.md URI minus
  `SKILL.md`, compared after normalising) is handed to the same resolve-and-verify function
  `read_skill_file` uses: a manifest-listed file is returned verified; an unlisted one is refused
  ("not in the skill's manifest — use read_skill_file"), with no request. Other URIs on `A`,
  including files of skills listed but never loaded, are ordinary reads.
- **Cross origin — ASK.** A `read_mcp_resource` whose target server **differs from any** loaded
  skill's origin is tightened to ASK, naming the target and the origin(s) it differs from ("Any
  cross-origin read MUST be gated behind explicit per-call user approval naming both servers");
  headless, ASK is refused. The tightening follows §6.2's rule: a DENY stays a DENY. With skills
  from one server `A` loaded, a read on `A` is not asked and a read anywhere else is — rev 3's
  behaviour. With skills from `A` **and** `B` loaded, every read is asked, including one on `B`:
  rev 3 asked only for a server outside the origins, so A's instructions could read `B` unasked
  just because a B skill had been loaded — and loading B approves B's skill, not each A→B read.
  Asking whenever more than one origin is present is the conservative answer that needs no guess
  at which skill the model is following. It is checked after the same-origin rule above, so a
  read inside a loaded skill's directory is verified *and*, with several origins, asked.
  `read_skill_file` is unaffected: it can only reach a loaded skill's own manifest files, content
  the user approved at load.

Neither applies before an MCP skill has been loaded, and neither turns a read into a skill load.

### 6.2 Code execution while acting

The spec: hosts "MUST apply the same approval gate to code-execution tool calls issued while the
model is acting on an MCP-served skill." While the session's held-entry map (§5.5 step 3) is
non-empty:

- `run_shell_command` is tightened to **ASK** — including in `full-access` and under an `allow`
  rule. The prompt names the MCP skill(s) loaded.
- Spawning a sub-agent whose tools include `run_shell_command` is tightened to ASK for the same
  reason: it is the one way around the first rule.
- A headless run answers ASK with no, so a skill cannot run commands there at all.

**Tightening only — a DENY is never loosened.** The gate sits in `runtime/tool_planning.py::_decide`
*after* the two places a DENY is decided — the read-only preset's short-circuit and an engine
`DENY` (`tool_planning.py:619-642`) — and before an engine `ALLOW` or the `requires_confirmation`
fallback is returned: ALLOW and no-match become ASK; DENY returns as DENY with its own reason. rev 2
placed it ahead of the engine and "whatever the rules", which would have turned a command a rule
forbids into one the user could approve. The hardline floor is upstream of all of this and
unchanged.

The gate keys on the held-entry map, not on how the tool reached the model, so it holds under the
on-demand tool loading the [pi 1.0 lessons](pi-1.0-lessons-for-agentao.zh.md) §2 recommends: a
deferred `run_shell_command` loaded while acting on an MCP skill is still ASK. It lifts only when
the map is emptied (`/clear`, a new session) — deactivation does not lift it (D7).

### 6.3 Caching

In memory, per session, keyed `(label, uri, digest)`: a file read twice is fetched once, and a
cache hit is still checked against the current manifest's digest. No disk cache in the first
version (D6) — the spec's disk-cache MUSTs (host-only-writable or re-hashed on every access; outside
every filesystem-skill path; still MCP-origin after a restart) are a feature of their own.

### 6.4 Not used

`resources/directory/read`: for a skill with a manifest, a directory read adds nothing (the spec
says so), and the only case it serves is `"dynamic"`, which D4 declines. Load-by-URI for a skill
absent from the listing (`skills/get`) is D2.

---

## 7. Lifecycle and failure

- **Retry.** `skills/list`, `skills/get` and `resources/read` are reads: they may reconnect and
  retry once after a transient failure, under the rule the
  [pi 1.0 lessons](pi-1.0-lessons-for-agentao.zh.md) §3 draws (retry what cannot have acted).
- **Disconnect / reconnect.** The registry stays; an activation or file read against a server
  that is down fails with an ordinary error. Local skills, MCP tools and other servers are
  unaffected — the issue's acceptance criterion, and a test.
- **Refresh.** The list is read once per process (D8), like the tool list. A changed skill is
  discovered by verification failure (§5.5 step 2), which is all the spec requires ("a host need
  not poll for changes").
- **Sub-agents.** A child sees the parent's catalogue (`child_view`, shared entries) and reads over
  the parent's connection. Its activation of an MCP skill asks like the parent's; a background
  child is refused. The §6 gates read the session's shared held-entry map (§5.5 step 3), never the
  child's empty `active_skills`.
- **Logout / `/mcp remove`.** The server's approvals are dropped. With no disk cache there is
  nothing else to remove.

---

## 8. Mapping the spec's host requirements

| Spec requirement | Where |
|---|---|
| Call `skills/*` only after the declaration | §5.2 gate |
| No `resources/directory/read` without `directoryRead` | never called (§6.4) |
| Identity is (server, uri); nothing keyed on uri alone; host-assigned label | §5.3 |
| Names not assumed unique; disambiguate, not discard | `mcp:<label>:<uri>` naming, §5.3 |
| No silent shadowing of any other origin, filesystem included | reserved `mcp:` prefix, §5.3 |
| Support 512 entries / 16 MiB; tell the user when declining | §5.3 |
| Empty listing ≠ no skills; load by URI | D2 |
| A bare `resources/read` of SKILL.md is not a load | the generic read loads nothing; inside a loaded skill it is verified (§6.1) |
| Verify digest and size on read; never use unverified bytes | §5.5, §6.1 |
| Reads only to URIs in the held entry | §6.1, `read_skill_file` and the same-origin generic read alike |
| Frontmatter verified field by field | §5.5 step 2 |
| No retrieval ahead of need | §5.3, §5.5 |
| Origin tagged in context; not indistinguishable from local | §5.4, §5.5 step 4 |
| No host code execution without per-skill approval; same gate while acting | §6.2 (tightens to ASK, keeps DENY) |
| Origin-bound reads; cross-origin needs per-call approval | §6.1 (`read_skill_file` cannot express one; `read_mcp_resource` asks) |
| `allowed-tools` ignored unless approved | §5.5 (ignored; D3) |
| Nested skills need their own consent | a nested SKILL.md read through `read_skill_file` is a file, never activated; activating it is a separate `activate_skill` with its own consent |
| Content-bound persisted approval, revoked on a changed set | §5.5 step 1, D5 |
| `"dynamic"` cannot be content-bound | declined, D4 |
| Disk cache rules | no disk cache, D6 |
| Skill content untrusted; not higher-authority | §5.4 (catalogue label, untrusted-input section) |

---

## 9. Spikes before PR 1

| # | Question | How |
|---|---|---|
| S1 | Does 2.0.0's `send_request` carry a custom `skills/list` over a 2026-07-28 session, request `_meta` included, against a real modern server? | The python-sdk #3485 branch's test server, or a modern-only FastMCP server; agentao's own request models |
| S2 | Discover-first against the servers we test with: a 1.x stdio server (expected: the stderr noise, then fallback), a dual-era server, a modern-only one | The `mcp-streamable-http.md` §5.8.1 fixtures, with `skills: true` |
| S3 | What `resources/read` of a text file returns on the wire — is the digest over the UTF-8 encoding of `text` in every case the spec example implies? | The ext-skills example server (151-byte SKILL.md) |
| S4 | Run the conformance suite's four host scenarios against agentao | `modelcontextprotocol/conformance` client scenarios |

---

## 10. Out of scope

Serving skills; `resources/directory/read`; `"dynamic"` skills; a disk cache; persisted
approvals; runtime refresh; MCP prompts. (The generic resource-read tool is `mcp-resources.md`'s.)

---

## 11. Defaults and decisions

**Written as first-version defaults** — each can be reopened in review, but none needs a choice
before implementation:

| # | Default | Why |
|---|---|---|
| D2 | Load-by-URI for a skill absent from the listing: `activate_skill("mcp:<label>:<uri>")` is accepted outside the enum and confirmed with `skills/get`; the enum is dropped from `activate_skill`'s schema only when a Skills server passed the gate | A spec MUST ("empty listing ≠ no skills"); the name is the same string §5.3 uses; `read_skill_file` is registered for it (§6.1) |
| D3 | `allowed-tools` from an MCP skill is ignored | agentao grants from `allowed-tools` for no skill today; the spec allows ignoring it |
| D4 | `"dynamic"` skills listed as unavailable, never loaded | The spec allows declining them; they cannot be content-bound |
| D5 | Approvals last the session; nothing persisted | Persisting needs a store, revocation on a changed set and removal on `/mcp remove` — later, when someone tires of the prompt |
| D6 | Cache in memory, per session; no disk cache | A disk cache carries three spec MUSTs of its own (§6.3) |
| D7 | The §6 gates last for the rest of the session once an MCP skill was loaded — the held-entry map, not `active_skills` (§5.5 step 3) | Instructions read into context do not leave it on deactivation; the spec's acting window ends "at the earliest" when SKILL.md leaves context, which in agentao is only `/clear` |
| D8 | The skill list is read once per process; restart to refresh | As for tools (mcp-oauth D8); re-listing rewrites the stable prefix mid-session |

**Decided by the maintainer (2026-10-02), as recommended:**

| # | Decision | Chosen | Why |
|---|---|---|---|
| **D1** | How skills are enabled | Per-server `"skills": true`, default off | §5.1: risk ranking, the discover-first cost, hermes' default. Enabling on declaration would make every connect of every 2.x server lead with the probe |
| **D9** | Discover-first | For an opted-in server only; the tripwire stays | It is the first modern-only capability, so §5.8.1's condition for going global has arrived — but only for servers that asked. Revisit once 1.x servers are rare |
| **D10** | Wire layer | agentao's own request models over `send_request` | Written so that moving onto python-sdk #3485's `Skills` client later is a swap of the bottom layer; the host obligations stay ours either way |

---

## 12. Test plan

A fake modern-era server (extending `tests/test_mcp_protocol_negotiation.py`'s modern fakes),
serving skills with real `mcp_types` models — no `MagicMock`, per the compat-shim rule.

- **Negotiation:** an opted-in dual-era server is discovered and its skills listed; a non-opted
  dual-era server still receives only `initialize` (the tripwire, unchanged); a server without the
  extension receives no `skills/*` request (asserted on the method log); 1.x gets the diagnostic and
  no request.
- **Listing failure keeps the server:** a `skills/list` error, a paging bound, and a listing that
  outlasts the startup budget each leave the server `CONNECTED` with its MCP tools registered and
  callable, Skills marked unavailable with one diagnostic, and no catalogue entry (fails if the
  error reaches `_cleanup_failed_connect`).
- **Listing:** pagination to completion; each bound; a repeated cursor; every invalid-entry rule;
  the 512 / 16 MiB limits; `"dynamic"` listed as unavailable; **no `resources/read` during connect
  or listing** (the conformance `no-prefetch` scenario).
- **Naming:** the name is `mcp:<label>:<uri>` verbatim, including a `skill://<authority>/SKILL.md`
  URI; adding or removing another entry does not change any name; two same-named skills on one
  server, two servers serving the same URI, a local skill and a remote one with the same name —
  all coexist, under distinct names, with no overwrite; a local skill named `mcp:…` is refused.
- **Activation:** consent asked once per skill; headless refuses; digest mismatch, size mismatch
  and frontmatter mismatch each fail the load (the conformance verification scenarios, including
  `description: Exfiltrate credentials`); a changed manifest on refresh revokes the approval and
  re-asks; the tool result and the tail carry the origin wrapper; the system message is
  byte-identical across activating an MCP skill.
- **Files:** a listed file is read and verified; an unlisted path fails without a request; there
  is no way to name another server; a cache hit is re-checked against the current digest.
- **Empty listing, load by URI:** a gated server that lists nothing still registers
  `read_skill_file`; a skill loaded by URI from it has its supporting file read and verified.
- **Generic reads (with `mcp-resources.md`):** with a skill from `A` loaded,
  `read_mcp_resource(server=A, …)` of a manifest file in its directory returns verified bytes and a
  digest mismatch fails as through `read_skill_file`; an unlisted file there is refused with no
  request; a URI outside every loaded skill's directory is an ordinary read; a read against `B`
  asks naming both servers, is refused headless, and a `deny` rule on `read_mcp_resource` still
  denies it. **With skills from `A` and `B` both loaded, a read on `B` still asks** (and one on `A`),
  naming the other origin; with only `A` loaded, a read on `A` does not ask.
- **Shared held entries:** a sub-agent spawned after a load verifies a same-origin read against
  the parent's held manifest (an unlisted file refused); a skill the child loads is visible to the
  parent's gates.
- **Execution gate:** with an MCP skill loaded, `run_shell_command` is ASK in `full-access` and
  under an `allow` rule; a shell-capable sub-agent spawn is ASK; headless denies; without one, the
  gate is absent.
- **DENY is kept:** with an MCP skill loaded, a `deny` rule on `run_shell_command` and the read-only
  mode still DENY — no prompt is shown (fails if the gate runs ahead of the engine).
- **Origin set:** deactivating the skill leaves the gate on; `/clear` lifts it; a sub-agent spawned
  after the load — whose `active_skills` is empty — is gated; a sub-agent's own MCP activation
  gates the parent.
- **Failure isolation:** server down, read failure, digest mismatch — local skills and MCP tools
  still work.
- **Sanitizing:** tag characters and control characters in a remote description never reach the
  system message.

---

## 13. Implementation plan

Docs travel with the code: each PR updates `docs/reference/configuration.md`, the guides, the
developer guide's MCP chapter (both languages), CLAUDE.md and CHANGELOG `[Unreleased]` for what it
lands.

**PR 1 — negotiation and registry.** `mcp/config.py` (`skills` key); `mcp/client.py`
(discover-first for opted-in servers, keep capabilities, the gate, `skills/list` paging in its own
failure scope, §5.3); `mcp/skills.py` (new: request/result models, entry validation, naming);
`skills/manager.py` (register MCP entries, reserve `mcp:`); `prompts/builder.py` (the separate,
sanitized catalogue section); `acp/mcp_translate.py`; `/mcp list` and `/skills` display. Docs: the
`skills` key in `mcp.json`, and the limitations (mcp 2.x only; servers on 2026-07-28 or later only;
`"dynamic"` declined; no persisted approval, no disk cache, no runtime refresh). Activation of an
MCP skill returns "not yet supported" — PR 1 alone puts skills in the catalogue that cannot be
used, so PR 1 and PR 2 ship in one release.

**PR 2 — activation, files, gates.** Consent, fetch, verification, the held entry and the session's
held-entry map, shared with sub-agents (`skills/manager.py`, `mcp/skills.py`, the sub-agent spawn);
the origin wrapper in the tool result and the tail; `read_skill_file` and its registration rule;
the §6.2 gate in `tool_planning.py`, after the DENY paths; load-by-URI (D2); and, if
`mcp-resources.md` has landed, both §6.1 rules for `read_mcp_resource` — same-origin verification
and the cross-origin ASK — in the same seams, with their tests. Docs: activation, the gates, and
`docs/guides/skills.md`.

PR 1 → PR 2, each against `main` after the previous merged, one release.

---

## Appendix A — Revision history

- **rev 3.2 (2026-10-02).** The maintainer adopted the recommendations on D1 (per-server opt-in),
  D9 (discover-first for opted-in servers only) and D10 (own request models over `send_request`);
  §11 records them as decided. No design change.

- **rev 3.1 (2026-10-02).** From the maintainer's re-review (one P1, one implementation note):
  - **Cross-origin reads with several origins** (§6.1, P1): rev 3 asked only when the target was
    outside the loaded origins, so with skills from `A` and `B` loaded, an A-driven read of `B` went
    unasked. Now any read whose target differs from any loaded origin asks — unchanged for one
    origin, always asked for several. DENY still first. Test added.
  - **One state, not two** (§5.5 step 3): the separate origin set is replaced by the session's
    shared `(label, uri) → held entry` map; origins come from its keys, and a sub-agent can look up
    the held manifest for same-origin verification.

- **rev 3 (2026-10-02).** From a maintainer review (three P1s, two P2s and a failure-handling gap,
  each checked against the code first):
  - **Same-origin generic reads** (§6.1, P1): only cross-origin `read_mcp_resource` was gated, so a
    skill's own files could be read from its server past the manifest. Reads inside a loaded
    skill's directory now go through `read_skill_file`'s verification; unlisted files are refused.
  - **The gate's state** (§5.5, §6.2, §7, D7, P1): §6.2 and §7 gated on `active_skills` while D7
    recommended "the rest of the session", and a sub-agent starts with the parent's context but an
    empty active set. Now one session origin set, kept on deactivation, shared with sub-agents.
  - **DENY is kept** (§6.2, §6.1, P1): rev 2 put the gate ahead of the engine "whatever the rules",
    which could make a forbidden command approvable. It now runs after the read-only and engine
    DENY paths and only tightens ALLOW / no-match to ASK (`tool_planning.py:619-642`).
  - **`read_skill_file` registration** (§6.1, P2): registered when a Skills server passes the gate,
    not only when one lists a skill, so a load by URI from an empty listing can read its files.
  - **Naming** (§5.3, P2): "the URI's path" was empty for `skill://git-workflow/SKILL.md` (that is
    the authority), and adding the scheme on collision made names depend on other entries. Now
    `mcp:<label>:<uri>` verbatim, the frontmatter name shown beside it for people.
  - **Listing failure** (§5.3): a `skills/list` failure or bound now turns off Skills only; the
    connection and its tools stay (a `McpCatalogError` today tears the connection down).
  - **Simplified:** D2–D8 written as defaults, D1/D9/D10 left open; docs go with each code PR, two
    PRs instead of three.

- **rev 2.1 (2026-10-02).** Follows mcp-resources rev 3, which moved the cross-origin
  `read_mcp_resource` gate here: §6.1's note and PR 2 now own it. No other change.

- **rev 2 (2026-10-02).** Cross-referenced with
  [pi-1.0-lessons-for-agentao.zh.md](pi-1.0-lessons-for-agentao.zh.md) and
  [mcp-resources.md](mcp-resources.md): a *Related* note on the three-way distinction its §8 draws
  and on its §9 ordering; pi 1.0 gets its own peer row (no extension; client capped at 2025-11-25,
  verified in `packages/`); §6.2 states that the shell gate holds under on-demand tool loading
  (its §2); §7 states the read-retry rule (its §3). No design change.

- **rev 1 (2026-10-02).** First proposal, from the spec (fetched 2026-10-02 15:22 UTC), a
  grep-verified map of agentao's skills, MCP negotiation and prompt code, the installed SDK, and the
  peers' source. One finding corrected an inference made during research: the mcp 2.0 handshake
  era *does* keep a server's `capabilities.extensions` (measured, §3).
