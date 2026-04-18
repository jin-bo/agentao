# Appendix D · AcpErrorCode Reference

When Agentao is used as an **ACP client** (`from agentao.acp_client import ACPManager`), every failure surfaces as an `AcpClientError` (or a subclass) with a structured `code: AcpErrorCode`. Branch on `code`, not on message strings — messages are considered unstable.

```python
from agentao.acp_client import ACPManager, AcpClientError, AcpErrorCode

try:
    result = manager.prompt_once(name="x", prompt="hi", timeout=30)
except AcpClientError as e:
    if e.code is AcpErrorCode.REQUEST_TIMEOUT:
        ...   # user-facing "please retry"
    elif e.code is AcpErrorCode.HANDSHAKE_FAIL:
        ...   # config problem — surface details to operator
```

## D.1 Code table

| Code | Typical cause | Remediation |
|------|---------------|-------------|
| `config_invalid` | `.agentao/acp.json` malformed, missing required field, or env-var expansion failed | Validate JSON; print `e.details` — it includes `server` + the offending field |
| `server_not_found` | Called `prompt_once(name=...)` / `start_server(name=...)` with a name not in config | Check `ACPManager().get_status()` for declared names |
| `process_start_fail` | `command` not on PATH, missing executable bit, subprocess died during spawn | Inspect `e.cause` and `e.details['stderr']`; re-run the `command` + `args` manually |
| `handshake_fail` | Server process started but `initialize` response never arrived, or capabilities incompatible | Often downstream of `transport_disconnect` / `request_timeout` during init — check server logs |
| `request_timeout` | RPC exceeded the `timeout=` you passed (or default) | Raise the timeout, or check whether the server is stuck in a long tool call |
| `transport_disconnect` | Server subprocess exited mid-turn, pipe closed, or stdio framing corrupted | Read `e.details['exit_code']` / stderr tail; common for OOM kills and crash bugs in the server |
| `interaction_required` | Non-interactive call (`interactive=False`, the default for `prompt_once`) but server asked for permission / user input | Switch to an interactive session, or pre-approve via `PermissionEngine` rules |
| `protocol_error` | Server sent an invalid JSON-RPC message, unexpected method, or mismatched ID | Upgrade server or file a bug; almost always a server defect |
| `server_busy` | Another turn is already active for this server and the call is fail-fast (`prompt_once` always is) | Wait and retry, or use a session-based API with queueing |

## D.2 JSON-RPC numeric codes vs `AcpErrorCode`

Two namespaces — don't confuse them:

| Layer | Type | Example | Where to read |
|-------|------|---------|---------------|
| **Structured classification** | `AcpErrorCode` (string enum) | `AcpErrorCode.REQUEST_TIMEOUT` | `err.code` for non-RPC errors; `err.error_code` on `AcpRpcError` |
| **JSON-RPC wire code** | `int` | `-32603` (Internal error), `-32601` (Method not found) | `AcpRpcError.rpc_code` (also shadowed onto `err.code` for legacy callers) |

`AcpRpcError` always carries `error_code = AcpErrorCode.PROTOCOL_ERROR` — the underlying category. If you need the numeric code, read `rpc_code` explicitly:

```python
from agentao.acp_client import AcpRpcError

try:
    ...
except AcpRpcError as e:
    print(e.rpc_code, e.rpc_message)    # e.g. -32601, "Method not found"
    print(e.error_code)                  # always AcpErrorCode.PROTOCOL_ERROR
```

## D.3 Details dict

Every `AcpClientError` carries a `details: dict` with context relevant to the code:

| Code | Typical `details` keys |
|------|----------------------|
| `server_not_found` | `server` |
| `process_start_fail` | `server`, `command`, `args`, `stderr` (tail) |
| `handshake_fail` | `server`, `protocol_version`, `phase` |
| `request_timeout` | `server`, `method`, `timeout` |
| `transport_disconnect` | `server`, `exit_code` |
| `interaction_required` | `server`, `method`, `prompt`, `options` |
| `server_busy` | `server` |

Always log `details` alongside the message so you can diagnose without re-running.

## D.4 Exception class hierarchy

```
AcpClientError                           # base — has .code, .details, .cause
├── AcpServerNotFound (also KeyError)    # code = server_not_found
├── AcpRpcError                           # JSON-RPC error response (protocol_error)
└── AcpInteractionRequiredError          # code = interaction_required
```

`AcpServerNotFound` inherits `KeyError` so legacy `except KeyError` handlers still work during migration.

## D.5 Retry guidance

| Code | Retryable? | Strategy |
|------|-----------|----------|
| `request_timeout` | Yes (idempotent calls) | Exponential backoff, cap attempts |
| `transport_disconnect` | Yes (after respawn) | `ACPManager.stop_server()` → `start_server()` → retry |
| `server_busy` | Yes | Wait for current turn to finish; poll `get_status()` |
| `process_start_fail` | No | Operator action required |
| `handshake_fail` | No (usually) | Operator action required |
| `config_invalid` | No | Fix config |
| `server_not_found` | No | Fix call site |
| `protocol_error` | No | File a bug |
| `interaction_required` | — | Not a retry case — switch to interactive mode |

---

→ [Appendix G · Glossary](./g-glossary)
