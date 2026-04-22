# ACP Embedding Guide

`agentao.acp_client` exposes a small, stable surface so workflow runtimes
(e.g. kanban) can drive project-local ACP servers from a daemon without
scraping the CLI. This doc covers the supported API, usage patterns, and
error handling. For the internal design, see
[`kanban-acp-embedded-client-issue.md`](../kanban-acp-embedded-client-issue.md)
and [`implementation/ACP_EMBEDDING_IMPLEMENTATION_PLAN.md`](../implementation/ACP_EMBEDDING_IMPLEMENTATION_PLAN.md).

## Supported Imports

The only semver-stable import root is `agentao.acp_client`:

```python
from agentao.acp_client import (
    ACPManager,
    AcpClientConfig, AcpServerConfig,
    AcpConfigError, AcpClientError, AcpRpcError, AcpErrorCode,
    AcpInteractionRequiredError,
    ServerState, AcpProcessInfo,
    PromptResult,
    load_acp_client_config,
)
```

Anything else — `ACPClient`, `ACPProcessHandle`, `Inbox`,
`InteractionRegistry`, `AcpConnectionInfo`, router / render helpers — is
an internal implementation detail. Import it from its concrete submodule
(`agentao.acp_client.client`, `.process`, `.interaction`, `.inbox`, etc.)
and accept that it can change between releases.

## Concurrency Contract

Per named server, `ACPManager` serializes turn-bearing operations:

- `send_prompt` **blocks** on the per-server lock.
- `prompt_once` **fails fast** with `AcpClientError(code=SERVER_BUSY)`
  when another turn is active for the same server.
- `cancel_turn` does not wait for the lock; it signals the active turn
  context so a latched interaction error is suppressed in favor of the
  cancellation outcome.

Timeouts cover the RPC wait, not time spent waiting for the per-server
lock. Queue work yourself if you need bounded wait-for-lock semantics.

## Non-Interactive Mode

A daemon has no user to prompt. Set `interactive=False` so the manager
auto-rejects `session/request_permission` and `_agentao.cn/ask_user`
instead of transitioning to `WAITING_FOR_USER`.

When a non-interactive turn is interrupted by such a request, the
outstanding `session/prompt` RPC still runs to completion; the first
rejected request is latched and re-raised as
`AcpInteractionRequiredError` after the RPC terminates.

## One-Shot: `prompt_once`

Right for per-task daemon execution with deterministic cleanup:

```python
from agentao.acp_client import ACPManager, AcpClientError, AcpErrorCode

mgr = ACPManager.from_project("/path/to/project")
try:
    result = mgr.prompt_once(
        "claude-code-worker",
        "Summarize repo structure",
        cwd="/path/to/task-checkout",
        timeout=60,
        interactive=False,
        stop_process=True,  # default
    )
    print(result.stop_reason, result.session_id, result.cwd)
    print(result.raw)
except AcpClientError as exc:
    if exc.code is AcpErrorCode.SERVER_BUSY:
        # Another turn is active for this server; queue and retry.
        ...
```

Guarantees:

- If no long-lived client exists for this server, an ephemeral client is
  built, used, and closed in `finally`.
- `stop_process=True` stops the subprocess on exit **only when** this
  call owned an ephemeral client. If a long-lived client already exists
  for the same server, the process is shared and survives.
- Ephemeral clients are not registered in `mgr._clients` and do not
  appear in `get_status()`.

## Long-Lived: `send_prompt(interactive=False)`

Right when you want to reuse one session across many tasks and accept
per-call `cwd` / `mcp_servers`:

```python
mgr = ACPManager.from_project()
mgr.start_all()
try:
    raw = mgr.send_prompt(
        "codex-reviewer",
        "Review the diff",
        cwd="/path/to/task-checkout",
        mcp_servers=[],
        timeout=120,
        interactive=False,
    )
    print(raw["stopReason"])
finally:
    mgr.stop_all()
```

Session reuse is conditional on both `cwd` and `mcp_servers`. A
mismatch reruns `session/new` on the same client (same transport) to
get a fresh session without restarting the subprocess.

## Error Taxonomy

Every client-originated failure sets a structured `AcpErrorCode`:

| Code | Meaning |
| --- | --- |
| `CONFIG_INVALID` | `.agentao/acp.json` failed validation |
| `SERVER_NOT_FOUND` | Unknown server name |
| `PROCESS_START_FAIL` | Subprocess failed to start |
| `HANDSHAKE_FAIL` | `initialize` or `session/new` failed. Auto-reclassified on **non-RPC** `AcpClientError` (original code preserved in `details["underlying_code"]`); `AcpRpcError` raised during handshake keeps its wire `code: int` and surfaces the handshake context via `details["phase"] == "handshake"`. See [Appendix D §D.7](/en/appendix/d-error-codes#d-7-detecting-handshake-phase-failures-canonical-pattern). |
| `REQUEST_TIMEOUT` | RPC or prompt timed out |
| `TRANSPORT_DISCONNECT` | Stdin broken, process exited, connection closed |
| `INTERACTION_REQUIRED` | Non-interactive turn hit a permission/input request |
| `PROTOCOL_ERROR` | Server returned a JSON-RPC error; malformed payload |
| `SERVER_BUSY` | `prompt_once` lock conflict |

Branch on `exc.code`, not on message strings:

```python
from agentao.acp_client import AcpClientError, AcpErrorCode

try:
    mgr.prompt_once("worker", prompt)
except AcpClientError as exc:
    match exc.code:
        case AcpErrorCode.SERVER_BUSY:
            ...
        case AcpErrorCode.INTERACTION_REQUIRED:
            ...
        case AcpErrorCode.REQUEST_TIMEOUT:
            ...
        case _:
            raise
```

`AcpRpcError` is a subclass of `AcpClientError` and honors the same
structured `.code: AcpErrorCode` contract — its code is always
`AcpErrorCode.PROTOCOL_ERROR`, so handlers that match on
`err.code` do the right thing whether the exception is an
`AcpRpcError` or any other `AcpClientError`. The raw JSON-RPC numeric
error code is available on `rpc_code`; `rpc_message` / `data` carry
the rest of the JSON-RPC payload. For backward compatibility the
constructor still accepts `code=` and `message=` as keyword aliases
for `rpc_code` / `rpc_message`.

`AcpInteractionRequiredError` exposes `server`, `prompt`, and
`options` as stable public fields. The raw server method
(`session/request_permission` / `_agentao.cn/ask_user`) is available
via `exc.details["method"]` for diagnostics only — do not branch on it.

## Status and Diagnostics

- `mgr.get_status()` returns `list[ServerStatus]` — one typed entry per
  configured server. The v1 fields are `server`, `state`, `pid`,
  `has_active_turn`. Ephemeral clients do not contribute durable
  entries to this snapshot. Full contract in
  [headless-runtime.md](./headless-runtime.md).
- `mgr.get_server_logs(name, n=50)` returns the last *n* stderr lines
  captured from the subprocess — the right place to look for daemon
  diagnostics when an RPC fails.
- Week 2 adds diagnostic fields on the same dataclass (`last_error`,
  `last_error_at`, `active_session_id`, `inbox_pending`,
  `interaction_pending`, `config_warnings`). Until then read them
  from `mgr.get_handle(name).info`, `mgr.inbox`, and `mgr.interactions`.

## Concurrency Cheat Sheet

| Situation | Behavior |
| --- | --- |
| Two `send_prompt` callers, same server | Second blocks on per-server lock |
| Two `prompt_once` callers, same server | Second raises `SERVER_BUSY` |
| `send_prompt` running, `prompt_once` called | `prompt_once` raises `SERVER_BUSY` |
| `cancel_turn` during non-interactive turn | Latched interaction error suppressed; prompt RPC runs to completion and returns normally |
| `prompt_once` with existing long-lived client | Reuses the client; `stop_process=True` does not stop the subprocess |

## Migration From Earlier Agentao

If your workflow code imported any of the following from
`agentao.acp_client`, move the import to the concrete submodule:

| Before | After |
| --- | --- |
| `from agentao.acp_client import ACPClient` | `from agentao.acp_client.client import ACPClient` |
| `from agentao.acp_client import ACPProcessHandle` | `from agentao.acp_client.process import ACPProcessHandle` |
| `from agentao.acp_client import AcpConnectionInfo` | `from agentao.acp_client.client import AcpConnectionInfo` |
| `from agentao.acp_client import Inbox, InboxMessage, MessageKind` | `from agentao.acp_client.inbox import ...` |
| `from agentao.acp_client import InteractionKind, InteractionRegistry, PendingInteraction` | `from agentao.acp_client.interaction import ...` |
| `from agentao.acp_client import AcpExplicitRoute, detect_explicit_route` | `from agentao.acp_client.router import ...` |

These submodule imports are **supported for internal/CLI code** but are
not part of the embedding contract. Treat them as best-effort.
