# 5.5 Memory System

> **What you'll learn**
> - The four kinds of data Memory holds: persistent records, session summaries, recall candidates, review items
> - The two SQLite databases (project + user scope) and how they're chosen
> - How to wipe / migrate / disable memory cleanly

Memory lets the agent **remember across sessions** — user preferences, project facts, conventions. It's distinct from conversation history (this turn's messages); memory persists.

## Four kinds of data

| Type | Storage | Purpose |
|------|---------|---------|
| **Persistent memory** (MemoryRecord) | SQLite `memories` table | Preferences, facts, rules — soft-deleted, never physically removed |
| **Session summary** (SessionSummaryRecord) | SQLite `session_summaries` table | LLM-generated summaries from context compression |
| **Recall candidate** (RecallCandidate) | In-memory only | Top-k scored against the current user message |
| **Review item** (MemoryReviewItem) | SQLite `memory_review_queue` table | Crystallizer candidates awaiting `/memory review approve`. The crystallizer reads only `role: "user"` messages. No bulk clear reaches this table — see the wipe warning below |

### Persistent memory records

Each `save_memory` call goes through `MemoryManager.upsert()` ([`manager.py`](https://github.com/jin-bo/agentao/blob/main/agentao/memory/manager.py)). The guard ([`guards.py`](https://github.com/jin-bo/agentao/blob/main/agentao/memory/guards.py)) fills in what the call leaves out:

| Field | Values | How it is set |
|-------|--------|---------------|
| `scope` | `user` / `project` | An explicit `scope` wins. Otherwise a `user_` key prefix or a `user`, `preference`, `profile` or `personal` tag gives `user`. Everything else is `project`. |
| `type` | `preference` / `profile` / `project_fact` / `workflow` / `decision` / `constraint` / `note` | An explicit `type` wins. Otherwise the first matching tag decides. With no matching tag the type is `note`. |
| `source` | `explicit` / `auto` / `crystallized` | `explicit` for `save_memory`. `crystallized` for an approved review item. |
| `confidence` | `explicit_user` / `inferred` / `auto_summary` | `explicit_user` when `source` is `explicit`. |

- **Upsert:** the identity is `(scope, key_normalized)`. Saving the same key again updates the row in place and keeps its `created_at`.
- **Limits:** the normalized key keeps 80 characters and the title 120. Content over 4,000 characters is refused, not truncated.

### Session summaries

The context compactor writes a summary row only when a full compaction **succeeds** (`ContextManager.commit_compaction`). A skipped, cancelled or failed compaction writes nothing. Microcompaction writes no summary. A summary whose window held MCP-skill content is not saved either.

A summary reaches the model through exactly one channel:

| Summary from | Channel |
|--------------|---------|
| The current session | `agent.messages`, as a `[Conversation Summary]` block |
| An earlier session | The `<session>` part of `<memory-stable>` |

`get_cross_session_tail()` takes the 3 newest summaries whose `session_id` is not the current one. It keeps the newest 800 characters of them (`SESSION_TAIL_CHARS`).

The current session id is set by `archive_session(session_id)`. The CLI and ACP pass the conversation id, so a resumed conversation keeps its own summaries out of the tail. **A host that resumes its own conversations must do the same:**

```python
agent.memory_manager.archive_session(conversation_id)
```

Without an argument, `archive_session()` picks a fresh random id. The resumed conversation's own summaries then come back as an "earlier session", and the model sees them twice.

## Two SQLite databases

```
<working_directory>/.agentao/memory.db   ← project-level
~/.agentao/memory.db                      ← user-level (cross-project)
```

Project-level = "facts specific to this project" (project codename, team conventions, deploy paths). User-level = "this user's own preferences" (tabs vs spaces, timezone).

### Tables and change tracking

Each database has these tables ([`storage.py`](https://github.com/jin-bo/agentao/blob/main/agentao/memory/storage.py)):

| Table | Holds |
|-------|-------|
| `memories` | Persistent records, soft-deleted with `deleted_at` |
| `session_summaries` | Compaction summaries (project database only) |
| `memory_review_queue` | Crystallizer candidates (project database only) |
| `memory_events` | Internal write audit log |
| `schema_meta` | Schema version and the `memories_change` counter |

Three triggers on `memories` add 1 to `memories_change` on every insert, update and delete. The triggers live in the database file, so every writer moves the counter: another session, another process, or an older Agentao. The recall index is keyed on this counter, so the next recall sees a memory that another session wrote.

`<memory-stable>` ignores this counter. It is rebuilt only when this session writes, so the cached prompt prefix stays byte-identical.

## Graceful degradation

In ACP subprocesses, restricted containers, or read-only filesystems, the memory DB may not be writable. The fallback policy lives at the storage layer (`SQLiteMemoryStore.open_or_memory`) and the embedding factory wires it in:

```
SQLiteMemoryStore.open_or_memory(<cwd>/.agentao/memory.db)
 ├─ success → normal usage
 └─ fail (OSError / sqlite3.Error)
     → return SQLiteMemoryStore(":memory:")
     → log warning
     → agent keeps starting (no crash)
```

The user-scope store uses `SQLiteMemoryStore.open(...)` (strict), with no `:memory:` fallback. If it fails, the factory logs a warning and builds the manager without a user store. Existing user memories are then not read this session, and a new user-scope write is saved to the **project** store, with a log entry ([below](#disable-user-level-memory)). **Memory never crashes agent construction** — worst case is loss of cross-session persistence.

A store can also open and then fail: a `memory.db` with a valid header and corrupt pages opens fine, and every query on it raises `sqlite3.DatabaseError`. The reads on the turn path (the `<memory-stable>` block and recall) catch a database error, log one warning, and return nothing from that store, so the turn goes on without its memories. Any other exception still propagates. Writes to a broken store still fail: `save_memory` returns the error to the model.

None of these cases stop the session, so a log line is the only trace unless you show it. `MemoryManager.take_store_problems()` returns one message per store that could not be opened or read, each message once. The CLI prints them before the prompt and after each `/goal` turn. A host that embeds Agentao can call it after each turn:

```python
reply = agent.chat(message)
for problem in agent.memory_manager.take_store_problems():
    ui.warn(problem)
```

To recover a corrupt store, move `memory.db` aside. The next session creates a fresh one.

## How to use it while embedding

You usually **don't touch MemoryManager directly** — Agentao handles:

1. Injecting relevant memories into every request each turn (`<memory-context>` block)
2. Letting the LLM save memories via the built-in `save_memory` tool
3. Preserving memory across `clear_history()` — memory lives longer than sessions

### Custom DB paths

For **full isolation** across agents (multi-tenant), passing distinct `working_directory` values is enough — project DBs land in `<working_directory>/.agentao/memory.db` and are naturally isolated.

### Disable user-level memory

By default the factory reads/writes `~/.agentao/memory.db`. If your product shouldn't pull in any "user-level" state, build the manager yourself with `user_store=None` and pass it explicitly (post-#16, `MemoryManager` takes pre-built stores):

```python
from agentao.memory import MemoryManager, SQLiteMemoryStore

workdir = Path("/tmp/sess")
agent = Agentao(
    working_directory=workdir,
    memory_manager=MemoryManager(
        project_store=SQLiteMemoryStore.open_or_memory(
            workdir / ".agentao" / "memory.db"
        ),
        # user_store=None — no cross-project memory
    ),
    llm_client=...,
)
```

Without a user store, a user-scope write goes to the project database. The downgrade is logged, and the log never contains the key or the value:

- An explicit `scope="user"` logs a warning.
- An inferred user scope (a `user_` key, a `preference` tag) logs at debug. On a project-only manager this is the ordinary case.

### Disable memory entirely

Simplest way: point both stores at `:memory:` (or just project; user is `None` by default on the bare constructor):

```python
from agentao.memory import MemoryManager, SQLiteMemoryStore

agent = Agentao(
    working_directory=Path("/tmp/sess"),
    memory_manager=MemoryManager(
        project_store=SQLiteMemoryStore(":memory:"),
    ),
    llm_client=...,
)
```

Works for the session, forgotten on process exit. A manager you pass in is yours to close: `agent.close()` leaves it open, so call `agent.memory_manager.close()` after it — a `:memory:` store holds its connection until then.

### Custom memory backend (Redis / Postgres / remote API)

The `MemoryStore` capability protocol (`agentao.capabilities.MemoryStore`) is the supported injection point — implement the 15-method contract once and pass an instance as `project_store=` / `user_store=`. See [2.2 · Tier 3](/en/part-2/2-constructor-reference#tier-3-·-advanced-injections) and `agentao/capabilities/memory.py` for the surface.

## The two prompt blocks

Agentao injects two memory blocks, and **they ride different messages** — which is the whole point of the split:

### `<memory-stable>` — stable block

In the **system message**, as its last section (`agentao/prompts/builder.py::SystemPromptBuilder.build()`, reached via the `agent._build_system_prompt()` facade). Holds **long-term, structural** memories. Identical every turn → benefits from **prompt caching** (most LLM vendors cache stable prefixes, reducing cost and latency).

`MemoryManager.get_stable_entries()` selects the entries:

| Entries | Rule |
|---------|------|
| Every `user`-scope entry | Always included |
| `project` entries of type `decision`, `constraint`, `workflow`, `profile`, `preference` | Always included |
| `project` entries of type `project_fact`, `note` | Only the 3 most recently updated |
| All other entries | Dynamic recall only |

The block has a budget of 2,000 characters (`STABLE_BLOCK_MAX_CHARS`). Each fact shows at most 240 characters of its value. The renderer ([`render.py`](https://github.com/jin-bo/agentao/blob/main/agentao/memory/render.py)) first reserves room for the earlier-session summaries. It then fills the rest greedily, **newest entry first**, and skips any entry that does not fit. The kept entries are written in `created_at` order. A skipped entry can still surface through dynamic recall.

### `<memory-context>` — dynamic recall

In the **request-only volatile tail** (`SystemPromptBuilder.build_volatile_tail()`), a trailing `user` message that is never persisted. Each turn, `MemoryRetriever` scores the saved memories against the current user message and keeps the top 5. Entries already in `<memory-stable>` are left out. The block has a budget of about 1,200 characters.

The score has these terms ([`retriever.py`](https://github.com/jin-bo/agentao/blob/main/agentao/memory/retriever.py), `score()`):

| Term | Weight |
|------|--------|
| Exact tag match | ×4 (×2.5 for a 2-token query, ×1.5 for a 1-token query) |
| Title Jaccard similarity | ×3 |
| Keyword match (keywords are tokenized, so `agent.py` matches `agent`) | ×2 |
| Content match (first 500 characters) | ×1 |
| File-path hint (paths in the last 10 messages match a keyword) | ×2 |
| Recency, `1 / (1 + days/30)` | ×1 |
| Stale (not updated for over 90 days) | −2 |

An entry that matches on none of the first five terms is never recalled, however recent it is. The `reason` attribute of each fact names the terms that matched.

It is deliberately *not* in the system message: it is query-specific, so putting it there changed `messages[0]` every turn and invalidated the provider's cached prefix over the entire history. See `developer-guide/en/part-5/6-system-prompt.md`.

**Division of labor**:

- Stable = "this user is always like this"
- Dynamic = "for this turn, which past memories are relevant"

## MemoryGuard: sensitive-info protection

`MemoryGuard` validates memories before they're persisted. The default (`agentao/memory/guards.py`) rejects obvious sensitive patterns:

- API keys and tokens (the shared patterns in `agentao/security/secret_scan.py`), private-key headers, and `password=` / `secret=` style assignments

It does not detect PII such as card or ID numbers. Every write calls `validate_title`, `validate_content` and then `detect_sensitive(content)`; the last one raises `SensitiveMemoryError` to refuse the write.

Customize by overriding `detect_sensitive`:

```python
from agentao.memory.guards import MemoryGuard, SensitiveMemoryError

class StrictGuard(MemoryGuard):
    def detect_sensitive(self, content: str) -> None:
        super().detect_sensitive(content)
        if "internal-only" in content.lower():
            raise SensitiveMemoryError("Cannot store 'internal-only' content")

agent = Agentao(working_directory=Path("/tmp"))
agent.memory_manager.guard = StrictGuard()
```

## What the LLM can do — and can't

The LLM is exposed **only the write side**:

```python
# The LLM can call:
save_memory(key="preference", value="user prefers TypeScript strict mode")
```

**The LLM cannot**:

- List all memories
- Delete memories
- Clear memory
- Search memories (though `<memory-context>` is already a recall result)

This is **intentional** — a prompt-injected LLM must not be able to read, write, or erase the user's entire memory library. List/delete/clear are CLI commands (`/memory search`, `/memory delete`, `/memory clear`) or direct `MemoryManager` API for your host. Older versions had `search_memory` and `delete_memory` tools. They were removed.

## Sub-agent memory boundary

A sub-agent gets its own `MemoryManager` on a transient `:memory:` store. The store is discarded when the sub-agent closes (`agents/tools/_inherit.py::_child_memory_manager`).

| What | Where it goes | Why |
|------|---------------|-----|
| `save_memory` writes | The **parent's** manager, including its user store and a host-injected manager | Long-term memory belongs where the parent's memory lives |
| Session summaries from the sub-agent's compaction | The sub-agent's transient store | In the parent's database they would come back as an "earlier session" in `<memory-stable>` |
| Crystallizer candidates | The sub-agent's transient store | The sub-agent's `user` messages are prompts the parent model wrote. Approved, they would become memories the user never stated. |
| Reads (`<memory-stable>`, recall) | None | The transient store is empty. The sub-agent's briefing comes from `parent_context`, which holds recent conversation messages and no memories. |

The last row is a deliberate trade-off. A sub-agent can write a memory that it cannot read back. If the `save_memory` rebind fails, the sub-agent gets no `save_memory` at all.

## Memory management UI in your host

Your host can call `MemoryManager` directly to build a user-facing "view / delete memories" panel:

```python
mm = agent.memory_manager

# List all project-scope memories (helper returns project + user when scope=None)
for record in mm.get_all_entries(scope="project"):
    print(record.title, "—", record.content[:60])

# Search
for record in mm.search("typescript"):
    print(record)

# Soft delete by id (use ``delete_by_title`` for the human-friendly key)
mm.delete(record_id)

# Full clear: soft-delete every memory + drop every session summary.
# Always check the result. A storage failure comes back *in it*, never as an
# exception and never as a zero — a count of 0 also means "nothing to delete".
result = mm.wipe_all()
if not result.ok:
    print("still in place:", ", ".join(result.not_cleared))

# Narrower primitives are still there. Note `clear()` raises on a store
# failure, and a raise after the project store has committed skips whatever
# you were going to run next — which is why `wipe_all()` exists.
mm.clear()                       # both scopes (what `wipe_all()` calls)
mm.clear(scope="project")        # one scope only
```

**Compliance value**: "show the user what AI remembers about them" and a "forget me" button are often SaaS hard requirements.

::: warning `wipe_all()` is not erasure — read this before building "forget me"
Two things survive it, and a compliance button has to deal with both.

**Memories are soft-deleted.** `wipe_all()` sets `deleted_at` on every row; the
`content` column keeps the text and the row stays in `memory.db`. Nothing reads
it back afterwards — not recall, not the prompt blocks, not `get_all_entries()`
— but `ok is True` does **not** mean the bytes are gone. Real erasure needs
your own `DELETE` plus a `VACUUM`, or disposing of the database file.

**The review queue is untouched.** The rule-based crystallizer's pending
candidates in `memory_review_queue` are a fourth data type: they carry excerpts
of the messages they were extracted from, survive a wipe, and stay visible to
`/memory review`. `reject_review_item(id)` is the only remedy, one at a time.

What it *does* clear: persistent memories in both scopes (soft), and session
summaries from every session (hard `DELETE`).
:::

To back up, inspect or move a memory database, see [CLI 6 · Back up and inspect the database](/en/cli/6-memory#back-up-and-inspect-the-database).

## Memory vs conversation history vs AGENTAO.md

| Content | Use |
|---------|-----|
| "What we just discussed this turn" | Conversation history (`agent.messages`; cleared by `clear_history()`) |
| "User is always a Python + tabs person" | Memory (`save_memory`) |
| "This project uses Ruff for linting, port 8080" | `AGENTAO.md` (project-level constraint, committed to git) |
| "Technical plan needed for this turn" | Plan mode (doesn't cross sessions) |

## ⚠️ Common pitfalls

::: warning Don't ship without these
- ❌ **Cramming big documents into memory** — recall pulls them into every prompt, blowing context
- ❌ **Shared memory across tenants** — user-scope DB is process-global; cross-tenant leak waiting to happen
- ❌ **Forgetting memory outlives `clear_history()`** — "new conversation" still remembers

Each pitfall below has the full fix.
:::

### ❌ Cramming big documents into memory

```python
save_memory("doc", open("readme.md").read())   # tens of KB
```

Recalled every turn, explodes context. Memories should be **short structured statements** (hundreds of characters). Store big docs in the repo and let the agent `read_file` on demand.

### ❌ Shared memory across tenants

Two users' agents pointing at the same default `Path.cwd()` or `working_directory` read/write the **same** DB and leak data. Multi-tenant must isolate `working_directory` per user.

### ❌ Forgetting memory outlives `clear_history()`

A user clicks "new conversation" → `agent.clear_history()` — that clears session but not memory. If "new conversation" should forget everything, also call `MemoryManager.wipe_all()` **and check `result.ok`**: a wipe that silently failed leaves session summaries that `get_cross_session_tail()` puts straight back into the next prompt.

## TL;DR

- Memory ≠ conversation history. Memory persists across sessions in SQLite; history lives on `agent.messages`.
- Two scopes / two DBs: **project** (`<wd>/.agentao/memory.db`) and **user** (`~/.agentao/memory.db`). Multi-tenant deployments must key user scope by `tenant_id+user_id` or disable it.
- Read-only / restricted FS auto-degrades to in-memory store with a warning — agent keeps starting. A corrupt store is skipped on read. `take_store_problems()` gives you the notices to show.
- `clear_history()` does **not** clear memory; that's intentional. Wipe both explicitly with `MemoryManager.wipe_all()` when "new conversation" should forget everything — and check `result.ok`, because the counts cannot tell a failed wipe from an empty store.

→ Next: [5.6 System Prompt Customization](./6-system-prompt)
