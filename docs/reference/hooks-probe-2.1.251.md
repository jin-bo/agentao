# Claude Code hook behavior — measured against 2.1.251

> **What this is.** A record of what a real `claude` binary *does*, for the questions
> `hooks-claude-contract-conformance-plan.md` could not answer from the documentation. Six of its ten
> design gates cited "probe a real CLI" as the only way to settle a row. This is that probe.
>
> **It is evidence, not a contract.** agentao's own promise is
> `claude-code@profile-1`, whose provenance is the fetched reference page (plan §3). This document
> says what one binary did on one platform on one day, and every finding below carries what it does
> **not** prove.

**Probed:** `claude --version` → `2.1.251 (Claude Code)`, macOS 15 (Darwin 25.6.0), 2026-08-29.
**Method:** each probe is a throwaway project directory with its own `.claude/settings.json`, run as
`claude -p '<prompt>' --model haiku --output-format {json | stream-json --verbose}`. The user's
`~/.claude/settings.json` was read (to confirm it declares **no** hooks, so nothing contaminates a
result) and never modified.
**Relationship to profile-1:** the plan's snapshot is the page as served 2026-08-28
(`c984f918…`), whose changelog head already read 2.1.251 while the page lacked 2.1.251's additions.
So these measurements are of a binary **at or after** the snapshot's page — the two are close but not
provably the same, which is exactly why the profile is named after agentao and not after a product
version.

## Results

| # | Question | Gate | Measured |
|---|---|---|---|
| A | What shell runs a command hook by default? | G5 | **`sh`** — `$0` is `/bin/sh`, `posix` is `on` |
| A | Is the handler's `shell: "bash"` field honored? | G5 | **No** — the same `/bin/sh`, on macOS |
| B | Is `continue: false` honored on `SessionStart`? | G7 | **No — discarded.** The session started, the turn ran |
| C | Is a top-level `decision: "block"` honored on `PostToolUseFailure`? | G7 | **Yes**, and it is *feedback*: reason → the model, original error preserved, turn continues |
| D | What happens to an `updatedInput` that fails the tool's schema? | G8 | **The call is rejected** with a `tool_use_error`; the **original never runs** |
| F | What is actually on stdin, per event? | G7 (§5.3) | Six payloads captured verbatim — see below |
| G3 | How is the string `matcher` evaluated? | G3 | **`*` is a wildcard; everything else is an anchored full match.** The *unanchored* reading is refuted |

---

## A — the shell (G5)

The reference contradicts itself on one page: the *"Exec form and shell form"* section says the
command string is passed to `sh -c`, while the `shell` row of *"Command hook fields"* says the field
*"Defaults to `bash`"*. Plan §2.4 refused to pick and handed it to G5.

Two handlers in one `SessionStart` matcher group, one with no `shell` key and one with `"shell": "bash"`:

```
# no shell key                    # "shell": "bash"
dollar0=[/bin/sh]                 dollar0=[/bin/sh]
bashver=[3.2.57(1)-release]
shellopt=[posix          	on]
```

**Finding.** The *shell form* sentence describes the implementation; the `shell` row's default does
not. `BASH_VERSION` is set only because macOS `/bin/sh` **is** bash 3.2 in POSIX mode — `$0` and
`posix on` are the discriminators, not `BASH_VERSION`.

**Consequences for agentao.** Its `shell=True` baseline is `/bin/sh` on POSIX
(`_dispatcher.py:353`), so the baseline is **conformant** — plan deviation 10's premise is withdrawn.
And because upstream *ignores* an explicit `shell` rather than refusing the rule, agentao must
**ignore the field with a diagnostic, not reject the rule**: rejecting would disable a hook that runs
upstream, which is a conformance regression in the direction the profile exists to prevent.

**What this does not prove.** Windows, where the reference names Git Bash and PowerShell and agentao
has no CI job. Nothing here says whether `shell: "powershell"` is honored there.

## B — `SessionStart` and `continue: false` (G7, contested row 1)

The reference's Decision-control table marks `SessionStart` *"Context only … No blocking or decision
control"* (`hooks.md:1009`), while every **other** event that discards `continue` also says so in its
own section — fifteen times — and `SessionStart`'s section does not. Plan §5.1 took the narrow
reading (`discarded`) on an asymmetric-cost argument and marked the row contested.

Hook output: `{"continue": false, "stopReason": "PROBE_STOP_B", "systemMessage": "PROBE_SYSMSG_B"}`,
plus a marker file proving the hook ran.

```
hook ran            : yes (marker written)
result              : "BRAVO"        ← the turn ran to completion
subtype/terminal    : success / completed,  num_turns = 1
"PROBE_STOP_B"      : 0 occurrences in the run's output
```

**Finding: discarded.** The narrow reading is **confirmed by measurement**. The row stops being
contested; §5.1's `SessionStart` / `continue` cell is `discarded` on evidence, and the flip list's
"if the probe finds it honors the stop" branch does **not** fire.

**What this does not prove.** That `systemMessage` is *also* discarded there. `--output-format json`
carries the result, not the user-notice channel, so its absence from that JSON is not evidence —
see the method note below, which caught exactly this class of false negative once.

## C — `PostToolUseFailure` and `decision: "block"` (G7, contested row 2)

The global Decision-control table names the event (`hooks.md:999`); its own section defines only
`additionalContext` (`:2043-2046`). Plan §5.1 called this the one contested row the document cannot
settle, and required the probe to answer **four** questions, because the global row fixes a wire
shape whose members' effects are mutually incompatible.

Setup: a failing `Read`, and a `PostToolUseFailure` hook printing
`{"decision": "block", "reason": "PROBE_C_REASON"}`.

**Control (C2), run first as a valid control:** the same event, same failing tool, hook printing
`{"unrelated_key": "PROBE_C2_MARKER"}` — marker file proves the hook fired, and the string reached the
model **0 times**. So raw hook stdout is not fed back wholesale; what reaches the model is a
*recognized field*.

What the model received, quoted back verbatim on request:

```
> File does not exist. Note: your current working directory is <PROJECT>.

> PostToolUseFailure:Read hook blocking error from command: "<the hook command>": PROBE_C_REASON
```

**Findings, one per question:**

1. **Accepted?** **Yes.** The wide reading of `hooks.md:999` is correct for this event.
2. **Where does the `reason` go?** **To the model**, as its own labelled line
   (`<Event>:<ToolName> hook blocking error from command: "<command>": <reason>`).
3. **Is the original error preserved?** **Yes** — both lines are present, the original first.
4. **Does the turn continue?** **Yes** — the assistant answered normally; `subtype: success`,
   `num_turns: 2`.

So the effect is **feedback-and-continue**, the same class as `PostToolUse`'s `block`. That is what
the plan refused to assume: it is now measured rather than copied across from a sibling event.

**Sibling measurement (C3):** `hookSpecificOutput.additionalContext` on the same event is delivered
to the model as

```
<system-reminder>
PostToolUseFailure:Read hook additional context: PROBE_C3_CONTEXT
```

appended after the preserved original error.

## D — an `updatedInput` that fails the tool schema (G8)

Plan §4.4 records that the sentence which used to settle this was **never in the reference** — a
fabricated citation, removed in rev 11 — and that the plan's own answer (deny the call; never fall
back to the original) was therefore an agentao choice awaiting a probe.

A `PreToolUse` hook on `Bash` returning
`{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": {"not_a_real_field": 123}}}`
against the prompt *"run: touch D_RAN.txt"*:

```
tool_result is_error = True
  "PreToolUse hook for Bash returned updatedInput that failed schema validation:
   Bash failed due to the following issue:
   The required parameter `command` is missing"

D_RAN.txt            : does not exist   ← the ORIGINAL command never ran
```

**Finding.** Upstream **validates the rewrite against the tool schema and rejects the call**. The
plan's safety-driven choice matches the measured behavior, so it ships as conformance rather than as
a documented deviation from safety. Note the shape of the confirmation: the *behavior* the
fabricated sentence described is real, even though the sentence was not on the page — which is why
the rule is "verify the claim", not "verify the wording".

## F — the real stdin payloads

Captured with `cat > payload_<Event>.json` as the whole hook body, one run, one session. Absolute
paths are normalised to `<HOME>` / `<PROJECT>`; ids are from a throwaway session.

```jsonc
// SessionStart                          // SessionEnd
{ "session_id", "transcript_path",       { "session_id", "transcript_path",
  "cwd", "hook_event_name",                "cwd", "prompt_id",
  "source": "startup" }                    "hook_event_name", "reason": "other" }

// UserPromptSubmit                      // PreToolUse
{ …, "prompt_id", "permission_mode":     { …, "prompt_id", "permission_mode": "default",
      "auto", "hook_event_name",           "hook_event_name", "tool_name": "Read",
  "prompt": "Read the file ./notes.txt…" } "tool_input": {…}, "tool_use_id": "toolu_…" }

// PostToolUse                           // Stop
{ …, "tool_name", "tool_input",          { …, "permission_mode": "default",
  "tool_response": { "type": "text",       "hook_event_name": "Stop",
    "file": { "filePath", "content",       "stop_hook_active": false,
      "numLines", "startLine",             "last_assistant_message": "FOXTROT",
      "totalLines" } },                    "background_tasks": [],
  "tool_use_id", "duration_ms": 3 }        "session_crons": [] }
```

Eight observations, each of which either confirms or corrects a cell of plan §5.3:

| Observation | Effect on §5.3 |
|---|---|
| `permission_mode` **present** on `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop` | confirms the matrix, including the `Stop` row it calls "easiest to omit" |
| `permission_mode` **absent** on `SessionStart` and `SessionEnd` | confirms both `—` cells |
| Its values here are `"default"` and — on `UserPromptSubmit` in the same session — `"auto"` | confirms the enum is upstream's, not agentao's. The intra-session difference is **unexplained** and recorded as an observation, not a rule |
| `prompt_id` present on all five post-input events, **absent** on `SessionStart` | confirms *"absent until the first user input"* |
| `effort` **absent everywhere** in this run | consistent with "conditional on model support"; not a positive measurement |
| `agent_id` / `agent_type` **absent everywhere** | confirms both **forbidden** columns |
| `transcript_path` is a real, continuously-written `<HOME>/.claude/projects/<slug>/<session>.jsonl` | agentao has no equivalent; G7's choice between "build one" and "document `null`" is unchanged, but the target shape is now known |
| `tool_response` is a **structured object** (`{type, file:{filePath, content, numLines, …}}`) | confirms §5.3's hardest row: agentao's tools return `str`, so this is a real type divergence, not a naming one |
| `Stop` carries `background_tasks: []` and `session_crons: []` — **present and empty**, not omitted | upstream emits empty arrays for features that are idle. agentao has neither feature, so it omits them per §1 — a documented difference, now known rather than assumed |

## Method notes — two false results this probe produced before it produced true ones

Recorded because both are cheap to repeat and neither announces itself.

1. **A control that never ran the thing it controlled.** The first C2 run used a prompt containing
   the word `PROBE`; the model decided it was being tested and **declined to call the tool**, so
   `PostToolUseFailure` never fired and the "marker did not reach the model" reading measured
   nothing. Fixed by a neutral prompt **and** a marker file that proves the hook fired. A control
   needs its own reachability proof.
2. **A false negative from a detection method that depends on the model.** The first C3 run reported
   `additionalContext` reaching the model **0 times**; the re-run, with a prompt asking the model to
   quote back everything it received, found it **3 times**. Detection-by-echo only fires when the
   model volunteers the string, so a zero from it is not evidence of absence — the same shape as the
   standing rule that a negative grep proves nothing unless it can find a known positive.

Both are the reason every finding above is stated with what it does not prove.

## G3 — the string matcher (G3)

Plan §2.3 said upstream evaluates the matcher three ways — `*`, exact alternation, and an
**unanchored** regex — on the strength of codex's implementation and the reference's prose. It is the
one claim in this probe that came back **refuted**.

Seven `PreToolUse` matchers, each against a single `Read` call:

| Matcher | Hook fired? | `re.fullmatch(p, "Read")` | `re.search(p, "Read")` |
|---|---|---|---|
| `*` | **yes** | *invalid regex* | *invalid regex* |
| `Read` | **yes** | True | True |
| `^Read$` | **yes** | True | True |
| `Read\|Write` | **yes** | True | True |
| `Rea.*` | **yes** | True | True |
| `ead` | **no** | False | True |
| `Rea\|Wri` | **no** | False | True |

**Finding.** Every one of the seven agrees with `re.fullmatch`, and the last two refute `re.search`:
a substring of the tool name does not match, and neither does an alternation of prefixes. `*` is
special-cased — it is not a valid regex, so it cannot be reaching the regex engine at all.

**Consequence for agentao.** The evaluator it needs already exists: `_regex_match_full`
(`_matchers.py:30`), plus a `*` case. What does **not** change is §2.3's headline — a string matcher
is still not a dict matcher spelled differently, because `toolName` is routed through `_glob_match`,
where `Edit|Write` is a literal with no `*` and matches nothing.

**What this does not prove.** Case sensitivity, what an *invalid* regex does, and whether MCP tool
names (`mcp__server__tool`) are matched by the same path.
