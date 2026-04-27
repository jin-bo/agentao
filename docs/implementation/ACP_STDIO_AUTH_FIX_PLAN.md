# ACP Stdio & Authorization Flow — Fix Plan (v2)

Consolidated review of three issues found while debugging dynamic model
configuration and the authorization flow under the ACP (Agent Communication
Protocol) stdio transport. All three findings were independently verified
against the current code on `chore/bump-0.2.15-dev`.

## Fix order

1. **P2 first** — restore reliable diagnostics; otherwise debugging signal
   itself is unreliable and the file-handler pile-up amplifies log noise.
2. **P1 next** — close the stdio-inheritance hole so authorization flows no
   longer hang.
3. **P3 / P3+ last** — on top of stable diagnostics + transport, add model
   switching, context parameter sync, and the model-list interface.

---

## P1 — Shell subprocess inherits stdin (Critical)

### Symptom
`agentao/tools/shell.py:272` (Windows background), `agentao/tools/shell.py:288`
(POSIX background), and `agentao/tools/shell.py:323` (foreground) all call
`subprocess.Popen` without passing `stdin`. Under the ACP stdio transport the
parent process's `sys.stdin` is the JSON-RPC channel; a `shell=True` child
inherits that handle, and even incidental reads by the shell wrapper or by a
`tee`/`cat`/`git`-like child can corrupt JSON-RPC framing — manifesting as
hangs in tool calls (especially those that go through the authorization path).

### Fix
Pass `stdin=subprocess.DEVNULL` to every `Popen` in `shell.py`:

- foreground execution
- background execution (Windows + POSIX branches)
- `taskkill` invocation in the timeout path (for consistency)

Not Windows-only — POSIX is equally affected.

### Acceptance
- Foreground and background `ShellTool` `Popen` calls include
  `stdin=subprocess.DEVNULL`.
- Under ACP stdio, a tool call that requires authorization completes the
  confirm/cancel round-trip without the child stealing stdin bytes.

---

## P2 — LLMClient log-handler dual constraint (High)

### Symptom
`agentao/acp/server.py:252-258` installs a stderr `StreamHandler` on the
`agentao` package logger so ACP-mode diagnostics reach the host. Then
`agentao/llm/client.py:147` calls `pkg_logger.handlers.clear()`, which
removes that stderr handler. Conversely, removing the `clear()` naively
would let repeated `LLMClient` construction (each model switch in ACP mode
re-constructs the client) accumulate multiple `RotatingFileHandler`s — log
lines would be written N times.

Both constraints must hold:

1. **Preserve handlers we don't own** (stderr handler installed by ACP, or
   anything the host injected).
2. **Avoid duplicate file handlers** across LLMClient reconstruction.

### Fix
Replace `pkg_logger.handlers.clear()` with marker-based selective cleanup:

- Tag the file handler that LLMClient creates, e.g.
  `handler._agentao_llm_file_handler = True`.
- On (re)initialization, iterate `pkg_logger.handlers`, `removeHandler` +
  `close()` only handlers carrying that marker, then `addHandler` the new
  file handler.
- Never call `handlers.clear()`.

### Acceptance
- After ACP installs its stderr handler, constructing `LLMClient` leaves
  the stderr `StreamHandler` in place.
- Constructing `LLMClient` N times in a row leaves at most one handler with
  `_agentao_llm_file_handler = True` on the `agentao` logger; the log file
  does not contain N-fold duplicate lines.

---

## P3 — ACP missing `set_model` / `set_mode` / `list_models` (Medium)

### Symptom
`agentao/acp/__main__.py:67-71` only registers `initialize`, `session/new`,
`session/prompt`, `session/cancel`, and `session/load`.
`agentao/acp/protocol.py` defines no `session/set_model`,
`session/set_mode`, or `session/list_models` constants. Consequence:

1. UI model switching returns `Method not found`.
2. There is no way to push `contextLength` / `maxTokens` from the front end,
   so the agent's context window is mis-configured and triggers improper
   compression.
3. The front end has no way to refresh the model list after `initialize`.

### Interface contract — strict separation of three knobs
The two `max_tokens` fields in the codebase are **not interchangeable**:

| Frontend field   | Target attribute                           | Meaning                              |
|------------------|--------------------------------------------|--------------------------------------|
| `model`          | `agent.set_model()` (`runtime/model.py:51`)| Active model id                      |
| `contextLength`  | `agent.context_manager.max_tokens`         | Context window — drives compression  |
| `maxTokens`      | `agent.llm.max_tokens`                     | Per-request completion cap           |

Rules:

- The three knobs **never overwrite each other**.
- A request that only carries `model` must not silently reset existing
  `contextLength` / `maxTokens`.
- Wiring `maxTokens` to `ContextManager.max_tokens` would collapse the
  compression threshold (default 200K → small number) and cause runaway
  compression — explicitly forbidden.

### `session/set_mode`
Updates the corresponding session's `permission_engine.active_mode` (see
`agentao/permissions.py:208`). Per-session — must not affect other sessions.

### `session/list_models` (P3+)
Front end needs a way to refresh available models after `initialize`:

- Implement `session/list_models`, reusing `agent.list_available_models()` or
  the equivalent runtime helper.
- Response schema must match `initialize.availableModels` so the front end
  maintains a single schema.
- **Failure behavior must be pinned down** — when the remote provider lookup
  fails, choose **one** and document it:
  - return a JSON-RPC error, or
  - return cached / empty list with a warning field.

### Acceptance
- Before registration, `session/set_model` returns `Method not found`.
- After implementation, `session/set_model` updates model + encoding cache +
  context window correctly.
- A single `session/set_model` call carrying both `contextLength` and
  `maxTokens` writes them to `ContextManager.max_tokens` and
  `LLMClient.max_tokens` respectively, with no cross-pollination.
- A `session/set_model` call carrying only `model` does not reset existing
  token configuration.
- `session/set_mode` on session A does not change session B's `active_mode`.
- `session/list_models` returns a payload whose schema matches
  `initialize.availableModels`; simulated provider failure follows the
  documented behavior (error vs. cached/empty + warning).

---

## File reference

- `agentao/tools/shell.py:272` — Windows background `Popen`
- `agentao/tools/shell.py:288` — POSIX background `Popen`
- `agentao/tools/shell.py:323` — foreground `Popen`
- `agentao/llm/client.py:147` — offending `pkg_logger.handlers.clear()`
- `agentao/acp/server.py:252-258` — stderr `StreamHandler` install
- `agentao/acp/__main__.py:67-71` — current handler registration list
- `agentao/acp/protocol.py:48-54` — current `METHOD_*` constants
- `agentao/runtime/model.py:51` — `set_model` runtime helper
- `agentao/context_manager.py:78` — `ContextManager.max_tokens` (window)
- `agentao/permissions.py:208` — `active_mode` for `set_mode`
