# Agentao — Optimization Opportunities Review

**Status:** Review record. Drafted 2026-06-19 from a multi-dimension audit of the
`agentao` runtime, packaging, and CLI/ACP surfaces. **This is an evidence-backed
findings list + prioritized improvement proposal, not an approved plan.** It
records where the codebase carries localized performance cost, packaging dead
weight, or drift-prone duplication — and, *for the maintainer's judgment*, what
to fix now, what to consolidate, and what to leave alone.
**Audience:** Agentao maintainers.
**Companion:** `optimization-opportunities-review.zh.md`.
**Related:**
- `core-boundary-review.md` — the core/host package-boundary audit; several
  findings here respect the boundaries it documents (don't pull display logic
  into core, etc.).
- `path-a-roadmap.md` — embed-first strategy; the packaging findings (slim core
  install, lazy imports) serve the "library-only install is lean" promise.
- `acp-server-conformance-review.md` — the ACP handler consolidation finding
  (`require_active_session`) extends cleanup it already anchors.

**Method:** four parallel evidence-gathering passes (complexity / per-turn
performance / duplication / packaging), each required to cite `file:line` and
back every claim with `grep`/read rather than intuition — consistent with this
repo's *evidence-before-recommendation, gap ≠ need* discipline. The highest-impact
and least-reversible claims (dead extras, `tiktoken` placement, dead constructor
params, ACP helper adoption) were independently re-verified before landing this
doc. Code references anchored to `main`@`08c23db` (2026-06-19). Line numbers will
drift — re-grep before acting.

---

## TL;DR

Agentao is a **mature, well-maintained codebase, not one with low-hanging tech
debt.** 262 source files / ~54.5k LOC with a near-1:1 test ratio (195 test files
/ ~54.8k LOC), only 4 TODO markers (3 of them false positives), and clean
lazy-import boundaries (`import agentao` loads **zero** heavy modules, verified
empirically). There is **no architectural emergency.**

"Optimization" here means three concrete, bounded classes of work:

1. **Tier 1 — quick wins** (low risk): removal of dead packaging extras, removal
   of dead constructor params, one misleading-comment fix that prevents a future
   cleanup from breaking live API. (The two compaction-token tweaks once listed
   here as "per-turn perf wins" were **downgraded on reverse review** — the
   Tier-1 token anchor already makes steady-state turns cheap, so they are
   cold-path robustness tweaks only. See T1.2 / T1.3.)
2. **Tier 2 — two latent bugs + opportunistic cleanup** (do the bugs now, the
   rest only when nearby): T2.3 (a settings.json cwd bug) and T2.5 (an ACP
   per-session-cwd bypass) are single-point fixes. The remaining duplication
   (T2.1 / T2.2 / T2.4 / T2.6) is consolidation against helpers that already
   exist — worth folding in *opportunistically* when those files are touched,
   **not** platform work to schedule for its own sake.
3. **Tier 3 — larger refactors** (real but costly, opt-in): one genuinely fat
   method (`_execute_one`, 249 lines), one display method living in core, one
   deeply-nested recovery method.

Recommended starting point: **low-risk cleanup first** (T1.1 `google`, T1.4 dead
params, T1.5 comment), then the **Tier-2 bug fixes** as single-point changes
(T2.3 settings.json read+write cwd, T2.5 ACP cwd bypass). T1.2 / T1.3 are
**optional cold-path tweaks** (reverse review removed the "runtime perf" PR — see
note below); Tier 3 stays recorded, not near-term.

> **The priority ranking is the maintainer's call.** This doc supplies
> grep-verified evidence and a *suggested* ordering; it does not unilaterally
> declare what is or isn't worth the maintainer's time.

> **Reverse-review pass (2026-06-19).** After the first draft, every actionable
> claim was adversarially re-checked against source. Net change: **T1.2 and T1.3
> were downgraded from HIGH/MEDIUM to LOW.** The Tier-1 token anchor
> (`_threshold_token_estimate` + `record_api_usage(.., message_count)` at
> `_runner.py:268`) already keeps steady-state turns counting only the few new
> messages, so the slow char-loop is a cold-path cost (session start +
> post-compaction), **not** a per-turn one — there is **no measurable per-turn
> performance win** in this review. T2.5 (the ACP cwd bypass) was confirmed a real
> bug. All other findings stand.

---

## Baseline metrics

| Metric | Value | Reading |
|---|---|---|
| Source files / LOC | 262 / ~54.5k | — |
| Test files / LOC | 195 / ~54.8k | near-1:1 test ratio (healthy) |
| `TODO`/`FIXME`/`XXX`/`HACK` | 4 (3 false positives: `XXXX` provider placeholder) | effectively zero debt markers |
| Deprecation markers | 31 | live, documented back-compat shims (not rot) |
| Commits / last 90 days | 316 | actively developed |
| `import agentao` heavy modules | 0 | openai/mcp/httpx/jinja2/rich/bs4/tiktoken all `loaded=False` |

The takeaway: the usual cheap signals of neglect are absent. The findings below
are residual, not systemic.

---

## Tier 1 — Quick wins (low risk)

### T1.1 — Five packaging extras are dead weight (`google` clearly removable) · HIGH
`pyproject.toml:48-52`. Five extras have **zero import sites** anywhere in
shipped `agentao/` code (verified by grep for the actual import names —
`google.genai`, `Crypto`, `fitz`/`pdfplumber`, `pandas`/`openpyxl`, `PIL`):

```
pdf    = ["pymupdf>=1.27.1", "pdfplumber>=0.11.9"]   # 0 import sites
excel  = ["pandas>=2.0", "openpyxl>=3.1.5"]          # 0 import sites
image  = ["Pillow>=10.0.0"]                          # 0 import sites
crypto = ["pycryptodome>=3.23.0"]                    # 0 import sites
google = ["google-genai>=1.0.0"]                     # 0 import sites
```

- `google-genai` is **unambiguously removable**: the Gemini path is fully
  OpenAI-compatible — `llm/client.py:493-495` branches on `"googleapis.com" in
  base_url` and `model.startswith("gemini")` and never imports the SDK. The extra
  drags grpc/protobuf in for nothing. Drop it from extras and from `[full]`.
- `pdf` / `excel` / `image` / `crypto` are a **judgment call** (harness-vs-product
  boundary): they may be intentionally provided so *user skills* (which shell out
  / run Python) can use them. Today they have **no in-tree consumer and no stated
  purpose**. Decision: either remove them, or document in the developer guide that
  they exist as a convenience for skills. Right now they are undeclared-purpose
  dead weight that also inflates the `[full]` closure and the
  `tests/test_dependency_split.py` 122-package baseline.

**Fix:** remove `google`; decide remove-or-document for the other four; then
refresh `tests/data/full_extras_baseline.txt` (`uv build && uv run pytest
tests/test_dependency_split.py -m slow`).

### T1.2 — The token-count fallback is a pure-Python char loop — but the Tier-1 anchor keeps it off the steady-state hot path · LOW (downgraded from HIGH)
`context_manager.py:40-54` (`_heuristic_token_count` iterates `for ch in text`,
fast-pathing only a *single* string above `_FAST_PATH_CHARS = 100_000`). Because
`estimate_tokens` sums `_count_message_tokens` per message
(`context_manager.py:181-188`), a long **message list** of smaller strings does
fall through to the per-char loop — the mechanism is real.

> **Reverse-review correction (2026-06-19).** The first draft rated this HIGH
> ("per-turn, default installs"). That is wrong. The full-history estimate is
> **not** on the steady-state path: `_threshold_token_estimate`
> (`context_manager.py:124-152`) reuses the real `prompt_tokens` from the last API
> response (the Tier-1 anchor) and locally counts **only the messages appended
> since** (`messages[n:]`). The anchor is re-warmed after *every* LLM call —
> `_runner.py:268` passes `record_api_usage(prompt_tokens, len(messages_with_system))`
> with the message count, and it persists across turns. So each turn's threshold
> check counts a handful of new messages, **not** the whole history. The full
> O(chars) estimate runs only on the **cold path**: the first check of a session,
> and right after a compaction invalidates the anchor (`invalidate_token_anchor`).
> (The "Verified non-findings" section already stated the anchor keeps steady state
> cheap — the original HIGH rating contradicted it.)

- **Impact:** the ~13.5ms full-history estimate (subagent measurement; 400-msg /
  ~800KB, no tiktoken) is a cold-path cost incurred ~once per session + once per
  compaction event — **not per turn.** Steady-state warm cost is ~0.14ms.
- **Fix (optional, minor robustness):** extend the `_FAST_PATH_CHARS` shortcut to
  trigger on cumulative message/list length, so the *cold-path* and
  post-compaction estimates approximate in O(messages). Cheap and safe, but low
  value — the anchor already makes the common path fast. **Not** a reason to move
  `tiktoken` into core (that only enlarges the default install and adds
  model-mapping maintenance for no steady-state gain).

### T1.3 — Both compaction thresholds recompute the same estimate twice per iteration · LOW (downgraded from MEDIUM, cold-path only)
`needs_microcompaction` (`context_manager.py:231-237`) and `needs_compression`
(`:227-229`) each call `_threshold_token_estimate(messages)`, and both run every
loop iteration (`_maybe_microcompact` / `_maybe_full_compress`,
`_compaction.py:29,60`). The two calls are redundant — identical input, identical
result, mutually-exclusive ranges (55–65% vs >65%).

> **Reverse-review correction (2026-06-19).** Downgraded from MEDIUM. On the
> anchor-warm path (the steady state — see T1.2) each call only counts
> `messages[n:]`, so the duplication is ~0.14ms total and immaterial. The doubling
> is visible only on the rare cold path, where it turns one full estimate into two.

**Fix (minimal, trivial):** compute `_threshold_token_estimate(messages)` once per
loop iteration and feed the int into both predicates — a local in `run()`, or an
optional `tokens=` parameter on the two predicates. Do **not** introduce a
decision object / state machine: this is one redundant call, not a missing
abstraction.

### T1.4 — Four dead deprecated params on `ToolRunner.__init__` · MEDIUM (pure noise)
`tool_runner.py:60-64`. `confirmation_callback`, `step_callback`,
`output_callback`, `tool_complete_callback` are **declared but never stored or
referenced** (body lines 65-90 store none), and **no caller passes them** — the
sole production constructor (`agent.py:587`) and all five test constructors omit
them. Unlike the `Agentao.__init__` legacy callbacks (live shims that emit
`DeprecationWarning` and feed `build_compat_transport`), these are inert
signature noise with no warning, no storage, no use.

- They sit after a keyword-only `*` so they can only be passed by keyword, and
  grep shows none are. Safe to delete outright.

**Fix:** delete the four params and their type imports if now-unused.

### T1.5 — Misleading "remove in 0.5.0" comment over live replay API · LOW (prevents a future break)
`agent.py:963-1009` is headed *"back-compat shims (remove in 0.5.0)"*, but
`start_replay` / `end_replay` / `reload_replay_config` are **live, actively-called
API**: `cli/session.py:29-30,46`, `cli/commands/sessions.py:144-146`,
`cli/replay_commands.py:194`, ACP `session_new.py:415` / `session_load.py:342`.
Only the `_replay_recorder` / `_replay_adapter` / `_host_replay_sink` *property
views* are genuinely removable test-facing shims. The blanket "remove in 0.5.0"
framing will mislead a future cleanup into breaking the CLI/ACP.

**Fix:** regroup the comment so the three `*_replay()` delegation methods are
marked as the supported surface, and only the three private property views carry
the removal note. (Comment/structure fix — do **not** remove the methods.)

---

## Tier 2 — Two latent bugs + opportunistic consolidation

**Do now (single-point bug fixes — fix the bug, not the abstraction):** T2.3 and
T2.5 each correct a real cwd / working-directory defect. Scope each PR to the fix.

**Opportunistic only (T2.1 / T2.2 / T2.4 / T2.6):** these consolidate against
helpers that already exist (or are one-liners). They are maintainability
nice-to-haves, not perf/correctness issues — fold them in *when you are next
editing that file anyway*. Do **not** manufacture new public/shared API solely to
delete duplication; that is what turns an optimization list into a refactoring
roadmap.

### T2.1 — Three ACP handlers still inline the session gate that `require_active_session()` centralizes · MEDIUM (opportunistic)
`_handler_utils.py:52-81` is the purpose-built helper; its own docstring
(`:10-12`) names the laggards. Five newer handlers adopt it
(`session_set_mode`, `session_set_model`, `session_list_models`,
`session_set_config_option`, `agentao_set_model` — confirmed). Still
hand-inlining the same 4-step gate: `session_cancel.py:112-129`,
`session_prompt.py:235-253`, `session_load.py:157-166`. The verbatim
`_parse_session_id` (cancel `:82-84`, prompt `:100-101`, load `:109-111`) is the
sessionId clause of that same helper.

- `session_cancel` / `session_prompt` are a safe drop-in onto the **existing**
  helper — do it when next editing ACP handlers. `session_load` uses
  get-not-require (it creates), so it would need a *new* thinner helper variant;
  leave it unless that file is already being changed (don't add API just to dedupe).

### T2.2 — The `LLM_PROVIDER` + `{PREFIX}_API_KEY/_BASE_URL/_MODEL` scheme is re-implemented in 4 modules, bypassing `discover_llm_kwargs()` · MEDIUM (opportunistic)
`factory.py:81-88` is the canonical reader; its docstring (`:77-79`) explicitly
asks peers/tests to call it rather than re-implement the prefix scheme.
Re-implemented inline at `cli/diagnostics/collectors.py:47-50`,
`acp/session_set_config_option.py:86,93-100` (its own docstring admits it
*"Mirrors factory.discover_llm_kwargs"*), `cli/app.py:57`, and
`cli/commands/provider.py:25,51,57,64`. The default `"OPENAI"` literal and the
`.strip().upper()` casing now live in 5 places.

- **Fix (opportunistic):** if/when these modules are touched, export
  `resolve_provider_env()` from factory and route the three value-extraction sites
  through it. A small new export, low value on its own — not a scheduled task.
  (`provider._list_providers_from_env`, which *scans all* `*_API_KEY` keys, is a
  legitimately different shape — leave it.)

### T2.3 — `cli/app.py` reads *and writes* `.agentao/settings.json` cwd-relative instead of the resolved project root · MEDIUM (bug — do now) + optional follow-up
The actionable item is a **bug that spans both sides of the settings round-trip**:
`cli/app.py:140` (`_load_settings`) **and** `cli/app.py:149` (`_save_settings`)
both use **cwd-relative** `Path(".agentao") / "settings.json"` instead of the
resolved project root. `factory.py:37-45` and `replay/config.py:107-115` read the
*same file* from the resolved root. So a CLI launched from a subdirectory
persists `mode` to a cwd-local `.agentao/settings.json` (it even `mkdir`s one)
that the factory never reads — **read and write both disagree** with the factory's
frozen `working_directory` contract.

- **Fix (single-point):** make **both** `_load_settings()` and `_save_settings()`
  use the resolved project root — ideally the existing
  `replay/config.py::settings_path()`. Fixing only the read leaves the write path
  inconsistent and the bug open. Scope the PR to these two methods; do **not**
  bundle a refactor.
- **Optional follow-up — separate, de-scoped, do NOT merge into the bug PR:** the
  read → `json.loads` → `isinstance dict` → swallow idiom is copy-pasted across
  `mcp/config.py:61-68`, `embedding/plugins/manager.py:422-430`,
  `agents/store.py:23-32`, `skills/registry.py:54-61`,
  `embedding/plugins/manifest.py:77-83`, `skills/manager.py:143-149`. A shared
  `read_json_object(path)` *could* collapse these, but that is a horizontal
  consolidation worth doing only if/when these files are touched anyway —
  recorded here, **not scheduled**.

### T2.4 — CLI subcommand dispatch preamble + "Unknown subcommand" footer copy-pasted 5+ times · LOW/MEDIUM (opportunistic only)
This is **display-consistency / minor drift**, not perf or correctness — rated
LOW/MEDIUM and explicitly **not** a standalone task. The `args.strip()` →
`split(None, 1)` → `sub`/`rest` preamble (`commands/mcp.py:17-20`,
`commands/sessions.py:31-34`, `commands/permission.py:38-40`,
`commands_ext/acp.py:35-38`, `commands_ext/agents.py:121-123`) has already drifted
(`permission.py:39` added `.lower()`; `mcp.py:20` dropped the remainder
`.strip()`); the `Unknown subcommand:` footer is duplicated at
`commands/mcp.py:94`, `commands/sessions.py:86`, `commands_ext/acp.py:73`,
`commands_ext/memory.py:196` (`permission.py:131` drifted).

- **Fix (opportunistic):** *when next touching these command handlers*, factor a
  small `split_subcommand()` / `unknown_subcommand()` helper into `_globals.py`.
  Don't manufacture a public CLI helper API solely to delete duplication — the
  drift here is cosmetic.

### T2.5 — `CodebaseInvestigatorTool` bypasses the base path resolver — a latent ACP cwd bug · MEDIUM (bug — do now)
The `resolve → exists() → "Directory ... does not exist"` guard is copy-pasted at
`search.py:158-161`, `search.py:352-355`, `file_ops.py:445-448`,
`agents.py:88-90`. The first three use the base resolver
(`_resolve_path`/`_resolve_directory`, `base.py:61/82`); `agents.py:89` uses raw
`Path(directory).expanduser()`, so it **ignores the session `working_directory`
binding** that `base.py:78-80` exists to enforce (the ACP per-session-cwd guard).

- **Fix (single-point, fixes the bug):** route `agents.py` through the
  **existing** base resolver (`_resolve_directory`, `base.py:82`) instead of raw
  `Path(...).expanduser()`. That alone closes the cwd bypass *and* drops the
  duplicated guard — no new helper required. (A shared
  `_resolve_existing_directory()` would also dedupe the message across the four
  sites, but that's the opportunistic part, not the bug fix.)

### T2.6 — `tools/search.py` Python fallback re-implements its own `_format_grep_output` · LOW (opportunistic, trivial)
The helper at `search.py:87-101` is used by the two fast paths; the slow Python
fallback re-hand-rolls the identical "No matches / Found N match(es) / cap-100 + '…
and X more'" logic at `search.py:425-433` (same file, byte-identical contract).

- **Fix (opportunistic, trivial):** route the fallback through the
  `_format_grep_output(...)` helper that already lives in the same file — no new
  API, single file. Fold in next time `search.py` is open.

---

## Tier 3 — Larger refactors (real but costly; opt-in)

### T3.1 — `ToolExecutor._execute_one` is a 249-line method mixing 6 concerns · HIGH maintenance cost
`tool_executor.py:177-426`. One method handles TOOL_START emit, DENY branch,
CANCELLED branch, token propagation, host `started` emit, pre-exec cancel check,
sandbox-profile injection, the `output_callback`-wired execute/try/except/finally,
async-cancel short-circuit, host terminal emit (4-way redaction), and post-tool
hook dispatch — with 5 `return call_id, ToolExecutionResult(...)` exits plus the
async short-circuit. The short-circuit guards (DENY/CANCELLED/pre-cancel) extract
cleanly into `_short_circuit_result(...)`; the sandbox resolution (`:290-304`) and
host-terminal-emit block (`:377-409`) are self-contained. Splitting drops the core
path to ~120 lines.

- **Constraint:** the TOOL_START/TOOL_COMPLETE pairing, the "host `started` fires
  only on ALLOW" ordering, and the single-emit-per-`call_id` guarantee are
  load-bearing for ACP/replay/CLI. Any extraction must preserve emit ordering.

### T3.2 — `Agentao.get_conversation_summary` is a 64-line presentation method in core · LOW/MEDIUM
`agent.py:1188-1250`. Pure string-formatting for a CLI/status display that reaches
into `context_manager`, `memory_manager`, `skill_manager`, `todo_tool`,
`mcp_manager`, and `llm` only to format text. A display concern that fits
`runtime/summarize(agent)` (mirroring the established `run_turn(agent, …)` /
`run_llm_call(agent, …)` facade pattern) or the CLI layer.

- **Constraint:** public method — keep a thin `agent.get_conversation_summary()`
  facade delegating to the extracted function (same pattern as `_build_system_prompt`).
- Respects `core-boundary-review.md`: display logic should not live in core.

### T3.3 — `_call_llm_with_overflow_recovery` — 110 lines, 3-deep nested try/except, 7 near-identical returns · MEDIUM
`_runner.py:697-806`. Image-fallback → context-overflow `full` compress →
`minimal_history` is a linear escalation expressed as rightward-drifting nesting,
with 3 textually-identical success constructions and 3 near-identical error
constructions. Reads more naturally as a short sequence of `_attempt(...)` steps.

- **Constraint:** escalation order, `is_context_too_long_error` gating, and the
  `_emit_context_compressed` / `_emit_session_summary_if_new` side-effect ordering
  are behavioral. Mechanical refactor, but must keep existing tests green.

---

## Verified non-findings (checked and cleared — do not "fix")

Recorded so a future pass doesn't re-flag intentional design (this repo's culture
is *gap ≠ need*):

- **`import agentao` is clean.** openai SDK, mcp, jieba, tiktoken, bs4, jinja2 are
  all lazy / deferred; `from agentao import Agentao` loads zero heavy modules.
- **AGENTAO.md is not re-read per turn** — loaded once at construction
  (`agent.py:504`); the builder uses cached `agent.project_instructions`.
- **Memory recall is not O(n²)** — inverted index + `write_version` dirty-gate
  (`retriever.py:300-319`); token bundles cached.
- **Context manager does not re-tokenize the whole history every turn** — Tier-1
  API anchor reuses real `prompt_tokens` (`context_manager.py:124-152`).
- **`requires-python = ">=3.10"` is honored** — no 3.11+ features (`tomllib`,
  `ExceptionGroup`, `Self`, `StrEnum`, `TaskGroup`, `datetime.UTC` all absent).
- **No imported-but-undeclared deps.** Every import resolves to a declared dep
  (`pygments` has 0 import sites but is an intentional presence-probe in
  `cli/__init__.py:81`).
- **`Agentao.__init__` (232 lines)** is already decomposed into ~9
  `_init_*`/`_resolve_*`/`_validate_*` helpers with documented ordering — further
  splitting adds indirection without reducing real complexity.
- **`chat_loop/` mixins, `harness→host` alias, legacy `Agentao` callbacks** are
  intentional documented shims, not debt.
- **`write_session_update` envelope centralization holds** — all three
  session/update emit sites route through it (commit `c6d6406` intact).
- **`subprocess.run` direct calls** remaining (`cli/input_loop.py` clipboard,
  `sandbox/policy.py` preflight) are short trivial commands with no captured-pipe
  grandchild risk — correctly out of scope for `run_captured`.
- **One `tiktoken` consideration only**: see T1.2 — that's the placement, not an
  import bug.

---

## Recommended sequence

Reverse review removed the "runtime hot-path perf PR" that headlined the first
draft (T1.2/T1.3 are cold-path-only — see the reverse-review note). What remains:

1. **PR 1 — low-risk cleanup (the clear win).** T1.1 `google` extra removal (plus
   the pdf/excel/image/crypto remove-or-document decision), T1.4 dead `ToolRunner`
   params, T1.5 replay comment. No runtime behavior change; refresh
   `full_extras_baseline.txt` for the extras.
2. **Tier 2 — single-point bug fixes.** T2.3 (settings.json read+write cwd bug, no
   helper) and T2.5 (ACP cwd bypass, via the existing resolver). Both close real
   defects. The consolidation items (T2.1 / T2.2 / T2.4 / T2.6) are
   **opportunistic** — fold them in only when those files are touched for other
   reasons. Do not platform-ize to remove duplication.
3. **Optional, low value — cold-path token tweaks.** T1.2 / T1.3 help only the
   cold / post-compaction path; do them as a tiny stand-alone change if at all,
   never as a "perf" PR, and leave `tiktoken` packaging as-is.
4. **Tier 3 — recorded, not near-term.** T3.1 (`_execute_one`) carries the only
   real ongoing maintenance cost but also the most risk (ACP emit-ordering
   contract); keep it behind its existing tests if/when it's done. T3.2 / T3.3 are
   nice-to-have.

The pdf/excel/image/crypto remove-or-document question is a harness-vs-product
boundary call that belongs to the maintainer.

---

## Appendix — how each finding was verified

| Finding | Verification |
|---|---|
| T1.1 | `grep -rE "from google\|import Crypto\|import fitz\|pdfplumber\|import pandas\|import openpyxl\|from PIL" agentao/` → 0 hits; `llm/client.py:493-495` Gemini-over-OpenAI path read |
| T1.2 / T1.3 | read `context_manager.py` (`_heuristic_token_count`, `_threshold_token_estimate`, anchor) + `_compaction.py`; **reverse review:** confirmed `_runner.py:268` warms the anchor with `message_count`, so the slow loop is cold-path-only → both downgraded to LOW |
| T1.4 | read `tool_runner.py:60-90`; `grep "ToolRunner("` across `agentao/` + `tests/` — no caller passes the four params |
| T1.5 | `grep "start_replay\|end_replay\|reload_replay_config"` → live CLI + ACP callers |
| T2.1 | `grep "require_active_session" agentao/acp/` — 5 adopters, 3 laggards |
| T2.2 | `grep "LLM_PROVIDER\|discover_llm_kwargs"`; read each re-impl site |
| T2.3 | read `app.py` `_load_settings` **and** `_save_settings` — both cwd-relative read/write; compared against `factory.py` / `replay/config.py` resolved-root loads |
| T2.4–T2.6 | read each cited snippet pair; confirmed drift / single-file dup |
| Non-findings | `-X importtime` for lazy-import claim; read anchor/inverted-index code; `grep` for 3.11+ features |

---

## Implementation status (2026-06-19)

All Tier-1 and Tier-2 findings were implemented in a single pass (Tier-3 left
recorded, not done). The default test suite is green. Two findings were refined
against source during implementation — recorded here so the proposals above are
read with these corrections:

- **T1.1** — all five extras (`pdf` / `excel` / `image` / `crypto` / `google`)
  removed from `[project.optional-dependencies]` and `[full]`.
  `full_extras_baseline.txt` was regenerated from a fresh `[full]` wheel install
  (122 → 106 packages; the 16 dropped are the removed extras' unique transitive
  closure — note `Pillow` *stayed*, since `crawl4ai` pulls it transitively, which
  confirms the `image` extra was redundant). `uv.lock` updated to match.
- **T1.2 — implementation differs from the proposal (and is better).** Instead of
  the suggested cumulative `len/4` shortcut (which would *under-count* CJK and
  delay compaction), `_heuristic_token_count` was **vectorized**: the ASCII/CJK
  split is computed with `len(text.encode("ascii", "ignore"))` (C-speed) instead
  of a Python per-character loop. Same `ASCII×0.25 + CJK×1.3` formula, same result
  on the pinned tests, no accuracy regression — it just removes the O(chars) loop.
- **T1.3 / T1.5 / T2.2 / T2.3 / T2.5 / T2.6** — landed as described. T1.3
  shares one `_threshold_token_estimate` per loop iteration across both compaction
  predicates via an optional `tokens=` kwarg; the fire/no-op decision is provably
  identical (the ranges are mutually exclusive and microcompaction only lowers the
  count).
- **T1.4 — reverted.** A Codex review flagged that deleting `ToolRunner`'s four
  deprecated callback kwargs ends their backward-compat window early: an external
  host/test still constructing `ToolRunner` directly with them would hit
  `TypeError`. They are restored as accepted-but-ignored no-ops, scheduled for
  removal in 0.5.0 alongside the matching `Agentao.__init__` legacy callbacks
  (same compat policy), not before. Net: no change to `tool_runner.py`.
- **T2.1 — the "safe drop-in" claim held only for `session_cancel`.** On close
  reading, `session_prompt` is **not** behavior-preserving against
  `require_active_session` (it parses the prompt *before* the session lookup, and
  maps an agent-less session to `INTERNAL_ERROR`, not `INVALID_REQUEST`), and
  `session_load` uses get-not-require. So a thin `resolve_session()` (envelope +
  lookup, no liveness check) was added to `_handler_utils.py`;
  `require_active_session()` now composes it, and **only** `session_cancel` was
  routed through it (error codes/messages verified byte-identical). The other two
  were intentionally left to preserve their wire contract.
- **T2.4** — `split_subcommand()` / `unknown_subcommand()` added to `_globals.py`;
  the five dispatch preambles and the four byte-identical footers route through
  them. Keyword flags (`default` / `lower` / `strip_rest`) preserve each handler's
  pre-existing drift; `/sandbox`'s prefixed footer kept its own message.

### Code-review follow-up (2026-06-19)

A workflow-backed adversarial code review of the diff surfaced (and these were
then fixed) three behavior regressions the first consolidation pass introduced:

- **T2.6 corrected.** Routing the pure-Python search fallback through
  `_format_grep_output` was **not** the no-op claimed: its `.splitlines()` re-split
  matched lines on embedded Unicode line separators (U+2028/U+2029/VT/FF/NEL),
  and its skip pass re-filtered on a `:`-truncated path (dropping a legitimate
  `build:notes.txt` match). Fixed by extracting `_format_match_lines(lines,
  pattern)` (the "No matches / Found N / cap-100" contract on a *list*); the
  fallback now calls it directly (no re-split, no re-filter) and `_format_grep_output`
  composes it for the git-grep / rg fast paths.
- **T2.2 narrowed.** `resolve_provider_name()` (which upper-cases) is kept for
  `factory` / `collectors` / `app` (all already `.strip().upper()` — exact match),
  but `acp/session_set_config_option.py` was **reverted** to read `LLM_PROVIDER`
  directly: its accept/reject comparison needs the raw casefold, and round-tripping
  through `.upper().lower()` would wrongly reject non-ASCII provider names with
  non-idempotent case (`ß` / `ı` / ligatures).
- **T1.2 made exact.** The vectorized count now uses integer arithmetic
  (`(ascii×25 + cjk×130) // 100`) rather than `int(ascii×0.25 + cjk×1.3)`, which
  float-accumulation drift made +1 too high on non-ASCII text.

Intended-and-correct changes the review also flagged but that were **kept**: the
T2.5 `agents.py` directory-resolution switch (the bug fix itself), and the T1.3
shared per-iteration estimate (proven decision-identical; the invariant is
commented at the call site).
