# 6. Memory

The agent has a persistent memory layer. It writes facts about you and the project as it works (`save_memory` tool), recalls them on later turns, and survives across sessions and across `/new` (`/clear` wipes it). This page is about inspecting, searching, and clearing what's in there.

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

Two blocks reach the model every turn:

- **`<memory-stable>`** — the core, slow-changing memories (your role, project anchors), at the end of the cached system prompt. Renders top-down, budget-limited.
- **`<memory-context>`** — top-K recall candidates scored against the current user message. Volatile: it is rebuilt every turn and sent in the per-request `<system-reminder>`, not in the system prompt.

`<memory-stable>` also carries up to 3 summaries from *previous* sessions in this project. The current session's own summaries are not repeated there: they are already in the conversation history as `[Conversation Summary]` blocks added at compaction time.

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

Searches across title, content, the normalized key, tags and extracted keywords. Case-insensitive substring match.

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

The argument is matched against the entry **title** first. The whole title must match, ignoring case. Every entry with that title is deleted, in both scopes. If no title matches, the argument is tried as a key: exactly, then lowercased with spaces turned into `_`. Tags and IDs are never matched.

Soft-delete: the row stays in the DB with a `deleted_at` timestamp; it's filtered out everywhere it would otherwise appear. The agent will not see it on the next turn.

## `/memory clear` — soft-delete everything

```text
> /memory clear
Are you sure you want to delete ALL memories? This cannot be undone. [y/N]: y
Successfully cleared 47 memory(ies)
```

Confirmation required. Wipes both `memories` and `session_summaries` (soft-delete + summary table truncation respectively). Both scopes are affected — the current project's memories *and* your user-global ones — plus every session summary in this project. Other projects' own `.agentao/memory.db` files are untouched. If part of the wipe fails, the command names what survived instead of reporting success.

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

Shows this session's most recent compaction summaries (up to 10), cut to the last 2,000 characters. Each summary is what got written when the context-compaction pipeline kicked in — useful when you want to verify what the agent "remembers" about earlier turns it can no longer see directly. Summaries of earlier sessions are not listed here.

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
| Session summaries | Compaction summaries written in this session |
| Recall hits (session) | How many times the dynamic block actually injected memory this session |
| Recall errors (session) | How many recall queries failed (DB lock, schema mismatch). A `Last recall error` line follows when there was one. |
| Stable block size | Characters of `<memory-stable>` injected per turn |
| Latest session summary | Characters of this session's newest summary |

Use this when you suspect memory is causing context bloat or when recall isn't working ("the agent should know this!").

## `/memory crystallize` and `/memory review`

These are the optional "memory crystallization" workflow — the CLI scans the **current** conversation for facts worth permanently remembering and stages them in a review queue. You then approve or reject each candidate; approved entries become `source=crystallized` memories.

The rule-based crystallizer also runs on its own at every successful compaction. It reads only your `role: "user"` messages and looks for preference, constraint, decision and workflow patterns. Assistant text never triggers it, so "I prefer X" in a reply stages nothing. A candidate that matches again folds into the pending item and raises its `occurrences` count.

| Command | Effect |
|---------|--------|
| `/memory crystallize` | Scan this session, stage candidates into the review queue |
| `/memory review` | List pending review items |
| `/memory review approve <id>` | Promote a candidate to a real memory |
| `/memory review reject <id>` | Drop the candidate |

Different from `/crystallize` (chapter 5): that one creates **skills**; this one creates **memory entries**. Same idea, different output.

## What the agent can do with memory

The LLM has exactly **one** memory tool: `save_memory(key, value, tags?, scope?, type?)`. It can write — it cannot list, search, or delete. Search/delete are CLI-only deliberately, so the agent can't accidentally erase its own context. (Older versions had `search_memory` and `delete_memory` tools. They were removed.)

- **Scope:** a `user_` key prefix or a `user`, `preference`, `profile` or `personal` tag stores the entry in the user database. Everything else goes to the project database.
- **Same key, new value:** saving a key again updates the entry in place. Ask the agent to "update" a memory and it overwrites the old value.
- **Refused writes:** content over 4,000 characters, and content that looks like a credential (API keys, private keys, `password=`).

The recall side is automatic: every turn, the retriever scores all stored memories against the current message and injects the top-K into `<memory-context>`. The agent doesn't call a recall tool — it just reads what's been put in front of it.

## Commands and their `MemoryManager` methods

Each command is a thin wrapper over a public method, so an embedding host can build the same features ([`commands_ext/memory.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands_ext/memory.py)):

| Command | Method |
|---------|--------|
| `/memory`, `/memory list` | `get_all_entries()` |
| `/memory user`, `/memory project` | `get_all_entries(scope="user")`, `get_all_entries(scope="project")` |
| `/memory search <query>` | `search(query)` |
| `/memory tag <tag>` | `filter_by_tag(tag)` |
| `/memory delete <title-or-key>` | `delete_by_title(title)`, then `delete(id)` for a key match |
| `/memory clear` | `wipe_all()` |
| `/memory session` | `get_recent_session_summaries(limit=10)` |
| `/memory crystallize` | `crystallize_user_messages(agent.messages)` |
| `/memory review` | `list_review_items(status="pending")` |
| `/memory review approve <id>` | `approve_review_item(id)` |
| `/memory review reject <id>` | `reject_review_item(id)` |

## Pitfalls

- **The agent saves things you didn't ask it to** — that's the design. If saved items are wrong or noisy, `/memory delete` them; if there's a pattern, tell the agent ("don't save memories about X").
- **`/memory clear` clears both user and current-project scopes** — it is still a soft delete; other projects' `.agentao/memory.db` files are not touched.
- **Memory ≠ skills** — a skill teaches the agent how to do a task; a memory tells it what's true about you / the project. Don't crystallize behavior into a memory; that's a skill.
- **Secrets in memory** — the guard refuses obvious credentials, but it is a pattern check. Do not ask the agent to remember passwords or tokens.
- **Long documents in memory** — a memory should be a short statement. `<memory-stable>` shows at most 240 characters of each value. Keep long content in a file and save its path.
- **Editing `memory.db` by hand** — fine if you know what you're doing. The schema is in [`agentao/memory/storage.py`](https://github.com/jin-bo/agentao/blob/main/agentao/memory/storage.py), and Agentao migrates it by `_SCHEMA_VERSION`. Do not change the tables yourself. Most users should stick to `/memory delete`.

## Back up and inspect the database

Memory is plain SQLite. Use the `sqlite3` client's online backup, because the database runs in WAL mode and a plain `cp` can miss recent writes:

```bash
# Back up (safe while Agentao runs)
sqlite3 .agentao/memory.db ".backup .agentao/memory.db.bak"
sqlite3 ~/.agentao/memory.db ".backup $HOME/.agentao/memory.db.bak"

# Inspect the live entries
sqlite3 .agentao/memory.db "SELECT scope, type, key_normalized, title FROM memories WHERE deleted_at IS NULL;"

# Restore (quit Agentao first)
sqlite3 .agentao/memory.db ".restore .agentao/memory.db.bak"
```

To move memory to another machine, copy the backup files to the same paths there.

## Troubleshooting

### Memory doesn't reach the prompt

1. Run `/memory status`. Check that the entry count is not zero and that `Recall errors` is 0. Recall errors are logged as warnings in `agentao.log`.
2. Check that `.agentao/memory.db` is writable. If it is not, Agentao falls back to an in-memory store, and every memory is lost on exit.
3. If `Recall hits` stays flat, no entry scored. An entry is recalled only when your message shares words with its tags, title, keywords or content. Save it again with better tags or title.
4. To show an entry on every turn, give it a structural type: tag it `decision`, `constraint`, `workflow` or `preference`. It then goes into `<memory-stable>`. A `preference` tag also makes it a user-scope entry.

### `/memory delete` finds nothing

- Run `/memory list` and copy the exact title. The title match is whole-string, ignoring case.
- A key also works, written as stored (`snake_case`).
- Delete is soft. The row stays in the file with `deleted_at` set, so the file does not shrink. Session summaries are not affected.

### Session summaries are missing

- A summary is written only when a full compaction **succeeds**. A short session never compacts, so it has no summary.
- After three failed automatic compactions in a row, the breaker pauses automatic compaction. `/context` then shows `Compact breaker: open (3/3 consecutive failures, last: …)`. Run `/compact`: it runs as a probe, and a success closes the breaker. `/clear` also closes it.
- A compaction can be **cancelled** on purpose: by a `PreCompact` command hook that returns `{"hookSpecificOutput": {"compactionDecision": "cancel"}}`, or by a host's `compaction_controller=`. A cancel does not count toward the breaker and does not show in `/context`. Look for `Compaction cancelled:` in `agentao.log`.
- `/clear` and `/memory clear` delete the summaries of **every** session. `/new` keeps them, and earlier sessions' summaries still reach `<memory-stable>`.

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
