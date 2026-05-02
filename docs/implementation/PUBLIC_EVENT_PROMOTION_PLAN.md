# Public Event Promotion Plan — `MCPLifecycleEvent` and `LLMCallEvent`

**Date:** 2026-05-01
**Status:** Draft implementation plan; design rationale embedded.
**Source design:** `docs/design/embedded-host-contract.md` (deferred items #3 and the LLM-call promotion rationale below)
**Scope:** Promote two new event families to the stable `agentao.host` contract, plus the schema/test/projection plumbing that comes with each.

---

## TL;DR

The host contract today ships three Pydantic event families
(`ToolLifecycleEvent`, `SubagentLifecycleEvent`,
`PermissionDecisionEvent`). Two more families have a clear demand
profile and existing internal wiring; this plan promotes them to the
stable surface in two independent PRs:

1. **`MCPLifecycleEvent`** — server connection state transitions.
   Today's coverage is *zero* on either channel; hosts cannot detect
   an MCP outage without polling internal state.
2. **`LLMCallEvent`** — start/completed/failed for one LLM request,
   carrying `usage`, `model`, `finish_reason`, `duration_ms`, and a
   structured `error_type` (covers rate-limit, retry-exhausted, etc).
   Internal `LLM_CALL_*` events already exist in
   `agentao/transport/events.py`; this PR adds a public projection
   plus schema snapshot.

Both PRs follow the existing pattern from PR-5/PR-6/PR-7 of the
embedded-harness epic: model → emit-site → projection → schema
snapshot → tests → docs.

Out of scope: hook lifecycle (waits on hook key contract), memory
crystallization (runtime-internal, not a host concern; user-driven
writes already covered by `ToolLifecycleEvent` on `save_memory`),
context compression as a public event (also runtime-internal).

---

## Why these two, why now

### Why `MCPLifecycleEvent`

- **Real-world failure mode.** When an MCP server drops mid-session,
  every tool call routed to it starts failing. The host learns this
  indirectly through `ToolLifecycleEvent(phase="failed",
  error_type="...")` — the tool layer cannot distinguish "tool
  errored" from "MCP transport disappeared." Operationally these
  warrant different responses (retry tool vs. reconnect server vs.
  alert on-call).
- **No internal channel either.** Unlike LLM events, MCP transitions
  do *not* fire as `AgentEvent`. The status only lives on
  `McpClient.status` and is reachable via `McpClientManager.get_status()`,
  which forces hosts into polling — exactly the anti-pattern the
  event-first contract was supposed to eliminate.
- **Bounded payload.** Connection state is a small finite set; the
  Pydantic model is straightforward and unlikely to evolve.

### Why `LLMCallEvent`

- **Cost / billing / compliance.** Enterprise hosts need stable,
  schema-snapshotted access to per-call token usage, model identity,
  finish reason, and duration. Today they either dip into the
  unstable `AgentEvent` `LLM_CALL_COMPLETED` payload or scrape logs.
- **Rate-limit visibility.** Provider 429s currently surface only
  through `ERROR` text. Routing them through
  `LLMCallEvent(phase="failed", error_type="rate_limited")` makes
  them queryable and aggregatable.
- **Cheap to do.** The internal emit sites already exist
  (`agentao/runtime/llm_call.py:80, 122, 148`) with all the right
  fields. The work is a projection layer + redaction + Pydantic
  model, not new instrumentation.

---

## Out of scope (and why)

- **Hook lifecycle (`HookExecutionEvent`).** Blocked on the hook key
  contract (`docs/design/embedded-host-contract.md` deferred item #1).
  Until hook keys are stable a public `hook_key` field would have to
  ship without a stability promise. Internal `PLUGIN_HOOK_FIRED`
  already exists for non-stable consumers.
- **Memory crystallization / context compression as public events.**
  Crystallization is a runtime-internal optimization with no host
  policy hook; user-driven memory writes already fire
  `ToolLifecycleEvent` on the `save_memory` tool. Leaving this
  internal preserves the freedom to overhaul the memory subsystem
  without a public schema bump.
- **`THINKING` / `LLM_TEXT` / `LLM_CALL_DELTA` / `LLM_CALL_IO`
  promotion.** These carry raw model output and prompt history;
  promoting them to the *stable* contract conflicts with the
  existing redaction discipline in `agentao/host/projection.py`.
  Streaming chat UI is the documented use case for the internal
  `Transport` channel; this plan does not change that.

---

## Pillar 1 — `MCPLifecycleEvent`

### Public model

```python
# agentao/host/models.py

class MCPLifecycleEvent(BaseModel):
    """Public lifecycle fact for one MCP server connection."""

    model_config = ConfigDict(extra="forbid")

    event_type: Literal["mcp_lifecycle"] = "mcp_lifecycle"
    schema_version: Literal[1] = 1
    timestamp: RFC3339UTCString
    session_id: str

    server_name: str               # logical name from .agentao/mcp.json
    transport_type: Literal["stdio", "sse", "unknown"]
    phase: Literal[
        "connecting",
        "connected",
        "auth_failed",
        "tool_discovery_failed",
        "disconnected",
    ]
    error_type: Optional[str] = None
    summary: Optional[str] = None  # redacted, short, host-facing
```

### Emit sites

`agentao/mcp/client.py::McpClient.connect()` already mutates
`self.status` at four boundaries. Wire emission at each:

| Existing transition | New emit |
|---|---|
| `DISCONNECTED → CONNECTING` (top of `connect`) | `phase="connecting"` |
| Successful `await session.initialize()` + `list_tools()` | `phase="connected"` |
| `mcp.shared.exceptions.McpError` with auth-related `code` | `phase="auth_failed"` (`error_type="mcp_auth"`) |
| `list_tools` raises | `phase="tool_discovery_failed"` (`error_type=type(exc).__name__`) |
| Exit stack teardown (`disconnect()`) | `phase="disconnected"` |

The emit happens at the `McpClientManager` level (which already has
the `EventStream` reference per the harness wiring), not from
`McpClient` directly — keeps `McpClient` ignorant of the public
contract.

### Projection / redaction

No raw payload. `summary` is the same redacted-string discipline as
`ToolLifecycleEvent.summary`: max 200 chars, no auth headers, no
endpoint URLs (URLs may contain tokens for SSE servers).

### Wiring

- Extend the discriminated union in `agentao/host/models.py::HostEvent`
  to include `MCPLifecycleEvent`.
- Extend `host_event_to_replay_kind` / `host_event_to_replay_payload`
  in `agentao/host/replay_projection.py` and the reverse projection
  for round-trip.
- Bump `host.events.v1.json` snapshot.
- Add `mcp_lifecycle` to `agentao/replay/events.py::EventKind` v1.3
  (or whatever the next replay schema version is); update replay
  schema policy doc.

### Tests

- `tests/test_host_mcp_lifecycle_events.py`
  - happy path: `connecting` → `connected` for stdio + sse
  - auth failure: `connecting` → `auth_failed`
  - tool discovery failure: `connecting` → `tool_discovery_failed`
  - clean teardown: `connected` → `disconnected`
  - ordering: `mcp_lifecycle(connected)` precedes the first
    `ToolLifecycleEvent` for any tool routed through that server
- `tests/test_host_schema.py` — snapshot delta
- `tests/test_host_typing.py` — extend the downstream-shaped consumer

---

## Pillar 2 — `LLMCallEvent`

### Public model

```python
# agentao/host/models.py

class LLMCallEvent(BaseModel):
    """Public lifecycle fact for one LLM request."""

    model_config = ConfigDict(extra="forbid")

    event_type: Literal["llm_call"] = "llm_call"
    schema_version: Literal[1] = 1
    timestamp: RFC3339UTCString
    session_id: str
    turn_id: str
    llm_call_id: str               # UUID4 minted at call entry

    phase: Literal["started", "completed", "failed"]
    attempt: int                   # 1-indexed; bumps on retry
    model: str
    duration_ms: Optional[int] = None     # populated on completed/failed

    # completed only
    finish_reason: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None

    # failed only
    error_type: Optional[
        Literal["rate_limited", "timeout", "auth", "server_error", "unknown"]
    ] = None
    summary: Optional[str] = None  # redacted host-facing string
```

### Identity

Add `llm_call_id` to `agentao/runtime/identity.py`. UUID4 minted at
`agentao/runtime/llm_call.py::_llm_call` entry, kept in scope for
the matched `started` / `completed` / `failed` triple. Uniqueness
scope: `(session_id, turn_id, llm_call_id)`.

### Emit sites

Existing emit sites in `agentao/runtime/llm_call.py:80, 122, 148`
already gather every needed field. The promotion adds a parallel
emit on the `EventStream` at the same three points:

- Line 80 (`LLM_CALL_STARTED`) → `LLMCallEvent(phase="started", ...)`
- Line 122 (exception path) → `LLMCallEvent(phase="failed", ...)`,
  with `error_type` derived from exception class:
  - `openai.RateLimitError` → `"rate_limited"`
  - `openai.APITimeoutError` / `asyncio.TimeoutError` → `"timeout"`
  - `openai.AuthenticationError` → `"auth"`
  - `openai.APIStatusError` (5xx) → `"server_error"`
  - everything else → `"unknown"`
- Line 148 (success path) → `LLMCallEvent(phase="completed", ...)`

Internal `LLM_CALL_DELTA` / `LLM_CALL_IO` / `LLM_TEXT` / `THINKING`
remain on the internal channel only.

### Projection / redaction

`summary` is bounded: max 200 chars, no model-output text, no
prompt fragments. For `error_type="auth"` strip API key fragments
defensively even though the client should never put them in
exception messages.

### Wiring

Same as MCP pillar: extend `HostEvent` union, extend replay
projection, bump snapshot, extend replay `EventKind`.

### Tests

- `tests/test_host_llm_events.py`
  - happy path: `started` → `completed` with token counts
  - rate-limit: `started` → `failed(error_type="rate_limited")`
  - timeout, auth, server-error coverage
  - ordering: `LLMCallEvent(started)` precedes the
    `ToolLifecycleEvent(started)` triggered by any tool call in the
    response (within the same turn)
  - retry path: `attempt=1 failed` → `attempt=2 completed`, both
    sharing the same `turn_id` but different `llm_call_id`
- `tests/test_host_schema.py` snapshot delta
- `tests/test_host_typing.py` extension

---

## Staging

| PR | Scope | Tests | Docs |
|---|---|---|---|
| PR-A | `MCPLifecycleEvent` | `test_host_mcp_lifecycle_events.py` + schema/typing extensions | `host.md` table row, `embedded-host-contract.md` deferred-list update |
| PR-B | `LLMCallEvent` + `llm_call_id` identity | `test_host_llm_events.py` + schema/typing extensions | same files |

PRs are independent; either can ship first. PR-A is smaller and
addresses the more painful operational gap; recommend A → B.

---

## Backwards compatibility

- Both events extend the `HostEvent` discriminated union. Existing
  consumers using `match event.event_type:` will hit the catch-all
  branch and ignore the new types — additive change, no breakage.
- `host.events.v1.json` schema delta is additive (new
  `oneOf` branches). Per the schema policy this is a v1
  backwards-compatible change, not a v2 cut.
- `agent.events()` iterator surface unchanged.
- No change to `Agentao.arun()` / `active_permissions()` / capability
  injection.

---

## Non-goals (explicit)

- No `MCPToolDiscoveryEvent` per discovered tool — would 100x the
  event volume on busy registries with no clear consumer.
- No `LLMRequestEvent` carrying full prompts. The internal
  `LLM_CALL_IO` channel exists for that and stays internal.
- No promotion of `MEMORY_*` events. Tracked separately if a
  concrete host need emerges.
- No public hook lifecycle until the hook key contract stabilizes.

---

## Open questions

1. **`session_id` vs `agent_id`.** MCP servers can be configured at
   the project (`<cwd>/.agentao/mcp.json`) or user (`~/.agentao/mcp.json`)
   level. A "user-level server connecting" event has no natural
   `session_id`. Two options:
   - Tag with the *first* session that needs the server.
   - Make `session_id` Optional on `MCPLifecycleEvent` only.
   Lean toward option 2 — connection lifecycle is genuinely process-
   scoped, not session-scoped. Decision needed before PR-A merges.
2. **Retry attempt semantics for `LLMCallEvent`.** Does the host
   want one event per attempt (current proposal) or a final
   "succeeded after N retries" rollup? Current proposal keeps the
   shape uniform with internal events, which simplifies projection.
3. **Should `ServerStatus.ERROR` (transient error during operation
   without disconnect) get its own phase?** Today `McpClient.status`
   has `ERROR` distinct from `DISCONNECTED`. Argument for: hosts may
   want to alert on a server stuck in `ERROR`. Argument against: a
   tool-call failure already fires `ToolLifecycleEvent` and the
   distinction is mostly internal. Lean toward: omit from MVP, add
   if a host asks.

---

## Acceptance

This plan is accepted when:

- All three open questions above are resolved (in the PRs or
  pinned in this doc).
- PR-A merges with green CI.
- PR-B merges with green CI.
- `docs/api/host.md` and `docs/api/host.zh.md` move
  `MCPLifecycleEvent` and `LLMCallEvent` from "Known gaps" in the
  `Transport` channel section to first-class entries in Public
  exports.
- `docs/design/embedded-host-contract.md` deferred-list items are
  updated to "Shipped in 0.x.y".
