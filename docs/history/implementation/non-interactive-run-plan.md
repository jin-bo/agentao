# Non-Interactive `agentao run` — Design Plan

**Date:** 2026-05-08
**Status:** Draft (M0 scope)

---

## TL;DR

Add an `agentao run` subcommand that:

1. accepts a structured run spec from stdin or `--spec <file>`,
2. merges it with explicit CLI flags,
3. executes one Agentao turn,
4. emits a machine-readable result.

```bash
agentao run --format json < task.yaml
agentao run --spec task.yaml --format text
agentao run --prompt "Summarize this directory" --format json
```

`agentao run` is the automation surface. The interactive REPL and `agentao -p`
remain. After this work, `-p` is reimplemented as a thin shim over the same
pipeline so they share one exit-code table.

---

## Why This Exists

`agentao -p` is intentionally simple. For automation it falls short:

- runtime settings are split across flags, env vars, cwd, and CLI defaults;
- stdin is treated as prompt text, not a structured task object;
- callers have to parse natural-language output to recover status, replay path,
  token usage, or failure reason;
- CI cannot reuse one task template with small per-run overrides.

`agentao run` provides a stable contract for this case:

```text
run spec + CLI overrides → one Agentao turn → structured result
```

---

## Scope

**M0 (this document):** subcommand, spec loader, merge rules, `text`/`json`
output, exit codes, non-interactive abort path, `-p` shim, basic tests.

**Post-MVP (tracked separately, not in M0):**

- `--format jsonl` live event stream + new `RunLifecycleEvent`
- `attachments:` field
- `provider:` selector (multi-provider env-var prefixes)
- `plugins:` per-run extra dirs (concurrent-run isolation)
- SIGINT-precise JSONL termination
- JSON Schema snapshots
- Session resume

The M0 cut keeps the surface small enough to ship in 1–2 PRs without breaking
any existing transport or engine signature.

---

## Non-Goals

- No general YAML/JSON stdin on every Agentao subcommand.
- No GJSON/JQ-style transform language.
- No OpenAPI-style flag metadata.
- No exposing internal replay/debug payloads as the run-result schema.
- No interactive approvals from `agentao run`. Failures are explicit.
- No `approve_all` shortcut. Full bypass uses
  `permission_mode: full-access` — honest and audit-visible.
- No parallel permission matcher. Spec-level rules reuse `PermissionEngine`.
- No new `Transport` exception types or `confirm_tool` signature changes.
  The non-interactive abort path is built from existing primitives
  (`CancellationToken` and the synchronous
  `EventStream.add_observer(...)` accessed via two new thin
  pass-throughs `Agent.add_event_observer` / `remove_event_observer`);
  see Design Decisions.
- No replacement for ACP. ACP is the long-lived protocol surface;
  `agentao run` is a one-shot local process surface.

---

## User-Facing Shape

### Spec via stdin

```bash
agentao run --format json < task.yaml
```

```yaml
prompt: "Review this repository for obvious test failures."
permission_mode: read-only
model: gpt-5.4
max_iterations: 8
skills:
  - code-review
replay: true
```

### Override spec values with flags

```bash
agentao run \
  --model gpt-5.5 \
  --permission-mode workspace-write \
  --format json \
  < task.yaml
```

Rule:

```text
effective spec = defaults + stdin/file spec + explicit CLI flags
```

Only flags **explicitly provided** by the user override the spec. Argparse
defaults must not erase fields from the spec.

### Spec from file

```bash
agentao run --spec .agentao/tasks/review.yaml --format json
```

If `--spec` and piped stdin are both provided, fail with a clear error
(exit `2`). One structured spec source per run.

### Inline prompt convenience

```bash
agentao run --prompt "Summarize the current directory" --format json
```

Useful when callers want structured output but no YAML file. Does not replace
`agentao -p`; it just shares the result contract.

---

## M0 Run Spec

```yaml
prompt: string
cwd: string
model: string
base_url: string
permission_mode: read-only | workspace-write | full-access | plan
interaction_policy: reject       # M0: only "reject" is accepted
permissions:
  allow:
    - tool: string               # glob — same syntax as user-scope rules
      args: { ... }              # optional arg-pattern map
      domain:                    # optional URL/domain matcher
        url_arg: string
        allowlist: [string]
        blocklist: [string]
  deny:
    - tool: string
      args: { ... }
      domain: { ... }
max_iterations: int
skills:
  - string
replay: boolean
output:
  format: text | json
```

### Field notes

- `prompt`: required unless `--prompt` is provided.
- `cwd`: working directory for the run. Relative paths resolve against the
  process cwd. Maps to `Agent.working_directory`. Returned in `RunResult.cwd`
  as the resolved absolute path.
- `model`, `base_url`: override env-derived LLM settings for this run only.
  Forwarded via `**overrides` to `build_from_environment(...)`. Secrets
  (`api_key`) are **never** accepted in the spec — they stay in the
  environment or in a host-injected client.
- `permission_mode`: existing Agentao modes
  (`agentao/permissions.py:71-76`). Default matches the interactive CLI
  (`workspace-write`). CI examples should set `read-only` explicitly.
- `interaction_policy`: M0 accepts only `reject`. ASK without an
  `permissions.allow` match → `permission_required` (exit `3`).
  `ask_user(...)` → `interaction_required` (exit `3`). Default `reject` is
  applied when the field is omitted. Future values reserved.
- `permissions.allow` / `permissions.deny`: injected into the existing
  `PermissionEngine` for this run only. Same matcher and rule shape as
  `~/.agentao/permissions.json` (see `agentao/permissions.py::_matches`).
  See **Permissions** under Design Decisions for priority and provenance.
- `max_iterations`: existing chat-loop cap. Defaults to `100`
  (`Agent.chat(max_iterations=100)`) when neither spec nor flag sets it.
- `skills`: names to activate before the turn. Append to discovered active
  skills, do not replace. Missing skills fail the run before the turn starts.
- `replay`: enables `ReplayManager` for this run.
- `output.format`: spec-level default for output, overridden by `--format`.

Unknown spec fields fail by default (`extra="forbid"`). A future
`--ignore-unknown` can be added if cross-version compatibility becomes a real
need.

---

## Merge Rules

Precedence:

1. Built-in defaults.
2. Spec from `--spec` or stdin.
3. Explicit CLI flags.

Rules:

- Scalars: later value replaces earlier value.
- Lists: explicit CLI list replaces spec list (e.g. repeated `--skill`
  replaces `skills:`).
- `null` in YAML/JSON clears an optional spec value.
- Unknown spec fields → exit `2`.

Implementation must track which flags were explicitly set; argparse defaults
do not count as user intent.

---

## Output Contract

### `--format text`

Final assistant text only on stdout. Diagnostics on stderr. This is the
closest analog to `agentao -p`, sharing the new pipeline.

### `--format json`

One JSON object after the run completes.

Success:

```json
{
  "status": "ok",
  "session_id": "session-id",
  "turn_id": "turn-id",
  "cwd": "/abs/path/to/project",
  "model": "gpt-5.5",
  "final_text": "The tests fail because ...",
  "replay_path": ".agentao/replays/session-id.jsonl",
  "usage": {
    "prompt_tokens": 12000,
    "completion_tokens": 900,
    "total_tokens": 12900
  },
  "tool_calls": 7,
  "warnings": []
}
```

Failure:

```json
{
  "status": "error",
  "session_id": "session-id",
  "turn_id": "turn-id",
  "cwd": "/abs/path/to/project",
  "model": "gpt-5.5",
  "error": {
    "type": "permission_required",
    "message": "run_shell_command requires approval in this mode",
    "tool_name": "run_shell_command",
    "tool_call_id": "call_01HX..."
  },
  "replay_path": ".agentao/replays/session-id.jsonl",
  "warnings": []
}
```

Error-envelope rules:

- `type` and `message` always present.
- `tool_name` present for `permission_required`, `permission_denied`,
  `interaction_required`. For `interaction_required` from `ask_user(...)`,
  `tool_name` is `"ask_user"`.
- `tool_call_id` present whenever the run reached the permission decision
  phase. Read from the `permission_decision` event the run pipeline
  already consumes via the sync observer registered with
  `Agent.add_event_observer(...)` (D1).
- **Raw `args` are never written to the structured output.** They may carry
  user data, paths, or partial secrets. Callers who need raw args read
  `replay_path`.

`cwd` and `model` are the resolved values used for the run, included for
auditability.

For `--format json`, stdout contains only the JSON object. Diagnostics go to
stderr.

`schema_version` is intentionally omitted from M0 to keep the result envelope
identical in shape to host events (which do not carry it). A future
`run-result-1.0.json` schema snapshot can introduce versioning then.

---

## Exit Codes

| Code | Meaning |
| ---- | ------- |
| `0`  | run completed successfully |
| `1`  | runtime error, provider error, or unexpected failure |
| `2`  | invalid CLI usage or invalid run spec (unknown field, malformed YAML/JSON, both `--spec` and stdin provided) |
| `3`  | permission or interaction required in non-interactive mode |
| `4`  | max iterations reached before a final answer |
| `130`| interrupted by SIGINT/SIGTERM (M0 deliberately collapses both onto `130` instead of the conventional `SIGINT=130` / `SIGTERM=143` split — non-interactive runs do not need to distinguish; one cancellation handler keeps the pipeline simpler) |

For exit `3`, `error.type` distinguishes:

- `permission_required` — runtime produced an ASK/prompt decision
  (engine ASK **or** `tool.requires_confirmation` fallback at
  `runtime/tool_planning.py:317-321`) and no `permissions.allow`
  matched.
- `permission_denied` — engine returned DENY (rule, preset, hardline,
  or read-only short-circuit). `error.matched_rule` is the projected
  matched rule when one matched. The serializer **omits the
  `matched_rule` key entirely whenever the underlying value is `None`**
  — this covers hardline denials (`permissions.py:432`) **and** the
  rule-less synthetic details produced by
  `runtime/tool_planning.py:298-302` (read-only short-circuit). Transport-
  level state may carry `None` internally, but the serializer must drop
  the key rather than emit `"matched_rule": null`. `error.message`
  always identifies the source via the standard reason prefix
  (`hardline:` / `mode-preset:` / `user-rule:` / `injected:run-spec:`).
  The read-only short-circuit reuses the existing `mode-preset:` family
  with the literal `mode-preset:read-only` — no new prefix category.
- `interaction_required` — `ask_user(...)` was called.

Structured JSON is emitted for all controlled runtime failures.
Invalid-usage failures may write only stderr.

`agentao -p` migrates onto this same table. Today `-p` returns `2` for
max-iterations (`cli/entrypoints.py:60`); after this change it returns `4`,
matching `agentao run`. **Breaking change**, called out in release notes.

---

## Design Decisions

This is the canonical specification of the parts that are easy to get wrong.
Phase 1 / 2 below reference this section by name; do not re-state.

### D1. Non-interactive abort path (no new transport API)

The run pipeline must abort when:

- the engine returns DENY for a tool the agent attempted, **or**
- the runtime produces an ASK/prompt decision (engine ASK **or**
  `tool.requires_confirmation` fallback) and no spec-level `allow`
  matched, **or**
- the agent calls `ask_user(...)`.

Today's transports (`NullTransport`, `SdkTransport`) auto-approve in non-
interactive contexts. Returning `False` from `confirm_tool` marks the plan
`CANCELLED` and the chat loop continues — wrong for automation.

**M0 mechanism (no new exception types, no `Transport` signature change):**

1. New `NonInteractiveTransport(SdkTransport)` lives in
   `agentao/transport/non_interactive.py`. It records rejections and the
   max-iterations flag on instance attributes, and **cancels a
   `CancellationToken` the run pipeline owns and passes into
   `agent.chat(cancellation_token=...)`** (the pipeline must construct the
   token explicitly — `runtime/turn.py:60` will otherwise create its own
   token that the transport cannot reach).

   ```python
   class NonInteractiveTransport(SdkTransport):
       def __init__(self):
           super().__init__()
           self.rejection: dict | None = None
           self.max_iterations_hit: bool = False
           self._cancel: Callable[[str], None] | None = None
           # FIFO populated by the pipeline event subscriber from
           # permission_decision events with outcome=="prompt"; consumed
           # by confirm_tool below to recover tool_call_id.
           self._ask_queue: list[tuple[str, str | None]] = []

       def bind_cancel(self, cancel_fn):
           self._cancel = cancel_fn

       def queue_ask(self, tool_name, tool_call_id):
           self._ask_queue.append((tool_name, tool_call_id))

       def confirm_tool(self, name, description, args):
           # ASK reaches here only if PermissionEngine did not short-circuit
           # to ALLOW (no spec-level allow matched). Pop the matching
           # queued ASK event by tool_name (FIFO matching is sufficient
           # because Phase 1 emits permission_decision in plan order and
           # Phase 2 walks plans in the same order — tool_runner.py:193-200).
           tool_call_id = None
           for i, (n, tcid) in enumerate(self._ask_queue):
               if n == name:
                   tool_call_id = tcid
                   del self._ask_queue[i]
                   break
           self.rejection = {
               "type": "permission_required",
               "tool_name": name,
               "tool_call_id": tool_call_id,
               "message": f"{name} requires approval in this mode",
           }
           if self._cancel:
               self._cancel(f"permission_required: {name}")
           return False  # Plan → CANCELLED; tool will not execute

       def ask_user(self, question):
           self.rejection = {
               "type": "interaction_required",
               "tool_name": "ask_user",
               "question": question,
               "message": "ask_user requires interaction in non-interactive mode",
           }
           if self._cancel:
               self._cancel("interaction_required: ask_user")
           return "[interaction_required]"

       def on_max_iterations(self, max_iterations, pending_tools):
           # Reuse the same Transport hook agentao -p uses today
           # (cli/entrypoints.py:25-37). Recording the flag lets the
           # pipeline map to exit 4 without inventing a MaxIterationsError.
           self.max_iterations_hit = True
           return {"action": "stop"}
   ```

2. The run pipeline registers a **synchronous observer** on the agent's
   host event stream and routes `permission_decision` events by
   `event.outcome`. `Agent.events()` is async (it returns an async
   iterator), which a sync CLI cannot drive without a background event
   loop; `EventStream.add_observer(callback)` fires the callback inline
   on the producer thread and is the right primitive for M0.

   This work adds two thin pass-throughs on `Agent` so the pipeline does
   not reach into a private attribute:

   ```python
   # agentao/agent.py
   def add_event_observer(self, callback):
       return self._host_events.add_observer(callback)

   def remove_event_observer(self, callback):
       return self._host_events.remove_observer(callback)
   ```

   The pipeline observer:

   - `"deny"` — record `transport.rejection` and cancel the token:

     ```python
     transport.rejection = {
         "type": "permission_denied",
         "tool_name": event.tool_name,
         "tool_call_id": event.tool_call_id,
         "matched_rule": event.matched_rule,  # may be None for hardline
         "message": event.reason,             # already source-prefixed
     }
     cancel_fn(f"permission_denied: {event.tool_name}")
     ```

     `event.reason` is the source-tagged string the runtime already
     produces (`hardline:<desc>` / `mode-preset:<tool>` /
     `user-rule:<tool>` / `injected:run-spec:<tool>` —
     `permissions.py:419-421`; the read-only short-circuit at
     `runtime/tool_planning.py:298-302` synthesizes one in the same
     `mode-preset:` family). Saving it here lets the JSON serializer
     emit it verbatim as `error.message`, satisfying the Output Contract
     prefix requirement without re-deriving it from `matched_rule` (which
     is itself absent for hardline and the read-only short-circuit).

   - `"prompt"` — push onto the transport's ASK FIFO so `confirm_tool` can
     recover the id when it fires:

     ```python
     transport.queue_ask(event.tool_name, event.tool_call_id)
     ```

   - `"allow"` — no action.

   The runner emits one `permission_decision` event per plan **before**
   Phase 2 (`tool_runner.py:193-195`), and `add_observer` callbacks fire
   synchronously inside `publish()` (`host/events.py:205-217`), so the
   FIFO is fully populated before any `confirm_tool` invocation in the
   same batch. The pipeline does not need a new field on `ToolCallPlan`.
   The observer must be cheap and non-blocking — it runs on the producer
   thread; raising is logged and discarded by `EventStream` so a broken
   sink cannot take down the runtime.

   **Emit-gate fix (required for observer-only consumers).** The runner
   currently skips `permission_decision` emission when no async
   subscriber is attached: `tool_runner.py:238-256` calls
   `EventStream._has_subscribers()` (`host/events.py:396`), which counts
   only `_subscribers` (the async iterators), not `_observers`. With the
   M0 pipeline registering only a sync observer, the gate evaluates
   false and **no event is ever emitted** — DENY would never reach the
   pipeline and the ASK FIFO would stay empty.

   Minimal fix (Phase 1, no new public surface):

   - Add a sibling `_has_listeners()` method on `EventStream` that
     returns `True` when either an async subscriber **or** a sync
     observer is attached (~3 LOC).
   - Change `ToolRunner._should_emit_permission_events()` to call
     `_has_listeners()` instead of `_has_subscribers()` (~1 LOC). The
     existing fallback ("no introspection available → emit anyway")
     is preserved.
   - Keep `_has_subscribers()` and its existing tests untouched —
     `host/events.py:396` is documented as a test hook for async
     subscriber state and other callers may depend on that narrower
     semantic.

3. After `agent.chat(...)` returns, the pipeline classifies by reading
   three pieces of state. **It does not catch `AgentCancelledError` or
   `KeyboardInterrupt`** — `runtime/turn.py:97-109` already swallows both
   and returns sentinel text, so the pipeline cannot rely on those
   propagating, and an outer `try/except` would never fire.

   Classification (in order):
   - `transport.rejection` is set → exit `3` with the matching `error.type`
     (`permission_required` / `permission_denied` / `interaction_required`).
   - `transport.max_iterations_hit` is set → exit `4`.
   - `token.is_cancelled` and `transport.rejection is None` → exit `130`
     (SIGINT path; the SIGINT handler installed by the pipeline calls
     `token.cancel("sigint")`, and the chat loop's existing
     `KeyboardInterrupt` catcher then propagates that cancel through
     `token.cancel("user-cancel")` — either reason value is acceptable
     evidence).
   - otherwise → exit `0` with the returned `final_text`.

   Generic exceptions raised from `chat()` (provider error, etc.) are
   re-raised by `runtime/turn.py:110-113` and caught at the entrypoint as
   exit `1`.

**What this avoids vs. the earlier design:**

- No new sentinel exception types (`PermissionRequired` / `PermissionDenied` /
  `InteractionRequired`).
- No `Transport.confirm_tool` signature change → no breakage at six in-tree
  call sites (`base.py`, `sdk.py`, `null.py`, `replay/adapter.py`,
  `acp/transport.py`, `cli/app.py`).
- No new `Transport.on_permission_decision` hook on the abstract base.
- No new `MaxIterationsError` — the existing `on_max_iterations` Transport
  hook is reused exactly as `agentao -p` already uses it.
- The chat loop's existing `CancellationToken` mechanism is reused — the
  same one SIGINT already uses.

**Trade-off:** when DENY occurs, the existing runtime still produces one
"cancelled by user" tool result before the chat loop notices the cancellation
on the next iteration. That's identical to current SIGINT behavior; the
final `RunResult` is unaffected.

### D2. Permission rule injection (deny-only pre-check tier)

**M0 trust model:** a task file (run spec) can always **restrict** what
the agent may do (spec `deny` is unconditional). Spec `allow` is
additive — it joins the standard user-rule list and follows the
existing `PermissionEngine` mode semantics. M0 deliberately does **not**
introduce a new priority tier for spec allow, and does **not** alter
the engine's existing per-mode source ordering.

This keeps the change surface small (one new pre-check, one extension
of the user list) and preserves the existing per-mode contract — most
importantly, `permission_mode: full-access` continues to mean full
access (the engine evaluates preset rules before user rules in
`full-access` / `plan`; `permissions.py:442-451`). Anything stronger
would be a redesign of the permission model and is out of scope for M0.

`PermissionEngine` gains one new list and one method:

```python
def add_run_rules(
    self,
    *,
    allow: list[dict],   # already converted by RunPermissionRule.to_engine_dict("allow")
    deny: list[dict],    # already converted by RunPermissionRule.to_engine_dict("deny")
    source: str = "run-spec",
) -> None:
    # Spec deny: a single new pre-check tier evaluated AFTER hardline
    # but BEFORE any other source, in every mode. Required because the
    # engine is first-match-wins per source list (permissions.py:452-462)
    # and spec deny appended to user rules could be shadowed by a
    # pre-existing allow:* (under read-only / workspace-write) or by a
    # preset allow:* (under full-access / plan).
    self._run_scope_rules.extend(deny)
    # Spec allow: appended to the standard user-rule list. Existing
    # ordering applies: user rules before preset (read-only,
    # workspace-write) or preset before user rules (full-access, plan).
    # No new priority tier, no change to existing mode semantics.
    self.rules.extend(allow)
    self.add_loaded_source(f"injected:{source}")  # provenance + cache bust
```

`decide_detail` evaluation order:

```text
hardline → spec deny (new pre-check) → existing PermissionEngine ordering
```

The "existing ordering" remains exactly what `permissions.py:442-451`
already does: preset-then-user under `full-access` / `plan`,
user-then-preset under `read-only` / `workspace-write`.

Practical consequences (worth keeping in mind when authoring tests):

- Under `read-only` and `workspace-write`, an existing user/project
  `deny` that lands earlier in the user-rule list still wins over a
  spec `allow` for the same tool. The spec cannot relax a standing
  user/project restriction.
- Under `full-access` and `plan`, the preset still wins over any user
  rule (including spec allow). Spec deny still takes effect because it
  runs before any preset, which is the whole reason the new pre-check
  tier exists.

On a `_run_scope_rules` match the reason string is
`"injected:run-spec:<tool>"`; on a spec-allow match within the user
tier, the reason follows the standard `user-rule:<tool>` prefix.
Provenance for both lives in `loaded_sources` (one shared
`"injected:run-spec"` label per run).

`active_permissions().rules` projection prepends `_run_scope_rules` in
every mode so the snapshot order mirrors `decide_detail`'s evaluation
order (invariant at `permissions.py:525-527`). `matched_rule` projection
(`host/projection.py:project_matched_rule`) needs no change — `action`
is already a normal field on engine rules.

**Action injection lives in exactly one place:**
`RunPermissionRule.to_engine_dict(action="allow"|"deny")` produces
already-normalized engine dicts. `add_run_rules` just appends —
it does not synthesize the `action` field. `action` is therefore
never authored by spec writers (`extra="forbid"` on `RunPermissionRule`
catches `action:` in YAML).

### D3. Transport must be constructor-injected

`Agentao.__init__` passes `self.transport` into `ToolRunner`, which stores it
on `ToolExecutor` and `ToolResultFormatter` (`agent.py:404`). A post-
construct `agent.transport = NonInteractiveTransport(...)` would not reach
those held references.

The run pipeline therefore constructs `NonInteractiveTransport` first and
forwards it via `build_from_environment(transport=...)` (the factory already
accepts `transport=` in `**overrides`, `embedding/factory.py:215-222`).
After construction, the pipeline binds the cancel function:
`transport.bind_cancel(token.cancel)`.

### D4. `agentao -p` is a thin shim

`agentao -p <text>` ≡ `agentao run --format text --prompt <text>`. The
current `run_print_mode` body becomes a stub that calls into the same
`cli.run.execute(...)` entry point. Exit-code unification follows
automatically — no parallel max-iterations branch.

### D5. Plugins, skills, replay, sessions

- Plugins discovered the way the interactive CLI does today; no per-run
  `extra_dirs` injection in M0 (Post-MVP).
- Skills: spec `skills:` activates before the turn. Missing skill → fail
  before chat begins.
- Replay: **`spec.replay` is authoritative for the run.** The pipeline
  always passes an explicit `replay_config=ReplayConfig(enabled=spec.replay)`
  into `build_from_environment(...)` so the factory's disk auto-load
  (`factory.py:194-199`, which runs whenever `replay_config` is absent
  from `**overrides`) is bypassed. Otherwise a `replay: false` run could
  silently inherit a project/env replay config — surprising and contrary
  to the spec contract.
- Sessions: each run is fresh. Resume is Post-MVP.

---

## Implementation Plan

Two-phase rollout. Each phase is independently shippable and reviewable.

### Phase 1 — Models, parser, transport, engine extension

- New module `agentao/cli/run_models.py`:
  - `RunSpec`, `RunOutputOptions`, `RunPermissionRule`,
    `RunPermissionDomainRule`, `RunResult`.
  - `RunSpec` and rule models: `extra="forbid"`. `RunResult`:
    `extra="ignore"` (forward-compat).
  - `RunPermissionRule.to_engine_dict(action)` converts to engine dict
    shape; `action` injected here, never authored by users.
- New module `agentao/transport/non_interactive.py`:
  - `NonInteractiveTransport(SdkTransport)` per D1 (~30 LOC).
  - No changes to `Transport` base, no changes to existing transports, no
    new exception types.
- `PermissionEngine.add_run_rules(...)` per D2 (~10 LOC + first-match-wins
  walk extended to check `_run_scope_rules` first in every mode).
- `Agent.add_event_observer` / `remove_event_observer` thin pass-throughs
  to `EventStream.add_observer/remove_observer` per D1 step 2 (~6 LOC).
- `EventStream._has_listeners()` + one-line update to
  `ToolRunner._should_emit_permission_events()` per D1 step 2's
  emit-gate fix (~4 LOC; required so observer-only consumers actually
  receive `permission_decision` events).
- One-line reason-string fix in `runtime/tool_planning.py:298-302`:
  change the read-only short-circuit's synthetic reason from
  `"readonly mode blocks non-read-only tools"` to
  `"mode-preset:read-only"` so it joins the existing `mode-preset:`
  prefix family the Output Contract requires for `error.message`.
  No new category, no new model.

### Phase 2 — CLI subcommand and pipeline

- New module `agentao/cli/run.py`:
  - argparse subparser: `--spec`, `--prompt`, `--format`, `--model`,
    `--base-url`, `--permission-mode`, `--interaction-policy`,
    `--max-iterations`, `--skill`, `--replay`.
  - YAML/JSON loader with strict unknown-field validation.
  - Explicit-flag tracking for argparse merge rules.
  - Pipeline:
    1. Parse spec, merge CLI overrides, validate `RunSpec`.
    2. Construct `NonInteractiveTransport()`.
    3. Resolve `cwd` (absolute).
    4. `agent = build_from_environment(working_directory=cwd,
       transport=transport, model=spec.model, base_url=spec.base_url,
       replay_config=ReplayConfig(enabled=spec.replay), ...)`. The
       explicit `replay_config=` is required to suppress the factory's
       disk auto-load — see D5.
    5. Synchronize permission mode across both runtime sites the
       interactive CLI already touches (`cli/app.py:125-133`):
       ```python
       engine.set_mode(spec.permission_mode)
       agent.tool_runner.set_readonly_mode(
           spec.permission_mode == PermissionMode.READ_ONLY
       )
       ```
       `engine.set_mode("read-only")` alone is **not** sufficient —
       `_PRESET_RULES["read-only"]` is empty by design
       (`permissions.py:153`); read-only enforcement actually lives in
       `ToolRunner` via the `readonly_mode` flag checked at
       `runtime/tool_planning.py:298`. Skipping the second call leaves
       a `permission_mode: read-only` run silently writable.
    6. Convert spec rules via `RunPermissionRule.to_engine_dict("allow"|"deny")`,
       then `engine.add_run_rules(allow=..., deny=..., source="run-spec")`
       (already-normalized dicts; D2).
    7. (Replay attachment is now handled inside `build_from_environment`
       via the explicit `replay_config=` above.)
    8. Activate `spec.skills`; fail if any missing.
    9. **Construct the cancellation token explicitly:**
       `token = CancellationToken()`. `runtime/turn.py:60` falls back to
       an internal token when `cancellation_token=` is omitted, and that
       internal token is unreachable from the transport — so the pipeline
       must own it.
    10. `transport.bind_cancel(token.cancel)`.
    11. Install a SIGINT/SIGTERM handler that calls
        `token.cancel("sigint")` so user interrupts route through the
        same cancellation surface as permission/interaction rejections.
    12. Register a sync observer via
        `agent.add_event_observer(callback)` — route `permission_decision`
        events by `outcome` per D1 step 2 (`deny` sets rejection +
        cancels; `prompt` queues onto `transport.queue_ask(...)` for
        ASK `tool_call_id` recovery; `allow` is a no-op). Detach with
        `remove_event_observer` after `chat()` returns.
    13. Snapshot LLM token totals (`llm/client.py:498`) for per-run delta.
    14. `agent.chat(spec.prompt, max_iterations=spec.max_iterations,
        cancellation_token=token)`. Do **not** wrap in a try/except for
        `AgentCancelledError` or `KeyboardInterrupt` — `runtime/turn.py:97-109`
        already swallows both and returns sentinel text.
    15. Classify the run by reading `transport.rejection`,
        `transport.max_iterations_hit`, and `token.is_cancelled` per D1
        step 3; build `RunResult`, compute `usage` delta, serialize per
        `--format`, map exit code (`0` / `3` / `4` / `130`). Catch
        generic exceptions from `chat()` at the entrypoint as exit `1`.
- Reimplement `agentao -p` as a shim per D4. Document the `2 → 4`
  max-iterations exit-code change in release notes.

---

## Test Matrix

Minimum coverage:

- YAML spec loads.
- JSON spec loads.
- Invalid YAML/JSON exits `2`.
- Unknown field exits `2`.
- `--spec` and piped stdin together exit `2`.
- CLI scalar flag overrides spec scalar.
- Repeated `--skill` overrides spec `skills`.
- `--format json` writes valid JSON to stdout only (stderr-clean assertion).
- `--format text` writes final text only.
- ASK without matching `permissions.allow` exits `3` with
  `error.type="permission_required"`, `error.tool_name` set,
  `error.message` present (matches `"<tool> requires approval in this
  mode"`), and `error.tool_call_id` matching the `permission_decision`
  event id on the agent's public event stream. Assert raw `args` are
  absent from the error envelope.
- DENY decision (spec `permissions.deny` matching the call) exits `3` with
  `error.type="permission_denied"`, `error.matched_rule` set, and **no**
  `tool_lifecycle phase="started"` event for that `tool_call_id`.
- `ask_user(...)` invocation exits `3` with
  `error.type="interaction_required"` and `error.message` present
  (matches `"ask_user requires interaction in non-interactive mode"`).
- Unknown `interaction_policy` value (e.g., `approve_all`) exits `2`.
- Spec `permissions.deny` blocks a tool under `workspace-write` when
  `~/.agentao/permissions.json` already contains an `allow:*` (regression
  guard for `_run_scope_rules` ordering — without it the user `allow:*`
  would shadow the spec deny).
- Spec `permissions.deny` blocks a tool under `full-access` (regression
  guard against the preset `allow:*` shadowing the spec deny).
- Under `read-only` / `workspace-write`, spec `permissions.allow` does
  **not** override a pre-existing user `deny` for the same tool that
  appears earlier in the user-rule list (regression guard for the
  additive-only semantic — spec allow joins the user list and follows
  first-match-wins).
- Under `read-only` / `workspace-write`, spec `permissions.allow`
  grants permission for a tool the standing user policy does not
  address (positive coverage; assert via the `user-rule:<tool>` reason
  prefix).
- Under `full-access`, `permission_mode: full-access` still means full
  access — spec `permissions.allow` is observed in the active rule
  snapshot, but the preset `allow:*` matches first per existing engine
  ordering (regression guard against accidentally re-ordering preset vs
  user rules in `full-access` / `plan`).
- `add_run_rules(...)` deny path: emits `permission_decision` with
  `reason="injected:run-spec:<tool>"`, `loaded_sources` includes
  `"injected:run-spec"`, and `engine.active_permissions().rules[0]` is
  the injected deny rule.
- Hardline-denied tool: `permission_denied` envelope **omits**
  `error.matched_rule` entirely (assert the key is absent from the
  serialized JSON, not present-with-`null`) and `error.message` starts
  with `hardline:` (regression guard for the matched_rule-omit-on-
  hardline contract and for the deny observer correctly carrying
  `event.reason` through to the envelope).
- Spec-deny prefix carry-through: a tool blocked by spec
  `permissions.deny` produces `error.message` starting with
  `injected:run-spec:` (regression guard for the same path under a
  non-hardline source).
- `permission_mode: read-only` blocks a non-read-only tool even when
  no user/preset rule matches (regression guard for the
  `set_readonly_mode(True)` synchronization in Phase 2 step 5 — without
  the second call, read-only enforcement at
  `runtime/tool_planning.py:298` would not kick in and the run could
  silently mutate the workspace). Additionally assert
  `error.message == "mode-preset:read-only"` and that
  `error.matched_rule` key is **absent** from the serialized envelope
  (regression guard for the synthetic-detail reason fix and the
  serializer's `matched_rule is None → omit key` rule).
- Observer-only emit-gate: with the run pipeline registering **only**
  a sync observer (no async `Agent.events()` subscriber attached), a
  tool-call attempt that would resolve DENY still produces a
  `permission_decision` event the observer receives (regression guard
  for `_has_listeners()` — without the fix, `_has_subscribers()`
  returns false, no event is emitted, and the pipeline would silently
  miss the DENY).
- `replay: false` in the spec runs with no replay output even when a
  project `.agentao/replay.toml` enables replay (regression guard for
  the explicit `replay_config=` injection in Phase 2 — without it the
  factory's disk auto-load would still attach a manager).
- ASK `tool_call_id` correlation: a batch with two ASK plans for the
  same tool name is correctly mapped — the first `confirm_tool` call
  records the first plan's `tool_call_id`, not the second's. (Confirms
  the FIFO queueing in D1 works for batches with duplicates.)
- Constructor-injected `NonInteractiveTransport` reaches the tool execution
  path: trigger an ASK and assert `NonInteractiveTransport.confirm_tool`
  was invoked (not `SdkTransport.confirm_tool`).
- `permissions.allow` `args` pattern that does not match the actual call
  arguments does **not** auto-approve.
- Max-iterations failure exits `4` (assert via the new `on_max_iterations`
  flag on `NonInteractiveTransport`, not via a raised exception).
- SIGINT during chat exits `130` (assert `token.is_cancelled` is true and
  `transport.rejection is None` after `agent.chat()` returns; do **not**
  assert a `KeyboardInterrupt` propagates out — `runtime/turn.py:97-103`
  swallows it).
- Missing requested skill fails before chat starts.
- Replay-enabled result includes `replay_path`.
- `agentao -p` returns `4` (not `2`) when max iterations are hit
  (regression guard for the exit-code unification).

---

## Open Questions

- M0 emits no `RunLifecycleEvent`; if Post-MVP adds JSONL streaming, should
  the host event base gain an optional `schema_version` to keep
  `RunLifecycleEvent` consistent with the existing host event family, or
  should `RunLifecycleEvent` be the lone exception?

---

## Recommended M0 Decisions

- **Permission default:** `workspace-write` (matches interactive CLI). CI
  examples should still set `read-only` explicitly.
- **Interaction policy default:** `reject`. No `approve_all`.
- **Permission rules (M0 trust model):** spec-level `allow`/`deny` reuse
  the existing engine matcher. Spec `deny` goes into a dedicated
  `_run_scope_rules` pre-check tier evaluated after hardline but before
  any other source in every mode — safety guards are unconditional.
  Spec `allow` is **additive**: appended to the standard user-rule
  list, following the engine's existing per-mode ordering
  (`permissions.py:442-451`). M0 introduces **no** new tier for spec
  allow and does **not** alter the existing per-mode source ordering —
  `permission_mode: full-access` still means full access (D2).
- **`cwd` policy:** allowed to point anywhere (including outside the
  original process cwd). The user's explicit `cwd` choice **is** the
  workspace boundary they're declaring for this run; `permission_mode`
  rules apply within it. This matches the interactive CLI, where the
  user can already `cd` anywhere before launching `agentao`. The
  resolved absolute path is included in `RunResult.cwd` for auditability.
- **Transport:** `NonInteractiveTransport` constructor-injected (D3).
  No new sentinel exceptions, no `Transport.confirm_tool` signature change,
  no new `on_permission_decision` hook on the base. Abort path uses the
  existing `CancellationToken` and the sync
  `EventStream.add_observer(...)` accessed via the new thin
  `Agent.add_event_observer` pass-through (D1).
- **Output:** `text` and `json` only in M0. JSONL streaming Post-MVP.
- **Exit codes:** shared with `-p`, which becomes a thin shim (D4).
- **Skills:** append-activate, do not replace.
- **Replay:** `spec.replay` is authoritative for the run. Pipeline
  always passes explicit `replay_config=ReplayConfig(enabled=spec.replay)`
  to bypass the factory's disk auto-load (D5). Summary path returned
  in `RunResult.replay_path`.
- **Secrets:** never accepted in the spec.
- **Sessions:** fresh-only.
