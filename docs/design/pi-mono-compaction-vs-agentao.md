# pi-mono compaction vs Agentao compaction

> **⚠️ Analysis only. Nothing here is authorized for implementation.** The ranking in §1 is a
> **priority ordering of findings**, not a work schedule. Quote this line whenever you quote the
> table — it is what stops the next reader from reading the ordering as a sprint plan.


> **⚠️ Threshold changed (2026-08-23, after this doc's anchor):** `COMPRESSION_THRESHOLD` was raised
> from **0.65 to 0.80** (`agentao/context_manager.py:69`), which widens the cheap band from
> `(55 %, 65 %]` to `(55 %, 80 %]`. Every "65 %" in the body is preserved as of the anchor —
> **do not** read it as the current value.

**Status:** analysis, rev 6 (2026-08-23). No implementation authorized.
**rev 3 after a maintainer review — 14 corrections, all upheld against source. rev 4 after a second
review: those corrections are now *folded into* the body, the tables and §13, and the superseded
verdicts are deleted rather than annotated.** §14 is therefore a **historical record**, not a live
errata layer — the body is authoritative on its own, and no section should need §14 to be read
correctly. **rev 5 (third review)** tightened three quantifiers that were still absolute: the cheap
tier is *not* guaranteed reachable on a lumpy jump (the band is half-open, `(55 %, 65 %]`), codex's silent-overflow
blind spot is *threshold-dependent* rather than total, and pi-mono's session persistence is the
**default**, not unconditional (`--no-session` is in-memory). **rev 6 (fourth review)** repairs two
consistency slips that rev 5's own tightening introduced: the band is **half-open**, `(55 %, 65 %]`,
not "exclusive" — and the citation for it pointed at `needs_compression` (`:256`) rather than
`needs_microcompaction` (`:278-279`); and §13 still described a lumpy jump as one where "the cheap
tier still fires". The headline change: rev 2's third P1 ("the context window does not follow
the model") is **demoted to P2 and re-scoped**. The window is a *documented host-owned knob*, not an
internal defect (§3.4); what survives is the absence of validation, warning, or reconciliation.
**Anchors:** pi-mono `a69bef789` (2026-08-23); agentao `main@a996395` (2026-08-23); codex
`openai/codex@2151d3a5b7` (2026-08-21, §3.3–3.4 only).
**Method:** all sides read from source; every claim carries an inline `file:line`. Where a claim is
reasoned rather than measured, it says so.
**rev 2 (three-way trigger merge):** the doc is a **two-way** pi-mono↔agentao comparison
*except* §3.3–3.4, which add codex as a third data point on the trigger axis alone. Do not read
codex into any other section; for the full codex compaction comparison see
`codex-compaction-vs-agentao.zh.md`.

> **Retracted in rev 3 — "one peer differing is a design choice, both peers differing the same way
> is a gap."** That inference is invalid as stated, and it is the reasoning that produced rev 2's
> third P1. Two peers converging shows a *popular* design; it cannot show that a third design is
> defective when that third design is a documented, deliberate ownership choice with its own
> configuration surface (§3.4). Peer convergence is evidence about the *option space*, not a verdict
> on a design that occupies a different point in it. Do not reuse the sentence.
**Related:** `codex-compaction-vs-agentao.zh.md` (the same question against a third peer — read its
§13 errata before reusing any of its conclusions), `pi-mono-borrow-review.md`, `path-a-roadmap.md`.
**Twin:** `pi-mono-compaction-vs-agentao.zh.md`.

---

## 1. Findings table (priority ordering, not a schedule)

**One-sentence difference: agentao compacts early and invests in the fidelity of what it feeds the
summarizer; pi-mono compacts late and invests in never destroying what it compacted away.**

| If implemented, priority | Finding | Section |
|---|---|---|
| **P1** | The `PreCompact` plugin hook is notify-only (`-> None`). An embedded host can observe compaction but cannot cancel it or substitute its own result. pi-mono's `session_before_compact` can do both. | §8 |
| **P1** | The summarizer-failure circuit breaker has **no reset path**: once open it short-circuits before any attempt, so it can never succeed, so it never resets. Compaction — **including manual `/compact`** — is dead for the life of that `ContextManager` instance. | §9 |
| **P2** *(was P1 in rev 2)* | **The context window is host-owned but unvalidated.** `max_context_tokens` is a documented host knob on **four** surfaces, so "it does not follow the model" is a design choice, not a defect. What is real: the CLI applies one `200_000` default to every model (`cli/app.py:278`) and `/model` performs **no validation, warning, or reconciliation**. A window configured *larger* than the model's real one degrades the two-tier design into the emergency ladder **under gradual growth**; a lumpy jump *may* land in the cheap-tier band instead, or overshoot it into planned full compaction, so the tier is not strictly unreachable. Recovery is not guaranteed either. | §3.4 |
| **P2** | `KEEP_RECENT_MESSAGES = 20` is a *message count*, not a token budget. 20 messages can be 500 tokens or 200 K. | §5 |
| **P2** | The previous summary is re-fed **as a block inside the newest-first allocator**, so it competes for eviction against live messages — the shape that produced the index-1 defect and required `carry_index` + `_clip_carry_summary` to patch. pi-mono has no such allocator: it appends the old summary after the transcript unconditionally, in a `<previous-summary>` tag, with a dedicated UPDATE prompt. (Both send one flat string; the difference is the eviction pool, not the wire format.) | §6.2 |
| **P3** | A cut landing mid-turn gives no *guarantee* the originating user request survives — it reaches the summarizer inside `messages[:split_index]`, but nothing reserves budget for it. pi-mono generates a separate turn-prefix summary at its own budget. | §5.2 |
| **P3** | Images are estimated at **0 tokens** — `_count_message_tokens` sums only `type == "text"` blocks. pi-mono charges 4 800 chars (~1 200 tok). | §4.2 |
| **Do not borrow** | pi-mono's `chars/4` estimator, its unbounded summarization input, its head-only tool-result truncation, its lack of a cheap tier | §10 |
| **Do not borrow** | codex's *separate* `ModelDownshift` / `CompHashChanged` trigger sites — agentao's per-iteration check picks up a corrected window on its own | §3.4 |
| **Observation** | Per-iteration vs per-turn checking: agentao is level with codex and **ahead of pi-mono**; no change indicated | §3.3 |
| **Observation** | Compaction is irreversible **and, by default, unlogged**: the session summary row stores no original messages, replay is off by default, and an ordinary session save persists the already-compressed list. rev 2's "nothing is lost for forensics" was wrong. | §7 |
| **Open, user's call** | The 65 % threshold itself. agentao is the most conservative of the three by 25–27 points, and the accuracy argument for it does not hold (§3.2). What *is* settled is that the ratios are not configurable while both peers' are | §3.2, §3.3 |

§11 lists items already checked and found at parity — do not re-report those.

---

## 2. Architectural placement: harness core vs host session layer

| | agentao | pi-mono |
|---|---|---|
| Core | `agentao/context_manager.py` (1287 L, one class) | `core/compaction/{compaction,utils,branch-summarization}.ts` (1541 L, pure functions) |
| Orchestration | `runtime/chat_loop/_compaction.py` (127 L); overflow ladder in `runtime/chat_loop/_runner.py:1117` | `core/agent-session.ts::_checkCompaction` (`:2050`), `::_runAutoCompaction` (`:2166`), `::compact` (`:1864`) |
| Layer | **harness core** — replaces `agent.messages` in place | **host session layer** — appends an entry to a session tree (persisted by default; in-memory under `--no-session`), then rebuilds context from it |
| Manual entry | `/compact` → `cli/commands/compact.py` (interactive CLI only) | `/compact` → `AgentSession.compact()`, also reachable from RPC and extensions |

That last-but-one row is the root of most of the rest. agentao's compaction *is* the mutation;
pi-mono's compaction is a *record of* a mutation that the context builder then honors.

Note the layering consequence for agentao's own stated identity: compaction sits inside the harness,
which is why an embedder gets no say in it (§8). pi-mono put it on the session/host side, which is
why an extension can replace it wholesale.

---

## 3. Trigger point and threshold

### 3.1 Where the check runs

- **agentao — every inner tool-loop iteration.** `_runner.py:365` computes one
  `_threshold_token_estimate` and feeds it to both `_maybe_microcompact` (`:366`) and
  `_maybe_full_compress` (`:369`), before each LLM call.
- **pi-mono — turn boundaries only.** `_checkCompaction(msg)` fires after `agent_end`
  (`agent-session.ts:1109`) and again before a prompt is submitted (`:1220`, with
  `skipAbortedCheck = false` so an aborted response still counts).

Consequence, in both directions: agentao can compact *between tool calls inside one turn*, which
matters when a single turn runs 50 tool calls; pi-mono structurally cannot, and depends on the
overflow path (§9.2) for that case. Conversely, agentao's per-iteration check rewrites the
already-sent prefix mid-turn, which invalidates the cached prefix from the rewrite point onward (or
degrades the hit rate) — a stable system-prompt prefix can still be reused, and how much is actually
lost is provider-dependent.

### 3.2 The threshold itself

| | agentao | pi-mono |
|---|---|---|
| Full compaction | `est > max_tokens × 0.65` (`context_manager.py:69`) | `contextTokens > contextWindow − reserveTokens` (`compaction.ts:235`) |
| Cheap tier | `0.55 – 0.65` band (`context_manager.py:70`) | none |
| `reserveTokens` default — **response headroom**, not verbatim retention | — | `16384` (`compaction.ts:132`, `settings-manager.ts:839`). The *verbatim* retention knob is `keepRecentTokens` = `20000` (§5.1) — do not conflate the two |
| Effective trigger, 200 K window | **65 %** | **≈ 91.8 %** |
| Configurable | only `max_context_tokens` (`agent.py:310`); the ratios are class constants | `compaction.{enabled, reserveTokens, keepRecentTokens}` in settings.json (`settings-manager.ts:826,839,843`) |

This is the largest single behavioral gap between the two, and it is a trade-off, not a bug on
either side. agentao buys headroom and a working cheap tier; it pays in summarization calls and
prompt-cache invalidation. pi-mono keeps ~27 % more of the window verbatim and preserves the cache
far longer; it pays by running with a thinner margin — `reserveTokens` = 16 384 exists precisely to
hold that margin.

**Not claimed: that pi-mono's overflow recovery is "routine".** rev 2 inferred a *frequency* from a
*threshold*, which source cannot establish — how often a session actually crosses the wall depends
on workload, and the reserve is the mechanism for keeping it from happening. The defensible statement
is a risk one: at ~92 % the margin absorbing an underestimate is 16 384 tokens, so a single large
tool result late in a window is more likely to reach the API than under agentao's 65 %. Whether it
does in practice is **unmeasured here**.

The one part that is not a trade-off is configurability: agentao's ratios are hard-coded class
attributes, so a host embedding agentao against a 32 K model and a host embedding it against a 1 M
model get the same 65 %/55 % split with no way to tune it.

**Note on what pi-mono counts (corrected in rev 3):** `calculateContextTokens`
(`compaction.ts:146`) is `usage.totalTokens || input + output + cacheRead + cacheWrite`. rev 2 said
the `output` term makes this "not a prompt size" because output is not part of the next request —
**that was wrong**: the assistant's visible output becomes history and *is* re-sent, so
`input(N) + output(N)` tracks the next request's prompt closely. The genuine over-count risk is
narrower: hidden reasoning tokens that are billed as output but not resent, and provider-specific
output accounting. Whether that matters is unmeasured here.

### 3.3 Three-way: codex as a third data point on the trigger axis

Scoped to this subsection and §3.4. codex anchor `2151d3a5b7`.

**codex's threshold** is `ModelInfo::auto_compact_token_limit()`
(`protocol/src/openai_models.rs:486`): `min(configured limit, resolved_context_window × 9 / 10)` —
**90 % of the window is a hard ceiling** and a configured limit can only lower it (the user's
`model_auto_compact_token_limit` is folded into `ModelInfo` first, `models-manager/src/model_info.rs:35`).
The check is `core/src/session/context_window.rs:77`:
`scope_tokens >= scope_limit + fallback_buffer || active_tokens >= full_context_window`, where the
scope defaults to `Total` (the whole active context, `protocol/src/config_types.rs:50`) and the
fallback buffer is 0 unless a `token_budget.auto_compact_fallback_prompt` is configured.

**codex's trigger sites.** The **five automatic** ones feed a single `run_auto_compact`
(`session/turn.rs:1178`), which *then* does the four-way implementation dispatch. **Manual
`/compact` does not**: `handlers::compact` spawns a standalone `CompactTask`
(`core/src/session/handlers.rs:244`) whose `run()` performs its **own** parallel four-way dispatch
(`core/src/tasks/compact.rs:29`). rev 2 said all six converge; corrected in rev 3. Either way the
same four implementations are reachable, so the "triggers are implementation-independent" conclusion
survives — it just travels two paths, not one:

| # | Phase | Reason | Site | Condition |
|---|---|---|---|---|
| 1 | `PreTurn` | `ContextLimit` | `turn.rs:1012,1024` | before each turn's sampling, `token_limit_reached` |
| 2 | `MidTurn` | `ContextLimit` | `turn.rs:458` | `needs_follow_up && (new-window request \|\| token_limit_reached)` |
| 3 | `PreTurn` | `CompHashChanged` | `turn.rs:1100` | previous and current turn declare different `comp_hash` — **not token-driven at all** |
| 4 | `PreTurn` | `ModelDownshift` | `turn.rs:1145` | switched to a smaller-window model and the existing context already exceeds it |
| 5 | `StandaloneTurn` | `UserRequested` | `handlers.rs:244` → `tasks/compact.rs:29` | manual `/compact` — **parallel dispatch, not via `run_auto_compact`** |
| 6 | via #2 | — | `tools/handlers/new_context_window.rs:35` | **the model calls the `new_context_window` tool**; registered only under `Feature::TokenBudget` (`tools/spec_plan.rs:1055`) and it rolls over *without* summarizing |

Three structural notes. Site #2 is gated on `needs_follow_up`: even over the limit, codex does
**not** compact mid-turn when the model is finishing — that is left to the next #1. codex has **no
direct error→compact path**: a `ContextWindowExceeded` API error calls `set_total_tokens_full`
(`turn.rs:1405` → `session/mod.rs:4075`), pinning usage at the window so the *next* check trips
naturally; agentao's in-place ladder is a different shape, not a missing one. And codex's
usage-based threshold is **not** general immunity to silent overflow — but the boundary is narrower
than "post-truncation usage is invisible". codex fires whenever the *reported* usage crosses its 90 %
/ full-window threshold, so a provider that truncates and still reports, say, 99 % **does** trigger
it; only a provider that truncates and then reports usage *below* the auto-compact threshold escapes
notice. pi-mono's extra `isRecoverableLength` (`ai/src/utils/overflow.ts:171`) is likewise not a
general answer: it requires `stopReason === "length"` with output below the intended limit, so a
provider that truncates silently and returns `stop` is outside it too.

**The full three-way trigger inventory:**

| Axis | agentao | pi-mono | codex |
|---|---|---|---|
| Where checked | every inner loop iteration | turn boundaries only | pre-turn + mid-turn (the latter gated) |
| Threshold | static `max_tokens` × 0.65 | `contextWindow − 16384` (≈ 92 %) | `min(config, window × 90 %)` |
| **Window source** | **constructor arg, static** (`agent.py:104` default 200 000; CLI reads `AGENTAO_CONTEXT_TOKENS`, `cli/app.py:278`) | `this.model?.contextWindow` — **per model** (`agent-session.ts:2057`) | `model_info.resolved_context_window()` — **per model** |
| On model switch | tiktoken encoding + token anchor only (`runtime/model.py:170-171`) | `sameModel` guard so a stale overflow does not fire on the new model | `ModelDownshift` + `CompHashChanged` sites |
| API overflow | in-place **2-rung** ladder (`_runner.py:1167`, `:1204`) | detect on message → compact + retry once | `set_total_tokens_full` → next check trips |
| Silent (non-error) overflow | not detectable | 2 provider families covered, **plus** `isRecoverableLength` (`agent-session.ts:2076`) — which needs `stopReason === "length"`, so a silent truncation returning `stop` is outside it | **partly** — fires whenever *reported* usage crosses the threshold (a post-truncation 99 % still trips it); escapes notice only when the reported usage falls *below* the auto-compact threshold |
| Cheap tier | microcompaction 55–65 % | none | none |
| Thresholds configurable | **no** (class constants) | yes (settings.json) | yes (`model_auto_compact_token_limit` + `_scope`) |

Read against three implementations rather than two: on checking cadence agentao is level with codex
and ahead of pi-mono, so §3.1's trade-off framing stands. On window sourcing both peers resolve per
model and agentao does not — but rev 3 no longer reads that as a verdict (see the retraction in the
header). agentao occupies a different, documented point in the option space: **explicit host
ownership** instead of automatic resolution. §3.4 states what is actually wrong with the way agentao
occupies it.

### 3.4 P2: the context window is host-owned, but nothing validates it

> **rev 3 re-scope.** rev 2 filed this as an unconditional internal P1. That was wrong on ownership
> and over-stated on consequence. Both are corrected below; §14-1 and §14-3 record what changed.

**The window is a documented host-owned knob, on four surfaces:**

| Surface | Citation |
|---|---|
| Public constructor parameter | `agent.py:104` (`max_context_tokens: int = 200_000`) |
| Embedding factory override | `embedding/factory.py:132` (`build_from_environment(**overrides)`) |
| CLI environment policy — **named as such** | `docs/design/cli-host-agent-factory.zh.md:104`: owner = "CLI 环境策略" |
| ACP, as one of three deliberately independent knobs | `docs/history/implementation/acp-stdio-auth-fix-plan.md:99-110`: "The three knobs **never overwrite each other**… a request that only carries `model` must not silently reset existing `contextLength`" |

So the ACP behaviour rev 2 cited as *evidence of the defect* is the opposite: it is a written
contract, deliberately chosen, with its rationale recorded (wiring the wrong field there "would
collapse the compression threshold"). **agentao has not failed to resolve the window; it has assigned
resolution to the host.** That is a different point in the option space from codex's and pi-mono's
automatic resolution, not a defective version of it.

**What is actually wrong, and it is real:** nothing checks the host got it right.

- The CLI applies **one default to every model** — `int(os.getenv("AGENTAO_CONTEXT_TOKENS", "200000"))`
  (`cli/app.py:278`) — so a user on a smaller-window model is misconfigured from turn 1 unless they
  know to set that variable.
- `set_model` (`runtime/model.py:156`) resets the tiktoken encoding (`:170`) and the token anchor
  (`:171`) and purges thinking artifacts, but issues **no validation, no warning, and no
  reconciliation** of the window. Switching models silently keeps the old number.

**Consequence — stated conditionally, because rev 2 over-claimed it (§14-3).** With a configured
window *larger* than the model's real one (e.g. `200_000` configured, 32 K real ⇒ thresholds
110 K / 130 K):

- Growth that is **gradual** never reaches the microcompaction band, because the API rejects at 32 K
  first. Growth that is **lumpy** may or may not: `needs_microcompaction` is a **half-open** band,
  `(55 %, 65 %]` — `est > 0.55 × max` **and** `est <= 0.65 × max`
  (`context_manager.py:278-279`) — so one oversized tool
  result that lands the estimate inside 110 K–130 K fires the cheap tier, while a jump straight past
  130 K skips it and goes to planned full compaction instead. Either way the band is **not strictly
  unreachable**; "the cheap tier is dead" holds for the gradual case only.
- A rejected call costs one round-trip plus one summarization. This is **not** every turn — after a
  compaction history is small again, and the cost recurs only when it re-crosses the real window.
- The ladder usually recovers but is **not guaranteed to**: `messages[-2:]` can itself exceed the
  window (a single huge tool result), and the third call then returns the error (§9.3).

With a configured window *smaller* than the model's real one, there is no overflow at all — the cost
is only wasted window (compacting at 20.8 K on a 200 K model). rev 2's README entry said "any
non-200 K model", which was wrong in this direction.

For a provider that silently truncates instead of erroring, agentao gets no rejection either, so a
wrong window is uncorrected *and* undetectable. **Which end such a provider drops is not established
here** — rev 2 asserted head-loss without evidence (§14-3).

**Do not fix this by porting codex's sites #3/#4.** codex needs `ModelDownshift` and
`CompHashChanged` as *separate trigger sites* because its check runs at turn boundaries and it wants
to compact using the **previous** model (whose `comp_hash` the history matches). agentao checks every
iteration, so a corrected window is picked up by the next iteration on its own.

**Routes (deliberately not collapsed to one).** Note that rev 2's route (c), "make it configurable",
is **already implemented** — see the four surfaces above. The remaining work is validation and
resolution:

- **(a) Derive the window from model metadata.** Needs a model→window table agentao does not have,
  and since agentao is provider-neutral that table goes stale continuously.
- **(b) Recover the real limit from the API error.** Overflow messages usually carry it — Anthropic
  `"213462 tokens > 200000 maximum"`, xAI `"maximum prompt length is 131072"` — and
  `_OVERFLOW_PATTERNS` (`context_manager.py:1235`) already matches those strings but keeps only the
  boolean and discards the number. Self-heals after the first wall-hit; no table to maintain.
- **(c′) Validate and warn.** Keep host ownership; on `set_model`, and once at startup, surface that
  the configured window is being carried across a model change unverified. Smallest change, and the
  one that matches the documented ownership rather than fighting it.

(b) and (c′) are not mutually exclusive. (a) may have value elsewhere (`/model` completion, cost
estimation) — **not checked**, so no claim either way.

---

## 4. Token accounting

### 4.1 The estimator stack

| Tier | agentao | pi-mono |
|---|---|---|
| 1 | real API `prompt_tokens` anchored to the message count that produced it, plus a local estimate of only what was appended since (`context_manager.py:130,153`) | `usage` from the last valid assistant message (`compaction.ts:202`) |
| 2 | tiktoken, per model family (`o200k_base` / `cl100k_base`) | — |
| 3 | **CJK-aware heuristic**: ASCII 0.25 tok/char, non-ASCII 1.3 tok/char (`context_manager.py:40`) | **`chars/4`** for every trailing message (`compaction.ts:266`) |

agentao is materially better here, and the gap is worst on exactly the histories most likely to be
long: `chars/4` under-counts Chinese roughly fivefold. agentao's tier-1 anchor is also the more
careful design — it charges the previous turn's system prompt for one turn and self-heals, a
trade-off documented in place (`context_manager.py:157-168`).

### 4.2 Images

`_count_message_tokens` (`context_manager.py:192`) walks list content and sums only blocks with
`type == "text"`. An image block contributes **0**. agentao does carry images —
`_runner.py::_render_image_reference_fallback` exists precisely to degrade them for non-vision
models — so this is a live under-count, not a theoretical one.

pi-mono charges `ESTIMATED_IMAGE_CHARS = 4800` (`compaction.ts:244`), i.e. ~1 200 tokens per image.

Effect, **scoped in rev 3 (§14-13)**: this only biases the part of the estimate that is locally
computed. `_threshold_token_estimate` (`:153`) charges the already-sent prefix at the real API
`prompt_tokens`, which *does* include the provider's image cost — so the under-count applies to
images appended **since the last anchor**, not to the whole history. On a vision-heavy session the
threshold check still reads low turn-to-turn, so compaction fires later than intended, but the error
is bounded by one turn's new images rather than accumulating.

---

## 5. Cut point and keep window

### 5.1 How much is kept

- **agentao — by message count.** `keep_count = min(KEEP_RECENT_MESSAGES, max(4, int(len × 0.60)))`
  (`context_manager.py:522`), then `_find_split_index` (`:434`) advances to the first non-`tool`
  message, preferring a `user` one. Refusing to land on a `role: "tool"` message is the correctness
  constraint — cutting there orphans a result from its `tool_calls`.
- **pi-mono — by token budget.** `findCutPoint` (`compaction.ts:403`) walks backwards accumulating
  `estimateTokens` until it passes `keepRecentTokens` (default 20 000), then snaps to the nearest
  valid cut point. `findValidCutPoints` excludes `toolResult` for the same correctness reason.

pi-mono's is the better shape and the change is small: agentao already has `_count_message_tokens`,
so replacing the count with a backwards token walk is local to `compress_messages`. Under the current
rule, 20 messages of terse exchanges and 20 messages carrying four 40 K tool results are treated
identically.

### 5.2 Splitting a turn

pi-mono detects a cut that lands mid-turn (`CutPointResult.isSplitTurn`), walks back to the turn's
starting user message (`findTurnStartIndex`), and summarizes that prefix **separately** with a
dedicated prompt (`TURN_PREFIX_SUMMARIZATION_PROMPT`, `compaction.ts:821`) at half the response
budget, merging it under a `**Turn Context (split turn):**` header (`compaction.ts:911`).

agentao has no equivalent. `_find_split_index` *prefers* a `user` boundary but falls back to any
non-tool message — and that fallback exists for a real reason documented in place: requiring a `user`
boundary made compaction a silent permanent no-op when a tail had none (20 consecutive
assistant/tool messages is ~10 tool calls in one turn). So the fallback is correct; what is missing
is the compensation for it.

The originating user message normally sits in `messages[:split_index]` (`context_manager.py:558`) —
exactly what goes to the summarizer — so it *is* represented in the summary. What agentao lacks is
any **guarantee**: there is no dedicated turn-prefix summary, so the request competes for the
transcript budget (§6.3) like everything else and then survives only as much of the summarizer's own
compression as it happens to. pi-mono reserves a separate call at its own budget for exactly this.
(rev 2 said "no record at all", which was too strong — §14-9.)

---

## 6. The summarization request

### 6.1 The prompt

| | agentao | pi-mono |
|---|---|---|
| Shape | 9 sections, two-stage `<analysis>` then `<summary>` (`context_manager.py:_SUMMARIZE_SYSTEM_PROMPT`) | 7 sections, single stage (`compaction.ts:467`) |
| Sections | Request/Intent, Concepts, Files & Code, Errors & Fixes, Problem Solving, User Messages, Pending, Current Work, Next Step | Goal, Constraints & Preferences, Progress (Done/In Progress/Blocked), Key Decisions, Next Steps, Critical Context |
| Tool-call guard | `tools=None` on the call | `toolChoice: "none"` **and** a post-check that throws if the response contains a `toolCall` (`compaction.ts:706`) |
| Response budget | none set explicitly | `min(0.8 × reserveTokens, model.maxTokens)` (`compaction.ts:659`) |

Both are reasonable. agentao's is more detailed and more explicitly oriented at code work; pi-mono's
`Progress` section with Done/In Progress/Blocked is the better shape for iterative update, which is
the next point.

### 6.2 How the previous summary is carried forward

This is pi-mono's best idea in this module.

- **pi-mono:** the previous summary is appended **unconditionally, after the transcript, in a
  dedicated tag**, and switches the instruction block to `UPDATE_SUMMARIZATION_PROMPT` (`:537`),
  whose first rule is "PRESERVE all existing information from the previous summary".
  **Corrected in rev 3 (§14-8):** rev 2 called this "a separate structured input". It is not — the
  tags are concatenated into **one string** and sent as a single user message
  (`compaction.ts:670-680`), exactly as agentao does. What differs is *where the competition
  happens*, see below.
- **agentao:** the previous summary is re-fed **inline**, as just another message in the transcript
  (`_format_for_summary` detects a `[Conversation Summary]` prefix and strips the end marker).

agentao's shape is what forced two separate patches: `carry_index` is exempted from budget eviction
in `_join_within_budget` (`context_manager.py:1018`) because the carried summary is by construction
the *oldest* block and newest-first spending drops it first; and `_clip_carry_summary` caps it at
half the budget so it cannot starve the live tail. Both are correct fixes.

**What pi-mono actually avoids is narrower than rev 2 claimed.** It has no local newest-first
allocator, so the carried summary is never a *block competing for eviction* — it is appended after
the transcript, always. That removes agentao's eviction failure mode. It does **not** remove
competition: both halves still share one provider context, and since pi-mono bounds neither (§6.3),
a large carried summary can still crowd the transcript at the provider. The right summary of the
difference is "pi-mono has no allocator to get the eviction order wrong in", not "the old summary
does not compete".

Correspondingly the adoption cost rev 2 quoted was wrong: pi-mono builds one flat string too, so
adopting its shape does **not** require a structured second input. The change is the dedicated
UPDATE prompt plus removing the carried summary from the eviction pool.

### 6.3 Bounding the summarization input

| | agentao | pi-mono |
|---|---|---|
| Global cap on the assembled transcript | `max(2000 tok, max_tokens × 0.10)` (`context_manager.py:750`), spent **newest-first** with a contiguous-suffix guarantee and one elision marker at the seam (`_join_within_budget`, `:1018`) | **none** |
| Per tool result | 1 000 chars head-only; **4 000 chars head+tail if `_FAILURE_MARKERS` matches** (`:723,734,943,774`) | 2 000 chars, **head-only** (`utils.ts:89,95`) |
| Per ordinary message | 2 000 chars | uncapped |
| Assistant thinking blocks | included in the message budget | uncapped (`utils.ts:133`) |
| Carried summary | 8 000 chars, further capped at half the budget | uncapped; appended after the transcript rather than competing for eviction (§6.2) |

Two things follow.

**pi-mono has no global bound on what it sends the summarizer.** The only limiter is the per-
tool-result 2 000-char cap; user text, assistant text, and thinking blocks are serialized whole. At a
184 K trigger with 20 K kept, `messagesToSummarize` covers roughly 164 K tokens of history. Whether
that actually overflows in practice depends on the tool-result-to-prose ratio, and **I did not
measure it** — tool results are usually the bulk, and capping them at 2 000 chars each may well
shrink the serialized text enough. The structural point stands regardless: nothing bounds it.
agentao's `_SUMMARY_INPUT_BUDGET_RATIO` exists because a failed summarization increments the circuit
breaker, turning an input-size problem into a compaction outage.

**pi-mono's head-only truncation drops the wrong end.** `truncateForSummary` is
`text.slice(0, maxChars)`. A failing command's diagnostic — traceback, assertion, non-zero exit — is
at the *end*. Its own prompt asks to "Preserve exact file paths, function names, and error messages";
head-only truncation is how you lose exactly those. agentao's `_FAILURE_MARKERS` tier
(`context_manager.py:774`) is anchored on diagnostic *shapes* rather than bare words specifically to
avoid over-tiering (measured: the shape regex matches 9 of 272 source files where a
`traceback|exception|\berror\b` word scan matched 169).

---

## 7. Storage model: destructive rewrite vs session tree

| | agentao | pi-mono |
|---|---|---|
| What compaction produces | a new list `[boundary_marker, summary, file_hint?, pinned…, kept…]` that replaces `agent.messages` (`context_manager.py:477`) | a `CompactionEntry{summary, firstKeptEntryId, tokensBefore, details, usage}` appended to the session tree (`session-manager.ts:1097`) |
| What the model then sees | that list | `buildSessionContext()` (`session-manager.ts:461`) renders `[compactionSummary]` + every entry from `firstKeptEntryId` onward (`:404-452`) |
| Where the originals go | gone from memory, and **on defaults gone entirely** — the SQLite row stores no messages, replay is off, and a session save persists the compressed list (see below) | **still on the tree, addressable** — written to the session file by default; under `--no-session` (`SessionManager.inMemory`, `main.ts:358`, `session-manager.ts:1569`) they stay in the runtime tree only and vanish with the process |
| Consequence | compaction is one-way | you can navigate back across a compaction boundary; `branch-summarization.ts` exists to summarize the branch you leave |

**Under default settings the discarded originals have no durable copy anywhere:**

- `session_summaries` stores `summary_text` plus counters — **no original messages**
  (`memory/storage.py:44`).
- Replay is **off by default** (`replay/config.py:36`, `REPLAY_DEFAULTS = {"enabled": False, …}`).
- An ordinary session save persists the **current** message list (`embedding/sessions.py:145`),
  which after a compaction is the compressed one.

So agentao's compaction is not merely one-way for *resumption*; on defaults the text is simply gone.
(rev 2 claimed it survived in replay + SQLite and that "nothing is lost for forensics" — §14-2.)
Turning replay on gives you the closest thing, but not a byte-exact one:
every event is passed through `sanitize_event` before it is written (`replay/recorder.py:135`), which
runs an always-on credential scanner that rewrites matches in place (`replay/sanitize.py`, via
`replay/redact.py::scan_recursive`). So the replay copy is a *redacted* record, adequate for audit
and not guaranteed adequate for reconstruction. That widens the gap
this section describes rather than narrowing it, and it is why the §1 table now carries it as an
observation. Whether it should change is a product question about agentao's session model, not a
defect in the compaction code.

pi-mono also threads a typed `details` generic through the entry, which is the extension escape hatch
(§8) — an extension can stash an artifact index or version marker beside the summary.

---

## 8. Extensibility — P1

| | agentao | pi-mono |
|---|---|---|
| Pre-compaction hook | `PreCompact` plugin hook, `_dispatch_pre_compact(...) -> None`, documented "side-effect only" (`runtime/chat_loop/_hook_dispatch.py:163`) | `session_before_compact`, returns `SessionBeforeCompactResult { cancel?, compaction? }` (`extensions/types.ts:592,1133`) |
| Can cancel? | no | yes — `result.cancel` aborts the compaction (`agent-session.ts:1903`) |
| Can replace the result? | no | yes — `result.compaction` is used verbatim in place of the built-in summarizer (`agent-session.ts:1907`) |
| Post events | `CONTEXT_COMPRESSED` host event | `session_compact` (`types.ts:606`), `session_compact_failed` (`:617`), plus `compaction_start` / `compaction_end` UI events carrying `reason: manual\|threshold\|overflow` |
| Cancellation | none | `AbortController` per compaction, separate for manual and auto (`agent-session.ts:332`), `abortCompaction()` (`:2017`) |
| Reason vocabulary | `compaction_type` + `reason` strings on the hook payload | typed `"manual" \| "threshold" \| "overflow"` on every event, plus `willRetry` |

The reference extension (`examples/extensions/custom-compaction.ts`) demonstrates the substitution:
it swaps the summarization model to Gemini Flash and summarizes **both** halves
(`messagesToSummarize + turnPrefixMessages`) in one call instead of two.

**Corrected in rev 3 (§14-10).** rev 2 said it "keeps only the summary". It does not — it returns
`preparation.firstKeptEntryId` unchanged, under the comment "Use firstKeptEntryId from preparation to
keep recent messages" (`custom-compaction.ts:100-107`), so the recent window is preserved exactly as
in the default path. Note the trap: the example's **own header comment** claims it "discards all old
turns completely, keeping only the summary", which contradicts its code. rev 2 trusted the comment
and did not read the return value — the error is still mine, but flag the upstream inconsistency
before anyone repeats it.

**Why this is P1 for agentao specifically.** agentao's stated identity is an embedded harness with a
host-facing stability boundary (`docs/design/embedded-host-contract.md`). Compaction is the one
operation that *permanently rewrites the host's conversation*, and it is the one operation the host
has no say in. Every other comparable policy in agentao — permissions, tool allowlists, LLM extra
params — is injectable. This one is observe-only.

That said, this is not a "port `session_before_compact`" item. pi-mono's version is shaped by its
tree storage: `CompactionResult` carries a `firstKeptEntryId`, which has no analogue in agentao's
flat list. The agentao-shaped question is narrower: *what is the minimal replaceable unit?* Plausible
answers range from "let a hook veto" to "let a hook supply the summary text" to "let a hook supply
the whole replacement message list". They are meaningfully different in blast radius and none is
obviously correct — that is a design decision, not a port.

---

## 9. Failure handling

### 9.1 When the summarizer call fails

- **agentao:** `_summarize_messages` (`context_manager.py:832`) catches everything and returns `""`.
  The caller increments `_consecutive_compact_failures` (`:590`) and returns history unchanged. **No
  retry.**
- **pi-mono:** every summarization goes through `completeSummarization` (`compaction.ts:565`), which
  wraps the call in `retryAssistantCall` with the session's configured retry policy, so a transient
  stream drop does not fail the compaction.

### 9.2 The circuit breaker — P1

`compaction_circuit_open` returns true at 3 consecutive failures (`context_manager.py:423,72`), and
`compress_messages` short-circuits on it **before attempting anything** (`:507`). The only reset is
`self._consecutive_compact_failures = 0` at `:593`, which is *after* a successful summary — i.e.
downstream of the short-circuit.

So: once open, no attempt is made; with no attempt there is no success; with no success there is no
reset. Compaction is disabled for the life of that `ContextManager` **instance** — not the process:
a fresh `Agentao` starts clean, and `/clear` builds a new session but does *not* rebuild the manager,
so it does not reset the counter either. Nothing in the CLI resets it
(`grep -rn '_consecutive_compact_failures' agentao/` → 8 hits, all inside `context_manager.py`).

**And it blocks manual `/compact` too.** The breaker check sits at `context_manager.py:506`, at the
top of `compress_messages`, above and independent of any `is_auto` branch; the
summarization-failure increment at `:590` is likewise unconditional, so a failed manual compaction
advances the counter as well. The `is_auto` exemption at `:539-546` covers **only** the
no-safe-split-point counter. The one path a user could use to recover from an open breaker is
therefore itself blocked by it.

This is known — the code comment at `runtime/chat_loop/_compaction.py:88` says so explicitly
("the counter has no reset path") and the stand-down logs a warning precisely because that line is
the only signal. Recording it here because pi-mono's design has no analogue: it retries per attempt
and carries no permanent latch, so a run of transient failures cannot disable compaction for the
session.

Both a retry wrapper and a reset path would address it, and they are independent — either alone
helps, and they are not alternatives to each other.

### 9.3 Overflow recovery

| | agentao | pi-mono |
|---|---|---|
| Detection | `is_context_too_long_error(exc)` on the raised exception — 21 positive patterns + 4 negative guards (`context_manager.py:1235-1290`) | `isContextOverflow(message, contextWindow)` on the assistant message — 25 positive + 3 negative, plus two non-error cases (`ai/src/utils/overflow.ts`) |
| Non-error overflow | not covered | **covered**: silent overflow (`stopReason === "stop"` but `input + cacheRead > contextWindow`, z.ai) and length-stop overflow (`stopReason === "length"`, `output === 0`, input ≥ 99 % of window, Xiaomi MiMo). Independently of both, `isRecoverableLength` (`agent-session.ts:2076`) also routes a length-stop that ended below the model's intended output limit into the same compact-and-retry — so pi-mono's coverage is **wider than the 99 % case** (§14-7) |
| Recovery | **2 rungs**: one `compress_messages` (`_runner.py:1167`) → retry → on a second overflow, `messages[-2:]` (`:1204`) → retry → error. rev 2 said three (§14-7) | 1 compact-and-retry, latched by `_overflowRecoveryAttempted` (`agent-session.ts:2090`) |
| Guards | — | `sameModel` — skips overflow compaction when the message came from a different provider/model (`agent-session.ts:2079`); stale-boundary check skips messages older than the latest compaction entry (`:2070`) |

agentao's ladder is deeper, but its last rung does **not** guarantee forward progress: `messages[-2:]`
can itself exceed the window — one oversized tool result is enough — and the third call then returns
the error to the caller. pi-mono's detection is broader: the two non-error overflow cases are
providers that accept an oversized prompt without erroring, which agentao's exception-based detection
cannot see by construction, and `isRecoverableLength` (`agent-session.ts:2076`) adds a semantic path
independent of both.

The comment at `context_manager.py:1234` already credits pi-mono's `overflow.ts` for the two-tier
positive+guard structure, so the pattern tables were checked once before; §11 records the delta.

---

## 10. Where agentao is ahead — do not borrow backwards

Recorded so a future reader does not "harmonize" toward pi-mono on these:

1. **CJK token estimation** (§4.1). `chars/4` under-counts Chinese roughly fivefold.
2. **A bounded summarization input** (§6.3). pi-mono bounds only the response.
3. **Failure-aware head+tail clipping** (§6.3). pi-mono's head-only truncation drops the traceback.
4. **Microcompaction** — a cheap 55–65 % tier that calls no LLM at all
   (`context_manager.py:377`), with a fixed-point guarantee and a `microcompact_would_mutate`
   stand-down so a no-op pass does not fork a hook subprocess per iteration.
5. **Generic spill-to-file.** `.agentao/tool-outputs/` at 40 000 chars applies to **every** tool via
   the result formatter (`runtime/tool_result_formatter.py:29,33`), with the excerpt inviting the
   model to `read_file` it back. pi-mono's `fullOutputPath` is bash-only (`core/tools/bash.ts:55`).
6. **The overflow ladder's last rung** (§9.3).
7. **Tool-call argument rendering in the transcript.** `_format_tool_call_args`
   (`context_manager.py:1088`) parses the JSON and emits shortest values first, so an oversized
   `write_file` body cannot evict the `file_path` beside it. pi-mono's `serializeConversation`
   renders `k=JSON.stringify(v)` in insertion order with no per-value cap.

---

## 11. Checked and at parity — do not re-report

> Manual `/compact` was listed here in rev 2 and is **not** at parity — agentao's manual path is
> blocked by the circuit breaker while pi-mono's is not. It has been moved to §9.2 (§14-5).

- **Tool-result orphaning.** Both refuse to cut at a tool result. agentao: `_find_split_index`
  skips `role == "tool"` (`context_manager.py:434`). pi-mono: `isCutPointMessage` returns false for
  `toolResult` (`compaction.ts:308`).
- **Tool-call suppression during summarization.** agentao passes `tools=None`; pi-mono sets
  `toolChoice: "none"` and additionally throws if a `toolCall` block comes back. Different rigor,
  same outcome for a compliant provider.
- **Overflow pattern tables.** Structurally the same two-tier design, already credited in
  `context_manager.py:1234`. Delta: pi-mono has GitHub Copilot, MiniMax, DS4, Cerebras
  (`400/413 (no body)`), z.ai `model_context_window_exceeded`; agentao has Alibaba/DashScope's
  `internalerror.algo.invalidparameter`. Neither list dominates.
- **Summary persistence.** agentao writes a `session_summaries` SQLite row
  (`memory/manager.py::save_session_summary`); pi-mono writes the `CompactionEntry` to the session
  file **when sessions are persisted** — under `--no-session` it is in-memory only. Different
  mechanism; durable on the default path for both.
- **File-operation extraction.** Both harvest paths from the summarized window to hand forward:
  agentao `_extract_recently_read_files` (last 10 `read_file` paths, rendered as a system hint);
  pi-mono `extractFileOpsFromMessage` (read/written/edited sets, rendered as `<read-files>` /
  `<modified-files>` XML and carried across compactions via `details`). pi-mono's is richer —
  it distinguishes read from modified and accumulates across boundaries — but this is a difference
  of degree, not a gap.

---

## 12. Not recommended

- **pi-mono's late threshold (~92 %).** It is coherent *with* pi-mono's tree storage and its
  production-time output caps. Adopting the number without the rest would remove agentao's headroom
  while keeping its one-way rewrite.
- **Branch summarization** (`branch-summarization.ts`). It exists to serve tree navigation. agentao
  has no session tree, so there is nothing for it to summarize between.
- **Dropping the cheap tier to match pi-mono's single tier.** Microcompaction is one of the places
  agentao is ahead (§10.4).
- **codex's separate `ModelDownshift` / `CompHashChanged` trigger sites** (`turn.rs:1100,1145`).
  They exist because codex checks at turn boundaries and wants to compact against the *previous*
  model; agentao's per-iteration check picks up a corrected window on its own. See §3.4.
- **Moving agentao's check to turn boundaries** to match pi-mono. On this axis agentao is level with
  codex and ahead of pi-mono (§3.3) — the change would lose mid-turn compaction for nothing.

---

## 13. What would change this verdict

- **§8 P1** would soften if agentao gains a different host-side lever over compaction (e.g. an
  injectable `ContextManager` subclass through the embedding surface). Check
  `agentao/embedding/` before re-asserting it.
- **§9.2 P1** is unconditional — it is an internal self-contradiction and holds even if not one line
  of pi-mono is ever borrowed.
- **§6.3's "pi-mono is unbounded"** is structural, not measured. If someone measures real pi-mono
  sessions and finds the 2 000-char tool-result cap already keeps the request small, the *risk*
  claim weakens; the *structural* claim does not.
- **§3.4 is a P2 about validation, not a defect in ownership.** The window is host-owned by design
  and by documentation; what is missing is any check that the host got it right. It would soften if
  a host-side convention or a doctor check already surfaces a mismatched window — not found here, but
  re-grep rather than trusting this line. It would *strengthen* if someone shows a common deployment
  where the CLI default silently mismatches a popular model.
- **§3.4's degradation path is reasoned from the cited constants, not observed in a session.** The
  arithmetic (130 K threshold vs 32 K window) follows directly; the *frequency* of rejected calls,
  and whether a real workload grows gradually (cheap tier skipped) or in jumps (which may land
  inside the band or overshoot it into full compaction), are both unmeasured. Instrumenting one
  session would settle it.
- **§3.3's codex material is scoped to the trigger axis only** and anchored at `2151d3a5b7`. Do not
  extend any codex claim here into the other sections; `codex-compaction-vs-agentao.zh.md` §13 is
  the errata table for the fuller codex comparison and it lists conclusions already withdrawn once.
- **Anchors expire.** pi-mono moves fast (`a69bef789` is one of several commits on 2026-08-23) and
  codex faster. Re-verify every `file:line` before acting on anything here.
- **§14 is the errata table.** Fourteen rev-2 claims were withdrawn or re-scoped after review. Read
  it before re-raising anything, and do not re-report a withdrawn claim from memory.

---

## 14. rev 3 errata — what rev 2 got wrong

All fourteen were raised in maintainer review and **each was re-verified against source before being
accepted**; none was taken on assertion. Numbering matches the review.

> **This table is history, not an override.** rev 3 recorded these corrections here but left several
> of the wrong statements standing in §1, §3.3, §6.3, §7, §9 and §13 — so the doc asserted a claim
> and its refutation at once. rev 4 rewrote those sites; the table is kept only so a reader who
> remembers a rev-2 conclusion can see why it is gone. **Do not re-derive a live claim from this
> table** — read the section it points at.

| # | rev 2 claim | Why it was wrong | Verified at |
|---|---|---|---|
| 1 | "The context window does not follow the model" is an unconditional internal **P1** | The window is a **documented host-owned knob** on four surfaces, and the ACP behaviour cited as evidence is a written contract with recorded rationale. Demoted to **P2** and re-scoped to *no validation / no warning / no reconciliation*. The framing sentence "both peers differing the same way is a gap" is **retracted** — peer convergence describes the option space, it does not convict a different point in it | `agent.py:104`; `embedding/factory.py:132`; `cli-host-agent-factory.zh.md:104`; `acp-stdio-auth-fix-plan.md:99-110` |
| 2 | Discarded originals survive in replay + SQLite, so "nothing is lost for forensics" | **False on defaults.** `session_summaries` holds `summary_text` + counters only; replay defaults to `enabled: False`; an ordinary session save persists the already-compressed list | `memory/storage.py:44`; `replay/config.py:36`; `embedding/sessions.py:145` |
| 3 | The degradation path stated unconditionally: "every turn" rejected, cheap tier "never reached", "the ladder recovers", silent truncation "loses the head" | Each needs a condition. Not every turn — only when history re-crosses the real window. Cheap tier is skipped under *gradual* growth; a lumpy jump can still enter the band. `messages[-2:]` can itself overflow, and the third call then errors. Head-vs-tail loss on a silently truncating provider is **unestablished** | §3.4, rewritten |
| 4 | All six codex trigger sites feed one `run_auto_compact` | **Five automatic** ones do. Manual `/compact` spawns a standalone `CompactTask` that does its own parallel four-way dispatch | `codex .../session/handlers.rs:244`; `.../tasks/compact.rs:29` |
| 5 | agentao's manual `/compact` bypasses the auto-path's failure accounting (listed as **parity**) | The breaker check sits at the top of `compress_messages`, above any `is_auto` branch, so it blocks manual too; the summary-failure increment is unconditional. The `is_auto` exemption covers only the no-safe-split-point counter. Moved out of §11 and folded into §9.2, which it strengthens | `context_manager.py:506`, `:590`, `:539-546` |
| 6 | pi-mono's `output` term makes its figure "not a prompt size" | Visible assistant output becomes history and **is** re-sent, so `input+output` tracks the next prompt closely. The real over-count is narrower: hidden reasoning tokens and provider-specific output accounting | `compaction.ts:146` |
| 7 | agentao's overflow ladder is 3 rungs (compress → compress again → `messages[-2:]`); pi-mono's non-error coverage is the 99 %-window case | **2 rungs** — there is exactly one `compress_messages` call in the recovery path. And pi-mono additionally routes `isRecoverableLength` into compact-and-retry, so its coverage is wider than the table showed | `_runner.py:1167`, `:1204`; `agent-session.ts:2076` |
| 8 | pi-mono passes the previous summary as "a separate structured input" that "cannot compete in the same allocation" | It concatenates `<conversation>` and `<previous-summary>` into **one string** sent as a single user message — same shape as agentao. What it avoids is agentao's *local newest-first allocator*, not provider-context competition. The quoted adoption cost ("needs a structured second input") was wrong too | `compaction.ts:670-680` |
| 9 | After a mid-turn cut the kept window has "no record of the request that produced it" | The originating user message is normally inside `messages[:split_index]`, which is what goes to the summarizer — so it is represented in the summary. Accurate gap: no *dedicated* prefix summary, hence no *guarantee* it survives budgeting and summarization | `context_manager.py:558` |
| 10 | The reference extension "summarizes everything, keeps only the summary" | It returns `preparation.firstKeptEntryId` unchanged and **keeps the recent window**. Trap worth flagging: the example's own header comment claims otherwise and contradicts its code | `custom-compaction.ts:100-107` |
| 11 | Table row labelled the `16384` default as generic "reserve" | It is `reserveTokens`, **response headroom**. Verbatim retention is `keepRecentTokens = 20000`; conflating them misreads the design | `compaction.ts:132`; `settings-manager.ts:839` |
| 12 | An open circuit breaker kills compaction "for the rest of the process" | It is `ContextManager`-**instance** state. A fresh `Agentao` starts clean; `/clear` does not rebuild the manager, so it does not reset it either | `context_manager.py:91` |
| 13 | Images undercount the estimate generally | Bounded to images appended **since the last API anchor** — the anchored prefix is charged at real `prompt_tokens`, which includes the provider's image cost | `context_manager.py:153`, `:192` |
| 14 | README index entry said "any non-200 K model", and contradicted itself on the P1 count | Only a window *smaller* than the configured value degrades into the ladder; a larger one merely compacts early. Count fixed with the §14-1 demotion | `docs/design/README.md` |

**Method note.** Findings 1, 8, and 10 share a root cause worth naming: rev 2 read a *comment or a
doc line* and inferred behaviour instead of reading the code path that produces it — the ACP comment
(1), the tag names in the prompt builder (8), and the example's header block (10), which in that last
case actively contradicts its own return statement. The lesson is the repo's existing one: verify the
sink, not the label on it.
