# Stop / PreCompact Hook Events — Implementation Plan

**Date:** 2026-05-04 (rev 2026-05-05 review passes 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23 — see "Revision notes" at the bottom)
**Status:** Draft. Triggered by `docs/design/pi-mono-borrow-review.md` §KEEP P2.
**Source design:** `docs/design/pi-mono-borrow-review.md` §KEEP P2 ("Add `Stop` / `PreCompact` event types to plugin-hook system").
**Companion:** `STOP_PRECOMPACT_HOOKS_PLAN.zh.md`.
**Scope:** Two new lifecycle events (`Stop`, `PreCompact`) on the existing plugin-hook surface, split into two layered phases — event surface (Phase A) and control-aware gate (Phase B).

---

## TL;DR

- **Phase A — Event surface (~1.5 days).** Register `Stop` and `PreCompact` in `SUPPORTED_HOOK_EVENTS`. Emit from `chat_loop` at the turn-end boundary (see "Semantics") and at every compaction trigger site. **Adopt Claude Code's flat snake_case wire format** for these two events specifically (other adapter methods keep Agentao's existing envelope — out of scope here), so a hook script written against Claude Code's documented Stop / PreCompact stdin shape runs unchanged. Both events go through `_dispatch_lifecycle` (side-effect-only) plus an extension to `_matches` for PreCompact's `manual|auto` matcher. See the "Claude Code compatibility matrix" section below for what is and isn't compatible.
- **Phase B — Control-aware gate (~2 days).** Promote `Stop` from side-effect-only to a control-aware result that honors **Claude Code's full Stop control surface**: exit code 2 (block + stderr-as-reason), JSON `decision: "block"` + `reason`, plus the documented common output fields (`continue`, `stopReason`, `suppressOutput`, `systemMessage`, `hookSpecificOutput.additionalContext`). Wire `chat_loop` to honor a `force_continue` decision. `PreCompact` blocking (Claude Code supports it via exit 2 / `decision: "block"`) is **explicitly out of scope** — that gap is documented in the matrix and the dedicated "PreCompact gate" section, not labelled "deferred."

PRs are independent. PR-1 alone is shippable if the triggering use case is observability (cost gates, audit logs). PR-2 is required for `shouldStopAfterTurn` parity (post-turn reviewers, autonomous-loop validators).

---

## Why two phases (recap)

`_dispatch_lifecycle` (`agentao/plugins/hooks.py:369-390`) is documented as side-effect-only: nonzero exit codes log a warning, stdout is captured into the dispatcher's returned `list[HookAttachmentRecord]` as a `hook_success` record (currently discarded by every call site — see A6 attachment caveat), and the dispatcher does not parse `preventContinuation` / `blockingError` / `continue=false`. Control-result parsing only lives on the `UserPromptSubmit` path (`_parse_command_output`, `hooks.py:537+`).

So registering `Stop` in `_dispatch_lifecycle` alone yields an observation surface, not a gate. The two phases below separate "host can see the event" from "host can change loop behavior," and let Phase A ship without committing to Phase B's design choices.

---

## Semantics — what does each event mark?

The hook *names* are inherited from Claude Code for parity, but the names alone do not answer "stop what?" / "compact what?". This section pins both definitions so payload fields and emit sites stay consistent.

**`Stop` = `BeforeTurnEnd`.** Fires when the agentic loop for the **current user turn** is about to end and the assistant's final message has not yet been committed to `agent.messages`. *Not* a session-end (use `SessionEnd`). *Not* a process-stop. *Not* fired when the user hits Ctrl-C mid-turn (no clean turn boundary). The **three** emit sites in this plan correspond to the three ways a turn ends in `chat_loop`:

- the model returned no further `tool_calls` (natural completion); payload `turn_end_reason="final_response"`.
- the loop hit `max_iterations` and the `on_max_iterations` callback returned `"stop"`; payload `turn_end_reason="max_iterations"`.
- `ToolRunner.execute(...)` returned `doom_loop_triggered=True` and the loop `break`s out (`chat_loop.py:271-272`); payload `turn_end_reason="doom_loop"`. The doom-loop detector is a separate safety net inside `tool_runner.py` / `tool_planning.py` (see `_DOOM_HALT_MESSAGE` / `result.doom_loop_triggered`); it is **not** the same condition as `max_iterations` (it can trip on iteration 2 if the model is repeating itself), so it gets its own discriminator value rather than being collapsed into `"max_iterations"`.

Hosts that need to distinguish "real answer" from "iteration cap reached" from "model misbehaving" must read `turn_end_reason` (snake_case — this is a top-level Claude-flat key alongside the Claude common fields, see A3); the hook event name alone is insufficient.

**`PreCompact` = `BeforeMessagesMutation`.** Fires immediately before any code path mutates `agent.messages` for context-size reasons, while the about-to-be-discarded history is still intact and inspectable. The four emit sites in this plan are listed in A4; hosts distinguish them via the `compaction_type` and `reason` payload fields (snake_case — these are top-level Claude-flat keys; see A3). *Not* fired for non-compaction mutations (tool-call appends, user-message appends, hook-injected user messages). *Not* fired post-compaction — the existing internal `EventType.CONTEXT_COMPRESSED` event already covers that boundary.

These definitions are the source of truth referenced by A3 (payload fields) and A4 (emit sites).

---

## Claude Code compatibility matrix

**Goal of this plan:** a hook script written against Claude Code's published Stop / PreCompact contract should run unchanged when Agentao loads it via this plan's adapter — for the dimensions marked ✅ below. Dimensions marked 🟡 are partially compatible with documented limits; ❌ dimensions are intentional gaps with rationale.

This matrix is the authoritative compatibility statement; A3, A4, A6, B1, B2, B5 implement it.

| Dimension | Claude Code (reference) | Agentao (this plan) | Status |
|---|---|---|---|
| Event names | `Stop`, `PreCompact` | Same | ✅ |
| Wire input shape (Stop, PreCompact) | Flat snake_case top-level keys | Flat snake_case via dedicated `build_*` (A3) | ✅ |
| Wire input shape (other events) | Flat snake_case | Agentao envelope `{event, data}` | ❌ — pre-existing across-the-board gap; **out of scope** for this plan, tracked separately |
| Common input fields (key shape) | `session_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name` | All five included for Stop / PreCompact as top-level keys (A3) | ✅ (with `transcript_path` nullable today — see Open Question 1) |
| `permission_mode` value space | Claude Code values: `"default" \| "plan" \| "acceptEdits" \| "auto" \| "dontAsk" \| "bypassPermissions"` | Agentao values: `"read-only" \| "workspace-write" \| "full-access" \| "plan"` (from `agent.permission_engine.active_permissions().mode`) | 🟡 — **field shape matches but value vocabularies diverge**. Only `"plan"` overlaps. A Claude hook script that branches on `if permission_mode == "acceptEdits": ...` will see `"workspace-write"` instead and fall through to its `else` branch. See Open Question 5 for the value-mapping decision. |
| Stop input `stop_hook_active` | Present | Included (A3) | ✅ |
| Stop input `last_assistant_message` | Present | Included (A3, A4 threads it from `assistant_content`) | ✅ |
| PreCompact input `trigger` | `"manual"` \| `"auto"` | Same enum, but **value `"manual"` never emitted** (no manual `/compact` CLI in Agentao) | 🟡 — value space narrower; documented in A3 |
| PreCompact input `custom_instructions` | Present (manual trigger payload) | Included; always empty (no manual trigger) | 🟡 — present but always empty |
| Exit code 0 | Continue | Continue | ✅ |
| Exit code 2 — Stop | Block + stderr fed back as reason | Honored via Stop-specific runner (B2) | ✅ |
| Exit code 2 — PreCompact | Block compaction | **Not honored** — Phase A is observe-only for PreCompact | ❌ — see "PreCompact gate" below |
| Other nonzero exit | Non-blocking warning | Same (existing `_run_command_hook` behavior) | ✅ |
| JSON `continue: false` | Stop the agent (overrides default continuation) | Honored on Stop path; unused for PreCompact (no gate) | 🟡 |
| JSON `stopReason` | Reason text | Honored (B2) | ✅ |
| JSON `suppressOutput` (Claude semantic — hide raw stdout / debug log from transcript) | Hide hook stdout from transcript / debug-log channel | **Vacuously honored on the raw-stdout channel today.** Agentao does **not** project hook stdout onto `PLUGIN_HOOK_FIRED` (the emit carries verdict + counts only — `outcome`, `matched_rule_count`, `added_context_count`, `suppress_output`, etc.), and there is no user-visible transcript display path for hook stdout in the current chat-loop. So there is no stdout body to suppress; the field is recorded on `StopHookResult.suppress_output` and threaded onto `PLUGIN_HOOK_FIRED.suppress_output` for replay fidelity, but Claude's "hide stdout from transcript" intent has nothing to act on. If a future Agentao surface starts displaying or projecting hook stdout, that surface must consult `suppress_output` to stay Claude-compatible. | 🟡 — **vacuous on this channel today**; field is recorded faithfully but does not gate any current display path |
| Agentao extension to `suppressOutput` (gate `additional_contexts` echo) | Not in Claude's documented semantic | When `True`, B3 also skips appending `<stop-hook>...additional_contexts...</stop-hook>` blocks onto the assistant's final answer (B1 docstring + B3 wiring at the natural-turn allow path) | 🟡 — **Agentao-specific reinterpretation, not Claude parity.** Claude's `hookSpecificOutput.additionalContext` is a separate structured channel and is documented as not affected by `suppressOutput`. We deliberately extend `suppressOutput` to also gate the echo because the use case ("audit hook attaches a note for replay but doesn't want to clutter the user-visible answer") is real and the alternative would require a new flag. Hosts that want strict Claude semantic should not write `suppressOutput: true` paired with `additionalContext` — keep them on separate hook outputs. |
| JSON `systemMessage` | System-message string | Mapped to `additional_contexts` (B2) | ✅ |
| JSON `decision: "block"` (Stop) | Block the stop; `reason` is follow-up | Honored as `force_continue` + `follow_up_message` (B2) | ✅ |
| JSON `decision: "block"` (PreCompact) | Block compaction | **Not honored** | ❌ — see "PreCompact gate" |
| JSON `hookSpecificOutput.additionalContext` (Stop) | Add context | Honored (B2) | ✅ |
| Matcher (Stop) | None — Stop hooks always fire | Same | ✅ |
| Matcher (PreCompact) — runtime regex evaluation | Regex against `manual\|auto` | Honored via extension to `_matches` (A2) using **`re.fullmatch`** for the `trigger` field — alternation patterns like `manual\|auto` work | ✅ for the runtime semantic, **assuming the matcher arrives as an Agentao-shape object** `{"trigger": "manual\|auto"}`. |
| Matcher (PreCompact) — config file shape | Top-level string field: `{"matcher": "manual\|auto", ...}` | A2 / A1 require **object** shape; a literal string matcher (`"matcher": "auto"`) is dropped at parse time as a `PluginWarning` and the rule does **not** load | 🟡 — **runtime semantic ✅, config shape ❌**. A Claude `hooks.json` migrated verbatim has its PreCompact matcher dropped. Hosts must either pre-translate to `{"trigger": "..."}` or wait for a config-translation layer (subsumed by the pre-existing "Hook config file path / shape" ❌ row above). |
| Hook type — `command` (Stop, PreCompact) | Supported on both events | Supported | ✅ |
| Hook type — `http` (Stop) | Supported | **Not supported** — `"http"` is in `KNOWN_UNSUPPORTED_HOOK_TYPES` (`agentao/plugins/models.py:210`); A1 parser warns and skips | ❌ — pre-existing Agentao gap, not specific to this plan. Hosts that need HTTP-callback Stop hooks must wait for an Agentao HTTP-hook runner; out of scope. |
| Hook type — `http` (PreCompact) | Supported | Same rejection as Stop | ❌ — pre-existing Agentao gap. |
| Hook type — `mcp_tool` (Stop) | Supported | **Not recognized** — `"mcp_tool"` is not in `SUPPORTED_HOOK_TYPES` nor in `KNOWN_UNSUPPORTED_HOOK_TYPES`; the parser falls through to the "Unknown hook type" branch and skips with a warning | ❌ — pre-existing Agentao gap. Adding `mcp_tool` would require a runner that bridges into Agentao's existing MCP client (`agentao/mcp/client.py`) — separate plan. |
| Hook type — `mcp_tool` (PreCompact) | Supported | Same rejection as Stop | ❌ — pre-existing Agentao gap. |
| Hook type — `prompt` (Stop) | Supported (Claude allows prompt-based Stop hooks) | **Rejected at parse time** by A1's `SUPPORTED_HOOK_TYPES_BY_EVENT` map (Stop allows only `command`) | ❌ — **deliberate**, not "not yet": rationale and migration path in the "Why not prompt-type hooks for Stop / PreCompact" section above. A Claude `hooks.json` with `{event: "Stop", hook_type: "prompt", ...}` will not load in Agentao; convert to a `command` shim. |
| Hook type — `agent` (Stop) | Supported | **Not supported** — `"agent"` is in `KNOWN_UNSUPPORTED_HOOK_TYPES` and additionally rejected per-event by A1 | ❌ — pre-existing Agentao gap; not specific to Stop. |
| Hook type — `prompt` (PreCompact) | **Not supported by Claude** for PreCompact (only `command`/`http`/`mcp_tool` are documented) | Rejected at parse time | N/A — **not a compatibility gap** because neither side supports it. Listed for completeness; no migration concern. |
| Hook type — `agent` (PreCompact) | **Not supported by Claude** for PreCompact | Not supported | N/A — same as above. |
| Hook config file path / shape | `~/.claude/settings.json` (Claude-specific schema) | Agentao reads its own `permissions.json` / hook config; **shape and discovery path differ** | ❌ — pre-existing; **out of scope**. Hosts that want drop-in Claude config files must pre-translate. |

**The intentional ❌ rows in scope of this plan are:**

- **PreCompact blocking** (exit 2 / `decision: "block"`). The PreCompact emit sites mutate `agent.messages` in place and the surrounding overflow-recovery code assumes compression succeeds; honoring a host "no" without a "host said no but we still don't fit" fallback produces unrecoverable runaway behavior. Section "PreCompact gate" below pins this as the gap, not a roadmap item.
- **prompt/agent hook types for Stop only** — Claude supports them, we choose not to (capability-redundant with command hooks; see "Why not prompt-type hooks for Stop / PreCompact" section). PreCompact prompt/agent rows in the matrix are labelled `N/A` rather than ❌, because Claude itself does not document support for them on PreCompact (only `command` / `http` / `mcp_tool`) — there is no compatibility gap to deliberate over.

The other ❌ rows (other events' wire shape; config file shape) are **not in scope for this plan**; they would require touching every adapter method or introducing a config-translation layer respectively, both of which are larger refactors.

---

## Why not prompt-type hooks for Stop / PreCompact

This section justifies the matrix ❌ rows that reject `hook_type ∈ {prompt, agent}` **for Stop**. (PreCompact prompt/agent are listed as `N/A` in the matrix because Claude Code itself does not support them on PreCompact — pass 8 corrected an earlier draft that mislabelled them as ❌; see revision notes.) Pass 5 added the parse-time rejection (A1's `SUPPORTED_HOOK_TYPES_BY_EVENT`); pass 6 surfaced the rejection on the matrix; this section pins **why** we are not implementing what Claude Code does support for Stop, so a future maintainer asking "should we just add it?" finds the answer in-document instead of re-deriving it.

### The Stop case — capability-redundant with command hooks

Claude Code's prompt-based Stop hook lets a host inject a templated prompt into the model at turn-end (typical use: a post-turn reviewer that asks "did you actually finish?"). In Agentao, the same effect is reachable through a `command`-type Stop hook with strictly less ambiguity:

- The reviewer host writes a small command-type hook subprocess that itself calls the LLM (using the host's own credentials and model choice — often desirable since the reviewer model can differ from the agent model).
- The subprocess emits Claude-compatible Stop JSON: `{"decision": "block", "reason": "...you skipped the test step"}` to force-continue, or `{"hookSpecificOutput": {"additionalContext": "..."}}` to attach a review note, or `{"continue": false}` to accept the stop unconditionally. All three paths already work end-to-end through B2's parser table.
- The host gets full control over which model reviews, with what system prompt, on what budget — none of which is parameterizable in a "templated prompt fed to the agent's own model" design.

Adding native prompt-type Stop support, in contrast, opens an unanswered design question for **every** Claude Stop output field: where does the model's response to the prompt land?

| If the prompt-hook's model response says... | ...does it become `force_continue`? `follow_up_message`? `additional_contexts`? `system_message`? `blocking_error`? |
|---|---|
| "you should keep going" | `force_continue=True` is the obvious read, but how do we tell that apart from a model that just chats? |
| "this looks good" | `additional_contexts`? Or no-op? |
| "you missed a test" | `blocking_error`? `follow_up_message`? Both? |

There is no canonical answer in the Claude Code documentation that maps a free-text model response to the structured Stop output schema; Claude Code itself sidesteps the question by feeding the prompt-hook's response into the conversation as a regular message. To replicate that, Agentao would need a third Stop control surface (raw conversation injection) sitting alongside `force_continue` and `additional_contexts`. The existing command-hook path already covers every concrete reviewer use case at lower design cost.

**Verdict.** Reject. Hosts that want a reviewer write a `command`-type hook that internally calls an LLM. The lost compatibility is a Claude-config-file portability gap, which is already a documented ❌ in the matrix's "Hook config file path / shape" row — i.e., **a pre-existing larger gap subsumes this one**, not a new break.

### The PreCompact case — Claude doesn't support prompt/agent here either

**Correction relative to earlier drafts of this plan.** Earlier passes wrote this subsection as "Claude supports prompt-type PreCompact, but we choose not to." That premise is wrong: Claude Code's documented hook-type matrix lists PreCompact as supporting `command` / `http` / `mcp_tool` only — `prompt` and `agent` are **not** Claude features for PreCompact in the first place. So there is no "we say no to a thing Claude says yes to" gap to discuss here.

What this means for the matrix:

- `prompt` (PreCompact): N/A. Neither Claude nor Agentao supports it. The matrix row is kept only for completeness so a reader doesn't ask "did we forget to evaluate this?"
- `agent` (PreCompact): N/A. Same.
- `http` and `mcp_tool` for PreCompact: ❌ **on the Agentao side only** — Claude supports them. These are the real Claude-vs-Agentao PreCompact hook-type gaps and are now listed explicitly in the matrix.

The Stop subsection above still applies as written: Stop genuinely supports prompt/agent in Claude (the docs include a Stop prompt-hook example), and we genuinely choose not to implement them in Agentao for the capability-redundancy reason given there.

### What this means for migration from Claude Code

A `hooks.json` that uses `{event: "Stop", hook_type: "prompt", prompt: "..."}` will not load in Agentao under this plan. The translation path is:

1. Author a `command`-type Stop hook script (a few lines of bash / Python).
2. Inside the script, call whichever LLM the host wants for review.
3. Emit Claude Code Stop JSON on stdout (`decision`, `additionalContext`, etc.) — the same shape Claude itself documents.

This is a one-time per-script conversion and produces a strictly more capable hook (independent model choice, independent budget, no ambiguity in how its output is interpreted). The matrix ❌ rows for prompt/agent Stop and PreCompact, the parse-time rejection in A1, and this section together form the documented "we considered it, here's why not, and here's the workaround" record.

---

## Phase A — Event surface

### A1. Add events to the supported set + per-event hook-type validation

`agentao/plugins/models.py:197`:

```python
SUPPORTED_HOOK_EVENTS: set[str] = {
    "UserPromptSubmit",
    "SessionStart",
    "SessionEnd",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",        # NEW
    "PreCompact",  # NEW
}

# Per-event hook-type allowlist. Stop / PreCompact deliberately exclude
# "prompt": at runtime `_dispatch_lifecycle` only invokes command-type
# hooks for these events (B2 also forks a Stop-specific runner that knows
# nothing about prompt hooks), so a prompt-type rule against Stop or
# PreCompact would parse as supported but be silently dropped at dispatch.
# Reject it at parse time instead so misconfiguration surfaces as a
# loud parser warning rather than a quiet no-op.
SUPPORTED_HOOK_TYPES_BY_EVENT: dict[str, set[str]] = {
    "UserPromptSubmit": {"command", "prompt"},
    "SessionStart": {"command"},
    "SessionEnd": {"command"},
    "PreToolUse": {"command"},
    "PostToolUse": {"command"},
    "PostToolUseFailure": {"command"},
    "Stop": {"command"},        # NEW — explicitly excludes "prompt"
    "PreCompact": {"command"},  # NEW — explicitly excludes "prompt"
}
```

**`ParsedHookRule.is_supported` extension** (`agentao/plugins/models.py:226`). Today:

```python
@property
def is_supported(self) -> bool:
    return self.hook_type in SUPPORTED_HOOK_TYPES and self.event in SUPPORTED_HOOK_EVENTS
```

becomes:

```python
@property
def is_supported(self) -> bool:
    if self.event not in SUPPORTED_HOOK_EVENTS:
        return False
    allowed = SUPPORTED_HOOK_TYPES_BY_EVENT.get(self.event, SUPPORTED_HOOK_TYPES)
    return self.hook_type in allowed
```

The fallback to `SUPPORTED_HOOK_TYPES` keeps backward compatibility for any event added later without an entry in the map.

**Parser-side per-event check (`agentao/plugins/hooks.py:120-140`).** The `is_supported` extension above is a runtime predicate, but the matrix's "Rejected at parse time" claim and A6's expectation of a parser-warning + parse-time rejection require the **parser** to also drop the rule before it reaches `rules.append`. Today the parser only checks `hook_type in SUPPORTED_HOOK_TYPES` (`hooks.py:132`), which lets a `{event: "Stop", type: "prompt"}` rule pass through and end up in `rules` with `is_supported == False` — silently inert at runtime, contradicting "rejected at parse time".

Add an explicit per-event check **after** the existing `hook_type in SUPPORTED_HOOK_TYPES` branch and **before** `rules.append`:

```python
# Existing checks (unchanged):
if hook_type in KNOWN_UNSUPPORTED_HOOK_TYPES:
    warnings.append(PluginWarning(... "not supported — skipped" ...))
    continue
if hook_type not in SUPPORTED_HOOK_TYPES:
    warnings.append(PluginWarning(... "Unknown hook type — skipped" ...))
    continue

# NEW per-event check, added by this plan:
allowed_for_event = SUPPORTED_HOOK_TYPES_BY_EVENT.get(
    event_name, SUPPORTED_HOOK_TYPES,
)
if hook_type not in allowed_for_event:
    warnings.append(
        PluginWarning(
            plugin_name=plugin_name,
            message=(
                f"Hook type '{hook_type}' is not supported for event "
                f"'{event_name}' — skipped. (Allowed for this event: "
                f"{sorted(allowed_for_event)})"
            ),
            field="hooks",
        )
    )
    continue

# ... existing timeout / matcher checks, then rules.append(...)
```

Result: a `prompt`-type Stop or PreCompact rule is dropped at parse time with a `PluginWarning`, matching what the matrix promises. The runtime `is_supported` extension stays as defense-in-depth — same role as the runtime matcher guard in A2 — for any code path that builds `ParsedHookRule` outside the loader.

A6's `test_hooks_stop_precompact_reject_prompt_type.py` test asserts the parse-time drop (`rules` list does not contain the prompt-type rule, `warnings` contains the dedicated event/type message), not just the runtime `is_supported` flip.

### A2. Add dispatcher entry methods + extend `_matches` for PreCompact's `manual|auto` matcher

`agentao/plugins/hooks.py`, alongside `dispatch_session_start` etc.:

```python
def dispatch_stop(self, *, payload, rules) -> list[HookAttachmentRecord]:
    return self._dispatch_lifecycle("Stop", payload, rules)

def dispatch_pre_compact(self, *, payload, rules) -> list[HookAttachmentRecord]:
    return self._dispatch_lifecycle("PreCompact", payload, rules)
```

**Phase B will replace `dispatch_stop`'s return type** with `StopHookResult` (see B2). This is a deliberate, breaking signature change between PR-1 and PR-2: `dispatch_pre_compact` keeps `list[HookAttachmentRecord]` (PreCompact stays observe-only — see B5), but `dispatch_stop` is upgraded to carry the gate signal. PR-2's checklist (Sequencing section) explicitly includes updating the A6 dispatcher test (`test_hook_dispatcher_stop.py`) to walk `result.messages` instead of asserting on the bare list — see B2's "Test impact" note. Hosts that depend on the Phase-A list shape do not exist yet (the dispatcher is internal); the breaking change is contained inside Agentao.

`_dispatch_lifecycle` itself is unchanged. **`_matches` is extended** to handle three new realities introduced by Stop / PreCompact:

1. The payload for these two events is **flat snake_case at the top level** (Claude Code parity — see A3), not the agentao `{event, data}` envelope, so `_matches` must read top-level keys when the event is `Stop` / `PreCompact`.
2. Claude Code's PreCompact matcher is a **regex** against `trigger` (`manual|auto`), not a glob and not `toolName`. The existing `_glob_match` helper (`agentao/plugins/hooks.py:832-844`) does not honor regex alternation: a pattern like `manual|auto` has no `*`, so it falls through to the exact-equality branch and would never match either `"manual"` or `"auto"`. We add a tiny `_regex_match` helper local to PreCompact and use `re.fullmatch` so Claude-style patterns work.
3. Stop has **no documented matcher** in Claude Code — Stop hooks always fire. A misconfigured rule with a non-empty `matcher` for Stop should still fire (matrix's "Matcher (Stop): None — always fire" row); we ignore matcher contents on Stop and always return True.

```python
import re

def _regex_match_full(pattern: str, value: str) -> bool:
    """Anchored full-match regex used by Claude-compat event matchers."""
    try:
        return re.fullmatch(pattern, value) is not None
    except re.error:
        # A malformed pattern degrades to exact-equality so the rule is
        # not silently dropped; warn at parse time when we can.
        return pattern == value

def _matches(self, rule: ParsedHookRule, payload: dict[str, Any]) -> bool:
    if rule.matcher is None:
        return True

    # Type guard: `rule.matcher` is whatever `entry.get("matcher")`
    # returned at parse time (`agentao/plugins/hooks.py:161`); the parser
    # does not enforce a dict shape today. A user (or a Claude config
    # translation layer) might supply a string like `"manual|auto"` or
    # a list. Without this guard, every `.get(...)` below would
    # AttributeError at runtime. Two viable responses:
    #   (i) treat non-dict as "match anything" — permissive, surprises
    #       hosts who expected the matcher to filter
    #   (ii) reject the rule (treat as no-match) and warn
    # We pick (ii) so a misconfigured matcher does not silently
    # broaden the rule's reach. A1's parser additionally warns at
    # parse time when it can detect the type early — see A1 caveat below.
    if not isinstance(rule.matcher, dict):
        logger.warning(
            "Hook rule for event %r has non-dict matcher %r; "
            "treating as no-match. Matchers must be objects, e.g. "
            "{\"trigger\": \"manual|auto\"}.",
            rule.event, rule.matcher,
        )
        return False

    # Claude-flat events: read fields from the top level.
    event = payload.get("hook_event_name") or rule.event
    if event in {"Stop", "PreCompact"}:
        if event == "PreCompact":
            trigger_pattern = rule.matcher.get("trigger")
            if trigger_pattern is not None:
                payload_trigger = payload.get("trigger", "")
                # Claude Code semantics: regex (NOT glob).
                if not _regex_match_full(trigger_pattern, payload_trigger):
                    return False
        # Stop: matcher is undefined in Claude Code; always fire.
        return True

    # Agentao-envelope events (UserPromptSubmit / SessionStart /
    # PreToolUse / PostToolUse / PostToolUseFailure) — unchanged behavior;
    # these still use glob, matching today's `_glob_match`.
    data = payload.get("data", {})
    tool_name_pattern = rule.matcher.get("toolName")
    if tool_name_pattern is not None:
        payload_tool = data.get("toolName", "")
        if not _glob_match(tool_name_pattern, payload_tool):
            return False
    return True
```

**A1 parse-time matcher type check (companion to the runtime guard above).** The loader at `agentao/plugins/hooks.py:161` currently writes `entry.get("matcher")` straight through. Add a type check there that **drops the rule entirely** rather than normalizing the bad value — because the existing runtime contract at `hooks.py:394` is `if rule.matcher is None: return True` (i.e., `None` means "match everything"), so silently rewriting a bad matcher to `None` would invert the warning's intent: a misconfigured rule would suddenly match every event instead of nothing. Drop-the-rule keeps Claude Code's "bad rules don't load" behavior and avoids the inversion entirely.

Use the existing `PluginWarning` model — the loader's `warnings` list is typed `list[PluginWarning]` (`hooks.py:82`); appending a raw f-string would break the type:

```python
matcher = entry.get("matcher")
if matcher is not None and not isinstance(matcher, dict):
    warnings.append(
        PluginWarning(
            plugin_name=plugin_name,
            message=(
                f"Hook rule under '{event_name}' has non-object matcher "
                f"of type {type(matcher).__name__}; matcher must be an object "
                f"like {{\"trigger\": \"manual|auto\"}} — rule skipped."
            ),
            field="hooks",
        )
    )
    continue  # skip rules.append entirely; the rule does not load.

rules.append(
    ParsedHookRule(
        event=event_name,
        hook_type=hook_type,
        # ... matcher=matcher (already validated as dict-or-None above)
    )
)
```

**Why drop-the-rule instead of `matcher = None`.** The runtime contract is `None` ≡ "no matcher, fire on every event". A user who wrote `"matcher": "auto"` clearly intended to *filter* events — silently turning that into "match everything" is the opposite of their intent. Dropping the rule is the conservative read: the host sees a parser warning, the rule does not run at all, and the host has the information to fix the config.

**Runtime guard at the top of `_matches` is now true defense-in-depth, not the primary line of defense.** Once the parser drops malformed rules, the `isinstance(rule.matcher, dict)` check at runtime only fires if some future code path constructs a `ParsedHookRule` directly (bypassing the loader). It is still worth keeping — but the parse-time drop is what hosts should rely on for visibility.

**Matched-rule counting (Phase A emit-gate dependency).** The Phase A `PLUGIN_HOOK_FIRED` payload (A5) carries `matched_rule_count` and gates the emit on `matched_rule_count > 0`, but the Phase A `dispatch_stop` / `dispatch_pre_compact` signatures above return `list[HookAttachmentRecord]` — and the attachment count is a poor proxy for the rule count (see "Why not `len(attachments)`" below for the failure modes). To expose the count without changing the lifecycle dispatch return type, add a small public utility on `PluginHookDispatcher`:

```python
def select_matching_rules(
    self, event: str, payload: dict[str, Any], rules: list[ParsedHookRule],
) -> list[ParsedHookRule]:
    """Canonical Stop / PreCompact selection filter
    (event + is_supported + _matches). Callers use this both to count
    matched rules for the A5 emit gate and to feed an already-filtered
    list to the corresponding `dispatch_*` method.

    See "Parity with `_dispatch_lifecycle`" below for why we use
    `is_supported` rather than the lifecycle runner's literal
    `hook_type == "command"` check, and why the two expressions agree
    in practice for Stop / PreCompact."""
    return [
        r for r in rules
        if r.event == event and r.is_supported and self._matches(r, payload)
    ]
```

The Phase A chat-loop helpers (`_dispatch_stop` / `_dispatch_pre_compact`, see A4) use it in a four-step flow:

1. Build the Claude-flat payload via `ClaudeHookPayloadAdapter.build_stop(...)` / `build_pre_compact(...)`.
2. `matched = dispatcher.select_matching_rules(<event>, payload, agent._plugin_hook_rules)`.
3. **If `len(matched) == 0`, return early without invoking `dispatch_*` and without emitting `PLUGIN_HOOK_FIRED`** — this is the no-emit gate referenced from A5.
4. Otherwise, call the corresponding lifecycle dispatch — `dispatcher.dispatch_stop(payload=payload, rules=matched)` for Stop, `dispatcher.dispatch_pre_compact(payload=payload, rules=matched)` for PreCompact (passing the filtered list keeps the dispatcher's internal re-filter a no-op for either event) — and emit `PLUGIN_HOOK_FIRED` with `matched_rule_count=len(matched)`.

**Parity with `_dispatch_lifecycle`.** The lifecycle runner at `agentao/plugins/hooks.py:381` filters by event + `hook_type == "command"` + `_matches`. `is_supported` here is a strict superset (it accepts `hook_type in {"command", "prompt"}`), so the two expressions could in principle diverge for events that accept prompt-type rules — `select_matching_rules` is **not** a literal copy of `_dispatch_lifecycle`'s in-loop filter. For Stop / PreCompact specifically the divergence is closed by A1's per-event hook-type rejection: a prompt-type rule under either event is dropped at parse time AND flips `is_supported` to `False` at runtime, so on every Stop / PreCompact rule the loader produces, `is_supported` and `hook_type == "command"` agree. We keep `is_supported` (rather than hard-coding `hook_type == "command"`) so this utility stays consistent with Agentao's general "is this rule runnable at all?" predicate, and a future event that legitimately accepts prompt-type rules does not need to fork a new selection filter. The "canonical Stop / PreCompact selection filter" framing in the docstring is the authoritative spec; the rough parity with `_dispatch_lifecycle` is incidental and bridged by A1 — implementers should rely on A1's parse-time rejection (and the runtime `is_supported` check it backs) rather than on the lifecycle runner's exact filter shape.

**Phase B continuity.** When `dispatch_stop` upgrades to return `StopHookResult` (B2), the result also carries `matched_rule_count` as defense-in-depth — set inside the dispatcher from the same event/is_supported/matcher filter. The helper still pre-computes via `select_matching_rules` so the no-emit early-return happens before `dispatch_stop` is called at all (no subprocess fork, no transport churn). The two counts are guaranteed to agree because both branches use the same filter expression; if a future refactor diverges them, B2's `matched_rule_count` is the dispatcher-side authority.

**Why not `len(attachments)`.** A Stop command hook that exits 0 with empty stdout produces a single `hook_success` attachment, so attachment count happens to align with rule count *in that case* — but the alignment is incidental. A hook emitting multiple `additionalContext` items inflates the attachment count above the rule count; conversely, a future refactor that drops the `hook_success` attachment for clean exits deflates it to zero. The rule count is what the gate actually wants ("did any of our hooks fire on this event?"), and `select_matching_rules` is the only canonical source.

**Per-event matcher dialect.** PreCompact's `trigger` matcher is **regex (`re.fullmatch`)** to match Claude Code; existing `toolName` matchers continue to use the glob helper. We do not unify the two dialects globally — that would break existing Agentao hook configs that use `*` glob patterns against `toolName`. The asymmetry is documented in the matrix (matcher rows) and tested in A6.

The asymmetry between Stop / PreCompact (flat payload + regex matcher for PreCompact) and the other events (envelope payload + glob matcher) is intentional in this plan — only the two new events need to be Claude-compatible; rewriting every adapter method *and* every matcher dialect is a separate refactor (see compatibility matrix).

### A3. Payload adapter — Claude Code flat snake_case for Stop / PreCompact

Extend `ClaudeHookPayloadAdapter` (`agentao/plugins/hooks.py:213+`) with `build_stop` and `build_pre_compact`. **These two builders return Claude Code's flat snake_case top-level schema**, *not* the existing agentao `{event, data}` envelope — so a hook script reading from stdin gets exactly the keys it would get under Claude Code.

**Why the deviation from sibling builders.** `build_user_prompt_submit` / `build_session_start` / `build_pre_tool_use` etc. return `{"event": "...", "data": {camelCase}}`. Adopting flat snake_case across the whole adapter would break every existing event consumer (and `_matches`'s `data["toolName"]` path) — a cross-cutting refactor that is out of scope here. Stop and PreCompact are net-new events; we get to pick their wire shape, so they are made Claude-compatible from the start. The `_matches` extension in A2 handles the dual shape.

**Common fields (top-level, snake_case) — both events:**

| Field | Source | Notes |
|---|---|---|
| `hook_event_name` | `"Stop"` or `"PreCompact"` | required by Claude common-input spec |
| `session_id` | `agent._session_id` | empty string when unset |
| `transcript_path` | `null` (Open Question 1) | Agentao has no single canonical transcript file today; option-(a) of OQ1 stays |
| `cwd` | `str(agent.working_directory)` | |
| `permission_mode` | `agent.permission_engine.active_permissions().mode` (`"read-only" \| "workspace-write" \| "full-access" \| "plan"`) | falls back to `"workspace-write"` if engine is None |

**Stop-specific fields (top-level):**

| Field | Value | Source |
|---|---|---|
| `stop_hook_active` | `bool` | `False` in Phase A; Phase B sets `True` on the 2nd-and-subsequent dispatches within one `chat()` invocation so a hook can detect it was re-entered after a prior `force_continue` |
| `last_assistant_message` | `str` | the about-to-be-final assistant text — natural turn: `assistant_content`; max-iter: `assistant_content_max` (B3 builds both before dispatch). This is the field that lets a Stop hook review the answer without parsing a transcript. |
| `turn_end_reason` | `"final_response" \| "max_iterations" \| "doom_loop"` | Agentao-specific addition for the boundary disambiguation introduced in pass 3 (and extended in pass 15 to cover the doom-loop break — see Semantics); coexists with the Claude common fields. Hosts that only care about Claude parity may ignore it. |

**PreCompact-specific fields (top-level):**

| Field | Value | Notes |
|---|---|---|
| `trigger` | `"manual" \| "auto"` | Claude Code parity. **Always `"auto"` in this plan** — Agentao has no `/compact` CLI surface, so `"manual"` is never emitted. The matcher in A2 still accepts `"manual"` patterns; they will simply never match. |
| `custom_instructions` | `str` | Claude Code parity. Always empty (no manual trigger). |
| `compaction_type` | `"microcompact" \| "full" \| "minimal_history"` | Agentao-specific subdivision under `trigger="auto"`, mirroring the `compression_type` argument to `_emit_context_compressed` (see `chat_loop.py:534, 561`). Hosts wanting to distinguish heuristic compaction from post-failure recovery read this. Not part of Claude Code's documented schema. |
| `reason` | `str` | Agentao-specific free-form discriminator. **Mirrors the `reason=` argument already passed to `_emit_context_compressed`** at each emit site, so pre/post compaction audit events agree without normalization. Stable values used by A4 (verified against `chat_loop.py` lines 413, 443, 536, 563): `"microcompact_threshold"`, `"compression_threshold"`, `"api_overflow"`, `"api_overflow_after_compression"`. |

**Builder shape (illustrative):**

```python
def build_stop(
    self, *, session_id, cwd, last_assistant_message,
    stop_hook_active,
    turn_end_reason: Literal["final_response", "max_iterations", "doom_loop"],
    permission_mode,
) -> dict[str, Any]:
    return {
        "hook_event_name": "Stop",
        "session_id": session_id or "",
        "transcript_path": None,  # OQ1 (a)
        "cwd": str(cwd or Path.cwd()),
        "permission_mode": permission_mode or "workspace-write",
        "stop_hook_active": bool(stop_hook_active),
        "last_assistant_message": last_assistant_message or "",
        "turn_end_reason": turn_end_reason,
    }

def build_pre_compact(
    self, *, session_id, cwd, compaction_type, reason, permission_mode,
) -> dict[str, Any]:
    return {
        "hook_event_name": "PreCompact",
        "session_id": session_id or "",
        "transcript_path": None,
        "cwd": str(cwd or Path.cwd()),
        "permission_mode": permission_mode or "workspace-write",
        "trigger": "auto",
        "custom_instructions": "",
        "compaction_type": compaction_type,
        "reason": reason,
    }
```

### A4. Emit sites in `chat_loop`

`agentao/runtime/chat_loop.py`. Every site goes through a new `self._dispatch_stop(...)` / `self._dispatch_pre_compact(...)` helper that mirrors the shape of `_dispatch_user_prompt_submit` but uses the side-effect-only return list. The Stop helper threads `assistant_content` (already in scope at each site) into the `last_assistant_message` payload field; both helpers read `permission_mode` from `agent.permission_engine.active_permissions().mode` (or `"workspace-write"` when the engine is absent). Discriminator fields per site:

**Stop** (three sites — all immediately *before* the about-to-be-final assistant message is committed; see B3 for the rewrite that moves `agent.messages.append(final_msg)` to after dispatch):

| Site | `chat_loop.py` line | `turn_end_reason` | `last_assistant_message` source |
|---|---|---|---|
| Natural turn end (no further `tool_calls`) | ~306 (final-answer `else` arm of the iteration loop) | `"final_response"` | `assistant_content` |
| Max-iterations exit (after `on_max_iterations` returns `"stop"`) | ~185 (inside the `else: # "stop"` arm — see B3 for the pinned location) | `"max_iterations"` | `assistant_content_max` (built by B3 inside the same arm) |
| Doom-loop break (`if doom_triggered: break`) | ~271-272 (inside the tool-call branch, immediately after `agent.messages.extend(tool_results)`) | `"doom_loop"` | `assistant_content_doom` — usually empty content (the assistant_message that produced the offending tool calls) so it falls back to `"Tool execution halted by doom-loop detection."` |

**`stop_hook_active` wiring (Phase B).** The `_dispatch_stop` helper computes `stop_hook_active = (self._stop_reentries > 0)` from the chat-loop instance counter introduced in B4. Phase A always passes `stop_hook_active=False` (counter is unused, `_stop_reentries` is initialized to 0 each `chat()` call). The flip from `False → True` happens automatically once Phase B's `force_continue` path increments `_stop_reentries` (see B3 wiring at all three force_continue sites — natural-turn / max-iter / doom-loop). A hook script written against Claude Code's documented `stop_hook_active` semantic — "True if I am being re-entered after my own previous force-continue" — sees the matching value without any extra plumbing on the host side. The B6 test `test_hooks_stop_hook_active_reentry.py` validates the false→true transition end-to-end.

**PreCompact** (four sites — every code path that mutates `agent.messages` for context-size reasons; fire *before* the mutation):

| Site | `chat_loop.py` line | `trigger` | `compaction_type` | `reason` |
|---|---|---|---|---|
| `_maybe_microcompact` (top, after `needs_microcompaction(...)` returns `True`) | ~396 | `"auto"` | `"microcompact"` | `"microcompact_threshold"` (mirrors `chat_loop.py:413`) |
| `_maybe_full_compress` (top, before mutation) | ~422 | `"auto"` | `"full"` | `"compression_threshold"` (mirrors `chat_loop.py:443`) |
| `_call_llm_with_overflow_recovery` — first forced compression after API context-overflow error | ~528 | `"auto"` | `"full"` | `"api_overflow"` (mirrors `chat_loop.py:536`) |
| `_call_llm_with_overflow_recovery` — minimal-history truncation after the second consecutive overflow | ~557 (`agent.messages = agent.messages[-2:]`) | `"auto"` | `"minimal_history"` | `"api_overflow_after_compression"` (mirrors `chat_loop.py:563`) |

The minimal-history site is post-failure recovery: the API just rejected a freshly-compacted context, so the loop slices history down to the last 2 messages before the third LLM call. It *is* a compaction event from the host's point of view (history is about to be lost), so Phase A fires `PreCompact` there. The `compaction_type="minimal_history"` discriminator lets hosts distinguish benign compaction from emergency truncation; hosts that snapshot context for forensic replay want both. Phase A is side-effect-only, so emitting from inside the exception handler does not introduce a new failure mode (a hook crash is logged and swallowed). Field name is snake_case to match A3's Claude-flat top-level shape — the internal `_emit_context_compressed` argument is `compression_type`, but the on-wire payload key for hooks is `compaction_type`.

### A5. Replay-event projection

Reuse the existing `EventType.PLUGIN_HOOK_FIRED` channel (`agentao/transport/events.py:39`). For Phase A, `outcome="allow"` is the only label (no control semantics yet).

**Phase A emit payload (minimum schema).** The on-wire `event.data` for `PLUGIN_HOOK_FIRED` under Phase A:

- **Stop:** `{hook_name: "Stop", outcome: "allow", turn_end_reason: <from A4>, at_max_iter: bool, matched_rule_count: int}`. `at_max_iter` is `True` only at the max-iter site; `turn_end_reason` is one of `"final_response" | "max_iterations" | "doom_loop"` matching the A4 site table. **`matched_rule_count` is the count of `Stop` rules selected for dispatch** in this turn — i.e. `len(dispatcher.select_matching_rules("Stop", payload, agent._plugin_hook_rules))` after the `event` + `is_supported` + `_matches` filter. **This is the selection count, not the execution count**: B2's run loop short-circuits on `blocking_error` / `force_continue`, so when 3 rules are selected and the first short-circuits, `matched_rule_count` still reports `3`. If a future host needs the actual run-count, add a sibling `executed_rule_count` field rather than redefining this one. When `matched_rule_count == 0`, **no event is emitted at all** (avoids replay noise on turns with zero registered Stop hooks). This is the gate the A6 test `test_hooks_stop_no_emit_when_no_stop_rules.py` pins.
- **PreCompact:** `{hook_name: "PreCompact", outcome: "allow", compaction_type: <from A4>, trigger: "auto", matched_rule_count: int}`. Same `matched_rule_count == 0` no-emit gate. `compaction_type` is one of `"microcompact" | "full" | "minimal_history"` per the A4 site table; `trigger` is always `"auto"` under this plan.

Phase A → Phase B is **additive**: the Phase B Stop emit dict (B7) extends this schema with `added_context_count` and `suppress_output` to support the five-outcome matrix; no key renames, no removals. PR-1 implementers can satisfy A6 directly from the schema above without forward-referencing B7.

**Emit ownership.** Phase A emits *inside* the helper (`_dispatch_stop` / `_dispatch_pre_compact`) because `outcome` is unconditionally `"allow"`. Phase B (B7) splits ownership for **Stop only**: the helper returns `StopHookResult`, the chat-loop call site computes one of five outcome labels (`allow | block | continue | continue_at_max_iter | reentry_capped`) based on its branching context, and a dedicated `_emit_stop_hook_fired` helper does the emit. PreCompact stays helper-internal under both phases because it has no control semantics.

**Visibility scope.** This is a **transport / replay** event, not a host-public event. The `agentao.host.EventStream` discriminated union currently covers `ToolLifecycleEvent | SubagentLifecycleEvent | PermissionDecisionEvent` (`agentao/host/events.py:53`, `agentao/host/models.py:157`) and does **not** include plugin-hook events. Hosts that consume `Agentao.events()` will not see `Stop` / `PreCompact` from this plan; only the transport/replay layer (and tests reading the transport queue) will. Promoting plugin-hook events into the host public model is a separate Public-Event-Promotion ticket and is explicitly out of scope (see "Out of scope").

### A6. Tests

**Attachment caveat (read first).** `_dispatch_lifecycle` returns `list[HookAttachmentRecord]`, but **all existing call sites discard that list** — see `agentao/runtime/tool_executor.py::_dispatch_pre_tool_hook` (line ~591, drops return) and `agentao/cli/session.py::_dispatch_session_start_hooks` (line ~79, drops return). There is no shared "attach to turn" wiring today. Phase A inherits this contract: the chat-loop helper that fires `Stop` / `PreCompact` does **not** consume the attachment list either, so attachments are observable only at the dispatcher boundary (and via the transport `PLUGIN_HOOK_FIRED` outcome label, which carries verdict + counts but not attachment payload). Surfacing attachments to the conversation/replay layer is a cross-cutting follow-up that should change every lifecycle event uniformly; pulling it into this plan would balloon scope, so it is tracked separately as `PLUGIN_HOOK_ATTACHMENT_PIPELINE_PLAN` (out of scope here).

Add under `tests/`:

- `test_hook_dispatcher_stop.py` — invoke `dispatcher.dispatch_stop(...)` directly with a matching command rule; assert the returned list contains a `hook_success` `HookAttachmentRecord`. The dispatcher is the authoritative observer of attachments under Phase A.
- `test_hooks_stop_event.py` — register a `Stop` command hook on a real chat turn, assert: hook subprocess invoked, transport emits `PLUGIN_HOOK_FIRED` with `hook_name="Stop"`, `outcome="allow"`, **`turn_end_reason="final_response"`**, and `at_max_iter=False`; final answer unchanged. The `turn_end_reason` assertion guards the B7 disambiguation contract — without it, a refactor that drops the field from `_emit_stop_hook_fired`'s emit dict would silently break dashboard consumers (the field's only purpose on the transport channel is to disambiguate `outcome="continue"` across emit sites).
- `test_hooks_pre_compact_event.py` — force the microcompact threshold, assert the hook fires *before* mutation, assert messages are still compacted regardless of hook outcome (side-effect-only contract).
- **`test_hooks_stop_payload_claude_shape.py`** — capture the JSON the Stop hook subprocess actually receives on stdin; assert it has top-level keys exactly `{hook_event_name, session_id, transcript_path, cwd, permission_mode, stop_hook_active, last_assistant_message, turn_end_reason}` and *no* `data` key. Assert `last_assistant_message` round-trips the `assistant_content` from the test fixture.
- **`test_hooks_pre_compact_payload_claude_shape.py`** — same idea for PreCompact: top-level keys exactly `{hook_event_name, session_id, transcript_path, cwd, permission_mode, trigger, custom_instructions, compaction_type, reason}`, `trigger == "auto"` in every emit site under the matrix.
- **`test_hooks_pre_compact_matcher_trigger.py`** — register four PreCompact rules and assert all four firing decisions are correct: (a) `matcher: {"trigger": "manual"}` does **not** fire (literal mismatch — only `"auto"` is ever emitted); (b) `matcher: {"trigger": "auto"}` fires; (c) `matcher: {"trigger": "manual|auto"}` fires (this is the Claude Code parity case — alternation regex); (d) `matcher: {"trigger": ".*"}` fires. This proves the matcher is regex (`re.fullmatch`), not the existing glob helper, and would have failed against `_glob_match` for cases (c) and (d).
- **`test_hooks_pre_compact_matcher_non_dict_guard.py`** — three sub-cases. (a) parse a `hooks.json` with `"matcher": "auto"` (string instead of object): assert the parser emits a `PluginWarning` (not a raw string — the warnings list is typed `list[PluginWarning]`) naming the offending event/type pair, and assert the resulting `rules` list is empty (the rule is **dropped**, not loaded with a normalized matcher). (b) same but `"matcher": ["auto"]` (list): same expectation — `PluginWarning` emitted, rule dropped. (c) bypass the parser, directly construct a `ParsedHookRule(matcher="auto")` and call `_matches`: assert it returns `False` (the runtime guard treats non-dict as no-match) and emits a runtime warning. (a) and (b) verify the parse-time drop; (c) verifies the runtime defense-in-depth that protects against any future code path that constructs `ParsedHookRule` outside the loader. **Critically**, (a) and (b) must assert the rule does **not** load — earlier drafts of this plan suggested normalizing the bad matcher to `None`, which (because `_matches` returns `True` on `None`) would have silently turned a misconfigured filter into a match-everything rule.
- **`test_hooks_stop_precompact_reject_prompt_type.py`** — feed `HookConfigParser.parse_dict` the real `hooks.json` shape (outer event name keys + entries' inner `"type"` field — see `agentao/plugins/hooks.py:63-78` docstring), one for each event:

  ```python
  raw_stop      = {"hooks": {"Stop":       [{"type": "prompt", "prompt": "..."}]}}
  raw_precompact = {"hooks": {"PreCompact": [{"type": "prompt", "prompt": "..."}]}}
  ```

  Assert for each: (a) the returned `rules` list is **empty** (rule dropped at parse time, not loaded); (b) the returned `warnings` list contains a `PluginWarning` whose message names both the offending event and the offending hook type (the per-event rejection branch added by A1, **not** the generic `"Unknown hook type"` branch — distinguishable by message text); (c) `field == "hooks"`. Also assert that registering an empty rule list on the dispatcher and emitting `Stop` / `PreCompact` does not invoke any subprocess.

  **Defense-in-depth sub-case.** Bypass the parser, directly construct a `ParsedHookRule(event="Stop", hook_type="prompt", ...)` and assert `rule.is_supported is False` (the runtime `is_supported` extension catches any future code path that builds rules outside the loader). Same for `event="PreCompact"`. This pins the two-layer defense (parse-time drop + runtime predicate) the matrix and A1 both reference.

  Without the explicit per-event warning text assertion, this test would pass against the previous draft of A1 (which had a generic `"Unknown hook type"` fallback that already drops the rule); the test must prove the *new* per-event branch fires.
- **`test_hooks_stop_no_emit_when_no_stop_rules.py`** — pins the Phase A no-emit gate from A5 (driven by A2's `select_matching_rules`). Three sub-cases. (a) `agent._plugin_hook_rules == []` (no plugin rules at all): run a real chat turn to natural completion; assert **zero** `PLUGIN_HOOK_FIRED` events with `hook_name == "Stop"`. (b) `agent._plugin_hook_rules == [<UserPromptSubmit rule>]` (rules exist but none target Stop): same assertion — no `Stop`-tagged event. The early-return at `_dispatch_user_prompt_submit:332-333` cannot catch this case alone; the Stop gate must be a separate filter on `select_matching_rules("Stop", ...)`. (c) Positive control — `agent._plugin_hook_rules == [<Stop rule>]`: assert exactly one `PLUGIN_HOOK_FIRED` with `hook_name == "Stop"`, `outcome == "allow"`, `matched_rule_count == 1`, and `turn_end_reason == "final_response"`. The `matched_rule_count == 1` assertion in (c) is what blocks a future refactor from swapping in `len(attachments)` (which would happen to equal 1 for a clean exit-0 hook but diverges in every other case — see A2's "Why not `len(attachments)`"). This test ships in PR-1; B7 reuses it without modification under Phase B because both `select_matching_rules` (helper-side) and `StopHookResult.matched_rule_count` (dispatcher-side, B1) report the same number.
- **`test_hooks_pre_compact_no_emit_when_no_rules.py`** — sibling gate test for PreCompact (A5's no-emit clause applies symmetrically). Three sub-cases. (a) no plugin rules at all, force a microcompact: assert zero `PLUGIN_HOOK_FIRED` with `hook_name == "PreCompact"`. (b) rules exist but none target PreCompact (e.g., a Stop rule): same. (c) Positive control — `[<PreCompact rule>]`: assert exactly one `PLUGIN_HOOK_FIRED` with `hook_name == "PreCompact"`, `outcome == "allow"`, `matched_rule_count == 1`, `compaction_type == "microcompact"`, `trigger == "auto"`. PreCompact needs its own gate test because A6's existing `test_hooks_pre_compact_event.py` registers a rule unconditionally and never exercises the empty-matched-rules branch. Carries the same robustness guarantee as the Stop test against an attachment-count regression.
- Extend any existing `test_hook_dispatcher.py` with `Stop` / `PreCompact` rule-matching cases.

### A7. Docs

- `docs/history/implementation/plugin-system-mvp/phase-6-session-tool-hooks-and-cli.md` — append the two new events to the supported list.
- `docs/reference/configuration.md` — if it enumerates hook events anywhere, sync the list.
- `CLAUDE.md` (project root) — only if the existing hook-events list is enumerated there.

---

## Phase B — Control-aware gate

### B1. New result model

`agentao/plugins/models.py`:

```python
@dataclass
class StopHookResult:
    """Aggregated result of all hooks for a single Stop event.

    Field semantics intentionally differ from UserPromptSubmitResult:
    - blocking_error: same shape — surface a hook failure to the user.
    - force_continue: when True, the loop refuses to end this turn;
      `follow_up_message` is appended to the conversation as if the
      user had said it, and the loop reissues one more LLM call.
    - additional_contexts: appended as system-reminder text on the
      assistant's final answer (rare; observability case).
    """
    # --- meaningful Stop-semantics fields, consumed by chat-loop wiring ---
    blocking_error: str | None = None
    force_continue: bool = False
    follow_up_message: str | None = None
    additional_contexts: list[str] = field(default_factory=list)
    stop_reason: str | None = None
    # Claude-Code-output parity fields (see compatibility matrix).
    # `suppress_output`: dual semantic.
    #   - Claude-parity meaning: hide raw hook stdout from any
    #     user-visible / debug-log channel. **In Agentao today this
    #     is vacuous**: hook stdout is never projected onto
    #     `PLUGIN_HOOK_FIRED` (the emit carries verdict + counts only —
    #     `outcome`, `matched_rule_count`, `added_context_count`,
    #     `suppress_output`, etc., see B7 helper) and the chat-loop
    #     does not render hook stdout into the user-visible transcript.
    #     The field is still recorded on the result and emitted on
    #     `PLUGIN_HOOK_FIRED.suppress_output` for replay fidelity, but
    #     no current display path consumes it. If a future Agentao
    #     surface starts surfacing hook stdout, that surface must
    #     consult `suppress_output` to stay Claude-compatible. Matrix
    #     row "JSON `suppressOutput`" is 🟡 specifically because of
    #     this vacuous-today-but-recorded-faithfully posture.
    #   - Agentao extension (🟡 in the matrix, not strict Claude
    #     parity): when True, B3 ALSO omits the
    #     `<stop-hook>...</stop-hook>` echoing of `additional_contexts`
    #     on the assistant's final answer. The contexts are still
    #     recorded in transport `PLUGIN_HOOK_FIRED.added_context_count`
    #     so replay does not lose count, but the user-visible answer
    #     stays clean.
    #   Hosts that want strict Claude semantic (suppressOutput affects
    #   stdout only, not additionalContext echo) should not pair the
    #   flag with `additionalContext` on the same hook output — keep
    #   them on separate hook invocations.
    # `system_message`: pulled from JSON `systemMessage`. Treated as a
    #     normal addition to `additional_contexts` (it shares the same
    #     channel; see B2). Kept as a separate field for replay
    #     fidelity.
    suppress_output: bool = False
    system_message: str | None = None

    # --- runner-internal scratch / legacy-tolerance fields ---
    # B2 forks a Stop-specific runner (`_run_stop_command_hook`) and
    # parser (`_parse_stop_command_output`); StopHookResult does NOT
    # share code with `_run_command_hook` / `_parse_command_output`.
    # The two fields below carry runner-internal state that the
    # chat-loop wiring (B3) never reads directly.
    #
    # `messages`: HookAttachmentRecord list produced by
    #     `_run_stop_command_hook` (timeout warning, exit-2 attachment,
    #     other-nonzero warning, JSON-path "hook_success", etc.) and
    #     returned to the dispatcher. Under A6's attachment caveat the
    #     dispatcher boundary is the only observation point today.
    # `prevent_continuation`: parser-write target for the
    #     Agentao-internal legacy `preventContinuation: true` JSON
    #     field — kept solely so a hook script authored for the
    #     UserPromptSubmit shape doesn't crash the Stop runner. B2's
    #     parser table translates a `preventContinuation: true` write
    #     into `force_continue=True` (subject to the `continue: false`
    #     precedence rule); the scratch field is never consumed by
    #     chat-loop wiring.
    messages: list[HookAttachmentRecord] = field(default_factory=list)
    prevent_continuation: bool = False

    # --- replay-emission gate ---
    # `matched_rule_count`: how many Stop rules from the plugin ruleset
    #     were SELECTED for dispatch in this turn (set by B2's
    #     `dispatch_stop` from `len(self.select_matching_rules("Stop",
    #     payload, rules))`). This is the **selection count**, not the
    #     execution count: the run loop in `dispatch_stop` short-circuits
    #     on `blocking_error` / `force_continue`, so a 3-rule selection
    #     where the first rule short-circuits still reports `3` here.
    #     The field name is preserved (rather than renamed to
    #     `selected_rule_count`) for replay-stream backward compatibility;
    #     if a future host needs the actual run-count, add a sibling
    #     `executed_rule_count` rather than redefining this one. B7's
    #     `_emit_stop_hook_fired` gates emission on this — when it is 0,
    #     no `PLUGIN_HOOK_FIRED` is sent. Mirrors
    #     `_dispatch_user_prompt_submit`'s "early-return when
    #     `agent._plugin_hook_rules` is empty" behavior at
    #     `chat_loop.py:332-333`, but also catches the case where rules
    #     exist but none target Stop (we do NOT want to emit
    #     `hook_name="Stop", outcome="allow"` from a turn that selected
    #     zero Stop hooks — that would make the replay channel both
    #     noisy and semantically false).
    matched_rule_count: int = 0
```

Naming note: `force_continue` (not `prevent_continuation`) because `Stop` fires *when the loop is about to end* — the host wants to prevent that ending, i.e. force continuation. Reusing `prevent_continuation` as the *meaningful* signal would invert the field's polarity relative to its name in `UserPromptSubmitResult` and mis-align with Claude Code's `{"continue": false}` JSON convention. The scratch `prevent_continuation` field above exists only to absorb a `preventContinuation: true` written by a misconfigured hook script — it is **never** consumed by chat-loop wiring; B2 translates the parser's `preventContinuation` writes into `force_continue` (subject to the `continue: false` precedence rule pinned in B2's invariants).

### B2. Control-aware dispatcher path for `Stop` — Stop-specific runner with Claude Code semantics

The previous draft suggested reusing `_run_command_hook` and `_parse_command_output` (the UserPromptSubmit runner). That **does not** give Claude Code parity, because Claude Code defines exit code 2 on Stop as "block the stop and feed stderr back as the follow-up reason," and the existing runner treats nonzero + empty stdout as a benign warning attachment (`hooks.py:520-533`). Reusing it would silently drop the most common control signal a Claude Stop hook produces.

**Fork a Stop-specific runner.** `dispatch_stop` calls `_run_stop_command_hook`, not `_run_command_hook`:

```python
def dispatch_stop(self, *, payload, rules) -> StopHookResult:
    result = StopHookResult()
    # Re-use A2's select_matching_rules so the helper-side and
    # dispatcher-side counts come from the same filter expression
    # (event + is_supported + _matches). Idempotent on already-filtered
    # input — B7's helper pre-filters, so this call is a no-op there;
    # for direct callers (e.g. test_hook_dispatcher_stop.py) it is the
    # only filter applied.
    stop_rules = self.select_matching_rules("Stop", payload, rules)
    # Set the replay-gate field BEFORE the run loop so its value is
    # the SELECTION count, not the execution count: when a rule
    # short-circuits via blocking_error / force_continue and we break
    # out of the loop below, this still reports the full selected
    # total (see B1 docstring for the contract — selection count
    # ≠ run count by design; rename `executed_rule_count` if a host
    # ever needs the latter).
    result.matched_rule_count = len(stop_rules)
    for rule in stop_rules:
        if rule.hook_type == "command":
            self._run_stop_command_hook(rule, payload, result)
        # prompt-type hooks not supported for Stop in this phase
        if result.blocking_error or result.force_continue:
            break
    return result

def _run_stop_command_hook(
    self, rule, payload, result: StopHookResult,
) -> None:
    """Stop-specific runner — honors Claude Code exit code 2 + JSON contract."""
    if not rule.command:
        return
    payload_json = json.dumps(payload)
    try:
        proc = subprocess.run(
            rule.command, input=payload_json, capture_output=True, text=True,
            timeout=rule.timeout, shell=True, cwd=str(self._cwd),
        )
    except subprocess.TimeoutExpired:
        result.messages.append(_make_attachment(
            "hook_success", {"warning": f"Hook timed out after {rule.timeout}s"},
            hook_name=rule.command, hook_event="Stop",
        ))
        return
    except OSError as exc:
        logger.warning("Stop hook failed to run: %s (%s)", rule.command, exc)
        return

    # Exit code 2 — Claude Code blocking signal. stderr is the reason.
    if proc.returncode == 2:
        stderr = (proc.stderr or "").strip() or "Stop hook blocked via exit 2"
        result.force_continue = True
        result.follow_up_message = stderr
        result.stop_reason = stderr
        result.messages.append(_make_attachment(
            "hook_stop_blocked_via_exit2",
            {"stderr": stderr[:500]},
            hook_name=rule.command, hook_event="Stop",
        ))
        return

    # Other nonzero with no JSON — same warning behavior as the existing
    # runner; not a control signal.
    if proc.returncode != 0 and not (proc.stdout or "").strip():
        result.messages.append(_make_attachment(
            "hook_success",
            {"warning": f"Hook exited with code {proc.returncode}",
             "stderr": (proc.stderr or "")[:500]},
            hook_name=rule.command, hook_event="Stop",
        ))
        return

    # JSON path — Claude Code Stop output schema.
    self._parse_stop_command_output(proc.stdout, rule, result)
```

**`_parse_stop_command_output` — Claude Code Stop JSON contract.** The runner above hands stdout to a Stop-specific parser. It is a near-copy of `_parse_command_output` minus the UserPromptSubmit-only branches, plus the additional Claude-output fields that Stop honors. **The parser must implement the rows below in evaluation order** — Claude Code documents `continue: false` as taking precedence over any event-specific decision field, so a hook returning `{"continue": false, "decision": "block"}` accepts the stop, it does **not** force a continue.

| Order | JSON field on hook stdout | StopHookResult mapping |
|---|---|---|
| 1 | `continue: false` | **Precedence override.** Set a `continue_false` flag on the parser scratch state and **clear / refuse to set** `force_continue` in any later branch (i.e., `decision: "block"` and `preventContinuation: true` below become no-ops on `force_continue` while still recording the attachment). Outcome: Stop is honored — same as the default Stop semantics. Recorded for replay so dashboards see the field arrived, but the loop already ends the turn. |
| 2 | `continue: true` (default, also when omitted) | no-op |
| 3 | `decision: "block"` + `reason: "<text>"` | If `continue_false` is **not** set: `force_continue=True`, `follow_up_message="<text>"`, `stop_reason="<text>"`. If `continue_false` is set: only record `stop_reason="<text>"` (for replay) and skip `force_continue`. |
| 4 | `stopReason: "<text>"` | `stop_reason="<text>"` (used by `force_continue` follow-up synthesis if no `decision` block); independent of `continue_false`. |
| 5 | `suppressOutput: true` | `suppress_output=True` |
| 6 | `systemMessage: "<text>"` | `system_message="<text>"`, also append to `additional_contexts` |
| 7 | `hookSpecificOutput.additionalContext: <str\|list>` | append to `additional_contexts` (Claude Code's documented Stop additional-context channel) |
| 8 | `additionalContext: <str\|list>` (legacy / top-level) | append to `additional_contexts` (Agentao tolerance for older hook scripts) |
| 9 | `blockingError: "<text>"` | `blocking_error="<text>"` (Agentao internal field; not part of Claude schema, kept for parity with UserPromptSubmit hooks the user may have already authored). Independent of `continue_false` — `blocking_error` ends the turn with an explicit error message, which is consistent with `continue: false`'s "stop" intent. |
| 10 | `preventContinuation: true` | If `continue_false` is **not** set: `force_continue=True`, `stop_reason=data.get("stopReason", "Hook prevented continuation")`, `follow_up_message=data.get("stopReason") or "Stop hook requested continuation"` (Agentao internal field; pathological for Stop). If `continue_false` is set: skip `force_continue`. |
| any path | `messages.append(_make_attachment(...))` |

**Invariants:**
1. Any branch that sets `force_continue=True` also produces a non-empty `follow_up_message` (synthesized from `stopReason` if the hook didn't supply one). B3 additionally falls back to `stop_reason` and a generic default at use time, so the contract is enforced from both sides.
2. Exit code 2 is **always** treated as `force_continue` — this is the Claude Code contract and the first reason this runner exists separately from `_run_command_hook`. Note that exit code 2 is checked *before* the JSON parser runs, so `continue: false` in stdout cannot countermand it. Claude Code documents the same precedence (exit codes are evaluated before JSON output).
3. **`continue: false` overrides any `force_continue`-producing JSON field on the same hook output.** This is row 1's precedence rule and the third reason this runner exists separately. A hook that wants to stop the agent regardless of what other fields say writes `{"continue": false}`.
4. The parser does *not* re-implement UserPromptSubmit-only branches (`prevent_continuation` as the meaningful signal, `additional_contexts` injection into the next user prompt). The scratch `prevent_continuation` field on `StopHookResult` (B1) only exists so a misconfigured hook script doesn't crash the runner.

**`_run_command_hook` is left untouched.** UserPromptSubmit hooks continue to use it; their semantics are unchanged.

**Test impact — A6's `test_hook_dispatcher_stop.py` must be updated in PR-2.** A2 declared `dispatch_stop` returning `list[HookAttachmentRecord]`, and A6 wrote a test that calls `dispatcher.dispatch_stop(...)` and asserts the returned list contains a `hook_success` record. With B2's signature upgrade to `StopHookResult`, that assertion no longer compiles. The PR-2 patch:

1. Updates the test to call the new signature: `result = dispatcher.dispatch_stop(...)` (now `StopHookResult`).
2. Walks `result.messages` for the attachment assertion instead of the bare list (`StopHookResult.messages` is the parser-shared scratch field defined in B1 and carries the same `HookAttachmentRecord` payloads the Phase-A list contained).
3. Adds new sub-cases for the gate-signal fields (`force_continue`, `blocking_error`, `suppress_output`, etc.) that did not exist in Phase A.

This is a **deliberate breaking change** between PR-1 and PR-2 — `dispatch_stop` is internal to Agentao (no host yet calls into it directly), so the breakage is contained. The Sequencing section's PR-2 bullet calls this test rewrite out explicitly so it is not missed during PR-2 review. `dispatch_pre_compact` is **not** affected — PreCompact stays observe-only (B5) and keeps its `list[HookAttachmentRecord]` return type into and beyond PR-2.

### B3. Chat-loop wiring

The existing finalization at `chat_loop.py:294-306` runs `agent.messages.append(final_msg)` *before* `return assistant_content`. A naive Stop dispatch placed after that point leaves a "blocked-but-already-recorded" assistant message in conversation history when the hook returns `blocking_error` — the user sees `[Blocked by Stop hook] ...` but the original answer is still in the transcript and re-fed to the model on the next turn.

Fix: build `final_msg` first, dispatch Stop, then decide what to commit. Replace the bare `return` *and* the `agent.messages.append(final_msg)` with:

```python
else:
    agent.llm.logger.info(f"Reached final response in iteration {iteration}")
    assistant_content = assistant_message.content or ""
    reasoning_content = getattr(assistant_message, "reasoning_content", None)
    final_msg: Dict[str, Any] = {"role": "assistant", "content": assistant_content}
    _attach_reasoning(final_msg, reasoning_content)
    if sanitize_assistant_message(final_msg):
        agent.llm.logger.warning(
            "Sanitised lone surrogates in final assistant message "
            "(iteration %d)", iteration,
        )

    # NOTE: do NOT append final_msg yet — Stop hook may rewrite the
    # content (blocking_error) or extend the turn (force_continue).
    # `_dispatch_stop` builds the payload internally (assistant_content
    # → `last_assistant_message`; `turn_end_reason` → flat top-level
    # key on the Claude-flat payload); the caller does **not** pass a
    # pre-built dict — see B7 for the signature and the helper
    # boundary rationale. The caller is responsible for emitting
    # PLUGIN_HOOK_FIRED with the right outcome label after branching.
    stop_result = self._dispatch_stop(
        agent, assistant_content,
        turn_end_reason="final_response", at_max_iter=False,
    )

    if stop_result.blocking_error:
        blocked = f"[Blocked by Stop hook] {stop_result.blocking_error}"
        final_msg["content"] = blocked
        agent.messages.append(final_msg)
        self._emit_stop_hook_fired(
            agent, outcome="block", at_max_iter=False,
            turn_end_reason="final_response",
            stop_result=stop_result,
        )
        return blocked

    if stop_result.force_continue:
        # Defend against translations that set force_continue=True
        # without populating follow_up_message (e.g. preventContinuation
        # path before B2 synthesizes it). force_continue is authoritative;
        # the follow-up text is synthesized from whichever field has
        # content, with a generic default as last resort.
        follow_up = (
            stop_result.follow_up_message
            or stop_result.stop_reason
            or "Stop hook requested continuation"
        )
        if self._stop_reentries >= self._stop_reentry_cap:
            agent.llm.logger.warning(
                "Stop hook reentry cap (%d) hit; ending turn.",
                self._stop_reentry_cap,
            )
            agent.messages.append(final_msg)
            self._emit_stop_hook_fired(
                agent, outcome="reentry_capped", at_max_iter=False,
                turn_end_reason="final_response",
                stop_result=stop_result,
            )
            return assistant_content
        self._stop_reentries += 1
        agent.messages.append(final_msg)  # preserve the answer being continued from
        agent.messages.append({
            "role": "user",
            "content": f"<system-reminder>Stop hook injected this</system-reminder>\n"
                       f"{follow_up}",
        })
        messages_with_system = [
            {"role": "system", "content": system_prompt}
        ] + agent.messages
        iteration = 0  # honest budget reset for the new sub-turn
        self._emit_stop_hook_fired(
            agent, outcome="continue", at_max_iter=False,
            turn_end_reason="final_response",
            stop_result=stop_result,
        )
        continue

    # Allow path. Optional additional_contexts ride on the assistant's
    # final answer as a system-reminder so the recorded message matches
    # what the user sees — UNLESS the hook returned `suppressOutput: true`,
    # in which case the contexts are still recorded on the transport
    # `PLUGIN_HOOK_FIRED.added_context_count` for replay fidelity but the
    # user-visible answer stays clean.
    #
    # NOTE: this gating is an **Agentao-specific extension** to
    # suppressOutput, not Claude parity. Claude's `suppressOutput`
    # documents only stdout/debug-log suppression; structured
    # `hookSpecificOutput.additionalContext` is a separate channel and
    # not affected by the flag in Claude. We extend the meaning here
    # because the audit-hook use case ("attach a replay note but don't
    # clutter the answer") is real and a separate flag would proliferate
    # configuration surface. See B1 docstring and the matrix's 🟡 row
    # "Agentao extension to suppressOutput".
    if stop_result.additional_contexts and not stop_result.suppress_output:
        extra = "\n".join(
            f"<stop-hook>\n{ctx}\n</stop-hook>"
            for ctx in stop_result.additional_contexts
        )
        final_msg["content"] = f"{assistant_content}\n{extra}"
        assistant_content = final_msg["content"]
    agent.messages.append(final_msg)
    self._emit_stop_hook_fired(
        agent, outcome="allow", at_max_iter=False,
        turn_end_reason="final_response",
        stop_result=stop_result,
    )
    return assistant_content
```

The max-iterations exit's allow path (below) deliberately does **not** echo `additional_contexts` onto `final_msg_max` — at iteration cap the user-visible answer is the assistant's last partial output (or `"Maximum tool call iterations reached."`), and decorating it with hook context is unlikely to be the right UX. If a future host concretely needs that echo at max-iter, it must be gated behind `not stop_result.suppress_output` for parity with the natural-turn path above.

**Max-iterations dispatch site (pinned).** The post-while finalization at `chat_loop.py:308-324` is *not* a viable Stop dispatch site, because `force_continue` cannot re-enter `while True` from outside the loop without a structural rewrite. Pin the dispatch instead **inside the `else: # "stop"` arm of the `iteration >= max_iterations` block at `chat_loop.py:185-186`**, replacing the bare `break`:

```python
if iteration >= max_iterations:
    pending = [...]
    _handler = getattr(agent.transport, "on_max_iterations", None)
    result = _handler(max_iterations, pending) if callable(_handler) else {"action": "stop"}
    action = result.get("action", "stop")
    if action == "continue":
        iteration = 0
    elif action == "new_instruction":
        ...
    else:  # "stop"
        # Build the max-iter final_msg here so we can dispatch Stop
        # before committing to history. Mirrors the natural-turn path.
        assistant_content_max = (
            assistant_message.content if assistant_message else None
        ) or "Maximum tool call iterations reached."
        final_msg_max: Dict[str, Any] = {
            "role": "assistant", "content": assistant_content_max,
        }
        _attach_reasoning(
            final_msg_max,
            getattr(assistant_message, "reasoning_content", None) if assistant_message else None,
        )
        sanitize_assistant_message(final_msg_max)

        stop_result = self._dispatch_stop(
            agent, assistant_content_max,
            turn_end_reason="max_iterations", at_max_iter=True,
        )

        if stop_result.blocking_error:
            blocked = f"[Blocked by Stop hook] {stop_result.blocking_error}"
            final_msg_max["content"] = blocked
            agent.messages.append(final_msg_max)
            self._emit_stop_hook_fired(
                agent, outcome="block", at_max_iter=True,
                turn_end_reason="max_iterations",
                stop_result=stop_result,
            )
            return blocked

        if stop_result.force_continue:
            # Cap check FIRST — symmetric with the natural-turn path.
            # Without this explicit branch, a max-iter cap-hit would
            # silently fall through to the allow path below and emit
            # outcome="allow", masking a pathological hook the same way
            # exit code 2 used to be silently demoted before B2.
            if self._stop_reentries >= self._stop_reentry_cap:
                agent.llm.logger.warning(
                    "Stop hook reentry cap (%d) hit at max-iterations; ending turn.",
                    self._stop_reentry_cap,
                )
                agent.messages.append(final_msg_max)
                self._emit_stop_hook_fired(
                    agent, outcome="reentry_capped", at_max_iter=True,
                    turn_end_reason="max_iterations",
                    stop_result=stop_result,
                )
                return assistant_content_max
            follow_up = (
                stop_result.follow_up_message
                or stop_result.stop_reason
                or "Stop hook requested continuation"
            )
            self._stop_reentries += 1
            agent.llm.logger.warning(
                "Stop hook force_continue at max-iterations; "
                "resetting iteration counter (outcome=continue_at_max_iter)."
            )
            agent.messages.append(final_msg_max)
            agent.messages.append({
                "role": "user",
                "content": (
                    f"<system-reminder>Stop hook injected this</system-reminder>\n"
                    f"{follow_up}"
                ),
            })
            messages_with_system = [
                {"role": "system", "content": system_prompt}
            ] + agent.messages
            iteration = 0
            self._emit_stop_hook_fired(
                agent, outcome="continue_at_max_iter", at_max_iter=True,
                turn_end_reason="max_iterations",
                stop_result=stop_result,
            )
            continue

        # Allow path (no force_continue). Max-iter deliberately does
        # NOT echo additional_contexts onto final_msg_max — see prose
        # below the natural-turn block for rationale.
        agent.messages.append(final_msg_max)
        self._emit_stop_hook_fired(
            agent, outcome="allow", at_max_iter=True,
            turn_end_reason="max_iterations",
            stop_result=stop_result,
        )
        return assistant_content_max
```

**Doom-loop dispatch site (third exit, pinned).** `ToolRunner.execute(...)` returns `(doom_loop_triggered, tool_results)`; when `doom_loop_triggered` is True, the chat loop currently does `if doom_triggered: break` (`chat_loop.py:271-272`) and falls through to the post-while finalization. Pre-pass-15 drafts of B3 missed this third exit and would have orphaned the doom-loop return path when PR-2 deletes the post-while block. Replace the bare `break` with an inline Stop dispatch, mirroring the natural-turn shape with three substitutions:

```python
agent.messages.extend(tool_results)
if doom_triggered:
    # Build final_msg here (mirrors max-iter pattern). assistant_message
    # at this point produced the offending tool_calls — its content is
    # usually empty, so the fallback string carries the user-facing
    # message.
    assistant_content_doom = (
        assistant_message.content if assistant_message else None
    ) or "Tool execution halted by doom-loop detection."
    final_msg_doom: Dict[str, Any] = {
        "role": "assistant", "content": assistant_content_doom,
    }
    _attach_reasoning(
        final_msg_doom,
        getattr(assistant_message, "reasoning_content", None) if assistant_message else None,
    )
    sanitize_assistant_message(final_msg_doom)

    stop_result = self._dispatch_stop(
        agent, assistant_content_doom,
        turn_end_reason="doom_loop", at_max_iter=False,
    )

    # Branch shape is structurally identical to the natural-turn block
    # above (block / cap-hit / continue / allow), with these substitutions:
    #   - `assistant_content` → `assistant_content_doom`
    #   - `final_msg` → `final_msg_doom`
    #   - `at_max_iter=False` (it is NOT max-iter; outcomes use
    #     `continue`, NOT `continue_at_max_iter`)
    #   - **all four `_emit_stop_hook_fired(...)` calls pass
    #     `turn_end_reason="doom_loop"`** — this is what dashboards
    #     parsing PLUGIN_HOOK_FIRED use to disambiguate doom-arm
    #     `continue` from natural-turn `continue` (B7 outcome table)
    #   - WARNING text on cap-hit names "doom-loop" instead of the
    #     natural-turn wording, for triage clarity
    # All four `_emit_stop_hook_fired(...)` call sites are present —
    # block / reentry_capped / continue / allow — same as natural-turn.
    #
    # NOTE on doom-counter reset: when force_continue is honored at
    # the doom site, we do NOT reset ToolRunner's doom-loop counter —
    # that counter belongs to ToolRunner's planner state, this plan
    # has no business mutating it, and re-tripping doom is a
    # reasonable outcome of "host insisted on continuing despite the
    # model misbehaving" (the re-entry cap will eventually win).
    ...  # (full block omitted for brevity — copy the natural-turn shape
         #  with the substitutions above; B6 adds a regression test)
```

If a future refactor extracts `_finalize_with_stop_hook(...)` to absorb the structural duplication across the three sites, that is a Phase-B implementation cleanup, not a spec change — the contract stays "three sites, same StopHookResult handling, distinct `turn_end_reason`."

**Consequence — dead code removal.** With this wiring, **all three** exits from `while True` (natural final-response `return`, max-iter `else: # "stop"` arm `return`, doom-loop `if doom_triggered:` arm `return`) terminate within the loop body, so the post-while finalization at `chat_loop.py:308-324` becomes unreachable. PR-2 deletes those lines. The three surviving exits from the loop are: (1) the natural-turn `return assistant_content` path, (2) the new max-iter `return assistant_content_max` path inside the `else: # "stop"` arm, and (3) the new doom-loop `return assistant_content_doom` path inside the `if doom_triggered:` arm.

### B4. Re-entry cap

Add two fields on the chat-loop instance, reset per `chat()` invocation:

- `_stop_reentries: int` — counter, starts at 0.
- `_stop_reentry_cap: int` — **constructor parameter on the chat loop with a hardcoded default of `3`**. Phase B does **not** read this value from `.agentao/settings.json`. Today the file has only two readers (`embedding/factory.py::_load_settings`, `plan/controller.py::_load_settings` — see `docs/reference/configuration.md` §3); adding a third one-key reader for a knob that nobody tunes until a real host hits the cap is premature configuration surface. If/when a host explicitly asks for runtime tuning, promote the field to a `stop_hook_reentry_max` settings key and route it through whichever shared settings loader exists at that point.

On hitting the cap: emit a `PLUGIN_HOOK_FIRED` transport event with `outcome="reentry_capped"` (a new label) and log a `WARNING`. No attachment record is written here — see the A6 attachment caveat for why this plan does not introduce new attachment-write paths.

### B5. PreCompact gate — **Claude Code compatibility gap, intentionally out of scope**

Claude Code's PreCompact hook supports blocking via exit code 2 and JSON `decision: "block"`. **This plan does not implement that.** PreCompact stays wired through `_dispatch_lifecycle` (side-effect-only) — the same dispatcher path it uses in Phase A. The two ❌ rows in the compatibility matrix labelled "PreCompact blocking" reflect this; this section is the rationale.

Reasons:

- `_maybe_microcompact` and `_maybe_full_compress` mutate `agent.messages` in place; there is no "skip compaction" branch, and the surrounding overflow-recovery code (`_call_llm_with_overflow_recovery`, `chat_loop.py:525-578`) assumes compression eventually succeeds. The minimal-history path (`chat_loop.py:557`) is *itself* a fallback after the regular compaction failed.
- Honoring "host said no" without a "but we still don't fit in context" recovery branch is unsafe: the next LLM call would re-trigger the same overflow, and we'd either loop forever or hit minimal-history truncation which would have happened anyway.
- The compaction code path is exception-recovery sensitive (it lives partly inside an `except` block); putting host-controlled gating inside an exception handler is its own design conversation.

This is a **deliberate compatibility gap**, not a roadmap "next step." A Claude Stop hook script that uses `decision: "block"` will work in Agentao; a Claude PreCompact hook script that uses the same pattern will be observed but its block decision will be discarded. Hosts should not assume Claude PreCompact scripts are gating in Agentao without explicit verification.

When/if a host concretely needs PreCompact gating, the work goes in a follow-up plan (`PRECOMPACT_GATE_PLAN.md`) that resolves the "host said no, still doesn't fit" question first. Out of scope here.

### B6. Tests

The compatibility matrix lists ✅ for `suppressOutput`, `systemMessage`, `hookSpecificOutput.additionalContext`, and exit code 2; pass-5 review correctly observed that these claims need targeted tests. Each ✅ row in the matrix maps to at least one test below.

- `test_hooks_stop_force_continue_decision_block.py` — Stop hook stdout `{"decision": "block", "reason": "needs more work"}`; assert chat loop appends `follow_up_message` (with the system-reminder prefix), reissues one LLM call, and the user sees the result. Also assert the corresponding transport `PLUGIN_HOOK_FIRED` event carries `outcome="continue"`, **`turn_end_reason="final_response"`**, and `at_max_iter=False` — these together pin the natural-turn `(turn_end_reason, outcome)` pair from B7's outcome matrix and prevent a future refactor from silently routing the natural-turn `continue` through the doom-loop or max-iter emit path. Maps to matrix row "JSON `decision: \"block\"` (Stop)".
- `test_hooks_stop_blocking_error.py` — Stop hook returns `blockingError`; assert final answer is the block message and no extra LLM call happens. Maps to the Agentao-internal blockingError tolerance.
- `test_hooks_stop_reentry_cap.py` — pathological hook that always returns `force_continue`; assert the cap fires, a `reentry_capped` event lands on the transport (with `hook_name="Stop"`, `outcome="reentry_capped"`, **`turn_end_reason="final_response"`** — this test runs the cap from the natural-turn path; max-iter and doom-loop cap-hit are pinned in `test_hooks_stop_doom_loop_dispatch.py` and the new max-iter sub-case below), and the loop terminates.
- **`test_hooks_stop_hook_active_reentry.py`** — register a Stop hook that captures the `stop_hook_active` value from each invocation's stdin payload, returns `force_continue` once on the first dispatch, then accepts the stop on the second. Assert: (a) first dispatch's payload has `stop_hook_active == False`; (b) second dispatch's payload has `stop_hook_active == True`; (c) third invocation in a fresh `chat()` call (after counter reset) is back to `False`. This pins the wiring `stop_hook_active = (self._stop_reentries > 0)` and protects the per-`chat()` reset semantic. Without this test, A3's `stop_hook_active` field claim (matrix says ✅) is undefended — pass-9 review correctly flagged that earlier B6 only checked key presence.
- **`test_hooks_stop_exit_code_2.py`** — Stop hook script `exit 2` with stderr `"please retry"`; assert `force_continue=True`, `follow_up_message="please retry"`, and the `<system-reminder>Stop hook injected this</system-reminder>\nplease retry` user message is appended. Also assert the transport `PLUGIN_HOOK_FIRED` event carries `outcome="continue"`, **`turn_end_reason="final_response"`** (the test fires from the natural-turn path), and `at_max_iter=False`. This is the most common Claude Code Stop control signal and was demoted to a benign warning by the previous draft's runner reuse. Maps to matrix row "Exit code 2 — Stop".
- **`test_hooks_stop_suppress_output.py`** — Stop hook returns `{"hookSpecificOutput": {"additionalContext": "audit-note"}, "suppressOutput": true}`; assert `final_msg["content"]` is **unchanged** (no `<stop-hook>` block appended) but the transport `PLUGIN_HOOK_FIRED` event still records `added_context_count == 1`. Companion negative test with `"suppressOutput": false` (or omitted) asserts the `<stop-hook>` block **is** appended. Maps to matrix row "JSON `suppressOutput`" + B1 `suppress_output` field + B3 wiring's new guard.
- **`test_hooks_stop_system_message.py`** — Stop hook returns `{"systemMessage": "ran lint, all clean"}`; assert `result.system_message == "ran lint, all clean"`, the same string is appended to `additional_contexts`, and the user-visible answer carries it as a `<stop-hook>` block (subject to `suppressOutput`). Maps to matrix row "JSON `systemMessage`".
- **`test_hooks_stop_hook_specific_additional_context.py`** — Stop hook returns `{"hookSpecificOutput": {"additionalContext": ["a", "b"]}}` (list form) and a separate run with the string form `{"hookSpecificOutput": {"additionalContext": "c"}}`; assert both forms append to `additional_contexts` correctly and the user-visible answer reflects each element. A third sub-case with the legacy top-level `{"additionalContext": "d"}` (no `hookSpecificOutput` envelope) asserts the Agentao tolerance branch in B2's parser table fires. Maps to matrix row "JSON `hookSpecificOutput.additionalContext` (Stop)".
- **`test_hooks_stop_continue_false_precedence.py`** — three sub-cases that cover the precedence rule documented in B2's parser invariants. (a) Stop hook returns `{"continue": false, "decision": "block", "reason": "ignore me"}`: assert `force_continue == False`, the loop ends the turn, and `stop_reason == "ignore me"` is still recorded for replay. (b) Stop hook returns `{"continue": false, "preventContinuation": true, "stopReason": "noop"}`: assert `force_continue == False` and the turn ends. (c) Stop hook returns `{"continue": false, "blockingError": "lint failed"}`: assert `blocking_error == "lint failed"` and the final answer is the block message — `continue:false` does **not** suppress `blockingError` because both are "stop the turn" intents (B2 invariant 3 spells this out). This test is the regression guard for Claude Code's "common output fields take precedence over event-specific decision fields" contract.
- **`test_hooks_stop_payload_common_fields_precedence.py`** — round-trip the five Claude common input fields (`session_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`) through a real chat turn: assert `permission_mode` reflects the actual `agent.permission_engine.active_permissions().mode` rather than the `"workspace-write"` fallback when an engine is wired; assert `permission_mode == "workspace-write"` (the documented fallback) when the engine is absent; assert `cwd == str(agent.working_directory)`; assert `session_id == agent._session_id` when set and `""` when unset; assert `transcript_path is None` (OQ1 (a)). This pins the precedence between live-engine values and fallbacks so a future refactor cannot silently regress to defaults. Maps to matrix row "Common input fields".
- **`test_hooks_stop_no_emit_when_no_stop_rules.py`** — already shipped under A6 in PR-1 (it pins the no-emit gate, which is a Phase A behavior driven by A2's `select_matching_rules`). Phase B reuses it unchanged: the gate now also lives in `_emit_stop_hook_fired` (B7) with a `stop_result.matched_rule_count > 0` check that agrees with the helper-side count by construction (B1 dispatcher field set from the same filter expression). No new assertions are needed at this layer — the existing test (a)/(b)/(c) sub-cases cover both Phase A and Phase B because the gate semantics are identical.
- **`test_hooks_stop_doom_loop_dispatch.py`** — pin the doom-loop emit site. Force `ToolRunner` into a doom-loop trip (e.g., monkeypatch `tool_planning` to set `result.doom_loop_triggered = True` on the second call) and run a real chat turn with a Stop hook registered. Assert: (a) the Stop subprocess receives `turn_end_reason == "doom_loop"` in its stdin payload (proves the third reason value is wired through `build_stop`); (b) `last_assistant_message == "Tool execution halted by doom-loop detection."` when `assistant_message.content` is empty (proves the fallback string is carried into the payload); (c) one `PLUGIN_HOOK_FIRED` event lands with `hook_name == "Stop"`, `outcome == "allow"`, **`at_max_iter == False`** (proves the doom site does **not** masquerade as max-iter), **and `turn_end_reason == "doom_loop"`** on the emit dict — this is the disambiguation contract from B7 and is what dashboards consuming `PLUGIN_HOOK_FIRED` use to distinguish doom-arm `continue` from natural-turn `continue`; (d) the assistant message in `agent.messages` is `final_msg_doom` and the `run()` return value is `assistant_content_doom`. Sub-case (e) — Stop hook returns `force_continue` once on the doom site: assert the loop reissues an LLM call with the transport event carrying `outcome == "continue"` (**not** `"continue_at_max_iter"` — `at_max_iter` stays False) **and `turn_end_reason == "doom_loop"`** (the discriminator that uniquely identifies the doom-arm `continue`), `_stop_reentries` is incremented, ToolRunner's doom counter is **not** reset by the chat loop. Sub-case (f) — Stop hook returns `force_continue` and the cap is already at max: assert the transport event carries `outcome == "reentry_capped"` **and `turn_end_reason == "doom_loop"`**, the WARNING line names "doom-loop", and the turn ends. This pins the third exit path's full Stop semantics, the four B3 emit sites parallel to natural-turn, and the `turn_end_reason` field on every doom-arm emit — without it, a future refactor could collapse the doom branch into max-iter or drop `turn_end_reason` from the emit dict and silently re-misclassify hosts' replay events.
- **`test_hooks_stop_max_iter_dispatch.py`** — pin the max-iter emit site analogously. Configure the chat loop with a low `max_iterations`, register a Stop hook and an `on_max_iterations` transport that returns `{"action": "stop"}`. Drive the loop past the iteration cap and assert: (a) the Stop subprocess receives `turn_end_reason == "max_iterations"` in its stdin payload; (b) the transport `PLUGIN_HOOK_FIRED` event lands with `hook_name == "Stop"`, `outcome == "allow"`, `at_max_iter == True`, **and `turn_end_reason == "max_iterations"`** on the emit dict (this is the only test that pins the natural⟂max-iter discriminator pair on the transport channel — `test_hooks_stop_force_continue_decision_block.py` covers the natural-turn pair, `test_hooks_stop_doom_loop_dispatch.py` covers doom-loop). Sub-case (b) — Stop hook returns `force_continue` and cap is not full: assert `outcome == "continue_at_max_iter"` **and `turn_end_reason == "max_iterations"`** on the emit. Sub-case (c) — `force_continue` with cap already at max: assert `outcome == "reentry_capped"` **and `turn_end_reason == "max_iterations"`** on the emit. The three transport-event assertions across this test, the natural-turn test, and the doom-loop test together cover the full 3×5 outcome matrix's `turn_end_reason` discriminator on the wire.

### B7. Replay-event projection (Stop)

`PLUGIN_HOOK_FIRED` (transport/replay channel — same visibility caveat as A5) is **emitted by the chat-loop wiring at B3, not by `_dispatch_stop`**. The helper only knows what the hook *requested* (`force_continue=True/False`, `blocking_error`); it does **not** know whether the caller was the natural-turn site or the max-iter site, nor whether the caller respected `force_continue` or hit the re-entry cap. Those four labels (`continue`, `continue_at_max_iter`, `reentry_capped`, `allow`) can only be assigned at each terminal branch, after the cap check has run.

**Helper / wiring split.**

- `_dispatch_stop(self, agent, assistant_content, *, turn_end_reason, at_max_iter)` — **builds the Claude-flat Stop payload internally** by instantiating `ClaudeHookPayloadAdapter()` and calling `adapter.build_stop(...)` (the A3 builder fixes `transcript_path=None` internally — the helper does **not** pass that field), then runs `PluginHookDispatcher(cwd=...)`'s `dispatch_stop(...)`, and returns a `StopHookResult`. **Does not emit `PLUGIN_HOOK_FIRED`.** Mirrors the `_dispatch_user_prompt_submit` shape at `chat_loop.py:330-348` exactly — adapter and dispatcher are local-scope (the runner's only persistent attribute is `self._agent`, see `chat_loop.py:112-113`), and rule list is read from `agent._plugin_hook_rules`. The caller never constructs a `payload_for_stop` dict.

  ```python
  def _dispatch_stop(
      self, agent: "Agentao", assistant_content: str, *,
      turn_end_reason: Literal["final_response", "max_iterations", "doom_loop"],
      at_max_iter: bool = False,
  ) -> StopHookResult:
      """Build payload, run Stop hooks, return aggregated result.
      Does NOT emit PLUGIN_HOOK_FIRED — the outcome label depends on
      caller-side branching (cap check, at_max_iter discrimination)
      that the helper does not have access to. See B7 for emission.
      """
      if not agent._plugin_hook_rules:
          return StopHookResult()
      from ..plugins.hooks import (
          ClaudeHookPayloadAdapter,
          PluginHookDispatcher,
      )
      cwd = agent.working_directory
      perm = (
          agent.permission_engine.active_permissions().mode
          if agent.permission_engine is not None else "workspace-write"
      )
      adapter = ClaudeHookPayloadAdapter()
      # Note: build_stop does NOT take a transcript_path argument
      # (the builder fixes it to None internally per A3 / OQ1 (a)),
      # so we omit it here. Adding it would TypeError.
      payload = adapter.build_stop(
          session_id=agent._session_id,
          cwd=cwd,
          permission_mode=perm,
          last_assistant_message=assistant_content,
          turn_end_reason=turn_end_reason,
          stop_hook_active=(self._stop_reentries > 0),
      )
      dispatcher = PluginHookDispatcher(cwd=cwd)
      # Phase B continuity (A2 "Matched-rule counting"): pre-filter via
      # select_matching_rules so the no-emit early-return happens before
      # dispatch_stop is called at all (no subprocess fork, no transport
      # churn). The dispatcher's internal filter (B2) then becomes a
      # no-op on the already-filtered list. Returning an empty
      # StopHookResult() yields matched_rule_count == 0, which suppresses
      # the PLUGIN_HOOK_FIRED emit at every B3 terminal branch.
      matched = dispatcher.select_matching_rules(
          "Stop", payload, agent._plugin_hook_rules,
      )
      if not matched:
          return StopHookResult()
      return dispatcher.dispatch_stop(payload=payload, rules=matched)
  ```

- `_emit_stop_hook_fired(self, agent, *, outcome, at_max_iter, stop_result)` — small chat-loop helper called at each B3 terminal branch. Single source of truth for the `PLUGIN_HOOK_FIRED` payload shape on Stop. Wraps the dict in `AgentEvent(EventType.PLUGIN_HOOK_FIRED, {...})` because the transport `emit(self, event: AgentEvent)` protocol (`agentao/transport/base.py:28`) only accepts a wrapped event — passing the dict directly is a type error. Wraps the call in `try/except Exception: pass` to honor the protocol's "must not raise" contract, mirroring the existing UserPromptSubmit emit at `chat_loop.py:368-369`. **Gates emission on `stop_result.matched_rule_count > 0`** (B1 field — the **selection** count, not the execution count; see B1 docstring) — when no Stop rule from the plugin ruleset was selected for dispatch, the helper returns silently, so a turn with zero matched Stop hooks does not emit `hook_name="Stop", outcome="allow"` (which would be both noisy and semantically false). This mirrors the early-return behavior of `_dispatch_user_prompt_submit` at `chat_loop.py:332-333` while also catching the second case the early-return cannot see (rules exist but none target Stop). Reading the call sites in B3 is enough to enumerate every outcome label this plan can emit; nothing is hidden inside `_dispatch_stop`.

  ```python
  # Imports at the top of chat_loop.py — `AgentEvent` and `EventType`
  # are already imported there for existing emit sites; no new import.
  def _emit_stop_hook_fired(
      self, agent: "Agentao", *,
      outcome: Literal["allow", "block", "continue",
                       "continue_at_max_iter", "reentry_capped"],
      at_max_iter: bool,
      turn_end_reason: Literal["final_response", "max_iterations", "doom_loop"],
      stop_result: StopHookResult,
  ) -> None:
      # Replay gate: skip when no Stop rule was selected for dispatch
      # (selection count, not execution count — see B1 docstring).
      # Without this, a turn with zero matched Stop hooks would emit
      # outcome="allow" — noisy + semantically false.
      if stop_result.matched_rule_count == 0:
          return
      try:
          # `turn_end_reason` is included on the emit payload so
          # dashboards can disambiguate (outcome="continue") at the
          # natural-turn site from (outcome="continue") at the
          # doom-loop site — see the outcome-enum table below for the
          # full disambiguation rule.
          agent.transport.emit(AgentEvent(EventType.PLUGIN_HOOK_FIRED, {
              "hook_name": "Stop",
              "outcome": outcome,
              "at_max_iter": at_max_iter,
              "turn_end_reason": turn_end_reason,
              "matched_rule_count": stop_result.matched_rule_count,
              "added_context_count": len(stop_result.additional_contexts),
              "suppress_output": stop_result.suppress_output,
          }))
      except Exception:
          pass
  ```

**Outcome enum (final, single source of truth — three emit sites, five labels).**

The five outcome labels and the three emit sites form a 3×5 surface, not all combinations populated. The table below enumerates **each (site, label) pair that actually emits**; readers/dashboards parsing `PLUGIN_HOOK_FIRED` should disambiguate by `(turn_end_reason, at_max_iter, outcome)` rather than by `outcome` alone.

| Label | Natural-turn (`turn_end_reason="final_response"`, `at_max_iter=False`) | Max-iter (`turn_end_reason="max_iterations"`, `at_max_iter=True`) | Doom-loop (`turn_end_reason="doom_loop"`, `at_max_iter=False`) | When |
|---|---|---|---|---|
| `allow` | ✅ allow path (~`chat_loop.py:730-737`) | ✅ allow path (~`chat_loop.py:805-810`) | ✅ allow path (~`chat_loop.py:271+` doom-arm allow tail) | hook(s) ran cleanly, no force_continue, no blocking_error; `added_context_count > 0` rides here when the hook attached `additional_contexts` and `suppress_output=False` (when `suppress_output=True`, the count is still recorded but no `<stop-hook>` echo lands on `final_msg`) |
| `block` | ✅ block branch (`return blocked` at ~`chat_loop.py:680-681`) | ✅ block branch (`return blocked` near ~`chat_loop.py:778`) | ✅ block branch (`return blocked` in the doom-arm) | `blocking_error` set; turn ended with a hook-supplied error message; independent of the cap |
| `continue` | ✅ force_continue branch, **only after** `_stop_reentries < _stop_reentry_cap` (around ~`chat_loop.py:711-712`) | ❌ N/A — max-iter uses `continue_at_max_iter` instead | ✅ force_continue branch, **only after** cap check (in the doom-arm; `at_max_iter=False` so this label, **not** `continue_at_max_iter`) | one re-entry was issued at a non-max-iter site |
| `continue_at_max_iter` | ❌ N/A — natural-turn uses `continue` | ✅ force_continue branch, **only after** the cap check (around ~`chat_loop.py:805-806`) | ❌ N/A — doom site uses `continue` (it is **not** max-iter; the discriminator is `turn_end_reason`, **not** the outcome) | one re-entry was issued at max-iterations; distinct from `continue` so dashboards can flag the suspicious case (B3) |
| `reentry_capped` | ✅ cap-hit branch (early-return at ~`chat_loop.py:694-700`) | ✅ cap-hit branch (the explicit `if force_continue and _stop_reentries >= _stop_reentry_cap` arm B3 spells out for max-iter) | ✅ cap-hit branch (the parallel explicit cap-hit arm in the doom-arm; B3 doom-loop section) | `force_continue` requested but `_stop_reentries >= _stop_reentry_cap`; the loop refused the re-entry and ended the turn |

**Disambiguation rule for consumers.** `outcome="continue"` alone does not tell a dashboard whether the re-entry happened at the natural-turn boundary or at the doom-loop boundary — those carry different operational risk (a doom-loop force_continue is closer to "host insisting on continuing despite model misbehaving" than a natural-turn force_continue). Consumers that care should read `turn_end_reason` from the corresponding payload alongside the `outcome`/`at_max_iter` fields. The three-way `turn_end_reason` discriminator is the single source of truth for *which exit fired*; `outcome` is the single source of truth for *what the hook said*; `at_max_iter` is a redundancy for downstream filtering.

**Why not emit inside `_dispatch_stop`.** Pushing the cap / `at_max_iter` decision down into the helper would force the helper to read `self._stop_reentries` and `self._stop_reentry_cap` and to know whether its caller is the natural site or the max-iter site — that is the chat-loop wiring's responsibility, not the dispatcher's. The split keeps `_dispatch_stop` testable in isolation (B6 dispatcher tests can assert `StopHookResult` shape without instantiating a chat loop) and prevents a class of bugs where a future caller forgets to wire `at_max_iter`.

The `"modify"` label from `_dispatch_user_prompt_submit` does **not** apply here — Stop's `additional_contexts` are appended to the assistant's final answer (B3), not inserted into a subsequent user prompt, so they ride on the `"allow"` outcome via `added_context_count > 0`. Open Q4 below is the source of `continue_at_max_iter` and is now resolved into this enum.

**Test impact.** `test_hooks_stop_reentry_cap.py` already asserts the `reentry_capped` event lands on transport. The pass-16 disambiguation contract (`turn_end_reason` on the emit dict) is pinned across **three** transport-event tests, one per emit site: `test_hooks_stop_event.py` / `test_hooks_stop_force_continue_decision_block.py` / `test_hooks_stop_exit_code_2.py` / `test_hooks_stop_no_emit_when_no_stop_rules.py` for natural-turn (`turn_end_reason="final_response"`); the new `test_hooks_stop_max_iter_dispatch.py` for max-iter (`"max_iterations"`); `test_hooks_stop_doom_loop_dispatch.py` for doom-loop (`"doom_loop"`). Together they cover the `turn_end_reason` discriminator across all three emit sites and prevent a future refactor from dropping the field from the emit dict (which would silently break dashboards relying on the disambiguation rule).

---

## Out of scope (named explicitly)

- `Notification`, `SubagentStop`, `PostToolBatch`, `StopFailure` — tracked in `pi-mono-borrow-review.md`. Each needs its own emit-site and shape decision; not bundled here.
- Manual-trigger compaction (`/compact`-style CLI command). The CLI does not currently expose one; revisit when it does. PreCompact's `trigger="manual"` is therefore never emitted (compatibility matrix 🟡).
- Promoting `PreCompact` to a public ACP event. The internal `EventType.CONTEXT_COMPRESSED` already covers post-compaction; pre-compaction promotion is a separate Public-Event-Promotion-style ticket.
- Promoting plugin-hook events into the `agentao.host.EventStream` discriminated union (today: `ToolLifecycleEvent | SubagentLifecycleEvent | PermissionDecisionEvent`). `PLUGIN_HOOK_FIRED` stays on the internal transport/replay channel for both phases of this plan; cross-cutting host promotion is its own ticket.
- Surfacing `_dispatch_lifecycle` `HookAttachmentRecord` lists to the conversation / replay layer for *any* lifecycle event. All current call sites discard the return (see A6 caveat); fixing that uniformly is `PLUGIN_HOOK_ATTACHMENT_PIPELINE_PLAN`.
- Prompt-type hooks (`hook_type == "prompt"`) for `Stop` / `PreCompact`. UserPromptSubmit is the only event that supports them today; expanding that surface is independent of this work. **A1 actively rejects** prompt-type rules for these two events at parse time via `SUPPORTED_HOOK_TYPES_BY_EVENT`, so misconfigured rules now surface as a parser warning instead of being silently accepted-then-dropped at dispatch. **The "Why not prompt-type hooks for Stop / PreCompact" section above lays out the rationale** — capability-redundancy with command hooks for Stop, no destination for a prompt response under our observe-only PreCompact contract — and the migration path (`command`-type shim that calls an LLM internally and emits Claude-compatible Stop JSON).
- **PreCompact blocking (Claude Code compatibility gap)** — exit 2 / `decision: "block"` on PreCompact are observed but not honored. See B5 for rationale.
- **Claude Code wire compatibility for events other than Stop / PreCompact.** UserPromptSubmit / SessionStart / PreToolUse / PostToolUse / PostToolUseFailure keep the agentao `{event, data}` envelope; converting them is a cross-cutting refactor with consumer-side impact. See compatibility matrix.
- **Claude Code config file compatibility** (`~/.claude/settings.json` shape). Agentao reads its own `permissions.json` / hook config; hosts that want drop-in Claude config files must pre-translate. Out of scope.

---

## Open questions

1. **`transcript_path` payload field.** Claude Code passes the path of the on-disk transcript. agentao's session log is split across `agentao.log` plus the in-memory `agent.messages` list — there is no single canonical file. Options: (a) leave `transcript_path = null`; (b) write a per-dispatch snapshot of `agent.messages` to a tempfile; (c) omit the field. **Recommendation:** (a) for Phase A — the field is *present* (Claude-compat top-level key) but holds `null`. Phase A's added `last_assistant_message` covers the most common Stop hook need (review the answer) without any transcript file. Revisit (b) if a host concretely asks for it.
2. **Force-continue follow-up — user role or system role?** Claude Code feeds the reason as a user-role message. Mirroring that gives drop-in compat but blurs provenance. **Recommendation:** user role + `<system-reminder>Stop hook injected this</system-reminder>` prefix, so provenance is visible to the model and to transcript readers.
3. **Re-entry cap default.** 3 is a guess. **Resolved:** ship 3 as a hardcoded constructor default on the chat loop. Do **not** introduce a `stop_hook_reentry_max` settings key in this plan — see B4 rationale (the existing `.agentao/settings.json` has only two readers and adding a third for an untuned knob is premature). Bump or expose once the first real host bumps into the cap.
4. **Stop emission at max-iterations exit — gate or observe?** A force-continue there could mask a real loop failure. **Resolved:** allow it but log a `WARNING` and emit the dedicated `outcome="continue_at_max_iter"` label (see B7 enum) so dashboards can flag it.
5. **`permission_mode` value-space mapping.** Field shape matches Claude Code but value vocabularies diverge (matrix row marked 🟡). Three options:
   - **(a) emit Agentao values verbatim** (`"read-only" \| "workspace-write" \| "full-access" \| "plan"`). Hook scripts must learn Agentao vocabulary; provenance is preserved. **Recommended for Phase A.**
   - **(b) translate to nearest Claude value** before emit (`"read-only" → "default"`, `"workspace-write" → "acceptEdits"`, `"full-access" → "bypassPermissions"`, `"plan" → "plan"`). Drop-in compat for Claude scripts that branch on these strings; the mapping is opinionated and lossy.
   - **(c) emit both: a `permission_mode` (Claude-ish, translated) plus an `agentao_permission_mode` (raw)**. Wire-bloat; only worth it if real hosts hit the gap.
   **Recommendation:** ship (a) and document the divergence in matrix + this OQ; revisit when a host concretely asks for (b) or (c). Do **not** silently translate — option (b) without an explicit translation table would obscure provenance.

---

## Sequencing

- **PR-1 (Phase A, ~1.5 days):** A1–A7. Includes the `_matches` extension for PreCompact `manual|auto` matchers and the new flat snake_case builders. `dispatch_stop` returns `list[HookAttachmentRecord]` for this PR. Independent commit. Shippable on its own.
- **PR-2 (Phase B, ~2 days):** B1–B4, B6–B7. Adds the Stop-specific runner with exit code 2 honoring and Claude Code JSON parsing. Depends on PR-1 being merged but does not change A's emit sites.
   - **Breaking signature change inside this PR:** `dispatch_stop` is upgraded from `list[HookAttachmentRecord]` to `StopHookResult` (see B2's "Test impact" note). The A6 test `test_hook_dispatcher_stop.py` is rewritten to walk `result.messages` and to cover the new gate-signal fields. `dispatch_pre_compact` keeps its Phase-A return type — PreCompact stays observe-only (B5).

Total: ~3.5 dev-days when triggered. PreCompact blocking is a Claude Code compatibility gap (B5), not on the roadmap; if a host requires it, that work goes in `PRECOMPACT_GATE_PLAN.md`.

---

## Revision notes

**rev 2026-05-05 — review pass 23 (`select_matching_rules` claimed "same filter" as `_dispatch_lifecycle`, but the two expressions are not identical).** One low-risk wording fix:

1. **P3 — the utility's docstring said "Apply the same event/is_supported/matcher filter that `_dispatch_lifecycle` applies internally", but `_dispatch_lifecycle` actually filters by event + `hook_type == "command"` + `_matches` (`agentao/plugins/hooks.py:381`), while the utility uses event + `is_supported` + `_matches`.** `is_supported` is a strict superset of `hook_type == "command"` — it accepts `hook_type in {"command", "prompt"}` — so the two expressions could in principle diverge for events that accept prompt-type rules. In the normal loader path the divergence is closed by A1's per-event hook-type rejection (Stop / PreCompact prompt-type rules are dropped at parse time and `is_supported` flips False at runtime), so for the events this plan introduces the two are equivalent on every loader-produced rule — but the docstring's "same filter" claim is a literal misstatement that an implementer reading this section in isolation could try to "fix" by hard-coding `hook_type == "command"`, regressing the utility into a dispatcher-internal helper rather than the canonical selection filter A2 needs. Resolved by (a) tightening the docstring to call this out as the **canonical Stop / PreCompact selection filter** (no claim of literal parity with `_dispatch_lifecycle`); (b) adding a dedicated **"Parity with `_dispatch_lifecycle`"** paragraph after the four-step flow that names the actual lifecycle filter, explains why `is_supported` is a superset, points at A1's per-event rejection as the bridge that closes the divergence for Stop / PreCompact specifically, and tells implementers to rely on A1's parse-time rejection rather than on the lifecycle runner's filter shape; and (c) keeping `is_supported` (not switching to `hook_type == "command"`) so a future event that legitimately supports prompt-type rules does not need to fork a new selection filter. No behavior change — for every Stop / PreCompact rule the loader produces under this plan, `is_supported` and `hook_type == "command"` agree, so the on-wire selection set is identical.

**No source-code changes — pass 23 is purely docstring + spec narrative tightening.** The utility's filter expression and the dispatcher's behavior are unchanged; only the spec's claim about the relationship between the utility and `_dispatch_lifecycle` is corrected to match the actual code.

**rev 2026-05-05 — review pass 22 (A2 four-step flow step 4 hard-coded `dispatch_stop`, leaving PreCompact helper without a dispatch reference).** One low-risk wording fix:

1. **P3 — A2's "four-step flow" prologue says "the Phase A chat-loop helpers (`_dispatch_stop` / `_dispatch_pre_compact`) use it" but step 4 only writes `dispatcher.dispatch_stop(payload=payload, rules=matched)`.** A reader implementing `_dispatch_pre_compact` from the four-step flow has no dispatch-method reference for PreCompact at the canonical step where it should appear; copying step 4 verbatim would route PreCompact's filtered rules into `dispatch_stop`, which would then be re-filtered by B2's Stop-only event filter (`select_matching_rules("Stop", ...)`) and produce an empty list — silently dropping every PreCompact hook subprocess. Resolved by rewriting step 4 to enumerate both: "call the corresponding lifecycle dispatch — `dispatcher.dispatch_stop(payload=payload, rules=matched)` for Stop, `dispatcher.dispatch_pre_compact(payload=payload, rules=matched)` for PreCompact (passing the filtered list keeps the dispatcher's internal re-filter a no-op for either event) — and emit `PLUGIN_HOOK_FIRED` with `matched_rule_count=len(matched)`". The "no-op for either event" wording also closes a smaller spec gap: previously the no-op claim was implicit for PreCompact (relying on `_dispatch_lifecycle`'s internal `_matches` returning True on a pre-filtered list) but only stated for Stop (where B2's `select_matching_rules("Stop", ...)` is the explicit re-filter); now it's symmetric.

**No source-code changes — pass 22 is purely a wording fix in A2's four-step flow.** No design changes; the dispatch routing was already correct in A4 (which names both helpers and routes them through their own `dispatch_*` methods) and in B7 (which uses `dispatch_stop` correctly), only A2's prologue had the Stop-only step-4 example.

**rev 2026-05-05 — review pass 21 (`matched_rule_count` semantic vs short-circuit + A2 internal contradiction on attachment count).** Two findings landed:

1. **P2 — `matched_rule_count` was documented as "rules that ran" but B2's run loop short-circuits on `blocking_error` / `force_continue`, so the field actually reports "rules selected before dispatch".** A5 (line 488) said "the number of `Stop` rules that ran"; B1's docstring said "matched-and-ran"; B7's prose and pseudocode comment said "actually ran" / "actually matched-and-ran". But B2 sets `result.matched_rule_count = len(stop_rules)` **before** the run loop precisely so a single-rule short-circuit still reports the full selected count (the comment at the assignment site is explicit about this). Result: a 3-rule selection where the first short-circuits reports `3`, contradicting every "ran" claim. Two ways to resolve: (i) move the assignment after the loop and count only rules that completed — but that defeats the field's purpose (the gate at `_emit_stop_hook_fired` would suppress emission for a force_continue path that did fire one rule, which is wrong); (ii) re-pin the documented semantic to "selection count" and keep the assignment where it is. Picked (ii). Updated **A5** (canonical schema definition: "**`matched_rule_count` is the count of Stop rules selected for dispatch** — i.e. `len(dispatcher.select_matching_rules(...))` — **not** the execution count; if a future host needs the actual run-count, add a sibling `executed_rule_count` field rather than redefining this one"), **B1 docstring** (replaced "matched-and-ran in this dispatch" with explicit "**selection count**, not execution count" framing + name-stability note for replay backward compatibility), **B2 dispatcher comment** (replaced "still reports the correct count" with explicit "selection count, not execution count" wording), **B7 prose** (replaced "actually ran" with "selected for dispatch" and added a parenthetical pointer to the B1 docstring contract), and **B7 pseudocode comment** (same wording switch). The on-wire field name is preserved (`matched_rule_count`) for replay-stream backward compatibility — renaming to `selected_rule_count` would have been cleaner in isolation but would break any downstream consumer that already keys on the field name.
2. **P3 — A2's "Why not `len(attachments)`" subsection contradicted the parenthetical in the section opener.** A2's opening paragraph said "the attachment count is a poor proxy for the rule count (a clean-running command hook with no `additionalContext` produces zero attachments but a non-zero matched count)", but the dedicated subsection 25 lines below correctly stated "A Stop command hook that exits 0 with empty stdout produces a single `hook_success` attachment, so attachment count happens to align with rule count *in that case*". The "produces zero attachments" claim in the opener is wrong — `_run_lifecycle_command`'s current behavior writes a `hook_success` attachment even on clean-exit-0. The subsection's correct counter-example (multi-`additionalContext` inflation, future hook_success-removal deflation) carries the proxy argument without misstating the clean-exit case. Resolved by dropping the inaccurate parenthetical from the opener and replacing it with a forward-pointer ("see 'Why not `len(attachments)`' below for the failure modes"). The dedicated subsection is unchanged.

**No source-code changes — pass 21 is pure semantic re-pinning + one factual correction.** The on-wire schema, the dispatcher logic, and the chat-loop wiring are all unchanged. The `matched_rule_count` field's name and value are preserved exactly; only the documented contract is tightened from the loose-and-wrong "ran" to the precise-and-correct "selected for dispatch".

**rev 2026-05-05 — review pass 20 (`select_matching_rules` not threaded through B7 / B2 — same-filter invariant violated by the canonical pseudocode).** Two findings landed; both close gaps the pass-19 A2 contract opened:

1. **P2 — B7's `_dispatch_stop` helper still passed unfiltered rules to `dispatch_stop`.** Pass 19's A2 added `select_matching_rules` and named the four-step Phase A flow (build → select → early-return-if-empty → dispatch with filtered list). It also explicitly claimed Phase B continuity: "the helper still pre-computes via `select_matching_rules` so the no-emit early-return happens before `dispatch_stop` is called at all (no subprocess fork, no transport churn)". But B7's authoritative pseudocode for `_dispatch_stop` was untouched and still called `dispatcher.dispatch_stop(payload=payload, rules=agent._plugin_hook_rules)` directly. An implementer copying B7 verbatim would forfeit the no-subprocess-fork guarantee — every Stop-emit site would fork a subprocess on every turn even when zero plugin rules existed (or when rules existed but none targeted Stop). The gate at `_emit_stop_hook_fired` (`matched_rule_count > 0`) would still suppress the *transport event*, but the *subprocess cost* would be paid. Resolved by rewriting B7's `_dispatch_stop` body to: build payload → `matched = dispatcher.select_matching_rules("Stop", payload, agent._plugin_hook_rules)` → `if not matched: return StopHookResult()` (early-return) → `return dispatcher.dispatch_stop(payload=payload, rules=matched)` (dispatch with the filtered list). The early-return path returns an empty `StopHookResult()` with `matched_rule_count == 0`, which the existing `_emit_stop_hook_fired` gate already suppresses at every B3 terminal branch — so the no-emit semantic is preserved end-to-end without further wiring changes.
2. **P3 — B2's `dispatch_stop` filter expression diverged from A2's `select_matching_rules`.** A2 defined the canonical filter as `event + is_supported + _matches`; A2's narrative ("the two counts are guaranteed to agree because both branches use the same filter expression") only holds if both branches actually use the same expression. But B2's pseudocode wrote `[r for r in rules if r.event == "Stop" and r.is_supported]` — missing `_matches`. In practice the divergence is benign for Stop because A2's `_matches` always returns `True` for Stop (Claude Code defines no Stop matcher), but the documented invariant is louder than the practical equivalence: a future event added to this dispatcher path with a real matcher would silently break the same-filter contract. Resolved by replacing B2's inline list-comp with `self.select_matching_rules("Stop", payload, rules)`. The dispatcher's filter is now idempotent on B7's pre-filtered input and acts as the only filter for direct callers (e.g., `test_hook_dispatcher_stop.py` which constructs mixed rules and calls `dispatch_stop` directly). The `matched_rule_count = len(stop_rules)` assignment is now sourced from the same expression A2's helper uses, so the same-filter-expression invariant is enforced syntactically rather than asserted in prose.

**No source-code changes — pass 20 is pure pseudocode-vs-contract reconciliation for PR-2.** The `select_matching_rules` utility introduced in pass 19 is now actually used at every site that A2's contract said it should be used at (Phase A helper, Phase B helper, dispatcher internal filter); implementers copying B7 / B2 verbatim no longer bypass the early-return guarantee or split the filter expression in two.

**rev 2026-05-05 — review pass 19 (Phase A `matched_rule_count` source + gate test ownership + last A4 dual-site wording).** Three findings landed:

1. **P2 — Phase A had no implementable spec for `matched_rule_count`.** Pass 18 added `matched_rule_count` to the Phase A emit schema (A5) and tied a no-emit gate to it, but the underlying Phase A `dispatch_stop` / `dispatch_pre_compact` (A2) still returned `list[HookAttachmentRecord]` and `_dispatch_lifecycle` did not surface "matched and ran" rule count anywhere. PR-2's `StopHookResult.matched_rule_count` (B1) only fixes Phase B Stop — not Phase A Stop, not PreCompact (which stays observe-only under both phases). Without a concrete source for the count, an implementer would either fall back to `len(attachments)` (incidentally aligned for clean exit-0 hooks but wrong in every other case — see "Why not `len(attachments)`" in the new A2 subsection) or skip the gate entirely. Closed by adding a "Matched-rule counting (Phase A emit-gate dependency)" subsection to A2 that introduces a small public dispatcher utility `select_matching_rules(event, payload, rules) -> list[ParsedHookRule]` (applies the same event/`is_supported`/`_matches` filter that `_dispatch_lifecycle` applies internally), and pinning the four-step Phase A helper flow: build payload → `select_matching_rules` → early-return if empty → otherwise dispatch with the filtered list and emit with `matched_rule_count=len(matched)`. Phase B continuity: when `dispatch_stop` upgrades to return `StopHookResult`, both the helper-side (`select_matching_rules`) and dispatcher-side (`StopHookResult.matched_rule_count`) counts come from the same filter expression and are guaranteed to agree; the helper still pre-computes so the no-emit early-return happens before any subprocess fork.
2. **P2 — gate test ownership inconsistency between A5/A6/B6.** A5 said "the A6 test `test_hooks_stop_no_emit_when_no_stop_rules.py` pins this gate" but A6's test list did not actually contain that test — it lived in B6. For PR-1 to be independently shippable (per §Sequencing) the gate test must run under PR-1; the fact that A5's reference pointed nowhere meant a strict reading of the plan would have shipped PR-1 with no test for the no-emit gate. Closed by relocating the test bullet from B6 to A6 with Phase A framing (it's `select_matching_rules`-driven, not `_emit_stop_hook_fired`-driven, so it belongs in PR-1), and by adding a sibling `test_hooks_pre_compact_no_emit_when_no_rules.py` (PreCompact had no equivalent gate test at all — the existing `test_hooks_pre_compact_event.py` always registers a rule and never exercises the empty-matched branch). The B6 entry is replaced with a one-line cross-reference noting the test ships in A6 and PR-2 reuses it without modification because both `select_matching_rules` and `StopHookResult.matched_rule_count` come from the same filter expression.
3. **P3 — last surviving "both natural-turn and max-iter sites" dual-site wording in A4.** Pass 18's P3 fixed the parenthetical in the Stop helper paragraph but missed the `stop_hook_active` wiring sentence two paragraphs later, which still said "see B3 wiring on both natural-turn and max-iter sites". Doom-loop is a full Stop re-entry site (see B3's doom-loop subsection — it increments `_stop_reentries` like the other two). Resolved by changing to "all three force_continue sites — natural-turn / max-iter / doom-loop" so the count agrees with the rest of the plan.

**No source-code changes — pass 19 is pure spec self-containment closure for PR-1.** The Phase A `matched_rule_count` source is now a named public utility (`select_matching_rules`) instead of being implicit; the gate test ownership is now consistent across A5 (referrer), A6 (test list), and B6 (cross-reference); and the last dual-site wording residue is gone.

**rev 2026-05-05 — review pass 18 (Phase A replay emit spec completion + A4 stale wording).** Two findings landed:

1. **P2 — A5 underspecified the Phase A `PLUGIN_HOOK_FIRED` payload, leaving PR-1 implementers to forward-reference B7.** A5 only said `outcome="allow"` is the only label, but A6's `test_hooks_stop_event.py` (rewritten in pass 17) already asserts `turn_end_reason="final_response"` and `at_max_iter=False` on the transport emit dict — fields whose schema only appears in B7's `_emit_stop_hook_fired` body (Phase B). Since PR-1 is described in §Sequencing as independently shippable, an implementer reading A5 in isolation would have no canonical schema for the on-wire dict and would either (a) under-emit (drop `turn_end_reason` / `at_max_iter`), failing A6, or (b) pull B7's full Phase B shape forward, blurring the PR-1/PR-2 boundary. Closed by adding two new subsections to A5: a **"Phase A emit payload (minimum schema)"** subsection enumerating the exact key/type/value tuple for Stop and PreCompact (`hook_name`, `outcome`, plus the discriminators from A4 — `turn_end_reason` + `at_max_iter` for Stop, `compaction_type` + `trigger` for PreCompact — plus the shared `matched_rule_count` no-emit gate); and an **"Emit ownership"** subsection clarifying that Phase A emits inside the helper (because outcome is unconditionally `allow`) while Phase B splits emit out to a dedicated `_emit_stop_hook_fired` for Stop only. Also explicitly states Phase A → Phase B is additive (no renames, no removals).
2. **P3 — A4 stale "(already in scope at both sites)" wording.** Pass 15 added doom-loop as the third Stop emit site, but the parenthetical in the Stop helper paragraph still said "both sites" (dual). Doesn't change implementation, but a reader could mistake the third site for second-class. Resolved by changing to "(already in scope at each site)".

**No source-code changes — pass 18 closes the PR-1 spec self-containment gap that pass 17's transport-event assertions exposed.** The Phase A emit schema is now stated once in A5 instead of being implicitly defined by an A6 test plus a B7 helper body.

**rev 2026-05-05 — review pass 17 (B6 transport-event `turn_end_reason` coverage gap).** One low-risk finding landed; closes the B6 test coverage gap that pass 16's disambiguation contract opened:

1. **B6 tests asserted `turn_end_reason` on hook stdin payloads but not on the transport `PLUGIN_HOOK_FIRED` emit dict.** Pass 16 made `PLUGIN_HOOK_FIRED.turn_end_reason` the disambiguation field consumers must read to distinguish doom-arm `continue` from natural-turn `continue` (B7 outcome table). But B6 only pinned `turn_end_reason` on the *input side* (Stop hook stdin payload via `build_stop`); the *output side* (transport emit dict) had no targeted assertion. A future refactor could drop `turn_end_reason` from `_emit_stop_hook_fired`'s emit dict — passing all input-side tests, all dispatcher tests, all `outcome=...` tests — and silently break dashboard consumers downstream. Closed by:
   - **Five existing tests gain explicit `PLUGIN_HOOK_FIRED["turn_end_reason"]` assertions:**
     - `test_hooks_stop_event.py` — pin `"final_response"` (natural-turn allow path)
     - `test_hooks_stop_force_continue_decision_block.py` — pin `"final_response"` + `outcome="continue"` (natural-turn force_continue)
     - `test_hooks_stop_exit_code_2.py` — pin `"final_response"` + `outcome="continue"` (natural-turn exit-2 force_continue)
     - `test_hooks_stop_reentry_cap.py` — pin `"final_response"` + `outcome="reentry_capped"` (natural-turn cap-hit)
     - `test_hooks_stop_no_emit_when_no_stop_rules.py` (sub-case c) — pin `"final_response"` on the positive control
   - **`test_hooks_stop_doom_loop_dispatch.py` gains transport-side assertions:** all three doom-arm sub-cases (allow / continue / reentry_capped) now assert `turn_end_reason == "doom_loop"` on the emit dict, not just on the hook's stdin.
   - **New `test_hooks_stop_max_iter_dispatch.py`** added — there was no dedicated max-iter Stop dispatch test before. Three sub-cases pin `(turn_end_reason, outcome, at_max_iter)` triples on the transport: `("max_iterations", "allow", True)`, `("max_iterations", "continue_at_max_iter", True)`, `("max_iterations", "reentry_capped", True)`. Without this test, the only max-iter transport-event coverage was the implicit one inside `test_hooks_stop_reentry_cap.py`, which doesn't disambiguate which site fired.
   - **B7 "Test impact" paragraph** rewritten to map each emit site to the test(s) that pin it, making the coverage matrix explicit: natural-turn → 4 tests, max-iter → 1 new test, doom-loop → 1 test.

**No design changes — pass 17 is pure test coverage closure for pass 16's disambiguation contract.** No source code or production behavior changes.

**rev 2026-05-05 — review pass 16 (pass-15 follow-on: helper Literal type + outcome table both still ignored doom-loop).** Two findings landed; both clean up the doom-loop integration that pass 15 introduced but did not finish wiring:

1. **P1 — `_dispatch_stop` Literal type was still `["final_response", "max_iterations"]`, missing `"doom_loop"`.** Pass 15 typed the A3 builder signature with all three values and the B3 doom-loop call site passes `turn_end_reason="doom_loop"`, but B7's helper Literal type in the `_dispatch_stop` body was untouched. An implementer copying B7's body verbatim would have a type-check error on the third call site, or — worse — would weaken the type to `str` to make it pass and lose the protection for the other two sites. Resolved by adding `"doom_loop"` to the Literal so all three sites and the type contract agree.
2. **P2 — Outcome enum table still listed only natural-turn / max-iter sources for each label, ignoring doom-loop.** Pass 15 added the doom-loop branch to B3 and a `test_hooks_stop_doom_loop_dispatch.py` that asserts `outcome="allow" / "continue" / "reentry_capped"` for doom-loop, but the B7 outcome table — which the section header still calls "**single source of truth**" — never grew a doom-loop column. Three downstream hazards: (a) implementers wiring the doom branch had no canonical reference for which labels to emit at which doom site; (b) dashboards parsing only `outcome` could not distinguish doom-loop from natural-turn (both emit `continue`); (c) the table contradicted the B6 test plan. Resolved by rewriting the outcome table as a 3×5 matrix (natural-turn / max-iter / doom-loop columns × five labels rows), with `(✅ at <line>)` cells for emitted pairs, `❌ N/A` cells for the three structurally-impossible combinations (`continue` at max-iter, `continue_at_max_iter` at natural-turn, `continue_at_max_iter` at doom-loop), and a new "**Disambiguation rule for consumers**" paragraph telling parsers to read `(turn_end_reason, at_max_iter, outcome)` together rather than `outcome` alone. To make the rule actually executable, also added `turn_end_reason` to the `_emit_stop_hook_fired` helper signature and to the emitted dict; updated all eight existing call sites in B3 (4 natural-turn + 4 max-iter) to pass it; added a substitution-list bullet in the doom-loop subsection telling implementers all four doom-arm emit calls pass `turn_end_reason="doom_loop"`.

The keep/delete decision: kept `continue_at_max_iter` as a separate label rather than collapsing it into plain `continue`+`turn_end_reason`. Reason: it is already documented and tested across three earlier passes; switching the label scheme now would invalidate a half-dozen test assertions and change wire-format semantics for hosts that may have already wired dashboards against pass-12. The doom-loop site uses plain `continue` + `turn_end_reason="doom_loop"` because there is no prior commitment to a `continue_at_doom_loop` label and because keeping the enum at five labels (rather than growing one per future exit site) is the future-proof shape — `turn_end_reason` is now the per-exit-site discriminator.

**No design changes — pass 16 finishes the doom-loop integration that pass 15 started.** The Literal type fix is mechanical; the outcome table rewrite makes the "single source of truth" claim actually true; the helper signature change ensures consumers can act on the disambiguation rule the table now documents.

**rev 2026-05-05 — review pass 15 (third loop exit — doom-loop break — was orphaned by the dead-code-removal claim).** One P1 finding landed; corrects a real implementation hazard the previous drafts overlooked:

1. **`while True` has *three* exits, not two; the doom-loop break (`chat_loop.py:271-272`) was missing from B3's wiring and from A4's emit table.** Pre-pass-15 B3 said: "every arm of `if iteration >= max_iterations` either continues or returns within the branch, so the post-while finalization at `chat_loop.py:308-324` becomes unreachable. PR-2 deletes those lines." That sentence is wrong because `chat_loop.py:271-272` has `if doom_triggered: break` — a third exit that is *not* inside the `iteration >= max_iterations` block, *not* the natural-final-response `else` arm, and currently relies on the post-while finalization to commit `final_msg` and return. Deleting the post-while block as the plan instructed would orphan the doom-loop return path, so `run()` would fall off the end of the function and return `None` whenever the doom-loop detector trips. Two repair options: (a) leave a doom-only post-while fallback (less symmetric, weakens the "PR-2 deletes those lines" claim); (b) handle doom inline so all three exits are inline and the post-while block truly becomes unreachable. Took option (b) for symmetry with the existing natural-turn / max-iter pattern.

   Concrete additions:
   - **Semantics** now lists three Stop emit sites (added the doom-loop bullet) and the `turn_end_reason` paragraph names three discriminator values.
   - **A3 builder signature** typed `turn_end_reason: Literal["final_response", "max_iterations", "doom_loop"]`.
   - **A4 Stop emit table** gained a third row: doom-loop break at `chat_loop.py:271-272`, `turn_end_reason="doom_loop"`, `last_assistant_message` falls back to `"Tool execution halted by doom-loop detection."` because the assistant_message at that point is the one that produced the offending tool_calls and usually has empty content.
   - **B3** has a new "Doom-loop dispatch site" subsection that mirrors the max-iter shape with three substitutions (`assistant_content` → `assistant_content_doom`, `final_msg` → `final_msg_doom`, `at_max_iter=False`) and notes explicitly that **doom-counter reset is NOT the chat loop's responsibility** — that counter belongs to ToolRunner's planner state; honoring `force_continue` at doom may re-trigger doom but the re-entry cap will eventually win.
   - **B3 "Consequence — dead code removal"** now correctly enumerates **three** surviving exits, so PR-2's deletion of the post-while block is honest.
   - **B7 outcome enum** is unchanged — the doom site emits the same five labels as natural-turn (it specifically does NOT use `continue_at_max_iter` because `at_max_iter=False`).
   - **B6** gained `test_hooks_stop_doom_loop_dispatch.py` with six sub-cases pinning: payload `turn_end_reason`, fallback last_assistant_message, `at_max_iter=False` on emit, the `final_msg_doom` commit, force_continue honored at doom (outcome="continue"), and cap-hit on doom (outcome="reentry_capped" + WARNING text).

**No design changes — pass 15 corrects a real exit-path omission**, not a contract change. The third site has identical Stop semantics to the other two; the only new piece of vocabulary is the `"doom_loop"` `turn_end_reason` value and the `final_msg_doom` fallback string.

**rev 2026-05-05 — review pass 14 (replay-emission gate + suppressOutput stdout-claim correction).** Two findings landed; both correct invariants the previous draft asserted but did not actually deliver:

1. **P1 — `_dispatch_stop` returned an empty `StopHookResult()` when no plugin rules matched, and B3 still emitted `PLUGIN_HOOK_FIRED` at every terminal branch.** Two failure modes: (a) `agent._plugin_hook_rules == []` — helper still returned a result; B3 still called `_emit_stop_hook_fired(..., outcome="allow", ...)`; replay channel got a noisy `hook_name="Stop", outcome="allow"` event from a turn that ran zero hook code. (b) Rules existed but none targeted Stop — `dispatch_stop` filtered to an empty `stop_rules` list and returned an empty result; B3 still emitted; same noise. The existing `_dispatch_user_prompt_submit` (`chat_loop.py:332-333`) avoids case (a) via early-return-before-emit, but cannot see case (b) because the rule list is non-empty. Resolved by adding a new field `matched_rule_count: int = 0` on `StopHookResult` (B1), set inside `dispatch_stop` to `len(stop_rules)` **before the run loop** (so single-rule short-circuits via `blocking_error`/`force_continue` still report the correct count), and gating emission inside `_emit_stop_hook_fired` on `stop_result.matched_rule_count > 0` (B7). The emit payload also gains a top-level `matched_rule_count` field for replay observability. New regression test `test_hooks_stop_no_emit_when_no_stop_rules.py` covers all three sub-cases (no rules / rules-but-no-Stop / Stop rule present).
2. **P2 — `suppressOutput` was claimed to suppress `PLUGIN_HOOK_FIRED.stdout`, but Agentao never emits a `stdout` field there.** The B1 docstring said the Claude-parity meaning hides hook stdout from "the user-visible transcript and from `PLUGIN_HOOK_FIRED.stdout`," and matrix row "JSON `suppressOutput`" said "the corresponding `PLUGIN_HOOK_FIRED` payload omits the stdout body." The `_emit_stop_hook_fired` body emits only `hook_name`, `outcome`, `at_max_iter`, `matched_rule_count`, `added_context_count`, `suppress_output` — there is no `stdout` field, never has been, and `StopHookResult` has no raw-stdout field either. Two repair options were available: (i) add a stdout/raw-output field on the result and gate it; (ii) honestly document that the Claude-parity portion of the contract is vacuous on this channel today. Took option (ii) — adding stdout projection would balloon scope, surface privacy-sensitive subprocess output to the replay channel by default, and make the matrix's already-🟡 row drift into a feature. Matrix row 68 rewritten to "Vacuously honored on the raw-stdout channel today. Agentao does **not** project hook stdout onto `PLUGIN_HOOK_FIRED` (the emit carries verdict + counts only)..." with status downgraded from "✅ for the raw-stdout meaning" → "🟡 — vacuous on this channel today; field is recorded faithfully but does not gate any current display path." B1 docstring rewritten in parallel: the Claude-parity meaning section now says "**In Agentao today this is vacuous**" and explicitly lists the fields the emit carries. The Agentao-extension (gate `additional_contexts` echo) portion is unchanged — it remains the *only* concrete behavior `suppress_output=True` produces today.

**No design changes — pass 14 corrects two false invariants the plan was claiming but not delivering.** Finding #1 introduces a new field (`matched_rule_count`) but only to document and enforce a contract the plan already stated ("emission lives in B3 because the outcome label depends on caller branching" — silently emitting `outcome="allow"` from a turn that ran zero Stop rules contradicts the spirit of that contract). Finding #2 fixes the doc to match the code, not the other way around.

**rev 2026-05-05 — review pass 13 (B7 helper bodies vs actual chat_loop / hooks / transport surface).** Three P1 findings landed; all three are pseudocode-vs-real-code drift in the helper bodies that would have produced runtime errors on first invocation:

1. **`_dispatch_stop` referenced `self._adapter` / `self._dispatcher` / `self._plugin_hook_rules`, none of which exist on `ChatLoopRunner`.** Pass 11 introduced these attribute references when promoting the helper into B7, but `ChatLoopRunner.__init__(agent)` (`agentao/runtime/chat_loop.py:112-113`) only stores `self._agent`. The existing `_dispatch_user_prompt_submit` (`chat_loop.py:330-348`) does the right thing: import `ClaudeHookPayloadAdapter` and `PluginHookDispatcher` lazily inside the method, instantiate them as locals (`adapter = ClaudeHookPayloadAdapter()`, `dispatcher = PluginHookDispatcher(cwd=cwd)`), and read `agent._plugin_hook_rules`. B7's `_dispatch_stop` body now mirrors that pattern verbatim — copying it into chat_loop.py would have AttributeErrored on the first hook dispatch.
2. **`build_stop(...)` was called with `transcript_path=None`, but the A3 builder signature does not accept that parameter.** A3's `build_stop(self, *, session_id, cwd, last_assistant_message, stop_hook_active, turn_end_reason, permission_mode)` (line 396-399) hardcodes `"transcript_path": None` *inside* the dict literal — there is no parameter for it. Pass 11 / 12's helper body wrote `transcript_path=None` as a kwarg, which would TypeError on first call. Removed from the helper body; added an inline comment explaining that the builder owns the field per OQ1 (a). Builder signature stays as-is (single point of authority for the field's value).
3. **`_emit_stop_hook_fired` called `agent.transport.emit(EventType.X, {...})`, but the Transport protocol is `emit(self, event: AgentEvent)`.** The protocol at `agentao/transport/base.py:28` accepts a single `AgentEvent` argument; passing the type and dict separately is a type error. Every existing emit site in `chat_loop.py` (e.g., line 360 for the UserPromptSubmit hook) wraps the dict in `AgentEvent(EventType.PLUGIN_HOOK_FIRED, {...})`. Helper body fixed to wrap. Also added `try/except Exception: pass` around the emit call to honor the protocol's "must not raise" contract — mirroring the existing UserPromptSubmit emit at `chat_loop.py:368-369`. Imports note: `AgentEvent` and `EventType` are already imported at the top of `chat_loop.py` for existing emit sites, no new import needed; the helper code block now states this explicitly.

**No design changes — pass 13 makes the helper bodies actually compile-and-run against the real surfaces** (`ChatLoopRunner` attributes, `build_stop` signature, `Transport.emit` protocol). Without these fixes, pass-11's helper code would have looked plausible in isolation but failed three different runtime checks the moment a Stop hook fired.

**rev 2026-05-05 — review pass 12 (B3 callable surface + emission visibility + max-iter cap symmetry).** Three findings landed; all three are spec-vs-pseudocode drift that would have shipped wrong code:

1. **`payload_for_stop` was undefined at the B3 call sites.** B3's natural-turn pseudocode (line ~686) and max-iter pseudocode (line ~782) both called `self._dispatch_stop(payload_for_stop, ...)` with no preceding `payload_for_stop = ...` line. A4 said the helper builds the payload internally; pass-11's B7 declared the helper as `_dispatch_stop(payload, assistant_content, *, at_max_iter)`, taking the payload as a parameter. The two sections contradicted each other and the implementer would have to guess which side owns construction. Resolved by collapsing onto the A4 contract: helper signature is now `_dispatch_stop(self, agent, assistant_content, *, turn_end_reason, at_max_iter)` and the helper calls `self._adapter.build_stop(...)` internally. A4's "the helper builds the payload" is now the only payload-construction site in the plan. B7 shows the full helper body. B3 call sites now pass `turn_end_reason="final_response"` / `"max_iterations"` — no `payload_for_stop` variable anywhere.
2. **B3 pseudocode had no `transport.emit(...)` calls anywhere — implementer would silently miss B7's emission requirement.** Pass 11 moved emission ownership to B3 in prose, but did not show emit calls in the B3 code blocks. An implementer reading B3 top-to-bottom would have written code that returns/continues at every terminal branch with zero PLUGIN_HOOK_FIRED events, then would be surprised by the failing transport-event assertions in `test_hooks_stop_event.py` / `test_hooks_stop_reentry_cap.py` etc. B3 now shows an explicit `self._emit_stop_hook_fired(agent, outcome="...", at_max_iter=..., stop_result=...)` call before each `return` / `continue`. B7 defines the small helper `_emit_stop_hook_fired` so the payload shape has one source of truth. Five terminal branches × two sites (natural-turn + max-iter, minus the always-allow `additional_contexts` decorations sharing the allow path) — all visible as concrete call sites.
3. **Max-iter cap-hit was an implicit fall-through, asymmetric with natural-turn.** B3's natural-turn block has an explicit `if self._stop_reentries >= self._stop_reentry_cap:` branch that logs WARNING and emits `reentry_capped`. The max-iter block instead used `if force_continue and reentries < cap:` and let cap-hit silently fall through to the "Allow OR cap-hit" return. That meant: (a) the max-iter cap-hit emits no WARNING (asymmetric with natural-turn), (b) without pass-12 fix #2, the cap-hit path emits no `PLUGIN_HOOK_FIRED` either (sliding into the allow tail), (c) the comment "Allow OR cap-hit (the latter rides on outcome=reentry_capped via B7)" was a lie — there is no later code path that distinguishes them. Resolved by splitting: max-iter `if stop_result.force_continue:` now contains an explicit `if reentries >= cap:` branch with WARNING log and `_emit_stop_hook_fired(..., outcome="reentry_capped", at_max_iter=True, ...)`, parallel to natural-turn. The dangling "Allow OR cap-hit" comment is gone; the trailing path is now labelled "Allow path (no force_continue)".

The B7 outcome table's `reentry_capped` row was updated to reference "the explicit `if force_continue and reentries >= cap` branch B3 now spells out" instead of the old "cap-hit fall-through at the max-iter site" wording.

**No design changes — pass 12 surfaces what was already decided.** All three findings are about what the pseudocode shows vs what the prose/contract requires. The contract is unchanged; the pseudocode catches up.

**rev 2026-05-05 — review pass 11 (B7 emit ownership + Semantics field-name + A6 test schema).** Three findings landed; all three are real spec-vs-implementation drift that would have produced wrong code at PR-2 time:

1. **`PLUGIN_HOOK_FIRED` emission moved from `_dispatch_stop` helper to B3 wiring at terminal branches.** Pass-9 wired `_dispatch_stop` as the single helper for Stop dispatch and pass-7's B7 told it to populate `PLUGIN_HOOK_FIRED`. But the helper only sees what the hook *requested* (`force_continue`, `blocking_error`); the four labels `continue` / `continue_at_max_iter` / `reentry_capped` / `allow` depend on whether the caller is the natural-turn site or the max-iter site **and** on whether `_stop_reentries < _stop_reentry_cap`. Both decisions live outside the helper. B7 now explicitly splits responsibilities: `_dispatch_stop(*, at_max_iter)` returns `StopHookResult`; the chat-loop wiring at each terminal branch in B3 emits `PLUGIN_HOOK_FIRED` with the correct outcome. This also keeps the helper unit-testable in isolation (B6 dispatcher tests don't need a chat-loop instance). Two B6 tests gained outcome-label assertions so the split cannot regress.
2. **`turnEndReason` (camelCase) → `turn_end_reason` (snake_case) in the Semantics section.** A3's payload-fields table, the builder code, A4's emit table, and the A6 payload-shape test all already used snake_case (consistent with the pass-10 alignment). The Semantics section at lines 34/35/37 still said `turnEndReason`, contradicting the "Claude-flat snake_case top-level key" contract A3 documents. An implementer reading top-down would have written the camelCase form into the payload builder. Fixed at the three Semantics call-sites in EN; mirrored in ZH.
3. **A6's `test_hooks_stop_precompact_reject_prompt_type.py` now uses the real `hooks.json` schema.** The pass-5 description used pseudo-notation `{event: "Stop", hook_type: "prompt"}` which, taken as literal JSON, has no `"type"` key — `entry.get("type", "")` would return `""` and the rule would land in the existing `"Unknown hook type"` branch, **not** the new per-event `SUPPORTED_HOOK_TYPES_BY_EVENT` branch added by A1. The test as written would have passed before A1's change and would not have proven A1 actually fires. Rewrote to feed `parse_dict` the real shape (`{"hooks": {"Stop": [{"type": "prompt", ...}]}}`), assert (a) `rules` empty, (b) warning text names *both* event and type (distinguishing the new branch from the generic fallback), (c) `field == "hooks"`, plus a defense-in-depth sub-case that constructs a `ParsedHookRule` directly and asserts `is_supported is False`. Mirrored verbatim into ZH.

No code or behavior changes — emission ownership clarification (#1) is a pure spec correction (B3 already had the cap-check logic; the helper just shouldn't be claiming to emit). Both #2 and #3 are documentation alignment that prevents the wrong code from being written.

**rev 2026-05-05 — review pass 10 (field-name + parser-branch + B1-comment alignment).** Three findings landed; all are documentation/code-spec alignment issues that would have wasted implementer time:

1. **Unified on `compaction_type` (snake_case) in current-tense prose.** The Semantics section and A4's emit table referenced `compactionType` (camelCase), while A3's payload-fields table, builder code, and A6's payload-shape test all already used `compaction_type`. The mismatch contradicted A3's "Claude-flat snake_case top-level" contract and would have led to an implementer writing `compactionType` at the emit site and breaking the A6 test on the very first run. Fixed in EN Semantics line 39, A4 table header, A4 prose; same three locations in ZH. Historical revision notes are left as-is — they describe the field name in effect at write time.
2. **Parser-side per-event hook-type check now spelled out, not just `is_supported`.** Pass 5 added `SUPPORTED_HOOK_TYPES_BY_EVENT` and extended the runtime `is_supported` predicate. But the existing parser at `agentao/plugins/hooks.py:120-140` only consults the bare `SUPPORTED_HOOK_TYPES`, so a `{event: "Stop", type: "prompt"}` rule still flowed into `rules` with `is_supported == False` — silently inert at runtime, contradicting the matrix's "Rejected at parse time" row and A6's "parser logs warning" assertion. A1 now shows the explicit parser branch added after the bare-type check and before `rules.append`: consult `SUPPORTED_HOOK_TYPES_BY_EVENT.get(event_name, SUPPORTED_HOOK_TYPES)`, emit a `PluginWarning`, `continue`. Runtime `is_supported` remains as defense-in-depth (same role as A2's runtime matcher guard). A6's prompt-type-rejection test asserts the parse-time drop, not just the runtime flag flip.
3. **B1 scratch-field comment rewritten to match B2 reality.** The pass-2 wording said `messages` and `prevent_continuation` exist to "let `_run_command_hook` / `_parse_command_output` be reused without `AttributeError`," but pass 4's B2 explicitly forks a Stop-specific runner (`_run_stop_command_hook`) and parser (`_parse_stop_command_output`) precisely *because* reusing the UserPromptSubmit code path silently demotes Claude's exit-code-2 contract. The B1 comment was stale and would have led an implementer to attempt the (rejected) reuse path. Rewritten to: `messages` carries Stop-specific-runner attachments, `prevent_continuation` is parser-write tolerance for legacy `preventContinuation: true` JSON; neither is consumed by chat-loop wiring.

No code or behavior changes — pure spec alignment so PR-1 / PR-2 implementers are not led astray by stale field names, missing parser branches, or stale comments pointing at a runner-reuse path the plan no longer takes.

**rev 2026-05-05 — review pass 9 (doc coherence + test design).** Four findings landed; no new behavior decisions, but three real documentation/test gaps closed and one stale-wording cleanup:

1. **PreCompact matcher row split: runtime ✅ vs config-shape 🟡.** Pass 6 wrote the matcher row as ✅, which conflated two questions: (i) does the regex evaluator work? — yes, with `re.fullmatch` on Agentao-shape matcher dicts; (ii) does a literal Claude `hooks.json` whose `"matcher"` is a top-level string load? — no, A1/A2 require object shape and pass 8 made the parser drop string matchers. The matrix now has two adjacent rows: "Matcher (PreCompact) — runtime regex evaluation" ✅ (assuming Agentao matcher object shape), and "Matcher (PreCompact) — config file shape" 🟡 (string matchers dropped at parse time; subsumed by the pre-existing config-shape ❌ row). This stops a Claude migrator from reading "PreCompact matcher ✅" and being surprised when their `"matcher": "auto"` rule silently does not load.
2. **`dispatch_stop` signature change A→B documented + A6 test rewrite called out.** A2 declares `dispatch_stop -> list[HookAttachmentRecord]`; B2 redeclares it as `-> StopHookResult`. The A6 test `test_hook_dispatcher_stop.py` was written against A's signature and would fail to compile under PR-2. The plan now explicitly: (a) calls out the signature change in A2's code block ("Phase B will replace dispatch_stop's return type") and in B2 under a new "Test impact" subsection; (b) lists the test rewrite in PR-2's Sequencing bullet. `dispatch_pre_compact` is unaffected (PreCompact stays observe-only — B5). The breaking change is contained inside Agentao (no host-public API).
3. **`stop_hook_active` wired to `_stop_reentries` + B6 re-entry test added.** A3's payload-fields table claimed `stop_hook_active` flips false→true on second-and-subsequent dispatches in the same `chat()` call, but B3 pseudocode never showed the wiring and B6 only checked key presence. A4's Stop-emit subsection now spells out `stop_hook_active = (self._stop_reentries > 0)` in the dispatch helper, and B6 gains `test_hooks_stop_hook_active_reentry.py` covering: (a) first dispatch = `False`; (b) post-`force_continue` re-dispatch = `True`; (c) fresh `chat()` call after counter reset = `False` again. This pins the field's claim instead of leaving it as an unguarded promise.
4. **Stale "three ❌ rows" / "prompt/agent for Stop and PreCompact" current-tense wording corrected.** Pass 8 reclassified PreCompact prompt/agent from ❌ to N/A (Claude does not support either there). But two current-tense statements still said "intentional ❌ ... prompt/agent hook types for Stop / PreCompact" (matrix preamble) and "three matrix ❌ rows that reject prompt/agent for Stop and PreCompact" (rationale-section opener). Both now say "for Stop only". Historical revision notes (passes 6–7) are left as-is since they accurately reflect the state at the time of writing, with pass 8's correction documented separately.

**rev 2026-05-05 — review pass 8 (parser correctness + matrix premise corrections).** Five findings landed; pass 8 corrects two real bugs introduced in earlier drafts and three Claude-doc misreadings in the compatibility matrix:

1. **Parse-time matcher fix no longer inverts the warning's intent.** Pass 6 added a parser branch that set `matcher = None` when a non-dict matcher was detected, with a warning saying "rule will not match." But the runtime contract at `agentao/plugins/hooks.py:394` is `if rule.matcher is None: return True` — i.e., `None` ≡ "match every event." The pass-6 fix would have silently turned a misconfigured filter into a match-everything rule. Pass 8 changes the parser to **drop the rule entirely** (`continue` past `rules.append`), matching Claude Code's "bad rules don't load" semantic. The runtime guard at the top of `_matches` is now true defense-in-depth (only fires if a code path constructs `ParsedHookRule` outside the loader). The A6 test was rewritten to assert the rule does **not** load, with an explicit note that earlier drafts' "normalize to None" approach was the bug being guarded against.
2. **Parser warning uses `PluginWarning`, not a raw f-string.** The loader's `warnings` list is typed `list[PluginWarning]` (`hooks.py:82`); pass 6's `warnings.append(f"...")` would have broken the type. Pass 8 spells out `PluginWarning(plugin_name=plugin_name, message=..., field="hooks")` to match every other warning emit site in the loader.
3. **Compatibility matrix gains `http` and `mcp_tool` hook-type rows.** Earlier passes only enumerated `command` / `prompt` / `agent`. Claude Code's documented hook-type set is `command` / `http` / `mcp_tool` / `prompt` / `agent`; Stop supports all five and PreCompact supports `command` / `http` / `mcp_tool`. Agentao recognizes only `command` / `prompt`, with `http` and `agent` in `KNOWN_UNSUPPORTED_HOOK_TYPES` and `mcp_tool` not recognized at all. The matrix now lists `http` (Stop, PreCompact) ❌ and `mcp_tool` (Stop, PreCompact) ❌ as pre-existing Agentao gaps, calling out the runner-bridge that `mcp_tool` would need to reach `agentao/mcp/client.py`. Hosts migrating Claude `hooks.json` files now see a complete picture of which hook types will load.
4. **PreCompact `prompt`/`agent` rows corrected from "we say no" to "neither side supports it".** Earlier passes wrote those rows as "Claude supports, Agentao chooses not to" with a dedicated rationale subsection. That premise is wrong: Claude's documented hook-type matrix lists PreCompact as supporting `command` / `http` / `mcp_tool` only — `prompt`/`agent` are not Claude features for PreCompact. The matrix rows are now labelled `N/A — not a compatibility gap` (kept for completeness so a reader doesn't ask "did we miss this?"). The "Why not prompt-type hooks for Stop / PreCompact" section's PreCompact subsection was rewritten to acknowledge the correction; the Stop subsection still applies as written.
5. **`suppressOutput` demoted from ✅ to a split row (✅ + 🟡).** The matrix now has two adjacent rows: "Claude semantic — hide raw stdout / debug-log" ✅, and "Agentao extension — gate `additional_contexts` echo on the assistant's final answer" 🟡. Claude's documented `suppressOutput` only covers raw stdout / debug-log; structured `hookSpecificOutput.additionalContext` is a separate channel. B3's gating of the `<stop-hook>` echo is an Agentao-specific extension, not parity. B1 docstring + B3 wiring comment now spell this out, and the matrix tells hosts that want strict Claude semantic to keep `suppressOutput` and `additionalContext` on separate hook outputs.

**rev 2026-05-05 — review pass 7 (prompt-type rejection rationale).** No new findings; this pass converts the pass-5/pass-6 parse-time rejection of prompt/agent hook types for Stop and PreCompact from a documented *behavior* into a documented *decision*. New top-level section "Why not prompt-type hooks for Stop / PreCompact" sits between the compatibility matrix and Phase A and explains:

- **Stop:** capability-redundant with `command`-type hooks. A reviewer use case is fully served by a command-hook shim that internally calls an LLM and emits Claude Code Stop JSON; supporting prompt-type natively would force Agentao to define a third Stop control surface (raw conversation injection) for which the Claude documentation itself has no canonical free-text → structured-output mapping. The lost portability is subsumed by the pre-existing "Hook config file path / shape" ❌ row.
- **PreCompact:** no destination for a prompt-hook response under our observe-only PreCompact contract (B5). It cannot gate compaction, cannot redirect compaction, and using an LLM call to produce an audit signal is the wrong tool for the job (`PLUGIN_HOOK_FIRED` already covers observation). Revisit if/when PreCompact gating lands (currently `PRECOMPACT_GATE_PLAN.md`, not on roadmap).

The matrix's three ❌ rows for prompt/agent Stop and PreCompact now point at this section and label themselves "deliberate, not 'not yet'". The Out-of-scope entry for prompt-type hooks links to the same section. No code or test changes; this is purely a documentation-correctness pass making the why visible alongside the what.

**rev 2026-05-05 — review pass 6 (matrix completeness + precedence + type safety).** Four findings landed; no earlier decisions reversed. Pass 6 closes remaining gaps in the compatibility matrix and pins two contracts the plan had previously left implicit:

1. **Stop / PreCompact prompt and agent hook types now have explicit ❌ rows in the matrix.** Pass 5 added parse-time rejection (A1's `SUPPORTED_HOOK_TYPES_BY_EVENT`), but the compatibility matrix did not enumerate the consequence. Claude Code's hooks reference documents prompt-based Stop hooks (with examples), so a Claude `hooks.json` migrating to Agentao that uses `{event: "Stop", hook_type: "prompt"}` would silently fail to load. Three new ❌ rows in the matrix (Stop prompt, Stop agent, PreCompact prompt/agent) call this out as a load-time incompatibility, with a workaround (convert prompt → command shim emitting `additionalContext`).
2. **`permission_mode` demoted from ✅ to 🟡, and the value-space divergence is now documented + tracked.** The matrix previously claimed full common-input compatibility, but Agentao's value space (`"read-only" | "workspace-write" | "full-access" | "plan"`) shares only `"plan"` with Claude Code's (`"default" | "plan" | "acceptEdits" | "auto" | "dontAsk" | "bypassPermissions"`). A Claude hook script that branches on `permission_mode == "acceptEdits"` would mis-route under Agentao. Matrix split into two rows (key shape ✅, value space 🟡); new Open Question 5 documents three options (verbatim emit / translate / dual-emit) with Phase A landing on verbatim-emit + documented divergence; the question explicitly forbids silent translation.
3. **`continue: false` precedence pinned in B2 + tested in B6.** Claude Code documents `continue: false` as taking precedence over event-specific decision fields. The previous B2 parser table listed `decision: "block"` and `continue: false` as siblings without ordering, so a hook returning `{"continue": false, "decision": "block"}` could have set `force_continue=True` first and then never reached the `continue: false` row. B2 now has a numbered evaluation-order column, row 1 is `continue: false` setting a `continue_false` scratch flag that suppresses every later `force_continue`-producing branch (`decision: "block"`, `preventContinuation`). New invariant #3 spells the rule out; new B6 test `test_hooks_stop_continue_false_precedence.py` covers three combinations (block, preventContinuation, blockingError) with the documented expectations. `blockingError` is intentionally **not** suppressed by `continue: false` — both are "stop the turn" intents, made explicit in the table and tested.
4. **Matcher type guard against non-dict values.** The loader at `hooks.py:161` stores `entry.get("matcher")` raw; A2's `rule.matcher.get("trigger")` would `AttributeError` on a string matcher (e.g., a Claude config translation layer that emitted `"matcher": "auto"` instead of `"matcher": {"trigger": "auto"}`). Two-layer defense added: (i) parse-time check in A1's loader patch warns and collapses non-dict matchers to `None`; (ii) runtime guard at the top of `_matches` warns and returns `False` (no-match) so a future code path that bypasses the parser still cannot crash. New A6 test `test_hooks_pre_compact_matcher_non_dict_guard.py` covers both layers across three sub-cases (string matcher, list matcher, parser-bypass).

**rev 2026-05-05 — review pass 5 (Claude Code compat correctness).** Five findings landed, all five validated against current source. No earlier decisions reversed; pass-5 tightens previously-claimed compatibility:

1. **`suppressOutput` is now actually honored in B3 wiring.** B1 declared the field; B3's natural-turn allow path appended `<stop-hook>` blocks unconditionally. Added `not stop_result.suppress_output` guard around the echo block (B3 natural-turn site). The max-iterations exit path does not echo `additional_contexts` at all — that asymmetry is now documented and gated for any future addition.
2. **PreCompact matcher uses regex (`re.fullmatch`), not glob.** A2 previously routed `rule.matcher["trigger"]` through `_glob_match` (`hooks.py:832-844`), which has no regex alternation: a Claude pattern like `manual|auto` would silently never match. Added a tiny `_regex_match_full` helper local to PreCompact and use it for the `trigger` field; the `toolName` matcher on other events keeps `_glob_match` (per-event matcher dialect, documented in A2). A6's `test_hooks_pre_compact_matcher_trigger.py` now covers four cases (`"manual"`, `"auto"`, `"manual|auto"`, `".*"`) — cases (c) and (d) would have failed against `_glob_match`.
3. **PreCompact `reason` values match the actual `_emit_context_compressed` arguments.** A4 listed `"size_threshold"` for both threshold sites, but `chat_loop.py:413` emits `"microcompact_threshold"` and `chat_loop.py:443` emits `"compression_threshold"`. The plan's stated invariant in A3 ("`reason` mirrors the existing argument") is now actually upheld in A4's table; the A3 stable-values list was updated correspondingly. The `"api_overflow"` and `"api_overflow_after_compression"` values were already correct.
4. **Prompt-type hooks for Stop / PreCompact are rejected at parse time.** `SUPPORTED_HOOK_TYPES = {"command", "prompt"}` (`models.py:207`) plus `is_supported = hook_type in SUPPORTED_HOOK_TYPES and event in SUPPORTED_HOOK_EVENTS` (`models.py:226`) would have parsed `{event: "Stop", hook_type: "prompt"}` as supported, then `_dispatch_lifecycle` would silently drop it (only command branch exists). A1 introduces `SUPPORTED_HOOK_TYPES_BY_EVENT` and extends `is_supported` to consult it; an A6 test verifies the rejection + parser warning.
5. **B6 covers the Claude JSON output fields B2 claims to support.** Pass 4 added `suppressOutput`, `systemMessage`, `hookSpecificOutput.additionalContext`, and exit code 2 to the matrix and to B2's parser table, but B6 only tested `decision: "block"` + `blockingError` + reentry cap. Five new tests added in B6 (and one in A6 for common-fields precedence) so every ✅ row in the compatibility matrix has direct test coverage. Critical because pass-5 finding #1 demonstrated that even claimed support can be unwired in code.

**rev 2026-05-05 — review pass 4 (Claude Code compatibility pivot).** The product goal is now: a Claude Code Stop / PreCompact hook script runs unchanged in Agentao. Six findings landed; one earlier decision is **reversed**:

1. **Reversal of pass-2 finding #2** ("payload envelope pinned to existing adapter shape; Claude flat schema is out of scope"). For Stop and PreCompact specifically, the wire shape is now Claude Code's flat snake_case top-level schema (A3). Other adapter methods (UserPromptSubmit / SessionStart / etc.) remain on the agentao `{event, data}` envelope — converting all of them is still out of scope. `_matches` is extended (A2) to handle the dual shape, and the asymmetry is documented in the new compatibility matrix.
2. **New "Claude Code compatibility matrix" section.** Authoritative ✅ / 🟡 / ❌ table covering event names, wire input, common input fields, exit codes, JSON output fields, matchers, decision/gate semantics, and config-file shape. Subsections A3, A4, A6, B1, B2, B5 implement the matrix.
3. **Stop payload now includes `last_assistant_message`** (A3, A4). Threaded from `assistant_content` (natural turn) and `assistant_content_max` (max-iter) before they are appended to history. This is the primary Stop hook use case (review the answer without parsing a transcript) and was missing.
4. **Stop honors exit code 2 (B2).** The Stop dispatcher forks a dedicated `_run_stop_command_hook`; reusing `_run_command_hook` would silently demote `exit 2` to a benign warning attachment (`hooks.py:520-533`). The Stop runner translates `exit 2 + stderr` into `force_continue` + `follow_up_message`, matching Claude Code's documented contract.
5. **Stop JSON parser handles the full Claude Code Stop output schema (B2).** `decision: "block"` + `reason`, `continue`, `stopReason`, `suppressOutput`, `systemMessage`, `hookSpecificOutput.additionalContext` are all mapped to `StopHookResult`. `StopHookResult` gains `suppress_output` and `system_message` fields (B1). The legacy top-level `additionalContext` and Agentao-internal `blockingError` / `preventContinuation` are still tolerated.
6. **PreCompact matcher extension (A2).** Claude Code matches PreCompact rules against `trigger ∈ {manual, auto}`; `_matches` previously only honored `toolName`. Phase A extends it. `trigger="manual"` is never emitted (no `/compact` CLI), so manual-matcher rules will never fire — documented as 🟡 in the matrix and with a dedicated test (A6).
7. **PreCompact blocking relabelled as a Claude Code compatibility gap (B5).** The previous "deferred" framing was too optimistic — there is no roadmap item to add PreCompact gating, because honoring "host said no, still doesn't fit" is its own design conversation. Hosts cannot rely on Claude PreCompact `decision: "block"` scripts to gate compaction in Agentao without explicit verification.

**rev 2026-05-05 — review pass 3.** Four follow-up findings landed:

1. **`force_continue` no longer falls through when `follow_up_message` is empty (B2, B3).** The pass-2 `preventContinuation` translation set `force_continue=True` but left `follow_up_message=None`, and B3's check was `force_continue and follow_up_message`, so the translation silently fell through to the `allow` path. Two-sided fix: B2 also synthesizes `follow_up_message` from `stopReason`, and B3 (both natural-turn and max-iter sites) now treats `force_continue` as authoritative and synthesizes the injected text from `follow_up_message or stop_reason or "Stop hook requested continuation"` at use time.
2. **Stop / PreCompact semantics defined explicitly (new "Semantics" section, A3, A4).** A new top-level "Semantics — what does each event mark?" section pins `Stop = BeforeTurnEnd` and `PreCompact = BeforeMessagesMutation`, and disclaims session/process scopes. The Stop payload gains `turnEndReason: "final_response" | "max_iterations"` so hooks can answer "stop what?" without name-overloading. A4 lists the two Stop sites with their `turnEndReason` values.
3. **Minimal-history emergency truncation now fires `PreCompact` (A3, A4).** `chat_loop.py:557` (`agent.messages = agent.messages[-2:]` after a second consecutive context-overflow) is a fourth compaction site that pass 2 missed. A4's emit table now includes it; PreCompact payload gains `compactionType: "microcompact" | "full" | "minimal_history"` and a `reason: str` mirror of `_emit_context_compressed` so hosts can distinguish heuristic compaction from post-failure recovery.
4. **TL;DR wording corrected.** "Hosts that already implement Claude-Code-style hooks get drop-in observability" oversold the parity given A3 already explained the wire envelope is not Claude Code's flat schema. Replaced with "the hook event *names* match Claude Code's, but the wire envelope is Agentao's existing `{event, data}` shape (see A3); observability lands without any new control semantics."

**rev 2026-05-05 — review pass 2.** Four follow-up findings landed:

1. **`StopHookResult` is parser-safe (B1, B2).** The original draft's `StopHookResult` lacked `messages` (and `prevent_continuation`), but `_run_command_hook` / `_parse_command_output` write `result.messages.append(...)` on every code path (`hooks.py:507, 525, 546, 561, 578, 591, 607, 618`) and set `result.prevent_continuation = True` on `hooks.py:589`. Both fields are now scratch fields on `StopHookResult` so the existing parser is reusable without an `AttributeError`; B2 spells out the `isinstance`-based translation that maps `decision: "block"` → `force_continue` and `preventContinuation` → `force_continue`.
2. **Payload envelope pinned to existing adapter shape (A3).** Earlier the plan listed `hook_event_name` / snake_case fields, which conflicts with every existing `build_*` returning `{"event": "...", "data": {camelCase}}` (and with `_matches` reading `payload["data"]`). A3 now specifies the agentao envelope and notes that adopting Claude Code's flat snake_case schema is a pre-existing cross-cutting refactor, out of scope here.
3. **Max-iterations dispatch site pinned (B3).** The earlier "exit block" wording was ambiguous between the top-of-loop max-iter check and the post-while finalization. The dispatch is now nailed to the `else: # "stop"` arm at `chat_loop.py:185-186` — the only site from which `force_continue` can re-enter the loop. Post-while finalization (`chat_loop.py:308-324`) becomes unreachable under PR-2 and is deleted.
4. **"Hook attached to turn" wording corrected.** §"Why two phases (recap)" line 22 used to say stdout was "attached to the turn"; now it says "captured into the dispatcher's returned `list[HookAttachmentRecord]` (currently discarded by every call site — see A6 caveat)," matching the actual A6 contract.

**rev 2026-05-05 — review pass 1.** Five findings landed:

1. **Attachment ownership clarified (A6).** `_dispatch_lifecycle` returns `list[HookAttachmentRecord]` but every existing caller (`tool_executor.py:591`, `cli/session.py:79`) discards it. Phase A inherits that contract and asserts attachments only at the dispatcher boundary. Cross-cutting attachment-pipeline work moved to `PLUGIN_HOOK_ATTACHMENT_PIPELINE_PLAN` (out of scope).
2. **Stop gate no longer pollutes history (B3).** `final_msg` is now constructed *before* dispatch and only appended after the hook outcome is known; `blocking_error` rewrites `final_msg.content`, `force_continue` keeps the original answer in history before the follow-up user message.
3. **`PLUGIN_HOOK_FIRED` visibility scoped (A5, B7, Out of scope).** The label "host EventStream" was wrong — it is a transport/replay event today; `agentao.host.EventStream` does not include plugin-hook events. Promotion is its own ticket.
4. **Outcome enum unified (B7).** Final set: `{"allow", "block", "continue", "continue_at_max_iter", "reentry_capped"}`. `"modify"` removed (Stop's `additional_contexts` ride on `"allow"` with `added_context_count > 0`); Open Q4's `continue_at_max_iter` is now a first-class member.
5. **Re-entry cap is a constructor constant (B4, Open Q3).** Dropped the `.agentao/settings.json` plumbing — that file has only two readers today and adding a third for an untuned knob is premature configuration surface. Default `3` lives on the chat loop; promotion to a settings key is a follow-up if/when a real host needs it.
