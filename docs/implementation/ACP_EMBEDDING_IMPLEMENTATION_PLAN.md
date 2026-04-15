# ACP Embedding Implementation Plan

Parent design doc: [Promote `ACPManager` as a Stable Embedding Facade for Non-Interactive Runtimes](../kanban-acp-embedded-client-issue.md)

## Goal

Implement a stable embedding-oriented ACP client surface on top of `agentao.acp_client` so workflow runtimes can:

- run one-shot prompts safely in daemon contexts
- detect non-interactive permission/input requests without CLI scraping
- map failures via structured error codes
- rely on a declared semver-stable import surface

## Scope

- non-interactive `send_prompt(..., interactive=False)`
- `prompt_once(...)`
- structured client-side error taxonomy
- per-call `cwd` behavior
- public/internal API boundary for `agentao.acp_client`
- tests and embedding docs

## Non-Goals

- ACP wire-protocol changes
- changes to existing CLI `/acp` user flows beyond compatibility-preserving internals
- workflow-level retry/fallback semantics
- a new top-level facade beyond `ACPManager`

## Fixed V1 Decisions

- `AcpRpcError` remains the exception for server-returned JSON-RPC error responses, but it also carries a client-side `code: AcpErrorCode`.
- `AcpErrorCode` includes `SERVER_BUSY` for named-server concurrency conflicts.
- v1 mapping for `AcpRpcError.code` is:
  - `PROTOCOL_ERROR` for server-returned JSON-RPC error payloads
  - `TRANSPORT_DISCONNECT` only when the response path fails because the connection disappears before a valid JSON-RPC error/result arrives
- `AcpRpcError` continues to expose the raw JSON-RPC numeric code separately as `rpc_code`, preserves `rpc_message` / `data`, and defaults `code=AcpErrorCode.PROTOCOL_ERROR`.
- manager turn tracking is a single active slot per named server: `server_name -> _TurnContext`.
- v1 concurrency contract is strict serialization per named server:
  - only one active turn may exist per named server
  - `send_prompt`, `prompt_once`, and `cancel_turn` are coordinated by a per-server lock
  - `send_prompt` blocks while acquiring the per-server lock
  - `prompt_once` does not wait for the lock; it fails fast with `AcpClientError(code=SERVER_BUSY)`
  - `prompt_once(stop_process=True)` must never stop a process that is serving another active turn
- session reuse compares both `cwd` and `mcp_servers`; a mismatch in either requires a fresh session.
- `cancel_turn` wins over a latched non-interactive interaction error. If both happen during one turn, the terminal outcome exposed to the caller is cancellation/timeout rather than `INTERACTION_REQUIRED`.
- ephemeral one-shot connections created by `prompt_once` must not be registered in `self._clients` and must not appear as durable entries in `get_status()`.
- all raised embedding-facing `AcpClientError.details` payloads may include a stderr snapshot for diagnostics when available.
- `AcpInteractionRequiredError` may include the raw server request method only in `details["method"]`; embedding callers must not branch on a public `method` attribute.

## Workstreams

### Workstream 1: Error Model and Public Types

Files:

- `agentao/acp_client/client.py`
- `agentao/acp_client/models.py`
- `agentao/acp_client/__init__.py`

Deliverables:

- `AcpErrorCode`
- richer `AcpClientError`
- `AcpInteractionRequiredError`
- `PromptResult`
- `AcpConnectionInfo.session_cwd`

Implementation notes:

- keep `AcpRpcError` for raw server-returned JSON-RPC failures
- add client-side error metadata without breaking `except AcpClientError`
- avoid class explosion; prefer a small number of structured fields
- expose raw server JSON-RPC error code as `rpc_code` rather than overloading `AcpErrorCode`
- default `AcpRpcError.code` to `PROTOCOL_ERROR` so most call sites do not need to pass it explicitly

Acceptance:

- all client-originated failure paths can be matched via `e.code`
- existing code that catches `AcpClientError` still works
- `AcpRpcError` shape is finalized in Phase 1 rather than deferred
- lock-conflict failures from `prompt_once` map to `SERVER_BUSY`

### Workstream 2: Non-Interactive Turn Control

Files:

- `agentao/acp_client/manager.py`
- `agentao/acp_client/client.py`
- `agentao/acp_client/interaction.py`

Deliverables:

- manager-owned per-turn context for non-interactive calls
- auto-reject handling for `session/request_permission`
- auto-reject handling for `_agentao.cn/ask_user`
- interaction-required exception raised only after prompt reaches terminal state

Implementation notes:

- build on `send_prompt_nonblocking()` plus `finish_prompt()`
- keep prompt lifecycle ownership in the prompt path, not in interaction helpers
- do not expose durable `WAITING_FOR_USER` for non-interactive calls
- use a single active `_TurnContext` slot per named server rather than attempting to key interaction requests by prompt request id
- add one manager-owned `threading.Lock` per named server to serialize turn-bearing operations
- lock acquisition wraps the synchronous manager entrypoint, not any async coroutine or MCP event-loop work

Acceptance:

- non-interactive calls never stall indefinitely on user interaction
- non-interactive rejection does not report `READY` before prompt completion
- concurrent `send_prompt` calls serialize behind the per-server lock
- concurrent `prompt_once` calls fail fast with `SERVER_BUSY`

### Workstream 3: `prompt_once` and Per-Call `cwd`

Files:

- `agentao/acp_client/manager.py`
- `agentao/acp_client/client.py`

Deliverables:

- `ACPManager.prompt_once()`
- deterministic cleanup in `finally`
- fresh-session behavior when `cwd` or `mcp_servers` differ from connected session metadata

Implementation notes:

- `prompt_once` should use a temporary connection path instead of mutating the long-lived cache
- if a cached client exists for the same server but a different `cwd` or `mcp_servers` is requested, do not silently reuse it
- v1 default: `prompt_once(..., stop_process=True)`
- timeout cleanup is part of the required `finally` contract, not an optional follow-up
- before implementing `mcp_servers` fingerprinting, confirm that the effective reuse boundary is still `session/new` rather than `session/prompt`

Acceptance:

- one-shot prompts stop client/process on both success and exception
- `send_prompt(..., cwd=<different>)` uses a fresh session
- `send_prompt(..., mcp_servers=<different>)` uses a fresh session

### Workstream 4: Stable Embedding Surface and Docs

Files:

- `agentao/acp_client/__init__.py`
- `docs/features/acp-embedding.md` (new)
- ACP client docs as needed

Deliverables:

- narrowed public exports
- explicit embedding docs
- import guidance for internal vs. supported symbols

Implementation notes:

- keep `agentao.acp_client` as the only stable embedding root
- update internal imports that currently rely on broad top-level re-exports
- if any compatibility aliases are kept, mark them deprecated in comments/docs
- migration notes must enumerate the symbols being removed from the public package root

Acceptance:

- embedding example imports only from `agentao.acp_client`
- internal helpers are no longer presented as supported API

## Phase Plan

### Phase 1: Types and Error Foundations

Steps:

1. Add `AcpErrorCode`, enhanced `AcpClientError`, `AcpInteractionRequiredError`.
2. Add `PromptResult` and `session_cwd`.
3. Update exports and import sites as needed.
4. Add unit tests for error construction and compatibility.

Exit criteria:

- core types compile
- no behavior change yet for CLI prompt flow
- `PromptResult` may ship before `prompt_once()` consumes it; this is acceptable because it freezes the public type shape early

### Phase 2: Non-Interactive Prompt Path

Steps:

1. Add manager-side turn context for non-interactive prompts.
2. Route server-initiated requests through policy-aware handling.
3. Auto-reject in non-interactive mode.
4. Remove incorrect `reject_interaction() -> READY` shortcut semantics.
5. Add tests for permission/input auto-rejection and state visibility.

Exit criteria:

- `send_prompt(..., interactive=False)` works end-to-end
- CLI interactive path remains green

### Phase 3: One-Shot API and `cwd` Semantics

Steps:

1. Implement `prompt_once()`.
2. Store session `cwd` on connect/session creation.
3. Store comparable session metadata for `mcp_servers`.
4. Make `ensure_connected()` reuse conditional on matching `cwd` and `mcp_servers`.
5. Add cleanup tests and `cwd` / `mcp_servers` reuse-vs-fresh-session tests.

Exit criteria:

- one-shot daemon usage is deterministic
- per-call `cwd` and `mcp_servers` invariants are enforced

### Phase 4: Docs and Public Surface Finalization

Steps:

1. Add `docs/features/acp-embedding.md`.
2. Tighten `agentao.acp_client` exports.
3. Update any affected docs/tests/imports.
4. Add a short migration note if internal top-level re-exports are removed.

Exit criteria:

- embedding API is documented and enforced

## Detailed Task Breakdown

### Task A: Add Error Types

Code changes:

- define `AcpErrorCode`
- update `AcpClientError.__init__`
- update all explicit `raise AcpClientError(...)` sites to pass a code
- add finalized `AcpRpcError(code, rpc_code, rpc_message, data, details=...)`
- reserve `details["stderr_tail"]` for diagnostic snapshots when available
- add `SERVER_BUSY` to support fail-fast `prompt_once` lock conflicts

Tests:

- timeout maps to `REQUEST_TIMEOUT`
- broken stdin/write maps to `TRANSPORT_DISCONNECT`
- invalid server name maps to `SERVER_NOT_FOUND`
- server JSON-RPC error maps to `AcpRpcError(code=PROTOCOL_ERROR, rpc_code=<raw>)`
- `prompt_once` lock conflict maps to `SERVER_BUSY`

### Task B: Add Turn Context

Code changes:

- add manager-side single active-turn registry keyed by `server_name`
- record whether current turn is interactive
- latch first interaction-required event onto the turn context
- keep a cancel/timeout terminal flag on the turn context so cancellation can override a previously latched interaction error
- acquire/release per-server locks only in the synchronous manager boundary; never hold them across async MCP loop internals

Tests:

- one prompt cannot accidentally consume another prompt's interaction event
- repeated server requests during one prompt still produce one latched interaction error for the caller
- cancel after auto-reject returns cancellation rather than `INTERACTION_REQUIRED`

### Task C: Fix State Transitions

Code changes:

- move final `READY`/`FAILED` ownership to prompt-completion path
- keep interaction helpers focused on response delivery
- ensure `get_status()` does not expose non-interactive turns as durably `WAITING_FOR_USER`
- preserve interactive `WAITING_FOR_USER` behavior for CLI paths
- update tests that currently assert `reject_interaction()` directly transitions to `READY`, especially in `tests/test_acp_client_cli.py`

Tests:

- interactive path still shows `WAITING_FOR_USER`
- non-interactive path stays `BUSY` until terminal result
- `get_status()` can observe `BUSY` after auto-reject and before prompt completion using a controllable mock server

### Task D: Implement `prompt_once`

Code changes:

- add temporary connect/send/cleanup path
- decide whether helper reuses lower-level `connect_server()` pieces or a private `_connect_ephemeral()` helper
- guarantee cleanup in `finally`
- ensure ephemeral clients are not inserted into `self._clients`
- on timeout, cancel the active turn, wait for bounded cleanup, then stop process/client per contract

Tests:

- success cleanup
- RPC error cleanup
- interaction-required cleanup
- transport failure cleanup
- timeout cleanup
- ephemeral calls do not appear as durable entries in `get_status()`

### Task E: Enforce `cwd` Invariant

Code changes:

- record `session_cwd` during `create_session()`
- record normalized session `mcp_servers` fingerprint during `create_session()`
- teach `ensure_connected()` and `send_prompt()` how to decide between reuse and fresh connect
- verify first that `mcp_servers` is truly session-scoped in the current ACP client path; if that assumption changes, move the fingerprint to prompt scope before implementation

Tests:

- same `cwd` reuses cached session
- different `cwd` does not reuse cached session
- different `mcp_servers` does not reuse cached session

### Task F: Finalize Public Surface

Code changes:

- narrow `__all__`
- update internal imports
- add docs
- enumerate symbols removed from public root:
  - `ACPClient`
  - `ACPProcessHandle`
  - `Inbox`, `InboxMessage`, `MessageKind`
  - `InteractionKind`, `InteractionRegistry`, `PendingInteraction`
  - `AcpConnectionInfo`
  - `AcpExplicitRoute`, `detect_explicit_route`

Tests:

- import smoke test for supported API
- internal modules still import directly where needed

## Test Strategy

Primary test files:

- `tests/test_acp_client_prompt.py`
- `tests/test_acp_client_cli.py`
- `tests/test_acp_client_jsonrpc.py`
- `tests/test_imports.py`

Add a new dedicated file if needed:

- `tests/test_acp_client_embedding.py`

Recommended order:

1. add error-model unit tests
2. add non-interactive prompt tests with a mock ACP server that emits permission/input requests
   - the mock should pause prompt completion after the auto-reject so tests can assert `get_status()` still reports `BUSY`
3. add `prompt_once` cleanup tests
4. add `cwd` / `mcp_servers` reuse/fresh-session tests
5. run existing ACP client and CLI tests for regression coverage

## Risks

### State Regression in CLI

Current interactive tests assume `WAITING_FOR_USER` transitions driven by interaction helpers. Refactoring may break them if prompt ownership is not clearly preserved.

Mitigation:

- change tests and code together
- keep interactive behavior unchanged from user perspective

### Public Export Breakage

Tightening `agentao.acp_client.__all__` can break internal imports or tests that relied on broad re-exports.

Mitigation:

- update import sites in the same PR
- keep temporary aliases only if strictly necessary

### Handle Reuse Confusion

Mixing cached long-lived clients and temporary `prompt_once` flows against one named server can create surprising process/session reuse behavior.

Mitigation:

- prefer isolated temporary clients for `prompt_once`
- document concurrency expectations
- add a per-server lock as part of v1, not as a follow-up

### Timeout and Late Notification Races

One-shot or non-interactive calls may timeout while the server is still capable of emitting late notifications or a late prompt result.

Mitigation:

- treat timeout as a terminal turn outcome with explicit cleanup steps
- mark the turn context completed before releasing the per-server lock
- ignore or safely drain late interaction notifications that arrive after the turn has already been finalized

## Rollout Recommendation

Implement in two PRs if possible:

1. foundation PR
   - public types (`AcpErrorCode`, `AcpInteractionRequiredError`, `PromptResult`)
   - error types
   - turn context
   - non-interactive prompt path
   - state fixups
   - per-server locking
2. embedding PR
   - `prompt_once`
   - per-call `cwd`
   - per-call `mcp_servers`
   - public export tightening
   - docs

This keeps the first PR focused on correctness and the second on API shaping and cleanup.

## Done Definition

The work is done when:

- `ACPManager.send_prompt(..., interactive=False)` behaves deterministically
- `ACPManager.prompt_once(...)` exists and cleans up deterministically
- all client-side failures expose `AcpErrorCode`
- per-call `cwd` and `mcp_servers` are honored
- `agentao.acp_client` exposes a documented stable embedding surface
- ACP client tests and relevant CLI regression tests pass
