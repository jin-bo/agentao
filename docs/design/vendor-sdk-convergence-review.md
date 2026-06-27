# Vendor-SDK Convergence — Path A Re-examination Trigger Review

**Status:** Strategic review record. Drafted 2026-06-18 after a June-2026 competitive
landscape sweep (CLI agents, embeddable agent-loop SDKs, general agent frameworks).
**This is a *trigger determination*, not a strategy reversal.** It records that
`path-a-roadmap.md` §16.4's "external shift" condition has plausibly fired, lays out the
evidence, and proposes — *for the maintainer's judgment* — what changes and what does
not. It does **not** unilaterally retire or rewrite Path A; §1–§8 of the roadmap remain
locked until a maintainer decision says otherwise.
**Audience:** Agentao maintainers and strategic reviewers.
**Companion:** `vendor-sdk-convergence-review.zh.md`.
**Related:**
- `path-a-roadmap.md` — the locked embed-first strategy this review tests against (esp. §2.1 success metrics, §2.3 non-goals, §7.1 Mode 2, §16.4 retirement triggers).
- `embedded-host-contract.md` — the host-contract design this review affirms is *not* the problem.
- `pi-mono-borrow-review.md` / `codex-reverse-review.md` — the reverse-review discipline this record follows (evidence before recommendation; gap ≠ need).

**Method:** competitive sweep → identify the single market shift that postdates the
roadmap lock → re-score Agentao's moat axis-by-axis → keep/cut/gate the implied
directions → determine whether §16.4 fires. Code references anchored to `main`@`bcdb8e4`
(2026-06-18); positioning claims about external SDKs are dated and will drift — re-verify
before acting on any one of them.

---

## TL;DR

The Path A roadmap was **locked 2026-04-30**. Since then, the two largest model vendors
shipped embeddable, governed, sandboxed agent loops driven from the host's own
Python/TypeScript process — **the literal Path A value proposition**:

- **Claude Agent SDK** — "the exact agent loop that powers Claude Code … driven from
  your own Python or TypeScript program," four-layer permission pipeline + optional
  sandbox.
- **OpenAI Agents SDK** — sandbox execution + model-native harness (isolated workspace,
  file-level permissions, snapshot/rehydrate). Non-OpenAI models are reachable via
  Any-LLM / LiteLLM adapters that the docs label **best-effort / beta**, with known gaps
  in tool calling, structured output, and usage reporting — i.e. broad adapter coverage,
  *not* Agentao-style first-class provider-neutrality.

**Finding:** "embeddable governed agent loop for Python" is no longer a differentiator —
it is now table stakes offered by Anthropic and OpenAI with far greater distribution and
native model integration. This is exactly the condition `path-a-roadmap.md` §16.4 names
as a retirement/re-examination trigger. **The embed contract is not the problem (P0
proved it clean); the *answer to "why embed Agentao instead of the vendor SDK"* is the
problem.**

**Disposition (proposed, maintainer to ratify):**
- **§16.4 trigger: FIRED (re-examination, not retirement).** The embed-first thesis is
  narrowed, not invalidated — a defensible moat survives (§3). Convene the surviving-moat
  decision rather than opening a Path B/C document.
- **Do now (messaging, ~zero code):** re-headline the differentiation from "embeddable"
  (now commodity) to "provider-neutral + local/private-first + governed & auditable" (§4 D1).
- **Do now (distribution):** fix the discoverability/name-collision drag (§4 D2, §5).
- **Reframe (channel):** treat the existing ~21.5%-of-codebase ACP investment as a
  distribution channel riding **early-but-real** ecosystem signals, not just a feature (§4 D3).
- **Re-prioritize but still demand-gate:** roadmap P1.1 (`on_usage_event`) and P1.2
  (OTel) gain salience because the vendor SDKs *already ship* usage/cost tracking and
  OpenTelemetry — so these are **table-stakes parity items Agentao currently lacks**, not
  vendor weak spots. Raise priority *only when a lighthouse asks* (§4 D4).
- **Cut / hold (unchanged from §2.3):** CLI/TUI polish, hosted SaaS, strong cross-platform
  sandbox, speculative P1 without a named adopter.

---

## 1. Why this record exists

`path-a-roadmap.md` §16.4 commits to three retirement/re-examination triggers, the third
being: *"an external shift invalidates the embed-first thesis (e.g., a Python-native agent
runtime ships with stdlib-level distribution)."* The roadmap also commits (§16.2) to
re-reading §1–§8 at each checkpoint and asking *"is the success picture still the right
success picture? External shifts … may have changed what 'embedded' means."*

The June-2026 sweep surfaced one shift large enough to test that trigger. This record
isolates it, scores its impact, and proposes a disposition — under the same evidence-first
discipline as the borrow reviews (`pi-mono-borrow-review.md`: *"the first-pass list was
biased toward 'architecturally interesting' rather than 'actually missing'"*). The
symmetric risk here is over-reacting to a competitor's launch; §3 and §4 are written to
guard against that.

## 2. The competitive landscape (June 2026)

Agentao sits at the cross-section of four categories, contested individually on each axis:

| Category | 2026 leaders | Agentao position |
|---|---|---|
| **A. CLI coding-agent products** | opencode (~150k★, ~6.5M MAU), Claude Code, Codex CLI, Gemini CLI, Aider, Goose (now under Linux Foundation), Crush, Continue `cn` | Capable CLI, but **Path A §2.3 already concedes this** ("opencode already wins TUI"). Not the battlefield. |
| **B. Embeddable agent-loop SDKs** — *Path A's real arena* | **Claude Agent SDK**, **OpenAI Agents SDK**, Vercel AI SDK ("program agent harnesses"), Pi | **Newly crowded by first-party vendor SDKs.** This is the §3 shift. |
| **C. General agent frameworks** | pydantic-ai (typed contracts), smolagents (code-first), Agno (~39k★), Strands, LangGraph, Instructor | *Build-your-own-agent toolkits.* Agentao differs: **batteries-included** (tools + skills + permissions + memory + replay already wired), not an orchestration kit. Genuine, stable distinction. |
| **D. Coding-agent platforms** | OpenHands, SWE-agent | Hosted products/research. Out of Path A scope. |

The takeaway is not "Agentao is behind in four races." It is that Agentao's identity is a
*combination* (a governed, provider-neutral, batteries-included coding runtime that is also
a clean embeddable library), and the combination remains unique even though **each
individual axis is contested** — most newly so in category B.

## 3. The shift: first-party SDKs occupy Path A's pitch

Both facts below postdate the 2026-04-30 roadmap lock:

- **Claude Agent SDK** lets a host "take the exact agent loop that powers Claude Code —
  the same tool execution, context management, permission system, and subagent machinery —
  and drive it from your own Python or TypeScript program," with a four-layer permission
  pipeline (deny → mode → allow → `canUseTool`) and an optional isolated sandbox.
- **OpenAI Agents SDK** added sandbox execution and a model-native harness: an isolated
  Unix-like workspace with filesystem + shell, file-level permissions, and durable
  snapshot/rehydrate state. It reaches non-OpenAI models through Any-LLM / LiteLLM
  adapters that the official docs flag as **best-effort / beta**, with capability gaps
  (tool calling, structured output, usage reporting) versus the native OpenAI path.

Map that against Agentao's README first line — *"local-first, private-first, embeddable
agent harness for Python hosts"* — and the **"embeddable / permissioned / subagents /
sandbox" portion is now matched by both vendors**, who bring distribution and native model
integration Agentao cannot. The clean embed contract that P0 delivered is necessary but no
longer sufficient differentiation. The strategic question shifts from *"is the embed
contract clean?"* (yes) to *"why would a host embed Agentao instead of the vendor's own
SDK?"*

## 4. Moat re-score, axis by axis

### Still defensible — structurally hard for a vendor SDK to match

1. **Provider-neutral *and vendor-data-path-free* by construction.** OpenAI-compatible
   *any* provider + runtime `/provider` switching (`agentao/runtime/`, `LLM_PROVIDER`).
   Note the distinction precisely: the vendor SDKs now ship *broad third-party provider
   adapters* (Claude Agent SDK can target other models; OpenAI's SDK reaches 100+ via
   Any-LLM/LiteLLM, best-effort/beta) — so "you can point it at another model" is **no
   longer exclusive to Agentao**. What stays exclusive is the *posture*: agentao is not a
   vendor's loop wearing an adapter, carries no vendor harness culture, and routes through
   no vendor data path. The moat is **no-vendor-loop / no-vendor-telemetry**, not merely
   "multi-model."
2. **Local-first / private-first / no managed infra / no global state.** Capability
   injection, no host-logger pollution, multi-`Agentao()` isolation
   (`tests/test_multi_agentao_isolation.py`, `tests/test_no_host_logger_pollution.py`).
   Hosts that *cannot* route code through a vendor loop are the wedge.
3. **Local, in-process audit contract.** Permission modes + replay JSONL audit sink
   (v1.2, `agentao/host/replay_projection.py`) + `events()` host stream. **Do not
   overclaim here:** both vendor SDKs ship first-class governance/observability — Claude
   Agent SDK lists hooks, permissions, sessions, cost/usage tracking, OpenTelemetry, and
   checkpointing; OpenAI's SDK has tracing, usage, and sandbox permissions/results/resume
   state. Agentao's narrower, defensible edge is that its audit trail is a **local JSONL
   replay artifact under a no-vendor-loop posture** — the record lives in the host's files,
   not a vendor's tracing backend — which is the relevant property for hosts that cannot
   emit telemetry to a vendor. It is a *locality/ownership* edge, not a "vendors lack
   audit" edge.
4. **ACP-server interop — riding early-but-real ecosystem signals.** The ~21.5% of the
   codebase in `acp/` + `acp_client/` (11,659 / 54,205 LOC, verified 2026-06-18) aligns
   with an emerging standard. **Verifiable today:** ACP was created by Zed and Zed ships an
   ACP client; Google's Gemini CLI has a documented Zed/ACP integration. **Claimed but not
   yet primary-sourced in this record (treat as early signals, verify before weighting):**
   JetBrains full-IDE support, GitHub Copilot CLI ACP support, and the "25+ agents" count —
   these came from secondary roundups, not vendor docs. Net: ACP is a *plausible, growing*
   channel for "the provider-neutral, governed agent you bring to an ACP editor," not yet a
   *confirmed* tailwind. D3's weight should track how many of the unverified claims hold up.
5. **Chinese ecosystem.** jieba 中文 memory segmentation, bilingual docs, `agentao.cn`.
   Structurally underserved by the US vendor SDKs; aligned with the roadmap's §14
   lighthouse-outreach plan (Chinese-community FastAPI / pytest / Jupyter candidates).

### Contested / eroding

- **"Embeddable" alone** — now table stakes (§3).
- **CLI UX** — conceded to opencode (§2.3, no change).
- **Sandbox sophistication** — OpenAI/Claude now ship container snapshotting; Agentao's
  macOS `sandbox-exec` is narrower. **No action implied** — §2.3 already declares strong
  cross-platform sandbox a non-goal; the vendor move *confirms* that call rather than
  reversing it.

## 5. A measurable distribution drag

**Method snapshot (for reproducibility):** query `"agentao" python agent framework github`
run 2026-06-18 via the assistant's `WebSearch` tool (US locale, logged-out, top-10
results); the top organic hit was an unrelated `github.com/taoagents/agentao` ("ridges-old"
repo) and **`jin-bo/agentao` did not appear in the top results.** Search ranking drifts by
locale, login state, and date — re-run incognito/logged-out and record the engine + date
before citing this as current. As of this snapshot: given §2.1's success metric is *"being
depended on / found,"* this name-collision + discoverability gap is a direct, measurable
drag on the dependents target — and it corroborates §7.1 Mode 2 (*"the problem is
distribution, not technology"*) independent of the §3 shift. Unlike the moat questions,
this one is unambiguous and cheap to act on.

## 6. Implied directions — keep / cut / gate

Framed as options for maintainer judgment (gap ≠ need; pain judgment is the maintainer's;
demand-gating holds). Ordered by leverage.

| ID | Direction | Disposition | Rationale |
|---|---|---|---|
| **D1** | Re-headline differentiation: lead with "provider-neutral + local/private-first + **governed & auditable**", demote "embeddable" | **Do now** (messaging, ~zero code) | Directly answers the new "why not the vendor SDK?" question; the current README leads with the now-commodity word. |
| **D2** | Fix discoverability: repo topics, PyPI keywords, a "vs Claude/OpenAI Agent SDK" comparison page; lean into the Chinese-ecosystem wedge | **Do now** (distribution) | §5 is unambiguous; §14 already names distribution as the dominant non-engineering risk. |
| **D3** | Position ACP-server as a *channel*, not just a feature ("the governed, provider-neutral agent you plug into an ACP editor") | **Reframe (weight gated on §4.4 verification)** | Turns a large existing code investment into distribution; rides early-but-real ACP signals. Weight should scale with how many §4.4 unverified-adoption claims (JetBrains / Copilot CLI / "25+ agents") survive primary-source checking. Harness-appropriate (interop, like `acp_client`). |
| **D4** | Roadmap P1.1 `on_usage_event` + P1.2 OTel | **Re-prioritize, still gate** | These are **table-stakes parity items** — both vendor SDKs already ship usage/cost tracking + OpenTelemetry, so this is Agentao closing a gap, not exploiting a vendor weak spot. Salience ↑, but **start only when a lighthouse asks** — §4 discipline unchanged. |
| **D5** | Interop bridge: drive a Claude/OpenAI Agent SDK agent *from* Agentao, or expose Agentao *under* their tool interface | **Hold (demand-gated)** | Passes the harness-vs-product test (interop, like `acp_client`), but speculative without a named host. Record as a candidate, not a commitment. |
| — | CLI/TUI polish · hosted SaaS · strong cross-platform sandbox · speculative P1 | **Cut / hold (unchanged)** | Consistent with §2.3 and prior boundary-rejections; the §3 shift reinforces these calls rather than reopening them. |

## 7. §16.4 trigger determination

**Determination (proposed): the §16.4 third trigger has FIRED — as a re-examination, not
a retirement.**

- It is **not** the failure-path §7.2 guardrail (that is three flat months of PyPI
  dependents; this record makes no claim about the metric trend — the M+3 checkpoint on
  2026-07-31 still owns that).
- It is **not** the success-path successor (that is the 2027-04-30 strategic review).
- It is the **external-shift** clause: the embed-first *thesis* is narrowed because
  "embeddable" became commodity, but a defensible moat survives (§4), so the correct output
  is a *re-headline + distribution* response under Path A, not a Path B/C pivot.

Per §16.3, a checkpoint's output is "what the snapshots say / what we are changing / what
the roadmap should say differently." This record is not a calendar checkpoint. To respect
the §16.4 lock cleanly, the proposed roadmap edits are **split by which sections they
touch**:

- **Edit that needs no unlock (a living section):** append a **note to §16.4** recording
  that the external-shift trigger fired on 2026-06-18, with this document as the linked
  record. §16 is checkpoint/metric framing, not locked strategy — this is the only edit
  this record will make if asked.
- **Edit that *would require* an explicit unlock (a locked section):** adding a line to
  **§2.1** noting that "embeddable" is necessary-not-sufficient as of 2026-Q2. §2.1 is
  inside the locked §1–§8, so this record **does not** propose making that edit silently —
  it flags it as a *candidate* that only a maintainer ratification-to-unlock should apply.
  Until then, §1–§8 stay locked verbatim.

This avoids the contradiction of "don't touch §1–§8" while editing §2.1: the default action
is the §16.4 note alone; the §2.1 line is held behind an explicit unlock.

## 8. What would change this determination

Listed so the record can be falsified rather than defended:

- **If** a lighthouse adopter reports they chose Agentao *specifically* for vendor-neutrality
  or audit → the moat (§4) is confirmed stronger than scored; D1/D2 are validated, escalate
  distribution.
- **If** the next two monthly snapshots show PyPI dependents moving on the back of the
  Chinese-ecosystem wedge → D2 is the dominant lever; deprioritize everything else.
- **If** a vendor SDK ships first-class provider-neutrality *and* a local-only, no-telemetry
  mode *and* a first-class audit contract → moat axes 1–3 erode simultaneously; that is the
  signal to open the Path B/C document §16.4 reserves for true invalidation.
- **If** the §4.4 unverified ACP-adoption claims (JetBrains full IDE / GitHub Copilot CLI /
  "25+ agents") fail primary-source checking, or ACP adoption stalls or fragments →
  downgrade D3 and revisit the ~21.5% allocation at the next heavy checkpoint
  (M+6, 2026-10-31).

---

*This record follows the reverse-review discipline of `pi-mono-borrow-review.md` and
`codex-reverse-review.md`: evidence before recommendation, gap ≠ need, and an explicit
falsification clause so the determination can be checked against reality rather than
re-argued. It records a trigger and proposes a response; the decision to ratify is the
maintainer's.*
