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

- **acp-g4-plan-modes-commands** — *proposal* — surface plan / modes / commands as ACP `session/update` (chat-target now-work).
- **deepchat-acp-patch-revision** — *impl in progress* — DeepChat/TensorChat ACP integration; what upstreams vs stays in the fork.
- **host-fs-policy** — *proposal* — path-domain write boundary over the single fs chokepoint (incl. shell).
- **host-tool-allowlist** — *draft, converged* — `enabled_tools` additive allowlist.
- **metacognitive-boundary** — *impl deferred* — metacognitive boundary as a host-injectable protocol (schema + default + override).
- **permission-hardening-plan** — *impl plan, rev 3* — shell-pattern hardline scanner hardening.
- **tool-search** — *draft, deferred* — deferred-loading tool discovery.

## Landed contracts · 已落地契约

Shipped behavior — read as reference for what exists today.

- **host-tool-injection** — `extra_tools` / `disable_tools` (v1 landed).
- **runtime-tool-injection** — `add_tool` / `remove_tool` (v1 landed).
- **host-llm-extra-params** — host LLM request passthrough `extra_body` (v1).
- **run-spec-parameters** — `agentao run` spec parameters & instructions (shipped 2026-05-25).

## Review & decision records · 评审与决策记录 *(the "backlog" class)*

Retrospective analyses — competitive/borrow reviews, conformance gap reviews, decision records. Evidence-before-recommendation; **gap ≠ need**. Not active build specs.

- **optimization-opportunities-review** — multi-dimension audit (complexity / per-turn perf / duplication / packaging); Tier 1–3 findings, evidence-backed (2026-06-19).
- **vendor-sdk-convergence-review** — Claude/OpenAI Agent SDKs converged on Path A's pitch; §16.4 trigger determination (2026-06-18).
- **acp-server-conformance-review** — agentao ACP server vs official ACP v1; gaps G1–G6 + chat/automation target decision (2026-06-18).
- **core-boundary-review** — core vs host package-boundary audit (codex parallel, 2026-05).
- **codex-reverse-review** *(en only)* — reverse-review discipline record (2026-05-12).
- **pi-mono-borrow-review** — pi-mono v0.66→v0.73 borrow analysis; demand-gated precedent anchor.
- **pi-mono-tools-review** — pi-mono tools-level companion review.
- **pi-mono-openai-stream-fix** — pi-mono OpenAI-compat stream fix + agentao-side gap analysis.
- **system-prompt-profile** — host-injectable collaboration posture (review record; impl deferred).

---

### Where does a new doc go? · 新文档放哪一组？

- Analyzing another repo / auditing agentao / recording a decision → **Review & decision records**.
- Proposing or speccing work not yet shipped → **Active & proposed designs** (mark status in the `Status:` line).
- Documenting shipped behavior → **Landed contracts**.

When a doc graduates (proposal → shipped), move its line between groups here; don't move the file.
