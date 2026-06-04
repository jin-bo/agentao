# Skill Crystallization NO-OP Gates Implementation Plan

## Goal

Reduce false-positive `/crystallize suggest` drafts by adding 4 explicit
NO-OP gates in front of skill suggestion. The gates are inspired by
Gemini-CLI's Memory Service skill extraction (5 gates), but adapted to
AgentAO's single-session crystallization model and made **observable** so
users see *which* gate fired, not just that one did.

Target outcomes:

- Skip drafts that are already covered by an installed skill
- Skip drafts that read like ordinary agent knowledge
- Skip drafts that cannot be expressed as concrete ordered steps
- Skip drafts that are too narrow to plausibly be reused
- Make every skip **observable**: the user sees the gate ID and a one-line reason
- Keep `/crystallize feedback` and `/crystallize refine` as explicit user-override paths (never gated)

Non-goals (explicitly out of scope for v1):

- Server-side enforcement (post-draft fuzzy matching against existing skills) — v2
- Semantic pre-filtering of available skills before sending to the model — v1.1
- Reworking the gate set into a learned classifier — open-ended

## Background

`/crystallize suggest` currently has **one** weak gate: the prompt
instructs the model to emit `NO_PATTERN_FOUND` when "no clear repeatable
pattern exists" (`agentao/memory/crystallizer.py:283`). The CLI then
prints a generic "no clear repeatable skill pattern found" message
(`agentao/cli/commands_ext/crystallize.py:498`).

In practice this single gate misfires in two directions:

- Too lenient: drafts get suggested for workflows that are already
  covered by an installed skill, or that are obviously ordinary coding
  knowledge.
- Too opaque: when it does fire, users don't know whether it skipped
  because of duplication, lack of structure, triviality, or one-off-ness.

Borrowing Gemini-CLI's NO-OP gate idea fixes the first; adding a
parseable skip reason fixes the second.

## Design Decision

### Four gates, evaluated in cheap-deterministic-first order

| # | Gate ID | Question the model asks | Why this position |
|---|---------|-------------------------|-------------------|
| 1 | `covered_by_existing_skill` | Is this workflow already substantially covered by an installed skill? | **Only deterministic gate** — a list-lookup against the skills summary in the prompt. Deserves position 1 because it eliminates the most NO-OPs at the lowest cost and lowest false-positive risk. |
| 2 | `not_concrete_steps` | Can this be written as an ordered, imperative step list? | Structural / mechanical check. Cheap and fairly objective. |
| 3 | `ordinary_agent_knowledge` | Is this just ordinary coding knowledge a competent agent would already know without this session's project-specific evidence? | Subjective. Most likely to misfire. Placed *after* the deterministic and structural gates so its judgment can't override them. |
| 4 | `too_session_specific` | Is this only useful for this one artifact / file / incident, with little chance of future reuse? | Also subjective but more anchored in the session content than #3. |

The original Gemini-CLI design used 5 gates. Gates 4 ("会复现吗") and 5
("比单次事件更广泛吗") are conceptually the same axis — both ask whether
the workflow generalizes — so we merge them into a single
`too_session_specific` gate. Keeping them separate creates instability
where the model contradicts itself between two near-identical judgments.

### Gates are prompt-level **advisory**, not enforcement

The model can ignore the gates entirely and emit a draft. v1 does not
add a server-side post-processing pass that compares the draft against
`available_skills` and auto-rejects matches. If a future need arises for
hard enforcement, that becomes v2 (`covered_by_existing_skill` is the
obvious candidate for fuzzy-match enforcement). v1 is shipping
**better signal**, not stricter enforcement.

This must be documented in:

- The system prompt (so future maintainers reading
  `SUGGEST_SYSTEM_PROMPT` understand the design)
- The PR description
- This doc

### Gates apply to `/crystallize suggest` only

`/crystallize feedback`, `/crystallize revise`, and `/crystallize refine`
are explicit user-override paths. The user has already seen the draft
and is requesting a rewrite — re-gating at that point would feel like
the system "won't listen". Document this clearly so future contributors
don't propagate gates into the rewrite path.

### Observable skip reason

The current `NO_PATTERN_FOUND` contract becomes:

```
NO_PATTERN_FOUND
NO_PATTERN_FOUND:<gate_id> <one-line reason>
```

Where `<gate_id>` is one of a **fixed enum**:

```
covered_by_existing_skill | not_concrete_steps | ordinary_agent_knowledge | too_session_specific
```

CLI parsing rules:

- Match `NO_PATTERN_FOUND:<known_id> <reason>` against the whitelist —
  if the ID is not in the enum, fall back to the legacy plain
  `NO_PATTERN_FOUND` branch (forward compatibility for older models that
  emit unrecognized IDs).
- On a known gate ID, print:
  ```
  Skipped — covered_by_existing_skill: Already covered by the `pdf-merge` skill.
  ```
- The CLI may map gate IDs to localized labels later (out of scope for
  v1; the canonical IDs stay English).

### Available skills summary — data channel

`suggest_prompt()` gains a third positional argument:

```python
def suggest_prompt(
    session_content: str,
    evidence_text: str = "",
    available_skills_text: str = "",
) -> str:
```

`available_skills_text` is a pre-rendered block of `name — description`
lines, one per skill, prepended to the prompt under a clear heading.

**Selection / truncation policy** for the CLI builder:

1. Pull from `cli.agent.skill_manager.available_skills`
2. Sort by:
   1. `active_skills` first (skills currently activated for this session)
   2. then by most recently installed/updated (skill registry timestamp)
   3. then alphabetical as tiebreaker
3. Render as `name — first_sentence_of_description`, one per line
4. Truncate to a hard budget of **2000 chars total**
5. If truncated, append a final line: `(showing top N of M installed
   skills; the model may not see all of them)` — this primes the model
   to be conservative on the duplication gate when its view is partial

Rationale for the sort order: alphabetical truncation can drop the very
skill that should have caused the gate to fire (e.g. `pdf-merge` losing
to 30 a–o skills). Active and recently-touched skills are by far the
most likely candidates for duplication, so they go first.

Semantic pre-filtering (matching evidence keywords against skill
descriptions before sending to the model) is **deferred to v1.1**.

## Code Changes

| File | Change |
|------|--------|
| `agentao/memory/crystallizer.py:259` | Rewrite `SUGGEST_SYSTEM_PROMPT` to include the 4 gates in the order above, with the fixed-enum `<gate_id>` contract documented inline. |
| `agentao/memory/crystallizer.py:286` | Add `available_skills_text: str = ""` parameter to `suggest_prompt()`. When non-empty, render under a `# Available skills (do not duplicate)` heading before the evidence block. |
| `agentao/cli/commands_ext/crystallize.py:482` | Build the `available_skills_text` block from `cli.agent.skill_manager` using the selection/truncation policy above; pass into `suggest_prompt(...)`. |
| `agentao/cli/commands_ext/crystallize.py:498` | Replace the equality check with a parser: split on first `:`, whitelist-match the gate ID, render skip reason; fall back to the plain branch on unknown IDs. |
| `tests/test_crystallizer.py:61` | Add tests for: prompt contains all 4 gate IDs; `suggest_prompt` includes available skills text when provided; truncation hint appears when over budget. |
| `tests/test_crystallizer.py` (new) | Add parser tests: known gate ID renders reason; unknown gate ID falls back to legacy branch; plain `NO_PATTERN_FOUND` still works. |

Estimated diff: ~150 lines across 3 source files + ~80 lines of tests.

## Implementation Plan

Three commits, in this order, so each is independently reversible:

1. **`feat(crystallizer): pass available skills summary into suggest_prompt`**
   - Add `available_skills_text` param to `suggest_prompt()`
   - Build the summary in the CLI handler with the sort/truncate policy
   - System prompt unchanged → no behavior change yet, just data channel
   - Tests cover prompt-shape only

2. **`feat(crystallizer): add NO-OP gates with parseable skip reason`**
   - Rewrite `SUGGEST_SYSTEM_PROMPT` with the 4 gates and fixed-enum contract
   - Add the parser to the CLI handler
   - Behavior change is **fully contained in this commit** so a revert is one commit
   - Tests cover gate prompt + parser + override semantics

3. **`test(crystallizer): cover gate prompt, skip-reason parser, override paths`**
   - Snapshot/integration tests pinning the gate IDs and parser behavior
   - Mock-LLM tests proving feedback/refine paths skip the gates

## Open Questions

- Should the localized CLI label for each gate ID live in this codebase
  or come from a future i18n table? — **Defer**. v1 ships English
  labels.
- Should `/crystallize create` warn when the resulting skill name
  collides with an installed skill, even if the user bypassed gates via
  feedback? — **Defer to v1.1**.
- Should the gate prompt mention the skill registry size to the model
  ("you are looking at top 30 of 47 skills")? — **Yes**, included in
  the truncation hint above.

## Acceptance Criteria

- `/crystallize suggest` in a session whose work is already covered by
  an installed skill prints a skip line naming that gate and that skill,
  rather than emitting a duplicate draft.
- `/crystallize suggest` in a session whose work is genuinely novel and
  ordered emits a draft as before.
- `/crystallize feedback "rewrite"` after a gated NO-OP still rewrites
  the *previous* draft (if one exists) — gates do not reapply.
- A model that emits the legacy bare `NO_PATTERN_FOUND` is handled
  identically to before this change.
- A model that emits `NO_PATTERN_FOUND:<unknown_id> ...` falls back to
  the legacy branch and does not crash.
- `tests/test_crystallizer.py` is green.
