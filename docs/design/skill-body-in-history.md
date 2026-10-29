# Active-skill bodies in persistent history (#552, local half) — Design

> 中文：[skill-body-in-history.zh.md](skill-body-in-history.zh.md)

**Status:** Built on branch `fix/552-local-skill-body-in-history`, 2026-10-28. Appendix A records the review.
**Baseline:** Agentao `44502d73`. Peers: Codex `26ad281fd1c`, pi-mono `136b87e8a`.
**Issue:** [#552](https://github.com/jin-bo/agentao/issues/552). #593 solved the path where the model activates an MCP skill with `activate_skill` and its result in history is intact. This document covers local and inline plugin skills. MCP skills stay as they are.

## TL;DR

- **The body goes into persistent history. The tail keeps only state.** Each active local or plugin skill has one user `<system-reminder>` in history that starts with `Skill loaded: "<name>"`. The tail gives the `Task:` and a one-line pointer.
- **Only each skill's latest state message counts.** There are two kinds of state message: `Skill loaded` and `Skill no longer active`. If the latest one equals the message the skill needs now, nothing is appended. Else one is appended. The rule reads history only, so it is idempotent.
- **One sync function, several call sites.** The flow is: sync and rebuild the request → estimate and compact → if history was replaced, sync and rebuild again → send. Both overflow rungs follow the same rule. A re-added body still counts against the budget and the overflow handling.
- **Activation snapshot.** The body and the resource list freeze at activation. Only re-activation or `/skills reload` refreshes them. This is a contract change, recorded in the CHANGELOG.
- **MCP skills are out of scope** (§2.3).

## 1. State before the change (grep-verified)

`SkillManager.get_skills_context()` runs once per request, called from `SystemPromptBuilder.build_volatile_tail` (`prompts/builder.py:268`).

| Path | Entry | Where the body went |
|---|---|---|
| Local `SKILL.md` | `tools/skill.py:75`, `cli/commands/skills.py:70`, `cli/run.py:819` (`spec.skills`), `embedding/sessions.py::restore_agent_skills` | The tail, on every request. The file was re-read and the resource directories re-scanned each time |
| Inline plugin skill (`full_content`, no `path`) | Same | The tail, on every request |
| MCP, model `activate_skill`, result intact | `tools/skill.py` | History. The tail has a pointer (#593) |
| MCP, `spec.skills` or `/skills activate` | Same | The tail |
| MCP, session restore | `restore_agent_skills` | Not restored. The content is withheld (D5) |

#552's measured workload activates one local skill through `spec.skills`. That is the first row.

Precedents this design reuses:

- Background notifications go into `agent.messages` as persistent user `<system-reminder>` messages (`_inject_background_notifications`).
- The `anthropic-messages` adapter merges consecutive same-role messages. Chat Completions accepts two user messages in a row.
- ACP replay strips `<system-reminder>` blocks. A user message that is empty after the strip is not sent to the client (`acp/_transport_replay.py:194-199`).
- Microcompaction touches only `role: "tool"` messages.

## 2. Review of the external proposal

### 2.1 Confirmed

| Claim | Evidence |
|---|---|
| The `spec.skills` case was still open | `cli/run.py:819` leaves no tool result. The local branch always emitted the body |
| "The body does not change while the skill is active" was not a current guarantee | `get_skill_content` called `open()` every time. No test pinned the live re-read, so it was incidental behaviour |
| Option B reverses the 0.4.27 invariant | `tests/test_skills_prompt.py::test_activating_a_skill_leaves_the_system_message_byte_identical` |
| Codex: `<skill>` fragment, `role=user`, recorded into session history | `codex-rs/ext/skills/src/fragments.rs:87-106`, `core/src/session/turn.rs:429` |
| pi-mono: `/skill:name` expands to `<skill>` as user input | `packages/coding-agent/src/core/agent-session.ts:2211-2230` |

Neither peer sends a deactivation notice or re-injects after compaction. Agentao needs both.

**Acceptance wording:** the current state the model sees is explicit. A deactivated or replaced body may stay in history, superseded by a later state message.

### 2.2 Correction: the activation result cannot be reused

Only an MCP skill's `activate_skill` result carries the body. A local skill's result has metadata and the resource list. There are two ways to handle this: (a) put the body into the tool result, or (b) carry the body in the state message for every entry. Option (a) covers one entry of five and spreads the #593 microcompaction guard. **Choose (b).**

### 2.3 Correction: the MCP non-tool paths are not folded in

An `<mcp-skill>` block in a user message breaks three invariants:

1. Provenance is recognised by tool origin (`provenance.is_skill_result`).
2. The restore-time withhold matches by tool name (`withhold_mcp_skill_content`). After a restore, the server's instructions would stay in context with no shell gate.
3. CLAUDE.md requires that moved MCP messages carry the origin marker.

These paths stay as they are and get their own work item (§6).

### 2.4 Correction: no version field

A custom key on a message dict is sent on the wire. The duplicate check compares content instead.

## 3. Design (as built)

### 3.1 Activation snapshot

- `activate_skill()` stores `_render_local_skill(name)` as `active_skills[name]["snapshot"]`: the skill-directory note, the `SKILL.md` text and the resource list.
- Calling `activate_skill()` again on an active skill retakes the snapshot.
- `reload_skills()` retakes the snapshot of each active local skill that the scan still finds. `/crystallize` relies on this.
- An entry that a host put into `active_skills` directly has no snapshot. It gets one on first use and keeps it (`_snapshot`).
- A per-request re-read with a hash compare is rejected: if the resource directory changes on every request, history grows without bound.

### 3.2 State messages

```text
<system-reminder>
Skill loaded: "<name>"
This is the current version of this skill. It replaces any earlier instructions of this skill in this conversation.
## <name> - <title>
<snapshot>
</system-reminder>
```

```text
<system-reminder>
Skill no longer active: "<name>"
Do not follow this skill's instructions from earlier in this conversation.
</system-reminder>
```

- The name on the first line is JSON-encoded, so any name reads back exactly.
- **The loaded message has no `Task:`.** The task is in the tail only. Otherwise a restore, which uses a different task, would never compare equal and would duplicate the body.
- `</system-reminder` in the body is escaped as `<\/system-reminder`. Without the escape, ACP replay's non-greedy regex would end inside the body and send the rest to the client.
- Recognition (`provenance.skill_state_name`, the only recogniser): `role == "user"`, string content that starts with `<system-reminder>\n`, a first line with one of the two prefixes, and then a JSON string that ends the line. The manager's latest-state lookup and every reader that skips the notes call it, so they cannot disagree about a message. A user's own turn starts with the date reminder, and tool results and file reads are not user messages, so none of them is misread.

### 3.3 The duplicate check reads only the latest message

`SkillManager.skill_state_messages(history)` returns what to append now:

```
latest = each skill's latest state message in history
for each skill whose latest is "loaded" and that is no longer active → a notice
for each active local or plugin skill:
    want = loaded message(name)
    latest.get(name) != want → want
```

- A check for "an equal copy appears anywhere in history" fails on `A → reload B → reload A`: it finds the early A and skips, while the model last saw B. Reading only the latest message avoids that.
- The compare is full equality. A wrong "not there" costs tokens. A wrong "there" drops instructions.
- There is no pending queue. Deactivation notices come from history alone:
  - A skill deactivated before any sync was never loaded in history, so it gets no notice.
  - A restored skill that no longer exists has a loaded message as its latest, so it gets a notice.
  - A skill deactivated and re-activated before a sync still has its latest equal to the wanted message, so nothing is sent.

### 3.4 The runner: one function, several call sites

`ChatLoopRunner._sync_skill_state(messages_with_system, system_prompt)` appends what `skill_state_messages(agent.messages)` returns to `agent.messages` and rebuilds `[system] + agent.messages`. When there is nothing to append, it returns its input unchanged. An answer that is not a list of non-empty strings (for example, from a host stand-in) is ignored.

Call sites:

1. **At the top of each iteration**, before `_volatile_tail()` and the threshold estimate. A newly loaded body counts against the budget and can trigger a compaction.
2. **After an in-loop compaction that replaced history.** Sync first, then decide with `tail_points_into_history` whether to rebuild the tail. That function now recognises both the MCP pointer and the local one.
3. **After overflow rung 1 (`api_overflow`).**
4. **After overflow rung 2 (`minimal_history`).** The append comes after the boundary repair, so the window still never opens on a `tool` message. If the request still overflows with the body re-added, the context-length error is returned, as before.

5. **After the 413 retry's compaction (`_recover_request_too_large`).** It runs a full `api_overflow` compaction, which can remove a loaded body. The returned `messages_with_system` is the persistent prefix actually sent, and the Tier-1 anchor counts that.

### 3.5 The tail

```
## <name> - <title>
Task: <task>
The instructions and the file list of this skill are in its latest "Skill loaded" reminder earlier in this conversation. The skill is still active.
```

The pointer is given only when the skill's latest state message in history equals its current loaded message. Otherwise (no history passed, or no sync ran), the tail sends the snapshot as before. The MCP branch does not change.

### 3.6 Compaction and restore

| Case | Result |
|---|---|
| Microcompaction | Unaffected |
| Full compaction, `/compact`, both overflow rungs | Appended once at the next sync point |
| Restore, file unchanged | `restore_agent_skills` re-activates, and the loaded message equals the one in history: nothing is appended |
| Restore, file changed | The new version is appended |
| Restore, skill gone | A notice is appended |
| Summarizer | A skill-state message stays in its input, so the transcript is still a contiguous suffix, but only as its first line (`Skill loaded: "<name>"`). The body is appended again after the compaction anyway, and a summary that restated it would outlive a later deactivation, which then has no loaded message to send a notice for |

A sub-agent has its own `messages` and runner, and the same rules apply.

## 4. Rejected options

| Option | Reason |
|---|---|
| B: body at the end of the system message | Reverses the 0.4.27 invariant. Every mid-session activation invalidates the cache for the whole history prefix |
| A "never compact" mark | A summary and `minimal_history` replace the whole list |
| Per-request hash compare | Unbounded history growth |
| A metadata key on the message | Sent on the wire |
| "An equal copy appears anywhere in history" | Wrong on `A → B → A` |
| A single flush point | Misses a body re-added after a compaction, and leaves it out of the budget |

## 5. Tests

`tests/test_skill_body_in_history.py`:

- The body is appended once and the tail has only the pointer. After two turns there is still one copy, and the loaded message has no task text.
- After a compaction replaces history, one copy is appended again.
- A first-time body is in the request before the threshold estimate.
- With no loaded message in history, the tail still carries the body.
- `A → B → A` appends three copies, and the last one is A.
- A file edit takes effect only after re-activation.
- The same text in an assistant or tool message does not count.
- A deactivation sends one notice. Deactivate then re-activate sends nothing. A skill never loaded in history gets no notice.
- A restore with a different task does not duplicate the body. A restored skill that is gone gets a notice.
- `</system-reminder>` in a body does not leak in ACP replay.
- After each of the two overflow rungs, one copy is appended, and `outcome.messages_with_system` equals the persistent prefix actually sent.

`tests/test_run_subcommand.py::test_a_spec_skill_does_not_copy_the_prompt_into_the_tail` now asserts that the tail has no body and history has exactly one copy.

**Mutation check:** with the runner reverted to its earlier version, the first three runner-level tests fail.

## 6. Out of scope

- The MCP `spec.skills` and `/skills activate` paths. They first need provenance to recognise user-role skill messages, and the restore-time withhold to cover them.
- Removing a deactivated skill's body from the request.
- Measuring the cache gain: run #552's guanlan workload, 10 runs before and 10 after. Compare absolute cache-miss tokens, total input tokens, request count and per-request `volatile_tail_sections`.

## Appendix A — Review record

- **2026-10-28, round 1** (external proposal): direction is option A. Corrections: a local result carries no body; the MCP non-tool paths are not folded in; no version field.
- **2026-10-28, round 2** (review of the draft). Two logic fixes were adopted:
  1. The duplicate check reads only each skill's latest state message (`A → B → A`), and the loaded message says it replaces the earlier instructions.
  2. Sync before the estimate and the compaction, and again after history is replaced. One idempotent function at several call sites replaces the "single flush point".

  Three wording fixes were also made: the scope of #593, the acceptance wording, and the removal of "append when the task text differs".
