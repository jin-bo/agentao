# Codex Reverse Review for Agentao

Date: 2026-05-12

This note records the reverse review of the latest Codex changes against
Agentao's current architecture. The goal is to identify what Agentao actually
needs, not to mirror Codex's product shape.

## 2026-05-17 Follow-up: Diagnostics, Not Product Weight

A later review against the 2026-05-17 Codex pull refined the conclusion. The
important correction is that most of the apparent Codex-inspired work is either
already present in Agentao or too early for Agentao's current scale.

The current actionable scope is deliberately small:

1. Add `agentao doctor --json`.
2. Add `agentao config validate`.

Both should be small wiring PRs over existing signals. They should not become a
new app-server diagnostics subsystem, a stricter runtime startup path, or a
plugin marketplace project.

### Verified Current State

This section records the code-grounded checks that drive the narrowed scope.

| Area | Current Agentao state |
|------|-----------------------|
| `doctor` / `config validate` CLI | Missing from `agentao/cli/`; this is a real gap. |
| Factory error handling | `agentao/embedding/factory.py` intentionally has best-effort paths that swallow load/open errors. These should remain safe at runtime but become visible through validation. |
| Permission snapshot | Already shipped. `Agentao.active_permissions()` returns `ActivePermissions(mode, rules, loaded_sources)`, backed by `PermissionEngine.active_permissions()` and `agentao/host/models.py`. No permission-profile rewrite is needed. |
| Correlation IDs | Partially present. Host projection and replay already carry `session_id`, `turn_id`, `tool_call_id`, and `call_id`. Cross LLM/tool/MCP/ACP trace stitching is not a current P0. |
| Plugin diagnostics | Already has a base in `agentao/embedding/plugins/diagnostics.py` via `PluginDiagnostics` and `build_diagnostics()`. `doctor` should reuse it. |
| Tool surface | Small enough that `ToolOutput` artifact/visibility restructuring is not justified without a real consumer. |

### P0: `agentao doctor --json`

Status — implemented 2026-05-16.

Goal: aggregate existing health signals into one operator-facing command.

This command should expose what Agentao already knows:

- plugin diagnostics from `PluginDiagnostics` / `build_diagnostics()`;
- active permission snapshot from `active_permissions()`;
- replay schema version / replay configuration visibility;
- ACP schema export status;
- config/factory source and validation errors;
- optional-dependency availability only where the feature is configured or
  obviously requested.

Non-goals:

- no remote network probes;
- no marketplace or plugin sharing behavior;
- no new long-running daemon inspection protocol;
- no new strict startup semantics;
- no broad "system audit" beyond Agentao-owned inputs.

Output contract:

- default human-readable output can be added, but `--json` is mandatory for
  host/CI usage;
- diagnostics should be redaction-safe by default;
- warnings and errors should include the source path when available.

### P0: `agentao config validate`

Status — implemented 2026-05-16.

Goal: make configuration problems visible without changing runtime startup
semantics.

The factory path stays best-effort because Agentao is embedded-host friendly:
bad optional config, unreadable user memory, or malformed settings should not
unexpectedly break hosts that inject their own subsystems. Validation is a
separate explicit command that reports those failures.

Minimum scope:

- validate `.agentao/settings.json` shape and JSON parse errors;
- validate environment-derived provider fields where relevant;
- report permission config source parse/load errors;
- report MCP config parse/load errors;
- report replay config parse/load errors;
- report SQLite open failures for project/user memory stores as validation
  findings instead of silent behavior.

Non-goals:

- no strict-on-startup mode;
- no Codex-style layered `profile-v2`;
- no migration of embedded constructor behavior;
- no broad schema redesign.

### Implementation notes (both commands, landed 2026-05-16)

- Both handlers live in ``agentao/cli/diagnostics_cli.py`` and are wired
  through the lazy-import surface in ``agentao/cli/__init__.py``; the
  ``[cli]`` missing-dep guard in :func:`agentao.cli.entrypoint` still applies
  to them as it does to ``plugin list`` and ``skill list``.
- Neither command instantiates an :class:`Agentao`; both work off the same
  on-disk signals the factory consults (``settings.json``, ``permissions.json``,
  ``mcp.json``, replay block, memory DB probes) plus
  :func:`agentao.embedding.plugins.diagnostics.build_diagnostics` for plugins.
- Output is redaction-safe by design: the provider section reports
  ``api_key_present`` (a boolean) and the model/base URL, but never the key
  value itself; ``LLM_TEMPERATURE`` / ``LLM_MAX_TOKENS`` values are echoed only
  in their raw form when they fail to parse, since those are not secrets.
- ``--json`` returns ``{"ok": bool, "sections": {...}, "findings": [...]}``;
  ``ok`` is ``False`` exactly when at least one finding has ``level == "error"``,
  and that case maps to ``exit 1``. Warnings keep ``ok = True`` so a missing
  API key (a normal fresh-clone state) never flunks CI.
- ``config validate`` deliberately omits the ``plugins`` section — plugin
  diagnostics already have a dedicated CLI (``agentao plugin list``) and a
  doctor section, so duplicating them here would just blur the "config" scope.
- Tests live in ``tests/test_diagnostics_cli.py`` (23 cases). Each test
  monkeypatches ``Path.home`` to ``tmp_path`` so the developer's real
  ``~/.agentao`` is not touched.

### Explicitly Not Doing

These are excluded until a real consumer or bug report creates pressure:

- **Permission profile rewrite.** Agentao already has the lightweight
  `ActivePermissions` snapshot it needs today.
- **Structured `ToolOutput` overhaul.** The current tool count and consumers do
  not justify artifact/visibility metadata across all tools.
- **Large trace ID refactor.** Existing `session_id` / `turn_id` /
  `tool_call_id` / `call_id` covers most debugging. Add one cross-layer field
  later only if host integrations need LLM-to-MCP stitching.
- **Plugin marketplace / share checkout.** The plugin system should stabilize
  locally before distribution is expanded.
- **TUI-style large module split.** Agentao's CLI is already split across
  `app`, `input_loop`, `session`, and `subcommands`; Codex's TUI refactor was
  debt repayment for a much larger UI.

## Summary

Codex's recent architecture work is mostly driven by Codex-specific scale:
app-server, remote environments, multiple workspaces, Windows enterprise
sandboxing, and a broader extension surface. Agentao should not copy that
weight by default.

For Agentao, the immediate gaps are narrower:

1. Make `PreToolUse` hooks minimally decision-capable instead of
   fire-and-forget.
2. Add a small host-facing telemetry increment for model latency, first token
   latency, and turn-level aggregates.

The host contract is already mostly done and should not be treated as a fresh
P0 project.

## Current State Check

### Host Contract

Status: mostly complete.

Evidence:

- `agentao/host/models.py` defines the public lifecycle models:
  `ToolLifecycleEvent`, `SubagentLifecycleEvent`, and
  `PermissionDecisionEvent`.
- `turn_id` and `tool_call_id` are already present on the public event surface.
- `docs/schema/host.events.v1.json` and `docs/schema/host.acp.v1.json` are
  checked-in schema snapshots.
- Host schema tests already assert snapshot compatibility.

Remaining work is small: keep public API signature tests and schema snapshots
tight. This is maintenance, not a P0 architecture gap.

### PreToolUse Hooks

Status: real gap — **resolved 2026-05-12** (see "Status — implemented" under
P0 below). The description below is the pre-fix state, kept for context.

`agentao/runtime/tool_executor.py::_dispatch_pre_tool_hook()` discards the
dispatcher's return value. `PluginHookDispatcher.dispatch_pre_tool_use(...)`
runs the matching hooks and returns a list of attachment records, but the
executor drops it and the dispatcher never parses any control fields out of
hook stdout for `PreToolUse` (only the `Stop` path parses
`decision` / `hookSpecificOutput`; `PreToolUse` goes through
`_dispatch_lifecycle` → `_run_lifecycle_command`, which only wraps stdout in an
attachment). So `PreToolUse` is observational only:

- hook output cannot deny a tool call;
- hook output cannot ask for confirmation;
- hook-provided context is not injected into the execution path;
- host/replay consumers do not see a permission decision caused by the hook.

This is the primary P0. Note the change spans two layers: the dispatcher must
learn to parse `permissionDecision` out of `PreToolUse` hook stdout, and the
executor must consume the parsed decision.

There is also an event-ordering constraint. `_dispatch_pre_tool_hook()` is
called at `tool_executor.py:273` — *after* the permission/cancel guards and
*after* `host_tool_emitter.started()` has already fired (line 251). The
existing ordering contract (code comment at `tool_executor.py:244-247`)
requires `PermissionDecisionEvent` to precede `ToolLifecycleEvent(started)` for
the same `tool_call_id`. Making the hook decision-capable therefore requires
moving the hook dispatch *before* the `started` emission, and emitting a
`cancelled` terminal event for any tool that already emitted `started` and is
then denied by a hook.

### Telemetry

Status: partially present.

Existing pieces:

- `agentao/runtime/llm_call.py` emits `LLM_CALL_STARTED` and
  `LLM_CALL_COMPLETED`.
- LLM call completion already includes `duration_ms`.
- Tool lifecycle events carry timestamps, so tool duration can be derived.
- Context compaction paths already emit duration in transport/replay events.

Missing pieces:

- host-facing `model_latency_ms` — this is a forward/rename of the existing
  `duration_ms` already on `LLM_CALL_COMPLETED`, not a new measurement;
- `first_token_ms` / TTFT — feasible today because `llm_call.py` is streaming
  (`chat_stream` + `LLM_CALL_DELTA`); TTFT = timestamp of the first
  `LLM_CALL_DELTA` minus call start. The data already flows through transport;
  only aggregation is missing. No LLM-client change required;
- turn-level tool count;
- stable public/replay placement for context compaction duration.

This is useful and low-cost, but it is P1 rather than P0.

## P0: Minimal Decision-Capable PreToolUse

`PreToolUse` should use the Claude Code-compatible
`hookSpecificOutput.permissionDecision` shape. Do not reuse the Stop/PreCompact
`decision: "block"` or `preventContinuation` semantics. Those mean "prevent
Stop and continue the agent loop", which is different from rejecting a tool
call.

Keep the first implementation deliberately small. The goal is to close the
current fire-and-forget hole, not to introduce a new hook policy subsystem.

Supported output shape:

```json
{
  "hookSpecificOutput": {
    "permissionDecision": "allow",
    "reason": "optional human-readable reason",
    "additionalContext": "optional structured context"
  }
}
```

Recognized `permissionDecision` values:

- `allow`
- `deny`
- `ask`

MVP behavior:

- `deny` cancels the current tool call.
- `ask` must reuse the existing confirmation path through the transport
  permission prompt. Do not introduce a separate interaction channel.
- `deny` and `ask` must produce a `PermissionDecisionEvent`.
- Replay must record the decision with enough metadata to explain why the tool
  was blocked or prompted.
- Do not add a new public `source` field just for this. Use existing
  `reason`/`matched_rule` fields to mark hook-derived decisions, for example
  `pre-tool-hook`. (Replay/projection consumers must tolerate the new origin
  string without a schema bump — it is just another value in an existing
  string field.)
- `allow` is a no-op in the MVP. It must not downgrade an existing
  `deny`/`ask` from the permission engine.
- If multiple hooks return decisions, keep merge behavior simple:
  first `deny` wins; otherwise first `ask` wins; otherwise continue.
- `additionalContext` should be parsed and recorded as hook output, but should
  not be injected into the model or tool execution path in the MVP. Today's
  `PreToolUse` attachments are only returned by the dispatcher and discarded by
  the executor; they are not wired into the UserPromptSubmit
  `_attachment_to_message` prompt-injection path.

Precedence (must be stated, not left implicit):

- A hook `deny`/`ask` *overrides* an `allow` from the permission engine
  (allowlist auto-allow or a prior user "allow"). Otherwise P0 has no effect.
- A hook `allow` never overrides an engine `deny`/`ask` (already stated above).
- A hook `ask` is implemented by setting the plan decision to `ASK`, so it
  reuses the *same* Phase 2 confirmation path as any other prompt — and is
  therefore subject to the `allow_all_tools` / full-access session state like
  any other prompt. Letting a hook `ask` bypass that toggle would need a new
  flag on `Transport.confirm_tool`, which is out of scope for the MVP; revisit
  if a host needs it.

Event ordering (part of P0 scope, see "Current State Check"):

- Move the hook dispatch to before the `host_tool_emitter.started()` emission
  so a hook-derived `PermissionDecisionEvent` precedes `ToolLifecycleEvent(started)`.
- If a tool has already emitted `started` and is then denied/asked-and-cancelled
  by a hook, emit a `cancelled` terminal event for it.

Out of scope for P0:

- public host schema changes;
- context injection into the model prompt;
- exit-code-based deny for `PreToolUse` (Claude Code honors exit code 2 =
  block, stderr → model); MVP supports only the JSON `permissionDecision`
  shape — see Non-Goals;
- `updatedInput`
- arbitrary argument rewriting
- complex multi-hook policy merging
- new extension APIs

### Status — implemented 2026-05-12

Landed:

- `PluginHookDispatcher.dispatch_pre_tool_use_decision()` parses the
  `hookSpecificOutput.permissionDecision` shape (`allow` / `deny` / `ask`),
  merges verdicts (first `deny` wins, then first `ask`), stops forking on the
  first `deny`, and records `additionalContext` on a new
  `PreToolUseHookResult` without injecting it. Exit-code-2 "block" is not
  honored. The old side-effect-only `dispatch_pre_tool_use()` is left intact
  for callers that just want attachments.
- The fire-and-forget `_dispatch_pre_tool_hook()` in
  `runtime/tool_executor.py` is removed; PreToolUse now runs in
  `ToolRunner._apply_pre_tool_use_hooks()` as Phase 1.5 — before the
  `PermissionDecisionEvent` emit and before any tool `started` event, so the
  ordering contract holds without an after-the-fact `cancelled`.
- A hook `deny` flips the plan to `DENY`; a hook `ask` flips an `ALLOW` plan to
  `ASK` (flows through Phase 2 confirmation); `allow`/no-decision is a no-op.
  Hook-derived decisions carry `reason = "pre-tool-hook[: <hook reason>]"` and
  `matched_rule = None`; no new public field. A `PLUGIN_HOOK_FIRED` replay
  event with `hook_name: "PreToolUse"` is emitted for parity with the other
  hook sites.
- Tests: `tests/test_hooks_pre_tool_use_decision.py` (dispatcher parsing/merge
  + runner Phase 1.5 wiring, including the deny/ask/allow precedence and the
  no-rules fast path).

## P1: Minimal Telemetry Increment

Add only the small set needed to debug host integrations:

- `model_latency_ms`
- `first_token_ms`
- turn-level `tool_count`
- stable compaction duration field for public/replay consumers

`trace_id`, `turn_id`, and `tool_call_id` are already present or conceptually
covered. Do not introduce a large SQLite telemetry subsystem unless Agentao's
deployment model changes.

### Status — implemented 2026-05-12

Landed (all as optional fields on existing transport/replay event payloads —
no new event types, no public host-schema bump):

- `runtime/llm_call.py`: `LLM_CALL_COMPLETED` now carries `model_latency_ms`
  (a stable, intent-named alias of the existing `duration_ms`) and
  `first_token_ms` (TTFT — monotonic stamp of the first streamed text chunk
  reaching `on_text_chunk`, minus call start; `None` for tool-only responses
  or failures before the first delta). Both the ok and error emit paths
  include them. The streaming callback was promoted from a lambda to a named
  closure so it can record the first-chunk timestamp.
- `runtime/turn.py` + `runtime/chat_loop/_runner.py`: a per-turn
  `agent._turn_tool_count` is reset in `run_turn`, bumped by
  `len(clean_tool_calls)` after each tool batch in the chat loop, and reported
  on `TURN_END` as `tool_count`. `replay/adapter.py` mirrors it onto the
  `TURN_COMPLETED` replay record (`end_turn(..., tool_count=...)`).
- Compaction duration was *already* covered: `CONTEXT_COMPRESSED` has carried
  `duration_ms` (populated by every microcompaction / full-compaction call
  site) and the replay adapter already records it — no change needed.
- Tests: `tests/test_telemetry_increment.py` (TTFT/latency on the ok, error,
  and tool-only paths; `tool_count` on `TURN_END` and the replay mirror).
- Docs: `developer-guide/{en,zh}/part-4/2-agent-events.md` updated for the new
  `LLM_CALL_COMPLETED` and `TURN_END` fields.

Deliberately *not* done: `model_latency_ms` is not re-exposed on the public
`agentao.host` Pydantic models — the host contract does not currently project
LLM-call events at all, and adding that surface is a larger decision than this
increment. Hosts that want latency today subscribe to the transport
`LLM_CALL_COMPLETED` event (same as cost/usage tracking already does).

## P2: Permission Abstraction and Redaction

Read denial is worth modeling in the permission abstraction, but OS-level
Windows deny-read parity is not a current priority. Agentao's current sandbox
focus remains local-first and macOS-first.

P2 work:

- add first-class read-deny concepts to the permission model;
- verify ACP/remote output redaction boundaries;
- keep OS-specific enforcement separate from the public permission contract.

## Explicit Non-Goals

Do not prioritize these based on Codex alone:

- a large unified extension API;
- moving guardian/review out of core;
- multi-environment `apply_patch` selection;
- OS-level Windows deny-read implementation;
- exit-code-based deny for `PreToolUse` hooks in the MVP (JSON
  `permissionDecision` only; exit-code-2 parity can come later);
- TUI restructuring just because Codex split its TUI modules.

These may become useful later, but current Agentao needs do not justify the
complexity.

## Final Priority

P0: make `PreToolUse` minimally decision-capable and auditable: support
hook-driven `deny` and `ask`, record the source through existing event fields,
fix the hook-dispatch ordering so the decision event precedes `started`, and
avoid schema or context-injection changes.

P1: add the minimal telemetry fields for model latency, TTFT, compaction
duration, and turn tool count.

P2: model read-deny and verify ACP/remote redaction.

Everything else should be deferred until Agentao has a concrete product need.
