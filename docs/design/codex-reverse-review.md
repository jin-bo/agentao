# Codex Reverse Review for Agentao

Date: 2026-05-12

This note records the reverse review of the latest Codex changes against
Agentao's current architecture. The goal is to identify what Agentao actually
needs, not to mirror Codex's product shape.

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
