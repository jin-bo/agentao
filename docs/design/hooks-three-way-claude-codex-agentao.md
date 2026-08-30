# Claude Code hooks · codex hooks · Agentao plugin hooks — a three-way hook-contract comparison

> **⚠️ Analysis only. Nothing here is authorized for implementation.** §1 is a **priority ordering
> of findings**, not a work schedule. Quote this line whenever you quote the table.

**Status:** analysis, **rev 5** (2026-08-26). No implementation authorized *from this document* — the
nine deviations it catalogues were closed by `hooks-claude-contract-conformance-plan.md`, merged
2026-08-30 (PR #199, `18fb628`, unreleased). This doc stays the **evidence**, not the work item: its
§1 ordering and its measured claims are as of `main@10b5fb8` and were not re-derived after the fix.
**Anchors:** Claude Code hooks reference `<https://code.claude.com/docs/en/hooks>`, raw markdown
fetched 2026-08-26 (3532 lines); codex `openai/codex@0d9bb6c34c` (2026-08-24), with OpenAI's own
hooks reference `<https://developers.openai.com/codex/hooks>` fetched 2026-08-26 as a **secondary**
anchor; agentao `main@10b5fb8` (2026-08-24). Every codex claim here is derived from source at the
pinned commit — that reference was read afterwards, and corroborates them: `suppressOutput` is
*"parsed today but not yet implemented"* and explicitly unsupported on `PreToolUse`,
`PermissionRequest`, and `PostToolUse`; `continue:false` is *"parsed for compatibility"* but inert on
`SubagentStart`; `SessionEnd` hooks are *"advisory, so their output won't steer Codex"*; and it
documents 11 events. It is silent in one place that matters: it gives exit 2 to five events and never
to `PermissionRequest`, which the source honors as a deny — see §6, item 5.
**Method:** all three sides read from source. codex and agentao claims carry an inline `file:line`.
Claude Code claims cite a **section name** in the reference, not a line number, because the fetched
file is not a reproducible artifact. **Ten** assertions are *(measured)* through the real
dispatcher and a real subprocess rather than read — each is marked inline, and §9.1 reproduces the
probe that produces all ten.
**Scope:** the **hook contract** in both directions — the JSON a hook receives on stdin (§5.9), and
what it may print on stdout, what exit codes mean, and which fields each side reads (everything
else in §5). Discovery, trust, and observability appear only where they change the contract; the
structural comparison of those axes is §6's closing subsection and §7.
**Twin:** `hooks-three-way-claude-codex-agentao.zh.md`.
**Related:** `codex-compaction-vs-agentao.zh.md` (§4 here refines its `PreCompact` note),
`pi-mono-compaction-vs-agentao.md`, `path-a-roadmap.md`.

### Revision history

Four review rounds. Kept as one line each because three of them **reversed a verdict in a table cell**,
and a cell that changed direction reads as arbitrary without the round that moved it.

| rev | Found | Headline |
|---|---|---|
| 2 | 6 corrections + scope | rev 1 covered only the **output** contract, leaving the doc's own headline under-evidenced; the input contract became §5.9. Also: agentao's `Stop` `hSO.additionalContext` is **⚠️**, not ✅ — it decorates the answer and ends the turn where the reference continues the conversation (§5.8) |
| 3 | 4, all on the codex and reference halves | Two cells flipped **toward codex**: its plain-text `UserPromptSubmit` stdout is ✅, not `Failed` (only `{`/`[`-leading invalid JSON fails), and ignoring `continue:false` on `SessionEnd` is **alignment**, not deviation. §6 became a *selected* set rather than a count |
| 4 | 4, three on §5.9's own scope | The input break is not just an envelope shape — it is **event-specific renames and omissions on 7 of 8 events**. And *(measured)* had been earned by a two-event sample; the probe now runs all eight dispatch paths |
| 5 | 1 quantifier + 2 wording | `PreCompact` was called clean on both layers; it is clean on two of **three**. Final quantification: **envelope 6/8, event-specific fields 7/8, common fields 8/8** — no event's stdin matches end to end |

---

## 0. The framing that makes this comparison fair

The Claude Code reference at the anchor date documents **31 hook events**. codex implements 11,
agentao 8. **That gap is not a defect on either side.** Both were built against an earlier, smaller
Claude Code surface, and events such as `TeammateIdle`, `WorktreeCreate`, and `Elicitation` are
plainly later additions tied to product features neither peer has.

What *is* comparable, and what this document is about: for the events all three do implement, does
a hook script written against the documented contract behave the same way? That question has a
falsifiable answer, and for agentao it is **no** in nine places.

A second framing point, because it is easy to read this document as "agentao is behind": codex
deviates from the same reference in at least nine places of its own — §6 is a **selected** list, not
an exhaustive count — and on two axes agentao is the **more** conformant of the two peers (§7). Deviation from this reference is the norm, not the
exception. The findings in §5 are ranked by *consequence*, not by the mere fact of divergence.

---

## 1. Findings table (priority ordering, not a schedule)

**One-sentence difference: Claude Code defines the contract and spreads widest; codex implements a
narrow slice of it but makes trust and observability first-class; agentao implements the narrowest
slice with the simplest machinery, and pays for it in one place where the hook never sees the input
it expects and three more where the same wire field means something different than it says on the
tin.**

| If implemented, priority | Finding | Section |
|---|---|---|
| **P1** | **The stdin contract diverges on all 8 events** *(measured, all 8 dispatch paths)*. Three layers: the **envelope** is not Claude's on 6 of 8 — only `Stop`/`PreCompact` are flat snake_case, the rest wrap everything in `{"event", "data"}` with camelCase inner keys (`agentao/plugins/models.py:230`), so the reference's own `jq -r '.tool_input.command'` example returns `null`; **event-specific fields** are renamed or missing on 7 of 8 — `prompt`→`userMessage`, `source` and `reason` absent, `tool_response`→`toolOutput` with an object→string type change, `tool_use_id` nowhere; and **common fields or their values** are wrong on 8 of 8, `PreCompact` included — hardcoded `transcript_path: None`, and a `permission_mode` the reference does not put on that event at all. | §5.9 |
| **P1** | **`UserPromptSubmit` ignores all four documented output channels** *(measured)*. `decision:"block"` + `reason`, `hookSpecificOutput.additionalContext`, `continue:false`, and exit 2 are each silently dropped; only agentao's own `blockingError` / `preventContinuation` / top-level `additionalContext` work. This directly contradicts the module's own stated goal — "a hook script written against Claude Code can run under Agentao without modification" (`agentao/plugins/hooks/_alias.py:5`). codex implements all four. | §5.1 |
| **P1** | **`systemMessage` is routed to the wrong channel and dropped on 3 of 4 sites.** The reference defines it as a *warning shown to the user*. agentao appends it to `additional_contexts` (`_output_parsing.py:183`), which on the `final_response` Stop site is echoed into the assistant answer **and persisted to `agent.messages`** (`_runner.py:1051-1053`) — so the model reads it next turn — and on the other three Stop sites is discarded entirely (`_runner.py:222,228,236`). On `UserPromptSubmit` the field is never read at all. | §5.2 |
| **P2** | **`Stop` `hookSpecificOutput.additionalContext` does not continue the conversation** *(measured)*. The reference's non-error feedback channel keeps the turn going under the same loop protections as `decision:"block"`. agentao parses it (`_output_parsing.py:185-187`) but never sets `force_continue`, so the text decorates the final answer and the turn ends — the model is handed feedback it is given no opportunity to act on. | §5.8 |
| **P2** | **No bound on hook output.** The reference caps hook strings at 10,000 characters and spills the remainder to a file with a recovery path; codex caps `additionalContext` at ~2,500 tokens and spills (`hooks/src/output_spill.rs:12`). agentao has no cap anywhere: a `UserPromptSubmit` hook that prints a large file injects all of it into the prompt. | §5.3 |
| **P2** | **`PreToolUse` `additionalContext` is parsed and then only logged** (`runtime/tool_runner.py:308`). The reference injects it next to the tool result; codex injects it. | §5.4 |
| **P2** | **Top-level `continue:false` is honored on `Stop` only** (`_output_parsing.py:161`). The reference makes it universal-but-discardable and names the discard cases per event (`PreCompact`, `PostCompact`, `SessionEnd`, and others); codex acts on it for **7 of 11** events and explicitly *rejects* it on two. All three differ here — see §5.5. | §5.5 |
| **P3** | **Exit code 2 is honored on `Stop` only** *(measured)* (`_dispatcher.py:562`). The reference honors it on 14 events including `PreToolUse`, `UserPromptSubmit`, and `PreCompact`; codex honors it on **6**. agentao's `PreToolUse` and `PreCompact` paths document the omission as deliberate MVP scope (`_dispatcher.py:414,208`). | §5.6 |
| **P3** | **No `${CLAUDE_PLUGIN_ROOT}`.** The reference specifies it as both a path placeholder and an exported environment variable; codex sets `PLUGIN_ROOT` *and* `CLAUDE_PLUGIN_ROOT` explicitly for Claude-plugin compatibility (`hooks/src/engine/discovery.rs:264`). agentao sets neither — zero matches repo-wide. | §5.7 |
| *note* | **`suppressOutput` is implemented, and the reference says it does nothing.** Not harmless: setting it suppresses the `<stop-hook>` echo, so a hook that sets a field it believes inert loses its `systemMessage`/`additionalContext` display. Kept as a note because that is §5.2/§5.8's breakage reached through a second field. | §5.10 |

---

## 2. Scale, at the anchor date

| | Claude Code (reference) | codex | agentao |
|---|---|---|---|
| Events | **31** | 11 (`hooks/src/lib.rs:23`) | 8 (`agentao/plugins/models.py:197`) |
| Input envelope | flat snake_case, every event | flat snake_case, every event | **split** *(measured, 8/8)*: flat on `Stop`/`PreCompact`, `{event,data}` camelCase on the other six (`models.py:230`) |
| Handler types | 5: `command` / `http` / `mcp_tool` / `prompt` / `agent` | 2 runnable: `command` / `mcp_tool`; `prompt` and `agent` parse then load-fail (`discovery.rs:629,639`) | 2: `command` / `prompt`; `http` and `agent` rejected at parse (`models.py:233`) |
| Matcher type | string, three-way evaluation | string, three-way evaluation (`events/common.rs:137`) | **dict**, two keys ever read (`_dispatcher.py:313,323`) |
| Execution | all matching hooks in parallel | parallel (`engine/dispatcher.rs:122`) + `async` background pool of 8 (`command_runner.rs:45`) | serial, short-circuit |
| Cross-source dedup | same handler in several settings files runs once | none (a test pins duplicates as kept, `dispatcher.rs::select_handlers_keeps_duplicate_stop_handlers`) | none |
| Output bound | 10,000 chars → spill to file | ~2,500 tokens → spill to file (`output_spill.rs:12`) | **none** |
| Default timeout | 600 s; 30 s on `UserPromptSubmit`; `SessionEnd` shares 1.5 s | 600 s (`discovery.rs:728`); `SessionEnd` default 1 s, clamped to 3 s (`events/session_end.rs:20,23`) | 60 s everywhere (`_parser.py:141`) |
| Config sources | 4 settings tiers + plugin + skill/subagent frontmatter | full config-layer stack + `hooks.json` + `config.toml [hooks]` | **plugins only** (`embedding/plugins/manager.py:66-67`) |
| Safety gate | workspace trust, `disableAllHooks`, `allowManagedHooksOnly` | per-hook trust hash, managed-only mode (`discovery.rs:695-697,771`) | **none** |

The three "events" numbers are not a quality ranking — see §0.

---

## 3. The output-contract matrix

`hSO` = `hookSpecificOutput`. ✅ implemented as documented · ⚠️ implemented differently · ❌ absent
or inert. The **input** contract is §5.9, not this table.

| Contract point | Claude Code | codex | agentao |
|---|---|---|---|
| `PreToolUse` `hSO.permissionDecision:"deny"` | ✅ | ✅ (empty reason → `Failed`) | ✅ (empty reason accepted) |
| … `"ask"` | ✅ | ❌ rejected: *"unsupported permissionDecision:ask"* (`output_parser.rs:446`) | ✅ |
| … `"allow"` on its own | ✅ | ❌ invalid unless paired with `updatedInput` (`output_parser.rs:442`) | ✅ (no-op) |
| … `"defer"` | ✅ (`-p` only) | ❌ | ❌ |
| … `updatedInput` | ✅ | ✅ (only with `allow`) | ❌ |
| … `hSO.additionalContext` | ✅ injected next to tool result | ✅ injected | ⚠️ parsed, logged only (`tool_runner.py:308`) |
| … multi-hook precedence | `deny > defer > ask > allow` | deny / allow only | `deny > ask` |
| … exit 2 blocks | ✅ | ✅ (`events/pre_tool_use.rs:261`) | ❌ (`_dispatcher.py:414`) |
| … deprecated top-level `decision` | mapped, deprecated | ✅ legacy path retained | ❌ |
| `UserPromptSubmit` top-level `decision:"block"` + `reason` | ✅ | ✅ | ❌ *(measured)* |
| … `hSO.additionalContext` | ✅ | ✅ | ❌ *(measured)*; only top-level `additionalContext` is read (`_output_parsing.py:90`) |
| … exit 2 blocks and erases the prompt | ✅ | ✅ (`events/user_prompt_submit.rs:227`) | ❌ *(measured)* |
| … plain-text stdout becomes context | ✅ | ✅ (`events/user_prompt_submit.rs:217-222`) — `{`/`[`-leading **invalid** JSON is `Failed` (`:211`), which **matches** the reference as of Claude Code v2.1.248 | ⚠️ falls back to plain text — **the ✅ this row carried is now the divergence** |
| `Stop` top-level `decision:"block"` + `reason` | ✅ `reason` required | ✅ empty `reason` → `Failed` | ⚠️ empty **string** accepted → continues with a default message *(measured)*; only a **missing** or non-string `reason` is ignored (`_output_parsing.py:165`, `_runner.py:1002-1005`) |
| … exit 2 | ✅ | ✅ (`events/stop.rs:297`) | ✅ (`_dispatcher.py:562`) |
| … `hSO.additionalContext` as non-error feedback | ✅ conversation continues | ❌ field absent from the Stop wire type | ⚠️ parsed, but the turn **ends** *(measured)* — §5.8 |
| … consecutive-block cap | **8**, host-enforced | **none** — `stop_hook_active` is passed but nothing counts (`core/src/session/turn.rs:524`) | **3** (`_runner.py:157`) |
| Top-level `continue:false` | universal, but discarded on some events (`PreCompact`, `PostCompact`, `SessionEnd` named, among others) | ⚠️ acts on **7 of 11**; explicitly rejects on `PreToolUse` (`output_parser.rs:358`) and `PermissionRequest` (`:370`); ignores on `SubagentStart` (`events/session_start.rs:272`) and `SessionEnd` | ⚠️ `Stop` only (`_output_parsing.py:161`) |
| `suppressOutput` | accepted, **no effect** | ⚠️ discarded on most events (`let _ =`), but **rejected as unsupported** on `PreToolUse` / `PermissionRequest` / `PostToolUse` (`output_parser.rs:362,374,382`) | ⚠️ implemented |
| `systemMessage` | warning shown to the **user** | warning entry, user-facing | ⚠️ merged into the model context channel (§5.2) |
| `terminalSequence` | ✅ | ❌ | ❌ |
| `PermissionRequest` `hSO.decision.behavior` | ✅ allow/deny | ✅ | ❌ event absent |
| … exit 2 | **not honored**, flow proceeds | ⚠️ honored as deny (`events/permission_request.rs:249`) | n/a |
| `PostToolUse` exit 2 | not a block; stderr shown to Claude | ⚠️ blocks (`events/post_tool_use.rs:259`) | ❌ side-effect only |
| Hook output bound | 10,000 chars | ~2,500 tokens | none |

> **Correction, 2026-08-28.** The `… plain-text stdout becomes context` row above read "where the reference falls back to plain text". Re-fetching the reference on 2026-08-28 shows that behavior is version-gated: *"when Claude Code tries to parse your stdout as JSON and can't, it reports a non-blocking error on every exit code other than 2 … On the events that add plain-text stdout as context, Claude Code doesn't add the text. **Before v2.1.248**, Claude Code treated that stdout as plain text."* So codex is conformant here and agentao is not — the direction of that comparison is reversed, and `hooks-claude-contract-conformance-plan.md` §4.2 carries the corrected five-state machine. The `{`-leading rule is also stricter than this doc assumed: JSON is attempted only when the trimmed output starts with `{` **and** ends with `}`, and a `[`-leading array is plain text unconditionally. No other row in this table was re-verified against the new fetch.

---

## 4. `PreCompact` cancellation: three spellings, none conformant

The reference is explicit on both halves: **exit 2 blocks compaction**, or return top-level
`"decision": "block"` — and *"Claude Code discards a PreCompact hook's `systemMessage` and
`continue` fields"* (§"PreCompact", §"Exit code 2 behavior per event", §"Decision control").

- **codex** cancels on `continue:false` (`hooks/src/events/compact.rs:287`) — precisely the field
  the reference says is discarded for this event — and treats any non-zero exit as `Failed` rather
  than a block (`compact.rs:313`). It also surfaces `systemMessage` as a warning entry here
  (`compact.rs:279`), the *other* half of the same discarded pair.
- **agentao** cancels on an invented key, `hookSpecificOutput.compactionDecision:"cancel"`
  (`_dispatcher.py:229`), and does not read `continue` at all *(measured — §9.1)*.
- **Neither matches, and they do not match each other.**

There is an exact mirror-image here, and it is easy to see only one half of: codex acts on
`continue:false` for the two events where the reference says it is **discarded** (`PreCompact`,
`PostCompact`), and rejects it as unsupported on two events where the reference accepts it
(`PreToolUse`, `PermissionRequest`). The field is not "universally honored" anywhere in codex; it is
honored in a set that only partially overlaps the reference's.

Two consequences worth stating plainly. First, this is **not** an agentao-only divergence, so it
should not be filed as one. Second, and against the instinct to converge: there is no de-facto
standard here to converge *on*. agentao's spelling is at least self-consistent and documented
(`CLAUDE.md`, "The control plane has two layers and one merge rule"), which is more than can be said
for building on a field the reference documents as discarded.

This **refines** the `codex-compaction-vs-agentao.zh.md` note that official docs confirm
`PreCompact | Yes | Blocks compaction`. That remains true and is re-verified at this anchor; what
is new is that the *JSON* path is top-level `decision:"block"`, and that codex's own implementation
does not use it either.

---

## 5. agentao's deviations

### 5.1 `UserPromptSubmit` ignores all four documented output channels *(measured)* — P1

`_run_command_hook` → `_parse_command_output` (`_output_parsing.py:26`) reads exactly three keys:
`blockingError` (`:65`), `preventContinuation` (`:77`), and a **top-level** `additionalContext`
(`:90`). It never reads `decision`, never reads `hookSpecificOutput`, never reads `continue`, and
`_run_command_hook` demotes a non-zero exit with empty stdout to a benign warning, so exit 2 is
inert here too.

The probe in §9.1 confirms each case end-to-end through the real dispatcher and a real subprocess,
including a hook that genuinely exits 2:

```
claude documented block          block=None     prevent=False ctx=[]
claude documented ctx            block=None     prevent=False ctx=[]
claude continue:false            block=None     prevent=False ctx=[]
claude exit 2 (stderr)           block=None     prevent=False ctx=[]
agentao-only blockingError       block='nope'   prevent=False ctx=[]
agentao-only additionalContext   block=None     prevent=False ctx=['FROM_AGENTAO_SHAPE']
```

Why this ranks P1: it is one of two findings that **falsify a claim the code makes about itself**.
`_alias.py:5` states the whole point of the Claude tool-name alias table is that "a hook script
written against Claude Code can run under Agentao without modification". For the single event where
a hook is most likely to be copied verbatim from a Claude Code setup, that is untrue, and it fails
**silently** — the hook exits 0 (or 2), the dispatcher records a generic success attachment, and
nothing surfaces to the user. §5.9 is the other, and it is broader.

### 5.2 `systemMessage` goes to the model channel, and vanishes on 3 of 4 Stop sites — P1

The reference: *"`systemMessage` — Warning message shown to the user."* Not to Claude.

agentao reads it only in the Stop parser, and appends it to `additional_contexts`
(`_output_parsing.py:180-183`). Downstream that list is not a user-warning channel:

- On the `final_response` Stop site it is wrapped in `<stop-hook>` blocks, appended to the
  assistant's answer, and **written into `agent.messages`** (`_runner.py:1042-1053`). The user does
  see it — but so does the model, on every subsequent turn.
- On `max_iterations`, `doom_loop`, and `length_truncation` the site config sets
  `echo_additional_contexts: False` (`_runner.py:222,228,236`), so the warning is **dropped
  entirely** — nobody sees it.
- On `UserPromptSubmit` the field is never parsed (§5.1), so it is dropped there too.

The consequence is not cosmetic. A hook author's "warning to the human" becomes durable model input
on one path and silence on four. Text authored as an out-of-band operator warning is exactly the
shape the reference elsewhere warns against feeding to a model ("Text framed as out-of-band system
commands can trigger Claude's prompt-injection defenses").

### 5.3 No bound on hook output — P2

The reference caps hook output strings — `additionalContext`, `systemMessage`, and plain stdout —
at 10,000 characters, writes the full text to a file, and passes a preview plus the path. codex
does the same on a token budget, per-handler configurable, `0` disabling it
(`hooks/src/output_spill.rs:12`, `AdditionalContextLimit::from_config`).

agentao applies no limit at any hook site. `_parse_command_output` appends `stdout` to
`additional_contexts` whole (`_output_parsing.py:49`), and `_dispatch_user_prompt_submit` prepends
every entry to the user message (`_hook_dispatch.py:86-91`). A hook that `cat`s a large file puts
the whole thing in the prompt, once per turn.

### 5.4 `PreToolUse` `additionalContext` is parsed, then only logged — P2

`_apply_pre_tool_use_hooks` harvests `additionalContext` into `hook_result.additional_contexts`,
then writes a log line and discards it: *"MVP: recorded, not injected into the model or tool path"*
(`runtime/tool_runner.py:308`). The reference injects the string next to the tool result; codex
injects it. The parsing, the event counter, and the log line all already exist — what is missing is
the sink.

### 5.5 Top-level `continue:false` is `Stop`-only — P2

`continue_false = data.get("continue") is False` appears once in the codebase, in the Stop parser
(`_output_parsing.py:161`).

All three sides differ, and the codex column is the one most often stated wrong, so state it precisely:

| | Acts on `continue:false` | Rejects it | Ignores it |
|---|---|---|---|
| Reference | universal, *"takes precedence over any event-specific decision fields"* | — | named per event as discarded: `PreCompact`, `PostCompact`, `SessionEnd`, and ~10 others (§"PreCompact", §"PostCompact", §"SessionEnd") |
| codex | **7 of 11**: `PreCompact` + `PostCompact` (`events/compact.rs:287`), `PostToolUse` (`events/post_tool_use.rs:212`), `SessionStart` (`events/session_start.rs:272`), `Stop` + `SubagentStop` (`events/stop.rs:250`), `UserPromptSubmit` (`events/user_prompt_submit.rs:183`) | `PreToolUse` (`output_parser.rs:358`), `PermissionRequest` (`:370`) | `SubagentStart`, `SessionEnd` |
| agentao | **1 of 8**: `Stop` | — | the other seven |

Note the shape: codex honors the field on exactly the two events the reference names as discarding
it, and rejects it on two the reference accepts. "codex honors it universally" is false in both
directions.

Of codex's two *ignore* events, only one is a divergence. The reference gives `SessionEnd` **no
decision control at all** and says it "discards their JSON output fields" (§"SessionEnd",
§"Decision control"), so codex ignoring the field there is **conformant** and not a deviation.
`SubagentStart`, whose section documents context injection and never names
`continue` as discarded, is the real one.

### 5.6 Exit code 2 is `Stop`-only *(measured)* — P3

`_run_stop_command_hook` checks `proc.returncode == 2` before parsing JSON, deliberately, so
`continue:false` in stdout cannot countermand it (`_dispatcher.py:562`). No other agentao dispatcher
checks it; `_run_pre_tool_use_command` and `_run_pre_compact_command` both carry a comment saying
so (`_dispatcher.py:414`, `:208`), and §9.1 measures the `UserPromptSubmit` case.

codex honors exit 2 on **6** events, not the 5 parser files it appears in: `Stop` and `SubagentStop`
share one parser whose event arm covers both (`events/stop.rs:216`, exit-2 arm at `:297`), alongside
`PreToolUse`, `UserPromptSubmit`, `PostToolUse`, and `PermissionRequest`.

Ranked P3 rather than P2 because the JSON path is the reference's *preferred* channel ("exit 0 and
print JSON for structured control"), so a hook author has a working alternative on every affected
event. It is still a portability gap: exit 2 is the shorter idiom and appears in most published
examples.

### 5.7 No `${CLAUDE_PLUGIN_ROOT}` — P3

The reference specifies three path placeholders — `${CLAUDE_PROJECT_DIR}`, `${CLAUDE_PLUGIN_ROOT}`,
`${CLAUDE_PLUGIN_DATA}` — substituted into `command`/`args` **and** exported into the hook process.
codex sets `PLUGIN_ROOT` and `PLUGIN_DATA` plus the `CLAUDE_`-prefixed aliases, with the reason in a
comment: *"For OOTB compat with existing plugins that use this env var"*
(`hooks/src/engine/discovery.rs:264`).

agentao sets none: `PluginHookDispatcher._run_subprocess` passes no `env=` to `run_captured`
(`_dispatcher.py:331`), and `grep -r 'CLAUDE_PLUGIN_ROOT\|PLUGIN_ROOT' agentao/` returns zero
matches. Since `${CLAUDE_PLUGIN_ROOT}/scripts/x.sh` is the standard way a Claude Code plugin
references its own bundled script, such hooks fail with a file-not-found under agentao.

### 5.8 `Stop` `hookSpecificOutput.additionalContext` does not continue the conversation *(measured)* — P2

The reference gives `Stop` two feedback channels, and the difference between them is the whole
point: `decision:"block"` is an *error* channel, while `hookSpecificOutput.additionalContext` is
*"Non-error feedback for Claude. The conversation continues so Claude can act on it"* — under the
same loop protections (`stop_hook_active`, the 8-continuation cap), just labelled as feedback
rather than a hook error.

agentao parses the field (`_output_parsing.py:185-187`) but never sets `force_continue` from it —
that flag has exactly three sources, all of them error-shaped: `decision:"block"`
(`_output_parsing.py:169`), `preventContinuation` (`:220`), and exit 2 (`_dispatcher.py:564`). The
measured result:

```
hSO.additionalContext alone      force_continue=False follow_up=None ctx=['run the tests first']
```

So the text is appended to the answer and the turn ends. The model is handed guidance it is given
no opportunity to act on — which is precisely the case the reference's non-error channel exists to
serve ("run the test suite before finishing"). Marking this ✅ on the strength of the parse alone is
the easy error: the parse is not the contract.

A related and separate correction, same parser: an **empty-string** `reason` on `decision:"block"`
is *not* silently ignored. `isinstance(reason, str)` accepts `""` (`_output_parsing.py:165`), so
`force_continue` is set and the runner substitutes a default continuation message
(`_runner.py:1002-1005`). Only a **missing** or non-string `reason` is ignored. Measured:

```
decision=block, reason=""        force_continue=True follow_up='' ctx=[]
decision=block, reason missing   force_continue=False follow_up=None ctx=[]
```

This makes agentao *more* permissive than both peers, where the reference calls `reason` required
and codex fails the hook.

### 5.9 The stdin contract: envelope wrong on 6 of 8, event-specific fields on 7 of 8, common fields on 8 of 8 *(measured)* — P1

This is the largest single portability break, and it is outside the output contract: an analysis scoped to
the output contract, which left its own headline claim — "a hook script runs unmodified" — resting
on half the evidence.

agentao emits **two different input shapes** (`_payload.py:7`). `Stop` and `PreCompact` use the
reference's flat snake_case top level; the other six events wrap everything in an `{"event",
"data"}` envelope with camelCase inner keys. The split is a named constant, `CLAUDE_FLAT_EVENTS`
(`agentao/plugins/models.py:230`), and the dispatcher's `_matches` reads both shapes accordingly
(`_dispatcher.py:313,323`). Measured by driving **all eight** dispatch paths with a `cat >` hook and
reading back what the hook process received — not what the adapter returned. Sampling two events and
generalizing to eight from source is what this replaces (§9.2 rule 4); this is the sweep:

```
UserPromptSubmit    envelope  keys=['cwd', 'sessionId', 'userMessage']
SessionStart        envelope  keys=['cwd', 'sessionId']
SessionEnd          envelope  keys=['cwd', 'sessionId']
PreToolUse          envelope  keys=['sessionId', 'toolInput', 'toolName']
PostToolUse         envelope  keys=['sessionId', 'toolInput', 'toolName', 'toolOutput']
PostToolUseFailure  envelope  keys=['error', 'sessionId', 'toolInput', 'toolName']
Stop                flat      keys=['cwd', 'last_assistant_message', 'permission_mode', 'session_id', 'stop_hook_active', 'transcript_path', 'turn_end_reason']
PreCompact          flat      keys=['compaction_type', 'custom_instructions', 'cwd', 'permission_mode', 'reason', 'session_id', 'transcript_path', 'trigger']
```

The reference's `PreToolUse` input is flat, and its fields are the **common** ones — `session_id`,
`transcript_path`, `cwd`, `hook_event_name`, plus `permission_mode`, `prompt_id`, and `effort` where
the event's own section shows them — with three event-specific: `tool_name`, `tool_input`,
`tool_use_id` (§"Common input fields", §"PreToolUse"). Its own worked example — the `rm`-blocking
script in §"Exit code 2" — reads `jq -r '.tool_input.command'`. Under agentao that expression
returns `null`; the value is at `.data.toolInput.command`.

**The envelope is only the first layer.** Event by event, against each event's own reference section
(the `data` / flat keys are the measured ones above):

| Event | Reference's event-specific input | agentao | Gap |
|---|---|---|---|
| `UserPromptSubmit` | `prompt` | `userMessage` | **renamed** |
| `SessionStart` | `source` (required); `model`, `agent_type`, `session_title` optional | — | `source` **missing**; `model` not supplied |
| `SessionEnd` | `reason` | — | `reason` **missing** |
| `PreToolUse` | `tool_name`, `tool_input`, `tool_use_id` | `toolName`, `toolInput` | renamed; `tool_use_id` missing |
| `PostToolUse` | `tool_name`, `tool_input`, `tool_response` (**object**), `tool_use_id`, `duration_ms` | `toolName`, `toolInput`, `toolOutput` (**string**) | `tool_response` → `toolOutput` is a rename **and** a type change; `tool_use_id`, `duration_ms` missing |
| `PostToolUseFailure` | `tool_name`, `tool_input`, `tool_use_id`, `error`, `is_interrupt`, `duration_ms` | `toolName`, `toolInput`, `error` | `tool_use_id`, `is_interrupt`, `duration_ms` missing |
| `Stop` | `stop_hook_active`, `last_assistant_message`, `background_tasks`, `session_crons` | first two present | `background_tasks`, `session_crons` missing; extra `turn_end_reason` |
| `PreCompact` | `trigger`, `custom_instructions` | both present | **none** (plus agentao's own `compaction_type`, `reason`) |

`PreCompact` is the only event clean at *this* layer — read the table strictly, since its scope is
the event-specific fields. **At the common-field layer, no event is clean, `PreCompact` included**;
that is the third note below. `toolOutput` is the one worth singling out: it
is not `tool_response` in camelCase but a different field — the reference passes the tool's
structured `Output` object (`{filePath, success}` for a write), agentao passes a string
(`_payload.py:100`).

Three second-order notes:

- The **tool-name alias is applied correctly** (`run_shell_command` → `Bash`), so the matcher and
  the payload agree. The alias table is doing its job; the envelope around it is not.
- **Common fields, stated precisely** — this is the easiest place to overreach. `hook_event_name` is absent
  from all six envelope events (the outer `event` key carries the name instead) and
  `transcript_path` from all six as well; `cwd` is missing on the three tool events. Of the
  conditional common fields, `permission_mode` is owed on `UserPromptSubmit`, `PreToolUse`,
  `PostToolUse`, and `PostToolUseFailure` — the reference's `SessionStart` and `SessionEnd` examples
  carry none, so its absence there is not a gap — and `effort` (tool-use context, model-dependent)
  and `prompt_id` (absent until the first user input, version-gated) are conditional rather than
  guaranteed. A flat "`permission_mode` on every event except `Stop`/`PreCompact`" overstates that,
  and "`model` is not owed to anything agentao implements" is simply wrong: agentao implements
  `SessionStart`, where `model` is an optional field it does not supply. Only
  `turn_id` is genuinely inapplicable — the reference gives it to `MessageDisplay`, which agentao
  does not implement.
- **`Stop` and `PreCompact` match the envelope layout, not the payload.** `transcript_path` is
  hardcoded `None` (`_payload.py:142,173`) where the reference documents a path — mitigated for the
  common case, since the reference itself steers `Stop` hooks to `last_assistant_message`, which
  agentao does supply, but a hook that reads the transcript gets `null`; `Stop` omits
  `background_tasks` and `session_crons` (§"Stop"); and `permission_mode` defaults to
  `"workspace-write"` (`_payload.py:144,175`), which is agentao's own mode vocabulary and not one of
  the reference's values (`default` / `plan` / `acceptEdits` / `auto` / `dontAsk` /
  `bypassPermissions`) — so a hook branching on the documented enum matches no arm, and on
  `PreCompact` the reference's input carries no `permission_mode` at all, making it an extra key
  rather than a wrong one. This is why the three-layer count ends at **8/8**: `PreCompact` clears the
  envelope and its own event fields, and still ships a `None` transcript path and an undefined key.

The dual shape is deliberate and documented — `_payload.py:7` calls the mismatch "intentional and
load-bearing for cross-tool portability", and it is, for the two flat events. The finding is that it
was applied to two events out of eight, and that even there the alignment is of the envelope only.

### 5.10 `suppressOutput` is implemented, and the reference says it is inert — note

The reference: *"Has no effect: Claude Code accepts the field but doesn't act on it."* codex drops
it explicitly on most events (`let _ = parsed.universal.suppress_output;`) but **rejects** it on
three — `PreToolUse`, `PermissionRequest`, `PostToolUse` all fail the hook with "returned unsupported
suppressOutput" (`output_parser.rs:362,374,382`), so a Claude-authored hook that sets a field the
reference calls inert is failed outright there. agentao sets
`result.suppress_output` (`_output_parsing.py:178`) and gates the `<stop-hook>` echo on it
(`_runner.py:1045`), with an inline comment already naming it an "Agentao extension to the Claude
semantic".

Filed as a note rather than a separate finding — **not** because it is harmless, which is the
tempting reading. A Claude-authored `Stop` hook that sets `suppressOutput: true` believing the
field inert loses its `systemMessage` and `additionalContext` display entirely, because the same flag
gates the `<stop-hook>` echo (`_runner.py:1042-1053`); on codex such a hook is failed outright on
three events (§6, item 8). It stays a note because what it costs on agentao is the display of exactly
the two channels §5.2 and §5.8 already file as findings — the same breakage reached through a second
field, not an additional one. Recorded so it is neither re-discovered as a fresh defect nor read as
harmless.

---

## 6. codex's deviations, for calibration

Listed so §5 is not read as a one-sided scorecard. **None of these is a proposal to change
agentao.** This is a **selected** set, not an exhaustive audit of codex against the reference — it
was assembled from the events and fields §5 already touches, so treat "nine" as a floor.

1. **`permissionDecision: "ask"` is rejected outright** (`output_parser.rs:446`), and a bare
   `"allow"` is invalid unless paired with `updatedInput` (`:442`). The reference documents four
   values with the precedence `deny > defer > ask > allow`. agentao supports `ask`.
2. **`continue:false` is rejected as unsupported on `PreToolUse`** (`output_parser.rs:358`) **and
   `PermissionRequest`** (`:370`) — as is `stopReason` on both (`:360`, `:372`) — and ignored on
   `SubagentStart` (`events/session_start.rs:272`), where the reference treats the field as
   universal. See the table in §5.5. (codex also ignores it on `SessionEnd`; that one is
   *conformant*, so it does not belong on this list.)
3. **No host-side cap on consecutive Stop blocks.** `stop_hook_active` is set and passed to the
   hook (`core/src/session/turn.rs:524`), but nothing counts blocks; the reference ends the turn
   after 8, agentao after 3.
4. **`PreCompact`/`PostCompact` act on `continue:false`**, the field the reference says is discarded
   for exactly those two events (§4).
5. **Exit 2 on `PermissionRequest` is honored as a deny** (`events/permission_request.rs:249`)
   where the reference says it is not honored at all; **exit 2 on `PostToolUse` blocks**
   (`events/post_tool_use.rs:259`) where the reference says the event cannot block.
6. **`Stop` has no `hookSpecificOutput.additionalContext`** — the reference's non-error feedback
   channel. agentao parses it, though it does not continue the turn (§5.8).
7. **No cross-source dedup**, where the reference runs an identical handler defined in several
   settings files once. codex pins the opposite in a test.
8. **`suppressOutput` is rejected as unsupported** on `PreToolUse`, `PermissionRequest`, and
   `PostToolUse` (`output_parser.rs:362,374,382`), where the reference accepts it as inert — so a
   hook setting a documented no-op field fails on those three events (§5.10).
9. **`systemMessage` is surfaced on `PreCompact`/`PostCompact`** (`compact.rs:279`), the second half
   of the pair the reference says is discarded for exactly those events (§4).

### Beyond the wire contract: where codex is structurally ahead

- **A per-hook trust gate.** Every non-managed handler is hashed over a normalized identity and
  runs only when the stored hash matches (`discovery.rs:695-697,771`), with a startup review UI and
  a `--bypass-hook-trust` escape hatch. The reference gates on *workspace* trust, which is coarser:
  it decides whether a folder's hooks may run at all, not whether *this* hook has changed since you
  approved it.
- **Observability.** A per-handler `HookRunSummary` state machine — `Running` / `Completed` /
  `Failed` / `Blocked` / `Stopped`, typed output entries, duration, source, scope — streamed live,
  plus a `preview_*` pass that renders pending rows before execution. agentao emits one
  `PLUGIN_HOOK_FIRED` event carrying a verdict and counts, and its own docstring says the hook
  output is "neither known nor stored at this layer" (`_hook_dispatch.py:52-53`).

---

## 7. Where agentao leads

1. **Provider credentials are scrubbed from the hook child.** `_run_subprocess` routes through
   `run_captured` (`_dispatcher.py:349`), which defaults `env=` to `build_child_env()` and strips
   `HARNESS_ENV_KEYS`. codex clears the environment and replays a session snapshot but scrubs only
   five launch-context variables (`protocol/src/shell_environment.rs:14-20`) — a hook there inherits
   the provider API key. The reference removes `OTEL_*` and says nothing about provider credentials.
2. **A host-side Stop reentry cap** (`_runner.py:157`), matching the reference's design intent
   (8 there, 3 here) where codex has none.
3. **`PreToolUse` supports `ask`**, which codex rejects.
4. **`PostToolUseFailure` exists.** codex has no such event; a failed tool call is invisible to
   codex hooks. The reference has it. (This is also why the union of implemented events is 12, not
   11 — see §9.3.)
5. **`prompt` handlers actually run** (`UserPromptSubmit` only, template expansion rather than a
   model call). codex declares the type and refuses to load it (`discovery.rs:629`).

Items 1 and 4 are the ones worth keeping in mind when this comparison is cited: they are places
where agentao made a deliberate choice the peers did not.

---

## 8. Parity — do not re-flag

Verified equivalent at this anchor, across all three where the event exists:

- `Stop` blocking via exit 2 with stderr as the continuation reason.
- `Stop` `decision:"block"` + a non-empty `reason` → continue the turn.
- `stop_hook_active` present in the `Stop` payload and set on re-entry.
- `PreToolUse` `deny` wins over `ask` in multi-hook aggregation.
- `PreCompact` matcher on `manual` / `auto`.
- The `Stop` and `PreCompact` **input envelope layout**: flat snake_case with `hook_event_name` at
  the top level, matching the reference (§5.9 is about the other six events). Parity is the
  *layout*, not the field set — §5.9's third bullet lists the field-level gaps that remain on these
  two events, which is why neither appears in this section on any stronger claim than layout.
- Per-rule `timeout` with the hook killed on expiry (all three kill the process **tree**, not just
  the direct child).
- JSON on stdout is the primary channel; a hook that prints nothing is a no-op.

---

## 9. Method

### 9.1 The probe

Ten of this document's assertions are measured rather than read — nine behavioural, plus §5.9's
envelope-and-field sweep, which the probe drives across **all eight** dispatch paths rather than a
representative pair. The probe constructs real `ParsedHookRule` objects, runs them through the real
`PluginHookDispatcher` against a real subprocess, and prints the resulting result object. It is
reproduced here so the measurement can be re-run against a later `main`:

```python
import json, tempfile, pathlib
from agentao.plugins.hooks import PluginHookDispatcher, ClaudeHookPayloadAdapter
from agentao.plugins.models import ParsedHookRule

d, A = PluginHookDispatcher(), ClaudeHookPayloadAdapter()
def rule(ev, out=None, sh=None):
    cmd = sh if sh else f"printf %s {json.dumps(json.dumps(out))}"
    return ParsedHookRule(event=ev, hook_type="command", command=cmd, timeout=10)

# §5.1 — four documented UserPromptSubmit channels, plus agentao's own two
p = A.build_user_prompt_submit(user_message="hi")
for name, out, sh in [
    ("claude documented block", {"decision": "block", "reason": "nope"}, None),
    ("claude documented ctx", {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                     "additionalContext": "FROM_CLAUDE_SHAPE"}}, None),
    ("claude continue:false", {"continue": False, "stopReason": "halt"}, None),
    ("claude exit 2 (stderr)", None, "echo blocked >&2; exit 2"),
    ("agentao-only blockingError", {"blockingError": "nope"}, None),
    ("agentao-only additionalContext", {"additionalContext": "FROM_AGENTAO_SHAPE"}, None),
]:
    r = d.dispatch_user_prompt_submit(payload=p, rules=[rule("UserPromptSubmit", out, sh)])
    print(f"{name:32} block={r.blocking_error!r:8} prevent={r.prevent_continuation} "
          f"ctx={r.additional_contexts}")

# §5.8 — Stop feedback channel and empty-vs-missing reason
sp = A.build_stop(last_assistant_message="done")
for name, out in [
    ("hSO.additionalContext alone", {"hookSpecificOutput": {"hookEventName": "Stop",
                                                            "additionalContext": "run the tests first"}}),
    ('decision=block, reason=""', {"decision": "block", "reason": ""}),
    ("decision=block, reason missing", {"decision": "block"}),
]:
    r = d.dispatch_stop(payload=sp, rules=[rule("Stop", out)])
    print(f"{name:32} force_continue={r.force_continue} follow_up={r.follow_up_message!r} "
          f"ctx={r.additional_contexts}")

# §4 — PreCompact cancellation spelling
pc = A.build_pre_compact(trigger="auto", compaction_type="full", reason="compression_threshold")
for name, out in [
    ("claude continue:false", {"continue": False, "stopReason": "no"}),
    ("agentao compactionDecision", {"hookSpecificOutput": {"compactionDecision": "cancel",
                                                           "reason": "no"}}),
]:
    r = d.dispatch_pre_compact_decision(payload=pc, rules=[rule("PreCompact", out)])
    print(f"{name:32} decision={r.decision!r} reason={r.reason!r}")

# §5.9 — what each of the eight events actually writes to the hook's stdin
cap = pathlib.Path(tempfile.mkdtemp()) / "stdin.json"
for ev, dispatch, payload in [
    ("UserPromptSubmit",   d.dispatch_user_prompt_submit,    A.build_user_prompt_submit(user_message="hi")),
    ("SessionStart",       d.dispatch_session_start,         A.build_session_start()),
    ("SessionEnd",         d.dispatch_session_end,           A.build_session_end()),
    ("PreToolUse",         d.dispatch_pre_tool_use_decision, A.build_pre_tool_use(tool_name="run_shell_command", tool_input={"command": "ls"})),
    ("PostToolUse",        d.dispatch_post_tool_use,         A.build_post_tool_use(tool_name="run_shell_command", tool_input={}, tool_output="ok")),
    ("PostToolUseFailure", d.dispatch_post_tool_use_failure, A.build_post_tool_use_failure(tool_name="run_shell_command", tool_input={}, error="boom")),
    ("Stop",               d.dispatch_stop,                  A.build_stop()),
    ("PreCompact",         d.dispatch_pre_compact_decision,  A.build_pre_compact(trigger="auto", compaction_type="full", reason="compression_threshold")),
]:
    dispatch(payload=payload, rules=[rule(ev, sh=f"cat > {cap}")])
    got = json.loads(cap.read_text())
    envelope = set(got) == {"event", "data"}
    keys = sorted(got["data"]) if envelope else sorted(k for k in got if k != "hook_event_name")
    print(f"{ev:19} {'envelope' if envelope else 'flat':9} keys={keys}")
```

The ten measured assertions, so each is traceable to a line of output: `UserPromptSubmit` drops
`decision:"block"`, `hSO.additionalContext`, `continue:false`, and exit 2 (four — §5.1, §5.5, §5.6);
`Stop` `hSO.additionalContext` does not set `force_continue`, an empty-string `reason` does, and a
missing `reason` does not (three — §5.8); `PreCompact` ignores `continue:false` and honors
`compactionDecision` (two — §4); and the eight-line envelope sweep is the tenth, covering the 6/8
split and every event's field list at once (§5.9). Two rounds were spent getting that last one
honest — first printing the adapter's return value while marking the claim *(measured)*, then fixing
the reporter but sampling two of eight events and generalizing anyway. The rule: **the probe has to
cover the population the claim quantifies over.**

### 9.2 Four method rules, each earned by an error in this document

Kept because each one produced a wrong table cell that survived at least one review.

1. **A model-summarized spec is not a spec.** The reference was first read through a summarizing
   fetch, which was wrong in two places that would have propagated into §3: it reported `continue` /
   `stopReason` as nested inside `hookSpecificOutput` (they are top-level), and `permissionDecision`
   as allow/deny/**escalate** (the values are allow/deny/ask/**defer**). Every reference claim here is
   re-derived from the raw markdown, read directly — Mintlify-hosted docs serve the source at the same
   URL with a `.md` suffix. When a comparison turns on exact field names and nesting, fetch the source
   form and grep it.
2. **Count the units the claim is about, not a proxy for them.** codex's exit-2 support was counted by
   *parser files* (5) rather than events (6 — `Stop` and `SubagentStop` share a parser); the
   events-neither-peer-implements figure subtracted codex's 11 rather than the union of 12; and
   `Stop`'s `additionalContext` was marked implemented on the strength of a `parse` call, without
   following it to a behaviour.
3. **The reference's global tables are overridden per event, and the per-event section is the
   authority.** Four instances, two of them inside the fix for the first two. `model` and `turn_id`
   were read out of the field vocabulary and attributed to `PreToolUse`, where the per-event section
   grants neither — and the correction then over-swung into "`model` is not owed to anything agentao
   implements", false because agentao implements `SessionStart`, which is exactly where `model` lives.
   `permission_mode` was called owed on all six non-flat events when the `SessionStart` and
   `SessionEnd` examples carry none. And codex was scored as deviating for ignoring `continue:false`
   on `SessionEnd`, on the strength of the universal-fields table — while that event's own section
   says it has no decision control and its JSON output is discarded, making codex conformant. A spec
   that says *"Every event accepts them, but some events discard them … Each event's section says so"*
   is telling you the global table is not the contract.
   **With the qualifier the conformance plan later added:** the per-event section overrides when it
   *says something different*; per-event **silence** is not an override.
4. **The probe has to cover the population the claim quantifies over** — §9.1's last paragraph.

### 9.3 What is *not* covered

- The **19** Claude Code events neither peer implements — 31 in the reference, minus a union of 12
  (codex's 11, plus agentao's `PostToolUseFailure`, which codex lacks). Consult the reference
  directly if one becomes relevant.
- `http` and `agent` handler types: absent on both peers, so there is nothing to compare.
- codex's MCP-tool handlers, executor-scoped hooks, and managed-requirements layer: structural, not
  contract, and out of scope here.
- codex's and the reference's own **input** envelopes beyond the fields §5.9 names: both are flat
  snake_case and were compared only where agentao differs.
- Performance. No timing was measured on any side.
