# MCP Streamable HTTP client support — Design

**Status:** **Shipped in 0.4.14 (2026-07-02)** — this doc now reads as a landed
contract, not a proposal. It added the MCP **Streamable HTTP** transport
(`type: "http"`) to `McpClient` and flipped the three ACP gates that were
deliberately left rejecting `http` until the client could dispatch it. Per the
maintainer's **2026-07-01 decision (D2)**, a bare `url` *defaults* to Streamable
HTTP (SSE is opt-in via `type: "sse"`) — a deliberate **breaking change**; see
§3 D2 and §10.

Verified against `main`@`8266de1` (2026-07-19): `agentao/mcp/config.py:61`
(`_KNOWN_TRANSPORTS`), `:166` (`resolve_transport`), `:216` (bare-`url` → `http`
inference); ACP gates at `agentao/acp/schema.py:50` (capability advertisement
`{"http": True, "sse": True}`), `:174` (`Literal["stdio", "sse", "http"]`),
`agentao/acp/session_new.py:226`, `agentao/acp/mcp_translate.py:218,243`.

**Audience:** Agentao maintainers; the DeepChat/TensorChat ACP integration owner.

**Companion:** `mcp-streamable-http.zh.md`.

**Related:**
- `docs/reference/configuration.md` — MCP config schema (`§ MCP`, transport table).
- `project_mcp_connect_preflight` (PR #71) — the content-type preflight this
  design reuses unchanged; it was already written transport-agnostic.
- `project_opencode_pull_review_20260629` (PR #119) — `resolve_timeouts`
  split (startup/request) that both URL transports already share.
- `embedding-vs-acp.md` — ACP is a frontend over the embedded core; the
  transport dispatch lives in the runtime, ACP only translates config.

**Method:** every claim below is anchored to source at `main`@`8bcb1b1`. MCP SDK
surfaces (`sse_client`, `streamablehttp_client`) were introspected from the
installed `mcp` package, not recalled. No intuition mappings.

---

## 1. Background — how transport is chosen today

`McpClient` (`agentao/mcp/client.py`) recognizes exactly two transports, and it
infers them **structurally** from which config key is present — there is no
explicit transport selector in native `.agentao/mcp.json`:

```python
# client.py:152-158
@property
def transport_type(self) -> str:
    if self.config.get("command"):
        return "stdio"
    if self.config.get("url"):
        return "sse"
    return "unknown"

# client.py:183-188  (inside connect())
if self.config.get("command"):
    await self._connect_stdio()
elif self.config.get("url"):
    await self._connect_sse(startup_timeout, request_timeout)
else:
    raise ValueError("No transport configured ... (need 'command' or 'url')")
```

- `_connect_stdio` (232-255) → `stdio_client`.
- `_connect_sse` (318-353) → `sse_client`, runs `_preflight_content_type`
  (257-316), applies `startup` as the HTTP-open timeout and raises
  `sse_read_timeout` to cover a large per-request budget.
- Timeouts are pre-resolved once by `resolve_timeouts` (`config.py:87-124`),
  which is **already transport-neutral** — its docstring literally says the
  legacy int "is handed to the SSE transport" but the semantics ("bound the
  connect/startup phase") apply verbatim to Streamable HTTP.
- `call_tool` (355-441) retries once on `SESSION_EXPIRED` / `TRANSPORT_DROPPED`
  per `classify_mcp_error` (85-99).

**The ACP layer already uses an explicit `type` field** — and already anticipates
this work by rejecting `http` in three places, each with a comment that says
"remove this when the client learns Streamable HTTP":

| Gate | Location | Current behavior |
|---|---|---|
| Capability advertised | `acp/initialize.py:75-78` | `mcpCapabilities = {"http": False, "sse": True}` |
| `session/new` parser | `acp/session_new.py:226-232` | `type` must be `stdio`\|`sse`; `http` → `INVALID_PARAMS` |
| ACP→native translator | `acp/mcp_translate.py:189, 232-244` | `http` entry logged + dropped (defensive backstop) |

So the config vocabulary (`type: "stdio" | "sse" | "http"`) is **already
established in-tree** on the ACP side. This design brings the native config and
the client up to the same vocabulary and connects the wire.

## 2. The gap and why now

The MCP spec **deprecated the HTTP+SSE transport** (2024-11-05) in favor of
**Streamable HTTP** (2025-03-26). New remote MCP servers ship Streamable HTTP;
many expose *only* it. Agentao can currently reach them only if they also keep a
legacy SSE endpoint. The client dependency is already present —
`mcp.client.streamable_http.streamablehttp_client` ships in the pinned
`mcp>=1.26.0` SDK — so this is a wiring task, not a new dependency.

## 3. Design decisions

### D1 — Transport selector is an explicit `type` field

Native `.agentao/mcp.json` gains an optional `type` per server:
`"stdio" | "sse" | "http"`. Accept `"streamable-http"`, `"streamable_http"`,
`"streamablehttp"` as case-insensitive aliases that normalize to `"http"`.

**Why `type`:** it is already the ACP wire vocabulary (`acp/mcp_translate.py:189`)
*and* the broad ecosystem convention (Claude Code `.mcp.json`, VS Code, Cursor
all key remote transport on `type`/`transport`). Reusing it keeps one vocabulary
across the native config, the ACP boundary, and the outside world — no third
dialect to reconcile.

### D2 — `type`-absent default is Streamable HTTP (bare `url` → `http`)

**Decided by the maintainer (2026-07-01).** When `type` is omitted, `url` →
**`http` (Streamable HTTP)**; `command` → `stdio` is unchanged. SSE is retained
but becomes **opt-in** via `type: "sse"`. A bare `{"url": "..."}` now means
Streamable HTTP.

**Why:** the MCP spec deprecated HTTP+SSE in favor of Streamable HTTP; new
servers ship http-first (Cursor already made a bare `url` mean Streamable HTTP).
Defaulting to the live transport is what most new configs will want and keeps
agentao aligned with the spec's direction rather than pinned to a deprecated
transport.

**Accepted cost — this is a breaking change.** Every existing bare-`url` config
that pointed at an SSE endpoint now attempts Streamable HTTP and will fail the
handshake until `"type": "sse"` is added. The maintainer owns this call (a
product-direction decision — cf. the project's "pain judgment is the user's"
rule). Two mitigations keep the break **loud, not silent**:

1. **Actionable connect error (§5.7).** When a connection made on the *inferred*
   http default (bare `url`, no explicit `type`) fails the handshake, the error
   appends: *"tried as Streamable HTTP (the default for a bare `url`); if this is
   a legacy SSE endpoint, set `\"type\": \"sse\"`."* The one-token fix travels
   with the failure.
2. **Release-note migration line (§10).**

**Alternative considered and rejected — auto-fallback** (try Streamable HTTP,
fall back to SSE on 405/404). Rejected: it contradicts agentao's
explicit-over-magic posture — the very preflight this design reuses is an
*allow-list*, deliberately not content-sniffing (`client.py:113-119`).
Auto-fallback also makes `transport_type` nondeterministic (breaks `/mcp list`
status + `get_server_status`), doubles the connect latency budget on the miss
path, and muddies which transport an error came from. Legacy SSE users write
`type: "sse"` — one token.

### D3 — One transport resolver, and it **fails closed**

Add `resolve_transport(config) -> str` to `config.py` as the single source of
truth. `McpClient.transport_type` and `connect()` both call it — the ad-hoc
`command?/url?` ladders (152-158, 183-188) collapse into one dispatch on its
return. Contract:

1. **Explicit `type` present** — normalize (lowercase + alias fold), then:
   - in `{stdio, sse, http}` → return it;
   - **anything else → raise `McpTransportConfigError`** (a `ValueError`
     subclass) with an actionable message listing the accepted values.
2. **Explicit `type` absent** — apply the D2 inference: `command` → `stdio`,
   `url` → `http`, neither → `"unknown"` (which `connect()` turns into the
   existing "No transport configured" error).
3. **Required-key check (both branches)** — once a concrete transport is chosen,
   the key it needs must be present: `stdio` needs `command`, `sse`/`http` need
   `url`. A mismatch (`{"type":"stdio","url":...}`, `{"type":"http","command":...}`)
   **raises `McpTransportConfigError`**, not a downstream `KeyError`.

**Why fail-closed, unlike `_coerce_timeout`'s warn-and-default (Finding 1).**
A bad timeout falls back to a *safe* default — a slightly-wrong number. A bad
*transport* is categorically different: under D2 a typo like `"type": "see"`
would, under warn-and-infer, silently resolve to Streamable HTTP (the bare-`url`
default) and connect to the **wrong protocol** — the exact class of silent
misroute the whole design is built to avoid. The ACP side already fails closed
here (`session_new.py:227-232` rejects unknown `type` with `INVALID_PARAMS`);
native config must match that posture, not undercut it.

**Keeping the status path total.** `resolve_transport` raising would make the
`transport_type` property (used for `/mcp list` + `get_server_status`, which must
never throw) unsafe. So the property swallows the error for *display only* —
`try: return resolve_transport(...) except McpTransportConfigError: return
"unknown"` — while `connect()` lets it propagate into its existing `except`
(212), which records the actionable message in `error_message`. Net: status
shows `transport=unknown` + the real reason in the error column; the connect
attempt fails closed with a clear message; nothing silently misroutes.

## 4. Config surface

```jsonc
{
  "mcpServers": {
    "remote-http":    { "type": "http", "url": "https://host/mcp",
                        "headers": { "Authorization": "Bearer $TOKEN" } },
    "remote-default": { "url": "https://host/mcp" },        // no type → Streamable HTTP (D2)
    "remote-sse":     { "type": "sse",  "url": "https://host/sse" },  // opt into legacy SSE
    "local":          { "command": "npx", "args": ["-y", "server"] }
  }
}
```

Resolution table (`resolve_transport`):

| config | result |
|---|---|
| `type:"stdio"` + `command` | stdio |
| `type:"sse"` + `url` | SSE |
| `type:"http"` (or aliases) + `url` | **Streamable HTTP (new)** |
| no `type`, `command` present | stdio |
| no `type`, `url` present | **Streamable HTTP** (D2 default) |
| no `type`, no `command`/`url` | `"unknown"` → connect raises "No transport configured" |
| `type` present but not stdio/sse/http | **raise `McpTransportConfigError`** (fail closed, Finding 1) |
| chosen transport's required key missing (`type:"http"` w/o `url`, `type:"stdio"` w/o `command`) | **raise `McpTransportConfigError`** (fail closed, Finding 3) |

`http` and `sse` are **URL-shaped and identical in every field** (`url`,
`headers`, `timeout`, `trust`) — only the client factory differs. That symmetry
is what lets §5 share almost all code.

## 5. Client changes (`agentao/mcp/client.py`)

### 5.1 Imports — use the **canonical** client, not the deprecated alias

```python
from mcp.client.streamable_http import (
    create_mcp_http_client,
    streamable_http_client,
)   # add
```

The pinned SDK (`mcp` 1.26.0, the pin floor) ships **two** functions:
`streamablehttp_client` is `@deprecated("Use streamable_http_client instead.")`
and emits a `DeprecationWarning` on every connect; `streamable_http_client` is
the canonical replacement. We import the canonical one (both exist at 1.26.0, so
no pin bump). The trade-off is a small API difference (§5.3): the canonical
function takes a pre-built httpx client instead of `headers`/`timeout`/
`sse_read_timeout` kwargs — we build that client with the SDK's own
`create_mcp_http_client` factory (also exported from this module), which is
exactly what the deprecated wrapper did internally.

The SDK is already imported eagerly at module top (`from mcp.client.sse import
sse_client`, line 14); Streamable HTTP is the same package, so no new lazy-load
concern — `agentao.mcp/__init__.py`'s PEP-562 gate already defers the whole SDK
until `McpClientManager` is first touched.

> **Update — mcp 2.0 (agentao 0.4.17).** mcp 2.0 **removed** the deprecated
> `streamablehttp_client` alias outright; only `streamable_http_client` exists.
> Choosing the canonical name here is the reason this import survived the major
> bump untouched — the deprecation warning it was avoiding turned out to be a
> removal notice. Nothing in §5.1 changes.

### 5.2 `transport_type` and dispatch → delegate to `resolve_transport`

The property is for *display* and must never throw (`get_server_status`,
`/mcp list`), so it swallows the fail-closed error to `"unknown"`; the real
message is surfaced by `connect()` instead (D3):

```python
@property
def transport_type(self) -> str:
    try:
        return resolve_transport(self.config)     # "stdio" | "sse" | "http" | "unknown"
    except McpTransportConfigError:
        return "unknown"                          # actionable reason rides error_message
```

`connect()` calls the *raising* resolver so a bad `type` / missing required key
fails closed through the existing `except` at 212 (which sets `error_message`):

```python
# connect()
transport = resolve_transport(self.config)        # raises McpTransportConfigError, caught by connect's except
if transport == "stdio":
    await self._connect_stdio()
elif transport == "sse":
    await self._connect_sse(startup_timeout, request_timeout)
elif transport == "http":
    await self._connect_streamable_http(startup_timeout, request_timeout)
else:  # "unknown" — no type and no command/url
    raise ValueError(
        f"No transport configured for server '{self.name}' "
        f"(need 'command', or 'url' with type 'sse'/'http')"
    )
```

Because `resolve_transport` has already validated the required key (D3 step 3),
`_connect_stdio`'s `self.config["command"]` (234) and the URL transports'
`self.config["url"]` are guaranteed present — no downstream `KeyError`.

### 5.3 The structural differences — client-building + tuple arity

> **Update — mcp 2.0 (agentao 0.4.17).** Two of the facts below are 1.x-only.
> mcp 2.0 **dropped the third tuple element**, so every transport now yields the
> same 2-tuple (`mcp.client._transport.TransportStreams`), and it **moved off
> `httpx` onto `httpx2`**, so an `httpx.Timeout` handed to
> `create_mcp_http_client` raises `TypeError: unhashable type: 'Timeout'`.
> Shipping code indexes the streams (`transport[0]`, `transport[1]`) instead of
> unpacking, and takes the `Timeout` class from whichever httpx flavour the
> installed SDK uses — both resolved in `agentao/mcp/_compat.py`, probed off the
> SDK rather than from a version string. The design in this section is
> unchanged; only the arity and the httpx module are.
>
> Note this arity is not visible in a signature or a field name — it only shows
> up at the `yield`. A green suite on one major said nothing about the other,
> which is what the `mcp-compat` CI job now exists to catch.

Introspection of the pinned SDK (verified, not recalled):

- `sse_client(url, headers=, timeout=, sse_read_timeout=)` yields
  **`(read_stream, write_stream)`** — 2-tuple.
- `streamable_http_client(url, *, http_client=None, terminate_on_close=True)`
  yields **`(read_stream, write_stream, get_session_id)`** — 3-tuple; the third
  element is a callable returning the negotiated `Mcp-Session-Id` (or `None`).
  It takes a **pre-built httpx client** (not header/timeout kwargs), and when
  the caller supplies one the SDK does **not** manage its lifecycle.

So `_connect_streamable_http` differs from `_connect_sse` in three ways:
(1) build the httpx client via `create_mcp_http_client` (headers + a
`httpx.Timeout(startup, read=sse_read_timeout)` — the same mapping the deprecated
wrapper used); (2) enter that client into the exit stack **before** the transport
so the LIFO unwind tears the transport down (its session-terminate `DELETE`)
while the client is still open, then closes the client; (3) unpack a 3-tuple, not
a 2-tuple:

```python
async def _connect_streamable_http(self, startup_timeout, request_timeout):
    import httpx
    url, headers, sse_read_timeout = await self._prepare_url_connect(startup_timeout, request_timeout)
    http_client = create_mcp_http_client(
        headers=headers or None,
        timeout=httpx.Timeout(startup_timeout, read=sse_read_timeout),
    )
    await self._exit_stack.enter_async_context(http_client)   # caller-managed lifecycle
    transport = await self._exit_stack.enter_async_context(
        streamable_http_client(url, http_client=http_client, terminate_on_close=False)
    )
    read_stream, write_stream, _get_session_id = transport   # 3-tuple, not 2
    self._session = await self._exit_stack.enter_async_context(
        ClientSession(read_stream, write_stream)
    )
```

**`terminate_on_close=False` (review Finding 1).** The obvious `True` sends a
session-terminate `DELETE` on close — but that `DELETE` reuses the same httpx
client, whose `read` timeout is raised to cover long tool-call budgets (up to
`request`; ≥300 s default). A server that accepts the connection but stalls the
`DELETE` would then block `disconnect()` — and the transient-error *reconnect*
path in `call_tool` — for that whole window. Bounding the exit-stack unwind with
`wait_for` is explicitly unsafe here (the connect() comment warns a timeout
cancellation must not cross an anyio cancel scope into transport cleanup). So we
skip the teardown request: SSE and stdio issue none either, the server expires
the idle session on its own, and the terminate `DELETE` is a spec SHOULD not a
MUST.

### 5.4 Factor the shared URL-connect preamble

`_connect_sse` and `_connect_streamable_http` share four steps verbatim: read
`url`/`headers`, compute `sse_read_timeout` (raise-only vs
`_DEFAULT_SSE_READ_TIMEOUT`, client.py:332-336), run `_preflight_content_type`,
then enter the transport CM. Extract the first three into a tiny helper
`_prepare_url_connect(startup, request) -> (url, headers, sse_read_timeout)`
(which also runs the preflight) so the two `_connect_*` methods differ only by
one line: the client factory and the tuple unpack.

**The preflight needs no change.** `_preflight_content_type` (257-316) already
allow-lists `application/json` + `text/event-stream` and its docstring/error
already name "Streamable HTTP" — it was written transport-agnostic in PR #71.
A Streamable HTTP endpoint that answers the HEAD/GET probe with `application/json`
or `text/event-stream` passes; one that 405s the probe returns non-2xx and passes
through to the real handshake (client.py:302-303). Confirmed against the existing
`test_mcp_preflight.py` logic — no allow-list edit required.

### 5.5 Timeouts — same semantics as SSE

`resolve_timeouts` is transport-neutral. `startup` becomes the httpx client's
connect timeout (`httpx.Timeout(startup, read=...)` fed to
`create_mcp_http_client`), `sse_read_timeout` becomes its read timeout (the
raise-only rule vs `_DEFAULT_SSE_READ_TIMEOUT` is unchanged), and `request`
bounds each `call_tool` via `read_timeout_seconds` exactly as for SSE
(client.py:368-371). Update the one wording nit in `config.py:100-103` ("handed
to the SSE transport") to "handed to the URL transport (SSE or Streamable
HTTP)".

### 5.6 Error classification / reconnect — v1 leaves it, with a watch-item

Streamable HTTP session expiry surfaces server-side as **HTTP 404 on the
`Mcp-Session-Id`**. The existing `SESSION_EXPIRED` markers (client.py:51-59:
"session expired/not found/unknown session/session terminated") catch the *text*
forms the SDK raises, and `TRANSPORT_DROPPED` + the reconnect-once loop
(372-402) already cover a dropped long-poll stream. A **bare** `404` with no
session wording is intentionally **not** added to `SESSION_EXPIRED`: `404` is far
too broad (a genuine tool-not-found would loop into a reconnect storm). Per the
project's "watch-item, not speculative fix" convention (cf.
`project_codex_pull_review_20260614`), v1 ships the existing rules and we add a
targeted marker *only if* a real Streamable HTTP server is observed emitting a
text-less 404 on expiry. Documented here so the next reader knows it was a
decision, not an oversight.

### 5.7 Connect-failure hint for the inferred http default (D2 mitigation)

D2 makes a bare `url` mean Streamable HTTP, which breaks configs that used a
bare `url` to reach a legacy SSE server. To keep that break loud, `connect()`
enriches the failure on the inferred path. Two guards keep the hint accurate:

1. **Inferred-only** — `url` present and `type` absent (the user did not
   explicitly choose http). `resolve_transport` already distinguishes "explicit
   http" from "inferred http"; thread that one bit (e.g.
   `resolve_transport(config, return_source=True) -> (transport, "explicit"|"inferred")`,
   or a sibling `transport_is_inferred(config)` predicate) into the `except`, so
   the hint fires for `{"url": ...}` but **not** for an explicit
   `{"type": "http", ...}` (where SSE is not the likely intent).
2. **Not the non-MCP verdict** — skip the hint when the error is a
   `NonMcpEndpointError`. That exception is the preflight's "this is a web page,
   not MCP at all" verdict (`client.py:102-110`) and already carries its own
   actionable message; appending "set `type:\"sse\"`" on top would wrongly imply
   the endpoint is a legacy SSE server when it isn't MCP at all. The hint is for
   *handshake/transport* failures — where SSE-vs-HTTP is a plausible cause — not
   for a content-type rejection.
3. **Not an auth failure (review Finding 5)** — skip when
   `classify_mcp_error(e) is McpErrorKind.AUTH`. A real Streamable HTTP server
   that returns 401/403 is not fixed by switching to SSE; suggesting it would
   send the user down a wrong debugging path instead of at their credentials.

```python
except Exception as e:
    message = str(e)
    if (
        transport == "http"
        and source == "inferred"                      # from resolve_transport(..., return_source=True)
        and not isinstance(e, NonMcpEndpointError)    # don't override the non-MCP verdict
        and classify_mcp_error(e) is not McpErrorKind.AUTH   # 401/403 isn't fixed by SSE
    ):
        message += (
            "  (tried as Streamable HTTP — the default for a bare 'url'; "
            "if this is a legacy SSE endpoint, set \"type\": \"sse\".)"
        )
    self.error_message = message
    ...
```

Low-noise (fires only on an actual connect failure, never on stdio/sse/explicit
http, never on a non-MCP-content-type verdict) and high-value (the one-token fix
rides along the failures where it actually applies).

### 5.8 Protocol era — handshake first, escalate to `server/discover` on `-32022`

*(Added 2026-07-30 after the mcp 2.0 work, as a record of why the handshake was
pinned at `2025-11-25`. **Implemented 2026-07-31** — the closing verdict below
is superseded; the analysis that leads to it still holds and is what the
implementation is built on.)*

Before this change, `McpClient` built a **bare** session on every transport —
`ClientSession(read_stream, write_stream)` (client.py:326, 442, 506) — and ran
the handshake with a plain `await self._session.initialize()` (client.py:298,
inside `_handshake`). No protocol-version argument was passed anywhere, and
`agentao/mcp/` contained no `mode=` at all.

Introspection across all three `mcp-spec` CI cells (verified, not recalled):

| SDK | `types.LATEST_PROTOCOL_VERSION` | what `initialize()` offers | modern era |
|---|---|---|---|
| `1.26.0` (pin floor) | `2025-11-25` | `types.LATEST_PROTOCOL_VERSION` → `2025-11-25` | absent |
| `1.29.0` (latest 1.x) | `2025-11-25` | `types.LATEST_PROTOCOL_VERSION` → `2025-11-25` | absent |
| `2.0.0` (lock) | `2026-07-28` | `LATEST_HANDSHAKE_VERSION` → `2025-11-25` | present, unreachable here |

**Same number on every supported SDK, for two different reasons.** Under 1.x the
offer is bound to `types.LATEST_PROTOCOL_VERSION`, so it *tracks the SDK* — an
upstream bump to that constant would have moved agentao's wire behaviour with no
code change on our side. Under 2.x the offer is bound to
`LATEST_HANDSHAKE_VERSION`, a constant upstream deliberately **split away** from
`LATEST_PROTOCOL_VERSION` (`2026-07-28`, also exposed as `LATEST_MODERN_VERSION`,
with `MODERN_PROTOCOL_VERSIONS = ('2026-07-28',)`). The split froze this path.
The practical consequence: **upgrading to mcp 2.0 did not change what agentao
says on the wire.** It granted the *capability* to speak `2026-07-28`; agentao
never exercises it.

The negotiated version is a **ceiling, not a constant** — `initialize()` only
*offers* `2025-11-25`; the server picks, and 2.x validates the answer against
`HANDSHAKE_PROTOCOL_VERSIONS` (`2024-11-05`, `2025-03-26`, `2025-06-18`,
`2025-11-25`) before `adopt()`ing it. So nothing downstream may assume
`2025-11-25` semantics — only `≤` it.

Reaching the modern era is **not a missing kwarg**, which is the easy misread.
`mode='auto'` is a field on mcp 2.x's new `mcp.client.client.Client` facade; it
does not exist on `ClientSession.__init__` (probed: 15 parameters, no `mode`).
Adopting that facade means handing it the transport, which agentao establishes
itself across three code paths — so it is a rewrite, not a swap. And the modern
era does not exist in 1.x at all, so under `mcp>=1.26.0,<3` any of this has to
be a probed branch in `agentao/mcp/_compat.py`, never a swapped construction.

~~**v1 stays pinned, per the §5.6 watch-item convention.**~~ *(Superseded — see
§5.8.1. The trigger named here, a server speaking **only** `2026-07-28` and
rejecting the legacy `initialize` with `-32022`, is what the probe now avoids.)*

#### 5.8.1 What was implemented (2026-07-31)

One correction to the analysis above, found by re-probing rather than by
re-reading it: **`negotiate_auto` being private does not make the modern era
private.** The pieces it drives — `ClientSession.discover()`, `send_discover()`,
`adopt()`, `protocol_version` — are all public on 2.0.0, and `discover()`
already carries the -32022 mutual-version retry. Only the *policy* had to be
written here, and policy is agentao's to own anyway.

`_compat.py` gains three probes (never a version string, per §5.1's rule):
`SUPPORTS_MODERN_ERA = hasattr(ClientSession, "discover")`, `McpProtocolError`
(1.x `McpError` / 2.x `MCPError`, renamed with no alias), and
`UNSUPPORTED_PROTOCOL_VERSION` read off `mcp.types` (`None` on 1.x, where
nothing can raise it).

`McpClient._negotiate()` sends `initialize()`, and escalates to `discover()`
**only** when the server rejects it with `-32022`.

##### Why not probe first — the measurement that decided it

The first cut led with `server/discover` (upstream's `mode='auto'` order) and
fell back on anything short of a usable `DiscoverResult`. Then it was run
against real servers, one per SDK generation, over stdio:

| Peer | Negotiated | Server stderr, per connect |
|---|---|---|
| `MCPServer` on mcp **2.0.0** | `2026-07-28` | 0 lines |
| `FastMCP` on mcp **1.26.0** | `2025-11-25` (fell back, worked) | **258 lines** |

The 1.x server rejects the unknown method by dumping a 31-error pydantic
*union*-validation failure — it tries `server/discover` against every member of
`ClientRequest` and prints all of them. For **stdio that stderr is agentao's
stderr** (`stdio_client` defaults `errlog=sys.stderr`, and agentao passes no
override), so it lands in the user's terminal on every connect and reconnect.

That is a per-connect cost on the most common kind of server today, paid for a
capability with **no consumer**: agentao does tool discovery and tool calls, and
both eras serve those identically — verified, not assumed, in the 2.0.0 row
above (modern era negotiated, `tools/list` and `tools/call` both fine).

So the order is inverted from upstream's, deliberately. The escalation is driven
by the server rather than by a guess: a modern-*only* server rejects the legacy
handshake with `-32022 UnsupportedProtocolVersion`, and that code — and only that
code — triggers one `discover()`, which itself retries at the highest mutual
version if the server names one. **That rejection is precisely the connect
failure §5.8 named as the trigger for doing any of this**, and it is now handled
at zero cost to every server that is not modern-only.

Re-measured after the flip: the 1.x server drops to 4 stderr lines (its own
ordinary `INFO` request logs — nothing from us).

##### What this gives up

A server supporting *both* eras stays on the handshake one, and its full
`supportedVersions` list is never learned. The tripwire is
`test_a_dual_era_server_is_left_on_the_handshake_era` (in
`tests/test_mcp_protocol_negotiation.py`), so the day it stops being acceptable,
the change is a deliberate edit rather than a surprise. The fix that day is to
lead with the probe again — `_negotiate`'s ordering is the whole switch, and the
noise above is what it will cost. Demand-gated per the §5.6 convention.

Smaller notes:

- Two codes escalate, with different weight. `-32022` is **definite** — the
  server is naming the versions it supports. `-32601` is **speculative**: the
  modern era replaces `initialize`, so a modern-only server has no handler for
  it, but neither does a URL that isn't an MCP endpoint. Worth one probe on a
  connect that has already failed; if the probe also fails, the *original*
  rejection surfaces unchanged rather than being renamed after a method the
  user never heard of.
- A failed escalation off a `-32022` raises `McpProtocolEraError` carrying
  **both** halves — the handshake rejection (the only one that names the
  server's `supported` list) and the probe's own error. Its type is
  load-bearing: `connect()` suppresses the "try `type: sse`" hint for it, since
  no transport change fixes a version mismatch, and on the 1.x cell that hint
  would otherwise ride every modern-only rejection.
- Anything else — a transport failure, any other JSON-RPC error — propagates.
  An outage is never reinterpreted as an era verdict.
- One bound is not ours: the SDK caps each `server/discover` send at its own
  `DISCOVER_TIMEOUT_SECONDS` (10 s on 2.0.0) and exposes no parameter to raise
  it, so a `timeout.startup` above that does not extend this round-trip. The
  enclosing `startup` budget still bounds the handshake as a whole.
- **Interactive input on the modern era is refused, in words the model can
  use.** The modern era re-encodes sampling / elicitation / roots: instead of a
  server→client request over a back-channel (which the SDK's own
  `NoBackChannelError` exists for), `tools/call` *returns* an
  `InputRequiredResult` carrying the requests plus an opaque `requestState`,
  and the client answers and calls again. agentao wires none of the three
  resolvers in *either* era — on the handshake era the SDK's defaults answer
  "not supported" — so the capability gap is not new; only the failure shape
  is. Left alone the SDK raises `RuntimeError("… pass
  allow_input_required=True … retry call_tool(…)")`, an instruction addressed
  to a *programmer*, which `call_tool`'s broad `except` would hand to the LLM
  as the tool's result. So `call_tool` opts in to *receive* the result
  (`allow_input_required=True`, probed) and names what was asked for instead.
  The sibling `UnexpectedClaimedResult` arm leaks the same way and is caught by
  type alongside it. Neither is reachable today: the modern era needs a
  modern-only peer, and no SDK can build one. Implementing the protocol —
  answering the requests and retrying — remains out of scope.

The outcome lands on `McpClient.protocol_version` — read off the
`InitializeResult`, because 1.x's `ClientSession` keeps no public record of what
it negotiated — and is surfaced through `get_server_status()["protocol"]` and
`/mcp list`. It is a **ceiling, not a constant**; gate on `>=`, never equality.

Tests: `tests/test_mcp_protocol_negotiation.py`, driving a real `ClientSession`
over the SDK's in-memory streams against a hand-rolled JSON-RPC peer. A stubbed
`session.discover()` would have restated agentao's assumptions instead of
testing them; the wire is where the era split is actually observable. Green on
all three `mcp-spec` cells (`1.26.0`, `1.29.0`, `2.0.0`). The escalation path is
covered there and **not** against a live server — the 2.x SDK has no knob to
make a server modern-only, so no such peer can be built today.

One thing the real-wire harness caught that a stub would not: in the modern era
`tools/list` results are **validated per version**, and `ttlMs` / `cacheScope` /
`resultType` are required there while the handshake era defaults them.

## 6. ACP changes — flip the three gates

All three were written to reject `http` *because the client couldn't dispatch
it*. §5 removes that reason, so each gate opens. Update the docstrings that
explain the rejection in the same edit (they will otherwise lie —
cf. the "comment lies" watch-items in `project_hermes_pull_review_20260629`).

1. **`acp/initialize.py:75-78`** — `mcpCapabilities = {"http": True, "sse": True}`.
   Update the docstring at 24-26 and the inline comment at 70-74.
2. **`acp/session_new.py:226-232`** — accept `"http"` in the type set. The
   URL-field validation branch is already `else: # http or sse` (line 248), so
   only the guard set at 227 widens: `("stdio", "sse", "http")`. Fix the
   docstring at 204-210.
3. **`acp/mcp_translate.py:217-244`** — the `sse` branch becomes
   `elif transport_type in ("sse", "http")` and, **critically, stamps the
   explicit transport into the produced cfg for _both_ URL transports**:

   ```python
   elif transport_type in ("sse", "http"):
       ...
       cfg = {"url": url, "type": transport_type}   # always stamp — both ways
       ...
   ```

   The stamp is **mandatory in both directions** under D2's http default: an ACP
   `sse` entry that produced a bare `{"url": ...}` would be read back by
   `resolve_transport` as *http* (the new default) and connect to the wrong
   transport — the exact inverse of the pre-flip hazard. Stamping the explicit
   `type` makes the translation independent of whatever the native default is,
   so it stays correct across any future default change. Update the module
   docstring at 63-69 and the dropped-branch comment at 233-237 (the `else` now
   only catches genuinely unknown types).

## 7. CLI change (`agentao/cli/commands/mcp.py`)

`/mcp add` currently writes `{"url": endpoint}` for any `http(s)://` endpoint
(53-54) — always SSE. Flip the flag-less URL default to Streamable HTTP (to
match D2) and add a `--sse` opt-out:

```
/mcp add <name> <url>                            # → { "url": ... }  (bare → inferred http, D2)
/mcp add --sse  <name> <url>                     # → { "type": "sse",  "url": ... }  (legacy SSE)
/mcp add --http <name> <url>                     # → { "type": "http", "url": ... }  (explicit)
/mcp add <name> <command> [args...]              # stdio (unchanged)
```

Parse a `--sse`/`--http` flag that may appear **before or after the name**
(review Finding 2 — `gh --http <url>` is a common ordering and must not fall
through to a `{"command": "--http"}` stdio config). Config written:

- flag-less URL → **bare `{"url": endpoint}`** (no `type`). It resolves to http
  (D2) but stays *inferred*, so if the endpoint is really legacy SSE the
  connect-failure hint (§5.7, gated to inferred) fires and guides the user to
  `--sse` (review Finding 4 — an explicit `type:"http"` would suppress that
  hint, the exact recovery guidance CLI-added servers need).
- `--http` → `{"type": "http", "url": endpoint}` (explicit choice, no hint).
- `--sse` → `{"type": "sse", "url": endpoint}`.

Update the usage/example block (44-48) to lead with the Streamable HTTP form.

## 8. Docs

- `docs/reference/configuration.md` — the transport table (197-198) gains a
  **Streamable HTTP** row: `url` (+ optional `type: "http"`) required key,
  `headers` / `timeout` / `trust` optional; and a short note that **bare `url` =
  Streamable HTTP (D2 default)** while `type: "sse"` opts into legacy SSE. Update
  the timeout bullet at 202 ("SSE HTTP-connection open" → "URL-transport HTTP
  open"). Add the §10 migration note near the transport table.
- `config.py` `McpServerConfig` docstring (14-32) — add the `type` key and a
  "Streamable HTTP transport" stanza mirroring the "SSE transport" one.
- `CLAUDE.md` § MCP — the transport list ("`command` (stdio subprocess) or `url`
  (SSE)") becomes "`command` (stdio) or `url` (Streamable HTTP by default; add
  `type: "sse"` for the legacy SSE transport)".
- `cli/help_text.py` (85) — the **authoritative** `/help` source; update the
  `/mcp add` line to `[--http|--sse]` (review Finding 7 — CLAUDE.md designates
  this file authoritative, so it must not lag the in-command usage string).

## 9. Test plan

New `tests/test_mcp_streamable_http.py`:

- `resolve_transport` happy rows: every valid row of the §4 table incl. alias
  normalization (`streamable-http`/`streamable_http`/case).
- `resolve_transport` **fail-closed** (Findings 1 & 3):
  - explicit-but-unknown `type` (`"see"`, `"streamable"`, `""`) → raises
    `McpTransportConfigError` — **it does not silently become http**;
  - required-key mismatch (`{"type":"http"}` w/o `url`, `{"type":"stdio"}` w/o
    `command`, `{"type":"http","command":...}` w/o `url`) → raises;
  - `transport_type` *property* returns `"unknown"` (never raises) for all of the
    above, while `connect()` records the actionable message in `error_message`.
- Dispatch: `connect()` routes both `type:"http"` **and bare `url` (no type)**
  to `_connect_streamable_http` (monkeypatch `streamable_http_client` +
  `create_mcp_http_client` to fakes; the http one yields a 3-tuple), while
  `type:"sse"` routes to `_connect_sse` — **the D2 default assertion (bare `url`
  = http).**
- §5.7 hint gating: a bare-`url` handshake failure appends the SSE hint; an
  explicit `type:"http"` failure does **not**; an SSE failure does not; a
  bare-`url` `NonMcpEndpointError` (HTML page) does not; **and a bare-`url` auth
  failure does not** (review Finding 5).
- 3-tuple unpack: the fake yields `(read, write, get_session_id)`; assert the
  session is built and the callback is not required.
- Timeouts: `create_mcp_http_client` receives `httpx.Timeout(startup,
  read=sse_read_timeout)` (assert `.connect` / `.read`); `terminate_on_close` is
  **False** (review Finding 1).
- CLI `/mcp add` (review Findings 2 & 4): a bare URL writes `{"url": ...}` (no
  `type`); `--http`/`--sse` write explicit types and are honored **before or
  after** the name; a stdio add is unaffected.
- Preflight reuse: a Streamable HTTP endpoint returning `application/json`
  passes; `text/html` raises `NonMcpEndpointError` (extend
  `test_mcp_preflight.py`).

Update existing:

- `test_acp_initialize.py:89`, `test_acp_schema.py` inline fixtures — expected
  `mcpCapabilities` flips to `{"http": True, "sse": True}`; regenerate the
  checked-in `docs/schema/host.acp.v1.json` snapshot (the `type` enum gains
  `http`).
- `test_acp_session_new.py` — the `http`-rejection case becomes a `http`-accept.
- `test_acp_mcp_injection.py` — the SSE-translation assertion now also carries
  `"type": "sse"`; add an `http` entry asserting the `"type": "http"` stamp; the
  translate-rejects-http class becomes translate-http (unknown type still drops).
- `test_mcp_connect_timeouts.py` — the two `connect()` tests pin `type:"sse"`
  (a bare `url` now dispatches to http, bypassing the `_connect_sse` patch).

## 10. Rollout, non-goals, future

**Rollout — breaking change (D2).** Bare `url` flips from SSE to Streamable
HTTP, so this is **not** backward-compatible for URL servers. Land as one PR
(client + ACP + CLI + docs + tests) since the ACP gates are meaningless to flip
without the client, and pointless to leave rejecting once the client lands.
Verify the merged tree runs green before merge (cf. "never merge red CI").

**Migration line (release notes / CHANGELOG):** *"MCP: a bare `url` server now
defaults to the Streamable HTTP transport. If your server is a legacy SSE
endpoint, add `\"type\": \"sse\"` to its entry in `.agentao/mcp.json`. Streamable
HTTP is the spec's replacement for the now-deprecated HTTP+SSE transport."* The
§5.7 connect-failure hint surfaces the same fix in-context for anyone who misses
the note.

**Non-goals (v1):**
- Surfacing/using the `Mcp-Session-Id` (the discarded 3rd tuple element) or
  explicit session resumption — the reconnect-once loop is sufficient.
- OAuth/`auth=` on `streamablehttp_client` — headers (incl. `Bearer $TOKEN` via
  env expansion) cover the current bearer-token case; interactive OAuth is a
  separate, larger design.
- Auto-fallback (the D2 alternative that was rejected).
- A one-time *runtime deprecation warning* on every bare-`url` config. Rejected
  as noisy — it would fire even for the now-correct http case. The §5.7
  on-failure hint is the targeted substitute; the §10 migration line covers the
  broad announcement.

## 11. Blast radius

| File | Change |
|---|---|
| `agentao/mcp/config.py` | `resolve_transport()` (fail-closed) + `McpTransportConfigError`; `type` in `McpServerConfig` docstring; timeout-doc wording |
| `agentao/mcp/client.py` | import (canonical `streamable_http_client` + `create_mcp_http_client`); `transport_type` (swallow-to-`unknown`)/`connect` → `resolve_transport`; `_connect_streamable_http` (3-tuple, `terminate_on_close=False`); `_prepare_url_connect` helper; §5.7 gated hint |
| `agentao/acp/initialize.py` | `mcpCapabilities.http = True` + docstrings |
| `agentao/acp/session_new.py` | parser accepts `http` + docstring |
| `agentao/acp/mcp_translate.py` | translate `http`, **stamp `type` both ways** + docstrings |
| `agentao/acp/schema.py` | `mcpCapabilities` default + `AcpMcpServer.type` `Literal` gains `http` + docstring |
| `docs/schema/host.acp.v1.json` | regenerated snapshot (`type` enum + capability default) |
| `agentao/cli/commands/mcp.py` | `/mcp add [--http|--sse]` (flag before/after name; bare URL → no `type`) |
| `agentao/cli/help_text.py` | authoritative `/help` `/mcp add` line |
| `docs/reference/configuration.md` | Streamable HTTP transport row + timeout note |
| `CLAUDE.md` | MCP transport line |
| `tests/test_mcp_streamable_http.py` (new) + `test_mcp_connect_timeouts.py` + 4 ACP tests | see §9 |

## 12. Commit checklist

Dependency-ordered — each stage compiles/tests on top of the previous, so an
implementer can work top-to-bottom and the PR bisects cleanly. Line anchors are
`main`@`8bcb1b1`. The whole thing lands as **one PR** (§10); the stages below can
be separate commits within it.

### Stage 0 — baseline

- [ ] Branch `feat/mcp-streamable-http` off `main`.
- [ ] Record green baseline: `uv run python -m pytest tests/ -q` (note the count;
      the merged tree must be ≥ baseline + new tests, all green — never merge red
      CI).
- [ ] (Already verified, re-confirm on the target machine) the pinned SDK: the
      canonical `streamable_http_client` yields a **3-tuple** and accepts
      `terminate_on_close`; the deprecated `streamablehttp_client` alias emits a
      `DeprecationWarning` (use the canonical) — `uv run python -c "import inspect,mcp.client.streamable_http as m; print(inspect.signature(m.streamable_http_client))"`.

### Stage 1 — `agentao/mcp/config.py` (foundation, no in-tree deps)

- [ ] Add `class McpTransportConfigError(ValueError)`.
- [ ] Add `resolve_transport(config, *, return_source=False) -> str`
      implementing D3: lowercase + alias-fold `type`
      (`streamable-http`/`streamable_http`/`streamablehttp` → `http`); **raise
      `McpTransportConfigError`** on an explicit-but-unknown `type`; D2 inference
      when absent (`command`→stdio, `url`→http, else `"unknown"`); **required-key
      validation** (stdio needs `command`, sse/http need `url`) → raise on
      mismatch. With `return_source=True`, also return `"explicit"|"inferred"`
      for the §5.7 hint (or expose a sibling `transport_is_inferred(config)`).
- [ ] Extend the `McpServerConfig` docstring (14-32): add the `type` key and a
      "Streamable HTTP transport" stanza mirroring the "SSE transport" one.
- [ ] Reword the `resolve_timeouts` docstring (100-103): "handed to the SSE
      transport" → "handed to the URL transport (SSE or Streamable HTTP)".

### Stage 2 — `agentao/mcp/client.py`

- [ ] `from mcp.client.streamable_http import create_mcp_http_client,
      streamable_http_client` (top, by the `sse_client` import at 14) — the
      **canonical** functions, not the deprecated `streamablehttp_client` alias.
- [ ] `transport_type` property → `try: return resolve_transport(self.config)
      except McpTransportConfigError: return "unknown"` (never raises — status
      path stays total).
- [ ] `connect()` (183-190): dispatch on the **raising** `resolve_transport`;
      keep the "No transport configured" `ValueError` for the `"unknown"` case.
- [ ] Extract `_prepare_url_connect(startup, request) -> (url, headers,
      sse_read_timeout)` (url/headers read + `sse_read_timeout` raise-only calc
      at 332-336 + `_preflight_content_type`); refactor `_connect_sse` (318-353,
      **2-tuple**) to use it.
- [ ] Add `_connect_streamable_http` using the helper: build the httpx client
      with `create_mcp_http_client(headers, httpx.Timeout(startup,
      read=sse_read_timeout))`, enter it into the exit stack, then
      `streamable_http_client(url, http_client=..., terminate_on_close=False)` +
      **3-tuple unpack** `read, write, _get_session_id = transport`.
- [ ] §5.7 hint in `connect()`'s `except` (211-221): append the
      set-`type:"sse"` hint **only when** `transport == "http"` **and**
      `source == "inferred"` **and** `not isinstance(e, NonMcpEndpointError)`
      **and** `classify_mcp_error(e) is not McpErrorKind.AUTH`.
- [ ] Leave `classify_mcp_error` / `_ERROR_RULES` unchanged (§5.6 watch-item).

### Stage 3 — ACP (flip the three gates; fix the docstrings so they don't lie)

- [ ] `acp/initialize.py`: `mcpCapabilities` (75-78) → `{"http": True, "sse":
      True}`; update the docstring (24-26) and inline comment (70-74).
- [ ] `acp/session_new.py`: accept `"http"` in the type guard (227); fix the
      `_parse_mcp_servers` docstring (204-210) that says http is rejected.
- [ ] `acp/mcp_translate.py`: `elif transport_type in ("sse", "http")` (217);
      `cfg = {"url": url, "type": transport_type}` — **stamp both** (§6.3); update
      the module docstring (63-69) and the dropped-branch comment (233-237).

### Stage 4 — CLI `agentao/cli/commands/mcp.py`

- [ ] `/mcp add` (41-69): parse a `--sse`/`--http` flag accepted **before or
      after the name**; flag-less URL → **bare `{"url": endpoint}`** (inferred,
      hint-eligible), `--http` → `{"type":"http",...}`, `--sse` →
      `{"type":"sse",...}`. Update the usage/example block (44-48).
- [ ] `cli/help_text.py` (85): update the authoritative `/help` `/mcp add` line
      to `[--http|--sse]` (review Finding 7).

### Stage 5 — docs & release note

- [ ] `docs/reference/configuration.md`: Streamable HTTP transport row (197-198);
      timeout bullet (202) wording; the §10 migration note near the table; state
      bare `url` = Streamable HTTP, `type:"sse"` for legacy SSE.
- [ ] `CLAUDE.md` § MCP: transport line → "`command` (stdio) or `url` (Streamable
      HTTP by default; add `type:"sse"` for legacy SSE)".
- [ ] CHANGELOG / release notes: the **BREAKING** migration line from §10.

### Stage 6 — tests

- [ ] New `tests/test_mcp_streamable_http.py` — all §9 cases: `resolve_transport`
      happy rows + **fail-closed** (unknown `type`, required-key mismatch, property
      returns `"unknown"`); dispatch (`type:"http"` **and bare `url`** →
      `_connect_streamable_http`, `type:"sse"` → `_connect_sse`); 3-tuple unpack;
      timeouts + `terminate_on_close=False`; **§5.7 hint gating incl. the
      `NonMcpEndpointError` and AUTH skips**; CLI `/mcp add` flag/bare-url cases.
- [ ] `tests/test_acp_initialize.py:89` → `{"http": True, "sse": True}` (the real
      advertised-value assertion).
- [ ] `tests/test_acp_schema.py` inline `mcpCapabilities` fixtures → `{"http":
      True, "sse": True}`; **regenerate `docs/schema/host.acp.v1.json`** via
      `normalized_schema_json(export_host_acp_json_schema())` (the `type` enum
      gains `http`).
- [ ] `tests/test_acp_session_new.py` — flip the `http`-**rejection** case to a
      `http`-**accept** case.
- [ ] `tests/test_acp_mcp_injection.py` — the SSE-translation assertion now also
      carries `"type": "sse"`; add an `http` entry asserting the `"type": "http"`
      stamp; the translate-rejects-http class becomes translate-http.
- [ ] `tests/test_mcp_connect_timeouts.py` — the two `connect()` tests pin
      `type:"sse"` (a bare `url` now dispatches to http, bypassing the patch).

### Stage 7 — verify (fail-closed self-review, per the grep-first ethos)

- [ ] Full suite green: `uv run python -m pytest tests/ -q`.
- [ ] Targeted: `uv run python -m pytest tests/test_mcp_*.py tests/test_acp_*.py -q`.
- [ ] No stale claims survive:
      `grep -rn "streamable_http_client\|http is not supported\|only supports.*sse\|mcpCapabilities.*http.*[Ff]alse" agentao/`
      returns only intended matches (comments now describing history, none
      asserting current behavior).
- [ ] No lying docstrings: the three ACP files no longer say `http` is rejected.
- [ ] Smoke: monkeypatch `streamable_http_client` + `create_mcp_http_client` to
      fakes (3-tuple) and drive `McpClient.connect()` → CONNECTED + tools listed;
      if a real Streamable HTTP server is reachable, `/mcp add <url>` then
      `/mcp list` shows connected and a tool call round-trips. (`verify` skill.)

### Stage 8 — commit / PR

- [ ] Suggested commit: `feat(mcp): add Streamable HTTP transport; bare url now
      defaults to http` (conventional-commit scope, matches repo history).
- [ ] PR body: **BREAKING CHANGE** callout + the §10 migration line + a link to
      this design doc (`docs/design/mcp-streamable-http.md`).
- [ ] End the commit message with the `Co-Authored-By` trailer.
- [ ] CI green before merge; if rebased, re-run the suite on the merged tree
      (semantic conflicts pass text-merge but can break — never merge red CI).
