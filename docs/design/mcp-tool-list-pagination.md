# MCP tool-list pagination — Design

**Status:** **Implemented 2026-08-03.** Fixed a live silent-truncation defect:
agentao issued exactly one `tools/list` call per MCP server and never read the
response's pagination cursor, so every tool past the first page was invisible to
the model with no error and no warning. The borrow source is codex's
`2026-07-24..2026-08-03` window, which shipped the *bounds* for this loop
(#36039, #35724) — the caps were taken with the loop, not after it.

Landed in `agentao/mcp/client.py` (`_list_all_tools`, `_MAX_TOOL_PAGES` /
`_MAX_TOOLS` / `_MAX_CURSOR_BYTES`), with `tests/test_mcp_tool_list_pagination.py`
and the shared `tools_result` / `paging_session` fakes in `tests/support/mcp.py`.
Three fakes that declared `list_tools(self)` were updated to the real SDK's
keyword-only `params` and to return real `ListToolsResult`s rather than
`MagicMock` / `SimpleNamespace`. Verified green on **both SDK majors** — the
2.0.0 lock and the 1.26.0 floor — and each of the three correctness fixes below
was confirmed by mutation: reintroducing it fails exactly the test written for
it, and nothing else.

**Review round 1 (2026-08-03)** corrected three correctness defects in the
original draft and collapsed the open decision:
`if cursor` → `cursor is not None` (§5.2), the item cap moved ahead of
`tools.extend` (§5.2/§5.3), the cursor cap changed to UTF-8 bytes (§5.3), the
§6.2 permission claim rewritten after it proved false, and D1 resolved to a
single fail-the-server rule (§5.4) once the isolation was traced to
`connect()`'s own handler rather than `_connect_one`.

**Review round 2 (2026-08-03)** fixed four accuracy defects, all in prose
rather than in the design: the empty-string cursor produces a wasted request
and a *wrong verdict*, not an infinite loop (§5.2 — the repeated-cursor guard
does catch it on the second pass); the item cap is a **catalog-accumulation**
bound, not a DoS/wire bound, since the SDK has already parsed the response
(§5.3, §8); the single-page-overflow test **cannot** pin check-before-`extend`
and must not pretend to (§7); and `permissions.json` *does* cover MCP tools, so
only `enabled_tools` and the name prefix are non-boundaries (§6.2). §5.2's
prose was also compressed against §5.3's table.

**Review round 3 (2026-08-03, xhigh multi-agent)** fixed five issues in the
landed code and tests, and recorded three it did not: bound failures now raise
`McpCatalogError` and are excluded from `connect()`'s "try `type: sse`" hint
(that hint is reached only *after* the transport provably worked); the redundant
`list(result.tools or [])` copy is gone (`tools` is required non-null on every
major); `paging_session` became cursor-keyed instead of call-counted, so a fake
can no longer hand back page 2 for the wrong cursor; the `connect_all` test now
closes its event loop and no longer claims to pin `gather`-level isolation it
cannot fail on; and a **non-`@modern_only` real-wire test** now pins the
`params=` contract through a genuine `ClientSession`, which the 1.x CI cells
previously never exercised. The CLAUDE.md permission rewrite from round 2 was
itself corrected — the read-only mode preset short-circuits *ahead* of the
engine. See §9 for the three regression profiles left unfixed by choice.

Verified against `main`@`6383d23` (2026-08-03): `agentao/mcp/client.py:290-292`
(the single-call handshake), `:310-347` (`connect()`'s catch-all — sets `ERROR`,
does **not** re-raise), `:349-356` (`_handshake`), `:912-920` (`_connect_one` /
`gather`, a second belt that does not fire on this path),
`:958-972` (`get_server_status`), `agentao/mcp/_compat.py:37-60` (`field`),
`agentao/mcp/config.py:74` (`_DEFAULT_STARTUP_TIMEOUT = 60.0`), `:125-163`
(`resolve_timeouts`), `agentao/mcp/tool.py:20-22` (`make_mcp_tool_name`),
`:111-117` (`requires_confirmation` — trusted servers skip confirmation),
`agentao/tooling/registry.py:170-178` (`apply_enabled_tools` leaves `mcp_*`
untouched), `agentao/tooling/mcp_tools.py:115-118` (connect-error handling).

**Audience:** Agentao maintainers.

**Companion:** `mcp-tool-list-pagination.zh.md`.

**Related:**
- `mcp-streamable-http.md` — the transport contract this sits on; §5.1/§5.3
  carry the mcp-2.0 update blocks whose compat discipline §4 below follows.
- `project_mcp_protocol_negotiation` (PR #158) — the handshake `_negotiate`
  runs immediately before the `list_tools` call this design wraps.
- `project_mcp_sdk_2x_compat` (PR #148) — `_compat.py`, and the rule that every
  cross-major difference is **probed off the installed SDK**, never sniffed
  from a version string.
- `docs/reference/configuration.md` — MCP `timeout` schema (`startup` /
  `request`), the budget §5.4 reuses unchanged.

**Method:** every claim below is anchored to source at `main`@`6383d23`. SDK
surfaces were **probed from three actually-installed majors** — 1.26.0 (the
declared floor), 1.29.0 (latest 1.x), 2.0.0 (the lock) — not read off a
signature in the abstract and not inferred from a version string. The three
probed cells are exactly the three CI cells at `.github/workflows/ci.yml:148`.

---

## 1. The gap

`McpClient` discovers a server's tools with a single round-trip:

```python
# agentao/mcp/client.py:349-356
async def _handshake(self):
    """Settle the protocol era, then run the ``list_tools()`` round-trip."""
    await self._negotiate()
    return await self._session.list_tools()

# agentao/mcp/client.py:290-292
self._tools = (
    await asyncio.wait_for(self._handshake(), timeout=startup_timeout)
).tools
```

`.tools` is the first page. The cursor that says "there is more" is never read:

```
$ grep -rn --include='*.py' -E 'next_cursor|nextCursor|PaginatedRequestParams' agentao/ tests/
(no matches)
```

Zero matches **repo-wide, including tests**. A server that paginates its
catalog loses every tool after page 1.

## 2. Why this is the bad kind of bug

There is no error path. The SDK returns a well-formed `ListToolsResult`, agentao
takes `.tools`, and the connect succeeds. The truncated catalog is then
registered normally (`tooling/mcp_tools.py:120`), the missing tools simply do
not exist as far as the model is concerned, and nothing anywhere — log line,
`/mcp list`, `get_server_status()` — distinguishes "this server has 12 tools"
from "this server has 12 of its 400 tools". The user's report will be *"the
model won't use my tool"*, and every obvious place to look will be clean.

This is worth stating plainly because it changes the priority: the cost is not
the missing tools, it is that the failure is indistinguishable from correct
operation.

## 3. Borrow source

Codex reached the same code from the opposite end — it already paginated, and
hardened an unbounded loop. Two commits in the reviewed window:

| Commit | What it added |
|---|---|
| `be2e4afcd7` (#35724) | `collect_paginated` — the shared cursor loop, rejecting repeated cursors |
| `3e3ae08839` (#36039) | The caps: 100 pages, 1,024 items, 64 KiB cursor, whole-loop timeout |

The collector is ~35 lines (`codex-rs/codex-mcp/src/pagination.rs`). Its
structure ports directly; the *values* need agentao-specific justification,
which §5.3 gives.

Taking the caps in the same change as the loop is the whole point of borrowing
here rather than writing the loop from scratch. An unbounded `while cursor:`
against an untrusted peer is a hang, and a repeated cursor is an infinite one.

## 4. Cross-major SDK probe — the load-bearing constraint

Agentao supports `mcp>=1.26.0,<3` and CI runs three cells
(`ci.yml:148`: `mcp==1.26.0`, `mcp>=1,<2`, `mcp>=2,<3`). Pagination touches
both a **method signature** and a **wire field name**, and the two behave
differently across the major split. Probed, not recalled:

```
mcp 1.26.0  list_tools(self, cursor: str|None = None, *, params: PaginatedRequestParams|None = None)
            ListToolsResult fields:        ['meta', 'nextCursor', 'tools']
            PaginatedRequestParams fields: ['task', 'meta', 'cursor']

mcp 1.29.0  list_tools(self, cursor: str|None = None, *, params: PaginatedRequestParams|None = None)
            ListToolsResult fields:        ['meta', 'nextCursor', 'tools']
            PaginatedRequestParams fields: ['task', 'meta', 'cursor']

mcp 2.0.0   list_tools(self, *, params: PaginatedRequestParams|None = None)
            ListToolsResult fields:        ['meta', 'ttl_ms', 'cache_scope', 'next_cursor', 'tools', 'result_type']
            PaginatedRequestParams fields: ['meta', 'cursor']
```

Two findings, and they point in opposite directions:

**(a) The call needs no new compat shim.** The positional `cursor=` exists only
on 1.x — it is *gone* in 2.0.0 — but `params=PaginatedRequestParams(cursor=…)`
is present and keyword-accepted on **all three cells**, including the 1.26.0
floor. So `params=` is the one spelling that works everywhere. Had the floor
lacked `params`, this design would have needed a `_compat.py` probe; it does
not. Note this is a fact about the *floor*, so it is pinned by the CI cell — if
the floor is ever lowered, re-probe before assuming it still holds.

**(b) The field read does need the existing shim.** `nextCursor` (1.x) →
`next_cursor` (2.x) is precisely the camelCase→snake_case rename `_compat.field`
exists for, and it is the case that helper was written to get right:
`ListToolsResult` is `extra='allow'` on both majors, so a `hasattr`/`getattr`
probe on 1.x would resolve a server-supplied `next_cursor` *extra* in preference
to the SDK-validated `nextCursor` field. Use `field(result, "nextCursor",
"next_cursor")` and nothing else.

## 5. Design

### 5.1 Where the loop goes

Inside `_handshake` (`client.py:349-356`), replacing the single call. This is
the placement that costs nothing, because `connect` already wraps the whole of
`_handshake` in `asyncio.wait_for(..., timeout=startup_timeout)` at `:290-292`.
The overall-loop timeout that codex had to add explicitly (#36039: "bound the
entire pagination operation by the configured tool timeout, falling back to 30
seconds") **agentao gets for free** — every page lands inside the existing 60s
`startup` budget (`config.py:74`), user-configurable via
`timeout: {"startup": N}`.

The comment at `:281-288` explaining why `wait_for` is safe here — plain awaits
on an established session, entering no exit-stack context, so a cancellation
never crosses an anyio scope into transport cleanup — holds unchanged for N
round-trips. Nothing about that argument was per-call.

### 5.2 Shape

```python
# agentao/mcp/client.py
_MAX_TOOL_PAGES = 100
_MAX_TOOLS = 1024
_MAX_CURSOR_BYTES = 64 * 1024


async def _handshake(self):
    await self._negotiate()
    return await self._list_all_tools()

async def _list_all_tools(self) -> list:
    tools: list = []
    seen_cursors: set[str] = set()
    cursor: Optional[str] = None

    for _ in range(_MAX_TOOL_PAGES):
        # params=None on the first page — byte-identical to today's call.
        # ``cursor is not None``, never ``if cursor``: see below.
        params = (
            PaginatedRequestParams(cursor=cursor) if cursor is not None else None
        )
        result = await self._session.list_tools(params=params)

        # Check BEFORE extending — a single page may itself exceed the cap.
        if len(tools) + len(result.tools) > _MAX_TOOLS:
            raise McpCatalogError(
                f"MCP server '{self.name}' exceeded the {_MAX_TOOLS}-tool "
                f"catalog limit"
            )
        tools.extend(result.tools)

        cursor = field(result, "nextCursor", "next_cursor")
        if cursor is None:
            return tools
        if len(cursor.encode("utf-8")) > _MAX_CURSOR_BYTES:
            raise McpCatalogError(
                f"MCP server '{self.name}' returned a tools/list pagination "
                f"cursor larger than {_MAX_CURSOR_BYTES} bytes"
            )
        if cursor in seen_cursors:
            raise McpCatalogError(
                f"MCP server '{self.name}' returned a repeated tools/list "
                f"pagination cursor"
            )
        seen_cursors.add(cursor)

    raise McpCatalogError(
        f"MCP server '{self.name}' exceeded {_MAX_TOOL_PAGES} pages of "
        f"tools/list"
    )
```

Two choices the §5.3 table cannot carry:

1. **`cursor is not None`, not `if cursor`.** An MCP cursor is an opaque
   *string*, and `""` is a legal one — it means "here is your resumption
   token", not "no more pages". Truthiness rewrites it to `params=None` and
   **re-requests page 1**. The damage is not a hang: `""` was already stored in
   `seen_cursors` on the first pass, so the repeated-cursor guard fires on the
   second. The result is one wasted request followed by a **wrong verdict** —
   a spec-legal server is reported as having repeated a cursor and, under §5.4,
   dropped entirely. §7 pins it with a `nextCursor=""` test.
2. **A bounded `for`, not `while True`.** Falling out of the loop still holding
   a cursor *is* the page-cap failure, so there is no separate counter to keep
   in sync.

The other two ordering details — the item cap gating `tools.extend`, and the
cursor cap counting UTF-8 bytes — are recorded in §5.3's table.

The first iteration passes `params=None`, which is the SDK default — so for the
overwhelmingly common single-page server the wire traffic is **unchanged**, not
merely equivalent. That property is what makes this safe to land without a
staged rollout, and §7 pins it with a test.

`_handshake` returning a `list` instead of a `ListToolsResult` also removes the
`.tools` unwrap at `:292`; that unwrap is the only consumer.

### 5.3 The bounds

| Bound | Value | Enforcement point | Rationale |
|---|---|---|---|
| Repeated cursor | reject | after reading the next cursor | A server repeating a cursor is an **infinite loop**. Non-negotiable; this is the bound that makes the loop safe to write at all. |
| Cursor size | 64 KiB, **UTF-8 bytes** | after reading the next cursor | Codex's value, adopted unchanged. A cursor is an opaque resumption token; 64 KiB is already absurd and the check is one comparison. Count bytes — `len()` on a multibyte cursor allows up to 4× the budget. |
| Pages | 100 | loop bound; exhausting it with a cursor in hand is the failure | Codex's value. With the item cap this is belt-and-braces, but it bounds a server that paginates one tool at a time. |
| Items | 1,024 | **before `tools.extend`** | Codex's value — see the caveat below. Bounds the **accumulated catalog**: gating the merge keeps an oversized page out of the accumulator instead of copying it in and only then objecting. |
| Whole loop | existing `startup_timeout` (60s default) | `asyncio.wait_for` at `:290-292` | Already present; §5.1. |

**What the item cap does *not* bound: the wire.** By the time `_list_all_tools`
sees `result.tools`, the SDK has already read the response off the transport,
parsed it, and constructed the models. A single page carrying 100,000 tools has
therefore already cost its response body and deserialization before any cap
here can speak. This bound is a **catalog-accumulation / registration** limit,
not a transport limit — see §8 for why the transport limit stays out of scope.

**Caveat on the item cap — it does not mean what it means in codex.** Codex can
afford 1,024 tools because it has `tool_search` deferred loading, so a large
catalog does not enter the model's context. Agentao has no such mechanism
(`docs/design/tool-search.md` is *draft, deferred*); every registered MCP tool
goes into the function list on every request. So agentao's *practical* ceiling
is far below 1,024, and a server approaching this cap will have destroyed the
context window long before tripping it.

The honest framing: **1,024 is a catalog-accumulation bound — not a context
bound, and not a wire bound.** It is not this design's job to fix agentao's
lack of tool-count management, and this change does not make that problem worse
— it makes a pre-existing unbounded surface (today a server can return 5,000
tools *on page 1* and agentao registers all of them) bounded for the first
time. Context management is separate work; do not let this cap stand in for it.

### 5.4 What happens when a bound trips — **one rule: fail the server**

Every bound raises. Repeated cursor, oversized cursor, item cap, page cap — all
four abort the connect for that one server. No partial catalogs, no truncation
mode.

**Where the isolation actually comes from.** `connect()` wraps its whole body in
`except Exception` at `client.py:310-347`: it sets `status = ServerStatus.ERROR`,
stores `error_message`, tears down the session and exit stack — and **does not
re-raise** (no `raise` anywhere in that block). So a raise from
`_list_all_tools` never escapes `connect()`. `_connect_one`'s own
`except`/`gather(return_exceptions=True)` at `:912-920` is a second belt that
does not fire on this path at all. An earlier revision of this document
attributed the isolation to `_connect_one`; that was wrong, and the correction
*strengthens* the single-rule policy rather than weakening it:

- The failure is already surfaced through an existing channel.
  `get_server_status()` (`client.py:958-972`) reports `status` and `error` for
  every server, so a bounds violation shows up as `ERROR` plus a message naming
  the cap. **No `"truncated"` field is needed** — the status channel already
  says everything a truncation flag would have said, and says it in the same
  shape as every other connect failure.
- Other servers are unaffected, because each `McpClient.connect()` handles
  itself.

**What this deletes** relative to a split raise/truncate policy: no `_truncated`
lifecycle state, no `get_server_status()` extension, no truncation-warning
branch, and no semantics to define for "how many do we keep" or "is a partially
consumed overflow page retained". A truncated catalog is also precisely the
failure mode §2 exists to eliminate — keeping a quieter version of it as a
supported outcome would undercut the whole change.

The cost is real and worth stating: a well-formed server with 1,025 tools loses
all of them rather than 1,024 of them. That is the right trade at this cap,
because §5.3's caveat applies — a catalog that large is unusable in agentao's
flat tool list regardless, so the useful signal is "this server does not fit",
not a silently clipped prefix of it.

## 6. Behavior changes

1. **Single-page servers: identical wire traffic (§5.2) — but *not*
   unconditionally identical behavior.** An earlier revision of this line said
   "none", which was false: `_MAX_TOOLS` is checked on page 1 too, so a server
   that returns **more than 1,024 tools in one uncursored response** now fails
   its connect where it previously registered all of them. Such a server never
   enters the loop this design adds, and is the one profile where the change is
   a pure regression rather than a fix.

   Kept deliberately, because the alternative — exempting page 1 from the cap —
   would leave the largest single-response catalogs unbounded, which is the
   pre-existing hole §5.3 says this closes for the first time. But it is a real
   trade, it was not visible when the fail-closed rule was approved, and it is
   the first thing to revisit if the caps are ever made configurable
   (see §9).
2. **Multi-page servers: more tools appear, and some may be directly
   callable.** This is the intended fix, but it widens the registered tool
   surface. Precisely which gates apply:

   - **`permissions.json` *is* a real boundary and does cover MCP tools.**
     `PermissionEngine.decide_detail(tool_name, tool_args)` runs for every tool
     call that gets past the mode preset, *before* any `requires_confirmation`
     fallback (`agentao/runtime/tool_planning.py:391-399`), and rules match on
     tool name with `*` patterns (`agentao/permissions.py:447-455`, `:220`). An
     operator can therefore deny or force-ask `mcp_*` — or one server's prefix —
     and that rule governs page-2 tools exactly as it governs page-1 tools.
     (Precedence note: the read-only mode preset short-circuits to `DENY`
     *ahead* of the engine at `tool_planning.py:381-389`, so a permissions.json
     `allow` cannot re-open a tool in read-only mode. Irrelevant to the risk
     below, which is about the permissive direction, but do not read "the
     engine decides everything" out of this bullet.)
   - **`enabled_tools` is not a boundary.** By design, `apply_enabled_tools`
     "removes every built-in / agent-path tool whose name is absent from the
     allowlist, leaving `extra_tools`, MCP (`mcp_*`), and plan-only tools
     untouched" (`agentao/tooling/registry.py:170-178`).
   - **The `mcp_` prefix is not a boundary either.** It guarantees MCP tools
     cannot *shadow* a built-in (`tool.py:20-22`); it grants no policy of its
     own — it is only useful as something a permission rule can match on.

   **The residual risk, stated exactly:** when **no permission rule matches**,
   the decision falls through to the tool's own `requires_confirmation`, and
   `McpTool` returns `False` for a `trust: true` server unless that server set
   `destructiveHint` (`agentao/mcp/tool.py:111-117`). So on a trusted server
   with no matching rule, tools arriving from page 2 onward can be called
   without a prompt where before this change they were unreachable.

   That is the correct outcome — the operator trusted the server, and a
   truncated catalog was never the intended security posture — but it is worth
   stating rather than waving off. **This design adds no new policy**; an
   operator who wants containment already has `permissions.json`. Whether
   `enabled_tools` *should* reach MCP tools is a separate, pre-existing
   question — see `docs/design/host-tool-allowlist.md`.
3. **The 60s startup budget now covers N round-trips.** A slow paginating
   server could newly hit the timeout that a single call fit inside. This is
   correct behavior, but the timeout message at `:297-301` currently reads
   `"initialize / server-discover / list_tools handshake"` and should say
   pagination is included — the comment above it makes exactly this point about
   not pointing users at a step that already completed.

## 7. Testing

Per `CLAUDE.md` and `project_mcp_sdk_2x_compat`: **build inputs from real
`mcp.types` models.** `SimpleNamespace`/`MagicMock` fakes hid every one of the
four 1.x→2.x breaks behind a green suite, and `MagicMock` is actively harmful
here — it answers `hasattr` for any name, so it would satisfy `field()`'s tail
case on both majors and prove nothing about either.

- **No-regression on the common path** — a single page with a null cursor
  issues exactly **one** call with `params=None`. Pins §6.1.
- **Multi-page collection** — real `ListToolsResult` pages; assert every tool
  from every page is registered, in order.
- **Empty-string cursor** — `nextCursor=""` must produce a *second* request
  carrying `params=PaginatedRequestParams(cursor="")`, not a repeat of page 1.
  Without this test the `if cursor` bug in §5.2 is invisible: a suite that only
  ever uses non-empty cursors passes either way.
- **Repeated cursor** — raises.
- **Oversized cursor** — raises. Build the cursor from **multibyte** characters
  sized to pass a `len()` check but fail a UTF-8 byte check, so the test
  distinguishes the two.
- **Single-page item overflow** — one page carrying more than `_MAX_TOOLS`
  raises. Assert the raise and nothing more: this test **cannot** distinguish
  check-before-`extend` from check-after. `tools` is a local accumulator and
  `self._tools` is only assigned on success (`:290-292`), so "no tools are
  registered" holds either way and asserting it would be a test that passes for
  a reason unrelated to what it claims to pin. The ordering is a code-review
  invariant, not a testable one — do not build machinery to chase it.
- **Page cap** — 100 pages each carrying a cursor raises.
- **Failure isolation** — on any of the above, the server lands in
  `status == ERROR` with `error_message` naming the cap (via `connect()`'s own
  handler at `:310-347`), and a sibling server in the same `connect_all` still
  reaches `CONNECTED`. Assert the status, not a propagated exception —
  `connect()` does not re-raise.
- **Cross-major field read** — the `nextCursor`/`next_cursor` read must be
  exercised on both majors. CI's three cells (`ci.yml:148`) do this for free
  *provided* the test constructs a real `ListToolsResult` rather than asserting
  on a hand-set attribute.

## 8. Out of scope

- **Tool-description bounding.** Codex #35941 caps model-facing MCP descriptions
  at 1,000 bytes; `agentao/mcp/tool.py:69` passes them through unbounded. Real,
  but a separate context-budget concern with a separate decision.
- **Other paginated MCP surfaces.** There are none:
  `grep -rn 'list_resources|list_prompts|list_resource_templates' agentao/`
  returns nothing. Agentao discovers tools only, so `tools/list` is the entire
  paginated surface — codex's parallel work on `resources/list` and
  `resources/templates/list` has no target here.
- **Tool-count / context management.** See the §5.3 caveat.
- **Making the four bounds configurable.** See §9 — the case for it is now on
  record, but no config surface is added here.
- **Transport-level response-size limits.** Codex applies an 8 MiB cap to JSON,
  SSE and stdio messages (#35725). agentao cannot cheaply match it: the MCP SDK
  owns the transport and hands `_list_all_tools` already-parsed models, so
  there is no agentao-side seam between the wire and the object. That is why
  §5.3's item cap is a catalog bound rather than a wire bound. Adding a real
  wire bound means going through the SDK, which is its own piece of work.

## 9. Known regression profiles — recorded, not fixed

A round-3 xhigh review found three server profiles that connected before this
change and now fail outright. All three are the fail-closed rule (§5.4) working
as approved, so none is treated as a defect here — but the rule was approved
before these profiles were enumerated, and they are the concrete cost of it.
Recorded so the trade is re-decidable rather than rediscovered.

| Profile | Outcome | Note |
|---|---|---|
| One uncursored page with **> 1,024 tools** | `ERROR`, 0 tools | Never enters the loop; pure regression. See §6.1. |
| Compliant server paging at **≤ 10 tools/page** with > 100 pages' worth | `ERROR`, 0 tools | 120 tools at 1/page trips `_MAX_TOOL_PAGES` at ~12% of the item cap. §5.3 calls the page cap "belt-and-braces with the item cap", which only holds above 10/page. |
| Server emitting `"nextCursor": ""` as a **zero-value** (no `omitempty`) | `ERROR`, 0 tools | Spec-wise `""` is a cursor, so §5.2 sends it; a server that means "none" by it returns page 1 again and trips the repeated-cursor guard. agentao matches codex's reference behavior here. |

**The obvious mitigation for all three is the same:** make the four bounds
per-server configurable, alongside the existing `timeout: {startup, request}`
in `.agentao/mcp.json`. That is deliberately *not* done here — it is new config
surface and a new compat contract, and no user has hit any of these yet. The
trigger to build it is the first report of one of the rows above.

Two smaller notes from the same review, also unfixed by choice:

- **The 60s `startup` budget now spans up to 100 round-trips**, so a
  high-latency paginating server can fail a connect that previously fit. This
  is §6.3, already stated, and the budget is already user-configurable — the
  fix if it bites is `timeout: {"startup": N}`, not a code change.
- **A transient error on page ≥ 2 aborts the whole connect**, discarding the
  pages already fetched. Adding mid-pagination retry is new design, and MCP
  connect has never retried; out of scope.
