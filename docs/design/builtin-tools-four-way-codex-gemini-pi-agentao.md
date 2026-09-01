# Built-in tools — a four-way comparison: codex · gemini-cli · pi-mono · agentao

> **⚠️ Analysis only. Nothing here is authorized for implementation.** §1 is a **priority ordering
> of findings**, not a work schedule. Quote this line whenever you quote the table.

**Status:** analysis, **rev 16** (2026-09-01).
**Anchors:** codex `openai/codex@b7cd519c76` (2026-08-31); gemini-cli
`google-gemini/gemini-cli@0bd1d4397` (2026-08-28); pi-mono `@853a80d26` (2026-08-28); agentao
`main@afda2ea` (2026-08-31). All four read from a local worktree at the pinned commit — no vendor
documentation was used as a primary source for any of them. Commit dates are carried so the review
date and the anchors can be checked against each other; rev 1 dated itself 2026-08-30 against two
anchors committed on the 31st, which is impossible.
**Method:** every claim carries an inline `file:line` at its own repo's anchor. Tool *names* are
resolved to their string literals, not to the constant that holds them, because three of the four
repos indirect through a constant and one of those constants has already drifted (§7).
**Scope:** which tools each harness ships **in-tree and model-visible**, and what decides whether a
given tool reaches the model on a given turn. Out of scope: tool *implementation* quality, MCP
transport, and permission-rule syntax — those have their own docs.
**Twin:** `builtin-tools-four-way-codex-gemini-pi-agentao.zh.md`.
**Related:** `host-tool-allowlist.md`, `host-tool-injection.md`,
`hooks-three-way-claude-codex-agentao.md` (same four-way method, different contract),
`codex-subagent-v2-vs-agentao.zh.md`, `path-a-roadmap.md`.

### Revision history

| rev | Found | Headline |
|---|---|---|
| 16 | 3 (P3) | **Quote containment, made mechanical — and it caught one I had passed by eye.** Content-checking §0, §1, §6, §9 and §10 closes the document: §0 cites nothing, and the rest reuse citations already verified, so the only new claim was `core/src/config/models.ts` — **verbatim**, regex at `core/src/config/models.ts:461` and the comment *"This is legacy behavior"* at `core/src/config/models.ts:459-460`. Which is the defect: three citations named a line that did not contain the text quoted beside it. `core/src/config/models.ts:458` → `:458-461` at the two sites that quote (the two that only name `isGemini2Model` correctly stay `:458`); `core/src/tools/handlers/apply_patch.rs:73` → `:73-74`, a doc comment running onto the second line — **rev 13 read this one by eye and passed it**, because the quote does start on the cited line; and `core/src/agents/cli-help-agent.ts:88` → `:89`, where §2 already cited `:89` for the same fact and §10 pointed one line up at the enclosing `toolConfig:` key. The rule is now in §10 with its script, and with its two known limits: strip leading comment markers before joining, and expect one standing false positive, since the document's own italic phrasing is shaped exactly like a source excerpt. |
| 15 | 3 (P3) | **"Unique in its repo" was too weak, and two range citations did not contain the text they quoted.** Content-checking §2 and §7 — 58 citations, **every substantive claim substantiated**, including four negative claims verified by repo-wide grep at the anchors (neither agent-tool class is instantiated anywhere; `cli_help` has no definition file; both background tools are absent from `ALL_BUILTIN_TOOL_NAMES`; the `MemoryTool` identifier is gone entirely, which is stronger than §7 says). The three defects: `tooling/registry.py:44-46` quoted a sentence that ends on `:47`, so the cited range did not contain its own quotation; `agentao/tools/__init__.py:10,32` said "both are exported" while the two `__all__` entries are `:31` and `:32`; and **13 citations were shorter than their repo's conventional root**, ten of them under-qualified. All ten are normalised. The rule in §10 is now a **floor**, not an equality — over-qualifying is allowed, and for `packages/coding-agent/src/extensions/index.ts` it is required. |
| 14 | 18 (P3) | **The §10 rule the document has cited since rev 2, never applied to the document itself.** rev 13 fixed two bare-basename citations one at a time; a sweep of the distinct filenames cited found **23**, of which five are deliberate (§10's three counter-examples and rev 13's two historical quotes) and **18 were live**. All 18 now carry a path qualified far enough to be unique in its own repo, 37 occurrences per twin, normalised to each repo's existing root — `codex-rs/` for codex, `packages/` for gemini-cli, `packages/coding-agent/src/` for pi-mono, the package root for agentao (`agent.py` and `context_manager.py` are already that path and are unchanged). Two of the 18 were live risks rather than style: `scheduler.ts` in a repo that also has `agents/agent-scheduler.ts`, and `local-executor.ts` reached only through §8's withdrawn sub-agent lead. **Method:** a bare basename unique in its repo *resolves*, so no mechanical pass rev 3–13 ran could see these — the rule needs a query of its own, not a stricter resolver. Counts unchanged (188 citations, 184 resolving, identical sequence across twins): qualifying a path neither adds nor removes a citation. |
| 13 | 3 (P3) | **The first pass to read the cited lines rather than resolve them, and both defects were the doc's own rule biting it.** rev 12 verified the 186 citations *mechanically* — the address exists — which cannot see a citation that resolves to the wrong thing. Reading the lines behind §3 and §4's 26 citations found every substantive claim substantiated and all four "quoted" strings verbatim, plus two precision defects: `agent-loop.ts:617-624` was a **bare basename** — §10's own prohibition, violated by a live citation rather than one of the three quoted as counter-examples — and it crosses the `packages/agent` ↔ `packages/coding-agent` boundary §2.4 says must not be conflated, while the adjacent citation uses a path; its range also brackets the *call site*, not `:634`/`:653` where "executes unchanged" is actually established. `model_info.rs:142` was the doc comment; the function is `:143` and the quoted warning `:144`. **Method note:** the resolver used for the rev-10–12 count was *looser* than the claim it checked — suffix-matching across all four repos, first hit wins — so it reported **184/186**, over the asserted 182, by "resolving" two of §10's own bad-citation examples. A checker that admits more inputs than the claim allows can only agree; a count coming out **better** than the claim is the tell. Only a tiered matcher (exact → repo-scoped → suffix, ambiguity counted as unresolved) reproduced 182/3/1. **Third finding, from the cell-count check rev 12 added:** rev 7's row rendered as **five** columns in both twins — `` `/^gemini-2(\.\|$)/.test(model)` `` carries an unescaped `\|`, and GFM code spans do not protect it inside a table. That is 4d's exact defect surviving the revision that wrote 4d, in a string the same document renders correctly in body prose two hundred lines later, where escaping would be wrong. Escaped in the table rows only. |
| 12 | 2 (P3) | **The same book-keeping slip twice: a correction folded into the row it corrected.** rev 11's fix was written into rev 10's cell instead of getting its own row, so the revision table under-reported the review count for the second time — the rule this document set for itself is that every review round gets a row, and a round that only edits prose is still a round. Also a unit error carried by rev 11: the fenced-block citations are **three tokens on two lines**, not "three lines" — two sit on the same line of §10's re-derivation recipe. |
| 11 | 1 (P3) | **The explanation of the miscount was itself miscounted.** rev 10 attributed the 180→186 gap to "three fenced plus one bare", which is four, not six. Re-derived **positionally** — mark every `path:line` token, subtract those fully covered by the old regex's matches — the six are: **three** citations written as `` `path:line :: symbol` `` (the closing backtick is not adjacent to the digits, so the old whole-span pattern skipped them) and **three** unbackticked tokens inside §10's fenced block. The failure was the same shape as the miscount it explained: the categories were **inferred from the gap size** rather than counted, and a missing fourth was invented to reach a number that was wrong anyway. Only the next review asking "where did the other two go" forced the arithmetic. |
| 10 | 1 (P3) | **A self-referential count was wrong for nine revisions, because the checker that produced it disagreed with the claim it was checking.** The README said 180 `path:line` citations; the real figure is **186**. The verification script's pattern required a citation to be the **entire** backtick span, so it under-counted. Every round it reported low, and every round that number was copied forward unexamined. Fixed by stating the **counting rule** alongside the number (every `path:line` token in the file, backticked or not, fenced block included) and by splitting the total: 182 resolve to a unique non-blank line, and the remaining four are deliberate — rev 1's three *bad* citations quoted verbatim inside §10's basename method note, plus the `<repo-root>/pyproject.toml` placeholder. The lesson generalises past this document: **a metric about a document is not exempt from that document's own review discipline**, and a checker's silence is only as wide as its pattern. |
| 9 | 1 (P2), **+1 peer defect found while verifying it** | **rev 8 fixed two projection cells and broke the third.** It claimed gemini-cli "re-filters on every schema build, so a `registerTool` lands as soon as the next request is assembled". It does not: `GeminiChat` caches its tool list, `registerTool` writes only `allKnownTools` and invalidates nothing (`core/src/tools/tool-registry.ts:271`), and although `core/src/core/client.ts:801` calls `setTools(modelToUse)` per request, `setTools` **returns early when the model is unchanged** (`core/src/core/client.ts:311-313`). The refresh points are `startChat`, an explicit **no-arg** `setTools()` (which bypasses the guard — that is why the `PLAN`/`YOLO` transition works), and a model switch. Verifying that surfaced **Finding 6**: `reloadSkills()` re-registers `ActivateSkillTool` with a fresh enum and calls only `updateSystemInstructionIfInitialized()` (`core/src/config/config.ts:3693-3699`), never `setTools()` — so a runtime skill reload leaves the model on a **stale schema**. agentao cannot reach that state; it re-projects at the top of every `chat()`. |
| 8 | 1 (P2) | **The four-stage table's last column answered the wrong question for half the row set.** "schema projected" is a *when*, and rev 7 filled pi-mono's and agentao's cells with *what* — "the active set, unfiltered" and "plan-only tools withheld". The timings are the interesting part and they differ materially: gemini-cli re-filters on **every** schema build, so a `registerTool` lands as soon as the next request is assembled; pi-mono defers to the **next agent turn** (`setActiveToolsByName`'s own contract, `core/agent-session.ts:965-971`); and agentao is strictest — `to_openai_format(...)` runs once at `runtime/chat_loop/_runner.py:348`, **above** the inner tool-call loop, so a mid-turn `add_tool` stays invisible for the rest of the turn no matter how many iterations it runs, exactly as `add_tool`'s docstring states (`agent.py:906-914`). This column is what decides when a post-build registry mutation reaches the model, which is the whole point of separating it from the mutation column. |
| 7 | 3 (1 P1, 2 P2) | **"The cost is a per-model catalogue" was itself an unproven constraint on the highest-priority finding.** rev 5–6 wrote that both peers maintain a per-model table; codex does (mixed with `provider.capabilities()`, and with `view_image` using neither), but **gemini-cli's is a regex** — `/^gemini-2(\.\|$)/.test(model)` (`core/src/config/models.ts:458-461`), its own comment calling it *"legacy behavior"*. The shape is not forced, so Finding 1 and §9 now say the cost is **an owned, continuously maintained compatibility fact of some shape**, leaving a regex or per-provider flag on the table. Then two propagation failures: rev 6 split §4 into four stages but §0 still announced **three** "each on a different one", contradicting §4's own "the columns are not a partition"; and §10's entry points were wrong in three places — pi-mono's `allToolNames` is a name set not a build step (the entry is `_buildRuntime`, which `reload()` **re-runs**, `core/agent-session.ts:2820`, so the table's "once" was wrong too), and agentao's full entry is `agent.py::_wire_tooling` (`:578`), not `register_builtin_tools`, which is one call inside it. Finally the rev-6 claim that `max_tokens` is "forwarded on every request" was too wide: `chat()`/`chat_stream()` default it to `None` (`llm/client.py:430,534`) and the kwarg is added only `if max_tokens` (`:419`) — the **main agent path** forwards it (`runtime/llm_call.py:138`), the compaction summariser does not (`context_manager.py:1573`), so the 65536 hazard is scoped to that path *and* to endpoints that clamp silently. |
| 6 | 3 (1 P1, 1 P2, 1 P3) | **rev 5's defence of the precedent was wrong in a new way, so the whole claim is now sourced rather than argued.** rev 5 said agentao "has no equivalent" of pi-mono's `maxTokens` and offered `grep -r context_window agentao/` = 0 — **the wrong field**: `maxTokens` is a requested *output* cap and agentao has `LLMClient.max_tokens` (`llm/client.py:139,188,419-421`), ACP-mapped (`acp/session_set_model.py:10`). The real difference is the **default** — pi falls back to a per-model registry value (`packages/ai/src/api/simple-options.ts:34`), agentao to a flat `65536` — so the borrow is portable when the host sets it per model and unsafe under the shipped default, which is a *defaulting* problem, not a catalogue one. rev 5 also called `supportsFinishReason` unrelated to catalogues; it is configurable at **provider and model levels** (`packages/coding-agent/test/model-registry.test.ts:771-778`) — what was non-catalogue was agentao's *reason* for inverting it. Net: **no precedent here rules on the catalogue question**, and §9's "cannot maintain provider-neutrally" is withdrawn as unsupported. Also: the three-stage table put registry *mutation* in the active-set column and then contradicted its own "no two harnesses vary on the same stage" — now **four** columns (initial build / registry mutated after / active selection / schema projected), with three of four mutating the registry post-build; and §10's "exactly one entry point" narrowed to *initial* construction. |
| 5 | 4 (1 P1, 3 P2), **1 partly disputed** | **The P1 is a citation to a document that does not exist**, and it was load-bearing: rev 4 justified §4/§9's catalogue verdict with "`isRecoverableLength` self-refuted in `pi-mono-pull-review-2026-08-09`". `docs/design/` has `-2026-08` and `-2026-08-21` and **no `-08-09`** — that review is a project record, not a design doc, so the citation is withdrawn. **The substance is not**, and the review's second half is disputed: `isRecoverableLength`'s *body* (`packages/ai/src/utils/overflow.ts:171`) carries no per-model data, but its **call site** passes `this.model?.maxTokens ?? 0` (`core/agent-session.ts:2156`) against a required model-type field (`packages/ai/src/types.ts:836`), and `grep -r context_window agentao/` returns **0**. The dependency is real; reading the signature and stopping is §10's second method note in a new place. What the passage now says instead: one prior borrow *was* declined on catalogue grounds, but it was about a single predicate — the general question has never been put. `supportsFinishReason` is a separate item, inverted because `INCOMPLETE_ANSWER_REASONS` values become CLI error envelopes (`docs/design/pi-mono-pull-review-2026-08.md:58`). Then: `enabled_tools` does **not** accept MCP names — the reserved-name guard rejects `mcp_` before the live-registry check (`agent.py:449-452`, `tests/test_host_tool_allowlist.py:138`); §9 still called the pi-mono/codex agreement "about context cost" after §5 had downgraded that to inference; and §4's "per turn or once per session" axis was still collapsing **three** stages that no two harnesses vary on together — it is now a table over *registry built* / *active set mutated* / *schema projected*, and only codex rebuilds per turn. |
| 4 | 5 (2 P1, 2 P2, 1 P3) | **Two numbers and one provenance claim were wrong, and one lead merged two mechanisms.** (a) gemini-cli's 19–20 was the **registration** count; `getFunctionDeclarations()` re-filters on every build (`core/src/tools/tool-registry.ts:601-624`) — no-MCP hides both resource tools, and `enter_`/`exit_plan_mode` are mutually exclusive by mode — so a bare session shows **16–17**. That also kills rev 3's "constant set for the session" (a mode transition calls `setTools()`, `core/src/config/config.ts:2810-2819`) and §8's "always-visible, policed at execution". (b) codex's `ModelInfo` is **not model-self-declared**: it is a harness/backend-maintained catalog matched by **slug prefix** (`models-manager/src/manager.rs:617-631`) with a warned fallback for unknown slugs (`models-manager/src/model_info.rs:143-144`), so the §9 objection is **owning a per-model catalogue**, not "providers don't send it" — *narrowed in rev 7 to "some maintained compatibility fact", since gemini-cli's is a regex*. (c) "explicit context-cost bet" was motive attribution — source shows only that pi-mono withholds the three tools and substitutes shell guidance (`core/system-prompt.ts:99-111`); now marked **inference, unmeasured**. (d) `enabled_tools` and `disable_tools` use **different** guards — live registry ∪ constant (`tooling/registry.py:195-205`) vs the static constant alone (`agent.py:466-472`). (e) §8 still said "the three below survive" after dropping to two. |
| 3 | 5 (2 P1, 3 P2) | **rev 2's own corrections were themselves too wide, three times.** (a) codex does **not** have zero *read* tools either — `view_image` takes a local path, resolves it against the environment cwd and reads it through the sandbox filesystem (`core/src/tools/handlers/view_image_spec.rs:19`, `core/src/tools/handlers/view_image.rs:150-175`), Stable and default-on (`features/src/lib.rs:889-893`). The column is now explicitly *general text/source* reads, and the surviving claim is "no general reader", not "no reader". It is also a **counter-example to §4**: `view_image` is registered without consulting `input_modalities` (`core/src/tools/spec_plan.rs:1259`) and refuses at execution instead (`core/src/tools/handlers/view_image.rs:97-105`), so codex is a mixed strategy, not a clean capability gate. (b) pi-mono has **no per-tool permission boundary at all** by default — `beforeToolCall` returns `undefined` with no handler registered (`core/agent-session.ts:489-492`) and the call executes (`packages/agent/src/agent-loop.ts:634,653`); the gate is an *example* extension. (c) On `activate_skill`, rev 2 said gemini-cli "agrees with agentao" **and** that ask is a third position — contradictory, and the ask rule is `interactive = true` (`core/src/policy/policies/plan.toml:110`), so non-interactive falls to the catch-all DENY (`:76-80`). Plus: §8's sub-agent-binding lead **withdrawn** (gemini-cli inherits the parent registry and shallow-clones, `core/src/agents/local-executor.ts:190-200` / `core/src/tools/tools.ts:480`), and its plan-mode lead **restated** — §9 claimed agentao keeps plan mode out of the tool surface, contradicting §2.1's own `plan_save` / `plan_finalize`; the real 1/4 is *mode entry/exit* as a model tool. §8 is now **two** leads. |
| 2 | 8 (3 P1, 4 P2, 1 P3) | **Three reversals in table cells.** (a) codex does **not** have "0 file tools" — `apply_patch` is a model-visible workspace-write tool that derives write permission **per target path** (`core/src/tools/handlers/apply_patch.rs:73,236-270`), so the "permission unit *cannot* be the tool" inference and the "3:1 majority" framing are both withdrawn; the real divergence is the **read** half (§3). (b) Finding 3's peer evidence was the wrong constant — `PLAN_MODE_TOOLS` has **no runtime consumer**; the live policy is `read-only.toml` / `plan.toml`, and it makes `activate_skill` an **ask**, not an allow. "None of them touches the workspace" was also false for `save_memory`, which persists to SQLite (§5). (c) "the engine runs for every tool call" is false on the read-only path (it short-circuits *above* the engine), and gemini-cli **does** run a uniform policy pass (`core/src/scheduler/scheduler.ts:648-652`) — §8's first lead withdrawn, the other three stand. Plus: default counts needed a host qualifier (11/13 embedded vs 13/15 via the factory); `cli_help` is exported and host-registerable, so "dead class" was too strong; gemini-cli **does** gate on a model-name heuristic (`isGemini2Model`), contradicting rev 1's own §2.3; and `get_internal_docs` is reachable by the `cli_help` subagent's model, so §10 could not list it as unreachable while §6 counted `complete_task` in scope. |

---

## 0. The framing that makes this comparison fair

**Tool count is not a quality axis, and this document does not treat it as one.** codex carries
roughly 50 distinct tool names in tree; pi-mono carries 8 and shows the model 4. Those are not the
same measurement taken twice — they are two different bets, and pi-mono's is *deliberate*: it has
written `grep`, `find` and `ls` and keeps them out of the default set (§5). Reading the gap as a
feature deficit gets the comparison backwards on the one axis where three of the four repos disagree
with each other.

Three things *are* comparable, and each one is a decision agentao has already made:

1. **Is a file *read* a tool, or a shell invocation?** (§3) All four give file *writes* a dedicated
   tool — codex's `apply_patch` included. The read half is where they actually split.
2. **When is the tool set decided?** (§4) Not one question but **four**: initial build, registry
   mutation after that, active selection over the registry, and schema projection. The four are
   **not** a partition and no harness owns one of them — three of the four mutate the registry after
   the initial build. A claim about "when the set is decided" has to name the stage it means.
3. **What does the model see by default?** (§5)

A fourth axis, **where non-core tools live** (§6), is where the four diverge most in structure and
least in outcome.

---

## 1. Findings, ordered

Priority is *what would change an agentao decision*, not severity of the underlying code.

| # | Finding | Where | Kind |
|---|---|---|---|
| 1 | agentao gates on **host config only**, never on the model. codex mixes a harness-maintained `ModelInfo` catalog, `provider.capabilities()`, and (for `view_image`) neither — admitting the tool and refusing at execution. gemini-cli's one model gate is a **regex** on the model name (`isGemini2Model`, `core/src/config/models.ts:458`), not a catalog. The gap is real; the cost of closing it is **owning some continuously maintained compatibility fact** — its shape is not forced, and a regex or per-provider flag is on the table. | §4 | gap, unquantified demand |
| 2 | **`cli_help` has no in-tree instantiation in agentao**, and the comment saying it "registers elsewhere" (`tooling/registry.py:44-47`) names a tool with no definition file. Both classes stay reachable through the public `extra_tools=` injection point, so this is a stale comment plus two never-defaulted exports — not dead code. | §7 | doc/code drift, ours |
| 3 | **`/mode read-only` denies `activate_skill`, `todo_write` and `save_memory`.** The first two mutate only session state; `save_memory` writes SQLite. This follows correctly from a documented rule. The comparison point: gemini-cli's **live** read-only policy explicitly allows the internal-state-only tools (`tracker_*`, `update_topic`, `complete_task`) with a comment saying exactly that; on `activate_skill` it lands on **ask when interactive, deny when not** (`core/src/policy/policies/plan.toml:105-110` + the catch-all at `:76-80`) — a middle position agentao's boolean `is_read_only` gate has no room for. | §5 | policy question, ours |
| 4 | gemini-cli registers two model-visible tools that are **absent from its own `ALL_BUILTIN_TOOL_NAMES`**, and its only test over that constant checks it against itself in the direction that cannot fail. agentao has the same constant and *does* have the reverse-direction test. | §7 | peer defect; validates ours |
| 5 | **`complete_task` is a sub-agent-only tool in both agentao and gemini-cli**, arrived at independently. Two data points that the sub-agent terminal signal belongs in a scoped registry, not the main one. | §6 | convergence, no action |
| 6 | **Second peer defect, and it validates agentao's per-`chat()` snapshot.** gemini-cli's `reloadSkills()` re-registers `ActivateSkillTool` with a fresh skill enum and then calls only `updateSystemInstructionIfInitialized()` — never `setTools()` — so the cached chat schema keeps the **stale** enum until a model switch or an explicit refresh. agentao cannot reach this state: it re-projects from the live registry at the top of every `chat()`. | §4 | peer defect; validates ours |

---

## 2. The four inventories

### 2.1 agentao — 11 embedded / 13 via the CLI factory, +2 with the `[web]` extra

`agentao/tooling/registry.py::register_builtin_tools()`, registration order preserved. Two qualifiers on any count here. (a) `web_fetch` / `web_search` need the `[web]` extra
(`beautifulsoup4`), which neither a bare install nor `agentao[cli]` pulls (`<repo-root>/pyproject.toml:50,52`).
(b) `check_background_agent` / `cancel_background_agent` need `bg_store`, whose **constructor
default is `None`** (`agent.py:148`) — only `build_from_environment()` wires one
(`embedding/factory.py:231-232`). So: **11** for a direct `Agentao(...)` embed, **13** via the CLI /
environment factory, **+2** in either case with the extra installed.

| Tool | Source | Gate | `is_read_only` |
|---|---|---|---|
| `read_file` | `tools/file_ops.py:116` | — | ✅ `:111` |
| `write_file` | `tools/file_ops.py:214` | — | ✗ |
| `replace` | `tools/file_ops.py:270` | — | ✗ |
| `list_directory` | `tools/file_ops.py:514` | — | ✅ `:509` |
| `glob` | `tools/search.py:128` | — | ✅ `:123` |
| `search_file_content` | `tools/search.py:203` | — | ✅ `:198` |
| `run_shell_command` | `tools/shell.py:121` | — | ✗ |
| `web_fetch` | `tools/web.py:748` | `bs4` present (`[web]` extra) | ✅ `:743` |
| `web_search` | `tools/web.py:1125` | same | ✅ `:1120` |
| `save_memory` | `tools/memory.py:19` | — | ✗ (base default) |
| `activate_skill` | `tools/skill.py:21` | — | ✗ (base default) |
| `ask_user` | `tools/ask_user.py:28` | — | ✅ `:20` |
| `todo_write` | `tools/todo.py:20` | — | ✗ (base default) |
| `check_background_agent` | `agents/tools/_bg_tools.py:32` | `bg_store is not None` | ✅ `:27` |
| `cancel_background_agent` | `agents/tools/_bg_tools.py:125` | same | ✗ |

Registered outside `BUILTIN_TOOL_NAMES`:

- `agent_codebase_investigator` / `agent_generalist` — `agents/tools/_wrapper.py:224` names them
  `agent_{definition}`; definitions in `agentao/agents/definitions/`. **Opt-in, default off**
  (`agent.py:151 :: enable_builtin_agents: bool = False`, `embedding/factory.py:62`).
- `complete_task` — `agents/tools/_complete.py:33`, registered only into a sub-agent's *scoped*
  registry (`agents/tools/_wrapper.py:466`). Never on the main registry.
- `plan_save` / `plan_finalize` — `tools/plan.py:17,62`, registered by the CLI
  (`cli/app.py:336-337`) and withheld from the schema unless the turn is in plan mode
  (`tools/base.py:256,276`).
- `update_goal` — `tools/goal.py:34`, injected via `add_tool` while a `/goal` is active.
- `mcp_{server}_{tool}`, and host `extra_tools`.

`BUILTIN_TOOL_NAMES` (`tooling/registry.py:48-64`) is a validation set for `disable_tools` /
`enabled_tools`, pinned to the real registration by
`tests/test_host_tool_injection.py:220 :: test_builtin_tool_names_constant_in_sync`. Its docstring
is explicit that its scope is *registration eligibility, not live availability* — which is why
`web_search` is listed even without the `[web]` extra.

### 2.2 codex — ~50 names, no fixed default

`core/src/tools/spec_plan.rs::build_tool_router()` rebuilds the set **every turn**. Seven sources:

| Source | Tools | Site |
|---|---|---|
| Shell | `exec_command`, `write_stdin`, `apply_patch` | `core/src/tools/spec_plan.rs:1086`, `:1245` |
| MCP resources | `list_mcp_resources`, `list_mcp_resource_templates`, `read_mcp_resource` | `:1134` (only when a server is configured) |
| Core utility | `update_plan`, `view_image`, `clock.curr_time`, `clock.sleep`, `request_user_input`, `send_user_message_async`, `request_permissions`, `new_context`, `get_context_remaining`, `wait_for_environment`, `list_available_plugins_to_install`, `request_plugin_install`, `test_sync_tool` | `:1143` |
| Multi-agent v1 | `multi_agent_v1.{spawn_agent,send_input,resume_agent,wait_agent,close_agent}` | `:1334` |
| Multi-agent v2 | `spawn_agent`, `send_message`, `followup_task`, `wait_agent`, `interrupt_agent`, `list_agents` | `:1291` |
| Discovery / Code Mode | `tool_search`, `exec`, `wait` | `tools/src/tool_discovery.rs:6`, `code-mode-protocol/src/lib.rs:51-52` |
| Hosted + extensions | `web_search` (provider-side), `web.run`, `image_gen.imagegen`, `skills.{list,read}`, `memories.{list,read,search,add_ad_hoc_note}`, `history.*`/`notes.*` (9), `get_goal`/`create_goal`/`update_goal` | `core/src/tools/hosted_spec.rs:14`; six `ToolContributor` impls under `ext/` |

Defaults live in `features/src/lib.rs` as `FeatureSpec { stage, default_enabled }`. On by default:
`shell_tool`, `unified_exec`, `view_image`, `sleep_tool`, `multi_agent` (v1), `image_generation`,
`goals`, `skill_search`. Off by default: `memories`, `multi_agent_v2`, `token_budget`,
`current_time_reminder`, `standalone_web_search`, `request_permissions_tool`, `deferred_executor`,
`code_mode`.

**codex ships no `read_file` / `write_file` / `grep` / `glob`.** Grepping the whole tree, the name
`read_file` occurs only as fixture data in `ext/guardian-v2` tests.

One restricted posture is worth recording: a guardian reviewer session gets exactly `exec_command`,
`write_stdin`, `view_image`, and only under `PermissionProfile::Managed` — otherwise **none**
(`core/src/tools/spec_plan.rs:989-1037`).

### 2.3 gemini-cli — 26 registerable, 19–20 registered, **16–17 model-visible**

`packages/core/src/config/config.ts:3934 :: createToolRegistry()`, built **once at startup**.

Unconditional (16, plus `invoke_agent`): `read_file`, `write_file`, `replace`, `list_directory`,
`glob`, `grep_search`, `run_shell_command`, `list_background_processes`, `read_background_output`,
`web_fetch`, `google_web_search`, `read_mcp_resource`, `list_mcp_resources`, `ask_user`,
`update_topic`, `activate_skill`, `invoke_agent`.

Conditional:

| Tool | Gate | Default |
|---|---|---|
| `write_todos` | `useWriteTodos` — Gemini-2 family **and** not a preview model **and** tracker off (`core/src/config/config.ts:1294`) | model-dependent |
| `enter_plan_mode`, `exit_plan_mode` | `plan` (`core/src/config/config.ts:1135`) | **on** |
| `tracker_*` × 6 | `tracker` (`core/src/config/config.ts:1137`) | **off** |

`grep_search` is one name with two implementations: `RipGrepTool` preferred, falling back to
`GrepTool` when ripgrep is unavailable (`core/src/config/config.ts:3979-4001`).

Three names are in `ALL_BUILTIN_TOOL_NAMES` but never registered into the main registry —
`read_many_files` (used by the `@`-command processor and the ACP session:
`cli/src/ui/hooks/atCommandProcessor.ts:519`, `cli/src/acp/acpSession.ts:1012`), `get_internal_docs` (handed only to the
`cli-help` subagent, `core/src/agents/cli-help-agent.ts:89`), and `complete_task` (`core/src/agents/local-executor.ts:272`).

**Registration is not visibility, and rev 3's "19–20" was the registration number.**
`getFunctionDeclarations()` filters the registry every time it is **called** — which is not once per
request; see §4 (`core/src/tools/tool-registry.ts:601-624`): `update_topic` is dropped unless topic narration is on (default
**true**, `core/src/config/config.ts:1237`); `read_mcp_resource` and `list_mcp_resources` are dropped
when no MCP server exposes resources; and `enter_plan_mode` / `exit_plan_mode` are **mutually
exclusive** — `enter_` is hidden inside plan mode, `exit_` is hidden outside it. A bare default
session with no MCP therefore shows the model **16–17**, not 19–20.

Built-in subagents behind `invoke_agent`: `codebase_investigator`, `cli_help`, `generalist`,
`browser` (config-gated) — `core/src/agents/registry.ts:286-313`.

### 2.4 pi-mono — 8 built-in, **4 active**

Two layers that must not be conflated.

**`@earendil-works/pi-agent-core`** (`packages/agent/src/harness/tools/`) exposes four *opt-in
factories* for embedders — `bash`, `read`, `edit`, `write` — plus an image-processing helper.
Nothing auto-registers: grepping every call site in `packages/agent/src` outside `tools/` itself
returns empty.

**coding-agent** (`packages/coding-agent/src/core/tools/index.ts:95`) ships eight: `read`, `bash`,
`powershell`, `edit`, `write`, `grep`, `find`, `ls`.

**The default active set is four** — `read`, `bash`, `edit`, `write` (`core/sdk.ts:256`,
`core/agent-session.ts:2801`). The other four are packaged but invisible to the model unless
`settings.defaultTools` / `--tools` names them. `powershell` is **not** auto-selected on Windows;
the system prompt merely branches on whether it happens to be in the active set
(`core/system-prompt.ts:98`).

**No MCP client.** `@modelcontextprotocol/sdk` appears only transitively in the lockfile; no
`package.json` in the repo declares it. No web search, no web fetch, no todo, no subagent, no
plan-mode tool. The only built-in extension is `llama.cpp` — a provider, not a tool
(`packages/coding-agent/src/extensions/index.ts:4`). `todo` exists as `examples/extensions/todo.ts`, a sample.

---

## 3. Axis 1 — is a file *read* a tool?

"Read" below means a **general text/source read or search** — the column deliberately excludes
media-only readers, which is the distinction rev 2 got wrong by writing a bare zero.

| | write-side file tools | general read/search file tools | other tool-mediated reads | permission boundary |
|---|---|---|---|---|
| agentao | 2 (`write_file`, `replace`) | 4 (`read_file`, `list_directory`, `glob`, `search_file_content`) | — | per-tool rules in `permissions.json`, engine consulted for every call **that survives the read-only preset** |
| gemini-cli | 2 (`write_file`, `replace`) | 4 (`read_file`, `list_directory`, `glob`, `grep_search`) | — | uniform `checkPolicy` pass + `TOOLS_REQUIRING_NARROWING` (argument-level narrowing for 8 tools) |
| pi-mono | 2 (`write`, `edit`) | 4 (`read`, `grep`, `find`, `ls` — the last three off by default) | — | **no default policy at all**; one uniform, optional extension hook |
| **codex** | **1** (`apply_patch`) | **0** | **1** — `view_image`, image files only | sandbox profile + approval layer, **plus** per-target-path write permission derived inside `apply_patch` |

**rev 1 said codex has zero file tools. That was wrong.** `apply_patch` is a model-visible tool
whose handler *"routes verified patches to the selected environment filesystem"*
(`core/src/tools/handlers/apply_patch.rs:73-74`), and `write_permissions_for_paths` (`:236-270`) derives an
`AdditionalPermissionProfile` from the patch's **target paths** — argument-level, tool-mediated
permission that is closer to gemini-cli's `TOOLS_REQUIRING_NARROWING` than to "the sandbox is the
only boundary". The inferences rev 1 drew from the zero — that codex's permission unit *cannot* be
the tool, and that a 3:1 vote puts agentao in the majority — are both **withdrawn**.

**rev 2 then said codex has zero *read* tools. That was also wrong, and this is the axis's real
shape.** `view_image` takes a `path` documented as *"Local filesystem path to an image file"*
(`core/src/tools/handlers/view_image_spec.rs:19`), resolves it against the environment cwd, and reads it through the sandbox
filesystem — `fs.get_metadata(...)` then `fs.read_file(&path_uri, ReadFileOptions::default(),
Some(&sandbox))` (`core/src/tools/handlers/view_image.rs:150-175`). It is `Stage::Stable, default_enabled: true`
(`features/src/lib.rs:889-893`) and registered whenever an environment exists
(`core/src/tools/spec_plan.rs:1259`), so it is model-visible in a default session. codex therefore *does* have a
tool-mediated workspace read path.

What actually survives, stated at the right width: **codex has no *general* text or source read or
search tool.** No `read_file`, no `grep`, no `glob`, no `list_directory`; a full name sweep across
`core/src/tools`, `ext/` and `tools/src` turns up `read_file` only in `guardian-v2` test fixtures,
an `mcp.rs` example namespaced to a hypothetical `filesystem` server, and `notes.read_file` (the
history-notes extension's *own* note files, not the workspace). Everything except images goes
through `exec_command` under the sandbox. That is a real and unusual position — but it is "no
general reader", not "no reader".

**pi-mono's row moved too.** rev 2 wrote "per-tool, with `bash` as the escape hatch"; there is no
per-tool boundary to escape. With no extension registering a `tool_call` handler,
`beforeToolCall` returns `undefined` (`core/agent-session.ts:489-492`) and the loop executes the
call unchanged (`packages/agent/src/agent-loop.ts:634,653`). The permission gate ships as an **example**
(`packages/coding-agent/examples/extensions/permission-gate.ts:13`), not as policy. The accurate row is: **no default
permission policy; one uniform optional interception hook.**

**What this means for agentao is correspondingly narrower.**
`runtime/tool_planning.py::_decide` (`:487-518`) is three-tier, and the first tier is *not* the
engine: the read-only mode preset returns `DENY` at `:487-495` **before the engine is consulted at
all**. Tier 2 (the engine, for every remaining call) and tier 3 (`requires_confirmation` on
`ASK`-or-no-match) follow. That design still needs the tool to be the unit of permission for the
*read* surface — which is where codex offers nothing to borrow — but it is not evidence of a
majority position, and §8 no longer claims a uniform-pass lead over gemini-cli (which has one).

## 4. Axis 2 — when is the tool set decided?

codex recomputes per turn from three independent inputs — feature flags, **per-model metadata**
(`model_info.experimental_supported_tools`, `apply_patch_tool_type`, `supports_search_tool`,
`shell_type`), and **provider capabilities** (`provider.capabilities().web_search`,
`.namespace_tools`) — `core/src/tools/spec_plan.rs:124-190`, `:1143-1272`.

**Where that metadata comes from matters, and rev 3 got it wrong.** rev 3 called it "structured
capabilities the model itself declares". It is not: `ModelInfo` comes from a **catalog the harness
and backend maintain** — bundled or fetched remotely — and the configured model string is matched
against it by `find_model_by_longest_prefix` (`models-manager/src/manager.rs:617-631`), i.e. **slug prefix**, not
negotiation. A slug the catalog does not cover gets `model_info_from_slug`
(`models-manager/src/model_info.rs:143-144`), which logs *"Unknown model {slug} is used. This will use fallback model
metadata"* and synthesises a minimal descriptor. Nothing is asked of the model and nothing is sent
by it.

**But not uniformly, and rev 2 overstated it.** `view_image` is registered on
`environment_mode.has_environment() && features.enabled(Feature::ViewImage)` alone
(`core/src/tools/spec_plan.rs:1259`) — `input_modalities` is **not** consulted at registration. A text-only model
still sees `view_image` in its schema and is refused at *execution* with a
`FunctionCallError::RespondToModel` (`core/src/tools/handlers/view_image.rs:97-105`). `model_info` does shape that tool's
*schema* (`can_request_original_image_detail`), just not its presence. So codex's honest description
is **"some tools narrowed by declared capability, others admitted and rejected at execution"** — a
mixed strategy, not a clean capability gate.

**"Per turn or once per session" is the wrong axis, and rev 4 still led with it.** Every revision
of this section wrote "the other three fix the set once" and then listed bullets each admitting some
later variation. rev 5 split it into three stages and still put registry *mutation* in the
active-set column — `registerTool` / `unregisterTool` and `add_tool` change the registry, not a
selection over it. **Four** stages:

| | initial build | registry mutated after that | active selection over the registry | schema projected to the model |
|---|---|---|---|---|
| codex | **every turn** (`build_tool_router`, `core/src/tools/spec_plan.rs:124`) | n/a — the rebuild *is* the mutation | n/a | **per turn**, from that turn's freshly built registry |
| gemini-cli | once at startup (`createToolRegistry`) | **yes** — `registerTool` / `unregisterTool` on skill discovery and MCP connect | n/a | **only at `startChat`, on an explicit no-arg `setTools()`, or when the model changes** — `core/src/core/client.ts:801` calls `setTools(modelToUse)` per request but it **returns early** if the model is unchanged (`core/src/core/client.ts:311-313`); a `PLAN`/`YOLO` transition calls the no-arg form, which bypasses that guard (`core/src/config/config.ts:2810-2819`) |
| pi-mono | at `_buildRuntime` (`core/agent-session.ts:2757`) — **not once**: `reload()` calls it again (`core/agent-session.ts:2820`) | **yes** — `_refreshToolRegistry` rebuilds `_toolRegistry` (`core/agent-session.ts:2664`) | **yes** — `setActiveToolsByName`, driven by extensions (`core/agent-session.ts:971`) | **at the next agent turn** — that method rebuilds the system prompt and its own contract says *"Changes take effect on the next agent turn"* (`core/agent-session.ts:965-971`); the active set is projected unfiltered |
| agentao | once at construction (`register_builtin_tools` → MCP → agent → `extra_tools` → `apply_enabled_tools`) | **yes** — `add_tool` injection (e.g. `update_goal` while a `/goal` is live) | n/a — `enabled_tools` prunes once, at construction | **once per `chat()`, before the inner LLM loop** — `to_openai_format(plan_mode=…)` is called at `runtime/chat_loop/_runner.py:348`, snapshotting the schema for the whole turn; content-wise plan-only tools are withheld outside plan mode (`tools/base.py:276`) |

rev 5 also wrote "no two harnesses vary on the same one" and then said in the next paragraph that
gemini-cli and agentao both vary the projection — a contradiction inside one section. The columns
are not a partition: **three of the four mutate the registry after the initial build**, only
pi-mono has a separate active-selection layer, and only codex rebuilds from scratch per turn. What
the axis actually buys is the discipline of naming a column: a claim about "when the tool set is
decided" is meaningless until it says *which* of the four it means.

**The last column is what decides when a post-build mutation actually reaches the model**, and rev 7
answered it with *content* rather than timing for two of the four — then rev 8 got gemini-cli's
timing wrong in the other direction. The timings differ materially, and **gemini-cli's is the
loosest, not the tightest**: `GeminiChat` holds a cached tool list, and although `core/src/core/client.ts:801`
calls `setTools(modelToUse)` on every request, `setTools` short-circuits when the model has not
changed (`core/src/core/client.ts:311-313`). `registerTool` (`core/src/tools/tool-registry.ts:271`) only writes
`allKnownTools`; it invalidates nothing. So a registry mutation reaches the model at `startChat`, at
an explicit no-arg `setTools()`, or at a model switch — **not** at the next request. pi-mono and
agentao both defer to a **turn boundary**, and agentao's is the strictest — `agent.tools.to_openai_format(...)` runs once at `runtime/chat_loop/_runner.py:348`,
above the inner tool-call loop, so a tool added mid-turn is invisible until the *next* `chat()`
even if the loop runs twenty more iterations. `add_tool`'s own docstring states that contract
(`agent.py:906-914`): the schema is snapshotted once per call, while *execution* resolves names
against the live registry.

**That looseness is a live defect in gemini-cli, and it is Finding 6.** `Config.reloadSkills()`
unregisters `ActivateSkillTool` and re-registers a fresh instance whose schema enumerates the newly
discovered skills (`core/src/config/config.ts:3693-3699`), then calls only
`updateSystemInstructionIfInitialized()`. Nothing calls `setTools()`, and the per-request call
short-circuits on an unchanged model — so after a runtime skill reload the model keeps being offered
the **previous** `activate_skill` enum until something else forces a refresh. agentao cannot land in
that state by construction: it re-projects from the live registry at the top of every `chat()`
(`runtime/chat_loop/_runner.py:348`). Recorded as a peer observation, not an action item — it is
gemini-cli's bug, and the only thing it changes here is that agentao's stricter snapshot is a
*safety* property, not just a stricter one.

gemini-cli's registration is also **not** purely host config: `write_todos` is gated on
`isGemini2Model(this.model)` and `isPreviewModel(...)` (`core/src/config/config.ts:1293-1297`) — a
**model-name heuristic**, read once at construction. rev 1 filed gemini-cli as "host config only",
contradicting its own §2.3.

**Finding 1.** The axis has three positions, not two. codex reads a **structured per-model catalog
that its own harness and backend maintain**, matched by slug prefix with a warned fallback;
gemini-cli reads the **model name** and infers one tool from it; agentao and pi-mono read the model
**not at all**, gating only on host config (`disable_tools`, `enabled_tools`, mode presets). agentao
is at the far end, and a model that cannot take a given tool has no way for that to become a
narrower schema — the failure surfaces as a bad turn instead.

**The blocker is not transport, it is maintenance — but "maintenance of what" is not fixed, and rev
5 over-specified it.** rev 3 argued that codex's answer "needs a structured capability declaration
agentao's providers do not send"; that died once the metadata was traced to the catalog side. rev 5
then swung to "both peers maintain a per-model table", which is true of codex and **false of
gemini-cli**: `isGemini2Model` is `/^gemini-2(\.|$)/.test(model)` (`core/src/config/models.ts:458-461`), a regex
its own comment calls *"legacy behavior"* — not a catalog. codex is itself mixed: `ModelInfo` for
some decisions, `provider.capabilities()` for others, and `view_image` for neither.

So the shape is not forced. What the two peers actually share is that **somebody owns a
compatibility fact and keeps it current** — a catalog entry, a regex, or a capability field. The
cost of closing this gap is *an owned, continuously maintained compatibility policy of some shape*,
which is a weaker and more honest statement than "a model catalogue", and it leaves the cheap
options (a regex, a per-provider flag) on the table.

**Two revisions in a row got the precedent wrong in opposite directions; here is what the source
says.** rev 4 cited "`isRecoverableLength` self-refuted in `pi-mono-pull-review-2026-08-09`" — a
document that does not exist (`docs/design/` has `-2026-08` and `-2026-08-21`), so that citation is
gone. rev 5 then defended the substance by saying agentao "has no equivalent" of pi-mono's
`maxTokens` and offered `grep -r context_window agentao/` = 0 as proof. **That grepped the wrong
field.** `maxTokens` there is the requested *output* cap, not a context window, and agentao has the
same-semantic knob: `LLMClient.max_tokens` (`llm/client.py:139,188`), host-settable and mapped from
ACP's `maxTokens` (`acp/session_set_model.py:10`). **On the main agent path** it is forwarded
explicitly — `runtime/llm_call.py:138` passes `max_tokens=agent.llm.max_tokens`, and
`_build_request_kwargs` then emits `max_tokens` / `max_completion_tokens`
(`llm/client.py:419-421`). It is *not* forwarded everywhere: both `chat()` and `chat_stream()`
default the argument to `None` (`llm/client.py:430,534`) and the kwarg is added only `if max_tokens`
(`:419`), so the compaction summariser — which calls `chat(messages=…, tools=None)`
(`context_manager.py:1573`) — omits the field entirely. rev 6's "forwarded on every request" was too
wide.

The real difference is **the default, not the field**. pi resolves the number as
`options?.maxTokens ?? model.maxTokens` before clamping (`packages/ai/src/api/simple-options.ts:34`), so an
unspecified call falls back to a **per-model registry value**; agentao's constructor default is a
flat `65536` for every model (`llm/client.py:139`). `usage.output < desiredMaxOutput` is only sound
when that number is what the endpoint will actually allow — send 65536 to an endpoint that
**silently clamps** output to 8192 and every genuine max-output stop satisfies the predicate. The
hazard is therefore scoped twice over: to **the main agent path** (the only one that forwards the
value) and to **endpoints that clamp silently rather than erroring**. So the honest verdict is
narrower than either revision's: the borrow is **portable when the host sets `max_tokens` per model,
and unsafe under agentao's shipped default on such an endpoint** — a defaulting problem, which says
nothing about capability catalogues.

**`supportsFinishReason` is per-model too**, contrary to rev 5: pi configures it at **provider and
model levels** (`packages/coding-agent/test/model-registry.test.ts:771-778`). What was *not* per-model was agentao's
reason for inverting it — every value in `INCOMPLETE_ANSWER_REASONS` becomes a CLI error envelope,
so joining that set would hard-fail every provider that omits the field
(`docs/design/pi-mono-pull-review-2026-08.md:58`).

**Net: this repo has no precedent on the catalogue question.** Two per-model borrows were declined,
each for a reason of its own — a defaulting hazard and an error-envelope hazard — and neither was a
ruling on whether agentao should own per-model metadata. That question has never been put. Recorded
as a gap, **not** a work item — demand is unmeasured.

## 5. Axis 3 — default exposure

| | in tree | default-visible |
|---|---|---|
| codex | ~50 | not a fixed number by construction |
| gemini-cli | 26 registerable (19–20 registered) | **16–17** |
| agentao | 15 + 6 conditional/scoped | **11** embedded / **13** CLI-factory; **+2** with `[web]` |
| pi-mono | 8 | **4** |

pi-mono's four is the most restrictive by a wide margin, and the withholding is **deliberate and
compensated**: `grep` / `find` / `ls` are written and tested, kept out of the default active set
(`core/sdk.ts:256`), and the system prompt substitutes for them — when `grep`/`find` are absent it
adds *"Use bash for file operations like ls, rg, find"* (`core/system-prompt.ts:99-111`).

**Why they are withheld is not in the source, and rev 3 asserted it anyway.** rev 3 called it "an
explicit context-cost bet" and said codex "reaches the same conclusion", counting the two as
independent data points. Neither claim is evidenced: pi-mono's code shows *that* the tools are
withheld and *that* shell is offered in their place, not the reason; and codex never having written
those tools is not a recorded judgment about anything. Treat the context-cost reading as
**inference, unmeasured** — the observation that stands is the shape (two harnesses ship a small
default surface and route general file work through the shell), not a shared motive.

**Finding 3.** agentao's `read-only` mode denies any tool whose `is_read_only` is `False`
(`runtime/tool_planning.py:487`, reason `mode-preset:read-only`), and the base default is `False`
(`tools/base.py:117-126`). Three tools never override it and are therefore denied under
`/mode read-only`: `save_memory`, `activate_skill`, `todo_write`. The mechanism is documented
(`agentao/docs/reference/configuration.md:171` — "empty preset; `ToolRunner` short-circuits on
`tool.is_read_only`"), so this is a correct consequence of a stated rule, not a defect.

**rev 1 got the peer evidence wrong, twice.** It cited `PLAN_MODE_TOOLS` (`core/src/tools/tool-names.ts:283`) as
gemini-cli's explicit read-only list — but that constant **has no runtime consumer anywhere in the
repo**; its own comment says it is used to generate the plan-mode prompt, and nothing reads it. The
live policy is TOML: `core/src/policy/policies/read-only.toml:30-55` and `plan.toml`. And on `activate_skill` that policy
says **ask**, not allow (`core/src/policy/policies/plan.toml:105-110`, grouped with `ask_user` and `web_fetch`). rev 1 also
wrote that none of the three "touches the workspace"; `save_memory` persists through
`MemoryManager.upsert` (`memory/manager.py:80`) into the project or user SQLite store, so that was
false for one of the three.

**rev 2 then mis-stated the ask.** It said gemini-cli "agrees with agentao" on `activate_skill` and,
two sentences later, that ask is "a third position" — those cannot both hold, and the first is
wrong: ASK is not DENY. Worse, the ask rule carries `interactive = true` (`core/src/policy/policies/plan.toml:110`), so in a
**non-interactive** run it does not apply and the plan-mode catch-all
(`toolName = "*"`, `decision = "deny"`, `core/src/policy/policies/plan.toml:76-80`) takes it. gemini-cli's actual behaviour is
**interactive → ASK, non-interactive → DENY**.

**The observation survives on the corrected evidence, and is narrower.** `core/src/policy/policies/read-only.toml:30-55`
allows `tracker_create_task`, `tracker_update_task`, `tracker_get_task`, `tracker_list_tasks`,
`tracker_add_dependency`, `tracker_visualize`, `update_topic` and `complete_task` under a comment
that reads *"safe as they only modify internal state"*. agentao's `todo_write` is the direct
analogue of that class and is denied, as is `activate_skill` — where gemini-cli reaches DENY only in
the non-interactive case and offers ASK otherwise. That middle position is unreachable **for this
gate specifically**: the read-only preset branches on the boolean `tool.is_read_only`
(`runtime/tool_planning.py:487`), which has two values. It is *not* a claim that agentao cannot express ASK
at all — the permission engine's `ASK` is tier 2 and works normally for every call the preset lets
through. agentao reached its answer by
inheriting a default rather than by deciding. Whether that is right is the maintainer's call; this
document only records that it was never made explicitly.

## 6. Axis 4 — where the non-core tools live

| | mechanism | examples |
|---|---|---|
| codex | `ToolContributor` extensions behind feature flags | `skills.*`, `memories.*`, `history.*`/`notes.*`, `get_goal`/`create_goal`/`update_goal`, `image_gen.imagegen` |
| gemini-cli | hard-coded in `createToolRegistry` behind booleans | `tracker_*`, plan-mode pair, `write_todos` |
| agentao | built-in list + CLI injection + host `extra_tools` | `activate_skill`, `save_memory`, `todo_write`; `update_goal` via `add_tool` |
| pi-mono | **nothing in tree** — user extensions only | `todo` ships as a sample extension |

Two convergences worth recording:

- **`complete_task` is sub-agent-scoped in both agentao and gemini-cli**, independently:
  `agents/tools/_wrapper.py:466` registers it into a scoped registry; `core/src/agents/local-executor.ts:272` hands
  it only to the local executor. Neither exposes it on the main registry. That is Finding 5 — no
  action, but it is the kind of two-repo agreement that should raise the bar for anyone proposing
  to promote it.
- **Skills and memory as model-visible tools**: codex exposes `memories.{list,read,search}` to the
  model; agentao deliberately exposes only the *write* (`save_memory`), keeping search/delete/clear
  on the CLI (`/memory …`). That asymmetry is documented and intentional — this comparison does not
  disturb it, but codex is the one peer that went the other way, so the asymmetry is a choice with
  a live counterexample rather than an industry default.

## 7. Dead and half-registered names

All four repos accumulate them; the interesting part is which direction each one's guard points.

**agentao — Finding 2.** `agentao/tools/agents.py` defines `CLIHelpAgentTool` (`:8`, name
`cli_help`) and `CodebaseInvestigatorTool` (`:43`, name `codebase_investigator`). Both are exported
(`agentao/tools/__init__.py:10,31-32`) and **neither is instantiated anywhere in `agentao/` or `tests/`**. The
comment at `tooling/registry.py:44-47` says agent-path tools "(codebase_investigator / cli_help)
register elsewhere and are intentionally out of scope" — half true. `codebase_investigator` exists
as an agent *definition* and registers as `agent_codebase_investigator` (`agents/tools/_wrapper.py:224`), so the
comment points at something real under a different name. `cli_help` has no definition file
(`agents/definitions/` holds only `codebase-investigator.md` and `generalist.md`) and no
instantiation, so it is a name the comment invents.

**What that does and does not prove.** It proves there is **no in-tree instantiation** and no
default registration — not that the classes are unreachable. Both are public exports of
`agentao.tools`, and `extra_tools=` (`agent.py:194`) is a documented host injection point that
would register either one as a live model tool. So this is a **stale comment plus two
never-defaulted exports**, not dead code: deleting them is an API change on a public surface, and
the cheap fix is the comment. rev 1 called them "dead classes", which overstated it.

**gemini-cli — Finding 4.** `save_memory` is gone the same way: `memoryTool.ts` retains only the
GEMINI.md filename constants, and no `new MemoryTool` exists anywhere. More usefully, the reverse
direction has already bitten: `list_background_processes` and `read_background_output` are
registered as real model tools (`core/src/tools/shellBackgroundTools.ts:75,253`, registered at
`core/src/config/config.ts:4028-4037`) but are **absent from `ALL_BUILTIN_TOOL_NAMES`**, so `isValidToolName()`
returns `false` for both. `core/src/agents/agentLoader.ts:103` gates a zod `.refine()` on that function, so a
user-authored agent file listing either name is **rejected outright**. The policy loader
(`core/src/policy/toml-loader.ts:278`) only warns on near-typos and these are far from every built-in name, so it
stays silent there.

The reason this matters to agentao and not just as peer trivia: the only test over
`ALL_BUILTIN_TOOL_NAMES` (`core/src/tools/tool-names.test.ts:50`) iterates the constant and asserts each entry is
valid — it checks the list against *itself*, in the direction that cannot fail. The direction that
drifted, registry → constant, is untested. agentao's `BUILTIN_TOOL_NAMES` is the same shape of
constant with the same job, and it **does** have the reverse-direction test
(`test_builtin_tool_names_constant_in_sync`). That test is doing real work; this is the peer
evidence for keeping it.

## 8. What agentao leads on

Recorded so the comparison is not one-directional. **Two**, down from rev 1's four and rev 2's
three — each verified at its anchor, and neither is a claim that the other three sides have nothing
comparable, only that agentao's form of it is stricter:

> **Withdrawn in rev 2.** rev 1 led with "a permission engine consulted on every tool call … no
> equivalent uniform pass" elsewhere. Both halves are wrong: agentao's read-only preset returns
> `DENY` *above* the engine (`runtime/tool_planning.py:487-495`), so the pass is not universal; and
> gemini-cli runs `checkPolicy` on every validated call from its scheduler
> (`core/src/scheduler/scheduler.ts:648-652`). On that axis agentao and gemini-cli are **level**, codex resolves before the
> tool layer, and pi-mono has no engine — parity, not a lead.

1. **Both tool-selection knobs reject an unknown name instead of silently no-op'ing** — but by
   **two different mechanisms**, which rev 3 wrongly merged into one. `enabled_tools` validates
   against the **live registry ∪ `BUILTIN_TOOL_NAMES`** (`tooling/registry.py:195-205`), so it can
   also accept **agent-path** names that only exist after wiring — but *not* MCP names: an
   `mcp_`-prefixed or plan-only entry is rejected earlier by the reserved-name guard
   (`agent.py:449-452`, pinned by `tests/test_host_tool_allowlist.py:138`), so it never reaches the
   live-registry check. rev 4 claimed MCP names were accepted; they are not. `disable_tools` validates
   against the **static constant alone** (`agent.py:466-472`), which is why its error message says
   only built-ins are disableable. Both carry the "registration eligibility ≠ live availability"
   rule that keeps `web_search` legal without the `[web]` extra. pi-mono's `--tools` silently
   filters (`core/sdk.ts:258-263`); gemini-cli's `coreTools` matches by substring prefix
   (`core/src/config/config.ts:3953-3959`) and cannot report an unknown name at all.
2. **Mode *entry and exit* are host commands, not model tools.** agentao's `/plan` switches the
   posture; the model never gets a tool that changes its own permission mode. gemini-cli ships
   `enter_plan_mode` / `exit_plan_mode` as model tools. rev 3 called them "always-visible, policed
   at execution"; that is **wrong** — the two are mutually exclusive in the schema
   (`core/src/tools/tool-registry.ts:617-624` hides `enter_` inside plan mode and `exit_` outside it), and a mode
   transition re-sends the list (`core/src/config/config.ts:2810-2819`). gemini-cli withholds from
   the schema exactly as agentao does. The **only** surviving difference is that its model gets a
   tool that changes its own permission posture at all; codex and pi-mono have none either, so 3:1.
   `core/src/policy/policies/plan.toml:68-72` is a second layer on top, not the mechanism.

> **Withdrawn in rev 3.** rev 2's second lead was "sub-agent tools inherit the parent's binding
> explicitly". gemini-cli does the same thing: `core/src/agents/local-executor.ts:190-200` builds the sub-agent
> registry from `context.toolRegistry` — the parent's — and `core/src/tools/tools.ts:480`'s `clone(messageBus)` is a
> shallow `Object.assign(Object.create(proto), this)` that replaces only the message bus, so
> `config`, target directory and filesystem binding all carry over. agentao's `_bind_and_register`
> is the same idea in a different language, not a stricter one. Parity.
>
> rev 2's third lead was "plan-only tools are withheld from the schema rather than gated at
> execution", and §9 generalized it to "agentao keeps plan mode out of the tool surface" — which
> **contradicts §2.1**, where `plan_save` / `plan_finalize` are model tools inside plan turns
> (`cli/app.py:336-337`, `tools/base.py:276`). The schema-withholding mechanism is real, but the
> claim that survives is about *mode switching*, which is lead 2 above.

## 9. Candidate borrows — none authorized

| Candidate | Verdict | Why |
|---|---|---|
| codex's per-turn model-capability gating | **tabled**, demand-gated | Real gap (§4). Closing it means agentao owning **some** continuously maintained compatibility fact — rev 5 said "a per-model table", which over-specified it: gemini-cli's is a regex (`core/src/config/models.ts:458`) and codex mixes catalog with `provider.capabilities()`. rev 5 also asserted agentao *cannot* maintain one provider-neutrally; nothing here supports that, and §4 records that the question has never been put — so this is tabled on **unmeasured demand**, not on established impossibility, and not on a fixed implementation shape. Revisit if a concrete model rejection is observed in the wild. |
| codex's `get_context_remaining` / `new_context` | **no** | Already analysed in `codex-compaction-vs-agentao.zh.md`; the token-budget row's verdict was "mode default off". This comparison adds a third data point *for* that verdict: gemini-cli and pi-mono both have nothing equivalent, so codex is 1/4 here, not the norm. |
| gemini-cli's **mode entry/exit** as model tools | **no** | Restated rev 3, narrowed rev 4: the 1/4 difference is that the model gets `enter_plan_mode` / `exit_plan_mode` **at all** — a tool that changes its own permission posture. It is *not* "plan mode out of the tool surface" (agentao's `plan_save` / `plan_finalize` are model tools inside plan turns, §2.1) and *not* "schema vs execution gating" (gemini-cli schema-filters the pair by mode too, `core/src/tools/tool-registry.ts:617-624`). |
| pi-mono's minimal default set | **no** | agentao's 11–15 is mid-field. The agreement noted in §5 is **shape only** — two harnesses ship a small default surface and route general file work through the shell. rev 4 downgraded the shared *motive* to inference and this row still called it "about context cost"; that is withdrawn here too. agentao's small-surface knobs are `enabled_tools` / `disable_tools`, host-side rather than a shipped default. No change proposed. |
| gemini-cli's `TOOLS_REQUIRING_NARROWING` | **watch** | Argument-level narrowing when granting session-wide approval — agentao's engine matches on arguments but has no notion of "this tool may not be blanket-approved without narrowing". Not a gap today because agentao has no session-wide grant UI of that shape; becomes relevant if one is added. |

## 10. How to re-derive

Each side has one entry point for *initial* construction; start there. It is not the whole story —
§4's four-stage table lists the mutation and projection sites, three of the four harnesses change
the registry after this entry point runs, and pi-mono re-runs the entry point itself on reload.

```
codex       codex-rs/core/src/tools/spec_plan.rs::build_tool_router
            → add_core_tool_sources (:985) → the four add_* functions
            → features/src/lib.rs for every default_enabled
gemini-cli  packages/core/src/config/config.ts::createToolRegistry (:3934)
            → tools/tool-names.ts::ALL_BUILTIN_TOOL_NAMES for the claimed list
            → diff the two; the delta is §7
pi-mono     core/agent-session.ts::_buildRuntime (:2757) — the real entry; re-runs
            from reload() (:2820). tools/index.ts::allToolNames (:95) is only the
            name set, not a build step
            → core/sdk.ts:256 + core/agent-session.ts:2801 for the active set
agentao     agentao/agent.py::_wire_tooling (:578) — the full entry; it calls
            register_builtin_tools, then MCP, agent, extra_tools, apply_enabled_tools
            → tooling/registry.py::BUILTIN_TOOL_NAMES (:48) and its sync test
            → cli/app.py:336, tools/goal.py, agents/tools/_wrapper.py for the rest
```

Three method notes, all learned the hard way — the third one on rev 2, from this document's own citations:

- **Resolve names to string literals.** Three of the four repos indirect through a constant, and in
  gemini-cli the constant and the registry disagree. A list of constant *names* would have missed
  Finding 4 entirely.
- **A tool class existing is not a tool being registered — and "registered" has more than one
  destination.** rev 1 lumped four names together as "unreachable from the model"; only two of them
  are, and for different reasons. `save_memory` (gemini-cli) is genuinely dead — no instantiation
  anywhere. `read_many_files` is instantiated but **host**-invoked (the `@`-command processor and the
  ACP session), so it never enters any model's tool list. `get_internal_docs` **is** model-reachable
  — `core/src/agents/cli-help-agent.ts:89` hands it to the `cli_help` subagent's own model, and since §6 counts the
  sub-agent-scoped `complete_task` as in scope, this one cannot be excluded as unreachable.
  `cli_help` (agentao) has no in-tree instantiation but is a public export that `extra_tools=`
  can register. So: grep for the *instantiation*, then ask **which registry it lands in** — main,
  sub-agent-scoped, or host-side-only.

- **A citation's basename is not an address.** rev 1 wrote `apply_patch.rs:73`, `config.ts:1135` and
  `registry.py:196`; codex has **four** `apply_patch.rs`, pi-mono has a second `config.ts`, and
  agentao has a second `registry.py` under `mcp/`. A resolver pointed at the wrong file in each case
  and the last one was also off by one (the typo guard starts at `:195`; `:194` is blank). Qualify
  every path far enough to be unique in its own repo, then **re-resolve every citation
  mechanically** — the check that catches this is running the anchors back against source, not
  re-reading the prose. **rev 14 applied that rule to the whole document for the first time**, which
  is the part worth copying: stating a citation rule does not enforce it, and the enforcement cannot
  be the resolver. **rev 15 then found that "unique in its repo" is too weak a rule and made it a
  floor:** no citation may be *shorter* than its repo's conventional root, because the damage is
  done by two spellings of the same directory sitting in one document — `core/src/tools/handlers/apply_patch.rs`
  beside `handlers/view_image.rs`, `core/src/config/config.ts` beside `config/models.ts`. Both short
  forms resolve uniquely, so rev 14's sweep (which looked only for names with no `/`) could not see
  them. Over-qualifying is always allowed and sometimes required — `extensions/index.ts` matches two
  files in pi-mono, and §2.4's contrast between its two packages is clearer with full paths.

- **A quotation must lie inside the range cited beside it, and that is mechanical.** rev 16 made it a
  script: pull every `*"…"*` excerpt, find the nearest citation, join the cited lines with leading
  comment markers stripped, and require containment. It immediately caught one the rev-13 pass had
  read by eye and passed — `core/src/tools/handlers/apply_patch.rs:73` quotes a doc comment that
  runs onto `:74`, and
  "the quote starts on the cited line" had felt like enough. Two limits worth knowing before
  trusting it: without the comment-marker strip a two-line `///` quotation reports as a miss, and the
  document's own italic-quoted phrasing is indistinguishable from a source excerpt, so one standing
  false positive (§4's "some tools narrowed by declared capability…") is expected. A bare basename that happens to be unique in its repo *resolves*, so every
  mechanical pass rev 3–13 ran reported it clean; the sweep that found the remaining 18 was a
  separate query — list the distinct filenames cited, then look at which carry no `/`.
