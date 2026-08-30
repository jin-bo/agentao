# Claude hook-contract conformance — a versioned compatibility layer

> **⚠️ Implemented and merged** — PR #199, `18fb628` (2026-08-30). The deviations it closes are
> catalogued in `hooks-three-way-claude-codex-agentao.md` (rev 5), which remains analysis-only.
> **Unreleased**: it ships inside the 0.4.21 dev cycle, not in any published version.

**Status:** **implemented** (rev 24, 2026-08-30) — all seven steps of §8 and all ten design gates.
See §0 for what each gate closed on. rev 23's text stands; this is the same document with the
closures recorded and the steps ticked off. Every `file.py:line` citation below still resolves
against the **pre-implementation** anchor `main@10b5fb8`: they are the evidence for the gap, not a
map of the code that closed it.

**Was:** plan, rev 23 (2026-08-29), after twenty-one maintainer reviews. **Implementation
authorized.** rev 23 is the gate-closure revision: the maintainer took the four decisions the document
reserved, and a probe of a real `claude` binary settled the rest — §0 records every closure and what
it changed. Six table rows moved with it; the design they belong to is otherwise the one review
cleared at rev 22.
**Source:** the maintainer's disposition of the nine deviations, restated with the code consequences
each choice carries. Where this document disagrees it says so inline.
**Anchors:** agentao `main@10b5fb8`; Claude Code hooks reference **fetched 2026-08-28 19:29** from
`code.claude.com/docs/en/hooks.md` (the 2026-08-26 anchor was
`docs.claude.com/en/docs/claude-code/hooks`, which now 301s there; the `.md` sibling serves the page as
source text and is what this document quotes) — **295,595 bytes, sha256
`c984f918cf93f75bd84bc7ea4c63006ca0624f3ddde1431d625af4933def5179`**, 56 documented events. Changelog
head at the same fetch: **2.1.251** (`code.claude.com/docs/en/changelog.md`), whose additions are
**not** in the fetched page — see §3, which is why the label is a profile and not a product version.
OpenAI codex hooks reference `<https://developers.openai.com/codex/hooks>` fetched 2026-08-26.
**Measured behavior:** `docs/reference/hooks-probe-2.1.251.md` — what a real `claude` 2.1.251
actually did for the rows the reference could not settle (§0).
**Twin:** `hooks-claude-contract-conformance-plan.zh.md`.
**Related:** `hooks-three-way-claude-codex-agentao.md` (the evidence; §5.1–§5.10 are the nine).

### Revision history

One line per round. The finding is here; the design it produced is in the section named. This table
exists for two reasons. **Five rounds are reversals** — rev 3, 6, 10, 11 and 14 each unmade a decision
an earlier round had made, and a decision made, unmade and remade reads as arbitrary without the round
that moved it. And **nine rounds found the previous revision's own new material breaking a rule the document already
carried** — rev 9, then 13 through 20 unbroken. The rule broken is nameable in each case, which is what
makes the count checkable rather than a mood: rev 13–17 are catalogued in the rows below; **rev 18**
found rev 17's latch construction violating §12's own standing rule that a test which can pass without
exercising the thing is not a test; **rev 19** found rev 18 weakening a test while leaving its promise
standing, which is the inverse of the fallback pattern §2.5 had already used for G6; and **rev 20**
found rev 19's seam list offering an option that fails rev 19's *own* just-written pairing rule.
(rev 3 and rev 4 are looser cousins and are not counted: rev 2's `contract` gate defeated rev 2's own
*argument* rather than a stated rule, and rev 4's two items were rev 3's regressions.) This is the
failure mode the plan is most prone to, and the reason §5.1 now says a status change re-runs every
checklist keyed on the old status. **The run ended at 20.** The round that produced rev 21 found a
stale statistic rather than a broken rule — nothing carried forward from rev 20 contradicted anything
already written — and the round after it found nothing at all.

| rev | Found | Headline | Landed in |
|---|---|---|---|
| 2 | 4 P1, 2 P2 | rev 1 planned the **wire** contract and never the **configuration** one: a Claude-shaped `hooks.json` parses to **zero rules**. Deviation 0, upstream of the comparison's nine | §2 |
| 3 | 5 P1 | Self-inflicted: rev 2 gated official-shape parsing on a `contract` key a copied Claude file does not contain — still zero rules. Shape becomes **auto-detected** | §2.2 |
| 4 | 3 P1, 2 P2 | Two were rev 3's own regressions: the new `resolve()` returned a bare verdict and **dropped every orthogonal channel**, and treated exit 2 as a boolean where the reference gives it three outcomes | §4.2 |
| 5 | 3 P1, 3 P2 | `resolve()` read plain stdout as model context *before* checking the exit code, and applied `continue:false` without consulting the capability table | §4.2 |
| 6 | 5 P1, 2 P2 | The stdout state machine collapsed *parse* failure into *schema* failure. And rev 5's "eleventh deviation" is **withdrawn** — the reference contradicts itself on one page (`sh -c` vs a `shell` field defaulting to `"bash"`) | §4.2, §2.4 |
| 7 | 4 P1, 3 P2 | The smallest P2 was the largest finding: re-fetching showed the contract is **version-gated**, and rev 6's headline fix had implemented the *pre*-v2.1.248 arm of it | §3, §4.2 |
| 8 | 4 P1, 2 P2 | Claiming a surface without enumerating it, twice: §1 promised the whole contract while **nine output fields** had nowhere to go, and the `@2.1.248` label was inferred from a lower bound | §5.1, §3 |
| 9 | 5 P1, 1 P2 | Three were rev 8's new material breaking rev 8's new rule — `reject` is a configuration verb, so an output field is only ever `accept` or `ignore` | §1, §5.1 |
| 10 | 6 P1, 1 P2 | A ruling rev 9 got wrong: the reference names `PostToolUseFailure` in its **global** decision table and omits it per-event. The standing method rule gains its qualifier — **silence is not an override** | §5.1 |
| 11 | 3 P1, 1 P2 | **A fabricated citation.** §4.4's `updatedInput` quote is not in the snapshot (`grep -c` → 0), and the behavior it justified — keep the original input — was unsafe | §4.4 |
| 12 | 2 P1, 3 P2 | Seams between tables each complete on its own: "universal" fields are not universal, and rev 2's `hookSpecificOutput.agentao` namespace was never implemented anywhere | §5.1, §3.3 |
| 13 | 3 P1, 2 P2 | rev 12's new material against checklists already in the document: a field promoted from prose to a table row, with "every `accept` owes three things" never re-run against it | §5.2.2 |
| 14 | 2 P1, 1 P2 | rev 13's own new route, built on a **third** global table it never read: `hooks.md:1009` marks `SessionStart` *"Context only … No blocking or decision control"*. And the `PostToolUse` stop it did keep stops in a worker three frames below anything that can act on it | §5.1, §5.2.2 |
| 15 | 2 P1, 2 P2 | rev 14 resolved two rows *against* the reference and left no way back: a probe could reverse either and only "invert the test" was written down — hence **flip lists** (G7). Its narrow branch also mixed the two axes, diagnosing a `discarded` field, and justified a batch policy with a fact the 8-worker executor contradicts | §5.1, G7, §5.4 |
| 16 | 1 P1, 1 P2 | rev 15's flip list **pre-filled the answer its own probe exists to get**: it copied `PostToolUse`'s feedback-not-stop semantics onto `PostToolUseFailure`, while §11 q9 two sections away said honoring would *stop* a turn. The shared global row fixes a **wire shape**, not an effect — four of its nine events have mutually incompatible effects | §5.1, G7 |
| 17 | 1 P1, 1 P2 | rev 16's turn-ending branch had no way into the lattice: rank 1 said `continue:false` **only**, G9 repeated it, and §12 bound the branch to rank 2 — three places disagreeing. **"Only" was a census, not a rule**; rank 1 is the class *ends processing*, and a contested row joins it by normalizing to `Stop(reason)`. Its queued-sibling test was also racy | §5.4, G9, §12 |
| 18 | 1 P2 | The queued-sibling test still could not be written: latching seven workers proves occupancy *during the hook*, but the stop is observable only after the dispatcher returns and `_execute_one` releases the worker one line later. **No seam exists at that point**, so G2 owns one — or the assertion is downgraded to batch-outcome level and recorded as able to pass vacuously | §12, G2 |
| 19 | 1 P2 | rev 18 weakened the **test** and left the **promise** standing: a queued-sibling rule with an optional seam is one an implementation can violate and still pass every acceptance run. G2 now picks a **pair** — guarantee **and** seam, or neither, with §1 recording the queued moment as undefined. G6 already used this pattern (weaken the promise, not the test) one gate over | §12, G2 |
| 20 | 1 P2 | rev 19's own seam list smuggled the hole back: it offered "an injectable executor**/cap**", and a bare configurable `max_workers` bounds concurrency without giving the test any control over the instant between the stop becoming observable and the tail being dequeued. A cap may ride along; it is never the seam | G2 |
| 21 | 1 P3 | The self-violation tally had stopped counting itself: still "six rounds, unbroken since rev 13" while rev 18, 19 and 20 each recorded exactly that pattern. Nine now, with the broken rule named per round so the number is checkable — a statistic about a failure mode is not exempt from the failure mode | this section |
| 23 | — | **Gates closed, implementation authorized.** Not a review round: the maintainer decided G2/G6 (weakened branch), G8 (no pre-execution validator) and G7's artifact question, and a probe of `claude` 2.1.251 settled both contested rows, G5's documented ambiguity and G8's flip. Six table rows changed; the two contested rows are now **measured**, one confirming the narrow reading and one reversing it | §0, §2.4, §5.1, §5.2, §5.4, §7 |
| 24 | — | **Implemented.** All seven steps of §8 landed across nine commits, and the ten gates closed as §0 records — four by a maintainer decision, five by probing a real `claude` 2.1.251, one by taking the plan's own proposal. Three of the probes **corrected the plan**: the matcher is anchored, not unanchored (§2.3); `PostToolUseFailure` honors `decision` (§5.1); `SessionStart` / `SessionEnd` matchers compare against `source` / `reason` | all of §8 |
| 22 | none | **Clean pass.** No P1, P2 or P3 — the first of the twenty-one rounds to find nothing. rev 22 is bookkeeping: the status line, this row, and the note that the self-violation run ended at 20. Nothing in §1–§12 moved, so what passed review is what is on disk | this section, §1 |

Two process rules came out of these rounds and are followed here: every patch is re-grepped after
applying (rev 4's English edit silently never ran, and the twins drifted on five items), and every
quotation is copy-pasted from the archived snapshot and re-`grep -F`'d (rev 11).

---

## 0. Gate closures

Nothing here is a new design. Each row is a gate the document deliberately left open, and the
decision that closed it — either the maintainer's call or a measurement. **Where a closure changed a
table, the table is the authority and this section is the index**; §12's tests follow the tables.

Measurements come from `docs/reference/hooks-probe-2.1.251.md`: a real `claude` 2.1.251 driven
headlessly in throwaway project directories, each with its own `.claude/settings.json`. That
document carries the method, the raw observations and — for each finding — what it does **not**
prove. It also records two false results the probe produced before it produced true ones, both from
controls that were not themselves reachability-checked.

| Gate | Closed by | Outcome | Changed |
|---|---|---|---|
| **G2** | maintainer | **Pair (ii): drop the queued-sibling guarantee.** No test seam is built. §1 records the queued-at-stop moment as undefined; §12's test shrinks to the invariant that holds either way — every plan yields a result and a `role:"tool"` message | §1, §12, G2 |
| **G6** | maintainer | **The fallback: "all matching handlers are submitted."** Not "all start". No per-dispatch admission control; under `SessionEnd`'s shared budget a queued handler may never run, and that is stated rather than engineered away. The declaration-order tie-break is unaffected | §2.5, §1, G6 |
| **G8** (validator) | maintainer | **No pre-execution input validation.** No `jsonschema` promotion, no `Tool.preflight()`. §4.4's step 2 is deleted with its two tests, and §1 states the narrowed promise: agentao does not reject a tool's input against its schema before execution, so upstream's "invalid input fires no hook" rule has no analogue here | §1, §4.4, §12 |
| **G7** (artifact) | maintainer | **Provenance table only.** The 295 KB reference page is not vendored; §3's table plus the probe document is what a reviewer gets. §11 q6 closes on that basis, with its cost restated: a quoted clause is locatable by `hooks.md:<line>`, not re-fetchable byte-for-byte | §3, §11 |
| **G5** (shell) | **probe** | **agentao's `/bin/sh` baseline is conformant**, and `shell` is ignored-with-a-diagnostic rather than rejected. 2.1.251 runs command hooks under `sh` (`$0` = `/bin/sh`, `posix on`) and does not honor an explicit `"shell": "bash"`. The reference's self-contradiction is settled by measurement; deviation 10 drops to P3 and its premise is withdrawn. **The gate's other two halves closed at implementation on §9's own terms**: `_paths.py` substitutes all three placeholders and roots `${CLAUDE_PLUGIN_DATA}` at `~/.agentao/plugin-data/<plugin>`, and the dispatcher gained the `args` exec-form branch beside the `shell=True` one | §2.4, §7, §9 |
| **G7** (`SessionStart`) | **probe** | **`continue:false` is discarded — the narrow reading is confirmed.** The hook ran, the session started, the turn completed, and the `stopReason` appeared nowhere. The flip list's "if it honors the stop" branch does **not** fire, and §12's non-stop test now pins a measurement instead of a reading | §5.1, §12 |
| **G7** (`PostToolUseFailure`) | **probe** | **`decision:"block"` is honored — the narrow reading is reversed**, and all four of the probe's questions are answered: the reason reaches the **model** on its own line, the **original error is preserved** before it, and the **turn continues**. So the effect is feedback-and-continue: §5.4's conditional rank-2 row becomes unconditional and rank 1 is untouched. A control run proved the mechanism is the recognized field and not raw stdout — an unrelated key reached the model zero times | §5.1, §5.2, §5.4, §12 |
| **G8** (invalid rewrite) | **probe** | **The plan's choice is what upstream does.** An `updatedInput` that fails the tool schema is rejected with a `tool_use_error` and the **original never runs**. It ships as conformance rather than as a documented deviation from safety. Note what agentao cannot copy: with the validator dropped, agentao has no way to *detect* the mismatch, so the rewritten call reaches the tool and fails there. The outcome that matters is the same — the original never runs — and the difference is the error surface, which §1 records | §4.4, §1 |
| **G7** (input matrix) | **probe**, partly | Six real stdin payloads were captured. They **confirm** §5.3's shape — `permission_mode` present on four events and absent on `SessionStart` / `SessionEnd`, `prompt_id` absent before first input, `agent_id` / `agent_type` absent everywhere, `tool_response` a structured object — and they leave the *decisions* open: what agentao sources for `transcript_path`, and how it maps `permission_mode`. Two facts are new: upstream emits `background_tasks: []` / `session_crons: []` present-and-empty, and `permission_mode` differed within one session (`auto` on `UserPromptSubmit`, `default` on the tool events), which is recorded as an observation and not as a rule | §5.3 |
| **G4** | plan's proposal, taken at implementation | **Tier 1 = 8 MiB per stream per invocation**, opt-in on the shared runner so no other caller's failure mode changes; over it the tree is killed and the hook fails, because output cut mid-JSON has no decision to contribute. **Tier 2 = 10,000 characters per channel** — the reference's own number, characters rather than tokens so the bound does not depend on the configured model. Spill to `.agentao/hook-outputs/`, files `0600`, redacted before the bytes land, pruned by age (7d) and count (200). A failed spill is **reported**, which the tool-output sink it copies does not do | §6, step 1 |
| **G10** | plan's proposal, taken at implementation | **Session-scoped, lock-guarded, keyed by a content-derived rule key** — never `id(rule)`, which changes on reload and would silently re-announce everything. Dispatcher scope was the trap: the dispatcher is constructed at six sites, two inside pool workers, so its state would dedup nothing and race while doing it. `clear_session()` on a plugin reload and on `/clear`, so a corrected hook speaks up again while an unchanged one stays quiet | §4.2, step 2 |
| **G3** | **probe** | **`*` is a wildcard; every other pattern is an anchored full match.** Seven probe points agree with `re.fullmatch` exactly, and two refute the *unanchored* reading this plan carried for its whole life: `ead` does not match `Read`, nor does `Rea\|Wri`. So the fix is agentao's existing `_regex_match_full` with `*` special-cased, not a new three-way evaluator — while §2.3's headline stands, because `toolName` goes through `_glob_match`. **A follow-up run (probe §G3b) adds the second wildcard spelling**: `""` fires too, and `re.fullmatch("", …)` is a miss, so a config that spells the wildcard that way parses with no warning and never fires | §2.3, step 3 |
| **G7** (input side) | plan's rule, applied at implementation | **`transcript_path` is an explicit `null`** — agentao writes no continuous transcript, and a path whose contents lag the session is worse than a null a hook can branch on; `null` rather than absent because the reference makes it required on all eight, so indexing it raises instead of branching. **`prompt_id` omitted** — the per-turn id is not a prompt id, and reusing it invents a correlation. **`permission_mode` mapped or omitted** — `plan`→`plan`, `full-access`→`bypassPermissions`; `workspace-write` is not `acceptEdits` and `read-only` has no analogue, so the field is absent rather than carrying agentao's own vocabulary. **`tool_response` stays a string**, a documented type divergence. The three private fields are **dropped** in profile mode and kept in v1 | §5.3, step 3 |
| **G1** (session events) | plan's proposal, taken at implementation | **A result type plus a route per surface.** `LifecycleHookResult` carries `user_notices` / `model_contexts` / `stop_reason` out of the four lifecycle dispatches that returned attachments and nothing else. Interactive: the CLI consumes the return value it used to discard inside a bare `except: pass`. Headless: `SessionEnd` now dispatches **before** `_emit` and its notices ride on `RunResult.warnings`, which is already serialized — the old order left a headless user no path at all. The tool-worker half of the route (`PostToolUse*` stops) is step 4b | §5.2, §5.2.1, step 4 |
| **G2** (the stop route) | plan's decisions + pair (ii) | **The verdict rides home on `ToolExecutionResult`**, is arbitrated in **plan order** on `ToolRunner`, and ends the turn through the ordinary `_resolve_stop_hook` path with a new `hook_stop` incomplete reason — so `agentao run` needs no exit code of its own. Surfaced as `runner.last_hook_stop` rather than a third tuple element: `execute`'s 2-tuple has callers whose tests are not about hooks, and `Agentao.last_turn` is the codebase's precedent. Both seams read a **string**, never a truthy value — a `MagicMock` runner answers any attribute, and truthiness there lets a stub end turns. Feedback (`additionalContext`, exit-2 stderr) is spliced **beside** the preserved result as a `<system-reminder>`, the shape probe §C measured | §5.2.2, step 4 |
| **G1** (transport) | plan's cheaper option | **An extended `PLUGIN_HOOK_FIRED` payload**, not a new event type: the field is `user_notices`, and a host that renders hook notices reads it there. The two first-party surfaces do **not** depend on it — they route directly (§5.2.1) — which is what made the cheap option viable | §5.2.1, step 5 |
| **G8** (the lifecycle) | plan's order, minus the validator | **The front half lands**: a call the engine already denied still fires the hook under the profile — observation and authority are separate, and the verdict stays DENY. `agentao-v1` keeps the skip. **The back half is a re-entry**: `updatedInput` replaces the arguments, the call is **re-decided** on what will actually run, and the two verdicts intersect to the stricter, so a hook `allow` cannot lift a re-computed DENY and phase 2 confirms the modified input. `defer` degrades to `deny` with the value named; exit 2 denies; `continue:false` ends the **turn**, not the call; `additionalContext` is injected beside the result instead of logged. No validation step, per the maintainer's decision (§1) | §4.4, step 6 |
| **G9** | plan's design, one deviation | **Partition by contract, run, merge once.** All four decision-carrying dispatches are partitioned; a v1 short-circuit ends **only** the v1 group, which is the property that matters — its side effects are the reason the all-handlers rule exists. The merge is group-agnostic, over the event's lattice, with the reason tie-break ranking **inside the winning class** and by declaration order, never group order. **One deviation from the plan's text**: the groups run one after the other rather than concurrently, because G6 took its documented fallback and there is no hook pool to run them in. Sequential ordering *delays* the profile group; it cannot suppress it | §9 |

---

## 1. The promise being adopted

Replaces the claim in `agentao/plugins/hooks/_alias.py:5` — *"a hook script written against Claude
Code can run under Agentao without modification"*:

> **Agentao implements a *declared profile* of the Claude Code hook contract. The event list is
> shorter and the field list is enumerated; within the profile, every event obeys the documented
> contract — in configuration and on the wire. Everything outside it is listed, not silently
> dropped.**

Every clause is load-bearing. **"In configuration and on the wire"**: without it the promise is
unfalsifiable in the direction that matters, since a hook whose configuration does not parse never
reaches the wire contract at all. **"The field list is enumerated"**: "every event obeys the
documented contract" is a claim about the *whole* contract for those events, and a sweep of the
reference against agentao's eight finds nine output fields the design cannot express (§5.1) — a
promise that broad is not narrowed by discovering the ninth field, it has to be replaced by a list.

So the profile is the promise, and it has three parts, each with its own table:

| Part | Table | What it declares |
|---|---|---|
| Events | §5.1 | the eight, and that the other 48 are absent |
| Execution context | this section | **main thread only.** No hook fires inside an agentao sub-agent — sub-agents are constructed without plugins (`agents/tools/_wrapper.py:513`) and `_plugin_hook_rules` defaults to `[]` (`agent.py:532`). Deviation 18 (§7); it is why `agent_id` / `agent_type` are **forbidden** rather than conditional (§5.3) |
| Handlers | §2.4 | `type: command` only; `prompt` / `http` / `agent` / `mcp_tool` are **profile exclusions**, rejected with a warning |
| Fields | §5.1 (output), §5.3 (input) | every **output** field marked **accept / ignore** — and an accepted field then carries a per-event **delivery** value, **honored / discarded** (§5.1's matrix). Every **input** field **required / conditional / forbidden** |
| Field *values* | §5.1 | where a field's enum is wider than agentao implements, the **value** carries its own disposition: **accept / ignore / degrade-to-X**. Never "reject" — that is the third rule below |

Three rules make the profile honest rather than a way to shrink the target:

- **An excluded field is ignored, never an error.** A hook emitting a legal field agentao does not
  implement must still have its *implemented* fields honored. The trap is a parser that treats an
  unrecognized key as a schema failure: the output then becomes `schema_invalid` and raises a
  user-visible `hook error` on perfectly legal hook output. So schema validation applies to the
  *value* of a **known** field (§4.2), and an unrecognized key is ignored with a one-shot diagnostic
  naming it.
- **`reject` is a configuration verb and has no meaning for an output field.** A handler `type`, a
  `shell` value, an `async` flag: those arrive at parse time, and refusing the *rule* is coherent
  because nothing has run yet. A field in a hook's stdout arrives after the process has exited, and
  there is no "rule" left to refuse — refusing the *result* would discard every sibling field in the
  same JSON object, which is what the first rule forbids. "Reject `watchPaths` at parse" is the
  tempting form and it is impossible twice over: the configuration parser never sees a stdout field,
  and the runtime cannot drop one field without dropping the object. So §5.1's output column has
  exactly two values, and §2.4's configuration column keeps `reject`.
  **The rule reaches values, not just fields.** A value agentao cannot honor is in exactly the
  position of a field it cannot honor. So a value is `accept`, `ignore`, or **degraded to a named
  alternative** — and a degrade must say which alternative and why, because silently substituting one
  permission verdict for another is the worst of the three outcomes.
- **Nothing is excluded silently.** A field agentao ignores appears in §5.1 with a reason, the way
  `SUPPORTED_HOOK_TYPES_BY_EVENT` already surfaces a dropped rule as a parser warning
  (`models.py:217`).

**Three things profile-1 explicitly does not promise**, added by §0's gate closures rather than
discovered later. Each is listed here because the alternative is the silent drop §1's third rule
forbids, and each names the decision that produced it:

- **Whether a queued sibling tool call runs after a hook stops the turn is undefined** (G2). agentao
  promises the *batch outcome* — every plan yields a result and a `role:"tool"` message — and nothing
  about the moment between a stop becoming observable and the tail being dequeued.
- **All matching handlers are *submitted*, not guaranteed to start** (G6). Under `SessionEnd`'s shared
  1.5-second budget a queued handler may never run. This is weaker than the reference's parallel
  clause and stronger than today's serial short-circuit.
- **agentao does not validate a tool's input against its schema before execution** (G8), so the
  reference's "invalid input fires no hook" rule has no analogue here — there is no rejection for it
  to describe. The visible consequence is on the rewrite path: where upstream refuses a schema-invalid
  `updatedInput` with a `tool_use_error`, agentao passes it to the tool and the tool fails on its own
  terms. The original input never runs either way; the error surface differs.

This makes the event-count gap (comparison §0: 31 / 11 / 8) formally out of scope, the configuration
shape formally *in*, and the field-count gap **enumerated** rather than either claimed away or
discovered one review at a time.

---

## 2. The configuration contract

Claude Code's `hooks.json` nests four levels: **event → matcher group → `hooks[]` → handler**, and
the matcher is a **string**. agentao's parser reads handlers straight out of the event array
(`_parser.py:102`, `entry.get("type", "")`) and rejects a non-object matcher outright
(`_parser.py:152-164`).

Measured, with the reference's own shape:

```python
from agentao.plugins.hooks import ClaudeHooksParser
P = ClaudeHooksParser()
def show(label, raw):
    rules, warns = P.parse_dict(raw, plugin_name="p")
    print(f"{label:22} rules={len(rules)}")
    for w in warns:
        print(f"{'':22} warn: {w.message}")

show("official shape", {"hooks": {"PreToolUse": [
    {"matcher": "Bash",
     "hooks": [{"type": "command", "command": "jq -r '.tool_input.command'"}]}]}})
show("agentao shape", {"hooks": {"PreToolUse": [
    {"type": "command", "command": "x", "matcher": {"toolName": "Bash"}}]}})
show("string matcher only", {"hooks": {"PreToolUse": [
    {"type": "command", "command": "x", "matcher": "Bash"}]}})
```

```
official shape         rules=0
                       warn: Unknown hook type '' under 'PreToolUse' — skipped
agentao shape          rules=1
string matcher only    rules=0
                       warn: Hook rule under 'PreToolUse' has non-object matcher of type str; matcher must be an object like {"trigger": "manual|auto"} — rule skipped.
```

The matcher group is read as a handler, its missing `type` is `""`, and the rule is dropped. This is
a **tenth deviation**, upstream of all nine: the comparison measured what a hook receives on stdin
and what it may print, and never asked whether the hook is registered at all.

### 2.1 The decision: parse the official shape

The alternative the review offered — narrow the promise to "the handler wire contract, once
registered through agentao's own configuration" — is honest but hollows out §1. A user copying a
Claude Code hook copies the `hooks.json` block; if that is the one thing that cannot be copied, the
wire conformance behind it buys very little.

So: the parser accepts **event → matcher group → `hooks[]` → handler** with a **string** matcher,
and keeps today's flat shape too. The nesting is a single extra level.

### 2.2 Which shape is which: detection, not a declaration

**Do not gate official-shape parsing on a `contract` key.** It is the obvious design and it defeats
the whole point: a file copied out of a Claude Code setup **has no `contract` key** — it is a Claude
file, not an agentao one — so gating on it leaves the copied file parsing to zero rules, which is the
defect §2 exists to close.

The two shapes are mutually exclusive per entry, so detect them:

| Entry has | Shape | Contract when `contract` is absent |
|---|---|---|
| `hooks` (a list), no `type` | official matcher group | newest `claude-code@profile-N` agentao ships |
| `type`, no `hooks` | agentao flat handler | `agentao-v1` |
| **both** | ambiguous | **the file is disabled** |
| **neither** | undetermined | no vote; parsed under the file's contract, which reports it **per rule** |

**Every *shape* failure here is file-level.** An ambiguous entry, a file mixing both shapes, or a
shape that disagrees with an explicit `contract` all disable the whole file with a warning.

**But "neither key" is not a shape failure**, and treating it as one broke a stronger promise than it
kept. An entry with no `type` and no `hooks` claims nothing: it is a malformed *handler*, and
`agentao-v1` — frozen by §3 — has always reported that per rule ("Unknown hook type ''") while its
siblings kept working. Collapsing the two made one typo'd entry disable every other hook in an
existing v1 file. So the table above has four values where it had three, and the rule is narrower
than "ambiguous ⇒ fatal": **only a contradiction is fatal.** A silently half-parsed file is worse than a
refused one — half a hook configuration is not a configuration, and per-entry rejection is exactly
how you get one.

An **explicit** `contract` still wins, and a shape that disagrees with it is a rejection rather than
a coercion. And the two failure directions are not symmetric:

- **Absent** `contract` → detect. This is the copied-file case, and it must work.
- **Explicit but unknown** (`claude-code@profile-99`, or a typo) → **disable the file**, warn, load no
  rules from it. Falling back to `agentao-v1` is wrong here: the author named semantics agentao does
  not have, and running their hooks under *different* semantics is a silent misinterpretation.
  Falling back to the frozen behavior is the right answer for an absent key, not for a wrong one.

### 2.3 A string matcher is not a dict matcher spelled differently

The cheap implementation — translate `"Bash"` into `{"toolName": "Bash"}` and reuse the existing
matchers — is wrong, and quietly so.

- agentao globs `toolName` (`_matchers.py:15`): `*` matches anything, otherwise **exact**.
- agentao full-matches `trigger` as an anchored regex (`_matchers.py:30`).
- Claude's matcher is a string, and its evaluation is **measured**, not inferred: `*` **and `""`**
  are wildcards, and every other pattern is an **anchored full match**
  (`docs/reference/hooks-probe-2.1.251.md` §G3 and §G3b — two runs, because the empty string was
  not among the first seven).

The measurement corrected this section. Earlier revisions said upstream used an **unanchored** regex,
on the strength of codex's implementation and the reference's prose; seven probe points say
otherwise, and two of them are decisive: `ead` does **not** match `Read`, and `Rea|Wri` does not
either — both of which an unanchored search would fire. All seven agree with `re.fullmatch` exactly.

The headline survives the correction, and it is worth separating the two: **a string matcher is
still not a dict matcher spelled differently**, because agentao routes `toolName` through
`_glob_match`, not through its anchored-regex path. `"Edit|Write"` — the most common published
matcher — is a literal string with no `*` in it there, compared for equality, so it matches nothing.
A translation layer would register the rule and never fire it, which is worse than refusing it.

What changed is the *cost*: `claude-code` mode does not need a new three-way evaluator, it needs the
anchored full match agentao already has (`_regex_match_full`) with `*` and `""` special-cased —
`*` is not a valid regex, so it cannot simply be passed through, and `""` matches nothing under
`fullmatch` where upstream treats it as "match all". **G3** is closed on that basis (§0).

### 2.4 The handler-field matrix

The nesting, the matcher and `${CLAUDE_PLUGIN_ROOT}` are not "the configuration contract" — the
reference defines five common handler fields and five more for command hooks. Either the matrix below
is the promise, or the promise narrows to "the listed subset". The matrix is the promise.

| Field | Reference | agentao today | `claude-code` mode |
|---|---|---|---|
| `type` | 5 types | `command`, `prompt`; `http`/`agent` rejected at parse | **accept** `command` only; **reject** `prompt` (see below), `http`, `agent`, `mcp_tool` with a warning |
| `matcher` | string, three-way | dict, two keys | **accept** string (§2.3) |
| `timeout` | per **type**: 600 for `command` / `http` / `mcp_tool`, 30 for `prompt`, 60 for `agent`; `UserPromptSubmit` lowers the command default to **30**; `SessionEnd` handlers share a **1.5 s** budget, raisable to the highest per-hook `timeout` set **in a settings file** — *"Timeouts set on plugin-provided hooks don't raise the budget"* | **60 everywhere** (`_parser.py:141`) | **accept**, with the reference's per-event defaults. Every agentao hook is plugin-provided, so the `SessionEnd` budget is one agentao **cannot** lift from configuration — which is what makes §2.5's all-start guarantee load-bearing precisely there |
| `command` | shell form when `args` absent | always `shell=True` (`_dispatcher.py:353`) | accept (unchanged) |
| `args` | **exec form** — no shell, each element one argument | **absent from `ParsedHookRule`** (`models.py:237`) | **accept.** Not optional: the reference tells authors to set `args` *whenever* the hook uses a path placeholder, so §7.1 without this is half a feature |
| `shell` | `bash` \| `powershell` | n/a — `shell=True` means `/bin/sh` (`_dispatcher.py:353`) | **ignore** with a diagnostic. **Measured**: Claude Code 2.1.251 runs command hooks under `sh` (`$0` = `/bin/sh`, `posix on`) and does **not** honor an explicit `"shell": "bash"` either (`docs/reference/hooks-probe-2.1.251.md` §A). Rejecting the *rule* would disable a hook that runs upstream — the regression direction §1 exists to prevent. `"powershell"` stays untested; agentao has no Windows CI job |
| `async` / `asyncRewake` | background, exit-2 rewake | no background runner | **reject** with a warning |
| `if` | one permission-rule pattern, best-effort | n/a | **reject** with a warning. Reachable in principle — agentao has a permission engine with pattern matching — but it is a sub-feature with its own Bash-subcommand semantics, not a field to wire up. §11 records why, and the disposition lives here |
| `statusMessage` | spinner text | n/a | **ignore** — cosmetic, no contract effect |
| `once` | skill-frontmatter only | agentao has no skill hooks | **ignore** — inapplicable by construction |

**This matrix is the authority.** Where any other section of this plan states a disposition for one
of these fields, it is a reference to this table, not an independent decision. If a disposition
changes, it changes here first — earlier revisions drifted twice by deciding a field in two places
(`if` open here and rejected in §11; two path variables in §7.1 against three here).

Path placeholders are **three**, and the third is the one that gets forgotten: `${CLAUDE_PROJECT_DIR}`,
`${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}`, substituted into `command` **and each `args`
element**, and exported as environment variables on the spawned process. §7.1 covers the export;
`${CLAUDE_PLUGIN_DATA}` additionally needs a per-plugin data directory, which agentao does not have —
that is a decision, not a substitution.

#### The shell: two certain gaps and one documented ambiguity

Two tempting rulings are both wrong: that `shell:"bash"` is a no-op "because bash is already the
default", and that the POSIX baseline is therefore non-conformant. **The reference does not settle
it** — the same page says two different things:

- §"Exec form and shell form": *"**Shell form** runs when `args` is absent. The `command` string is
  passed to a shell: `sh -c` on macOS and Linux…"*
- §"Command hook fields", the `shell` row: *"Shell to use for this hook … **Defaults to `"bash"`**,
  or to `"powershell"` on Windows when Git Bash isn't installed."*

A field whose default is `"bash"` and a shell form documented as `sh -c` cannot both describe the
same unset-field case. Since agentao's `shell=True` gives `/bin/sh` on POSIX (`_dispatcher.py:353`),
whether that baseline is conformant depends entirely on which sentence is authoritative — and this
plan does not get to pick. **G5 resolves it**, either by pinning the dated snapshot's reading or by
probing a real Claude Code install; it is not resolvable from the document.

Two gaps *are* certain and do not depend on the answer:

- **The `shell` field is not honored at all.** An explicit `shell: "bash"` changes nothing today, and
  under `/bin/sh` a hook that asked for bash does not get it. This is what the §2.4 row rejects.
- **Windows.** Python's `shell=True` runs `cmd.exe`, which is neither of the reference's two Windows
  shells (Git Bash, PowerShell). agentao has no Windows CI job, so this is untested in both
  directions — and it is the same gap the codex borrow review flagged as the real headline there.

The fix, once G5 decides, is one argument — `executable=…` on the existing call, or an explicit
`[shell, "-c", cmd]` vector — plus a decision about what happens where the chosen shell is absent.
It stays in G5 with exec form because it is the same code site.

#### Why `prompt` is rejected in `claude-code` mode

The reference's `prompt` hook **calls a model**: the prompt text carries `$ARGUMENTS`, which is
replaced by the hook's JSON input, the model evaluates it, and the model's JSON reply is parsed as
the hook's decision.

agentao's `prompt` hook calls no model. `_run_prompt_hook` substitutes `{userMessage}` into the
prompt string and appends the **result** to `additional_contexts` (`_dispatcher.py:603`) — the prompt
text itself becomes model context. The direction is inverted: upstream sends the prompt *to* a model
and reads a decision back; agentao injects the prompt *into* the conversation.

So this is not an incomplete implementation of the same feature — it is a different feature wearing
the same `type`. Accepting a Claude `prompt` hook in `claude-code` mode would take an evaluation
instruction ("Evaluate if Claude should stop: $ARGUMENTS") and paste it into the conversation as
context, with `$ARGUMENTS` unsubstituted. Rejecting it with a warning is the honest answer.

It stays fully supported in `agentao-v1`, where it is agentao's own extension and documented as such.
Comparison §7 item 5 lists it as a place agentao leads codex — that remains true and unchanged, and
its parenthetical already said "template expansion rather than a model call". Building a real prompt
runner is a separate feature, not a conformance fix.

### 2.5 Serial short-circuit is a semantic divergence

The reference: *"All matching hooks run in parallel. If you define the same handler in more than one
settings file, it runs once. A plugin's or skill's copy of the same handler stays separate."* Every
agentao hook comes from a plugin, so the dedup clause never applies here and the parallel clause
always does. agentao runs them serially **and stops at the
first blocker** — four sites: `PreToolUse` on the first `deny` (`_dispatcher.py:117`), `Stop` on a
block or continuation (`:156`), `PreCompact` on the first `cancel` (`:193`), `UserPromptSubmit` on a
block (`:497`).

This is a **semantic** divergence, not a performance choice — it was once filed under "must not
regress" on the assumption that it was one. A second matching hook that logs to an audit sink, notifies a service, or writes a marker file **never runs**
once an earlier one blocks — its side effects simply do not happen, and nothing tells the author.
Copying a two-hook configuration therefore produces different observable behavior even when both
hooks are perfectly conformant on the wire.

**In `claude-code` mode, all matching handlers start under bounded concurrency; aggregation happens
after.** "All start" is a guarantee, not an aspiration, and a pool cap alone does not provide it —
past the cap handlers queue, and a queued handler under a shared deadline may never run. The
per-event handler count is therefore bounded (gate G6). **Where** it is bounded is the part that is
easy to get wrong, in two independent ways.

*The limit belongs at the merge point, not at parse time.* "At load time" reads as "while the file is
parsed", and rules are parsed per file and concatenated afterwards:
`resolve_all_hook_rules` walks every plugin and every entry in its `hook_specs`, parsing each into
its own list and extending one flat result (`_user_turn.py:28-59`). Two plugins with three
`SessionEnd` handlers each are six handlers on one event, and neither file exceeded anything. The cap
therefore applies to the **concatenated** list, with a warning that names the plugins that collided —
a per-file check bounds nothing an operator actually installs.

*And a cap on the configuration still does not deliver the guarantee, because dispatches race each
other.* A batch of tool calls runs on an 8-worker thread pool (`tool_executor.py:189`) and each worker
fires its own `PostToolUse` / `PostToolUseFailure` dispatch from inside itself
(`tool_executor.py:463-472`). Eight individually conformant dispatches against one shared hook pool
queue behind each other exactly as over-cap handlers within a single dispatch would. So admission is
per **dispatch**, not per handler: a dispatch acquires capacity for all of its handlers or waits
before starting any of them — a per-dispatch executor sized to that event's (now bounded) handler
count, under a global thread ceiling that bounds the total. The alternative, if that machinery is
judged too much, is to weaken the promise to "all matching handlers are **submitted**" and say
plainly that under `SessionEnd`'s shared 1.5-second budget a queued one may never run. That is
honest, and it gives up the fix on the one event whose deadline is short enough for the difference to
show. Both belong to G6.

**Removing the short-circuit while keeping serial execution does not hold together**, which is worth
stating because it is the cheap-looking fix. §2.4's timeout row gives `SessionEnd` handlers a shared
**1.5-second budget**; in series the first handler can consume it and the second never starts. "All
handlers run" and a shared deadline are not jointly satisfiable serially. Either concurrency is in
scope, or the timeout row and the all-run promise both come back out — and taking them out hollows
§1 the same way gating the config shape on `contract` did.

The cost is real and should be stated:

- **Every matching hook spawns**, even when the verdict is already known. The short-circuits exist
  partly to stop forking once decided (`PreCompactHookResult`'s docstring says so explicitly).
- **A fourth thread pool.** `CLAUDE.md` documents three deliberately non-contending pools —
  `agentao-arun-*`, `agentao-web-html-*`, and the loop default left free for httpx — and warns
  against collapsing them. Hooks need their own named pool with its own bound, not a borrowed one.
- **Aggregation must stop depending on completion order.** "First deny wins" currently means first
  *to run*; concurrently it would mean first *to finish*, and the winning `reason` would vary run to
  run. Merge rules become order-independent (deny wins if **any** hook denies) and any tie-break —
  which reason to surface — resolves by **declaration order**, not completion order. This is design
  gate **G6**.

`agentao-v1` keeps serial short-circuit dispatch unchanged.

---

## 3. The contract version

**File-scoped, resolved onto every rule** — not per-rule, which overstates it: one file has one
contract, and the value is copied onto each `ParsedHookRule` it produces so the
dispatcher — which holds a rule, never a file, at every decision point — can act on it. Handler-level
override is **not** offered in this plan; if it is ever wanted, the field is already in the right
place to carry it.

```json
{
  "contract": "claude-code@profile-1",
  "hooks": { "PreToolUse": [ { "matcher": "Bash", "hooks": [ { "type": "command", "command": "…" } ] } ] }
}
```

| Value | Meaning |
|---|---|
| `agentao-v1` | today's **contract surface**, frozen. Flat handler list, dict matcher, `{event,data}` envelope, top-level `blockingError` / `preventContinuation` **plus nested `hookSpecificOutput.compactionDecision`** (§3.3 — the three are **not** all top-level, which this plan asserted for ten revisions), `suppressOutput` controls the `<stop-hook>` echo, serial short-circuit dispatch, `Stop` reentry cap 3. See "what frozen does not cover", below. |
| `claude-code@profile-<n>` | the **agentao profile** of the Claude contract — the enumerated event, handler and field set of §1, with its configuration shape, matcher semantics, input payloads and output fields. Each profile carries the provenance of the upstream document it was derived from (below). |
| `claude-code` | alias for the newest profile agentao ships. Convenient, and it **drifts by design**; plugins that need stability pin the numbered form. |
| absent | **detected from the file's shape** (§2.2) — official nesting ⇒ newest profile, flat ⇒ `agentao-v1`. Newly generated plugins still write a numbered `claude-code@profile-…` explicitly. |
| explicit but unknown | **the file is disabled** with a warning. Not a fallback — see §2.2. |

**What "frozen" does not cover.** "Today's behavior, frozen" is too broad: §8's step 1 changes
truncation, preview text and the spill path for *every* hook, two steps before `contract` is parsed
at all — and by design it must, since a memory bound cannot be a per-file opt-in (§6). Rather than
relabel step 1 as invisible, the promise is scoped:
`agentao-v1` freezes what a hook author can **depend on** — configuration shape, payload fields,
decision semantics, dispatch order and its short-circuit, the reentry cap — and does not freeze the
**resource envelope** the runtime enforces around it. A hook whose output was unbounded and is now
bounded still parses the same file, receives the same payload, and gets the same decision honored.
§11 question 2 keeps the drift question open on the contract half only.

The label carries a snapshot because the upstream contract moves: between the comparison's anchor
and this plan's first draft, the reference's `permissionDecision` gained `defer` and
`prompt_id` appeared as a version-gated common field. A bare `claude-code` that silently means
"whatever agentao implements today" makes a plugin's behavior a function of the agentao version with
nothing in the file to say so.

**The label names an agentao profile, because neither a date nor a product version can be
substantiated.** A date (`claude-code@2026-08-26`) records only the day someone fetched a web page. A
product version (`claude-code@2.1.248`) looks better, because the reference names product versions
inside behavior clauses — four of them in text this plan depends on:

| Version | The behavior it gates |
|---|---|
| v2.1.199 | an MCP tool marked `requiresUserInteraction` can no longer be auto-approved by a hook `"allow"`, with or without `updatedInput` |
| v2.1.212 | `modelsUsed` appears as an input field |
| v2.1.214 | exit 2 **plus** schema-invalid JSON still blocks, using stderr as the reason. Before: a non-blocking error, and the action proceeded |
| v2.1.248 | stdout that fails to parse as JSON is a `hook error` notice and is **not** added as context. Before: plain text |

The last two are load-bearing for §4.2, and this plan once implemented the "before" arm of one of
them without knowing there was an arm — which is why the gates are tabulated here rather than
discovered per clause. **But naming the snapshot after the newest version it mentions does not
follow.** A `Before vX` clause bounds the page from **below** — it says the page describes
post-2.1.248 behavior for *that clause* — and says nothing at all about the top. An addition ships
without a gate, because there is no prior behavior to contrast with.

Measured, at the same fetch:

| Question | Answer |
|---|---|
| Newest version in the changelog (`changelog.md`) | **2.1.251**, dated 2026-08-28 |
| What 2.1.251 added to hooks | `PreModelSwitch` / `PostModelSwitch` events; `SessionStart` resume input gains staleness and re-cache cost |
| Are they in the fetched page? | **No** — `grep -c 'PreModelSwitch'` → `0`; no `staleness` / `re-cache` either |

So the page is not the documentation of 2.1.251, and calling it 2.1.248 asserts an upper bound
nothing supports — the page could equally carry an ungated change from 2.1.249 or 2.1.250. It is one
artifact: **the page as served at 2026-08-28 19:29**, whose relationship to any released binary is
unverified in both directions.

Two honest ways out, and the plan takes the first:

1. **Name the profile after agentao.** `claude-code@profile-1` is a set agentao enumerates (§1),
   implements and tests, stamped with the provenance of what it was derived from:

   | Provenance field | Value for profile-1 |
   |---|---|
   | Source | `code.claude.com/docs/en/hooks.md` |
   | Fetched | 2026-08-28 19:29 |
   | Bytes / sha256 | 295,595 / `c984f918cf93f75bd84bc7ea4c63006ca0624f3ddde1431d625af4933def5179` |
   | Changelog head at fetch | 2.1.251 — **whose hook additions the page did not yet contain** |
   | Behavior gates relied on | v2.1.214, v2.1.248 (§4.2) |
   | Live page **one day later** | 297,440 bytes, sha256 `b727657a202f472207b60fd443aa5542d8c6e1f8b9aef79689c8ec917cf19e6a` (2026-08-29) |

   **The anchor drifted in a day, and the measurement is the argument for this whole section.** The
   2026-08-29 re-fetch differs by **19 lines**, all in `SessionStart`'s input section: the four
   resume-staleness fields (`seconds_since_last_response`, `context_tokens`,
   `prompt_cache_likely_expired`, `estimated_cache_write_usd`) the page's own text marks *"require
   Claude Code v2.1.251 or later"* — exactly the addition §3 recorded as **absent** at the 08-28 fetch
   while the changelog head already read 2.1.251. So the page caught up with its own changelog
   overnight, which is what a label like `claude-code@2.1.251` would have asserted a day too early.
   Nothing bearing on this plan changed: the Decision-control table, the universal-field rule, and
   every `continue` / `decision` clause are byte-identical across the two fetches. Profile-1 stays
   pinned to `c984f918…`; the diff is recorded so the next reviewer does not have to re-derive it.

   agentao can warrant what it implements; it cannot warrant Anthropic's version semantics, and a
   label that pretends otherwise is the same over-claim as §1's old wording, one layer down.
2. **Keep chasing the product version**, which requires installing that exact CLI and probing the
   behaviors — a real harness with a real cost, and the only thing that would justify the name. If
   that is ever built, `claude-code@2.1.248` becomes meaningful and profile-1 becomes its alias.

The artifact question stays with G7: a profile that resolves only to a live URL is not a snapshot.
Either the fetched reference is archived in-repo (`docs/reference/snapshots/`, ~290 KB of upstream
prose — a redistribution question worth asking before doing it), or the repo carries the provenance
table above and the archive lives outside it. The hash is what makes either one checkable.

Default flips no earlier than the next major. **No dual-shape payloads** — emitting both field sets
would be a third contract, and the matcher would have to guess which the author meant.

### 3.1 Where it lands, and the one place it has nowhere to go

`contract` becomes a field on `ParsedHookRule` (`models.py:237`), beside `plugin_name`.

The complication: `parse_dict` does `hooks_dict = raw.get("hooks", raw)` (`_parser.py:66`) — it
accepts **either** the wrapper **or** a bare events dict. In the bare form a top-level `"contract"`
key is not metadata; it is parsed as an event name and degrades to an "unsupported hook event"
warning. So:

- `contract` is read only from the wrapper form.
- The bare form has no place for the key — but it still gets **shape detection** (§2.2), so a bare
  official-shaped dict is not stranded on `agentao-v1`.
- Both entry points (`parse_file` `:28`, `parse_dict` `:44`) thread the resolved value onto every
  rule they emit; nothing downstream re-derives it.
- An unknown `contract` value **disables the file** (§2.2). Only an *absent* one falls through to
  detection.

### 3.2 The payload is built before rules are selected

`_dispatch_user_prompt_submit` builds one payload for the event (`_hook_dispatch.py:44`) and hands it
to the dispatcher, which *then* selects rules. Contract-per-rule inverts that. Cheapest correct
shape: pass a per-event builder closure into dispatch, memoized per contract — two payload
constructions per event at worst, not per rule.

This also retires the shape-sniffing in `_matches` (`_dispatcher.py:313,323`), which reads both
layouts today only because it cannot know which it was handed.

### 3.3 agentao's own fields are out of profile-1

The natural design — agentao-only output keys "move under `hookSpecificOutput.agentao`" in
`claude-code` mode and stay top-level in `agentao-v1` — stood in this plan for nine revisions with
**nothing implementing the namespace**: no field on `ParsedHookOutput` (§4.1), no row in the
capability table (§5.1), no consumer (§5.2), no aggregation rule for two handlers setting it. Under
§4.2's unknown-key rule `hookSpecificOutput.agentao` is simply an unrecognized key — collected into
`unknown_fields`, ignored, diagnosed. **The promise was inert from the day it was written**, and
§5.1's "every `accept` owes three things" check would have caught it had the namespace ever been
entered in the table it was meant to live in.

It is also not merely unimplemented. On `Stop`, the reference's `decision:"block"` means **continue
the conversation** and agentao's `blockingError` means **end the turn** (`_runner.py:964` vs `:984`,
§5.4) — so a single output carrying both would hold two opposite controls, and §5.4's lattice does not
help: it merges across *rules*, and this is one rule's own output. Supporting the namespace therefore
costs an **intra-output** precedence rule on top of the four missing pieces, for a feature nobody has
asked for.

**So profile-1 does not have it.** In `claude-code` mode agentao's own control keys are simply not
available; a hook that wants `blockingError`, `preventContinuation` or `compactionDecision` declares
`agentao-v1`, where they work exactly as they do today. The namespace can return in a later profile,
and the price is now written down: a field, a capability row, a consumer, an aggregation rule, and an
intra-output precedence rule.

**And the three v1 keys are not all top-level**, which this plan asserted for ten revisions before
checking. Two are — `blockingError` and `preventContinuation` are read from the top level
(`_output_parsing.py:65`) — but **`compactionDecision` is not**: it is read from
`hookSpecificOutput.compactionDecision` (`_dispatcher.py:226-229`). v1 already mixes the two shapes,
which is a small argument in the same direction: the "namespace agentao's keys" idea was never applied
consistently even inside v1.

What does *not* change: comparison §4's conclusion that there is **no de-facto standard to converge
on** for `PreCompact` cancellation — the reference wants exit 2 or top-level `decision:"block"`, codex
uses `continue:false`, which the reference documents as *discarded* for that event. In `claude-code`
mode agentao follows the reference, not codex; in `agentao-v1` `compactionDecision` keeps working, so
the existing control plane (`CLAUDE.md`, "The control plane has two layers and one merge rule") is
untouched.

---

## 4. The normalized parse result

The diagnosis holds and the code shows why. `_parse_command_output` (`_output_parsing.py:26`) writes
straight into runtime fields and **returns early** after `blockingError` (`:65`),
`preventContinuation` (`:77`), and `additionalContext` (`:90`). Two consequences:

- A hook emitting more than one recognized key gets only the first honored.
- The reference's precedence — `continue` *"takes precedence over any event-specific decision
  fields"* — is unimplementable as more `if` branches, because precedence needs every field parsed
  *before* anything is decided.

**Name it `ParsedHookOutput`, not `HookOutcome`:** `_HookOutcome` is already taken
(`runtime/chat_loop/_outcomes.py:13`) for the UserPromptSubmit dispatch verdict.

### 4.1 Universal fields plus a per-event typed union

A single `control: allow | block | stop | continue` is not enough: it cannot express
`permissionDecision` (`allow`/`deny`/`ask`/`defer`), and it has no slot for `updatedToolOutput` —
which would contradict §10 item 3, where `ask` is a lead that must not regress (`models.py:302`
supports it today; codex rejects it).

**Two types, not one.** What a hook's JSON *says* and what the runtime *does* are different objects,
and the second is a function of the first plus the exit code:

```
ParsedHookOutput            # what one hook's stdout claims — the parse, nothing resolved
├── universal
│   ├── continue_processing : bool          # top-level `continue`; outranks the event decision,
│   │                                        #   is itself outranked by exit 2, and is honored only
│   │                                        #   where the capability table says so — see §4.2
│   ├── stop_reason         : str | None
│   ├── system_message      : str | None
│   ├── terminal_sequence   : str | None     # the fifth universal field; §5.1 rules it
│   │                                        #   accept-or-ignore, but it must parse
│   └── suppress_output     : bool           # inert in claude-code mode
├── additional_context : list[str]           # hookSpecificOutput.additionalContext — six of the
│                                            #   eight events carry it, so it is not event-specific;
│                                            #   §5.2 routes it per event, this only holds it
├── unknown_fields : list[str]               # keys agentao does not implement — kept as names
│                                            #   only, for the one-shot diagnostic (§4.2). Their
│                                            #   presence is never an error; see §1
├── plain_text : str | None                  # stdout when the state is "plain" (§4.2)
└── decision : one of
    ├── PreToolUseDecision   { permission: allow|deny|ask|defer|None, reason, updated_tool_input }
    ├── PostToolUseDecision  { block: bool, reason, updated_tool_output }
    ├── UserPromptSubmitDecision { block: bool, reason, suppress_original_prompt: bool }
    │                                        # the flag is parsed and, in profile-1, not acted
    │                                        #   on (§5.1) — representing what the table then
    │                                        #   declines is the same discipline `defer` gets
    ├── BlockDecision        { block: bool, reason }   # Stop, PreCompact, and
    │                                        #   PostToolUseFailure — whose disposition is
    │                                        #   CONTESTED (§5.1). The type can hold a decision
    │                                        #   the profile does not currently honor; that is
    │                                        #   why parse and disposition are separate layers
    └── SessionStartDecision { reload_skills: bool }   # parsed, ignored in profile-1 (§5.1)
        # SessionEnd remains context-only; it has no decision control at all

ResolvedHookOutput          # what resolve() returns — what runtime sites actually consume
├── control : Allow | Block(reason) | Stop(reason) | PermissionDecision(...) | None
├── user_notices[]        → the human
├── model_contexts[]      → the model's context channel
├── tool_contexts[]       → injected next to a tool result
├── updated_tool_input / updated_tool_output
└── diagnostics[]         → warnings, parse failures, budget notices
```

`defer` is carried even though agentao does not implement it (the reference marks it `-p` only). The
type must be able to *represent* what the capability table then **degrades or declines**; a value that
cannot be parsed cannot be degraded with a reason, only silently dropped — and "reject it" is what
§1's third rule forbids for an output field, so §5.1 degrades `defer` to `deny` and says so in the
reason. The same separation covers the contested `PostToolUseFailure`
decision and the parsed-but-unhonored `suppressOriginalPrompt` / `reloadSkills`: **the parse layer is
wider than the disposition layer, deliberately**, so that a disposition can change in a later profile
without changing the parser.

`additional_context` sits above the union rather than inside it because six of the eight events carry
it — `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `Stop`.
It is the single most-used channel in the contract, and leaving it out of both halves is the easy
mistake: `absorb_channels()` in §4.2 then has no typed field to read. Where the value *goes* is
per-event (§5.2's consumer table) — model context, tool context, or nothing — but
that is routing, not parsing.

### 4.2 Precedence is a function, not a field ordering

`continue` is **not** simply "highest precedence". The reference says something narrower and
something else on top of it:

- `continue: false` *"Takes precedence over any event-specific decision fields"* — over `decision`,
  over `permissionDecision`. Not over everything.
- Exit 2, on an event that can block: *"exit 2 blocks whether or not you print JSON: even a JSON
  `permissionDecision` of `\"allow\"` can't override it"* — and Claude still **reads** the JSON, using
  its blocking reason when it has one and stderr otherwise.

So the order is **exit 2 → `continue` → event decision**, and it cannot be expressed as field
ordering inside a dataclass.

Two things a first implementation gets wrong. First, **exit 2 is not a boolean.** The reference
gives it three outcomes, and all three are live across agentao's eight events:

| Exit-2 outcome | Events (of agentao's eight) |
|---|---|
| **blocks** | `PreToolUse`, `UserPromptSubmit`, `Stop`, `PreCompact` |
| **stderr → the model** (the tool already ran) | `PostToolUse`, `PostToolUseFailure` |
| **stderr → the user only** | `SessionStart`, `SessionEnd` |

A single `blocks_on_exit_2` predicate, with everything else falling through to the plain-stdout path,
silently discards the stderr on exactly the two events where §5.2 promises it reaches the model.

Second, **the control verdict is not the whole result.** Returning `Block` / `Stop` / `decision`
alone drops `systemMessage`, `additionalContext`, `tool_contexts`, `updatedToolOutput` and
`diagnostics` — the channels §4.1 exists to keep separate. They are orthogonal to the verdict: a hook
that blocks *and* emits a user notice does both.

So `resolve()` branches over the exit code **first**, and two ordering mistakes follow from not
doing that. Taking plain-text stdout as model context before looking at the exit code — the
reference gates it on **exit 0** (*"The exceptions are `UserPromptSubmit`, `UserPromptExpansion`,
and `SessionStart`, where Claude Code adds plain-text stdout as context"*, §"Exit code 0"), so a
`SessionStart` hook that fails with exit 1 and prints a diagnostic would have had that diagnostic
injected into the model's context. And it applied `continue:false` unconditionally, where the
reference discards the field on roughly a dozen events including `SessionEnd`, `PreCompact`, and
`PostCompact` — the capability table already knows this and was not consulted.

The branch structure is `{0, 2, other}` × **five** stdout states — not the three or four it looks
like from a first reading of the page. As fetched 2026-08-28 the reference decides *whether to attempt JSON at all* from **both ends** of the string,
and it treats a parse failure as an error rather than as text:

| State | What produced it | Where it goes |
|---|---|---|
| `empty` | nothing on stdout | nothing — but see the non-0/2 notice below |
| `plain` | trimmed stdout that does not both start with `{` and end with `}` — *"Starts with `{` but doesn't end with `}`: Claude Code treats it as plain text"*, *"Starts with anything else: Claude Code treats it as plain text, a JSON array or a quoted JSON string included"*. Also multi-line output whose lines each parse as JSON and none of which sets a JSON-output field | model context on **exit 0**, and only on `UserPromptSubmit` / `SessionStart` |
| `parse_error` | starts with `{`, ends with `}`, does not parse — or is the multi-line case where one line **does** set a field | a **user-visible** `hook error` notice with the parse message, on every exit code other than 2; and *"On the events that add plain-text stdout as context, Claude Code doesn't add the text"* |
| `schema_invalid` | parses to an object, and a **known** field's value fails validation — an unrecognized key never lands here (§1, and below) | the same user notice, with the validation message; *"the action proceeds"* — **except on exit 2, which still blocks** |
| `valid` | parses and validates | channels and decision, on **every** exit code |

Three traps in that table, each traceable to a clause:

- **`[`-leading is never JSON.** "Not starting with `{`/`[`" is the natural way to write the `plain`
  row, and it implies an array is parsed. The reference puts a JSON array and a quoted JSON string on
  the plain-text side by name.
- **The gate is both ends.** A `{"decision":` truncated by a dying pipe is *plain text*: it never
  reaches the parser, so it is not a parse error either.
- **A parse failure is not text, and this is version-gated.** The intuitive reading — a `{`-leading
  string that fails to parse is plain text by the first-character rule — was true, and the sentence
  that said so (*"If it isn't valid JSON, Claude Code treats it as plain text"*) **is no longer in the
  reference**. What stands in its place: *"when Claude Code tries to parse your stdout as JSON and
  can't, it reports a non-blocking error on every exit code other than 2 … On the events that add
  plain-text stdout as context, Claude Code doesn't add the text. **Before v2.1.248**, Claude Code
  treated that stdout as plain text."* Under the targeted snapshot (§3) it is a notice and the text is
  withheld — §12's test asserts that direction, and the pre-2.1.248 assertion is the one to avoid
  re-deriving.

**An unrecognized key is not a schema failure.** agentao's profile (§1) is narrower than the
reference's field set by nine fields on these eight events (§5.1), so a hook that is perfectly legal
upstream routinely emits keys agentao does not implement. Validating with a closed schema turns every
one of them into `schema_invalid`, which the table above routes to a user-visible `hook error` —
agentao would be telling the author their correct hook is broken, and dropping the fields it *does*
implement in the same object. So: validation applies to the **value of a field agentao
declares**; an undeclared key is collected into `unknown_fields` (§4.1), ignored for control purposes,
and surfaced **once per (rule, field)** as a `diagnostics[]` entry — the author still learns the field
had no effect, without a per-invocation notice. *Once* needs an owner, and the obvious one is wrong:
`PluginHookDispatcher` is constructed fresh at **six** call sites — `cli/session.py:79,96`,
`_hook_dispatch.py:47,124`, `tool_runner.py:275`, `tool_executor.py:662,685` — and the last two run
**inside pool workers**, so dispatcher-scoped state would dedup nothing and race while doing it. Gate
**G10** owns this: a **session-scoped, lock-guarded registry** keyed by a *stable* rule key
(`plugin_name` + source file or inline index + event + matcher + handler index — never `id(rule)`,
which changes on reload), cleared when plugins reload so a corrected hook re-announces, and cleared
with the session. Get it wrong in either direction and the mechanism inverts: per-invocation noise, or
silence. This is the output-side twin of §5.3's input rule
(never fabricate) and of `SUPPORTED_HOOK_TYPES_BY_EVENT`'s existing discipline (`models.py:217`).

Two smaller rules, both specified upstream. A non-0/2 exit with `plain` or `empty` stdout is a
**user** notice, specified down to the wording: *"followed by the first line of stderr, prefixed with `Failed with
non-blocking status code:`"* (§"Other exit codes"). And schema failure is a user notice rather than an
internal log line — **with an exit-2 qualifier** that is easy to drop from the prose while the code
happens to keep it: *"A hook that exits 2 while printing JSON that fails JSON output schema validation still
blocks: Claude Code uses stderr as the blocking reason and records the validation failure in the
debug log. Before v2.1.214, Claude Code treated that combination as a non-blocking error and the
action proceeded."*

```python
def resolve(event, returncode, stdout, stderr, table) -> ResolvedHookOutput:
    out = ResolvedHookOutput()
    # state: "empty" | "plain" | "parse_error" | "schema_invalid" | "valid".
    # ``parsed`` is None unless the state is "valid"; ``failure`` carries the
    # parse or validation message for the two failure states and is None
    # otherwise — a failed parse has no object to hang a message on.
    parsed, state, failure = parse_stdout(stdout)

    # 1. Channels. Valid JSON applies on every exit code. A parse or schema
    #    failure is a user-visible notice on every exit code EXCEPT 2, where
    #    the block below owns the outcome and stderr supplies the reason.
    if state == "valid":
        out.absorb_channels(parsed, table, event)     # system_message, additional_context, updated_*
    elif state in ("parse_error", "schema_invalid") and returncode != 2:
        out.user_notices.append(f"{event} hook error: {failure}")
    elif state == "plain" and returncode == 0:
        # Plain text is context on exit 0 only, and only where the event allows it.
        table.plain_text_channel(event, stdout, into=out)   # no-op outside UPS / SessionStart

    # 2. Exit 2 — three outcomes (see the table above).
    if returncode == 2:
        kind = table.exit2(event)          # "block" | "model_feedback" | "user_notice" | "ignore"
        reason = parsed.blocking_reason if state == "valid" else None
        if reason is None:
            reason = stderr                # JSON's reason when it has one, stderr otherwise
        if kind == "block":
            out.control = Block(reason)
            return out                     # JSON cannot override an exit-2 block
        if kind == "model_feedback":
            out.model_contexts.append(reason)
        elif kind == "user_notice":
            out.user_notices.append(reason)

    # 3. Any other non-zero exit with no usable JSON is a non-blocking error the user sees.
    elif returncode != 0 and state in ("plain", "empty"):
        first_line = stderr.splitlines()[0] if stderr.strip() else ""
        out.user_notices.append(
            f"{event} hook error: Failed with non-blocking status code: {returncode} {first_line}"
        )

    # 4. Control verdict from JSON, only if exit 2 did not already settle it.
    if state == "valid":
        if parsed.universal.continue_processing is False and table.honors_continue(event):
            out.control = Stop(parsed.universal.stop_reason)
        else:
            out.control = table.apply(event, parsed.decision)
    return out
```

Five things this ordering buys, the last four of which earlier revisions got wrong: valid JSON takes
effect on **every** exit code (*"Claude Code reads JSON output fields from stdout on every exit code,
not just 0"*); plain text reaches the model on **exit 0 only**; a *parse failure* reaches it never;
`continue` passes through `table.honors_continue(event)` rather than firing everywhere; and **four
separate failure shapes reach the user** rather than a log — unparseable JSON, schema-invalid JSON, a
non-0/2 exit with no JSON, and exit-2 stderr on the events whose exit-2 outcome is a user notice. The reference confirms these are one
channel, not three: for `SessionStart`, exit-2 stderr *"renders in the transcript as a `<hook name>
hook error` notice, the same way a non-blocking error does"*, and Claude does not see it.

The `reason` fallback is spelled out rather than folded into a conditional expression, because the
compact form `parsed.blocking_reason if parsed else None or stderr` binds as
`(parsed.blocking_reason) if parsed else (None or stderr)` — so a hook that exits 2 with JSON but no
blocking reason would have blocked with `reason=None` and never reached stderr.

agentao already has this shape in one place: `_run_stop_command_hook` checks `proc.returncode == 2`
**before** parsing JSON, deliberately, so `continue:false` in stdout cannot countermand it
(`_dispatcher.py:562`). That is the precedent — it just has to become the rule rather than one
event's special case.

The matrix (§12) must therefore cover the **combinations**, not the fields one at a time: exit 2 ×
`continue:false`/`true`/absent × `allow`/`block`/absent, per event.

### 4.3 `user_notices` is half-built already

`StopHookResult.system_message` **exists** (`models.py:372`) and is set by the parser
(`_output_parsing.py:180-182`) — and nothing in `agentao/` reads it. The only reader anywhere is a
test (`tests/test_hooks_stop_suppress_output_and_system_message.py:37`). The same parser then *also*
appends the string to `additional_contexts` (`:183`), the model channel.

So the `systemMessage` fix is **stop the double-write and give the existing field a consumer**, not
"build a channel".

### 4.4 The `PreToolUse` lifecycle: when it fires, and `updatedInput` as a re-entry

Two halves, one ordering: **when the hook fires at all**, and *what happens after* it returns a
rewrite. agentao gets the front half wrong in one place and right in another by accident.

The reference states it in a single note, on `PostToolUseFailure`:

> *"This event doesn't fire for tool calls rejected before execution: an unknown tool name, input that
> fails schema or tool-specific validation, or a permission denial. Validation rejections are returned
> as `tool_use_error` results and happen before hooks run, so they fire neither `PreToolUse` nor
> `PostToolUseFailure`. **Permission denials fire `PreToolUse`** but not this event."*

Two rules, opposite directions:

| Rejection | Upstream fires `PreToolUse`? | agentao today |
|---|---|---|
| Unknown tool name | **no** | **conformant, incidentally** — `ToolPlanner` appends an error result and `continue`s before a plan exists (`tool_planning.py:438-451`), so no plan reaches phase 1.5 |
| Input fails schema / tool-specific validation | **no** | **no check exists to fire the rule** — see below. agentao validates no tool input before execution, but it is not for lack of a schema |
| Permission denial | **yes** | **not conformant** — `_apply_pre_tool_use_hooks` skips any plan whose decision is not `ALLOW`/`ASK` (`tool_runner.py:277-279`, *"An already-DENY plan can't be made 'more denied'; skip the fork"*) |

The skip is a sound optimization while a hook can only *tighten* a verdict: if the call is already
denied, nothing a hook says changes the outcome, so why fork. It stops being sound the moment the contract says the hook must **observe** the call. An audit hook, a notifier, or a
metrics hook registered on `PreToolUse` never sees denied calls — which is precisely the population
such a hook exists for — and nothing tells its author. It is the same defect as §2.5's short-circuit,
one layer down: reasoning about the verdict and forgetting the side effects.

**In `claude-code` mode the skip is removed; `agentao-v1` keeps it** (today's behavior, frozen — §3).
The cost is stated rather than discovered: every denied tool call now forks a hook process, so a
session that denies many calls pays for it, and the tier-1 output budget (§6) applies to those runs
too.

**Step 2 needs a validator that does not exist, and a schema that does.** "Invalid ⇒ no hook" plus
"nothing to validate against today" is a rule with no way to fire, and the second half is simply
false: `Tool.parameters` is an abstract property returning a JSON Schema (`tools/base.py:106-109`)
and every registered tool has one — it is what the registry converts into the provider's
function-calling schema. What is missing is the *check*: `ToolPlanner.plan()` resolves
the tool (`tool_planning.py:436-437`) and goes straight to `_decide` (`:453`).

So G8 owes a **pre-hook validator**, and three things about it:

- **It runs after argument repair, not before.** The planner already repairs malformed arguments
  (`tool_planning.py:426-434`) and repairs tool names (`:438-451`); validating the pre-repair text
  would reject calls agentao successfully fixes today. Validation is the last word after repair, and a
  failure is a rejection rather than another repair attempt.
- **It is a behavior change for calls that today reach the tool.** A tool whose `execute()` tolerates
  a missing optional or a loose type currently runs; under a strict validator it stops. That is the
  point on the conformance side (upstream returns `tool_use_error` before hooks run) and it is a real
  regression surface, so the validator lands with the lifecycle in step 6 and not silently earlier.
- **Its dependency is a decision, and the obvious fallback is refuted.** `jsonschema` 4.26.0 is
  already in `uv.lock` as a *transitive* pin, so promoting it to a direct dependency is a supply-chain
  call. The tempting alternative — "validate the subset agentao's own schemas actually use (`type`,
  `required`, `enum`, `properties`)" — is already too small for the tools in tree.
  `todo.py:33-52` nests `array → items → object` with its own `properties` and an `enum` two levels
  down, and **MCP tools pass a third party's schema through verbatim** (`mcp/tool.py:72-80`) — agentao
  cannot bound what those contain. A partial validator would accept invalid nested input and report
  success, which is worse than not validating. **G8 takes a real validator or drops step 2.**
- **Tool-specific validation needs an interface that does not exist.** §12 asks for a test where
  input failing a *tool-specific* check fires no hook, and there is nothing to fail it with: `Tool`
  declares `parameters` (`tools/base.py:106-109`) and no validation entry point, so today a tool's own
  argument checking happens inside `execute()` — after the hook, and after the permission verdict. The
  interface has to be **pure**: an optional `preflight(args) -> str | None` that returns a message and
  performs no side effects, defaulting to `None` so existing tools are unaffected. G8 either adds it or
  narrows the lifecycle promise to schema validation alone and deletes that test — what it may not do
  is keep the promise and the test with no way to satisfy either.

So the full order. Steps 1–3 and 10 are the half that is easy to omit, because they are about when
the hook fires rather than what it returns:

| # | Step | Rule |
|---|---|---|
| 1 | Resolve the tool | unknown ⇒ error result, **no hook** |
| 2 | Validate the original input **against the tool's own JSON Schema** | invalid ⇒ `tool_use_error`-shaped result, **no hook**, no execution |
| 3 | Compute the engine verdict | `_decide` (`tool_planning.py:453`) |
| 4 | **Dispatch `PreToolUse`** | **regardless of the verdict**, including `DENY` |
| 5 | Aggregate rewrites | §4.4's conflict rule, below |
| 6 | Validate the rewritten input (agentao's own step — no upstream basis, above) | invalid ⇒ **deny the call**, user notice. **Not** "keep the original": that executes the input the hook was trying to replace. **G8** may flip it after a probe |
| 7 | **Re-decide** on the rewritten input | the verdict must describe what will run |
| 8 | Intersect | stricter of {re-decided, hook's own}; a pre-existing `DENY` stays `DENY` |
| 9 | Confirm | on the **modified** input |
| 10 | Execute | hooks are not re-dispatched |

Step 8 carries the asymmetry that makes the old skip look safe: the hook is *consulted* on a denied
call and still cannot lift the denial. Observation and authority are separate — conflating them is
what produces the skip.

The rest of this section is step 5–9, and the reason they cannot be a field plus a sink: it is about
**when** agentao decides.

`ToolPlanner.plan()` computes the permission verdict for every call and stores it on the plan
(`tool_planning.py:453` → `_decide`). `ToolRunner` dispatches `PreToolUse` hooks *afterwards*, in
phase 1.5 (`tool_runner.py:194-203`). That ordering is deliberate and, today, safe — because a hook
can move the verdict in exactly one direction. `_apply_pre_tool_use_hooks`
(`tool_runner.py:255`) denies or downgrades to ask, and the comment at its call site records the
asymmetry: a hook `allow` "is a no-op — it never downgrades an engine deny/ask or a tool's own
`requires_confirmation` ask" (`tool_runner.py:200`). Nothing a
hook returns changes the arguments the verdict was computed on.

`updatedInput` changes exactly that, and the reference is explicit about its scope: *"Modifies the
tool's input parameters before execution. **Replaces the entire input object**, so include unchanged
fields alongside modified ones."* A hook that rewrites a benign `Bash` command into `rm -rf /` would
hand the executor a command the permission engine never saw, carrying an `ALLOW` computed on the
original — and the hardline shell scanner (`permissions_hardline/`) runs *inside* that verdict, not
downstream of it. Planning the field as storage plus a sink ships that hole.

Upstream does not have it, and says so twice:

- on `permissionDecision` — *"Deny and ask rules are still evaluated regardless of what the hook
  returns"*;
- on the sibling `PermissionRequest.updatedInput` — *"The modified input is re-evaluated against deny
  and ask rules"*.

**A third quote stood here for four revisions and does not exist.** It read *"Claude Code validates
the updated input against the tool schema and rejects it if it doesn't match, showing an error in the
transcript"* — `grep -c "validates the updated input"` against the snapshot §3 stamps returns **0**.
It came from a *summarizing* fetch made before the raw `.md` was archived. Recorded here rather than
quietly deleted, because it is re-derivable: the sentence is exactly what a reader expects the page to
say. Two things follow from its absence — the **validation step** (step 2 below) has no upstream basis
and is agentao's own choice, labelled as such; and its **failure branch**, which the invented sentence
supplied, has to be decided rather than quoted.

The nearest thing the snapshot actually says is on the **output** side, and it is not a substitute:
*"For built-in tools, a value that doesn't match the tool's output schema is ignored and the original
output is used. MCP tool output is passed through without schema validation."* That is `updatedToolOutput`,
scoped to built-in tools, on a channel where "the original" is a result that already exists. Reading
it across to inputs would mean **running the very command the hook tried to replace**, which is why
the plan does not.

So `updatedInput` is not a field to carry; it is a re-entry, and the plan owes the sequence. This is
**gate G8**, blocking step 6 — and G8 now covers the whole ten-step order above, not only the rewrite:

1. **Aggregate.** Rewrites from all matching handlers merge under §2.5's order-independent rules,
   tie-broken in declaration order. Two handlers rewriting the same call differently is a conflict to
   name now, not to discover later: the plan proposes **denying the call** with a diagnostic, because
   a silently discarded rewrite is a hook that believes it sanitized something. The alternatives —
   last-in-declaration-order wins, or first wins — are cheaper and both leave that belief intact.
2. **Validate** the merged input against the tool's parameter schema. On mismatch: **deny the call**
   and emit a `hook error` user notice naming the validation failure. **Not** "reject the rewrite,
   keep the original input" — that was this plan's rule for four revisions on the strength of the
   sentence above, and it is unsafe independently of its provenance. A hook rewrites an input for a
   reason; if the rewrite is unusable, the two available outcomes are *run what the hook
   rejected* or *run nothing*, and only the second is defensible by the same argument this section
   already makes about conflicting rewrites. **G8** owns the flip: probe what Claude Code actually
   does, and if it turns out to fall back to the original, adopt that as a **documented profile
   deviation from safety**, not as a silent default. Either way, never execute a shape the tool did
   not declare.
3. **Re-decide.** Run `_decide` again on the rewritten arguments. This is the point of the gate — the
   verdict must be a function of what will actually execute.
4. **Intersect, never upgrade.** The re-decided verdict and the hook's own `permissionDecision`
   combine by taking the **stricter**. A hook `allow` cannot lift a re-computed `DENY`; and per
   `CLAUDE.md`'s three-tier precedence, nothing lifts the read-only mode preset, which short-circuits
   before the engine is consulted at all.
5. **Re-confirm on the modified input.** Phase 2's prompt must show what will run — which is the
   reference's own pairing: *"Combine with `\"allow\"` to auto-approve, or `\"ask\"` to show the
   modified input to the user."* A confirmation dialog showing pre-rewrite arguments collects consent
   for something else.
6. **Do not re-dispatch.** A rewritten input does not re-enter `PreToolUse`. Hooks see a call once.

Two neighbours inherit from this. `updatedToolOutput` (G2) needs step 2's validation and has no
schema to validate against — see §5.3's `tool_response` row. And the reference's v2.1.199 rule (§3)
is the same shape one level up: there are tool calls a hook `allow` may not auto-approve at all.

---

## 5. Two tables

### 5.1 The capability table — and the output profile it declares

`event × field-or-exit-code → accept | ignore | reject | block | feedback`, beside
`SUPPORTED_HOOK_TYPES_BY_EVENT` (`models.py:217`) — already an event×capability table, and already
carrying the right discipline in its docstring: a rule that parses as supported but is silently
dropped at dispatch must **surface as a parser warning**. New table, same rule.

Both peers are structured this way — the reference as "universal fields + per-event exceptions", and
codex's own reference likewise. Comparison §9.2 records this as a standing method lesson: the global
table is not the contract, the per-event section is.

**The sweep.** This design spent seven revisions adding fields one review at a time, which is how §1
came to promise a contract nobody had enumerated. Below is the *whole* output surface the reference
defines for agentao's eight events, swept once. Nine rows arrived in that sweep and only four had been
named by anyone — the difference is the argument for sweeping rather than patching, and the reason a
new field goes in this table before it goes anywhere else.

| Field | Events | Upstream meaning | Profile-1 |
|---|---|---|---|
| `continue` / `stopReason` | universal, **with per-event exceptions** | stop processing; message to the user | **accept where the event honors it** — see the matrix below |
| `systemMessage` | universal, **with per-event exceptions** | warning **to the user** | **accept where the event honors it** → `user_notices` (§4.3); the per-event exception is easy to miss on this field, since the reference states it only inside two events' sections |
| `suppressOutput` | universal | documented **inert** | **ignore** in profile-1; live in `agentao-v1` (§11 q1) |
| `terminalSequence` | universal | an OSC/BEL sequence Claude Code emits for the hook — restricted to OSC `0`/`1`/`2`/`9`/`99`/`777` and BEL, ignored if anything else appears | `ignore` — agentao's CLI has no hook-owned terminal-write path, and the allowlist is a security boundary this plan will not implement blind. Listed, not silent; **G7** may flip it to accept, since the transport is the same one `user_notices` needs (G1) |
| `hookSpecificOutput.hookEventName` | wherever `hSO` is used | *"It requires a `hookEventName` field set to the event name"* — the **discriminator** of the whole nested object | **accept, and it is the one output field whose *value* can legitimately fail validation.** Absent or mismatched ⇒ `schema_invalid` for the **whole object**, top-level fields included. "The top-level fields still apply" is the tempting softening and it contradicts both the resolver (`parse_stdout` returns `parsed=None` outside `valid`, and `absorb_channels` runs only on `valid` — §4.2) and the reference, which validates the object as a unit (*"a parsed object that fails schema validation"*). A partial-validity state is a coherent alternative, but it is a **profile deviation** needing its own row here. Omitting this field from a sweep leaves the parser reading an `hSO` block addressed to another event |
| `hSO.additionalContext` | 6 of 8 | context for the model | **accept** (§4.1) |
| `decision` / `reason` | UPS, PostToolUse, Stop, PreCompact, **PostToolUseFailure** | block + reason | **accept** on all five — but `"block"` does not mean *stop* on either Post* event. `PostToolUse`: *"adds the `reason` next to the tool result. Claude still sees the original output"* (`hooks.md:1933`). `PostToolUseFailure`: **measured** the same shape — reason to the model, original error preserved, turn continues (`docs/reference/hooks-probe-2.1.251.md` §C). Both are feedback; only `continue:false` stops |
| `permissionDecision` / `permissionDecisionReason` | PreToolUse | allow/deny/ask/defer | **accept** as a field; the **values** carry their own dispositions (§1): `allow` / `deny` / `ask` accept, **`defer` degrades to `deny`** with a `permissionDecisionReason` naming the unimplemented value, plus one diagnostic per (rule, field). Not "rejected with a reason", which §1's third rule forbids — and which no runtime could honor anyway: upstream `defer` *"exits gracefully so the tool can be resumed later"*, a resumption lifecycle (the session waits on disk, the hook may defer again) with no counterpart here — agentao has nowhere to park a pending call and no `tool_deferred` result. `deny` is the conservative degradation: the tool does not run, and the model is told why. The alternative is `ask`, which is closer in spirit and unavailable in non-interactive runs; **G7** picks. Degradation happens in `resolve()`, so the §5.4 lattice only ever sees `allow` / `deny` / `ask` |
| `updatedInput` | PreToolUse | replaces the whole input object | **accept**, through §4.4's re-entry (G8) |
| `updatedToolOutput` | PostToolUse | replaces the tool result; *"must match the tool's output shape"* | **accept, pending G2** — agentao has no tool output schemas (§5.3) |
| `updatedMCPToolOutput` | PostToolUse | MCP-only variant; the reference itself says prefer `updatedToolOutput` | `ignore` — a second spelling of a field whose first spelling is already gated on G2 |
| `classifierContext` | PostToolUse | a note for the **auto-mode classifier**, not for the model; capped at 2,000 chars per call across all hooks; v2.1.236+ | `ignore` — agentao has no auto-mode classifier, so there is no consumer to route it to. This is the row that shows the profile working: the field is legal, unimplementable here, and must not become a `hook error` |
| `sessionTitle` | SessionStart, UserPromptSubmit | sets the session title, as `/rename` | `ignore` for now — agentao sessions have ids, not titles (`embedding/sessions.py`); a title field is a product decision, not a conformance fix. **G7** records it |
| `reloadSkills` | SessionStart | re-scan skill directories after SessionStart hooks finish | **`ignore` in profile-1.** `accept` looks right — there is a `SkillManager` and a `reload_skills()` — and it rests on two false premises. (1) **The sink is not equivalent.** `SkillManager` scans `~/.agentao/skills`, `<cwd>/.agentao/skills` and the bundled tree (`skills/manager.py:25,35,110`); a Claude-authored hook installs into `.claude/skills` and command directories, so accepting the field would rescan a tree the hook never wrote to and report success — a silent semantic divergence, which is what §1 exists to prevent. (2) **There is no lock on that path.** `reload_skills()` (`:480`) takes none; the `filelock` CLAUDE.md refers to lives in `skills/registry.py:66-75`, a different component, so a rescan concurrent with a hook still writing files has no defined outcome. The diagnostic names the directory mismatch so the author is not left guessing. **G7** owns the two ways forward: teach discovery the `.claude/skills` tree and then accept, or accept-and-document the different tree |
| `initialUserMessage` | SessionStart | becomes the session's first turn under `-p` | `ignore` in profile-1; it would have to inject a turn into `agentao run` before the spec's prompt, which is a `run.py` pipeline change with its own ordering questions (§5.2, and it lands next to G1's problem) |
| `watchPaths` | SessionStart | paths to watch for `FileChanged` | `ignore` — `FileChanged` is not one of agentao's eight events, so accepting the field would arm nothing. **Not "reject at parse"** (§1's third rule): it is a stdout field the configuration parser never sees, and dropping the result would take the `systemMessage` beside it |
| `suppressOriginalPrompt` | UserPromptSubmit | omit the original prompt from the block message | **`ignore` in profile-1.** The case for accepting it — "otherwise we leak a prompt the author asked to hide" — misses that **agentao's block message never contains the prompt**: it is `f"[Blocked by hook] {blocking_error}"` (`_hook_dispatch.py:73`). For `true` the observable outcome already matches; only `false` differs, and only against a message that does not exist. Honoring the field would first require *adding* the prompt to the block message so the flag has something to suppress — a user-visible change to today's output, made in order to support a flag. **G7** may take that route; profile-1 does not. Note the trap this closes: the obvious test ("assert the prompt is absent when `true`") passes today **without any parsing at all** |

**"Universal" is not universal.** The reference says so in the same breath as introducing the fields:
*"Every event accepts them, but some events discard them or deliver `systemMessage` somewhere other
than the transcript. Each event's section says so."* Two of agentao's eight are named exceptions, so a
flat `accept` on these fields sends a `PreCompact` hook's `systemMessage` to a user the reference says
never sees it. A predicate named in prose is not a mechanism — this is the table:

| Event | `continue` | `stopReason` | `systemMessage` | `suppressOutput` | `terminalSequence` |
|---|---|---|---|---|---|
| `SessionStart` | **discarded — measured** (below) | **discarded** | honored | n/a — ignored | n/a — ignored |
| `UserPromptSubmit` | honored | honored | honored | n/a — ignored | n/a — ignored |
| `PreToolUse` | honored | honored | honored | n/a — ignored | n/a — ignored |
| `PostToolUse` | honored | honored | honored | n/a — ignored | n/a — ignored |
| `PostToolUseFailure` | honored | honored | honored | n/a — ignored | n/a — ignored |
| `Stop` | honored | honored | honored | n/a — ignored (live in `agentao-v1` — §11 q1) | n/a — ignored |
| `PreCompact` | **discarded** | **discarded** | **discarded** | n/a — ignored | n/a — ignored |
| `SessionEnd` | **discarded** | **discarded** | **discarded** | n/a — ignored | n/a — ignored |

*"Claude Code discards a PreCompact hook's `systemMessage` and `continue` fields"*; `SessionEnd`
*"hooks have no decision control … Claude Code discards their JSON output fields, such as
`systemMessage`"*.

**`SessionStart` is the third contested row, and finding it corrected a rule this section states.**
Five of the six remaining events carry no exception statement, so they inherit the global row.
`SessionStart` carries none either — but it is named in a **third** global table that neither the
universal-field rule nor its own section points at:

> *"| SessionStart, SubagentStart | **Context only** | `hookSpecificOutput.additionalContext` adds
> context for Claude. SessionStart also accepts `initialUserMessage`, `watchPaths`, `sessionTitle`,
> and `reloadSkills`. **No blocking or decision control**"*  (`hooks.md:1009`)

That row is decisive against `continue` and silent about `systemMessage`, and the difference is in the
table's own taxonomy: `continue: false` **is** one of its decision patterns — the `TeammateIdle,
TaskCompleted` row's pattern is literally *"Exit code or `continue: false`"*, and `TaskCreated`'s says
*"`continue: false` is ignored"*. `systemMessage` never appears in that table at all; it is a user
notice, not decision control, and its exceptions live in the per-event sections (`hooks.md:717`).

Against that sits a real counter-signal, which is why the row is **contested** rather than settled:
**every** other event that discards `continue` says so in its own section, fifteen times, and that
includes events which *also* sit in this table's no-decision rows — `SessionEnd` (`:3029`), `Setup`
(`:1227`), `InstructionsLoaded` (`:1264`), `Notification` (`:2249`), `PostCompact` (`:2973`). Upstream
writes both sentences every time except here. So either `SessionStart` genuinely honors a stop nobody
documented, or its section is missing the sentence its fourteen neighbours have.

**Profile-1 takes the narrow reading — `continue:false` is `discarded` on `SessionStart`** — chosen
on §11 q9's asymmetric-cost argument (honoring an undocumented stop lets a hook refuse to start a
session upstream would have started; declining a stop nobody has asked for costs nothing) and since
**confirmed by measurement**: a `SessionStart` hook printing `{"continue": false, "stopReason": …}`
left the session started, the turn completed, and the reason nowhere in the output
(`docs/reference/hooks-probe-2.1.251.md` §B, §0). So the fifteen-times-elsewhere sentence is missing
from this event's section and the Decision-control row is what governs. The row is no longer
contested; what remains unmeasured is whether `systemMessage` is *also* discarded here, which the
probe's transport could not see — §5.1's matrix keeps it `honored` on the reference's own wording.

`discarded` is the exact word, and it settles a question the cell would otherwise leave open: **the
narrow branch is silent, not diagnosed.** A diagnostic belongs to the `ignore` axis, which reports an
*agentao* limitation; `continue` is `accept` in the field table and has consumers on five events, so
its absence here is a per-event delivery outcome and takes the delivery axis's silence rule. Being
*contested* does not change that — a diagnostic would fire for hooks that are correct under the
reading agentao did not take, which is the same "flagging correct code" the silence rule exists to
avoid. Whether a contested row should get its own one-shot notice anyway is a **G7** sub-question, not
a licence to mix the two axes here.

**A contested row owes a flip list.** Both contested rows are decided *against* the reference's wider
statement, and a probe can reverse either. Recording only "invert the test" is what let the last
revision's `SessionStart` route be planned and then deleted with nothing saying how to get it back. So
each contested row names, once, every section that changes if the probe goes the other way — for
`SessionStart` in **G7** below, for `PostToolUseFailure` in §5.4's lattice and §12.

**The method rule this cost.** Earlier revisions of this section said per-event silence *is*
inheritance for `continue`, because "the global table asserts the field applies". There are **three**
global tables — JSON output fields (`:904`), Decision control (`:1005`), and the per-event sections —
and that rule was derived from two of them. The corrected form: **an event inherits a universal field
only if no global table excludes it.** The Decision-control table excludes `SessionStart` from
decision control, and `continue` is one of the patterns it enumerates.

Two consequences for the code. `absorb_channels` must consult the table for `systemMessage` exactly as
`resolve()` already does for `continue` — one predicate each, `honors_continue(event)` and
`honors_system_message(event)`, both fed by the matrix above rather than by a literal in the
resolver. And `terminalSequence` is the one field whose upstream behavior *survives* a discarding
event (*"the field works on events that discard `systemMessage` and `continue`"*), which is worth
recording even though profile-1 ignores it: if G7 ever accepts it, it does **not** inherit these two
rows' exceptions.

`stopReason` has its own column because it is **accepted**, and it mirrors `continue` by
construction: it is the message a stop carries, so on an event that discards the stop there is
nothing left for it to qualify. A column rather than a footnote is what lets `resolve()` call
`honors_stop_reason(event)` instead of reaching for a literal — the same reason `systemMessage` has
its own predicate.

**Two axes, not four values.** The cells above carry four words (`honored`, `discarded`, and two
kinds of `n/a`) for a model §1 and this section both describe as having **two**. They are not four
dispositions; they are two axes, and the matrix only ever varies one of them:

- **Profile disposition** — `accept` or `ignore`, decided once in the field table above, never per
  event. An `ignore` produces one diagnostic per (rule, field) (§4.2). `suppressOutput` and
  `terminalSequence` are `ignore` in profile-1, which is why their two columns read `n/a`: the
  delivery axis never gets to run, and their diagnostic comes from the field table, not from here.
  (`suppressOutput` is *additionally* documented **inert** upstream — a fact about the reference, not
  a third disposition; it stays live in `agentao-v1`, §11 q1. `terminalSequence` is the row G7 may
  flip, and if it does it lands `honored` on **all eight** events, exceptions included.)
- **Delivery** — `honored` or `discarded`, and it applies **only to an accepted field**. This axis is
  what the matrix exists for.

**A discard is silent — no diagnostic.** The hook is upstream-conformant: the same output on Claude
Code does nothing either, so a diagnostic here would flag correct code. A one-shot registry (§4.2,
G10) survives exactly one thing badly, and that is being trained to be ignored. An `ignore` is the
opposite case — it reports an agentao limitation, so it is announced once. §12 pins **both**
directions, because "no diagnostic" is the assertion an implementation drifts out of without failing
anything.

The **field table**'s last column is not a ranking of importance: `accept` means a field with a
consumer, `ignore` means the field is parsed, has no effect, and produces one diagnostic per (rule,
field) (§4.2). Neither is ever a `hook error` shown to the user, because neither is the hook author's
mistake. `reject` lives in §2.4, where a rule can still be refused before anything runs.

**The one contested row.** `PostToolUseFailure`'s per-event section is real and lists
`additionalContext` and nothing else — but the **global** Decision-control table names the event
explicitly:

> *"UserPromptSubmit, UserPromptExpansion, PostToolUse, **PostToolUseFailure**, PostToolBatch, Stop,
> SubagentStop, ConfigChange, PreCompact | Top-level `decision` | `decision: "block"`, `reason`."*
> (`hooks.md:999`)

Two normative statements about the same field on the same page. **There is no third data point**, and
the one that looks like it is invalid: `PostToolUseFailure`'s exit-2 row (*"Shows stderr to Claude;
the tool already failed"*) reads as support for the narrow reading, but `PostToolUse`'s exit-2 row is
word-for-word the same shape (*"Shows stderr to Claude; the tool already ran"*, `hooks.md:854-855`)
and that event **does** support `decision:"block"`. Exit-2 stderr is an independent feedback channel
and carries no information about decision support in either direction — worth stating because it is
the first place a reader looks for a tie-break. This is the second self-contradiction the snapshot has
produced; the first was `sh -c` versus a `shell` default of `"bash"` (§2.4), withdrawn into G5 rather
than resolved from the document. Same answer here, and the standing method rule needs the qualifier
this case reveals:

> **The per-event section overrides the global table when it *says something different*. Silence in
> the per-event section is not an override.**

`PostToolUse`'s section explicitly narrows the global row; `PostToolUseFailure`'s simply omits the
field, which is equally consistent with "inherits the global row" and with "does not have it".

**And even the wide reading would not tell you what `block` *does* there — the global row fixes a
shape, not a semantics.** Its members' effects are mutually incompatible, verifiably: on
`UserPromptSubmit` a block *"blocks prompt processing and erases the prompt"*; on `Stop` it *"prevents
Claude from stopping, continues the conversation"*; on `PreCompact` it *"blocks compaction"*
(`hooks.md:845,847,866`); and on `PostToolUse` it *"adds the `reason` next to the tool result. Claude
still sees the original output"* (`:1933`) — annotate and continue. **Four of that row's nine events,
four incompatible outcomes** — and the other five are simply unverified here, which makes the point
stronger rather than weaker.
So membership tells you the wire form is a top-level `decision` / `reason` pair and **nothing** about
where the `reason` goes, whether the original failure survives, or whether the turn continues.
`PostToolUseFailure`'s own section defines `additionalContext` and no `decision` at all (`:2043-2046`),
so there is no second source to read the effect out of.

**G7 probed four things, not one, and all four came back** (`docs/reference/hooks-probe-2.1.251.md`
§C, §0): (1) a `decision` **is** accepted; (2) the `reason` reaches the **model**, on its own labelled
line; (3) the **original error is preserved** before it; (4) the **turn continues**. So the wide
reading of `hooks.md:999` is right for this event, and the narrow reading this plan held for seven
revisions is **reversed** — profile-1 honors the `decision`, as feedback.

**What the answers were not allowed to be, and the control that made them evidence.** (2)–(4) could
not be pre-filled from `PostToolUse`, so they were measured; that they came back the *same* as
`PostToolUse`'s is a result, not the assumption the flip list forbade. And a control run of the same
event with an unrecognized key showed it reaching the model **zero** times, which is what separates
"the field is honored" from "hook stdout is echoed at the model" — without it the finding would have
measured the wrong mechanism.

**The one thing that was never in tension.** The event's *own* exit-2 row says it **cannot block** —
*"Shows stderr to Claude; the tool already failed"*, in a table framed around events that *"represent
things that already happened or can't be prevented"* (`:838,855`). The measurement agrees with it:
nothing is prevented, because a `block` here annotates rather than stops. Reading that row as an
answer to (1) would still have been the inference §5.1 withdrew once — it constrains the *effect*, not
the acceptance.

§4.1's `BlockDecision` keeps listing `PostToolUseFailure`, now for a decision the profile **does**
honor; the parse-wider-than-disposition split still earns its keep on `defer`.

**Every `accept` in this table owes three things**: a field on `ParsedHookOutput` (§4.1), a row in the
consumer table (§5.2), and an aggregation rule for when several handlers set it. This plan has shipped
an `accept` with none of the three, and another row left at "recommended", which is not a disposition
at all. Treat the triple as a checklist whenever a row moves from `ignore` to `accept` — and re-run it
whenever a field's *status* changes at all, which is how §5.2.2's gap survived a revision. Which,
per §11 q4, is itself a profile bump.

### 5.2 The event × output → runtime consumer table

A capability table says what is *accepted*. It does not create a place for the value to go. Three of
the eight events run through `_dispatch_lifecycle` (`_dispatcher.py:267`), which is side-effect only
and returns attachments — there is no result object, so there is nothing to consume. All three are
easy to miss, because the dispatcher call sites look complete.

| Event | Output the reference defines | Sink today | Needed |
|---|---|---|---|
| `SessionStart` | plain stdout (**exit 0 only**) and `hSO.additionalContext` → model context; exit-2 stderr → **user**; `continue:false` **not honored** (§5.1 — `hooks.md:1009`); `initialUserMessage`, `sessionTitle`, `watchPaths`, `reloadSkills` all **ignored** in profile-1 (§5.1) | **none** — `_dispatch_lifecycle`, and `cli/session.py:81` discards the dispatcher's return value | model-context injection **and** a user-notice sink. `_dispatch_lifecycle` returns attachments only (`_dispatcher.py:66,267-288`), so consuming either still needs a return value — but **no control result**: profile-1 does not honor a stop here. No rescan sink: `reloadSkills` is ignored, so nothing routes to `SkillManager` in profile-1 |
| `SessionEnd` | JSON output discarded (**agentao is conformant here**) — but exit-2 stderr → **user** | **none**, on both surfaces — `cli/session.py:87` discards the return value, and `agentao run` has already emitted its result before the dispatch (`run.py:814,815`) | a user-notice sink **with a route on each surface** (§5.2.1). "Nothing needed, already conformant" is true of the *JSON* half only |
| `UserPromptSubmit` | `decision:"block"`+`reason`, `hSO.additionalContext`, `continue`, exit 2; `suppressOriginalPrompt` **ignored** in profile-1 (§5.1) | partial (`_hook_dispatch.py`) | wire the three missing channels. **No route for `suppressOriginalPrompt`** — parsed and diagnosed, nothing consumes it, because agentao's block message never contains the prompt for it to suppress. a row demanding a route for an `ignore`d field is the drift to watch for here |
| `PreToolUse` | `permissionDecision`, `updatedInput`, `hSO.additionalContext`, **`continue:false`** | decision yes; context parsed then logged | `tool_contexts` sink; `updated_tool_input` **plus the re-decide sequence** — the sink is the small half (§4.4, G8). And the turn-level stop route (§5.2.2), which is **not** the permission verdict: `continue:false` ends the turn, `deny` blocks one call |
| `PostToolUse` | `decision:"block"`+`reason` (**feedback, not a stop** — below), `hSO.additionalContext`, `updatedToolOutput`, exit 2 → feedback, **`continue:false`** (a real stop) | **none** (`_dispatch_lifecycle`, `_dispatcher.py:120`) | result object + tool-result splice — and a decision about what `updatedToolOutput` replaces, since agentao's tool output is a string with no schema (§5.3). **Two different sinks, not one:** `decision:"block"` appends `reason` beside the preserved result and the turn continues; `continue:false` ends the turn (§5.2.2) |
| `PostToolUseFailure` | `hSO.additionalContext`, exit-2 stderr → model, **`continue:false`**, and **`decision:"block"` → model feedback** (measured) | **none** (`_dispatch_lifecycle`) | result object + model feedback; it carries a turn-level `Stop` **unconditionally** (the universal row, §5.1). The `decision` sink is specified by measurement rather than inherited from `PostToolUse`: the `reason` reaches the **model** on its own line, the **original error is preserved** before it, and the **turn continues** (`docs/reference/hooks-probe-2.1.251.md` §C) |
| `Stop` | `decision`, `hSO.additionalContext`, `continue`, exit 2 | mostly present | `user_notices` consumer; continuation from `hSO` |
| `PreCompact` | exit 2, top-level `decision:"block"` | agentao's own spelling | the reference spellings (§3.3) |

`SessionEnd` is the row worth reading twice. Its *JSON* half is genuinely
conformant — the reference gives the event no decision control and discards its JSON output, so
agentao's side-effect-only path is right, and "give every lifecycle event a result object" would be
wrong here. But exit 2 is a separate channel from JSON, and on `SessionEnd` it means *stderr shown to
the user* (§4.2). agentao has no sink for that: both `dispatch_plugin_session_start` and
`dispatch_plugin_session_end` throw the dispatcher's return value away inside a bare
`try/except: pass` (`cli/session.py:81,87`), so nothing downstream could consume it even if the
dispatcher produced it.

§5.3 is the input-side twin of this table.

The plan's position: the existing `logger.warning` is **not** the user channel — it is not a surface
the user sees in a normal session, and treating a log line as a contract sink is how the
`systemMessage` mis-routing happened in the first place (§4.3). A real sink is needed, it is the same
one `user_notices` needs, and it therefore belongs to **G1** and lands in step 4 alongside the other
lifecycle sinks.

#### 5.2.1 A sink is not a route

G1 decides the *transport* — a new event type, or an extended `PLUGIN_HOOK_FIRED` payload. That is
necessary and not sufficient, because on the one surface where hook notices matter
most there is nothing left to carry them by the time `SessionEnd` fires:

```
run.py:770   agent.remove_event_observer(_on_event)   # observers detached
run.py:771   transport_unsubscribe()
   …
run.py:814   _emit(result, output_format)             # the run's entire output is written HERE
run.py:815   dispatch_plugin_session_end(...)         # …and only now does SessionEnd run
```

An event-based transport is dead on arrival (nothing is subscribed); a return-value transport arrives
after the only thing that prints. So a `SessionEnd` hook's exit-2 stderr — which §4.2 routes to the
user — reaches a `agentao run` user through no path at all. The interactive surface has the same
shape one step earlier: `dispatch_plugin_session_end` throws the return value away inside a bare
`try/except: pass` (`cli/session.py:87`).

**G1 therefore owes a route per surface, not just a shape**, and the reference supplies the wire form
for the headless one: `systemMessage` *"can arrive as an `SDKInformationalMessage`"* under
`--output-format stream-json`. The plan's proposal:

- **`agentao run`:** dispatch `SessionEnd` **before** `_emit`, and carry its notices on `RunResult` —
  `warnings[]` already exists and is already serialized (`run.py:812`), which makes this a two-line
  reordering plus a field, testable at `_run_pipeline` level. The session is ending either way; what
  moves is only whether the notice makes the same output as the result it belongs to.
- **Interactive CLI:** consume the dispatcher's return value at `cli/session.py:87` and render through
  whatever G1 picks for `user_notices` generally.
- Either way the test is end-to-end (§12), because a resolver-level test passes while the feature does
  not exist.

The same ordering question governs `initialUserMessage` (§5.1), which must land *before* the first
turn is built rather than after — the mirror image of this defect on the `SessionStart` side.

#### 5.2.2 Stopping is a route too

§5.1's matrix says **five** of the eight events honor `continue: false`. The table above routes it on
two — `UserPromptSubmit` and `Stop`. The gap survived a revision of this plan, and the reason is worth
recording: the field and the aggregation rule were both in place (`continue_processing` /
`stop_reason` on `ParsedHookOutput`, §4.1; rank 1 of §5.4's lattice), so §5.1's "every `accept` owes
three things" checklist looked satisfied to anyone who did not re-run it after the field moved from
prose into a table. The consumer was missing on three events.

**The mechanical half.** `dispatch_post_tool_use` and `dispatch_post_tool_use_failure` return
`list[HookAttachmentRecord]` out of `_dispatch_lifecycle` (`_dispatcher.py:126,134,267-288`), so a
`ResolvedHookOutput.control = Stop(reason)` computed by `resolve()` is dropped at the call site.
`PreToolUse` is the third and it is different: `dispatch_pre_tool_use_decision` **does** return a
result object (`PreToolUseHookResult`), so what is missing there is a control *arm*, not a type.
(`SessionStart` was in this list until profile-1 took the narrow reading of `hooks.md:1009` — §5.1. It
still needs its return value consumed, for the exit-2 user notice and for `additionalContext`, but not
for a control.)

**The semantic half**, which no gate can leave to the implementer, because *"stops processing
entirely"* means something different at each point in a session:

| Event | What `continue:false` stops | Surface behavior |
|---|---|---|
| `UserPromptSubmit` | the turn, before the first model call | already step 5's channel; the existing early-return path is the shape (`_hook_dispatch.py:75`) |
| `PreToolUse` | the whole turn, **not** just the call | this is the one that can be silently mis-implemented as a `deny` — see below |
| `PostToolUse`, `PostToolUseFailure` | the turn, after the tool result is recorded | the tool has already run, so this is a stop, not a rollback — and it has three call frames to cross before anything can act on it |
| `Stop` | the turn | present today (`_runner.py:964-981`) |
| `SessionStart`, `PreCompact`, `SessionEnd` | nothing — not honored / discarded (§5.1) | no route, and that is conformance rather than a gap |

**A stop is not a deny.** On `PreToolUse` they are different arms of §4.1's `control` union with
different outcomes for the user — one ends the turn, the other blocks a call and lets the model try
something else — so folding `continue:false` into the permission verdict because a verdict field
happens to be there is exactly the kind of semantic divergence §1 exists to prevent.

**And a `PostToolUse` `decision:"block"` is not a stop either.** The reference: *"`"block"` adds the
`reason` next to the tool result. Claude still sees the original output; to replace it, use
`updatedToolOutput`"* (`hooks.md:1933`). It is a **feedback** channel — the original result is
preserved, the reason rides beside it, and the turn continues to the next model call. Only
`continue:false` ends the turn. The two therefore need two sinks and two tests (§12), and the word
"block" is the trap: it reads as *prevent* and means *annotate*.

##### The `PostToolUse` stop has three frames to cross — and an invariant it must not break

This is the part a "give the event a result type" gate does not cover. `PostToolUse` and
`PostToolUseFailure` hooks fire **inside a tool worker**: `execute_batch` runs plans on an 8-worker
pool (`tool_executor.py:189`) and each worker dispatches its own hooks (`:462`). Above that,
`ToolRunner.execute` returns `(doom_triggered: bool, result_messages: list)` and nothing else
(`tool_runner.py:238,249`), and the chat loop reads exactly those two values (`_runner.py:773`). A
`Stop` produced in a worker has three frames to climb and no channel in any of them.

So **G2** owes four decisions, not one result type:

1. **The aggregation path.** `Stop` on `ToolExecutionResult` → collected by `execute_batch` →
   surfaced by `ToolRunner.execute` (a third return value, or a result object replacing the tuple) →
   acted on by the chat loop beside `doom_triggered`.
2. **Sibling calls in the same batch — and the state they are actually in.** Only the *firing* tool
   has run. `execute_batch` submits **every** plan up front to an 8-worker pool and each worker
   dispatches its own hooks the moment its own tool returns (`tool_executor.py:189-200,462-470`), so
   when one `PostToolUse` hook runs, up to seven siblings are mid-execution and, past eight calls,
   others are still **queued**. "The tools have already run" is true of one of them and was the wrong
   basis for a policy. The plan still proposes **let the batch finish, then stop**, on the basis that
   survives: it is the only option that preserves the invariant below with no new machinery. The
   alternative — cancel queued siblings and interrupt running ones — needs cancellation plumbing *and*
   synthesized results for every plan it cancels. **Which one upstream does is unknown**, so G2 either
   probes it or declares the choice a documented profile deviation; what it may not do is present the
   cheap option as forced.
3. **The invariant neither option may break.** `format_batch` emits exactly one tool message per
   plan and indexes `exec_results[plan.tool_call_id]` directly (`tool_result_formatter.py:113-128`).
   A plan without a result entry is a `KeyError`, and a plan without a message is an assistant
   message carrying `tool_calls` with no answering `role:"tool"` — which strict APIs reject. **Every
   plan still produces a result and a message, stop or no stop.**
4. **Which `stopReason` is surfaced** when several tools stop at once: **plan order** — the order in
   `_plans`, which is the model's own tool-call order — never completion order, matching §5.4's
   declaration-order tie-break and §2.5's determinism rule.

**The headless exit code** is a decision, not a detail: `agentao run` publishes a fixed table (`0` ok,
`1` runtime, `2` usage, `3` permission/interaction, `4` max iterations, `130` interrupted —
`CLAUDE.md`, "Running"), every entry of which some CI script already branches on. A hook-initiated
stop mid-turn ends the turn like any other early return, so it maps through the ordinary turn-outcome
path rather than needing a code of its own; **G2** confirms that, and it is the one part of this
section that got simpler when `SessionStart` left it.

### 5.3 The input field matrix

Deviation 1 (§7) is one cell in step 3: "Claude input serialization". Behind it sits the comparison's
three-layer finding — envelope 6/8, event-specific fields 7/8, common fields 8/8, and **no event
conformant end to end** (`hooks-three-way-claude-codex-agentao.md` §5.9). Flattening the envelope and
renaming keys is the easy layer, and the only one that is cheap to cost. The hard layer is that
several fields have **no value to serialize**, and a plan cannot promise a payload it cannot fill.

**The matrix is per event, not per field**, and every cell is one of **required** / **conditional** /
**forbidden**. A field-oriented table with an "Events" column hides two things: an events list that is
missing one (the reference's `Stop` input carries `permission_mode`, which is exactly where agentao
hardcodes the out-of-enum `"workspace-write"`, `_payload.py:144`), and *forbidden* itself, which a
per-field list cannot express at all — agentao ships fields upstream does not define on two events.

**Common fields.** ✓ = the reference's example for that event carries it; — = it does not.

| Event | `session_id` | `transcript_path` | `cwd` | `hook_event_name` | `permission_mode` | `prompt_id` | `effort` | `agent_id` | `agent_type` |
|---|---|---|---|---|---|---|---|---|---|
| `SessionStart` | ✓ | ✓ | ✓ | ✓ | — | cond. | — | **forbidden** | **forbidden** |
| `SessionEnd` | ✓ | ✓ | ✓ | ✓ | — | cond. | — | **forbidden** | **forbidden** |
| `UserPromptSubmit` | ✓ | ✓ | ✓ | ✓ | ✓ | cond. | — | **forbidden** | **forbidden** |
| `PreToolUse` | ✓ | ✓ | ✓ | ✓ | ✓ | cond. | cond. | **forbidden** | **forbidden** |
| `PostToolUse` | ✓ | ✓ | ✓ | ✓ | ✓ | cond. | cond. | **forbidden** | **forbidden** |
| `PostToolUseFailure` | ✓ | ✓ | ✓ | ✓ | ✓ | cond. | cond. | **forbidden** | **forbidden** |
| `Stop` | ✓ | ✓ | ✓ | ✓ | **✓ — the row it is easiest to omit** | cond. | cond. | **forbidden** | **forbidden** |
| `PreCompact` | ✓ | ✓ | ✓ | ✓ | **— agentao sends it anyway** (`_payload.py:175`) | cond. | — | **forbidden** | **forbidden** |

`session_id`, `cwd` and `hook_event_name` are **required** everywhere and all three are in hand today
(`cwd` is simply missing on the three tool events; `hook_event_name` is the envelope's `event` key
renamed). The other two columns carry the whole problem:

- **`transcript_path` — required on all eight, and agentao has no value for it.** Hardcoded `None`
  (`_payload.py:142,173`). `.agentao/sessions/*.json` is written at save points, not continuously
  (`embedding/sessions.py`); `.agentao/replays/*.jsonl` exists only when replay is enabled. **G7
  decides:** a continuously-written transcript (a new component, with its own redaction question), or
  an explicit `null` documented in §1's profile. Never a path to a file whose contents lag the session
  — a hook reading a stale transcript is worse off than one reading `null` and branching.
- **`prompt_id` — conditional.** The reference: a UUID for the prompt being
  processed, *"Absent until the first user input"*, v2.1.196+, and deliberately equal to the
  OpenTelemetry `prompt.id` so hook output and telemetry correlate. agentao has a per-turn id
  (`agent._current_turn_id`, snapshotted at `TURN_BEGIN`) but it is a *turn* id, and the reference
  gives `turn_id` to a different event — reusing one for the other invents a correlation that does not
  hold. **G7:** mint a real prompt id, or omit the field. Absent-before-first-input is a real
  condition to test, not a footnote.
- **`effort` — conditional on two things at once.** *"Present for events that fire within a tool-use
  context, such as `PreToolUse`, `PostToolUse`, `Stop` … when the current model supports the effort
  parameter"*, shaped `{"level": "low"|"medium"|"high"|"xhigh"|"max"}`. agentao **has** a source:
  `/thinking` writes `reasoning_effort` into the live client's `extra_body`
  (`cli/commands/provider.py`). It also has an enum problem of the same family as `permission_mode`:
  agentao accepts `minimal` and `off`, neither of which exists upstream. **G7:** map what maps
  (`low`/`medium`/`high`), omit the field when the value is `minimal`/`off`/unset — never coerce
  `off` into a level, which would tell a hook that thinking is on.
- **`agent_id` and `agent_type` — both forbidden, for two different reasons, which is why they are two
  columns and not one.** Upstream separates them: `agent_id` is *"Present only when
  the hook fires inside a subagent call"*, while `agent_type` is *"Present when the session uses
  `--agent` **or** the hook fires inside a subagent"* — a main-thread session started with a named
  agent carries it. So the sub-agent argument below covers `agent_id` completely and `agent_type` only
  halfway. The other half: **agentao has no named-agent session mode at all** — no `--agent` flag on
  any entry point (`cli/entrypoints.py`, `cli/run.py`), and nothing sets a session-level agent name.
  Both fields are therefore forbidden, in the **common-fields** matrix, which is their single home —
  the event-specific table below lists neither, deliberately: one field in two tables is how an
  inverted annotation survives a revision — `agent_type` sat in that table's *forbidden* column under a
  header reading "agentao ships it today", the opposite of true for a field forbidden **because**
  agentao never sends it. If a named-agent mode ever ships,
  `agent_type` becomes conditional on the main thread **before** sub-agent hooks exist — the two move
  independently, which is why they are now two columns. They are present *"when the hook fires inside a subagent call"*. Chasing that
  turned up this: agentao sub-agents are built as a fresh `Agentao(...)` with **no `plugins=`**
  (`agents/tools/_wrapper.py:513`), and `_plugin_hook_rules` defaults to `[]` (`agent.py:532`) — so
  **no hook fires inside an agentao sub-agent at all**. The fields are unsourceable because the events
  never happen there. That is a scope decision, not a serialization one, and "§1's event list" does
  not cover it — that list scopes by event *name* and has no execution-context dimension. §1 carries
  one: **profile-1 is main-thread only.** Deviation 18 (§7) records the gap; if sub-agent hooks are
  ever wanted, plugin rules have to reach the sub-agent constructor first, and these two fields land
  with them, not before.
- **`permission_mode` — conditional, and wrong in three different ways at once.** agentao's vocabulary
  is `read-only` / `workspace-write` / `full-access` / `plan`; the reference's is `default` / `plan` /
  `acceptEdits` / `auto` / `dontAsk` / `bypassPermissions`. (i) On the five events that owe it, agentao
  supplies a value outside the enum, so a hook branching on the documented values matches no arm.
  (ii) On `Stop` the value is not even read from the session — `build_stop` defaults the parameter to
  `"workspace-write"` (`_payload.py:144`), a constant. (iii) On `PreCompact` the field is **forbidden**
  and agentao sends it. G7 pins a mapping (`plan`→`plan` is the only exact one; `full-access` is near
  `bypassPermissions`; `workspace-write` is **not** `acceptEdits`) or omits the field — and either way
  removes it from `PreCompact`.

**Event-specific fields.**

| Event | Required | Conditional | Forbidden | Source |
|---|---|---|---|---|
| `SessionStart` | `source` | `model`, `session_title` | — | `source` is derivable at both dispatch sites (`cli/session.py:104`, `cli/run.py:691`) — `startup`/`resume`/`clear`/`compact`/`fork` map onto distinct agentao commands. `model` from the LLM client |
| `SessionEnd` | `reason` | — | — | derivable at `cli/session.py:108`, `cli/run.py:815`; values `clear`/`resume`/`logout`/`prompt_input_exit`/`other`, with `other` the honest default |
| `UserPromptSubmit` | `prompt` | — | — | a rename of `userMessage` |
| `PreToolUse` | `tool_name`, `tool_input`, `tool_use_id` | — | — | `tool_use_id` **exists, unplumbed** — the normalized `plan.tool_call_id` (`tool_runner.py:160-188`) |
| `PostToolUse` | `tool_name`, `tool_input`, `tool_response`, `tool_use_id` | `duration_ms` | — | `duration_ms` **exists, unplumbed** (`tool_executor.py:426`). `tool_response` is the hard one: a **string** here (`_payload.py:100`) where upstream passes the tool's structured output object |
| `PostToolUseFailure` | `tool_name`, `tool_input`, `tool_use_id`, `error` | `is_interrupt`, `duration_ms` | — | `is_interrupt` derivable from the cancellation token |
| `Stop` | `stop_hook_active`, `last_assistant_message` | `background_tasks`, `session_crons` | **`turn_end_reason`** (`_payload.py:147`) | the two conditionals name features agentao does not have — omit, per §1's profile |
| `PreCompact` | `trigger`, `custom_instructions` | — | **`compaction_type`, `reason`** (`_payload.py:178-179`), plus `permission_mode` | agentao's own compaction vocabulary on a flat Claude-shaped payload |

**The forbidden column holds two different kinds of entry**, and conflating them in the header is the
mistake to avoid. Everything **bold** is a field agentao *sends today* that upstream does not define
for that event, with the `_payload.py` citation as proof; anything unbolded would be forbidden without
being shipped. As it stands this table contains only the first kind, and the common-fields matrix
carries the second. Three private fields ride on two flat Claude-shape payloads today. In `claude-code` mode they are **removed**; in `agentao-v1` they
stay. If any of them is genuinely wanted upstream-side, the input analogue of §3.3 applies — one
`agentao` sub-object, never a bare sibling of the documented keys. That is a G7 decision, and it is
the same shape as the output-side namespacing already decided.

**`tool_response` is still the row most likely to narrow the profile.** Upstream passes a structured
object (`{filePath, success}` for a write); agentao tools return `str` and declare no output schema.
Wrapping the string in an invented object is a third contract; emitting the string is a documented
type divergence. The same decision blocks `updatedToolOutput` (G2), whose reference semantics are
*"must match the tool's output shape"* — a shape agentao does not have.

**G7** closes: `transcript_path`, the `permission_mode` mapping, `tool_response`, the disposition of
the three private fields, whether an unsourced field is *absent* or explicitly `null`, and the two
§5.1 rows it inherits (`terminalSequence`, `sessionTitle`). It blocks step 3. The rule the matrix
enforces is one line: **a field agentao cannot source is absent or documented, never fabricated —
and a field upstream does not define is not sent at all.**

### 5.4 Mixed-contract dispatch

§2.5 gives `claude-code` rules all-start bounded concurrency and leaves `agentao-v1` rules serial with
short-circuit. §3 makes the contract **file-scoped**. Put those together and one event's rule list can
hold both kinds — `resolve_all_hook_rules` concatenates every plugin's every hook spec into one flat
list (`_user_turn.py:28-59`), and nothing sorts or partitions it. Every revision so far has described
the two modes as if a session only ever had one, and the only mixed case addressed anywhere is the
`Stop` cap (§10 item 2).

That leaves four questions with no answer in the plan, all of them observable:

1. **Do the two groups interleave?** If dispatch walks one merged list, a v1 rule's short-circuit
   stops the walk — and with it the *execution* of Claude rules behind it, breaking the one guarantee
   §2.5 exists to provide.
2. **Can a v1 short-circuit suppress a Claude rule's side effects?** Same defect, stated as the
   author sees it.
3. **How do two groups' verdicts, reasons, rewrites and contexts merge?** Within a group §2.5 already
   says: any deny wins, tie-break by declaration order. Across groups it is unsaid.
4. **When several `Stop` rules each produce a continuation, which one is "the rule that produced this
   continuation"** whose contract picks the cap (§10 item 2)?

**The plan's answer — partition, run concurrently, merge once. Gate G9.**

- **Partition by contract, not by position.** Two groups per event. The `claude-code` group runs under
  §2.5's bounded concurrency with the all-start guarantee; the `agentao-v1` group runs serially and
  short-circuits, exactly as today.
- **The groups run concurrently with each other**, and a v1 short-circuit ends **only the v1 group**.
  Running v1 first would let its short-circuit delay-and-suppress Claude rules; running Claude first
  would merely delay v1's short-circuit, which is harmless but pointless. Concurrent is both correct
  and simplest, and it means a v1 file installed alongside a Claude file cannot change what the Claude
  file observes.
- **One merge, group-agnostic — but over a lattice, not over "deny".** "Deny wins if any rule denies"
  flattens the four control types §4.1 deliberately separates. `continue:false`
  stops the whole turn; a `decision:"block"` stops one action; `PreToolUse` also has `ask`; and on
  `Stop` the two contracts mean **opposite** things — v1's `blockingError` ends the turn and returns
  (`_runner.py:964-981`) while the profile's `decision:"block"` **continues** it (`:984`). "Deny" is
  not a common denominator for those. The merge runs over a lattice instead, given below.
- **The `reason` tie-break ranks only inside the winning class.** "Declaration-order winner across the
  merged list" would let a `Stop`'s `stopReason` be surfaced as the reason for a `deny`. Declaration
  order picks among the rules that produced the **winning** control; nothing else
  is eligible. Contexts, notices and diagnostics are orthogonal and all concatenate in declaration
  order regardless of who won (§4.2's channel/verdict separation).
- **Rewrites cannot cross groups**, because `updatedInput` exists only in the Claude profile; a v1
  rule has no field for it. So §4.4's conflict rule handles the only conflicts that can arise.
- **The `Stop` cap follows the reason tie-break.** The continuation that survives the merge is the
  declaration-order winner, and its rule's contract picks the cap — 8 or 3. This makes attribution
  consistent with what the user actually sees as the continuation reason. The conservative
  alternative is `min()` over the contributing rules' caps, which never loosens a v1 author's
  expectation but breaks `claude-code` conformance whenever any v1 `Stop` rule also continues. G9
  picks one; §11 q5 records the trade.

**The merge lattice.** Rank across classes first:

| Rank | Control | Where it comes from |
|---|---|---|
| 1 | `Stop(reason)` | **the class "ends processing", whatever produces it.** Today exactly one thing does: `continue:false`, on an event that honors it (§5.1). The reference already ranks `continue` above event decisions *within* one hook, and across hooks the same order is the only consistent one. "`continue:false` only" is a **census of what reaches this rank today, not a rule about what may** — writing it as a rule is what made the `PostToolUseFailure` flip list contradict this table |
| 2 | the event's own decision | merged on that event's own lattice, below |
| 3 | `Allow` / none | no rule asked for anything |

**Exit 2 does not enter at rank 1.** Writing rank 1 as "`continue:false` … or an exit-2 block" is
wrong twice: §4.2's resolver maps exit 2 to `Block(reason)`, not `Stop`, and exit 2 is a **per-event**
outcome, not a global halt. It blocks a tool call on
`PreToolUse`, blocks compaction on `PreCompact`, and on `Stop` it blocks *stopping* — which means the
turn **continues**. Calling that "ends processing" inverts it on the one event where the two
contracts already collide. So exit 2 is normalized through `table.exit2(event)` **first** — `block` /
`model_feedback` / `user_notice` / `ignore` (§4.2) — and a resulting block enters that event's own
lattice at rank 2, as the event's class rather than as a generic stop. On `Stop` specifically the
normalization maps it to **continue**, which is why §4.2's `Block(reason)` spelling is a resolver-level
name and not the merge-level class.

**The same normalization is the door for a contested row — and the probe walked through it.** Rank 1
admits anything whose *effect* is "ends processing", and the only test for membership is the effect.
`PostToolUseFailure`'s `decision:"block"` was the candidate: had it ended the turn, `resolve()` would
normalize it to `Stop(reason)` exactly as it normalizes exit 2 through `table.exit2(event)`, and the
rank-2 row below would be deleted rather than kept. **Measured, it annotates and continues**
(`docs/reference/hooks-probe-2.1.251.md` §C), so it stays at rank 2 and rank 1 is untouched — the
outcome the flip list reserved, reached by probing rather than by choosing. The door stays open for
the next such row: membership is still decided by effect, never by which table a field appears in.

Then, at rank 2, per event:

| Event | Lattice | Note |
|---|---|---|
| `PreToolUse` | **`deny > ask > allow`** | upstream's own multi-hook precedence is `deny > defer > ask > allow`; `defer` is **degraded to `deny` inside `resolve()`** (§5.1), so it never reaches the merge and the merger needs no arm for it. Keep this row and G9 spelling the same set of values, or the implementer cannot tell whether to handle `defer` |
| `UserPromptSubmit`, `PostToolUse`, `PreCompact` | `block > none` | one axis, so a flat "deny wins" rule happens to be right here — and only here |
| `PostToolUseFailure` | `block > none` | **In force.** The probe found the event honors a top-level `decision` and that its effect is *feedback and continue*, so it merges here at rank 2 and rank 1 is untouched (`docs/reference/hooks-probe-2.1.251.md` §C). Two profile handlers both returning `block` merge on this row, tie-broken in declaration order. Its `continue:false` arm still merges at rank 1 through the universal row |
| `Stop` | **`end-turn > continue > none`** | the only place the two contracts collide. Ending outranks continuing because it is the outcome the other cannot undo, and because agentao's own code already orders them that way (`_runner.py:964` returns before `:984` is reached). A continuation dropped this way becomes a `user_notices` entry naming the rule that lost — silently discarding it is how an author concludes their hook "sometimes doesn't fire" |

None of this is expensive — it is one partition and one merge — but every part of it is observable,
so it is a gate rather than an implementation detail, and §12 gains a mixed-contract test per
decision-carrying event — `PreToolUse`, `PostToolUse`, `Stop`, `UserPromptSubmit`, `PreCompact` and
`PostToolUseFailure`. Two ways this set has been got wrong: omitting `PostToolUse`, which this
section's own lattice names on the `block > none` row; and making `PostToolUseFailure` conditional on
G7, which gates the event on the wrong axis — G7 governs its **event-level `decision`**, while
`continue:false` reaches it through the *universal* row (§5.1's matrix) whatever G7 decides. It is in
the set unconditionally; only its `decision` arm is gated.

**And the test shape is per event, not one template.** Asking every listed event for "a v1 rule that
blocks" is unconstructible on `PostToolUse` and `PostToolUseFailure`:
`agentao-v1` routes both through `_dispatch_lifecycle` (`_dispatcher.py:126,134`) and gives them no
stdout decision surface at all, so there is no v1 verdict for a Claude verdict to be merged with. The
mixed case on those two events is still real and still worth a test — it is a different assertion:
the v1 rule contributes an **observable side effect** and the Claude rule contributes the control, and
what must hold is that the v1 handler *ran* and the profile's control *took effect* — and on
`PostToolUse` "took effect" is **two different observations**, since `decision:"block"` preserves the
result and continues the turn while `continue:false` ends it (§5.2.2). §12 carries both shapes and, on
`PostToolUse`, both branches.

---

## 6. Output budget — two tiers, and why one is not enough

Ranked **P0**, above the P1 conformance items and above the comparison's own P2 (§5.3). The
re-ranking is a reliability argument, not a conformance one.

One tier is not enough. Capping the *parsed strings* is right as far as it goes — truncating raw
stdout before parsing corrupts the JSON control channel and turns a `decision:"block"` into unparsed
text — but it protects the model's context and nothing else.
`run_captured` reads the whole of stdout and stderr into memory through two pipes and
`communicate()` (`capabilities/process.py:214`) before any parser exists. A hook emitting gigabytes
exhausts memory before the semantic cap is reachable.

**Tier 1 — raw, at the subprocess boundary. A bounded in-memory buffer, and no spill.** "Spool to
disk while reading the pipes" is the obvious design and it cannot coexist with the redact-before-disk
property §6.1 leans on. `scan_and_redact` takes a whole string
(`security/secret_scan.py:108`); scanning chunk by chunk misses any token that straddles a boundary
and every multi-line key, and buffering the whole thing to scan it gives back exactly the memory
tier 1 exists to bound. Raw plaintext spill would also need its own on-disk policy for content that
was never redacted.

So: read the pipes incrementally against a byte ceiling, and on exceeding it **kill the process tree
and fail the hook** with a diagnostic. Not a truncation — a hook whose output was cut mid-JSON has no
meaningful decision to contribute, and pretending otherwise turns a resource failure into a silent
semantic one. `communicate()` cannot be bounded (`capabilities/process.py:214`), so this is a real
change to the shared runner or a hook-local sibling, and it must default to *off* for the runner's
other callers — `search_file_content` and the plugin hook dispatcher both route through it
(`CLAUDE.md`, Common gotchas).

**Tier 2 — semantic, and the unit is the channel, not the field.** Naming three *fields* —
`additionalContext`, `systemMessage`, and the plain-stdout-as-context path (`_output_parsing.py:49`)
— under-covers §4.2. `resolve()` appends exit-2 stderr to
`model_contexts` on `PostToolUse` / `PostToolUseFailure`, to `user_notices` on `SessionStart` /
`SessionEnd`, and a `Stop` `reason` / `stopReason` becomes the next turn's input. Each is a
hook-authored string that reaches a model or a user surface without passing through any of the three
named fields, and tier 1's ceiling — a *memory* bound, orders of magnitude above a context budget —
does not constrain it. The reference's own list is non-exhaustive by construction: *"Hook output
strings, **including** `additionalContext`, `systemMessage`, and plain stdout, are capped at 10,000
characters."*

So the cap applies to the **`ResolvedHookOutput` channels**, where `resolve()` fills them:
`model_contexts[]`, `tool_contexts[]`, `user_notices[]`, and the continuation / `stop_reason` string.
Every string leaving the resolver toward a model, a user surface, or a next turn is capped; nothing
inside the resolver is.

The ceiling itself is G4's, and upstream has more than one number: 10,000 characters for hook output
strings, and a separate **2,000**-character cap on `classifierContext` — a channel agentao does not
implement — that is *"shared across every hook that responds to that call"*. Quoted here for its
shape, not its value: upstream caps per channel, and one of its caps is a per-call aggregate rather
than a per-hook limit. codex: ~2,500 tokens, per-handler configurable, `0` disables
(`hooks/src/output_spill.rs:12`). **Only tier-2 content spills**, and it is a parsed string, so it
can be redacted whole before it lands.

### 6.1 Spill policy — reuse the sink that exists

`.agentao/tool-outputs/` already does this for large tool results
(`runtime/tool_result_formatter.py:33`), and it settles three of the five questions by precedent:
head/tail preview rather than a tail cut, **redaction before the bytes land on disk** via the shared
credential scanner (`:69`, `security/secret_scan.py:16`), and a `disk_path` surfaced on the replay
event so the full output is recoverable.

Reuse the shape in a sibling `.agentao/hook-outputs/`. The parts that precedent does **not** settle,
and that this plan must decide (design gate G4, §9):

- **Mode `0600`** on create. The existing sink does not set it; hook output is likelier to carry
  credentials than tool output, since a hook is a user script that may echo its environment.
- **Quota and cleanup.** Neither exists for `tool-outputs/` either. A per-session cap plus
  age-based pruning, or the directory grows without bound.
- **Write failure.** The existing helper is often assumed to degrade to the legacy 80,000-char cap; it
  does not. The `except` only logs (`tool_result_formatter.py:92`) and the function still returns its
  head/tail excerpt built from `TOOL_OUTPUT_SAVE_THRESHOLD` — 40,000 chars (`:29`) — so
  `MAX_TOOL_RESULT_CHARS` (`:36`) is a *different* branch that this path never reaches. The
  behavior to copy is therefore "keep the excerpt, lose the recoverable copy", and here the failure
  must additionally appear in `diagnostics[]` rather than being swallowed.

OpenAI's own hooks reference flags the same hazard — spill puts hook output on disk. Redaction on the
write path is the answer agentao already has; it just has to be wired to the new sink rather than
re-derived.

---

## 7. Disposition

Priorities are the maintainer's. "Was" is the comparison's ranking, shown where it differs, because
two items moved for reasons that are not conformance.

| # | Deviation | Disposition | Priority | Was |
|---|---|---|---|---|
| 0 | **Claude-shaped `hooks.json` parses to zero rules** (§2) | Parse the official nesting + string matcher in `claude-code` mode. | **P1** | *not in the comparison* |
| 1 | stdin contract diverges on all 8 events (comparison §5.9) | `claude-code` events emit flat snake_case. Never both shapes — and per the **§5.3 field matrix**, which decides where each value comes from and which fields agentao cannot source at all (gate G7). | **P1** | P1 |
| 2 | `UserPromptSubmit` drops all four output channels (§5.1) | Full support for all four. | **P1** | P1 |
| 3 | `systemMessage` routed to the model channel (§5.2) | `user_notices`. Field already exists (§4.3). | **P1** | P1 |
| 4 | `Stop` `hSO.additionalContext` does not continue (§5.8) | Continuation, inside the reentry cap — **8** in `claude-code`, 3 in `agentao-v1` (§10 item 2). | **P1** | P2 |
| 5 | No bound on hook output (§5.3) | **Two** tiers + spill (§6). | **P0** | P2 — moved on reliability |
| 6 | `PreToolUse` `additionalContext` only logged (§5.4) | Inject via `tool_contexts`. | **P2** | P2 |
| 7 | `continue:false` honored on `Stop` only (§5.5) | Per the capability table — **not** a global switch — **and per §5.2.2's route table**: the switch is only half of it, since two of the five honoring events have no result object that can carry a stop, and their hooks run inside a tool worker three frames below anything that could act on one (`_dispatcher.py:267-288`, `tool_executor.py:462`, `tool_runner.py:249`). | **P2** | P2 |
| 8 | exit 2 honored on `Stop` only (§5.6) | Per the capability table: block / feedback / ignore. | **P2** | P3 |
| 9 | No `${CLAUDE_PLUGIN_ROOT}` (§5.7) | Placeholder substitution **and** env export — all **three** placeholders (§2.4). | **P1**, low cost | P3 — moved on cost |
| 10 | **The `shell` field is not honored** (§2.4) | **Ignore it with a diagnostic** — and the premise is withdrawn: 2.1.251 does not honor it either and runs command hooks under `sh`, so agentao's `/bin/sh` baseline is **conformant** (`docs/reference/hooks-probe-2.1.251.md` §A). The reference's self-contradiction is settled by measurement, not by picking a sentence. | **P3** | *not in the comparison* |
| 11 | **Windows runs `cmd.exe`** — neither Git Bash nor PowerShell (§2.4) | Out of scope here; agentao has no Windows CI job, so any claim either way is untested. Recorded so it is not re-discovered. | *note* | *not in the comparison* |
| 12 | **`Stop` reentry cap is 3 where the snapshot's is 8** | Contract-resolved: 8 in `claude-code`, 3 in `agentao-v1`. It reads as a lead to preserve and is a divergence (§10 item 2). | **P2** | *comparison table, not among the nine* |
| 13 | **`updatedInput` would bypass the permission verdict** (§4.4) | Aggregate → validate → **re-decide** → intersect → re-confirm. Gate G8, blocking step 6. | **P1** | *not in the comparison* |
| 14 | **Nine profile fields have nowhere to go**, and a legal one would raise a `hook error` (§5.1, §4.2) | Enumerate the profile; unknown keys are **ignored with a diagnostic**, never schema failures. | **P1** | *not in the comparison* |
| 15 | **`PreToolUse` is skipped on an already-denied call** (`tool_runner.py:277`) | Dispatch regardless of the verdict in `claude-code`; keep the skip in `agentao-v1` (§4.4). | **P1** | *not in the comparison* |
| 16 | **Mixed-contract dispatch is undefined** (§5.4) | Partition by contract, run the groups concurrently, merge once. Gate G9. | **P2** | *not in the comparison* |
| 17 | **`permission_mode` is an out-of-enum constant, and rides on `PreCompact` where it is not defined** (§5.3) | Map or omit; strip the three agentao-private input fields in `claude-code` mode. | **P2** | *comparison §5.9, restated per event* |
| 18 | **No hook fires inside an agentao sub-agent** — sub-agents are built without plugins (`agents/tools/_wrapper.py:513`), and `_plugin_hook_rules` defaults to `[]` (`agent.py:532`) | Out of profile-1, stated in §1's event list rather than discovered. It is why `agent_id` / `agent_type` have no source (§5.3), and it is a larger scope question than the two fields that surfaced it. | *note* | *not in the comparison* |

### 7.1 Item 9 is a quick win with one trap

`plugin.root_path` is already in hand where rules are parsed (`_user_turn.py:42`) and simply is not
passed down; carrying it onto `ParsedHookRule` beside `contract` makes substitution and export
trivial in `_run_subprocess` (`_dispatcher.py:331`).

The trap: `_run_subprocess` passes **no** `env=` to `run_captured`, which is precisely why the hook
child gets `build_child_env()` and provider credentials are stripped (`capabilities/process.py:200`)
— comparison §7 item 1, one of the five places agentao leads both peers. Writing the export as
`env={...}` or `env=os.environ | {...}` silently deletes it.

The only correct form is `env=build_child_env({...})` — overrides are applied *after* the scrub by
construction (`capabilities/process.py:92`). The keys are **the three §2.4 names** — it is easy to
carry only two here: `CLAUDE_PROJECT_DIR`, `CLAUDE_PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA`. §2.4 is the authority;
this section only says *how* to export them. A test must pin that a provider key is absent from the
hook child **after** this change, not only before.

---

## 8. Implementation order

Each step is a PR **and carries its own tests**. Deferring all testing to a final step leaves five
PRs whose safety boundary is a future PR; only cross-event golden and matrix coverage belongs at the
end.

Only **step 2** is behavior-preserving. Step 1 is not, despite looking like plumbing: its truncation,
preview text and spill path are observable by design, and labelling them invisible hides a
user-facing change behind a refactor.

| # | Step | Behavior | Gates |
|---|---|---|---|
| 1 | Output budget: tier-1 bounded buffer + tier-2 semantic cap + spill (§6) | **observable** | G4 |
| 2 | `ParsedHookOutput` **and** `ResolvedHookOutput` (§4.1) + the profile and capability tables (§5.1) + the consumer table (§5.2) + the diagnostic registry (§4.2), describing today | preserving | **G10** |
| 3 | Shape detection + `contract` (a **version**, §3) + official config shape + handler-field matrix (§2.4) + Claude input serialization per the **field matrix** (§5.3) + the three path placeholders | observable | G3, G5, **G7** |
| 4 | Runtime sinks **and their per-surface routes** (§5.2.1): the lifecycle user-notice sink for `SessionStart`/`SessionEnd`, the `agentao run` dispatch-before-emit reordering, `SessionStart` context, `PostToolUse` / `PostToolUseFailure` result objects **and the stop path out of the tool workers**, and the `continue:false` route on the three honoring events that lack one (§5.2, §5.2.2) | observable | G1, G2 |
| 5 | `UserPromptSubmit` four channels, `systemMessage` → `user_notices`, `Stop` continuation | observable | G1 |
| 6 | `PreToolUse` `tool_contexts`, `resolve()` precedence over the **five** stdout states (§4.2), `continue:false`, exit 2, the full `PreToolUse` lifecycle incl. dispatch-on-DENY and `updatedInput`'s re-decide (§4.4) — table-driven | observable | **G8** |
| 6b | Run **all** matching handlers under bounded concurrency in `claude-code` mode; order-independent aggregation (§2.5); contract partitioning and the single merge (§5.4) | observable | G6, **G9** |
| 7 | Cross-event golden payloads + event × field × exit-code matrix | tests only | — |

Dependency order, not a schedule. 5 needs 2 and 3; 6 needs 2 and 4; 4 needs 2.

**All of them landed** (rev 24) — in this dependency order, but inside a **single** PR
(#199 / `18fb628`, 12 commits) rather than the seven the first line above imagines. §0 records the
three places the implementation departed from this document's text.

Step 1 is the one step that is **not** contract-scoped, and deliberately so: it lands two steps before
`contract` is parsed, and a memory bound cannot be a per-file opt-in. That is the carve-out §3 makes
explicit — `agentao-v1` freezes the contract surface, not the resource envelope.

---

## 9. Design gates

> **All ten are closed as of rev 24 — §0 is the index and the authority.** What follows is preserved
> as the record of *what each gate had to decide*. The "Blocks step N" lines are historical, and the
> per-gate closure markers below stop at rev 23, which is the last revision that wrote one. Read a
> bullet for the question, §0 for the answer.

Each must be closed **before** the step that depends on it. They are gates rather than open questions
precisely because the steps already treat them as dependencies.

- **G1 — `user_notices` transport shape *and its route on each surface*.** Blocks steps **4 and 5** —
  step 4 needs it for the `SessionStart`/`SessionEnd` exit-2 sink (§5.2), step 5 for `systemMessage`.
  agentao emits one `PLUGIN_HOOK_FIRED` carrying verdict and counts, and its docstring says hook output
  is "neither known nor stored at this layer" (`_hook_dispatch.py:52-53`). A user-visible notice channel
  needs more than a count. Decide: new event type, or an extended payload — **and then**, per §5.2.1,
  where each surface renders it: `agentao run` emits its whole output at `run.py:814` *before*
  `SessionEnd` fires at `:815` and has already detached observers at `:770`, so the transport decision
  alone leaves headless users with no path. The reference's wire form for the headless case is an
  `SDKInformationalMessage` under `--output-format stream-json`.
- **G2 — lifecycle result types, and the stop route out of a tool worker (§5.2.2).** **DECIDED (rev 23): pair (ii)** — the queued-sibling guarantee is dropped and no seam is built; §1 records the queued-at-stop moment as **undefined**, and §12's test shrinks to the invariant that holds either way. Everything below about result types, the aggregation path and the `stopReason` tie-break still stands. Blocks step 4.
  `SessionStart`, `PostToolUse` and `PostToolUseFailure` are lifecycle-only today: all three return
  `list[HookAttachmentRecord]` out of `_dispatch_lifecycle` (`_dispatcher.py:66,126,134,267-288`), so
  anything a hook decides is dropped at the call site. `SessionStart` needs its return value for the
  exit-2 user notice and `additionalContext` only — profile-1 does not honor a stop there (§5.1). The
  other two need a **control path across three frames**, and that is where a result type alone is not
  enough: the hooks run inside a worker (`tool_executor.py:189,462`), `ToolRunner.execute` returns
  `(bool, list)` (`tool_runner.py:238,249`), and the chat loop reads exactly those two
  (`_runner.py:773`). Six decisions: (a) the result type for each; (b) **the aggregation path** worker
  → `execute_batch` → `ToolRunner.execute` → chat loop; (c) **sibling calls in a parallel batch** —
  only the *firing* tool has run, since every plan is submitted up front to an 8-worker pool and each
  worker dispatches on its own completion (`tool_executor.py:189-200,462-470`), so up to seven siblings
  are mid-execution and more may be queued; the plan proposes *let the batch finish, then stop*
  because it is the only option that preserves (d) with no new machinery, and G2 either **probes what
  upstream does with queued siblings** or declares the choice a documented deviation. **The seam is not optional independently of the promise**: today nothing can observe the moment
  between a stop being attached to a `ToolExecutionResult` and the worker's future completing, so a
  queued-sibling *rule* with no seam is a rule no acceptance run can enforce. The two move together,
  and G2 picks the pair, not the parts — **(i) keep the guarantee and land the seam**, or
  **(ii) drop the guarantee**. **A configurable `max_workers` is not the seam.** Making the `8`
  injectable bounds concurrency and nothing else: the stopping task's future still completes whenever
  it completes, and its worker still dequeues the tail immediately after, so the test gains no control
  over the instant between *the stop becoming observable* and *the tail being dequeued* — "keep the
  guarantee, inject only the cap" is the same unenforceable pair (i) exists to forbid, wearing a
  different name. The seam is one of exactly two things: a **test-visible callback or event fired
  between attaching the stop to the `ToolExecutionResult` and the worker's future completing**, or a
  **controllable executor / admission gate** the test drives, deciding when a worker is released and
  when the tail is admitted. A cap knob may ride along to keep the batch small; it is never the seam.
  Under (ii): §1's profile
  gains a line saying agentao guarantees only the *batch outcome*, and whether a queued sibling runs
  after a stop is **undefined** — listed, per §1's third rule, rather than silently dropped. This is
  G6's own pattern one gate over: when the machinery for "all matching handlers start" looked too
  expensive, the fallback weakened the **promise** to "all handlers are submitted", not the test
  (§2.5). Weakening only the test leaves a rule that an implementation can violate and still pass; (d) the invariant that **every plan still yields a result and a tool
  message**, because `format_batch` indexes `exec_results[plan.tool_call_id]` per plan
  (`tool_result_formatter.py:113-128`) and an assistant `tool_calls` entry with no answering
  `role:"tool"` message is rejected by strict APIs; (e) **which `stopReason` wins** when several tools
  stop — plan order, never completion order; (f) **what `continue:false` terminates at each site**, per
  §5.2.2's table, where the `PreToolUse` row is the one that can be silently mis-implemented as a
  `deny` because a verdict field is already there. `updatedToolOutput` in particular has to splice into
  the tool result before the model sees it — a `ToolRunner` format-phase concern, not a hook-package
  one — and it is a **different sink** from `decision:"block"`, which preserves the original output and
  only appends a reason (§5.2.2).
- **G3 — Claude matcher semantics.** Blocks step 3. Pin the three-way evaluation (§2.2) and confirm
  against codex's implementation, which already has all three.
- **G4 — budget units, ceilings, spill policy.** Blocks step 1. Tier-1 byte ceiling; tier-2 unit
  (characters need no tokenizer, tokens are what the budget protects); mode/quota/cleanup/failure
  per §6.1. Tier 1 no longer writes to disk, so the temporary-plaintext question is closed rather than
  answered.
- **G6 — hook concurrency bound, overflow, and merge determinism.** **DECIDED (rev 23): the fallback** — the promise is "all matching handlers are **submitted**", not "all start", and under `SessionEnd`'s shared budget a queued handler may never run. No per-dispatch admission control. (c)'s declaration-order tie-break is unaffected and still required. Blocks step 6b. Three
  decisions, not one. (a) The pool's name and cap — a fourth pool, kept off the three `CLAUDE.md`
  documents. (b) **What happens past the cap.** A cap alone does not deliver "all matching handlers
  start": beyond it they queue, and under `SessionEnd`'s shared 1.5-second budget a queued handler
  may never start at all, which is the failure the whole change exists to fix. The plan's proposal
  is a **per-event handler limit equal to the pool cap, enforced on the merged rule list** — not per
  file, which bounds nothing, since `resolve_all_hook_rules` concatenates every plugin's every hook
  spec afterwards (`_user_turn.py:28-59`) — with an over-cap configuration rejected at load with a
  warning naming the colliding plugins. And because dispatches race each other (8 tool workers, each
  firing its own `PostToolUse`, `tool_executor.py:189,463`), admission is per **dispatch**:
  capacity for all of an event's handlers is acquired before any of them starts. The fallback, if
  that is too much machinery, is to weaken the promise to "all handlers are *submitted*" and accept
  that `SessionEnd`'s shared budget may expire on a queued one. (c) The tie-break that makes an
  aggregated `reason` reproducible — declaration order, never completion order.
- **G7 — the profile's two field matrices (§5.1 output, §5.3 input).** **PARTLY CLOSED (rev 23).** Both contested rows are measured (§0): `SessionStart` discards `continue:false` — narrow reading confirmed; `PostToolUseFailure` **honors** `decision`, as feedback-and-continue — narrow reading reversed. The artifact question is decided: **provenance table only, no in-repo archive**. The input-side rows below are **corrected but not closed** — the probe captured six real payloads (§0), which confirms the matrix's shape without deciding what agentao sources. Blocks step 3. On the input
  side: what `transcript_path` points at (or that it stays `null`); the `permission_mode` mapping or
  its omission, **including its removal from `PreCompact`**; whether `tool_response` becomes an
  invented object, stays a string as a documented divergence, or waits for real tool output schemas;
  the disposition of the three agentao-private input fields (`turn_end_reason`, `compaction_type`,
  `reason`) — dropped, or namespaced under one `agentao` sub-object; and whether an unsourced field is
  absent or explicitly `null`. On the output side: the rows §5.1 leaves open — `terminalSequence` (its
  transport is the same one G1 is deciding), `sessionTitle` (a product decision, not a conformance
  one), **the two contested rows** — `PostToolUseFailure`'s `decision` and `SessionStart`'s `continue:false`
  (probe a real CLI, or declare a deliberate profile deviation; the document settles neither, §5.1) — **`reloadSkills`** (teach discovery the
  `.claude/skills` tree, or accept-and-document that agentao rescans a different one),
  **`suppressOriginalPrompt`** (add the prompt to the block message so the flag has something to
  suppress, or leave it ignored), and **`defer`'s degradation target** (`deny`, as proposed, or `ask`). It also carries the provenance-artifact decision from §3 — archive the fetched
  reference in-repo, or record the provenance table and archive it elsewhere.

  **Flip lists.** A contested row is decided against the reference's wider statement, so the probe can
  reverse it — and "invert the assertion" is not a plan. Each branch names what changes, once:

  **Both probes have now returned** (§0). The rows are kept as they were written, with the outcome
  marked, because a flip list read after the fact is how the next contested row gets planned.

  | If the probe finds… | Then, together in one change |
  |---|---|
  | **`SessionStart` honors `continue:false`** — ❌ **did not fire**; measured `discarded` | §5.1's matrix cell `discarded` → `honored`; §5.2's `SessionStart` row gains a **control result** beside the notice and context sinks; §5.2.2's route table gains the row deleted here — *the session, before its first turn*, with **both surface semantics** (interactive: notice, then exit without entering the input loop; `agentao run`: no turn runs, `RunResult` carries the reason) and a **headless exit code**, which mid-turn stops do not need because they map through the ordinary turn-outcome path; **G2** gains a seventh decision (`SessionStart`'s result type and that exit code — `3` was the proposal, or `1`); **step 4** gains the route; and §12 replaces the non-stop test with an end-to-end stop test on **both surfaces**, which fails against today's code because `cli/session.py:81` discards the dispatcher's return value |
  | **`PostToolUseFailure` honors `decision`** — ✅ **fired**, and answers (2)–(4) selected the *feedback* branch below | §5.1's row drops "contested" — **but only probe answer (1) is settled by that.** The rest of this branch is written *after* answers (2)–(4), not before: §5.2's row gets the `reason` channel the probe found (model / user / transcript-only), a statement on whether the original error survives beside it, and whether the turn continues; **§5.4 changes in one of two mutually exclusive ways, and the probe picks** — if answers (2)–(4) describe a *feedback / per-event* effect, the conditional `block > none` row at rank 2 simply becomes unconditional; if they describe *ending the turn*, that row is **deleted** instead and `resolve()` normalizes the block to `Stop(reason)` so it enters at **rank 1**, which then also changes rank 1's source list, **G9**'s parenthetical, and the resolver and consumer that produce and read it; §12 gains the `decision` branch it already reserves, **plus a multi-handler aggregation test**, since the `accept` owes an aggregation rule the moment it is honored (§5.1). What this row may **not** do is copy `PostToolUse`'s semantics across: the global row they share fixes a shape, not an effect (§5.1) |

  Neither list is speculative work: both name sections that were written and then deleted, or left
  conditional, when the narrow reading was taken. Writing them down is what stops that deletion from
  having to be re-derived.
- **G9 — mixed-contract dispatch and the control lattice (§5.4).** Blocks step 6b. Partitioning,
  whether the groups run concurrently, **the per-event control lattice and its cross-class
  precedence** — `Stop` (the "ends processing" class; today reached by `continue:false` alone, while
  exit 2 normalizes through the event table first, and a contested row can join it if G7 finds its
  effect is turn-ending, §5.4) over the event decision, `deny > ask > allow` on `PreToolUse` after `defer` has been
  degraded in `resolve()`, and `end-turn > continue` on `Stop`, where the two contracts mean opposite
  things —
  the `reason` tie-break *within the winning class only*, and which contract's `Stop` cap applies when
  several rules produce a continuation. The plan proposes concurrent groups, the lattice above, and
  `min()` as the named alternative for the cap.
- **G10 — the diagnostic registry (§4.2).** Blocks step 2, where `diagnostics[]` first has a producer.
  Owner (session-scoped, not dispatcher-scoped — it is constructed at six sites, two inside pool
  workers), the lock, the **stable rule key** that survives a plugin reload, and the lifecycle on
  reload and on `/clear`. Small, and it is the difference between a useful one-time notice and either
  a per-invocation storm or silence.
- **G8 — the `PreToolUse` lifecycle (§4.4).** **PARTLY CLOSED (rev 23).** The invalid-rewrite branch is **measured**: upstream rejects the call and the original never runs, so the plan's choice ships as conformance, not as a deviation from safety. The **pre-execution validator is dropped** by maintainer decision — no `jsonschema` promotion, no `Tool.preflight()`, step 2 of the order below is deleted and §1 records the narrowed promise. What remains open is the rest of the lifecycle. Blocks step 6. Ten steps, and the half that gets omitted
  is the front: **when the hook fires** (never on an unknown tool or a failed input validation; **always** on a
  permission denial, which means deleting the `tool_runner.py:277` skip in `claude-code` mode and
  keeping it in `agentao-v1`), then aggregate, validate against the tool schema, **re-decide**,
  intersect (never upgrade), re-confirm on the modified input, do not re-dispatch — plus the conflict
  rule when two handlers rewrite the same call. Without the second half, `updatedInput` launders an
  argument past a verdict already computed; without the first, every audit hook is blind to exactly
  the calls it exists to record. G8 also owns the **pre-hook validator** step 2 depends on: where it
  sits relative to argument repair, what it costs in calls that succeed today, whether to promote
  `jsonschema` to a direct dependency (the subset fallback is refuted — §4.4), and whether to add the
  pure `Tool.preflight()` interface that tool-specific validation needs or to narrow step 2 to schema
  validation alone. **And what an invalid *rewrite* does** (§4.4 step 6): the plan denies the call,
  because the alternative runs the input the hook was replacing. The sentence that used to settle this
  was never in the reference, so G8 settles it by probing — and if Claude Code does fall back to the
  original, that is adopted as a **documented deviation from safety**, in writing, not by default.
- **G5 — `${CLAUDE_PLUGIN_DATA}`, exec form, and the shell.** **The shell half is CLOSED (rev 23) by measurement**: 2.1.251 runs command hooks under `sh` and ignores an explicit `shell`, so agentao's baseline is conformant and the field is ignored-with-a-diagnostic (§2.4, §0). No `executable=` change is needed. `${CLAUDE_PLUGIN_DATA}` and the exec form **closed at implementation** (§0). Blocked step 3. The placeholder needs a
  per-plugin data directory agentao does not have (location, creation, lifetime); `args` needs a
  field on `ParsedHookRule` and an exec-form branch at `_dispatcher.py:353`, which is `shell=True`
  unconditionally today; the same site must decide `executable="/bin/bash"` (§2.4, "The baseline
  shell is already wrong") and what happens where bash is absent.

---

## 10. What must not regress

The five places agentao leads both peers (comparison §7). Each needs a test that survives this work,
and the first is actively threatened by step 3:

1. **Provider credentials scrubbed from the hook child** — §7.1. The one at real risk.
2. **That a `Stop` reentry cap exists at all** (`_runner.py:157`, `stop_reentry_cap: int = 3`).
   The cap itself is the lead; **its value of 3 is not.** The snapshot's number is **8**: *"Claude Code overrides the hook and ends the turn after 8 consecutive blocks"*, and
   `additionalContext` continuation runs *"through the same loop protections … namely the
   `stop_hook_active` input and the 8-consecutive-continuation cap"*. Keeping 3 under a `claude-code`
   label makes reentries 4 through 8 behave differently on the two tools — the exact class of
   divergence §1 exists to close, listed as deviation 12 in §7. So the cap is contract-resolved: **8**
   in `claude-code`, **3** in `agentao-v1`. What must not regress is that a cap exists and that item
   4's new continuation source lands inside it rather than beside it.
   The cap lives on `ChatLoopRunner`, per turn; the contract lives on a rule, per file. The plan
   proposes one counter compared against the cap of the contract that produced *this* continuation,
   so a pure `agentao-v1` setup keeps 3 and a mixed session is not silently loosened for hooks that
   never asked for it. The simpler alternative — one cap per turn, the maximum over installed
   contracts — lets a `claude-code` file raise the ceiling for a `v1` hook sharing the turn. §11
   records the choice as open.
3. **`permissionDecision:"ask"`** — supported here (`models.py:302`), rejected by codex. The
   capability table must not copy codex's row, and §4.1's union exists so the type can hold it.
4. **`PostToolUseFailure`** exists here and not in codex. Keep the event; it gets a capability row
   *and* a sink (§5.2).
5. **Runnable `prompt` handlers** (`UserPromptSubmit` only). `SUPPORTED_HOOK_TYPES_BY_EVENT`
   (`models.py:217`) already encodes the restriction; the new table must not contradict it.

One `agentao-v1` guarantee that belongs here rather than among the leads: **the DENY skip at
`tool_runner.py:277-279` stays in `agentao-v1`.** `claude-code` mode deletes it (§4.4), so a test must
pin both halves — a denied call forks the hook under the profile and does not under v1. Without that
test the two modes converge silently the first time someone "simplifies" the branch.

Note what is deliberately **not** on this list: "serial, short-circuit dispatch", which reads like a
lead and is not. **Execution is bounded-concurrent in `claude-code` mode** (§2.5) — short-circuiting *and* serial ordering are both conformance divergences
there, and only `agentao-v1` keeps them. What stays declaration-ordered is **aggregation and the
`reason` tie-break**, not execution.

Still out of scope, and unchanged: per-hook trust hashing and `HookRunSummary` observability — the
two axes where codex is structurally ahead (comparison §6). Bounded concurrency for hook *execution*
is not an adoption of codex's model; it is what the reference specifies.

---

## 11. Remaining open questions

Not gates — they can be decided during the step that touches them.

1. **The `<stop-hook>` echo has no suppressor in `claude-code` mode — settled.** `suppressOutput`
   gates it today (`_runner.py:1045`); inert-in-strict-mode is correct for conformance, so in
   profile-1 **the echo is unconditional**. The tempting alternative —
   `hookSpecificOutput.agentao.suppressOutput` takes over — is not available: §3.3 removed that
   namespace from profile-1, and an open question cannot keep alive a surface the design section
   deleted. What stays open is the *feature*, not the spelling: if suppressing the echo under a
   Claude-shaped hook is ever wanted, it arrives with the namespace at §3.3's price, in a later
   profile (q4). `agentao-v1` is unaffected.
2. **Does `agentao-v1` freeze, or drift?** Stated as frozen in §3, with "frozen" scoped to the
   **contract surface** — resource limits are explicitly outside it. What stays open is the contract
   half: if a future field lands only in `claude-code`, `v1` files silently miss it — cheaper to say
   so now than to discover it per-field.
3. **The `if` field.** Its disposition is decided and lives in §2.4 — **reject with a warning**.
   What stays open is only whether to build it later: agentao has a permission engine with pattern
   matching (`permissions.py`), so it is reachable, but it carries its own Bash-subcommand semantics
   and fails open by design. Not a field to wire up; a sub-feature to schedule if someone asks.
4. **Does the profile need more than one value?** Shipping exactly one `claude-code@profile-1` until
   a second is needed keeps the mechanism honest without the cost of maintaining two contracts on day
   one. The related question is when a profile *must* bump: a field moving from `ignore` to `accept`
   changes observable behavior for a hook that already emits it, so it is a new profile, not a patch.
5. **Which `Stop` cap applies in a mixed-contract session?** §10 item 2 proposes the cap of the
   contract that produced the continuation, over a per-turn maximum. Both are defensible; only the
   silence is not.
6. **Does the archived reference live in this repo? — now with a measurement.** §3 needs the profile's
   provenance to resolve to something immutable, and the anchor **drifted within 24 hours**
   (`c984f918…` → `b727657a…`, §3). Nothing in the repo holds the pinned bytes today, so a reviewer
   who wants to check a quoted clause against profile-1 cannot: fetching the URL gives a different
   file. Roughly 290 KB of upstream prose in `docs/reference/snapshots/` is the direct answer and a
   redistribution question; the provenance table (URL, fetch time, sha256, changelog head) is the
   light one and **depends on an external copy surviving**, which is the assumption the drift just
   made expensive. **Decided (rev 23): the provenance table, not the vendored copy** (§0). The cost is
   stated rather than removed — every quotation carries its `hooks.md:<line>`, so a re-fetch can
   *locate* a clause but cannot check it byte-for-byte against profile-1.
7. **Is a real Claude Code probe harness ever worth building?** §3's second option — the only thing
   that would let a label name a product version honestly, at the cost of an installed CLI plus a
   behavior suite. **Half-paid since rev 23**: the CLI is installed and one probe set is on record
   (`docs/reference/hooks-probe-2.1.251.md`), but that is a *transcript*, not a rerunnable suite, so it
   dates rather than tracks. Until the other half exists, `profile-N` is the accurate name and the
   provenance table carries what is actually known.
8. **Does `reloadSkills` land in profile-1 or profile-2?** Accepting it looks defensible on the
   strength of an existing consumer, but that consumer scans a *different tree* (`~/.agentao/skills`,
   not `.claude/skills`) with no lock on the reload path, so profile-1 ignores it. The open question is whether skill discovery should learn the `.claude` tree — which is a
   compatibility feature well beyond hooks — or whether accepting-and-documenting the difference is
   good enough. **G7.**
9. **Is a `PostToolUseFailure` `decision` honored, and if so what does it do? — CLOSED (rev 23).**
   **Yes, and it annotates**: the reason reaches the model, the original error survives beside it, and
   the turn continues (§0). The snapshot said both things about *whether* and nothing about *what*, and
   no amount of re-reading it would have produced this — the global row fixes a wire shape whose
   members' effects are mutually incompatible. Two things are worth keeping now that the answer is in.
   The asymmetric-cost argument that governed the interim (decline a defined `decision` and you lose a
   feedback channel `additionalContext` already covers; honor one and you commit to an unnamed effect)
   was the right *interim* rule and is the wrong *permanent* one — it was always a way to be safe while
   ignorant, not a finding. And an earlier revision of this entry asserted that honoring would "stop a
   turn upstream would have continued": that pre-filled the answer G7 existed to obtain, contradicted
   the flip list two sections away, **and was wrong on the facts** — the turn continues.

---

## 12. Verification

- **8 golden stdin payloads**, one per event, asserted byte-for-byte per contract mode. Comparison
  §9.1's probe already drives all eight dispatch paths and is the natural generator. The goldens must
  also pin the **§5.3 rule**: a field agentao cannot source is absent or `null` per G7's decision, and
  never a plausible-looking fabrication — `tool_use_id` and `duration_ms` present and correct,
  `transcript_path` whatever G7 chose *and nothing else*, `source` / `reason` carrying the real cause
  rather than a constant. And the **forbidden** column is an assertion too: no `turn_end_reason` on
  `Stop`, no `compaction_type` / `reason` / `permission_mode` on `PreCompact`, and `permission_mode`
  present with an in-enum value on the five events that owe it — a golden that only checks presence
  would pass today on `Stop`, where the value is the constant `"workspace-write"`
  (`_payload.py:144`).
- **Golden configuration files** — the official nested shape **with no `contract` key** (the copied
  file, §2.2), the flat shape, a mixed file (rejected whole), and a file with an unknown explicit
  contract (disabled). The §2 probe is the seed, and it currently produces `0` rules from the
  official shape.
- **A precedence matrix**, not per-field assertions: the full `{0, 2, other}` × `{valid,
  schema_invalid, parse_error, plain, empty}` grid (§4.2's five states), crossed with
  `continue:false`/`true`/absent and `allow`/`block`/absent, per event, against `resolve()`. It must cover all **three** exit-2
  outcomes — including a `PostToolUse` hook that exits 2 with empty stdout and asserts its stderr
  reached the *model*, a `SessionStart` hook whose stderr reached the *user* and not the model, and
  a `SessionStart` hook that exits **1** with plain text, asserting that text did **not** become
  model context.
- **Two universal-field exception tests** (§5.1's matrix): a `PreCompact` hook setting
  both `systemMessage` and `continue:false` asserts **neither** takes effect — no user notice, no stop
  — while the same output on `Stop` produces both; and the same pair on `SessionEnd`, where the whole
  JSON is discarded. The first assertion is the one that fails against a design with no per-event gate
  for `systemMessage`. **Each also asserts that no diagnostic is emitted** (§5.1's two axes):
  a discard is silent because the hook is upstream-conformant, and "silent" is the half that drifts
  without failing anything — an implementation that routes a discard through the `ignore` path passes
  every other assertion in this bullet. Its mirror is in the forward-compatibility bullet below, where
  an *ignored* field must produce exactly one.
- **A channel-orthogonality test**: one hook that blocks *and* sets `systemMessage`, asserting both
  the block and the user notice survive (§4.2's merge — the channels a verdict-only `resolve()` drops).
- **One test per stdout state** (§4.2's **five**), and the first of them asserts the post-v2.1.248
  arm: a `{`-leading, `}`-ending string that fails to parse produces a **user notice** and does **not**
  become `UserPromptSubmit` context on exit 0. Asserting the reverse is the pre-2.1.248 behavior.
  Plus: a `{`-leading string that does *not* end in `}` staying plain text and becoming context on
  exit 0; a `[`-leading array likewise plain text; a schema-invalid object producing a **user** notice
  while the action proceeds, and the same object on **exit 2** blocking with stderr as the reason
  (v2.1.214); and an exit-1 hook with empty stdout producing a user notice carrying the **first line
  of stderr**.
- **An end-to-end `SessionEnd` exit-2 test** — not a resolver unit test: a real hook exiting 2, run
  through `dispatch_plugin_session_end`, asserting the stderr reached the user sink. That path
  discards the dispatcher's return value today (`cli/session.py:87`), so a resolver-level test would
  pass while the feature does not exist.
- **An all-handlers-run test**: two matching hooks where the first blocks, asserting the second still
  executed in `claude-code` mode and still does not in `agentao-v1` (§2.5) — plus a determinism test
  that the aggregated `reason` is the declaration-order winner regardless of which finished first.
- **A merged-limit test** (§2.5, G6): **two plugins**, each individually under the per-event cap,
  whose merged `SessionEnd` handler count exceeds it — asserting the refusal happens at merge with
  both plugin names in the warning. A single-file test passes with the wrong implementation.
- **A concurrent-dispatch test**: a batch of tool calls large enough that their `PostToolUse`
  dispatches overlap, asserting every handler of every dispatch started (§2.5's admission rule) — or,
  if G6 takes the fallback, asserting the documented weaker promise instead. It must fail against a
  per-dispatch cap with no global admission.
- **An `updatedInput` re-decide test** (§4.4, G8), the security case: a `PreToolUse` hook that
  rewrites an allowed `Bash` argument into one the hardline scanner denies, asserting the call is
  **denied and never executed** — and its mirror, a rewrite that fails the tool's parameter
  schema. That one **was** branch-structured on G8 and is now settled: the probe found upstream
  rejects the call and **never runs the original** (§0), which is the plan's own default, so the test
  asserts it unconditionally. One thing it must not assert is upstream's *error surface*: with the
  pre-execution validator dropped, agentao cannot detect the mismatch, so the rewritten call reaches
  the tool and fails there. The assertion that carries the security property is that the **original**
  arguments never reach the executor. Plus a confirmation test asserting the
  Phase 2 prompt shows the **modified** input, and a no-re-dispatch test asserting the hook fires
  once.
- **A `Stop` cap test**: 8 consecutive continuations honored under `claude-code` and 3 under
  `agentao-v1` (§10 item 2), with the mixed-contract case pinned to whatever G9 decides.
- **Profile forward-compatibility tests** (§1, §4.2, §5.1) — the class a closed-schema parser fails: a hook
  emitting `terminalSequence` alongside a `systemMessage` asserts the notice is delivered, the unknown
  field is ignored, **no `hook error` reaches the user**, and one diagnostic names the field; a second
  invocation of the same rule asserts the diagnostic does **not** repeat. **`watchPaths` gets the same
  test, not a parse-rejection test** — the parser cannot perform one (§1's third rule), and the
  assertion that matters is that the `systemMessage` beside it still arrives. Then the
  rows that changed disposition. **Two of them are ignored fields** — `suppressOriginalPrompt` and
  `reloadSkills` — each asserted as **parsed, diagnosed, and not acted on**: nothing consumes the flag,
  and no rescan is triggered (the diagnostic naming the directory mismatch). **The third is a degraded
  value, which is a different assertion**: `defer` **is** acted on — it becomes `deny`, the reason
  names the unimplemented value, and the tool never runs. Grouping all three as "parsed and not acted
  on" and then requiring `defer` to block cannot both be true. Two traps to avoid while writing these:
  a `suppressOriginalPrompt: true` test that asserts "the prompt is absent from the block message"
  passes today **with no parsing at all** (`_hook_dispatch.py:73` never includes it), so the assertion
  has to be on the *parse and the diagnostic*, not the message; and an `hSO` block whose
  `hookEventName` names a **different** event must produce `schema_invalid` for the **whole object** —
  the top-level `systemMessage` in it does **not** survive (§5.1's `hookEventName` row).
- **Diagnostic-registry tests** (§4.2, G10), which is where the mechanism is easiest to get silently
  wrong: two dispatches of the same rule in one session produce **one** diagnostic even though the
  dispatcher object differs between them; two *concurrent* tool events produce one, not two; and a
  plugin reload makes the same rule announce again.
- **Four `PreToolUse` lifecycle tests** (§4.4, G8), which is where the reference is explicit and
  agentao is not: a call the permission engine already **denied** still fires the hook, and the verdict
  stays `DENY` after it (`claude-code`) while the same case does **not** fire it under `agentao-v1`; a
  call rejected for an unknown tool name fires **no** hook. **The two validator tests are deleted**
  (§0): with no pre-execution validation there is no rejection for them to observe, so "input that
  fails the schema fires no hook" has nothing to fail the input. §1 records the narrowed promise in
  their place — which is the point of listing a non-promise rather than dropping a test quietly.
- **Mixed-contract tests** (§5.4, G9), one per decision-carrying event — `PreToolUse`,
  **`PostToolUse`**, `Stop`, `UserPromptSubmit`, `PreCompact` and **`PostToolUseFailure`**, the last
  of them unconditionally: `continue:false` reaches it through §5.1's universal row independently of
  its event-level `decision`, and that `decision` is now honored too (§0), so **neither arm is
  gated** — the distinction survives only as the reason the event was in this set before the probe. **Two shapes,
  not one template.** On the four events where **both** contracts carry a decision
  (`PreToolUse`, `UserPromptSubmit`, `Stop`, `PreCompact`): a v1 rule that blocks and a Claude rule
  behind it in declaration order, asserting the Claude rule still **executed**, that the merged verdict
  is the deny, and that the surfaced `reason` is the declaration-order winner regardless of which group
  finished first. On the two where **only the profile** has a decision (`PostToolUse`,
  `PostToolUseFailure`) that setup is unconstructible — `agentao-v1` routes both through
  `_dispatch_lifecycle` (`_dispatcher.py:126,134`) and gives them no stdout decision surface at all —
  so the v1 rule contributes an **observable side effect** (a file it writes, or its attachment record)
  and the Claude rule contributes the control. **Two branches on `PostToolUse`, because its two
  controls mean opposite things** (§5.2.2): with `decision:"block"`, assert the original tool output is
  **preserved**, the `reason` reaches the **model** beside it, and the turn **continues** to the next
  model call; with `continue:false`, assert the turn **ends** and no further model call is made. On
  `PostToolUseFailure` **both** branches are now unconditional: the probe turned the `decision` on
  (§0), so the two-handler test lands with it — two profile `PostToolUseFailure` handlers both
  returning `block`, asserting the merged verdict and that the surfaced `reason` is the
  declaration-order winner, which is the aggregation rule §5.1 requires of every honored `accept`.
  It merges at **rank 2** on §5.4's now-unconditional `block > none` row, because the probe's answers
  (2)–(4) put its effect in the feedback class, and the test asserts those answers directly: the
  reason reaches the model, the original error survives beside it, and a further model call happens. It is
  written against whichever branch the probe selects — rank 2 with the `block > none` row, or rank 1
  with the block normalized to `Stop(reason)` — and it asserts the probe's own findings for (2)–(4):
  where the `reason` was delivered, whether the original error survived beside it, and whether a
  further model call happened. Pre-binding it to rank 2 assumes answer (4). Either way the v1 handler must have **run**, with the v1 rule placed *first* in declaration
  order so a walk that short-circuits on the profile's control would fail it. "The profile's control
  took effect" is not an assertion — it is the thing the two branches disambiguate. Writing the original template against these two events yields a test that
  cannot be written.
  Then the three the lattice exists for, none of which "deny wins" can express: **`continue:false`
  against a `block`** (the stop outranks it, and the reason surfaced is the stop's); **`ask` against
  `allow`** on `PreToolUse` (ask survives — `deny > ask > allow`, `defer` having been degraded before
  the merge); and **a v1 `blockingError`
  against a Claude continuation** on `Stop`, asserting the turn ends, that the dropped continuation
  appears as a user notice naming its rule, and that the `reason` shown is the ending rule's — not the
  continuation's.
- **A `PostToolUse` stop test that crosses the worker boundary** (§5.2.2, G2), end to end rather than
  at the resolver, with **two tools whose completion order is swappable**: both calls in one batch, the
  hook on the *second-declared* one emitting `{"continue": false, "stopReason": "…"}`. Assert (a) the
  turn ends and no further model call is made; (b) **both** tool-call ids still have a `role:"tool"`
  message in history, in plan order — the invariant `format_batch` enforces per plan
  (`tool_result_formatter.py:113-128`), and the one a mid-flight abort breaks; (c) the surfaced reason
  is the stopping rule's; and (d) the outcome is identical when the two workers finish in the opposite
  order, which is the assertion a completion-order implementation fails. Written against today's code
  it cannot pass above the worker at all: `ToolRunner.execute` returns `(bool, list)`
  (`tool_runner.py:249`) and the chat loop reads only those two (`_runner.py:773`).
  **Two calls do not exercise the two rules G2 actually adds**, so two more tests come with it:
- **A queued-sibling test — dropped with its guarantee (G2 took pair (ii), §0).** What lands instead
  is the invariant that holds either way: every plan yields a result and a `role:"tool"` message. The
  analysis below is kept because it is *why* the guarantee was dropped rather than quietly weakened,
  and because the seam it describes is what a future revision would have to build first.
  **The original bullet — which cannot be written against today's executor, and that is the
  finding.** "Nine short tools with the stop on an early one" is a race: the stopping tool's worker is
  freed and can pick up the ninth plan before the assertion runs, so the test passes with nothing ever
  queued. **Latching plans 2–8 does not fix it.** It is true that the `PostToolUse` hook runs *inside*
  the worker's own task (`tool_executor.py:468-471`) and that all eight workers are therefore busy for
  the hook's duration — but the `Stop` only becomes observable to anything outside that worker *after*
  the dispatcher parses the output and returns, and `_execute_one` then returns at `:473` and releases
  the worker. The interval between "stop exists" and "worker takes plan 9" is not one a test thread can
  enter. An earlier revision proposed the latch construction as deterministic; it is not, and the
  reason it looked deterministic is that it proves the wrong thing — occupancy *during the hook* says
  nothing about the queue *when the stop is observed*.

  What the test needs is a **synchronization point between attaching the stop to the
  `ToolExecutionResult` and completing the worker's future**, and no such seam exists: the pool is
  constructed inline with a literal cap (`ThreadPoolExecutor(max_workers=8)`, `:189`), `execute_batch`
  accepts neither an executor nor a cap, and the executor exposes no callback at that point
  (`output_callback` is a *tool* attribute for streaming, not a seam here). So **G2**'s (c) owns one of
  two production changes: a test-visible callback/event fired between the attach and the return, or an
  injectable executor / admission control so the test decides when a worker is released. Both are
  small; neither is free, and one of them is a precondition for this test existing at all.

  **And the seam is not optional on its own** — it is optional only together with the rule it
  enforces. An earlier revision kept the queued-sibling guarantee while allowing G2 to decline the
  seam and downgrade this test to a batch-outcome assertion "that can pass vacuously". That is a rule
  no acceptance run enforces: an implementation that cancels queued siblings when the plan says to run
  them passes every test in this file. So G2 picks a **pair** (G2 (c)): either the guarantee stands and
  the seam lands with it, and this test runs in its non-vacuous form — the batch, the stop, and a
  synchronization point proving the tail was still queued when the stop became observable — or the
  guarantee is dropped, §1 records that only the batch outcome is promised and the queued-at-stop
  moment is **undefined**, and this bullet shrinks to what remains testable: **every plan yields a
  result and a `role:"tool"` message**, which needs no seam and is the invariant that holds either way.
  What the plan will not ship is the third combination — the promise without the seam.
- **A stop-arbitration test.** **Two** tools each returning a *different* `stopReason`, run twice with
  the completion order swapped, asserting the surfaced reason is the **plan-order** winner both times.
  The single-stop test above cannot fail a completion-order implementation of the tie-break, because
  with one stop there is nothing to arbitrate — this is the test that can.
- **Its companion on `PreToolUse`**: the same output ends **the turn** and is not recorded as a
  permission `deny` — the tool does not run, no `DENY` verdict is emitted, and the turn's result is the
  `stopReason`, not a blocked-tool message.
- **A `SessionStart` non-stop test** (§5.1, `hooks.md:1009`): a `SessionStart` hook emitting
  `{"continue": false, "stopReason": "…"}` asserts the session **starts anyway**, its first turn runs,
  **and no diagnostic is emitted** — `discarded` is a delivery outcome and takes the silence rule
  (§5.1), where a diagnostic would wrongly report it as an agentao capability gap. A sibling assertion
  pins that a `systemMessage` in the same output **is** delivered, which is what separates the two
  fields on this event. The reading it pins is now a **measurement**, not a choice: 2.1.251 started the
  session, ran the turn, and surfaced the reason nowhere (§0). The flip list stays on file for the
  next contested row, not for this one.
- **A headless `SessionEnd` notice test** (§5.2.1) at `_run_pipeline` level, not resolver level: a
  `SessionEnd` hook exiting 2 under `agentao run --output-format json`, asserting the stderr reaches
  the emitted `RunResult`. Written against today's ordering it fails, because `_emit` runs at
  `run.py:814` and the dispatch at `:815`.
- **A channel-budget test** (§6): an exit-2 `PostToolUse` hook whose **stderr** exceeds the tier-2
  ceiling, asserting the model-bound string is capped and spilled — the case a three-named-fields cap
  does not cover (§6).
- **Table-driven** event × field × exit-code tests over §5.1, so a new row is a data change and a
  missing row is a test failure rather than silence.
- **A namespace-absence test** (§3.3): a `claude-code` hook emitting an `hSO` object that carries
  **both `"hookEventName": "Stop"` and** `agentao.blockingError` asserts the turn is **not** blocked,
  one diagnostic names the unknown key, and the same output under `agentao-v1` (top-level
  `blockingError`) **does** block — pinning that the extension is out of profile-1 rather than
  half-present. The discriminator is not decoration: without it the object fails §5.1's
  `hookEventName` rule and the whole `hSO` is `schema_invalid`, so the test passes while measuring the
  wrong mechanism entirely — the turn is unblocked because the object was rejected, not because the
  namespace is unknown. A second assertion pins the difference: a **sibling** `additionalContext` in
  the same object is still delivered.
- **A negative test per preserved lead** (§10), credential scrub first.
- `uv run python -m pytest tests/` and `uv run ruff check .` — the lint gate is a required CI check
  and a green pytest run is not sufficient (`CLAUDE.md`, "Testing").
