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
        ...   # config problem — surface details to operator.
              # `e.details["underlying_code"]` preserves the original
              # cause (timeout vs. disconnect vs. protocol error).
    elif e.details.get("phase") == "handshake":
        ...   # AcpRpcError raised during handshake — see §D.7
```

## D.1 Code table

| Code | Typical cause | Remediation |
|------|---------------|-------------|
| `config_invalid` | `.agentao/acp.json` malformed, missing required field, or env-var expansion failed | Validate JSON; print `e.details` — it includes `server` + the offending field |
| `server_not_found` | Called `prompt_once(name=...)` / `start_server(name=...)` with a name not in config | Check `ACPManager().get_status()` for declared names |
| `process_start_fail` | `command` not on PATH, missing executable bit, subprocess died during spawn | Inspect `e.cause` and `e.details['stderr']`; re-run the `command` + `args` manually |
| `handshake_fail` | Server process started but `initialize` / `session/new` failed (protocol/transport/timeout during setup). **Auto-emitted by the manager for non-RPC `AcpClientError`** — it reclassifies `code` from `PROTOCOL_ERROR` / `TRANSPORT_DISCONNECT` / `REQUEST_TIMEOUT` to `HANDSHAKE_FAIL` and stashes the original code in `details["underlying_code"]` so finer detail stays available (see §D.7). `AcpRpcError` is **not** re-coded (see §D.2 contract); detect RPC handshake failures via `isinstance(err, AcpRpcError) and err.details["phase"] == "handshake"`. | Check server logs; `details["underlying_code"]` tells you whether the root cause was a timeout, disconnect, or protocol error |
| `request_timeout` | RPC exceeded the `timeout=` you passed (or default) | Raise the timeout, or check whether the server is stuck in a long tool call |
| `transport_disconnect` | Server subprocess exited mid-turn, pipe closed, or stdio framing corrupted | Read `e.details['exit_code']` / stderr tail; common for OOM kills and crash bugs in the server |
| `interaction_required` | Non-interactive call (`interactive=False`, the default for `prompt_once`) but server asked for permission / user input | Switch to an interactive session, or pre-approve via `PermissionEngine` rules |
| `protocol_error` | Server sent an invalid JSON-RPC message, unexpected method, or mismatched ID | Upgrade server or file a bug; almost always a server defect |
| `server_busy` | Another turn is already active for this server and the call is fail-fast (`prompt_once` always is). In headless deployments this is the pinned failure mode for the Week 1 **single-active-turn, no-queueing** contract (see [`docs/features/headless-runtime.md`](../../../docs/features/headless-runtime.md)) | Wait and retry; there is no implicit queue — the host must poll `get_status()` and gate its own submissions |

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

Special key — **`phase`**: `details["phase"] == "handshake"` is stamped on *every* `AcpClientError` (including `AcpRpcError`) raised during the `initialize` / `session/new` setup path. It is the canonical "was this a handshake-phase failure?" signal — see §D.7.

Special key — **`underlying_code`**: for non-RPC `AcpClientError` whose `code` the manager reclassified to `handshake_fail`, the original `AcpErrorCode` (one of `PROTOCOL_ERROR` / `TRANSPORT_DISCONNECT` / `REQUEST_TIMEOUT`) is stashed here. `AcpRpcError` does not set this key — its underlying RPC detail lives on `rpc_code` / `rpc_message`.

Always log `details` alongside the message so you can diagnose without re-running.

## D.4 Exception class hierarchy

```
AcpClientError                           # base — has .code, .details, .cause
├── AcpServerNotFound (also KeyError)    # code = server_not_found
├── AcpRpcError                           # code: int (JSON-RPC wire); error_code = protocol_error
└── AcpInteractionRequiredError          # code = interaction_required
```

`AcpServerNotFound` inherits `KeyError` so legacy `except KeyError` handlers still work during migration.

`AcpRpcError` is the one subclass where `.code` is **not** an `AcpErrorCode`. It keeps the raw JSON-RPC numeric code for backwards compatibility (see §D.2); its structured category is always `error_code = AcpErrorCode.PROTOCOL_ERROR`. Handshake reclassification applies asymmetrically: for non-RPC `AcpClientError` the manager flips `code` to `HANDSHAKE_FAIL` (and stashes the original in `details["underlying_code"]`), while `AcpRpcError` is left unchanged so its class contract holds. Both paths stamp `details["phase"] = "handshake"`, so that key is the canonical cross-subclass detector.

## D.5 State-vs-error contract (headless)

For headless / daemon embedders the status surface (`ACPManager.get_status()`, `ACPManager.readiness(name)`) and the error surface are separate signals. Consume them in a fixed order:

1. **Look at `state` (or `readiness(name)`) first.** It is the authoritative "can I submit a turn right now" signal.
2. **Then consult `last_error` / `last_error_at`** for diagnostic context — these describe what most recently went wrong, not whether anything is wrong now.

Key properties of the recorded-error surface:

- `last_error` is **sticky**. A successful turn does not clear it. Intentional: a host that polls once per minute should still see the last-known failure.
- To clear the stored error explicitly (e.g., after forwarding to an external logger), call `ACPManager.reset_last_error(name)`. A new error overwrites automatically.
- `last_error_at` is a `datetime` with `tzinfo=timezone.utc`, assigned **at store time** (inside the manager), not at raise time. Use it to judge staleness; a non-None `last_error` paired with `state == "ready"` and a stale `last_error_at` is historical, not blocking.
- Two codes are **excluded** from the store because they are caller-side signals, not server state: `SERVER_BUSY` (retry overwrite would wipe real failures) and `SERVER_NOT_FOUND` (no server to attach to). All other codes are recorded.

## D.6 Retry guidance

| Code | Retryable? | Strategy |
|------|-----------|----------|
| `request_timeout` | Yes (idempotent calls) | Exponential backoff, cap attempts |
| `transport_disconnect` | Yes (after respawn) | `ACPManager.stop_server()` → `start_server()` → retry |
| `server_busy` | Yes | Wait for current turn to finish; poll `get_status()` |
| `process_start_fail` | No | Operator action required |
| `handshake_fail` | No (usually) | Operator action required. Catches non-RPC handshake failures directly; for the RPC case, also treat any exception inside a `details["phase"] == "handshake"` branch the same way (see §D.7). |
| `config_invalid` | No | Fix config |
| `server_not_found` | No | Fix call site |
| `protocol_error` | No | File a bug. **Note:** an `AcpRpcError` raised during handshake always carries `error_code = PROTOCOL_ERROR` — check `details["phase"]` first to distinguish a handshake failure (config/operator action) from a steady-state server bug. |
| `interaction_required` | — | Not a retry case — switch to interactive mode |

## D.7 Detecting handshake-phase failures (canonical pattern)

Handshake / session-setup failures split into two shapes by subclass, which the manager classifies asymmetrically:

- **Non-RPC `AcpClientError`** — e.g. a timeout, transport disconnect, or protocol-layer issue before the server responds. The manager reclassifies `code` to `AcpErrorCode.HANDSHAKE_FAIL` **and** stashes the original underlying `AcpErrorCode` in `details["underlying_code"]`, so the classic `case HANDSHAKE_FAIL:` branch still fires and embedders can further distinguish the root cause.
- **`AcpRpcError`** — the server responded with a JSON-RPC error to `initialize` / `session/new`. The class contract forbids mutating `code` (int wire code) or `error_code` (`PROTOCOL_ERROR`), so the manager leaves those alone. Detect this case via `isinstance(err, AcpRpcError)` + `details["phase"] == "handshake"`.

Both branches stamp `details["phase"] = "handshake"`, so that key is the one canonical cross-subclass detector — but existing `case AcpErrorCode.HANDSHAKE_FAIL:` code still works for the non-RPC path it has always covered.

```python
from agentao.acp_client import AcpClientError, AcpErrorCode, AcpRpcError

try:
    manager.connect_server("x", timeout=30)
except AcpRpcError as e:
    # RPC-layer handshake rejection (also matches steady-state RPC
    # errors — use `details["phase"]` to tell them apart).
    if e.details.get("phase") == "handshake":
        ...   # server rejected handshake — e.rpc_code / e.rpc_message
    else:
        ...   # steady-state JSON-RPC error on an established session
except AcpClientError as e:
    # Non-RPC handshake failures keep the legacy branch working:
    if e.code is AcpErrorCode.HANDSHAKE_FAIL:
        # `details["underlying_code"]` preserves the original cause.
        underlying = e.details.get("underlying_code")
        if underlying is AcpErrorCode.REQUEST_TIMEOUT:
            ...   # init timed out — raise timeout or check server health
        elif underlying is AcpErrorCode.TRANSPORT_DISCONNECT:
            ...   # subprocess died during setup
        else:
            ...   # protocol-layer handshake failure
    elif e.code is AcpErrorCode.REQUEST_TIMEOUT:
        ...   # steady-state timeout on an established session
```

If you want a *single* uniform predicate, use `details.get("phase") == "handshake"` — it covers both subclasses. The two-branch form above is provided so hosts that already branch on `case HANDSHAKE_FAIL` (as shown in Part 3 §3.4.8 and related examples) can extend to the RPC case without restructuring.

---

→ [Appendix G · Glossary](./g-glossary)
