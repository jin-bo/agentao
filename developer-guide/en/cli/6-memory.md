# 6. Memory

The agent has a persistent memory layer. It writes facts about you and the project as it works (`save_memory` tool), recalls them on later turns, and survives across sessions and across `/clear`. This page is about inspecting, searching, and clearing what's in there.

## What memory is, what it isn't

| Memory is | Memory isn't |
|-----------|--------------|
| Long-term, cross-session knowledge | Conversation history (that's per-session) |
| Written by the agent (LLM-side) | Written by the CLI |
| Soft-deleted on `/memory delete` | Physically wiped (originals stay in DB) |
| Inspected via `/memory` CLI commands | Editable from the CLI (no `/memory edit`) |
| Two scopes: `user` (cross-project) and `project` | Backed by one shared file |

Two SQLite databases:

| Scope | Path | Contents |
|-------|------|----------|
| Project | `.agentao/memory.db` | Project-scoped persistent memories + session summaries |
| User | `~/.agentao/memory.db` | Cross-project user-scoped memories (your role, preferences, profile) |

Inside each DB:

- **`memories` table** — the persistent entries (`title`, `content`, `tags`, `scope`, `type`, `source`, ...)
- **`session_summaries` table** — auto-written by the context-compaction pipeline so a previously compacted session can still inform future turns

Recall candidates (the "this might be relevant right now" matches injected into the next turn) are computed in-memory at query time and never stored.

## How memory enters the prompt

Two blocks are added to the system prompt every turn:

- **`<memory-stable>`** — the core, slow-changing memories (your role, project anchors). Renders top-down, budget-limited.
- **`<memory-context>`** — top-K recall candidates scored against the current user message. Volatile; rebuilt every turn.

Session summaries are *not* in `<memory-stable>` — they live in the conversation history as `[Conversation Summary]` blocks added at compaction time.

## `/memory` — list everything

```text
> /memory
Saved Memories (12 total):

  • User Role [user]: Senior backend engineer, currently focused on...
    Tags: profile, role
    Updated: 2026-04-29 14:22:01

  • OAuth integration plan [project]: Use existing session middleware...
    Tags: feature, oauth
    Updated: 2026-04-30 09:15:33

Tag Summary:
  #profile (3)
  #project (2)
  #feature (2)
  ...
```

Each entry shows title, scope (`user` or `project`), the first 120 chars of content, tags, and last update. Bottom of the listing: tag frequency summary.

## `/memory search <query>` — keyword search

```text
> /memory search oauth
Found 2 memory(ies) matching 'oauth':

  • OAuth integration plan [project]: Use existing session middleware...
  • Auth token storage [project]: Tokens go through SealedStorage...
```

Searches across title, content, and tags. Case-insensitive substring match.

## `/memory tag <tag>` — filter by tag

```text
> /memory tag feature
Found 2 memory(ies) with tag 'feature':
  ...
```

Tags are written by the agent when it calls `save_memory(tags=[...])`. Use `/memory` to see what tags exist.

## `/memory user` and `/memory project` — single-scope views

```text
> /memory user        # only cross-project profile/preference entries
> /memory project     # only entries scoped to this directory
```

Useful when you suspect cross-project pollution ("the agent thinks I'm working on a different repo") — `/memory user` shows what's globally remembered.

## `/memory delete <key>` — soft-delete one entry

```text
> /memory delete OAuth integration plan
Successfully deleted memory: OAuth integration plan
```

The argument is the entry **title**, not its tags or ID. Match is exact (case sensitive); a normalized-key fallback catches obvious variants.

Soft-delete: the row stays in the DB with a `deleted_at` timestamp; it's filtered out everywhere it would otherwise appear. The agent will not see it on the next turn.

## `/memory clear` — soft-delete everything

```text
> /memory clear
Are you sure you want to delete ALL memories? This cannot be undone. [y/N]: y
Successfully cleared 47 memory(ies)
```

Confirmation required. Wipes both `memories` and `session_summaries` (soft-delete + summary table truncation respectively). Only the **active** scope and current project are affected — your user-global memories survive unless you're in the user scope.

::: warning "Cannot be undone" is from the agent's perspective
The DB rows are soft-deleted, so a determined operator with a SQLite browser can recover them. But the agent will never see them again, and the CLI offers no undo button.
:::

## `/memory session` — current session summary

```text
> /memory session
Session Memory (1842 chars, 3 summaries):

[Conversation Summary, 2026-04-30 14:22]
We worked on adding OAuth login. Investigated existing session middleware...

---

[Conversation Summary, 2026-04-30 14:08]
...
```

Shows the most recent compaction summaries (up to 10). Each summary is what got written when the context-compaction pipeline kicked in — useful when you want to verify what the agent "remembers" about earlier turns it can no longer see directly.

## `/memory status` — diagnostic counts

```text
> /memory status

Memory Status:
  Profile  (user):        7 entries
  Project:                12 entries
  Session summaries:      3
  Recall hits (session):  18
  Recall errors (session): 0
  Stable block size:      482 chars
  Latest session summary: 1842 chars
```

What each line means:

| Field | Meaning |
|-------|---------|
| Profile (user) | Persistent user-scope entries |
| Project | Persistent project-scope entries |
| Session summaries | Compaction-written summaries on file |
| Recall hits (session) | How many times the dynamic block actually injected memory this session |
| Recall errors (session) | How many recall queries failed (DB lock, schema mismatch) |
| Stable block size | Bytes of `<memory-stable>` injected per turn |
| Latest session summary | Bytes of the most recent `[Conversation Summary]` block |

Use this when you suspect memory is causing context bloat or when recall isn't working ("the agent should know this!").

## `/memory crystallize` and `/memory review`

These are the optional "memory crystallization" workflow — the CLI scans the **current** conversation for facts worth permanently remembering and stages them in a review queue. You then approve or reject each candidate; approved entries become `source=crystallized` memories.

| Command | Effect |
|---------|--------|
| `/memory crystallize` | Scan this session, stage candidates into the review queue |
| `/memory review` | List pending review items |
| `/memory review approve <id>` | Promote a candidate to a real memory |
| `/memory review reject <id>` | Drop the candidate |

Different from `/crystallize` (chapter 5): that one creates **skills**; this one creates **memory entries**. Same idea, different output.

## What the agent can do with memory

The LLM has exactly **one** memory tool: `save_memory(key, value, tags?)`. It can write — it cannot list, search, or delete. Search/delete are CLI-only deliberately, so the agent can't accidentally erase its own context.

The recall side is automatic: every turn, the retriever scores all stored memories against the current message and injects the top-K into `<memory-context>`. The agent doesn't call a recall tool — it just reads what's been put in front of it.

## Pitfalls

- **The agent saves things you didn't ask it to** — that's the design. If saved items are wrong or noisy, `/memory delete` them; if there's a pattern, tell the agent ("don't save memories about X").
- **`/memory clear` clears both user and current-project scopes** — it is still a soft delete; other projects' `.agentao/memory.db` files are not touched.
- **Memory ≠ skills** — a skill teaches the agent how to do a task; a memory tells it what's true about you / the project. Don't crystallize behavior into a memory; that's a skill.
- **Editing `memory.db` by hand** — fine if you know what you're doing. Schema is in [`agentao/memory/manager.py`](https://github.com/jin-bo/agentao/blob/main/agentao/memory/manager.py). Most users should stick to `/memory delete`.

## Where to go next

| Want to… | Read |
|----------|------|
| Diagnose context blow-up that includes too much memory | [7. Context & Status](./7-context-status) |
| Understand the recall scoring formula | [Part 5.5 · Memory System](/en/part-5/5-memory) |
| Pin custom memory rendering rules in the system prompt | [Part 5.6 · System Prompt](/en/part-5/6-system-prompt) |

---

::: info Where this fits
The `MemoryManager` is `agent.memory_manager`. Embedding hosts can call `mgr.get_all_entries()`, `mgr.search(...)`, `mgr.delete(...)` directly — same surface the CLI uses. The two-block prompt injection is a pure runtime detail and applies identically when embedding.
:::

::: tip Authoritative help
Command syntax: `/help`. Behavior: [`agentao/cli/commands_ext/memory.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands_ext/memory.py). Storage and recall: [`agentao/memory/manager.py`](https://github.com/jin-bo/agentao/blob/main/agentao/memory/manager.py).
:::
