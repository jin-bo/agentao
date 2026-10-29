# MCP skill bodies in persistent history (#552: MCP non-tool paths, and the fallback after compaction) — Design

> 中文：[mcp-skill-body-in-history.zh.md](mcp-skill-body-in-history.zh.md)

**Status:** Design review passed (Appendix A, rounds 1–2), 2026-10-28. Option (a) of §6 is chosen. Not built.
**Baseline:** Agentao `96cca5b7` (#594 + #595).
**Issue:** [#552](https://github.com/jin-bo/agentao/issues/552). This follows [skill-body-in-history.md](skill-body-in-history.md), which moved local and plugin skill bodies into history and left the MCP paths out (§2.3 and §6 of that document).

## TL;DR

- **Scope:**
  1. An MCP skill activated by `spec.skills` (`agentao run`) or `/skills activate`. These paths leave no `activate_skill` result, so #593's pointer cannot apply, and the body rides the uncached tail on every request.
  2. A model-activated MCP skill whose `activate_skill` result a compaction removed or summarized. Today the body falls back to the tail.
- **Proposal:** use the same `Skill loaded` state message as local skills. Its body is the `_render_mcp_skill` block, and it ends with a whole-line origin marker. The marker is the provenance that compaction, the session save and the sub-agent paths already read. The name prefix `mcp:` is a second signal that does not depend on the marker.
- **The active set decides the effective state.** An intact `activate_skill` result that matches the current held bytes is the copy, and it supersedes older state messages. When that result is gone, the current version is appended as a state message (§2.2).
- **One gap to close:** `withhold_mcp_skill_content` only covers tool results. It must also withhold an MCP state message's body at restore, and keep the message's first line.
- **Origins are a union of what is actually found.** Only a message that yields nothing falls back to the unknown origin `*` (§2.4).

## 1. State today (grep-verified at `96cca5b7`)

| Path | Where the body goes | Provenance record in history |
|---|---|---|
| Model `activate_skill`, result intact | The tool result. The tail points at it (#593) | The tool result (`provenance.is_skill_result`) |
| Model `activate_skill`, result summarized away or withheld | The tail, on every request | The summary marker or the placeholder marker |
| `/skills activate` | The tail, on every request | `cli/commands/skills.py:20::_record_mcp_activation` appends a user `<system-reminder>` with `result_marker([label])` |
| `spec.skills` (`cli/run.py:818`) | The tail, on every request | None. Origins come only from `_mcp_session_origins` (held entries) and `name_origin(active_skills)` at save |
| Restore | Not re-activated (D5, `embedding/sessions.py:583`) | Tool results withheld (`withhold_mcp_skill_content`), origins tainted (`_gate_restored_origins`) |

What already holds, and what this design relies on:

1. **A whole-line marker on a user message is trusted provenance.** `provenance.marker_origins` reads markers on every message except assistant messages and non-agent tool results. `_record_mcp_activation` already depends on this.
2. **Every origin consumer reads markers:**
   - `skill_origins`, used by the compaction summary marker (`context_manager.py:1317`), the session file's `mcp_skill_origins` (`sessions.py:307`) and sub-agent results (`_wrapper.py:1188`).
   - The crystallizer, which skips marker-bearing user messages (`context_manager.py:1301`).
3. **The gates key on held entries, never on the active set or on history.** A deactivated skill's held entry stays until `/clear`. So a body left in history after deactivation is still gated.
4. **#594's readers already skip state messages**: the crystallizer, `/crystallize`, the summarizer (it sees the first line only), the sub-agent's parent context and the recall hints.
5. **Local and plugin skills cannot use the `mcp:` prefix** (CLAUDE.md, MCP Skills). A state message whose name starts with `mcp:` is MCP content.
6. **`provenance.content_origins` returns `{"*"}` when the text has no `<mcp-skill server=…>` wrapper.** This matters for §2.4.

## 2. Design

### 2.1 The loaded message

```text
<system-reminder>
Skill loaded: "mcp:<label>:<uri>"
This is the current version of this skill. It replaces any earlier instructions of this skill in this conversation.
## mcp:<label>:<uri>
<_render_mcp_skill(held)>
[This result includes content from MCP skills served by: <label>]
</system-reminder>
```

- **The body is `_render_mcp_skill(held)`**, which is the same bytes as the tail and the tool result. The held bytes are already frozen at activation, so the activation-snapshot semantics need no new code.
- **The marker is a whole line**, written by `result_marker([label])`. It sits outside the `<mcp-skill>` wrapper, so the restore-time block substitution cannot remove it.
- **Escaping:**
  - `_render_mcp_skill` already escapes `</mcp-skill`.
  - The message also escapes `</system-reminder` with `_CLOSING_REMINDER_RE`, as the local message does.
- **No `Task:` line.** The task stays in the tail.
- `_skill_loaded_message` gains an MCP branch, cached per held object and title.

### 2.2 The effective state

**Rule:** the current active set decides what the model should have. History is read only to find the skill's latest *record*, and only to answer one question: does the model already have that?

A skill's records in history are of three kinds:
- its `Skill loaded` messages (any version);
- its `Skill no longer active` notices;
- for an MCP skill, its `activate_skill` results that `provenance.mcp_activation_name()` reads as a load. That function only matches a result whose first line is `Skill Activated: mcp:…`. A refused, denied, cancelled or failed call returns other text, so it is not a load and cannot supersede an earlier notice. A restore's withheld activation keeps that head line, so it still counts as a load of a skill that is now inactive, and the next sync sends a notice.

The *latest record* is the one with the highest index.

```
pointed = mcp_pointer_targets(history)        # intact result holding the current held bytes (#593)
for each active skill:
    local                    → want = loaded(name); append if latest state message != want
    MCP, held is None        → nothing
    MCP, name in pointed     → nothing: the intact result supersedes every older state message
    MCP, otherwise           → want = loaded(name); append if latest state message != want
for each inactive skill whose latest record is a load
  (a loaded message, or an activate_skill result) → one "no longer active" notice
```

Consequences, each covered by a test (§5, item 5):

| Sequence | Result |
|---|---|
| loaded A → notice → model re-activates, result intact | Active and pointed: nothing appended. The old notice is superseded, so no wrong notice. The tail points at the result |
| loaded A → model re-activates with B (re-approved), result intact | Pointed at B: nothing appended. The tail points at B, never at A |
| The same, then a compaction removes the B result | Not pointed. The latest state message is A or the notice, which is not loaded(B), so loaded(B) is appended once. A second sync appends nothing |
| Model-activated, result intact, then deactivated | The latest record is a load (the result): one notice. **This is new for the model path**, which sends no notice today |
| Deactivated, re-activated before a sync | Active, and the latest record still matches: nothing |
| Notice → model re-activation fails (refused or an error) → sync | The failed result is not a load and the skill is not active: the notice stays the latest record, so nothing is appended and no new notice is sent |

- The notice loop must use the full active set. Today it uses only the *local* active skills, so an active MCP skill whose latest message is "loaded" would wrongly get a notice.
- `get_skills_context`, MCP branch:
  - a pointer to the result when `name in pointed`;
  - else a pointer to the state message when its latest state message equals `want`;
  - else the block.
  - The local note (`LOCAL_SKILL_IN_HISTORY_NOTE`) already says "latest 'Skill loaded' reminder", so it can be reused as is.

### 2.3 The runner

No new call sites. `_sync_skill_state` already runs:
- before the estimate,
- after an in-loop compaction,
- after both overflow rungs,
- after the 413 retry.

Microcompaction touches only tool messages, so it never clips a state message. It keeps intact pointed results whole, as now.

### 2.4 Provenance

- **New:** `provenance.is_mcp_skill_state_message(m)`. It is `skill_state_name(m)` starting with `mcp:`, built on #595's single recogniser.
- **`skill_origins` for such a message** is the union of the labels actually found. A *fallback* `*` from a helper is not a found label:
  - the `server=` attributes of its wrappers, read with `_SERVER_ATTR` directly. **Not** `content_origins`, which returns `*` when there is no wrapper.
  - its markers (`marker_origins`). A marker that **explicitly** names `*` is a found label and is kept. It means "server unknown" and keeps its gating meaning.
  - `name_origin(name)`, but only when it parsed a label. It returns `UNKNOWN_ORIGIN` for an `mcp:` name it cannot parse, and that fallback is dropped.
  - Only when nothing was found is the answer `{UNKNOWN_ORIGIN}`.
  - A withheld placeholder and a notice have no wrapper, but the name and the marker still give the label. So they never widen to `*`.
- **`_record_mcp_activation` (`/skills activate`) is removed.** It duplicated what the state message now records, and its text becomes false. Between the command and the first sync, the origin is still read from the held entry (`_mcp_session_origins`) and from `active_skills` at save. A test pins that window (§5, item 6).

### 2.5 Restore: withhold

`withhold_mcp_skill_content` gains one case, before the tool-role check:

```
if is_mcp_skill_state_message(message) and it is a loaded message:
    head = its first two lines (open tag + "Skill loaded: …")
    replace content with head + withheld(origins of §2.4) + "\n</system-reminder>"
```

- The head is kept, so the message is still a state message:
  - every reader of "the user's words" still skips it;
  - its latest record is a load of an inactive skill, so the next sync appends a `Skill no longer active` notice.
- The withheld text carries the marker, so a second save and restore keeps the same origins.
- A notice holds no body, so it is left as is.
- `_gate_restored_origins`, branch with no Skills session: it replaces every marker-bearing message with `withheld(origins)`. It must use the same head-keeping helper for a state message.
- The list entry is replaced, never edited in place, so ACP's replay of the user's history is unaffected.

### 2.6 Sub-agents and `agentao run`

- A child's `active_skills` starts empty, and `_build_parent_context` skips state messages, so no MCP body reaches a child through either.
- `agentao run` with `spec.skills` gets the body once in history instead of in every tail.

## 3. What is not changed

- The consent rules: model activation still needs `require_approval`, while `/skills activate` and `spec.skills` are consent themselves (`mcp/skills.py:804`).
- The gates (held entries), `/clear` (new generation), D5 (no re-activation on restore).
- The 0.4.27 system-prefix invariant and the catalogue.
- The tool-result path while its result is intact, apart from the deactivation notice in §2.2.

## 4. Risks

| Risk | Mitigation |
|---|---|
| MCP text in a user-role message reads as the user's words | All #594 readers skip state messages, and the marker gates the crystallizer as well. The `_render_mcp_skill` framing says the text comes from the server |
| A restored body without consent | §2.5 withholds it. Tests cover both the Skills-session and the no-session branches |
| A host message forged to look like a state message | It would be withheld, and it would add gates. Both fail safe |
| A damaged marker | `name_origin(name)` and `server=` still give the label |
| Origins widened to `*` by a message without a wrapper | §2.4 unions only what is found. The round-trip test (§5, item 4) asserts no `*` |
| The summary loses that a skill was MCP | `skill_origins(prep.to_summarize)` reads the message itself, not the first line the summarizer sees |

## 5. Tests (acceptance)

Use real `mcp.types` models and `tests/support/skills_mcp_server.py`, no `MagicMock` fakes (CLAUDE.md).

1. **Activate:** with `spec.skills` and with `/skills activate`, the body is in history once, the tail has the pointer, and a second turn adds nothing.
2. **Deactivate:** one `Skill no longer active` notice. Deactivate then re-activate before a sync sends nothing.
3. **Compaction, conditional:** for full compaction, both overflow rungs and the 413 retry:
   - when the compaction removed the loaded message or the intact result, one copy is appended;
   - when it kept it, nothing is appended.
   - The compaction summary carries the origin marker whenever MCP content was summarized.
4. **Restore before re-approval:**
   - The body is withheld and the head is kept, and the next sync appends a notice.
   - The gates ask for the shell (with a Skills session), and the content is withheld (without one).
   - After `/skills activate` again, a new loaded message is appended.
   - **Restore → save → restore:** the session file's `mcp_skill_origins` is exactly the original label each time, with no extra `*`.
   - **Origin edge cases:**
     - an unparseable `mcp:` name with a valid marker gives the marker's label only;
     - a marker that explicitly names `*` keeps `*`;
     - nothing found gives `{*}`.
5. **Transitions (§2.2 table):** each row is one test, including "deactivate → re-activation fails → sync". The assertions are: no wrong notice, no pointer to an older body, and no duplicate append.
6. **Save before the first sync:** after `/skills activate` and after `spec.skills`, a save before any turn records the label in `mcp_skill_origins`.
7. **Model path:** with an intact `activate_skill` result, no state message is appended, and the tail points at the result.
8. **Escapes:** `</system-reminder` and `</mcp-skill` in a server's SKILL.md neither leak in ACP replay nor close the wrapper.
9. **Readers:** the crystallizer, `/crystallize`, the parent context and the recall hints skip the message (one parametrized test).
10. **Mutation checks:** with §2.5 reverted, test 4 fails. With §2.4's union replaced by `content_origins`, the round-trip assertion fails.

## 6. Decision: a model-activated skill after compaction

**Chosen: (a).** Once the intact result is gone, the body moves into a state message, as for the non-tool paths. One rule covers all MCP skills, and the per-request tail copy after every compaction goes away.

Rejected: (b), keeping the tail. It keeps that cache miss, and the two MCP paths keep different lifecycles.

The model's `activate_skill` path does not change while its result is intact. The only additions are the deactivation notice (§2.2) and the fallback after the result is gone.

## 7. Out of scope

- Removing a deactivated skill's body from the request.
- Measuring an MCP workload. The local measurement on the [#552 comment](https://github.com/jin-bo/agentao/issues/552#issuecomment-6489784417) supports the mechanism. It does not show how large the MCP gain is, and this design makes no claim about that.

## Appendix A — Review record

- **2026-10-28, round 1.** (a) chosen. Changes made:
  1. **Origins:** a union of what is actually found, with `*` only when nothing is found. A restore → save → restore test was added. `content_origins` alone would widen a wrapperless placeholder or notice to `*`.
  2. **Transitions:** the active set decides the effective state. An intact result matching the held bytes supersedes older state messages. When it is gone, the current version is appended. The transition table and tests were added.
  3. **`_record_mcp_activation` removal agreed.** A test for saving before the first sync was added.
  4. **Compaction acceptance is conditional:** append only when the copy was removed.
  5. **Narrower claim** about the cache gain for MCP.
  6. **Title and scope** include the fallback after compaction.
- **2026-10-28, round 2.** Design review passed; the doc PR may merge after CI. Clarifications made:
  1. **§2.4:** `name_origin()`'s `UNKNOWN_ORIGIN` for an unparseable name is a fallback, not a found label, and is dropped from the union. A marker that explicitly names `*` is kept. Edge-case tests were added.
  2. **§2.2:** a load record is a result that `mcp_activation_name()` reads. A refused, cancelled or failed `activate_skill` result is not one and cannot supersede a notice. A "deactivate → re-activation fails → sync" case was added.
  - This review covered the document and the existing code it cites, not an implementation.
