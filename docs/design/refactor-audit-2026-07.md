# Agentao — Refactor Audit & Reverse Review (2026-07)

**Status:** Review record. Drafted 2026-07-24/25 from a full-tree refactor audit
(58.8k LOC / 268 modules) followed by an **adversarial reverse review of the
audit's own findings**. Three items shipped; five were declined *after* the
reverse review killed or downgraded their premise. **The declined list is the
substantive half of this document** — it exists so the same proposals are not
re-raised without new evidence.
**Audience:** Agentao maintainers.
**Companion:** `refactor-audit-2026-07.zh.md`.
**Related:**
- `optimization-opportunities-review.md` — the 2026-06-19 audit. Its Tier 1–2
  shipped in v0.4.12; its **Tier 3 is formally declined here**, with churn
  evidence the June pass did not collect.
- `core-boundary-review.md` — the render/display-in-core boundary that T3.2's
  "move `get_conversation_summary` out of core" argument leaned on.
- `embedded-host-contract.md` — why the v1.2 replay kinds exist at all.

---

## TL;DR

Seven candidate items. **Two shipped as proposed, one shipped after being
narrowed, four declined.** The reverse review changed the verdict on more items
than the original audit got right, which is the point of running one.

| # | Item | Verdict |
|---|---|---|
| 1 | `/copy` unbounded `subprocess.run` | **Shipped** (#139) |
| 2 | `run_loop` if/elif chain → dispatch table | **Shipped** (#140) |
| 3 | Replay v1.2 audit kinds unrendered | **Shipped, scope corrected** (8 kinds → 3) |
| 4 | 4× ruff F821 | **Shipped, value corrected** — static hygiene only |
| 5 | June Tier 3 (`_execute_one`, overflow recovery, `get_conversation_summary`) | **Declined** — no churn evidence |
| 6 | mypy per-package ratchet | **Declined** — 27/89 are mixin false positives |
| 7 | Lazy-compile hardline regexes | **Declined** — 17ms of a 130ms start |

---

## Shipped

### 1 — `/copy` could hang the CLI forever (#139)

`_copy_last_response` called `subprocess.run` three times with no `timeout=`.
A wedged `pbcopy` (unresponsive pasteboard server) blocked the input loop with
no exit but SIGINT, and the bare `subprocess.run` violated the `run_captured`
rule in `CLAUDE.md` (kill the whole process tree, not just the direct child).

Replaced with a flat candidate loop: every attempt bounded at 5s via
`run_captured`; a timeout **or** non-zero exit now falls through to the next
utility instead of aborting the chain (previously a failing `pbcopy` reported
"Copy failed" without ever trying `xclip`/`xsel`).

### 2 — `run_loop` dispatch table (#140)

353 LOC / **cyclomatic complexity 74** — nearly double the next-worst function
in the tree — and driven by no test. 24 of 31 branches were already pure
delegation. Result: **127 LOC / cx 26**, dispatch itself 13 lines.

Extracted `commands/skills.py`, `commands/reset.py` (`/clear` + `/new` now share
one `_reset_session(clear_memories=…)`), and `/mode` → `commands/permission.py`.
`/exit` / `/quit` stay inline — loop control flow a table cannot express — with
a test pinning them *out* of the table.

**Drift the table surfaced:** `/sandbox` was dispatchable but absent from
tab-completion, so it was undiscoverable from the prompt despite being
documented in `CLAUDE.md`. Making the vocabulary a *value* is what allowed a
test to compare it against `_utils._SLASH_COMMANDS` and `help_text`.

### 3 — v1.2 audit kinds recorded but not rendered

`tool_lifecycle` / `subagent_lifecycle` / `permission_decision` exist so an
embedded host has **one** audit artifact instead of two parallel streams
(`EventKind` docstring, v1.2). The JSONL side was correct; both CLI views were
not:

- `--raw` → degraded to a sorted payload-key preview.
- **Default grouped view → dropped them entirely.** `_print_turn`'s event loop
  is an *allowlist*; an unnamed kind is silently skipped. A turn containing a
  denied permission decision and a failed tool rendered as `user / asst / ok`.

Both now render, plus `tests/test_replay_render_coverage.py`: a probe-based
exhaustiveness guard that fails when a new `EventKind` gains no summary.

> **Scope correction.** The audit first claimed *8* uncovered kinds. Four
> (`session_ended`, `session_forked`, `session_loaded`, `session_saved`) have
> **no emission point anywhere in the tree** — `session_saved` is marked
> "reserved; not emitted in v1" in `EventKind`'s own docstring, and the one
> `session_ended` hit is a *docstring example* in `recorder.py`. A renderer for
> them would be dead code. `turn_started` is emitted but structural (consumed by
> `_group_events_into_turns`). The real number was **3**.

### 4 — 4× ruff F821, narrowed to static hygiene

`agents/manager.py` and `skills/manager.py` annotate plugin-subsystem types in
string form while importing them only *inside* the function body (deliberate —
the plugin subsystem is an optional dependency). Nothing binds those names at
module scope, so no static checker can resolve the annotations.

Fixed by declaring them in `TYPE_CHECKING` blocks. Verified this adds **no
runtime import** (`agentao.plugins.models` stays out of `sys.modules`).

> **Value correction.** The audit implied this restored runtime introspection.
> It does not — verified: `typing.get_type_hints()` still raises `NameError`,
> because it resolves against runtime `__globals__` where `TYPE_CHECKING`
> imports do not exist. Nothing in the tree calls `get_type_hints` on these
> methods; the schema generators operate on `agentao.host` Pydantic models, and
> the one introspecting test (`test_async_tool.py`) targets a different method
> and already supplies a `localns` workaround. **This is future-proofing for a
> widened mypy gate, not a live defect.**

---

## Declined after reverse review

### 5 — June Tier 3: the three long functions

`optimization-opportunities-review.md` recorded three functions as costly to
maintain and left them unbuilt. They are **formally declined**, not deferred.

The premise was maintenance cost. Churn over 12 months does not support it:

| File | Commits | of which `fix:` |
|---|---|---|
| `runtime/tool_executor.py` (T3.1, `_execute_one` 249 LOC) | 7 | 2 |
| `runtime/chat_loop/_runner.py` (T3.3, overflow recovery) | 12 | 3 |

A 249-line function nobody edits is not costing anything. Length alone is not a
refactor trigger — the same standard that keeps `agent.py`'s 231-line `__init__`
off the list (cx **3**, pure sequential wiring).

**T3.2 fails on a second, independent ground.** The proposal was to move
`Agentao.get_conversation_summary` out of core as presentation logic. It has a
real caller (`cli/ui.py:75`) *and* appears as a host-provided method in a test
stub (`test_cli_host_events.py:133`) — it is part of an existing interface.
Moving it is a breaking change for embedded hosts in exchange for zero
functional gain. Reopen only with a host actually blocked by it.

### 6 — mypy per-package ratchet

The audit recommended extending the CI typing gate package by package, calling
`agentao.replay` (16 errors) the natural entry point. Inspection killed it:

- `agentao.replay` — 15 of 16 are `type-arg` / `no-any-return` cosmetics. One
  real annotation defect (`recorder.py:113`, missing `Optional[TextIO]`).
- `agentao.runtime` — **27 of 89 are mixin false positives** (`_CompactionMixin`
  accessing `self._agent`, provided by the concrete class). Silencing them means
  writing Protocol scaffolding to satisfy the checker, not fixing anything. The
  rest: 26 `type-arg`, 24 missing annotations.

Poor return on effort. `mypy --strict` stays scoped to `agentao.host` — the
stability boundary, which is where it earns its keep. The single
`recorder.py:113` defect can be fixed on its own whenever that file is touched.

### 7 — Broad linter adoption

`uvx ruff check agentao` reports ~2800 findings, but the number is misleading:
926 are `List` → `list` modernization, 833 `Optional[X]` → `X | None`, and 224
are the `except Exception` handlers that are a deliberate design posture. The
high-signal subset (`--select F`) is 92, of which only the 4 F821 had any
argument behind them — and see the value correction above.

**No linter is being adopted and no CI gate added.** Should that change, start
at `--select F` and expect ~88 findings of pure cleanup value.

### 8 — Lazy-compile the hardline regex table

`permissions_hardline/_patterns.py` compiles 23 regexes at import: 17ms of the
88ms `import agentao.agent`, ~13% of a 130ms CLI cold start. It is the single
most expensive module in the import graph, which is what made it look like a
finding. It is still 17ms, one-time, and for an embedded host it is paid once at
module import. Demand-gated.

### 9 — Renderers for the 5 remaining unrendered `EventKind`s

Four have no emission point; one (`turn_started`) is structural. Documented in
`_NO_SUMMARY_EXPECTED` in `tests/test_replay_render_coverage.py` with the reason
per entry, and a companion test fails if one of them *gains* a renderer (so the
exemption list cannot go stale silently). Adding renderers now would be dead
code.

---

## Post-landing code review (xhigh, 2026-07-25)

An adversarial multi-agent review of all three branches returned **14
confirmed defects**, all of them in work this document had already described
as shipped. The three that matter most:

- **The v1.2 render did not actually work in production.** The audit events
  carry the *runtime's* `turn_id` (a uuid4 from `runtime/identity.py`), while
  `ReplayAdapter` mints a short id per replay turn. The envelope forwarded the
  former into a file grouped by the latter, so `/replay show` rendered them as
  extra phantom turns — and `SubagentLifecycleEvent`, which has no `turn_id`
  field at all, fell out of every turn. **The PR's own test passed because it
  hand-wrote `turn_id: "t1"` on all six events, a shape the recorder cannot
  produce.** Fixed by giving `HostReplaySink` a `turn_id_provider`: the
  envelope is now the replay id space, the payload keeps the host's.
- **A test soft-deleted the developer's real `~/.agentao/memory.db`.** Routing
  `_clear_reset` through the real handler was correct, but `MemoryManager.clear()`
  at `scope=None` clears the *user* store, which resolves to `user_root()`
  regardless of the fixture's `working_directory`. Every `pytest tests/` run
  destroyed cross-project user memories, silently, all green. Fixed by
  redirecting `HOME` in the fixture, with a test asserting the redirect took.
- **`/copy` regressed by adopting `run_captured`.** Its pipes mean
  `communicate()` waits for *every descendant* to close the write ends; `xclip`
  forks a background selection owner that never does. Measured: 5.01s timeout
  vs 0.05s. Replaced with a local runner that keeps the process-group and
  tree-kill properties but sends stderr to a temp file.

Two lessons, both about the same failure mode — **a test written from the same
mental model as the code cannot falsify it**:

1. The v1.2 fixture was built from my reading of `host/models.py`, not from a
   recorder. It encoded the same wrong assumption as the renderer.
2. All nine `/copy` tests stubbed `run_captured`, so the one property the
   refactor actually changed — pipe/EOF semantics — was invisible. The
   replacement spawns real forking children.

Also corrected: `session_ended` was exempted in the new exhaustiveness guard
as "never emitted" when `ReplayManager.end()` writes it to every completed
replay file — the guard certified the exact defect it exists to catch. The
exemption list now has a test that greps for emission sites rather than
trusting the comment.

---

## Verified non-findings

Checked against source and cleared — do not "fix" these:

- **`cli/commands` vs `cli/commands_ext`** — intentional, documented in
  `commands_ext/__init__.py` ("heavier dependencies"), not legacy drift.
- **Cross-file duplication** — an 8-line-window token-normalized detector over
  the tree returns only interface-signature repetition (`transport/*.ask_user`,
  unavoidable and clearer explicit) and small response envelopes that already
  share helpers (`session_new` / `session_load` import `_session_modes`). No
  extractable duplication of consequence.
- **`agent.py` at 1299 lines** — already decomposed into `_init_*` helpers; the
  231-line `__init__` has cyclomatic complexity 3.
- **`acp_client/process.py:236` bare `Popen`** — a long-running server child, so
  correctly *not* routed through `run_captured`.

---

## Method notes

**Make an implicit vocabulary explicit and drift falls out.** The `/sandbox`
completion gap and the v1.2 render gap are the same bug shape: a set of names
maintained in several places with nothing comparing them. Both were found by
turning the set into a value and writing one test over it. Both guards were
verified to fail when their fix is reverted — an exhaustiveness test that has
never been seen red is not evidence of anything.

**Run the reverse review before the work, not after.** Of seven items, the
reverse pass declined four and corrected the scope or the stated value of two
more. The audit's raw output was closer to wrong than right; churn data,
emission-point greps, and one empirical render were what separated them.

**Length is not a defect.** Three of the four declined items were justified by
size or count (249 LOC, 89 errors, 2800 findings). None of those numbers
predicted actual cost.
