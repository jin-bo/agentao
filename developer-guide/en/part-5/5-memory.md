# 5.5 Memory System

> **What you'll learn**
> - The three kinds of data Memory holds: persistent records, session summaries, recall candidates
> - The two SQLite databases (project + user scope) and how they're chosen
> - How to wipe / migrate / disable memory cleanly

Memory lets the agent **remember across sessions** — user preferences, project facts, conventions. It's distinct from conversation history (this turn's messages); memory persists.

## Three kinds of data

| Type | Storage | Purpose |
|------|---------|---------|
| **Persistent memory** (MemoryRecord) | SQLite `memories` table | Preferences, facts, rules — soft-deleted, never physically removed |
| **Session summary** (SessionSummaryRecord) | SQLite `session_summaries` table | LLM-generated summaries from context compression |
| **Recall candidate** (RecallCandidate) | In-memory only | Top-k scored against the current user message |

## Two SQLite databases

```
<working_directory>/.agentao/memory.db   ← project-level
~/.agentao/memory.db                      ← user-level (cross-project)
```

Project-level = "facts specific to this project" (project codename, team conventions, deploy paths). User-level = "this user's own preferences" (tabs vs spaces, timezone).

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

The user-scope store uses `SQLiteMemoryStore.open(...)` (strict): a failure disables the user scope for the session rather than degrading to `:memory:` (silently re-routing user-scope writes into project would conflate the scopes). **Memory never crashes agent construction** — worst case is loss of cross-session persistence.

## How to use it while embedding

You usually **don't touch MemoryManager directly** — Agentao handles:

1. Injecting relevant memories into the system prompt each turn (`<memory-context>` block)
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

Works for the session, forgotten on process exit.

### Custom memory backend (Redis / Postgres / remote API)

The `MemoryStore` capability protocol (`agentao.capabilities.MemoryStore`) is the supported injection point — implement the 15-method contract once and pass an instance as `project_store=` / `user_store=`. See `docs/EMBEDDING.md` and `agentao/capabilities/memory.py` for the surface.

## The two prompt blocks

Agentao injects two memory blocks into the system prompt (composition lives in `agentao/prompts/builder.py::SystemPromptBuilder.build()`, invoked via the `agent._build_system_prompt()` facade):

### `<memory-stable>` — stable block

Holds **long-term, structural** memories (types `profile` / `constraint` / `decision`). Identical every turn → benefits from **prompt caching** (most LLM vendors cache stable prefixes, reducing cost and latency).

### `<memory-context>` — dynamic recall

Top-k recall scored against the current user message each turn. Selected from all saved memories using keyword / Jaccard / tag / recency scoring.

**Division of labor**:

- Stable = "this user is always like this"
- Dynamic = "for this turn, which past memories are relevant"

## MemoryGuard: sensitive-info protection

`MemoryGuard` validates memories before they're persisted. The default (`agentao/memory/guards.py`) rejects obvious sensitive patterns:

- API keys, tokens, password literals
- Credit card numbers, national-ID-style PII

Customize:

```python
from agentao.memory.guards import MemoryGuard

class StrictGuard(MemoryGuard):
    def validate(self, content: str, key: str) -> None:
        super().validate(content, key)
        if "internal-only" in content.lower():
            raise SensitiveMemoryError("Cannot store 'internal-only' content")

agent = Agentao(working_directory=Path("/tmp"))
agent._memory_manager.guard = StrictGuard()
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

This is **intentional** — a prompt-injected LLM must not be able to read, write, or erase the user's entire memory library. List/delete/clear are CLI commands (`/memory search`, `/memory delete`, `/memory clear`) or direct `MemoryManager` API for your host.

## Memory management UI in your host

Your host can call `MemoryManager` directly to build a user-facing "view / delete memories" panel:

```python
mm = agent._memory_manager

# List all project-scope memories (helper returns project + user when scope=None)
for record in mm.get_all_entries(scope="project"):
    print(record.title, "—", record.content[:60])

# Search
for record in mm.search("typescript"):
    print(record)

# Soft delete by id (use ``delete_by_title`` for the human-friendly key)
mm.delete(record_id)

# Full clear: soft-delete every memory + drop every session summary
mm.clear()                       # both scopes; pass scope="project" or "user" to narrow
mm.clear_all_session_summaries()
```

**Compliance value**: "show the user what AI remembers about them" and a "forget me" button are often SaaS hard requirements.

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

A user clicks "new conversation" → `agent.clear_history()` — that clears session but not memory. If "new conversation" should forget everything, also call `MemoryManager.clear()` + `clear_all_session_summaries()`.

## TL;DR

- Memory ≠ conversation history. Memory persists across sessions in SQLite; history lives on `agent.messages`.
- Two scopes / two DBs: **project** (`<wd>/.agentao/memory.db`) and **user** (`~/.agentao/memory.db`). Multi-tenant deployments must key user scope by `tenant_id+user_id` or disable it.
- Read-only / restricted FS auto-degrades to in-memory store with a warning — agent keeps starting.
- `clear_history()` does **not** clear memory; that's intentional. Wipe both explicitly when "new conversation" should forget everything.

→ Next: [5.6 System Prompt Customization](./6-system-prompt)
