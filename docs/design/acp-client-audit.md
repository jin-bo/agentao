# Agentao — `acp_client/` Subpackage Audit

**Status:** Review record. Drafted 2026-07-23 from a June-grade four-dimension
audit of the `agentao/acp_client/` subpackage — the ACP *client* (Agentao talking
**out** to project-local ACP servers it spawns as child processes over stdio).
**This is an evidence-backed findings list + prioritized proposal, not an approved
plan.** The subpackage was **not in scope** of the 2026-06-19
`optimization-opportunities-review.md` (that pass covered the runtime, packaging,
CLI, and the ACP *server*); this closes that gap.
**Audience:** Agentao maintainers.
**Companion:** `acp-client-audit.zh.md`.
**Related:**
- `optimization-opportunities-review.md` — the June audit whose method and
  tiering this doc mirrors; several conventions here (do-now bug vs opportunistic
  consolidation, `gap ≠ need`, verified non-findings) are inherited from it.
- `acp-server-conformance-review.md` — the ACP *server* side; this is its client mirror.
- `core-boundary-review.md` — the render/display-in-core question (AC5) respects
  the boundary it documents.

**Method:** four parallel evidence-gathering passes — **complexity/structure**,
**correctness/concurrency/resource-safety**, **duplication/reuse**, and
**contract/boundary/security** — each required to cite `file:line` and back every
claim with `grep`/read rather than intuition, consistent with this repo's
*evidence-before-recommendation, gap ≠ need* discipline. The highest-impact and
least-reversible claims (the two concurrency bugs, the process-kill limitation,
the dead-code removals, and the render/injection surface) were **independently
re-read against source** before this doc landed. Code references anchored to
`main`@`278e92a` (2026-07-23). Line numbers will drift — re-grep before acting.

---

## TL;DR

`acp_client/` is a **well-built, defensively-coded subpackage** — 19 files /
~5.8k LOC with 11 test files exercising it, a deliberate mixin decomposition of
the manager, correct provider-credential scrubbing on spawned children, and a
genuinely hard concurrency contract (single-active-turn lock + handshake lock +
sticky-fatal recovery) that is *mostly* right. There is **no security hole and no
architectural emergency.** The auditors' strongest signal was **convergence on a
few real items**, not a long defect list.

The findings split into three bounded classes:

1. **Do-now, low-risk (AC1, AC4).** One trivial concurrency bug (`stop_all`
   iterates a live dict — a one-line `list()` fix) and three dead / test-only
   code surfaces that read as live behavior. Both are safe, small, and reduce
   real confusion.
2. **Do-now-ish, small (AC2) + recorded limitation (AC3).** A lock-asymmetry in
   the recovery counter (AC2) is a small correctness fix; the process-tree kill
   gap (AC3) is a **documented** limitation whose fix is not drop-in (needs a
   spawn-side change) — recorded, opt-in.
3. **Opportunistic / larger (AC5–AC8).** Terminal-escape + size hardening of
   server-controlled display text (AC5), and two maintainability items: the
   handshake "sticky-fatal dance" copy-pasted across 5 entry points (AC6, the
   *dangerous* kind of duplication) and the 261-line `prompt_once` (AC7). These
   carry the real ongoing cost but also the most behavioral risk — Tier-3, opt-in.

> **The priority ranking is the maintainer's call.** This doc supplies
> grep-verified evidence and a *suggested* ordering; it does not declare what is
> or isn't worth the maintainer's time.

> **Scope honesty — the concurrency findings (AC1/AC2) bite only the documented
> multi-threaded embedding surface.** Inside the interactive CLI,
> `get_status`/`stop_all`/`send_prompt` run on one thread and never race. But the
> module is explicitly documented for concurrent daemon/workflow embedding
> (`turns.py` docstrings on monitoring + worker threads), and **per-client reader
> threads already run callbacks concurrently with the main thread**, so the races
> are real for that surface — not hypothetical.

---

## Baseline metrics

| Metric | Value | Reading |
|---|---|---|
| Source files / LOC | 19 / ~5,785 | largest never-audited subpackage |
| Test files referencing it | 11 (`test_acp_client_*` ×7 + 4) | well-covered |
| Largest file | `manager/turns.py` (892) | turn orchestration + ephemerals |
| Largest method | `manager/turns.py::prompt_once` (261, `452–712`) | **fattest method in the whole repo** |
| Child-env credential scrub | present (`process.py:124 build_child_env`) | security-positive (see N1) |
| Remote/URL transport | none (stdio-only) | voids SSRF surface (see N2) |

---

## Tier 1 — Do now, low risk

### AC1 — `stop_all()` iterates the live `_clients` dict → `RuntimeError` + leaked subprocesses under concurrent embedding · MEDIUM (bug — do now, trivial)
`lifecycle.py:75`: `for name, client in self._clients.items():` iterates the
**live** dict — while the very next block (`:82`) defensively copies
`list(self._ephemeral_clients.items())`. The asymmetry is the tell. `_clients` is
`pop`-ed from **lock-free** call sites reachable concurrently:
`_check_cached_client_alive` (`recovery.py:376`) is invoked *without* the
handshake lock from the doc-stated "read-only" accessors `get_status`
(`status.py`), `readiness` (`status.py`), and the `prompt_once` pre-check
(`turns.py:525`); `stop_server` (`lifecycle.py:171`) and `_evict_cached_client`
(`lifecycle.py:114`) also `pop` off that path.

- **Failure scenario:** a monitor thread polls `readiness()`/`get_status()` on a
  server whose subprocess died while still `READY`; its
  `_check_cached_client_alive` runs `self._clients.pop(name)` (`recovery.py:376`)
  *while* another thread is inside `stop_all()`'s `for … in self._clients.items()`
  → `RuntimeError: dictionary changed size during iteration`. `stop_all` aborts
  partway; every handle after the crash point never gets `handle.stop()` →
  **leaked ACP subprocesses.**
- **Fix (single-point, trivial):** iterate a snapshot —
  `for name, client in list(self._clients.items()):` — matching the ephemeral
  block one line below. Scope the PR to this method.

### AC4 — Three dead / test-only surfaces read as live behavior · LOW (quick win)
Each is grep-verified to have **zero production callers/writers**; all three are
kept alive only by tests, so a reader treats inert machinery as real:

- **`interaction.py:56` `deadline_at` + `:122` `expire_overdue`** — `deadline_at`
  is **never assigned** anywhere outside its `None` default (grep: only the
  docstring `:43`, the field default `:56`, and the two *reads* at `:134–135`).
  The `deadline_at is not None` guard is therefore **always False in production**,
  so `expire_overdue()` is a guaranteed no-op — the entire deadline/expiry
  mechanism is inert, wired only by tests that set the field by hand.
- **`turns.py:760` `_open_ephemeral_client`** (the lock-acquiring wrapper) — zero
  production callers; `prompt_once` calls `_open_ephemeral_client_locked` (`:789`)
  directly (`turns.py:575`). Every other mention is a comment. Only a test
  monkeypatches it as a spy — which can't even intercept the real `_locked` path.
- **`recovery.py:166` `_note_handshake_failure`** (bare, non-`_and_maybe_fatal`)
  — zero production callers (grep across `agentao/`); only tests use it to
  simulate a streak.
- **Fix:** delete the two dead helpers and either wire `deadline_at` or remove the
  expiry machinery. Removal touches the tests that reference them — do it as one
  small, clearly-scoped cleanup, not bundled with a behavior change.

---

## Tier 2 — Small correctness fix + a recorded limitation

### AC2 — Recovery counter written without `_recovery_lock`; "read-only" status accessors mutate recovery state off the lock path · LOW–MEDIUM (bug — do now, small)
Same root as AC1 (lock-free mutation from poll paths), distinct defect.
`recovery.py:389` writes `self._handshake_fail_streak[name] = 0` **without**
`_recovery_lock` — while **every other** write to that map holds it
(`recovery.py:164,168,184,200,300`; e.g. `_clear_fatal:296–300` wraps the
identical `= 0` write in `with self._recovery_lock`). The enclosing
`_check_cached_client_alive` also `pop`s `_clients`, closes clients, calls
`_mark_fatal`, and mutates handle state — all reachable lock-free from the
doc-stated "read-only" `status.py` accessors.

- **Failure scenario:** thread A in `_note_handshake_failure_and_maybe_fatal`
  does a locked read-modify-write on the streak (read `1` → write `2` → trip
  fatal at `>1`); thread B (a lock-free `get_status` poll on a died-in-`READY`
  server) executes the bare `= 0` at `:389` **between A's read and write**,
  because B never contends for the lock A holds. The reset is lost or clobbers
  A's increment → the "2 consecutive handshake failures ⇒ sticky-fatal"
  accounting desyncs, flipping a server fatal one crash early, or failing to trip
  when it should. Impact is bounded (off-by-one on a recovery counter), but a
  method documented "read-only" (`status.py`) silently closing clients and
  evicting `_clients` is its own surprise.
- **Fix (small):** take `_recovery_lock` around the `:389` write (and audit the
  other mutations in `_check_cached_client_alive` for the same). Alternatively —
  and this is a maintainer judgment, not a forced choice — give the status
  accessors a lock-free *read-only* liveness probe that defers the eviction/mark
  to the next real recovery call, so "read-only" is true again. The narrow lock
  fix is the smaller change; the probe split is the cleaner contract.

### AC3 — Process teardown reaps only the direct child; grandchildren orphaned on SIGKILL · LOW–MEDIUM (documented limitation — recorded, opt-in)
`process.py:127` spawns the server with **no** `start_new_session=True` / process
group; `_stop_unlocked` escalates `terminate()` (`:234`) → `kill()` (`:240`),
both signalling only the immediate child. `acp_client/` never uses the repo's
`capabilities/process.py::kill_process_tree` (killpg / `taskkill /T`).

- This is **explicitly documented** in-code (`process.py:209–216`): the design
  prefers the graceful stdin-EOF path precisely so the server reaps its own
  MCP/shell grandchildren, "which `terminate()` here cannot." So it is a
  *known* limitation, not an oversight.
- **Failure scenario:** a server that ignores both stdin-EOF **and** SIGTERM is
  SIGKILLed; its grandchildren (its own MCP-stdio / shell children) are orphaned
  and survive. Repeated `restart_server`/`stop_all` cycles accumulate them.
- **Fix is NOT drop-in.** `kill_process_tree` uses `killpg(proc.pid)`, which only
  isolates descendants when the child is a session/group leader — so a correct
  fix must **both** spawn with `start_new_session=True` **and** switch the kill
  path to the tree reaper. Two options, maintainer's call: (a) make that
  spawn+kill change and route through `kill_process_tree`, or (b) leave the
  documented graceful-first design and accept the SIGKILL-tail leak as a rare,
  recorded edge. Recorded, not scheduled.

---

## Tier 3 — Opportunistic hardening + larger refactors (opt-in)

### AC5 — Server-controlled display text is not sanitized for terminal escapes; agent chunks are size-unbounded · LOW–MEDIUM (opportunistic hardening)
The renderer prints a **third-party** ACP server's output to the user's terminal
(`render.py`). Two gaps:

- **Terminal-escape injection.** The plain fallback (`render.py:117`,
  `sys.stdout.write(render_all_plain(...))`) writes server text **verbatim** —
  `render_plain:74` only does `\n`→`\n  `, no escape-sequence stripping. The Rich
  path escapes Rich *markup* (`rich.markup.escape`, `:185,195,197`) but that
  handles `[bold]`-style tags, **not** raw ANSI/terminal control sequences
  (`\x1b[…`, OSC set-title, clipboard). Source text is fully server-controlled
  (`agent_message_chunk` → `helpers.py`). A malicious/compromised server can emit
  cursor/screen/title manipulation into the terminal; the plain path is the
  unambiguous vector.
- **Unbounded size (DoS).** No length cap on the stdout read (`process.py` line
  iteration) or on `agent_message_chunk`/`agent_thought_chunk` text
  (`helpers.py` returns them **untruncated**, unlike every other kind, which caps
  at 40–120 chars). A server emitting one multi-GB line forces unbounded
  in-memory buffering.
- **Fix (opportunistic):** strip/replace C0/C1 control bytes (keep `\n`/`\t`)
  before display on **both** paths, and cap per-line / per-chunk length on read.
  Fold in next time `render.py` / `helpers.py` is touched. (Trust posture: the
  same server already runs as a spawned child with scrubbed env — this is
  defense-in-depth on the *output* channel, not a headline hole.)
- **Status: IMPLEMENTED** (escape sanitization on both paths + per-chunk cap);
  the readline-level cap is **deferred** (framing rewrite). See the Implementation
  status section below.

### AC6 — The re-session + sticky-fatal handshake "dance" is copy-pasted across 5 entry points · MEDIUM (dangerous duplication — consolidate)
The classification *primitives* were already extracted (`_reclassify_as_handshake_fail`
/ `_note_handshake_failure_and_maybe_fatal` / `_note_handshake_success`), but the
**sequence that wraps `create_session`/`initialize`** in
`try … except BaseException: if reclassify: note-fatal; raise` + trailing
`note-success` is hand-inlined at **5 sites** (grep-confirmed):
`connection.py:170–173`, `:263–271`, `:400–406`, `turns.py:662–665`, `:840–869`.

- **Why this is the dangerous kind:** the code itself repeatedly comments that
  *every* handshake entry point "must also flip sticky-fatal … otherwise a host
  choosing one API silently opts out of the recovery contract." The invariant is
  fragile **because** it is copied — a 6th entry point that forgets the dance
  silently breaks recovery, with no test forcing the wrapper's presence.
- **Fix:** one `_handshake_guarded(fn, name)` wrapper/context-manager that runs
  the setup call, does reclassify+maybe-fatal on failure and `note-success` on
  success; all 5 sites call it. This is real consolidation against a
  correctness-adjacent invariant — worth doing on its own, not merely
  opportunistically. Two related closures (the client-callback builders in
  `connection.py` ≈ `turns.py:_open_ephemeral_client_locked`) can fold in with it.

### AC7 — `prompt_once` is a 261-line, depth-~8 method mixing 7 concerns · HIGH maintenance cost (refactor, opt-in)
`turns.py:452–712` — the single fattest method in the repo (confirmed span; next
method `_rollback_ephemeral_on_busy` begins at `:714`). It interleaves: policy
resolution + handle lookup, recovery pre-check, ephemeral setup under the
handshake lock, fail-fast turn-lock acquire + rollback, a **cached-client
reuse/re-session branch that re-implements `_ensure_connected_locked`** (this is
one of AC6's 5 copies), turn run + `PromptResult` build, and a deeply-nested
`finally` ephemeral teardown. The depth-8 re-session-inside-`finally` region is
exactly where a concurrency regression is most likely to hide.

- **Extractable blocks (with the AC6 helper doing the heavy lifting):**
  `_setup_ephemeral_or_defer(...)`, `_reuse_cached_client_for_prompt_once(...)`
  (→ the shared `_handshake_guarded`), `_teardown_ephemeral_after_prompt(...)`.
  Splitting drops the core path to ~120 lines.
- **Constraint:** the handshake-lock/turn-lock ordering, the fail-fast
  `_rollback_ephemeral_on_busy` gate, and the `finally` teardown's
  "don't stop a proc a live winner is using" guard (`turns.py:751`) are
  load-bearing. Mechanical refactor, must keep the concurrency tests green.

### AC8 — Cluster of LOW, no-drift internal duplication · LOW (opportunistic only)
Recorded so a future pass doesn't re-discover them; **none has dangerous drift**,
so fold in only when the file is open:

- `manager/interactions.py`: `approve_interaction` (`:412–453`) ≈
  `reject_interaction` (`:455–498`) are mirror images; the `{"outcome":{"outcome":
  "selected","optionId":…}}` envelope is hand-built 5× (`:277,297,314,443,488`).
- `manager/helpers.py`: `_select_approve_option` (`:218`) ≈ `_select_reject_option`
  (`:131`) — same 3-pass scanner, differing only in target literals; the `_opt_id`
  closure is defined 3×.
- `client.py`: `send_prompt` (`:519`) re-implements the send half of
  `send_prompt_nonblocking` (`:622`) + `finish_prompt` (`:679`).
- `models.py`: `InteractionPolicy.mode` validated 3× (`:206`, `:367`, `_parse…:274`);
  `max_recoverable_restarts` bound checked 2× (`:347`, `from_dict:466`).
- **Fix:** parameterized helpers (`_select_option(...)`, `_selected(option_id)`,
  route `from_dict` through `__post_init__`). Opportunistic; do **not** manufacture
  shared API solely to delete these.
- **Status: PARTIALLY IMPLEMENTED** — the no-drift wins landed (`_select_option` +
  `_opt_id` in `helpers.py`; `_selected_outcome` + `_resolve_and_respond` in
  `interactions.py`). The `models.py` validation dedup and the `client.py`
  `send_prompt` overlap are **skipped with cause** (context-specific error messages;
  concurrency-sensitive path). See the Implementation status section below.

---

## Verified non-findings (checked and cleared — do not "fix")

- **N1 — child env IS scrubbed (security-positive).** `process.py:124`
  `build_child_env(self.config.env)` drops `HARNESS_ENV_KEYS` (provider creds)
  before spawning; the sole `Popen` (`:127`) uses it. A third-party ACP server
  does **not** inherit `OPENAI_API_KEY` etc. Explicitly documented (`:119–124`,
  "same trust position as an MCP server").
- **N2 — no SSRF surface.** `AcpServerConfig` accepts only `command/args/env/cwd`;
  grep for `url|https?|headers|authorization|bearer|sse|streamable|websocket`
  across `acp_client/` → **no transport match.** stdio-only; `url_policy.py` is
  inapplicable (watch-item only if a URL transport is ever added).
- **N3 — secrets are redacted in `agentao.log`.** The `agentao.acp_client` logger
  is a child of `agentao`, whose file handler carries `_RedactingFormatter`; debug
  param dumps and child stderr are redacted. Config never logs env *values*
  (errors use `type(...).__name__`). No hand-rolled redaction (correctly — grep
  `secret_scan|redact` → no match; it isn't needed here).
- **N4 — the ephemeral open/rollback/busy race is well-defended.** Traced the
  two-prompt interleavings: the handshake lock serializes open/connect/rollback;
  `has_ephemeral` fail-fasts a competing `prompt_once`; `_rollback_ephemeral_on_busy`
  guards teardown with `name not in self._clients and not process_was_running`
  (`turns.py:751`) so a proc a live winner uses is never torn down. No TOCTOU.
- **N5 — concurrent `_send` cannot corrupt NDJSON framing.** Each frame is a single
  `stdin.write(line + "\n")` on a `BufferedWriter` whose `write()` is atomic under
  its internal lock; concurrent cancel/response/notify interleave whole lines,
  never bytes.
- **N6 — no turn-slot / proc leak on the normal error paths.** `_active_turns` is
  cleared in `finally` on every path; failed connect / bad handshake calls
  `handle.stop()` only when *this* call started the proc (`_we_started` gate),
  so a pre-warmed server survives a bad handshake. `prompt_once`'s ephemeral is
  popped+closed in `finally` (`turns.py:683–712`).
- **N7 — boundary is clean.** No core module eagerly imports `acp_client`; the only
  non-package references are two docstring mentions and **lazy, function-local**
  imports in CLI entrypoints (`cli/acp_inbox.py`, `cli/commands_ext/acp.py`).
  Render/display lives inside `acp_client/` (mild layering smell) but is
  self-contained — `client.py`/`process.py`/`manager/*` never import `render`, so
  core doesn't pull display in.
- **N8 — `config.py` does NOT copy the lossy read-json-swallow idiom** the June
  review flagged repo-wide; it **raises** `AcpConfigError` with precise messages on
  both `OSError` and `JSONDecodeError`. Stricter by design.
- **N9 — the manager mixin split is deliberate, not a god-class.** The
  lifecycle/connection/turns/interactions/status/recovery split is documented in
  the module + `__init__` docstrings; shared `self.*` state is the documented
  contract.

---

## Recommended sequence

> **This is the original proposal.** For what actually landed (and where AC4
> deviated), see **Implementation status** below — it supersedes this ordering.

1. **PR 1 — do-now, low risk.** AC1 (`stop_all` snapshot — one line) + AC4 (delete
   the three dead surfaces). No behavior change beyond removing a crash and dead
   code.
2. **PR 2 — small correctness.** AC2 (lock the recovery-counter write, or split the
   read-only probe — maintainer's call). Single, tightly-scoped.
3. **Recorded, opt-in.** AC3 (spawn+kill-tree change *or* accept the documented
   limitation) — a deliberate decision, not scheduled work.
4. **Tier 3 — opt-in, higher value/risk.** AC6 (the sticky-fatal dance is the one
   consolidation worth doing on its own — a correctness-adjacent invariant), then
   AC7 (`prompt_once`, which the AC6 helper de-risks). AC5 hardening and the AC8
   cluster fold in opportunistically when those files are next touched.

The AC3 spawn-model change and the AC5 output-hardening posture are
harness-vs-product judgment calls that belong to the maintainer.

---

## Appendix — how each finding was verified

| Finding | Verification |
|---|---|
| AC1 | read `lifecycle.py:73–97` (live `.items()` at `:75` vs `list(...)` at `:82`); traced `_clients.pop` sites — `recovery.py:376`, `lifecycle.py:114/171` — reachable lock-free from `status.py` |
| AC2 | read `recovery.py:296–407`; `_clear_fatal:300` holds `_recovery_lock` for the identical `= 0` write, `:389` does not; grep of all `_handshake_fail_streak` writes confirmed the asymmetry |
| AC3 | read `process.py:100–260`: `Popen` at `:127` has no `start_new_session`; escalation `terminate:234`/`kill:240`; documented limitation comment `:209–216`; compared to `capabilities/process.py::kill_process_tree` |
| AC4 | grep `deadline_at` (no assignment outside `:56` default), `_open_ephemeral_client\b` (only comments + the def), `_note_handshake_failure\b` (no production caller) |
| AC5 | read `render.py` end-to-end: plain `sys.stdout.write` `:117`, `escape` handles markup not ANSI `:185/195/197`; `helpers.py` agent chunks untruncated |
| AC6 | grep `_reclassify_as_handshake_fail`/`_note_handshake_success` → 5 call-site clusters (`connection.py:170/263/400`, `turns.py:662/840`); matching `create_session`/`initialize` sites |
| AC7 | `awk` method-span → `prompt_once` `452–712` (261 lines), next def `_rollback_ephemeral_on_busy` at `:714` |
| N1–N9 | `build_child_env` read; grep for url/SSRF transport (none); logger parentage + `_RedactingFormatter`; traced ephemeral race interleavings; NDJSON atomic-write reasoning; `_we_started` gate; `import.*acp_client` across `agentao/` |

---

## Implementation status (2026-07-23 → 2026-07-24)

**Landed: AC1 (crash fix), AC2 (lock fix), AC3 (process-tree kill), AC4′
(test-correctness fix), AC6 (handshake-dance consolidation), AC7 (`prompt_once`
decomposition).** AC4's "dead code" was re-scoped and deliberately NOT deleted —
the direct read contradicted the "dead code, delete it" framing, so the honest
call was to keep the code and record why (same conservative reflex as the June
review's T1.4 revert — don't end a contract early just because a surface looks
dead). AC5 and AC8 remain recorded (opportunistic).

- **AC1 — implemented.** `lifecycle.py::stop_all` now iterates
  `list(self._clients.items())` (a snapshot), matching the `_ephemeral_clients`
  block directly below it. Regression test added:
  `tests/test_acp_client_process.py::TestACPManager::test_stop_all_survives_client_removed_mid_iteration`
  — a fake client whose `close()` pops a sibling deterministically reproduces the
  mid-iteration mutation. **Proven to have teeth:** reverting the one-line fix
  makes the test fail with exactly `RuntimeError: dictionary changed size during
  iteration` at `lifecycle.py:80`; with the fix, the full acp_client + headless
  suites are green (194 passed). An `xhigh` workflow code review then flagged that
  the test's explicit `mgr._clients == {}` assertion is guaranteed by `stop_all`'s
  unconditional `clear()` — so a `client_a.closed and client_b.closed` assertion
  was added to prove each snapshotted client is actually drained (not merely
  dropped). (The reviewer's other candidate — that the two close-loops are now
  copy-paste — was refuted by the verify pass: they clear different dicts.)

- **AC2 — implemented.** `recovery.py::_check_cached_client_alive` now wraps the
  streak-reset write (`self._handshake_fail_streak[name] = 0`) in
  `with self._recovery_lock:`, matching every other streak mutation
  (`_clear_fatal` / `_note_handshake_success` / `_note_handshake_failure_and_maybe_fatal`).
  `_mark_fatal` and `_note_recovery_attempt` were confirmed already locked, so
  `:389` was the sole unlocked recovery-dict write. The narrow-lock option was
  chosen over the read-only-probe split (the smaller change). Green: embedding +
  headless suites (117 passed).

- **AC3 — implemented.** `process.py::ACPProcessHandle.start` now spawns with
  `start_new_session=True` (POSIX) / `CREATE_NEW_PROCESS_GROUP` (Windows), and the
  force-stop escalation's final SIGKILL routes through
  `capabilities/process.py::kill_process_tree` instead of `self._proc.kill()`, so an
  unresponsive server's MCP/shell grandchildren are reaped rather than orphaned. The
  graceful stdin-EOF path and the child-scoped SIGTERM middle stage are unchanged
  (the server still reaps its own grandchildren on a clean shutdown); only the last
  resort became tree-scoped. Both halves shipped together because `killpg(proc.pid)`
  is only safe once the child leads its own group. The two escalation waits are now
  module constants (`_TERMINATE_STOP_TIMEOUT` / `_KILL_STOP_TIMEOUT`) so tests can
  speed them up. Two new tests: a fast POSIX check that the child is its own
  process-group leader, and an integration test that a signal-ignoring server's
  grandchild is reaped — **proven to have teeth** (reverting to `self._proc.kill()`
  makes the grandchild survive and the test fail). A follow-up review pass found the
  first cut only reaped the tree when the server *survived* SIGTERM; a server that
  *dies* on SIGTERM without reaping its own children would still orphan them. Fixed
  by making the whole-tree sweep unconditional after the SIGTERM stage (a no-op when
  the group is already empty), with a second teeth test for the die-on-SIGTERM path.
  Two **accepted trade-offs** the review surfaced, both requiring changes outside
  this subpackage so left as recorded follow-ups: (1) `start_new_session` detaches
  the server from agentao's controlling terminal, so a terminal-close `SIGHUP` no
  longer reaps it — robust cleanup on agentao's *own* abnormal termination needs a
  CLI-layer `SIGHUP`/`SIGTERM` handler calling `stop_all()` (the interactive CLI only
  cleans up via a `finally`); (2) on Windows the force-kill now shells out to
  `taskkill /F /T` (≤5 s) instead of an instant `proc.kill()`, so the handle lock is
  held marginally longer on the already-degenerate force path. Both are inherent to
  correct process-group tree-reaping.

- **AC4′ — implemented.** `test_acp_client_embedding.py` (the sessionless-cached
  re-session test) now spies on `_open_ephemeral_client_locked` — the method
  `prompt_once` actually calls — instead of the never-called `_open_ephemeral_client`
  wrapper, so `called["ephemeral"] == 0` now genuinely asserts "no ephemeral was
  created on the cached-reuse path" rather than being vacuously true. Still green.

- **AC4 — re-scoped, not deleted.** Reading each of the three surfaces against its
  callers showed none is pure rot:
  - `interaction.py deadline_at` / `expire_overdue` — a **designed-but-unwired
    feature** (interaction deadline → default action), with a full docstring and an
    isolation test (`test_acp_client_cli.py::test_expire_overdue`). Wiring-vs-remove
    is a **product decision for the maintainer**, not a cleanup — left in place.
  - `recovery.py:166 _note_handshake_failure` — functions as a clean **test seam**:
    `test_headless_runtime.py:1207,1247` use it to simulate "streak = 1" without
    reaching into the private `_handshake_fail_streak` dict. Deleting it would push
    those tests to poke internal state directly — a downgrade. Left in place.
  - `turns.py:760 _open_ephemeral_client` (redundant lock-acquiring wrapper) — the
    real defect here is **not** the dead wrapper but a **vacuous test assertion**:
    `test_acp_client_embedding.py:945–953` spies on `_open_ephemeral_client` and
    asserts it "MUST NOT fire", but production takes the `_locked` path, so the
    assertion is trivially true and guards nothing. **New sub-finding (AC4′):**
    retarget that spy to `_open_ephemeral_client_locked` so the test actually
    asserts "no ephemeral client is created on the cached path." That is a
    test-correctness fix (behavior-meaningful), separate from any code deletion.
    **Implemented** (see the AC4′ bullet above).

- **AC6 — implemented.** A `RecoveryMixin._handshake_guarded(name, *, on_failure=None)`
  context manager now owns the sticky-fatal dance (reclassify → maybe-fatal → raise
  on failure; note-success on success). All **5** copies route through it:
  `connection.py` ×3 (`_connect_server_locked` cached-reuse + greenfield,
  `ensure_connected` re-session) and `turns.py` ×2 (`prompt_once` cached re-session,
  `_open_ephemeral_client_locked` greenfield). Sites 2 and 5 keep their site-specific
  teardown via the `on_failure` hook (close the half-built client, stop a
  self-started proc). **Order subtlety (caught by the xhigh code review):** the two
  greenfield sites ran accounting and teardown in *opposite* orders, and both
  `_mark_fatal` (→ handle state `FAILED`) and `handle.stop()` (→ `STOPPED`) write
  `handle.info.state`, so the order decides the terminal state a host sees via
  `get_status()`. The CM therefore takes a `cleanup_before_accounting` flag; each
  site passes the value that reproduces its own prior order (site 2 → `FAILED`, site
  5 → `STOPPED`), keeping the consolidation truly behaviour-preserving. A 6th entry
  point can no longer forget the accounting. Green: full acp_client + headless suites.

- **AC7 — implemented.** `prompt_once` dropped from **261 → 148 lines** (the code
  body ~95; the rest is its docstring) by extracting three cohesive helpers:
  `_setup_ephemeral_or_defer` (handshake-locked ephemeral setup / defer →
  `(client, ephemeral_created, process_was_running)`),
  `_reuse_cached_client_for_prompt_once` (the cached-reuse/re-session branch,
  flattened from depth-8 nesting to early returns), and
  `_teardown_ephemeral_after_prompt` (the `finally` teardown). The load-bearing
  invariants held: the turn lock's unconditional `lock.release()` stays in
  `prompt_once`'s `finally`; the handshake-lock/turn-lock ordering, the fail-fast
  `_rollback_ephemeral_on_busy` gate, and the "don't stop a proc a live winner is
  using" guard are all preserved (moved verbatim into the helpers). Green: 252
  acp_client + headless tests.

- **AC5 — implemented (output hardening), one sub-part deferred.** Added
  `render._sanitize_terminal_text` (drops C0 incl. ESC, DEL, and C1 controls;
  keeps `\n`/`\t`) and wired it into **both** display paths: the plain
  `render_plain` fallback (the verbatim-`sys.stdout.write` vector) and the Rich
  path (agent-Markdown accumulation + the prefixed-line branch). Stripping the ESC
  byte alone neutralizes every CSI/OSC sequence — the standard, robust approach —
  so the residual `[2J`-style text is inert; we do **not** fragile-parse whole
  sequences. Added a per-chunk cap `helpers._cap_chunk` (`_MAX_CHUNK_DISPLAY_CHARS
  = 256 KiB`) on `agent_message_chunk` / `agent_thought_chunk` — the only kinds
  that were returned untruncated — so a compromised server can't force multi-GB
  buffering in the inbox + Markdown accumulator. The cap is far above any real
  streaming delta, so it never touches legitimate content. +9 teeth tests
  (`test_acp_client_inbox.py::TestTerminalSanitization`/`TestChunkCap`), incl.
  Rich-path OSC-set-title/BEL removal. **Deferred (documented):** the
  readline-level hard cap in `process.py` — the multi-GB buffering happens inside
  the C-level `for raw_line in stdout` before any Python check can see the line, so
  a real cap there needs a bounded-frame NDJSON reader with a *drop-oversized-frame*
  policy (a mid-line truncation would corrupt a legitimately-large valid JSON-RPC
  frame). That is a framing rewrite on the hottest client path; given the child is
  already env-scrubbed and the display buffer is now bounded by `_cap_chunk`, it is
  left as a recorded follow-up rather than shipped under the "opportunistic" label.

- **AC8 — partially implemented (the no-drift wins), two items skipped with cause.**
  Done: (a) `helpers.py` — extracted a module-level `_opt_id` and a parameterized
  `_select_option(options, *, canonical_kind, kind_prefix, hints)`; the three
  copies (`_select_reject_option`, `_select_approve_option`, and the inlined
  `_opt_id` in `_select_option_by_kind`) now route through it. The public
  `_select_approve_option` / `_select_reject_option` / `_select_option_by_kind`
  names (exported in `manager/__init__.__all__`, imported by
  `test_acp_client_embedding.py`) are kept as thin wrappers — behavior-preserving.
  (b) `interactions.py` — the `{"outcome":{"outcome":"selected","optionId":…}}`
  envelope (hand-built 5×) is now `_selected_outcome(option_id)`, and the identical
  resolve → send-response → transition-off-`WAITING_FOR_USER` tail of
  `approve`/`reject`/`reply_interaction` is now `_resolve_and_respond(...)`.
  **Skipped, with cause:** (c) `models.py`'s 3× mode / 2× restart-bound validation
  is intentional context-specific messaging — `from_dict` / `_parse_non_interactive_policy`
  emit *JSON field-name* errors (`'maxRecoverableRestarts'`, migration hints) at
  config-load, while `__post_init__` emits *Python attribute* errors at dataclass
  construction; these strings are user-facing UX (and some are asserted in tests),
  so collapsing them would degrade the message, not just dedup. This is exactly the
  "no dangerous drift → leave it" case. (d) `client.py`'s `send_prompt` vs
  `send_prompt_nonblocking` + `finish_prompt` overlap sits on the JSON-RPC
  send/response-correlation path that the concurrency tests guard; it is the
  riskiest of the four for the lowest (LOW) payoff, so per the audit's own
  "do not manufacture shared API solely to delete these" guidance it is left
  recorded. Existing coverage (`TestSelectRejectOption`, the
  `result["outcome"]["optionId"]` envelope assertions in `test_acp_client_cli.py`,
  and the `accept_all` auto-reject tests in `test_headless_runtime.py`) validates
  the consolidations as behavior-preserving.

- **AC5/AC8 xhigh code-review round (post-implementation)** — a second workflow
  review of the AC5/AC8 diff found **9 distinct defects, all fixed** (the AC1/2/3/6/7
  diff was separately reviewed clean). The material ones were mine:
  1. **Sanitizer missed the interaction display path** (highest severity). `flush_to_console`
     skips PERMISSION/INPUT and delegates to `_handle_inline_interaction`
     (`cli/commands_ext/acp.py`), which `console.print`s the server's `toolCall.title`
     / `rawInput` / `content` / `interaction.prompt` **unsanitized** — the exact
     interaction channel AC5 targets. Fixed by sanitizing every server-controlled
     field at that second display boundary (+3 tests driving ESC/OSC through a fake
     console).
  2. **`_cap_chunk` TypeError regression** — `len()`/slice assumed `str`; a JSON-number
     `content.text` from a hostile server raised `TypeError` (swallowed by the client's
     notification try/except → the message *and* the host callback silently dropped),
     where the pre-diff code returned the value. Fixed with an `isinstance(text, str)`
     passthrough guard.
  3. **Sanitizer left Unicode bidi-override controls** (Trojan-Source / CVE-2021-42574):
     U+202A–202E / U+2066–2069 / U+200E/200F/061C reorder text with no ESC byte. Added
     `_BIDI_CONTROLS` to the strip set; deliberately keeps ZWJ/ZWNJ/BOM (legit in
     emoji / Arabic-Indic).
  4. **Cap over-claimed memory bounding** — `_cap_chunk` bounds only the *display*
     string; `InboxMessage.raw = params` still retains the full payload. Corrected the
     comment to scope the claim (display path only) and point the true payload / process-
     read bound at the deferred readline-frame cap.
  5. **Cap skipped sibling server text** — permission `toolCall.title` and `ask_user`
     `question`/`message` reached `InboxMessage.text` uncapped. Now routed through
     `_cap_chunk`.
  6. **Cross-chunk accumulation unbounded** — per-chunk cap didn't bound the
     `agent_text_parts` join handed to RichMarkdown (~256×256 KiB per flush). Added
     `render._MAX_AGENT_RENDER_CHARS = 1 MiB` on the aggregate.
  7. **Non-ASCII ellipsis** in the truncation marker (`…`) could `UnicodeEncodeError`
     under a non-UTF-8 stdout in the plain path. Switched to ASCII `...`.
  8. **Debug log-only branch** logged server `msg.text[:200]` unsanitized (a DEBUG
     stream handler would echo escapes). Now sanitized before logging.
  9. **`_select_option_by_kind` duplicated pass 1** of `_select_option`. Extracted
     `_first_id_by_kind` shared by both. Full suite green after fixes.

**Net:** three real defects addressed (AC1 crash, AC2 lock, AC3 orphaned
grandchildren) with teeth-proven / behavior-preserving coverage; two Tier-3
refactors landed behind their concurrency tests (AC6 handshake-dance consolidation,
AC7 `prompt_once` decomposition); AC5 output-hardening shipped (escape sanitization
+ chunk cap; the readline-level cap deferred as a framing rewrite); AC8's no-drift
consolidations landed (`_select_option`, `_selected_outcome`, `_resolve_and_respond`)
with the two message-/concurrency-sensitive items left recorded. The AC4 "dead code"
bucket stays downgraded to one implemented test-correctness item (AC4′) plus one
maintainer product decision.
