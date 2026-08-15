# Design docs index · 设计文档索引

Grouped map of `docs/design/`. Docs are **not** moved into subdirectories — they
cross-link each other heavily (`Companion:` / `Related:` frontmatter) and several
are cited as anchors by other docs and by agent memory, so flattening keeps those
links stable. Use this index to find the **review records / backlog** vs the
**active designs** vs the **landed contracts**.

本目录不做物理分目录：这些文档彼此交叉链接密集，且部分被其它文档与记忆当锚点引用，扁平结构能让链接
稳定。用本索引区分「评审记录 / backlog」「活跃设计」「已落地契约」。

> Each doc has a `.zh.md` companion **except** those marked *(en only)*.
> 除标注 *(en only)* 外，每篇都有 `.zh.md` 对照件。

---

## Foundational / orientation · 基础与定位

Start here — strategy and the host/ACP surface boundary.

- **path-a-roadmap** — embed-first strategy (locked 2026-04-30); the anchor most other records test against.
- **embedding-vs-acp** — decision tree: which integration surface (embed / `agentao run` / ACP) do I use?
- **embedded-host-contract** *(en only)* — the public host-contract design (events, ActivePermissions, ACP schema).

## Active & proposed designs · 活跃与提案中的设计

The live backlog of work — proposed, in progress, or impl-deferred. New build work usually starts from one of these.

- **cli-host-agent-factory** — *shipped* — instance-scoped `agent_factory` on `AgentaoCLI` / `cli.main()` so thin Python hosts reach `extra_tools` and the existing runtime construction contract without monkey-patching; validates the returned runtime (#132, 2026-07-19).
- **acp-g4-plan-modes-commands** — *proposal* — surface plan / modes / commands as ACP `session/update` (chat-target now-work).
- **deepchat-acp-patch-revision** — *impl in progress* — DeepChat/TensorChat ACP integration; what upstreams vs stays in the fork.
- **host-fs-policy** — *proposal* — path-domain write boundary over the single fs chokepoint (incl. shell).
- **host-tool-allowlist** — *draft, converged* — `enabled_tools` additive allowlist.
- **mcp-tool-list-pagination** — *shipped 2026-08-03 (2 review rounds)* — agentao issued one `tools/list` per server and never read the cursor, so a paginating server's tools past page 1 were **silently** invisible (`grep next_cursor` → zero matches repo-wide). Borrows codex's bounds (#36039/#35724) with the loop. §4 is load-bearing: `params=` is the one call spelling working on all three CI SDK cells incl. the 1.26.0 floor — **probed, not recalled** — while the `nextCursor`/`next_cursor` read needs the existing `_compat.field`. Round 1 fixed three correctness defects (`cursor is not None` — `""` is a legal cursor; item cap gates `tools.extend`; cursor cap counts UTF-8 bytes) and closed D1 to **one rule — fail that server** — the isolation being `connect()`'s own non-re-raising catch at `client.py:310-347`, **not** `_connect_one`, which never fires here. Round 2 fixed four *prose* inaccuracies worth not re-deriving: the `""` cursor causes a wasted request + **wrong verdict**, not a hang (the repeated-cursor guard does catch it); the item cap is a **catalog-accumulation** bound, not DoS/wire, since the SDK parses before `_list_all_tools` sees anything; the single-page-overflow test **cannot** pin check-before-`extend` and must not pretend to; and **`permissions.json` does cover MCP tools** (`decide_detail` runs before `requires_confirmation`) — only `enabled_tools` and the name prefix are non-boundaries. A round-3 xhigh review then corrected the precedence once more: the **read-only mode preset short-circuits ahead of the engine** (`tool_planning.py:381-389`), so "the engine sees every call" is true only past that preset — the same overstatement had propagated into CLAUDE.md and both language docs (2026-08-03).
- **metacognitive-boundary** — *impl deferred* — metacognitive boundary as a host-injectable protocol (schema + default + override).
- **permission-hardening-plan** — *impl plan, rev 3* — shell-pattern hardline scanner hardening.
- **tool-search** — *draft, deferred* — deferred-loading tool discovery. **Two reference implementations** as of 2026-08-03: codex (heavy — `ToolExposure` enum + core BM25 `tool_search`) and pi-mono (light — a 39-line stateless `splitDeferredTools`, contract is one `addedToolNames` field, search itself is a *userland example extension*). pi-mono adds activation model **(c) transcript-carried**, which keeps the tool prefix byte-stable but needs a provider-native load point agentao's chat-completions path lacks — so it **does not lower agentao's cost**, and its driver is provider-led, not tool-list bloat, so it is **not** a second instance of the trigger. Decision unchanged.

## Landed contracts · 已落地契约

Shipped behavior — read as reference for what exists today.

- **host-tool-injection** — `extra_tools` / `disable_tools` (v1 landed).
- **runtime-tool-injection** — `add_tool` / `remove_tool` (v1 landed).
- **host-llm-extra-params** — host LLM request passthrough `extra_body` (v1).
- **run-spec-parameters** — `agentao run` spec parameters & instructions (shipped 2026-05-25).
- **mcp-streamable-http** — MCP Streamable HTTP transport; bare `url` now defaults to it (**breaking**, SSE is opt-in via `type: "sse"`). Shipped 0.4.14, 2026-07-02. **§5.1/§5.3 carry mcp-2.0 update blocks** (0.4.17): the stream tuple lost its third element and the SDK moved to `httpx2` — an arity that no signature or field name exposes, only the `yield`.
- **lint-gate** — the CI `ruff check .` gate: which rules, and the measurement behind each inclusion *and exclusion* (2026-08-03). Two non-obvious records: `F821` is inert in star-import modules unless `F405` is also selected, and `F401` is undecidable inside `agentao/` because a name re-exported for embedders looks identical to dead code from in-repo.

## Review & decision records · 评审与决策记录 *(the "backlog" class)*

Retrospective analyses — competitive/borrow reviews, conformance gap reviews, decision records. Evidence-before-recommendation; **gap ≠ need**. Not active build specs.

- **otel-peer-survey** *(zh only)* — OpenTelemetry across 12 peer harnesses: **8 use OTel, 1 uses a non-OTel contract (pi-mono), 3 have none** — but the finding is the four *architectures* (in-core SDK / out-of-core extension / vendor-neutral contract / third-party wrapper), not the count. **Zero build items; does not change P1.2's demand gate** — the three conclusions are: don't start P1.2; when it starts, default-off + explicit endpoint + **all** OTel deps declared in an extra; keep the core event contract decoupled and defer JSONL-vs-live until a real enterprise topology exists. **§5 is a 13-row errata table from seven revisions — read it before re-raising anything**, and note the "none found" verdicts have a short shelf life (two upstream re-checks both flipped conclusions) (2026-08-15).
- **openworker-borrow-review** *(zh only)* — OpenWorker (andrewyng, aisuite-based desktop coworker) adoption assessment. **rev 3: zero borrow items approved.** The one action item is unrelated to OpenWorker — a **live permission fail-open found while cross-checking**: background sub-agents pass `confirmation_callback=None` (`_wrapper.py:502`), and `sdk.py:101` auto-approves when no callback, so an ASK-gated shell call runs silently (§1). Two reverse passes falsified one finding (untrusted-content framing already exists in `prompts/sections.py`), declined the persistent shell **on process-management cost — not containment, since `path_policy.py:13` already excludes shell arguments**, downgraded the "capability catalog" to config hygiene, ruled RiskClass out, and caught **two items already settled** by `pi-mono-borrow-review` / `dynamic-workflows-review` — do not re-raise those without new evidence (2026-07-29).
- **dynamic-workflows-review** *(zh only)* — Claude Code Dynamic Workflows adoption assessment. **rev 5: don't adopt, don't design an alternative, change no code.** Records one verified fact (same tool instance serializes within a batch; whether that default may be bypassed is undefined) plus §3.5, a candidate implementation plan that activates only if a real use case demands it — explicitly not a commitment (2026-07-19).
- **code-mode-ptc-review** *(zh only)* — Code Mode / Programmatic Tool Calling decision record; 4 peer impls compared, current decision **not to start** pending demand (2026-07-09, 4th impl added 07-11).
- **codex-goal-mechanism-review** — two parts: Codex `/goal` mechanism teardown (§§1–9, descriptive) + agentao candidate design (§§10–11, **not approved**) (2026-06-23).
- **subagent-discovery-entrypoint-review** — sub-agent discovery asymmetry across the three entry points; corrects the skill-vs-plugin conflation the original report assumed (2026-06-23).
- **refactor-audit-2026-07** — full-tree refactor audit **plus an adversarial reverse review of its own findings**: 7 candidates → 3 shipped (#139/#140 + replay v1.2 render), 4 declined with evidence. Formally declines `optimization-opportunities-review`'s Tier 3 on churn data. Read the *declined* half first (2026-07-24/25).
- **optimization-opportunities-review** — multi-dimension audit (complexity / per-turn perf / duplication / packaging); Tier 1–3 findings, evidence-backed (2026-06-19). **Tier 1–2 shipped in v0.4.12; Tier 3 declined — see refactor-audit-2026-07 §5.**
- **vendor-sdk-convergence-review** — Claude/OpenAI Agent SDKs converged on Path A's pitch; §16.4 trigger determination (2026-06-18).
- **acp-server-conformance-review** — agentao ACP server vs official ACP v1; gaps G1–G6 + chat/automation target decision (2026-06-18).
- **core-boundary-review** — core vs host package-boundary audit (codex parallel, 2026-05).
- **codex-reverse-review** *(en only)* — reverse-review discipline record (2026-05-12).
- **pi-mono-borrow-review** — pi-mono v0.66→v0.73 borrow analysis; demand-gated precedent anchor.
- **pi-mono-pull-review-2026-08** — pi-mono v0.80.6→v0.83.0 (434 commits). 3 landed (#159 NFKC edit matching, #160 listener-error logging, #161 `finish_reason_missing` — the last **adopted inverted** from pi's error-by-default). 2 deferred as contract decisions (sub-agent boundary, ACP channel). `watch()` snapshot/subscribe recorded as the one real architectural gap, but its assumed trigger `agentao serve` is ✗-listed in the roadmap, so read §"Recorded, demand-gated" before assuming it is scheduled. 8 not-applicable, each with the query that settled it — **do not re-raise those without new evidence** (2026-08-02).
- **pi-mono-tools-review** — pi-mono tools-level companion review.
- **pi-mono-openai-stream-fix** — pi-mono OpenAI-compat stream fix + agentao-side gap analysis.
- **system-prompt-profile** — host-injectable collaboration posture (review record; impl deferred).

---

### Where does a new doc go? · 新文档放哪一组？

- Analyzing another repo / auditing agentao / recording a decision → **Review & decision records**.
- Proposing or speccing work not yet shipped → **Active & proposed designs** (mark status in the `Status:` line).
- Documenting shipped behavior → **Landed contracts**.

When a doc graduates (proposal → shipped), move its line between groups here; don't move the file.
