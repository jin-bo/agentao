# Session-lifecycle hook values — codex #44349 against agentao's three surfaces

> **⚠️ Everything described here is implemented.** §1 was a priority ordering of findings; it is now
> a summary table. `docs/reference/configuration.md` §11 is the authority on the values themselves. Exactly one item needs a maintainer decision (§4, the
> ACP question); the rest of the implemented scope was wiring, not deciding. Quote this line whenever
> you quote the table.

**Status:** **fully implemented** (2026-09-10, working tree; suite green at 4991) — §6.1 and §6.2
(the CLI values and the resume path), §4 (ACP), and §6.3 (compaction), each cleared by its own
maintainer review. This document is now the **rationale for landed behaviour**, not a proposal. The emitted values are documented in
`docs/reference/configuration.md` §11. The body below is the **rev 4** analysis. **rev 4 closed rev 3's two remaining P2s and one stale
scope line**: load failure splits into the startup and interactive cases (§6.2's last two rows); §5's
"unchanged" for v1 is limited to matching and execution count, since a v1 rule receives the envelope
verbatim and the input field does change; and §1 no longer lists compaction under wiring.
**rev 3 narrowed the implementable scope after a maintainer review.** The direction holds, but only two pieces are implementable here, **the CLI values
and the resume path**. ACP (§4) and compaction (§6.3) are recorded as gaps this round and handled
separately. rev 2's §6 carried two P1 errors, both of the form "implementing this as written
double-dispatches"; they are corrected in place and marked. **rev 1 was scoped to half the problem.** It covered
only `SessionStart.source` and asserted that this was the one required field in the field table still
shipping a constant. `SessionEnd.reason` is exactly symmetric, and the probe table rev 1 leaned on
**carries its two rows immediately below the ones rev 1 quoted**. The file was renamed from
`session-start-source-vs-codex` in the same pass. The other finding from that review pass (the
`/goal` no-progress guard) was implemented separately and is out of scope here.
**Anchors:** codex `openai/codex@9688359977` (delta `b7cd519c76..9688359977`, 551 commits), key
commit `e444aa99d7` "Distinguish forked sessions in session-start hooks" (#44349, 2026-09-10);
agentao `main@770c045`.
**Method:** both sides read from source, every claim carrying an inline `file:line`. The matcher
semantics cite the **measured** result in `hooks-probe-2.1.251.md` §G6, not a reading of the reference.
**Prior record:** the event-field table in `hooks-claude-contract-conformance-plan.md` §5.3 already
carries both rows, `source` at line 1517 and `reason` at line 1518. This document is not the first
report. What it adds is that they are the **last two unwired required fields in that table**.

---

## 1. Findings

| Priority | Finding | Evidence |
|---|---|---|
| **Wiring (no decision needed)** | `SessionStart.source` is always `startup`, so one of four upstream values is reachable | §2 |
| **Wiring (no decision needed)** | `SessionEnd.reason` is always `other`, so one of five upstream values is reachable | §2 |
| **Wiring (no decision needed)** | `/clear` **misreports on both events**, where upstream says `clear` for each | §2 |
| **Wiring (no decision needed)** | `/resume` dispatches **neither** event | §2 |
| **Implemented** | ACP dispatched neither event; the fix is **not** a pair each on new/load, see §4 | §4 |
| **Implemented** | Post-compaction dispatched no `SessionStart`; scoped to a **successful full** compaction, see §6.3 | §6.3 |
| **Do not adopt** | codex's new `fork` source | §3 |

**Landed in three batches**, each with its own maintainer review: the first four rows (the CLI values
and the resume path), then §4 (ACP), then §6.3 (compaction). The prerequisites the last two carried
are answered in their own sections.

**In one line:** this is not an open policy row, it is two stragglers. The §5.3 table now sorts into
three classes:

| Class | Fields | State |
|---|---|---|
| Required, still constant | `SessionStart.source`, `SessionEnd.reason` | **this document's subject** |
| Conditional, still unwired | `SessionStart.model` (no caller passes it), `PostToolUseFailure.is_interrupt` (**no caller anywhere in the tree**) | out of scope here |
| Formerly "exists, unplumbed", now wired | `tool_use_id` (three dispatch sites, incl. `agentao/runtime/tool_runner.py:377`), `duration_ms` (`tool_executor.py:717`) | the precedent |

The third row is this document's argument: the same table's other instances of the same problem were
all wired later.

---

## 2. What agentao does today: two constants where nine values belong

Both adapter methods take a value argument (`source` at `agentao/plugins/hooks/_payload.py:47`,
`reason` at `:72`) and the profile serializer writes both out (`_profile_payload.py:100` and `:104`).
**Not one of the four dispatch sites passes either.**

| Trigger | `SessionStart` | `SessionEnd` |
|---|---|---|
| Interactive startup | `startup` ✓ | — |
| Interactive exit | — | `other` (upstream `prompt_input_exit`) **under-reported** |
| `agentao run` | `startup` ✓ | `other` **under-reported** |
| `/clear` | `startup` **wrong**, should be `clear` | `other` **wrong**, should be `clear` |
| `/resume` | **no dispatch** | **no dispatch** |
| After a successful compaction | **no dispatch** (should be `compact`) | — |
| ACP | **no dispatch** | **no dispatch** |

The dispatch sites are `agentao/cli/session.py:95` (start) and `:124` (end), plus
`agentao/cli/run.py:698` and `:827`. `/clear` reaches both through
`agentao/cli/commands/reset.py:30` and `:53`, taking the default each time. `/resume`
(`cli/commands/sessions.py:87`) calls neither.

**Wrong values and under-reported values are different.** `other` is upstream's own value for "none
of the named causes", so emitting it when `agentao run` finishes is merely unspecific. `/clear` is a
**named cause**, so taking the default on both events reports a known situation as a different one.

**Why the values have consequences.** The dispatcher compares a `SessionStart` matcher against
`source` and a `SessionEnd` matcher against `reason` (`agentao/plugins/hooks/_dispatcher.py:573` and
`:575`). That is **measured**: the table in `docs/reference/hooks-probe-2.1.251.md` §G6, lines
281-285, records real `claude` 2.1.251 behaviour across **four rows covering both events**. A
`startup` matcher fires against a `startup` source and a `resume` matcher does not; an `other`
matcher fires against an `other` reason and a `clear` matcher does not. So in agentao:

- A rule written `matcher: "resume"` / `"clear"` / `"compact"` / `"logout"` / `"prompt_input_exit"`
  is permanently dead, **with no diagnostic**. The profile's one-shot diagnostic covers
  *unimplemented fields*, and both of these are implemented. They simply never vary.
- Rules written `matcher: "startup"` and `matcher: "other"` fire more often than they should.

**Test status.** `tests/test_hooks_profile_payloads.py:38` passes `source="resume"` explicitly. The
plumbing is tested. Nothing in production feeds it.

---

## 3. What codex did, and why it covers only half

The Why section of `e444aa99d7` (#44349) reads: forked threads reported `startup`, so startup hooks
ran again even when the context was inherited from the parent, and resuming with supplied history
also reported `startup` instead of `resume`. The fix adds `fork` as a `SessionStart` source, exposes
it in the hook input schema, and settles the rule as "a fork parent means `fork`, supplied history
without one means `resume`".

**Same class, different origin.** codex's wrong value came from a new session shape. agentao's comes
from an argument nothing ever passes. The consequence is identical: a matcher written to upstream
semantics does not fire, and upstream compatibility is the entire purpose of the profile contract.

**This peer commit is the occasion, not the boundary.** It touches `SessionStart` only. The
`SessionEnd` half was not borrowed from codex; it came out of this document's own rev 2 self-review.
Do not read the codex anchor as evidence that `SessionEnd` was checked and found clean on either side.

**`fork` is not adopted.** agentao has no thread fork. Sub-agents are one-shot workers (see
`codex-subagent-v2-vs-agentao.zh.md`) and produce no new session with inherited context. Declaring a
value that can never be emitted makes the profile's enumeration longer without making it truer.

---

## 4. ACP: implemented, and not by adding three dispatch calls

**The original gap.** `agentao/acp/` contained **zero** references to `SessionStart` or
`SessionEnd`, while the interactive CLI and `agentao run` dispatched both, with no doc, comment, or
test behind the divergence.

**rev 2 proposed a wrong shape here** ("a pair each for `session/new` and `session/load`") and it is
withdrawn. What landed follows the three constraints a separate maintainer review set:

**1. Start must precede the first prompt and follow the history restore.** Injected context is
appended to `agent.messages`, so firing before the restore discards it and publishing the session
before firing lets a pipelined `session/prompt` start a turn in front of it. The dispatch therefore
runs in a `before_publish` callback added to `AcpSessionManager.create` — **after the duplicate check
and before publication**, both halves load-bearing:

- *After the duplicate check*, because `SessionStart` hooks are arbitrary user commands and running
  them for a `session/load` that then fails on a duplicate id runs side effects for a session that
  never existed.
- *Before publication*, because a client supplies its own id on `session/load` and can pipeline a
  prompt behind it, and `turn_lock` is acquired **non-blocking** — a racing prompt is rejected rather
  than queued, so "publish then fire" would turn a hook into a spurious error.

The cost is explicit: other sessions' lookups block for the callback's duration, since they share the
registration lock. It is bounded by the hook timeout and paid once per session creation. One existing
lock, no state machine.

**2. End follows the real close path.** It sits in `AcpSessionState.close()`, behind the idempotence
guard and before any resource is released, with `reason="other"` — ACP has no named upstream cause,
and `other` is upstream's own value for exactly that. It does **not** follow new/load, because ACP
holds several sessions at once and creating or loading one ends nothing, and it does not follow a
cancelled turn, which is not a session ending. A construction failure cleans up the agent and emits
no End; a killed process promises nothing.

**3. The dispatch is shared, but the CLI is not imported.** The terminal-independent dispatch and
context injection moved to `agentao/plugins/hooks/lifecycle.py` (`fire_session_start` /
`fire_session_end`). The CLI's two `dispatch_plugin_session_*` helpers are now thin aliases that keep
the printing, and ACP reaches the same behaviour through `agentao/acp/_lifecycle.py`. User notices go
out through a new `_transport_helpers.write_user_notice` as a `session/update` chunk, because ACP has
no notice channel of its own and exit 2 on these two events **is** the user channel.

**One accepted weakness.** On `session/new` the notice is written before the response that tells the
client which sessionId it just created, so a strict client may drop it. The event's substantive
channel is the context injected into history, which is unaffected, and buffering a diagnostic until a
turn that may never come trades a dropped message for one that never arrives.

**The values as landed:** `session/new` gives `startup`; `session/load` and a successful startup
resume give `resume`; a startup resume that falls back to a new session gives `startup`, because the
value follows what happened rather than which method was called; a real close gives `other`; a failed
load, a duplicate load, and a cancelled turn dispatch nothing. Tests:
`tests/test_acp_session_lifecycle_hooks.py`.

---

## 5. Two risks that are not the same risk: changing a value vs adding a dispatch site

**The matcher symptom is profile-only.** `_dispatcher.py::_matches:509` sends only non-v1 rules
through the Claude matcher, and `CLAUDE_FLAT_EVENTS` is `{Stop, PreCompact}`
(`agentao/plugins/models.py:230`), so an `agentao-v1` rule on either event takes the envelope branch,
filters on `toolName` alone, and therefore does not filter at all. A dead rule — a matcher written
`resume` that never fires — exists only under `claude-code@profile-1`.

**But "v1 does not filter" is the source of the risk in adding dispatch sites, not a guarantee
against it.** §6's two kinds of change carry entirely different risk:

| Kind of change | Effect on profile rules | Effect on v1 rules | Risk |
|---|---|---|---|
| **Change an existing event's value** (`/clear` emits `clear`) | Matching changes: `clear` rules start firing, `startup` rules stop | **Matching and execution count unchanged**, but **the input field changes** | Low but non-zero, and it is the fix itself |
| **Add a dispatch site** (`/resume`, post-compaction) | One more execution | **One more execution too** | **Execution counts and side effects change** |

**Row one's "unchanged" covers matching and count only, not "v1 cannot see it".** A v1 rule receives
the agentao envelope verbatim (`_dispatcher.py:605-609`: a profile rule gets the flattened payload, a
v1 rule gets `payload` itself), and `data.source` / `data.reason` are inside it. A v1 script that
reads either field will see the value change from `startup` to `clear`, and **its own behaviour may
change as a result**. No compatibility layer is needed, since correcting the value is the point, but
it does make "changing a value cannot affect v1" a false statement: the regression to assert is that
matching and execution count are unchanged, not that the input is.

The second row is what this document lacked before rev 3. A user with a working `agentao-v1`
`SessionStart` hook does not have it run on `/resume` today; after a new dispatch site it does, and a
v1 rule has no matcher with which to opt out. **Every new dispatch site needs a v1 regression test**
proving the change in execution count for existing v1 hooks is intended rather than incidental.

---

## 6. If implemented: the narrowed route

**Scope: CLI values plus the resume path.** ACP (§4) and compaction (§6.3) are not in this round.

### 6.1 Thread the values through the existing call chain

Give `dispatch_plugin_session_start(agent, session_id, *, source=...)` and
`dispatch_plugin_session_end(agent, session_id, *, reason=...)` one keyword argument each. **Add no
call sites.** Have the four that exist pass their own value:

| Entry point | `source` | `reason` |
|---|---|---|
| Interactive startup (`input_loop.py:272`) | `startup` | — |
| Interactive exit | — | `prompt_input_exit` |
| `agentao run` | `startup` | `other` (**kept**, see below) |
| `/clear` (`reset.py:30` / `:53`) | `clear` | `clear` |
| `/new` (`reset.py:63`, **shares `_reset_session`**) | `clear` | `clear` |

The `/new` row has to be stated, not left to the implementer. It shares one reset path with `/clear`
and differs only in keeping memories; upstream's vocabulary has no value of its own for it, and
`clear` is the nearest true one.

A normal `agentao run` **keeps `other`**. Upstream's `prompt_input_exit` means leaving an interactive
prompt, which a non-interactive run is not, so `other` is a legitimate fallback here rather than an
under-report.

### 6.2 Fix the order and count for both resume paths (rev 2 was wrong here)

**rev 2 said "add two dispatches inside `resume_session()`". That is wrong.** The command-line
`--resume` runs the same function: `entrypoints.py:97-99` calls `resume_session(...)`, then `main()`
enters `run_loop()`, which calls `on_session_start()` **unconditionally** at `input_loop.py:272`.
Implementing rev 2 would emit `resume` and then `startup`, and would emit a `SessionEnd` for a session
that never started.

The rule, per scenario:

| Scenario | `SessionEnd` | `SessionStart` |
|---|---|---|
| Startup resume (`agentao --resume`) | **none** — there is no prior session | **exactly one**, the existing `run_loop` call, reporting `resume` |
| Interactive `/sessions resume` | yes, `reason="resume"` | yes, `source="resume"` |
| Interactive resume that **fails to load** (the error branch at `sessions.py:116`) | **none** | **none** — the current session is left intact |
| Startup resume that **fails to load** (the CLI then starts normally) | **none** | **one `startup`** |

The implementation note for the first row: `resume_session()` does not dispatch. It leaves a one-shot
marker on the CLI, and the `on_session_start()` call `run_loop` already makes reads it to choose
`startup` or `resume`. The number of dispatch sites is then unchanged and only the value differs,
which puts this back on row one of §5's table rather than row two.

**The marker is set only on a successful load**, which is what separates the last two rows.
`entrypoints.py:97-100` calls `_resume(...)` and then `cli.run()` **unconditionally**: when a startup
resume fails there was never a prior session, so no `SessionEnd` is owed, but a new session does
begin and is owed a `SessionStart` — one that is not a resume, hence `startup` rather than `resume`.
Writing "load failure dispatches neither" as one rule would silence a session that really started.

### 6.3 Compaction: implemented, scoped to a successful full compaction

The prerequisite was never layering, it was **scope: which compactions count as a lifecycle
rebuild?** The answer is full ones, and only when they succeed:

| Case | Dispatches `SessionStart(source="compact")` |
|---|---|
| manual `/compact` succeeds | once |
| automatic threshold full compaction succeeds | once |
| full compaction after an API overflow succeeds | once |
| `microcompact`, `minimal_history` | never |
| failed, cancelled, skipped | never |

`microcompact` runs on most iterations inside its band, so hanging startup hooks on it would
re-inject the same context over and over; `minimal_history` is the overflow ladder's last rung, whose
purpose is to **shrink** a request the provider has already refused twice, not to re-seed one.
Neither rebuilds the session. **That is also why this does not subscribe to `CONTEXT_COMPRESSED`**,
which is not gated by kind — its emit site filters only `status == "skipped"`, so subscribing would
fire startup hooks on every lightweight trim.

**Placement is the one delicate part.** The success branch in `coordinator.py` replaces history and
then assembles `messages_with_system`, and that snapshot is what the caller sends next — **the two
API-overflow rungs retry with it immediately**. The dispatch therefore sits between those two steps:
after the history replacement, or the injected context is discarded wholesale, and before the
snapshot, or that retry carries a request the hook's context never reached. The injected content
lands in `post_est_tokens` for free. If the request still overflows, the existing `minimal_history`
rung handles it; no hook-specific retry or carry-over was added.

One consequence is accepted rather than corrected: `CONTEXT_COMPRESSED`'s `post_msgs` and
`post_est_tokens` are measured after the injection, so a host charting compaction effectiveness sees
the injected message counted against the transform. Measuring before the injection would report a
history that is not the one the next request carries, which is the worse of the two, and the event
already documents itself as describing the post-compaction window.

**`on_session_start` is not called.** A compaction keeps the session id, emits no `SessionEnd`,
restarts no replay, and archives no memory session. Only the plugin dispatch applies, so the shared
`plugins/hooks/lifecycle.py::fire_session_start` extracted in §4 is called directly.

**A hook failure cannot undo a compaction that succeeded.** The dispatch swallows everything: history
has already been rewritten when it runs, and two of its three callers are the overflow recovery
ladder, so a hook fault must never be able to end the turn the compaction exists to save. User
notices ride `PLUGIN_HOOK_FIRED`, the same host channel `UserPromptSubmit` and `PreCompact` use.

Tests: `tests/test_compaction_session_start_hook.py`.

### 6.4 Explicit non-goals

No lifecycle manager, no generic event framework, no `fork` enum (§3), and no ACP work in this round
(§4).

### 6.5 Tests

Organized around **real entry points** rather than one per enum value:

- One per real entry point: startup, `/clear`, `/new`, `agentao run`.
- One per resume scenario, asserting **order and count**: startup resume has one `SessionStart` and no
  `SessionEnd`; interactive resume has exactly one of each, in that order.
- One asserting the session ids on `SessionEnd` and `SessionStart` belong to the **old and new**
  session respectively.
- **Two failure cases, kept apart**: an interactive resume that fails to load emits neither event and
  leaves the current session intact; a startup resume that fails to load emits no `SessionEnd` and one
  `source="startup"` (**not** `resume`), proving the one-shot marker is set only on a successful load.
- A **v1 regression** (§5): after `/resume` gains a dispatch site, the change in execution count for
  an existing `agentao-v1` `SessionStart` rule is asserted rather than incidental.
- Two value regressions: `/clear` no longer reports `startup`, and no longer reports `other`.

### 6.6 Out of scope but adjacent

`SessionStart.model` and `PostToolUseFailure.is_interrupt` are likewise unwired (see §1). Both are
conditional fields, so their absence is conformant and wiring them is a product call rather than a
conformance fix.
