# 5.7 Plugin Hooks — The Control-Plane Extension Surface

> **What you'll learn**
> - **Why** hooks get their own chapter — they live on a different axis from the previous six sections
> - **The 8 events** at a glance, including Phase B's `Stop` control surface
> - **Authoring a rule**: `hooks.json` fields, command vs. prompt, per-event constraints
> - **Verdicts**: the four attachment types, `UserPromptSubmitResult` / `StopHookResult`, and the `matched_rule_count == 0` silent-emit rule

§5.1–§5.6 all live on the capability plane. This section adds the seventh axis: the control plane.

## 5.7.1 The problem this solves

The previous six extension points (Tool / Skill / MCP / Permission / Memory / SystemPrompt) all answer the **same** question:

> "How do I teach the agent a new capability?"

Hooks answer a **different** question:

> "When the agent reaches step X, can I take a peek / block it / inject something?"

The first axis is the **capability plane**. The second is the **control plane**. They're orthogonal — one plugin can register a Tool *and* attach a `PreToolUse` hook that audits calls to that tool.

::: tip Which axis do you want?
The capability plane is "the agent doesn't know your business; you write an X to plug it in."
The control plane is "the agent is already mid-action; the runtime **exposes that moment to you**, and you decide whether to allow, block, or rewrite."
:::

A note on format: Agentao's hook system is **wire-compatible with Claude Code's `hooks.json`** — rules written for Agentao can be picked up by Claude Code and vice versa. The two exceptions are `Stop` and `PreCompact`, which keep Claude Code's flat snake_case top-level schema instead of Agentao's `{event, data}` envelope (see `CLAUDE_FLAT_EVENTS`).

::: warning This chapter is the **rule-author** view
You'll learn how to write a hook rule, when it fires, what it can output.

The host-side hooks list / disable / hot-reload API is **deliberately not exposed** — that surface is *out* of [4.7 The Embedded Harness Contract](/en/part-4/7-host-contract#4-7-8-whats-not-in-the-contract). If you're building a SaaS platform that wants to give tenants a "manage hooks" toggle, the answer today is: do it inside your own plugin-loading layer, **not** by reaching into `agentao.host`.
:::

## 5.7.2 The eight events at a glance

| Event | When it fires | What it can do |
|-------|---------------|----------------|
| `UserPromptSubmit` | Before the user message enters the turn | Inject context / block the turn / refuse to continue |
| `SessionStart` | A session opens | Init, log, hydrate long-term context |
| `SessionEnd` | A session closes | Cleanup, archive, push metrics |
| `PreToolUse` | Before a tool call | Intercept dangerous args, audit, attach trace |
| `PostToolUse` | After a tool call succeeds | Post-process the result, audit, shape the next-step input |
| `PostToolUseFailure` | After a tool call raises | Classify errors, degrade gracefully, decide whether to abort the turn |
| `Stop` | Turn exit (`final_response` / `max_iterations` / `doom_loop`) | `force_continue` for one more turn / `suppress_output` / `system_message` |
| `PreCompact` | Before compaction (`microcompact` / `full` / `minimal_history`) | Observe only — record, alert; cannot block or rewrite |

Source: `SUPPORTED_HOOK_EVENTS` in `agentao/plugins/models.py`.

::: info `Stop` is a control point. `PreCompact` is an observation point.
After Phase B landed, a `Stop` hook can ask the chat loop to **issue one more LLM call** (via `force_continue` + `follow_up_message`) — that's a real **control signal** that bends the turn's trajectory.
`PreCompact` stays observe-only: `outcome` is always `"allow"`. You can record "what kind of compaction, when did it fire", but you **cannot prevent compaction**.
:::

## 5.7.3 Writing a rule

Hook rules live in a `hooks.json` file inside a plugin (the path is declared in the plugin manifest). The shape is identical to Claude Code's `hooks.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "type": "prompt", "prompt": "Always answer in markdown." }
    ],
    "PreToolUse": [
      {
        "type": "command",
        "command": "/usr/local/bin/audit-tool-call.sh",
        "matcher": { "tool_name": "run_shell_command" },
        "timeout": 30
      }
    ]
  }
}
```

Per-rule fields:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `type` | `"command"` \| `"prompt"` | yes | See below |
| `command` | string | yes for `type=command` | Script/command to run; payload arrives on stdin (JSON), attachments come back on stdout |
| `prompt` | string | yes for `type=prompt` | Literal text injected into the conversation |
| `matcher` | object | no | Filter (e.g. `tool_name`, `trigger`); `null` = match everything |
| `timeout` | int | no | Seconds for `command` rules; default 60 |

A manifest may declare multiple `hooks.json` files; the parser merges them into one set of `ParsedHookRule`s.

### command vs. prompt

**`command`**: spawn an external process. The runtime pipes the event payload into stdin (JSON); the process's stdout becomes the hook's "output attachment". General-purpose, can do dirty work, but pays a process spawn per call.

**`prompt`**: attach a literal string back. **No side effects**, pure text injection, zero process overhead. Ideal for "inject a system reminder every turn" or "decide based on the user message whether to add a one-liner" — anything that lives entirely on the LLM side.

### Per-event constraints

Not every event accepts both types. The allow matrix from `SUPPORTED_HOOK_TYPES_BY_EVENT`:

| Event | Allowed `type`s |
|-------|-----------------|
| `UserPromptSubmit` | `command` + `prompt` |
| `SessionStart` / `SessionEnd` | `command` only |
| `PreToolUse` / `PostToolUse` / `PostToolUseFailure` | `command` only |
| `Stop` / `PreCompact` | `command` only |

::: warning `Stop` and `PreCompact` reject `prompt` on purpose
Reason: Phase B's Stop runner and the lifecycle dispatcher only invoke command hooks for these events. If `prompt` were allowed through, it would parse fine but be **silently dropped at dispatch** — the classic "looks like it works but doesn't" trap.

So the parser surfaces a warning at parse time and skips the rule:

```
Hook type 'prompt' is not supported for event 'Stop' — skipped.
(Allowed for this event: ['command'])
```
:::

### `matcher` and tool aliasing

`matcher` is a JSON object. The most common shape is filtering by `tool_name`:

```json
{ "matcher": { "tool_name": "Bash" } }
```

Note that `Bash` is Claude Code's tool name; Agentao internally calls it `run_shell_command`. The runtime's [`ToolAliasResolver`](https://github.com/jin-bo/agentao/blob/main/agentao/plugins/hooks.py) bridges the two **bidirectionally** — writing either `Bash` or `run_shell_command` matches the same tool.

Matcher string values support glob (`*`, `?`) and **full-string regex** (patterns of the form `^...$` are interpreted as regex). A `null` matcher matches every invocation of that event.

### Reserved-but-not-runnable types

The `http` and `agent` types are listed under `KNOWN_UNSUPPORTED_HOOK_TYPES` — the parser **recognises** them (so they don't surface as "unknown type" errors), but the current runtime doesn't execute them; you get a warning along the lines of "type not yet runnable, skipped." They're placeholders for the future.

## 5.7.4 Outputs and verdicts

After hooks run, they emit **attachments** (`HookAttachmentRecord`); the runtime decides what to do based on attachment type.

### The four attachment types

| `attachment_type` | Meaning | Who emits it |
|-------------------|---------|--------------|
| `hook_additional_context` | "Please add this to the conversation" | Both `command` and `prompt` |
| `hook_success` | "I ran, nothing to add" | Mostly for audit/observability |
| `hook_stopped_continuation` | "Please don't let the turn continue" | Specific events (e.g. the `force_continue` signal on `Stop`) |
| `hook_blocking_error` | "Something went wrong — surface this as an error" | Any event; raises the `[Blocked by hook]` marker on the error stream (see [2.3 Lifecycle](/en/part-2/3-lifecycle)) |

### Aggregated result: `UserPromptSubmitResult`

All hooks running on a `UserPromptSubmit` event are **aggregated** into a single result:

```python
@dataclass
class UserPromptSubmitResult:
    blocking_error: str | None = None      # Any hook emitted hook_blocking_error
    prevent_continuation: bool = False     # Any hook said "don't continue"
    stop_reason: str | None = None
    additional_contexts: list[str] = ...   # All injected contexts, in fire order
    messages: list[HookAttachmentRecord] = ...
```

Aggregation rule: **any hook blocking = whole turn blocked**; `additional_contexts` are concatenated in hook fire order.

### Aggregated result: `StopHookResult` (Phase B)

`Stop` hooks aggregate into:

```python
@dataclass
class StopHookResult:
    blocking_error: str | None = None
    force_continue: bool = False           # The real "one more turn" signal
    follow_up_message: str | None = None   # Becomes the next user message
    additional_contexts: list[str] = ...
    stop_reason: str | None = None
    suppress_output: bool = False          # Don't echo additional_contexts in the final answer
    system_message: str | None = None
    messages: list[HookAttachmentRecord] = ...
    matched_rule_count: int = 0
```

When `force_continue=True`, the chat loop appends `follow_up_message` as the next user turn and **issues another LLM call**. This is the **only** legitimate way for a `Stop` hook to bend the turn — it isn't blocking, it's *continuing*.

`suppress_output` is mainly there for replay fidelity; the chat loop also honors it as a guard so that hook-injected `additional_contexts` don't get echoed into the assistant's final answer.

### The `matched_rule_count == 0` silent-emit rule

::: warning Why you may not see any hook event at all
`matched_rule_count` is the number of rules **selected for dispatch** (not the number that ran successfully). When it's 0 — i.e. no hook rule needed to run for this event — the runtime **emits no `PLUGIN_HOOK_FIRED` event at all**.

Design intent: keep the event stream's volume aligned with what actually happened. A session with no hooks installed shouldn't be drowned in `PLUGIN_HOOK_FIRED` noise.

Be aware of the side effect: you **cannot** treat "did I receive a `PLUGIN_HOOK_FIRED`?" as "did the runtime reach this lifecycle point?" — for the latter, look at other `EventType` members.
:::

### `outcome` enum

Every `PLUGIN_HOOK_FIRED` event carries an `outcome` whose meaning depends on the event:

| Event | `outcome` values |
|-------|------------------|
| `UserPromptSubmit` and other events | `"allow"` / `"block"` |
| `Stop` | `"allow"` / `"block"` / `"continue"` / `"continue_at_max_iter"` / `"reentry_capped"` |
| `PreCompact` | always `"allow"` (observe-only) |

For `Stop`, `continue` vs. `continue_at_max_iter` disambiguates which exit site honored the `force_continue` decision — the former is a normal turn end, the latter is a `max_iterations` exit where the hook still asked for one more pass. `reentry_capped` means the chat loop refused to re-enter again.

Full field table: [4.2 AgentEvent · Replay observability events](/en/part-4/2-agent-events#replay-observability-events).

## 5.7.5 How interception signals reach the UI

§5.7.4 covered how hooks reach a verdict internally; this section is the outside view — the chat loop surfaces hook results to the host in two shapes, and the UI needs to recognise both.

### Shape 1: `additional_contexts` → wrapped and prepended to the next turn

When a `UserPromptSubmit` hook returns `additional_contexts` without blocking, the chat loop prepends them to the user message:

```
<user-prompt-submit-hook>
{ctx[0]}
</user-prompt-submit-hook>
<user-prompt-submit-hook>
{ctx[1]}
</user-prompt-submit-hook>
{original user message}
```

Each context gets its own `<user-prompt-submit-hook>` tag pair — the LLM sees these as system-injected, distinct from anything the user typed.

### Shape 2: early-return markers

When a hook returns a blocking signal, `chat()` doesn't enter the LLM loop at all — it **returns directly** with a marker-prefixed string:

| Marker | Source | Field |
|--------|--------|-------|
| `[Blocked by hook] {message}` | `UserPromptSubmitResult.blocking_error != None` | `blocking_error` verbatim |
| `[Hook stopped] {reason}` | `UserPromptSubmitResult.prevent_continuation == True` | `stop_reason` (defaults to `"Hook prevented continuation"`) |

::: tip How the UI should handle them
Both markers are **prefixes on the return value** — they don't go through an exception path. When your UI sees `chat()` return a normal string starting with one of these prefixes, render that turn as "intercepted", not as an "assistant reply".

See also [2.3 Lifecycle · error signals](/en/part-2/3-lifecycle) for other early-return markers the chat loop produces.
:::

::: warning Stop hooks don't use markers
Even if a `Stop` hook sets `blocking_error`, the chat loop **doesn't** prefix `[Blocked by hook]` onto the final answer. Stop's influence travels through `force_continue` / `suppress_output` / `system_message` instead (see §5.7.4).

If you need to surface a `Stop` hook error to the user, return `system_message` or push it through `additional_contexts` — markers are `UserPromptSubmit`-only.
:::

## 5.7.6 Observability and replay

The traces hooks leave behind sit on two layers: a real-time event stream (for UI / audit) and replay archives (for postmortems).

### Real-time layer: `PLUGIN_HOOK_FIRED`

After each hook dispatch — gated on `matched_rule_count > 0` — the runtime emits a `PLUGIN_HOOK_FIRED` on the transport:

```python
async for ev in agent.events_async():
    if ev.type == EventType.PLUGIN_HOOK_FIRED:
        hook_name = ev.data["hook_name"]
        outcome = ev.data["outcome"]
        # ... branch on hook_name
```

Different `hook_name`s carry different fields (emit shapes are fixed in the chat loop):

| `hook_name` | Always present | Hook-specific |
|-------------|----------------|---------------|
| `UserPromptSubmit` | `outcome` / `matched_rule_count` | `blocking_error` / `stop_reason` / `added_context_count` |
| `Stop` | `outcome` / `matched_rule_count` | `turn_end_reason` / `at_max_iter` / `added_context_count` / `suppress_output` |
| `PreCompact` | `outcome="allow"` / `matched_rule_count` | `compaction_type` / `trigger="auto"` |
| Other lifecycle events | `outcome` / `matched_rule_count` | (minimal field set) |

Full field table: [4.2 AgentEvent · Replay observability events](/en/part-4/2-agent-events#replay-observability-events).

### Archive layer: replay

The replay subsystem also records hook dispatches. By default it captures hook metadata (event name, rule count, outcome); the hook's `output_preview` field (a snippet of the command's stdout) is truncated.

If you need full stdout in the replay log, flip the flag in `.agentao/settings.json`:

```json
{
  "replay": {
    "capture_flags": {
      "capture_plugin_hook_output_full": true
    }
  }
}
```

::: warning Weigh deep capture before turning it on
- **Privacy**: `command`-type hook stdout can contain shell output, API credentials, user data. Replay files **don't** get auto-redacted after the fact.
- **Volume**: long stdout bloats replay files and slows replay-server load times.
- **Secret scan still runs**: deep capture only bypasses length truncation (`ScanTruncate`); the secret scanner still runs — but it isn't a panacea, don't treat it as the only line of defense.

Full flag table: [Appendix B · `replay.capture_flags`](/en/appendix/b-config-keys#replay-capture-flags). Observability overview: [6.6 Observability](/en/part-6/6-observability).
:::

## 5.7.7 Boundaries

A consolidated answer to "can I extend X?" — these are the things deliberately **not** in the system today.

### Host-side APIs that aren't exposed

The host-side hooks **list / disable / hot-reload API** is intentionally absent from [4.7 The Embedded Harness Contract](/en/part-4/7-host-contract#4-7-8-whats-not-in-the-contract).

- ❌ "Enumerate the hook rules currently in effect" — no public API
- ❌ "Disable a rule at runtime" — no
- ❌ "Hot-reload `hooks.json`" — no
- ✅ Want it anyway? Do it in your own plugin-loading layer (you control the manifest, so you control the hooks)

::: info Why not expose them
"Managing hooks from the platform" only has a meaningful shape inside a specific use case — a SaaS platform may want a tenant-level toggle, an IDE may want a "disable during testing" switch. The runtime can't predict your semantics, and forcing an abstraction would just produce a middle layer no one wants to use. So that freedom stays in *your* plugin layer; the host contract stays out of it.
:::

### Hook types that don't run

`http` / `agent` are listed under `KNOWN_UNSUPPORTED_HOOK_TYPES` — recognised at parse time, not executed at runtime (details in §5.7.3). They're **placeholders for the future**; writing one today only gets you a warning.

### Event/type combinations that are rejected

`Stop` / `PreCompact` reject `prompt` (details in §5.7.3). The principle: parseable doesn't mean runnable, so we reject at parse time to avoid the "looks like it works but doesn't" trap.

### What we *do* promise to keep stable

| Surface | Stability |
|---------|-----------|
| `hooks.json` fields (`type` / `command` / `prompt` / `matcher` / `timeout`) | **Stable**; aligned with Claude Code |
| `SUPPORTED_HOOK_EVENTS` set | **Append-compatible** — new events may be added; existing events won't disappear or be renamed |
| `HookAttachmentRecord.attachment_type` values | **Stable** — additions won't sneak in beyond the documented four |
| `PLUGIN_HOOK_FIRED.data` fields | **Append-compatible** (same as `AgentEvent` overall; for a stable contract use `HostEvent` — but the host surface currently doesn't project `PLUGIN_HOOK_FIRED`, so consume it from `AgentEvent` directly) |

## 5.7.8 Recipes

### 1 · Inject project context every turn (prompt type)

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "type": "prompt",
        "prompt": "Project codename ATLAS. Prefer design docs under docs/atlas/; check the ops-runbook channel before answering deployment questions."
      }
    ]
  }
}
```

Zero process overhead, injected on every turn. Good for "project-identity awareness" — the agent knows what project it's in from the very first message.

### 2 · Block dangerous shell commands (command + matcher)

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "type": "command",
        "command": "/usr/local/bin/shell-guardrail.py",
        "matcher": { "tool_name": "run_shell_command" },
        "timeout": 5
      }
    ]
  }
}
```

`shell-guardrail.py` reads the payload from stdin (full command included), and on a blacklist hit (`rm -rf /`, `curl | sh`, etc.) writes back a `hook_blocking_error`. The chat loop honors `blocking_error` and aborts the tool call; the UI sees `[Blocked by hook] {message}`.

::: tip Keep matcher timeouts low
A `PreToolUse` hook **blocks the tool call**. Set the timeout in single-digit seconds — the hook itself shouldn't become the new bottleneck.
:::

### 3 · Force one more LLM call after an empty `Stop` (Stop + force_continue)

Reasoning models occasionally stop with an empty assistant message. Catch it with a Stop hook:

```json
{
  "hooks": {
    "Stop": [
      {
        "type": "command",
        "command": "/usr/local/bin/empty-answer-rescue.sh",
        "timeout": 3
      }
    ]
  }
}
```

`empty-answer-rescue.sh` checks whether `last_assistant_message` from stdin is empty. If so, it writes `force_continue=true` and `follow_up_message="Please give a final answer based on the existing context."` The chat loop sees the signal and issues another LLM call.

::: warning Pair this with `max_iterations`
`force_continue` consumes one iteration. Unbounded retries are doom-loop fuel — set a sensible `max_iterations`, and have the hook check `at_max_iter` (refuse to force when the cap is already hit). See [4.6 Max Iterations](/en/part-4/6-max-iterations).
:::

### 4 · Audit before compaction (PreCompact + command)

```json
{
  "hooks": {
    "PreCompact": [
      {
        "type": "command",
        "command": "/usr/local/bin/audit-compaction.sh",
        "timeout": 2
      }
    ]
  }
}
```

`audit-compaction.sh` reads `compaction_type` (`microcompact` / `full` / `minimal_history`) and `trigger` from stdin, then logs an audit row (which session, when, what kind).

::: info PreCompact cannot prevent compaction
Even if the hook returns `hook_blocking_error`, `outcome` is always `"allow"` — that's the observe-only contract. If you need "alert on overly frequent compaction", have the hook ship data to an external metrics system and let *that* trigger the alert. **Don't** expect the hook itself to stop compaction.
:::

---

→ Next stop: [Part 6 · Security & Production Deployment](/en/part-6/) — once hooks, permissions, and tools are in place, shipping the whole stack to real users is its own set of problems.
