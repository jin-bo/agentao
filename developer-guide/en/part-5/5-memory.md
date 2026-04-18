# 5.5 Memory System

Memory lets the agent **remember across sessions** — user preferences, project facts, conventions. It's distinct from conversation history (this turn's messages); memory persists.

## Three kinds of data

Source: `agentao/memory/manager.py`, `agentao/memory/models.py`

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

In ACP subprocesses, restricted containers, or read-only filesystems, the memory DB may not be writable. Agentao's policy (`agentao/memory/manager.py:59-90`):

```
try to open <cwd>/.agentao/memory.db
 ├─ success → normal usage
 └─ fail (OSError / sqlite3.Error)
     → fallback to SQLiteMemoryStore(":memory:")
     → log warning
     → agent keeps starting (no crash)
```

Same for user-level. **Memory never crashes agent construction** — worst case is loss of cross-session persistence.

## How to use it while embedding

You usually **don't touch MemoryManager directly** — Agentao handles:

1. Injecting relevant memories into the system prompt each turn (`<memory-context>` block)
2. Letting the LLM save memories via the built-in `save_memory` tool
3. Preserving memory across `clear_history()` — memory lives longer than sessions

### Custom DB paths

For **full isolation** across agents (multi-tenant), passing distinct `working_directory` values is enough — project DBs land in `<working_directory>/.agentao/memory.db` and are naturally isolated.

### Disable user-level memory

By default the agent reads/writes `~/.agentao/memory.db`. If your product shouldn't pull in any "user-level" state:

```python
# Must replace the memory manager directly
from agentao.memory import MemoryManager

agent = Agentao(working_directory=Path("/tmp/sess"))
agent._memory_manager = MemoryManager(
    project_root=agent.working_directory / ".agentao",
    global_root=None,   # no user scope
)
```

⚠️ This touches a private attribute (`_memory_manager`). The name could change in future versions. If you plan to rely on this, file an issue upstream requesting a public config.

### Disable memory entirely

Simplest way: point the project store at `:memory:`:

```python
from agentao.memory import MemoryManager
from agentao.memory.storage import SQLiteMemoryStore

agent = Agentao(working_directory=Path("/tmp/sess"))
agent._memory_manager.project_store = SQLiteMemoryStore(":memory:")
agent._memory_manager.user_store = None
```

Works for the session, forgotten on process exit.

## The two prompt blocks

Agentao injects two memory blocks into the system prompt (source `agentao/agent.py::_build_system_prompt()`):

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

# List all project-scope memories
for record in mm.list_memories(scope="project"):
    print(record.title, "—", record.content[:60])

# Search
for record in mm.search("typescript"):
    print(record)

# Soft delete
mm.soft_delete(record_id)

# Full clear (including session summaries)
mm.clear_all()
```

**Compliance value**: "show the user what AI remembers about them" and a "forget me" button are often SaaS hard requirements.

## Memory vs conversation history vs AGENTAO.md

| Content | Use |
|---------|-----|
| "What we just discussed this turn" | Conversation history (`agent.messages`; cleared by `clear_history()`) |
| "User is always a Python + tabs person" | Memory (`save_memory`) |
| "This project uses Ruff for linting, port 8080" | `AGENTAO.md` (project-level constraint, committed to git) |
| "Technical plan needed for this turn" | Plan mode (doesn't cross sessions) |

## Common pitfalls

### ❌ Cramming big documents into memory

```python
save_memory("doc", open("readme.md").read())   # tens of KB
```

Recalled every turn, explodes context. Memories should be **short structured statements** (hundreds of characters). Store big docs in the repo and let the agent `read_file` on demand.

### ❌ Shared memory across tenants

Two users' agents pointing at the same default `Path.cwd()` or `working_directory` read/write the **same** DB and leak data. Multi-tenant must isolate `working_directory` per user.

### ❌ Forgetting memory outlives `clear_history()`

A user clicks "new conversation" → `agent.clear_history()` — that clears session but not memory. If "new conversation" should forget everything, also call `MemoryManager.clear_all()`.

→ Next: [5.6 System Prompt Customization](./6-system-prompt)
