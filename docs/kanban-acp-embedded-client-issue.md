# Promote `ACPManager` as a Stable Embedding Facade for Non-Interactive Runtimes

Implementation plan: [ACP Embedding Implementation Plan](implementation/ACP_EMBEDDING_IMPLEMENTATION_PLAN.md)

## Summary

Kanban (and similar workflow runtimes) want to reuse `agentao`'s ACP client as a backend for roles such as:

- `worker → claude-code-worker`
- `reviewer → codex-reviewer`

Most of the plumbing already exists in `agentao/acp_client/`. The remaining work is **not** a new facade — it is (1) non-interactive semantics, (2) a structured error taxonomy, (3) an ephemeral `prompt_once` helper, (4) per-call `cwd`, and (5) an explicit public/internal API boundary on top of `ACPManager`.

## Current State (what already works)

From `agentao/acp_client/manager.py` and `agentao/acp_client/client.py`:

| Need                                     | Existing API                                                 |
| ---------------------------------------- | ------------------------------------------------------------ |
| Load project-local config                | `ACPManager.from_project(project_root)` → `load_acp_client_config` |
| Enumerate / access server configs        | `server_names`, `config`, `get_handle(name)`                 |
| Start / stop / restart a server          | `start_server`, `stop_server`, `restart_server`, `start_all`, `stop_all` |
| Full start + handshake + session         | `connect_server`, `ensure_connected`                         |
| Send a prompt (auto-connect)             | `send_prompt(name, text, timeout=...)`                       |
| Cancel an active turn                    | `cancel_turn(name)`                                          |
| Structured status snapshot               | `get_status()` → list of dicts with `state`, `pid`, `last_error`, `last_activity`, `inbox_pending`, `interactions_pending`, `stderr_lines` |
| Stderr tail                              | `get_server_logs(name, n)`                                   |
| Lifecycle state enum                     | `ServerState` (`configured`, `starting`, `initializing`, `ready`, `busy`, `waiting_for_user`, `stopping`, `stopped`, `failed`) |
| Base exception types                     | `AcpClientError`, `AcpRpcError(code, message, data)`         |

Kanban can already call `ACPManager.from_project(...).send_prompt("claude-code-worker", prompt)` and read `get_status()` without scraping CLI output. The issue is not "add a facade" — it is **close the five remaining gaps and freeze the contract**.

## Remaining Gaps

### Gap 1 — Non-interactive mode (highest priority)

Today, when an ACP server issues `session/request_permission` or `_agentao.cn/ask_user`, `ACPManager._route_server_request` registers a `PendingInteraction` and transitions the server to `WAITING_FOR_USER`. Nothing resolves the interaction unless a CLI (or caller) explicitly invokes `approve_interaction` / `reject_interaction` / `reply_interaction`. A workflow daemon has no user to ask and will stall indefinitely.

**Proposal:** add a non-interactive policy to `send_prompt` / `prompt_once`:

```python
mgr.send_prompt(name, text, interactive=False)  # default True for CLI parity
```

When `interactive=False` and the server raises a permission/input request during the turn:

- default to auto-responding with a rejection (`reject_once`); optional future policy may abort the turn instead
- raise `AcpInteractionRequiredError` (new subclass) carrying the method, prompt text, and any option list
- never leave the server stuck in `WAITING_FOR_USER`
- preserve turn-state correctness: after auto-rejecting the interaction, the server remains `BUSY` until the outstanding `session/prompt` completes or fails; only then may it transition to `READY` or `FAILED`

State machine requirement for non-interactive turns:

- `READY` → `BUSY` when `session/prompt` is sent
- if the server asks for permission/input, auto-respond immediately and **do not** expose a durable `WAITING_FOR_USER` state to the caller
- the manager then waits for the original prompt RPC to finish
- final state is `READY` on a cleanly handled turn or `FAILED` on transport/protocol failure

This avoids the current ambiguity where a rejected interaction could make the server look idle before the prompt RPC has actually finished.

### Gap 2 — Structured error taxonomy

Today there are only two exception classes. The categories kanban needs to map (config / server-not-found / start-fail / handshake-fail / timeout / disconnect / interaction-required / protocol) are not distinguishable without string matching on `AcpClientError.args[0]` or on `AcpRpcError.code`.

**Proposal:** introduce an `AcpErrorCode` enum and attach it to `AcpClientError`:

```python
class AcpErrorCode(str, Enum):
    CONFIG_INVALID      = "config_invalid"
    SERVER_NOT_FOUND    = "server_not_found"
    PROCESS_START_FAIL  = "process_start_fail"
    HANDSHAKE_FAIL      = "handshake_fail"
    REQUEST_TIMEOUT     = "request_timeout"
    TRANSPORT_DISCONNECT = "transport_disconnect"
    INTERACTION_REQUIRED = "interaction_required"
    PROTOCOL_ERROR      = "protocol_error"

class AcpClientError(Exception):
    code: AcpErrorCode
    # existing behavior preserved
```

Prefer an `Enum` field over a class hierarchy so existing `except AcpClientError` handlers keep working. `AcpRpcError` continues to carry the raw JSON-RPC `code`; the new `AcpErrorCode` covers client-side categories.

### Gap 3 — Ephemeral `prompt_once` helper

`send_prompt` today keeps the session and process alive after the call — correct for CLI reuse, wrong for a one-shot workflow task that wants deterministic cleanup.

**Proposal:** add `prompt_once`:

```python
def prompt_once(
    self,
    name: str,
    prompt: str,
    *,
    cwd: Optional[str] = None,           # per-call override (see Gap 4)
    timeout_ms: Optional[int] = None,
    interactive: bool = False,           # default False for embedding
    ephemeral: bool = True,              # tear down resources on return/exception
    mcp_servers: Optional[List[dict]] = None,
) -> PromptResult: ...
```

Guarantees:

- connects, runs one turn, returns the result
- on both success and exception, performs deterministic cleanup according to an explicit policy
- never re-enters `WAITING_FOR_USER` when `interactive=False`

Cleanup semantics must be explicit rather than inferred from `auto_start`:

- `ephemeral=True` means the resources created for this call are cleaned up before returning to the caller
- if `prompt_once` created a fresh client/session, it closes that session/client on both success and exception
- whether it also stops the subprocess should be a documented behavior or a separate option; it should **not** depend on `auto_start`, which is a per-server startup preference rather than a cleanup contract

### Gap 4 — Per-call `cwd`

`ensure_connected` captures `cwd` only on first connect, so subsequent `send_prompt` calls for different workflow tasks share the initial working directory. A workflow runner with N tasks against one long-lived server needs per-call `cwd`.

**Proposal:** allow `send_prompt(..., cwd=...)` to either (a) create a fresh session for that call or (b) forward `cwd` on `session/prompt` if the protocol supports it. (a) is simpler and composes with `prompt_once(ephemeral=True)`.

Required invariant:

- a call that supplies `cwd` must not silently reuse an existing session bound to a different working directory
- if the implementation chooses session reuse, it must prove that the effective `cwd` for the turn is the requested one
- otherwise it must create a fresh session for that call

### Gap 5 — Declare the stable embedding surface

`agentao/acp_client/__init__.py` re-exports 20+ names with no indication of which are embedding contract vs. internal. A kanban integration that imports `Inbox`, `InteractionRegistry`, or `ACPProcessHandle` directly will break on every refactor.

**Proposal:** define a real public boundary instead of only adding advisory lists:

```python
# Public embedding API — semver-stable
__all__ = [
    "ACPManager",
    "AcpClientConfig", "AcpServerConfig",
    "AcpConfigError", "AcpClientError", "AcpRpcError", "AcpErrorCode",
    "ServerState", "AcpProcessInfo",
    "PromptResult",
    "load_acp_client_config",
]
```

And move/refactor internal helpers behind explicit internal modules, for example:

- keep `agentao.acp_client` limited to the stable embedding surface
- treat `agentao.acp_client.client`, `.process`, `.interaction`, `.inbox`, and similar modules as internal implementation details
- if compatibility pressure requires temporary re-exports, mark them deprecated and time-box their removal

Document this boundary in `docs/features/acp-embedding.md` (new) with a minimal kanban-shaped example and a short "imports you may rely on" section.

## Non-Goals

- Workflow concepts: `role`, `profile`, retry policy, fallback policy — those belong to kanban.
- Changes to the ACP wire protocol.
- Replacing or reshaping the CLI `/acp` flows.
- A new top-level facade class; `ACPManager` is the facade.

## V1 Decisions

To keep implementation bounded, v1 should make the following explicit choices:

- Non-interactive policy defaults to **auto-reject**, not auto-abort.
- `AcpInteractionRequiredError` is a subclass of `AcpClientError` with `code == AcpErrorCode.INTERACTION_REQUIRED`.
- Error metadata may include the raw method name (`session/request_permission` or `_agentao.cn/ask_user`) for diagnostics, but callers should branch on `code`, not method.
- `prompt_once(ephemeral=True)` closes the client/session it created and also stops the subprocess before returning. This gives daemon callers deterministic cleanup.
- `send_prompt(..., cwd=...)` uses a fresh connection/session whenever the requested `cwd` differs from the connected session's `cwd`.
- `agentao.acp_client` becomes the only semver-stable import surface for embedding. Direct imports from `.client`, `.process`, `.interaction`, `.inbox`, and similar modules remain supported only for internal/CLI code.

## Detailed Design

### 1. Error Model

Add structured client-side error codes in `agentao/acp_client/client.py` or a dedicated `errors.py` module:

```python
class AcpErrorCode(str, Enum):
    CONFIG_INVALID = "config_invalid"
    SERVER_NOT_FOUND = "server_not_found"
    PROCESS_START_FAIL = "process_start_fail"
    HANDSHAKE_FAIL = "handshake_fail"
    REQUEST_TIMEOUT = "request_timeout"
    TRANSPORT_DISCONNECT = "transport_disconnect"
    INTERACTION_REQUIRED = "interaction_required"
    PROTOCOL_ERROR = "protocol_error"


class AcpClientError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: AcpErrorCode,
        details: Optional[dict] = None,
        cause: Optional[BaseException] = None,
    ) -> None: ...


class AcpInteractionRequiredError(AcpClientError):
    def __init__(
        self,
        *,
        server: str,
        method: str,
        prompt: str,
        options: Optional[list[dict]] = None,
        details: Optional[dict] = None,
    ) -> None: ...
```

Expected mapping:

- config parsing errors → `CONFIG_INVALID`
- unknown server name → `SERVER_NOT_FOUND`
- `ACPProcessHandle.start()` failure → `PROCESS_START_FAIL`
- `initialize` / `session/new` failure → `HANDSHAKE_FAIL`
- prompt or RPC timeout → `REQUEST_TIMEOUT`
- broken pipe / client close / process death during I/O → `TRANSPORT_DISCONNECT`
- auto-rejected permission/input request in non-interactive mode → `INTERACTION_REQUIRED`
- malformed server payload or impossible state transition → `PROTOCOL_ERROR`

`AcpRpcError` should remain distinct for raw JSON-RPC error responses from the server, but it should also expose a client-side `code` field or adjacent field that lets embedding callers distinguish transport vs. server-originated failures without string parsing.

### 2. Turn Context and Interaction Policy

The current manager tracks pending interactions globally, but it does not track **which prompt call owns the interaction policy**. That needs a small per-turn context object:

```python
@dataclass
class _TurnContext:
    request_id: int
    server: str
    interactive: bool
    interaction_error: Optional[AcpInteractionRequiredError] = None
    auto_replied_request_ids: set[Any] = field(default_factory=set)
```

Manager behavior:

- `send_prompt(..., interactive=True)` preserves current CLI behavior.
- `send_prompt(..., interactive=False)` sends the prompt non-blocking, associates a `_TurnContext`, and polls until the underlying prompt slot resolves.
- if `_route_server_request()` sees a permission/input request while a non-interactive turn is active:
  - send the default rejection response immediately
  - record an `AcpInteractionRequiredError` on the owning `_TurnContext`
  - do not register a durable pending interaction for caller action
  - do not transition the handle into a durable `WAITING_FOR_USER` state
- after the prompt RPC finishes:
  - if the turn context captured an interaction error, raise it
  - otherwise return the normal prompt result

This keeps interaction policy attached to the call site that started the turn rather than leaking it into the general CLI interaction registry.

### 3. State Ownership Rules

State transitions need a tighter contract:

- `ACPClient` owns transport-level turn states: `BUSY`, prompt completion, prompt failure.
- `ACPManager` may surface `WAITING_FOR_USER` only for truly interactive flows.
- `approve_interaction`, `reject_interaction`, and `reply_interaction` should not unconditionally force terminal handle states for prompt lifecycle; they should only send responses and let the prompt owner drive final state.

Concretely:

- interactive CLI path:
  - `BUSY` → `WAITING_FOR_USER` when the server asks
  - `WAITING_FOR_USER` → `BUSY` when user responds
  - final `READY`/`FAILED` comes from prompt completion
- non-interactive embedding path:
  - `BUSY` stays visible for the entire prompt
  - `WAITING_FOR_USER` is either skipped entirely or treated as an internal transient not exposed through `get_status()`

This requires removing the current `reject_interaction(...): WAITING_FOR_USER -> READY` shortcut, because it can mark the server idle while the prompt is still active.

### 4. `prompt_once` API Shape

Add a small typed result model to the public API:

```python
@dataclass
class PromptResult:
    stop_reason: str
    raw: Dict[str, Any]
    session_id: Optional[str] = None
    cwd: Optional[str] = None
```

Recommended signatures:

```python
def send_prompt(
    self,
    name: str,
    text: str,
    *,
    timeout: Optional[float] = None,
    interactive: bool = True,
    cwd: Optional[str] = None,
    mcp_servers: Optional[List[dict]] = None,
) -> Dict[str, Any]: ...


def prompt_once(
    self,
    name: str,
    prompt: str,
    *,
    cwd: Optional[str] = None,
    timeout: Optional[float] = None,
    interactive: bool = False,
    mcp_servers: Optional[List[dict]] = None,
    stop_process: bool = True,
) -> PromptResult: ...
```

Implementation rule:

- `prompt_once` should not mutate or reuse a long-lived cached client from `self._clients`.
- it should create a temporary connection path, run exactly one turn, and always close it in `finally`
- if `stop_process=True`, it also stops the underlying handle in `finally`

That gives deterministic cleanup without entangling one-shot workflow calls with the CLI's reusable connection cache.

### 5. Per-Call `cwd`

Current `ACPClient` remembers only `session_id`; it does not remember the session `cwd`. Add lightweight session metadata:

```python
@dataclass
class AcpConnectionInfo:
    protocol_version: Optional[int] = None
    agent_capabilities: Dict[str, Any] = field(default_factory=dict)
    agent_info: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None
    session_cwd: Optional[str] = None
```

Then:

- `create_session(cwd=...)` stores `session_cwd`
- `ensure_connected(name, cwd=...)` may reuse a client only if `cwd is None` or `cwd == client.connection_info.session_cwd`
- otherwise it creates a fresh session/client for that call

This is the smallest change that preserves existing protocol behavior and avoids inventing a `session/prompt.cwd` extension that the server does not support.

### 6. Public API Boundary

The stable embedding surface should be enforced with exports, docs, and import cleanup:

- narrow `agentao/acp_client/__init__.py` to the supported embedding names
- update internal callers to import implementation details from concrete modules instead of relying on top-level re-exports
- add `docs/features/acp-embedding.md` with:
  - supported imports
  - one-shot usage example via `prompt_once`
  - long-lived usage example via `send_prompt(..., interactive=False)`
  - error handling example using `AcpErrorCode`

This makes the public boundary real instead of advisory.

## Implementation Plan

### Phase 1 — Error and Type Foundations

Files:

- `agentao/acp_client/client.py`
- `agentao/acp_client/models.py`
- `agentao/acp_client/__init__.py`

Changes:

- add `AcpErrorCode`, richer `AcpClientError`, `AcpInteractionRequiredError`
- add `PromptResult`
- extend `AcpConnectionInfo` with `session_cwd`
- update top-level exports to the intended embedding surface

### Phase 2 — Non-Interactive Turn Control

Files:

- `agentao/acp_client/manager.py`
- `agentao/acp_client/interaction.py`
- `agentao/acp_client/client.py`

Changes:

- add manager-owned per-turn context for non-interactive calls
- implement `send_prompt(..., interactive=False)` on top of non-blocking prompt flow
- auto-reject `session/request_permission` and `_agentao.cn/ask_user` during non-interactive turns
- keep CLI interactive behavior unchanged
- remove state transitions from interaction helpers that incorrectly terminate prompt ownership

### Phase 3 — `prompt_once` and Per-Call `cwd`

Files:

- `agentao/acp_client/manager.py`
- `agentao/acp_client/client.py`

Changes:

- add `prompt_once`
- isolate temporary clients from `self._clients`
- enforce fresh-session behavior when `cwd` differs from connected session metadata
- make cleanup deterministic in success, RPC error, interaction-required, and transport-failure paths

### Phase 4 — Docs and Import Cleanup

Files:

- `agentao/acp_client/__init__.py`
- `docs/features/acp-embedding.md` (new)
- existing ACP client docs as needed

Changes:

- remove or deprecate internal re-exports from the public package root
- document the stable embedding contract
- add migration notes for internal imports if tests or CLI modules depended on root re-exports

## Test Plan

Add or update tests in:

- `tests/test_acp_client_prompt.py`
- `tests/test_acp_client_cli.py`
- `tests/test_acp_client_jsonrpc.py`
- new `tests/test_acp_client_embedding.py` if separation helps readability

Coverage matrix:

- `send_prompt(interactive=False)` auto-rejects permission requests and raises `AcpInteractionRequiredError`
- `send_prompt(interactive=False)` auto-rejects `_agentao.cn/ask_user` and raises `AcpInteractionRequiredError`
- non-interactive rejection never leaves the handle in observable `WAITING_FOR_USER`
- non-interactive rejection never reports `READY` before the prompt RPC ends
- `prompt_once()` stops process/client on success
- `prompt_once()` stops process/client on exception
- `send_prompt(..., cwd=<same>)` reuses session
- `send_prompt(..., cwd=<different>)` creates a fresh session
- every client-side failure path sets the expected `AcpErrorCode`
- existing CLI interactive tests still pass unchanged

## Risks and Mitigations

- Risk: current tests assert `reject_interaction()` transitions directly to `READY`.
  Mitigation: rewrite those assertions around prompt-owned final states, not interaction helper side effects.
- Risk: narrowing `__init__.py` exports may break internal tests.
  Mitigation: update internal imports in the same changeset and keep any temporary compatibility alias explicitly deprecated.
- Risk: temporary `prompt_once` clients may race with a long-lived cached client for the same handle.
  Mitigation: document that embedding callers should not run concurrent one-shot and long-lived turns against the same named server without external coordination; if needed, add a per-server lock in manager.
- Risk: server may emit additional notifications after auto-rejection before prompt completion.
  Mitigation: treat the interaction error as latched on the turn context but still drain the prompt to a terminal response before raising to the caller.

## Acceptance Criteria

- `ACPManager.prompt_once(name, prompt, interactive=False)` completes one turn end-to-end and performs deterministic cleanup on both success and exception.
- A permission or `_agentao.cn/ask_user` request during a non-interactive turn raises `AcpClientError` with `code == AcpErrorCode.INTERACTION_REQUIRED`; the server never remains observably stuck in `WAITING_FOR_USER`.
- During non-interactive auto-rejection, the server does not report `READY` until the outstanding `session/prompt` has completed or failed.
- Every failure path sets an `AcpErrorCode`; a caller can `except AcpClientError as e: match e.code:` without string matching.
- `send_prompt(..., cwd=...)` applies the passed `cwd` to the turn.
- `agentao.acp_client` documents and enforces its public vs. internal surface; existing `CLI /acp` behavior and `.agentao/acp.json` format are unchanged.
- Tests cover: non-interactive permission rejection, non-interactive `ask_user` rejection, `prompt_once` cleanup on exception, per-call `cwd`, and each `AcpErrorCode` branch.

## Why This Matters

With these five deltas, kanban (and any other workflow runtime) can build on `ACPManager` directly:

- reuse `.agentao/acp.json` unchanged
- run ACP servers safely in a daemon context
- map ACP failures to kanban's `config_error` / `infra_error` / `interaction_required` buckets without brittle string parsing
- rely on a declared public API that survives internal refactors

No parallel ACP client stack, no CLI scraping, and no speculative new facade.

## Open Questions

- Should `interactive=False` ever support both policies, or should v1 standardize on **reject** only and defer **abort** until a concrete need appears? A single default keeps the state machine and tests simpler.
- Is `_agentao.cn/ask_user` stable enough to expose in the public error metadata, or should kanban only see `INTERACTION_REQUIRED` without the method name?
- For `prompt_once(ephemeral=True)`, do we stop the process too, or only close the session/client? Stopping the process is cleaner for daemons but costs ~subprocess startup per task; the answer should be an explicit contract, not an implication of `auto_start`.
