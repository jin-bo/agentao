# MCP OAuth — Design

**Status:** **Proposal, rev 1 (2026-10-02). Not approved; nothing implemented.**
Revisits `openworker-borrow-review.zh.md` §9 (rev 3, 2026-07-29), which downgraded MCP OAuth to a
single principle on the grounds that it "needs a loopback HTTP route and a browser — host-side, and
agentao should have neither". This document keeps that boundary and argues it no longer implies
"don't build it": the protocol machinery already lives in the MCP SDK, the browser and callback stay
with the host, and agentao's own CLI is one of those hosts. §1 says what changed; §11 lists the
decisions this needs from the maintainer.

**Readers:** agentao maintainers; anyone touching `agentao/mcp/`. **Chinese twin:** to be written once review round 1 settles the decisions in §11.

**Sources** (all fetched 2026-10-02): the MCP specification raw `.mdx` files for revisions
2025-11-25 and 2026-07-28 (`basic/authorization/…`) and the `ext-auth` repository; the installed
SDKs (`mcp` 2.0.0 in `uv.lock`, 1.26.0 and 1.30.0 via `uv run --with`); peer source at
codex `4dd51f4a`, gemini-cli `fb972b2f`, goose `b9db895a`, opencode `1ddb0873`; Claude Code's
public MCP docs. Spec wording below is paraphrased unless in quotation marks, where it is literal
from the raw file.

---

## TL;DR

- **The gap.** agentao's MCP client has no authorization beyond static `headers`
  (`mcp/config.py:34`). A remote server that requires OAuth — increasingly the default for hosted
  Streamable HTTP servers — cannot be used at all. Every peer we compare against (codex, gemini-cli,
  goose, opencode, Claude Code) supports it.
- **Most of the work is already in the SDK.** `mcp.client.auth.OAuthClientProvider` (both majors)
  is an `httpx.Auth` that does protected-resource and authorization-server discovery, client
  registration (CIMD, DCR), PKCE, the `resource` parameter, the token exchange and refresh. The
  caller supplies exactly three things: a `TokenStorage`, a `redirect_handler` (show the user a
  URL) and a `callback_handler` (receive the code).
- **The split** follows the 2026-07-29 boundary. *Harness* (`agentao/mcp/`): wire the provider into
  both URL transports, store tokens, add a `needs_auth` status, and **guarantee that nothing but an
  explicit login can start an interactive flow**. *Host*: open the browser and receive the
  callback. The CLI is a host and implements that part (`/mcp login`); an embedded host can
  implement it or not; an ACP session never starts it.
- **Five decisions** for the maintainer (§11), the main ones being whether agentao publishes a CIMD
  document (D2) and whether OAuth requires the 2.x SDK (D4).
- **One thing must be measured before building** (§9 S1): the SDK holds its auth lock until a
  response's headers arrive, which may serialize parallel tool calls on servers that answer in
  JSON mode — undoing #241 for exactly those servers.

---

## 1. What changed since the 2026-07-29 verdict

The verdict was right about *where* the browser and the callback belong, and this design does not
move them. What changed is the cost and the demand on the agentao side:

1. **The SDK does the protocol.** In July the alternative looked like re-implementing OAuth 2.1 +
   RFC 9728/8414/8707/7636. It isn't: §3 shows the SDK covers all of it on both majors, and the
   harness piece reduces to wiring, storage and one invariant.
2. **The CLI is a host, and it is ours.** "Host-side" does not mean "nobody in this repo": the
   `agentao` CLI already owns the terminal, the confirmation prompt and the `--login` flow (#382).
   A loopback listener in the CLI is the same kind of code.
3. **Users now arrive through editors and remote servers.** The ACP Registry listing (#380/#644)
   and the remote-first MCP ecosystem mean "use the hosted MCP server my team already uses" is a
   first-session request, and every peer answers it.
4. **The spec settled.** Revision 2026-07-28 made client duties concrete (issuer binding, RFC 9207
   `iss`, scope union on step-up, refresh-token handling) and deprecated DCR in favour of CIMD (§4).

The principle §9 kept is adopted unchanged as the core invariant (§5.2): *a background context
never starts an interactive flow; only a connect the user explicitly asked for may.*

---

## 2. Current state (grep-verified on `main` @ `946d11f`)

| Fact | Evidence |
|---|---|
| No OAuth anywhere in the MCP client | `grep -rni oauth agentao/mcp` → no match |
| Static headers work end to end, with `$VAR` expansion | `mcp/config.py:34,250-251`; `client.py:910` (`_prepare_url_connect`) |
| The content-type preflight passes a 401 through to the real handshake | `client.py:872-875` ("a 4xx/5xx may be an auth challenge") |
| Server status has no "needs auth" state; a failed connect is `ERROR` | `client.py:261-265`, `:551` |
| Streamable HTTP builds its own httpx client and passes it to the SDK | `client.py:978-993` (`create_mcp_http_client` → `streamable_http_client(http_client=…)`) |
| SSE passes `headers` to `sse_client`, which also accepts `auth=` | `client.py:928-934`; SDK signature (both majors) |
| Bearer values are redacted in `agentao.log` | `security/secret_scan.py:72-77` |
| `filelock` is a core dependency | `pyproject.toml:51` |
| CLI `/mcp` has `list`, `add`, `remove` only | `cli/commands/mcp.py:21,50,109` |
| In ACP, the editor supplies MCP servers per session | `acp/mcp_translate.py`, `acp/schema.py:191` |

---

## 3. What the SDK provides, per major

Verified by reading the installed source and by `inspect.signature` on each version.

| | mcp 1.26.0 / 1.30.0 | mcp 2.0.0 |
|---|---|---|
| Provider | `OAuthClientProvider(httpx.Auth)` (1.30: via `RedirectAwareAuth`) | `OAuthClientProvider(httpx2.Auth)` |
| Constructor | `server_url, client_metadata, storage, redirect_handler, callback_handler, timeout=300.0, client_metadata_url` | same, minus `timeout`, plus `validate_resource_url` |
| `callback_handler` returns | `tuple[code, state]` | `AuthorizationCodeResult(code, state, iss)` |
| RFC 9207 `iss` validation | no | yes (`validate_authorization_response_iss`) |
| Issuer-bound registration (SEP-2352) | yes | yes |
| Step-up scopes | replaced | union of previous and challenged (2026-07-28 rule) |
| Keeps a non-rotated refresh token | no | yes |
| Locking | one `anyio.Lock` per provider, held for the whole flow | same |
| Transport hook | `streamablehttp_client(auth=)`, `sse_client(auth=)`; `streamable_http_client` takes a pre-built client | `streamable_http_client(http_client=)`, `sse_client(auth=)` |
| Where `redirect_handler` runs | inline inside `async_auth_flow`, under the lock, on the request that got the 401 | same |

`TokenStorage` is four async methods (`get/set_tokens`, `get/set_client_info`) on **one instance
per provider**; keying is the caller's job.

The differences are the shape `agentao/mcp/_compat.py` already absorbs (probe the installed SDK,
never parse a version). The 1.x compliance gaps — no `iss` check, scope replacement on step-up —
are the subject of D4.

---

## 4. What the spec requires of a client (2026-07-28, core)

Authorization is optional in MCP ("Authorization is **OPTIONAL** for MCP implementations"), applies
to HTTP transports only (stdio "**SHOULD NOT** follow this specification, and instead retrieve
credentials from the environment"), and static headers are not forbidden. When a client does
OAuth, the duties that matter here:

| Duty | Who does it |
|---|---|
| Discover the authorization server from Protected Resource Metadata (`WWW-Authenticate` `resource_metadata`, then well-known fallbacks); support RFC 8414 and OIDC discovery; reject metadata whose `issuer` differs from the URL it was fetched from | SDK |
| Registration order: pre-registered → CIMD (if `client_id_metadata_document_supported`) → DCR (deprecated in 2026-07-28) → ask the user | SDK, given our inputs (§6.3) |
| DCR with `application_type: "native"` for CLI/desktop clients | **us** — it is client metadata we pass |
| PKCE S256; refuse if the AS does not advertise `code_challenge_methods_supported` | SDK |
| `resource` (RFC 8707) on authorization and token requests | SDK |
| Scope: the `WWW-Authenticate` challenge is authoritative; on 403 `insufficient_scope`, re-authorize with the union, a few times at most | SDK (2.0) / partly (1.x) |
| RFC 9207 `iss` check on the authorization response | SDK (2.0 only) |
| Keep separate credentials per authorization server; re-register when it changes | SDK binds registration to the issuer; **storage keying is ours** (§6.2) |
| "**MUST** keep refresh tokens confidential in transit and storage"; "implement secure token storage" | **us** (§6.2) |
| Redirect URIs "**MUST** be either `localhost` or use HTTPS"; use and verify `state` | **us** for the URI (§7), SDK for `state` |
| `Authorization: Bearer` on every request, never in the query string | SDK |

There is **no device flow** in core. The only non-browser path is the draft *OAuth Client
Credentials* extension (pre-registered machine credentials); it and *Enterprise-Managed
Authorization* are out of scope (§10).

---

## 5. Layering

### 5.1 Who does what

| Layer | Owns | Does not own |
|---|---|---|
| `agentao/mcp/` (harness) | building the provider for a URL server; the storage; the `needs_auth` status; refusing interactive flows outside an explicit login; one `login(name, ui)` entry point | opening a browser, binding a port, reading a terminal |
| An auth UI object (host-supplied) | `open(url)`, `wait_for_callback() -> (code, state, iss)` | storage, discovery, refresh |
| CLI | an auth UI: loopback listener + `webbrowser` + paste fallback; `/mcp login`, `/mcp logout`, status in `/mcp list` | — |
| Embedded host | optionally an auth UI; otherwise servers just report `needs_auth` | — |
| ACP session | nothing interactive — reports `needs_auth`; login happens in a terminal (§8.3) | — |

The auth UI is a small protocol in `agentao.host.protocols` (inbound surface, like `FileSystem`),
so a host can render the URL in its own UI and deliver the code however it receives it.

### 5.2 The invariant

> **Only `login()` can reach a `redirect_handler` that does anything.** Every other provider —
> the one used by startup `connect_all()`, by reconnects, and by tool calls — is constructed with a
> `redirect_handler` that raises `McpAuthRequired`.

Why it has to be enforced by construction rather than by a check at the call site: the SDK runs the
interactive flow **inline, inside `httpx.Auth.async_auth_flow`, on whatever request received the
401**. A tool call whose token expired and whose refresh failed would otherwise open a browser in
the middle of a turn — which is exactly what goose does (`streamable_http.rs:334,374`, step-up
reconnect) and what the July verdict forbade. With a raising handler, that request fails with
`McpAuthRequired`, the server moves to `needs_auth`, and the tool call returns an error the model
can read ("server X needs the user to log in again").

`McpAuthRequired` is raised inside the SDK's flow. Part of S2 (§9) is confirming it propagates out
of the transport as itself and not wrapped in an `ExceptionGroup`/`TaskGroup` message — the same
unwrapping `client.py:550` (`_first_failure`) already does for other connect failures.

### 5.3 When OAuth is used at all

- A **stdio** server never uses OAuth (spec: credentials come from the environment).
- A URL server whose `headers` already contain `Authorization` never uses OAuth; a 401 there is an
  ordinary failure. (Claude Code reports the same case as "failed", not "needs auth".)
- Otherwise OAuth is **on by default**: the provider is attached, and it does nothing until a
  request gets a 401. `"oauth": false` in the server's config turns it off.
- Optional per-server config, all keys optional:
  `"oauth": {"client_id", "client_secret", "scopes", "callback_port", "redirect_host"}`.
  `client_secret` supports `$VAR` expansion like `headers` and is never written back by `/mcp add`.

---

## 6. Storage and refresh

### 6.1 The bug class to design against

Across the five peers, the most frequent OAuth defect is the same one: **two refreshes spend one
rotating refresh token**, and the loser deletes or overwrites valid credentials. codex built a
cross-process refresh lock (PR #42413) and still has open races (#45944, #46028, #48507); gemini
deletes credentials on any refresh failure (#29048); opencode's whole-file rewrites drop other
servers' tokens (#46128, #42875). The SDK locks within one provider only. agentao has the
multi-process shape that triggers it: a CLI, an ACP server and an embedded host can share one
home directory.

### 6.2 Proposal

- **One file per (server, authorization server)**, not one shared file:
  `<home>/.agentao/mcp-oauth/<sha256(canonical server URL)>/<sha256(issuer)>.json`, mode 0600
  (directory 0700), written atomically (temp file + `os.replace`, like `LocalFileSystem`).
  Keyed by **URL and issuer, not by server name** — name-keyed storage (gemini, goose) makes tokens
  follow a renamed or re-pointed server; issuer keying is the 2026-07-28 "separate credentials per
  authorization server" rule. The file records the URL and issuer it was written for and is
  ignored on mismatch (opencode's `getForUrl` guard).
- **A `filelock` per file around read → refresh → write.** Before refreshing, re-read the file:
  if another process already rotated the token, use the new one instead of refreshing again.
- **Never delete credentials because a refresh failed.** Distinguish (codex PR #43947):
  the refresh endpoint said the grant is invalid → `needs_auth`, keep the file until a login
  replaces it; a transport error or 5xx → an ordinary connect error, keep everything.
- **Refresh only when there is a refresh token**; an access token without one is used until it
  expires (goose PR #11324 wiped year-long tokens by refreshing unconditionally).
- **No OS keyring.** It would add a dependency and a failure mode peers keep hitting (codex #34943,
  #41071, #32799). The 0600 file is the same posture as `~/.agentao/memory.db` and the provider
  credentials in `.env`. A host that wants a keyring injects its own `TokenStorage` factory.
- **Logs.** Token values must never reach `agentao.log`. `secret_scan` already redacts `Bearer`
  values; the PR adds patterns for `access_token` / `refresh_token` / `code` in JSON and form
  bodies, and a test that a full login against the fake AS (§12) leaves none of them in the log.

### 6.3 Client registration inputs

In the spec's order: a configured `client_id` (+ `client_secret`) → CIMD, **only if agentao
publishes a client metadata document (D2)** → DCR with `application_type: "native"` and
`grant_types` including `refresh_token`. Without D2, real-world order is configured → DCR, which is
what gemini-cli and opencode ship today.

---

## 7. The CLI's auth UI

| Choice | Proposal | Peer evidence |
|---|---|---|
| Bind address | `127.0.0.1` only, always | gemini binds all interfaces (`oauth-flow.ts:369`); opencode #41255 |
| Port | OS-assigned by default; `callback_port` for identity providers that need an exact registered URI | goose PR #9209, Claude Code `--callback-port` |
| Redirect host | `localhost` by default, `redirect_host` to override | Claude Code v2.1.229 switched to `127.0.0.1` and broke exact-match servers, reverted in v2.1.231 |
| Port in use | fail with a message naming the port; never assume another process owns it | opencode's 19876 "busy means another opencode" lets the callback land in a different process |
| Path | `/callback/<server-id>`, so two concurrent logins cannot receive each other's code | codex `oauth_callback.rs` (mix-up defence when the AS lacks RFC 9207) |
| Timeout | 300 s, then close the listener | goose PR #9536 (WSL hang), gemini #28279 (listener left open) |
| No browser / SSH | if `webbrowser.open` fails, or with `--no-browser`: print the URL and accept the pasted redirect URL, hidden input, size-bounded, while the listener keeps waiting | codex PR #44629; Claude Code docs |

Commands: `/mcp login <name>`, `/mcp logout <name>` (deletes that server's files), and `/mcp list`
shows `needs login` next to the status. A non-interactive `agentao mcp login <name>` subcommand
covers the ACP case (§8.3).

---

## 8. Surfaces

### 8.1 CLI startup

`connect_all()` runs with the raising handler. A server needing login is listed once at startup
("`linear` needs login — run `/mcp login linear`"), like codex and opencode. No browser opens at
startup — goose's "always try unauthenticated first" path (PR #8386) re-opened the browser on every
session, and stored credentials are attached before the first request here.

### 8.2 Embedded hosts

New public surface, deliberately small: `Agentao.mcp_login(name, ui)` (or on the manager — D5) and
`needs_auth` in `get_server_status()`. A host that supplies no UI gets exactly today's behaviour
plus an accurate status. New `Agentao.__init__` parameters, if any (a `TokenStorage` factory), go
after the `*`.

### 8.3 ACP

An ACP session must not start a flow: the editor drives the agent over stdio, and there is no
terminal to read from (gemini's headless consent reads **stdin**, which over a stdio protocol would
read the JSON-RPC channel). Servers report `needs_auth`; the user runs `agentao mcp login <name>`
in a terminal, which writes the same per-server file the ACP process then reads on its next
connect. Servers the **editor** passes in `session/new` carry the editor's own headers and are not
agentao's to authorize.

---

## 9. Spikes before building

| # | Question | Why it matters | How to answer |
|---|---|---|---|
| **S1** | The SDK holds the provider's lock from sending a request until its response **headers** arrive (verified: `async_auth_flow` is overridden, so httpx's `requires_response_body` read never runs). On a server that answers a tool call with `application/json` after the tool finishes, are parallel calls serialized? | #241 made parallel tool calls over one session real; this would silently undo it for OAuth servers only | A two-tool server in JSON-response mode and in SSE mode, each tool sleeping 2 s, two concurrent calls, both SDK majors. If serialized: wrap the provider so the lock covers only token acquisition, and report upstream |
| **S2** | Does `McpAuthRequired` raised in `redirect_handler` surface as itself? Does the SDK distinguish "refresh rejected" from "refresh request failed"? | §5.2 and §6.2 depend on it | Fake AS (§12) returning `invalid_grant`, 503, and a dropped connection |
| **S3** | Does legacy SSE behave the same with `auth=`? | SSE is deprecated in 2026-07-28 but still supported here | Same fake, `type: "sse"` |
| **S4** | Cross-process refresh with a rotating token | §6.1 | Two processes, a token that expires immediately, a refresh endpoint that invalidates the old token on use |

---

## 10. Out of scope

- The *Client Credentials* (draft) and *Enterprise-Managed Authorization* extensions.
- Device flow (not in the spec), OS keyring storage (§6.2), OAuth for stdio servers (spec says no).
- Authorizing servers an ACP editor passes in (§8.3).
- Any change to how static `headers` work.

---

## 11. Decisions for the maintainer

| # | Decision | Options | Recommendation |
|---|---|---|---|
| **D1** | Accept the revised boundary (harness: provider, storage, invariant; host: UI; CLI implements a UI) in place of the July "principle only" verdict | accept / keep July verdict | Accept, and update `openworker-borrow-review.zh.md` §9 to point here |
| **D2** | Publish a CIMD document (e.g. `https://agentao.cn/oauth/client.json`) | yes / no | Not in the first PR. It needs a stable HTTPS URL we commit to and `redirect_uris` that match every callback shape we use (opencode #50510 shipped one that didn't). DCR works today: gemini-cli and opencode ship DCR-only, and codex and goose fall back to it |
| **D3** | OAuth on by default for URL servers without an `Authorization` header | default on (attach provider, act only on 401) / opt-in per server | Default on — it does nothing unless the server asks |
| **D4** | Require the 2.x SDK for OAuth | require ≥2 / support both with documented 1.x gaps | Support both; on 1.x log once that `iss` validation and step-up scope union are unavailable. Requiring 2.x would make OAuth depend on a lock-file choice users don't see |
| **D5** | Where the public login entry point lives | `Agentao.mcp_login` / `McpClientManager.login` | `McpClientManager.login`, re-exported through `Agentao` only if a host asks — the manager already owns connect/disconnect |

---

## 12. Test plan

- **A fake authorization server and protected MCP server built from the SDK's own server-side auth
  (`mcp.server.auth`)**, run in-process — no `MagicMock` for anything the SDK parses (the
  `mcp-sdk-2x-compat` lesson). Both SDK majors in CI, as the existing `mcp-compat` job does.
- The invariant: every path except `login()` — startup connect, reconnect, tool call after expiry,
  403 step-up — ends in `needs_auth` with the UI's `open` never called. One test per path, plus a
  mutation check that passing the real handler anywhere else turns one of them red.
- Storage: 0600/0700 modes (skip on Windows, assert the file is not world-readable there via ACL
  only if cheap); URL/issuer mismatch ignored; atomic write under a killed writer; S4's
  two-process race.
- Logs: a full login leaves no token, code or verifier in `agentao.log`.
- CLI UI: bind address is loopback; port-in-use message; timeout closes the listener; paste path
  with hidden input.

## 13. Phasing

1. **Spikes S1–S4** — answers go back into this document as rev 2.
2. **PR 1 — harness**: provider wiring for both transports, storage, `needs_auth`, the invariant,
   `McpClientManager.login(name, ui)`. Usable by embedded hosts.
3. **PR 2 — CLI**: the loopback/paste UI, `/mcp login|logout`, status in `/mcp list`,
   `agentao mcp login`.
4. **PR 3 — docs**: configuration reference §4 (`oauth` keys), CLI handbook ch. 8, developer guide,
   both languages; CHANGELOG.
