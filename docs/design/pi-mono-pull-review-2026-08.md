# pi-mono Pull Review (v0.80.6 → v0.83.0)

**Status:** Decision record. Drafted 2026-08-02 after surveying 434 commits / 730 files in `../pi-mono` (`34582ef34..aa0ec808b`, the delta since the 2026-07-10 pull) and fact-checking every candidate against agentao's current tree.
**Audience:** Agentao maintainers deciding what to port from pi-mono.
**Companion:** `pi-mono-pull-review-2026-08.zh.md`.
**Prior art:** `pi-mono-borrow-review.md` (v0.66 → v0.73), `pi-mono-tools-review.md`, `pi-mono-openai-stream-fix.md`.
**Method:** Categorise the delta → shortlist by the harness-vs-product boundary → grep-verify each candidate against agentao before recommending → land, defer with a stated reason, or record as not-applicable with the query that proved it.

## TL;DR

Three landed, two deferred as contract decisions, one architectural gap recorded but demand-gated, eight verified not-applicable.

| Disposition | Item |
|---|---|
| **Landed** | NFKC in the edit fuzzy matcher (PR #159) |
| **Landed** | Listener-exception logging in `EventBroadcaster` (PR #160) |
| **Landed** | `TurnOutcome.finish_reason_missing` (PR #161) |
| **Deferred — contract decision** | Sub-agent boundary for `finish_reason_missing` |
| **Deferred — contract decision** | ACP channel for `finish_reason_missing` |
| **Recorded, demand-gated** | `watch()` — atomic snapshot + gap-free subscription |
| **Decision, not recommendation** | Lanes / conversation tree; durable operations with crash recovery |
| **Not applicable (8)** | See the table at the bottom — each with the query that settled it |

The bulk of the 434 commits is pi's own product surface: a TUI alt-screen rewrite, a session-storage move to SQLite with a repository facade, and three new packages (`protocol`, `server`, `client`) implementing a remote-session wire protocol. None of that crosses into agentao's harness.

## Landed

### NFKC before the codepoint table — PR #159

**Source:** `packages/agent/src/harness/tools/edit-diff.ts::normalizeForFuzzyMatch`.

`EditTool`'s tier-3 match normalised through a codepoint table only (dashes, quotes, spaces — copied from codex-rs `seek_sequence.rs`). The table has no entries for Unicode *compatibility forms*, so an edit whose `old_text` differed from the file only by full-width punctuation fell straight through to `_not_found_hint`. In a CJK source file mixing 全角 and 半角 that is the common case: `print（"你好"）；` was unreachable from `print("你好");`.

Both passes are needed and neither subsumes the other — NFKC folds full-width forms, ligatures and every space variant, but leaves smart quotes and en/em dashes alone (no compatibility decomposition), which is exactly what the table covers.

Ordering is load-bearing, not stylistic. Sweeping the Unicode planes finds exactly five characters that NFKC folds *into* a table entry without being in the table themselves — `U+207B ⁻`, `U+208B ₋`, `U+FE31 ︱`, `U+FE32 ︲`, `U+FE58 ﹘`, the last three CJK compatibility dashes. Table-first strands all five one step short of ASCII.

agentao needs none of pi's `applyReplacementsPreservingUnchangedLines` machinery: `line_transform` only builds per-line comparison keys, while the prefix table comes from the original `content_lines` lengths, so spliced spans still index the original content.

### Listener-exception logging — PR #160

**Source:** pi's `handler_error` event (`packages/agent/docs/harness-v2.md` §10). Note it is **design-only there** — zero hits in `packages/agent/src` or `packages/coding-agent/src`; what ships today is the narrower extensions-only `ExtensionError`.

`EventBroadcaster.notify` caught every subscriber exception with a bare `except Exception: pass` — no log, no counter. Swallowing is correct and stays; being silent about it was not. WARNING-and-swallow was already the documented convention for the other side-channel sink on this contract (`HostReplaySink`, `docs/reference/host-api.md`); `broadcast.py` was the one place that did not follow it.

Three choices worth preserving:

- **Logged, not re-emitted as an event.** An event would re-enter `notify`, so a listener that raises on everything would spin forever — which is why pi's `handler_error` needs an explicit recursion guard. A log call has no such edge, so agentao needs none of that machinery.
- **`event.type` only, never `event.data`.** The credential redaction is a `Formatter` on agentao's own file handler, deliberately not a `Filter`, precisely so it does not leak into an embedded host's handlers — which means a payload logged here would reach those handlers *unredacted*.
- **`exc_info=True`.** A swallowed exception with no stack is barely better than silence.

The hook half of pi's design already had an agentao equivalent and was left alone: plugin hooks are subprocesses, so timeout / spawn-failure / non-zero-exit are each already logged in `agentao/plugins/hooks/_dispatcher.py`.

### `TurnOutcome.finish_reason_missing` — PR #161

**Source:** `2c3041242 fix(ai): support streams without finish reasons` — **adopted inverted.**

pi makes a stream that ends without `finish_reason` a hard error by default, with a per-model `supportsFinishReason: false` opt-out. agentao does the opposite: it reports the fact and classifies nothing. The reason is that every value in `INCOMPLETE_ANSWER_REASONS` becomes a CLI error envelope, so joining that set would turn each turn into a hard failure on every provider that never sends the field. The flag rides its own axis, the way `max_iterations` does, and does **not** affect `is_answer`. A host that wants the strict reading writes `o.is_answer and not o.finish_reason_missing`.

Design points worth not re-litigating:

- The wire value of `finish_reason` keeps its `"stop"` fallback. Flipping it to `None` would shift LLM_CALL_COMPLETED payloads and replay renders for every provider that omits the field.
- Detection tests *falsy*, not `is None`, because the streaming recorder gates on truthiness. Testing `is None` would let the same `""` answer differently depending on whether the turn took the streaming or the Gemini/fallback bypass — a transport detail no host can see.
- Sticky across every LLM call in the turn. An intermediate call that ends without a finish_reason may have had its tool-call arguments cut off with nothing to detect it: `_is_length_truncation` never fires and the arguments get executed.
- The compaction summariser bypasses the chat loop entirely, so it records its own observation on the context manager and the two compaction call sites fold it in. That is the one call whose output *permanently rewrites history*.
- Suppressed on a cancelled turn (the cancellation already explains the absence); reported on an errored one, where it does not.

An xhigh review of the change produced 15 verified findings, 13 fixed before merge — most of them coverage gaps where the new fact failed to reach `agentao.log`, LLM_CALL_COMPLETED, the replay `turn_completed` record, or the `agentao run` JSON envelope.

## Deferred — contract decisions, not defects

Both came out of the xhigh review of PR #161. Both are real; neither is a defect *of that change*, and each needs a shape decision first.

**1. Sub-agent boundary.** A sub-agent's `finish_reason_missing` dies with the child and never reaches the parent turn. `AgentToolWrapper` deliberately holds no parent-agent reference (`agentao/agents/tools/_wrapper.py` takes getters only), so propagating it means opening a new channel through that seam. Note the `max_iterations` precedent is *not* exact: it flows into the child's own `_classify_subagent_outcome`, making the sub-agent's result render as incomplete to the parent LLM — it does not write the parent's turn flag. Three plausible shapes: parent `TurnOutcome`, a note in the rendered sub-agent result, or `SubagentLifecycleEvent`.

**2. ACP channel.** `handle_session_prompt` returns only `{"stopReason": …}` and `acp/transport.py::_build_update` has no TURN_END branch, so no `session/update` carries the flag either. Adding one widens the ACP surface, which the project's ACP scoping deliberately keeps narrow (`acp-target-client` decisions; fs/terminal proxy is already a documented non-goal).

## Recorded, demand-gated: `watch()`

The one genuinely architectural gap. pi's `watch()` (`harness-v2.md` §9) captures a snapshot and starts buffering in **one step**; `start(listener)` then flushes the buffer in order and switches to live. Their own words: *"No sequence numbers, no registration race."* The motivation is explicitly the proxy case — a server must deliver the snapshot to its client before any event reaches the wire.

agentao has no snapshot primitive. `agentao/host/events.py` documents the gap as a contract property: *"Subscriber starts after events were emitted: no replay; only future events are delivered."* Grepping `snapshot` across `host/`, `transport/` and `acp/` returns only permission snapshots, schema snapshots and list-copy comments.

A host **cannot** build this on top of a plain `subscribe()`: read-state-then-subscribe drops the events in between, subscribe-then-read-state double-counts. It has to be a harness primitive.

agentao has already hit this race once and solved it point-wise — `agentao/acp/session_load.py` registers the session *after* replay finishes, precisely so a pipelined prompt cannot interleave live updates with replayed ones. One instance, ad hoc, no general primitive.

**Gate:** it only bites when a host attaches to a *running* turn. Today's CLI and ACP flows construct before running and never hit it.

The obvious trigger would be `agentao serve` — but note that is **not** merely unstarted: `path-a-roadmap.md` lists it under "deferred to P2 or moved to separate projects" with `✗ agentao serve daemon — clashes with "in-process harness" positioning`. So this is not a primitive waiting on a scheduled feature; on current strategy that feature is not coming.

The realistic trigger is narrower and independent of `serve`: an **embedded host** that attaches an observer to an `Agentao` mid-turn — a web UI reconnecting while a `/goal` loop or an `agentao run` is executing. That is squarely inside the stated positioning, so it can arrive without `serve` ever existing. If it does, build the primitive rather than a second point fix like `session_load.py`'s.

## Decisions, not recommendations

**Lanes / conversation tree.** pi restructured the harness around an append-only entry tree plus *lanes* — named tree positions, one operation each, running in parallel, keyed by external identity (a Slack thread id). It buys shared history prefixes and per-lane model config. agentao's implicit answer is N harness instances: `examples/slack-bot/src/bot.py` constructs a fresh `Agentao` per message. Both are coherent; pi's costs a single-writer discipline and buys token reuse. Only worth revisiting if agentao targets a multi-thread host.

**Durable operations with crash recovery.** pi's accepted prompt is a durable operation with "no partial outcomes" — a crash leaves either "did not happen" or "recovery can finish it" — backed by an operation log kept strictly out of the conversation tree. agentao's replay is explicitly out-of-core observability and sessions are save/load snapshots; a mid-turn crash loses the turn. Relevant to `/goal` loops and `agentao run`, but it is pi's entire Part II (record catalog, provisioned ids, recovery reduction) holding it up — not a slice you can take.

## Not applicable — verified

| pi change | agentao status | Query |
|---|---|---|
| `7af8533c6` abortable provider retries | Already done, before pi | SDK `max_retries=0` (`llm/client.py`) + own `_retry.py::_interruptible_sleep` |
| 6× `preserve raw stop reasons` | Never normalised; raw string passes through | `_LENGTH_FINISH_REASONS` is already a multi-vendor set |
| `f4e9ca746` date out of the system prompt | agentao is ahead — pi *deleted* it; agentao moved it to a per-turn `<system-reminder>` on the user message | `tests/test_date_in_prompt.py` |
| `cced6a21d` AGENTS.md loaded twice in nested worktrees | Structurally impossible | `prompts/helpers.py` reads `working_directory / "AGENTAO.md"` only; no ancestor walk |
| `5d548ae96` rpc bash bypassing the permission gate | No second entry point | `grep LocalShellExecutor` → `tools/shell.py` + `tools/base.py` lazy accessor only; ACP has no terminal/exec method |
| `bd2cfabc5` reject cyclic values | CBOR-specific | Python `json.dumps` raises `Circular reference detected` by default |
| `74caa2649` validate package manifests | Already thorough | `embedding/plugins/manifest.py` validates each field with `isinstance` |
| Append-only context invariant | Already holds | `grep 'messages.insert\|messages\[:0\]'` → no match; only wholesale compaction, pi's own named exception |

**Deliberate divergence worth holding:** pi added `protocol` + `server` + `client` — its own CBOR wire protocol and Unix-socket transport — and now runs two remote surfaces. agentao has one (ACP), scoped narrowly on purpose — see `acp-server-conformance-review.md` §4, where the non-IDE / chat-automation target decision is what makes the narrow surface correct rather than incomplete. Not a borrow; a line to hold.

**Observation, not a candidate:** `3d8f74357 message-anchored tool loading` anchors newly-added tools to a tool-result position instead of the cached prompt prefix, so adding a tool mid-session does not wipe the cache. agentao has the same shape (`/goal`'s `add_tool` injection, skill activation), but the mechanism depends on Anthropic / OpenAI-Responses cache anchors and agentao is chat.completions-shaped. Not portable.

## Process note

The xhigh review of PR #161 caught four weak tests I had written, including a positive fixture that reused the accumulator's own `"stop"` fallback so its assertion could not fail. Separately, two of my counterfactual checks initially *passed* when they should have failed — the falsy-vs-`None` fix and the stale-summary reset were both unprotected, because my first tests exercised the producer and a non-compacting turn respectively, neither of which reaches the changed line.

The lesson generalises past this PR: writing the test is not the check. Reverting the fix and confirming the test reddens is the check, and it has to be done per distinct piece of logic, one at a time — two of these counterfactuals masked each other when applied together.
