# MCP OAuth — Design

**Status:** **Approved (2026-10-02)** — the go-ahead and every §11 recommendation. **PR 1
implemented** (auth module and record store, merged as #398) and **PR 2 implemented** (CLI login
loop); see the *Implementation records* in Appendix A. **PR 3 implemented** (docs, §13.5). **Credential profiles** (`oauth.profile`, two accounts on
one URL) added for 0.5.11, §6.4. Design history: proposal rev 8 (2026-10-02). Four design-review rounds passed it; a reverse review
of rev 5 (→ rev 6) and an external review of rev 6 (→ rev 7, two P1s: auth failures are invisible
once the transport has handled them, so the verdict must come from the auth object on every
connection) were folded in, and its second round (→ rev 8: a shielded refresh must also survive
the manager's shutdown). Its verdict: implement once that is fixed, no wider scope (Appendix A). §13 is the
implementation plan.

**What this adds.** Native OAuth for remote MCP servers: the user adds a server URL, runs
`/mcp login <name>` once, and agentao stores the credential, refreshes it and reconnects — in the
CLI, in `agentao run`, in an ACP session and in an embedded host, without a browser ever opening
on its own.

**Where it comes from.** It revisits `openworker-borrow-review.zh.md` §9 (rev 3, 2026-07-29), which
downgraded MCP OAuth to a single principle because it "needs a loopback HTTP route and a browser —
host-side, and agentao should have neither". This design keeps that boundary — the browser and the
callback stay with the host, and agentao's own CLI is one such host — and shows it no longer
implies "don't build it" (§1). How the design got here, round by round, is in Appendix A.

**Readers:** agentao maintainers; anyone touching `agentao/mcp/`. **Chinese twin:**
`mcp-oauth.zh.md`, kept in step with this file.

**Sources** (all fetched 2026-10-02): the MCP specification raw `.mdx` files for revisions
2025-11-25 and 2026-07-28 (`basic/authorization/…`) and the `ext-auth` repository; the installed
SDKs (`mcp` 2.0.0 in `uv.lock`, 1.26.0 and 1.30.0 via `uv run --with`); python-sdk issues and PRs
cited by number; peer source at codex `4dd51f4a`, gemini-cli `fb972b2f`, goose `b9db895a`, opencode
`1ddb0873`, pi-mono `8562bcf6`; Claude Code's public MCP docs. Spec wording below is paraphrased
unless in quotation marks, where it is literal from the raw file.

---

## TL;DR

- **The gap.** agentao's MCP client has no authorization beyond static `headers`
  (`mcp/config.py:34`). A remote server that requires OAuth — increasingly the default for hosted
  Streamable HTTP servers — cannot be used at all. Every peer we compare against (codex, gemini-cli,
  goose, opencode, Claude Code) supports it.
- **The SDK does the login, not the steady state.** `mcp.client.auth.OAuthClientProvider` (both
  majors) correctly does discovery, client registration, PKCE, `resource`, `iss` (2.x) and the code
  exchange. Its handling of a connection that is already logged in is what fails (§3.2, all
  measured or read from source, all open upstream).
- **The split.** *Login* — explicit, rare — runs the SDK provider on a dedicated connection, with a
  host-supplied UI that opens the browser and receives the callback. *Every other connection* uses
  agentao's `StoredTokenAuth`: attach the stored access token; refresh it under a cross-process file
  lock shortly before expiry or once on a 401; a rejected refresh means `needs_auth`, a failed one
  means an ordinary error. It never constructs the SDK provider, so it **cannot** start an
  interactive flow — the July principle, enforced by construction.
- **The host boundary** is unchanged from July: the CLI implements the UI (`/mcp login`); an ACP
  session never does and reports `needs_auth` instead. A public login API for embedded hosts is
  deferred until one asks (§8.2).
- **Eight decisions** for the maintainer (§11), each with a recommendation. D6 is whether agentao
  owns the steady-state auth (recommended) or subclasses the SDK provider; D7 is whether `login()`
  may widen scopes by patching one SDK function (recommended: not in the first version); D8 is
  whether a server's tools are registered at runtime after its first login (recommended: not in the
  first version — restart, as `/mcp add` already asks).
- **The plan** (§13): three PRs — auth module and record store, CLI login loop, docs — merged to
  `main` in that order and released together, with three limitations stated in the docs. It
  assumes every §11 recommendation; a different answer changes the PR it names.

---

## 1. What changed since the 2026-07-29 verdict

The verdict was right about *where* the browser and the callback belong, and this design does not
move them. What changed is the cost and the demand on the agentao side:

1. **The SDK does the interactive login.** In July the alternative looked like re-implementing
   OAuth 2.1 + RFC 9728/8414/8707/7636. It isn't: the SDK's provider performs the whole
   authorization-code flow on both majors; the harness supplies storage and a UI.
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
| Static headers work end to end, with `$VAR` expansion | `mcp/config.py:34,250-251`; `client.py:890` (`_prepare_url_connect`) |
| The content-type preflight uses plain `httpx` on both SDK majors | `client.py:850-863` (`import httpx` → `httpx.AsyncClient`) — not `httpx_for_mcp` |
| On mcp 2.0 a request's 401 reaches agentao as `"Server returned an error response"` — status code and headers gone | `mcp/client/streamable_http.py:342-370` (2.0.0); 1.26 uses `raise_for_status()` |
| A tool call's 401/403 is reported as text and leaves the status `CONNECTED` | `client.py:69-80` (`McpErrorKind.AUTH`, string markers), `call_tool`'s AUTH arm |
| MCP tools are registered once, at agent construction; `/mcp add` asks for a restart | `tooling/mcp_tools.py` (`init_mcp` → `register_mcp_tools`); `cli/commands/mcp.py:107` |
| All MCP I/O runs on one event loop thread shared by every server | `client.py:1335` (`agentao-mcp-loop`) |
| The content-type preflight passes a 401 through to the real handshake | `client.py:872-875` ("a 4xx/5xx may be an auth challenge") |
| Server status has no "needs auth" state; a failed connect is `ERROR` | `client.py:261-265`, `:551` |
| A connect failure is unwrapped from its `ExceptionGroup` before it is reported | `client.py:550` (`_first_failure`) |
| Streamable HTTP builds its own httpx client and passes it to the SDK | `client.py:978-993` (`create_mcp_http_client` → `streamable_http_client(http_client=…)`) |
| SSE passes `headers` to `sse_client`, which also accepts `auth=` | `client.py:928-934`; SDK signature (both majors) |
| Bearer values are redacted in `agentao.log` | `security/secret_scan.py:72-77` |
| `filelock` is a core dependency | `pyproject.toml:51` |
| CLI `/mcp` has `list`, `add`, `remove` only | `cli/commands/mcp.py:21,50,109` |
| In ACP, the editor supplies MCP servers per session | `acp/mcp_translate.py`, `acp/schema.py:191` |

---

## 3. What the SDK provides, per major

### 3.1 Surface

Verified by reading the installed source and by `inspect.signature` on each version.

| | mcp 1.26.0 / 1.30.0 | mcp 2.0.0 |
|---|---|---|
| Provider | `OAuthClientProvider(httpx.Auth)` (1.30: via `RedirectAwareAuth`) | `OAuthClientProvider(httpx2.Auth)` |
| Constructor | `server_url, client_metadata, storage, redirect_handler, callback_handler, timeout=300.0, client_metadata_url` | same, minus `timeout`, plus `validate_resource_url` |
| `callback_handler` returns | `tuple[code, state]` | `AuthorizationCodeResult(code, state, iss)` |
| RFC 9207 `iss` validation | no | yes (`validate_authorization_response_iss`) |
| Issuer-bound registration (SEP-2352): the issuer is stored in the client information and compared by exact string after discovering the current AS | **1.26: no** (no `issuer` field, no `credentials_match_issuer`); 1.30: yes | yes (`utils.py:337-354`) — **but see the two caveats below** |
| Step-up scopes | replaced | union of previous and challenged (2026-07-28 rule) |
| Transport hook | `streamablehttp_client(auth=)`, `sse_client(auth=)`; `streamable_http_client` takes a pre-built client | `streamable_http_client(http_client=)`, `sse_client(auth=)` |

`TokenStorage` is four async methods (`get/set_tokens`, `get/set_client_info`) on one instance per
provider; keying is the caller's job.

Two caveats on issuer binding (2.0.0, read from source in the rev-6 reverse review):

- **An empty issuer matches every issuer.** `credentials_match_issuer` returns `True` when
  `client_info.issuer is None` (`utils.py:349-353`: "carry no binding to enforce and are left
  as-is"). The SDK leaves it empty when a DCR registration went to the resource-origin `/register`
  fallback rather than the AS's published `registration_endpoint` (`oauth2.py:727-739`), and every
  registration made under 1.26 has no such field at all.
- **The bound value is `context.auth_server_url`, not `oauth_metadata.issuer`.** The stamp is
  `auth_server_url or str(oauth_metadata.issuer)` (`oauth2.py:703`) and the comparison is against
  `auth_server_url` (`:641`) — the string from Protected Resource Metadata's
  `authorization_servers[0]`. The RFC 9207 `iss` check, by contrast, compares against
  `oauth_metadata.issuer` (`utils.py:250`).

### 3.2 Steady-state defects (why rev 2 does not use the provider for ordinary connections)

| # | Defect | Evidence | Upstream (open unless noted) |
|---|---|---|---|
| **F1** | `context.lock` is held from sending a request until its **response headers** arrive. A server that answers a tool call in JSON mode sends headers when the tool finishes, so parallel calls run one after another — undoing #241 for those servers | §9 S1: two concurrent 2-s calls take **2.0 s without the provider and 4.0 s with it**, JSON mode, on 2.0.0, 1.26.0 and 1.30.0; SSE mode unaffected. Narrowing the lock to token load + header brings it back to 2.0 s on both majors | PR #2858 (rebase of closed #2660). Its head does not currently import (`MCP_PROTOCOL_VERSION` moved on `main`) |
| **F2** | The same lock is an `anyio.Lock` held across a `yield`; when httpx drives the generator from another task it raises `RuntimeError: The current task is not holding this lock` | Production report on 1.26.0 with several OAuth servers connecting concurrently — agentao's shape (#241/#243) | Issue #2847, fixed by the same #2858 |
| **F3** | `_initialize()` loads stored tokens but never restores their expiry, so after a restart an expired access token is treated as valid and sent | `oauth2.py:550-554` (2.0), same on 1.26; `update_token_expiry` is only called on fresh token responses (`:486,541`) | Issue #3250, #1318; PR #1784, #2492 |
| **F4** | The 401 branch goes straight to discovery and the interactive flow; it never tries the stored refresh token | §9 S2: stored expired token + refresh token → `redirect_handler` called, fake AS's `/token` **never hit**, on 2.0.0 and 1.26.0 | PR #2875 |

F3 + F4 together mean: with the provider as-is, every new process whose access token has expired
(typically after an hour) needs an interactive login. In a background context that is
`needs_auth` on every CLI start and every ACP session; in a careless design it is a browser
opening on every start (goose PR #8386 was exactly this).

What the provider does well — and what login uses it for — is the authorization-code flow itself:
discovery with the spec's fallback order, registration (pre-registered / CIMD / DCR), PKCE,
`resource`, `state` and (2.x) `iss` checks, and the code exchange.

---

## 4. What the spec requires of a client (2026-07-28, core)

Authorization is optional in MCP ("Authorization is **OPTIONAL** for MCP implementations"), applies
to HTTP transports only (stdio "**SHOULD NOT** follow this specification, and instead retrieve
credentials from the environment"), and static headers are not forbidden. When a client does
OAuth, the duties that matter here, and which side of the rev-2 split meets each:

| Duty | Met by |
|---|---|
| Discover the authorization server from Protected Resource Metadata (`WWW-Authenticate` `resource_metadata`, then well-known fallbacks); support RFC 8414 and OIDC discovery; reject metadata whose `issuer` differs from the URL it was fetched from | login (SDK) |
| Registration order: pre-registered → CIMD (if `client_id_metadata_document_supported`) → DCR (deprecated in 2026-07-28) → ask the user | login (SDK), given our inputs (§6.3) |
| DCR with `application_type: "native"` for CLI/desktop clients | login — client metadata we pass |
| PKCE S256; refuse if the AS does not advertise `code_challenge_methods_supported` | login (SDK) |
| `resource` (RFC 8707) on authorization **and token** requests | login (SDK); **refresh (ours)** — sent on every refresh |
| Scope: the `WWW-Authenticate` challenge is authoritative; on 403 `insufficient_scope`, re-authorize with the union, a few times at most | **partly.** Login requests what the 401 challenge names (SDK). Step-up union is not supported in the first version (D7); a 403 `insufficient_scope` becomes `needs_auth` with a message that says so (§5.3) — never an automatic retry loop |
| RFC 9207 `iss` check on the authorization response | login (SDK, 2.x only — D4) |
| Separate credentials per authorization server; re-register when it changes | the record is bound to one issuer and replaced by a login against another (§6.2); registration reuse is decided by the SDK's issuer binding where it exists, and not attempted where it does not (§5.5) |
| "**MUST** keep refresh tokens confidential in transit and storage"; "implement secure token storage" | storage (§6.2) |
| "**MUST NOT** assume refresh tokens will be issued" | refresh (ours): no refresh token → use the access token until it expires, then `needs_auth` |
| Redirect URIs "**MUST** be either `localhost` or use HTTPS"; use and verify `state` | CLI UI (§7) for the URI; SDK for `state` |
| `Authorization: Bearer` on every request, never in the query string | steady state (ours) |

There is **no device flow** in core. The only non-browser path is the draft *OAuth Client
Credentials* extension (pre-registered machine credentials); it and *Enterprise-Managed
Authorization* are out of scope (§10).

---

## 5. Layering

### 5.1 Who does what

| Part | Owns | Does not own |
|---|---|---|
| Auth module (`agentao/mcp/oauth.py`, internal) | `StoredTokenAuth` for ordinary connections; `login(name, ui)`, which runs the SDK provider once and writes the record; `logout(name)`; the `needs_auth` status | opening a browser, binding a port, reading a terminal |
| Record store (internal) | one record per server URL, the per-record lock, atomic writes (§6) | deciding when to refresh |
| CLI login loop | the UI: bind a loopback listener, open the browser or print the URL, accept a paste; `/mcp login`, `/mcp logout`, status in `/mcp list`; `agentao mcp login` | storage, discovery, refresh |
| ACP session | nothing interactive — reports `needs_auth`; login happens in a terminal (§8.3) | — |

The UI is an internal interface with four steps — `prepare(preferred_port) -> redirect_uri`,
`open(authorization_url)`, `wait() -> (code, state, iss)`, `close()` — so the CLI can implement it
now and an embedded host can be offered it later without a redesign (§8.2).

### 5.2 The invariant

> **Only `login()` constructs an `OAuthClientProvider`.** Ordinary connections — startup
> `connect_all()`, reconnects, tool calls — carry `StoredTokenAuth`, which has no code path that
> reaches a browser, a callback or a terminal.

Rev 1 enforced this by giving background providers a `redirect_handler` that raises. S2 showed why
that is the weaker form: the SDK runs the interactive flow inline on whatever request got the 401,
so a background connection would still perform discovery and (with DCR) **register a new client**
before reaching the raising handler — goose's "6+ client_ids in a day" (PR #11324) is that cost
repeated per start. Not constructing the provider at all removes the path, not just its last step.

`login()` keeps the raising handler only as a guard for its own timeout and cancellation. S2 also
confirmed what a handler's exception looks like from the outside: on both majors it arrives as
`ExceptionGroup("unhandled errors in a TaskGroup")` wrapping the original, so `login()` unwraps it
with the same `_first_failure` the connect path uses (`client.py:550`).

### 5.3 `StoredTokenAuth` (steady state)

An `httpx.Auth` (or `httpx2.Auth` — whichever `_compat` resolves for the installed SDK) built from
the server's stored credential record (§6.2):

1. **Before each request:** if the access token expires within 60 s and a refresh token exists,
   refresh first (step 3). Attach `Authorization: Bearer`. Hold an in-process lock only for this
   step — never across the request (the F1/F2 lesson).
2. **On 401:** if the token was refreshed by another request or process since it was attached,
   retry once with the new one; otherwise refresh once and retry once. A second 401 → `needs_auth`.
   "`needs_auth`" from inside the auth flow means: record the verdict on the server's `McpClient`
   (a flag the auth object is constructed with a reference to) and let the 401 response through.
   **The auth object is the only place the 401 is still visible.** On mcp 2.0 the Streamable HTTP
   transport turns any non-404 status ≥ 400 on a request into
   `ErrorData(INTERNAL_ERROR, "Server returned an error response")` (`streamable_http.py:342-370`)
   — status code and headers gone; on 1.26 it is `raise_for_status()`, whose text says `401` but
   whose headers do not reach `_fail_connect` either. So nothing downstream of the transport may
   decide "auth" from the error: not `_fail_connect`, and not `classify_mcp_error`, whose string
   markers (`client.py:69-80`) file the 2.0 text under `OTHER`. The verdict is read **before**
   any classification at the two exits:
   - `_fail_connect` checks the flag first; set → `NEEDS_AUTH`, otherwise the existing path.
   - `call_tool`'s single `except Exception` (the one that today calls `classify_mcp_error`) checks
     the flag first; set → `NEEDS_AUTH` and the "run `/mcp login <name>`" hint, no reconnect, no
     retry. Only an unset flag goes on to the string classification. Reading it in the AUTH arm
     alone, as rev 6 did, misses every 2.0 auth failure — that arm is never reached.

   The flag is cleared when a connect starts (`connect()`), so a server that was logged in again
   does not carry the old verdict into its new session.
3. **Refresh** — single-flight per server in process, and **the record's `filelock` held from the
   re-read to the write**, across processes. Under the lock: re-read the record; if it is gone
   (logged out) → `needs_auth`; if another process already rotated the token → use it. Otherwise
   `POST token_endpoint` with `grant_type=refresh_token`, `refresh_token`, `resource`, the client's
   registered authentication method, and handle the outcome:
   - Success → **merge** the response into the record, never replace it, and write it atomically
     before releasing the lock:
     - `refresh_token` omitted → keep the old one (RFC 6749 §6 lets the server not rotate;
       replacing would make the first refresh succeed and the second impossible). The SDK 2.0.0
       keeps both (`oauth2.py:536,538`), and so does pi (`packages/mcp/src/oauth/flow.ts:263` @
       `1c1e9c0e`: `{ refresh_token: options.refreshToken, ...tokens }`).
     - `scope` omitted → keep the previously granted scope (RFC 6749 §5.1: omitted means unchanged).
     - `expires_in` omitted → expiry unknown: use the token until a 401.
     - `token_type` other than `Bearer` (case-insensitive) → treat as a failed refresh.
   - `400 invalid_grant` (or `invalid_client`) → `needs_auth`; **the record is kept** until a login
     replaces it.
   - Network error, timeout, 5xx → an ordinary connect/tool error; **the record is kept**; nothing
     is marked `needs_auth`.

   Because the lock spans the network request, a logout or a login's commit waits for an in-flight
   refresh and then applies on top of its result; neither can be overwritten by it. The token
   request has its own timeout, which bounds that wait.

   **Where the lock is taken.** This flow runs on `agentao-mcp-loop` (`client.py:1335`), the one
   event loop every MCP server in the process shares, and two properties of `filelock.FileLock`
   make the obvious spelling wrong there:
   - A blocking `acquire()` on the loop thread stalls **every** server's I/O for as long as another
     process holds the record — up to its token request's timeout.
   - `FileLock` is **reentrant per thread** (`thread_local=True` by default). Measured in the
     rev-6 reverse review on filelock 3.25.2: two coroutines on one thread, one `FileLock`
     instance, both print "acquired" while the other still holds it. Anything else on the loop
     thread that takes the same instance is not excluded.

   **One scheme, chosen** (rev 7; rev 6 left two open):
   - **Every record write runs on the MCP loop.** Refresh already does; a login's commit and
     `logout` are submitted to it the way every other manager call is (`run_coroutine_threadsafe`),
     so all three holders live on one thread.
   - **In one manager:** one `asyncio.Lock` per record, owned by the `McpClientManager` and bound
     to its loop. Every holder takes it before the file lock, so the file lock's reentrant counter
     is never consulted twice on that loop. The refresh single-flight is this lock: a request that
     finds it held waits and then re-reads the record. Each manager runs its own loop (one manager per
     `Agentao` that has MCP servers — sub-agents connect none), so these locks are never shared between managers,
     and each manager constructs its own `FileLock` instances.
   - **Between managers in one process** — different loops, different threads — the file lock is
     the exclusion, the same as between processes: two `FileLock` instances on one path, held from
     two threads, exclude each other (measured: the second `acquire(timeout=0)` gets `Timeout`).
   - **Across processes:** `FileLock(path).acquire(timeout=0)` polled — on `filelock.Timeout`,
     `await asyncio.sleep(0.05)` and try again, up to a deadline of the token request's timeout
     plus a margin; past it, an ordinary error (not `needs_auth`). No thread, no blocking call on
     the loop. Measured: a second process gets `Timeout` from `acquire(timeout=0)` immediately on
     filelock 3.0.0 and 3.25.2; PR 1 raises the floor from `>=1.4.0` to `>=3.0` rather than test
     against a version from 2017.

   **Cancellation.** Both locks are released in one `finally`; a waiter cancelled while polling
   holds nothing. Once held, the critical section — re-read, token request, write — runs as its own
   task under `asyncio.shield`, bounded by the token request's timeout, so a cancelled tool call
   cannot abandon a rotation between spending the old refresh token and writing the new one (that
   gap is §6.1's bug class). The cancelled caller returns at once; the shielded task finishes,
   writes and releases. A logout or a login's commit is never cancelled mid-write for the same
   reason.

   **Shutdown.** `shield` protects against a caller's cancel, not against the loop stopping. The
   manager's close (`client.py:1512-1543`, `_shutdown`) waits only for the call tasks in
   `self._calls`, then disconnects the clients and stops the loop — a shielded critical section is
   not among them, so a tool call cancelled mid-refresh followed at once by `disconnect_all()`
   stops the loop with the rotation unwritten (the external review of rev 7 reproduced this on the
   current manager). So the manager **owns** these tasks: every shielded critical section is
   registered in a manager-held set (removed on completion), and `_shutdown` waits for that set
   — bounded by the token request's timeout — **after** the calls and **before** the client
   disconnects and the loop stop. `disconnect_all`'s outer deadline
   (`timeout + _CANCEL_WAIT_S + _OWNER_STOP_S + 1.0` today, `:1488`) gains that same bound, or the
   thread join would give up and stop the loop under the wait it just added. No new scheduler: one
   set and one `asyncio.wait`, next to the existing one for calls.

   What this cannot cover is the process dying (a second Ctrl+C, a kill) between the token
   response and the write. The outcome is bounded: the record still holds the old, now-spent
   refresh token, the next refresh gets `invalid_grant`, and the server reports `needs_auth` with
   the record kept (§5.3 step 3) — one extra login, never a corrupted or deleted record.
4. **On 403 with `insufficient_scope`:** `needs_auth`, with a message that names the challenged
   scope and says plainly that this version cannot request extra scopes: logging in again requests
   what the server's 401 challenge names, which may not include it, so a re-login may not clear the
   error. No automatic re-login, no retry loop. (Step-up union is D7.)

This is the part peers converge on independently: codex and goose refresh 30 s before expiry at
connect, gemini 5 min; codex separates "cannot refresh" from "refresh failed" (PR #43947). Its size
is roughly one module of ~200 lines against the httpx Auth protocol, which both httpx majors share.

### 5.4 When OAuth is used at all

- A **stdio** server never uses OAuth (spec: credentials come from the environment).
- A URL server whose `headers` already contain `Authorization` never uses OAuth; a 401 there is an
  ordinary failure. (Claude Code reports the same case as "failed", not "needs auth".)
- Otherwise **`StoredTokenAuth` is always attached**, record or not. With a record it attaches
  and refreshes the token (§5.3). Without one it sends no `Authorization` and only **observes**:
  a 401 carrying a `WWW-Authenticate: Bearer` challenge sets the `needs_auth` verdict (§5.3 step 2)
  and the server reports `needs_auth`. Rev 6 attached it only when a record existed and expected
  `_fail_connect` to spot the challenge — but by then the transport has discarded status and
  headers (2.0: "Server returned an error response"), so a first connect could never be told
  apart from any other failure. Observing in the auth object needs no extra probe request. A 401
  without the challenge — typically a server that wants an API-key header the user has not
  configured — sets nothing and stays `ERROR`: telling that user to run `/mcp login` sends them to
  a login that fails at discovery. `"oauth": false` in the server's config attaches nothing.
- The content-type preflight (`client.py:829`) **never carries auth.** It runs on plain `httpx`
  on both majors, while `StoredTokenAuth` is an `httpx2.Auth` on 2.x, so one object could not serve
  both; and it already lets every non-2xx through to the real handshake, so an unauthenticated
  probe of a protected server (401) is judged exactly as it is today. Attaching auth there would
  also let a probe trigger a refresh.
- Optional per-server config, all keys optional:
  `"oauth": {"client_id", "client_secret", "callback_port", "redirect_host"}`. There is no
  `scopes` key: the SDK replaces the requested scope with the 401 challenge's (§5.5 step 4), so the
  key would be accepted and ignored.
  `client_secret` supports `$VAR` expansion like `headers` and is never written back by `/mcp add`.

### 5.5 `login(name, ui)`

The sequence, each step chosen against an S5 observation or a review finding:

1. **Prepare the callback first.** `ui.prepare(preferred_port)` binds the listener and returns the
   actual `redirect_uri`. `preferred_port` is the port of the redirect URI the stored registration
   was made with, so a re-login normally reuses it; `callback_port` in config overrides it.
2. **Decide what registration to offer the SDK.** Only what can be judged *before* discovery is
   judged here — whether the `redirect_uri` from step 1 is among the stored registration's
   `redirect_uris`. The SDK does not check it (it always sends `redirect_uris[0]` of the metadata we
   pass, `oauth2.py:397,452`, and reuses whatever client info storage returns), so a changed port
   would otherwise reach the authorization server with a URI it never registered. If it is not: a
   configured `client_id` fails the login with a message to set `callback_port`; a dynamically
   registered one is not offered, so the SDK registers again.

   **Whether the registration belongs to the current authorization server is not judged here** — the
   current AS is only known after the SDK's discovery. Where the SDK binds registrations to an
   issuer (1.30, 2.0: it records the issuer in the client information and discards a mismatch after
   discovery, by exact string comparison), a stored dynamic registration is offered **only if its
   `issuer` is set**, and the SDK then decides. One whose `issuer` is empty is never offered: the
   SDK treats an empty issuer as matching every issuer (§3.1 caveats), so offering it would hand
   one AS's client to another — the case this step exists to rule out. That covers a registration
   the SDK made through the resource-origin `/register` fallback and every registration stored
   under 1.26, which matters when the installed SDK is upgraded under an existing record. On 1.26
   itself, which has no binding, a dynamically registered client is **never** offered: every login
   registers again. Logins are explicit and rare. A configured `client_id` is pre-registered by
   the user for this server and is always offered.
3. **Run the SDK provider with no tokens.** Its storage returns `None` from `get_tokens()` — old
   tokens are never loaded, so the first request carries no `Authorization`, the server answers
   401, and the flow runs. Loading them would let the handshake succeed with the old token and the
   login would silently do nothing. (S5: an empty store triggers DCR → authorize → token on both
   majors.)
4. **Scopes:** the SDK selects them from the 401 challenge and **overwrites** whatever the client
   metadata carried (`oauth2.py:691`; same call on 1.26). S5 measured it: metadata scope
   `a b step`, challenge `a` → the authorization URL asked for `a`. So `login()` requests what the
   server challenges for, and step-up union is D7.
5. **Build the record from the provider afterwards:** tokens from its storage; the token endpoint
   and supported auth methods from `provider.context.oauth_metadata`; and the issuer from
   `context.auth_server_url`, falling back to `str(oauth_metadata.issuer)` only when it is unset —
   the same expression the SDK stamps registrations with (`oauth2.py:703`), so the record and the
   registration it holds name the AS identically. These are internal attributes, present on 1.26
   and 2.0 (S5); `login()`
   reads them through a `_compat` probe and **fails the login** if they are missing, rather than
   writing a record that cannot refresh. The issuer is stored **exactly as the SDK reports it** and
   compared exactly — the SDK's own binding and RFC 9207 `iss` check are exact string comparisons
   (`utils.py:354`, `:254`), and RFC 8414 issuer identifiers are compared as strings. Normalising
   (say, dropping a trailing `/`) could make two different issuers' records look like one. The cost
   of not normalising is known and small: 1.26's model renders an issuer as `http://host:port/` where
   2.0 renders `http://host:port` (S5), so after switching SDK major the first login sees a
   different issuer, replaces the record and registers again.
6. **Commit the record under the record's lock.** Only this step holds it; the browser wait does
   not.
7. **Close the UI** (listener closed on success, failure, timeout and cancel) and **reconnect** that
   server's ordinary connection. Reconnecting is enough when the server's tools were registered at
   startup (it was connected once, and a later refresh was rejected). It is **not** enough for a
   server that was `needs_auth` from the start: MCP tools are registered once, at agent
   construction (`tooling/mcp_tools.py::init_mcp`), so that server has no tools to bring back.
   The first version says so — "logged in; restart agentao to load `<name>`'s tools", the same
   instruction `/mcp add` gives (`cli/commands/mcp.py:107`). Registering tools at runtime is D8.

`logout(name)`: wait for the record's lock (an in-flight refresh finishes first), delete the
credential file, release; then disconnect the server and drop the in-memory `StoredTokenAuth`. A
refresh that starts afterwards re-reads under the lock, finds no record and reports `needs_auth` —
it cannot write a logged-out record back.

---

## 6. Storage

### 6.1 The bug class to design against

Across the five peers, the most frequent OAuth defect is the same one: **two refreshes spend one
rotating refresh token**, and the loser deletes or overwrites valid credentials. codex built a
cross-process refresh lock (PR #42413) and still has open races (#45944, #46028, #48507); gemini
deletes credentials on any refresh failure (#29048); opencode's whole-file rewrites drop other
servers' tokens (#46128, #42875). The SDK locks within one provider only. agentao has the
multi-process shape that triggers it: a CLI, an ACP server and an embedded host can share one
home directory.

### 6.2 The credential record

- **One file per server URL**: `user_root() / "mcp-oauth" / <sha256(canonical server URL)>.json`
  (`paths.user_root()`, i.e. `~/.agentao`, resolved the way every other user-scope file is),
  mode 0600 (directory 0700), written atomically (temp file + `os.replace`, like
  `LocalFileSystem`). Keyed by **URL, not by server name** — name-keyed storage (gemini, goose)
  makes tokens follow a renamed or re-pointed server.
- **Issuer is recorded and bound, not part of the key.** An ordinary connection only knows the URL,
  so a per-issuer file would leave it unable to choose between several. The 2026-07-28 rule
  ("separate credentials per authorization server") is met by never presenting a record's
  credentials to any other issuer: a login whose issuer differs from the record's **replaces** the
  record, and a server that moved to a new authorization server shows up in the steady state as a
  failed refresh → `needs_auth` → a login that replaces it. History across issuers is not kept in
  the first version.
- **Contents:** server URL, `resource`, issuer (as reported, §5.5 step 5), `token_endpoint` and the
  AS's supported token-endpoint auth methods, client information as registered (including its
  `redirect_uris`), access token, **absolute** expiry time, refresh token if any, granted scope. The absolute expiry and the token endpoint are what the SDK's storage protocol cannot
  carry (F3; upstream PR #2492 adds the latter) — which is why the record is ours, not a
  `TokenStorage`.
- The record names the URL it was written for and is ignored on mismatch (opencode's `getForUrl`
  guard).
- **One lock per record, for every write:** refresh (re-read → request → write), a login's commit
  and logout all take the record's `filelock` — behind a per-record in-process lock, and never by a
  blocking acquire on the MCP loop (§5.3, "Where the lock is taken") — so none of them can
  interleave (§5.3, §5.5). The lock
  file is separate from the credential file (`<hash>.json.lock`) and **survives the record's
  deletion**: deleting the lock file while another process waits on it would let two holders exist.
- **Never delete a record because a refresh failed** (§5.3). `logout` is the only delete.
- **No OS keyring.** It would add a dependency and a failure mode peers keep hitting (codex #34943,
  #41071, #32799). The 0600 file is the same posture as `~/.agentao/memory.db` and the provider
  credentials in `.env`.
- **Logs.** Token values must never reach `agentao.log`. `secret_scan` already redacts `Bearer`
  values; the PR adds patterns for `access_token` / `refresh_token` / `code` / `code_verifier` in
  JSON and form bodies, and a test that a full login and a refresh leave none of them in the log.
- **Which issuer.** The record's `issuer` is the value §5.5 step 5 defines — the SDK's binding
  key, `auth_server_url` — not `oauth_metadata.issuer`; "a login whose issuer differs replaces the
  record" compares that string.

### 6.3 Client registration inputs

In the spec's order: a configured `client_id` (+ `client_secret`) → CIMD, **only if agentao
publishes a client metadata document (D2)** → DCR with `application_type: "native"` and
`grant_types` including `refresh_token`. Without D2, real-world order is configured → DCR, which is
what gemini-cli and opencode ship today. The registered client is stored in the record and offered
to the next login only under the conditions of §5.5 step 2: the redirect URI still matches, the
SDK binds registrations to an issuer (1.30, 2.0), and the stored registration's `issuer` is set —
on 1.26, or with an empty `issuer`, a dynamic registration is never offered.

### 6.4 Credential profiles (0.5.11)

Keying by URL alone means one login per URL: two `mcp.json` entries pointing at one URL (a work and
a personal account), or two projects connecting to it, share whatever account logged in last,
because the store is per user. An optional **`oauth.profile`** names a separate credential for the
same URL.

- **Key.** No profile: the canonical URL, exactly as in §6.2, so every record written before
  profiles existed is found under the same file name and an upgrade logs nobody out. With a
  profile: the canonical URL, a newline, `profile=<name>`. No URL contains a newline, so no URL can
  spell another URL's profiled key. The file name is the SHA-256 of the key; the lock file and the
  in-process lock use the same key.
- **The record names its profile**, and a load checks it alongside the URL (the §6.2 guard), so a
  file copied to another credential's name is ignored. A record without the field is the default
  credential. The record format stays version 1: an older agentao never opens a profiled file,
  since its name differs.
- **Opt-in and explicit, not keyed by server name.** Pi 1.0 keys by server name + URL; §6.2
  already rejected name keys because tokens would follow a renamed or re-pointed entry, and renaming
  an entry would log it out. A profile is a name for the *account*, so entries in different
  projects can share one deliberately, and renaming an entry changes nothing.
- **No fallback.** An entry with a profile never reads the default credential, or another
  profile's: borrowing one would act as the wrong account. It starts at `needs login`.
- **Everything goes through the key.** Steady-state load and refresh (`StoredTokenAuth`), a login's
  load of the stored registration and its commit, logout, and the `needs_auth` re-check that wakes
  a server after a login in another process. Each profile's record keeps its own client
  registration.
- **Which account a login picks is the browser's.** agentao sends the same authorization request
  for every profile; an identity provider that is already signed in may complete it with that
  account. `--no-browser` prints the URL, which can be opened in another browser profile.
- **Validation**, fail-closed like the other keys: a non-empty string with no leading or trailing
  whitespace, compared exactly (`Work` and `work` are two profiles).

Tests: `tests/test_mcp_oauth_profiles.py`.

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

Commands: `/mcp login <name>`, `/mcp logout <name>` (deletes that server's record), and `/mcp
list` shows `needs login` next to the status. An `agentao mcp login <name>` subcommand — the same
interactive flow, run from a shell instead of the REPL — covers the ACP case (§8.3).

---

## 8. Surfaces

### 8.1 CLI startup

`connect_all()` uses `StoredTokenAuth` only. A server needing login is listed once at startup
("`linear` needs login — run `/mcp login linear`"), like codex and opencode. No browser opens at
startup, and a stored refresh token is used before anyone is asked to log in (F3/F4 are why this
has to be ours).

### 8.2 Embedded hosts

Deferred. An embedded host gets `StoredTokenAuth` and the `needs_auth` status for free (the record
store is shared with the CLI, so `agentao mcp login` in a terminal works for it too). A public
login entry point — the §5.1 UI interface moved into `agentao.host.protocols`, plus a record-store
factory parameter after the `*` in `Agentao.__init__` — waits for a host that needs to run the login
inside its own UI.

### 8.3 ACP

An ACP session must not start a flow: the editor drives the agent over stdio, and there is no
terminal to read from (gemini's headless consent reads **stdin**, which over a stdio protocol would
read the JSON-RPC channel). Servers from agentao's own `mcp.json` report `needs_auth`; the user runs
`agentao mcp login <name>` in a terminal, which writes the record the ACP process reads on its next
connect.

Servers the **editor** passes in `session/new` are the editor's to authorize. Rev 2 assumed they
carry the editor's headers; they need not — the translator only sets `headers` when the editor sent
some (`acp/mcp_translate.py:231-235`). Without an explicit opt-out, the default rule (§5.4) would
attach agentao's stored token to an editor-supplied server with the same URL. So the translator sets
`"oauth": false` on every server it produces.

## 9. Spike results

Scripts and fixtures live outside the repo (scratchpad); each is small enough to be rebuilt as a
test in the implementation PR.

### S1 — does the provider serialize parallel calls? **Yes, in JSON mode.**

A one-tool server (`slow`, sleeps 2 s) in JSON-response and SSE-response mode; a client making two
concurrent calls over one session, with and without the provider (valid stored token, interactive
handlers that raise).

| SDK | mode | no provider | provider | provider, lock narrowed |
|---|---|---|---|---|
| 2.0.0 | JSON | 2.05 s | **4.05 s** | 2.05 s |
| 2.0.0 | SSE | 2.05 s | 2.05 s | — |
| 1.26.0 | JSON | 2.02 s | **4.02 s** | 2.02 s |
| 1.26.0 | SSE | 2.02 s | 2.02 s | — |
| 1.30.0 | JSON | 2.02 s | **4.04 s** | — |
| 1.30.0 | SSE | 2.04 s | 2.03 s | — |

With the provider in JSON mode **both** calls complete at 4 s — the first call's result is held up
too. "Lock narrowed" is a test subclass that holds the lock only to load the token and add the
header; it shows the remedy, not production code. The upstream fix (#2858) could not be run: its
head fails to import against current `main`.

### S2 — what happens with an expired token after a restart? **Interactive login; refresh never tried.**

A fake origin serving PRM, AS metadata, DCR and a token endpoint, whose MCP endpoint always answers
401. The client's storage returns an expired access token **and a refresh token**; the token
endpoint was configured in turn to answer `400 invalid_grant`, `503`, and to drop the connection.

On 2.0.0 and 1.26.0, in all three configurations, the server saw the same four requests —
`GET /.well-known/oauth-authorization-server`, `POST /mcp` (with the stale token), `GET` PRM,
`GET` AS metadata — and then the client called `redirect_handler`. `/token` was never requested,
so the three refresh outcomes could not even be distinguished. The handler's exception surfaced as
an `ExceptionGroup` wrapping it. This is F3 + F4, and it is why refresh is ours (§5.3).

### S5 — what does a login run look like? **Full flow from an empty store; scope taken from the challenge.**

A fake origin serving PRM (`scopes_supported: a b step`), AS metadata, DCR, an `/authorize` that
redirects straight back with a code, and a token endpoint; the MCP endpoint answers 401 with
`scope="a"` until it sees the issued token. The redirect handler plays the browser (fetches the
authorization URL, reads the code from the 302). Same results on 2.0.0 and 1.26.0:

| Variant | Registration scope | Authorization `scope` | Notes |
|---|---|---|---|
| no metadata scope | `a` | `a` | DCR → authorize → token; `resource` sent on both |
| metadata scope `a b step` | `a` | `a` | **overwritten by the challenge** |
| scope-selection function patched to add `a b step` | `a b step` | `a b step` | works, but patches a module-level SDK function (D7) |

After the run, `context.auth_server_url`, `context.oauth_metadata` (issuer, token endpoint) and
`context.token_expiry_time` were populated on both majors; the issuer was `http://127.0.0.1:<port>`
on 2.0.0 and `http://127.0.0.1:<port>/` on 1.26.0.

### Still open

| # | Question | Plan |
|---|---|---|
| S3 | SSE transport with `StoredTokenAuth` | Same fake, `type: "sse"`; part of PR 1's tests. **Ran in PR 1: passes on 2.0.0, 1.26.0, 1.30.0** |
| S4 | Two processes refreshing one rotating token, and a refresh racing a logout | Two processes, a token that expires immediately, a token endpoint that invalidates the old refresh token on use; then a logout during a slow refresh; part of PR 1's tests. **Ran in PR 1: one token request, both processes end on the new token; logout waits for the refresh** |

## 10. Out of scope

- The *Client Credentials* (draft) and *Enterprise-Managed Authorization* extensions.
- Device flow (not in the spec), OS keyring storage (§6.2), OAuth for stdio servers (spec says no).
- Authorizing servers an ACP editor passes in (§8.3).
- Any change to how static `headers` work.
- Fixing F1–F4 upstream. Worth a comment on #2858 and #2875 with the S1/S2 numbers; not a
  dependency.

---

## 11. Decisions for the maintainer

| # | Decision | Options | Recommendation |
|---|---|---|---|
| **D1** | Accept the revised boundary (harness: steady-state auth, storage, invariant, `login()`; host: UI; CLI implements a UI) in place of the July "principle only" verdict | accept / keep July verdict | Accept, and update `openworker-borrow-review.zh.md` §9 to point here |
| **D2** | Publish a CIMD document (e.g. `https://agentao.cn/oauth/client.json`) | yes / no | Not in the first PR. It needs a stable HTTPS URL we commit to and `redirect_uris` that match every callback shape we use (opencode #50510 shipped one that didn't). DCR works today: gemini-cli and opencode ship DCR-only, and codex and goose fall back to it |
| **D3** | OAuth for URL servers without an `Authorization` header | default on (a 401 with a `Bearer` challenge marks `needs_auth`; a stored record is used) / opt-in per server | Default on — it does nothing unless the server asks, and a 401 without a `Bearer` challenge stays `ERROR` (§5.4) |
| **D4** | Require the 2.x SDK for OAuth | require ≥2 / support both with documented 1.x gaps | Support both; on 1.x log once at login that `iss` validation is unavailable (1.26 and 1.30 alike — verified), and on 1.26 that registrations are not reused (§5.5 step 2). The steady state is ours and identical on both |
| **D5** | Public login entry point for embedded hosts | now / when a host asks | When a host asks (§8.2). The first version is internal plus the CLI |
| **D6** | Steady-state auth | (a) ours, `StoredTokenAuth` (§5.3) · (b) subclass the SDK provider to fix F1–F4 · (c) use the provider as-is and wait for upstream | **(a).** (b) overrides private methods (`_initialize`, `async_auth_flow`) that differ between 1.26, 1.30 (`RedirectAwareAuth`) and 2.0 — the kind of coupling `_compat.py` exists to avoid. (c) ships F1–F4 to users: a re-login every hour per process, and serialized JSON-mode servers. With (a), the **steady state** touches only the public `httpx.Auth` protocol and the record we own; **login still reads the provider's internal `context`** (§5.5 step 5), behind a `_compat` probe that fails the login closed, and pinned by tests on both majors |
| **D7** | Step-up scope union at login | (a) request what the server's 401 challenges for; a 403 `insufficient_scope` names the missing scope in the status · (b) wrap `mcp.client.auth.oauth2.get_client_metadata_scopes` for the duration of `login()` to add the recorded scopes (S5: works on both majors) | **(a) first.** (b) patches a module-level function — process-wide, private, and needs a lock against concurrent logins. Revisit when a real server is found that challenges with fewer scopes on 401 than it later demands on 403 |
| **D8** | Tools of a server that needed login from startup | (a) register them at runtime after `login()` · (b) the login says "restart agentao to load its tools" | **(b) first.** MCP tools are registered once, at construction (`tooling/mcp_tools.py::init_mcp`), and `/mcp add` already asks for a restart. (a) is a runtime change to the tool registry — the tools block the model sees, the prompt-cache prefix, sub-agents spawned afterwards — that `/mcp add` would want too, and belongs in a change of its own. A server whose tools were registered (connected once, refresh later rejected) gets them back by the reconnect alone (§5.5 step 7) |

---

## 12. Test plan

- **A fake authorization server and protected MCP server**, in-process, extending the S2 fixture
  (PRM, AS metadata, DCR, `/authorize` issuing codes, `/token` with switchable behaviour). Protocol
  objects are the SDK's own models — no `MagicMock` for anything the SDK parses. Both SDK majors in
  CI, as the existing `mcp-compat` job does.
- **The invariant:** ordinary connections never construct `OAuthClientProvider` — asserted by
  patching its constructor to raise across startup connect, reconnect, a tool call after expiry,
  and a 403 step-up; each ends in `needs_auth` or success, never in the UI's `open`. A mutation
  check (construct it on reconnect) must turn a test red.
- **Restart with an expired token** (the S2 shape): the stored refresh token is used, `/token` is
  hit once, no `needs_auth`. Then each refresh outcome: `invalid_grant` → `needs_auth`, record kept;
  503 and dropped connection → ordinary error, record kept, not `needs_auth`.
- **Refresh merge:** a response without `refresh_token` keeps the old one, and a second refresh
  then succeeds; a response without `scope` keeps the granted scope; without `expires_in` the token
  is used until a 401.
- **Login:** old tokens are not loaded (a login with a valid old record still reaches `/authorize`);
  a changed callback port re-registers a DCR client and fails a configured one with the
  `callback_port` message; the record built after login has issuer, token endpoint and expiry on
  both majors, with the issuer stored as reported (a record written under 1.26 and read under 2.0
  is replaced by the next login, not merged); on 1.26 a stored DCR registration is never offered;
  **on 2.x a stored DCR registration with an empty `issuer` is never offered** — built from the
  SDK's own `OAuthClientInformationFull`, once as the resource-origin fallback leaves it and once
  as 1.26 wrote it — and the second login against a *different* AS registers again instead of
  presenting it; the record's issuer equals `context.auth_server_url`; a server connected before
  the login gets its tools back by reconnect, and one that was `needs_auth` from startup gets the
  restart message (D8); the listener is closed on success, failure, timeout and cancel.
- **Logout:** a logout issued during a slow refresh waits for it and then deletes the record; a
  refresh started after the logout reports `needs_auth`; the lock file remains (S4).
- **Step-up:** a 403 `insufficient_scope` yields `needs_auth` with the "cannot request extra scopes"
  message and no retry.
- **Mid-session rejection:** a tool call whose refresh gets `invalid_grant` returns the login hint
  and leaves the server `NEEDS_AUTH` in `get_server_status()` — not `CONNECTED`.
- **401 without a challenge:** a server answering 401 with no `WWW-Authenticate: Bearer` stays
  `ERROR`, not `needs_auth`.
- **Preflight:** with a record present, the preflight request carries no `Authorization` header and
  triggers no refresh.
- **Auth failures through the real transport** (both majors, real SDK transport over the fake
  server — never a raised fake): a first connect with no record to a server answering 401 +
  `Bearer` ends `NEEDS_AUTH`, and one answering a bare 401 ends `ERROR`; a tool call whose 401
  survives refresh ends `NEEDS_AUTH` on 2.0, where the error text is "Server returned an error
  response" and `classify_mcp_error` says `OTHER` — the test that fails if the flag is read only
  in the AUTH arm; after a successful login and reconnect the flag is clear.
- **Lock placement:** with another process holding a record's lock, a tool call on a *different*
  server completes (the loop is not blocked); a login commit or logout issued while a refresh on
  the same record is in flight waits for it — the test that fails against a bare reentrant
  `FileLock`; a tool call cancelled during a refresh leaves the rotated token written and both
  locks released; **a tool call cancelled during a refresh followed immediately by
  `disconnect_all()`** (token endpoint slowed so the refresh is still in flight) returns only after
  the rotated token is on disk and the lock file is free — the test that fails against a
  `_shutdown` that waits for calls alone; two managers in one process refreshing one record make
  exactly one token request.
- **ACP:** a server from `session/new` with no headers, whose URL has a record, is connected without
  an `Authorization` header.
- **Parallel calls** (the S1 shape): two concurrent 2-s calls on a JSON-mode server with
  `StoredTokenAuth` finish in < 3 s.
- **Storage:** 0600/0700 modes (POSIX); URL/issuer mismatch ignored; atomic write under a killed
  writer; S4's two-process race leaves exactly one refresh at the token endpoint and both processes
  holding the new token.
- **Logs:** a full login and a refresh leave no token, code or verifier in `agentao.log`.
- **CLI UI:** bind address is loopback; port-in-use message; timeout closes the listener; paste path
  with hidden input.

## 13. Implementation plan

### 13.1 Before PR 1

1. **The go-ahead.** *Done 2026-10-02:* the maintainer approved building it and every §11
   recommendation.
2. **This document lands first**: revs 2–8 are committed to PR #396 and merged, so every
   implementation PR can cite a design on `main`.
3. **Chinese twin** `mcp-oauth.zh.md` (written at rev 8), and the D1 follow-up: `openworker-borrow-review.zh.md` §9
   points here (*done* — its summary row and §9 now say this design supersedes it).
4. **Optional, outward-facing, needs its own approval:** a comment on python-sdk #2858 and #2875
   with the S1/S2 numbers (§10). Nothing here depends on it.

### 13.2 What the first version contains

| In | Out (and where it is argued) |
|---|---|
| OAuth on Streamable HTTP and SSE servers from agentao's own `mcp.json`, default on (D3) | stdio servers; servers an ACP editor passes in (§8.3) |
| Login through the SDK provider: discovery, pre-registered or DCR client, PKCE, `resource`, `iss` on 2.x | CIMD (D2), Client Credentials and Enterprise-Managed extensions (§10) |
| Our steady state: attach, refresh before expiry and once on 401, cross-process lock (§5.3) | subclassing or patching the SDK provider (D6) |
| One record per server URL in `user_root()/mcp-oauth/`, i.e. `~/.agentao/mcp-oauth/` (§6.2) | OS keyring (§6.2) |
| CLI: `/mcp login|logout`, `needs login` in `/mcp list`, `agentao mcp login|logout`, loopback + paste | a public login API for embedded hosts (D5, §8.2) |
| SDK 1.26 – 2.x, with documented 1.x gaps (D4) | step-up scope union (D7) |
| Reconnect after login; a restart message for a server with no registered tools | registering MCP tools at runtime (D8) |

### 13.3 PR 1 — auth module and record store

Everything below the UI. After it merges, a URL server that answers 401 reports `needs_auth`
instead of `error`, and a record written by a test is used and refreshed; nothing can create a
record from the CLI yet.

| File | Change |
|---|---|
| `agentao/mcp/oauth_store.py` (new) | The record (§6.2): path under `paths.user_root()` from the canonical URL, 0600/0700, atomic write, URL-mismatch guard, the separate `.lock` file; the per-record `asyncio.Lock` (one set per manager), the polled `FileLock`, and the shielded critical section (§5.3, "Where the lock is taken") |
| `agentao/mcp/oauth.py` (new) | `StoredTokenAuth` (§5.3), recording a `needs_auth` verdict on its `McpClient` rather than raising; `login(name, ui)` and `logout(name)` (§5.5), offering a stored dynamic registration only when its `issuer` is set; the internal four-step UI protocol (§5.1); the 401 / `invalid_grant` / 403 `insufficient_scope` classification |
| `agentao/mcp/_compat.py` | Probes, never version strings: the `httpx.Auth` / `httpx2.Auth` base; the callback return shape (tuple vs `AuthorizationCodeResult`); the provider `context` fields login reads (`auth_server_url`, `oauth_metadata`), failing closed; whether the SDK binds registrations to an issuer (1.26 vs 1.30+) |
| `pyproject.toml` | `filelock>=3.0` (from `>=1.4.0`; §5.3) |
| `agentao/mcp/config.py` | The `oauth` key: `false` or an object with `client_id`, `client_secret` (`$VAR`), `callback_port`, `redirect_host`; validated fail-closed like `resolve_transport` |
| `agentao/mcp/client.py` | `ServerStatus.NEEDS_AUTH`; the §5.4 rule deciding whether a server gets `StoredTokenAuth` (every OAuth-eligible URL server, record or not); the verdict flag, cleared in `connect()` and read **before** `classify_mcp_error` in `call_tool`'s single `except` and before the existing path in `_fail_connect`; passing it as `auth=` to `create_mcp_http_client` (Streamable HTTP, `:981`) and to `sse_client` (`:932`) — both majors accept it; the preflight (`:918`) is left **without** auth (§5.4); each exit setting `NEEDS_AUTH` with the login hint when the flag is set (a 401 with a `Bearer` challenge, or a rejected refresh); `reconnect(name)` for login and logout; the manager-held set of shielded refresh/commit tasks, awaited in `_shutdown` after the calls and before disconnect, with `disconnect_all`'s deadline (`:1488`) extended by the token timeout (§5.3, "Shutdown") |
| `agentao/acp/mcp_translate.py` | `"oauth": false` on every server the editor passes (§8.3) |
| `agentao/security/secret_scan.py` | Patterns for `access_token`, `refresh_token`, `code`, `code_verifier` in JSON and form bodies (§6.2) |

**Tests** — §12 except the CLI UI row: the fake authorization server on both SDK majors; the
invariant with its mutation check; restart with an expired token and the three refresh outcomes;
the refresh merge; login (old tokens not loaded, changed port, 1.26 never offering a DCR client,
2.x never offering one with an empty `issuer`, the record's fields, reconnect, listener closed on
every exit — through a test UI); logout racing a refresh; step-up; mid-session rejection; 401
without a challenge; preflight without auth; lock placement; ACP; parallel calls under 3 s;
storage modes and the killed writer; logs.
**S3** (SSE) and **S4** (two processes, one rotating refresh token) are run here for the first time.

**Done when** the suite and `ruff check .` are green on all CI jobs (Windows and macOS included:
`filelock` and `os.replace` are the cross-platform pieces; the mode check is POSIX-only), and the
mutation check turns a test red.

### 13.4 PR 2 — CLI login loop

| File | Change |
|---|---|
| `agentao/cli/mcp_login_ui.py` (new) | The §7 UI: listener on `127.0.0.1`, OS-assigned or `callback_port`, path `/callback/<server-id>`, 300 s timeout, `webbrowser.open` or print the URL, a hidden size-bounded paste of the redirect URL while the listener keeps waiting. Standard library only, so it works on a bare `pip install agentao` like `--login` (#388) |
| `agentao/cli/commands/mcp.py` | `/mcp login <name>`, `/mcp logout <name>`; `needs login` in `/mcp list` (`:31` colours only `connected` green today); after a login, "restart agentao to load `<name>`'s tools" when the server has none registered (D8) |
| `agentao/cli/entrypoints.py`, `agentao/cli/subcommands.py` | `agentao mcp login|logout <name>` beside the existing `plugin` / `skill` / `config` subcommands (`entrypoints.py:221-243`), for ACP and embedded users (§8.3) |
| CLI startup | One line per server needing login (§8.1) |
| `agentao/cli/help_text.py` | The new commands |

**Tests** — the §12 CLI UI row: loopback bind; the port-in-use message; timeout closes the
listener; the paste path; and one end-to-end login → tool call → logout against the PR 1 fake
through the real UI with a scripted browser.

### 13.5 PR 3 — docs

- `docs/reference/configuration.md` §5 (`mcp.json`): the `oauth` key and the credential directory;
  `developer-guide/{en,zh}/cli/8-mcp-acp-plugins.md`: login, logout, `needs login`, the ACP and
  headless path; both languages.
- `developer-guide/{en,zh}/part-1/6-compared-to-vendor-sdks.md`: "no OAuth yet" leaves the MCP row
  (`:25`), and "MCP OAuth" is taken out of the sentence it shares with Linux/container sandboxing
  and OpenTelemetry (`:45`) — the sentence is rewritten, not deleted; both languages.
- `CHANGELOG.md` `[Unreleased]`, and the MCP section of `CLAUDE.md`.
- **The three limitations**, in the docs and the CHANGELOG entry rather than a bare "OAuth
  supported": no RFC 9207 `iss` check on SDK 1.x, and 1.26 registers a new dynamic client at every
  login (D4); login reads the SDK provider's internal `context` through `_compat`, pinned by tests
  (§5.5 step 5); no step-up scope union — `insufficient_scope` is reported, not resolved (D7).
  Plus one usage note, not a limitation of the protocol: a server that needed login from startup
  loads its tools after a restart (D8).

### 13.6 Order and release

PR 1 → PR 2 → PR 3, each opened against `main` after the previous one merged (a PR stacked on
another branch gets no CI run). All three ship in **one release**; no release is cut between PR 1
and PR 3, because PR 1 alone would put `needs_auth` in front of users with no command that clears
it.

### 13.7 Risks

| Risk | If it happens |
|---|---|
| S3 fails: `sse_client(auth=)` does not drive our 401 retry the way Streamable HTTP does | OAuth on SSE leaves the first version and the docs say so; legacy SSE is the minority transport |
| S4 shows a gap in the lock (e.g. a refresh outliving its token-request timeout) | Fixed in PR 1 before merge — it is the bug class §6.1 exists for, so it is not deferred |
| A new SDK release moves a `context` field `login()` reads | The `_compat` probe fails the login closed with a message; the steady state is unaffected, so already-logged-in servers keep working |
| Upstream fixes F1–F4 | Nothing to change: the steady state never used the provider. Revisit D6 only if it ever becomes simpler to drop our module |
| A future SDK changes what an empty `issuer` means, or stamps `oauth_metadata.issuer` instead of `auth_server_url` | The 2.x login tests built from the SDK's own models (§12) go red; §5.5 steps 2 and 5 follow the SDK, never a version string |

---

## Appendix A — Revision history

**Rev 1 (2026-10-02)** wired the SDK's `OAuthClientProvider` into every connection.

**Rev 2 (same day) — the spikes changed the architecture.** Rev 1 wired the SDK's
`OAuthClientProvider` into every connection. Running it (§9) showed that the provider's
*steady-state* token handling has three defects, each with an open upstream issue and an unmerged
fix, on every SDK version agentao supports: it serializes requests that answer in JSON mode
(measured 2 s → 4 s), it forgets token expiry across restarts, and it answers a 401 with a full
interactive login instead of using the stored refresh token (measured: `/token` never called). Rev 2
uses the SDK provider **only for an explicit login**; ordinary connections use a small `httpx.Auth`
of our own that attaches the stored token and refreshes it (§5.3). That also makes the §5.2
invariant structural instead of a convention.

**Rev 3 (same day) — the behaviour contracts rev 2 left implicit.** A review of rev 2 accepted the
architecture and asked for five contracts; each was checked against the SDK and, where it depends
on SDK behaviour, measured (§9 S5): how `login()` treats old tokens and requested scopes (§5.5),
the merge rule for refresh responses (§5.3), the callback / login / logout lifecycle under one lock
(§5.5, §6.2), one record per server URL (§6.2), and ACP-supplied servers opting out explicitly
(§8.3). The public host-facing pieces (an auth-UI protocol in `agentao.host`, a record-store
factory) are deferred; the first implementation is three internal parts: the auth module, the
record store, and the CLI login loop.

**Rev 4 (same day) — three local corrections from review round 3, no architectural change.**
Issuers are kept as the strings the protocol carries, never normalised: the SDK compares them
exactly, and normalising could make two different issuers' registrations look like one (§5.5). The
generation counter is gone: a refresh holds the record lock from re-read to write, so a login or
logout can never land inside it and there was nothing for the counter to detect (§5.3, §6.2). And
the deferral of step-up scope union (D7) is now carried through — §4 and §5.4 no longer promise it,
the `scopes` key is removed, and the limitation is stated (§5.3). Checking point 1 also corrected
§3.1: issuer-bound registration exists on 1.30 and 2.0 but **not on the 1.26 floor**.
Review round 4 passed the design with no blocking item and recommended building it; its one
addition is in §13 — the docs ship in the same release and name the three limitations.

**Rev 5 (same day) — tidied, and the plan written out.** No design change. The rev notes moved
here from the header; the status records the passed review; §13 became the implementation plan
(prerequisites, contents, files per PR, release rule, risks); the docs targets were corrected to
`configuration.md` §5 and the CLI guide's chapter 8; §6.3 and §7 were brought in line with §5.5
and §6.2.

**Rev 6 (same day) — a reverse review of rev 5 against the code and the installed SDKs.** Round 4
had passed rev 5 with no blocking item; checking its claims against `main` and the SDK source found
three defects in the plan and three smaller ones. The architecture (D6 (a)) is unchanged.

1. **A stored registration with an empty issuer was offered to any AS.** Rev 5 offered a stored
   dynamic registration "as-is, and the SDK decides" wherever the SDK binds issuers — but 2.0's
   `credentials_match_issuer` passes an empty issuer for every AS, and the SDK leaves it empty after
   the resource-origin `/register` fallback; every 1.26-written registration has none. Now offered
   only when `issuer` is set (§3.1 caveats, §5.5 step 2, §6.3). The same reading fixed which value
   the record calls its issuer: the SDK binds `auth_server_url`, not `oauth_metadata.issuer`
   (§5.5 step 5, §6.2).
2. **Tools could not appear without a restart.** Rev 5 promised a reconnect would bring a newly
   logged-in server's tools; tools are registered once, at construction, so a server that was
   `needs_auth` from startup has none to bring back. The first version says "restart", as
   `/mcp add` does; runtime registration is the new D8.
3. **The record lock on the MCP loop.** A blocking `FileLock.acquire` there stalls every server,
   and `FileLock` is reentrant per thread — measured: two coroutines on one thread both hold one
   instance. The lock is now taken off the loop behind a per-record in-process lock (§5.3).
4. **A mid-session rejection left the server `CONNECTED`** — `call_tool`'s AUTH arm only returned
   text. `needs_auth` is now a verdict recorded on the client and read at both exits (§5.3 step 2).
5. **The preflight could not carry the auth object** (plain `httpx` on both majors; `httpx2.Auth`
   on 2.x) and had no reason to; it now carries none (§5.4).
6. **A 401 without a `Bearer` challenge** no longer reads as `needs_auth` (§5.4, D3).

Also: `agentao mcp login` is described as interactive-from-a-shell, not "non-interactive" (§7);
the record path is `paths.user_root()` (§6.2); §2 gained four grep-verified rows and one corrected
line number; the PR 3 docs target names the shared sentence at `:45`. Verified true and left alone:
F3 on 1.26/1.30/2.0, no `iss` check on either 1.x, issuer binding absent on 1.26 and present on
1.30, and every `oauth2.py` line §5 cites.

**Rev 7 (same day) — an external review of rev 6: two P1s, one lock scheme.** No scope change; the
reviewer's verdict was "fix these two, then implement". Both were checked against the installed
SDK before being taken.

1. **A first connect with no record could not be recognised as `needs_auth`.** Rev 6 attached
   `StoredTokenAuth` only when a record existed and had `_fail_connect` look for the `Bearer`
   challenge — but mcp 2.0's Streamable HTTP transport replaces any non-404 error status with
   `"Server returned an error response"` (`streamable_http.py:342-370`), so neither the status
   nor the header survives to `_fail_connect`. `StoredTokenAuth` is now attached to every
   OAuth-eligible URL server and, without a record, sends no token and only observes the 401
   (§5.4). No extra probe request.
2. **Reading the verdict in `call_tool`'s AUTH arm missed every 2.0 auth failure.** The same text
   classifies as `OTHER`, so that arm is never reached. The flag is now read first in the single
   `except`, before any classification, and cleared on connect (§5.3 step 2), with a test through
   the real transport (§12).
3. **The lock scheme is decided** instead of offering two: every record write on the MCP loop, a
   per-record `asyncio.Lock`, a polled non-blocking `FileLock` (verified on filelock 3.0.0 and
   3.25.2; floor raised to `>=3.0`), and a shielded critical section so a cancel cannot abandon a
   rotation midway (§5.3).

Not taken: the reviewer's suggestion to compress the body into "flow, storage and locks, first
version's boundary, acceptance" with peers and history in appendices — an editorial pass worth
doing before the Chinese twin is written, not a design change.

**Rev 8 (same day) — the external review's second round: one P1.** It confirmed rev 7's two fixes
and found that `asyncio.shield` protects a refresh from its caller's cancel but not from the
manager's close: `_shutdown` waits only for `self._calls`, then stops the loop, so a cancel
followed at once by `disconnect_all()` could lose a rotation the server had already performed.
Verified by reading `client.py:1512-1543`. The manager now owns the shielded tasks and waits for
them, inside an extended outer deadline (§5.3, "Shutdown"), with the test that reproduces it
(§12). The residual case — the process killed between the token response and the write — is
stated with its bounded outcome. Clarified at the reviewer's note: the `asyncio.Lock`s are per
manager (each has its own loop); between managers in one process the file lock excludes, measured
with two instances on two threads.

### Implementation record — PR 1 (2026-10-02)

Auth module and record store, as §13.3 lists them: `agentao/mcp/oauth.py` and `oauth_store.py`
(new), and changes to `_compat.py`, `config.py`, `client.py`, `acp/mcp_translate.py`,
`security/secret_scan.py` and `pyproject.toml` (`filelock>=3.0`). Tests: `tests/test_mcp_oauth.py`
over `tests/support/oauth_server.py`, a fake origin that plays the MCP server, PRM, AS metadata,
DCR, `/authorize` and `/token` behind the SDK's real transports with only the socket replaced.
90 tests, passing on mcp 2.0.0, 1.26.0 and 1.30.0. **S3** (SSE) and **S4** (two processes, one
rotating refresh token: exactly one token request, both processes end on the new token) were
run here for the first time and pass on all three.

Eight mutation checks were run by hand, each against the test that exists for it, and each turned
it red: constructing the SDK provider on connect; reading the verdict only in the AUTH arm; a
`_shutdown` that ignores the shielded tasks; a lock that is not per record (the reentrant-lock
case); a blocking file-lock acquire; offering a registration with an empty issuer; attaching the
auth object only when a record exists; SSE without the auth object. The blocking-acquire check
first came back **green** — the test started its clock after the loop had already stalled — and
the test was fixed before this record was written.

Where the code says more than the design did, or differs:

1. **The lock file.** filelock ≥ 3.x on POSIX unlinks the lock file itself on release, and handles
   the race that creates on its own side. So "the lock file survives the record's deletion" (§6.2)
   holds in the sense that matters — agentao never deletes it, logout included — but the file is
   not always present between holders. The test asserts behaviour (logout waits), not the file.
2. **At most one token request per request.** A 401 that follows a refresh which already failed in
   the same request only re-reads the record; it does not ask an unreachable token endpoint twice.
   Found when a non-`Bearer` refresh response was followed by a forced second refresh that spent
   the rotated token and turned an ordinary error into `needs_auth`.
3. **A server in `NEEDS_AUTH` is not reconnected on every call.** `call_tool` returns the login
   hint until the record file changes (its mtime, recorded with the verdict) — a login here or in
   another process — and only then reconnects.
4. **Where the verdict lives.** On the `StoredTokenAuth` object, which `connect()` rebuilds; that
   is the "flag cleared when a connect starts" of §5.3 step 2.
5. **What a login sends.** One request through the provider — an `initialize` POST (Streamable
   HTTP) or the SSE `GET` — on the SDK's own client factory, body never read, any session it opened
   deleted best-effort; then the ordinary connection reconnects. Not a full MCP connect.
6. **A configured `client_id` with a `client_secret`** is offered as `client_secret_basic`, RFC
   7591's default; without a secret, as a public client (`none`).
7. **`CHANGELOG.md`** is not touched here: §13.5 puts it in PR 3, which ships in the same release.
8. **Review fixes before commit**, each with a test that fails without it. A `/code-review` pass:
   a 401 whose retry succeeds clears the refresh failure it recovered from; that failure no longer
   stops a session-expired or dropped connection from reconnecting; a refresh response with no
   `token_type` is Bearer, as the SDK reads it; login and logout reconnect under the reconnect lock.
   A Codex review: a verdict carries the request that reached it, and a success clears only verdicts
   older than itself or its own — a concurrent success used to clear another request's verdict before
   its caller read it; a retry's 403 is classified like a first response's; and a refresh has an
   elapsed-time deadline, since httpx's timeouts bound each read, not the exchange. A second Codex
   round: a `NEEDS_AUTH` verdict is never cleared by a success — its caller may still be reading
   the error body when another request starts and succeeds, and once read it moves the whole server
   to `NEEDS_AUTH` anyway; the success rule now guards `REFRESH_FAILED` alone. And a refusal carries
   the stamp of the credential that was refused, not of whatever the file holds when the 401
   arrives, so a login that lands during the request is tried on the next call instead of being
   read as already rejected. A third Codex round: the secret scanner skipped strings shorter than
   20 characters, so a short `?code=…` escaped the new patterns (and a short `token=…` had already
   escaped `kv_secret`); the cutoff is now 13, the shortest match. A fourth: a concurrent request's
   failed refresh overwrote a pending `NEEDS_AUTH`; a refusal now outranks a failed refresh, as it
   already outlived a success. A fifth: a connection attached its cached token until a refresh or a
   401, so a logout elsewhere (another manager, another alias of the URL, another process) left a
   still-valid token in use, and a login as another account left it acting as the old one. Each
   request now compares the record file's stamp — one `stat` — and reloads on any change. A sixth:
   only a *transient* refresh failure keeps the old token. After a terminal one — logged out while
   waiting for the lock, or the grant rejected — the request goes without a token, even if the old
   one is still inside its validity window; and a credential already refused (a 401 or a rejected
   grant, not a scope 403) is neither sent nor refreshed again until the file changes, so a dead
   grant costs one token request, not one per request. A credential file that is not valid UTF-8 is
   unreadable like malformed JSON, so a login replaces it. A second `/code-review` pass: the "set
   `type: sse`" hint no longer rides on a malformed `oauth` block or a failed refresh; and a
   `REFRESH_FAILED` verdict no longer steers `call_tool` at all — it is the connection's, so another
   call's refresh failure was relabelling this call's own error and skipping its reconnect. It now
   only annotates an error that is itself an auth failure or the opaque HTTP one a refresh failure
   would produce. A seventh Codex round: requests that entered the refresh window together each
   re-submitted a grant the first had just seen rejected. The refusal is now rechecked under the
   record lock, a waiter that finds it sends no token, and a tokenless 401 no longer re-records the
   refusal under "no credential" — which had dropped the refused credential's stamp and let the
   next request send it again. An eighth: a record whose stored URL does not parse
   (`https://h:bad/mcp`) raised out of `load()`, failing connects and blocking the login that would
   replace it; it is now unreadable like malformed JSON.

### Implementation record — PR 2 (2026-10-02)

CLI login loop, as §13.4 lists it: `agentao/cli/mcp_login_ui.py` (the §7 UI) and
`agentao/cli/mcp_auth.py` (login and logout shared by the two surfaces, and the `agentao mcp`
subparser), both new; changes to `cli/commands/mcp.py` (`/mcp login <name> [--no-browser]`,
`/mcp logout <name>`, `needs login` in `/mcp list`), `cli/ui.py` (the §8.1 startup line),
`cli/_light.py`, `cli/__init__.py` and `cli/entrypoints.py` (`agentao mcp login|logout <name>`,
routed through the light entry so it runs without the `[cli]` extras), and `cli/help_text.py`.
Tests: `tests/test_mcp_oauth_cli.py`, 45 tests — the listener over real loopback sockets, the
paste reader over a pseudo-terminal, and login → tool call → logout through the real UI against
the PR 1 fake with a scripted browser that requests the redirect from the listener. Passing on
mcp 2.0.0, 1.26.0 and 1.30.0, and on Python 3.10.

Twelve mutation checks, each turned its test red: binding all interfaces; dropping the state
check; dropping the path check; `close` not stopping the paste prompt; no wait timeout; echo left
on; canonical mode; the terminal not restored; no paste size bound; the listener not closed;
`SO_REUSEADDR` off; no fallback from a remembered port. Two first came back as a **hang** rather
than a failure (no timeout, canonical mode), and one red only two runs in three (`SO_REUSEADDR`,
which depended on which side closed the callback connection first); the tests were bounded and
made deterministic before this record was written.

Where the code says more than §7 did, or differs:

1. **The callback checks `state`.** The listener is reachable by any local process, so only a
   redirect carrying the `state` of the authorization URL this login opened can settle it; any
   other request is answered 400 and the login keeps waiting. The SDK checks `state` too, but only
   after the first redirect has been taken — without this, a stray request ends the login.
2. **A port can be reused after TIME_WAIT.** The callback's server side closes first, so a second
   login within a minute found its stored registration's port "in use". POSIX `SO_REUSEADDR` lets
   that bind succeed and still refuses a port another program listens on; Windows keeps it off,
   where it would allow exactly that.
3. **Only a configured port fails when taken.** A port remembered from a stored registration
   (no `oauth.callback_port`) falls back to an OS-assigned one: a dynamic registration simply
   registers again, and a configured `client_id` gets §5.5's "set `callback_port`" message.
4. **The paste prompt reads `/dev/tty` in non-canonical mode, without echo**, polling so `close`
   can stop it when the listener wins. Canonical mode caps a line at the driver's limit (1024 bytes
   on macOS), shorter than a real redirect URL. It runs on its own thread, not the loop's default
   executor, which httpx resolves hostnames on.
5. **No browser on a Linux session with no display.** `webbrowser` would fall back to a console
   browser that takes over the terminal, so the paste path is used instead.
6. **Exit status of `agentao mcp login`:** `0` connected, `1` failed or stored-but-not-connected,
   `2` usage, `130` cancelled.
7. **Review fixes before commit**, each with a test that fails without it. A `/code-review` pass:
   a parse error on the `mcp` line printed twice (the light entry handed it on); `-h` before `mcp`
   ran the login; the no-browser message dropped an IPv6 host's brackets; and Ctrl+C at the Windows
   paste prompt raised on the reader thread, where it would have escaped the MCP loop. Codex, five
   rounds: the scripted-browser tests would have waited out the timeout on headless Linux CI (the
   browser is skipped without a display), and the headless path now has a test of its own; REPL
   output from a login is passed through the terminal sanitizer, not only Rich escaping, since an
   authorization server writes the error description; a redirect with more than 32 query fields
   killed the paste prompt; a cancelled login now waits (bounded) for its UI to close, so the paste
   prompt cannot hold the terminal under the REPL's next prompt; `agentao mcp login` finds plugin
   MCP servers, installed or `--plugin-dir`, as the REPL does; `oauth.redirect_host` gets a listener
   on the loopback address it names (`::1` included) and any non-loopback host is refused before
   the login starts; a callback is refused until the authorization URL is opened, so a stale
   redirect during discovery cannot end the login; and an invalid transport is reported, not raised.
   The fifth round was clean.
