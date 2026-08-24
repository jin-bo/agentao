# Compaction Orchestration — Implementation Plan

> **✅ Implemented — 2026-08-24. All six PRs are merged to `main`, in the dependency order below.**
> Not yet released; targeting 0.4.20.
>
> | | PR | Squashed onto `main` as |
> |---|---|---|
> | PR-1 | #187 | `8d38e0b` |
> | PR-3 | #188 | `05b6c93` |
> | PR-2 | #189 | `2661ff1` |
> | PR-4 | #190 | `a0ba5ca` |
> | PR-5 | #191 | `96340b4` |
> | PR-6 | #192 | `d4c95ad` |
>
> The banner this replaces read "reviewed twelve times, **not authorized for implementation** —
> nothing here has been built". That is recorded here rather than deleted, because seven other
> documents quote it.
>
> **Two deliberate departures from the PR split**, both stated in their PR descriptions: PR-3 also
> landed the `cancel` / host-summary branches of `_run_compaction` (leaving a `decide` parameter
> whose `cancel` is not honoured is a worse half-state than a larger PR, and §4.2's status table is
> explicitly pinned *before* PR-3) and the replay projection of `COMPACTION_SETTLED` (without it
> PR-3 would have removed audit records — the overflow path's unconditional `CONTEXT_COMPRESSED` —
> without adding the replacement; replay schema 1.2 → 1.3).
>
> **Read §§1–8 for the design; read the six PR descriptions for what shipped.** Where they differ,
> the PRs are what is on `main`.

**Date:** 2026-08-23
**Status:** **Implemented** (2026-08-24, unreleased — targeting 0.4.20) — rev 14, twelve maintainer
reviews folded into the body (§9 is the record, not an override; the body is authoritative on its own).
**Anchors:** agentao `main@a996395`, plus two uncommitted working-tree changes that this plan
already accounts for: `COMPRESSION_THRESHOLD` 0.65 → 0.80 (`agentao/context_manager.py:69`) and the
`/context` colour tiers now reading off the constants (`cli/commands/context.py:25-29`).
**Method:** every premise below was read from source and carries an inline `file:line`. Where a
claim is reasoned rather than measured, it says so.

**This document fulfils the `PRECOMPACT_GATE_PLAN.md` placeholder** named — but never written — in
`docs/history/implementation/stop-precompact-hooks-plan.md:1081,1253,1398` and `codex-compaction-vs-agentao.zh.md:294`. §4.4
answers the specific blocker those documents recorded. The file is not named `PRECOMPACT_GATE_PLAN.md`
because its scope is larger than the gate; all seven references have been repointed here. It lives in
`docs/design/` rather than `docs/history/implementation/` because that directory is a frozen snapshot of
the old `docs/implementation/` as it stood at the 2026-06-05 docs reorg — every file in it arrived in that
one commit and nothing has been filed there since. `docs/design/` is where a document carries a **Status**
line across its whole lifecycle, so this plan stays put when it lands and is only restatused.

**Upstream analysis:** `docs/design/pi-mono-compaction-vs-agentao.md` §8 (`PreCompact` is notify-only)
and §9.2 (the circuit breaker has no reset path) are the two P1s this plan closes.

---

## TL;DR

Compaction is orchestrated in four unrelated places today — two threshold tiers, a two-rung
API-overflow ladder, and the manual `/compact` command. They disagree about what a trigger is called,
about whether a failure counts, and about whether "compacted" means history actually changed. Six PRs
converge them behind one coordinator, in two batches:

```
5 compaction entry points
   ↓
CompactionCoordinator
   ├─ circuit-breaker policy
   ├─ PreCompact / host policy
   ├─ ContextManager content transform
   └─ one CompactionOutcome + events
```

`ContextManager` keeps token estimation, cut-point selection and summarization. The coordinator owns
*whether to run, whose summary to take, how to recover, and what to emit*.

**Batch 1 — PR-1 → PR-3 → PR-2 → PR-4.** Closes both P1s. Note this is not the order the PRs are
numbered in: PR-2 depends on PR-1 (§3.3) and would be reworked by PR-3 if it landed first (§4.3).

**Batch 2 — PR-5, PR-6.** P2/P3 quality work; PR-5's priority rose when the threshold moved to 0.80
(§5.1).

**Do not port pi-mono's session tree.** The whole plan stays inside agentao's flat message list.

---

## 1. Target architecture

| Layer | Owns | Does not own |
|---|---|---|
| `ContextManager` | token estimation, `_threshold_token_estimate`, cut-point search, summarization prompt, tool-result clipping | when to run, who may veto, what to emit |
| `CompactionCoordinator` (new) | trigger provenance, breaker **policy**, host policy dispatch, the `CompactionOutcome` **contract**, events | how history is rewritten; breaker **state** (stays in `ContextManager`, §4.3) |
| Entry points (5) | detecting *their own* condition and calling the coordinator | everything else |

The coordinator is a seam, not a rewrite — but it is **not a zero-diff seam**.
`microcompact_messages` keeps its signature and its body. `compress_messages` cannot: today it does
the breaker short-circuit (`context_manager.py:507`), the cut point (`:526`), microcompaction of the
surviving half (`:565`), the crystallize write (`:576-584`), summarization (`:588`), failure counting
(`:590`), the session-summary write (`:598`) and message assembly (`:606-659`) in one call — and PR-4
has to inject the host's text **at the summarization step** (§4.4). So it splits into
`prepare_compaction()` / `commit_compaction()`, with the seam immediately before `:588` — **shaped by
PR-4's needs but landed in PR-3, which is why it is written up in §4.2.1.** What moves
out is the policy that sits *inside* it (the breaker short-circuit at `:507`) and the policy scattered
*around* it (the stand-down gates at `runtime/chat_loop/_compaction.py:32,78`). "Moves out" means
**ownership of the policy**: `prepare_compaction` / `commit_compaction` make no breaker judgement, the
coordinator does — while `compress_messages`, as the legacy wrapper, **keeps an equivalent gate** so
its docstring-and-test-pinned behaviour does not change (§4.3).

---

## 2. Current state — the five entry points, measured

| # | Entry point | Detect | PreCompact dispatch | Mutate | `compaction_type` | `reason` | `is_auto` |
|---|---|---|---|---|---|---|---|
| 1 | Microcompaction | `_compaction.py:30` | `:41` | `:48` `microcompact_messages` | `microcompact` | `microcompact_threshold` | n/a |
| 2 | Threshold full | `_compaction.py:76` | `:94` | `:102` `compress_messages` | `full` | `compression_threshold` | `True` (explicit) |
| 3 | API overflow, rung 1 | `_runner.py:1155` | `:1161` | `:1167` `compress_messages` | `full` | `api_overflow` | **`True` by default — not passed** |
| 4 | API overflow, rung 2 | `_runner.py:1195` | `:1199` | `:1204` `messages[-2:]` | `minimal_history` | `api_overflow_after_compression` | n/a |
| 5 | Manual `/compact` | `compact.py:88` | `:94` | `:103` `compress_messages` | `full` | `manual_cli` | `False` (explicit) |

Three facts fall straight out of that table and drive the whole plan:

1. **`compaction_type` and `reason` already carry the vocabulary** the coordinator needs, and they
   are **already in the hook payload** (`plugins/hooks/_payload.py:162-163`). What is missing is two
   other things: `trigger` is hard-coded to `"auto"` for all five entry points (`:160`), and the
   PreCompact matcher **reads only the `trigger` key** (`_dispatcher.py:206-235`) — so both fields
   arrive but are **not matchable** (§3.1, §4.1).
2. **Entry 3 does not pass `is_auto`.** `compress_messages(self, messages, is_auto: bool = True)`
   (`context_manager.py:480`) means the overflow path is indistinguishable from the threshold path at
   the `ContextManager` boundary: same breaker gate, same failure counter.
3. **Entries 1 and 2 stand down before announcing; entries 3 and 4 do not.** `_compaction.py:32`
   (`microcompact_would_mutate`) and `:78` (`compaction_circuit_open`) both return early *before*
   dispatching the hook or emitting an event, with comments explaining exactly why. The overflow path
   at `_runner.py:1177` emits `CONTEXT_COMPRESSED` unconditionally, immediately after
   `compress_messages` — so with the breaker open it reports a compaction that returned the list
   unchanged, `pre_msgs == post_msgs`.

---

## 3. Verified premises

### 3.1 The `trigger` field is a dead matcher value — worse than "inaccurate"

`ClaudeHookPayloadAdapter.build_pre_compact` hard-codes `"trigger": "auto"`
(`plugins/hooks/_payload.py:160`) for **all five** entry points. Manual `/compact` emits a
`PLUGIN_HOOK_FIRED` replay event saying `"trigger": "manual"` (`cli/commands/compact.py:75`), so the
event stream and the hook payload disagree about the same compaction.

The consequence is larger than a wrong field. Hook rules match on payload fields by regex
(`PluginHookDispatcher._matches`, `plugins/hooks/_dispatcher.py:206`, reached through
`select_matching_rules` at `:166-181`), and a test pins the behaviour:
`test_manual_matcher_does_not_fire_on_auto_payload` (`tests/test_hooks_pre_compact_matcher_trigger.py:35`).

> **A hook registered with `matcher: {"trigger": "manual"}` can never fire, at any entry point,
> in any configuration.** It is not a mislabelled payload — it is a configuration value with no
> reachable producer.

### 3.2 The `trigger` vocabulary must stay `manual|auto`

The first instinct is `CompactionTrigger = manual | threshold | overflow`. **Do not.**
`tests/test_hooks_pre_compact_matcher_trigger.py:47` (`test_alternation_pattern_fires_claude_parity`)
pins `manual|auto` as Claude Code parity. Splitting `auto` would make an existing rule
`{"trigger": "manual|auto"}` stop matching threshold compaction — a silent regression in a user's
config file, which is the exact failure class this plan exists to remove.

The finer granularity already has a home: `compaction_type` carries
`microcompact | full | minimal_history` and `reason` carries the five values in §2's table. So PR-1's
substantive change is **one line — pass the trigger through** — and the enums mostly rename fields
that already exist.

### 3.3 The breaker is half-fixed already, and PR-2 depends on PR-1

`compress_messages` has **two** failure-increment sites, and they already disagree:

- `context_manager.py:540` — no safe split point. **Already `is_auto`-gated.** The comment at
  `:535-539` states the reason: manual `/compact` is user-driven, does not loop, and "the breaker it
  would trip disables *automatic* compaction for the rest of the session with no reset path."
- `context_manager.py:590` — summarization returned nothing. **Unconditional.**

So the policy PR-2 wants is not a new invention; it is the `:540` exemption extended to `:590`, plus
a probe path. The short-circuit itself is at `:507`, above any `is_auto` branch, which is why manual
`/compact` is blocked too. Reset is at `:593` and is unreachable once the breaker is open.

`/clear` does **not** reset it: `cli/commands/reset.py:35` calls `agent.clear_history()`
(`agent.py:1155-1163`), which clears messages, skills, todos, the token anchor and the token counters
— not `_consecutive_compact_failures`.

**PR-2 therefore depends on PR-1.** Making overflow an emergency probe requires the coordinator to
know it *is* overflow, and today it cannot (§2, fact 2).

### 3.4 Do not add a retry wrapper around summarization

Summarization calls `self.llm_client.chat(...)` (`context_manager.py:859`), so it already inherits
the client's retry loop (`llm/client.py:451`): `MAX_RETRY_ATTEMPTS = 5` including the first attempt,
under a `MAX_TOTAL_RETRY_SECONDS = 60.0` wall-clock budget (`llm/_retry.py:27,30`). A second layer
multiplies rather than adds.

### 3.5 The PreToolUse decision path is the precedent for PR-4

`PluginHookDispatcher.dispatch_pre_tool_use_decision` (`plugins/hooks/_dispatcher.py:90-117`) already
implements exactly the shape PR-4 needs: it parses each hook's **stdout** for
`hookSpecificOutput.permissionDecision` (`:353-358`) and merges verdicts in two tiers — **first deny
wins, otherwise first ask wins** (`:102-104`) — stopping the forks once a deny is seen, and
**deliberately not honouring exit-code 2** (docstring `:104-105`).

This matters for PR-4's rollout question. Because the decision rides a JSON field in stdout, a legacy
observe-only script that prints nothing is silently `allow`. **The "hook v2 / explicit opt-in" gate
the original plan proposed is therefore unnecessary**, provided cancel keys off the JSON shape only.
It would only be needed if cancel keyed off exit codes — which is the approach the precedent already
rejected.

**But "silence is `allow`" only proves half of it.** It proves scripts that print **nothing** are
safe; it says nothing about a private script writing `hookSpecificOutput` for some other purpose.
That is why §4.4.1 does not reuse `permissionDecision` but takes a key that has never existed,
`compactionDecision` — that is the complete argument for needing no gate. The precedent also covers
only **half** of what PR-4 needs: `dispatch_pre_compact` is a side-effect-only `_dispatch_lifecycle`
today (`:158-164`) and does not parse stdout at all, so that parsing path has to be written fresh
(§4.4.1).

---

## 4. Batch 1 — both P1s

Sequence is **PR-1 → PR-3 → PR-2 → PR-4**. The labels keep their original numbers for traceability;
the sequence column is what to build in.

| Seq | PR | What | Acceptance |
|---|---|---|---|
| 1 | PR-1 | Fix the existing PreCompact contract | All five entry points produce a payload whose `trigger` matches reality; a `{"trigger": "manual"}` rule fires on `/compact` and only on `/compact` |
| 2 | PR-3 | Unify result and events, **stand up `CompactionCoordinator` and land the mechanical base it needs** | All five entries return one `CompactionOutcome` *through the coordinator*; `CONTEXT_COMPRESSED` only when `status == "success"`; `compress_messages`'s legacy semantics unchanged apart from the one item §4.3 names |
| 3 | PR-2 | Recoverable circuit breaker | Threshold attempts pause after 3 failures; manual and overflow act as probes; a successful probe resets |
| 4 | PR-4 | Host control plane (**switched on over already-wired paths only**) | Cancel and provide-summary; no arbitrary message-list replacement; **migrates no entry** |

**PR-3 stands the coordinator up, wires it in, and carries the whole mechanical base — nobody owned
this before.** PR-1 only fixes the payload and introduces no new object; but PR-3's requirement that
"every entry returns one `CompactionOutcome`" forces all five through one path. That requirement drags
a whole set of pieces into PR-3, because **without any one of them PR-3 cannot meet its own
acceptance**: only `_run_compaction` can produce an authoritative `status` for the `full` path, and §6
forbids inferring one from message identity or counts. So PR-3's scope is:

1. The neutral type module `agentao/compaction/types.py` and `coordinator.py` (end of §4.2.1).
2. Splitting `compress_messages` into `prepare_compaction` / `commit_compaction`, plus the private
   `_run_compaction(..., decide=None)` (§4.2.1) — **not** the path where `decide` is actually supplied.
3. The legacy wrapper's `reason` mapping (`is_auto=True` → `compression_threshold`, `is_auto=False` →
   `manual_cli`) and `apply_minimal_history` (§4.2.1, §4.4.3).
4. Rewiring all five entries to the coordinator **atomically**, entry 3 included, handing over its real
   `api_overflow`.
5. `CompactionOutcome`, `COMPACTION_SETTLED`, and moving in the two stand-down gates at
   `_compaction.py:32,78`.

PR-4 therefore **migrates no entry at all**: it only switches `decide` on over already-wired paths — the
command-hook decision protocol, `compaction_controller=`, `provide_summary`, the cancellation semantics
and the suppression latch. PR-2 moves the breaker policy in; PR-5 / PR-6 do not touch it.

**Name the cost: PR-3 is a large PR, and it alone carries the second behaviour change §4.3 names**
(crystallize no longer running on a failed summarization). What that buys is that every PR can be
accepted on its own — leave the split to PR-4 and PR-3 can only fake a `status` out of counts or
message identity, which is the very defect this plan exists to fix.

### 4.1 PR-1 — fix the trigger contract first

- `build_pre_compact(..., trigger: str, custom_instructions: str = "")` takes the provenance
  explicitly instead of hard-coding `"auto"` (`_payload.py:160`).
- **Vocabulary — `trigger` stays `manual | auto`** (§3.2). Entries 1–4 pass `auto`, entry 5 passes
  `manual`.
- The two existing fields are typed rather than replaced:
  - `CompactionKind = microcompact | full | minimal_history` (today's `compaction_type`)
  - `CompactionReason = microcompact_threshold | compression_threshold | api_overflow | api_overflow_after_compression | manual_cli` (today's `reason`)
- **Typed is not matchable.** `_matches` compares exactly one key for PreCompact — `trigger`
  (`_dispatcher.py:206-235`) — so once `CompactionKind` / `CompactionReason` land, a host still
  **cannot** write a matcher against them. This PR does **not** extend `_matches`: Claude Code's
  PreCompact matcher is trigger-only, so extending it is an agentao-only extension needing its own
  item and its own docs. Recorded as a consequence, not an oversight.
- All five dispatch sites in §2's table are covered, plus the `PLUGIN_HOOK_FIRED` emit at
  `_hook_dispatch.py:200` and `cli/commands/compact.py:75` so the event and the payload agree.
- Update the en/zh plugin docs and the matcher contract together — they are twins and drift.

**Regression risk to test explicitly:** an existing rule `{"trigger": "manual|auto"}` must keep firing
on every entry point after the change.

### 4.2 PR-3 — one outcome, honest events

```python
@dataclass(frozen=True)
class CompactionOutcome:
    status: Literal["success", "cancelled", "failed", "skipped"]
    trigger: CompactionTrigger
    kind: CompactionKind
    reason: CompactionReason
    messages: list[dict]
    pre_tokens: int | None
    post_tokens: int | None
    detail: str | None
```

`pre_tokens` is `| None`, but **the previous revision's reason for it was wrong**: the full path
*does* compute it today — `pre_tokens = self.estimate_tokens(messages)` at `context_manager.py:587`,
inside `compress_messages`, which is also what entry 3 goes through. The two real reasons:

1. **`minimal_history` (entry 4) estimates nothing at all.** That branch (`_runner.py:1195-1210`) makes
   no `estimate_tokens` call whatsoever. Requiring `pre_tokens` there really would add a full-history
   estimate — on a path where the context has already blown up and the request has just been rejected
   twice.
2. **Two differently-measured `pre_tokens` already exist.** `context_manager.py:587` measures
   `messages` (**excluding** the system prompt); `_compaction.py:46,100` measures
   `messages_with_system` (**including** it). One required `int` would quietly merge two units into
   one field.

Pinned: **`CompactionOutcome.pre_tokens` and `CompactionDecisionContext.pre_tokens` are both
`int | None`, measured **excluding the system prompt***, the same value prepare gets from `:587`.
`_emit_context_compressed` is already declared `pre_tokens: Optional[int] = None` (`agent.py:1118`), so
this introduces no new shape.

This removes two guesses:

- Manual `/compact` currently infers success by sniffing for a freshly prepended `[Compact Boundary]`
  marker on `messages[0]` (`cli/commands/compact.py:26-40`) — a heuristic that exists only because
  `compress_messages` returns a bare list.
- The overflow path emits success unconditionally (`_runner.py:1177`), including when the breaker
  made `compress_messages` a no-op.

**This is not new work — it finishes an incomplete fix.** PR #181 gave the *threshold* path exactly
this treatment; the stand-down comments at `_compaction.py:32,78` describe the same defect
("announcing the compaction would fork a PreCompact hook subprocess per iteration for something that
never happens — and emit a `CONTEXT_COMPRESSED` reporting pre == post"). The overflow path was
missed.

**Status mapping — pinned before PR-3, because it is a publicly observable contract.**

| Situation | Behaviour today | `status` | Counts against breaker |
|---|---|---|---|
| Breaker already open (`:507`) | returns unchanged | `skipped` | no |
| `len(messages) < 5` (`:517`) | returns unchanged | `skipped` | no |
| Microcompaction has no targets (`microcompact_would_mutate` false) | stands down (`_compaction.py:32`) | `skipped` | no |
| Cancelled by host / hook | new | `cancelled` | no |
| No safe split point (`:528`) | returns unchanged + increments (already `is_auto`-gated, `:540`) | `failed` | per PR-2's rule |
| Summarization returned nothing (`:589`) | returns unchanged + increments **unconditionally** (`:590`) | `failed` | per PR-2's rule |
| Host summary invalid *and* built-in also failed | new | `failed` | per PR-2's rule (§4.2.1) |
| Completed normally | returns a new list | `success` | resets (`:593`) |
| Suppression latch hit (§4.4.4) | new | `skipped` | no |

The criterion in one line: **`skipped` = nothing was attempted; `failed` = attempted and did not
succeed; `cancelled` = vetoed.** `skipped` never counts against the breaker — which is exactly what
`:507` and `:517` do today, not a new rule.

**`skipped` is silent: it emits no event.** The table has **four** `skipped` rows (breaker open,
`len < 5`, microcompaction with no targets, latch hit), and **three** of them (breaker open,
microcompaction with no targets, latch hit) re-trigger on **every iteration**, so emitting one event
each is a fresh event storm — exactly what the stand-down comments at `_compaction.py:32,78` exist to
prevent, and those paths emit nothing today either. So the terminal event fires for
`success | cancelled | failed` only.

**The terminal event — its name and payload are pinned too.** Add
`EventType.COMPACTION_SETTLED = "compaction_settled"` next to `CONTEXT_COMPRESSED`
(`transport/events.py:36`). Payload:

```json
{"trigger": "manual|auto", "kind": "microcompact|full|minimal_history",
 "reason": "...", "status": "success|cancelled|failed",
 "pre_msgs": 0, "post_msgs": 0,
 "pre_tokens_history": null, "post_tokens_history": null,
 "duration_ms": 0, "detail": null}
```

**The token fields are deliberately named `*_tokens_history`, because they are not the same unit as
the old event's.** Both tokens on the Outcome **exclude the system prompt**: `pre_tokens` comes from
`context_manager.py:587` and `post_tokens` from `:641`
(`post_tokens = self.estimate_tokens(result)`). The old event's `pre_est_tokens` / `post_est_tokens`
measure `messages_with_system` and therefore **include** it (`_compaction.py:46,64`, `:100,121`;
`cli/commands/compact.py:98-100,116`). Two different names so the two units **cannot** be wired to
each other by accident.

**The per-path "populated / null" contract — two tables, both pinned in PR-3.** The governing rule in
one line: **this plan adds no new `estimate_tokens` call**, so both fields are populated only where a
history-only estimate **already exists today**.

Old event `CONTEXT_COMPRESSED` (system-inclusive, **unchanged before and after PR-3**):

| Entry | `pre_est_tokens` | `post_est_tokens` |
|---|---|---|
| 1 Microcompaction | `_compaction.py:46` | `:64` |
| 2 Threshold full | `:100` | `:121` |
| 3 API overflow, rung 1 | **`null`** — not passed today (`_runner.py:1177`) | **`null`** |
| 4 API overflow, rung 2 | **`null`** (`_runner.py:1208-1213`) | **`null`** |
| 5 Manual `/compact` | `cli/commands/compact.py:98-100` | `:116` |

Entries 3 and 4 **stay `null`** — this change does not "fill them in while we're here", because doing
so means two new full-history estimates on exactly the path where the context has already blown up
(§4.2's two opening reasons).

New event `COMPACTION_SETTLED` (system-exclusive):

| `kind` × `status` | `pre_tokens_history` | `post_tokens_history` |
|---|---|---|
| `full` / `success` | `:587` | `:641` |
| `full` / `cancelled` | `:587` (prepare already computed it) | `null` — history did not change, there is no "post" |
| `full` / `failed` (summary empty / host summary invalid and built-in also failed) | `:587` | `null` |
| `full` / `failed` (`no_safe_split`, `:528`) | `null` — `:587` runs after it | `null` |
| `microcompact` / any | `null` | `null` |
| `minimal_history` / any | `null` | `null` |
| any / `skipped` | — | — (no event emitted) |

**Microcompaction being `null` on both is deliberate.** It runs on **every iteration** inside the
55–80% band, and adding two full-history system-exclusive estimates there is precisely the cost the
comment at `_compaction.py:50-53` works to avoid ("a full re-encode of the entire history on every
iteration spent in the microcompact band — precisely when it is most expensive"). For microcompaction
tokens, read the old event and its system-inclusive unit.

**Compatibility — the "superset" claim was wrong and is withdrawn.** The old event's keys are
`type` / `reason` / `pre_msgs` / `post_msgs` / `pre_est_tokens` / `post_est_tokens` / `duration_ms`
(`replay/observability.py:47-55`): three of them are **named differently** in the new event. The
mapping, to be wired line by line at implementation time:

| `CONTEXT_COMPRESSED` (unchanged) | `COMPACTION_SETTLED` (new) | Unit |
|---|---|---|
| `type` | `kind` | same value, renamed |
| `reason` | `reason` | same |
| `pre_msgs` / `post_msgs` | same names | same |
| `pre_est_tokens` / `post_est_tokens` | `pre_tokens_history` / `post_tokens_history` | **different unit**: old includes the system prompt, new excludes it |
| `duration_ms` | same name | same |
| — (absent) | `trigger` / `status` / `detail` | new |

**`CONTEXT_COMPRESSED`'s payload does not change by a single key, and both token fields keep the
system-inclusive unit.** They are never sourced from the Outcome's history-only numbers — that would
quietly change a public field's semantics, which is precisely the case `transport/events.py:57-58`
requires a `schema_version` bump for.

`schema_version` therefore **does not move**, on two separately-standing grounds: no field of
`CONTEXT_COMPRESSED` changes shape or semantics; and `COMPACTION_SETTLED` is a **new type**, where
"adding is not such a change" is the established rule (`transport/events.py:59-60`).

**One thing does change, and it must be named in the PR description and the CHANGELOG:
`CONTEXT_COMPRESSED`'s emission condition.** Today it fires even when the breaker made compaction a
no-op (`_runner.py:1177`); after PR-3 it fires only on `success`. That is turning a lying event into a
truthful one rather than a payload-contract break — but it is observable, so it gets said out loud.

Both events record counts, tokens, duration and a failure class — never raw context.

#### 4.2.1 The seam — `compress_messages` splits into prepare / commit

> **This seam belongs to PR-3, which is why it sits here and not under §4.4.** Its **shape** is dictated
> by what the host control plane needs — the summarization step must be replaceable (§4.4) — but its
> **landing time** is dictated by PR-3's acceptance: PR-3 requires "five entries returning one
> `CompactionOutcome` through the coordinator", and only `_run_compaction` can produce an authoritative
> `status` for the `full` path, while §6 explicitly forbids inferring it from message identity or
> counts. **Only three parts of this section are PR-4's**: the path where `decide` is actually supplied,
> the `cancel` and two "host summary" rows, and the `detail` composition rule. The full split is in the
> paragraph under §4's overview table.

The host's summary has to be injected at the summarization step, so `compress_messages`
(`context_manager.py:477-659`) must come apart. The seam is immediately before `:588`
(`summary = self._summarize_messages(to_summarize)`):

| Phase | Does | Side effects allowed |
|---|---|---|
| `prepare_compaction(messages, *, trigger, kind, reason) -> PrepareResult` | `len < 5` guard (`:517`), cut point (`:526`), microcompaction of the surviving half (`:565`), pinned extraction (`:568`), recently-read extraction (`:574`), summary-input assembly (`_format_for_summary`) | `last_microcompact_mutated` (`:408`) + one log line (`:411-413`) only; **no SQLite write, no touch of `agent.messages`** |
| controller / hook decision | `allow` / `cancel` / `provide_summary(text)` | none |
| summarize | `allow` runs `_summarize_messages` (`:588`); `provide_summary` **skips that LLM call** | one LLM request |
| `commit_compaction(prep, summary) -> list[dict]` | crystallize write (`:576-584`), session-summary write (`:598`), message assembly (`:606-659`) | SQLite writes + history rewrite |
| coordinator | breaker **policy** (allow / pause / probe), hook dispatch, composing `decide`, building the gate / `microcompact` / `minimal_history` results, emitting every event, writing the suppression latch | events + latch |
| `_run_compaction` (private to `ContextManager`, shared by both callers) | strings prepare → decide → summarize → commit, and does the **three counting points** at this level | counter |

**prepare has to be able to say "did not happen", so it returns a union.** The previous revision had
it return `PreparedCompaction`, but the two early exits in that row (`len < 5` at `:517`, no safe
split at `:528`) have no `PreparedCompaction` to return:

```python
@dataclass(frozen=True)
class PrepareRejected:
    status: Literal["skipped", "failed"]
    detail: str                # "history_too_short" | "no_safe_split"
    counts_as_failure: bool    # True only for no_safe_split

PrepareResult = PreparedCompaction | PrepareRejected
```

- `len < 5` (`:517`) → `PrepareRejected("skipped", "history_too_short", False)`
- No safe split (`:528`) → `PrepareRejected("failed", "no_safe_split", True)`

**The three counting points must live in one function — but that function is neither the coordinator
nor commit.** The previous revision put "failure counting and reset" in the commit row, and that was a
hole: **commit never runs when summarization returns nothing** (`:589` returns first), so today's
increment at `:590` would silently disappear. But moving them onto the coordinator does not work
either — `ContextManager.compress_messages()` is a legacy method that can be called on its own and has
**no coordinator** in hand, and the counter `_consecutive_compact_failures` (`:91`) stays on
`ContextManager` by §4.3's ruling.

Landing: add a **private `ContextManager` method** `_run_compaction`, shared by both callers:

```python
class ContextManager:
    def _run_compaction(
        self, messages, *, is_auto: bool, reason: CompactionReason,
        decide: Callable[[CompactionDecisionContext], CompactionDecision] | None = None,
    ) -> CompactionOutcome: ...
```

**`trigger` and `kind` are not passed in — they are derived, not inputs.** `trigger`'s vocabulary is
exactly `manual | auto` (§3.2), and `is_auto` is the same fact in a different encoding: of today's three
call sites, `_compaction.py:102` and `_runner.py:1167` pass `True` (→ `auto`) and
`cli/commands/compact.py:103` passes `False` (→ `manual`) — a one-to-one match. So
**`trigger = "auto" if is_auto else "manual"`**, and `kind` is constantly `full` (`_run_compaction`
handles only that kind — see the end of this section). `is_auto` stays in the signature rather than
being replaced by `trigger` because §4.3 pins the legacy `compress_messages(messages, is_auto=...)`
signature, and both callers share this layer.

**It therefore returns `CompactionOutcome` directly; the intermediate `_CompactionRun` is deleted.**
The previous revision justified that intermediate type with "the Outcome also carries `trigger` /
`kind` / `reason`, and `_run_compaction` does not know the trigger provenance" — which does not hold:
`reason` is a parameter and `trigger` / `kind` are derived as above, so it knows all eight fields. Once
it knows all of them, the extra type is pure copying cost — and **that copying produced one defect in
each of rev 7, 8 and 9** (a wrong field mapping, misplaced `detail` ownership, an over-reaching
`counted_failure`). Deleting it beats maintaining the mapping table. `pre_msgs` / `post_msgs` are
unaffected: they were never `CompactionOutcome` fields and go only into the event payloads.

It strings together `prepare → (decide) → summarize → commit` and does three things **at that level**:

1. `PrepareRejected.counts_as_failure` is true → increment (gated by PR-2's `is_auto` / trigger rule).
2. Summarization returned nothing → increment (same gate).
3. commit succeeded → reset (`:593`).

All three sit inside `_run_compaction`, so none is missed by commit's early return and none needs a
coordinator present.

**The per-branch mapping.** `_run_compaction` has to state outright whether the run succeeded rather
than letting the layer above go back to guessing from message identity or count — the exact criterion
§6 already rules out. Of `CompactionOutcome`'s eight fields, `trigger` / `kind` / `reason` are constants
or parameters (above), so the table lists only the four that vary per branch, plus its internal
counting behaviour:

| Branch | `status` | `messages` | `pre_tokens` | `post_tokens` | `detail` | Counter |
|---|---|---|---|---|---|---|
| `PrepareRejected("skipped", "history_too_short")` (`:517`) | `skipped` | the original object | `None` | `None` | `history_too_short` | untouched |
| `PrepareRejected("failed", "no_safe_split")` (`:528`) | `failed` | the original object | `None` | `None` | `no_safe_split` | +1, gated |
| `decide` returned `cancel` | `cancelled` | the original object | `:587` | `None` | **no internal reason** (`None`, see below) | untouched |
| Summarization returned nothing (`:589`) | `failed` | the original object | `:587` | `None` | `summary_empty` | +1, gated |
| Host summary invalid → built-in succeeded (§4.2.1) | `success` | a new list | `:587` | `:641` | `host_summary_rejected:<check>` | reset |
| Host summary invalid → built-in also failed | `failed` | the original object | `:587` | `None` | `host_summary_rejected:<check>+summary_empty` | +1, gated |
| Host summary valid → adopted | `success` | a new list | `:587` | `:641` | `None` | reset (`:593`) |
| Completed normally (built-in summary) | `success` | a new list | `:587` | `:641` | `None` | reset (`:593`) |

The `detail` column above carries the **internal reason**; `_run_compaction` composes it with the
decision's `reason` into the final value **before returning**. The rule: internal reason + `; ` + the
decision's `reason` (§4.4.2); if either is absent only the other remains; neither present → `None`.
This is what tells the two `success` rows above apart when a `reason` is given, and what makes
`CompactionDecision.reason`'s promise of "feeds `CompactionOutcome.detail` and the log" actually hold.

**But that decision `reason` does not exist on two paths, so the rule cannot be written as
`decision.reason`.** First, `decide` may be `None` — the legacy wrapper passes exactly that (see the
end of this section) — so there is no decision object at all. Second, the **first two rows** of the
table return during prepare, **before the decision step is ever reached** (the four-stage order is
prepare → decide → summarize → commit). Pinned: start from `decision_reason: str | None = None` and
**assign it only once `decide` has actually been called and returned a usable decision**; those two
rows then get a `detail` equal to the internal reason itself, with no exception carved out of the
composition rule.

**The `cancel` row's internal reason being `None` is not a slip.** The previous revision wrote it as
"the hook's / controller's `reason`" — which *is* `decision.reason`, so running it through the
composition rule again would yield `reason; reason`. Leaving the internal reason empty makes the rule
produce `decision.reason` exactly once, and `cancel` needs no exception to the rule.

**The composition has to happen inside `_run_compaction`; it cannot be left to the coordinator.** That
function is the only holder of a `CompactionDecision` **instance** — what the coordinator hands over is
a `decide` **callable**, and the actual call happens after prepare and before summarize (the four-phase
table above), so the coordinator never has that return value in hand. The previous revision's
"the coordinator appends it after the internal reason with `; `" contradicted the very next claim that
`detail` is "copied straight into `CompactionOutcome`". Now `detail` is final the moment it leaves
`_run_compaction`, which is what makes "copied straight across" literally true.

While here, pin what `decide` contains: it is a closure composed by the coordinator with **both control
layers inside it** — command hooks first (§4.4.1, first-cancel-wins within that layer), then, if all
allow, `compaction_controller` (§4.4.2) — returning **one** merged `CompactionDecision`. So the
`cancel` row's `detail`, composed by the rule above, is that merged result's `reason`.

The table's "Counter" column describes what `_run_compaction` does **internally**; it is not a returned
field — see below.

`_run_compaction` **never** returns `skipped` for an open breaker or a latch hit — both are stopped
**above** it and never reach it. The only `skipped` it can produce is `history_too_short`.

`_run_compaction` returns `CompactionOutcome` itself — **no intermediate type, no field copying** (rev
9's `_CompactionRun` is deleted; reasoning above). What remains here are three things that were once
written wrong and are worth keeping on the record:

- **The `counted_failure` field no longer exists.** rev 8 added it and then said the coordinator uses
  it to decide "whether this probe counted and how to render the breaker in `/context`" — which hands
  breaker-state ownership back to the coordinator, contradicting **both** §4.3's "the single source of
  truth is `ContextManager`" and this section's own closing "the coordinator never touches the
  counter". It was redundant anyway: counting and reset already happen
  inside `_run_compaction`, the coordinator needs only `status` and `compaction_circuit_open` (`:423`)
  to apply probe policy, and `/context` has always rendered from
  `get_usage_stats()['circuit_breaker_failures']` (`:1223`), never from a return value.
- **`trigger` / `kind` / `reason` need no filling in by the coordinator** — `reason` is a parameter, `trigger` is derived from `is_auto`, and `kind` is constantly `full` (above). The previous revision's "added by the coordinator; `_run_compaction` does not know the trigger provenance" was wrong and is withdrawn.
- **`pre_msgs` / `post_msgs` are not `CompactionOutcome` fields at all** (see §4.2's definition). They
  go only into the **event payloads** (both `CONTEXT_COMPRESSED` and `COMPACTION_SETTLED` carry them),
  computed by the coordinator with two `len()` calls and no token estimate.

**`_run_compaction` handles `kind == full` only.** The other two kinds do not go through it, because
they have nothing to share: microcompaction and `minimal_history` **call no summarizer, write no
SQLite, and never touch the breaker counter**. Forcing them through the same function would make every
field optional and buy no reuse. `PrepareResult` therefore stays the two-arm
`PreparedCompaction | PrepareRejected`.

**But "does not go through `_run_compaction`" is not the same as "the coordinator rewrites history
itself" — writing it that way crossed §1's layering boundary.** §1's table lists "how history is
rewritten" squarely in the coordinator's **does not own** column, yet the previous revision had it
produce `PreparedMicrocompact` / `PreparedMinimalHistory` and run the short transforms itself, with
microcompaction reaching into the private `_microcompactable_indices` (`:348`). §1 is **not** amended
here — that layering is the plan's foundation, and two two-line transforms are not worth an exception.
The fix is two narrow method pairs on `ContextManager`, with the coordinator only orchestrating:

```python
class ContextManager:
    # kind == microcompact
    def prepare_microcompact(self, messages) -> PreparedMicrocompact: ...
    #   the apply half already exists: microcompact_messages(messages) (`:377`), signature and body untouched
    # kind == minimal_history
    def prepare_minimal_history(self, messages, *, keep_tail: int = 2) -> PreparedMinimalHistory: ...
    def apply_minimal_history(self, messages, *, keep_tail: int = 2) -> list[dict]: ...
```

- `prepare_microcompact` wraps `_microcompactable_indices` (`:348`), so **the coordinator no longer
  touches any private member**; it returns only `tool_results_to_clip = len(targets)` and
  `pre_tokens = None`. The existing public predicate `microcompact_would_mutate` (`:365`) stays — it is
  the cheap pre-check at `_compaction.py:32`, and `prepare_microcompact` expresses the same thing as
  `tool_results_to_clip > 0`.
- `apply_minimal_history` moves the `messages[-2:]` slice that lives at `_runner.py:1204` today behind
  `ContextManager`. It is one line, but **by §1 that is where a content transform belongs**, and it
  gives the ladder's last rung a named, unit-testable seam. **Of this pair, `apply_minimal_history`
  belongs to PR-3** (entry 4 needs it the moment it is rewired); the two `prepare_*` methods belong to
  PR-4 — they exist only to populate `CompactionDecisionContext`, which has no reader until then.

So what the three kinds share is the **`CompactionOutcome` contract** and the **decision step** —
**not one builder** (below); history rewriting always happens inside `ContextManager`.

**The dependency direction is pinned: `ContextManager` does not import, hold, or know about
`CompactionCoordinator`.** The control plane arrives as one optional `decide` callback, nothing more.
The coordinator sits **above** `ContextManager`: it decides breaker policy (allow / pause / probe),
dispatches hooks, composes the hook and controller verdicts into a single `decide`, builds **its own
three classes of result** (gate short-circuit / `microcompact` / `minimal_history`, below), emits
**every** event, and writes the suppression latch — but it **never touches the counter**.

**Which means the shared types have to live in a neutral module — that is a precondition of the
sentence above.** `_run_compaction` has to `return CompactionOutcome(...)`, and `decide`'s parameter
and return types are `CompactionDecisionContext` / `CompactionDecision`. Define any of those in the
coordinator's module and `ContextManager` must import it, voiding the direction rule on the spot.
Pinned: a new `agentao/compaction/` package — `types.py` holds only types (the three vocabulary
aliases, `CompactionOutcome`, `CompactionDecisionContext`, `CompactionDecision`,
`CompactionController`) and **imports nothing but the standard library**; `coordinator.py` holds
`CompactionCoordinator`. Both `context_manager.py` and `coordinator.py` import from `types.py`, so
both edges point down. Two attached constraints:

- **`agentao/compaction/__init__.py` must not re-export `coordinator`.** The public subset
  (`compaction_controller=` is a public constructor kwarg; `Agentao.compact()` returns a
  `CompactionOutcome`) is re-exported from `agentao.host` — and if `agentao.host` drags
  `coordinator` → `context_manager` → the LLM stack in through that `__init__`, it trips import-layering
  rule 5 (`tests/test_import_layering.py:471`, "`import agentao.host` must not drag in the runtime or
  the LLM stack").
- **`PreparedCompaction` / `PrepareRejected` / `PreparedMicrocompact` / `PreparedMinimalHistory` stay
  out of `types.py`.** They are the private prepare → commit snapshots (marked **private** in this
  section); they live next to `context_manager.py`, and the coordinator only reaches them through the
  narrow methods — the types themselves never need to surface on the public boundary.

So the two callers land cleanly:

- `compress_messages(messages, is_auto=True)` = breaker gate + `_run_compaction(..., decide=None)`,
  returning `outcome.messages`. **Identical to today except for the two deliberate changes named in
  §4.3** (failure counting on the manual path; when crystallize runs on a failed summarization). It has
  no `reason` parameter of its own — §4.3 pins the old signature — so the wrapper derives one from
  `is_auto`, pinned: **`is_auto=True` → `compression_threshold`, `is_auto=False` → `manual_cli`**.
- `CompactionCoordinator` = policy + observability + `_run_compaction(..., decide=<composed>)`.

**That legacy mapping serves out-of-tree callers only — which is why entry 3's rewiring has to land
atomically with it, inside PR-3.** Entry 3 (`_runner.py:1167`) is precisely the call site riding the
wrapper's `is_auto=True` default today (§2's table, row 3), yet its true `reason` is `api_overflow` —
that is what the hook dispatch two lines above it says (`_runner.py:1161`). If `reason` becomes
mandatory while entry 3 is still on the wrapper, it would report `compression_threshold` and contradict
the hook payload it just emitted. PR-3's acceptance is already "all five entries through the
coordinator", so this is not an extra condition — it just spells out what *atomic* means: **the same PR
that makes `reason` mandatory also rewires entries 2 / 3 / 5 to pass their real `reason` through the
coordinator.** After that, the only thing left to hit this mapping is an embedder calling
`compress_messages()` directly from outside the tree — and such a caller has no better information to
offer anyway.

`commit_compaction` only writes and assembles, returning the new `list[dict]`. **Who builds the
`CompactionOutcome` is split by kind**: the `kind == full` result is built by `_run_compaction` (it
holds all eight fields, above); the gate short-circuits (breaker open, latch hit), `microcompact` and
`minimal_history` are built by the coordinator — those three never enter `_run_compaction` at all (see
§4.4.3 and the "only handles `kind == full`" paragraph above). **Events, by contrast, are always
emitted by the coordinator**, whoever built the outcome, so the "emit or stay silent" criterion lives
in exactly one place (§4.2's silent-`skipped` rule).

**The breaker query is not in prepare.** The previous revision listed it inside
`prepare_compaction`, which contradicts §4.3's "the coordinator owns policy". Landing: the coordinator
reads `compaction_circuit_open` (`:423`) **before** calling prepare and decides allow / pause / probe;
`prepare_compaction` itself makes **no** breaker judgement. The gate in front of the legacy
`compress_messages()` is kept by that wrapper (§4.3), not by prepare.

**prepare is also not "zero side effects" — writing that was wrong.** It calls
`microcompact_messages` (`:565`), which writes `last_microcompact_mutated` (`:408`) and logs one line
(`:411-413`). Both are acceptable: the flag's only reader is the microcompaction entry point, and it
reads immediately after its own call (`_compaction.py:49`), so a cancelled prepare cannot mislead
anyone. The real red line is the next paragraph.

**`crystallize_user_messages` must move from prepare to commit.** It writes to the store *before*
summarization today (`:576-584`, call at `:582`); left in prepare, a `cancel`led compaction would
satisfy "history is byte-identical" while **having already changed the memory store**.
`save_session_summary` (`:598`) is the same. **The side effects that must not happen before a cancel
are exactly those two SQLite writes, plus any assignment to `agent.messages`.** Everything else
(token estimation, string assembly) is pure computation and may happen freely.

**Two types, not one.** The previous revision collapsed them into a single `CompactionPreparation`
carrying only indices, counts and budgets — which leaves `commit_compaction` without what it needs.

`PreparedCompaction` (**private**, the prepare → commit snapshot):

```python
@dataclass(frozen=True)
class PreparedCompaction:
    trigger: CompactionTrigger
    kind: CompactionKind
    reason: CompactionReason
    is_auto: bool
    split_index: int
    to_summarize: list[dict]     # messages[:split_index] (`:558`)
    to_keep: list[dict]          # the already-microcompacted surviving half (`:565`)
    pinned: list[dict]           # (`:568`)
    recently_read: list[str]     # (`:574`)
    summary_messages: list[dict] # to_summarize minus the [PIN] entries (below)
    summary_input: str           # the result of _format_for_summary(summary_messages)
    pre_tokens: int              # estimate_tokens(messages), **excluding the system prompt** (`:587`)
```

Commit needs every one of these: `crystallize_user_messages(to_summarize)` (`:582`),
`len(to_summarize)` / `len(to_keep)` for `_last_compact_stats` (`:646-647`), and
`result = [boundary, summary] + file_hint + pinned + to_keep` (`:639`). **"The host can read
`agent.messages`" does not resolve commit's data dependency** — that sentence answers what the *host*
needs, not what *commit* needs.

`CompactionDecisionContext` (**public, redacted, in-process only, for the controller alone**):

```python
@dataclass(frozen=True)
class CompactionDecisionContext:
    trigger: CompactionTrigger
    kind: CompactionKind
    reason: CompactionReason
    pre_tokens: int | None            # excludes the system prompt; **set only when kind == full**
    messages_to_summarize: int        # counts, never the text
    messages_to_keep: int
    recently_read_files: tuple[str, ...]
    summary_input_budget: int | None  # set only when kind == full
    max_summary_tokens: int | None    # same
    can_provide_summary: bool          # True only when kind == full
    tool_results_to_clip: int | None   # set only when kind == microcompact (see §4.4.3's per-kind table)
```

**Command hooks do not get this object — they get the existing PreCompact payload.** That flat
Claude-compatible payload is built by `build_pre_compact` (`plugins/hooks/_payload.py:145-163`, in the
form PR-1 fixes `trigger` into). Three reasons: `_matches` matches on its **top-level fields**
(`_dispatcher.py:206-235`), so a different shape means rewriting the matcher contract; hooks only
decide `allow` / `cancel` and have no use for the budget fields; and defining a second wire schema
means a second public contract to maintain forever. **`CompactionDecisionContext` never goes on the
wire and is never serialized** — it is an in-process object only `compaction_controller` ever sees.

**No raw message text in the public object.** The reason is **not** "it copies the whole history" —
that sentence was wrong; putting a list reference in a dataclass shares the reference and copies
nothing. The two real reasons: it is a redaction boundary, so what a controller or a command hook sees
should be a small versioned set of fields defined by this plan rather than the whole conversation; and
handing over the text is an invitation to mutate it, which the top of this section already rules out.
A host that genuinely needs the text reads `agent.messages` — its existing channel, with its existing
consequences.

**Budget and validation for a host summary.** `max_summary_tokens = _summary_input_budget() // 2`,
the same half `_clip_carry_summary` gives the carried summary today (`:983-993`), for the same
reason: this text comes back as `<previous-summary>` in the next summarization input, and past half
it starves the live tail. Reject four shapes before committing: empty string, non-`str`, over
`max_summary_tokens`, and containing `SUMMARY_END_MARKER` (which would break the next round's carry
stripping, `:904-914`).

**An invalid host summary — degrade and retry first, then decide the terminal state; never "failed
and also continuing".** The previous revision said both `status="failed"` **and** "retry through the
built-in summarizer"; one operation cannot be both. The fixed ladder:

1. Validation fails → **reject that text**, record which check failed in `detail`, log a warning.
   **This is not a terminal state.**
2. Immediately run the built-in summarizer once as if `allow` (`:588`).
3. Built-in succeeds → `status="success"`, with `detail` retaining "host summary rejected + reason".
   The host sees it was rejected and the compaction still completes.
4. Built-in also fails (returns empty) → `status="failed"`, counted by PR-2's ordinary rule. That is:
   **what the breaker counts is always the built-in summarizer's failure**, regardless of whether a
   host summary was offered; an invalid host summary **never counts on its own**.

So a bad controller costs at most one extra built-in summarization per compaction and can never
disable auto-compaction for the session in three calls — which is exactly what this rule exists to
prevent.

### 4.3 PR-2 — breaker as a recoverable state machine

Semantics:

- Three consecutive failures pause **threshold** attempts only — enough to stop the per-iteration
  re-entry the `:535-539` comment describes.
- Manual `/compact` is always allowed as a half-open probe.
- An API overflow that has already happened is allowed one emergency probe; it must not be blocked by
  a breaker that only ever describes threshold behaviour. **Requires PR-1** (§3.3).
- A successful probe resets immediately; a failed one leaves the breaker open.
- `/clear` calls a public `ContextManager.reset_compaction_circuit()` (**does not exist today**;
  added by this PR).
- Extend the `:590` increment with the same `is_auto` exemption `:540` already has.

**Single source of truth — the state stays in `ContextManager`; the coordinator only applies policy.**
The counter `_consecutive_compact_failures` (`context_manager.py:91`) does not move. Moving it would
require rewiring three existing public surfaces —
`get_usage_stats()['circuit_breaker_failures']` (`:1223`), `/context`'s rendering
(`cli/commands/context.py:33-40`) and `/clear` — and buys nothing: the coordinator only needs to read
`compaction_circuit_open` (`:423`), decide whether this attempt is a probe, and call
`reset_compaction_circuit()`. Of `closed / open / probing`, only `probing` is coordinator-transient
and never lands in `ContextManager`. §1's layering table is rewritten to say so.

**Add `Agentao.compact(*, reason=...) -> CompactionOutcome` as the public entry.** There is no public
compaction API today: all three call sites reach straight into `context_manager`
(`_compaction.py:102`, `_runner.py:1167`, `cli/commands/compact.py:103`).

**Compatibility line — the signature, the returned list's shape, and the breaker-open short-circuit
are preserved.** `ContextManager.compress_messages()` becomes the composition
`breaker query → prepare_compaction → built-in summarize → commit_compaction`, with the gate still in
front (today's `:507`), still returning `list[dict]`. The gate cannot be dropped — it is documented in
the docstring (`:496-497`) and pinned by a test that **calls it directly**
(`tests/test_context_manager.py:692-701`, `test_compress_messages_no_safe_split_counts_a_failure`,
which calls it three times and then asserts `compaction_circuit_open is True`). The previous
revision's "calling it directly bypasses the breaker policy" was wrong.

**But "behaviour byte-identical" overstated it too, and is withdrawn.** This plan **deliberately**
changes two side effects, and both must be named in their PR and have their tests updated:

1. **Failure counting on the manual path.** PR-2 extends `:590` with the `is_auto` exemption `:540`
   already has, so `compress_messages(..., is_auto=False)` **no longer** counts a summarization
   failure — today it does.
2. **When crystallize runs on a failed summarization.** Today crystallize is at `:582` and
   summarization at `:588`, so **a failed summarization has still written to the store**. After the
   prepare/commit split crystallize belongs to commit (§4.2.1, so that a cancel is genuinely
   side-effect-free), and commit does not run when summarization fails — so it **no longer** writes.
   That is a direct consequence of the cancellation semantics, not an accident. **This item lands in
   PR-3**, not PR-2 and not PR-4: PR-3 is what draws the prepare/commit boundary, and "no irreversible
   side effect before commit" *is* the definition of that boundary. Motivated by PR-4, landed in PR-3 —
   so PR-3's description and tests have to name this behaviour change.

What a direct call does bypass is exactly two things: the **host control plane** (PreCompact dispatch,
controller) and the coordinator's **probe policy**. That is why it is documented as an internal
transform and new code goes through `Agentao.compact()`. This goes in
`docs/reference/host-api.md`, not into a deprecation warning.

**Display is an extension, not new plumbing.** `get_usage_stats` already exports
`circuit_breaker_failures` (`context_manager.py:1223`) and `/context` already renders it with an
"(circuit open — auto-compact disabled)" annotation (`cli/commands/context.py:33-40`). PR-2 replaces
the count with `closed / open / probing`, the last failure class, and how to recover.

**Do not add a summarization retry wrapper** (§3.4).

**Sequencing note:** PR-2 introduces `probing`, a state that has to be reported. Landing it before
PR-3 means reworking its return value and its events inside PR-3.

### 4.4 PR-4 — the PreCompact control plane

Granularity, v1: **`allow` / `cancel` / `provide_summary(summary_text)`**.

Arbitrary message-list replacement is **out**. agentao's history is a flat list with a load-bearing
invariant — `tool_calls[*].id` must round-trip byte-for-byte to match the answering `role: "tool"`
message, or strict APIs reject the request (CLAUDE.md, "Unicode tag stripping"). A host returning an
orphaned tool result, an unknown role, or a bad boundary would produce a request the provider refuses,
at a point where history has already been destroyed. pi-mono can afford the richer contract because
its `CompactionResult` addresses a persisted session tree by `firstKeptEntryId`; agentao has no
analogue (`pi-mono-compaction-vs-agentao.md` §8).

The seam itself is **§4.2.1** (PR-3's); this section only switches `decide` on over it.

#### 4.4.1 The cancellation protocol for command hooks

`dispatch_pre_compact` is a side-effect-only `_dispatch_lifecycle` today
(`_dispatcher.py:158-164`) and **does not parse stdout at all**. So this PR adds a sibling,
`dispatch_pre_compact_decision`, shaped after `dispatch_pre_tool_use_decision` (`:90-117`).

The wire shape — **a dedicated key that has never existed**:

```json
{"hookSpecificOutput": {"compactionDecision": "cancel", "compactionDecisionReason": "..."}}
```

- The key is `compactionDecision`; it does **not** reuse `permissionDecision`.
- Domain: `allow` | `cancel`. A missing key, a missing `hookSpecificOutput`, non-JSON stdout, or a
  script that prints nothing all mean `allow`. Any other value is treated as `allow` with a warning —
  a typo must not be able to block compaction permanently and drive the context into the overflow
  ladder.
- `compactionDecisionReason` (optional `str`) feeds `CompactionOutcome.detail` and the log; `reason`
  is read as a fallback key, matching the precedent at `:358`.
- Merge rule: **first-cancel-wins**, stop forking once a `cancel` is seen. Note the precedent has two
  tiers — "first deny wins, otherwise first ask wins" (`:102-104`) — while this has one, because v1
  has no `ask`.
- Exit-code 2 stays unhonoured, matching the precedent and
  `docs/history/implementation/stop-precompact-hooks-plan.md:87`.
- Command hooks **cannot** `provide_summary`: they have no trust boundary, and summary text
  permanently rewrites history. `provide_summary` lives only in §4.4.2.

**The "no opt-in gate needed" argument tightens accordingly.** The old argument was "silence is
`allow`" — but that only proves scripts printing **nothing** are safe; it says nothing about a private
script that writes `hookSpecificOutput` for some other purpose. The real argument is the key name:
`compactionDecision` has never existed in agentao, so no existing script can produce it by accident.
That retires the "grep before relying on it" bullet in §8 — a dedicated field needs no grep, and grep
could never have covered a user's local private scripts anyway.

#### 4.4.2 The constructor-argument layer

**A new `compaction_controller=` constructor argument** gives trusted embedded hosts a
`CompactionDecisionContext` they can cancel, or (only when `kind == full`) answer with summary text.
The new kwarg goes after the `*` in `Agentao.__init__` (inserting into the older group would shift
legacy positional args).

**The contract — `Protocol`, return type, synchronous, and what happens on failure.**

```python
class CompactionController(Protocol):
    def __call__(self, ctx: CompactionDecisionContext) -> CompactionDecision: ...

@dataclass(frozen=True)
class CompactionDecision:
    action: Literal["allow", "cancel", "provide_summary"]
    summary: str | None = None      # only when action == "provide_summary"
    reason: str | None = None       # feeds CompactionOutcome.detail and the log
```

- **Synchronous; v1 does not accept a coroutine.** The whole compaction path runs inside
  `ContextManager`, which is synchronous and holds no loop; supporting an async controller means
  reproducing `AsyncToolBase`'s `runtime_loop` bridging (CLAUDE.md). An awaitable return is handled as
  an **unknown return value** (below) with a warning saying v1 does not support it.
- **Unknown return values** (`None`, another type, an `action` outside the vocabulary,
  `provide_summary` with `summary is None`) → ignored, warned, treated as `allow`.
- **A controller that raises → caught, warned, treated as `allow` (fail-open); the exception does not
  propagate.** This is a hard rule, and the reason is on the overflow path: entries 3 and 4 are the
  recovery ladder, so an `AttributeError` inside a controller escaping would turn "context too long"
  into "the turn crashes" — killing precisely the recovery path this plan exists to fix. Same
  direction as §4.4.1's "any other value is `allow`" and §4.4.3's "an invalid decision is `allow`":
  **no control-plane error may be able to drive the context into the overflow ladder, let alone end
  the turn.**
- **No timeout.** It is a synchronous in-process callback; if it hangs, it hangs the turn — the same
  semantics as the host's other callbacks (`confirmation_callback` and friends). This plan does not
  invent a timeout mechanism for it.
- At most one controller: the constructor argument is not a list.


**Ordering and cross-layer merge — command hooks run first, and a cancel in either layer ends it.**

1. Dispatch the command hooks first (`dispatch_pre_compact_decision`), **first-cancel-wins** within
   that layer (§4.4.1).
2. Any hook returns `cancel` → finish immediately, **do not consult the controller**. Asking a trusted
   host to compute a summary that is about to be thrown away is pure waste.
3. All `allow` → call `compaction_controller` (at most one — the constructor argument is not a list).
   It may `allow`, `cancel`, or `provide_summary(text)`.
4. The merge rule in one line: **a cancel in either layer is a cancel; `provide_summary` can only come
   from the controller layer.**

#### 4.4.3 Per-kind execution plans for the five entry points

The previous revision only worked out `kind == full`: `split_index` and the summary input budget do
not exist for microcompaction or `minimal_history`, and `provide_summary` has no legal meaning for
either. The three plans:

| `kind` | Entries | prepare produces | Decisions available | Result when cancelled |
|---|---|---|---|---|
| `microcompact` | 1 | `prepare_microcompact()` → `PreparedMicrocompact`: `tool_results_to_clip`, `pre_tokens = None` (see below) | `allow` / `cancel` | Skip this pass; not re-dispatched for the rest of the turn; history byte-identical |
| `full` | 2 / 3 / 5 | `PreparedCompaction` (above) | `allow` / `cancel` / `provide_summary` | See "Cancellation semantics" below |
| `minimal_history` | 4 | `PreparedMinimalHistory`: `keep_tail = 2`, `pre_tokens = None` (this path makes no token estimate at all today — §4.2) | `allow` / `cancel` | Return the context-length error; no cut to `messages[-2:]` |

- **All three kinds share one request type**, `CompactionRequest(trigger, kind, reason)`. The
  difference lives entirely in what prepare produces and in `can_provide_summary`; the coordinator has
  one skeleton.
- **`provide_summary` is legal only for `kind == full`.** For the other two,
  `CompactionDecisionContext.can_provide_summary` is `False`; a controller that returns summary text
  anyway is treated as an **invalid decision**: ignored, warned, treated as `allow`. Same direction as
  §4.4.1's "any other value is `allow`" — a misconfiguration in the control plane must not be able to
  drive the context into the overflow ladder.
- **Every field's value is pinned per kind**, leaving no "what goes here?" gaps:

| Field | `microcompact` | `full` | `minimal_history` |
|---|---|---|---|
| `pre_tokens` | **`None`** (see below) | the value from `:587` | `None` (this path makes no estimate, §4.2) |
| `messages_to_summarize` | `0` (nothing is summarized) | `len(to_summarize)` | `0` |
| `messages_to_keep` | `len(messages)` (nothing dropped, only shortened) | `len(to_keep)` | `2` (`keep_tail`) |
| `recently_read_files` | `()` | the result from `:574` | `()` |
| `summary_input_budget` | `None` | `_summary_input_budget()` | `None` |
| `max_summary_tokens` | `None` | half the budget | `None` |
| `can_provide_summary` | `False` | `True` | `False` |
| `tool_results_to_clip` | `len(targets)` | `None` | `None` |

  That last field exists for microcompaction specifically: `messages_to_summarize = 0` and
  `messages_to_keep = len(messages)` carry no information there, and a host deciding whether this pass
  is worth cancelling needs to know how many tool results are about to be clipped.

- **Microcompaction's `pre_tokens` is `None`, not `estimate_tokens(messages)` — the previous revision
  had this fighting §4.2.** §4.2 pins "this plan adds no new `estimate_tokens` call", and the **only**
  estimate microcompaction has today measures `messages_with_system` (`_compaction.py:46`) — the
  **system-inclusive** unit. Using it to fill a field declared system-exclusive is exactly the unit
  mixing §4.2 renamed the new event's fields to `*_tokens_history` to prevent; and computing a fresh
  history-only one means another full-history encode **every iteration** inside the 55–80% band,
  precisely the cost the comment at `_compaction.py:50-53` works to avoid. So it stays `None`:
  `tool_results_to_clip` already carries this kind's decision signal. This is now word-for-word
  consistent with §4.2's new-event table, where `microcompact` is `null` in both columns.

#### 4.4.4 The cancellation-suppression latch, and cancellation semantics

"Not re-dispatched for the rest of the turn" was only a promise in the previous revision, with no
mechanism — and the loop **re-checks the thresholds on every iteration** (`_compaction.py:30`, `:76`),
so without a latch that means asking again every iteration, which is exactly what the stand-down
comments at `:32,78` exist to prevent. Pinned:

- **Owner: the coordinator**, one `set` instance field. It does not land in `ContextManager` (unlike
  the breaker state, which has three existing public surfaces to serve; this has none).
- **It covers exactly two reasons: `microcompact_threshold` and `compression_threshold`.** Only those
  two are **re-checked every iteration** by the loop (`_compaction.py:30`, `:76`), and only those two
  can be re-dispatched. The key is still written `(kind, reason)`, since the two belong to different
  kinds and must be recorded separately.
- **`manual_cli` never enters the latch.** It is user-driven, does not loop, and **runs outside a
  turn** — `/compact` is slash-command dispatch (`cli/input_loop.py:230`) and does not go through
  `run_turn`; the new `Agentao.compact()` (§4.3) can likewise be called outside one. A turn-reset latch
  applied to them means "cancel manually once, and every immediate retry stays suppressed until the
  user first runs an ordinary turn" — a pothole created purely by an implementation detail.
- **Neither overflow reason enters it either.** A cancelled overflow returns the context-length error
  to the caller and ends the turn on the spot (§4.4's cancellation semantics), so there is no
  re-dispatch to suppress.
- **Reset point: the start of the turn.** It goes in the existing per-turn reset block at
  `runtime/turn.py:98-106`, beside `_turn_finish_reason_missing` and
  `last_summary_finish_reason_missing` — already the home for "flags cleared once per turn". Narrowed
  to the two threshold reasons, that reset point is sufficient: those two happen **only** inside a
  turn.
- **Only `cancelled` enters the latch.** `skipped` and `failed` do not: the former never attempted
  anything, and the latter is throttled by the breaker. The two mechanisms do not stack.
- **A latch hit is silent:** no hook dispatch, no controller call, no event — it just returns
  `CompactionOutcome(status="skipped", detail="suppressed_by_latch")`. Same direction as §4.2's
  "`skipped` emits no event", and for the same reason: it hits on every iteration.

**Cancellation semantics — this is the part the earlier exclusion was about.**
`docs/history/implementation/stop-precompact-hooks-plan.md:1081` deferred PreCompact gating precisely because "accepting a host
'deny' with no fallback for *host denied and still too long* produces unrecoverable runaway", and
`codex-compaction-vs-agentao.zh.md:294` records the same reasoning. The answer:

- A cancelled **threshold** compaction is not re-dispatched for the rest of the turn — no per-iteration
  hook fork (the mechanism is the latch above).
- If the API then really overflows, ask again with `reason=api_overflow`. This is a different question
  and the host gets to answer it separately.
- If **overflow** is also cancelled, return the context-length error to the caller. **Do not silently
  fall through to `messages[-2:]`.** The runaway the earlier plan feared comes from a cancel that is
  ignored, not from a cancel that is honoured and reported.
- **Entry 4 (`minimal_history`) cancelled**: also returns the context-length error to the caller,
  history byte-identical. It is a separate dispatch site (`_runner.py:1199`), reachable only when
  entry 3 was allowed, compacted successfully, and the request **still** overflowed — so it needs its
  own answer; the previous bullet does not cover it. Same semantics: the cancel is honoured and
  reported, never a quiet fall-through to `messages[-2:]` (`:1204`).
- A cancelled **manual** compaction reports `cancelled` and leaves history byte-identical.
- A cancelled **microcompaction** (entry 1) simply skips this pass, history byte-identical; like the
  threshold case it is not re-dispatched for the rest of the turn. It returns **no** error —
  microcompaction was never the step you cannot proceed without, and the 55–80% band re-evaluates on
  the next turn.

---

## 5. Batch 2 — P2/P3

### 5.1 PR-5 — window validation and self-healing

`max_context_tokens` is a **documented host-owned knob** on four surfaces (`agent.py:104`;
`embedding/factory.py:132`; `cli-host-agent-factory.zh.md:104` names its owner; ACP's three
never-overwrite-each-other knobs in `docs/history/implementation/acp-stdio-auth-fix-plan.md:99-110`). This PR does **not** take
that ownership away and does **not** introduce a model-window catalogue to maintain.

- Keep `configured_max_tokens` as the host set it.
- Parse the provider's stated limit out of high-confidence overflow errors.
- Use `effective_max_tokens = min(configured, observed_limit)` at runtime.
- Clear `observed_limit` on model/provider switch and warn that the window is unverified for the new
  model — never silently overwrite the host's value. This joins the **existing** clear-on-switch
  family (thinking artifacts, tiktoken encoding, token anchor, capability latches — CLAUDE.md); it is
  not a new mechanism.
- `/context` shows configured, effective, provenance and mismatch state.

**Migration rules — `max_tokens` has 8 read/write sites today and none of them can be waved off as
"probably unused".** Two external writes: `/context limit <n>` (`cli/commands/context.py:66`) and
ACP `session/set_model`'s `contextLength` (`acp/session_set_model.py:69`). Three external reads: the
ACP echo (`session_set_model.py:75`, which decides what the client sees),
`get_usage_stats()['max_tokens']` (`context_manager.py:1219`), and `/context`'s rendering
(`context.py:22`). Five internal reads: `:267` (full-compression threshold), `:278-279` (the
microcompact band), `:1002` (summary input budget), `:1215` (`usage_percent`). The rules:

- The `max_tokens` attribute **keeps meaning configured**, with read/write semantics unchanged — it is
  the host's knob and reads back what the host wrote. `effective_max_tokens` is a new **read-only**
  property.
- **Internal budgets all switch to effective**: `:267`, `:278-279`, `:1002`. Those three are where a
  mis-set window actually bites.
- **`usage_percent` switches to effective too** (`:1215`), or `/context` reports 70% while the API is
  already rejecting.
- **The ACP echo keeps returning configured** (`session_set_model.py:75`): `session/set_model` is a
  setter, and its echo must equal what was just written, or clients read agentao's self-healing as a
  failed write.
- **`get_usage_stats()` keeps its `max_tokens` key** with the configured value, and gains
  `effective_max_tokens` and `observed_limit_provenance`. The old key does not change meaning, so old
  hosts are unaffected.

**Known limits of the parse — design around these, they are not blockers.** Of the 21 patterns in
`_OVERFLOW_PATTERNS` (`context_manager.py:1235`), roughly half carry a number in the message
(Anthropic `tokens > N maximum`, OpenAI `maximum context length is N`, xAI `maximum prompt length is
N`, OpenRouter, Mistral) and roughly half do not (`context_length_exceeded`, `request_too_large`,
`reduce the length`, `too many tokens`, `range of input length`). Worse, the ones that do carry a
number usually carry **two** — Anthropic's `213462 tokens > 200000 maximum` has both the request and
the limit, and OpenAI's has both. Picking the wrong one permanently shrinks `effective_max_tokens`
until the next model switch, which is a *silent degradation with no warning* — the exact failure
class this plan exists to remove. Therefore:

- The parse is provider-asserted, not a bare number scrape.
- **If the parse is not certain, do not adopt it.** Falling back to the ladder is the safe outcome.
- `/context` shows the provenance string the limit was learned from.

**Priority note — and what it cannot do.** This item's priority rose with the threshold change: at
0.65 there were 35 points of window between "we compact" and "the API rejects"; at 0.80 there are 20.
That margin is what absorbs a mis-set window, and it is what the two-rung ladder falls back on.

But state plainly what PR-5 **cannot** do: `observed_limit` can only be learned from an overflow
error, so **the first fall into the ladder is its input, not something it can prevent**. It reduces
how often you fall in again afterwards; it does not stand "between a mis-set window and the ladder".
What 0.80 raised is two different risks — estimation error under a *correct* configuration, and some
mid-size window mismatches — and PR-5 can do nothing about the first and only post-first-failure work
about the second.

### 5.2 PR-6 — summarization quality, in risk order

1. **Tokenized recency window — aimed at "still heavy after compaction", not at the summary input
   budget.** Today's retention is `keep_count = min(20, max(4, int(len(messages) * 0.60)))`
   (`context_manager.py:522-525`); layering a backwards-from-the-tail token accumulation on top takes
   the **intersection** of all three, so it can only make the kept set **smaller**.

   **The previous revision stated the mechanism wrongly; corrected here.** The recency window **never
   reaches the summarizer**: only `to_summarize = messages[:split_index]` (`:558`) does, while
   `to_keep` (`:565`) is spliced verbatim into the result (`:639`). So a heavy tail does not blow out
   the summary input budget — it blows out **the post-compaction context itself**: a compaction
   replaces the old half with a few hundred tokens of summary and leaves tens of thousands of tokens
   of tail untouched, so the threshold is crossed again immediately and the next iteration compacts
   again. Note the tail is already microcompacted at `:565`, so the residual weight comes mostly from
   non-tool content and from tool results sitting near `MICROCOMPACT_TOOL_LIMIT`.

   It does **not** address the dual half — 20 very short messages still keep very few tokens — which
   would require raising `KEEP_RECENT_MESSAGES = 20` (`:71`), a separate, data-driven change **out of
   scope here**.

   **The previous revision called "at least 4" a hard floor; that was wrong and is corrected.**
   `keep_count` only sets the **search start**
   (`split_index = _find_split_index(messages, len(messages) - keep_count)`, `:526`);
   `_find_split_index` then scans **forward** from that start to the first non-`tool` index, preferring
   a `user` one (`:458-467`). So the number actually kept **can be fewer than 4** (when the messages at
   the start are all `role: "tool"`), and can be **zero** — `chosen is None or chosen == 0` returns
   `None` and the whole compaction fails (`:473-474`, `status="failed"`, §4.2).

   **So the formula is `max`, not `min` — the previous revision had it backwards.** Three starts:

   ```
   count_start = len(messages) - min(20, max(4, int(len(messages) * 0.60)))   # today's, :522-526
   token_start = smallest i where estimate_tokens(messages[i:]) <= keep_budget  # accumulate backwards
   start       = max(count_start, token_start)
   split_index = _find_split_index(messages, start)                          # :526
   ```

   A later start means fewer kept. The token budget is the **tightening** constraint this item adds, so
   the two must be combined by taking the **later** one — taking `min` simply violates the budget on a
   heavy tail, which is the very thing this item exists to fix. The previous revision's
   `min(token_start, len - 4)` also dropped the 20-message and 60% limits inside `keep_count`; they are
   folded back into `count_start` above.

   **The consequence is accepted: when `token_start > count_start`, fewer than 4 messages can be
   kept.** The `max(4, …)` inside `count_start` is overridden by the outer `max()` — consistent with
   the previous round's conclusion that there was never a real message-count floor. The only structural
   floor is **1**, from `_find_split_index`'s `limit = len(messages) - 1` (`:455`), which never returns
   `len(messages)`. Log a line whenever it drops below 4. Making "keep at least N" a *genuine*
   invariant would require expanding the boundary **backwards over whole tool-call groups**; that is a
   separate change and is **out of scope here**. Ship behind config first; decide the default budget
   from data.
2. **Move the carried summary out of the local eviction pool — the gain is shape and prompt, not a
   bug fix.** Append it as a `<previous-summary>` block under its own budget with an UPDATE prompt,
   and delete the local patches this shape forced — `carry_index`
   (`context_manager.py:893,938,940`), `_clip_carry_summary` (`:983`), and the `carry_index` special
   case in `_join_within_budget` (`:1045`).

   **This item used to say "removes the defect class"; that sentence did not match the code and is
   gone.** The carried summary **is already never evicted** today: `_join_within_budget` charges its
   budget first, adds it to `keep`, and never evicts it (`:1045-1047`) — and those three patches are
   exactly what fixed that defect. So the real gain here is prompt shape (UPDATE semantics) and
   simplification, and the risk is reintroducing the defect they fixed.

   **A replacement ceiling is therefore mandatory, not optional.** There are two invariants today, and
   deleting `_clip_carry_summary` (`:983-993`) drops both: carry ≤ `_summary_input_budget()` / 2, and
   carry + live ≤ `_summary_input_budget()` (`:1041-1047`). The new shape must restate and test
   `carry_budget + live_budget <= summary_input_budget` — "its own budget" only changes the
   bookkeeping; both texts still go into one provider request, so the provider-level budget
   competition is entirely unchanged.
3. **P3 compensation — the first one is explicitly a partial mitigation.** Reserve a separate
   **input** budget for the originating user request when the cut point lands mid-turn; add an
   injectable token estimator for images appended after the anchor (`_count_message_tokens` sums only
   `type == "text"` blocks today).

   The first only closes the half the P3 literally states — "no budget reserves it"
   (`pi-mono-compaction-vs-agentao.md:51`). **Reserving input budget does not make the summarizing
   model write it into the output**, and pi-mono's answer is the other half of that same line: a
   dedicated turn-prefix summarization call with its own budget. This plan does not adopt that half,
   so this item must close the P3 as a **partial mitigation**, not as closed. To actually close it,
   pick one: deterministic carry (splice the raw user request into the result without going through
   the summarizer), output validation (retry once if the summary does not contain it), or adopt
   pi-mono's dedicated prefix-summary step. All three are out of scope for this PR.

**Thresholds stay put during batch 2.** `MICROCOMPACT_THRESHOLD = 0.55` and
`COMPRESSION_THRESHOLD = 0.80` (`context_manager.py:69-70`) are not touched here. Collect compaction
success rate, compression ratio, latency, distance-to-next-overflow and cache-read delta **against the
0.80 baseline** before deciding whether to expose the ratios as `CompactionSettings`. Any measurement
taken at 0.65 is not a baseline for this.

---

## 6. Release gates

```bash
uv run python -m pytest tests/
uv run ruff check .
```

**`uv run mypy agentao` is not a viable gate and must not be added to this plan.** It fails on `main`
today with **1084 errors in 146 files (272 source files checked)**. The cause is visible in
`pyproject.toml:195-199`: the comment says "strict only on the public host boundary; the rest of the
codebase is untouched until separate items raise its bar", but `strict = true` is set at the top-level
`[tool.mypy]` table, so it applies everywhere. A typing ratchet was separately evaluated and declined
(`docs/design/refactor-audit-2026-07.md`). Fixing the config/intent mismatch is legitimate work — it
is simply not a precondition for compaction orchestration.

`ruff check .` is written with the bare `.` on purpose: the rules **and** the scope both live in
`pyproject.toml`, so that command is character-for-character what CI runs
(`docs/design/lint-gate.md`). Narrowing it to `agentao tests` drops `examples/`, `scripts/`,
`skills/` and `developer-guide/`. Both invocations pass today; the point is that only one of them is
the gate.

### Scenario coverage

- After three summarization failures: threshold attempts pause, a manual or overflow probe succeeds,
  the breaker resets.
- `/clear` resets the breaker state.
- All five entry points agree on `trigger` / `kind` / `reason`, hook ordering, and events.
- A `{"trigger": "manual"}` rule fires on `/compact` and on nothing else; a `{"trigger": "manual|auto"}`
  rule still fires everywhere.
- History is byte-identical after a cancelled compaction.
- A custom summary that is empty, over budget, or the wrong type is rejected before it is committed.
- A cancelled overflow returns a context-length error and does **not** quietly cut to the last two
  messages.
- Token-budgeted cut points never orphan a tool result.
- The carried summary and the live tail each respect their own budget.
- A model switch does not silently reuse the previous model's observed window.
- `CONTEXT_COMPRESSED` is emitted only when `CompactionOutcome.status == "success"`. **The criterion
  is not the message count.** Microcompaction builds a fresh list element by element and only shortens
  `content` (`context_manager.py:396-405`), so on success `pre_msgs` and `post_msgs` are **necessarily
  equal** — gating on the count would suppress every successful microcompaction event. The converse
  fails too: at `len(messages) == 5`, `keep_count = 4` and `split_index = 1`, so one message becomes
  boundary + summary and **a successful compaction raises the count**. The repo already carries three
  pieces of evidence that neither count nor identity is usable — `microcompact_messages`'s docstring
  (`:387-389`, "a fresh list is always built, so `result is not messages` says nothing"), the
  `last_microcompact_mutated` flag added for exactly this (`:112`), and `/compact` sniffing for the
  `[Compact Boundary` marker instead (`cli/commands/compact.py:26-40`).
- The overflow entry does not emit `CONTEXT_COMPRESSED` while the breaker is open (today it emits
  unconditionally, `_runner.py:1177`).
- A `[PIN]` message appears **exactly once** in the compacted result and **never** in the summary
  input (`summary_messages` has the `[PIN]` entries removed, matching the existing filter at
  `_summarize_messages:847-853`).
- After a cancelled threshold compaction, PreCompact is not dispatched again in that turn — but a
  later `api_overflow` in the **same** turn still dispatches once (the latch key carries `reason`,
  §4.4.4).
- The latch is cleared at the start of the next turn.
- A controller that raises does not stop the compaction (fail-open), the exception does not propagate,
  and the log records it.
- A controller returning an unknown shape (`None` / an awaitable / an `action` outside the vocabulary)
  is treated as `allow` with a warning.
- Each of `success | cancelled | failed` emits one `COMPACTION_SETTLED`; **`skipped` emits none**;
  only `success` additionally emits `CONTEXT_COMPRESSED`.
- `CONTEXT_COMPRESSED`'s seven keys and both token units (system-inclusive) are byte-identical before
  and after PR-3.
- A cancelled manual `/compact` **still dispatches normally on an immediate retry** (`manual_cli` does
  not enter the suppression latch).
- A failure count is still incremented when summarization returns nothing (all three counting points
  live in `ContextManager._run_compaction`, so none is lost when commit does not run).
- After a failed summarization on manual `/compact`, `_consecutive_compact_failures` does **not**
  increase (a deliberate change in PR-2, §4.3).

---

## 7. Non-goals

- **Do not port pi-mono's session tree.** Everything here stays in the flat message list.
- **Do not adopt `chars/4` estimation.** agentao's CJK-aware estimator is ahead
  (`pi-mono-compaction-vs-agentao.md` §10).
- **Do not remove microcompaction.**
- **Do not move the check to turn boundaries.** On checking cadence agentao is level with codex and
  ahead of pi-mono.
- **Do not add codex's separate `ModelDownshift` / `CompHashChanged` trigger sites.** agentao checks
  every iteration and picks up a corrected window on its own.
- **Do not change `trigger` to a non-Claude-compatible vocabulary** (§3.2).
- **Do not wrap summarization in a second retry layer** (§3.4).
- **Do not accept arbitrary host-supplied message lists** (§4.4).

---

## 8. What would change this plan

- ~~**§3.1 weakens** if a `{"trigger": ...}` matcher turns out to be unused in every shipped plugin —
  it would still be a contract bug, but a P2. Re-grep the marketplace manifests before
  downgrading.~~ **This downgrade condition was closed on 2026-08-24 as "undecidable by the method it
  prescribes"; §3.1 stays a P1.** The condition asks for a **measured zero** across shipped plugins,
  but the plugin marketplace has not been built — so there is **no population to measure**. That is
  "no population", not "measured zero", and only the latter would support a downgrade. Plugins do not
  go through a marketplace anyway: `PluginManager` loads from `~/.agentao/plugins`
  (`embedding/plugins/manager.py:96`) and `<cwd>/.agentao/plugins` (`:100`), with the marketplace being
  only a **directory level inside that** (`:297-312`, "Scan *plugins_dir* for plugins organised by
  marketplace"). So the real plugin population today is exactly the one the bullet below already
  concedes a grep cannot reach. **And the direction is inverted: no marketplace is an argument for
  landing PR-1 sooner, not for downgrading it.** PR-1 is a matcher **behaviour change** — today
  `{"trigger": "auto"}` matches all five entries including manual `/compact` (everything is hard-coded
  at `_payload.py:160`), and after PR-1 it stops matching manual. Done before the ecosystem exists it
  breaks only locally hand-written rules; done after shipped plugins encode `{"trigger": "auto"}` in
  their manifests, the same fix is a breaking change to third-party config. Finally, **one shipped
  document is now wrong regardless of adoption**: `docs/releases/v0.4.4.md:131` states
  `trigger | PreCompact only: auto (no manual site exists)`, and that parenthetical was true at 0.4.4
  and false since manual `/compact` shipped. **No re-open trigger** — by the time a marketplace exists
  the question is moot. One follow-on: PR-1's doc work should add an **erratum** line to
  `docs/releases/v0.4.4.md:131` (the same treatment `stop-precompact-hooks-plan` got — annotate, do not
  rewrite a historical statement).
- ~~**§3.5's "no opt-in gate needed" fails** if any shipped PreCompact hook writes
  `hookSpecificOutput` to stdout for another purpose.~~ **Structurally removed by §4.4.1's choice of
  key**: `compactionDecision` has never existed, so no existing script can produce it by accident. A
  grep only ever covered shipped plugins, never a user's local private scripts; a dedicated field
  needs to cover nothing.
- **§4.4's cancellation design fails** if a host can cancel both the threshold *and* the overflow
  question and then expects the turn to continue. It cannot; the plan returns the error. If that is
  unacceptable to a real host, the gate needs a forced-compaction escape hatch and this section has to
  be redesigned.
- **§5.1 strengthens** if someone shows a common deployment where the CLI's single `200_000` default
  (`cli/app.py:278`) silently mismatches a popular model. It **weakens** if a host-side convention
  already surfaces the mismatch — not found here, but re-grep rather than trusting this line.
- ~~**§5.2's item 2 weakens** if the summary-eviction defect turns out to be reachable only under
  budgets nobody runs.~~ **Retired**: that defect is already fixed today by `carry_index` +
  `_clip_carry_summary` (`:1045-1047`), and this item is not fixing it. The only thing that now
  weakens item 2 is measurement showing the UPDATE prompt does not produce better summaries than the
  current in-band shape — in which case only its simplification value remains and it should drop to
  P3 or be dropped.
- **Anchors expire.** Re-verify every `file:line` before acting; this plan was written against
  `main@a996395` plus two uncommitted edits.

---

## 9. Review record

Items from all twelve reviews are folded into the body above (rev 13 and rev 14 are a maintainer instruction and a closed condition, not review rounds). Recorded here as history, **not** as an
override layer: §§1–8 are authoritative on their own and no section needs this one to be read
correctly.

### rev 1 — nine items

| # | Item | Where it landed |
|---|---|---|
| 1 | `uv run mypy agentao` proposed as a release gate; it fails on `main` with 1084 errors in 146 files, and the ratchet was previously declined | §6 |
| 2 | `CompactionTrigger = manual\|threshold\|overflow` would break the Claude-parity `manual\|auto` matcher vocabulary | §3.2, §4.1 |
| 3 | The `trigger` bug is a **dead matcher value**, not merely an inaccurate payload field | §3.1 |
| 4 | PR-4's "hook v2 / explicit opt-in" gate is unnecessary given the `dispatch_pre_tool_use_decision` stdout-JSON precedent | §3.5, §4.4 |
| 5 | PR-2 depends on PR-1 (`compress_messages` defaults `is_auto=True`, so overflow and threshold are indistinguishable) | §3.3, §4.3 |
| 6 | Sequence should be PR-1 → PR-3 → PR-2 → PR-4, not 1→2→3→4 | §4 |
| 7 | Four items are smaller than budgeted: PR-3 finishes PR #181; the `is_auto` exemption exists at `:540`; `/context` already renders the breaker; clear-on-switch is an existing family | §4.2, §4.3, §5.1 |
| 8 | The plan was one revision stale — the threshold is now 0.80, which moves PR-6's baseline and raises PR-5's priority | §5.1, §5.2 |
| 9 | Narrowing `ruff check .` to `agentao tests` diverges from the CI command | §6 |

**Method note.** Two of these (3 and 5) came from reading the *call sites* rather than the signatures:
the severity of the `trigger` bug is only visible in the matcher test, and the PR-1 dependency is only
visible in a defaulted keyword argument at `_runner.py:1167`. Signatures do not state what callers
pass.

### rev 2 — ten items

The second review raised seven. On verification five stand as filed, one is reclassified, and one is
downgraded to a scope disagreement; verification added three more (items 8–10).

| # | Item | Verification | Where it landed |
|---|---|---|---|
| 1 | §6 used `pre_msgs == post_msgs` as the "nothing happened" criterion, which would suppress every **successful** microcompaction event | Stands; severity lowered from blocker to a gate-wording defect | §6 |
| 2 | `provide_summary` has no implementable prepare/commit seam, and cannot coexist with §1's "keeps its body" | Stands — **the one blocker this round** | §1, §4.2.1 |
| 3 | Breaker-state ownership disagrees between §1 and §4.3 | Stands, but as **undefined** rather than contradictory: `reset_compaction_circuit()` does not exist today, so the plan simply never said where it goes | §1, §4.3 |
| 4 | The command-hook cancellation protocol has no field name, domain, reason, or merge rule | Stands, and the gap is larger: `dispatch_pre_compact` is a side-effect-only `_dispatch_lifecycle` today (`_dispatcher.py:158-164`) and does not parse stdout at all | §4.4.1 |
| 5 | configured/effective changes the existing public `max_tokens` contract with no migration rule; and "stands between a mis-set window and the ladder" is false | Both halves stand | §5.1 |
| 6 | PR-6's three items do not deliver their stated goals | Items 1 and 2 stand (and item 2's **stated rationale** also contradicts the code); item 3 is downgraded to a scope disagreement and marked a partial mitigation | §5.2 |
| 7 | §2's fact 1 claims `compaction_type` / `reason` do not reach the hook payload — a factual error | Stands; verified at `_payload.py:162-163` | §2 |
| 8 | Added on verification: `!=` is no proof of success either — at `len == 5` one message becomes boundary+summary, so **a successful compaction raises the count** | — | §6 |
| 9 | Added on verification: the two enums PR-1 adds are **still not matchable**; `_matches` reads only `trigger` | — | §4.1 |
| 10 | Added on verification: the overflow entry does not compute `pre_tokens` today, so a required `CompactionOutcome.pre_tokens: int` forces a full-history estimate on a failing path | — | §4.2 |

**Method note.** Items 2, 4 and 7 all came from reading **the cited line itself**: `_payload.py:162-163`
directly refutes one of §2's foundational facts, and `_dispatcher.py:158-164` shows the precedent
covers only half of what PR-4 needs. Item 6's second point goes further — the patch the plan proposes
to delete (`carry_index`) is precisely what fixed the defect it claims to be removing. **Read a cited
line together with its context, or you will file something already fixed as something still to
fix.**

### rev 3 — six items

The third review raised six. **All six stand**, and two of them (the second half of item 1, and item
6) point at errors the rev 2 revision **introduced itself**.

| # | Item | Verification | Where it landed |
|---|---|---|---|
| 1 | `CompactionPreparation` carries only indices and counts, so `commit_compaction` cannot get `to_summarize` / `to_keep` / `pinned`; and prepare claims "zero side effects" while calling `microcompact_messages`, which writes `last_microcompact_mutated` and logs | Stands, both halves. Split into a private `PreparedCompaction` and a public redacted `CompactionDecisionContext`; "zero side effects" replaced by naming the two acceptable ones. The "a reference copies the history" rationale was rev 2's own error and is gone | §4.2.1 |
| 2 | The control plane only works out `full`, not the five entry points it promises; and none of the six PRs owns creating the coordinator | Stands | §4, §4.4.2, §4.4.3 |
| 3 | An invalid host summary both "returns `status=\"failed\"`" and "continues into the built-in summarizer" — a self-contradictory terminal state | Stands | §4.2.1 |
| 4 | The breaker query was placed inside `prepare_compaction`, contradicting "the coordinator owns policy"; and "calling `compress_messages` directly bypasses the breaker policy" changes documented and tested behaviour | Stands. The second half has hard evidence: the docstring at `:496-497` plus `tests/test_context_manager.py:692-701` | §4.3, §4.2.1 |
| 5 | "20 huge tool results blow out the summary input budget" is false — `to_keep` never reaches the summarizer; and the token ceiling and the "at least 4 messages" floor cannot both hold when 4 messages already exceed the budget | Stands, both halves | §5.2 |
| 6 | Twin and index drift: the Chinese §8 carried both the new and the retired §3.5 bullet and had lost the §3.1 one; `docs/README.md` still said "Reviewed once" | Stands. Both the duplicate and the loss came from rev 2's patch landing one line off | §8, `docs/README.md`, `docs/design/README.md` |

**Method note.** Items 1, 4 and 5 all came from **reading one step further along the data flow**: what
commit needs is answered by which locals `:582` / `:639` / `:646-647` consume; whether "bypasses the
breaker" can be said is answered by whether a test calls it directly; whether "blows out the summary
budget" is right is answered by whether `to_keep` flows into `_format_for_summary` or into `result`.
**Before asserting what a function does, look at who consumes its return value.** One more, on
process: rev 2 patched §8 by line number, and landing one line off produced a duplicate and a deletion
at once — neither of which is conspicuous when reading either bullet alone. **Editing a document by
line number requires re-reading one entry either side of the change.**

### rev 4 — eight items

The fourth review raised eight. **All eight stand.** Item 5 also refutes the *reason* rev 2 gave for
`pre_tokens` (the conclusion happened to be right, the reason was not), and item 6 refutes an
"invariant" rev 3 had just written in.

| # | Item | Verification | Where it landed |
|---|---|---|---|
| 1 | `summary_input = _format_for_summary(to_summarize)` feeds `[PIN]` messages to the summarizer while commit re-injects `pinned` verbatim | Stands. `_summarize_messages` strips `[PIN]` at `:847-853` before calling `_format_for_summary` (`:854`), and `pinned` (`:568`) is exactly that complement. Added a `summary_messages` field plus a regression scenario | §4.2.1, §6 |
| 2 | "Not re-dispatched for the rest of the turn" has no latch owner, key, or reset point | Stands. The loop re-checks thresholds every iteration (`_compaction.py:30,76`). Pinned: coordinator-owned, keyed `(kind, reason)`, reset at `runtime/turn.py:98-106` | §4.4.4 |
| 3 | `compaction_controller` has no `Protocol`, return type, sync/async answer, or exception / unknown-return policy | Stands. Added `CompactionController` / `CompactionDecision`, pinned synchronous and **fail-open**, with the reason spelled out: a propagating controller exception would kill the overflow recovery ladder | §4.4.2 |
| 4 | `CompactionOutcome` has no status mapping; the "terminal event" has no name or payload schema | Stands. Added an 8-row mapping table plus `EventType.COMPACTION_SETTLED` and its payload | §4.2 |
| 5 | `pre_tokens` nullability conflicts: §4.2 says `int \| None` while both new types require it | Stands — and §4.2's **reason** was also wrong: the full path already computes `pre_tokens` at `:587`, entry 3 included. What genuinely estimates nothing is `minimal_history`, and there are two "with / without system prompt" units in play | §4.2, §4.2.1, §4.4.3 |
| 6 | "At least 4 messages" is not something the current cut-point algorithm guarantees | Stands. `_find_split_index` scans **forward** from the start (`:458-467`), so fewer than 4 — or `None` — is reachable. rev 3's "the message-count floor is a hard floor" is withdrawn | §5.2 |
| 7 | "legacy behaviour byte-identical" is too strong; it conflicts with the deliberate changes to crystallize timing and manual failure counting | Stands. Narrowed to "signature + returned-list shape + breaker-open short-circuit", with both deliberate changes listed | §4.3 |
| 8 | `docs/design/README.md`'s "Active & proposed designs" still does not list this plan | Stands. rev 3 only edited the review count inside that README's **upstream-analysis** entry; it never added a backlog entry | `docs/design/README.md` |

**Method note.** Items 1, 5 and 6 share one shape: **I cited a function without reading what happens
to its input before it is called.** `_format_for_summary` has a `[PIN]` filter in front of it
(`:847-853`); `estimate_tokens` has already been called on the full path (`:587`); the `keep_count`
handed to `_find_split_index` is its **starting point**, not its **conclusion** (`:458`). **"Function
X's input is Y" has to be read upward from the call site until you reach what it actually receives.**
Item 8 is a different class: rev 3 claimed it had "synced `docs/design/README.md`" while having
changed exactly one string inside it — **"I edited that file" is not "I did that thing".**

### rev 5 — five items

The fifth review raised five. **All five stand.** Items 1, 2 and 3 each refute a piece of design rev 4
had just written in.

| # | Item | Verification | Where it landed |
|---|---|---|---|
| 1 | prepare's return type cannot express the `len < 5` and no-safe-split early exits; and the table puts summarization-failure counting in commit | Stands, both halves. The second is a genuine hole: when summarization returns nothing `:589` returns first, commit never runs, and that increment would vanish. Changed to a `PrepareResult` union plus **all three counting points on the coordinator** | §4.2.1 |
| 2 | PR-6's cut-point formula is inverted, and drops the existing 20-message / 60% limits | Stands. A later start means fewer kept; the token budget is a tightening constraint, so it must be `max`. `min` violates the budget outright | §5.2 |
| 3 | `post_tokens` has no unit; the old event's tokens include the system prompt (`_compaction.py:46`), so sourcing the old event from the Outcome changes a public field's semantics; and the keys differ, so "superset" is false | Stands, all three parts. The old event's keys are `type` / `pre_est_tokens` / `post_est_tokens` (`replay/observability.py:47-55`) — **all three differ in name** from the `kind` / `pre_tokens` / `post_tokens` I wrote | §4.2 |
| 4 | The latch's turn-scoped reset misfires for out-of-turn compaction — `/compact` and the new `Agentao.compact()` | Stands. `/compact` is slash-command dispatch (`cli/input_loop.py:230`) and never goes through `run_turn`. The latch is narrowed to the two threshold reasons, and "does a latch hit emit an event?" is now answered (no) | §4.4.4, §4.2 |
| 5 | `CompactionDecisionContext`'s field values are undefined for the non-`full` entries, and the command hook's input wire schema is undefined | Stands. Added a per-kind field-value table (including a microcompaction-specific `tool_results_to_clip`) and pinned that hooks keep receiving the existing PreCompact payload (`_payload.py:145-163`); the context never goes on the wire | §4.2.1, §4.4.3 |

**Method note.** Items 1 and 3 share a shape: **I defined a new type's fields without walking every
path it has to cover.** prepare has two early returns and I designed a return value only for the
successful one; the event has two producers and I looked only at the payload I was writing, never
opening `replay/observability.py` to see what the existing keys are called. **Before pinning a type or
a schema, enumerate the branches it must cover and the existing shapes it must stay compatible with.**
Item 2 is a plain sign error: `start` is where the *kept* window begins, so a later start keeps less —
substitute a concrete number and check the sign, rather than trusting the words "floor" and
"ceiling".

### rev 6 — four items

The sixth review raised four. **All four stand.** Item 1 refutes rev 5's freshly-written "all counting
belongs to the coordinator".

| # | Item | Verification | Where it landed |
|---|---|---|---|
| 1 | Counting ownership contradicts between the legacy wrapper and the coordinator: `compress_messages()` can be called on its own and has no coordinator, while §4.3 keeps the counter on `ContextManager`; and "its counting matches today's exactly" conflicts with the two deliberate changes §4.3 names | Stands, both halves. Changed to a **shared private `ContextManager._run_compaction`** carrying the three counting points, with the dependency direction pinned (`ContextManager` never knows the coordinator exists; the control plane arrives as a `decide` callback); the compatibility line now reads "identical except for the two deliberate changes named in §4.3" | §4.2.1 |
| 2 | `CompactionDecisionContext` is missing `tool_results_to_clip`, contradicting §4.4.3's per-kind table | Stands. Added as `int \| None` | §4.2.1 |
| 3 | The token event has no per-path "populated / null" contract: the old event is already `null` for entries 3 and 4 (`_runner.py:1177`, `:1208-1213`), and the new event only gave a source for full-success | Stands. Added two tables — the old event by its five entries, the new one by `kind × status` — plus the governing rule: **this plan adds no new `estimate_tokens` call** | §4.2 |
| 4 | "the five `skipped` rows" — there are only four | Stands. Corrected to "four, three of which re-trigger every iteration" | §4.2 |

**Method note.** Item 1 is **ownership drifting between two sections**: §4.3 assigned the counter to
`ContextManager`, §4.2.1 then assigned "counting" to the coordinator — each sentence reads fine alone
and only the pair exposes it. **When one resource is granted twice in different sections, read both
grants side by side.** Items 3 and 4 are the same carelessness twice: writing "five" without counting
the table's rows, and pinning a token unit without first listing what each of the five entries passes
today. **Cite a table and you count it; write a field's contract and you enumerate every path that
produces it.**

**Also on the record: an operational failure this round.** The Chinese patch again landed on the wrong
line numbers — `can_provide_summary` was at 469, not 461, so `kind: CompactionKind` was overwritten,
and a 402-414 replacement ate one line too many, swallowing the opening of "the breaker query is not in
prepare". Both were caught and repaired in the pre-submit structural check. This is the second
occurrence of the trap recorded in rev 3, so the lesson is upgraded: **when editing a document by line
number, re-read the whole edited block afterwards, not just one entry either side.**

### rev 7 — three items

The seventh review raised three. **All three stand.** The first two both point at things rev 6
introduced itself: a type whose name was written but never defined, and a field value that fights a
hard constraint set in the same revision.

| # | Item | Verification | Where it landed |
|---|---|---|---|
| 1 | `_run_compaction`'s return type `_CompactionRun` is never defined anywhere; and it is not stated whether it covers `full` only or all three kinds | Stands. Added `_CompactionRun`'s six fields plus a seven-row per-branch mapping, and pinned it to **`kind == full` only** (the other two call no summarizer, write no SQLite and never touch the counter, so there is nothing to share); `PrepareResult` therefore stays two-armed | §4.2.1 |
| 2 | Microcompaction's `CompactionDecisionContext.pre_tokens = estimate_tokens(messages)` conflicts with §4.2's "no new token estimate" and with "microcompact tokens are null" in the new event | Stands. Microcompaction's only existing estimate measures `messages_with_system` (`_compaction.py:46`) — the wrong unit; a fresh history-only one is a per-iteration cost inside the band. Changed to `None`, with `tool_results_to_clip` carrying the decision signal | §4.4.3, §4.2.1 |
| 3 | Scenario coverage still says counting lives on the coordinator | Stands, in both twins. Changed to "all three counting points live in `ContextManager._run_compaction`" | §6 |

**Method note.** Items 1 and 2 are the same "half-finished edit": rev 6 **introduced**
`_run_compaction` to fix an ownership contradiction but wrote only its signature — not what it
returns, not which kinds it covers. The same revision set a new global constraint in §4.2 ("no new
`estimate_tokens` call") without walking the document's *existing* entries against it. **Introduce a
type and you finish its definition, its branch mapping and its scope in one go; set a global
constraint and you re-read every existing entry against it.** The second is the easier one to miss:
the constraint is new, the text it constrains is old, and it is not in view while writing the new
paragraph.

**Operational record.** Both twins were patched behind pre-flight line assertions this round (each
target line verified by a fingerprint string before editing); zh had no incident. On the en side one
insertion still landed mid-paragraph (splitting "That last field exists…" in two) and was caught while
re-reading the whole edited block — the rev 6 lesson doing its job.

### rev 8 — four items

The eighth review raised four (three P2 and one P3). **All four stand**, and **all four land in
paragraphs rev 7 wrote itself**. No P1 this round.

| # | Item | Verification | Where it landed |
|---|---|---|---|
| 1 | The two short paths cross §1's layering boundary again: the coordinator produces `PreparedMicrocompact` / `PreparedMinimalHistory` and runs the transforms itself, with microcompaction reading the private `_microcompactable_indices` | Stands. Took the review's first option — **§1 is not amended** (that layering is the foundation; two two-line transforms are not worth an exception) — and added two narrow method pairs to `ContextManager` (`prepare_microcompact` / the existing `microcompact_messages`; `prepare_minimal_history` / `apply_minimal_history`), leaving the coordinator to orchestrate only | §4.2.1 |
| 2 | The `_CompactionRun → CompactionOutcome` mapping does not hold: `counted_failure` is not an Outcome field, and neither are `pre_msgs` / `post_msgs` | Stands. Changed to **five identically-named fields copied straight across**; `counted_failure` stays internal; `trigger` / `kind` / `reason` are added by the coordinator; `pre_msgs` / `post_msgs` go only into the event payloads | §4.2.1 |
| 3 | The seven-row mapping never covers a *legal* `provide_summary`, and "completed normally" pins `detail=None`, conflicting with §4.4.2's promise that `CompactionDecision.reason` feeds `detail` | Stands. Added a "host summary valid → adopted" row and pinned the `detail` composition rule (internal reason + `; ` + `decision.reason`; neither → `None`) | §4.2.1 |
| 4 | The per-kind table still writes `PreparedMicrocompact(..., pre_tokens)`, inconsistent with the field table's `None` | Stands. Changed to `prepare_microcompact()` → `tool_results_to_clip`, `pre_tokens = None` | §4.4.3 |

**Method note.** All four sit in the two paragraphs rev 7 added, and they share one shape: **the new
paragraph never went back to check the existing conventions it depends on.** §1's layering table,
§4.2's `CompactionOutcome` field list, §4.4.2's `CompactionDecision.reason` semantics — all three were
pinned earlier in this same document, and the new text referenced them from memory rather than by
re-reading. **When writing a new piece of design, re-open every existing section it has to dock with
before putting words down** — especially the ones you wrote yourself a few revisions ago; that
"I know what it says" confidence is exactly the one not to trust.

**Operational record.** This round's pre-flight line assertions caught one offset in each twin (the
per-kind row was at 676, not 674; the English paragraph ended at 564, not 565). Both failed *before*
the edit and were corrected on the spot, producing no damage to repair afterwards — the guardrail
added after the rev 6 / rev 7 incidents doing its job for the first time in full.

### rev 9 — two items

The ninth review raised two P2s. **Both stand**, both are **ownership contradicting itself inside one
section**, and both were introduced by rev 8. No P1 this round.

| # | Item | Verification | Where it landed |
|---|---|---|---|
| 1 | `detail` is not yet "identically-named, copied straight across": rev 8 had the coordinator append `decision.reason`, but the layer that actually calls `decide` and holds the `CompactionDecision` is `_run_compaction` — and the very next sentence said `detail` is copied straight into the Outcome | Stands. Changed so **`_run_compaction` composes the final value before returning**, and pinned what `decide` contains (a coordinator-composed closure holding both control layers, returning one merged `CompactionDecision`) | §4.2.1 |
| 2 | `counted_failure`'s stated purpose re-invades breaker-state ownership: the coordinator supposedly uses it for probe counting and `/context` rendering, while the state and display source of truth are pinned to `ContextManager` and the coordinator "never touches the counter" | Stands. **Field deleted** — counting and reset already happen inside `_run_compaction`, the coordinator needs only `status` and `compaction_circuit_open` (`:423`) for probe policy, and `/context` has always rendered from `get_usage_stats()` (`:1223`), never from a return value | §4.2.1 |

With `counted_failure` gone, `_CompactionRun` has exactly five fields mapping one-to-one onto
`CompactionOutcome`, and "copied straight across" is literally true for the first time; the two types
stay separate only because the Outcome also carries `trigger` / `kind` / `reason`, which
`_run_compaction` does not know.

**Method note.** Both are the same mistake: **writing a field's purpose without checking that the layer
named in that purpose can actually reach the value.** The holder of `decision.reason` is
`_run_compaction`, not the coordinator; the source of truth for breaker counting is `ContextManager`,
not any return value. **Before writing "X uses this to do Y", confirm X actually holds that value and
that Y is not someone else's ownership.** This is also the third consecutive round of ownership drift —
rev 6 the counter, rev 8 history rewriting, rev 9 `detail` and breaker display. All three happened in
**paragraphs newly written to fix the previous item**: the patch itself is where boundaries get crossed,
because attention is entirely on the thing being fixed.

### rev 10 — two items

The tenth review raised two P2s. **Both stand**, and both were introduced by rev 9. No P1 this round.

| # | Item | Verification | Where it landed |
|---|---|---|---|
| 1 | The `cancel` branch's internal `detail` is already the merged `decision.reason`, so running it through the composition rule again yields `reason; reason` | Stands. Set the `cancel` row's internal reason to `None` so the uniform rule produces `decision.reason` exactly once — **no** exception carved out for it | §4.2.1 |
| 2 | `_run_compaction`'s trigger-metadata flow contradicts itself: the signature has only `is_auto` / `reason`, while `prepare_compaction`, `PreparedCompaction` and `CompactionDecisionContext` all require `trigger` / `kind` / `reason`, and the text simultaneously claims it "does not know the trigger" | Stands | §4.2.1 |

Item 2 takes the review's second option and goes one step further: **`trigger = "auto" if is_auto else
"manual"`** — `trigger`'s vocabulary is already `manual | auto` (§3.2), and the three call sites'
`is_auto` maps one-to-one onto it (`_compaction.py:102` / `_runner.py:1167` → `auto`,
`cli/commands/compact.py:103` → `manual`); the two are the same fact in two encodings. `kind` is
constantly `full`. The "does not know the trigger" sentence is withdrawn.

**The extra step: the intermediate `_CompactionRun` is deleted entirely and `_run_compaction` returns
`CompactionOutcome` directly.** Once `trigger` / `kind` are derivable and `reason` is a parameter, it
knows all eight fields, and rev 9's stated reason for having two types collapses. The ledger on that
copying layer is unambiguous: **rev 7, 8 and 9 each produced exactly one defect in it** — a wrong field
mapping, misplaced `detail` ownership, an over-reaching `counted_failure`. Deleting it beats
maintaining the mapping table.

**Method note.** Item 1 is **one value processed by two rules**: the cancel row hard-codes
`decision.reason` as the internal reason, and the composition rule then appends `decision.reason` — each
rule is right on its own and they double up when stacked. **When adding a "uniform rule", run every
pre-existing special-case row through it and check for double application.** Item 2 is **one fact
written in four places without reconciliation**: the signature, `prepare_compaction`'s parameters,
`PreparedCompaction`'s fields, `CompactionDecisionContext`'s fields. The larger lesson is the one item 2
forced: **when an intermediate layer produces a defect three revisions running, the problem is usually
not repeated carelessness — it is that the layer should not exist.**

### rev 11 — three items

Round eleven raised 3 P2s, **all three stand**, and all three are loose ends left by rev 10's step of
deleting `_CompactionRun` and having `_run_compaction` return `CompactionOutcome` directly. No P1 this
round.

| # | Item | Verification | Landed in |
|---|---|---|---|
| 1 | Construction ownership of `CompactionOutcome` contradicts itself: §4.2.1 now says `_run_compaction` builds and returns it directly, while the stage table and later prose still say the coordinator builds it, in three places | Stands — and it was **four** places: §1's layer table, the coordinator row of §4.2.1's stage table, the dependency-direction paragraph, and the closing `commit_compaction` sentence | §1, §4.2.1 |
| 2 | `decide` may be `None`, and the two `PrepareRejected` branches return **before** `decide` is ever called, yet the uniform rule composes `decision.reason` into every result | Stands. Changed to start from `decision_reason: str \| None = None`, assigned only once `decide` has actually run and returned a usable decision | §4.2.1 |
| 3 | `_run_compaction` requires `reason`, but `compress_messages(messages, is_auto=True)` — whose old signature is pinned — has no such parameter, and the call relation only writes `decide=None` | Stands | §4.2.1 |

**Item 1 is pinned as a split by kind, not "anyone may build one".** `kind == full` is built by
`_run_compaction` — it holds all eight fields; the gate short-circuits, `microcompact` and
`minimal_history` are built by the coordinator, because **those never enter `_run_compaction` at all**
(§4.4.3). What the three kinds share is the `CompactionOutcome` **contract**, not one builder.
**Events are still emitted by the coordinator, always**, so "emit or stay silent" has exactly one home.

**And a precondition that should have been written before the dependency-direction sentence has been
added: the shared types must live in a neutral module.** Once `_run_compaction` has to
`return CompactionOutcome(...)`, and `decide`'s parameter and return types are
`CompactionDecisionContext` / `CompactionDecision`, defining any of those in the coordinator's module
voids "`ContextManager` does not import the coordinator" on the spot. Pinned: a new
`agentao/compaction/` package with `types.py` holding types only and importing nothing but the standard
library, and `coordinator.py` holding the coordinator — plus two constraints (`__init__.py` must not
re-export the coordinator, or `agentao.host`'s re-export trips import-layering rule 5,
`tests/test_import_layering.py:471`; the four private `Prepared*` snapshots stay out of `types.py`).

**Item 3 is pinned to the mapping the review proposed, plus one timing condition it did not raise.**
`is_auto=True` → `compression_threshold` and `is_auto=False` → `manual_cli` are right, but **entry 3
(`_runner.py:1167`) is running on the wrapper's `is_auto=True` default today** (§2's table, row 3) while
its true `reason` is `api_overflow` (that is what the hook dispatch at `_runner.py:1161` says). So the
mapping is only self-consistent if "PR-4 migrates entries 2 / 3 / 5 to the coordinator" is a **same-PR
precondition**; otherwise entry 3 would report `compression_threshold` and contradict the hook payload
it just emitted. That is now in §4.2.1. (**rev 12 reassigned it to "entry 3 and the legacy mapping land
atomically inside PR-3"** — the judgement held, the PR it was hung on did not.)

Two rev-10 residues were swept up along the way: both twins still had the legacy-wrapper bullet
"returning `run.messages`", where `run` *is* the deleted `_CompactionRun`; and the English twin's
`counted_failure` bullet carried a duplicated "then said the coordinator uses it" (the review flagged
it).

**Method note.** All three share one cause: rev 10 edited only **the paragraph the deleted type lived
in**, and never swept the three **cross-paragraph facts** — who builds the Outcome, who supplies
`reason`, and where the name `run.` still appears. The rule: **after deleting or merging a type, grep
the whole document for its name, for every responsibility it used to carry, and for every field it was
the sole source of.** I did grep the *name* `_CompactionRun` (which is why only the two deliberate
retractions remain in the body) — but neither the *responsibility* "builds the Outcome" nor the *usage*
`run.messages` got the same sweep. Names are easy to grep; responsibilities are not, and 2 of this
round's 3 items were responsibilities.

### rev 12 — one item

Round twelve raised 1 **P1**, and it **stands**. Nothing else this round.

| # | Item | Verification | Landed in |
|---|---|---|---|
| 1 | PR-3's and PR-4's scopes cannot both hold: the overview table has PR-3 delivering "five entries returning a trustworthy `CompactionOutcome` through the coordinator", yet the `full` Outcome is now built solely by `_run_compaction`, and both `_run_compaction` and the prepare/commit split sit in PR-4's chapter — while rev 11's new paragraph demanded PR-4 migrate entries 2/3/5 in the same PR | Stands. PR-3 has no authoritative `status` for the `full` path, and §6 forbids inferring one from message identity or counts — so it cannot meet its own acceptance | §1, §4's overview table, §4.3, §4.2.1, §4.4.3 |

**The mechanical base moves forward into PR-3, as the review proposed**, in five parts: the neutral
`types.py` + `coordinator.py`; the prepare/commit split and `_run_compaction(..., decide=None)`; the
legacy `reason` mapping and `apply_minimal_history`; the atomic rewiring of all five entries; and
`CompactionOutcome` plus the events. PR-4 narrows to "switch `decide` on over already-wired paths" — the
command-hook decision protocol, `compaction_controller=`, `provide_summary`, the cancellation semantics
and the suppression latch — and **migrates no entry**. rev 11's "same-PR precondition for PR-4" is
rewritten as "entry 3 and the legacy mapping land atomically inside PR-3", which no longer contradicts
the table.

Three items that had no PR attribution at all are pinned along the way, since this move changes which
PR they belong to:

- **§4.3's second behaviour change (crystallize no longer running on a failed summarization) lands in
  PR-3**, not PR-2 and not PR-4: PR-3 draws the prepare/commit boundary, and "no irreversible side
  effect before commit" *is* that boundary's definition. Motivated by PR-4, landed in PR-3.
- **`apply_minimal_history` belongs to PR-3** (entry 4 needs it the moment it is rewired); the two
  `prepare_*` methods belong to PR-4 — they exist only to populate `CompactionDecisionContext`, which
  has no reader until then.
- **§1's "so it splits into prepare/commit" sentence gains its landing PR** — shaped by PR-4's needs,
  landed in PR-3.

**The seam section was not moved at the time; it only gained an ownership banner** — it runs ~200 lines
and is cross-referenced a dozen times, so moving it wholesale bought a tidier table of contents at the
cost of exactly the class of defect the previous four rounds kept producing. That was flagged as a
trade-off, not a verdict. **rev 13 moved it, on the maintainer's instruction: it is now §4.2.1, and the
old §4.4.2–§4.4.5 shifted up to §4.4.1–§4.4.4.**

**The cost is in the body, not hidden: PR-3 is now a large PR**, and it alone carries the second
behaviour change §4.3 names. What it buys is that every PR can be accepted on its own; leave the split
in PR-4 and PR-3 can only fake a `status` out of counts or message identity — the very defect this plan
exists to fix (§6 already lists that as a release gate).

**Method note.** Same root as rev 11, one level deeper: rev 11 swept the body along the
*responsibility* "who builds the Outcome" but never swept the **build plan** along it. Move a
responsibility and the PR boundary moves with it — **§4's overview table is an index over every section
in this document, and any ownership change inside a section has to be reconciled against that table.**
More generally: "which PR does this belong to" had never been systematically annotated in this plan —
before rev 12 only §4.3's one "ordering note" did that job — so every round had been quietly reshuffling
the build order with nobody checking.

**The verification method gained one more check, too.** This round's line-number replacements left an
**adjacent duplicated line** in each twin (the replacement block re-supplied two lines it did not
actually span). The "identical citation sets" check run every round compares **distinct sets**, and a
duplicated line does not change a set — so it missed both. Switching to a citation **multiset** (count)
comparison plus an adjacent-duplicate-line scan surfaced both immediately. **When editing a document by
line number, read the edited block back *and* run a mechanical check that does not depend on my memory —
and the check has to be able to find "one too many", not only "one too few".**

### rev 13 — one instruction

Not a review — a maintainer instruction: **move §4.4.1 wholesale under §4.2.** rev 12 had left this as
"a trade-off, not a verdict"; it is a verdict now.

- The seam section moves under §4.2 (PR-3) as **§4.2.1**; the old §4.4.2–§4.4.5 shift up to
  **§4.4.1–§4.4.4**.
- All 69 `§4.4.x` cross-references were rewritten in one pass through a single mapping
  (`4.4.1→4.2.1`, `4.4.2→4.4.1`, `4.4.3→4.4.2`, `4.4.4→4.4.3`, `4.4.5→4.4.4`) — 69 in each twin,
  still aligned one-for-one afterwards.
- The section's banner is **inverted**: from "written under PR-4's chapter but belongs to PR-3" to
  "belongs to PR-3, which is why it sits here and not under §4.4", listing all three parts that stay
  PR-4's (the path where `decide` is actually supplied, the `cancel` and two "host summary" rows, the
  `detail` composition rule). §4.4's opening now points back at §4.2.1.
- §1's "landed in PR-3 (see the paragraph under §4's overview table)" now points at §4.2.1, and rev
  12's "the section was not moved" paragraph is marked as overturned by this round.

**The table of contents now matches the PR boundaries**: everything under §4.2 is PR-3's, seam
included; everything under §4.4 is PR-4 switching `decide` on over already-wired paths.

**Method note.** The risk in a wholesale section move is not the move — it is **number drift**: one of
69 references left on an old number is one silent mis-pointer. So this was not done as five sequential
replacements (`4.4.5→4.4.4`, then `4.4.4→4.4.3`, …), which would have each eaten the previous one's
output, but as **one regex over a single mapping table**: one scan, no interference. The mechanical
checks rev 12 added (citation multiset, adjacent duplicate lines, heading counts) were re-run
afterwards to confirm the twins are still aligned.

**And the check caught one.** The Chinese twin had a sentence reading "只走 4.4.3 那一层" — **no `§`**, so
the regex could not see it, and the move turned it into a silent mis-pointer (the new number is §4.4.2).
What found it was not the citation comparison but a **per-section multiset comparison of `§x.y`
references across the twins**: the body carried one more §4.4.2 in en than in zh, and the subtraction
exposed it. One more rule: **after moving a section, replace the `§`-prefixed references *and* sweep for
bare section numbers** — the symbol an author drops in passing is exactly what automated replacement
cannot reach. After the fix both bodies carry the same 89 `§` references, one for one.

### rev 14 — closing §8's first downgrade condition

The maintainer reports that **the plugin marketplace has not been built**. That triggers a review of
§8's first condition, not the grep the condition prescribes. Verdict: **the condition is undecidable
by its own method; closed in place, and §3.1 stays a P1.**

Four grounds, all checked against source:

1. **The premise is not satisfied.** The condition asks for a **measured zero** across shipped
   plugins; "no marketplace yet" delivers **no population to measure**. Two different epistemic
   states, and only the first supports a downgrade.
2. **Plugins do not ship through a marketplace.** `PluginManager` loads from `~/.agentao/plugins`
   (`embedding/plugins/manager.py:96`) and `<cwd>/.agentao/plugins` (`:100`); the marketplace is only a
   directory level inside that (`:297-312`). So today's real plugin population is exactly the one §8's
   next bullet already concedes a grep cannot reach — a zero measured with an instrument that cannot
   reach the target population is not evidence.
3. **The direction is inverted.** If the question were "how much damage right now", zero adoption →
   less damage → downgrade. But the decision at hand is "when is a contract defect cheapest to fix",
   and the answer is **before the ecosystem exists**. PR-1 is a matcher **behaviour change**
   (`{"trigger": "auto"}` matches all five entries today and stops matching manual afterwards), so
   doing it now touches only locally hand-written rules, while doing it after a marketplace ships makes
   it a breaking change to third-party config.
4. **One shipped document is now wrong, regardless of adoption.**
   `docs/releases/v0.4.4.md:131` states `trigger | PreCompact only: auto (no manual site exists)`; that
   parenthetical was true at 0.4.4 and false since manual `/compact` shipped. Existing tests do not
   catch it either — and in fact **encodes the same stale premise in its own name and docstring**:
   `test_pre_compact_trigger_always_auto_for_every_emit_site`
   (`tests/test_hooks_pre_compact_payload_claude_shape.py:60`, docstring "no manual surface"), whose
   site list (`:62-66`) holds four entries with `manual_cli` not among them. PR-1 has to update that
   test too.

**One pragmatic point on top: the label changes no decision here.** PR-1 is already first in the
dependency order; P1 vs P2 governs *whether* something is built, not *when*. So the condition is closed
and no further cost is spent on it.

Noted as PR-1 doc work: add an **erratum** line to `docs/releases/v0.4.4.md:131` — the same treatment
`stop-precompact-hooks-plan` got, annotating rather than rewriting a published historical statement.

**Method note.** The lesson is that **"no data" and "data is zero" have to be recorded separately.**
§8's original wording collapsed them into one sentence, so "the marketplace was never built" *looked*
like it satisfied the downgrade condition — when in fact nothing was ever measured. The rule: **a
downgrade condition premised on a measured zero must also name the population it is measured over; when
that population does not exist, the correct verdict is "undecidable, severity unchanged", not "zero".**
The other half is worth keeping too: **the same evidence yields opposite conclusions under different
questions** — "nobody uses it" mitigates *present damage* and simultaneously argues for *acting sooner*.
A downgrade condition has to say which question it serves.