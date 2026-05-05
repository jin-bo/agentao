# pi-mono Borrow Review (v0.66 → v0.73)

**Status:** Decision record. Drafted 2026-05-04 after surveying ~590 commits in `../pi-mono` between v0.66 and v0.73 and fact-checking each candidate against agentao's current code.
**Audience:** Agentao maintainers deciding what (if anything) to port from pi-mono / pi-coding-agent.
**Companion:** `pi-mono-borrow-review.zh.md`.
**Method:** Initial enthusiastic list → reverse review against agentao's actual modules (`runtime/`, `harness/`, `plugins/`, `tools/`) → final keep/cut/reframe verdict.

## TL;DR

12 candidates surveyed (the disposition table has 13 rows because `shouldStopAfterTurn` is listed twice — once as CUT and once as the reframed "add `Stop`/`PreCompact` events" follow-up). Final disposition:

- **Do now (1):** grep/find argument-injection fix. Confirmed vulnerability at three sites in `agentao/tools/search.py` (one ripgrep, two git-grep branches).
- **Do soon (1):** compact `read` rendering for context files (`AGENTAO.md`, `CLAUDE.md`, `SKILL.md`).
- **Do as protocol completion (1):** add `Stop` / `PreCompact` event types to the existing plugin-hook system. *Not* a port — agentao already has a Claude-Code-style hook protocol; this is filling in missing event types.
- **Backlog (4):** `prepareArguments` per-tool normalize hook, stale-extension-context detection, OSC 9;4 progress, stacked autocomplete. Useful patterns, no current pain.
- **Cut (2):** `shouldStopAfterTurn` (redundant with existing hooks), self-update / batch package update (npm-only, doesn't apply to `uv` world).
- **Reframe / wait (3):** `terminate: true` tool-result hint, per-tool `executionMode = "sequential"`, bash incremental streaming. Each has a real-but-different use case in pi-mono; agentao's equivalent need is either absent, semantically non-overlapping, or already covered by a different mechanism.

The honest meta-finding: the first-pass list was biased toward "architecturally interesting" rather than "actually missing." Once compared against the existing plugin hooks, AsyncTool framework, and per-tool-instance locks, most "Tier 1" items collapse to redundant or premature.

## Reverse-review verdicts (per candidate)

### CUT

#### `shouldStopAfterTurn` post-turn callback (pi-agent-core v0.72.0)
**Verdict:** Cut. Redundant with existing infrastructure.

pi-mono added a low-level `shouldStopAfterTurn(...)` callback at the turn boundary because its low-level loop has no other extensibility seam. agentao already has a complete plugin-hook protocol (`agentao/plugins/hooks.py` + `models.py`) modeled on Claude Code: `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`. `chat_loop.py:140` already supports block / inject context / early-return at the turn boundary.

What's actually missing is **event-type coverage**, not a new callback shape. Claude Code defines `Stop` and `PreCompact` events that agentao does not yet emit. Adding those to the existing `_dispatch_lifecycle` pipeline gets the same outcome as `shouldStopAfterTurn` while keeping a single, documented protocol surface.

This is "complete your own protocol," not "borrow from pi-mono."

#### Self-update + batched package updates (pi-coding-agent v0.68.0 / v0.70.3)
**Verdict:** Cut. Doesn't apply.

agentao ships via `uv` + Python. pi-mono's batched npm/pnpm updates and self-rebuild story are npm-ecosystem specialization. Nothing to borrow.

### REFRAME / WAIT

#### Tool-result `terminate: true` hint (pi-agent-core v0.69.0)
**Verdict:** Wait. Mechanism is sound, but no current tool has the matching semantics.

pi-mono's motivating use case is `structured-output.ts` — a tool whose return value **is** the run's final answer, so the loop must end without another LLM call. The closest agentao surface, `ask_user`, has the opposite shape: the user reply resumes the conversation, it does not terminate it. So this is not "agentao's `ask_user` doesn't match" — the two semantics simply don't overlap.

The real candidates would be `complete_task` / `final_answer` / `submit_for_review` — none of which agentao currently has. Decide on the tool first; the mechanism is a one-day add once the tool exists. Porting the mechanism speculatively is over-engineering.

#### Per-tool `executionMode = "sequential"` override (pi-agent-core v0.68.0)
**Verdict:** Wait. Already covered by a different mechanism.

`agentao/runtime/tool_executor.py:119-152` runs a `ThreadPoolExecutor(max_workers=8)` with **per-tool-instance locks** that serialize concurrent calls to the same tool. Because each tool name is registered exactly once in the registry, "per-instance" and "per-tool-name" coincide here — two parallel calls to `run_shell_command` hit the same lock and serialize. The risk pi-mono solves with `executionMode: "sequential"` (e.g., shell or write_file inside a parallel batch) is already addressed.

The remaining gap would be **cross-tool serialization** ("when shell is running, don't let write_file start either"), which is not a stated requirement. Skip until requested.

#### Bash incremental streaming + `OutputAccumulator` (pi-coding-agent v0.73.0)
**Verdict:** Wait. No consumer needs it yet.

`agentao/tools/shell.py` is currently capture-then-return: subprocess output is collected and the whole result is delivered in one `ToolLifecycleEvent`. Migrating to incremental streaming would require a new "tool-execution-update" event variant on `EventStream` (the current event stream does not have one) plus ~200 lines of accumulator + line buffering + binary handling. None of agentao's current event consumers (CLI, future IDE host) need live shell output today.

Action: build a host-side demo that *needs* live shell output, then add the event variant **and** the accumulator together. Doing it speculatively risks adding code only to keep parity with pi-mono.

#### `after_provider_response` + structured `BuildSystemPromptOptions` introspection
**Verdict:** Reframe. Use as case study, not template.

The metacognitive-boundary design (`docs/design/metacognitive-boundary.md`) commits to a "schema + default + host-override" form, not to "callbacks inserted at loop seams." pi-mono's hook surface is a useful **inventory of what hosts ask for** (system-prompt options, post-response audit, message replacement), but the mechanism shape is wrong for agentao's protocol approach.

When the boundary work resumes, mine pi-mono for *what fields hosts want exposed*, not for *where to put callbacks*.

### BACKLOG (good patterns, no current pain)

#### `prepareArguments` per-tool normalize hook (pi-agent-core v0.64.0)
agentao has `arg_repair.py` (219 lines) + `name_repair.py` (78 lines) as global heuristics. pi-mono's pattern collapses these into per-tool declarative normalization, which is cleaner. But: there is no current bug report or new-tool friction blocked on the global heuristics. Refactor is pure code-quality, not a feature unlock. Defer.

#### Stale-extension-context detection (pi-coding-agent v0.69.0)
Triggered by session `/fork` / `/clone` / replace. agentao has none of these flows yet. Pattern to remember when session-lifecycle work starts.

#### OSC 9;4 terminal progress indicator (pi-coding-agent v0.69.0)
~50 LOC, off-by-default ergonomic win in iTerm2 / WezTerm / Ghostty. CLI-only, zero architectural risk. Pick up when the CLI gets its next polish pass.

#### Stacked autocomplete providers (pi-coding-agent v0.69.0)
Only relevant if/when agentao adds an interactive editor. No-op until then.

### KEEP — DO NOW / SOON

#### grep argument-injection fix (pi-coding-agent v0.71.0, PR #4018)
**Verdict:** P0. Confirmed vulnerability at three sites.

pi-mono's fix idiom — `rg -- <pattern> <path>` so a pattern like `--pre=/tmp/payload.sh` is treated as text, not a flag — applies to ripgrep but **not directly** to git grep. agentao has both backends and is exposed at three call sites:

- `agentao/tools/search.py:308` (ripgrep) — `cmd.extend([pattern, "."])` with no separator. **Fix:** `cmd.extend(["--", pattern, "."])`. ripgrep's `--` is the standard option terminator, so this is the pi-mono recipe verbatim.
- `agentao/tools/search.py:269` (git grep, no file-pattern branch) — bare `cmd.append(pattern)`.
- `agentao/tools/search.py:267` (git grep, **with** file-pattern branch) — `cmd.insert(-2, pattern)` puts the pattern *before* the existing `--`, but for git grep `--` is the **pathspec separator**, not an option terminator: a pattern beginning with `-` is still parsed as a flag on either side of `--`. **Fix for both git-grep branches:** use `git grep ... -e <pattern> [-- <pathspec>]`. The `-e` flag explicitly marks the next argument as a pattern; this is the documented git-grep equivalent of ripgrep's `--`.

Net change is ~6 lines plus a regression test that exercises both backends with a pattern like `--help` / `--pre=…` and asserts "no matches" rather than execution / unintended behavior. No design tradeoff. Do this independent of the rest of the review.

#### Compact `read` rendering for context files (pi-coding-agent v0.73.0)
**Verdict:** P1. Pure UX, zero risk.

`read` of `AGENTS.md` / `CLAUDE.md` / `SKILL.md` (and equivalents) collapses by default in interactive output, with line-range hints. agentao's read tool currently dumps the full content of project context files every time, wasting screen and tokens in the rendered transcript. Mechanical change, no protocol impact.

#### Add `Stop` / `PreCompact` event types to plugin-hook system
**Verdict:** P2. Protocol completion, not a borrow.

Listed here to frame the alternative to `shouldStopAfterTurn`. agentao's hook protocol is incomplete relative to Claude Code's published surface. Closing that gap inside the existing `agentao/plugins/hooks.py` dispatcher is the right shape: hosts that already implement Claude-Code-style hooks get drop-in compatibility, and agentao gets the same expressiveness pi-mono added with `shouldStopAfterTurn` without inventing a parallel callback path.

Defer until a concrete host workflow asks for it (compaction gates, cost gates, post-turn review). Estimated 1–2 days when triggered.

## Disposition table

| Item | First-pass tier | Final verdict | Reason |
|---|---|---|---|
| grep argument-injection fix | T1 | **DO NOW** | Confirmed vuln at 3 sites (rg + 2 git-grep branches) |
| Compact read rendering | T3 | **DO SOON** | Zero-risk UX |
| `Stop` / `PreCompact` hook events | (was: shouldStopAfterTurn T1) | **DO WHEN TRIGGERED** | Reframed: protocol completion, not port |
| `prepareArguments` per-tool hook | T2 | Backlog | No current pain |
| Stale-extension-context detection | T2 | Backlog | No session fork yet |
| OSC 9;4 progress | T3 | Backlog | Polish |
| Stacked autocomplete | T3 | Backlog | No interactive editor |
| `terminate: true` tool hint | T1 | Wait | Need the tool first |
| Per-tool `executionMode` | T1 | Wait | Per-instance lock already covers it |
| Bash incremental streaming | T2 | Wait | No consumer needs it |
| `after_provider_response` / system-prompt options | T2 | Reframe as case study | Wrong mechanism shape for boundary design |
| `shouldStopAfterTurn` | T1 | **CUT** | Redundant with plugin hooks |
| Self-update / batch updates | T3 | **CUT** | npm-only, N/A |

## Lessons for future cross-project surveys

1. **Fact-check before recommending.** The first-pass list named things that "look architecturally interesting." Half collapsed once compared against `tool_executor.py`, `plugins/hooks.py`, and `AsyncToolBase`.
2. **Different starting points → different right answers.** pi-mono added low-level callbacks because it has no high-level hook protocol. agentao has the protocol; the right move is finishing it, not stapling another callback layer alongside.
3. **Mechanism without use case is over-engineering.** `terminate: true` is a clean mechanism, but agentao has no tool that would use it. Porting it now creates a feature looking for a problem.
4. **A real bug beats a clever feature.** The single highest-value finding in 590 commits was a small (~6-line) security fix touching three sites, not any of the marquee features.
