# Headless Runtime (Week 1 + Week 2 + Week 3 + Week 4)

Operator-facing contract for running Agentao as a **logically headless,
daemon-capable runtime**. This page is the authoritative description of
what an embedding host may rely on right now. Subsequent weeks will add
fields and surfaces; existing ones are frozen.

Where code and this doc disagree, the code wins for **behaviour**; this
doc wins for **product intent** — file a bug on any drift.

Sample consumer: [`examples/headless_worker.py`](../../examples/headless_worker.py).
Run it from the repository root as the Week 1 smoke entry:

```bash
uv run python examples/headless_worker.py
```

It spins up an inline mock ACP server, runs success / error / cancel
paths, and prints a typed status snapshot after each. Exit code `0`
means every required path worked; the script is the CI smoke job.

## 1. Public runtime surface

Three call sites drive turns against a declared ACP server. Week 1
fixes product-level support as follows:

| Entry point | Support level | Intended use |
|-------------|---------------|---------------|
| `ACPManager.prompt_once(name, prompt, …)` | **public** | One-shot fire-and-forget turn; fail-fast concurrency; ephemeral client when no long-lived one exists |
| `ACPManager.send_prompt(name, prompt, …)` | **public** | Long-lived session variant; fail-fast on the per-server lock — a second concurrent caller raises `SERVER_BUSY` instead of blocking |
| `ACPManager.send_prompt_nonblocking(…)` + `finish_prompt_nonblocking` / `cancel_prompt_nonblocking` | **internal / unstable** | Lower-level async helper used by the interactive CLI inline-confirmation pipeline. Signatures may change without notice; do not depend on them from embedding code |

Consequences:

- Headless hosts should call `prompt_once` or `send_prompt`. Week 3's
  `interaction_policy=` per-call override will land on these two only.
- `send_prompt_nonblocking` remains available for internal consumers
  but is not part of the embedding contract. Its documentation lives in
  module docstrings, not in the developer guide.

## 2. Concurrency contract

Every named ACP server in the manager has **one active turn slot**.

- A turn starts when `prompt_once` / `send_prompt` /
  `send_prompt_nonblocking` acquires the per-server lock and installs
  a `_TurnContext` under the manager's `_active_turns` map.
- A turn ends when the entry point's `finally` clears the slot — on
  success, error, timeout, and cancel alike.
- There is **no queueing**. A second concurrent caller on the same
  server raises `AcpClientError(code=AcpErrorCode.SERVER_BUSY)`:
  - `prompt_once` is fail-fast by construction (non-blocking lock
    acquire).
  - `send_prompt` is **also fail-fast** on the manager lock — a
    second concurrent `send_prompt`, `prompt_once`, or
    `send_prompt_nonblocking` against the same server raises
    `SERVER_BUSY` immediately instead of blocking a worker thread
    behind a slow or stuck turn. The underlying
    `ACPClient.send_prompt` keeps its own single-in-flight
    `session/prompt` guard as belt-and-suspenders for the nonblocking
    turn path.
- `AcpErrorCode.SERVER_BUSY` is the existing code from
  `agentao/acp_client/client.py:54`; Week 1 only documents and pins
  it. No new error type is introduced.

Consumers that want to "wait for capacity" must implement their own
polling on `get_status()` — the manager never queues for them.

## 3. Status snapshot (v1 + v2)

`ACPManager.get_status()` returns `list[ServerStatus]`. The dataclass
is frozen; Week 2 added diagnostic fields additively — Week 1 field
semantics are unchanged.

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

@dataclass(frozen=True)
class ServerStatus:
    # Week 1 — core (frozen)
    server: str                         # name in .agentao/acp.json
    state: str                          # ServerState enum value (.value)
    pid: Optional[int]                  # None when no process is running
    has_active_turn: bool               # derived from manager's active turn slot

    # Week 2 — diagnostics (additive; shape frozen)
    active_session_id: Optional[str] = None
    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None   # tz-aware, UTC
    inbox_pending: int = 0
    interaction_pending: int = 0
    config_warnings: List[str] = field(default_factory=list)
```

### 3.1 Week 1 field semantics (frozen)

- **`server`** — the name under which the server was registered. This
  replaces the legacy `"name"` dict key.
- **`state`** — the `ServerState` enum value (string). Consumers
  should treat it as the primary readiness signal.
- **`pid`** — operating system process id when the subprocess is up,
  otherwise `None`.
- **`has_active_turn`** — `True` for the full lifetime of any turn
  running under the manager's `_active_turns` slot, including the
  in-flight interaction phase of non-interactive turns. It is
  **not** derived from handle state, so `ServerState.WAITING_FOR_USER`
  on the handle does not cause `has_active_turn` to flip to `False`.

### 3.2 Week 2 field semantics

- **`active_session_id`** — current `sessionId` from the server's
  long-lived client (or the ephemeral client underneath an in-flight
  `prompt_once`). `None` while no session has been created on the
  server yet. Populated whether or not a turn is active.
- **`last_error`** — the most recent human-readable error raised out
  of a public entry point (`send_prompt` / `prompt_once` /
  `send_prompt_nonblocking`). See §4 for the state-vs-error contract.
- **`last_error_at`** — `datetime` with `tzinfo=timezone.utc`, assigned
  **at the moment the error is stored on the manager**, not at the
  moment it was raised. Use it to judge staleness, not to reconstruct
  exact raise-time instrumentation. Consumers may treat
  `now - last_error_at > Δ` combined with `state == "ready"` as
  "historical failure, not blocking."
- **`inbox_pending`** — count of messages currently queued in the
  shared `Inbox` attributed to this server (filter on
  `InboxMessage.server`).
- **`interaction_pending`** — `len(mgr.interactions.list_pending(server=name))`,
  i.e. unresolved server-initiated permission / input requests for this
  server. Singular name (v2) replaces the pre-v1 dict alias
  `interactions_pending`.
- **`config_warnings`** — per-server deprecation surface. Populated by
  the Week 3 legacy-config handling path; empty today for fresh
  configs.

### 3.3 Readiness classifier (v2)

Consumers that only want to know "can I submit a turn right now?"
should call `ACPManager.readiness(name)` rather than string-matching
on `state`:

```python
mgr.readiness("my-server")   # -> "ready" | "busy" | "failed" | "not_ready"
mgr.is_ready("my-server")    # -> bool, shortcut for readiness == "ready"
```

Mapping:

| Classification | When |
|----------------|------|
| `"ready"` | Handle `state == READY` and no active turn slot. Safe to submit. |
| `"busy"` | Manager has an active turn slot **or** handle state is `BUSY` / `WAITING_FOR_USER`. Second submit will raise `SERVER_BUSY`. |
| `"failed"` | Handle state is `FAILED` **or** the sticky fatal flag is set. Auto-recovery handles recoverable idle exits via `classify_process_death` up to `maxRecoverableRestarts` (default 3); past that — or on any fatal classification — an explicit `restart_server` / `start_server` by an operator is required. See §7. |
| `"not_ready"` | Handle is still coming up or winding down (`CONFIGURED` / `STARTING` / `INITIALIZING` / `STOPPING` / `STOPPED`). |

The classification is deliberately coarse and stable. Use the raw
`state` string when you need full diagnostic detail; prefer
`readiness()` when you need a small, durable decision surface.

### 3.4 Migration from the pre-Week-1 dict shape

The previous `get_status()` returned `list[dict]` with keys `name`,
`state`, `pid`, `last_error`, `last_activity`, `description`,
`inbox_pending`, `interactions_pending`, `stderr_lines`. This was a
CLI-friendly shape; it is now collapsed into the typed contract
above.

| Old access | New access |
|------------|------------|
| `s["name"]` | `s.server` |
| `s["state"]` | `s.state` |
| `s["pid"]` | `s.pid` |
| `s["last_error"]` | `s.last_error` (re-exposed on `ServerStatus` in Week 2; see §4) |
| `s["description"]` | `mgr.get_handle(s.server).config.description` |
| `s["inbox_pending"]` | `mgr.inbox.pending_count` |
| `s["interactions_pending"]` | `len(mgr.interactions.list_pending(server=s.server))` |
| `s["stderr_lines"]` | `len(mgr.get_server_logs(s.server, n=200))` |

This is a deliberate, once-for-all API convergence; there is no
`get_status_typed()` side channel and no permanent dict alias.

## 4. State-vs-error contract (v2)

Consume the snapshot in a fixed order:

1. **Look at `state` first** (or `readiness(name)` for the typed
   classification). It is the single authoritative signal for whether
   the server will accept a turn right now.
2. **Then consult `last_error` / `last_error_at`** for diagnostic
   context. These are *diagnostic*, not gating — they describe what
   most recently went wrong, not whether anything is wrong now.

Consequences of this ordering:

- `last_error` is **sticky**. A successful turn does **not** clear it.
  If you submit `prompt A → prompt B`, and A raised, B succeeds, the
  snapshot after B still shows A's error. This is intentional: a host
  that polls once per minute still gets the last-known failure
  context.
- To clear the error surface explicitly (for example, after the host
  has logged the failure upstream and wants a clean panel), call
  `ACPManager.reset_last_error(name)`.
- A new error **overwrites** the stored one (with fresh
  `last_error_at`); the store is a most-recent, not a journal.
- `last_error_at` is assigned **inside** the manager's store path,
  using `datetime.now(timezone.utc)`. It is not taken at raise time
  and it is not taken from the exception payload. This is an explicit
  product decision so embedders have a single authoritative clock.
  The regression suite asserts this by patching
  `agentao.acp_client.manager.datetime` during a recorded error and
  verifying the snapshot reflects the patched `now()`.

### 4.1 What does **not** go into `last_error`

Two `AcpErrorCode` values are filtered out of the store because they
are caller-side concurrency / misuse signals, not server state:

| Code | Why filtered |
|------|--------------|
| `SERVER_BUSY` | Fail-fast concurrency signal for `prompt_once` (or the `ACPClient`-level guard on `send_prompt`). Every retry would overwrite a real failure. |
| `SERVER_NOT_FOUND` | Raised before any server-side work happens; there is no per-server state to attach to. |

All other codes — `REQUEST_TIMEOUT`, `INTERACTION_REQUIRED`,
`HANDSHAKE_FAIL`, `TRANSPORT_DISCONNECT`, `PROTOCOL_ERROR`,
`CONFIG_INVALID`, `PROCESS_START_FAIL` — are recorded.

## 5. Error classification

Week 1 pins — but does not extend — the existing
`AcpErrorCode` taxonomy. For a headless host the codes that matter are:

| Code | When it fires | Action |
|------|--------------|--------|
| `SERVER_BUSY` | Concurrent turn submitted against the same server | Back off and retry; do not assume queueing |
| `SERVER_NOT_FOUND` | Name not in config | Fix config or call site |
| `HANDSHAKE_FAIL` | Subprocess came up but `initialize`/`session/new` failed. Auto-reclassified on **non-RPC** `AcpClientError` (original `AcpErrorCode` preserved in `details["underlying_code"]`); `AcpRpcError` raised during handshake keeps its wire `code: int` and is identified by `details["phase"] == "handshake"` instead. | Operator action |
| `REQUEST_TIMEOUT` | Turn exceeded the per-call `timeout=` | Safe to retry; turn slot is released |
| `INTERACTION_REQUIRED` | Non-interactive turn received a `session/request_permission` or `_agentao.cn/ask_user` from the server | Embedder's interaction policy was not satisfied; do not retry silently |
| `TRANSPORT_DISCONNECT` | Process died mid-RPC | Recovery policy defined in §7: active-turn deaths are always allowed at least one rebuild; idle-exit recoveries are capped by `maxRecoverableRestarts`. Operator fallback: `restart_server` / `start_server`. |

## 6. Interaction policy (Week 3)

Non-interactive turns need one decision point: when the server asks
for a `session/request_permission` or `_agentao.cn/ask_user`, does
the runtime auto-reject or auto-approve? Week 3 exposes a minimal
two-layer model over that decision.

### 6.1 `InteractionPolicy`

```python
from agentao.acp_client import InteractionPolicy

InteractionPolicy(mode="reject_all")   # default
InteractionPolicy(mode="accept_all")
```

Only `mode` exists. Do not add more fields; when a second dimension
(timeout family, per-tool split, …) becomes necessary, it belongs on
a new options object, not this one.

- `reject_all` — auto-reject every permission request; every
  non-interactive turn that hits one surfaces as
  `AcpInteractionRequiredError`.
- `accept_all` — auto-approve permission requests that carry an
  allow-flavored option. `ask_user` still errors (no user to answer).

### 6.2 Precedence

```
per-call override   >   server default (nonInteractivePolicy)
```

- **Server default** — `nonInteractivePolicy` in
  `.agentao/acp.json` (structured object, §6.4). Missing = implicit
  `{"mode": "reject_all"}`.
- **Per-call override** — `interaction_policy=` kwarg on
  `prompt_once` / `send_prompt`. Accepts `InteractionPolicy` or the
  bare string `"reject_all"` / `"accept_all"`. `None` falls back to
  the server default.

The resolved policy is captured on the manager's `_TurnContext` at
turn start, so overrides are consulted inside the running turn's
router — not re-read from the config.

### 6.3 Signatures

```python
mgr.send_prompt(
    "srv", "…",
    interactive=False,
    interaction_policy="accept_all",       # override
)

mgr.prompt_once(
    "srv", "…",
    interaction_policy=InteractionPolicy(mode="accept_all"),
)
```

The `send_prompt_nonblocking` family **stays internal** (Week 1
decision). It does not receive an `interaction_policy=` kwarg; the
Week 3 policy surface is `prompt_once` + `send_prompt` only.

### 6.4 Config shape (`.agentao/acp.json`)

The structured object form is the only supported shape as of Week 3:

```json
{
  "servers": {
    "my-server": {
      "command": "…",
      "args": [],
      "env": {},
      "cwd": ".",
      "nonInteractivePolicy": { "mode": "accept_all" }
    }
  }
}
```

The legacy bare-string form (`"nonInteractivePolicy": "reject_all"`)
is **rejected at config-load time** with a configuration error that
names the new shape and points at the migration appendix. There is
no silent upgrade. Errors surface at `AcpClientConfig.from_dict` /
`load_acp_client_config` — they cannot slip through to
`send_prompt` execution time.

Rationale: Week 1 of the headless plan prefers loud configuration
failure over silent drift. Anyone running an old config should see
the failure during `ACPManager.from_project()`, not when a turn
later hits a permission prompt.

### 6.5 `config_warnings`

The Week 2 `ServerStatus.config_warnings` list is the stable surface
for per-server, non-fatal config-level deprecations. Week 3 does not
populate it for the legacy-string case (that path is a hard error,
not a warning). Future deprecations — added in a way that keeps
existing configs running — will write human-readable entries here.

## 7. Lifecycle & recovery (Week 4)

Week 4 closes the loop between "a turn went wrong" and "the runtime
is usable again" without either orphaning state or respawning into a
crash loop.

### 7.1 Deterministic cleanup after cancel / timeout (Issue 15)

Every failure path in `send_prompt` / `prompt_once` /
`send_prompt_nonblocking` runs the same release sequence exactly
once, in this order:

1. **Pending-slot drop** via `ACPClient.discard_pending_slot(rid)` —
   idempotent and raise-free. Called **before** `session/cancel` is
   sent so a broken transport cannot leave the client poisoned for
   the next turn.
2. **Turn-slot clear** via `ACPManager._clear_turn(name)` — the
   manager's single-active-turn slot is dropped inside the `finally`
   block, not inside the `try`. Runs on success, timeout, cancel,
   and transport error identically.
3. **Per-server lock release** — the `send_prompt` / `prompt_once`
   outer `finally` releases the lock regardless of what raised
   above. `send_prompt_nonblocking`'s rollback path releases the
   lock after clearing the slot, never before.
4. **`last_error` record** (Week 2) — captured in the outer
   `except Exception` block *before* the lock is released, so a
   later reader of `get_status()` on the same tick already sees the
   error.

Combined, the above guarantees: `cancel_turn` or a timeout never
leaves a server in `busy` / locked state for the next call.

### 7.2 Client / process death classification (Issue 16)

When a subprocess dies between calls — or under an active turn — the
runtime decides whether to auto-rebuild or give up:

| Trigger | Classification | Action |
|---------|----------------|--------|
| idle process exits cleanly (exit 0) | **recoverable** | Lazy rebuild on next call; `last_error` untouched |
| idle process exits non-zero | **recoverable** within cap; **fatal** beyond | Bump `restart_count`; cap is `maxRecoverableRestarts` (default 3) |
| active turn + process dies (any reason) | **recoverable** | Current turn fails with `last_error`; next call rebuilds |
| stdio pipe EOF with process still alive | **recoverable** | Rebuild client; process is not respawned |
| OOM / SIGKILL / exit 137 / signal-terminated | **fatal** | No auto-respawn; `state == FAILED`; explicit restart required |
| consecutive handshake failure after restart | **fatal** | Environment / config problem; not a transient fault |
| user-initiated `cancel_turn` | (n/a here) | Handled by §7.1 cleanup |

Implementation notes:

- The classifier is a pure function, `classify_process_death`,
  exported from `agentao.acp_client`. It takes `exit_code`,
  `signaled`, `during_active_turn`, `restart_count`,
  `max_recoverable_restarts`, `handshake_fail_streak` and returns
  `"recoverable"` / `"fatal"`. Tests exercise every matrix row.
- The manager consults it inside `ensure_connected` before returning
  a cached client. A recoverable classification closes the dead
  cached client, bumps `_restart_counts[name]`, and falls through to
  `connect_server`. A fatal classification adds the server to the
  sticky `_fatal_servers` set and raises `AcpClientError` with
  `code=TRANSPORT_DISCONNECT` and `details={"recovery": "fatal"}`.
- `restart_count` is **reset to 0** on the first successful turn
  after a recovery. `handshake_fail_streak` is reset the same way.
- The fatal mark is cleared only by an explicit `restart_server` or
  `start_server` — never by a passing turn. This is a deliberate
  product decision: if the runtime decided a server is fatal, an
  operator should acknowledge it before the next auto-rebuild.
- Embedders distinguish recoverable vs fatal via `is_fatal(name)`
  and the `(state, last_error)` pair on `ServerStatus`. A server in
  `state == FAILED` with `is_fatal(name) == True` will refuse all
  `ensure_connected` / `send_prompt` / `prompt_once` calls until the
  operator acts.

### 7.3 Config

```json
{
  "servers": {
    "my-server": {
      …
      "maxRecoverableRestarts": 3
    }
  }
}
```

Defaults to 3 when absent. Must be a non-negative integer; negative
values or non-int types raise `AcpConfigError` at config-load time
(consistent with the Week 3 loud-fail stance).

### 7.4 Regression suite

`tests/test_headless_runtime.py::TestDaemonRegression` runs the
Week 4 end-to-end scenarios on every CI push:

- Long session reuse stays READY between turns.
- Reject → next turn succeeds.
- Cancel → next turn succeeds.
- Timeout → next turn succeeds.
- Process death → classifier decides; manager either rebuilds or
  stays fatal, and `is_fatal(name)` matches.

The Week 1 sample consumer `examples/headless_worker.py` exercises
the first four paths as the CI smoke job; the regression suite adds
the lifecycle-specific cases on top.

## 8. What's **not** in scope

Deliberately out of the 4-week plan:

- Multi-dimension policy (timeouts, per-tool split, precedence
  beyond two layers).
- Transport auto-respawn for fatal deaths (operator-action only by
  design).
- A TCP / local-socket daemon transport; the headless runtime stays
  on stdio + embedding.
- ACP protocol extensions.

If you find yourself reaching for one of these, open a follow-up
issue rather than bolting it onto this plan.
